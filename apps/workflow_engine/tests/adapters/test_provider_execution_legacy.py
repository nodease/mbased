from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from apps.workflow_engine.adapters.provider_execution_legacy import (
    LegacyProviderExecutionAdapter,
)
from apps.workflow_engine.application.provider_execution import (
    ProviderExecutionConfigurationError,
    ProviderExecutionPreflight,
    ProviderExecutionRequest,
)


class _Session:
    def __init__(self) -> None:
        self.closes = 0

    def close(self) -> None:
        self.closes += 1


def _plan(runtime, *, organization_id: uuid.UUID, user_id: uuid.UUID):
    return runtime.preflight(
        ProviderExecutionPreflight(
            node_id="llm-1",
            configured_model_id="gpt-safe",
            auto_model_routing=False,
            fallback_model_id=None,
            knowledge_enabled=False,
            memory_summary_requested=False,
            client_override=None,
            execution_context={
                "organization_id": str(organization_id),
                "user_id": str(user_id),
            },
            runtime_control=None,
        )
    )


@pytest.mark.parametrize("mismatch", ["organization", "model", "principal"])
def test_legacy_adapter_rejects_resolver_attribution_outside_request_scope(mismatch):
    organization_id = uuid.uuid4()
    user_id = uuid.uuid4()
    session = _Session()

    def resolver(_db, *, user_id, model_id, organization_id):
        return SimpleNamespace(
            client=object(),
            credential_id=uuid.uuid4(),
            model_id="other-model" if mismatch == "model" else model_id,
            organization_id=(
                uuid.uuid4() if mismatch == "organization" else organization_id
            ),
            credential_principal_user_id=(
                uuid.uuid4() if mismatch == "principal" else user_id
            ),
            model_db_id=uuid.uuid4(),
        )

    runtime = LegacyProviderExecutionAdapter(
        session_factory=lambda: session,
        legacy_resolver=resolver,
    )
    plan = _plan(runtime, organization_id=organization_id, user_id=user_id)

    with pytest.raises(ProviderExecutionConfigurationError):
        runtime.resolve(
            ProviderExecutionRequest(
                plan=plan,
                model_id="gpt-safe",
                messages=(),
                parameters={},
            )
        )

    assert session.closes == 1
