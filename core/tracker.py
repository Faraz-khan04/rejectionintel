"""
The built-in tracker.

THE ARCHITECTURAL DECISION
--------------------------
A tracker writes to the SAME `applications` table the CSV importer writes to.

The obvious design is a separate `tracked_jobs` table with its own columns and
its own lifecycle. Do that and you get two sources of truth: jobs added by hand
never reach the analysis, the funnel disagrees with the board, and eventually
someone has to write a sync. Every product that ends up with a "why don't my
numbers match" support burden built it that way.

So a tracked job IS an application. Moving a card from Applied to Interview
appends an ApplicationEvent, exactly as a CSV row would. The analysis has no
idea the tracker exists, which is the point.

WHY STATUS IS DERIVED, NOT STORED
---------------------------------
The board shows a column per stage, but no column stores "which column am I
in". Position comes from the event stream via the same `derive_outcome` the
rest of the product uses. A stored status would drift from the events the
moment anything else touched the row.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import select

from .db import session_scope
from .enums import EventType, Stage
from .models import Application, ApplicationEvent, Company, Job
from .store import fingerprint

TRACKER_VERSION = "0.1.0"

# The board. Ordered left to right; each maps to the event that puts a card here.
COLUMNS: list[tuple[str, str, EventType | None]] = [
    ("applied", "Applied", None),
    ("screen", "Recruiter call", EventType.RECRUITER_CONTACT),
    ("interview", "Interview", EventType.INTERVIEW_COMPLETED),
    ("final", "Final round", EventType.FINAL_ROUND),
    ("offer", "Offer", EventType.OFFER),
    ("closed", "Closed", EventType.REJECTION_RECEIVED),
]

COLUMN_KEYS = [c[0] for c in COLUMNS]
_EVENT_FOR = {k: e for k, _, e in COLUMNS}


@dataclass
class Card:
    id: int
    company: str
    role: str
    applied_at: str
    column: str
    resume_label: str
    days_since: int
    has_jd: bool

    def as_dict(self) -> dict:
        return {"id": self.id, "company": self.company, "role": self.role,
                "applied_at": self.applied_at, "column": self.column,
                "resume_label": self.resume_label, "days_since": self.days_since,
                "has_jd": self.has_jd}


def _column_for(app: Application, events: list[ApplicationEvent]) -> str:
    """Which column this card belongs in, derived from its events.

    Closed wins over everything: a rejected application that reached a final
    round belongs in Closed, not Final round, because the board is about what
    is still live.
    """
    kinds = {e.event_type for e in events}
    if EventType.REJECTION_RECEIVED in kinds or EventType.WITHDRAWN in kinds:
        return "closed"
    if EventType.OFFER in kinds:
        return "offer"
    if EventType.FINAL_ROUND in kinds:
        return "final"
    if EventType.INTERVIEW_COMPLETED in kinds or EventType.INTERVIEW_SCHEDULED in kinds:
        return "interview"
    if EventType.RECRUITER_CONTACT in kinds or EventType.SCREEN_COMPLETED in kinds:
        return "screen"
    return "applied"


def board(user_id: int, today: date | None = None) -> dict:
    """Every application as a card, grouped by derived column."""
    today = today or date.today()
    with session_scope() as s:
        apps = s.scalars(
            select(Application).where(Application.user_id == user_id)
            .order_by(Application.applied_at.desc())).all()

        cards: dict[str, list[dict]] = {k: [] for k in COLUMN_KEYS}
        for a in apps:
            events = s.scalars(select(ApplicationEvent).where(
                ApplicationEvent.application_id == a.id)).all()
            job = s.get(Job, a.job_id)
            company = s.get(Company, job.company_id) if job and job.company_id else None
            col = _column_for(a, events)
            cards[col].append(Card(
                id=a.id,
                company=company.name if company else "(unknown)",
                role=job.title_raw if job else "(unknown)",
                applied_at=a.applied_at.isoformat(),
                column=col,
                resume_label=a.resume_label or "default",
                days_since=(today - a.applied_at).days,
                has_jd=bool(job and job.jd_text),
            ).as_dict())

        return {
            "columns": [{"key": k, "title": t, "count": len(cards[k])}
                        for k, t, _ in COLUMNS],
            "cards": cards,
            "total": len(apps),
            "version": TRACKER_VERSION,
        }


def add_job(user_id: int, company: str, role: str, applied_at: date,
            jd_text: str = "", resume_label: str = "default") -> dict:
    """Add one job by hand. Same shape a CSV row would produce.

    Deduplicated on the same fingerprint the importer uses, so adding a job
    here and later importing a spreadsheet that contains it does not create
    two of them.
    """
    from .extract_llm import jd_hash

    company, role = company.strip(), role.strip()
    if not company or not role:
        raise ValueError("Company and role are both needed.")

    fp = fingerprint(company, role, applied_at)
    with session_scope() as s:
        existing = s.scalar(select(Application).where(
            Application.user_id == user_id, Application.row_fingerprint == fp))
        if existing:
            return {"id": existing.id, "created": False, "duplicate": True}

        norm = company.lower()
        comp = s.scalar(select(Company).where(Company.normalized_name == norm))
        if comp is None:
            comp = Company(name=company, normalized_name=norm)
            s.add(comp)
            s.flush()

        h = jd_hash(jd_text) if jd_text.strip() else None
        job = s.scalar(select(Job).where(Job.jd_hash == h)) if h else None
        if job is None:
            job = Job(company_id=comp.id, title_raw=role, jd_hash=h,
                      jd_text=jd_text or None)
            s.add(job)
            s.flush()

        app = Application(user_id=user_id, job_id=job.id, applied_at=applied_at,
                          source="tracker", row_fingerprint=fp,
                          resume_label=resume_label or "default")
        s.add(app)
        s.flush()
        s.add(ApplicationEvent(application_id=app.id,
                               event_type=EventType.SUBMITTED,
                               occurred_at=applied_at, source="tracker"))
        return {"id": app.id, "created": True, "duplicate": False}


def move_card(user_id: int, app_id: int, column: str,
              when: date | None = None) -> dict:
    """Move a card by appending the event that stage implies.

    Moving BACKWARDS removes the events ahead of the target rather than adding
    a contradicting one — otherwise a mis-drag would leave an application that
    is simultaneously rejected and in a final round, and the funnel would count
    it twice.
    """
    if column not in COLUMN_KEYS:
        raise ValueError(f"Unknown column: {column}")
    when = when or date.today()

    with session_scope() as s:
        app = s.scalar(select(Application).where(
            Application.id == app_id, Application.user_id == user_id))
        if app is None:
            raise LookupError("No such application.")

        events = s.scalars(select(ApplicationEvent).where(
            ApplicationEvent.application_id == app_id)).all()
        target_index = COLUMN_KEYS.index(column)

        # Drop any event implying a later stage than the one we're moving to.
        for e in events:
            for key, _, ev in COLUMNS:
                if ev is not None and e.event_type == ev:
                    if COLUMN_KEYS.index(key) > target_index:
                        s.delete(e)

        wanted = _EVENT_FOR.get(column)
        if wanted is not None:
            already = any(e.event_type == wanted for e in events)
            if not already:
                s.add(ApplicationEvent(application_id=app_id, event_type=wanted,
                                       occurred_at=when, source="tracker"))

        # The score depends on outcome, so it has to be recomputed.
        app.scored_at = None
        return {"id": app_id, "column": column}


def delete_card(user_id: int, app_id: int) -> dict:
    with session_scope() as s:
        app = s.scalar(select(Application).where(
            Application.id == app_id, Application.user_id == user_id))
        if app is None:
            raise LookupError("No such application.")
        s.query(ApplicationEvent).filter(
            ApplicationEvent.application_id == app_id).delete()
        s.delete(app)
        return {"id": app_id, "deleted": True}


def stale_cards(user_id: int, days: int = 21, today: date | None = None) -> list[dict]:
    """Applications sitting untouched with no reply — the nudge worth showing.

    A tracker that only records is a spreadsheet with rounded corners. Telling
    someone which fourteen applications have gone quiet is the part they can't
    do themselves at a glance.
    """
    b = board(user_id, today)
    out = []
    for c in b["cards"]["applied"]:
        if c["days_since"] >= days:
            out.append(c)
    return sorted(out, key=lambda c: -c["days_since"])
