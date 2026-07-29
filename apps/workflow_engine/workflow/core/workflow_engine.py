"""
WorkflowEngine - Gevent-based Workflow Execution Engine

[GEVENT] Migrated from asyncio to gevent for Celery gevent pool compatibility.
"""

import logging
import time
import uuid
from dataclasses import replace
from enum import Enum
from typing import Any, Dict, List, Optional, Union

import gevent
from gevent.pool import Pool
from gevent.queue import Queue
from sqlalchemy.orm import Session

from apps.shared.db.session import SessionLocal
from apps.shared.domain.slack_delivery import (
    SlackGraphBoundaryError,
    validate_slack_graph_boundary,
)
from apps.shared.domain.workflow_execution_identity import (
    InvocationSegment,
    derive_node_invocation_id,
    ensure_execution_id,
)
from apps.shared.domain.workflow_node_binding import (
    WorkflowNodeBinding,
    parse_workflow_node_bindings,
)
from apps.shared.pubsub import publish_workflow_event  # [GEVENT] Use sync version
from apps.shared.schemas.workflow import EdgeSchema, NodeSchema
from apps.shared.schemas.workflow_citation import (
    MAX_WORKFLOW_CITATIONS,
    WORKFLOW_CITATION_RESULT_KEY,
    WorkflowCitationEnvelope,
    WorkflowCitationItem,
)
from apps.shared.services.external_effect_trace_capture import (
    durable_provider_summary,
    uses_metadata_only_provider_capture,
)
from apps.shared.services.tracing.metadata import TraceMetadataSanitizer
from apps.workflow_engine.domain.execution import NodeExecutionControl
from apps.workflow_engine.domain.external_effect import (
    ExternalEffectContext,
    ExternalEffectError,
    ExternalEffectRetrySignal,
)
from apps.workflow_engine.services.model_routing_effect_profile import (
    build_model_routing_effect_profiles,
)
from apps.workflow_engine.workflow.core.runtime_dependencies import (
    WorkflowRuntimeDependencies,
)
from apps.workflow_engine.workflow.core.workflow_logger import WorkflowLogger
from apps.workflow_engine.workflow.core.workflow_node_factory import NodeFactory
from apps.workflow_engine.workflow.errors import (
    NonRetryableWorkflowError,
    WorkflowNodeConfigurationError,
)


class _ControlEdgeState(str, Enum):
    PENDING = "pending"
    ACTIVE = "active"
    INACTIVE = "inactive"


class _NodeScheduleState(str, Enum):
    PENDING = "pending"
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    INACTIVE = "inactive"
    FAILED = "failed"
    CANCELLED = "cancelled"


class WorkflowEngine:
    """노드와 엣지를 받아서 전체 워크플로우 실행을 담당하는 엔진 (Gevent 기반)"""

    @staticmethod
    def _default_session_factory() -> Session:
        return SessionLocal()

    @classmethod
    def create_child(
        cls,
        graph: Union[Dict[str, Any], tuple[List[NodeSchema], List[EdgeSchema]]],
        user_input: Optional[Dict[str, Any]] = None,
        *,
        execution_context: Optional[Dict[str, Any]],
        runtime_control: Optional[NodeExecutionControl],
        invocation_segment: InvocationSegment,
        is_deployed: bool = False,
        db: Optional[Session] = None,
        parent_run_id: Optional[str] = None,
        workflow_timeout: int = 600,
        entry_node_id: Optional[str] = None,
        workflow_node_bindings: Optional[tuple[WorkflowNodeBinding, ...]] = None,
        binding_container_path: tuple[tuple[str, str], ...] = (),
        runtime_dependencies: Optional[WorkflowRuntimeDependencies] = None,
    ) -> "WorkflowEngine":
        """부모 lifecycle을 변경할 수 없는 내부 graph engine을 생성합니다."""
        child_context = dict(execution_context or {})
        child_parent_run_id = parent_run_id or child_context.get("workflow_run_id")
        execution_id = runtime_control.execution_id if runtime_control else None
        invocation_path_prefix = (
            runtime_control.invocation_path_prefix + (invocation_segment,)
            if runtime_control
            else None
        )
        task_deadline = runtime_control.task_deadline if runtime_control else None

        return cls(
            graph=graph,
            user_input=user_input,
            execution_context=child_context,
            is_deployed=is_deployed,
            db=db,
            parent_run_id=child_parent_run_id,
            workflow_timeout=workflow_timeout,
            is_subworkflow=True,
            entry_node_id=entry_node_id,
            execution_id=execution_id,
            invocation_path_prefix=invocation_path_prefix,
            workflow_node_bindings=workflow_node_bindings,
            binding_container_path=binding_container_path,
            task_deadline=task_deadline,
            runtime_dependencies=runtime_dependencies,
        )

    def __init__(
        self,
        graph: Union[Dict[str, Any], tuple[List[NodeSchema], List[EdgeSchema]]],
        user_input: Dict[str, Any] = None,
        execution_context: Dict[str, Any] = None,
        is_deployed: bool = False,
        db: Optional[Session] = None,
        parent_run_id: Optional[str] = None,
        workflow_timeout: int = 600,
        is_subworkflow: bool = False,
        entry_node_id: str | None = None,
        execution_id: uuid.UUID | str | None = None,
        invocation_path_prefix: tuple[InvocationSegment, ...] | None = None,
        workflow_node_bindings: tuple[WorkflowNodeBinding, ...] | None = None,
        binding_container_path: tuple[tuple[str, str], ...] = (),
        task_deadline: float | None = None,
        runtime_dependencies: WorkflowRuntimeDependencies | None = None,
    ):
        """
        WorkflowEngine 초기화

        [GEVENT] asyncio에서 gevent로 전환됨.

        Args:
            graph: 워크플로우 그래프 데이터
            user_input: 사용자가 입력한 변수 값들
            execution_context: 실행 컨텍스트 (user_id 등 전역 환경 정보)
            is_deployed: 배포 모드 여부
            db: DB 세션 (로깅용)
            parent_run_id: 부모 워크플로우의 run_id
            workflow_timeout: 워크플로우 전체 실행 제한 시간 (초)
            is_subworkflow: 서브 워크플로우 여부
            entry_node_id: Loop body처럼 trigger 대신 사용할 검증된 진입 노드 ID
        """
        if isinstance(graph, dict):
            parsed_bindings = parse_workflow_node_bindings(graph)
            nodes = [NodeSchema(**node) for node in graph.get("nodes", [])]
            edges = [EdgeSchema(**edge) for edge in graph.get("edges", [])]
        elif isinstance(graph, tuple) and len(graph) == 2:
            parsed_bindings = None
            nodes = [
                node if isinstance(node, NodeSchema) else NodeSchema(**node)
                for node in graph[0]
            ]
            edges = [
                edge if isinstance(edge, EdgeSchema) else EdgeSchema(**edge)
                for edge in graph[1]
            ]
        else:
            raise ValueError(
                "워크플로우 graph는 dict 또는 (nodes, edges) tuple이어야 합니다."
            )

        self.is_deployed = is_deployed
        self.workflow_node_bindings = tuple(
            workflow_node_bindings
            if workflow_node_bindings is not None
            else (parsed_bindings or ())
        )
        self.binding_container_path = tuple(binding_container_path)
        self.task_deadline = task_deadline
        self.node_schemas = {node.id: node for node in nodes}
        self.node_instances = {}
        self.edges = edges
        self.user_input = user_input if user_input is not None else {}
        self.execution_context = dict(execution_context) if execution_context else {}
        queued_execution_id = self.execution_context.pop("execution_id", None)
        selected_execution_id = execution_id or queued_execution_id
        self._execution_identity_trusted = selected_execution_id is not None
        self.execution_id = ensure_execution_id(selected_execution_id)
        if invocation_path_prefix is None:
            root_scope = str(
                self.execution_context.get("workflow_id") or uuid.UUID(int=0)
            )
            self.invocation_path_prefix = (InvocationSegment("root", "", root_scope),)
        else:
            self.invocation_path_prefix = tuple(invocation_path_prefix)
            if (
                not self.invocation_path_prefix
                or self.invocation_path_prefix[0].kind != "root"
            ):
                raise ValueError("invalid invocation path prefix")
        self.workflow_timeout = workflow_timeout
        self.start_time = 0.0
        self._node_sequence = 0
        self._node_submit_ordinals: dict[str, int] = {}

        self.execution_context.pop("db", None)
        if "db_session_factory" not in self.execution_context:
            self.execution_context["db_session_factory"] = self._default_session_factory
        knowledge_enabled = any(
            schema.type == "llmNode"
            and bool(
                (schema.data or {}).get("knowledgeBases")
                or (schema.data or {}).get("knowledgeCollections")
            )
            for schema in self.node_schemas.values()
        )
        remote_file_enabled = any(
            schema.type == "fileExtractionNode" for schema in self.node_schemas.values()
        )
        self.runtime_dependencies = runtime_dependencies or WorkflowRuntimeDependencies()
        session_factory = self.execution_context["db_session_factory"]
        if (
            self.runtime_dependencies.provider_execution_runtime is None
            or self.runtime_dependencies.provider_usage_recorder is None
        ):
            from apps.workflow_engine.composition.provider_execution import (
                build_provider_execution_runtime,
                build_provider_usage_recorder,
            )

            self.runtime_dependencies = replace(
                self.runtime_dependencies,
                provider_execution_runtime=(
                    self.runtime_dependencies.provider_execution_runtime
                    or build_provider_execution_runtime(
                        session_factory=session_factory
                    )
                ),
                provider_usage_recorder=(
                    self.runtime_dependencies.provider_usage_recorder
                    or build_provider_usage_recorder(
                        session_factory=session_factory
                    )
                ),
            )
        if (
            knowledge_enabled
            and self.runtime_dependencies.knowledge_runtime_candidate_resolver is None
        ):
            from apps.workflow_engine.composition.runtime_retrieval import (
                build_knowledge_runtime_candidate_resolver,
            )

            self.runtime_dependencies = replace(
                self.runtime_dependencies,
                knowledge_runtime_candidate_resolver=(
                    build_knowledge_runtime_candidate_resolver(
                        session_factory=session_factory
                    )
                ),
            )
        if knowledge_enabled and self.runtime_dependencies.query_embedding_runtime is None:
            from apps.workflow_engine.composition.provider_execution import (
                build_query_embedding_runtime,
            )

            self.runtime_dependencies = replace(
                self.runtime_dependencies,
                query_embedding_runtime=build_query_embedding_runtime(
                    session_factory=session_factory,
                    usage_recorder=self.runtime_dependencies.provider_usage_recorder,
                ),
            )
        if remote_file_enabled and self.runtime_dependencies.remote_file_fetcher is None:
            from apps.workflow_engine.composition.remote_file import (
                build_remote_file_fetcher,
            )

            self.runtime_dependencies = replace(
                self.runtime_dependencies,
                remote_file_fetcher=build_remote_file_fetcher(),
            )

        # [PERF] 그래프 구조 사전 계산
        self.adjacency_list = {}
        self.reverse_graph = {}
        self.edge_handles = {}
        self.data_dependencies = {}
        self.data_dependents = {}
        self._incoming_control_edges: dict[str, list[int]] = {}
        self._outgoing_control_edges: dict[str, list[int]] = {}
        self._control_edge_states: dict[int, _ControlEdgeState] = {}
        self._node_schedule_states: dict[str, _NodeScheduleState] = {}
        self._build_optimized_graph()
        self._model_routing_effect_profiles = build_model_routing_effect_profiles(
            self.node_schemas,
            self.adjacency_list,
        )
        self._mail_sensitive_node_ids = self._descendants_of_type("mailNode")
        direct_provider_node_ids = {
            node_id
            for node_id, schema in self.node_schemas.items()
            if uses_metadata_only_provider_capture(
                schema.type,
                dict(schema.data or {}),
            )
        }
        self._external_effect_sensitive_node_ids = self._descendants_of_nodes(
            direct_provider_node_ids
        )
        self._external_effect_output_sensitive = False

        # 타입별 노드 인덱스
        self.nodes_by_type = {}
        for node_id, schema in self.node_schemas.items():
            if schema.type not in self.nodes_by_type:
                self.nodes_by_type[schema.type] = []
            self.nodes_by_type[schema.type].append(node_id)

        self._build_node_instances()

        # 로깅 관련 초기화
        self.logger = WorkflowLogger(db)
        if self.execution_context.get("suppress_content_persistence"):
            self.logger.suppress_content_persistence()
        self.parent_run_id = parent_run_id
        self.start_node_id = entry_node_id
        self.is_subworkflow = is_subworkflow

        # 그래프 구조 검증
        self.validate_graph()
        self._reset_scheduler_state()

    def cleanup(self):
        """실행 완료 후 메모리 정리"""
        for node_instance in self.node_instances.values():
            try:
                if (
                    hasattr(node_instance, "_subgraph_engine")
                    and node_instance._subgraph_engine
                ):
                    node_instance._subgraph_engine.cleanup()
            except Exception as exc:
                logging.getLogger(__name__).warning(
                    "Workflow child cleanup failed: error_type=%s",
                    type(exc).__name__,
                )
            finally:
                if hasattr(node_instance, "_subgraph_engine"):
                    node_instance._subgraph_engine = None

        self.node_instances.clear()
        self.node_schemas.clear()
        self.adjacency_list.clear()
        self.reverse_graph.clear()
        self.edge_handles.clear()
        self.data_dependencies.clear()
        self.data_dependents.clear()
        self._incoming_control_edges.clear()
        self._outgoing_control_edges.clear()
        self._control_edge_states.clear()
        self._node_schedule_states.clear()
        self._model_routing_effect_profiles.clear()
        self.nodes_by_type.clear()
        self.execution_context.clear()
        self.user_input = None
        self.logger = None
        self.edges = None
        self._node_submit_ordinals.clear()
        self._external_effect_sensitive_node_ids.clear()
        self.workflow_node_bindings = ()
        self.runtime_dependencies = WorkflowRuntimeDependencies()

    def execute(self) -> Dict[str, Any]:
        """
        워크플로우 전체 실행 (Wrapper)

        [GEVENT] 동기 메서드로 변환.
        """
        if self.is_deployed:
            return self.execute_deployed()

        final_context = {}
        for event in self.execute_stream():
            if event["type"] == "workflow_finish":
                final_context = event["data"]
            elif event["type"] == "error":
                event_data = event["data"]
                error_payload = (
                    event_data
                    if isinstance(event_data, dict) and event_data.get("code")
                    else event_data.get("error")
                    if isinstance(event_data, dict)
                    else None
                )
                if isinstance(error_payload, dict) and error_payload.get("code"):
                    raise ExternalEffectError(
                        str(error_payload["code"]),
                        retryable=bool(error_payload.get("retryable", False)),
                        node_id=error_payload.get("node_id"),
                    )
                if event["data"].get("non_retryable"):
                    raise NonRetryableWorkflowError(event["data"]["message"])
                raise ValueError(event["data"]["message"])

        return final_context

    def execute_stream(self):
        """
        워크플로우를 실행하고 진행 상황을 제너레이터로 반환합니다.

        [GEVENT] async generator에서 일반 generator로 변환.

        Yields Events:
        - node_start: 노드 실행 시작
        - node_finish: 노드 실행 완료
        - workflow_finish: 전체 워크플로우 완료
        - error: 실행 중 오류 발생
        """
        for event in self._execute_core(stream_mode=True):
            yield event

    def execute_deployed(self):
        """
        워크플로우 실행 로직 - 배포 모드

        [GEVENT] 동기 메서드로 변환.
        """
        final_result = None
        try:
            for event in self._execute_core(stream_mode=False):
                if event["type"] == "workflow_finish":
                    final_result = event["data"]
        except Exception as e:
            raise e

        return final_result

    def _execute_core(self, stream_mode: bool = False):
        """
        핵심 실행 로직 - 스트리밍/배포 모드 공용

        [GEVENT] asyncio에서 gevent로 전환:
        - asyncio.Semaphore → gevent.pool.Pool
        - asyncio.wait() → gevent.joinall() with polling
        - asyncio.create_task() → gevent.spawn()
        - asyncio.Queue → gevent.queue.Queue
        """
        self.start_time = time.time()

        # ============================================================
        # 실행 로그 시작
        # ============================================================
        external_run_id = self.execution_context.get("workflow_run_id")

        if self.is_subworkflow:
            if self.parent_run_id:
                self.logger.workflow_run_id = uuid.UUID(self.parent_run_id)
                self.execution_context["workflow_run_id"] = self.parent_run_id
        elif external_run_id:
            self.logger.workflow_run_id = uuid.UUID(external_run_id)
            # [GEVENT] 직접 동기 호출 (gevent가 I/O를 처리)
            self.logger.create_run_log(
                workflow_id=self.execution_context.get("workflow_id"),
                user_id=self.execution_context.get("user_id"),
                user_input=self.user_input,
                is_deployed=self.is_deployed,
                execution_context=self.execution_context,
                external_run_id=external_run_id,
            )
        elif self.parent_run_id:
            self.logger.workflow_run_id = uuid.UUID(self.parent_run_id)
            self.execution_context["workflow_run_id"] = self.parent_run_id
        else:
            # 새로운 run_id 생성
            workflow_run_id = self.logger.create_run_log(
                workflow_id=self.execution_context.get("workflow_id"),
                user_id=self.execution_context.get("user_id"),
                user_input=self.user_input,
                is_deployed=self.is_deployed,
                execution_context=self.execution_context,
            )

            if workflow_run_id:
                self.execution_context["workflow_run_id"] = str(workflow_run_id)
        # ============================================================

        start_node = self._find_start_node()
        results = {}
        self._reset_scheduler_state()

        # [GEVENT] Greenlet 관리
        running_greenlets = {}  # {greenlet: node_id}
        running_nodes: Dict[str, Dict[str, Any]] = {}

        # [GEVENT] Pool for concurrency control
        max_concurrent_tasks = 10
        pool = Pool(size=max_concurrent_tasks)

        # [GEVENT] 이벤트 큐
        event_queue = Queue() if stream_mode else None

        try:
            # 워크플로우 시작 이벤트
            if stream_mode:
                yield {"type": "workflow_start", "data": {}}

            # 초기 시작 노드 실행
            if not self._claim_node_if_ready(start_node, results):
                raise RuntimeError("workflow entry node is not ready")
            self._submit_node(
                start_node,
                results,
                running_greenlets,
                running_nodes,
                stream_mode,
                pool,
                event_queue,
            )

            while running_greenlets:
                # 전체 타임아웃 체크
                elapsed_time = time.time() - self.start_time
                if elapsed_time > self.workflow_timeout:
                    timeout_error = TimeoutError("workflow_timeout")
                    self._mark_running_nodes_timeout(running_nodes, timeout_error)
                    for running_node_id in running_greenlets.values():
                        self._node_schedule_states[running_node_id] = (
                            _NodeScheduleState.CANCELLED
                        )
                    for g in running_greenlets:
                        g.kill()

                    error_msg = (
                        f"Workflow timed out after {self.workflow_timeout} seconds."
                    )
                    raise TimeoutError(error_msg)

                # [GEVENT] 이벤트 큐 처리
                if stream_mode and event_queue:
                    while not event_queue.empty():
                        try:
                            event = event_queue.get_nowait()
                            yield event
                        except Exception:
                            break

                # [GEVENT] 완료된 greenlet 확인
                completed = []
                for greenlet, node_id in list(running_greenlets.items()):
                    if greenlet.ready():
                        completed.append((greenlet, node_id))

                if not completed:
                    # 대기 중인 greenlet이 없으면 잠시 양보
                    gevent.sleep(0.01)
                    continue

                for greenlet, node_id in completed:
                    del running_greenlets[greenlet]
                    running_nodes.pop(node_id, None)

                    try:
                        result_data = greenlet.get()
                        node_result = result_data["result"]
                        results[node_id] = node_result
                        self._propagate_external_effect_output_sensitivity(node_id)
                        next_nodes = self._complete_node_scheduling(
                            node_id,
                            node_result,
                        )

                    except Exception as e:
                        self._node_schedule_states[node_id] = _NodeScheduleState.FAILED
                        for running_node_id in running_greenlets.values():
                            self._node_schedule_states[running_node_id] = (
                                _NodeScheduleState.CANCELLED
                            )
                        for g in running_greenlets:
                            g.kill()

                        raise e

                    # 다음 실행할 노드 탐색 및 제출
                    for next_node_id in self.node_schemas:
                        if next_node_id in next_nodes and self._claim_node_if_ready(
                            next_node_id,
                            results,
                        ):
                            self._submit_node(
                                next_node_id,
                                results,
                                running_greenlets,
                                running_nodes,
                                stream_mode,
                                pool,
                                event_queue,
                            )

            # 남은 이벤트 모두 전달
            if stream_mode and event_queue:
                while not event_queue.empty():
                    try:
                        event = event_queue.get_nowait()
                        yield event
                    except Exception:
                        break

            # 워크플로우 종료
            run_id = self.execution_context.get("workflow_run_id")
            self._external_effect_output_sensitive = bool(
                self._provider_output_source_ids(results)
            )
            if stream_mode:
                final_context = self._with_user_citations(dict(results), results)
                if not self.is_subworkflow:
                    self.logger.update_run_log_finish(
                        self._durable_run_outputs(
                            final_context,
                            all_results=results,
                            stream_mode=True,
                        ),
                        mail_sensitive_lineage=bool(self._mail_sensitive_node_ids),
                    )
                if run_id and not self.is_subworkflow:
                    publish_workflow_event(run_id, "workflow_finish", final_context)
                yield {"type": "workflow_finish", "data": final_context}
            else:
                final_result = self._get_answer_node_result(results)
                final_result = self._with_user_citations(final_result, results)
                if not self.is_subworkflow:
                    self.logger.update_run_log_finish(
                        self._durable_run_outputs(
                            final_result,
                            all_results=results,
                            stream_mode=False,
                        ),
                        mail_sensitive_lineage=bool(self._mail_sensitive_node_ids),
                    )
                if run_id and not self.is_subworkflow:
                    publish_workflow_event(run_id, "workflow_finish", final_result)
                yield {"type": "workflow_finish", "data": final_result}

        except ExternalEffectRetrySignal as e:
            if not self.is_subworkflow:
                self.logger.update_run_log_error(e.code)
            raise
        except Exception as e:
            run_id = self.execution_context.get("workflow_run_id")
            safe_error_code = self._error_code(e)
            safe_payload = (
                e.to_payload() if isinstance(e, ExternalEffectError) else None
            )
            if not stream_mode:
                if not self.is_subworkflow:
                    self.logger.update_run_log_error(safe_error_code)
                if run_id and not self.is_subworkflow:
                    event_data = safe_payload or {"message": safe_error_code}
                    publish_workflow_event(run_id, "error", event_data)
                raise
            else:
                error_msg = safe_error_code
                if not self.is_subworkflow:
                    self.logger.update_run_log_error(error_msg)
                if run_id and not self.is_subworkflow:
                    event_data = safe_payload or {"message": error_msg}
                    publish_workflow_event(run_id, "error", event_data)
                yielded_error = {
                    "message": error_msg,
                    "non_retryable": isinstance(
                        e, (NonRetryableWorkflowError, ExternalEffectError)
                    )
                    and not bool(getattr(e, "retryable", False)),
                }
                if safe_payload is not None:
                    yielded_error.update(safe_payload)
                yield {
                    "type": "error",
                    "data": yielded_error,
                }

    def _submit_node(
        self,
        node_id,
        results,
        running_greenlets,
        running_nodes,
        stream_mode,
        pool,
        event_queue,
    ):
        """
        개별 노드를 실행하기 위해 Greenlet 생성

        [GEVENT] asyncio.create_task() → gevent.spawn()
        """
        if node_id not in self.node_instances:
            raise ValueError(f"노드 ID '{node_id}'를 찾을 수 없습니다.")
        if self._node_schedule_states.get(node_id) != _NodeScheduleState.QUEUED:
            raise RuntimeError("workflow node was submitted without a scheduling claim")
        self._node_schedule_states[node_id] = _NodeScheduleState.RUNNING

        node_instance = self.node_instances[node_id]
        node_schema = self.node_schemas[node_id]

        inputs = self._get_context(node_id, results)

        from datetime import datetime, timezone

        started_at = datetime.now(timezone.utc)

        node_options_snapshot = None
        log_id = None
        self._node_sequence += 1
        sequence = self._node_sequence

        if not self.is_subworkflow:
            node_options_snapshot = self._extract_node_options(node_schema)
            if node_id in self._external_effect_sensitive_node_ids:
                node_options_snapshot = {
                    **node_options_snapshot,
                    "_external_effect_output_sensitive": True,
                }
            log_id = self.logger.create_node_log(
                node_id,
                node_schema.type,
                inputs,
                process_data=node_options_snapshot,
                sequence=sequence,
                mail_sensitive_lineage=self._is_mail_sensitive_node(node_id),
            )
            running_nodes[node_id] = {
                "log_id": log_id,
                "node_type": node_schema.type,
                "inputs": inputs,
                "process_data": node_options_snapshot,
                "started_at": started_at,
                "sequence": sequence,
            }

        ordinal = self._node_submit_ordinals.get(node_id, 0)
        self._node_submit_ordinals[node_id] = ordinal + 1
        runtime_control = self._node_execution_control(
            node_id,
            ordinal=ordinal,
            node_run_id=log_id,
        )

        # Redis Pub/Sub 이벤트 발행
        run_id = self.execution_context.get("workflow_run_id")
        if run_id and not self.is_subworkflow:
            publish_workflow_event(
                run_id,
                "node_start",
                {
                    "node_id": node_id,
                    "node_type": node_schema.type,
                },
            )

        if stream_mode and event_queue:
            event_queue.put(
                {
                    "type": "node_start",
                    "data": {"node_id": node_id, "node_type": node_schema.type},
                }
            )

        def _execute_with_event():
            """노드 실행 및 이벤트 발행 래퍼"""
            node_timeout = (
                node_schema.timeout if node_schema.timeout is not None else 300
            )

            try:
                # [GEVENT] 타임아웃 적용
                with gevent.Timeout(node_timeout):
                    result = self._execute_node_task(
                        node_id,
                        node_schema,
                        node_instance,
                        inputs,
                        log_id,
                        node_options_snapshot,
                        started_at,
                        sequence,
                        runtime_control,
                    )

            except gevent.Timeout:
                timeout_error = TimeoutError("node_timeout")
                self._mark_node_timeout(node_id, running_nodes, timeout_error)
                raise timeout_error

            # node_finish 이벤트 발행
            run_id = self.execution_context.get("workflow_run_id")
            if run_id and not self.is_subworkflow:
                # 실시간 이벤트는 실행자용 실시간 출력이며 추적 메타데이터의 기준 저장소로 쓰지 않습니다.
                publish_workflow_event(
                    run_id,
                    "node_finish",
                    {
                        "node_id": node_id,
                        "node_type": node_schema.type,
                        "output": result,
                    },
                )

            if stream_mode and event_queue:
                # SSE 이벤트도 실행자용 실시간 출력 경계이며 추적 페이로드 접근제어를 대체하지 않습니다.
                event_queue.put(
                    {
                        "type": "node_finish",
                        "data": {
                            "node_id": node_id,
                            "node_type": node_schema.type,
                            "output": result,
                        },
                    }
                )

            return {"result": result}

        # [GEVENT] Pool.spawn()으로 Greenlet 생성
        greenlet = pool.spawn(_execute_with_event)
        running_greenlets[greenlet] = node_id

    def _mark_running_nodes_timeout(
        self, running_nodes: Dict[str, Dict[str, Any]], error: Exception
    ) -> None:
        for node_id in list(running_nodes):
            self._mark_node_timeout(node_id, running_nodes, error)

    def _mark_node_timeout(
        self, node_id: str, running_nodes: Dict[str, Dict[str, Any]], error: Exception
    ) -> None:
        if self.is_subworkflow:
            return
        node_info = running_nodes.pop(node_id, None)
        if not node_info:
            return
        from datetime import datetime, timezone

        finished_at = datetime.now(timezone.utc)
        safe_error_code = self._error_code(error)
        trace_metadata = self._build_error_trace_metadata(
            node_info["node_type"],
            node_info.get("started_at"),
            finished_at,
            error,
            error_code=safe_error_code,
        )
        self.logger.update_node_log_error(
            node_info.get("log_id"),
            node_id,
            safe_error_code,
            node_type=node_info["node_type"],
            inputs=node_info.get("inputs"),
            process_data=node_info.get("process_data"),
            started_at=node_info.get("started_at"),
            trace_metadata=trace_metadata,
            sequence=node_info.get("sequence"),
            mail_sensitive_lineage=self._is_mail_sensitive_node(node_id),
        )

    def _raise_if_task_deadline_expired(
        self,
        node_id: str,
        runtime_control: NodeExecutionControl | None,
    ) -> None:
        deadline = (
            runtime_control.task_deadline
            if runtime_control is not None
            else self.task_deadline
        )
        if isinstance(deadline, bool) or not isinstance(deadline, (int, float)):
            return
        if time.monotonic() >= float(deadline):
            raise ExternalEffectError(
                "external_effect.deadline_exceeded",
                retryable=False,
                node_id=node_id,
            )

    def _execute_node_task(
        self,
        node_id,
        node_schema,
        node_instance,
        inputs,
        log_id=None,
        node_options_snapshot=None,
        started_at=None,
        sequence=None,
        runtime_control: NodeExecutionControl | None = None,
    ):
        """
        개별 노드를 실행하는 작업

        [GEVENT] 동기 메서드로 변환.
        """
        try:
            self._raise_if_task_deadline_expired(node_id, runtime_control)
            # 노드 실행 (핵심) - 동기 실행
            result = node_instance.execute(inputs, runtime_control=runtime_control)
            from datetime import datetime, timezone

            finished_at = datetime.now(timezone.utc)
            trace_metadata = self._build_node_trace_metadata(
                node_schema.type,
                node_instance,
                result,
                node_options_snapshot,
                started_at,
                finished_at,
            )
            trace_payloads = self._collect_node_trace_payloads(node_instance)

            # 노드 완료 로깅
            if not self.is_subworkflow:
                self.logger.update_node_log_finish(
                    log_id,
                    node_id,
                    result,
                    node_type=node_schema.type,
                    inputs=inputs,
                    process_data=node_options_snapshot,
                    started_at=started_at,
                    trace_metadata=trace_metadata,
                    trace_payloads=trace_payloads,
                    sequence=sequence,
                    mail_sensitive_lineage=self._is_mail_sensitive_node(node_id),
                )

            return result

        except Exception as e:
            error_msg = self._error_code(e)
            from datetime import datetime, timezone

            finished_at = datetime.now(timezone.utc)
            trace_metadata = self._build_error_trace_metadata(
                node_schema.type,
                started_at,
                finished_at,
                e,
                node_instance=node_instance,
            )
            if not self.is_subworkflow:
                self.logger.update_node_log_error(
                    log_id,
                    node_id,
                    error_msg,
                    node_type=node_schema.type,
                    inputs=inputs,
                    process_data=node_options_snapshot,
                    started_at=started_at,
                    trace_metadata=trace_metadata,
                    sequence=sequence,
                    mail_sensitive_lineage=self._is_mail_sensitive_node(node_id),
                )
            raise e

    def _collect_node_trace_payloads(self, node_instance) -> list[dict[str, Any]]:
        payloads = getattr(node_instance, "_trace_payloads", []) or []
        if hasattr(node_instance, "_trace_payloads"):
            # 노드 인스턴스 재사용 시 같은 추가 전용 페이로드가 중복 수집되지 않게 비웁니다.
            node_instance._trace_payloads = []
        return payloads

    def _build_error_trace_metadata(
        self,
        node_type: str,
        started_at,
        finished_at,
        error: Exception,
        error_code: Optional[str] = None,
        node_instance=None,
    ) -> Dict[str, Any]:
        safe_error_code = error_code or self._error_code(error)
        metadata: Dict[str, Any] = TraceMetadataSanitizer.sanitize_span_metadata(
            node_type,
            getattr(node_instance, "_trace_metadata", {}) or {},
        )
        metadata["error"] = {
            "type": "node_error",
            "error_type": type(error).__name__,
            "error_code": safe_error_code,
        }
        failure_phase = getattr(error, "failure_phase", None)
        if failure_phase in {"before_send", "response_received", "outcome_unknown"}:
            metadata["error"]["failure_phase"] = failure_phase
        if started_at and finished_at:
            metadata["latency_ms"] = int(
                (finished_at - started_at).total_seconds() * 1000
            )
        if "guardrail" in node_type.lower():
            metadata["guardrail"] = {
                "type": "moduly_guardrail",
                "decision": "errored",
                "blocked": False,
                "reason_code": safe_error_code,
                "reason_redacted": safe_error_code,
                "latency_ms": metadata.get("latency_ms"),
            }
        return TraceMetadataSanitizer.sanitize_span_metadata(node_type, metadata)

    @staticmethod
    def _error_code(error: Exception) -> str:
        code = getattr(error, "code", None)
        if isinstance(code, str) and code:
            return code
        if isinstance(error, TimeoutError):
            return "timeout"
        return "node_error"

    def _node_execution_control(
        self,
        node_id: str,
        *,
        ordinal: int,
        node_run_id: str | uuid.UUID | None,
    ) -> NodeExecutionControl:
        node_segment = InvocationSegment("node", node_id, str(ordinal))
        node_invocation_id = derive_node_invocation_id(
            self.execution_id,
            self.invocation_path_prefix + (node_segment,),
        )
        effect_context = self._external_effect_context(
            node_id,
            node_invocation_id=node_invocation_id,
            node_run_id=node_run_id,
        )
        workflow_node_binding = next(
            (
                binding
                for binding in self.workflow_node_bindings
                if binding.container_path == self.binding_container_path
                and binding.workflow_node_id == node_id
            ),
            None,
        )
        return NodeExecutionControl(
            execution_id=self.execution_id,
            invocation_path_prefix=self.invocation_path_prefix,
            external_effect_context=effect_context,
            task_deadline=self.task_deadline,
            workflow_node_binding=workflow_node_binding,
            workflow_node_bindings=self.workflow_node_bindings,
            binding_container_path=self.binding_container_path,
            external_effect_enforced=self._execution_identity_trusted,
        )

    def _external_effect_context(
        self,
        node_id: str,
        *,
        node_invocation_id: uuid.UUID,
        node_run_id: str | uuid.UUID | None,
    ) -> ExternalEffectContext | None:
        if not self._execution_identity_trusted:
            return None
        try:
            organization_id = uuid.UUID(str(self.execution_context["organization_id"]))
            app_id = uuid.UUID(str(self.execution_context["app_id"]))
            workflow_id = uuid.UUID(str(self.execution_context["workflow_id"]))
            workflow_run_raw = self.execution_context.get("workflow_run_id")
            workflow_run_id = (
                uuid.UUID(str(workflow_run_raw)) if workflow_run_raw else None
            )
            canonical_node_run_id = uuid.UUID(str(node_run_id)) if node_run_id else None
        except (KeyError, TypeError, ValueError, AttributeError):
            return None
        return ExternalEffectContext(
            organization_id=organization_id,
            app_id=app_id,
            workflow_id=workflow_id,
            execution_id=self.execution_id,
            node_invocation_id=node_invocation_id,
            workflow_run_id=workflow_run_id,
            node_run_id=canonical_node_run_id,
            node_id=node_id,
        )

    def _build_node_trace_metadata(
        self,
        node_type: str,
        node_instance,
        result: Any,
        process_data: Optional[Dict[str, Any]],
        started_at,
        finished_at,
    ) -> Dict[str, Any]:
        metadata: Dict[str, Any] = TraceMetadataSanitizer.sanitize_span_metadata(
            node_type, getattr(node_instance, "_trace_metadata", {}) or {}
        )
        latency_ms = (
            int((finished_at - started_at).total_seconds() * 1000)
            if started_at and finished_at
            else None
        )
        result_dict = result if isinstance(result, dict) else {}
        process_data = process_data or {}

        if node_type == "llmNode":
            usage = result_dict.get("usage") or {}
            result_metadata = result_dict.get("metadata") or {}
            result_metadata = (
                result_metadata if isinstance(result_metadata, dict) else {}
            )
            llm_metadata = dict(metadata.get("llm") or {})
            # LLM 메타데이터는 비용/모델 식별용 요약만 담고 프롬프트/완성 원문은 페이로드로 분리합니다.
            llm_metadata.update(
                {
                    "provider": process_data.get("provider"),
                    "model": result_dict.get("model") or process_data.get("model"),
                    "credential_id": process_data.get("credential_id"),
                    "prompt_tokens": usage.get("prompt_tokens", 0),
                    "completion_tokens": usage.get("completion_tokens", 0),
                    "total_tokens": usage.get("total_tokens")
                    or usage.get("prompt_tokens", 0)
                    + usage.get("completion_tokens", 0),
                    "total_cost": result_dict.get("cost", 0.0),
                    "latency_ms": latency_ms,
                    "retry_count": 0,
                }
            )
            routing_metadata = result_metadata.get("model_routing")
            routing_metadata = (
                routing_metadata if isinstance(routing_metadata, dict) else {}
            )
            routing_context = routing_metadata.get("runtime_context")
            if not isinstance(routing_context, dict):
                routing_context = result_metadata.get("routing_context")
            routing_context = (
                routing_context if isinstance(routing_context, dict) else {}
            )
            for key in (
                "policy_id",
                "policy_version",
                "selected_model",
                "fallback_model",
                "fallback_from_model",
                "fallback_reason_code",
                "fallback_provider_error_code",
                "fallback_provider_error_type",
                "fallback_provider_status_code",
                "fallback_provider_remote_error_code",
                "fallback_provider_remote_error_param",
                "fallback_provider_response_status",
                "fallback_used",
                "decision_source",
                "matched_rule_id",
                "strategy_id",
                "reason_code",
                "judge_called",
                "judge",
                "decision_factors",
            ):
                if key in routing_metadata:
                    llm_metadata[key] = routing_metadata[key]
            for key in (
                "customer_facing",
                "knowledge_enabled",
                "output_format",
                "schema_required",
                "has_file_input",
                "input_length_bucket",
                "prompt_length_bucket",
                "node_task",
            ):
                if key in routing_context:
                    llm_metadata[key] = routing_context[key]
            for key in (
                "finish_reason",
                "schema_status",
                "fallback_used",
                "repetition_rate",
            ):
                if key in result_metadata:
                    llm_metadata[key] = result_metadata[key]
                elif key in routing_metadata:
                    llm_metadata[key] = routing_metadata[key]
            metadata["llm"] = llm_metadata
            rag_summary = result_metadata.get("rag")
            rag_summary = rag_summary if isinstance(rag_summary, dict) else {}
            knowledge = result_metadata.get("knowledge_search")
            if knowledge or rag_summary:
                # Per-chunk evidence는 trace_payloads에 두고 run/node metadata에는 요약만 남깁니다.
                rag_metadata = dict(metadata.get("rag") or {})
                rag_metadata.update(rag_summary)
                rag_metadata.update(
                    TraceMetadataSanitizer.summarize_rag_metadata(knowledge)
                )
                rag_metadata["latency_ms"] = latency_ms
                metadata["rag"] = rag_metadata

        elif node_type == "httpRequestNode":
            http_metadata = dict(metadata.get("http") or {})
            http_metadata.setdefault("latency_ms", latency_ms)
            metadata["http"] = http_metadata

        elif node_type == "codeNode":
            sandbox_metadata = dict(metadata.get("sandbox") or {})
            sandbox_metadata.update(
                {
                    "execution_time_ms": result_dict.get(
                        "execution_time_ms", latency_ms
                    ),
                    "exit_code": result_dict.get("exit_code"),
                    "timeout": bool(result_dict.get("timeout", False)),
                    "latency_ms": latency_ms,
                }
            )
            metadata["sandbox"] = sandbox_metadata

        elif node_type == "workflowNode":
            workflow_metadata = dict(metadata.get("workflow") or {})
            workflow_metadata.setdefault("latency_ms", latency_ms)
            metadata["workflow"] = workflow_metadata

        elif "guardrail" in node_type.lower():
            guardrail_metadata = dict(metadata.get("guardrail") or {})
            guardrail_metadata.update(
                {
                    "type": "moduly_guardrail",
                    "decision": result_dict.get("decision"),
                    "blocked": bool(result_dict.get("blocked", False)),
                    "policy_id": result_dict.get("policy_id"),
                    "rule_id": result_dict.get("rule_id"),
                    "reason_code": result_dict.get("reason_code"),
                    "reason_redacted": result_dict.get("reason_redacted"),
                    "severity": result_dict.get("severity"),
                    "latency_ms": latency_ms,
                }
            )
            metadata["guardrail"] = guardrail_metadata

        if latency_ms is not None:
            metadata["latency_ms"] = latency_ms
        return TraceMetadataSanitizer.sanitize_span_metadata(node_type, metadata)

    # ================================================================
    # 그래프 검증 메서드
    # ================================================================

    def validate_graph(self):
        """워크플로우 그래프의 구조적 유효성을 검사합니다."""
        try:
            validate_slack_graph_boundary(
                self.node_schemas.values(),
                require_resolved=True,
                allow_legacy_selectors=False,
            )
        except SlackGraphBoundaryError as exc:
            raise WorkflowNodeConfigurationError(exc.reason_code) from None
        self._check_cycles()
        self._check_start_nodes()
        self._check_isolation()

    def _check_cycles(self):
        """DFS를 사용하여 그래프 내 순환(Cycle)을 감지합니다."""
        visited = set()
        recursion_stack = set()

        for node_id in self.node_schemas:
            if node_id not in visited:
                if self._detect_cycle_dfs(node_id, visited, recursion_stack):
                    raise ValueError(
                        f"워크플로우에 순환(Cycle)이 감지되었습니다. 노드 ID: {node_id}"
                    )

    def _detect_cycle_dfs(self, node_id, visited, recursion_stack):
        """순환 감지를 위한 DFS 재귀 함수"""
        visited.add(node_id)
        recursion_stack.add(node_id)

        for neighbor in self.adjacency_list.get(node_id, []):
            if neighbor not in visited:
                if self._detect_cycle_dfs(neighbor, visited, recursion_stack):
                    return True
            elif neighbor in recursion_stack:
                return True

        recursion_stack.remove(node_id)
        return False

    def _check_start_nodes(self):
        """시작 노드 유효성 검사 및 ID 캐싱"""
        if self.start_node_id is not None:
            entry_node = self.node_schemas.get(self.start_node_id)
            if entry_node is None or entry_node.type == "note":
                raise ValueError("워크플로우 진입 노드가 유효하지 않습니다.")
            return
        start_nodes = []
        TRIGGER_TYPES = ["startNode", "webhookTrigger", "scheduleTrigger"]

        for node_id, node in self.node_schemas.items():
            if node.type in TRIGGER_TYPES:
                start_nodes.append(node_id)

        if len(start_nodes) > 1:
            raise ValueError(
                f"워크플로우에 시작 노드가 {len(start_nodes)}개 있습니다. 시작 노드는 1개만 있어야 합니다."
            )
        elif len(start_nodes) == 0:
            raise ValueError(
                "워크플로우에 시작 노드(type='startNode' or 'webhookTrigger')가 없습니다."
            )

        self.start_node_id = start_nodes[0]

    def _check_isolation(self):
        """시작 노드에서 도달 불가능한 고립 노드가 있는지 검사합니다."""
        start_node_id = self._find_start_node()
        visited = {start_node_id}
        queue = [start_node_id]

        while queue:
            current_node = queue.pop(0)
            neighbors = self.adjacency_list.get(current_node, [])
            for neighbor in neighbors:
                if neighbor not in visited:
                    visited.add(neighbor)
                    queue.append(neighbor)

        all_nodes = set(self.node_schemas.keys())
        valid_nodes = {
            node_id
            for node_id in all_nodes
            if self.node_schemas[node_id].type != "note"
        }

        isolated_nodes = valid_nodes - visited

        if isolated_nodes:
            raise ValueError(
                f"시작 노드에서 도달할 수 없는 고립된 노드가 발견되었습니다. "
                f"노드 IDs: {list(isolated_nodes)}"
            )

    # ================================================================
    # 헬퍼 메서드들
    # ================================================================

    def _find_start_node(self) -> str:
        """시작 노드 찾기"""
        if self.start_node_id is None:
            raise ValueError(
                "시작 노드가 설정되지 않았습니다. validate_graph()를 먼저 호출해주세요."
            )
        return self.start_node_id

    def _get_next_nodes(self, node_id: str, result: Dict[str, Any]) -> List[str]:
        """현재 노드의 다음 노드 목록을 반환합니다."""
        selected_handle = result.get("selected_handle")

        if selected_handle is not None:
            key = (node_id, selected_handle)
            next_nodes = self.edge_handles.get(key, [])
            return next_nodes

        return self.adjacency_list.get(node_id, [])

    def _is_ready(self, node_id: str, results: Dict) -> bool:
        """활성 control edge와 selector source가 모두 준비되었는지 확인합니다."""
        if self._node_schedule_states.get(node_id) != _NodeScheduleState.PENDING:
            return False

        incoming_edges = self._incoming_control_edges.get(node_id, ())
        if incoming_edges:
            incoming_states = [
                self._control_edge_states[edge_index] for edge_index in incoming_edges
            ]
            if _ControlEdgeState.PENDING in incoming_states:
                return False

            active_edges = [
                edge_index
                for edge_index in incoming_edges
                if self._control_edge_states[edge_index] == _ControlEdgeState.ACTIVE
            ]
            if not active_edges:
                return False
            if any(
                self.edges[edge_index].source not in results
                for edge_index in active_edges
            ):
                return False
        elif node_id != self._find_start_node():
            return False

        required_inputs = self.data_dependencies.get(node_id, set())
        return all(source_id in results for source_id in required_inputs)

    def _reset_scheduler_state(self) -> None:
        self._control_edge_states = {
            edge_index: _ControlEdgeState.PENDING
            for edge_index in range(len(self.edges))
        }
        self._node_schedule_states = {
            node_id: _NodeScheduleState.PENDING for node_id in self.node_schemas
        }

    def _claim_node_if_ready(self, node_id: str, results: Dict) -> bool:
        if not self._is_ready(node_id, results):
            return False
        self._node_schedule_states[node_id] = _NodeScheduleState.QUEUED
        return True

    def _complete_node_scheduling(
        self,
        node_id: str,
        result: Dict[str, Any],
    ) -> set[str]:
        if self._node_schedule_states.get(node_id) != _NodeScheduleState.RUNNING:
            raise RuntimeError("workflow node completed outside the running state")
        self._node_schedule_states[node_id] = _NodeScheduleState.SUCCEEDED

        selected_handle = result.get("selected_handle")
        affected_nodes = set(self.data_dependents.get(node_id, ()))
        for edge_index in self._outgoing_control_edges.get(node_id, ()):
            edge = self.edges[edge_index]
            edge_state = (
                _ControlEdgeState.ACTIVE
                if selected_handle is None or edge.sourceHandle == selected_handle
                else _ControlEdgeState.INACTIVE
            )
            self._set_control_edge_state(edge_index, edge_state)
            affected_nodes.add(edge.target)

        affected_nodes.update(self._propagate_inactive_nodes(affected_nodes))
        return affected_nodes

    def _set_control_edge_state(
        self,
        edge_index: int,
        state: _ControlEdgeState,
    ) -> None:
        current_state = self._control_edge_states[edge_index]
        if current_state not in (_ControlEdgeState.PENDING, state):
            raise RuntimeError("workflow control edge state conflict")
        self._control_edge_states[edge_index] = state

    def _propagate_inactive_nodes(self, candidate_node_ids) -> set[str]:
        affected_nodes = set(candidate_node_ids)
        pending = list(candidate_node_ids)

        while pending:
            node_id = pending.pop()
            if self._node_schedule_states.get(node_id) != _NodeScheduleState.PENDING:
                continue

            data_source_inactive = any(
                self._node_schedule_states.get(source_id) == _NodeScheduleState.INACTIVE
                for source_id in self.data_dependencies.get(node_id, ())
            )
            incoming_edges = self._incoming_control_edges.get(node_id, ())
            control_path_inactive = bool(incoming_edges) and all(
                self._control_edge_states[edge_index] == _ControlEdgeState.INACTIVE
                for edge_index in incoming_edges
            )
            if not data_source_inactive and not control_path_inactive:
                continue

            self._node_schedule_states[node_id] = _NodeScheduleState.INACTIVE
            downstream_nodes = set(self.data_dependents.get(node_id, ()))
            for edge_index in self._outgoing_control_edges.get(node_id, ()):
                self._set_control_edge_state(
                    edge_index,
                    _ControlEdgeState.INACTIVE,
                )
                downstream_nodes.add(self.edges[edge_index].target)

            for downstream_node_id in downstream_nodes:
                affected_nodes.add(downstream_node_id)
                pending.append(downstream_node_id)

        return affected_nodes

    def _build_optimized_graph(self):
        """엣지를 분석하여 효율적인 그래프 구조 생성"""
        for edge_index, edge in enumerate(self.edges):
            if edge.source not in self.adjacency_list:
                self.adjacency_list[edge.source] = []
            self.adjacency_list[edge.source].append(edge.target)

            if edge.target not in self.reverse_graph:
                self.reverse_graph[edge.target] = []
            self.reverse_graph[edge.target].append(edge.source)

            key = (edge.source, edge.sourceHandle)
            if key not in self.edge_handles:
                self.edge_handles[key] = []
            self.edge_handles[key].append(edge.target)

            self._incoming_control_edges.setdefault(edge.target, []).append(edge_index)
            self._outgoing_control_edges.setdefault(edge.source, []).append(edge_index)

        self._analyze_data_dependencies()

    def _descendants_of_type(self, node_type: str) -> set[str]:
        return self._descendants_of_nodes(
            node_id
            for node_id, schema in self.node_schemas.items()
            if schema.type == node_type
        )

    def _descendants_of_nodes(self, source_node_ids) -> set[str]:
        forward_dependencies: dict[str, set[str]] = {
            node_id: set(targets) for node_id, targets in self.adjacency_list.items()
        }
        for target_id, source_ids in self.data_dependencies.items():
            for source_id in source_ids:
                forward_dependencies.setdefault(source_id, set()).add(target_id)
        pending = list(source_node_ids)
        descendants: set[str] = set()
        while pending:
            node_id = pending.pop()
            if node_id in descendants:
                continue
            descendants.add(node_id)
            pending.extend(forward_dependencies.get(node_id, ()))
        return descendants

    def _durable_run_outputs(
        self,
        outputs: Any,
        *,
        all_results: Dict[str, Any],
        stream_mode: bool,
    ) -> Any:
        outputs = self._without_user_citations(outputs)
        provider_node_ids = self._provider_output_source_ids(all_results)
        if not provider_node_ids:
            return outputs

        protected_node_ids = self._descendants_of_nodes(provider_node_ids)
        if stream_mode:
            durable_outputs = dict(outputs) if isinstance(outputs, dict) else {}
            for node_id in protected_node_ids:
                if node_id not in durable_outputs:
                    continue
                if node_id not in provider_node_ids:
                    durable_outputs[node_id] = {}
                    continue
                schema = self.node_schemas[node_id]
                durable_outputs[node_id] = (
                    durable_provider_summary(
                        node_type=schema.type,
                        process_data=dict(schema.data or {}),
                        trace_metadata=getattr(
                            self.node_instances.get(node_id), "_trace_metadata", {}
                        ),
                    )
                    or {}
                )
            return durable_outputs

        answer_node_ids = set(self.nodes_by_type.get("answerNode", ()))
        if answer_node_ids & protected_node_ids:
            return {}
        return outputs

    def _provider_output_source_ids(
        self,
        all_results: Dict[str, Any],
    ) -> set[str]:
        return {
            node_id
            for node_id, schema in self.node_schemas.items()
            if node_id in all_results
            and uses_metadata_only_provider_capture(
                schema.type,
                dict(schema.data or {}),
                getattr(self.node_instances.get(node_id), "_trace_metadata", {}),
            )
        }

    def _propagate_external_effect_output_sensitivity(self, node_id: str) -> None:
        schema = self.node_schemas.get(node_id)
        if schema is None or not uses_metadata_only_provider_capture(
            schema.type,
            dict(schema.data or {}),
            getattr(self.node_instances.get(node_id), "_trace_metadata", {}),
        ):
            return
        self._external_effect_sensitive_node_ids.update(
            self._descendants_of_nodes((node_id,))
        )

    def _is_mail_sensitive_node(self, node_id: str) -> bool:
        return node_id in getattr(self, "_mail_sensitive_node_ids", set())

    def _build_node_instances(self):
        """NodeSchema를 실제 Node 인스턴스로 변환"""
        for node_id, schema in self.node_schemas.items():
            if schema.type == "note":
                continue

            try:
                node_context = self.execution_context
                if schema.type == "llmNode":
                    node_context = {
                        **self.execution_context,
                        "model_routing_effect_profile": self._model_routing_effect_profiles.get(
                            node_id,
                            {},
                        ),
                    }
                self.node_instances[node_id] = NodeFactory.create(
                    schema,
                    context=node_context,
                    runtime_dependencies=self.runtime_dependencies,
                )
            except NotImplementedError as e:
                raise NotImplementedError(
                    f"Cannot create node '{node_id}': {str(e)}"
                ) from e

    def _analyze_data_dependencies(self):
        """각 노드의 value_selector를 분석하여 실제 데이터 의존성을 추출합니다."""
        for node_id, schema in self.node_schemas.items():
            if schema.type in ["startNode", "webhookTrigger", "scheduleTrigger"]:
                self.data_dependencies[node_id] = set()
                continue

            referenced_nodes = self._extract_value_selectors(schema)
            self.data_dependencies[node_id] = referenced_nodes
            for source_node_id in referenced_nodes:
                self.data_dependents.setdefault(source_node_id, set()).add(node_id)

    def _extract_value_selectors(self, schema: NodeSchema) -> set:
        """NodeSchema의 data에서 모든 value_selector를 추출합니다."""
        referenced_nodes = set()

        if not schema.data:
            return referenced_nodes

        data_dict = schema.data if isinstance(schema.data, dict) else schema.data.dict()

        def add_selector(selector):
            if isinstance(selector, list) and selector:
                if isinstance(selector[0], str):
                    node_id = selector[0]
                    if node_id in self.node_schemas:
                        referenced_nodes.add(node_id)
                    return
                for item in selector:
                    add_selector(item)

        def extract_from_value(value):
            if isinstance(value, dict):
                for key, child in value.items():
                    if key.endswith("_selector") or key.endswith("_selectors"):
                        add_selector(child)
                    extract_from_value(child)

            elif isinstance(value, list):
                for item in value:
                    extract_from_value(item)

        extract_from_value(data_dict)

        # CodeNode input은 selector 배열 대신 "node-id.variable" source를 사용한다.
        # Citation lineage에도 실제 데이터 전달 경로를 포함해야 한다.
        if getattr(schema, "type", None) == "codeNode":
            inputs = data_dict.get("inputs")
            if isinstance(inputs, list):
                for input_item in inputs:
                    if not isinstance(input_item, dict):
                        continue
                    source = input_item.get("source")
                    if not isinstance(source, str):
                        continue
                    source_node_id, separator, _ = source.partition(".")
                    if separator and source_node_id in self.node_schemas:
                        referenced_nodes.add(source_node_id)
        return referenced_nodes

    def _get_context(self, node_id: str, results: Dict) -> Dict[str, Any]:
        """현재 노드가 실행에 필요한 모든 입력 데이터를 구성"""
        if node_id == self.start_node_id:
            return self.user_input

        return dict(results)

    def _get_answer_node_result(self, results: Dict) -> Dict[str, Any]:
        """배포 모드에서 AnswerNode의 결과만 추출하여 반환합니다."""
        for node_id in self.nodes_by_type.get("answerNode", []):
            if node_id in results:
                return results[node_id]

        return {}

    def _with_user_citations(
        self,
        outputs: Any,
        all_results: Dict[str, Any],
    ) -> Any:
        """Answer data lineage의 ephemeral Citation만 최종 응답에 투영한다."""
        if not isinstance(outputs, dict):
            return outputs

        response = dict(outputs)
        self._user_citations_attached = False
        if self.is_subworkflow or self._has_user_citation_key_collision(response):
            return response
        envelope = self._collect_user_citations(all_results)
        if envelope.items:
            response[WORKFLOW_CITATION_RESULT_KEY] = envelope.model_dump(mode="json")
            self._user_citations_attached = True
        return response

    def _collect_user_citations(
        self,
        all_results: Dict[str, Any],
    ) -> WorkflowCitationEnvelope:
        answer_node_id = next(
            (
                node_id
                for node_id in self.nodes_by_type.get("answerNode", ())
                if node_id in all_results
            ),
            None,
        )
        if answer_node_id is None:
            return WorkflowCitationEnvelope()

        ancestor_ids = self._data_ancestors(answer_node_id)
        merged_items: list[WorkflowCitationItem] = []
        seen: set[tuple[object, ...]] = set()
        for node_id, schema in self.node_schemas.items():
            if (
                node_id not in ancestor_ids
                or node_id not in all_results
                or schema.type != "llmNode"
            ):
                continue
            raw_envelope = getattr(
                self.node_instances.get(node_id),
                "_user_citations",
                None,
            )
            try:
                envelope = WorkflowCitationEnvelope.model_validate(raw_envelope)
            except Exception:
                continue
            for item in envelope.items:
                fingerprint = (
                    item.label,
                    item.page_number,
                    item.section,
                    item.content_preview,
                )
                if fingerprint in seen:
                    continue
                seen.add(fingerprint)
                rank = len(merged_items) + 1
                merged_items.append(
                    item.model_copy(
                        update={
                            "citation_id": f"evidence-{rank}",
                            "evidence_rank": rank,
                        }
                    )
                )
                if len(merged_items) >= MAX_WORKFLOW_CITATIONS:
                    return WorkflowCitationEnvelope(items=merged_items)
        return WorkflowCitationEnvelope(items=merged_items)

    def _data_ancestors(self, node_id: str) -> set[str]:
        pending = list(self.data_dependencies.get(node_id, ()))
        ancestors: set[str] = set()
        while pending:
            ancestor_id = pending.pop()
            if ancestor_id in ancestors:
                continue
            ancestors.add(ancestor_id)
            pending.extend(self.data_dependencies.get(ancestor_id, ()))
        return ancestors

    @staticmethod
    def _has_user_citation_key_collision(response: dict[str, Any]) -> bool:
        return WORKFLOW_CITATION_RESULT_KEY in response

    def _without_user_citations(self, outputs: Any) -> Any:
        if (
            not isinstance(outputs, dict)
            or not getattr(self, "_user_citations_attached", False)
            or WORKFLOW_CITATION_RESULT_KEY not in outputs
        ):
            return outputs
        durable_outputs = dict(outputs)
        durable_outputs.pop(WORKFLOW_CITATION_RESULT_KEY, None)
        return durable_outputs

    def _extract_node_options(self, node_schema) -> Dict[str, Any]:
        """노드 설정을 process_data용 스냅샷으로 추출합니다."""
        try:
            data = dict(node_schema.data) if node_schema.data else {}

            return {
                "node_options": data,
                "node_title": data.get("title", ""),
            }
        except Exception:
            return {}
