"""Framework-free embedding model identity shared across runtime boundaries."""

from __future__ import annotations

import uuid
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class EmbeddingModelBinding:
    model_id: uuid.UUID
    provider_id: uuid.UUID
    model_identifier: str

    def __post_init__(self) -> None:
        if not isinstance(self.model_id, uuid.UUID) or not isinstance(
            self.provider_id, uuid.UUID
        ):
            raise ValueError("embedding model identifiers must be UUIDs")
        if (
            not isinstance(self.model_identifier, str)
            or not self.model_identifier
            or self.model_identifier != self.model_identifier.strip()
            or len(self.model_identifier) > 255
        ):
            raise ValueError("embedding model API identifier is invalid")

    @property
    def id(self) -> uuid.UUID:
        return self.model_id

    @property
    def model_id_for_api_call(self) -> str:
        return self.model_identifier


__all__ = ["EmbeddingModelBinding"]
