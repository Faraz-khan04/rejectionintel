"""
Domain enums. Deliberately free of any ORM / framework import so that the
analytical core (deterministic.py, fit_score.py, stats.py) can be imported
and unit-tested with zero infrastructure.
"""

from __future__ import annotations

import enum


class Seniority(str, enum.Enum):
    """Ordered. Use .rank for arithmetic."""
    INTERN = "intern"
    JUNIOR = "junior"
    MID = "mid"
    SENIOR = "senior"
    STAFF = "staff"
    PRINCIPAL = "principal"
    DIRECTOR = "director"
    VP_PLUS = "vp_plus"

    @property
    def rank(self) -> int:
        return list(Seniority).index(self)


class EventType(str, enum.Enum):
    """Observable things. Note there is no 'rejected_by_ats' — we cannot
    observe that, we can only observe a fast automated rejection."""
    SUBMITTED = "submitted"
    ACKNOWLEDGED = "acknowledged"          # auto "we received your application"
    RECRUITER_CONTACT = "recruiter_contact"
    SCREEN_SCHEDULED = "screen_scheduled"
    SCREEN_COMPLETED = "screen_completed"
    ASSESSMENT_SENT = "assessment_sent"
    INTERVIEW_SCHEDULED = "interview_scheduled"
    INTERVIEW_COMPLETED = "interview_completed"
    FINAL_ROUND = "final_round"
    OFFER = "offer"
    REJECTION_RECEIVED = "rejection_received"
    WITHDRAWN = "withdrawn"


class Stage(str, enum.Enum):
    """Derived funnel stage. Ordered."""
    SUBMITTED = "submitted"
    SCREEN = "screen"
    INTERVIEW = "interview"
    FINAL = "final"
    OFFER = "offer"

    @property
    def rank(self) -> int:
        return list(Stage).index(self)


class Resolution(str, enum.Enum):
    REJECTED = "rejected"        # explicit rejection message received
    GHOSTED = "ghosted"          # no terminal event, past the silence threshold
    OPEN = "open"                # still plausibly live
    WITHDRAWN = "withdrawn"      # candidate pulled out — EXCLUDE from stats
    OFFER = "offer"


class RequirementLevel(str, enum.Enum):
    REQUIRED = "required"
    PREFERRED = "preferred"


class ConfidenceTier(str, enum.Enum):
    OBSERVED = "observed"           # employer said it in writing
    STRONG = "strong"               # survives FDR correction + n gate
    WEAK = "weak"                   # directional only
    INSUFFICIENT = "insufficient"   # below n gate — must render as "unknown"


