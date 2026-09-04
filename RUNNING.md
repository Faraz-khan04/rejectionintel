# Running RejectionIntel locally

No database, no API key, no network. Everything runs offline.

## 1. Set up (once)

```bash
cd rejectionintel
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install fastapi "uvicorn[standard]" python-multipart scipy
```

## 2. Run

```bash
uvicorn api.main:app --reload --port 8000
```

Open **http://127.0.0.1:8000**

| Route | What it is |
|---|---|
| `/` | Landing page |
| `/app` | Upload form + report |
| `/api/health` | Sanity check — confirms the server is up |
| `/api/template` | Downloads the CSV template |
| `/docs` | Auto-generated API docs (FastAPI gives you this free) |

`--reload` restarts the server whenever you save a file. Leave it running in one
terminal and edit in another.

## 3. Try it with the sample data

Go to `/app`, upload `sample/sample_applications.csv`, and paste the contents of
`sample/sample_resume.txt` into the resume box.

That file is 88 synthetic applications with a **planted signal**: response rate
scales with how well the candidate covers what each JD asks for. It's there so
you can tell the difference between "the pipeline works" and "the pipeline found
something." If the report shows no relationship on this file, something is broken.

Or from the command line:

```bash
curl -X POST http://127.0.0.1:8000/api/analyze \
  -F "applications=@sample/sample_applications.csv" \
  -F "resume=@sample/sample_resume.txt" | python3 -m json.tool
```

## 4. Run the test suite

```bash
python3 -m tests.test_pipeline
```

31 checks, including a simulation of 300 users with pure-noise outcomes. If the
false-positive rate climbs above ~0.15 STRONG findings per user, you've broken a
statistical gate — check that `adjust_run()` is still being called on the whole
run at once rather than per finding.

## Troubleshooting

**`ModuleNotFoundError: No module named 'core'`** — run uvicorn from the
`rejectionintel/` directory, not from inside `api/`.

**Port 8000 in use** — `uvicorn api.main:app --reload --port 8001`.

**"server unreachable" in the page header** — uvicorn isn't running, or you
opened `app.html` as a file instead of through `http://127.0.0.1:8000/app`.
Opening the file directly means there's no server to call.

**CSV rejected** — the error list under the upload button gives you row number,
column, and problem for every bad row at once. Dates accept `YYYY-MM-DD`,
`DD/MM/YYYY`, `MM/DD/YYYY`, and a few others.

## What runs, and what doesn't yet

Running now:
- CSV ingest with per-row validation
- Deterministic resume parsing (merged tenure, skills, seniority)
- Regex JD requirement extraction
- Fit scoring with full breakdown
- Funnel derivation including ghosted-vs-open
- Fisher's exact + BH-FDR correction + confidence tiering

Not wired yet:
- **LLM JD extraction** — `core/analyze.py` uses regex + a ~15-skill vocabulary.
  Real recall needs the LLM path in `narrate.py`'s sibling module plus a larger
  `skills` table. Slot it into `_requirements_from_jd`.
- **LLM narration** — `narrate.py` exists and is tested, but the report renders
  raw statistics. Wire it once you trust the numbers.
- **Persistence** — nothing is saved. `models.py` defines the schema; add
  Alembic and a session when you want uploads to survive a refresh.

## Before you buy a domain

Two things worth doing first, because they change what you need to host:

1. **Does the analysis survive contact with a real CSV?** Your own job search,
   or a friend's. Synthetic data always cooperates.
2. **Do you need persistence at launch?** In-memory means a static host plus a
   small Python service. Persistence means a managed Postgres, and that changes
   both cost and provider choice.

A single small instance running uvicorn behind a reverse proxy handles this
workload comfortably at early scale — the compute is a few hundred milliseconds
of scipy per upload.
