from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

from apps.shared.db.models.workflow_node_secret import (
    WORKFLOW_NODE_SECRET_ACTIVE,
    WorkflowNodeSecret,
)
from apps.shared.services.credential_encryption import (
    CredentialEncryptionError,
    CredentialEncryptionService,
    EncryptedSecretEnvelope,
)

WORKFLOW_NODE_SECRET_REFERENCE_PREFIX = "workflow-node-secret://"
SLACK_API_DEFAULT_ENDPOINT = "https://slack.com/api/chat.postMessage"
WORKFLOW_NODE_SECRET_PARAMETERS = {
    ("slackPostNode", "bot_token"),
    ("slackPostNode", "url"),
    ("githubNode", "api_token"),
}


class WorkflowNodeSecretError(ValueError):
    """Safe workflow-node secret failure without secret-bearing details."""


class WorkflowNodeSecretStorageError(WorkflowNodeSecretError):
    """Safe availability failure for encrypted workflow-node secret storage."""


@dataclass(frozen=True)
class _WorkflowNodeSecretBinding:
    secret_id: UUID
    node_id: str
    node_type: str
    parameter_key: str


def get_workflow_node_secret_encryption_service() -> CredentialEncryptionService:
    return CredentialEncryptionService.from_environment_variables(
        keyring_environment_variable="WORKFLOW_NODE_SECRET_ENCRYPTION_KEYS",
        active_version_environment_variable=(
            "WORKFLOW_NODE_SECRET_ACTIVE_KEY_VERSION"
        ),
        subject_label="Workflow node secret",
    )


def workflow_node_secret_reference(secret_id: UUID) -> str:
    return f"{WORKFLOW_NODE_SECRET_REFERENCE_PREFIX}{secret_id}"


def workflow_node_secret_id(reference: str) -> UUID:
    if not isinstance(reference, str) or not reference.startswith(
        WORKFLOW_NODE_SECRET_REFERENCE_PREFIX
    ):
        raise WorkflowNodeSecretError("Workflow node secret is not available.")
    try:
        return UUID(reference.removeprefix(WORKFLOW_NODE_SECRET_REFERENCE_PREFIX))
    except (TypeError, ValueError) as exc:
        raise WorkflowNodeSecretError(
            "Workflow node secret is not available."
        ) from exc


def is_workflow_node_secret_reference(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        workflow_node_secret_id(value)
    except WorkflowNodeSecretError:
        return False
    return True


def _workflow_node_secret_bindings(
    nodes: object,
) -> list[_WorkflowNodeSecretBinding]:
    if not isinstance(nodes, list):
        raise WorkflowNodeSecretError("Workflow node secret reference is invalid.")
    bindings: list[_WorkflowNodeSecretBinding] = []
    binding_identities: set[tuple[str, str, str]] = set()
    pending = list(nodes)
    while pending:
        node = pending.pop()
        if isinstance(node, Mapping):
            node_id = str(node.get("id") or "")
            node_type = str(node.get("type") or "")
            data = node.get("data")
        else:
            node_id = str(getattr(node, "id", "") or "")
            node_type = str(getattr(node, "type", "") or "")
            data = getattr(node, "data", None)
        if not isinstance(data, Mapping):
            continue
        values: list[tuple[str, object]] = []
        if node_type == "slackPostNode":
            values.append(("url", data.get("url")))
            auth_config = data.get("authConfig")
            if isinstance(auth_config, Mapping):
                values.append(("bot_token", auth_config.get("token")))
        elif node_type == "githubNode":
            values.append(("api_token", data.get("api_token")))
        for parameter_key, value in values:
            if value in (None, ""):
                continue
            if not node_id or not isinstance(value, str):
                raise WorkflowNodeSecretError(
                    "Workflow node secret reference is invalid."
                )
            try:
                secret_id = workflow_node_secret_id(value)
            except WorkflowNodeSecretError as exc:
                raise WorkflowNodeSecretError(
                    "Workflow node secret reference is invalid."
                ) from exc
            binding_identity = (node_id, node_type, parameter_key)
            if binding_identity in binding_identities:
                raise WorkflowNodeSecretError(
                    "Workflow node secret reference is invalid."
                )
            binding_identities.add(binding_identity)
            bindings.append(
                _WorkflowNodeSecretBinding(
                    secret_id=secret_id,
                    node_id=node_id,
                    node_type=node_type,
                    parameter_key=parameter_key,
                )
            )
        subgraph = data.get("subGraph")
        nested_nodes = (
            subgraph.get("nodes") if isinstance(subgraph, Mapping) else None
        )
        if isinstance(nested_nodes, list):
            pending.extend(nested_nodes)
    return bindings


def validate_workflow_node_secret_persistence_boundary(nodes: object) -> None:
    _workflow_node_secret_bindings(nodes)


def validate_workflow_node_secret_reference_ownership(
    db,
    *,
    nodes: object,
    workflow_id: UUID,
    organization_id: UUID | None,
) -> None:
    bindings = _workflow_node_secret_bindings(nodes)
    if not bindings:
        return

    secret_ids = {binding.secret_id for binding in bindings}
    rows = (
        db.query(WorkflowNodeSecret)
        .filter(WorkflowNodeSecret.id.in_(secret_ids))
        .all()
    )
    rows_by_id = {row.id: row for row in rows}
    for binding in bindings:
        row = rows_by_id.get(binding.secret_id)
        if (
            row is None
            or row.workflow_id != workflow_id
            or row.organization_id != organization_id
            or row.node_id != binding.node_id
            or row.node_type != binding.node_type
            or row.parameter_key != binding.parameter_key
            or row.status != WORKFLOW_NODE_SECRET_ACTIVE
        ):
            raise WorkflowNodeSecretError(
                "Workflow node secret is not available."
            )


def redact_legacy_workflow_node_secrets(
    graph: Mapping[str, Any],
) -> dict[str, Any]:
    """Return a response-safe graph even when a legacy migration cannot run."""
    redacted = deepcopy(dict(graph))
    nodes = redacted.get("nodes")
    if not isinstance(nodes, list):
        return redacted
    pending = list(nodes)
    while pending:
        node = pending.pop()
        if not isinstance(node, dict):
            continue
        node_type = str(node.get("type") or "")
        data = node.get("data")
        if not isinstance(data, dict):
            continue
        slots: list[tuple[dict[str, Any], str]] = []
        if node_type == "slackPostNode":
            slots.append((data, "url"))
            auth_config = data.get("authConfig")
            if isinstance(auth_config, dict):
                slots.append((auth_config, "token"))
        elif node_type == "githubNode":
            slots.append((data, "api_token"))
        for container, key in slots:
            value = container.get(key)
            if value not in (None, "") and not is_workflow_node_secret_reference(
                value
            ):
                container.pop(key, None)
        subgraph = data.get("subGraph")
        nested_nodes = (
            subgraph.get("nodes") if isinstance(subgraph, dict) else None
        )
        if isinstance(nested_nodes, list):
            pending.extend(nested_nodes)
    return redacted


def migrate_legacy_workflow_graph_secrets(
    db,
    *,
    graph: Mapping[str, Any],
    encryption: CredentialEncryptionService,
    workflow_id: UUID,
    organization_id: UUID,
    user_id: UUID,
) -> tuple[dict[str, Any], bool]:
    migrated = deepcopy(dict(graph))
    nodes = migrated.get("nodes")
    if not isinstance(nodes, list):
        return migrated, False
    changed = False
    pending = list(nodes)
    while pending:
        node = pending.pop()
        if not isinstance(node, dict):
            continue
        node_id = str(node.get("id") or "")
        node_type = str(node.get("type") or "")
        data = node.get("data")
        if not node_id or not isinstance(data, dict):
            continue
        slots: list[tuple[dict[str, Any], str, str]] = []
        if node_type == "slackPostNode":
            slots.append((data, "url", "url"))
            auth_config = data.get("authConfig")
            if isinstance(auth_config, dict):
                slots.append((auth_config, "token", "bot_token"))
        elif node_type == "githubNode":
            slots.append((data, "api_token", "api_token"))
        for container, key, parameter_key in slots:
            value = container.get(key)
            if value in (None, "") or is_workflow_node_secret_reference(value):
                continue
            if (
                node_type == "slackPostNode"
                and parameter_key == "url"
                and str(data.get("slackMode", "api")) == "api"
                and value == SLACK_API_DEFAULT_ENDPOINT
            ):
                container.pop(key, None)
                changed = True
                continue
            if not isinstance(value, str):
                raise WorkflowNodeSecretError(
                    "Workflow node secret reference is invalid."
                )
            container[key] = WorkflowNodeSecretService.create_reference(
                db,
                encryption=encryption,
                workflow_id=workflow_id,
                organization_id=organization_id,
                user_id=user_id,
                node_id=node_id,
                node_type=node_type,
                parameter_key=parameter_key,
                secret_value=value,
            )
            changed = True
        subgraph = data.get("subGraph")
        nested_nodes = (
            subgraph.get("nodes") if isinstance(subgraph, dict) else None
        )
        if isinstance(nested_nodes, list):
            pending.extend(nested_nodes)
    return migrated, changed


class WorkflowNodeSecretService:
    @staticmethod
    def create_reference(
        db,
        *,
        encryption: CredentialEncryptionService,
        workflow_id: UUID,
        organization_id: UUID,
        user_id: UUID,
        node_id: str,
        node_type: str,
        parameter_key: str,
        secret_value: str,
    ) -> str:
        if (node_type, parameter_key) not in WORKFLOW_NODE_SECRET_PARAMETERS:
            raise WorkflowNodeSecretError(
                "Workflow node secret parameter is unsupported."
            )
        if not isinstance(secret_value, str) or not secret_value:
            raise WorkflowNodeSecretError("Workflow node secret is required.")
        try:
            envelope = encryption.encrypt(secret_value)
        except CredentialEncryptionError as exc:
            raise WorkflowNodeSecretStorageError(
                "Workflow node secret could not be stored."
            ) from exc
        row = WorkflowNodeSecret(
            id=uuid4(),
            organization_id=organization_id,
            workflow_id=workflow_id,
            node_id=node_id,
            node_type=node_type,
            parameter_key=parameter_key,
            encrypted_secret=envelope.ciphertext,
            encryption_key_version=envelope.key_version,
            encryption_algorithm=envelope.algorithm,
            status=WORKFLOW_NODE_SECRET_ACTIVE,
            created_by=user_id,
        )
        db.add(row)
        db.flush()
        return workflow_node_secret_reference(row.id)

    @staticmethod
    def resolve_reference(
        db,
        *,
        encryption: CredentialEncryptionService,
        reference: str,
        workflow_id: UUID,
        organization_id: UUID,
        node_id: str,
        node_type: str,
        parameter_key: str,
    ) -> str:
        secret_id = workflow_node_secret_id(reference)
        row = (
            db.query(WorkflowNodeSecret)
            .filter(
                WorkflowNodeSecret.id == secret_id,
                WorkflowNodeSecret.workflow_id == workflow_id,
                WorkflowNodeSecret.organization_id == organization_id,
                WorkflowNodeSecret.status == WORKFLOW_NODE_SECRET_ACTIVE,
            )
            .first()
        )
        if (
            row is None
            or row.id != secret_id
            or row.workflow_id != workflow_id
            or row.organization_id != organization_id
            or row.status != WORKFLOW_NODE_SECRET_ACTIVE
            or row.node_id != node_id
            or row.node_type != node_type
            or row.parameter_key != parameter_key
        ):
            raise WorkflowNodeSecretError(
                "Workflow node secret is not available."
            )
        try:
            return encryption.decrypt(
                EncryptedSecretEnvelope(
                    ciphertext=row.encrypted_secret,
                    key_version=row.encryption_key_version,
                    algorithm=row.encryption_algorithm,
                )
            )
        except CredentialEncryptionError as exc:
            raise WorkflowNodeSecretError(
                "Workflow node secret is not available."
            ) from exc


def resolve_runtime_workflow_node_secret(
    *,
    reference: str,
    execution_context: Mapping[str, Any],
    node_id: str,
    node_type: str,
    parameter_key: str,
) -> str:
    """Resolve one opaque graph reference immediately before provider I/O."""
    if not is_workflow_node_secret_reference(reference):
        return reference

    workflow_id = execution_context.get("workflow_id")
    organization_id = execution_context.get("organization_id")
    injected_resolver = execution_context.get("workflow_node_secret_resolver")
    if callable(injected_resolver):
        value = injected_resolver(
            reference=reference,
            workflow_id=workflow_id,
            organization_id=organization_id,
            node_id=node_id,
            node_type=node_type,
            parameter_key=parameter_key,
        )
        if not isinstance(value, str) or not value:
            raise WorkflowNodeSecretError(
                "Workflow node secret is not available."
            )
        return value

    try:
        workflow_uuid = UUID(str(workflow_id))
        organization_uuid = UUID(str(organization_id))
    except (TypeError, ValueError) as exc:
        raise WorkflowNodeSecretError(
            "Workflow node secret is not available."
        ) from exc

    session_factory = execution_context.get("db_session_factory")
    legacy_session = execution_context.get("db")
    owns_session = callable(session_factory)
    db = session_factory() if owns_session else legacy_session
    if db is None:
        raise WorkflowNodeSecretError("Workflow node secret is not available.")
    try:
        return WorkflowNodeSecretService.resolve_reference(
            db,
            encryption=get_workflow_node_secret_encryption_service(),
            reference=reference,
            workflow_id=workflow_uuid,
            organization_id=organization_uuid,
            node_id=node_id,
            node_type=node_type,
            parameter_key=parameter_key,
        )
    finally:
        if owns_session:
            db.close()
