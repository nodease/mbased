from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Iterable

GMAIL_MODIFY_SCOPE = "https://www.googleapis.com/auth/gmail.modify"
GMAIL_OAUTH_SECRET_VERSION = 1


class MailOAuthSecretError(ValueError):
    def __init__(self, reason_code: str):
        super().__init__(reason_code)
        self.reason_code = reason_code


@dataclass(frozen=True)
class GmailOAuthSecret:
    refresh_token: str = field(repr=False)
    scopes: tuple[str, ...]
    version: int = GMAIL_OAUTH_SECRET_VERSION

    def serialize(self) -> str:
        if not self.refresh_token or any(
            char in self.refresh_token for char in ("\r", "\n", "\x00")
        ):
            raise MailOAuthSecretError("mail.oauth_secret_invalid")
        normalized_scopes = normalize_scopes(self.scopes)
        if GMAIL_MODIFY_SCOPE not in normalized_scopes:
            raise MailOAuthSecretError("mail.oauth_scope_insufficient")
        return json.dumps(
            {
                "refresh_token": self.refresh_token,
                "scopes": list(normalized_scopes),
                "version": self.version,
            },
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )

    @classmethod
    def parse(cls, raw: str) -> "GmailOAuthSecret":
        try:
            data = json.loads(raw)
        except (TypeError, json.JSONDecodeError) as exc:
            raise MailOAuthSecretError("mail.oauth_secret_invalid") from exc
        if (
            not isinstance(data, dict)
            or data.get("version") != GMAIL_OAUTH_SECRET_VERSION
        ):
            raise MailOAuthSecretError("mail.oauth_secret_invalid")
        refresh_token = data.get("refresh_token")
        scopes = data.get("scopes")
        if not isinstance(refresh_token, str) or not isinstance(scopes, list):
            raise MailOAuthSecretError("mail.oauth_secret_invalid")
        secret = cls(
            refresh_token=refresh_token,
            scopes=tuple(str(scope) for scope in scopes),
        )
        secret.serialize()
        return secret


def normalize_scopes(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        items: Iterable[Any] = value.split()
    elif isinstance(value, Iterable):
        items = value
    else:
        raise MailOAuthSecretError("mail.oauth_scope_insufficient")
    normalized = tuple(
        sorted({str(scope).strip() for scope in items if str(scope).strip()})
    )
    if not normalized:
        raise MailOAuthSecretError("mail.oauth_scope_insufficient")
    return normalized
