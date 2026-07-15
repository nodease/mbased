from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
from uuid import UUID


@dataclass(frozen=True)
class DeleteAppCommand:
    app_id: UUID
    actor_id: UUID
    organization_id: UUID | None = None


@dataclass(frozen=True)
class LockedApp:
    id: UUID
    organization_id: UUID | None
    primary_workflow_id: UUID | None
    created_by: UUID


@dataclass(frozen=True)
class LockedWorkflow:
    id: UUID
    organization_id: UUID | None
    app_id: UUID


@dataclass(frozen=True)
class AppDeletionCounts:
    permissions: int
    budgets: int
    deployments: int
    schedules: int
    active_routing_policies: int
    llm_node_versions: int


@dataclass(frozen=True)
class DeletedWorkflowPermission:
    id: UUID
    subject_type: Literal["user", "team"]
    organization_id: UUID | None
    workflow_id: UUID
    subject_id: UUID


@dataclass(frozen=True)
class ActiveResourceDeletion:
    counts: AppDeletionCounts
    permissions: tuple[DeletedWorkflowPermission, ...]


@dataclass(frozen=True)
class DeleteAppResult:
    app_id: UUID
    workflow_ids: tuple[UUID, ...]
    counts: AppDeletionCounts
