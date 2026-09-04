"""
Strategy layer — resume comparison and the learning roadmap.

TWO MODULES, TWO DIFFERENT EPISTEMIC PROBLEMS
---------------------------------------------

Resume A/B is the feature everyone asks for and it is fundamentally
underpowered. Detecting a doubling of a 5% response rate needs ~432
applications per arm. Nobody's job search generates that. So this module
reports what happened and states plainly how far short of a conclusion the
data falls. It never declares a winner, because declaring a winner from 30
applications is a coin flip with a confident voice.

The learning roadmap has the opposite property and it is underrated for it.
"31 of the roles you applied to required SQL" is a COUNT over the user's own
job descriptions. No hypothesis, no sampling, no power requirement. It is
exactly as true at n=10 as at n=1000 — which makes it the most useful thing
this product can say to a new user, and the only headline finding available
before the 25-application analysis gate opens.

The framing rule for the roadmap: eligibility, never outcome. "You would have
met the stated requirements of 31 more roles" is a fact about the postings.
"Learning SQL would raise your interview rate" is a prediction we cannot make.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .stats import required_n_per_arm, wilson_interval

STRATEGY_VERSION = "0.1.0"

MIN_ARM_FOR_COMPARISON = 8
MIN_ROLES_FOR_GAP = 3


# --------------------------------------------------------------------------
# Resume comparison
# --------------------------------------------------------------------------

@dataclass
class ResumeArm:
    label: str
    n: int
    responses: int

    @property
    def rate(self) -> float:
        return self.responses / self.n if self.n else 0.0

    @property
    def ci(self) -> tuple[float, float]:
        return wilson_interval(self.responses, self.n)


@dataclass
class ResumeComparison:
    arms: list[ResumeArm]
    verdict: str                 # "underpowered" | "separated" | "single" | "none"
    explanation: str
    needed_per_arm: int | None = None
    shortfall: int | None = None

    @property
    def leader(self) -> str | None:
        """Highest observed rate. NOT a winner — the verdict says whether the
        data can support that word, and usually it cannot."""
        usable = [a for a in self.arms if a.n >= MIN_ARM_FOR_COMPARISON]
        return max(usable, key=lambda a: a.rate).label if usable else None


def compare_resumes(apps) -> ResumeComparison:
    """Descriptive comparison of resume versions.

    Deliberately returns no winner. The `verdict` field exists so the UI cannot
    render this as a decision without also rendering why it isn't one.
    """
    from .deterministic import is_analyzable

    buckets: dict[str, list] = {}
    for a in apps:
        if not is_analyzable(a.outcome):
            continue
        buckets.setdefault(a.resume_label or "default", []).append(a)

    arms = [
        ResumeArm(label, len(items), sum(1 for x in items if x.responded))
        for label, items in sorted(buckets.items())
    ]
    arms.sort(key=lambda a: -a.n)

    if len(arms) < 2:
        return ResumeComparison(
            arms, "single",
            "Only one resume version is tagged in your data. Add a "
            "`resume_version` column to compare versions later — the comparison "
            "needs the tag at the time you apply, so it cannot be reconstructed.")

    usable = [a for a in arms if a.n >= MIN_ARM_FOR_COMPARISON]
    if len(usable) < 2:
        return ResumeComparison(
            arms, "none",
            f"No two versions have at least {MIN_ARM_FOR_COMPARISON} resolved "
            "applications, so there is nothing to compare yet.")

    top, second = usable[0], usable[1]
    baseline = min(top.rate, second.rate) or 0.05
    target = max(top.rate, second.rate)

    needed = None
    if target > baseline:
        needed = required_n_per_arm(baseline, target)
    shortfall = max(0, (needed or 0) - min(top.n, second.n)) if needed else None

    lo_hi = [(a.ci for a in (top, second))]
    separated = top.ci[0] > second.ci[1] or top.ci[1] < second.ci[0]

    if separated:
        return ResumeComparison(
            arms, "separated",
            f"The intervals for {top.label} and {second.label} do not overlap, "
            "which is unusual at this sample size and worth acting on.",
            needed, shortfall)

    detail = (
        f"The intervals overlap, so this difference is consistent with chance. "
        f"Telling {top.label} and {second.label} apart reliably would need about "
        f"{needed} applications per version — you have {top.n} and {second.n}."
        if needed else
        "The versions performed identically, so there is nothing to separate.")

    return ResumeComparison(arms, "underpowered", detail, needed, shortfall)


# --------------------------------------------------------------------------
# Learning roadmap
# --------------------------------------------------------------------------

@dataclass
class SkillGap:
    skill: str
    required_by: int             # roles that listed it as required
    required_by_unanswered: int  # of those, how many never responded
    total_roles: int

    @property
    def share_of_all(self) -> float:
        return self.required_by / self.total_roles if self.total_roles else 0.0

    @property
    def headline(self) -> str:
        """Eligibility, never outcome.

        "You would have met the stated requirements of 31 more roles" is a fact
        about the postings you applied to. "This would raise your interview
        rate" is a prediction with no counterfactual behind it.
        """
        return (f"Adding {self.skill} would have met the stated requirements of "
                f"{self.required_by} more roles "
                f"({self.share_of_all:.0%} of everything you applied to)")


@dataclass
class Roadmap:
    gaps: list[SkillGap] = field(default_factory=list)
    total_roles: int = 0
    note: str = ""

    @property
    def top(self) -> SkillGap | None:
        return self.gaps[0] if self.gaps else None


def learning_roadmap(apps, limit: int = 5) -> Roadmap:
    """Rank recurring skill gaps by how many roles required them.

    Works at any n. This is a tally over the user's own job descriptions, not a
    statistical test — which makes it the only substantive finding available
    before the 25-application gate opens.
    """
    counts: dict[str, int] = {}
    unanswered: dict[str, int] = {}
    total = len(apps)

    for a in apps:
        missing = a.fit.breakdown.get("skills_required", {}).get("missing", [])
        for skill in missing:
            counts[skill] = counts.get(skill, 0) + 1
            if not a.responded:
                unanswered[skill] = unanswered.get(skill, 0) + 1

    gaps = [
        SkillGap(skill, n, unanswered.get(skill, 0), total)
        for skill, n in counts.items() if n >= MIN_ROLES_FOR_GAP
    ]
    gaps.sort(key=lambda g: -g.required_by)

    if not gaps:
        return Roadmap([], total,
                       "No skill was required by three or more of your roles "
                       "and missing from your resume.")

    return Roadmap(gaps[:limit], total,
                   f"Ranked by how many of your {total} applications listed the "
                   "skill as required. These are counts, not predictions.")


def to_json(comparison: ResumeComparison, roadmap: Roadmap) -> dict:
    return {
        "resume_comparison": {
            "verdict": comparison.verdict,
            "explanation": comparison.explanation,
            "needed_per_arm": comparison.needed_per_arm,
            "shortfall": comparison.shortfall,
            "leader": comparison.leader,
            "arms": [
                {"label": a.label, "n": a.n, "responses": a.responses,
                 "rate": round(a.rate, 4),
                 "ci": [round(x, 4) for x in a.ci]}
                for a in comparison.arms
            ],
        },
        "roadmap": {
            "note": roadmap.note,
            "total_roles": roadmap.total_roles,
            "gaps": [
                {"skill": g.skill, "required_by": g.required_by,
                 "required_by_unanswered": g.required_by_unanswered,
                 "share_of_all": round(g.share_of_all, 4),
                 "headline": g.headline}
                for g in roadmap.gaps
            ],
        },
    }
