import ast
from pathlib import Path


def test_connector_application_does_not_import_framework_or_adapters() -> None:
    root = Path(__file__).parents[2] / "application" / "connectors"
    forbidden = (
        "fastapi",
        "sqlalchemy",
        "redis",
        "apps.gateway.adapters",
        "apps.gateway.composition",
    )

    for path in root.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.append(node.module)
        assert not [name for name in imports if name.startswith(forbidden)], path
