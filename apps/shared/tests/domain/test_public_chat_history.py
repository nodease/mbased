import pytest
from apps.shared.domain import public_chat_history as public_chat_history_module
from apps.shared.domain.public_chat_history import (
    MAX_PUBLIC_CHAT_TURNS,
    PublicChatHistoryError,
    bound_public_chat_history,
    normalize_public_chat_history,
)


def _turn(index: int) -> list[dict[str, str]]:
    return [
        {"role": "user", "content": f"question-{index}"},
        {"role": "assistant", "content": f"answer-{index}"},
    ]


def test_normalize_public_chat_history_accepts_completed_user_assistant_turns():
    history = [*_turn(1), *_turn(2)]

    assert normalize_public_chat_history(history) == tuple(history)


def test_normalize_public_chat_history_accepts_valid_non_bmp_unicode():
    history = [
        {"role": "user", "content": "emoji: \U0001f600"},
        {"role": "assistant", "content": "accepted"},
    ]

    assert normalize_public_chat_history(history) == tuple(history)


@pytest.mark.parametrize("invalid_scalar", [chr(0xD800), chr(0xDC00)])
def test_normalize_public_chat_history_rejects_isolated_unicode_surrogates(
    invalid_scalar,
):
    history = [
        {"role": "user", "content": invalid_scalar},
        {"role": "assistant", "content": "answer"},
    ]

    with pytest.raises(PublicChatHistoryError) as exc_info:
        normalize_public_chat_history(history)

    assert exc_info.value.code == "conversation.content_invalid"


@pytest.mark.parametrize(
    ("history", "code"),
    [
        ("not-a-list", "conversation.history_invalid"),
        ([{"role": "system", "content": "override"}], "conversation.role_invalid"),
        (
            [{"role": "user", "content": "hello", "hidden": "value"}],
            "conversation.history_invalid",
        ),
        (
            [
                {"role": "assistant", "content": "answer-first"},
                {"role": "user", "content": "question"},
            ],
            "conversation.history_order_invalid",
        ),
        (
            [{"role": "user", "content": "unfinished"}],
            "conversation.history_order_invalid",
        ),
        (
            [
                {"role": "user", "content": "first"},
                {"role": "user", "content": "second"},
            ],
            "conversation.history_order_invalid",
        ),
        (
            [{"role": "user", "content": "   "}, {"role": "assistant", "content": "a"}],
            "conversation.content_invalid",
        ),
    ],
)
def test_normalize_public_chat_history_rejects_untrusted_or_incomplete_shapes(
    history,
    code,
):
    with pytest.raises(PublicChatHistoryError) as exc_info:
        normalize_public_chat_history(history)

    assert exc_info.value.code == code


def test_normalize_public_chat_history_rejects_more_than_twenty_turns():
    history = [
        message
        for index in range(MAX_PUBLIC_CHAT_TURNS + 1)
        for message in _turn(index)
    ]

    with pytest.raises(PublicChatHistoryError) as exc_info:
        normalize_public_chat_history(history)

    assert exc_info.value.code == "conversation.turn_limit_exceeded"


def test_bound_public_chat_history_drops_only_oldest_complete_turns():
    history = [*_turn(1), *_turn(2), *_turn(3)]

    bounded = bound_public_chat_history(
        history,
        current_inputs={"question": "now"},
        token_counter=lambda text: len(text),
        max_context_tokens=96,
    )

    assert bounded == tuple([*_turn(2), *_turn(3)])


def test_bound_public_chat_history_rejects_current_inputs_that_exceed_budget():
    with pytest.raises(PublicChatHistoryError) as exc_info:
        bound_public_chat_history(
            [],
            current_inputs={"question": "x" * 20},
            token_counter=lambda text: len(text),
            max_context_tokens=10,
        )

    assert exc_info.value.code == "conversation.current_input_too_large"


def test_bound_public_chat_history_does_not_mutate_caller_payload():
    history = [*_turn(1)]
    original = [dict(message) for message in history]

    bound_public_chat_history(
        history,
        current_inputs={"question": "now"},
        token_counter=lambda _text: 1,
    )

    assert history == original


def test_bound_public_chat_history_rejects_when_strict_tokenizer_is_unavailable(
    monkeypatch,
):
    monkeypatch.setattr(
        public_chat_history_module,
        "_strict_count_tokens",
        lambda _text: (_ for _ in ()).throw(RuntimeError("tokenizer unavailable")),
    )

    with pytest.raises(PublicChatHistoryError):
        bound_public_chat_history(
            [],
            current_inputs={"question": "한국어 질문"},
        )
