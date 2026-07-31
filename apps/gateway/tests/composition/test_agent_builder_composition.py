import uuid
from types import SimpleNamespace

from apps.gateway.composition.agent_builder import AgentBuilderComposition
from apps.gateway.services.agent_builder.intent_usage_service import (
    AgentBuilderIntentUsageService,
)


def test_agent_builder_composition_wires_durable_intent_usage_recorder():
    composition = AgentBuilderComposition(
        db=SimpleNamespace(),
        user=SimpleNamespace(id=uuid.uuid4()),
        organization_id=uuid.uuid4(),
    )

    service = composition.orchestration(
        intent_credential_id=uuid.uuid4(),
        intent_model_id=uuid.uuid4(),
    )

    assert isinstance(
        service.intent_extractor.usage_recorder,
        AgentBuilderIntentUsageService,
    )
