from __future__ import annotations

import pytest

from apps.gateway.application.deployment.browser_access_errors import (
    BrowserAccessPolicyError,
)
from apps.gateway.application.deployment.browser_access_policy import (
    CONTRACT_VERSION,
    normalize_browser_access_policy,
    render_frame_ancestors,
    resolve_persisted_browser_access_policy,
)


def _policy(*origins: str, enabled: bool = True) -> dict:
    return {
        "contract_version": CONTRACT_VERSION,
        "embedding": {
            "enabled": enabled,
            "parent_origins": list(origins),
        },
    }


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("https://example.com", "https://example.com"),
        ("HTTPS://Example.COM/", "https://example.com"),
        ("https://example.com:443", "https://example.com"),
        ("https://example.com:8443", "https://example.com:8443"),
        ("https://faß.de", "https://xn--fa-hia.de"),
        ("https://192.0.2.1", "https://192.0.2.1"),
        (
            "https://[2001:0db8::1]:8443",
            "https://[2001:db8::1]:8443",
        ),
        (" \thttps://example.com/\r\n", "https://example.com"),
    ],
)
def test_production_origin_canonicalization(raw: str, expected: str) -> None:
    result = normalize_browser_access_policy(
        "chatbot",
        _policy(raw),
        environment="production",
    )

    assert result is not None
    assert result.embedding.parent_origins == (expected,)


@pytest.mark.parametrize("environment", ["development", "dev", "test", "testing"])
@pytest.mark.parametrize(
    "origin",
    [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://[::1]:3000",
    ],
)
def test_development_http_allows_only_exact_loopback_hosts(
    environment: str,
    origin: str,
) -> None:
    result = normalize_browser_access_policy(
        "widget",
        _policy(origin),
        environment=environment,
    )

    assert result is not None
    assert result.embedding.parent_origins == (origin,)


@pytest.mark.parametrize("environment", [None, "", "production", "preview", "qa"])
def test_missing_or_unknown_environment_is_https_only(environment: str | None) -> None:
    with pytest.raises(BrowserAccessPolicyError) as exc_info:
        normalize_browser_access_policy(
            "chatbot",
            _policy("http://localhost:3000"),
            environment=environment,
        )

    assert exc_info.value.code == "deployment.browser_access.invalid_origin"


@pytest.mark.parametrize(
    "origin",
    [
        "",
        "   ",
        "*",
        "null",
        "//example.com",
        "file:///tmp/page.html",
        "data:text/html,hello",
        "blob:https://example.com/id",
        "javascript:alert(1)",
        "ftp://example.com",
        "https://*.example.com",
        "https://example.*",
        "https://example.com/path",
        "https://example.com?query=1",
        "https://example.com#fragment",
        "https://user@example.com",
        "https://user:pass@example.com",
        "https://example.com\t.evil.test",
        "https://exa mple.com",
        "https://example.com%2eevil.test",
        "https://example.com.",
        "https://[fe80::1%25eth0]",
        "https://127.1",
        "https://2130706433",
        "https://0177.0.0.1",
        "https://0x7f000001",
        "https://example.com:0",
        "https://example.com:65536",
        "https://example.com:not-a-port",
        "https://example.com:",
        "https://[::ffff:127.0.0.1]",
        "https://example.com\x00.evil.test",
        "https://example.com\x85.evil.test",
        "https://[2001:db8::1",
    ],
)
def test_invalid_origin_is_rejected_without_raw_value(origin: str) -> None:
    with pytest.raises(BrowserAccessPolicyError) as exc_info:
        normalize_browser_access_policy(
            "chatbot",
            _policy(origin),
            environment="production",
        )

    assert exc_info.value.code == "deployment.browser_access.invalid_origin"
    if origin:
        assert origin not in str(exc_info.value)


@pytest.mark.parametrize(
    "origin",
    [
        "http://example.com",
        "http://127.0.0.2:3000",
        "http://192.168.0.1:3000",
        "http://localhost.attacker.example:3000",
        "http://127.0.0.1.attacker.example:3000",
    ],
)
def test_development_http_does_not_expand_loopback_exception(origin: str) -> None:
    with pytest.raises(BrowserAccessPolicyError):
        normalize_browser_access_policy(
            "chatbot",
            _policy(origin),
            environment="development",
        )


def test_raw_origin_limit_is_checked_before_ascii_whitespace_trim() -> None:
    base = "https://example.com"
    accepted = f"{base}{' ' * (512 - len(base))}"
    rejected = f"{base}{' ' * (513 - len(base))}"

    result = normalize_browser_access_policy(
        "chatbot",
        _policy(accepted),
        environment="production",
    )
    assert result is not None
    assert result.embedding.parent_origins == (base,)

    with pytest.raises(BrowserAccessPolicyError) as exc_info:
        normalize_browser_access_policy(
            "chatbot",
            _policy(rejected),
            environment="production",
        )
    assert exc_info.value.code == "deployment.browser_access.invalid_origin"


def test_policy_state_and_deployment_type_matrix() -> None:
    chatbot_default = normalize_browser_access_policy(
        "chatbot",
        None,
        environment="production",
    )
    widget_default = normalize_browser_access_policy(
        "widget",
        None,
        environment="production",
    )

    assert chatbot_default is not None
    assert chatbot_default.to_dict() == _policy(enabled=False)
    assert widget_default == chatbot_default
    assert (
        normalize_browser_access_policy(
            "api",
            None,
            environment="production",
        )
        is None
    )

    with pytest.raises(BrowserAccessPolicyError) as exc_info:
        normalize_browser_access_policy(
            "internal_chatbot",
            _policy(enabled=False),
            environment="production",
        )
    assert exc_info.value.code == "deployment.browser_access.not_supported"


@pytest.mark.parametrize(
    ("policy", "code"),
    [
        (_policy(enabled=True), "deployment.browser_access.origins_required"),
        (
            _policy("https://example.com", enabled=False),
            "deployment.browser_access.origins_not_allowed",
        ),
        (
            {
                "contract_version": "deployment_browser_access.v2",
                "embedding": {"enabled": False, "parent_origins": []},
            },
            "deployment.browser_access.unsupported_contract_version",
        ),
    ],
)
def test_invalid_policy_state_has_fixed_code(policy: dict, code: str) -> None:
    with pytest.raises(BrowserAccessPolicyError) as exc_info:
        normalize_browser_access_policy(
            "chatbot",
            policy,
            environment="production",
        )

    assert exc_info.value.code == code


def test_canonical_duplicates_are_rejected_instead_of_silently_removed() -> None:
    with pytest.raises(BrowserAccessPolicyError) as exc_info:
        normalize_browser_access_policy(
            "chatbot",
            _policy(
                "HTTPS://Example.COM/",
                "https://example.com:443",
            ),
            environment="production",
        )

    assert exc_info.value.code == "deployment.browser_access.duplicate_origin"


def test_parent_origins_are_sorted_and_bounded() -> None:
    origins = [f"https://site-{index:02d}.example.com" for index in range(20)]
    result = normalize_browser_access_policy(
        "widget",
        _policy(*reversed(origins)),
        environment="production",
    )

    assert result is not None
    assert result.embedding.parent_origins == tuple(origins)

    with pytest.raises(BrowserAccessPolicyError) as exc_info:
        normalize_browser_access_policy(
            "widget",
            _policy(*origins, "https://site-20.example.com"),
            environment="production",
        )
    assert exc_info.value.code == "deployment.browser_access.origin_limit_exceeded"


def _hostname_of_length(length: int, index: int) -> str:
    prefix = f"o{index:02d}"
    remaining = length - len(prefix) - 1
    for label_count in range(1, 10):
        payload = remaining - (label_count - 1)
        if label_count <= payload <= label_count * 63:
            sizes = []
            for part in range(label_count):
                size = min(63, payload - (label_count - part - 1))
                sizes.append(size)
                payload -= size
            return prefix + "." + ".".join("a" * size for size in sizes)
    raise AssertionError("unable to construct hostname")


def _origins_for_header_size(size: int) -> list[str]:
    prefix_and_spaces = len("frame-ancestors ") + 19
    total_origins = size - prefix_and_spaces
    base_origin_length, extra = divmod(total_origins, 20)
    return [
        "https://" + _hostname_of_length(base_origin_length - 8 + (index < extra), index)
        for index in range(20)
    ]


def test_rendered_csp_value_accepts_4096_bytes_and_rejects_4097() -> None:
    accepted = normalize_browser_access_policy(
        "chatbot",
        _policy(*_origins_for_header_size(4096)),
        environment="production",
    )
    assert accepted is not None
    assert len(render_frame_ancestors(accepted).encode("utf-8")) == 4096

    with pytest.raises(BrowserAccessPolicyError) as exc_info:
        normalize_browser_access_policy(
            "chatbot",
            _policy(*_origins_for_header_size(4097)),
            environment="production",
        )
    assert exc_info.value.code == "deployment.browser_access.header_limit_exceeded"


def test_persisted_policy_resolution_is_fail_closed() -> None:
    malformed_values = [
        None,
        {},
        [],
        "unexpected",
        _policy("https://example.com", enabled=False),
        {
            "contract_version": "deployment_browser_access.v2",
            "embedding": {"enabled": True, "parent_origins": ["https://example.com"]},
        },
    ]

    for value in malformed_values:
        resolved = resolve_persisted_browser_access_policy(
            "chatbot",
            value,
            environment="production",
        )
        assert resolved.to_dict() == _policy(enabled=False)
        assert render_frame_ancestors(resolved) == "frame-ancestors 'none'"


def test_enabled_policy_renders_only_canonical_sources() -> None:
    result = normalize_browser_access_policy(
        "chatbot",
        _policy("https://b.example.com", "https://a.example.com"),
        environment="production",
    )

    assert result is not None
    assert render_frame_ancestors(result) == (
        "frame-ancestors https://a.example.com https://b.example.com"
    )
