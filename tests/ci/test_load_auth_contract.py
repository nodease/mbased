from __future__ import annotations

import ast
from pathlib import Path

import pytest


LOAD_SCRIPT_DIR = Path(__file__).resolve().parents[1] / "load"
LOAD_SCRIPTS = tuple(LOAD_SCRIPT_DIR / f"load{index}.py" for index in range(1, 4))
ENV_EXAMPLE = Path(__file__).resolve().parents[2] / "dev" / ".env.example"


@pytest.mark.parametrize("script_path", LOAD_SCRIPTS, ids=lambda path: path.name)
def test_load_script_uses_bearer_without_secret_preview(script_path: Path) -> None:
    source = script_path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    string_literals = {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }

    assert "X-Auth-Secret" not in string_literals
    assert "Authorization" in string_literals
    assert "token_preview" not in source

    authorization_values = [
        value
        for node in ast.walk(tree)
        if isinstance(node, ast.Dict)
        for key, value in zip(node.keys, node.values, strict=True)
        if isinstance(key, ast.Constant) and key.value == "Authorization"
    ]
    assert len(authorization_values) == 1
    assert isinstance(authorization_values[0], ast.JoinedStr)
    assert any(
        isinstance(value, ast.Constant) and value.value == "Bearer "
        for value in authorization_values[0].values
    )


def test_load_environment_guidance_uses_bearer_contract() -> None:
    guidance = ENV_EXAMPLE.read_text(encoding="utf-8")

    assert guidance.find("X-Auth-Secret") == -1
    assert guidance.find("Authorization: Bearer") >= 0
