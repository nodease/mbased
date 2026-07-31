from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol


class GoogleOAuthRefreshError(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


class GoogleOAuthRefreshProviderPort(Protocol):
    def refresh_access_token(
        self,
        *,
        client_id: str,
        client_secret: str,
        refresh_token: str,
    ) -> Mapping[str, Any]: ...


__all__ = ["GoogleOAuthRefreshError", "GoogleOAuthRefreshProviderPort"]
