"""Legacy credential-selection strategy behind the provider execution port."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from sqlalchemy.orm import Session

from apps.workflow_engine.adapters.provider_invocation import (
    ProviderClientInvocationLease,
)
from apps.workflow_engine.application.provider_execution import (
    LLMCredentialNotAvailableError,
    ProviderExecutionAttribution,
    ProviderExecutionConfigurationError,
    ProviderExecutionPlan,
    ProviderExecutionPreflight,
    ProviderExecutionRequest,
)
from apps.workflow_engine.services.llm_service import (
    LLMRuntimeSelection,
    LLMService,
)


@dataclass(frozen=True, slots=True)
class _LegacyPlanState:
    credential_principal_user_id: uuid.UUID | None
    organization_id: uuid.UUID | None
    client_override: Any | None


class LegacyProviderExecutionAdapter:
    """Resolve the current user-scoped runtime client without capability policy."""

    def __init__(
        self,
        *,
        session_factory: Callable[[], Session],
        legacy_resolver: Callable[..., LLMRuntimeSelection] | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._legacy_resolver = (
            legacy_resolver or LLMService.get_runtime_client_for_user
        )

    def preflight(
        self,
        request: ProviderExecutionPreflight,
    ) -> ProviderExecutionPlan:
        capability_required = request.execution_context.get(
            "provider_execution_capability_required",
            False,
        )
        if capability_required is not False:
            raise ProviderExecutionConfigurationError()
        if request.client_override is not None:
            state = _LegacyPlanState(None, None, request.client_override)
        else:
            user_id = self._legacy_principal(request.execution_context)
            if user_id is None:
                raise ValueError(
                    "LLM 노드 실행에는 유효한 credential principal이 필요합니다."
                )
            organization_id = self._required_organization_id(
                request.execution_context,
                model_id=request.configured_model_id,
            )
            state = _LegacyPlanState(user_id, organization_id, None)
        return ProviderExecutionPlan(
            fixed_model_id=None,
            allow_legacy_memory_summary=True,
            state=state,
        )

    def resolve(
        self,
        request: ProviderExecutionRequest,
    ) -> ProviderClientInvocationLease:
        state = request.plan.state
        if not isinstance(state, _LegacyPlanState):
            raise ProviderExecutionConfigurationError()
        if state.client_override is not None:
            return ProviderClientInvocationLease(
                client=state.client_override,
                messages=request.messages,
                parameters=request.parameters,
                attribution=None,
            )
        if state.credential_principal_user_id is None or state.organization_id is None:
            raise ProviderExecutionConfigurationError()

        db = self._new_isolated_session(request.shared_session)
        try:
            selection = self._legacy_resolver(
                db,
                user_id=state.credential_principal_user_id,
                model_id=request.model_id,
                organization_id=state.organization_id,
            )
        finally:
            db.close()
        attribution = self._selection_attribution(
            selection,
            expected_model_id=request.model_id,
            expected_organization_id=state.organization_id,
            expected_credential_principal_user_id=(state.credential_principal_user_id),
        )
        return ProviderClientInvocationLease(
            client=selection.client,
            messages=request.messages,
            parameters=request.parameters,
            attribution=attribution,
        )

    def _new_isolated_session(self, shared_session: Any | None) -> Session:
        try:
            db = self._session_factory()
        except Exception as exc:
            raise ProviderExecutionConfigurationError() from exc
        if db is None or db is shared_session:
            raise ProviderExecutionConfigurationError()
        return db

    @staticmethod
    def _selection_attribution(
        selection: LLMRuntimeSelection,
        *,
        expected_model_id: str,
        expected_organization_id: uuid.UUID,
        expected_credential_principal_user_id: uuid.UUID,
    ) -> ProviderExecutionAttribution:
        try:
            credential_id = uuid.UUID(str(selection.credential_id))
            organization_id = uuid.UUID(str(selection.organization_id))
            raw_principal_id = getattr(
                selection,
                "credential_principal_user_id",
                None,
            )
            principal_id = (
                uuid.UUID(str(raw_principal_id))
                if raw_principal_id is not None
                else expected_credential_principal_user_id
            )
            raw_model_db_id = getattr(selection, "model_db_id", None)
            model_db_id = (
                uuid.UUID(str(raw_model_db_id)) if raw_model_db_id is not None else None
            )
        except (AttributeError, TypeError, ValueError) as exc:
            raise ProviderExecutionConfigurationError() from exc
        if (
            selection.model_id != expected_model_id
            or organization_id != expected_organization_id
            or principal_id != expected_credential_principal_user_id
        ):
            raise ProviderExecutionConfigurationError()
        return ProviderExecutionAttribution(
            credential_id=credential_id,
            credential_principal_user_id=principal_id,
            organization_id=expected_organization_id,
            model_id=expected_model_id,
            model_db_id=model_db_id,
        )

    @staticmethod
    def _legacy_principal(context: Mapping[str, Any]) -> uuid.UUID | None:
        principal = context.get("credential_principal")
        if principal is not None:
            if not isinstance(principal, Mapping):
                raise PermissionError("LLM credential principal is invalid.")
            principal_type = principal.get("subject_type") or principal.get("type")
            principal_id = principal.get("subject_id") or principal.get("id")
            if principal_type != "user":
                raise PermissionError("LLM credential principal type is not supported.")
            try:
                return uuid.UUID(str(principal_id))
            except (TypeError, ValueError) as exc:
                raise PermissionError("LLM credential principal is invalid.") from exc
        value = context.get("user_id")
        if not value:
            return None
        try:
            return uuid.UUID(str(value))
        except (TypeError, ValueError) as exc:
            raise PermissionError("LLM credential principal is invalid.") from exc

    @staticmethod
    def _required_organization_id(
        context: Mapping[str, Any],
        *,
        model_id: str,
    ) -> uuid.UUID:
        try:
            return uuid.UUID(str(context["organization_id"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise LLMCredentialNotAvailableError(
                "organization_scope_missing",
                "Workflow LLM runtime requires a valid organization_id.",
                model_id=model_id,
            ) from exc


__all__ = ["LegacyProviderExecutionAdapter"]
