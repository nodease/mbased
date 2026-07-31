from __future__ import annotations

import uuid

from ..authorization import require_organization_manager
from ..errors import ResourceHidden
from ..models import Page, TeamMembershipProjection
from ..ports import (
    ActorAccessQueryPort,
    OrganizationAuthorizationPort,
    PermissionDenialPort,
)


class ListTeamMemberships:
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
        team_id: uuid.UUID | None = None,
        page: int,
        limit: int,
    ) -> Page[TeamMembershipProjection]:
        require_organization_manager(
            self.authorization,
            self.denial_recorder,
            actor_id=actor_id,
            organization_id=organization_id,
        )
        if self.query.get_member(organization_id, target_user_id) is None:
            raise ResourceHidden("Member not found.")
        return self.query.list_team_memberships(
            organization_id,
            target_user_id,
            team_id=team_id,
            page=page,
            limit=limit,
        )
