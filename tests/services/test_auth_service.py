import importlib.util
import sys
import types
import uuid
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException


try:
    import jose  # noqa: F401
except ModuleNotFoundError:
    jose_module = types.ModuleType("jose")

    class JWTError(Exception):
        pass

    class FakeJWT:
        @staticmethod
        def encode(payload, secret, algorithm):
            return str(payload["user_id"])

        @staticmethod
        def decode(token, secret, algorithms):
            return {"user_id": token}

    jose_module.JWTError = JWTError
    jose_module.jwt = FakeJWT()
    sys.modules["jose"] = jose_module


def _load_module(module_name: str, path: Path):
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    if "." in module_name:
        parent_name, child_name = module_name.rsplit(".", 1)
        parent = sys.modules.get(parent_name)
        if parent is not None:
            setattr(parent, child_name, module)
    return module


ROOT = Path(__file__).resolve().parents[2]
for package_name, package_path in {
    "apps": ROOT / "apps",
    "apps.gateway": ROOT / "apps" / "gateway",
    "apps.gateway.services": ROOT / "apps" / "gateway" / "services",
    "apps.shared": ROOT / "apps" / "shared",
    "apps.shared.db": ROOT / "apps" / "shared" / "db",
    "apps.shared.db.models": ROOT / "apps" / "shared" / "db" / "models",
    "apps.shared.schemas": ROOT / "apps" / "shared" / "schemas",
}.items():
    package = sys.modules.get(package_name) or types.ModuleType(package_name)
    package.__path__ = [str(package_path)]
    sys.modules[package_name] = package
    if "." in package_name:
        parent_name, child_name = package_name.rsplit(".", 1)
        setattr(sys.modules[parent_name], child_name, package)

# 주의: base/user가 이미 import돼 있으면(결합 실행에서 다른 테스트가 실제
# 패키지를 먼저 import한 경우) 반드시 재사용한다. 여기서 격리 복사본으로
# 교체하면 아래의 "없을 때만 로드" 분기가 나머지 모델 로드를 건너뛰어,
# User만 담긴 불완전한 Base registry가 만들어지고 첫 User() 인스턴스화의
# mapper 설정이 "OrganizationMembership is not defined"로 실패한다.
if "apps.shared.db.base" not in sys.modules:
    _load_module(
        "apps.shared.db.base", ROOT / "apps" / "shared" / "db" / "base.py"
    )
user_module = sys.modules.get("apps.shared.db.models.user") or _load_module(
    "apps.shared.db.models.user",
    ROOT / "apps" / "shared" / "db" / "models" / "user.py",
)
for module_name, relative_path in (
    ("apps.shared.db.models.organization", "apps/shared/db/models/organization.py"),
    ("apps.shared.db.models.workflow", "apps/shared/db/models/workflow.py"),
    ("apps.shared.db.models.knowledge", "apps/shared/db/models/knowledge.py"),
    ("apps.shared.db.models.llm", "apps/shared/db/models/llm.py"),
    ("apps.shared.db.models.team", "apps/shared/db/models/team.py"),
):
    if module_name not in sys.modules:
        _load_module(module_name, ROOT / relative_path)
_load_module(
    "apps.shared.schemas.auth",
    ROOT / "apps" / "shared" / "schemas" / "auth.py",
)
auth_service_module = _load_module(
    "apps.gateway.services.auth_service",
    ROOT / "apps" / "gateway" / "services" / "auth_service.py",
)

AuthService = auth_service_module.AuthService
User = user_module.User


class FakeQuery:
    def __init__(self, db):
        self.db = db

    def join(self, *args, **kwargs):
        return self

    def filter(self, *args, **kwargs):
        return self

    def order_by(self, *args, **kwargs):
        return self

    def first(self):
        return self.db.query_first_result


class FakeDB:
    def __init__(self, user=None):
        self.user = user
        self.objects = []
        self.query_first_result = user
        self.commit_count = 0
        self.refresh_count = 0
        self.flush_count = 0

    def query(self, model):
        return FakeQuery(self)

    def add(self, obj):
        if getattr(obj, "id", None) is None:
            obj.id = uuid.uuid4()
        if hasattr(obj, "created_at") and obj.created_at is None:
            obj.created_at = datetime.now(timezone.utc)
        if hasattr(obj, "updated_at") and obj.updated_at is None:
            obj.updated_at = obj.created_at
        self.objects.append(obj)
        if isinstance(obj, User):
            self.user = obj
        self.query_first_result = None

    def commit(self):
        self.commit_count += 1

    def flush(self):
        self.flush_count += 1

    def refresh(self, user):
        self.refresh_count += 1


def make_user(**overrides):
    now = datetime.now(timezone.utc)
    values = {
        "id": uuid.uuid4(),
        "email": "admin@example.com",
        "name": "Admin",
        "password": AuthService.hash_password("password"),
        "social_provider": "none",
        "created_at": now,
        "updated_at": now,
    }
    values.update(overrides)
    return User(**values)


def test_login_updates_last_login_at_after_successful_password_check():
    user = make_user(last_login_at=None)
    db = FakeDB(user)

    result = AuthService.login(
        db,
        SimpleNamespace(email=user.email, password="password"),
    )

    assert result.user.id == str(user.id)
    assert user.last_login_at is not None
    assert user.last_login_at.tzinfo is not None
    assert db.commit_count == 1
    assert db.refresh_count == 1


def test_login_rejects_deactivated_user_without_updating_last_login_at():
    user = make_user(
        deactivated_at=datetime.now(timezone.utc),
        last_login_at=None,
    )
    db = FakeDB(user)

    with pytest.raises(HTTPException) as exc_info:
        AuthService.login(
            db,
            SimpleNamespace(email=user.email, password="password"),
        )

    assert exc_info.value.status_code == 403
    assert user.last_login_at is None
    assert db.commit_count == 0


def test_login_checks_password_before_reporting_deactivated_user():
    user = make_user(
        deactivated_at=datetime.now(timezone.utc),
        last_login_at=None,
    )
    db = FakeDB(user)

    with pytest.raises(HTTPException) as exc_info:
        AuthService.login(
            db,
            SimpleNamespace(email=user.email, password="wrong-password"),
        )

    assert exc_info.value.status_code == 401
    assert user.last_login_at is None
    assert db.commit_count == 0


@pytest.mark.parametrize(
    "user",
    [
        None,
        make_user(password=None),
    ],
)
def test_login_performs_dummy_verification_for_missing_or_passwordless_account(
    user,
    monkeypatch,
):
    db = FakeDB(user)
    verification_calls = []
    monkeypatch.setattr(
        AuthService,
        "verify_password",
        staticmethod(
            lambda password, password_hash: verification_calls.append(
                (password, password_hash)
            )
            or False
        ),
    )

    with pytest.raises(HTTPException) as exc_info:
        AuthService.login(
            db,
            SimpleNamespace(email="missing@example.com", password="wrong-password"),
        )

    assert exc_info.value.status_code == 401
    assert len(verification_calls) == 1
    assert verification_calls[0][0] == "wrong-password"
    assert verification_calls[0][1]


def test_token_auth_rejects_deactivated_user(monkeypatch):
    user = make_user(deactivated_at=datetime.now(timezone.utc))
    db = FakeDB(user)
    monkeypatch.setattr(
        AuthService,
        "verify_jwt_token",
        staticmethod(lambda token: str(user.id)),
    )

    with pytest.raises(HTTPException) as exc_info:
        AuthService.get_user_from_token(db, "token")

    assert exc_info.value.status_code == 403


def test_signup_sets_last_login_at_for_initial_session():
    db = FakeDB()

    result = AuthService.signup(
        db,
        SimpleNamespace(
            email="new@example.com",
            password="password",
            name="New User",
        ),
    )

    assert result.user.id == str(db.user.id)
    assert db.user.last_login_at is not None
    assert db.user.last_login_at.tzinfo is not None
    assert db.commit_count == 1
    assert {type(obj).__name__ for obj in db.objects} >= {
        "Organization",
        "Team",
        "TeamMembership",
    }
