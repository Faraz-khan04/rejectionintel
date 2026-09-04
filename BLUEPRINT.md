# RejectionIntel — Production Blueprint v0.2

Everything below is either built and tested in this repo, or specified precisely
enough to build. The final section reorders your five pillars, and I'd argue for
that order.

---

## 1. Database schema

The v0.1 schema (12 tables in `core/models.py`) holds up. Production needs
**eight additions**, most of them about not redoing work.

### New: `jobs` becomes a shared cache

```python
class Job(Base):
    jd_hash: Mapped[str]  = mapped_column(String(32), unique=True, index=True)
    llm_extract: Mapped[dict | None] = mapped_column(JSONB)
    extract_version: Mapped[str | None] = mapped_column(String(20))
    extracted_at: Mapped[datetime | None] = mapped_column(DateTime)
```

`jd_hash` is a hash of *normalised* JD text — whitespace collapsed, dates
stripped — so a changed application deadline doesn't force a re-extraction.
This row is **global, not per-user**. Two people applying to the same Stripe
req cost one extraction forever. It's the difference between spend that scales
with users and spend that scales with distinct postings.

### New: `resumes` tracks files, not just text

```python
    source_format: Mapped[str]  = mapped_column(String(10))   # pdf | docx | txt
    original_filename: Mapped[str | None] = mapped_column(String(255))
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    parse_warnings: Mapped[list | None] = mapped_column(JSONB)
```

`content_hash` gives you resume A/B testing for free: two uploads with the same
hash are the same resume regardless of filename.

### New: `rejection_evidence` — the OBSERVED tier

```python
class RejectionEvidence(Base):
    __tablename__ = "rejection_evidence"
    application_id: Mapped[int] = mapped_column(ForeignKey("applications.id"))
    verbatim_span: Mapped[str]  = mapped_column(String(500))   # exact employer words
    skill_id: Mapped[int | None] = mapped_column(ForeignKey("skills.id"))
    category: Mapped[str] = mapped_column(String(40))  # skill_gap|experience|comp|role_filled|boilerplate
    is_actionable: Mapped[bool] = mapped_column(Boolean)
    classifier_version: Mapped[str] = mapped_column(String(20))
```

`verbatim_span` is non-negotiable. An OBSERVED finding must quote the employer,
never paraphrase — the moment you paraphrase, it's an inference wearing an
observation's badge. `is_actionable` separates *"we needed deeper SQL"* from
*"we went with other candidates"*; expect 80–90% of rejection emails to be
boilerplate, and the UI must handle that gracefully rather than looking broken.

### New: incremental updates

```python
class Application(Base):
    ingest_batch_id: Mapped[str] = mapped_column(String(32), index=True)
    row_fingerprint: Mapped[str] = mapped_column(String(64), index=True)
    scored_at: Mapped[datetime | None] = mapped_column(DateTime)
    scored_algo_version: Mapped[str | None] = mapped_column(String(20))
```

`row_fingerprint = sha256(company|title|applied_date)` and a unique constraint
on `(user_id, row_fingerprint)` makes re-uploading a superset CSV idempotent —
the common case, since people export the whole sheet every time.

Incremental scoring is then a query, not a flag:

```sql
SELECT * FROM applications
WHERE user_id = :uid
  AND (scored_at IS NULL OR scored_algo_version != :current_version);
```

Bump `ALGO_VERSION` in `fit_score.py` and every row re-scores on next run,
automatically. No migration, no manual invalidation.

### New: job runs

```python
class JobRun(Base):
    state: Mapped[str]  = mapped_column(String(12))      # queued|running|done|failed
    steps: Mapped[list] = mapped_column(JSONB)
    result_run_id: Mapped[int | None] = mapped_column(ForeignKey("analysis_runs.id"))
```

Persisting this means a page refresh mid-analysis reconnects instead of losing
the job.

### Relationships

```
users ─┬─ resumes ─────── resume_skills ──┐
       │   (content_hash)                 ├── skills ── job_skills ── jobs
       ├─ applications ─┬─ application_events                          │
       │  (fingerprint) ├─ fit_scores (algo_version)          (jd_hash, global cache)
       │                ├─ rejection_evidence ────────────────┘
       │                └─→ jobs ──→ companies
       ├─ analysis_runs ── insights (tier, n, q_value)
       └─ job_runs
```

---

## 2. Async architecture

### What's built: `core/tasks.py`

An in-process queue behind an interface RQ can implement verbatim. Tested:
progress events, SSE streaming that emits only on change, crash handling that
never leaves a step spinning, and TTL eviction.

**I'd argue against Celery or RQ right now.** Both need Redis, a second
supervised process, and a deploy story. At one concurrent uploader with a
20–60 second job, that's infrastructure you maintain for no benefit.

The actual reason to decouple isn't concurrency — it's that a 60-second HTTP
request times out behind most proxies and shows a blank screen while it does.
An in-process queue fixes that today.

### The migration point

Move to RQ when **any** of these is true: two uploads regularly overlap; you
need retries to survive a restart; or you add scheduled work (nightly
re-analysis). The swap:

```python
# now
asyncio.create_task(run_job(job, work))

# then — everything above this line is unchanged
from rq import Queue
Queue("analysis", connection=redis).enqueue(run_analysis_task, job.id, user_id)
```

`JobStore` becomes Redis-backed, `stream_job` polls Redis instead of a dict.
The API layer and the entire frontend are untouched.

### SSE endpoint

```python
from fastapi.responses import StreamingResponse

@app.post("/api/analyze")
async def start(applications: UploadFile = File(...), resume: UploadFile | None = File(None)):
    raw, rraw = await applications.read(), (await resume.read() if resume else b"")
    job = STORE.create()
    asyncio.create_task(run_job(job, lambda j: pipeline(j, raw, rraw)))
    return {"job_id": job.id}          # returns in milliseconds

@app.get("/api/jobs/{job_id}/stream")
async def stream(job_id: str):
    return StreamingResponse(
        stream_job(job_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
```

`X-Accel-Buffering: no` matters — nginx buffers SSE by default and your stepper
will sit frozen then dump every event at once.

---

## 3. Browser extension

**Manifest V3, three components.**

*Content script* runs on `linkedin.com/jobs/*`, `greenhouse.io/*`,
`lever.co/*`, `ashbyhq.com/*`, `workday*.com/*`. Each board gets a small
adapter — a selector map plus a fallback — because they all restructure their
DOM every few months and one generic scraper breaks on all five at once:

```js
const ADAPTERS = {
  'boards.greenhouse.io': {
    title:   () => q('.app-title')?.textContent,
    company: () => q('.company-name')?.textContent?.replace(/^at\s+/i,''),
    jd:      () => q('#content')?.innerText,
  },
  // ...
};
```

*Capture trigger:* listen for the submit button, not the page URL. Confirmation
pages are inconsistent; the click is not.

*Background service worker* holds a queue in `chrome.storage.local` and posts to
`/api/applications/capture` with an API token. Queue first, send second — the
user closes the tab immediately after applying, and an in-flight fetch dies with
it.

**The design decision that matters:** capture should be **review-then-confirm**,
not silent. A toast saying *"Captured: Senior PM at Acme — undo?"* keeps the
user aware of what's being collected. Silent capture of browsing behaviour is
the thing that gets extensions removed from stores, and it contradicts a product
whose pitch is epistemic honesty.

Ship this **after** the web app has real users. It's the highest-friction
component to build and review, and it's worthless until the analysis it feeds is
proven.

---

## 4. Build order

I've reordered your pillars. Reasoning follows the list.

### Stage 1 — Make real data work (this week)

1. **Wire `ingest_v2.py`** into `api/main.py`, replacing `ingest.py`. *Built and tested.* Fuzzy column matching, 20 date formats, day-first inference, per-row errors that don't kill the batch.
2. **Wire `resume_parse.py`.** *Built.* Add `pip install pdfplumber python-docx`. The scanned-PDF detection is the important part — a silent empty extraction produces a confident report about nothing.
3. **Run your own job search through it.** Not optional and not skippable. Everything after this is guesswork until it happens.

### Stage 2 — Make findings real (week 2)

4. **Wire `extract_llm.py`.** *Built and tested.* Replace `_requirements_from_jd` in `core/analyze.py`. Measure precision/recall against 20 hand-labelled JDs before trusting it.
5. **Persistence.** Postgres + Alembic + the schema above. Nothing else in Stage 3+ works without it.
6. **OBSERVED tier.** Now that rejection text is stored, classify it. This is your only real differentiator — it's on the landing page already with no code behind it.

### Stage 3 — Make it feel finished (week 3)

7. **Async + stepper.** `core/tasks.py` and `site/ui-kit.html` are both built. Wire the SSE endpoint and lift the stepper into `app.html`.
8. **Drill-downs.** Built in the UI kit. Highest trust-per-hour of anything on this list: showing the rows behind a number is the fastest way to make someone believe it.
9. **Demo mode.** `POST /api/demo` loads `sample/sample_applications.csv` server-side and returns a job id — the same pipeline, no upload. One line of frontend. Do it before any paid acquisition; most first-time visitors have no CSV ready and currently bounce.

### Stage 4 — Only after users exist

10. **Resume A/B.** The schema supports it now. But `required_n_per_arm(0.05, 0.10)` is **432 per arm** — no individual generates that. Ship it as *descriptive* ("Resume B: 4 responses from 31") with the interval shown, never as a winner.
11. **Learning roadmap.** *"Adding Docker makes you eligible for 34% more of your failed target roles"* is honest — it's a count over your own JDs, not a prediction. Keep the phrasing at eligibility, never at outcome.
12. **Browser extension.** Highest effort, needs store review, worthless until the analysis is proven.

### Why this order

Your pillars are ordered by architectural layer. This list is ordered by **what
you'd regret not knowing sooner**.

Stage 1 is first because the product has never processed a real job search.
Every number you've seen came from a CSV I generated with a signal deliberately
planted in it — synthetic data always cooperates. A real search will break the
date parser and expose the skill vocabulary as too small, and it will tell you
whether the output is *useful* rather than merely correct.

Stage 4 sits last because A/B testing and the extension are both features whose
value depends entirely on having users, and building them first is the most
expensive way to discover you didn't need them yet.

The one item I'd move up if you insist on a quick win: **demo mode**. It's
roughly an hour and it fixes the largest funnel leak you have.
