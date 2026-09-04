"""
The translation layer between rigorous statistics and readable English.

WHY THIS IS A MODULE AND NOT A PROMPT
-------------------------------------
Jargon leaks. It leaks through template fallbacks, through error messages,
through field names that reach a screen because nobody re-read them. A prompt
instructing a model to avoid jargon fixes exactly one of those paths.

So the vocabulary lives here, `soften()` is applied to every user-facing
string regardless of origin, and the prompt below is one consumer of the same
dictionary rather than the only line of defence.

THE EASY VARIANT
----------------
Not a translation — a register shift. The default voice is an experienced
career coach talking over coffee: plain, warm, roughly an eighth-grade reading
level, no hedging and no statistics. Set EASY_STYLE=hinglish for casual
Hinglish instead, which is a better fit for some audiences.

Kept as a separate field so the toggle is instant and neither version is a
degraded copy of the other. Both carry the same numbers, and the numeric guard
runs over both — an easy version that invents a figure is exactly as wrong as
a formal one, and it is the version more people actually read.
"""

from __future__ import annotations

import os
import re

# Which register the "easy" variant uses. Default is the coach voice; Hinglish
# remains available because it lands better with some audiences.
EASY_STYLE = os.environ.get("EASY_STYLE", "coach").strip().lower()

# --------------------------------------------------------------------------
# The dictionary
# --------------------------------------------------------------------------

# Ordered longest-first at use time so "95% confidence interval" is replaced
# before "confidence interval" can match inside it.
GLOSSARY: dict[str, str] = {
    "95% confidence interval": "the range your real number is probably in",
    "confidence interval": "the range your real number is probably in",
    "95% CI": "probably between",
    "CI": "probably between",
    "p-value": "how likely this is just luck",
    "q-value": "how likely this is just luck",
    "statistically significant": "strong enough to trust",
    "not statistically significant": "could still be luck",
    "FDR corrected": "checked against false alarms",
    "false discovery rate": "false alarms",
    "multiple comparisons": "testing lots of things at once",
    "Benjamini-Hochberg": "a check for false alarms",
    "Wilson score interval": "the range your real number is probably in",
    "Fisher's exact test": "a check on whether the gap is real",
    "sample size": "how many applications you've sent",
    "n =": "applications:",
    "risk ratio": "how many times more often",
    "effect size": "how big the difference is",
    "null hypothesis": "the idea that nothing's going on",
    "statistical power": "enough data to tell",
    "underpowered": "not enough data yet",
    "point estimate": "the number we measured",
    "arm": "group",
    "cohort": "group",
    "correlation": "things moving together",
    "causation": "one thing causing another",
    "counterfactual": "what would have happened otherwise",
    "baseline": "your usual rate",
    "conversion rate": "how often it worked",
    "deterministic": "worked out by plain arithmetic",
    "inference": "our best read",
    "distribution": "spread",
    "variance": "how much it jumps around",
    "significance threshold": "our bar for calling something real",
}

# Internal identifiers that must never appear on screen.
FORBIDDEN_ON_SCREEN = [
    r"\bfit_score\b", r"\bq\s*=", r"\bp\s*=", r"\bRR\s+\d", r"\bn\s*=\s*\d",
    r"\brequires:", r"\bmissing:", r"seniority_mismatch",
    r"under_required_experience", r"api_error", r"ClientError",
    r"GEMINI_TIER", r"gemini-\d", r"claude-\w+-\d",
]
_FORBIDDEN = [re.compile(p, re.I) for p in FORBIDDEN_ON_SCREEN]


def soften(text: str) -> str:
    """Replace jargon with plain wording. Applied to every user-facing string.

    >>> soften("The 95% CI was wide and the p-value was small")
    'The probably between was wide and the how likely this is just luck was small'
    """
    if not text:
        return text
    for term in sorted(GLOSSARY, key=len, reverse=True):
        text = re.sub(re.escape(term), GLOSSARY[term], text, flags=re.I)
    return text


def audit(text: str) -> list[str]:
    """Return any internal identifier still present. Empty list is a pass.

    Used in tests so a new field name can't quietly reach a screen.
    """
    return [p.pattern for p in _FORBIDDEN if p.search(text or "")]


# --------------------------------------------------------------------------
# The narration prompt
# --------------------------------------------------------------------------

SYSTEM_PROMPT = """You explain job-search findings to someone who has been rejected \
many times and is tired. Write like a straight-talking friend who happens to be good \
with numbers.

STRUCTURE — every finding has exactly three parts:

1. observation — What the data shows. One sentence. Concrete numbers only.
2. translation — What it means for them. Two sentences maximum. No numbers unless \
one carries the whole point.
3. next_move — Exactly what to do. One imperative sentence. Something they could \
start in the next ten minutes.

BANNED WORDS — never use any of these:
confidence interval, p-value, q-value, statistically significant, FDR, correction, \
multiple comparisons, sample size, effect size, risk ratio, null hypothesis, \
correlation, causation, baseline, distribution, variance, inference, deterministic, \
cohort, arm, threshold, power, significance.

Also never write a variable name. Not fit_score, not requires:SQL, not n=24. \
If the facts contain one, describe it in words instead.

VOICE:
- Short sentences. Under twenty words each.
- Second person. "You heard back from..." not "The candidate received..."
- No cheerleading. No "Don't give up!" or "You've got this!". They want \
information, not encouragement.
- No blame. "Roles asking for SQL went badly" not "You're weak on SQL".
- Never apologise for the data.

HARD RULES:
1. Every number you write must appear in the FACTS payload. You may round and turn \
a rate into a percentage. You may not calculate anything new.
2. Never say why a specific employer rejected them. You do not know.
3. Never predict a future rate. You have no way to know it.
4. If the finding is uncertain, say so in the translation — plainly, not with hedging \
words like "may potentially".

THE EASY VERSION — this is the one people actually read:

Write translation_easy as an experienced career coach would say it out loud, \
sitting across from this person with a coffee. Not a summary of the finding — \
the thing you would actually tell them.

  - Around an eighth-grade reading level. Short words beat precise ones.
  - Contractions. "You're", "don't", "isn't".
  - Say what it means for them, then what it means they should stop or start.
  - Warm and level. Not chirpy, not clinical, never pitying.
  - Two or three sentences. It can be slightly longer than `translation` if \
that makes it land better.
  - No numbers unless one number carries the whole point.
  - Never "the data suggests", "statistically", "it appears that", "based on \
your metrics". A coach doesn't talk like that.

Target register — this is the standard to hit:
  "You get way more responses when you clearly match the core requirements. \
Don't waste your time on jobs that are a stretch — focus your energy where you \
have a strong fit."

Weaker version to avoid (too flat, too formal):
  "Applications with higher fit scores received more responses. Consider \
prioritising better-matched roles."

Reply with JSON only:
{"observation": str, "translation": str, "translation_easy": str, "next_move": str}"""


SUMMARY_PROMPT = """You write the opening paragraph of a job-search report for someone \
who has been rejected many times.

Be direct and useful. No consoling, no motivating, no exclamation marks. They want to \
know what's going on.

BANNED WORDS: confidence interval, p-value, statistically significant, FDR, correction, \
sample size, effect size, significance, power, baseline, distribution, inference.

Never write a variable name or an internal identifier.

VOICE: short sentences, second person, no cheerleading, no blame.

HARD RULES:
1. Every number must appear in the FACTS payload. Rounding is fine. Calculating is not.
2. Never say why any employer rejected them.
3. Never predict a future rate.
4. If the strongest evidence is weak, say so in the first two sentences.

Also write summary_easy — the same paragraph as a career coach would say it out loud, \
around an eighth-grade reading level. Contractions, short words, warm and level. Lead \
with what it means for them rather than what the numbers are. Same facts, same numbers, \
no new claims, no "the data suggests".

Reply with JSON only:
{"headline": str, "summary": str, "summary_easy": str, "next_step": str}"""


NARRATION_SCHEMA = {
    "type": "object",
    "properties": {
        "observation": {"type": "string"},
        "translation": {"type": "string"},
        "translation_easy": {"type": "string"},
        "next_move": {"type": "string"},
    },
    "required": ["observation", "translation", "translation_easy", "next_move"],
}

SUMMARY_SCHEMA = {
    "type": "object",
    "properties": {
        "headline": {"type": "string"},
        "summary": {"type": "string"},
        "summary_easy": {"type": "string"},
        "next_step": {"type": "string"},
    },
    "required": ["headline", "summary", "summary_easy", "next_step"],
}


# --------------------------------------------------------------------------
# Deterministic three-part fallback
# --------------------------------------------------------------------------

def _advice(dimension: str, label_with: str, label_without: str,
            better: bool, tier: str) -> tuple[str, str]:
    """What to actually do — constrained by what the user can control.

    THE PROBLEM THIS SOLVES
    -----------------------
    A generic "send more/fewer applications to X" produced advice that was
    useless at best and harmful at worst:

      "Applying through a referral: 11% vs 25%
       -> Send fewer applications through a referral."

    Referrals underperforming across 18 applications is almost certainly noise,
    and telling someone to stop asking for referrals could do real damage to
    their search. Likewise "send fewer applications to jobs asking for Looker"
    asks them to control what employers put in a job ad.

    So advice depends on the kind of thing being measured, and some kinds get
    no directive at all — which is the honest answer when nothing actionable
    follows from the number.
    """
    w = label_with.lower()

    # Referrals. A negative result here is noise far more often than signal,
    # and the action it implies is one nobody should take.
    if dimension == "referral":
        if better:
            return ("Keep asking for referrals — they're working for you.",
                    "Referrals are clearly doing something for you. Keep asking.")
        return ("Don't read much into this. Referrals usually help, and this is "
                "too small a group to say otherwise — keep asking for them.",
                "I wouldn't change anything here. Referrals almost always help, "
                "and this is too small a batch to say otherwise. Keep asking.")

    # A skill the resume doesn't evidence. The lever is the resume, not the job ads.
    if dimension.startswith("missing:"):
        skill = dimension.split(":", 1)[1]
        if better:
            return (f"No change needed — these went fine despite {skill} not being "
                    f"on your resume.",
                    f"These went fine even without {skill} on your resume. Leave it.")
        return (f"Add one bullet showing where you've used {skill}. If you haven't, "
                f"these roles aren't worth applying to.",
                f"Put one line on your resume about {skill} — where you used it and "
                f"what came of it. If you've genuinely never touched it, skip these "
                f"jobs rather than hoping.")

    # What a job ad asks for isn't something the applicant chooses.
    if dimension.startswith("requires:"):
        skill = dimension.split(":", 1)[1]
        if better:
            return (f"Roles asking for {skill} suit you. Look for more of them.",
                    f"Jobs that want {skill} are going well for you. Go find more of them.")
        return (f"Roles asking for {skill} aren't landing. Either strengthen that "
                f"part of your resume or filter these out.",
                f"Jobs asking for {skill} aren't landing. Either make that part of "
                f"your resume much stronger, or stop applying to them.")

    if dimension == "seniority_mismatch":
        return ("Apply at your own level unless you have a specific reason not to.",
                "Stick to roles at your own level unless you've got a real reason "
                "to reach. Reaching is costing you here.")

    if dimension == "under_required_experience":
        return ("Check the years asked for before applying — you're stretching a lot.",
                "Check the years they're asking for before you apply. You're "
                "stretching a long way on a lot of these.")

    if dimension.startswith("fit_score"):
        return ("Check the match score before you apply. It's the strongest signal here.",
                "You get way more responses when you clearly match what they asked "
                "for. Don't burn time on jobs that are a stretch — put your energy "
                "where you're a strong fit.")

    # Nothing specific follows. Say nothing rather than invent a directive.
    return ("", "")


def three_part_template(label: str, label_with: str, label_without: str,
                        rate_with: float, rate_without: float,
                        n_with: int, n_without: int, tier: str,
                        dimension: str = "") -> dict:
    """Template that keeps the same shape as the model output.

    A fallback with a different shape means the UI has two layouts to support
    and one of them is only ever seen when something has gone wrong — which is
    exactly when you want it to look normal.
    """
    rw, ro = rate_with * 100, rate_without * 100
    gap = abs(rw - ro)
    better = rw > ro
    w, o = label_with.lower(), label_without.lower()

    if tier == "insufficient":
        return {
            "observation": f"You have {n_with} applications in one group and {n_without} in the other.",
            "translation": "That's too few to tell a real pattern from luck. We'd rather say nothing than guess.",
            "translation_easy": "There just isn't enough here to tell a real pattern from a run of bad luck. We'd rather tell you that than make something up.",
            "next_move": "Nothing to do here. We'll check again as you add applications.",
            "next_move_easy": "Nothing to do with this one yet. We'll look again once you've sent a few more.",
        }

    direction = "better" if better else "worse"
    hedge = "" if tier == "strong" else " This might still be luck, so don't rebuild anything around it yet."
    easy_hedge = ("" if tier == "strong"
                  else " That said, it's not certain yet — give it a few more applications.")

    move, move_easy = _advice(dimension, label_with, label_without, better, tier)

    return {
        "observation": (f"You heard back from {rw:.0f}% of {label_with} ({n_with} "
                        f"applications) and {ro:.0f}% of {label_without} ({n_without})."),
        "translation": (f"{label} is doing {direction} for you — a gap of about "
                        f"{gap:.0f} points.{hedge}"),
        "translation_easy": (
            f"You're hearing back a lot more from {label_with.lower()} than from "
            f"{label_without.lower()} — about {gap:.0f} points more."
            if better else
            f"{label_with} are going noticeably worse for you — about {gap:.0f} "
            f"points below {label_without.lower()}."
        ) + easy_hedge,
        "next_move": move,
        "next_move_easy": move_easy,
    }
