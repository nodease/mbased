class ConnectorTestError(Exception):
    code: str
    retry_after: int | None

    def __init__(self, code: str, *, retry_after: int | None = None) -> None:
        self.code = code
        self.retry_after = retry_after
        super().__init__(code)


class ConnectorTestRateLimited(ConnectorTestError):
    def __init__(self, retry_after: int) -> None:
        super().__init__(
            "connector.test_rate_limited",
            retry_after=max(1, min(60, retry_after)),
        )


class ConnectorTestBusy(ConnectorTestError):
    def __init__(self, retry_after: int = 1) -> None:
        super().__init__(
            "connector.test_busy",
            retry_after=max(1, min(60, retry_after)),
        )


class ConnectorTestAdmissionUnavailable(ConnectorTestError):
    def __init__(self) -> None:
        super().__init__("connector.admission_unavailable")


class ConnectorTargetNotAllowed(Exception):
    pass


class ConnectorProbeFailed(Exception):
    pass


class ConnectorProbeCapacityExceeded(Exception):
    pass


class ConnectorTestIngressError(Exception):
    status_code: int
    code: str

    def __init__(self, status_code: int, code: str) -> None:
        self.status_code = status_code
        self.code = code
        super().__init__(code)


class ConnectorTestPayloadInvalid(ConnectorTestIngressError):
    def __init__(self) -> None:
        super().__init__(400, "connector.test_payload_invalid")


class ConnectorTestPayloadTimeout(ConnectorTestIngressError):
    def __init__(self) -> None:
        super().__init__(408, "connector.test_payload_timeout")


class ConnectorTestPayloadTooLarge(ConnectorTestIngressError):
    def __init__(self) -> None:
        super().__init__(413, "connector.test_payload_too_large")


class ConnectorTestMediaTypeNotSupported(ConnectorTestIngressError):
    def __init__(self) -> None:
        super().__init__(415, "connector.test_media_type_not_supported")


__all__ = [
    "ConnectorProbeCapacityExceeded",
    "ConnectorProbeFailed",
    "ConnectorTargetNotAllowed",
    "ConnectorTestAdmissionUnavailable",
    "ConnectorTestBusy",
    "ConnectorTestError",
    "ConnectorTestIngressError",
    "ConnectorTestMediaTypeNotSupported",
    "ConnectorTestPayloadInvalid",
    "ConnectorTestPayloadTimeout",
    "ConnectorTestPayloadTooLarge",
    "ConnectorTestRateLimited",
]
