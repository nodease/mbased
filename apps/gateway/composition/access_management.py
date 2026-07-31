from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from apps.gateway.adapters.audit.management_reason_sanitizer import (
    ManagementReasonSanitizer,
)
from apps.gateway.adapters.audit.sqlalchemy_audit_recorder import (
    AccessManagementPermissionDenialRecorder,
    SqlAlchemyAccessManagementAuditRecorder,
)
from apps.gateway.adapters.db.access_management_mutation_adapter import (
    SqlAlchemyAccessManagementMutationAdapter,
)
from apps.gateway.adapters.db.access_management_query_adapter import (
    SqlAlchemyAccessManagementQueryAdapter,
)
from apps.gateway.adapters.db.sqlalchemy_unit_of_work import SqlAlchemyUnitOfWork
from apps.gateway.application.access_management.use_cases.execute_access_action import (
    ExecuteAccessAction,
)
from apps.gateway.application.access_management.use_cases.get_access_profile import (
    GetAccessProfile,
)
from apps.gateway.application.access_management.use_cases.list_resource_access import (
    ListResourceAccess,
)
from apps.gateway.application.access_management.use_cases.list_team_memberships import (
    ListTeamMemberships,
)


@dataclass(frozen=True)
class AccessManagementApplication:
    get_access_profile: GetAccessProfile
    list_team_memberships: ListTeamMemberships
    list_resource_access: ListResourceAccess
    execute_access_action: ExecuteAccessAction


def build_access_management_application(
    db: Session,
    *,
    actor: Any,
) -> AccessManagementApplication:
    query = SqlAlchemyAccessManagementQueryAdapter(db)
    mutation = SqlAlchemyAccessManagementMutationAdapter(db)
    denial = AccessManagementPermissionDenialRecorder()
    audit = SqlAlchemyAccessManagementAuditRecorder(db, actor=actor)
    return AccessManagementApplication(
        get_access_profile=GetAccessProfile(query, query, denial),
        list_team_memberships=ListTeamMemberships(query, query, denial),
        list_resource_access=ListResourceAccess(query, query, denial),
        execute_access_action=ExecuteAccessAction(
            query,
            mutation,
            mutation,
            mutation,
            mutation,
            mutation,
            audit,
            denial,
            ManagementReasonSanitizer(),
            SqlAlchemyUnitOfWork(db),
        ),
    )
