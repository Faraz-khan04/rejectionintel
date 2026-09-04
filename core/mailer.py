"""
Email delivery — the switch that turns "no users" from a fact into a choice.

WHY THIS IS THE LAST BLOCKER AND NOT A FEATURE
----------------------------------------------
Magic links currently print to the server console. That is a hard ceiling of
exactly one tester: you. Nothing else on the roadmap can be validated against
real behaviour until someone else can log in from their own device.

THE DESIGN
----------
Three transports behind one function:

  console  (default) prints the link. Correct for development, and correct as a
           fallback — a magic link that silently fails to send is worse than one
           that visibly wasn't sent.
  resend   HTTP API. No SMTP handshake, no port 587 blocked by a host.
  smtp     For anyone who'd rather not add a vendor.

WHAT IS TESTED WITHOUT A PROVIDER KEY
-------------------------------------
Everything except the network call. The transport is injectable, so the whole
path — template rendering, both message parts, the failure branch, the fallback
to console — runs against a fake that records what it was asked to send. What
remains untested at deploy is exactly one HTTP POST.

DELIVERABILITY IS NOT AN IMPLEMENTATION DETAIL
----------------------------------------------
A magic link that lands in spam is indistinguishable from a broken product, and
the user blames you rather than their filter. The notes in check_dns() are the
difference between this working and appearing to work.
"""

from __future__ import annotations

import json
import os
import smtplib
import urllib.error
import urllib.request
from dataclasses import dataclass
from email.message import EmailMessage

MAILER_VERSION = "0.1.0"

FROM_NAME = os.environ.get("MAIL_FROM_NAME", "RejectionIntel")
FROM_ADDR = os.environ.get("MAIL_FROM", "")
RESEND_KEY = os.environ.get("RESEND_API_KEY", "")
SMTP_URL = os.environ.get("SMTP_URL", "")
APP_URL = os.environ.get("APP_URL", "http://127.0.0.1:8000")


@dataclass
class SendResult:
    ok: bool
    transport: str
    detail: str = ""
    link: str | None = None      # returned only in console mode


def transport_name() -> str:
    if RESEND_KEY and FROM_ADDR:
        return "resend"
    if SMTP_URL and FROM_ADDR:
        return "smtp"
    return "console"


# --------------------------------------------------------------------------
# The message
# --------------------------------------------------------------------------

def _subject() -> str:
    # No "Re:", no fake urgency, no emoji. Those raise spam scores and this is
    # a transactional message someone is actively waiting for.
    return "Your sign-in link"


def _text_body(link: str, minutes: int) -> str:
    return f"""Here's your sign-in link for RejectionIntel:

{link}

It works once and expires in {minutes} minutes.

If you didn't ask for this, you can ignore it — nobody can sign in without
the link, and it doesn't tell us anything about you.

RejectionIntel
{APP_URL}
"""


def _html_body(link: str, minutes: int) -> str:
    """Deliberately plain HTML.

    Tables, images and heavy CSS raise spam scores and break in a third of
    clients. A transactional email needs to arrive and be clickable; it does
    not need to be a brochure.
    """
    return f"""<!DOCTYPE html>
<html><body style="margin:0;padding:0;background:#f7f7fb;
  font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;">
  <div style="max-width:520px;margin:0 auto;padding:40px 24px;">
    <p style="font-size:15px;color:#0e1017;margin:0 0 24px;">
      Here's your sign-in link.
    </p>
    <a href="{link}" style="display:inline-block;background:#5a47e0;color:#ffffff;
      text-decoration:none;padding:13px 28px;border-radius:10px;font-weight:600;
      font-size:15px;">Sign in to RejectionIntel</a>
    <p style="font-size:13px;color:#5f6780;margin:24px 0 0;line-height:1.6;">
      It works once and expires in {minutes} minutes.
    </p>
    <p style="font-size:13px;color:#5f6780;margin:16px 0 0;line-height:1.6;">
      If you didn't ask for this you can ignore it. Nobody can sign in without
      the link, and it doesn't tell us anything about you.
    </p>
    <p style="font-size:12px;color:#868da3;margin:32px 0 0;
      border-top:1px solid #e6e8f0;padding-top:16px;line-height:1.6;">
      If the button doesn't work, paste this into your browser:<br>
      <span style="color:#5f6780;word-break:break-all;">{link}</span>
    </p>
  </div>
</body></html>"""


# --------------------------------------------------------------------------
# Transports
# --------------------------------------------------------------------------

def _send_resend(to: str, subject: str, text: str, html: str) -> SendResult:
    payload = json.dumps({
        "from": f"{FROM_NAME} <{FROM_ADDR}>",
        "to": [to], "subject": subject, "text": text, "html": html,
    }).encode()
    req = urllib.request.Request(
        "https://api.resend.com/emails", data=payload, method="POST",
        headers={"Authorization": f"Bearer {RESEND_KEY}",
                 "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return SendResult(200 <= r.status < 300, "resend",
                              f"HTTP {r.status}")
    except urllib.error.HTTPError as e:
        return SendResult(False, "resend", f"HTTP {e.code}: {e.read()[:180]!r}")
    except Exception as exc:  # noqa: BLE001
        return SendResult(False, "resend", str(exc))


def _send_smtp(to: str, subject: str, text: str, html: str) -> SendResult:
    from urllib.parse import urlparse

    u = urlparse(SMTP_URL)
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = f"{FROM_NAME} <{FROM_ADDR}>"
    msg["To"] = to
    msg.set_content(text)
    msg.add_alternative(html, subtype="html")
    try:
        cls = smtplib.SMTP_SSL if u.scheme == "smtps" else smtplib.SMTP
        with cls(u.hostname, u.port or (465 if u.scheme == "smtps" else 587),
                 timeout=10) as srv:
            if u.scheme != "smtps":
                srv.starttls()
            if u.username:
                srv.login(u.username, u.password or "")
            srv.send_message(msg)
        return SendResult(True, "smtp", "sent")
    except Exception as exc:  # noqa: BLE001
        return SendResult(False, "smtp", str(exc))


def _send_console(to: str, link: str) -> SendResult:
    print(f"\n  ── sign-in link for {to} ──\n  {link}\n")
    return SendResult(True, "console", "printed to server log", link=link)


# --------------------------------------------------------------------------
# The one function the rest of the app calls
# --------------------------------------------------------------------------

def send_magic_link(to: str, link: str, minutes: int = 15,
                    transport=None) -> SendResult:
    """Deliver a sign-in link.

    `transport` exists so the whole path can be tested without a provider key
    or a network. Everything above the HTTP call is exercised by the fake.

    A failed send falls back to console rather than raising. A user staring at
    "check your email" for a message that will never arrive is a worse outcome
    than a link in a log the operator can find.
    """
    subject = _subject()
    text = _text_body(link, minutes)
    html = _html_body(link, minutes)

    if transport is not None:
        return transport(to, subject, text, html)

    name = transport_name()
    if name == "resend":
        res = _send_resend(to, subject, text, html)
    elif name == "smtp":
        res = _send_smtp(to, subject, text, html)
    else:
        return _send_console(to, link)

    if not res.ok:
        print(f"  mail send failed ({res.transport}): {res.detail}")
        fallback = _send_console(to, link)
        fallback.detail = f"{res.transport} failed, printed instead: {res.detail}"
        fallback.transport = f"{res.transport}->console"
        return fallback
    return res


def check_config() -> dict:
    """What /api/health reports, so a misconfigured mailer is visible.

    A silent fallback to console in production looks identical to working
    email until someone reports never receiving a link.
    """
    name = transport_name()
    problems: list[str] = []

    if name == "console":
        problems.append(
            "No mail provider configured — links print to the server log. Set "
            "RESEND_API_KEY (or SMTP_URL) and MAIL_FROM to send real email.")
    if FROM_ADDR and "@" not in FROM_ADDR:
        problems.append(f"MAIL_FROM doesn't look like an address: {FROM_ADDR!r}")
    if APP_URL.startswith("http://") and "127.0.0.1" not in APP_URL \
            and "localhost" not in APP_URL:
        problems.append(
            "APP_URL is http:// on a public host — the link in the email will "
            "be insecure and some clients will warn on it.")

    return {
        "transport": name,
        "from": FROM_ADDR or None,
        "app_url": APP_URL,
        "ok": not problems,
        "problems": problems,
        "deliverability_note": (
            "Before real users: add SPF and DKIM records for your sending "
            "domain, and a DMARC record set to p=none while you watch reports. "
            "Without them a transactional email is routinely filtered, and a "
            "sign-in link in a spam folder is indistinguishable from a broken "
            "product — the user blames you, not their filter."
        ),
        "version": MAILER_VERSION,
    }
