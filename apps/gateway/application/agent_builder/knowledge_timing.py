from __future__ import annotations

from datetime import datetime
from uuid import UUID

from apps.gateway.application.agent_builder.graph_mutation_builder import (
    GraphMutationBuilder,
)
from apps.shared.schemas.agent_builder import (
    AgentBuilderKnowledgePlacement,
    AgentBuilderStructuredRequest,
    GraphMutation,
    GraphMutationCompletionContext,
)


def materialize_before_graph_plan(
    structured: AgentBuilderStructuredRequest,
    *,
    placement: AgentBuilderKnowledgePlacement,
    has_selection: bool,
) -> AgentBuilderStructuredRequest:
    if placement.timing != "before_graph" or placement.effect_kind != "insert_step":
        raise ValueError("before-graph placement is required")
    step_ids = {step.step_id for step in structured.planned_steps}
    required_refs = {
        placement.knowledge_step_id,
        placement.upstream_step_id,
        placement.downstream_step_id,
    }
    if None in required_refs or not required_refs.issubset(step_ids):
        raise ValueError("knowledge placement references are invalid")
    knowledge_step = next(
        step
        for step in structured.planned_steps
        if step.step_id == placement.knowledge_step_id
    )
    if knowledge_step.capability != "knowledge_backed_llm":
        raise ValueError("knowledge step capability is invalid")
    if len(required_refs) != 3:
        raise ValueError("knowledge placement topology is invalid")
    step_by_id = {step.step_id: step for step in structured.planned_steps}
    downstream_step = step_by_id[str(placement.downstream_step_id)]
    if (
        placement.upstream_step_id not in knowledge_step.depends_on
        or placement.knowledge_step_id not in downstream_step.depends_on
    ):
        raise ValueError("knowledge placement topology is invalid")
    if has_selection:
        return structured.model_copy(deep=True)
    if placement.empty_selection_bridge != "connect_upstream_to_downstream":
        raise ValueError("knowledge empty-selection bridge is invalid")

    planned_steps = []
    for step in structured.planned_steps:
        if step.step_id == placement.knowledge_step_id:
            continue
        depends_on = [
            placement.upstream_step_id
            if dependency == placement.knowledge_step_id
            else dependency
            for dependency in step.depends_on
        ]
        planned_steps.append(step.model_copy(update={"depends_on": depends_on}))
    required_capabilities = [
        capability
        for capability in structured.required_capabilities
        if capability not in {"knowledge_backed_llm", "knowledge_base"}
    ]
    return structured.model_copy(
        update={
            "planned_steps": planned_steps,
            "required_capabilities": required_capabilities,
        },
        deep=True,
    )


class KnowledgeTimingResolver:
    def build_after_graph_mutation(
        self,
        *,
        operation_id: UUID,
        workflow_id: UUID,
        graph: dict,
        workflow_updated_at: datetime,
        target_node_id: str,
        selected_knowledge_bases: list[dict[str, str]],
        resolution_id: str,
        selected_knowledge_collections: list[dict[str, str]] | None = None,
    ) -> GraphMutation:
        target = next(
            (
                node
                for node in graph.get("nodes") or []
                if str(node.get("id")) == target_node_id
            ),
            None,
        )
        if target is None or target.get("type") != "llmNode":
            raise ValueError("knowledge binding target is invalid")
        data = dict(target.get("data") or {})
        # Workflow graph references always carry a safe display name as well as
        # the ID. Omitting the name makes the binding fail graph validation at
        # the draft-save boundary after an otherwise successful selection.
        knowledge_bases_by_id: dict[str, dict[str, str]] = {}
        for reference in selected_knowledge_bases:
            knowledge_base_id = str(reference.get("id") or "")
            name = str(reference.get("name") or "")
            if not knowledge_base_id or not name:
                raise ValueError("knowledge binding reference is invalid")
            knowledge_bases_by_id.setdefault(
                knowledge_base_id,
                {"id": knowledge_base_id, "name": name},
            )
        data["knowledgeBases"] = list(knowledge_bases_by_id.values())
        knowledge_collections_by_id: dict[str, dict[str, str]] = {}
        for reference in selected_knowledge_collections or []:
            collection_id = str(reference.get("id") or "")
            name = str(reference.get("name") or "")
            if not collection_id or not name:
                raise ValueError("knowledge collection reference is invalid")
            knowledge_collections_by_id.setdefault(
                collection_id,
                {"id": collection_id, "safeLabel": name},
            )
        data["knowledgeCollections"] = list(knowledge_collections_by_id.values())
        return GraphMutationBuilder().build(
            operation_id=operation_id,
            kind="knowledge_binding",
            generation_mode="configure_and_generate",
            workflow_id=workflow_id,
            base_graph=graph,
            expected_workflow_updated_at=workflow_updated_at,
            operations=[
                {
                    "op": "replace_node_data",
                    "node_id": target_node_id,
                    "data": data,
                }
            ],
            completion_context=GraphMutationCompletionContext(
                knowledge_resolution_id=str(resolution_id)
            ),
        )
