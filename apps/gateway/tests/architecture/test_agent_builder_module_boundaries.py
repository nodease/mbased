from pathlib import Path


ROOT = Path(__file__).resolve().parents[4]


def test_agent_builder_endpoint_keeps_graph_and_task_business_logic_out():
    source = (
        ROOT / "apps/gateway/api/v1/endpoints/agent_builder.py"
    ).read_text(encoding="utf-8")

    assert "GraphMutationBuilder" not in source
    assert "ParameterTaskPlanner" not in source
    assert "DirectEditOrchestrator" not in source


def test_agent_builder_endpoint_uses_the_production_composition_root():
    endpoint = (
        ROOT / "apps/gateway/api/v1/endpoints/agent_builder.py"
    ).read_text(encoding="utf-8")
    composition = (
        ROOT / "apps/gateway/composition/agent_builder.py"
    ).read_text(encoding="utf-8")

    assert "apps.gateway.composition.agent_builder" in endpoint
    assert "AgentBuilderService(" not in endpoint
    assert "LLMAgentBuilderIntentExtractor" not in endpoint
    assert "GraphMutationLifecycleService(" not in endpoint
    assert "ParameterTaskService(" not in endpoint
    assert "KnowledgeSelectionService(" not in endpoint
    assert "apps.gateway.api" not in composition
    assert "apps.client" not in composition
    assert "apps.workflow_engine" not in composition


def test_workflow_result_group_owns_the_shared_knowledge_selection_control():
    panel = (
        ROOT
        / "apps/client/app/features/workflow/components/agentBuilder/AgentBuilderPanel.tsx"
    ).read_text(encoding="utf-8")
    result_group = (
        ROOT
        / "apps/client/app/features/workflow/components/agentBuilder/WorkflowResultGroup.tsx"
    ).read_text(encoding="utf-8")

    assert "from './WorkflowResultGroup'" in panel
    assert "<WorkflowResultGroup" in panel
    assert "from './KnowledgeSelectionControl'" in result_group
    assert "<KnowledgeSelectionControl" in result_group


def test_agent_builder_application_does_not_depend_on_frontend_or_runtime_nodes():
    application = ROOT / "apps/gateway/application/agent_builder"
    source = "\n".join(
        path.read_text(encoding="utf-8") for path in application.glob("*.py")
    )

    assert "apps.client" not in source
    assert "apps.workflow_engine.workflow.nodes" not in source
