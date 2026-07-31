from scripts.ci.evaluate_job_results import ConditionalResult, evaluate_results


def test_allows_successful_selected_jobs_and_skipped_unselected_jobs():
    errors = evaluate_results(
        [("scope", "success"), ("alembic-graph", "success")],
        [
            ConditionalResult("client", True, "success"),
            ConditionalResult("gateway", False, "skipped"),
        ],
    )

    assert errors == []


def test_rejects_failed_required_job():
    errors = evaluate_results([("scope", "failure")], [])

    assert errors == ["required job scope ended with failure"]


def test_rejects_cancelled_or_skipped_selected_job():
    errors = evaluate_results(
        [("scope", "success")],
        [
            ConditionalResult("client", True, "cancelled"),
            ConditionalResult("gateway", True, "skipped"),
        ],
    )

    assert errors == [
        "selected job client ended with cancelled",
        "selected job gateway ended with skipped",
    ]


def test_rejects_invalid_or_failed_unselected_job_result():
    errors = evaluate_results(
        [("scope", "success")],
        [
            ConditionalResult("client", False, "failure"),
            ConditionalResult("gateway", False, "missing"),
        ],
    )

    assert errors == [
        "unselected job client ended with failure",
        "conditional job gateway has invalid result 'missing'",
    ]
