from __future__ import annotations

import math

from apps.gateway import knowledge_worker_health


def test_local_worker_nodename_uses_exact_knowledge_prefix() -> None:
    assert (
        knowledge_worker_health.local_worker_nodename("worker-pod-1")
        == "knowledge@worker-pod-1"
    )


def test_readiness_accepts_only_the_exact_local_worker_reply() -> None:
    calls = []

    def ping(*, destination, timeout):
        calls.append((destination, timeout))
        return [{"knowledge@worker-pod-1": {"ok": "pong"}}]

    assert knowledge_worker_health.is_local_worker_ready(
        hostname="worker-pod-1",
        ping=ping,
        timeout_seconds=1.5,
    )
    assert calls == [(["knowledge@worker-pod-1"], 1.5)]


def test_readiness_rejects_a_reply_from_another_replica() -> None:
    assert not knowledge_worker_health.is_local_worker_ready(
        hostname="worker-pod-1",
        ping=lambda **_kwargs: [
            {"knowledge@worker-pod-2": {"ok": "pong"}}
        ],
    )


def test_readiness_rejects_malformed_or_empty_replies() -> None:
    replies = (
        None,
        {},
        [],
        [None],
        [{"knowledge@worker-pod-1": None}],
        [{"knowledge@worker-pod-1": {"ok": "not-pong"}}],
    )

    for reply in replies:
        assert not knowledge_worker_health.is_local_worker_ready(
            hostname="worker-pod-1",
            ping=lambda reply=reply, **_kwargs: reply,
        )


def test_readiness_rejects_invalid_timeout_without_calling_broker() -> None:
    calls = []

    for timeout_seconds in (0, -1, math.inf, math.nan):
        assert not knowledge_worker_health.is_local_worker_ready(
            hostname="worker-pod-1",
            ping=lambda **_kwargs: calls.append("ping"),
            timeout_seconds=timeout_seconds,
        )

    assert calls == []


def test_readiness_rejects_empty_hostname_without_calling_broker() -> None:
    calls = []

    assert not knowledge_worker_health.is_local_worker_ready(
        hostname="   ",
        ping=lambda **_kwargs: calls.append("ping"),
    )
    assert calls == []


def test_readiness_suppresses_library_output_and_exception_details(capsys) -> None:
    def ping(**_kwargs):
        print("unsafe diagnostic")
        raise RuntimeError("unsafe exception detail")

    assert not knowledge_worker_health.is_local_worker_ready(
        hostname="worker-pod-1",
        ping=ping,
    )
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_main_returns_only_process_status(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        knowledge_worker_health,
        "is_local_worker_ready",
        lambda: True,
    )
    assert knowledge_worker_health.main() == 0

    monkeypatch.setattr(
        knowledge_worker_health,
        "is_local_worker_ready",
        lambda: False,
    )
    assert knowledge_worker_health.main() == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""
