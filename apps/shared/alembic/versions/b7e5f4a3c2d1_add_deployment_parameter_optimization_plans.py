"""Add deployment-scoped automatic parameter optimization plans.

Revision ID: b7e5f4a3c2d1
Revises: fd4e5f6a7b89
Create Date: 2026-07-14 21:30:00.000000
"""

from typing import Sequence, Union

from apps.shared.db.models.deployment_parameter_optimization import (
    DeploymentParameterOptimizationPlan,
)

from alembic import op

revision: str = "b7e5f4a3c2d1"
down_revision: Union[str, Sequence[str], None] = "fd4e5f6a7b89"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    DeploymentParameterOptimizationPlan.__table__.create(
        bind=op.get_bind(),
        checkfirst=True,
    )


def downgrade() -> None:
    DeploymentParameterOptimizationPlan.__table__.drop(
        bind=op.get_bind(),
        checkfirst=True,
    )
