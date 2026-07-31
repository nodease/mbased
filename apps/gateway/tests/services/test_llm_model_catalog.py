from types import SimpleNamespace

import pytest

from apps.gateway.services import llm_service as gateway_llm_service
from apps.gateway.services.llm_service import LLMService
from apps.shared.services.egress_guard import EgressGuardError, SafeHTTPResponse
from apps.shared.services.outbound_operation_policy import LLM_MODEL_DISCOVERY
from apps.workflow_engine.services import llm_service as workflow_llm_service
from apps.workflow_engine.services.llm_service import LLMService as WorkflowLLMService


@pytest.mark.parametrize(
    ("service_module", "service_type"),
    [
        (gateway_llm_service, LLMService),
        (workflow_llm_service, WorkflowLLMService),
    ],
)
def test_model_discovery_uses_guarded_operation_profile(
    monkeypatch,
    service_module,
    service_type,
):
    calls = []

    def fake_safe_http_request(method, url, **kwargs):
        calls.append((method, url, kwargs))
        return SafeHTTPResponse(
            status_code=200,
            headers={"content-type": "application/json"},
            content=b'{"data":[{"id":"synthetic-model"}]}',
            final_url=url,
        )

    monkeypatch.setattr(service_module, "safe_http_request", fake_safe_http_request)

    result = service_type._fetch_remote_models(
        "https://provider.example/v1",
        "synthetic-key",
        "openai",
    )

    assert result == [{"id": "synthetic-model"}]
    assert calls == [
        (
            "GET",
            "https://provider.example/v1/models",
            {
                "headers": {"Authorization": "Bearer synthetic-key"},
                "operation_id": LLM_MODEL_DISCOVERY,
            },
        )
    ]


@pytest.mark.parametrize(
    ("service_module", "service_type"),
    [
        (gateway_llm_service, LLMService),
        (workflow_llm_service, WorkflowLLMService),
    ],
)
def test_model_discovery_maps_guard_denial_without_leaking_endpoint(
    monkeypatch,
    service_module,
    service_type,
):
    def deny_request(*_args, **_kwargs):
        raise EgressGuardError("egress.unsupported_scheme")

    monkeypatch.setattr(service_module, "safe_http_request", deny_request)

    with pytest.raises(ValueError) as captured:
        service_type._fetch_remote_models(
            "http://internal.service.invalid/v1?token=must-not-leak",
            "synthetic-key",
            "openai",
        )

    assert str(captured.value) == "Network error verifying openai key"
    assert "internal.service.invalid" not in str(captured.value)
    assert "must-not-leak" not in str(captured.value)


def test_gpt_5_6_tiers_have_display_names_and_prices():
    """새 durable model tier는 seed/비용 표시가 가능한 Gateway catalog에 등록된다."""
    expected_prices = {
        "gpt-5.6": {"input": 0.005, "output": 0.03},
        "gpt-5.6-terra": {"input": 0.0025, "output": 0.015},
        "gpt-5.6-luna": {"input": 0.001, "output": 0.006},
    }

    for model_id, price in expected_prices.items():
        assert LLMService.MODEL_DISPLAY_NAMES[model_id]
        assert LLMService.KNOWN_MODEL_PRICES[model_id] == price


def test_gateway_and_workflow_engine_use_the_same_catalog_costs():
    usage = {"prompt_tokens_details": {"cached_tokens": 400}}

    gateway_cost = LLMService.calculate_cost(
        None,
        "gpt-4o-mini",
        prompt_tokens=1_000,
        completion_tokens=500,
        usage=usage,
    )
    workflow_cost = WorkflowLLMService.calculate_cost(
        None,
        "gpt-4o-mini",
        prompt_tokens=1_000,
        completion_tokens=500,
        usage=usage,
    )

    assert gateway_cost == workflow_cost == 0.00042


def test_gateway_and_workflow_engine_prefer_the_same_db_price_override():
    """관리자가 바꾼 단가는 라우팅 판단과 실행 비용에 같은 기준으로 쓰인다."""

    model = SimpleNamespace(
        model_id_for_api_call="gpt-4o-mini",
        input_price_1k=0.01,
        output_price_1k=0.02,
    )

    class FakeDb:
        def query(self, _model):
            return self

        def filter(self, *_args):
            return self

        def first(self):
            return model

    db = FakeDb()
    gateway_cost = LLMService.calculate_cost(
        db,
        "gpt-4o-mini",
        prompt_tokens=1_000,
        completion_tokens=500,
    )
    workflow_cost = WorkflowLLMService.calculate_cost(
        db,
        "gpt-4o-mini",
        prompt_tokens=1_000,
        completion_tokens=500,
    )

    assert gateway_cost == workflow_cost == 0.02


def test_dated_model_uses_the_canonical_db_price_override():
    """날짜가 붙은 실행 ID도 canonical DB 가격과 라우팅 catalog를 공유한다."""

    model = SimpleNamespace(
        model_id_for_api_call="gpt-4.1",
        input_price_1k=0.01,
        output_price_1k=0.02,
    )

    class FakeDb:
        def __init__(self):
            self.lookup_count = 0

        def query(self, _model):
            return self

        def filter(self, *_args):
            return self

        def first(self):
            self.lookup_count += 1
            return None if self.lookup_count == 1 else model

    gateway_cost = LLMService.calculate_cost(
        FakeDb(),
        "gpt-4.1-2025-04-14",
        prompt_tokens=1_000,
        completion_tokens=500,
    )
    workflow_cost = WorkflowLLMService.calculate_cost(
        FakeDb(),
        "gpt-4.1-2025-04-14",
        prompt_tokens=1_000,
        completion_tokens=500,
    )

    assert gateway_cost == workflow_cost == 0.02


def test_pricing_sync_overwrites_legacy_google_alias_price():
    model = SimpleNamespace(
        model_id_for_api_call="models/gemini-3.5-flash",
        input_price_1k=0.0015,
        output_price_1k=0.009,
    )

    class FakeDb:
        committed = False

        def query(self, _model):
            return self

        def all(self):
            return [model]

        def commit(self):
            self.committed = True

    db = FakeDb()
    result = LLMService.sync_system_prices(db)

    assert result == {"updated_models": 1}
    assert db.committed is True
    assert model.input_price_1k == 0.00075
    assert model.output_price_1k == 0.0045
