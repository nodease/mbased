from __future__ import annotations

import pytest

from scripts.experiments.model_routing.secret_input import read_required_secret


def test_read_required_secret_returns_injected_value_without_transforming_it():
    candidate = "synthetic-secret-value"

    assert (
        read_required_secret(
            "MODEL_ROUTING_TEST_SECRET",
            environ={"MODEL_ROUTING_TEST_SECRET": candidate},
        )
        == candidate
    )


@pytest.mark.parametrize("environment", [{}, {"MODEL_ROUTING_TEST_SECRET": ""}])
def test_read_required_secret_reports_only_the_missing_variable_name(environment):
    with pytest.raises(ValueError) as error:
        read_required_secret("MODEL_ROUTING_TEST_SECRET", environ=environment)

    assert "MODEL_ROUTING_TEST_SECRET" in str(error.value)
    assert "synthetic-secret-value" not in str(error.value)
