# RejectionIntel — Architecture & Build Plan

**Version 0.1 · MVP scope: CSV in, honest dashboard out**

---

## 1. Stack

| Layer | Choice | Why |
|---|---|---|
| Backend | **FastAPI** (Python 3.12) | The product *is* a data pipeline. Keep the pipeline and the web layer in one language so there's no serialisation boundary between your analytics and your API. |
| DB | **Postgres 16** (SQLite locally) | You need JSONB for parsed blobs and real window functions for cohort stats later. Don't start on Mongo and migrate. |
| ORM | **SQLAlchemy 2.0** | Typed `Mapped[]` columns, and Alembic migrations come free. |
| Frontend | **Jinja + HTMX + Tailwind + Chart.js** | Contrarian pick, deliberate. Your UI is one upload form and one dashboard. A React SPA means building and versioning a REST contract for a single page. HTMX gets you there in a third of the time. Swap to Next.js when you add real interactivity. |
| Jobs | **FastAPI BackgroundTasks → RQ + Redis** | Processing 80 JDs takes 2–4 minutes. Start with BackgroundTasks and a `status` column the frontend polls. Move to RQ the first time a worker restart loses someone's upload. |
| LLM | **Anthropic API, Haiku for both extraction and narration** | Both calls are high-volume and low-difficulty. Force schema-shaped output via `tool_choice`; that works on every model today. Native structured outputs are in beta on some models — check `docs.claude.com` before switching. |
| Cache | **Postgres, keyed on `jd_hash`** | Don't add Redis just for this. Two users applying to the same Stripe req should cost one extraction, forever. |
| Auth | **Magic link** | No password reset flow in an MVP. |

### The one architectural rule

```
Raw → Structured Extraction → Deterministic Math → Statistics → LLM Narration
                                                                 ▲
                                                    the LLM enters HERE and
                                                    only reads what came before
```

The LLM appears twice and both are constrained:

- **Extraction** (stage 2): turns JD prose into a skill list. Output is validated against a schema and a controlled vocabulary. It cannot emit a skill that isn't in `skills`.
- **Narration** (stage 5): turns computed statistics into sentences. It receives a JSON facts payload and **nothing else** — no JD, no resume, no other applications.

Everything numeric happens in stages 3 and 4, in plain Python you can unit-test.

---

## 2. Data model

Twelve tables. Four decisions matter more than the rest:

**Applications have no `status` column.** They have an `application_events` stream, and status is derived. The most common outcome in a job search is *silence*, and you cannot honestly encode "nothing happened for 45 days" as an enum value without inventing a date. `derive_outcome()` turns events into `(furthest_stage, resolution)` and distinguishes `GHOSTED` from `OPEN` from `REJECTED`.

**`resume_id` sits on `applications`.** Even though MVP users mostly have one resume. If you ever want to compare versions, the join must already exist — you cannot backfill which resume was sent.

**Every derived number carries a version.** `fit_scores.algo_version`, `applications.derivation_version`, `analysis_runs.stats_version`. You will change the fit weights repeatedly; without this you can't tell a scoring change from a candidate change.

**`insights` stores `n`, `effect`, `p_value`, `q_value`, and `confidence_tier` next to the text.** The tier is computed once in the stats layer and persisted. It is not a rendering decision, because rendering decisions get quietly dropped in the first design review.

```
users ─┬─ resumes ──── resume_skills ──┐
       │                               ├── skills
       ├─ applications ─┬─ application_events
       │                ├─ fit_scores
       │                └─→ jobs ──── job_skills ──┘
       │                      └─→ companies
       └─ analysis_runs ──── insights
```

---

## 3. Pipeline stages

| Stage | Module | LLM? | Idempotent |
|---|---|---|---|
| 0. Ingest CSV | `ingest.py` | No | Yes |
| 1. Deterministic extract | `deterministic.py` | No | Yes |
| 2. JD → requirements | `llm_extract.py` | Yes, cached by hash | Yes |
| 3. Fit score | `fit_score.py` | No | Yes — pure function |
| 4. Statistics | `stats.py` | No | Yes |
| 5. Narration | `narrate.py` | Yes, validated | Yes |

Stages are separately re-runnable. Changing the fit weights (stage 3) must never mean re-paying for stage 2.

### The honesty gates

These are the product, not a compliance layer:

- `MIN_RESOLVED = 25` — below this, no pattern analysis runs at all. Show the funnel and say so.
- `MIN_ARM_SIZE = 8` — no 2×2 test on a tiny arm.
- **Benjamini–Hochberg FDR** across every test in a run. Test 25 attributes on 80 applications and you'll find 1–2 "significant" patterns by pure chance. This is the difference between analytics and a random number generator with a confident voice.
- **Wilson score intervals** on every rate. At n=10 with 1 success, the normal approximation puts the lower bound below zero.
- `ConfidenceTier.INSUFFICIENT` is a **first-class, displayable result.** "We don't know yet" must be a screen you designed, not a fallback.

Verified against 300 simulated users whose outcomes were pure noise: **0.02 false STRONG findings per user.** Without FDR correction that number is roughly 20× higher.

### What is deliberately NOT built

- **Per-user resume A/B testing.** `required_n_per_arm(0.05, 0.10)` returns **432 per arm** — 864 applications to detect a *doubling*. Nobody's job search generates that. `minimum_detectable_effect(40, 0.05)` returns a 27% response rate, i.e. at 40 applications per arm you could only detect a 5× effect. The function is in `stats.py` so you can show users this honestly instead of shipping a demo that lies.
- **"Rejected at ATS stage" percentages.** A fast rejection is a *fast rejection*. It is not evidence of an automated screen. Report `days_to_first_response` and let the user interpret it.
- **Predicted lift** ("18% → 34%"). Requires a counterfactual you'll never have.

---

## 4. Execution roadmap

Each step ships something you can look at. Nothing here needs a team.

### Week 1 — Prove the math before building a product

1. **Hand-label 20 JDs yourself.** Extract required vs preferred skills manually. This is your extraction ground truth and it takes an afternoon. Skipping it means you'll never know if stage 2 works.
2. **Run the deterministic modules against your own job search** (or a friend's). No web app, no database — just a script and a CSV.
3. **Look at the fit scores and ask: do these ranks feel right?** If a job you were obviously perfect for scores 40, the weights are wrong. Fix them now, while it costs nothing.

> Gate: if you can't produce fit scores that a human agrees with on 20 applications, the product doesn't work and no amount of frontend will fix it.

### Week 2 — Storage and ingest

4. Postgres + Alembic + the schema in `models.py`.
5. CSV upload → validation report → rows in the database. Ship the *error report* UI first; it's what users will actually see most.
6. Seed the `skills` table with ~200 canonical skills and aliases for your target function (start with one: PM, or backend eng — not both).

### Week 3 — LLM extraction

7. JD → structured requirements, forced tool schema, output constrained to your canonical vocabulary.
8. Cache on `jd_hash`. Log every extraction with its cost.
9. **Measure against your Week 1 hand labels.** Report precision/recall. If required-skill recall is under ~0.8, fix the prompt before moving on.

### Week 4 — Statistics and the dashboard

10. Wire `run_analysis()` to real data.
11. Build three screens: **the funnel**, **the fit-score gradient**, **findings by tier**.
12. Build the `INSUFFICIENT` screen properly. Most early users will see it. It should feel like a progress bar, not an error.

### Week 5 — Narration and first users

13. Narration with the numeric guard. Log every validation failure — that log tells you where your prompt is weak.
14. **Ten users you can talk to.** Not a launch. Watch them read the dashboard and note where they say "wait, what does that mean?"

### Week 6 — Decide

15. Instrument one thing: *do users change behaviour after reading a finding?* If nobody acts on the output, the analysis is entertainment.

### After the MVP

The individual-level statistics will always be weak — that's arithmetic, not engineering. The same tests run across 500 users of one bootcamp cohort have real power. **The cohort is both where the math starts working and where someone has budget.** Design the schema now so `analysis_runs` can take a cohort filter instead of a `user_id`; that's the only forward-looking change worth making today.

---

## 5. Cost per user (order of magnitude)

80 JDs × ~1,200 input tokens + ~300 output, Haiku pricing, plus ~15 narration calls: **cents, not dollars.** Cache hits drive it lower as the corpus grows. This is not a cost-constrained product; it's a trust-constrained one.

---

## 6. Repository layout

```
rejectionintel/
├── core/
│   ├── enums.py          # domain enums, zero framework imports
│   ├── models.py         # SQLAlchemy schema (12 tables)
│   ├── ingest.py         # CSV contract + validation
│   ├── deterministic.py  # seniority, tenure, skills, funnel derivation
│   ├── fit_score.py      # pure scoring function
│   ├── stats.py          # Wilson, Fisher, BH-FDR, power
│   ├── narrate.py        # constrained LLM + numeric guard
│   └── pipeline.py       # orchestration
├── api/                  # FastAPI routes (not yet written)
└── tests/
    └── test_pipeline.py  # 30 checks incl. false-positive simulation
```

`core/` has no web dependency. You can run the entire analysis from a script,
which is exactly how you should develop it.
