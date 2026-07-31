import secrets

from apps.shared.domain.app_auth_secret import (
    APP_AUTH_SECRET_PREFIX,
    generate_app_auth_secret,
)
from apps.shared.services.tracing.policy import ResolvedRedactionPolicy
from apps.shared.services.tracing.redaction import TraceRedactionService


def test_redaction_masks_pii_and_sensitive_headers():
    result = TraceRedactionService.redact_payload(
        {
            "email": "person@example.com",
            "phone": "+82 10-1234-5678",
            "headers": {
                "Authorization": "Bearer should-not-survive",
                "X-API-Key": "should-not-survive",
            },
        },
        ResolvedRedactionPolicy(),
    )

    assert result.redaction_applied is True
    assert result.pii_detected is True
    assert result.secret_detected is True
    assert result.redacted_payload["email"] == "[REDACTED]"
    assert result.redacted_payload["phone"] == "[REDACTED]"
    assert result.redacted_payload["headers"]["Authorization"] == "[REDACTED]"
    assert result.redacted_payload["headers"]["X-API-Key"] == "[REDACTED]"


def test_secret_redaction_stays_enabled_when_policy_redaction_disabled():
    result = TraceRedactionService.redact_payload(
        {
            "password": "should-not-survive",
            "message": "person@example.com",
        },
        ResolvedRedactionPolicy(redaction_enabled=False, pii_detection_enabled=False),
    )

    assert result.secret_detected is True
    assert result.redacted_payload["password"] == "[REDACTED]"
    assert result.redacted_payload["message"] == "person@example.com"


def test_purge_receipt_key_and_free_text_value_are_always_redacted():
    receipt = f"cpr_v1_{secrets.token_urlsafe(32)}"
    result = TraceRedactionService.redact_payload(
        {
            "purge_receipt": receipt,
            "note": f"purge status credential: {receipt}",
        },
        ResolvedRedactionPolicy(redaction_enabled=False, pii_detection_enabled=False),
    )

    assert result.secret_detected is True
    assert result.redacted_payload["purge_receipt"] == "[REDACTED]"
    assert receipt not in str(result.redacted_payload)


def test_public_access_grant_free_text_value_is_always_redacted():
    access_grant = f"cag_v1_{secrets.token_urlsafe(32)}"
    result = TraceRedactionService.redact_payload(
        {"note": f"conversation credential: {access_grant}"},
        ResolvedRedactionPolicy(redaction_enabled=False, pii_detection_enabled=False),
    )

    assert result.secret_detected is True
    assert access_grant not in str(result.redacted_payload)
    assert result.redacted_payload["note"] == "conversation credential: [REDACTED]"


def test_sensitive_json_path_redaction_records_metadata_without_values():
    result = TraceRedactionService.redact_payload(
        {"input": {"account": {"number": "1234567890"}}},
        ResolvedRedactionPolicy(sensitive_json_paths=("payload.input.account.number",)),
    )

    metadata = result.redaction_metadata["redaction"]
    assert result.redacted_payload["input"]["account"]["number"] == "[REDACTED]"
    assert "payload.input.account.number" in metadata["fields"]
    assert "1234567890" not in str(metadata)


def test_token_count_fields_remain_visible_while_auth_tokens_stay_redacted():
    result = TraceRedactionService.redact_payload(
        {
            "parameters": {
                "max_tokens": 700,
                "max_output_tokens": 512,
                "max_completion_tokens": None,
            },
            "usage": {
                "prompt_tokens": 120,
                "completion_tokens": 80,
                "input_tokens": 120,
                "output_tokens": 80,
                "total_tokens": 200,
                "cached_tokens": 40,
                "reasoning_tokens": 20,
                "token_count": 200,
                "context_token_estimate": 180,
            },
            "access_token": "access-secret",
            "refresh_token": "refresh-secret",
            "provider_token": "provider-secret",
            "fencing_token": 123,
            "invalid_parameter": {"max_tokens": "not-a-number"},
        },
        ResolvedRedactionPolicy(),
    )

    assert result.redacted_payload["parameters"] == {
        "max_tokens": 700,
        "max_output_tokens": 512,
        "max_completion_tokens": None,
    }
    assert result.redacted_payload["usage"] == {
        "prompt_tokens": 120,
        "completion_tokens": 80,
        "input_tokens": 120,
        "output_tokens": 80,
        "total_tokens": 200,
        "cached_tokens": 40,
        "reasoning_tokens": 20,
        "token_count": 200,
        "context_token_estimate": 180,
    }
    assert result.redacted_payload["access_token"] == "[REDACTED]"
    assert result.redacted_payload["refresh_token"] == "[REDACTED]"
    assert result.redacted_payload["provider_token"] == "[REDACTED]"
    assert result.redacted_payload["fencing_token"] == "[REDACTED]"
    assert result.redacted_payload["invalid_parameter"]["max_tokens"] == "[REDACTED]"


def test_explicit_sensitive_path_still_redacts_safe_token_count_field():
    result = TraceRedactionService.redact_payload(
        {"parameters": {"max_tokens": 700}},
        ResolvedRedactionPolicy(
            sensitive_json_paths=("payload.parameters.max_tokens",)
        ),
    )

    assert result.redacted_payload["parameters"]["max_tokens"] == "[REDACTED]"


def test_explicit_sensitive_keyword_still_redacts_safe_token_count_field():
    result = TraceRedactionService.redact_payload(
        {"parameters": {"max_tokens": 700}},
        ResolvedRedactionPolicy(sensitive_keywords=("max_tokens",)),
    )

    assert result.redacted_payload["parameters"]["max_tokens"] == "[REDACTED]"


def test_known_provider_credential_prefixes_are_redacted_from_free_text():
    secrets = [
        "github_pat_" + "a" * 40,
        "AKIA" + "A" * 16,
        "AIza" + "a" * 35,
    ]
    result = TraceRedactionService.redact_payload(
        "use " + " and ".join(secrets),
        ResolvedRedactionPolicy(),
        payload_kind="agent_builder_message",
    )

    assert result.secret_detected is True
    assert all(secret not in result.redacted_payload for secret in secrets)


def test_generated_app_secret_is_redacted_from_an_ordinary_free_text_field():
    secret = f"{APP_AUTH_SECRET_PREFIX}{'a' * 42}-"

    result = TraceRedactionService.redact_payload(
        {"note": f"connector echoed {secret}"},
        ResolvedRedactionPolicy(),
    )

    assert result.secret_detected is True
    assert secret not in str(result.redacted_payload)
    assert result.redacted_payload["note"] == "connector echoed [REDACTED]"


def test_generated_app_secret_uses_the_redaction_recognizable_marker():
    assert generate_app_auth_secret().startswith(APP_AUTH_SECRET_PREFIX)
