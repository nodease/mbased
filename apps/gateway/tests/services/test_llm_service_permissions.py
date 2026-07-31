import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from apps.gateway.api.v1.endpoints import llm as llm_endpoint
from apps.gateway.services import llm_service
from apps.gateway.services.llm_service import (
    LLMCredentialNotAvailableError,
    LLMService,
)
from apps.shared.db.models.user import User
from apps.shared.schemas.llm import LLMCredentialCreate, LLMModelPricingUpdate
from apps.shared.services.retrieval_embedding_model_projection import (
    EmbeddingModelBinding,
)


class FakeQuery:
    def __init__(self, value):
        self.value = value

    def join(self, *args, **kwargs):
        return self

    def options(self, *args, **kwargs):
        return self

    def filter(self, *args, **kwargs):
        return self

    def order_by(self, *args, **kwargs):
        return self

    def first(self):
        return self.value

    def all(self):
        return self.value


class FakeDb:
    def __init__(self, value):
        self.value = value

    def query(self, *args, **kwargs):
        return FakeQuery(self.value)


def test_get_my_credentials_does_not_expose_internal_database_error(monkeypatch):
    leaked_detail = "SELECT llm_credentials.encryption_key_version"
    organization_id = uuid.uuid4()

    def fail_lookup(*_args, **_kwargs):
        raise RuntimeError(leaked_detail)

    monkeypatch.setattr(
        llm_endpoint,
        "resolve_active_organization_id",
        lambda *_args, **_kwargs: organization_id,
    )
    monkeypatch.setattr(LLMService, "get_user_credentials", fail_lookup)

    with pytest.raises(HTTPException) as exc_info:
        llm_endpoint.get_my_credentials(
            SimpleNamespace(),
            x_organization_id=str(organization_id),
            db=FakeDb([]),
            current_user=SimpleNamespace(id=uuid.uuid4()),
        )

    assert exc_info.value.status_code == 500
    assert exc_info.value.detail == "LLM credential lookup failed."
    assert leaked_detail not in str(exc_info.value.detail)


def test_get_my_credentials_resolves_active_organization(monkeypatch):
    user_id = uuid.uuid4()
    organization_id = uuid.uuid4()
    request = SimpleNamespace()
    db = object()
    expected = [SimpleNamespace(id=uuid.uuid4())]
    captured = {}

    def resolve_org(db_arg, request_arg, header, checked_user_id):
        captured["resolve"] = (db_arg, request_arg, header, checked_user_id)
        return organization_id

    def list_credentials(db_arg, checked_user_id, checked_organization_id):
        captured["service"] = (
            db_arg,
            checked_user_id,
            checked_organization_id,
        )
        return expected

    monkeypatch.setattr(llm_endpoint, "resolve_active_organization_id", resolve_org)
    monkeypatch.setattr(LLMService, "get_user_credentials", list_credentials)

    result = llm_endpoint.get_my_credentials(
        request,
        x_organization_id=str(organization_id),
        db=db,
        current_user=SimpleNamespace(id=user_id),
    )

    assert result == expected
    assert captured["resolve"] == (
        db,
        request,
        str(organization_id),
        user_id,
    )
    assert captured["service"] == (db, user_id, organization_id)


def test_get_client_for_user_rejects_non_object_credential_config(monkeypatch):
    user_id = uuid.uuid4()
    model = SimpleNamespace(
        id=uuid.uuid4(),
        name="Test Model",
        provider_id=uuid.uuid4(),
    )
    credential = SimpleNamespace(
        encrypted_config="[]",
        provider=SimpleNamespace(
            name="openai",
            base_url="https://catalog.example/v1",
        ),
    )
    db = FakeDb(model)
    monkeypatch.setattr(
        LLMService,
        "_get_valid_credential_for_user",
        lambda *_args, **_kwargs: credential,
    )
    monkeypatch.setattr(
        llm_service,
        "get_llm_client",
        lambda **_kwargs: pytest.fail("client must not be created"),
    )

    with pytest.raises(ValueError, match="Invalid credential config"):
        LLMService.get_client_for_user(
            db,
            user_id=user_id,
            model_id="test-model",
            organization_id=uuid.uuid4(),
        )


class FakeWizardRuntimeQuery:
    def __init__(self, db, model):
        self.db = db
        self.model = model
        self.filters = []

    def options(self, *args, **kwargs):
        return self

    def filter(self, *args, **kwargs):
        self.filters.extend(args)
        return self

    def order_by(self, *args, **kwargs):
        return self

    def all(self):
        if self.model is llm_service.LLMCredential:
            return self.db.credentials
        return []

    def first(self):
        if self.model is llm_service.LLMCredential:
            return self.db.first_credential(self.filters)
        if self.model is llm_service.LLMModel:
            return self.db.first_model(self.filters)
        if self.model is llm_service.LLMRelCredentialModel:
            return self.db.first_relation(self.filters)
        return None


class FakeWizardRuntimeDb:
    def __init__(self, credentials, model=None, relations=None, models=None):
        self.credentials = credentials
        self.models = list(models if models is not None else [model])
        self.relations = list(relations or [])

    def query(self, *args, **kwargs):
        return FakeWizardRuntimeQuery(self, args[0])

    def first_credential(self, filters):
        credential_id = _filter_value(filters, "id")
        organization_id = _filter_value(filters, "organization_id")
        is_valid = _filter_value(filters, "is_valid")
        return next(
            (
                credential
                for credential in self.credentials
                if credential.id == credential_id
                and credential.organization_id == organization_id
                and is_valid is True
            ),
            None,
        )

    def first_model(self, filters):
        model_db_id = _filter_value(filters, "id")
        provider_id = _filter_value(filters, "provider_id")
        model_id_for_api_call = _filter_value(filters, "model_id_for_api_call")
        for model in self.models:
            if (
                model
                and model.provider_id == provider_id
                and (
                    (model_db_id is not None and model.id == model_db_id)
                    or (
                        model_db_id is None
                        and model.model_id_for_api_call == model_id_for_api_call
                    )
                )
            ):
                return model
        return None

    def first_relation(self, filters):
        credential_id = _filter_value(filters, "credential_id")
        model_id = _filter_value(filters, "model_id")
        is_verified = _filter_value(filters, "is_verified")
        candidates = [
            relation
            for relation in self.relations
            if relation.credential_id == credential_id
            and relation.model_id == model_id
            and relation.is_verified == is_verified
        ]
        return sorted(candidates, key=lambda relation: relation.priority)[0] if candidates else None


def _filter_value(filters, column_key):
    for expression in filters:
        left = getattr(expression, "left", None)
        if getattr(left, "key", None) != column_key:
            continue
        right = getattr(expression, "right", None)
        if hasattr(right, "value"):
            return right.value
        if str(right).lower() == "true":
            return True
        if str(right).lower() == "false":
            return False
    return None


class FakeCredentialRegisterQuery:
    def __init__(self, db, model):
        self.db = db
        self.model = model

    def join(self, *args, **kwargs):
        return self

    def filter(self, *args, **kwargs):
        return self

    def order_by(self, *args, **kwargs):
        return self

    def first(self):
        if self.model is llm_service.LLMProvider:
            return self.db.provider
        if self.model is User:
            return SimpleNamespace(name="First User")
        return None


class FakeCredentialRegisterDb:
    def __init__(self, provider):
        self.provider = provider
        self.added = []
        self.flush_count = 0
        self.committed = False

    def query(self, *args, **kwargs):
        return FakeCredentialRegisterQuery(self, args[0])

    def add(self, row):
        self.added.append(row)

    def flush(self):
        self.flush_count += 1
        now = datetime.now(timezone.utc)
        for row in self.added:
            if getattr(row, "id", None) is None:
                row.id = uuid.uuid4()
            if getattr(row, "created_at", None) is None:
                row.created_at = now
            if getattr(row, "updated_at", None) is None:
                row.updated_at = now

    def commit(self):
        self.committed = True

    def refresh(self, row):
        self.refreshed = row


def test_delete_credential_preserves_permission_http_exception(monkeypatch):
    credential_id = uuid.uuid4()
    organization_id = uuid.uuid4()
    user = SimpleNamespace(id=uuid.uuid4())
    request = SimpleNamespace()
    seen = {}

    monkeypatch.setattr(
        llm_endpoint,
        "resolve_active_organization_id",
        lambda *_args, **_kwargs: organization_id,
    )

    def deny(
        db,
        current_user,
        checked_credential_id,
        action,
        *,
        active_organization_id=None,
    ):
        seen["action"] = action
        seen["organization_id"] = active_organization_id
        raise HTTPException(status_code=403, detail="Forbidden")

    monkeypatch.setattr(llm_endpoint, "ensure_llm_credential_permission", deny)
    monkeypatch.setattr(
        "apps.gateway.utils.audit.record_audit",
        lambda **kwargs: None,
    )

    with pytest.raises(HTTPException) as exc_info:
        llm_endpoint.delete_credential(
            credential_id,
            request,
            x_organization_id=str(organization_id),
            db=FakeDb(None),
            current_user=user,
        )

    assert exc_info.value.status_code == 403
    assert seen == {"action": "write", "organization_id": organization_id}


def test_sync_credential_models_uses_active_organization(monkeypatch):
    credential_id = uuid.uuid4()
    organization_id = uuid.uuid4()
    user = SimpleNamespace(id=uuid.uuid4())
    request = SimpleNamespace()
    db = object()
    captured = {}

    monkeypatch.setattr(
        llm_endpoint,
        "resolve_active_organization_id",
        lambda *_args, **_kwargs: organization_id,
    )

    def allow(
        db_arg,
        current_user,
        checked_credential_id,
        action,
        *,
        active_organization_id=None,
    ):
        captured["permission"] = (
            db_arg,
            current_user,
            checked_credential_id,
            action,
            active_organization_id,
        )
        return SimpleNamespace(id=credential_id)

    def sync(
        db_arg,
        user_id,
        checked_credential_id,
        checked_organization_id,
        *,
        purge_unverified=False,
    ):
        captured["service"] = (
            db_arg,
            user_id,
            checked_credential_id,
            checked_organization_id,
            purge_unverified,
        )
        return {"status": "ok"}

    monkeypatch.setattr(llm_endpoint, "ensure_llm_credential_permission", allow)
    monkeypatch.setattr(LLMService, "sync_credential_models", sync)

    result = llm_endpoint.sync_credential_models(
        credential_id,
        request,
        purge_unverified=True,
        x_organization_id=str(organization_id),
        db=db,
        current_user=user,
    )

    assert result == {"status": "ok"}
    assert captured["permission"] == (
        db,
        user,
        credential_id,
        "write",
        organization_id,
    )
    assert captured["service"] == (
        db,
        user.id,
        credential_id,
        organization_id,
        True,
    )


def _route(path, method):
    for route in llm_endpoint.router.routes:
        if route.path == path and method in route.methods:
            return route
    raise AssertionError(f"route not found: {method} {path}")


def _dependency_calls(route):
    return [dependency.call for dependency in route.dependant.dependencies]


def test_llm_catalog_and_pricing_routes_require_current_user():
    protected_routes = [
        ("/providers", "GET"),
        ("/agent-answer-options", "GET"),
        ("/models/sync-pricing", "POST"),
        ("/models/{model_id}/pricing", "PUT"),
    ]

    for path, method in protected_routes:
        route = _route(path, method)
        assert llm_endpoint.get_current_user in _dependency_calls(route)


def test_sync_system_pricing_requires_system_admin(monkeypatch):
    user = SimpleNamespace(id=uuid.uuid4())

    monkeypatch.setattr(
        llm_endpoint.TraceAccessService,
        "is_system_admin",
        staticmethod(lambda db, current_user: False),
    )
    monkeypatch.setattr(
        LLMService,
        "sync_system_prices",
        lambda *a, **k: pytest.fail("pricing sync should require system admin"),
    )

    with pytest.raises(HTTPException) as exc_info:
        llm_endpoint.sync_system_pricing(db=FakeDb(None), current_user=user)

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "system_admin_required"


def test_update_model_pricing_requires_system_admin(monkeypatch):
    user = SimpleNamespace(id=uuid.uuid4())
    pricing = LLMModelPricingUpdate(input_price_1k=0.1, output_price_1k=0.2)

    monkeypatch.setattr(
        llm_endpoint.TraceAccessService,
        "is_system_admin",
        staticmethod(lambda db, current_user: False),
    )
    monkeypatch.setattr(
        LLMService,
        "update_model_pricing",
        lambda *a, **k: pytest.fail("pricing update should require system admin"),
    )

    with pytest.raises(HTTPException) as exc_info:
        llm_endpoint.update_model_pricing(
            model_id=uuid.uuid4(),
            pricing=pricing,
            db=FakeDb(None),
            current_user=user,
        )

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "system_admin_required"


def test_register_credential_checks_organization_manager_before_remote_fetch(monkeypatch):
    organization_id = uuid.uuid4()
    provider = SimpleNamespace(id=uuid.uuid4(), name="openai", base_url="https://api.example")
    request = LLMCredentialCreate(
        provider_id=provider.id,
        organization_id=organization_id,
        credential_name="shared",
        api_key="sk-test",
    )

    monkeypatch.setattr(llm_service, "has_organization_manager_permission", lambda *a: False)
    monkeypatch.setattr(
        LLMService,
        "_fetch_remote_models",
        lambda *a, **k: pytest.fail("remote fetch should not run before permission check"),
    )

    with pytest.raises(PermissionError):
        LLMService.register_credential(
            FakeDb(provider), uuid.uuid4(), request, organization_id
        )


def test_register_credential_uses_explicit_active_organization_before_manager_check(
    monkeypatch,
):
    user_id = uuid.uuid4()
    provider = SimpleNamespace(
        id=uuid.uuid4(), name="openai", base_url="https://api.example"
    )
    organization_id = uuid.uuid4()
    request = LLMCredentialCreate(
        provider_id=provider.id,
        credential_name="first",
        api_key="sk-test",
    )
    db = FakeCredentialRegisterDb(provider)
    manager_check_flush_counts = []

    def has_manager_permission(db_arg, checked_user_id, checked_organization_id):
        manager_check_flush_counts.append(db_arg.flush_count)
        assert checked_user_id == user_id
        assert checked_organization_id == organization_id
        return True

    monkeypatch.setattr(
        llm_service,
        "has_organization_manager_permission",
        has_manager_permission,
    )
    protected_configs = []

    def protect_config(config):
        protected_configs.append(config)
        return SimpleNamespace(
            ciphertext="synthetic-ciphertext",
            key_version="v2",
            algorithm="fernet-v1",
        )

    monkeypatch.setattr(llm_service, "protect_llm_credential_config", protect_config)
    monkeypatch.setattr(LLMService, "_fetch_remote_models", lambda *a, **k: [])
    monkeypatch.setattr(LLMService, "_sync_models_to_db", lambda *a, **k: [])
    monkeypatch.setattr(
        llm_service.LLMCredentialResponse,
        "model_validate",
        staticmethod(lambda credential: credential),
    )

    credential = LLMService.register_credential(
        db, user_id, request, organization_id
    )

    assert manager_check_flush_counts == [0]
    assert credential.organization_id == organization_id
    assert credential.encrypted_config == "synthetic-ciphertext"
    assert credential.encryption_key_version == "v2"
    assert credential.encryption_algorithm == "fernet-v1"
    assert protected_configs == [
        {"apiKey": "sk-test", "baseUrl": "https://api.example"}
    ]
    assert db.committed is True


def test_provider_verification_does_not_expose_response_body(monkeypatch):
    sensitive_body = "provider payload with api_key=must-not-leak"
    monkeypatch.setattr(
        llm_service,
        "safe_http_request",
        lambda *args, **kwargs: SimpleNamespace(
            status_code=500,
            text=sensitive_body,
        ),
    )

    with pytest.raises(ValueError) as exc_info:
        LLMService._fetch_remote_models(
            "https://api.example",
            "synthetic-key",
            "openai",
        )

    assert "status_code=500" in str(exc_info.value)
    assert sensitive_body not in str(exc_info.value)


def test_provider_verification_does_not_expose_network_error(monkeypatch):
    sensitive_detail = "request failed with token=must-not-leak"

    def fail_request(*args, **kwargs):
        raise RuntimeError(sensitive_detail)

    monkeypatch.setattr(llm_service, "safe_http_request", fail_request)

    with pytest.raises(ValueError) as exc_info:
        LLMService._fetch_remote_models(
            "https://api.example",
            "synthetic-key",
            "openai",
        )

    assert "Network error verifying openai key" == str(exc_info.value)
    assert sensitive_detail not in str(exc_info.value)


def test_provider_verification_does_not_expose_json_error(monkeypatch):
    sensitive_detail = "invalid provider JSON with api_key=must-not-leak"

    class InvalidJsonResponse:
        status_code = 200

        def json(self):
            raise ValueError(sensitive_detail)

    monkeypatch.setattr(
        llm_service,
        "safe_http_request",
        lambda *args, **kwargs: InvalidJsonResponse(),
    )

    with pytest.raises(ValueError) as exc_info:
        LLMService._fetch_remote_models(
            "https://api.example",
            "synthetic-key",
            "openai",
        )

    assert str(exc_info.value) == "Invalid model response from openai"
    assert sensitive_detail not in str(exc_info.value)


def test_credential_registration_endpoint_redacts_unexpected_error(monkeypatch):
    sensitive_detail = "database failed with ciphertext=must-not-leak"
    organization_id = uuid.uuid4()
    credential_request = LLMCredentialCreate(
        provider_id=uuid.uuid4(),
        organization_id=organization_id,
        credential_name="shared",
        api_key="synthetic-key",
    )
    monkeypatch.setattr(
        llm_endpoint,
        "resolve_active_organization_id",
        lambda *_args, **_kwargs: organization_id,
    )

    def fail_registration(*args, **kwargs):
        raise RuntimeError(sensitive_detail)

    monkeypatch.setattr(LLMService, "register_credential", fail_registration)

    with pytest.raises(HTTPException) as exc_info:
        llm_endpoint.register_credential.__wrapped__(
            credential_request,
            SimpleNamespace(state=SimpleNamespace()),
            x_organization_id=str(organization_id),
            db=FakeDb(None),
            current_user=SimpleNamespace(id=uuid.uuid4()),
        )

    assert exc_info.value.status_code == 500
    assert exc_info.value.detail == "Credential registration failed"
    assert sensitive_detail not in str(exc_info.value.detail)


def test_credential_registration_rejects_body_organization_outside_active_context(
    monkeypatch,
):
    active_organization_id = uuid.uuid4()
    credential_request = LLMCredentialCreate(
        provider_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        credential_name="cross-organization",
        api_key="synthetic-key",
    )
    monkeypatch.setattr(
        llm_endpoint,
        "resolve_active_organization_id",
        lambda *_args, **_kwargs: active_organization_id,
    )
    monkeypatch.setattr(
        LLMService,
        "register_credential",
        lambda *_args, **_kwargs: pytest.fail(
            "cross-organization registration reached the service"
        ),
    )

    with pytest.raises(HTTPException) as exc_info:
        llm_endpoint.register_credential.__wrapped__(
            credential_request,
            SimpleNamespace(state=SimpleNamespace()),
            x_organization_id=str(active_organization_id),
            db=object(),
            current_user=SimpleNamespace(id=uuid.uuid4()),
        )

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail["error"]["code"] == "resource.not_found"


def test_get_user_credentials_filters_query_by_active_organization_and_read_permission(
    monkeypatch,
):
    user_id = uuid.uuid4()
    organization_id = uuid.uuid4()
    other_organization_id = uuid.uuid4()
    readable = SimpleNamespace(
        id=uuid.uuid4(), organization_id=organization_id, is_valid=True
    )
    blocked = SimpleNamespace(
        id=uuid.uuid4(), organization_id=organization_id, is_valid=True
    )
    cross_organization = SimpleNamespace(
        id=uuid.uuid4(), organization_id=other_organization_id, is_valid=True
    )
    revoked = SimpleNamespace(
        id=uuid.uuid4(), organization_id=organization_id, is_valid=False
    )
    credentials = [readable, blocked, cross_organization, revoked]
    permission_calls = []

    class FilterAwareCredentialQuery:
        def __init__(self):
            self.filters = []

        def filter(self, *criteria):
            self.filters.extend(criteria)
            return self

        def all(self):
            filtered_organization_id = _filter_value(
                self.filters, "organization_id"
            )
            filtered_validity = _filter_value(self.filters, "is_valid")
            return [
                credential
                for credential in credentials
                if (
                    filtered_organization_id is None
                    or credential.organization_id == filtered_organization_id
                )
                and (
                    filtered_validity is None
                    or credential.is_valid is filtered_validity
                )
            ]

    class FilterAwareCredentialDb:
        def query(self, model):
            assert model is llm_service.LLMCredential
            return FilterAwareCredentialQuery()

    def can_read(
        db,
        checked_user_id,
        credential_id,
        action,
        organization_id=None,
    ):
        permission_calls.append(
            (checked_user_id, credential_id, action, organization_id)
        )
        return credential_id in {readable.id, cross_organization.id}

    monkeypatch.setattr(llm_service, "has_llm_credential_permission", can_read)
    monkeypatch.setattr(
        llm_service.LLMCredentialResponse,
        "model_validate",
        staticmethod(lambda credential: credential),
    )

    result = LLMService.get_user_credentials(
        FilterAwareCredentialDb(), user_id, organization_id
    )

    assert result == [readable]
    assert permission_calls == [
        (user_id, readable.id, "read", organization_id),
        (user_id, blocked.id, "read", organization_id),
    ]


def test_get_client_for_model_uses_model_db_id(monkeypatch):
    user_id = uuid.uuid4()
    organization_id = uuid.uuid4()
    model = SimpleNamespace(
        id=uuid.uuid4(),
        is_active=True,
        model_id_for_api_call="gpt-test",
    )
    credential = SimpleNamespace(
        id=uuid.uuid4(),
        encrypted_config='{"apiKey":"redacted-test-key","baseUrl":"https://example.test"}',
        provider=SimpleNamespace(
            name="openai",
            base_url="https://catalog.example/v1",
        ),
    )
    captured = {}

    class FakeDb:
        def refresh(self, obj):
            captured["refreshed"] = obj

    def fake_get_valid_credential(
        db,
        user_id,
        provider_id=None,
        model_db_id=None,
        organization_id=None,
    ):
        captured["user_id"] = user_id
        captured["model_db_id"] = model_db_id
        captured["organization_id"] = organization_id
        return credential

    def fake_get_llm_client(provider, model_id, credentials):
        captured["provider"] = provider
        captured["model_id"] = model_id
        captured["has_api_key"] = bool(credentials.get("apiKey"))
        return SimpleNamespace(provider=provider, model_id=model_id)

    monkeypatch.setattr(
        LLMService,
        "_get_valid_credential_for_user",
        staticmethod(fake_get_valid_credential),
    )
    monkeypatch.setattr(llm_service, "get_llm_client", fake_get_llm_client)

    client = LLMService.get_client_for_model(
        FakeDb(),
        user_id,
        model,
        organization_id=organization_id,
    )

    assert client.model_id == "gpt-test"
    assert captured["model_db_id"] == model.id
    assert captured["organization_id"] == organization_id
    assert captured["refreshed"] is credential
    assert captured["has_api_key"] is True


def test_get_client_for_model_binding_uses_preloaded_model_identity(monkeypatch):
    binding = EmbeddingModelBinding(
        model_id=uuid.uuid4(),
        provider_id=uuid.uuid4(),
        model_identifier="text-embedding-test",
    )
    organization_id = uuid.uuid4()
    user_id = uuid.uuid4()
    captured = {}
    expected_client = object()

    def fake_get_client(
        db,
        *,
        user_id,
        model_db_id,
        model_identifier,
        organization_id,
    ):
        captured.update(
            {
                "db": db,
                "user_id": user_id,
                "model_db_id": model_db_id,
                "model_identifier": model_identifier,
                "organization_id": organization_id,
            }
        )
        return expected_client

    monkeypatch.setattr(
        LLMService,
        "_get_client_for_resolved_model",
        staticmethod(fake_get_client),
    )
    db = object()

    client = LLMService.get_client_for_model_binding(
        db,
        user_id,
        binding,
        organization_id=organization_id,
    )

    assert client is expected_client
    assert captured == {
        "db": db,
        "user_id": user_id,
        "model_db_id": binding.model_id,
        "model_identifier": binding.model_identifier,
        "organization_id": organization_id,
    }


def test_get_my_available_models_filters_by_credential_use_permission(monkeypatch):
    allowed_credential_id = uuid.uuid4()
    blocked_credential_id = uuid.uuid4()
    shared_model = SimpleNamespace(id=uuid.uuid4(), name="Shared")
    blocked_model = SimpleNamespace(id=uuid.uuid4(), name="Blocked")
    rows = [
        (shared_model, blocked_credential_id),
        (shared_model, allowed_credential_id),
        (blocked_model, blocked_credential_id),
    ]
    seen_actions = []

    def can_use(db, user_id, credential_id, action):
        seen_actions.append(action)
        return credential_id == allowed_credential_id and action == "use"

    monkeypatch.setattr(llm_service, "has_llm_credential_permission", can_use)
    monkeypatch.setattr(
        llm_service.LLMModelResponse,
        "model_validate",
        staticmethod(lambda model: model),
    )

    result = LLMService.get_my_available_models(FakeDb(rows), uuid.uuid4())

    assert result == [shared_model]
    assert seen_actions == ["use", "use", "use"]


def test_get_agent_answer_options_returns_only_verified_usable_pairs(monkeypatch):
    user_id = uuid.uuid4()
    organization_id = uuid.uuid4()
    allowed_credential = SimpleNamespace(
        id=uuid.uuid4(),
        credential_name="agent",
        organization_id=organization_id,
    )
    blocked_credential = SimpleNamespace(
        id=uuid.uuid4(),
        credential_name="blocked",
        organization_id=organization_id,
    )
    model = SimpleNamespace(id=uuid.uuid4(), name="GPT Test", provider_name="openai")
    rows = [
        (model, blocked_credential, 0),
        (model, allowed_credential, 1),
    ]
    seen = []

    def can_use(db, checked_user_id, credential_id, action, organization_id=None):
        seen.append((checked_user_id, credential_id, action, organization_id))
        return credential_id == allowed_credential.id and action == "use"

    monkeypatch.setattr(llm_service, "has_llm_credential_permission", can_use)
    monkeypatch.setattr(
        llm_service.LLMModelResponse,
        "model_validate",
        staticmethod(lambda value: value),
    )
    monkeypatch.setattr(
        llm_service.LLMCredentialOptionResponse,
        "model_validate",
        staticmethod(lambda value: value),
    )
    monkeypatch.setattr(
        llm_service,
        "LLMCredentialModelOptionResponse",
        lambda **kwargs: kwargs,
    )

    result = LLMService.get_agent_answer_options(
        FakeDb(rows), user_id, organization_id
    )

    assert result == [
        {
            "model": model,
            "credential": allowed_credential,
            "provider_name": "openai",
            "relation_priority": 1,
        }
    ]
    assert seen == [
        (user_id, blocked_credential.id, "use", organization_id),
        (user_id, allowed_credential.id, "use", organization_id),
    ]


def test_get_agent_answer_options_returns_safe_credential_option_schema(monkeypatch):
    user_id = uuid.uuid4()
    organization_id = uuid.uuid4()
    credential = SimpleNamespace(
        id=uuid.uuid4(),
        provider_id=uuid.uuid4(),
        user_id=user_id,
        organization_id=organization_id,
        credential_name="agent",
        config_preview="sk-****",
        is_valid=True,
        quota_type="monthly",
        quota_limit=1000,
        quota_used=100,
        encrypted_config="synthetic-secret-placeholder",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    model = SimpleNamespace(
        id=uuid.uuid4(),
        model_id_for_api_call="gpt-test",
        name="GPT Test",
        type="chat",
        provider_name="openai",
        context_window=8192,
        input_price_1k=None,
        output_price_1k=None,
        is_active=True,
        model_metadata=None,
    )

    monkeypatch.setattr(
        llm_service,
        "has_llm_credential_permission",
        lambda *args, **kwargs: True,
    )

    result = LLMService.get_agent_answer_options(
        FakeDb([(model, credential, 0)]), user_id, organization_id
    )

    assert len(result) == 1
    option = result[0].model_dump(mode="json")
    assert option["credential"] == {
        "id": str(credential.id),
        "provider_id": str(credential.provider_id),
        "organization_id": str(organization_id),
        "credential_name": "agent",
        "config_preview": "sk-****",
        "is_valid": True,
    }
    assert "user_id" not in option["credential"]
    assert "quota_type" not in option["credential"]
    assert "quota_limit" not in option["credential"]
    assert "quota_used" not in option["credential"]
    assert "encrypted_config" not in option["credential"]
    assert "created_at" not in option["credential"]
    assert "updated_at" not in option["credential"]


def test_agent_builder_model_options_use_unified_recommendation_order(
    monkeypatch,
):
    user_id = uuid.uuid4()
    organization_id = uuid.uuid4()

    def option(provider_name, model_id, name, credential_name, priority=0):
        provider_id = uuid.uuid4()
        return (
            SimpleNamespace(
                id=uuid.uuid4(),
                provider_id=provider_id,
                model_id_for_api_call=model_id,
                name=name,
                type="chat",
                provider_name=provider_name,
                context_window=8192,
                input_price_1k=None,
                output_price_1k=None,
                is_active=True,
                model_metadata=None,
            ),
            SimpleNamespace(
                id=uuid.uuid4(),
                provider_id=provider_id,
                organization_id=organization_id,
                credential_name=credential_name,
                config_preview=None,
                is_valid=True,
            ),
            priority,
        )

    rows = [
        option("google", "gemini-3.1-pro-preview", "Gemini 3.1 Pro", "google"),
        option("openai", "gpt-5.4-mini", "GPT-5.4 Mini", "openai"),
        option("anthropic", "claude-opus-4-8", "Claude Opus 4.8", "anthropic"),
        option("openai", "gpt-5.5", "GPT-5.5", "openai"),
        option("google", "gemini-3.5-flash", "Gemini 3.5 Flash", "google"),
        option("anthropic", "claude-sonnet-5", "Claude Sonnet 5", "anthropic"),
        option("openai", "gpt-5.5-pro", "GPT-5.5 Pro", "openai"),
        option("openai", "gpt-5.3-codex", "GPT-5.3 Codex", "openai"),
        option("openai", "gpt-custom", "GPT Custom", "openai"),
        option(
            "openai",
            "models/gpt-5.5",
            "GPT-5.5 Prefixed Alias",
            "openai",
        ),
        option("openai", "o4-mini", "o4 Mini", "openai"),
    ]
    monkeypatch.setattr(
        llm_service,
        "has_llm_credential_permission",
        lambda *args, **kwargs: True,
    )
    monkeypatch.setattr(
        llm_service.LLMModelResponse,
        "model_validate",
        staticmethod(lambda value: value),
    )
    monkeypatch.setattr(
        llm_service.LLMCredentialOptionResponse,
        "model_validate",
        staticmethod(lambda value: value),
    )
    monkeypatch.setattr(
        llm_service,
        "LLMCredentialModelOptionResponse",
        lambda **kwargs: SimpleNamespace(**kwargs),
    )
    monkeypatch.setattr(
        llm_service,
        "LLMIntentModelProviderResponse",
        lambda **kwargs: SimpleNamespace(**kwargs),
        raising=False,
    )

    groups = LLMService.get_agent_builder_model_option_groups(
        FakeDb(rows), user_id, organization_id
    )

    assert [group.provider_name for group in groups] == [
        "openai",
        "anthropic",
        "google",
        "llamaparse",
    ]
    assert [
        item.model.model_id_for_api_call for item in groups[0].options
    ] == [
        "gpt-5.5",
        "models/gpt-5.5",
        "gpt-5.5-pro",
        "gpt-5.4-mini",
        "o4-mini",
        "gpt-custom",
    ]
    assert [
        item.model.model_id_for_api_call for item in groups[1].options
    ] == ["claude-sonnet-5", "claude-opus-4-8"]
    assert [
        item.model.model_id_for_api_call for item in groups[2].options
    ] == ["gemini-3.5-flash", "gemini-3.1-pro-preview"]
    assert groups[3].options == []
    assert groups[3].unavailable_reason == "chat_model_not_supported"

    fallback_rows = [
        row for row in rows if row[0].model_id_for_api_call != "gpt-5.5"
    ]
    fallback_groups = LLMService.get_agent_builder_model_option_groups(
        FakeDb(fallback_rows), user_id, organization_id
    )
    assert [
        item.model.model_id_for_api_call for item in fallback_groups[0].options
    ] == [
        "models/gpt-5.5",
        "gpt-5.5-pro",
        "gpt-5.4-mini",
        "o4-mini",
        "gpt-custom",
    ]


def test_agent_builder_options_collapse_duplicate_relations_to_best_priority(
    monkeypatch,
):
    model_id = uuid.uuid4()
    credential_id = uuid.uuid4()

    def option(priority):
        return SimpleNamespace(
            provider_name="openai",
            model=SimpleNamespace(
                id=model_id,
                model_id_for_api_call="gpt-5.5",
                name="GPT-5.5",
            ),
            credential=SimpleNamespace(
                id=credential_id,
                credential_name="openai-credential",
            ),
            relation_priority=priority,
        )

    options = [option(0), option(9)]
    monkeypatch.setattr(
        LLMService,
        "get_agent_answer_options",
        lambda *args, **kwargs: options,
    )
    monkeypatch.setattr(
        llm_service,
        "LLMIntentModelProviderResponse",
        lambda **kwargs: SimpleNamespace(**kwargs),
    )

    groups = LLMService.get_agent_builder_model_option_groups(
        object(),
        uuid.uuid4(),
        uuid.uuid4(),
    )
    recommendation = LLMService.get_agent_builder_draft_model_recommendation(
        object(),
        uuid.uuid4(),
        uuid.uuid4(),
    )

    assert len(groups[0].options) == 1
    assert groups[0].options[0].relation_priority == 0
    assert recommendation is not None
    assert recommendation.relation_priority == 0


def test_agent_builder_draft_model_recommendation_prefers_openai_gpt_5_5(
    monkeypatch,
):
    user_id = uuid.uuid4()
    organization_id = uuid.uuid4()

    def option(provider_name, model_id, name, priority=0):
        return SimpleNamespace(
            provider_name=provider_name,
            model=SimpleNamespace(
                id=uuid.uuid4(),
                model_id_for_api_call=model_id,
                name=name,
            ),
            credential=SimpleNamespace(
                id=uuid.uuid4(),
                credential_name=f"{provider_name}-credential",
            ),
            relation_priority=priority,
        )

    options = [
        option("anthropic", "claude-haiku-5", "Claude Haiku 5"),
        option("openai", "gpt-5.5-pro", "GPT-5.5 Pro"),
        option("openai", "gpt-5.5", "GPT-5.5"),
        option("openai", "gpt-5.5-nano", "GPT-5.5 Nano"),
        option("openai", "gpt-5.4-mini", "GPT-5.4 Mini"),
        option("openai", "gpt-5.5-mini", "GPT-5.5 Mini"),
    ]
    monkeypatch.setattr(
        LLMService,
        "get_agent_answer_options",
        lambda *args, **kwargs: options,
    )

    recommendation = LLMService.get_agent_builder_draft_model_recommendation(
        object(),
        user_id,
        organization_id,
    )

    assert recommendation is not None
    assert recommendation.model.model_id_for_api_call == "gpt-5.5"


def test_agent_builder_draft_model_recommendation_uses_nano_when_mini_is_absent(
    monkeypatch,
):
    user_id = uuid.uuid4()
    organization_id = uuid.uuid4()
    options = [
        SimpleNamespace(
            provider_name="openai",
            model=SimpleNamespace(
                id=uuid.uuid4(),
                model_id_for_api_call=model_id,
                name=model_id,
            ),
            credential=SimpleNamespace(
                id=uuid.uuid4(),
                credential_name="openai-credential",
            ),
            relation_priority=0,
        )
        for model_id in ("gpt-5.5-pro", "gpt-5.5-nano")
    ]
    monkeypatch.setattr(
        LLMService,
        "get_agent_answer_options",
        lambda *args, **kwargs: options,
    )

    recommendation = LLMService.get_agent_builder_draft_model_recommendation(
        object(),
        user_id,
        organization_id,
    )

    assert recommendation is not None
    assert recommendation.model.model_id_for_api_call == "gpt-5.5-nano"


def test_agent_builder_draft_model_recommendation_normalizes_aliases_and_snapshots(
    monkeypatch,
):
    user_id = uuid.uuid4()
    organization_id = uuid.uuid4()

    def option(provider_name, model_id):
        return SimpleNamespace(
            provider_name=provider_name,
            model=SimpleNamespace(
                id=uuid.uuid4(),
                model_id_for_api_call=model_id,
                name=model_id,
            ),
            credential=SimpleNamespace(
                id=uuid.uuid4(),
                credential_name=f"{provider_name}-credential",
            ),
            relation_priority=0,
        )

    options = [
        option("anthropic", "gpt-5.5"),
        option("openai", "models/gpt-5.5"),
        option("openai", "gpt-5.5-2026-07-01"),
        option("openai", "gpt-5.5-mini"),
    ]
    monkeypatch.setattr(
        LLMService,
        "get_agent_answer_options",
        lambda *args, **kwargs: options,
    )

    recommendation = LLMService.get_agent_builder_draft_model_recommendation(
        object(),
        user_id,
        organization_id,
    )

    assert recommendation is not None
    assert recommendation.provider_name == "openai"
    assert recommendation.model.model_id_for_api_call == "models/gpt-5.5"


def test_agent_builder_draft_model_recommendation_returns_none_without_authorized_option(
    monkeypatch,
):
    monkeypatch.setattr(
        LLMService,
        "get_agent_answer_options",
        lambda *args, **kwargs: [],
    )

    recommendation = LLMService.get_agent_builder_draft_model_recommendation(
        object(),
        uuid.uuid4(),
        uuid.uuid4(),
    )

    assert recommendation is None


def test_agent_answer_options_endpoint_resolves_active_organization(monkeypatch):
    user_id = uuid.uuid4()
    organization_id = uuid.uuid4()
    request = SimpleNamespace()
    db = object()
    captured = {}
    expected = [{"model": "model", "credential": "credential"}]

    def resolve_org(db_arg, request_arg, header, checked_user_id):
        captured["resolve_args"] = (db_arg, request_arg, header, checked_user_id)
        return organization_id

    def get_options(db_arg, checked_user_id, checked_org_id):
        captured["args"] = (db_arg, checked_user_id, checked_org_id)
        return expected

    monkeypatch.setattr(llm_endpoint, "resolve_active_organization_id", resolve_org)
    monkeypatch.setattr(LLMService, "get_agent_answer_options", get_options)

    result = llm_endpoint.get_agent_answer_options(
        request,
        x_organization_id=str(organization_id),
        db=db,
        current_user=SimpleNamespace(id=user_id),
    )

    assert result == expected
    assert captured["resolve_args"] == (
        db,
        request,
        str(organization_id),
        user_id,
    )
    assert captured["args"] == (db, user_id, organization_id)


def test_agent_answer_options_endpoint_does_not_mask_scope_errors(monkeypatch):
    user_id = uuid.uuid4()
    db = object()

    def reject_scope(*args, **kwargs):
        raise HTTPException(
            status_code=404,
            detail={"error": {"code": "resource.not_found"}},
        )

    monkeypatch.setattr(llm_endpoint, "resolve_active_organization_id", reject_scope)
    monkeypatch.setattr(
        LLMService,
        "get_agent_answer_options",
        lambda *args, **kwargs: pytest.fail("service should not run after org error"),
    )

    with pytest.raises(HTTPException) as exc_info:
        llm_endpoint.get_agent_answer_options(
            SimpleNamespace(),
            x_organization_id=str(uuid.uuid4()),
            db=db,
            current_user=SimpleNamespace(id=user_id),
        )

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail["error"]["code"] == "resource.not_found"


def test_agent_answer_options_endpoint_sanitizes_unexpected_errors(monkeypatch):
    user_id = uuid.uuid4()
    organization_id = uuid.uuid4()
    db = object()

    monkeypatch.setattr(
        llm_endpoint,
        "resolve_active_organization_id",
        lambda *args, **kwargs: organization_id,
    )
    monkeypatch.setattr(
        LLMService,
        "get_agent_answer_options",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            RuntimeError("credential secret leaked")
        ),
    )

    with pytest.raises(HTTPException) as exc_info:
        llm_endpoint.get_agent_answer_options(
            SimpleNamespace(),
            x_organization_id=str(organization_id),
            db=db,
            current_user=SimpleNamespace(id=user_id),
        )

    assert exc_info.value.status_code == 500
    assert exc_info.value.detail == "Agent answer options lookup failed."
    assert "secret" not in exc_info.value.detail


def test_wizard_runtime_uses_relation_priority_before_credential_created_at(
    monkeypatch,
):
    user_id = uuid.uuid4()
    organization_id = uuid.uuid4()
    provider = SimpleNamespace(
        id=uuid.uuid4(),
        name="openai",
        base_url="https://catalog.example/v1",
    )
    older_credential = SimpleNamespace(
        id=uuid.uuid4(),
        provider=provider,
        provider_id=provider.id,
        organization_id=organization_id,
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        encrypted_config='{"apiKey": "older-key", "baseUrl": "https://older.example"}',
    )
    priority_credential = SimpleNamespace(
        id=uuid.uuid4(),
        provider=provider,
        provider_id=provider.id,
        organization_id=organization_id,
        created_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
        encrypted_config='{"apiKey": "priority-key", "baseUrl": "https://priority.example"}',
    )
    model = SimpleNamespace(
        id=uuid.uuid4(),
        provider_id=provider.id,
        model_id_for_api_call="gpt-4o-mini",
        is_active=True,
    )
    db = FakeWizardRuntimeDb(
        credentials=[older_credential, priority_credential],
        model=model,
        relations=[
            SimpleNamespace(
                credential_id=older_credential.id,
                model_id=model.id,
                is_verified=True,
                priority=10,
            ),
            SimpleNamespace(
                credential_id=priority_credential.id,
                model_id=model.id,
                is_verified=True,
                priority=1,
            ),
        ],
    )
    client_configs = []

    monkeypatch.setattr(
        llm_service,
        "has_llm_credential_permission",
        lambda *args, **kwargs: True,
    )
    monkeypatch.setattr(
        llm_service,
        "get_llm_client",
        lambda **kwargs: client_configs.append(kwargs["credentials"])
        or SimpleNamespace(),
    )

    runtime = LLMService.get_wizard_client_for_user(
        db,
        user_id,
        {"openai": "gpt-4o-mini"},
        organization_id=organization_id,
        audit_on_failure=False,
    )

    assert runtime.credential_id == priority_credential.id
    assert client_configs == [
        {"apiKey": "priority-key", "baseUrl": "https://catalog.example/v1"}
    ]


def test_wizard_runtime_uses_provider_model_map_order_before_relation_priority(
    monkeypatch,
):
    user_id = uuid.uuid4()
    organization_id = uuid.uuid4()
    openai_provider = SimpleNamespace(
        id=uuid.uuid4(),
        name="openai",
        base_url="https://openai-catalog.example/v1",
    )
    anthropic_provider = SimpleNamespace(
        id=uuid.uuid4(),
        name="anthropic",
        base_url="https://anthropic-catalog.example/v1",
    )
    openai_credential = SimpleNamespace(
        id=uuid.uuid4(),
        provider=openai_provider,
        provider_id=openai_provider.id,
        organization_id=organization_id,
        created_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
        encrypted_config='{"apiKey": "openai-key", "baseUrl": "https://openai.example"}',
    )
    anthropic_credential = SimpleNamespace(
        id=uuid.uuid4(),
        provider=anthropic_provider,
        provider_id=anthropic_provider.id,
        organization_id=organization_id,
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        encrypted_config='{"apiKey": "anthropic-key", "baseUrl": "https://anthropic.example"}',
    )
    openai_model = SimpleNamespace(
        id=uuid.uuid4(),
        provider_id=openai_provider.id,
        model_id_for_api_call="gpt-4o-mini",
        is_active=True,
    )
    anthropic_model = SimpleNamespace(
        id=uuid.uuid4(),
        provider_id=anthropic_provider.id,
        model_id_for_api_call="claude-haiku-4-5-20251001",
        is_active=True,
    )
    db = FakeWizardRuntimeDb(
        credentials=[anthropic_credential, openai_credential],
        models=[openai_model, anthropic_model],
        relations=[
            SimpleNamespace(
                credential_id=openai_credential.id,
                model_id=openai_model.id,
                is_verified=True,
                priority=10,
            ),
            SimpleNamespace(
                credential_id=anthropic_credential.id,
                model_id=anthropic_model.id,
                is_verified=True,
                priority=1,
            ),
        ],
    )
    client_configs = []

    monkeypatch.setattr(
        llm_service,
        "has_llm_credential_permission",
        lambda *args, **kwargs: True,
    )
    monkeypatch.setattr(
        llm_service,
        "get_llm_client",
        lambda **kwargs: client_configs.append(kwargs["credentials"])
        or SimpleNamespace(),
    )

    runtime = LLMService.get_wizard_client_for_user(
        db,
        user_id,
        {
            "openai": "gpt-4o-mini",
            "anthropic": "claude-haiku-4-5-20251001",
        },
        organization_id=organization_id,
        audit_on_failure=False,
    )

    assert runtime.credential_id == openai_credential.id
    assert client_configs == [
        {"apiKey": "openai-key", "baseUrl": "https://openai-catalog.example/v1"}
    ]


def test_agent_builder_runtime_revalidates_explicit_credential_model_selection(
    monkeypatch,
):
    user_id = uuid.uuid4()
    organization_id = uuid.uuid4()
    provider = SimpleNamespace(
        id=uuid.uuid4(),
        name="openai",
        base_url="https://catalog.example/v1",
    )
    credential = SimpleNamespace(
        id=uuid.uuid4(),
        provider=provider,
        provider_id=provider.id,
        organization_id=organization_id,
        encrypted_config='{"apiKey": "selected-key", "baseUrl": "https://example.com"}',
    )
    model = SimpleNamespace(
        id=uuid.uuid4(),
        provider_id=provider.id,
        model_id_for_api_call="gpt-5.5-pro",
        is_active=True,
        type="chat",
    )
    relation = SimpleNamespace(
        credential_id=credential.id,
        model_id=model.id,
        is_verified=True,
        priority=0,
    )
    db = FakeWizardRuntimeDb(
        credentials=[credential],
        model=model,
        relations=[relation],
    )
    permission_calls = []
    client_calls = []
    monkeypatch.setattr(
        llm_service,
        "has_llm_credential_permission",
        lambda *args, **kwargs: permission_calls.append((args, kwargs)) or True,
    )
    monkeypatch.setattr(
        llm_service,
        "get_llm_client",
        lambda **kwargs: client_calls.append(kwargs) or SimpleNamespace(),
    )

    runtime = LLMService.get_wizard_client_for_selection(
        db,
        user_id=user_id,
        credential_id=credential.id,
        model_id=model.id,
        organization_id=organization_id,
        runtime_surface="agent_builder_intent",
        audit_on_failure=False,
    )

    assert runtime.credential_id == credential.id
    assert runtime.model_id == "gpt-5.5-pro"
    assert permission_calls[0][0][:4] == (
        db,
        user_id,
        credential.id,
        "use",
    )
    assert permission_calls[0][1]["organization_id"] == organization_id
    assert client_calls == [
        {
            "provider": "openai",
            "model_id": "gpt-5.5-pro",
            "credentials": {
                "apiKey": "selected-key",
                "baseUrl": "https://catalog.example/v1",
            },
        }
    ]


def test_agent_builder_runtime_blocks_explicit_selection_without_use_permission(
    monkeypatch,
):
    user_id = uuid.uuid4()
    organization_id = uuid.uuid4()
    provider = SimpleNamespace(id=uuid.uuid4(), name="openai")
    credential = SimpleNamespace(
        id=uuid.uuid4(),
        provider=provider,
        provider_id=provider.id,
        organization_id=organization_id,
        encrypted_config='{"apiKey": "never-used"}',
    )
    model = SimpleNamespace(
        id=uuid.uuid4(),
        provider_id=provider.id,
        model_id_for_api_call="gpt-5.5",
        is_active=True,
        type="chat",
    )
    relation = SimpleNamespace(
        credential_id=credential.id,
        model_id=model.id,
        is_verified=True,
        priority=0,
    )
    db = FakeWizardRuntimeDb(
        credentials=[credential],
        model=model,
        relations=[relation],
    )
    monkeypatch.setattr(
        llm_service,
        "has_llm_credential_permission",
        lambda *args, **kwargs: False,
    )
    monkeypatch.setattr(
        llm_service,
        "get_llm_client",
        lambda **kwargs: pytest.fail("denied selection created an LLM client"),
    )

    with pytest.raises(LLMCredentialNotAvailableError) as exc:
        LLMService.get_wizard_client_for_selection(
            db,
            user_id=user_id,
            credential_id=credential.id,
            model_id=model.id,
            organization_id=organization_id,
            runtime_surface="agent_builder_intent",
            audit_on_failure=False,
        )

    assert exc.value.reason == "credential_use_denied"


def test_wizard_runtime_block_uses_unknown_target_without_credential(monkeypatch):
    user_id = uuid.uuid4()
    organization_id = uuid.uuid4()
    audit_calls = []

    monkeypatch.setattr(
        llm_service,
        "record_resource_permission_denied",
        lambda **kwargs: audit_calls.append(kwargs),
    )

    error = LLMCredentialNotAvailableError(
        "credential_not_available",
        "missing",
        model_id="gpt-4o-mini",
        organization_id=organization_id,
    )

    LLMService._record_wizard_runtime_block(  # noqa: SLF001 - MBA-43 audit helper
        user_id,
        error,
        "prompt_wizard",
    )

    assert audit_calls[0]["resource_type"] == "llm_credential"
    assert audit_calls[0]["resource_id"] == "unknown"
    assert audit_calls[0]["organization_id"] == organization_id
    assert audit_calls[0]["metadata"]["credential_id"] is None
    assert audit_calls[0]["metadata"]["model_id"] == "gpt-4o-mini"
    assert audit_calls[0]["metadata"]["reason"] == "credential_not_available"


def test_wizard_runtime_relation_missing_uses_unknown_target(monkeypatch):
    """Wizard relation-missing runtime blocks keep the audit target unknown. MBA-43"""
    user_id = uuid.uuid4()
    organization_id = uuid.uuid4()
    provider = SimpleNamespace(id=uuid.uuid4(), name="openai")
    credential = SimpleNamespace(
        id=uuid.uuid4(),
        provider=provider,
        provider_id=provider.id,
        organization_id=organization_id,
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        encrypted_config='{"apiKey": "key", "baseUrl": "https://example.com"}',
    )
    model = SimpleNamespace(
        id=uuid.uuid4(),
        provider_id=provider.id,
        model_id_for_api_call="gpt-4o-mini",
        is_active=True,
    )
    db = FakeWizardRuntimeDb(credentials=[credential], model=model, relations=[])
    audit_calls = []

    monkeypatch.setattr(
        llm_service,
        "record_resource_permission_denied",
        lambda **kwargs: audit_calls.append(kwargs),
    )

    with pytest.raises(LLMCredentialNotAvailableError) as exc:
        LLMService.get_wizard_client_for_user(
            db,
            user_id,
            {"openai": "gpt-4o-mini"},
            organization_id=organization_id,
            runtime_surface="prompt_wizard",
        )

    assert exc.value.reason == "model_relation_not_verified"
    assert exc.value.credential_id is None
    assert audit_calls[0]["resource_type"] == "llm_credential"
    assert audit_calls[0]["resource_id"] == "unknown"
    assert audit_calls[0]["organization_id"] == organization_id
    assert audit_calls[0]["metadata"]["credential_id"] is None
    assert audit_calls[0]["metadata"]["model_id"] == "gpt-4o-mini"
    assert audit_calls[0]["metadata"]["reason"] == "model_relation_not_verified"
