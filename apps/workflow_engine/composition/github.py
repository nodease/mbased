from apps.workflow_engine.adapters.providers.github import (
    GithubCommentEffectAdapter,
    GithubReadProvider,
)


def build_github_read_provider() -> GithubReadProvider:
    return GithubReadProvider()


def build_github_comment_effect_adapter() -> GithubCommentEffectAdapter:
    return GithubCommentEffectAdapter()


__all__ = [
    "build_github_comment_effect_adapter",
    "build_github_read_provider",
]
