from __future__ import annotations

import sqlalchemy as sa


def assert_external_effect_downgrade_is_safe(connection) -> None:
    connection.execute(
        sa.text(
            "LOCK TABLE workflow_node_effect_attempts IN ACCESS EXCLUSIVE MODE"
        )
    )
    has_attempts = connection.execute(
        sa.text("SELECT EXISTS (SELECT 1 FROM workflow_node_effect_attempts)")
    ).scalar_one()
    if has_attempts:
        raise RuntimeError(
            "cannot downgrade while workflow node effect attempts exist"
        )
