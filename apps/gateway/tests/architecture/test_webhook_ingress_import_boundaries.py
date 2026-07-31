import ast
from pathlib import Path


APPLICATION_ROOT = (
    Path(__file__).resolve().parents[2] / "application" / "webhook_ingress"
)
FORBIDDEN_PREFIXES = (
    "fastapi",
    "sqlalchemy",
    "starlette",
    "apps.gateway.adapters",
    "apps.gateway.api",
    "apps.gateway.composition",
    "apps.gateway.services",
    "apps.shared.db",
    "apps.shared.schemas",
)


def test_webhook_ingress_application_does_not_import_outer_layers() -> None:
    violations: list[str] = []
    application_files = list(APPLICATION_ROOT.rglob("*.py"))
    assert application_files, f"No application files found under {APPLICATION_ROOT}"

    for path in application_files:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            modules: list[str] = []
            if isinstance(node, ast.Import):
                modules.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules.append(node.module)
                if node.level > 2:
                    violations.append(
                        f"{path}:{node.lineno}: relative import escapes webhook package"
                    )
            for module in modules:
                if module.startswith(FORBIDDEN_PREFIXES):
                    violations.append(f"{path}:{node.lineno}: {module}")

    assert violations == []
