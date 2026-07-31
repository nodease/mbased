from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass, replace

from apps.shared.domain.knowledge_runtime_candidates import (
    MAX_RUNTIME_CANDIDATE_BUDGET,
)
from apps.shared.domain.workflow_knowledge_references import (
    WorkflowKnowledgeReferenceError,
    parse_llm_knowledge_references,
    parse_workflow_knowledge_references,
)
from apps.shared.domain.workflow_node_binding import (
    WorkflowNodeBinding,
    WorkflowNodeBindingError,
    canonical_snapshot_sha256,
    parse_workflow_node_bindings,
)
from apps.shared.domain.workflow_node_location import iter_workflow_node_locations
from apps.shared.domain.workflow_graph import (
    WorkflowGraphValidationError,
    validate_workflow_graph,
)

from .errors import DeploymentPreflightBlocked
from .models import (
    DeploymentPreflightResult,
    KnowledgeCollectionPreflightSnapshot,
    NodeCatalogSnapshot,
    PreflightAudience,
    PreflightNodeResult,
    PreflightRequiredAction,
    PreflightStatus,
    PreflightSummary,
)
from .node_configuration import NodeConfigurationEvaluator
from .ports import DeploymentPreflightRepository, PermissionDenialAuditPort

PUBLIC_REQUIRED_ACTIONS = {
    "knowledge_reference_invalid": PreflightRequiredAction(
        action="fix_invalid_knowledge_references",
        label="잘못된 지식 참조 형식을 수정하세요",
    ),
    "knowledge_reference_limit_exceeded": PreflightRequiredAction(
        action="reduce_knowledge_references",
        label="LLM 노드의 지식 참조 수를 허용 범위로 줄이세요",
    ),
    "private_kb_requires_execution_subject": PreflightRequiredAction(
        action="remove_private_kb_or_use_authenticated_run",
        label="Private KB를 제거하거나 인증 실행 경로를 사용하세요",
    ),
    "source_public_exposure_required": PreflightRequiredAction(
        action="approve_source_public_exposure_or_remove_reference",
        label="Source 공개 승인 정책을 추가하거나 해당 지식 참조를 제거하세요",
    ),
    "knowledge_base_unavailable": PreflightRequiredAction(
        action="remove_unavailable_kb_reference",
        label="사용할 수 없는 KB 참조를 제거하세요",
    ),
    "private_collection_requires_execution_subject": PreflightRequiredAction(
        action="remove_private_collection_or_use_authenticated_run",
        label="Private Collection을 제거하거나 인증 실행 경로를 사용하세요",
    ),
    "knowledge_collection_unavailable": PreflightRequiredAction(
        action="remove_unavailable_collection_reference",
        label="사용할 수 없는 Collection 참조를 제거하세요",
    ),
    "workflow_node_target_unavailable": PreflightRequiredAction(
        action="fix_workflow_node_target_or_remove_reference",
        label="서브 모듈 대상 배포를 복구하거나 노드를 제거하세요",
    ),
    "workflow_node_cycle_detected": PreflightRequiredAction(
        action="remove_recursive_workflow_node_reference",
        label="순환되는 서브 모듈 참조를 제거하세요",
    ),
    "node_configuration_unresolved": PreflightRequiredAction(
        action="complete_node_configuration",
        label="외부 연동 노드의 필수 설정을 완료하세요",
    ),
    "node_configuration_invalid": PreflightRequiredAction(
        action="fix_node_configuration",
        label="외부 연동 노드의 설정을 수정하세요",
    ),
    "mail_credential_unavailable": PreflightRequiredAction(
        action="select_available_mail_credential",
        label="사용 가능한 Mail credential을 선택하세요",
    ),
    "mail_execution_subject_required": PreflightRequiredAction(
        action="use_authenticated_execution_surface",
        label="사용자 실행 주체가 있는 인증 실행 경로를 사용하세요",
    ),
    "node_configuration_validator_unavailable": PreflightRequiredAction(
        action="remove_or_update_unsupported_node",
        label="지원되지 않는 외부 연동 노드를 제거하거나 갱신하세요",
    ),
    "workflow_graph_invalid": PreflightRequiredAction(
        action="fix_workflow_graph",
        label="워크플로우 그래프 구조를 수정하세요",
    ),
}

WARNING_REQUIRED_ACTIONS = {
    "workflow_node_execution_subject_inherited": PreflightRequiredAction(
        action="verify_parent_execution_subject",
        label="상위 워크플로우 실행 주체가 KB 권한을 제공하는지 확인하세요",
    ),
    "knowledge_candidate_budget_limited": PreflightRequiredAction(
        action="review_knowledge_candidate_selection",
        label="실행 후보 제한을 고려해 KB와 Collection 선택을 검토하세요",
    ),
    "mail_execution_subject_inherited": PreflightRequiredAction(
        action="verify_parent_execution_subject",
        label="상위 워크플로우 실행 주체가 Mail 권한을 제공하는지 확인하세요",
    ),
}

NON_DOWNGRADABLE_REASON_CODES = {
    "knowledge_reference_invalid",
    "knowledge_reference_limit_exceeded",
    "workflow_node_target_unavailable",
    "workflow_node_cycle_detected",
    "node_configuration_validator_unavailable",
    "node_configuration_invalid",
    "workflow_graph_invalid",
    "mail_credential_unavailable",
}

ANONYMOUS_PUBLIC_TYPES = {
    "api",
    "webapp",
    "widget",
    "chatbot",
    "mcp",
    "schedule",
    "webhook",
}


@dataclass(frozen=True)
class _PreflightIssue:
    node_id: str | None
    node_type: str
    severity: PreflightStatus
    reason_code: str
    knowledge_base_count: int = 0
    knowledge_collection_count: int = 0
    candidate_budget_limited: bool = False
    permission_resource_id: uuid.UUID | None = None
    permission_effective_auth_state: str | None = None


class DeploymentPreflightUseCase:
    MAX_WORKFLOW_NODE_DEPTH = 3

    def __init__(
        self,
        repository: DeploymentPreflightRepository,
        *,
        organization_id: uuid.UUID | None,
        principal_id: uuid.UUID | None = None,
        node_catalog_by_type: Mapping[str, NodeCatalogSnapshot] | None = None,
        candidate_graphs_by_app_id: Mapping[uuid.UUID, dict] | None = None,
        candidate_deployment_types_by_app_id: Mapping[uuid.UUID, str] | None = None,
        permission_denial_audit: PermissionDenialAuditPort | None = None,
    ) -> None:
        self.repository = repository
        self.organization_id = organization_id
        self.principal_id = principal_id
        self.permission_denial_audit = permission_denial_audit
        self.node_configuration_evaluator = (
            NodeConfigurationEvaluator(
                repository,
                organization_id=organization_id,
                principal_id=principal_id,
                node_catalog_by_type=node_catalog_by_type,
            )
            if node_catalog_by_type is not None
            else None
        )
        self.candidate_graphs_by_app_id = dict(candidate_graphs_by_app_id or {})
        self.candidate_deployment_types_by_app_id = dict(
            candidate_deployment_types_by_app_id or {}
        )

    def preview(
        self,
        *,
        deployment_type: str,
        graph_snapshot: dict,
        audience_hint: PreflightAudience | None = None,
        is_active: bool = True,
        trusted_audience_override: PreflightAudience | None = None,
    ) -> DeploymentPreflightResult:
        audience = self._effective_audience(
            deployment_type,
            audience_hint,
            trusted_audience_override,
        )
        return self._preview_for_audience(
            graph_snapshot=graph_snapshot,
            audience=audience,
            is_active=is_active,
        )

    def _preview_for_audience(
        self,
        *,
        graph_snapshot: dict,
        audience: PreflightAudience,
        is_active: bool,
        record_permission_denials: bool = False,
    ) -> DeploymentPreflightResult:
        issues = self._evaluate_graph(
            graph_snapshot,
            audience=audience,
            depth=0,
            visited_app_ids=set(self.candidate_graphs_by_app_id),
        )
        if not is_active:
            issues = self._downgrade_blocked_issues(issues)
        if record_permission_denials:
            self._record_permission_denials(issues)
        return self._result(audience, issues)

    def enforce_active_publish(
        self,
        *,
        deployment_type: str,
        graph_snapshot: dict,
    ) -> DeploymentPreflightResult:
        result = self._preview_for_audience(
            graph_snapshot=graph_snapshot,
            audience=self._effective_audience(deployment_type, None),
            is_active=True,
            record_permission_denials=True,
        )
        if result.status == "blocked":
            raise DeploymentPreflightBlocked(result)
        return result

    def enforce_inactive_save(
        self,
        *,
        deployment_type: str,
        graph_snapshot: dict,
    ) -> DeploymentPreflightResult:
        result = self._preview_for_audience(
            graph_snapshot=graph_snapshot,
            audience=self._effective_audience(deployment_type, None),
            is_active=False,
            record_permission_denials=True,
        )
        if result.status == "blocked":
            raise DeploymentPreflightBlocked(result)
        return result

    def enforce_authenticated_run(
        self,
        *,
        graph_snapshot: dict,
    ) -> DeploymentPreflightResult:
        result = self._preview_for_audience(
            graph_snapshot=graph_snapshot,
            audience="authenticated_user",
            is_active=True,
            record_permission_denials=True,
        )
        if result.status == "blocked":
            raise DeploymentPreflightBlocked(result)
        return result

    def enforce_schedule_dispatch(
        self,
        *,
        graph_snapshot: dict,
    ) -> DeploymentPreflightResult:
        result = self._preview_for_audience(
            graph_snapshot=graph_snapshot,
            audience="anonymous_public",
            is_active=True,
        )
        if result.status == "blocked":
            raise DeploymentPreflightBlocked(result)
        return result

    @staticmethod
    def server_derived_audience(deployment_type: str) -> PreflightAudience:
        if deployment_type == "internal_chatbot":
            return "authenticated_user"
        if deployment_type == "workflow_node":
            return "workflow_node_inherited"
        if deployment_type in ANONYMOUS_PUBLIC_TYPES:
            return "anonymous_public"
        return "anonymous_public"

    def _effective_audience(
        self,
        deployment_type: str,
        audience_hint: PreflightAudience | None,
        trusted_audience_override: PreflightAudience | None = None,
    ) -> PreflightAudience:
        if trusted_audience_override == "authenticated_user":
            return "authenticated_user"
        derived = self.server_derived_audience(deployment_type)
        if audience_hint == "anonymous_public":
            return "anonymous_public"
        return derived

    def _evaluate_graph(
        self,
        graph_snapshot: dict,
        *,
        audience: PreflightAudience,
        depth: int,
        visited_app_ids: set[uuid.UUID],
    ) -> list[_PreflightIssue]:
        issues: list[_PreflightIssue] = []
        try:
            validate_workflow_graph(graph_snapshot)
        except WorkflowGraphValidationError:
            return [
                _PreflightIssue(
                    node_id=None,
                    node_type="unknown",
                    severity="blocked",
                    reason_code="workflow_graph_invalid",
                )
            ]
        configuration_issue = self._graph_configuration_issue(graph_snapshot)
        if configuration_issue is not None:
            return [configuration_issue]
        if self.node_configuration_evaluator is not None:
            issues.extend(
                _PreflightIssue(
                    node_id=issue.node_id,
                    node_type=issue.node_type,
                    severity=issue.severity,
                    reason_code=issue.reason_code,
                    permission_resource_id=issue.permission_resource_id,
                    permission_effective_auth_state=(
                        issue.permission_effective_auth_state
                    ),
                )
                for issue in self.node_configuration_evaluator.evaluate(
                    graph_snapshot,
                    audience=audience,
                )
            )
        if not isinstance(graph_snapshot, dict):
            return issues
        nodes = graph_snapshot.get("nodes")
        if not isinstance(nodes, list):
            return issues

        try:
            parsed_bindings = parse_workflow_node_bindings(graph_snapshot)
        except WorkflowNodeBindingError:
            issues.append(
                _PreflightIssue(
                    node_id=None,
                    node_type="workflowNode",
                    severity="blocked",
                    reason_code="workflow_graph_invalid",
                )
            )
            return issues
        bindings_by_reference = {
            (binding.container_path, binding.workflow_node_id): binding
            for binding in parsed_bindings or ()
        }

        for node, container_path in self._iter_graph_nodes(graph_snapshot):
            if not isinstance(node, dict):
                continue
            node_id = self._safe_node_id(node)
            node_type = self._safe_node_type(node)
            data = node.get("data")
            if not isinstance(data, dict):
                data = {}

            if node_type == "llmNode":
                try:
                    references = parse_llm_knowledge_references(data)
                except WorkflowKnowledgeReferenceError as exc:
                    reason_code = (
                        "knowledge_reference_limit_exceeded"
                        if exc.reason_code == "knowledge_reference_limit_exceeded"
                        else "knowledge_reference_invalid"
                    )
                    issues.append(
                        _PreflightIssue(
                            node_id=node_id,
                            node_type=node_type,
                            severity="blocked",
                            reason_code=reason_code,
                        )
                    )
                    continue

                direct_ids = list(references.direct_kb_ids)
                collection_ids = list(references.collection_ids)
                if direct_ids:
                    issues.extend(
                        self._evaluate_kb_references(
                            direct_ids,
                            node_id=node_id,
                            node_type=node_type,
                            audience=audience,
                        )
                    )
                collection_snapshots: Mapping[
                    uuid.UUID, KnowledgeCollectionPreflightSnapshot
                ] = {}
                if collection_ids:
                    collection_issues, collection_snapshots = (
                        self._evaluate_collection_references(
                            collection_ids,
                            node_id=node_id,
                            node_type=node_type,
                            audience=audience,
                        )
                    )
                    issues.extend(collection_issues)
                if self._candidate_budget_may_be_limited(
                    direct_ids,
                    collection_snapshots,
                ):
                    issues.append(
                        _PreflightIssue(
                            node_id=node_id,
                            node_type=node_type,
                            severity="warning",
                            reason_code="knowledge_candidate_budget_limited",
                            knowledge_collection_count=len(collection_snapshots),
                            candidate_budget_limited=True,
                        )
                    )

            if node_type == "workflowNode":
                issues.extend(
                    self._evaluate_workflow_node_target(
                        data,
                        node_id=node_id,
                        node_type=node_type,
                        audience=audience,
                        depth=depth,
                        visited_app_ids=visited_app_ids,
                        binding=bindings_by_reference.get(
                            (container_path, str(node.get("id") or ""))
                        ),
                    )
                )
        return issues

    def _record_permission_denials(self, issues: list[_PreflightIssue]) -> None:
        if (
            self.permission_denial_audit is None
            or self.principal_id is None
            or self.organization_id is None
        ):
            return
        recorded_ids: set[uuid.UUID] = set()
        for issue in issues:
            credential_id = issue.permission_resource_id
            effective_auth_state = issue.permission_effective_auth_state
            if (
                credential_id is None
                or effective_auth_state is None
                or credential_id in recorded_ids
            ):
                continue
            recorded_ids.add(credential_id)
            self.permission_denial_audit.record_mail_credential_use_denied(
                principal_id=self.principal_id,
                organization_id=self.organization_id,
                credential_id=credential_id,
                effective_auth_state=effective_auth_state,
            )

    def _evaluate_kb_references(
        self,
        kb_ids: list[uuid.UUID],
        *,
        node_id: str | None,
        node_type: str,
        audience: PreflightAudience,
    ) -> list[_PreflightIssue]:
        kbs_by_id = self.repository.get_active_knowledge_bases(
            kb_ids,
            self.organization_id,
        )
        missing_count = sum(kb_id not in kbs_by_id for kb_id in kb_ids)
        issues: list[_PreflightIssue] = []
        if missing_count:
            issues.append(
                _PreflightIssue(
                    node_id=node_id,
                    node_type=node_type,
                    severity="blocked",
                    reason_code="knowledge_base_unavailable",
                    knowledge_base_count=missing_count,
                )
            )

        active_ids = [kb_id for kb_id in kb_ids if kb_id in kbs_by_id]
        if audience == "workflow_node_inherited":
            if active_ids:
                issues.append(
                    _PreflightIssue(
                        node_id=node_id,
                        node_type=node_type,
                        severity="warning",
                        reason_code="workflow_node_execution_subject_inherited",
                        knowledge_base_count=len(active_ids),
                    )
                )
            return issues
        if audience == "authenticated_user":
            return issues

        public_eligible_ids = (
            self.repository.get_public_runtime_eligible_knowledge_base_ids(
                active_ids,
                self.organization_id,
            )
        )
        issue_counts: dict[str, int] = {}
        for kb_id in active_ids:
            kb = kbs_by_id[kb_id]
            if kb_id not in public_eligible_ids:
                reason_code = "private_kb_requires_execution_subject"
            elif kb.source_managed:
                reason_code = "source_public_exposure_required"
            else:
                continue
            issue_counts[reason_code] = issue_counts.get(reason_code, 0) + 1

        issues.extend(
            [
                _PreflightIssue(
                    node_id=node_id,
                    node_type=node_type,
                    severity="blocked",
                    reason_code=reason_code,
                    knowledge_base_count=count,
                )
                for reason_code, count in sorted(issue_counts.items())
            ]
        )
        return issues

    def _evaluate_collection_references(
        self,
        collection_ids: list[uuid.UUID],
        *,
        node_id: str | None,
        node_type: str,
        audience: PreflightAudience,
    ) -> tuple[
        list[_PreflightIssue],
        Mapping[uuid.UUID, KnowledgeCollectionPreflightSnapshot],
    ]:
        collections_by_id = self.repository.get_active_knowledge_collections(
            collection_ids,
            self.organization_id,
        )
        missing_count = sum(
            collection_id not in collections_by_id
            for collection_id in collection_ids
        )
        issues: list[_PreflightIssue] = []
        if missing_count:
            issues.append(
                _PreflightIssue(
                    node_id=node_id,
                    node_type=node_type,
                    severity="blocked",
                    reason_code="knowledge_collection_unavailable",
                    knowledge_collection_count=missing_count,
                )
            )

        active_ids = [
            collection_id
            for collection_id in collection_ids
            if collection_id in collections_by_id
        ]
        if audience == "workflow_node_inherited":
            if active_ids:
                issues.append(
                    _PreflightIssue(
                        node_id=node_id,
                        node_type=node_type,
                        severity="warning",
                        reason_code="workflow_node_execution_subject_inherited",
                        knowledge_collection_count=len(active_ids),
                    )
                )
            return issues, collections_by_id
        if audience == "authenticated_user":
            return issues, collections_by_id

        issue_counts: dict[str, int] = {}
        for collection_id in active_ids:
            collection = collections_by_id[collection_id]
            if not collection.public:
                reason_code = "private_collection_requires_execution_subject"
            elif (
                collection.source_managed
                or collection.has_source_managed_members
            ):
                reason_code = "source_public_exposure_required"
            else:
                continue
            issue_counts[reason_code] = issue_counts.get(reason_code, 0) + 1
        issues.extend(
            [
                _PreflightIssue(
                    node_id=node_id,
                    node_type=node_type,
                    severity="blocked",
                    reason_code=reason_code,
                    knowledge_collection_count=count,
                )
                for reason_code, count in sorted(issue_counts.items())
            ]
        )
        return issues, collections_by_id

    @staticmethod
    def _candidate_budget_may_be_limited(
        direct_ids: list[uuid.UUID],
        collections_by_id: Mapping[
            uuid.UUID, KnowledgeCollectionPreflightSnapshot
        ],
    ) -> bool:
        conservative_candidate_count = len(direct_ids) + sum(
            max(snapshot.candidate_member_count, 0)
            for snapshot in collections_by_id.values()
        )
        return conservative_candidate_count > MAX_RUNTIME_CANDIDATE_BUDGET

    @staticmethod
    def _graph_configuration_issue(
        graph_snapshot: object,
    ) -> _PreflightIssue | None:
        try:
            parse_workflow_knowledge_references(graph_snapshot)
        except WorkflowKnowledgeReferenceError as exc:
            reason_code = (
                "knowledge_reference_limit_exceeded"
                if exc.reason_code == "knowledge_reference_limit_exceeded"
                else "knowledge_reference_invalid"
            )
            return _PreflightIssue(
                node_id=None,
                node_type="llmNode",
                severity="blocked",
                reason_code=reason_code,
            )
        return None

    def _evaluate_workflow_node_target(
        self,
        data: dict,
        *,
        node_id: str | None,
        node_type: str,
        audience: PreflightAudience,
        depth: int,
        visited_app_ids: set[uuid.UUID],
        binding: WorkflowNodeBinding | None,
    ) -> list[_PreflightIssue]:
        if depth >= self.MAX_WORKFLOW_NODE_DEPTH:
            return [self._workflow_node_unavailable(node_id, node_type)]

        target_app_id = self._uuid_or_none(data.get("appId"))
        if target_app_id is None:
            return [self._workflow_node_unavailable(node_id, node_type)]

        if binding is not None:
            if binding.target_app_id != target_app_id:
                return [self._workflow_node_unavailable(node_id, node_type)]
            target = self.repository.get_workflow_node_deployment(
                target_app_id,
                binding.deployment_id,
                self.organization_id,
            )
            if not self._bound_target_matches(target, binding):
                return [self._workflow_node_unavailable(node_id, node_type)]
        else:
            target = self.repository.get_workflow_node_target(
                target_app_id,
                self.organization_id,
            )
        if target is None:
            return [self._workflow_node_unavailable(node_id, node_type)]

        candidate_graph = self.candidate_graphs_by_app_id.get(target_app_id)
        if candidate_graph is not None:
            if (
                self.candidate_deployment_types_by_app_id.get(target_app_id)
                != "workflow_node"
            ):
                return [self._workflow_node_unavailable(node_id, node_type)]
            if target_app_id in visited_app_ids:
                return [self._workflow_node_cycle(node_id, node_type)]
            return self._evaluate_graph(
                candidate_graph,
                audience=audience,
                depth=depth + 1,
                visited_app_ids={*visited_app_ids, target_app_id},
            )

        if target_app_id in visited_app_ids:
            return [self._workflow_node_cycle(node_id, node_type)]
        if target.active_graph_snapshot is None:
            return [self._workflow_node_unavailable(node_id, node_type)]
        return self._evaluate_graph(
            target.active_graph_snapshot,
            audience=audience,
            depth=depth + 1,
            visited_app_ids={*visited_app_ids, target_app_id},
        )

    def _bound_target_matches(
        self,
        target,
        binding: WorkflowNodeBinding,
    ) -> bool:
        if (
            target is None
            or target.app_id != binding.target_app_id
            or target.organization_id is None
            or (
                self.organization_id is not None
                and target.organization_id != self.organization_id
            )
            or target.workflow_id is None
            or target.deployment_id != binding.deployment_id
            or target.deployment_version != binding.deployment_version
            or target.deployment_type != "workflow_node"
            or not target.active_pointer_valid
            or not isinstance(target.active_graph_snapshot, dict)
        ):
            return False
        try:
            return (
                canonical_snapshot_sha256(target.active_graph_snapshot)
                == binding.snapshot_sha256
            )
        except WorkflowNodeBindingError:
            return False

    @staticmethod
    def _workflow_node_unavailable(
        node_id: str | None,
        node_type: str,
    ) -> _PreflightIssue:
        return _PreflightIssue(
            node_id=node_id,
            node_type=node_type,
            severity="blocked",
            reason_code="workflow_node_target_unavailable",
        )

    @staticmethod
    def _workflow_node_cycle(
        node_id: str | None,
        node_type: str,
    ) -> _PreflightIssue:
        return _PreflightIssue(
            node_id=node_id,
            node_type=node_type,
            severity="blocked",
            reason_code="workflow_node_cycle_detected",
        )

    def _result(
        self,
        audience: PreflightAudience,
        issues: list[_PreflightIssue],
    ) -> DeploymentPreflightResult:
        node_results = self._node_results(issues)
        status: PreflightStatus = "passed"
        if any(issue.severity == "blocked" for issue in issues):
            status = "blocked"
        elif any(issue.severity == "warning" for issue in issues):
            status = "warning"

        first_reason = next(
            (issue.reason_code for issue in issues if issue.severity == "blocked"),
            next((issue.reason_code for issue in issues), None),
        )
        affected_kb_count = sum(issue.knowledge_base_count for issue in issues)
        affected_collection_count = sum(
            issue.knowledge_collection_count for issue in issues
        )
        candidate_budget_limited = any(
            issue.candidate_budget_limited for issue in issues
        )
        warnings = tuple(
            dict.fromkeys(
                issue.reason_code for issue in issues if issue.severity == "warning"
            )
        )
        return DeploymentPreflightResult(
            status=status,
            audience=audience,
            safe_summary=PreflightSummary(
                blocked_reason=first_reason,
                affected_node_count=len(node_results),
                affected_kb_count_bucket=self._bucket_count(affected_kb_count),
                affected_collection_count_bucket=self._bucket_count(
                    affected_collection_count
                ),
                candidate_budget_limited=candidate_budget_limited,
            ),
            required_actions=self._required_actions(issues),
            warnings=warnings,
            nodes=node_results,
        )

    def _node_results(
        self,
        issues: list[_PreflightIssue],
    ) -> tuple[PreflightNodeResult, ...]:
        grouped: dict[tuple[str | None, str], list[_PreflightIssue]] = {}
        for issue in issues:
            grouped.setdefault((issue.node_id, issue.node_type), []).append(issue)

        results: list[PreflightNodeResult] = []
        for (node_id, node_type), node_issues in grouped.items():
            status: PreflightStatus = (
                "blocked"
                if any(issue.severity == "blocked" for issue in node_issues)
                else "warning"
            )
            results.append(
                PreflightNodeResult(
                    node_id=node_id,
                    node_type=node_type,
                    status=status,
                    reason_codes=tuple(
                        dict.fromkeys(issue.reason_code for issue in node_issues)
                    ),
                    knowledge_base_count_bucket=self._bucket_count(
                        sum(issue.knowledge_base_count for issue in node_issues)
                    ),
                    knowledge_collection_count_bucket=self._bucket_count(
                        sum(
                            issue.knowledge_collection_count
                            for issue in node_issues
                        )
                    ),
                    candidate_budget_limited=any(
                        issue.candidate_budget_limited
                        for issue in node_issues
                    ),
                )
            )
        return tuple(results)

    @staticmethod
    def _required_actions(
        issues: list[_PreflightIssue],
    ) -> tuple[PreflightRequiredAction, ...]:
        actions: dict[str, PreflightRequiredAction] = {}
        for issue in issues:
            action = (
                WARNING_REQUIRED_ACTIONS.get(issue.reason_code)
                if issue.severity == "warning"
                else None
            ) or PUBLIC_REQUIRED_ACTIONS.get(issue.reason_code)
            if action is not None:
                actions[action.action] = action
        return tuple(actions.values())

    @staticmethod
    def _downgrade_blocked_issues(
        issues: list[_PreflightIssue],
    ) -> list[_PreflightIssue]:
        return [
            replace(issue, severity="warning")
            if issue.severity == "blocked"
            and issue.reason_code not in NON_DOWNGRADABLE_REASON_CODES
            else issue
            for issue in issues
        ]

    @staticmethod
    def _iter_graph_nodes(graph_snapshot: dict):
        for located in iter_workflow_node_locations(graph_snapshot):
            yield located.node, located.location.container_path

    @staticmethod
    def _safe_node_id(node: dict) -> str | None:
        node_id = node.get("id")
        if node_id is None:
            return None
        return str(node_id)[:128]

    @staticmethod
    def _safe_node_type(node: dict) -> str:
        node_type = node.get("type")
        if not isinstance(node_type, str) or not node_type:
            return "unknown"
        return node_type[:64]

    @staticmethod
    def _uuid_or_none(value: object) -> uuid.UUID | None:
        try:
            return uuid.UUID(str(value))
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _bucket_count(value: int) -> str:
        if value <= 0:
            return "0"
        if value == 1:
            return "1"
        if value <= 10:
            return "2-10"
        if value <= 100:
            return "11-100"
        if value <= 1000:
            return "101-1000"
        return "1000+"
