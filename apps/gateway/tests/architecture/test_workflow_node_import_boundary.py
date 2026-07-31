"""Gateway와 Workflow worker 패키지의 import 경계를 검증한다."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys


REPOSITORY_ROOT = Path(__file__).resolve().parents[4]


def test_llm_entity_import_does_not_load_worker_runtime() -> None:
    """Data-only schema import must not require worker-only dependencies."""

    script = """
import sys

from apps.workflow_engine.workflow.nodes.llm.entities import LLMNodeData

assert LLMNodeData.__name__ == "LLMNodeData"
assert "apps.workflow_engine.workflow.nodes.llm.llm_node" not in sys.modules
"""
    environment = os.environ.copy()
    existing_pythonpath = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = os.pathsep.join(
        value
        for value in (str(REPOSITORY_ROOT), existing_pythonpath)
        if value
    )

    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=REPOSITORY_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr


def test_node_factory_import_does_not_require_gevent_runtime() -> None:
    """Gateway-side graph validation must not require worker-only gevent."""

    script = """
import sys


class BlockGeventImport:
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "gevent" or fullname.startswith("gevent."):
            raise ModuleNotFoundError("blocked worker-only gevent dependency")
        return None


sys.meta_path.insert(0, BlockGeventImport())

from apps.workflow_engine.workflow.core.workflow_node_factory import NodeFactory

assert "llmNode" in NodeFactory.NODE_REGISTRY
assert "gevent" not in sys.modules
"""
    environment = os.environ.copy()
    existing_pythonpath = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = os.pathsep.join(
        value
        for value in (str(REPOSITORY_ROOT), existing_pythonpath)
        if value
    )

    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=REPOSITORY_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
