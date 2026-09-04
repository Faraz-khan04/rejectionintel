"""
LLM narration layer — the ONLY place a model touches user-facing output.

CONTRACT
--------
The model receives a JSON payload of already-computed facts. It may:
  - rephrase them in plain English
  - explain what a pattern plausibly means
  - suggest a concrete next action

It may NOT:
  - introduce any number not present in the payload
  - assert a cause for any individual rejection
  - upgrade a WEAK finding's language to sound certain

The last one cannot be prompted away reliably, so it is enforced twice:
by prompt AND by post-generation validation. If validation fails we retry
once, then fall back to a deterministic template. The template is not a
degraded experience — it is the floor, and it is always correct.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from .enums import ConfidenceTier
from .stats import Finding

NARRATION_VERSION = "0.1.0"

# Cheap + fast is right here: this is rephrasing, not reasoning. Extraction
# and narration are both high-volume, low-difficulty calls.
NARRATION_MODEL = "claude-haiku-4-5-20251001"
EXTRACTION_MODEL = "claude-haiku-4-5-20251001"

# Language the model is not allowed to use, by tier. Causal verbs are banned
# outright because we never observe causation.
BANNED_ALWAYS = [
    "because you", "the reason you were rejected", "they rejected you for",
    "caused", "proves", "guaranteed", "will get you", "definitely",
]
BANNED_BELOW_STRONG = [
    "clearly", "strongly suggests", "significant", "conclusive", "shows that",
]


@dataclass
class NarrationResult:
    headline: str            # the observation
    explanation: str         # the translation
    recommendation: str      # the next move
    model: str
    used_fallback: bool
    validation_errors: list[str]
    translation_easy: str = ""
    recommendation_easy: str = ""


# --------------------------------------------------------------------------
# The guard
# --------------------------------------------------------------------------

_NUM = re.compile(r"\d+(?:\.\d+)?")


def _numbers_in(text: str) -> set[str]:
    """Normalised numeric tokens, so '3.40' and '3.4' compare equal."""
    out = set()
    for m in _NUM.finditer(text):
        try:
            out.add(f"{float(m.group()):g}")
        except ValueError:
            continue
    return out


def allowed_numbers(payload: dict) -> set[str]:
    """Every number the model is permitted to write, harvested from the facts.

    Includes rounded forms, because we WANT the model to write '3.4x' when the
    payload says 3.417 — that is good writing, not fabrication.
    """
    allowed: set[str] = set()

    def walk(node):
        if isinstance(node, dict):
            for v in node.values():
                walk(v)
        elif isinstance(node, (list, tuple)):
            for v in node:
                walk(v)
        elif isinstance(node, bool):
            return
        elif isinstance(node, (int, float)):
            allowed.add(f"{float(node):g}")
            allowed.add(f"{round(float(node)):g}")
            allowed.add(f"{round(float(node), 1):g}")
            # percentage forms of any rate between 0 and 1
            if 0 < float(node) <= 1:
                pct = float(node) * 100
                allowed.add(f"{pct:g}")
                allowed.add(f"{round(pct):g}")
                allowed.add(f"{round(pct, 1):g}")
        elif isinstance(node, str):
            allowed.update(_numbers_in(node))

    walk(payload)
    return allowed


def validate(text: str, payload: dict, tier: ConfidenceTier) -> list[str]:
    """Return a list of violations. Empty list == publishable."""
    errors: list[str] = []
    lowered = text.lower()

    permitted = allowed_numbers(payload)
    for token in _numbers_in(text):
        if token not in permitted:
            errors.append(f"unsourced number: {token}")

    for phrase in BANNED_ALWAYS:
        if phrase in lowered:
            errors.append(f"banned causal phrasing: '{phrase}'")

    if tier != ConfidenceTier.STRONG:
        for phrase in BANNED_BELOW_STRONG:
            if phrase in lowered:
                errors.append(f"overconfident phrasing for {tier.value} finding: '{phrase}'")

    return errors


# --------------------------------------------------------------------------
# Payload construction — the model sees this and nothing else
# --------------------------------------------------------------------------

TIER_STANCE = {
    ConfidenceTier.OBSERVED: (
        "An employer stated this in writing. You may report it as fact, "
        "attributed to that employer only."
    ),
    ConfidenceTier.STRONG: (
        "A pattern that survived multiple-comparison correction. Describe it "
        "as a pattern in THIS user's own applications. Never generalise it to "
        "the job market. Never call it a cause."
    ),
    ConfidenceTier.WEAK: (
        "Directional only, could still be chance. You MUST hedge. Say the data "
        "'hints' or 'is suggestive'. Recommend gathering more data before acting."
    ),
    ConfidenceTier.INSUFFICIENT: (
        "There is not enough data. Say so plainly. Do not speculate about what "
        "the pattern might be. State how many more applications would help."
    ),
}


def build_payload(f: Finding, context: dict | None = None) -> dict:
    return {
        "dimension": f.dimension,
        "what_we_compared": f.label or f.dimension,
        "group_with": f.label_with,
        "group_without": f.label_without,
        "confidence_tier": f.confidence_tier.value,
        "applications_with_attribute": f.n_exposed,
        "applications_without_attribute": f.n_unexposed,
        "response_rate_with": round(f.rate_exposed, 3),
        "response_rate_without": round(f.rate_unexposed, 3),
        "confidence_interval_with": [round(x, 3) for x in f.ci_exposed],
        "confidence_interval_without": [round(x, 3) for x in f.ci_unexposed],
        "risk_ratio": round(f.risk_ratio, 2) if f.risk_ratio else None,
        # The difference between the two rates, in points. Deterministically
        # derived, and the most natural way to describe the finding in a
        # sentence — the template already used it. Without it in the payload
        # the guard would reject an LLM writing the same true sentence the
        # fallback writes, which made the two paths inconsistent.
        "gap_in_points": round(abs(f.rate_exposed - f.rate_unexposed) * 100),
        "q_value": round(f.q_value, 4) if f.q_value else None,
        "gate_reason": f.gate_reason,
        "context": context or {},
    }


SYSTEM_PROMPT = """You write plain-English explanations of job-search analytics.

ABSOLUTE RULES:
1. Every number in your output must appear in the FACTS payload. You may round \
and convert rates to percentages. You may NOT compute new figures, estimate, \
extrapolate, or project.
2. Never state why a specific employer rejected this person. You do not know. \
The data shows correlations in their own application history, nothing more.
3. Never predict a future rate ("this would raise you to X%"). You have no \
counterfactual.
4. Match the confidence stance given for the tier. Do not exceed it.
5. Recommendations must be concrete actions the person can take this week.

Return ONLY a JSON object: {"headline": str, "explanation": str, "recommendation": str}
headline: under 90 characters.
explanation: 2-3 sentences.
recommendation: 1-2 sentences, imperative."""


def _fallback(f: Finding, payload: dict) -> NarrationResult:
    """Deterministic template. Always correct, never interesting."""
    label = f.label or f.dimension
    with_l = f.label_with or "applications with this"
    without_l = f.label_without or "applications without it"
    rate_w = payload["response_rate_with"] * 100
    rate_o = payload["response_rate_without"] * 100

    if f.confidence_tier == ConfidenceTier.INSUFFICIENT:
        headline = f"We couldn't answer this: {label.lower()}"
        explanation = (
            f"You have {f.n_exposed} applications in one group and {f.n_unexposed} "
            f"in the other — not enough to tell them apart from luck."
        )
        recommendation = "Nothing to do. We'll test it again as you add applications."
    else:
        better = rate_w > rate_o
        gap = abs(rate_w - rate_o)
        hedge = ("looks like it might matter" if f.confidence_tier == ConfidenceTier.WEAK
                 else "clearly matters")
        headline = f"{label} {hedge}"
        explanation = (
            f"You heard back from {rate_w:.0f}% of {with_l.lower()} "
            f"({f.n_exposed} applications), against {rate_o:.0f}% of "
            f"{without_l.lower()} ({f.n_unexposed}). That's a {gap:.0f} point gap"
            + (", in your favour." if better else ", against you.")
        )
        if f.confidence_tier == ConfidenceTier.WEAK:
            recommendation = (
                f"Worth watching. Don't change anything on the strength of this "
                f"alone — it could still be luck.")
        else:
            recommendation = (
                f"Send more applications to {with_l.lower()} and fewer to "
                f"{without_l.lower()}.")

    return NarrationResult(headline, explanation, recommendation,
                           model="template", used_fallback=True, validation_errors=[])


def narrate(f: Finding, client=None, context: dict | None = None) -> NarrationResult:
    """Generate a validated explanation for one finding.

    `client` is an anthropic.Anthropic instance. If None, returns the template.
    """
    payload = build_payload(f, context)
    if client is None:
        return _fallback(f, payload)

    user_msg = (
        f"CONFIDENCE STANCE: {TIER_STANCE[f.confidence_tier]}\n\n"
        f"FACTS:\n{json.dumps(payload, indent=2)}"
    )

    last_errors: list[str] = []
    for attempt in range(2):
        try:
            # Forced tool use gives schema-shaped output on every model.
            # (Native structured outputs exist in beta on some models — check
            #  docs.claude.com before switching; this approach works today.)
            resp = client.messages.create(
                model=NARRATION_MODEL,
                max_tokens=600,
                temperature=0,
                system=SYSTEM_PROMPT,
                tools=[{
                    "name": "emit_narration",
                    "description": "Emit the narration for this finding.",
                    "input_schema": {
                        "type": "object",
                        "properties": {
                            "headline": {"type": "string"},
                            "explanation": {"type": "string"},
                            "recommendation": {"type": "string"},
                        },
                        "required": ["headline", "explanation", "recommendation"],
                    },
                }],
                tool_choice={"type": "tool", "name": "emit_narration"},
                messages=[{"role": "user", "content": user_msg}],
            )
            block = next(b for b in resp.content if b.type == "tool_use")
            out = block.input
            combined = " ".join([out["headline"], out["explanation"], out["recommendation"]])

            errors = validate(combined, payload, f.confidence_tier)
            if not errors:
                return NarrationResult(
                    out["headline"], out["explanation"], out["recommendation"],
                    model=NARRATION_MODEL, used_fallback=False, validation_errors=[],
                )
            last_errors = errors
            user_msg += (
                f"\n\nYour previous attempt was rejected for: {errors}. "
                "Fix it. Use only numbers from FACTS."
            )
        except Exception as exc:  # noqa: BLE001
            last_errors = [f"api_error: {exc}"]
            break

    result = _fallback(f, payload)
    result.validation_errors = last_errors
    return result
