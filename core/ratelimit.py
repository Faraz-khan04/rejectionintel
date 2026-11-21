"""
Rate limiting for the auth endpoints.

WHY THIS IS IN THE DATABASE
---------------------------
An in-memory counter is not a rate limit in this deployment. The service is
about to run under RQ with multiple worker processes (HANDOVER priority 3),
and a per-process dictionary means the real limit is `limit x workers`, which
is a number nobody chose. It also resets on restart, so the cheapest bypass is
to wait for a deploy.

WHAT IT PROTECTS
----------------
`/api/auth/request` sends an email to any address anyone types. Without a
limit that is: a free mail cannon pointed at strangers, a bill once Resend is
wired up, and a reliable way to get the sending domain blacklisted before the
first real user arrives.

WHAT IT DELIBERATELY DOES NOT DO
--------------------------------
It does not tell the caller which limit they hit, or how many attempts remain
for a given address. Both would leak whether an address is in use, which is
the same enumeration oracle HANDOVER 9.5 closed. The caller gets one number:
seconds to wait.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, func, select

from .db import session_scope
from .models import RateLimitHit

# Two windows on the same endpoint, because they stop different things. The
# per-address limit stops someone being mailed repeatedly; the per-IP limit
# stops one client walking a list of addresses.
EMAIL_LIMIT = 3
EMAIL_WINDOW = 15 * 60          # 3 links per address per 15 minutes
IP_LIMIT = 12
IP_WINDOW = 60 * 60             # 12 attempts per client per hour

PRUNE_AFTER = 24 * 60 * 60      # rows older than this are dead weight


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def check(bucket: str, limit: int, window: int) -> int:
    """Record an attempt. Returns 0 if allowed, else seconds to wait.

    The attempt is recorded whether or not it is allowed, so hammering the
    endpoint keeps the window full rather than letting it drain while the
    caller retries.
    """
    cutoff = _now() - timedelta(seconds=window)

    with session_scope() as s:
        s.execute(delete(RateLimitHit).where(
            RateLimitHit.at < _now() - timedelta(seconds=PRUNE_AFTER)))

        s.add(RateLimitHit(bucket=bucket, at=_now()))
        s.flush()

        used = s.execute(
            select(func.count()).select_from(RateLimitHit)
            .where(RateLimitHit.bucket == bucket, RateLimitHit.at >= cutoff)
        ).scalar_one()

        if used <= limit:
            return 0

        oldest = s.execute(
            select(func.min(RateLimitHit.at))
            .where(RateLimitHit.bucket == bucket, RateLimitHit.at >= cutoff)
        ).scalar_one()

    if oldest is None:
        return window
    wait = int((oldest + timedelta(seconds=window) - _now()).total_seconds())
    return max(wait, 1)


def check_sign_in(email: str, client_ip: str) -> int:
    """Both windows for a sign-in request. Returns seconds to wait, or 0."""
    wait = check(f"signin:ip:{client_ip}", IP_LIMIT, IP_WINDOW)
    email_wait = check(f"signin:email:{email.strip().lower()}",
                       EMAIL_LIMIT, EMAIL_WINDOW)
    return max(wait, email_wait)


def clear(bucket_prefix: str = "") -> int:
    """Drop hits. Used by tests, and by an operator unblocking someone."""
    with session_scope() as s:
        stmt = delete(RateLimitHit)
        if bucket_prefix:
            stmt = stmt.where(RateLimitHit.bucket.like(f"{bucket_prefix}%"))
        return s.execute(stmt).rowcount or 0
