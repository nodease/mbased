from apps.gateway.api.v1.endpoints.workflow import (
    _safe_stream_event,
    _safe_task_error,
)
from apps.gateway.services.deployment_service import _safe_deployment_error_detail


def test_safe_task_error_normalizes_external_effect_message() -> None:
    marker = "provider-private-error-detail"

    result = _safe_task_error(
        {
            "code": "external_effect.outcome_unknown",
            "message": marker,
            "retryable": False,
            "node_id": "http-1",
        }
    )

    assert result == {
        "code": "external_effect.outcome_unknown",
        "message": "external_effect.outcome_unknown",
        "retryable": False,
        "node_id": "http-1",
    }
    assert marker not in str(result)


def test_safe_task_error_rejects_unknown_external_effect_code() -> None:
    assert (
        _safe_task_error(
            {
                "code": "external_effect.provider-private-code",
                "message": "provider-private-detail",
                "retryable": False,
            }
        )
        is None
    )


def test_safe_task_error_rejects_internal_retry_signal() -> None:
    assert (
        _safe_task_error(
            {
                "code": "external_effect.retry_allowed",
                "message": "internal-retry-detail",
                "retryable": True,
            }
        )
        is None
    )


def test_safe_stream_event_normalizes_external_effect_error() -> None:
    event = _safe_stream_event(
        {
            "type": "error",
            "data": {
                "code": "external_effect.outcome_unknown",
                "message": "provider-private-error-detail",
                "retryable": False,
                "node_id": "http-1",
                "raw_response": "must-not-pass",
            },
        }
    )

    assert event == {
        "type": "error",
        "data": {
            "code": "external_effect.outcome_unknown",
            "message": "external_effect.outcome_unknown",
            "retryable": False,
            "node_id": "http-1",
        },
    }


def test_safe_stream_event_replaces_unknown_external_effect_error() -> None:
    event = _safe_stream_event(
        {
            "type": "error",
            "data": {
                "code": "external_effect.provider-private-code",
                "message": "provider-private-error-detail",
                "retryable": False,
            },
        }
    )

    assert event == {
        "type": "error",
        "data": {"message": "workflow.execution_failed"},
    }


def test_deployment_error_detail_normalizes_external_effect_error() -> None:
    detail = _safe_deployment_error_detail(
        {
            "code": "external_effect.identity_conflict",
            "message": "provider-private-error-detail",
            "retryable": False,
            "node_id": "http-1",
            "raw_response": "must-not-pass",
        }
    )

    assert detail == {
        "code": "external_effect.identity_conflict",
        "message": "external_effect.identity_conflict",
        "retryable": False,
        "node_id": "http-1",
    }


def test_deployment_error_detail_replaces_unknown_external_effect_error() -> None:
    detail = _safe_deployment_error_detail(
        {
            "code": "external_effect.provider-private-code",
            "message": "provider-private-error-detail",
            "retryable": False,
        }
    )

    assert detail == "Workflow execution failed"
