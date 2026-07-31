import pytest
from apps.shared.domain.run_trigger import (
    RunTriggerContractError,
    normalize_run_trigger_mode,
)


@pytest.mark.parametrize(
    ("trigger_mode", "is_deployed", "expected"),
    [
        ("manual", False, "manual"),
        ("test", False, "manual"),
        ("manual_compare", False, "manual"),
        ("cost_optimizer_compare", False, "manual"),
        ("api", True, "api"),
        ("app", True, "api"),
        ("deployed", True, "api"),
        ("api_secret", True, "api"),
        ("webhook", True, "webhook"),
        ("schedule", True, "scheduler"),
        ("scheduler", True, "scheduler"),
        (" WEBHOOK ", False, "webhook"),
        ("WeBhOoK", False, "webhook"),
        (None, True, "api"),
        (None, False, "manual"),
    ],
)
def test_normalize_run_trigger_mode(
    trigger_mode,
    is_deployed,
    expected,
):
    assert (
        normalize_run_trigger_mode(
            trigger_mode,
            is_deployed=is_deployed,
        )
        == expected
    )


@pytest.mark.parametrize(
    "trigger_mode",
    [
        "",
        "   ",
        "future_surface_marker",
        "workflow_node",
        1,
        True,
        [],
        {},
    ],
)
@pytest.mark.parametrize("is_deployed", [False, True])
def test_explicit_invalid_trigger_fails_without_echoing_input(
    trigger_mode,
    is_deployed,
):
    with pytest.raises(
        RunTriggerContractError,
        match="^workflow run trigger mode is invalid$",
    ) as exc_info:
        normalize_run_trigger_mode(
            trigger_mode,
            is_deployed=is_deployed,
        )

    rendered_input = str(trigger_mode)
    if rendered_input:
        assert rendered_input not in str(exc_info.value)


@pytest.mark.parametrize(
    ("trigger_mode", "expected"),
    [
        ("manual", "manual"),
        ("api", "api"),
        ("webhook", "webhook"),
        ("schedule", "scheduler"),
    ],
)
def test_explicit_alias_takes_precedence_over_deployment_fallback(
    trigger_mode,
    expected,
):
    assert normalize_run_trigger_mode(trigger_mode, is_deployed=False) == expected
    assert normalize_run_trigger_mode(trigger_mode, is_deployed=True) == expected
