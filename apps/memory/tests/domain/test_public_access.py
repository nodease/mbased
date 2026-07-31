from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from apps.memory.domain.errors import (
    AccessGrantNotUsableError,
    AccessGrantScopeError,
)
from apps.memory.domain.public_access import (
    AccessGrantState,
    ConversationAccessGrant,
)


def _now() -> datetime:
    return datetime(2026, 7, 18, 10, 0, tzinfo=timezone.utc)


def _grant() -> ConversationAccessGrant:
    return ConversationAccessGrant.issue(
        grant_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        session_id=uuid.uuid4(),
        deployment_id=uuid.uuid4(),
        deployment_version=3,
        audience_kind="public_chatbot",
        verifier_hash=hashlib.sha256(secrets.token_bytes(32)).hexdigest(),
        verifier_key_version="grant-hmac-v1",
        expires_at=_now() + timedelta(hours=24),
        now=_now(),
    )


def test_grant_keeps_only_verifier_and_enforces_exact_binding():
    grant = _grant()

    assert "token" not in grant.__dataclass_fields__
    grant.require_active(
        deployment_id=grant.deployment_id,
        deployment_version=grant.deployment_version,
        audience_kind="public_chatbot",
        now=_now(),
    )

    with pytest.raises(AccessGrantScopeError):
        grant.require_active(
            deployment_id=uuid.uuid4(),
            deployment_version=grant.deployment_version,
            audience_kind="public_chatbot",
            now=_now(),
        )


def test_close_changes_grant_to_transcript_only_without_rotation():
    grant = _grant()

    grant.restrict_to_transcript(now=_now())

    assert grant.state is AccessGrantState.TRANSCRIPT_ONLY
    grant.require_transcript(
        deployment_id=grant.deployment_id,
        deployment_version=grant.deployment_version,
        audience_kind="public_chatbot",
        now=_now(),
    )
    with pytest.raises(AccessGrantNotUsableError):
        grant.require_active(
            deployment_id=grant.deployment_id,
            deployment_version=grant.deployment_version,
            audience_kind="public_chatbot",
            now=_now(),
        )


def test_reset_or_delete_revokes_grant_immediately_and_is_idempotent():
    grant = _grant()

    grant.revoke(now=_now())
    grant.revoke(now=_now() + timedelta(seconds=1))

    assert grant.state is AccessGrantState.REVOKED
    assert grant.revoked_at == _now()
    with pytest.raises(AccessGrantNotUsableError):
        grant.require_transcript(
            deployment_id=grant.deployment_id,
            deployment_version=grant.deployment_version,
            audience_kind="public_chatbot",
            now=_now(),
        )


def test_expired_grant_is_not_usable_for_transcript_or_mutation():
    grant = _grant()

    with pytest.raises(AccessGrantNotUsableError):
        grant.require_active(
            deployment_id=grant.deployment_id,
            deployment_version=grant.deployment_version,
            audience_kind="public_chatbot",
            now=grant.expires_at,
        )
    assert grant.state is AccessGrantState.EXPIRED
