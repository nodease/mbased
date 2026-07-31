"""Network policy enforced by every Sandbox execution entry point."""

NETWORK_ACCESS_UNSUPPORTED_REASON = "sandbox.network_access_unsupported"


class SandboxNetworkAccessUnsupported(ValueError):
    """Raised when a caller requests unsupported Sandbox network access."""

    reason_code = NETWORK_ACCESS_UNSUPPORTED_REASON

    def __init__(self) -> None:
        super().__init__("Sandbox network access is not supported.")


def require_network_access_disabled(enable_network: bool) -> None:
    """Fail closed before an execution can disable the NSJail network namespace."""
    if enable_network:
        raise SandboxNetworkAccessUnsupported()
