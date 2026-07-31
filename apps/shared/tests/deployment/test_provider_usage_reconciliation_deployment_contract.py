from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]


def test_gateway_image_and_runbook_ship_the_no_replay_reconciliation_command() -> None:
    dockerfile = (ROOT / "docker/gateway/Dockerfile").read_text(encoding="utf-8")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert (
        "COPY scripts/reconcile_provider_usage.py "
        "/app/scripts/reconcile_provider_usage.py" in dockerfile
    )
    assert "python /app/scripts/reconcile_provider_usage.py" in readme
    assert "--expected-state-version" in readme
    assert "provider를 재호출하지" in readme
