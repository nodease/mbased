from __future__ import annotations

import uuid
from collections.abc import Callable

from sqlalchemy.orm import Session

from apps.gateway.application.deployment.browser_access_errors import (
    BrowserAccessConversationContractError,
)
from apps.gateway.application.deployment.browser_access_models import (
    BrowserAccessSourceSnapshot,
)
from apps.gateway.application.deployment.preflight import DeploymentPreflightUseCase
from apps.gateway.services.workflow_service import WorkflowService
from apps.shared.domain.public_chat_conversation import (
    PublicChatConversationContractError,
    resolve_public_chat_conversation_contract,
)

BrowserAccessPreflightFactory = Callable[
    [BrowserAccessSourceSnapshot, uuid.UUID],
    DeploymentPreflightUseCase,
]


class DeploymentBrowserAccessActivationGuard:
    def __init__(
        self,
        db: Session,
        *,
        preflight_factory: BrowserAccessPreflightFactory,
        require_public_chat_conversation_contract: bool = False,
    ) -> None:
        self.db = db
        self.preflight_factory = preflight_factory
        self.require_public_chat_conversation_contract = (
            require_public_chat_conversation_contract
        )

    def enforce(
        self,
        source: BrowserAccessSourceSnapshot,
        *,
        actor_id: uuid.UUID,
    ) -> None:
        if (
            self.require_public_chat_conversation_contract
            and source.deployment_type == "chatbot"
        ):
            try:
                resolve_public_chat_conversation_contract(
                    source.config,
                    source.graph_snapshot,
                    required=True,
                )
            except PublicChatConversationContractError as exc:
                raise BrowserAccessConversationContractError(exc.code) from None
        WorkflowService.validate_mail_credential_references(
            self.db,
            source.graph_snapshot,
            user_id=str(actor_id),
            organization_id=source.organization_id,
            require_resolved=True,
        )
        self.preflight_factory(source, actor_id).enforce_active_publish(
            deployment_type=source.deployment_type,
            graph_snapshot=source.graph_snapshot,
        )
