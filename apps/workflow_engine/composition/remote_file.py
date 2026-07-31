from apps.workflow_engine.adapters.remote_file import GuardedRemoteFileFetcher
from apps.workflow_engine.application.remote_file import RemoteFileFetcher


def build_remote_file_fetcher() -> RemoteFileFetcher:
    return GuardedRemoteFileFetcher()


__all__ = ["build_remote_file_fetcher"]
