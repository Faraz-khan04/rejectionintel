# Demo data

Five files, each built to show a different thing. All figures below are real —
I ran every one through the server and copied the output.

| File | Use it to show |
|---|---|
| `demo_applications.csv` | The main demo — 112 applications, all four tiers |
| `demo_resume.pdf` | PDF parsing (real text layer) |
| `demo_resume.docx` | Word parsing |
| `demo_resume.txt` | Plain text, if you'd rather paste |
| `demo_small.csv` | The "not enough data" gate — 12 rows |
| `demo_messy.csv` | Fuzzy ingest surviving real-world mess |

---

## The main demo

**Upload `demo_applications.csv` + `demo_resume.pdf`.**

What comes back:

```
Response rate   24.1%   (somewhere between 17% and 33%)
Funnel          112 applied → 27 responded → 16 interviewed → 0 offers
Tiers           1 strong · 5 worth watching · 3 not enough data
```

**The headline finding, and the one to point at:**

> Roles you fit well: **52%** heard back (23 applications)
> Roles you fit poorly: **17%** heard back (89 applications)

That survived correction — the intervals don't overlap. It's the product's
core claim, demonstrated on its own data.

**Then scroll to Resume versions.** This is the strongest moment in the demo:

```
Resume A   n=56   16.1%
Resume B   n=56   32.1%
verdict: not enough data to call it — needs 108 per version
```

Resume B looks **twice as good** and the product still refuses to name a winner.
Every competitor would have declared one. That single panel is the whole
argument about restraint, in numbers an investor can check.

**And the skill gaps**, which need no statistics at all:

> Adding Python would have met the stated requirements of 36 more roles (32% of
> everything you applied to)

---

## The gate

**Upload `demo_small.csv` (12 rows).**

> Only 12 resolved applications. We need 25 before running pattern analysis.

Use this if someone asks what a new user sees. Note that the skill gaps still
appear — those are counts over their own job ads, so they work at any size.
The statistics are what gets withheld.

---

## Messy data

**Upload `demo_messy.csv`.**

```
accepted 10/12 · dates read as day/month
fuzzy match: company   ← "Comapny"    (86%)
fuzzy match: job_title ← "Job Titel"  (89%)
dropped row 12: could not read 'not-a-date' as a date
dropped row 13: 'maybe?' not recognised
```

Four different date formats in one column, two misspelled headers, two broken
rows. The typos resolve, the bad rows are named individually, and the other ten
go through. Good answer to "what if my spreadsheet is a mess?"

---

## Resume with no spreadsheet

**Upload `demo_resume.pdf` and press "Read my resume only".**

```
8.6 years · senior · 9 skills
Backed by a bullet: A/B Testing, API Design, Agile, Figma, Jira, Roadmapping
Listed only: Excel, SQL
```

Two things worth calling out. The 8.6 years has overlapping roles counted once
rather than twice — most parsers inflate that. And **SQL is listed but never
evidenced**, which lines up exactly with the rejection emails in the main
dataset naming SQL. That's the story arc: the resume says SQL, the employers
say it isn't enough, and the analysis finds it independently.

It ends with an amber panel saying this is a reading, not a diagnosis.

---

## Suggested five minutes

1. **Landing page** — scroll to the rejection wall, let them read the chips
2. **"Try it with sample data"** — the stepper runs, report appears
3. **Click a number** — the modal shows the exact applications behind it
4. **Resume versions panel** — the refusal to declare a winner
5. **`demo_small.csv`** — what honesty looks like when there's nothing to say

Steps 3 and 4 are the ones that land. Everything else is context.

---

## Two things to say before being asked

The data is synthetic. Say so — it's on the landing page footer already, and
volunteering it is much stronger than being caught.

There are no users yet. The engineering is done; the calibration isn't. That's
a fair description of where you are, and it's a better position than pretending
otherwise and getting one question deeper than the answer goes.
