import ast
from pathlib import Path


APPLICATION_DIR = (
    Path(__file__).resolve().parents[2] / "application" / "authentication"
)
FORBIDDEN_PREFIXES = (
    "fastapi",
    "sqlalchemy",
    "redis",
    "apps.gateway.adapters",
    "apps.gateway.composition",
    "apps.gateway.services",
    "apps.shared",
)


def test_password_authentication_application_has_no_framework_or_adapter_imports():
    violations = []
    for path in APPLICATION_DIR.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                if name.startswith(FORBIDDEN_PREFIXES):
                    violations.append(f"{path.name}:{node.lineno}:{name}")

    assert violations == []
