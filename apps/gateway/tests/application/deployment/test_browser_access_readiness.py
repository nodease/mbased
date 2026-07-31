from __future__ import annotations

from apps.gateway.application.deployment.browser_access_readiness import (
    BrowserAccessReadinessCandidate,
    build_browser_access_readiness_report,
)


def _candidate(identifier: str, policy: object) -> BrowserAccessReadinessCandidate:
    return BrowserAccessReadinessCandidate(
        deployment_id=identifier,
        deployment_version=2,
        deployment_type="chatbot",
        browser_access_policy=policy,
    )


def _policy(*origins: str, enabled: bool) -> dict:
    return {
        "contract_version": "deployment_browser_access.v1",
        "embedding": {
            "enabled": enabled,
            "parent_origins": list(origins),
        },
    }


def test_readiness_report_classifies_all_policy_states_without_raw_values() -> None:
    sensitive_origin = "https://internal-only.example.com"
    report = build_browser_access_readiness_report(
        [
            _candidate("deployment-null", None),
            _candidate("deployment-malformed", {"parent_origins": ["secret"]}),
            _candidate("deployment-disabled", _policy(enabled=False)),
            _candidate(
                "deployment-enabled",
                _policy(sensitive_origin, enabled=True),
            ),
        ]
    ).safe_dict()

    assert report["counts"] == {
        "total": 4,
        "legacy_null": 1,
        "malformed": 1,
        "disabled": 1,
        "enabled": 1,
    }
    assert [item["policy_state"] for item in report["deployments"]] == [
        "disabled",
        "enabled",
        "malformed",
        "legacy_null",
    ]
    assert sensitive_origin not in str(report)
    assert "browser_access_policy" not in str(report)


def test_non_production_http_policy_is_not_ready_for_enforcement() -> None:
    report = build_browser_access_readiness_report(
        [
            _candidate(
                "deployment-http",
                _policy("http://localhost:3000", enabled=True),
            )
        ]
    ).safe_dict()

    assert report["counts"]["malformed"] == 1


def test_non_object_json_values_are_reported_as_malformed() -> None:
    malformed_values = ([{}], "invalid", 1, True)

    report = build_browser_access_readiness_report(
        [
            _candidate(f"deployment-malformed-{index}", value)
            for index, value in enumerate(malformed_values)
        ]
    ).safe_dict()

    assert report["counts"]["total"] == len(malformed_values)
    assert report["counts"]["malformed"] == len(malformed_values)
    assert {
        item["policy_state"] for item in report["deployments"]
    } == {"malformed"}
