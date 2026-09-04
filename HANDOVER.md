# RejectionIntel — Handover

Everything a fresh session needs. All figures were measured from the running
code, not recalled.

**Read sections 1, 2, 9 and 10 before writing anything.**

---

## 1. What this is

Job-search analytics. A person uploads a spreadsheet of their applications plus
a resume; the system derives their funnel, tests pre-registered hypotheses
against it, corrects for multiple comparisons, and narrates the result in plain
English.

The distinguishing feature is **the refusal to overclaim**. "We don't have
enough data to say" is a designed screen with its own copy, not a failure state.
Four structural guards make it impossible for the language model to invent a
number, a quote, or a metric. Every threshold that hides a feature until there
is enough evidence is deliberate and must not be lowered to make a demo look
fuller.

The emotional thesis, which came out of reading job-search forums: people are
not damaged by rejection, they are damaged by **silence** — by not knowing
whether the problem was them. The product's job is to name what happened
precisely and, where the data supports it, move blame off the person and onto
something external and true.

---

## 2. Non-negotiable rules

Break any of these and the product stops being what it is.

1. **The AI never produces a number.** If a figure can be computed in Python, it
   is. A model-produced score would eventually disagree with `compute_fit()` for
   the same input and neither could be defended.
2. **No statistical jargon in user-facing text.** No "CI", "p-value",
   "confidence interval", "n=", "FDR", "significant". `plain_language.audit()`
   scans for leaks. It has caught regressions three separate times, most
   recently in `funnel.js`, which was written separately and missed two sweeps.
3. **No internal identifiers on screen.** Not `fit_score >= 80`, not
   `requires:SQL`, not `api_error`.
4. **No CSS framework, no build step.** Vanilla JS, D3, custom CSS variables.
5. **Every displayed number must reconcile with the sentence beside it.** A
   headline figure the reader cannot derive from the words underneath reads as a
   bug even when it is arithmetically correct. This caused a real fix — see 9.3.
6. **Gates are features.** Minimum sample sizes exist to protect the claim.
7. **The share card carries counts and rates only**, audited against the
   report's own company names, job titles and quotes before it ships.
8. **Advice must be actionable and safe.** Where nothing follows from a finding,
   say nothing.

---

## 3. Stack and scale

Python 3.13, FastAPI, SQLAlchemy 2.0, SQLite (Postgres-tested), optional Gemini
or Claude. Frontend is vanilla JS with D3 self-hosted at `/d3.min.js` — a CDN
was silently blocked by Brave shields and the chart degraded to flat bars
without erroring, which is why nothing is loaded from a CDN.

```
34 core modules · 11,495 lines Python · 5,881 lines frontend
39 endpoints · 19 tables · 4 test suites · 6 pages
```

---

## 4. Architecture

```
CSV ─► ingest ─► deterministic maths ─► statistics ─► AI narration
                                                          ▲
                                        the model enters ONLY here
```

The model does exactly two things: parses job-description prose into a typed
schema, and turns finished numbers into sentences. It never scores, ranks,
predicts or decides.

### The four guards

1. **Numeric** (`narrate.py`) — every number in AI output must exist in the
   facts payload. A violation rejects the draft, retries once, then falls back
   to a deterministic template.
2. **Verbatim** (`evidence.py`) — an employer's stated reason must be a literal
   substring of their message. A paraphrase is an inference wearing a quotation.
3. **All-or-nothing extraction** (`extract_llm.py`) — a report is entirely
   model-parsed or entirely vocabulary-parsed, never mixed. Mixing shifts fit
   scores by roughly 27 points and manufactures a pattern that is an artefact of
   the parser.
4. **Invented-metric** (`resume_diff.py`) — resume suggestions are shapes with
   blanks: `[e.g. response time down to __ms]`. Any digit absent from the source
   line is rejected.

### Key modules

| File | What it owns |
|---|---|
| `ingest_v2.py` | Fuzzy column matching, 20 date formats, encoding fallback, plausibility bounds |
| `deterministic.py` | Seniority, tenure merging, skill extraction, funnel derivation |
| `fit_score.py` | Pure scoring: required 0.40, experience 0.20, seniority 0.20, preferred 0.10, industry 0.10 |
| `stats.py` | Wilson intervals, Fisher's exact, Benjamini–Hochberg |
| `pipeline.py` | Pre-registered hypotheses, FDR across the whole run, dedupe by membership |
| `verdict.py` | One sentence, one number. Bottleneck ranked by conversion health |
| `insights.py` | Stage split, pace as a **floor not a forecast**, learning estimates |
| `cohort.py` | Benchmarks, employer reply index, group reports — all gated |
| `ghost.py` | Employers who never reply, from outcomes not listing text |
| `pre_apply.py` | One resume vs one JD. Model extracts, Python scores |
| `vocabulary.py` | 108 skills, 409 aliases, 16 domains |
| `plain_language.py` | 34-term glossary, `soften()`, `audit()` |
| `mailer.py` | Resend / SMTP / console, injectable transport |

### Data model notes

- Applications have **no status column**. Position is derived from an event
  stream. Moving a tracker card backwards **deletes** later events rather than
  writing a contradicting one.
- The tracker writes to the **same `applications` table** as the CSV importer,
  deduped by `row_fingerprint` (company | title | date).
- `jd_extractions` is separate from `jobs` — see 9.1.

### Thresholds

```
stats:   MIN_RESOLVED 25 · MIN_ARM_SIZE 8 · MIN_SUCCESSES_TOTAL 3
         FDR_ALPHA 0.10 · MIN_RISK_RATIO 1.5
cohort:  MIN_BENCHMARK_USERS 20 · MIN_USER_APPLICATIONS 10
         MIN_EMPLOYER_USERS 4 · MIN_EMPLOYER_APPS 8
         MIN_COHORT_SIZE 8 · K_ANON 3
```

Validated against 300 simulated users drawn from pure noise:
**0.02 false "strong" findings per user.**

---

## 5. What is built

**Do not rebuild any of this.** An earlier roadmap listed several of these as
upcoming work; they are finished.

### Core report (`/app`)
Verdict → benchmark ("is this normal?") → progress vs last visit → action plan →
D3 funnel → two questions → pace → facts → observed quotes → patterns → skill
gaps → resume diff → ghost signals → share card → every application.

### Retention loop
Verdict names one bottleneck → 2–3 tracked actions with persistent ticks →
upload more → comeback screen compares against a stored snapshot, reading it
**before** writing the new one, with an explicit caveat that causation cannot be
proven.

### Ghost check (`/ghost`)
Zero-friction front door — paste an advert, no signup. Five listing heuristics
plus, for signed-in users, **what their own history says about that employer**.
The strong case: a clean-looking advert (0/5 signals) from a company that
ignored them nine times. No competitor can compute that.

### Cohort features (`/cohort`, `/employers`)
- **Benchmarks** — "your reply rate is normal", gated at 20 users
- **Employer reply index** — gated at 4 independent applicants, reported as
  counts with intervals, explicitly framed as *not* an accusation
- **Group reports** — opt-in by join code, k-anonymity 3, gated at 8 members

### Tracker (`/track`)
Kanban, event-driven, deduped against CSV imports.

### Pre-apply analyzer (`/api/analyze-jd`)
Model extracts requirements with a verbatim `evidence` span; anything not found
in the advert is dropped. Python computes the score with the same
`compute_fit()` the report uses.

### Infrastructure
Passwordless auth, SSE progress streaming, in-process queue, Postgres-verified,
mailer built, four test suites.

---

## 6. Tests

```
python -m tests.test_pipeline         maths, 31 checks + 300-user noise sim
python -m tests.test_routes           HTTP layer, 31 checks (needs httpx)
python -m tests.test_dom_order        no listener binds before its markup
python -m tests.test_share_redaction  share card leaks nothing
```

`test_routes.py` exists because unit tests prove a guard works in isolation;
they cannot prove the route still calls it. It covers auth round-trip including
**magic-link replay**, the four guards over HTTP, SSE, access control, 16
concurrent writes, and malformed input.

---

## 7. Environment

```
GEMINI_API_KEY / ANTHROPIC_API_KEY / LLM_PROVIDER=gemini|anthropic|none
DATABASE_URL=            # sqlite default; postgresql+psycopg://... for prod
SECRET_KEY= / COOKIE_SECURE=
QUEUE_BACKEND=rq / REDIS_URL=
RESEND_API_KEY= / MAIL_FROM= / APP_URL=
EASY_STYLE=coach|hinglish
```

```
python run.py                     # from project root, restart after edits
python -m sample.seed_demo        # 40 fictional users for the shared views
python -m sample.seed_demo --clear
```

Windows, `C:\rejectionintel`. Brave (blocks CDNs — self-host everything).
Git repo exists with tags `blue-accent` and `violet-accent`; see `ROLLBACK.md`.

---

## 8. Working practices that were learned the hard way

- **Commit before any large change.** A stylesheet was destroyed mid-session and
  recovered only because a served copy happened to be sitting in a temp folder.
  That was luck. The repo now exists; use it.
- **Verify with a real parser, not a heuristic.** Brace-counting reported
  `depth 0` on a CSS file that was structurally destroyed. Balanced braces are
  not valid CSS. Use an actual CSS/JS parser.
- **Never regex-delete lines that are only `}`.** That is how every
  well-formatted rule closes. This deleted 25 legitimate closers.
- **Check names before using them.** Four bugs in one feature came from assuming
  `complete_json`, `get_provider`, `JobRequirements`'s module, and that
  `seniority=None` was acceptable.
- **The server takes ~25s to restart.** Poll rather than assuming.

---

## 9. Bugs found and fixed — worth knowing about

**9.1 Company attribution was silently corrupted.** `store.py` looked up jobs by
`jd_hash` alone and the column was globally unique, so two employers posting
similar boilerplate **shared one Job row** and every application inherited the
first company's ID. A seeded test showed 12 companies, 1,187 applications, and
**one job row**. Fixed by splitting `jd_extractions` (a property of the text)
from `jobs` (a property of the company), unique on `(company_id, jd_hash)`.

**9.2 Concurrent startup crashed.** Six processes calling `create_all()`
together: **five died** with "table users already exists". `checkfirst` is a
check-then-create with a race window. This is exactly the RQ scenario. Fixed
with a file lock and treating "already exists" as success.

**9.3 The verdict number could not be reconciled.** It showed "31%" above a
sentence reading "43 replied, only 14 moved on" — 31 was 29/96, a share of *all*
applications. Now the share of those who **reached that stage**: 67% of 43.

**9.4 The vocabulary had 15 entries, all product-management.** A backend advert
parsed to almost nothing, so an engineer's report was confidently wrong with no
sign anything was missed. Now 108 skills. Two traps found while expanding:
short canonicals matching English words ("Go" in "go beyond"), and **"hiring" as
an alias for Recruiting**, which fired on 96 of 96 adverts.

**9.5 Auth leaked account existence.** `/api/auth/request` responded differently
for known and unknown addresses — an enumeration oracle revealing who uses a
job-search tool. Responses are now identical.

**9.6 Two animations on one element.** `.orb` declared both `orbSpin` and
`orbBreathe`; both drive `transform`, so the last one silently won. Split into a
wrapper.

---

## 10. What to do next

### Priority 1 — run twenty real applications through the ingest
**This is the blocker and it needs the user, not the assistant.**

Every number this system has produced came from data generated to cooperate:
clean dates, skill names that match the vocabulary, outcomes drawn from a
distribution chosen in advance. Real applications will break things no
infrastructure work touches. Doing anything else first optimises the wrong
thing.

### Priority 2 — Postgres
Tested and documented in `POSTGRES.md`. One environment variable plus
`pip install "psycopg[binary]"`. **Must happen before RQ**, since multi-process
writes are the landmine.

### Priority 3 — activate RQ
Adapter exists in `queue_rq.py`. `tasks.active_store()` already routes SSE reads
to Redis when enabled.

### Priority 4 — email delivery
Built and tested against a fake transport. Needs a domain, a provider key, and
SPF/DKIM/DMARC records. A sign-in link in a spam folder is indistinguishable
from a broken product.

### Then
- **Syllabus mapping** — strongest unbuilt B2B feature; maps cohort skill gaps
  to coursework. Turns a gap report into a curriculum decision.
- **Semantic analysis** — must cite literal evidence spans and be labelled as
  interpretation, or it reintroduces the overclaiming the guards prevent.

### Explicitly cut
**"Alert users when a market sector is cooling."** A market prediction on data
volume that will not exist for years, and it contradicts every other decision in
the product. The honest version already exists: the employer index reports what
happened, with intervals, gated on sample size.

---

## 11. Known weaknesses — state these plainly

- **Never processed a real job search.**
- No users; email needs a domain.
- Every hypothesis tests *response*. Nothing tests *given a reply, did they
  progress* — so a user whose bottleneck is post-reply gets an **empty Patterns
  tab by construction**. Fixing this needs stage-conditional hypotheses.
- No cross-source consistency (CV vs LinkedIn).
- Single-process queue by default.
- No licence file. Default copyright applies.

---

## 12. Design status — closed for now

The palette is steel blue (`#255C99` light, `#5AA9E6` dark) with four semantic
tier colours that encode confidence and must not be repurposed for branding:
observed green `#0E8A5F`, weak amber `#C4820E`, insufficient grey `#78809A`,
negative red `#C4433F`.

A long exploration of alternatives (matte, mint, ochre, graphite, newsprint,
"confidence-as-chroma") ended with the user saying **leave it**. Do not reopen
this unless asked. If asked, the useful finding was that the problem was never
the hue — it was structural: too many components, uniform density, and the
standard AI-landing-page vocabulary (orb, pill, gradient-clipped headline,
bordered cards). The fix is subtractive, not chromatic.

`git checkout violet-accent -- site/` returns to the previous palette.
