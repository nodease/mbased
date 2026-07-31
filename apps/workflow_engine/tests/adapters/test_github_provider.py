from __future__ import annotations

import json

import pytest

from apps.shared.services.outbound_operation_http import OperationHttpResponse
from apps.shared.services.outbound_operation_policy import (
    GITHUB_ISSUE_COMMENT_CREATE,
    GITHUB_PULL_REQUEST_READ,
    require_outbound_operation_profile,
)
from apps.workflow_engine.adapters.providers.github import (
    GithubCommentEffectAdapter,
    GithubCommentRequest,
    GithubProviderError,
    GithubReadProvider,
)
from apps.workflow_engine.domain.external_effect import (
    EffectInvocationFailure,
    EffectOutcome,
    PreparedProviderCall,
)


class _Requester:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def request(self, **kwargs):
        self.calls.append(kwargs)
        return self.responses.pop(0)


def _response(status_code, payload):
    return OperationHttpResponse(
        status_code=status_code,
        headers={"content-type": "application/json"},
        content=json.dumps(payload).encode("utf-8"),
    )


def test_read_provider_uses_two_fixed_get_operations() -> None:
    requester = _Requester(
        [
            _response(
                200,
                {
                    "title": "PR",
                    "body": None,
                    "state": "open",
                    "number": 1,
                    "diff_url": "https://github.example.test/diff",
                },
            ),
            _response(
                200,
                [
                    {
                        "filename": "src/app.py",
                        "status": "modified",
                        "additions": 1,
                        "deletions": 0,
                        "changes": 1,
                    }
                ],
            ),
        ]
    )
    provider = GithubReadProvider(requester=requester)

    pr, files = provider.get_pull_request(
        token="synthetic-token",
        repo_owner="owner",
        repo_name="repo",
        pr_number=1,
    )

    assert pr["title"] == "PR"
    assert files[0]["filename"] == "src/app.py"
    assert [call["operation_id"] for call in requester.calls] == [
        GITHUB_PULL_REQUEST_READ,
        GITHUB_PULL_REQUEST_READ,
    ]
    assert requester.calls[1]["url"].endswith("/pulls/1/files")


@pytest.mark.parametrize("segment", ["..", "owner/repo", "owner\\repo", "owner?x=1"])
def test_read_provider_rejects_path_changing_repository_segments(segment) -> None:
    requester = _Requester([])
    provider = GithubReadProvider(requester=requester)

    with pytest.raises(GithubProviderError) as captured:
        provider.get_pull_request(
            token="synthetic-token",
            repo_owner=segment,
            repo_name="repo",
            pr_number=1,
        )

    assert captured.value.reason_code == "github.request_invalid"
    assert requester.calls == []


def test_read_provider_rejects_incomplete_provider_projection() -> None:
    requester = _Requester(
        [
            _response(200, {"title": "PR"}),
            _response(200, [{"filename": "src/app.py"}]),
        ]
    )
    provider = GithubReadProvider(requester=requester)

    with pytest.raises(GithubProviderError) as captured:
        provider.get_pull_request(
            token="synthetic-token",
            repo_owner="owner",
            repo_name="repo",
            pr_number=1,
        )

    assert captured.value.reason_code == "github.response_invalid"


def test_comment_adapter_revalidates_forged_provider_call_before_transport() -> None:
    requester = _Requester([])
    adapter = GithubCommentEffectAdapter(requester=requester)
    call = PreparedProviderCall(
        request=GithubCommentRequest(
            token="synthetic-token",
            repo_owner="../other",
            repo_name="repo",
            pr_number=1,
            comment_body="comment",
        ),
        idempotency_key=None,
        profile=adapter.profile,
    )

    with pytest.raises(EffectInvocationFailure) as captured:
        adapter.invoke_effect(call)

    assert captured.value.outcome is EffectOutcome.FAILED_BEFORE_EFFECT
    assert captured.value.error_code == "invalid_prepared_request"
    assert requester.calls == []


def test_comment_prepare_rejects_wire_json_body_above_operation_limit() -> None:
    requester = _Requester([])
    adapter = GithubCommentEffectAdapter(requester=requester)
    max_request_bytes = require_outbound_operation_profile(
        GITHUB_ISSUE_COMMENT_CREATE
    ).policy.max_request_bytes
    escaped_body = '"' * (max_request_bytes // 2)

    assert len(escaped_body.encode("utf-8")) <= max_request_bytes
    with pytest.raises(ValueError, match="^invalid GitHub comment request$"):
        adapter.prepare_effect(
            GithubCommentRequest(
                token="synthetic-token",
                repo_owner="owner",
                repo_name="repo",
                pr_number=1,
                comment_body=escaped_body,
            )
        )

    assert requester.calls == []


def test_comment_prepare_accepts_wire_json_body_at_operation_limit() -> None:
    adapter = GithubCommentEffectAdapter(requester=_Requester([]))
    max_request_bytes = require_outbound_operation_profile(
        GITHUB_ISSUE_COMMENT_CREATE
    ).policy.max_request_bytes
    wire_overhead = len(
        json.dumps(
            {"body": ""},
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
    )
    comment_body = "a" * (max_request_bytes - wire_overhead)

    prepared = adapter.prepare_effect(
        GithubCommentRequest(
            token="synthetic-token",
            repo_owner="owner",
            repo_name="repo",
            pr_number=1,
            comment_body=comment_body,
        )
    )

    assert prepared.request.comment_body == comment_body


def test_comment_prepare_rejects_non_utf8_comment_without_raw_encode_error() -> None:
    adapter = GithubCommentEffectAdapter(requester=_Requester([]))

    with pytest.raises(ValueError, match="^invalid GitHub comment request$"):
        adapter.prepare_effect(
            GithubCommentRequest(
                token="synthetic-token",
                repo_owner="owner",
                repo_name="repo",
                pr_number=1,
                comment_body="invalid-\ud800-comment",
            )
        )


def test_comment_adapter_uses_the_mutating_operation_without_transport_retry() -> None:
    requester = _Requester(
        [
            _response(
                201,
                {
                    "id": 1,
                    "html_url": "https://github.example.test/comment/1",
                    "body": "comment",
                },
            )
        ]
    )
    adapter = GithubCommentEffectAdapter(requester=requester)

    result = adapter.create_comment(
        GithubCommentRequest(
            token="synthetic-token",
            repo_owner="owner",
            repo_name="repo",
            pr_number=1,
            comment_body="comment",
        )
    )

    assert result["comment_id"] == 1
    assert requester.calls[0]["operation_id"] == GITHUB_ISSUE_COMMENT_CREATE
    assert len(requester.calls) == 1
    assert adapter.trace_metadata["http"]["request_size"] == len(
        json.dumps(
            {"body": "comment"},
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
    )
