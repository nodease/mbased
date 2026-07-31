from __future__ import annotations

from datetime import datetime, timedelta

from apscheduler.triggers.cron import CronTrigger

from apps.gateway.application.deployment.schedule_errors import (
    ScheduleConfigurationError,
)
from apps.shared.domain.schedule_dispatch import canonical_utc


class ApschedulerNextFireCalculator:
    def first_after(
        self, *, cron_expression: str, timezone_name: str, now: datetime
    ) -> datetime:
        trigger = self._trigger(cron_expression, timezone_name)
        canonical_now = canonical_utc(now)
        candidate = trigger.get_next_fire_time(
            None,
            canonical_now + timedelta(microseconds=1),
        )
        return self._require_future(candidate, canonical_now)

    def next_after_occurrence(
        self,
        *,
        cron_expression: str,
        timezone_name: str,
        scheduled_for: datetime,
        now: datetime,
    ) -> datetime:
        trigger = self._trigger(cron_expression, timezone_name)
        previous = canonical_utc(scheduled_for)
        canonical_now = canonical_utc(now)
        # Missed occurrences are coalesced. Replaying each historical fire time
        # makes a valid high-frequency cron look invalid after a long outage.
        anchor = max(previous, canonical_now)
        candidate = trigger.get_next_fire_time(
            None,
            anchor + timedelta(microseconds=1),
        )
        return self._require_future(candidate, canonical_now)

    @staticmethod
    def _trigger(cron_expression: str, timezone_name: str) -> CronTrigger:
        try:
            return CronTrigger.from_crontab(cron_expression, timezone=timezone_name)
        except Exception as exc:
            raise ScheduleConfigurationError("schedule configuration is invalid") from exc

    @staticmethod
    def _require_future(candidate: datetime | None, now: datetime) -> datetime:
        if candidate is None:
            raise ScheduleConfigurationError("schedule has no future fire time")
        canonical = canonical_utc(candidate)
        if canonical <= now:
            raise ScheduleConfigurationError("schedule next fire time did not advance")
        return canonical
