"""추천 설정 빠른 검증 요청의 멱등성 저장소."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError

from apps.shared.db.models.cost_optimizer import (
    CostOptimizerRecommendationVerification,
)


@dataclass(frozen=True)
class RecommendationVerificationClaim:
    record: CostOptimizerRecommendationVerification
    replay_response: dict[str, Any] | None = None


class CostOptimizerRecommendationVerificationService:
    """같은 Idempotency-Key의 candidate/judge 중복 실행을 차단한다."""

    @staticmethod
    def request_fingerprint(
        *,
        workflow_id: Any,
        node_id: str,
        recommendation_ids: list[str],
        baseline_mode: str,
        recommendation_policy_version: str | None = None,
        recommendation_fingerprint: str | None = None,
        node_config_fingerprint: str | None = None,
    ) -> str:
        payload = {
            "workflow_id": str(workflow_id),
            "node_id": node_id,
            "recommendation_ids": sorted(recommendation_ids),
            "baseline_mode": baseline_mode,
            "recommendation_policy_version": recommendation_policy_version,
            "recommendation_fingerprint": recommendation_fingerprint,
            "node_config_fingerprint": node_config_fingerprint,
        }
        serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()

    @classmethod
    def claim(
        cls,
        db: Any,
        *,
        workflow_id: Any,
        node_id: str,
        user_id: Any,
        idempotency_key: str,
        request_fingerprint: str,
    ) -> RecommendationVerificationClaim:
        existing = cls._find(db, user_id=user_id, idempotency_key=idempotency_key)
        if existing is not None:
            return cls._claim_existing(existing, request_fingerprint)

        record = CostOptimizerRecommendationVerification(
            id=uuid4(),
            workflow_id=workflow_id,
            node_id=node_id,
            created_by=user_id,
            idempotency_key=idempotency_key,
            request_fingerprint=request_fingerprint,
            status="running",
            response_summary={},
        )
        db.add(record)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            existing = cls._find(db, user_id=user_id, idempotency_key=idempotency_key)
            if existing is None:
                raise
            return cls._claim_existing(existing, request_fingerprint)
        return RecommendationVerificationClaim(record=record)

    @classmethod
    def replay_existing(
        cls,
        db: Any,
        *,
        user_id: Any,
        idempotency_key: str,
        request_fingerprint: str,
    ) -> dict[str, Any] | None:
        """Return a completed safe response without creating a new claim."""
        existing = cls._find(
            db,
            user_id=user_id,
            idempotency_key=idempotency_key,
        )
        if existing is None:
            return None
        return cls._claim_existing(existing, request_fingerprint).replay_response

    @staticmethod
    def complete(
        db: Any,
        *,
        record: CostOptimizerRecommendationVerification,
        response: dict[str, Any],
        experiment_id: Any | None,
        candidate_id: Any | None,
    ) -> None:
        record.status = str(response.get("verification_status") or "failed")
        record.experiment_id = experiment_id
        record.candidate_id = candidate_id
        record.response_summary = response
        db.commit()

    @staticmethod
    def fail(
        db: Any,
        *,
        record: CostOptimizerRecommendationVerification,
        status: str = "failed",
    ) -> None:
        record.status = status
        record.response_summary = {
            "verification_status": status,
            "comparison_id": None,
            "candidate_id": None,
            "apply": {
                "allowed": False,
                "requires_confirmation": False,
                "reasons": ["verification_failed"],
            },
        }
        db.commit()

    @staticmethod
    def _find(db: Any, *, user_id: Any, idempotency_key: str):
        return (
            db.query(CostOptimizerRecommendationVerification)
            .filter(
                CostOptimizerRecommendationVerification.created_by == user_id,
                CostOptimizerRecommendationVerification.idempotency_key == idempotency_key,
            )
            .first()
        )

    @staticmethod
    def _claim_existing(
        existing: CostOptimizerRecommendationVerification,
        request_fingerprint: str,
    ) -> RecommendationVerificationClaim:
        if existing.request_fingerprint != request_fingerprint:
            raise HTTPException(
                status_code=409,
                detail="cost_optimizer.idempotency_key_reused",
            )
        if existing.status == "running":
            raise HTTPException(
                status_code=409,
                detail="cost_optimizer.verification_in_progress",
            )
        payload = existing.response_summary
        if not isinstance(payload, dict) or not payload:
            raise HTTPException(
                status_code=409,
                detail="cost_optimizer.verification_result_unavailable",
            )
        return RecommendationVerificationClaim(record=existing, replay_response=payload)
