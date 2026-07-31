"""Production composition for the Slack external-effect adapter."""

from apps.shared.services.outbound_operation_http import OperationHttpRequester
from apps.workflow_engine.adapters.providers.slack import (
    SlackDeliveryMode,
    SlackDeliveryPolicy,
    SlackEffectAdapter,
)


def build_slack_effect_adapter(mode: SlackDeliveryMode) -> SlackEffectAdapter:
    policy = SlackDeliveryPolicy()
    return SlackEffectAdapter(
        mode,
        policy=policy,
        requester=OperationHttpRequester(),
    )
