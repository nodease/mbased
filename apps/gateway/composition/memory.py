from __future__ import annotations

import hmac
import os
from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache

import redis
from sqlalchemy.orm import Session

from apps.memory.adapters.queue.conversation_turn_publisher import (
    CeleryConversationTurnPublisher,
)

from apps.memory.adapters.admission import (
    PublicConversationAdmissionPolicy,
    RedisPublicConversationAdmission,
)
from apps.memory.adapters.persistence.readiness import (
    check_memory_schema_readiness,
)
from apps.memory.adapters.audit import SqlAlchemyPublicConversationAudit
from apps.memory.adapters.persistence.repository import (
    SqlAlchemyConversationMemoryRepository,
    SqlAlchemyMemoryUnitOfWork,
)
from apps.memory.adapters.security import (
    FernetMemoryContentCipher,
    FernetSecretReplayCipher,
    HmacMemoryRuntimeFingerprinter,
    HmacPublicSecretIssuer,
)
from apps.memory.application.public_lifecycle import (
    ClosePublicConversationUseCase,
    CreatePublicConversationUseCase,
    DeletePublicConversationUseCase,
    GetPublicPurgeStatusUseCase,
    GetPublicTranscriptUseCase,
    PublicConversationPolicy,
    ResetPublicConversationUseCase,
)
from apps.memory.application.public_runtime import (
    GetPublicTurnStatusUseCase,
    StartPublicConversationTurnUseCase,
)
from apps.memory.domain.errors import PublicConversationFeatureDisabledError
from apps.shared.celery_app import celery_app
from apps.shared.db.session import SessionLocal
from apps.shared.domain.conversation_memory_task import CONVERSATION_TURN_TASK_QUEUE


@dataclass(frozen=True, slots=True)
class PublicConversationApplication:
    create: CreatePublicConversationUseCase
    close: ClosePublicConversationUseCase
    reset: ResetPublicConversationUseCase
    delete: DeletePublicConversationUseCase
    transcript: GetPublicTranscriptUseCase
    purge_status: GetPublicPurgeStatusUseCase


@dataclass(frozen=True, slots=True)
class PublicConversationRuntimeApplication:
    start_turn: StartPublicConversationTurnUseCase
    turn_status: GetPublicTurnStatusUseCase


def build_public_conversation_application(
    db: Session,
    *,
    environ: Mapping[str, str] | None = None,
    redis_client=None,
) -> PublicConversationApplication:
    values = environ if environ is not None else os.environ
    validate_public_conversation_security_configuration(values)
    if not public_conversation_enabled_from_environment(values):
        raise PublicConversationFeatureDisabledError()
    policy = public_conversation_policy_from_environment(values)
    admission_policy = public_conversation_admission_policy_from_environment(values)
    repository = SqlAlchemyConversationMemoryRepository(db)
    uow = SqlAlchemyMemoryUnitOfWork(db)
    secrets = HmacPublicSecretIssuer.from_environment(values)
    replay_cipher = FernetSecretReplayCipher.from_environment(values)
    content_cipher = FernetMemoryContentCipher.from_environment(values)
    admission_key = _admission_key(values)
    admission = RedisPublicConversationAdmission(
        redis_client if redis_client is not None else _redis_client(values),
        hmac_key=admission_key,
        policy=admission_policy,
        key_namespace=values.get(
            "MEMORY_PUBLIC_ADMISSION_KEY_NAMESPACE",
            "nodease-memory-public",
        ),
        request_deduplication_ttl_seconds=int(
            policy.idempotency_retention.total_seconds()
        ),
    )
    audit = SqlAlchemyPublicConversationAudit(db)
    kwargs = {
        "repository": repository,
        "uow": uow,
        "secrets": secrets,
        "replay_cipher": replay_cipher,
        "content_cipher": content_cipher,
        "audit": audit,
        "policy": policy,
        "admission": admission,
    }
    return PublicConversationApplication(
        create=CreatePublicConversationUseCase(**kwargs),
        close=ClosePublicConversationUseCase(**kwargs),
        reset=ResetPublicConversationUseCase(**kwargs),
        delete=DeletePublicConversationUseCase(**kwargs),
        transcript=GetPublicTranscriptUseCase(**kwargs),
        purge_status=GetPublicPurgeStatusUseCase(**kwargs),
    )


def build_public_conversation_runtime_application(
    db: Session,
    *,
    environ: Mapping[str, str] | None = None,
    redis_client=None,
) -> PublicConversationRuntimeApplication:
    values = environ if environ is not None else os.environ
    validate_public_conversation_runtime_configuration(values)
    if not public_conversation_runtime_enabled_from_environment(values):
        raise PublicConversationFeatureDisabledError()
    repository = SqlAlchemyConversationMemoryRepository(db)
    uow = SqlAlchemyMemoryUnitOfWork(db)
    secrets = HmacPublicSecretIssuer.from_environment(values)
    content_cipher = FernetMemoryContentCipher.from_environment(values)
    fingerprinter = HmacMemoryRuntimeFingerprinter.from_environment(values)
    policy = public_conversation_policy_from_environment(values)
    admission = RedisPublicConversationAdmission(
        redis_client if redis_client is not None else _redis_client(values),
        hmac_key=_admission_key(values),
        policy=public_conversation_admission_policy_from_environment(values),
        key_namespace=values.get(
            "MEMORY_PUBLIC_ADMISSION_KEY_NAMESPACE",
            "nodease-memory-public",
        ),
        request_deduplication_ttl_seconds=int(
            policy.idempotency_retention.total_seconds()
        ),
    )
    return PublicConversationRuntimeApplication(
        start_turn=StartPublicConversationTurnUseCase(
            repository=repository,
            uow=uow,
            secrets=secrets,
            content_cipher=content_cipher,
            fingerprinter=fingerprinter,
            admission=admission,
            dispatch_publisher=CeleryConversationTurnPublisher(
                celery_app=celery_app,
                session_factory=SessionLocal,
            ),
            minimum_worker_capability=values.get(
                "MEMORY_RUNTIME_MINIMUM_WORKER_CAPABILITY",
                "memory-runtime-v1",
            ),
            max_dispatch_attempts=_integer(
                values,
                "MEMORY_RUNTIME_MAX_DISPATCH_ATTEMPTS",
                5,
                1,
                20,
            ),
        ),
        turn_status=GetPublicTurnStatusUseCase(
            repository=repository,
            uow=uow,
            secrets=secrets,
            content_cipher=content_cipher,
        ),
    )


def public_conversation_runtime_required(url_slug: str) -> bool:
    """Return whether a deployment is bound to the versioned Memory runtime."""
    with SessionLocal() as db:
        return SqlAlchemyConversationMemoryRepository(
            db
        ).public_deployment_requires_conversation_runtime(
            url_slug,
        )


def start_public_conversation_turn(command):
    """Construct and execute a public turn in one thread-owned DB session."""
    with SessionLocal() as db:
        return build_public_conversation_runtime_application(db).start_turn.execute(
            command
        )


def public_conversation_runtime_enabled_from_environment(
    environ: Mapping[str, str],
) -> bool:
    value = environ.get("MEMORY_PUBLIC_RUNTIME_ENABLED", "false").strip().lower()
    if value in {"true", "1"}:
        return True
    if value in {"false", "0"}:
        return False
    raise RuntimeError("MEMORY_PUBLIC_RUNTIME_ENABLED must be true or false")


def validate_public_conversation_runtime_configuration(
    environ: Mapping[str, str] | None = None,
) -> None:
    values = environ if environ is not None else os.environ
    validate_public_conversation_security_configuration(values)
    if not public_conversation_runtime_enabled_from_environment(values):
        return
    if not public_conversation_enabled_from_environment(values):
        raise RuntimeError("public Conversation lifecycle must be enabled first")
    if values.get("MEMORY_PUBLIC_RUNTIME_WORKER_READY", "").strip().lower() not in {
        "true",
        "1",
    }:
        raise RuntimeError(
            "MEMORY_PUBLIC_RUNTIME_WORKER_READY must be true before activation"
        )
    if values.get("MEMORY_PUBLIC_RUNTIME_WORKER_QUEUE", "").strip() != (
        CONVERSATION_TURN_TASK_QUEUE
    ):
        raise RuntimeError(
            "MEMORY_PUBLIC_RUNTIME_WORKER_QUEUE must name the versioned capable queue"
        )
    secrets = HmacPublicSecretIssuer.from_environment(values)
    replay_cipher = FernetSecretReplayCipher.from_environment(values)
    content_cipher = FernetMemoryContentCipher.from_environment(values)
    fingerprinter = HmacMemoryRuntimeFingerprinter.from_environment(values)
    _require_distinct_public_security_keys(
        *secrets.configuration_key_materials(),
        *replay_cipher.configuration_key_materials(),
        *content_cipher.configuration_key_materials(),
        *fingerprinter.configuration_key_materials(),
        _admission_key(values),
    )


def validate_public_conversation_security_configuration(
    environ: Mapping[str, str] | None = None,
) -> None:
    values = environ if environ is not None else os.environ
    if not public_conversation_enabled_from_environment(values):
        return
    if values.get("MEMORY_PUBLIC_REPLAY_BACKUP_ERASURE_MODE", "") not in {
        "external_crypto_erasure",
        "no_database_backups",
    }:
        raise RuntimeError(
            "MEMORY_PUBLIC_REPLAY_BACKUP_ERASURE_MODE must confirm an approved mode"
        )
    public_conversation_policy_from_environment(values)
    public_conversation_admission_policy_from_environment(values)
    secrets = HmacPublicSecretIssuer.from_environment(values)
    replay_cipher = FernetSecretReplayCipher.from_environment(values)
    admission_key = _admission_key(values)
    content_cipher = FernetMemoryContentCipher.from_environment(values)
    _require_distinct_public_security_keys(
        *secrets.configuration_key_materials(),
        *replay_cipher.configuration_key_materials(),
        admission_key,
        *content_cipher.configuration_key_materials(),
    )
    _require_public_purge_worker_ready(values)


def require_public_conversation_schema_ready(
    db: Session,
    *,
    environ: Mapping[str, str] | None = None,
) -> None:
    values = environ if environ is not None else os.environ
    if not public_conversation_enabled_from_environment(values):
        return
    readiness = check_memory_schema_readiness(db)
    if not readiness.ready:
        raise RuntimeError(
            "public conversation Memory schema is not ready; apply required "
            "migrations before enabling the feature"
        )


def public_conversation_enabled_from_environment(
    environ: Mapping[str, str],
) -> bool:
    value = environ.get("MEMORY_PUBLIC_CONVERSATION_ENABLED", "false").strip().lower()
    if value in {"true", "1"}:
        return True
    if value in {"false", "0"}:
        return False
    raise RuntimeError("MEMORY_PUBLIC_CONVERSATION_ENABLED must be true or false")


def public_conversation_policy_from_environment(
    environ: Mapping[str, str],
) -> PublicConversationPolicy:
    return PublicConversationPolicy(
        idle_lifetime=_seconds(
            environ,
            "MEMORY_PUBLIC_IDLE_SECONDS",
            86_400,
            60,
            604_800,
        ),
        absolute_lifetime=_seconds(
            environ,
            "MEMORY_PUBLIC_ABSOLUTE_SECONDS",
            604_800,
            120,
            2_592_000,
        ),
        access_grant_lifetime=_seconds(
            environ,
            "MEMORY_PUBLIC_GRANT_SECONDS",
            86_400,
            60,
            604_800,
        ),
        access_secret_replay_lifetime=_seconds(
            environ,
            "MEMORY_PUBLIC_SECRET_REPLAY_SECONDS",
            600,
            60,
            600,
        ),
        purge_receipt_lifetime=_seconds(
            environ,
            "MEMORY_PUBLIC_PURGE_RECEIPT_SECONDS",
            691_200,
            691_200,
            691_200,
        ),
        purge_secret_replay_lifetime=_seconds(
            environ,
            "MEMORY_PUBLIC_PURGE_REPLAY_SECONDS",
            86_400,
            60,
            86_400,
        ),
        idempotency_retention=_seconds(
            environ,
            "MEMORY_PUBLIC_IDEMPOTENCY_RETENTION_SECONDS",
            86_400,
            60,
            86_400,
        ),
        purge_max_attempts=_integer(
            environ,
            "MEMORY_PUBLIC_PURGE_MAX_ATTEMPTS",
            8,
            1,
            100,
        ),
    )


def public_conversation_admission_policy_from_environment(
    environ: Mapping[str, str],
) -> PublicConversationAdmissionPolicy:
    return PublicConversationAdmissionPolicy(
        window_seconds=_integer(
            environ,
            "MEMORY_PUBLIC_ADMISSION_WINDOW_SECONDS",
            60,
            1,
            3600,
        ),
        deployment_rate_limit=_integer(
            environ,
            "MEMORY_PUBLIC_DEPLOYMENT_RATE_LIMIT",
            60,
            1,
            100_000,
        ),
        organization_rate_limit=_integer(
            environ,
            "MEMORY_PUBLIC_ORGANIZATION_RATE_LIMIT",
            240,
            1,
            100_000,
        ),
        network_rate_limit=_integer(
            environ,
            "MEMORY_PUBLIC_NETWORK_RATE_LIMIT",
            60,
            1,
            100_000,
        ),
        grant_rate_limit=_integer(
            environ,
            "MEMORY_PUBLIC_GRANT_RATE_LIMIT",
            30,
            1,
            100_000,
        ),
        create_window_seconds=_integer(
            environ,
            "MEMORY_PUBLIC_CREATE_WINDOW_SECONDS",
            600,
            1,
            3600,
        ),
        create_deployment_rate_limit=_integer(
            environ,
            "MEMORY_PUBLIC_CREATE_DEPLOYMENT_RATE_LIMIT",
            200,
            1,
            100_000,
        ),
        create_organization_rate_limit=_integer(
            environ,
            "MEMORY_PUBLIC_CREATE_ORGANIZATION_RATE_LIMIT",
            1_000,
            1,
            100_000,
        ),
        create_deployment_network_rate_limit=_integer(
            environ,
            "MEMORY_PUBLIC_CREATE_DEPLOYMENT_NETWORK_RATE_LIMIT",
            10,
            1,
            100_000,
        ),
        retry_window_seconds=_integer(
            environ,
            "MEMORY_PUBLIC_RETRY_WINDOW_SECONDS",
            60,
            1,
            3600,
        ),
        request_retry_rate_limit=_integer(
            environ,
            "MEMORY_PUBLIC_REQUEST_RETRY_RATE_LIMIT",
            10,
            1,
            100_000,
        ),
    )


def _admission_key(environ: Mapping[str, str]) -> bytes:
    raw_key = environ.get("MEMORY_PUBLIC_ADMISSION_HMAC_KEY", "")
    if not raw_key:
        raise RuntimeError("MEMORY_PUBLIC_ADMISSION_HMAC_KEY is required")
    key = raw_key.encode("utf-8")
    if len(key) < 32:
        raise RuntimeError(
            "MEMORY_PUBLIC_ADMISSION_HMAC_KEY must contain at least 32 bytes"
        )
    return key


def _require_distinct_public_security_keys(*key_materials: bytes) -> None:
    if any(
        hmac.compare_digest(left, right)
        for index, left in enumerate(key_materials)
        for right in key_materials[index + 1 :]
    ):
        raise RuntimeError("public conversation security keys must be distinct")


def _require_public_purge_worker_ready(environ: Mapping[str, str]) -> None:
    value = environ.get("MEMORY_PUBLIC_PURGE_WORKER_READY", "").strip().lower()
    if value not in {"true", "1"}:
        raise RuntimeError(
            "MEMORY_PUBLIC_PURGE_WORKER_READY must be true before enabling "
            "public conversation deletion"
        )


@lru_cache(maxsize=1)
def _process_redis_client(
    host: str,
    port: int,
    database: int,
    password: str | None,
):
    return redis.Redis(
        host=host,
        port=port,
        db=database,
        password=password,
        socket_connect_timeout=0.5,
        socket_timeout=0.5,
        health_check_interval=30,
    )


def _redis_client(environ: Mapping[str, str]):
    try:
        port = int(environ.get("REDIS_PORT", "6379"))
        database = int(environ.get("REDIS_DB", "0"))
    except ValueError as exc:
        raise RuntimeError(
            "public conversation Redis configuration is invalid"
        ) from exc
    if not 1 <= port <= 65535 or not 0 <= database <= 255:
        raise RuntimeError("public conversation Redis configuration is invalid")
    return _process_redis_client(
        environ.get("REDIS_HOST", "localhost"),
        port,
        database,
        environ.get("REDIS_PASSWORD") or None,
    )


def _integer(
    environ: Mapping[str, str],
    name: str,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    raw = environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer") from exc
    if not minimum <= value <= maximum:
        raise RuntimeError(f"{name} is outside allowed bounds")
    return value


def _seconds(
    environ: Mapping[str, str],
    name: str,
    default: int,
    minimum: int,
    maximum: int,
):
    from datetime import timedelta

    return timedelta(seconds=_integer(environ, name, default, minimum, maximum))


__all__ = [
    "PublicConversationApplication",
    "build_public_conversation_application",
    "build_public_conversation_runtime_application",
    "public_conversation_enabled_from_environment",
    "public_conversation_runtime_enabled_from_environment",
    "public_conversation_admission_policy_from_environment",
    "public_conversation_policy_from_environment",
    "require_public_conversation_schema_ready",
    "validate_public_conversation_security_configuration",
    "validate_public_conversation_runtime_configuration",
]
