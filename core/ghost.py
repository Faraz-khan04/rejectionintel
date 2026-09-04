"""
Ghost signals — which employers never reply, measured rather than guessed.

WHY THIS BELONGS HERE AND NOT IN A COMPETITOR
---------------------------------------------
Every ghost-job detector on the market reads the LISTING: how old it is, whether
the salary band is missing, whether the description is vague. All inference from
text, all available to anyone.

This product has something none of them do — the OUTCOME. We know which
companies actually replied to this person, and eventually to everyone. A company
that received four applications and answered none of them isn't a guess about a
job ad; it's a fact about that employer's behaviour.

That distinction matters for the same reason the rest of the product is built
this way. "This listing looks vague" is an opinion. "You applied here three
times and never heard back" is a count.

WHY IT MATTERS MORE THAN THE ACCURACY SUGGESTS
----------------------------------------------
Research on job-search forums keeps landing on the same finding: people are not
damaged by rejection, they're damaged by silence — by not knowing whether the
problem was them. Between a fifth and a quarter of listings have no hiring
process behind them at all, which means a meaningful share of that silence was
never about the applicant.

Telling someone "eleven of your applications went to companies that answer
nobody" does two jobs at once: it saves them time, and it moves a piece of
self-blame onto something external and true.

WHAT THIS DELIBERATELY DOES NOT DO
----------------------------------
It does not call a company fraudulent, and it does not predict whether a
specific listing is real. A company can have a genuine hiring freeze, a broken
applicant tracker, or one bad quarter. We report what happened and let the
reader decide.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

GHOST_VERSION = "0.1.0"

# A single silent application is normal — most applications are silent. Below
# this we have no signal, only noise, and saying so is the honest answer.
MIN_APPLICATIONS = 2

# Public research puts ghost listings somewhere between a fifth and a quarter of
# all postings. Used only for context in the copy, never in a calculation.
PUBLIC_GHOST_RANGE = "18–27%"


@dataclass
class CompanySignal:
    company: str
    applications: int
    replies: int
    silent: int
    first_applied: str
    last_applied: str
    days_span: int
    level: str          # "silent" | "mixed" | "responsive"
    note: str

    def as_dict(self) -> dict:
        return {
            "company": self.company, "applications": self.applications,
            "replies": self.replies, "silent": self.silent,
            "first_applied": self.first_applied, "last_applied": self.last_applied,
            "days_span": self.days_span, "level": self.level, "note": self.note,
        }


@dataclass
class GhostReport:
    silent_companies: list[dict] = field(default_factory=list)
    responsive_companies: list[dict] = field(default_factory=list)
    wasted_applications: int = 0
    wasted_share: float = 0.0
    headline: str = ""
    reframe: str = ""
    caveat: str = ""

    def as_dict(self) -> dict:
        return {
            "silent_companies": self.silent_companies,
            "responsive_companies": self.responsive_companies,
            "wasted_applications": self.wasted_applications,
            "wasted_share": round(self.wasted_share, 4),
            "headline": self.headline, "reframe": self.reframe,
            "caveat": self.caveat, "version": GHOST_VERSION,
        }


def analyse(apps, today: date | None = None) -> GhostReport:
    """Group applications by employer and report who never answered.

    Only companies applied to more than once are considered. One silent
    application says nothing — most applications are silent, and flagging a
    company on a single non-reply would be exactly the kind of overclaiming
    the rest of this product refuses.
    """
    today = today or date.today()
    by_company: dict[str, list] = {}
    for a in apps:
        by_company.setdefault(a.company, []).append(a)

    silent: list[CompanySignal] = []
    responsive: list[CompanySignal] = []

    for company, group in by_company.items():
        if len(group) < MIN_APPLICATIONS:
            continue

        replies = sum(1 for a in group if a.responded)

        # The analysed application carries an outcome, not an applied date, so
        # the span is derived from whatever dates are available and simply
        # omitted when there are none. A missing span costs one clause of copy;
        # assuming a field exists costs the whole feature.
        dates = sorted(
            d for d in (getattr(a.outcome, "resolved_at", None) for a in group)
            if d is not None)
        span = (dates[-1] - dates[0]).days if len(dates) > 1 else 0

        sig = CompanySignal(
            company=company,
            applications=len(group),
            replies=replies,
            silent=len(group) - replies,
            first_applied=dates[0].isoformat() if dates else "",
            last_applied=dates[-1].isoformat() if dates else "",
            days_span=span,
            level="silent" if replies == 0 else (
                "responsive" if replies == len(group) else "mixed"),
            note="",
        )

        if replies == 0:
            sig.note = (
                f"{len(group)} applications over {span} days, no reply to any."
                if span > 0 else
                f"{len(group)} applications, no reply to any of them."
            )
            silent.append(sig)
        elif replies == len(group):
            sig.note = f"Replied to all {len(group)}."
            responsive.append(sig)

    silent.sort(key=lambda s: (-s.applications, s.company))
    responsive.sort(key=lambda s: (-s.applications, s.company))

    wasted = sum(s.applications for s in silent)
    total = len(apps)
    share = wasted / total if total else 0.0

    if not silent:
        headline = "No employer here ignored you more than once."
        reframe = ("Every company you applied to more than once replied at least "
                   "once. Whatever is happening, it isn't a wall of silent employers.")
    elif len(silent) == 1:
        s = silent[0]
        headline = f"One employer took {s.applications} applications and answered none."
        reframe = (f"{s.company} received {s.applications} applications from you and "
                   f"never replied to any. That's worth knowing before you send a third.")
    else:
        headline = (f"{len(silent)} employers took {wasted} applications between them "
                    f"and answered none.")
        reframe = (f"That's {share*100:.0f}% of everything you sent, going to companies "
                   f"that have never once replied to you. Not a rejection — no process "
                   f"at all. Public research puts {PUBLIC_GHOST_RANGE} of listings in "
                   f"that category, so this is a known problem and not a you problem.")

    caveat = ("This is what happened, not a verdict on the companies. A hiring "
              "freeze, a broken applicant tracker or one bad quarter all look "
              "identical from the outside. It's a reason to spend your time "
              "elsewhere, not an accusation.")

    return GhostReport(
        silent_companies=[s.as_dict() for s in silent[:8]],
        responsive_companies=[s.as_dict() for s in responsive[:5]],
        wasted_applications=wasted, wasted_share=share,
        headline=headline, reframe=reframe, caveat=caveat,
    )


# --------------------------------------------------------------------------
# Listing-level check, for the pre-apply path
# --------------------------------------------------------------------------

# Signals drawn from published ghost-job research. Each is weak alone; the point
# is to count them, show which fired, and let the reader judge.
LISTING_CHECKS = [
    ("no_salary", "No salary range",
     "Legitimate postings increasingly include one, and several jurisdictions "
     "require it."),
    ("vague", "Vague responsibilities",
     "Real postings are written by someone who needs specific problems solved."),
    ("no_team", "No named team, manager or reporting line",
     "A funded role usually knows who it reports to."),
    ("evergreen", "Reads like an always-open pipeline role",
     "Phrases like \"always looking\" or \"future opportunities\" describe a "
     "resume collection, not a vacancy."),
    ("wide_range", "Requirements span several different jobs",
     "A wish list for three people often means the role isn't defined yet."),
]

_VAGUE_HINTS = ("fast-paced", "wear many hats", "rockstar", "ninja", "self-starter",
                "dynamic environment", "other duties as assigned")
_EVERGREEN = ("always looking", "future opportunities", "talent pool", "pipeline",
              "general application", "we are always", "ongoing recruitment")


def check_listing(jd_text: str) -> dict:
    """Count ghost signals in one job description.

    Deliberately not a score out of 100. Three signals firing is information;
    turning that into "68% likely fake" would be inventing a precision the
    inputs cannot support.
    """
    text = (jd_text or "").lower()
    if len(text) < 80:
        return {"checked": False,
                "note": "Too little text to check. Paste the full posting."}

    fired = []

    has_salary = any(t in text for t in ("salary", "compensation", "₹", "$", "£", "€",
                                         "lpa", "per annum", "ctc"))
    if not has_salary:
        fired.append("no_salary")

    if sum(1 for h in _VAGUE_HINTS if h in text) >= 2:
        fired.append("vague")

    if not any(t in text for t in ("report to", "reporting to", "you'll join",
                                   "you will join", "the team", "our team")):
        fired.append("no_team")

    if any(t in text for t in _EVERGREEN):
        fired.append("evergreen")

    # A posting demanding many senior-sounding areas at once often isn't scoped.
    areas = sum(1 for t in ("engineering", "marketing", "sales", "design",
                            "operations", "finance", "product", "support")
                if t in text)
    if areas >= 5:
        fired.append("wide_range")

    detail = [{"key": k, "label": l, "why": w}
              for k, l, w in LISTING_CHECKS if k in fired]

    if not fired:
        verdict = "Nothing unusual. This reads like a real, scoped role."
    elif len(fired) == 1:
        verdict = "One thing worth noticing, but plenty of real postings look like this."
    elif len(fired) == 2:
        verdict = "A couple of signals. Worth checking the company's own careers page."
    else:
        verdict = ("Several signals at once. Check the company careers page before "
                   "spending an hour tailoring anything.")

    return {
        "checked": True,
        "signals_found": len(fired),
        "signals_total": len(LISTING_CHECKS),
        "detail": detail,
        "verdict": verdict,
        "caveat": ("These are patterns from published research, not proof. A real "
                   "posting can trip all of them and a ghost posting can trip none. "
                   "Treat it as a reason to check, not a reason to skip."),
    }


# --------------------------------------------------------------------------
# Standalone check — the front door
# --------------------------------------------------------------------------

def check_with_history(jd_text: str, company: str = "",
                       history: list | None = None) -> dict:
    """Listing signals, plus what this person's own history says about the employer.

    THE POINT OF SPLITTING THIS OUT
    -------------------------------
    The listing half is weak by construction — five text heuristics that any
    competitor could reproduce, and which are wrong often enough that they must
    be presented as "worth checking" rather than "this is fake".

    The history half is the part nobody else can compute, and it only exists
    once someone has applications on record. So the standalone tool is honest
    about being the weaker half, and says plainly what the stronger one needs.
    That is a real limitation stated plainly, not a growth tactic dressed up as
    one — the tool is genuinely less useful without outcomes behind it.
    """
    listing = check_listing(jd_text)

    prior = None
    if company and history:
        norm = company.strip().lower()
        mine = [a for a in history if (a.company or "").strip().lower() == norm]
        if mine:
            replies = sum(1 for a in mine if a.responded)
            prior = {
                "company": company,
                "applications": len(mine),
                "replies": replies,
                "verdict": (
                    f"You've applied to {company} {len(mine)} time"
                    f"{'s' if len(mine) > 1 else ''} and heard back "
                    f"{replies} time{'s' if replies != 1 else ''}."
                ),
                "weight": ("strong" if len(mine) >= 3 and replies == 0
                           else "weak" if len(mine) >= 2 else "none"),
            }

    return {
        "listing": listing,
        "prior": prior,
        "upgrade_note": (
            "This reads the advert only. The signal that actually predicts "
            "silence is which employers have ignored you before — and that "
            "needs your application history, not one job ad."
        ) if prior is None else "",
    }
