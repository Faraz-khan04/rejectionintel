"""
Persistence operations.

Two things matter here, and neither is "save the upload":

1. `DbExtractionCache` gives the JD cache a home that survives a restart.
   Without it, warming on the free tier is Sisyphean — 20 extractions a day
   against 88 needed, reset to zero every time uvicorn reloads. With it,
   coverage completes over a few days and stays complete.

2. `upsert_applications` makes re-uploading a superset CSV idempotent, which
   is how people actually use a spreadsheet: export the whole sheet, upload
   the whole sheet, every time.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import date, datetime

from sqlalchemy import func, select

from .db import session_scope
from .extract_llm import EXTRACT_VERSION, ExtractedJob, jd_hash
from .models import (
    ActionItem, Application, ApplicationEvent, Company, JDExtraction, Job,
    Resume, Snapshot, User,
)


def fingerprint(company: str, title: str, applied: date) -> str:
    """Stable identity for an application row across uploads.

    Company and title are normalised because people retype them inconsistently
    between exports ("Acme Corp" vs "acme corp "). The date is not — two
    applications to the same role on different dates are genuinely different
    applications, and collapsing them would hide a real re-application.
    """
    key = f"{company.strip().lower()}|{title.strip().lower()}|{applied.isoformat()}"
    return hashlib.sha256(key.encode()).hexdigest()


# --------------------------------------------------------------------------
# Users
# --------------------------------------------------------------------------

def get_or_create_user(email: str) -> int:
    with session_scope() as s:
        user = s.scalar(select(User).where(User.email == email))
        if user is None:
            user = User(email=email)
            s.add(user)
            s.flush()
        return user.id


# --------------------------------------------------------------------------
# JD extraction cache
# --------------------------------------------------------------------------

class DbExtractionCache:
    """Drop-in replacement for ExtractionCache, backed by the jobs table.

    Same three methods, so core/analyze.py doesn't know the difference. Reads
    are batched into memory once per report — 88 individual SELECTs per upload
    would be slower than the extraction it's meant to save.
    """

    def __init__(self) -> None:
        self._store: dict[str, ExtractedJob] = {}
        self.hits = 0
        self.misses = 0
        self._loaded = False

    def load(self, hashes: set[str] | None = None) -> int:
        """Pull cached extractions into memory. One query, not N."""
        with session_scope() as s:
            stmt = select(JDExtraction.jd_hash, JDExtraction.payload).where(
                JDExtraction.version == EXTRACT_VERSION,
            )
            if hashes:
                stmt = stmt.where(JDExtraction.jd_hash.in_(list(hashes)))
            rows = s.execute(stmt).all()

        loaded = 0
        for h, blob in rows:
            if not h or not blob:
                continue
            try:
                self._store[h] = ExtractedJob.model_validate(blob)
                loaded += 1
            except Exception:  # noqa: BLE001
                # A row written by an older schema. Skipping means it gets
                # re-extracted rather than crashing the whole report.
                continue
        self._loaded = True
        return loaded

    def get(self, h: str) -> ExtractedJob | None:
        v = self._store.get(h)
        if v is not None:
            self.hits += 1
        else:
            self.misses += 1
        return v

    def put(self, h: str, v: ExtractedJob, model: str = "") -> None:
        """Cache the parse of this TEXT, in its own table.

        Previously this wrote a Job row with no company attached, which both
        polluted the postings table and forced jd_hash to be globally unique.
        """
        self._store[h] = v
        with session_scope() as s:
            row = s.scalar(select(JDExtraction).where(JDExtraction.jd_hash == h))
            if row is None:
                row = JDExtraction(jd_hash=h, payload=v.model_dump())
                s.add(row)
            row.payload = v.model_dump()
            row.version = EXTRACT_VERSION
            row.model = model or None
            row.extracted_at = datetime.utcnow()

    @property
    def hit_rate(self) -> float:
        total = self.hits + self.misses
        return self.hits / total if total else 0.0

    def stats(self) -> dict:
        with session_scope() as s:
            total = s.scalar(select(func.count()).select_from(JDExtraction)) or 0
        return {"in_memory": len(self._store), "persisted": total,
                "hit_rate": round(self.hit_rate, 3)}


# --------------------------------------------------------------------------
# Applications
# --------------------------------------------------------------------------

def upsert_applications(user_id: int, parsed_rows: list[dict],
                        resume_id: int | None = None) -> dict:
    """Insert new applications, update existing ones. Idempotent by fingerprint.

    Returns counts so the UI can say "12 new, 76 already known" rather than
    pretending a re-upload was a fresh analysis.
    """
    batch = uuid.uuid4().hex[:16]
    inserted = updated = 0

    with session_scope() as s:
        existing = {
            fp: app_id for fp, app_id in s.execute(
                select(Application.row_fingerprint, Application.id)
                .where(Application.user_id == user_id)
            ).all()
        }

        for row in parsed_rows:
            fp = fingerprint(row["company"], row["job_title"], row["applied_at"])

            if fp in existing:
                app = s.get(Application, existing[fp])
                # Outcomes change over time — a row that was "no response" last
                # week may be "interviewed" now. Replace the event stream and
                # invalidate the score so it recomputes.
                s.query(ApplicationEvent).filter(
                    ApplicationEvent.application_id == app.id).delete()
                app.scored_at = None
                app.ingest_batch_id = batch
                if row.get("resume_version"):
                    app.resume_label = row["resume_version"]
                updated += 1
            else:
                company = s.scalar(
                    select(Company).where(
                        Company.normalized_name == row["company"].strip().lower()))
                if company is None:
                    company = Company(name=row["company"],
                                      normalized_name=row["company"].strip().lower())
                    s.add(company)
                    s.flush()

                h = jd_hash(row["jd_text"]) if row["jd_text"].strip() else None

                # Scope the lookup to the COMPANY as well as the text hash.
                #
                # THE BUG THIS FIXES
                # ------------------
                # Matching on jd_hash alone means two different employers who
                # post identical text share one Job row — and since Job carries
                # company_id, every application to the second company is
                # silently attributed to the first. Boilerplate job ads are
                # near-identical across companies, so this is common rather
                # than exotic: a seeded test with 12 companies collapsed into
                # ONE job row holding 1,187 applications.
                #
                # It corrupts anything that groups by employer — the ghost
                # detector, the employer index, the applications table. The
                # extraction cache is still keyed on the hash alone, which is
                # correct: identical text does have identical requirements.
                job = s.scalar(
                    select(Job).where(Job.jd_hash == h,
                                      Job.company_id == company.id)
                ) if h else None
                if job is None:
                    job = Job(company_id=company.id, title_raw=row["job_title"], jd_hash=h,
                              jd_text=row["jd_text"] or None)
                    s.add(job)
                    s.flush()
                    job.jd_text = row["jd_text"] or None

                app = Application(
                    user_id=user_id, job_id=job.id, resume_id=resume_id,
                    applied_at=row["applied_at"], source=row.get("source"),
                    referral=row.get("source") == "referral",
                    row_fingerprint=fp, ingest_batch_id=batch,
                    resume_label=row.get("resume_version") or "default",
                )
                s.add(app)
                s.flush()
                inserted += 1

            for etype, when in row["events"]:
                s.add(ApplicationEvent(application_id=app.id, event_type=etype,
                                       occurred_at=when, source="csv",
                                       evidence_text=row.get("rejection_text") or None))

        total = s.scalar(select(func.count()).select_from(Application)
                         .where(Application.user_id == user_id)) or 0

    return {"batch_id": batch, "inserted": inserted, "updated": updated, "total": total}


def save_resume(user_id: int, text: str, meta: dict) -> int:
    """Store a resume, deduplicated by content hash.

    The same file uploaded twice is one resume regardless of filename — which
    is also what makes resume-version comparison possible later.
    """
    h = hashlib.sha256(text.encode()).hexdigest()
    with session_scope() as s:
        existing = s.scalar(select(Resume).where(
            Resume.user_id == user_id, Resume.content_hash == h))
        if existing:
            return existing.id
        n = s.scalar(select(func.count()).select_from(Resume)
                     .where(Resume.user_id == user_id)) or 0
        r = Resume(
            user_id=user_id,
            label=meta.get("filename") or f"Resume {chr(65 + n)}",
            raw_text=text, content_hash=h,
            source_format=meta.get("source", "txt"),
            original_filename=meta.get("filename"),
            parse_warnings=meta.get("warnings") or None,
            is_default=(n == 0),
        )
        s.add(r)
        s.flush()
        return r.id


def load_applications(user_id: int) -> list[dict]:
    """Read a user's full history back in ingest-row shape.

    Returning the same structure the CSV parser produces means the analysis
    pipeline is identical whether data came from an upload or the database.
    """
    with session_scope() as s:
        apps = s.scalars(
            select(Application).where(Application.user_id == user_id)
            .order_by(Application.applied_at)
        ).all()

        out = []
        for a in apps:
            job = s.get(Job, a.job_id)
            company = s.get(Company, job.company_id) if job and job.company_id else None
            events = s.scalars(select(ApplicationEvent).where(
                ApplicationEvent.application_id == a.id)).all()
            out.append({
                "row": a.id,
                "company": company.name if company else "(unknown)",
                "job_title": job.title_raw if job else "(unknown)",
                "applied_at": a.applied_at,
                "events": [(e.event_type, e.occurred_at) for e in events],
                "date_imputed": False,
                "jd_text": (job.jd_text if job else "") or "",
                "resume_version": a.resume_label or "default",
                "source": a.source or "unknown",
                "rejection_text": next(
                    (e.evidence_text for e in events if e.evidence_text), ""),
                "location": "",
            })
        return out


# --------------------------------------------------------------------------
# Action plan
# --------------------------------------------------------------------------

def sync_actions(user_id: int, actions: list) -> list[dict]:
    """Upsert the current plan and return it with saved done-state applied.

    The analysis regenerates recommendations on every run. Without this the
    user's ticks would vanish each time — which would make the whole loop
    pointless, since the ticks are what we measure the next run against.
    """
    with session_scope() as s:
        existing = {
            a.action_key: a for a in s.scalars(
                select(ActionItem).where(ActionItem.user_id == user_id)).all()
        }
        out = []
        for a in actions:
            row = existing.get(a.key)
            if row is None:
                row = ActionItem(user_id=user_id, action_key=a.key, title=a.title,
                                 source=a.source, done=False)
                s.add(row)
                s.flush()
            else:
                row.title = a.title          # wording may improve between runs
            d = a.as_dict()
            d["done"] = bool(row.done)
            out.append(d)
        return out


def set_action(user_id: int, action_key: str, done: bool) -> dict:
    with session_scope() as s:
        row = s.scalar(select(ActionItem).where(
            ActionItem.user_id == user_id, ActionItem.action_key == action_key))
        if row is None:
            row = ActionItem(user_id=user_id, action_key=action_key,
                             title=action_key, source="unknown")
            s.add(row)
        row.done = done
        row.done_at = datetime.utcnow() if done else None
        return {"key": action_key, "done": done}


def done_action_keys(user_id: int) -> list[str]:
    with session_scope() as s:
        return [k for (k,) in s.execute(
            select(ActionItem.action_key).where(
                ActionItem.user_id == user_id, ActionItem.done.is_(True))).all()]


# --------------------------------------------------------------------------
# Snapshots
# --------------------------------------------------------------------------

def last_snapshot(user_id: int) -> dict | None:
    """The previous run — read BEFORE writing the current one."""
    with session_scope() as s:
        row = s.scalars(
            select(Snapshot).where(Snapshot.user_id == user_id)
            .order_by(Snapshot.created_at.desc()).limit(1)).first()
        if row is None:
            return None
        return {"response_rate": row.response_rate, "n_analysed": row.n_analysed,
                "bottleneck_stage": row.bottleneck_stage,
                "actions_done": row.actions_done or [],
                "created_at": row.created_at}


def save_snapshot(user_id: int, n_analysed: int, response_rate: float,
                  bottleneck_stage: str, actions_done: list[str]) -> None:
    with session_scope() as s:
        s.add(Snapshot(user_id=user_id, n_analysed=n_analysed,
                       response_rate=response_rate,
                       bottleneck_stage=bottleneck_stage,
                       actions_done=actions_done))
