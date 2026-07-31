from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from apps.gateway.api.deps import get_db, require_json_content_type
from apps.gateway.composition.authentication import login_network_resolver
from apps.gateway.composition.memory import build_public_conversation_application
from apps.memory.application.public_lifecycle import (
    CreatePublicConversationCommand,
    LifecycleCommand,
    public_request_fingerprint,
)
from apps.memory.domain.errors import (
    AccessGrantNotUsableError,
    DuplicateRequestConflictError,
    MemoryAdapterUnavailableError,
    PublicConversationFeatureDisabledError,
    PublicConversationRateLimitedError,
    PurgeReceiptNotUsableError,
    SecretReplayExpiredError,
    SessionNotActiveError,
    StaleLifecycleRevisionError,
)

router = APIRouter()

_IDEMPOTENCY_KEY = re.compile(r"^[A-Za-z0-9_-]{22,256}$")
_LIFECYCLE_ETAG = re.compile(r'^"lifecycle-revision-([1-9][0-9]{0,9})"$')


class _EmptyPublicConversationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _idempotency_key_hash(value: str | None) -> str:
    if value is None or not _IDEMPOTENCY_KEY.fullmatch(value):
        raise _safe_error(
            "memory.invalid_idempotency_key",
            "A high-entropy Idempotency-Key is required.",
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _request_fingerprint(
    request_model: BaseModel,
    *,
    expected_lifecycle_revision: int | None = None,
) -> str:
    return public_request_fingerprint(
        request_model.model_dump(mode="json"),
        expected_lifecycle_revision=expected_lifecycle_revision,
    )


def _network_address(request: Request) -> str:
    network_address = login_network_resolver().resolve(request)
    if network_address == "unknown":
        raise MemoryAdapterUnavailableError()
    return network_address


def _expected_lifecycle_revision(if_match: str | None) -> int:
    match = _LIFECYCLE_ETAG.fullmatch(if_match or "")
    if match is None:
        raise _safe_error(
            "memory.lifecycle_precondition_required",
            'If-Match: "lifecycle-revision-N" is required.',
            status_code=status.HTTP_428_PRECONDITION_REQUIRED,
        )
    return int(match.group(1))


def _conversation_token(authorization: str | None) -> str:
    prefix = "Conversation "
    if authorization is None or not authorization.startswith(prefix):
        raise _hidden_error()
    token = authorization[len(prefix) :]
    if not token or len(token) > 256:
        raise _hidden_error()
    return token


def _purge_receipt(authorization: str | None) -> str:
    prefix = "Purge "
    if authorization is None or not authorization.startswith(prefix):
        raise _hidden_error()
    receipt = authorization[len(prefix) :]
    if not receipt or len(receipt) > 256:
        raise _hidden_error()
    return receipt


def _set_public_headers(response: Response, *, lifecycle_revision: int | None = None) -> None:
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    if lifecycle_revision is not None:
        response.headers["ETag"] = f'"lifecycle-revision-{lifecycle_revision}"'


def _hidden_error() -> HTTPException:
    return _safe_error(
        "memory.session_hidden",
        "Conversation not found",
        status_code=status.HTTP_404_NOT_FOUND,
    )


def _safe_error(code: str, message: str, *, status_code: int) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail={"code": code, "message": message},
        headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"},
    )


def _map_public_error(error: Exception) -> HTTPException:
    if isinstance(
        error,
        (AccessGrantNotUsableError, PurgeReceiptNotUsableError, SessionNotActiveError),
    ):
        return _hidden_error()
    if isinstance(error, SecretReplayExpiredError):
        return _safe_error(
            "memory.secret_replay_expired",
            "The capability replay window has expired.",
            status_code=status.HTTP_409_CONFLICT,
        )
    if isinstance(error, DuplicateRequestConflictError):
        return _safe_error(
            "memory.duplicate_request_conflict",
            "The Idempotency-Key was already used for a different request.",
            status_code=status.HTTP_409_CONFLICT,
        )
    if isinstance(error, StaleLifecycleRevisionError):
        return _safe_error(
            "memory.stale_lifecycle_revision",
            "The conversation lifecycle has changed.",
            status_code=status.HTTP_409_CONFLICT,
        )
    if isinstance(error, PublicConversationRateLimitedError):
        return HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={"code": error.code, "message": "Too many conversation requests."},
            headers={
                "Retry-After": str(error.retry_after_seconds),
                "Cache-Control": "no-store",
                "Referrer-Policy": "no-referrer",
            },
        )
    if isinstance(error, PublicConversationFeatureDisabledError):
        return _safe_error(
            "memory.feature_unavailable",
            "Public conversation is not enabled.",
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        )
    if isinstance(error, (MemoryAdapterUnavailableError, RuntimeError)):
        return _safe_error(
            "memory.adapter_unavailable",
            "Public conversation is temporarily unavailable.",
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        )
    raise error


def _application(db: Session):
    try:
        return build_public_conversation_application(db)
    except (RuntimeError, ValueError) as error:
        raise _map_public_error(error) from None


@router.post(
    "/run-public/{url_slug}/conversations",
    status_code=status.HTTP_201_CREATED,
)
def create_public_conversation(
    url_slug: str,
    body: _EmptyPublicConversationRequest,
    request: Request,
    response: Response,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    _content_type: None = Depends(require_json_content_type),
    db: Session = Depends(get_db),
):
    application = _application(db)
    try:
        result = application.create.execute(
            CreatePublicConversationCommand(
                url_slug=url_slug,
                idempotency_key_hash=_idempotency_key_hash(idempotency_key),
                request_fingerprint=_request_fingerprint(body),
                now=_now(),
                network_address=_network_address(request),
            )
        )
    except Exception as error:
        raise _map_public_error(error) from None
    _set_public_headers(response, lifecycle_revision=result.lifecycle_revision)
    return {
        "conversation": {
            "access_token": result.access_token,
            "lifecycle_revision": result.lifecycle_revision,
            "memory_contract_version": result.memory_contract_version,
            "expires_at": result.expires_at,
        }
    }


@router.post("/run-public/{url_slug}/conversation/close")
def close_public_conversation(
    url_slug: str,
    body: _EmptyPublicConversationRequest,
    request: Request,
    response: Response,
    authorization: Annotated[str | None, Header()] = None,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    if_match: Annotated[str | None, Header(alias="If-Match")] = None,
    _content_type: None = Depends(require_json_content_type),
    db: Session = Depends(get_db),
):
    application = _application(db)
    try:
        result = application.close.execute(
            _lifecycle_command(
                url_slug=url_slug,
                body=body,
                request=request,
                authorization=authorization,
                idempotency_key=idempotency_key,
                if_match=if_match,
            )
        )
    except Exception as error:
        raise _map_public_error(error) from None
    _set_public_headers(response, lifecycle_revision=result.lifecycle_revision)
    return {
        "status": result.lifecycle.value,
        "lifecycle_revision": result.lifecycle_revision,
        "memory_contract_version": result.memory_contract_version,
        "expires_at": result.expires_at,
    }


@router.post(
    "/run-public/{url_slug}/conversation/reset",
    status_code=status.HTTP_201_CREATED,
)
def reset_public_conversation(
    url_slug: str,
    body: _EmptyPublicConversationRequest,
    request: Request,
    response: Response,
    authorization: Annotated[str | None, Header()] = None,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    if_match: Annotated[str | None, Header(alias="If-Match")] = None,
    _content_type: None = Depends(require_json_content_type),
    db: Session = Depends(get_db),
):
    application = _application(db)
    try:
        result = application.reset.execute(
            _lifecycle_command(
                url_slug=url_slug,
                body=body,
                request=request,
                authorization=authorization,
                idempotency_key=idempotency_key,
                if_match=if_match,
            )
        )
    except Exception as error:
        raise _map_public_error(error) from None
    _set_public_headers(response, lifecycle_revision=result.lifecycle_revision)
    return {
        "conversation": {
            "access_token": result.access_token,
            "lifecycle_revision": result.lifecycle_revision,
            "memory_contract_version": result.memory_contract_version,
            "expires_at": result.expires_at,
        },
        "previous": {
            "lifecycle": result.previous_lifecycle.value,
            "lifecycle_revision": result.previous_lifecycle_revision,
        },
    }


@router.delete(
    "/run-public/{url_slug}/conversation",
    status_code=status.HTTP_202_ACCEPTED,
)
def delete_public_conversation(
    url_slug: str,
    body: _EmptyPublicConversationRequest,
    request: Request,
    response: Response,
    authorization: Annotated[str | None, Header()] = None,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    if_match: Annotated[str | None, Header(alias="If-Match")] = None,
    _content_type: None = Depends(require_json_content_type),
    db: Session = Depends(get_db),
):
    application = _application(db)
    try:
        result = application.delete.execute(
            _lifecycle_command(
                url_slug=url_slug,
                body=body,
                request=request,
                authorization=authorization,
                idempotency_key=idempotency_key,
                if_match=if_match,
            )
        )
    except Exception as error:
        raise _map_public_error(error) from None
    _set_public_headers(response, lifecycle_revision=result.lifecycle_revision)
    return {
        "status": result.lifecycle.value,
        "purge_request_id": f"prg_{result.purge_job_id.hex}",
        "purge_receipt": result.purge_receipt,
    }


@router.get("/run-public/{url_slug}/conversation/transcript")
def get_public_transcript(
    url_slug: str,
    response: Response,
    authorization: Annotated[str | None, Header()] = None,
    db: Session = Depends(get_db),
):
    application = _application(db)
    try:
        result = application.transcript.execute(
            url_slug=url_slug,
            access_token=_conversation_token(authorization),
            now=_now(),
        )
    except Exception as error:
        raise _map_public_error(error) from None
    _set_public_headers(response, lifecycle_revision=result.lifecycle_revision)
    return {
        "conversation": {
            "state": result.lifecycle.value,
            "lifecycle_revision": result.lifecycle_revision,
            "content_revision": result.content_revision,
            "expires_at": result.expires_at,
        },
        "turns": list(result.turns),
        "next_cursor": None,
    }


@router.get("/run-public/{url_slug}/conversation/turns/{turn_id}")
def get_public_turn_status(
    url_slug: str,
    turn_id: str,
    authorization: Annotated[str | None, Header()] = None,
    db: Session = Depends(get_db),
):
    # MBA-317 deliberately does not publish a turn/dispatch/runtime path.  The
    # grant is still validated so this remains a resource-hidden response.
    application = _application(db)
    try:
        application.transcript.execute(
            url_slug=url_slug,
            access_token=_conversation_token(authorization),
            now=_now(),
        )
    except Exception as error:
        raise _map_public_error(error) from None
    raise _hidden_error()


@router.get("/run-public/{url_slug}/conversation/purge-status")
def get_public_purge_status(
    url_slug: str,
    response: Response,
    authorization: Annotated[str | None, Header()] = None,
    db: Session = Depends(get_db),
):
    application = _application(db)
    try:
        result = application.purge_status.execute(
            url_slug=url_slug,
            purge_receipt=_purge_receipt(authorization),
            now=_now(),
        )
    except Exception as error:
        raise _map_public_error(error) from None
    _set_public_headers(response)
    return {
        "status": result.status.value,
        "updated_at": result.updated_at,
        "safe_failure_reason": result.safe_failure_reason,
    }


def _lifecycle_command(
    *,
    url_slug: str,
    body: BaseModel,
    request: Request,
    authorization: str | None,
    idempotency_key: str | None,
    if_match: str | None,
) -> LifecycleCommand:
    expected_lifecycle_revision = _expected_lifecycle_revision(if_match)
    return LifecycleCommand(
        url_slug=url_slug,
        access_token=_conversation_token(authorization),
        idempotency_key_hash=_idempotency_key_hash(idempotency_key),
        request_fingerprint=_request_fingerprint(
            body,
            expected_lifecycle_revision=expected_lifecycle_revision,
        ),
        expected_lifecycle_revision=expected_lifecycle_revision,
        now=_now(),
        network_address=_network_address(request),
    )


__all__ = ["router"]
