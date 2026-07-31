from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Mapping
from uuid import UUID

from sqlalchemy.orm import Session

from apps.shared.services.connection_use_resolver import ConnectionUseResolver


_SELECTION_KEYS = frozenset(
    {"columns", "sensitive_columns", "table_name", "template"}
)
_JOIN_KEYS = frozenset({"base_table", "enabled", "joins"})
_JOIN_EDGE_KEYS = frozenset(
    {"from_column", "from_table", "to_column", "to_table"}
)
_CHUNK_SETTING_KEYS = frozenset({"chunk_size", "overlap"})
_MAX_CONFIG_TEXT_LENGTH = 65_536


class KnowledgeDbSourceConfigInvalid(ValueError):
    code = "validation.failed"


@dataclass(frozen=True)
class ValidatedKnowledgeDbSourceConfig:
    connection_id: UUID
    persisted_db_config: dict[str, Any]

    @property
    def runtime_config(self) -> dict[str, Any]:
        return {
            "connection_id": str(self.connection_id),
            **deepcopy(self.persisted_db_config),
        }


def validate_knowledge_db_source_config(
    db: Session,
    *,
    execution_subject_user_id: Any,
    stored_meta_info: Any,
    submitted_db_config: Any,
) -> ValidatedKnowledgeDbSourceConfig:
    stored_meta = _mapping_or_empty(stored_meta_info)
    if submitted_db_config is None:
        stored_nested = _stored_mapping_or_empty(stored_meta.get("db_config"))
        selected_config = stored_nested
    elif isinstance(submitted_db_config, Mapping):
        selected_config = submitted_db_config
    else:
        raise KnowledgeDbSourceConfigInvalid()

    connection_id = selected_config.get("connection_id")
    if connection_id is None:
        connection_id = stored_meta.get("connection_id")
    if connection_id is None and submitted_db_config is not None:
        stored_nested = _stored_mapping_or_empty(stored_meta.get("db_config"))
        connection_id = stored_nested.get("connection_id")
    connection = ConnectionUseResolver(db).resolve(
        connection_id,
        execution_subject_user_id=execution_subject_user_id,
    )
    persisted = _project_persisted_config(selected_config)
    return ValidatedKnowledgeDbSourceConfig(
        connection_id=connection.id,
        persisted_db_config=persisted,
    )


def remove_legacy_connection_details(meta_info: dict[str, Any]) -> None:
    for key in (
        "connection_name",
        "db_type",
        "type",
        "host",
        "port",
        "database",
        "username",
        "password",
        "encrypted_password",
        "use_ssh",
        "ssh",
        "ssh_host",
        "ssh_port",
        "ssh_username",
        "ssh_auth_type",
        "ssh_password",
        "ssh_private_key",
        "encrypted_ssh_password",
        "encrypted_ssh_private_key",
    ):
        meta_info.pop(key, None)


def _mapping_or_empty(value: Any) -> Mapping[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise KnowledgeDbSourceConfigInvalid()
    return value


def _stored_mapping_or_empty(value: Any) -> Mapping[str, Any]:
    if isinstance(value, str):
        if len(value) > _MAX_CONFIG_TEXT_LENGTH:
            raise KnowledgeDbSourceConfigInvalid()
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            raise KnowledgeDbSourceConfigInvalid() from exc
    return _mapping_or_empty(value)


def _project_persisted_config(config: Mapping[str, Any]) -> dict[str, Any]:
    projected: dict[str, Any] = {}
    if "selections" in config:
        projected["selections"] = _project_mapping_list(
            config["selections"],
            allowed_keys=_SELECTION_KEYS,
        )
    for key in ("selected_items", "sensitive_columns"):
        if key in config:
            projected[key] = _project_list_mapping(config[key])
    if "aliases" in config:
        projected["aliases"] = _project_nested_mapping(config["aliases"])
    for key in ("template", "limit", "enable_auto_chunking"):
        if key in config:
            projected[key] = deepcopy(config[key])
    if "chunk_settings" in config:
        projected["chunk_settings"] = _project_mapping(
            config["chunk_settings"],
            allowed_keys=_CHUNK_SETTING_KEYS,
        )
    if "join_config" in config:
        join_config = _project_mapping(
            config["join_config"],
            allowed_keys=_JOIN_KEYS,
        )
        if "joins" in join_config:
            join_config["joins"] = _project_mapping_list(
                join_config["joins"],
                allowed_keys=_JOIN_EDGE_KEYS,
            )
        projected["join_config"] = join_config
    return projected


def _project_mapping(
    value: Any,
    *,
    allowed_keys: frozenset[str],
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise KnowledgeDbSourceConfigInvalid()
    return {
        key: deepcopy(item)
        for key, item in value.items()
        if key in allowed_keys
    }


def _project_mapping_list(
    value: Any,
    *,
    allowed_keys: frozenset[str],
) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise KnowledgeDbSourceConfigInvalid()
    return [
        _project_mapping(item, allowed_keys=allowed_keys)
        for item in value
    ]


def _project_list_mapping(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise KnowledgeDbSourceConfigInvalid()
    if any(not isinstance(item, list) for item in value.values()):
        raise KnowledgeDbSourceConfigInvalid()
    return {str(key): deepcopy(item) for key, item in value.items()}


def _project_nested_mapping(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise KnowledgeDbSourceConfigInvalid()
    projected: dict[str, Any] = {}
    for key, item in value.items():
        if not isinstance(item, Mapping):
            raise KnowledgeDbSourceConfigInvalid()
        projected[str(key)] = {
            str(nested_key): deepcopy(nested_value)
            for nested_key, nested_value in item.items()
        }
    return projected
