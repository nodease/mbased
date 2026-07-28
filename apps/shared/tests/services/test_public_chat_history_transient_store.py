import asyncio

import pytest
from apps.shared.services.public_chat_history_transient_store import (
    PublicChatHistoryTransientStoreError,
    consume_public_chat_history,
    store_public_chat_history,
)


class _FakeRedis:
    def __init__(self):
        self.values = {}
        self.set_calls = []

    async def set(self, key, value, *, ex, nx):
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

    reference = asyncio.run(
        store_public_chat_history(
            history,
            ttl_seconds=600,
            redis_client=redis_client,
        )
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


def test_public_history_store_times_out_without_blocking_gateway_event_loop():
    class _BlockingRedis:
        async def set(self, *_args, **_kwargs):
            await asyncio.Event().wait()

    async def store():
        return await store_public_chat_history(
            (),
            ttl_seconds=600,
            timeout_seconds=0.001,
            redis_client=_BlockingRedis(),
        )

    with pytest.raises(PublicChatHistoryTransientStoreError) as exc_info:
        asyncio.run(
            asyncio.wait_for(
                store(),
                timeout=0.1,
            )
        )

    assert exc_info.value.code == "conversation.history_store_unavailable"


def test_public_history_consume_classifies_store_unavailable():
    class _UnavailableRedis:
        def eval(self, *_args):
            raise ConnectionError("redis unavailable")

    with pytest.raises(PublicChatHistoryTransientStoreError) as exc_info:
        consume_public_chat_history(
            "a" * 32,
            redis_client=_UnavailableRedis(),
        )

    assert exc_info.value.code == "conversation.history_store_unavailable"


def test_public_history_consume_classifies_invalid_and_corrupt_values():
    with pytest.raises(PublicChatHistoryTransientStoreError) as invalid:
        consume_public_chat_history("not-a-reference", redis_client=_FakeRedis())
    assert invalid.value.code == "conversation.history_reference_invalid"

    corrupt_redis = _FakeRedis()
    corrupt_redis.values["nodease:public-chat-history:v1:" + ("b" * 32)] = b"{"
    with pytest.raises(PublicChatHistoryTransientStoreError) as corrupt:
        consume_public_chat_history("b" * 32, redis_client=corrupt_redis)
    assert corrupt.value.code == "conversation.history_store_corrupt"
