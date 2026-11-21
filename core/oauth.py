"""
Sign in with Google and GitHub.

WHAT THIS DOES AND DOES NOT TRUST
---------------------------------
The only thing taken from a provider is a verified email address and a stable
subject id. No avatar, no name, no contact list, no repository scope. The
scopes requested are the narrowest each provider offers, because an OAuth
consent screen listing five permissions on a job-search tool is a reason not to
click it.

An unverified email is refused. Google returns `email_verified`; GitHub returns
a `verified` flag per address. Accepting an unverified one lets somebody claim
an account by signing up at the provider with an address they do not own.

STATE IS SIGNED, NOT STORED
---------------------------
The `state` parameter is an HMAC-signed token with a 10-minute expiry, using
the same signer as the rest of auth.py. That makes CSRF on the callback a
signature failure rather than a database lookup, and it works unchanged across
worker processes.

CONFIGURATION
-------------
    GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET
    GITHUB_CLIENT_ID / GITHUB_CLIENT_SECRET
    APP_URL                       # must match the redirect URI registered

Redirect URIs to register with each provider:
    {APP_URL}/auth/callback/google
    {APP_URL}/auth/callback/github

With no keys set, `available()` returns an empty list and the buttons do not
render. A dead OAuth button is worse than no button.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import urlencode

import httpx

from . import auth

STATE_TTL = 600     # 10 minutes from click to callback is generous


@dataclass(frozen=True)
class Provider:
    key: str
    label: str
    authorize_url: str
    token_url: str
    scope: str


PROVIDERS = {
    "google": Provider(
        key="google",
        label="Google",
        authorize_url="https://accounts.google.com/o/oauth2/v2/auth",
        token_url="https://oauth2.googleapis.com/token",
        scope="openid email",
    ),
    "github": Provider(
        key="github",
        label="GitHub",
        authorize_url="https://github.com/login/oauth/authorize",
        token_url="https://github.com/login/oauth/access_token",
        scope="read:user user:email",
    ),
}


def _creds(key: str) -> tuple[str, str]:
    return (
        os.environ.get(f"{key.upper()}_CLIENT_ID", "").strip(),
        os.environ.get(f"{key.upper()}_CLIENT_SECRET", "").strip(),
    )


def configured(key: str) -> bool:
    cid, secret = _creds(key)
    return bool(cid and secret)


def available() -> list[dict]:
    """Providers with keys present. Drives which buttons the page renders."""
    return [{"key": p.key, "label": p.label}
            for p in PROVIDERS.values() if configured(p.key)]


def _app_url() -> str:
    return os.environ.get("APP_URL", "http://127.0.0.1:8000").rstrip("/")


def redirect_uri(key: str) -> str:
    return f"{_app_url()}/auth/callback/{key}"


def start_url(key: str, next_path: str = "/app") -> str | None:
    """The URL to send the browser to. None when the provider is unconfigured."""
    p = PROVIDERS.get(key)
    if p is None or not configured(key):
        return None
    cid, _ = _creds(key)

    state = auth.sign({"p": key, "next": next_path[:120]}, STATE_TTL)
    params = {
        "client_id": cid,
        "redirect_uri": redirect_uri(key),
        "scope": p.scope,
        "state": state,
        "response_type": "code",
    }
    if key == "google":
        # Ask for the account chooser rather than silently reusing whichever
        # Google account the browser happens to be signed into.
        params["prompt"] = "select_account"

    return f"{p.authorize_url}?{urlencode(params)}"


def read_state(state: str) -> dict | None:
    payload = auth.verify(state or "")
    if not payload or payload.get("p") not in PROVIDERS:
        return None
    return payload


@dataclass(frozen=True)
class Profile:
    email: str
    subject: str


async def exchange(key: str, code: str) -> Profile | None:
    """Swap the code for a token, then read a verified email. None on failure."""
    p = PROVIDERS.get(key)
    if p is None or not configured(key):
        return None
    cid, secret = _creds(key)

    async with httpx.AsyncClient(timeout=12) as client:
        token_res = await client.post(
            p.token_url,
            data={
                "client_id": cid,
                "client_secret": secret,
                "code": code,
                "redirect_uri": redirect_uri(key),
                "grant_type": "authorization_code",
            },
            headers={"Accept": "application/json"},
        )
        if token_res.status_code != 200:
            return None
        access = (token_res.json() or {}).get("access_token")
        if not access:
            return None

        headers = {"Authorization": f"Bearer {access}",
                   "Accept": "application/json"}

        if key == "google":
            me = await client.get(
                "https://openidconnect.googleapis.com/v1/userinfo",
                headers=headers)
            if me.status_code != 200:
                return None
            data = me.json() or {}
            # email_verified arrives as a bool or the string "true" depending
            # on the endpoint. Anything else is treated as unverified.
            verified = data.get("email_verified")
            if str(verified).lower() not in {"true", "1"}:
                return None
            email = (data.get("email") or "").strip().lower()
            sub = str(data.get("sub") or "")
            return Profile(email, sub) if email and sub else None

        # GitHub: the profile endpoint's email may be null or unverified, so
        # the address always comes from /user/emails.
        me = await client.get("https://api.github.com/user", headers=headers)
        if me.status_code != 200:
            return None
        sub = str((me.json() or {}).get("id") or "")

        emails = await client.get("https://api.github.com/user/emails",
                                  headers=headers)
        if emails.status_code != 200:
            return None
        rows = emails.json() or []
        primary = next((r for r in rows
                        if r.get("primary") and r.get("verified")), None)
        if primary is None:
            primary = next((r for r in rows if r.get("verified")), None)
        if primary is None:
            return None
        email = (primary.get("email") or "").strip().lower()
        return Profile(email, sub) if email and sub else None
