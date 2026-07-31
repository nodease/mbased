from __future__ import annotations

import ast
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from apps.shared.domain.app_auth_secret import (
    APP_AUTH_SECRET_VERIFIER_VERSION,
    app_auth_secret_verifier,
    app_auth_secret_verifier_state_is_valid,
    generate_app_auth_secret,
    verify_app_auth_secret,
)
from scripts.managed_app_secret_fixture import (
    configure_managed_app_secret_fixture,
)

SCRIPT_DIR = Path(__file__).resolve().parents[2] / "scripts"
DIRECT_RUNTIME_SCRIPTS = tuple(
    SCRIPT_DIR / name
    for name in (
        "experiment_model_router_61_runs.py",
        "verify_model_router_actual.py",
        "verify_model_router_demo.py",
    )
)


def test_fixture_replaces_raw_state_with_managed_verifier() -> None:
    app = SimpleNamespace(
        auth_secret=None,
        auth_secret_verifier=None,
        auth_secret_verifier_version=None,
        auth_secret_generation=0,
        auth_secret_previous_verifier=None,
        auth_secret_previous_verifier_version=None,
        auth_secret_previous_valid_until=None,
        auth_secret_rotated_at=None,
    )

    configure_managed_app_secret_fixture(app)

    assert app.auth_secret is None
    assert app.auth_secret_generation == 1
    assert app_auth_secret_verifier_state_is_valid(
        app.auth_secret_verifier,
        app.auth_secret_verifier_version,
    )
    assert app.auth_secret_previous_verifier is None
    assert app.auth_secret_previous_verifier_version is None
    assert app.auth_secret_previous_valid_until is None
    assert app.auth_secret_rotated_at is not None


def test_fixture_preserves_a_legacy_credential_without_raw_storage() -> None:
    candidate = generate_app_auth_secret()
    app = SimpleNamespace(
        auth_secret=candidate,
        auth_secret_verifier=None,
        auth_secret_verifier_version=None,
        auth_secret_generation=0,
        auth_secret_previous_verifier=None,
        auth_secret_previous_verifier_version=None,
        auth_secret_previous_valid_until=None,
        auth_secret_rotated_at=None,
    )

    configure_managed_app_secret_fixture(app)

    assert app.auth_secret is None
    assert verify_app_auth_secret(
        candidate,
        current_verifier=app.auth_secret_verifier,
        current_verifier_version=app.auth_secret_verifier_version,
    )


def test_fixture_does_not_rotate_valid_managed_state() -> None:
    verifier = app_auth_secret_verifier(generate_app_auth_secret())
    rotated_at = datetime.now(timezone.utc)
    app = SimpleNamespace(
        auth_secret=None,
        auth_secret_verifier=verifier,
        auth_secret_verifier_version=APP_AUTH_SECRET_VERIFIER_VERSION,
        auth_secret_generation=3,
        auth_secret_previous_verifier=None,
        auth_secret_previous_verifier_version=None,
        auth_secret_previous_valid_until=None,
        auth_secret_rotated_at=rotated_at,
    )

    configure_managed_app_secret_fixture(app)

    assert app.auth_secret_verifier == verifier
    assert app.auth_secret_generation == 3
    assert app.auth_secret_rotated_at == rotated_at


@pytest.mark.parametrize(
    "script_path",
    DIRECT_RUNTIME_SCRIPTS,
    ids=lambda path: path.name,
)
def test_direct_runtime_script_uses_managed_secret_fixture(
    script_path: Path,
) -> None:
    tree = ast.parse(script_path.read_text(encoding="utf-8"))
    app_constructors = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "App"
    ]

    assert app_constructors
    assert all(
        keyword.arg != "auth_secret"
        for constructor in app_constructors
        for keyword in constructor.keywords
    )
    assert any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "configure_managed_app_secret_fixture"
        for node in ast.walk(tree)
    )
