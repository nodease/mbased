"""workflow_budgets 스키마 계약 테스트.

docs/features/budget-management/requirements.md의 저장 구조 요구를 검증한다.

- BGT-REQ-001~002: workflow당 최대 1개 예산, 최신 상태만 유지 (UNIQUE(workflow_id))
- BGT-REQ-004: organization scope 정합 — 예산 row의 organization_id는
  대상 workflow의 organization_id와 일치해야 한다 (composite FK)
- BGT-REQ-005: 동시 upsert 방어의 기반인 DB unique 제약
- Policies: workflow 삭제 시 예산 row 함께 삭제 (FK cascade)
"""

from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Numeric,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID

# 주의: 모듈 top-level import를 유지한다. tests/db의 다른 테스트가
# sys.modules의 apps.shared.db.base를 격리 복사본으로 교체하므로,
# 함수 안에서 import하면 WorkflowBudget이 잘못된 Base registry에 등록된다.
from apps.shared.db import models as shared_models
from apps.shared.db.models.workflow_budget import WorkflowBudget


def _workflow_budget_model():
    return WorkflowBudget


def _constraint(table, constraint_type, name):
    for constraint in table.constraints:
        if isinstance(constraint, constraint_type) and constraint.name == name:
            return constraint
    return None


def test_workflow_budget_table_name():
    model = _workflow_budget_model()

    assert model.__tablename__ == "workflow_budgets"


def test_workflow_budget_is_registered_in_models_package():
    # Celery worker / alembic metadata에 모델이 잡히려면 package 등록이 필요하다.
    assert shared_models.WorkflowBudget is WorkflowBudget


def test_workflow_id_is_unique():
    # workflow당 예산 최대 1개 (BGT-REQ-001), 동시 생성 경합 방어 (BGT-REQ-005).
    table = _workflow_budget_model().__table__

    constraint = _constraint(
        table, UniqueConstraint, "uq_workflow_budgets_workflow_id"
    )
    assert constraint is not None
    assert [column.name for column in constraint.columns] == ["workflow_id"]


def test_composite_fk_enforces_workflow_organization_match():
    # 예산 row의 organization_id가 대상 workflow의 organization_id와 다르면
    # INSERT/UPDATE가 실패해야 한다 (BGT-REQ-004, teams composite FK 패턴).
    table = _workflow_budget_model().__table__

    constraint = _constraint(
        table, ForeignKeyConstraint, "fk_workflow_budgets_workflow_org"
    )
    assert constraint is not None
    assert [column.name for column in constraint.columns] == [
        "workflow_id",
        "organization_id",
    ]
    assert [element.target_fullname for element in constraint.elements] == [
        "workflows.id",
        "workflows.organization_id",
    ]
    assert constraint.ondelete == "CASCADE"


def test_workflow_delete_cascades_budget_row():
    # workflow 삭제 시 예산 row 함께 삭제 (requirements Policies).
    table = _workflow_budget_model().__table__

    workflow_fks = [
        fk for fk in table.columns["workflow_id"].foreign_keys
    ]
    assert workflow_fks, "workflow_id에 workflows.id FK가 필요하다"
    assert all(fk.ondelete == "CASCADE" for fk in workflow_fks)


def test_organization_id_is_required():
    # 예산은 organization scope 안에서만 의미가 있다 (BGT-REQ-004).
    table = _workflow_budget_model().__table__

    column = table.columns["organization_id"]
    assert column.nullable is False
    assert isinstance(column.type, UUID)
    assert any(
        fk.target_fullname == "organization.id" for fk in column.foreign_keys
    )


def test_monthly_budget_usd_is_numeric_12_2_and_required():
    # USD 소수점 2자리 저장 (api_spec PUT 검증 규칙과 일치).
    table = _workflow_budget_model().__table__

    column = table.columns["monthly_budget_usd"]
    assert column.nullable is False
    assert isinstance(column.type, Numeric)
    assert column.type.precision == 12
    assert column.type.scale == 2


def test_monthly_budget_usd_must_be_positive():
    # API 422 검증(0 이하 거부)의 DB 무결성 백스톱.
    table = _workflow_budget_model().__table__

    constraint = _constraint(
        table, CheckConstraint, "ck_workflow_budgets_monthly_budget_usd_positive"
    )
    assert constraint is not None
    assert "monthly_budget_usd > 0" in str(constraint.sqltext)


def test_is_enabled_is_boolean_with_default_true():
    # 비활성화는 row 삭제가 아니라 is_enabled=false (BGT-REQ-002).
    table = _workflow_budget_model().__table__

    column = table.columns["is_enabled"]
    assert column.nullable is False
    assert isinstance(column.type, Boolean)
    assert column.server_default is not None
    assert "true" in str(column.server_default.arg).lower()


def test_actor_columns_are_nullable_user_fks():
    # created_by는 생성 시 1회, updated_by는 매 갱신 기록 (api_spec).
    # 사용자 삭제/legacy 대비 nullable (API 응답도 uuid|null).
    table = _workflow_budget_model().__table__

    for name in ("created_by", "updated_by"):
        column = table.columns[name]
        assert column.nullable is True, name
        assert any(
            fk.target_fullname == "users.id" for fk in column.foreign_keys
        ), name


def test_timestamps_are_timezone_aware_with_server_default():
    table = _workflow_budget_model().__table__

    for name in ("created_at", "updated_at"):
        column = table.columns[name]
        assert column.nullable is False, name
        assert isinstance(column.type, DateTime), name
        assert column.type.timezone is True, name
        assert column.server_default is not None, name


def test_options_and_flags_follow_new_table_convention():
    # 최근 테이블(permission_requests 등)의 options/flags 확장 컬럼 관례.
    table = _workflow_budget_model().__table__

    options = table.columns["options"]
    assert options.nullable is False
    assert isinstance(options.type, JSONB)

    flags = table.columns["flags"]
    assert flags.nullable is False
    assert isinstance(flags.type, BigInteger)

    constraint = _constraint(
        table, CheckConstraint, "ck_workflow_budgets_flags_nonnegative"
    )
    assert constraint is not None


def test_monthly_budget_usd_roundtrips_decimal():
    # 판정은 Decimal로 수행한다 (requirements Policies). asdecimal 기본 유지 확인.
    table = _workflow_budget_model().__table__

    column = table.columns["monthly_budget_usd"]
    assert column.type.asdecimal is True
    assert column.type.python_type is Decimal


def test_alembic_has_single_head_including_workflow_budgets_migration():
    # additive migration이 단일 head를 유지하며 workflow_budgets를 생성해야 한다.
    from pathlib import Path

    from alembic.config import Config
    from alembic.script import ScriptDirectory

    root = Path(__file__).resolve().parents[2]
    config = Config(str(root / "apps" / "shared" / "alembic.ini"))
    config.set_main_option(
        "script_location", str(root / "apps" / "shared" / "alembic")
    )
    script = ScriptDirectory.from_config(config)

    heads = script.get_heads()
    assert len(heads) == 1, f"multiple alembic heads: {heads}"

    assert any(
        "workflow_budgets" in Path(revision.path).read_text(encoding="utf-8")
        for revision in script.walk_revisions()
    ), "workflow_budgets를 생성하는 migration이 없다"
