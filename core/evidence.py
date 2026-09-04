"""
The OBSERVED tier — extracting what employers actually said.

THE ONE RULE
------------
An OBSERVED finding must quote the employer verbatim. The moment a reason is
paraphrased it becomes an inference wearing an observation's badge, and the
whole tier system stops meaning anything.

That is not enforceable by prompt. So `verbatim_span` is checked against the
source text after extraction, and any span that isn't a literal substring is
rejected. Same principle as the numeric guard in narrate.py: the model's output
is validated against the source, not trusted.

THE OTHER PROBLEM: MOST REJECTIONS SAY NOTHING
----------------------------------------------
Real rejection emails are overwhelmingly boilerplate — "we've decided to move
forward with other candidates" tells you literally nothing. Expect 80-90% of a
user's rejection text to be unusable.

So there is a deterministic boilerplate screen BEFORE any model call. It costs
nothing, runs on the free tier without touching quota, and stops the OBSERVED
tier filling up with noise. Only text that survives the screen is worth
spending an extraction on.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from pydantic import BaseModel, Field, field_validator

CLASSIFIER_VERSION = "0.1.0"

# --------------------------------------------------------------------------
# Categories
# --------------------------------------------------------------------------

CATEGORIES = {
    "skill_gap":        "A specific skill or tool they wanted more of",
    "experience_level": "Amount or depth of experience",
    "seniority":        "Too junior or too senior for the level",
    "domain":           "Industry or domain background",
    "compensation":     "Salary expectations",
    "location_auth":    "Location, relocation or work authorisation",
    "role_filled":      "Filled internally, frozen, or cancelled",
    "process":          "Timing, process, or candidate volume",
    "boilerplate":      "Says nothing actionable",
}

ACTIONABLE = {"skill_gap", "experience_level", "seniority", "domain", "compensation"}


# --------------------------------------------------------------------------
# Deterministic boilerplate screen — runs before any model call
# --------------------------------------------------------------------------

BOILERPLATE_PATTERNS = [
    r"move forward with (other|another|different)",
    r"decided to (proceed|move|go) (with|forward)",
    r"other candidates? whose",
    r"not (be )?(moving|progressing|proceeding)",
    r"keep your (resume|cv|application) on file",
    r"best of luck",
    r"wish you (all the best|the best|success)",
    r"we (were |are )?(very )?impressed",
    r"thank you for (your (interest|time|application)|applying)",
    r"after (careful|much) (consideration|review|deliberation)",
    r"unusually (strong|competitive|large) (applicant )?(pool|field)",
    r"encourage you to apply",
    r"this (isn'?t|is not) a reflection",
    r"we received (a large|many|numerous)",
    r"unfortunately",
]
_BOILER = [re.compile(p, re.I) for p in BOILERPLATE_PATTERNS]

# Phrases that mark a real, specific reason. Their presence overrides the
# boilerplate screen — a message can open with pleasantries and then say
# something genuinely useful.
SIGNAL_PATTERNS = [
    r"\b(more|deeper|stronger|additional|less|greater)\s+\w+(\s+\w+)?\s+(experience|background|expertise|exposure|depth)",
    r"\b(looking for|needed|required|wanted|prefer(red)?)\s+(someone|a candidate)?\s*(with|who)",
    r"\bhands[- ]on\b",
    r"\b(too|not)\s+(junior|senior|experienced)\b",
    r"\b(salary|compensation|budget|range|rate)\b",
    r"\b(visa|sponsor|authoriz|authoris|relocat|onsite|on-site|in office)\w*",
    r"\b(filled (the role )?internally|internal candidate|role (has been|was) (put on hold|frozen|cancelled|canceled))",
    r"\b(\d+\+?\s*years?)\b",
    r"\bgap\b",
]
_SIGNAL = [re.compile(p, re.I) for p in SIGNAL_PATTERNS]

MIN_USEFUL_CHARS = 25


@dataclass
class ScreenResult:
    worth_extracting: bool
    reason: str
    boilerplate_hits: list[str] = field(default_factory=list)
    signal_hits: list[str] = field(default_factory=list)


def screen(text: str) -> ScreenResult:
    """Decide whether a rejection message is worth an extraction call.

    Cheap, deterministic, zero quota. Roughly 80-90% of real rejection text
    should stop here.

    >>> screen("We've decided to move forward with other candidates.").worth_extracting
    False
    >>> screen("We needed more hands-on SQL experience for this role.").worth_extracting
    True
    """
    t = (text or "").strip()
    if len(t) < MIN_USEFUL_CHARS:
        return ScreenResult(False, "too short to contain a reason")

    signal = [p.pattern for p in _SIGNAL if p.search(t)]
    boiler = [p.pattern for p in _BOILER if p.search(t)]

    if signal:
        # A specific statement outweighs any amount of surrounding politeness.
        return ScreenResult(True, "contains a specific statement", boiler, signal)

    if boiler:
        return ScreenResult(False, "boilerplate only", boiler, signal)

    # No known boilerplate and no known signal. Unusual phrasing that might
    # still be meaningful — send it, rather than silently discarding a real
    # reason because our pattern list is incomplete.
    return ScreenResult(True, "unrecognised phrasing, worth checking", boiler, signal)


# --------------------------------------------------------------------------
# Schema
# --------------------------------------------------------------------------

class ExtractedReason(BaseModel):
    verbatim_span: str = Field(
        description="The exact words from the message, copied character for character")
    category: str
    skill: str | None = Field(default=None, description="Canonical skill name if one is named")
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)

    @field_validator("category")
    @classmethod
    def _known(cls, v: str) -> str:
        v = (v or "").strip().lower().replace(" ", "_")
        return v if v in CATEGORIES else "boilerplate"

    @field_validator("verbatim_span")
    @classmethod
    def _nonempty(cls, v: str) -> str:
        v = re.sub(r"\s+", " ", v or "").strip()
        if len(v) < 8:
            raise ValueError("verbatim_span too short to be a real quote")
        return v[:500]


class ReasonExtraction(BaseModel):
    reasons: list[ExtractedReason] = Field(default_factory=list, max_length=4)
    is_boilerplate: bool = False


SYSTEM = """You extract stated reasons from job rejection messages.

You are a quote-finder, not an interpreter.

RULES:
1. verbatim_span MUST be copied character-for-character from the message. Do not \
rephrase, summarise, tidy grammar, or fix typos. If you cannot quote it exactly, \
do not report it.
2. Only report a reason the employer actually stated. Never infer one from tone, \
from what is absent, or from what usually causes rejections.
3. Most rejection messages contain no real reason. Returning an empty list with \
is_boilerplate=true is the correct answer far more often than not.
4. category must be one of: skill_gap, experience_level, seniority, domain, \
compensation, location_auth, role_filled, process, boilerplate.
5. Set skill only when a specific named tool or skill appears in the message."""


# --------------------------------------------------------------------------
# The guard
# --------------------------------------------------------------------------

def _normalise(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip().lower()


def verify_span(span: str, source: str) -> tuple[bool, str]:
    """Confirm the quote genuinely appears in the message.

    Whitespace is normalised on both sides — a model that collapses a line
    break is quoting faithfully. Anything beyond that is a paraphrase, and a
    paraphrase must not be presented as an observation.

    >>> verify_span("more hands-on SQL", "We needed more hands-on SQL for this.")
    (True, '')
    >>> verify_span("they wanted better SQL skills", "We needed more hands-on SQL.")[0]
    False
    """
    if not span:
        return False, "empty span"
    if _normalise(span) in _normalise(source):
        return True, ""
    return False, "span is not a literal quote from the message"


@dataclass
class Evidence:
    application_id: int
    verbatim_span: str
    category: str
    skill: str | None
    is_actionable: bool
    confidence: float
    classifier_version: str = CLASSIFIER_VERSION


@dataclass
class EvidenceResult:
    evidence: list[Evidence] = field(default_factory=list)
    screened_out: int = 0
    extracted: int = 0
    rejected_spans: int = 0
    failed: int = 0
    notes: list[str] = field(default_factory=list)

    @property
    def actionable(self) -> list[Evidence]:
        return [e for e in self.evidence if e.is_actionable]


async def extract_reasons(
    items: list[tuple[int, str]],        # (application_id, rejection_text)
    provider=None,
    budget: int | None = None,
) -> EvidenceResult:
    """Screen, then extract, then verify every quote against its source."""
    import asyncio

    from .extract_llm import canonicalize
    from .llm_provider import RateLimited, complete_with_backoff

    result = EvidenceResult()
    candidates: list[tuple[int, str]] = []

    for app_id, text in items:
        sr = screen(text)
        if not sr.worth_extracting:
            result.screened_out += 1
            continue
        candidates.append((app_id, text))

    if provider is None or not candidates:
        if provider is None and candidates:
            result.notes.append(
                f"{len(candidates)} message(s) look substantive but no LLM provider "
                "is configured, so no Observed findings were extracted.")
        return result

    if budget is not None:
        candidates = candidates[:budget]

    schema = ReasonExtraction.model_json_schema()
    schema.pop("$defs", None)
    sem = asyncio.Semaphore(provider.info.max_concurrency)

    async def one(app_id: int, text: str):
        async with sem:
            try:
                raw = await complete_with_backoff(
                    provider, SYSTEM, f"REJECTION MESSAGE:\n{text[:2000]}", schema)
            except RateLimited:
                result.failed += 1
                return
            except Exception:  # noqa: BLE001
                result.failed += 1
                return

            try:
                parsed = ReasonExtraction.model_validate(raw)
            except Exception:  # noqa: BLE001
                result.failed += 1
                return

            for r in parsed.reasons:
                ok, why = verify_span(r.verbatim_span, text)
                if not ok:
                    # A paraphrase. Discard it — this is the guarantee that
                    # makes the tier mean something.
                    result.rejected_spans += 1
                    result.notes.append(f"app {app_id}: {why}")
                    continue
                result.evidence.append(Evidence(
                    application_id=app_id,
                    verbatim_span=r.verbatim_span,
                    category=r.category,
                    skill=canonicalize(r.skill) if r.skill else None,
                    is_actionable=r.category in ACTIONABLE,
                    confidence=r.confidence,
                ))
                result.extracted += 1

    await asyncio.gather(*[one(a, t) for a, t in candidates])
    return result


# --------------------------------------------------------------------------
# Aggregation into findings
# --------------------------------------------------------------------------

@dataclass
class ObservedFinding:
    dimension: str
    category: str
    skill: str | None
    quotes: list[str]
    employer_count: int

    @property
    def headline(self) -> str:
        n = self.employer_count
        who = "1 employer" if n == 1 else f"{n} employers"
        if self.skill:
            return f"{who} named {self.skill} in writing"
        return f"{who} gave a reason: {CATEGORIES.get(self.category, self.category)}"


def aggregate(evidence: list[Evidence]) -> list[ObservedFinding]:
    """Group evidence into findings, most-corroborated first.

    Grouping by skill where one is named, otherwise by category. One employer
    saying something is a data point; three saying the same thing is a pattern
    you can act on — and unlike the statistical tiers, this needs no
    correction, because we are counting statements rather than testing a
    hypothesis.
    """
    groups: dict[tuple[str, str | None], list[Evidence]] = {}
    for e in evidence:
        if not e.is_actionable:
            continue
        groups.setdefault((e.category, e.skill), []).append(e)

    out = []
    for (category, skill), items in groups.items():
        out.append(ObservedFinding(
            dimension=f"observed:{skill or category}",
            category=category,
            skill=skill,
            quotes=[i.verbatim_span for i in items][:5],
            employer_count=len({i.application_id for i in items}),
        ))
    out.sort(key=lambda f: -f.employer_count)
    return out
