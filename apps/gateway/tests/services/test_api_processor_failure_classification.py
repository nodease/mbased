from types import SimpleNamespace

import pytest

from apps.gateway.services.ingestion.processors import api_processor
from apps.gateway.services.ingestion.processors.api_processor import ApiProcessor
from apps.shared.services.egress_guard import EgressGuardError


@pytest.mark.parametrize(
    ("egress_reason", "expected_reason"),
    [
        ("egress.timeout", "source.temporarily_unavailable"),
        ("egress.connection_failed", "source.temporarily_unavailable"),
        ("egress.dns_resolution_failed", "source.temporarily_unavailable"),
        ("egress.private_target", "configuration.invalid"),
    ],
)
def test_api_egress_failure_is_classified(
    monkeypatch,
    egress_reason: str,
    expected_reason: str,
) -> None:
    monkeypatch.setattr(
        api_processor,
        "safe_http_request",
        lambda **_kwargs: (_ for _ in ()).throw(EgressGuardError(egress_reason)),
    )

    result = ApiProcessor().process({"url": "https://example.com/data"})

    assert result.metadata["reason_code"] == expected_reason


@pytest.mark.parametrize(
    ("status_code", "expected_reason"),
    [
        (400, "configuration.invalid"),
        (408, "source.temporarily_unavailable"),
        (429, "source.temporarily_unavailable"),
        (503, "source.temporarily_unavailable"),
    ],
)
def test_api_http_failure_is_classified(
    monkeypatch,
    status_code: int,
    expected_reason: str,
) -> None:
    monkeypatch.setattr(
        api_processor,
        "safe_http_request",
        lambda **_kwargs: SimpleNamespace(status_code=status_code),
    )

    result = ApiProcessor().process({"url": "https://example.com/data"})

    assert result.metadata["reason_code"] == expected_reason
