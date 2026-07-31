import logging
from datetime import datetime, timezone
from typing import List
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy import desc, func
from sqlalchemy.orm import Session

from apps.gateway.auth.dependencies import get_current_user
from apps.gateway.auth.permissions import ensure_llm_credential_permission
from apps.gateway.services.llm_service import LLMService
from apps.gateway.services.organization_context import resolve_active_organization_id
from apps.gateway.utils.api_errors import raise_api_error
from apps.gateway.utils.audit import audit
from apps.shared.audit.actions import AuditAction
from apps.shared.db.models.llm import LLMModel, LLMProvider
from apps.shared.db.models.user import User
from apps.shared.db.session import get_db
from apps.shared.schemas.llm import (
    LLMCredentialCreate,
    LLMCredentialModelOptionResponse,
    LLMCredentialResponse,
    LLMModelPricingUpdate,
    LLMModelResponse,
    LLMProviderResponse,
)
from apps.shared.services.provider_usage_cost_read_model import (
    provider_usage_model_subject_rows_subquery,
)
from apps.shared.services.tracing.access import TraceAccessService

router = APIRouter()
logger = logging.getLogger(__name__)


def _raise_internal_operation_error(
    operation: str,
    exc: Exception,
    public_message: str,
) -> None:
    logger.error(
        "LLM endpoint operation failed: operation=%s error_type=%s",
        operation,
        type(exc).__name__,
    )
    raise HTTPException(status_code=500, detail=public_message) from None


def _require_system_admin(db: Session, current_user: User):
    if not TraceAccessService.is_system_admin(db, current_user):
        raise HTTPException(status_code=403, detail="system_admin_required")

# --- Providers (System) ---


@router.get("/providers", response_model=List[LLMProviderResponse])
def get_system_providers(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    List all system-defined LLM providers and their models.
    """
    try:
        return LLMService.get_system_providers(db)
    except Exception as exc:
        _raise_internal_operation_error(
            "provider_lookup", exc, "LLM provider lookup failed."
        )


# --- Credentials (User) ---


@router.get("/my-models", response_model=List[LLMModelResponse])
def get_my_models(
    db: Session = Depends(get_db), current_user: User = Depends(get_current_user)
):
    """
    List all models available to the current user.
    """
    try:
        return LLMService.get_my_available_models(db, current_user.id)
    except Exception as exc:
        _raise_internal_operation_error(
            "model_lookup", exc, "LLM model lookup failed."
        )


@router.get("/my-embedding-models", response_model=List[LLMModelResponse])
def get_my_embedding_models(
    db: Session = Depends(get_db), current_user: User = Depends(get_current_user)
):
    """
    현재 사용자가 사용 가능한 임베딩 모델 목록 조회.
    """
    try:
        return LLMService.get_my_embedding_models(db, current_user.id)
    except Exception as exc:
        _raise_internal_operation_error(
            "embedding_model_lookup", exc, "LLM embedding model lookup failed."
        )


@router.get("/credentials", response_model=List[LLMCredentialResponse])
def get_my_credentials(
    request: Request,
    x_organization_id: str | None = Header(default=None, alias="X-Organization-Id"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List readable credentials in the server-validated active organization."""

    organization_id = resolve_active_organization_id(
        db, request, x_organization_id, current_user.id
    )
    try:
        return LLMService.get_user_credentials(
            db, current_user.id, organization_id
        )
    except Exception as exc:
        _raise_internal_operation_error(
            "credential_lookup", exc, "LLM credential lookup failed."
        )


@router.get(
    "/agent-answer-options",
    response_model=List[LLMCredentialModelOptionResponse],
)
def get_agent_answer_options(
    request: Request,
    x_organization_id: str | None = Header(default=None, alias="X-Organization-Id"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    RAG Agent answer에서 사용할 수 있는 verified model/credential 조합을 조회합니다.
    """
    organization_id = resolve_active_organization_id(
        db, request, x_organization_id, current_user.id
    )
    try:
        return LLMService.get_agent_answer_options(
            db, current_user.id, organization_id
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Agent answer options lookup failed")
        raise HTTPException(
            status_code=500,
            detail="Agent answer options lookup failed.",
        ) from exc


@router.post("/credentials", response_model=LLMCredentialResponse)
@audit(AuditAction.CREDENTIAL_CREATE)
def register_credential(
    credential_request: LLMCredentialCreate,
    request: Request,
    x_organization_id: str | None = Header(default=None, alias="X-Organization-Id"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Register a provider credential in the active organization."""

    organization_id = resolve_active_organization_id(
        db, request, x_organization_id, current_user.id
    )
    if (
        credential_request.organization_id is not None
        and credential_request.organization_id != organization_id
    ):
        raise_api_error(
            request,
            404,
            "resource.not_found",
            "Organization not found.",
        )

    try:
        return LLMService.register_credential(
            db,
            current_user.id,
            credential_request,
            organization_id,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.error(
            "LLM credential registration failed: error_type=%s",
            type(exc).__name__,
        )
        raise HTTPException(
            status_code=500,
            detail="Credential registration failed",
        ) from None


@router.delete("/credentials/{credential_id}")
@audit(AuditAction.CREDENTIAL_DELETE, target_param="credential_id")
def delete_credential(
    credential_id: UUID,
    request: Request,
    x_organization_id: str | None = Header(default=None, alias="X-Organization-Id"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Revoke a credential in the active organization."""

    organization_id = resolve_active_organization_id(
        db, request, x_organization_id, current_user.id
    )
    try:
        ensure_llm_credential_permission(
            db,
            current_user,
            credential_id,
            "write",
            active_organization_id=organization_id,
        )
        deleted = LLMService.delete_credential(
            db, credential_id, current_user.id, organization_id
        )
        if not deleted:
            raise HTTPException(status_code=404, detail="Credential not found")
        return {"message": "Credential deleted", "id": str(credential_id)}
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(
            "LLM credential deletion failed: error_type=%s",
            type(exc).__name__,
        )
        raise HTTPException(
            status_code=500,
            detail="Credential deletion failed",
        ) from None


@router.post("/credentials/{credential_id}/sync-models")
def sync_credential_models(
    credential_id: UUID,
    request: Request,
    purge_unverified: bool = False,
    x_organization_id: str | None = Header(default=None, alias="X-Organization-Id"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """현재 active organization의 credential model 관계를 재동기화합니다."""

    organization_id = resolve_active_organization_id(
        db, request, x_organization_id, current_user.id
    )
    try:
        ensure_llm_credential_permission(
            db,
            current_user,
            credential_id,
            "write",
            active_organization_id=organization_id,
        )
        return LLMService.sync_credential_models(
            db,
            current_user.id,
            credential_id,
            organization_id,
            purge_unverified=purge_unverified,
        )
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.error(
            "LLM credential model sync failed: error_type=%s",
            type(exc).__name__,
        )
        raise HTTPException(
            status_code=500,
            detail="Credential model synchronization failed",
        ) from None


# --- Stats ---


@router.get("/stats/top-models")
def get_top_expensive_models(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Get Top 3 expensive models for the current month (user-scoped).
    각 사용자의 본인 사용량 기준으로 Top 3 모델을 반환합니다.
    """
    try:
        # 1. Determine date range (This Month in UTC mostly, or naive)
        now = datetime.now(timezone.utc)
        start_of_month = datetime(now.year, now.month, 1, tzinfo=timezone.utc)

        usage = provider_usage_model_subject_rows_subquery(
            user_id=current_user.id,
            start_at=start_of_month,
            end_at=now,
        )

        # 2. Query (User-scoped)
        # Join Log -> Model -> Provider
        # Group by Model, filtered by current user
        results = (
            db.query(
                LLMModel.name.label("model_name"),
                LLMProvider.name.label("provider_name"),
                func.sum(usage.c.total_cost).label("total_cost"),
                func.sum(usage.c.total_tokens).label("total_tokens"),
            )
            .join(LLMModel, usage.c.model_id == LLMModel.id)
            .join(LLMProvider, LLMModel.provider_id == LLMProvider.id)
            .group_by(LLMModel.id, LLMModel.name, LLMProvider.id, LLMProvider.name)
            .order_by(desc("total_cost"))
            .limit(3)
            .all()
        )

        # 3. Format Response
        response = []
        for r in results:
            response.append(
                {
                    "model_name": r.model_name,
                    "provider_name": r.provider_name,
                    "total_cost": float(r.total_cost) if r.total_cost else 0.0,
                    "total_tokens": int(r.total_tokens) if r.total_tokens else 0,
                }
            )

        return response

    except Exception as exc:
        _raise_internal_operation_error(
            "usage_statistics_lookup",
            exc,
            "LLM usage statistics lookup failed.",
        )


# --- Pricing Management ---


@router.post("/models/sync-pricing")
def sync_system_pricing(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    [Admin] Sync all DB models with hardcoded system prices.
    Useful when system price list is updated.
    """
    _require_system_admin(db, current_user)
    try:
        result = LLMService.sync_system_prices(db)
        return result
    except Exception as exc:
        _raise_internal_operation_error(
            "pricing_sync", exc, "LLM pricing synchronization failed."
        )


@router.put("/models/{model_id}/pricing")
@audit(AuditAction.MODEL_PRICING_UPDATE, target_param="model_id")
def update_model_pricing(
    model_id: UUID,
    pricing: LLMModelPricingUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    [Admin] Manually update pricing for a specific model.
    """
    _require_system_admin(db, current_user)
    try:
        model = LLMService.update_model_pricing(
            db, model_id, pricing.input_price_1k, pricing.output_price_1k
        )
        return {"message": "Pricing updated", "model": model.name}
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        _raise_internal_operation_error(
            "pricing_update", exc, "LLM pricing update failed."
        )
