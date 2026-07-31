import unittest
from datetime import datetime, timedelta, timezone
from operator import eq
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy.dialects import postgresql
from sqlalchemy.sql.operators import is_

from apps.gateway.main import app
from apps.gateway.services.app_lifecycle_lock import (
    AppPrimaryChangedDuringMutationError,
)
from apps.shared.audit.manual_ownership import is_manually_audited
from apps.shared.db.models.app import App
from apps.shared.db.models.audit_log import AuditLog
from apps.shared.db.models.knowledge import KnowledgeBase
from apps.shared.db.models.llm import LLMCredential
from apps.shared.db.models.organization import Organization
from apps.shared.db.models.organization_membership import OrganizationMembership
from apps.shared.db.models.team import (
    Team,
    TeamKnowledgePermission,
    TeamLLMPermission,
    TeamMembership,
    TeamWorkflowPermission,
    UserKnowledgePermission,
    UserLLMPermission,
    UserWorkflowPermission,
)
from apps.shared.db.models.user import User
from apps.shared.db.models.workflow import Workflow
from apps.shared.db.session import get_db


class TestPermissionsApi(unittest.TestCase):
    def setUp(self):
        """audit side effect를 막고 호출 인자만 검증하도록 patch한다."""
        self.main_audit_patcher = patch("apps.gateway.main.record_audit")
        self.permission_audit_patcher = patch(
            "apps.gateway.api.v1.endpoints.permissions.record_audit"
        )
        self.domain_delegate_patcher = patch(
            "apps.gateway.api.v1.endpoints.permissions._has_knowledge_permission_delegate",
            return_value=False,
        )
        self.main_audit_patcher.start()
        self.permission_audit = self.permission_audit_patcher.start()
        self.domain_delegate = self.domain_delegate_patcher.start()

    def tearDown(self):
        """테스트에서 추가한 patch와 FastAPI dependency override를 정리한다."""
        self.permission_audit_patcher.stop()
        self.domain_delegate_patcher.stop()
        self.main_audit_patcher.stop()
        app.dependency_overrides.pop(get_db, None)

    def test_put_team_workflow_permission_creates_row_for_organization_manager(self):
        # organization owner/manager는 team membership 없이도 workflow 권한을 부여할 수 있다.
        user_id = uuid4()
        organization_id = uuid4()
        workflow_id = uuid4()
        team_id = uuid4()
        upsert_result = _team_workflow_permission(
            organization_id=organization_id,
            workflow_id=workflow_id,
            team_id=team_id,
            auth_state="builder",
            assigned_by=user_id,
        )
        session = _Session(
            organization=_organization(id=organization_id, created_by=user_id),
            workflow=_workflow(id=workflow_id, organization_id=organization_id),
            team=_team(id=team_id, organization_id=organization_id),
            upsert_result=upsert_result,
        )
        response = self._put_permission(
            session=session,
            user_id=user_id,
            organization_id=organization_id,
            workflow_id=workflow_id,
            team_id=team_id,
            payload={"auth_state": "builder"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json()["grantee_organization_id"], str(organization_id)
        )
        self.assertEqual(response.json()["workflow_id"], str(workflow_id))
        self.assertEqual(response.json()["team_id"], str(team_id))
        self.assertEqual(response.json()["auth_state"], "builder")
        self.assertEqual(response.json()["assigned_by"], str(user_id))
        self.assertEqual(len(session.added), 1)
        self.assertTrue(session.scalars_called)
        _assert_organization_scope_filters(
            self, session.organization_query, organization_id
        )
        _assert_workflow_scope_filters(
            self,
            session.workflow_query,
            workflow_id,
            organization_id,
        )
        _assert_team_scope_filters(self, session.team_query, team_id, organization_id)
        self.assertIn(
            "ON CONFLICT (grantee_organization_id, workflow_id, team_id)",
            str(session.upsert_statement.compile(dialect=postgresql.dialect())),
        )
        self.assertTrue(session.committed)
        self.assertEqual(len(session.lock_statements), 2)
        scope_lock = str(
            session.lock_statements[0].compile(
                dialect=postgresql.dialect(),
                compile_kwargs={"literal_binds": True},
            )
        )
        self.assertIn("workflow_permission_scope", scope_lock)
        self.assertIsNotNone(session.lock_statement)
        self.assertIn(
            "pg_advisory_xact_lock",
            str(session.lock_statement.compile(dialect=postgresql.dialect())),
        )
        self.assertNotIn(TeamMembership, session.query_calls)
        self.permission_audit.assert_not_called()
        audit = session.added[0]
        self.assertIsInstance(audit, AuditLog)
        self.assertEqual(audit.action, "team_workflow_permission.created")
        self.assertEqual(audit.category, "data_change")
        self.assertEqual(audit.actor_id, user_id)
        self.assertEqual(audit.actor_type, "user")
        self.assertEqual(audit.target_type, "team_workflow_permission")
        self.assertEqual(audit.target_id, str(upsert_result.id))
        self.assertIsNone(audit.before)
        self.assertEqual(
            audit.after,
            {
                "grantee_organization_id": str(organization_id),
                "team_id": str(team_id),
                "workflow_id": str(workflow_id),
                "auth_state": "builder",
            },
        )
        self.assertEqual(audit.audit_metadata["request_id"], "98d6d88b-8d7a-46fd-8d12-f2024d2fac4b")
        self.assertEqual(audit.audit_metadata["actor"]["id"], str(user_id))

    def test_put_team_workflow_permission_rejects_primary_changed_while_waiting(self):
        user_id = uuid4()
        organization_id = uuid4()
        workflow_id = uuid4()
        team_id = uuid4()
        session = _Session(
            organization=_organization(id=organization_id, created_by=user_id),
            workflow=_workflow(id=workflow_id, organization_id=organization_id),
            team=_team(id=team_id, organization_id=organization_id),
        )

        with patch(
            "apps.gateway.api.v1.endpoints.permissions.lock_app_for_workflow_mutation",
            side_effect=AppPrimaryChangedDuringMutationError,
        ):
            response = self._put_permission(
                session=session,
                user_id=user_id,
                organization_id=organization_id,
                workflow_id=workflow_id,
                team_id=team_id,
                payload={"auth_state": "viewer"},
            )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"]["code"], "workflow.primary_changed")
        self.assertIn(("rollback", None), session.operations)
        self.assertFalse(session.scalars_called)

    def test_list_workflow_permissions_returns_team_and_user_entries_for_manager(self):
        user_id = uuid4()
        organization_id = uuid4()
        workflow_id = uuid4()
        team_id = uuid4()
        target_user_id = uuid4()
        assigned_at = datetime(2026, 6, 29, 5, 17, 29, tzinfo=timezone.utc)
        team = _team(id=team_id, organization_id=organization_id)
        target_user = _user(
            id=target_user_id,
            email="target@example.com",
            name="Target User",
        )
        team_permission = _team_workflow_permission(
            organization_id=organization_id,
            workflow_id=workflow_id,
            team_id=team_id,
            auth_state="builder",
            assigned_by=user_id,
        )
        team_permission.assigned_at = assigned_at
        team_permission.team = team
        team_permission.team_organization_id = organization_id
        team_permission.team_is_active = True
        user_permission = _user_workflow_permission(
            organization_id=organization_id,
            workflow_id=workflow_id,
            user_id=target_user_id,
            auth_state="viewer",
            assigned_by=user_id,
        )
        user_permission.assigned_at = assigned_at
        user_permission.user = target_user
        session = _Session(
            organization=_organization(id=organization_id, created_by=user_id),
            workflow=_workflow(id=workflow_id, organization_id=organization_id),
            workflow_team_permissions=[team_permission],
            workflow_user_permissions=[user_permission],
        )

        response = self._get_workflow_permissions(
            session=session,
            user_id=user_id,
            organization_id=organization_id,
            workflow_id=workflow_id,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "resource_type": "workflow",
                "resource_id": str(workflow_id),
                "organization_id": str(organization_id),
                "team_permissions": [
                    {
                        "id": str(team_permission.id),
                        "grantee_type": "team",
                        "grantee_id": str(team_id),
                        "grantee_name": "Builders",
                        "auth_state": "builder",
                        "assigned_at": "2026-06-29T05:17:29Z",
                    }
                ],
                "user_permissions": [
                    {
                        "id": str(user_permission.id),
                        "grantee_type": "user",
                        "grantee_id": str(target_user_id),
                        "grantee_name": "Target User",
                        "auth_state": "viewer",
                        "assigned_at": "2026-06-29T05:17:29Z",
                    }
                ],
            },
        )
        self.assertIn(TeamWorkflowPermission, session.query_calls)
        self.assertIn(UserWorkflowPermission, session.query_calls)

    def test_list_knowledge_permissions_returns_team_and_user_entries_for_manager(self):
        user_id = uuid4()
        organization_id = uuid4()
        knowledge_base_id = uuid4()
        team_id = uuid4()
        target_user_id = uuid4()
        assigned_at = datetime(2026, 7, 9, 5, 17, 29, tzinfo=timezone.utc)
        team = _team(id=team_id, organization_id=organization_id)
        target_user = _user(
            id=target_user_id,
            email="target@example.com",
            name="Target User",
        )
        team_permission = _team_knowledge_permission(
            organization_id=organization_id,
            knowledge_base_id=knowledge_base_id,
            team_id=team_id,
            auth_state="operator",
            assigned_by=user_id,
        )
        team_permission.assigned_at = assigned_at
        team_permission.team = team
        team_permission.team_organization_id = organization_id
        team_permission.team_is_active = True
        user_permission = _user_knowledge_permission(
            organization_id=organization_id,
            knowledge_base_id=knowledge_base_id,
            user_id=target_user_id,
            auth_state="manager",
            assigned_by=user_id,
        )
        user_permission.assigned_at = assigned_at
        user_permission.user = target_user
        session = _Session(
            organization=_organization(id=organization_id, created_by=user_id),
            knowledge_base=_knowledge_base(
                id=knowledge_base_id,
                organization_id=organization_id,
                user_id=user_id,
            ),
            knowledge_team_permissions=[team_permission],
            knowledge_user_permissions=[user_permission],
        )

        response = self._get_knowledge_permissions(
            session=session,
            user_id=user_id,
            organization_id=organization_id,
            knowledge_base_id=knowledge_base_id,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "resource_type": "knowledge_base",
                "resource_id": str(knowledge_base_id),
                "organization_id": str(organization_id),
                "team_permissions": [
                    {
                        "id": str(team_permission.id),
                        "grantee_type": "team",
                        "grantee_id": str(team_id),
                        "grantee_name": "Builders",
                        "auth_state": "operator",
                        "assigned_at": "2026-07-09T05:17:29Z",
                    }
                ],
                "user_permissions": [
                    {
                        "id": str(user_permission.id),
                        "grantee_type": "user",
                        "grantee_id": str(target_user_id),
                        "grantee_name": "Target User",
                        "auth_state": "manager",
                        "assigned_at": "2026-07-09T05:17:29Z",
                    }
                ],
            },
        )
        self.assertIn(TeamKnowledgePermission, session.query_calls)
        self.assertIn(UserKnowledgePermission, session.query_calls)

    def test_list_knowledge_permissions_hides_archived_knowledge_base(self):
        user_id = uuid4()
        organization_id = uuid4()
        knowledge_base_id = uuid4()
        session = _Session(
            organization=_organization(id=organization_id, created_by=user_id),
            knowledge_base=_knowledge_base(
                id=knowledge_base_id,
                organization_id=organization_id,
                user_id=user_id,
                lifecycle_state="archived",
            ),
        )

        response = self._get_knowledge_permissions(
            session=session,
            user_id=user_id,
            organization_id=organization_id,
            knowledge_base_id=knowledge_base_id,
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(
            response.json(),
            _error("resource.not_found", "Knowledge Base not found."),
        )
        self.assertNotIn(TeamKnowledgePermission, session.query_calls)
        self.assertNotIn(UserKnowledgePermission, session.query_calls)

    def test_put_team_knowledge_permission_hides_archived_knowledge_base(self):
        user_id = uuid4()
        organization_id = uuid4()
        knowledge_base_id = uuid4()
        team_id = uuid4()
        session = _Session(
            organization=_organization(id=organization_id, created_by=user_id),
            knowledge_base=_knowledge_base(
                id=knowledge_base_id,
                organization_id=organization_id,
                user_id=user_id,
                lifecycle_state="archived",
            ),
            team=_team(id=team_id, organization_id=organization_id),
        )

        response = self._put_knowledge_permission(
            session=session,
            user_id=user_id,
            knowledge_base_id=knowledge_base_id,
            team_id=team_id,
            payload={"auth_state": "viewer"},
            organization_id=organization_id,
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(
            response.json(),
            _error("resource.not_found", "Knowledge Base not found."),
        )
        self.assertNotIn(Team, session.query_calls)
        self.assertFalse(session.scalars_called)
        self.assertFalse(session.committed)
        self.permission_audit.assert_not_called()

    def test_put_team_knowledge_permission_creates_audit_row_for_organization_manager(
        self,
    ):
        user_id = uuid4()
        organization_id = uuid4()
        knowledge_base_id = uuid4()
        team_id = uuid4()
        upsert_result = _team_knowledge_permission(
            organization_id=organization_id,
            knowledge_base_id=knowledge_base_id,
            team_id=team_id,
            auth_state="operator",
            assigned_by=user_id,
        )
        session = _Session(
            organization=_organization(id=organization_id, created_by=user_id),
            knowledge_base=_knowledge_base(
                id=knowledge_base_id,
                organization_id=organization_id,
                user_id=user_id,
            ),
            team=_team(id=team_id, organization_id=organization_id),
            knowledge_upsert_result=upsert_result,
        )

        response = self._put_knowledge_permission(
            session=session,
            user_id=user_id,
            knowledge_base_id=knowledge_base_id,
            team_id=team_id,
            payload={"auth_state": "operator"},
            organization_id=organization_id,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["knowledge_base_id"], str(knowledge_base_id))
        self.assertEqual(response.json()["team_id"], str(team_id))
        self.assertEqual(response.json()["auth_state"], "operator")
        self.assertTrue(session.committed)
        self.permission_audit.assert_not_called()
        audit = _single_added_audit(session)
        self.assertEqual(audit.action, "team_knowledge_permission.created")
        self.assertEqual(audit.category, "data_change")
        self.assertEqual(audit.actor_id, user_id)
        self.assertEqual(audit.target_type, "team_knowledge_permission")
        self.assertEqual(audit.target_id, str(upsert_result.id))
        self.assertIsNone(audit.before)
        self.assertEqual(audit.after["knowledge_base_id"], str(knowledge_base_id))
        self.assertEqual(audit.after["team_id"], str(team_id))
        self.assertIsInstance(audit.after["assigned_at"], str)
        self.assertEqual(
            audit.audit_metadata["organization_id"], str(organization_id)
        )
        _assert_audit_added_before_commit(self, session)

    def test_put_team_knowledge_permission_updates_audit_row_for_manager(self):
        user_id = uuid4()
        organization_id = uuid4()
        knowledge_base_id = uuid4()
        team_id = uuid4()
        previous_assigned_by = uuid4()
        existing_permission = _team_knowledge_permission(
            organization_id=organization_id,
            knowledge_base_id=knowledge_base_id,
            team_id=team_id,
            auth_state="viewer",
            assigned_by=previous_assigned_by,
        )
        upsert_result = _team_knowledge_permission(
            organization_id=organization_id,
            knowledge_base_id=knowledge_base_id,
            team_id=team_id,
            auth_state="manager",
            assigned_by=user_id,
        )
        upsert_result.id = existing_permission.id
        upsert_result.assigned_at = existing_permission.assigned_at + timedelta(
            seconds=1
        )
        session = _Session(
            organization=_organization(id=organization_id, created_by=user_id),
            knowledge_base=_knowledge_base(
                id=knowledge_base_id,
                organization_id=organization_id,
                user_id=user_id,
            ),
            team=_team(id=team_id, organization_id=organization_id),
            existing_knowledge_permission=existing_permission,
            knowledge_upsert_result=upsert_result,
        )

        response = self._put_knowledge_permission(
            session=session,
            user_id=user_id,
            knowledge_base_id=knowledge_base_id,
            team_id=team_id,
            payload={"auth_state": "manager"},
            organization_id=organization_id,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["auth_state"], "manager")
        self.permission_audit.assert_not_called()
        audit = _single_added_audit(session)
        self.assertEqual(audit.action, "team_knowledge_permission.updated")
        self.assertEqual(audit.target_type, "team_knowledge_permission")
        self.assertEqual(audit.target_id, str(existing_permission.id))
        self.assertEqual(audit.before["auth_state"], "viewer")
        self.assertEqual(audit.after["auth_state"], "manager")
        self.assertEqual(audit.before["assigned_by"], str(previous_assigned_by))
        self.assertEqual(audit.after["assigned_by"], str(user_id))
        self.assertNotIn("knowledge_base_id", audit.before)
        _assert_audit_added_before_commit(self, session)

    def test_put_user_knowledge_permission_creates_row_for_organization_manager(self):
        user_id = uuid4()
        organization_id = uuid4()
        knowledge_base_id = uuid4()
        target_user_id = uuid4()
        upsert_result = _user_knowledge_permission(
            organization_id=organization_id,
            knowledge_base_id=knowledge_base_id,
            user_id=target_user_id,
            auth_state="builder",
            assigned_by=user_id,
        )
        session = _Session(
            organization=_organization(id=organization_id, created_by=user_id),
            knowledge_base=_knowledge_base(
                id=knowledge_base_id,
                organization_id=organization_id,
                user_id=user_id,
            ),
            target_user=_user(
                id=target_user_id,
                email="target@example.com",
                name="Target User",
            ),
            target_membership=_membership(
                user_id=target_user_id,
                organization_id=organization_id,
            ),
            user_knowledge_upsert_result=upsert_result,
        )

        response = self._put_user_knowledge_permission(
            session=session,
            user_id=user_id,
            knowledge_base_id=knowledge_base_id,
            target_user_id=target_user_id,
            payload={"auth_state": "builder"},
            organization_id=organization_id,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["knowledge_base_id"], str(knowledge_base_id))
        self.assertEqual(response.json()["user_id"], str(target_user_id))
        self.assertEqual(response.json()["auth_state"], "builder")
        self.assertTrue(session.committed)
        self.assertTrue(session.scalars_called)
        compiled = str(session.upsert_statement.compile(dialect=postgresql.dialect()))
        self.assertIn(
            "ON CONFLICT (grantee_organization_id, user_id, knowledge_base_id)",
            compiled,
        )
        self.permission_audit.assert_not_called()
        audit = _single_added_audit(session)
        self.assertEqual(audit.action, "user_knowledge_permission.created")
        self.assertEqual(audit.category, "data_change")
        self.assertEqual(audit.actor_id, user_id)
        self.assertEqual(audit.actor_type, "user")
        self.assertEqual(audit.target_type, "user_knowledge_permission")
        self.assertEqual(audit.target_id, str(upsert_result.id))
        self.assertIsNone(audit.before)
        self.assertEqual(audit.after["knowledge_base_id"], str(knowledge_base_id))
        self.assertEqual(audit.after["user_id"], str(target_user_id))
        self.assertEqual(audit.after["auth_state"], "builder")
        self.assertNotIn("assigned_at", audit.after)
        self.assertEqual(audit.audit_metadata["request_id"], "98d6d88b-8d7a-46fd-8d12-f2024d2fac4b")
        self.assertEqual(audit.audit_metadata["actor"]["id"], str(user_id))
        self.assertEqual(
            audit.audit_metadata["organization_id"], str(organization_id)
        )
        _assert_audit_added_before_commit(self, session)

    def test_put_user_knowledge_permission_updates_audit_row_for_manager(self):
        user_id = uuid4()
        organization_id = uuid4()
        knowledge_base_id = uuid4()
        target_user_id = uuid4()
        previous_assigned_by = uuid4()
        existing_permission = _user_knowledge_permission(
            organization_id=organization_id,
            knowledge_base_id=knowledge_base_id,
            user_id=target_user_id,
            auth_state="viewer",
            assigned_by=previous_assigned_by,
        )
        upsert_result = _user_knowledge_permission(
            organization_id=organization_id,
            knowledge_base_id=knowledge_base_id,
            user_id=target_user_id,
            auth_state="builder",
            assigned_by=user_id,
        )
        upsert_result.id = existing_permission.id
        upsert_result.assigned_at = existing_permission.assigned_at + timedelta(
            seconds=1
        )
        session = _Session(
            organization=_organization(id=organization_id, created_by=user_id),
            knowledge_base=_knowledge_base(
                id=knowledge_base_id,
                organization_id=organization_id,
                user_id=user_id,
            ),
            target_user=_user(
                id=target_user_id,
                email="target@example.com",
                name="Target User",
            ),
            target_membership=_membership(
                user_id=target_user_id,
                organization_id=organization_id,
            ),
            existing_user_knowledge_permission=existing_permission,
            user_knowledge_upsert_result=upsert_result,
        )

        response = self._put_user_knowledge_permission(
            session=session,
            user_id=user_id,
            knowledge_base_id=knowledge_base_id,
            target_user_id=target_user_id,
            payload={"auth_state": "builder"},
            organization_id=organization_id,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["auth_state"], "builder")
        self.permission_audit.assert_not_called()
        audit = _single_added_audit(session)
        self.assertEqual(audit.action, "user_knowledge_permission.updated")
        self.assertEqual(audit.target_type, "user_knowledge_permission")
        self.assertEqual(audit.target_id, str(existing_permission.id))
        self.assertEqual(audit.before["auth_state"], "viewer")
        self.assertEqual(audit.after["auth_state"], "builder")
        self.assertEqual(
            audit.before["knowledge_base_id"],
            str(knowledge_base_id),
        )
        self.assertEqual(audit.after["knowledge_base_id"], str(knowledge_base_id))
        self.assertNotIn("assigned_by", audit.before)
        _assert_audit_added_before_commit(self, session)

    def test_put_user_knowledge_permission_noops_same_auth_state_without_audit(self):
        user_id = uuid4()
        organization_id = uuid4()
        knowledge_base_id = uuid4()
        target_user_id = uuid4()
        existing_permission = _user_knowledge_permission(
            organization_id=organization_id,
            knowledge_base_id=knowledge_base_id,
            user_id=target_user_id,
            auth_state="viewer",
            assigned_by=user_id,
        )
        session = _Session(
            organization=_organization(id=organization_id, created_by=user_id),
            knowledge_base=_knowledge_base(
                id=knowledge_base_id,
                organization_id=organization_id,
                user_id=user_id,
            ),
            target_user=_user(
                id=target_user_id,
                email="target@example.com",
                name="Target User",
            ),
            target_membership=_membership(
                user_id=target_user_id,
                organization_id=organization_id,
            ),
            existing_user_knowledge_permission=existing_permission,
            user_knowledge_upsert_result=None,
        )

        response = self._put_user_knowledge_permission(
            session=session,
            user_id=user_id,
            knowledge_base_id=knowledge_base_id,
            target_user_id=target_user_id,
            payload={"auth_state": "viewer"},
            organization_id=organization_id,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["auth_state"], "viewer")
        self.assertTrue(session.committed)
        self.assertTrue(session.scalars_called)
        self.permission_audit.assert_not_called()
        self.assertEqual(
            [value for value in session.added if isinstance(value, AuditLog)],
            [],
        )

    def test_put_team_knowledge_permission_rejects_none_auth_state(self):
        response = self._put_knowledge_permission(
            session=_Session(),
            user_id=uuid4(),
            knowledge_base_id=uuid4(),
            team_id=uuid4(),
            payload={"auth_state": "none"},
            organization_id=uuid4(),
        )

        self.assertEqual(response.status_code, 422)

    def test_domain_delegate_can_grant_kb_permission_to_another_user(self):
        actor_id = uuid4()
        target_user_id = uuid4()
        organization_id = uuid4()
        knowledge_base_id = uuid4()
        membership = _membership(actor_id, organization_id)
        target_membership = _membership(target_user_id, organization_id)
        upsert_result = _user_knowledge_permission(
            organization_id=organization_id,
            knowledge_base_id=knowledge_base_id,
            user_id=target_user_id,
            auth_state="viewer",
            assigned_by=actor_id,
        )
        session = _Session(
            organization=_organization(id=organization_id, created_by=uuid4()),
            membership=membership,
            knowledge_base=_knowledge_base(
                id=knowledge_base_id,
                organization_id=organization_id,
                user_id=uuid4(),
            ),
            target_user=_user(
                id=target_user_id,
                email="target@example.com",
                name="Target User",
            ),
            target_membership=target_membership,
            user_knowledge_upsert_result=upsert_result,
        )
        self.domain_delegate.return_value = True

        response = self._put_user_knowledge_permission(
            session=session,
            user_id=actor_id,
            knowledge_base_id=knowledge_base_id,
            target_user_id=target_user_id,
            payload={"auth_state": "viewer"},
            organization_id=organization_id,
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(session.committed)

    def test_domain_delegate_cannot_grant_kb_permission_to_self(self):
        actor_id = uuid4()
        organization_id = uuid4()
        knowledge_base_id = uuid4()
        membership = _membership(actor_id, organization_id)
        session = _Session(
            organization=_organization(id=organization_id, created_by=uuid4()),
            membership=membership,
            knowledge_base=_knowledge_base(
                id=knowledge_base_id,
                organization_id=organization_id,
                user_id=uuid4(),
            ),
            target_user=_user(
                id=actor_id,
                email="actor@example.com",
                name="Actor User",
            ),
            target_membership=membership,
        )
        self.domain_delegate.return_value = True

        response = self._put_user_knowledge_permission(
            session=session,
            user_id=actor_id,
            knowledge_base_id=knowledge_base_id,
            target_user_id=actor_id,
            payload={"auth_state": "manager"},
            organization_id=organization_id,
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"]["code"], "policy.blocked")
        self.assertEqual(
            response.json()["error"]["details"]["policy_reason"],
            "knowledge.self_escalation",
        )
        self.assertFalse(session.scalars_called)
        self.assertFalse(session.committed)
        self.permission_audit.assert_called_once()
        self.assertEqual(
            self.permission_audit.call_args.kwargs["action"],
            "knowledge.permission_grant.blocked",
        )

    def test_domain_delegate_cannot_grant_kb_permission_to_own_team(self):
        actor_id = uuid4()
        organization_id = uuid4()
        knowledge_base_id = uuid4()
        team_id = uuid4()
        membership = _membership(actor_id, organization_id)
        session = _Session(
            organization=_organization(id=organization_id, created_by=uuid4()),
            membership=membership,
            knowledge_base=_knowledge_base(
                id=knowledge_base_id,
                organization_id=organization_id,
                user_id=uuid4(),
            ),
            team=_team(id=team_id, organization_id=organization_id),
        )
        self.domain_delegate.return_value = True

        with patch(
            "apps.gateway.api.v1.endpoints.permissions._actor_is_active_member_of_team",
            return_value=True,
        ):
            response = self._put_knowledge_permission(
                session=session,
                user_id=actor_id,
                knowledge_base_id=knowledge_base_id,
                team_id=team_id,
                payload={"auth_state": "builder"},
                organization_id=organization_id,
            )

        self.assertEqual(response.status_code, 409)
        self.assertFalse(session.scalars_called)
        self.assertFalse(session.committed)
        self.permission_audit.assert_called_once()

    def test_delete_user_knowledge_permission_deletes_row_for_organization_manager(self):
        user_id = uuid4()
        organization_id = uuid4()
        knowledge_base_id = uuid4()
        target_user_id = uuid4()
        existing_permission = _user_knowledge_permission(
            organization_id=organization_id,
            knowledge_base_id=knowledge_base_id,
            user_id=target_user_id,
            auth_state="viewer",
            assigned_by=user_id,
        )
        session = _Session(
            organization=_organization(id=organization_id, created_by=user_id),
            knowledge_base=_knowledge_base(
                id=knowledge_base_id,
                organization_id=organization_id,
                user_id=user_id,
            ),
            existing_user_knowledge_permission=existing_permission,
        )

        response = self._delete_user_knowledge_permission(
            session=session,
            user_id=user_id,
            knowledge_base_id=knowledge_base_id,
            target_user_id=target_user_id,
            organization_id=organization_id,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "message": "User knowledge permission deleted",
                "id": str(existing_permission.id),
            },
        )
        self.assertEqual(session.deleted, [existing_permission])
        self.assertEqual(session.bulk_deleted, [])
        self.assertTrue(session.committed)
        self.permission_audit.assert_not_called()
        audit = _single_added_audit(session)
        self.assertEqual(audit.action, "user_knowledge_permission.deleted")
        self.assertEqual(audit.category, "data_change")
        self.assertEqual(audit.actor_id, user_id)
        self.assertEqual(audit.target_type, "user_knowledge_permission")
        self.assertEqual(audit.target_id, str(existing_permission.id))
        self.assertEqual(audit.before["user_id"], str(target_user_id))
        self.assertEqual(
            audit.before["knowledge_base_id"],
            str(knowledge_base_id),
        )
        self.assertEqual(audit.before["auth_state"], "viewer")
        self.assertNotIn("assigned_at", audit.before)
        self.assertIsNone(audit.after)
        _assert_audit_added_before_commit(self, session)

    def test_delete_user_knowledge_permission_hides_deleted_knowledge_base(self):
        user_id = uuid4()
        organization_id = uuid4()
        knowledge_base_id = uuid4()
        target_user_id = uuid4()
        existing_permission = _user_knowledge_permission(
            organization_id=organization_id,
            knowledge_base_id=knowledge_base_id,
            user_id=target_user_id,
            auth_state="viewer",
            assigned_by=user_id,
        )
        session = _Session(
            organization=_organization(id=organization_id, created_by=user_id),
            knowledge_base=_knowledge_base(
                id=knowledge_base_id,
                organization_id=organization_id,
                user_id=user_id,
                lifecycle_state="deleted",
            ),
            existing_user_knowledge_permission=existing_permission,
        )

        response = self._delete_user_knowledge_permission(
            session=session,
            user_id=user_id,
            knowledge_base_id=knowledge_base_id,
            target_user_id=target_user_id,
            organization_id=organization_id,
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(
            response.json(),
            _error("resource.not_found", "Knowledge Base not found."),
        )
        self.assertEqual(session.deleted, [])
        self.assertFalse(session.committed)
        self.assertIsNone(session.lock_statement)
        self.permission_audit.assert_not_called()

    def test_delete_team_knowledge_permission_deletes_row_for_organization_manager(self):
        user_id = uuid4()
        organization_id = uuid4()
        knowledge_base_id = uuid4()
        team_id = uuid4()
        existing_permission = _team_knowledge_permission(
            organization_id=organization_id,
            knowledge_base_id=knowledge_base_id,
            team_id=team_id,
            auth_state="operator",
            assigned_by=user_id,
        )
        session = _Session(
            organization=_organization(id=organization_id, created_by=user_id),
            knowledge_base=_knowledge_base(
                id=knowledge_base_id,
                organization_id=organization_id,
                user_id=user_id,
            ),
            team=_team(id=team_id, organization_id=organization_id),
            existing_knowledge_permission=existing_permission,
        )

        response = self._delete_knowledge_permission(
            session=session,
            user_id=user_id,
            knowledge_base_id=knowledge_base_id,
            team_id=team_id,
            organization_id=organization_id,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "message": "Team knowledge permission deleted",
                "id": str(existing_permission.id),
            },
        )
        self.assertEqual(session.deleted, [])
        self.assertEqual(session.bulk_deleted, [existing_permission])
        self.assertTrue(session.committed)
        self.permission_audit.assert_not_called()
        audit = _single_added_audit(session)
        self.assertEqual(audit.action, "team_knowledge_permission.deleted")
        self.assertEqual(audit.category, "data_change")
        self.assertEqual(audit.actor_id, user_id)
        self.assertEqual(audit.target_type, "team_knowledge_permission")
        self.assertEqual(audit.target_id, str(existing_permission.id))
        self.assertEqual(audit.before["id"], str(existing_permission.id))
        self.assertEqual(audit.before["team_id"], str(team_id))
        self.assertEqual(audit.before["auth_state"], "operator")
        self.assertIsInstance(audit.before["assigned_at"], str)
        self.assertIsNone(audit.after)
        self.assertEqual(
            audit.audit_metadata["organization_id"], str(organization_id)
        )
        _assert_audit_added_before_commit(self, session)

    def test_put_team_workflow_permission_updates_row_for_workflow_manager(self):
        # organization manager가 아니어도 대상 workflow의 manager 권한이 있으면 기존 row를 수정할 수 있다.
        user_id = uuid4()
        organization_id = uuid4()
        workflow_id = uuid4()
        team_id = uuid4()
        previous_assigned_by = uuid4()
        existing_permission = _team_workflow_permission(
            organization_id=organization_id,
            workflow_id=workflow_id,
            team_id=team_id,
            auth_state="viewer",
            assigned_by=previous_assigned_by,
        )
        upsert_result = _team_workflow_permission(
            organization_id=organization_id,
            workflow_id=workflow_id,
            team_id=team_id,
            auth_state="manager",
            assigned_by=user_id,
        )
        upsert_result.id = existing_permission.id
        upsert_result.assigned_at = existing_permission.assigned_at + timedelta(
            seconds=1
        )
        session = _Session(
            organization=_organization(id=organization_id, created_by=uuid4()),
            membership=_membership(user_id=user_id, organization_id=organization_id),
            workflow=_workflow(id=workflow_id, organization_id=organization_id),
            team=_team(id=team_id, organization_id=organization_id),
            manager_permissions=[
                _team_workflow_permission(
                    organization_id=organization_id,
                    workflow_id=workflow_id,
                    team_id=uuid4(),
                    auth_state="manager",
                    assigned_by=uuid4(),
                    member_user_id=user_id,
                )
            ],
            existing_permission=existing_permission,
            upsert_result=upsert_result,
        )

        response = self._put_permission(
            session=session,
            user_id=user_id,
            organization_id=organization_id,
            workflow_id=workflow_id,
            team_id=team_id,
            payload={"auth_state": "MANAGER"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["auth_state"], "manager")
        self.assertEqual(len(session.added), 1)
        self.assertEqual(session.workflow_permission_query_count, 2)
        self.assertTrue(session.scalars_called)
        _assert_active_membership_filters(
            self,
            session.membership_query,
            user_id,
            organization_id,
        )
        _assert_workflow_manage_filters(
            self,
            session.workflow_manager_query,
            user_id,
            workflow_id,
            organization_id,
        )
        self.assertTrue(session.committed)
        self.assertIsNotNone(session.lock_statement)
        self.permission_audit.assert_not_called()
        audit = session.added[0]
        self.assertEqual(audit.action, "team_workflow_permission.updated")
        self.assertEqual(audit.target_id, str(existing_permission.id))
        self.assertEqual(audit.before["auth_state"], "viewer")
        self.assertEqual(audit.after["auth_state"], "manager")
        self.assertEqual(audit.before["workflow_id"], str(workflow_id))
        self.assertEqual(audit.before["team_id"], str(team_id))
        self.assertNotIn("assigned_by", audit.before)
        self.assertNotIn("assigned_at", audit.before)
        self.assertEqual(audit.audit_metadata["request_id"], "98d6d88b-8d7a-46fd-8d12-f2024d2fac4b")
        self.assertEqual(audit.audit_metadata["actor"]["id"], str(user_id))

    def test_put_team_workflow_permission_rejects_member_without_manage(self):
        # active member라도 workflow manager가 아니면 권한 변경은 거부해야 한다.
        user_id = uuid4()
        organization_id = uuid4()
        workflow_id = uuid4()
        team_id = uuid4()
        session = _Session(
            organization=_organization(id=organization_id, created_by=uuid4()),
            membership=_membership(user_id=user_id, organization_id=organization_id),
            workflow=_workflow(id=workflow_id, organization_id=organization_id),
            team=_team(id=team_id, organization_id=organization_id),
            manager_permissions=[
                _team_workflow_permission(
                    organization_id=organization_id,
                    workflow_id=workflow_id,
                    team_id=uuid4(),
                    auth_state="builder",
                    assigned_by=uuid4(),
                    member_user_id=user_id,
                )
            ],
        )

        response = self._put_permission(
            session=session,
            user_id=user_id,
            organization_id=organization_id,
            workflow_id=workflow_id,
            team_id=team_id,
            payload={"auth_state": "viewer"},
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(
            response.json(),
            _error(
                "permission.denied",
                "Workflow manage or organization manager permission is required.",
            ),
        )
        self.assertFalse(session.committed)
        self.assertFalse(session.scalars_called)
        self.assertEqual(len(session.added), 0)
        _assert_workflow_manage_filters(
            self,
            session.workflow_manager_query,
            user_id,
            workflow_id,
            organization_id,
        )

    def test_put_team_workflow_permission_ignores_manager_permission_from_other_scope(
        self,
    ):
        # 다른 workflow/organization의 manager 권한은 현재 workflow 권한 변경에 쓰이면 안 된다.
        user_id = uuid4()
        organization_id = uuid4()
        workflow_id = uuid4()
        team_id = uuid4()
        session = _Session(
            organization=_organization(id=organization_id, created_by=uuid4()),
            membership=_membership(user_id=user_id, organization_id=organization_id),
            workflow=_workflow(id=workflow_id, organization_id=organization_id),
            team=_team(id=team_id, organization_id=organization_id),
            manager_permissions=[
                _team_workflow_permission(
                    organization_id=uuid4(),
                    workflow_id=uuid4(),
                    team_id=team_id,
                    auth_state="manager",
                    assigned_by=uuid4(),
                    member_user_id=user_id,
                )
            ],
        )

        response = self._put_permission(
            session=session,
            user_id=user_id,
            organization_id=organization_id,
            workflow_id=workflow_id,
            team_id=team_id,
            payload={"auth_state": "viewer"},
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(
            response.json(),
            _error(
                "permission.denied",
                "Workflow manage or organization manager permission is required.",
            ),
        )
        self.assertFalse(session.committed)
        self.assertFalse(session.scalars_called)
        _assert_workflow_manage_filters(
            self,
            session.workflow_manager_query,
            user_id,
            workflow_id,
            organization_id,
        )

    def test_put_team_workflow_permission_allows_user_direct_manager(self):
        # user direct manager 권한도 effective manager로 합산되어 권한 변경을 허용한다.
        user_id = uuid4()
        organization_id = uuid4()
        workflow_id = uuid4()
        team_id = uuid4()
        upsert_result = _team_workflow_permission(
            organization_id=organization_id,
            workflow_id=workflow_id,
            team_id=team_id,
            auth_state="viewer",
            assigned_by=user_id,
        )
        session = _Session(
            organization=_organization(id=organization_id, created_by=uuid4()),
            membership=_membership(user_id=user_id, organization_id=organization_id),
            workflow=_workflow(id=workflow_id, organization_id=organization_id),
            team=_team(id=team_id, organization_id=organization_id),
            manager_permissions=[],
            user_direct_permissions=[
                _user_workflow_permission(
                    organization_id=organization_id,
                    workflow_id=workflow_id,
                    user_id=user_id,
                    auth_state="manager",
                    assigned_by=uuid4(),
                )
            ],
            upsert_result=upsert_result,
        )

        response = self._put_permission(
            session=session,
            user_id=user_id,
            organization_id=organization_id,
            workflow_id=workflow_id,
            team_id=team_id,
            payload={"auth_state": "viewer"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(session.scalars_called)
        self.assertTrue(session.committed)
        _assert_user_workflow_manage_filters(
            self,
            session.user_workflow_query,
            user_id,
            workflow_id,
            organization_id,
        )

    def test_put_team_llm_permission_creates_row_for_organization_manager(self):
        # organization manager는 credential manage 권한 없이도 team LLM 권한을 부여할 수 있다.
        user_id = uuid4()
        organization_id = uuid4()
        credential_id = uuid4()
        team_id = uuid4()
        upsert_result = _team_llm_permission(
            organization_id=organization_id,
            credential_id=credential_id,
            team_id=team_id,
            auth_state="operator",
            assigned_by=user_id,
        )
        session = _Session(
            organization=_organization(id=organization_id, created_by=user_id),
            credential=_credential(
                id=credential_id,
                organization_id=organization_id,
                user_id=user_id,
            ),
            team=_team(id=team_id, organization_id=organization_id),
            llm_upsert_result=upsert_result,
        )
        response = self._put_llm_permission(
            session=session,
            user_id=user_id,
            organization_id=organization_id,
            credential_id=credential_id,
            team_id=team_id,
            payload={"auth_state": "operator"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json()["grantee_organization_id"], str(organization_id)
        )
        self.assertEqual(response.json()["llm_credential_id"], str(credential_id))
        self.assertEqual(response.json()["team_id"], str(team_id))
        self.assertEqual(response.json()["auth_state"], "operator")
        self.assertEqual(response.json()["assigned_by"], str(user_id))
        _assert_credential_scope_filters(
            self,
            session.credential_query,
            credential_id,
            organization_id,
        )
        _assert_team_scope_filters(self, session.team_query, team_id, organization_id)
        self.assertIn(
            "ON CONFLICT (grantee_organization_id, llm_credential_id, team_id)",
            str(session.upsert_statement.compile(dialect=postgresql.dialect())),
        )
        self.assertTrue(session.committed)
        self.assertTrue(session.scalars_called)
        self.permission_audit.assert_not_called()
        audit = session.added[0]
        self.assertEqual(audit.action, "team_llm_permission.created")
        self.assertEqual(audit.target_type, "team_llm_permission")
        self.assertEqual(audit.target_id, str(upsert_result.id))
        self.assertEqual(audit.after["llm_credential_id"], str(credential_id))

    def test_put_team_llm_permission_allows_credential_manager(self):
        # organization manager가 아니어도 credential manager 권한이 있으면 team LLM 권한을 부여할 수 있다.
        user_id = uuid4()
        organization_id = uuid4()
        credential_id = uuid4()
        team_id = uuid4()
        upsert_result = _team_llm_permission(
            organization_id=organization_id,
            credential_id=credential_id,
            team_id=team_id,
            auth_state="viewer",
            assigned_by=user_id,
        )
        session = _Session(
            organization=_organization(id=organization_id, created_by=uuid4()),
            membership=_membership(user_id=user_id, organization_id=organization_id),
            credential=_credential(
                id=credential_id,
                organization_id=organization_id,
                user_id=uuid4(),
            ),
            team=_team(id=team_id, organization_id=organization_id),
            llm_manager_permissions=[
                _team_llm_permission(
                    organization_id=organization_id,
                    credential_id=credential_id,
                    team_id=uuid4(),
                    auth_state="manager",
                    assigned_by=uuid4(),
                    member_user_id=user_id,
                )
            ],
            llm_upsert_result=upsert_result,
        )

        response = self._put_llm_permission(
            session=session,
            user_id=user_id,
            organization_id=organization_id,
            credential_id=credential_id,
            team_id=team_id,
            payload={"auth_state": "viewer"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(session.committed)
        _assert_llm_manage_filters(
            self,
            session.llm_manager_query,
            user_id,
            credential_id,
            organization_id,
        )

    def test_put_team_llm_permission_rejects_member_without_manage(self):
        # active member라도 credential manager가 아니면 LLM 권한 변경은 거부해야 한다.
        user_id = uuid4()
        organization_id = uuid4()
        credential_id = uuid4()
        team_id = uuid4()
        session = _Session(
            organization=_organization(id=organization_id, created_by=uuid4()),
            membership=_membership(user_id=user_id, organization_id=organization_id),
            credential=_credential(
                id=credential_id,
                organization_id=organization_id,
                user_id=uuid4(),
            ),
            team=_team(id=team_id, organization_id=organization_id),
            llm_manager_permissions=[
                _team_llm_permission(
                    organization_id=organization_id,
                    credential_id=credential_id,
                    team_id=uuid4(),
                    auth_state="builder",
                    assigned_by=uuid4(),
                    member_user_id=user_id,
                )
            ],
        )

        response = self._put_llm_permission(
            session=session,
            user_id=user_id,
            organization_id=organization_id,
            credential_id=credential_id,
            team_id=team_id,
            payload={"auth_state": "viewer"},
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(
            response.json(),
            _error(
                "permission.denied",
                "Credential manage or organization manager permission is required.",
            ),
        )
        self.assertFalse(session.committed)
        self.assertFalse(session.scalars_called)
        self.permission_audit.assert_not_called()

    def test_put_team_llm_permission_hides_organization_outside_user_scope(self):
        # scope 밖 organization은 credential 존재 여부를 노출하지 않도록 404로 숨긴다.
        user_id = uuid4()
        organization_id = uuid4()
        session = _Session(
            organization=_organization(id=organization_id, created_by=uuid4()),
            membership=None,
        )

        response = self._put_llm_permission(
            session=session,
            user_id=user_id,
            organization_id=organization_id,
            credential_id=uuid4(),
            team_id=uuid4(),
            payload={"auth_state": "viewer"},
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(
            response.json(),
            _error("resource.not_found", "Organization not found."),
        )
        _assert_active_membership_filters(
            self,
            session.membership_query,
            user_id,
            organization_id,
        )
        self.assertNotIn(LLMCredential, session.query_calls)
        self.assertFalse(session.committed)
        self.assertFalse(session.scalars_called)

    def test_put_team_llm_permission_requires_organization_header(self):
        # X-Organization-Id가 없으면 organization 조회 전에 400으로 거부한다.
        session = _Session()

        response = self._put_llm_permission(
            session=session,
            user_id=uuid4(),
            organization_id=None,
            credential_id=uuid4(),
            team_id=uuid4(),
            payload={"auth_state": "viewer"},
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.json(),
            _error("organization.required", "X-Organization-Id header is required."),
        )
        self.assertNotIn(Organization, session.query_calls)
        self.assertFalse(session.scalars_called)

    def test_put_team_llm_permission_rejects_malformed_organization_header(self):
        # X-Organization-Id가 UUID가 아니면 scope 조회 전에 validation error로 거부한다.
        session = _Session()

        response = self._put_llm_permission(
            session=session,
            user_id=uuid4(),
            organization_id=None,
            raw_organization_id="not-a-uuid",
            credential_id=uuid4(),
            team_id=uuid4(),
            payload={"auth_state": "viewer"},
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
        self.assertFalse(session.scalars_called)

    def test_put_team_llm_permission_requires_authentication(self):
        # auth_token cookie가 없고 AuthService가 401을 내면 auth.required envelope으로 반환한다.
        session = _Session()

        response = self._put_llm_permission(
            session=session,
            user_id=uuid4(),
            organization_id=uuid4(),
            credential_id=uuid4(),
            team_id=uuid4(),
            payload={"auth_state": "viewer"},
            include_auth_cookie=False,
            auth_side_effect=HTTPException(
                status_code=401,
                detail="로그인이 필요합니다",
            ),
        )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(
            response.json(),
            _error("auth.required", "로그인이 필요합니다"),
        )
        self.assertNotIn(Organization, session.query_calls)
        self.assertFalse(session.scalars_called)

    def test_put_team_llm_permission_rejects_invalid_token(self):
        # auth_token cookie가 있지만 AuthService가 401을 내면 auth.invalid envelope으로 반환한다.
        session = _Session()

        response = self._put_llm_permission(
            session=session,
            user_id=uuid4(),
            organization_id=uuid4(),
            credential_id=uuid4(),
            team_id=uuid4(),
            payload={"auth_state": "viewer"},
            auth_side_effect=HTTPException(
                status_code=401,
                detail="Invalid token",
            ),
        )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(
            response.json(),
            _error("auth.invalid", "Invalid token"),
        )
        self.assertNotIn(Organization, session.query_calls)
        self.assertFalse(session.scalars_called)

    def test_put_team_llm_permission_rejects_invalid_auth_state(self):
        # LLM permission matrix에 없는 auth_state는 DB 조회 전에 request validation에서 막는다.
        session = _Session()

        response = self._put_llm_permission(
            session=session,
            user_id=uuid4(),
            organization_id=uuid4(),
            credential_id=uuid4(),
            team_id=uuid4(),
            payload={"auth_state": "admin"},
        )

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["error"]["code"], "validation.failed")
        self.assertEqual(response.json()["error"]["request_id"], "98d6d88b-8d7a-46fd-8d12-f2024d2fac4b")
        self.assertEqual(
            response.json()["error"]["details"]["errors"][0]["loc"],
            ["body", "auth_state"],
        )
        self.assertNotIn(Organization, session.query_calls)

    def test_put_team_llm_permission_hides_missing_credential(self):
        # credential이 organization scope 안에 없으면 team 조회나 upsert 없이 404로 숨긴다.
        user_id = uuid4()
        organization_id = uuid4()
        credential_id = uuid4()
        session = _Session(
            organization=_organization(id=organization_id, created_by=user_id),
            credential=None,
            team=_team(id=uuid4(), organization_id=organization_id),
        )

        response = self._put_llm_permission(
            session=session,
            user_id=user_id,
            organization_id=organization_id,
            credential_id=credential_id,
            team_id=uuid4(),
            payload={"auth_state": "viewer"},
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(
            response.json(),
            _error("resource.not_found", "LLM credential not found."),
        )
        _assert_credential_scope_filters(
            self,
            session.credential_query,
            credential_id,
            organization_id,
        )
        self.assertNotIn(Team, session.query_calls)
        self.assertFalse(session.scalars_called)

    def test_put_team_llm_permission_hides_invalid_credential(self):
        # is_valid=False credential은 soft-deleted 상태이므로 없는 credential처럼 취급한다.
        user_id = uuid4()
        organization_id = uuid4()
        credential_id = uuid4()
        session = _Session(
            organization=_organization(id=organization_id, created_by=user_id),
            credential=_credential(
                id=credential_id,
                organization_id=organization_id,
                user_id=user_id,
                is_valid=False,
            ),
            team=_team(id=uuid4(), organization_id=organization_id),
        )

        response = self._put_llm_permission(
            session=session,
            user_id=user_id,
            organization_id=organization_id,
            credential_id=credential_id,
            team_id=uuid4(),
            payload={"auth_state": "viewer"},
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(
            response.json(),
            _error("resource.not_found", "LLM credential not found."),
        )
        _assert_credential_scope_filters(
            self,
            session.credential_query,
            credential_id,
            organization_id,
        )
        self.assertNotIn(Team, session.query_calls)
        self.assertFalse(session.scalars_called)

    def test_put_team_llm_permission_hides_missing_team(self):
        # team이 없으면 권한 row를 만들지 않고 404로 응답한다.
        user_id = uuid4()
        organization_id = uuid4()
        credential_id = uuid4()
        team_id = uuid4()
        session = _Session(
            organization=_organization(id=organization_id, created_by=user_id),
            credential=_credential(
                id=credential_id,
                organization_id=organization_id,
                user_id=user_id,
            ),
            team=None,
        )

        response = self._put_llm_permission(
            session=session,
            user_id=user_id,
            organization_id=organization_id,
            credential_id=credential_id,
            team_id=team_id,
            payload={"auth_state": "viewer"},
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(
            response.json(), _error("resource.not_found", "Team not found.")
        )
        _assert_team_scope_filters(self, session.team_query, team_id, organization_id)
        self.assertFalse(session.committed)
        self.assertFalse(session.scalars_called)

    def test_put_team_llm_permission_hides_inactive_team(self):
        # inactive team은 fake query의 Team.is_active 필터 적용으로 실제 404가 되어야 한다.
        user_id = uuid4()
        organization_id = uuid4()
        credential_id = uuid4()
        team_id = uuid4()
        session = _Session(
            organization=_organization(id=organization_id, created_by=user_id),
            credential=_credential(
                id=credential_id,
                organization_id=organization_id,
                user_id=user_id,
            ),
            team=_team(id=team_id, organization_id=organization_id, is_active=False),
        )

        response = self._put_llm_permission(
            session=session,
            user_id=user_id,
            organization_id=organization_id,
            credential_id=credential_id,
            team_id=team_id,
            payload={"auth_state": "viewer"},
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(
            response.json(), _error("resource.not_found", "Team not found.")
        )
        _assert_team_scope_filters(self, session.team_query, team_id, organization_id)
        self.assertFalse(session.committed)
        self.assertFalse(session.scalars_called)

    def test_put_team_llm_permission_noops_same_auth_state(self):
        # 같은 auth_state PUT은 assigned metadata만 바꾸는 update/audit을 만들지 않는다.
        user_id = uuid4()
        organization_id = uuid4()
        credential_id = uuid4()
        team_id = uuid4()
        previous_assigned_by = uuid4()
        existing_permission = _team_llm_permission(
            organization_id=organization_id,
            credential_id=credential_id,
            team_id=team_id,
            auth_state="operator",
            assigned_by=previous_assigned_by,
        )
        session = _Session(
            organization=_organization(id=organization_id, created_by=user_id),
            credential=_credential(
                id=credential_id,
                organization_id=organization_id,
                user_id=user_id,
            ),
            team=_team(id=team_id, organization_id=organization_id),
            existing_llm_permission=existing_permission,
            llm_upsert_result=None,
        )

        response = self._put_llm_permission(
            session=session,
            user_id=user_id,
            organization_id=organization_id,
            credential_id=credential_id,
            team_id=team_id,
            payload={"auth_state": "operator"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["auth_state"], "operator")
        self.assertEqual(response.json()["assigned_by"], str(previous_assigned_by))
        self.assertTrue(session.committed)
        self.permission_audit.assert_not_called()

    def test_put_team_workflow_permission_hides_other_org_membership(self):
        # 다른 organization membership은 active member scope로 인정하지 않는다.
        user_id = uuid4()
        organization_id = uuid4()
        session = _Session(
            organization=_organization(id=organization_id, created_by=uuid4()),
            membership=_membership(user_id=user_id, organization_id=uuid4()),
        )

        response = self._put_permission(
            session=session,
            user_id=user_id,
            organization_id=organization_id,
            workflow_id=uuid4(),
            team_id=uuid4(),
            payload={"auth_state": "viewer"},
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(
            response.json(),
            _error("resource.not_found", "Organization not found."),
        )
        self.assertNotIn(Workflow, session.query_calls)
        self.assertFalse(session.scalars_called)

    def test_put_team_workflow_permission_hides_removed_organization_membership(self):
        # removed organization membership은 organization scope 진입 권한으로 인정하지 않는다.
        user_id = uuid4()
        organization_id = uuid4()
        session = _Session(
            organization=_organization(id=organization_id, created_by=uuid4()),
            membership=_membership(
                user_id=user_id,
                organization_id=organization_id,
                membership_state="removed",
            ),
        )

        response = self._put_permission(
            session=session,
            user_id=user_id,
            organization_id=organization_id,
            workflow_id=uuid4(),
            team_id=uuid4(),
            payload={"auth_state": "viewer"},
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(
            response.json(),
            _error("resource.not_found", "Organization not found."),
        )
        self.assertNotIn(Workflow, session.query_calls)
        self.assertFalse(session.scalars_called)

    def test_put_team_workflow_permission_hides_organization_outside_user_scope(self):
        # scope 밖 organization은 존재 여부를 노출하지 않도록 404로 숨긴다.
        user_id = uuid4()
        organization_id = uuid4()
        session = _Session(
            organization=_organization(id=organization_id, created_by=uuid4()),
            membership=None,
        )

        response = self._put_permission(
            session=session,
            user_id=user_id,
            organization_id=organization_id,
            workflow_id=uuid4(),
            team_id=uuid4(),
            payload={"auth_state": "viewer"},
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(
            response.json(),
            _error("resource.not_found", "Organization not found."),
        )
        _assert_active_membership_filters(
            self,
            session.membership_query,
            user_id,
            organization_id,
        )
        self.assertNotIn(Workflow, session.query_calls)
        self.assertFalse(session.committed)
        self.assertFalse(session.scalars_called)

    def test_put_team_workflow_permission_requires_organization_header(self):
        # X-Organization-Id가 없으면 organization 조회 전에 400으로 거부한다.
        user_id = uuid4()
        session = _Session()

        response = self._put_permission(
            session=session,
            user_id=user_id,
            organization_id=None,
            workflow_id=uuid4(),
            team_id=uuid4(),
            payload={"auth_state": "viewer"},
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.json(),
            _error("organization.required", "X-Organization-Id header is required."),
        )
        self.assertNotIn(Organization, session.query_calls)
        self.assertFalse(session.scalars_called)

    def test_put_team_workflow_permission_rejects_malformed_organization_header(self):
        # X-Organization-Id가 UUID가 아니면 scope 조회 전에 validation error로 거부한다.
        user_id = uuid4()
        session = _Session()

        response = self._put_permission(
            session=session,
            user_id=user_id,
            organization_id=None,
            raw_organization_id="not-a-uuid",
            workflow_id=uuid4(),
            team_id=uuid4(),
            payload={"auth_state": "viewer"},
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
        self.assertFalse(session.scalars_called)

    def test_put_team_workflow_permission_allows_managed_by_manager(self):
        # organization.managed_by도 organization manager로 인정되어 membership 없이 허용된다.
        user_id = uuid4()
        organization_id = uuid4()
        workflow_id = uuid4()
        team_id = uuid4()
        upsert_result = _team_workflow_permission(
            organization_id=organization_id,
            workflow_id=workflow_id,
            team_id=team_id,
            auth_state="operator",
            assigned_by=user_id,
        )
        session = _Session(
            organization=_organization(
                id=organization_id,
                created_by=uuid4(),
                managed_by=user_id,
            ),
            workflow=_workflow(id=workflow_id, organization_id=organization_id),
            team=_team(id=team_id, organization_id=organization_id),
            upsert_result=upsert_result,
        )

        response = self._put_permission(
            session=session,
            user_id=user_id,
            organization_id=organization_id,
            workflow_id=workflow_id,
            team_id=team_id,
            payload={"auth_state": "operator"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["auth_state"], "operator")
        self.assertNotIn(TeamMembership, session.query_calls)
        self.assertTrue(session.scalars_called)
        self.assertTrue(session.committed)

    def test_put_team_workflow_permission_noops_same_auth_state(self):
        # 같은 auth_state PUT은 assigned metadata만 바꾸는 update/audit을 만들지 않는다.
        user_id = uuid4()
        organization_id = uuid4()
        workflow_id = uuid4()
        team_id = uuid4()
        previous_assigned_by = uuid4()
        existing_permission = _team_workflow_permission(
            organization_id=organization_id,
            workflow_id=workflow_id,
            team_id=team_id,
            auth_state="builder",
            assigned_by=previous_assigned_by,
        )
        session = _Session(
            organization=_organization(id=organization_id, created_by=user_id),
            workflow=_workflow(id=workflow_id, organization_id=organization_id),
            team=_team(id=team_id, organization_id=organization_id),
            existing_permission=existing_permission,
            upsert_result=None,
        )

        response = self._put_permission(
            session=session,
            user_id=user_id,
            organization_id=organization_id,
            workflow_id=workflow_id,
            team_id=team_id,
            payload={"auth_state": "builder"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["auth_state"], "builder")
        self.assertEqual(response.json()["assigned_by"], str(previous_assigned_by))
        self.assertTrue(session.committed)
        self.permission_audit.assert_not_called()

    def test_put_team_workflow_permission_hides_missing_workflow(self):
        # workflow가 active organization scope 안에 없으면 team 조회나 upsert 없이 404로 숨긴다.
        user_id = uuid4()
        organization_id = uuid4()
        workflow_id = uuid4()
        session = _Session(
            organization=_organization(id=organization_id, created_by=user_id),
            workflow=None,
            team=_team(id=uuid4(), organization_id=organization_id),
        )

        response = self._put_permission(
            session=session,
            user_id=user_id,
            organization_id=organization_id,
            workflow_id=workflow_id,
            team_id=uuid4(),
            payload={"auth_state": "viewer"},
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(
            response.json(),
            _error("resource.not_found", "Workflow not found."),
        )
        _assert_workflow_scope_filters(
            self,
            session.workflow_query,
            workflow_id,
            organization_id,
        )
        self.assertNotIn(Team, session.query_calls)
        self.assertFalse(session.scalars_called)

    def test_put_team_workflow_permission_requires_authentication(self):
        # auth_token cookie가 없고 AuthService가 401을 내면 auth.required envelope으로 반환한다.
        session = _Session()

        response = self._put_permission(
            session=session,
            user_id=uuid4(),
            organization_id=uuid4(),
            workflow_id=uuid4(),
            team_id=uuid4(),
            payload={"auth_state": "viewer"},
            include_auth_cookie=False,
            auth_side_effect=HTTPException(
                status_code=401,
                detail="로그인이 필요합니다",
            ),
        )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(
            response.json(),
            _error("auth.required", "로그인이 필요합니다"),
        )
        self.assertNotIn(Organization, session.query_calls)
        self.assertFalse(session.scalars_called)

    def test_put_team_workflow_permission_rejects_invalid_token(self):
        # auth_token cookie가 있지만 AuthService가 401을 내면 auth.invalid envelope으로 반환한다.
        session = _Session()

        response = self._put_permission(
            session=session,
            user_id=uuid4(),
            organization_id=uuid4(),
            workflow_id=uuid4(),
            team_id=uuid4(),
            payload={"auth_state": "viewer"},
            auth_side_effect=HTTPException(
                status_code=401,
                detail="Invalid token",
            ),
        )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(
            response.json(),
            _error("auth.invalid", "Invalid token"),
        )
        self.assertNotIn(Organization, session.query_calls)
        self.assertFalse(session.scalars_called)

    def test_put_team_workflow_permission_rejects_invalid_auth_state(self):
        # workflow matrix에 없는 auth_state는 DB 조회 전에 request validation에서 막는다.
        user_id = uuid4()
        organization_id = uuid4()
        session = _Session()

        response = self._put_permission(
            session=session,
            user_id=user_id,
            organization_id=organization_id,
            workflow_id=uuid4(),
            team_id=uuid4(),
            payload={"auth_state": "admin"},
        )

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["error"]["code"], "validation.failed")
        self.assertEqual(response.json()["error"]["request_id"], "98d6d88b-8d7a-46fd-8d12-f2024d2fac4b")
        self.assertEqual(
            response.json()["error"]["details"]["errors"][0]["loc"],
            ["body", "auth_state"],
        )
        self.assertNotIn(Organization, session.query_calls)

    def test_put_team_workflow_permission_hides_missing_team(self):
        # team이 없으면 권한 row를 만들지 않고 404로 응답한다.
        user_id = uuid4()
        organization_id = uuid4()
        workflow_id = uuid4()
        team_id = uuid4()
        session = _Session(
            organization=_organization(id=organization_id, created_by=user_id),
            workflow=_workflow(id=workflow_id, organization_id=organization_id),
            team=None,
        )

        response = self._put_permission(
            session=session,
            user_id=user_id,
            organization_id=organization_id,
            workflow_id=workflow_id,
            team_id=team_id,
            payload={"auth_state": "viewer"},
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(
            response.json(), _error("resource.not_found", "Team not found.")
        )
        _assert_team_scope_filters(self, session.team_query, team_id, organization_id)
        self.assertFalse(session.committed)
        self.assertFalse(session.scalars_called)

    def test_put_team_workflow_permission_hides_inactive_team(self):
        # inactive team은 fake query의 Team.is_active 필터 적용으로 실제 404가 되어야 한다.
        user_id = uuid4()
        organization_id = uuid4()
        workflow_id = uuid4()
        team_id = uuid4()
        session = _Session(
            organization=_organization(id=organization_id, created_by=user_id),
            workflow=_workflow(id=workflow_id, organization_id=organization_id),
            team=_team(id=team_id, organization_id=organization_id, is_active=False),
        )

        response = self._put_permission(
            session=session,
            user_id=user_id,
            organization_id=organization_id,
            workflow_id=workflow_id,
            team_id=team_id,
            payload={"auth_state": "viewer"},
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(
            response.json(), _error("resource.not_found", "Team not found.")
        )
        _assert_team_scope_filters(self, session.team_query, team_id, organization_id)
        self.assertFalse(session.committed)
        self.assertFalse(session.scalars_called)

    def test_put_user_workflow_permission_creates_row_for_organization_manager(self):
        # organization manager는 user direct workflow 권한을 부여할 수 있다.
        actor_id = uuid4()
        target_user_id = uuid4()
        organization_id = uuid4()
        workflow_id = uuid4()
        upsert_result = _user_workflow_permission(
            organization_id=organization_id,
            workflow_id=workflow_id,
            user_id=target_user_id,
            auth_state="builder",
            assigned_by=actor_id,
        )
        session = _Session(
            organization=_organization(
                id=organization_id,
                created_by=target_user_id,
                managed_by=actor_id,
            ),
            workflow=_workflow(id=workflow_id, organization_id=organization_id),
            target_user=SimpleNamespace(id=target_user_id),
            target_membership=_membership(
                user_id=target_user_id,
                organization_id=organization_id,
            ),
            user_upsert_result=upsert_result,
        )

        response = self._put_user_permission(
            session=session,
            user_id=actor_id,
            organization_id=organization_id,
            workflow_id=workflow_id,
            target_user_id=target_user_id,
            payload={"auth_state": "builder"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json()["grantee_organization_id"], str(organization_id)
        )
        self.assertEqual(response.json()["workflow_id"], str(workflow_id))
        self.assertEqual(response.json()["user_id"], str(target_user_id))
        self.assertEqual(response.json()["auth_state"], "builder")
        self.assertEqual(response.json()["assigned_by"], str(actor_id))
        self.assertIn(
            "ON CONFLICT (grantee_organization_id, user_id, workflow_id)",
            str(session.upsert_statement.compile(dialect=postgresql.dialect())),
        )
        self.assertTrue(session.committed)
        self.assertIsNotNone(session.lock_statement)
        self.permission_audit.assert_not_called()
        audit = _single_added_audit(session)
        self.assertEqual(audit.action, "user_workflow_permission.created")
        self.assertEqual(audit.target_type, "user_workflow_permission")
        self.assertEqual(audit.target_id, str(upsert_result.id))
        self.assertEqual(audit.after["user_id"], str(target_user_id))
        self.assertEqual(audit.after["auth_state"], "builder")
        self.assertEqual(
            audit.audit_metadata["organization_id"], str(organization_id)
        )
        _assert_audit_added_before_commit(self, session)

    def test_put_user_workflow_permission_rolls_back_when_audit_add_fails(self):
        actor_id = uuid4()
        target_user_id = uuid4()
        organization_id = uuid4()
        workflow_id = uuid4()
        session = _Session(
            organization=_organization(
                id=organization_id,
                created_by=target_user_id,
                managed_by=actor_id,
            ),
            workflow=_workflow(id=workflow_id, organization_id=organization_id),
            target_user=SimpleNamespace(id=target_user_id),
            target_membership=_membership(
                user_id=target_user_id,
                organization_id=organization_id,
            ),
            user_upsert_result=_user_workflow_permission(
                organization_id=organization_id,
                workflow_id=workflow_id,
                user_id=target_user_id,
                auth_state="builder",
                assigned_by=actor_id,
            ),
        )
        session.audit_add_error = RuntimeError("audit unavailable")

        response = self._put_user_permission(
            session=session,
            user_id=actor_id,
            organization_id=organization_id,
            workflow_id=workflow_id,
            target_user_id=target_user_id,
            payload={"auth_state": "builder"},
        )

        self.assertEqual(response.status_code, 500)
        self.assertEqual(
            response.json(),
            _error(
                "audit.persistence_failed",
                "The required audit record could not be persisted.",
            ),
        )
        self.assertFalse(session.committed)
        self.assertIn(("rollback", None), session.operations)

    def test_put_user_workflow_permission_locks_subject_before_app_scope(self):
        actor_id = uuid4()
        target_user_id = uuid4()
        organization_id = uuid4()
        workflow_id = uuid4()
        session = _Session(
            organization=_organization(
                id=organization_id,
                created_by=target_user_id,
                managed_by=actor_id,
            ),
            workflow=_workflow(id=workflow_id, organization_id=organization_id),
            target_user=SimpleNamespace(id=target_user_id),
            target_membership=_membership(
                user_id=target_user_id,
                organization_id=organization_id,
            ),
            user_upsert_result=_user_workflow_permission(
                organization_id=organization_id,
                workflow_id=workflow_id,
                user_id=target_user_id,
                auth_state="builder",
                assigned_by=actor_id,
            ),
        )
        lock_order = []

        with patch(
            "apps.gateway.api.v1.endpoints.permissions."
            "_lock_active_direct_permission_subject",
            side_effect=lambda *args, **kwargs: lock_order.append("subject"),
        ), patch(
            "apps.gateway.api.v1.endpoints.permissions."
            "_lock_workflow_mutation_app_scope",
            side_effect=lambda *args, **kwargs: lock_order.append("app"),
        ):
            response = self._put_user_permission(
                session=session,
                user_id=actor_id,
                organization_id=organization_id,
                workflow_id=workflow_id,
                target_user_id=target_user_id,
                payload={"auth_state": "builder"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(lock_order, ["subject", "app"])

    def test_put_user_workflow_permission_allows_workflow_manager(self):
        # organization manager가 아니어도 workflow manager면 user direct 권한을 부여할 수 있다.
        actor_id = uuid4()
        target_user_id = uuid4()
        organization_id = uuid4()
        workflow_id = uuid4()
        upsert_result = _user_workflow_permission(
            organization_id=organization_id,
            workflow_id=workflow_id,
            user_id=target_user_id,
            auth_state="viewer",
            assigned_by=actor_id,
        )
        session = _Session(
            organization=_organization(id=organization_id, created_by=uuid4()),
            membership=_membership(user_id=actor_id, organization_id=organization_id),
            target_membership=_membership(
                user_id=target_user_id,
                organization_id=organization_id,
            ),
            workflow=_workflow(id=workflow_id, organization_id=organization_id),
            target_user=SimpleNamespace(id=target_user_id),
            manager_permissions=[
                _team_workflow_permission(
                    organization_id=organization_id,
                    workflow_id=workflow_id,
                    team_id=uuid4(),
                    auth_state="manager",
                    assigned_by=uuid4(),
                    member_user_id=actor_id,
                )
            ],
            user_upsert_result=upsert_result,
        )

        response = self._put_user_permission(
            session=session,
            user_id=actor_id,
            organization_id=organization_id,
            workflow_id=workflow_id,
            target_user_id=target_user_id,
            payload={"auth_state": "viewer"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["user_id"], str(target_user_id))
        self.assertTrue(session.committed)
        _assert_workflow_manage_filters(
            self,
            session.workflow_manager_query,
            actor_id,
            workflow_id,
            organization_id,
        )

    def test_put_user_workflow_permission_hides_target_user_outside_scope(self):
        # 대상 user가 organization active membership 밖이면 direct permission을 만들지 않는다.
        actor_id = uuid4()
        target_user_id = uuid4()
        organization_id = uuid4()
        workflow_id = uuid4()
        session = _Session(
            organization=_organization(id=organization_id, created_by=actor_id),
            workflow=_workflow(id=workflow_id, organization_id=organization_id),
            target_user=SimpleNamespace(id=target_user_id),
            target_membership=None,
        )

        response = self._put_user_permission(
            session=session,
            user_id=actor_id,
            organization_id=organization_id,
            workflow_id=workflow_id,
            target_user_id=target_user_id,
            payload={"auth_state": "viewer"},
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(
            response.json(), _error("resource.not_found", "User not found.")
        )
        self.assertFalse(session.committed)
        self.assertFalse(session.scalars_called)

    def test_put_user_workflow_permission_rejects_deactivated_target_user(self):
        # 비활성 user에게는 direct workflow permission을 만들지 않는다.
        actor_id = uuid4()
        target_user_id = uuid4()
        organization_id = uuid4()
        workflow_id = uuid4()
        session = _Session(
            organization=_organization(id=organization_id, created_by=actor_id),
            workflow=_workflow(id=workflow_id, organization_id=organization_id),
            target_user=SimpleNamespace(
                id=target_user_id,
                deactivated_at=datetime.now(timezone.utc),
            ),
            target_membership=_membership(
                user_id=target_user_id,
                organization_id=organization_id,
            ),
        )

        response = self._put_user_permission(
            session=session,
            user_id=actor_id,
            organization_id=organization_id,
            workflow_id=workflow_id,
            target_user_id=target_user_id,
            payload={"auth_state": "viewer"},
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(
            response.json(), _error("resource.not_found", "User not found.")
        )
        self.assertFalse(session.committed)
        self.assertFalse(session.scalars_called)

    def test_put_user_llm_permission_creates_row_for_organization_manager(self):
        # organization manager는 user direct LLM credential 권한을 부여할 수 있다.
        actor_id = uuid4()
        target_user_id = uuid4()
        organization_id = uuid4()
        credential_id = uuid4()
        upsert_result = _user_llm_permission(
            organization_id=organization_id,
            credential_id=credential_id,
            user_id=target_user_id,
            auth_state="operator",
            assigned_by=actor_id,
        )
        session = _Session(
            organization=_organization(id=organization_id, created_by=actor_id),
            credential=_credential(
                id=credential_id,
                organization_id=organization_id,
                user_id=actor_id,
            ),
            target_user=SimpleNamespace(id=target_user_id),
            target_membership=_membership(
                user_id=target_user_id,
                organization_id=organization_id,
            ),
            user_llm_upsert_result=upsert_result,
        )

        response = self._put_user_llm_permission(
            session=session,
            user_id=actor_id,
            organization_id=organization_id,
            credential_id=credential_id,
            target_user_id=target_user_id,
            payload={"auth_state": "operator"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json()["grantee_organization_id"], str(organization_id)
        )
        self.assertEqual(response.json()["llm_credential_id"], str(credential_id))
        self.assertEqual(response.json()["user_id"], str(target_user_id))
        self.assertEqual(response.json()["auth_state"], "operator")
        self.assertEqual(response.json()["assigned_by"], str(actor_id))
        self.assertIn(
            "ON CONFLICT (grantee_organization_id, user_id, llm_credential_id)",
            str(session.upsert_statement.compile(dialect=postgresql.dialect())),
        )
        self.assertTrue(session.committed)
        self.assertIsNotNone(session.lock_statement)
        self.permission_audit.assert_not_called()
        audit = _single_added_audit(session)
        self.assertEqual(audit.action, "user_llm_permission.created")
        self.assertEqual(audit.target_type, "user_llm_permission")
        self.assertEqual(audit.target_id, str(upsert_result.id))
        self.assertEqual(audit.after["user_id"], str(target_user_id))
        self.assertEqual(audit.after["auth_state"], "operator")
        self.assertEqual(
            audit.audit_metadata["organization_id"], str(organization_id)
        )
        _assert_audit_added_before_commit(self, session)

    def test_put_user_llm_permission_allows_credential_manager(self):
        # organization manager가 아니어도 credential manager면 user direct 권한을 부여할 수 있다.
        actor_id = uuid4()
        target_user_id = uuid4()
        organization_id = uuid4()
        credential_id = uuid4()
        upsert_result = _user_llm_permission(
            organization_id=organization_id,
            credential_id=credential_id,
            user_id=target_user_id,
            auth_state="viewer",
            assigned_by=actor_id,
        )
        session = _Session(
            organization=_organization(id=organization_id, created_by=uuid4()),
            membership=_membership(user_id=actor_id, organization_id=organization_id),
            credential=_credential(
                id=credential_id,
                organization_id=organization_id,
                user_id=uuid4(),
            ),
            target_user=SimpleNamespace(id=target_user_id),
            target_membership=_membership(
                user_id=target_user_id,
                organization_id=organization_id,
            ),
            llm_manager_permissions=[
                _team_llm_permission(
                    organization_id=organization_id,
                    credential_id=credential_id,
                    team_id=uuid4(),
                    auth_state="manager",
                    assigned_by=uuid4(),
                    member_user_id=actor_id,
                )
            ],
            user_llm_upsert_result=upsert_result,
        )

        response = self._put_user_llm_permission(
            session=session,
            user_id=actor_id,
            organization_id=organization_id,
            credential_id=credential_id,
            target_user_id=target_user_id,
            payload={"auth_state": "viewer"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["user_id"], str(target_user_id))
        self.assertTrue(session.committed)
        _assert_llm_manage_filters(
            self,
            session.llm_manager_query,
            actor_id,
            credential_id,
            organization_id,
        )

    def test_put_user_llm_permission_rejects_member_without_manage(self):
        # active member라도 credential manager가 아니면 user direct LLM 권한 변경은 거부해야 한다.
        actor_id = uuid4()
        target_user_id = uuid4()
        organization_id = uuid4()
        credential_id = uuid4()
        session = _Session(
            organization=_organization(id=organization_id, created_by=uuid4()),
            membership=_membership(user_id=actor_id, organization_id=organization_id),
            credential=_credential(
                id=credential_id,
                organization_id=organization_id,
                user_id=uuid4(),
            ),
            target_user=SimpleNamespace(id=target_user_id),
            target_membership=_membership(
                user_id=target_user_id,
                organization_id=organization_id,
            ),
            llm_manager_permissions=[
                _team_llm_permission(
                    organization_id=organization_id,
                    credential_id=credential_id,
                    team_id=uuid4(),
                    auth_state="builder",
                    assigned_by=uuid4(),
                    member_user_id=actor_id,
                )
            ],
        )

        response = self._put_user_llm_permission(
            session=session,
            user_id=actor_id,
            organization_id=organization_id,
            credential_id=credential_id,
            target_user_id=target_user_id,
            payload={"auth_state": "viewer"},
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(
            response.json(),
            _error(
                "permission.denied",
                "Credential manage or organization manager permission is required.",
            ),
        )
        self.assertFalse(session.committed)
        self.assertFalse(session.scalars_called)
        self.permission_audit.assert_not_called()

    def test_delete_team_workflow_permission_deletes_row_for_organization_manager(self):
        # organization manager는 team workflow permission을 회수할 수 있다.
        user_id = uuid4()
        organization_id = uuid4()
        workflow_id = uuid4()
        team_id = uuid4()
        existing_permission = _team_workflow_permission(
            organization_id=organization_id,
            workflow_id=workflow_id,
            team_id=team_id,
            auth_state="builder",
            assigned_by=uuid4(),
        )
        session = _Session(
            organization=_organization(id=organization_id, created_by=user_id),
            workflow=_workflow(id=workflow_id, organization_id=organization_id),
            team=_team(id=team_id, organization_id=organization_id),
            existing_permission=existing_permission,
        )
        response = self._delete_permission(
            session=session,
            user_id=user_id,
            organization_id=organization_id,
            workflow_id=workflow_id,
            team_id=team_id,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "message": "Team workflow permission deleted",
                "id": str(existing_permission.id),
            },
        )
        self.assertEqual(session.deleted, [existing_permission])
        self.assertTrue(is_manually_audited(session, existing_permission, "deleted"))
        self.assertTrue(session.committed)
        self.assertFalse(session.scalars_called)
        self.assertEqual(len(session.lock_statements), 2)
        scope_lock = str(
            session.lock_statements[0].compile(
                dialect=postgresql.dialect(),
                compile_kwargs={"literal_binds": True},
            )
        )
        self.assertIn("workflow_permission_scope", scope_lock)
        self.assertNotIn(TeamMembership, session.query_calls)
        _assert_organization_scope_filters(
            self, session.organization_query, organization_id
        )
        _assert_workflow_scope_filters(
            self,
            session.workflow_query,
            workflow_id,
            organization_id,
        )
        _assert_team_scope_filters(self, session.team_query, team_id, organization_id)
        self.permission_audit.assert_not_called()
        audit = session.added[0]
        self.assertEqual(audit.action, "team_workflow_permission.deleted")
        self.assertEqual(audit.target_id, str(existing_permission.id))
        self.assertEqual(audit.before["auth_state"], "builder")
        self.assertEqual(audit.before["workflow_id"], str(workflow_id))
        self.assertEqual(audit.before["team_id"], str(team_id))
        self.assertIsNone(audit.after)
        self.assertEqual(audit.audit_metadata["request_id"], "98d6d88b-8d7a-46fd-8d12-f2024d2fac4b")
        self.assertEqual(audit.audit_metadata["actor"]["id"], str(user_id))

    def test_delete_team_workflow_permission_allows_workflow_manager(self):
        # organization manager가 아니어도 workflow manager면 permission 회수가 가능하다.
        user_id = uuid4()
        organization_id = uuid4()
        workflow_id = uuid4()
        team_id = uuid4()
        existing_permission = _team_workflow_permission(
            organization_id=organization_id,
            workflow_id=workflow_id,
            team_id=team_id,
            auth_state="viewer",
            assigned_by=uuid4(),
        )
        session = _Session(
            organization=_organization(id=organization_id, created_by=uuid4()),
            membership=_membership(user_id=user_id, organization_id=organization_id),
            workflow=_workflow(id=workflow_id, organization_id=organization_id),
            team=_team(id=team_id, organization_id=organization_id),
            manager_permissions=[
                _team_workflow_permission(
                    organization_id=organization_id,
                    workflow_id=workflow_id,
                    team_id=uuid4(),
                    auth_state="manager",
                    assigned_by=uuid4(),
                    member_user_id=user_id,
                )
            ],
            existing_permission=existing_permission,
        )

        response = self._delete_permission(
            session=session,
            user_id=user_id,
            organization_id=organization_id,
            workflow_id=workflow_id,
            team_id=team_id,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(session.deleted, [existing_permission])
        self.assertTrue(session.committed)
        _assert_active_membership_filters(
            self,
            session.membership_query,
            user_id,
            organization_id,
        )
        _assert_workflow_manage_filters(
            self,
            session.workflow_manager_query,
            user_id,
            workflow_id,
            organization_id,
        )

    def test_delete_team_workflow_permission_rejects_member_without_manage(self):
        # active member라도 workflow manager가 아니면 permission 회수는 거부해야 한다.
        user_id = uuid4()
        organization_id = uuid4()
        workflow_id = uuid4()
        team_id = uuid4()
        existing_permission = _team_workflow_permission(
            organization_id=organization_id,
            workflow_id=workflow_id,
            team_id=team_id,
            auth_state="viewer",
            assigned_by=uuid4(),
        )
        session = _Session(
            organization=_organization(id=organization_id, created_by=uuid4()),
            membership=_membership(user_id=user_id, organization_id=organization_id),
            workflow=_workflow(id=workflow_id, organization_id=organization_id),
            team=_team(id=team_id, organization_id=organization_id),
            manager_permissions=[
                _team_workflow_permission(
                    organization_id=organization_id,
                    workflow_id=workflow_id,
                    team_id=uuid4(),
                    auth_state="builder",
                    assigned_by=uuid4(),
                    member_user_id=user_id,
                )
            ],
            existing_permission=existing_permission,
        )

        response = self._delete_permission(
            session=session,
            user_id=user_id,
            organization_id=organization_id,
            workflow_id=workflow_id,
            team_id=team_id,
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(
            response.json(),
            _error(
                "permission.denied",
                "Workflow manage or organization manager permission is required.",
            ),
        )
        self.assertEqual(session.deleted, [])
        self.assertFalse(session.committed)
        self.permission_audit.assert_not_called()

    def test_delete_team_workflow_permission_hides_missing_permission_row(self):
        # scope와 권한이 맞아도 회수할 permission row가 없으면 404를 반환한다.
        user_id = uuid4()
        organization_id = uuid4()
        workflow_id = uuid4()
        team_id = uuid4()
        session = _Session(
            organization=_organization(id=organization_id, created_by=user_id),
            workflow=_workflow(id=workflow_id, organization_id=organization_id),
            team=_team(id=team_id, organization_id=organization_id),
            existing_permission=None,
        )

        response = self._delete_permission(
            session=session,
            user_id=user_id,
            organization_id=organization_id,
            workflow_id=workflow_id,
            team_id=team_id,
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(
            response.json(),
            _error(
                "resource.not_found",
                "Team workflow permission not found.",
            ),
        )
        self.assertEqual(session.deleted, [])
        self.assertFalse(session.committed)
        self.permission_audit.assert_not_called()

    def test_delete_team_llm_permission_deletes_row_for_organization_manager(self):
        # organization manager는 team LLM credential permission을 회수할 수 있다.
        user_id = uuid4()
        organization_id = uuid4()
        credential_id = uuid4()
        team_id = uuid4()
        existing_permission = _team_llm_permission(
            organization_id=organization_id,
            credential_id=credential_id,
            team_id=team_id,
            auth_state="operator",
            assigned_by=uuid4(),
        )
        session = _Session(
            organization=_organization(id=organization_id, created_by=user_id),
            credential=_credential(
                id=credential_id,
                organization_id=organization_id,
                user_id=uuid4(),
            ),
            team=_team(id=team_id, organization_id=organization_id),
            existing_llm_permission=existing_permission,
        )
        response = self._delete_llm_permission(
            session=session,
            user_id=user_id,
            organization_id=organization_id,
            credential_id=credential_id,
            team_id=team_id,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "message": "Team LLM credential permission deleted",
                "id": str(existing_permission.id),
            },
        )
        self.assertEqual(session.deleted, [existing_permission])
        self.assertTrue(is_manually_audited(session, existing_permission, "deleted"))
        self.assertTrue(session.committed)
        self.assertFalse(session.scalars_called)
        self.assertNotIn(TeamMembership, session.query_calls)
        self.assertIsNotNone(session.lock_statement)
        self.assertIn(
            "pg_advisory_xact_lock",
            str(session.lock_statement.compile(dialect=postgresql.dialect())),
        )
        _assert_credential_scope_filters(
            self,
            session.credential_query,
            credential_id,
            organization_id,
        )
        _assert_team_scope_filters(self, session.team_query, team_id, organization_id)
        self.permission_audit.assert_not_called()
        audit = session.added[0]
        self.assertEqual(audit.action, "team_llm_permission.deleted")
        self.assertEqual(audit.target_id, str(existing_permission.id))
        self.assertEqual(audit.before["llm_credential_id"], str(credential_id))
        self.assertEqual(audit.before["team_id"], str(team_id))
        self.assertEqual(audit.before["auth_state"], "operator")
        self.assertIsNone(audit.after)
        self.assertEqual(audit.audit_metadata["request_id"], "98d6d88b-8d7a-46fd-8d12-f2024d2fac4b")
        self.assertEqual(audit.audit_metadata["actor"]["id"], str(user_id))

    def test_delete_team_llm_permission_allows_credential_manager(self):
        # organization manager가 아니어도 credential manager면 permission 회수가 가능하다.
        user_id = uuid4()
        organization_id = uuid4()
        credential_id = uuid4()
        team_id = uuid4()
        existing_permission = _team_llm_permission(
            organization_id=organization_id,
            credential_id=credential_id,
            team_id=team_id,
            auth_state="viewer",
            assigned_by=uuid4(),
        )
        session = _Session(
            organization=_organization(id=organization_id, created_by=uuid4()),
            membership=_membership(user_id=user_id, organization_id=organization_id),
            credential=_credential(
                id=credential_id,
                organization_id=organization_id,
                user_id=uuid4(),
            ),
            team=_team(id=team_id, organization_id=organization_id),
            llm_manager_permissions=[
                _team_llm_permission(
                    organization_id=organization_id,
                    credential_id=credential_id,
                    team_id=uuid4(),
                    auth_state="manager",
                    assigned_by=uuid4(),
                    member_user_id=user_id,
                )
            ],
            existing_llm_permission=existing_permission,
        )

        response = self._delete_llm_permission(
            session=session,
            user_id=user_id,
            organization_id=organization_id,
            credential_id=credential_id,
            team_id=team_id,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(session.deleted, [existing_permission])
        self.assertTrue(session.committed)
        _assert_llm_manage_filters(
            self,
            session.llm_manager_query,
            user_id,
            credential_id,
            organization_id,
        )

    def test_delete_team_llm_permission_rejects_member_without_manage(self):
        # active member라도 credential manager가 아니면 permission 회수는 거부해야 한다.
        user_id = uuid4()
        organization_id = uuid4()
        credential_id = uuid4()
        team_id = uuid4()
        existing_permission = _team_llm_permission(
            organization_id=organization_id,
            credential_id=credential_id,
            team_id=team_id,
            auth_state="viewer",
            assigned_by=uuid4(),
        )
        session = _Session(
            organization=_organization(id=organization_id, created_by=uuid4()),
            membership=_membership(user_id=user_id, organization_id=organization_id),
            credential=_credential(
                id=credential_id,
                organization_id=organization_id,
                user_id=uuid4(),
            ),
            team=_team(id=team_id, organization_id=organization_id),
            llm_manager_permissions=[
                _team_llm_permission(
                    organization_id=organization_id,
                    credential_id=credential_id,
                    team_id=uuid4(),
                    auth_state="builder",
                    assigned_by=uuid4(),
                    member_user_id=user_id,
                )
            ],
            existing_llm_permission=existing_permission,
        )

        response = self._delete_llm_permission(
            session=session,
            user_id=user_id,
            organization_id=organization_id,
            credential_id=credential_id,
            team_id=team_id,
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(
            response.json(),
            _error(
                "permission.denied",
                "Credential manage or organization manager permission is required.",
            ),
        )
        self.assertEqual(session.deleted, [])
        self.assertFalse(session.committed)
        self.permission_audit.assert_not_called()

    def test_delete_team_llm_permission_hides_missing_permission_row(self):
        # scope와 권한이 맞아도 회수할 team LLM permission row가 없으면 404를 반환한다.
        user_id = uuid4()
        organization_id = uuid4()
        credential_id = uuid4()
        team_id = uuid4()
        session = _Session(
            organization=_organization(id=organization_id, created_by=user_id),
            credential=_credential(
                id=credential_id,
                organization_id=organization_id,
                user_id=uuid4(),
            ),
            team=_team(id=team_id, organization_id=organization_id),
            existing_llm_permission=None,
        )

        response = self._delete_llm_permission(
            session=session,
            user_id=user_id,
            organization_id=organization_id,
            credential_id=credential_id,
            team_id=team_id,
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(
            response.json(),
            _error(
                "resource.not_found",
                "Team LLM credential permission not found.",
            ),
        )
        self.assertEqual(session.deleted, [])
        self.assertFalse(session.committed)
        self.assertIsNotNone(session.lock_statement)
        self.permission_audit.assert_not_called()

    def test_delete_user_workflow_permission_deletes_row_for_organization_manager(self):
        # organization manager는 user direct workflow permission을 회수할 수 있다.
        actor_id = uuid4()
        target_user_id = uuid4()
        organization_id = uuid4()
        workflow_id = uuid4()
        existing_permission = _user_workflow_permission(
            organization_id=organization_id,
            workflow_id=workflow_id,
            user_id=target_user_id,
            auth_state="builder",
            assigned_by=uuid4(),
        )
        session = _Session(
            organization=_organization(id=organization_id, created_by=actor_id),
            workflow=_workflow(id=workflow_id, organization_id=organization_id),
            target_user=SimpleNamespace(id=target_user_id),
            target_membership=_membership(
                user_id=target_user_id,
                organization_id=organization_id,
            ),
            existing_user_permission=existing_permission,
        )

        response = self._delete_user_permission(
            session=session,
            user_id=actor_id,
            organization_id=organization_id,
            workflow_id=workflow_id,
            target_user_id=target_user_id,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "message": "User workflow permission deleted",
                "id": str(existing_permission.id),
            },
        )
        self.assertEqual(session.deleted, [existing_permission])
        self.assertTrue(is_manually_audited(session, existing_permission, "deleted"))
        self.assertTrue(session.committed)
        self.assertFalse(session.scalars_called)
        scope_locks = [
            str(
                statement.compile(
                    dialect=postgresql.dialect(),
                    compile_kwargs={"literal_binds": True},
                )
            )
            for statement in session.lock_statements
        ]
        self.assertTrue(
            any("workflow_permission_scope" in lock for lock in scope_locks)
        )
        self.assertIsNotNone(session.lock_statement)
        self.assertIn(
            "pg_advisory_xact_lock",
            str(session.lock_statement.compile(dialect=postgresql.dialect())),
        )
        self.permission_audit.assert_not_called()
        audit = _single_added_audit(session)
        self.assertEqual(audit.action, "user_workflow_permission.deleted")
        self.assertEqual(audit.category, "data_change")
        self.assertEqual(audit.actor_id, actor_id)
        self.assertEqual(audit.target_type, "user_workflow_permission")
        self.assertEqual(audit.target_id, str(existing_permission.id))
        self.assertEqual(audit.before["workflow_id"], str(workflow_id))
        self.assertEqual(audit.before["user_id"], str(target_user_id))
        self.assertEqual(audit.before["auth_state"], "builder")
        self.assertIsNone(audit.after)
        self.assertEqual(
            audit.audit_metadata["organization_id"], str(organization_id)
        )
        self.assertEqual(audit.audit_metadata["request_id"], "98d6d88b-8d7a-46fd-8d12-f2024d2fac4b")
        _assert_audit_added_before_commit(self, session)

    def test_delete_user_workflow_permission_locks_subject_before_app_scope(self):
        actor_id = uuid4()
        target_user_id = uuid4()
        organization_id = uuid4()
        workflow_id = uuid4()
        existing_permission = _user_workflow_permission(
            organization_id=organization_id,
            workflow_id=workflow_id,
            user_id=target_user_id,
            auth_state="builder",
            assigned_by=actor_id,
        )
        session = _Session(
            organization=_organization(id=organization_id, created_by=actor_id),
            workflow=_workflow(id=workflow_id, organization_id=organization_id),
            target_user=SimpleNamespace(id=target_user_id),
            target_membership=_membership(
                user_id=target_user_id,
                organization_id=organization_id,
            ),
            existing_user_permission=existing_permission,
        )
        lock_order = []

        with patch(
            "apps.gateway.api.v1.endpoints.permissions."
            "_lock_direct_permission_cleanup_subject",
            side_effect=lambda *args, **kwargs: lock_order.append("subject"),
        ), patch(
            "apps.gateway.api.v1.endpoints.permissions."
            "_lock_workflow_mutation_app_scope",
            side_effect=lambda *args, **kwargs: lock_order.append("app"),
        ):
            response = self._delete_user_permission(
                session=session,
                user_id=actor_id,
                organization_id=organization_id,
                workflow_id=workflow_id,
                target_user_id=target_user_id,
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(lock_order, ["subject", "app"])

    def test_delete_user_workflow_permission_allows_workflow_manager(self):
        # organization manager가 아니어도 workflow manager면 user direct permission 회수가 가능하다.
        actor_id = uuid4()
        target_user_id = uuid4()
        organization_id = uuid4()
        workflow_id = uuid4()
        existing_permission = _user_workflow_permission(
            organization_id=organization_id,
            workflow_id=workflow_id,
            user_id=target_user_id,
            auth_state="viewer",
            assigned_by=uuid4(),
        )
        session = _Session(
            organization=_organization(id=organization_id, created_by=uuid4()),
            membership=_membership(user_id=actor_id, organization_id=organization_id),
            target_membership=_membership(
                user_id=target_user_id,
                organization_id=organization_id,
            ),
            workflow=_workflow(id=workflow_id, organization_id=organization_id),
            target_user=SimpleNamespace(id=target_user_id),
            manager_permissions=[
                _team_workflow_permission(
                    organization_id=organization_id,
                    workflow_id=workflow_id,
                    team_id=uuid4(),
                    auth_state="manager",
                    assigned_by=uuid4(),
                    member_user_id=actor_id,
                )
            ],
            existing_user_permission=existing_permission,
        )

        response = self._delete_user_permission(
            session=session,
            user_id=actor_id,
            organization_id=organization_id,
            workflow_id=workflow_id,
            target_user_id=target_user_id,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(session.deleted, [existing_permission])
        self.assertTrue(session.committed)
        _assert_workflow_manage_filters(
            self,
            session.workflow_manager_query,
            actor_id,
            workflow_id,
            organization_id,
        )

    def test_delete_user_workflow_permission_rejects_member_without_manage(self):
        # active member라도 workflow manager가 아니면 user direct permission 회수는 거부해야 한다.
        actor_id = uuid4()
        target_user_id = uuid4()
        organization_id = uuid4()
        workflow_id = uuid4()
        existing_permission = _user_workflow_permission(
            organization_id=organization_id,
            workflow_id=workflow_id,
            user_id=target_user_id,
            auth_state="viewer",
            assigned_by=uuid4(),
        )
        session = _Session(
            organization=_organization(id=organization_id, created_by=uuid4()),
            membership=_membership(user_id=actor_id, organization_id=organization_id),
            target_membership=_membership(
                user_id=target_user_id,
                organization_id=organization_id,
            ),
            workflow=_workflow(id=workflow_id, organization_id=organization_id),
            target_user=SimpleNamespace(id=target_user_id),
            manager_permissions=[
                _team_workflow_permission(
                    organization_id=organization_id,
                    workflow_id=workflow_id,
                    team_id=uuid4(),
                    auth_state="builder",
                    assigned_by=uuid4(),
                    member_user_id=actor_id,
                )
            ],
            existing_user_permission=existing_permission,
        )

        response = self._delete_user_permission(
            session=session,
            user_id=actor_id,
            organization_id=organization_id,
            workflow_id=workflow_id,
            target_user_id=target_user_id,
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(
            response.json(),
            _error(
                "permission.denied",
                "Workflow manage or organization manager permission is required.",
            ),
        )
        self.assertEqual(session.deleted, [])
        self.assertFalse(session.committed)
        self.permission_audit.assert_not_called()

    def test_delete_user_workflow_permission_hides_missing_permission_row(self):
        # scope와 권한이 맞아도 회수할 user direct permission row가 없으면 404를 반환한다.
        actor_id = uuid4()
        target_user_id = uuid4()
        organization_id = uuid4()
        workflow_id = uuid4()
        session = _Session(
            organization=_organization(id=organization_id, created_by=actor_id),
            workflow=_workflow(id=workflow_id, organization_id=organization_id),
            target_user=SimpleNamespace(id=target_user_id),
            target_membership=_membership(
                user_id=target_user_id,
                organization_id=organization_id,
            ),
            existing_user_permission=None,
        )

        response = self._delete_user_permission(
            session=session,
            user_id=actor_id,
            organization_id=organization_id,
            workflow_id=workflow_id,
            target_user_id=target_user_id,
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(
            response.json(),
            _error(
                "resource.not_found",
                "User workflow permission not found.",
            ),
        )
        self.assertEqual(session.deleted, [])
        self.assertFalse(session.committed)
        self.assertIsNotNone(session.lock_statement)
        self.assertIn(
            "pg_advisory_xact_lock",
            str(session.lock_statement.compile(dialect=postgresql.dialect())),
        )
        self.permission_audit.assert_not_called()

    def test_delete_user_llm_permission_deletes_row_for_organization_manager(self):
        # organization manager는 user direct LLM credential permission을 회수할 수 있다.
        actor_id = uuid4()
        target_user_id = uuid4()
        organization_id = uuid4()
        credential_id = uuid4()
        existing_permission = _user_llm_permission(
            organization_id=organization_id,
            credential_id=credential_id,
            user_id=target_user_id,
            auth_state="operator",
            assigned_by=uuid4(),
        )
        session = _Session(
            organization=_organization(id=organization_id, created_by=actor_id),
            credential=_credential(
                id=credential_id,
                organization_id=organization_id,
                user_id=uuid4(),
            ),
            target_user=SimpleNamespace(id=target_user_id),
            target_membership=_membership(
                user_id=target_user_id,
                organization_id=organization_id,
            ),
            existing_user_llm_permission=existing_permission,
        )

        response = self._delete_user_llm_permission(
            session=session,
            user_id=actor_id,
            organization_id=organization_id,
            credential_id=credential_id,
            target_user_id=target_user_id,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "message": "User LLM credential permission deleted",
                "id": str(existing_permission.id),
            },
        )
        self.assertEqual(session.deleted, [existing_permission])
        self.assertTrue(is_manually_audited(session, existing_permission, "deleted"))
        self.assertTrue(session.committed)
        self.assertFalse(session.scalars_called)
        self.assertIsNotNone(session.lock_statement)
        self.permission_audit.assert_not_called()
        audit = _single_added_audit(session)
        self.assertEqual(audit.action, "user_llm_permission.deleted")
        self.assertEqual(audit.category, "data_change")
        self.assertEqual(audit.actor_id, actor_id)
        self.assertEqual(audit.target_type, "user_llm_permission")
        self.assertEqual(audit.target_id, str(existing_permission.id))
        self.assertEqual(audit.before["user_id"], str(target_user_id))
        self.assertEqual(
            audit.before["llm_credential_id"],
            str(credential_id),
        )
        self.assertEqual(audit.before["auth_state"], "operator")
        self.assertEqual(
            audit.audit_metadata["organization_id"], str(organization_id)
        )
        self.assertIsNone(audit.after)
        self.assertEqual(audit.audit_metadata["request_id"], "98d6d88b-8d7a-46fd-8d12-f2024d2fac4b")
        _assert_audit_added_before_commit(self, session)

    def test_delete_user_llm_permission_allows_credential_manager(self):
        # organization manager가 아니어도 credential manager면 user direct permission 회수가 가능하다.
        actor_id = uuid4()
        target_user_id = uuid4()
        organization_id = uuid4()
        credential_id = uuid4()
        existing_permission = _user_llm_permission(
            organization_id=organization_id,
            credential_id=credential_id,
            user_id=target_user_id,
            auth_state="viewer",
            assigned_by=uuid4(),
        )
        session = _Session(
            organization=_organization(id=organization_id, created_by=uuid4()),
            membership=_membership(user_id=actor_id, organization_id=organization_id),
            credential=_credential(
                id=credential_id,
                organization_id=organization_id,
                user_id=uuid4(),
            ),
            target_user=SimpleNamespace(id=target_user_id),
            target_membership=_membership(
                user_id=target_user_id,
                organization_id=organization_id,
            ),
            llm_manager_permissions=[
                _team_llm_permission(
                    organization_id=organization_id,
                    credential_id=credential_id,
                    team_id=uuid4(),
                    auth_state="manager",
                    assigned_by=uuid4(),
                    member_user_id=actor_id,
                )
            ],
            existing_user_llm_permission=existing_permission,
        )

        response = self._delete_user_llm_permission(
            session=session,
            user_id=actor_id,
            organization_id=organization_id,
            credential_id=credential_id,
            target_user_id=target_user_id,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(session.deleted, [existing_permission])
        self.assertTrue(session.committed)
        _assert_llm_manage_filters(
            self,
            session.llm_manager_query,
            actor_id,
            credential_id,
            organization_id,
        )

    def test_delete_user_llm_permission_rejects_member_without_manage(self):
        # active member라도 credential manager가 아니면 user direct LLM permission 회수는 거부해야 한다.
        actor_id = uuid4()
        target_user_id = uuid4()
        organization_id = uuid4()
        credential_id = uuid4()
        existing_permission = _user_llm_permission(
            organization_id=organization_id,
            credential_id=credential_id,
            user_id=target_user_id,
            auth_state="viewer",
            assigned_by=uuid4(),
        )
        session = _Session(
            organization=_organization(id=organization_id, created_by=uuid4()),
            membership=_membership(user_id=actor_id, organization_id=organization_id),
            credential=_credential(
                id=credential_id,
                organization_id=organization_id,
                user_id=uuid4(),
            ),
            target_user=SimpleNamespace(id=target_user_id),
            target_membership=_membership(
                user_id=target_user_id,
                organization_id=organization_id,
            ),
            llm_manager_permissions=[
                _team_llm_permission(
                    organization_id=organization_id,
                    credential_id=credential_id,
                    team_id=uuid4(),
                    auth_state="builder",
                    assigned_by=uuid4(),
                    member_user_id=actor_id,
                )
            ],
            existing_user_llm_permission=existing_permission,
        )

        response = self._delete_user_llm_permission(
            session=session,
            user_id=actor_id,
            organization_id=organization_id,
            credential_id=credential_id,
            target_user_id=target_user_id,
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(
            response.json(),
            _error(
                "permission.denied",
                "Credential manage or organization manager permission is required.",
            ),
        )
        self.assertEqual(session.deleted, [])
        self.assertFalse(session.committed)
        self.permission_audit.assert_not_called()

    def test_delete_user_llm_permission_hides_missing_permission_row(self):
        # scope와 권한이 맞아도 회수할 user direct LLM permission row가 없으면 404를 반환한다.
        actor_id = uuid4()
        target_user_id = uuid4()
        organization_id = uuid4()
        credential_id = uuid4()
        session = _Session(
            organization=_organization(id=organization_id, created_by=actor_id),
            credential=_credential(
                id=credential_id,
                organization_id=organization_id,
                user_id=uuid4(),
            ),
            target_user=SimpleNamespace(id=target_user_id),
            target_membership=_membership(
                user_id=target_user_id,
                organization_id=organization_id,
            ),
            existing_user_llm_permission=None,
        )

        response = self._delete_user_llm_permission(
            session=session,
            user_id=actor_id,
            organization_id=organization_id,
            credential_id=credential_id,
            target_user_id=target_user_id,
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(
            response.json(),
            _error(
                "resource.not_found",
                "User LLM credential permission not found.",
            ),
        )
        self.assertEqual(session.deleted, [])
        self.assertFalse(session.committed)
        self.assertIsNotNone(session.lock_statement)
        self.permission_audit.assert_not_called()

    def _put_permission(
        self,
        session,
        user_id,
        workflow_id,
        team_id,
        payload,
        organization_id=None,
        raw_organization_id=None,
        include_auth_cookie=True,
        auth_side_effect=None,
    ):
        """fake DB session과 fake 인증 결과로 권한 PUT endpoint를 호출한다."""
        # 각 테스트는 실제 DB 대신 fake session을 주입하고 인증 결과만 고정한다.
        _ensure_active_user_row(session, user_id)
        app.dependency_overrides[get_db] = lambda: session
        headers = {
            "X-Request-ID": "98d6d88b-8d7a-46fd-8d12-f2024d2fac4b",
        }
        if include_auth_cookie:
            headers["Cookie"] = "auth_token=token"
        if raw_organization_id is not None:
            headers["X-Organization-Id"] = raw_organization_id
        elif organization_id is not None:
            headers["X-Organization-Id"] = str(organization_id)

        patch_kwargs = (
            {"side_effect": auth_side_effect}
            if auth_side_effect is not None
            else {"return_value": SimpleNamespace(id=user_id)}
        )
        with patch(
            "apps.gateway.api.v1.endpoints.permissions.AuthService.get_user_from_token",
            **patch_kwargs,
        ):
            return TestClient(app).put(
                f"/api/v1/permissions/workflows/{workflow_id}/teams/{team_id}",
                headers=headers,
                json=payload,
            )

    def _get_workflow_permissions(
        self,
        session,
        user_id,
        workflow_id,
        organization_id=None,
        raw_organization_id=None,
        include_auth_cookie=True,
        auth_side_effect=None,
    ):
        """fake DB session과 fake 인증 결과로 workflow permission 목록 endpoint를 호출한다."""
        app.dependency_overrides[get_db] = lambda: session
        headers = {
            "X-Request-ID": "98d6d88b-8d7a-46fd-8d12-f2024d2fac4b",
        }
        if include_auth_cookie:
            headers["Cookie"] = "auth_token=token"
        if raw_organization_id is not None:
            headers["X-Organization-Id"] = raw_organization_id
        elif organization_id is not None:
            headers["X-Organization-Id"] = str(organization_id)

        patch_kwargs = (
            {"side_effect": auth_side_effect}
            if auth_side_effect is not None
            else {"return_value": SimpleNamespace(id=user_id)}
        )
        with patch(
            "apps.gateway.api.v1.endpoints.permissions.AuthService.get_user_from_token",
            **patch_kwargs,
        ):
            return TestClient(app).get(
                f"/api/v1/permissions/workflows/{workflow_id}",
                headers=headers,
            )

    def _get_knowledge_permissions(
        self,
        session,
        user_id,
        knowledge_base_id,
        organization_id=None,
        raw_organization_id=None,
        include_auth_cookie=True,
        auth_side_effect=None,
    ):
        """fake DB session과 fake 인증 결과로 KB permission 목록 endpoint를 호출한다."""
        app.dependency_overrides[get_db] = lambda: session
        headers = {
            "X-Request-ID": "98d6d88b-8d7a-46fd-8d12-f2024d2fac4b",
        }
        if include_auth_cookie:
            headers["Cookie"] = "auth_token=token"
        if raw_organization_id is not None:
            headers["X-Organization-Id"] = raw_organization_id
        elif organization_id is not None:
            headers["X-Organization-Id"] = str(organization_id)

        patch_kwargs = (
            {"side_effect": auth_side_effect}
            if auth_side_effect is not None
            else {"return_value": SimpleNamespace(id=user_id)}
        )
        with patch(
            "apps.gateway.api.v1.endpoints.permissions.AuthService.get_user_from_token",
            **patch_kwargs,
        ):
            return TestClient(app).get(
                f"/api/v1/permissions/knowledge-bases/{knowledge_base_id}",
                headers=headers,
            )

    def _put_user_permission(
        self,
        session,
        user_id,
        workflow_id,
        target_user_id,
        payload,
        organization_id=None,
        raw_organization_id=None,
        include_auth_cookie=True,
        auth_side_effect=None,
    ):
        """fake DB session과 fake 인증 결과로 user 권한 PUT endpoint를 호출한다."""
        app.dependency_overrides[get_db] = lambda: session
        headers = {
            "X-Request-ID": "98d6d88b-8d7a-46fd-8d12-f2024d2fac4b",
        }
        if include_auth_cookie:
            headers["Cookie"] = "auth_token=token"
        if raw_organization_id is not None:
            headers["X-Organization-Id"] = raw_organization_id
        elif organization_id is not None:
            headers["X-Organization-Id"] = str(organization_id)

        patch_kwargs = (
            {"side_effect": auth_side_effect}
            if auth_side_effect is not None
            else {"return_value": SimpleNamespace(id=user_id)}
        )
        with patch(
            "apps.gateway.api.v1.endpoints.permissions.AuthService.get_user_from_token",
            **patch_kwargs,
        ):
            return TestClient(app).put(
                f"/api/v1/permissions/workflows/{workflow_id}/users/{target_user_id}",
                headers=headers,
                json=payload,
            )

    def _put_knowledge_permission(
        self,
        session,
        user_id,
        knowledge_base_id,
        team_id,
        payload,
        organization_id=None,
        raw_organization_id=None,
        include_auth_cookie=True,
        auth_side_effect=None,
    ):
        """fake DB session과 fake 인증 결과로 KB team 권한 PUT endpoint를 호출한다."""
        _ensure_active_user_row(session, user_id)
        app.dependency_overrides[get_db] = lambda: session
        headers = {
            "X-Request-ID": "98d6d88b-8d7a-46fd-8d12-f2024d2fac4b",
        }
        if include_auth_cookie:
            headers["Cookie"] = "auth_token=token"
        if raw_organization_id is not None:
            headers["X-Organization-Id"] = raw_organization_id
        elif organization_id is not None:
            headers["X-Organization-Id"] = str(organization_id)

        patch_kwargs = (
            {"side_effect": auth_side_effect}
            if auth_side_effect is not None
            else {"return_value": SimpleNamespace(id=user_id)}
        )
        with patch(
            "apps.gateway.api.v1.endpoints.permissions.AuthService.get_user_from_token",
            **patch_kwargs,
        ):
            return TestClient(app).put(
                f"/api/v1/permissions/knowledge-bases/{knowledge_base_id}/teams/{team_id}",
                headers=headers,
                json=payload,
            )

    def _put_user_knowledge_permission(
        self,
        session,
        user_id,
        knowledge_base_id,
        target_user_id,
        payload,
        organization_id=None,
        raw_organization_id=None,
        include_auth_cookie=True,
        auth_side_effect=None,
    ):
        """fake DB session과 fake 인증 결과로 KB user 직접 권한 PUT endpoint를 호출한다."""
        app.dependency_overrides[get_db] = lambda: session
        headers = {
            "X-Request-ID": "98d6d88b-8d7a-46fd-8d12-f2024d2fac4b",
        }
        if include_auth_cookie:
            headers["Cookie"] = "auth_token=token"
        if raw_organization_id is not None:
            headers["X-Organization-Id"] = raw_organization_id
        elif organization_id is not None:
            headers["X-Organization-Id"] = str(organization_id)

        patch_kwargs = (
            {"side_effect": auth_side_effect}
            if auth_side_effect is not None
            else {"return_value": SimpleNamespace(id=user_id)}
        )
        with patch(
            "apps.gateway.api.v1.endpoints.permissions.AuthService.get_user_from_token",
            **patch_kwargs,
        ):
            return TestClient(app).put(
                f"/api/v1/permissions/knowledge-bases/{knowledge_base_id}/users/{target_user_id}",
                headers=headers,
                json=payload,
            )

    def _put_llm_permission(
        self,
        session,
        user_id,
        credential_id,
        team_id,
        payload,
        organization_id=None,
        raw_organization_id=None,
        include_auth_cookie=True,
        auth_side_effect=None,
    ):
        """fake DB session과 fake 인증 결과로 LLM credential team 권한 PUT endpoint를 호출한다."""
        _ensure_active_user_row(session, user_id)
        app.dependency_overrides[get_db] = lambda: session
        headers = {
            "X-Request-ID": "98d6d88b-8d7a-46fd-8d12-f2024d2fac4b",
        }
        if include_auth_cookie:
            headers["Cookie"] = "auth_token=token"
        if raw_organization_id is not None:
            headers["X-Organization-Id"] = raw_organization_id
        elif organization_id is not None:
            headers["X-Organization-Id"] = str(organization_id)

        patch_kwargs = (
            {"side_effect": auth_side_effect}
            if auth_side_effect is not None
            else {"return_value": SimpleNamespace(id=user_id)}
        )
        with patch(
            "apps.gateway.api.v1.endpoints.permissions.AuthService.get_user_from_token",
            **patch_kwargs,
        ):
            return TestClient(app).put(
                f"/api/v1/permissions/llm-credentials/{credential_id}/teams/{team_id}",
                headers=headers,
                json=payload,
            )

    def _put_user_llm_permission(
        self,
        session,
        user_id,
        credential_id,
        target_user_id,
        payload,
        organization_id=None,
        raw_organization_id=None,
        include_auth_cookie=True,
        auth_side_effect=None,
    ):
        """fake DB session과 fake 인증 결과로 LLM credential user 권한 PUT endpoint를 호출한다."""
        app.dependency_overrides[get_db] = lambda: session
        headers = {
            "X-Request-ID": "98d6d88b-8d7a-46fd-8d12-f2024d2fac4b",
        }
        if include_auth_cookie:
            headers["Cookie"] = "auth_token=token"
        if raw_organization_id is not None:
            headers["X-Organization-Id"] = raw_organization_id
        elif organization_id is not None:
            headers["X-Organization-Id"] = str(organization_id)

        patch_kwargs = (
            {"side_effect": auth_side_effect}
            if auth_side_effect is not None
            else {"return_value": SimpleNamespace(id=user_id)}
        )
        with patch(
            "apps.gateway.api.v1.endpoints.permissions.AuthService.get_user_from_token",
            **patch_kwargs,
        ):
            return TestClient(app).put(
                f"/api/v1/permissions/llm-credentials/{credential_id}/users/{target_user_id}",
                headers=headers,
                json=payload,
            )

    def _delete_permission(
        self,
        session,
        user_id,
        workflow_id,
        team_id,
        organization_id=None,
        raw_organization_id=None,
        include_auth_cookie=True,
        auth_side_effect=None,
    ):
        """fake DB session과 fake 인증 결과로 권한 DELETE endpoint를 호출한다."""
        app.dependency_overrides[get_db] = lambda: session
        headers = {
            "X-Request-ID": "98d6d88b-8d7a-46fd-8d12-f2024d2fac4b",
        }
        if include_auth_cookie:
            headers["Cookie"] = "auth_token=token"
        if raw_organization_id is not None:
            headers["X-Organization-Id"] = raw_organization_id
        elif organization_id is not None:
            headers["X-Organization-Id"] = str(organization_id)

        patch_kwargs = (
            {"side_effect": auth_side_effect}
            if auth_side_effect is not None
            else {"return_value": SimpleNamespace(id=user_id)}
        )
        with patch(
            "apps.gateway.api.v1.endpoints.permissions.AuthService.get_user_from_token",
            **patch_kwargs,
        ):
            return TestClient(app).delete(
                f"/api/v1/permissions/workflows/{workflow_id}/teams/{team_id}",
                headers=headers,
            )

    def _delete_llm_permission(
        self,
        session,
        user_id,
        credential_id,
        team_id,
        organization_id=None,
        raw_organization_id=None,
        include_auth_cookie=True,
        auth_side_effect=None,
    ):
        """fake DB session과 fake 인증 결과로 LLM credential team 권한 DELETE endpoint를 호출한다."""
        app.dependency_overrides[get_db] = lambda: session
        headers = {
            "X-Request-ID": "98d6d88b-8d7a-46fd-8d12-f2024d2fac4b",
        }
        if include_auth_cookie:
            headers["Cookie"] = "auth_token=token"
        if raw_organization_id is not None:
            headers["X-Organization-Id"] = raw_organization_id
        elif organization_id is not None:
            headers["X-Organization-Id"] = str(organization_id)

        patch_kwargs = (
            {"side_effect": auth_side_effect}
            if auth_side_effect is not None
            else {"return_value": SimpleNamespace(id=user_id)}
        )
        with patch(
            "apps.gateway.api.v1.endpoints.permissions.AuthService.get_user_from_token",
            **patch_kwargs,
        ):
            return TestClient(app).delete(
                f"/api/v1/permissions/llm-credentials/{credential_id}/teams/{team_id}",
                headers=headers,
            )

    def _delete_user_permission(
        self,
        session,
        user_id,
        workflow_id,
        target_user_id,
        organization_id=None,
        raw_organization_id=None,
        include_auth_cookie=True,
        auth_side_effect=None,
    ):
        """fake DB session과 fake 인증 결과로 user 권한 DELETE endpoint를 호출한다."""
        app.dependency_overrides[get_db] = lambda: session
        headers = {
            "X-Request-ID": "98d6d88b-8d7a-46fd-8d12-f2024d2fac4b",
        }
        if include_auth_cookie:
            headers["Cookie"] = "auth_token=token"
        if raw_organization_id is not None:
            headers["X-Organization-Id"] = raw_organization_id
        elif organization_id is not None:
            headers["X-Organization-Id"] = str(organization_id)

        patch_kwargs = (
            {"side_effect": auth_side_effect}
            if auth_side_effect is not None
            else {"return_value": SimpleNamespace(id=user_id)}
        )
        with patch(
            "apps.gateway.api.v1.endpoints.permissions.AuthService.get_user_from_token",
            **patch_kwargs,
        ):
            return TestClient(app).delete(
                f"/api/v1/permissions/workflows/{workflow_id}/users/{target_user_id}",
                headers=headers,
            )

    def _delete_knowledge_permission(
        self,
        session,
        user_id,
        knowledge_base_id,
        team_id,
        organization_id=None,
        raw_organization_id=None,
        include_auth_cookie=True,
        auth_side_effect=None,
    ):
        """fake DB session과 fake 인증 결과로 KB team 권한 DELETE endpoint를 호출한다."""
        app.dependency_overrides[get_db] = lambda: session
        headers = {
            "X-Request-ID": "98d6d88b-8d7a-46fd-8d12-f2024d2fac4b",
        }
        if include_auth_cookie:
            headers["Cookie"] = "auth_token=token"
        if raw_organization_id is not None:
            headers["X-Organization-Id"] = raw_organization_id
        elif organization_id is not None:
            headers["X-Organization-Id"] = str(organization_id)

        patch_kwargs = (
            {"side_effect": auth_side_effect}
            if auth_side_effect is not None
            else {"return_value": SimpleNamespace(id=user_id)}
        )
        with patch(
            "apps.gateway.api.v1.endpoints.permissions.AuthService.get_user_from_token",
            **patch_kwargs,
        ):
            return TestClient(app).delete(
                f"/api/v1/permissions/knowledge-bases/{knowledge_base_id}/teams/{team_id}",
                headers=headers,
            )

    def _delete_user_knowledge_permission(
        self,
        session,
        user_id,
        knowledge_base_id,
        target_user_id,
        organization_id=None,
        raw_organization_id=None,
        include_auth_cookie=True,
        auth_side_effect=None,
    ):
        """fake DB session과 fake 인증 결과로 KB user 직접 권한 DELETE endpoint를 호출한다."""
        app.dependency_overrides[get_db] = lambda: session
        headers = {
            "X-Request-ID": "98d6d88b-8d7a-46fd-8d12-f2024d2fac4b",
        }
        if include_auth_cookie:
            headers["Cookie"] = "auth_token=token"
        if raw_organization_id is not None:
            headers["X-Organization-Id"] = raw_organization_id
        elif organization_id is not None:
            headers["X-Organization-Id"] = str(organization_id)

        patch_kwargs = (
            {"side_effect": auth_side_effect}
            if auth_side_effect is not None
            else {"return_value": SimpleNamespace(id=user_id)}
        )
        with patch(
            "apps.gateway.api.v1.endpoints.permissions.AuthService.get_user_from_token",
            **patch_kwargs,
        ):
            return TestClient(app).delete(
                f"/api/v1/permissions/knowledge-bases/{knowledge_base_id}/users/{target_user_id}",
                headers=headers,
            )

    def _delete_user_llm_permission(
        self,
        session,
        user_id,
        credential_id,
        target_user_id,
        organization_id=None,
        raw_organization_id=None,
        include_auth_cookie=True,
        auth_side_effect=None,
    ):
        """fake DB session과 fake 인증 결과로 LLM credential user 권한 DELETE endpoint를 호출한다."""
        app.dependency_overrides[get_db] = lambda: session
        headers = {
            "X-Request-ID": "98d6d88b-8d7a-46fd-8d12-f2024d2fac4b",
        }
        if include_auth_cookie:
            headers["Cookie"] = "auth_token=token"
        if raw_organization_id is not None:
            headers["X-Organization-Id"] = raw_organization_id
        elif organization_id is not None:
            headers["X-Organization-Id"] = str(organization_id)

        patch_kwargs = (
            {"side_effect": auth_side_effect}
            if auth_side_effect is not None
            else {"return_value": SimpleNamespace(id=user_id)}
        )
        with patch(
            "apps.gateway.api.v1.endpoints.permissions.AuthService.get_user_from_token",
            **patch_kwargs,
        ):
            return TestClient(app).delete(
                f"/api/v1/permissions/llm-credentials/{credential_id}/users/{target_user_id}",
                headers=headers,
            )


class _Query:
    # SQLAlchemy Query chain 중 이 테스트에서 사용하는 최소 동작만 흉내 낸다.
    def __init__(
        self,
        first_result=None,
        items=None,
        apply_filters=False,
        project_auth_state=False,
        delete_sink=None,
    ):
        """first/all 결과와 필터 적용 여부를 받아 fake query를 구성한다."""
        self.first_result = first_result
        self.items = items or []
        self.apply_filters = apply_filters
        self.project_auth_state = project_auth_state
        self.delete_sink = delete_sink
        self.join_values = []
        self.filter_expressions = []
        self.options_values = []
        self.order_by_values = []

    def join(self, *args):
        """endpoint가 생성한 join 조건을 나중에 검증할 수 있게 저장한다."""
        self.join_values.append(args)
        return self

    def filter(self, *expressions):
        """filter 조건을 저장하고 필요하면 fake 결과에 적용한다."""
        self.filter_expressions.extend(expressions)
        return self

    def options(self, *args):
        """joinedload 같은 ORM option 호출을 기록한다."""
        self.options_values.extend(args)
        return self

    def order_by(self, *args):
        """endpoint가 생성한 정렬 조건을 기록한다."""
        self.order_by_values.extend(args)
        return self

    def with_for_update(self, **_kwargs):
        """Production row-lock query chain을 보존하는 테스트 더블이다."""
        return self

    def populate_existing(self):
        """Identity-map refresh query chain을 보존하는 테스트 더블이다."""
        return self

    def first(self):
        """첫 fake row를 반환하고 필요하면 SQLAlchemy 조건을 흉내 낸다."""
        if self.first_result is None:
            rows = self.all()
            return rows[0] if rows else None
        if self.apply_filters:
            for expression in self.filter_expressions:
                if not _matches_expression(self.first_result, expression):
                    return None
        return self.first_result

    def all(self):
        """fake row 목록을 반환하고 필요하면 조건과 맞는 row만 남긴다."""
        if self.apply_filters:
            rows = [
                item
                for item in self.items
                if all(
                    _matches_expression(item, expr) for expr in self.filter_expressions
                )
            ]
        else:
            rows = self.items
        if self.project_auth_state:
            return [item.auth_state for item in rows]
        return rows

    def delete(self, synchronize_session=False):
        """SQLAlchemy bulk delete 경로를 기록한다."""
        if self.first_result is not None:
            rows = [self.first_result] if self.first() is not None else []
        else:
            rows = self.all()
        if self.delete_sink is not None:
            self.delete_sink.extend(rows)
        return len(rows)


class _ScalarResult:
    def __init__(self, value):
        """Session.scalars(...).one() 호출 형태를 흉내 내기 위한 wrapper."""
        self.value = value

    def one(self):
        """upsert returning 결과로 사용할 fake permission 객체를 반환한다."""
        return self.value

    def one_or_none(self):
        """no-op upsert처럼 returning row가 없을 수 있는 경로를 흉내 낸다."""
        return self.value


class _Session:
    # endpoint의 query 순서에 맞춰 각 모델별 fake query 결과를 반환한다.
    def __init__(
        self,
        organization=None,
        membership=None,
        workflow=None,
        knowledge_base=None,
        credential=None,
        team=None,
        target_user=None,
        target_membership=None,
        manager_permissions=None,
        knowledge_manager_permissions=None,
        llm_manager_permissions=None,
        user_direct_permissions=None,
        user_knowledge_direct_permissions=None,
        user_llm_direct_permissions=None,
        existing_permission=None,
        existing_knowledge_permission=None,
        existing_llm_permission=None,
        existing_user_permission=None,
        existing_user_knowledge_permission=None,
        existing_user_llm_permission=None,
        workflow_team_permissions=None,
        workflow_user_permissions=None,
        knowledge_team_permissions=None,
        knowledge_user_permissions=None,
        llm_team_permissions=None,
        llm_user_permissions=None,
        upsert_result=None,
        knowledge_upsert_result=None,
        llm_upsert_result=None,
        user_upsert_result=None,
        user_knowledge_upsert_result=None,
        user_llm_upsert_result=None,
    ):
        """권한 endpoint가 사용하는 Session API의 최소 동작을 구성한다."""
        self.organization_query = _Query(
            first_result=organization,
            apply_filters=True,
        )
        organization_memberships = [
            row for row in (membership, target_membership) if row is not None
        ]
        self.membership_rows = organization_memberships
        self.membership_query = _Query(
            items=organization_memberships,
            apply_filters=True,
        )
        self.workflow_query = _Query(first_result=workflow, apply_filters=True)
        app_row = None
        if workflow is not None:
            app_row = App(
                id=workflow.app_id,
                organization_id=workflow.organization_id,
                name="Workflow App",
                workflow_id=workflow.id,
                url_slug=f"workflow-app-{workflow.app_id}",
                auth_secret=None,
                created_by=workflow.created_by,
            )
        self.app_query = _Query(first_result=app_row, apply_filters=True)
        self.knowledge_base_query = _Query(
            first_result=knowledge_base,
            apply_filters=True,
        )
        self.credential_query = _Query(first_result=credential, apply_filters=True)
        self.team_query = _Query(first_result=team, apply_filters=True)
        self.user_rows = _active_user_rows(organization, membership, target_user)
        self.user_query = _Query(items=self.user_rows, apply_filters=True)
        self.target_membership_query = _Query(
            first_result=target_membership,
            apply_filters=True,
        )
        self.manager_permissions = manager_permissions
        self.knowledge_manager_permissions = knowledge_manager_permissions
        self.llm_manager_permissions = llm_manager_permissions
        self.user_direct_permissions = user_direct_permissions or []
        self.user_knowledge_direct_permissions = user_knowledge_direct_permissions or []
        self.user_llm_direct_permissions = user_llm_direct_permissions or []
        self.existing_permission = existing_permission
        self.existing_knowledge_permission = existing_knowledge_permission
        self.existing_llm_permission = existing_llm_permission
        self.existing_user_permission = existing_user_permission
        self.existing_user_knowledge_permission = existing_user_knowledge_permission
        self.existing_user_llm_permission = existing_user_llm_permission
        self.workflow_team_permissions = workflow_team_permissions
        self.workflow_user_permissions = workflow_user_permissions
        self.knowledge_team_permissions = knowledge_team_permissions
        self.knowledge_user_permissions = knowledge_user_permissions
        self.llm_team_permissions = llm_team_permissions
        self.llm_user_permissions = llm_user_permissions
        self.workflow_manager_query = None
        self.knowledge_manager_query = None
        self.llm_manager_query = None
        self.user_workflow_query = None
        self.user_knowledge_query = None
        self.user_llm_query = None
        self.workflow_permission_query_count = 0
        self.knowledge_permission_query_count = 0
        self.llm_permission_query_count = 0
        self.membership_query_count = 0
        self.user_query_count = 0
        self.user_workflow_permission_query_count = 0
        self.user_knowledge_permission_query_count = 0
        self.user_llm_permission_query_count = 0
        self.upsert_result = upsert_result
        self.knowledge_upsert_result = knowledge_upsert_result
        self.llm_upsert_result = llm_upsert_result
        self.user_upsert_result = user_upsert_result
        self.user_knowledge_upsert_result = user_knowledge_upsert_result
        self.user_llm_upsert_result = user_llm_upsert_result
        self.upsert_statement = None
        self.lock_statement = None
        self.lock_statements = []
        self.query_calls = []
        self.added = []
        self.deleted = []
        self.bulk_deleted = []
        self.operations = []
        self.info = {}
        self.audit_add_error = None
        self.scalars_called = False
        self.committed = False
        self.refreshed = False

    def query(self, model):
        """요청 ORM model에 맞는 fake query를 반환하고 순서를 기록한다."""
        self.query_calls.append(model)
        if model is Organization:
            return self.organization_query
        if model is OrganizationMembership:
            self.membership_query_count += 1
            self.membership_query = _Query(
                items=self.membership_rows,
                apply_filters=True,
            )
            return self.membership_query
        if model is TeamMembership:
            self.membership_query_count += 1
            # target user 조회 이후의 membership 조회는 target user scope 검증용이다.
            if self.user_query_count > 0:
                return self.target_membership_query
            return self.membership_query
        if model is Workflow:
            return self.workflow_query
        if model is App:
            return self.app_query
        if model is KnowledgeBase:
            return self.knowledge_base_query
        if model is LLMCredential:
            return self.credential_query
        if model is Team:
            return self.team_query
        if model is User:
            self.user_query_count += 1
            self.user_query = _Query(items=self.user_rows, apply_filters=True)
            return self.user_query
        if model is TeamWorkflowPermission:
            self.workflow_permission_query_count += 1
            if self.workflow_team_permissions is not None:
                return _Query(
                    items=self.workflow_team_permissions,
                    apply_filters=True,
                )
            # 첫 번째 TeamWorkflowPermission query는 요청자의 manage 권한 판정용이다.
            if (
                self.manager_permissions is not None
                and self.workflow_permission_query_count == 1
            ):
                self.workflow_manager_query = _Query(
                    items=self.manager_permissions,
                    apply_filters=True,
                )
                return self.workflow_manager_query
            return _Query(first_result=self.existing_permission, apply_filters=True)
        if model is TeamWorkflowPermission.auth_state:
            self.workflow_permission_query_count += 1
            if self.workflow_team_permissions is not None:
                return _Query(
                    items=self.workflow_team_permissions,
                    apply_filters=True,
                    project_auth_state=True,
                )
            if (
                self.manager_permissions is not None
                and self.workflow_permission_query_count == 1
            ):
                self.workflow_manager_query = _Query(
                    items=self.manager_permissions,
                    apply_filters=True,
                    project_auth_state=True,
                )
                return self.workflow_manager_query
            return _Query(items=[], apply_filters=True, project_auth_state=True)
        if model is TeamKnowledgePermission:
            self.knowledge_permission_query_count += 1
            if self.knowledge_team_permissions is not None:
                return _Query(
                    items=self.knowledge_team_permissions,
                    apply_filters=True,
                )
            if (
                self.knowledge_manager_permissions is not None
                and self.knowledge_permission_query_count == 1
            ):
                self.knowledge_manager_query = _Query(
                    items=self.knowledge_manager_permissions,
                    apply_filters=True,
                )
                return self.knowledge_manager_query
            return _Query(
                first_result=self.existing_knowledge_permission,
                apply_filters=True,
                delete_sink=self.bulk_deleted,
            )
        if model is TeamKnowledgePermission.auth_state:
            self.knowledge_permission_query_count += 1
            if self.knowledge_team_permissions is not None:
                return _Query(
                    items=self.knowledge_team_permissions,
                    apply_filters=True,
                    project_auth_state=True,
                )
            if (
                self.knowledge_manager_permissions is not None
                and self.knowledge_permission_query_count == 1
            ):
                self.knowledge_manager_query = _Query(
                    items=self.knowledge_manager_permissions,
                    apply_filters=True,
                    project_auth_state=True,
                )
                return self.knowledge_manager_query
            return _Query(items=[], apply_filters=True, project_auth_state=True)
        if model is TeamLLMPermission:
            self.llm_permission_query_count += 1
            if self.llm_team_permissions is not None:
                return _Query(
                    items=self.llm_team_permissions,
                    apply_filters=True,
                )
            if (
                self.llm_manager_permissions is not None
                and self.llm_permission_query_count == 1
            ):
                self.llm_manager_query = _Query(
                    items=self.llm_manager_permissions,
                    apply_filters=True,
                )
                return self.llm_manager_query
            return _Query(first_result=self.existing_llm_permission, apply_filters=True)
        if model is TeamLLMPermission.auth_state:
            self.llm_permission_query_count += 1
            if self.llm_team_permissions is not None:
                return _Query(
                    items=self.llm_team_permissions,
                    apply_filters=True,
                    project_auth_state=True,
                )
            if (
                self.llm_manager_permissions is not None
                and self.llm_permission_query_count == 1
            ):
                self.llm_manager_query = _Query(
                    items=self.llm_manager_permissions,
                    apply_filters=True,
                    project_auth_state=True,
                )
                return self.llm_manager_query
            return _Query(items=[], apply_filters=True, project_auth_state=True)
        if model is UserWorkflowPermission:
            self.user_workflow_permission_query_count += 1
            if self.workflow_user_permissions is not None:
                return _Query(
                    items=self.workflow_user_permissions,
                    apply_filters=True,
                )
            # manager 판정이 필요한 경로에서는 첫 query가 권한 합산용이고,
            # 그 다음 query가 upsert/delete 대상 row 조회용이다.
            if (
                self.user_direct_permissions
                and self.user_workflow_permission_query_count == 1
            ):
                self.user_workflow_query = _Query(
                    items=self.user_direct_permissions,
                    apply_filters=True,
                )
                return self.user_workflow_query
            return _Query(
                first_result=self.existing_user_permission,
                apply_filters=True,
            )
        if model is UserWorkflowPermission.auth_state:
            self.user_workflow_permission_query_count += 1
            if self.workflow_user_permissions is not None:
                return _Query(
                    items=self.workflow_user_permissions,
                    apply_filters=True,
                    project_auth_state=True,
                )
            if (
                self.user_direct_permissions
                and self.user_workflow_permission_query_count == 1
            ):
                self.user_workflow_query = _Query(
                    items=self.user_direct_permissions,
                    apply_filters=True,
                    project_auth_state=True,
                )
                return self.user_workflow_query
            return _Query(items=[], apply_filters=True, project_auth_state=True)
        if model is UserKnowledgePermission:
            self.user_knowledge_permission_query_count += 1
            if self.knowledge_user_permissions is not None:
                return _Query(
                    items=self.knowledge_user_permissions,
                    apply_filters=True,
                )
            if (
                self.user_knowledge_direct_permissions
                and self.user_knowledge_permission_query_count == 1
            ):
                self.user_knowledge_query = _Query(
                    items=self.user_knowledge_direct_permissions,
                    apply_filters=True,
                )
                return self.user_knowledge_query
            return _Query(
                first_result=self.existing_user_knowledge_permission,
                apply_filters=True,
                delete_sink=self.bulk_deleted,
            )
        if model is UserKnowledgePermission.auth_state:
            self.user_knowledge_permission_query_count += 1
            if self.knowledge_user_permissions is not None:
                return _Query(
                    items=self.knowledge_user_permissions,
                    apply_filters=True,
                    project_auth_state=True,
                )
            if (
                self.user_knowledge_direct_permissions
                and self.user_knowledge_permission_query_count == 1
            ):
                self.user_knowledge_query = _Query(
                    items=self.user_knowledge_direct_permissions,
                    apply_filters=True,
                    project_auth_state=True,
                )
                return self.user_knowledge_query
            return _Query(items=[], apply_filters=True, project_auth_state=True)
        if model is UserLLMPermission:
            self.user_llm_permission_query_count += 1
            if self.llm_user_permissions is not None:
                return _Query(
                    items=self.llm_user_permissions,
                    apply_filters=True,
                )
            if (
                self.user_llm_direct_permissions
                and self.user_llm_permission_query_count == 1
            ):
                self.user_llm_query = _Query(
                    items=self.user_llm_direct_permissions,
                    apply_filters=True,
                )
                return self.user_llm_query
            return _Query(
                first_result=self.existing_user_llm_permission,
                apply_filters=True,
            )
        if model is UserLLMPermission.auth_state:
            self.user_llm_permission_query_count += 1
            if self.llm_user_permissions is not None:
                return _Query(
                    items=self.llm_user_permissions,
                    apply_filters=True,
                    project_auth_state=True,
                )
            if (
                self.user_llm_direct_permissions
                and self.user_llm_permission_query_count == 1
            ):
                self.user_llm_query = _Query(
                    items=self.user_llm_direct_permissions,
                    apply_filters=True,
                    project_auth_state=True,
                )
                return self.user_llm_query
            return _Query(items=[], apply_filters=True, project_auth_state=True)
        raise AssertionError(f"Unexpected query model: {model}")

    def add(self, value):
        """ORM insert 경로 사용 여부를 감지하도록 add 호출 값을 기록한다."""
        if isinstance(value, AuditLog) and self.audit_add_error is not None:
            raise self.audit_add_error
        self.added.append(value)
        self.operations.append(("add", type(value)))

    def delete(self, value):
        """ORM delete 경로 사용 여부를 감지하도록 delete 호출 값을 기록한다."""
        self.deleted.append(value)

    def scalars(self, statement):
        """Core upsert statement를 기록하고 returning 결과 wrapper를 반환한다."""
        self.scalars_called = True
        self.upsert_statement = statement
        table_name = getattr(getattr(statement, "table", None), "name", None)
        if table_name == TeamKnowledgePermission.__tablename__:
            return _ScalarResult(self.knowledge_upsert_result)
        if table_name == TeamLLMPermission.__tablename__:
            return _ScalarResult(self.llm_upsert_result)
        if table_name == UserWorkflowPermission.__tablename__:
            return _ScalarResult(self.user_upsert_result)
        if table_name == UserKnowledgePermission.__tablename__:
            return _ScalarResult(self.user_knowledge_upsert_result)
        if table_name == UserLLMPermission.__tablename__:
            return _ScalarResult(self.user_llm_upsert_result)
        return _ScalarResult(self.upsert_result)

    def execute(self, statement):
        """advisory lock statement를 기록한다."""
        self.lock_statement = statement
        self.lock_statements.append(statement)
        return None

    def commit(self):
        """endpoint가 transaction commit까지 도달했는지 표시한다."""
        self.committed = True
        self.operations.append(("commit", None))

    def rollback(self):
        self.operations.append(("rollback", None))

    def flush(self):
        self.operations.append(("flush", None))

    def refresh(self, value):
        """id가 비어 있으면 fake id를 넣고 refresh 호출을 기록한다."""
        if value.id is None:
            value.id = uuid4()
        self.refreshed = True


def _organization(id, created_by, managed_by=None, is_active=True):
    """organization scope 검증용 Organization fixture를 만든다."""
    now = datetime.now(timezone.utc)
    return Organization(
        id=id,
        name="Acme",
        options={},
        flags=0,
        created_by=created_by,
        managed_by=managed_by,
        is_active=is_active,
        created_at=now,
        updated_at=now,
    )


def _workflow(id, organization_id):
    """workflow scope 검증에 필요한 필드만 채운 Workflow fixture를 만든다."""
    now = datetime.now(timezone.utc)
    return Workflow(
        id=id,
        organization_id=organization_id,
        app_id=uuid4(),
        graph={},
        features={},
        env_variables={},
        runtime_variables={},
        created_by=uuid4(),
        created_at=now,
        updated_at=now,
    )


def _knowledge_base(id, organization_id, user_id, lifecycle_state="active"):
    """KB scope 검증에 필요한 필드만 채운 KnowledgeBase fixture를 만든다."""
    now = datetime.now(timezone.utc)
    return KnowledgeBase(
        id=id,
        organization_id=organization_id,
        name="Knowledge Base",
        description=None,
        embedding_model="text-embedding-3-small",
        top_k=5,
        similarity_threshold=0.7,
        user_id=user_id,
        lifecycle_state=lifecycle_state,
        created_at=now,
        updated_at=now,
    )


def _credential(id, organization_id, user_id, is_valid=True):
    """LLM credential scope 검증에 필요한 필드만 채운 fixture를 만든다."""
    now = datetime.now(timezone.utc)
    return LLMCredential(
        id=id,
        provider_id=uuid4(),
        user_id=user_id,
        organization_id=organization_id,
        credential_name="OpenAI",
        encrypted_config="encrypted",
        config_preview="sk-****",
        is_valid=is_valid,
        quota_type="none",
        quota_limit=-1,
        quota_used=0,
        created_at=now,
        updated_at=now,
    )


def _team(id, organization_id, is_active=True):
    """team scope와 active team 검증에 필요한 Team fixture를 만든다."""
    now = datetime.now(timezone.utc)
    return Team(
        id=id,
        organization_id=organization_id,
        name="Builders",
        description=None,
        options={},
        flags=0,
        created_by=uuid4(),
        managed_by=None,
        is_active=is_active,
        is_auto_add=False,
        created_at=now,
        updated_at=now,
    )


def _user(id, email, name, deactivated_at=None):
    """permission list 응답의 user grantee 표시용 fixture를 만든다."""
    now = datetime.now(timezone.utc)
    return User(
        id=id,
        email=email,
        name=name,
        social_provider="local",
        deactivated_at=deactivated_at,
        created_at=now,
        updated_at=now,
    )


def _active_user_rows(organization, membership, target_user):
    """permission helper의 active user 조회에 필요한 fake user row를 만든다."""
    rows = []
    seen = set()

    def add_user(user_id):
        if user_id is None or user_id in seen:
            return
        seen.add(user_id)
        rows.append(SimpleNamespace(id=user_id, deactivated_at=None))

    if organization is not None:
        add_user(organization.created_by)
        add_user(organization.managed_by)
    if membership is not None:
        add_user(membership.user_id)
    if target_user is not None:
        if not hasattr(target_user, "deactivated_at"):
            target_user.deactivated_at = None
        if target_user.id not in seen:
            seen.add(target_user.id)
            rows.append(target_user)
    return rows


def _ensure_active_user_row(session, user_id):
    """요청의 인증 user가 fake active user 조회에서 검색되도록 보강한다."""
    if not hasattr(session, "user_rows"):
        return
    if any(row.id == user_id for row in session.user_rows):
        return
    session.user_rows.append(SimpleNamespace(id=user_id, deactivated_at=None))


def _membership(
    user_id,
    organization_id,
    team_is_active=True,
    auth_state="member",
    membership_state="active",
):
    """active membership scope 검증용 fixture를 만든다."""
    return SimpleNamespace(
        id=uuid4(),
        user_id=user_id,
        organization_id=organization_id,
        membership_state=membership_state,
        organization_auth_state=auth_state,
        grantee_organization_id=organization_id,
        team_organization_id=organization_id,
        team_is_active=team_is_active,
    )


def _team_workflow_permission(
    organization_id,
    workflow_id,
    team_id,
    auth_state,
    assigned_by,
    member_user_id=None,
    membership_organization_id=None,
    team_organization_id=None,
    team_is_active=True,
):
    """권한 판정과 upsert returning용 permission fixture를 만든다."""
    permission = TeamWorkflowPermission(
        id=uuid4(),
        grantee_organization_id=organization_id,
        workflow_id=workflow_id,
        team_id=team_id,
        auth_state=auth_state,
        assigned_by=assigned_by,
        assigned_at=datetime.now(timezone.utc),
        options={},
        flags=0,
    )
    if member_user_id is not None:
        permission.member_user_id = member_user_id
        permission.membership_grantee_organization_id = (
            membership_organization_id or organization_id
        )
        permission.team_organization_id = team_organization_id or organization_id
        permission.team_is_active = team_is_active
    return permission


def _user_workflow_permission(
    organization_id,
    workflow_id,
    user_id,
    auth_state,
    assigned_by,
):
    """user direct workflow permission fixture를 만든다."""
    return UserWorkflowPermission(
        id=uuid4(),
        grantee_organization_id=organization_id,
        workflow_id=workflow_id,
        user_id=user_id,
        auth_state=auth_state,
        assigned_by=assigned_by,
        assigned_at=datetime.now(timezone.utc),
        options={},
        flags=0,
    )


def _team_knowledge_permission(
    organization_id,
    knowledge_base_id,
    team_id,
    auth_state,
    assigned_by,
    member_user_id=None,
    membership_organization_id=None,
    team_organization_id=None,
    team_is_active=True,
):
    """KB 권한 판정과 upsert returning용 team permission fixture를 만든다."""
    permission = TeamKnowledgePermission(
        id=uuid4(),
        grantee_organization_id=organization_id,
        knowledge_base_id=knowledge_base_id,
        team_id=team_id,
        auth_state=auth_state,
        assigned_by=assigned_by,
        assigned_at=datetime.now(timezone.utc),
        options={},
        flags=0,
    )
    if member_user_id is not None:
        permission.member_user_id = member_user_id
        permission.membership_grantee_organization_id = (
            membership_organization_id or organization_id
        )
        permission.team_organization_id = team_organization_id or organization_id
        permission.team_is_active = team_is_active
    return permission


def _user_knowledge_permission(
    organization_id,
    knowledge_base_id,
    user_id,
    auth_state,
    assigned_by,
):
    """user direct KB permission fixture를 만든다."""
    return UserKnowledgePermission(
        id=uuid4(),
        grantee_organization_id=organization_id,
        knowledge_base_id=knowledge_base_id,
        user_id=user_id,
        auth_state=auth_state,
        assigned_by=assigned_by,
        assigned_at=datetime.now(timezone.utc),
        options={},
        flags=0,
    )


def _user_llm_permission(
    organization_id,
    credential_id,
    user_id,
    auth_state,
    assigned_by,
):
    """user direct LLM credential permission fixture를 만든다."""
    return UserLLMPermission(
        id=uuid4(),
        grantee_organization_id=organization_id,
        llm_credential_id=credential_id,
        user_id=user_id,
        auth_state=auth_state,
        assigned_by=assigned_by,
        assigned_at=datetime.now(timezone.utc),
        options={},
        flags=0,
    )


def _team_llm_permission(
    organization_id,
    credential_id,
    team_id,
    auth_state,
    assigned_by,
    member_user_id=None,
    membership_organization_id=None,
    team_organization_id=None,
    team_is_active=True,
):
    """LLM credential 권한 판정과 upsert returning용 permission fixture를 만든다."""
    permission = TeamLLMPermission(
        id=uuid4(),
        grantee_organization_id=organization_id,
        llm_credential_id=credential_id,
        team_id=team_id,
        auth_state=auth_state,
        assigned_by=assigned_by,
        assigned_at=datetime.now(timezone.utc),
        options={},
        flags=0,
    )
    if member_user_id is not None:
        permission.member_user_id = member_user_id
        permission.membership_grantee_organization_id = (
            membership_organization_id or organization_id
        )
        permission.team_organization_id = team_organization_id or organization_id
        permission.team_is_active = team_is_active
    return permission


def _single_added_audit(session):
    audits = [value for value in session.added if isinstance(value, AuditLog)]
    if len(audits) != 1:
        raise AssertionError(f"Expected exactly one AuditLog, got {len(audits)}")
    return audits[0]


def _assert_audit_added_before_commit(testcase, session):
    testcase.assertIn(("add", AuditLog), session.operations)
    testcase.assertIn(("commit", None), session.operations)
    testcase.assertLess(
        session.operations.index(("add", AuditLog)),
        session.operations.index(("commit", None)),
    )


def _matches_expression(obj, expression):
    """fake query가 주요 SQLAlchemy filter 표현식을 적용하게 평가한다."""
    left_value = _column_value(obj, str(expression.left))

    if expression.operator is eq:
        if not hasattr(expression.right, "value"):
            return left_value == _column_value(obj, str(expression.right))
        return left_value == expression.right.value
    if expression.operator is is_:
        # User.deactivated_at.is_(None) 같은 NULL 필터도 fake query에서 실제로 걸러낸다.
        if str(expression.right).lower() == "null":
            return left_value is None
        return left_value is (str(expression.right) == "true")
    raise AssertionError(f"Unexpected filter operator: {expression.operator}")


def _column_value(obj, column):
    missing = object()
    value_by_column = {
        "organization.id": getattr(obj, "id", missing),
        "organization.is_active": getattr(obj, "is_active", missing),
        "apps.id": getattr(obj, "id", missing),
        "apps.organization_id": getattr(obj, "organization_id", missing),
        "organization_memberships.user_id": getattr(obj, "user_id", missing),
        "organization_memberships.organization_id": getattr(
            obj,
            "organization_id",
            missing,
        ),
        "organization_memberships.membership_state": getattr(
            obj,
            "membership_state",
            missing,
        ),
        "users.id": getattr(obj, "id", missing),
        "users.deactivated_at": getattr(obj, "deactivated_at", None),
        "workflows.id": getattr(obj, "id", missing),
        "workflows.organization_id": getattr(obj, "organization_id", missing),
        "knowledge_bases.id": getattr(obj, "id", missing),
        "knowledge_bases.organization_id": getattr(
            obj,
            "organization_id",
            missing,
        ),
        "knowledge_bases.lifecycle_state": getattr(obj, "lifecycle_state", missing),
        "llm_credentials.id": getattr(obj, "id", missing),
        "llm_credentials.organization_id": getattr(
            obj,
            "organization_id",
            missing,
        ),
        "llm_credentials.is_valid": getattr(obj, "is_valid", missing),
        "teams.id": getattr(obj, "id", missing),
        "teams.organization_id": getattr(
            obj,
            "team_organization_id",
            getattr(obj, "organization_id", missing),
        ),
        "teams.is_active": getattr(
            obj,
            "team_is_active",
            getattr(obj, "is_active", missing),
        ),
        "team_memberships.user_id": getattr(
            obj,
            "member_user_id",
            getattr(obj, "user_id", missing),
        ),
        "team_memberships.grantee_organization_id": getattr(
            obj,
            "membership_grantee_organization_id",
            getattr(obj, "grantee_organization_id", missing),
        ),
        "team_workflow_permissions.workflow_id": getattr(
            obj,
            "workflow_id",
            missing,
        ),
        "team_workflow_permissions.grantee_organization_id": getattr(
            obj,
            "grantee_organization_id",
            missing,
        ),
        "team_workflow_permissions.team_id": getattr(obj, "team_id", missing),
        "team_knowledge_permissions.knowledge_base_id": getattr(
            obj,
            "knowledge_base_id",
            missing,
        ),
        "team_knowledge_permissions.grantee_organization_id": getattr(
            obj,
            "grantee_organization_id",
            missing,
        ),
        "team_knowledge_permissions.team_id": getattr(obj, "team_id", missing),
        "team_llm_permissions.llm_credential_id": getattr(
            obj,
            "llm_credential_id",
            missing,
        ),
        "team_llm_permissions.grantee_organization_id": getattr(
            obj,
            "grantee_organization_id",
            missing,
        ),
        "team_llm_permissions.team_id": getattr(obj, "team_id", missing),
        "user_workflow_permissions.user_id": getattr(obj, "user_id", missing),
        "user_workflow_permissions.workflow_id": getattr(
            obj,
            "workflow_id",
            missing,
        ),
        "user_workflow_permissions.grantee_organization_id": getattr(
            obj,
            "grantee_organization_id",
            missing,
        ),
        "user_knowledge_permissions.user_id": getattr(obj, "user_id", missing),
        "user_knowledge_permissions.knowledge_base_id": getattr(
            obj,
            "knowledge_base_id",
            missing,
        ),
        "user_knowledge_permissions.grantee_organization_id": getattr(
            obj,
            "grantee_organization_id",
            missing,
        ),
        "user_llm_permissions.user_id": getattr(obj, "user_id", missing),
        "user_llm_permissions.llm_credential_id": getattr(
            obj,
            "llm_credential_id",
            missing,
        ),
        "user_llm_permissions.grantee_organization_id": getattr(
            obj,
            "grantee_organization_id",
            missing,
        ),
    }
    if column not in value_by_column:
        raise AssertionError(f"Unexpected filter column: {column}")
    if value_by_column[column] is missing:
        raise AssertionError(f"Missing fixture attribute for filter column: {column}")
    return value_by_column[column]


def _assert_organization_scope_filters(testcase, query, organization_id):
    """organization 조회가 id와 active 조건으로 scope를 제한하는지 검증한다."""
    id_filter = _find_filter(query, "organization.id", eq)
    testcase.assertEqual(id_filter.right.value, organization_id)

    active_filter = _find_filter(query, "organization.is_active", is_)
    testcase.assertEqual(str(active_filter.right), "true")


def _assert_active_membership_filters(testcase, query, user_id, organization_id):
    """organization membership row 조회에 필요한 scope 조건을 포함하는지 검증한다."""
    user_filter = _find_filter(query, "organization_memberships.user_id", eq)
    testcase.assertEqual(user_filter.right.value, user_id)

    org_filter = _find_filter(
        query,
        "organization_memberships.organization_id",
        eq,
        right_value=organization_id,
    )
    testcase.assertEqual(org_filter.right.value, organization_id)


def _assert_workflow_scope_filters(testcase, query, workflow_id, organization_id):
    """workflow 조회가 요청 workflow와 organization scope로 제한되는지 검증한다."""
    workflow_filter = _find_filter(query, "workflows.id", eq)
    testcase.assertEqual(workflow_filter.right.value, workflow_id)

    org_filter = _find_filter(query, "workflows.organization_id", eq)
    testcase.assertEqual(org_filter.right.value, organization_id)


def _assert_credential_scope_filters(testcase, query, credential_id, organization_id):
    """credential 조회가 요청 credential과 organization scope로 제한되는지 검증한다."""
    credential_filter = _find_filter(query, "llm_credentials.id", eq)
    testcase.assertEqual(credential_filter.right.value, credential_id)

    org_filter = _find_filter(query, "llm_credentials.organization_id", eq)
    testcase.assertEqual(org_filter.right.value, organization_id)

    valid_filter = _find_filter(query, "llm_credentials.is_valid", is_)
    testcase.assertEqual(str(valid_filter.right), "true")


def _assert_team_scope_filters(testcase, query, team_id, organization_id):
    """team 조회가 요청 team, organization, active 조건을 포함하는지 검증한다."""
    team_filter = _find_filter(query, "teams.id", eq)
    testcase.assertEqual(team_filter.right.value, team_id)

    org_filter = _find_filter(query, "teams.organization_id", eq)
    testcase.assertEqual(org_filter.right.value, organization_id)

    active_filter = _find_filter(query, "teams.is_active", is_)
    testcase.assertEqual(str(active_filter.right), "true")


def _assert_workflow_manage_filters(
    testcase,
    query,
    user_id,
    workflow_id,
    organization_id,
):
    """workflow manager 조회가 membership/team/permission scope를 묶는지 검증한다."""
    _assert_join_predicate(
        testcase,
        query,
        "team_memberships.team_id",
        "team_workflow_permissions.team_id",
    )
    _assert_join_predicate(
        testcase,
        query,
        "teams.id",
        "team_workflow_permissions.team_id",
    )

    user_filter = _find_filter(query, "team_memberships.user_id", eq)
    testcase.assertEqual(user_filter.right.value, user_id)

    workflow_filter = _find_filter(
        query,
        "team_workflow_permissions.workflow_id",
        eq,
    )
    testcase.assertEqual(workflow_filter.right.value, workflow_id)

    org_filter = _find_filter(
        query,
        "team_workflow_permissions.grantee_organization_id",
        eq,
    )
    testcase.assertEqual(org_filter.right.value, organization_id)

    membership_org_filter = _find_filter(
        query,
        "team_memberships.grantee_organization_id",
        eq,
        right_text="team_workflow_permissions.grantee_organization_id",
    )
    testcase.assertEqual(
        str(membership_org_filter.right),
        "team_workflow_permissions.grantee_organization_id",
    )

    team_org_filter = _find_filter(
        query,
        "teams.organization_id",
        eq,
        right_value=organization_id,
    )
    testcase.assertEqual(team_org_filter.right.value, organization_id)

    active_filter = _find_filter(query, "teams.is_active", is_)
    testcase.assertEqual(str(active_filter.right), "true")


def _assert_llm_manage_filters(
    testcase,
    query,
    user_id,
    credential_id,
    organization_id,
):
    """LLM credential manager 조회가 membership/team/permission scope를 묶는지 검증한다."""
    _assert_join_predicate(
        testcase,
        query,
        "team_memberships.team_id",
        "team_llm_permissions.team_id",
    )
    _assert_join_predicate(
        testcase,
        query,
        "teams.id",
        "team_llm_permissions.team_id",
    )

    user_filter = _find_filter(query, "team_memberships.user_id", eq)
    testcase.assertEqual(user_filter.right.value, user_id)

    credential_filter = _find_filter(
        query,
        "team_llm_permissions.llm_credential_id",
        eq,
    )
    testcase.assertEqual(credential_filter.right.value, credential_id)

    org_filter = _find_filter(
        query,
        "team_llm_permissions.grantee_organization_id",
        eq,
        right_value=organization_id,
    )
    testcase.assertEqual(org_filter.right.value, organization_id)

    membership_org_filter = _find_filter(
        query,
        "team_memberships.grantee_organization_id",
        eq,
        right_text="team_llm_permissions.grantee_organization_id",
    )
    testcase.assertEqual(
        str(membership_org_filter.right),
        "team_llm_permissions.grantee_organization_id",
    )

    team_org_filter = _find_filter(
        query,
        "teams.organization_id",
        eq,
        right_value=organization_id,
    )
    testcase.assertEqual(team_org_filter.right.value, organization_id)

    active_filter = _find_filter(query, "teams.is_active", is_)
    testcase.assertEqual(str(active_filter.right), "true")


def _assert_user_workflow_manage_filters(
    testcase,
    query,
    user_id,
    workflow_id,
    organization_id,
):
    """user-direct manager 조회가 필요한 scope 조건을 포함하는지 검증한다."""
    user_filter = _find_filter(query, "user_workflow_permissions.user_id", eq)
    testcase.assertEqual(user_filter.right.value, user_id)

    workflow_filter = _find_filter(
        query,
        "user_workflow_permissions.workflow_id",
        eq,
    )
    testcase.assertEqual(workflow_filter.right.value, workflow_id)

    org_filter = _find_filter(
        query,
        "user_workflow_permissions.grantee_organization_id",
        eq,
    )
    testcase.assertEqual(org_filter.right.value, organization_id)


def _find_filter(query, left, operator, right_value=None, right_text=None):
    """저장된 SQLAlchemy filter 표현식 중 기대한 column/operator 조건을 찾는다."""
    for expression in query.filter_expressions:
        if str(expression.left) != left or expression.operator is not operator:
            continue
        if (
            right_value is not None
            and getattr(expression.right, "value", None) != right_value
        ):
            continue
        if right_text is not None and str(expression.right) != right_text:
            continue
        return expression
    raise AssertionError(f"Missing filter: {left}")


def _assert_join_predicate(testcase, query, left, right):
    """저장된 join 조건 중 기대한 column equality predicate를 검증한다."""
    for join_value in query.join_values:
        if len(join_value) < 2:
            continue
        expression = join_value[1]
        if (
            str(expression.left) == left
            and expression.operator is eq
            and str(expression.right) == right
        ):
            return
    testcase.fail(f"Missing join predicate: {left} == {right}")


def _error(code, message, details=None):
    """테스트에서 기대하는 권한 API error envelope을 만든다."""
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
