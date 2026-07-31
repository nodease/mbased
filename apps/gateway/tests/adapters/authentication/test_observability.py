from apps.gateway.adapters.authentication.observability import LoginObservability


def test_observability_replaces_unbounded_labels_with_unknown():
    LoginObservability._local_counters.clear()

    LoginObservability.record(
        outcome="raw-account@example.com",
        policy_version="unbounded-policy",
        operation="198.51.100.1",
    )

    assert LoginObservability._local_counters == {
        ("unknown", "unknown", "unknown"): 1
    }
