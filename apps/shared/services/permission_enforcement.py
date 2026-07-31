from uuid import UUID

from apps.shared.schemas.permission import (
    KNOWLEDGE_AUTH_STATE_RANK,
    LLM_AUTH_STATE_RANK,
    MAIL_AUTH_STATE_RANK,
    WORKFLOW_AUTH_STATE_RANK,
)
from apps.shared.services.permissions import (
    get_effective_knowledge_base_auth_state,
    get_effective_llm_credential_auth_state,
    get_effective_mail_credential_auth_state,
    get_effective_workflow_auth_state,
)
from sqlalchemy.orm import Session


class PermissionEnforcementService:
    """Reusable RBAC enforcement helpers for API and runtime code."""

    @staticmethod
    def normalize_workflow_auth_state(auth_state: str | None) -> str:
        value = str(auth_state or "none").lower()
        if value not in WORKFLOW_AUTH_STATE_RANK:
            return "none"
        return value

    @staticmethod
    def normalize_llm_auth_state(auth_state: str | None) -> str:
        value = str(auth_state or "none").lower()
        if value not in LLM_AUTH_STATE_RANK:
            return "none"
        return value

    @staticmethod
    def normalize_knowledge_auth_state(auth_state: str | None) -> str:
        value = str(auth_state or "none").lower()
        if value not in KNOWLEDGE_AUTH_STATE_RANK:
            return "none"
        return value

    @staticmethod
    def normalize_mail_auth_state(auth_state: str | None) -> str:
        value = str(auth_state or "none").lower()
        if value not in MAIL_AUTH_STATE_RANK:
            return "none"
        return value

    @classmethod
    def get_workflow_auth_state(
        cls,
        db: Session,
        organization_id: UUID,
        workflow_id: UUID,
        user_id: UUID,
    ) -> str:
        return get_effective_workflow_auth_state(
            db,
            user_id,
            workflow_id,
            organization_id=organization_id,
        )

    @classmethod
    def get_llm_credential_auth_state(
        cls,
        db: Session,
        organization_id: UUID,
        credential_id: UUID,
        user_id: UUID,
    ) -> str:
        return get_effective_llm_credential_auth_state(
            db,
            user_id,
            credential_id,
            organization_id=organization_id,
        )

    @classmethod
    def get_knowledge_base_auth_state(
        cls,
        db: Session,
        organization_id: UUID,
        knowledge_base_id: UUID,
        user_id: UUID,
    ) -> str:
        return get_effective_knowledge_base_auth_state(
            db,
            user_id,
            knowledge_base_id,
            organization_id=organization_id,
        )

    @classmethod
    def get_mail_credential_auth_state(
        cls,
        db: Session,
        organization_id: UUID,
        credential_id: UUID,
        user_id: UUID,
    ) -> str:
        return get_effective_mail_credential_auth_state(
            db,
            user_id,
            credential_id,
            organization_id=organization_id,
        )

    @classmethod
    def has_workflow_manage_permission(
        cls,
        db: Session,
        organization_id: UUID,
        workflow_id: UUID,
        user_id: UUID,
    ) -> bool:
        auth_state = cls.get_workflow_auth_state(
            db,
            organization_id,
            workflow_id,
            user_id,
        )
        return (
            WORKFLOW_AUTH_STATE_RANK[auth_state] >= WORKFLOW_AUTH_STATE_RANK["manager"]
        )

    @classmethod
    def has_llm_credential_manage_permission(
        cls,
        db: Session,
        organization_id: UUID,
        credential_id: UUID,
        user_id: UUID,
    ) -> bool:
        auth_state = cls.get_llm_credential_auth_state(
            db,
            organization_id,
            credential_id,
            user_id,
        )
        return LLM_AUTH_STATE_RANK[auth_state] >= LLM_AUTH_STATE_RANK["manager"]

    @classmethod
    def has_knowledge_base_manage_permission(
        cls,
        db: Session,
        organization_id: UUID,
        knowledge_base_id: UUID,
        user_id: UUID,
    ) -> bool:
        auth_state = cls.get_knowledge_base_auth_state(
            db,
            organization_id,
            knowledge_base_id,
            user_id,
        )
        return (
            KNOWLEDGE_AUTH_STATE_RANK[auth_state]
            >= KNOWLEDGE_AUTH_STATE_RANK["manager"]
        )

    @classmethod
    def has_mail_credential_manage_permission(
        cls,
        db: Session,
        organization_id: UUID,
        credential_id: UUID,
        user_id: UUID,
    ) -> bool:
        auth_state = cls.get_mail_credential_auth_state(
            db, organization_id, credential_id, user_id
        )
        return MAIL_AUTH_STATE_RANK[auth_state] >= MAIL_AUTH_STATE_RANK["manager"]
