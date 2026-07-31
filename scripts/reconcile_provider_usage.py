"""Resolve one investigated provider usage outcome without provider replay."""

from __future__ import annotations

import argparse
import json
import uuid

from apps.shared.db.session import SessionLocal
from apps.shared.domain.provider_usage_ledger import (
    ProviderUsageMeasurement,
)
from apps.shared.services.provider_usage_ledger import ProviderUsageLedgerService

_DEFINITIVE_REASONS = ("provider_not_sent", "provider_rejected")


def _positive_integer(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("value must be a positive integer")
    return parsed


def _nonnegative_integer(value: str) -> int:
    parsed = int(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("value must be a non-negative integer")
    return parsed


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Resolve one investigated outcome-unknown ledger operation without "
            "replaying the provider call."
        )
    )
    parser.add_argument("--organization-id", required=True, type=uuid.UUID)
    parser.add_argument("--operation-id", required=True, type=uuid.UUID)
    parser.add_argument(
        "--expected-state-version",
        required=True,
        type=_positive_integer,
    )
    parser.add_argument(
        "--resolution",
        required=True,
        choices=("succeeded", "failed_definitive"),
    )
    parser.add_argument("--prompt-tokens", type=_nonnegative_integer)
    parser.add_argument("--completion-tokens", type=_nonnegative_integer)
    parser.add_argument("--total-cost-microusd", type=_nonnegative_integer)
    parser.add_argument("--latency-ms", type=_nonnegative_integer)
    parser.add_argument("--reason-code", choices=_DEFINITIVE_REASONS)
    return parser.parse_args()


def _validate_resolution_arguments(args: argparse.Namespace) -> None:
    usage_values = (
        args.prompt_tokens,
        args.completion_tokens,
        args.total_cost_microusd,
        args.latency_ms,
    )
    if args.resolution == "succeeded":
        if any(value is None for value in usage_values) or args.reason_code is not None:
            raise ValueError("success resolution requires usage only")
        return
    if any(value is not None for value in usage_values) or args.reason_code is None:
        raise ValueError("definitive resolution requires one safe reason only")


def _print_failed() -> int:
    print(json.dumps({"status": "failed"}))
    return 3


def _close_session(db: object) -> bool:
    try:
        db.close()  # type: ignore[attr-defined]
    except Exception:
        return False
    return True


def main() -> int:
    args = _arguments()
    try:
        _validate_resolution_arguments(args)
    except ValueError:
        return _print_failed()

    try:
        db = SessionLocal()
    except Exception:
        return _print_failed()

    try:
        service = ProviderUsageLedgerService()
        if args.resolution == "succeeded":
            operation = service.reconcile_success(
                db,
                organization_id=args.organization_id,
                operation_id=args.operation_id,
                expected_state_version=args.expected_state_version,
                measurement=ProviderUsageMeasurement(
                    prompt_tokens=args.prompt_tokens,
                    completion_tokens=args.completion_tokens,
                    total_cost_microusd=args.total_cost_microusd,
                    latency_ms=args.latency_ms,
                ),
            )
        else:
            operation = service.reconcile_definitive_failure(
                db,
                organization_id=args.organization_id,
                operation_id=args.operation_id,
                expected_state_version=args.expected_state_version,
                reason_code=args.reason_code,
            )
    except Exception:
        _close_session(db)
        return _print_failed()

    if not _close_session(db):
        return _print_failed()

    print(
        json.dumps(
            {
                "status": "complete",
                "operation_id": str(operation.id),
                "state": operation.state.value,
                "state_version": operation.state_version,
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
