from __future__ import annotations

import ast
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[4]
_TARGETS = (
    "apps/gateway/services/gmail_oauth_service.py",
    "apps/gateway/adapters/google_oauth.py",
    "apps/gateway/composition/google_oauth.py",
    "apps/workflow_engine/services/google_oauth_service.py",
    "apps/workflow_engine/adapters/google_oauth.py",
    "apps/workflow_engine/adapters/gmail_mailbox_provider.py",
    "apps/workflow_engine/adapters/gmail_draft_provider.py",
    "apps/workflow_engine/adapters/providers/github.py",
    "apps/workflow_engine/adapters/providers/slack.py",
    "apps/workflow_engine/composition/github.py",
    "apps/workflow_engine/composition/mail.py",
    "apps/workflow_engine/composition/slack.py",
    "apps/workflow_engine/workflow/nodes/github/github_node.py",
    "apps/workflow_engine/workflow/nodes/mail/acknowledge_node.py",
    "apps/workflow_engine/workflow/nodes/mail/gmail_draft_node.py",
    "apps/workflow_engine/workflow/nodes/mail/mail_node.py",
    "apps/workflow_engine/workflow/nodes/slack/slack_post_node.py",
)


class _DirectHttpClientVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.module_aliases: dict[str, str] = {}
        self.symbol_aliases: dict[str, tuple[str, str]] = {}
        self.violations: list[tuple[int, str]] = []

    def visit_Import(self, node: ast.Import) -> None:
        for name in node.names:
            if name.name in {"httpx", "requests"}:
                self.module_aliases[name.asname or name.name] = name.name
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module in {"httpx", "requests"}:
            for name in node.names:
                self.symbol_aliases[name.asname or name.name] = (
                    node.module,
                    name.name,
                )
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        target = node.func
        if isinstance(target, ast.Name):
            imported = self.symbol_aliases.get(target.id)
            if imported and imported[1] in {
                "Client",
                "AsyncClient",
                "Session",
                "get",
                "post",
                "request",
            }:
                self.violations.append((node.lineno, ".".join(imported)))
        elif isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name):
            module = self.module_aliases.get(target.value.id)
            if module and target.attr in {
                "Client",
                "AsyncClient",
                "Session",
                "get",
                "post",
                "request",
            }:
                self.violations.append((node.lineno, f"{module}.{target.attr}"))
        self.generic_visit(node)


@pytest.mark.parametrize("relative_path", _TARGETS)
def test_fixed_saas_consumers_cannot_construct_direct_http_clients(
    relative_path: str,
) -> None:
    source = (_ROOT / relative_path).read_text(encoding="utf-8")
    visitor = _DirectHttpClientVisitor()
    visitor.visit(ast.parse(source, filename=relative_path))

    assert visitor.violations == []


def test_mail_adapters_do_not_expose_a_send_surface() -> None:
    production = "\n".join(
        (_ROOT / relative_path).read_text(encoding="utf-8")
        for relative_path in (
            "apps/workflow_engine/adapters/gmail_mailbox_provider.py",
            "apps/workflow_engine/adapters/gmail_draft_provider.py",
        )
    )

    assert "drafts.send" not in production
    assert "messages.send" not in production


def test_application_services_depend_on_ports_not_concrete_http_adapters() -> None:
    gateway = (_ROOT / "apps/gateway/services/gmail_oauth_service.py").read_text(
        encoding="utf-8"
    )
    worker = (
        _ROOT / "apps/workflow_engine/services/google_oauth_service.py"
    ).read_text(encoding="utf-8")

    assert "apps.gateway.adapters" not in gateway
    assert "apps.workflow_engine.adapters" not in worker
