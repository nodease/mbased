import asyncio
from datetime import datetime, timezone
from typing import Annotated

from fastapi import (
    APIRouter,
    Body,
    Depends,
    Header,
    Request,
    Response,
    status,
)
from sqlalchemy.orm import Session
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from apps.gateway.api.deps import get_deployment_runtime_policy
from apps.shared.db.session import get_db
from apps.gateway.services.deployment_service import DeploymentService
from apps.gateway.composition.memory import (
    public_conversation_runtime_required,
    start_public_conversation_turn,
)
from apps.gateway.api.v1.endpoints.public_conversation import (
    _conversation_token,
    _idempotency_key_hash,
    _map_public_error,
    _network_address,
    _safe_error,
    _set_public_headers,
)
from apps.memory.application.public_runtime import (
    StartPublicConversationTurnCommand,
)
from apps.gateway.middleware.public_conversation_cors import (
    mark_public_conversation_transport_boundary,
)
from apps.shared.domain.deployment_runtime_policy import DeploymentRuntimePolicy

router = APIRouter()


_public_conversation_runtime_required = public_conversation_runtime_required
_start_public_conversation_turn = start_public_conversation_turn


class _PublicConversationRunEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_lifecycle_revision: int = Field(ge=1)


class _PublicConversationRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    inputs: dict[str, object]
    conversation: _PublicConversationRunEnvelope


@router.post("/run/{url_slug}")
async def run_workflow(
    url_slug: str,
    runtime_policy: Annotated[
        DeploymentRuntimePolicy,
        Depends(get_deployment_runtime_policy),
    ],
    request_body: dict = Body(...),
    authorization: list[str] | None = Header(None),
    db: Session = Depends(get_db),
):
    """
    배포된 워크플로우를 URL Slug로 실행합니다 (REST API: 인증 필요).
    - url_slug: workflow_deployments 생성시 만들어진 고유 주소
    """
    # 인증 토큰 추출

    auth_token = None
    if authorization and len(authorization) == 1 and "," not in authorization[0]:
        scheme, separator, candidate = authorization[0].partition(" ")
        if separator and scheme.lower() == "bearer" and candidate:
            auth_token = candidate

    # REST API: 인증 필요
    return await DeploymentService.run_deployment(
        db=db,
        url_slug=url_slug,
        user_inputs=request_body.get("inputs", {}),
        auth_token=auth_token,
        require_auth=True,  # 인증 필수
        trigger_mode="api",  # REST API 호출
        runtime_policy=runtime_policy,
    )


@router.post("/run-public/{url_slug}")
async def run_workflow_public(
    url_slug: str,
    request: Request,
    runtime_policy: Annotated[
        DeploymentRuntimePolicy,
        Depends(get_deployment_runtime_policy),
    ],
    request_body: dict = Body(...),
    authorization: Annotated[str | None, Header()] = None,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    response: Response = None,
    db: Session = Depends(get_db),
):
    """
    배포된 워크플로우를 URL Slug로 실행합니다 (웹 앱/임베딩: 공개 접근).
    - url_slug: workflow_deployments 생성시 만들어진 고유 주소

    """
    if "conversation" in request_body:
        mark_public_conversation_transport_boundary(request.scope)
        try:
            body = _PublicConversationRunRequest.model_validate(request_body)
        except ValidationError:
            raise _safe_error(
                "memory.input_mapping_invalid",
                "The conversation run envelope is invalid.",
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            ) from None
        try:
            result = await asyncio.to_thread(
                _start_public_conversation_turn,
                StartPublicConversationTurnCommand(
                    url_slug=url_slug,
                    access_token=_conversation_token(authorization),
                    idempotency_key_hash=_idempotency_key_hash(idempotency_key),
                    expected_lifecycle_revision=(
                        body.conversation.expected_lifecycle_revision
                    ),
                    inputs=body.inputs,
                    network_address=_network_address(request),
                    now=datetime.now(timezone.utc),
                )
            )
        except ValueError as error:
            if str(error) == "memory.input_mapping_invalid":
                raise _safe_error(
                    "memory.input_mapping_invalid",
                    "The mapped conversation input is invalid.",
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                ) from None
            raise
        except Exception as error:
            raise _map_public_error(error) from None
        response.status_code = status.HTTP_202_ACCEPTED
        _set_public_headers(
            response,
            lifecycle_revision=result.lifecycle_revision,
        )
        turn_id = str(result.turn_id)
        return {
            "status": "accepted",
            "conversation": {
                "turn_id": turn_id,
                "turn_state": result.turn_state.value,
                "turn_sequence": result.turn_sequence,
                "lifecycle_revision": result.lifecycle_revision,
                "status_path": (
                    f"/api/v1/run-public/{url_slug}/conversation/turns/{turn_id}"
                ),
            },
        }
    if await asyncio.to_thread(_public_conversation_runtime_required, url_slug):
        mark_public_conversation_transport_boundary(request.scope)
        raise _safe_error(
            "memory.conversation_required",
            "A conversation envelope is required for this deployment.",
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        )
    # 웹 앱/임베딩: 공개 접근 (인증 불필요)
    return await DeploymentService.run_deployment(
        db=db,
        url_slug=url_slug,
        user_inputs=request_body.get("inputs", {}),
        auth_token=None,
        require_auth=False,  # 인증 불필요
        trigger_mode="app",  # 웹 앱/임베딩 호출
        runtime_policy=runtime_policy,
    )
