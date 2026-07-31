import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from apps.shared.db.models.cost_optimizer import CostOptimizerExperiment
from apps.shared.services.tracing.policy import TracePolicyService
from sqlalchemy.orm import Session

DEFAULT_COST_OPTIMIZER_PURGE_LIMIT = 1000
MAX_COST_OPTIMIZER_PURGE_LIMIT = 5000


class CostOptimizerRetentionService:
    """Cost Optimizer experiment/candidate 메타데이터 보관 기간을 관리한다."""

    @staticmethod
    def validate_limit(limit: int) -> int:
        if isinstance(limit, bool):
            raise ValueError("cost_optimizer_retention_purge limit must be an integer.")
        try:
            normalized_limit = int(limit)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "cost_optimizer_retention_purge limit must be an integer."
            ) from exc
        if (
            normalized_limit < 1
            or normalized_limit > MAX_COST_OPTIMIZER_PURGE_LIMIT
        ):
            raise ValueError(
                "cost_optimizer_retention_purge limit must be between "
                f"1 and {MAX_COST_OPTIMIZER_PURGE_LIMIT}."
            )
        return normalized_limit

    @staticmethod
    def expires_at(
        db: Session,
        *,
        app_id: uuid.UUID | None = None,
        organization_id: uuid.UUID | None = None,
        created_at: datetime | None = None,
    ) -> datetime:
        retention = TracePolicyService.resolve_retention_policy(
            db,
            app_id=app_id,
            organization_id=organization_id,
        )
        retention_days = getattr(retention, "metadata_retention_days", None)
        if isinstance(retention_days, bool) or not isinstance(retention_days, int):
            retention_days = (
                TracePolicyService.bootstrap_retention_policy().metadata_retention_days
            )
        base_time = created_at or datetime.now(timezone.utc)
        return base_time + timedelta(days=retention_days)

    @staticmethod
    def purge(
        db: Session,
        *,
        now: datetime | None = None,
        organization_id: uuid.UUID | None = None,
        limit: int = DEFAULT_COST_OPTIMIZER_PURGE_LIMIT,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        normalized_limit = CostOptimizerRetentionService.validate_limit(limit)
        cutoff = now or datetime.now(timezone.utc)
        query = db.query(CostOptimizerExperiment).filter(
            CostOptimizerExperiment.retention_expires_at <= cutoff
        )
        if organization_id is not None:
            query = query.filter(
                CostOptimizerExperiment.organization_id == organization_id
            )
        rows = (
            query.order_by(CostOptimizerExperiment.retention_expires_at.asc())
            .limit(normalized_limit)
            .all()
        )
        would_purge_count = len(rows)

        purged_count = 0
        failed_count = 0
        if not dry_run:
            try:
                for row in rows:
                    db.delete(row)
                    purged_count += 1
                db.commit()
            except Exception:
                db.rollback()
                failed_count = len(rows)
                purged_count = 0
                raise

        return {
            "cutoff": cutoff.isoformat(),
            "would_purge_count": would_purge_count,
            "purged_count": purged_count,
            "failed_count": failed_count,
            "retryable": failed_count > 0,
            "dry_run": dry_run,
        }
