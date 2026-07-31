from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from apps.gateway.adapters.schedule.apscheduler_next_fire import (
    ApschedulerNextFireCalculator,
)
from apps.gateway.application.deployment.schedule_errors import (
    ScheduleConfigurationError,
)


def test_first_fire_is_strictly_after_now():
    now = datetime(2026, 7, 10, 9, 0, tzinfo=timezone.utc)

    result = ApschedulerNextFireCalculator().first_after(
        cron_expression="0 * * * *",
        timezone_name="UTC",
        now=now,
    )

    assert result == datetime(2026, 7, 10, 10, 0, tzinfo=timezone.utc)


def test_overdue_occurrences_coalesce_to_first_future_fire():
    result = ApschedulerNextFireCalculator().next_after_occurrence(
        cron_expression="*/5 * * * *",
        timezone_name="UTC",
        scheduled_for=datetime(2026, 7, 10, 8, 0, tzinfo=timezone.utc),
        now=datetime(2026, 7, 10, 9, 2, tzinfo=timezone.utc),
    )

    assert result == datetime(2026, 7, 10, 9, 5, tzinfo=timezone.utc)


def test_high_frequency_schedule_recovers_after_more_than_1024_missed_fires():
    result = ApschedulerNextFireCalculator().next_after_occurrence(
        cron_expression="* * * * *",
        timezone_name="UTC",
        scheduled_for=datetime(2023, 1, 1, tzinfo=timezone.utc),
        now=datetime(2026, 7, 11, 9, 2, tzinfo=timezone.utc),
    )

    assert result == datetime(2026, 7, 11, 9, 3, tzinfo=timezone.utc)


def test_future_occurrence_never_moves_the_cursor_backwards():
    scheduled_for = datetime(2026, 7, 11, 10, 0, tzinfo=timezone.utc)

    result = ApschedulerNextFireCalculator().next_after_occurrence(
        cron_expression="*/5 * * * *",
        timezone_name="UTC",
        scheduled_for=scheduled_for,
        now=datetime(2026, 7, 11, 9, 2, tzinfo=timezone.utc),
    )

    assert result == datetime(2026, 7, 11, 10, 5, tzinfo=timezone.utc)


@pytest.mark.parametrize(
    ("expression", "timezone_name"),
    [("bad cron", "UTC"), ("0 * * * *", "Invalid/Timezone")],
)
def test_invalid_configuration_uses_safe_typed_error(expression, timezone_name):
    with pytest.raises(ScheduleConfigurationError):
        ApschedulerNextFireCalculator().first_after(
            cron_expression=expression,
            timezone_name=timezone_name,
            now=datetime.now(timezone.utc),
        )


def test_dst_fall_back_occurrences_remain_distinct_utc_instants():
    calculator = ApschedulerNextFireCalculator()
    seoul = ZoneInfo("America/New_York")
    first_fold = datetime(2026, 11, 1, 1, 30, fold=0, tzinfo=seoul)

    result = calculator.next_after_occurrence(
        cron_expression="30 1 * * *",
        timezone_name="America/New_York",
        scheduled_for=first_fold,
        now=first_fold.astimezone(timezone.utc),
    )

    assert result.tzinfo is timezone.utc
    assert result > first_fold.astimezone(timezone.utc)
