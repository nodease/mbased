from __future__ import annotations

import json
import secrets
from datetime import timedelta
from unittest.mock import MagicMock

import pytest
from cryptography.fernet import Fernet
from sqlalchemy.orm import Session

from apps.gateway.composition import memory as memory_composition
from apps.gateway.composition.memory import (
    build_public_conversation_application,
    public_conversation_admission_policy_from_environment,
    public_conversation_policy_from_environment,
    validate_public_conversation_runtime_configuration,
    validate_public_conversation_security_configuration,
)
from apps.memory.adapters.admission import RedisPublicConversationAdmission
from apps.memory.adapters.persistence.readiness import (
    MemorySchemaReadinessResult,
)
from apps.memory.domain.errors import PublicConversationFeatureDisabledError
from apps.shared.domain.conversation_memory_task import CONVERSATION_TURN_TASK_QUEUE


def _environment() -> dict[str, str]:
    return {
        "MEMORY_PUBLIC_CONVERSATION_ENABLED": "true",
        "MEMORY_PUBLIC_PURGE_WORKER_READY": "true",
        "MEMORY_PUBLIC_REPLAY_BACKUP_ERASURE_MODE": "no_database_backups",
        "MEMORY_PUBLIC_CAPABILITY_HMAC_KEY": secrets.token_urlsafe(32),
        "MEMORY_PUBLIC_REPLAY_ENCRYPTION_KEY": Fernet.generate_key().decode("ascii"),
        "MEMORY_PUBLIC_ADMISSION_HMAC_KEY": secrets.token_urlsafe(32),
        "MEMORY_CONTENT_ENCRYPTION_KEYS": json.dumps(
            {"content-v1": Fernet.generate_key().decode("ascii")}
        ),
        "MEMORY_CONTENT_ENCRYPTION_PRIMARY_VERSION": "content-v1",
        "MEMORY_CONTENT_DIGEST_HMAC_KEY": secrets.token_urlsafe(32),
    }


def _runtime_environment() -> dict[str, str]:
    environment = _environment()
    environment.update(
        {
            "MEMORY_PUBLIC_RUNTIME_ENABLED": "true",
            "MEMORY_PUBLIC_RUNTIME_WORKER_READY": "true",
            "MEMORY_PUBLIC_RUNTIME_WORKER_QUEUE": CONVERSATION_TURN_TASK_QUEUE,
            "MEMORY_CONTENT_ENCRYPTION_KEYS": json.dumps(
                {"content-v1": Fernet.generate_key().decode("ascii")}
            ),
            "MEMORY_CONTENT_ENCRYPTION_PRIMARY_VERSION": "content-v1",
            "MEMORY_CONTENT_DIGEST_HMAC_KEY": secrets.token_urlsafe(32),
            "MEMORY_RUNTIME_ADMISSION_HMAC_KEYS": json.dumps(
                {"admission-v1": secrets.token_urlsafe(32)}
            ),
            "MEMORY_RUNTIME_ADMISSION_HMAC_PRIMARY_VERSION": "admission-v1",
        }
    )
    return environment


def test_disabled_public_memory_does_not_require_security_keys():
    validate_public_conversation_security_configuration({})


def test_disabled_public_memory_skips_schema_readiness(monkeypatch):
    def unexpected_check(_db):
        raise AssertionError("disabled feature must not inspect Memory schema")

    monkeypatch.setattr(
        memory_composition,
        "check_memory_schema_readiness",
        unexpected_check,
    )

    memory_composition.require_public_conversation_schema_ready(
        MagicMock(spec=Session),
        environ={"MEMORY_PUBLIC_CONVERSATION_ENABLED": "false"},
    )


def test_enabled_public_memory_fails_startup_when_schema_is_incomplete(monkeypatch):
    monkeypatch.setattr(
        memory_composition,
        "check_memory_schema_readiness",
        lambda _db: MemorySchemaReadinessResult(
            missing_columns={"conversation_purge_jobs": ["app_id"]},
        ),
    )

    with pytest.raises(RuntimeError, match="schema is not ready"):
        memory_composition.require_public_conversation_schema_ready(
            MagicMock(spec=Session),
            environ={"MEMORY_PUBLIC_CONVERSATION_ENABLED": "true"},
        )


def test_enabled_public_memory_accepts_capability_complete_schema(monkeypatch):
    monkeypatch.setattr(
        memory_composition,
        "check_memory_schema_readiness",
        lambda _db: MemorySchemaReadinessResult(missing_columns={}),
    )

    memory_composition.require_public_conversation_schema_ready(
        MagicMock(spec=Session),
        environ={"MEMORY_PUBLIC_CONVERSATION_ENABLED": "true"},
    )


def test_enabled_composition_requires_lifecycle_content_encryption_keys():
    with pytest.raises(RuntimeError):
        validate_public_conversation_security_configuration(
            {
                "MEMORY_PUBLIC_CONVERSATION_ENABLED": "true",
                "MEMORY_PUBLIC_REPLAY_BACKUP_ERASURE_MODE": "no_database_backups",
            }
        )


@pytest.mark.parametrize(
    ("first_name", "second_name"),
    (
        (
            "MEMORY_PUBLIC_CAPABILITY_HMAC_KEY",
            "MEMORY_PUBLIC_REPLAY_ENCRYPTION_KEY",
        ),
        (
            "MEMORY_PUBLIC_CAPABILITY_HMAC_KEY",
            "MEMORY_PUBLIC_ADMISSION_HMAC_KEY",
        ),
        (
            "MEMORY_PUBLIC_REPLAY_ENCRYPTION_KEY",
            "MEMORY_PUBLIC_ADMISSION_HMAC_KEY",
        ),
        (
            "MEMORY_PUBLIC_CAPABILITY_HMAC_KEY",
            "MEMORY_CONTENT_DIGEST_HMAC_KEY",
        ),
    ),
)
def test_enabled_composition_rejects_reused_memory_security_key_material(
    first_name: str,
    second_name: str,
):
    environment = _environment()
    shared_key = Fernet.generate_key().decode("ascii")
    environment[first_name] = shared_key
    environment[second_name] = shared_key

    with pytest.raises(RuntimeError, match="must be distinct"):
        validate_public_conversation_security_configuration(environment)


def test_enabled_composition_rejects_reused_previous_keyring_material():
    environment = _environment()
    environment["MEMORY_PUBLIC_CAPABILITY_HMAC_KEYS"] = json.dumps(
        {
            "hmac-v2": secrets.token_urlsafe(32),
            "hmac-v1": environment["MEMORY_PUBLIC_ADMISSION_HMAC_KEY"],
        }
    )
    environment["MEMORY_PUBLIC_CAPABILITY_HMAC_PRIMARY_VERSION"] = "hmac-v2"

    with pytest.raises(RuntimeError, match="must be distinct"):
        validate_public_conversation_security_configuration(environment)


def test_enabled_composition_requires_an_approved_backup_erasure_contract():
    environment = _environment()
    environment["MEMORY_PUBLIC_REPLAY_BACKUP_ERASURE_MODE"] = "unconfirmed"

    with pytest.raises(RuntimeError):
        validate_public_conversation_security_configuration(environment)


@pytest.mark.parametrize("value", (None, "false", "invalid"))
def test_enabled_composition_requires_physical_purge_worker_readiness(
    value: str | None,
):
    environment = _environment()
    if value is None:
        environment.pop("MEMORY_PUBLIC_PURGE_WORKER_READY")
    else:
        environment["MEMORY_PUBLIC_PURGE_WORKER_READY"] = value

    with pytest.raises(RuntimeError, match="MEMORY_PUBLIC_PURGE_WORKER_READY"):
        validate_public_conversation_security_configuration(environment)


def test_disabled_composition_cannot_build_a_request_application():
    with pytest.raises(PublicConversationFeatureDisabledError):
        build_public_conversation_application(
            MagicMock(spec=Session),
            environ={"MEMORY_PUBLIC_CONVERSATION_ENABLED": "false"},
        )


def test_composition_rejects_an_invalid_lifetime_relationship():
    environment = _environment()
    environment.update(
        {
            "MEMORY_PUBLIC_IDLE_SECONDS": "86400",
            "MEMORY_PUBLIC_ABSOLUTE_SECONDS": "86400",
        }
    )

    with pytest.raises(ValueError):
        validate_public_conversation_security_configuration(environment)


def test_composition_wires_one_shared_fail_closed_admission_adapter_per_request():
    environment = _environment()
    fake_redis = MagicMock()
    application = build_public_conversation_application(
        MagicMock(spec=Session),
        environ=environment,
        redis_client=fake_redis,
    )

    assert application.transcript.content_cipher is not None
    assert environment.get("MEMORY_PUBLIC_RUNTIME_ENABLED", "false") == "false"
    assert isinstance(application.create.admission, RedisPublicConversationAdmission)
    assert application.create.admission is application.close.admission
    assert application.create.admission is application.reset.admission
    assert application.create.admission is application.delete.admission
    assert application.create.admission._redis is fake_redis


def test_composition_reuses_one_process_scoped_redis_pool(monkeypatch):
    environment = _environment()
    environment["REDIS_HOST"] = f"memory-{secrets.token_hex(8)}"
    created_clients = []

    def redis_factory(**_kwargs):
        client = object()
        created_clients.append(client)
        return client

    monkeypatch.setattr(memory_composition.redis, "Redis", redis_factory)

    first = build_public_conversation_application(
        MagicMock(spec=Session),
        environ=environment,
    )
    second = build_public_conversation_application(
        MagicMock(spec=Session),
        environ=environment,
    )

    assert first.create.admission._redis is second.create.admission._redis
    assert len(created_clients) == 1


def test_composition_uses_documented_safe_defaults_for_public_session_create():
    policy = public_conversation_admission_policy_from_environment({})

    assert policy.window_seconds == 60
    assert policy.deployment_rate_limit == 120
    assert policy.organization_rate_limit == 600
    assert policy.network_rate_limit == 60
    assert policy.grant_rate_limit == 20
    assert policy.create_window_seconds == 600
    assert policy.create_deployment_network_rate_limit == 10
    assert policy.create_deployment_rate_limit == 200
    assert policy.create_organization_rate_limit == 1_000
    assert policy.retry_window_seconds == 60
    assert policy.request_retry_rate_limit == 10


def test_composition_limits_general_idempotency_retention_to_twenty_four_hours():
    policy = public_conversation_policy_from_environment({})

    assert policy.idempotency_retention == timedelta(hours=24)


def test_composition_retains_purge_receipt_for_the_full_eight_day_contract():
    policy = public_conversation_policy_from_environment({})

    assert policy.purge_receipt_lifetime == timedelta(days=8)


def test_composition_rejects_a_purge_receipt_lifetime_shorter_than_eight_days():
    environment = _environment()
    environment["MEMORY_PUBLIC_PURGE_RECEIPT_SECONDS"] = "604800"

    with pytest.raises(RuntimeError, match="outside allowed bounds"):
        public_conversation_policy_from_environment(environment)


def test_runtime_configuration_accepts_only_the_versioned_capable_worker_queue():
    validate_public_conversation_runtime_configuration(_runtime_environment())


@pytest.mark.parametrize("worker_queue", (None, "workflow", "conversation-memory-v2"))
def test_runtime_configuration_rejects_a_ready_flag_without_the_exact_worker_queue(
    worker_queue,
):
    environment = _runtime_environment()
    if worker_queue is None:
        environment.pop("MEMORY_PUBLIC_RUNTIME_WORKER_QUEUE")
    else:
        environment["MEMORY_PUBLIC_RUNTIME_WORKER_QUEUE"] = worker_queue

    with pytest.raises(RuntimeError, match="MEMORY_PUBLIC_RUNTIME_WORKER_QUEUE"):
        validate_public_conversation_runtime_configuration(environment)


def test_disabled_runtime_does_not_require_a_worker_queue():
    environment = _environment()
    environment["MEMORY_PUBLIC_RUNTIME_ENABLED"] = "false"

    validate_public_conversation_runtime_configuration(environment)
