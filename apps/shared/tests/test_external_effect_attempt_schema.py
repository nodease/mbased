import pytest
from apps.shared.alembic.external_effect_downgrade import (
    assert_external_effect_downgrade_is_safe,
)
from apps.shared.db.models.workflow_node_effect_attempt import (
    WorkflowNodeEffectAttempt,
)
from sqlalchemy import CheckConstraint, UniqueConstraint


def test_external_effect_attempt_model_has_required_contract_shape() -> None:
    table = WorkflowNodeEffectAttempt.__table__
    columns = set(table.columns.keys())

    assert {
        "organization_id",
        "app_id",
        "workflow_id",
        "execution_id",
        "node_invocation_id",
        "node_id",
        "operation",
        "effect_sequence",
        "provider",
        "provider_contract_version",
        "provider_replay_capability",
        "result_reuse_capability",
        "effect_input_digest",
        "replay_deadline_at",
        "status",
        "outcome",
        "replay_decision",
        "claim_owner",
        "claim_expires_at",
        "claim_generation",
        "key_version",
        "key_format_version",
        "idempotency_key_fingerprint",
        "replay_result",
        "provider_status_code",
        "error_code",
        "provider_started_at",
        "terminal_at",
    } <= columns

    constraint_names = {
        constraint.name
        for constraint in table.constraints
        if isinstance(constraint, (CheckConstraint, UniqueConstraint))
    }
    assert "uq_workflow_node_effect_attempts_slot" in constraint_names
    assert "ck_workflow_node_effect_attempts_status_shape" in constraint_names
    assert "ck_workflow_node_effect_attempts_outcome_decision" in constraint_names
    assert "ck_workflow_node_effect_attempts_key_contract" in constraint_names
    assert "ck_workflow_node_effect_attempts_error_code" in constraint_names

    indexes = {index.name: index for index in table.indexes}
    assert "ix_workflow_node_effect_attempts_contract" in indexes
    assert "ix_workflow_node_effect_attempts_hmac_readiness" in indexes
    assert indexes["ix_workflow_node_effect_attempts_hmac_readiness"].dialect_options[
        "postgresql"
    ]["where"] is not None


class _DowngradeResult:
    def __init__(self, value: bool) -> None:
        self.value = value

    def scalar_one(self) -> bool:
        return self.value


class _DowngradeConnection:
    def __init__(self, has_attempts: bool) -> None:
        self.has_attempts = has_attempts
        self.statements = []

    def execute(self, statement):
        self.statements.append(str(statement))
        return _DowngradeResult(self.has_attempts)


def test_external_effect_downgrade_refuses_to_drop_attempts() -> None:
    empty = _DowngradeConnection(False)
    assert_external_effect_downgrade_is_safe(empty)
    assert empty.statements[0] == (
        "LOCK TABLE workflow_node_effect_attempts IN ACCESS EXCLUSIVE MODE"
    )
    assert "SELECT EXISTS" in empty.statements[1]

    with pytest.raises(RuntimeError, match="attempts exist"):
        assert_external_effect_downgrade_is_safe(_DowngradeConnection(True))
