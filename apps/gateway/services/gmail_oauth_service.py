from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import time
import uuid
from typing import Any, MutableMapping, NoReturn
from urllib.parse import urlencode, urlparse

from sqlalchemy.orm import Session

from apps.gateway.application.google_oauth import (
    GoogleOAuthProviderError,
    GoogleOAuthProviderPort,
)
from apps.gateway.services.mail_credential_service import (
    MailCredentialPermissionDenied,
    MailCredentialService,
    MailCredentialServiceError,
)
from apps.shared.domain.mail_oauth import (
    GMAIL_MODIFY_SCOPE,
    MailOAuthSecretError,
    normalize_scopes,
)
from apps.shared.schemas.mail_credential import MailCredentialResponse
from apps.shared.services.permissions import has_organization_manager_permission

GOOGLE_AUTHORIZATION_URL = "https://accounts.google.com/o/oauth2/v2/auth"
SESSION_KEY = "gmail_mail_oauth_flow"
FLOW_TTL_SECONDS = 10 * 60
MAX_PENDING_FLOWS = 5


class GmailOAuthServiceError(MailCredentialServiceError):
    status_code = 400
    code = "mail.oauth_failed"
    detail = "Gmail authorization failed."


class GmailOAuthConfigurationMissing(GmailOAuthServiceError):
    status_code = 503
    code = "mail.oauth_configuration_missing"
    detail = "Gmail authorization is not configured."


class GmailOAuthStateInvalid(GmailOAuthServiceError):
    code = "mail.oauth_state_invalid"
    detail = "Gmail authorization state is invalid."


class GmailOAuthFlowExpired(GmailOAuthServiceError):
    code = "mail.oauth_flow_expired"
    detail = "Gmail authorization flow expired."


class GmailOAuthTokenExchangeFailed(GmailOAuthServiceError):
    code = "mail.oauth_token_exchange_failed"
    detail = "Gmail authorization could not be completed."


class GmailOAuthScopeInsufficient(GmailOAuthServiceError):
    code = "mail.oauth_scope_insufficient"
    detail = "Required Gmail mailbox permission was not granted."


class GmailOAuthRefreshTokenRequired(GmailOAuthServiceError):
    code = "mail.oauth_refresh_token_required"
    detail = "Gmail offline authorization is required."


class GmailOAuthCancelled(GmailOAuthServiceError):
    code = "mail.oauth_cancelled"
    detail = "Gmail authorization was cancelled."


class GmailOAuthService:
    def __init__(
        self,
        db: Session,
        *,
        client_id: str | None = None,
        client_secret: str | None = None,
        provider: GoogleOAuthProviderPort | None = None,
        now=time.time,
        session_signing_key: str | None = None,
    ) -> None:
        self._db = db
        self._client_id = (client_id or os.getenv("GOOGLE_CLIENT_ID", "")).strip()
        self._client_secret = (
            client_secret or os.getenv("GOOGLE_CLIENT_SECRET", "")
        ).strip()
        self._provider = provider
        self._now = now
        self._session_signing_key = (
            session_signing_key or os.getenv("SECRET_KEY", "")
        ).strip()

    def start(
        self,
        *,
        actor_id: uuid.UUID,
        organization_id: uuid.UUID,
        credential_name: str,
        redirect_uri: str,
        session: MutableMapping[str, Any],
    ) -> str:
        self._require_configuration()
        if not has_organization_manager_permission(self._db, actor_id, organization_id):
            raise MailCredentialPermissionDenied()
        state = secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(64)
        challenge = (
            base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest())
            .rstrip(b"=")
            .decode("ascii")
        )
        flow = {
            "actor_id": str(actor_id),
            "organization_id": str(organization_id),
            "credential_name": credential_name.strip(),
            "redirect_uri": redirect_uri,
            "state": state,
            "verifier": verifier,
            "created_at": int(self._now()),
        }
        flows = self._pending_flows(session)
        flows[state] = flow
        if len(flows) > MAX_PENDING_FLOWS:
            oldest_states = sorted(
                flows,
                key=lambda item: int(flows[item].get("created_at", 0)),
            )
            for stale_state in oldest_states[: len(flows) - MAX_PENDING_FLOWS]:
                flows.pop(stale_state, None)
        session[SESSION_KEY] = flows
        query = urlencode(
            {
                "client_id": self._client_id,
                "redirect_uri": redirect_uri,
                "response_type": "code",
                "scope": GMAIL_MODIFY_SCOPE,
                "access_type": "offline",
                "prompt": "consent",
                "include_granted_scopes": "false",
                "state": state,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            }
        )
        return f"{GOOGLE_AUTHORIZATION_URL}?{query}"

    async def complete(
        self,
        *,
        actor_id: uuid.UUID,
        state: str,
        code: str,
        session: MutableMapping[str, Any],
    ) -> MailCredentialResponse:
        flow, organization_id = self._consume_flow(
            actor_id=actor_id,
            state=state,
            session=session,
        )
        token = await self._exchange_code(
            code=code,
            redirect_uri=str(flow.get("redirect_uri", "")),
            verifier=str(flow.get("verifier", "")),
        )
        refresh_token = token.get("refresh_token")
        access_token = token.get("access_token")
        try:
            scopes = normalize_scopes(token.get("scope", ""))
        except MailOAuthSecretError as exc:
            raise GmailOAuthScopeInsufficient() from exc
        if GMAIL_MODIFY_SCOPE not in scopes:
            raise GmailOAuthScopeInsufficient()
        if not isinstance(refresh_token, str) or not refresh_token:
            raise GmailOAuthRefreshTokenRequired()
        if not isinstance(access_token, str) or not access_token:
            raise GmailOAuthTokenExchangeFailed()
        email_address = await self._mailbox_email(access_token)
        return MailCredentialService(self._db).create_gmail_oauth(
            actor_id=actor_id,
            organization_id=organization_id,
            credential_name=str(flow.get("credential_name", "")).strip(),
            email_address=email_address,
            refresh_token=refresh_token,
            scopes=scopes,
        )

    async def handle_callback(
        self,
        *,
        actor_id: uuid.UUID,
        state: str,
        code: str | None,
        provider_error: str | None,
        session: MutableMapping[str, Any],
    ) -> MailCredentialResponse:
        if provider_error or not code:
            self.cancel(actor_id=actor_id, state=state, session=session)
        return await self.complete(
            actor_id=actor_id,
            state=state,
            code=code,
            session=session,
        )

    def cancel(
        self,
        *,
        actor_id: uuid.UUID,
        state: str,
        session: MutableMapping[str, Any],
    ) -> NoReturn:
        self._consume_flow(actor_id=actor_id, state=state, session=session)
        raise GmailOAuthCancelled()

    def _consume_flow(
        self,
        *,
        actor_id: uuid.UUID,
        state: str,
        session: MutableMapping[str, Any],
    ) -> tuple[dict[str, Any], uuid.UUID]:
        self._require_configuration()
        stored = session.get(SESSION_KEY)
        if isinstance(stored, dict) and "state" in stored:
            session.pop(SESSION_KEY, None)
            flow = stored
        elif isinstance(stored, dict):
            matching_state = next(
                (
                    candidate
                    for candidate in stored
                    if hmac.compare_digest(str(candidate), str(state or ""))
                ),
                None,
            )
            flow = stored.pop(matching_state, None) if matching_state else None
            if stored:
                session[SESSION_KEY] = stored
            else:
                session.pop(SESSION_KEY, None)
        else:
            flow = None
        if not isinstance(flow, dict):
            raise GmailOAuthStateInvalid()
        if not hmac.compare_digest(str(flow.get("state", "")), str(state or "")):
            raise GmailOAuthStateInvalid()
        try:
            flow_actor_id = uuid.UUID(str(flow["actor_id"]))
            organization_id = uuid.UUID(str(flow["organization_id"]))
            created_at = int(flow["created_at"])
        except (KeyError, TypeError, ValueError) as exc:
            raise GmailOAuthStateInvalid() from exc
        if flow_actor_id != actor_id:
            raise GmailOAuthStateInvalid()
        if self._now() - created_at > FLOW_TTL_SECONDS:
            raise GmailOAuthFlowExpired()
        if not has_organization_manager_permission(self._db, actor_id, organization_id):
            raise MailCredentialPermissionDenied()
        return flow, organization_id

    def _pending_flows(
        self, session: MutableMapping[str, Any]
    ) -> dict[str, dict[str, Any]]:
        stored = session.get(SESSION_KEY)
        if not isinstance(stored, dict) or "state" in stored:
            return {}
        now = self._now()
        flows: dict[str, dict[str, Any]] = {}
        for state, flow in stored.items():
            if not isinstance(flow, dict):
                continue
            try:
                created_at = int(flow.get("created_at", 0))
            except (TypeError, ValueError):
                continue
            if now - created_at <= FLOW_TTL_SECONDS:
                flows[str(state)] = flow
        return flows

    async def _exchange_code(
        self, *, code: str, redirect_uri: str, verifier: str
    ) -> dict:
        if not code or not redirect_uri or not verifier:
            raise GmailOAuthStateInvalid()
        try:
            payload = await self._require_provider().exchange_authorization_code(
                client_id=self._client_id,
                client_secret=self._client_secret,
                code=code,
                verifier=verifier,
                redirect_uri=redirect_uri,
            )
        except GoogleOAuthProviderError:
            raise GmailOAuthTokenExchangeFailed() from None
        if not isinstance(payload, dict):
            raise GmailOAuthTokenExchangeFailed()
        return dict(payload)

    async def _mailbox_email(self, access_token: str) -> str:
        try:
            email_address = await self._require_provider().read_mailbox_email(
                access_token=access_token
            )
        except GoogleOAuthProviderError:
            raise GmailOAuthTokenExchangeFailed() from None
        if (
            not isinstance(email_address, str)
            or "@" not in email_address
            or any(char in email_address for char in ("\r", "\n", "\x00"))
        ):
            raise GmailOAuthTokenExchangeFailed()
        return email_address.lower()

    def _require_provider(self) -> GoogleOAuthProviderPort:
        if self._provider is None:
            raise GoogleOAuthProviderError("mail.oauth_provider_unavailable")
        return self._provider

    def _require_configuration(self) -> None:
        if not self._client_id or not self._client_secret:
            raise GmailOAuthConfigurationMissing()
        if os.getenv("NODE_ENV") == "production" and (
            len(self._session_signing_key) < 32
            or self._session_signing_key == "your-secret-key-change-in-production"
        ):
            raise GmailOAuthConfigurationMissing()


def resolve_gmail_oauth_redirect_uri(derived_uri: str) -> str:
    configured = os.getenv("GOOGLE_OAUTH_REDIRECT_URI", "").strip()
    candidate = configured or derived_uri.strip()
    try:
        parsed = urlparse(candidate)
    except ValueError as exc:
        raise GmailOAuthConfigurationMissing() from exc
    local_host = parsed.hostname in {"localhost", "127.0.0.1"}
    if (
        not parsed.scheme
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or (parsed.scheme != "https" and not (local_host and parsed.scheme == "http"))
        or (not configured and not local_host)
    ):
        raise GmailOAuthConfigurationMissing()
    return candidate
