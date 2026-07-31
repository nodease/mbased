from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol


class GoogleOAuthProviderError(RuntimeError):
    pass


class GoogleOAuthProviderPort(Protocol):
    async def exchange_authorization_code(
        self,
        *,
        client_id: str,
        client_secret: str,
        code: str,
        verifier: str,
        redirect_uri: str,
    ) -> Mapping[str, Any]: ...

    async def read_mailbox_email(self, *, access_token: str) -> str: ...


__all__ = ["GoogleOAuthProviderError", "GoogleOAuthProviderPort"]
