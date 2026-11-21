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
from fastapi.responses import (FileResponse, JSONResponse, PlainTextResponse,
                               RedirectResponse)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.analyze import JD_CACHE, analyze, analyze_async, to_json  # noqa: E402
from core.ingest_v2 import (  # noqa: E402
    TEMPLATE_EXAMPLE, TEMPLATE_HEADER, ingest_csv, parse_date,
)
from core.enums import EventType  # noqa: E402
from core.narration import narrate_report, provider_status  # noqa: E402
import logging  # noqa: E402
import os  # noqa: E402
import uuid  # noqa: E402
from core.resume_parse import parse_resume  # noqa: E402
from core.db import db_info, init_db  # noqa: E402
from core import auth  # noqa: E402
from core import ratelimit  # noqa: E402
from core import oauth as oauth_mod  # noqa: E402
from core import passwords  # noqa: E402
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
    """Whoever this request belongs to, or None if there's no valid session.

    Cookie first, then a bearer token. The extension has no cookie — its
    requests are cross-origin, and it holds an API token instead. Without the
    second branch every route here is invisible to it, which is why the ghost
    check could only ever see the advert and never the caller's own history
    with that employer.
    """
    who = auth.read_session(request.cookies.get(auth.SESSION_COOKIE))
    if who is not None:
        return who

    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        return auth.identity_from_api_token(header[7:].strip())
    return None


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


@app.exception_handler(Exception)
async def unhandled_error(request: Request, exc: Exception):
    """One shape for every unexpected failure.

    A traceback in an HTTP response gives away the file layout, the library
    versions and often the contents of local variables — which here would
    include another account's rows. Log it with a reference, return a sentence
    the reader can act on.
    """
    ref = uuid.uuid4().hex[:8]
    logging.getLogger("rejectionintel").exception(
        "unhandled [%s] %s %s", ref, request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content={"detail": "Something went wrong on our side. Nothing you "
                           "uploaded was lost.", "ref": ref},
    )


@app.middleware("http")
async def security_headers(request: Request, call_next):
    """Headers that cost nothing and close real holes.

    The CSP allows inline script because the pages carry inline <script>
    blocks; removing 'unsafe-inline' needs per-request nonces and is a
    separate change. What it does enforce today is origin: no script, style,
    font or image may load from anywhere but this server. That is the same
    rule HANDOVER section 3 states in prose after a CDN was silently blocked
    by Brave and the chart degraded without erroring — now the browser
    enforces it instead of a code review.
    """
    response = await call_next(request)
    response.headers.setdefault("Content-Security-Policy", "; ".join([
        "default-src 'self'",
        "script-src 'self' 'unsafe-inline'",
        # Google Fonts is the one remaining third-party origin the pages
        # actually use. Listing it keeps the header honest; self-hosting the
        # two families would let both entries drop, which is what section 3
        # of the handover asks for.
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
        "img-src 'self' data:",
        "font-src 'self' data: https://fonts.gstatic.com",
        "connect-src 'self'",
        "base-uri 'self'",
        "form-action 'self'",
        "frame-ancestors 'none'",
    ]))
    # Stops a browser second-guessing a Content-Type, which is how a text
    # upload becomes executable script.
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    # A report URL should not travel to third parties in a Referer header.
    response.headers.setdefault("Referrer-Policy", "same-origin")
    response.headers.setdefault(
        "Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    return response


@app.get("/", include_in_schema=False)
def home():
    return FileResponse(SITE / "index.html")


@app.get("/signin", include_in_schema=False)
def signin_page():
    return FileResponse(SITE / "signin.html")


@app.get("/app", include_in_schema=False)
def upload_page():
    return FileResponse(SITE / "app.html")


@app.get("/backdrop.css", include_in_schema=False)
def backdrop_css():
    # no-store on the shared stylesheet. Three separate rounds of "the change
    # is not showing" turned out to be Brave serving a cached copy while the
    # file on disk was already correct, and a stale stylesheet is impossible to
    # tell apart from a bug in the CSS.
    return FileResponse(SITE / "backdrop.css", media_type="text/css",
                        headers={"Cache-Control": "no-store"})


@app.get("/backdrop.js", include_in_schema=False)
def backdrop_js():
    return FileResponse(SITE / "backdrop.js", media_type="application/javascript")


@app.get("/contour.svg", include_in_schema=False)
def contour_svg():
    return FileResponse(SITE / "contour.svg", media_type="image/svg+xml")


@app.get("/totop.js", include_in_schema=False)
def totop_js():
    return FileResponse(SITE / "totop.js", media_type="application/javascript")


@app.get("/authbox.js", include_in_schema=False)
def authbox_js():
    return FileResponse(SITE / "authbox.js", media_type="application/javascript")


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


# Strong references to in-flight background jobs.
#
# asyncio keeps only a WEAK reference to a task. A bare create_task() whose
# result nobody holds can therefore be garbage-collected mid-run, and the job
# stops silently — no exception, no failed state, just a stepper that spins
# forever. It survived before only because the coroutine happened to finish
# inside a couple of loop iterations; the moment any step waits on a thread the
# collector gets its window.
_JOBS: set = set()


def _spawn(coro):
    task = asyncio.create_task(coro)
    _JOBS.add(task)
    task.add_done_callback(_JOBS.discard)
    return task


async def _pipeline(job, csv_text: str, text: str, resume_meta: dict,
                    uid: int, extra_resumes: list[dict] | None = None) -> dict:
    """The analysis, instrumented for progress reporting.

    Identical work to before — the only change is that each stage announces
    itself, so the client can show what's happening instead of a spinner.
    """
    job.start("ingest")
    # asyncio.to_thread, not starlette's run_in_threadpool: this job runs in a
    # bare asyncio.create_task, outside anyio's task context, and anyio's
    # threadpool suspends forever there.
    # Off the event loop. Parsing a few hundred CSV rows is pure CPU, and while
    # it runs nothing else on this worker can be served — including the SSE
    # stream reporting this very job's progress.
    report_in = await asyncio.to_thread(ingest_csv, csv_text)
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
    resume_id = await asyncio.to_thread(
        save_resume, uid, text, resume_meta) if text.strip() else None

    # Store the other versions. Labelled from the filename so a CSV row tagged
    # "Resume B" lines up with the file the person actually sent.
    stored_versions = []
    for extra in (extra_resumes or []):
        await asyncio.to_thread(save_resume, uid, extra["text"], extra["meta"])
        stored_versions.append(extra["meta"].get("filename") or "version")
    if stored_versions:
        job.update("resume", f"{len(stored_versions) + 1} versions stored")
    job.finish("resume",
               f"{resume_meta.get('words', 0)} words from {resume_meta.get('source', 'text')}")

    # Both hit the database synchronously; on a slow connection this is the
    # longest blocking stretch in the whole request.
    upsert = await asyncio.to_thread(
        upsert_applications, uid, report_in.parsed, resume_id)
    history = await asyncio.to_thread(load_applications, uid)
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
    payload = await asyncio.to_thread(to_json, result)
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

    # method may carry the reason the model path was skipped, as
    # "vocabulary:<why>". Split it back out: the label drives the on-screen
    # note, the reason is for whoever is debugging why the better path is not
    # running. Silence here is how a broken model path survives for weeks.
    fallback_reason = ""
    if method.startswith("vocabulary:"):
        method, fallback_reason = "vocabulary", method.split(":", 1)[1]

    result = pre_apply.analyse(text, jd_text, extraction, method)
    payload = result.model_dump()
    if fallback_reason:
        payload["fallback_reason"] = fallback_reason

    # The rewrite shapes for lines that already exist. resume_diff never
    # invents a line for a skill the resume does not mention — every bracket is
    # a number only the reader can supply, and verify_suggestion() rejects any
    # digit that was not in the original. Rendering these next to the gap list
    # is what makes the page an action plan rather than a score.
    from core import resume_diff
    payload["resume_diff"] = resume_diff.to_json(
        resume_diff.analyse_resume(text, limit=5))

    # Which heading each skill sits under. Forty chips in one wall is a list
    # nobody reads; the same forty under six headings shows what the person
    # actually does. Computed here so the page never has to guess.
    from core.vocabulary import display_group
    names = list(payload.get("matched_skills", [])) + [
        m["skill_name"] for m in payload.get("missing_skills", [])]
    payload["skill_groups"] = {n: display_group(n) for n in names}

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
    _spawn(run_job(
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
    _spawn(run_job(job, lambda j: _pipeline(j, csv_text, text, meta, who.user_id)))
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
async def auth_request(request: Request, email: str = Form(...)):
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

    # Two windows: per address, and per client. Without them this endpoint
    # mails anyone, repeatedly, for free — abuse of a stranger's inbox and a
    # fast route to getting the sending domain blacklisted.
    client_ip = (request.client.host if request.client else "unknown")
    wait = ratelimit.check_sign_in(email, client_ip)
    if wait:
        minutes = max(1, round(wait / 60))
        raise HTTPException(429, f"Too many sign-in attempts. Try again in "
                                 f"about {minutes} minute"
                                 f"{'s' if minutes != 1 else ''}.")

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


@app.get("/api/auth/providers")
async def auth_providers():
    """Which sign-in methods this deployment can actually offer.

    The header renders buttons from this. A "Sign in with Google" button that
    500s because no client id is set is worse than no button.
    """
    return {"password": True, "magic_link": True,
            "oauth": oauth_mod.available()}


@app.post("/api/auth/signup")
async def auth_signup(request: Request, response: Response,
                      email: str = Form(...), password: str = Form(...)):
    """Create an account with a password.

    AN EXISTING ADDRESS DOES NOT GET AN ERROR. It gets a sign-in link and the
    same response body as a new account. Telling the caller "that email is
    taken" turns this form into the account-enumeration oracle that HANDOVER
    9.5 closed on /api/auth/request — and on a job-search tool, confirming
    somebody has an account is confirming they are job hunting.
    """
    email = (email or "").strip().lower()
    if "@" not in email or len(email) < 5:
        raise HTTPException(400, "That doesn't look like an email address.")

    client_ip = (request.client.host if request.client else "unknown")
    wait = ratelimit.check(f"signup:ip:{client_ip}", 6, 60 * 60)
    if wait:
        raise HTTPException(429, "Too many attempts. Try again later.")

    check = passwords.validate(password, email)
    if not check.ok:
        raise HTTPException(400, check.reason)

    who = auth.create_with_password(email, password)

    if who is None:
        # Address is taken. Same shape of answer, no new session, and a link
        # goes to the real owner so a legitimate returning user still gets in.
        token = auth.make_magic_token(email)
        mailer.send_magic_link(
            email, f"{mailer.APP_URL.rstrip('/')}/auth/verify?token={token}",
            minutes=15)
        return {"ok": True, "signed_in": False,
                "note": "Check your email to finish signing in."}

    response.set_cookie(auth.SESSION_COOKIE, auth.make_session(who),
                        **auth.cookie_kwargs())
    return {"ok": True, "signed_in": True, "display": who.display}


@app.post("/api/auth/login")
async def auth_login(request: Request, response: Response,
                     email: str = Form(...), password: str = Form(...)):
    """Password sign-in. Every failure is the same failure."""
    email = (email or "").strip().lower()
    client_ip = (request.client.host if request.client else "unknown")

    # Per address and per client. The address window stops one account being
    # ground down; the client window stops one attacker walking a list.
    wait = max(
        ratelimit.check(f"login:email:{email}", 8, 15 * 60),
        ratelimit.check(f"login:ip:{client_ip}", 30, 60 * 60),
    )
    if wait:
        minutes = max(1, round(wait / 60))
        raise HTTPException(429, f"Too many attempts. Try again in about "
                                 f"{minutes} minute{'s' if minutes != 1 else ''}.")

    who = auth.check_password(email, password)
    if who is None:
        # Wrong password, no such account, and account-without-a-password all
        # answer identically.
        raise HTTPException(401, "Email or password is incorrect.")

    response.set_cookie(auth.SESSION_COOKIE, auth.make_session(who),
                        **auth.cookie_kwargs())
    return {"ok": True, "signed_in": True, "display": who.display}


@app.post("/api/auth/set-password")
async def auth_set_password(request: Request, response: Response,
                            password: str = Form(...)):
    """Add or change a password on an account that signed in another way."""
    who = resolve_identity(request)
    if who is None or who.is_anonymous:
        raise HTTPException(401, "Sign in first.")

    check = passwords.validate(password, who.email)
    if not check.ok:
        raise HTTPException(400, check.reason)

    auth.set_password(who.user_id, password)
    # set_password rotates the session nonce, so this browser needs a fresh
    # cookie or it would sign itself out.
    response.set_cookie(auth.SESSION_COOKIE, auth.make_session(who),
                        **auth.cookie_kwargs())
    return {"ok": True, "note": "Password saved. Other devices were signed out."}


@app.get("/auth/oauth/{provider}", include_in_schema=False)
async def oauth_start(provider: str, next: str = "/app"):
    url = oauth_mod.start_url(provider, next)
    if url is None:
        return RedirectResponse("/signin?auth=provider_off", status_code=303)
    return RedirectResponse(url, status_code=303)


@app.get("/auth/callback/{provider}", include_in_schema=False)
async def oauth_callback(provider: str, code: str = "", state: str = "",
                         error: str = ""):
    """Provider redirect lands here.

    The signed state is checked before the code is spent: without it, anyone
    can make a victim's browser complete a sign-in as the attacker's account.
    """
    if error or not code:
        return RedirectResponse("/signin?auth=cancelled", status_code=303)

    payload = oauth_mod.read_state(state)
    if payload is None or payload.get("p") != provider:
        return RedirectResponse("/signin?auth=bad_state", status_code=303)

    profile = await oauth_mod.exchange(provider, code)
    if profile is None:
        return RedirectResponse("/signin?auth=oauth_failed", status_code=303)

    who = auth.link_oauth(profile.email, provider, profile.subject)
    if who is None:
        return RedirectResponse("/signin?auth=oauth_failed", status_code=303)

    dest = payload.get("next") or "/app"
    if not dest.startswith("/"):
        dest = "/app"        # never redirect off-site from a callback
    resp = RedirectResponse(f"{dest}?auth=ok", status_code=303)
    resp.set_cookie(auth.SESSION_COOKIE, auth.make_session(who),
                    **auth.cookie_kwargs())
    return resp


@app.post("/api/auth/logout")
async def auth_logout(response: Response):
    response.delete_cookie(auth.SESSION_COOKIE, path="/")
    return {"ok": True}


@app.post("/api/auth/logout-everywhere")
async def auth_logout_everywhere(request: Request, response: Response):
    """Invalidate every session and extension token for this account.

    The cookie lasts 30 days and the extension token 180. On a shared or lost
    machine, clearing one browser's cookie does nothing about either. This
    rotates both nonces, so every previously issued credential stops
    resolving on its next use.
    """
    who = resolve_identity(request)
    if who is None or who.is_anonymous:
        raise HTTPException(401, "Sign in first.")
    auth.revoke_sessions(who.user_id)
    auth.revoke_api_tokens(who.user_id)
    response.delete_cookie(auth.SESSION_COOKIE, path="/")
    return {"ok": True,
            "note": "Signed out on every device. The browser extension will "
                    "ask you to reconnect."}


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


@app.get("/api/ghost/history")
async def ghost_history(request: Request, company: str = ""):
    """What this person's own record says about one employer.

    Split out from /api/ghost/check so the page can look up the company the
    moment it is typed, rather than making the reader paste a job description
    first to learn something that has nothing to do with the job description.
    """
    name = (company or "").strip()
    if len(name) < 2:
        return {"prior": None}

    who = resolve_identity(request)
    if who is None or who.is_anonymous:
        return {"prior": None, "signed_in": False}

    rows = load_applications(who.user_id)
    payload = ghost.check_with_history("x" * 100, name, rows)
    return {"prior": payload.get("prior"), "signed_in": True}


@app.get("/api/cohort/signals")
async def cohort_signals(request: Request, company: str = ""):
    """One call for the extension: benchmark, this employer, and what has gone quiet.

    EVERY BRANCH CAN RETURN "NOT ENOUGH DATA YET" AND THAT IS THE POINT.
    The gates live in core/cohort.py and are not relaxed here. This endpoint
    additionally refuses any employer row describing fewer than K_ANON
    independent applicants, which the index already enforces at a higher
    threshold — the second check is there so that lowering one number in one
    file cannot quietly expose an individual through this route.

    Bearer-token callers are resolved by resolve_identity, so the extension
    reaches it without a cookie.
    """
    who = resolve_identity(request)
    out = {
        "signed_in": bool(who and not who.is_anonymous),
        "benchmark": {"available": False, "verdict": "Not enough data yet."},
        "employer": None,
        "stale": {"count": 0, "cards": []},
        # The threshold, read from the constant rather than written into the
        # page. A number typed into the UI drifts from the number the gate
        # enforces, and then the screen is lying about our own rules.
        "min_applicants": cohort.K_ANON,
    }
    if who is None:
        return out

    # --- how this person compares -----------------------------------------
    try:
        rows = load_applications(who.user_id)
        if rows:
            blank = ingest_csv(TEMPLATE_HEADER)
            blank.parsed = rows
            rep = analyze(blank, "")
            b = cohort.benchmark(who.user_id, rep.result.overall_response_rate).as_dict()
            out["benchmark"] = ({"available": True, "verdict": b.get("verdict", "")}
                                if b.get("available")
                                else {"available": False,
                                      "verdict": "Not enough data yet."})
    except Exception:  # noqa: BLE001
        pass          # a benchmark failing must not take the whole popup down

    # --- what everyone's history says about this employer ------------------
    name = (company or "").strip()
    if name:
        out["employer"] = {"available": False, "name": name,
                           "statement": "Not enough data yet."}
        try:
            index = cohort.employer_index()
            for row in (index.get("employers") or []):
                if (row.get("name") or "").strip().lower() != name.lower():
                    continue
                if int(row.get("applicants", 0)) < cohort.K_ANON:
                    break     # never describe a group this small
                out["employer"] = {"available": True, "name": row["name"],
                                   "statement": row.get("statement", "")}
                break
        except Exception:  # noqa: BLE001
            pass

    # --- applications that have gone quiet ---------------------------------
    try:
        stale = tracker.stale_cards(who.user_id)
        out["stale"] = {
            "count": len(stale),
            "cards": [{"id": c["id"], "company": c["company"], "role": c["role"],
                       "days_since": c["days_since"],
                       "applied_at": c.get("applied_at", "")}
                      for c in stale[:5]],
        }
    except Exception:  # noqa: BLE001
        pass

    return out


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


@app.get("/pre-apply", include_in_schema=False)
def preapply_page():
    """One advert against one resume, before it is sent."""
    return FileResponse(SITE / "preapply.html")


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
