"""
Deterministic extraction and derivation. NO LLM CALLS IN THIS FILE.

Everything here is a pure function you can unit-test with a fixed expected
answer. If a number appears in the product, it should be traceable to
something in this module or in stats.py — never to a model's opinion.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

from .enums import EventType, Resolution, Seniority, Stage

DERIVATION_VERSION = "0.1.0"
PARSER_VERSION = "0.1.0"

# Applications with no terminal event and no activity for this long are
# treated as ghosted rather than open. 45 days is a judgement call, not a
# fact — it is a tunable constant on purpose, and it belongs in one place.
GHOST_THRESHOLD_DAYS = 45


# --------------------------------------------------------------------------
# Seniority
# --------------------------------------------------------------------------

# Order matters: first match wins, so more specific patterns come first.
_SENIORITY_PATTERNS: list[tuple[str, Seniority]] = [
    (r"\b(vp|vice president|head of|chief|cxo|c-level)\b", Seniority.VP_PLUS),
    (r"\b(director|dir\.)\b", Seniority.DIRECTOR),
    (r"\b(principal|distinguished|fellow)\b", Seniority.PRINCIPAL),
    (r"\b(staff|lead)\b", Seniority.STAFF),
    (r"\b(senior|sr\.?|snr)\b", Seniority.SENIOR),
    (r"\b(junior|jr\.?|associate|entry[- ]level|graduate|grad)\b", Seniority.JUNIOR),
    (r"\b(intern|internship|trainee)\b", Seniority.INTERN),
    (r"\b(i{1,3}|1|2|3)\b$", None),  # trailing roman/arabic levels, handled below
]

_LEVEL_SUFFIX = re.compile(r"\b(i{1,3}|iv|v|[1-5])\s*$", re.I)


def infer_seniority(title: str, default: Seniority = Seniority.MID) -> Seniority:
    """Map a job title to a seniority band.

    >>> infer_seniority("Senior Product Manager")
    <Seniority.SENIOR: 'senior'>
    >>> infer_seniority("Product Manager")
    <Seniority.MID: 'mid'>
    >>> infer_seniority("Director of Product")
    <Seniority.DIRECTOR: 'director'>
    """
    t = title.lower().strip()
    for pattern, level in _SENIORITY_PATTERNS:
        if level is None:
            continue
        if re.search(pattern, t):
            return level

    # "Engineer II" style levelling
    m = _LEVEL_SUFFIX.search(t)
    if m:
        token = m.group(1).lower()
        mapping = {"i": Seniority.JUNIOR, "1": Seniority.JUNIOR,
                   "ii": Seniority.MID, "2": Seniority.MID,
                   "iii": Seniority.SENIOR, "3": Seniority.SENIOR,
                   "iv": Seniority.STAFF, "4": Seniority.STAFF,
                   "v": Seniority.PRINCIPAL, "5": Seniority.PRINCIPAL}
        if token in mapping:
            return mapping[token]

    return default


# --------------------------------------------------------------------------
# Years of experience
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Tenure:
    start: date
    end: date | None      # None == present

    def resolved_end(self, today: date) -> date:
        return self.end or today


def merge_tenure_months(tenures: list[Tenure], today: date | None = None) -> int:
    """Total months of experience with OVERLAPS MERGED.

    Naively summing role durations double-counts concurrent roles (contracting,
    a side company, an internal transfer logged as two entries). Almost every
    resume parser gets this wrong and inflates experience by 10-25%.

    >>> merge_tenure_months([
    ...     Tenure(date(2020, 1, 1), date(2022, 1, 1)),
    ...     Tenure(date(2021, 1, 1), date(2023, 1, 1)),
    ... ], today=date(2026, 1, 1))
    36
    """
    today = today or date.today()
    if not tenures:
        return 0

    intervals = sorted(
        ((t.start, t.resolved_end(today)) for t in tenures), key=lambda x: x[0]
    )
    merged: list[list[date]] = [list(intervals[0])]
    for start, end in intervals[1:]:
        if start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])

    months = 0
    for start, end in merged:
        months += (end.year - start.year) * 12 + (end.month - start.month)
    return max(months, 0)


_YEARS_REQ = re.compile(
    r"(\d{1,2})\s*(?:\+|plus)?\s*(?:-|–|to)?\s*(\d{1,2})?\s*(?:\+)?\s*years?"
    r"(?:\s+of)?(?:\s+\w+){0,3}?\s*experience",
    re.I,
)


def extract_min_years_required(jd_text: str) -> int | None:
    """Pull the minimum years-of-experience ask out of a JD.

    Takes the *lowest* number found, because JDs frequently list a headline
    requirement plus higher asks for nice-to-haves.

    >>> extract_min_years_required("We need 5+ years experience in product")
    5
    >>> extract_min_years_required("3-5 years of relevant experience required")
    3
    """
    found = [int(m.group(1)) for m in _YEARS_REQ.finditer(jd_text)]
    return min(found) if found else None


# --------------------------------------------------------------------------
# Skill matching
# --------------------------------------------------------------------------

@dataclass
class SkillVocab:
    """canonical -> set of lowercase aliases (including the canonical form)."""
    entries: dict[str, set[str]] = field(default_factory=dict)

    # Canonical names that are ordinary English words. Word-boundary matching
    # stops "Go" matching inside "Google", but not the standalone word "go" in
    # "go beyond" — and "R" is a letter before it is a language. For these the
    # canonical is NOT a match form; only the explicit aliases are.
    # Names that are ordinary English words. Never matchable in this exact
    # spelling, from any entry — the skill must be reached through a qualified
    # alias ("chef cookbook", "patient triage") or not at all.
    ENGLISH_WORDS = {
        "go", "r", "tax", "audit", "brand", "astro", "helm", "celery",
        "iceberg", "notion", "framer", "lever", "greenhouse", "segment",
        "beam", "arrow", "hive", "pig", "storm", "drill", "atlas", "nomad",
        "glue", "fabric", "fusion", "composer", "ray", "chef", "puppet",
        "salt", "consul", "lambda", "route", "rancher", "operators",
        "epic", "sage", "prime", "close", "triage",
        # Legal and manufacturing are unusually full of ordinary words.
        "brief", "counsel", "discovery", "practice", "matter", "trust",
        "will", "bar", "lean", "yield", "batch", "plant", "cell", "line",
        "shift", "inspection", "procurement", "logistics",
    }

    AMBIGUOUS_CANONICALS = {"go", "r", "c#", "c++", "qa", "hr", "ux", "ui", "crm",
                            "erp", "seo", "etl", "tax", "audit", "brand", "git",
                            # Second expansion: every one of these is a real
                            # product AND an ordinary English word. "Astro"
                            # matched "astro-project" in prose during the check
                            # that added them.
                            "astro", "helm", "celery", "iceberg", "notion",
                            "flink", "framer", "lever", "greenhouse", "segment",
                            # Data engineering: each is a product and a word.
                            "beam", "arrow", "hive", "pig", "storm", "presto",
                            "drill", "atlas", "nomad", "glue", "fabric",
                            "fusion", "composer", "ray", "prefect",
                            # Cloud & DevOps. Chef, Puppet and Salt are the
                            # canonical example of this whole rule.
                            "chef", "puppet", "salt", "packer", "vagrant",
                            "consul", "nomad", "lambda", "route", "rancher",
                            "operators", "sentry", "pager",
                            # Healthcare and finance: vendors and terms of art
                            # that are also ordinary words.
                            "epic", "sage", "prime", "close", "basel", "triage",
                            "xero", "cima", "pals"}

    # Proper nouns that are also ordinary words, where excluding the bare name
    # would cost more than it saves: a job description writes "experience with
    # Rust", and guarding it would miss almost every real mention. These match
    # case-sensitively instead — "Rust" is the language, "rust on the railings"
    # is not. Verified: all three fired on prose before this existed.
    CASE_SENSITIVE = {"Rust", "Swift", "Spark"}

    def add(self, canonical: str, aliases: list[str] | None = None) -> None:
        forms = {a.lower() for a in (aliases or [])}

        # A word in ENGLISH_WORDS must never match in that exact spelling —
        # not as a canonical, and not as an alias either. "Triage" was excluded
        # as a canonical and re-added as the alias "triage", which fired on
        # "triage your inbox".
        #
        # AMBIGUOUS_CANONICALS is a weaker rule and deliberately so: "CRM",
        # "ETL" and "C++" are acronyms, not words, and listing them as their
        # own alias is how they match at all. Filtering those broke eight
        # skills outright.
        forms -= self.ENGLISH_WORDS
        # An English word is never a matchable canonical either. Keeping the
        # two exclusions on separate lists meant adding "inspection" to
        # ENGLISH_WORDS while the canonical check still read the other set, so
        # "Inspection" matched "under close inspection". One test, both lists.
        if canonical.lower() not in (self.AMBIGUOUS_CANONICALS | self.ENGLISH_WORDS):
            forms.add(canonical.lower())
        if not forms:
            # An ambiguous name with no aliases would never match anything —
            # better to fail loudly at load than silently drop a skill.
            raise ValueError(
                f"{canonical!r} is ambiguous and needs explicit aliases.")
        self.entries[canonical] = forms

    def find(self, text: str) -> dict[str, int]:
        """Return {canonical: mention_count} using word-boundary matching.

        Word boundaries matter more than they look: substring matching turns
        'R' into a match inside every word containing an r, and 'Go' into a
        match inside 'Google'.
        """
        lowered = text.lower()
        hits: dict[str, int] = {}
        for canonical, forms in self.entries.items():
            count = 0
            cased = canonical in self.CASE_SENSITIVE
            for form in forms:
                escaped = re.escape(form)
                if cased and form == canonical.lower():
                    # Match the capitalised proper noun against the original
                    # text; the multi-word aliases below stay case-insensitive.
                    pattern = rf"(?<![A-Za-z0-9]){re.escape(canonical)}(?![A-Za-z0-9+#])"
                    count += len(re.findall(pattern, text))
                    continue
                # C++ / C# / .NET need lookarounds rather than \b
                pattern = rf"(?<![a-z0-9]){escaped}(?![a-z0-9+#])"
                count += len(re.findall(pattern, lowered))
            if count:
                hits[canonical] = count
        return hits


def default_vocab() -> SkillVocab:
    """The cold-cache vocabulary, loaded from core/vocabulary.py.

    This began as fifteen product-management terms, which meant a backend
    advert asking for "Java, Spring Boot and Kubernetes" parsed to nothing —
    a confident report computed against a fraction of the actual requirements,
    with no sign anything had been missed.

    Kept in a data file so adding a domain is an edit to a list rather than a
    change to this function.
    """
    from .vocabulary import all_entries

    v = SkillVocab()
    for canonical, aliases in all_entries():
        v.add(canonical, aliases)
    return v


# --------------------------------------------------------------------------
# Funnel derivation from the event stream
# --------------------------------------------------------------------------

_EVENT_TO_STAGE: dict[EventType, Stage] = {
    EventType.SUBMITTED: Stage.SUBMITTED,
    EventType.ACKNOWLEDGED: Stage.SUBMITTED,
    EventType.RECRUITER_CONTACT: Stage.SCREEN,
    EventType.SCREEN_SCHEDULED: Stage.SCREEN,
    EventType.SCREEN_COMPLETED: Stage.SCREEN,
    EventType.ASSESSMENT_SENT: Stage.SCREEN,
    EventType.INTERVIEW_SCHEDULED: Stage.INTERVIEW,
    EventType.INTERVIEW_COMPLETED: Stage.INTERVIEW,
    EventType.FINAL_ROUND: Stage.FINAL,
    EventType.OFFER: Stage.OFFER,
}

_RESPONSE_EVENTS = {
    EventType.RECRUITER_CONTACT,
    EventType.SCREEN_SCHEDULED,
    EventType.INTERVIEW_SCHEDULED,
    EventType.ASSESSMENT_SENT,
    EventType.OFFER,
}


@dataclass
class DerivedOutcome:
    furthest_stage: Stage
    resolution: Resolution
    resolved_at: date | None
    days_to_first_response: int | None
    version: str = DERIVATION_VERSION


def derive_outcome(
    applied_at: date,
    events: list[tuple[EventType, date]],
    as_of: date,
    ghost_threshold_days: int = GHOST_THRESHOLD_DAYS,
) -> DerivedOutcome:
    """Collapse an event stream into a funnel position and a resolution.

    The important case is the one with no events: silence. We call that
    GHOSTED only after the threshold, and OPEN before it. We never call it
    'rejected', because we did not observe a rejection.
    """
    events = sorted(events, key=lambda e: e[1])

    furthest = Stage.SUBMITTED
    for etype, _ in events:
        stage = _EVENT_TO_STAGE.get(etype)
        if stage and stage.rank > furthest.rank:
            furthest = stage

    first_response = next(
        (d for etype, d in events if etype in _RESPONSE_EVENTS), None
    )
    days_to_first = (first_response - applied_at).days if first_response else None

    terminal = {
        EventType.OFFER: Resolution.OFFER,
        EventType.REJECTION_RECEIVED: Resolution.REJECTED,
        EventType.WITHDRAWN: Resolution.WITHDRAWN,
    }
    for etype, d in reversed(events):
        if etype in terminal:
            return DerivedOutcome(furthest, terminal[etype], d, days_to_first)

    last_activity = events[-1][1] if events else applied_at
    if (as_of - last_activity).days >= ghost_threshold_days:
        return DerivedOutcome(furthest, Resolution.GHOSTED, None, days_to_first)
    return DerivedOutcome(furthest, Resolution.OPEN, None, days_to_first)


def got_response(outcome: DerivedOutcome) -> bool:
    """The primary outcome variable for the MVP.

    We use 'got any human response' rather than 'got an interview' because at
    n=50-100 the interview rate is too sparse to support any test. Response
    rate has a base rate around 4-8x higher, which is the difference between
    a detectable effect and noise.
    """
    return outcome.furthest_stage.rank >= Stage.SCREEN.rank


def is_analyzable(outcome: DerivedOutcome) -> bool:
    """Exclude still-open and candidate-withdrawn applications from stats.

    Including OPEN applications biases against recent ones (they have had less
    time to respond). Including WITHDRAWN attributes the candidate's own
    decision to the employer.
    """
    return outcome.resolution in {
        Resolution.REJECTED, Resolution.GHOSTED, Resolution.OFFER
    }
