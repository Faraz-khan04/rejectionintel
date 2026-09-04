# RejectionIntel — What Exists and How It Works

A complete account of the system as built. Every figure here was measured from
the running code, not estimated.

**Scale:** 8,816 lines of Python across 28 modules, 4,565 lines of frontend,
25 HTTP endpoints, 16 database tables, 3 test suites.

---

## Table of contents

1. [What the product does](#1-what-the-product-does)
2. [The architecture rule](#2-the-architecture-rule)
3. [Getting data in](#3-getting-data-in)
4. [Understanding the data](#4-understanding-the-data)
5. [The maths](#5-the-maths)
6. [The AI layer and its four guards](#6-the-ai-layer-and-its-four-guards)
7. [What the user actually sees](#7-what-the-user-actually-sees)
8. [The retention loop](#8-the-retention-loop)
9. [Infrastructure](#9-infrastructure)
10. [Testing](#10-testing)
11. [What isn't built](#11-what-isnt-built)

---

## 1. What the product does

Someone applies to a hundred jobs and gets rejected. Nobody tells them why.

RejectionIntel takes their application history and their resume, and reports
where they are actually losing — with an explicit statement of how confident it
is, and a refusal to answer when the data can't support one.

**Three ways in, because most people don't have a tidy spreadsheet:**

| Entry point | For someone who has | Route |
|---|---|---|
| Full analysis | A spreadsheet of applications | `/app` |
| Pre-apply check | A resume and one job ad | `/app` → Check one job |
| Built-in tracker | Nothing yet | `/track` |

---

## 2. The architecture rule

```
Raw data → Structured extraction → Deterministic maths → Statistics → AI narration
                                                                          ▲
                                                    AI enters HERE, read-only
```

Every number in the product is computed in ordinary Python that could be
re-derived by hand. AI appears exactly twice: parsing job descriptions into
structured requirements, and turning finished figures into sentences.

**It never scores, predicts, or decides.**

This ordering is the product. It's why the guards in section 6 are possible at
all — you cannot check a model's arithmetic if the model did the arithmetic.

---

## 3. Getting data in

### 3.1 CSV ingest — `core/ingest_v2.py` (470 lines)

Built for spreadsheets a human maintained by hand, not for clean exports.

**Column matching** runs exact → alias → fuzzy using `SequenceMatcher` at an
0.82 threshold. `"Comapny"` resolves to `company` at 86% confidence. Each header
is consumed once; when two compete for one field, the better score wins. Fuzzy
matches surface as warnings, so nothing is silently assumed.

**Date direction is decided once per file, not per row.** `infer_day_first()`
scans the whole column for any value with a first component above 12 — that
proves day-first. A single file is written by one person in one convention, so
guessing per row would produce a file where March 4th and April 3rd both appear.

Twenty date formats are recognised, including `22nd Mar 2026`, `14.03.2026`, and
`5 days ago`.

**Outcomes are matched strictly — no fuzzy matching.** `"maybe?"` is rejected
rather than mapped to something plausible, because a wrong funnel stage silently
corrupts every downstream statistic.

**Bad rows don't kill the batch.** Each is reported with its row number, the
column, and what went wrong. Measured on a deliberately messy file: 10 of 12 rows
accepted, two dropped with named reasons, two typo'd headers resolved.

### 3.2 Pasted data — `/api/preview`

Copying cells out of Sheets, Excel or Notion produces tab-delimited text, which
`csv.Sniffer` already handles. `/api/preview` parses it and returns what was
understood *before* anything is committed: which columns matched, how confidently,
which rows are broken, and a sample of parsed output.

### 3.3 Resume parsing — `core/resume_parse.py` (167 lines)

PDF via `pdfplumber`, DOCX via `python-docx`, plus plain text. Dispatch is on
magic bytes first, extension second — people rename files.

**The failure that matters:** a scanned PDF has no text layer. `pdfplumber`
returns `""`, and every downstream step then builds a confident report about an
empty skill set. Below 220 characters the parser refuses the file with an
actionable message rather than degrading silently.

DOCX parsing reads table cells too, because many resumes use tables for layout
and skipping them loses the entire body.

### 3.4 The tracker — `core/tracker.py` (241 lines)

Six columns: Applied → Recruiter call → Interview → Final round → Offer → Closed.
Drag-and-drop on desktop, a dropdown on mobile.

**The architectural decision:** a tracked job *is* an application. It writes to
the same `applications` table the CSV importer writes to. Moving a card appends
an `ApplicationEvent` rather than setting a status field.

A separate `tracked_jobs` table would give two sources of truth, a funnel that
disagrees with the board, and eventually a sync job. Proven instead:

```
CSV rows uploaded : 112
total stored      : 124   (112 + 12 added by hand)
analysed          : 124
```

Adding by hand also dedupes against the importer's fingerprint, so importing a
spreadsheet later that contains the same job won't duplicate it.

**Column position is derived, never stored.** No column holds "which column am I
in" — it comes from the event stream. A stored status would drift the moment
anything else touched the row.

**Moving backwards deletes the events ahead of the target** rather than adding a
contradicting one. Otherwise a mis-drag leaves an application simultaneously
rejected and in a final round, and the funnel counts it twice.

---

## 4. Understanding the data

### 4.1 The event stream — `core/deterministic.py` (294 lines)

**Applications have no `status` column.** They have a stream of events, and
status is derived.

This is the central modelling decision. The most common outcome in a job search
is *silence*, and you cannot honestly encode "nothing happened for 45 days" as a
status enum without inventing a date.

`derive_outcome()` collapses events into `(furthest_stage, resolution)` and
distinguishes:

- **REJECTED** — an explicit rejection was received
- **GHOSTED** — no terminal event, past the 45-day silence threshold
- **OPEN** — still plausibly live, **excluded from all statistics**

Excluding OPEN matters: counting them biases results against recent applications
that simply haven't had time to reply.

**Overlapping tenure is merged, not summed.** `merge_tenure_months()` collapses
concurrent roles. Most resume parsers sum durations and inflate experience 10–25%.
Measured: 36 months merged versus 48 naive.

### 4.2 Job description extraction — `core/extract_llm.py` (347 lines)

AI reads each posting into a Pydantic schema: required vs preferred skills,
seniority, years, industry. Schema violations are rejected, not repaired —
unknown seniority defaults to mid, a reversed year range drops the max, negative
years raise.

**Canonicalisation happens after extraction.** `"Postgres"`, `"PostgreSQL"` and
`"psql"` collapse to one skill. Without this, one skill splits into three
underpowered hypotheses and none survives correction.

**Caching is keyed on a content hash of normalised JD text** — whitespace
collapsed, dates stripped, so a changed application deadline isn't a cache miss.
The cache is **global, not per-user**: two people applying to the same Stripe req
cost one extraction, forever. Cost scales with distinct postings, not with users.

### 4.3 The extraction plan — the subtlest decision in the product

Gemini's free tier allows ~20 requests/day. One report needs 88.

The obvious fix is "extract what you can afford, regex for the rest." **That is
unsafe.** AI extraction finds far more required skills than a keyword vocabulary
does — same candidate, same job, fit scored **97 versus 70**.

Mix both inside one report and every AI-parsed application scores lower for
reasons unrelated to the candidate. The statistics layer would faithfully detect
that artefact and narrate it as a real pattern.

So extraction is **all-or-nothing per report**. Below full cache coverage the
system warms the cache in the background and scores everything with the weaker
uniform method. Measured warming, with a simulated restart between each run:

```
run 1: 20 calls · cached 20/88 · method = regex
run 2: 20 calls · cached 40/88 · method = regex
run 3: 20 calls · cached 60/88 · method = regex
run 4: 20 calls · cached 80/88 · method = regex
run 5:  8 calls · cached 88/88 · method = llm
run 6:  0 calls                · method = llm
```

Uniformly weak beats unevenly strong when the output is statistics.

---

## 5. The maths

### 5.1 Fit scoring — `core/fit_score.py` (186 lines)

A pure function. Same inputs, same output, forever.

| Component | Weight | Notes |
|---|---|---|
| Required skills | 40% | Half credit if listed but not evidenced in a bullet |
| Preferred skills | 10% | |
| Experience | 20% | Penalises under **and** over qualification |
| Seniority | 20% | Distance in bands |
| Industry | 10% | |

The skills-in-bullets discount is a design opinion, applied consistently and
visible in the breakdown. A screener reads a skill inside an achievement
differently from one in a comma-separated blob.

Every score carries `algo_version`, so a score change can be told apart from a
candidate change.

### 5.2 Statistics — `core/stats.py` (263 lines)

**The central problem:** a user with 80 applications and a 12% response rate has
~10 successes. Test 25 attributes against that and 1–2 will look "significant"
by pure chance.

Four defences:

1. **Minimum cell counts** — no test on a 2×2 with an arm below 8
2. **Benjamini-Hochberg FDR correction** across *every* test in a run, applied at
   once (not per finding)
3. **Wilson score intervals** on every rate — correct at small n, unlike the
   normal approximation which puts the lower bound below zero at n=10, k=1
4. **`INSUFFICIENT` is a first-class, displayable result** — "we don't know yet"
   is a screen that was designed, not an error state

**Fisher's exact test**, not chi-square, because cell counts here are small by
nature.

A finding reaches `STRONG` only if it survives FDR **and** shows a meaningful
effect size **and** has non-overlapping confidence intervals. On demo data a
finding with q=0.069 — clearing the 0.10 threshold — is still labelled *weak*
because the intervals touch.

**Hypotheses are pre-registered and few.** Adding one costs statistical power, so
it's a product decision, not a loop. They're also deduplicated by *membership*:
`requires:Looker` and `missing:Looker` select identical applications when the
candidate lacks the skill, so they're one hypothesis wearing two labels.

### 5.3 Power — the honest refusal

`required_n_per_arm(0.05, 0.10)` returns **432**. Detecting a doubling of a 5%
response rate needs 432 applications per resume version — 864 total.

`minimum_detectable_effect(40, 0.05)` returns **27%**: at 40 per arm you could
only detect a five-fold effect.

Tested with a deliberately planted 2× effect:

```
Resume A   n=44   20.5%
Resume B   n=44   38.6%
verdict: not enough data to call it — needs ~95 per version
```

Observed rates nearly doubled and it still refuses to name a winner, because the
intervals overlap. Every competitor ships that as "Resume B wins by 88%."

### 5.4 The verdict — `core/verdict.py` (320 lines)

One sentence, one number, naming the stage where most applications stop.

**A bug worth recording:** ranking stages by absolute drop always accuses the top
of the funnel, because that's where the denominator is. A candidate with a 50%
response rate who reaches finals and gets no offers was being told "most of your
applications never reach a human."

The fix discards stages already converting acceptably before naming the worst.
Verified across five funnel shapes, each landing on the correct stage.

### 5.5 Pace — a floor, never a forecast

"How long until I get a job?" is the most-asked question and the one the data
cannot answer. With zero offers observed, any month figure is invented.

What the data *does* support is arithmetic on measured rates:

```
7 days to hear back  ·  5.3 applications/week  ·  0 offers in 112
→ one offer is at least ~30 applications away, about 1 month minimum
```

The derived figure uses the **upper bound** of the offer-rate interval, which is
what makes it a floor. The point estimate would read like a promise.

---

## 6. The AI layer and its four guards

### Guard 1 — The numeric guard (`core/narrate.py`)

Every number in the facts payload is harvested, including rounded and percentage
forms. Any AI output containing a number outside that set is rejected, retried
once with the error, then falls back to a deterministic template.

Caught in testing: an invented "52%", a causal claim, and hype language on a weak
finding — while allowing the same hype on a strong one.

**Each finding gets its own API call, never batched.** Batching would be cheaper
but breaks the guard: with nine findings merged, a figure from finding #3 is
legitimately in the combined allow-list and can migrate into finding #7's
sentence, pass validation, and be wrong.

### Guard 2 — The verbatim guard (`core/evidence.py`)

An OBSERVED finding must quote the employer literally. A paraphrase presented as
an observation is an inference wearing a badge.

Tested with a model that paraphrases. Source: *"we needed more hands-on SQL."*
Model returned: *"they wanted stronger SQL skills"* — accurate in meaning,
confidence 0.7. **Rejected.**

A deterministic boilerplate screen runs first, at zero quota cost. On real
rejection emails it filtered 3 of 9; on demo data, 5 of 16. Text matching neither
boilerplate nor signal patterns is sent anyway — better to spend a call than
discard a real reason.

### Guard 3 — All-or-nothing extraction

Covered in 4.3. The 27-point fit-score gap between methods is why.

### Guard 4 — The invented-metric guard (`core/resume_diff.py`)

The highest-risk feature. Elsewhere an overclaim produces a misleading chart;
here it produces a sentence someone pastes onto a real resume and defends in an
interview.

Suggestions are **shapes with blanks**. `verify_suggestion()` rejects anything
introducing a digit absent from the source line:

```
REJECT  "Cut onboarding time by 40%"     invents 40
ACCEPT  "Cut onboarding time by [X]%"    blank for the user
REJECT  "Owned billing for 12 teams"     invents 12
REJECT  "Built reporting"                no slot for a result
```

Placeholders are **context-aware** across 14 domains — Java backend gets
`[e.g. response time down to __ms]`, ML work gets `[e.g. accuracy up __ points]`
— naming what to measure without supplying a figure.

**A bug this caught:** resume bullets wrap across lines, and the metric usually
sits at the *end*. Reading line-by-line reported "no measurable outcome" on a
resume full of percentages. Every flag was a false positive.

### Plain language — `core/plain_language.py` (358 lines)

A 34-term glossary plus `soften()`, applied to every user-facing string. Jargon
leaks through template fallbacks and error messages, and a prompt guards only one
path.

`audit()` scans for internal identifiers that must never reach a screen —
`fit_score >= 80`, `requires:SQL`, `q = 0.019`, `api_error`. It's a regression
test, not a comment.

Two registers ship in one payload, so the Easy-language toggle is instant with no
request. Both go through the numeric guard: an easy version that invents a figure
is exactly as wrong, and it's the one more people read.

---

## 7. What the user actually sees

### The report, in order

1. **Verdict** — one sentence, one number, the bottleneck named
2. **Since last time** — movement, if there's a previous run
3. **Your plan** — 2–3 tracked actions with checkboxes
4. **Funnel** — D3 chart with confidence bands
5. **Two questions** — the funnel split into reply-stage and offer-stage
6. **Pace** — reply time, weekly rate, the floor
7. **Facts** — six counts, no inference
8. **Observed** — employer quotes, verbatim
9. **Patterns** — top 3 by gap size; the rest collapsed
10. **What's holding you back** — skill gaps with learning timeline
11. **Resume, line by line** — the diff
12. **Share** — the redacted card
13. **Every application** — the table

### The D3 funnel — `site/funnel.js`

Every funnel library draws a point estimate: one shape, total confidence. Each
stage here renders twice — a translucent flare spanning the interval, and a solid
band at the observed value.

The Offer row demonstrates it: zero out of 88 renders a **22px flare around a
zero-width band**, because "no offers yet" means the true rate could be up to 4.2%.

Widths are proportional to **count**, not rate-relative-to-max. A funnel that
rescales each stage to fill the frame looks better and lies about the drop.

### Traffic lights

One `fitBand()` function decides what a score means, so nothing can disagree
about whether 68 is good.

| Band | Range | Verdict |
|---|---|---|
| Green | 80–100 | Strong match — worth a tailored application |
| Amber | 60–79 | Stretch role — tighten the resume first |
| Red | 0–59 | Missing core skills — save the time |

### Pattern culling

Top 3 held-up findings, ranked by **gap size** rather than test order. A pattern
that survives correction but moves the rate two points is technically real and
practically useless.

Everything else collapses into "Explore deeper insights (n)" — collapsed, not
deleted, with the count visible.

---

## 8. The retention loop

A tool used once is consumed, not retained. The loop is:

**Diagnose → commit to one change → apply 20 more → see whether the number moved.**

**The action plan** ranks by evidence strength: what an employer wrote beats what
statistics found beats a count over job ads. Capped at three — a list of ten
actions is a list of zero.

Actions are keyed by a stable string, so ticking survives re-analysis. Verified:
tick two, upload 26 more, re-analyse — ticks persisted.

**The comeback screen** compares against a stored snapshot. Read *before* the
current one is written, or the run compares against itself.

**The honesty problem here is the biggest in the product.** A before-and-after has
no control group. If someone changed their resume and the rate rose, we cannot say
the change caused it. Claiming credit would be the largest overclaim available,
and the one users would most want to believe.

So the caveat is part of the finding:

> We can't prove that's what moved the number — the roles were different too, and
> 26 applications is still a small batch. But it's the direction you wanted.

It also reported **8 new applications, not 26**, because unresolved recent
applications are excluded.

**The share card** carries counts and rates only. Audited against **232 identifying
strings** from the user's own report — company names, job titles, rejection quotes,
resume lines — not against a guess at what identifying data looks like. If the
audit fails, no card ships.

---

## 9. Infrastructure

**Stack:** Python 3.13, FastAPI, SQLAlchemy 2.0, SQLite (Postgres-ready), Gemini
or Claude, vanilla JS + D3.

**16 tables.** Notable: `applications` with `row_fingerprint` for idempotent
re-upload; `jobs` as a global JD cache; `action_items` and `snapshots` for the
loop; `rejection_evidence` with mandatory `verbatim_span`.

**A trap worth recording:** `create_all()` creates missing *tables*, never missing
*columns*. Adding a field appears to work until the first query dies with
"no such column" — by which point there's real data. `_add_missing_columns()`
runs an additive migration on startup; tested against a simulated old database,
it added six columns and preserved every row.

**Background jobs** with SSE streaming. POST returns a job id in **160ms**;
progress streams as named stages. An in-process queue by default; `queue_rq.py`
provides a Redis-backed implementation behind the same interface, opt-in via
`QUEUE_BACKEND=rq`.

**Auth** is passwordless — HMAC-signed cookies, single-use magic links expiring in
15 minutes, per-user revocable API tokens. Anonymous sessions get a real user row
immediately, so demo mode works on the first click. Tested: tampered body,
swapped signature, expired token, replayed link — all rejected.

---

## 10. Testing

**`test_pipeline.py`** — 31 checks. The important one: **300 simulated users whose
outcomes were pure noise**. Result: **0.02 false strong findings per user**.
Without FDR correction, roughly 20× higher.

**`test_dom_order.py`** — a static guard. Twice, a listener was bound to an element
whose markup appears later in the document. The element is null, the statement
throws, and every statement below it silently never runs. The visible symptom was
`Cannot access 'EASY' before initialization` — a variable in an unrelated file.

**`test_share_redaction.py`** — proves the share card carries nothing identifying,
and proves the audit isn't a no-op by planting a leak.

**Beyond the suites:** the full page is executed in jsdom exactly as a browser
would, in document order. That's what caught the null-listener bug my earlier stub
couldn't — the stub returned an object for every `getElementById`, so nothing was
ever null. **My test was more forgiving than a browser.**

---

## 11. What isn't built

**It has never processed a real job search.** Every number in this document came
from synthetic data generated with a signal deliberately planted in it. Synthetic
data always cooperates. A real search will break the date parser and expose the
skill vocabulary as too small — and, more importantly, reveal whether the output
is *useful* rather than merely correct.

Also missing:

- **No users.** Working product, not a launched one.
- **No email delivery.** Magic links print to the console; wiring a provider is
  one function.
- **No cross-source consistency.** Checking a CV against LinkedIn for title
  inflation and unexplained gaps — a competitor does this and it's a genuine
  capability gap.
- **No web-layer test coverage.** The analysis core is well tested; the FastAPI
  routes are exercised manually.
- **Single-process queue** by default. RQ is written, not switched on.
- **A small skill vocabulary** until AI extraction warms the cache.

---

## The sentence that summarises it

The hard part wasn't the statistics. It was building a system that can't be
talked into overclaiming — not by a prompt, not by a model, and not by wanting a
better demo.
