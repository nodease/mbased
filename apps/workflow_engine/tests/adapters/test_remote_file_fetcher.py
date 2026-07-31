from __future__ import annotations

import os

import pytest

from apps.shared.services.egress_guard import EgressGuardError
from apps.shared.services.outbound_operation_policy import WORKFLOW_REMOTE_FILE_FETCH
from apps.workflow_engine.adapters import remote_file as remote_file_module
from apps.workflow_engine.adapters.remote_file import GuardedRemoteFileFetcher
from apps.workflow_engine.application.remote_file import RemoteFileFetchError


def test_fetcher_uses_workflow_remote_file_operation(monkeypatch, tmp_path) -> None:
    captured = {}
    target = tmp_path / "download.pdf"
    target.write_bytes(b"synthetic")

    def download(url, **kwargs):
        captured["url"] = url
        captured.update(kwargs)
        return str(target)

    monkeypatch.setattr(remote_file_module, "download_url_to_temp_file", download)

    result = GuardedRemoteFileFetcher().fetch_to_temp(
        "https://files.example/policy.pdf?opaque=value"
    )

    assert result == str(target)
    assert captured["operation_id"] == WORKFLOW_REMOTE_FILE_FETCH
    assert captured["suffix"] == ".pdf"


def test_fetcher_maps_guard_failure_without_raw_url(monkeypatch) -> None:
    sensitive_url = "https://files.example/policy.pdf?signature=must-not-leak"
    monkeypatch.setattr(
        remote_file_module,
        "download_url_to_temp_file",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            EgressGuardError("egress.private_target")
        ),
    )

    with pytest.raises(RemoteFileFetchError) as captured:
        GuardedRemoteFileFetcher().fetch_to_temp(sensitive_url)

    assert captured.value.code == "remote_file.target_denied"
    assert sensitive_url not in str(captured.value)


def test_fetcher_removes_partial_file_when_downloader_reports_path(
    monkeypatch,
    tmp_path,
) -> None:
    partial = tmp_path / "partial.pdf"
    partial.write_bytes(b"partial")
    error = EgressGuardError("egress.response_too_large")
    error.partial_file_path = str(partial)
    monkeypatch.setattr(
        remote_file_module,
        "download_url_to_temp_file",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(error),
    )

    with pytest.raises(RemoteFileFetchError):
        GuardedRemoteFileFetcher().fetch_to_temp("https://files.example/policy.pdf")

    assert not os.path.exists(partial)


def test_fetcher_maps_http_error_status_to_safe_response_failure(monkeypatch) -> None:
    monkeypatch.setattr(
        remote_file_module,
        "download_url_to_temp_file",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            EgressGuardError("egress.http_status_rejected")
        ),
    )

    with pytest.raises(RemoteFileFetchError) as captured:
        GuardedRemoteFileFetcher().fetch_to_temp(
            "https://files.example/missing.txt?opaque=value"
        )

    assert captured.value.code == "remote_file.response_rejected"
