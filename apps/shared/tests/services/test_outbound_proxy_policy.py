from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest
from apps.shared.services.outbound_proxy_policy import (
    OutboundProxyConfigurationError,
    OutboundTransportMode,
    outbound_proxy_policy_from_environment,
)


def _proxy_environment(**overrides: str) -> dict[str, str]:
    environment = {
        "NODE_ENV": "production",
        "OUTBOUND_TRANSPORT_MODE": "proxy_guarded_external",
        "OUTBOUND_PROXY_URL": "http://egress-proxy:3128",
        "OUTBOUND_PROXY_ALLOWED_HOSTS": "egress-proxy",
        "OUTBOUND_PROXY_POLICY_REVISION": "proxy-v1",
    }
    environment.update(overrides)
    return environment


def test_proxy_policy_accepts_only_the_exact_server_owned_endpoint() -> None:
    policy = outbound_proxy_policy_from_environment(_proxy_environment())

    assert policy.mode is OutboundTransportMode.PROXY_GUARDED_EXTERNAL
    assert policy.proxy_url == "http://egress-proxy:3128"
    assert policy.allowed_proxy_hosts == ("egress-proxy",)
    assert policy.policy_revision == "proxy-v1"


def test_proxy_policy_accepts_the_workflow_http_compatible_listener() -> None:
    policy = outbound_proxy_policy_from_environment(
        _proxy_environment(OUTBOUND_PROXY_URL="http://egress-proxy:3129")
    )

    assert policy.proxy_url == "http://egress-proxy:3129"


def test_proxy_policy_accepts_an_explicit_private_ipv6_endpoint() -> None:
    policy = outbound_proxy_policy_from_environment(
        _proxy_environment(
            OUTBOUND_PROXY_URL="http://[fd00::10]:3128",
            OUTBOUND_PROXY_ALLOWED_HOSTS="fd00::10",
        )
    )

    assert policy.proxy_url == "http://[fd00::10]:3128"
    assert policy.allowed_proxy_hosts == ("fd00::10",)


@pytest.mark.parametrize(
    ("overrides", "reason_code"),
    [
        ({"OUTBOUND_PROXY_URL": ""}, "egress.proxy_endpoint_required"),
        (
            {"OUTBOUND_PROXY_URL": "https://egress-proxy:3128"},
            "egress.proxy_endpoint_invalid",
        ),
        (
            {"OUTBOUND_PROXY_URL": "http://user@egress-proxy:3128"},
            "egress.proxy_endpoint_invalid",
        ),
        (
            {"OUTBOUND_PROXY_URL": "http://egress-proxy:3128/path"},
            "egress.proxy_endpoint_invalid",
        ),
        (
            {"OUTBOUND_PROXY_URL": "http://egress-proxy:8080"},
            "egress.proxy_endpoint_invalid",
        ),
        (
            {"OUTBOUND_PROXY_URL": "http://public.example:3128"},
            "egress.proxy_host_not_allowed",
        ),
        (
            {
                "OUTBOUND_PROXY_URL": "http://127.0.0.1:3128",
                "OUTBOUND_PROXY_ALLOWED_HOSTS": "127.0.0.1",
            },
            "egress.proxy_host_not_allowed",
        ),
        (
            {
                "OUTBOUND_PROXY_URL": "http://localhost:3128",
                "OUTBOUND_PROXY_ALLOWED_HOSTS": "localhost",
            },
            "egress.proxy_host_not_allowed",
        ),
        (
            {"OUTBOUND_PROXY_POLICY_REVISION": "stale-v0"},
            "egress.proxy_policy_revision_invalid",
        ),
    ],
)
def test_proxy_policy_rejects_unsafe_or_incomplete_configuration(
    overrides: dict[str, str],
    reason_code: str,
) -> None:
    with pytest.raises(OutboundProxyConfigurationError) as captured:
        outbound_proxy_policy_from_environment(_proxy_environment(**overrides))

    assert captured.value.reason_code == reason_code
    assert "public.example" not in str(captured.value)


@pytest.mark.parametrize(
    "host",
    [
        "-egress-proxy",
        "egress-proxy-",
        "egress_proxy",
        "egress..svc",
        ".svc.cluster.local",
        "2130706433",
        "0x7f000001",
    ],
)
def test_proxy_policy_rejects_malformed_internal_dns_names(host: str) -> None:
    with pytest.raises(OutboundProxyConfigurationError) as captured:
        outbound_proxy_policy_from_environment(
            _proxy_environment(
                OUTBOUND_PROXY_URL=f"http://{host}:3128",
                OUTBOUND_PROXY_ALLOWED_HOSTS=host,
            )
        )

    assert captured.value.reason_code == "egress.proxy_host_not_allowed"
    assert host not in str(captured.value)


def test_production_cannot_start_with_direct_external_transport() -> None:
    with pytest.raises(OutboundProxyConfigurationError) as captured:
        outbound_proxy_policy_from_environment(
            {
                "NODE_ENV": "production",
                "OUTBOUND_TRANSPORT_MODE": ("direct_pinned_internal_or_dedicated"),
            }
        )

    assert captured.value.reason_code == "egress.proxy_required_in_production"


def test_non_production_defaults_to_direct_pinned_transport() -> None:
    policy = outbound_proxy_policy_from_environment({"NODE_ENV": "test"})

    assert policy.mode is OutboundTransportMode.DIRECT_PINNED_INTERNAL_OR_DEDICATED
    assert policy.proxy_url is None
    assert policy.allowed_proxy_hosts == ()


def test_ambient_proxy_variables_do_not_change_server_owned_policy() -> None:
    policy = outbound_proxy_policy_from_environment(
        _proxy_environment(
            HTTP_PROXY="http://ambient.invalid:9999",
            HTTPS_PROXY="http://ambient.invalid:9999",
            ALL_PROXY="http://ambient.invalid:9999",
            NO_PROXY="*",
        )
    )

    assert policy.proxy_url == "http://egress-proxy:3128"
    assert policy.allowed_proxy_hosts == ("egress-proxy",)


def test_proxy_policy_is_immutable_and_copies_allowed_hosts() -> None:
    environment = _proxy_environment(
        OUTBOUND_PROXY_ALLOWED_HOSTS="egress-proxy,egress-proxy.moduly.svc"
    )
    policy = outbound_proxy_policy_from_environment(environment)
    environment["OUTBOUND_PROXY_ALLOWED_HOSTS"] = "changed"

    assert policy.allowed_proxy_hosts == (
        "egress-proxy",
        "egress-proxy.moduly.svc",
    )
    with pytest.raises(FrozenInstanceError):
        policy.proxy_url = "http://changed:3128"  # type: ignore[misc]


def test_unknown_transport_mode_fails_closed_without_configuration_values() -> None:
    with pytest.raises(OutboundProxyConfigurationError) as captured:
        outbound_proxy_policy_from_environment(
            {
                "NODE_ENV": "test",
                "OUTBOUND_TRANSPORT_MODE": "unexpected-mode",
            }
        )

    assert captured.value.reason_code == "egress.transport_mode_invalid"
    assert "unexpected-mode" not in str(captured.value)
