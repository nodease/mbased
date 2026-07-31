from __future__ import annotations

import uuid

from ..authorization import require_organization_manager
from ..errors import ResourceHidden
from ..models import MemberAccessProfile, PermissionCounts
from ..policies import access_controls, app_creation_access, is_last_active_manager
from ..ports import (
    ActorAccessQueryPort,
    OrganizationAuthorizationPort,
    PermissionDenialPort,
)


class GetAccessProfile:
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
    ) -> MemberAccessProfile:
        require_organization_manager(
            self.authorization,
            self.denial_recorder,
            actor_id=actor_id,
            organization_id=organization_id,
        )
        member = self.query.get_member(organization_id, target_user_id)
        if member is None:
            raise ResourceHidden("Member not found.")

        active_manager_count = self.query.count_active_managers(organization_id)
        last_manager = is_last_active_manager(member, active_manager_count)
        app_permission = self.query.get_app_creation_permission(
            organization_id,
            target_user_id,
        )
        return MemberAccessProfile(
            member=member,
            control=access_controls(
                member,
                actor_id=actor_id,
                last_active_manager=last_manager,
            ),
            effective_access_enabled=member.effective_access_enabled,
            team_membership_count=self.query.count_team_memberships(
                organization_id,
                target_user_id,
            ),
            app_creation=app_creation_access(member, app_permission),
            permission_counts=PermissionCounts(
                direct=self.query.count_direct_permissions(
                    organization_id,
                    target_user_id,
                ),
                team_inherited=self.query.count_team_permission_sources(
                    organization_id,
                    target_user_id,
                ),
            ),
        )
