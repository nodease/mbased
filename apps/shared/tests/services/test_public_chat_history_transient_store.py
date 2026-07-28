from apps.shared.services.public_chat_history_transient_store import (
    consume_public_chat_history,
    store_public_chat_history,
)


class _FakeRedis:
    def __init__(self):
        self.values = {}
        self.set_calls = []

    def set(self, key, value, *, ex, nx):
        self.set_calls.append({"key": key, "value": value, "ex": ex, "nx": nx})
        if nx and key in self.values:
            return False
        self.values[key] = value.encode("utf-8")
        return True

    def eval(self, _script, key_count, key):
        assert key_count == 1
        return self.values.pop(key, None)


def test_public_history_store_uses_ttl_and_one_time_consume():
    redis_client = _FakeRedis()
    history = (
        {"role": "user", "content": "이전 질문"},
        {"role": "assistant", "content": "이전 답변"},
    )

    reference = store_public_chat_history(
        history,
        ttl_seconds=600,
        redis_client=redis_client,
    )

    assert redis_client.set_calls[0]["ex"] == 600
    assert redis_client.set_calls[0]["nx"] is True
    assert "이전 질문" not in reference
    assert (
        consume_public_chat_history(
            reference,
            redis_client=redis_client,
        )
        == history
    )
    assert (
        consume_public_chat_history(
            reference,
            redis_client=redis_client,
        )
        is None
    )
