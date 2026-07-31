from __future__ import annotations

import base64
import re
import uuid
from dataclasses import dataclass
from typing import Iterable

_INVOCATION_DOMAIN = "nodease.node-invocation.v1"
_SCHEDULE_DOMAIN = "nodease:schedule-execution:v1"
_SEGMENT_KINDS = frozenset({"root", "loop", "subworkflow", "node"})
_CANONICAL_NON_NEGATIVE_INTEGER = re.compile(r"(?:0|[1-9][0-9]*)\Z")


def _uuid(value: uuid.UUID | str, *, label: str) -> uuid.UUID:
    try:
        return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))
    except (TypeError, ValueError, AttributeError) as exc:
        raise ValueError(f"invalid {label}") from exc


def ensure_execution_id(value: uuid.UUID | str | None = None) -> uuid.UUID:
    if value is None:
        return uuid.uuid4()
    return _uuid(value, label="execution identity")


def schedule_execution_id(claim_id: uuid.UUID | str) -> uuid.UUID:
    canonical_claim_id = _uuid(claim_id, label="schedule claim identity")
    return uuid.uuid5(
        uuid.NAMESPACE_URL,
        f"{_SCHEDULE_DOMAIN}:{str(canonical_claim_id).lower()}",
    )


@dataclass(frozen=True)
class InvocationSegment:
    kind: str
    node_id: str
    scope: str

    def __post_init__(self) -> None:
        if self.kind not in _SEGMENT_KINDS:
            raise ValueError("invalid invocation segment kind")
        if not isinstance(self.node_id, str) or not isinstance(self.scope, str):
            raise ValueError("invalid invocation segment value")


def _field(name: str, value: str) -> bytes:
    name_bytes = name.encode("utf-8")
    value_bytes = value.encode("utf-8")
    if len(name_bytes) >= 2**32 or len(value_bytes) >= 2**32:
        raise ValueError("canonical field is too large")
    return (
        len(name_bytes).to_bytes(4, "big")
        + name_bytes
        + len(value_bytes).to_bytes(4, "big")
        + value_bytes
    )


def canonical_invocation_bytes(
    execution_id: uuid.UUID | str,
    segments: Iterable[InvocationSegment],
) -> bytes:
    canonical_execution_id = _uuid(execution_id, label="execution identity")
    segment_values = tuple(segments)
    if len(segment_values) < 2:
        raise ValueError("invocation path requires root and node segments")
    if segment_values[0].kind != "root" or segment_values[-1].kind != "node":
        raise ValueError("invocation path must start with root and end with node")
    if sum(segment.kind == "root" for segment in segment_values) != 1:
        raise ValueError("invocation path requires exactly one root")
    if sum(segment.kind == "node" for segment in segment_values) != 1:
        raise ValueError("invocation path requires exactly one terminal node")
    if any(
        segment.kind not in {"loop", "subworkflow"} for segment in segment_values[1:-1]
    ):
        raise ValueError("invalid nested invocation segment")

    root = segment_values[0]
    if root.node_id or not _is_canonical_uuid(root.scope):
        raise ValueError("invalid root invocation segment")
    for segment in segment_values[1:-1]:
        if not segment.node_id:
            raise ValueError("invalid nested invocation segment")
        if segment.kind == "loop" and not _is_canonical_non_negative_integer(
            segment.scope
        ):
            raise ValueError("invalid loop invocation segment")
        if segment.kind == "subworkflow" and not _is_canonical_uuid(segment.scope):
            raise ValueError("invalid subworkflow invocation segment")
    terminal = segment_values[-1]
    if not terminal.node_id or not _is_canonical_non_negative_integer(terminal.scope):
        raise ValueError("invalid node invocation segment")

    framed = bytearray()
    framed.extend(_field("domain", _INVOCATION_DOMAIN))
    framed.extend(_field("execution_id", str(canonical_execution_id).lower()))
    framed.extend(_field("segment_count", str(len(segment_values))))
    for segment in segment_values:
        framed.extend(_field("segment_kind", segment.kind))
        framed.extend(_field("segment_node_id", segment.node_id))
        framed.extend(_field("segment_scope", segment.scope))
    return bytes(framed)


def _is_canonical_uuid(value: str) -> bool:
    try:
        return str(uuid.UUID(value)) == value
    except (TypeError, ValueError, AttributeError):
        return False


def _is_canonical_non_negative_integer(value: str) -> bool:
    return _CANONICAL_NON_NEGATIVE_INTEGER.fullmatch(value) is not None


def derive_node_invocation_id(
    execution_id: uuid.UUID | str,
    segments: Iterable[InvocationSegment],
) -> uuid.UUID:
    framed = canonical_invocation_bytes(execution_id, segments)
    name = base64.urlsafe_b64encode(framed).decode("ascii").rstrip("=")
    return uuid.uuid5(
        uuid.NAMESPACE_URL,
        f"nodease:node-invocation:v1:{name}",
    )
