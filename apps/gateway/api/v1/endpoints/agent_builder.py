from uuid import UUID

from fastapi import APIRouter, Depends, Header, Request
from sqlalchemy.orm import Session

from apps.gateway.auth.dependencies import get_current_user
from apps.gateway.composition.agent_builder import (
    AgentBuilderComposition,
    compose_agent_builder,
)
from apps.shared.db.models.user import User
from apps.shared.db.session import get_db
from apps.shared.schemas.agent_builder import (
    AgentBuilderMessageRequest,
    AgentBuilderDirectMessageResponse,
    AgentBuilderDirectSessionResponse,
    AgentBuilderSessionCreateRequest,
    GraphMutationAcknowledgementRequest,
    GraphMutationAcknowledgementResponse,
    AgentBuilderParameterTaskDecisionRequest,
    AgentBuilderParameterTaskDecisionResponse,
    AgentBuilderParameterGroupCancelRequest,
    AgentBuilderParameterGroupCancelResponse,
    AgentBuilderKnowledgeSelectionRequest,
    AgentBuilderKnowledgeSelectionResponse,
)
from apps.shared.schemas.llm import LLMIntentModelProviderResponse


router = APIRouter()


def _composition(
    *,
    db: Session,
    request: Request,
    raw_organization_id: str | None,
    current_user: User,
) -> AgentBuilderComposition:
    return compose_agent_builder(
        db=db,
        request=request,
        raw_organization_id=raw_organization_id,
        current_user=current_user,
    )


@router.get(
    "/model-options",
    response_model=list[LLMIntentModelProviderResponse],
)
def get_agent_builder_model_options(
    request: Request,
    x_organization_id: str | None = Header(default=None, alias="X-Organization-Id"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return _composition(
        db=db,
        request=request,
        raw_organization_id=x_organization_id,
        current_user=current_user,
    ).model_options()


@router.post("/sessions", response_model=AgentBuilderDirectSessionResponse)
def create_or_restore_session(
    payload: AgentBuilderSessionCreateRequest,
    request: Request,
    x_organization_id: str | None = Header(default=None, alias="X-Organization-Id"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return _composition(
        db=db,
        request=request,
        raw_organization_id=x_organization_id,
        current_user=current_user,
    ).orchestration().create_or_restore_session(payload)


@router.get("/sessions/{session_id}", response_model=AgentBuilderDirectSessionResponse)
def get_session(
    session_id: UUID,
    request: Request,
    x_organization_id: str | None = Header(default=None, alias="X-Organization-Id"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return _composition(
        db=db,
        request=request,
        raw_organization_id=x_organization_id,
        current_user=current_user,
    ).orchestration().get_session(session_id)


@router.post(
    "/sessions/{session_id}/messages",
    response_model=AgentBuilderDirectMessageResponse,
)
def submit_message(
    session_id: UUID,
    payload: AgentBuilderMessageRequest,
    request: Request,
    x_organization_id: str | None = Header(default=None, alias="X-Organization-Id"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    model_selection = payload.intent_model_selection
    response = _composition(
        db=db,
        request=request,
        raw_organization_id=x_organization_id,
        current_user=current_user,
    ).orchestration(
        intent_credential_id=(
            model_selection.credential_id if model_selection else None
        ),
        intent_model_id=model_selection.model_id if model_selection else None,
    ).submit_message(session_id, payload)
    return AgentBuilderDirectMessageResponse.from_internal(response)


@router.post(
    "/requests/{request_id}/cancel",
    response_model=AgentBuilderDirectMessageResponse,
)
def cancel_request(
    request_id: UUID,
    request: Request,
    x_organization_id: str | None = Header(default=None, alias="X-Organization-Id"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    response = _composition(
        db=db,
        request=request,
        raw_organization_id=x_organization_id,
        current_user=current_user,
    ).orchestration().cancel_request(request_id)
    return AgentBuilderDirectMessageResponse.from_internal(response)


@router.post(
    "/sessions/{session_id}/graph-mutations/{operation_id}/ack",
    response_model=GraphMutationAcknowledgementResponse,
)
def acknowledge_graph_mutation(
    session_id: UUID,
    operation_id: UUID,
    payload: GraphMutationAcknowledgementRequest,
    request: Request,
    x_organization_id: str | None = Header(default=None, alias="X-Organization-Id"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return _composition(
        db=db,
        request=request,
        raw_organization_id=x_organization_id,
        current_user=current_user,
    ).mutation_lifecycle().acknowledge(session_id, operation_id, payload)


@router.patch(
    "/sessions/{session_id}/parameter-tasks/{task_id}",
    response_model=AgentBuilderParameterTaskDecisionResponse,
)
def decide_parameter_task(
    session_id: UUID,
    task_id: UUID,
    payload: AgentBuilderParameterTaskDecisionRequest,
    request: Request,
    x_organization_id: str | None = Header(default=None, alias="X-Organization-Id"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return _composition(
        db=db,
        request=request,
        raw_organization_id=x_organization_id,
        current_user=current_user,
    ).parameter_tasks().decide(session_id, task_id, payload)


@router.post(
    "/sessions/{session_id}/parameter-groups/{group_id}/cancel",
    response_model=AgentBuilderParameterGroupCancelResponse,
)
def cancel_parameter_group(
    session_id: UUID,
    group_id: UUID,
    payload: AgentBuilderParameterGroupCancelRequest,
    request: Request,
    x_organization_id: str | None = Header(default=None, alias="X-Organization-Id"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return _composition(
        db=db,
        request=request,
        raw_organization_id=x_organization_id,
        current_user=current_user,
    ).parameter_tasks().cancel_group(session_id, group_id, payload)


@router.post(
    "/sessions/{session_id}/knowledge-selection",
    response_model=AgentBuilderKnowledgeSelectionResponse,
)
def select_knowledge(
    session_id: UUID,
    payload: AgentBuilderKnowledgeSelectionRequest,
    request: Request,
    x_organization_id: str | None = Header(default=None, alias="X-Organization-Id"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return _composition(
        db=db,
        request=request,
        raw_organization_id=x_organization_id,
        current_user=current_user,
    ).knowledge_selection().select(session_id, payload)
