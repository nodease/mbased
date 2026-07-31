from apps.shared.db.models.llm import LLMUsageLog


def test_llm_usage_latency_column_uses_public_name():
    assert "latency_ms" in LLMUsageLog.__table__.c
    legacy_missing_l_latency_column = "a" + "tency_ms"
    assert legacy_missing_l_latency_column not in LLMUsageLog.__table__.c
