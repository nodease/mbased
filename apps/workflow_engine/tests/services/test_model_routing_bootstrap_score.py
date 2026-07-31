from apps.shared.services.model_routing_global_profile_catalog import (
    OFFICIAL_PROVIDER_CATALOG,
)
from apps.workflow_engine.services.model_routing_bootstrap_score import (
    BOOTSTRAP_MODEL_ROUTING_SCORES,
    BOOTSTRAP_ROUTING_SCORE_VERSION,
    model_bootstrap_score,
    required_bootstrap_score,
)


def test_required_score_uses_axis_average_for_noncritical_request():
    assert required_bootstrap_score(
        {
            "task_complexity": 2,
            "decision_impact": 1,
            "evidence_synthesis": 1,
        }
    ) == 4 / 3


def test_required_score_preserves_critical_axis_floors():
    assert required_bootstrap_score(
        {
            "task_complexity": 3,
            "decision_impact": 0,
            "evidence_synthesis": 0,
        }
    ) == 2.4
    assert required_bootstrap_score(
        {
            "task_complexity": 0,
            "decision_impact": 3,
            "evidence_synthesis": 0,
        }
    ) == 2.9
    assert required_bootstrap_score(
        {
            "task_complexity": 0,
            "decision_impact": 0,
            "evidence_synthesis": 3,
        }
    ) == 2.5


def test_model_scores_are_real_valued_versioned_priors():
    assert BOOTSTRAP_ROUTING_SCORE_VERSION == "manual-bootstrap-score-v1"
    assert model_bootstrap_score("gpt-4o-mini") == 1.0
    assert model_bootstrap_score("gpt-5.4-mini") == 2.35
    assert model_bootstrap_score("gpt-5.6") == 3.0
    assert model_bootstrap_score("gpt-5.6-sol") == 3.0


def test_every_official_routing_model_has_an_explicit_bootstrap_score():
    assert set(OFFICIAL_PROVIDER_CATALOG) <= set(BOOTSTRAP_MODEL_ROUTING_SCORES)
