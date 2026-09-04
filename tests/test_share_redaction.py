"""The share card must never carry identifying data.

This is the only place a job-search history leaves the account, and the
consequences of getting it wrong are not "a confusing chart" — they are a
person's employer learning they are looking. So the guarantee is tested
against real report data rather than asserted in a comment.
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.share import audit_share, build_share, forbidden_from_report  # noqa: E402

SAMPLE = Path("/tmp/final.json")


def _report():
    if SAMPLE.exists():
        return json.loads(SAMPLE.read_text())["result"]
    # Minimal stand-in so the test still runs without a captured report.
    return {
        "summary": {"n_analyzable": 50, "response_rate": 0.2,
                    "funnel": {"submitted": 50, "screen": 10}},
        "verdict": {"headline": "Most of your applications never reach a human."},
        "evidence_stats": {"with_text": 12, "screened_out": 10},
        "pace": {"median_reply_days": 8},
        "applications": [{"company": "Corvus Systems", "title": "Senior Product Manager"}],
        "observed": [], "resume_lines": {"lines": []},
    }


def main() -> int:
    d = _report()
    card = build_share(d["summary"], d["verdict"],
                       d.get("evidence_stats") or {}, d.get("pace"))
    forbidden = forbidden_from_report(d)
    leaks = audit_share(card.as_dict(), forbidden)

    print(f"  checked against {len(forbidden)} identifying strings")
    if leaks:
        print("  FAIL — share card leaks:")
        for l in leaks:
            print("   ", l)
        return 1
    print("  PASS  share card carries counts and rates only")

    # The audit itself must not be a no-op.
    planted = card.as_dict()
    planted["line"] = "Rejected by Corvus Systems for a Senior Product Manager role."
    if not audit_share(planted, forbidden or ["Corvus Systems"]):
        print("  FAIL — audit did not catch a planted company name")
        return 1
    print("  PASS  audit catches a planted leak")
    return 0


if __name__ == "__main__":
    sys.exit(main())
