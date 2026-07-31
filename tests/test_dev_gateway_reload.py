from scripts.dev_gateway import ReloadDebouncer


def test_reload_debouncer_waits_until_changes_are_quiet() -> None:
    debouncer = ReloadDebouncer(quiet_seconds=3)

    assert debouncer.observe(now=10, changed=True) is False
    assert debouncer.observe(now=12, changed=False) is False
    assert debouncer.observe(now=12.5, changed=True) is False
    assert debouncer.observe(now=15.4, changed=False) is False
    assert debouncer.observe(now=15.5, changed=False) is True


def test_reload_debouncer_requires_a_new_change_after_restart() -> None:
    debouncer = ReloadDebouncer(quiet_seconds=3)

    assert debouncer.observe(now=1, changed=True) is False
    assert debouncer.observe(now=4, changed=False) is True
    assert debouncer.observe(now=20, changed=False) is False
