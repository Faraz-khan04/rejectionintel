# Test personas

Five people, each built to land in a different product state. Every figure below
came from running the file through the analysis — nothing here is estimated.

Each persona has a spreadsheet and a matching resume. Upload them together.

| Persona | Files | Lands on |
|---|---|---|
| **Priya** | `../demo/demo_applications.csv` + `demo_resume.pdf` | Top-of-funnel bottleneck |
| **Arjun** | `arjun_applications.csv` + `arjun_resume.txt` | Interview-stage bottleneck |
| **Meera** | `meera_applications.csv` + `meera_resume.txt` | Final-round bottleneck |
| **Sam** | `sam_applications.csv` + `sam_resume.txt` | The "not enough data" gate |
| **Dev** | `dev_applications.csv` + `dev_resume.txt` | Healthy funnel, no bottleneck |

---

## Priya — most applications never reach a human

**112 applications · 24% reply rate · 0 offers**

```
verdict   76% stop before anyone replies          [critical]
funnel    112 → 27 → 16 → 0 → 0
findings  1 strong · 4 maybe · 3 can't say
```

The default demo. Exercises the whole report: a strong finding on match quality,
skill gaps, the resume diff, the share card.

**What to look at:** the verdict card, then Patterns. The strong finding is that
roles she matched well got answered far more often — the product's core claim,
visible in her own data.

---

## Arjun — replies come, then it stops

**95 applications · 45% reply rate · 1 offer**

```
verdict   You get replies, then it stops at the screening call.  [critical]
funnel    95 → 43 → 14 → 1 → 0
findings  none testable
```

A backend engineer whose resume is genuinely strong — fit scores run 90–97, so
almost every application is a good match. He gets replies. He then loses at the
first conversation, and three employers said why in writing.

**This persona exposes a real gap, and that's why it's here.**

Every hypothesis in `pipeline.py` tests the same outcome: *did they respond?*
None ask *given a reply, did they progress?* So for anyone whose bottleneck is
after the reply, the Patterns tab is empty by construction — not because there's
nothing to find, but because nothing is looking there.

The verdict and the stage split both name his problem correctly. The pattern
engine can't say anything about it yet. Worth fixing with stage-conditional
hypotheses.

**What to look at:** the verdict is right and the Patterns tab is bare. Also
check Observed — his rejection emails name system design repeatedly, which is
the finding the statistics can't reach.

---

## Meera — reaches final rounds, converts none

**74 applications · 59% reply rate · 0 offers**

```
verdict   You're reaching final rounds without converting.  [notable]
funnel    74 → 44 → 26 → 13 → 0
findings  4 strong · 2 maybe · 1 can't say
```

The richest report of the five. Strong findings on match quality, on jobs wanting
A/B Testing and Looker (neither on her resume), and on SQL roles going markedly
better.

```
Jobs that asked for SQL                      96% vs 42%
Jobs wanting A/B Testing (not on resume)     40% vs 73%
Jobs wanting Looker (not on resume)          37% vs 72%
```

**Note the verdict doesn't blame the top of her funnel.** A 59% reply rate is
excellent, and an earlier version of the bottleneck logic would have told her
"most of your applications never reach a human" — because absolute drop always
accuses the widest stage. Hers is the case that caught that bug.

**What to look at:** Patterns, and the skill-gap timeline. Her two missing skills
have very different learning costs.

---

## Sam — not enough data yet

**14 applications · 43% reply rate**

```
verdict   You interview well enough to get there, then stall.  [notable]
blocked   Only 14 resolved applications. We need 25 before running pattern analysis.
findings  gated — none run
```

A new graduate. Fourteen applications is a real starting point and not enough for
statistics.

**What to look at:** the gate. The funnel, fit scores and skill gaps all still
appear, because those are counts over his own data and work at any size. Only the
statistics are withheld. That distinction is the product's whole argument, and
this is the file that shows it.

---

## Dev — nothing is obviously broken

**58 applications · 72% reply rate · 6 offers**

```
verdict   Your funnel is converting well at every stage.  [healthy]
funnel    58 → 42 → 31 → 15 → 6
findings  1 strong · 1 maybe · 3 can't say
```

A staff engineer with a working search. Included because a product that only has
something to say when things are broken is a product that lies when they aren't.

One counter-intuitive strong finding: roles he matched *well* replied 64% against
100% for the rest — he's overqualified for the ones he matches on paper.

**What to look at:** the verdict refusing to invent a bottleneck, and the pace
card, which for him produces a real estimate rather than a floor.

---

## Suggested order for a demo

1. **Priya** — the full report, the standard case
2. **Sam** — what honesty looks like when there's nothing to say
3. **Meera** — the richest findings, and the verdict correctly ignoring a healthy
   top of funnel
4. **Dev** — the product declining to manufacture a problem

Arjun is the one to show a technical audience: the verdict is right, the Patterns
tab is empty, and the reason is a design gap rather than a bug.

---

## Also in `../demo/`

- `demo_messy.csv` — misspelled headers, four date formats, two broken rows
- `demo_small.csv` — 12 rows, another view of the gate
- `demo_resume.pdf` / `.docx` — for testing the file parsers

---

## A caveat that matters

All of this is synthetic, generated with signals deliberately planted in it.
Synthetic data cooperates: the dates are clean, the skill names match the
vocabulary, the outcomes are drawn from a distribution I chose.

These files prove the system behaves correctly across five different funnel
shapes. They cannot tell you whether the output is *useful* to someone whose
rejections are real. Only one real job search can do that.
