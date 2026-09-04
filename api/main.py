"""
RejectionIntel local server.

Run:
    uvicorn api.main:app --reload --port 8000

Then open http://127.0.0.1:8000

Everything here runs offline. No API key, no database, no network calls.
Uploads are analysed in memory and thrown away — which is the right default
for a tool handling someone's rejection history, and a decision worth keeping
when you add persistence.
"""

from __future__ import annotations

import asyncio
import sys
from datetime import date, datetime
from pathlib import Path

from dataclasses import field as _dc_field  # noqa: E402
from fastapi import Body, FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, PlainTextResponse, RedirectResponse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.analyze import JD_CACHE, analyze, analyze_async, to_json  # noqa: E402
from core.ingest_v2 import (  # noqa: E402
    TEMPLATE_EXAMPLE, TEMPLATE_HEADER, ingest_csv, parse_date,
)
from core.enums import EventType  # noqa: E402
from core.narration import narrate_report, provider_status  # noqa: E402
import os  # noqa: E402
from core.resume_parse import parse_resume  # noqa: E402
from core.db import db_info, init_db  # noqa: E402
from core import auth  # noqa: E402
from core import queue_rq  # noqa: E402
from fastapi.responses import StreamingResponse  # noqa: E402

from core.tasks import STORE, active_store, run_job, stream_job  # noqa: E402
from core.verdict import build_actions, compare, find_bottleneck  # noqa: E402
from core.share import audit_share, build_share, forbidden_from_report  # noqa: E402
from core import cohort, ghost, mailer, tracker  # noqa: E402
from core.insights import (  # noqa: E402
    interesting_facts, learning_plan, pace_estimate, split_funnel,
)
from core.store import (  # noqa: E402
    done_action_keys, get_or_create_user, last_snapshot, load_applications,
    save_resume, save_snapshot, set_action, sync_actions, upsert_applications,
)

SITE = ROOT / "site"

app = FastAPI(title="RejectionIntel", version="0.3.0")


@app.on_event("startup")
def _startup() -> None:
    init_db()


def resolve_identity(request: Request) -> auth.Identity | None:
    """Whoever this request belongs to, or None if there's no valid session."""
    return auth.read_session(request.cookies.get(auth.SESSION_COOKIE))


def ensure_identity(request: Request, response: Response) -> auth.Identity:
    """Resolve a session, creating an anonymous one if needed.

    Anonymous-by-default is what keeps demo mode a single click. Requiring a
    sign-in before anyone has seen the product is the fastest way to lose them.
    """
    who = resolve_identity(request)
    if who is None:
        who = auth.create_anonymous()
        response.set_cookie(auth.SESSION_COOKIE, auth.make_session(who),
                            **auth.cookie_kwargs())
    return who

MAX_BYTES = 2 * 1024 * 1024        # a 100-row CSV is ~200KB even with JDs pasted in
MAX_RESUME_BYTES = 8 * 1024 * 1024  # a text-layer PDF resume is well under 1MB


@app.get("/", include_in_schema=False)
def home():
    return FileResponse(SITE / "index.html")


@app.get("/app", include_in_schema=False)
def upload_page():
    return FileResponse(SITE / "app.html")


@app.get("/backdrop.css", include_in_schema=False)
def backdrop_css():
    return FileResponse(SITE / "backdrop.css", media_type="text/css")


@app.get("/backdrop.js", include_in_schema=False)
def backdrop_js():
    return FileResponse(SITE / "backdrop.js", media_type="application/javascript")


@app.get("/d3.min.js", include_in_schema=False)
def d3_js():
    """Self-hosted. From a CDN this is blocked by common shield extensions, and
    the funnel then silently degrades to flat bars — losing the confidence
    flares, which are the one thing that distinguishes this chart."""
    return FileResponse(SITE / "d3.min.js", media_type="application/javascript")


@app.get("/funnel.js", include_in_schema=False)
def funnel_js():
    return FileResponse(SITE / "funnel.js", media_type="application/javascript")


@app.get("/api/health")
def health():
    st = provider_status()
    live = st.provider is not None
    return {
        "status": "ok",
        "llm": live,
        "provider": st.provider.info.name if live else "none",
        "model": st.provider.info.model if live else "template",
        "call_budget": st.provider.info.call_budget if live else 0,
        "keys_detected": st.available,
        "jd_extract_mode": os.environ.get("JD_EXTRACT", "auto"),
        "jd_cache_entries": len(JD_CACHE._store),
        "database": db_info(),
        "queue": queue_rq.health(),
        "mail": mailer.check_config(),
        "jd_cache_hit_rate": round(JD_CACHE.hit_rate, 3),
        "note": (st.provider.info.notes if live else st.reason) or
                "Narration is live.",
    }


@app.get("/api/template", response_class=PlainTextResponse)
def template():
    """The CSV template, so nobody has to guess the column names."""
    return PlainTextResponse(
        TEMPLATE_EXAMPLE,
        headers={"Content-Disposition": 'attachment; filename="rejectionintel-template.csv"'},
    )


async def _pipeline(job, csv_text: str, text: str, resume_meta: dict,
                    uid: int, extra_resumes: list[dict] | None = None) -> dict:
    """The analysis, instrumented for progress reporting.

    Identical work to before — the only change is that each stage announces
    itself, so the client can show what's happening instead of a spinner.
    """
    job.start("ingest")
    report_in = ingest_csv(csv_text)
    if report_in.rows_accepted == 0:
        job.fail("ingest", "no readable rows")
        return {
            "ok": False,
            "ingest": {
                "rows_total": report_in.rows_total,
                "rows_accepted": 0,
                "missing_required": report_in.missing_required,
                "unmatched_columns": report_in.unmatched_columns,
                "issues": [
                    {"row": i.row, "column": i.column,
                     "problem": i.problem, "severity": i.severity}
                    for i in report_in.issues[:40]
                ],
            },
        }
    job.finish("ingest",
               f"{report_in.rows_accepted}/{report_in.rows_total} rows · "
               f"{len(report_in.columns)} columns matched")

    job.start("resume")
    resume_id = save_resume(uid, text, resume_meta) if text.strip() else None

    # Store the other versions. Labelled from the filename so a CSV row tagged
    # "Resume B" lines up with the file the person actually sent.
    stored_versions = []
    for extra in (extra_resumes or []):
        save_resume(uid, extra["text"], extra["meta"])
        stored_versions.append(extra["meta"].get("filename") or "version")
    if stored_versions:
        job.update("resume", f"{len(stored_versions) + 1} versions stored")
    job.finish("resume",
               f"{resume_meta.get('words', 0)} words from {resume_meta.get('source', 'text')}")

    upsert = upsert_applications(uid, report_in.parsed, resume_id)
    history = load_applications(uid)
    report_in.parsed = history

    job.start("jobs", f"{len(history)} applications on record")
    st = provider_status()
    result = await analyze_async(
        report_in, text, as_of=date.today(),
        provider=st.provider,
        mode=os.environ.get("JD_EXTRACT", "auto").strip().lower(),
        job=job,
    )

    job.start("fit")
    job.finish("fit", f"{len(result.applications)} scored")

    job.start("stats")
    n_findings = len(result.result.findings)
    job.finish("stats", f"{n_findings} hypotheses tested, FDR corrected")

    job.start("narrate")
    payload = to_json(result)
    payload["ok"] = True
    payload["resume"] = resume_meta
    payload["resume_versions"] = stored_versions
    payload["persistence"] = {
        "new_applications": upsert["inserted"],
        "updated_applications": upsert["updated"],
        "total_stored": upsert["total"],
        "analysed": len(history),
    }

    narrated = await narrate_report(
        result.result, result.result.findings, result.profile
    )
    payload["narration"] = {
        "mode": narrated.mode,
        "provider": narrated.provider,
        "model": narrated.model,
        "calls": narrated.calls_made,
        "budget": narrated.budget,
        "fallbacks": narrated.fallbacks,
        "note": narrated.note,
        "summary": {
            "headline": narrated.summary.headline,
            "summary": narrated.summary.summary,
            "summary_easy": getattr(narrated.summary, "summary_easy", ""),
            "next_step": narrated.summary.next_step,
            "used_fallback": narrated.summary.used_fallback,
            "validation_errors": narrated.summary.validation_errors,
        },
    }
    for f in payload["findings"]:
        n = narrated.findings.get(f["dimension"])
        if n:
            f["headline"] = n.headline           # the observation
            f["explanation"] = n.explanation     # the translation
            f["translation"] = n.explanation
            f["translation_easy"] = getattr(n, "translation_easy", "")
            f["recommendation"] = n.recommendation
            f["recommendation_easy"] = getattr(n, "recommendation_easy", "")
            f["used_fallback"] = n.used_fallback
            f["validation_errors"] = n.validation_errors
    job.finish("narrate", f"{narrated.provider} · {narrated.calls_made} calls")

    # ---- the loop: one verdict, a short plan, and movement since last time ----
    summary = payload["summary"]
    verdict = find_bottleneck(summary["funnel"], summary["n_analyzable"])

    actions = build_actions(
        payload.get("observed") or [],
        payload.get("findings") or [],
        ((payload.get("strategy") or {}).get("roadmap") or {}).get("gaps") or [],
        ((payload.get("resume_lines") or {}).get("lines") or []),
    )

    # Read the previous snapshot BEFORE writing this one, or we'd compare the
    # run against itself and always report no movement.
    previous = last_snapshot(uid)
    done_keys = done_action_keys(uid)
    days = 0
    if previous and previous.get("created_at"):
        days = max(0, (datetime.utcnow() - previous["created_at"]).days)

    progress = compare(previous, summary["response_rate"],
                       summary["n_analyzable"], done_keys, days)

    applied_dates = [r["applied_at"] for r in history if r.get("applied_at")]
    payload["stages"] = [st.as_dict() for st in split_funnel(result.applications)]
    payload["pace"] = pace_estimate(result.applications, applied_dates).as_dict()
    payload["facts"] = interesting_facts(
        result.applications, result.profile, applied_dates, text)
    payload["learning"] = [
        sp.as_dict() for sp in learning_plan(
            ((payload.get("strategy") or {}).get("roadmap") or {}).get("gaps") or [],
            len(result.applications))
    ]
    # Share card, audited against this report's own identifying strings before
    # it is allowed into the payload. If the audit ever fails we ship no card
    # rather than a leaky one — a missing feature is recoverable, a published
    # employer name is not.
    card = build_share(payload["summary"], verdict.as_dict(),
                       payload.get("evidence_stats") or {}, payload.get("pace"))
    leaks = audit_share(card.as_dict(), forbidden_from_report(payload))
    payload["share"] = card.as_dict() if not leaks else None
    if leaks:
        print(f"  share card suppressed — redaction audit failed: {leaks[:3]}")

    # Which employers never replied. A count over the user's own history, and
    # the one ghost-job signal no competitor can compute from a listing alone.
    payload["ghost"] = ghost.analyse(result.applications).as_dict()

    # "Is this normal?" — the question the research says matters most, and the
    # one a single user's own data can never answer.
    payload["benchmark"] = cohort.benchmark(
        uid, payload["summary"]["response_rate"]).as_dict()

    payload["verdict"] = verdict.as_dict()
    payload["actions"] = sync_actions(uid, actions)
    payload["progress"] = progress.as_dict()

    save_snapshot(uid, summary["n_analyzable"], summary["response_rate"],
                  verdict.stage, done_keys)
    return payload


async def _read_inputs(applications: UploadFile | None, resume: UploadFile | None,
                       resume_text: str,
                       applications_text: str = "") -> tuple[str, str, dict]:
    """Read and validate uploads. Runs BEFORE the job is queued so bad input
    fails immediately with a real HTTP error instead of a job that dies."""
    if applications is not None:
        raw = await applications.read()
        if len(raw) > MAX_BYTES:
            raise HTTPException(413, "CSV is larger than 2MB. Trim it or split it.")
        # utf-8-sig first: it handles clean UTF-8 and strips a BOM, which is
        # what Excel prepends. cp1252 next, because that is what Excel on
        # Windows actually writes when it isn't writing UTF-8 — a smart quote
        # or an accented company name is enough to break a strict decode.
        csv_text = None
        for enc in ("utf-8-sig", "cp1252", "latin-1"):
            try:
                csv_text = raw.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        if csv_text is None:
            raise HTTPException(400,
                "Could not read that file as text. Re-export it as CSV UTF-8.")
    elif (applications_text or "").strip():
        csv_text = applications_text
    else:
        raise HTTPException(400,
            "Add your applications — upload a CSV, or paste rows from a spreadsheet.")

    text = resume_text
    meta: dict = {"source": "pasted" if resume_text.strip() else "none",
                  "words": len(resume_text.split())}

    if resume is not None:
        rraw = await resume.read()
        if len(rraw) > MAX_RESUME_BYTES:
            raise HTTPException(413, "Resume file is larger than 8MB.")
        parsed = parse_resume(resume.filename or "", rraw)
        if not parsed.ok:
            raise HTTPException(400, parsed.error)
        text = parsed.text
        meta = {"source": parsed.source_format, "filename": resume.filename,
                "pages": parsed.pages, "characters": len(parsed.text),
                "words": parsed.word_count, "warnings": parsed.warnings}

    if not text.strip():
        raise HTTPException(
            400,
            "No resume provided. Upload a PDF or DOCX, or paste the text — "
            "fit scores can't be computed without it.")
    return csv_text, text, meta


@app.post("/api/preview")
async def preview(text: str = Form("")):
    """Parse pasted data and report what was understood — nothing is saved.

    The gap between "I have my applications somewhere" and "I have a correctly
    formatted CSV" is where most people give up. Copying cells out of Sheets,
    Excel or Notion produces tab- or semicolon-delimited text, which the
    ingest already handles; what was missing was somewhere to put it and a way
    to see whether it landed right before committing.
    """
    if not (text or "").strip():
        raise HTTPException(400, "Paste some rows first.")

    rep = ingest_csv(text)
    return {
        "rows_total": rep.rows_total,
        "rows_accepted": rep.rows_accepted,
        "missing_required": rep.missing_required,
        "unmatched_columns": rep.unmatched_columns,
        "day_first": rep.day_first,
        "columns": {
            k: {"header": v.header, "method": v.method, "confidence": v.confidence}
            for k, v in rep.columns.items()
        },
        "issues": [
            {"row": i.row, "column": i.column, "problem": i.problem,
             "severity": i.severity}
            for i in rep.issues[:30]
        ],
        # A sample, so the user can confirm we read what they meant before
        # committing the whole batch.
        "sample": [
            {
                "row": r["row"],
                "company": r["company"],
                "job_title": r["job_title"],
                "applied_at": r["applied_at"].isoformat(),
                "outcome": next((e[0].value for e in r["events"]
                                 if e[0].value != "submitted"), "no response"),
                "has_jd": bool(r["jd_text"].strip()),
            }
            for r in rep.parsed[:12]
        ],
    }


@app.post("/api/analyze-jd")
async def analyze_jd(jd_text: str = Form(""), resume_text: str = Form(""),
                     resume: UploadFile | None = File(None)):
    """One resume against one job description.

    The model reads the advert into structured requirements; every number in
    the response is computed here with the same scorer the full report uses.
    A model-produced percentage would eventually disagree with the report for
    the same pair, and there would be no way to say which was wrong.
    """
    from core import pre_apply

    jd_text = (jd_text or "").strip()
    if len(jd_text) < 40:
        raise HTTPException(400, "Paste the full job description — we need the "
                                 "requirements, not just the title.")

    text = (resume_text or "").strip()
    if resume is not None and getattr(resume, "filename", ""):
        raw = await resume.read()
        if len(raw) > MAX_RESUME_BYTES:
            raise HTTPException(413, "Resume file is larger than 8MB.")
        parsed = parse_resume(resume.filename, raw)
        if not parsed.ok:
            raise HTTPException(400, parsed.error)
        text = parsed.text
    if len(text) < 80:
        raise HTTPException(400, "Add your resume — upload a file or paste the text.")

    from core.llm_provider import get_provider
    provider = get_provider().provider
    try:
        extraction, method, dropped = await pre_apply.extract_requirements(
            jd_text, provider)
    except Exception as exc:  # noqa: BLE001
        # Extraction is the only step that can hang or fail. Falling back keeps
        # the endpoint useful rather than returning a 500 for a slow model.
        print(f"  pre-apply extraction failed, using vocabulary: {exc}")
        extraction, method, dropped = await pre_apply.extract_requirements(
            jd_text, None)

    result = pre_apply.analyse(text, jd_text, extraction, method)
    payload = result.model_dump()
    if dropped:
        # Surfaced rather than swallowed: a model inventing requirements is
        # something the operator should be able to see happening.
        payload["dropped_unverifiable"] = dropped
    return payload


@app.post("/api/match")
async def match_jd(resume_text: str = Form(""), jd_text: str = Form(""),
                   resume: UploadFile | None = File(None)):
    """Score one resume against one pasted job description.

    The entry point for someone with a resume and no history. It runs the same
    deterministic scorer the full report uses — no separate "quick" path that
    could disagree with the real one.
    """
    from core.analyze import _requirements_from_jd
    from core.analyze import parse_resume as build_profile
    from core.deterministic import default_vocab
    from core.fit_score import compute_fit

    if not (jd_text or "").strip():
        raise HTTPException(400, "Paste the job description first.")

    text = resume_text
    if resume is not None:
        raw = await resume.read()
        if len(raw) > MAX_RESUME_BYTES:
            raise HTTPException(413, "Resume file is larger than 8MB.")
        parsed = parse_resume(resume.filename or "", raw)
        if not parsed.ok:
            raise HTTPException(400, parsed.error)
        text = parsed.text
    if not (text or "").strip():
        raise HTTPException(400, "Upload a resume or paste its text.")

    vocab = default_vocab()
    profile = build_profile(text, vocab, today=date.today())
    reqs = _requirements_from_jd(jd_text, "", vocab)
    fit = compute_fit(profile, reqs)

    req_detail = fit.breakdown.get("skills_required", {})
    matched = req_detail.get("matched", [])
    weak = req_detail.get("weak", [])
    missing = req_detail.get("missing", [])

    if fit.overall >= 80:
        verdict = "Strong match — worth a tailored application."
    elif fit.overall >= 65:
        verdict = "Reasonable match. Close the gaps below before you send it."
    else:
        verdict = "Weak match. On your own history, applications like this rarely land."

    return {
        "ok": True,
        "score": fit.overall,
        "verdict": verdict,
        # Before spending an hour tailoring, worth knowing whether anyone is
        # reading applications for this at all.
        "listing": ghost.check_listing(jd_text),
        "matched": matched,
        "weak_evidence": weak,
        "missing": missing,
        "breakdown": {
            "skills": fit.skills_required,
            "experience": fit.experience,
            "seniority": fit.seniority,
        },
        "experience": fit.breakdown.get("experience", {}),
        "seniority": fit.breakdown.get("seniority", {}),
        "note": ("This is the same score used on every application in your report, "
                 "so a role you check here will read the same once it's in your history."),
    }


@app.post("/api/resume-only")
async def resume_only(
    resume: UploadFile | None = File(None),
    resumes: list[UploadFile] = File([]),
    resume_text: str = Form(""),
):
    """Read a resume with no application history.

    Most first-time visitors have a resume and no spreadsheet. Returning a
    422 to them was the single largest onboarding leak in the product.

    What comes back is a READOUT, not a diagnosis: merged experience,
    seniority, and which skills are evidenced in bullets versus merely listed.
    Nothing about outcomes, because there are none — and saying so is the
    point. A tool that invents a verdict from a resume alone is the thing this
    product exists not to be.
    """
    text = resume_text
    meta: dict = {"source": "pasted", "words": len(resume_text.split())}

    if resume is not None:
        raw = await resume.read()
        if len(raw) > MAX_RESUME_BYTES:
            raise HTTPException(413, "Resume file is larger than 8MB.")
        parsed = parse_resume(resume.filename or "", raw)
        if not parsed.ok:
            raise HTTPException(400, parsed.error)
        text = parsed.text
        meta = {"source": parsed.source_format, "filename": resume.filename,
                "pages": parsed.pages, "words": parsed.word_count,
                "warnings": parsed.warnings}

    if not text.strip():
        raise HTTPException(400, "Upload a PDF or DOCX, or paste your resume text.")

    from core.analyze import parse_resume as build_profile
    from core.deterministic import default_vocab

    vocab = default_vocab()
    profile = build_profile(text, vocab, today=date.today())

    evidenced = sorted(profile.skills_in_bullets)
    listed = sorted(set(profile.skills) - profile.skills_in_bullets)

    return {
        "ok": True,
        "resume": meta,
        "profile": {
            "experience_years": round(profile.total_experience_months / 12, 1),
            "experience_months": profile.total_experience_months,
            "seniority": profile.seniority.value,
            "skills_evidenced": evidenced,
            "skills_listed_only": listed,
            "skill_count": len(profile.skills),
        },
        "limits": {
            "headline": "This is a reading of your resume, not a diagnosis.",
            "detail": ("Response rates, patterns and skill gaps all need outcome "
                       "data. Add a CSV of where you applied and what happened, "
                       "and the analysis unlocks at 25 resolved applications."),
        },
    }


@app.post("/api/analyze")
async def analyze_upload(
    request: Request,
    response: Response,
    applications: UploadFile | None = File(None),
    applications_text: str = Form(""),
    resume: UploadFile | None = File(None),
    resumes: list[UploadFile] = File([]),
    resume_text: str = Form(""),
):
    """Queue the analysis and return immediately.

    The work now takes long enough — LLM extraction plus evidence
    classification — that holding the request open would time out behind most
    proxies and show a blank screen while it did.
    """
    csv_text, text, meta = await _read_inputs(
        applications, resume, resume_text, applications_text)

    # Extra resume versions. Parsed and validated here so a broken one fails
    # fast rather than becoming a background job that dies.
    extras: list[dict] = []
    for up in (resumes or []):
        if up is None or not getattr(up, "filename", ""):
            continue
        raw = await up.read()
        if len(raw) > MAX_RESUME_BYTES:
            raise HTTPException(413, f"{up.filename} is larger than 8MB.")
        parsed = parse_resume(up.filename, raw)
        if not parsed.ok:
            raise HTTPException(400, f"{up.filename}: {parsed.error}")
        extras.append({
            "text": parsed.text,
            "meta": {"source": parsed.source_format, "filename": up.filename,
                     "pages": parsed.pages, "words": parsed.word_count,
                     "warnings": parsed.warnings},
        })
    who = ensure_identity(request, response)
    job = STORE.create()
    asyncio.create_task(run_job(
        job, lambda j: _pipeline(j, csv_text, text, meta, who.user_id, extras)))
    response.headers["X-Job-Id"] = job.id
    return {"job_id": job.id, "user": who.display}


@app.post("/api/demo")
async def demo(request: Request, response: Response):
    """Run the pipeline on the bundled sample data.

    Most first-time visitors have no CSV ready. Without this they bounce, and
    the analysis they came to see never renders.
    """
    csv_path = ROOT / "sample" / "sample_applications.csv"
    resume_path = ROOT / "sample" / "sample_resume.txt"
    if not csv_path.exists():
        raise HTTPException(500, "Sample data is missing from the install.")

    csv_text = csv_path.read_text(encoding="utf-8")
    text = resume_path.read_text(encoding="utf-8") if resume_path.exists() else ""
    meta = {"source": "demo", "words": len(text.split()), "filename": "sample_resume.txt"}

    who = ensure_identity(request, response)
    job = STORE.create()
    asyncio.create_task(run_job(job, lambda j: _pipeline(j, csv_text, text, meta, who.user_id)))
    return {"job_id": job.id, "demo": True, "user": who.display}


@app.get("/api/auth/me")
async def whoami(request: Request):
    who = resolve_identity(request)
    return {
        "signed_in": bool(who and not who.is_anonymous),
        "display": who.display if who else None,
        "anonymous": who.is_anonymous if who else True,
        "delivery": auth.delivery_mode(),
    }


@app.post("/api/auth/request")
async def auth_request(email: str = Form(...)):
    """Issue a magic link and send it.

    The response never reveals whether the address is already registered —
    "we've sent a link if that address is valid" is the same answer either way.
    Otherwise this endpoint is an account-enumeration oracle: try an address,
    read the response, learn whether that person uses the product. For a
    job-search tool that leaks something genuinely sensitive.

    The link is only returned in the body in console mode, which is development.
    """
    email = (email or "").strip().lower()
    if "@" not in email or len(email) < 5:
        raise HTTPException(400, "That doesn't look like an email address.")

    token = auth.make_magic_token(email)
    path = f"/auth/verify?token={token}"
    full = f"{mailer.APP_URL.rstrip('/')}{path}"

    res = mailer.send_magic_link(email, full, minutes=15)

    if res.transport.startswith("console") or res.link:
        return {"sent": False, "mode": "console", "link": path,
                "note": "No mail provider configured — use the link below."}

    return {"sent": True, "mode": res.transport,
            "note": "Check your email. The link works once and expires in "
                    "15 minutes."}



@app.get("/auth/verify", include_in_schema=False)
async def auth_verify(token: str):
    who = auth.consume_magic_token(token)
    if who is None:
        return RedirectResponse("/app?auth=expired", status_code=303)
    resp = RedirectResponse("/app?auth=ok", status_code=303)
    resp.set_cookie(auth.SESSION_COOKIE, auth.make_session(who), **auth.cookie_kwargs())
    return resp


@app.post("/api/auth/logout")
async def auth_logout(response: Response):
    response.delete_cookie(auth.SESSION_COOKIE, path="/")
    return {"ok": True}


@app.post("/api/auth/token")
async def issue_token(request: Request):
    """Mint an API token for the browser extension."""
    who = resolve_identity(request)
    if who is None or who.is_anonymous:
        raise HTTPException(401, "Sign in with an email address first — a guest "
                                 "session can't be reached from the extension.")
    token = auth.issue_api_token(who.user_id)
    return {"token": token, "expires_days": 180, "account": who.email}


@app.post("/api/auth/token/revoke")
async def revoke_token(request: Request):
    who = resolve_identity(request)
    if who is None or who.is_anonymous:
        raise HTTPException(401, "Not signed in.")
    auth.revoke_api_tokens(who.user_id)
    return {"revoked": True}


@app.post("/api/applications/capture")
async def capture(request: Request, payload: dict = Body(...)):
    """Record one application, captured by the extension.

    Deliberately thin: it stores the row and returns. No analysis runs here.
    A capture happens the moment someone clicks Apply, and making them wait on
    a statistics pass at that moment would be absurd — the analysis is a thing
    they come back for.
    """
    header = request.headers.get("authorization", "")
    token = header[7:] if header.lower().startswith("bearer ") else ""
    who = auth.identity_from_api_token(token)
    if who is None:
        raise HTTPException(401, "Invalid or revoked API token.")

    company = (payload.get("company") or "").strip()
    title = (payload.get("job_title") or "").strip()
    if not company or not title:
        raise HTTPException(400, "company and job_title are required.")

    raw_date = (payload.get("applied_date") or "").strip()
    parsed = parse_date(raw_date) if raw_date else None
    applied = (parsed.value if parsed else None) or date.today()

    row = {
        "row": 0,
        "company": company,
        "job_title": title,
        "applied_at": applied,
        "events": [(EventType.SUBMITTED, applied)],
        "date_imputed": False,
        "jd_text": (payload.get("jd_text") or "")[:20000],
        "resume_version": (payload.get("resume_version") or "default")[:80],
        "source": (payload.get("source") or "extension")[:40],
        "rejection_text": "",
        "location": (payload.get("location") or "")[:120],
    }

    res = upsert_applications(who.user_id, [row])
    return {
        "ok": True,
        "created": res["inserted"] == 1,
        "duplicate": res["inserted"] == 0,
        "total": res["total"],
        "company": company,
        "job_title": title,
    }


@app.get("/track", include_in_schema=False)
def track_page():
    return FileResponse(SITE / "track.html")


@app.get("/cohort", include_in_schema=False)
def cohort_page():
    return FileResponse(SITE / "cohort.html")


@app.get("/api/cohort/mine")
async def cohort_mine(request: Request, response: Response):
    who = ensure_identity(request, response)
    return {
        "owned": cohort.cohorts_for_owner(who.user_id),
        "joined": cohort.cohorts_for_member(who.user_id),
        "signed_in": not who.is_anonymous,
    }


@app.post("/api/cohort")
async def cohort_create(request: Request, response: Response,
                        name: str = Form(...)):
    who = ensure_identity(request, response)
    if who.is_anonymous:
        raise HTTPException(401, "Sign in before creating a cohort — a guest "
                                 "session can't be recovered if you lose the code.")
    try:
        return cohort.create_cohort(who.user_id, name)
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@app.post("/api/cohort/join")
async def cohort_join(request: Request, response: Response, code: str = Form(...)):
    who = ensure_identity(request, response)
    try:
        return cohort.join_cohort(who.user_id, code)
    except LookupError as exc:
        raise HTTPException(404, str(exc))


@app.post("/api/cohort/{cohort_id}/leave")
async def cohort_leave(cohort_id: int, request: Request, response: Response):
    who = ensure_identity(request, response)
    return cohort.leave_cohort(who.user_id, cohort_id)


@app.get("/api/cohort/{cohort_id}")
async def cohort_view(cohort_id: int, request: Request, response: Response):
    """Group report. Owner or member only — a join code is not a public URL."""
    who = ensure_identity(request, response)
    owned = {c["id"] for c in cohort.cohorts_for_owner(who.user_id)}
    joined = {c["id"] for c in cohort.cohorts_for_member(who.user_id)}
    if cohort_id not in owned and cohort_id not in joined:
        raise HTTPException(403, "You're not part of that cohort.")

    ids = cohort.member_ids(cohort_id)
    rep = cohort.cohort_report(ids)
    return {**rep.as_dict(), "is_owner": cohort_id in owned}


@app.get("/api/benchmark")
async def benchmark_view(request: Request, response: Response):
    """Where this person sits against everyone else."""
    who = ensure_identity(request, response)
    rows = load_applications(who.user_id)
    if not rows:
        return {"available": False,
                "verdict": "Add some applications and we'll compare you."}
    blank = ingest_csv(TEMPLATE_HEADER)
    blank.parsed = rows
    rep = analyze(blank, "")
    rate = rep.result.overall_response_rate
    return cohort.benchmark(who.user_id, rate).as_dict()


@app.get("/employers", include_in_schema=False)
def employers_page():
    return FileResponse(SITE / "employers.html")


@app.get("/api/employers")
async def employers_view():
    """Public reply-rate index. Gated hard — see core/cohort.py."""
    return cohort.employer_index()


@app.get("/ghost", include_in_schema=False)
def ghost_page():
    """Zero-friction front door: paste an advert, get an answer, no signup."""
    return FileResponse(SITE / "ghost.html")


@app.post("/api/ghost/check")
async def ghost_check(request: Request, response: Response,
                      jd_text: str = Form(""), company: str = Form("")):
    """Check one advert. Uses the caller's own history when they have any."""
    if not (jd_text or "").strip():
        raise HTTPException(400, "Paste the job advert first.")

    history = []
    who = resolve_identity(request)
    if who is not None:
        try:
            rows = load_applications(who.user_id)
            if rows:
                blank = ingest_csv(TEMPLATE_HEADER)   # empty report shell
                blank.parsed = rows
                history = analyze(blank, "").applications
        except Exception:  # noqa: BLE001
            history = []      # never let a history lookup break the free tool

    return ghost.check_with_history(jd_text, company, history)


@app.get("/api/track")
async def track_board(request: Request, response: Response):
    who = ensure_identity(request, response)
    data = tracker.board(who.user_id)
    data["stale"] = tracker.stale_cards(who.user_id)
    return data


@app.post("/api/track")
async def track_add(request: Request, response: Response,
                    company: str = Form(...), role: str = Form(...),
                    applied_date: str = Form(""), jd_text: str = Form(""),
                    resume_label: str = Form("default")):
    who = ensure_identity(request, response)
    when = parse_date(applied_date).value if applied_date.strip() else date.today()
    if when is None:
        raise HTTPException(400, "Couldn't read that date.")
    try:
        return tracker.add_job(who.user_id, company, role, when, jd_text, resume_label)
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@app.post("/api/track/{app_id}/move")
async def track_move(app_id: int, request: Request, response: Response,
                     column: str = Form(...)):
    who = ensure_identity(request, response)
    try:
        return tracker.move_card(who.user_id, app_id, column)
    except LookupError:
        raise HTTPException(404, "No such application.")
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@app.delete("/api/track/{app_id}")
async def track_delete(app_id: int, request: Request, response: Response):
    who = ensure_identity(request, response)
    try:
        return tracker.delete_card(who.user_id, app_id)
    except LookupError:
        raise HTTPException(404, "No such application.")


@app.post("/api/actions/{action_key}")
async def toggle_action(action_key: str, request: Request, response: Response,
                        done: bool = Form(True)):
    """Tick or untick one item. Persisted, so it survives the next analysis."""
    who = ensure_identity(request, response)
    return set_action(who.user_id, action_key, done)


@app.get("/api/jobs/{job_id}/stream")
async def job_stream(job_id: str):
    return StreamingResponse(
        stream_job(job_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            # nginx buffers SSE by default, which freezes the stepper and then
            # dumps every event at once when the response closes.
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/api/jobs/{job_id}")
async def job_status(job_id: str):
    """Polling fallback for clients where EventSource isn't available."""
    # Same cross-process concern as the SSE stream — read from whichever
    # store the worker is actually writing to.
    job = active_store().get(job_id)
    if job is None:
        raise HTTPException(404, "Job not found or expired.")
    return job.as_dict()
