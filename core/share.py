"""
The share card — aggregate numbers only, with redaction enforced by test.

WHY THIS IS THE MOST DANGEROUS FEATURE IN THE PRODUCT
-----------------------------------------------------
Everything else here is private by default. This is the one place where a
person's job-search history leaves their account, and job-search history is
about as sensitive as personal data gets: it names their current employer's
competitors, the roles they were rejected from, and the fact that they are
looking at all. Someone's manager seeing "applied to 113 jobs" is a real
consequence of a careless share button.

So the rule is absolute: **a share card contains counts and rates, and nothing
else.** No company names, no job titles, no dates, no rejection text, no
resume content. `audit_share()` proves it rather than assuming it, and the
test suite runs that audit against real report data.

WHY SHARE AT ALL
----------------
The wall of identical rejection boilerplate is the one thing in this product
that makes people feel recognised rather than measured. "87 rejections, 86 of
them said nothing" is a sentence job seekers send each other. That's the
growth loop — not a referral code.

WHAT THIS DELIBERATELY DOESN'T DO
---------------------------------
No auto-posting, no pre-filled tweet intent, no "share to unlock". The card is
generated, shown to the person in full, and copied only when they ask. A
growth loop that surprises someone with what it published is not a growth loop,
it is an incident.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

SHARE_VERSION = "0.1.0"

# Anything matching these must never appear in a share payload.
_FORBIDDEN_KEYS = {
    "company", "job_title", "title", "companies", "applications", "employer",
    "rejection_text", "verbatim_span", "quotes", "resume", "original",
    "suggestion", "jd_text", "email", "label_with", "label_without",
}


@dataclass
class ShareCard:
    headline: str
    stats: list[dict] = field(default_factory=list)
    line: str = ""                 # the sentence people actually paste
    caveat: str = ""

    def as_dict(self) -> dict:
        return {"headline": self.headline, "stats": self.stats,
                "line": self.line, "caveat": self.caveat,
                "version": SHARE_VERSION}


def build_share(summary: dict, verdict: dict, evidence_stats: dict,
                pace: dict | None = None) -> ShareCard:
    """Assemble the card from aggregates only.

    Every value read here is a count or a rate. Nothing that could identify an
    employer, a role, or the person is touched — which is what makes the audit
    below able to pass rather than merely usually pass.
    """
    n = summary.get("n_analyzable", 0) or 0
    rate = summary.get("response_rate", 0.0) or 0.0
    funnel = summary.get("funnel", {}) or {}
    silent = max(0, (funnel.get("submitted", 0) or 0) - (funnel.get("screen", 0) or 0))

    with_text = evidence_stats.get("with_text", 0) or 0
    boilerplate = evidence_stats.get("screened_out", 0) or 0

    stats = [
        {"value": str(n), "label": "applications sent"},
        {"value": f"{rate * 100:.0f}%", "label": "got any reply"},
        {"value": str(silent), "label": "never heard back"},
    ]
    if pace and pace.get("median_reply_days"):
        stats.append({"value": f"{pace['median_reply_days']}d",
                      "label": "typical time to a reply"})

    # The verdict is written in second person for the reader's own report.
    # Shared, "your applications" addresses the wrong person entirely.
    headline = (verdict.get("headline") or "")
    headline = (headline.replace("Your ", "My ").replace("your ", "my ")
                        .replace("You ", "I ").replace("you ", "I "))
    headline = headline or "Here is where my applications actually go."

    # The line is written to be recognisable rather than impressive. Nobody
    # forwards a humblebrag about their own funnel; they forward the bit that
    # describes the thing everyone quietly experiences.
    if with_text and boilerplate:
        line = (f"I sent {n} job applications. {silent} never got a reply. "
                f"Of the {with_text} rejections that did arrive, {boilerplate} "
                f"said nothing at all.")
    elif silent:
        line = (f"I sent {n} job applications and {silent} of them never got a "
                f"reply. Not a rejection — nothing.")
    else:
        line = f"I sent {n} job applications. Here's what the numbers actually say."

    return ShareCard(
        headline=headline,
        stats=stats,
        line=line,
        caveat="Numbers only — no company names, job titles or dates are included.",
    )


# --------------------------------------------------------------------------
# The guarantee
# --------------------------------------------------------------------------

# Loose patterns for things that should never survive redaction.
_LEAK_PATTERNS = [
    (re.compile(r"\b\d{4}-\d{2}-\d{2}\b"), "an ISO date"),
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+"), "an email address"),
    (re.compile(r"\bhttps?://"), "a URL"),
]


def audit_share(card: dict, forbidden_strings: list[str]) -> list[str]:
    """Prove the card carries nothing identifying. Empty list means safe.

    `forbidden_strings` is every company name, job title and rejection quote
    from the source report. We check the rendered card against the actual data
    it came from, rather than against a guess at what identifying data looks
    like — a company called "Sable Retail" would sail past any generic pattern.
    """
    problems: list[str] = []

    blob = " ".join(
        [str(card.get("headline", "")), str(card.get("line", "")),
         str(card.get("caveat", ""))]
        + [f"{s.get('value','')} {s.get('label','')}" for s in card.get("stats", [])]
    )
    low = blob.lower()

    for needle in forbidden_strings:
        needle = (needle or "").strip()
        # Short tokens produce false positives ("PM" appears inside "impact").
        if len(needle) < 5:
            continue
        if needle.lower() in low:
            problems.append(f"leaks source text: {needle[:48]!r}")

    for pattern, what in _LEAK_PATTERNS:
        if pattern.search(blob):
            problems.append(f"contains {what}")

    for key in card:
        if key.lower() in _FORBIDDEN_KEYS:
            problems.append(f"forbidden key present: {key}")

    return problems


def forbidden_from_report(payload: dict) -> list[str]:
    """Collect everything in a report that must not reach a share card."""
    out: list[str] = []
    for a in payload.get("applications", []) or []:
        out.append(a.get("company", ""))
        out.append(a.get("title", ""))
    for o in payload.get("observed", []) or []:
        out.extend(o.get("quotes", []) or [])
    for l in ((payload.get("resume_lines") or {}).get("lines") or []):
        out.append(l.get("original", ""))
    return [x for x in out if x]
