"""tracing payload 보안 경계 보강

Revision ID: c0d1e2f3a4b5
Revises: f7a8b9c0d1e2
Create Date: 2026-06-25 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "c0d1e2f3a4b5"
down_revision: Union[str, Sequence[str], None] = "f7a8b9c0d1e2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 비삭제 보관 정리가 같은 실행을 반복 처리하지 않도록 표시 컬럼을 추가합니다.
    op.add_column(
        "workflow_runs",
        sa.Column("retention_purged_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_workflow_runs_retention_purged_at",
        "workflow_runs",
        ["retention_purged_at"],
        unique=False,
    )

    op.add_column(
        "trace_payloads",
        sa.Column("retention_purged_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_trace_payloads_retention_purged_at",
        "trace_payloads",
        ["retention_purged_at"],
        unique=False,
    )
    # 사용처 없는 원문 페이로드 해시는 저엔트로피 페이로드 단서가 될 수 있어 제거합니다.
    op.drop_column("trace_payloads", "raw_payload_hash")
    # 최신 조회의 그룹/정렬 조합을 보조합니다.
    op.create_index(
        "ix_trace_payloads_latest_view",
        "trace_payloads",
        [
            "workflow_run_id",
            "scope",
            "workflow_node_run_id",
            "payload_kind",
            "created_at",
            "sequence",
            "attempt",
        ],
        unique=False,
    )
    # 보관 정리 후보 스캔에서 페이로드 종류별 만료 조건을 빠르게 좁힙니다.
    op.create_index(
        "ix_trace_payloads_retention_scan",
        "trace_payloads",
        ["retention_purged_at", "payload_kind", "created_at", "retention_expires_at"],
        unique=False,
    )
    # 원문 암호문 제거 작업은 암호문이 있는 행만 스캔하도록 부분 인덱스를 둡니다.
    op.create_index(
        "ix_trace_payloads_raw_retention",
        "trace_payloads",
        ["created_at"],
        unique=False,
        postgresql_where=sa.text("raw_payload_encrypted IS NOT NULL"),
    )

    op.drop_constraint(
        "trace_payload_access_events_actor_user_id_fkey",
        "trace_payload_access_events",
        type_="foreignkey",
    )
    # 사용자 삭제 후에도 원문 접근 감사 이벤트 자체는 보존합니다.
    op.alter_column(
        "trace_payload_access_events",
        "actor_user_id",
        existing_type=sa.UUID(),
        nullable=True,
    )
    op.create_foreign_key(
        "fk_trace_payload_access_events_actor_user_id",
        "trace_payload_access_events",
        "users",
        ["actor_user_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.add_column(
        "trace_payload_access_events",
        sa.Column("actor_user_ref", sa.String(length=128), nullable=True),
    )
    op.create_index(
        "ix_trace_payload_access_events_actor_user_ref",
        "trace_payload_access_events",
        ["actor_user_ref"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_trace_payload_access_events_actor_user_ref",
        table_name="trace_payload_access_events",
    )
    op.drop_column("trace_payload_access_events", "actor_user_ref")
    op.drop_constraint(
        "fk_trace_payload_access_events_actor_user_id",
        "trace_payload_access_events",
        type_="foreignkey",
    )
    op.alter_column(
        "trace_payload_access_events",
        "actor_user_id",
        existing_type=sa.UUID(),
        nullable=False,
    )
    op.create_foreign_key(
        "trace_payload_access_events_actor_user_id_fkey",
        "trace_payload_access_events",
        "users",
        ["actor_user_id"],
        ["id"],
        ondelete="CASCADE",
    )

    op.drop_index("ix_trace_payloads_raw_retention", table_name="trace_payloads")
    op.drop_index("ix_trace_payloads_retention_scan", table_name="trace_payloads")
    op.drop_index("ix_trace_payloads_latest_view", table_name="trace_payloads")
    op.add_column(
        "trace_payloads",
        sa.Column("raw_payload_hash", sa.String(length=128), nullable=True),
    )
    op.drop_index("ix_trace_payloads_retention_purged_at", table_name="trace_payloads")
    op.drop_column("trace_payloads", "retention_purged_at")

    op.drop_index(
        "ix_workflow_runs_retention_purged_at",
        table_name="workflow_runs",
    )
    op.drop_column("workflow_runs", "retention_purged_at")
