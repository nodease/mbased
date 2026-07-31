"""Merge heads and add conversation_id to workflow_runs

Revision ID: d2e3f4a5b6c7
Revises: c4d5e6f7a8b9, fa2b3c4d5e6f
Create Date: 2026-07-08 00:00:00.000000

docs/features/chatbot-deployment: 챗봇 배포의 방문자별 대화 격리를 위한
workflow_runs.conversation_id 컬럼 추가. 동시에 기존 두 마이그레이션 head
(c4d5e6f7a8b9, fa2b3c4d5e6f)를 단일 head로 병합한다.
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d2e3f4a5b6c7"
down_revision: Union[str, Sequence[str], None] = ("c4d5e6f7a8b9", "fa2b3c4d5e6f")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "workflow_runs",
        sa.Column("conversation_id", sa.String(length=255), nullable=True),
    )
    op.create_index(
        "ix_workflow_runs_conversation_id",
        "workflow_runs",
        ["conversation_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_workflow_runs_conversation_id", table_name="workflow_runs")
    op.drop_column("workflow_runs", "conversation_id")
