import logging
import uuid
from collections.abc import Iterable, Mapping
from typing import Any
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy.orm import Session

from apps.gateway.services.organization_context import ensure_user_default_organization
from apps.gateway.services.app_service import AppService
from apps.shared.db.models.app import App
from apps.shared.audit.context import get_current_metadata
from apps.shared.db.models.mail_credential import (
    MAIL_CREDENTIAL_ACTIVE,
    MailCredential,
)
from apps.shared.db.models.workflow import Workflow
from apps.shared.domain.mail_credential import (
    MailNodeCredentialBoundaryError,
    MailProcessingGraphBoundaryError,
    validate_mail_node_credential_boundary,
    validate_mail_processing_graph_contract,
    validate_mail_processing_node_boundary,
)
from apps.shared.domain.workflow_knowledge_references import (
    WorkflowKnowledgeReferenceError,
    aggregate_workflow_knowledge_reference_ids,
    parse_workflow_knowledge_references,
)
from apps.shared.domain.slack_delivery import (
    SlackGraphBoundaryError,
    is_valid_commercial_slack_webhook_url,
    validate_slack_graph_boundary,
)
from apps.shared.schemas.workflow import WorkflowCreateRequest, WorkflowDraftRequest
from apps.shared.services.permission_audit import record_resource_permission_denied
from apps.shared.services.credential_encryption import CredentialEncryptionError
from apps.shared.services.workflow_node_secret_service import (
    WorkflowNodeSecretError,
    WorkflowNodeSecretService,
    WorkflowNodeSecretStorageError,
    get_workflow_node_secret_encryption_service,
    is_workflow_node_secret_reference,
    migrate_legacy_workflow_graph_secrets,
    validate_workflow_node_secret_persistence_boundary,
    validate_workflow_node_secret_reference_ownership,
)
from apps.shared.services.workflow_node_catalog import (
    validate_node_parameter_value,
)
from apps.shared.services.permissions import (
    get_effective_mail_credential_auth_state,
    has_mail_credential_permission,
    has_workflow_permission,
)
from apps.gateway.services.workflow_knowledge_reference_service import (
    WorkflowKnowledgeReferenceAuthorizationUnavailable,
    WorkflowKnowledgeReferenceService,
    WorkflowKnowledgeReferenceUnavailable,
)
from apps.gateway.adapters.db.agent_builder_repository import (
    AgentBuilderRepository,
    AgentBuilderRepositoryError,
)
from apps.gateway.application.agent_builder.workflow_cas import (
    WorkflowDraftCASService,
    WorkflowMutationConflict,
)
from apps.gateway.application.agent_builder.graph_mutation_builder import (
    GraphMutationValidationError,
    canonical_graph_hash,
    deferred_parameter_projection,
    materialize_candidate_features,
    materialize_candidate_graph,
)
from apps.gateway.application.agent_builder.parameter_tasks import (
    refresh_parameter_group_configuration,
)
from apps.gateway.services.audit_records import add_action_audit
from apps.shared.audit.actions import AuditAction
from apps.shared.schemas.agent_builder import GraphMutationSafeEnvelope


logger = logging.getLogger(__name__)


class WorkflowService:
    @staticmethod
    def _iter_workflow_nodes(nodes: Iterable[Any]) -> Iterable[Any]:
        pending = list(nodes)
        while pending:
            node = pending.pop()
            yield node
            data = (
                node.get("data")
                if isinstance(node, Mapping)
                else getattr(node, "data", None)
            )
            if not isinstance(data, Mapping):
                continue
            subgraph = data.get("subGraph")
            if not isinstance(subgraph, Mapping):
                continue
            nested_nodes = subgraph.get("nodes")
            if isinstance(nested_nodes, list):
                pending.extend(nested_nodes)

    @staticmethod
    def create_workflow(
        db: Session,
        request: WorkflowCreateRequest,
        user_id: UUID,
        organization_id: UUID | None = None,
    ) -> Workflow:
        """
        새 워크플로우 생성

        Args:
            db: 데이터베이스 세션
            request: 워크플로우 생성 요청 (app_id, name, description)
            user_id: 생성자 ID (UUID)
        Returns:
            생성된 Workflow 객체
        """
        # 앱 존재 확인 및 권한 체크
        app = db.query(App).filter(App.id == request.app_id).first()
        if not app:
            raise HTTPException(status_code=404, detail="App not found")

        if (
            app.organization_id
            and organization_id
            and app.organization_id != organization_id
        ):
            raise HTTPException(status_code=404, detail="App not found")

        denial_status = AppService.access_denial_status(db, app, user_id, "manage")
        if denial_status is not None:
            detail = "Forbidden" if denial_status == 403 else "App not found"
            raise HTTPException(status_code=denial_status, detail=detail)

        # organization_id fallback은 organization scope가 없는 legacy app 보정용이다.
        organization_id = (
            app.organization_id
            or organization_id
            or ensure_user_default_organization(db, user_id)
        )

        # 새 워크플로우 생성
        workflow = Workflow(
            organization_id=organization_id,
            app_id=request.app_id,
            created_by=user_id,
            graph={
                "nodes": [],
                "edges": [],
                "viewport": {"x": 0, "y": 0, "zoom": 1},
            },
        )

        db.add(workflow)
        db.flush()
        AppService._grant_workflow_manager_permission(
            db, workflow, user_id, organization_id
        )
        db.commit()
        db.refresh(workflow)

        return workflow

    @staticmethod
    def store_node_secret(
        db: Session,
        *,
        workflow_id: str,
        active_organization_id: UUID,
        user_id: UUID,
        node_id: str,
        node_type: str,
        parameter_key: str,
        secret_value: str,
    ) -> dict[str, Any]:
        workflow = (
            db.query(Workflow)
            .filter(Workflow.id == workflow_id)
            .with_for_update()
            .first()
        )
        if workflow is None:
            raise HTTPException(status_code=404, detail="Workflow not found")
        if (
            workflow.organization_id is None
            or workflow.organization_id != active_organization_id
        ):
            raise HTTPException(status_code=404, detail="Workflow not found")
        if not has_workflow_permission(
            db,
            user_id,
            workflow.id,
            "write",
            organization_id=workflow.organization_id,
        ):
            raise HTTPException(status_code=403, detail="Forbidden")
        validation_issues = validate_node_parameter_value(
            node_type,
            parameter_key,
            secret_value,
        )
        if (
            validation_issues
            or is_workflow_node_secret_reference(secret_value)
            or (
                node_type == "slackPostNode"
                and parameter_key == "url"
                and not is_valid_commercial_slack_webhook_url(secret_value)
            )
        ):
            db.rollback()
            raise HTTPException(
                status_code=422,
                detail="workflow.node_secret_invalid",
            )
        try:
            reference = WorkflowNodeSecretService.create_reference(
                db,
                encryption=get_workflow_node_secret_encryption_service(),
                workflow_id=workflow.id,
                organization_id=workflow.organization_id,
                user_id=user_id,
                node_id=node_id,
                node_type=node_type,
                parameter_key=parameter_key,
                secret_value=secret_value,
            )
        except (CredentialEncryptionError, WorkflowNodeSecretStorageError) as exc:
            db.rollback()
            raise HTTPException(
                status_code=503,
                detail="workflow.node_secret_storage_unavailable",
            ) from exc
        except WorkflowNodeSecretError as exc:
            db.rollback()
            raise HTTPException(
                status_code=422,
                detail="workflow.node_secret_invalid",
            ) from exc
        db.commit()
        return {"secret_reference": reference, "configured": True}

    @staticmethod
    def save_draft(
        db: Session,
        workflow_id: str,
        request: WorkflowDraftRequest | dict[str, Any],
        user_id: str,
    ):
        """
        워크플로우 초안을 PostgreSQL에 저장합니다.

        Args:
            db: 데이터베이스 세션
            workflow_id: 워크플로우 ID
            request: 워크플로우 데이터 (노드, 엣지, 뷰포트)
            user_id: 사용자 ID

        Returns:
            저장된 Workflow 객체
        """
        mutation_context = (
            request.mutation_context
            if isinstance(request, WorkflowDraftRequest)
            else None
        )
        query = (
            db.query(Workflow)
            .filter(Workflow.id == workflow_id)
            .populate_existing()
            .with_for_update()
        )
        workflow = query.first()

        # workflow 없으면 error 반환
        if not workflow:
            raise HTTPException(
                status_code=404,  # "찾을 수 없음" (에러 종류)
                detail="Workflow not found",  # 상세 메시지
            )

        try:
            user_uuid = uuid.UUID(str(user_id))
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=403, detail="Forbidden") from exc
        if not has_workflow_permission(
            db,
            user_uuid,
            workflow.id,
            "write",
            organization_id=workflow.organization_id,
        ):
            raise HTTPException(status_code=403, detail="Forbidden")

        request_nodes = (
            request.nodes
            if isinstance(request, WorkflowDraftRequest)
            else request.get("nodes", [])
        )
        try:
            validate_workflow_node_secret_persistence_boundary(request_nodes)
        except WorkflowNodeSecretError as exc:
            raise HTTPException(
                status_code=422,
                detail="workflow.node_secret_reference_required",
            ) from exc
        try:
            validate_workflow_node_secret_reference_ownership(
                db,
                nodes=request_nodes,
                workflow_id=workflow.id,
                organization_id=workflow.organization_id,
            )
        except WorkflowNodeSecretError as exc:
            raise HTTPException(
                status_code=422,
                detail="workflow.node_secret_reference_invalid",
            ) from exc

        raw_features = (
            request.features
            if isinstance(request, WorkflowDraftRequest)
            else request.get("features")
        )
        try:
            canonical_features = materialize_candidate_features(raw_features)
        except GraphMutationValidationError as exc:
            raise HTTPException(
                status_code=422,
                detail="workflow.features_invalid",
            ) from exc

        WorkflowService.validate_knowledge_references(
            db,
            request,
            user_id=user_id,
            organization_id=workflow.organization_id,
            require_retrieval_ready=mutation_context is None,
        )

        if mutation_context is not None:
            repository = AgentBuilderRepository()
            saved_retry = False
            boundary = None
            try:
                request_row, raw_envelope = repository.load_request_for_operation(
                    db,
                    operation_id=mutation_context.operation_id,
                    workflow_id=workflow.id,
                    organization_id=workflow.organization_id,
                    user_id=user_uuid,
                    for_update=True,
                )
                envelope = GraphMutationSafeEnvelope.model_validate(raw_envelope)
                if mutation_context.action == "revert":
                    boundary = repository.load_history_boundary(request_row)
                    if (
                        isinstance(boundary, dict)
                        and boundary.get("status") not in {"completed", "reverted"}
                    ):
                        raise WorkflowMutationConflict(
                            "history_boundary_incomplete"
                        )
                    retried_action = (
                        WorkflowDraftCASService.validate_history_action_retry(
                            workflow=workflow,
                            request=request,
                            boundary=boundary,
                        )
                        if isinstance(boundary, dict)
                        else None
                    )
                    pre_run = (
                        boundary.get("pre_run_snapshot")
                        if isinstance(boundary, dict)
                        else None
                    )
                    if retried_action is not None:
                        validated = retried_action
                        saved_retry = True
                    elif (
                        isinstance(boundary, dict)
                        and boundary.get("status") == "reverted"
                        and isinstance(pre_run, dict)
                        and canonical_graph_hash(workflow.graph)
                        == pre_run.get("graph_hash")
                    ):
                        validated = WorkflowDraftCASService.validate_reverted_retry(
                            workflow=workflow,
                            request=request,
                            boundary=boundary,
                        )
                        saved_retry = True
                    else:
                        if isinstance(boundary, dict):
                            raw_envelope = repository.history_boundary_envelope(
                                request_row,
                                mutation_context.operation_id,
                            )
                            envelope = GraphMutationSafeEnvelope.model_validate(
                                raw_envelope
                            )
                        WorkflowDraftCASService.validate_expected_draft_state(
                            workflow=workflow,
                            request=request,
                        )
                        validated = WorkflowDraftCASService.validate_revert_candidate(
                            workflow=workflow,
                            request=request,
                            envelope=envelope,
                        )
                elif mutation_context.action == "redo":
                    boundary = repository.load_history_boundary(request_row)
                    if boundary is None:
                        raise WorkflowMutationConflict("history_boundary_not_found")
                    retried_action = (
                        WorkflowDraftCASService.validate_history_action_retry(
                            workflow=workflow,
                            request=request,
                            boundary=boundary,
                        )
                    )
                    if retried_action is not None:
                        validated = retried_action
                        saved_retry = True
                    else:
                        WorkflowDraftCASService.validate_expected_draft_state(
                            workflow=workflow,
                            request=request,
                        )
                        validated = WorkflowDraftCASService.validate_redo_candidate(
                            workflow=workflow,
                            request=request,
                            boundary=boundary,
                        )
                else:  # apply
                    if envelope.status in {"pending_ack", "acknowledged"}:
                        validated = WorkflowDraftCASService.validate_saved_retry(
                            workflow=workflow,
                            request=request,
                            envelope=envelope,
                        )
                        saved_retry = True
                    else:
                        WorkflowDraftCASService.validate_expected_draft_state(
                            workflow=workflow,
                            request=request,
                        )
                        validated = WorkflowDraftCASService.validate_candidate(
                            workflow=workflow,
                            request=request,
                            envelope=envelope,
                        )
                        repository.mark_envelope_pending_save(
                            request_row,
                            validated.operation_id,
                        )
            except (AgentBuilderRepositoryError, WorkflowMutationConflict) as exc:
                code = exc.code if isinstance(exc, WorkflowMutationConflict) else str(exc)
                logger.warning(
                    "agent_builder_workflow_mutation_conflict "
                    "workflow_id=%s operation_id=%s action=%s code=%s",
                    workflow.id,
                    mutation_context.operation_id,
                    mutation_context.action,
                    code,
                )
                raise HTTPException(status_code=409, detail=code) from exc

            if saved_retry:
                parameter_group = (
                    repository.load_latest_parameter_group(request_row)
                    if mutation_context.action == "revert"
                    else None
                )
                return {
                    "status": "success",
                    "workflow_id": str(workflow.id),
                    "operation_id": str(validated.operation_id),
                    "graph_hash": validated.graph_hash,
                    "updated_at": workflow.updated_at.isoformat(),
                    "canonical_deferred_parameters": deferred_parameter_projection(
                        workflow.graph
                    ),
                    "parameter_group": (
                        parameter_group.model_dump(mode="json")
                        if parameter_group is not None
                        else None
                    ),
                }

            WorkflowService.validate_mail_credential_references(
                db,
                request,
                user_id=user_id,
                organization_id=workflow.organization_id,
            )
            workflow.graph = validated.graph
            workflow.features = canonical_features
            workflow.env_variables = (
                [value.model_dump() for value in request.env_variables]
                if request.env_variables
                else []
            )
            workflow.runtime_variables = (
                [value.model_dump() for value in request.runtime_variables]
                if request.runtime_variables
                else []
            )
            workflow.updated_by = user_uuid
            try:
                db.flush()
                db.refresh(workflow)
                reverted_parameter_group = None
                if mutation_context.action == "revert":
                    repository.mark_envelope_reverted(
                        request_row,
                        validated.operation_id,
                    )
                    reverted_parameter_group = repository.revert_completion_state(
                        request_row,
                        raw_envelope,
                    )
                    reverted_parameter_group = refresh_parameter_group_configuration(
                        reverted_parameter_group,
                        validated.graph,
                    )
                    if reverted_parameter_group is not None:
                        repository.store_parameter_group(
                            request_row,
                            reverted_parameter_group,
                        )
                elif mutation_context.action == "apply":
                    repository.mark_envelope_saved(
                        request_row,
                        operation_id=validated.operation_id,
                        result_graph_hash=validated.graph_hash,
                        workflow_updated_at=workflow.updated_at,
                    )
                else:  # redo keeps the reverted task/Knowledge lifecycle canceled.
                    repository.advance_history_boundary_final(
                        request_row,
                        graph_hash=validated.graph_hash,
                        workflow_updated_at=workflow.updated_at,
                    )
                if mutation_context.action in {"revert", "redo"}:
                    repository.record_history_boundary_action(
                        request_row,
                        operation_id=validated.operation_id,
                        action=mutation_context.action,
                        candidate_graph_hash=validated.graph_hash,
                        expected_base_graph_hash=(
                            mutation_context.expected_base_graph_hash
                        ),
                        expected_workflow_updated_at=(
                            mutation_context.expected_workflow_updated_at
                        ),
                        result_graph_hash=validated.graph_hash,
                        result_workflow_updated_at=workflow.updated_at,
                    )
                add_action_audit(
                    db,
                    (
                        AuditAction.AGENT_BUILDER_GRAPH_MUTATION_REVERTED
                        if mutation_context.action == "revert"
                        else AuditAction.AGENT_BUILDER_APPLY_SAVE_SUCCEEDED
                    ),
                    user_uuid,
                    "workflow",
                    workflow.id,
                    organization_id=workflow.organization_id,
                    metadata={
                        "operation_id": validated.operation_id,
                        "graph_hash": validated.graph_hash,
                        "catalog_version": 3,
                        "mutation_action": mutation_context.action,
                    },
                )
                db.commit()
                db.refresh(workflow)
            except Exception:
                db.rollback()
                raise
            return {
                "status": "success",
                "workflow_id": str(workflow.id),
                "operation_id": str(validated.operation_id),
                "graph_hash": validated.graph_hash,
                "updated_at": workflow.updated_at.isoformat(),
                "canonical_deferred_parameters": deferred_parameter_projection(
                    workflow.graph
                ),
                "parameter_group": (
                    reverted_parameter_group.model_dump(mode="json")
                    if mutation_context.action == "revert"
                    and reverted_parameter_group is not None
                    else None
                ),
            }

        try:
            WorkflowDraftCASService.validate_expected_draft_state(
                workflow=workflow,
                request=request,
            )
        except WorkflowMutationConflict as exc:
            raise HTTPException(status_code=409, detail=exc.code) from exc

        WorkflowService.validate_mail_credential_references(
            db,
            request,
            user_id=user_id,
            organization_id=workflow.organization_id,
        )

        # Graph 데이터 저장 (JSONB 형식)
        try:
            workflow.graph = materialize_candidate_graph({
                "nodes": [node.model_dump() for node in request.nodes],
                "edges": [edge.model_dump() for edge in request.edges],
                "viewport": request.viewport.model_dump() if request.viewport else None,
            })
        except GraphMutationValidationError as exc:
            raise HTTPException(
                status_code=422,
                detail="workflow.graph_invalid",
            ) from exc

        workflow.features = canonical_features

        # 환경 변수 처리: 요청에 환경 변수가 있으면 딕셔너리 형태로 변환하여 저장, 없으면 빈 리스트 저장
        workflow.env_variables = (
            [v.model_dump() for v in request.env_variables]
            if request.env_variables
            else []
        )
        # 런타임 변수 처리: 요청에 런타임 변수가 있으면 딕셔너리 형태로 변환하여 저장, 없으면 빈 리스트 저장
        workflow.runtime_variables = (
            [v.model_dump() for v in request.runtime_variables]
            if request.runtime_variables
            else []
        )
        workflow.updated_by = user_uuid

        # DB에 커밋
        db.commit()
        db.refresh(workflow)

        return {
            "status": "success",
            "message": "Draft saved to PostgreSQL",
            "workflow_id": str(workflow.id),
            "graph_hash": canonical_graph_hash(workflow.graph),
            "updated_at": workflow.updated_at.isoformat(),
            "canonical_deferred_parameters": deferred_parameter_projection(
                workflow.graph
            ),
        }

    @staticmethod
    def validate_knowledge_references(
        db: Session,
        request: WorkflowDraftRequest | Mapping[str, Any],
        *,
        user_id: str | UUID,
        organization_id: UUID | None,
        require_retrieval_ready: bool = True,
    ) -> None:
        graph = (
            request.model_dump(mode="python")
            if isinstance(request, WorkflowDraftRequest)
            else dict(request)
        )
        try:
            parsed_nodes = parse_workflow_knowledge_references(graph)
            direct_ids, collection_ids = aggregate_workflow_knowledge_reference_ids(
                parsed_nodes
            )
        except WorkflowKnowledgeReferenceError as exc:
            raise HTTPException(
                status_code=422,
                detail={"code": exc.reason_code, "field": exc.field_path},
            ) from exc

        # Legacy workflows may not have organization scope. A graph with no
        # Knowledge intent needs neither authorization context nor a DB query.
        if not direct_ids and not collection_ids:
            return

        try:
            user_uuid = uuid.UUID(str(user_id))
            organization_uuid = uuid.UUID(str(organization_id))
        except (TypeError, ValueError) as exc:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "knowledge_reference_context_invalid",
                    "field": "graph",
                },
            ) from exc

        service = WorkflowKnowledgeReferenceService(
            db,
            user_id=user_uuid,
            organization_id=organization_uuid,
        )
        try:
            service.validate_parsed_references(
                parsed_nodes,
                require_retrieval_ready=require_retrieval_ready,
            )
        except WorkflowKnowledgeReferenceError as exc:
            raise HTTPException(
                status_code=422,
                detail={"code": exc.reason_code, "field": exc.field_path},
            ) from exc
        except WorkflowKnowledgeReferenceUnavailable as exc:
            raise HTTPException(
                status_code=403,
                detail={"code": exc.reason_code, "field": exc.field_path},
            ) from exc
        except WorkflowKnowledgeReferenceAuthorizationUnavailable as exc:
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "knowledge_reference_authorization_unavailable",
                    "field": "graph.knowledgeReferences",
                },
            ) from exc

    @staticmethod
    def validate_mail_credential_references(
        db: Session,
        request: WorkflowDraftRequest | Mapping[str, Any],
        *,
        user_id: str,
        organization_id: UUID,
        require_resolved: bool = False,
    ) -> None:
        WorkflowService.validate_external_node_storage_boundaries(
            request,
            require_resolved=require_resolved,
        )
        nodes = (
            request.nodes
            if isinstance(request, WorkflowDraftRequest)
            else request.get("nodes", [])
        )
        if not isinstance(nodes, list):
            return
        mail_nodes = [
            node
            for node in WorkflowService._iter_workflow_nodes(nodes)
            if (
                getattr(node, "type", None)
                if not isinstance(node, dict)
                else node.get("type")
            )
            in {"mailNode", "gmailDraftNode", "mailAcknowledgeNode"}
        ]
        if not mail_nodes:
            return
        try:
            user_uuid = uuid.UUID(str(user_id))
            organization_uuid = uuid.UUID(str(organization_id))
        except (TypeError, ValueError) as exc:
            raise HTTPException(
                status_code=422, detail="mail.credential_context_invalid"
            ) from exc

        for node in mail_nodes:
            raw_data = node.get("data") if isinstance(node, dict) else node.data
            node_type = node.get("type") if isinstance(node, dict) else node.type
            data = raw_data
            if node_type == "mailAcknowledgeNode":
                continue
            credential_value = data.get("credential_id")
            if credential_value in (None, ""):
                if require_resolved:
                    raise HTTPException(
                        status_code=422,
                        detail="mail.credential_reference_required",
                    )
                continue
            try:
                credential_id = uuid.UUID(str(credential_value))
            except (TypeError, ValueError) as exc:
                raise HTTPException(
                    status_code=422,
                    detail="mail.credential_reference_invalid",
                ) from exc

            credential = (
                db.query(MailCredential)
                .filter(
                    MailCredential.id == credential_id,
                    MailCredential.organization_id == organization_uuid,
                    MailCredential.status == MAIL_CREDENTIAL_ACTIVE,
                )
                .first()
            )
            if credential is None:
                raise HTTPException(status_code=404, detail="resource.not_found")
            if has_mail_credential_permission(
                db,
                user_uuid,
                credential.id,
                "use",
                organization_id=organization_uuid,
            ):
                if node_type == "gmailDraftNode" and (
                    credential.provider != "gmail"
                    or credential.auth_type != "oauth2"
                ):
                    raise HTTPException(
                        status_code=422,
                        detail="mail.gmail_oauth_credential_required",
                    )
                continue
            record_resource_permission_denied(
                user_id=user_uuid,
                resource_type="mail_credential",
                resource_id=credential.id,
                action="use",
                effective_auth_state=get_effective_mail_credential_auth_state(
                    db,
                    user_uuid,
                    credential.id,
                    organization_id=organization_uuid,
                ),
                organization_id=organization_uuid,
                metadata=get_current_metadata(),
            )
            raise HTTPException(
                status_code=403,
                detail="mail.credential_permission_denied",
            )

    @staticmethod
    def validate_external_node_storage_boundaries(
        request: WorkflowDraftRequest | Mapping[str, Any],
        *,
        require_resolved: bool = False,
    ) -> None:
        nodes = (
            request.nodes
            if isinstance(request, WorkflowDraftRequest)
            else request.get("nodes", [])
        )
        try:
            validate_slack_graph_boundary(
                nodes,
                require_resolved=require_resolved,
                allow_legacy_selectors=not require_resolved,
            )
        except SlackGraphBoundaryError as exc:
            raise HTTPException(
                status_code=422,
                detail="slack.graph_configuration_invalid",
            ) from exc

        graph = (
            request.model_dump(mode="python")
            if isinstance(request, WorkflowDraftRequest)
            else dict(request)
        )
        try:
            validate_mail_processing_graph_contract(
                graph,
                require_resolved=require_resolved,
            )
        except MailProcessingGraphBoundaryError as exc:
            raise HTTPException(
                status_code=422,
                detail="mail.processing_configuration_invalid",
            ) from exc

        for node in WorkflowService._iter_workflow_nodes(nodes):
            node_type = (
                node.get("type")
                if isinstance(node, dict)
                else getattr(node, "type", None)
            )
            if node_type not in {
                "mailNode",
                "gmailDraftNode",
                "mailAcknowledgeNode",
            }:
                continue
            data = (
                node.get("data")
                if isinstance(node, dict)
                else getattr(node, "data", None)
            )
            try:
                if node_type == "mailNode":
                    validate_mail_node_credential_boundary(data)
                else:
                    validate_mail_processing_node_boundary(
                        node_type,
                        data,
                        allow_unresolved=not require_resolved,
                    )
            except MailNodeCredentialBoundaryError as exc:
                raise HTTPException(
                    status_code=422,
                    detail=(
                        "mail.credential_reference_required"
                        if node_type == "mailNode"
                        else "mail.processing_configuration_invalid"
                    ),
                ) from exc

    @staticmethod
    def get_draft(db: Session, workflow_id: str, *, include_metadata: bool = False):
        """
        워크플로우 초안을 PostgreSQL에서 조회합니다.
        """
        # db.query(...).first()는 조건에 맞는 첫 번째 행을 'Workflow' 모델 인스턴스(객체)로 반환합니다.
        # 데이터가 없으면 None을 반환합니다.
        workflow = db.query(Workflow).filter(Workflow.id == workflow_id).first()

        if not workflow:
            return None

        needs_secret_migration = False
        if isinstance(workflow.graph, Mapping):
            try:
                validate_workflow_node_secret_persistence_boundary(
                    workflow.graph.get("nodes", [])
                )
            except WorkflowNodeSecretError:
                needs_secret_migration = True
        if needs_secret_migration and workflow.organization_id is not None:
            workflow = (
                db.query(Workflow)
                .filter(Workflow.id == workflow_id)
                .populate_existing()
                .with_for_update()
                .first()
            )
            if workflow is None:
                return None
            try:
                validate_workflow_node_secret_persistence_boundary(
                    workflow.graph.get("nodes", [])
                    if isinstance(workflow.graph, Mapping)
                    else []
                )
                needs_secret_migration = False
            except WorkflowNodeSecretError:
                needs_secret_migration = True
        if needs_secret_migration:
            if workflow.organization_id is None:
                raise HTTPException(
                    status_code=503,
                    detail="workflow.node_secret_migration_unavailable",
                )
            try:
                migrated_graph, changed = migrate_legacy_workflow_graph_secrets(
                    db,
                    graph=workflow.graph,
                    encryption=get_workflow_node_secret_encryption_service(),
                    workflow_id=workflow.id,
                    organization_id=workflow.organization_id,
                    user_id=workflow.updated_by or workflow.created_by,
                )
            except (WorkflowNodeSecretError, CredentialEncryptionError) as exc:
                db.rollback()
                raise HTTPException(
                    status_code=503,
                    detail="workflow.node_secret_migration_unavailable",
                ) from exc
            if changed:
                workflow.graph = migrated_graph
                db.commit()
                db.refresh(workflow)

        # workflow.graph는 DB의 JSONB 타입 컬럼이며, 파이썬에서는 딕셔너리(dict)로 변환되어 반환됩니다.
        # 구조 예시: {"nodes": [...], "edges": [...], "viewport": {...}}
        # 이 데이터는 WorkflowEngine의 초기화 인자로 전달되어 실행에 사용됩니다.
        from apps.shared.domain.workflow_node_binding import (
            strip_workflow_node_bindings,
        )

        data = strip_workflow_node_bindings(workflow.graph)

        if workflow.features:
            data["features"] = workflow.features

        if include_metadata:
            data.setdefault("nodes", [])
            data.setdefault("edges", [])
            data.setdefault("viewport", {"x": 0, "y": 0, "zoom": 1})
            data["workflow_id"] = str(workflow.id)
            try:
                data["graph_hash"] = canonical_graph_hash(workflow.graph)
            except GraphMutationValidationError as exc:
                raise HTTPException(
                    status_code=422,
                    detail="workflow.graph_invalid",
                ) from exc
            data["updated_at"] = workflow.updated_at.isoformat()

        return data
