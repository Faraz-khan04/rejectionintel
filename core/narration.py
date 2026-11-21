"""
Narration service — turns computed statistics into English.

Provider-agnostic: see core/llm_provider.py. The numeric guard lives in
narrate.py and is applied identically regardless of which model produced the
text, because the guard validates output against the facts payload rather than
against anything model-specific.

WHY EACH FINDING GETS ITS OWN CALL
----------------------------------
Batching all findings into one request would be cheaper. It would also break
the guard: the guard checks that every number in a sentence appears in THAT
finding's facts. Combine nine findings into one call and a figure from finding
#3 is legitimately in the merged allow-list, so it can migrate into finding
#7's sentence, pass validation, and be wrong.

CALL BUDGET
-----------
Providers declare how many calls a report may make. On Gemini's free tier that
is 5 (summary + 4 findings), because the free tier allows only a handful of
requests per minute. Findings outside the budget get deterministic templates.

Budget is spent on the findings that matter most: OBSERVED first, then STRONG,
then WEAK, then INSUFFICIENT. A "not enough data" card reads perfectly well
from a template; a strong finding is where good prose earns its keep.

FAILURE IS NOT AN ERROR STATE
-----------------------------
No key, no network, a rate limit that survived three backoffs, or two failed
validations — every finding still gets correct text from a template. Narration
is an enhancement over a product that already works without it.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass

from .enums import ConfidenceTier
from .plain_language import (
    NARRATION_SCHEMA as PL_NARRATION_SCHEMA,
    SUMMARY_SCHEMA as PL_SUMMARY_SCHEMA,
    SYSTEM_PROMPT as PL_SYSTEM,
    SUMMARY_PROMPT as PL_SUMMARY_SYSTEM,
    three_part_template,
)
from .llm_provider import (
    NARRATION_SCHEMA, SUMMARY_SCHEMA, Provider, ProviderStatus,
    RateLimited, complete_with_backoff, get_provider,
)
from .narrate import (
    SYSTEM_PROMPT, TIER_STANCE, NarrationResult, _fallback, build_payload, validate,
)
from .stats import Finding

# Budget is spent in this order.
TIER_PRIORITY = {
    ConfidenceTier.OBSERVED: 0,
    ConfidenceTier.STRONG: 1,
    ConfidenceTier.WEAK: 2,
    ConfidenceTier.INSUFFICIENT: 3,
}


def provider_status() -> ProviderStatus:
    return get_provider()


# --------------------------------------------------------------------------
# Per-finding
# --------------------------------------------------------------------------

def _template_result(f: Finding) -> NarrationResult:
    """Deterministic three-part fallback, including Hinglish."""
    t = three_part_template(
        f.label or f.dimension, f.label_with or "these applications",
        f.label_without or "the others", f.rate_exposed, f.rate_unexposed,
        f.n_exposed, f.n_unexposed, f.confidence_tier.value, f.dimension,
    )
    r = NarrationResult(t["observation"], t["translation"], t["next_move"],
                        model="template", used_fallback=True, validation_errors=[])
    r.translation_easy = t["translation_easy"]
    r.recommendation_easy = t.get("next_move_easy", "")
    return r


async def _narrate_one(provider: Provider | None, f: Finding,
                       context: dict) -> NarrationResult:
    payload = build_payload(f, context)
    if provider is None:
        return _template_result(f)

    user_msg = (
        f"CONFIDENCE STANCE: {TIER_STANCE[f.confidence_tier]}\n\n"
        f"FACTS:\n{json.dumps(payload, indent=2)}"
    )
    last_errors: list[str] = []

    for _ in range(2):
        try:
            out = await complete_with_backoff(
                provider, PL_SYSTEM, user_msg, PL_NARRATION_SCHEMA
            )
            # Both language variants are validated. An easy version that
            # invents a number is exactly as wrong as an English one, and it
            # would be the version most people actually read.
            combined = " ".join([
                out.get("observation", ""), out.get("translation", ""),
                out.get("translation_easy", ""), out.get("next_move", ""),
            ])
            errors = validate(combined, payload, f.confidence_tier)
            if not errors:
                r = NarrationResult(
                    out["observation"], out["translation"], out["next_move"],
                    model=provider.info.model, used_fallback=False,
                    validation_errors=[],
                )
                r.translation_easy = out.get("translation_easy", "")
                return r
            last_errors = errors
            user_msg += (
                f"\n\nYour previous attempt was rejected for: {errors}. "
                "Fix it. Use only numbers that appear in FACTS."
            )
        except RateLimited:
            last_errors = ["rate_limited"]
            break
        except Exception as exc:  # noqa: BLE001
            last_errors = [f"api_error: {type(exc).__name__}"]
            break

    result = _template_result(f)
    result.validation_errors = last_errors
    return result


# --------------------------------------------------------------------------
# Report summary
# --------------------------------------------------------------------------

SUMMARY_SYSTEM = """You write the opening paragraph of a job-search analytics report.

The reader has been rejected many times and has not been told why. Be direct \
and useful. Do not console them, do not motivate them, do not use exclamation \
marks. They want information.

ABSOLUTE RULES:
1. Every number you write must appear in the FACTS payload. Rounding and \
converting a rate to a percentage is fine. Computing anything new is not.
2. Never say why any employer rejected them. You do not know.
3. Never predict a future rate or an improvement. You have no counterfactual.
4. If the strongest evidence is weak or absent, say so in the first two \
sentences rather than burying it.
5. Describe patterns as facts about THEIR OWN applications, never as facts \
about the job market.

Reply with JSON only:
headline: under 90 characters, the single most useful observation.
summary: 3-4 sentences.
next_step: one imperative sentence about what to do before applying again."""


@dataclass
class SummaryResult:
    headline: str
    summary: str
    next_step: str
    used_fallback: bool
    validation_errors: list[str]
    summary_easy: str = ""


def build_summary_payload(result, findings: list[Finding], profile) -> dict:
    tiers = {t.value: 0 for t in ConfidenceTier}
    for f in findings:
        tiers[f.confidence_tier.value] += 1

    notable = [
        {
            "dimension": f.dimension,
            "tier": f.confidence_tier.value,
            "rate_with": round(f.rate_exposed, 3),
            "rate_without": round(f.rate_unexposed, 3),
            "n_with": f.n_exposed,
            "n_without": f.n_unexposed,
        }
        for f in sorted(findings, key=lambda x: TIER_PRIORITY[x.confidence_tier])
        if f.confidence_tier is not ConfidenceTier.INSUFFICIENT
    ][:4]

    return {
        "applications_analysed": result.n_analyzable,
        "applications_uploaded": result.n_total,
        "overall_response_rate": round(result.overall_response_rate, 3),
        "response_rate_confidence_interval": [round(x, 3) for x in result.overall_ci],
        "funnel_counts": result.funnel,
        "candidate_years_experience": round(profile.total_experience_months / 12, 1),
        "findings_by_tier": tiers,
        "notable_findings": notable,
        "analysis_blocked_reason": result.blocked_reason,
    }


def _summary_fallback(payload: dict) -> SummaryResult:
    n = payload["applications_analysed"]
    if payload["analysis_blocked_reason"]:
        return SummaryResult(
            f"Funnel only — {n} resolved applications so far",
            payload["analysis_blocked_reason"],
            "Keep logging applications until you reach the analysis threshold.",
            used_fallback=True, validation_errors=[],
        )

    rate = payload["overall_response_rate"] * 100
    lo, hi = [x * 100 for x in payload["response_rate_confidence_interval"]]
    strong = payload["findings_by_tier"].get("strong", 0)
    weak = payload["findings_by_tier"].get("weak", 0)

    if strong:
        head = (f"{strong} pattern held up across {n} applications" if strong == 1
                else f"{strong} patterns held up across {n} applications")
    elif weak:
        head = f"No firm patterns across {n} applications, {weak} worth watching"
    else:
        head = f"No testable patterns across {n} applications"

    res = SummaryResult(
        head,
        f"You heard back from {rate:.0f}% of the {n} applications we could read. "
        f"The real figure is probably between {lo:.0f}% and {hi:.0f}% — that range "
        f"is wide because {n} isn't a big number yet.",
        "Look at how well you matched the roles that never replied.",
        used_fallback=True, validation_errors=[],
    )
    res.summary_easy = (
        f"{n} applications mein se {rate:.0f}% ne reply kiya. Asli number "
        f"{lo:.0f}% se {hi:.0f}% ke beech hoga — {n} abhi kam hai isliye range badi hai."
    )
    return res


async def _narrate_summary(provider: Provider | None, payload: dict) -> SummaryResult:
    if provider is None:
        return _summary_fallback(payload)

    user_msg = f"FACTS:\n{json.dumps(payload, indent=2)}"
    last_errors: list[str] = []

    for _ in range(2):
        try:
            out = await complete_with_backoff(
                provider, PL_SUMMARY_SYSTEM, user_msg, PL_SUMMARY_SCHEMA
            )
            combined = " ".join([
                out.get("headline", ""), out.get("summary", ""),
                out.get("summary_easy", ""), out.get("next_step", ""),
            ])
            # Held to the STRONG bar: this is the most prominent text in the
            # report, so hedging language is checked least leniently.
            errors = validate(combined, payload, ConfidenceTier.STRONG)
            if not errors:
                sr = SummaryResult(out["headline"], out["summary"],
                                   out["next_step"], False, [])
                sr.summary_easy = out.get("summary_easy", "")
                return sr
            last_errors = errors
            user_msg += f"\n\nRejected for: {errors}. Use only numbers from FACTS."
        except RateLimited:
            last_errors = ["rate_limited"]
            break
        except Exception as exc:  # noqa: BLE001
            last_errors = [f"api_error: {type(exc).__name__}"]
            break

    res = _summary_fallback(payload)
    res.validation_errors = last_errors
    return res


# --------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------

@dataclass
class NarratedReport:
    summary: SummaryResult
    findings: dict[str, NarrationResult]
    mode: str                 # "llm" | "template"
    provider: str
    model: str
    calls_made: int
    fallbacks: int
    budget: int
    note: str


async def narrate_report(result, findings: list[Finding], profile,
                         context: dict | None = None) -> NarratedReport:
    status = get_provider()
    provider = status.provider
    context = context or {}
    summary_payload = build_summary_payload(result, findings, profile)

    if provider is None:
        return NarratedReport(
            summary=_summary_fallback(summary_payload),
            findings={f.dimension: _template_result(f) for f in findings},
            mode="template", provider="none", model="template",
            calls_made=0, fallbacks=len(findings) + 1, budget=0,
            note=status.reason,
        )

    info = provider.info
    # The summary always gets a call — it's the most-read text in the report.
    finding_budget = max(0, info.call_budget - 1)
    ranked = sorted(findings, key=lambda f: TIER_PRIORITY[f.confidence_tier])
    narrate_llm = {f.dimension for f in ranked[:finding_budget]}

    sem = asyncio.Semaphore(info.max_concurrency)

    async def one(f: Finding) -> NarrationResult:
        if f.dimension not in narrate_llm:
            return _template_result(f)
        async with sem:
            return await _narrate_one(provider, f, context)

    async def summary_call() -> SummaryResult:
        # Must share the semaphore. Leaving it outside means peak concurrency
        # is max_concurrency + 1, which is exactly the request that trips a
        # 429 on a 5-per-minute free tier.
        async with sem:
            return await _narrate_summary(provider, summary_payload)

    summary, *narrations = await asyncio.gather(
        summary_call(),
        *[one(f) for f in findings],
    )

    by_dim = {f.dimension: n for f, n in zip(findings, narrations)}
    attempted = len(narrate_llm) + 1
    fallbacks = sum(1 for n in narrations if n.used_fallback) + int(summary.used_fallback)

    return NarratedReport(
        summary=summary, findings=by_dim, mode="llm",
        provider=info.name, model=info.model,
        calls_made=attempted, fallbacks=fallbacks, budget=info.call_budget,
        note=info.notes,
    )
