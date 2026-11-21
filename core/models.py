"""
RejectionIntel — core data model.

DESIGN NOTES (read these before changing anything):

1. An application does NOT have a `status` column. It has a stream of
   ApplicationEvents. Status is *derived*. This matters because the single
   most common outcome in a job search is a non-event (ghosting), and you
   cannot represent "nothing happened for 45 days" as a status enum without
   lying about when it happened.

2. Resume is a first-class variable on Application, not a user attribute.
   If you ever want to compare resume versions, the join has to already exist.

3. Every derived number carries `algo_version`. Scores get recomputed as the
   algorithm changes; without a version column you can't tell a score change
   from a candidate change.

4. Insight rows store `n`, `effect`, `p_value`, `q_value` and `confidence_tier`
   alongside the text. The confidence tier is computed once, in the stats
   layer, and persisted. It is NOT a rendering decision, because rendering
   decisions get quietly dropped.
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    JSON, Boolean, Date, DateTime, Enum, Float, ForeignKey, Integer,
    String, Text, UniqueConstraint, func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from .enums import (
    ConfidenceTier, EventType, RequirementLevel, Resolution, Seniority, Stage,
)


class Base(DeclarativeBase):
    pass


# --------------------------------------------------------------------------
# Identity
# --------------------------------------------------------------------------

class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    # Self-reported search parameters. Kept on the user, not the application,
    # because in the MVP the CSV won't carry per-application salary asks.
    # Rotating this invalidates every API token for the user without touching
    # the global signing key (which would sign out everyone, everywhere).
    api_nonce: Mapped[str | None] = mapped_column(String(32))

    # Rotating this invalidates every browser session for this user without
    # touching the global signing key. That is what "sign out everywhere"
    # needs: a 30-day cookie you cannot revoke is a 30-day window for anyone
    # who walked away from a shared machine.
    session_nonce: Mapped[str | None] = mapped_column(String(32))

    # Password sign-in. Nullable because the original and still-supported way
    # in is a magic link, and an OAuth account never sets one. A NULL here
    # means "this account has no password", not "empty password".
    password_hash: Mapped[str | None] = mapped_column(String(255))
    password_set_at: Mapped[datetime | None] = mapped_column(DateTime)

    # Which provider vouched for this address, and their stable id for the
    # user. The id is stored because an email address can be reassigned at a
    # company domain; the subject cannot.
    oauth_provider: Mapped[str | None] = mapped_column(String(20))
    oauth_subject: Mapped[str | None] = mapped_column(String(64))

    target_titles: Mapped[list | None] = mapped_column(JSON, default=list)
    salary_expectation_min: Mapped[int | None] = mapped_column(Integer)
    salary_currency: Mapped[str | None] = mapped_column(String(3))
    location: Mapped[str | None] = mapped_column(String(120))
    work_authorization: Mapped[str | None] = mapped_column(String(60))

    resumes: Mapped[list[Resume]] = relationship(back_populates="user")
    applications: Mapped[list[Application]] = relationship(back_populates="user")


# --------------------------------------------------------------------------
# Resumes
# --------------------------------------------------------------------------

class Resume(Base):
    __tablename__ = "resumes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)

    label: Mapped[str] = mapped_column(String(80))          # "Resume A", "PM-focused v2"
    raw_text: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64), index=True)

    # Deterministic extraction output (dates, titles, computed tenure).
    parsed: Mapped[dict | None] = mapped_column(JSON)
    total_experience_months: Mapped[int | None] = mapped_column(Integer)
    seniority: Mapped[Seniority | None] = mapped_column(Enum(Seniority))
    parser_version: Mapped[str] = mapped_column(String(20), default="0.1.0")

    source_format: Mapped[str] = mapped_column(String(10), default="txt")   # pdf|docx|txt
    original_filename: Mapped[str | None] = mapped_column(String(255))
    parse_warnings: Mapped[list | None] = mapped_column(JSON)

    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    user: Mapped[User] = relationship(back_populates="resumes")
    skills: Mapped[list[ResumeSkill]] = relationship(back_populates="resume")


class Skill(Base):
    """Canonical skill vocabulary. Aliases live in `aliases` so that
    'postgres', 'PostgreSQL' and 'psql' collapse to one row."""
    __tablename__ = "skills"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    canonical: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    aliases: Mapped[list] = mapped_column(JSON, default=list)
    category: Mapped[str | None] = mapped_column(String(40))  # language / tool / domain


class ResumeSkill(Base):
    __tablename__ = "resume_skills"
    __table_args__ = (UniqueConstraint("resume_id", "skill_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    resume_id: Mapped[int] = mapped_column(ForeignKey("resumes.id"), index=True)
    skill_id: Mapped[int] = mapped_column(ForeignKey("skills.id"), index=True)

    mention_count: Mapped[int] = mapped_column(Integer, default=1)
    # Where it appears matters: a skill in a bullet with a metric reads very
    # differently to a screener than a skill in a comma-separated skills blob.
    in_bullet: Mapped[bool] = mapped_column(Boolean, default=False)
    in_skills_section_only: Mapped[bool] = mapped_column(Boolean, default=False)
    most_recent_year: Mapped[int | None] = mapped_column(Integer)

    resume: Mapped[Resume] = relationship(back_populates="skills")


# --------------------------------------------------------------------------
# Jobs
# --------------------------------------------------------------------------

class Company(Base):
    __tablename__ = "companies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200), index=True)
    normalized_name: Mapped[str] = mapped_column(String(200), index=True)
    industry: Mapped[str | None] = mapped_column(String(60))
    size_bucket: Mapped[str | None] = mapped_column(String(20))  # startup/mid/enterprise


class Job(Base):
    __tablename__ = "jobs"
    # One posting per company per JD text — not one globally.
    __table_args__ = (UniqueConstraint("company_id", "jd_hash"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    company_id: Mapped[int | None] = mapped_column(ForeignKey("companies.id"), index=True)

    title_raw: Mapped[str] = mapped_column(String(200))
    title_normalized: Mapped[str | None] = mapped_column(String(200), index=True)
    jd_text: Mapped[str | None] = mapped_column(Text)
    jd_hash: Mapped[str | None] = mapped_column(String(64), index=True)

    # Deterministic
    seniority: Mapped[Seniority | None] = mapped_column(Enum(Seniority))
    min_years_experience: Mapped[int | None] = mapped_column(Integer)

    # LLM-extracted, cached by jd_hash so a re-run costs nothing
    llm_extract: Mapped[dict | None] = mapped_column(JSON)
    llm_extract_version: Mapped[str | None] = mapped_column(String(20))
    llm_extract_model: Mapped[str | None] = mapped_column(String(60))
    extracted_at: Mapped[datetime | None] = mapped_column(DateTime)

    function: Mapped[str | None] = mapped_column(String(60))     # PM / eng / design
    industry: Mapped[str | None] = mapped_column(String(60))
    remote_policy: Mapped[str | None] = mapped_column(String(20))
    posted_at: Mapped[date | None] = mapped_column(Date)

    company: Mapped[Company | None] = relationship()
    requirements: Mapped[list[JobSkill]] = relationship(back_populates="job")


class JobSkill(Base):
    __tablename__ = "job_skills"
    __table_args__ = (UniqueConstraint("job_id", "skill_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"), index=True)
    skill_id: Mapped[int] = mapped_column(ForeignKey("skills.id"), index=True)
    level: Mapped[RequirementLevel] = mapped_column(Enum(RequirementLevel))
    # Verbatim span from the JD. Needed so the UI can show *why* we think
    # this is required rather than asking the user to trust us.
    evidence_span: Mapped[str | None] = mapped_column(String(400))

    job: Mapped[Job] = relationship(back_populates="requirements")


# --------------------------------------------------------------------------
# Applications & events
# --------------------------------------------------------------------------

class Application(Base):
    __tablename__ = "applications"
    __table_args__ = (UniqueConstraint("user_id", "row_fingerprint"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"), index=True)
    resume_id: Mapped[int | None] = mapped_column(ForeignKey("resumes.id"), index=True)

    applied_at: Mapped[date] = mapped_column(Date, index=True)
    source: Mapped[str | None] = mapped_column(String(40))     # linkedin/referral/direct
    referral: Mapped[bool] = mapped_column(Boolean, default=False)
    cover_letter: Mapped[bool] = mapped_column(Boolean, default=False)

    # Identity of a row ACROSS uploads. sha256(company|title|applied_date).
    # With the unique constraint below, re-uploading a superset CSV — the way
    # people actually use a spreadsheet — becomes idempotent instead of
    # producing 88 duplicates.
    row_fingerprint: Mapped[str] = mapped_column(String(64), index=True)
    # The version label as written in the CSV ("Resume A"). Distinct from
    # resume_id, which points at an uploaded file — most people tag a version
    # in their spreadsheet without ever uploading both files.
    resume_label: Mapped[str | None] = mapped_column(String(80))
    ingest_batch_id: Mapped[str | None] = mapped_column(String(32), index=True)

    # --- derived, written by the pipeline ---
    scored_at: Mapped[datetime | None] = mapped_column(DateTime)
    scored_algo_version: Mapped[str | None] = mapped_column(String(20))
    furthest_stage: Mapped[Stage | None] = mapped_column(Enum(Stage))
    resolution: Mapped[Resolution | None] = mapped_column(Enum(Resolution))
    resolved_at: Mapped[date | None] = mapped_column(Date)
    days_to_first_response: Mapped[int | None] = mapped_column(Integer)
    derivation_version: Mapped[str | None] = mapped_column(String(20))

    user: Mapped[User] = relationship(back_populates="applications")
    job: Mapped[Job] = relationship()
    resume: Mapped[Resume | None] = relationship()
    events: Mapped[list[ApplicationEvent]] = relationship(
        back_populates="application", order_by="ApplicationEvent.occurred_at"
    )
    fit_score: Mapped[FitScore | None] = relationship(
        back_populates="application", uselist=False
    )


class ApplicationEvent(Base):
    __tablename__ = "application_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    application_id: Mapped[int] = mapped_column(ForeignKey("applications.id"), index=True)

    event_type: Mapped[EventType] = mapped_column(Enum(EventType))
    occurred_at: Mapped[date] = mapped_column(Date)

    # If the employer wrote a reason, it lives here verbatim. This is the ONLY
    # source that can ever produce a ConfidenceTier.OBSERVED insight.
    evidence_text: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(20), default="csv")  # csv | email | manual

    application: Mapped[Application] = relationship(back_populates="events")


# --------------------------------------------------------------------------
# Derived scores & insights
# --------------------------------------------------------------------------

class FitScore(Base):
    __tablename__ = "fit_scores"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    application_id: Mapped[int] = mapped_column(
        ForeignKey("applications.id"), index=True, unique=True
    )

    overall: Mapped[float] = mapped_column(Float)
    skills_required: Mapped[float] = mapped_column(Float)
    skills_preferred: Mapped[float] = mapped_column(Float)
    experience: Mapped[float] = mapped_column(Float)
    seniority: Mapped[float] = mapped_column(Float)
    industry: Mapped[float] = mapped_column(Float)

    # Human-readable trace: which skills matched, which didn't, what the
    # seniority delta was. Feeds both the UI and the LLM narration payload.
    breakdown: Mapped[dict] = mapped_column(JSON)
    algo_version: Mapped[str] = mapped_column(String(20))
    computed_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    application: Mapped[Application] = relationship(back_populates="fit_score")


class RejectionEvidence(Base):
    """Explicit employer statements — the only source for an OBSERVED finding.

    `verbatim_span` is non-negotiable. The moment a reason is paraphrased it
    becomes an inference wearing an observation's badge, and the whole tier
    system stops meaning anything.
    """
    __tablename__ = "rejection_evidence"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    application_id: Mapped[int] = mapped_column(ForeignKey("applications.id"), index=True)

    verbatim_span: Mapped[str] = mapped_column(String(500))
    skill_id: Mapped[int | None] = mapped_column(ForeignKey("skills.id"))
    category: Mapped[str] = mapped_column(String(40))
    # Most rejection emails say nothing usable. Separating boilerplate from
    # substance is what stops the OBSERVED tier filling up with noise.
    is_actionable: Mapped[bool] = mapped_column(Boolean, default=False)
    classifier_version: Mapped[str] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    application: Mapped[Application] = relationship()


class ActionItem(Base):
    """A change the user committed to. Ticking one is the whole retention loop.

    Keyed by a stable string rather than a row id, so an item survives
    re-analysis: the same recommendation next week is the same item, still
    ticked, rather than a fresh unticked duplicate.
    """
    __tablename__ = "action_items"
    __table_args__ = (UniqueConstraint("user_id", "action_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    action_key: Mapped[str] = mapped_column(String(120), index=True)
    title: Mapped[str] = mapped_column(String(300))
    source: Mapped[str] = mapped_column(String(20))
    done: Mapped[bool] = mapped_column(Boolean, default=False)
    done_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class Snapshot(Base):
    """One analysis run's headline numbers, kept so the next run can compare.

    Without this there is no "since last time" — and "is it working yet?" is
    the only question that brings someone back on a Thursday.
    """
    __tablename__ = "snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    n_analysed: Mapped[int] = mapped_column(Integer)
    response_rate: Mapped[float] = mapped_column(Float)
    bottleneck_stage: Mapped[str | None] = mapped_column(String(20))
    actions_done: Mapped[list | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class JDExtraction(Base):
    """Cached parse of one job-description text, keyed on its content hash.

    WHY THIS IS SEPARATE FROM Job
    -----------------------------
    Two things were sharing one table and they are not the same thing:

      * "what does this text require"  — a property of the TEXT. Global, shared
        across every company, correct to cache once forever.
      * "this company posted this role" — a property of the COMPANY. Two
        employers posting identical boilerplate are two postings.

    Keeping both on Job forced jd_hash to be globally unique, which silently
    attributed every application to whichever company happened to post that
    text first. With boilerplate adverts that is common, not exotic.
    """
    __tablename__ = "jd_extractions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    jd_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    payload: Mapped[dict] = mapped_column(JSON)
    model: Mapped[str | None] = mapped_column(String(80))
    version: Mapped[str | None] = mapped_column(String(20))
    extracted_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class Cohort(Base):
    """A group whose outcomes are read together — a bootcamp batch, a
    university year, an outplacement client."""
    __tablename__ = "cohorts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    join_code: Mapped[str] = mapped_column(String(24), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class CohortMember(Base):
    """Membership is explicit and opt-in. Nobody's outcomes join a group report
    because an administrator added their email — they join by using a code."""
    __tablename__ = "cohort_members"
    __table_args__ = (UniqueConstraint("cohort_id", "user_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    cohort_id: Mapped[int] = mapped_column(ForeignKey("cohorts.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    joined_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class JobRun(Base):
    """A background analysis. Persisted so a refresh mid-run reconnects."""
    __tablename__ = "job_runs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), index=True)
    state: Mapped[str] = mapped_column(String(12), default="queued")
    steps: Mapped[list | None] = mapped_column(JSON)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    ended_at: Mapped[datetime | None] = mapped_column(DateTime)


class AnalysisRun(Base):
    """One diagnostic pass over a user's data. Immutable snapshot."""
    __tablename__ = "analysis_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)

    n_applications: Mapped[int] = mapped_column(Integer)
    n_resolved: Mapped[int] = mapped_column(Integer)
    stats_version: Mapped[str] = mapped_column(String(20))
    fdr_alpha: Mapped[float] = mapped_column(Float, default=0.10)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    insights: Mapped[list[Insight]] = relationship(back_populates="run")


class Insight(Base):
    __tablename__ = "insights"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("analysis_runs.id"), index=True)

    kind: Mapped[str] = mapped_column(String(40))       # fit_gradient / skill_gap / ...
    dimension: Mapped[str] = mapped_column(String(120)) # "requires:sql"

    # The numbers. The LLM is allowed to read these and nothing else.
    n_exposed: Mapped[int] = mapped_column(Integer)
    n_unexposed: Mapped[int] = mapped_column(Integer)
    rate_exposed: Mapped[float] = mapped_column(Float)
    rate_unexposed: Mapped[float] = mapped_column(Float)
    ci_low: Mapped[float | None] = mapped_column(Float)
    ci_high: Mapped[float | None] = mapped_column(Float)
    effect: Mapped[float | None] = mapped_column(Float)     # risk ratio
    p_value: Mapped[float | None] = mapped_column(Float)
    q_value: Mapped[float | None] = mapped_column(Float)    # BH-adjusted

    confidence_tier: Mapped[ConfidenceTier] = mapped_column(Enum(ConfidenceTier))

    # LLM output, generated last, validated against the numbers above.
    headline: Mapped[str | None] = mapped_column(String(300))
    explanation: Mapped[str | None] = mapped_column(Text)
    recommendation: Mapped[str | None] = mapped_column(Text)
    narration_model: Mapped[str | None] = mapped_column(String(60))

    run: Mapped[AnalysisRun] = relationship(back_populates="insights")


# --------------------------------------------------------------------------
# Auth hardening
# --------------------------------------------------------------------------

class UsedMagicToken(Base):
    """Spent sign-in links.

    Replay protection used to live in a process-local set, which meant it
    reset on every restart and, worse, was not shared between workers — under
    RQ or any multi-process deploy the same link would work once per process.
    A magic link sits in a mailbox forever, so this has to be durable.

    The nonce is the primary key, so a second use is an integrity error rather
    than a read-then-write race.
    """

    __tablename__ = "used_magic_tokens"

    nonce: Mapped[str] = mapped_column(String(32), primary_key=True)
    used_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class RateLimitHit(Base):
    """One row per attempt, counted inside a moving window.

    Rows rather than a counter because a counter needs a window reset, and a
    reset is a gap an attacker can align with. Old rows are pruned on write.
    """

    __tablename__ = "rate_limit_hits"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    bucket: Mapped[str] = mapped_column(String(200), index=True)
    at: Mapped[datetime] = mapped_column(DateTime, index=True,
                                         server_default=func.now())
