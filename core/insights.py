"""
Stage-split analysis, pace arithmetic, and the facts worth telling someone.

WHY THE FUNNEL IS SPLIT
-----------------------
"Why am I not getting replies" and "why am I not getting the job" are different
questions with different evidence behind them, and lumping them together lets a
top-of-funnel finding masquerade as an interview problem. They also have wildly
different sample sizes: someone with 112 applications may have 27 that reached
a human, so the second question is usually still unanswerable while the first
is well supported. The split makes that visible instead of hiding it.

WHY THE TIMELINE IS A FLOOR
---------------------------
"You'll have a job in 3 months" is the single most requested number in this
category and the one thing the data cannot support. With zero offers observed,
any month figure is invented — and it is the claim a user would most rely on
and most resent being wrong.

What the data DOES support is arithmetic on their own measured rates: how fast
replies arrive, how many applications they send a week, and — given an offer
rate whose upper bound we can compute — how many applications one offer would
take AT LEAST. A floor is honest. A forecast is not.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from .stats import wilson_interval

INSIGHTS_VERSION = "0.1.0"

MIN_STAGE_ROWS = 12          # below this a stage can't be analysed on its own


# --------------------------------------------------------------------------
# Output 2 — the funnel, split
# --------------------------------------------------------------------------

@dataclass
class StageReport:
    key: str                  # "response" | "offer"
    title: str
    question: str
    n: int                    # how many applications entered this stage
    converted: int
    rate: float
    ci: tuple[float, float]
    analysable: bool
    blocked_reason: str = ""
    findings: list = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "key": self.key, "title": self.title, "question": self.question,
            "n": self.n, "converted": self.converted, "rate": round(self.rate, 4),
            "ci": [round(x, 4) for x in self.ci],
            "analysable": self.analysable, "blocked_reason": self.blocked_reason,
        }


def split_funnel(apps) -> list[StageReport]:
    """Two separate questions, each answered only where the data allows.

    Stage one uses every resolved application. Stage two uses only those that
    got a reply — a much smaller pool, and usually too small to say anything,
    which is exactly what it should then say.
    """
    from .deterministic import is_analyzable

    resolved = [a for a in apps if is_analyzable(a.outcome)]
    replied = [a for a in resolved if a.responded]
    offers = [a for a in resolved if a.outcome.furthest_stage.value == "offer"]

    n1, c1 = len(resolved), len(replied)
    rate1 = c1 / n1 if n1 else 0.0
    stage1 = StageReport(
        key="response",
        title="Getting a reply",
        question="Why aren't employers responding?",
        n=n1, converted=c1, rate=rate1, ci=wilson_interval(c1, n1),
        analysable=n1 >= MIN_STAGE_ROWS,
        blocked_reason="" if n1 >= MIN_STAGE_ROWS else
        f"Only {n1} finished applications. We need about {MIN_STAGE_ROWS} to say anything.",
    )

    n2, c2 = len(replied), len(offers)
    rate2 = c2 / n2 if n2 else 0.0
    stage2 = StageReport(
        key="offer",
        title="Converting to an offer",
        question="Why aren't the conversations turning into a job?",
        n=n2, converted=c2, rate=rate2, ci=wilson_interval(c2, n2),
        analysable=n2 >= MIN_STAGE_ROWS,
        blocked_reason="" if n2 >= MIN_STAGE_ROWS else
        f"Only {n2} of your applications reached a human. That's not enough to "
        f"study this half of the funnel yet — fix the reply rate first and this "
        f"question answers itself.",
    )
    return [stage1, stage2]


# --------------------------------------------------------------------------
# Output 4 — pace, as a floor
# --------------------------------------------------------------------------

@dataclass
class Pace:
    median_reply_days: int | None
    fastest_reply_days: int | None
    per_week: float
    weeks_active: float
    offers: int
    offer_rate_ceiling: float          # upper bound of the interval
    applications_for_one_offer: int | None
    months_floor: float | None
    headline: str
    caveat: str

    def as_dict(self) -> dict:
        return {
            "median_reply_days": self.median_reply_days,
            "fastest_reply_days": self.fastest_reply_days,
            "per_week": round(self.per_week, 1),
            "weeks_active": round(self.weeks_active, 1),
            "offers": self.offers,
            "offer_rate_ceiling": round(self.offer_rate_ceiling, 4),
            "applications_for_one_offer": self.applications_for_one_offer,
            "months_floor": round(self.months_floor, 1) if self.months_floor else None,
            "headline": self.headline, "caveat": self.caveat,
        }


def pace_estimate(apps, applied_dates: list[date]) -> Pace:
    """Reply speed, application pace, and a lower bound on time to an offer.

    Every number here is measured from the user's own history. The one derived
    figure — applications needed for an offer — uses the UPPER bound of the
    offer-rate interval, which makes the resulting timeline a minimum. Using
    the point estimate would produce a number that reads like a promise.
    """
    replies = sorted(a.outcome.days_to_first_response for a in apps
                     if a.outcome.days_to_first_response is not None)
    median = replies[len(replies) // 2] if replies else None
    fastest = replies[0] if replies else None
    if fastest == median:
        fastest = None      # identical values read as a bug, not a fact

    dates = sorted(d for d in applied_dates if d)
    weeks = max(1.0, ((dates[-1] - dates[0]).days / 7) if len(dates) > 1 else 1.0)
    per_week = len(dates) / weeks if dates else 0.0

    offers = sum(1 for a in apps if a.outcome.furthest_stage.value == "offer")
    _, ceiling = wilson_interval(offers, len(apps)) if apps else (0.0, 1.0)

    needed = int(round(1 / ceiling)) if ceiling > 0 else None
    months = (needed / per_week / 4.345) if (needed and per_week > 0) else None

    if median is None:
        head = "No replies recorded yet, so there's nothing to time."
        cav = "Add the dates you heard back and we can measure your reply speed."
    elif offers == 0:
        head = (f"Employers who reply take about {median} days. You send roughly "
                f"{per_week:.0f} applications a week.")
        cav = (f"You've had no offers in {len(apps)} applications, so we can't put a "
               f"date on one. What we can say: your offer rate is under "
               f"{ceiling*100:.1f}%, which means one offer is at least ~{needed} more "
               f"applications away"
               + (f" — about {months:.0f} month{'s' if round(months) != 1 else ''} "
                  f"at your current pace." if months else ".")
               + " That's a floor, not a forecast.")
    else:
        head = (f"Employers who reply take about {median} days. You send roughly "
                f"{per_week:.0f} applications a week.")
        cav = (f"You've had {offers} offer{'s' if offers != 1 else ''} in {len(apps)} "
               f"applications. At that rate another one is around {needed} applications "
               f"away"
               + (f", or about {months:.0f} month{'s' if round(months) != 1 else ''} "
                  f"at your current pace." if months else ".")
               + " Pace is the part you control.")

    return Pace(median, fastest, per_week, weeks, offers, ceiling, needed, months,
                head, cav)


# --------------------------------------------------------------------------
# Learning estimates
# --------------------------------------------------------------------------

# Typical time to the level a job ad means when it lists the skill. These are
# population ranges from common curricula, NOT a prediction about this person —
# the wording downstream must keep that distinction.
LEARNING_WEEKS: dict[str, tuple[int, int, str]] = {
    "SQL": (2, 4, "joins, aggregation and window functions"),
    "Python": (6, 12, "enough to write real scripts, not just syntax"),
    "Excel": (1, 3, "pivot tables, lookups and basic modelling"),
    "Tableau": (2, 4, "building and publishing a dashboard"),
    "Looker": (2, 4, "LookML basics and exploring a model"),
    "Amplitude": (1, 2, "funnels, cohorts and retention charts"),
    "Mixpanel": (1, 2, "event tracking and funnel reports"),
    "Docker": (2, 4, "images, volumes and compose"),
    "Kubernetes": (6, 12, "deployments, services and debugging a pod"),
    "AWS": (6, 10, "the handful of services a job actually means"),
    "Machine Learning": (12, 24, "training and evaluating a model end to end"),
    "A/B Testing": (2, 4, "designing a test and reading the result honestly"),
    "Figma": (1, 3, "wireframes and prototypes, not visual design"),
    "Jira": (1, 1, "an afternoon, genuinely"),
    "Agile": (1, 2, "ceremonies and the vocabulary"),
    "Git": (1, 2, "branching, rebasing and undoing mistakes"),
    "CI/CD": (2, 5, "a working pipeline you built yourself"),
    "API Design": (3, 6, "REST conventions, versioning and auth"),
    "React": (6, 12, "components, state and data fetching"),
    "TypeScript": (3, 6, "types that help rather than fight you"),
    "Data Analysis": (4, 8, "framing a question and defending the answer"),
    "Roadmapping": (2, 4, "sequencing and saying no in writing"),
    "Stakeholder Management": (4, 8, "mostly practice, not study"),
}

DEFAULT_LEARNING = (4, 8, "enough to talk about it credibly in an interview")


@dataclass
class SkillPlan:
    skill: str
    required_by: int
    share: float
    weeks_low: int
    weeks_high: int
    milestone: str
    unlocks: int              # applications that would have matched

    def as_dict(self) -> dict:
        return {
            "skill": self.skill, "required_by": self.required_by,
            "share": round(self.share, 4),
            "weeks_low": self.weeks_low, "weeks_high": self.weeks_high,
            "milestone": self.milestone, "unlocks": self.unlocks,
        }


def learning_plan(roadmap_gaps, total_roles: int, limit: int = 4) -> list[SkillPlan]:
    """Rank missing skills by how many roles they'd unlock, with typical timings.

    The weeks are a population range, not a promise about this reader. The
    ranking is theirs though — it's a count over the jobs they applied to.
    """
    out: list[SkillPlan] = []
    for g in (roadmap_gaps or [])[:limit]:
        skill = g.get("skill") if isinstance(g, dict) else getattr(g, "skill", "")
        n = g.get("required_by") if isinstance(g, dict) else getattr(g, "required_by", 0)
        lo, hi, milestone = LEARNING_WEEKS.get(skill, DEFAULT_LEARNING)
        out.append(SkillPlan(
            skill=skill, required_by=n,
            share=(n / total_roles) if total_roles else 0.0,
            weeks_low=lo, weeks_high=hi, milestone=milestone, unlocks=n))
    return out


# --------------------------------------------------------------------------
# Facts worth reading
# --------------------------------------------------------------------------

def interesting_facts(apps, profile, applied_dates: list[date],
                      resume_text: str) -> list[dict]:
    """Small true things about this person's own data.

    Facts, not findings — each is a count or a max, nothing inferential. They
    exist because a report full of rates is hard to care about, and "you applied
    to 9 roles in one day" is the sort of thing that makes someone recognise
    their own behaviour.
    """
    facts: list[dict] = []
    if not apps:
        return facts

    # Busiest single day
    from collections import Counter
    per_day = Counter(applied_dates)
    if per_day:
        day, count = per_day.most_common(1)[0]
        if count >= 3:
            facts.append({
                "icon": "burst",
                "label": "Your busiest day",
                "value": f"{count} applications",
                "detail": f"All sent on {day.strftime('%d %b')}. Batches like this "
                          f"usually mean less tailoring per application.",
            })

    # Best and worst matched application
    best = max(apps, key=lambda a: a.fit.overall)
    worst = min(apps, key=lambda a: a.fit.overall)
    facts.append({
        "icon": "target",
        "label": "Your best-matched application",
        "value": f"{best.fit.overall:.0f}/100",
        "detail": f"{best.job_title} at {best.company}"
                  + (" — and they replied." if best.responded
                     else " — and you still heard nothing."),
    })
    facts.append({
        "icon": "low",
        "label": "Your weakest application",
        "value": f"{worst.fit.overall:.0f}/100",
        "detail": f"{worst.job_title} at {worst.company}. Worth asking whether "
                  f"applications like this are worth the time.",
    })

    # Spread of companies
    companies = {a.company for a in apps}
    facts.append({
        "icon": "spread",
        "label": "Companies applied to",
        "value": f"{len(companies)}",
        "detail": f"across {len(apps)} applications"
                  + (f" — you applied to some more than once."
                     if len(companies) < len(apps) else "."),
    })

    # Resume shape
    words = len(resume_text.split())
    if words:
        facts.append({
            "icon": "doc",
            "label": "Your resume",
            "value": f"{words} words",
            "detail": (f"{len(profile.skills_in_bullets)} of your "
                       f"{len(profile.skills)} skills appear inside an achievement "
                       f"rather than just a list."),
        })

    # Longest silence
    ghosted = [a for a in apps if a.outcome.resolution.value == "ghosted"]
    if ghosted:
        facts.append({
            "icon": "silence",
            "label": "Never heard back",
            "value": f"{len(ghosted)}",
            "detail": f"{len(ghosted)} of your {len(apps)} applications got no reply "
                      f"at all. That's normal, and it's still the biggest number here.",
        })

    return facts
