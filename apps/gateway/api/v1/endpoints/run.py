from typing import Annotated, NoReturn

from fastapi import (
    APIRouter,
    Body,
    Depends,
    Header,
    HTTPException,
    Request,
    status,
)
from sqlalchemy.orm import Session

from apps.gateway.api.deps import get_deployment_runtime_policy
from apps.gateway.core.config import settings
from apps.shared.db.session import get_db
from apps.gateway.services.deployment_service import DeploymentService
from apps.gateway.middleware.public_conversation_cors import (
    mark_public_conversation_transport_boundary,
)
from apps.shared.domain.deployment_runtime_policy import DeploymentRuntimePolicy
from apps.shared.domain.public_chat_history import (
    PublicChatHistoryError,
    bound_public_chat_history,
)

router = APIRouter()


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
    db: Session = Depends(get_db),
):
    """
    배포된 워크플로우를 URL Slug로 실행합니다 (웹 앱/임베딩: 공개 접근).
    - url_slug: workflow_deployments 생성시 만들어진 고유 주소

    """
    user_inputs = request_body.get("inputs", {})
    # 웹 앱/임베딩: 공개 접근 (인증 불필요)
    try:
        expected_deployment_version = _optional_public_deployment_version(
            request_body
        )
        return await DeploymentService.run_deployment(
            db=db,
            url_slug=url_slug,
            user_inputs=user_inputs,
            client_conversation_history=None,
            allow_stateless_public_chatbot_compatibility=(
                settings.PUBLIC_CHAT_CONVERSATION_ROLLOUT_MODE == "compatibility"
            ),
            expected_deployment_version=expected_deployment_version,
            auth_token=None,
            require_auth=False,  # 인증 불필요
            trigger_mode="app",  # 웹 앱/임베딩 호출
            runtime_policy=runtime_policy,
        )
    except HTTPException as error:
        detail = error.detail
        if isinstance(detail, dict) and str(detail.get("code", "")).startswith(
            "conversation."
        ):
            mark_public_conversation_transport_boundary(request.scope)
        raise


@router.post("/run-public/{url_slug}/chat")
async def run_public_chatbot(
    url_slug: str,
    runtime_policy: Annotated[
        DeploymentRuntimePolicy,
        Depends(get_deployment_runtime_policy),
    ],
    request_body: dict = Body(...),
    db: Session = Depends(get_db),
):
    """Run a public Chatbot with bounded client-held conversation history."""
    user_inputs = request_body.get("inputs", {})
    if "conversation" not in request_body:
        _raise_public_chat_history_error("conversation.history_required")

    conversation = request_body.get("conversation")
    try:
        if not isinstance(conversation, dict) or set(conversation) != {"history"}:
            raise PublicChatHistoryError("conversation.envelope_invalid")
        client_conversation_history = bound_public_chat_history(
            conversation["history"],
            current_inputs=user_inputs,
        )
    except PublicChatHistoryError as error:
        _raise_public_chat_history_error(error.code)

    expected_deployment_version = _required_public_deployment_version(request_body)
    return await DeploymentService.run_deployment(
        db=db,
        url_slug=url_slug,
        user_inputs=user_inputs,
        client_conversation_history=client_conversation_history,
        expected_deployment_version=expected_deployment_version,
        auth_token=None,
        require_auth=False,
        trigger_mode="app",
        runtime_policy=runtime_policy,
    )


def _optional_public_deployment_version(request_body: dict) -> int | None:
    if "deployment_version" not in request_body:
        return None
    version = request_body["deployment_version"]
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        _raise_public_chat_history_error(
            "conversation.deployment_version_invalid"
        )
    return version


def _required_public_deployment_version(request_body: dict) -> int:
    version = _optional_public_deployment_version(request_body)
    if version is None:
        _raise_public_chat_history_error(
            "conversation.deployment_version_invalid"
        )
    return version


def _raise_public_chat_history_error(code: str) -> NoReturn:
    raise HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail={
            "code": code,
            "message": "The public conversation history is invalid.",
        },
    ) from None
