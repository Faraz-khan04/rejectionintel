"""
Route tests — the HTTP surface, which nothing else covers.

WHY THIS EXISTS SEPARATELY FROM test_pipeline
----------------------------------------------
test_pipeline proves the mathematics is right. It says nothing about whether
the web layer delivers that mathematics correctly, and those are different
failure surfaces with different bugs.

A unit test proves a signature-verification function works. A route test proves
the function is actually CALLED — which is the bug that ships. Every failure
below is one that unit tests pass straight through:

  * a guard that works in isolation but was bypassed by a route change
  * auth verified in a helper nobody invokes on the protected path
  * an access check that returns the right code and the wrong body
  * a write endpoint that deadlocks under two simultaneous callers

Run with:  python -m tests.test_routes
"""

from __future__ import annotations

import concurrent.futures
import json
import os
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# Isolated database, so running the suite never touches real data.
_TMP = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_TMP.close()
os.environ["DATABASE_URL"] = f"sqlite:///{_TMP.name}"
os.environ.pop("RESEND_API_KEY", None)
os.environ.pop("SMTP_URL", None)

from fastapi.testclient import TestClient  # noqa: E402

from api.main import app  # noqa: E402

PASS, FAIL = [], []


def check(label: str, ok: bool, detail: str = "") -> None:
    (PASS if ok else FAIL).append(label)
    mark = "  ok  " if ok else " FAIL "
    print(f"{mark}{label}" + (f"  — {detail}" if detail and not ok else ""))


SAMPLE = ROOT / "sample" / "demo"


# ==========================================================================
# 1. Auth, through real routes and real cookies
# ==========================================================================

def test_auth(c: TestClient) -> None:
    print("\n── auth ──")

    r = c.post("/api/auth/request", data={"email": "not-an-email"})
    check("malformed address rejected", r.status_code == 400, str(r.status_code))

    r = c.post("/api/auth/request", data={"email": "route@test.com"})
    check("link issued", r.status_code == 200 and "link" in r.json())
    link = r.json()["link"]

    # A protected route before signing in.
    fresh = TestClient(app)
    r = fresh.post("/api/cohort", data={"name": "x"})
    check("guest refused cohort creation", r.status_code == 401, str(r.status_code))

    r = c.get(link, follow_redirects=False)
    check("magic link accepted", r.status_code == 303, str(r.status_code))
    check("session cookie set", "ri_session" in c.cookies)

    me = c.get("/api/auth/me").json()
    check("identity resolves through the cookie",
          me.get("signed_in") and me.get("display") == "route@test.com", str(me))

    # The replay. This is the test that matters: the unit test proves the nonce
    # is consumed, this proves the route consults it.
    replay = TestClient(app)
    r = replay.get(link, follow_redirects=False)
    check("replayed link grants no session",
          "ri_session" not in replay.cookies
          and "expired" in r.headers.get("location", ""),
          r.headers.get("location", ""))

    # Tampered signature.
    tampered = TestClient(app)
    tampered.cookies.set("ri_session", c.cookies["ri_session"][:-6] + "AAAAAA")
    me = tampered.get("/api/auth/me").json()
    check("tampered cookie is not honoured", not me.get("signed_in"), str(me))


# ==========================================================================
# 2. The guards, exercised over HTTP
# ==========================================================================

def test_guards(c: TestClient) -> None:
    print("\n── guards at the HTTP layer ──")

    with open(SAMPLE / "demo_applications.csv", "rb") as f:
        csv_bytes = f.read()
    with open(SAMPLE / "demo_resume.txt") as f:
        resume = f.read()

    r = c.post("/api/analyze",
               files={"applications": ("a.csv", csv_bytes, "text/csv")},
               data={"resume_text": resume})
    check("analysis queued", r.status_code == 200 and "job_id" in r.json())
    job_id = r.json()["job_id"]

    import time
    payload = None
    for _ in range(60):
        time.sleep(0.5)
        d = c.get(f"/api/jobs/{job_id}").json()
        if d["state"] in ("done", "failed"):
            payload = d
            break
    check("job completed", payload is not None and payload["state"] == "done",
          str(payload and payload.get("error"))[:90])
    if not payload or payload["state"] != "done":
        return
    res = payload["result"]

    # Numeric guard, using the REAL validator rather than a reimplementation
    # of its allowlist. A second copy of the rules drifts from the first and
    # then tests itself instead of the product.
    from core.enums import ConfidenceTier
    from core.narrate import validate

    invented = []
    for f_json in res["findings"]:
        combined = " ".join([f_json.get("headline") or "",
                             f_json.get("explanation") or "",
                             f_json.get("recommendation") or ""])
        # Rebuild the allowlist the same way the guard does, from this
        # finding's own figures.
        allowed = {
            "applications_with_attribute": f_json["n_exposed"],
            "applications_without_attribute": f_json["n_unexposed"],
            "response_rate_with": f_json["rate_exposed"],
            "response_rate_without": f_json["rate_unexposed"],
            "gap_in_points": round(abs(f_json["rate_exposed"]
                                       - f_json["rate_unexposed"]) * 100),
        }
        tier = ConfidenceTier(f_json['tier'])
        errs = validate(combined, allowed, tier)
        if errs:
            invented.append((f_json["dimension"], errs[:1]))
    check("narration contains no number absent from the data",
          not invented, str(invented[:2]))

    # Internal identifiers must never reach a rendered string.
    from core.plain_language import audit
    leaks = []
    for f in res["findings"]:
        for key in ("label", "headline", "explanation", "recommendation"):
            leaks += audit(f.get(key) or "")
    check("no internal identifiers in output", not leaks, str(leaks[:3]))

    # Share redaction, over HTTP rather than in isolation.
    from core.share import audit_share, forbidden_from_report
    card = res.get("share")
    if card:
        problems = audit_share(card, forbidden_from_report(res))
        check("share card carries nothing identifying", not problems, str(problems[:2]))
    else:
        check("share card suppressed rather than leaked", True)

    # Resume suggestions must never contain a metric the user didn't write.
    bad = []
    for l in (res.get("resume_lines") or {}).get("lines", []):
        src = set(re.findall(r"\d[\d,.]*", l["original"]))
        new = set(re.findall(r"\d[\d,.]*", re.sub(r"\[[^\]]*\]", "", l["suggestion"])))
        if new - src:
            bad.append(l["original"][:40])
    check("no invented metrics in resume suggestions", not bad, str(bad[:2]))


# ==========================================================================
# 3. Streaming
# ==========================================================================

def test_stream(c: TestClient) -> None:
    print("\n── SSE ──")

    r = c.post("/api/demo")
    check("demo queued", r.status_code == 200)
    job_id = r.json()["job_id"]

    events, terminal = [], False
    with c.stream("GET", f"/api/jobs/{job_id}/stream") as resp:
        check("stream opens", resp.status_code == 200, str(resp.status_code))
        check("content type is event-stream",
              "text/event-stream" in resp.headers.get("content-type", ""),
              resp.headers.get("content-type", ""))
        for line in resp.iter_lines():
            if not line:
                continue
            if line.startswith("event:"):
                events.append(line.split(":", 1)[1].strip())
                if events[-1] in ("done", "error"):
                    terminal = True
                    break
            if len(events) > 40:
                break

    check("progress events emitted", len(events) >= 2, f"{len(events)} events")
    check("stream sends a terminal event", terminal, str(events[-3:]))


# ==========================================================================
# 4. Access control
# ==========================================================================

def test_access(c: TestClient) -> None:
    print("\n── access control ──")

    owner = TestClient(app)
    link = owner.post("/api/auth/request",
                      data={"email": "owner@routes.com"}).json()["link"]
    owner.get(link, follow_redirects=False)
    made = owner.post("/api/cohort", data={"name": "Routes Cohort"})
    check("signed-in user can create a cohort", made.status_code == 200,
          str(made.status_code))
    cid = owner.get("/api/cohort/mine").json()["owned"][0]["id"]

    outsider = TestClient(app)
    olink = outsider.post("/api/auth/request",
                          data={"email": "outsider@routes.com"}).json()["link"]
    outsider.get(olink, follow_redirects=False)
    r = outsider.get(f"/api/cohort/{cid}")
    check("outsider refused the cohort report", r.status_code == 403,
          str(r.status_code))
    check("refusal says why, without leaking membership",
          "not part of that cohort" in r.json().get("detail", "").lower(),
          str(r.json())[:80])

    r = c.post("/api/track/999999/move", data={"column": "interview"})
    check("moving someone else's card is refused", r.status_code in (403, 404),
          str(r.status_code))

    r = c.post("/api/applications/capture",
               headers={"Authorization": "Bearer nonsense"},
               json={"company": "X", "job_title": "Y"})
    check("bogus API token refused", r.status_code == 401, str(r.status_code))


# ==========================================================================
# 5. Concurrency — the SQLite write-lock surface
# ==========================================================================

def test_concurrency() -> None:
    print("\n── concurrent writes ──")

    def add_one(i: int) -> int:
        cl = TestClient(app)
        return cl.post("/api/track", data={
            "company": f"Concurrent {i}", "role": "Engineer",
            "applied_date": "2026-07-01"}).status_code

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        codes = list(pool.map(add_one, range(16)))

    check("16 concurrent writes all succeeded",
          all(c == 200 for c in codes),
          f"codes: {sorted(set(codes))}")
    check("no 500s under contention", 500 not in codes,
          f"{codes.count(500)} server errors")


# ==========================================================================
# 6. Input handling
# ==========================================================================

def test_inputs(c: TestClient) -> None:
    print("\n── bad input ──")

    r = c.post("/api/analyze", data={"resume_text": "x"})
    check("analysis without applications is refused",
          r.status_code == 400, str(r.status_code))

    r = c.post("/api/analyze",
               files={"applications": ("a.csv", b"\xff\xfe\x00garbage", "text/csv")},
               data={"resume_text": "x"})
    check("undecodable upload doesn't 500", r.status_code in (200, 400),
          str(r.status_code))

    r = c.post("/api/ghost/check", data={"jd_text": "short"})
    check("too-short advert handled", r.status_code == 200
          and not r.json()["listing"]["checked"], str(r.status_code))

    r = c.post("/api/match", data={"jd_text": "", "resume_text": "x"})
    check("empty job description refused", r.status_code == 400, str(r.status_code))

    r = c.get("/api/jobs/does-not-exist")
    check("unknown job id returns 404", r.status_code == 404, str(r.status_code))


def main() -> int:
    print("=" * 62)
    print("ROUTE TESTS — the HTTP surface")
    print("=" * 62)

    # Context-managed on purpose. Without `with`, TestClient spins up a fresh
    # portal per request and tears it down again, so a background job that
    # suspends on a worker thread never gets resumed between polls — it reads
    # as a hung analysis that works perfectly against a real server.
    with TestClient(app) as c:
        test_auth(c)
        test_guards(c)
        test_stream(c)
        test_access(c)
    test_concurrency()
    test_inputs(c)

    print("\n" + "=" * 62)
    if FAIL:
        print(f"{len(FAIL)} FAILED, {len(PASS)} passed")
        for f in FAIL:
            print(f"   FAILED: {f}")
        print("=" * 62)
        return 1
    print(f"ALL {len(PASS)} ROUTE CHECKS PASSED")
    print("=" * 62)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    finally:
        try:
            os.unlink(_TMP.name)
        except OSError:
            pass
