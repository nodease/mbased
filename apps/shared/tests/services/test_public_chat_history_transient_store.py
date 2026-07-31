import asyncio

import apps.shared.services.public_chat_history_transient_store as transient_store
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
        self.close_calls = 0

    async def set(self, key, value, *, ex, nx):
        self.set_calls.append({"key": key, "value": value, "ex": ex, "nx": nx})
        if nx and key in self.values:
            return False
        self.values[key] = value.encode("utf-8")
        return True

    def eval(self, _script, key_count, key):
        assert key_count == 1
        return self.values.pop(key, None)

    def close(self):
        self.close_calls += 1


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
            timeout_seconds=0.1,
        )
        == history
    )
    assert (
        consume_public_chat_history(
            reference,
            redis_client=redis_client,
            timeout_seconds=0.1,
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


def test_public_history_consume_builds_and_closes_deadline_bounded_client(
    monkeypatch,
):
    redis_client = _FakeRedis()
    reference = "a" * 32
    redis_client.values[
        "nodease:public-chat-history:v1:" + reference
    ] = (
        b'[{"role":"user","content":"previous question"},'
        b'{"role":"assistant","content":"previous answer"}]'
    )
    captured = {}

    def bounded_client(*, socket_timeout_seconds):
        captured["socket_timeout_seconds"] = socket_timeout_seconds
        return redis_client

    monkeypatch.setattr(transient_store, "get_redis_client", bounded_client)

    result = consume_public_chat_history(reference, timeout_seconds=0.125)

    assert result == (
        {"role": "user", "content": "previous question"},
        {"role": "assistant", "content": "previous answer"},
    )
    assert captured == {"socket_timeout_seconds": 0.125}
    assert redis_client.close_calls == 1


def test_public_history_consume_classifies_store_unavailable():
    class _UnavailableRedis:
        def eval(self, *_args):
            raise ConnectionError("redis unavailable")

    with pytest.raises(PublicChatHistoryTransientStoreError) as exc_info:
        consume_public_chat_history(
            "a" * 32,
            redis_client=_UnavailableRedis(),
            timeout_seconds=0.1,
        )

    assert exc_info.value.code == "conversation.history_store_unavailable"


def test_public_history_consume_classifies_invalid_and_corrupt_values():
    with pytest.raises(PublicChatHistoryTransientStoreError) as invalid:
        consume_public_chat_history(
            "not-a-reference",
            redis_client=_FakeRedis(),
            timeout_seconds=0.1,
        )
    assert invalid.value.code == "conversation.history_reference_invalid"

    corrupt_redis = _FakeRedis()
    corrupt_redis.values["nodease:public-chat-history:v1:" + ("b" * 32)] = b"{"
    with pytest.raises(PublicChatHistoryTransientStoreError) as corrupt:
        consume_public_chat_history(
            "b" * 32,
            redis_client=corrupt_redis,
            timeout_seconds=0.1,
        )
    assert corrupt.value.code == "conversation.history_store_corrupt"


@pytest.mark.parametrize("timeout_seconds", [None, True, 0, -1, float("inf")])
def test_public_history_consume_rejects_invalid_timeout(timeout_seconds):
    with pytest.raises(PublicChatHistoryTransientStoreError) as exc_info:
        consume_public_chat_history("c" * 32, timeout_seconds=timeout_seconds)

    assert exc_info.value.code == "conversation.history_store_unavailable"
