"""
Stage orchestration: Raw -> Structured -> Deterministic Math -> Stats -> LLM.

Each stage is idempotent and separately re-runnable. That matters because
you will change the fit-score weights fifteen times, and re-running stage 3
must not mean re-paying for stage 2's LLM extraction.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from .deterministic import (
    DerivedOutcome, SkillVocab, derive_outcome, extract_min_years_required,
    got_response, infer_seniority, is_analyzable,
)
from .fit_score import CandidateProfile, FitResult, JobRequirements, compute_fit
from .enums import ConfidenceTier, EventType, Stage
from .stats import (
    MIN_RESOLVED, Contingency, Finding, adjust_run, test_contingency,
    wilson_interval,
)


@dataclass
class AnalyzedApplication:
    app_id: int
    job_title: str
    company: str
    fit: FitResult
    outcome: DerivedOutcome
    required_skills: list[str]
    industry: str | None
    source: str | None
    resume_label: str | None

    @property
    def responded(self) -> bool:
        return got_response(self.outcome)


# --------------------------------------------------------------------------
# Stage 3 -> 4: turn applications into 2x2 tables
# --------------------------------------------------------------------------

def _contingency(
    apps: list[AnalyzedApplication], dimension: str, predicate,
    label: str = "", label_with: str = "", label_without: str = "",
) -> Contingency:
    es = ef = us = uf = 0
    exposed_ids: list[int] = []
    unexposed_ids: list[int] = []
    for a in apps:
        exposed = predicate(a)
        if exposed:
            exposed_ids.append(a.app_id)
            if a.responded:
                es += 1
            else:
                ef += 1
        else:
            unexposed_ids.append(a.app_id)
            if a.responded:
                us += 1
            else:
                uf += 1
    return Contingency(dimension, es, ef, us, uf, exposed_ids, unexposed_ids,
                       label or dimension, label_with, label_without)


def build_hypotheses(apps: list[AnalyzedApplication]) -> list[Contingency]:
    """Every hypothesis tested in a run. Keep this list SHORT and PRE-REGISTERED.

    The temptation is to auto-generate a hypothesis per skill, per company,
    per month — hundreds of tests. FDR correction then kills everything, and
    rightly so. A small curated list of a priori plausible drivers has far
    more power. Adding a hypothesis here has a statistical cost; treat it as
    a product decision, not a loop.
    """
    tables: list[Contingency] = []

    # 1. Fit score — the core hypothesis of the product.
    tables.append(_contingency(
        apps, "fit_score >= 80", lambda a: a.fit.overall >= 80,
        "How well you matched the role",
        "Roles you matched well", "Roles you matched poorly"))
    tables.append(_contingency(
        apps, "fit_score >= 65", lambda a: a.fit.overall >= 65,
        "How well you matched the role (looser cut-off)",
        "Reasonable match", "Weak match"))

    # 2. Seniority mismatch
    tables.append(_contingency(
        apps, "seniority_mismatch",
        lambda a: a.fit.breakdown["seniority"].get("delta_bands", 0) != 0,
        "Applying above or below your level",
        "Roles at a different level to yours", "Roles at your level"))

    # 3. Experience shortfall
    tables.append(_contingency(
        apps, "under_required_experience",
        lambda a: (a.fit.breakdown["experience"].get("delta_years") or 0) < 0,
        "Applying with less experience than asked for",
        "Roles wanting more experience than you have",
        "Roles where you met the experience bar"))

    # 4. Referral vs cold
    tables.append(_contingency(
        apps, "referral", lambda a: a.source == "referral",
        "Applying through a referral",
        "Applications through a referral", "Applications sent cold"))

    # 5. Most frequently REQUIRED skills — capped at 3.
    #    Note this is "the JD asked for X", not "the resume lacks X". If we only
    #    tested missing skills, a candidate who already has a skill could never
    #    learn that roles demanding it go badly for them — and that is exactly
    #    the question worth asking.
    req_counts: dict[str, int] = {}
    for a in apps:
        for s_ in a.required_skills:
            req_counts[s_] = req_counts.get(s_, 0) + 1
    for skill, _ in sorted(req_counts.items(), key=lambda x: -x[1])[:3]:
        tables.append(_contingency(
            apps, f"requires:{skill}", lambda a, s2=skill: s2 in a.required_skills,
            f"Jobs that asked for {skill}",
            f"Jobs asking for {skill}", f"Jobs not asking for {skill}"))

    # 6. Skill gaps the candidate actually has — capped at 2.
    gap_counts: dict[str, int] = {}
    for a in apps:
        for s_ in a.fit.breakdown["skills_required"].get("missing", []):
            gap_counts[s_] = gap_counts.get(s_, 0) + 1
    for skill, _ in sorted(gap_counts.items(), key=lambda x: -x[1])[:2]:
        dim = f"missing:{skill}"
        if any(t.dimension == dim for t in tables):
            continue
        tables.append(_contingency(
            apps, dim,
            lambda a, s2=skill: s2 in a.fit.breakdown["skills_required"].get("missing", []),
            f"Jobs wanting {skill}, which isn't on your resume",
            f"Jobs wanting {skill}", f"Jobs not wanting {skill}"))

    # Drop hypotheses with an empty arm before they reach FDR correction —
    # an untestable question should not consume a comparison slot.
    live = [t for t in tables if t.n_exposed > 0 and t.n_unexposed > 0]

    # Deduplicate by MEMBERSHIP, not by name. "Jobs that asked for Looker" and
    # "Jobs wanting Looker, which isn't on your resume" select exactly the same
    # applications when the candidate lacks Looker — so they are one hypothesis
    # wearing two labels. Testing both burns a comparison and prints the same
    # result twice. The later one wins because its label is more specific.
    by_members: dict[tuple, Contingency] = {}
    for t in live:
        key = (tuple(sorted(t.exposed_ids)), tuple(sorted(t.unexposed_ids)))
        by_members[key] = t          # last write wins
    return list(by_members.values())


@dataclass
class AnalysisResult:
    n_total: int
    n_analyzable: int
    overall_response_rate: float
    overall_ci: tuple[float, float]
    funnel: dict[str, int]
    findings: list[Finding]
    blocked_reason: str | None = None


def run_analysis(apps: list[AnalyzedApplication]) -> AnalysisResult:
    analyzable = [a for a in apps if is_analyzable(a.outcome)]

    funnel = {s.value: 0 for s in Stage}
    for a in analyzable:
        for s in Stage:
            if a.outcome.furthest_stage.rank >= s.rank:
                funnel[s.value] += 1

    responses = sum(1 for a in analyzable if a.responded)
    rate = responses / len(analyzable) if analyzable else 0.0
    ci = wilson_interval(responses, len(analyzable))

    if len(analyzable) < MIN_RESOLVED:
        return AnalysisResult(
            n_total=len(apps), n_analyzable=len(analyzable),
            overall_response_rate=rate, overall_ci=ci, funnel=funnel, findings=[],
            blocked_reason=(
                f"Only {len(analyzable)} resolved applications. "
                f"We need {MIN_RESOLVED} before running pattern analysis. "
                "Showing your funnel only."
            ),
        )

    findings = [test_contingency(t) for t in build_hypotheses(analyzable)]
    return AnalysisResult(
        n_total=len(apps), n_analyzable=len(analyzable),
        overall_response_rate=rate, overall_ci=ci, funnel=funnel,
        findings=adjust_run(findings),
    )
