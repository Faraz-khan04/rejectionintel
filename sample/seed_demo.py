"""
Demo seeding — populate the shared views so an empty product can be shown.

THE LINE THIS SCRIPT DOES NOT CROSS
------------------------------------
The reply index and the benchmarks only have anything to say once many people
have used the product. Before that they correctly show "not enough data", which
is honest and also makes the feature impossible to demonstrate.

The tempting fix is to seed them with real company names and invented reply
rates. That would be publishing a fabricated factual claim about a named
business — the exact defamation exposure the index was designed to avoid, and
it would poison the real data the moment users arrived.

So every company here is fictional, every user is marked, and the seeded state
is flagged so the interface can say plainly that it is sample data. Real usage
overwrites nothing: seeded accounts live under @sample.invalid, which is a
reserved TLD that can never belong to a person.

    python -m sample.seed_demo          seed
    python -m sample.seed_demo --clear  remove every seeded account
"""

from __future__ import annotations

import io
import random
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy import select  # noqa: E402

from core.db import init_db, session_scope  # noqa: E402
from core.ingest_v2 import ingest_csv  # noqa: E402
from core.models import Application, ApplicationEvent, User  # noqa: E402
from core.store import get_or_create_user, upsert_applications  # noqa: E402

SEED_DOMAIN = "@sample.invalid"     # reserved TLD — can never be a real address

# Deliberately invented. Any resemblance to a real employer would make the
# reply index a statement about that employer, which it must never be.
COMPANIES = [
    ("Kestrel Systems", 0.42), ("Ambergate Software", 0.38),
    ("Northwind Data", 0.31), ("Corvus Labs", 0.29),
    ("Perch Analytics", 0.26), ("Lumen Works", 0.24),
    ("Alder & Finch", 0.21), ("Stillwater Tech", 0.18),
    ("Meridian Grid", 0.14), ("Copperline Digital", 0.11),
    ("Bramblewick", 0.06), ("Halden Interactive", 0.03),
    ("Draycott Holdings", 0.00),   # answers nobody
    ("Stagecraft Media", 0.00),    # answers nobody
]

TITLES = [
    "Backend Engineer", "Data Analyst", "Product Manager", "Platform Engineer",
    "Software Engineer", "Analytics Engineer", "Senior Engineer", "QA Engineer",
    "Frontend Engineer", "DevOps Engineer",
]

JD = ("We are looking for a {title}. Requirements: {years}+ years of experience. "
      "Strong SQL skills required. {extra} Nice to have: Docker, Git.")
EXTRAS = ["Strong Python skills required.", "Strong Java skills required.",
          "Strong React skills required.", "Strong AWS skills required.",
          "Strong Kubernetes skills required.", "Strong Tableau skills required."]

HEADER = ("company,job_title,applied_date,outcome,first_response_date,"
          "job_description\n")


def seed(n_users: int = 40, seed_value: int = 11) -> dict:
    """Create fictional users whose outcomes make the shared views meaningful.

    Each user gets their own underlying reply rate, and each company its own —
    so the index has a genuine spread rather than every employer looking the
    same. The two companies at 0.00 exist to prove the gate lets a real signal
    through, not just to look dramatic.
    """
    random.seed(seed_value)
    init_db()
    created = 0

    for u in range(n_users):
        uid = get_or_create_user(f"sample{u}{SEED_DOMAIN}")
        personal = random.uniform(0.55, 1.45)     # this person's own multiplier
        n = random.randint(14, 55)

        buf = io.StringIO()
        buf.write(HEADER)
        seen: set = set()

        for _ in range(n):
            for _try in range(25):
                company, base = random.choice(COMPANIES)
                title = random.choice(TITLES)
                when = date(2026, 2, 1) + timedelta(days=random.randint(0, 175))
                if (company, title, when) not in seen:
                    seen.add((company, title, when))
                    break
            else:
                continue

            replied = random.random() < min(0.9, base * personal)
            reply_date = (when + timedelta(days=random.randint(4, 16))).isoformat()
            jd = JD.format(title=title, years=random.choice([3, 4, 5, 6]),
                           extra=random.choice(EXTRAS))
            buf.write(
                f'{company},{title},{when.isoformat()},'
                f'{"recruiter call" if replied else "no response"},'
                f'{reply_date if replied else ""},"{jd}"\n')

        report = ingest_csv(buf.getvalue())
        upsert_applications(uid, report.parsed, None)
        created += 1

    return {"users": created, "companies": len(COMPANIES)}


def clear() -> dict:
    """Remove every seeded account and everything belonging to it."""
    removed = 0
    with session_scope() as s:
        users = s.scalars(
            select(User).where(User.email.like(f"%{SEED_DOMAIN}"))).all()
        for u in users:
            app_ids = [a for (a,) in s.execute(
                select(Application.id).where(Application.user_id == u.id)).all()]
            if app_ids:
                s.query(ApplicationEvent).filter(
                    ApplicationEvent.application_id.in_(app_ids)).delete(
                        synchronize_session=False)
                s.query(Application).filter(
                    Application.id.in_(app_ids)).delete(synchronize_session=False)
            s.delete(u)
            removed += 1
    return {"removed": removed}


def is_seeded() -> bool:
    """Whether any sample account exists — used to flag the UI.

    A page showing invented numbers without saying so is the problem this
    whole script was written to avoid.
    """
    with session_scope() as s:
        return s.scalar(
            select(User.id).where(User.email.like(f"%{SEED_DOMAIN}")).limit(1)
        ) is not None


if __name__ == "__main__":
    if "--clear" in sys.argv:
        print(f"  removed {clear()['removed']} sample accounts")
    else:
        out = seed()
        print(f"  seeded {out['users']} sample users across "
              f"{out['companies']} fictional companies")
        print(f"  every account is under {SEED_DOMAIN} — remove with --clear")
