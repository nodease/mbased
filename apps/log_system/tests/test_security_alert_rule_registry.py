from datetime import timedelta


def test_security_alert_rule_registry_is_the_single_rule_contract():
    from apps.shared.services.security_alert_rule_registry import (
        SECURITY_ALERT_MAX_WINDOW,
        SECURITY_ALERT_RULES,
        get_security_alert_rule,
    )

    assert [rule.rule_id for rule in SECURITY_ALERT_RULES] == [
        "repeated_permission_denied",
        "multi_resource_permission_probe",
        "repeated_policy_block",
    ]
    assert get_security_alert_rule("repeated_permission_denied") == (
        SECURITY_ALERT_RULES[0]
    )
    assert SECURITY_ALERT_RULES[0].window == timedelta(minutes=5)
    assert SECURITY_ALERT_RULES[0].threshold == 5
    assert SECURITY_ALERT_RULES[0].severity == "medium"
    assert SECURITY_ALERT_RULES[0].count_mode == "events"
    assert SECURITY_ALERT_RULES[1].window == timedelta(minutes=10)
    assert SECURITY_ALERT_RULES[1].threshold == 5
    assert SECURITY_ALERT_RULES[1].count_mode == "distinct_targets"
    assert SECURITY_ALERT_RULES[2].window == timedelta(minutes=10)
    assert SECURITY_ALERT_RULES[2].threshold == 3
    assert SECURITY_ALERT_RULES[2].group_by_policy_reason is True
    assert {rule.version for rule in SECURITY_ALERT_RULES} == {"v1"}
    assert SECURITY_ALERT_MAX_WINDOW == timedelta(minutes=10)


def test_unknown_security_alert_rule_is_not_silently_accepted():
    from apps.shared.services.security_alert_rule_registry import (
        get_security_alert_rule,
    )

    assert get_security_alert_rule("unknown") is None
