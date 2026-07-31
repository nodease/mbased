from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]


def test_dev_script_waits_for_a_service_exit_before_cleanup() -> None:
    script = (ROOT_DIR / "scripts" / "dev.sh").read_text(encoding="utf-8")

    wait_index = script.find('wait_for_first_service_exit "${SERVICE_PIDS[@]}"')
    cleanup_index = script.find('cleanup "$service_exit_code"', wait_index)

    assert wait_index >= 0, "dev.sh must detect the first required service exit"
    assert cleanup_index > wait_index, (
        "dev.sh must clean up the remaining services when one service exits"
    )
    assert "wait -n" not in script, "dev.sh must support Bash versions before 4.3"


def test_dev_script_cleans_up_when_startup_fails() -> None:
    script = (ROOT_DIR / "scripts" / "dev.sh").read_text(encoding="utf-8")

    exit_trap_index = script.find("trap 'cleanup $?' EXIT")
    gateway_failure_index = script.find("Gateway API failed to start")

    assert exit_trap_index >= 0, "dev.sh must clean up on every startup failure"
    assert exit_trap_index < gateway_failure_index


def test_dev_script_separates_log_stream_from_required_docker_health() -> None:
    script = (ROOT_DIR / "scripts" / "dev.sh").read_text(encoding="utf-8")

    service_array = script.split("SERVICE_PIDS=(", 1)[1].split(")", 1)[0]

    assert 'DOCKER_LOG_PID=$!' in script
    assert '"$DOCKER_LOG_PID"' not in service_array
    assert 'DOCKER_WATCHDOG_PID=$!' in script
    assert '"$DOCKER_WATCHDOG_PID"' in service_array
    assert "monitor_docker_services()" in script
    assert "pg_isready" in script
    assert "redis-cli ping" in script
    assert "http://localhost:8194/health" in script


def test_dev_script_waits_for_sandbox_readiness_before_watchdog() -> None:
    script = (ROOT_DIR / "scripts" / "dev.sh").read_text(encoding="utf-8")

    wait_function_index = script.find("wait_for_sandbox_ready()")
    wait_call_index = script.find("wait_for_sandbox_ready", wait_function_index + 1)
    watchdog_start_index = script.find("monitor_docker_services &")

    assert 'SANDBOX_STARTUP_TIMEOUT_SECONDS="${SANDBOX_STARTUP_TIMEOUT_SECONDS:-180}"' in script
    assert wait_function_index >= 0
    assert wait_call_index > wait_function_index
    assert wait_call_index < watchdog_start_index
    assert "sandbox_is_healthy" in script
    assert "Sandbox startup timed out" in script
    assert "background" not in script.lower()


def test_dev_script_bounds_each_sandbox_health_request() -> None:
    script = (ROOT_DIR / "scripts" / "dev.sh").read_text(encoding="utf-8")

    assert (
        'SANDBOX_HEALTH_CONNECT_TIMEOUT_SECONDS="${SANDBOX_HEALTH_CONNECT_TIMEOUT_SECONDS:-2}"'
        in script
    )
    assert (
        'SANDBOX_HEALTH_REQUEST_TIMEOUT_SECONDS="${SANDBOX_HEALTH_REQUEST_TIMEOUT_SECONDS:-5}"'
        in script
    )
    assert 'curl --connect-timeout "$SANDBOX_HEALTH_CONNECT_TIMEOUT_SECONDS"' in script
    assert '--max-time "$request_timeout"' in script
    assert script.count("sandbox_is_healthy") >= 3


def test_dev_gateway_uses_settled_change_supervisor() -> None:
    script = (ROOT_DIR / "scripts" / "dev.sh").read_text(encoding="utf-8")

    assert (
        'DEV_GATEWAY_RELOAD_QUIET_SECONDS="${DEV_GATEWAY_RELOAD_QUIET_SECONDS:-3}"'
        in script
    )
    assert "$VENV_PYTHON scripts/dev_gateway.py" in script
    assert "-m uvicorn apps.gateway.main:app --reload" not in script
