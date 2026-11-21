"""
Authentication — passwordless, cookie-based.

WHY NO PASSWORDS
----------------
Passwords mean a reset flow, a hashing choice, a breach surface, and a support
burden — for a product whose users show up, upload a CSV, and leave. A magic
link needs none of that and the security story is simpler to reason about.

WHY ANONYMOUS SESSIONS EXIST
----------------------------
Demo mode has to work on the first click. Forcing a sign-in before someone has
seen anything is the fastest way to lose them. So every visitor gets a real
user row immediately, keyed to a signed cookie. Signing in with an email later
gives them an identity that survives a cleared cookie or a different device.

KNOWN LIMITATION, DELIBERATELY LEFT
-----------------------------------
Signing in switches you to the email-backed account; it does not merge data
uploaded anonymously. Merging is a genuine design question (which resume wins?
do fingerprints collide?) and guessing at it would silently corrupt someone's
history. The UI says so rather than pretending otherwise.

NO NEW DEPENDENCIES
-------------------
hmac, hashlib and secrets are all stdlib. A signed cookie is not the place to
add a library you have to keep patched.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from .db import session_scope
from .models import UsedMagicToken, User

SESSION_COOKIE = "ri_session"
SESSION_TTL = 60 * 60 * 24 * 30          # 30 days
MAGIC_TTL = 60 * 15                      # 15 minutes — long enough to find the email

ROOT = Path(__file__).resolve().parents[1]
KEY_FILE = ROOT / ".secret_key"


def _load_secret() -> bytes:
    """Read SECRET_KEY, or generate and persist one.

    Generating on every boot would silently log everyone out on each reload,
    which looks like a bug and is impossible to diagnose from the symptom.
    """
    env = os.environ.get("SECRET_KEY")
    if env:
        return env.encode()

    # The generated-file fallback is a development convenience and is actively
    # unsafe with more than one replica: each invents its own key, so a session
    # or API token minted by one is rejected by the next. The symptom is
    # "people get logged out at random" with nothing in the logs, and a
    # redeploy that loses the file logs everyone out at once.
    if os.environ.get("ENV", "dev").strip().lower() == "production":
        raise RuntimeError(
            "SECRET_KEY must be set when ENV=production. Without it each "
            "process generates a different signing key and sessions stop "
            "working across replicas."
        )

    if KEY_FILE.exists():
        return KEY_FILE.read_bytes().strip()
    key = secrets.token_urlsafe(48).encode()
    KEY_FILE.write_bytes(key)
    try:
        KEY_FILE.chmod(0o600)             # no-op on Windows, correct on POSIX
    except OSError:
        pass
    return key


SECRET = _load_secret()


# --------------------------------------------------------------------------
# Signing
# --------------------------------------------------------------------------

def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def sign(payload: dict, ttl: int) -> str:
    """payload -> 'body.signature', with the expiry inside the signed body.

    Expiry lives in the payload rather than alongside it, so it cannot be
    edited without invalidating the signature.
    """
    body = dict(payload)
    body["exp"] = int(time.time()) + ttl
    raw = json.dumps(body, separators=(",", ":"), sort_keys=True).encode()
    sig = hmac.new(SECRET, raw, hashlib.sha256).digest()
    return f"{_b64(raw)}.{_b64(sig)}"


def verify(token: str) -> dict | None:
    """Return the payload, or None. Never raises on malformed input."""
    try:
        body_b64, sig_b64 = token.split(".", 1)
        raw, sig = _unb64(body_b64), _unb64(sig_b64)
    except Exception:  # noqa: BLE001
        return None

    expected = hmac.new(SECRET, raw, hashlib.sha256).digest()
    # compare_digest, not ==, so a forged token can't be found byte by byte
    # from response timing.
    if not hmac.compare_digest(sig, expected):
        return None

    try:
        payload = json.loads(raw)
    except Exception:  # noqa: BLE001
        return None

    if int(payload.get("exp", 0)) < time.time():
        return None
    return payload


# --------------------------------------------------------------------------
# Users
# --------------------------------------------------------------------------

@dataclass
class Identity:
    user_id: int
    email: str
    is_anonymous: bool

    @property
    def display(self) -> str:
        return "Guest" if self.is_anonymous else self.email


def _anon_email(handle: str) -> str:
    return f"anon-{handle}@guest.local"


def create_anonymous() -> Identity:
    handle = secrets.token_hex(8)
    email = _anon_email(handle)
    with session_scope() as s:
        user = User(email=email)
        s.add(user)
        s.flush()
        return Identity(user.id, email, True)


def get_or_create_by_email(email: str) -> Identity:
    email = email.strip().lower()
    with session_scope() as s:
        user = s.scalar(select(User).where(User.email == email))
        if user is None:
            user = User(email=email)
            s.add(user)
            s.flush()
        return Identity(user.id, email, False)


def identity_for(user_id: int) -> Identity | None:
    with session_scope() as s:
        user = s.get(User, user_id)
        if user is None:
            return None
        return Identity(user.id, user.email, user.email.endswith("@guest.local"))


# --------------------------------------------------------------------------
# Session cookie
# --------------------------------------------------------------------------

def _session_nonce(user_id: int, rotate: bool = False) -> str | None:
    """Read (or rotate) the per-user session nonce.

    Same shape as the API token nonce and for the same reason: revocation has
    to be per-user. Rotating the global SECRET_KEY would sign out every user
    on the service, which is an outage, not a logout.
    """
    with session_scope() as s:
        user = s.get(User, user_id)
        if user is None:
            return None
        if rotate or not user.session_nonce:
            user.session_nonce = secrets.token_hex(8)
        return user.session_nonce


def make_session(identity: Identity) -> str:
    return sign({
        "uid": identity.user_id,
        "anon": identity.is_anonymous,
        "n": _session_nonce(identity.user_id) or "",
    }, SESSION_TTL)


def revoke_sessions(user_id: int) -> None:
    """Sign out every browser holding a cookie for this user."""
    _session_nonce(user_id, rotate=True)


def read_session(cookie: str | None) -> Identity | None:
    """Resolve a cookie to a live identity.

    Re-reads the user from the database rather than trusting the cookie's
    contents, so a deleted account can't keep working off a valid signature.
    """
    if not cookie:
        return None
    payload = verify(cookie)
    if not payload:
        return None

    uid = int(payload.get("uid", 0))
    with session_scope() as s:
        user = s.get(User, uid)
        if user is None:
            return None
        # A cookie minted before the last "sign out everywhere" carries a stale
        # nonce. Signatures alone cannot express revocation.
        if user.session_nonce and not hmac.compare_digest(
                str(payload.get("n", "")), user.session_nonce):
            return None
        return Identity(user.id, user.email, user.email.endswith("@guest.local"))


def cookie_kwargs(secure: bool | None = None) -> dict:
    """Cookie flags.

    `secure` defaults off for http://127.0.0.1 — a Secure cookie is silently
    dropped over plain HTTP, which presents as "login does nothing" with no
    error anywhere. Set COOKIE_SECURE=1 behind HTTPS.
    """
    if secure is None:
        raw = os.environ.get("COOKIE_SECURE", "").strip().lower()
        if raw:
            secure = raw in {"1", "true", "yes"}
        else:
            # Follow the environment rather than defaulting to the insecure
            # option. One forgotten variable should not quietly downgrade every
            # session cookie to plaintext on a live domain.
            secure = os.environ.get("ENV", "dev").strip().lower() == "production"
    return {
        "httponly": True,          # unreadable from JS, so XSS can't lift it
        "samesite": "lax",         # survives the magic-link click, blocks CSRF POSTs
        "secure": secure,
        "max_age": SESSION_TTL,
        "path": "/",
    }


# --------------------------------------------------------------------------
# Magic links
# --------------------------------------------------------------------------

# Single-use enforcement lives in the database. It used to be a module-level
# set, which reset on restart and was not shared between worker processes —
# under RQ the same link would work once per worker. A link sits in a mailbox
# forever, so the guard has to outlive the process that issued it.


def make_magic_token(email: str) -> str:
    return sign({"email": email.strip().lower(), "nonce": secrets.token_hex(8)}, MAGIC_TTL)


def consume_magic_token(token: str) -> Identity | None:
    payload = verify(token)
    if not payload or "email" not in payload:
        return None
    nonce = payload.get("nonce", "")
    if not nonce:
        return None

    # Claim the nonce by inserting it. The primary key does the work: a second
    # use is an IntegrityError rather than a read-then-write race two requests
    # can both win.
    try:
        with session_scope() as s:
            s.add(UsedMagicToken(nonce=nonce))
            s.flush()
    except IntegrityError:
        return None

    return get_or_create_by_email(payload["email"])


def prune_used_tokens(older_than_seconds: int = MAGIC_TTL * 4) -> int:
    """Spent nonces past any possible expiry are dead weight."""
    from datetime import datetime, timedelta, timezone
    cutoff = (datetime.now(timezone.utc).replace(tzinfo=None)
              - timedelta(seconds=older_than_seconds))
    with session_scope() as s:
        return s.execute(
            delete(UsedMagicToken).where(UsedMagicToken.used_at < cutoff)
        ).rowcount or 0


# --------------------------------------------------------------------------
# API tokens (browser extension)
# --------------------------------------------------------------------------

API_TTL = 60 * 60 * 24 * 180        # 180 days


def _nonce_for(user_id: int, rotate: bool = False) -> str | None:
    """Read (or rotate) the user's token nonce.

    Revocation has to be per-user. Rotating the global SECRET_KEY would work
    but would also sign out every other user, which is not a revocation
    mechanism, it is an outage.
    """
    with session_scope() as s:
        user = s.get(User, user_id)
        if user is None:
            return None
        if rotate or not user.api_nonce:
            user.api_nonce = secrets.token_hex(8)
        return user.api_nonce


def issue_api_token(user_id: int) -> str | None:
    nonce = _nonce_for(user_id)
    if nonce is None:
        return None
    return sign({"uid": user_id, "kind": "api", "n": nonce}, API_TTL)


def revoke_api_tokens(user_id: int) -> None:
    _nonce_for(user_id, rotate=True)


def identity_from_api_token(token: str) -> Identity | None:
    """Validate a bearer token from the extension.

    Checks the nonce against the database, so a revoked token stops working
    immediately rather than at expiry.
    """
    payload = verify(token or "")
    if not payload or payload.get("kind") != "api":
        return None
    uid = int(payload.get("uid", 0))
    with session_scope() as s:
        user = s.get(User, uid)
        if user is None or not user.api_nonce:
            return None
        if not hmac.compare_digest(str(payload.get("n", "")), user.api_nonce):
            return None
        return Identity(user.id, user.email, user.email.endswith("@guest.local"))


def delivery_mode() -> str:
    """No SMTP is configured, and pretending otherwise would be worse than
    saying so. In dev the link is returned in the response and printed to the
    console; wiring a mail provider is a single function swap."""
    return "console" if not os.environ.get("SMTP_URL") else "smtp"


# --------------------------------------------------------------------------
# Password sign-in
# --------------------------------------------------------------------------

def find_by_email(email: str) -> Identity | None:
    """Look up without creating. Sign-in must not create accounts by typo."""
    addr = (email or "").strip().lower()
    if not addr:
        return None
    with session_scope() as s:
        user = s.execute(select(User).where(User.email == addr)).scalar_one_or_none()
        if user is None:
            return None
        return Identity(user.id, user.email, user.email.endswith("@guest.local"))


def has_password(user_id: int) -> bool:
    with session_scope() as s:
        user = s.get(User, user_id)
        return bool(user and user.password_hash)


def set_password(user_id: int, password: str) -> bool:
    """Set or replace a password. Rotates sessions: a password change that
    leaves old sessions alive is not a way to recover a compromised account."""
    from . import passwords
    from datetime import datetime, timezone

    with session_scope() as s:
        user = s.get(User, user_id)
        if user is None:
            return False
        user.password_hash = passwords.hash_password(password)
        user.password_set_at = datetime.now(timezone.utc).replace(tzinfo=None)
        user.session_nonce = secrets.token_hex(8)
    return True


def check_password(email: str, password: str) -> Identity | None:
    """Verify a sign-in. None for every failure, with no hint which one.

    A missing account still pays the cost of a hash comparison, so response
    time does not answer "is this address registered?".
    """
    from . import passwords

    addr = (email or "").strip().lower()
    with session_scope() as s:
        user = s.execute(select(User).where(User.email == addr)).scalar_one_or_none()

        if user is None or not user.password_hash:
            passwords.waste_time()
            return None

        if not passwords.verify_password(password, user.password_hash):
            return None

        # Cost was raised since this hash was made — upgrade it now, while we
        # legitimately hold the plaintext.
        if passwords.needs_rehash(user.password_hash):
            user.password_hash = passwords.hash_password(password)

        return Identity(user.id, user.email, user.email.endswith("@guest.local"))


def create_with_password(email: str, password: str) -> Identity | None:
    """New account. None if the address is already taken — the caller decides
    what to say about that, because saying it plainly leaks who has an
    account."""
    from . import passwords
    from datetime import datetime, timezone

    addr = (email or "").strip().lower()
    with session_scope() as s:
        existing = s.execute(select(User).where(User.email == addr)).scalar_one_or_none()
        if existing is not None:
            return None
        user = User(
            email=addr,
            password_hash=passwords.hash_password(password),
            password_set_at=datetime.now(timezone.utc).replace(tzinfo=None),
        )
        s.add(user)
        s.flush()
        return Identity(user.id, user.email, False)


# --------------------------------------------------------------------------
# OAuth account linking
# --------------------------------------------------------------------------

def link_oauth(email: str, provider: str, subject: str) -> Identity | None:
    """Resolve a verified provider identity to an account, creating if new.

    Matching is by email because the provider has verified it. If the address
    already exists — signed up by password or magic link — this links rather
    than forking a second account, which is what a user expects when they
    click "Sign in with Google" on an address they already use here.
    """
    addr = (email or "").strip().lower()
    if not addr or "@" not in addr:
        return None

    with session_scope() as s:
        user = s.execute(select(User).where(User.email == addr)).scalar_one_or_none()
        if user is None:
            user = User(email=addr, oauth_provider=provider, oauth_subject=subject)
            s.add(user)
            s.flush()
        else:
            if not user.oauth_provider:
                user.oauth_provider = provider
                user.oauth_subject = subject
        return Identity(user.id, user.email, False)
