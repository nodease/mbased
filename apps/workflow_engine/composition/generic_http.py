from __future__ import annotations

from apps.shared.services.egress_guard import OutboundEgressGuard
from apps.workflow_engine.adapters.outbound_http import (
    GuardedHttpxOutboundAdapter,
    generic_http_egress_policy,
)
from apps.workflow_engine.adapters.providers.generic_http import GenericHttpEffectAdapter
from apps.workflow_engine.domain.external_effect import ProviderContractRegistry


def build_generic_http_effect_adapter(
    *,
    slack_mode: bool = False,
    contracts: ProviderContractRegistry | None = None,
) -> GenericHttpEffectAdapter:
    guard = OutboundEgressGuard(generic_http_egress_policy())
    return GenericHttpEffectAdapter(
        slack_mode=slack_mode,
        outbound_http=GuardedHttpxOutboundAdapter(guard=guard),
        contracts=contracts,
    )
