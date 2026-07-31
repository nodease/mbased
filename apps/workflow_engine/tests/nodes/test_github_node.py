from __future__ import annotations

from unittest.mock import Mock

import pytest

from apps.workflow_engine.adapters.providers.github import GithubProviderError
from apps.workflow_engine.workflow.nodes.github.entities import (
    GithubAction,
    GithubNodeData,
    GithubVariable,
)
from apps.workflow_engine.workflow.nodes.github.github_node import GithubNode


class _ReadProvider:
    def __init__(self, *, error=None):
        self.error = error
        self.calls = []

    def get_pull_request(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return (
            {
                "title": "Add new feature",
                "body": "This PR adds a new feature",
                "state": "open",
                "number": 123,
                "diff_url": "https://github.com/owner/repo/pull/123.diff",
            },
            [
                {
                    "filename": "src/app.py",
                    "status": "modified",
                    "additions": 10,
                    "deletions": 5,
                    "changes": 15,
                    "patch": "@@ -1,5 +1,10 @@\n+new code",
                }
            ],
        )


class _CommentAdapter:
    def __init__(self):
        self.calls = []

    def create_comment(self, request):
        self.calls.append(request)
        return {
            "comment_id": 456789,
            "comment_url": "https://github.com/owner/repo/pull/123#issuecomment-456789",
            "comment_body": request.comment_body,
        }


def _node(data, *, read_provider=None, comment_adapter=None, extra_context=None):
    context = dict(extra_context or {})
    if read_provider is not None:
        context["github_read_provider_factory"] = lambda: read_provider
    if comment_adapter is not None:
        context["github_comment_effect_adapter_factory"] = lambda: comment_adapter
    return GithubNode(id="github-1", data=data, execution_context=context)


def test_get_pr_success():
    provider = _ReadProvider()
    node = _node(
        GithubNodeData(
            title="GitHub",
            action=GithubAction.GET_PR,
            api_token="synthetic-token",
            repo_owner="facebook",
            repo_name="react",
            pr_number="123",
        ),
        read_provider=provider,
    )

    result = node._run(inputs={})

    assert result["pr_title"] == "Add new feature"
    assert result["files_count"] == 1
    assert result["files"][0]["filename"] == "src/app.py"
    assert result["diff_url"].endswith("/123.diff")
    assert provider.calls[0]["repo_owner"] == "facebook"


def test_get_pr_resolves_opaque_secret_reference_at_runtime():
    provider = _ReadProvider()
    resolver = Mock(return_value="resolved-runtime-token")
    node = _node(
        GithubNodeData(
            title="GitHub",
            action=GithubAction.GET_PR,
            api_token="workflow-node-secret://00000000-0000-4000-8000-000000000001",
            repo_owner="owner",
            repo_name="repo",
            pr_number="1",
        ),
        read_provider=provider,
        extra_context={
            "workflow_id": "00000000-0000-4000-8000-000000000010",
            "organization_id": "00000000-0000-4000-8000-000000000020",
            "workflow_node_secret_resolver": resolver,
        },
    )

    node._run(inputs={})

    assert provider.calls[0]["token"] == "resolved-runtime-token"
    resolver.assert_called_once()


def test_comment_pr_success():
    adapter = _CommentAdapter()
    node = _node(
        GithubNodeData(
            title="GitHub",
            action=GithubAction.COMMENT_PR,
            api_token="synthetic-token",
            repo_owner="facebook",
            repo_name="react",
            pr_number="123",
            comment_body="Great work!",
        ),
        comment_adapter=adapter,
    )

    result = node._run(inputs={})

    assert result["comment_id"] == 456789
    assert result["comment_body"] == "Great work!"
    assert adapter.calls[0].repo_name == "react"


def test_variable_substitution_simple():
    adapter = _CommentAdapter()
    node = _node(
        GithubNodeData(
            title="GitHub",
            action=GithubAction.COMMENT_PR,
            api_token="synthetic-token",
            repo_owner="facebook",
            repo_name="react",
            pr_number="123",
            comment_body="Review result: {{ review }}",
            referenced_variables=[
                GithubVariable(name="review", value_selector=["llm-1", "text"])
            ],
        ),
        comment_adapter=adapter,
    )

    node._run(inputs={"llm-1": {"text": "LGTM!"}})

    assert adapter.calls[0].comment_body == "Review result: LGTM!"


@pytest.mark.parametrize(
    "reason_code", ["github.provider_rejected", "github.unavailable"]
)
def test_provider_failure_uses_safe_runtime_error(reason_code):
    node = _node(
        GithubNodeData(
            title="GitHub",
            action=GithubAction.GET_PR,
            api_token="synthetic-token",
            repo_owner="owner",
            repo_name="repo",
            pr_number="123",
        ),
        read_provider=_ReadProvider(error=GithubProviderError(reason_code)),
    )

    with pytest.raises(RuntimeError, match="^GitHub API 오류$"):
        node._run(inputs={})
