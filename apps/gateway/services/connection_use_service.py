from __future__ import annotations

from typing import Any

from fastapi import Request
from sqlalchemy.orm import Session

from apps.gateway.utils.api_errors import raise_api_error
from apps.shared.db.models.connection import Connection
from apps.shared.services.connection_use_resolver import (
    ConnectionUseDenied,
    ConnectionUseResolver,
    ConnectionUseUnavailable,
)


def resolve_connection_use_or_hidden(
    request: Request,
    db: Session,
    *,
    connection_id: Any,
    execution_subject_user_id: Any,
) -> Connection:
    try:
        return ConnectionUseResolver(db).resolve(
            connection_id,
            execution_subject_user_id=execution_subject_user_id,
        )
    except ConnectionUseDenied:
        raise_api_error(
            request,
            404,
            "resource.hidden",
            "Resource not found.",
        )
    except ConnectionUseUnavailable:
        raise_api_error(
            request,
            503,
            "connection.reference_unavailable",
            "The DB connection reference is temporarily unavailable.",
        )
