"""Acknowledge one investigated schedule outcome without replaying it."""

from __future__ import annotations

import argparse
import logging
import sys
import uuid
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from apps.gateway.adapters.audit.sqlalchemy_schedule_dispatch_audit import (  # noqa: E402
    SqlAlchemyScheduleDispatchAuditRecorder,
)
from apps.gateway.adapters.db.schedule_dispatch_repository import (  # noqa: E402
    SqlAlchemyScheduleDispatchRepository,
)
from apps.gateway.adapters.db.sqlalchemy_unit_of_work import (  # noqa: E402
    SqlAlchemyUnitOfWork,
)
from apps.gateway.application.deployment.review_schedule_outcome import (  # noqa: E402
    ScheduleOutcomeReviewUseCase,
)
from apps.shared.db.session import SessionLocal  # noqa: E402
from apps.shared.domain.schedule_dispatch import OUTCOME_RESOLUTIONS  # noqa: E402
from apps.shared.services.schedule_dispatch_observability import (  # noqa: E402
    emit_schedule_dispatch_signal,
)

logger = logging.getLogger(__name__)


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Record a no-replay review for one outcome-unknown claim."
    )
    parser.add_argument("--claim-id", required=True, type=uuid.UUID)
    parser.add_argument("--resolution", required=True, choices=sorted(OUTCOME_RESOLUTIONS))
    parser.add_argument("--operation-correlation-id", required=True)
    return parser.parse_args()


def main() -> int:
    args = _arguments()
    db = SessionLocal()
    try:
        result = ScheduleOutcomeReviewUseCase().review(
            repository=SqlAlchemyScheduleDispatchRepository(db),
            audit=SqlAlchemyScheduleDispatchAuditRecorder(db),
            uow=SqlAlchemyUnitOfWork(db),
            claim_id=args.claim_id,
            resolution=args.resolution,
            operation_correlation_id=args.operation_correlation_id,
        )
    except Exception as exc:
        print(f"schedule outcome review failed: error_type={type(exc).__name__}")
        return 1
    finally:
        db.close()

    print(
        "schedule outcome review recorded: "
        f"claim_id={result.claim_id} resolution={result.resolution}"
    )
    emit_schedule_dispatch_signal(
        logger,
        "schedule_claim_outcome_reviewed_total",
        status="dead_lettered",
        reason=result.resolution,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
