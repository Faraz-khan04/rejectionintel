"""
The deterministic-only analysis path.

This runs with NO API key and NO network. It uses the skill vocabulary and
regex extraction instead of LLM parsing of job descriptions. Recall is lower
than the LLM path, but every result is reproducible and free — which is
exactly what you want while developing locally.

When you add the LLM extraction stage, it plugs in here: replace
`_requirements_from_jd` with a cached lookup against `jobs.llm_extract` and
fall back to this function on a cache miss.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

from .deterministic import (
    SkillVocab, Tenure, default_vocab, derive_outcome,
    extract_min_years_required, infer_seniority, merge_tenure_months,
)
from .enums import Seniority
from .fit_score import CandidateProfile, JobRequirements, compute_fit
from .extract_llm import (
    ExtractionCache, canonicalize, extract_jobs, jd_hash, to_requirements,
)
from .evidence import ObservedFinding, aggregate, extract_reasons, screen
from .ingest_v2 import IngestReport
from .resume_diff import analyse_resume
from .resume_diff import to_json as diff_json
from .strategy import compare_resumes, learning_roadmap
from .strategy import to_json as strategy_json
from .pipeline import AnalyzedApplication, AnalysisResult, run_analysis

# Phrases that mark the requirements half of a JD. Everything after the first
# match is treated as "required", before it as context. Crude but honest, and
# it beats treating the whole posting as one bag of words.
_REQUIRED_MARKERS = re.compile(
    r"(what you'?ll need|requirements|qualifications|you have|must have|"
    r"we'?re looking for|minimum qualifications|required skills)", re.I
)
_PREFERRED_MARKERS = re.compile(
    r"(nice to have|preferred|bonus|plus if|desirable|good to have)", re.I
)


def _requirements_from_jd(jd: str, title: str, vocab: SkillVocab) -> JobRequirements:
    """Split a JD into required vs preferred skills without an LLM."""
    pref_match = _PREFERRED_MARKERS.search(jd)
    if pref_match:
        required_text, preferred_text = jd[: pref_match.start()], jd[pref_match.start():]
    else:
        required_text, preferred_text = jd, ""

    req = set(vocab.find(required_text))
    pref = set(vocab.find(preferred_text)) - req

    return JobRequirements(
        required_skills=sorted(req),
        preferred_skills=sorted(pref),
        min_years=extract_min_years_required(jd),
        seniority=infer_seniority(title),
        industry=None,
    )


_DATE_RANGE = re.compile(
    # The month between the dash and the year is the normal way a resume writes
    # a range — "Jan 1, 2021 - June 2022", "May 2017- Mar 2019". Requiring
    # year-dash-year saw one role in five on a real CV and reported 4.7 years
    # of experience for someone with nine, which is the kind of wrong a reader
    # notices in the first sentence and does not forgive.
    #
    # The leading month is captured too, so a range starting in November is not
    # counted as a full year.
    r"(?:(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+)?"
    r"(?:\d{1,2},?\s+)?"
    r"(\d{4})\s*[-–—]\s*"
    r"(?:(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+)?"
    r"(?:\d{1,2},?\s+)?"
    r"(present|current|now|\d{4})", re.I
)

_MONTHS = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
           "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12}


def parse_resume(text: str, vocab: SkillVocab, today: date | None = None) -> CandidateProfile:
    """Deterministic resume parse: skills, bullet placement, merged tenure."""
    today = today or date.today()

    skills = vocab.find(text)

    # A skill "in a bullet" means it appears on a line starting with a bullet
    # marker. That is a proxy for evidence rather than a claim.
    in_bullets: set[str] = set()
    for line in text.splitlines():
        stripped = line.strip()
        if stripped[:1] in {"-", "*", "•", "·"} or re.match(r"^\d+[.)]", stripped):
            in_bullets.update(vocab.find(stripped))

    tenures = []
    for m in _DATE_RANGE.finditer(text):
        start_month = _MONTHS.get((m.group(1) or "").lower()[:3], 1)
        start_year = int(m.group(2))
        end_raw = m.group(3)
        end = (None if end_raw.lower() in {"present", "current", "now"}
               else date(int(end_raw), 12, 31))
        tenures.append(Tenure(date(start_year, start_month, 1), end))

    months = merge_tenure_months(tenures, today=today)

    # Seniority from the most senior title line found in the resume.
    best = Seniority.MID
    for line in text.splitlines()[:80]:
        lvl = infer_seniority(line, default=Seniority.INTERN)
        if lvl.rank > best.rank and lvl is not Seniority.INTERN:
            best = lvl

    return CandidateProfile(
        skills=skills,
        skills_in_bullets=in_bullets,
        total_experience_months=months,
        seniority=best,
        industries=set(),
    )


def _make_cache():
    """DB-backed cache when a database is reachable, in-memory otherwise.

    This is the change that makes warming actually finish. On the free tier a
    report needs 88 extractions against a 20/day quota; an in-memory cache
    resets on every reload, so coverage never reaches 100% and the report is
    permanently stuck on regex parsing. Persisted, warming completes in a few
    days and stays complete — including across users, since a job posting is
    the same posting whoever applied to it.
    """
    try:
        from .store import DbExtractionCache
        from .db import init_db
        init_db()
        return DbExtractionCache()
    except Exception:  # noqa: BLE001
        return ExtractionCache()


JD_CACHE = _make_cache()


@dataclass
class ExtractionPlan:
    """Decides how a whole report is parsed — never per application.

    THE BIAS THIS PREVENTS
    ----------------------
    LLM extraction finds far more required skills than the regex vocabulary
    does. Same candidate, same job: regex scored 97.0, LLM scored 70.3 — a
    26.7 point gap caused purely by parsing method.

    Mix the two inside one report and every LLM-parsed application scores
    lower for reasons unrelated to the candidate. The stats layer would then
    detect that artefact as a genuine pattern and narrate it confidently.

    So extraction is all-or-nothing per report. Below full coverage we warm the
    cache in the background and score everything with regex, which is weaker
    but uniform. Uniformly weak beats unevenly strong when the output is
    statistics.
    """
    method: str                  # "llm" | "regex"
    unique_jds: int
    cached: int
    newly_extracted: int = 0
    budget: int = 0
    note: str = ""

    @property
    def coverage(self) -> float:
        # `cached` is the total AFTER extraction, so newly_extracted is already
        # included. Adding it again reported 200% coverage.
        return min(1.0, self.cached / self.unique_jds) if self.unique_jds else 1.0


@dataclass
class Report:
    result: AnalysisResult
    applications: list[AnalyzedApplication]
    profile: CandidateProfile
    ingest: IngestReport
    plan: ExtractionPlan | None = None
    observed: list[ObservedFinding] = field(default_factory=list)
    evidence_stats: dict = field(default_factory=dict)
    # Appended, never inserted. Adding a field in the middle silently reassigns
    # every positional argument after it — which is exactly what happened here:
    # `observed` landed in profile_text and evidence_stats landed in observed.
    profile_text: str = ""


async def analyze_async(ingest: IngestReport, resume_text: str,
                        as_of: date | None = None, provider=None,
                        mode: str = "auto", job=None) -> Report:
    """Analysis with optional LLM job-description extraction.

    `mode`: auto (LLM once the cache covers every JD) | llm (force) | regex.
    `job`: optional core.tasks.Job for progress reporting.
    """
    as_of = as_of or date.today()
    vocab = default_vocab()
    profile = parse_resume(resume_text, vocab, today=as_of)

    jds = [(r["job_title"], r["jd_text"]) for r in ingest.parsed if r["jd_text"].strip()]
    hashes = {jd_hash(t) for _, t in jds}
    # One query for the whole report rather than 88 individual lookups.
    if hasattr(JD_CACHE, "load") and hashes:
        JD_CACHE.load(hashes)
    cached_now = sum(1 for h in hashes if h in JD_CACHE._store)

    plan = ExtractionPlan(method="regex", unique_jds=len(hashes), cached=cached_now)

    if mode != "regex" and provider is not None and hashes:
        plan.budget = provider.info.call_budget
        missing = len(hashes) - cached_now
        if job:
            job.update("jobs", f"{cached_now}/{len(hashes)} job descriptions cached")

        if missing:
            # Warm up to the budget. These calls are never wasted — they carry
            # over to the next run, and to every other user with the same JD.
            res = await extract_jobs(jds, provider=provider, cache=JD_CACHE,
                                     budget=plan.budget)
            plan.newly_extracted = res.from_llm
            cached_now += res.from_llm

        if cached_now >= len(hashes) or mode == "llm":
            plan.method = "llm"
            plan.cached = cached_now
            plan.note = f"All {len(hashes)} job descriptions parsed by {provider.info.model}."
        else:
            plan.cached = cached_now
            plan.note = (
                f"Job descriptions parsed with the built-in vocabulary. "
                f"{cached_now} of {len(hashes)} are now cached for the model — "
                f"re-run to improve coverage. Mixing both methods in one report "
                f"would bias every fit score, so we use one or the other.")
    elif provider is None:
        plan.note = "No LLM provider configured — using the built-in skill vocabulary."

    if job:
        job.finish("jobs", plan.note[:80])

    apps: list[AnalyzedApplication] = []
    for i, row in enumerate(ingest.parsed):
        if plan.method == "llm":
            extracted = JD_CACHE._store.get(jd_hash(row["jd_text"]))
            reqs = (to_requirements(extracted) if extracted
                    else _requirements_from_jd(row["jd_text"], row["job_title"], vocab))
        else:
            reqs = _requirements_from_jd(row["jd_text"], row["job_title"], vocab)

        fit = compute_fit(profile, reqs)
        outcome = derive_outcome(row["applied_at"], row["events"], as_of=as_of)
        apps.append(AnalyzedApplication(
            app_id=i, job_title=row["job_title"], company=row["company"],
            fit=fit, outcome=outcome, required_skills=reqs.required_skills,
            industry=reqs.industry, source=row["source"],
            resume_label=row["resume_version"]))

    # OBSERVED tier. Runs last and separately: it is not a statistical result,
    # it is a count of things employers wrote down. No FDR correction applies
    # because no hypothesis is being tested.
    texts = [(i, r.get("rejection_text") or "")
             for i, r in enumerate(ingest.parsed) if (r.get("rejection_text") or "").strip()]
    observed: list[ObservedFinding] = []
    ev_stats: dict = {"with_text": len(texts), "screened_out": 0, "extracted": 0,
                      "rejected_spans": 0}

    if texts:
        if job:
            job.update("stats", f"reading {len(texts)} rejection messages")
        ev = await extract_reasons(
            texts, provider=provider if mode != "regex" else None,
            budget=provider.info.call_budget if provider else None)
        observed = aggregate(ev.evidence)
        ev_stats.update({
            "screened_out": ev.screened_out,
            "extracted": ev.extracted,
            "rejected_spans": ev.rejected_spans,
            "actionable": len(ev.actionable),
            "notes": ev.notes[:5],
        })

    return Report(
        result=run_analysis(apps), applications=apps, profile=profile,
        ingest=ingest, plan=plan, observed=observed, evidence_stats=ev_stats,
        profile_text=resume_text,
    )


def strategy_for(report) -> dict:
    """Resume comparison + learning roadmap.

    Kept out of the statistics layer on purpose: the roadmap is a tally over
    the user's own postings, and the comparison is explicitly non-inferential.
    Neither is a hypothesis test, so neither belongs under FDR correction.
    """
    return strategy_json(
        compare_resumes(report.applications),
        learning_roadmap(report.applications),
    )


def analyze(ingest: IngestReport, resume_text: str,
            as_of: date | None = None) -> Report:
    as_of = as_of or date.today()
    vocab = default_vocab()
    profile = parse_resume(resume_text, vocab, today=as_of)

    apps: list[AnalyzedApplication] = []
    for i, row in enumerate(ingest.parsed):
        reqs = _requirements_from_jd(row["jd_text"], row["job_title"], vocab)
        fit = compute_fit(profile, reqs)
        outcome = derive_outcome(row["applied_at"], row["events"], as_of=as_of)
        apps.append(AnalyzedApplication(
            app_id=i,
            job_title=row["job_title"],
            company=row["company"],
            fit=fit,
            outcome=outcome,
            required_skills=reqs.required_skills,
            industry=None,
            source=row["source"],
            resume_label=row["resume_version"],
        ))

    return Report(
        result=run_analysis(apps), applications=apps, profile=profile,
        ingest=ingest,
        plan=ExtractionPlan(method="regex", unique_jds=0, cached=0,
                            note="Deterministic extraction only."),
        profile_text=resume_text,
    )


def to_json(report: Report) -> dict:
    """Serialise for the frontend. Keeps tier and gate_reason on every finding
    so the UI cannot accidentally render a weak result as a strong one."""
    r = report.result
    return {
        "ingest": {
            "rows_total": report.ingest.rows_total,
            "rows_accepted": report.ingest.rows_accepted,
            "version": report.ingest.version,
            "day_first": report.ingest.day_first,
            "columns": {
                k: {"header": v.header, "method": v.method, "confidence": v.confidence}
                for k, v in report.ingest.columns.items()
            },
            "unmatched_columns": report.ingest.unmatched_columns,
            "issues": [
                {"row": i.row, "column": i.column, "problem": i.problem, "severity": i.severity}
                for i in report.ingest.issues[:40]
            ],
        },
        "extraction": {
            "method": report.plan.method if report.plan else "regex",
            "unique_jds": report.plan.unique_jds if report.plan else 0,
            "cached": report.plan.cached if report.plan else 0,
            "newly_extracted": report.plan.newly_extracted if report.plan else 0,
            "coverage": round(report.plan.coverage, 3) if report.plan else 1.0,
            "note": report.plan.note if report.plan else "",
        },
        "profile": {
            "experience_years": round(report.profile.total_experience_months / 12, 1),
            "seniority": report.profile.seniority.value,
            "skills_found": sorted(report.profile.skills),
        },
        "summary": {
            "n_total": r.n_total,
            "n_analyzable": r.n_analyzable,
            "response_rate": round(r.overall_response_rate, 4),
            "response_ci": [round(x, 4) for x in r.overall_ci],
            "funnel": r.funnel,
            "blocked_reason": r.blocked_reason,
        },
        "observed": [
            {
                "dimension": f.dimension,
                "tier": "observed",
                "headline": f.headline,
                "category": f.category,
                "skill": f.skill,
                "quotes": f.quotes,
                "employer_count": f.employer_count,
            }
            for f in (report.observed or [])
        ],
        "evidence_stats": report.evidence_stats or {},
        "strategy": strategy_for(report),
        # Line-level resume feedback. Suggestions are shapes with blanks —
        # see core/resume_diff.py for why we never fill in a number.
        "resume_lines": diff_json(analyse_resume(
            report.profile_text or "",
            skills_listed_only=sorted(
                set(report.profile.skills) - report.profile.skills_in_bullets),
        )),
        "findings": [
            {
                "dimension": f.dimension,
                "label": f.label or f.dimension,
                "label_with": f.label_with,
                "label_without": f.label_without,
                "tier": f.confidence_tier.value,
                "n_exposed": f.n_exposed,
                "n_unexposed": f.n_unexposed,
                "rate_exposed": round(f.rate_exposed, 4),
                "rate_unexposed": round(f.rate_unexposed, 4),
                "ci_exposed": [round(x, 4) for x in f.ci_exposed],
                "ci_unexposed": [round(x, 4) for x in f.ci_unexposed],
                "risk_ratio": round(f.risk_ratio, 2) if f.risk_ratio else None,
                "p_value": round(f.p_value, 5) if f.p_value else None,
                "q_value": round(f.q_value, 4) if f.q_value else None,
                "gate_reason": f.gate_reason,
                "exposed_ids": f.exposed_ids,
                "unexposed_ids": f.unexposed_ids,
            }
            for f in r.findings
        ],
        "applications": [
            {
                "id": a.app_id,
                "company": a.company,
                "title": a.job_title,
                "fit": a.fit.overall,
                "stage": a.outcome.furthest_stage.value,
                "resolution": a.outcome.resolution.value,
                "responded": a.responded,
                "missing": a.fit.breakdown["skills_required"].get("missing", []),
            }
            for a in report.applications
        ],
    }
