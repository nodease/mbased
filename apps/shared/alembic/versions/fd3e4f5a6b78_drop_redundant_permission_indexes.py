"""Drop redundant permission indexes.

Revision ID: fd3e4f5a6b78
Revises: fc9d0e1f2a34
Create Date: 2026-07-14 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op

revision: str = "fd3e4f5a6b78"
down_revision: Union[str, Sequence[str], None] = "fc9d0e1f2a34"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Remove indexes covered by shorter unique indexes."""
    op.drop_index(
        "ix_team_knowledge_collection_permissions_org",
        table_name="team_knowledge_collection_permissions",
        if_exists=True,
    )
    op.drop_index(
        "ix_user_knowledge_collection_permissions_org",
        table_name="user_knowledge_collection_permissions",
        if_exists=True,
    )
    op.drop_index(
        "ix_team_knowledge_domain_permissions_effective",
        table_name="team_knowledge_domain_permissions",
        if_exists=True,
    )
    op.drop_index(
        "ix_user_knowledge_domain_permissions_effective",
        table_name="user_knowledge_domain_permissions",
        if_exists=True,
    )


def downgrade() -> None:
    """Restore the legacy indexes."""
    op.create_index(
        "ix_user_knowledge_domain_permissions_effective",
        "user_knowledge_domain_permissions",
        ["organization_id", "user_id", "permission_action", "expires_at"],
    )
    op.create_index(
        "ix_team_knowledge_domain_permissions_effective",
        "team_knowledge_domain_permissions",
        ["organization_id", "team_id", "permission_action", "expires_at"],
    )
    op.create_index(
        "ix_user_knowledge_collection_permissions_org",
        "user_knowledge_collection_permissions",
        ["grantee_organization_id"],
    )
    op.create_index(
        "ix_team_knowledge_collection_permissions_org",
        "team_knowledge_collection_permissions",
        ["grantee_organization_id"],
    )
