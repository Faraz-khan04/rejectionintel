"""
CSV ingest. The MVP's entire input surface.

DESIGN CONSTRAINT: the user is filling this in by hand, probably from memory,
probably annoyed. Every required column is a chance for them to bounce. So:
four required columns, everything else optional, and validation that reports
ALL problems at once rather than dying on row 3.

The wide/event-based columns exist because dates-per-stage is what makes the
funnel real. But we degrade gracefully: given only `outcome`, we can still
derive a coarse stage.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from datetime import date, datetime

from .enums import EventType

REQUIRED = ["company", "job_title", "applied_date", "outcome"]

OPTIONAL = [
    "job_description",      # paste or URL — the single highest-value optional field
    "resume_version",       # enables version comparison later
    "source",               # linkedin / referral / direct / recruiter
    "first_response_date",
    "screen_date",
    "interview_date",
    "final_round_date",
    "offer_date",
    "rejection_date",
    "rejection_text",       # the ONLY path to an OBSERVED-tier insight
    "salary_posted",
    "location",
]

# Free-text outcome -> the event we can defend having observed.
OUTCOME_MAP = {
    "no response": None,
    "ghosted": None,
    "no reply": None,
    "rejected": EventType.REJECTION_RECEIVED,
    "rejection": EventType.REJECTION_RECEIVED,
    "auto-rejected": EventType.REJECTION_RECEIVED,
    "screened": EventType.SCREEN_COMPLETED,
    "phone screen": EventType.SCREEN_COMPLETED,
    "recruiter call": EventType.RECRUITER_CONTACT,
    "interviewed": EventType.INTERVIEW_COMPLETED,
    "interview": EventType.INTERVIEW_COMPLETED,
    "final round": EventType.FINAL_ROUND,
    "onsite": EventType.FINAL_ROUND,
    "offer": EventType.OFFER,
    "withdrew": EventType.WITHDRAWN,
    "withdrawn": EventType.WITHDRAWN,
}

DATE_FORMATS = ["%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y", "%b %d %Y", "%d %b %Y"]


@dataclass
class RowIssue:
    row: int
    column: str
    problem: str
    severity: str  # "error" blocks the row; "warning" degrades it


@dataclass
class IngestReport:
    rows_total: int = 0
    rows_accepted: int = 0
    issues: list[RowIssue] = field(default_factory=list)
    parsed: list[dict] = field(default_factory=list)

    @property
    def blocked(self) -> bool:
        return self.rows_accepted < 25

    def summary(self) -> str:
        errs = sum(1 for i in self.issues if i.severity == "error")
        warns = len(self.issues) - errs
        return (f"{self.rows_accepted}/{self.rows_total} rows accepted "
                f"({errs} errors, {warns} warnings)")


def parse_date(raw: str) -> date | None:
    raw = (raw or "").strip()
    if not raw:
        return None
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return None


def ingest_csv(text: str) -> IngestReport:
    """Parse and validate. Never raises on bad user data — reports instead."""
    report = IngestReport()
    reader = csv.DictReader(io.StringIO(text))

    headers = {(h or "").strip().lower() for h in (reader.fieldnames or [])}
    for col in REQUIRED:
        if col not in headers:
            report.issues.append(RowIssue(0, col, "required column missing", "error"))
    if any(i.severity == "error" for i in report.issues):
        return report

    for n, raw in enumerate(reader, start=2):
        row = {(k or "").strip().lower(): (v or "").strip() for k, v in raw.items()}
        report.rows_total += 1
        row_errors = []

        applied = parse_date(row.get("applied_date", ""))
        if not applied:
            row_errors.append(RowIssue(n, "applied_date", "unparseable date", "error"))

        outcome_key = row.get("outcome", "").lower()
        if outcome_key not in OUTCOME_MAP:
            row_errors.append(RowIssue(
                n, "outcome",
                f"'{outcome_key}' not recognised; expected one of {sorted(set(OUTCOME_MAP))[:5]}...",
                "error"))

        if not row.get("company"):
            row_errors.append(RowIssue(n, "company", "empty", "error"))

        if not row.get("job_description"):
            row_errors.append(RowIssue(
                n, "job_description",
                "no JD — this application can't contribute to skill-gap analysis",
                "warning"))

        report.issues.extend(row_errors)
        if any(e.severity == "error" for e in row_errors):
            continue

        events: list[tuple[EventType, date]] = [(EventType.SUBMITTED, applied)]
        for col, etype in [
            ("first_response_date", EventType.RECRUITER_CONTACT),
            ("screen_date", EventType.SCREEN_COMPLETED),
            ("interview_date", EventType.INTERVIEW_COMPLETED),
            ("final_round_date", EventType.FINAL_ROUND),
            ("offer_date", EventType.OFFER),
            ("rejection_date", EventType.REJECTION_RECEIVED),
        ]:
            d = parse_date(row.get(col, ""))
            if d:
                events.append((etype, d))

        # If they gave an outcome but no dates, synthesise a dateless terminal
        # event at the application date. Coarse, but honest: we mark it so the
        # time-to-response stats can exclude it.
        implied = OUTCOME_MAP[outcome_key]
        date_imputed = False
        if implied and not any(e[0] == implied for e in events):
            events.append((implied, applied))
            date_imputed = True

        report.parsed.append({
            "row": n,
            "company": row["company"],
            "job_title": row.get("job_title", ""),
            "applied_at": applied,
            "events": events,
            "date_imputed": date_imputed,
            "jd_text": row.get("job_description", ""),
            "resume_version": row.get("resume_version") or "default",
            "source": (row.get("source") or "unknown").lower(),
            "rejection_text": row.get("rejection_text", ""),
        })
        report.rows_accepted += 1

    return report


TEMPLATE_HEADER = ",".join(REQUIRED + OPTIONAL)

TEMPLATE_EXAMPLE = (
    TEMPLATE_HEADER + "\n"
    "Acme Corp,Senior Product Manager,2026-03-14,rejected,"
    "\"We're looking for a PM with 5+ years and strong SQL...\","
    "Resume A,linkedin,2026-03-20,2026-03-25,,,,2026-04-02,"
    "\"Thanks for interviewing. We've decided to move forward with a candidate "
    "who has deeper analytics experience.\",,Remote\n"
)
