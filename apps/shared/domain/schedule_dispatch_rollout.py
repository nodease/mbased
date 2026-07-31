from __future__ import annotations

import argparse
from dataclasses import dataclass


class ScheduleDispatchRolloutError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ScheduleDispatchRolloutState:
    fingerprint: str
    image: str
    ready: bool = True


def evaluate_schedule_dispatch_rollout(
    *,
    desired_fingerprint: str,
    previous_fingerprint: str,
    desired_gateway_image: str,
    desired_worker_image: str,
    gateway: ScheduleDispatchRolloutState | None,
    worker: ScheduleDispatchRolloutState | None,
) -> str:
    """Return all/gateway/worker/none for a fail-closed staged rollout."""
    if not desired_fingerprint or not previous_fingerprint:
        raise ScheduleDispatchRolloutError("rollout fingerprints are required")
    target_mode = _mode(desired_fingerprint)
    previous_mode = _mode(previous_fingerprint)
    settings_changed = desired_fingerprint != previous_fingerprint
    if previous_mode == "claim" and target_mode == "claim" and settings_changed:
        raise ScheduleDispatchRolloutError(
            "drain mode is required before changing active claim settings"
        )
    if previous_mode == "claim" and target_mode == "disabled":
        raise ScheduleDispatchRolloutError(
            "drain mode is required before disabling schedule dispatch"
        )

    if gateway is None or worker is None:
        if target_mode != "disabled":
            raise ScheduleDispatchRolloutError(
                "non-disabled rollout requires both live deployments"
            )
        existing = gateway or worker
        if existing and existing.fingerprint not in {
            desired_fingerprint,
            previous_fingerprint,
        }:
            raise ScheduleDispatchRolloutError(
                "bootstrap deployment fingerprint is not recognized"
            )
        return "all"

    gateway_desired = (
        gateway.fingerprint == desired_fingerprint
        and gateway.image == desired_gateway_image
        and gateway.ready
    )
    worker_desired = (
        worker.fingerprint == desired_fingerprint
        and worker.image == desired_worker_image
        and worker.ready
    )
    if gateway_desired and worker_desired:
        return "none"

    recognized_fingerprints = {desired_fingerprint, previous_fingerprint}
    if (
        gateway.fingerprint not in recognized_fingerprints
        or worker.fingerprint not in recognized_fingerprints
    ):
        raise ScheduleDispatchRolloutError(
            "live fingerprint does not match a resumable rollout state"
        )
    if (
        settings_changed
        and gateway.fingerprint == desired_fingerprint
        and gateway.image != desired_gateway_image
    ):
        raise ScheduleDispatchRolloutError(
            "desired gateway fingerprint has an unexpected image identity"
        )
    if (
        settings_changed
        and worker.fingerprint == desired_fingerprint
        and worker.image != desired_worker_image
    ):
        raise ScheduleDispatchRolloutError(
            "desired worker fingerprint has an unexpected image identity"
        )
    if settings_changed and target_mode == "claim":
        if (
            gateway.fingerprint == desired_fingerprint
            and worker.fingerprint == previous_fingerprint
        ):
            raise ScheduleDispatchRolloutError(
                "claim rollout cannot advance gateway before worker"
            )
    elif settings_changed and (
        worker.fingerprint == desired_fingerprint
        and gateway.fingerprint == previous_fingerprint
    ):
        raise ScheduleDispatchRolloutError(
            "non-claim rollout cannot advance worker before gateway"
        )

    # A desired Deployment spec is not a completed stage until its Pods have
    # converged. Re-apply the incomplete stage in the safe service order.
    if target_mode == "claim":
        if not worker_desired:
            return "all"
        if not gateway_desired:
            return "gateway"
    else:
        if not gateway_desired:
            return "all"
        if not worker_desired:
            return "worker"

    raise ScheduleDispatchRolloutError(
        "live fingerprints do not match a resumable staged rollout"
    )


def _mode(fingerprint: str) -> str:
    parts = fingerprint.split("|")
    if len(parts) < 3 or parts[0] != "v1" or parts[1] not in {
        "disabled",
        "drain",
        "claim",
    }:
        raise ScheduleDispatchRolloutError("invalid schedule dispatch fingerprint")
    return parts[1]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--desired", required=True)
    parser.add_argument("--previous", required=True)
    parser.add_argument("--desired-gateway-image", required=True)
    parser.add_argument("--desired-worker-image", required=True)
    parser.add_argument("--gateway-fingerprint", default="")
    parser.add_argument("--gateway-image", default="")
    parser.add_argument("--worker-fingerprint", default="")
    parser.add_argument("--worker-image", default="")
    parser.add_argument("--gateway-ready", choices=("true", "false"), default="true")
    parser.add_argument("--worker-ready", choices=("true", "false"), default="true")
    args = parser.parse_args()
    try:
        action = evaluate_schedule_dispatch_rollout(
            desired_fingerprint=args.desired,
            previous_fingerprint=args.previous,
            desired_gateway_image=args.desired_gateway_image,
            desired_worker_image=args.desired_worker_image,
            gateway=(
                ScheduleDispatchRolloutState(
                    fingerprint=args.gateway_fingerprint,
                    image=args.gateway_image,
                    ready=args.gateway_ready == "true",
                )
                if args.gateway_fingerprint
                else None
            ),
            worker=(
                ScheduleDispatchRolloutState(
                    fingerprint=args.worker_fingerprint,
                    image=args.worker_image,
                    ready=args.worker_ready == "true",
                )
                if args.worker_fingerprint
                else None
            ),
        )
    except ScheduleDispatchRolloutError:
        print("schedule dispatch rollout state is not safe")
        return 1
    print(action)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
