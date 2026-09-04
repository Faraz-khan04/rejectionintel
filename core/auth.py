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

from sqlalchemy import select

from .db import session_scope
from .models import User

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

def make_session(identity: Identity) -> str:
    return sign({"uid": identity.user_id, "anon": identity.is_anonymous}, SESSION_TTL)


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
    return identity_for(int(payload.get("uid", 0)))


def cookie_kwargs(secure: bool | None = None) -> dict:
    """Cookie flags.

    `secure` defaults off for http://127.0.0.1 — a Secure cookie is silently
    dropped over plain HTTP, which presents as "login does nothing" with no
    error anywhere. Set COOKIE_SECURE=1 behind HTTPS.
    """
    if secure is None:
        secure = os.environ.get("COOKIE_SECURE", "").strip() in {"1", "true", "yes"}
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

# Single-use enforcement. A link sits in an inbox forever; without this,
# anyone who later reads that mailbox has a working key.
_USED: set[str] = set()


def make_magic_token(email: str) -> str:
    return sign({"email": email.strip().lower(), "nonce": secrets.token_hex(8)}, MAGIC_TTL)


def consume_magic_token(token: str) -> Identity | None:
    payload = verify(token)
    if not payload or "email" not in payload:
        return None
    nonce = payload.get("nonce", "")
    if nonce in _USED:
        return None
    _USED.add(nonce)
    if len(_USED) > 10_000:
        _USED.clear()              # bounded; expiry does the real work
    return get_or_create_by_email(payload["email"])


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
