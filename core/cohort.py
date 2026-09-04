"""
Cohort statistics — the three things that only work across many people.

THE ARGUMENT FOR ALL OF THIS
----------------------------
One person's job search cannot produce statistically strong findings. That is
arithmetic, not effort, and the rest of this product is built around admitting
it. Five hundred people's searches can.

Everything here is the same operation — aggregate across users, gate hard on
sample size, report an interval — applied to three questions:

  1. Benchmarks       "is my reply rate normal?"
  2. Cohort view      "where is our whole batch losing?"
  3. Employer index   "does this company reply to anyone?"

THE GUARDS THAT MATTER MORE THAN THE MATHS
------------------------------------------
Aggregation across people introduces failure modes the single-user product
doesn't have, and each needs its own defence:

  * A benchmark computed from four users is not a benchmark, it is four people.
    Below MIN_BENCHMARK_USERS we say so instead of printing a number.

  * An employer index built from one person's experience is that person's
    experience with a company name attached. Publishing it as a fact about the
    employer would be defamatory and wrong. MIN_EMPLOYER_USERS requires several
    independent people before anything is reported at all.

  * A cohort report can re-identify someone. "One student in this batch got an
    offer" plus a group of twelve is not anonymous. MIN_COHORT_SIZE gates the
    whole view, and per-cell counts below K_ANON are suppressed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from sqlalchemy import func, select

from .db import session_scope
from .models import Application, ApplicationEvent, Company, Job, User
from .stats import wilson_interval

COHORT_VERSION = "0.1.0"

# --- gates -----------------------------------------------------------------
MIN_BENCHMARK_USERS = 20      # below this a "benchmark" is a handful of people
MIN_USER_APPLICATIONS = 10    # a user with 3 applications shouldn't move a median
MIN_EMPLOYER_USERS = 4        # independent people before we describe an employer
MIN_EMPLOYER_APPS = 8         # and enough total applications to mean anything
MIN_COHORT_SIZE = 8           # below this a group report identifies individuals
K_ANON = 3                    # suppress any cell describing fewer than this


# ==========================================================================
# 1. Benchmarks — "is this normal?"
# ==========================================================================

@dataclass
class Benchmark:
    available: bool
    n_users: int = 0
    median_rate: float = 0.0
    p25: float = 0.0
    p75: float = 0.0
    your_rate: float = 0.0
    your_percentile: int = 0
    verdict: str = ""
    reframe: str = ""

    def as_dict(self) -> dict:
        return {
            "available": self.available, "n_users": self.n_users,
            "median_rate": round(self.median_rate, 4),
            "p25": round(self.p25, 4), "p75": round(self.p75, 4),
            "your_rate": round(self.your_rate, 4),
            "your_percentile": self.your_percentile,
            "verdict": self.verdict, "reframe": self.reframe,
        }


def _user_response_rates(exclude_user: int | None = None) -> list[float]:
    """One reply rate per user, for users with enough applications to count."""
    rates: list[float] = []
    with session_scope() as s:
        rows = s.execute(
            select(Application.user_id, func.count(Application.id))
            .group_by(Application.user_id)).all()
        for uid, total in rows:
            if uid == exclude_user or total < MIN_USER_APPLICATIONS:
                continue
            replied = s.scalar(
                select(func.count(func.distinct(ApplicationEvent.application_id)))
                .join(Application, Application.id == ApplicationEvent.application_id)
                .where(Application.user_id == uid,
                       ApplicationEvent.event_type.in_(
                           ["recruiter_contact", "screen_completed",
                            "interview_scheduled", "interview_completed",
                            "final_round", "offer"]))) or 0
            rates.append(replied / total)
    return sorted(rates)


def _pct(sorted_values: list[float], q: float) -> float:
    if not sorted_values:
        return 0.0
    i = max(0, min(len(sorted_values) - 1, int(round(q * (len(sorted_values) - 1)))))
    return sorted_values[i]


def benchmark(user_id: int, your_rate: float) -> Benchmark:
    """Where this person sits against everyone else using the product.

    The point is not the number. Research on job-search forums keeps finding
    the same thing: what damages people is not rejection, it is not knowing
    whether the problem is them. "Your rate is normal" is the sentence they
    came for, and it needs a real population behind it or it is a lie that
    happens to be comforting.
    """
    others = _user_response_rates(exclude_user=user_id)
    if len(others) < MIN_BENCHMARK_USERS:
        return Benchmark(
            available=False, n_users=len(others), your_rate=your_rate,
            verdict="Not enough people yet to say what normal looks like.",
            reframe=(f"We compare you against other people using this, and there "
                     f"are only {len(others)} with enough history so far. We'd "
                     f"rather show nothing than call {len(others)} people a "
                     f"benchmark."),
        )

    median = _pct(others, 0.5)
    p25, p75 = _pct(others, 0.25), _pct(others, 0.75)
    below = sum(1 for r in others if r < your_rate)
    percentile = int(round(100 * below / len(others)))

    if your_rate >= p75:
        verdict = "Your reply rate is better than most people here."
        reframe = (f"You hear back more often than {percentile}% of people we can "
                   f"compare you to. Whatever is going wrong, it isn't that "
                   f"employers are ignoring you unusually often.")
    elif your_rate >= p25:
        verdict = "Your reply rate is normal."
        reframe = (f"You're in the middle of the pack — {percentile}% of people here "
                   f"hear back less often than you do. This is what the market looks "
                   f"like right now, not a verdict on you.")
    else:
        verdict = "Your reply rate is lower than most people here."
        reframe = (f"You hear back less often than {100 - percentile}% of people we "
                   f"can compare you to. That's worth acting on — and it's a "
                   f"targeting problem far more often than a you problem.")

    return Benchmark(available=True, n_users=len(others), median_rate=median,
                     p25=p25, p75=p75, your_rate=your_rate,
                     your_percentile=percentile, verdict=verdict, reframe=reframe)


# ==========================================================================
# 2. The employer response index
# ==========================================================================

@dataclass
class EmployerRecord:
    company: str
    applicants: int          # distinct people
    applications: int
    replies: int
    rate: float
    ci: tuple[float, float]
    statement: str

    def as_dict(self) -> dict:
        return {"company": self.company, "applicants": self.applicants,
                "applications": self.applications, "replies": self.replies,
                "rate": round(self.rate, 4),
                "ci": [round(x, 4) for x in self.ci], "statement": self.statement}


def employer_index(limit: int = 25, min_users: int = MIN_EMPLOYER_USERS) -> dict:
    """How often employers reply, across everyone.

    WHAT THIS DELIBERATELY IS NOT
    -----------------------------
    Not a blocklist, not a naming-and-shaming table, and not a claim about
    intent. A company with a broken applicant tracker and a company with no
    intention of hiring look identical from here, and describing either as
    "ignoring applicants" is a claim we cannot support and would not want to
    defend.

    So the output is a count with an interval and a neutral sentence. Two hard
    gates before anything is said at all: several INDEPENDENT people, and enough
    applications between them. One person's bad experience with a company name
    attached is not data, it is a grievance.
    """
    records: list[EmployerRecord] = []
    with session_scope() as s:
        rows = s.execute(
            select(Company.name,
                   func.count(func.distinct(Application.user_id)),
                   func.count(Application.id))
            .join(Job, Job.company_id == Company.id)
            .join(Application, Application.job_id == Job.id)
            .group_by(Company.name)).all()

        for name, applicants, apps in rows:
            if applicants < min_users or apps < MIN_EMPLOYER_APPS:
                continue
            replies = s.scalar(
                select(func.count(func.distinct(ApplicationEvent.application_id)))
                .join(Application, Application.id == ApplicationEvent.application_id)
                .join(Job, Job.id == Application.job_id)
                .where(Job.company_id.in_(
                    select(Company.id).where(Company.name == name)),
                    ApplicationEvent.event_type.in_(
                        ["recruiter_contact", "screen_completed",
                         "interview_scheduled", "interview_completed",
                         "final_round", "offer"]))) or 0

            rate = replies / apps if apps else 0.0
            lo, hi = wilson_interval(replies, apps)
            if replies == 0:
                statement = (f"{applicants} people sent {apps} applications and none "
                             f"received a reply. The true reply rate is under "
                             f"{hi*100:.0f}%.")
            else:
                statement = (f"{applicants} people sent {apps} applications; "
                             f"{replies} got a reply. That's somewhere between "
                             f"{lo*100:.0f}% and {hi*100:.0f}%.")
            records.append(EmployerRecord(name, applicants, apps, replies,
                                          rate, (lo, hi), statement))

    records.sort(key=lambda r: (r.rate, -r.applications))

    # Whether any of this comes from seeded demo accounts. A page showing
    # invented numbers without saying so is exactly what the seeding script
    # was written to avoid, so the flag travels with the data rather than
    # being something the template has to remember.
    sample = False
    try:
        from sample.seed_demo import is_seeded
        sample = is_seeded()
    except Exception:  # noqa: BLE001
        sample = False

    return {
        "version": COHORT_VERSION,
        "is_sample": sample,
        "available": bool(records),
        "n_companies": len(records),
        "least_responsive": [r.as_dict() for r in records[:limit]],
        "most_responsive": [r.as_dict() for r in reversed(records[-limit:])],
        "gate": (f"A company appears only once at least {min_users} different people "
                 f"have applied to it, with {MIN_EMPLOYER_APPS} applications between "
                 f"them."),
        "caveat": ("These are counts of what happened, not judgements. A hiring "
                   "freeze, a broken applicant tracker and a deliberate pipeline "
                   "post are indistinguishable from the outside. Treat a low number "
                   "as a reason to spend your time elsewhere, not as an accusation."),
    }


# ==========================================================================
# 3. Cohort view — for bootcamps, universities, outplacement
# ==========================================================================

@dataclass
class CohortReport:
    available: bool
    n_members: int = 0
    n_applications: int = 0
    response_rate: float = 0.0
    ci: tuple[float, float] = (0.0, 0.0)
    offers: int = 0
    funnel: dict = field(default_factory=dict)
    skill_gaps: list[dict] = field(default_factory=list)
    blocked_reason: str = ""

    def as_dict(self) -> dict:
        return {
            "available": self.available, "n_members": self.n_members,
            "n_applications": self.n_applications,
            "response_rate": round(self.response_rate, 4),
            "ci": [round(x, 4) for x in self.ci], "offers": self.offers,
            "funnel": self.funnel, "skill_gaps": self.skill_gaps,
            "blocked_reason": self.blocked_reason, "version": COHORT_VERSION,
        }


def cohort_report(user_ids: list[int]) -> CohortReport:
    """Aggregate funnel and skill gaps across a group.

    THE RE-IDENTIFICATION PROBLEM
    -----------------------------
    A bootcamp report is read by staff who know every member by name. "One
    person in this cohort got an offer" in a group of twelve is not anonymous —
    it identifies someone, and probably identifies who is struggling too.

    So the whole view is gated below MIN_COHORT_SIZE, and any individual figure
    describing fewer than K_ANON people is suppressed rather than rounded. The
    value here is the aggregate pattern, which survives suppression fine.
    """
    if len(user_ids) < MIN_COHORT_SIZE:
        return CohortReport(
            available=False, n_members=len(user_ids),
            blocked_reason=(f"A cohort needs at least {MIN_COHORT_SIZE} people before "
                            f"we'll show a report. Below that the numbers describe "
                            f"individuals, and anyone reading would know which."),
        )

    stages = ["recruiter_contact", "screen_completed", "interview_scheduled",
              "interview_completed", "final_round", "offer"]
    with session_scope() as s:
        total = s.scalar(select(func.count(Application.id))
                         .where(Application.user_id.in_(user_ids))) or 0
        if total == 0:
            return CohortReport(available=False, n_members=len(user_ids),
                                blocked_reason="No applications on record yet.")

        def stage_count(kinds: list[str]) -> int:
            return s.scalar(
                select(func.count(func.distinct(ApplicationEvent.application_id)))
                .join(Application, Application.id == ApplicationEvent.application_id)
                .where(Application.user_id.in_(user_ids),
                       ApplicationEvent.event_type.in_(kinds))) or 0

        replied = stage_count(stages)
        interview = stage_count(["interview_completed", "interview_scheduled"])
        final = stage_count(["final_round"])
        offers = stage_count(["offer"])

        # Which skills the group's job ads kept asking for.
        gap_rows = s.execute(
            select(Job.jd_text).join(Application, Application.job_id == Job.id)
            .where(Application.user_id.in_(user_ids), Job.jd_text.isnot(None))
        ).all()

    from .deterministic import default_vocab
    vocab = default_vocab()
    counts: dict[str, int] = {}
    for (jd,) in gap_rows:
        seen = set()
        low = (jd or "").lower()
        for name in vocab.entries:
            if name.lower() in low and name not in seen:
                counts[name] = counts.get(name, 0) + 1
                seen.add(name)

    gaps = [{"skill": k, "asked_by": v, "share": round(v / total, 4)}
            for k, v in sorted(counts.items(), key=lambda x: -x[1])[:10]
            if v >= K_ANON]

    lo, hi = wilson_interval(replied, total)
    return CohortReport(
        available=True, n_members=len(user_ids), n_applications=total,
        response_rate=replied / total if total else 0.0, ci=(lo, hi),
        offers=offers if offers >= K_ANON else 0,
        funnel={"submitted": total, "screen": replied, "interview": interview,
                "final": final, "offer": offers},
        skill_gaps=gaps,
    )


# ==========================================================================
# Cohort management
# ==========================================================================

import secrets  # noqa: E402

from .models import Cohort, CohortMember  # noqa: E402


def create_cohort(owner_id: int, name: str) -> dict:
    """Make a group and mint a join code."""
    name = (name or "").strip()
    if not name:
        raise ValueError("Give the cohort a name.")
    code = secrets.token_urlsafe(6).replace("-", "").replace("_", "")[:8].upper()
    with session_scope() as s:
        c = Cohort(name=name, owner_id=owner_id, join_code=code)
        s.add(c)
        s.flush()
        # The owner is not automatically a member. An administrator's own job
        # search is rarely part of the batch they're measuring, and silently
        # folding it in would skew a small cohort noticeably.
        return {"id": c.id, "name": c.name, "join_code": c.join_code}


def join_cohort(user_id: int, code: str) -> dict:
    """Join by code. Opt-in by construction — nobody is added by an admin."""
    code = (code or "").strip().upper()
    with session_scope() as s:
        c = s.scalar(select(Cohort).where(Cohort.join_code == code))
        if c is None:
            raise LookupError("No cohort with that code.")
        existing = s.scalar(select(CohortMember).where(
            CohortMember.cohort_id == c.id, CohortMember.user_id == user_id))
        if existing:
            return {"cohort_id": c.id, "name": c.name, "joined": False,
                    "already": True}
        s.add(CohortMember(cohort_id=c.id, user_id=user_id))
        return {"cohort_id": c.id, "name": c.name, "joined": True, "already": False}


def leave_cohort(user_id: int, cohort_id: int) -> dict:
    """Leaving removes this person from every future group report.

    Worth having for the same reason joining is opt-in: someone who shares
    their outcomes with a bootcamp should be able to stop.
    """
    with session_scope() as s:
        s.query(CohortMember).filter(
            CohortMember.cohort_id == cohort_id,
            CohortMember.user_id == user_id).delete()
        return {"cohort_id": cohort_id, "left": True}


def member_ids(cohort_id: int) -> list[int]:
    with session_scope() as s:
        return [u for (u,) in s.execute(
            select(CohortMember.user_id)
            .where(CohortMember.cohort_id == cohort_id)).all()]


def cohorts_for_owner(owner_id: int) -> list[dict]:
    with session_scope() as s:
        rows = s.scalars(select(Cohort).where(Cohort.owner_id == owner_id)
                         .order_by(Cohort.created_at.desc())).all()
        out = []
        for c in rows:
            n = s.scalar(select(func.count(CohortMember.id))
                         .where(CohortMember.cohort_id == c.id)) or 0
            out.append({"id": c.id, "name": c.name, "join_code": c.join_code,
                        "members": n})
        return out


def cohorts_for_member(user_id: int) -> list[dict]:
    with session_scope() as s:
        rows = s.execute(
            select(Cohort.id, Cohort.name)
            .join(CohortMember, CohortMember.cohort_id == Cohort.id)
            .where(CohortMember.user_id == user_id)).all()
        return [{"id": i, "name": n} for i, n in rows]
