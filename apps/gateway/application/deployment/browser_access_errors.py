from __future__ import annotations


class BrowserAccessPolicyError(ValueError):
    """Safe application error for deployment browser policy validation."""

    def __init__(self, code: str, *, origin_index: int | None = None) -> None:
        message = code
        if origin_index is not None:
            message = f"{code} (origin_index={origin_index})"
        super().__init__(message)
        self.code = code
        self.origin_index = origin_index


class BrowserAccessResourceHidden(Exception):
    def __init__(self) -> None:
        super().__init__("Deployment browser access resource is unavailable")


class BrowserAccessConversationContractError(ValueError):
    """Safe application error for strict Public Chatbot activation."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code
