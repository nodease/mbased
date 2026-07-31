from apps.gateway.application.agent_builder.model_recommendation_policy import (
    ModelRecommendationCandidate,
    sort_model_candidates,
)


def _candidate(
    provider_name: str,
    model_id: str,
    *,
    model_name: str | None = None,
    relation_priority: int = 0,
    credential_name: str = "credential",
) -> ModelRecommendationCandidate:
    return ModelRecommendationCandidate(
        provider_name=provider_name,
        model_id=model_id,
        model_name=model_name or model_id,
        relation_priority=relation_priority,
        credential_name=credential_name,
        model_stable_id=f"model:{model_id}",
        credential_stable_id=f"credential:{credential_name}",
    )


def _model_ids(candidates: list[ModelRecommendationCandidate]) -> list[str]:
    return [candidate.model_id for candidate in candidates]


def test_sort_model_candidates_uses_provider_generation_tier_and_status_order():
    candidates = [
        _candidate("google", "gemini-9-pro"),
        _candidate("anthropic", "claude-sonnet-9"),
        _candidate("openai", "gpt-5.5-latest"),
        _candidate("openai", "gpt-5.5-pro"),
        _candidate("openai", "gpt-5.5-mini"),
        _candidate("openai", "gpt-5.5-preview"),
        _candidate("openai", "gpt-5.5-nano"),
        _candidate("openai", "gpt-5.5-2026-04-23"),
        _candidate("openai", "gpt-5.5"),
        _candidate("openai", "gpt-5.6-pro"),
    ]

    assert _model_ids(sort_model_candidates(candidates)) == [
        "gpt-5.6-pro",
        "gpt-5.5",
        "gpt-5.5-2026-04-23",
        "gpt-5.5-preview",
        "gpt-5.5-latest",
        "gpt-5.5-mini",
        "gpt-5.5-nano",
        "gpt-5.5-pro",
        "claude-sonnet-9",
        "gemini-9-pro",
    ]


def test_sort_model_candidates_parses_current_provider_model_id_forms():
    candidates = [
        _candidate("google", "gemini-3.1-pro-preview"),
        _candidate("google", "gemini-3.1-flash-lite"),
        _candidate("google", "gemini-3.1-flash"),
        _candidate("google", "gemini-3.1"),
        _candidate("anthropic", "claude-3-5-opus"),
        _candidate("anthropic", "claude-3-5-haiku"),
        _candidate("anthropic", "claude-3-5-sonnet-latest"),
        _candidate("anthropic", "claude-sonnet-4-5-20250929"),
        _candidate("openai", "o4-mini"),
        _candidate("openai", "gpt-4o-mini"),
        _candidate("openai", "gpt-4o"),
    ]

    assert _model_ids(sort_model_candidates(candidates)) == [
        "gpt-4o",
        "gpt-4o-mini",
        "o4-mini",
        "claude-sonnet-4-5-20250929",
        "claude-3-5-sonnet-latest",
        "claude-3-5-haiku",
        "claude-3-5-opus",
        "gemini-3.1",
        "gemini-3.1-flash",
        "gemini-3.1-flash-lite",
        "gemini-3.1-pro-preview",
    ]


def test_sort_model_candidates_excludes_only_explicit_special_purpose_ids():
    candidates = [
        _candidate("openai", "gpt-4o-mini-tts"),
        _candidate("openai", "gpt-5.3-codex"),
        _candidate("google", "gemini-2.5-flash-image-preview"),
        _candidate("google", "gemini-2.5-computer-use-preview"),
        _candidate("openai", "gpt-livestream"),
        _candidate("openai", "gpt-researcher"),
        _candidate("openai", "gpt-audiophile"),
        _candidate("openai", "gpt-custom"),
        _candidate("llamaparse", "llama-parse"),
    ]

    assert _model_ids(sort_model_candidates(candidates)) == [
        "gpt-audiophile",
        "gpt-custom",
        "gpt-livestream",
        "gpt-researcher",
    ]


def test_sort_model_candidates_is_deterministic_for_ties_and_input_order():
    candidates = [
        _candidate(
            " OpenAI ",
            " models/GPT-5.5 ",
            model_name="Zulu",
            relation_priority=1,
            credential_name="Beta",
        ),
        _candidate(
            "openai",
            "gpt-5.5",
            model_name="Alpha",
            relation_priority=1,
            credential_name="Beta",
        ),
        _candidate(
            "OPENAI",
            "gpt-5.5",
            model_name="Alpha",
            relation_priority=0,
            credential_name="Zulu",
        ),
    ]

    expected = _model_ids(sort_model_candidates(candidates))

    assert expected == ["gpt-5.5", "gpt-5.5", " models/GPT-5.5 "]
    assert _model_ids(sort_model_candidates(list(reversed(candidates)))) == expected
