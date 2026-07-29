from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from re import Pattern


class CsrfRoutePolicyKind(str, Enum):
    COOKIE_AUTHENTICATED = "cookie_authenticated"
    PRE_AUTH_SESSION = "pre_auth_session"
    PUBLIC_ANONYMOUS = "public_anonymous"
    SERVER_CREDENTIAL = "server_credential"
    OAUTH_STATE = "oauth_state"


class CsrfContentKind(str, Enum):
    JSON = "json"
    BODY_OPTIONAL = "body_optional"
    MULTIPART = "multipart"
    UNRESTRICTED = "unrestricted"


@dataclass(frozen=True)
class CsrfRoutePolicy:
    method: str
    path_template: str
    path_pattern: Pattern[str]
    policy_kind: CsrfRoutePolicyKind
    content_kind: CsrfContentKind

    @property
    def key(self) -> tuple[str, str]:
        return (self.method, self.path_template)

    @property
    def requires_csrf(self) -> bool:
        return self.policy_kind in {
            CsrfRoutePolicyKind.COOKIE_AUTHENTICATED,
            CsrfRoutePolicyKind.PRE_AUTH_SESSION,
        }


class CsrfRoutePolicyRegistry:
    def __init__(self, policies: tuple[CsrfRoutePolicy, ...]):
        by_key: dict[tuple[str, str], CsrfRoutePolicy] = {}
        for policy in policies:
            normalized_key = (policy.method.upper(), policy.path_template)
            if normalized_key in by_key:
                raise ValueError(f"Duplicate CSRF route policy: {normalized_key!r}")
            by_key[normalized_key] = policy
        self._policies = tuple(policies)
        self._by_key = by_key

    @property
    def policies(self) -> tuple[CsrfRoutePolicy, ...]:
        return self._policies

    def by_key(self, method: str, path_template: str) -> CsrfRoutePolicy:
        return self._by_key[(method.upper(), path_template)]

    def match(self, method: str, path: str) -> CsrfRoutePolicy | None:
        normalized_method = method.upper()
        for policy in self._policies:
            if (
                policy.method == normalized_method
                and policy.path_pattern.fullmatch(path) is not None
            ):
                return policy
        return None
