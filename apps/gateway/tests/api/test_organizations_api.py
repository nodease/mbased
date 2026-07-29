import asyncio
import unittest
from datetime import datetime, timezone
from operator import eq
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy.sql.operators import in_op, is_

from apps.gateway.api.v1.endpoints.organization import (
    list_organization_memberships,
    list_organizations,
)
from apps.gateway.api.v1.endpoints.notification import stream_notifications
from apps.gateway.auth.dependencies import get_current_user
from apps.gateway.main import app
from apps.shared.db.models.organization import Organization
from apps.shared.db.models.organization_membership import OrganizationMembership
from apps.shared.db.models.user import User
from apps.shared.db.session import get_db
from apps.shared.schemas.organization_membership import (
    MemberCurrentMonthUsage,
    OrganizationMemberListItemResponse,
    OrganizationMemberRemoveResponse,
    OrganizationMemberResponse,
    RevokedUserPermissionCounts,
)
from apps.shared.schemas.notification import NotificationItemResponse


class TestOrganizationsApi(unittest.TestCase):
    def setUp(self):
        self.audit_patchers = [
            patch("apps.gateway.utils.audit.record_audit"),
            patch("apps.gateway.main.record_audit"),
        ]
        for audit_patcher in self.audit_patchers:
            audit_patcher.start()

    def tearDown(self):
        for audit_patcher in reversed(self.audit_patchers):
            audit_patcher.stop()
        app.dependency_overrides = {}

    def test_list_organization_memberships_uses_active_and_invited_memberships(self):
        # 새 membership 목록 API가 active/invited organization membership 기준으로 조직을 조회하는지 검증한다.
        user_id = uuid4()
        organization = _organization(id=uuid4(), name="Acme", created_by=user_id)
        other_organization = _organization(id=uuid4(), name="Beta")
        removed_organization = _organization(id=uuid4(), name="Removed")
        db = _Session(
            [organization, other_organization, removed_organization],
            users=[_user(user_id)],
            memberships=[
                _membership(user_id, organization.id, auth_state="manager"),
                _membership(user_id, other_organization.id, membership_state="invited"),
                _membership(user_id, removed_organization.id, membership_state="removed"),
            ],
        )

        response = list_organization_memberships(
            db=db,
            current_user=SimpleNamespace(id=user_id),
        )
        query = db.query_value

        self.assertEqual(
            [item.id for item in response],
            [organization.id, other_organization.id],
        )
        self.assertEqual(response[0].membership_state, "active")
        self.assertEqual(response[0].organization_auth_state, "manager")
        self.assertEqual(response[1].membership_state, "invited")
        self.assertFalse(query.distinct_called)
        self.assertEqual(len(query.join_values), 1)

        user_filter, membership_state_filter, org_active_filter = (
            query.filter_expressions
        )
        self.assertEqual(str(user_filter.left), "organization_memberships.user_id")
        self.assertIs(user_filter.operator, eq)
        self.assertEqual(user_filter.right.value, user_id)

        self.assertEqual(
            str(membership_state_filter.left),
            "organization_memberships.membership_state",
        )
        self.assertIs(membership_state_filter.operator, in_op)
        self.assertEqual(
            set(membership_state_filter.right.value),
            {"active", "invited"},
        )

        self.assertEqual(str(org_active_filter.left), "organization.is_active")
        self.assertIs(org_active_filter.operator, is_)
        self.assertEqual(str(org_active_filter.right), "true")

    def test_list_organizations_returns_active_context_candidates_only(self):
        # 기존 /organizations API는 frontend 호환을 위해 active organization만 OrganizationResponse로 반환한다.
        user_id = uuid4()
        active_organization = _organization(
            id=uuid4(),
            name="Acme",
            created_by=user_id,
        )
        invited_organization = _organization(id=uuid4(), name="Beta")
        db = _Session(
            [active_organization, invited_organization],
            users=[_user(user_id)],
            memberships=[
                _membership(
                    user_id,
                    active_organization.id,
                    auth_state="manager",
                ),
                _membership(
                    user_id,
                    invited_organization.id,
                    membership_state="invited",
                ),
            ],
        )

        response = list_organizations(
            db=db,
            current_user=SimpleNamespace(id=user_id),
        )
        query = db.query_value

        self.assertEqual([item.id for item in response], [active_organization.id])
        self.assertTrue(response[0].is_manager)
        self.assertFalse(hasattr(response[0], "membership_state"))

        user_filter, membership_state_filter, org_active_filter = (
            query.filter_expressions
        )
        self.assertEqual(str(user_filter.left), "organization_memberships.user_id")
        self.assertIs(user_filter.operator, eq)
        self.assertEqual(user_filter.right.value, user_id)
        self.assertEqual(
            str(membership_state_filter.left),
            "organization_memberships.membership_state",
        )
        self.assertIs(membership_state_filter.operator, eq)
        self.assertEqual(membership_state_filter.right.value, "active")
        self.assertEqual(str(org_active_filter.left), "organization.is_active")
        self.assertIs(org_active_filter.operator, is_)

    def test_route_returns_current_user_organizations(self):
        # GET /api/v1/organizations 라우터가 현재 사용자의 조직 목록을 응답 스키마로 직렬화하는지 검증한다.
        organization_id = uuid4()
        invited_organization_id = uuid4()
        user_id = uuid4()
        created_at = datetime(2026, 6, 27, 1, 2, 3, tzinfo=timezone.utc)
        updated_at = datetime(2026, 6, 27, 4, 5, 6, tzinfo=timezone.utc)
        organization = _organization(
            id=organization_id,
            name="Acme",
            created_by=user_id,
            created_at=created_at,
            updated_at=updated_at,
        )
        invited_organization = _organization(
            id=invited_organization_id,
            name="Beta",
            created_by=user_id,
            created_at=created_at,
            updated_at=updated_at,
        )

        app.dependency_overrides[get_db] = lambda: _Session(
            [organization, invited_organization],
            users=[_user(user_id)],
            memberships=[
                _membership(user_id, organization_id, auth_state="manager"),
                _membership(
                    user_id,
                    invited_organization_id,
                    membership_state="invited",
                ),
            ],
        )
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=user_id)

        response = TestClient(app).get("/api/v1/organizations")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            [
                {
                    "id": str(organization_id),
                    "name": "Acme",
                    "options": {},
                    "is_active": True,
                    "is_manager": True,
                    "created_at": "2026-06-27T01:02:03Z",
                    "updated_at": "2026-06-27T04:05:06Z",
                },
            ],
        )

    def test_memberships_route_returns_current_user_memberships(self):
        # GET /api/v1/organizations/memberships는 active와 invited membership을 함께 반환한다.
        organization_id = uuid4()
        invited_organization_id = uuid4()
        user_id = uuid4()
        created_at = datetime(2026, 6, 27, 1, 2, 3, tzinfo=timezone.utc)
        updated_at = datetime(2026, 6, 27, 4, 5, 6, tzinfo=timezone.utc)
        organization = _organization(
            id=organization_id,
            name="Acme",
            created_by=user_id,
            created_at=created_at,
            updated_at=updated_at,
        )
        invited_organization = _organization(
            id=invited_organization_id,
            name="Beta",
            created_by=user_id,
            created_at=created_at,
            updated_at=updated_at,
        )

        app.dependency_overrides[get_db] = lambda: _Session(
            [organization, invited_organization],
            users=[_user(user_id)],
            memberships=[
                _membership(user_id, organization_id, auth_state="manager"),
                _membership(
                    user_id,
                    invited_organization_id,
                    membership_state="invited",
                ),
            ],
        )
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=user_id)

        response = TestClient(app).get("/api/v1/organizations/memberships")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            [
                {
                    "id": str(organization_id),
                    "name": "Acme",
                    "membership_state": "active",
                    "organization_auth_state": "manager",
                    "is_active": True,
                },
                {
                    "id": str(invited_organization_id),
                    "name": "Beta",
                    "membership_state": "invited",
                    "organization_auth_state": "member",
                    "is_active": True,
                },
            ],
        )

    def test_route_returns_member_organization_detail(self):
        # GET /api/v1/organizations/{organization_id}가 현재 사용자의 active organization membership scope 안에 있는 조직만 반환하는지 검증한다.
        organization_id = uuid4()
        user_id = uuid4()
        created_at = datetime(2026, 6, 27, 1, 2, 3, tzinfo=timezone.utc)
        updated_at = datetime(2026, 6, 27, 4, 5, 6, tzinfo=timezone.utc)
        organization = _organization(
            id=organization_id,
            name="Acme",
            created_at=created_at,
            updated_at=updated_at,
        )
        db = _Session(
            [organization],
            users=[_user(user_id)],
            memberships=[_membership(user_id, organization_id)],
        )
        app.dependency_overrides[get_db] = lambda: db
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=user_id)

        response = TestClient(app).get(f"/api/v1/organizations/{organization_id}")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "id": str(organization_id),
                "name": "Acme",
                "options": {},
                "is_active": True,
                "is_manager": False,
                "created_at": "2026-06-27T01:02:03Z",
                "updated_at": "2026-06-27T04:05:06Z",
            },
        )

        _assert_active_membership_scope_filters(
            self, db.membership_query, organization_id, user_id
        )

    def test_route_returns_current_organization_from_header(self):
        # GET /api/v1/organizations/current는 header의 organization id를 active membership scope로 검증한다.
        organization_id = uuid4()
        user_id = uuid4()
        created_at = datetime(2026, 6, 27, 1, 2, 3, tzinfo=timezone.utc)
        updated_at = datetime(2026, 6, 27, 4, 5, 6, tzinfo=timezone.utc)
        organization = _organization(
            id=organization_id,
            name="Acme",
            created_at=created_at,
            updated_at=updated_at,
        )
        db = _Session(
            [organization],
            users=[_user(user_id)],
            memberships=[_membership(user_id, organization_id)],
        )

        app.dependency_overrides[get_db] = lambda: db
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=user_id)

        response = TestClient(app).get(
            "/api/v1/organizations/current",
            headers={"X-Organization-Id": str(organization_id)},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "id": str(organization_id),
                "name": "Acme",
                "options": {},
                "is_active": True,
                "is_manager": False,
                "created_at": "2026-06-27T01:02:03Z",
                "updated_at": "2026-06-27T04:05:06Z",
            },
        )

        _assert_active_membership_scope_filters(
            self, db.membership_query, organization_id, user_id
        )

    def test_route_marks_managed_by_user_as_manager(self):
        # organization.managed_by인 사용자는 조회 응답에서 manager로 표시되어야 한다.
        organization_id = uuid4()
        user_id = uuid4()
        created_at = datetime(2026, 6, 27, 1, 2, 3, tzinfo=timezone.utc)
        updated_at = datetime(2026, 6, 27, 4, 5, 6, tzinfo=timezone.utc)
        organization = _organization(
            id=organization_id,
            name="Acme",
            managed_by=user_id,
            created_at=created_at,
            updated_at=updated_at,
        )
        app.dependency_overrides[get_db] = lambda: _Session([organization])
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=user_id)

        response = TestClient(app).get(f"/api/v1/organizations/{organization_id}")

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["is_manager"])

    def test_route_requires_current_organization_header(self):
        # Header가 없으면 active organization을 결정할 수 없으므로 organization.required를 반환한다.
        user_id = uuid4()

        app.dependency_overrides[get_db] = lambda: SimpleNamespace(
            query=lambda model: _Query([])
        )
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=user_id)

        response = TestClient(app).get(
            "/api/v1/organizations/current",
            headers={"X-Request-ID": "98d6d88b-8d7a-46fd-8d12-f2024d2fac4b"},
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.json(),
            _error("organization.required", "X-Organization-Id header is required."),
        )

    def test_route_rejects_invalid_current_organization_header(self):
        # Header 값이 UUID가 아니면 scope 조회 전에 validation.failed로 거부한다.
        user_id = uuid4()

        app.dependency_overrides[get_db] = lambda: SimpleNamespace(
            query=lambda model: _Query([])
        )
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=user_id)

        response = TestClient(app).get(
            "/api/v1/organizations/current",
            headers={
                "X-Organization-Id": "not-a-uuid",
                "X-Request-ID": "98d6d88b-8d7a-46fd-8d12-f2024d2fac4b",
            },
        )

        self.assertEqual(response.status_code, 422)
        self.assertEqual(
            response.json(),
            _error(
                "validation.failed",
                "X-Organization-Id must be a valid UUID.",
                {"field": "X-Organization-Id"},
            ),
        )

    def test_route_hides_current_organization_outside_user_memberships(self):
        # Organization이 없거나 active membership scope 밖이면 존재 여부를 숨기기 위해 404로 응답한다.
        organization_id = uuid4()
        user_id = uuid4()

        app.dependency_overrides[get_db] = lambda: SimpleNamespace(
            query=lambda model: _Query([])
        )
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=user_id)

        response = TestClient(app).get(
            "/api/v1/organizations/current",
            headers={
                "X-Organization-Id": str(organization_id),
                "X-Request-ID": "98d6d88b-8d7a-46fd-8d12-f2024d2fac4b",
            },
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(
            response.json(),
            _error("resource.not_found", "Organization not found."),
        )

    def test_route_lists_members(self):
        organization_id = uuid4()
        user_id = uuid4()
        member = _member_list_item_response(organization_id=organization_id)

        app.dependency_overrides[get_db] = lambda: SimpleNamespace()
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=user_id)

        with patch(
            "apps.gateway.api.v1.endpoints.organization."
            "OrganizationMemberService.list_members",
            return_value=[member],
        ) as service:
            response = TestClient(app).get(
                f"/api/v1/organizations/{organization_id}/members?state=active",
                headers={"X-Organization-Id": str(organization_id)},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()[0]["id"], str(member.id))
        self.assertEqual(
            response.json()[0]["current_month_usage"],
            {
                "total_cost": 3.5,
                "workflow_execution_cost": 2.0,
                "agent_builder_cost": 1.5,
                "usage_data_complete": True,
                "unresolved_provider_call_count": 0,
            },
        )
        service.assert_called_once()
        self.assertEqual(service.call_args.args[2], organization_id)
        self.assertEqual(service.call_args.args[3], "active")

    def test_route_invites_member(self):
        organization_id = uuid4()
        user_id = uuid4()
        target_user_id = uuid4()
        member = _member_response(
            organization_id=organization_id,
            user_id=target_user_id,
            membership_state="invited",
        )

        app.dependency_overrides[get_db] = lambda: SimpleNamespace()
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=user_id)

        with patch(
            "apps.gateway.api.v1.endpoints.organization."
            "OrganizationMemberService.invite_member",
            return_value=member,
        ) as service:
            response = TestClient(app).post(
                f"/api/v1/organizations/{organization_id}/members/invitations",
                headers={"X-Organization-Id": str(organization_id)},
                json={"user_id": str(target_user_id)},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["membership_state"], "invited")
        self.assertEqual(service.call_args.args[2], organization_id)
        self.assertEqual(service.call_args.args[3].user_id, target_user_id)
        self.assertEqual(service.call_args.args[3].organization_auth_state, "member")

    def test_route_rejects_unknown_member_invite_field(self):
        organization_id = uuid4()
        user_id = uuid4()
        target_user_id = uuid4()

        app.dependency_overrides[get_db] = lambda: SimpleNamespace()
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=user_id)

        with patch(
            "apps.gateway.api.v1.endpoints.organization."
            "OrganizationMemberService.invite_member",
        ) as service:
            response = TestClient(app).post(
                f"/api/v1/organizations/{organization_id}/members/invitations",
                headers={"X-Organization-Id": str(organization_id)},
                json={
                    "user_id": str(target_user_id),
                    "organization_auth_sate": "manager",
                },
            )

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["error"]["code"], "validation.failed")
        service.assert_not_called()

    def test_route_accepts_invitation_on_literal_me_path(self):
        organization_id = uuid4()
        user_id = uuid4()
        member = _member_response(
            organization_id=organization_id,
            user_id=user_id,
            membership_state="active",
        )

        app.dependency_overrides[get_db] = lambda: SimpleNamespace()
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=user_id)

        with patch(
            "apps.gateway.api.v1.endpoints.organization."
            "OrganizationMemberService.accept_invitation",
            return_value=member,
        ) as service:
            response = TestClient(app).post(
                f"/api/v1/organizations/{organization_id}/members/me/accept"
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["membership_state"], "active")
        self.assertEqual(service.call_args.args[2], organization_id)

    def test_route_declines_invitation_on_literal_me_path(self):
        organization_id = uuid4()
        user_id = uuid4()
        member = _member_response(
            organization_id=organization_id,
            user_id=user_id,
            membership_state="removed",
        )

        app.dependency_overrides[get_db] = lambda: SimpleNamespace()
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=user_id)

        with patch(
            "apps.gateway.api.v1.endpoints.organization."
            "OrganizationMemberService.decline_invitation",
            return_value=member,
        ) as service:
            response = TestClient(app).post(
                f"/api/v1/organizations/{organization_id}/members/me/decline"
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["membership_state"], "removed")
        self.assertEqual(service.call_args.args[2], organization_id)

    def test_notifications_list_returns_invitation_items(self):
        user_id = uuid4()
        organization_id = uuid4()
        item = NotificationItemResponse(
            id="organization_invitation:membership-1",
            type="organization.invitation",
            organization_id=organization_id,
            organization_name="Acme",
            organization_auth_state="member",
            created_at=datetime.now(timezone.utc),
        )

        app.dependency_overrides[get_db] = lambda: SimpleNamespace()
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=user_id)

        with patch(
            "apps.gateway.api.v1.endpoints.notification."
            "NotificationService.list_notifications",
            return_value=[item],
        ) as service:
            response = TestClient(app).get("/api/v1/notifications")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["items"][0]["type"], "organization.invitation")
        self.assertEqual(response.json()["items"][0]["organization_name"], "Acme")
        self.assertEqual(service.call_args.args[1], user_id)

    def test_notifications_stream_sets_no_buffer_headers(self):
        response = asyncio.run(
            stream_notifications(
                request=SimpleNamespace(),
                current_user=SimpleNamespace(id=uuid4()),
            )
        )

        self.assertIn("text/event-stream", response.headers["content-type"])
        self.assertEqual(response.headers["cache-control"], "no-cache, no-transform")
        self.assertEqual(response.headers["x-accel-buffering"], "no")
        self.assertEqual(response.headers["connection"], "keep-alive")

    def test_route_updates_member(self):
        organization_id = uuid4()
        user_id = uuid4()
        target_user_id = uuid4()
        member = _member_response(
            organization_id=organization_id,
            user_id=target_user_id,
            organization_auth_state="manager",
        )

        app.dependency_overrides[get_db] = lambda: SimpleNamespace()
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=user_id)

        with patch(
            "apps.gateway.api.v1.endpoints.organization."
            "OrganizationMemberService.update_member",
            return_value=member,
        ) as service:
            response = TestClient(app).patch(
                f"/api/v1/organizations/{organization_id}/members/{target_user_id}",
                headers={"X-Organization-Id": str(organization_id)},
                json={"organization_auth_state": "manager"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["organization_auth_state"], "manager")
        self.assertEqual(service.call_args.args[2], organization_id)
        self.assertEqual(service.call_args.args[3], target_user_id)

    def test_route_rejects_unknown_member_update_field(self):
        organization_id = uuid4()
        user_id = uuid4()
        target_user_id = uuid4()

        app.dependency_overrides[get_db] = lambda: SimpleNamespace()
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=user_id)

        with patch(
            "apps.gateway.api.v1.endpoints.organization."
            "OrganizationMemberService.update_member",
        ) as service:
            response = TestClient(app).patch(
                f"/api/v1/organizations/{organization_id}/members/{target_user_id}",
                headers={"X-Organization-Id": str(organization_id)},
                json={"organization_auth_sate": "manager"},
            )

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["error"]["code"], "validation.failed")
        service.assert_not_called()

    def test_route_removes_member(self):
        organization_id = uuid4()
        user_id = uuid4()
        target_user_id = uuid4()

        app.dependency_overrides[get_db] = lambda: SimpleNamespace()
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=user_id)

        with patch(
            "apps.gateway.api.v1.endpoints.organization."
            "OrganizationMemberService.remove_member",
            return_value=OrganizationMemberRemoveResponse(
                status="removed",
                removed_team_memberships=1,
                revoked_user_permissions=RevokedUserPermissionCounts(
                    workflow=2,
                    llm_credential=1,
                ),
            ),
        ) as service:
            response = TestClient(app).delete(
                f"/api/v1/organizations/{organization_id}/members/{target_user_id}",
                headers={"X-Organization-Id": str(organization_id)},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "removed")
        self.assertEqual(response.json()["removed_team_memberships"], 1)
        self.assertEqual(service.call_args.args[2], organization_id)
        self.assertEqual(service.call_args.args[3], target_user_id)

    def test_member_management_routes_require_matching_organization_header(self):
        organization_id = uuid4()
        header_organization_id = uuid4()
        user_id = uuid4()
        target_user_id = uuid4()

        app.dependency_overrides[get_db] = lambda: SimpleNamespace()
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=user_id)

        cases = [
            (
                "get",
                f"/api/v1/organizations/{organization_id}/members",
                "list_members",
                None,
            ),
            (
                "post",
                f"/api/v1/organizations/{organization_id}/members/invitations",
                "invite_member",
                {"user_id": str(target_user_id)},
            ),
            (
                "patch",
                f"/api/v1/organizations/{organization_id}/members/{target_user_id}",
                "update_member",
                {"organization_auth_state": "manager"},
            ),
            (
                "delete",
                f"/api/v1/organizations/{organization_id}/members/{target_user_id}",
                "remove_member",
                None,
            ),
        ]
        header_cases = [
            (
                {"X-Request-ID": "98d6d88b-8d7a-46fd-8d12-f2024d2fac4b"},
                400,
                _error("organization.required", "X-Organization-Id header is required."),
            ),
            (
                {
                    "X-Organization-Id": "not-a-uuid",
                    "X-Request-ID": "98d6d88b-8d7a-46fd-8d12-f2024d2fac4b",
                },
                422,
                _error(
                    "validation.failed",
                    "X-Organization-Id must be a valid UUID.",
                    {"field": "X-Organization-Id"},
                ),
            ),
            (
                {
                    "X-Organization-Id": str(header_organization_id),
                    "X-Request-ID": "98d6d88b-8d7a-46fd-8d12-f2024d2fac4b",
                },
                404,
                _error("resource.not_found", "Organization not found."),
            ),
        ]

        for method, path, service_name, payload in cases:
            for headers, status_code, expected in header_cases:
                with self.subTest(method=method, path=path, headers=headers):
                    with patch(
                        "apps.gateway.api.v1.endpoints.organization."
                        f"OrganizationMemberService.{service_name}",
                    ) as service:
                        kwargs = {}
                        if payload is not None:
                            kwargs["json"] = payload
                        response = getattr(TestClient(app), method)(
                            path,
                            headers=headers,
                            **kwargs,
                        )

                    self.assertEqual(response.status_code, status_code)
                    self.assertEqual(response.json(), expected)
                    service.assert_not_called()

    def test_member_routes_wrap_service_errors(self):
        organization_id = uuid4()
        user_id = uuid4()
        target_user_id = uuid4()

        app.dependency_overrides[get_db] = lambda: SimpleNamespace()
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=user_id)

        cases = [
            (
                "get",
                f"/api/v1/organizations/{organization_id}/members?state=bad",
                "list_members",
                HTTPException(status_code=400, detail="Invalid membership state."),
                "validation.failed",
            ),
            (
                "post",
                f"/api/v1/organizations/{organization_id}/members/invitations",
                "invite_member",
                HTTPException(status_code=403, detail="Permission denied."),
                "permission.denied",
            ),
            (
                "post",
                f"/api/v1/organizations/{organization_id}/members/me/accept",
                "accept_invitation",
                HTTPException(status_code=404, detail="Invitation not found."),
                "resource.not_found",
            ),
            (
                "patch",
                f"/api/v1/organizations/{organization_id}/members/{target_user_id}",
                "update_member",
                HTTPException(status_code=409, detail="Invitation cannot be accepted."),
                "resource.conflict",
            ),
        ]

        for method, path, service_name, exc, code in cases:
            with self.subTest(method=method, path=path):
                with patch(
                    "apps.gateway.api.v1.endpoints.organization."
                    f"OrganizationMemberService.{service_name}",
                    side_effect=exc,
                ):
                    kwargs = {}
                    if method in {"post", "patch"} and service_name != "accept_invitation":
                        if method == "patch":
                            kwargs["json"] = {"organization_auth_state": "manager"}
                        else:
                            kwargs["json"] = {"user_id": str(target_user_id)}
                    headers = {"X-Request-ID": "98d6d88b-8d7a-46fd-8d12-f2024d2fac4b"}
                    if service_name != "accept_invitation":
                        headers["X-Organization-Id"] = str(organization_id)
                    response = getattr(TestClient(app), method)(
                        path,
                        headers=headers,
                        **kwargs,
                    )

                self.assertEqual(response.status_code, exc.status_code)
                self.assertEqual(response.json()["error"]["code"], code)
                self.assertEqual(response.json()["error"]["request_id"], "98d6d88b-8d7a-46fd-8d12-f2024d2fac4b")

    def test_route_hides_organization_outside_user_memberships(self):
        # 조직이 없거나 현재 사용자의 membership scope 밖이면 존재 여부를 노출하지 않고 404로 숨긴다.
        organization_id = uuid4()
        user_id = uuid4()

        app.dependency_overrides[get_db] = lambda: SimpleNamespace(
            query=lambda model: _Query([])
        )
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=user_id)

        response = TestClient(app).get(
            f"/api/v1/organizations/{organization_id}",
            headers={"X-Request-ID": "98d6d88b-8d7a-46fd-8d12-f2024d2fac4b"},
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(
            response.json(),
            _error("resource.not_found", "Organization not found."),
        )

    def test_patch_organization_allows_owner_without_team_membership(self):
        organization_id = uuid4()
        user_id = uuid4()
        organization = _organization(
            id=organization_id,
            name="Acme",
            created_by=user_id,
            options={"theme": "legacy", "limits": {"runs": 10}},
        )
        db = _Session([organization])

        app.dependency_overrides[get_db] = lambda: db
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=user_id)

        response = TestClient(app).patch(
            f"/api/v1/organizations/{organization_id}",
            headers={"X-Organization-Id": str(organization_id)},
            json={"name": "  Acme Korea  ", "options": {"theme": "modern"}},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["name"], "Acme Korea")
        self.assertEqual(response.json()["options"], {"theme": "modern"})
        self.assertTrue(response.json()["is_manager"])
        self.assertEqual(organization.name, "Acme Korea")
        self.assertEqual(organization.options, {"theme": "modern"})
        self.assertEqual(db.query_value.join_values, [])
        _assert_patch_scope_filters(self, db.query_value, organization_id)
        self.assertTrue(db.commit_called)
        self.assertEqual(db.refresh_values, [organization])

    def test_patch_organization_requires_organization_header(self):
        organization_id = uuid4()
        user_id = uuid4()

        app.dependency_overrides[get_db] = lambda: _Session([])
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=user_id)

        response = TestClient(app).patch(
            f"/api/v1/organizations/{organization_id}",
            headers={"X-Request-ID": "98d6d88b-8d7a-46fd-8d12-f2024d2fac4b"},
            json={"name": "Acme Korea"},
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.json(),
            _error("organization.required", "X-Organization-Id header is required."),
        )

    def test_patch_organization_rejects_invalid_organization_header(self):
        organization_id = uuid4()
        user_id = uuid4()

        app.dependency_overrides[get_db] = lambda: _Session([])
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=user_id)

        response = TestClient(app).patch(
            f"/api/v1/organizations/{organization_id}",
            headers={
                "X-Organization-Id": "not-a-uuid",
                "X-Request-ID": "98d6d88b-8d7a-46fd-8d12-f2024d2fac4b",
            },
            json={"name": "Acme Korea"},
        )

        self.assertEqual(response.status_code, 422)
        self.assertEqual(
            response.json(),
            _error(
                "validation.failed",
                "X-Organization-Id must be a valid UUID.",
                {"field": "X-Organization-Id"},
            ),
        )

    def test_patch_organization_hides_header_path_mismatch(self):
        organization_id = uuid4()
        header_organization_id = uuid4()
        user_id = uuid4()

        app.dependency_overrides[get_db] = lambda: _Session([])
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=user_id)

        response = TestClient(app).patch(
            f"/api/v1/organizations/{organization_id}",
            headers={
                "X-Organization-Id": str(header_organization_id),
                "X-Request-ID": "98d6d88b-8d7a-46fd-8d12-f2024d2fac4b",
            },
            json={"name": "Acme Korea"},
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(
            response.json(),
            _error("resource.not_found", "Organization not found."),
        )

    def test_patch_organization_rejects_non_manager(self):
        organization_id = uuid4()
        user_id = uuid4()
        organization = _organization(id=organization_id, name="Acme")

        app.dependency_overrides[get_db] = lambda: _Session(
            [organization],
            users=[_user(user_id)],
            memberships=[_membership(user_id, organization_id)],
        )
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=user_id)

        response = TestClient(app).patch(
            f"/api/v1/organizations/{organization_id}",
            headers={
                "X-Organization-Id": str(organization_id),
                "X-Request-ID": "98d6d88b-8d7a-46fd-8d12-f2024d2fac4b",
            },
            json={"name": "Acme Korea"},
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(
            response.json(),
            _error("permission.denied", "Permission denied."),
        )

    def test_patch_organization_non_manager_records_scoped_permission_denial(self):
        organization_id = uuid4()
        user_id = uuid4()
        organization = _organization(id=organization_id, name="Acme")
        events = []

        app.dependency_overrides[get_db] = lambda: _Session(
            [organization],
            users=[_user(user_id)],
            memberships=[_membership(user_id, organization_id)],
        )
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=user_id)

        with patch(
            "apps.shared.services.permission_audit.record_audit",
            side_effect=lambda **event: events.append(event),
        ), patch("apps.gateway.main.record_audit") as global_record_audit:
            response = TestClient(app).patch(
                f"/api/v1/organizations/{organization_id}",
                headers={
                    "X-Organization-Id": str(organization_id),
                    "X-Request-ID": "98d6d88b-8d7a-46fd-8d12-f2024d2fac4b",
                },
                json={"name": "Acme Korea"},
            )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual(event["action"], "permission.denied")
        self.assertEqual(event["actor_id"], user_id)
        self.assertEqual(event["actor_type"], "user")
        self.assertEqual(event["category"], "action")
        self.assertEqual(event["status"], "failure")
        self.assertEqual(event["target_type"], "organization")
        self.assertEqual(event["target_id"], organization_id)
        self.assertEqual(
            event["metadata"]["organization_id"],
            str(organization_id),
        )
        global_record_audit.assert_not_called()

    def test_patch_organization_hides_missing_or_out_of_scope_organization(self):
        organization_id = uuid4()
        user_id = uuid4()

        app.dependency_overrides[get_db] = lambda: _Session([])
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=user_id)

        with patch(
            "apps.shared.services.permission_audit.record_audit"
        ) as scoped_record_audit:
            response = TestClient(app).patch(
                f"/api/v1/organizations/{organization_id}",
                headers={
                    "X-Organization-Id": str(organization_id),
                    "X-Request-ID": "98d6d88b-8d7a-46fd-8d12-f2024d2fac4b",
                },
                json={"name": "Acme Korea"},
            )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(
            response.json(),
            _error("resource.not_found", "Organization not found."),
        )
        scoped_record_audit.assert_not_called()

    def test_patch_organization_hides_different_organization_from_query(self):
        organization_id = uuid4()
        user_id = uuid4()
        organization = _organization(
            id=uuid4(),
            name="Acme",
            created_by=user_id,
        )

        app.dependency_overrides[get_db] = lambda: _Session([organization])
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=user_id)

        response = TestClient(app).patch(
            f"/api/v1/organizations/{organization_id}",
            headers={
                "X-Organization-Id": str(organization_id),
                "X-Request-ID": "98d6d88b-8d7a-46fd-8d12-f2024d2fac4b",
            },
            json={"name": "Acme Korea"},
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(
            response.json(),
            _error("resource.not_found", "Organization not found."),
        )
        self.assertEqual(organization.name, "Acme")

    def test_patch_organization_hides_inactive_organization(self):
        organization_id = uuid4()
        user_id = uuid4()
        organization = _organization(
            id=organization_id,
            name="Acme",
            created_by=user_id,
            is_active=False,
        )

        app.dependency_overrides[get_db] = lambda: _Session([organization])
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=user_id)

        response = TestClient(app).patch(
            f"/api/v1/organizations/{organization_id}",
            headers={
                "X-Organization-Id": str(organization_id),
                "X-Request-ID": "98d6d88b-8d7a-46fd-8d12-f2024d2fac4b",
            },
            json={"name": "Acme Korea"},
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(
            response.json(),
            _error("resource.not_found", "Organization not found."),
        )
        self.assertEqual(organization.name, "Acme")

    def test_patch_organization_rejects_empty_update(self):
        organization_id = uuid4()
        user_id = uuid4()
        organization = _organization(
            id=organization_id,
            name="Acme",
            created_by=user_id,
        )

        app.dependency_overrides[get_db] = lambda: _Session([organization])
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=user_id)

        response = TestClient(app).patch(
            f"/api/v1/organizations/{organization_id}",
            headers={
                "X-Organization-Id": str(organization_id),
                "X-Request-ID": "98d6d88b-8d7a-46fd-8d12-f2024d2fac4b",
            },
            json={},
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.json(),
            _error("validation.failed", "No organization fields to update."),
        )

    def test_patch_organization_rejects_blank_name(self):
        organization_id = uuid4()
        user_id = uuid4()
        organization = _organization(
            id=organization_id,
            name="Acme",
            created_by=user_id,
        )

        app.dependency_overrides[get_db] = lambda: _Session([organization])
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=user_id)

        response = TestClient(app).patch(
            f"/api/v1/organizations/{organization_id}",
            headers={
                "X-Organization-Id": str(organization_id),
                "X-Request-ID": "98d6d88b-8d7a-46fd-8d12-f2024d2fac4b",
            },
            json={"name": "   "},
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.json(),
            _error(
                "validation.failed",
                "Organization name is required.",
                {"field": "name"},
            ),
        )

    def test_patch_organization_rejects_name_longer_than_database_column(self):
        organization_id = uuid4()
        user_id = uuid4()
        organization = _organization(
            id=organization_id,
            name="Acme",
            created_by=user_id,
        )

        app.dependency_overrides[get_db] = lambda: _Session([organization])
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=user_id)

        response = TestClient(app).patch(
            f"/api/v1/organizations/{organization_id}",
            headers={
                "X-Organization-Id": str(organization_id),
                "X-Request-ID": "98d6d88b-8d7a-46fd-8d12-f2024d2fac4b",
            },
            json={"name": "A" * 256},
        )

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["error"]["code"], "validation.failed")
        self.assertEqual(response.json()["error"]["request_id"], "98d6d88b-8d7a-46fd-8d12-f2024d2fac4b")
        self.assertEqual(
            response.json()["error"]["message"],
            "Request validation failed.",
        )
        self.assertEqual(organization.name, "Acme")


class _Query:
    def __init__(self, items):
        self.items = items
        self.join_values = []
        self.filter_expressions = []
        self.order_by_values = []
        self.distinct_called = False

    def join(self, *args):
        self.join_values.append(args)
        return self

    def filter(self, *expressions):
        self.filter_expressions.extend(expressions)
        return self

    def distinct(self):
        self.distinct_called = True
        seen_ids = set()
        unique_items = []
        for item in self.items:
            if item.id in seen_ids:
                continue
            seen_ids.add(item.id)
            unique_items.append(item)
        self.items = unique_items
        return self

    def order_by(self, *args):
        self.order_by_values.extend(args)
        return self

    def all(self):
        return self.items

    def first(self):
        for item in self.items:
            if self._matches_filters(item):
                return item
        return None

    def _matches_filters(self, item):
        return all(
            self._matches_filter(item, expression)
            for expression in self.filter_expressions
        )

    def _matches_filter(self, item, expression):
        if not hasattr(expression, "left"):
            return True

        left = str(expression.left)
        if left == "organization.id" and expression.operator is eq:
            return item.id == expression.right.value
        if left == "organization.is_active" and expression.operator is is_:
            return item.is_active is (str(expression.right) == "true")
        if left == "users.id" and expression.operator is eq:
            return item.id == expression.right.value
        if left == "users.deactivated_at" and expression.operator is is_:
            return item.deactivated_at is None
        if left == "organization_memberships.user_id" and expression.operator is eq:
            return item.user_id == expression.right.value
        if (
            left == "organization_memberships.organization_id"
            and expression.operator is eq
        ):
            return item.organization_id == expression.right.value
        if (
            left == "organization_memberships.membership_state"
            and expression.operator is eq
        ):
            return item.membership_state == expression.right.value
        if left.startswith("organization."):
            raise AssertionError(f"Unsupported organization filter: {expression}")
        return True


class _OrganizationMembershipQuery(_Query):
    def __init__(self, organizations, memberships):
        rows = []
        by_id = {organization.id: organization for organization in organizations}
        for membership in memberships:
            organization = by_id.get(membership.organization_id)
            if organization is not None:
                rows.append((organization, membership))
        super().__init__(rows)

    def all(self):
        return [item for item in self.items if self._matches_filters(item)]

    def _matches_filter(self, item, expression):
        if not hasattr(expression, "left"):
            return True

        organization, membership = item
        left = str(expression.left)
        if left == "organization_memberships.user_id" and expression.operator is eq:
            return membership.user_id == expression.right.value
        if (
            left == "organization_memberships.membership_state"
            and expression.operator is in_op
        ):
            return membership.membership_state in expression.right.value
        if (
            left == "organization_memberships.membership_state"
            and expression.operator is eq
        ):
            return membership.membership_state == expression.right.value
        if left == "organization.is_active" and expression.operator is is_:
            return organization.is_active is (str(expression.right) == "true")
        return True


class _Session:
    def __init__(self, items, users=None, memberships=None):
        self.organizations = items
        self.users = users if users is not None else _users_from_organizations(items)
        self.memberships = memberships or []
        self.query_value = None
        self.membership_query = None
        self.commit_called = False
        self.refresh_values = []

    def query(self, *models):
        if models == (Organization,):
            query = _Query(self.organizations)
            if self.query_value is None:
                self.query_value = query
            return query
        if models == (Organization, OrganizationMembership):
            query = _OrganizationMembershipQuery(self.organizations, self.memberships)
            if self.query_value is None:
                self.query_value = query
            return query
        if models == (User,):
            return _Query(self.users)
        if models == (OrganizationMembership,):
            self.membership_query = _Query(self.memberships)
            return self.membership_query
        return _Query([])

    def commit(self):
        self.commit_called = True

    def refresh(self, value):
        self.refresh_values.append(value)


def _organization(
    id,
    name,
    created_by=None,
    managed_by=None,
    options=None,
    is_active=True,
    created_at=None,
    updated_at=None,
):
    now = datetime.now(timezone.utc)
    return Organization(
        id=id,
        name=name,
        options=options or {},
        created_by=created_by or uuid4(),
        managed_by=managed_by,
        is_active=is_active,
        created_at=created_at or now,
        updated_at=updated_at or now,
    )


def _user(user_id, deactivated_at=None):
    return SimpleNamespace(id=user_id, deactivated_at=deactivated_at)


def _users_from_organizations(organizations):
    users = []
    seen = set()
    for organization in organizations:
        for user_id in (organization.created_by, organization.managed_by):
            if user_id is None or user_id in seen:
                continue
            seen.add(user_id)
            users.append(_user(user_id))
    return users


def _membership(user_id, organization_id, auth_state="member", membership_state="active"):
    return SimpleNamespace(
        id=uuid4(),
        user_id=user_id,
        organization_id=organization_id,
        membership_state=membership_state,
        organization_auth_state=auth_state,
    )


def _member_response(
    organization_id,
    user_id=None,
    membership_state="active",
    organization_auth_state="member",
):
    now = datetime(2026, 6, 27, 1, 2, 3, tzinfo=timezone.utc)
    return OrganizationMemberResponse(
        id=uuid4(),
        organization_id=organization_id,
        user_id=user_id or uuid4(),
        user_email="member@example.com",
        user_name="Member",
        membership_state=membership_state,
        organization_auth_state=organization_auth_state,
        invited_by=uuid4(),
        invited_at=now,
        accepted_at=now if membership_state == "active" else None,
        removed_at=None,
        created_at=now,
        updated_at=now,
    )


def _member_list_item_response(
    organization_id,
    user_id=None,
    membership_state="active",
    organization_auth_state="member",
):
    member = _member_response(
        organization_id=organization_id,
        user_id=user_id,
        membership_state=membership_state,
        organization_auth_state=organization_auth_state,
    )
    return OrganizationMemberListItemResponse(
        **member.model_dump(),
        current_month_usage=MemberCurrentMonthUsage(
            total_cost=3.5,
            workflow_execution_cost=2.0,
            agent_builder_cost=1.5,
        ),
    )


def _assert_patch_scope_filters(test_case, query, organization_id):
    test_case.assertEqual(len(query.filter_expressions), 2)
    organization_filter, org_active_filter = query.filter_expressions

    test_case.assertEqual(str(organization_filter.left), "organization.id")
    test_case.assertIs(organization_filter.operator, eq)
    test_case.assertEqual(organization_filter.right.value, organization_id)

    test_case.assertEqual(str(org_active_filter.left), "organization.is_active")
    test_case.assertIs(org_active_filter.operator, is_)
    test_case.assertEqual(str(org_active_filter.right), "true")


def _assert_active_membership_scope_filters(test_case, query, organization_id, user_id):
    # 문서 기준 모델에 맞춰 organization_memberships row를 조회한다.
    # active 여부는 non-active row가 legacy owner/manager fallback으로 우회되지 않도록
    # 서비스 로직에서 membership_state를 직접 판정한다.
    test_case.assertEqual(len(query.join_values), 0)
    user_filter, membership_org_filter = query.filter_expressions

    test_case.assertEqual(str(user_filter.left), "organization_memberships.user_id")
    test_case.assertIs(user_filter.operator, eq)
    test_case.assertEqual(user_filter.right.value, user_id)

    test_case.assertEqual(
        str(membership_org_filter.left),
        "organization_memberships.organization_id",
    )
    test_case.assertIs(membership_org_filter.operator, eq)
    test_case.assertEqual(membership_org_filter.right.value, organization_id)


def _error(code, message, details=None):
    return {
        "error": {
            "code": code,
            "message": message,
            "request_id": "98d6d88b-8d7a-46fd-8d12-f2024d2fac4b",
            "details": details or {},
        }
    }


if __name__ == "__main__":
    unittest.main()
