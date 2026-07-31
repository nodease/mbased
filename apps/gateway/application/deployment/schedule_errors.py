class ScheduleConfigurationError(ValueError):
    """A safe schedule configuration error without provider details."""


class ScheduleDispatchInvariantError(RuntimeError):
    """A canonical dispatch state did not match the requested transition."""
