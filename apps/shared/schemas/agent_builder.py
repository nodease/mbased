from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from apps.shared.schemas.knowledge import (
    KnowledgeSelectionCollection,
    KnowledgeSelectionKBCandidate,
)
from apps.shared.schemas.workflow import EdgeSchema, NodeSchema, Position
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PrivateAttr,
    field_validator,
    model_validator,
)

AgentBuilderRequestStatus = Literal[
    "planning",
    "graph_mutation_ready",
    "parameter_configuration",
    "completed",
    "stale",
    "stale_protocol",
    "draft_ready",
    "clarification_required",
    "validation_failed",
    "unsupported",
    "configuration_required",
    "failed",
    "canceled",
]
AgentBuilderDraftMode = Literal["new_workflow", "modify_workflow", "replace_workflow"]
AgentBuilderApplyAction = Literal["apply_and_save", "cancel"]
AgentBuilderApplyOutcome = Literal["saved", "blocked", "canceled", "failed"]
AgentBuilderPendingSlotType = Literal["knowledge_base", "target", "capability", "other"]
AgentBuilderEditOperationType = Literal["insert"]
AgentBuilderEditPlacement = Literal["before", "after", "between"]
AgentBuilderTargetReferenceType = Literal[
    "natural_language_node",
    "natural_language_edge",
    "selected_node",
    "selected_edge",
]
AgentBuilderGenerationMode = Literal["configure_and_generate", "structure_only"]
ParameterTaskStatus = Literal[
    "completed", "pending", "active", "skipped", "deferred", "invalid", "canceled"
]
ParameterResolutionSource = Literal[
    "user_request", "existing_graph", "upstream_selector", "catalog_default"
]
GraphMutationKind = Literal[
    "initial_graph",
    "graph_edit",
    "replace_workflow",
    "parameter_update",
    "knowledge_binding",
]
GraphMutationStatus = Literal[
    "pending_apply",
    "pending_save",
    "pending_ack",
    "acknowledged",
    "blocked",
    "reverted",
]


class AddNodeOperation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    op: Literal["add_node"]
    node: NodeSchema


class RemoveNodeOperation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    op: Literal["remove_node"]
    node_id: str = Field(min_length=1, max_length=255)


class AddEdgeOperation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    op: Literal["add_edge"]
    edge: EdgeSchema


class RemoveEdgeOperation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    op: Literal["remove_edge"]
    edge_id: str = Field(min_length=1, max_length=255)


class ReplaceNodeDataOperation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    op: Literal["replace_node_data"]
    node_id: str = Field(min_length=1, max_length=255)
    data: dict[str, Any]


class ReplaceNodePositionOperation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    op: Literal["replace_node_position"]
    node_id: str = Field(min_length=1, max_length=255)
    position: Position


GraphOperation = Annotated[
    AddNodeOperation
    | RemoveNodeOperation
    | AddEdgeOperation
    | RemoveEdgeOperation
    | ReplaceNodeDataOperation
    | ReplaceNodePositionOperation,
    Field(discriminator="op"),
]


class GraphMutationCompletionContext(BaseModel):
    model_config = ConfigDict(extra="forbid")
    parameter_task_id: UUID | None = None
    knowledge_resolution_id: str | None = Field(default=None, max_length=255)

    @model_validator(mode="after")
    def only_one_completion_target(self):
        if sum(
            value is not None
            for value in (self.parameter_task_id, self.knowledge_resolution_id)
        ) > 1:
            raise ValueError("completion context accepts one target")
        return self


class GraphMutation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation_id: UUID
    kind: GraphMutationKind
    status: GraphMutationStatus = "pending_apply"
    generation_mode: AgentBuilderGenerationMode = "configure_and_generate"
    workflow_id: UUID
    base_graph_hash: str = Field(min_length=64, max_length=64)
    expected_workflow_updated_at: datetime
    expected_result_graph_hash: str = Field(min_length=64, max_length=64)
    catalog_version: Literal[3] = 3
    operations: list[GraphOperation]
    affected_node_ids: list[str] = Field(default_factory=list)
    completion_context: GraphMutationCompletionContext | None = None


class GraphMutationSafeEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation_id: UUID
    kind: GraphMutationKind
    status: GraphMutationStatus
    generation_mode: AgentBuilderGenerationMode
    workflow_id: UUID
    base_graph_hash: str
    expected_workflow_updated_at: datetime
    expected_result_graph_hash: str
    catalog_version: Literal[3] = 3
    affected_node_ids: list[str] = Field(default_factory=list)
    completion_context: GraphMutationCompletionContext | None = None
    result_graph_hash: str | None = Field(default=None, min_length=64, max_length=64)
    saved_workflow_updated_at: datetime | None = None
    blocked_reason: str | None = Field(default=None, max_length=120)

    @classmethod
    def from_mutation(cls, mutation: GraphMutation) -> "GraphMutationSafeEnvelope":
        return cls.model_validate(
            mutation.model_dump(exclude={"operations"})
        )


class GraphMutationAcknowledgementRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workflow_id: UUID
    graph_hash: str = Field(min_length=64, max_length=64)
    updated_at: datetime


class GraphMutationAcknowledgementResponse(BaseModel):
    operation_id: UUID
    operation_status: Literal["acknowledged"]
    graph_hash: str
    updated_at: datetime
    parameter_group: AgentBuilderParameterGroup | None = None
    completed_task_id: UUID | None = None
    completed_knowledge_resolution_id: str | None = None
    next_task_id: UUID | None = None


class AgentBuilderSessionCreateRequest(BaseModel):
    workflow_id: UUID | None = None
    app_id: UUID | None = None


class AgentBuilderSessionResponse(BaseModel):
    session_id: UUID
    workflow_id: UUID | None = None
    app_id: UUID | None = None
    protocol_version: Literal["direct_edit_v1"] | None = None
    default_generation_mode: AgentBuilderGenerationMode = "configure_and_generate"
    status: str
    messages: list[dict[str, Any]] = Field(default_factory=list)
    active_request: dict[str, Any] | None = None
    active_graph_mutation: dict[str, Any] | None = None
    parameter_group: AgentBuilderParameterGroup | None = None
    pending_request: dict[str, Any] | None = None
    draft_preview: dict[str, Any] | None = None


class AgentBuilderKnowledgeCandidateSelection(BaseModel):
    candidate_id: str = Field(min_length=1, max_length=255)
    resolution_id: str | None = Field(default=None, max_length=255)
    requirement_id: str | None = Field(default=None, max_length=255)


class AgentBuilderKnowledgeSelectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    resolution_id: str = Field(min_length=1, max_length=255)
    selected_candidates: list[AgentBuilderKnowledgeCandidateSelection] = Field(
        default_factory=list,
        max_length=20,
    )
    selected_collection_handles: list[str] = Field(default_factory=list, max_length=20)
    selected_kb_handles: list[str] = Field(default_factory=list, max_length=20)
    editor_target_node_id: str | None = Field(default=None, max_length=255)
    selected_knowledge_base_ids: list[UUID] = Field(default_factory=list, max_length=20)
    selected_knowledge_collection_ids: list[UUID] = Field(
        default_factory=list,
        max_length=20,
    )

    @model_validator(mode="after")
    def validate_selection_source(self):
        editor_ids_present = bool(
            self.selected_knowledge_base_ids
            or self.selected_knowledge_collection_ids
        )
        if editor_ids_present and self.editor_target_node_id is None:
            raise ValueError("editor_target_node_id is required for editor selections")
        if self.editor_target_node_id is not None and (
            self.selected_candidates
            or self.selected_collection_handles
            or self.selected_kb_handles
        ):
            raise ValueError("editor and Agent Builder selections cannot be mixed")
        return self


class AgentBuilderKnowledgeSelectionResponse(BaseModel):
    resolution_id: str
    selected_candidates: list[AgentBuilderKnowledgeCandidateSelection]
    selected_collection_handles: list[str] = Field(default_factory=list)
    selected_kb_handles: list[str] = Field(default_factory=list)
    graph_mutation: GraphMutation


class AgentBuilderIntentModelSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    credential_id: UUID
    model_id: UUID


class AgentBuilderMessageRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    message: str = Field(min_length=1, max_length=4000)
    generation_mode: AgentBuilderGenerationMode = "configure_and_generate"
    workflow_id: UUID | None = None
    app_id: UUID | None = None
    selected_node_id: str | None = Field(default=None, max_length=255)
    selected_edge_id: str | None = Field(default=None, max_length=255)
    conversation_context_id: str | None = Field(default=None, max_length=255)
    selected_knowledge_candidate: AgentBuilderKnowledgeCandidateSelection | None = None
    selected_knowledge_candidates: (
        list[AgentBuilderKnowledgeCandidateSelection] | None
    ) = None
    intent_model_selection: AgentBuilderIntentModelSelection | None = None

    @model_validator(mode="before")
    @classmethod
    def reject_raw_client_graph_snapshot(cls, data: Any):
        raw_graph_keys = {
            "client_graph_snapshot",
            "clientGraphSnapshot",
            "graph",
            "nodes",
            "edges",
            "preview_graph",
            "previewGraph",
            "workflow_graph",
            "workflowGraph",
            "raw_graph",
            "rawGraph",
        }
        if isinstance(data, dict) and raw_graph_keys.intersection(data):
            raise ValueError("raw workflow graph payload is not accepted")
        return data

class AgentBuilderPlannedStep(BaseModel):
    step_id: str
    capability: str
    purpose: str
    depends_on: list[str] = Field(default_factory=list)


class AgentBuilderParameterGuidanceHint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    step_id: str = Field(min_length=1, max_length=255)
    parameter_key: str = Field(min_length=1, max_length=255)
    reason: str = Field(min_length=1, max_length=500)
    input_guidance: str = Field(min_length=1, max_length=500)


class AgentBuilderExplicitParameterValue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    step_id: str = Field(min_length=1, max_length=255)
    parameter_key: str = Field(min_length=1, max_length=255)
    value: str | int | float | bool | list[str]


class AgentBuilderParameterSuggestion(BaseModel):
    suggestion_id: str
    kind: Literal["variable_selector"] = "variable_selector"
    label: str
    description: str
    source_node_id: str
    output_key: str
    value_type: str
    value_selector: list[str]
    json_path: str


class AgentBuilderParameterCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_id: UUID
    kind: Literal["resource_ref", "credential_ref"]
    label: str = Field(min_length=1, max_length=255)
    description: str = Field(default="", max_length=500)
    reference_value: str | None = Field(default=None, min_length=1, max_length=255)


class AgentBuilderParameterTask(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: UUID
    group_id: UUID
    step_id: str
    node_id: str
    node_type: str
    parameter_key: str
    task_group: str | None = Field(default=None, min_length=1, max_length=64)
    label: str
    input_type: str
    required: bool
    confirmation_required: bool = False
    defer_policy: Literal["forbidden", "allow_unresolved"] = "forbidden"
    status: ParameterTaskStatus
    task_version: int = Field(ge=1)
    stable_order: int = Field(ge=0)
    resolution_source: ParameterResolutionSource | None = None
    recommendation_fingerprint: str | None = Field(
        default=None,
        pattern=r"^[a-f0-9]{64}$",
    )
    reason: str
    input_guidance: str
    node_label: str = ""
    node_purpose: str = ""
    configuration_state: Literal["resolved", "unresolved"] = "unresolved"
    validation: dict[str, Any] = Field(default_factory=dict)
    sensitivity: Literal["safe", "reference_only", "secret_forbidden"] = "safe"
    suggestions: list[AgentBuilderParameterSuggestion] = Field(default_factory=list)
    candidates: list[AgentBuilderParameterCandidate] = Field(default_factory=list)


class AgentBuilderParameterGroup(BaseModel):
    group_id: UUID
    status: Literal[
        "pending_save", "pending_ack", "active", "completed", "blocked", "canceled"
    ]
    tasks: list[AgentBuilderParameterTask]


class ParameterTextValue(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["text", "textarea", "code"]
    value: str


class ParameterSelectValue(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["select"]
    value: str


class ParameterSecretValue(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["secret"]
    value: str = Field(min_length=1, max_length=4096)


class ParameterNumberValue(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["number"]
    value: int | float

    @field_validator("value", mode="before")
    @classmethod
    def reject_boolean_value(cls, value: Any) -> Any:
        if isinstance(value, bool):
            raise ValueError("number value must not be boolean")
        return value


class ParameterBooleanValue(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["boolean"]
    value: bool


class ParameterJsonValue(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["json"]
    value: Any


class ParameterResourceValue(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["resource_ref"]
    resource_id: UUID


class ParameterCredentialValue(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["credential_ref"]
    credential_id: UUID


class ParameterVariableSelectorValue(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["variable_selector"]
    suggestion_id: str = Field(min_length=1, max_length=255)
    value_selector: list[str] = Field(min_length=2, max_length=32)


class ParameterVariableSelectorSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    suggestion_id: str = Field(min_length=1, max_length=255)
    value_selector: list[str] = Field(min_length=2, max_length=32)


class ParameterVariableSelectorListValue(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["variable_selector_list"]
    selections: list[ParameterVariableSelectorSelection] = Field(
        min_length=1,
        max_length=32,
    )

    @field_validator("selections")
    @classmethod
    def reject_duplicate_selections(
        cls,
        selections: list[ParameterVariableSelectorSelection],
    ) -> list[ParameterVariableSelectorSelection]:
        suggestion_ids = [selection.suggestion_id for selection in selections]
        selectors = [tuple(selection.value_selector) for selection in selections]
        if len(suggestion_ids) != len(set(suggestion_ids)) or len(selectors) != len(
            set(selectors)
        ):
            raise ValueError("selector list selections must be unique")
        return selections


ParameterDecisionValue = Annotated[
    ParameterTextValue
    | ParameterSelectValue
    | ParameterSecretValue
    | ParameterNumberValue
    | ParameterBooleanValue
    | ParameterJsonValue
    | ParameterResourceValue
    | ParameterCredentialValue
    | ParameterVariableSelectorValue
    | ParameterVariableSelectorListValue,
    Field(discriminator="kind"),
]


class AgentBuilderParameterTaskDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation_id: UUID
    expected_task_version: int = Field(ge=1)
    action: Literal["set", "clear", "confirm", "defer", "skip", "previous"]
    value: ParameterDecisionValue | None = None

    @model_validator(mode="after")
    def validate_action_value(self):
        if self.action == "set" and self.value is None:
            raise ValueError("set requires a typed value")
        if self.action != "set" and self.value is not None:
            raise ValueError("only set accepts a value")
        return self


class AgentBuilderParameterTaskDecisionResponse(BaseModel):
    task: AgentBuilderParameterTask
    graph_mutation: GraphMutation | None = None
    next_task_id: UUID | None = None
    group_status: str
    awaiting_persistence_ack: bool
    validation_issues: list[dict[str, Any]] = Field(default_factory=list)


class AgentBuilderParameterGroupCancelRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation_id: UUID
    expected_task_id: UUID
    expected_task_version: int = Field(ge=1)


class AgentBuilderParameterGroupCancelResponse(BaseModel):
    operation_id: UUID
    parameter_group: AgentBuilderParameterGroup


class AgentBuilderKnowledgePlacement(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requirement_id: str = Field(min_length=1, max_length=255)
    timing: Literal["before_graph", "after_graph"]
    effect_kind: Literal["insert_step", "binding_only"]
    target_step_id: str | None = None
    knowledge_step_id: str | None = None
    upstream_step_id: str | None = None
    downstream_step_id: str | None = None
    empty_selection_bridge: Literal["connect_upstream_to_downstream"] | None = None

    @model_validator(mode="after")
    def validate_timing_contract(self):
        if self.timing == "before_graph":
            if self.effect_kind != "insert_step" or not all(
                (
                    self.target_step_id,
                    self.knowledge_step_id,
                    self.upstream_step_id,
                    self.downstream_step_id,
                    self.empty_selection_bridge,
                )
            ):
                raise ValueError("before_graph requires topology references")
        else:
            if self.effect_kind != "binding_only" or not self.target_step_id:
                raise ValueError("after_graph requires a binding target")
            if any(
                (
                    self.knowledge_step_id,
                    self.upstream_step_id,
                    self.downstream_step_id,
                    self.empty_selection_bridge,
                )
            ):
                raise ValueError("after_graph does not accept topology references")
        return self


class AgentBuilderKnowledgeRequirement(BaseModel):
    requirement_id: str
    query_topics: list[str] = Field(default_factory=list)
    suggested_candidate_handles: list[str] = Field(default_factory=list, max_length=20)
    expected_evidence_type: str = "policy_or_reference"
    required: bool = True
    target_step_ref: str | None = None


class AgentBuilderPendingResolution(BaseModel):
    resolution_id: str
    slot_type: AgentBuilderPendingSlotType
    slot_key: str
    blocking: bool = True
    target_step_ref: str | None = None


class AgentBuilderEditTargetReference(BaseModel):
    reference_type: AgentBuilderTargetReferenceType
    query: str | None = Field(default=None, max_length=255)
    source_query: str | None = Field(default=None, max_length=255)
    destination_query: str | None = Field(default=None, max_length=255)
    capabilities: list[str] = Field(default_factory=list)
    node_types: list[str] = Field(default_factory=list)


class AgentBuilderEditOperation(BaseModel):
    operation_id: str
    operation: AgentBuilderEditOperationType
    placement: AgentBuilderEditPlacement
    step_refs: list[str] = Field(default_factory=list)
    target: AgentBuilderEditTargetReference


class AgentBuilderStructuredRequest(BaseModel):
    request_type: Literal[
        "new_workflow",
        "modify_workflow",
        "clarification",
        "unsupported",
        "validation_failure",
    ]
    draft_mode: AgentBuilderDraftMode
    intent_summary: str
    planned_steps: list[AgentBuilderPlannedStep] = Field(default_factory=list)
    parameter_guidance_hints: list[AgentBuilderParameterGuidanceHint] = Field(
        default_factory=list,
        max_length=128,
    )
    explicit_parameter_values: list[AgentBuilderExplicitParameterValue] = Field(
        default_factory=list,
        max_length=128,
    )
    knowledge_requirements: list[AgentBuilderKnowledgeRequirement] = Field(
        default_factory=list
    )
    knowledge_placements: list[AgentBuilderKnowledgePlacement] = Field(
        default_factory=list
    )
    required_capabilities: list[str] = Field(default_factory=list)
    pending_resolution: list[AgentBuilderPendingResolution] = Field(
        default_factory=list
    )
    edit_operations: list[AgentBuilderEditOperation] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)
    unsupported_requests: list[str] = Field(default_factory=list)
    risk_flags: list[str] = Field(default_factory=list)


class AgentBuilderValidationIssue(BaseModel):
    code: str
    message: str
    path: str | None = None


class AgentBuilderValidationResult(BaseModel):
    valid: bool
    issues: list[AgentBuilderValidationIssue] = Field(default_factory=list)


class AgentBuilderMissingParameter(BaseModel):
    key: str
    label: str


class AgentBuilderNodeConfigurationIssue(BaseModel):
    node_id: str
    node_type: str
    node_label: str
    capability: str
    missing_parameters: list[AgentBuilderMissingParameter] = Field(
        default_factory=list
    )


class AgentBuilderDraftPreview(BaseModel):
    draft_id: UUID
    preview_graph: dict[str, Any]
    base_graph_hash: str | None = None
    base_workflow_updated_at: datetime | None = None
    draft_mode: AgentBuilderDraftMode
    node_detail_previews: list[dict[str, Any]] = Field(default_factory=list)
    validation_result: AgentBuilderValidationResult
    safety_notices: list[str] = Field(default_factory=list)
    configuration_issues: list[AgentBuilderNodeConfigurationIssue] = Field(
        default_factory=list
    )


class AgentBuilderMessageResponse(BaseModel):
    _issued_knowledge_handle_bindings: dict[str, dict[str, str]] = PrivateAttr(
        default_factory=dict
    )

    request_id: UUID
    status: AgentBuilderRequestStatus
    structured_request: AgentBuilderStructuredRequest | None = None
    graph_mutation: GraphMutation | None = None
    parameter_group: AgentBuilderParameterGroup | None = None
    clarification_questions: list[str] = Field(default_factory=list)
    clarification_options: list[dict[str, Any]] = Field(default_factory=list)
    knowledge_selection: dict[str, Any] | None = None
    draft_preview: AgentBuilderDraftPreview | None = None
    validation_result: AgentBuilderValidationResult | None = None
    preview_prompt: str | None = None
    warnings: list[str] = Field(default_factory=list)
    safe_step_node_ids: dict[str, str] = Field(default_factory=dict)


class AgentBuilderDirectSessionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: UUID
    workflow_id: UUID | None = None
    app_id: UUID | None = None
    protocol_version: Literal["direct_edit_v1"] | None = None
    default_generation_mode: AgentBuilderGenerationMode = "configure_and_generate"
    status: str
    messages: list[dict[str, Any]] = Field(default_factory=list)
    active_request: dict[str, Any] | None = None
    active_graph_mutation: dict[str, Any] | None = None
    parameter_group: AgentBuilderParameterGroup | None = None
    pending_request: dict[str, Any] | None = None


class AgentBuilderDirectStructuredPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_type: Literal[
        "new_workflow",
        "modify_workflow",
        "clarification",
        "unsupported",
        "validation_failure",
    ]
    intent_summary: str
    steps: list[AgentBuilderPlannedStep] = Field(default_factory=list)
    parameter_guidance_hints: list[AgentBuilderParameterGuidanceHint] = Field(
        default_factory=list
    )
    knowledge_requirements: list[AgentBuilderKnowledgeRequirement] = Field(
        default_factory=list
    )


class AgentBuilderKnowledgeCandidateOption(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: str | None = None
    candidate_id: str
    resolution_id: str | None = None
    requirement_id: str | None = None
    label: str | None = None
    safe_label: str | None = None
    confidence: Literal["high", "medium", "low"] | None = None
    score: float | None = Field(default=None, ge=0.0, le=1.0)
    reason_category: str | None = None
    reason: str | None = None
    threshold_result: str | None = None
    runtime_availability: str | None = None


class AgentBuilderKnowledgeSelectedOption(BaseModel):
    model_config = ConfigDict(extra="forbid")

    selection_type: Literal["collection", "knowledge_base"] | None = None
    candidate_id: str | None = None
    collection_handle: str | None = None
    kb_handle: str | None = None
    resolution_id: str | None = None
    requirement_id: str | None = None
    label: str | None = None
    safe_label: str | None = None


class AgentBuilderKnowledgeResolution(BaseModel):
    model_config = ConfigDict(extra="forbid")

    resolution_id: str | None = None
    requirement_id: str | None = None
    target_node_id: str | None = None
    timing: Literal["before_graph", "after_graph"] = "after_graph"
    required: bool = False
    candidates: list[AgentBuilderKnowledgeCandidateOption] = Field(
        default_factory=list
    )
    collections: list[KnowledgeSelectionCollection] = Field(default_factory=list)
    ungrouped_kbs: list[KnowledgeSelectionKBCandidate] = Field(default_factory=list)
    selected: list[AgentBuilderKnowledgeSelectedOption] = Field(default_factory=list)
    selected_collection_handles: list[str] = Field(default_factory=list)
    selected_kb_handles: list[str] = Field(default_factory=list)
    selection_status: Literal["pending_ack", "completed", "unapplied"] | None = None


class AgentBuilderDirectMessageResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: UUID
    status: AgentBuilderRequestStatus
    structured_plan: AgentBuilderDirectStructuredPlan | None = None
    knowledge_resolution: AgentBuilderKnowledgeResolution | None = None
    graph_mutation: GraphMutation | None = None
    parameter_group: AgentBuilderParameterGroup | None = None
    clarification_questions: list[str] = Field(default_factory=list)
    clarification_options: list[dict[str, Any]] = Field(default_factory=list)
    validation_result: AgentBuilderValidationResult | None = None
    warnings: list[str] = Field(default_factory=list)

    @classmethod
    def from_internal(
        cls, response: "AgentBuilderMessageResponse"
    ) -> "AgentBuilderDirectMessageResponse":
        structured = response.structured_request
        placements = structured.knowledge_placements if structured else []
        requirements = structured.knowledge_requirements if structured else []
        options = [
            option
            for option in response.clarification_options
            if isinstance(option, dict) and option.get("candidate_id")
        ]
        non_knowledge_options = [
            option
            for option in response.clarification_options
            if not (isinstance(option, dict) and option.get("candidate_id"))
        ]
        resolution_id = next(
            (
                str(option["resolution_id"])
                for option in options
                if option.get("resolution_id")
            ),
            None,
        )
        if resolution_id is None and structured is not None:
            resolution_id = next(
                (
                    item.resolution_id
                    for item in structured.pending_resolution
                    if item.slot_type == "knowledge_base"
                ),
                None,
            )
        knowledge_resolution = None
        if placements or requirements or options or response.knowledge_selection:
            target_step_id = placements[0].target_step_id if placements else None
            target_node_id = (
                response.safe_step_node_ids.get(target_step_id)
                if target_step_id is not None
                else None
            )
            knowledge_resolution = {
                "resolution_id": resolution_id,
                "target_node_id": target_node_id,
                "timing": placements[0].timing if placements else "after_graph",
                "required": bool(requirements),
                "candidates": options,
                "collections": (
                    response.knowledge_selection.get("collections", [])
                    if response.knowledge_selection
                    else []
                ),
                "ungrouped_kbs": (
                    response.knowledge_selection.get("ungrouped_kbs", [])
                    if response.knowledge_selection
                    else []
                ),
                "selected": [],
                "selected_collection_handles": [],
                "selected_kb_handles": [],
            }
        return cls(
            request_id=response.request_id,
            status=response.status,
            structured_plan=(
                AgentBuilderDirectStructuredPlan(
                    request_type=structured.request_type,
                    intent_summary=structured.intent_summary,
                    steps=structured.planned_steps,
                    parameter_guidance_hints=structured.parameter_guidance_hints,
                    knowledge_requirements=structured.knowledge_requirements,
                )
                if structured
                else None
            ),
            knowledge_resolution=knowledge_resolution,
            graph_mutation=response.graph_mutation,
            parameter_group=response.parameter_group,
            clarification_questions=response.clarification_questions,
            clarification_options=non_knowledge_options,
            validation_result=response.validation_result,
            warnings=response.warnings,
        )


class AgentBuilderApplyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: AgentBuilderApplyAction
    client_preview_graph_hash: str | None = None
    client_latest_graph_hash: str | None = None
    client_workflow_version: str | None = None
    client_workflow_updated_at: datetime | None = None

    @model_validator(mode="before")
    @classmethod
    def reject_raw_client_graph_snapshot(cls, data: Any):
        raw_graph_keys = {
            "client_graph_snapshot",
            "clientGraphSnapshot",
            "graph",
            "nodes",
            "edges",
            "preview_graph",
            "previewGraph",
            "workflow_graph",
            "workflowGraph",
            "raw_graph",
            "rawGraph",
        }
        if isinstance(data, dict) and raw_graph_keys.intersection(data):
            raise ValueError("raw workflow graph payload is not accepted")
        return data


class AgentBuilderApplyResponse(BaseModel):
    apply_id: UUID
    outcome: AgentBuilderApplyOutcome
    saved_workflow_id: UUID | None = None
    latest_graph_hash: str | None = None
    latest_workflow_version: str | None = None
    latest_workflow_updated_at: datetime | None = None
    block_reason: str | None = None
    failure_reason: str | None = None
    stale_state: str = "not_stale"
    permission_recheck_outcome: str = "not_checked"
    validation_state: str = "not_checked"
    audit_recorded: bool = False
    layout_optimization_applied: bool = False
    notices: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def saved_requires_audit_recorded(self):
        if self.outcome == "saved" and not self.audit_recorded:
            raise ValueError("outcome=saved requires audit_recorded=true")
        return self
