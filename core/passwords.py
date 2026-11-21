"""
Password hashing.

WHY PBKDF2 AND NOT ARGON2
-------------------------
Argon2id is the better algorithm. It is also a compiled dependency, and this
codebase runs on stdlib crypto everywhere else (see auth.py). PBKDF2-HMAC-SHA256
at a high iteration count is what `hashlib` gives us for free, it is FIPS-listed,
and it is what OWASP still lists as acceptable. The cost is stored per hash, so
raising it later re-hashes users transparently on their next sign-in — the
migration path is already here.

WHAT THIS MODULE REFUSES TO DO
------------------------------
It does not score passwords out of five, and it does not draw a strength meter.
A meter tells the user their password is "strong" based on character classes,
which is how `P@ssw0rd1!` gets a green bar. The checks here are the two that
actually predict compromise: length, and presence in a list of passwords that
have already been breached.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from dataclasses import dataclass

ALGO = "pbkdf2_sha256"
ITERATIONS = 600_000        # OWASP 2023 floor for PBKDF2-HMAC-SHA256
SALT_BYTES = 16
MIN_LENGTH = 10
MAX_LENGTH = 200            # a megabyte password is a denial-of-service, not security

# Deliberately short. A real deployment should check Have I Been Pwned's
# k-anonymity range API, which never sees the password. This list catches the
# handful that show up in practice without a network call.
_COMMON = {
    "password", "password1", "password123", "123456", "12345678", "123456789",
    "qwerty", "qwerty123", "letmein", "welcome", "welcome1", "admin", "admin123",
    "iloveyou", "monkey", "dragon", "sunshine", "princess", "football",
    "abc123", "111111", "123123", "changeme", "passw0rd", "p@ssword",
    "jobsearch", "rejectionintel", "linkedin", "trustno1", "baseball",
}


@dataclass(frozen=True)
class Check:
    ok: bool
    reason: str = ""


def validate(password: str, email: str = "") -> Check:
    """Reject what is known to fail. Everything else is the user's call."""
    pw = password or ""

    if len(pw) < MIN_LENGTH:
        return Check(False, f"Use at least {MIN_LENGTH} characters. Length is "
                            f"the only thing that reliably makes a password "
                            f"hard to guess.")
    if len(pw) > MAX_LENGTH:
        return Check(False, f"Keep it under {MAX_LENGTH} characters.")
    if pw.lower() in _COMMON:
        return Check(False, "That password appears in every breach list there "
                            "is. Pick something else.")
    if email and pw.lower() == email.strip().lower():
        return Check(False, "Your password cannot be your email address.")
    if email and "@" in email:
        handle = email.split("@", 1)[0].lower()
        if len(handle) >= 4 and handle in pw.lower():
            return Check(False, "Your password contains your email name, which "
                                "is the first thing anyone would try.")
    if len(set(pw)) < 4:
        return Check(False, "That is too few distinct characters.")
    return Check(True)


def hash_password(password: str) -> str:
    """`algo$iterations$salt$hash`, so the cost can be raised without a migration."""
    salt = secrets.token_bytes(SALT_BYTES)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, ITERATIONS)
    return f"{ALGO}${ITERATIONS}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """Constant-time check. False for anything malformed rather than raising."""
    if not stored or not password:
        return False
    try:
        algo, iters, salt_hex, hash_hex = stored.split("$", 3)
        if algo != ALGO:
            return False
        dk = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"),
            bytes.fromhex(salt_hex), int(iters))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(dk.hex(), hash_hex)


def needs_rehash(stored: str) -> bool:
    """True when a hash was made with a weaker cost than we now use."""
    try:
        algo, iters, _, _ = stored.split("$", 3)
    except (ValueError, AttributeError):
        return True
    return algo != ALGO or int(iters) < ITERATIONS


# A hash of a random string, computed once at import. verify() against this
# when the account does not exist, so a missing user costs the same time as a
# wrong password — otherwise response timing answers "does this address have
# an account?", which is the oracle HANDOVER 9.5 closed at the HTTP layer.
_DUMMY = hash_password(secrets.token_hex(16))


def waste_time() -> None:
    verify_password(os.urandom(8).hex(), _DUMMY)
