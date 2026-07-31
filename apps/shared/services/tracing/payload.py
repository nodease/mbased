import json
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Optional

from apps.shared.services.tracing.observability import TraceObservabilityService
from apps.shared.services.tracing.policy import (
    ResolvedRedactionPolicy,
    ResolvedRetentionPolicy,
)
from apps.shared.services.tracing.redaction import TraceRedactionService

PROMPT_COMPLETION_KINDS = {"prompt", "completion"}


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_json_safe(v) for v in value]
    if isinstance(value, tuple):
        return [_json_safe(v) for v in value]
    if hasattr(value, "model_dump"):
        return _json_safe(value.model_dump())
    if isinstance(value, (uuid.UUID, datetime)):
        return str(value)
    return value


def _canonical_json(value: Any) -> str:
    return json.dumps(_json_safe(value), sort_keys=True, ensure_ascii=False, default=str)


class TracePayloadDecryptionError(RuntimeError):
    """원문 페이로드 복호화 실패를 호출자에게 안전하게 알리는 예외."""


class TracePayloadService:
    """원문 평문을 비동기 작업 큐에 노출하지 않고 추가 전용 페이로드 기록을 생성."""

    @staticmethod
    def raw_encryption_available() -> bool:
        return bool(os.getenv("ENCRYPTION_KEY"))

    @staticmethod
    def _encrypt_raw_payload(
        payload: Any,
        payload_kind: str = "unknown",
        scope: str = "unknown",
        workflow_node_run_id: Any = None,
    ) -> Optional[str]:
        if not TracePayloadService.raw_encryption_available():
            return None
        try:
            from apps.shared.utils.encryption import encryption_manager

            return encryption_manager.encrypt(_canonical_json(payload))
        except Exception as error:
            TraceObservabilityService.record_raw_payload_encryption_failed(
                payload_kind=payload_kind,
                scope=scope,
                workflow_node_run_id=workflow_node_run_id,
                error=error,
            )
            return None

    @staticmethod
    def _retention_expires_at(
        payload_kind: str, retention_policy: ResolvedRetentionPolicy
    ) -> datetime:
        if payload_kind in PROMPT_COMPLETION_KINDS:
            days = retention_policy.prompt_completion_retention_days
        else:
            days = retention_policy.redacted_payload_retention_days
        return datetime.now(timezone.utc) + timedelta(days=days)

    @staticmethod
    def prepare_payload_records(
        payloads: Iterable[dict[str, Any]],
        redaction_policy: ResolvedRedactionPolicy,
        retention_policy: ResolvedRetentionPolicy,
        default_scope: str,
        default_node_run_id: Any = None,
        sequence_start: int = 1,
    ) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []

        for offset, item in enumerate(payloads):
            payload_kind = str(item.get("payload_kind") or "input")
            scope = str(item.get("scope") or default_scope)
            payload = item.get("payload")
            sequence = item.get("sequence", sequence_start + offset)
            attempt = item.get("attempt", 1)
            node_run_id = item.get("workflow_node_run_id", default_node_run_id)

            if (
                payload_kind in PROMPT_COMPLETION_KINDS
                and not redaction_policy.prompt_completion_storage_enabled
            ):
                payload_id = uuid.uuid4()
                records.append(
                    {
                        "id": payload_id,
                        "workflow_node_run_id": node_run_id,
                        "scope": scope,
                        "payload_kind": payload_kind,
                        "sequence": sequence,
                        "attempt": attempt,
                        "redacted_payload": None,
                        "raw_payload_encrypted": None,
                        "redaction_applied": False,
                        "pii_detected": False,
                        "secret_detected": False,
                        "redaction_metadata": {
                            "storage": {"reason": "prompt_completion_storage_disabled"}
                        },
                        "storage_mode": "metadata_only",
                        "retention_expires_at": TracePayloadService._retention_expires_at(
                            payload_kind, retention_policy
                        ),
                    }
                )
                continue

            redaction = TraceRedactionService.redact_payload(
                payload,
                policy=redaction_policy,
                payload_kind=payload_kind,
            )
            raw_payload_encrypted = None
            storage_mode = "redacted_only"

            # 비밀값 탐지나 마스킹 실패가 있으면 정책이 허용해도 원문 저장을 열지 않습니다.
            raw_allowed = (
                redaction_policy.raw_payload_storage_enabled
                and not redaction_policy.store_redacted_copy_only
                and not redaction.secret_detected
                and not redaction.failed
            )
            if raw_allowed:
                raw_payload_encrypted = TracePayloadService._encrypt_raw_payload(
                    payload,
                    payload_kind=payload_kind,
                    scope=scope,
                    workflow_node_run_id=node_run_id,
                )
                if raw_payload_encrypted:
                    storage_mode = "raw_and_redacted"

            payload_id = uuid.uuid4()
            records.append(
                {
                    "id": payload_id,
                    "workflow_node_run_id": node_run_id,
                    "scope": scope,
                    "payload_kind": payload_kind,
                    "sequence": sequence,
                    "attempt": attempt,
                    "redacted_payload": _json_safe(redaction.redacted_payload),
                    "raw_payload_encrypted": raw_payload_encrypted,
                    "redaction_applied": redaction.redaction_applied,
                    "pii_detected": redaction.pii_detected,
                    "secret_detected": redaction.secret_detected,
                    "redaction_metadata": _json_safe(redaction.redaction_metadata),
                    "storage_mode": storage_mode,
                    "retention_expires_at": TracePayloadService._retention_expires_at(
                        payload_kind, retention_policy
                    ),
                }
            )

        return records

    @staticmethod
    def summarize_payload_records(records: Iterable[dict[str, Any]]) -> dict[str, Any]:
        record_list = list(records)
        if not record_list:
            storage_mode = "metadata_only"
        elif any(r.get("storage_mode") == "raw_and_redacted" for r in record_list):
            storage_mode = "raw_and_redacted"
        elif all(r.get("storage_mode") == "metadata_only" for r in record_list):
            storage_mode = "metadata_only"
        else:
            storage_mode = "redacted_only"
        return {
            "redaction_applied": any(r.get("redaction_applied") for r in record_list),
            "pii_detected": any(r.get("pii_detected") for r in record_list),
            "secret_detected": any(r.get("secret_detected") for r in record_list),
            "payload_storage_mode": storage_mode,
        }

    @staticmethod
    def payload_references(records: Iterable[dict[str, Any]]) -> dict[str, str]:
        references: dict[str, str] = {}
        for record in records:
            references[str(record["payload_kind"])] = str(record["id"])
        return references

    @staticmethod
    def apply_view(payload: Any, view_level: str) -> Any:
        if view_level == "metadata":
            return None
        if view_level == "redacted":
            return payload.redacted_payload
        if view_level == "raw":
            if not payload.raw_payload_encrypted:
                return None
            try:
                from apps.shared.utils.encryption import encryption_manager

                return json.loads(encryption_manager.decrypt(payload.raw_payload_encrypted))
            except Exception as error:
                # 복호화 장애는 운영 신호로 남기되 원문/암호문/키 정보는 기록하지 않습니다.
                TraceObservabilityService.record_raw_payload_decryption_failed(
                    payload, error
                )
                raise TracePayloadDecryptionError(
                    "raw_payload_decryption_failed"
                ) from error
        raise ValueError("Invalid payload view level")
