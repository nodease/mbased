from __future__ import annotations

import ast
import importlib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[4]


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def test_memory_domain_has_no_framework_or_adapter_imports():
    forbidden = ("fastapi", "celery", "sqlalchemy", "openai", "anthropic", "google")
    for path in (ROOT / "apps" / "memory" / "domain").glob("*.py"):
        imports = _imports(path)
        assert not any(
            name == prefix or name.startswith(f"{prefix}.")
            for name in imports
            for prefix in forbidden
        ), f"{path.name} crosses the Memory domain boundary"


def test_memory_application_does_not_import_orm_or_outer_services():
    forbidden = (
        "sqlalchemy",
        "apps.shared.db.models",
        "apps.gateway",
        "apps.workflow_engine",
        "apps.log_system",
    )
    for path in (ROOT / "apps" / "memory" / "application").glob("*.py"):
        imports = _imports(path)
        assert not any(
            name == prefix or name.startswith(f"{prefix}.")
            for name in imports
            for prefix in forbidden
        ), f"{path.name} crosses the Memory application boundary"


def test_importing_memory_package_has_no_connection_or_worker_side_effects():
    module = importlib.import_module("apps.memory")

    assert not hasattr(module, "engine")
    assert not hasattr(module, "celery_app")


def test_memory_orm_models_are_only_imported_by_persistence_adapter_in_production():
    allowed = {
        Path("apps/shared/db/models/__init__.py"),
        Path("apps/memory/adapters/persistence/repository.py"),
        Path("apps/memory/adapters/persistence/readiness.py"),
    }
    offenders = []
    for path in (ROOT / "apps").rglob("*.py"):
        relative = path.relative_to(ROOT)
        if (
            "tests" in relative.parts
            or ".venv" in relative.parts
            or relative in allowed
        ):
            continue
        source = path.read_text(encoding="utf-8")
        if "apps.shared.db.models.conversation_memory" in source:
            offenders.append(relative.as_posix())

    assert offenders == []
