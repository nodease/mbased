import logging

from apps.gateway.adapters.csrf import observability
from apps.gateway.adapters.csrf.observability import CsrfObservability


class _MetricSpy:
    def __init__(self):
        self.labels_seen: list[dict[str, str]] = []
        self.increment_count = 0

    def labels(self, **labels):
        self.labels_seen.append(labels)
        return self

    def inc(self):
        self.increment_count += 1


def test_csrf_organization_scope_invalid_is_preserved_and_unknown_stays_bounded(
    monkeypatch,
    caplog,
):
    metric = _MetricSpy()
    monkeypatch.setattr(observability, "CSRF_DENIALS", metric)
    CsrfObservability._local_counters.clear()

    with caplog.at_level(logging.INFO, logger=observability.__name__):
        CsrfObservability.record(
            reason="organization_scope_invalid",
            policy="pre_auth_session",
            method="GET",
        )
        CsrfObservability.record(
            reason="raw-scope-sentinel",
            policy="pre_auth_session",
            method="GET",
        )

    labels = (
        "organization_scope_invalid",
        "pre_auth_session",
        "GET",
    )
    assert CsrfObservability._local_counters[labels] == 1
    assert CsrfObservability._local_counters[
        ("unknown", "pre_auth_session", "GET")
    ] == 1
    assert metric.labels_seen == [
        {
            "reason": "organization_scope_invalid",
            "policy": "pre_auth_session",
            "method": "GET",
        },
        {
            "reason": "unknown",
            "policy": "pre_auth_session",
            "method": "GET",
        },
    ]
    assert metric.increment_count == 2
    assert [record.reason for record in caplog.records[-2:]] == [
        "organization_scope_invalid",
        "unknown",
    ]
