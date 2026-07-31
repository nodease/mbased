from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from fastapi import Request
from sqlalchemy.orm import Session

from apps.gateway.services.agent_builder.intent_usage_service import (
    AgentBuilderIntentUsageService,
)
from apps.gateway.services.agent_builder.knowledge_selection_service import (
    KnowledgeSelectionService,
)
from apps.gateway.services.agent_builder.mutation_lifecycle import (
    GraphMutationLifecycleService,
)
from apps.gateway.services.agent_builder.parameter_task_service import (
    ParameterTaskService,
)
from apps.gateway.services.agent_builder_intent_service import (
    LLMAgentBuilderIntentExtractor,
)
from apps.gateway.services.agent_builder_service import (
    NO_KB_CANDIDATE_ID,
    AgentBuilderService,
)
from apps.gateway.services.llm_service import LLMService
from apps.gateway.services.organization_context import resolve_active_organization_id
from apps.shared.db.models.user import User


@dataclass(frozen=True)
class AgentBuilderComposition:
    db: Session
    user: User
    organization_id: UUID

    def orchestration(
        self,
        *,
        intent_credential_id: UUID | None = None,
        intent_model_id: UUID | None = None,
    ) -> AgentBuilderService:
        return AgentBuilderService(
            self.db,
            user=self.user,
            organization_id=self.organization_id,
            intent_extractor=LLMAgentBuilderIntentExtractor(
                db=self.db,
                user_id=self.user.id,
                organization_id=self.organization_id,
                credential_id=intent_credential_id,
                model_id=intent_model_id,
                usage_recorder=AgentBuilderIntentUsageService(),
            ),
        )

    def mutation_lifecycle(self) -> GraphMutationLifecycleService:
        return GraphMutationLifecycleService(
            self.db,
            user_id=self.user.id,
            organization_id=self.organization_id,
        )

    def parameter_tasks(self) -> ParameterTaskService:
        return ParameterTaskService(
            self.db,
            user_id=self.user.id,
            organization_id=self.organization_id,
        )

    def knowledge_selection(self) -> KnowledgeSelectionService:
        bridge = AgentBuilderService(
            self.db,
            user=self.user,
            organization_id=self.organization_id,
        )
        return KnowledgeSelectionService(
            self.db,
            user_id=self.user.id,
            organization_id=self.organization_id,
            binding_materializer=bridge.materialize_knowledge_selection,
            knowledge_selection_refresher=(
                bridge.refresh_knowledge_selection_candidates
            ),
            before_graph_builder=bridge.build_before_graph_knowledge_selection,
            no_knowledge_candidate_id=NO_KB_CANDIDATE_ID,
        )

    def model_options(self):
        return LLMService.get_agent_builder_model_option_groups(
            self.db,
            self.user.id,
            self.organization_id,
        )


def compose_agent_builder(
    *,
    db: Session,
    request: Request,
    raw_organization_id: str | None,
    current_user: User,
) -> AgentBuilderComposition:
    organization_id = resolve_active_organization_id(
        db,
        request,
        raw_organization_id,
        current_user.id,
    )
    return AgentBuilderComposition(
        db=db,
        user=current_user,
        organization_id=organization_id,
    )
