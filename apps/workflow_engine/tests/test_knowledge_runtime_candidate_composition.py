import ast
import uuid
from pathlib import Path

from apps.workflow_engine.adapters.knowledge_runtime_candidates import (
    PostgresKnowledgeRuntimeCandidateSnapshotAdapter,
)
from apps.workflow_engine.application.runtime_retrieval.knowledge_candidates import (
    KnowledgeRuntimeCandidateResolver,
)
from apps.workflow_engine.composition.runtime_retrieval import (
    build_knowledge_runtime_candidate_resolver,
)
from apps.workflow_engine.workflow.core.runtime_dependencies import (
    WorkflowRuntimeDependencies,
)
from apps.workflow_engine.workflow.core.workflow_engine import WorkflowEngine


def test_composition_builds_runtime_resolver_without_opening_session():
    calls = []

    def session_factory():
        calls.append("opened")
        raise AssertionError("composition must not open a snapshot eagerly")

    resolver = build_knowledge_runtime_candidate_resolver(
        session_factory=session_factory
    )

    assert isinstance(resolver, KnowledgeRuntimeCandidateResolver)
    assert isinstance(
        resolver._snapshot_port,
        PostgresKnowledgeRuntimeCandidateSnapshotAdapter,
    )
    assert resolver._snapshot_port._session_factory is session_factory
    assert calls == []


def test_engine_injects_resolver_only_into_knowledge_llm_node():
    class FakeResolver:
        def resolve(self, request):  # pragma: no cover - binding-only test
            raise AssertionError("resolver must not run during composition")

    resolver = FakeResolver()
    collection_id = uuid.uuid4()
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
                        "knowledgeCollections": [
                            {
                                "id": str(collection_id),
                                "safeLabel": "Collection",
                            }
                        ],
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
            knowledge_runtime_candidate_resolver=resolver,
        ),
    )

    assert (
        engine.node_instances["llm-1"]._knowledge_runtime_candidate_resolver
        is resolver
    )
    assert not hasattr(
        engine.node_instances["start-1"],
        "_knowledge_runtime_candidate_resolver",
    )
    assert "knowledge_runtime_candidate_resolver" not in engine.execution_context


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def test_runtime_candidate_layers_never_import_gateway():
    root = Path(__file__).resolve().parents[3]
    paths = (
        root / "apps/shared/domain/knowledge_runtime_candidates.py",
        root
        / "apps/workflow_engine/application/runtime_retrieval/knowledge_candidates.py",
        root / "apps/workflow_engine/adapters/knowledge_runtime_candidates.py",
        root / "apps/workflow_engine/composition/runtime_retrieval.py",
    )

    for path in paths:
        assert not any(
            module == "apps.gateway" or module.startswith("apps.gateway.")
            for module in _imported_modules(path)
        ), path


def test_llm_runtime_does_not_restore_gateway_or_legacy_authorization_imports():
    root = Path(__file__).resolve().parents[3]
    llm_node_path = (
        root / "apps/workflow_engine/workflow/nodes/llm/llm_node.py"
    )
    imported_modules = _imported_modules(llm_node_path)
    source = llm_node_path.read_text(encoding="utf-8")

    assert not any(
        module == "apps.gateway" or module.startswith("apps.gateway.")
        for module in imported_modules
    )
    assert "KnowledgePermissionHelper" not in source
    assert "KnowledgeCollectionItem" not in source
    assert "KnowledgeCollection," not in source


def test_pure_candidate_policy_has_no_framework_or_runtime_imports():
    root = Path(__file__).resolve().parents[3]
    policy_path = root / "apps/shared/domain/knowledge_runtime_candidates.py"
    forbidden_roots = {
        "celery",
        "fastapi",
        "sqlalchemy",
        "apps.gateway",
        "apps.workflow_engine",
    }

    for module in _imported_modules(policy_path):
        assert not any(
            module == forbidden or module.startswith(f"{forbidden}.")
            for forbidden in forbidden_roots
        ), module
