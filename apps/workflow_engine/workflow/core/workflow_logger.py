"""
워크플로우 실행 로깅 유틸리티 (Celery 기반)

WorkflowEngine의 실행 이력을 Log-System 마이크로서비스에 비동기로 전송합니다.
- WorkflowRun: 워크플로우 전체 실행 로그
- WorkflowNodeRun: 개별 노드 실행 로그

[리팩토링 이력]
- v1: 동기식 DB 저장
- v2: 비동기식 Queue + Worker Thread 방식 (인스턴스별 스레드)
- v3: 애플리케이션 레벨 공유 LogWorkerPool 사용
- v4 (현재): Celery 태스크를 통한 마이크로서비스 분리
  - 모든 DB 작업은 apps/log_system/tasks.py에서 수행
  - 이 파일은 Celery 태스크 호출만 담당
"""

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from apps.shared.celery_app import celery_app
from apps.shared.db.session import SessionLocal
from apps.shared.domain.run_trigger import (
    RunTriggerContractError,
    normalize_run_trigger_mode,
)
from apps.shared.services.external_effect_trace_capture import (
    defers_provider_capture_until_finish,
    durable_provider_summary,
    uses_metadata_only_provider_capture,
)
from apps.shared.services.app_lifecycle_admission import (
    AppWorkflowAdmissionUnavailable,
    admit_workflow_run,
    uuid_or_none,
)
from apps.shared.services.tracing.metadata import TraceMetadataSanitizer
from apps.shared.services.tracing.mail_payload import sanitize_mail_trace_payload
from apps.shared.services.tracing.payload import TracePayloadService
from apps.shared.services.tracing.policy import TracePolicyService
from apps.shared.services.tracing.redaction import TraceRedactionService
from apps.workflow_engine.workflow.errors import NonRetryableWorkflowError


class WorkflowLogger:
    """
    워크플로우 실행 로깅을 담당하는 유틸리티 클래스

    Celery 태스크를 통해 Log-System 마이크로서비스로 로그 전송
    - 비동기 처리: Celery 큐를 통해 로그 저장 요청 전송
    - 마이크로서비스 분리: 로그 저장 로직이 apps/log_system에서 실행

    Context Manager 패턴을 지원합니다.

    Usage:
        with WorkflowLogger() as logger:
            logger.create_run_log(...)
    """

    def __init__(self, db=None):
        """
        Args:
            db: SQLAlchemy 세션 (하위 호환성을 위해 유지, 실제로는 사용하지 않음)
        """
        self.workflow_run_id: Optional[uuid.UUID] = None
        self.app_id: Optional[str] = None
        self._policy_cache: Dict[str, Any] = {}
        self._durable_admission_enabled = db is not None

    def _admit_run(self, data: Dict[str, Any], canonical_trigger_mode: str) -> None:
        if not self._durable_admission_enabled:
            return
        app_id = uuid_or_none(data.get("app_id"))
        workflow_id = uuid_or_none(data.get("workflow_id"))
        organization_id = uuid_or_none(data.get("organization_id"))
        run_id = uuid_or_none(data.get("run_id"))
        if None in {app_id, workflow_id, organization_id, run_id}:
            raise NonRetryableWorkflowError("workflow.target_unavailable")

        session = SessionLocal()
        try:
            admit_workflow_run(
                session,
                run_id=run_id,
                app_id=app_id,
                workflow_id=workflow_id,
                organization_id=organization_id,
                user_id=uuid_or_none(data.get("user_id")),
                trigger_mode=canonical_trigger_mode,
                inputs=data.get("user_input") or {},
                deployment_id=uuid_or_none(data.get("deployment_id")),
                workflow_version=data.get("workflow_version"),
                correlation_id=data.get("correlation_id"),
                conversation_id=uuid_or_none(data.get("conversation_id")),
                request_id=data.get("request_id"),
                workflow_task_id=data.get("workflow_task_id"),
                trace_metadata=data.get("trace_metadata") or {},
                redaction_applied=bool(data.get("redaction_applied")),
                pii_detected=bool(data.get("pii_detected")),
                redaction_policy_id=uuid_or_none(data.get("redaction_policy_id")),
                retention_policy_id=uuid_or_none(data.get("retention_policy_id")),
                visibility_policy_id=uuid_or_none(data.get("visibility_policy_id")),
                payload_storage_mode=(
                    data.get("payload_storage_mode") or "redacted_only"
                ),
            )
        except AppWorkflowAdmissionUnavailable:
            raise NonRetryableWorkflowError("workflow.target_unavailable") from None
        finally:
            session.close()

    def _serialize_for_celery(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Celery 태스크용 데이터 직렬화 (UUID, datetime 변환)"""
        serialized = {}
        for key, value in data.items():
            if isinstance(value, uuid.UUID):
                serialized[key] = str(value)
            elif isinstance(value, datetime):
                serialized[key] = value.isoformat()
            elif isinstance(value, dict):
                serialized[key] = self._serialize_for_celery(value)
            elif isinstance(value, (list, tuple)):
                serialized[key] = [
                    self._serialize_for_celery(item)
                    if isinstance(item, dict)
                    else str(item)
                    if isinstance(item, (uuid.UUID, datetime))
                    else item
                    for item in value
                ]
            else:
                serialized[key] = value
        return serialized

    def _policy_context(self, app_id: Optional[str] = None):
        cache_key = str(app_id or "global")
        if cache_key in self._policy_cache:
            return self._policy_cache[cache_key]

        session = None
        try:
            session = SessionLocal()
            context = {
                "redaction": TracePolicyService.resolve_redaction_policy(
                    session, app_id=app_id
                ),
                "retention": TracePolicyService.resolve_retention_policy(
                    session, app_id=app_id
                ),
                "visibility": TracePolicyService.resolve_visibility_policy(
                    session, app_id=app_id
                ),
                "payload_capture_enabled": True,
            }
        except Exception:
            # 정책 해석 실패 시 페이로드 수집은 닫고, 호환 컬럼만 기본 마스킹으로 저장합니다.
            context = {
                "redaction": TracePolicyService.fail_closed_redaction_policy(),
                "retention": TracePolicyService.bootstrap_retention_policy(),
                "visibility": TracePolicyService.bootstrap_visibility_policy(),
                "payload_capture_enabled": False,
            }
        finally:
            if session is not None:
                session.close()

        self._policy_cache[cache_key] = context
        return context

    def _prepare_payloads(
        self,
        payloads: list[dict[str, Any]],
        default_scope: str,
        app_id: Optional[str] = None,
        default_node_run_id: Optional[uuid.UUID] = None,
    ):
        context = self._policy_context(app_id)
        if not context.get("payload_capture_enabled", True):
            # 실행은 유지하되 추적 페이로드 행은 만들지 않는 보수적 차단 경로입니다.
            return [], TracePayloadService.summarize_payload_records([]), context
        records = TracePayloadService.prepare_payload_records(
            payloads=payloads,
            redaction_policy=context["redaction"],
            retention_policy=context["retention"],
            default_scope=default_scope,
            default_node_run_id=default_node_run_id,
        )
        summary = TracePayloadService.summarize_payload_records(records)
        return records, summary, context

    def _redact_compat_value(
        self,
        value: Any,
        payload_kind: str,
        app_id: Optional[str] = None,
    ) -> Any:
        policy = self._policy_context(app_id)["redaction"]
        return TraceRedactionService.redact_payload(
            value, policy=policy, payload_kind=payload_kind
        ).redacted_payload

    def _metadata_with_payload_refs(
        self,
        trace_metadata: Optional[Dict[str, Any]],
        payload_records: list[dict[str, Any]],
    ) -> Dict[str, Any]:
        metadata = dict(trace_metadata or {})
        references = TracePayloadService.payload_references(payload_records)
        # 메타데이터에는 페이로드 본문 대신 식별자 참조만 남겨 조회/접근 정책 경계를 유지합니다.
        mapping = {
            "prompt": ("llm", "prompt_payload_id"),
            "completion": ("llm", "completion_payload_id"),
            "retrieved_context": ("rag", "retrieved_context_payload_id"),
            "rag.retrieval": ("rag", "retrieval_payload_id"),
            "http_request": ("http", "request_payload_id"),
            "http_response": ("http", "response_payload_id"),
            "stdout": ("sandbox", "stdout_payload_id"),
            "stderr": ("sandbox", "stderr_payload_id"),
            "guardrail_reason": ("guardrail", "reason_payload_id"),
        }
        for payload_kind, payload_id in references.items():
            target = mapping.get(payload_kind)
            if not target:
                continue
            section, field = target
            section_value = dict(metadata.get(section) or {})
            section_value[field] = payload_id
            metadata[section] = section_value
        return metadata

    def _submit_log(self, task_name: str, data: Dict[str, Any], countdown: float = 0):
        """
        Celery 태스크로 로그 작업 제출 (비동기)

        Args:
            task_name: Celery 태스크 이름
            data: 전송할 데이터
            countdown: 태스크 실행 전 대기 시간(초). 부모 레코드 생성 대기용.
        """
        serialized_data = self._serialize_for_celery(data)
        celery_app.send_task(task_name, args=[serialized_data], countdown=countdown)

    def __enter__(self):
        """Context Manager 진입"""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context Manager 종료"""
        return False

    def shutdown(self):
        """로깅 종료 처리 (호환성을 위해 유지, no-op)"""
        pass

    # ============================================================
    # 공개 메서드 (Celery 태스크 호출 )
    # ============================================================

    def create_run_log(
        self,
        workflow_id: str,
        user_id: str,
        user_input: Dict[str, Any],
        is_deployed: bool,
        execution_context: Dict[str, Any],
        external_run_id: Optional[str] = None,  # [NEW] 외부에서 전달받은 run_id
    ) -> Optional[uuid.UUID]:
        """워크플로우 실행 로그 생성"""
        try:
            canonical_trigger_mode = normalize_run_trigger_mode(
                execution_context.get("trigger_mode"),
                is_deployed=is_deployed,
            )
        except RunTriggerContractError:
            raise NonRetryableWorkflowError(
                "workflow run trigger mode is invalid"
            ) from None

        is_system_schedule = canonical_trigger_mode == "scheduler" and str(
            execution_context.get("workflow_task_id") or ""
        ).startswith("schedule:")
        if not workflow_id or (not user_id and not is_system_schedule):
            return None

        # 외부 run_id가 있으면 사용, 없으면 새로 생성
        if external_run_id:
            run_id = uuid.UUID(external_run_id)
        else:
            run_id = uuid.uuid4()
        self.workflow_run_id = run_id
        self.app_id = execution_context.get("app_id")

        payload_records, summary, policy_context = self._prepare_payloads(
            [{"payload_kind": "input", "payload": user_input, "scope": "trace"}],
            default_scope="trace",
            app_id=self.app_id,
        )
        redacted_input = (
            payload_records[0]["redacted_payload"] if payload_records else user_input
        )
        if not payload_records:
            # 페이로드 수집이 닫힌 경우에도 기존 로그 UI용 입력값은 마스킹 후 저장합니다.
            redacted_input = self._redact_compat_value(
                user_input, payload_kind="input", app_id=self.app_id
            )

        data = {
            "run_id": run_id,
            "workflow_id": workflow_id,
            "app_id": self.app_id,
            "user_id": user_id,
            "user_input": redacted_input,
            "organization_id": execution_context.get("organization_id"),
            "durable_admission": self._durable_admission_enabled,
            "is_deployed": is_deployed,
            "trigger_mode": execution_context.get("trigger_mode"),
            "deployment_id": execution_context.get("deployment_id"),
            "workflow_version": execution_context.get("workflow_version"),
            "correlation_id": execution_context.get("correlation_id"),
            "conversation_id": execution_context.get("conversation_id"),
            "request_id": execution_context.get("request_id"),
            "workflow_task_id": execution_context.get("workflow_task_id"),
            "trace_payloads": payload_records,
            "trace_metadata": TraceMetadataSanitizer.sanitize_run_metadata(
                execution_context.get("trace_metadata") or {}
            ),
            "redaction_applied": summary["redaction_applied"],
            "pii_detected": summary["pii_detected"],
            "redaction_policy_id": policy_context["redaction"].id,
            "retention_policy_id": policy_context["retention"].id,
            "visibility_policy_id": policy_context["visibility"].id,
            "payload_storage_mode": summary["payload_storage_mode"],
            "started_at": datetime.now(timezone.utc),
        }
        self._admit_run(data, canonical_trigger_mode)
        self._submit_log("log.create_run", data)
        return run_id

    def update_run_log_finish(
        self,
        outputs: Dict[str, Any],
        *,
        mail_sensitive_lineage: bool = False,
    ):
        """워크플로우 실행 완료 로그 업데이트"""
        if not self.workflow_run_id:
            return

        trace_outputs = sanitize_mail_trace_payload(
            node_type=None,
            payload_kind="output",
            value=outputs,
            mail_sensitive_lineage=mail_sensitive_lineage,
        )
        payload_records, summary, _ = self._prepare_payloads(
            [{"payload_kind": "output", "payload": trace_outputs, "scope": "trace"}],
            default_scope="trace",
            app_id=self.app_id,
        )
        redacted_outputs = (
            payload_records[0]["redacted_payload"] if payload_records else trace_outputs
        )
        if not payload_records:
            redacted_outputs = self._redact_compat_value(
                trace_outputs, payload_kind="output", app_id=self.app_id
            )

        data = {
            "run_id": self.workflow_run_id,
            "outputs": redacted_outputs,
            "trace_payloads": payload_records,
            "redaction_applied": summary["redaction_applied"],
            "pii_detected": summary["pii_detected"],
            "payload_storage_mode": summary["payload_storage_mode"],
            "finished_at": datetime.now(timezone.utc),
        }
        self._submit_log("log.update_run_finish", data)

    def update_run_log_error(self, error_message: str):
        """워크플로우 실행 에러 로그 업데이트"""
        if not self.workflow_run_id:
            return

        redacted_error = self._redact_compat_value(
            error_message, payload_kind="error", app_id=self.app_id
        )

        data = {
            "run_id": self.workflow_run_id,
            "error_message": redacted_error,
            "finished_at": datetime.now(timezone.utc),
        }
        self._submit_log("log.update_run_error", data)

    def create_node_log(
        self,
        node_id: str,
        node_type: str,
        inputs: Dict[str, Any],
        process_data: Optional[Dict[str, Any]] = None,
        sequence: Optional[int] = None,
        retry_count: int = 0,
        mail_sensitive_lineage: bool = False,
    ) -> Optional[uuid.UUID]:
        """노드 실행 로그 생성"""
        if not self.workflow_run_id:
            return None

        # [FIX] Deterministic Log ID 생성
        log_id = uuid.uuid4()
        metadata_only = uses_metadata_only_provider_capture(node_type, process_data)
        deferred_capture = defers_provider_capture_until_finish(node_type)
        suppress_initial_payload = metadata_only or deferred_capture
        trace_inputs = sanitize_mail_trace_payload(
            node_type=node_type,
            payload_kind="input",
            value={} if suppress_initial_payload else inputs,
            mail_sensitive_lineage=mail_sensitive_lineage,
        )
        payload_records, summary, policy_context = self._prepare_payloads(
            []
            if deferred_capture
            else [
                {
                    "payload_kind": "input",
                    "payload": trace_inputs,
                    "scope": "span",
                    "workflow_node_run_id": log_id,
                }
            ],
            default_scope="span",
            app_id=self.app_id,
            default_node_run_id=log_id,
        )
        redacted_inputs = (
            payload_records[0]["redacted_payload"] if payload_records else trace_inputs
        )
        if not payload_records:
            redacted_inputs = self._redact_compat_value(
                trace_inputs, payload_kind="input", app_id=self.app_id
            )
        redacted_process_data = (
            {}
            if suppress_initial_payload
            else self._redact_compat_value(
                process_data or {}, payload_kind="process_data", app_id=self.app_id
            )
        )

        data = {
            "id": log_id,  # [NEW] PK를 미리 생성하여 전달
            "workflow_run_id": self.workflow_run_id,
            "node_id": node_id,
            "node_type": node_type,
            "inputs": redacted_inputs,
            "process_data": redacted_process_data or {},
            "trace_payloads": payload_records,
            "redaction_applied": summary["redaction_applied"],
            "pii_detected": summary["pii_detected"],
            "redaction_policy_id": policy_context["redaction"].id,
            "sequence": sequence,
            "retry_count": retry_count,
            "started_at": datetime.now(timezone.utc),
        }
        # [FIX] 0.3초 딜레이: 부모 WorkflowRun이 먼저 생성되도록 대기
        self._submit_log("log.create_node", data, countdown=0.3)
        return log_id

    def update_node_log_finish(
        self,
        log_id: uuid.UUID,
        node_id: str,
        outputs: Any,
        node_type: str = None,
        inputs: Dict[str, Any] = None,
        process_data: Dict[str, Any] = None,
        started_at: datetime = None,
        trace_metadata: Dict[str, Any] = None,
        trace_payloads: list[dict[str, Any]] = None,
        sequence: Optional[int] = None,
        retry_count: int = 0,
        mail_sensitive_lineage: bool = False,
    ):
        """노드 실행 완료 로그 업데이트 (Upsert 패턴 지원)"""
        if not self.workflow_run_id or not log_id:
            return

        provider_summary = durable_provider_summary(
            node_type=node_type,
            process_data=process_data,
            trace_metadata=trace_metadata,
        )
        metadata_only = provider_summary is not None
        deferred_capture = defers_provider_capture_until_finish(node_type)
        normalized_outputs = (
            provider_summary
            if provider_summary is not None
            else outputs
            if isinstance(outputs, dict)
            else {"result": outputs}
        )
        trace_outputs = sanitize_mail_trace_payload(
            node_type=node_type,
            payload_kind="output",
            value=normalized_outputs,
            mail_sensitive_lineage=mail_sensitive_lineage,
        )
        payload_items = [
            {
                "payload_kind": "output",
                "payload": trace_outputs,
                "scope": "span",
                "workflow_node_run_id": log_id,
            }
        ]
        if deferred_capture and not metadata_only:
            payload_items.append(
                {
                    "payload_kind": "input",
                    "payload": sanitize_mail_trace_payload(
                        node_type=node_type,
                        payload_kind="input",
                        value=inputs or {},
                        mail_sensitive_lineage=mail_sensitive_lineage,
                    ),
                    "scope": "span",
                    "workflow_node_run_id": log_id,
                }
            )
        if not mail_sensitive_lineage and not metadata_only:
            payload_items.extend(trace_payloads or [])
        payload_records, summary, _ = self._prepare_payloads(
            payload_items,
            default_scope="span",
            app_id=self.app_id,
            default_node_run_id=log_id,
        )
        redacted_outputs = (
            payload_records[0]["redacted_payload"] if payload_records else trace_outputs
        )
        if not payload_records:
            redacted_outputs = self._redact_compat_value(
                trace_outputs, payload_kind="output", app_id=self.app_id
            )
        trace_inputs = sanitize_mail_trace_payload(
            node_type=node_type,
            payload_kind="input",
            value={} if metadata_only else inputs or {},
            mail_sensitive_lineage=mail_sensitive_lineage,
        )
        redacted_inputs = self._redact_compat_value(
            trace_inputs, payload_kind="input", app_id=self.app_id
        )
        redacted_process_data = (
            {}
            if metadata_only
            else self._redact_compat_value(
                process_data or {}, payload_kind="process_data", app_id=self.app_id
            )
        )
        enriched_metadata = self._metadata_with_payload_refs(
            trace_metadata, payload_records
        )
        sanitized_metadata = TraceMetadataSanitizer.sanitize_span_metadata(
            node_type, enriched_metadata
        )
        if metadata_only:
            sanitized_metadata["external_effect_output"] = {"sensitive": True}

        data = {
            "log_id": log_id,
            "workflow_run_id": self.workflow_run_id,
            "node_id": node_id,
            "outputs": redacted_outputs,
            "finished_at": datetime.now(timezone.utc),
            # [NEW] Upsert용 추가 정보 (레코드가 없을 때 생성에 사용)
            "node_type": node_type,
            "inputs": redacted_inputs or {},
            "process_data": redacted_process_data or {},
            "started_at": started_at,
            "duration": (
                (datetime.now(timezone.utc) - started_at).total_seconds()
                if started_at
                else None
            ),
            "trace_metadata": sanitized_metadata,
            "trace_payloads": payload_records,
            "redaction_applied": summary["redaction_applied"],
            "pii_detected": summary["pii_detected"],
            "sequence": sequence,
            "retry_count": retry_count,
        }
        # [FIX] 0.3초 딜레이: 부모 WorkflowRun이 먼저 생성되도록 대기
        self._submit_log("log.update_node_finish", data, countdown=0.3)

    def update_node_log_error(
        self,
        log_id: uuid.UUID,
        node_id: str,
        error_message: str,
        node_type: str = None,
        inputs: Dict[str, Any] = None,
        process_data: Dict[str, Any] = None,
        started_at: datetime = None,
        trace_metadata: Dict[str, Any] = None,
        sequence: Optional[int] = None,
        retry_count: int = 0,
        mail_sensitive_lineage: bool = False,
    ):
        """노드 실행 에러 로그 업데이트 (Upsert 패턴 지원)"""
        if not self.workflow_run_id or not log_id:
            return

        sensitive_output = uses_metadata_only_provider_capture(
            node_type,
            process_data,
            trace_metadata,
        )
        metadata_only = defers_provider_capture_until_finish(node_type) or sensitive_output
        trace_inputs = sanitize_mail_trace_payload(
            node_type=node_type,
            payload_kind="input",
            value={} if metadata_only else inputs or {},
            mail_sensitive_lineage=mail_sensitive_lineage,
        )
        redacted_inputs = self._redact_compat_value(
            trace_inputs, payload_kind="input", app_id=self.app_id
        )
        redacted_process_data = (
            {}
            if metadata_only
            else self._redact_compat_value(
                process_data or {}, payload_kind="process_data", app_id=self.app_id
            )
        )
        redacted_error = self._redact_compat_value(
            error_message, payload_kind="error", app_id=self.app_id
        )

        sanitized_metadata = TraceMetadataSanitizer.sanitize_span_metadata(
            node_type, trace_metadata or {}
        )
        if sensitive_output:
            sanitized_metadata["external_effect_output"] = {"sensitive": True}

        data = {
            "log_id": log_id,
            "workflow_run_id": self.workflow_run_id,
            "node_id": node_id,
            "error_message": redacted_error,
            "finished_at": datetime.now(timezone.utc),
            # [NEW] Upsert용 추가 정보 (레코드가 없을 때 생성에 사용)
            "node_type": node_type,
            "inputs": redacted_inputs or {},
            "process_data": redacted_process_data or {},
            "started_at": started_at,
            "duration": (
                (datetime.now(timezone.utc) - started_at).total_seconds()
                if started_at
                else None
            ),
            "trace_metadata": sanitized_metadata,
            "sequence": sequence,
            "retry_count": retry_count,
        }
        # [FIX] 0.3초 딜레이: 부모 WorkflowRun이 먼저 생성되도록 대기
        self._submit_log("log.update_node_error", data, countdown=0.3)
