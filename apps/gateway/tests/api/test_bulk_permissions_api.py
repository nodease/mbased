from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

from apps.gateway.api.v1.endpoints.permissions import (
    _grant_bulk_workflow_permissions,
    grant_permissions_bulk,
)
from apps.shared.schemas.permission import BulkPermissionGrantRequest


def _workflow_payload(resource_ids, grantee_ids):
    return BulkPermissionGrantRequest(
        resource_type="workflow",
        resource_ids=resource_ids,
        grantee_type="team",
        grantee_ids=grantee_ids,
        auth_state="builder",
    )


def test_bulk_workflow_grant_builds_the_resource_grantee_cross_product_once():
    resource_ids = [uuid4(), uuid4()]
    grantee_ids = [uuid4(), uuid4()]
    payload = _workflow_payload(resource_ids, grantee_ids)
    request = MagicMock()
    db = MagicMock()
    current_user = SimpleNamespace(id=uuid4())
    organization_id = uuid4()
    use_case = MagicMock()

    def authorize(_request, _db, _user, _organization_id, workflow_id, team_id):
        return SimpleNamespace(), SimpleNamespace(id=workflow_id), SimpleNamespace(id=team_id)

    with (
        patch(
            "apps.gateway.api.v1.endpoints.permissions."
            "_authorize_team_workflow_permission_change",
            side_effect=authorize,
        ) as authorize_mock,
        patch(
            "apps.gateway.api.v1.endpoints.permissions."
            "_lock_workflow_mutation_app_scope"
        ) as lifecycle_lock,
        patch(
            "apps.gateway.api.v1.endpoints.permissions."
            "build_resource_permission_mutation_use_case",
            return_value=use_case,
        ),
    ):
        _grant_bulk_workflow_permissions(
            request,
            db,
            current_user,
            organization_id,
            payload,
        )

    assert authorize_mock.call_count == 4
    assert lifecycle_lock.call_count == 2
    commands = use_case.execute_many.call_args.args[0]
    assert len(commands) == 4
    assert {
        (command.resource_id, command.grantee_id) for command in commands
    } == {
        (resource_id, grantee_id)
        for resource_id in resource_ids
        for grantee_id in grantee_ids
    }
    assert {command.auth_state for command in commands} == {"builder"}


def test_bulk_permission_endpoint_returns_selection_counts():
    resource_ids = [uuid4(), uuid4()]
    grantee_ids = [uuid4(), uuid4()]
    payload = _workflow_payload(resource_ids, grantee_ids)
    request = MagicMock()
    db = MagicMock()
    current_user = SimpleNamespace(id=uuid4())
    organization_id = uuid4()

    with (
        patch(
            "apps.gateway.api.v1.endpoints.permissions._authenticate",
            return_value=current_user,
        ),
        patch(
            "apps.gateway.api.v1.endpoints.permissions.parse_organization_id",
            return_value=organization_id,
        ),
        patch(
            "apps.gateway.api.v1.endpoints.permissions."
            "_grant_bulk_workflow_permissions"
        ) as grant_mock,
    ):
        response = grant_permissions_bulk(payload, request, "org", db, None)

    grant_mock.assert_called_once_with(
        request,
        db,
        current_user,
        organization_id,
        payload,
    )
    assert response.resource_count == 2
    assert response.grantee_count == 2
    assert response.grant_count == 4
