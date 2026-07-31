from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[3]


def test_dev_script_starts_and_stops_log_system_beat_process():
    script = (ROOT_DIR / "scripts/dev.sh").read_text(encoding="utf-8")

    assert "-A apps.log_system.main beat" in script
    assert "LOG_CELERY_BEAT_PID=$!" in script
    assert 'stop_managed_process "$LOG_CELERY_BEAT_PID"' in script


def test_docker_compose_runs_dedicated_log_system_beat_service():
    compose = (ROOT_DIR / "docker/docker-compose.yml").read_text(encoding="utf-8")

    assert "\n  log_system_beat:" in compose
    beat_service = compose.split("\n  log_system_beat:", maxsplit=1)[1].split(
        "\n  frontend:", maxsplit=1
    )[0]
    assert "container_name: moduly-log-system-beat" in beat_service
    assert "entrypoint:" in beat_service
    assert "apps.log_system.main" in beat_service
    assert "beat" in beat_service
