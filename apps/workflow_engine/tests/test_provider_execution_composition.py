from __future__ import annotations

import pytest

from apps.workflow_engine.adapters.provider_execution import (
    ProviderExecutionRuntimeRouter,
)
from apps.workflow_engine.adapters.provider_execution_capability import (
    CapabilityProviderExecutionAdapter,
)
from apps.workflow_engine.adapters.provider_execution_legacy import (
    LegacyProviderExecutionAdapter,
)
from apps.workflow_engine.adapters.provider_usage import (
    PostgresProviderUsageRecorder,
)
from apps.workflow_engine.adapters.query_embedding import (
    QueryEmbeddingExecutionRuntimeRouter,
)
from apps.workflow_engine.adapters.query_embedding_capability import (
    CapabilityQueryEmbeddingAdapter,
)
from apps.workflow_engine.adapters.query_embedding_legacy import (
    LegacyQueryEmbeddingAdapter,
)
from apps.workflow_engine.adapters.query_embedding_model_projection import (
    PostgresQueryEmbeddingModelProjection,
)
from apps.workflow_engine.application.provider_execution import (
    ProviderExecutionConfigurationError,
)
from apps.workflow_engine.composition.provider_execution import (
    build_provider_execution_runtime,
    build_provider_usage_recorder,
    build_query_embedding_runtime,
)
from apps.workflow_engine.application.query_embedding_execution import (
    QueryEmbeddingConfigurationError,
    QueryEmbeddingExecutionService,
)
from apps.workflow_engine.workflow.core.runtime_dependencies import (
    WorkflowRuntimeDependencies,
)
from apps.workflow_engine.workflow.core.workflow_engine import WorkflowEngine
from apps.workflow_engine.workflow.nodes.llm.entities import LLMNodeData
from apps.workflow_engine.workflow.nodes.llm.llm_node import LLMNode


def test_direct_llm_node_fails_closed_without_composed_provider_runtime():
    node = LLMNode(
        "llm-1",
        LLMNodeData(title="LLM", model_id="gpt-safe", user_prompt="query"),
    )

    with pytest.raises(ProviderExecutionConfigurationError):
        node._get_provider_execution_runtime()

    with pytest.raises(QueryEmbeddingConfigurationError):
        node._get_query_embedding_runtime()


def test_provider_composition_does_not_open_sessions_eagerly():
    calls = []

    def session_factory():
        calls.append("opened")
        raise AssertionError("composition must not open a session eagerly")

    runtime = build_provider_execution_runtime(session_factory=session_factory)
    query_runtime = build_query_embedding_runtime(session_factory=session_factory)
    recorder = build_provider_usage_recorder(session_factory=session_factory)

    assert isinstance(runtime, ProviderExecutionRuntimeRouter)
    assert isinstance(runtime._legacy_strategy, LegacyProviderExecutionAdapter)
    assert isinstance(runtime._capability_strategy, CapabilityProviderExecutionAdapter)
    assert isinstance(query_runtime, QueryEmbeddingExecutionService)
    assert isinstance(
        query_runtime._model_projection,
        PostgresQueryEmbeddingModelProjection,
    )
    provider_runtime = query_runtime._provider_runtime
    assert isinstance(provider_runtime, QueryEmbeddingExecutionRuntimeRouter)
    assert isinstance(provider_runtime._legacy_strategy, LegacyQueryEmbeddingAdapter)
    assert isinstance(
        provider_runtime._capability_strategy,
        CapabilityQueryEmbeddingAdapter,
    )
    assert isinstance(recorder, PostgresProviderUsageRecorder)
    assert calls == []


def test_engine_injects_provider_ports_without_serializing_them():
    class FakeRuntime:
        pass

    class FakeRecorder:
        pass

    class FakeQueryEmbeddingRuntime:
        pass

    runtime = FakeRuntime()
    recorder = FakeRecorder()
    query_runtime = FakeQueryEmbeddingRuntime()
    engine = WorkflowEngine(
        graph={
            "nodes": [
                {
                    "id": "start-1",
                    "type": "startNode",
                    "position": {"x": 0, "y": 0},
                    "data": {"title": "Start"},
                },
                {
                    "id": "llm-1",
                    "type": "llmNode",
                    "position": {"x": 200, "y": 0},
                    "data": {
                        "title": "LLM",
                        "provider": "openai",
                        "model_id": "gpt-4o",
                        "user_prompt": "query",
                    },
                },
            ],
            "edges": [
                {
                    "id": "edge-1",
                    "source": "start-1",
                    "target": "llm-1",
                }
            ],
        },
        runtime_dependencies=WorkflowRuntimeDependencies(
            provider_execution_runtime=runtime,
            provider_usage_recorder=recorder,
            query_embedding_runtime=query_runtime,
        ),
    )

    node = engine.node_instances["llm-1"]
    assert node._provider_execution_runtime is runtime
    assert node._provider_usage_recorder is recorder
    assert node._query_embedding_runtime is query_runtime
    assert "provider_execution_runtime" not in engine.execution_context
    assert "provider_usage_recorder" not in engine.execution_context
    assert "query_embedding_runtime" not in engine.execution_context
