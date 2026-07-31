from __future__ import annotations

from typing import Protocol


class RemoteFileFetchError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__("Remote file could not be fetched.")
        self.code = code


class RemoteFileFetcher(Protocol):
    def fetch_to_temp(self, url: str) -> str: ...


__all__ = ["RemoteFileFetchError", "RemoteFileFetcher"]
