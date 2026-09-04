"""
Resume line analysis — finding weak bullets and suggesting stronger shapes.

THE RULE THAT SHAPES THIS ENTIRE MODULE
---------------------------------------
A suggested bullet must never contain a number the user did not write.

This is the highest-risk feature in the product. Everywhere else, an
overclaim produces a misleading chart. Here it produces a sentence the user
pastes onto a real resume and then has to defend in an interview. "Increased
revenue by 30%" invented by a model is a lie we handed them.

So suggestions are TEMPLATES WITH BLANKS. We supply the shape — strong verb,
scope, a slot for the outcome — and the user supplies the number, because
they are the only one who knows it. `verify_suggestion()` rejects any
suggestion introducing a digit absent from the source line, and it runs on
model output and template output alike.

What we can honestly detect:
  - no measurable outcome in the line
  - a weak opening verb ("responsible for", "helped with")
  - a skill named in the skills list but never evidenced in a bullet
  - a bullet describing duties rather than results
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

DIFF_VERSION = "0.1.0"

# Verbs that describe presence rather than contribution.
WEAK_OPENERS = [
    "responsible for", "helped with", "helped to", "worked on", "worked with",
    "involved in", "participated in", "assisted with", "assisted in",
    "tasked with", "duties included", "part of a team", "contributed to",
    "supported the", "supported", "in charge of", "handled",
]

# Verbs that describe a contribution with an edge to it.
STRONG_OPENERS = [
    "Shipped", "Led", "Built", "Cut", "Grew", "Launched", "Rebuilt",
    "Negotiated", "Automated", "Reduced", "Doubled", "Consolidated",
    "Recovered", "Owned", "Drove", "Delivered",
]

_METRIC = re.compile(
    r"(\d+\s*%|\d+\s*(x|×)\b|[₹$€£]\s*\d|\b\d[\d,\.]*\s*(k|m|bn|crore|lakh|lakhs)\b"
    r"|\b\d[\d,\.]*\s*(users?|customers?|accounts?|people|engineers?|teams?|"
    r"hours?|days?|weeks?|months?|tickets?|reqs?|requests?)\b|\bfrom\s+\d+.*?\bto\s+\d+)",
    re.I,
)
_ANY_DIGIT = re.compile(r"\d")


@dataclass
class LineIssue:
    line: str
    line_no: int
    problems: list[str] = field(default_factory=list)
    weak_opener: str | None = None
    suggestion: str = ""
    why: str = ""

    @property
    def severity(self) -> int:
        return len(self.problems)


def has_metric(line: str) -> bool:
    """Does this bullet contain a measurable outcome?

    >>> has_metric("Cut onboarding time from 9 days to 2")
    True
    >>> has_metric("Responsible for the onboarding experience")
    False
    """
    return bool(_METRIC.search(line or ""))


def weak_opener(line: str) -> str | None:
    """Return the weak phrase this bullet opens with, if any."""
    stripped = re.sub(r"^[\-\*\u2022\u00b7]\s*", "", (line or "").strip()).lower()
    for phrase in WEAK_OPENERS:
        if stripped.startswith(phrase):
            return phrase
    return None


_BULLET_START = re.compile(r"^([\-\*\u2022\u00b7]\s*|\d+[.)]\s+)")


def bullet_lines(resume_text: str) -> list[tuple[int, str]]:
    """Extract bullets, joining lines that wrap.

    THE BUG THIS EXISTS FOR
    -----------------------
    Resumes wrap. A bullet routinely runs across two or three physical lines,
    and the continuation has no bullet marker. Reading line-by-line therefore
    truncates the bullet — and the measurable outcome usually sits at the END
    of a bullet, on the continuation. The result was every line reporting "no
    measurable outcome" on a resume full of percentages.

    A continuation is any non-empty line that follows a bullet, isn't itself a
    bullet, and isn't a section heading or a dated role line.
    """
    lines = resume_text.split("\n")
    out: list[tuple[int, str]] = []
    current_no: int | None = None
    current: list[str] = []

    def flush():
        nonlocal current_no, current
        if current_no is not None and current:
            body = re.sub(r"\s+", " ", " ".join(current)).strip()
            if len(body.split()) >= 4:
                out.append((current_no, body))
        current_no, current = None, []

    for i, raw in enumerate(lines, start=1):
        line = raw.strip()
        if not line:
            flush()
            continue

        if _BULLET_START.match(line):
            flush()
            current_no = i
            current = [_BULLET_START.sub("", line).strip()]
            continue

        # A heading or a role/date line ends the previous bullet.
        is_heading = line == line.upper() and len(line) < 44
        has_dates = bool(re.search(r"(19|20)\d{2}\s*[-\u2013]\s*((19|20)\d{2}|present)", line, re.I))
        if is_heading or has_dates:
            flush()
            continue

        if current_no is not None:
            current.append(line)      # wrapped continuation
        # else: ordinary prose outside a bullet — ignore

    flush()
    return out


# --------------------------------------------------------------------------
# The guard
# --------------------------------------------------------------------------

def verify_suggestion(original: str, suggestion: str) -> tuple[bool, str]:
    """Reject any suggestion introducing a number the user didn't write.

    Placeholders like [X] and [number] are the intended way to represent an
    unknown, and they pass. Concrete digits that weren't in the source do not.

    >>> verify_suggestion("Helped with onboarding", "Cut onboarding time by 40%")[0]
    False
    >>> verify_suggestion("Helped with onboarding", "Cut onboarding time by [X]%")[0]
    True
    >>> verify_suggestion("Cut it from 9 days to 2", "Cut onboarding from 9 days to 2")[0]
    True
    """
    if not suggestion:
        return False, "empty suggestion"

    # Strip placeholders before looking for digits.
    cleaned = re.sub(r"\[[^\]]*\]", "", suggestion)
    src_nums = set(re.findall(r"\d[\d,\.]*", original or ""))
    new_nums = set(re.findall(r"\d[\d,\.]*", cleaned))

    invented = {n for n in new_nums if n not in src_nums}
    if invented:
        return False, f"introduces a number the resume doesn't contain: {sorted(invented)}"

    if not re.search(r"\[[^\]]+\]", suggestion) and not _ANY_DIGIT.search(original or ""):
        # No placeholder and no source number means it silently dropped the
        # measurement slot — the one thing the rewrite was supposed to add.
        return False, "no placeholder for the user to fill in"

    return True, ""


# --------------------------------------------------------------------------
# Suggestions
# --------------------------------------------------------------------------

# What a measurable outcome actually looks like in each domain. The metric TYPE
# is context — the metric VALUE is not ours to supply.
DOMAIN_HINTS: list[tuple[tuple[str, ...], str]] = [
    (("api", "endpoint", "backend", "microservice", "rest", "graphql", "latency"),
     "response time down to __ms, or __ requests/sec handled"),
    (("java", "spring", "jvm", "kotlin"),
     "response time cut by __%, or __ concurrent users supported"),
    (("sql", "query", "database", "postgres", "mysql", "warehouse"),
     "query time down from __s to __s, or __ rows processed"),
    (("pipeline", "etl", "airflow", "batch", "ingest"),
     "runtime cut from __h to __h, or __ records a day"),
    (("model", "ml", "machine learning", "training", "inference", "neural"),
     "accuracy up __ points, or inference time down __ms"),
    (("trading", "bot", "strategy", "backtest", "quant"),
     "__% return over __ months, or drawdown cut to __%"),
    (("test", "coverage", "qa", "bug", "defect"),
     "coverage up to __%, or defects down __%"),
    (("deploy", "ci", "cd", "docker", "kubernetes", "infra", "server"),
     "deploy time from __min to __min, or __ services migrated"),
    (("onboard", "signup", "activation", "funnel", "conversion"),
     "conversion up from __% to __%, or time-to-value down to __ days"),
    (("revenue", "pricing", "billing", "churn", "retention", "arr"),
     "revenue up __%, or churn down __ points"),
    (("roadmap", "stakeholder", "planning", "prioriti"),
     "__ teams aligned, or __ weeks of rework avoided"),
    (("design", "ui", "ux", "figma", "redesign"),
     "support tickets down __%, or task completion up __%"),
    (("mentor", "team", "hire", "led", "manage"),
     "__ engineers mentored, or ramp-up time cut to __ weeks"),
    (("scrap", "crawl", "automat", "script"),
     "__ hours a week saved, or __ records processed automatically"),
]


def metric_hint(line: str) -> str:
    """Suggest the KIND of metric that fits this bullet's subject matter.

    A generic "[add a percentage]" gives no help — the writer still has to work
    out what to measure. Naming the metric type for their actual domain does.

    The number stays blank on purpose. A filled-in example like
    "decreasing API response time by 30%" is a figure the user never measured,
    and pasting it onto a resume means defending it in an interview. We supply
    the shape; only they can supply the value.

    >>> metric_hint("Built REST endpoints in Java for the billing service")
    'response time down to __ms, or __ requests/sec handled'
    """
    low = (line or "").lower()
    for keywords, hint in DOMAIN_HINTS:
        if any(k in low for k in keywords):
            return hint
    return "a number, a time saved, or a percentage"


def _pick_verb(line: str) -> str:
    """Choose a strong opener that fits what the line is about."""
    low = line.lower()
    if any(w in low for w in ("reduc", "cut", "decreas", "less", "fewer", "time")):
        return "Cut"
    if any(w in low for w in ("grew", "increas", "more", "growth", "revenue", "expansion")):
        return "Grew"
    if any(w in low for w in ("launch", "ship", "releas", "built", "created")):
        return "Shipped"
    if any(w in low for w in ("team", "led", "manage", "coordinat", "stakeholder")):
        return "Led"
    if any(w in low for w in ("automat", "script", "pipeline", "process")):
        return "Automated"
    return "Owned"


def suggest_rewrite(line: str) -> tuple[str, str]:
    """Return (suggestion, why). The suggestion contains blanks, never numbers.

    The blank is the point. We know the shape of a strong bullet; only the
    person who did the work knows the outcome.
    """
    wo = weak_opener(line)
    body = line
    if wo:
        body = re.sub(rf"^{re.escape(wo)}\s*", "", line.strip(), flags=re.I)
        body = body[0].lower() + body[1:] if body else body

    body = re.sub(r"\s+", " ", body).strip().rstrip(".")

    if has_metric(line) and not wo:
        return "", ""

    # Only supply a verb when we removed a weak one. Otherwise the line already
    # has its own — prepending gave "Cut Partnered with design" and
    # "Led Ran weekly business reviews". If the bullet's real problem is a
    # missing outcome, rewriting the verb is meddling, not helping.
    if wo:
        stem = f"{_pick_verb(line)} {body}"
    else:
        stem = body[0].upper() + body[1:] if body else body

    if has_metric(line) and wo:
        return (f"{stem}.",
                f"The result is already here — it just opens with \"{wo}\", "
                f"which reads as being present rather than responsible.")

    hint = metric_hint(line)
    return (f"{stem} — [e.g. {hint}].",
            f"This says what you did, but not what changed because of it. "
            f"For work like this, the number that lands is usually something like "
            f"{hint.replace('__', 'X')} — fill in your own figure.")


def analyse_resume(resume_text: str, skills_listed_only: list[str] | None = None,
                   limit: int = 6) -> list[LineIssue]:
    """Find the weakest bullets, worst first.

    Ranked by number of problems, then by position — earlier bullets carry
    more weight because most screeners don't reach the bottom of page one.
    """
    issues: list[LineIssue] = []

    for line_no, body in bullet_lines(resume_text):
        problems, wo = [], weak_opener(body)
        if wo:
            problems.append("opens weakly")
        if not has_metric(body):
            problems.append("no measurable outcome")
        if len(body.split()) > 34:
            problems.append("too long to scan")
        if not problems:
            continue

        suggestion, why = suggest_rewrite(body)
        if suggestion:
            ok, reason = verify_suggestion(body, suggestion)
            if not ok:
                # Should not happen with templates, but the guard runs on every
                # path so a future model-generated suggestion can't skip it.
                suggestion, why = "", f"suggestion withheld: {reason}"

        issues.append(LineIssue(line=body, line_no=line_no, problems=problems,
                                weak_opener=wo, suggestion=suggestion, why=why))

    issues.sort(key=lambda i: (-i.severity, i.line_no))
    out = issues[:limit]

    # A skill named in the skills blob but never evidenced in a bullet is a
    # different kind of gap, and the cheapest one to fix.
    for skill in (skills_listed_only or [])[:2]:
        out.append(LineIssue(
            line=f"{skill} — appears only in your skills list",
            line_no=0,
            problems=["named but never evidenced"],
            suggestion=f"[Verb] [what you built with {skill}] — [e.g. {metric_hint(skill)}].",
            why=(f"A screener reads {skill} in a list very differently from "
                 f"{skill} inside something you achieved. Add one bullet."),
        ))
    return out


def to_json(issues: list[LineIssue]) -> dict:
    return {
        "version": DIFF_VERSION,
        "count": len(issues),
        "note": ("Suggestions are shapes, not sentences. Every bracket is a number "
                 "only you can supply — we never invent one."),
        "lines": [
            {
                "line_no": i.line_no,
                "original": i.line,
                "problems": i.problems,
                "suggestion": i.suggestion,
                "why": i.why,
            }
            for i in issues
        ],
    }
