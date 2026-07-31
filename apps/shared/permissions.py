from typing import Any

AUTH_STATE_NONE = "none"
AUTH_STATE_VIEWER = "viewer"
AUTH_STATE_OPERATOR = "operator"
AUTH_STATE_BUILDER = "builder"
AUTH_STATE_MANAGER = "manager"
AUTH_STATE_AUDITOR = "auditor"
AUTH_STATE_RAW_AUDITOR = "raw_auditor"

CANONICAL_AUTH_STATES = {
    AUTH_STATE_NONE,
    AUTH_STATE_VIEWER,
    AUTH_STATE_OPERATOR,
    AUTH_STATE_BUILDER,
    AUTH_STATE_MANAGER,
    AUTH_STATE_AUDITOR,
    AUTH_STATE_RAW_AUDITOR,
}

LEGACY_AUTH_STATE_MAP = {
    "read": AUTH_STATE_VIEWER,
    "write": AUTH_STATE_BUILDER,
    "execute": AUTH_STATE_OPERATOR,
    "admin": AUTH_STATE_MANAGER,
}

AUTH_STATE_RANK = {
    AUTH_STATE_NONE: 0,
    AUTH_STATE_VIEWER: 1,
    AUTH_STATE_AUDITOR: 1,
    AUTH_STATE_OPERATOR: 2,
    AUTH_STATE_RAW_AUDITOR: 2,
    AUTH_STATE_BUILDER: 3,
    AUTH_STATE_MANAGER: 4,
}

RESOURCE_AUTH_STATES = {
    AUTH_STATE_NONE,
    AUTH_STATE_VIEWER,
    AUTH_STATE_OPERATOR,
    AUTH_STATE_BUILDER,
    AUTH_STATE_MANAGER,
}

WORKFLOW_ACTION_MINIMUM_AUTH_STATE = {
    "read": AUTH_STATE_VIEWER,
    "execute": AUTH_STATE_OPERATOR,
    "write": AUTH_STATE_BUILDER,
    "deploy": AUTH_STATE_MANAGER,
    "manage": AUTH_STATE_MANAGER,
}

LLM_CREDENTIAL_ACTION_MINIMUM_AUTH_STATE = {
    "read": AUTH_STATE_VIEWER,
    "use": AUTH_STATE_OPERATOR,
    "write": AUTH_STATE_MANAGER,
    "manage": AUTH_STATE_MANAGER,
}

MAIL_CREDENTIAL_ACTION_MINIMUM_AUTH_STATE = {
    "read": AUTH_STATE_VIEWER,
    "use": AUTH_STATE_OPERATOR,
    "write": AUTH_STATE_MANAGER,
    "manage": AUTH_STATE_MANAGER,
}

KNOWLEDGE_BASE_ACTION_MINIMUM_AUTH_STATE = {
    "read": AUTH_STATE_VIEWER,
    "use": AUTH_STATE_OPERATOR,
    "write": AUTH_STATE_BUILDER,
    "content_read": AUTH_STATE_BUILDER,
    "manage": AUTH_STATE_MANAGER,
}

KNOWLEDGE_DOMAIN_ACTIONS = frozenset(
    {
        "catalog_manage",
        "permission_delegate",
        "lifecycle_manage",
        "sync_manage",
    }
)


def normalize_auth_state(auth_state: Any) -> str:
    value = str(auth_state or AUTH_STATE_NONE).lower()
    value = LEGACY_AUTH_STATE_MAP.get(value, value)
    if value not in AUTH_STATE_RANK:
        return AUTH_STATE_NONE
    return value


def is_canonical_auth_state(auth_state: Any) -> bool:
    return str(auth_state or "").lower() in CANONICAL_AUTH_STATES


def stronger_auth_state(left: Any, right: Any) -> str:
    normalized_left = normalize_auth_state(left)
    normalized_right = normalize_auth_state(right)
    if AUTH_STATE_RANK[normalized_right] > AUTH_STATE_RANK[normalized_left]:
        return normalized_right
    return normalized_left


def normalize_resource_auth_state(auth_state: Any) -> str:
    value = normalize_auth_state(auth_state)
    if value not in RESOURCE_AUTH_STATES:
        return AUTH_STATE_NONE
    return value


def stronger_resource_auth_state(left: Any, right: Any) -> str:
    normalized_left = normalize_resource_auth_state(left)
    normalized_right = normalize_resource_auth_state(right)
    if AUTH_STATE_RANK[normalized_right] > AUTH_STATE_RANK[normalized_left]:
        return normalized_right
    return normalized_left


def auth_state_at_least(auth_state: Any, minimum: Any) -> bool:
    normalized_state = normalize_auth_state(auth_state)
    normalized_minimum = normalize_auth_state(minimum)
    return AUTH_STATE_RANK[normalized_state] >= AUTH_STATE_RANK[normalized_minimum]


def workflow_auth_state_allows(auth_state: Any, action: str) -> bool:
    minimum = WORKFLOW_ACTION_MINIMUM_AUTH_STATE.get(action)
    if minimum is None:
        return False
    return auth_state_at_least(normalize_resource_auth_state(auth_state), minimum)


def llm_credential_auth_state_allows(auth_state: Any, action: str) -> bool:
    minimum = LLM_CREDENTIAL_ACTION_MINIMUM_AUTH_STATE.get(action)
    if minimum is None:
        return False
    return auth_state_at_least(normalize_resource_auth_state(auth_state), minimum)


def mail_credential_auth_state_allows(auth_state: Any, action: str) -> bool:
    minimum = MAIL_CREDENTIAL_ACTION_MINIMUM_AUTH_STATE.get(action)
    if minimum is None:
        return False
    return auth_state_at_least(normalize_resource_auth_state(auth_state), minimum)


def knowledge_base_auth_state_allows(auth_state: Any, action: str) -> bool:
    minimum = KNOWLEDGE_BASE_ACTION_MINIMUM_AUTH_STATE.get(action)
    if minimum is None:
        return False
    return auth_state_at_least(normalize_resource_auth_state(auth_state), minimum)
