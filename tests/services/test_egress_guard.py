import socket
import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest
from cryptography.fernet import Fernet

from apps.gateway.api.v1.endpoints import rag as rag_endpoint
from apps.gateway.api.v1.endpoints import connectors as connectors_endpoint
from apps.gateway.api.v1.endpoints.rag import _prepare_api_source
from apps.gateway.services.ingestion.service import IngestionOrchestrator
from apps.gateway.services.ingestion.processors import api_processor
from apps.gateway.services.ingestion.processors.api_processor import ApiProcessor
from apps.gateway.services.knowledge_document_content_service import (
    content_disposition_type_for_document,
)
from apps.shared.services.ingestion.processors.db_processor import DbProcessor
from apps.shared.db.models.knowledge import SourceType
from apps.shared.connectors.postgres import PostgresConnector
from apps.shared.utils.encryption import encryption_manager
from apps.shared.services.egress_guard import (
    API_RESPONSE_CONTENT_TYPES,
    EgressGuardError,
    EgressGuardPolicy,
    OutboundEgressGuard,
    download_url_to_temp_file,
    ensure_db_probe_allowed,
    ensure_network_target_allowed,
    ensure_object_listing_allowed,
    ensure_ssh_command_allowed,
    safe_db_fetch_batch_size,
    safe_http_request,
)
from apps.shared.services.outbound_operation_policy import KNOWLEDGE_API_FETCH


def _fake_getaddrinfo(ip_address):
    return [
        (
            socket.AF_INET,
            socket.SOCK_STREAM,
            6,
            "",
            (ip_address, 443),
        )
    ]


def test_egress_guard_rejects_private_network_target(monkeypatch):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *args, **kwargs: _fake_getaddrinfo("127.0.0.1"),
    )

    with pytest.raises(EgressGuardError) as exc_info:
        OutboundEgressGuard().validate_url("https://example.com/path")

    assert exc_info.value.reason_code == "egress.private_target"


def test_egress_guard_rejects_unsupported_scheme(monkeypatch):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *args, **kwargs: _fake_getaddrinfo("8.8.8.8"),
    )

    with pytest.raises(EgressGuardError) as exc_info:
        OutboundEgressGuard().validate_url("file:///etc/passwd")

    assert exc_info.value.reason_code == "egress.unsupported_scheme"


def test_egress_guard_rejects_invalid_port_without_raw_exception(monkeypatch):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *args, **kwargs: _fake_getaddrinfo("8.8.8.8"),
    )

    with pytest.raises(EgressGuardError) as exc_info:
        OutboundEgressGuard().validate_url("https://example.com:99999/path")

    assert exc_info.value.reason_code == "egress.invalid_port"


def test_egress_guard_preserves_brackets_in_canonical_public_ipv6_url(monkeypatch):
    address = "2606:4700:4700::1111"
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [
            (
                socket.AF_INET6,
                socket.SOCK_STREAM,
                socket.IPPROTO_TCP,
                "",
                (address, 443, 0, 0),
            )
        ],
    )

    canonical = OutboundEgressGuard().validate_url(
        f"https://[{address}]:443/path?value=1"
    )

    assert canonical == f"https://[{address}]:443/path?value=1"


def test_egress_guard_rejects_disallowed_port(monkeypatch):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *args, **kwargs: _fake_getaddrinfo("8.8.8.8"),
    )

    with pytest.raises(EgressGuardError) as exc_info:
        OutboundEgressGuard().validate_url("https://example.com:8443/path")

    assert exc_info.value.reason_code == "egress.disallowed_port"


def test_egress_guard_rejects_https_downgrade(monkeypatch):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *args, **kwargs: _fake_getaddrinfo("8.8.8.8"),
    )

    with pytest.raises(EgressGuardError) as exc_info:
        OutboundEgressGuard().validate_redirect(
            "https://example.com/start",
            "http://example.com/next",
        )

    assert exc_info.value.reason_code == "egress.https_downgrade"


def test_safe_http_request_rejects_unsupported_method_at_guard_policy(monkeypatch):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *args, **kwargs: _fake_getaddrinfo("8.8.8.8"),
    )

    with pytest.raises(EgressGuardError) as exc_info:
        safe_http_request("PUT", "https://example.com/resource")

    assert exc_info.value.reason_code == "egress.unsupported_method"


def test_safe_http_request_no_longer_uses_post_response_requests_peer_check(
    monkeypatch,
):
    class ForbiddenSession:
        def __init__(self, *_args, **_kwargs):
            raise AssertionError("requests must not perform the network connection")

    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *args, **kwargs: _fake_getaddrinfo("8.8.8.8"),
    )
    monkeypatch.setattr(
        "apps.shared.services.egress_guard.requests.Session",
        ForbiddenSession,
    )
    monkeypatch.setattr(
        "apps.shared.services.guarded_http_transport.GuardedHttpTransport",
        lambda *_args, **_kwargs: httpx.MockTransport(
            lambda request: httpx.Response(200, content=b"ok", request=request)
        ),
    )

    response = safe_http_request("GET", "https://example.com/resource")

    assert response.content == b"ok"


def test_response_peer_ip_validation_rejects_missing_or_private_peer():
    guard = OutboundEgressGuard()

    with pytest.raises(EgressGuardError) as missing:
        guard.validate_response_peer_ip(None)
    with pytest.raises(EgressGuardError) as private:
        guard.validate_response_peer_ip("127.0.0.1")

    assert missing.value.reason_code == "egress.peer_unverified"
    assert private.value.reason_code == "egress.private_target"


def test_response_peer_ip_validation_allows_public_peer():
    OutboundEgressGuard().validate_response_peer_ip("8.8.8.8")


def test_exact_trusted_local_target_allows_private_address_and_pins_it(monkeypatch):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [
            (
                socket.AF_INET,
                socket.SOCK_STREAM,
                6,
                "",
                ("172.20.0.20", 5432),
            )
        ],
    )

    result = ensure_network_target_allowed(
        "CONNECTOR-TEST-POSTGRES.",
        5432,
        allowed_ports=frozenset({5432}),
        trusted_local_targets=frozenset({("connector-test-postgres", 5432)}),
    )

    assert result == ("connector-test-postgres", 5432, "172.20.0.20")


def test_unlisted_private_target_remains_denied_when_local_targets_exist(monkeypatch):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [
            (
                socket.AF_INET,
                socket.SOCK_STREAM,
                6,
                "",
                ("172.20.0.30", 5432),
            )
        ],
    )

    with pytest.raises(EgressGuardError) as exc_info:
        ensure_network_target_allowed(
            "redis",
            5432,
            allowed_ports=frozenset({5432}),
            trusted_local_targets=frozenset({("connector-test-postgres", 5432)}),
        )

    assert exc_info.value.reason_code == "egress.private_target"


def test_exact_trusted_local_target_rejects_mixed_private_public_dns(monkeypatch):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [
            (
                socket.AF_INET,
                socket.SOCK_STREAM,
                6,
                "",
                ("172.20.0.20", 5432),
            ),
            (
                socket.AF_INET,
                socket.SOCK_STREAM,
                6,
                "",
                ("8.8.8.8", 5432),
            ),
        ],
    )

    with pytest.raises(EgressGuardError) as exc_info:
        ensure_network_target_allowed(
            "connector-test-postgres",
            5432,
            allowed_ports=frozenset({5432}),
            trusted_local_targets=frozenset({("connector-test-postgres", 5432)}),
        )

    assert exc_info.value.reason_code == "egress.private_target"


def test_exact_localhost_target_allows_only_loopback_results(monkeypatch):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [
            (
                socket.AF_INET6,
                socket.SOCK_STREAM,
                6,
                "",
                ("::1", 55432, 0, 0),
            ),
            (
                socket.AF_INET,
                socket.SOCK_STREAM,
                6,
                "",
                ("127.0.0.1", 55432),
            ),
        ],
    )

    result = ensure_network_target_allowed(
        "localhost",
        55432,
        allowed_ports=frozenset({55432}),
        trusted_local_targets=frozenset({("localhost", 55432)}),
    )

    assert result == ("localhost", 55432, "127.0.0.1")


@pytest.mark.parametrize(
    "address",
    [
        "169.254.169.254",
        "100.64.0.1",
        "224.0.0.1",
        "0.0.0.0",
        "2001:db8::1",
    ],
)
def test_exact_trusted_local_target_rejects_non_local_address_classes(
    monkeypatch,
    address,
):
    family = socket.AF_INET6 if ":" in address else socket.AF_INET
    socket_address = (address, 5432, 0, 0) if family == socket.AF_INET6 else (
        address,
        5432,
    )
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [
            (family, socket.SOCK_STREAM, 6, "", socket_address)
        ],
    )

    with pytest.raises(EgressGuardError) as exc_info:
        ensure_network_target_allowed(
            "connector-test-postgres",
            5432,
            allowed_ports=frozenset({5432}),
            trusted_local_targets=frozenset({("connector-test-postgres", 5432)}),
        )

    assert exc_info.value.reason_code == "egress.private_target"


def test_egress_guard_rejects_request_body_over_policy_cap(monkeypatch):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *args, **kwargs: _fake_getaddrinfo("8.8.8.8"),
    )

    with pytest.raises(EgressGuardError) as exc_info:
        safe_http_request(
            "POST",
            "https://example.com/resource",
            json_body={"payload": "x" * 32},
            policy=EgressGuardPolicy(max_request_bytes=16),
        )

    assert exc_info.value.reason_code == "egress.request_too_large"


def test_egress_guard_rejects_crlf_header_injection():
    with pytest.raises(EgressGuardError) as exc_info:
        OutboundEgressGuard().sanitize_request_headers({"X-Test": "ok\r\nInjected: 1"})

    assert exc_info.value.reason_code == "egress.invalid_header"


def test_egress_guard_removes_hop_by_hop_headers():
    sanitized = OutboundEgressGuard().sanitize_request_headers(
        {
            "Connection": "keep-alive",
            "Transfer-Encoding": "chunked",
            "Content-Length": "10",
            "Accept": "application/json",
        }
    )

    assert sanitized == {"Accept": "application/json"}


def test_egress_guard_header_items_preserve_duplicates_and_can_reject_hop_by_hop():
    guard = OutboundEgressGuard()

    validated = guard.validate_request_header_items(
        (("X-Trace", "first"), ("X-Trace", "second"))
    )

    assert validated == (("X-Trace", "first"), ("X-Trace", "second"))
    with pytest.raises(EgressGuardError) as exc_info:
        guard.validate_request_header_items(
            (("Connection", "keep-alive"),),
            reject_hop_by_hop=True,
        )
    assert exc_info.value.reason_code == "egress.invalid_header"


def test_egress_guard_normalizes_malformed_url_to_safe_error():
    with pytest.raises(EgressGuardError) as exc_info:
        OutboundEgressGuard().validate_url("https://[invalid")

    assert exc_info.value.reason_code == "egress.invalid_url"


def test_egress_guard_rejects_header_count_and_size_over_policy():
    guard = OutboundEgressGuard(EgressGuardPolicy(max_header_count=1))
    with pytest.raises(EgressGuardError) as count_exc:
        guard.sanitize_request_headers({"A": "1", "B": "2"})
    assert count_exc.value.reason_code == "egress.headers_too_large"

    guard = OutboundEgressGuard(EgressGuardPolicy(max_header_value_bytes=4))
    with pytest.raises(EgressGuardError) as size_exc:
        guard.sanitize_request_headers({"A": "12345"})
    assert size_exc.value.reason_code == "egress.headers_too_large"


def test_egress_guard_rejects_compressed_response():
    with pytest.raises(EgressGuardError) as exc_info:
        OutboundEgressGuard().validate_response_headers({"content-encoding": "gzip"})

    assert exc_info.value.reason_code == "egress.compressed_response_not_allowed"


def test_egress_guard_rejects_content_length_over_policy_cap():
    guard = OutboundEgressGuard(EgressGuardPolicy(max_response_bytes=10))

    with pytest.raises(EgressGuardError) as exc_info:
        guard.validate_response_headers({"content-length": "11"})

    assert exc_info.value.reason_code == "egress.response_too_large"


def test_egress_guard_rejects_unsupported_content_type():
    guard = OutboundEgressGuard(
        EgressGuardPolicy(allowed_content_types=API_RESPONSE_CONTENT_TYPES)
    )

    with pytest.raises(EgressGuardError) as exc_info:
        guard.validate_response_headers({"content-type": "text/html; charset=utf-8"})

    assert exc_info.value.reason_code == "egress.unsupported_content_type"


def test_egress_guard_strips_sensitive_headers_on_cross_origin_redirect():
    headers = {
        "Authorization": "Bearer secret",
        "X-API-Key": "secret",
        "Accept": "application/json",
    }

    sanitized = OutboundEgressGuard().sanitize_request_headers(
        headers,
        strip_sensitive=True,
    )

    assert "Authorization" not in sanitized
    assert "X-API-Key" not in sanitized
    assert sanitized["Accept"] == "application/json"


def test_operation_bound_request_allows_same_origin_redirect_with_query(
    monkeypatch,
):
    requests_seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests_seen.append(str(request.url))
        if request.url.path == "/start":
            return httpx.Response(
                302,
                headers={"location": "/result?cursor=next"},
                request=request,
            )
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            content=b"{}",
            request=request,
        )

    monkeypatch.setattr(
        "apps.shared.services.guarded_http_transport.GuardedHttpTransport",
        lambda *_args, **_kwargs: httpx.MockTransport(handler),
    )

    response = safe_http_request(
        "GET",
        "https://example.com/start?cursor=first",
        operation_id=KNOWLEDGE_API_FETCH,
    )

    assert response.status_code == 200
    assert requests_seen == [
        "https://example.com/start?cursor=first",
        "https://example.com/result?cursor=next",
    ]


def test_operation_bound_request_rejects_cross_origin_redirect(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            302,
            headers={"location": "https://redirect.example/result"},
            request=request,
        )

    monkeypatch.setattr(
        "apps.shared.services.guarded_http_transport.GuardedHttpTransport",
        lambda *_args, **_kwargs: httpx.MockTransport(handler),
    )

    with pytest.raises(EgressGuardError) as captured:
        safe_http_request(
            "GET",
            "https://example.com/start",
            operation_id=KNOWLEDGE_API_FETCH,
        )

    assert captured.value.reason_code == "egress.origin_not_allowed"


def test_safe_http_request_disables_environment_proxy(monkeypatch):
    captured = {}
    real_client = httpx.Client

    def client_factory(**kwargs):
        captured.update(kwargs)
        return real_client(**kwargs)

    def handler(request: httpx.Request) -> httpx.Response:
        captured["headers"] = dict(request.headers)
        return httpx.Response(200, content=b"ok", request=request)

    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *args, **kwargs: _fake_getaddrinfo("8.8.8.8"),
    )
    monkeypatch.setattr("apps.shared.services.egress_guard.httpx.Client", client_factory)
    monkeypatch.setattr(
        "apps.shared.services.guarded_http_transport.GuardedHttpTransport",
        lambda *_args, **_kwargs: httpx.MockTransport(handler),
    )

    response = safe_http_request(
        "GET",
        "https://example.com/resource",
        policy=EgressGuardPolicy(validate_peer_ip=False),
    )

    assert response.text == "ok"
    assert captured["trust_env"] is False
    assert captured["headers"]["accept-encoding"] == "identity"


def test_download_url_to_temp_file_streams_without_buffered_helper(
    monkeypatch,
) -> None:
    class ChunkedStream(httpx.SyncByteStream):
        def __iter__(self):
            yield b"chunk-one"
            yield b"-chunk-two"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "application/octet-stream"},
            stream=ChunkedStream(),
            request=request,
        )

    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: _fake_getaddrinfo("8.8.8.8"),
    )
    monkeypatch.setattr(
        "apps.shared.services.guarded_http_transport.GuardedHttpTransport",
        lambda *_args, **_kwargs: httpx.MockTransport(handler),
    )
    monkeypatch.setattr(
        "apps.shared.services.egress_guard.safe_http_request",
        lambda *_args, **_kwargs: pytest.fail(
            "download must not buffer through safe_http_request"
        ),
    )

    path = download_url_to_temp_file(
        "https://example.com/document.pdf",
        suffix=".pdf",
        policy=EgressGuardPolicy(
            max_response_bytes=64,
            validate_peer_ip=False,
        ),
    )
    try:
        assert Path(path).read_bytes() == b"chunk-one-chunk-two"
    finally:
        Path(path).unlink(missing_ok=True)


def test_download_url_to_temp_file_removes_partial_file_at_size_cap(
    monkeypatch,
) -> None:
    class OversizedStream(httpx.SyncByteStream):
        def __iter__(self):
            yield b"1234"
            yield b"5"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            stream=OversizedStream(),
            request=request,
        )

    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: _fake_getaddrinfo("8.8.8.8"),
    )
    monkeypatch.setattr(
        "apps.shared.services.guarded_http_transport.GuardedHttpTransport",
        lambda *_args, **_kwargs: httpx.MockTransport(handler),
    )

    with pytest.raises(EgressGuardError) as captured:
        download_url_to_temp_file(
            "https://example.com/document.pdf",
            policy=EgressGuardPolicy(
                max_response_bytes=4,
                validate_peer_ip=False,
            ),
        )

    partial_path = Path(captured.value.partial_file_path)
    assert captured.value.reason_code == "egress.response_too_large"
    assert partial_path.exists() is False


def test_download_url_to_temp_file_rejects_http_error_before_writing(
    monkeypatch,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            404,
            headers={"content-type": "text/plain"},
            content=b"synthetic upstream error",
            request=request,
        )

    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: _fake_getaddrinfo("8.8.8.8"),
    )
    monkeypatch.setattr(
        "apps.shared.services.guarded_http_transport.GuardedHttpTransport",
        lambda *_args, **_kwargs: httpx.MockTransport(handler),
    )
    monkeypatch.setattr(
        "apps.shared.services.egress_guard.tempfile.NamedTemporaryFile",
        lambda *_args, **_kwargs: pytest.fail(
            "an unsuccessful response must not create a temporary file"
        ),
    )

    with pytest.raises(EgressGuardError) as captured:
        download_url_to_temp_file(
            "https://example.com/missing.txt",
            policy=EgressGuardPolicy(validate_peer_ip=False),
        )

    assert captured.value.reason_code == "egress.http_status_rejected"


def test_protocol_adapter_guards_reject_dangerous_operations():
    with pytest.raises(EgressGuardError) as sql_exc:
        ensure_db_probe_allowed("DROP TABLE users")
    with pytest.raises(EgressGuardError) as ssh_exc:
        ensure_ssh_command_allowed("cat /etc/passwd")
    with pytest.raises(EgressGuardError) as object_exc:
        ensure_object_listing_allowed(
            bucket="documents",
            prefix="",
            recursive=True,
            max_keys=10000,
        )

    assert sql_exc.value.reason_code == "adapter.sql_not_allowed"
    assert ssh_exc.value.reason_code == "adapter.ssh_command_not_allowed"
    assert object_exc.value.reason_code == "adapter.object_listing_too_broad"


@pytest.mark.parametrize(
    "query",
    [
        "SELECT pg_sleep(10)",
        "SELECT pg_notify('channel', 'message')",
        "SELECT pg_read_file('/etc/passwd')",
        "SELECT pg_stat_file('/etc/passwd')",
        "SELECT pg_ls_dir('.')",
        "SELECT lo_export(1, '/tmp/out')",
        "SELECT lo_import('/tmp/in')",
        "SELECT * FROM dblink('dbname=x', 'select 1') AS t(x int)",
        "SELECT set_config('work_mem', '64MB', false)",
        "SELECT generate_series(1, 1000000000)",
        "SELECT * FROM users; SELECT * FROM secrets",
        "SELECT * FROM users -- hidden",
        "COPY users TO STDOUT",
    ],
)
def test_db_probe_guard_rejects_dangerous_read_like_sql(query):
    with pytest.raises(EgressGuardError) as exc_info:
        ensure_db_probe_allowed(query)

    assert exc_info.value.reason_code == "adapter.sql_not_allowed"


def test_safe_db_fetch_batch_size_caps_untrusted_values():
    assert safe_db_fetch_batch_size(50_000) == 1000
    assert safe_db_fetch_batch_size(0) == 1
    assert safe_db_fetch_batch_size("not-int") == 1000


def test_postgres_connector_rejects_arbitrary_sql_before_connecting(monkeypatch):
    def fail_if_called(config):
        raise AssertionError("DB connection should not be opened for rejected SQL")

    connector = PostgresConnector()
    monkeypatch.setattr(connector, "_create_tunnel_and_engine", fail_if_called)

    with pytest.raises(EgressGuardError) as exc_info:
        list(connector.fetch_data({}, "DELETE FROM users"))

    assert exc_info.value.reason_code == "adapter.sql_not_allowed"


def test_postgres_connector_rejects_private_db_target_before_connecting(monkeypatch):
    connector = PostgresConnector()
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *args, **kwargs: _fake_getaddrinfo("10.0.0.5"),
    )

    with pytest.raises(EgressGuardError) as exc_info:
        connector.check(
            {
                "host": "db.example.com",
                "port": 5432,
                "username": "user",
                "password": "password",
                "database": "app",
            }
        )

    assert exc_info.value.reason_code == "egress.private_target"


def test_postgres_connector_rejects_ssh_tunnel_before_opening(monkeypatch):
    connector = PostgresConnector()
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *args, **kwargs: _fake_getaddrinfo("8.8.8.8"),
    )

    with pytest.raises(EgressGuardError) as exc_info:
        connector.check(
            {
                "host": "db.example.com",
                "port": 5432,
                "username": "user",
                "password": "password",
                "database": "app",
                "ssh": {"enabled": True},
            }
        )

    assert exc_info.value.reason_code == "adapter.ssh_tunnel_not_allowed"


def test_postgres_connector_allows_ssh_tunnel_only_when_explicitly_enabled(monkeypatch):
    captured = {}

    class FakeTunnel:
        local_bind_port = 6543

        def __init__(self, **kwargs):
            captured["ssh_params"] = kwargs
            self.started = False

        def start(self):
            self.started = True

        def stop(self):
            pass

    class FakeEngine:
        def dispose(self):
            pass

    def fake_create_engine(url):
        captured["url"] = url
        return FakeEngine()

    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *args, **kwargs: _fake_getaddrinfo("8.8.8.8"),
    )
    monkeypatch.setattr("apps.shared.connectors.postgres.SSHTunnelForwarder", FakeTunnel)
    monkeypatch.setattr("apps.shared.connectors.postgres.create_engine", fake_create_engine)

    connector = PostgresConnector(allow_ssh_tunnel=True, allowed_db_ports=None)
    engine, tunnel = connector._create_tunnel_and_engine(
        {
            "host": "internal-db.local",
            "port": 15432,
            "username": "user",
            "password": "password",
            "database": "app",
            "ssh": {
                "enabled": True,
                "host": "bastion.example.com",
                "port": 2222,
                "username": "ssh-user",
                "auth_type": "password",
                "password": "ssh-password",
            },
        }
    )

    assert engine is not None
    assert tunnel.started is True
    assert captured["ssh_params"]["ssh_address_or_host"] == ("bastion.example.com", 2222)
    assert captured["ssh_params"]["remote_bind_address"] == ("internal-db.local", 15432)
    assert captured["url"].host == "127.0.0.1"
    assert captured["url"].port == 6543
    assert "hostaddr" not in captured["url"].query


def test_workflow_connector_factory_preserves_ssh_tunnel_compatibility():
    connector = connectors_endpoint._build_workflow_connector(PostgresConnector)

    assert connector.allow_ssh_tunnel is True
    assert connector.allowed_db_ports is None


def test_knowledge_db_processor_uses_ssh_tunnel_denied_default():
    connector = DbProcessor()._get_connector("postgres")

    assert isinstance(connector, PostgresConnector)
    assert connector.allow_ssh_tunnel is False
    assert connector.allowed_db_ports == frozenset({5432})


def test_postgres_connector_caps_fetchmany_batch_size(monkeypatch):
    batch_sizes = []
    executed = []

    class FakeResult:
        def fetchmany(self, batch_size):
            batch_sizes.append(batch_size)
            return []

    class FakeConnection:
        def execution_options(self, **kwargs):
            return self

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return None

        def execute(self, query):
            executed.append(str(query))
            return FakeResult()

    class FakeEngine:
        def connect(self):
            return FakeConnection()

        def dispose(self):
            pass

    connector = PostgresConnector()
    monkeypatch.setattr(
        connector,
        "_create_tunnel_and_engine",
        lambda config: (FakeEngine(), None),
    )

    list(connector.fetch_data({}, "SELECT * FROM users", batch_size=50_000))

    assert batch_sizes == [1000]
    assert executed[0] == "SET TRANSACTION READ ONLY"
    assert executed[1].startswith("SET LOCAL statement_timeout = ")
    assert executed[2] == "SELECT * FROM users"


def test_postgres_connector_fails_closed_when_row_cap_exceeded(monkeypatch):
    class FakeRow:
        _mapping = {"id": 1}

    class FakeResult:
        def fetchmany(self, batch_size):
            return [FakeRow(), FakeRow(), FakeRow()]

    class FakeConnection:
        def execution_options(self, **kwargs):
            return self

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return None

        def execute(self, query):
            if str(query).startswith("SET "):
                return FakeResult()
            return FakeResult()

    class FakeEngine:
        def connect(self):
            return FakeConnection()

        def dispose(self):
            pass

    connector = PostgresConnector()
    monkeypatch.setattr("apps.shared.connectors.postgres.MAX_DB_FETCH_ROWS", 2)
    monkeypatch.setattr(
        connector,
        "_create_tunnel_and_engine",
        lambda config: (FakeEngine(), None),
    )

    with pytest.raises(EgressGuardError) as exc_info:
        list(connector.fetch_data({}, "SELECT * FROM users", batch_size=10))

    assert exc_info.value.reason_code == "adapter.row_limit_exceeded"


def test_postgres_connector_fails_closed_when_byte_cap_exceeded(monkeypatch):
    class FakeRow:
        _mapping = {"content": "larger-than-test-cap"}

    class FakeResult:
        def __init__(self):
            self.returned = False

        def fetchmany(self, batch_size):
            if self.returned:
                return []
            self.returned = True
            return [FakeRow()]

    result = FakeResult()

    class FakeConnection:
        def execution_options(self, **kwargs):
            return self

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return None

        def execute(self, query):
            if str(query).startswith("SET "):
                return SimpleNamespace()
            return result

    class FakeEngine:
        def connect(self):
            return FakeConnection()

        def dispose(self):
            pass

    connector = PostgresConnector()
    monkeypatch.setattr("apps.shared.connectors.postgres.MAX_DB_FETCH_BYTES", 8)
    monkeypatch.setattr(
        connector,
        "_create_tunnel_and_engine",
        lambda config: (FakeEngine(), None),
    )

    with pytest.raises(EgressGuardError) as exc_info:
        list(connector.fetch_data({}, "SELECT * FROM users", batch_size=10))

    assert exc_info.value.reason_code == "adapter.byte_limit_exceeded"


def test_postgres_connector_cleans_up_before_returning_buffered_rows(monkeypatch):
    events = []

    class FakeRow:
        _mapping = {"id": 1}

    class FakeResult:
        def __init__(self):
            self.returned = False

        def fetchmany(self, batch_size):
            if self.returned:
                return []
            self.returned = True
            return [FakeRow()]

    result = FakeResult()

    class FakeConnection:
        def execution_options(self, **kwargs):
            return self

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            events.append("connection_closed")

        def execute(self, query):
            if str(query).startswith("SET "):
                return SimpleNamespace()
            return result

    class FakeEngine:
        def connect(self):
            return FakeConnection()

        def dispose(self):
            events.append("engine_disposed")

    class FakeTunnel:
        def stop(self):
            events.append("tunnel_stopped")

    connector = PostgresConnector()
    monkeypatch.setattr(
        connector,
        "_create_tunnel_and_engine",
        lambda config: (FakeEngine(), FakeTunnel()),
    )

    generator = connector.fetch_data({}, "SELECT * FROM users", batch_size=10)
    first_row = next(generator)

    assert first_row == {"id": 1}
    assert events == ["connection_closed", "engine_disposed", "tunnel_stopped"]


def test_postgres_connector_pins_resolved_hostaddr(monkeypatch):
    captured = {}

    def fake_create_engine(url):
        captured["url"] = url

        class FakeEngine:
            def dispose(self):
                pass

        return FakeEngine()

    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *args, **kwargs: _fake_getaddrinfo("8.8.8.8"),
    )
    monkeypatch.setattr("apps.shared.connectors.postgres.create_engine", fake_create_engine)

    connector = PostgresConnector()
    engine, tunnel = connector._create_tunnel_and_engine(
        {
            "host": "db.example.com",
            "port": 5432,
            "username": "user",
            "password": "password",
            "database": "app",
        }
    )

    assert tunnel is None
    assert engine is not None
    assert captured["url"].host == "db.example.com"
    assert captured["url"].query["hostaddr"] == "8.8.8.8"
    assert captured["url"].query["connect_timeout"] == "5"
    assert captured["url"].query["options"] == (
        "-c statement_timeout=5000 -c default_transaction_read_only=on"
    )


def test_postgres_schema_info_applies_table_column_and_fk_caps(monkeypatch):
    class FakeInspector:
        def get_table_names(self):
            return ["table_a", "table_b"]

        def get_columns(self, table_name):
            return [{"name": f"col_{index}", "type": "text"} for index in range(3)]

        def get_foreign_keys(self, table_name):
            return [
                {
                    "constrained_columns": [f"fk_{index}"],
                    "referred_table": "other",
                    "referred_columns": ["id"],
                }
                for index in range(3)
            ]

    class FakeEngine:
        def dispose(self):
            pass

    monkeypatch.setattr(
        "apps.shared.connectors.postgres.MAX_SCHEMA_TABLES",
        1,
    )
    monkeypatch.setattr(
        "apps.shared.connectors.postgres.MAX_SCHEMA_COLUMNS_PER_TABLE",
        1,
    )
    monkeypatch.setattr(
        "apps.shared.connectors.postgres.MAX_SCHEMA_FOREIGN_KEYS_PER_TABLE",
        1,
    )
    connector = PostgresConnector()
    monkeypatch.setattr(
        connector,
        "_create_tunnel_and_engine",
        lambda config: (FakeEngine(), None),
    )
    monkeypatch.setattr("apps.shared.connectors.postgres.inspect", lambda engine: FakeInspector())

    tables = connector.get_schema_info({})

    assert tables[0]["table_name"] == "table_a"
    assert len(tables[0]["columns"]) == 1
    assert len(tables[0]["foreign_keys"]) == 1
    assert tables[0]["columns_truncated"] is True
    assert tables[0]["foreign_keys_truncated"] is True
    assert tables[1]["table_name"] == "__schema_truncated__"


def test_connector_schema_endpoint_returns_safe_error(monkeypatch):
    connection_id = "11111111-1111-1111-1111-111111111111"

    class FakeSnapshot:
        adapter_type = "postgres"

        @staticmethod
        def to_connector_config():
            return {"host": "db.example.test", "password": "test-value"}

    class FakeSnapshotProvider:
        def __init__(self, _session_factory):
            pass

        def load(self, resolved_connection_id, *, execution_subject_user_id):
            assert str(resolved_connection_id) == connection_id
            assert execution_subject_user_id == "user-id"
            return FakeSnapshot()

    class FailingConnector:
        def get_schema_info(self, config):
            raise RuntimeError("host=db.internal password=secret")

    monkeypatch.setitem(
        connectors_endpoint.CONNECTOR_MAP,
        connectors_endpoint.SupportedDBType.POSTGRES,
        FailingConnector,
    )
    monkeypatch.setattr(
        connectors_endpoint,
        "ConnectionRuntimeSnapshotProvider",
        FakeSnapshotProvider,
    )
    monkeypatch.setattr(
        connectors_endpoint,
        "_authorize_connection_schema_management",
        lambda _db, **_kwargs: connection_id,
    )

    with pytest.raises(Exception) as exc_info:
        asyncio.run(
            connectors_endpoint.get_connection_schema(
                connection_id=connection_id,
                db=SimpleNamespace(),
                current_user=SimpleNamespace(id="user-id"),
            )
        )

    assert getattr(exc_info.value, "status_code", None) == 400
    assert exc_info.value.detail == {"reason_code": "connector.schema_fetch_failed"}


def test_connector_schema_endpoint_hides_denied_connection_before_dial(monkeypatch):
    connector = Mock()
    connection_id = "11111111-1111-1111-1111-111111111111"

    class DeniedSnapshotProvider:
        def __init__(self, _session_factory):
            pass

        def load(self, _connection_id, *, execution_subject_user_id):
            assert execution_subject_user_id == "user-id"
            raise connectors_endpoint.ConnectionUseDenied()

    monkeypatch.setattr(
        connectors_endpoint,
        "ConnectionRuntimeSnapshotProvider",
        DeniedSnapshotProvider,
    )
    monkeypatch.setattr(
        connectors_endpoint,
        "_build_workflow_connector",
        connector,
    )
    monkeypatch.setattr(
        connectors_endpoint,
        "_authorize_connection_schema_management",
        lambda _db, **_kwargs: connection_id,
    )

    with pytest.raises(Exception) as exc_info:
        asyncio.run(
            connectors_endpoint.get_connection_schema(
                connection_id=connection_id,
                db=SimpleNamespace(),
                current_user=SimpleNamespace(id="user-id"),
            )
        )

    assert getattr(exc_info.value, "status_code", None) == 404
    assert exc_info.value.detail == {"reason_code": "resource.hidden"}
    connector.assert_not_called()


def test_create_connection_returns_safe_connection_error(monkeypatch):
    class FailingConnector:
        def check(self, config):
            raise RuntimeError("host=db.internal password=secret")

    monkeypatch.setitem(
        connectors_endpoint.CONNECTOR_MAP,
        connectors_endpoint.SupportedDBType.POSTGRES,
        FailingConnector,
    )
    request = connectors_endpoint.DBConnectionTestRequest(
        connection_name="db",
        type="postgres",
        host="db.example.com",
        port=5432,
        database="app",
        username="user",
        password="secret",
    )

    with pytest.raises(Exception) as exc_info:
        asyncio.run(
            connectors_endpoint.create_connection(
                request=request,
                db=object(),
                current_user=SimpleNamespace(id="user-id"),
            )
        )

    assert getattr(exc_info.value, "status_code", None) == 400
    assert exc_info.value.detail == {"reason_code": "connector.connection_failed"}


def test_prepare_api_source_stores_protected_config_without_raw_url_or_body(monkeypatch):
    monkeypatch.setenv("ENCRYPTION_KEY", Fernet.generate_key().decode())
    encryption_manager._cipher_suite = None

    _, filename, meta_info = _prepare_api_source(
        "https://api.example.com/data?token=secret",
        "POST",
        '{"Authorization":"Bearer secret"}',
        '{"password":"secret"}',
    )

    serialized = str(meta_info)
    assert filename == "API source"
    assert "api.example.com" not in serialized
    assert "token=secret" not in serialized
    assert "password" not in serialized
    assert "Bearer secret" not in serialized
    assert "url_encrypted" in meta_info["api_config"]
    assert "body_encrypted" in meta_info["api_config"]


def test_presigned_url_rejects_unsupported_extension_before_storage(monkeypatch):
    def fail_if_called():
        raise AssertionError("Storage service should not be called for invalid filenames")

    monkeypatch.setattr(rag_endpoint, "get_storage_service", fail_if_called)

    with pytest.raises(Exception) as exc_info:
        asyncio.run(
            rag_endpoint.generate_presigned_url(
                request=SimpleNamespace(),
                filename="../evil.svg",
                content_type="image/svg+xml",
                knowledge_base_id=None,
                x_organization_id=None,
                db=object(),
                current_user=SimpleNamespace(id="user-id"),
            )
        )

    assert getattr(exc_info.value, "status_code", None) == 400
    assert exc_info.value.detail == {"reason_code": "file_type.unsupported"}


def test_analyze_document_returns_safe_error(monkeypatch):
    class FailingIngestionService:
        def __init__(self, db, user_id, organization_id=None):
            pass

        async def analyze_document(self, document_id):
            raise RuntimeError("path=C:/secret/file.pdf")

    monkeypatch.setattr(rag_endpoint, "IngestionService", FailingIngestionService)
    monkeypatch.setattr(
        rag_endpoint,
        "_authorize_knowledge_document_action",
        lambda *_args, **_kwargs: (object(), object()),
    )
    request = SimpleNamespace(
        state=SimpleNamespace(request_id="req"),
        url=SimpleNamespace(path="/api/v1/rag/document/document-id/analyze"),
    )

    with pytest.raises(Exception) as exc_info:
        asyncio.run(
            rag_endpoint.analyze_document(
                document_id="document-id",
                request=request,
                x_organization_id="33333333-3333-3333-3333-333333333333",
                db=object(),
                current_user=SimpleNamespace(id="user-id"),
            )
        )

    assert getattr(exc_info.value, "status_code", None) == 400
    assert exc_info.value.detail == {"reason_code": "document.analyze_failed"}


def test_direct_upload_source_uses_canonical_s3_url_and_user_prefix(monkeypatch):
    user_id = "11111111-1111-1111-1111-111111111111"
    monkeypatch.setattr(rag_endpoint.settings, "S3_BUCKET_NAME", "safe-bucket")
    monkeypatch.setattr(rag_endpoint.settings, "AWS_REGION", "ap-northeast-2")

    file_path, filename, meta_info = rag_endpoint._prepare_direct_upload_source(
        s3_file_key=f"uploads/{user_id}/doc.pdf",
        user_id=user_id,
    )

    assert file_path == (
        "https://safe-bucket.s3.ap-northeast-2.amazonaws.com/"
        f"uploads/{user_id}/doc.pdf"
    )
    assert filename == "doc.pdf"
    assert meta_info == {
        "s3_key": f"uploads/{user_id}/doc.pdf",
        "upload_method": "direct",
    }


def test_direct_upload_source_rejects_cross_user_or_active_file(monkeypatch):
    user_id = "11111111-1111-1111-1111-111111111111"
    monkeypatch.setattr(rag_endpoint.settings, "S3_BUCKET_NAME", "safe-bucket")
    monkeypatch.setattr(rag_endpoint.settings, "AWS_REGION", "ap-northeast-2")

    with pytest.raises(Exception) as prefix_exc:
        rag_endpoint._prepare_direct_upload_source(
            s3_file_key="uploads/22222222-2222-2222-2222-222222222222/doc.pdf",
            user_id=user_id,
        )
    assert getattr(prefix_exc.value, "status_code", None) == 400

    with pytest.raises(Exception) as active_exc:
        rag_endpoint._prepare_direct_upload_source(
            s3_file_key=f"uploads/{user_id}/evil.html",
            user_id=user_id,
        )
    assert getattr(active_exc.value, "status_code", None) == 400


def test_safe_document_filename_strips_path_and_rejects_active_extension():
    assert rag_endpoint._validate_safe_document_filename("../nested/report.pdf") == "report.pdf"

    with pytest.raises(Exception) as exc_info:
        rag_endpoint._validate_safe_document_filename("../nested/evil.svg")

    assert getattr(exc_info.value, "status_code", None) == 400


def test_upload_existing_kb_requires_write_permission_without_owner_bypass(monkeypatch):
    user_id = "11111111-1111-1111-1111-111111111111"
    owner_id = "22222222-2222-2222-2222-222222222222"
    org_id = "33333333-3333-3333-3333-333333333333"
    kb_id = "44444444-4444-4444-4444-444444444444"
    request = SimpleNamespace(
        state=SimpleNamespace(request_id="req"),
        url=SimpleNamespace(path="/api/v1/rag/upload"),
    )
    user = SimpleNamespace(id=user_id)
    kb = SimpleNamespace(
        id=kb_id,
        user_id=owner_id,
        organization_id=org_id,
        embedding_model="text-embedding-3-small",
    )

    class DeniedAuthorizationService:
        def __init__(self, *_args, **_kwargs):
            pass

        def load_kb(self, requested_kb_id, action):
            assert requested_kb_id == kb_id
            assert action == "write"
            raise rag_endpoint.KnowledgePermissionDenied

    monkeypatch.setattr(
        rag_endpoint,
        "KnowledgeAuthorizationService",
        DeniedAuthorizationService,
    )

    with pytest.raises(Exception) as denied:
        rag_endpoint._get_or_create_knowledge_base(
            request,
            db=object(),
            user=user,
            organization_id=org_id,
            kb_id=kb_id,
            name=None,
            description=None,
            ai_model=None,
            top_k=5,
            similarity_threshold=0.7,
            file=None,
        )

    assert getattr(denied.value, "status_code", None) == 403

    class AllowedAuthorizationService(DeniedAuthorizationService):
        def load_kb(self, requested_kb_id, action):
            assert requested_kb_id == kb_id
            assert action == "write"
            return kb

    monkeypatch.setattr(
        rag_endpoint,
        "KnowledgeAuthorizationService",
        AllowedAuthorizationService,
    )
    result = rag_endpoint._get_or_create_knowledge_base(
        request,
        db=object(),
        user=user,
        organization_id=org_id,
        kb_id=kb_id,
        name=None,
        description=None,
        ai_model=None,
        top_k=5,
        similarity_threshold=0.7,
        file=None,
    )
    assert result == (kb_id, "text-embedding-3-small")


def test_uploaded_active_content_is_not_served_inline():
    assert (
        content_disposition_type_for_document(
            "evil.html",
            "text/html",
        )
        == "attachment"
    )
    assert (
        content_disposition_type_for_document(
            "safe.pdf",
            "application/pdf",
        )
        == "inline"
    )


def test_ingestion_service_decrypts_protected_api_config(monkeypatch):
    monkeypatch.setenv("ENCRYPTION_KEY", Fernet.generate_key().decode())
    encryption_manager._cipher_suite = None
    _, _, meta_info = _prepare_api_source(
        "https://api.example.com/data?token=secret",
        "POST",
        '{"Accept":"application/json"}',
        '{"query":"safe"}',
    )
    service = IngestionOrchestrator(db=None)
    doc = SimpleNamespace(source_type=SourceType.API, meta_info=meta_info, id="doc-id")

    config = service._build_config(doc)

    assert config["url"] == "https://api.example.com/data?token=secret"
    assert config["method"] == "POST"
    assert config["headers"] == '{"Accept":"application/json"}'
    assert config["body"] == '{"query":"safe"}'


def test_api_processor_does_not_copy_raw_url_into_chunk_metadata(monkeypatch):
    class FakeResponse:
        status_code = 200

        def json(self):
            return {"message": "hello"}

        @property
        def text(self):
            return '{"message":"hello"}'

    monkeypatch.setattr(
        api_processor,
        "safe_http_request",
        lambda *args, **kwargs: FakeResponse(),
    )

    result = ApiProcessor().process({"url": "https://example.com/private/path"})

    assert result.metadata == {"source_type": "API", "status_code": 200}
    assert result.chunks
    assert result.chunks[0]["metadata"]["source"] == "api_response"
    assert "example.com/private/path" not in str(result.metadata)
    assert "example.com/private/path" not in str(result.chunks)


def test_api_processor_returns_sanitized_egress_error(monkeypatch, caplog):
    def deny(*args, **kwargs):
        raise EgressGuardError("egress.private_target")

    monkeypatch.setattr(api_processor, "safe_http_request", deny)

    result = ApiProcessor().process({"url": "http://127.0.0.1/admin"})

    assert result.chunks == []
    assert result.metadata == {
        "error": "Outbound request denied.",
        "reason_code": "configuration.invalid",
    }
    assert "127.0.0.1" not in str(result.metadata)
    assert "127.0.0.1" not in caplog.text
    assert "egress.private_target" not in caplog.text
