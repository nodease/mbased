"""Provider documentation-backed catalog for initial model-routing candidates.

This module deliberately does not invent quality, latency, or fallback numbers.
Those values must come from Nodease operational evidence, not vendor marketing.
The catalog only records the provider's published positioning and source links.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Iterable

from apps.shared.services.llm_model_pricing import normalize_model_pricing_id

CATALOG_SOURCE = "official_provider_catalog"
CATALOG_PROFILE_VERSION = "official-provider-catalog-v3"
CATALOG_EVIDENCE_TYPE = "provider_documentation"
CATALOG_SOURCE_VERIFIED_ON = "2026-07-20"

OPENAI_MODELS_URL = "https://developers.openai.com/api/docs/models"
OPENAI_PRICING_URL = "https://developers.openai.com/api/docs/pricing"
ANTHROPIC_MODELS_URL = "https://platform.claude.com/docs/en/about-claude/models/overview"
ANTHROPIC_PRICING_URL = "https://platform.claude.com/docs/en/about-claude/pricing"
GOOGLE_MODELS_URL = "https://ai.google.dev/gemini-api/docs/models"
GOOGLE_PRICING_URL = "https://ai.google.dev/gemini-api/docs/pricing"


@dataclass(frozen=True)
class OfficialProviderCatalogEntry:
    """Provider-published positioning, not a Nodease performance measurement."""

    provider: str
    canonical_model_id: str
    capability_tier: str
    official_position: str
    model_role: str
    reasoning_profile: str
    complexity_ceiling: str
    cost_position: str
    task_affinities: tuple[str, ...]
    specialization_tags: tuple[str, ...]
    evidence_type: str
    source_verified_on: str
    lifecycle: str
    model_source_url: str
    pricing_source_url: str


@dataclass(frozen=True)
class ModelRoutingCatalogProfile:
    """DB-compatible profile with no unmeasured runtime claims."""

    capability_tier: str
    quality_by_difficulty: dict[str, float]
    uncertainty_by_difficulty: dict[str, float]
    expected_latency_ms_by_input_profile: dict[str, int]
    fallback_rate: Decimal
    prior_strength: Decimal


MODEL_ROUTING_MODEL_ALIASES = {
    "gpt-5.6": "gpt-5.6-sol",
}
WORKFLOW_EXECUTION_EXCLUDED_MODEL_IDS = frozenset({"gpt-5-mini"})
_VERSION_PINNED_MODEL_ID_PATTERN = re.compile(r"-(?:\d{4}-\d{2}-\d{2}|\d{8})$")


def is_version_pinned_model_id(value: object) -> bool:
    """날짜 suffix가 붙은 provider 고정 버전 ID인지 판정한다.

    가격과 과거 실행 이력은 해당 ID를 기본 alias 계열로 정규화해 읽을 수 있다.
    다만 새 workflow 실행과 자동 라우팅은 사람이 선택하는 alias만 사용한다.
    """

    raw_model_id = str(value or "").strip().lower().removeprefix("models/")
    return bool(_VERSION_PINNED_MODEL_ID_PATTERN.search(raw_model_id))


def normalize_model_id(value: object) -> str:
    """Normalize only for catalog lookup; callers retain the executable ID."""

    return normalize_model_pricing_id(value)


def canonical_model_routing_id(value: object) -> str:
    normalized = normalize_model_id(value)
    return MODEL_ROUTING_MODEL_ALIASES.get(normalized, normalized)


def is_workflow_execution_model_excluded(value: object) -> bool:
    """새 workflow 실행에서 사용하지 않는 모델 ID인지 판정한다.

    카탈로그와 가격·과거 실행 이력은 보존하되, 새 수동 실행과 자동 라우팅
    후보에서는 날짜 고정 버전 및 전역 제외 모델을 사용하지 않는다.
    """

    return is_version_pinned_model_id(value) or (
        canonical_model_routing_id(value) in WORKFLOW_EXECUTION_EXCLUDED_MODEL_IDS
    )


def deduplicate_model_routing_ids(model_ids: Iterable[str]) -> list[str]:
    """Collapse provider aliases while retaining an actually available ID.

    When both an alias and its canonical ID are available, prefer the canonical
    ID. If only an alias is available, preserve it so runtime credential lookup
    can still execute that exact provider model ID.
    """

    positions: dict[str, int] = {}
    result: list[str] = []
    for model_id in model_ids:
        executable_id = str(model_id or "").strip()
        normalized = normalize_model_id(executable_id)
        if not normalized:
            continue
        canonical_id = canonical_model_routing_id(normalized)
        existing_position = positions.get(canonical_id)
        if existing_position is None:
            positions[canonical_id] = len(result)
            result.append(executable_id)
            continue
        if normalized == canonical_id:
            result[existing_position] = executable_id
    return result


def _entries(
    model_ids: tuple[str, ...],
    *,
    provider: str,
    capability_tier: str,
    official_position: str,
    model_role: str = "general_purpose",
    reasoning_profile: str,
    complexity_ceiling: str,
    cost_position: str,
    task_affinities: tuple[str, ...],
    specialization_tags: tuple[str, ...],
    lifecycle: str = "listed",
    model_source_url: str,
    pricing_source_url: str,
) -> dict[str, OfficialProviderCatalogEntry]:
    return {
        model_id: OfficialProviderCatalogEntry(
            provider=provider,
            canonical_model_id=canonical_model_routing_id(model_id),
            capability_tier=capability_tier,
            official_position=official_position,
            model_role=model_role,
            reasoning_profile=reasoning_profile,
            complexity_ceiling=complexity_ceiling,
            cost_position=cost_position,
            task_affinities=task_affinities,
            specialization_tags=specialization_tags,
            evidence_type=CATALOG_EVIDENCE_TYPE,
            source_verified_on=CATALOG_SOURCE_VERIFIED_ON,
            lifecycle=lifecycle,
            model_source_url=model_source_url,
            pricing_source_url=pricing_source_url,
        )
        for model_id in model_ids
    }


# Keep this list in exact sync with WORKFLOW_CHAT_MODEL_ALIASES. Tiers are a
# coarse representation of provider-published product positioning only. They
# are not a quality score and must not override Nodease operational evidence.
OFFICIAL_PROVIDER_CATALOG: dict[str, OfficialProviderCatalogEntry] = {
    **_entries(
        ("gpt-4o-mini",),
        provider="openai",
        capability_tier="economy",
        official_position="fast_affordable_small_model",
        model_role="efficient_generalist",
        reasoning_profile="non_reasoning",
        complexity_ceiling="routine",
        cost_position="economy",
        task_affinities=("classification", "extraction", "simple_response"),
        specialization_tags=("well_defined_tasks", "cost_sensitive", "high_volume"),
        model_source_url=OPENAI_MODELS_URL,
        pricing_source_url=OPENAI_PRICING_URL,
    ),
    **_entries(
        ("gpt-5-nano", "gpt-5.4-nano"),
        provider="openai",
        capability_tier="economy",
        official_position="simple_high_volume_reasoning",
        model_role="efficient_generalist",
        reasoning_profile="general_reasoning",
        complexity_ceiling="routine",
        cost_position="economy",
        task_affinities=("classification", "extraction", "ranking", "subagent_tasks"),
        specialization_tags=("well_defined_tasks", "cost_sensitive", "high_volume"),
        model_source_url=OPENAI_MODELS_URL,
        pricing_source_url=OPENAI_PRICING_URL,
    ),
    **_entries(
        ("gpt-5-mini",),
        provider="openai",
        capability_tier="balanced",
        official_position="cost_efficient_reasoning",
        model_role="balanced_generalist",
        reasoning_profile="general_reasoning",
        complexity_ceiling="multi_constraint",
        cost_position="economy",
        task_affinities=("well_defined_reasoning", "coding", "agentic_tasks"),
        specialization_tags=("reasoning", "cost_sensitive", "general_work"),
        model_source_url=OPENAI_MODELS_URL,
        pricing_source_url=OPENAI_PRICING_URL,
    ),
    **_entries(
        ("gpt-5.6-luna",),
        provider="openai",
        capability_tier="balanced",
        official_position="cost_sensitive_high_volume",
        model_role="balanced_generalist",
        reasoning_profile="general_reasoning",
        complexity_ceiling="multi_constraint",
        cost_position="economy",
        task_affinities=("general_work", "high_volume", "cost_sensitive"),
        specialization_tags=("cost_sensitive", "high_volume", "reasoning"),
        model_source_url=OPENAI_MODELS_URL,
        pricing_source_url=OPENAI_PRICING_URL,
    ),
    **_entries(
        ("gpt-4o", "gpt-4.1-mini"),
        provider="openai",
        capability_tier="balanced",
        official_position="non_reasoning_instruction_following",
        model_role="non_reasoning_generalist",
        reasoning_profile="non_reasoning",
        complexity_ceiling="multi_constraint",
        cost_position="balanced",
        task_affinities=("instruction_following", "tool_calling", "long_context"),
        specialization_tags=("instruction_following", "tool_calling", "long_context", "low_latency_non_reasoning"),
        model_source_url=OPENAI_MODELS_URL,
        pricing_source_url=OPENAI_PRICING_URL,
    ),
    **_entries(
        ("gpt-4.1",),
        provider="openai",
        capability_tier="balanced",
        official_position="strongest_previous_non_reasoning",
        model_role="non_reasoning_generalist",
        reasoning_profile="non_reasoning",
        complexity_ceiling="multi_constraint",
        cost_position="balanced",
        task_affinities=("instruction_following", "tool_calling", "long_context"),
        specialization_tags=("instruction_following", "tool_calling", "long_context", "low_latency_non_reasoning"),
        model_source_url=OPENAI_MODELS_URL,
        pricing_source_url=OPENAI_PRICING_URL,
    ),
    **_entries(
        ("gpt-5", "gpt-5.1", "gpt-5.2"),
        provider="openai",
        capability_tier="balanced",
        official_position="previous_general_reasoning",
        model_role="reasoning_generalist",
        reasoning_profile="general_reasoning",
        complexity_ceiling="multi_constraint",
        cost_position="balanced",
        task_affinities=("coding", "agentic_tasks", "general_reasoning"),
        specialization_tags=("reasoning", "coding", "agentic_work"),
        model_source_url=OPENAI_MODELS_URL,
        pricing_source_url=OPENAI_PRICING_URL,
    ),
    **_entries(
        ("gpt-5.4-mini",),
        provider="openai",
        capability_tier="balanced",
        official_position="strong_mini_for_coding_and_subagents",
        model_role="reasoning_generalist",
        reasoning_profile="general_reasoning",
        complexity_ceiling="complex_professional",
        cost_position="economy",
        task_affinities=("coding", "computer_use", "subagent_tasks"),
        specialization_tags=("reasoning", "coding", "agents"),
        model_source_url=OPENAI_MODELS_URL,
        pricing_source_url=OPENAI_PRICING_URL,
    ),
    **_entries(
        ("gpt-5.6-terra",),
        provider="openai",
        capability_tier="advanced",
        official_position="balanced_intelligence_cost",
        model_role="frontier_generalist",
        reasoning_profile="general_reasoning",
        complexity_ceiling="complex_professional",
        cost_position="balanced",
        task_affinities=("complex_professional_work", "general_reasoning", "coding"),
        specialization_tags=("everyday_work", "balanced_intelligence_cost", "reasoning"),
        model_source_url=OPENAI_MODELS_URL,
        pricing_source_url=OPENAI_PRICING_URL,
    ),
    **_entries(
        ("gpt-5.4", "gpt-5.4-pro", "gpt-5.5", "gpt-5.5-pro"),
        provider="openai",
        capability_tier="advanced",
        official_position="high_capability_professional_reasoning",
        model_role="frontier_generalist",
        reasoning_profile="frontier_reasoning",
        complexity_ceiling="complex_professional",
        cost_position="premium",
        task_affinities=("complex_professional_work", "complex_reasoning", "coding"),
        specialization_tags=("complex_professional_work", "reasoning", "coding"),
        model_source_url=OPENAI_MODELS_URL,
        pricing_source_url=OPENAI_PRICING_URL,
    ),
    **_entries(
        ("gpt-5.6", "gpt-5.6-sol"),
        provider="openai",
        capability_tier="advanced",
        official_position="frontier_complex_professional_work",
        model_role="frontier_generalist",
        reasoning_profile="frontier_reasoning",
        complexity_ceiling="complex_professional",
        cost_position="premium",
        task_affinities=("complex_professional_work", "complex_reasoning", "coding"),
        specialization_tags=("complex_professional_work", "complex_reasoning", "coding"),
        model_source_url=OPENAI_MODELS_URL,
        pricing_source_url=OPENAI_PRICING_URL,
    ),
    **_entries(
        ("o3", "o3-pro"),
        provider="openai",
        capability_tier="advanced",
        official_position="specialized_complex_reasoning",
        model_role="reasoning_specialist",
        reasoning_profile="specialized_reasoning",
        complexity_ceiling="complex_professional",
        cost_position="balanced",
        task_affinities=("math_reasoning", "scientific_reasoning", "code_reasoning", "formal_reasoning"),
        specialization_tags=("multi_step_reasoning", "math_reasoning", "scientific_reasoning", "code_reasoning", "visual_reasoning", "technical_writing", "instruction_following"),
        model_source_url=OPENAI_MODELS_URL,
        pricing_source_url=OPENAI_PRICING_URL,
    ),
    **_entries(
        ("claude-haiku-4-5",),
        provider="anthropic",
        capability_tier="balanced",
        official_position="fast_cost_efficient",
        model_role="efficient_generalist",
        reasoning_profile="general_reasoning",
        complexity_ceiling="multi_constraint",
        cost_position="economy",
        task_affinities=("real_time_response", "high_volume", "subagent_tasks"),
        specialization_tags=("low_latency", "high_volume", "cost_sensitive", "subagent_tasks"),
        model_source_url=ANTHROPIC_MODELS_URL,
        pricing_source_url=ANTHROPIC_PRICING_URL,
    ),
    **_entries(
        ("claude-sonnet-4-6", "claude-sonnet-5"),
        provider="anthropic",
        capability_tier="advanced",
        official_position="balanced_capability_cost",
        model_role="balanced_generalist",
        reasoning_profile="frontier_reasoning",
        complexity_ceiling="complex_professional",
        cost_position="balanced",
        task_affinities=("coding", "agents", "enterprise_workflows", "data_analysis"),
        specialization_tags=("coding", "agents", "enterprise_workflows", "balanced_capability_cost"),
        model_source_url=ANTHROPIC_MODELS_URL,
        pricing_source_url=ANTHROPIC_PRICING_URL,
    ),
    **_entries(
        ("claude-fable-5", "claude-opus-4-6", "claude-opus-4-7", "claude-opus-4-8"),
        provider="anthropic",
        capability_tier="advanced",
        official_position="high_capability_reasoning",
        model_role="frontier_generalist",
        reasoning_profile="frontier_reasoning",
        complexity_ceiling="complex_professional",
        cost_position="premium",
        task_affinities=("complex_reasoning", "agentic_coding", "enterprise_work", "advanced_research"),
        specialization_tags=("complex_reasoning", "agentic_coding", "enterprise_work", "advanced_research"),
        model_source_url=ANTHROPIC_MODELS_URL,
        pricing_source_url=ANTHROPIC_PRICING_URL,
    ),
    **_entries(
        ("gemini-2.5-flash-lite",),
        provider="google",
        capability_tier="economy",
        official_position="cost_efficient_high_volume",
        model_role="efficient_generalist",
        reasoning_profile="general_reasoning",
        complexity_ceiling="routine",
        cost_position="economy",
        task_affinities=("classification", "extraction", "high_volume"),
        specialization_tags=("low_latency", "high_volume", "cost_sensitive"),
        model_source_url=GOOGLE_MODELS_URL,
        pricing_source_url=GOOGLE_PRICING_URL,
    ),
    **_entries(
        ("gemini-3.1-flash-lite",),
        provider="google",
        capability_tier="balanced",
        official_position="frontier_class_cost_efficient",
        model_role="efficient_generalist",
        reasoning_profile="general_reasoning",
        complexity_ceiling="multi_constraint",
        cost_position="economy",
        task_affinities=("general_work", "high_volume", "cost_sensitive"),
        specialization_tags=("low_latency", "high_volume", "reasoning"),
        model_source_url=GOOGLE_MODELS_URL,
        pricing_source_url=GOOGLE_PRICING_URL,
    ),
    **_entries(
        ("gemini-2.5-flash",),
        provider="google",
        capability_tier="balanced",
        official_position="fast_general_capability",
        model_role="balanced_generalist",
        reasoning_profile="general_reasoning",
        complexity_ceiling="multi_constraint",
        cost_position="balanced",
        task_affinities=("reasoning", "low_latency", "high_volume", "general_work"),
        specialization_tags=("reasoning", "low_latency", "high_volume", "general_work"),
        model_source_url=GOOGLE_MODELS_URL,
        pricing_source_url=GOOGLE_PRICING_URL,
    ),
    **_entries(
        ("gemini-3.5-flash",),
        provider="google",
        capability_tier="advanced",
        official_position="sustained_frontier_agentic_coding",
        model_role="frontier_generalist",
        reasoning_profile="frontier_reasoning",
        complexity_ceiling="complex_professional",
        cost_position="balanced",
        task_affinities=("agentic_work", "coding", "complex_reasoning"),
        specialization_tags=("advanced_reasoning", "coding", "agentic_work"),
        model_source_url=GOOGLE_MODELS_URL,
        pricing_source_url=GOOGLE_PRICING_URL,
    ),
    **_entries(
        ("gemini-2.5-pro",),
        provider="google",
        capability_tier="advanced",
        official_position="complex_problem_solving",
        model_role="reasoning_generalist",
        reasoning_profile="frontier_reasoning",
        complexity_ceiling="complex_professional",
        cost_position="premium",
        task_affinities=("complex_problem_solving", "deep_reasoning", "coding", "long_context"),
        specialization_tags=("complex_problem_solving", "deep_reasoning", "coding", "long_context"),
        model_source_url=GOOGLE_MODELS_URL,
        pricing_source_url=GOOGLE_PRICING_URL,
    ),
    **_entries(
        ("gemini-3-flash-preview", "gemini-3.1-pro-preview"),
        provider="google",
        capability_tier="advanced",
        official_position="preview_advanced_capability",
        model_role="reasoning_generalist",
        reasoning_profile="frontier_reasoning",
        complexity_ceiling="complex_professional",
        cost_position="balanced",
        task_affinities=("advanced_reasoning", "coding", "agentic_work"),
        specialization_tags=("advanced_reasoning", "coding", "agentic_work"),
        lifecycle="preview",
        model_source_url=GOOGLE_MODELS_URL,
        pricing_source_url=GOOGLE_PRICING_URL,
    ),
}


# The global profile table currently requires these legacy metric columns.
# Empty metrics and zero prior strength explicitly mean "not measured".
SUPPORTED_MODEL_ROUTING_PROFILES: dict[str, ModelRoutingCatalogProfile] = {
    model_id: ModelRoutingCatalogProfile(
        capability_tier=entry.capability_tier,
        quality_by_difficulty={},
        uncertainty_by_difficulty={},
        expected_latency_ms_by_input_profile={},
        fallback_rate=Decimal("0"),
        prior_strength=Decimal("0"),
    )
    for model_id, entry in OFFICIAL_PROVIDER_CATALOG.items()
}


def supported_model_routing_ids(model_ids: Iterable[str]) -> list[str]:
    """Preserve input order and retain only explicitly cataloged model IDs."""

    supported: list[str] = []
    for model_id in deduplicate_model_routing_ids(model_ids):
        if is_workflow_execution_model_excluded(model_id):
            continue
        normalized = normalize_model_id(model_id)
        if normalized in SUPPORTED_MODEL_ROUTING_PROFILES:
            supported.append(str(model_id).strip())
    return supported


def catalog_metadata_for_model_id(model_id: object) -> dict[str, Any]:
    entry = OFFICIAL_PROVIDER_CATALOG.get(normalize_model_id(model_id))
    if entry is None:
        return {}
    return {
        "provider": entry.provider,
        "canonical_model_id": entry.canonical_model_id,
        "capability_tier": entry.capability_tier,
        "official_position": entry.official_position,
        "model_role": entry.model_role,
        "reasoning_profile": entry.reasoning_profile,
        "complexity_ceiling": entry.complexity_ceiling,
        "cost_position": entry.cost_position,
        "task_affinities": list(entry.task_affinities),
        "specialization_tags": list(entry.specialization_tags),
        "evidence_type": entry.evidence_type,
        "source_verified_on": entry.source_verified_on,
        "lifecycle": entry.lifecycle,
        "model_source_url": entry.model_source_url,
        "pricing_source_url": entry.pricing_source_url,
    }


def seed_model_routing_global_profiles(db: Any) -> dict[str, int]:
    """Create or refresh official provider catalog rows idempotently.

    Manually edited or operational profiles are not overwritten. Runtime metrics
    remain empty until Nodease has collected actual operational evidence.
    """

    from apps.shared.db.models.llm import LLMModel
    from apps.shared.db.models.model_routing_policy import LLMModelRoutingGlobalProfile

    model_rows = (
        db.query(LLMModel)
        .filter(LLMModel.model_id_for_api_call.in_(OFFICIAL_PROVIDER_CATALOG))
        .all()
    )
    if not model_rows:
        return {"created": 0, "updated": 0, "skipped": 0}

    existing_by_model_id = {
        row.llm_model_id: row
        for row in db.query(LLMModelRoutingGlobalProfile)
        .filter(LLMModelRoutingGlobalProfile.llm_model_id.in_([row.id for row in model_rows]))
        .all()
    }
    created = updated = skipped = 0
    replaceable_sources = {CATALOG_SOURCE, "routing_catalog_seed"}
    for model in model_rows:
        model_id = normalize_model_id(model.model_id_for_api_call)
        catalog = SUPPORTED_MODEL_ROUTING_PROFILES.get(model_id)
        if catalog is None:
            continue
        row = existing_by_model_id.get(model.id)
        if row is None:
            row = LLMModelRoutingGlobalProfile(
                llm_model_id=model.id,
                source=CATALOG_SOURCE,
                profile_version=CATALOG_PROFILE_VERSION,
            )
            db.add(row)
            created += 1
        elif row.source not in replaceable_sources:
            skipped += 1
            continue
        elif row.profile_version == CATALOG_PROFILE_VERSION and row.source == CATALOG_SOURCE:
            skipped += 1
            continue
        else:
            updated += 1
        row.capability_tier = catalog.capability_tier
        row.quality_by_difficulty = dict(catalog.quality_by_difficulty)
        row.uncertainty_by_difficulty = dict(catalog.uncertainty_by_difficulty)
        row.expected_latency_ms_by_input_profile = dict(
            catalog.expected_latency_ms_by_input_profile
        )
        row.fallback_rate = catalog.fallback_rate
        row.prior_strength = catalog.prior_strength
        row.source = CATALOG_SOURCE
        row.profile_version = CATALOG_PROFILE_VERSION
        row.is_active = True
    db.flush()
    return {"created": created, "updated": updated, "skipped": skipped}
