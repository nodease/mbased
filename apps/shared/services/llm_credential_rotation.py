from __future__ import annotations

from dataclasses import dataclass

from apps.shared.db.models.llm import LLMCredential
from apps.shared.services.llm_credential_config import LLMCredentialConfigService
from sqlalchemy import or_
from sqlalchemy.orm import Session

MAX_ROTATION_BATCH_SIZE = 1_000


class LLMCredentialRotationError(RuntimeError):
    """Safe bounded rotation failure without credential material."""


@dataclass(frozen=True)
class LLMCredentialRotationResult:
    processed: int
    pending: int
    batches: int


class LLMCredentialRotationService:
    def __init__(
        self,
        db: Session,
        *,
        config_service: LLMCredentialConfigService,
    ) -> None:
        self.db = db
        self.config_service = config_service

    def pending_count(self) -> int:
        return self.db.query(LLMCredential).filter(self._requires_rotation()).count()

    def rotate_batch(self, *, batch_size: int) -> int:
        if batch_size < 1 or batch_size > MAX_ROTATION_BATCH_SIZE:
            raise ValueError(
                f"batch_size must be between 1 and {MAX_ROTATION_BATCH_SIZE}."
            )

        try:
            credentials = (
                self.db.query(LLMCredential)
                .filter(self._requires_rotation())
                .order_by(LLMCredential.id.asc())
                .with_for_update(skip_locked=True)
                .limit(batch_size)
                .all()
            )
            for credential in credentials:
                config = self.config_service.load(credential)
                envelope = self.config_service.protect(config)
                credential.encrypted_config = envelope.ciphertext
                credential.encryption_key_version = envelope.key_version
                credential.encryption_algorithm = envelope.algorithm
            self.db.commit()
            return len(credentials)
        except Exception:
            self.db.rollback()
            raise LLMCredentialRotationError(
                "LLM credential rotation batch failed."
            ) from None

    def rotate(
        self, *, batch_size: int, max_batches: int
    ) -> LLMCredentialRotationResult:
        if max_batches < 1:
            raise ValueError("max_batches must be at least 1.")

        processed = 0
        completed_batches = 0
        for _ in range(max_batches):
            batch_processed = self.rotate_batch(batch_size=batch_size)
            if batch_processed == 0:
                break
            processed += batch_processed
            completed_batches += 1
        return LLMCredentialRotationResult(
            processed=processed,
            pending=self.pending_count(),
            batches=completed_batches,
        )

    def _requires_rotation(self):
        return or_(
            LLMCredential.encryption_key_version.is_(None),
            LLMCredential.encryption_algorithm.is_(None),
            LLMCredential.encryption_key_version
            != self.config_service.active_key_version,
            LLMCredential.encryption_algorithm != self.config_service.algorithm,
        )
