from types import SimpleNamespace
from uuid import uuid4

import pytest

from apps.gateway.services.llm_service import LLMService as GatewayLLMService
from apps.shared.db.models.llm import LLMModel
from apps.shared.db.models.workflow import Workflow
from apps.shared.db.models.workflow_run import WorkflowRun
from apps.workflow_engine.services.llm_service import LLMService as EngineLLMService


class FakeQuery:
    def __init__(self, value):
        self.value = value

    def filter(self, *args, **kwargs):
        return self

    def first(self):
        return self.value


class FakeDb:
    def __init__(self, *, model=None, run=None, workflow=None):
        self.model = model
        self.run = run
        self.workflow = workflow
        self.added = []
        self.committed = False

    def query(self, model_cls):
        if model_cls is LLMModel:
            return FakeQuery(self.model)
        if model_cls is WorkflowRun:
            return FakeQuery(self.run)
        if model_cls is Workflow:
            return FakeQuery(self.workflow)
        return FakeQuery(None)

    def add(self, row):
        self.added.append(row)

    def commit(self):
        self.committed = True

    def refresh(self, row):
        return None


@pytest.mark.parametrize("service_cls", [GatewayLLMService, EngineLLMService])
def test_log_usage_skips_invalid_explicit_workflow_id(monkeypatch, service_cls):
    user_id = uuid4()
    model_id = uuid4()
    credential_called = False
    db = FakeDb(
        model=SimpleNamespace(id=model_id, model_id_for_api_call="gpt-test"),
    )

    def get_credential(*args, **kwargs):
        nonlocal credential_called
        credential_called = True
        return SimpleNamespace(id=uuid4())

    monkeypatch.setattr(
        service_cls,
        "_get_valid_credential_for_user",
        staticmethod(get_credential),
    )

    result = service_cls.log_usage(
        db,
        user_id=user_id,
        model_id="gpt-test",
        usage={"prompt_tokens": 1, "completion_tokens": 2},
        cost=0.1,
        workflow_id="not-a-uuid",
        node_id="node-a",
    )

    assert result is None
    assert db.added == []
    assert db.committed is False
    assert credential_called is False


@pytest.mark.parametrize("service_cls", [GatewayLLMService, EngineLLMService])
def test_log_usage_skips_unknown_explicit_workflow_id(monkeypatch, service_cls):
    user_id = uuid4()
    model_id = uuid4()
    credential_called = False
    db = FakeDb(
        model=SimpleNamespace(id=model_id, model_id_for_api_call="gpt-test"),
    )

    def get_credential(*args, **kwargs):
        nonlocal credential_called
        credential_called = True
        return SimpleNamespace(id=uuid4())

    monkeypatch.setattr(
        service_cls,
        "_get_valid_credential_for_user",
        staticmethod(get_credential),
    )

    result = service_cls.log_usage(
        db,
        user_id=user_id,
        model_id="gpt-test",
        usage={"prompt_tokens": 1, "completion_tokens": 2},
        cost=0.1,
        workflow_id=uuid4(),
        node_id="node-a",
    )

    assert result is None
    assert db.added == []
    assert db.committed is False
    assert credential_called is False


@pytest.mark.parametrize("service_cls", [GatewayLLMService, EngineLLMService])
def test_log_usage_skips_mismatched_workflow_context(monkeypatch, service_cls):
    user_id = uuid4()
    model_id = uuid4()
    run_workflow_id = uuid4()
    supplied_workflow_id = uuid4()
    run_id = uuid4()
    credential_called = False
    db = FakeDb(
        model=SimpleNamespace(id=model_id, model_id_for_api_call="gpt-test"),
        run=SimpleNamespace(id=run_id, workflow_id=run_workflow_id),
    )

    def get_credential(*args, **kwargs):
        nonlocal credential_called
        credential_called = True
        return SimpleNamespace(id=uuid4())

    monkeypatch.setattr(
        service_cls,
        "_get_valid_credential_for_user",
        staticmethod(get_credential),
    )

    result = service_cls.log_usage(
        db,
        user_id=user_id,
        model_id="gpt-test",
        usage={"prompt_tokens": 1, "completion_tokens": 2},
        cost=0.1,
        workflow_id=supplied_workflow_id,
        workflow_run_id=run_id,
        node_id="node-a",
    )

    assert result is None
    assert db.added == []
    assert db.committed is False
    assert credential_called is False


@pytest.mark.parametrize("service_cls", [GatewayLLMService, EngineLLMService])
def test_log_usage_derives_context_from_workflow_run(monkeypatch, service_cls):
    user_id = uuid4()
    model_id = uuid4()
    run_id = uuid4()
    workflow_id = uuid4()
    organization_id = uuid4()
    credential_id = uuid4()
    db = FakeDb(
        model=SimpleNamespace(id=model_id, model_id_for_api_call="gpt-test"),
        run=SimpleNamespace(id=run_id, workflow_id=workflow_id),
        workflow=SimpleNamespace(id=workflow_id, organization_id=organization_id),
    )

    monkeypatch.setattr(
        service_cls,
        "_get_valid_credential_for_user",
        staticmethod(lambda *args, **kwargs: SimpleNamespace(id=credential_id)),
    )

    result = service_cls.log_usage(
        db,
        user_id=user_id,
        model_id="gpt-test",
        usage={"prompt_tokens": 1, "completion_tokens": 2, "latency_ms": 123},
        cost=0.1,
        workflow_run_id=run_id,
        node_id="node-a",
    )

    assert result is db.added[0]
    assert result.workflow_id == workflow_id
    assert result.organization_id == organization_id
    assert result.workflow_run_id == run_id
    assert result.latency_ms == 123
    assert db.committed is True


@pytest.mark.parametrize("service_cls", [GatewayLLMService, EngineLLMService])
@pytest.mark.parametrize("workflow_organization_id", [None, "not-a-uuid"])
def test_log_usage_allows_legacy_workflow_without_valid_organization(
    monkeypatch,
    service_cls,
    workflow_organization_id,
):
    user_id = uuid4()
    model_id = uuid4()
    workflow_id = uuid4()
    credential_id = uuid4()
    credential_kwargs = {}
    db = FakeDb(
        model=SimpleNamespace(id=model_id, model_id_for_api_call="gpt-test"),
        workflow=SimpleNamespace(
            id=workflow_id,
            organization_id=workflow_organization_id,
        ),
    )

    def get_credential(*args, **kwargs):
        credential_kwargs.update(kwargs)
        return SimpleNamespace(id=credential_id)

    monkeypatch.setattr(
        service_cls,
        "_get_valid_credential_for_user",
        staticmethod(get_credential),
    )

    result = service_cls.log_usage(
        db,
        user_id=user_id,
        model_id="gpt-test",
        usage={"prompt_tokens": 1, "completion_tokens": 2},
        cost=0.1,
        workflow_id=workflow_id,
        node_id="node-a",
    )

    assert result is db.added[0]
    assert result.workflow_id == workflow_id
    assert result.organization_id is None
    assert credential_kwargs["organization_id"] is None
    assert db.committed is True
