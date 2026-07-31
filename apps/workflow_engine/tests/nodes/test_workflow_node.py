"""
WorkflowNode 테스트 [GEVENT] Sync 버전

워크플로우 내에서 다른 워크플로우를 실행하는 WorkflowNode의 동작을 테스트합니다.
"""

import uuid
from unittest.mock import MagicMock, Mock, patch

import pytest

from apps.shared.db.models.workflow_deployment import DeploymentType
from apps.shared.domain.workflow_node_binding import (
    WorkflowNodeBinding,
    canonical_snapshot_sha256,
)
from apps.shared.schemas.workflow import NodeSchema
from apps.workflow_engine.domain.execution import NodeExecutionControl
from apps.workflow_engine.workflow.core.runtime_dependencies import (
    WorkflowRuntimeDependencies,
)
from apps.workflow_engine.workflow.core.workflow_node_factory import NodeFactory
from apps.workflow_engine.workflow.errors import WorkflowNodeConfigurationError
from apps.workflow_engine.workflow.nodes.base.entities import NodeStatus
from apps.workflow_engine.workflow.nodes.workflow import WorkflowNode
from apps.workflow_engine.workflow.nodes.workflow.entities import (
    WorkflowNodeData,
    WorkflowNodeInput,
)


def _filter_values(expressions):
    values = {}
    for expression in expressions:
        key = getattr(getattr(expression, "left", None), "key", None)
        right = getattr(expression, "right", None)
        if hasattr(right, "value"):
            value = right.value
        elif str(right).lower() == "true":
            value = True
        else:
            value = right
        values[key] = value
    return values


def test_workflow_node_initialization():
    """WorkflowNode가 올바르게 초기화되는지 테스트합니다."""
    # Given
    node_data = WorkflowNodeData(
        title="서브 워크플로우 실행",
        workflowId="workflow-123",
        appId="app-456",
        inputs=[
            WorkflowNodeInput(name="query", value_selector=["node-1", "output"]),
            WorkflowNodeInput(name="context", value_selector=["node-2", "text"]),
        ],
    )

    # When
    node = WorkflowNode(id="workflow-node-1", data=node_data)

    # Then
    assert node.id == "workflow-node-1"
    assert node.data.title == "서브 워크플로우 실행"
    assert node.data.workflowId == "workflow-123"
    assert node.data.appId == "app-456"
    assert len(node.data.inputs) == 2
    assert node.status == NodeStatus.IDLE
    assert node.node_type == "workflowNode"


def test_workflow_node_factory_allows_optional_workflow_id_to_be_omitted():
    node = NodeFactory.create(
        NodeSchema(
            id="workflow-node-1",
            type="workflowNode",
            position={"x": 0, "y": 0},
            data={"title": "서브 워크플로우 실행", "appId": "app-456"},
        )
    )

    assert isinstance(node, WorkflowNode)
    assert node.data.workflowId == ""
    assert node.data.appId == "app-456"


def test_bound_workflow_node_keeps_original_deployment_after_active_change() -> None:
    organization_id = uuid.uuid4()
    parent_app_id = uuid.uuid4()
    target_app_id = uuid.uuid4()
    workflow_id = uuid.uuid4()
    bound_deployment_id = uuid.uuid4()
    active_deployment_id = uuid.uuid4()
    bound_graph = {
        "nodes": [{"id": "bound", "type": "startNode", "data": {}}],
        "edges": [],
    }
    active_graph = {
        "nodes": [{"id": "active", "type": "startNode", "data": {}}],
        "edges": [],
    }
    app = Mock(
        id=target_app_id,
        organization_id=organization_id,
        workflow_id=workflow_id,
        active_deployment_id=active_deployment_id,
    )
    active_deployment = Mock(
        id=active_deployment_id,
        app_id=target_app_id,
        version=2,
        type=DeploymentType.WORKFLOW_NODE,
        graph_snapshot=active_graph,
    )
    bound_deployment = Mock(
        id=bound_deployment_id,
        app_id=target_app_id,
        version=1,
        type=DeploymentType.WORKFLOW_NODE,
        graph_snapshot=bound_graph,
    )
    binding = WorkflowNodeBinding(
        container_path=(),
        workflow_node_id="workflow-node",
        target_app_id=target_app_id,
        deployment_id=bound_deployment_id,
        deployment_version=1,
        snapshot_sha256=canonical_snapshot_sha256(bound_graph),
    )
    control = NodeExecutionControl(
        execution_id=uuid.uuid4(),
        invocation_path_prefix=(),
        external_effect_context=None,
        workflow_node_binding=binding,
    )
    db = MagicMock()
    db.query.return_value.filter.return_value.first.side_effect = [
        app,
        active_deployment,
        bound_deployment,
    ]
    node = WorkflowNode(
        id="workflow-node",
        data=WorkflowNodeData(
            title="Bound child",
            workflowId=str(workflow_id),
            appId=str(target_app_id),
            inputs=[],
        ),
        execution_context={
            "db": db,
            "organization_id": str(organization_id),
            "app_id": str(parent_app_id),
        },
    )
    runtime_dependencies = WorkflowRuntimeDependencies(
        provider_execution_runtime=object(),
        provider_usage_recorder=object(),
    )
    node.bind_runtime_dependencies(runtime_dependencies)

    with patch(
        "apps.workflow_engine.workflow.core.workflow_engine.WorkflowEngine"
    ) as engine_type:
        child_engine = engine_type.create_child.return_value
        child_engine.execute.return_value = {"answer": "bound"}
        child_engine.cleanup = Mock()

        result = node.execute({}, runtime_control=control)

    assert result == {"result": {"answer": "bound"}}
    assert engine_type.create_child.call_args.args[0] == bound_graph
    assert engine_type.create_child.call_args.args[0] != active_graph
    assert engine_type.create_child.call_args.kwargs["runtime_control"] is control
    assert (
        engine_type.create_child.call_args.kwargs["runtime_dependencies"]
        is runtime_dependencies
    )


def test_bound_workflow_node_respects_broken_active_pointer_kill_switch() -> None:
    organization_id = uuid.uuid4()
    target_app_id = uuid.uuid4()
    deployment_id = uuid.uuid4()
    workflow_id = uuid.uuid4()
    graph = {"nodes": [], "edges": []}
    app = Mock(
        id=target_app_id,
        organization_id=organization_id,
        workflow_id=workflow_id,
        active_deployment_id=uuid.uuid4(),
    )
    binding = WorkflowNodeBinding(
        container_path=(),
        workflow_node_id="workflow-node",
        target_app_id=target_app_id,
        deployment_id=deployment_id,
        deployment_version=1,
        snapshot_sha256=canonical_snapshot_sha256(graph),
    )
    control = NodeExecutionControl(
        execution_id=uuid.uuid4(),
        invocation_path_prefix=(),
        external_effect_context=None,
        workflow_node_binding=binding,
    )
    db = MagicMock()
    db.query.return_value.filter.return_value.first.side_effect = [app, None]
    node = WorkflowNode(
        id="workflow-node",
        data=WorkflowNodeData(
            title="Bound child",
            workflowId=str(workflow_id),
            appId=str(target_app_id),
            inputs=[],
        ),
        execution_context={
            "db": db,
            "organization_id": str(organization_id),
        },
    )

    with pytest.raises(
        WorkflowNodeConfigurationError,
        match="workflow_node.target_unavailable",
    ):
        node.execute({}, runtime_control=control)

    assert db.query.call_count == 2


def test_workflow_node_execution_with_input_mapping():
    """WorkflowNode가 입력 매핑을 적용하여 서브 워크플로우를 실행하는지 테스트합니다."""
    # Given
    node_data = WorkflowNodeData(
        title="텍스트 처리 모듈",
        workflowId="workflow-abc",
        appId="app-xyz",
        inputs=[
            WorkflowNodeInput(name="input_text", value_selector=["start-node", "text"]),
            WorkflowNodeInput(name="language", value_selector=["config-node", "lang"]),
        ],
    )
    node = WorkflowNode(id="wf-node-1", data=node_data)

    # Mock DB and models
    mock_db = MagicMock()
    mock_app = Mock()
    mock_app.id = "app-xyz"
    mock_app.name = "Text Processor"
    mock_app.organization_id = "org-current"
    mock_app.active_deployment_id = "deploy-1"

    mock_deployment = Mock()
    mock_deployment.id = "deploy-1"
    mock_deployment.version = "v1.0"
    mock_deployment.type = DeploymentType.WORKFLOW_NODE
    mock_deployment.graph_snapshot = {
        "nodes": [
            {
                "id": "start",
                "type": "startNode",
                "position": {"x": 0, "y": 0},
                "data": {"title": "Start"},
            }
        ],
        "edges": [],
    }

    # Mock query chain
    mock_db.query.return_value.filter.return_value.first.side_effect = [
        mock_app,
        mock_deployment,
    ]

    # Mock WorkflowEngine [GEVENT] sync version
    # WorkflowEngine is imported inside _run method
    with patch(
        "apps.workflow_engine.workflow.core.workflow_engine.WorkflowEngine"
    ) as MockEngine:
        mock_engine_instance = MockEngine.create_child.return_value
        mock_engine_instance.execute = Mock(
            return_value={
                "answer": "처리 완료",
                "processed_text": "HELLO WORLD",
            }
        )
        mock_engine_instance.cleanup = Mock()

        # Execution context
        node.execution_context = {
            "db": mock_db,
            "user_id": "user-1",
            "organization_id": "org-current",
            "app_id": "parent-app",
            "public_chat_history": [
                {"role": "user", "content": "private parent question"}
            ],
            "public_chat_history_ref": "e" * 32,
            "public_chat_history_consumer_ref": "node-location:v1:parent",
            "execution_actor": {"type": "public"},
            "suppress_content_persistence": True,
            "public_request_deadline_at": "2026-01-02T00:10:00+00:00",
        }

        # Input from previous nodes
        inputs = {
            "start-node": {"text": "Hello World", "timestamp": "2026-01-02"},
            "config-node": {"lang": "en", "mode": "basic"},
        }

        # When [GEVENT] sync 호출
        result = node.execute(inputs)

        # Then
        # 1. App과 Deployment 조회 확인
        assert mock_db.query.call_count == 2
        deployment_filters = mock_db.query.return_value.filter.call_args_list[1].args
        assert _filter_values(deployment_filters) == {
            "id": "deploy-1",
            "app_id": "app-xyz",
            "is_active": True,
        }

        # 2. Child WorkflowEngine이 올바른 인자로 초기화되었는지 확인
        MockEngine.create_child.assert_called_once()
        call_args = MockEngine.create_child.call_args

        # First positional arg is the graph
        assert call_args[0][0] == mock_deployment.graph_snapshot
        # Second positional arg is the user_input
        assert call_args[0][1] == {"input_text": "Hello World", "language": "en"}
        # Keyword arg is_deployed should be True
        assert call_args[1]["is_deployed"] is True
        invocation_segment = call_args[1]["invocation_segment"]
        assert invocation_segment.kind == "subworkflow"
        assert invocation_segment.node_id == "wf-node-1"
        assert invocation_segment.scope == "deploy-1"
        sub_context = call_args[1]["execution_context"]
        assert sub_context["workflow_node_depth"] == 1
        assert set(sub_context["workflow_node_visited_app_ids"]) == {
            "parent-app",
            "app-xyz",
        }
        assert "public_chat_history" not in sub_context
        assert "public_chat_history_ref" not in sub_context
        assert "public_chat_history_consumer_ref" not in sub_context
        assert sub_context["execution_actor"] == {"type": "public"}
        assert sub_context["suppress_content_persistence"] is True
        assert sub_context["public_request_deadline_at"] == "2026-01-02T00:10:00+00:00"
        assert (
            node.execution_context["public_chat_history"][0]["content"]
            == "private parent question"
        )
        assert node.execution_context.get("workflow_node_depth") is None

        # 3. 실행 결과 확인 (WorkflowNode는 {"result": ...} 형태로 반환)
        assert result["result"]["answer"] == "처리 완료"
        assert result["result"]["processed_text"] == "HELLO WORLD"

        # 4. 상태가 COMPLETED로 변경되었는지 확인
        assert node.status == NodeStatus.COMPLETED


def test_workflow_node_error_no_db_session():
    """DB 세션이 없을 때 ValueError를 발생시키는지 테스트합니다."""
    # Given
    node_data = WorkflowNodeData(
        title="에러 테스트", workflowId="wf-1", appId="app-1", inputs=[]
    )
    node = WorkflowNode(id="node-1", data=node_data)
    node.execution_context = {}  # DB 세션 없음

    # When / Then
    with pytest.raises(WorkflowNodeConfigurationError, match="DB session required"):
        node.execute({})


def test_workflow_node_error_app_not_found():
    """타겟 App을 찾을 수 없을 때 ValueError를 발생시키는지 테스트합니다."""
    # Given
    node_data = WorkflowNodeData(
        title="App 없음", workflowId="wf-1", appId="nonexistent-app", inputs=[]
    )
    node = WorkflowNode(id="node-1", data=node_data)

    mock_db = MagicMock()
    mock_db.query.return_value.filter.return_value.first.return_value = None  # App 없음

    node.execution_context = {"db": mock_db}

    # When / Then
    with pytest.raises(
        WorkflowNodeConfigurationError,
        match="Target App .* not found",
    ):
        node.execute({})


def test_workflow_node_error_no_active_deployment():
    """활성 배포가 없을 때 ValueError를 발생시키는지 테스트합니다."""
    # Given
    node_data = WorkflowNodeData(
        title="배포 없음", workflowId="wf-1", appId="app-1", inputs=[]
    )
    node = WorkflowNode(id="node-1", data=node_data)

    mock_db = MagicMock()
    mock_app = Mock()
    mock_app.id = "app-1"
    mock_app.name = "Test App"
    mock_app.organization_id = "org-current"
    mock_app.active_deployment_id = None  # 활성 배포 없음

    mock_db.query.return_value.filter.return_value.first.return_value = mock_app

    node.execution_context = {"db": mock_db, "organization_id": "org-current"}

    # When / Then
    with pytest.raises(
        WorkflowNodeConfigurationError,
        match="workflow_node.target_unavailable",
    ):
        node.execute({})


def test_workflow_node_rejects_recursive_target_app_before_db_lookup():
    node_data = WorkflowNodeData(
        title="순환 참조", workflowId="wf-1", appId="app-1", inputs=[]
    )
    node = WorkflowNode(id="node-1", data=node_data)
    mock_db = MagicMock()
    node.execution_context = {
        "db": mock_db,
        "organization_id": "org-current",
        "app_id": "app-1",
    }

    with pytest.raises(
        WorkflowNodeConfigurationError,
        match="Recursive workflow-node reference",
    ):
        node.execute({})

    mock_db.query.assert_not_called()


def test_workflow_node_rejects_visited_target_app_before_db_lookup():
    node_data = WorkflowNodeData(
        title="순환 참조", workflowId="wf-1", appId="app-2", inputs=[]
    )
    node = WorkflowNode(id="node-1", data=node_data)
    mock_db = MagicMock()
    node.execution_context = {
        "db": mock_db,
        "organization_id": "org-current",
        "app_id": "app-1",
        "workflow_node_visited_app_ids": ["app-2"],
    }

    with pytest.raises(
        WorkflowNodeConfigurationError,
        match="Recursive workflow-node reference",
    ):
        node.execute({})

    mock_db.query.assert_not_called()


def test_workflow_node_rejects_depth_limit_before_db_lookup():
    node_data = WorkflowNodeData(
        title="깊이 제한", workflowId="wf-1", appId="app-2", inputs=[]
    )
    node = WorkflowNode(id="node-1", data=node_data)
    mock_db = MagicMock()
    node.execution_context = {
        "db": mock_db,
        "organization_id": "org-current",
        "app_id": "app-1",
        "workflow_node_depth": 3,
    }

    with pytest.raises(
        WorkflowNodeConfigurationError,
        match="nesting limit exceeded",
    ):
        node.execute({})

    mock_db.query.assert_not_called()


def test_workflow_node_rejects_active_deployment_without_workflow_node_type():
    node_data = WorkflowNodeData(
        title="일반 배포 거부", workflowId="wf-1", appId="app-1", inputs=[]
    )
    node = WorkflowNode(id="node-1", data=node_data)

    mock_db = MagicMock()
    mock_app = Mock()
    mock_app.id = "app-1"
    mock_app.name = "Test App"
    mock_app.organization_id = "org-current"
    mock_app.active_deployment_id = "deploy-1"

    mock_deployment = Mock()
    mock_deployment.type = DeploymentType.API

    mock_db.query.return_value.filter.return_value.first.side_effect = [
        mock_app,
        mock_deployment,
    ]
    node.execution_context = {"db": mock_db, "organization_id": "org-current"}

    with pytest.raises(
        WorkflowNodeConfigurationError,
        match="workflow_node.target_unavailable",
    ):
        node.execute({})

    deployment_filter_args = mock_db.query.return_value.filter.call_args_list[1].args
    assert _filter_values(deployment_filter_args) == {
        "id": "deploy-1",
        "app_id": "app-1",
        "is_active": True,
    }


def test_workflow_node_rejects_cross_organization_target():
    node_data = WorkflowNodeData(
        title="조직 불일치", workflowId="wf-1", appId="app-1", inputs=[]
    )
    node = WorkflowNode(id="node-1", data=node_data)

    mock_db = MagicMock()
    mock_app = Mock()
    mock_app.id = "app-1"
    mock_app.name = "Test App"
    mock_app.organization_id = "org-other"
    mock_app.active_deployment_id = "deploy-1"
    mock_db.query.return_value.filter.return_value.first.return_value = mock_app

    node.execution_context = {"db": mock_db, "organization_id": "org-current"}

    with pytest.raises(
        WorkflowNodeConfigurationError,
        match="Target App is unavailable",
    ):
        node.execute({})


def test_workflow_node_rejects_missing_parent_organization_context():
    node_data = WorkflowNodeData(
        title="조직 컨텍스트 없음", workflowId="wf-1", appId="app-1", inputs=[]
    )
    node = WorkflowNode(id="node-1", data=node_data)

    mock_db = MagicMock()
    mock_app = Mock()
    mock_app.id = "app-1"
    mock_app.name = "Test App"
    mock_app.organization_id = "org-target"
    mock_app.active_deployment_id = "deploy-1"
    mock_db.query.return_value.filter.return_value.first.return_value = mock_app

    node.execution_context = {"db": mock_db}

    with pytest.raises(
        WorkflowNodeConfigurationError,
        match="Target App is unavailable",
    ):
        node.execute({})


def test_workflow_node_rejects_target_without_organization_scope():
    node_data = WorkflowNodeData(
        title="조직 없는 대상", workflowId="wf-1", appId="app-1", inputs=[]
    )
    node = WorkflowNode(id="node-1", data=node_data)

    mock_db = MagicMock()
    mock_app = Mock()
    mock_app.id = "app-1"
    mock_app.name = "Legacy App"
    mock_app.organization_id = None
    mock_app.active_deployment_id = "deploy-1"
    mock_db.query.return_value.filter.return_value.first.return_value = mock_app

    node.execution_context = {"db": mock_db, "organization_id": "org-current"}

    with pytest.raises(
        WorkflowNodeConfigurationError,
        match="Target App is unavailable",
    ):
        node.execute({})


def test_workflow_node_nested_value_extraction():
    """중첩된 값 선택자가 올바르게 동작하는지 테스트합니다."""
    # Given
    node_data = WorkflowNodeData(
        title="중첩 값 테스트",
        workflowId="wf-1",
        appId="app-1",
        inputs=[
            WorkflowNodeInput(
                name="user_name", value_selector=["user-node", "profile", "name"]
            ),
            WorkflowNodeInput(
                name="user_age", value_selector=["user-node", "profile", "age"]
            ),
        ],
    )
    node = WorkflowNode(id="node-1", data=node_data)

    # Mock DB
    mock_db = MagicMock()
    mock_app = Mock()
    mock_app.organization_id = "org-current"
    mock_app.active_deployment_id = "deploy-1"
    mock_deployment = Mock()
    mock_deployment.type = DeploymentType.WORKFLOW_NODE
    mock_deployment.graph_snapshot = {
        "nodes": [
            {
                "id": "start",
                "type": "startNode",
                "position": {"x": 0, "y": 0},
                "data": {"title": "Start"},
            }
        ],
        "edges": [],
    }

    mock_db.query.return_value.filter.return_value.first.side_effect = [
        mock_app,
        mock_deployment,
    ]

    with patch(
        "apps.workflow_engine.workflow.core.workflow_engine.WorkflowEngine"
    ) as MockEngine:
        mock_engine_instance = MockEngine.create_child.return_value
        mock_engine_instance.execute = Mock(return_value={"result": "OK"})
        mock_engine_instance.cleanup = Mock()

        node.execution_context = {"db": mock_db, "organization_id": "org-current"}
        inputs = {
            "user-node": {"profile": {"name": "Alice", "age": 30, "city": "Seoul"}}
        }

        # When [GEVENT] sync 호출
        node.execute(inputs)

        # Then - check positional args (graph, user_input)
        call_args = MockEngine.create_child.call_args
        sub_workflow_inputs = call_args[0][1]  # Second positional arg
        assert sub_workflow_inputs["user_name"] == "Alice"
        assert sub_workflow_inputs["user_age"] == 30
