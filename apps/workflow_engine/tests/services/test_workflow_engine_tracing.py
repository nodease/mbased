import sys
import types
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from apps.shared.schemas.workflow import NodeSchema


if "gevent" not in sys.modules:
    gevent_stub = types.ModuleType("gevent")
    gevent_stub.Timeout = TimeoutError
    gevent_stub.sleep = lambda seconds: None
    pool_stub = types.ModuleType("gevent.pool")
    pool_stub.Pool = object
    queue_stub = types.ModuleType("gevent.queue")
    queue_stub.Queue = list
    sys.modules["gevent"] = gevent_stub
    sys.modules["gevent.pool"] = pool_stub
    sys.modules["gevent.queue"] = queue_stub

if "pymupdf4llm" not in sys.modules:
    pymupdf_stub = types.ModuleType("pymupdf4llm")
    pymupdf_stub.to_markdown = lambda *args, **kwargs: ""
    sys.modules["pymupdf4llm"] = pymupdf_stub

from apps.workflow_engine.workflow.core.workflow_engine import WorkflowEngine
from apps.workflow_engine.workflow.core.workflow_logger import WorkflowLogger
from apps.workflow_engine.workflow.errors import NonRetryableWorkflowError


def _engine_without_init():
    return object.__new__(WorkflowEngine)


def test_invalid_trigger_stops_before_start_node_execution():
    engine = _engine_without_init()
    engine.execution_context = {
        "workflow_id": str(uuid.uuid4()),
        "user_id": str(uuid.uuid4()),
        "trigger_mode": "unknown-secret-like-trigger",
    }
    engine.user_input = {"secret_like_value": "must-not-appear"}
    engine.is_deployed = True
    engine.is_subworkflow = False
    engine.parent_run_id = None
    engine.logger = WorkflowLogger()
    engine._find_start_node = Mock(
        side_effect=AssertionError("start node must not be resolved")
    )

    with pytest.raises(
        NonRetryableWorkflowError,
        match="^workflow run trigger mode is invalid$",
    ):
        next(engine._execute_core(stream_mode=False))

    engine._find_start_node.assert_not_called()
    assert engine.logger.workflow_run_id is None


def test_mail_sensitive_lineage_includes_every_graph_descendant():
    engine = _engine_without_init()
    engine.node_schemas = {
        "start": SimpleNamespace(type="startNode"),
        "mail": SimpleNamespace(type="mailNode"),
        "template": SimpleNamespace(type="templateNode"),
        "answer": SimpleNamespace(type="answerNode"),
        "other": SimpleNamespace(type="llmNode"),
    }
    engine.adjacency_list = {
        "start": ["mail", "other"],
        "mail": ["template"],
        "template": ["answer"],
    }
    engine.data_dependencies = {}

    assert engine._descendants_of_type("mailNode") == {
        "mail",
        "template",
        "answer",
    }


def test_mail_sensitive_lineage_includes_selector_only_data_dependencies():
    engine = _engine_without_init()
    engine.node_schemas = {
        "mail": SimpleNamespace(type="mailNode"),
        "llm": SimpleNamespace(type="llmNode"),
        "answer": SimpleNamespace(type="answerNode"),
    }


def test_dependency_extraction_supports_all_selector_field_shapes():
    engine = _engine_without_init()
    engine.node_schemas = {
        "mail": SimpleNamespace(type="mailNode"),
        "draft": SimpleNamespace(type="gmailDraftNode"),
        "other": SimpleNamespace(type="templateNode"),
    }
    schema = SimpleNamespace(
        data={
            "variable_selector": ["mail", "emails"],
            "source_selector": ["other", "text"],
            "required_effect_ref_selectors": [["draft", "draft_ref"]],
        }
    )

    assert engine._extract_value_selectors(schema) == {"mail", "draft", "other"}
    engine.adjacency_list = {"llm": ["answer"]}
    engine.data_dependencies = {"llm": {"mail"}}

    assert engine._descendants_of_type("mailNode") == {
        "mail",
        "llm",
        "answer",
    }


def test_error_trace_metadata_excludes_raw_error_message():
    engine = _engine_without_init()
    started_at = datetime.now(timezone.utc)
    finished_at = datetime.now(timezone.utc)

    metadata = engine._build_error_trace_metadata(
        "modulyGuardrailNode",
        started_at,
        finished_at,
        RuntimeError("secret prompt token leaked"),
    )

    assert metadata["error"]["error_type"] == "RuntimeError"
    assert metadata["error"]["error_code"] == "node_error"
    assert "message" not in metadata["error"]
    assert "secret prompt token" not in str(metadata)
    assert metadata["guardrail"]["reason_redacted"] == "node_error"


def test_error_trace_metadata_keeps_only_safe_failure_phase():
    engine = _engine_without_init()
    error = NonRetryableWorkflowError("provider_outcome_unknown")
    error.code = "provider_outcome_unknown"
    error.failure_phase = "outcome_unknown"

    metadata = engine._build_error_trace_metadata(
        "llmNode",
        datetime.now(timezone.utc),
        datetime.now(timezone.utc),
        error,
    )

    assert metadata["error"] == {
        "type": "node_error",
        "error_type": "NonRetryableWorkflowError",
        "error_code": "provider_outcome_unknown",
        "failure_phase": "outcome_unknown",
    }


def test_external_effect_error_trace_keeps_only_safe_provider_summary():
    engine = _engine_without_init()
    started_at = datetime.now(timezone.utc)
    node = SimpleNamespace(
        _trace_metadata={
            "http": {
                "method": "POST",
                "status_code": 403,
                "response_size": 17,
                "latency_ms": 12,
                "body": "opaque-customer-response",
            },
            "external_effect": {
                "provider": "github",
                "operation": "github.issue_comment.create",
                "outcome": "failed_before_effect",
                "replay_decision": "stop",
                "error_code": "provider_rejected_request",
            },
        }
    )

    metadata = engine._build_error_trace_metadata(
        "githubNode",
        started_at,
        datetime.now(timezone.utc),
        RuntimeError("raw provider failure"),
        node_instance=node,
    )

    assert metadata["http"] == {
        "method": "POST",
        "status_code": 403,
        "response_size": 17,
        "latency_ms": 12,
    }
    assert metadata["external_effect"] == {
        "provider": "github",
        "operation": "github.issue_comment.create",
        "outcome": "failed_before_effect",
        "replay_decision": "stop",
        "error_code": "provider_rejected_request",
    }
    assert "opaque-customer-response" not in str(metadata)
    assert "raw provider failure" not in str(metadata)


def test_run_log_outputs_remove_provider_results_and_downstream_copies():
    engine = _engine_without_init()
    engine.node_schemas = {
        "http": SimpleNamespace(type="httpRequestNode", data={"method": "POST"}),
        "template": SimpleNamespace(type="templateNode", data={}),
        "answer": SimpleNamespace(type="answerNode", data={}),
        "other": SimpleNamespace(type="templateNode", data={}),
    }
    engine.node_instances = {
        "http": SimpleNamespace(
            _trace_metadata={
                "http": {
                    "method": "POST",
                    "status_code": 200,
                    "response_size": 27,
                },
                "external_effect": {
                    "provider": "generic_http",
                    "operation": "generic_http.request",
                    "outcome": "succeeded",
                    "replay_decision": "result_unavailable",
                },
            }
        )
    }
    engine.adjacency_list = {"http": ["template"], "template": ["answer"]}
    engine.data_dependencies = {}
    engine.nodes_by_type = {"answerNode": ["answer"]}
    all_results = {
        "http": {"data": "opaque-provider-response"},
        "template": {"value": "copied-provider-response"},
        "answer": {"answer": "copied-provider-response"},
        "other": {"value": "safe-unrelated-output"},
    }

    durable_stream = engine._durable_run_outputs(
        all_results,
        all_results=all_results,
        stream_mode=True,
    )
    durable_answer = engine._durable_run_outputs(
        all_results["answer"],
        all_results=all_results,
        stream_mode=False,
    )

    assert durable_stream["http"]["external_effect"]["provider"] == "generic_http"
    assert durable_stream["template"] == {}
    assert durable_stream["answer"] == {}
    assert durable_stream["other"] == {"value": "safe-unrelated-output"}
    assert durable_answer == {}
    assert "opaque-provider-response" not in str(durable_stream)
    assert "copied-provider-response" not in str(durable_stream)


def test_run_log_outputs_remove_nested_workflow_provider_results():
    engine = _engine_without_init()
    engine.node_schemas = {
        "child": SimpleNamespace(type="workflowNode", data={}),
        "answer": SimpleNamespace(type="answerNode", data={}),
    }
    engine.node_instances = {
        "child": SimpleNamespace(
            _trace_metadata={"external_effect_output": {"sensitive": True}}
        )
    }
    engine.adjacency_list = {"child": ["answer"]}
    engine.data_dependencies = {}
    engine.nodes_by_type = {"answerNode": ["answer"]}
    all_results = {
        "child": {"result": "opaque-child-provider-response"},
        "answer": {"answer": "opaque-child-provider-response"},
    }

    durable = engine._durable_run_outputs(
        all_results,
        all_results=all_results,
        stream_mode=True,
    )

    assert durable == {"child": {}, "answer": {}}


def test_nested_workflow_provider_result_marks_parent_downstream_before_submit():
    engine = _engine_without_init()
    engine.node_schemas = {
        "child": SimpleNamespace(type="workflowNode", data={}),
        "template": SimpleNamespace(type="templateNode", data={}),
        "answer": SimpleNamespace(type="answerNode", data={}),
        "other": SimpleNamespace(type="templateNode", data={}),
    }
    engine.node_instances = {
        "child": SimpleNamespace(
            _trace_metadata={"external_effect_output": {"sensitive": True}}
        )
    }
    engine.adjacency_list = {
        "child": ["template"],
        "template": ["answer"],
    }
    engine.data_dependencies = {}
    engine._external_effect_sensitive_node_ids = set()

    engine._propagate_external_effect_output_sensitivity("child")

    assert engine._external_effect_sensitive_node_ids == {
        "child",
        "template",
        "answer",
    }
    assert "other" not in engine._external_effect_sensitive_node_ids


def test_node_trace_metadata_applies_allowlist_and_denylist():
    engine = _engine_without_init()
    node = SimpleNamespace(
        _trace_metadata={
            "http": {
                "method": "POST",
                "status_code": 200,
                "body": "secret-body",
                "headers": {"authorization": "secret-token"},
            },
            "custom": {"content": "raw content"},
        }
    )

    metadata = engine._build_node_trace_metadata(
        "httpRequestNode",
        node,
        result={"body": "raw response"},
        process_data={},
        started_at=datetime.now(timezone.utc),
        finished_at=datetime.now(timezone.utc),
    )

    assert metadata["http"]["method"] == "POST"
    assert metadata["http"]["status_code"] == 200
    assert "body" not in metadata["http"]
    assert "headers" not in metadata["http"]
    assert "custom" not in metadata
    assert "secret-body" not in str(metadata)
    assert "raw content" not in str(metadata)


def test_rag_metadata_keeps_source_fields_only():
    engine = _engine_without_init()
    node = SimpleNamespace(_trace_metadata={})

    metadata = engine._build_node_trace_metadata(
        "llmNode",
        node,
        result={
            "model": "test-model",
            "usage": {"prompt_tokens": 1, "completion_tokens": 2},
            "metadata": {
                "knowledge_search": [
                    {
                        "knowledge_base_id": "kb-1",
                        "chunk_id": "chunk-1",
                        "document_id": "doc-1",
                        "filename": "guide.pdf",
                        "page_number": 3,
                        "similarity_score": 0.8,
                        "score": 0.82,
                        "content": "raw chunk text",
                        "body": "raw body",
                    }
                ]
            },
        },
        process_data={"provider": "test", "model": "test-model"},
        started_at=datetime.now(timezone.utc),
        finished_at=datetime.now(timezone.utc),
    )

    assert metadata["rag"]["knowledge_base_id"] == "kb-1"
    assert metadata["rag"]["retrieved_chunk_count"] == 1
    assert metadata["rag"]["document_ids"] == ["doc-1"]
    assert metadata["rag"]["citation_ids"] == ["chunk-1"]
    assert metadata["rag"]["score_summary"] == {"min": 0.82, "max": 0.82}
    assert metadata["rag"]["raw_content_returned"] is False
    assert "retrieval_results" not in metadata["rag"]
    assert "raw chunk text" not in str(metadata)


def test_llm_trace_metadata_preserves_canonical_routing_and_rag_summaries():
    """추천/라우팅이 읽는 안전한 summary는 실제 node trace에도 남아야 한다."""
    engine = _engine_without_init()
    node = SimpleNamespace(_trace_metadata={})

    metadata = engine._build_node_trace_metadata(
        "llmNode",
        node,
        result={
            "model": "gpt-4.1-mini",
            "usage": {"prompt_tokens": 21, "completion_tokens": 13},
            "cost": 0.001,
            "metadata": {
                "finish_reason": "stop",
                "schema_status": "passed",
                "repetition_rate": 0.125,
                "model_routing": {
                    "policy_id": "policy-1",
                    "policy_version": "router-policy-v2",
                    "selected_model": "gpt-4.1",
                    "fallback_model": "gpt-4.1-mini",
                    "fallback_used": True,
                    "decision_source": "local_router",
                    "matched_rule_id": "incremental-local-router",
                    "strategy_id": "judge_bootstrap_incremental_v1",
                    "reason_code": "local_router_confident",
                    "decision_factors": {
                        "learning_mode": "local_first",
                        "local_confidence": 0.86,
                        "local_confidence_threshold": 0.78,
                        "candidate_model_count": 3,
                    },
                    "judge_called": False,
                    "judge": {
                        "status": "not_called",
                        "attempted": False,
                        "candidate_model_count": 3,
                        "not_called_reason": "test_policy_preview",
                    },
                    "runtime_context": {
                        "output_format": "json",
                        "schema_required": True,
                        "knowledge_enabled": True,
                        "input_length_bucket": "short",
                        "prompt_length_bucket": "medium",
                        "raw_input": "must not persist",
                    },
                },
                "rag": {
                    "context_token_estimate": 123,
                    "retrieved_chunk_count": 2,
                    "evidence_sufficient": True,
                    "raw_query": "must not persist",
                },
            },
        },
        process_data={"provider": "openai", "model": "gpt-4.1"},
        started_at=datetime.now(timezone.utc),
        finished_at=datetime.now(timezone.utc),
    )

    assert metadata["llm"]["finish_reason"] == "stop"
    assert metadata["llm"]["schema_status"] == "passed"
    assert metadata["llm"]["repetition_rate"] == 0.125
    assert metadata["llm"]["fallback_used"] is True
    assert metadata["llm"]["input_length_bucket"] == "short"
    assert metadata["llm"]["decision_factors"] == {
        "learning_mode": "local_first",
        "local_confidence": 0.86,
        "local_confidence_threshold": 0.78,
        "candidate_model_count": 3,
    }
    assert metadata["llm"]["judge"] == {
        "status": "not_called",
        "attempted": False,
        "candidate_model_count": 3,
        "not_called_reason": "test_policy_preview",
    }
    assert metadata["rag"]["context_token_estimate"] == 123
    assert metadata["rag"]["evidence_sufficient"] is True
    assert "raw_input" not in str(metadata)
    assert "raw_query" not in str(metadata)


def test_tuple_graph_is_supported_explicitly():
    node = NodeSchema(
        id="start-1",
        type="startNode",
        position={"x": 0, "y": 0},
        data={"title": "Start"},
    )

    engine = WorkflowEngine(graph=([node], []))

    assert engine.node_schemas["start-1"].type == "startNode"
    engine.cleanup()


def test_running_node_timeout_closes_span_without_raw_message():
    engine = _engine_without_init()
    engine.is_subworkflow = False
    engine.logger = Mock()
    running_nodes = {
        "node-1": {
            "log_id": uuid.uuid4(),
            "node_type": "httpRequestNode",
            "inputs": {"url": "https://example.test"},
            "process_data": {},
            "started_at": datetime.now(timezone.utc),
            "sequence": 1,
        }
    }

    engine._mark_running_nodes_timeout(
        running_nodes, TimeoutError("secret timeout detail")
    )

    assert running_nodes == {}
    call = engine.logger.update_node_log_error.call_args
    assert call.args[2] == "timeout"
    trace_metadata = call.kwargs["trace_metadata"]
    assert trace_metadata["error"]["error_code"] == "timeout"
    assert "secret timeout detail" not in str(trace_metadata)
