from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class NotificationItemResponse(BaseModel):
    id: str
    type: str
    organization_id: UUID
    organization_name: str
    organization_auth_state: str
    created_at: datetime


class NotificationListResponse(BaseModel):
    items: list[NotificationItemResponse]
