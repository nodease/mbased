class WebhookIngressError(Exception):
    """Framework-independent public webhook rejection."""

    status_code: int
    code: str

    def __init__(self, *, status_code: int, code: str) -> None:
        self.status_code = status_code
        self.code = code
        super().__init__(code)


class QuerySecretNotSupportedError(WebhookIngressError):
    def __init__(self) -> None:
        super().__init__(
            status_code=400,
            code="webhook.query_secret_not_supported",
        )


class CredentialAmbiguousError(WebhookIngressError):
    def __init__(self) -> None:
        super().__init__(status_code=400, code="webhook.credential_ambiguous")


class AuthenticationFailedError(WebhookIngressError):
    def __init__(self) -> None:
        super().__init__(status_code=403, code="webhook.authentication_failed")


class UnsupportedMediaTypeError(WebhookIngressError):
    def __init__(self) -> None:
        super().__init__(
            status_code=415,
            code="webhook.payload.unsupported_media_type",
        )


class PayloadTooLargeError(WebhookIngressError):
    def __init__(self) -> None:
        super().__init__(status_code=413, code="webhook.payload.too_large")


class PayloadTimeoutError(WebhookIngressError):
    def __init__(self) -> None:
        super().__init__(status_code=408, code="webhook.payload.timeout")


class PayloadInvalidError(WebhookIngressError):
    def __init__(self) -> None:
        super().__init__(status_code=400, code="webhook.payload.invalid")


__all__ = [
    "AuthenticationFailedError",
    "CredentialAmbiguousError",
    "PayloadInvalidError",
    "PayloadTimeoutError",
    "PayloadTooLargeError",
    "QuerySecretNotSupportedError",
    "UnsupportedMediaTypeError",
    "WebhookIngressError",
]
