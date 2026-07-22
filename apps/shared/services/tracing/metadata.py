import math
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

SENSITIVE_METADATA_KEYS = {
    "access_token",
    "api_key",
    "apikey",
    "authorization",
    "body",
    "chunk",
    "completion",
    "content",
    "cookie",
    "credential",
    "credentials",
    "filename",
    "headers",
    "input",
    "inputs",
    "message",
    "messages",
    "output",
    "outputs",
    "password",
    "prompt",
    "purge_receipt",
    "query",
    "raw",
    "raw_source_path",
    "raw_source_title",
    "raw_source_url",
    "refresh_token",
    "request",
    "response",
    "secret",
    "set_cookie",
    "source_acl",
    "source_path",
    "source_principal",
    "source_title",
    "source_url",
    "text",
    "token",
    "url",
}

SENSITIVE_METADATA_PATTERNS = (
    "access_token",
    "api_key",
    "authorization",
    "bearer",
    "body",
    "chunk",
    "completion",
    "content",
    "cookie",
    "credential",
    "header",
    "input",
    "message",
    "output",
    "password",
    "prompt",
    "purge_receipt",
    "query",
    "raw",
    "raw_source_path",
    "raw_source_title",
    "raw_source_url",
    "refresh_token",
    "request",
    "response",
    "secret",
    "source_acl",
    "source_path",
    "source_principal",
    "source_title",
    "source_url",
    "token",
)

RUN_SCALAR_FIELDS = {
    "correlation_id",
    "request_id",
    "trace_id",
    "workflow_task_id",
}
RUN_SECTION_FIELDS = {
    "auth": {
        "authenticated",
        "latency_ms",
        "principal_type",
        "status",
    },
    "celery": {
        "enqueue_latency_ms",
        "queue",
        "status",
        "task_id",
    },
    "deployment": {
        "deployment_id",
        "lookup_latency_ms",
        "status",
        "workflow_version",
    },
}

RETENTION_FIELDS = {
    "action",
    "dry_run",
    "purged",
    "purged_at",
    "scope_id",
    "scope_type",
}

GATEWAY_FIELDS = {
    "auth_latency_ms",
    "celery_enqueue_latency_ms",
    "client_ip",
    "content_type",
    "deployment_lookup_latency_ms",
    "latency_ms",
    "method",
    "path",
    "request_id",
    "route",
    "status_code",
    "trace_id",
    "user_agent_family",
}

COMMON_SPAN_FIELDS = {
    "error",
    "external_effect_output",
    "latency_ms",
}
SPAN_TOP_LEVEL_BY_NODE_TYPE = {
    "llmNode": {"llm", "rag"},
    "httpRequestNode": {"http", "external_effect"},
    "slackPostNode": {"http", "slack", "external_effect"},
    "githubNode": {"http", "external_effect"},
    "codeNode": {"sandbox"},
    "workflowNode": {"workflow"},
}
SPAN_SECTION_FIELDS = {
    "error": {
        "error_code",
        "error_type",
        "failure_phase",
        "type",
    },
    "external_effect": {
        "error_code",
        "operation",
        "outcome",
        "provider",
        "replay_decision",
    },
    "external_effect_output": {"sensitive"},
    "guardrail": {
        "blocked",
        "decision",
        "latency_ms",
        "policy_id",
        "reason_code",
        "reason_payload_id",
        "reason_redacted",
        "rule_id",
        "severity",
        "type",
    },
    "http": {
        "content_type",
        "host",
        "latency_ms",
        "method",
        "path",
        "request_payload_id",
        "request_size",
        "response_payload_id",
        "response_size",
        "retry_count",
        "status_code",
    },
    "llm": {
        "completion_payload_id",
        "completion_tokens",
        "credential_id",
        "confidence",
        "fallback_model",
        "recommended_fallback_model",
        "recommended_model",
        "recommendation_type",
        "analysis_stage",
        "customer_facing",
        "decision_factors",
        "decision_source",
        "downstream_status",
        "execution_mode",
        "fallback_used",
        "fallback_from_model",
        "fallback_reason_code",
        "fallback_provider_error_code",
        "fallback_provider_error_type",
        "fallback_provider_status_code",
        "fallback_provider_remote_error_code",
        "fallback_provider_remote_error_param",
        "fallback_provider_response_status",
        "finish_reason",
        "has_file_input",
        "input_length_bucket",
        "judge_called",
        "judge",
        "knowledge_enabled",
        "latency_ms",
        "matched_rule_id",
        "model",
        "node_task",
        "output_format",
        "policy_id",
        "policy_source",
        "learner_id",
        "learner_version",
        "included_in_routing_learning",
        "included_in_policy_learning",
        "prompt_payload_id",
        "prompt_tokens",
        "prompt_length_bucket",
        "provider",
        "reason",
        "reason_code",
        "repetition_rate",
        "retry_count",
        "routing_stage",
        "schema_required",
        "schema_status",
        "selected_model",
        "strategy_id",
        "policy_version",
        "total_cost",
        "total_tokens",
    },
    "rag": {
        "candidate_resolution_latency_ms",
        "citation_ids",
        "context_token_estimate",
        "document_ids",
        "evidence_policy_latency_ms",
        "evidence_sufficient",
        "fanout_concurrency",
        "fanout_timeout_seconds",
        "failed_candidate_count_bucket",
        "failure_policy",
        "hierarchy_fallback",
        "insufficiency_reason",
        "knowledge_base_id",
        "authorized_kb_count",
        "authorized_kb_count_bucket",
        "permission_filter_applied",
        "latency_ms",
        "partial_result",
        "query_embedding_latency_ms",
        "query_rewrite_applied",
        "query_rewrite_strategy",
        "raw_content_returned",
        "rag_mode",
        "retrieval_fanout_latency_ms",
        "retrieval_payload_id",
        "retrieval_strategy",
        "retrieved_chunk_summary_truncated",
        "retrieved_context_payload_id",
        "retrieved_chunk_count",
        "safe_exclusion_summary",
        "score_summary",
        "selected_kb_count",
        "selected_kb_count_bucket",
        "slowest_search_latency_ms",
        "source_tier_policy",
        "source_tier_used",
        "stored_result_count",
    },
    "slack": {
        "delivery_mode",
        "delivery_status",
        "has_message_ref",
        "latency_ms",
        "provider_reason",
        "provider_retryable",
        "request_size",
        "response_size",
        "retry_after_seconds",
        "status_code",
    },
    "sandbox": {
        "execution_time_ms",
        "exit_code",
        "latency_ms",
        "stderr_payload_id",
        "stdout_payload_id",
        "timeout",
    },
    "workflow": {
        "latency_ms",
    },
}
RAG_STAGE_LATENCY_FIELDS = {
    "candidate_resolution_latency_ms",
    "query_embedding_latency_ms",
    "retrieval_fanout_latency_ms",
    "slowest_search_latency_ms",
    "evidence_policy_latency_ms",
}
MAX_RAG_STAGE_LATENCY_MS = 300_000
RAG_RESULT_FIELDS = {
    "document_id",
    "chunk_id",
    "parent_chunk_id",
    "rank",
    "evidence_rank",
    "knowledge_base_id",
    "metadata_summary",
    "hierarchy_path",
    "hierarchy_fallback",
    "page_number",
    "similarity_score",
    "score",
    "token_count",
}

_MODEL_ROUTING_REASON_SHORT_BY_CODE = {
    "advanced_quality": "고급 품질 판단",
    "ambiguous_request": "모호한 요청 해석",
    "balanced_quality": "균형 품질 판단",
    "contract_reliability": "운영 성적 반영",
    "economy_fit": "경제형 모델 적합",
    "evidence_synthesis": "근거 종합 판단",
    "high_risk_reasoning": "고위험 판단 필요",
    "judge_selected": "모델 적합 판단",
    "long_context": "긴 문맥 종합",
    "multi_constraint": "여러 조건 종합",
    "request_capability_match": "요청 역량 적합",
    "short_answer": "짧은 응답 적합",
    "simple_request": "단순 요청 적합",
    "simple_response": "단순 응답 처리",
    "structured_precision": "정확한 형식 필요",
}
_UNRECOGNIZED_MODEL_ROUTING_REASON_CODE = "judge_reason_unrecognized"
_MODEL_ROUTING_REASON_FACTOR_CODES = {
    "high_decision_impact",
    "security_or_compliance_risk",
    "multi_step_reasoning",
    "evidence_conflict",
    "broad_context_synthesis",
    "strict_output_reliability",
    "long_context_handling",
}


class TraceMetadataSanitizer:
    """Tracing metadata를 scope별 allowlist로 제한하는 공용 경계."""

    @staticmethod
    def sanitize_json_safe(value: Any) -> Any:
        """JSONB 저장과 응답 직렬화가 가능한 값으로 정규화합니다."""
        if isinstance(value, dict):
            return {
                str(key): TraceMetadataSanitizer.sanitize_json_safe(child)
                for key, child in value.items()
            }
        if isinstance(value, list):
            return [TraceMetadataSanitizer.sanitize_json_safe(item) for item in value]
        if isinstance(value, tuple):
            return [TraceMetadataSanitizer.sanitize_json_safe(item) for item in value]
        if isinstance(value, set):
            return [TraceMetadataSanitizer.sanitize_json_safe(item) for item in value]
        if hasattr(value, "model_dump"):
            return TraceMetadataSanitizer.sanitize_json_safe(value.model_dump())
        if isinstance(value, uuid.UUID):
            return str(value)
        if isinstance(value, datetime):
            return value.isoformat()
        if isinstance(value, date):
            return value.isoformat()
        if isinstance(value, Decimal):
            return float(value)
        return value

    @staticmethod
    def is_sensitive_metadata_key(key: Any) -> bool:
        """allowlist 밖의 임의 키가 원문성/비밀값 성격인지 판별합니다."""
        normalized = str(key).strip().lower().replace("-", "_")
        if normalized in SENSITIVE_METADATA_KEYS:
            return True
        return any(pattern in normalized for pattern in SENSITIVE_METADATA_PATTERNS)

    @classmethod
    def sanitize_retention_metadata(cls, metadata: Any) -> dict[str, Any]:
        return cls._filter_allowed_dict(metadata, RETENTION_FIELDS)

    @classmethod
    def sanitize_gateway_metadata(cls, metadata: Any) -> dict[str, Any]:
        return cls._filter_allowed_dict(metadata, GATEWAY_FIELDS)

    @classmethod
    def sanitize_run_metadata(cls, metadata: Any) -> dict[str, Any]:
        safe_metadata = cls.sanitize_json_safe(metadata)
        if not isinstance(safe_metadata, dict):
            return {}

        sanitized: dict[str, Any] = {}
        for key in RUN_SCALAR_FIELDS:
            if key in safe_metadata:
                sanitized[key] = cls._sanitize_allowed_value(safe_metadata[key])

        if "gateway" in safe_metadata:
            gateway = cls.sanitize_gateway_metadata(safe_metadata["gateway"])
            if gateway:
                sanitized["gateway"] = gateway
        if "retention" in safe_metadata:
            retention = cls.sanitize_retention_metadata(safe_metadata["retention"])
            if retention:
                sanitized["retention"] = retention

        for section, allowed_fields in RUN_SECTION_FIELDS.items():
            if section not in safe_metadata:
                continue
            section_value = cls._filter_allowed_dict(
                safe_metadata[section], allowed_fields
            )
            if section_value:
                sanitized[section] = section_value

        return sanitized

    @classmethod
    def sanitize_span_metadata(cls, node_type: str | None, metadata: Any) -> dict[str, Any]:
        safe_metadata = cls.sanitize_json_safe(metadata)
        if not isinstance(safe_metadata, dict):
            return {}

        node_type_key = str(node_type or "")
        allowed_top_level = set(COMMON_SPAN_FIELDS)
        allowed_top_level.update(SPAN_TOP_LEVEL_BY_NODE_TYPE.get(node_type_key, set()))
        if "guardrail" in node_type_key.lower():
            allowed_top_level.add("guardrail")

        sanitized: dict[str, Any] = {}
        for key in allowed_top_level:
            if key not in safe_metadata:
                continue
            if key == "latency_ms":
                sanitized[key] = cls._sanitize_allowed_value(safe_metadata[key])
                continue
            if key == "rag":
                rag = cls._sanitize_rag_section(safe_metadata[key])
                if rag:
                    sanitized[key] = rag
                continue
            if key == "llm":
                llm = cls._sanitize_llm_section(safe_metadata[key])
                if llm:
                    sanitized[key] = llm
                continue
            section_value = cls._filter_allowed_dict(
                safe_metadata[key], SPAN_SECTION_FIELDS.get(key, set())
            )
            if section_value:
                sanitized[key] = section_value

        return sanitized

    @classmethod
    def _sanitize_llm_section(cls, value: Any) -> dict[str, Any]:
        """LLM trace에서 라우팅 판단 근거를 원문 없이 제한된 형태로 보존한다."""
        safe_value = cls.sanitize_json_safe(value)
        if not isinstance(safe_value, dict):
            return {}

        allowed_fields = SPAN_SECTION_FIELDS["llm"] - {"decision_factors", "judge"}
        sanitized = cls._filter_allowed_dict(safe_value, allowed_fields)
        decision_factors = cls._sanitize_model_routing_decision_factors(
            safe_value.get("decision_factors")
        )
        if decision_factors:
            sanitized["decision_factors"] = decision_factors
        judge = cls._sanitize_model_routing_judge(safe_value.get("judge"))
        if judge:
            sanitized["judge"] = judge
        return sanitized

    @classmethod
    def _sanitize_model_routing_judge(cls, value: Any) -> dict[str, Any]:
        """Judge 호출 결과 중 정책 설명에 필요한 제한된 값만 trace에 남긴다."""
        safe_value = cls.sanitize_json_safe(value)
        if not isinstance(safe_value, dict):
            return {}
        sanitized = cls._filter_allowed_dict(
            safe_value,
            {
                "model",
                "selected_model",
                "error_code",
                "usage_log_error",
                "learning_error",
            },
        )
        status = safe_value.get("status")
        if status in {"selected", "failed", "unavailable", "not_called"}:
            sanitized["status"] = status
        attempted = safe_value.get("attempted")
        if isinstance(attempted, bool):
            sanitized["attempted"] = attempted
        candidate_model_count = safe_value.get("candidate_model_count")
        if (
            not isinstance(candidate_model_count, bool)
            and isinstance(candidate_model_count, int)
            and 0 <= candidate_model_count <= 10_000
        ):
            sanitized["candidate_model_count"] = candidate_model_count
        not_called_reason = safe_value.get("not_called_reason")
        if isinstance(not_called_reason, str) and not_called_reason in {
            "policy_unavailable",
            "active_policy_unavailable",
            "legacy_policy_ignored",
            "test_policy_preview",
            "judge_not_required",
        }:
            sanitized["not_called_reason"] = not_called_reason
        raw_reason_code = safe_value.get("reason_code")
        reason_code = raw_reason_code.strip() if isinstance(raw_reason_code, str) else ""
        if reason_code:
            reason_short = _MODEL_ROUTING_REASON_SHORT_BY_CODE.get(reason_code)
            if reason_short is None:
                sanitized["reason_code"] = _UNRECOGNIZED_MODEL_ROUTING_REASON_CODE
            else:
                sanitized["reason_code"] = reason_code
                sanitized["reason_short"] = reason_short
        raw_reason_factors = safe_value.get("reason_factors")
        if isinstance(raw_reason_factors, list):
            reason_factors: list[str] = []
            for raw_factor in raw_reason_factors:
                factor = raw_factor.strip() if isinstance(raw_factor, str) else ""
                if (
                    factor in _MODEL_ROUTING_REASON_FACTOR_CODES
                    and factor not in reason_factors
                ):
                    reason_factors.append(factor)
                if len(reason_factors) == 3:
                    break
            if reason_factors:
                sanitized["reason_factors"] = reason_factors
        confidence = safe_value.get("confidence")
        if (
            not isinstance(confidence, bool)
            and isinstance(confidence, (int, float))
            and math.isfinite(float(confidence))
            and 0.0 <= float(confidence) <= 1.0
        ):
            sanitized["confidence"] = float(confidence)
        cost = safe_value.get("cost")
        if (
            not isinstance(cost, bool)
            and isinstance(cost, (int, float))
            and math.isfinite(float(cost))
            and 0.0 <= float(cost) <= 1_000_000.0
        ):
            sanitized["cost"] = float(cost)
        usage = safe_value.get("usage")
        if isinstance(usage, dict):
            safe_usage: dict[str, int | float] = {}
            for key in {"prompt_tokens", "completion_tokens", "total_tokens"}:
                token_count = usage.get(key)
                if (
                    not isinstance(token_count, bool)
                    and isinstance(token_count, (int, float))
                    and math.isfinite(float(token_count))
                    and 0 <= float(token_count) <= 10_000_000
                ):
                    safe_usage[key] = token_count
            latency_ms = usage.get("latency_ms")
            if (
                not isinstance(latency_ms, bool)
                and isinstance(latency_ms, (int, float))
                and math.isfinite(float(latency_ms))
                and 0 <= float(latency_ms) <= 86_400_000
            ):
                safe_usage["latency_ms"] = latency_ms
            if safe_usage:
                sanitized["usage"] = safe_usage
        return sanitized

    @classmethod
    def _sanitize_model_routing_decision_factors(cls, value: Any) -> dict[str, Any]:
        safe_value = cls.sanitize_json_safe(value)
        if not isinstance(safe_value, dict):
            return {}

        sanitized = cls._filter_allowed_dict(
            safe_value,
            {
                "profile",
                "evaluated_candidate_count",
                "excluded_candidate_count",
                "learning_mode",
                "candidate_model_count",
                "judged_request_count",
            },
        )
        selected_score = cls._filter_allowed_dict(
            safe_value.get("selected_model_score"),
            {
                "quality_lower_bound",
                "expected_total_cost_usd",
                "expected_latency_ms",
                "expected_fallback_rate",
                "effective_evidence_samples",
                "prior_source",
            },
        )
        if selected_score:
            sanitized["selected_model_score"] = selected_score

        # Bootstrap 난이도 라우팅은 원문 없이도 "왜 이 모델인가"를 설명할 수
        # 있어야 한다. 아래 값은 enum/범위가 제한된 수치만 보존하고, policy가
        # 만들었던 phrase나 실제 입력 단어는 trace에 남기지 않는다.
        classification_status = str(
            safe_value.get("classification_status") or ""
        ).strip()
        if classification_status in {
            "planner_rule",
            "matched",
            "fallback",
            "artifact_missing",
            "unavailable",
        }:
            sanitized["classification_status"] = classification_status
        difficulty = str(safe_value.get("difficulty") or "").strip()
        if difficulty in {"economy", "balanced", "advanced"}:
            sanitized["difficulty"] = difficulty
        for key in ("confidence", "minimum_confidence"):
            raw_value = safe_value.get(key)
            if (
                not isinstance(raw_value, bool)
                and isinstance(raw_value, (int, float))
                and math.isfinite(float(raw_value))
                and 0.0 <= float(raw_value) <= 1.0
            ):
                sanitized[key] = float(raw_value)
        for key in ("local_confidence", "local_confidence_threshold"):
            raw_value = safe_value.get(key)
            if (
                not isinstance(raw_value, bool)
                and isinstance(raw_value, (int, float))
                and math.isfinite(float(raw_value))
                and 0.0 <= float(raw_value) <= 1.0
            ):
                sanitized[key] = float(raw_value)
        for key in ("matched_signal_count", "match_score"):
            raw_value = safe_value.get(key)
            if (
                not isinstance(raw_value, bool)
                and isinstance(raw_value, int)
                and 0 <= raw_value <= 50
            ):
                sanitized[key] = raw_value
        raw_probabilities = safe_value.get("probabilities")
        if isinstance(raw_probabilities, dict):
            probabilities: dict[str, float] = {}
            for key in ("economy", "balanced", "advanced"):
                raw_value = raw_probabilities.get(key)
                if (
                    not isinstance(raw_value, bool)
                    and isinstance(raw_value, (int, float))
                    and math.isfinite(float(raw_value))
                    and 0.0 <= float(raw_value) <= 1.0
                ):
                    probabilities[key] = float(raw_value)
            if probabilities:
                sanitized["probabilities"] = probabilities

        signature = cls.sanitize_json_safe(safe_value.get("constraint_signature"))
        if isinstance(signature, dict):
            safe_signature: dict[str, Any] = {}
            for key in {
                "context_input_bucket",
                "rag_context_bucket",
                "output_contract",
                "schema_complexity",
                "downstream_strictness",
                "file_input",
                "required_input_missing",
                "required_capability_tier",
                "schema_required",
            }:
                if key not in signature:
                    continue
                sanitized_value = cls._sanitize_allowed_value(signature[key])
                if sanitized_value is not None:
                    safe_signature[key] = sanitized_value
            if safe_signature:
                sanitized["constraint_signature"] = safe_signature
        return sanitized

    @classmethod
    def sanitize_rag_metadata(cls, value: Any) -> Any:
        safe_value = cls.sanitize_json_safe(value)
        if isinstance(safe_value, list):
            return [
                item
                for item in (cls.sanitize_rag_metadata(child) for child in safe_value)
                if item
            ]
        if isinstance(safe_value, dict):
            filtered = cls._filter_allowed_dict(safe_value, RAG_RESULT_FIELDS)
            evidence_rank = filtered.get("evidence_rank")
            if evidence_rank is not None and (
                isinstance(evidence_rank, bool)
                or not isinstance(evidence_rank, int)
                or evidence_rank < 1
            ):
                filtered.pop("evidence_rank", None)
            return filtered
        return None

    @classmethod
    def summarize_rag_metadata(cls, value: Any) -> dict[str, Any]:
        """Per-chunk evidence에서 run/node trace에 둘 수 있는 요약만 만든다."""
        safe_results = cls.sanitize_rag_metadata(value)
        if isinstance(safe_results, dict):
            results = [safe_results]
        elif isinstance(safe_results, list):
            results = [item for item in safe_results if isinstance(item, dict)]
        else:
            results = []
        if not results:
            return {}

        document_ids: list[Any] = []
        citation_ids: list[Any] = []
        knowledge_base_ids: list[Any] = []
        scores: list[float] = []
        hierarchy_fallback = False

        for item in results:
            document_id = item.get("document_id")
            if document_id is not None:
                document_ids.append(document_id)
            chunk_id = item.get("chunk_id")
            if chunk_id is not None:
                citation_ids.append(chunk_id)
            knowledge_base_id = item.get("knowledge_base_id")
            if knowledge_base_id is not None:
                knowledge_base_ids.append(knowledge_base_id)
            raw_score = item.get("score", item.get("similarity_score"))
            try:
                if raw_score is not None:
                    scores.append(float(raw_score))
            except (TypeError, ValueError):
                pass
            hierarchy_fallback = hierarchy_fallback or bool(item.get("hierarchy_fallback"))

        summary: dict[str, Any] = {
            "retrieved_chunk_count": len(results),
            "raw_content_returned": False,
        }
        unique_kb_ids = cls._unique_preserving_order(knowledge_base_ids)
        if len(unique_kb_ids) == 1:
            summary["knowledge_base_id"] = unique_kb_ids[0]
        if document_ids:
            summary["document_ids"] = cls._unique_preserving_order(document_ids)
        if citation_ids:
            summary["citation_ids"] = cls._unique_preserving_order(citation_ids)
        if scores:
            summary["score_summary"] = {
                "min": min(scores),
                "max": max(scores),
            }
        if hierarchy_fallback:
            summary["hierarchy_fallback"] = True
        return summary

    @classmethod
    def _sanitize_rag_section(cls, value: Any) -> dict[str, Any]:
        safe_value = cls.sanitize_json_safe(value)
        if not isinstance(safe_value, dict):
            return {}

        sanitized: dict[str, Any] = {}
        for key in SPAN_SECTION_FIELDS["rag"]:
            if key in safe_value:
                if key in RAG_STAGE_LATENCY_FIELDS:
                    latency = safe_value[key]
                    if (
                        type(latency) is int
                        and 0 <= latency <= MAX_RAG_STAGE_LATENCY_MS
                    ):
                        sanitized[key] = latency
                    continue
                sanitized_value = cls._sanitize_allowed_value(safe_value[key])
                if sanitized_value is not None:
                    sanitized[key] = sanitized_value
        if "retrieval_results" in safe_value:
            summary = cls.summarize_rag_metadata(safe_value["retrieval_results"])
            sanitized.update({key: value for key, value in summary.items() if value is not None})
        return sanitized

    @classmethod
    def _filter_allowed_dict(cls, value: Any, allowed_keys: set[str]) -> dict[str, Any]:
        safe_value = cls.sanitize_json_safe(value)
        if not isinstance(safe_value, dict):
            return {}

        sanitized: dict[str, Any] = {}
        for key in allowed_keys:
            if key not in safe_value:
                continue
            sanitized_value = cls._sanitize_allowed_value(safe_value[key])
            if sanitized_value is not None:
                sanitized[key] = sanitized_value
        return sanitized

    @classmethod
    def _sanitize_allowed_value(cls, value: Any) -> Any:
        safe_value = cls.sanitize_json_safe(value)
        if isinstance(safe_value, dict):
            sanitized: dict[str, Any] = {}
            for key, child in safe_value.items():
                # exact allowlist 이후에만 임의 하위 키 패턴 denylist를 적용합니다.
                if cls.is_sensitive_metadata_key(key):
                    continue
                sanitized_child = cls._sanitize_allowed_value(child)
                if sanitized_child is not None:
                    sanitized[key] = sanitized_child
            return sanitized
        if isinstance(safe_value, list):
            sanitized_items = []
            for item in safe_value:
                sanitized_item = cls._sanitize_allowed_value(item)
                if sanitized_item is not None:
                    sanitized_items.append(sanitized_item)
            return sanitized_items
        return safe_value

    @staticmethod
    def _unique_preserving_order(values: list[Any]) -> list[Any]:
        unique: list[Any] = []
        seen: set[str] = set()
        for value in values:
            key = str(value)
            if key in seen:
                continue
            seen.add(key)
            unique.append(value)
        return unique
