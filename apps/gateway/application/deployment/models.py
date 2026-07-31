from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Literal

PreflightStatus = Literal["passed", "warning", "blocked"]
PreflightAudience = Literal[
    "anonymous_public",
    "authenticated_user",
    "workflow_node_inherited",
]


@dataclass(frozen=True)
class KnowledgeBaseSnapshot:
    id: uuid.UUID
    source_managed: bool


@dataclass(frozen=True)
class KnowledgeCollectionPreflightSnapshot:
    id: uuid.UUID
    public: bool
    source_managed: bool
    has_source_managed_members: bool
    candidate_member_count: int


@dataclass(frozen=True)
class MailCredentialSnapshot:
    provider: str
    auth_type: str
    usable_by_principal: bool
    effective_auth_state: str


@dataclass(frozen=True)
class NodeCatalogSnapshot:
    side_effect: str
    implemented: bool


@dataclass(frozen=True)
class WorkflowNodeTargetSnapshot:
    app_id: uuid.UUID
    active_graph_snapshot: dict | None
    organization_id: uuid.UUID | None = None
    workflow_id: uuid.UUID | None = None
    deployment_id: uuid.UUID | None = None
    deployment_version: int | None = None
    deployment_type: str | None = None
    active_pointer_valid: bool = False


@dataclass(frozen=True)
class PreflightRequiredAction:
    action: str
    label: str


@dataclass(frozen=True)
class PreflightNodeResult:
    node_id: str | None
    node_type: str
    status: PreflightStatus
    reason_codes: tuple[str, ...]
    knowledge_base_count_bucket: str
    knowledge_collection_count_bucket: str
    candidate_budget_limited: bool


@dataclass(frozen=True)
class PreflightSummary:
    blocked_reason: str | None
    affected_node_count: int
    affected_kb_count_bucket: str
    affected_collection_count_bucket: str
    candidate_budget_limited: bool


@dataclass(frozen=True)
class DeploymentPreflightResult:
    status: PreflightStatus
    audience: PreflightAudience
    safe_summary: PreflightSummary
    required_actions: tuple[PreflightRequiredAction, ...]
    warnings: tuple[str, ...]
    nodes: tuple[PreflightNodeResult, ...]
