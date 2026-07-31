import pytest
from apps.log_system.tasks import _parse_rag_answer_purge_limit


def test_parse_rag_answer_purge_limit_uses_default():
    assert _parse_rag_answer_purge_limit({}) == 1000


def test_parse_rag_answer_purge_limit_accepts_numeric_string():
    assert _parse_rag_answer_purge_limit({"limit": "25"}) == 25


@pytest.mark.parametrize("limit", [0, -3, 5001, "invalid", True])
def test_parse_rag_answer_purge_limit_rejects_invalid_values(limit):
    with pytest.raises(ValueError):
        _parse_rag_answer_purge_limit({"limit": limit})
