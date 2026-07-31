from apps.shared.services.llm_model_pricing import (
    calculate_text_token_cost,
    extract_cached_input_tokens,
    get_model_pricing,
    known_model_prices,
    normalize_model_pricing_id,
    pricing_estimate_metadata,
)


def test_gemini_35_flash_uses_official_standard_text_prices():
    pricing = get_model_pricing("gemini-3.5-flash")

    assert pricing is not None
    assert pricing.standard_input_per_1k == 0.00075
    assert pricing.standard_output_per_1k == 0.0045


def test_model_pricing_normalizes_google_prefix_and_version_aliases():
    assert normalize_model_pricing_id("models/gemini-3.5-flash") == "gemini-3.5-flash"
    assert normalize_model_pricing_id("gpt-4o-2024-11-20") == "gpt-4o"


def test_openai_cached_input_tokens_use_cached_input_price():
    cost = calculate_text_token_cost(
        "gpt-4o-mini",
        prompt_tokens=1_000,
        completion_tokens=500,
        cached_input_tokens=400,
    )

    # $0.15 / 1M standard input, $0.075 / 1M cached input,
    # $0.60 / 1M output.
    assert cost == 0.00042


def test_cached_tokens_are_read_from_provider_prompt_details():
    assert extract_cached_input_tokens(
        {"prompt_tokens_details": {"cached_tokens": 400}}
    ) == 400


def test_known_model_prices_include_executable_provider_aliases():
    prices = known_model_prices()

    assert prices["claude-haiku-4-5-20251001"] == prices["claude-haiku-4-5"]
    assert prices["claude-sonnet-4-5-20250929"] == prices["claude-sonnet-4-5"]


def test_pricing_metadata_marks_unsupported_terms_as_standard_estimate():
    metadata = pricing_estimate_metadata("gemini-3.5-flash")

    assert metadata["basis"] == "standard_text_tokens"
    assert metadata["unsupported_conditions"]["long_context"] is True
    assert metadata["unsupported_conditions"]["batch"] is True


def test_unknown_conditional_prices_are_reported_as_standard_estimate():
    pricing = get_model_pricing("gemini-3.5-flash")

    assert pricing is not None
    assert pricing.long_context_input_per_1k is None
    assert pricing.batch_input_per_1k is None
