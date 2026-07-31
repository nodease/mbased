from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final, Iterable


SUPPORTED_PROVIDER_ORDER: Final[tuple[str, ...]] = (
    "openai",
    "anthropic",
    "google",
)

_PROVIDER_ORDER: Final[dict[str, int]] = {
    provider_name: index
    for index, provider_name in enumerate(SUPPORTED_PROVIDER_ORDER)
}
_TIER_ORDER: Final[dict[str, int]] = {
    "general": 0,
    "mini": 1,
    "nano": 2,
    "pro": 3,
}
_SPECIAL_PURPOSE_TOKENS: Final[frozenset[str]] = frozenset(
    {
        "sora",
        "audio",
        "transcribe",
        "tts",
        "speech",
        "whisper",
        "realtime",
        "live",
        "image",
        "embedding",
        "moderation",
        "search",
        "codex",
        "robotics",
    }
)
_SPECIAL_PURPOSE_TOKEN_SEQUENCES: Final[tuple[tuple[str, ...], ...]] = (
    ("computer", "use"),
)
_PROVIDER_SPECIAL_FULLMATCH_PATTERNS: Final[
    dict[str, tuple[re.Pattern[str], ...]]
] = {
    "openai": (re.compile(r"sora(?:-[a-z0-9.]+)*"),),
    "anthropic": (),
    "google": (),
}
_TOKEN_SPLIT_RE: Final[re.Pattern[str]] = re.compile(r"[-_./:]+")
_ISO_DATE_SUFFIX_RE: Final[re.Pattern[str]] = re.compile(
    r"-(?:\d{4}-\d{2}-\d{2}|\d{8})$"
)
_RELEASE_SUFFIX_RE: Final[re.Pattern[str]] = re.compile(r"-release-\d+$")


@dataclass(frozen=True, slots=True)
class ModelRecommendationCandidate:
    provider_name: str
    model_id: str
    model_name: str
    relation_priority: int
    credential_name: str
    model_stable_id: str
    credential_stable_id: str


@dataclass(frozen=True, slots=True)
class _ParsedModelRank:
    generation_major: int
    generation_minor: int
    tier_order: int
    status_order: int


def _normalize_provider_name(provider_name: str) -> str:
    return provider_name.strip().casefold()


def _normalize_model_id(model_id: str) -> str:
    normalized = model_id.strip().casefold()
    if normalized.startswith("models/"):
        return normalized.removeprefix("models/")
    return normalized


def _contains_token_sequence(
    tokens: tuple[str, ...],
    sequence: tuple[str, ...],
) -> bool:
    width = len(sequence)
    return any(
        tokens[index : index + width] == sequence
        for index in range(len(tokens) - width + 1)
    )


def _is_special_purpose_model(provider_name: str, model_id: str) -> bool:
    normalized_provider = _normalize_provider_name(provider_name)
    normalized_model_id = _normalize_model_id(model_id)
    provider_patterns = _PROVIDER_SPECIAL_FULLMATCH_PATTERNS.get(
        normalized_provider,
        (),
    )
    if any(pattern.fullmatch(normalized_model_id) for pattern in provider_patterns):
        return True

    tokens = tuple(
        token for token in _TOKEN_SPLIT_RE.split(normalized_model_id) if token
    )
    if any(token in _SPECIAL_PURPOSE_TOKENS for token in tokens):
        return True
    return any(
        _contains_token_sequence(tokens, sequence)
        for sequence in _SPECIAL_PURPOSE_TOKEN_SEQUENCES
    )


def _strip_model_status(model_id: str) -> tuple[str, int]:
    if model_id.endswith("-latest"):
        return model_id.removesuffix("-latest"), 3
    if model_id.endswith("-preview"):
        return model_id.removesuffix("-preview"), 2
    if _ISO_DATE_SUFFIX_RE.search(model_id):
        return _ISO_DATE_SUFFIX_RE.sub("", model_id), 1
    if _RELEASE_SUFFIX_RE.search(model_id):
        return _RELEASE_SUFFIX_RE.sub("", model_id), 1
    return model_id, 0


def _generation(
    match: re.Match[str],
    major_group: int,
    minor_group: int,
) -> tuple[int, int]:
    return int(match.group(major_group)), int(match.group(minor_group) or 0)


def _parse_openai_model(model_id: str, status_order: int) -> _ParsedModelRank | None:
    match = re.fullmatch(r"gpt-(\d+)o(?:-(mini))?", model_id)
    if match:
        tier = match.group(2) or "general"
        return _ParsedModelRank(int(match.group(1)), 0, _TIER_ORDER[tier], status_order)

    match = re.fullmatch(
        r"gpt-(\d+)(?:\.(\d+))?(?:-(mini|nano|pro))?",
        model_id,
    )
    if match:
        major, minor = _generation(match, 1, 2)
        tier = match.group(3) or "general"
        return _ParsedModelRank(major, minor, _TIER_ORDER[tier], status_order)

    match = re.fullmatch(
        r"o(\d+)(?:\.(\d+))?(?:-(mini|nano|pro))?",
        model_id,
    )
    if match:
        major, minor = _generation(match, 1, 2)
        tier = match.group(3) or "general"
        return _ParsedModelRank(major, minor, _TIER_ORDER[tier], status_order)
    return None


def _parse_anthropic_model(
    model_id: str,
    status_order: int,
) -> _ParsedModelRank | None:
    product_first = re.fullmatch(
        r"claude-(sonnet|haiku|opus|fable)-(\d+)(?:-(\d+))?",
        model_id,
    )
    if product_first:
        tier = {
            "sonnet": "general",
            "haiku": "mini",
            "opus": "pro",
            "fable": "pro",
        }[product_first.group(1)]
        major, minor = _generation(product_first, 2, 3)
        return _ParsedModelRank(major, minor, _TIER_ORDER[tier], status_order)

    generation_first = re.fullmatch(
        r"claude-(\d+)(?:-(\d+))?-(sonnet|haiku|opus|fable)",
        model_id,
    )
    if generation_first:
        tier = {
            "sonnet": "general",
            "haiku": "mini",
            "opus": "pro",
            "fable": "pro",
        }[generation_first.group(3)]
        major, minor = _generation(generation_first, 1, 2)
        return _ParsedModelRank(major, minor, _TIER_ORDER[tier], status_order)
    return None


def _parse_google_model(model_id: str, status_order: int) -> _ParsedModelRank | None:
    match = re.fullmatch(
        r"gemini-(\d+)(?:\.(\d+))?(?:-(flash-lite|flash|pro))?",
        model_id,
    )
    if not match:
        return None
    tier = {
        None: "general",
        "flash": "mini",
        "flash-lite": "nano",
        "pro": "pro",
    }[match.group(3)]
    major, minor = _generation(match, 1, 2)
    return _ParsedModelRank(major, minor, _TIER_ORDER[tier], status_order)


def _parse_model_rank(provider_name: str, model_id: str) -> _ParsedModelRank | None:
    normalized_provider = _normalize_provider_name(provider_name)
    normalized_model_id = _normalize_model_id(model_id)
    base_model_id, status_order = _strip_model_status(normalized_model_id)
    if normalized_provider == "openai":
        return _parse_openai_model(base_model_id, status_order)
    if normalized_provider == "anthropic":
        return _parse_anthropic_model(base_model_id, status_order)
    if normalized_provider == "google":
        return _parse_google_model(base_model_id, status_order)
    return None


def _candidate_sort_key(candidate: ModelRecommendationCandidate) -> tuple[object, ...]:
    provider_name = _normalize_provider_name(candidate.provider_name)
    parsed = _parse_model_rank(provider_name, candidate.model_id)
    common_tie_break = (
        candidate.relation_priority,
        candidate.model_name.strip().casefold(),
        candidate.credential_name.strip().casefold(),
        candidate.model_stable_id.strip().casefold(),
        candidate.credential_stable_id.strip().casefold(),
    )
    if parsed is None:
        return (
            _PROVIDER_ORDER[provider_name],
            1,
            0,
            0,
            0,
            0,
            *common_tie_break,
        )
    return (
        _PROVIDER_ORDER[provider_name],
        0,
        -parsed.generation_major,
        -parsed.generation_minor,
        parsed.tier_order,
        parsed.status_order,
        *common_tie_break,
    )


def sort_model_candidates(
    candidates: Iterable[ModelRecommendationCandidate],
) -> list[ModelRecommendationCandidate]:
    eligible = [
        candidate
        for candidate in candidates
        if _normalize_provider_name(candidate.provider_name) in _PROVIDER_ORDER
        and not _is_special_purpose_model(
            candidate.provider_name,
            candidate.model_id,
        )
    ]
    return sorted(eligible, key=_candidate_sort_key)
