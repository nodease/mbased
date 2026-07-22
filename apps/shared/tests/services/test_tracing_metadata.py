import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from apps.shared.schemas.tracing import TraceDetailSchema, TraceSummarySchema
from apps.shared.services.tracing.metadata import TraceMetadataSanitizer
from apps.shared.services.tracing.query import TraceQueryService


def _trace_schema_values(*, user_id):
    return {
        "id": uuid.uuid4(),
        "workflow_id": uuid.uuid4(),
        "app_id": uuid.uuid4(),
        "user_id": user_id,
        "deployment_id": uuid.uuid4(),
        "status": "success",
        "trigger_mode": "scheduler",
        "started_at": datetime.now(timezone.utc),
    }


def test_trace_summary_and_detail_allow_null_system_schedule_actor():
    values = _trace_schema_values(user_id=None)

    summary = TraceSummarySchema.model_validate(values)
    detail = TraceDetailSchema.model_validate(values)

    assert summary.user_id is None
    assert detail.user_id is None


def test_trace_summary_preserves_interactive_user_actor():
    user_id = uuid.uuid4()

    summary = TraceSummarySchema.model_validate(
        _trace_schema_values(user_id=user_id)
    )

    assert summary.user_id == user_id


def test_purge_receipt_metadata_keys_are_always_sensitive():
    assert TraceMetadataSanitizer.is_sensitive_metadata_key("purge_receipt")
    assert TraceMetadataSanitizer.is_sensitive_metadata_key(
        "public_purge_receipt_value"
    )


def test_span_metadata_allowlist_preserves_safe_response_summary_fields():
    metadata = TraceMetadataSanitizer.sanitize_span_metadata(
        "httpRequestNode",
        {
            "http": {
                "method": "POST",
                "content_type": "application/json",
                "response_size": 123,
                "response": {"body": "raw response"},
                "authorization": "Bearer token",
            }
        },
    )

    assert metadata["http"]["method"] == "POST"
    assert metadata["http"]["content_type"] == "application/json"
    assert metadata["http"]["response_size"] == 123
    assert "response" not in metadata["http"]
    assert "authorization" not in metadata["http"]


def test_run_metadata_is_sanitized_by_scope():
    metadata = TraceMetadataSanitizer.sanitize_run_metadata(
        {
            "gateway": {
                "method": "POST",
                "path": "/v1/workflows/run",
                "status_code": 202,
                "latency_ms": 42,
                "content_type": "application/json",
                "body": {"prompt": "raw"},
            },
            "retention": {
                "purged_at": "2026-06-25T00:00:00+00:00",
                "action": "summarize",
                "content": "raw",
            },
            "auth": {
                "authenticated": True,
                "authorization": "Bearer token",
            },
            "prompt": "raw prompt",
        }
    )

    assert metadata["gateway"]["content_type"] == "application/json"
    assert metadata["gateway"]["status_code"] == 202
    assert "body" not in metadata["gateway"]
    assert metadata["retention"] == {
        "purged_at": "2026-06-25T00:00:00+00:00",
        "action": "summarize",
    }
    assert metadata["auth"] == {"authenticated": True}
    assert "prompt" not in metadata


def test_rag_metadata_drops_scalar_retrieval_results():
    metadata = TraceMetadataSanitizer.sanitize_span_metadata(
        "llmNode",
        {
            "rag": {
                "retrieval_results": "raw chunk text",
                "retrieved_context_payload_id": "payload-1",
            }
        },
    )

    assert metadata["rag"] == {"retrieved_context_payload_id": "payload-1"}


def test_llm_span_metadata_preserves_model_routing_summary_only():
    metadata = TraceMetadataSanitizer.sanitize_span_metadata(
        "llmNode",
        {
            "llm": {
                "recommendation_type": "user_click_model_routing",
                "analysis_stage": "optimized",
                "recommended_model": "gpt-4.1-mini",
                "recommended_fallback_model": "gpt-4.1",
                "reason": "최근 운영 로그의 품질 gate를 통과했습니다.",
                "policy_version": "model-router-v1",
                "confidence": 0.9,
                "raw_prompt": "secret prompt",
                "api_key": "sk-secret",
            }
        },
    )

    assert metadata["llm"] == {
        "recommendation_type": "user_click_model_routing",
        "analysis_stage": "optimized",
        "recommended_model": "gpt-4.1-mini",
        "recommended_fallback_model": "gpt-4.1",
        "reason": "최근 운영 로그의 품질 gate를 통과했습니다.",
        "policy_version": "model-router-v1",
        "confidence": 0.9,
    }


def test_llm_span_metadata_preserves_current_routing_fields_without_sensitive_values():
    metadata = TraceMetadataSanitizer.sanitize_span_metadata(
        "llmNode",
        {
            "llm": {
                "finish_reason": "stop",
                "schema_status": "passed",
                "repetition_rate": 0.125,
                "downstream_status": "passed",
                "fallback_used": True,
                "policy_id": "policy-1",
                "policy_version": "bootstrap-1234",
                "strategy_id": "bootstrap_task_complexity_v2",
                "selected_model": "gpt-4.1-mini",
                "fallback_model": "gpt-4.1",
                "matched_rule_id": "task-complexity-balanced",
                "decision_source": "active_policy",
                "reason_code": "bootstrap_task_complexity_balanced",
                "judge_called": False,
                "input_length_bucket": "short",
                "prompt_length_bucket": "medium",
                "output_format": "json",
                "schema_required": True,
                "knowledge_enabled": False,
                "raw_input": "secret input",
                "raw_prompt": "secret prompt",
            }
        },
    )

    assert metadata["llm"]["fallback_used"] is True
    assert metadata["llm"]["finish_reason"] == "stop"
    assert metadata["llm"]["repetition_rate"] == 0.125
    assert metadata["llm"]["input_length_bucket"] == "short"
    assert metadata["llm"]["matched_rule_id"] == "task-complexity-balanced"
    assert metadata["llm"]["decision_source"] == "active_policy"
    assert metadata["llm"]["strategy_id"] == "bootstrap_task_complexity_v2"
    assert metadata["llm"]["selected_model"] == "gpt-4.1-mini"
    assert "raw_input" not in metadata["llm"]
    assert "raw_prompt" not in metadata["llm"]


def test_llm_span_metadata_preserves_safe_runtime_judge_summary_only():
    metadata = TraceMetadataSanitizer.sanitize_span_metadata(
        "llmNode",
        {
            "llm": {
                "strategy_id": "judge_bootstrap_incremental_v1",
                "judge_called": True,
                "judge": {
                    "model": "gpt-5-mini",
                    "selected_model": "gpt-4o-mini",
                    "confidence": 0.87,
                    "reason_short": "여러 조건 종합",
                    "reason_code": "simple_request",
                    "cost": 0.00012,
                    "usage": {
                        "prompt_tokens": 120,
                        "completion_tokens": 30,
                        "latency_ms": 1432,
                        "raw_content": "customer secret",
                    },
                    "raw_prompt": "sensitive prompt",
                },
                "decision_factors": {
                    "learning_mode": "judge_first",
                    "judged_request_count": 7,
                    "local_confidence": 0.42,
                    "raw_input": "sensitive input",
                },
            }
        },
    )

    assert metadata["llm"]["judge"] == {
        "model": "gpt-5-mini",
        "selected_model": "gpt-4o-mini",
        "confidence": 0.87,
        "reason_short": "단순 요청 적합",
        "reason_code": "simple_request",
        "cost": 0.00012,
        "usage": {
            "prompt_tokens": 120,
            "completion_tokens": 30,
            "latency_ms": 1432,
        },
    }
    assert metadata["llm"]["decision_factors"] == {
        "learning_mode": "judge_first",
        "judged_request_count": 7,
        "local_confidence": 0.42,
    }


@pytest.mark.parametrize(
    "reason_short",
    [
        "english only",
        "한글 이유가 너무 길어서 허용된 열네 글자를 초과합니다",
        "한글 이유\n원문",
        "키sk-abc123",
    ],
)
def test_llm_span_metadata_drops_untrusted_runtime_judge_reason_short(reason_short):
    metadata = TraceMetadataSanitizer.sanitize_span_metadata(
        "llmNode",
        {"llm": {"judge": {"reason_code": "safe_code", "reason_short": reason_short}}},
    )

    assert metadata["llm"]["judge"] == {
        "reason_code": "judge_reason_unrecognized"
    }


def test_llm_span_metadata_uses_canonical_reason_instead_of_judge_text():
    metadata = TraceMetadataSanitizer.sanitize_span_metadata(
        "llmNode",
        {
            "llm": {
                "judge": {
                    "reason_code": "evidence_synthesis",
                    "reason_short": "키sk-abc123",
                }
            }
        },
    )

    assert metadata["llm"]["judge"] == {
        "reason_code": "evidence_synthesis",
        "reason_short": "근거 종합 판단",
    }
    assert "sk-abc123" not in str(metadata)


def test_llm_span_metadata_drops_all_runtime_judge_selection_explanations():
    metadata = TraceMetadataSanitizer.sanitize_span_metadata(
        "llmNode",
        {
            "llm": {
                "judge": {
                    "reason_code": "evidence_synthesis",
                    "selection_explanation": "홍길동 고객의 010-1234-5678 계정 계약을 확인해야 합니다.",
                }
            }
        },
    )

    assert "selection_explanation" not in metadata["llm"]["judge"]
    assert "홍길동" not in str(metadata)
    assert "010-1234-5678" not in str(metadata)


def test_llm_span_metadata_keeps_only_safe_runtime_judge_reason_factors():
    metadata = TraceMetadataSanitizer.sanitize_span_metadata(
        "llmNode",
        {
            "llm": {
                "judge": {
                    "reason_code": "high_risk_reasoning",
                    "reason_factors": [
                        "high_decision_impact",
                        "security_or_compliance_risk",
                        "홍길동 계정 차단",
                        "high_decision_impact",
                    ],
                }
            }
        },
    )

    assert metadata["llm"]["judge"] == {
        "reason_code": "high_risk_reasoning",
        "reason_short": "고위험 판단 필요",
        "reason_factors": [
            "high_decision_impact",
            "security_or_compliance_risk",
        ],
    }
    assert "홍길동" not in str(metadata)


def test_llm_span_metadata_preserves_safe_provider_fallback_diagnostics_only():
    metadata = TraceMetadataSanitizer.sanitize_span_metadata(
        "llmNode",
        {
            "llm": {
                "fallback_provider_error_code": "responses_incomplete",
                "fallback_provider_error_type": "ValueError",
                "fallback_provider_status_code": 429,
                "fallback_provider_remote_error_code": "unsupported_parameter",
                "fallback_provider_remote_error_param": "reasoning.effort",
                "provider_error_message": "raw provider detail must not persist",
            }
        },
    )

    assert metadata["llm"] == {
        "fallback_provider_error_code": "responses_incomplete",
        "fallback_provider_error_type": "ValueError",
        "fallback_provider_status_code": 429,
        "fallback_provider_remote_error_code": "unsupported_parameter",
        "fallback_provider_remote_error_param": "reasoning.effort",
    }


def test_llm_span_metadata_preserves_safe_model_routing_decision_factors_only():
    metadata = TraceMetadataSanitizer.sanitize_span_metadata(
        "llmNode",
        {
            "llm": {
                "strategy_id": "prior_guided_adaptive_v1",
                "selected_model": "gpt-4.1-mini",
                "decision_factors": {
                    "profile": "short",
                    "evaluated_candidate_count": 3,
                    "excluded_candidate_count": 1,
                    "selected_model_score": {
                        "quality_lower_bound": 0.92,
                        "expected_total_cost_usd": 0.00042,
                        "expected_latency_ms": 640,
                        "expected_fallback_rate": 0.01,
                        "effective_evidence_samples": 12,
                        "prior_source": "model_catalog_family_prior",
                        "raw_input": "민감한 원문",
                    },
                    "constraint_signature": {
                        "context_input_bucket": "short",
                        "output_contract": "json_schema",
                        "schema_required": True,
                        "raw_query": "민감한 질의",
                    },
                    "raw_prompt": "민감한 프롬프트",
                },
            }
        },
    )

    assert metadata["llm"]["strategy_id"] == "prior_guided_adaptive_v1"
    assert metadata["llm"]["decision_factors"] == {
        "profile": "short",
        "evaluated_candidate_count": 3,
        "excluded_candidate_count": 1,
        "selected_model_score": {
            "quality_lower_bound": 0.92,
            "expected_total_cost_usd": 0.00042,
            "expected_latency_ms": 640,
            "expected_fallback_rate": 0.01,
            "effective_evidence_samples": 12,
            "prior_source": "model_catalog_family_prior",
        },
        "constraint_signature": {
            "context_input_bucket": "short",
            "output_contract": "json_schema",
            "schema_required": True,
        },
    }
    assert "민감한" not in str(metadata)


def test_llm_span_metadata_preserves_safe_bootstrap_difficulty_summary_only():
    """Bootstrap 난이도 라우팅의 판정값은 보이되, 입력 원문/매칭 문구는 숨긴다."""
    metadata = TraceMetadataSanitizer.sanitize_span_metadata(
        "llmNode",
        {
            "llm": {
                "strategy_id": "bootstrap_mdeberta_difficulty_v1",
                "matched_rule_id": "planner-advanced-policy-conflict",
                "selected_model": "gpt-5.4",
                "decision_factors": {
                    "classification_status": "planner_rule",
                    "difficulty": "advanced",
                    "confidence": 0.84,
                    "minimum_confidence": 0.0,
                    "matched_signal_count": 2,
                    "match_score": 4,
                    "probabilities": {
                        "economy": 0.05,
                        "balanced": 0.16,
                        "advanced": 0.79,
                    },
                    "matched_terms": ["개인정보", "충돌"],
                    "raw_input": "민감한 사용자 문의 원문",
                },
            }
        },
    )

    assert metadata["llm"]["decision_factors"] == {
        "classification_status": "planner_rule",
        "difficulty": "advanced",
        "confidence": 0.84,
        "minimum_confidence": 0.0,
        "matched_signal_count": 2,
        "match_score": 4,
        "probabilities": {
            "economy": 0.05,
            "balanced": 0.16,
            "advanced": 0.79,
        },
    }
    assert "개인정보" not in str(metadata)
    assert "민감한" not in str(metadata)


def test_rag_metadata_preserves_payload_reference_and_summarizes_evidence():
    metadata = TraceMetadataSanitizer.sanitize_span_metadata(
        "llmNode",
        {
            "rag": {
                "retrieval_payload_id": "payload-2",
                "retrieval_results": [
                    {
                        "knowledge_base_id": "kb-1",
                        "chunk_id": "chunk-1",
                        "parent_chunk_id": "parent-1",
                        "document_id": "doc-1",
                        "filename": "sensitive-title.pdf",
                        "rank": 1,
                        "evidence_rank": 1,
                        "similarity_score": 0.9,
                        "score": 0.91,
                        "token_count": 210,
                        "metadata_summary": {
                            "classification": "internal",
                            "source_path": "/private/source/path",
                            "raw_source_url": "https://internal.example/private",
                        },
                        "hierarchy_fallback": True,
                        "content": "raw chunk text",
                    }
                ],
            }
        },
    )

    assert metadata["rag"]["retrieval_payload_id"] == "payload-2"
    assert metadata["rag"]["knowledge_base_id"] == "kb-1"
    assert metadata["rag"]["retrieved_chunk_count"] == 1
    assert metadata["rag"]["document_ids"] == ["doc-1"]
    assert metadata["rag"]["citation_ids"] == ["chunk-1"]
    assert metadata["rag"]["score_summary"] == {"min": 0.91, "max": 0.91}
    assert metadata["rag"]["hierarchy_fallback"] is True
    assert metadata["rag"]["raw_content_returned"] is False
    assert "retrieval_results" not in metadata["rag"]
    assert "raw chunk text" not in str(metadata)
    assert "sensitive-title.pdf" not in str(metadata)
    assert "internal.example" not in str(metadata)
    assert "/private/source/path" not in str(metadata)


def test_rag_result_sanitizer_keeps_global_evidence_rank_only_by_allowlist():
    metadata = TraceMetadataSanitizer.sanitize_rag_metadata(
        {
            "evidence_rank": 2,
            "rank": 1,
            "child_kb_rank": 1,
        }
    )

    assert metadata == {"evidence_rank": 2, "rank": 1}


@pytest.mark.parametrize("invalid_rank", [0, -1, True, 1.5, "1"])
def test_rag_result_sanitizer_drops_invalid_global_evidence_rank(invalid_rank):
    metadata = TraceMetadataSanitizer.sanitize_rag_metadata(
        {"evidence_rank": invalid_rank, "score": 0.9}
    )

    assert metadata == {"score": 0.9}


def test_rag_span_metadata_preserves_evidence_summary_fields_only():
    metadata = TraceMetadataSanitizer.sanitize_span_metadata(
        "llmNode",
        {
            "rag": {
                "evidence_sufficient": False,
                "insufficiency_reason": "minimum_score_not_met",
                "source_tier_used": "company_policy",
                "partial_result": True,
                "failed_candidate_count_bucket": "2-10",
                "failure_policy": "safe_no_result",
                "stored_result_count": 20,
                "retrieved_chunk_summary_truncated": True,
                "retrieval_strategy": "permission_scoped_hierarchical_hybrid",
                "rag_mode": "explicit_kb",
                "authorized_kb_count": 2,
                "authorized_kb_count_bucket": "2-10",
                "selected_kb_count": 1,
                "selected_kb_count_bucket": "1",
                "retrieved_chunk_count": 3,
                "context_token_estimate": 123,
                "permission_filter_applied": True,
                "safe_exclusion_summary": {
                    "operational_failure_count_bucket": "1"
                },
                "query_rewrite_applied": False,
                "query_rewrite_strategy": "off",
                "source_tier_policy": "tie_break",
                "hidden_candidate_ids": ["kb-hidden"],
                "raw_rewritten_query": "raw query",
            }
        },
    )

    assert metadata["rag"]["evidence_sufficient"] is False
    assert metadata["rag"]["insufficiency_reason"] == "minimum_score_not_met"
    assert metadata["rag"]["source_tier_used"] == "company_policy"
    assert metadata["rag"]["partial_result"] is True
    assert metadata["rag"]["failed_candidate_count_bucket"] == "2-10"
    assert metadata["rag"]["failure_policy"] == "safe_no_result"
    assert metadata["rag"]["stored_result_count"] == 20
    assert metadata["rag"]["retrieved_chunk_summary_truncated"] is True
    assert (
        metadata["rag"]["retrieval_strategy"]
        == "permission_scoped_hierarchical_hybrid"
    )
    assert metadata["rag"]["rag_mode"] == "explicit_kb"
    assert metadata["rag"]["authorized_kb_count"] == 2
    assert metadata["rag"]["authorized_kb_count_bucket"] == "2-10"
    assert metadata["rag"]["selected_kb_count"] == 1
    assert metadata["rag"]["selected_kb_count_bucket"] == "1"
    assert metadata["rag"]["retrieved_chunk_count"] == 3
    assert metadata["rag"]["context_token_estimate"] == 123
    assert metadata["rag"]["permission_filter_applied"] is True
    assert metadata["rag"]["safe_exclusion_summary"] == {
        "operational_failure_count_bucket": "1"
    }
    assert metadata["rag"]["query_rewrite_applied"] is False
    assert metadata["rag"]["query_rewrite_strategy"] == "off"
    assert metadata["rag"]["source_tier_policy"] == "tie_break"
    assert "hidden_candidate_ids" not in metadata["rag"]
    assert "raw_rewritten_query" not in metadata["rag"]


def test_rag_span_metadata_allows_only_bounded_integer_stage_latencies():
    metadata = TraceMetadataSanitizer.sanitize_span_metadata(
        "llmNode",
        {
            "rag": {
                "candidate_resolution_latency_ms": 0,
                "query_embedding_latency_ms": 12,
                "retrieval_fanout_latency_ms": 34,
                "slowest_search_latency_ms": 56,
                "evidence_policy_latency_ms": 300_000,
                "per_kb_latency_ms": {"hidden-resource": 99},
            }
        },
    )

    assert metadata["rag"] == {
        "candidate_resolution_latency_ms": 0,
        "query_embedding_latency_ms": 12,
        "retrieval_fanout_latency_ms": 34,
        "slowest_search_latency_ms": 56,
        "evidence_policy_latency_ms": 300_000,
    }


@pytest.mark.parametrize(
    "invalid_value",
    [True, -1, 1.5, "12", float("nan"), float("inf"), 300_001],
)
def test_rag_span_metadata_drops_invalid_stage_latency(invalid_value):
    metadata = TraceMetadataSanitizer.sanitize_span_metadata(
        "llmNode",
        {"rag": {"retrieval_fanout_latency_ms": invalid_value}},
    )

    assert metadata.get("rag", {}) == {}


def test_trace_detail_metadata_view_hides_error_message_and_sanitizes_metadata():
    run = SimpleNamespace(
        id=uuid.uuid4(),
        workflow_id=uuid.uuid4(),
        app_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        deployment_id=None,
        status="failed",
        trigger_mode="api",
        started_at=None,
        finished_at=None,
        duration=None,
        total_tokens=0,
        total_cost=0,
        redaction_applied=True,
        pii_detected=False,
        payload_storage_mode="redacted_only",
        inputs={},
        outputs={},
        error_message="redacted compatibility error",
        trace_metadata={
            "gateway": {
                "status_code": 500,
                "response": {"body": "raw response"},
            }
        },
    )

    detail = TraceQueryService.trace_detail(run, view_level="metadata")

    assert detail["error_message"] is None
    assert detail["trace_metadata"] == {"gateway": {"status_code": 500}}
