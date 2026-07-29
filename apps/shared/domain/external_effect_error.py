from __future__ import annotations

from collections.abc import Mapping

ALLOWED_EFFECT_ERROR_CODES = frozenset(
    {
        "connection_failed",
        "deadline_exceeded",
        "invalid_prepared_request",
        "provider_call_failed",
        "provider_call_finalize_failed",
        "provider_key_field_conflict",
        "provider_key_request_conflict",
        "provider_rejected_request",
        "response_lost",
        "response_malformed",
        "timeout",
        "unexpected_provider_status",
    }
)

PUBLIC_EXTERNAL_EFFECT_ERROR_CODES = frozenset(
    {
        "external_effect.claim_wait",
        "external_effect.identity_conflict",
        "external_effect.identity_invalid",
        "external_effect.invalid_request",
        "external_effect.outcome_unknown",
        "external_effect.prepare_failed",
        "external_effect.result_unavailable",
        "external_effect.stopped",
        *(f"external_effect.{code}" for code in ALLOWED_EFFECT_ERROR_CODES),
    }
)

ALLOWED_EXTERNAL_EFFECT_CONTROL_CODES = frozenset(
    {
        *PUBLIC_EXTERNAL_EFFECT_ERROR_CODES,
        "external_effect.retry_allowed",
    }
)


def safe_effect_error_code(code: object, *, fallback: str) -> str:
    if isinstance(code, str) and code in ALLOWED_EFFECT_ERROR_CODES:
        return code
    if fallback not in ALLOWED_EFFECT_ERROR_CODES:
        raise ValueError("external effect fallback error code is not allowlisted")
    return fallback


def safe_external_effect_error_payload(value: object) -> dict[str, object] | None:
    if not isinstance(value, Mapping):
        return None
    code = value.get("code")
    if not isinstance(code, str) or code not in PUBLIC_EXTERNAL_EFFECT_ERROR_CODES:
        return None
    if value.get("retryable") is not False:
        return None
    payload: dict[str, object] = {
        "code": code,
        "message": code,
        "retryable": False,
    }
    node_id = value.get("node_id")
    if isinstance(node_id, str):
        payload["node_id"] = node_id
    return payload
