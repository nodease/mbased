from __future__ import annotations

import os

from apps.shared.domain.schedule_dispatch import (
    ScheduleDispatchSettings,
    schedule_dispatch_settings_from_environment,
)


def get_schedule_dispatch_settings() -> ScheduleDispatchSettings:
    return schedule_dispatch_settings_from_environment(os.environ)
