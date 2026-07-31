from unittest.mock import Mock

from apps.workflow_engine.workflow.nodes.code.code_node import CodeNode
from apps.workflow_engine.workflow.nodes.code.entities import CodeNodeData


def _execute_with_context(execution_context):
    node = CodeNode(
        id="code-1",
        data=CodeNodeData(title="Code", code="def main(inputs): return inputs"),
        execution_context=execution_context,
    )
    node.sandbox_service.execute_python_code = Mock(return_value={"success": True})

    node.execute({})

    return node.sandbox_service.execute_python_code.call_args.kwargs


def test_system_schedule_passes_canonical_organization_to_sandbox():
    kwargs = _execute_with_context(
        {
            "user_id": None,
            "organization_id": "organization-123",
            "trigger_mode": "schedule",
        }
    )

    assert kwargs["organization_id"] == "organization-123"
    assert kwargs["trigger_type"] == "schedule"


def test_interactive_execution_uses_organization_instead_of_user_as_tenant():
    kwargs = _execute_with_context(
        {
            "user_id": "user-123",
            "organization_id": "organization-123",
            "trigger_mode": "manual",
        }
    )

    assert kwargs["organization_id"] == "organization-123"


def test_missing_organization_does_not_promote_user_to_sandbox_tenant():
    kwargs = _execute_with_context(
        {
            "user_id": "user-123",
            "trigger_mode": "manual",
        }
    )

    assert kwargs["organization_id"] is None
