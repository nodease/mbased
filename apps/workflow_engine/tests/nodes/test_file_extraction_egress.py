from __future__ import annotations

import inspect

import pytest

from apps.workflow_engine.application.remote_file import RemoteFileFetchError
from apps.workflow_engine.workflow.nodes.file_extraction import (
    FileExtractionNode,
    FileExtractionNodeData,
)


def _node() -> FileExtractionNode:
    return FileExtractionNode(
        "file",
        FileExtractionNodeData(
            title="File",
            referenced_variables=[
                {"name": "document", "value_selector": ["input", "path"]}
            ],
        ),
    )


def test_remote_file_uses_injected_port_and_removes_temp_file(
    tmp_path,
    monkeypatch,
) -> None:
    temp_file = tmp_path / "remote.pdf"
    temp_file.write_bytes(b"synthetic")
    calls: list[str] = []

    class Fetcher:
        def fetch_to_temp(self, url: str) -> str:
            calls.append(url)
            return str(temp_file)

    node = _node()
    node.bind_remote_file_fetcher(Fetcher())
    monkeypatch.setattr(node, "_extract_text_sync", lambda *_args: "extracted")

    result = node.execute({"input": {"path": "https://files.example/policy.pdf"}})

    assert result == {"document": "extracted"}
    assert calls == ["https://files.example/policy.pdf"]
    assert not temp_file.exists()


def test_remote_file_fails_closed_when_runtime_dependency_is_missing() -> None:
    node = _node()

    with pytest.raises(RemoteFileFetchError) as captured:
        node.execute({"input": {"path": "https://files.example/policy.pdf"}})

    assert captured.value.code == "remote_file.fetcher_unavailable"


def test_remote_file_failure_does_not_expose_url_or_provider_exception() -> None:
    sensitive_url = "https://files.example/policy.pdf?signature=must-not-leak"

    class Fetcher:
        def fetch_to_temp(self, _url: str) -> str:
            raise RemoteFileFetchError("remote_file.connection_failed")

    node = _node()
    node.bind_remote_file_fetcher(Fetcher())

    with pytest.raises(RemoteFileFetchError) as captured:
        node.execute({"input": {"path": sensitive_url}})

    assert captured.value.code == "remote_file.connection_failed"
    assert sensitive_url not in str(captured.value)
    assert "signature" not in str(captured.value)


def test_parser_failure_does_not_expose_local_or_remote_path(monkeypatch) -> None:
    node = _node()
    monkeypatch.setattr(
        "apps.workflow_engine.workflow.nodes.file_extraction.file_extraction_node.pymupdf4llm.to_markdown",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("provider detail with path")
        ),
    )

    with pytest.raises(ValueError) as captured:
        node._extract_text_sync(
            "C:/sensitive/location.pdf",
            "document",
            "https://files.example/policy.pdf?signature=must-not-leak",
        )

    message = str(captured.value)
    assert "sensitive" not in message
    assert "signature" not in message
    assert "provider detail" not in message


def test_file_extraction_node_has_no_direct_http_client_import() -> None:
    source = inspect.getsource(
        __import__(
            "apps.workflow_engine.workflow.nodes.file_extraction.file_extraction_node",
            fromlist=["FileExtractionNode"],
        )
    )

    assert "import requests" not in source
    assert "requests.get" not in source
