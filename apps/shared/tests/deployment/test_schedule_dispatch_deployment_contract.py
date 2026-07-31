from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[4]

SCHEDULE_ENV_NAMES = (
    "SCHEDULE_DISPATCH_MODE",
    "SCHEDULE_DISPATCH_MODE_FINGERPRINT",
    "SCHEDULE_DISPATCH_POLL_SECONDS",
    "SCHEDULE_OCCURRENCE_BATCH_SIZE",
    "SCHEDULE_DISPATCH_BATCH_SIZE",
    "SCHEDULE_RECOVERY_BATCH_SIZE",
    "SCHEDULE_CLEANUP_BATCH_SIZE",
    "SCHEDULE_DISPATCH_LEASE_SECONDS",
    "SCHEDULE_ENQUEUED_DELIVERY_TIMEOUT_SECONDS",
    "SCHEDULE_EXECUTION_DEADLINE_SECONDS",
    "SCHEDULE_WORKFLOW_RUN_VISIBILITY_TIMEOUT_SECONDS",
    "SCHEDULE_DISPATCH_MAX_ATTEMPTS",
    "SCHEDULE_DISPATCH_RETRY_BASE_SECONDS",
    "SCHEDULE_DISPATCH_RETENTION_DAYS",
    "SCHEDULE_DISPATCH_DEAD_LETTER_RETENTION_DAYS",
)


def _read(relative_path: str) -> str:
    return (REPOSITORY_ROOT / relative_path).read_text(encoding="utf-8")


def test_helm_values_explicitly_start_schedule_dispatch_disabled():
    for relative_path in (
        "infra/helm/moduly/values.yaml",
        "infra/helm/moduly/values-local.yaml",
        "infra/helm/moduly/values-production.yaml",
    ):
        content = _read(relative_path)
        assert "scheduleDispatch:" in content
        assert 'mode: "disabled"' in content


def test_helm_gateway_and_worker_use_one_schedule_dispatch_environment_contract():
    helper = _read("infra/helm/moduly/templates/_helpers.tpl")
    validation = _read(
        "infra/helm/moduly/templates/schedule-dispatch-validation.yaml"
    )
    gateway = _read("infra/helm/moduly/templates/gateway-deployment.yaml")
    worker = _read("infra/helm/moduly/templates/worker-deployment.yaml")

    for env_name in SCHEDULE_ENV_NAMES:
        assert f"- name: {env_name}" in helper
    assert 'include "moduly.scheduleDispatchEnv"' in gateway
    assert 'include "moduly.scheduleDispatchEnv"' in worker
    assert "nodease.io/schedule-dispatch-fingerprint:" in gateway
    assert "nodease.io/schedule-dispatch-fingerprint:" in worker
    assert 'define "moduly.scheduleDispatchFingerprint"' in helper
    assert 'define "moduly.validateScheduleDispatchMode"' in helper
    assert (
        "non-disabled schedule dispatch is unsupported by the current "
        "provider-neutral deployment surface"
        in helper
    )
    assert 'include "moduly.validateScheduleDispatchMode"' in validation
    assert ".Values.gateway.enabled" not in validation
    assert ".Values.worker.enabled" not in validation
    assert (
        "metadata.annotations['nodease.io/schedule-dispatch-fingerprint']" in helper
    )


def test_compose_keeps_gateway_worker_schedule_settings_aligned():
    compose = _read("docker/docker-compose.yml")

    for env_name in SCHEDULE_ENV_NAMES:
        assert f"{env_name}:" in compose
        if env_name not in (
            "SCHEDULE_DISPATCH_MODE",
            "SCHEDULE_DISPATCH_MODE_FINGERPRINT",
        ):
            assert f"${{{env_name}:-" in compose
    assert compose.count("SCHEDULE_DISPATCH_MODE: disabled") == 2
    assert "${SCHEDULE_DISPATCH_MODE" not in compose
    assert 'SCHEDULE_DISPATCH_MODE_FINGERPRINT: "v1|' in compose
    assert compose.count('SCHEDULE_DISPATCH_MODE_FINGERPRINT: "v1|disabled|') == 2
    assert "${SCHEDULE_DISPATCH_LEASE_SECONDS:-60}" in compose
