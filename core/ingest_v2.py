"""
Resilient ingest — built for CSVs a human maintained by hand.

DESIGN PRINCIPLE
----------------
A real export has a column called "Company Name" or "employer", dates in four
formats, blank rows, a BOM, and one row where someone typed "n/a". The v1
ingest rejected all of it. Every rejection is a user who leaves.

So: match columns by similarity rather than equality, try many date formats,
and NEVER let one bad row kill the batch. A row that can't be parsed is
reported with its row number and the reason, and the other 87 still go through.

WHAT IS DELIBERATELY NOT FUZZY
------------------------------
Outcomes. If we can't confidently map "maybe?" to a funnel stage we ask rather
than guess, because guessing here silently corrupts the statistics — the one
thing in this product that has to be right.
"""

from __future__ import annotations

import csv
import io
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from difflib import SequenceMatcher

from .enums import EventType

INGEST_VERSION = "0.2.0"

# --------------------------------------------------------------------------
# Column matching
# --------------------------------------------------------------------------

# canonical -> known aliases. Matching is: exact -> normalised -> fuzzy.
COLUMN_ALIASES: dict[str, list[str]] = {
    "company": ["company", "company name", "employer", "organisation", "organization",
                "firm", "employer name", "company/organisation"],
    "job_title": ["job title", "title", "role", "position", "job", "job role",
                  "role title", "position title"],
    "applied_date": ["applied date", "date applied", "application date", "applied",
                     "date", "submitted", "date submitted", "apply date"],
    "outcome": ["outcome", "status", "result", "stage", "current status",
                "application status", "response"],
    "job_description": ["job description", "jd", "description", "posting",
                        "job post", "requirements", "job details"],
    "resume_version": ["resume version", "resume", "cv version", "cv",
                       "resume used", "which resume", "variant"],
    "source": ["source", "channel", "applied via", "via", "platform", "where"],
    "first_response_date": ["first response date", "response date", "replied",
                            "date of response", "first reply"],
    "screen_date": ["screen date", "phone screen", "screening date", "recruiter call"],
    "interview_date": ["interview date", "interview", "date of interview"],
    "final_round_date": ["final round date", "final round", "onsite", "onsite date"],
    "offer_date": ["offer date", "offer", "date of offer"],
    "rejection_date": ["rejection date", "rejected", "date rejected", "rejected on"],
    "rejection_text": ["rejection text", "rejection reason", "feedback", "notes",
                       "rejection email", "reason", "recruiter notes", "comments"],
    "salary_posted": ["salary", "salary posted", "compensation", "pay", "salary range"],
    "location": ["location", "city", "place", "where based", "office"],
}

REQUIRED = ["company", "job_title", "applied_date", "outcome"]
FUZZY_THRESHOLD = 0.82


def _norm(s: str) -> str:
    """Lowercase, strip accents/punctuation, collapse whitespace."""
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"[_\-/\\.]+", " ", s.lower())
    s = re.sub(r"[^a-z0-9 ]+", "", s)
    return re.sub(r"\s+", " ", s).strip()


@dataclass
class ColumnMatch:
    canonical: str
    header: str
    confidence: float
    method: str          # exact | alias | fuzzy


def match_columns(headers: list[str]) -> tuple[dict[str, ColumnMatch], list[str]]:
    """Map incoming headers to canonical names.

    Returns (matches, unmatched_headers). Each header is used at most once, and
    when two headers compete for one canonical name the better score wins.

    >>> m, _ = match_columns(["Company Name", "Role", "Date Applied", "Status"])
    >>> sorted(m)
    ['applied_date', 'company', 'job_title', 'outcome']
    """
    normalized = {h: _norm(h) for h in headers if h}
    candidates: list[tuple[float, str, str, str]] = []

    for canonical, aliases in COLUMN_ALIASES.items():
        alias_norms = {_norm(a) for a in aliases}
        for header, hn in normalized.items():
            if not hn:
                continue
            if hn == _norm(canonical):
                candidates.append((1.0, canonical, header, "exact"))
            elif hn in alias_norms:
                candidates.append((0.97, canonical, header, "alias"))
            else:
                best = max(
                    (SequenceMatcher(None, hn, a).ratio() for a in alias_norms),
                    default=0.0,
                )
                if best >= FUZZY_THRESHOLD:
                    candidates.append((best, canonical, header, "fuzzy"))

    candidates.sort(key=lambda c: -c[0])
    matches: dict[str, ColumnMatch] = {}
    used_headers: set[str] = set()

    for score, canonical, header, method in candidates:
        if canonical in matches or header in used_headers:
            continue
        matches[canonical] = ColumnMatch(canonical, header, round(score, 3), method)
        used_headers.add(header)

    unmatched = [h for h in headers if h and h not in used_headers]
    return matches, unmatched


# --------------------------------------------------------------------------
# Dates
# --------------------------------------------------------------------------

_MONTHS = ("jan feb mar apr may jun jul aug sep oct nov dec").split()

DATE_FORMATS = [
    "%Y-%m-%d", "%Y/%m/%d", "%d-%m-%Y", "%d/%m/%Y", "%m/%d/%Y", "%m-%d-%Y",
    "%d %b %Y", "%d %B %Y", "%b %d %Y", "%B %d %Y", "%b %d, %Y", "%B %d, %Y",
    "%d-%b-%Y", "%d-%B-%Y", "%Y%m%d", "%d.%m.%Y", "%m.%d.%Y",
    "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%b %Y", "%B %Y",
]

_RELATIVE = re.compile(r"(\d+)\s*(day|week|month)s?\s*ago", re.I)


@dataclass
class DateResult:
    value: date | None
    ambiguous: bool = False       # e.g. 03/04/2026 — could be Mar 4 or Apr 3
    note: str = ""


def parse_date(raw: str, today: date | None = None,
               prefer_day_first: bool | None = None) -> DateResult:
    """Normalise a date written by a human.

    `prefer_day_first` resolves the DD/MM vs MM/DD ambiguity. It should be
    inferred once for the whole file (see `infer_day_first`) rather than
    guessed per row, because a single file is written by a single person in a
    single convention.

    >>> parse_date("2026-03-14").value
    datetime.date(2026, 3, 14)
    >>> parse_date("14/03/2026", prefer_day_first=True).value
    datetime.date(2026, 3, 14)
    >>> parse_date("Mar 14, 2026").value
    datetime.date(2026, 3, 14)
    """
    today = today or date.today()
    s = (raw or "").strip()
    if not s or s.lower() in {"n/a", "na", "none", "-", "--", "?", "tbd", "unknown"}:
        return DateResult(None)

    low = s.lower()
    if low in {"today", "now"}:
        return DateResult(today)
    if low == "yesterday":
        return DateResult(date.fromordinal(today.toordinal() - 1))

    m = _RELATIVE.search(low)
    if m:
        n = int(m.group(1))
        days = {"day": 1, "week": 7, "month": 30}[m.group(2).lower()] * n
        return DateResult(date.fromordinal(today.toordinal() - days),
                          note="converted from a relative date")

    cleaned = re.sub(r"\s+", " ", s.replace(",", ", ").replace(", ", ", ")).strip()
    cleaned = re.sub(r"(\d)(st|nd|rd|th)\b", r"\1", cleaned, flags=re.I)

    # Numeric slash/dash dates: decide the order explicitly.
    nums = re.match(r"^(\d{1,4})[/\-.](\d{1,2})[/\-.](\d{2,4})$", cleaned)
    if nums:
        a, b, c = (int(x) for x in nums.groups())
        if a > 31:                                    # leading 4-digit year
            return DateResult(_safe(a, b, c))
        year = c if c > 99 else 2000 + c
        if a > 12:                                    # must be day-first
            return DateResult(_safe(year, b, a))
        if b > 12:                                    # must be month-first
            return DateResult(_safe(year, a, b))
        if prefer_day_first is None:
            return DateResult(_safe(year, b, a), ambiguous=True,
                              note=f"'{s}' could be day-first or month-first")
        return DateResult(_safe(year, b, a) if prefer_day_first else _safe(year, a, b))

    for fmt in DATE_FORMATS:
        try:
            return DateResult(datetime.strptime(cleaned, fmt).date())
        except ValueError:
            continue

    # Last resort: pull a month name and a year out of free text.
    mo = next((i + 1 for i, mn in enumerate(_MONTHS) if mn in low), None)
    yr = re.search(r"(19|20)\d{2}", s)
    if mo and yr:
        dy = re.search(r"\b(\d{1,2})\b", re.sub(r"(19|20)\d{2}", "", s))
        return DateResult(_safe(int(yr.group()), mo, int(dy.group(1)) if dy else 1),
                          note="reconstructed from partial text")

    return DateResult(None, note=f"could not read '{s}' as a date")


def _safe(y: int, m: int, d: int) -> date | None:
    try:
        return date(y, m, d)
    except ValueError:
        return None


def infer_day_first(samples: list[str]) -> bool | None:
    """Decide DD/MM vs MM/DD once, from the whole column.

    If any value has a first component above 12 the file is day-first; if any
    has a second component above 12 it is month-first. Otherwise unknowable,
    and we say so rather than assuming a locale.
    """
    day_first = month_first = 0
    for s in samples:
        m = re.match(r"^\s*(\d{1,2})[/\-.](\d{1,2})[/\-.]\d{2,4}\s*$", s or "")
        if not m:
            continue
        a, b = int(m.group(1)), int(m.group(2))
        if a > 12:
            day_first += 1
        elif b > 12:
            month_first += 1
    if day_first and not month_first:
        return True
    if month_first and not day_first:
        return False
    return None


# --------------------------------------------------------------------------
# Outcomes — matched strictly, never fuzzily
# --------------------------------------------------------------------------

OUTCOME_MAP: dict[str, EventType | None] = {}
for words, ev in [
    (["no response", "no reply", "ghosted", "ghost", "silence", "nothing",
      "no answer", "pending", "waiting", "applied", "submitted", "open"], None),
    (["rejected", "rejection", "declined", "turned down", "not selected",
      "unsuccessful", "auto rejected", "auto-rejected", "no thanks"],
     EventType.REJECTION_RECEIVED),
    (["recruiter call", "recruiter screen", "phone screen", "screened",
      "screening", "hr call", "first call"], EventType.RECRUITER_CONTACT),
    (["assessment", "take home", "coding test", "test"], EventType.ASSESSMENT_SENT),
    (["interviewed", "interview", "interviewing", "tech interview"],
     EventType.INTERVIEW_COMPLETED),
    (["final round", "final", "onsite", "on-site", "panel", "last round"],
     EventType.FINAL_ROUND),
    (["offer", "offered", "got offer", "hired", "accepted"], EventType.OFFER),
    (["withdrew", "withdrawn", "i withdrew", "pulled out", "declined by me"],
     EventType.WITHDRAWN),
]:
    for w in words:
        OUTCOME_MAP[w] = ev


def match_outcome(raw: str) -> tuple[EventType | None, bool]:
    """Return (event, recognised). Strict: normalised exact match only.

    Fuzzy matching is refused here on purpose. Mapping "maybe" onto OFFER
    would silently corrupt the funnel, and the funnel is the product.
    """
    n = _norm(raw)
    if not n:
        return None, False
    if n in OUTCOME_MAP:
        return OUTCOME_MAP[n], True
    # allow a leading qualifier: "auto rejected by ats" -> rejected
    for key, ev in OUTCOME_MAP.items():
        if n.startswith(key + " ") or n.endswith(" " + key):
            return ev, True
    return None, False


# --------------------------------------------------------------------------
# Ingest
# --------------------------------------------------------------------------

@dataclass
class RowIssue:
    row: int
    column: str
    problem: str
    severity: str        # error (row dropped) | warning (row kept, degraded)


@dataclass
class IngestReport:
    rows_total: int = 0
    rows_accepted: int = 0
    columns: dict[str, ColumnMatch] = field(default_factory=dict)
    unmatched_columns: list[str] = field(default_factory=list)
    missing_required: list[str] = field(default_factory=list)
    day_first: bool | None = None
    issues: list[RowIssue] = field(default_factory=list)
    parsed: list[dict] = field(default_factory=list)
    version: str = INGEST_VERSION

    @property
    def ok(self) -> bool:
        return self.rows_accepted > 0 and not self.missing_required

    def summary(self) -> str:
        errs = sum(1 for i in self.issues if i.severity == "error")
        return (f"{self.rows_accepted}/{self.rows_total} rows accepted · "
                f"{errs} dropped · {len(self.columns)} columns recognised")


EVENT_COLUMNS = [
    ("first_response_date", EventType.RECRUITER_CONTACT),
    ("screen_date", EventType.SCREEN_COMPLETED),
    ("interview_date", EventType.INTERVIEW_COMPLETED),
    ("final_round_date", EventType.FINAL_ROUND),
    ("offer_date", EventType.OFFER),
    ("rejection_date", EventType.REJECTION_RECEIVED),
]


# --------------------------------------------------------------------------
# Plausibility — well-formed values that are nevertheless wrong
# --------------------------------------------------------------------------

# A date can parse perfectly and still be nonsense. "2052-03-14" is a valid
# date and a fat-fingered year; a rejection dated before its application is
# internally consistent and impossible. Field validation cannot catch either,
# because nothing about the FIELD is wrong — the problem is the relationship
# between fields, or between the value and reality.
#
# This matters more here than in most products. Everything downstream is a
# statistic, and a single application dated 2052 shifts the median time-to-reply
# and can invent a "pattern" out of one typo. A system whose entire claim is
# that it won't overclaim has to defend its own inputs.

MAX_FUTURE_DAYS = 14        # applied "tomorrow" is fine; next year is a typo
MAX_HISTORY_YEARS = 15      # older than this is almost certainly a wrong year


def sanity_check(row: dict, today: date | None = None) -> list[RowIssue]:
    """Relationship and bounds checks, run after parsing, before analysis.

    Returns warnings and errors. An error drops the row; a warning keeps it and
    tells the user, because most implausible values are still usable and
    silently discarding someone's data is worse than flagging it.
    """
    today = today or date.today()
    out: list[RowIssue] = []
    n = row.get("row", 0)
    applied = row.get("applied_at")

    if isinstance(applied, date):
        if applied > today + timedelta(days=MAX_FUTURE_DAYS):
            out.append(RowIssue(n, "applied_date",
                             f"dated {applied.isoformat()}, which is in the future — "
                             f"probably a wrong year", "error"))
        elif applied < today - timedelta(days=365 * MAX_HISTORY_YEARS):
            out.append(RowIssue(n, "applied_date",
                             f"dated {applied.isoformat()}, over {MAX_HISTORY_YEARS} "
                             f"years ago — probably a wrong year", "error"))

    # Event ordering. A rejection before the application is impossible, and it
    # would produce a negative days-to-response that skews the median.
    events = row.get("events") or []
    for kind, when in events:
        if not isinstance(when, date) or not isinstance(applied, date):
            continue
        if kind.value == "submitted":
            continue
        if when < applied:
            out.append(RowIssue(n, "dates",
                             f"{kind.value.replace('_', ' ')} dated "
                             f"{when.isoformat()}, before the application on "
                             f"{applied.isoformat()}", "error"))
        elif when > today + timedelta(days=MAX_FUTURE_DAYS):
            out.append(RowIssue(n, "dates",
                             f"{kind.value.replace('_', ' ')} is dated in the future",
                             "warning"))

    # Events out of order with each other — interviewed before first contact.
    #
    # Only PROGRESSION events are comparable. Terminal events like a rejection
    # legitimately come after everything, and including them (at an implicit
    # rank of 0) made a correctly-ordered row look inverted: a rejection dated
    # after a recruiter call was flagged as the call being out of order.
    ranked = {"recruiter_contact": 1, "screen_completed": 2, "interview_scheduled": 3,
              "interview_completed": 4, "final_round": 5, "offer": 6}
    dated = [(k, w) for k, w in events
             if isinstance(w, date) and k.value in ranked]
    seq = sorted(((ranked[k.value], w, k) for k, w in dated), key=lambda x: x[0])
    for i in range(1, len(seq)):
        if seq[i][0] > seq[i - 1][0] and seq[i][1] < seq[i - 1][1]:
            out.append(RowIssue(n, "dates",
                             f"{seq[i][2].value.replace('_', ' ')} is dated before "
                             f"{seq[i - 1][2].value.replace('_', ' ')}", "warning"))
            break

    if len((row.get("company") or "")) > 120:
        out.append(RowIssue(n, "company", "company name is implausibly long — "
                                       "columns may be misaligned", "warning"))
    return out


def ingest_csv(text: str, today: date | None = None) -> IngestReport:
    """Parse and validate. Never raises on user data — reports instead."""
    today = today or date.today()
    rep = IngestReport()

    text = text.lstrip("\ufeff")
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(io.StringIO(text), dialect=dialect)

    headers = [h for h in (reader.fieldnames or []) if h and h.strip()]
    rep.columns, rep.unmatched_columns = match_columns(headers)
    rep.missing_required = [c for c in REQUIRED if c not in rep.columns]
    if rep.missing_required:
        for c in rep.missing_required:
            rep.issues.append(RowIssue(
                0, c,
                f"no column matched '{c}'. Found: {', '.join(headers[:8]) or 'nothing'}",
                "error"))
        return rep

    for canonical, cm in rep.columns.items():
        if cm.method == "fuzzy":
            rep.issues.append(RowIssue(
                0, canonical,
                f"read column '{cm.header}' as {canonical} "
                f"({cm.confidence:.0%} match) — check this is right",
                "warning"))

    rows = list(reader)

    # Settle DD/MM vs MM/DD once for the whole file.
    date_col = rep.columns["applied_date"].header
    rep.day_first = infer_day_first([(r.get(date_col) or "") for r in rows])

    def cell(row: dict, canonical: str) -> str:
        cm = rep.columns.get(canonical)
        return (row.get(cm.header) or "").strip() if cm else ""

    for n, row in enumerate(rows, start=2):
        if not any((v or "").strip() for v in row.values()):
            continue                                   # blank spacer row
        rep.rows_total += 1
        errors: list[RowIssue] = []

        company = cell(row, "company")
        if not company:
            errors.append(RowIssue(n, "company", "empty", "error"))

        dr = parse_date(cell(row, "applied_date"), today, rep.day_first)
        if dr.value is None:
            errors.append(RowIssue(n, "applied_date",
                                   dr.note or "unreadable date", "error"))
        elif dr.ambiguous:
            rep.issues.append(RowIssue(n, "applied_date", dr.note, "warning"))

        raw_outcome = cell(row, "outcome")
        event, recognised = match_outcome(raw_outcome)
        if not recognised:
            errors.append(RowIssue(
                n, "outcome",
                f"'{raw_outcome}' not recognised — try: no response, rejected, "
                f"recruiter call, interviewed, final round, offer, withdrew",
                "error"))

        rep.issues.extend(errors)
        if any(e.severity == "error" for e in errors):
            continue

        applied = dr.value
        events: list[tuple[EventType, date]] = [(EventType.SUBMITTED, applied)]
        for col, etype in EVENT_COLUMNS:
            d = parse_date(cell(row, col), today, rep.day_first).value
            if d:
                if d < applied:
                    rep.issues.append(RowIssue(
                        n, col, "date precedes the application date — ignored",
                        "warning"))
                    continue
                events.append((etype, d))

        imputed = False
        if event and not any(e[0] == event for e in events):
            events.append((event, applied))
            imputed = True

        jd = cell(row, "job_description")
        if not jd:
            rep.issues.append(RowIssue(
                n, "job_description",
                "no JD — this row can't contribute to skill-gap analysis",
                "warning"))

        candidate = {
            "row": n,
            "company": company,
            "job_title": cell(row, "job_title") or "(untitled)",
            "applied_at": applied,
            "events": events,
            "date_imputed": imputed,
            "jd_text": jd,
            "resume_version": cell(row, "resume_version") or "default",
            "source": _norm(cell(row, "source")) or "unknown",
            "rejection_text": cell(row, "rejection_text"),
            "location": cell(row, "location"),
        }

        # Relationship and bounds checks. These compare fields against each
        # other, so they can only run once the row is assembled.
        problems = sanity_check(candidate)
        rep.issues.extend(problems)
        if any(p.severity == "error" for p in problems):
            continue

        rep.parsed.append(candidate)
        rep.rows_accepted += 1

    return rep


TEMPLATE_HEADER = ",".join(REQUIRED + [
    "job_description", "resume_version", "source", "first_response_date",
    "screen_date", "interview_date", "final_round_date", "offer_date",
    "rejection_date", "rejection_text", "salary_posted", "location",
])

TEMPLATE_EXAMPLE = (
    TEMPLATE_HEADER + "\n"
    "Acme Corp,Senior Product Manager,2026-03-14,rejected,"
    "\"We're looking for a PM with 5+ years and strong SQL...\","
    "Resume A,linkedin,2026-03-20,2026-03-25,,,,2026-04-02,"
    "\"Thanks for interviewing. We've decided to move forward with a candidate "
    "who has deeper analytics experience.\",,Remote\n"
)
