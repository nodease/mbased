import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from apps.gateway.services.model_routing_preview_service import (
    ModelRoutingPreviewBlockedError,
    ModelRoutingPreviewService,
)
from apps.workflow_engine.services.model_router import ModelRouter


def _deployment(*, node_data):
    node_data = {"title": "LLM", **node_data}
    return SimpleNamespace(
        id=uuid4(),
        version=2,
        graph_snapshot={
            "nodes": [
                {
                    "id": "llm-triage",
                    "type": "llmNode",
                    "data": node_data,
                }
            ]
        },
    )


def _policy(*, active_policy, status="active"):
    return SimpleNamespace(
        id=uuid4(),
        enabled=True,
        status=status,
        policy_version="router-policy-v4",
        active_policy=active_policy,
        execution_subject_user_id=uuid4(),
        organization_id=uuid4(),
    )


def _db_with_policy(policy):
    db = MagicMock()
    (
        db.query.return_value.filter.return_value.filter.return_value.filter.return_value.first.return_value
    ) = policy
    return db


class TestModelRoutingPreviewService:
    def test_preview_blocks_when_deployed_node_has_automatic_routing_disabled(self):
        """draft와 무관하게 deployment snapshot의 자동 라우팅 상태를 기준으로 막는다."""
        db = MagicMock()
        workflow = SimpleNamespace(id=uuid4(), graph={"nodes": []})
        deployment = _deployment(
            node_data={
                "model_id": "gpt-4.1",
                "auto_model_routing": False,
            }
        )

        with pytest.raises(ModelRoutingPreviewBlockedError) as exc_info:
            ModelRoutingPreviewService.preview(
                db,
                workflow=workflow,
                deployment=deployment,
                node_id="llm-triage",
                inputs={"message": "일반 문의"},
            )

        assert exc_info.value.code == "model_routing.disabled"

    def test_preview_blocks_until_an_active_policy_is_persisted(self):
        """정책이 없을 때 저장 모델을 추정해 미리보기 결과로 쓰지 않는다."""
        db = _db_with_policy(None)
        deployment = _deployment(
            node_data={
                "model_id": "gpt-4.1",
                "auto_model_routing": True,
            }
        )
        workflow = SimpleNamespace(id=uuid4(), graph=deployment.graph_snapshot)

        with pytest.raises(ModelRoutingPreviewBlockedError) as exc_info:
            ModelRoutingPreviewService.preview(
                db,
                workflow=workflow,
                deployment=deployment,
                node_id="llm-triage",
                inputs={"message": "일반 문의"},
            )

        assert exc_info.value.code == "model_routing.policy_not_ready"

    def test_preview_uses_judge_first_default_without_creating_run_or_policy_event(self):
        """미리보기는 Judge를 호출하지 않고 기본 모델만 안전하게 보여준다."""
        policy = _policy(
            active_policy={
                "strategy_id": "judge_bootstrap_incremental_v1",
                "default_model_id": "gpt-4.1",
                "fallback_model_id": "gpt-4.1-mini",
                "candidate_model_ids": ["gpt-4o-mini", "gpt-4.1-mini", "gpt-4.1"],
                "learning": {"mode": "judge_first", "judged_request_count": 0},
            }
        )
        db = _db_with_policy(policy)
        deployment = _deployment(
            node_data={
                "model_id": "gpt-4.1",
                "fallback_model_id": "gpt-4.1-mini",
                "auto_model_routing": True,
                "system_prompt": "고객 문의를 처리합니다.",
            }
        )
        workflow = SimpleNamespace(
            id=uuid4(),
            graph={
                "nodes": [
                    {
                        "id": "llm-triage",
                        "type": "llmNode",
                        "data": {
                            "model_id": "gpt-5.4",
                            "auto_model_routing": False,
                        },
                    }
                ]
            },
        )

        with patch(
            "apps.gateway.services.model_routing_preview_service.WorkflowRuntimeLLMService.get_runtime_available_model_ids_for_user",
            return_value=["gpt-4o-mini", "gpt-4.1-mini", "gpt-4.1"],
        ), patch(
            "apps.gateway.services.model_routing_preview_service.WorkflowRuntimeLLMService.get_runtime_client_for_user"
        ) as runtime_client:
            result = ModelRoutingPreviewService.preview(
                db,
                workflow=workflow,
                deployment=deployment,
                node_id="llm-triage",
                inputs={"message": "영수증을 다시 받고 싶습니다."},
            )

        assert result["deployment_version"] == 2
        assert result["selected_model_id"] == "gpt-4.1"
        assert result["fallback_model_id"] == "gpt-4.1-mini"
        assert result["decision_source"] == "default_model"
        assert result["matched_rule_id"] is None
        assert result["strategy_id"] == "judge_bootstrap_incremental_v1"
        assert result["reason_code"] == "judge_bootstrap_required"
        assert result["runtime_context"]["input_length_bucket"] == "short"
        assert result["draft_matches_deployment"] is False
        assert "inputs" not in result
        runtime_client.assert_not_called()
        db.add.assert_not_called()
        db.commit.assert_not_called()
        db.flush.assert_not_called()

    def test_preview_passes_shared_routing_feature_text_to_policy_resolver(self):
        policy = _policy(
            active_policy={
                "strategy_id": "judge_bootstrap_incremental_v1",
                "default_model_id": "gpt-4.1",
                "candidate_model_ids": ["gpt-4.1"],
                "learning": {"mode": "judge_first"},
            }
        )
        db = _db_with_policy(policy)
        deployment = _deployment(
            node_data={
                "model_id": "gpt-4.1",
                "auto_model_routing": True,
                "model_routing_task_description": "회사 정책 근거를 비교해 답합니다.",
                "system_prompt": "{{department}} 정책만 검토합니다.",
                "user_prompt": "질문: {{question}}",
                "assistant_prompt": "응답 형식: {{format}}",
                "referenced_variables": [
                    {
                        "name": "department",
                        "value_selector": ["start", "department"],
                    },
                    {
                        "name": "question",
                        "value_selector": ["start", "question"],
                    },
                    {
                        "name": "format",
                        "value_selector": ["start", "format"],
                    },
                ],
                "output_format": {
                    "type": "json",
                    "schema": {
                        "type": "object",
                        "properties": {"answer": {"type": "string"}},
                    },
                },
            }
        )
        workflow = SimpleNamespace(id=uuid4(), graph=deployment.graph_snapshot)

        with patch(
            "apps.gateway.services.model_routing_preview_service.WorkflowRuntimeLLMService.get_runtime_available_model_ids_for_user",
            return_value=["gpt-4.1"],
        ), patch.object(
            ModelRouter,
            "resolve_policy",
            wraps=ModelRouter.resolve_policy,
        ) as resolve_policy:
            ModelRoutingPreviewService.preview(
                db,
                workflow=workflow,
                deployment=deployment,
                node_id="llm-triage",
                inputs={
                    "start": {
                        "department": "개발팀",
                        "question": "휴가와 운영 규정을 비교해 주세요.",
                        "format": "요약",
                    }
                },
        )

        feature = resolve_policy.call_args.kwargs["routing_feature_text"]
        assert "CURRENT_REQUEST_JSON:" in feature
        request_json = feature.split("CURRENT_REQUEST_JSON:\n", 1)[1].split(
            "\n\nNODE_TASK_CONTRACT:", 1
        )[0]
        assert json.loads(request_json) == {
            "department": "개발팀",
            "format": "요약",
            "question": "휴가와 운영 규정을 비교해 주세요.",
        }
        assert "휴가와 운영 규정" in feature
        assert "TASK_DESCRIPTION:\n회사 정책 근거를 비교해 답합니다." in feature
        assert "SYSTEM_PROMPT:\n개발팀 정책만 검토합니다." in feature
        assert "USER_PROMPT:\n질문: 휴가와 운영 규정을 비교해 주세요." in feature
        assert "ASSISTANT_PROMPT:\n응답 형식: 요약" in feature
        assert "json schema:" in feature
        assert '"answer"' in feature
        assert "{{" not in feature

    def test_preview_uses_deployed_default_before_runtime_judge_runs(self):
        policy = _policy(
            active_policy={
                "strategy_id": "judge_bootstrap_incremental_v1",
                "default_model_id": "gpt-4.1",
                "fallback_model_id": "gpt-4.1-mini",
                "candidate_model_ids": ["gpt-4.1", "gpt-4.1-mini"],
                "learning": {"mode": "judge_first"},
            }
        )
        db = _db_with_policy(policy)
        deployment = _deployment(
            node_data={
                "model_id": "gpt-4.1",
                "fallback_model_id": "gpt-4.1-mini",
                "auto_model_routing": True,
                "system_prompt": "고객 문의를 처리합니다.",
            }
        )
        workflow = SimpleNamespace(id=uuid4(), graph=deployment.graph_snapshot)

        with patch(
            "apps.gateway.services.model_routing_preview_service.WorkflowRuntimeLLMService.get_runtime_available_model_ids_for_user",
            return_value=["gpt-4.1", "gpt-4.1-mini"],
        ):
            result = ModelRoutingPreviewService.preview(
                db,
                workflow=workflow,
                deployment=deployment,
                node_id="llm-triage",
                inputs={"message": "일반 문의"},
            )

        assert result["selected_model_id"] == "gpt-4.1"
        assert result["decision_source"] == "default_model"
        assert result["matched_rule_id"] is None

    def test_preview_blocks_invalid_prompt_template_instead_of_using_raw_source(self):
        policy = _policy(
            active_policy={
                "strategy_id": "judge_bootstrap_incremental_v1",
                "default_model_id": "gpt-4.1",
                "candidate_model_ids": ["gpt-4.1"],
                "learning": {"mode": "judge_first"},
            }
        )
        db = _db_with_policy(policy)
        deployment = _deployment(
            node_data={
                "model_id": "gpt-4.1",
                "auto_model_routing": True,
                "user_prompt": "{% if broken %}",
            }
        )
        workflow = SimpleNamespace(id=uuid4(), graph=deployment.graph_snapshot)

        with patch(
            "apps.gateway.services.model_routing_preview_service.WorkflowRuntimeLLMService.get_runtime_available_model_ids_for_user",
            return_value=["gpt-4.1"],
        ), pytest.raises(ModelRoutingPreviewBlockedError) as exc_info:
            ModelRoutingPreviewService.preview(
                db,
                workflow=workflow,
                deployment=deployment,
                node_id="llm-triage",
                inputs={},
            )

        assert exc_info.value.code == "model_routing.prompt_render_failed"

    def test_preview_uses_fallback_when_default_model_is_not_available(self):
        policy = _policy(
            active_policy={
                "strategy_id": "judge_bootstrap_incremental_v1",
                "default_model_id": "gpt-4.1",
                "fallback_model_id": "gpt-4.1-mini",
                "candidate_model_ids": ["gpt-4.1", "gpt-4.1-mini"],
                "learning": {"mode": "judge_first"},
            }
        )
        db = _db_with_policy(policy)
        deployment = _deployment(
            node_data={
                "model_id": "gpt-4.1",
                "fallback_model_id": "gpt-4.1-mini",
                "auto_model_routing": True,
                "system_prompt": "고객 문의를 처리합니다.",
            }
        )
        workflow = SimpleNamespace(id=uuid4(), graph=deployment.graph_snapshot)

        with patch(
            "apps.gateway.services.model_routing_preview_service.WorkflowRuntimeLLMService.get_runtime_available_model_ids_for_user",
            return_value=["gpt-4.1-mini"],
        ):
            result = ModelRoutingPreviewService.preview(
                db,
                workflow=workflow,
                deployment=deployment,
                node_id="llm-triage",
                inputs={"message": "일반 문의"},
            )

        assert result["selected_model_id"] == "gpt-4.1-mini"
        assert result["decision_source"] == "fallback_model"
        assert result["availability"] == "fallback"

    def test_preview_blocks_when_no_policy_model_is_available(self):
        policy = _policy(
            active_policy={
                "strategy_id": "judge_bootstrap_incremental_v1",
                "default_model_id": "gpt-4.1",
                "fallback_model_id": "gpt-4.1-mini",
                "candidate_model_ids": ["gpt-4.1", "gpt-4.1-mini"],
                "learning": {"mode": "judge_first"},
            }
        )
        db = _db_with_policy(policy)
        deployment = _deployment(
            node_data={
                "model_id": "gpt-4.1",
                "fallback_model_id": "gpt-4.1-mini",
                "auto_model_routing": True,
                "system_prompt": "고객 문의를 처리합니다.",
            }
        )
        workflow = SimpleNamespace(id=uuid4(), graph=deployment.graph_snapshot)

        with patch(
            "apps.gateway.services.model_routing_preview_service.WorkflowRuntimeLLMService.get_runtime_available_model_ids_for_user",
            return_value=[],
        ), pytest.raises(ModelRoutingPreviewBlockedError) as exc_info:
            ModelRoutingPreviewService.preview(
                db,
                workflow=workflow,
                deployment=deployment,
                node_id="llm-triage",
                inputs={"message": "일반 문의"},
            )

        assert exc_info.value.code == "model_routing.no_available_model"
