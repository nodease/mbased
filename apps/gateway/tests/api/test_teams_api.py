import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError
from sqlalchemy.sql.operators import eq, is_

from apps.gateway.main import app
from apps.shared.db.models.audit_log import AuditLog
from apps.shared.db.models.organization import Organization
from apps.shared.db.models.organization_membership import (
    ORGANIZATION_AUTH_MEMBER,
    ORGANIZATION_MEMBERSHIP_ACTIVE,
    OrganizationMembership,
)
from apps.shared.db.models.team import Team, TeamMembership
from apps.shared.db.models.user import User
from apps.shared.db.session import get_db


class TestTeamsApi(unittest.TestCase):
    def setUp(self):
        self._permission_denied_audit_patch = patch(
            "apps.gateway.services.team_service.record_permission_denied"
        )
        self._permission_denied_audit_patch.start()

    def tearDown(self):
        self._permission_denied_audit_patch.stop()
        app.dependency_overrides = {}

    def test_list_teams_returns_manager_visible_fields_and_records_query_contract(self):
        user_id = uuid4()
        organization_id = uuid4()
        created_at = datetime(2026, 6, 27, 1, 2, 3, tzinfo=timezone.utc)
        updated_at = datetime(2026, 6, 27, 4, 5, 6, tzinfo=timezone.utc)
        deactivated_at = datetime(2026, 6, 28, 1, 2, 3, tzinfo=timezone.utc)
        active_team_id = uuid4()
        inactive_team_id = uuid4()
        manager_id = uuid4()

        active_team = _team(
            id=active_team_id,
            organization_id=organization_id,
            name="Alpha",
            description="Core builders",
            options={"template": "builder"},
            flags=3,
            created_by=user_id,
            managed_by=manager_id,
            is_active=True,
            is_auto_add=True,
            created_at=created_at,
            updated_at=updated_at,
        )
        inactive_team = _team(
            id=inactive_team_id,
            organization_id=organization_id,
            name="Legacy",
            description=None,
            options={"template": "viewer"},
            flags=0,
            created_by=user_id,
            managed_by=None,
            is_active=False,
            is_auto_add=False,
            created_at=created_at,
            updated_at=updated_at,
            deactivated_at=deactivated_at,
        )
        session = _Session(
            organization=_organization(
                id=organization_id,
                name="Acme",
                created_by=user_id,
            ),
            teams=[active_team, inactive_team],
        )

        response = self._get_teams(
            session=session,
            user_id=user_id,
            organization_id=organization_id,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            [
                {
                    "id": str(active_team_id),
                    "organization_id": str(organization_id),
                    "name": "Alpha",
                    "description": "Core builders",
                    "options": {"template": "builder"},
                    "flags": 3,
                    "created_by": str(user_id),
                    "managed_by": str(manager_id),
                    "is_active": True,
                    "is_auto_add": True,
                    "created_at": "2026-06-27T01:02:03Z",
                    "updated_at": "2026-06-27T04:05:06Z",
                    "deactivated_at": None,
                },
                {
                    "id": str(inactive_team_id),
                    "organization_id": str(organization_id),
                    "name": "Legacy",
                    "description": None,
                    "options": {"template": "viewer"},
                    "flags": 0,
                    "created_by": str(user_id),
                    "managed_by": None,
                    "is_active": False,
                    "is_auto_add": False,
                    "created_at": "2026-06-27T01:02:03Z",
                    "updated_at": "2026-06-27T04:05:06Z",
                    "deactivated_at": "2026-06-28T01:02:03Z",
                },
            ],
        )
        self.assertNotIn(TeamMembership, session.query_calls)
        self.assertEqual(session.team_query.limit_value, 10)
        self.assertEqual(
            [str(value) for value in session.team_query.order_by_values],
            ["teams.name ASC", "teams.id ASC"],
        )

    def test_list_teams_applies_limit_query_parameter(self):
        user_id = uuid4()
        organization_id = uuid4()
        session = _Session(
            organization=_organization(
                id=organization_id,
                name="Acme",
                created_by=user_id,
            ),
            teams=[
                _team(id=uuid4(), organization_id=organization_id, name="A"),
                _team(id=uuid4(), organization_id=organization_id, name="B"),
            ],
        )

        response = self._get_teams(
            session=session,
            user_id=user_id,
            organization_id=organization_id,
            query="?limit=1",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()), 1)
        self.assertEqual(session.team_query.limit_value, 1)

    def test_list_teams_allows_managed_by_without_team_membership(self):
        user_id = uuid4()
        organization_id = uuid4()
        session = _Session(
            organization=_organization(
                id=organization_id,
                name="Acme",
                managed_by=user_id,
            ),
            teams=[],
        )

        response = self._get_teams(
            session=session,
            user_id=user_id,
            organization_id=organization_id,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), [])
        self.assertNotIn(TeamMembership, session.query_calls)

    def test_list_teams_requires_organization_header(self):
        user_id = uuid4()
        session = _Session()

        response = self._get_teams(
            session=session,
            user_id=user_id,
            organization_id=None,
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.json(),
            _error("organization.required", "X-Organization-Id header is required."),
        )
        self.assertNotIn(Organization, session.query_calls)

    def test_list_teams_rejects_invalid_organization_header(self):
        user_id = uuid4()
        session = _Session()

        response = self._get_teams(
            session=session,
            user_id=user_id,
            raw_organization_id="not-a-uuid",
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
        self.assertNotIn(Organization, session.query_calls)

    def test_list_teams_rejects_invalid_limit(self):
        user_id = uuid4()
        organization_id = uuid4()
        session = _Session()

        response = self._get_teams(
            session=session,
            user_id=user_id,
            organization_id=organization_id,
            query="?limit=0",
        )

        self.assertEqual(response.status_code, 422)
        self.assertEqual(
            response.json(),
            _error(
                "validation.failed",
                "limit must be between 1 and 100.",
                {"field": "limit"},
            ),
        )
        self.assertNotIn(Organization, session.query_calls)

    def test_list_teams_hides_missing_or_inactive_organization(self):
        user_id = uuid4()
        organization_id = uuid4()
        session = _Session(organization=None)

        response = self._get_teams(
            session=session,
            user_id=user_id,
            organization_id=organization_id,
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(
            response.json(),
            _error("resource.not_found", "Organization not found."),
        )

    def test_list_teams_hides_organization_outside_user_scope(self):
        user_id = uuid4()
        organization_id = uuid4()
        session = _Session(
            organization=_organization(id=organization_id, name="Acme"),
            membership=None,
        )

        response = self._get_teams(
            session=session,
            user_id=user_id,
            organization_id=organization_id,
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(
            response.json(),
            _error("resource.not_found", "Organization not found."),
        )
        self.assertIn(OrganizationMembership, session.query_calls)

    def test_list_teams_rejects_member_without_manager_permission(self):
        user_id = uuid4()
        organization_id = uuid4()
        session = _Session(
            organization=_organization(id=organization_id, name="Acme"),
            membership=SimpleNamespace(id=uuid4()),
        )

        response = self._get_teams(
            session=session,
            user_id=user_id,
            organization_id=organization_id,
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(
            response.json(),
            _error(
                "permission.denied",
                "Organization manager permission is required.",
            ),
        )

    def test_list_teams_returns_auth_envelope_for_unauthenticated_request(self):
        session = _Session()
        app.dependency_overrides[get_db] = lambda: session

        with patch(
            "apps.gateway.api.v1.endpoints.team.AuthService.get_user_from_token",
            side_effect=HTTPException(status_code=401, detail="로그인이 필요합니다"),
        ):
            response = TestClient(app).get(
                "/api/v1/teams",
                headers={"X-Request-ID": "98d6d88b-8d7a-46fd-8d12-f2024d2fac4b"},
            )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(
            response.json(),
            _error("auth.required", "로그인이 필요합니다"),
        )
        self.assertNotIn(Organization, session.query_calls)

    def test_list_team_members_returns_members_for_manager(self):
        user_id = uuid4()
        organization_id = uuid4()
        team_id = uuid4()
        member_user_id = uuid4()
        membership_id = uuid4()
        assigned_at = datetime(2026, 6, 29, 2, 11, 34, tzinfo=timezone.utc)
        member = _user(
            id=member_user_id,
            email="member@example.com",
            name="Member One",
        )
        membership = _membership(
            id=membership_id,
            organization_id=organization_id,
            team_id=team_id,
            user_id=member_user_id,
            assigned_by=user_id,
            assigned_at=assigned_at,
            user=member,
        )
        session = _Session(
            organization=_organization(
                id=organization_id,
                name="Acme",
                created_by=user_id,
            ),
            team=_team(id=team_id, organization_id=organization_id, name="Alpha"),
            memberships=[membership],
        )

        response = self._get_team_members(
            session=session,
            user_id=user_id,
            organization_id=organization_id,
            team_id=team_id,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            [
                {
                    "id": str(membership_id),
                    "user_id": str(member_user_id),
                    "email": "member@example.com",
                    "name": "Member One",
                    "assigned_at": "2026-06-29T02:11:34Z",
                }
            ],
        )
        self.assertIn(Team, session.query_calls)
        self.assertIn(TeamMembership, session.query_calls)
        self.assertEqual(
            [str(value) for value in session.membership_query.order_by_values],
            ["users.name ASC", "users.email ASC", "team_memberships.id ASC"],
        )

    def test_create_team_creates_team_for_active_organization(self):
        user_id = uuid4()
        organization_id = uuid4()
        session = _Session(
            organization=_organization(
                id=organization_id,
                name="Acme",
                created_by=user_id,
            ),
            teams=[],
        )

        response = self._post_team(
            session=session,
            user_id=user_id,
            organization_id=organization_id,
            payload={
                "name": " Builders ",
                "description": "Core team",
                "is_auto_add": True,
            },
        )

        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertEqual(body["organization_id"], str(organization_id))
        self.assertEqual(body["name"], "Builders")
        self.assertEqual(body["description"], "Core team")
        self.assertTrue(body["is_auto_add"])
        self.assertEqual(session.added[0].organization_id, organization_id)
        self.assertEqual(session.added[0].created_by, user_id)
        self.assertTrue(session.committed)
        self.assertNotIn(TeamMembership, session.query_calls)

    def test_create_team_returns_conflict_for_duplicate_name(self):
        user_id = uuid4()
        organization_id = uuid4()
        session = _Session(
            organization=_organization(
                id=organization_id,
                name="Acme",
                created_by=user_id,
            ),
            teams=[
                _team(
                    id=uuid4(),
                    organization_id=organization_id,
                    name="Builders",
                )
            ],
        )

        response = self._post_team(
            session=session,
            user_id=user_id,
            organization_id=organization_id,
            payload={"name": "Builders"},
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(
            response.json(),
            _error("resource.conflict", "Team name already exists"),
        )
        self.assertFalse(session.committed)

    def test_create_team_returns_auth_before_body_validation(self):
        session = _Session()
        app.dependency_overrides[get_db] = lambda: session

        with patch(
            "apps.gateway.api.v1.endpoints.team.AuthService.get_user_from_token",
            side_effect=HTTPException(status_code=401, detail="로그인이 필요합니다"),
        ):
            response = TestClient(app).post(
                "/api/v1/teams",
                headers={
                    "X-Organization-Id": str(uuid4()),
                    "X-Request-ID": "98d6d88b-8d7a-46fd-8d12-f2024d2fac4b",
                },
                json={},
            )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(
            response.json(),
            _error("auth.required", "로그인이 필요합니다"),
        )
        self.assertNotIn(Organization, session.query_calls)

    def test_create_team_rejects_member_without_manager_permission(self):
        user_id = uuid4()
        organization_id = uuid4()
        session = _Session(
            organization=_organization(id=organization_id, name="Acme"),
            membership=SimpleNamespace(id=uuid4()),
        )

        response = self._post_team(
            session=session,
            user_id=user_id,
            organization_id=organization_id,
            payload={"name": "Builders"},
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(
            response.json(),
            _error(
                "permission.denied",
                "Organization manager permission is required.",
            ),
        )
        self.assertIn(OrganizationMembership, session.query_calls)

    def test_create_team_hides_organization_outside_user_scope(self):
        user_id = uuid4()
        organization_id = uuid4()
        session = _Session(
            organization=_organization(id=organization_id, name="Acme"),
            membership=None,
        )

        response = self._post_team(
            session=session,
            user_id=user_id,
            organization_id=organization_id,
            payload={"name": "Builders"},
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(
            response.json(),
            _error("resource.not_found", "Organization not found."),
        )
        self.assertIn(OrganizationMembership, session.query_calls)

    def test_update_team_updates_team_in_active_organization(self):
        user_id = uuid4()
        organization_id = uuid4()
        team_id = uuid4()
        team = _team(
            id=team_id,
            organization_id=organization_id,
            name="Builders",
            description="Before",
            created_by=user_id,
        )
        session = _Session(
            organization=_organization(
                id=organization_id,
                name="Acme",
                created_by=user_id,
            ),
            teams=[team],
        )

        response = self._patch_team(
            session=session,
            user_id=user_id,
            organization_id=organization_id,
            team_id=team_id,
            payload={
                "name": " Operators ",
                "description": None,
                "is_auto_add": True,
            },
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["id"], str(team_id))
        self.assertEqual(body["name"], "Operators")
        self.assertIsNone(body["description"])
        self.assertTrue(body["is_auto_add"])
        self.assertEqual(team.name, "Operators")
        self.assertIsNone(team.description)
        self.assertTrue(session.committed)
        self.assertNotIn(TeamMembership, session.query_calls)

    def test_update_team_returns_conflict_for_duplicate_name(self):
        user_id = uuid4()
        organization_id = uuid4()
        team_id = uuid4()
        existing_team_id = uuid4()
        team = _team(
            id=team_id,
            organization_id=organization_id,
            name="Builders",
            created_by=user_id,
        )
        existing_team = _team(
            id=existing_team_id,
            organization_id=organization_id,
            name="Operators",
            created_by=user_id,
        )
        session = _Session(
            organization=_organization(
                id=organization_id,
                name="Acme",
                created_by=user_id,
            ),
            teams=[team, existing_team],
        )

        response = self._patch_team(
            session=session,
            user_id=user_id,
            organization_id=organization_id,
            team_id=team_id,
            payload={"name": "Operators"},
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(
            response.json(),
            _error("resource.conflict", "Team name already exists"),
        )
        self.assertEqual(team.name, "Builders")
        self.assertFalse(session.committed)

    def test_update_team_hides_team_outside_active_organization(self):
        user_id = uuid4()
        organization_id = uuid4()
        other_organization_id = uuid4()
        team_id = uuid4()
        session = _Session(
            organization=_organization(
                id=organization_id,
                name="Acme",
                created_by=user_id,
            ),
            teams=[
                _team(
                    id=team_id,
                    organization_id=other_organization_id,
                    name="Builders",
                )
            ],
        )

        response = self._patch_team(
            session=session,
            user_id=user_id,
            organization_id=organization_id,
            team_id=team_id,
            payload={"name": "Operators"},
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json(), _error("resource.not_found", "Team not found"))
        self.assertFalse(session.committed)

    def test_update_team_rejects_missing_managed_by_user(self):
        user_id = uuid4()
        organization_id = uuid4()
        team_id = uuid4()
        manager_id = uuid4()
        session = _Session(
            organization=_organization(
                id=organization_id,
                name="Acme",
                created_by=user_id,
            ),
            teams=[
                _team(
                    id=team_id,
                    organization_id=organization_id,
                    name="Builders",
                    created_by=user_id,
                )
            ],
            user=None,
        )

        response = self._patch_team(
            session=session,
            user_id=user_id,
            organization_id=organization_id,
            team_id=team_id,
            payload={"managed_by": str(manager_id)},
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json(), _error("resource.not_found", "User not found"))
        self.assertFalse(session.committed)

    def test_update_team_rejects_managed_by_outside_organization(self):
        user_id = uuid4()
        organization_id = uuid4()
        team_id = uuid4()
        manager_id = uuid4()
        team = _team(
            id=team_id,
            organization_id=organization_id,
            name="Builders",
            created_by=user_id,
        )
        session = _Session(
            organization=_organization(
                id=organization_id,
                name="Acme",
                created_by=user_id,
            ),
            teams=[team],
            user=_user(id=manager_id),
            membership=None,
        )

        response = self._patch_team(
            session=session,
            user_id=user_id,
            organization_id=organization_id,
            team_id=team_id,
            payload={"managed_by": str(manager_id)},
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.json(),
            _error(
                "validation.failed",
                "Managed user is not in the organization",
            ),
        )
        self.assertIsNone(team.managed_by)
        self.assertFalse(session.committed)

    def test_update_team_allows_managed_by_organization_member(self):
        user_id = uuid4()
        organization_id = uuid4()
        team_id = uuid4()
        manager_id = uuid4()
        membership = _membership(
            id=uuid4(),
            organization_id=organization_id,
            team_id=team_id,
            user_id=manager_id,
            assigned_by=user_id,
        )
        team = _team(
            id=team_id,
            organization_id=organization_id,
            name="Builders",
            created_by=user_id,
        )
        session = _Session(
            organization=_organization(
                id=organization_id,
                name="Acme",
                created_by=user_id,
            ),
            teams=[team],
            user=_user(id=manager_id),
            membership=membership,
        )

        response = self._patch_team(
            session=session,
            user_id=user_id,
            organization_id=organization_id,
            team_id=team_id,
            payload={"managed_by": str(manager_id)},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["managed_by"], str(manager_id))
        self.assertEqual(team.managed_by, manager_id)
        self.assertTrue(session.committed)

    def test_update_team_requires_organization_header(self):
        # PATCH 라우트도 조직 헤더가 없으면 권한/DB 조회 전에 거부해야 한다.
        user_id = uuid4()
        session = _Session()

        response = self._patch_team(
            session=session,
            user_id=user_id,
            organization_id=None,
            team_id=uuid4(),
            payload={"name": "Builders"},
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.json(),
            _error("organization.required", "X-Organization-Id header is required."),
        )
        self.assertNotIn(Organization, session.query_calls)

    def test_update_team_rejects_invalid_organization_header(self):
        # PATCH 라우트도 조직 헤더 UUID 형식을 먼저 검증해야 한다.
        user_id = uuid4()
        session = _Session()

        response = self._patch_team(
            session=session,
            user_id=user_id,
            raw_organization_id="not-a-uuid",
            team_id=uuid4(),
            payload={"name": "Builders"},
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
        self.assertNotIn(Organization, session.query_calls)

    def test_update_team_returns_auth_envelope_for_unauthenticated_request(self):
        # PATCH 라우트가 미인증 요청을 공통 auth envelope로 반환하는지 확인한다.
        session = _Session()
        app.dependency_overrides[get_db] = lambda: session

        with patch(
            "apps.gateway.api.v1.endpoints.team.AuthService.get_user_from_token",
            side_effect=HTTPException(status_code=401, detail="로그인이 필요합니다"),
        ):
            response = TestClient(app).patch(
                f"/api/v1/teams/{uuid4()}",
                headers={
                    "X-Organization-Id": str(uuid4()),
                    "X-Request-ID": "98d6d88b-8d7a-46fd-8d12-f2024d2fac4b",
                },
                json={"name": "Builders"},
            )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(
            response.json(),
            _error("auth.required", "로그인이 필요합니다"),
        )
        self.assertNotIn(Organization, session.query_calls)

    def test_update_team_rejects_invalid_team_id_route_parameter(self):
        # team_id 경로 파라미터가 UUID가 아니면 FastAPI route validation에서 막혀야 한다.
        user_id = uuid4()
        organization_id = uuid4()
        session = _Session()

        response = self._patch_team(
            session=session,
            user_id=user_id,
            organization_id=organization_id,
            team_id="not-a-uuid",
            payload={"name": "Builders"},
        )

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["error"]["code"], "validation.failed")
        self.assertEqual(response.json()["error"]["request_id"], "98d6d88b-8d7a-46fd-8d12-f2024d2fac4b")
        self.assertEqual(
            response.json()["error"]["message"],
            "Request validation failed.",
        )
        self.assertEqual(
            response.json()["error"]["details"]["errors"][0]["loc"],
            ["path", "team_id"],
        )
        self.assertNotIn(Organization, session.query_calls)

    def test_update_team_rejects_member_without_manager_permission(self):
        user_id = uuid4()
        organization_id = uuid4()
        session = _Session(
            organization=_organization(id=organization_id, name="Acme"),
            membership=SimpleNamespace(id=uuid4()),
        )

        response = self._patch_team(
            session=session,
            user_id=user_id,
            organization_id=organization_id,
            team_id=uuid4(),
            payload={"name": "Builders"},
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(
            response.json(),
            _error(
                "permission.denied",
                "Organization manager permission is required.",
            ),
        )
        self.assertIn(OrganizationMembership, session.query_calls)

    def test_update_team_hides_organization_outside_user_scope(self):
        user_id = uuid4()
        organization_id = uuid4()
        session = _Session(
            organization=_organization(id=organization_id, name="Acme"),
            membership=None,
        )

        response = self._patch_team(
            session=session,
            user_id=user_id,
            organization_id=organization_id,
            team_id=uuid4(),
            payload={"name": "Builders"},
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(
            response.json(),
            _error("resource.not_found", "Organization not found."),
        )
        self.assertIn(OrganizationMembership, session.query_calls)

    def test_add_team_member_adds_membership_in_active_organization(self):
        user_id = uuid4()
        organization_id = uuid4()
        team_id = uuid4()
        member_user_id = uuid4()
        session = _Session(
            organization=_organization(
                id=organization_id,
                name="Acme",
                created_by=user_id,
            ),
            teams=[
                _team(
                    id=team_id,
                    organization_id=organization_id,
                    name="Builders",
                    created_by=user_id,
                )
            ],
            user=_user(id=member_user_id),
            organization_memberships=[
                _organization_membership(member_user_id, organization_id)
            ],
        )

        response = self._post_team_member(
            session=session,
            user_id=user_id,
            organization_id=organization_id,
            team_id=team_id,
            payload={"user_id": str(member_user_id)},
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["status"], "added")
        self.assertEqual(session.added[0].team_id, team_id)
        self.assertEqual(session.added[0].user_id, member_user_id)
        self.assertEqual(session.added[0].assigned_by, user_id)
        audit = next(row for row in session.added if isinstance(row, AuditLog))
        self.assertEqual(audit.action, "team_membership.created")
        self.assertEqual(audit.target_id, str(session.added[0].id))
        self.assertEqual(audit.after["team_id"], str(team_id))
        self.assertEqual(audit.after["user_id"], str(member_user_id))
        self.assertTrue(session.committed)

    def test_add_team_member_rolls_back_when_audit_add_fails(self):
        user_id = uuid4()
        organization_id = uuid4()
        team_id = uuid4()
        member_user_id = uuid4()
        session = _Session(
            organization=_organization(
                id=organization_id,
                name="Acme",
                created_by=user_id,
            ),
            teams=[
                _team(
                    id=team_id,
                    organization_id=organization_id,
                    name="Builders",
                    created_by=user_id,
                )
            ],
            user=_user(id=member_user_id),
            organization_memberships=[
                _organization_membership(member_user_id, organization_id)
            ],
        )
        session.audit_add_error = RuntimeError("audit unavailable")

        with self.assertRaisesRegex(RuntimeError, "audit unavailable"):
            self._post_team_member(
                session=session,
                user_id=user_id,
                organization_id=organization_id,
                team_id=team_id,
                payload={"user_id": str(member_user_id)},
            )

        self.assertFalse(session.committed)
        self.assertTrue(session.rolled_back)

    def test_add_team_member_rejects_missing_user(self):
        user_id = uuid4()
        organization_id = uuid4()
        team_id = uuid4()
        member_user_id = uuid4()
        session = _Session(
            organization=_organization(
                id=organization_id,
                name="Acme",
                created_by=user_id,
            ),
            teams=[
                _team(
                    id=team_id,
                    organization_id=organization_id,
                    name="Builders",
                    created_by=user_id,
                )
            ],
            user=None,
        )

        response = self._post_team_member(
            session=session,
            user_id=user_id,
            organization_id=organization_id,
            team_id=team_id,
            payload={"user_id": str(member_user_id)},
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json(), _error("resource.not_found", "User not found"))
        self.assertFalse(session.committed)

    def test_add_team_member_rejects_inactive_team(self):
        user_id = uuid4()
        organization_id = uuid4()
        team_id = uuid4()
        member_user_id = uuid4()
        session = _Session(
            organization=_organization(
                id=organization_id,
                name="Acme",
                created_by=user_id,
            ),
            teams=[
                _team(
                    id=team_id,
                    organization_id=organization_id,
                    name="Builders",
                    is_active=False,
                )
            ],
            user=_user(id=member_user_id),
        )

        response = self._post_team_member(
            session=session,
            user_id=user_id,
            organization_id=organization_id,
            team_id=team_id,
            payload={"user_id": str(member_user_id)},
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json(), _error("resource.not_found", "Team not found"))
        self.assertFalse(session.committed)

    def test_add_team_member_returns_existing_membership_after_unique_race(self):
        user_id = uuid4()
        organization_id = uuid4()
        team_id = uuid4()
        member_user_id = uuid4()
        membership = _membership(
            id=uuid4(),
            organization_id=organization_id,
            team_id=team_id,
            user_id=member_user_id,
            assigned_by=user_id,
        )
        session = _Session(
            organization=_organization(
                id=organization_id,
                name="Acme",
                created_by=user_id,
            ),
            teams=[
                _team(
                    id=team_id,
                    organization_id=organization_id,
                    name="Builders",
                    created_by=user_id,
                )
            ],
            user=_user(id=member_user_id),
            organization_memberships=[
                _organization_membership(member_user_id, organization_id)
            ],
            membership_after_rollback=membership,
            commit_error=IntegrityError("insert", {}, Exception("duplicate")),
        )

        response = self._post_team_member(
            session=session,
            user_id=user_id,
            organization_id=organization_id,
            team_id=team_id,
            payload={"user_id": str(member_user_id)},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {"id": str(membership.id), "status": "added"},
        )
        self.assertTrue(session.rolled_back)

    def test_add_team_member_returns_auth_before_body_validation(self):
        session = _Session()
        app.dependency_overrides[get_db] = lambda: session

        with patch(
            "apps.gateway.api.v1.endpoints.team.AuthService.get_user_from_token",
            side_effect=HTTPException(status_code=401, detail="로그인이 필요합니다"),
        ):
            response = TestClient(app).post(
                f"/api/v1/teams/{uuid4()}/members",
                headers={
                    "X-Organization-Id": str(uuid4()),
                    "X-Request-ID": "98d6d88b-8d7a-46fd-8d12-f2024d2fac4b",
                },
                json={},
            )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(
            response.json(),
            _error("auth.required", "로그인이 필요합니다"),
        )
        self.assertNotIn(Organization, session.query_calls)

    def test_add_team_member_requires_organization_header(self):
        user_id = uuid4()
        session = _Session()

        response = self._post_team_member(
            session=session,
            user_id=user_id,
            organization_id=None,
            team_id=uuid4(),
            payload={"user_id": str(uuid4())},
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.json(),
            _error("organization.required", "X-Organization-Id header is required."),
        )
        self.assertNotIn(Organization, session.query_calls)

    def test_add_team_member_rejects_invalid_organization_header(self):
        user_id = uuid4()
        session = _Session()

        response = self._post_team_member(
            session=session,
            user_id=user_id,
            raw_organization_id="not-a-uuid",
            team_id=uuid4(),
            payload={"user_id": str(uuid4())},
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
        self.assertNotIn(Organization, session.query_calls)

    def test_add_team_member_returns_auth_envelope_for_unauthenticated_request(self):
        session = _Session()
        app.dependency_overrides[get_db] = lambda: session

        with patch(
            "apps.gateway.api.v1.endpoints.team.AuthService.get_user_from_token",
            side_effect=HTTPException(status_code=401, detail="로그인이 필요합니다"),
        ):
            response = TestClient(app).post(
                f"/api/v1/teams/{uuid4()}/members",
                headers={
                    "X-Organization-Id": str(uuid4()),
                    "X-Request-ID": "98d6d88b-8d7a-46fd-8d12-f2024d2fac4b",
                },
                json={"user_id": str(uuid4())},
            )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(
            response.json(),
            _error("auth.required", "로그인이 필요합니다"),
        )
        self.assertNotIn(Organization, session.query_calls)

    def test_add_team_member_rejects_invalid_team_id_route_parameter(self):
        user_id = uuid4()
        organization_id = uuid4()
        session = _Session()

        response = self._post_team_member(
            session=session,
            user_id=user_id,
            organization_id=organization_id,
            team_id="not-a-uuid",
            payload={"user_id": str(uuid4())},
        )

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["error"]["code"], "validation.failed")
        self.assertEqual(response.json()["error"]["request_id"], "98d6d88b-8d7a-46fd-8d12-f2024d2fac4b")
        self.assertEqual(
            response.json()["error"]["message"],
            "Request validation failed.",
        )
        self.assertEqual(
            response.json()["error"]["details"]["errors"][0]["loc"],
            ["path", "team_id"],
        )
        self.assertNotIn(Organization, session.query_calls)

    def test_add_team_member_rejects_member_without_manager_permission(self):
        user_id = uuid4()
        organization_id = uuid4()
        session = _Session(
            organization=_organization(id=organization_id, name="Acme"),
            membership=SimpleNamespace(id=uuid4()),
        )

        response = self._post_team_member(
            session=session,
            user_id=user_id,
            organization_id=organization_id,
            team_id=uuid4(),
            payload={"user_id": str(uuid4())},
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(
            response.json(),
            _error(
                "permission.denied",
                "Organization manager permission is required.",
            ),
        )
        self.assertIn(OrganizationMembership, session.query_calls)

    def test_add_team_member_hides_organization_outside_user_scope(self):
        user_id = uuid4()
        organization_id = uuid4()
        session = _Session(
            organization=_organization(id=organization_id, name="Acme"),
            membership=None,
        )

        response = self._post_team_member(
            session=session,
            user_id=user_id,
            organization_id=organization_id,
            team_id=uuid4(),
            payload={"user_id": str(uuid4())},
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(
            response.json(),
            _error("resource.not_found", "Organization not found."),
        )
        self.assertIn(OrganizationMembership, session.query_calls)

    def test_remove_team_member_removes_membership_in_active_organization(self):
        user_id = uuid4()
        organization_id = uuid4()
        team_id = uuid4()
        member_user_id = uuid4()
        membership = _membership(
            id=uuid4(),
            organization_id=organization_id,
            team_id=team_id,
            user_id=member_user_id,
            assigned_by=user_id,
        )
        session = _Session(
            organization=_organization(
                id=organization_id,
                name="Acme",
                created_by=user_id,
            ),
            membership=membership,
            teams=[
                _team(
                    id=team_id,
                    organization_id=organization_id,
                    name="Builders",
                    created_by=user_id,
                )
            ],
        )

        response = self._delete_team_member(
            session=session,
            user_id=user_id,
            organization_id=organization_id,
            team_id=team_id,
            member_user_id=member_user_id,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "removed"})
        self.assertEqual(session.deleted, [membership])
        audit = next(row for row in session.added if isinstance(row, AuditLog))
        self.assertEqual(audit.action, "team_membership.deleted")
        self.assertEqual(audit.target_id, str(membership.id))
        self.assertEqual(audit.before["team_id"], str(team_id))
        self.assertEqual(audit.before["user_id"], str(member_user_id))
        self.assertTrue(session.committed)

    def test_remove_team_member_allows_managed_by(self):
        # organization.managed_by도 manager 권한으로 인정되어야 하므로,
        # created_by가 아닌 관리자가 DELETE mutation을 수행할 수 있는지 검증한다.
        user_id = uuid4()
        organization_id = uuid4()
        team_id = uuid4()
        member_user_id = uuid4()
        membership = _membership(
            id=uuid4(),
            organization_id=organization_id,
            team_id=team_id,
            user_id=member_user_id,
            assigned_by=user_id,
        )
        session = _Session(
            organization=_organization(
                id=organization_id,
                name="Acme",
                created_by=uuid4(),
                managed_by=user_id,
            ),
            membership=membership,
            teams=[
                _team(
                    id=team_id,
                    organization_id=organization_id,
                    name="Builders",
                )
            ],
        )

        response = self._delete_team_member(
            session=session,
            user_id=user_id,
            organization_id=organization_id,
            team_id=team_id,
            member_user_id=member_user_id,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "removed"})
        self.assertEqual(session.deleted, [membership])

    def test_remove_team_member_requires_organization_header(self):
        user_id = uuid4()
        session = _Session()

        response = self._delete_team_member(
            session=session,
            user_id=user_id,
            organization_id=None,
            team_id=uuid4(),
            member_user_id=uuid4(),
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.json(),
            _error("organization.required", "X-Organization-Id header is required."),
        )
        self.assertNotIn(Organization, session.query_calls)

    def test_remove_team_member_rejects_invalid_organization_header(self):
        user_id = uuid4()
        session = _Session()

        response = self._delete_team_member(
            session=session,
            user_id=user_id,
            raw_organization_id="not-a-uuid",
            team_id=uuid4(),
            member_user_id=uuid4(),
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
        self.assertNotIn(Organization, session.query_calls)

    def test_remove_team_member_returns_auth_envelope_for_unauthenticated_request(self):
        session = _Session()
        app.dependency_overrides[get_db] = lambda: session

        with patch(
            "apps.gateway.api.v1.endpoints.team.AuthService.get_user_from_token",
            side_effect=HTTPException(status_code=401, detail="로그인이 필요합니다"),
        ):
            response = TestClient(app).delete(
                f"/api/v1/teams/{uuid4()}/members/{uuid4()}",
                headers={
                    "X-Organization-Id": str(uuid4()),
                    "X-Request-ID": "98d6d88b-8d7a-46fd-8d12-f2024d2fac4b",
                },
            )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(
            response.json(),
            _error("auth.required", "로그인이 필요합니다"),
        )
        self.assertNotIn(Organization, session.query_calls)

    def test_remove_team_member_rejects_invalid_team_id_route_parameter(self):
        user_id = uuid4()
        organization_id = uuid4()
        session = _Session()

        response = self._delete_team_member(
            session=session,
            user_id=user_id,
            organization_id=organization_id,
            team_id="not-a-uuid",
            member_user_id=uuid4(),
        )

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["error"]["code"], "validation.failed")
        self.assertEqual(response.json()["error"]["request_id"], "98d6d88b-8d7a-46fd-8d12-f2024d2fac4b")
        self.assertEqual(
            response.json()["error"]["message"],
            "Request validation failed.",
        )
        self.assertEqual(
            response.json()["error"]["details"]["errors"][0]["loc"],
            ["path", "team_id"],
        )
        self.assertNotIn(Organization, session.query_calls)

    def test_remove_team_member_rejects_invalid_user_id_route_parameter(self):
        user_id = uuid4()
        organization_id = uuid4()
        session = _Session()

        response = self._delete_team_member(
            session=session,
            user_id=user_id,
            organization_id=organization_id,
            team_id=uuid4(),
            member_user_id="not-a-uuid",
        )

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["error"]["code"], "validation.failed")
        self.assertEqual(response.json()["error"]["request_id"], "98d6d88b-8d7a-46fd-8d12-f2024d2fac4b")
        self.assertEqual(
            response.json()["error"]["message"],
            "Request validation failed.",
        )
        self.assertEqual(
            response.json()["error"]["details"]["errors"][0]["loc"],
            ["path", "user_id"],
        )
        self.assertNotIn(Organization, session.query_calls)

    def test_remove_team_member_rejects_member_without_manager_permission(self):
        user_id = uuid4()
        organization_id = uuid4()
        session = _Session(
            organization=_organization(id=organization_id, name="Acme"),
            membership=SimpleNamespace(id=uuid4()),
        )

        response = self._delete_team_member(
            session=session,
            user_id=user_id,
            organization_id=organization_id,
            team_id=uuid4(),
            member_user_id=uuid4(),
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(
            response.json(),
            _error(
                "permission.denied",
                "Organization manager permission is required.",
            ),
        )
        self.assertIn(OrganizationMembership, session.query_calls)

    def test_remove_team_member_hides_organization_outside_user_scope(self):
        user_id = uuid4()
        organization_id = uuid4()
        session = _Session(
            organization=_organization(id=organization_id, name="Acme"),
            membership=None,
        )

        response = self._delete_team_member(
            session=session,
            user_id=user_id,
            organization_id=organization_id,
            team_id=uuid4(),
            member_user_id=uuid4(),
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(
            response.json(),
            _error("resource.not_found", "Organization not found."),
        )
        self.assertIn(OrganizationMembership, session.query_calls)

    def test_deactivate_team_deactivates_team_in_active_organization(self):
        user_id = uuid4()
        organization_id = uuid4()
        team_id = uuid4()
        team = _team(
            id=team_id,
            organization_id=organization_id,
            name="Builders",
            created_by=user_id,
        )
        session = _Session(
            organization=_organization(
                id=organization_id,
                name="Acme",
                created_by=user_id,
            ),
            teams=[team],
        )

        response = self._delete_team(
            session=session,
            user_id=user_id,
            organization_id=organization_id,
            team_id=team_id,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "deactivated"})
        self.assertFalse(team.is_active)
        self.assertIsNotNone(team.deactivated_at)
        self.assertTrue(session.committed)

    def test_deactivate_team_is_idempotent_for_inactive_team(self):
        user_id = uuid4()
        organization_id = uuid4()
        team_id = uuid4()
        deactivated_at = datetime(2026, 6, 28, 1, 2, 3, tzinfo=timezone.utc)
        team = _team(
            id=team_id,
            organization_id=organization_id,
            name="Legacy",
            created_by=user_id,
            is_active=False,
            deactivated_at=deactivated_at,
        )
        session = _Session(
            organization=_organization(
                id=organization_id,
                name="Acme",
                created_by=user_id,
            ),
            teams=[team],
        )

        response = self._delete_team(
            session=session,
            user_id=user_id,
            organization_id=organization_id,
            team_id=team_id,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "deactivated"})
        self.assertFalse(team.is_active)
        self.assertEqual(team.deactivated_at, deactivated_at)
        self.assertFalse(session.committed)

    def test_deactivate_team_hides_team_outside_active_organization(self):
        user_id = uuid4()
        organization_id = uuid4()
        other_organization_id = uuid4()
        team_id = uuid4()
        session = _Session(
            organization=_organization(
                id=organization_id,
                name="Acme",
                created_by=user_id,
            ),
            teams=[
                _team(
                    id=team_id,
                    organization_id=other_organization_id,
                    name="Builders",
                )
            ],
        )

        response = self._delete_team(
            session=session,
            user_id=user_id,
            organization_id=organization_id,
            team_id=team_id,
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json(), _error("resource.not_found", "Team not found"))
        self.assertFalse(session.committed)

    def test_deactivate_team_rejects_member_without_manager_permission(self):
        user_id = uuid4()
        organization_id = uuid4()
        session = _Session(
            organization=_organization(id=organization_id, name="Acme"),
            membership=SimpleNamespace(id=uuid4()),
        )

        response = self._delete_team(
            session=session,
            user_id=user_id,
            organization_id=organization_id,
            team_id=uuid4(),
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(
            response.json(),
            _error(
                "permission.denied",
                "Organization manager permission is required.",
            ),
        )
        self.assertIn(OrganizationMembership, session.query_calls)

    def _get_teams(
        self,
        session,
        user_id,
        organization_id=None,
        raw_organization_id=None,
        query="",
    ):
        session.authenticate(user_id)
        app.dependency_overrides[get_db] = lambda: session
        headers = {"X-Request-ID": "98d6d88b-8d7a-46fd-8d12-f2024d2fac4b"}
        if raw_organization_id is not None:
            headers["X-Organization-Id"] = raw_organization_id
        elif organization_id is not None:
            headers["X-Organization-Id"] = str(organization_id)
        headers["Cookie"] = "auth_token=token"

        with patch(
            "apps.gateway.api.v1.endpoints.team.AuthService.get_user_from_token",
            return_value=SimpleNamespace(id=user_id),
        ):
            return TestClient(app).get(
                f"/api/v1/teams{query}",
                headers=headers,
            )

    def _get_team_members(
        self,
        session,
        user_id,
        team_id,
        organization_id=None,
        raw_organization_id=None,
    ):
        session.authenticate(user_id)
        app.dependency_overrides[get_db] = lambda: session
        headers = {"X-Request-ID": "98d6d88b-8d7a-46fd-8d12-f2024d2fac4b"}
        if raw_organization_id is not None:
            headers["X-Organization-Id"] = raw_organization_id
        elif organization_id is not None:
            headers["X-Organization-Id"] = str(organization_id)
        headers["Cookie"] = "auth_token=token"

        with patch(
            "apps.gateway.api.v1.endpoints.team.AuthService.get_user_from_token",
            return_value=SimpleNamespace(id=user_id),
        ):
            return TestClient(app).get(
                f"/api/v1/teams/{team_id}/members",
                headers=headers,
            )

    def _post_team(
        self,
        session,
        user_id,
        organization_id=None,
        raw_organization_id=None,
        payload=None,
    ):
        session.authenticate(user_id)
        app.dependency_overrides[get_db] = lambda: session
        headers = {"X-Request-ID": "98d6d88b-8d7a-46fd-8d12-f2024d2fac4b"}
        if raw_organization_id is not None:
            headers["X-Organization-Id"] = raw_organization_id
        elif organization_id is not None:
            headers["X-Organization-Id"] = str(organization_id)
        headers["Cookie"] = "auth_token=token"

        with patch(
            "apps.gateway.api.v1.endpoints.team.AuthService.get_user_from_token",
            return_value=SimpleNamespace(id=user_id),
        ):
            return TestClient(app).post(
                "/api/v1/teams",
                headers=headers,
                json=payload,
            )

    def _patch_team(
        self,
        session,
        user_id,
        team_id,
        organization_id=None,
        raw_organization_id=None,
        payload=None,
    ):
        session.authenticate(user_id)
        app.dependency_overrides[get_db] = lambda: session
        headers = {"X-Request-ID": "98d6d88b-8d7a-46fd-8d12-f2024d2fac4b"}
        if raw_organization_id is not None:
            headers["X-Organization-Id"] = raw_organization_id
        elif organization_id is not None:
            headers["X-Organization-Id"] = str(organization_id)
        headers["Cookie"] = "auth_token=token"

        with patch(
            "apps.gateway.api.v1.endpoints.team.AuthService.get_user_from_token",
            return_value=SimpleNamespace(id=user_id),
        ):
            return TestClient(app).patch(
                f"/api/v1/teams/{team_id}",
                headers=headers,
                json=payload,
            )

    def _post_team_member(
        self,
        session,
        user_id,
        team_id,
        organization_id=None,
        raw_organization_id=None,
        payload=None,
    ):
        session.authenticate(user_id)
        app.dependency_overrides[get_db] = lambda: session
        headers = {"X-Request-ID": "98d6d88b-8d7a-46fd-8d12-f2024d2fac4b"}
        if raw_organization_id is not None:
            headers["X-Organization-Id"] = raw_organization_id
        elif organization_id is not None:
            headers["X-Organization-Id"] = str(organization_id)
        headers["Cookie"] = "auth_token=token"

        with patch(
            "apps.gateway.api.v1.endpoints.team.AuthService.get_user_from_token",
            return_value=SimpleNamespace(id=user_id),
        ):
            return TestClient(app).post(
                f"/api/v1/teams/{team_id}/members",
                headers=headers,
                json=payload,
            )

    def _delete_team_member(
        self,
        session,
        user_id,
        team_id,
        member_user_id,
        organization_id=None,
        raw_organization_id=None,
    ):
        session.authenticate(user_id)
        app.dependency_overrides[get_db] = lambda: session
        headers = {"X-Request-ID": "98d6d88b-8d7a-46fd-8d12-f2024d2fac4b"}
        if raw_organization_id is not None:
            headers["X-Organization-Id"] = raw_organization_id
        elif organization_id is not None:
            headers["X-Organization-Id"] = str(organization_id)
        headers["Cookie"] = "auth_token=token"

        with patch(
            "apps.gateway.api.v1.endpoints.team.AuthService.get_user_from_token",
            return_value=SimpleNamespace(id=user_id),
        ):
            return TestClient(app).delete(
                f"/api/v1/teams/{team_id}/members/{member_user_id}",
                headers=headers,
            )

    def _delete_team(
        self,
        session,
        user_id,
        team_id,
        organization_id=None,
        raw_organization_id=None,
    ):
        session.authenticate(user_id)
        app.dependency_overrides[get_db] = lambda: session
        headers = {"X-Request-ID": "98d6d88b-8d7a-46fd-8d12-f2024d2fac4b"}
        if raw_organization_id is not None:
            headers["X-Organization-Id"] = raw_organization_id
        elif organization_id is not None:
            headers["X-Organization-Id"] = str(organization_id)
        headers["Cookie"] = "auth_token=token"

        with patch(
            "apps.gateway.api.v1.endpoints.team.AuthService.get_user_from_token",
            return_value=SimpleNamespace(id=user_id),
        ):
            return TestClient(app).delete(
                f"/api/v1/teams/{team_id}",
                headers=headers,
            )


class _Query:
    def __init__(self, first_result=None, items=None, apply_filters=True):
        self.first_result = first_result
        self.items = items or []
        self.apply_filters = apply_filters
        self.join_values = []
        self.filter_expressions = []
        self.order_by_values = []
        self.options_values = []
        self.limit_value = None

    def join(self, *args):
        self.join_values.append(args)
        return self

    def filter(self, *expressions):
        self.filter_expressions.extend(expressions)
        return self

    def order_by(self, *args):
        self.order_by_values.extend(args)
        return self

    def with_for_update(self, **_kwargs):
        return self

    def options(self, *args):
        self.options_values.extend(args)
        return self

    def limit(self, value):
        self.limit_value = value
        return self

    def first(self):
        items = self._filtered_items()
        return items[0] if items else None

    def all(self):
        items = self._filtered_items()
        if self.limit_value is None:
            return items
        return items[: self.limit_value]

    def _filtered_items(self):
        if self.first_result is not None:
            items = [self.first_result]
        else:
            items = list(self.items)
        if not self.apply_filters:
            return items
        return [
            item
            for item in items
            if all(_matches_expression(item, expression) for expression in self.filter_expressions)
        ]


class _Session:
    def __init__(
        self,
        organization=None,
        membership=None,
        teams=None,
        team=None,
        memberships=None,
        organization_memberships=None,
        user=None,
        membership_after_rollback=None,
        commit_error=None,
    ):
        self.organization = organization
        self.membership = membership
        self.memberships = memberships or ([] if membership is None else [membership])
        self.explicit_organization_memberships = organization_memberships or []
        self.teams = teams or ([] if team is None else [team])
        self.user = user
        self.users = [] if user is None else [user]
        self.current_user_id = None
        self.membership_after_rollback = membership_after_rollback
        self.commit_error = commit_error
        self.organization_query = _Query(first_result=organization)
        self.membership_query = _Query(
            first_result=membership,
            items=self.memberships,
        )
        self.team_query = _Query(items=self.teams)
        self.user_query = _Query(first_result=user)
        self.query_calls = []
        self.added = []
        self.deleted = []
        self.info = {}
        self.audit_add_error = None
        self.committed = False
        self.commit_calls = 0
        self.rolled_back = False

    def authenticate(self, user_id):
        self.current_user_id = user_id
        if all(user.id != user_id for user in self.users):
            self.users.append(_user(id=user_id))

    def query(self, model):
        self.query_calls.append(model)
        if model is Organization:
            self.organization_query = _Query(first_result=self.organization)
            return self.organization_query
        if model is OrganizationMembership:
            self.organization_membership_query = _Query(
                items=self._organization_memberships()
            )
            return self.organization_membership_query
        if model is TeamMembership:
            self.membership_query = _Query(
                first_result=self.membership,
                items=self.memberships,
            )
            return self.membership_query
        if model is Team:
            self.team_query = _Query(items=self.teams)
            return self.team_query
        if model is User:
            self.user_query = _Query(items=self.users)
            return self.user_query
        raise AssertionError(f"Unexpected query model: {model}")

    def _organization_memberships(self):
        if self.organization is None:
            return []
        organization_id = self.organization.id
        rows = []
        rows.extend(self.explicit_organization_memberships)
        if (
            self.current_user_id is not None
            and self.membership is not None
            and not hasattr(self.membership, "user_id")
        ):
            rows.append(_organization_membership(self.current_user_id, organization_id))
        for membership in self.memberships:
            user_id = getattr(membership, "user_id", None)
            grantee_organization_id = getattr(
                membership, "grantee_organization_id", organization_id
            )
            if user_id is not None:
                rows.append(_organization_membership(user_id, grantee_organization_id))
        return rows

    def add(self, row):
        if isinstance(row, AuditLog) and self.audit_add_error is not None:
            raise self.audit_add_error
        _hydrate_defaults(row)
        self.added.append(row)
        if isinstance(row, Team):
            self.teams.append(row)
        if isinstance(row, TeamMembership):
            self.membership = row
            self.memberships.append(row)

    def delete(self, row):
        self.deleted.append(row)
        if self.membership is row:
            self.membership = None

    def commit(self):
        self.commit_calls += 1
        if self.commit_error is not None:
            error = self.commit_error
            self.commit_error = None
            raise error
        self.committed = True

    def rollback(self):
        self.rolled_back = True
        if self.membership_after_rollback is not None:
            self.membership = self.membership_after_rollback

    def refresh(self, row):
        _hydrate_defaults(row)


def _organization(
    id,
    name,
    created_by=None,
    managed_by=None,
    is_active=True,
):
    now = datetime.now(timezone.utc)
    return Organization(
        id=id,
        name=name,
        options={},
        flags=0,
        created_by=created_by or uuid4(),
        managed_by=managed_by,
        is_active=is_active,
        created_at=now,
        updated_at=now,
    )


def _user(id, email=None, name="Member", deactivated_at=None):
    now = datetime.now(timezone.utc)
    return User(
        id=id,
        email=email or f"{id}@example.com",
        name=name,
        social_provider="local",
        social_id=str(id),
        deactivated_at=deactivated_at,
        created_at=now,
        updated_at=now,
    )


def _team(
    id,
    organization_id,
    name,
    description=None,
    options=None,
    flags=0,
    created_by=None,
    managed_by=None,
    is_active=True,
    is_auto_add=False,
    created_at=None,
    updated_at=None,
    deactivated_at=None,
):
    now = datetime.now(timezone.utc)
    return Team(
        id=id,
        organization_id=organization_id,
        name=name,
        description=description,
        options=options or {},
        flags=flags,
        created_by=created_by or uuid4(),
        managed_by=managed_by,
        is_active=is_active,
        is_auto_add=is_auto_add,
        created_at=created_at or now,
        updated_at=updated_at or now,
        deactivated_at=deactivated_at,
    )


def _membership(
    id,
    organization_id,
    team_id,
    user_id,
    assigned_by,
    assigned_at=None,
    user=None,
):
    membership = TeamMembership(
        id=id,
        grantee_organization_id=organization_id,
        team_id=team_id,
        user_id=user_id,
        assigned_by=assigned_by,
        assigned_at=assigned_at or datetime.now(timezone.utc),
        options={},
        flags=0,
    )
    if user is not None:
        membership.user = user
    return membership


def _organization_membership(user_id, organization_id):
    return OrganizationMembership(
        id=uuid4(),
        user_id=user_id,
        organization_id=organization_id,
        membership_state=ORGANIZATION_MEMBERSHIP_ACTIVE,
        organization_auth_state=ORGANIZATION_AUTH_MEMBER,
    )


def _hydrate_defaults(row):
    now = datetime.now(timezone.utc)
    if getattr(row, "id", None) is None:
        row.id = uuid4()
    if isinstance(row, Team):
        row.options = row.options or {}
        row.flags = row.flags or 0
        row.is_active = True if row.is_active is None else row.is_active
        row.is_auto_add = (
            False if row.is_auto_add is None else row.is_auto_add
        )
        row.created_at = row.created_at or now
        row.updated_at = row.updated_at or now
        row.deactivated_at = getattr(row, "deactivated_at", None)
    if isinstance(row, TeamMembership):
        row.options = row.options or {}
        row.flags = row.flags or 0
        row.assigned_at = row.assigned_at or now


_ANY = object()


def _matches_expression(obj, expression):
    left_value = _column_value(obj, str(expression.left))
    if left_value is _ANY:
        return True

    if expression.operator is eq:
        right_value = _expression_value(obj, expression.right)
        if right_value is _ANY:
            return True
        return left_value == right_value
    if expression.operator is is_:
        if str(expression.right).lower() == "null":
            return left_value is None
        return left_value is (str(expression.right).lower() == "true")
    raise AssertionError(f"Unexpected filter operator: {expression.operator}")


def _expression_value(obj, expression):
    if hasattr(expression, "value"):
        return expression.value
    return _column_value(obj, str(expression))


def _column_value(obj, column):
    missing = object()
    value_by_column = {
        "organization.id": getattr(obj, "id", _ANY),
        "organization.is_active": getattr(obj, "is_active", _ANY),
        "teams.id": getattr(obj, "id", _ANY),
        "teams.organization_id": getattr(
            obj,
            "team_organization_id",
            getattr(obj, "organization_id", _ANY),
        ),
        "teams.name": getattr(obj, "name", _ANY),
        "teams.is_active": getattr(
            obj,
            "team_is_active",
            getattr(obj, "is_active", _ANY),
        ),
        "team_memberships.user_id": getattr(obj, "user_id", _ANY),
        "team_memberships.team_id": getattr(obj, "team_id", _ANY),
        "team_memberships.grantee_organization_id": getattr(
            obj,
            "membership_grantee_organization_id",
            getattr(obj, "grantee_organization_id", _ANY),
        ),
        "organization_memberships.user_id": getattr(obj, "user_id", _ANY),
        "organization_memberships.organization_id": getattr(
            obj, "organization_id", _ANY
        ),
        "organization_memberships.membership_state": getattr(
            obj, "membership_state", _ANY
        ),
        "users.id": getattr(obj, "id", _ANY),
        "users.deactivated_at": getattr(obj, "deactivated_at", _ANY),
    }
    value = value_by_column.get(column, missing)
    if value is missing:
        raise AssertionError(f"Unexpected filter column: {column}")
    return value


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
