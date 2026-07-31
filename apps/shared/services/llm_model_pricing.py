"""Single source of truth for token-based LLM price estimates.

Database ``LLMModel`` rows retain the standard input/output price used by the
admin catalog. Conditional prices remain code catalog data because a single
model row cannot represent provider-specific cache, batch, or long-context
rates. Callers must label a result as a Standard text-token estimate when the
provider usage does not identify a supported conditional rate.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from decimal import Decimal
from typing import Any, Mapping, Optional

OPENAI_PRICING_URL = "https://developers.openai.com/api/docs/pricing"
ANTHROPIC_PRICING_URL = "https://docs.anthropic.com/en/docs/about-claude/pricing"
GOOGLE_PRICING_URL = "https://ai.google.dev/gemini-api/docs/pricing"


@dataclass(frozen=True)
class ModelPricing:
    """Per-1K USD token prices for one canonical model ID."""

    standard_input_per_1k: float
    standard_output_per_1k: float
    source_url: str
    cached_input_per_1k: Optional[float] = None
    long_context_input_per_1k: Optional[float] = None
    long_context_output_per_1k: Optional[float] = None
    batch_input_per_1k: Optional[float] = None
    batch_output_per_1k: Optional[float] = None
    flex_input_per_1k: Optional[float] = None
    flex_output_per_1k: Optional[float] = None
    priority_input_per_1k: Optional[float] = None
    priority_output_per_1k: Optional[float] = None

    def legacy_standard_prices(self) -> dict[str, float]:
        return {
            "input": self.standard_input_per_1k,
            "output": self.standard_output_per_1k,
        }


def _prices(
    source_url: str,
    values: Mapping[str, tuple[float, float]],
) -> dict[str, ModelPricing]:
    return {
        model_id: ModelPricing(
            standard_input_per_1k=input_price,
            standard_output_per_1k=output_price,
            source_url=source_url,
        )
        for model_id, (input_price, output_price) in values.items()
    }


MODEL_PRICING_CATALOG: dict[str, ModelPricing] = {
    **_prices(
        OPENAI_PRICING_URL,
        {
            "gpt-5.6": (0.005, 0.030),
            "gpt-5.6-sol": (0.005, 0.030),
            "gpt-5.6-terra": (0.0025, 0.015),
            "gpt-5.6-luna": (0.001, 0.006),
            "gpt-5.5": (0.005, 0.030),
            "gpt-5.5-pro": (0.030, 0.180),
            "gpt-5.4": (0.0025, 0.015),
            "gpt-5.4-mini": (0.00075, 0.0045),
            "gpt-5.4-nano": (0.0002, 0.00125),
            "gpt-5.4-pro": (0.030, 0.180),
            "gpt-5.2": (0.00175, 0.014),
            "gpt-5.2-pro": (0.021, 0.168),
            "gpt-5.1": (0.00125, 0.010),
            "gpt-5": (0.00125, 0.010),
            "gpt-5-mini": (0.00025, 0.002),
            "gpt-5-nano": (0.00005, 0.0004),
            "gpt-5-pro": (0.015, 0.120),
            "gpt-4.1": (0.002, 0.008),
            "gpt-4.1-mini": (0.0004, 0.0016),
            "gpt-4o": (0.0025, 0.010),
            "gpt-4o-mini": (0.00015, 0.0006),
            "o3-pro": (0.020, 0.080),
            "o3": (0.002, 0.008),
            "gpt-5-search-api": (0.00125, 0.010),
            "gpt-5.3-codex": (0.00175, 0.014),
            "gpt-realtime-2": (0.004, 0.024),
            "gpt-realtime-1.5": (0.004, 0.016),
            "gpt-realtime": (0.004, 0.016),
            "gpt-realtime-mini": (0.0006, 0.0024),
            "gpt-audio-1.5": (0.0025, 0.010),
            "gpt-audio": (0.0025, 0.010),
            "gpt-audio-mini": (0.0006, 0.0024),
            "gpt-4o-transcribe": (0.0025, 0.010),
            "gpt-4o-transcribe-diarize": (0.0025, 0.010),
            "gpt-4o-mini-transcribe": (0.00125, 0.005),
            "gpt-4o-mini-tts": (0.0, 0.0006),
            "text-embedding-3-small": (0.00002, 0.0),
            "text-embedding-3-large": (0.00013, 0.0),
            "text-embedding-ada-002": (0.00010, 0.0),
        },
    ),
    **_prices(
        ANTHROPIC_PRICING_URL,
        {
            "claude-fable-5": (0.010, 0.050),
            "claude-opus-4-8": (0.005, 0.025),
            "claude-opus-4-7": (0.005, 0.025),
            "claude-opus-4-6": (0.005, 0.025),
            "claude-opus-4-5": (0.005, 0.025),
            "claude-sonnet-5": (0.002, 0.010),
            "claude-sonnet-4-6": (0.003, 0.015),
            "claude-sonnet-4-5": (0.003, 0.015),
            "claude-haiku-4-5": (0.001, 0.005),
        },
    ),
    **_prices(
        GOOGLE_PRICING_URL,
        {
            "gemini-3.5-flash": (0.00075, 0.0045),
            "gemini-3.1-pro-preview": (0.002, 0.012),
            "gemini-3.1-flash-lite": (0.00025, 0.0015),
            "gemini-3-flash-preview": (0.0005, 0.003),
            "gemini-2.5-pro": (0.00125, 0.010),
            "gemini-2.5-flash": (0.0003, 0.0025),
            "gemini-2.5-flash-lite": (0.0001, 0.0004),
            "gemini-robotics-er-1.6-preview": (0.001, 0.005),
            "gemini-embedding-2": (0.0002, 0.0),
            "gemini-embedding-001": (0.00015, 0.0),
        },
    ),
}

# The provider returns dated and prefixed IDs, while DB rows may preserve the
# exact executable ID. Keep aliases here, rather than duplicating lookup logic
# in Gateway and Workflow Engine.
MODEL_PRICING_ALIASES = {
    "gpt-5.6": "gpt-5.6-sol",
    "claude-opus-4-5-20251101": "claude-opus-4-5",
    "claude-sonnet-4-5-20250929": "claude-sonnet-4-5",
    "claude-haiku-4-5-20251001": "claude-haiku-4-5",
}

# OpenAI cache discounts are only used when provider usage explicitly reports
# the cached input count. Unlisted models stay on their Standard input rate.
for _model_id, _cached_price in {
    "gpt-4o-mini": 0.000075,
    "gpt-4o": 0.00125,
    "gpt-4.1-mini": 0.0001,
    "gpt-4.1": 0.0005,
}.items():
    _pricing = MODEL_PRICING_CATALOG[_model_id]
    MODEL_PRICING_CATALOG[_model_id] = replace(
        _pricing,
        cached_input_per_1k=_cached_price,
    )


def normalize_model_pricing_id(model_id: object) -> str:
    """Return the catalog key for provider aliases and dated executable IDs."""

    normalized = str(model_id or "").strip().lower().removeprefix("models/")
    normalized = re.sub(r"-\d{4}-\d{2}-\d{2}$", "", normalized)
    normalized = re.sub(r"-\d{8}$", "", normalized)
    return MODEL_PRICING_ALIASES.get(normalized, normalized)


def get_model_pricing(model_id: object) -> Optional[ModelPricing]:
    return MODEL_PRICING_CATALOG.get(normalize_model_pricing_id(model_id))


def known_model_prices() -> dict[str, dict[str, float]]:
    """Compatibility view for model seed and API contracts.

    Canonical catalog rows are returned together with known executable provider
    aliases. Runtime credential lookup uses the exact provider model ID, so a
    fresh database must have rows for aliases such as dated Claude releases.
    """

    prices = {
        model_id: pricing.legacy_standard_prices()
        for model_id, pricing in MODEL_PRICING_CATALOG.items()
    }
    for alias_model_id, canonical_model_id in MODEL_PRICING_ALIASES.items():
        if alias_model_id not in prices:
            prices[alias_model_id] = MODEL_PRICING_CATALOG[
                canonical_model_id
            ].legacy_standard_prices()
    return prices


def extract_cached_input_tokens(usage: Optional[Mapping[str, Any]]) -> int:
    """Read OpenAI-compatible cached token fields without trusting negatives."""

    if not isinstance(usage, Mapping):
        return 0
    direct = usage.get("cached_tokens")
    details = usage.get("prompt_tokens_details") or usage.get("input_tokens_details")
    nested = details.get("cached_tokens") if isinstance(details, Mapping) else None
    for value in (direct, nested):
        try:
            return max(0, int(value))
        except (TypeError, ValueError):
            continue
    return 0


def pricing_estimate_metadata(
    model_id: object,
    usage: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    """Return display-safe context for a token cost estimate.

    The current DB model stores only one Standard rate pair. The returned
    metadata makes that limitation explicit to trace and cost UIs instead of
    silently implying that Batch, Flex, Priority, or long-context terms were
    priced exactly.
    """

    pricing = get_model_pricing(model_id)
    if pricing is None:
        return {
            "status": "unavailable",
            "reason": "model_price_not_in_catalog",
        }

    cached_tokens = extract_cached_input_tokens(usage)
    return {
        "status": "estimated",
        "basis": (
            "cached_input_and_standard_text_tokens"
            if cached_tokens > 0 and pricing.cached_input_per_1k is not None
            else "standard_text_tokens"
        ),
        "source_url": pricing.source_url,
        "unsupported_conditions": {
            "long_context": pricing.long_context_input_per_1k is None,
            "batch": pricing.batch_input_per_1k is None,
            "flex": pricing.flex_input_per_1k is None,
            "priority": pricing.priority_input_per_1k is None,
            "data_residency": True,
        },
    }


def calculate_text_token_cost(
    model_id: object,
    *,
    prompt_tokens: int,
    completion_tokens: int,
    cached_input_tokens: int = 0,
) -> float:
    """Calculate the supported text-token estimate in USD.

    Unknown model prices intentionally return 0.0 for existing callers, which
    continue to display the result as unavailable rather than inventing a rate.
    """

    pricing = get_model_pricing(model_id)
    if pricing is None:
        return 0.0

    prompt = max(0, int(prompt_tokens or 0))
    completion = max(0, int(completion_tokens or 0))
    cached = min(prompt, max(0, int(cached_input_tokens or 0)))
    standard_input_tokens = prompt - cached
    cached_rate = pricing.cached_input_per_1k or pricing.standard_input_per_1k

    total = (
        Decimal(standard_input_tokens) * Decimal(str(pricing.standard_input_per_1k))
        + Decimal(cached) * Decimal(str(cached_rate))
        + Decimal(completion) * Decimal(str(pricing.standard_output_per_1k))
    ) / Decimal(1000)
    return float(total)


def calculate_text_token_cost_from_rates(
    *,
    input_price_per_1k: float | Decimal,
    output_price_per_1k: float | Decimal,
    prompt_tokens: int,
    completion_tokens: int,
) -> float:
    """Calculate a standard text-token cost from an explicitly stored rate pair.

    Database overrides intentionally use their own standard rates for every
    input token. Conditional catalog rates such as cached-input discounts are
    provider terms, not part of the editable ``LLMModel`` price pair.
    """

    prompt = max(0, int(prompt_tokens or 0))
    completion = max(0, int(completion_tokens or 0))
    total = (
        Decimal(prompt) * Decimal(str(input_price_per_1k))
        + Decimal(completion) * Decimal(str(output_price_per_1k))
    ) / Decimal(1000)
    return float(total)
