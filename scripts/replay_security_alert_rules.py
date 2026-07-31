"""Replay Security Alert rules without creating alerts, evidence, or notifications."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from uuid import UUID

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from apps.shared.db.models.audit_log import AuditLog  # noqa: E402
from apps.shared.db.session import SessionLocal  # noqa: E402
from apps.shared.services.security_alert_rule_registry import (  # noqa: E402
    SECURITY_ALERT_MAX_WINDOW,
)
from apps.shared.services.security_alert_rule_replay import (  # noqa: E402
    replay_security_alert_rules,
)

REPLAY_LIMIT_EXCEEDED = "security_alert.replay_limit_exceeded"


def _aware_datetime(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise argparse.ArgumentTypeError("expected an ISO-8601 datetime") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise argparse.ArgumentTypeError("datetime must include a timezone")
    return parsed


def _uuid(value: str) -> UUID:
    try:
        return UUID(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("expected a UUID") from error


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Read audit logs and report Security Alert rule matches without writes."
        )
    )
    parser.add_argument("--organization-id", required=True, type=_uuid)
    parser.add_argument("--start-at", required=True, type=_aware_datetime)
    parser.add_argument("--end-at", required=True, type=_aware_datetime)
    parser.add_argument("--limit", type=int, default=10_000)
    return parser


def _load_events(
    db,
    *,
    organization_id: UUID,
    start_at: datetime,
    end_at: datetime,
    limit: int,
) -> list[AuditLog]:
    return (
        db.query(AuditLog)
        .filter(
            AuditLog.audit_metadata["organization_id"].astext
            == str(organization_id),
            AuditLog.action.in_(("permission.denied", "policy.block")),
            AuditLog.occurred_at >= start_at,
            AuditLog.occurred_at < end_at,
        )
        .order_by(AuditLog.occurred_at, AuditLog.id)
        .limit(limit + 1)
        .all()
    )


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.end_at <= args.start_at:
        parser.error("--end-at must be later than --start-at")
    if args.limit < 1 or args.limit > 100_000:
        parser.error("--limit must be between 1 and 100000")

    lookback_started_at = args.start_at - SECURITY_ALERT_MAX_WINDOW
    db = SessionLocal()
    try:
        lookback_events = _load_events(
            db,
            organization_id=args.organization_id,
            start_at=lookback_started_at,
            end_at=args.start_at,
            limit=args.limit,
        )
        evaluation_events = _load_events(
            db,
            organization_id=args.organization_id,
            start_at=args.start_at,
            end_at=args.end_at,
            limit=args.limit,
        )
    finally:
        db.close()

    if len(lookback_events) > args.limit or len(evaluation_events) > args.limit:
        print(
            json.dumps(
                {
                    "dry_run": True,
                    "error": REPLAY_LIMIT_EXCEEDED,
                    "truncated": True,
                },
                sort_keys=True,
            )
        )
        return 2

    events = [*lookback_events, *evaluation_events]
    result = replay_security_alert_rules(
        events,
        activation_started_at=lookback_started_at,
        evaluation_started_at=args.start_at,
        evaluation_ended_at=args.end_at,
    )
    print(
        json.dumps(
            {
                **result.safe_dict(),
                "loaded_event_count": len(events),
                "truncated": False,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
