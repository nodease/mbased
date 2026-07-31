import subprocess
import sys
from pathlib import Path

import pytest
from apps.shared.domain.schedule_dispatch_rollout import (
    ScheduleDispatchRolloutError,
    ScheduleDispatchRolloutState,
    evaluate_schedule_dispatch_rollout,
)

DISABLED = "v1|disabled|5"
DRAIN = "v1|drain|5"
CLAIM = "v1|claim|5"
GATEWAY_IMAGE = "registry/gateway:commit"
WORKER_IMAGE = "registry/worker:commit"
ROOT_DIR = Path(__file__).resolve().parents[4]
ROLLOUT_SCRIPT = ROOT_DIR / "apps/shared/domain/schedule_dispatch_rollout.py"


def _state(fingerprint, image, *, ready=True):
    return ScheduleDispatchRolloutState(
        fingerprint=fingerprint,
        image=image,
        ready=ready,
    )


@pytest.mark.parametrize(
    ("desired", "previous", "gateway", "worker", "expected"),
    [
        (DISABLED, DISABLED, None, None, "all"),
        (
            CLAIM,
            DRAIN,
            _state(DRAIN, "registry/gateway:old"),
            _state(DRAIN, "registry/worker:old"),
            "all",
        ),
        (
            CLAIM,
            DRAIN,
            _state(DRAIN, "registry/gateway:old"),
            _state(CLAIM, WORKER_IMAGE),
            "gateway",
        ),
        (
            DRAIN,
            CLAIM,
            _state(DRAIN, GATEWAY_IMAGE),
            _state(CLAIM, "registry/worker:old"),
            "worker",
        ),
        (
            CLAIM,
            DRAIN,
            _state(CLAIM, GATEWAY_IMAGE),
            _state(CLAIM, WORKER_IMAGE),
            "none",
        ),
        (
            CLAIM,
            CLAIM,
            _state(CLAIM, "registry/gateway:old"),
            _state(CLAIM, WORKER_IMAGE),
            "gateway",
        ),
    ],
)
def test_rollout_state_matrix(desired, previous, gateway, worker, expected):
    assert (
        evaluate_schedule_dispatch_rollout(
            desired_fingerprint=desired,
            previous_fingerprint=previous,
            desired_gateway_image=GATEWAY_IMAGE,
            desired_worker_image=WORKER_IMAGE,
            gateway=gateway,
            worker=worker,
        )
        == expected
    )


@pytest.mark.parametrize(
    ("desired", "previous", "gateway", "worker"),
    [
        (CLAIM, DRAIN, None, None),
        (
            CLAIM,
            DRAIN,
            _state(DRAIN, "registry/gateway:old"),
            _state(CLAIM, "registry/worker:wrong"),
        ),
        (
            DRAIN,
            CLAIM,
            _state(CLAIM, "registry/gateway:old"),
            _state(DRAIN, WORKER_IMAGE),
        ),
        (
            CLAIM,
            DRAIN,
            _state("v1|disabled|5", "registry/gateway:old"),
            _state("v1|disabled|5", "registry/worker:old"),
        ),
        (
            "v1|claim|6",
            CLAIM,
            _state(CLAIM, "registry/gateway:old"),
            _state(CLAIM, "registry/worker:old"),
        ),
        (
            DISABLED,
            CLAIM,
            _state(CLAIM, "registry/gateway:old"),
            _state(CLAIM, "registry/worker:old"),
        ),
        (
            "v1|claim|6",
            CLAIM,
            _state("v1|claim|6", GATEWAY_IMAGE),
            _state("v1|claim|6", WORKER_IMAGE),
        ),
        (
            CLAIM,
            DRAIN,
            _state(CLAIM, "registry/gateway:old"),
            _state(CLAIM, WORKER_IMAGE),
        ),
    ],
)
def test_rollout_state_rejects_non_resumable_mismatch(
    desired, previous, gateway, worker
):
    with pytest.raises(ScheduleDispatchRolloutError):
        evaluate_schedule_dispatch_rollout(
            desired_fingerprint=desired,
            previous_fingerprint=previous,
            desired_gateway_image=GATEWAY_IMAGE,
            desired_worker_image=WORKER_IMAGE,
            gateway=gateway,
            worker=worker,
        )


@pytest.mark.parametrize(
    ("desired", "previous", "gateway", "worker", "expected"),
    [
        (
            CLAIM,
            DRAIN,
            _state(DRAIN, "registry/gateway:old"),
            _state(CLAIM, WORKER_IMAGE, ready=False),
            "all",
        ),
        (
            CLAIM,
            DRAIN,
            _state(CLAIM, GATEWAY_IMAGE, ready=False),
            _state(CLAIM, WORKER_IMAGE),
            "gateway",
        ),
        (
            DRAIN,
            CLAIM,
            _state(DRAIN, GATEWAY_IMAGE, ready=False),
            _state(CLAIM, "registry/worker:old"),
            "all",
        ),
        (
            DRAIN,
            CLAIM,
            _state(DRAIN, GATEWAY_IMAGE),
            _state(DRAIN, WORKER_IMAGE, ready=False),
            "worker",
        ),
    ],
)
def test_rollout_reapplies_unready_desired_stage_in_safe_order(
    desired, previous, gateway, worker, expected
):
    assert (
        evaluate_schedule_dispatch_rollout(
            desired_fingerprint=desired,
            previous_fingerprint=previous,
            desired_gateway_image=GATEWAY_IMAGE,
            desired_worker_image=WORKER_IMAGE,
            gateway=gateway,
            worker=worker,
        )
        == expected
    )


def test_rollout_cli_runs_without_site_packages():
    completed = subprocess.run(
        [
            sys.executable,
            "-S",
            str(ROLLOUT_SCRIPT),
            "--desired",
            DISABLED,
            "--previous",
            DISABLED,
            "--desired-gateway-image",
            GATEWAY_IMAGE,
            "--desired-worker-image",
            WORKER_IMAGE,
            "--gateway-fingerprint",
            DISABLED,
            "--gateway-image",
            GATEWAY_IMAGE,
            "--gateway-ready",
            "true",
            "--worker-fingerprint",
            DISABLED,
            "--worker-image",
            WORKER_IMAGE,
            "--worker-ready",
            "true",
        ],
        cwd=ROOT_DIR,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "none"
    assert completed.stderr == ""
