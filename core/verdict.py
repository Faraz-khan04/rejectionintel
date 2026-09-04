"""
The verdict and the action plan — the two things a person actually needs.

WHY THIS MODULE EXISTS
----------------------
The report was eight cards of percentages. Accurate, and nobody could tell you
what to do differently tomorrow after reading it. A person arriving at this
product has one question: *where am I losing, and what do I change?*

So: one sentence, one number, and two or three things to tick off. Everything
else in the report stays — it moves below, for the people who want it.

WHAT MAKES THIS DIFFERENT FROM A SCORE OUT OF 100
-------------------------------------------------
Every competitor turns a job search into a grade. A grade is a prediction
wearing a number, and we can't make predictions. The verdict here is a
*measurement* of where applications stop — a fact about the funnel, not a
forecast about the person. It reads as decisive because it is, not because
we rounded uncertainty away.
"""

from __future__ import annotations

from dataclasses import dataclass, field

VERDICT_VERSION = "0.1.0"

STAGE_ORDER = ["submitted", "screen", "interview", "final", "offer"]

STAGE_LABEL = {
    "submitted": "applying",
    "screen": "getting a first reply",
    "interview": "the interview",
    "final": "the final round",
    "offer": "the offer",
}


@dataclass
class Verdict:
    headline: str            # one sentence, the thing they came for
    stat: str                # one number, large
    stat_label: str
    detail: str
    stage: str               # where the funnel breaks
    severity: str            # "critical" | "notable" | "healthy" | "unknown"

    def as_dict(self) -> dict:
        return {
            "headline": self.headline, "stat": self.stat,
            "stat_label": self.stat_label, "detail": self.detail,
            "stage": self.stage, "severity": self.severity,
            "version": VERDICT_VERSION,
        }


def find_bottleneck(funnel: dict, n_analysed: int) -> Verdict:
    """Name the single stage where the most applications stop.

    This is arithmetic over the funnel, not a model output. "84% never reach a
    human" is a count, and counts don't need hedging — which is why the verdict
    can be blunt without overclaiming.
    """
    total = funnel.get("submitted", 0) or 0
    if total < 5:
        return Verdict(
            "Too early to tell where you're losing people.",
            str(total), "applications so far",
            "Add more and we'll tell you exactly where they stop.",
            "submitted", "unknown")

    # A stage is only a bottleneck if it converts BADLY. Ranking by absolute
    # drop alone always accuses the top of the funnel, because that's where the
    # denominator is — a candidate with a 50% response rate would be told "most
    # of your applications never reach a human", which is both wrong and the
    # opposite of useful.
    #
    # So: discard stages already converting acceptably, then among what's left
    # name the one losing the most people.
    HEALTHY = {"screen": 0.30, "interview": 0.50, "final": 0.40, "offer": 0.30}

    candidates = []
    for i in range(len(STAGE_ORDER) - 1):
        a, b = STAGE_ORDER[i], STAGE_ORDER[i + 1]
        before, after = funnel.get(a, 0) or 0, funnel.get(b, 0) or 0
        if before < 3:
            continue                       # too few to judge this stage at all
        conversion = after / before
        if conversion >= HEALTHY.get(b, 0.3):
            continue                       # this stage is fine; not the story
        candidates.append((before - after, b, before, conversion))

    if not candidates:
        best = funnel.get("offer", 0) or 0
        return Verdict(
            "Nothing here is obviously broken.",
            f"{best}", "offers so far",
            f"Across {total} applications, no single stage stands out as the place "
            f"you're losing people. That's unusual, and it's good news.",
            "offer", "healthy")

    biggest_drop, biggest_stage, biggest_from, _conv = max(candidates)

    reached = funnel.get(biggest_stage, 0) or 0

    # The headline number is the share of people who REACHED this stage and
    # went no further — not a share of everyone who ever applied.
    #
    # Those are different numbers, and using the second one broke the card:
    # it showed "31%" above a sentence reading "43 replied, only 14 moved on".
    # 31 is 29/96; the reader is looking at 43 and 14 and cannot get there.
    # A headline figure you can't reconcile with the sentence under it reads
    # as a mistake even when it's correct.
    lost_pct = 100.0 * biggest_drop / biggest_from if biggest_from else 0

    if biggest_stage == "screen":
        return Verdict(
            "Most of your applications never reach a human.",
            f"{lost_pct:.0f}%", f"of your {total} applications got no reply",
            f"{biggest_drop} of your {total} applications got no response at all. "
            f"Fix this first — everything else only happens after someone replies.",
            "screen", "critical" if lost_pct >= 70 else "notable")

    if biggest_stage == "interview":
        return Verdict(
            "You get replies. The first conversation is where it ends.",
            f"{lost_pct:.0f}%", f"of the {biggest_from} who replied went no further",
            f"{biggest_from} employers got back to you, and {reached} of them took "
            f"you to an interview. Your applications are doing their job — the "
            f"first call isn't.",
            "interview", "critical" if lost_pct >= 60 else "notable")

    if biggest_stage == "final":
        return Verdict(
            "You interview well. It stops just before the end.",
            f"{lost_pct:.0f}%", f"of your {biggest_from} interviews ended there",
            f"You had {biggest_from} interviews and {reached} of them became final "
            f"rounds. This is a late-stage problem, which is a much better problem "
            f"to have than the alternative.",
            "final", "notable")

    return Verdict(
        "You reach the last round and don't get the job.",
        f"{funnel.get('offer', 0)} of {biggest_from}",
        "final rounds became an offer",
        f"You reached {biggest_from} final rounds and got "
        f"{funnel.get('offer', 0)} offer{'s' if funnel.get('offer', 0) != 1 else ''}. "
        f"At this stage it is rarely the resume — it is what happens in the room.",
        "offer", "notable")


# --------------------------------------------------------------------------
# The action plan
# --------------------------------------------------------------------------

@dataclass
class Action:
    key: str                 # stable id, so ticking survives a re-analysis
    title: str
    why: str
    source: str              # observed | pattern | resume | targeting
    effort: str              # "10 minutes" | "an hour" | "ongoing"
    done: bool = False

    def as_dict(self) -> dict:
        return {"key": self.key, "title": self.title, "why": self.why,
                "source": self.source, "effort": self.effort, "done": self.done}


def build_actions(observed, findings, roadmap_gaps, resume_lines,
                  limit: int = 3) -> list[Action]:
    """Two or three things to actually do, strongest evidence first.

    Capped deliberately. A list of ten actions is a list of zero actions — the
    whole point is that someone finishes this today. Ranked the same way the
    tiers are: what an employer wrote beats what statistics found, which beats
    a count over job ads.
    """
    out: list[Action] = []

    # 1. An employer said it in writing. Nothing outranks that.
    for o in (observed or [])[:1]:
        skill = getattr(o, "skill", None) or (o.get("skill") if isinstance(o, dict) else None)
        count = getattr(o, "employer_count", None) or (
            o.get("employer_count") if isinstance(o, dict) else 1)
        if skill:
            out.append(Action(
                key=f"observed:{skill}",
                title=f"Add a line about {skill} to your resume",
                why=f"{count} employer{'s' if count > 1 else ''} named it in writing "
                    f"when they turned you down. This isn't our inference — it's theirs.",
                source="observed", effort="10 minutes"))

    # 2. A pattern that survived correction.
    for f in (findings or []):
        tier = getattr(f, "confidence_tier", None)
        tier = tier.value if hasattr(tier, "value") else (
            f.get("tier") if isinstance(f, dict) else None)
        if tier != "strong":
            continue
        dim = getattr(f, "dimension", None) or f.get("dimension", "")
        rate_w = getattr(f, "rate_exposed", None)
        rate_o = getattr(f, "rate_unexposed", None)
        if rate_w is None:
            rate_w, rate_o = f.get("rate_exposed", 0), f.get("rate_unexposed", 0)
        if not str(dim).startswith("fit_score"):
            continue
        out.append(Action(
            key="targeting:fit",
            title="Stop applying to roles you don't match",
            why=f"You heard back from {rate_w*100:.0f}% of the roles you matched well "
                f"and {rate_o*100:.0f}% of the ones you didn't. Same you, same resume.",
            source="pattern", effort="ongoing"))
        break

    # 3. The most-demanded skill missing from the resume.
    for g in (roadmap_gaps or [])[:1]:
        skill = g.get("skill") if isinstance(g, dict) else getattr(g, "skill", None)
        n = g.get("required_by") if isinstance(g, dict) else getattr(g, "required_by", 0)
        if skill and not any(a.key == f"observed:{skill}" for a in out):
            out.append(Action(
                key=f"skill:{skill}",
                title=f"Put {skill} on your resume, or stop applying to roles that want it",
                why=f"{n} of the jobs you applied to listed it as required. Right now "
                    f"you're competing for those without showing it.",
                source="resume", effort="an hour"))

    # 4. Fallback: the weakest resume line. Always something to do.
    if len(out) < 2:
        for l in (resume_lines or [])[:1]:
            orig = l.get("original") if isinstance(l, dict) else getattr(l, "line", "")
            out.append(Action(
                key="resume:weakest",
                title="Rewrite your weakest resume bullet",
                why=f'"{orig[:70]}…" says what you did but not what changed because '
                    f'of it.',
                source="resume", effort="10 minutes"))

    return out[:limit]


# --------------------------------------------------------------------------
# Progress between runs
# --------------------------------------------------------------------------

@dataclass
class Progress:
    has_history: bool = False
    new_applications: int = 0
    previous_rate: float = 0.0
    current_rate: float = 0.0
    days_between: int = 0
    actions_done: list[str] = field(default_factory=list)
    headline: str = ""
    caveat: str = ""
    direction: str = "flat"      # up | down | flat

    def as_dict(self) -> dict:
        return {
            "has_history": self.has_history,
            "new_applications": self.new_applications,
            "previous_rate": round(self.previous_rate, 4),
            "current_rate": round(self.current_rate, 4),
            "days_between": self.days_between,
            "actions_done": self.actions_done,
            "headline": self.headline, "caveat": self.caveat,
            "direction": self.direction,
        }


def compare(previous: dict | None, current_rate: float, current_n: int,
            actions_done: list[str], days_between: int = 0) -> Progress:
    """Movement since the last analysis.

    THE HONESTY PROBLEM HERE
    ------------------------
    A before-and-after comparison has no control group. If someone changed
    their resume and the rate went up, we cannot say the change caused it —
    the market moved, they applied to different roles, they got lucky. Claiming
    credit would be the single biggest overclaim in the product, and it's the
    one users would most want to believe.

    So: report the movement, name what they changed, and say plainly that this
    isn't proof. The caveat is not a disclaimer bolted on — it's part of the
    finding.
    """
    if not previous:
        return Progress(has_history=False)

    prev_rate = previous.get("response_rate", 0.0)
    prev_n = previous.get("n_analysed", 0)
    new_apps = max(0, current_n - prev_n)
    delta = (current_rate - prev_rate) * 100

    if new_apps < 5:
        return Progress(
            has_history=True, new_applications=new_apps,
            previous_rate=prev_rate, current_rate=current_rate,
            days_between=days_between, actions_done=actions_done,
            headline=f"{new_apps} new application{'s' if new_apps != 1 else ''} since last time.",
            caveat="Too few to say whether anything has changed yet. Come back after "
                   "another twenty or so.",
            direction="flat")

    direction = "up" if delta > 1 else ("down" if delta < -1 else "flat")
    changed = len(actions_done)

    if direction == "up":
        head = (f"Your response rate went from {prev_rate*100:.0f}% to "
                f"{current_rate*100:.0f}% across {new_apps} new applications.")
    elif direction == "down":
        head = (f"Your response rate moved from {prev_rate*100:.0f}% to "
                f"{current_rate*100:.0f}% across {new_apps} new applications.")
    else:
        head = (f"Your response rate held steady at about {current_rate*100:.0f}% "
                f"across {new_apps} new applications.")

    caveat = (
        f"You ticked off {changed} change{'s' if changed != 1 else ''} before these. "
        f"We can't prove that's what moved the number — the roles were different too, "
        f"and {new_apps} applications is still a small batch. But it's the direction "
        f"you wanted."
        if changed and direction == "up" else
        f"{new_apps} applications isn't enough to be sure this is a real shift rather "
        f"than normal variation."
    )

    return Progress(
        has_history=True, new_applications=new_apps,
        previous_rate=prev_rate, current_rate=current_rate,
        days_between=days_between, actions_done=actions_done,
        headline=head, caveat=caveat, direction=direction)
