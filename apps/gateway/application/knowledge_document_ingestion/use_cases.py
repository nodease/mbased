from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Mapping, Protocol

from apps.gateway.application.knowledge_document_ingestion.progress import (
    DocumentIngestionProgressPort,
    clear_progress_projection,
)

from apps.shared.domain.knowledge_document_ingestion import (
    DEFAULT_MAX_ATTEMPTS,
    build_idempotency_key,
    build_input_revision,
)
from apps.shared.services.rag_hierarchy import chunking_fingerprint_hash


_SHA256_PATTERN = re.compile(r"^(?:sha256:)?[0-9a-fA-F]{64}$")
_SAFE_PERSISTENCE_REASON_CODES = frozenset(
    {"connection.reference_busy", "connection.reference_unavailable"}
)


@dataclass(frozen=True, slots=True)
class DocumentIngestionSettings:
    chunk_size: int | None = None
    chunk_overlap: int | None = None
    embedding_model: str | None = None
    meta_updates: Mapping[str, Any] = field(default_factory=dict)
    meta_remove_keys: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class RequestDocumentIngestionCommand:
    actor_id: uuid.UUID
    organization_id: uuid.UUID
    knowledge_base_id: uuid.UUID
    document_id: uuid.UUID
    operation: str
    settings: DocumentIngestionSettings = field(
        default_factory=DocumentIngestionSettings
    )
    required_document_status: str | None = None
    expected_document_updated_at: datetime | None = None
    require_document_revision_match: bool = False


@dataclass(frozen=True, slots=True)
class RequestKnowledgeBaseReindexCommand:
    actor_id: uuid.UUID
    organization_id: uuid.UUID
    knowledge_base_id: uuid.UUID
    embedding_model: str


@dataclass(frozen=True, slots=True)
class AdmissionDocumentSnapshot:
    document_id: uuid.UUID
    knowledge_base_id: uuid.UUID
    organization_id: uuid.UUID
    source_type: str
    document_status: str
    lifecycle_state: str
    sync_state: str
    chunk_size: int
    chunk_overlap: int
    meta_info: Mapping[str, Any]
    content_hash: str | None
    active_document_version_id: uuid.UUID | None
    embedding_model: str
    document_updated_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class DocumentIngestionJobSnapshot:
    job_id: uuid.UUID
    organization_id: uuid.UUID
    knowledge_base_id: uuid.UUID | None
    document_id: uuid.UUID | None
    operation: str
    generation: int
    input_revision: str
    status: str
    attempt_count: int
    max_attempts: int
    retryable: bool
    safe_reason_code: str | None
    requested_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    result_document_version_id: uuid.UUID | None


@dataclass(frozen=True, slots=True)
class DocumentIngestionRequestResult:
    job: DocumentIngestionJobSnapshot
    reused: bool
    dispatch_deferred: bool


@dataclass(frozen=True, slots=True)
class KnowledgeBaseReindexRequestResult:
    jobs: tuple[DocumentIngestionJobSnapshot, ...]
    reused: bool
    dispatch_deferred: bool


@dataclass(frozen=True, slots=True)
class RedriveDocumentIngestionCommand:
    actor_id: uuid.UUID
    organization_id: uuid.UUID
    knowledge_base_id: uuid.UUID
    document_id: uuid.UUID
    expected_job_id: uuid.UUID | None


class DocumentIngestionHidden(Exception):
    pass


class DocumentIngestionConflict(Exception):
    pass


class DocumentIngestionPolicyBlocked(Exception):
    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


class DocumentIngestionPersistenceFailed(Exception):
    def __init__(self, reason_code: str = "ingestion.admission_unavailable") -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


class DocumentIngestionRepositoryPort(Protocol):
    def database_now(self) -> datetime: ...

    def lock_document_scope(
        self,
        organization_id: uuid.UUID,
        knowledge_base_id: uuid.UUID,
        document_id: uuid.UUID,
    ) -> AdmissionDocumentSnapshot | None: ...

    def lock_knowledge_base_documents(
        self,
        organization_id: uuid.UUID,
        knowledge_base_id: uuid.UUID,
    ) -> tuple[AdmissionDocumentSnapshot, ...] | None: ...

    def find_active_job(
        self, document_id: uuid.UUID
    ) -> DocumentIngestionJobSnapshot | None: ...

    def next_generation(self, document_id: uuid.UUID) -> int: ...

    def latest_job(
        self, document_id: uuid.UUID, *, lock: bool = False
    ) -> DocumentIngestionJobSnapshot | None: ...

    def apply_settings_and_mark_queued(
        self,
        target: AdmissionDocumentSnapshot,
        settings: DocumentIngestionSettings,
        *,
        now: datetime,
    ) -> None: ...

    def update_locked_knowledge_base_model(
        self,
        knowledge_base_id: uuid.UUID,
        embedding_model: str,
        *,
        now: datetime,
    ) -> None: ...

    def create_job(
        self,
        *,
        command: RequestDocumentIngestionCommand,
        generation: int,
        input_revision: str,
        idempotency_key: str,
        now: datetime,
        max_attempts: int,
    ) -> DocumentIngestionJobSnapshot: ...


class DocumentIngestionPublisherPort(Protocol):
    def publish(self, job_id: uuid.UUID) -> None: ...


class DocumentIngestionUnitOfWorkPort(Protocol):
    def flush(self) -> None: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...


class RequestDocumentIngestion:
    def __init__(
        self,
        *,
        repository: DocumentIngestionRepositoryPort,
        publisher: DocumentIngestionPublisherPort,
        unit_of_work: DocumentIngestionUnitOfWorkPort,
        progress: DocumentIngestionProgressPort | None = None,
    ) -> None:
        self.repository = repository
        self.publisher = publisher
        self.unit_of_work = unit_of_work
        self.progress = progress

    def execute(
        self, command: RequestDocumentIngestionCommand
    ) -> DocumentIngestionRequestResult:
        try:
            target = self.repository.lock_document_scope(
                command.organization_id,
                command.knowledge_base_id,
                command.document_id,
            )
            if target is None:
                self.unit_of_work.rollback()
                raise DocumentIngestionHidden()
            self._require_supported_target(target)

            active = self.repository.find_active_job(command.document_id)
            generation = (
                active.generation
                if active
                else self.repository.next_generation(command.document_id)
            )
            input_revision = _input_revision(target, command, generation=generation)
            settings_match = _settings_match(target, command.settings)
            if active is not None:
                if (
                    active.operation == command.operation
                    and active.input_revision == input_revision
                    and settings_match
                ):
                    self.unit_of_work.rollback()
                    deferred = self._publish(active.job_id)
                    return DocumentIngestionRequestResult(active, True, deferred)
                self.unit_of_work.rollback()
                raise DocumentIngestionConflict()

            if (
                command.required_document_status is not None
                and target.document_status != command.required_document_status
            ):
                raise DocumentIngestionPolicyBlocked("ingestion.configuration_invalid")
            if (
                command.require_document_revision_match
                and target.document_updated_at
                != command.expected_document_updated_at
            ):
                raise DocumentIngestionPolicyBlocked("connection.reference_conflict")

            now = self.repository.database_now()
            self.repository.apply_settings_and_mark_queued(
                target,
                command.settings,
                now=now,
            )
            job = self.repository.create_job(
                command=command,
                generation=generation,
                input_revision=input_revision,
                idempotency_key=build_idempotency_key(
                    organization_id=command.organization_id,
                    document_id=command.document_id,
                    operation=command.operation,
                    generation=generation,
                    input_revision=input_revision,
                ),
                now=now,
                max_attempts=DEFAULT_MAX_ATTEMPTS,
            )
            self.unit_of_work.flush()
            self.unit_of_work.commit()
        except (
            DocumentIngestionHidden,
            DocumentIngestionConflict,
            DocumentIngestionPolicyBlocked,
        ):
            self.unit_of_work.rollback()
            raise
        except Exception as exc:
            self.unit_of_work.rollback()
            raise _persistence_failure(exc) from exc

        clear_progress_projection(self.progress, command.document_id)
        deferred = self._publish(job.job_id)
        return DocumentIngestionRequestResult(job, False, deferred)

    @staticmethod
    def _require_supported_target(target: AdmissionDocumentSnapshot) -> None:
        if target.lifecycle_state != "active" or target.sync_state == "source_deleted":
            raise DocumentIngestionPolicyBlocked("ingestion.lifecycle_blocked")

    def _publish(self, job_id: uuid.UUID) -> bool:
        try:
            self.publisher.publish(job_id)
        except Exception:
            return True
        return False


class RequestKnowledgeBaseReindex:
    def __init__(
        self,
        *,
        repository: DocumentIngestionRepositoryPort,
        publisher: DocumentIngestionPublisherPort,
        unit_of_work: DocumentIngestionUnitOfWorkPort,
        progress: DocumentIngestionProgressPort | None = None,
    ) -> None:
        self.repository = repository
        self.publisher = publisher
        self.unit_of_work = unit_of_work
        self.progress = progress

    def execute(
        self, command: RequestKnowledgeBaseReindexCommand
    ) -> KnowledgeBaseReindexRequestResult:
        try:
            targets = self.repository.lock_knowledge_base_documents(
                command.organization_id,
                command.knowledge_base_id,
            )
            if targets is None:
                self.unit_of_work.rollback()
                raise DocumentIngestionHidden()
            for target in targets:
                RequestDocumentIngestion._require_supported_target(target)

            document_commands = tuple(
                RequestDocumentIngestionCommand(
                    actor_id=command.actor_id,
                    organization_id=command.organization_id,
                    knowledge_base_id=command.knowledge_base_id,
                    document_id=target.document_id,
                    operation="reindex",
                    settings=DocumentIngestionSettings(
                        embedding_model=command.embedding_model
                    ),
                )
                for target in targets
            )
            active_jobs = tuple(
                self.repository.find_active_job(target.document_id)
                for target in targets
            )
            if any(active_jobs):
                if not all(active_jobs):
                    self.unit_of_work.rollback()
                    raise DocumentIngestionConflict()
                reused_jobs: list[DocumentIngestionJobSnapshot] = []
                for target, document_command, active in zip(
                    targets, document_commands, active_jobs
                ):
                    assert active is not None
                    revision = _input_revision(
                        target,
                        document_command,
                        generation=active.generation,
                    )
                    if (
                        active.operation != "reindex"
                        or active.input_revision != revision
                        or not _settings_match(target, document_command.settings)
                    ):
                        self.unit_of_work.rollback()
                        raise DocumentIngestionConflict()
                    reused_jobs.append(active)
                self.unit_of_work.rollback()
                deferred = self._publish_all(tuple(reused_jobs))
                return KnowledgeBaseReindexRequestResult(
                    jobs=tuple(reused_jobs),
                    reused=True,
                    dispatch_deferred=deferred,
                )

            now = self.repository.database_now()
            self.repository.update_locked_knowledge_base_model(
                command.knowledge_base_id,
                command.embedding_model,
                now=now,
            )
            jobs: list[DocumentIngestionJobSnapshot] = []
            for target, document_command in zip(targets, document_commands):
                generation = self.repository.next_generation(target.document_id)
                revision = _input_revision(
                    target,
                    document_command,
                    generation=generation,
                )
                self.repository.apply_settings_and_mark_queued(
                    target,
                    document_command.settings,
                    now=now,
                )
                jobs.append(
                    self.repository.create_job(
                        command=document_command,
                        generation=generation,
                        input_revision=revision,
                        idempotency_key=build_idempotency_key(
                            organization_id=command.organization_id,
                            document_id=target.document_id,
                            operation="reindex",
                            generation=generation,
                            input_revision=revision,
                        ),
                        now=now,
                        max_attempts=DEFAULT_MAX_ATTEMPTS,
                    )
                )
            self.unit_of_work.flush()
            self.unit_of_work.commit()
        except (
            DocumentIngestionHidden,
            DocumentIngestionConflict,
            DocumentIngestionPolicyBlocked,
        ):
            self.unit_of_work.rollback()
            raise
        except Exception as exc:
            self.unit_of_work.rollback()
            raise DocumentIngestionPersistenceFailed() from exc

        for job in jobs:
            clear_progress_projection(self.progress, job.document_id)
        deferred = self._publish_all(tuple(jobs))
        return KnowledgeBaseReindexRequestResult(
            jobs=tuple(jobs),
            reused=False,
            dispatch_deferred=deferred,
        )

    def _publish_all(self, jobs: tuple[DocumentIngestionJobSnapshot, ...]) -> bool:
        deferred = False
        for job in jobs:
            try:
                self.publisher.publish(job.job_id)
            except Exception:
                deferred = True
        return deferred


class ReadDocumentIngestionStatus:
    def __init__(
        self,
        *,
        repository: DocumentIngestionRepositoryPort,
        unit_of_work: DocumentIngestionUnitOfWorkPort,
    ) -> None:
        self.repository = repository
        self.unit_of_work = unit_of_work

    def execute(self, document_id: uuid.UUID) -> DocumentIngestionJobSnapshot | None:
        job = self.repository.latest_job(document_id)
        self.unit_of_work.rollback()
        return job


class RedriveDocumentIngestion:
    def __init__(
        self,
        *,
        repository: DocumentIngestionRepositoryPort,
        publisher: DocumentIngestionPublisherPort,
        unit_of_work: DocumentIngestionUnitOfWorkPort,
        progress: DocumentIngestionProgressPort | None = None,
    ) -> None:
        self.repository = repository
        self.publisher = publisher
        self.unit_of_work = unit_of_work
        self.progress = progress

    def execute(
        self, command: RedriveDocumentIngestionCommand
    ) -> DocumentIngestionRequestResult:
        try:
            target = self.repository.lock_document_scope(
                command.organization_id,
                command.knowledge_base_id,
                command.document_id,
            )
            if target is None:
                self.unit_of_work.rollback()
                raise DocumentIngestionHidden()
            RequestDocumentIngestion._require_supported_target(target)
            if self.repository.find_active_job(command.document_id) is not None:
                self.unit_of_work.rollback()
                raise DocumentIngestionConflict()
            previous = self.repository.latest_job(command.document_id, lock=True)
            if (
                previous is None
                or previous.job_id != command.expected_job_id
                or previous.status != "dead_lettered"
                or not previous.retryable
            ):
                self.unit_of_work.rollback()
                raise DocumentIngestionPolicyBlocked("ingestion.retry_not_available")

            next_command = RequestDocumentIngestionCommand(
                actor_id=command.actor_id,
                organization_id=command.organization_id,
                knowledge_base_id=command.knowledge_base_id,
                document_id=command.document_id,
                operation=previous.operation,
                settings=DocumentIngestionSettings(
                    embedding_model=target.embedding_model
                ),
            )
            generation = self.repository.next_generation(command.document_id)
            revision = _input_revision(
                target,
                next_command,
                generation=generation,
            )
            now = self.repository.database_now()
            self.repository.apply_settings_and_mark_queued(
                target,
                next_command.settings,
                now=now,
            )
            job = self.repository.create_job(
                command=next_command,
                generation=generation,
                input_revision=revision,
                idempotency_key=build_idempotency_key(
                    organization_id=command.organization_id,
                    document_id=command.document_id,
                    operation=next_command.operation,
                    generation=generation,
                    input_revision=revision,
                ),
                now=now,
                max_attempts=DEFAULT_MAX_ATTEMPTS,
            )
            self.unit_of_work.flush()
            self.unit_of_work.commit()
        except (
            DocumentIngestionHidden,
            DocumentIngestionConflict,
            DocumentIngestionPolicyBlocked,
        ):
            self.unit_of_work.rollback()
            raise
        except Exception as exc:
            self.unit_of_work.rollback()
            raise DocumentIngestionPersistenceFailed() from exc

        clear_progress_projection(self.progress, command.document_id)
        try:
            self.publisher.publish(job.job_id)
            deferred = False
        except Exception:
            deferred = True
        return DocumentIngestionRequestResult(job, False, deferred)


def _input_revision(
    target: AdmissionDocumentSnapshot,
    command: RequestDocumentIngestionCommand,
    *,
    generation: int,
) -> str:
    settings = command.settings
    meta_info = {**dict(target.meta_info), **dict(settings.meta_updates)}
    for key in settings.meta_remove_keys:
        meta_info.pop(key, None)
    chunk_size = (
        settings.chunk_size if settings.chunk_size is not None else target.chunk_size
    )
    chunk_overlap = (
        settings.chunk_overlap
        if settings.chunk_overlap is not None
        else target.chunk_overlap
    )
    embedding_model = (
        settings.embedding_model
        if settings.embedding_model is not None
        else target.embedding_model
    )
    chunking_fingerprint = chunking_fingerprint_hash(
        meta_info=meta_info,
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        source_type=target.source_type,
    )
    return build_input_revision(
        document_id=target.document_id,
        operation=command.operation,
        generation=generation,
        active_document_version_id=target.active_document_version_id,
        content_hash=_protected_digest(target.content_hash),
        chunking_fingerprint=chunking_fingerprint,
        embedding_model=embedding_model,
        protected_source_revision=_protected_source_revision(meta_info),
    )


def _settings_match(
    target: AdmissionDocumentSnapshot,
    settings: DocumentIngestionSettings,
) -> bool:
    if settings.chunk_size is not None and settings.chunk_size != target.chunk_size:
        return False
    if (
        settings.chunk_overlap is not None
        and settings.chunk_overlap != target.chunk_overlap
    ):
        return False
    if (
        settings.embedding_model is not None
        and settings.embedding_model != target.embedding_model
    ):
        return False
    current_meta = dict(target.meta_info)
    if any(key in current_meta for key in settings.meta_remove_keys):
        return False
    return all(
        current_meta.get(key) == value for key, value in settings.meta_updates.items()
    )


def _protected_source_revision(meta_info: Mapping[str, Any]) -> str | None:
    for key in (
        "protected_source_revision",
        "source_revision_hash",
        "sync_revision_hash",
    ):
        value = meta_info.get(key)
        if isinstance(value, str) and _SHA256_PATTERN.fullmatch(value):
            return value
    return None


def _protected_digest(value: str | None) -> str | None:
    if isinstance(value, str) and _SHA256_PATTERN.fullmatch(value):
        return value
    return None


def _persistence_failure(exc: Exception) -> DocumentIngestionPersistenceFailed:
    reason_code = getattr(exc, "code", None)
    if reason_code not in _SAFE_PERSISTENCE_REASON_CODES:
        reason_code = "ingestion.admission_unavailable"
    return DocumentIngestionPersistenceFailed(reason_code)
