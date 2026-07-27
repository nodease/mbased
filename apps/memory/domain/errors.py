from __future__ import annotations


class MemoryDomainError(RuntimeError):
    """Base typed error containing only a safe public reason code."""

    code = "memory.invariant_violation"

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message or self.code)


class SessionNotFoundError(MemoryDomainError):
    code = "memory.session_hidden"


class SessionNotActiveError(MemoryDomainError):
    code = "memory.session_hidden"


class SessionClosedError(SessionNotActiveError):
    code = "memory.session_closed"


class StaleRevisionError(MemoryDomainError):
    code = "memory.stale_revision"


class StaleLifecycleRevisionError(StaleRevisionError):
    code = "memory.stale_lifecycle_revision"


class StaleTurnVersionError(StaleRevisionError):
    code = "memory.stale_turn_version"


class ActiveTurnConflictError(MemoryDomainError):
    code = "memory.active_turn_conflict"


class DuplicateRequestConflictError(MemoryDomainError):
    code = "memory.duplicate_request_conflict"


class InvalidTurnTransitionError(MemoryDomainError):
    code = "memory.stale_turn_version"


class DispatchStateConflictError(StaleRevisionError):
    code = "memory.dispatch_state_conflict"


class MemoryAdapterUnavailableError(MemoryDomainError):
    code = "memory.adapter_unavailable"


class PublicConversationFeatureDisabledError(MemoryDomainError):
    code = "memory.feature_unavailable"


class EntryNotFoundError(MemoryDomainError):
    code = "memory.entry_not_found"


class MemoryContextConflictError(MemoryDomainError):
    code = "memory.context_conflict"


class MemoryContextUnavailableError(MemoryDomainError):
    code = "memory.context_unavailable"


class AccessGrantNotUsableError(MemoryDomainError):
    """A public grant must be rendered as a resource-hidden failure."""

    code = "memory.session_hidden"


class AccessGrantScopeError(AccessGrantNotUsableError):
    """The grant does not bind to the requested public deployment surface."""


class SecretReplayExpiredError(MemoryDomainError):
    code = "memory.secret_replay_expired"


class PurgeReceiptNotUsableError(MemoryDomainError):
    code = "memory.session_hidden"


class PublicConversationRateLimitedError(MemoryDomainError):
    code = "memory.rate_limited"

    def __init__(self, retry_after_seconds: int = 1) -> None:
        self.retry_after_seconds = max(1, min(3600, int(retry_after_seconds)))
        super().__init__()


class PublicConversationTurnLimitExceededError(MemoryDomainError):
    code = "memory.turn_limit_exceeded"
