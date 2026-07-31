"""검증용 모델 라우팅 데모 workflow를 실행하고 3단계 라우팅 결정을 출력합니다.

실제 provider 호출 없이 WorkflowEngine을 실행합니다. LLM client와 log Celery task만
동기 테스트용으로 바꿔, workflow_runs/workflow_node_runs/llm_usage_logs가 실제 실행
경로에서 생성되는지 확인합니다.
"""

from __future__ import annotations

# Repository imports intentionally follow the sys.path bootstrap below.
# ruff: noqa: E402

import copy
import json
import pathlib
import sys
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

ROOT = pathlib.Path(__file__).resolve().parents[1]
PARENT_OF_ROOT = ROOT.parent
for path in (ROOT, PARENT_OF_ROOT):
    if str(path) not in sys.path:
        sys.path.append(str(path))

from apps.log_system import tasks as log_tasks
from apps.shared.celery_app import celery_app
from apps.shared.db.models.app import App
from apps.shared.db.models.llm import (
    LLMCredential,
    LLMModel,
    LLMRelCredentialModel,
    LLMUsageLog,
)
from apps.shared.db.models.workflow import Workflow
from apps.shared.db.models.workflow_deployment import DeploymentType, WorkflowDeployment
from apps.shared.db.models.workflow_run import (
    NodeRunStatus,
    RunStatus,
    RunTriggerMode,
    WorkflowNodeRun,
    WorkflowRun,
)
from apps.shared.db.session import SessionLocal
from apps.workflow_engine.services.llm_service import LLMRuntimeSelection, LLMService
from apps.workflow_engine.services.model_router import ModelRouter, ModelRouterContext
from apps.workflow_engine.workflow.core.workflow_engine import WorkflowEngine
from scripts.managed_app_secret_fixture import configure_managed_app_secret_fixture


APP_ID = uuid.UUID("91000000-0000-0000-0000-000000000001")
WORKFLOW_ID = uuid.UUID("91000000-0000-0000-0000-000000000002")
DEPLOYMENT_ID = uuid.UUID("91000000-0000-0000-0000-000000000003")
NAMESPACE = uuid.UUID("91000000-0000-0000-0000-000000000100")
NODE_ID = "llm-triage"
RUN_COUNT = 95
CHECKPOINTS = {0, 1, 19, 20, 89, 90, 95}
HIGH_RISK_RUN_COUNT = 110
HIGH_RISK_CHECKPOINTS = {0, 1, 19, 20, 89, 90, 110}
ORG_ID = uuid.UUID("10200000-0000-0000-0000-000000000100")
USER_IDS = {
    "author": uuid.UUID("10200000-0000-0000-0000-000000000003"),
}


def _base_node_data(title: str, description: str, display_number: int) -> dict[str, Any]:
    return {
        "title": title,
        "description": description,
        "displayNumber": display_number,
        "visibleProperties": [],
    }


def _node(
    node_id: str,
    node_type: str,
    x: int,
    y: int,
    data: dict[str, Any],
) -> dict[str, Any]:
    return {
        "id": node_id,
        "type": node_type,
        "position": {"x": x, "y": y},
        "data": data,
    }


def _edge(
    edge_id: str,
    source: str,
    target: str,
    source_handle: str | None = None,
) -> dict[str, Any]:
    edge = {"id": edge_id, "source": source, "target": target}
    if source_handle:
        edge["sourceHandle"] = source_handle
    return edge


def _ticket_ops_graph() -> dict[str, Any]:
    return {
        "nodes": [
            _node(
                "webhook-ticket",
                "webhookTrigger",
                120,
                220,
                {
                    **_base_node_data(
                        "고객 티켓 수신",
                        "고객지원 티켓 payload를 수신합니다.",
                        1,
                    ),
                    "variable_mappings": [
                        {"json_path": "message", "variable_name": "message"},
                        {"json_path": "customerTier", "variable_name": "customerTier"},
                    ],
                },
            ),
            _node(
                NODE_ID,
                "llmNode",
                540,
                220,
                {
                    **_base_node_data(
                        "티켓 처리 판단",
                        "티켓 유형, 심각도, 승인 필요 여부를 판단합니다.",
                        2,
                    ),
                    "provider": "openai",
                    "model_id": "gpt-4.1",
                    "fallback_model_id": "gpt-4.1",
                    "system_prompt": (
                        "고객지원 티켓을 처리하는 AI로서 각 티켓의 긴급도를 "
                        "true/false로 판별하고 JSON 형식으로 답변 초안을 작성합니다."
                    ),
                    "user_prompt": "고객 등급: {{ customerTier }}\n문의: {{ message }}",
                    "referenced_variables": [
                        {
                            "name": "customerTier",
                            "value_selector": ["webhook-ticket", "customerTier"],
                        },
                        {
                            "name": "message",
                            "value_selector": ["webhook-ticket", "message"],
                        },
                    ],
                    "knowledgeBases": [],
                    "parameters": {"temperature": 0.2, "max_tokens": 2000},
                },
            ),
            _node(
                "extract-ticket",
                "variableExtractionNode",
                960,
                220,
                {
                    **_base_node_data(
                        "처리 결과 추출",
                        "LLM JSON 문자열을 후속 분기 변수로 추출합니다.",
                        3,
                    ),
                    "source_selector": [NODE_ID, "text"],
                    "mappings": [
                        {"name": "approvalRequired", "json_path": "긴급도"},
                        {"name": "mailDraft", "json_path": "답변 초안"},
                    ],
                },
            ),
            _node(
                "condition-approval",
                "conditionNode",
                1380,
                220,
                {
                    **_base_node_data("승인 필요 분기", "승인 요청 여부로 분기합니다.", 4),
                    "cases": [
                        {
                            "id": "approval",
                            "case_name": "승인 필요",
                            "logical_operator": "or",
                            "conditions": [
                                {
                                    "id": "cond-approval-required",
                                    "variable_selector": [
                                        "extract-ticket",
                                        "approvalRequired",
                                    ],
                                    "operator": "equals",
                                    "value": True,
                                }
                            ],
                        }
                    ],
                },
            ),
            _node(
                "template-approval",
                "templateNode",
                1800,
                80,
                {
                    **_base_node_data("승인 요청 메시지", "CS 리드 승인 요청 메시지를 만듭니다.", 5),
                    "template": (
                        "승인 검토가 필요한 enterprise 고객 문의입니다.\n\n"
                        "고객 문의: {{ message }}\n"
                        "답변 초안: {{ mailDraft }}"
                    ),
                    "variables": [
                        {"name": "mailDraft", "value_selector": ["extract-ticket", "mailDraft"]},
                        {"name": "message", "value_selector": ["webhook-ticket", "message"]},
                    ],
                },
            ),
            _node(
                "template-reply",
                "templateNode",
                1800,
                360,
                {
                    **_base_node_data("고객 답변 초안", "고객에게 보낼 답변을 정리합니다.", 6),
                    "template": "안녕하세요.\n\n{{ mailDraft }}\n\n감사합니다.",
                    "variables": [
                        {"name": "mailDraft", "value_selector": ["extract-ticket", "mailDraft"]}
                    ],
                },
            ),
            _node(
                "answer-approval",
                "answerNode",
                2220,
                80,
                {
                    **_base_node_data("승인 요청 결과", "승인 요청 branch 결과를 반환합니다.", 7),
                    "outputs": [
                        {
                            "variable": "answer_text",
                            "label": "처리 결과",
                            "value_selector": ["template-approval", "text"],
                        }
                    ],
                },
            ),
            _node(
                "answer-reply",
                "answerNode",
                2220,
                360,
                {
                    **_base_node_data("고객 답변 결과", "고객 답변 branch 결과를 반환합니다.", 8),
                    "outputs": [
                        {
                            "variable": "answer_text",
                            "label": "처리 결과",
                            "value_selector": ["template-reply", "text"],
                        }
                    ],
                },
            ),
        ],
        "edges": [
            _edge("edge-webhook-llm", "webhook-ticket", NODE_ID),
            _edge("edge-llm-extract", NODE_ID, "extract-ticket"),
            _edge("edge-extract-condition", "extract-ticket", "condition-approval"),
            _edge("edge-condition-approval", "condition-approval", "template-approval", "approval"),
            _edge("edge-condition-reply", "condition-approval", "template-reply", "default"),
            _edge("edge-approval-answer", "template-approval", "answer-approval"),
            _edge("edge-reply-answer", "template-reply", "answer-reply"),
        ],
        "viewport": {"x": 20, "y": 70, "zoom": 0.48},
    }


def _make_graph() -> dict[str, Any]:
    graph = copy.deepcopy(_ticket_ops_graph())
    for node in graph.get("nodes", []):
        if node.get("id") == NODE_ID:
            node.setdefault("data", {})
            node["data"]["model_id"] = "gpt-4.1"
            node["data"]["fallback_model_id"] = "gpt-4.1"
            # 모델 라우팅 검증에서는 RAG 권한/색인 상태가 변수로 끼지 않도록 끕니다.
            # LLM 출력은 여전히 추출, 조건 분기, 템플릿, 응답 노드에서 재사용됩니다.
            node["data"]["knowledgeBases"] = []
    return graph


def _make_high_risk_graph() -> dict[str, Any]:
    graph = _make_graph()
    for node in graph.get("nodes", []):
        if node.get("id") == NODE_ID:
            node["data"]["title"] = "보상/SLA 위험 판단"
            node["data"]["description"] = "고객 보상, SLA, 장애 영향 범위를 보수적으로 판단합니다."
            node["data"]["system_prompt"] = (
                "당신은 enterprise 고객 장애, SLA, 보상, 법무 리스크를 검토하는 "
                "고객지원 운영 AI입니다. 잘못된 답변은 고객 보상과 법무 리스크로 이어질 수 "
                "있으므로 보수적으로 판단하고 JSON 형식을 지키세요."
            )
            node["data"]["user_prompt"] = (
                "고객 등급: {{ customerTier }}\n"
                "계약 유형: enterprise\n"
                "문의: {{ message }}\n"
                "SLA/보상/법무 리스크를 고려해 승인 필요 여부와 답변 초안을 작성하세요."
            )
            node["data"]["output_format"] = {
                "type": "json",
                "schema": {
                    "type": "object",
                    "properties": {
                        "긴급도": {"type": "boolean"},
                        "답변 초안": {"type": "string"},
                    },
                    "required": ["긴급도", "답변 초안"],
                },
            }
    return graph


def _upsert_demo_workflow(db, graph: dict[str, Any]) -> None:
    app = db.get(App, APP_ID)
    if app is None:
        app = App(
            id=APP_ID,
            organization_id=ORG_ID,
            name="모델 라우팅 검증용 고객 티켓 처리",
            description="LLM 출력이 추출, 조건 분기, 템플릿, 응답 노드에서 재사용되는 검증용 workflow",
            icon={"type": "emoji", "content": "🧭", "background_color": "#E0F2FE"},
            url_slug="demo-model-router-ticket-ops",
            is_api_enabled=True,
            api_req_per_minute=60,
            api_req_per_hour=3600,
            is_market=False,
            created_by=USER_IDS["author"],
        )
        db.add(app)
        db.flush()

    configure_managed_app_secret_fixture(app)
    app.organization_id = ORG_ID
    app.name = "모델 라우팅 검증용 고객 티켓 처리"

    workflow = db.get(Workflow, WORKFLOW_ID)
    if workflow is None:
        workflow = Workflow(
            id=WORKFLOW_ID,
            organization_id=ORG_ID,
            app_id=APP_ID,
            graph=graph,
            features={},
            env_variables=[],
            runtime_variables=[],
            created_by=USER_IDS["author"],
            updated_by=USER_IDS["author"],
        )
        db.add(workflow)
    else:
        workflow.organization_id = ORG_ID
        workflow.app_id = APP_ID
        workflow.graph = graph
        workflow.updated_by = USER_IDS["author"]
        workflow.updated_at = datetime.now(timezone.utc)
    db.flush()

    app.workflow_id = WORKFLOW_ID
    db.flush()

    deployment = db.get(WorkflowDeployment, DEPLOYMENT_ID)
    if deployment is None:
        deployment = WorkflowDeployment(
            id=DEPLOYMENT_ID,
            app_id=APP_ID,
            version=1,
            type=DeploymentType.WEBHOOK,
            graph_snapshot=graph,
            config={"demo": "model-router"},
            input_schema={"type": "object"},
            output_schema={"type": "object"},
            description="모델 라우팅 검증용 활성 배포",
            created_by=USER_IDS["author"],
            is_active=True,
        )
        db.add(deployment)
    else:
        deployment.app_id = APP_ID
        deployment.graph_snapshot = graph
        deployment.type = DeploymentType.WEBHOOK
        deployment.is_active = True
    db.flush()

    app.active_deployment_id = DEPLOYMENT_ID
    db.flush()


def _clear_demo_runs(db) -> None:
    run_ids = [
        row[0]
        for row in db.query(WorkflowRun.id)
        .filter(WorkflowRun.workflow_id == WORKFLOW_ID)
        .all()
    ]
    db.query(LLMUsageLog).filter(LLMUsageLog.workflow_id == WORKFLOW_ID).delete(
        synchronize_session=False
    )
    if run_ids:
        db.query(WorkflowNodeRun).filter(
            WorkflowNodeRun.workflow_run_id.in_(run_ids)
        ).delete(synchronize_session=False)
        db.query(WorkflowRun).filter(WorkflowRun.id.in_(run_ids)).delete(
            synchronize_session=False
        )
    db.flush()


def _model(db, model_id: str) -> LLMModel:
    model = (
        db.query(LLMModel)
        .filter(LLMModel.model_id_for_api_call == model_id)
        .first()
    )
    if model is None:
        raise RuntimeError(f"LLM model is missing in DB: {model_id}")
    return model


def _run_uuid(index: int, suffix: str = "run") -> uuid.UUID:
    return uuid.uuid5(NAMESPACE, f"{suffix}-{index}")


def _node_uuid(index: int, node_id: str) -> uuid.UUID:
    return uuid.uuid5(NAMESPACE, f"node-{index}-{node_id}")


def _credential_id_for_model(db, model: LLMModel) -> uuid.UUID:
    credential = (
        db.query(LLMCredential)
        .join(LLMRelCredentialModel, LLMRelCredentialModel.credential_id == LLMCredential.id)
        .filter(LLMCredential.organization_id == ORG_ID)
        .filter(LLMCredential.is_valid.is_(True))
        .filter(LLMRelCredentialModel.model_id == model.id)
        .filter(LLMRelCredentialModel.is_verified.is_(True))
        .first()
    )
    if credential is None:
        raise RuntimeError(f"verified credential is missing for model: {model.model_id_for_api_call}")
    return credential.id


def _insert_run(db, *, index: int, model: LLMModel, deployed: bool) -> None:
    now = datetime.now(timezone.utc) - timedelta(minutes=RUN_COUNT - index)
    approval_required = index % 4 == 0
    duration = 4.8 if model.model_id_for_api_call == "gpt-4.1" else 1.8
    prompt_tokens = 1450 if model.model_id_for_api_call == "gpt-4.1" else 780
    completion_tokens = 460 if model.model_id_for_api_call == "gpt-4.1" else 260
    total_tokens = prompt_tokens + completion_tokens
    total_cost = (
        Decimal("0.0190")
        if model.model_id_for_api_call == "gpt-4.1"
        else Decimal("0.0013")
    )
    branch = "approval" if approval_required else "reply"
    output_text = {
        "긴급도": approval_required,
        "답변 초안": (
            "SLA와 보상 가능성을 CS 리드가 검토해야 합니다."
            if approval_required
            else "정산 파일 재생성 절차와 다운로드 위치를 안내합니다."
        ),
    }
    run = WorkflowRun(
        id=_run_uuid(index, "operational" if deployed else "test"),
        workflow_id=WORKFLOW_ID,
        user_id=USER_IDS["author"],
        app_id=APP_ID,
        deployment_id=DEPLOYMENT_ID if deployed else None,
        workflow_version=1 if deployed else None,
        status=RunStatus.SUCCESS,
        trigger_mode=RunTriggerMode.WEBHOOK if deployed else RunTriggerMode.MANUAL,
        inputs={
            "customerTier": "enterprise",
            "message": "결제 API 장애로 정산 파일 생성이 실패했습니다. 보상 가능 여부를 확인해 주세요.",
        },
        outputs={"answer_text": output_text["답변 초안"], "branch": branch},
        started_at=now,
        finished_at=now + timedelta(seconds=duration),
        duration=duration,
        meta_info={"demo": "model-router", "deployed": deployed},
        trace_metadata={"demo": "model-router"},
        total_tokens=total_tokens,
        total_cost=total_cost,
        redaction_applied=False,
        pii_detected=False,
        payload_storage_mode="redacted_only",
    )
    db.add(run)
    db.flush()

    node_specs = [
        ("webhook-ticket", "webhookTrigger", {"message": run.inputs["message"]}),
        (NODE_ID, "llmNode", {"text": output_text, "model": model.model_id_for_api_call}),
        ("extract-ticket", "variableExtractionNode", {"approvalRequired": approval_required, "mailDraft": output_text["답변 초안"]}),
        ("condition-approval", "conditionNode", {"selected_handle": branch}),
        (f"template-{branch}", "templateNode", {"text": output_text["답변 초안"]}),
        (f"answer-{branch}", "answerNode", {"answer_text": output_text["답변 초안"]}),
    ]
    for sequence, (node_id, node_type, outputs) in enumerate(node_specs, start=1):
        db.add(
            WorkflowNodeRun(
                id=_node_uuid(index, node_id),
                workflow_run_id=run.id,
                node_id=node_id,
                node_type=node_type,
                status=NodeRunStatus.SUCCESS,
                inputs=run.inputs if node_id == "webhook-ticket" else {"previous": "redacted"},
                process_data={"demo": "model-router", "sequence": sequence},
                outputs=outputs,
                started_at=now + timedelta(milliseconds=sequence * 100),
                finished_at=now + timedelta(milliseconds=sequence * 100, seconds=0.1),
                duration=duration if node_id == NODE_ID else 0.1,
                trace_metadata={
                    "schema_status": "passed",
                    "downstream_status": "compatible",
                    "llm": {"fallback_used": False},
                }
                if node_id == NODE_ID
                else {"demo": "model-router"},
                redaction_applied=False,
                pii_detected=False,
                sequence=sequence,
                retry_count=0,
            )
        )

    db.add(
        LLMUsageLog(
            id=_run_uuid(index, "usage"),
            user_id=USER_IDS["author"],
            organization_id=ORG_ID,
            credential_id=_credential_id_for_model(db, model),
            model_id=model.id,
            workflow_id=WORKFLOW_ID,
            workflow_run_id=run.id,
            node_id=NODE_ID,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_cost=total_cost,
            latency_ms=int(duration * 1000),
            status="success",
            created_at=now,
        )
    )


class _DemoLLMClient:
    """실제 provider 호출 대신 모델별 비용/토큰 차이를 재현하는 검증용 client."""

    def __init__(self, model_id: str):
        self.model_id = model_id

    def invoke_sync(self, messages: list[dict[str, Any]], **_: Any) -> dict[str, Any]:
        prompt_text = "\n".join(str(message.get("content", "")) for message in messages)
        approval_required = any(
            keyword in prompt_text for keyword in ("보상", "SLA", "장애", "법무")
        )
        content = json.dumps(
            {
                "긴급도": approval_required,
                "답변 초안": (
                    "SLA와 보상 가능성을 CS 리드가 검토해야 합니다."
                    if approval_required
                    else "정산 파일 재생성 절차와 다운로드 위치를 안내합니다."
                ),
            },
            ensure_ascii=False,
        )

        if self.model_id == "gpt-4.1":
            usage = {
                "prompt_tokens": 1450,
                "completion_tokens": 460,
                "total_tokens": 1910,
                "latency_ms": 4800,
            }
        else:
            usage = {
                "prompt_tokens": 780,
                "completion_tokens": 260,
                "total_tokens": 1040,
                "latency_ms": 1800,
            }
        return {"choices": [{"message": {"content": content}}], "usage": usage}


@contextmanager
def _synchronous_log_tasks():
    """WorkflowLogger가 보내는 log.* Celery task를 현재 프로세스에서 즉시 실행한다."""

    original_send_task = celery_app.send_task
    task_map = {
        "log.create_run": log_tasks.create_run_log,
        "log.update_run_finish": log_tasks.update_run_log_finish,
        "log.update_run_error": log_tasks.update_run_log_error,
        "log.create_node": log_tasks.create_node_log,
        "log.update_node_finish": log_tasks.update_node_log_finish,
        "log.update_node_error": log_tasks.update_node_log_error,
    }

    def send_task(task_name, args=None, kwargs=None, **options):
        if task_name not in task_map:
            return original_send_task(task_name, args=args, kwargs=kwargs, **options)
        result = task_map[task_name].run(*(args or []), **(kwargs or {}))
        return SimpleNamespace(get=lambda timeout=None: result)

    celery_app.send_task = send_task
    try:
        yield
    finally:
        celery_app.send_task = original_send_task


@contextmanager
def _demo_llm_runtime():
    """LLM runtime credential 선택은 유지하되 provider 호출은 검증용 client로 바꾼다."""

    original = LLMService.get_runtime_client_for_user

    def get_runtime_client_for_user(db, user_id, model_id, organization_id=None):
        model = _model(db, model_id)
        return LLMRuntimeSelection(
            client=_DemoLLMClient(model.model_id_for_api_call),
            credential_id=_credential_id_for_model(db, model),
            model_id=model.model_id_for_api_call,
            organization_id=organization_id or ORG_ID,
        )

    LLMService.get_runtime_client_for_user = staticmethod(get_runtime_client_for_user)
    try:
        yield
    finally:
        LLMService.get_runtime_client_for_user = original


def _input_for_run(index: int, *, high_risk: bool) -> dict[str, Any]:
    if not high_risk:
        return {
            "customerTier": "enterprise",
            "message": (
                "결제 API 장애로 정산 파일 생성이 실패했습니다. 보상 가능 여부를 확인해 주세요."
                if index % 4 == 0
                else "정산 파일을 다시 생성하는 방법과 다운로드 위치를 안내해 주세요."
            ),
        }

    topics = [
        "정산 파일 생성 실패",
        "결제 API 지연",
        "관리자 콘솔 접근 장애",
        "주문 동기화 누락",
        "월간 리포트 데이터 불일치",
        "SSO 로그인 실패",
        "대량 웹훅 재전송 실패",
        "세금계산서 발행 오류",
        "개인정보 마스킹 누락 의심",
        "계약 SLA 응답 시간 초과",
    ]
    regions = ["KR", "JP", "US", "EU"]
    impacts = ["정산 지연", "고객 업무 중단", "보고 누락", "보안 검토 필요", "매출 인식 지연"]
    topic = topics[index % len(topics)]
    region = regions[index % len(regions)]
    impact = impacts[index % len(impacts)]
    minutes = 10 + (index * 7) % 180
    account = f"ENT-{1000 + index:04d}"
    return {
        "customerTier": "enterprise",
        "message": (
            f"[{account}/{region}] {topic} 건입니다. 영향: {impact}. "
            f"장애 지속 시간은 약 {minutes}분이며 SLA 위반 가능성과 보상 안내 문구, "
            f"법무 검토 필요 여부를 함께 판단해 주세요. 입력번호={index}."
        ),
    }


def _execute_engine_run(
    index: int,
    graph: dict[str, Any],
    *,
    high_risk: bool = False,
) -> dict[str, Any]:
    engine = None
    user_input = _input_for_run(index, high_risk=high_risk)
    execution_context = {
        "workflow_id": str(WORKFLOW_ID),
        "app_id": str(APP_ID),
        "deployment_id": str(DEPLOYMENT_ID),
        "workflow_version": 1,
        "user_id": str(USER_IDS["author"]),
        "organization_id": str(ORG_ID),
        "trigger_mode": "webhook",
        "execution_subject": {
            "subject_type": "user",
            "subject_id": str(USER_IDS["author"]),
        },
    }
    try:
        engine = WorkflowEngine(
            graph=graph,
            user_input=user_input,
            execution_context=execution_context,
            is_deployed=True,
            workflow_timeout=30,
        )
        return engine.execute()
    finally:
        if engine is not None:
            engine.cleanup()


def _decision_summary(
    db,
    *,
    customer_facing: bool = False,
    knowledge_enabled: bool = False,
) -> dict[str, Any]:
    candidates = ModelRouter.collect_candidates(db, organization_id=ORG_ID)
    context = ModelRouterContext(
        workflow_id=str(WORKFLOW_ID),
        node_id=NODE_ID,
        current_model_id="gpt-4.1",
        fallback_model_id="gpt-4.1",
        candidate_models=candidates,
        customer_facing=customer_facing,
        knowledge_enabled=knowledge_enabled,
    )
    decision = ModelRouter.resolve(context, db=db)
    profile = ModelRouter.collect_profile(db, context)
    return {
        "usable_runs": profile.operational_usable_runs,
        "stage": decision.routing_stage,
        "selected_model": decision.selected_model_id,
        "fallback_model": decision.fallback_model_id,
        "reason": decision.reason,
        "models": {
            model_id: performance.as_summary()
            for model_id, performance in profile.model_performance.items()
        },
    }


def _assert_low_risk_optimization(summaries: dict[int, dict[str, Any]]) -> None:
    for checkpoint, summary in summaries.items():
        if summary["selected_model"] != "gpt-4.1":
            raise AssertionError(
                "검증된 저비용 후보가 없는 실행은 현재 모델을 유지해야 합니다. "
                f"checkpoint={checkpoint}, selected={summary['selected_model']}"
            )


def _assert_high_risk_keeps_expensive_model(
    summaries: dict[int, dict[str, Any]]
) -> None:
    for checkpoint, summary in summaries.items():
        if summary["selected_model"] != "gpt-4.1":
            raise AssertionError(
                "high-risk workflow must keep gpt-4.1 at "
                f"{checkpoint} runs, got {summary['selected_model']}"
            )
    if summaries[110]["models"].get("gpt-4.1", {}).get("run_count") != 110:
        raise AssertionError("high-risk scenario must execute 110 varied operational runs.")


def _print_summaries(title: str, summaries: dict[int, dict[str, Any]]) -> None:
    print(title)
    for checkpoint in sorted(summaries):
        summary = summaries[checkpoint]
        print(
            f"{checkpoint:>3} operational usable runs | "
            f"stage={summary['stage']} | "
            f"selected={summary['selected_model']} | "
            f"fallback={summary['fallback_model']} | "
            f"reason={summary['reason']}"
        )
        for model_id, metrics in sorted(summary["models"].items()):
            print(f"    - {model_id}: {metrics}")


def main() -> None:
    with SessionLocal() as db:
        graph = _make_graph()
        _upsert_demo_workflow(db, graph)
        _clear_demo_runs(db)
        db.commit()

        summaries = {0: _decision_summary(db)}
        with _synchronous_log_tasks(), _demo_llm_runtime():
            for index in range(1, RUN_COUNT + 1):
                _execute_engine_run(index, graph)
                if index in CHECKPOINTS:
                    db.expire_all()
                    summaries[index] = _decision_summary(db)

        high_risk_graph = _make_high_risk_graph()
        _upsert_demo_workflow(db, high_risk_graph)
        _clear_demo_runs(db)
        db.commit()

        high_risk_summaries = {
            0: _decision_summary(db, customer_facing=True, knowledge_enabled=False)
        }
        with _synchronous_log_tasks(), _demo_llm_runtime():
            for index in range(1, HIGH_RISK_RUN_COUNT + 1):
                _execute_engine_run(index, high_risk_graph, high_risk=True)
                if index in HIGH_RISK_CHECKPOINTS:
                    db.expire_all()
                    high_risk_summaries[index] = _decision_summary(
                        db,
                        customer_facing=True,
                        knowledge_enabled=False,
                    )

    _assert_low_risk_optimization(summaries)
    _assert_high_risk_keeps_expensive_model(high_risk_summaries)

    print("Model router demo workflow")
    print(f"- workflow_id: {WORKFLOW_ID}")
    print(f"- app_id: {APP_ID}")
    print(f"- deployment_id: {DEPLOYMENT_ID}")
    print("- graph: webhook -> LLM -> variable extraction -> condition -> template -> answer")
    print("- execution path: WorkflowEngine + synchronous log tasks + fake LLM client")
    print(f"- deployed operational runs executed: {RUN_COUNT}")
    print()
    _print_summaries("Low-risk optimization scenario", summaries)
    print()
    print("- high-risk deployed operational runs executed: 110")
    print("- high-risk inputs: 110 unique enterprise SLA/compensation/legal cases")
    _print_summaries("High-risk expensive-model scenario", high_risk_summaries)


if __name__ == "__main__":
    main()
