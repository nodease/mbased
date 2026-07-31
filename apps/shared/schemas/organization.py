from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class OrganizationPatchRequest(BaseModel):
    name: str | None = Field(default=None, max_length=255)
    options: dict[str, Any] | None = None


class OrganizationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    options: dict[str, Any]
    is_active: bool
    is_manager: bool
    created_at: datetime
    updated_at: datetime
