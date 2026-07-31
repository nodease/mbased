from datetime import datetime, timezone

from apps.shared.services.tracing.retention import TraceRetentionService
from sqlalchemy.dialects import postgresql


def test_prompt_completion_retention_condition_is_separate_from_redacted_payloads():
    now = datetime(2026, 6, 25, tzinfo=timezone.utc)

    condition = TraceRetentionService.expired_payload_condition(
        now=now,
        redacted_cutoff=now,
        prompt_cutoff=now,
    )

    sql = str(
        condition.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )

    assert "trace_payloads.retention_purged_at IS NULL" in sql
    assert "trace_payloads.payload_kind NOT IN ('prompt', 'completion')" in sql
    assert "trace_payloads.payload_kind IN ('prompt', 'completion')" in sql
