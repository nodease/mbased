from __future__ import annotations

import math
import re
import uuid
from dataclasses import dataclass, field


_HOST_LABEL_PATTERN = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")


@dataclass(frozen=True, slots=True)
class TrustedLocalConnectorTarget:
    host: str = field(repr=False)
    port: int = field(repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.host, str) or not self.host:
            raise ValueError("trusted local target host must be non-empty")
        if self.host != self.host.strip().rstrip(".").lower():
            raise ValueError("trusted local target host must be canonical")
        if len(self.host) > 253 or any(
            marker in self.host for marker in ("://", "/", "@", "?", "#", "*", ":")
        ):
            raise ValueError("trusted local target host is invalid")
        labels = self.host.split(".")
        if all(label.isdigit() for label in labels) or any(
            not _HOST_LABEL_PATTERN.fullmatch(label) for label in labels
        ):
            raise ValueError("trusted local target host is invalid")
        if (
            isinstance(self.port, bool)
            or not isinstance(self.port, int)
            or self.port < 1
            or self.port > 65535
        ):
            raise ValueError("trusted local target port is invalid")


@dataclass(frozen=True, slots=True)
class ConnectorTestPolicy:
    allowed_ports: frozenset[int] = frozenset({5432})
    trusted_local_targets: frozenset[TrustedLocalConnectorTarget] = field(
        default_factory=frozenset,
        repr=False,
    )
    trusted_local_ca_file: str | None = field(default=None, repr=False)
    rate_window_seconds: int = 60
    user_rate_limit: int = 5
    organization_rate_limit: int = 30
    network_rate_limit: int = 20
    user_concurrency_limit: int = 1
    organization_concurrency_limit: int = 4
    global_concurrency_limit: int = 16
    connect_timeout_seconds: int = 5
    statement_timeout_seconds: int = 3
    response_timeout_seconds: float = 10.0
    probe_hard_timeout_seconds: float = 20.0
    redis_operation_timeout_seconds: float = 1.0
    lease_ttl_seconds: int = 30

    def __post_init__(self) -> None:
        if not isinstance(self.allowed_ports, frozenset) or not self.allowed_ports:
            raise ValueError("allowed_ports must be a non-empty frozenset")
        if len(self.allowed_ports) > 16:
            raise ValueError("allowed_ports must not contain more than 16 ports")
        if any(
            isinstance(port, bool)
            or not isinstance(port, int)
            or port < 1
            or port > 65535
            for port in self.allowed_ports
        ):
            raise ValueError("allowed_ports must contain valid TCP ports")
        if not isinstance(self.trusted_local_targets, frozenset):
            raise ValueError("trusted_local_targets must be a frozenset")
        if len(self.trusted_local_targets) > 4:
            raise ValueError("trusted_local_targets must not contain more than 4 targets")
        if any(
            not isinstance(target, TrustedLocalConnectorTarget)
            for target in self.trusted_local_targets
        ):
            raise ValueError("trusted_local_targets contains an invalid target")
        if any(
            target.port not in self.allowed_ports
            for target in self.trusted_local_targets
        ):
            raise ValueError("trusted local target port must be deployment-allowed")
        if self.trusted_local_ca_file is not None:
            if (
                not isinstance(self.trusted_local_ca_file, str)
                or not self.trusted_local_ca_file.strip()
                or self.trusted_local_ca_file != self.trusted_local_ca_file.strip()
            ):
                raise ValueError("trusted_local_ca_file must be a non-empty path")
        if bool(self.trusted_local_targets) != bool(self.trusted_local_ca_file):
            raise ValueError(
                "trusted local targets and CA file must be configured together"
            )
        positive_values = {
            "rate_window_seconds": self.rate_window_seconds,
            "user_rate_limit": self.user_rate_limit,
            "organization_rate_limit": self.organization_rate_limit,
            "network_rate_limit": self.network_rate_limit,
            "user_concurrency_limit": self.user_concurrency_limit,
            "organization_concurrency_limit": self.organization_concurrency_limit,
            "global_concurrency_limit": self.global_concurrency_limit,
            "connect_timeout_seconds": self.connect_timeout_seconds,
            "statement_timeout_seconds": self.statement_timeout_seconds,
            "response_timeout_seconds": self.response_timeout_seconds,
            "probe_hard_timeout_seconds": self.probe_hard_timeout_seconds,
            "redis_operation_timeout_seconds": (
                self.redis_operation_timeout_seconds
            ),
            "lease_ttl_seconds": self.lease_ttl_seconds,
        }
        for name, value in positive_values.items():
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be positive")
        if self.rate_window_seconds > 300:
            raise ValueError("rate_window_seconds must not exceed 300")
        if self.user_rate_limit > 100:
            raise ValueError("user_rate_limit must not exceed 100")
        if self.organization_rate_limit > 1000:
            raise ValueError("organization_rate_limit must not exceed 1000")
        if self.network_rate_limit > 1000:
            raise ValueError("network_rate_limit must not exceed 1000")
        if not (
            self.user_concurrency_limit
            <= self.organization_concurrency_limit
            <= self.global_concurrency_limit
            <= 128
        ):
            raise ValueError("connector test concurrency limits are inconsistent")
        if self.user_rate_limit > self.organization_rate_limit:
            raise ValueError("user_rate_limit must not exceed organization_rate_limit")
        if self.connect_timeout_seconds > 10:
            raise ValueError("connect_timeout_seconds must not exceed 10")
        if self.statement_timeout_seconds > 10:
            raise ValueError("statement_timeout_seconds must not exceed 10")
        if self.response_timeout_seconds > 30:
            raise ValueError("response_timeout_seconds must not exceed 30")
        if self.probe_hard_timeout_seconds > 60:
            raise ValueError("probe_hard_timeout_seconds must not exceed 60")
        if self.redis_operation_timeout_seconds > 5:
            raise ValueError("redis_operation_timeout_seconds must not exceed 5")
        if self.lease_ttl_seconds > 120:
            raise ValueError("lease_ttl_seconds must not exceed 120")
        if self.connect_timeout_seconds >= self.response_timeout_seconds:
            raise ValueError("connect timeout must be shorter than response timeout")
        if self.statement_timeout_seconds >= self.response_timeout_seconds:
            raise ValueError("statement timeout must be shorter than response timeout")
        if self.response_timeout_seconds >= self.probe_hard_timeout_seconds:
            raise ValueError("response timeout must be shorter than probe hard timeout")
        if self.redis_operation_timeout_seconds >= self.response_timeout_seconds:
            raise ValueError(
                "Redis operation timeout must be shorter than response timeout"
            )
        if self.redis_operation_timeout_seconds * 3 >= self.lease_ttl_seconds:
            raise ValueError(
                "Redis operation timeout must be shorter than one third of lease TTL"
            )
        if self.response_timeout_seconds >= self.lease_ttl_seconds:
            raise ValueError("response timeout must be shorter than lease TTL")


@dataclass(frozen=True, slots=True)
class ConnectorTestCommand:
    organization_id: uuid.UUID
    actor_id: uuid.UUID
    network_address: str = field(repr=False)
    host: str = field(repr=False)
    port: int = field(repr=False)
    database: str = field(repr=False)
    username: str = field(repr=False)
    password: str = field(repr=False)
    ssh_enabled: bool = False


@dataclass(frozen=True, slots=True)
class AdmissionLease:
    member: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class ConnectorTestResult:
    success: bool
    message: str
    reason_code: str | None = None


SUCCESS_RESULT = ConnectorTestResult(
    success=True,
    message="데이터베이스 연결에 성공했습니다.",
)


def failure_result(reason_code: str) -> ConnectorTestResult:
    messages = {
        "connector.ssh_probe_not_supported": "SSH 연결 테스트는 지원하지 않습니다.",
        "connector.target_not_allowed": "정책상 허용되지 않은 연결 대상입니다.",
        "connector.connection_timeout": "제한 시간 내에 연결을 확인하지 못했습니다.",
        "connector.connection_failed": "데이터베이스 연결을 확인하지 못했습니다.",
        "connector.test_busy": "연결 테스트 처리 용량이 사용 중입니다.",
        "connector.admission_unavailable": "연결 테스트 제한 서비스를 사용할 수 없습니다.",
    }
    return ConnectorTestResult(
        success=False,
        message=messages[reason_code],
        reason_code=reason_code,
    )


__all__ = [
    "AdmissionLease",
    "ConnectorTestCommand",
    "ConnectorTestPolicy",
    "ConnectorTestResult",
    "SUCCESS_RESULT",
    "TrustedLocalConnectorTarget",
    "failure_result",
]
