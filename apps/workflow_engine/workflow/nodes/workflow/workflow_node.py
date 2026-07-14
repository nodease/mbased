import logging
# asyncio import removed - [GEVENT] migration
from typing import Any, Dict, List

from apps.shared.db.models.app import App
from apps.shared.domain.workflow_execution_identity import InvocationSegment
from apps.shared.domain.workflow_node_binding import (
    MAX_WORKFLOW_NODE_DEPTH,
    canonical_snapshot_sha256,
    graph_has_external_effect,
    workflow_node_references,
)
from apps.shared.services.workflow_node_catalog import node_side_effect_mapping
from apps.shared.domain.deployment_runtime_policy import (
    SURFACE_WORKFLOW_NODE_CHILD_RUN,
    is_deployment_type_allowed_for_surface,
)
from apps.workflow_engine.runtime_policy import get_deployment_runtime_policy
from apps.workflow_engine.workflow.errors import WorkflowNodeConfigurationError
from apps.workflow_engine.workflow.nodes.base.node import Node

from .entities import WorkflowNodeData


logger = logging.getLogger(__name__)

# 참고: 순환 참조를 피하기 위해 WorkflowEngine은 메서드 내부에서 임포트합니다.
# 하지만 WorkflowEngine은 NodeFactory에 의존하고, NodeFactory는 Node에 의존합니다...
# 순환 의존성이 발생할 가능성이 높습니다.
# 따라서 _run 메서드 내부에서 임포트를 처리합니다.

_WORKFLOW_NODE_DEPTH_CONTEXT_KEY = "workflow_node_depth"
_WORKFLOW_NODE_VISITED_APP_IDS_CONTEXT_KEY = "workflow_node_visited_app_ids"


def _get_nested_value(data: Any, keys: List[str]) -> Any:
    for key in keys:
        if isinstance(data, dict):
            data = data.get(key)
        else:
            return None
    return data


def _workflow_node_depth(execution_context: Dict[str, Any]) -> int:
    try:
        return int(execution_context.get(_WORKFLOW_NODE_DEPTH_CONTEXT_KEY, 0) or 0)
    except (TypeError, ValueError):
        return 0


def _visited_app_ids(execution_context: Dict[str, Any]) -> set[str]:
    raw_value = execution_context.get(_WORKFLOW_NODE_VISITED_APP_IDS_CONTEXT_KEY) or []
    if isinstance(raw_value, (str, bytes)):
        raw_value = [raw_value]
    return {str(app_id) for app_id in raw_value if app_id is not None}


class WorkflowNode(Node[WorkflowNodeData]):
    """
    다른 워크플로우(모듈)를 실행하는 노드.
    Function call과 유사하게 동작함.
    """

    node_type = "workflowNode"

    def _run(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        from apps.workflow_engine.workflow.core.workflow_engine import WorkflowEngine

        db, should_close_session = self._borrow_db_session()
        if not db:
            raise WorkflowNodeConfigurationError(
                f"[WorkflowNode] DB session required in execution_context for node {self.id}"
            )

        try:
            # 1. 대상 워크플로우(App)의 Active Deployment 조회
            # workflow_id는 사실상 App의 ID를 가리킴 (App 선택 UI에서 App ID를 저장하도록 가정)
            # 만약 workflow_id가 실제 Workflow 테이블의 ID라면 App을 거쳐서 찾아야 함.
            # 여기서는 프론트엔드에서 App ID를 workflowId 필드에 저장한다고 가정하겠습니다. (또는 appId 필드 사용)
            target_app_id = self.data.appId  # 엔티티 정의에 appId가 있음
            target_app_key = str(target_app_id)
            current_depth = _workflow_node_depth(self.execution_context)
            if current_depth >= MAX_WORKFLOW_NODE_DEPTH:
                raise WorkflowNodeConfigurationError(
                    "[WorkflowNode] Workflow-node nesting limit exceeded"
                )

            visited_app_ids = _visited_app_ids(self.execution_context)
            current_app_id = self.execution_context.get("app_id")
            if current_app_id is not None:
                visited_app_ids.add(str(current_app_id))
            if target_app_key in visited_app_ids:
                raise WorkflowNodeConfigurationError(
                    "[WorkflowNode] Recursive workflow-node reference detected"
                )

            app = db.query(App).filter(App.id == target_app_id).first()
            if not app:
                raise WorkflowNodeConfigurationError(
                    "workflow_node.target_unavailable"
                )
            execution_organization_id = self.execution_context.get("organization_id")
            app_organization_id = getattr(app, "organization_id", None)
            if not execution_organization_id or not app_organization_id:
                raise WorkflowNodeConfigurationError(
                    "[WorkflowNode] Target App is unavailable"
                )
            if (
                execution_organization_id
                and app_organization_id
                and str(app_organization_id) != str(execution_organization_id)
            ):
                raise WorkflowNodeConfigurationError(
                    "[WorkflowNode] Target App is unavailable"
                )

            from apps.shared.db.models.workflow_deployment import WorkflowDeployment

            active_deployment = (
                db.query(WorkflowDeployment)
                .filter(
                    WorkflowDeployment.id == app.active_deployment_id,
                    WorkflowDeployment.app_id == app.id,
                    WorkflowDeployment.is_active.is_(True),
                )
                .first()
            )

            if not active_deployment or not is_deployment_type_allowed_for_surface(
                active_deployment.type,
                SURFACE_WORKFLOW_NODE_CHILD_RUN,
                policy=get_deployment_runtime_policy(),
            ):
                raise WorkflowNodeConfigurationError(
                    "workflow_node.target_unavailable"
                )

            binding = (
                self._runtime_control.workflow_node_binding
                if self._runtime_control is not None
                else None
            )
            if binding is not None:
                if binding.target_app_id != app.id:
                    raise WorkflowNodeConfigurationError(
                        "workflow_node.binding_mismatch"
                    )
                deployment = (
                    db.query(WorkflowDeployment)
                    .filter(
                        WorkflowDeployment.id == binding.deployment_id,
                        WorkflowDeployment.app_id == app.id,
                    )
                    .first()
                )
                if (
                    deployment is None
                    or deployment.version != binding.deployment_version
                    or not is_deployment_type_allowed_for_surface(
                        deployment.type,
                        SURFACE_WORKFLOW_NODE_CHILD_RUN,
                        policy=get_deployment_runtime_policy(),
                    )
                    or canonical_snapshot_sha256(deployment.graph_snapshot)
                    != binding.snapshot_sha256
                ):
                    raise WorkflowNodeConfigurationError(
                        "workflow_node.binding_mismatch"
                    )
            else:
                deployment = active_deployment
                if self._legacy_closure_has_external_effect(
                    db,
                    deployment.graph_snapshot,
                    organization_id=app.organization_id,
                    depth=current_depth + 1,
                    visited={*visited_app_ids, target_app_key},
                ):
                    raise WorkflowNodeConfigurationError(
                        "workflow_node.child_redeployment_required"
                    )

            graph = deployment.graph_snapshot
            if not graph:
                raise WorkflowNodeConfigurationError(
                    f"[WorkflowNode] Deployment {deployment.version} has no graph data"
                )

            # 2. 입력 매핑 처리 (Inputs Mapping)
            sub_workflow_inputs = {}
            for mapping in self.data.inputs:
                target_var = mapping.name
                selector = mapping.value_selector

                val = None
                if selector and len(selector) > 0:
                    node_id = selector[0]
                    source_data = inputs.get(node_id)

                    if source_data is not None:
                        if len(selector) > 1:
                            val = _get_nested_value(source_data, selector[1:])
                        else:
                            val = source_data

                # 값이 없으면 None 또는 빈 문자열? (일단 None)
                sub_workflow_inputs[target_var] = val

            # 3. 서브 워크플로우 실행 (비동기)
            # is_deployed=True로 설정하여 AnswerNode의 결과만 반환받도록 함
            # user_id 등 context 전달
            # parent_run_id를 전달하여 서브 워크플로우의 노드 실행 기록이 부모 워크플로우와 연결되도록 함
            parent_run_id = self.execution_context.get("workflow_run_id")
            sub_execution_context = dict(self.execution_context)
            sub_execution_context["organization_id"] = str(app.organization_id)
            sub_execution_context["app_id"] = str(app.id)
            sub_execution_context["workflow_id"] = str(app.workflow_id)
            sub_execution_context["deployment_id"] = str(deployment.id)
            sub_execution_context["workflow_version"] = deployment.version
            sub_execution_context[_WORKFLOW_NODE_DEPTH_CONTEXT_KEY] = current_depth + 1
            sub_execution_context[_WORKFLOW_NODE_VISITED_APP_IDS_CONTEXT_KEY] = list(
                visited_app_ids | {target_app_key}
            )

            # 서브 워크플로우도 세션 객체 대신 factory를 통해 필요한 시점에 세션을 엽니다.
            control = self._runtime_control
            execution_id = control.execution_id if control is not None else None
            invocation_path_prefix = None
            if control is not None:
                invocation_path_prefix = control.invocation_path_prefix + (
                    InvocationSegment(
                        "subworkflow",
                        self.id,
                        str(deployment.id),
                    ),
                )
            engine = WorkflowEngine(
                graph,
                sub_workflow_inputs,
                execution_context=sub_execution_context,
                is_deployed=True,
                db=db,
                parent_run_id=parent_run_id,
                is_subworkflow=True,  # [FIX] 서브 워크플로우 표시 - Redis 이벤트 발행 스킵
                execution_id=execution_id,
                invocation_path_prefix=invocation_path_prefix,
                task_deadline=(control.task_deadline if control is not None else None),
            )

            # [동기 전환] 직접 동기적으로 서브 워크플로우 실행
            try:
                result = engine.execute()
                if engine._external_effect_output_sensitive:
                    self._trace_metadata = {
                        "external_effect_output": {"sensitive": True}
                    }
            finally:
                try:
                    engine.cleanup()
                except Exception as exc:
                    logger.warning(
                        "Workflow-node child cleanup failed: error_type=%s",
                        type(exc).__name__,
                    )
        finally:
            if should_close_session and db is not None:
                try:
                    db.close()
                except Exception as exc:
                    logger.warning(
                        "Workflow-node session close failed: error_type=%s",
                        type(exc).__name__,
                    )

        # 출력 통일: 항상 'result' 키로 반환
        # 서브 워크플로우의 출력값 구조와 관계없이 일관된 출력 제공
        return {"result": result}

    def _legacy_closure_has_external_effect(
        self,
        db,
        graph: dict | None,
        *,
        organization_id,
        depth: int,
        visited: set[str],
    ) -> bool:
        if not isinstance(graph, dict) or graph_has_external_effect(
            graph,
            node_side_effect_mapping(),
        ):
            return True
        references = workflow_node_references(graph)
        if not references:
            return False
        if depth >= MAX_WORKFLOW_NODE_DEPTH:
            return True

        from apps.shared.db.models.workflow_deployment import WorkflowDeployment

        for reference in references:
            target_key = str(reference.target_app_id)
            if target_key in visited:
                return True
            target_app = (
                db.query(App)
                .filter(
                    App.id == reference.target_app_id,
                    App.organization_id == organization_id,
                )
                .first()
            )
            if target_app is None or target_app.active_deployment_id is None:
                return True
            target_deployment = (
                db.query(WorkflowDeployment)
                .filter(
                    WorkflowDeployment.id == target_app.active_deployment_id,
                    WorkflowDeployment.app_id == target_app.id,
                    WorkflowDeployment.is_active.is_(True),
                )
                .first()
            )
            if (
                target_deployment is None
                or not is_deployment_type_allowed_for_surface(
                    target_deployment.type,
                    SURFACE_WORKFLOW_NODE_CHILD_RUN,
                    policy=get_deployment_runtime_policy(),
                )
                or self._legacy_closure_has_external_effect(
                    db,
                    target_deployment.graph_snapshot,
                    organization_id=organization_id,
                    depth=depth + 1,
                    visited={*visited, target_key},
                )
            ):
                return True
        return False

    def _borrow_db_session(self):
        session_factory = self.execution_context.get("db_session_factory")
        if callable(session_factory):
            return session_factory(), True
        legacy_session = self.execution_context.get("db")
        if legacy_session is not None:
            return legacy_session, False
        return None, False
