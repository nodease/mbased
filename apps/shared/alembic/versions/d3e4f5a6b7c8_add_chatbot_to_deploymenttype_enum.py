"""Add CHATBOT to DeploymentType enum

Revision ID: d3e4f5a6b7c8
Revises: d2e3f4a5b6c7
Create Date: 2026-07-08 00:00:01.000000

docs/features/chatbot-deployment: 챗봇 배포 타입 추가.
"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d3e4f5a6b7c8"
down_revision: Union[str, Sequence[str], None] = "d2e3f4a5b6c7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # PostgreSQL에서 ENUM 타입에 값을 추가할 때는 트랜잭션 밖에서 실행해야 함
    op.execute("COMMIT")
    op.execute("ALTER TYPE deploymenttype ADD VALUE IF NOT EXISTS 'CHATBOT'")


def downgrade() -> None:
    """Downgrade schema."""
    pass
