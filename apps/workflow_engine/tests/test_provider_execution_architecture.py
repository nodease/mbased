from __future__ import annotations

import ast
import os
import tokenize
from collections.abc import Iterator
from pathlib import Path


_EXCLUDED_SOURCE_PARTS = {"tests", "__pycache__", "venv"}


def _iter_workflow_production_sources(workflow_root: Path) -> Iterator[Path]:
    for directory, directory_names, filenames in os.walk(workflow_root):
        directory_names[:] = sorted(
            name
            for name in directory_names
            if not name.startswith(".") and name not in _EXCLUDED_SOURCE_PARTS
        )
        for filename in sorted(filenames):
            if filename.endswith(".py"):
                yield Path(directory, filename)


def _parse_python_source(source_path: Path) -> ast.AST:
    with tokenize.open(source_path) as source:
        return ast.parse(source.read(), filename=str(source_path))


def test_llm_node_depends_on_provider_application_port_not_capability_service():
    source_path = (
        Path(__file__).parents[1] / "workflow" / "nodes" / "llm" / "llm_node.py"
    )
    tree = _parse_python_source(source_path)
    imported_modules = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }

    assert "apps.shared.services.provider_execution_capability" not in imported_modules
    assert "apps.shared.domain.provider_execution_capability" not in imported_modules
    assert not any(
        module.startswith("apps.shared.services.llm_client")
        for module in imported_modules
    )
    assert "apps.workflow_engine.composition.provider_execution" not in imported_modules
    assert not any(
        module.startswith("apps.workflow_engine.adapters.provider_")
        for module in imported_modules
    )


def test_legacy_llm_service_does_not_own_capability_contract():
    source_path = Path(__file__).parents[1] / "services" / "llm_service.py"
    tree = _parse_python_source(source_path)
    imported_modules = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }

    assert "apps.shared.services.provider_execution_capability" not in imported_modules
    assert "apps.shared.domain.provider_execution_capability" not in imported_modules


def test_provider_runtime_router_does_not_own_strategy_implementations():
    source_path = Path(__file__).parents[1] / "adapters" / "provider_execution.py"
    tree = _parse_python_source(source_path)
    imported_modules = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }

    assert "apps.shared.services.provider_execution_capability" not in imported_modules
    assert "apps.shared.domain.provider_execution_capability" not in imported_modules
    assert "apps.workflow_engine.services.llm_service" not in imported_modules


def test_capability_adapters_are_the_only_workflow_shared_capability_owners():
    workflow_root = Path(__file__).parents[1]
    owners = set()
    capability_modules = {
        "apps.shared.services.provider_execution_capability",
        "apps.shared.domain.provider_execution_capability",
    }

    for source_path in _iter_workflow_production_sources(workflow_root):
        tree = _parse_python_source(source_path)
        imported_modules = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        }
        if capability_modules.intersection(imported_modules):
            owners.add(source_path.relative_to(workflow_root).as_posix())

    assert owners == {
        "adapters/provider_execution_capability.py",
        "adapters/query_embedding_capability.py",
    }


def test_provider_architecture_scan_ignores_non_source_runtime_directories(tmp_path):
    workflow_root = tmp_path / "workflow_engine"
    production_source = workflow_root / "adapters" / "provider.py"
    production_source.parent.mkdir(parents=True)
    production_source.write_text("VALUE = 1\n", encoding="utf-8")

    ignored_sources = (
        workflow_root / ".venv" / "Lib" / "site-packages" / "vendor.py",
        workflow_root / "venv" / "Lib" / "site-packages" / "vendor.py",
        workflow_root / "tests" / "test_vendor.py",
        workflow_root / "__pycache__" / "cached.py",
    )
    for ignored_source in ignored_sources:
        ignored_source.parent.mkdir(parents=True, exist_ok=True)
        ignored_source.write_bytes(
            "# -*- coding: big5 -*-\nVALUE = '測試'\n".encode("big5")
        )

    assert list(_iter_workflow_production_sources(workflow_root)) == [
        production_source
    ]
