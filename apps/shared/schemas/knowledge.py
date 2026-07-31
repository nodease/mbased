import re
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    PrivateAttr,
    field_validator,
)

SourceAclState = Literal[
    "fresh",
    "stale",
    "unmapped",
    "ambiguous",
    "unverified",
    "revoked",
    "not_source_managed",
]
RequesterSourceAuthorization = Literal[
    "allowed",
    "denied",
    "unknown",
    "not_applicable",
]
ResourceVisibility = Literal["visible", "resource_hidden", "hidden", "admin_visible"]
RuntimeAvailability = Literal["available", "warning", "unavailable", "unknown"]
KnowledgeRAGRecommendationStatus = Literal[
    "recommended",
    "clarification_required",
    "no_candidate",
    "unavailable",
]
KnowledgeCandidateType = Literal["knowledge_base", "collection"]
KnowledgeCandidateResolutionMode = Literal["explicit_kb", "auto_collection"]
KnowledgeCandidatePurpose = Literal[
    "builder_suggestion",
    "deployment_preflight",
    "runtime_preview",
]
KnowledgeRAGRecommendationMode = Literal["auto", "explicit_kb", "auto_collection"]
KnowledgeRAGRecommendationResolvedMode = Literal["explicit_kb", "auto_collection"]
KnowledgeRAGHighRiskDomain = Literal["none", "policy", "legal", "compliance"]
KnowledgeRAGCandidateType = Literal["knowledge_base"]
KnowledgeRAGQueryRewriteMode = Literal["off", "template"]
KnowledgeRAGEvidenceSufficiencyPolicy = Literal["minimum_evidence", "strict_citation"]
KnowledgeRAGFailurePolicy = Literal["safe_no_result", "fail_node"]
KnowledgeRAGSourceTierPolicy = Literal["tie_break", "off"]
KnowledgeCollectionAction = Literal["read", "route", "manage", "sync"]
KnowledgeCollectionVisibility = Literal["private", "public"]
KnowledgeCollectionLifecycleState = Literal["active", "archived", "deleted"]
KnowledgeCollectionSyncJobStatus = Literal[
    "queued",
    "running",
    "succeeded",
    "partially_failed",
    "failed",
    "cancelled",
]
KnowledgeCollectionSyncProgress = Literal[
    "none",
    "started",
    "progressing",
    "most",
    "complete",
]
KnowledgeCollectionRoleBundle = Literal[
    "viewer",
    "workflow_router",
    "maintainer",
    "sync_operator",
]
KnowledgeDomainPermissionAction = Literal[
    "catalog_manage",
    "permission_delegate",
    "lifecycle_manage",
    "sync_manage",
]

_CONTROL_CHAR_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]+")


class KnowledgePermissionDecision(BaseModel):
    allowed: bool
    resource_visibility: ResourceVisibility = "resource_hidden"
    effective_auth_state: str = "none"
    source_acl_state: SourceAclState = "not_source_managed"
    requester_source_authorization: RequesterSourceAuthorization = "not_applicable"
    freshness_epoch: int = 0
    reason_code: str = "resource.hidden"
    external_reason_code: str = "resource.hidden"
    safe_metadata: dict = Field(default_factory=dict)


class KnowledgeCandidate(BaseModel):
    candidate_id: UUID
    candidate_type: KnowledgeCandidateType
    permission: KnowledgePermissionDecision
    runtime_availability: RuntimeAvailability = "unknown"
    safe_label: str | None = None
    safe_metadata: dict = Field(default_factory=dict)


class KnowledgeCandidateResolution(BaseModel):
    candidates: list[KnowledgeCandidate] = Field(default_factory=list)
    hidden_candidate_count_bucket: str = "0"
    unavailable_candidate_count_bucket: str = "0"
    reason_code: str | None = None


class KnowledgeCandidateCollectionGroup(BaseModel):
    """Internal authorized Collection group used before opaque projection."""

    collection_id: UUID
    safe_label: str | None = None
    safe_metadata: dict = Field(default_factory=dict)
    candidates: list[KnowledgeCandidate] = Field(default_factory=list)


class KnowledgeCandidateHierarchyResolution(BaseModel):
    collections: list[KnowledgeCandidateCollectionGroup] = Field(default_factory=list)
    ungrouped_candidates: list[KnowledgeCandidate] = Field(default_factory=list)
    hidden_candidate_count_bucket: str = "0"
    unavailable_candidate_count_bucket: str = "0"
    reason_code: str | None = None


class KnowledgeSelectionKBCandidate(BaseModel):
    kb_handle: str
    selection_key: str
    safe_label: str | None = None
    score: float = Field(ge=0.0, le=1.0)
    shared_collection_count: int = Field(default=0, ge=0)


class KnowledgeSelectionCollection(BaseModel):
    collection_handle: str
    safe_label: str | None = None
    score: float = Field(ge=0.0, le=1.0)
    children: list[KnowledgeSelectionKBCandidate] = Field(default_factory=list)


class KnowledgeSelection(BaseModel):
    collections: list[KnowledgeSelectionCollection] = Field(default_factory=list)
    ungrouped_kbs: list[KnowledgeSelectionKBCandidate] = Field(default_factory=list)


class KnowledgeCandidateResolveRequest(BaseModel):
    mode: KnowledgeCandidateResolutionMode
    knowledge_base_ids: list[UUID] = Field(default_factory=list)
    collection_ids: list[UUID] | None = None
    intended_execution_subject_id: UUID | None = None
    purpose: KnowledgeCandidatePurpose = "builder_suggestion"
    max_collections: int = Field(default=20, ge=1, le=100)
    max_candidate_kbs: int = Field(default=5000, ge=1, le=5000)


class KnowledgeCollectionCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=2000)
    safe_metadata: dict = Field(default_factory=dict)


class KnowledgeCollectionUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=2000)
    safe_metadata: dict | None = None


class KnowledgeCollectionResponse(BaseModel):
    id: UUID
    organization_id: UUID
    name: str
    description: str | None = None
    is_system_managed: bool = False
    sync_state: str = "manual"
    lifecycle_state: KnowledgeCollectionLifecycleState = "active"
    visibility: KnowledgeCollectionVisibility = "private"
    linked_kb_count_bucket: str = "0"
    active_kb_count_bucket: str = "0"
    can_read: bool = False
    can_route: bool = False
    can_manage: bool = False
    can_sync: bool = False
    sync_supported: bool = False
    safe_metadata: dict = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime


class KnowledgeCollectionListResponse(BaseModel):
    collections: list[KnowledgeCollectionResponse] = Field(default_factory=list)
    can_create_collection: bool = False
    can_change_public_visibility: bool = False


class KnowledgeCollectionSyncJobResponse(BaseModel):
    job_id: UUID
    collection_id: UUID
    status: KnowledgeCollectionSyncJobStatus
    progress: KnowledgeCollectionSyncProgress
    safe_reason_code: str | None = None
    retryable: bool = True
    requested_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None


class KnowledgeCollectionSyncRequestResponse(BaseModel):
    job: KnowledgeCollectionSyncJobResponse
    reused: bool = False
    dispatch_deferred: bool = False


class KnowledgeCollectionLatestSyncJobResponse(BaseModel):
    job: KnowledgeCollectionSyncJobResponse | None = None


class KnowledgeCollectionLLMSelectableItem(BaseModel):
    id: UUID
    safe_label: str | None = None


class KnowledgeCollectionLLMSelectableResponse(BaseModel):
    collections: list[KnowledgeCollectionLLMSelectableItem] = Field(
        default_factory=list
    )


class KnowledgeCollectionItemLinkRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    knowledge_base_id: UUID
    rank: int | None = Field(default=None, ge=0, deprecated=True)
    acknowledged_public_runtime_exposure: bool = False


class KnowledgeCollectionItemResponse(BaseModel):
    item_id: UUID
    knowledge_base_id: UUID
    safe_label: str | None = None
    lifecycle_state: str = "active"
    sync_state: str = "manual"
    rank: int = 0
    can_manage_kb: bool = False
    can_use_kb: bool = False


class KnowledgeCollectionItemsResponse(BaseModel):
    items: list[KnowledgeCollectionItemResponse] = Field(default_factory=list)
    order_revision: str = Field(
        ...,
        pattern=r"^ord_v1_[0-9a-f]{64}$",
    )
    reorder_supported: bool = True
    safe_reason_code: Literal["item_reorder_limit_exceeded"] | None = None


class KnowledgeCollectionItemReorderEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item_id: UUID
    rank: int = Field(ge=0)


class KnowledgeCollectionItemReorderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[KnowledgeCollectionItemReorderEntry] = Field(
        ..., max_length=500
    )
    expected_order_revision: str = Field(
        ...,
        pattern=r"^ord_v1_[0-9a-f]{64}$",
    )
    acknowledged_public_runtime_exposure: bool = False


class KnowledgeCollectionLinkCandidate(BaseModel):
    knowledge_base_id: UUID
    safe_label: str | None = None
    disabled: bool = False
    safe_reason_code: str | None = None


class KnowledgeCollectionLinkCandidatesResponse(BaseModel):
    candidates: list[KnowledgeCollectionLinkCandidate] = Field(default_factory=list)


class KnowledgeCollectionPermissionGrantRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subject_type: Literal["team", "user"]
    subject_id: UUID
    permission_action: KnowledgeCollectionAction


class KnowledgeCollectionPermissionBundleGrantRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subject_type: Literal["team", "user"]
    subject_id: UUID
    role_bundle: KnowledgeCollectionRoleBundle


class KnowledgeCollectionPermissionBulkBundleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    collection_ids: list[UUID] = Field(..., min_length=1, max_length=50)
    operation: Literal["grant", "revoke"]
    subject_type: Literal["team", "user"]
    subject_id: UUID
    role_bundle: KnowledgeCollectionRoleBundle

    @field_validator("collection_ids")
    @classmethod
    def validate_unique_collection_ids(cls, value: list[UUID]) -> list[UUID]:
        if len(set(value)) != len(value):
            raise ValueError("collection_ids must be unique")
        return value


class KnowledgeCollectionPermissionBulkBundleResponse(BaseModel):
    operation: Literal["grant", "revoke"]
    subject_type: Literal["team", "user"]
    role_bundle: KnowledgeCollectionRoleBundle
    target_count_bucket: Literal["0", "1", "2-10", "11-50"]
    changed_count_bucket: Literal["0", "1", "2-10", "11-50"]
    unchanged_count_bucket: Literal["0", "1", "2-10", "11-50"]


class KnowledgeCollectionPermissionResponse(BaseModel):
    permission_id: UUID
    subject_type: Literal["team", "user"]
    subject_id: UUID
    subject_safe_label: str | None = None
    permission_action: KnowledgeCollectionAction


class KnowledgeCollectionPermissionsResponse(BaseModel):
    permissions: list[KnowledgeCollectionPermissionResponse] = Field(
        default_factory=list
    )


class KnowledgeDelegationSubject(BaseModel):
    subject_type: Literal["team", "user"]
    subject_id: UUID
    subject_safe_label: str


class KnowledgeDelegationSubjectsResponse(BaseModel):
    subjects: list[KnowledgeDelegationSubject] = Field(default_factory=list)
    next_cursor: str | None = None


class KnowledgeCollectionVisibilityRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    visibility: KnowledgeCollectionVisibility
    acknowledged_public_runtime_exposure: bool = False


class KnowledgeCollectionVisibilityResponse(BaseModel):
    collection: KnowledgeCollectionResponse
    public_runtime_effect: str = "anonymous_public_only_candidate"
    linked_kb_count_bucket: str = "0"
    active_kb_count_bucket: str = "0"
    sensitive_content_warning: str = "unknown_or_present"


class KnowledgeDomainPermissionUpsertRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expires_at: datetime | None = None


class KnowledgeDomainPermissionResponse(BaseModel):
    permission_id: UUID
    subject_type: Literal["team", "user"]
    subject_id: UUID
    subject_safe_label: str
    permission_action: KnowledgeDomainPermissionAction
    assigned_at: datetime
    expires_at: datetime | None = None
    is_expired: bool = False


class KnowledgeDomainPermissionListResponse(BaseModel):
    permissions: list[KnowledgeDomainPermissionResponse] = Field(default_factory=list)


class KnowledgeDomainCapabilitiesResponse(BaseModel):
    actions: list[KnowledgeDomainPermissionAction] = Field(default_factory=list)
    can_manage_domain_permissions: bool = False
    can_create_collection: bool = False
    can_delegate_permissions: bool = False
    can_manage_lifecycle: bool = False
    can_manage_sync: bool = False
    can_change_public_visibility: bool = False


def normalize_recommendation_text(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = _CONTROL_CHAR_RE.sub(" ", value)
    normalized = " ".join(normalized.split())
    return normalized


class KnowledgeRAGRecommendationRequest(BaseModel):
    workflow_intent: str = Field(
        ...,
        min_length=1,
        max_length=4000,
        validation_alias=AliasChoices("workflow_intent", "intent_summary"),
    )
    node_purpose: str | None = Field(
        default=None,
        max_length=1000,
        validation_alias=AliasChoices("node_purpose", "node_purpose_summary"),
    )
    knowledge_requirement: dict | None = None
    safe_query_topics: list[str] = Field(default_factory=list, max_length=20)
    pending_resolution_ref: str | None = Field(default=None, max_length=255)
    authorized_safe_candidate_set_ref: str | None = Field(default=None, max_length=255)
    safe_workflow_context_summary: dict | None = None
    mode: KnowledgeRAGRecommendationMode = "auto"
    collection_ids: list[UUID] = Field(default_factory=list)
    knowledge_base_ids: list[UUID] = Field(default_factory=list)
    intended_execution_subject_id: UUID | None = None
    max_recommendations: int = Field(default=20, ge=1, le=20)
    max_collections: int = Field(default=20, ge=1, le=20)
    max_candidate_kbs: int = Field(default=5000, ge=1, le=5000)
    high_risk_domain: KnowledgeRAGHighRiskDomain = "none"
    allow_query_rewrite: bool = True

    @field_validator("workflow_intent", "node_purpose")
    @classmethod
    def normalize_raw_text(cls, value: str | None) -> str | None:
        normalized = normalize_recommendation_text(value)
        if normalized is None:
            return None
        if not normalized:
            raise ValueError("text must not be empty")
        return normalized

    @field_validator("safe_query_topics", mode="before")
    @classmethod
    def normalize_safe_query_topics(cls, value):
        if value is None:
            return []
        if isinstance(value, str):
            raw_values = [value]
        elif isinstance(value, (list, tuple, set)):
            raw_values = list(value)
        else:
            return []

        topics: list[str] = []
        for item in raw_values:
            normalized = normalize_recommendation_text(item if isinstance(item, str) else None)
            if not normalized:
                continue
            normalized = normalized[:128]
            if normalized not in topics:
                topics.append(normalized)
            if len(topics) >= 20:
                break
        return topics


class KnowledgeBaseOptionRef(BaseModel):
    id: UUID
    name: str = "Knowledge Base"


class KnowledgeRAGRecommendedOptions(BaseModel):
    queryRewriteMode: KnowledgeRAGQueryRewriteMode = "off"
    queryRewriteTemplate: str | None = Field(default=None, max_length=512)
    evidenceSufficiencyPolicy: KnowledgeRAGEvidenceSufficiencyPolicy = (
        "minimum_evidence"
    )
    ragFailurePolicy: KnowledgeRAGFailurePolicy = "safe_no_result"
    sourceTierPolicy: KnowledgeRAGSourceTierPolicy = "tie_break"
    scoreThreshold: float = Field(default=0.3, ge=0.0, le=1.0)
    topK: int = Field(default=5, ge=1, le=8)


class KnowledgeSourceCollectionSummary(BaseModel):
    collection_id: UUID | None = None
    safe_label: str | None = None
    route_scope_type: str | None = None
    linked_kb_count_bucket: str | None = None


class KnowledgeRAGRecommendationProvenance(BaseModel):
    recommendation_strategy: str = "structured_kb_relevance_v2"
    safe_reason_code: str
    used_signals: list[str] = Field(default_factory=list)
    matched_safe_terms: list[str] = Field(default_factory=list)
    candidate_count_bucket: str = "0"
    warning_count_bucket: str = "0"


class KnowledgeRAGRecommendation(BaseModel):
    recommendation_id: str
    recommendation_mode: KnowledgeRAGRecommendationResolvedMode
    candidate_type: KnowledgeRAGCandidateType = "knowledge_base"
    candidate_id: str
    candidate_handle: str | None = None
    safe_label: str | None = None
    confidence: Literal["high", "medium", "low"]
    confidence_label: Literal["high", "medium", "low"] | None = None
    score: float | None = Field(default=None, ge=0.0, le=1.0)
    reason_category: str | None = None
    threshold_result: str | None = None
    safe_reason_code: str
    recommended_options: KnowledgeRAGRecommendedOptions
    materialized_knowledge_bases: list[KnowledgeBaseOptionRef] = Field(
        default_factory=list
    )
    source_collection_summary: KnowledgeSourceCollectionSummary | None = None
    provenance: KnowledgeRAGRecommendationProvenance
    runtime_availability: RuntimeAvailability = "unknown"
    warnings: list[str] = Field(default_factory=list)


class KnowledgeRAGRecommendationSummary(BaseModel):
    candidate_count_bucket: str = "0"
    recommendation_count_bucket: str = "0"
    hidden_or_unavailable_count_bucket: str = "0"
    recommendation_strategy: str = "structured_kb_relevance_v2"
    warning_count_bucket: str = "0"


class KnowledgeRAGRecommendationResponse(BaseModel):
    _issued_kb_resource_ids: dict[str, UUID] = PrivateAttr(default_factory=dict)
    _issued_collection_resource_ids: dict[str, UUID] = PrivateAttr(
        default_factory=dict
    )

    status: KnowledgeRAGRecommendationStatus = "recommended"
    resolution_id: str | None = None
    requirement_id: str | None = None
    recommendations: list[KnowledgeRAGRecommendation] = Field(default_factory=list)
    clarification_options: list[dict] = Field(default_factory=list)
    knowledge_selection: KnowledgeSelection | None = None
    fallback_reason: str | None = None
    summary: KnowledgeRAGRecommendationSummary = Field(
        default_factory=KnowledgeRAGRecommendationSummary
    )
    reason_code: str | None = None
    user_safe_warning: str | None = None
