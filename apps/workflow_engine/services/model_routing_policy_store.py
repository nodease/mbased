"""모델 라우팅 policy의 DB persistence와 배포 run 완료 훅을 담당한다."""

import uuid
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from apps.shared.db.models.model_routing_policy import (
    LLMNodeModelRoutingPolicy,
    LLMNodeModelRoutingLearningLabel,
    LLMNodeModelRoutingPolicyRunEvent,
    LLMNodeModelRoutingPolicyUpdate,
)
from apps.shared.db.models.app import App
from apps.shared.db.models.llm import LLMModel, LLMUsageLog
from apps.shared.db.models.workflow_deployment import WorkflowDeployment
from apps.shared.db.models.workflow_run import (
    NodeRunStatus,
    RunStatus,
    WorkflowNodeRun,
    WorkflowRun,
)
from apps.workflow_engine.services.model_routing_policy_lifecycle import (
    ModelRoutingPolicyLifecycleService,
)
from apps.workflow_engine.services.model_routing_learner_store import (
    ModelRoutingLearnerStore,
)
from apps.workflow_engine.services.model_routing_bootstrap import (
    downstream_contract_from_graph,
)
from apps.workflow_engine.services.model_routing_judge_first_policy import (
    JUDGE_FIRST_STRATEGY_ID,
    build_judge_first_active_policy,
)
from apps.workflow_engine.services.model_routing_operational_performance import (
    ModelRoutingOperationalPerformanceService,
)
from apps.workflow_engine.services.llm_service import LLMService
from apps.shared.services.model_routing_model_filter import (
    filter_model_routing_available_model_ids,
    filter_supported_model_routing_candidates,
    normalize_model_routing_model_id,
)

class ModelRoutingRunLogPendingError(RuntimeError):
    """Workflow는 끝났지만 자동 라우팅 node 완료 로그가 아직 반영되지 않았다."""


class ModelRoutingPolicyStore:
    """정책 row 조회와 운영 run 누적을 한 곳에서 일관되게 처리한다."""

    @staticmethod
    def _refresh_every_runs(node_data: dict[str, Any]) -> int:
        policy = node_data.get("model_routing_policy")
        refresh = policy.get("refresh") if isinstance(policy, dict) else None
        value = refresh.get("refresh_every_runs") if isinstance(refresh, dict) else 20
        try:
            return max(5, min(100, int(value)))
        except (TypeError, ValueError):
            return 20

    @staticmethod
    def _validation_budget_usd(node_data: dict[str, Any]) -> Decimal:
        """배포 snapshot에 저장된 월간 검증 예산을 안전한 범위로 정규화한다."""
        policy = node_data.get("model_routing_policy")
        raw_value = (
            policy.get("validation_budget_usd")
            if isinstance(policy, dict)
            else Decimal("3")
        )
        try:
            value = Decimal(str(raw_value))
        except (InvalidOperation, TypeError, ValueError):
            value = Decimal("3")
        return max(Decimal("0.5"), min(Decimal("10"), value))

    @staticmethod
    def queue_runtime_judge_label(
        db: Session,
        *,
        learner_id: str | uuid.UUID,
        source_policy_id: str | uuid.UUID | None,
        workflow_run_id: str | uuid.UUID,
        node_id: str,
        routing_feature_text: str,
        learning_feature_text: str,
        selected_model_id: str,
        candidate_model_ids: list[str],
        confidence: float,
        reason_code: str,
        task_requirements: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """호환 진입점이며 실제 학습 데이터는 learner가 소유한다."""

        return ModelRoutingLearnerStore.queue_runtime_judge_label(
            db,
            learner_id=learner_id,
            source_policy_id=source_policy_id,
            workflow_run_id=workflow_run_id,
            node_id=node_id,
            routing_feature_text=routing_feature_text,
            learning_feature_text=learning_feature_text,
            selected_model_id=selected_model_id,
            candidate_model_ids=candidate_model_ids,
            confidence=confidence,
            reason_code=reason_code,
            task_requirements=task_requirements,
        )

    @classmethod
    def pending_learning_learner_ids_for_run(
        cls,
        db: Session,
        *,
        workflow_run_id: str | uuid.UUID,
    ) -> list[uuid.UUID]:
        """완료 훅에서 확정됐지만 아직 비동기 학습하지 않은 학습기를 찾는다."""

        rows = (
            db.query(LLMNodeModelRoutingLearningLabel.learner_id)
            .filter(
                LLMNodeModelRoutingLearningLabel.workflow_run_id
                == uuid.UUID(str(workflow_run_id))
            )
            .filter(
                LLMNodeModelRoutingLearningLabel.status.in_(("accepted", "rejected"))
            )
            .filter(
                LLMNodeModelRoutingLearningLabel.learning_processed_at.is_(None)
            )
            .distinct()
            .all()
        )
        return [row[0] for row in rows]

    @staticmethod
    def _is_routing_evidence_eligible_node_run(node_run: WorkflowNodeRun) -> bool:
        """실제 모델 응답이 없는 안전 RAG 응답은 routing 증거에서 제외한다.

        RAG가 검색 오류나 근거 부족으로 ``safe_no_result``를 반환하면 LLM node는
        workflow를 안전하게 계속 진행하기 위해 SUCCESS로 끝날 수 있다. 그러나 이
        경로는 provider 모델을 호출하지 않았으므로 모델별 비용·품질·지연의 운영
        증거로 사용할 수 없다. ``failure_policy``는 실패 여부가 아니라 노드 설정값
        이므로, 실제 근거 부족을 뜻하는 ``evidence_sufficient=False``도 함께 확인한다.
        """
        outputs = getattr(node_run, "outputs", None)
        output_metadata = outputs.get("metadata") if isinstance(outputs, dict) else None
        trace_metadata = getattr(node_run, "trace_metadata", None)
        rag_metadata_candidates = (
            output_metadata.get("rag") if isinstance(output_metadata, dict) else None,
            trace_metadata.get("rag") if isinstance(trace_metadata, dict) else None,
        )
        return not any(
            isinstance(rag_metadata, dict)
            and rag_metadata.get("failure_policy") == "safe_no_result"
            and rag_metadata.get("evidence_sufficient") is False
            for rag_metadata in rag_metadata_candidates
        )

    @classmethod
    def get_runtime_policy(
        cls,
        db: Session,
        *,
        workflow_id: str | uuid.UUID | None,
        deployment_id: str | uuid.UUID | None,
        node_id: str,
    ) -> LLMNodeModelRoutingPolicy | None:
        if not workflow_id or not deployment_id:
            return None
        try:
            workflow_uuid = uuid.UUID(str(workflow_id))
            deployment_uuid = uuid.UUID(str(deployment_id))
        except (TypeError, ValueError):
            return None
        return (
            db.query(LLMNodeModelRoutingPolicy)
            .filter(LLMNodeModelRoutingPolicy.workflow_id == workflow_uuid)
            .filter(LLMNodeModelRoutingPolicy.deployment_id == deployment_uuid)
            .filter(LLMNodeModelRoutingPolicy.node_id == node_id)
            .first()
        )

    @classmethod
    def ensure_policy_for_deployed_node(
        cls,
        db: Session,
        *,
        workflow_run: WorkflowRun,
        node_id: str,
        node_data: dict[str, Any],
    ) -> LLMNodeModelRoutingPolicy | None:
        if not bool(node_data.get("auto_model_routing")):
            return None
        if workflow_run.deployment_id is None:
            return None

        organization_id = cls._organization_id_for_run(db, workflow_run)
        deployment = (
            db.query(WorkflowDeployment)
            .filter(WorkflowDeployment.id == workflow_run.deployment_id)
            .first()
        )
        graph_snapshot = deployment.graph_snapshot if deployment is not None else {}
        policy = cls._ensure_policy(
            db,
            workflow_id=workflow_run.workflow_id,
            deployment_id=workflow_run.deployment_id,
            organization_id=organization_id,
            execution_subject_user_id=getattr(workflow_run, "user_id", None),
            node_id=node_id,
            node_data=node_data,
            downstream_contract=downstream_contract_from_graph(
                graph_snapshot, node_id
            ),
        )
        return policy

    @classmethod
    def ensure_policies_for_deployment(
        cls,
        db: Session,
        *,
        workflow_id: uuid.UUID,
        deployment_id: uuid.UUID,
        organization_id: uuid.UUID,
        execution_subject_user_id: uuid.UUID,
        graph_snapshot: dict[str, Any],
    ) -> list[LLMNodeModelRoutingPolicy]:
        """첫 운영 실행 전에 배포 snapshot의 기본 정책을 DB에 만든다."""
        policies: list[LLMNodeModelRoutingPolicy] = []
        nodes = graph_snapshot.get("nodes") if isinstance(graph_snapshot, dict) else []
        for node in nodes if isinstance(nodes, list) else []:
            if not isinstance(node, dict):
                continue
            node_id = str(node.get("id") or "").strip()
            node_data = node.get("data") if isinstance(node.get("data"), dict) else None
            if not node_id or not isinstance(node_data, dict):
                continue
            if str(node.get("type") or "") != "llmNode":
                continue
            if not bool(node_data.get("auto_model_routing")):
                continue
            existing = cls.get_runtime_policy(
                db,
                workflow_id=workflow_id,
                deployment_id=deployment_id,
                node_id=node_id,
            )
            policy = cls._ensure_policy(
                db,
                workflow_id=workflow_id,
                deployment_id=deployment_id,
                organization_id=organization_id,
                execution_subject_user_id=execution_subject_user_id,
                node_id=node_id,
                node_data=node_data,
                downstream_contract=downstream_contract_from_graph(
                    graph_snapshot, node_id
                ),
            )
            if policy is not None and existing is None:
                policies.append(policy)
        return policies

    @classmethod
    def _ensure_policy(
        cls,
        db: Session,
        *,
        workflow_id: uuid.UUID,
        deployment_id: uuid.UUID,
        organization_id: uuid.UUID | None,
        execution_subject_user_id: uuid.UUID | None,
        node_id: str,
        node_data: dict[str, Any],
        downstream_contract: dict[str, Any] | None = None,
    ) -> LLMNodeModelRoutingPolicy | None:
        existing = cls.get_runtime_policy(
            db,
            workflow_id=workflow_id,
            deployment_id=deployment_id,
            node_id=node_id,
        )
        if existing is not None:
            if existing.learner_id is None and organization_id is not None:
                learner = ModelRoutingLearnerStore.get_or_create(
                    db,
                    organization_id=organization_id,
                    workflow_id=workflow_id,
                    node_id=node_id,
                    node_data=node_data,
                    downstream_contract=downstream_contract,
                )
                version = ModelRoutingLearnerStore.latest_version(
                    db, learner_id=learner.id
                )
                existing.learner_id = learner.id
                existing.active_learner_version_id = (
                    version.id if version is not None else None
                )
            return existing

        policy_id = uuid.uuid4()
        refresh_every_runs = cls._refresh_every_runs(node_data)
        validation_budget_usd = cls._validation_budget_usd(node_data)
        configured_model_id = str(node_data.get("model_id") or "").strip()
        if not configured_model_id:
            # 실행 모델이 없는 잘못된 deployment snapshot은 policy를 만들지 않는다.
            # 이후 runtime도 저장 모델을 임의로 추정하지 않고 기존 validation 경로에서 막는다.
            return None
        if organization_id is None or execution_subject_user_id is None:
            return None
        learner = ModelRoutingLearnerStore.get_or_create(
            db,
            organization_id=organization_id,
            workflow_id=workflow_id,
            node_id=node_id,
            node_data=node_data,
            downstream_contract=downstream_contract,
        )
        learner_version = ModelRoutingLearnerStore.latest_version(
            db, learner_id=learner.id
        )
        available_model_ids = filter_supported_model_routing_candidates(
            filter_model_routing_available_model_ids(
                LLMService.get_runtime_available_model_ids_for_user(
                    db,
                    user_id=execution_subject_user_id,
                    organization_id=organization_id,
                ),
                node_data=node_data,
            )
        )
        available_by_normalized_id = {
            normalize_model_routing_model_id(model_id): model_id
            for model_id in available_model_ids
            if normalize_model_routing_model_id(model_id)
        }
        configured_model_id = available_by_normalized_id.get(
            normalize_model_routing_model_id(configured_model_id)
        )
        if configured_model_id is None:
            # bootstrap policy가 실행 주체에게 사용할 수 없는 모델을 active로 만들면
            # policy runtime의 credential guard보다 먼저 잘못된 상태를 저장하게 된다.
            return None
        fallback_model_id = available_by_normalized_id.get(
            normalize_model_routing_model_id(node_data.get("fallback_model_id"))
        )
        policy_version = "deployment-judge-first-v2"
        bootstrap_active_policy = build_judge_first_active_policy(
            policy_version=policy_version,
            default_model_id=configured_model_id,
            fallback_model_id=fallback_model_id,
            candidate_model_ids=available_model_ids,
        )
        update_summary = {
            "strategy_id": JUDGE_FIRST_STRATEGY_ID,
            "policy_source": "deployment_runtime",
        }

        policy = LLMNodeModelRoutingPolicy(
            id=policy_id,
            organization_id=organization_id,
            workflow_id=workflow_id,
            deployment_id=deployment_id,
            node_id=node_id,
            enabled=True,
            status="active",
            policy_version=policy_version,
            active_policy=bootstrap_active_policy,
            bootstrap_id=None,
            learner_id=learner.id,
            active_learner_version_id=(
                learner_version.id if learner_version is not None else None
            ),
            performance_checkpoint={},
            refresh_every_runs=refresh_every_runs,
            judge_user_id=execution_subject_user_id,
            execution_subject_user_id=execution_subject_user_id,
            validation_budget_usd=validation_budget_usd,
        )
        try:
            with db.begin_nested():
                db.add(policy)
                db.flush()
                db.add(
                    LLMNodeModelRoutingPolicyUpdate(
                        policy_id=policy.id,
                        trigger="deployment_bootstrap",
                        status="applied",
                        eligible_run_count=0,
                        input_summary={
                            "available_model_count": len(available_model_ids),
                        },
                        output_summary={
                            "reason": "배포 시점에 첫 실행용 정책을 계산했습니다.",
                            "bootstrap": update_summary,
                        },
                        new_policy_version=policy_version,
                    )
                )
                db.flush()
        except IntegrityError:
            return cls.get_runtime_policy(
                db,
                workflow_id=workflow_id,
                deployment_id=deployment_id,
                node_id=node_id,
            )
        return policy

    @classmethod
    def _lock_policy_for_update(
        cls,
        db: Session,
        *,
        policy_id: uuid.UUID,
    ) -> LLMNodeModelRoutingPolicy | None:
        return (
            db.query(LLMNodeModelRoutingPolicy)
            .populate_existing()
            .filter(LLMNodeModelRoutingPolicy.id == policy_id)
            .with_for_update()
            .first()
        )

    @classmethod
    def claim_pending_auto_refresh(
        cls,
        db: Session,
        *,
        policy_id: str | uuid.UUID,
    ) -> LLMNodeModelRoutingPolicy | None:
        """동일 auto refresh 메시지 중 아직 처리할 요청 하나만 transaction 안에서 통과시킨다."""
        try:
            policy_uuid = uuid.UUID(str(policy_id))
        except (TypeError, ValueError):
            return None
        policy = cls._lock_policy_for_update(db, policy_id=policy_uuid)
        if not ModelRoutingPolicyLifecycleService.has_pending_refresh_request(policy):
            return None

        # 동일 refresh 요청은 task 재전달로 여러 번 들어올 수 있다. policy 행을
        # 잠근 뒤, 이 요청 시각 이후 생성된 update가 있으면 이미 다른 worker가
        # refresh를 시작한 것이므로 중복 Judge/Replay를 막는다. 첫 worker가 commit
        # 전에 실패하면 update도 남지 않아 Celery 재시도가 정상적으로 다시 claim한다.
        refresh_requested_at = policy.refresh_requested_at
        refresh_already_started = (
            db.query(LLMNodeModelRoutingPolicyUpdate.id)
            .filter(LLMNodeModelRoutingPolicyUpdate.policy_id == policy.id)
            .filter(LLMNodeModelRoutingPolicyUpdate.created_at >= refresh_requested_at)
            .first()
        )
        if refresh_already_started is not None:
            return None
        return policy

    @staticmethod
    def _organization_id_for_run(db: Session, workflow_run: WorkflowRun):
        deployment = (
            db.query(WorkflowDeployment)
            .filter(WorkflowDeployment.id == workflow_run.deployment_id)
            .first()
        )
        if deployment is None:
            return None
        app = db.query(App).filter(App.id == deployment.app_id).first()
        return app.organization_id if app is not None else None

    @classmethod
    def record_completed_deployed_run(
        cls,
        db: Session,
        *,
        workflow_run_id: str | uuid.UUID,
    ) -> list[uuid.UUID]:
        """완료된 배포 run의 auto-routing LLM node를 카운트하고 예약할 policy id를 반환한다."""
        workflow_run = (
            db.query(WorkflowRun)
            .filter(WorkflowRun.id == uuid.UUID(str(workflow_run_id)))
            .first()
        )
        if (
            workflow_run is None
            or not ModelRoutingPolicyLifecycleService.is_eligible_operational_run(
                workflow_run
            )
        ):
            return []

        deployment = (
            db.query(WorkflowDeployment)
            .filter(WorkflowDeployment.id == workflow_run.deployment_id)
            .first()
        )
        graph = deployment.graph_snapshot if deployment is not None else {}
        node_data_by_id = {
            str(node.get("id")): node.get("data")
            for node in (graph.get("nodes") or [])
            if isinstance(node, dict) and isinstance(node.get("data"), dict)
        }
        auto_routing_node_ids = {
            node_id
            for node_id, node_data in node_data_by_id.items()
            if bool(node_data.get("auto_model_routing"))
        }
        if not auto_routing_node_ids:
            return []
        node_runs = (
            db.query(WorkflowNodeRun)
            .filter(WorkflowNodeRun.workflow_run_id == workflow_run.id)
            .filter(WorkflowNodeRun.node_type == "llmNode")
            .filter(WorkflowNodeRun.node_id.in_(auto_routing_node_ids))
            .all()
        )
        node_runs_by_id = {str(node_run.node_id): node_run for node_run in node_runs}
        workflow_status = getattr(workflow_run.status, "value", workflow_run.status)
        if workflow_status == RunStatus.SUCCESS.value:
            pending_node_ids = {
                node_id
                for node_id in auto_routing_node_ids
                if node_id in node_runs_by_id
                and node_runs_by_id[node_id].status == NodeRunStatus.RUNNING
            }
            if pending_node_ids:
                raise ModelRoutingRunLogPendingError(
                    "auto-routing node completion logs are not ready"
                )

        evidence_node_runs = [
            node_run
            for node_run in node_runs
            if node_run.status in (NodeRunStatus.SUCCESS, NodeRunStatus.FAILED)
            and cls._is_routing_evidence_eligible_node_run(node_run)
        ]
        scheduled: list[uuid.UUID] = []
        for node_run in sorted(
            evidence_node_runs,
            key=lambda item: str(item.node_id),
        ):
            node_data = node_data_by_id.get(node_run.node_id)
            if not isinstance(node_data, dict):
                continue
            if not bool(node_data.get("auto_model_routing")):
                # 현재 draft가 아니라 deployment snapshot을 기준으로 집계한다.
                # 자동 라우팅이 포함되지 않은 배포의 과거 run은 policy/event를 만들지 않는다.
                continue
            policy = cls.ensure_policy_for_deployed_node(
                db,
                workflow_run=workflow_run,
                node_id=node_run.node_id,
                node_data=node_data,
            )
            if policy is None:
                continue
            event_was_created = cls._record_policy_event(
                db,
                policy_id=policy.id,
                workflow_run_id=workflow_run.id,
            )
            # 동시 run은 event insert 뒤 같은 순서로 policy row를 잠근다. lock을
            # 얻은 시점의 최신 counter에만 event를 반영해 누락 갱신을 막는다.
            policy = cls._lock_policy_for_update(db, policy_id=policy.id)
            if policy is None:
                continue
            performance_changed = False
            if event_was_created and hasattr(policy, "performance_checkpoint"):
                usage_row = (
                    db.query(LLMUsageLog, LLMModel)
                    .join(LLMModel, LLMModel.id == LLMUsageLog.model_id)
                    .filter(LLMUsageLog.workflow_run_id == workflow_run.id)
                    .filter(LLMUsageLog.node_id == node_run.node_id)
                    .filter(LLMUsageLog.cost_optimizer_candidate_id.is_(None))
                    .order_by(LLMUsageLog.created_at.desc())
                    .first()
                )
                usage_log = usage_row[0] if usage_row is not None else None
                usage_model_id = (
                    usage_row[1].model_id_for_api_call
                    if usage_row is not None
                    else None
                )
                try:
                    sample = ModelRoutingOperationalPerformanceService.sample_from_run(
                        workflow_run=workflow_run,
                        node_run=node_run,
                        usage_log=usage_log,
                        usage_model_id=usage_model_id,
                    )
                    ModelRoutingOperationalPerformanceService.record_sample(
                        db,
                        policy_id=policy.id,
                        sample=sample,
                    )
                    current_performance = (
                        ModelRoutingOperationalPerformanceService.checkpoint_snapshot(
                            db,
                            policy_id=policy.id,
                        )
                    )
                    performance_changed = (
                        ModelRoutingOperationalPerformanceService.has_material_change(
                            policy.performance_checkpoint,
                            current_performance,
                        )
                    )
                except ValueError:
                    # provider 호출 전 실패처럼 모델을 식별할 수 없는 실행은
                    # event 이력만 남기고 모델 성적에는 귀속하지 않는다.
                    performance_changed = False
                # 모델 사용량을 복원하지 못한 실패도 학습하면 안 된다. 성적 누계와
                # 별개로 대기 label은 항상 이번 node의 계약 결과로 확정한다.
                if policy.learner_id is not None:
                    ModelRoutingLearnerStore.finalize_labels(
                        db,
                        learner_id=policy.learner_id,
                        workflow_run=workflow_run,
                        node_run=node_run,
                    )
            outcome = ModelRoutingPolicyLifecycleService.apply_run_event(
                policy,
                event_was_created=event_was_created,
                performance_changed=performance_changed,
            )
            if outcome.should_enqueue_refresh or (
                not event_was_created
                and ModelRoutingPolicyLifecycleService.has_pending_refresh_request(
                    policy
                )
            ):
                scheduled.append(policy.id)
        db.flush()
        return scheduled

    @staticmethod
    def _record_policy_event(
        db: Session,
        *,
        policy_id: uuid.UUID,
        workflow_run_id: uuid.UUID,
    ) -> bool:
        try:
            with db.begin_nested():
                db.add(
                    LLMNodeModelRoutingPolicyRunEvent(
                        policy_id=policy_id,
                        workflow_run_id=workflow_run_id,
                    )
                )
                db.flush()
            return True
        except IntegrityError:
            return False
