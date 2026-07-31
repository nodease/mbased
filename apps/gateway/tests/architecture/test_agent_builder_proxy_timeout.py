from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[4]


def test_docker_nginx_allows_the_agent_builder_planner_timeout_budget():
    config = (ROOT / "docker" / "nginx" / "nginx.conf").read_text(encoding="utf-8")
    match = re.search(
        r"location\s+\^~\s+/api/v1/agent-builder/\s*\{(?P<body>.*?)\n\s*\}",
        config,
        flags=re.DOTALL,
    )

    assert match is not None
    body = match.group("body")
    read_timeout = re.search(r"proxy_read_timeout\s+(\d+)s;", body)
    assert read_timeout is not None
    assert int(read_timeout.group(1)) >= 420
    assert "proxy_pass http://gateway:8000;" in body


def test_next_proxy_allows_the_agent_builder_planner_timeout_budget():
    config = (ROOT / "apps" / "client" / "next.config.ts").read_text(
        encoding="utf-8"
    )
    timeout = re.search(r"proxyTimeout:\s*(\d+)", config)

    assert timeout is not None
    assert int(timeout.group(1)) >= 420_000
