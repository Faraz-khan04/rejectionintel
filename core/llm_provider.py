"""
LLM provider abstraction.

Narration is the only place a model touches user-facing text, and the numeric
guard in narrate.py is what makes that safe. The guard is provider-independent:
it checks output against the facts payload, not against anything model-specific.
So swapping Gemini for Claude changes cost, latency and prose style — it does
not change a single guarantee.

WHY THIS FILE EXISTS AT ALL
---------------------------
Two providers with genuinely different shapes:

  Anthropic  forced tool use  ->  schema-shaped tool_use block
  Gemini     response_schema  ->  JSON in .text

Both are asked for the same three fields and both are validated identically.
Everything above this file works in terms of `Narration(headline, explanation,
recommendation)` and never learns which provider produced it.

RATE LIMITS ARE THE REAL CONSTRAINT
-----------------------------------
Gemini's free tier is roughly 5-15 requests per minute and, for some models,
as few as 20-1000 per day. A report firing ten parallel calls would 429
instantly and exhaust a daily quota in a couple of uploads.

So concurrency and per-report call budget are PROVIDER PROPERTIES, not
constants. Gemini free tier gets 2 concurrent calls and a budget of 5; the
rest of the findings fall back to deterministic templates, which were always
the floor anyway. Set GEMINI_TIER=paid to lift both.
"""

from __future__ import annotations

import asyncio
import json
import os
import random
from dataclasses import dataclass, field

DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"
DEFAULT_ANTHROPIC_MODEL = "claude-haiku-4-5-20251001"

# The three fields every narration call must return.
NARRATION_SCHEMA = {
    "type": "object",
    "properties": {
        "headline": {"type": "string"},
        "explanation": {"type": "string"},
        "recommendation": {"type": "string"},
    },
    "required": ["headline", "explanation", "recommendation"],
}

SUMMARY_SCHEMA = {
    "type": "object",
    "properties": {
        "headline": {"type": "string"},
        "summary": {"type": "string"},
        "next_step": {"type": "string"},
    },
    "required": ["headline", "summary", "next_step"],
}


class RateLimited(Exception):
    """Provider returned 429 / quota exhausted."""


@dataclass
class ProviderInfo:
    name: str
    model: str
    max_concurrency: int
    call_budget: int          # LLM calls per report; the rest use templates
    notes: str = ""


# --------------------------------------------------------------------------
# Base
# --------------------------------------------------------------------------

class Provider:
    info: ProviderInfo

    async def complete(self, system: str, user: str, schema: dict) -> dict:
        """Return a dict matching `schema`. Raise RateLimited on 429."""
        raise NotImplementedError


# --------------------------------------------------------------------------
# Gemini
# --------------------------------------------------------------------------

class GeminiProvider(Provider):
    def __init__(self, api_key: str, model: str | None = None, tier: str = "free"):
        from google import genai  # imported lazily so the app runs without it

        self._client = genai.Client(api_key=api_key)
        model = model or os.environ.get("GEMINI_MODEL") or DEFAULT_GEMINI_MODEL
        paid = tier.lower() in {"paid", "tier1", "tier2", "tier3"}

        self.info = ProviderInfo(
            name="gemini",
            model=model,
            max_concurrency=8 if paid else 2,
            call_budget=40 if paid else 5,
            notes=(
                ""
                if paid else
                "Free tier: narration is limited to the summary plus the 4 most "
                "important findings. The rest use templates. Set GEMINI_TIER=paid "
                "once billing is enabled."
            ),
        )

    async def complete(self, system: str, user: str, schema: dict) -> dict:
        try:
            resp = await self._client.aio.models.generate_content(
                model=self.info.model,
                contents=user,
                config={
                    "system_instruction": system,
                    "temperature": 0,
                    "max_output_tokens": 900,
                    "response_mime_type": "application/json",
                    "response_schema": schema,
                    # We pass no tools, so the SDK's automatic function calling
                    # is dead weight — and it logs a warning on every call.
                    # Turning it off silences the noise and removes a code path
                    # we never want running inside a narration request.
                    "automatic_function_calling": {"disable": True},
                },
            )
        except Exception as exc:  # noqa: BLE001
            msg = str(exc).lower()
            if "429" in msg or "quota" in msg or "resource_exhausted" in msg:
                raise RateLimited(str(exc)) from exc
            raise

        text = (resp.text or "").strip()
        if not text:
            raise ValueError("Gemini returned an empty response")
        return json.loads(text)


# --------------------------------------------------------------------------
# Anthropic
# --------------------------------------------------------------------------

class AnthropicProvider(Provider):
    def __init__(self, api_key: str, model: str | None = None):
        from anthropic import AsyncAnthropic

        self._client = AsyncAnthropic(api_key=api_key, timeout=25.0)
        self.info = ProviderInfo(
            name="anthropic",
            model=model or os.environ.get("ANTHROPIC_MODEL") or DEFAULT_ANTHROPIC_MODEL,
            max_concurrency=8,
            call_budget=40,
        )

    async def complete(self, system: str, user: str, schema: dict) -> dict:
        try:
            resp = await self._client.messages.create(
                model=self.info.model,
                max_tokens=900,
                temperature=0,
                system=system,
                tools=[{
                    "name": "emit",
                    "description": "Emit the narration.",
                    "input_schema": schema,
                }],
                tool_choice={"type": "tool", "name": "emit"},
                messages=[{"role": "user", "content": user}],
            )
        except Exception as exc:  # noqa: BLE001
            msg = str(exc).lower()
            if "429" in msg or "rate_limit" in msg:
                raise RateLimited(str(exc)) from exc
            raise

        block = next(b for b in resp.content if b.type == "tool_use")
        return dict(block.input)


# --------------------------------------------------------------------------
# Retry
# --------------------------------------------------------------------------

async def complete_with_backoff(provider: Provider, system: str, user: str,
                                schema: dict, attempts: int = 3) -> dict:
    """Retry only on rate limits, with jitter.

    Other errors are not retried — a malformed prompt fails the same way twice
    and retrying just doubles the latency before the template takes over.
    """
    delay = 2.0
    last: Exception | None = None
    for i in range(attempts):
        try:
            return await provider.complete(system, user, schema)
        except RateLimited as exc:
            last = exc
            if i == attempts - 1:
                break
            await asyncio.sleep(delay + random.uniform(0, 1.0))
            delay *= 2
        except Exception as exc:  # noqa: BLE001
            raise exc
    raise last if last else RuntimeError("no attempts made")


# --------------------------------------------------------------------------
# Selection
# --------------------------------------------------------------------------

@dataclass
class ProviderStatus:
    provider: Provider | None
    reason: str = ""
    available: list[str] = field(default_factory=list)


def get_provider() -> ProviderStatus:
    """Pick a provider from the environment. Never raises.

    LLM_PROVIDER=gemini|anthropic|none   (default: whichever key is present,
                                          gemini first because of the free tier)
    GEMINI_API_KEY / ANTHROPIC_API_KEY
    GEMINI_MODEL / ANTHROPIC_MODEL       (optional overrides)
    GEMINI_TIER=free|paid                (default: free)
    """
    gem_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    ant_key = os.environ.get("ANTHROPIC_API_KEY")

    available = [n for n, k in (("gemini", gem_key), ("anthropic", ant_key)) if k]
    requested = (os.environ.get("LLM_PROVIDER") or "").strip().lower()

    if requested == "none":
        return ProviderStatus(None, "LLM_PROVIDER=none — templates only", available)

    order = [requested] if requested else ["gemini", "anthropic"]

    for name in order:
        if name == "gemini" and gem_key:
            try:
                p = GeminiProvider(gem_key, tier=os.environ.get("GEMINI_TIER", "free"))
                return ProviderStatus(p, "", available)
            except ImportError:
                return ProviderStatus(
                    None, "GEMINI_API_KEY is set but google-genai isn't installed. "
                          "Run: pip install google-genai", available)
            except Exception as exc:  # noqa: BLE001
                return ProviderStatus(None, f"Gemini setup failed: {exc}", available)

        if name == "anthropic" and ant_key:
            try:
                return ProviderStatus(AnthropicProvider(ant_key), "", available)
            except ImportError:
                return ProviderStatus(
                    None, "ANTHROPIC_API_KEY is set but the anthropic package isn't "
                          "installed. Run: pip install anthropic", available)
            except Exception as exc:  # noqa: BLE001
                return ProviderStatus(None, f"Anthropic setup failed: {exc}", available)

    if requested:
        return ProviderStatus(
            None, f"LLM_PROVIDER={requested} but no matching API key found", available)
    return ProviderStatus(
        None, "No GEMINI_API_KEY or ANTHROPIC_API_KEY found — using templates", available)
