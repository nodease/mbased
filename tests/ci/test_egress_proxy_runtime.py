from __future__ import annotations

import os
import subprocess
import tempfile
import time
import uuid
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "ci" / "fixtures" / "egress-proxy"
CURL_IMAGE = (
    "curlimages/curl:8.12.1@"
    "sha256:94e9e444bcba979c2ea12e27ae39bee4cd10bc7041a472c4727a558e213744e6"
)
PYTHON_IMAGE = (
    "python:3.11.11-alpine@"
    "sha256:d5e2fc72296647869f5eeb09e7741088a1841195059de842b05b94cb9d3771bb"
)


pytestmark = pytest.mark.skipif(
    os.getenv("NODEASE_RUN_EGRESS_PROXY_INTEGRATION") != "1",
    reason="requires disposable Docker networks and containers",
)


def _run(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=ROOT,
        check=check,
        capture_output=True,
        text=True,
        timeout=180,
    )


def _proxy_status(network: str, proxy: str, url: str) -> str:
    result = _run(
        "docker",
        "run",
        "--rm",
        "--network",
        network,
        CURL_IMAGE,
        "--silent",
        "--show-error",
        "--connect-timeout",
        "3",
        "--max-time",
        "8",
        "--output",
        "/dev/null",
        "--write-out",
        "%{http_code}",
        "--proxy",
        proxy,
        url,
        check=False,
    )
    return result.stdout.strip()


def _connector_tunnel_output(network: str, target_port: int) -> str:
    result = _run(
        "docker",
        "run",
        "--rm",
        "--network",
        network,
        CURL_IMAGE,
        "--silent",
        "--show-error",
        "--connect-timeout",
        "3",
        "--max-time",
        "8",
        "--proxytunnel",
        "--proxy",
        "http://proxy:3130",
        f"telnet://11.240.0.80:{target_port}",
        check=False,
    )
    assert result.returncode == 0
    return result.stdout


def test_squid_rejects_unsafe_dns_sets_and_direct_or_unauthorized_paths() -> None:
    suffix = uuid.uuid4().hex[:10]
    client_network = f"nodease-egress-client-{suffix}"
    egress_network = f"nodease-egress-origin-{suffix}"
    dns_name = f"nodease-egress-dns-{suffix}"
    origin_name = f"nodease-egress-origin-{suffix}"
    proxy_name = f"nodease-egress-proxy-{suffix}"
    image = f"nodease-egress-proxy-test:{suffix}"
    created_containers: list[str] = []
    created_networks: list[str] = []

    with tempfile.TemporaryDirectory(prefix="nodease-egress-") as temp_dir:
        temp = Path(temp_dir)
        squid_config = (
            ROOT.joinpath("docker/proxy/squid.conf")
            .read_text(encoding="utf-8")
            .replace("positive_dns_ttl 60 seconds", "positive_dns_ttl 1 seconds")
        )
        config_path = temp / "squid.conf"
        sources_path = temp / "authorized-sources.conf"
        config_path.write_text(squid_config, encoding="utf-8")
        sources_path.write_text(
            "acl authorized_source src 172.31.250.0/24\n"
            "acl workflow_http_source src 172.31.250.0/24\n",
            encoding="utf-8",
        )

        try:
            _run(
                "docker",
                "build",
                "--quiet",
                "--tag",
                image,
                "--file",
                str(ROOT / "docker" / "proxy" / "Dockerfile"),
                str(ROOT),
            )
            _run(
                "docker",
                "network",
                "create",
                "--internal",
                "--subnet",
                "172.31.250.0/24",
                client_network,
            )
            created_networks.append(client_network)
            _run(
                "docker",
                "network",
                "create",
                "--subnet",
                "11.240.0.0/24",
                egress_network,
            )
            created_networks.append(egress_network)

            for name, ip, script in (
                (dns_name, "11.240.0.53", "dns_server.py"),
                (origin_name, "11.240.0.80", "origin_server.py"),
            ):
                _run(
                    "docker",
                    "run",
                    "--detach",
                    "--name",
                    name,
                    "--network",
                    egress_network,
                    "--ip",
                    ip,
                    "--mount",
                    f"type=bind,src={FIXTURES / script},dst=/srv/{script},readonly",
                    PYTHON_IMAGE,
                    "python",
                    f"/srv/{script}",
                )
                created_containers.append(name)

            _run(
                "docker",
                "run",
                "--detach",
                "--name",
                proxy_name,
                "--network",
                egress_network,
                "--ip",
                "11.240.0.10",
                "--dns",
                "11.240.0.53",
                "--read-only",
                "--tmpfs",
                "/run/squid:rw,noexec,nosuid,nodev,uid=13,gid=13,mode=0700",
                "--tmpfs",
                "/var/log/squid",
                "--tmpfs",
                "/var/spool/squid",
                "--tmpfs",
                "/tmp",
                "--mount",
                f"type=bind,src={config_path},dst=/etc/squid/squid.conf,readonly",
                "--mount",
                (
                    "type=bind,"
                    f"src={sources_path},"
                    "dst=/etc/squid/authorized-sources.conf,readonly"
                ),
                image,
            )
            created_containers.append(proxy_name)
            _run(
                "docker",
                "network",
                "connect",
                "--alias",
                "proxy",
                client_network,
                proxy_name,
            )

            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                if (
                    _proxy_status(
                        client_network,
                        "http://proxy:3129",
                        "http://safe.test/",
                    )
                    == "200"
                ):
                    break
                time.sleep(0.5)
            else:
                logs = _run(
                    "docker",
                    "logs",
                    proxy_name,
                    check=False,
                )
                diagnostic = f"{logs.stdout}\n{logs.stderr}".strip()[-4096:]
                pytest.fail(f"proxy did not become ready: {diagnostic}")

            assert (
                _proxy_status(client_network, "http://proxy:3128", "http://safe.test/")
                == "403"
            )
            for hostname in (
                "unsafe.test",
                "mixed.test",
                "mixed-aaaa.test",
                "cname-unsafe.test",
            ):
                assert (
                    _proxy_status(
                        client_network,
                        "http://proxy:3129",
                        f"http://{hostname}/",
                    )
                    == "403"
                )

            assert (
                _proxy_status(
                    client_network,
                    "http://proxy:3129",
                    "http://rebind.test/",
                )
                == "200"
            )
            time.sleep(2)
            assert (
                _proxy_status(
                    client_network,
                    "http://proxy:3129",
                    "http://rebind.test/",
                )
                == "403"
            )

            for connector_port in (22, 5432):
                assert (
                    _connector_tunnel_output(client_network, connector_port)
                    == "connector-ready"
                )
            assert (
                _proxy_status(
                    client_network,
                    "http://proxy:3130",
                    "http://safe.test/",
                )
                == "403"
            )

            assert (
                _proxy_status(
                    egress_network,
                    f"http://{proxy_name}:3129",
                    "http://safe.test/",
                )
                == "403"
            )
            direct = _run(
                "docker",
                "run",
                "--rm",
                "--network",
                client_network,
                CURL_IMAGE,
                "--silent",
                "--connect-timeout",
                "2",
                "--max-time",
                "3",
                "http://11.240.0.80/",
                check=False,
            )
            assert direct.returncode != 0
        finally:
            for container in reversed(created_containers):
                _run("docker", "rm", "--force", container, check=False)
            for network in reversed(created_networks):
                _run("docker", "network", "rm", network, check=False)
            _run("docker", "image", "rm", "--force", image, check=False)
