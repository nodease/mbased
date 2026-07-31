from __future__ import annotations

import uuid

from ..authorization import require_organization_manager
from ..errors import ResourceHidden
from ..models import MemberResourceAccess, Page, ResourceType
from ..policies import effective_resource_auth_state
from ..ports import (
    ActorAccessQueryPort,
    OrganizationAuthorizationPort,
    PermissionDenialPort,
)


class ListResourceAccess:
    def __init__(
        self,
        authorization: OrganizationAuthorizationPort,
        query: ActorAccessQueryPort,
        denial_recorder: PermissionDenialPort,
    ) -> None:
        self.authorization = authorization
        self.query = query
        self.denial_recorder = denial_recorder

    def execute(
        self,
        *,
        actor_id: uuid.UUID,
        organization_id: uuid.UUID,
        target_user_id: uuid.UUID,
        resource_type: ResourceType,
        resource_id: uuid.UUID | None = None,
        source: str,
        page: int,
        limit: int,
    ) -> Page[MemberResourceAccess]:
        require_organization_manager(
            self.authorization,
            self.denial_recorder,
            actor_id=actor_id,
            organization_id=organization_id,
        )
        member = self.query.get_member(organization_id, target_user_id)
        if member is None:
            raise ResourceHidden("Member not found.")
        projections = self.query.list_resource_access(
            organization_id,
            target_user_id,
            resource_type=resource_type,
            resource_id=resource_id,
            source=source,
            page=page,
            limit=limit,
        )
        return Page(
            total=projections.total,
            items=tuple(
                MemberResourceAccess(
                    resource_type=item.resource_type,
                    resource_id=item.resource_id,
                    resource_name=item.resource_name,
                    effective_auth_state=effective_resource_auth_state(member, item),
                    direct_permission=item.direct_permission,
                    team_sources=item.team_sources,
                )
                for item in projections.items
            ),
        )
