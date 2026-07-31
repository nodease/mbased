import importlib.util
import sys
import types
from pathlib import Path

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    ForeignKeyConstraint,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB


_MISSING = object()


def _snapshot_modules(names):
    """격리 로딩이 교체할 sys.modules 항목과 부모 패키지 속성을 저장한다."""
    modules = {name: sys.modules.get(name) for name in names}
    attrs = {}
    for name in names:
        if "." not in name:
            continue
        parent = modules[name.rsplit(".", 1)[0]]
        if parent is not None:
            attrs[name] = getattr(parent, name.rsplit(".", 1)[1], _MISSING)
    return modules, attrs


def _restore_modules(snapshot):
    """격리 복사본을 걷어내고 로딩 전 import 상태로 되돌린다.

    원복하지 않으면 같은 프로세스에서 뒤에 실행되는 테스트(예: tests/services)가
    격리된 Base registry에 등록된 모델을 import해서 mapper 초기화에 실패한다.
    """
    modules, attrs = snapshot
    for name, module in modules.items():
        if module is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = module
    for name, value in attrs.items():
        parent_name, child_name = name.rsplit(".", 1)
        parent = modules[parent_name]
        if value is _MISSING:
            if hasattr(parent, child_name):
                delattr(parent, child_name)
        else:
            setattr(parent, child_name, value)


def _load_team_module():
    root = Path(__file__).resolve().parents[2]
    packages = {
        "apps": root / "apps",
        "apps.shared": root / "apps" / "shared",
        "apps.shared.db": root / "apps" / "shared" / "db",
        "apps.shared.db.models": root / "apps" / "shared" / "db" / "models",
    }
    snapshot = _snapshot_modules(
        list(packages) + ["apps.shared.db.base", "apps.shared.db.models.team"]
    )
    try:
        for name, path in packages.items():
            module = sys.modules.get(name) or types.ModuleType(name)
            module.__path__ = [str(path)]
            sys.modules[name] = module
            if "." in name:
                parent_name, child_name = name.rsplit(".", 1)
                setattr(sys.modules[parent_name], child_name, module)

        base_spec = importlib.util.spec_from_file_location(
            "apps.shared.db.base",
            root / "apps" / "shared" / "db" / "base.py",
        )
        base_module = importlib.util.module_from_spec(base_spec)
        sys.modules["apps.shared.db.base"] = base_module
        base_spec.loader.exec_module(base_module)
        sys.modules["apps.shared.db"].base = base_module

        team_spec = importlib.util.spec_from_file_location(
            "apps.shared.db.models.team",
            root / "apps" / "shared" / "db" / "models" / "team.py",
        )
        team_module = importlib.util.module_from_spec(team_spec)
        sys.modules["apps.shared.db.models.team"] = team_module
        team_spec.loader.exec_module(team_module)
        sys.modules["apps.shared.db.models"].team = team_module
        return team_module
    finally:
        _restore_modules(snapshot)


def test_team_tables_use_final_names():
    team = _load_team_module()

    assert team.Team.__tablename__ == "teams"
    assert team.TeamMembership.__tablename__ == "team_memberships"
    assert team.TeamWorkflowPermission.__tablename__ == "team_workflow_permissions"
    assert team.UserWorkflowPermission.__tablename__ == "user_workflow_permissions"
    assert team.UserLLMPermission.__tablename__ == "user_llm_permissions"
    assert (
        team.TeamKnowledgeCollectionPermission.__tablename__
        == "team_knowledge_collection_permissions"
    )
    assert (
        team.UserKnowledgeCollectionPermission.__tablename__
        == "user_knowledge_collection_permissions"
    )
    assert (
        team.TeamKnowledgeDomainPermission.__tablename__
        == "team_knowledge_domain_permissions"
    )
    assert (
        team.UserKnowledgeDomainPermission.__tablename__
        == "user_knowledge_domain_permissions"
    )


def test_team_has_unique_team_id_organization_id_constraint():
    team = _load_team_module()

    assert any(
        isinstance(constraint, UniqueConstraint)
        and constraint.name == "uq_teams_id_organization_id"
        and [column.name for column in constraint.columns] == [
            "id",
            "organization_id",
        ]
        for constraint in team.Team.__table__.constraints
    )


def test_team_assignment_tables_have_composite_team_org_fk():
    team = _load_team_module()
    expected_constraints = {
        team.TeamMembership: "fk_team_memberships_team_org",
        team.TeamWorkflowPermission: "fk_team_workflow_permissions_team_org",
        team.TeamKnowledgePermission: "fk_team_knowledge_permissions_team_org",
        team.TeamKnowledgeCollectionPermission: (
            "fk_team_knowledge_collection_permissions_team_org"
        ),
        team.TeamLLMPermission: "fk_team_llm_permissions_team_org",
        team.TeamAuditPermission: "fk_team_audit_permissions_team_org",
    }

    for model, constraint_name in expected_constraints.items():
        foreign_keys = [
            constraint
            for constraint in model.__table__.constraints
            if isinstance(constraint, ForeignKeyConstraint)
        ]
        target = next(
            (
                constraint
                for constraint in foreign_keys
                if constraint.name == constraint_name
            ),
            None,
        )

        assert target is not None
        assert [column.name for column in target.columns] == [
            "team_id",
            "grantee_organization_id",
        ]
        assert [element.target_fullname for element in target.elements] == [
            "teams.id",
            "teams.organization_id",
        ]

    domain_fk = next(
        constraint
        for constraint in team.TeamKnowledgeDomainPermission.__table__.constraints
        if isinstance(constraint, ForeignKeyConstraint)
        and constraint.name == "fk_team_knowledge_domain_permissions_team_org"
    )
    assert [column.name for column in domain_fk.columns] == [
        "team_id",
        "organization_id",
    ]
    assert [element.target_fullname for element in domain_fk.elements] == [
        "teams.id",
        "teams.organization_id",
    ]


def test_team_options_and_flags_columns():
    team = _load_team_module()
    expected_check_names = {
        team.Team: "ck_teams_flags_nonnegative",
        team.TeamMembership: "ck_team_memberships_flags_nonnegative",
        team.TeamWorkflowPermission: "ck_team_workflow_permissions_flags_nonnegative",
        team.TeamKnowledgePermission: "ck_team_knowledge_permissions_flags_nonnegative",
        team.TeamKnowledgeCollectionPermission: (
            "ck_team_knowledge_collection_permissions_flags_nonnegative"
        ),
        team.TeamLLMPermission: "ck_team_llm_permissions_flags_nonnegative",
        team.TeamAuditPermission: "ck_team_audit_permissions_flags_nonnegative",
        team.UserWorkflowPermission: "ck_user_workflow_permissions_flags_nonnegative",
        team.UserLLMPermission: "ck_user_llm_permissions_flags_nonnegative",
        team.UserKnowledgeCollectionPermission: (
            "ck_user_knowledge_collection_permissions_flags_nonnegative"
        ),
    }

    for model, check_name in expected_check_names.items():
        assert "option" not in model.__table__.columns

        options_column = model.__table__.columns["options"]
        assert isinstance(options_column.type, JSONB)
        assert options_column.nullable is False

        flags_column = model.__table__.columns["flags"]
        assert isinstance(flags_column.type, BigInteger)
        assert flags_column.nullable is False

        assert any(
            isinstance(constraint, CheckConstraint)
            and constraint.name == check_name
            for constraint in model.__table__.constraints
        )


def test_auth_state_is_only_on_resource_permission_tables():
    team = _load_team_module()

    assert "auth_state" not in team.Team.__table__.columns
    assert "auth_state" not in team.TeamMembership.__table__.columns

    for model in (
        team.TeamWorkflowPermission,
        team.TeamKnowledgePermission,
        team.TeamLLMPermission,
        team.TeamAuditPermission,
        team.UserWorkflowPermission,
        team.UserLLMPermission,
    ):
        assert "auth_state" in model.__table__.columns

    assert "auth_state" not in team.TeamKnowledgeCollectionPermission.__table__.columns
    assert "auth_state" not in team.UserKnowledgeCollectionPermission.__table__.columns
    assert "auth_state" not in team.TeamKnowledgeDomainPermission.__table__.columns
    assert "auth_state" not in team.UserKnowledgeDomainPermission.__table__.columns


def test_collection_permission_tables_use_permission_action_rows():
    team = _load_team_module()
    expected_unique_constraints = {
        team.TeamKnowledgeCollectionPermission: (
            "uq_team_knowledge_collection_permissions_action",
            [
                "grantee_organization_id",
                "knowledge_collection_id",
                "team_id",
                "permission_action",
            ],
        ),
        team.UserKnowledgeCollectionPermission: (
            "uq_user_knowledge_collection_permissions_action",
            [
                "grantee_organization_id",
                "user_id",
                "knowledge_collection_id",
                "permission_action",
            ],
        ),
    }

    for model, (constraint_name, columns) in expected_unique_constraints.items():
        assert "permission_action" in model.__table__.columns
        assert "auth_state" not in model.__table__.columns
        assert any(
            isinstance(constraint, UniqueConstraint)
            and constraint.name == constraint_name
            and [column.name for column in constraint.columns] == columns
            for constraint in model.__table__.constraints
        )
        assert any(
            isinstance(constraint, CheckConstraint)
            and "permission_action IN" in str(constraint.sqltext)
            and all(action in str(constraint.sqltext) for action in ("read", "route", "manage", "sync"))
            for constraint in model.__table__.constraints
        )


def test_user_direct_permission_tables_are_additive_user_resource_grants():
    team = _load_team_module()
    expected_unique_constraints = {
        team.UserWorkflowPermission: (
            "uq_user_workflow_permissions_org_user_workflow",
            ["grantee_organization_id", "user_id", "workflow_id"],
        ),
        team.UserLLMPermission: (
            "uq_user_llm_permissions_org_user_credential",
            ["grantee_organization_id", "user_id", "llm_credential_id"],
        ),
    }

    for model, (constraint_name, columns) in expected_unique_constraints.items():
        assert "team_id" not in model.__table__.columns
        assert any(
            isinstance(constraint, UniqueConstraint)
            and constraint.name == constraint_name
            and [column.name for column in constraint.columns] == columns
            for constraint in model.__table__.constraints
        )


def test_knowledge_domain_permission_tables_use_bounded_expiring_action_rows():
    team = _load_team_module()
    expected = {
        team.TeamKnowledgeDomainPermission: (
            "uq_team_knowledge_domain_permissions_action",
            ["organization_id", "team_id", "permission_action"],
        ),
        team.UserKnowledgeDomainPermission: (
            "uq_user_knowledge_domain_permissions_action",
            ["organization_id", "user_id", "permission_action"],
        ),
    }

    for model, (constraint_name, columns) in expected.items():
        assert "expires_at" in model.__table__.columns
        assert "permission_action" in model.__table__.columns
        assert "options" not in model.__table__.columns
        assert "flags" in model.__table__.columns
        assert any(
            isinstance(constraint, UniqueConstraint)
            and constraint.name == constraint_name
            and [column.name for column in constraint.columns] == columns
            for constraint in model.__table__.constraints
        )
        assert any(
            isinstance(constraint, CheckConstraint)
            and all(
                action in str(constraint.sqltext)
                for action in (
                    "catalog_manage",
                    "permission_delegate",
                    "lifecycle_manage",
                    "sync_manage",
                )
            )
            for constraint in model.__table__.constraints
        )
