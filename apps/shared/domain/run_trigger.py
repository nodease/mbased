"""Canonical WorkflowRun trigger classification without runtime dependencies."""

from typing import Final, Literal

CanonicalRunTriggerMode = Literal["manual", "api", "webhook", "scheduler", "app"]


class RunTriggerContractError(ValueError):
    """Raised when an explicit trigger cannot be classified safely."""


_TRIGGER_ALIASES: Final[dict[str, CanonicalRunTriggerMode]] = {
    "manual": "manual",
    "test": "manual",
    "manual_compare": "manual",
    "cost_optimizer_compare": "manual",
    "api": "api",
    "app": "api",
    "deployed": "api",
    "api_secret": "api",
    "webhook": "webhook",
    "schedule": "scheduler",
    "scheduler": "scheduler",
}


def normalize_run_trigger_mode(
    trigger_mode: object,
    *,
    is_deployed: bool,
) -> CanonicalRunTriggerMode:
    """Normalize producer input while preserving the missing-value fallback.

    Explicit invalid values fail with a static error so task telemetry cannot retain
    an untrusted trigger value.
    """

    if trigger_mode is None:
        return "api" if is_deployed else "manual"

    if not isinstance(trigger_mode, str):
        raise RunTriggerContractError("workflow run trigger mode is invalid")

    normalized = trigger_mode.strip().lower()
    canonical = _TRIGGER_ALIASES.get(normalized)
    if canonical is None:
        raise RunTriggerContractError("workflow run trigger mode is invalid")
    return canonical
