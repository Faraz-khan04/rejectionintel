"""
Application Fit Score — deterministic, explainable, version-stamped.

This is the independent variable for the whole product. If it is arbitrary,
every downstream statistic is arbitrary too. So: no LLM, no learned weights
(yet), every sub-score bounded 0-100, and a breakdown dict that can be
rendered directly in the UI.

The weights below are a PRIOR, not a finding. They are wrong. The point of
shipping them is that once you have outcome data you can fit them — that is
the roadmap item, and until then the UI must not imply they were learned.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

from .enums import Seniority

ALGO_VERSION = "0.1.0"

WEIGHTS = {
    "skills_required": 0.40,
    "skills_preferred": 0.10,
    "experience": 0.20,
    "seniority": 0.20,
    "industry": 0.10,
}


@dataclass
class JobRequirements:
    required_skills: list[str] = field(default_factory=list)
    preferred_skills: list[str] = field(default_factory=list)
    min_years: int | None = None
    seniority: Seniority = Seniority.MID
    industry: str | None = None


@dataclass
class CandidateProfile:
    skills: dict[str, int] = field(default_factory=dict)   # canonical -> mentions
    skills_in_bullets: set[str] = field(default_factory=set)
    total_experience_months: int = 0
    seniority: Seniority = Seniority.MID
    industries: set[str] = field(default_factory=set)


@dataclass
class FitResult:
    overall: float
    skills_required: float
    skills_preferred: float
    experience: float
    seniority: float
    industry: float
    breakdown: dict
    algo_version: str = ALGO_VERSION

    def as_dict(self) -> dict:
        return asdict(self)


def _skill_coverage(
    needed: list[str], have: dict[str, int], in_bullets: set[str]
) -> tuple[float, dict]:
    """Coverage 0-100 with a quality discount.

    A skill that only appears in a comma-separated skills blob counts for less
    than one backed by a bullet, because a human screener reads them
    differently. This is a design opinion; it is applied consistently and it
    is visible in the breakdown, which is the most we can honestly claim.
    """
    if not needed:
        return 100.0, {"matched": [], "missing": [], "weak": [], "note": "no requirements parsed"}

    matched, missing, weak = [], [], []
    score = 0.0
    for skill in needed:
        if skill not in have:
            missing.append(skill)
            continue
        if skill in in_bullets:
            matched.append(skill)
            score += 1.0
        else:
            weak.append(skill)
            score += 0.5

    pct = 100.0 * score / len(needed)
    return pct, {"matched": matched, "weak": weak, "missing": missing}


def _experience_fit(months: int, min_years: int | None) -> tuple[float, dict]:
    """Under-qualification is penalised hard; over-qualification, mildly.

    Both directions cost you, which most fit scores ignore — being 8 years
    over the ask on a mid-level req is a real rejection driver.
    """
    if min_years is None:
        return 75.0, {"note": "no years requirement found in JD", "candidate_years": round(months / 12, 1)}

    years = months / 12.0
    delta = years - min_years

    if delta >= 0:
        # 0 to +3 years over: full marks. Beyond that, taper.
        score = 100.0 if delta <= 3 else max(55.0, 100.0 - (delta - 3) * 6.0)
    else:
        # Each year short costs 18 points.
        score = max(0.0, 100.0 + delta * 18.0)

    return score, {
        "candidate_years": round(years, 1),
        "required_years": min_years,
        "delta_years": round(delta, 1),
    }


def _seniority_fit(cand: Seniority, job: Seniority) -> tuple[float, dict]:
    delta = cand.rank - job.rank
    if delta == 0:
        score = 100.0
    elif delta == -1:
        score = 70.0     # one band up: a stretch, often viable
    elif delta < -1:
        score = max(0.0, 70.0 + (delta + 1) * 30.0)
    elif delta == 1:
        score = 75.0     # one band down: sometimes fine
    else:
        score = max(20.0, 75.0 - (delta - 1) * 25.0)

    return score, {
        "candidate_seniority": cand.value,
        "job_seniority": job.value,
        "delta_bands": delta,
    }


def _industry_fit(cand: set[str], job: str | None) -> tuple[float, dict]:
    if not job:
        return 70.0, {"note": "job industry unknown"}
    hit = job.lower() in {i.lower() for i in cand}
    return (100.0 if hit else 45.0), {
        "job_industry": job,
        "candidate_industries": sorted(cand),
        "match": hit,
    }


def compute_fit(profile: CandidateProfile, job: JobRequirements) -> FitResult:
    """Pure function. Same inputs -> same output, forever."""
    req_score, req_detail = _skill_coverage(
        job.required_skills, profile.skills, profile.skills_in_bullets
    )
    pref_score, pref_detail = _skill_coverage(
        job.preferred_skills, profile.skills, profile.skills_in_bullets
    )
    exp_score, exp_detail = _experience_fit(profile.total_experience_months, job.min_years)
    sen_score, sen_detail = _seniority_fit(profile.seniority, job.seniority)
    ind_score, ind_detail = _industry_fit(profile.industries, job.industry)

    overall = (
        WEIGHTS["skills_required"] * req_score
        + WEIGHTS["skills_preferred"] * pref_score
        + WEIGHTS["experience"] * exp_score
        + WEIGHTS["seniority"] * sen_score
        + WEIGHTS["industry"] * ind_score
    )

    return FitResult(
        overall=round(overall, 1),
        skills_required=round(req_score, 1),
        skills_preferred=round(pref_score, 1),
        experience=round(exp_score, 1),
        seniority=round(sen_score, 1),
        industry=round(ind_score, 1),
        breakdown={
            "weights": WEIGHTS,
            "skills_required": req_detail,
            "skills_preferred": pref_detail,
            "experience": exp_detail,
            "seniority": sen_detail,
            "industry": ind_detail,
        },
    )
