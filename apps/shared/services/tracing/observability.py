import logging
from collections import Counter
from typing import Any, ClassVar, Optional

logger = logging.getLogger(__name__)

try:
    from prometheus_client import Counter as PrometheusCounter
except Exception:
    PrometheusCounter = None

try:
    # 공통 내보내기가 없어도 prometheus_client가 있으면 기본 저장소에 계수기를 등록합니다.
    RAW_PAYLOAD_DECRYPT_FAILURES = (
        PrometheusCounter(
            "tracing_raw_payload_decrypt_failures_total",
            "Raw trace payload decryption failure count.",
            ["payload_kind", "scope"],
        )
        if PrometheusCounter
        else None
    )
except ValueError:
    RAW_PAYLOAD_DECRYPT_FAILURES = None

try:
    RAW_PAYLOAD_AUDIT_FAILURES = (
        PrometheusCounter(
            "tracing_raw_payload_audit_failures_total",
            "Raw trace payload audit write failure count.",
            ["allowed", "reason_code"],
        )
        if PrometheusCounter
        else None
    )
except ValueError:
    RAW_PAYLOAD_AUDIT_FAILURES = None

try:
    RAW_PAYLOAD_ENCRYPT_FAILURES = (
        PrometheusCounter(
            "tracing_raw_payload_encrypt_failures_total",
            "Raw trace payload encryption failure count.",
            ["payload_kind", "scope"],
        )
        if PrometheusCounter
        else None
    )
except ValueError:
    RAW_PAYLOAD_ENCRYPT_FAILURES = None

try:
    TRACE_ACCESS_CONTEXT_FAILURES = (
        PrometheusCounter(
            "tracing_access_context_failures_total",
            "Trace access context resolution failure count.",
            ["reason_code"],
        )
        if PrometheusCounter
        else None
    )
except ValueError:
    TRACE_ACCESS_CONTEXT_FAILURES = None


class TraceObservabilityService:
    """추적 전용 서버 로그와 메트릭을 기록하는 얇은 경계."""

    # 테스트와 내보내기 부재 환경을 위한 프로세스 로컬 대체 계수기입니다.
    _local_counters: ClassVar[Counter[tuple[str, str, str]]] = Counter()

    @classmethod
    def record_raw_payload_decryption_failed(
        cls, payload: Any, error: Optional[Exception] = None
    ) -> None:
        payload_kind = str(getattr(payload, "payload_kind", "unknown") or "unknown")
        scope = str(getattr(payload, "scope", "unknown") or "unknown")
        cls._local_counters[
            ("raw_payload_decryption_failed", payload_kind, scope)
        ] += 1

        if RAW_PAYLOAD_DECRYPT_FAILURES is not None:
            RAW_PAYLOAD_DECRYPT_FAILURES.labels(
                payload_kind=payload_kind,
                scope=scope,
            ).inc()

        # 로그에는 원문, 암호문, 해시, 예외 메시지를 넣지 않습니다.
        logger.error(
            "tracing.raw_payload_decryption_failed",
            extra={
                "event": "tracing.raw_payload_decryption_failed",
                "trace_id": cls._safe_str(getattr(payload, "workflow_run_id", None)),
                "span_id": cls._safe_str(
                    getattr(payload, "workflow_node_run_id", None)
                ),
                "payload_id": cls._safe_str(getattr(payload, "id", None)),
                "payload_kind": payload_kind,
                "scope": scope,
                "storage_mode": cls._safe_str(getattr(payload, "storage_mode", None)),
                "error_type": type(error).__name__ if error else "unknown",
            },
        )

    @classmethod
    def record_raw_payload_encryption_failed(
        cls,
        payload_kind: str,
        scope: str,
        workflow_node_run_id: Any = None,
        error: Optional[Exception] = None,
    ) -> None:
        safe_kind = str(payload_kind or "unknown")
        safe_scope = str(scope or "unknown")
        cls._local_counters[
            ("raw_payload_encryption_failed", safe_kind, safe_scope)
        ] += 1

        if RAW_PAYLOAD_ENCRYPT_FAILURES is not None:
            RAW_PAYLOAD_ENCRYPT_FAILURES.labels(
                payload_kind=safe_kind,
                scope=safe_scope,
            ).inc()

        # 암호화 실패 로그에는 원문, 암호문, 키, 예외 메시지를 남기지 않습니다.
        logger.error(
            "tracing.raw_payload_encryption_failed",
            extra={
                "event": "tracing.raw_payload_encryption_failed",
                "span_id": cls._safe_str(workflow_node_run_id),
                "payload_kind": safe_kind,
                "scope": safe_scope,
                "error_type": type(error).__name__ if error else "unknown",
            },
        )

    @classmethod
    def record_raw_payload_audit_failed(
        cls,
        workflow_run_id: Any,
        payload_id: Any,
        allowed: bool,
        reason_code: str,
        error: Optional[Exception] = None,
    ) -> None:
        allowed_label = "true" if allowed else "false"
        safe_reason = str(reason_code or "unknown")
        cls._local_counters[
            ("raw_payload_audit_failed", allowed_label, safe_reason)
        ] += 1

        if RAW_PAYLOAD_AUDIT_FAILURES is not None:
            RAW_PAYLOAD_AUDIT_FAILURES.labels(
                allowed=allowed_label,
                reason_code=safe_reason,
            ).inc()

        # 감사 실패 로그에도 행위자 식별자, 원문, 암호문, 예외 메시지는 남기지 않습니다.
        logger.error(
            "tracing.raw_payload_audit_failed",
            extra={
                "event": "tracing.raw_payload_audit_failed",
                "trace_id": cls._safe_str(workflow_run_id),
                "payload_id": cls._safe_str(payload_id),
                "allowed": allowed,
                "reason_code": safe_reason,
                "error_type": type(error).__name__ if error else "unknown",
            },
        )

    @classmethod
    def record_trace_access_context_failed(
        cls,
        reason_code: str,
        app_id: Any = None,
        error: Optional[Exception] = None,
    ) -> None:
        safe_reason = str(reason_code or "unknown")
        cls._local_counters[
            ("trace_access_context_failed", safe_reason, "all")
        ] += 1

        if TRACE_ACCESS_CONTEXT_FAILURES is not None:
            TRACE_ACCESS_CONTEXT_FAILURES.labels(reason_code=safe_reason).inc()

        # 접근 판단 실패 로그에는 정책/DB 오류 유형만 남기고 상세 예외 메시지는 제외합니다.
        logger.error(
            "tracing.access_context_failed",
            extra={
                "event": "tracing.access_context_failed",
                "app_id": cls._safe_str(app_id),
                "reason_code": safe_reason,
                "error_type": type(error).__name__ if error else "unknown",
            },
        )

    @classmethod
    def get_local_counter(cls, name: str, payload_kind: str, scope: str) -> int:
        return cls._local_counters[(name, payload_kind, scope)]

    @classmethod
    def reset_local_counters(cls) -> None:
        cls._local_counters.clear()

    @staticmethod
    def _safe_str(value: Any) -> Optional[str]:
        if value is None:
            return None
        return str(value)
