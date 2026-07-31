from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class BrowserEmbeddingPolicy:
    enabled: bool
    parent_origins: tuple[str, ...]

    def to_dict(self) -> dict:
        return {
            "enabled": self.enabled,
            "parent_origins": list(self.parent_origins),
        }


@dataclass(frozen=True)
class BrowserAccessPolicy:
    contract_version: str
    embedding: BrowserEmbeddingPolicy

    def to_dict(self) -> dict:
        return {
            "contract_version": self.contract_version,
            "embedding": self.embedding.to_dict(),
        }


@dataclass(frozen=True)
class BrowserAccessSourceSnapshot:
    id: uuid.UUID
    app_id: uuid.UUID
    workflow_id: uuid.UUID
    organization_id: uuid.UUID | None
    version: int
    deployment_type: str
    graph_snapshot: dict
    config: dict | None
    input_schema: dict | None
    output_schema: dict | None
    description: str | None
    url_slug: str | None


@dataclass(frozen=True)
class BrowserAccessRevisionCommand:
    source_deployment_id: uuid.UUID
    actor_id: uuid.UUID
    browser_access_policy: dict[str, Any]
    is_active: bool
    environment: str | None


@dataclass(frozen=True)
class BrowserAccessRevision:
    id: uuid.UUID
    app_id: uuid.UUID
    version: int
    deployment_type: str
    graph_snapshot: dict
    config: dict | None
    input_schema: dict | None
    output_schema: dict | None
    description: str | None
    created_by: uuid.UUID
    created_at: datetime
    is_active: bool
    browser_access_policy: dict
    url_slug: str | None


@dataclass(frozen=True)
class ActiveBrowserAccessSnapshot:
    deployment_version: int
    deployment_type: str
    browser_access_policy: object


@dataclass(frozen=True)
class PublicBrowserAccessProjection:
    contract_version: str
    deployment_version: int
    enabled: bool
    frame_ancestors: tuple[str, ...]
