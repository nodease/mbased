from __future__ import annotations

from sqlalchemy.orm import Session

_SESSION_KEY = "_manual_audit_ownership"
_VALID_OPERATIONS = {"created", "updated", "deleted"}


def register_manual_audit_ownership(
    session: Session,
    obj: object,
    operation: str,
) -> None:
    if operation not in _VALID_OPERATIONS:
        raise ValueError(f"Unsupported audit operation: {operation}")
    ownership = session.info.setdefault(_SESSION_KEY, set())
    ownership.add((type(obj), id(obj), operation))


def is_manually_audited(
    session: Session,
    obj: object,
    operation: str,
) -> bool:
    ownership = session.info.get(_SESSION_KEY, set())
    return (type(obj), id(obj), operation) in ownership


def clear_manual_audit_ownership(session: Session) -> None:
    session.info.pop(_SESSION_KEY, None)
