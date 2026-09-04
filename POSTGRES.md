# Moving to PostgreSQL

Everything below was run against a real PostgreSQL 16 instance, not asserted
from reading the schema.

---

## Why, and when

SQLite is genuinely fine for a single process. It is not fine the moment the
app and one or more RQ workers run at the same time — which is the next thing
on the roadmap. **Do this before activating RQ, not after.** Turning on
multi-process writes and then discovering you need Postgres means debugging
intermittent failures under real load instead of doing a clean swap now.

---

## What the test found

Six separate processes starting simultaneously, each writing 30 rows:

| | Before the fix | After |
|---|---|---|
| SQLite | **5 of 6 processes crashed** on startup | 6 of 6, 180 writes, no failures |
| PostgreSQL | 6 of 6 | 6 of 6, 180 writes, no failures |

The crash was `table users already exists` during `init_db()`.
`create_all(checkfirst=True)` reads the table list and then issues CREATE for
whatever is missing — two steps, with a window in between. One process wins,
the rest die. It never happens with a single process, which is why it had
never appeared.

`init_db()` now takes a file lock and treats "already exists" as success.
That fix matters on both backends; Postgres survived only because its DDL is
transactional, and the race was still there.

---

## What already works on Postgres

Run end to end against a live instance:

- 19 tables created from the same models, no dialect changes
- CSV ingest → upsert → analysis: 112 applications, 8 findings
- Idempotent re-upload: second run inserted 0, updated 112
- Employer index, benchmarks, cohort reports — all aggregation queries
- Tracker: add, dedupe, move
- Ghost signals

No SQLite-specific SQL exists in the codebase. The models avoid `JSONB`,
arrays and server-side defaults precisely so this swap would be one setting.

---

## The migration

### 1. Install the driver

```
pip install "psycopg[binary]"
```

### 2. Create the database

```sql
CREATE USER rejectionintel WITH PASSWORD 'choose-something-real';
CREATE DATABASE rejectionintel OWNER rejectionintel;
```

### 3. Point the app at it

```
DATABASE_URL=postgresql+psycopg://rejectionintel:password@localhost/rejectionintel
```

That is the only application change. `init_db()` creates the schema on first
start.

### 4. Move existing data, if you have any

There is no automated path and there shouldn't be — with no real users yet,
starting clean is simpler and safer than writing a migration you will run once.

If you do need to carry data across:

```
python -m sample.export_sqlite > dump.json    # not written; see note below
```

I have deliberately not written this. A one-shot data migration that runs
against real user data and is never tested twice is a bad trade against
"re-upload your CSV", which is a thirty-second task your users can do
themselves. Write it when you have data worth carrying.

---

## Before real traffic

- **Back up.** `pg_dump` on a cron. Do this before "no users" stops being true,
  not after.
- **Connection pool.** SQLAlchemy defaults to 5 connections with 10 overflow.
  Fine for one app process; if you run gunicorn with N workers, each gets its
  own pool, so `N × 15` must stay under Postgres `max_connections` (100 by
  default).
- **Bind to localhost** unless the database is on another machine, in which
  case require TLS.
- **Run `init_db()` once at deploy**, not on every worker start. The lock makes
  concurrent starts safe, but serialising six processes through a migration on
  every boot is wasted time.

---

## What this does not fix

Postgres solves concurrent writes. It does not make the analysis correct.

Every number this system has produced came from data generated to cooperate —
clean dates, skill names that match the vocabulary, outcomes drawn from a
distribution chosen in advance. Real applications will break things this
migration has nothing to do with.

Running twenty real applications through the ingest is still the highest-value
outstanding task, and it is the one thing infrastructure work cannot substitute
for.
