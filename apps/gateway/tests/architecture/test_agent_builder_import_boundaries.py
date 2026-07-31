import ast
from pathlib import Path

import pytest


APPLICATION_ROOT = Path(__file__).resolve().parents[2] / "application" / "agent_builder"
FORBIDDEN_PREFIXES = (
    "anthropic",
    "fastapi",
    "google",
    "httpx",
    "openai",
    "requests",
    "sqlalchemy",
    "apps.gateway.adapters",
    "apps.gateway.api",
    "apps.gateway.composition",
    "apps.gateway.services",
    "apps.shared.db",
    "apps.shared.services.credential_encryption",
    "apps.shared.services.llm_client",
    "apps.shared.services.llm_usage_context",
)
PURE_POLICY_FORBIDDEN_PREFIXES = FORBIDDEN_PREFIXES + (
    "pydantic",
    "apps.shared.schemas",
)


def _find_import_violations(
    tree: ast.AST,
    source_name: str,
    *,
    forbidden_prefixes: tuple[str, ...] = FORBIDDEN_PREFIXES,
) -> list[str]:
    violations: list[str] = []
    for node in ast.walk(tree):
        modules: list[str] = []
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                modules.append(node.module)
            if node.level > 1:
                message = "relative import escapes agent_builder package"
                violations.append(f"{source_name}:{node.lineno}: {message}")
        for module in modules:
            if module.startswith(forbidden_prefixes):
                violations.append(f"{source_name}:{node.lineno}: {module}")
    return violations


def test_agent_builder_application_does_not_import_outer_layers():
    violations: list[str] = []
    application_files = list(APPLICATION_ROOT.rglob("*.py"))
    assert application_files, f"No application files found under {APPLICATION_ROOT}"

    for path in application_files:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        violations.extend(_find_import_violations(tree, str(path)))

    assert violations == []


def test_agent_builder_application_allows_shared_pydantic_contracts():
    tree = ast.parse(
        "from apps.shared.schemas.agent_builder import GraphMutation\n"
        "from pydantic import TypeAdapter"
    )

    assert _find_import_violations(tree, "synthetic.py") == []


def test_model_recommendation_policy_remains_framework_independent():
    path = APPLICATION_ROOT / "model_recommendation_policy.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

    assert (
        _find_import_violations(
            tree,
            str(path),
            forbidden_prefixes=PURE_POLICY_FORBIDDEN_PREFIXES,
        )
        == []
    )


@pytest.mark.parametrize(
    "statement",
    [
        "import openai",
        "import anthropic",
        "import google.genai",
        "import requests",
        "import httpx",
        "from apps.shared.services.llm_client import LLMClient",
        "from apps.shared.services.credential_encryption import decrypt_credential",
        "from .. import shared_policy",
    ],
)
def test_agent_builder_application_boundary_rejects_provider_dependencies(
    statement,
):
    tree = ast.parse(statement)

    assert _find_import_violations(tree, "synthetic.py")
