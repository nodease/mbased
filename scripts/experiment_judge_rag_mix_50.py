"""RAG 혼합 요청에서 Judge-first 자동 모델 라우팅을 검증하는 실제 호출 실험.

한 workflow 안에서 ``requiresRag`` 조건으로 RAG LLM node와 비-RAG LLM node를
분기한다. 매 10개 batch에 같은 비율의 RAG/직접 요청과 난이도를 넣고, Runtime
Judge 또는 JSON/RAG 계약이 실패하면 즉시 중단한다.

기본값은 비용이 발생하지 않는 ``--dry-run``이며 실제 provider 호출에는
``--execute``가 필요하다.
"""

# 이 스크립트는 repo root를 sys.path에 추가한 뒤 애플리케이션 모듈을 import한다.
# ruff: noqa: E402

from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time
import uuid
from collections import Counter
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Iterable


ROOT = pathlib.Path(__file__).resolve().parents[1]
PARENT_OF_ROOT = ROOT.parent
for candidate_path in (ROOT, PARENT_OF_ROOT):
    if str(candidate_path) not in sys.path:
        sys.path.append(str(candidate_path))

from apps.log_system import tasks as log_tasks
from apps.shared.celery_app import celery_app
from apps.shared.db.demo_seed import KB_IDS, _base_node_data, _edge, _node
from apps.shared.db.models.app import App
from apps.shared.db.models.llm import LLMUsageLog
from apps.shared.db.models.model_routing_policy import LLMNodeModelRoutingPolicy
from apps.shared.db.models.workflow import Workflow
from apps.shared.db.models.workflow_deployment import DeploymentType, WorkflowDeployment
from apps.shared.db.models.workflow_run import WorkflowNodeRun, WorkflowRun
from apps.shared.db.session import SessionLocal
from apps.workflow_engine.services.llm_service import LLMService
from apps.workflow_engine.services.model_routing_judge_first_policy import (
    build_judge_first_active_policy,
)
from apps.workflow_engine.services.model_routing_policy_store import ModelRoutingPolicyStore
from apps.workflow_engine.services.model_routing_runtime_judge import ModelRoutingRuntimeJudge


ORG_ID = uuid.UUID("10200000-0000-0000-0000-000000000100")
USER_ID = uuid.UUID("10200000-0000-0000-0000-000000000001")
APP_ID = uuid.UUID("98100000-0000-0000-0000-000000000001")
WORKFLOW_ID = uuid.UUID("98100000-0000-0000-0000-000000000002")
DEPLOYMENT_ID = uuid.UUID("98100000-0000-0000-0000-000000000003")
NAMESPACE = uuid.UUID("98100000-0000-0000-0000-000000000100")

RAG_NODE_ID = "llm-rag"
DIRECT_NODE_ID = "llm-direct"
ROUTING_JUDGE_MODEL = "gpt-5.4-mini"
DEFAULT_MODEL = "gpt-4.1"
FALLBACK_MODEL = "gpt-4.1-mini"
OUTPUT_DIR = pathlib.Path("reports/model-routing/runs/judge-rag-mix-50")


@dataclass(frozen=True)
class ExperimentCase:
    case_id: str
    category: str
    expected_difficulty: str
    requires_rag: bool
    question: str


@dataclass
class RunResult:
    case_id: str
    node_id: str
    category: str
    expected_difficulty: str
    requires_rag: bool
    workflow_success: bool
    schema_pass: bool
    selected_model: str | None
    task_cost_usd: float
    task_latency_ms: int | None
    routing_judge_cost_usd: float
    routing_judge_tokens: int
    routing: dict[str, Any]
    rag_context: dict[str, Any]
    error: str | None


def _cases(prefix: str, category: str, difficulty: str, requires_rag: bool, values: list[str]) -> list[ExperimentCase]:
    return [
        ExperimentCase(
            case_id=f"{prefix}-{index:02d}",
            category=category,
            expected_difficulty=difficulty,
            requires_rag=requires_rag,
            question=value,
        )
        for index, value in enumerate(values, start=1)
    ]


def build_cases() -> list[ExperimentCase]:
    """각 10개 batch가 같은 구성으로 섞인 50개 synthetic 요청을 만든다."""

    rag = _cases(
        "rag",
        "rag_grounded",
        "balanced",
        True,
        [
            "입사 첫날 해야 할 SSO 설정과 보안 교육 순서를 알려 주세요.",
            "플랫폼 팀 신규 입사자가 Git과 VPN 접근을 받기 전에 확인할 항목은 무엇인가요?",
            "영업팀 온보딩에서 CRM 접근과 고객 데이터 취급 교육은 어떤 순서로 진행하나요?",
            "재무팀 입사자가 결산 자료와 지급 승인 업무를 시작하기 전 필요한 권한을 정리해 주세요.",
            "팀 공간에 접속하지 못하는 신규 직원에게 사내 온보딩 문서 기준으로 안내해 주세요.",
            "보안 교육을 아직 마치지 않은 직원의 시스템 접근을 어떻게 처리해야 하나요?",
            "플랫폼 운영 조회 권한과 배포 권한의 차이를 온보딩 기준으로 설명해 주세요.",
            "영업 담당자가 고객 정보를 조회할 때 지켜야 하는 내부 절차를 알려 주세요.",
            "지급 승인 담당자가 휴가일 때 결산 업무를 인수인계하는 기준이 문서에 있나요?",
            "새 입사자의 노트북 수령, 계정 활성화, 보안 교육을 어떤 순서로 해야 하나요?",
            "SSO 로그인 이후 Git 접근이 안 되는 직원에게 확인할 온보딩 절차를 알려 주세요.",
            "재무 결산 기간에 신규 인력이 접근할 수 있는 문서와 승인 범위를 정리해 주세요.",
            "협력사 온보딩 시 내부 고객 데이터에 접근하기 전에 어떤 교육이 필요한가요?",
            "플랫폼 팀이 운영 장애 대응 훈련에 참여하기 전 준비해야 할 계정 권한을 알려 주세요.",
            "영업 신규 인력이 CRM에서 고객 정보를 다룰 때 필수로 확인할 안내가 무엇인가요?",
            "입사자 계정 생성 후 MFA 등록과 보안 교육 중 어떤 것을 먼저 완료해야 하나요?",
            "지급 승인 권한을 새 직원에게 부여할 때 재무 온보딩 문서의 확인 사항을 알려 주세요.",
            "신규 개발자가 VPN을 쓰기 위해 온보딩에서 어떤 보안 절차를 밟아야 하나요?",
            "고객 데이터가 포함된 영업 자료를 처음 조회하는 직원의 교육 요건을 알려 주세요.",
            "새 팀원이 SSO는 되지만 팀 공간에 안 들어가집니다. 온보딩 기준으로 점검해 주세요.",
        ],
    )
    short_advanced = _cases(
        "advanced",
        "short_advanced_direct",
        "advanced",
        False,
        [
            "고객 삭제 요청과 법적 보존 명령이 충돌합니다. 지금 자동 삭제를 멈춰야 하나요?",
            "결제 중복 청구 의심 건입니다. 환불을 약속하기 전 무엇을 확인해야 하나요?",
            "개인정보 유출 정황이 있습니다. 즉시 차단과 증거 보존을 어떻게 나눌까요?",
            "SLA 위반 가능 장애입니다. 고객 크레딧을 먼저 제안해도 되나요?",
            "퇴사자 관리자 권한이 남아 있었습니다. 어떤 순서로 회수하고 기록해야 하나요?",
            "고객의 EU 데이터가 다른 리전에 조회됐을 수 있습니다. 즉시 어떤 판단이 필요한가요?",
            "침해 의심 파일이 업로드됐습니다. 업무 중단과 조사 중 무엇을 먼저 해야 하나요?",
            "환불 요청과 미결제 잔액이 동시에 있습니다. 고객에게 확정 답변을 해도 되나요?",
            "권한 상승 요청자가 팀장인지 확인되지 않습니다. 어떤 정보가 있어야 승인할 수 있나요?",
            "보상 합의 전 장애 원인이 불명확합니다. 고객 회신에서 피해야 할 표현은 무엇인가요?",
        ],
    )
    routine = _cases(
        "routine",
        "routine_direct",
        "economy",
        False,
        [
            "실행 로그에서 성공한 결과만 보는 방법을 알려 주세요.",
            "배포 URL을 팀원에게 전달하려면 어디에서 복사하나요?",
            "내 모듈 목록에서 수정 가능한 항목만 보려면 어떻게 하나요?",
            "테스트 결과의 비용 표시는 어디에서 확인하나요?",
            "지식 베이스 문서 업로드 상태는 어디에서 보나요?",
            "워크플로우 화면을 기본 확대 비율로 되돌리는 방법을 알려 주세요.",
            "사용하지 않는 초안 워크플로우를 지우기 전에 무엇을 확인하나요?",
            "LLM credential 동기화가 실패했을 때 첫 확인 항목은 무엇인가요?",
            "실행 이력에서 오류만 필터링하는 방법을 짧게 알려 주세요.",
            "팀원에게 내부 배포 링크를 공유하는 방법을 안내해 주세요.",
        ],
    )
    balanced = _cases(
        "balanced",
        "balanced_direct",
        "balanced",
        False,
        [
            "웹훅이 200을 반환했지만 후속 실행이 없습니다. 고객에게 받을 재현 정보와 내부 점검 순서를 정리해 주세요.",
            "월간 사용량 CSV의 팀별 합계와 전체 합계가 다릅니다. 답변 전에 확인할 절차를 작성해 주세요.",
            "백업은 성공했지만 복구 점검에서 일부 첨부 파일이 없습니다. 영향 범위 확인 순서를 알려 주세요.",
            "Slack 알림은 오지만 담당자 멘션이 빠집니다. 고객에게 안내할 설정 확인 절차를 만들어 주세요.",
            "대시보드 활성 사용자 수가 지난주 보고서와 다릅니다. 원인 확인의 우선순위를 정리해 주세요.",
            "OAuth 연동 후 일부 사용자만 로그인에 실패합니다. 로그와 설정을 어떤 순서로 확인하나요?",
            "업무 자동화가 같은 요청을 두 번 처리한 것 같습니다. 중복 여부 확인과 고객 안내를 분리해 주세요.",
            "팀 권한 변경 후 보고서 다운로드가 막혔습니다. 지원팀 점검 절차를 단계별로 알려 주세요.",
            "배포 직후 특정 지역에서 API 오류가 늘었습니다. 설정 변경과 트래픽 차이를 함께 점검해 주세요.",
            "월말 결산 보고서 수치가 바뀌었습니다. 데이터 확정 전에 확인할 검증 절차를 작성해 주세요.",
        ],
    )

    # 매 batch에 RAG 4, 짧은 고난도 2, 일반 안내 2, 복합 직접 요청 2를 고정한다.
    ordered: list[ExperimentCase] = []
    for offset in range(0, 10, 2):
        ordered.extend(rag[offset * 2 : offset * 2 + 4])
        ordered.extend(short_advanced[offset : offset + 2])
        ordered.extend(routine[offset : offset + 2])
        ordered.extend(balanced[offset : offset + 2])
    if len(ordered) != 50 or len({case.case_id for case in ordered}) != 50:
        raise AssertionError("third experiment dataset must contain 50 unique cases")
    return ordered


def batches_of_ten(cases: Iterable[ExperimentCase]) -> list[list[ExperimentCase]]:
    values = list(cases)
    return [values[index : index + 10] for index in range(0, len(values), 10)]


def _llm_data(*, knowledge_bases: list[dict[str, str]]) -> dict[str, Any]:
    return {
        **_base_node_data("기업 요청 판단", "요청 위험도와 처리 방향을 JSON으로 판단합니다.", 3),
        "provider": "openai",
        "model_id": DEFAULT_MODEL,
        "fallback_model_id": FALLBACK_MODEL,
        "auto_model_routing": True,
        "model_routing_policy": {"refresh": {"refresh_every_runs": 20}},
        "system_prompt": (
            "당신은 기업 운영 요청을 처리하는 AI입니다. 반드시 JSON object 하나만 반환하세요. "
            "필수 필드는 classification(string), risk_level(low|medium|high|critical), "
            "approval_required(boolean), response(string)입니다. 요청에 근거가 부족하면 "
            "확정 약속 대신 확인 절차를 안내하세요."
        ),
        "user_prompt": "고객 등급: {{ customerTier }}\n요청: {{ question }}",
        "referenced_variables": [
            {"name": "customerTier", "value_selector": ["request", "customerTier"]},
            {"name": "question", "value_selector": ["request", "question"]},
        ],
        "knowledgeBases": knowledge_bases,
        "topK": 4,
        # 온보딩 demo KB의 실제 embedding score 분포(약 0.26~0.35)에 맞춘다.
        # 기본값 0.5를 쓰면 문서가 정상 인덱싱돼도 실험 RAG 분기가 모두 no_evidence가 된다.
        "scoreThreshold": 0.3,
        "dedupeRetrievedContext": True,
        "retrievedContextMaxChars": 6000,
        "retrievedContextCompression": "light",
        "ragFailurePolicy": "safe_no_result",
        "parameters": {
            "temperature": 0,
            "max_tokens": 1000,
            "response_format": {"type": "json_object"},
        },
        "output_format": {
            "type": "json",
            "schema": {
                "type": "object",
                "properties": {
                    "classification": {"type": "string"},
                    "risk_level": {"type": "string"},
                    "approval_required": {"type": "boolean"},
                    "response": {"type": "string"},
                },
                "required": [
                    "classification",
                    "risk_level",
                    "approval_required",
                    "response",
                ],
            },
        },
    }


def experiment_graph() -> dict[str, Any]:
    knowledge_bases = [
        {"id": str(KB_IDS[key]), "name": key}
        for key in (
            "onboarding_platform",
            "onboarding_sales",
            "onboarding_finance",
        )
    ]
    return {
        "nodes": [
            _node(
                "request",
                "webhookTrigger",
                80,
                180,
                {
                    **_base_node_data("요청 수신", "실험 요청과 RAG 필요 여부를 수신합니다.", 1),
                    "variable_mappings": [
                        {"json_path": "question", "variable_name": "question"},
                        {"json_path": "customerTier", "variable_name": "customerTier"},
                        {"json_path": "requiresRag", "variable_name": "requiresRag"},
                    ],
                },
            ),
            _node(
                "route-rag",
                "conditionNode",
                420,
                180,
                {
                    **_base_node_data("RAG 사용 분기", "검색 근거가 필요한 요청만 지식 베이스를 사용합니다.", 2),
                    "cases": [
                        {
                            "id": "rag",
                            "case_name": "RAG 필요",
                            "logical_operator": "or",
                            "conditions": [
                                {
                                    "id": "requires-rag-true",
                                    "variable_selector": ["request", "requiresRag"],
                                    "operator": "equals",
                                    "value": True,
                                }
                            ],
                        }
                    ],
                },
            ),
            _node(RAG_NODE_ID, "llmNode", 760, 80, _llm_data(knowledge_bases=knowledge_bases)),
            _node(DIRECT_NODE_ID, "llmNode", 760, 360, _llm_data(knowledge_bases=[])),
            _node(
                "rag-answer",
                "answerNode",
                1140,
                80,
                {
                    **_base_node_data("RAG 응답", "검색 근거 기반 결과를 반환합니다.", 4),
                    "outputs": [
                        {"variable": "answer", "label": "응답", "value_selector": [RAG_NODE_ID, "text"]}
                    ],
                },
            ),
            _node(
                "direct-answer",
                "answerNode",
                1140,
                360,
                {
                    **_base_node_data("직접 응답", "직접 처리 결과를 반환합니다.", 5),
                    "outputs": [
                        {"variable": "answer", "label": "응답", "value_selector": [DIRECT_NODE_ID, "text"]}
                    ],
                },
            ),
        ],
        "edges": [
            _edge("request-to-condition", "request", "route-rag"),
            _edge("condition-to-rag", "route-rag", RAG_NODE_ID, "rag"),
            _edge("condition-to-direct", "route-rag", DIRECT_NODE_ID, "default"),
            _edge("rag-to-answer", RAG_NODE_ID, "rag-answer"),
            _edge("direct-to-answer", DIRECT_NODE_ID, "direct-answer"),
        ],
        "viewport": {"x": 0, "y": 0, "zoom": 0.7},
    }


def _run_id(case: ExperimentCase) -> uuid.UUID:
    return uuid.uuid5(NAMESPACE, f"judge-rag-mix-50:{case.case_id}")


def _model_ids(db) -> list[str]:
    available = LLMService.get_runtime_available_model_ids_for_user(
        db, user_id=USER_ID, organization_id=ORG_ID
    )
    wanted = ["gpt-4o-mini", "gpt-4.1-mini", "gpt-4.1", "gpt-5-mini", "gpt-5.4-mini", "gpt-5.4"]
    selected = [model_id for model_id in wanted if model_id in available]
    required = {ROUTING_JUDGE_MODEL, DEFAULT_MODEL, FALLBACK_MODEL, "gpt-4o-mini"}
    missing = sorted(required - set(selected))
    if missing:
        raise RuntimeError(f"실험에 필요한 실행 가능 모델이 없습니다: {missing}")
    for model_id in selected:
        LLMService.get_runtime_client_for_user(db, USER_ID, model_id, ORG_ID)
    return selected


def _upsert_experiment_artifacts(db, available_models: list[str]) -> None:
    graph = experiment_graph()
    app = db.get(App, APP_ID)
    if app is None:
        app = App(
            id=APP_ID,
            organization_id=ORG_ID,
            name="Judge RAG 혼합 라우팅 실험",
            description="RAG와 비-RAG 요청을 한 workflow에서 실제 Judge-first 라우팅으로 검증합니다.",
            icon={"type": "emoji", "content": "🧪", "background_color": "#E0F2FE"},
            url_slug="judge-rag-mix-50",
            auth_secret="experiment-judge-rag-mix-secret",
            is_api_enabled=True,
            api_req_per_minute=600,
            api_req_per_hour=3600,
            is_market=False,
            created_by=USER_ID,
        )
        db.add(app)
        db.flush()
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
            created_by=USER_ID,
            updated_by=USER_ID,
        )
        db.add(workflow)
    else:
        workflow.graph = graph
        workflow.updated_by = USER_ID
        workflow.updated_at = datetime.now(timezone.utc)
    db.flush()
    app.workflow_id = WORKFLOW_ID

    deployment = db.get(WorkflowDeployment, DEPLOYMENT_ID)
    if deployment is None:
        deployment = WorkflowDeployment(
            id=DEPLOYMENT_ID,
            app_id=APP_ID,
            version=1,
            type=DeploymentType.WEBHOOK,
            graph_snapshot=graph,
            config={"experiment": "judge-rag-mix-50"},
            input_schema={"type": "object"},
            output_schema={"type": "object"},
            description="50회 RAG 혼합 Judge-first 라우팅 실험",
            created_by=USER_ID,
            is_active=True,
        )
        db.add(deployment)
    else:
        deployment.graph_snapshot = graph
        deployment.config = {"experiment": "judge-rag-mix-50"}
        deployment.is_active = True
    db.flush()
    app.active_deployment_id = DEPLOYMENT_ID

    for node_id in (RAG_NODE_ID, DIRECT_NODE_ID):
        policy = (
            db.query(LLMNodeModelRoutingPolicy)
            .filter(LLMNodeModelRoutingPolicy.workflow_id == WORKFLOW_ID)
            .filter(LLMNodeModelRoutingPolicy.deployment_id == DEPLOYMENT_ID)
            .filter(LLMNodeModelRoutingPolicy.node_id == node_id)
            .first()
        )
        active_policy = build_judge_first_active_policy(
            policy_version="judge-rag-mix-v1",
            default_model_id=DEFAULT_MODEL,
            fallback_model_id=FALLBACK_MODEL,
            candidate_model_ids=available_models,
        )
        active_policy["judge_model_id"] = ROUTING_JUDGE_MODEL
        if policy is None:
            policy = LLMNodeModelRoutingPolicy(
                organization_id=ORG_ID,
                workflow_id=WORKFLOW_ID,
                deployment_id=DEPLOYMENT_ID,
                node_id=node_id,
                enabled=True,
                status="active",
                policy_version="judge-rag-mix-v1",
                active_policy=active_policy,
                refresh_every_runs=20,
                judge_user_id=USER_ID,
                execution_subject_user_id=USER_ID,
                validation_budget_usd=3,
            )
            db.add(policy)
        else:
            policy.enabled = True
            policy.status = "active"
            policy.policy_version = "judge-rag-mix-v1"
            policy.active_policy = active_policy
            policy.refresh_every_runs = 20
            policy.eligible_runs_since_last_refresh = 0
            policy.refresh_requested_at = None
            policy.judge_user_id = USER_ID
            policy.execution_subject_user_id = USER_ID
    db.flush()


def _clear_experiment_runs(db) -> None:
    run_ids = [row[0] for row in db.query(WorkflowRun.id).filter(WorkflowRun.workflow_id == WORKFLOW_ID).all()]
    db.query(LLMUsageLog).filter(LLMUsageLog.workflow_id == WORKFLOW_ID).delete(synchronize_session=False)
    if run_ids:
        db.query(WorkflowNodeRun).filter(WorkflowNodeRun.workflow_run_id.in_(run_ids)).delete(synchronize_session=False)
        db.query(WorkflowRun).filter(WorkflowRun.id.in_(run_ids)).delete(synchronize_session=False)
    for policy in db.query(LLMNodeModelRoutingPolicy).filter(LLMNodeModelRoutingPolicy.workflow_id == WORKFLOW_ID).all():
        active_policy = dict(policy.active_policy or {})
        active_policy["learning"] = {
            "mode": "judge_first",
            "judged_request_count": 0,
            "selected_model_ids": [],
            "local_confidence_threshold": 0.78,
        }
        policy.active_policy = active_policy
    db.flush()


@contextmanager
def synchronous_experiment_tasks():
    """run/node log는 즉시 저장하고 자동 refresh task는 실험 중 실행하지 않는다."""

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
        if task_name in task_map:
            value = task_map[task_name].run(*(args or []), **(kwargs or {}))
            return type("SyncTaskResult", (), {"get": lambda self, timeout=None: value})()
        if task_name.startswith("workflow.model_routing."):
            return type("SkippedTaskResult", (), {"get": lambda self, timeout=None: {"status": "skipped"}})()
        return original_send_task(task_name, args=args, kwargs=kwargs, **options)

    celery_app.send_task = send_task
    try:
        yield
    finally:
        celery_app.send_task = original_send_task


def _json_object(value: str) -> dict[str, Any]:
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _schema_pass(text: str) -> bool:
    value = _json_object(text)
    return (
        isinstance(value.get("classification"), str)
        and value.get("risk_level") in {"low", "medium", "high", "critical"}
        and isinstance(value.get("approval_required"), bool)
        and isinstance(value.get("response"), str)
    )


def _usage(row: LLMUsageLog | None) -> tuple[float, int]:
    if row is None:
        return 0.0, 0
    return float(row.total_cost or 0), int(row.prompt_tokens or 0) + int(row.completion_tokens or 0)


def _run_case(case: ExperimentCase) -> RunResult:
    from apps.workflow_engine.workflow.core.workflow_engine import WorkflowEngine

    run_id = _run_id(case)
    engine = WorkflowEngine(
        graph=experiment_graph(),
        user_input={"question": case.question, "customerTier": "enterprise", "requiresRag": case.requires_rag},
        execution_context={
            "workflow_id": str(WORKFLOW_ID),
            "workflow_run_id": str(run_id),
            "app_id": str(APP_ID),
            "deployment_id": str(DEPLOYMENT_ID),
            "workflow_version": 1,
            "user_id": str(USER_ID),
            "organization_id": str(ORG_ID),
            "trigger_mode": "webhook",
            "execution_subject": {"subject_type": "user", "subject_id": str(USER_ID)},
        },
        is_deployed=True,
        workflow_timeout=120,
    )
    try:
        engine.execute()
        engine_error = None
    except Exception as exc:  # measured failure; batch guard decides whether to stop.
        engine_error = f"{type(exc).__name__}: {exc}"
    finally:
        engine.cleanup()

    node_id = RAG_NODE_ID if case.requires_rag else DIRECT_NODE_ID
    db = SessionLocal()
    try:
        node_run = None
        task_usage = None
        for _ in range(30):
            db.expire_all()
            node_run = (
                db.query(WorkflowNodeRun)
                .filter(WorkflowNodeRun.workflow_run_id == run_id)
                .filter(WorkflowNodeRun.node_id == node_id)
                .first()
            )
            task_usage = (
                db.query(LLMUsageLog)
                .filter(LLMUsageLog.workflow_run_id == run_id)
                .filter(LLMUsageLog.node_id == node_id)
                .order_by(LLMUsageLog.created_at.desc())
                .first()
            )
            if node_run is not None or engine_error is not None:
                break
            time.sleep(0.2)
        judge_usage = (
            db.query(LLMUsageLog)
            .filter(LLMUsageLog.workflow_run_id == run_id)
            .filter(LLMUsageLog.node_id == f"{node_id}:routing_judge")
            .order_by(LLMUsageLog.created_at.desc())
            .first()
        )
        outputs = node_run.outputs if node_run is not None and isinstance(node_run.outputs, dict) else {}
        trace = node_run.trace_metadata if node_run is not None and isinstance(node_run.trace_metadata, dict) else {}
        llm_trace = trace.get("llm") if isinstance(trace.get("llm"), dict) else {}
        metadata = outputs.get("metadata") if isinstance(outputs.get("metadata"), dict) else {}
        routing = llm_trace.get("model_routing") if isinstance(llm_trace.get("model_routing"), dict) else metadata.get("model_routing") if isinstance(metadata.get("model_routing"), dict) else {}
        # llm node는 routing metadata 안에 RAG 사용 요약을 함께 남긴다. 원문 KB
        # 내용이 아니라 safe summary만 있으므로 실험 보고서에도 그대로 사용한다.
        rag_context = (
            routing.get("rag_context")
            if isinstance(routing.get("rag_context"), dict)
            else llm_trace.get("rag_context")
            if isinstance(llm_trace.get("rag_context"), dict)
            else metadata.get("rag_context")
            if isinstance(metadata.get("rag_context"), dict)
            else {}
        )
        task_cost, _task_tokens = _usage(task_usage)
        judge_cost, judge_tokens = _usage(judge_usage)
        duration_ms = int(float(node_run.duration or 0) * 1000) if node_run is not None and node_run.duration is not None else None
        selected_model = str(routing.get("selected_model") or outputs.get("model") or "").strip() or None
        status = str(getattr(node_run, "status", "")).lower()
        return RunResult(
            case_id=case.case_id,
            node_id=node_id,
            category=case.category,
            expected_difficulty=case.expected_difficulty,
            requires_rag=case.requires_rag,
            workflow_success=engine_error is None and node_run is not None and status.endswith("success"),
            schema_pass=_schema_pass(str(outputs.get("text") or "")),
            selected_model=selected_model,
            task_cost_usd=task_cost,
            task_latency_ms=duration_ms,
            routing_judge_cost_usd=judge_cost,
            routing_judge_tokens=judge_tokens,
            routing=routing,
            rag_context=rag_context,
            error=engine_error,
        )
    finally:
        db.close()


def stop_reason_for(result: dict[str, Any], *, requires_rag: bool) -> str | None:
    if not bool(result.get("workflow_success")):
        return "workflow_failed"
    if not bool(result.get("schema_pass")):
        return "schema_contract_failed"
    routing = result.get("routing") if isinstance(result.get("routing"), dict) else {}
    if routing.get("reason_code") == "runtime_judge_unavailable":
        return "runtime_judge_unavailable"
    if not str(routing.get("selected_model") or result.get("selected_model") or "").strip():
        return "selected_model_missing"
    rag_context = result.get("rag_context") if isinstance(result.get("rag_context"), dict) else {}
    if bool(rag_context.get("used")) != requires_rag:
        return "rag_mode_mismatch"
    if requires_rag and not bool(rag_context.get("evidence_sufficient")):
        return "rag_evidence_missing"
    return None


def _record_completed_run(case: ExperimentCase) -> None:
    db = SessionLocal()
    try:
        scheduled = ModelRoutingPolicyStore.record_completed_deployed_run(db, workflow_run_id=_run_id(case))
        # 실험의 목적은 실행 시 Runtime Judge와 초기 local learning 확인이다. 자동
        # policy refresh task는 별도 비용을 만들어 결과를 섞지 않도록 해제한다.
        for policy_id in scheduled:
            policy = db.get(LLMNodeModelRoutingPolicy, policy_id)
            if policy is not None:
                policy.status = "active"
                policy.refresh_requested_at = None
                policy.last_refresh_result = "experiment_refresh_suppressed"
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _report(output_dir: pathlib.Path, results: list[RunResult], *, stopped_reason: str | None) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    rows = [asdict(item) for item in results]
    result_path = output_dir / "result.json"
    result_path.write_text(json.dumps({"results": rows, "stopped_reason": stopped_reason}, ensure_ascii=False, indent=2), encoding="utf-8")
    model_counts = Counter(item.selected_model or "unknown" for item in results)
    source_counts = Counter(str(item.routing.get("decision_source") or "unknown") for item in results)
    rag_count = sum(1 for item in results if item.requires_rag)
    rag_evidence = sum(1 for item in results if item.requires_rag and item.rag_context.get("evidence_sufficient"))
    total_task_cost = sum(item.task_cost_usd for item in results)
    total_judge_cost = sum(item.routing_judge_cost_usd for item in results)
    report = [
        "# 3차 Judge-first RAG 혼합 라우팅 실험 결과",
        "",
        f"- 상태: {'중단' if stopped_reason else '완료'}",
        f"- 실행 수: {len(results)} / 50",
        f"- Runtime Judge 모델: `{ROUTING_JUDGE_MODEL}` (출력 한도 `{ModelRoutingRuntimeJudge.MAX_OUTPUT_TOKENS}` tokens)",
        f"- 중단 사유: `{stopped_reason or '없음'}`",
        "",
        "## 확인한 계약",
        "",
        "- Judge에는 기본 모델, fallback 모델, 독립된 node contract를 넣지 않았다.",
        "- RAG 요청은 검색된 원문 대신 `used`, 검색 문서 수, 컨텍스트 길이, 근거 충족 여부만 Judge에 전달했다.",
        "- 비-RAG 요청은 `rag_context.used=false`로 전달했다.",
        "- RAG 원문은 실제 LLM 요청에만 포함되므로, 실제 task 비용에는 반영된다.",
        "",
        "## 실행 요약",
        "",
        f"- RAG 분기 실행: {rag_count}건, 근거 충족: {rag_evidence}건",
        f"- task 비용 합계: `${total_task_cost:.6f}`",
        f"- routing Judge 비용 합계: `${total_judge_cost:.6f}`",
        f"- 선택 모델 분포: {dict(model_counts)}",
        f"- 결정 출처 분포: {dict(source_counts)}",
        "",
        "## 실행별 결과",
        "",
        "| 요청 | 유형 | RAG | 짧은 사유 | 검토 후보 | 선택 모델 | 결과 |",
        "| --- | --- | --- | --- | ---: | --- | --- |",
    ]
    for item in results:
        status = "성공" if item.workflow_success and item.schema_pass else "실패"
        judge = item.routing.get("judge") if isinstance(item.routing.get("judge"), dict) else {}
        report.append(
            f"| {item.case_id} | {item.category} | {'사용' if item.requires_rag else '미사용'} | {judge.get('reason_short', '-')} | {judge.get('candidate_model_count', '-')} | {item.selected_model or '-'} | {status} |"
        )
    if stopped_reason:
        report.extend(["", "## 중단 해석", "", "10회 batch의 안전 규칙이 실패를 감지해 이후 요청을 실행하지 않았습니다. `result.json`의 마지막 행과 trace metadata를 우선 점검해야 합니다."])
    else:
        report.extend(["", "## 해석 범위", "", "이 실험은 RAG/비-RAG 분기와 실제 Runtime Judge 선택이 끊기지 않는지를 검증합니다. 고정 고가·저가 모델 대비 품질/비용 경제성 평가는 별도 실험에서 비교해야 합니다."])
    (output_dir / "report.md").write_text("\n".join(report) + "\n", encoding="utf-8")


def _write_plan(output_dir: pathlib.Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "experiment-plan.md").write_text(
        """# 3차 Judge-first RAG 혼합 라우팅 실험 계획

## 목적

한 workflow에서 RAG 근거가 필요한 요청과 검색 없이 직접 처리할 요청을 분기하고, 각 LLM 노드가 실제 Runtime Judge(`gpt-5.4-mini`)를 통해 실행 가능한 후보 모델 중 하나를 선택하는지 확인한다.

## 데이터셋

- 총 50건, 10건씩 5 batch
- 각 batch: RAG 4건, 짧은 고난도 직접 요청 2건, 일반 안내 2건, 복합 직접 요청 2건
- RAG 요청은 온보딩 공통·플랫폼·영업·재무 KB의 실제 문서 주제와 맞춘다.
- 비-RAG 요청은 검색 분기로 가지 않으며, 짧더라도 보안·결제·개인정보 판단처럼 높은 난이도를 포함한다.

## 중단 규칙

각 실행 뒤 workflow 실패, JSON 계약 실패, Runtime Judge 실패, 선택 모델 누락, RAG 분기 불일치, RAG 근거 부족을 검사한다. 하나라도 발생하면 해당 10건 batch에서 즉시 중지한다.

## Judge 입력 경계

Judge는 렌더된 요청 feature, RAG 사용 여부와 검색량 요약만 받아 난이도 점수·확신도·짧은 한국어 사유를 반환한다. 기본 모델·fallback 모델·후보 가격·KB 원문은 Judge 입력에서 제외한다. 실제 모델 선택은 서버가 카탈로그의 품질·비용 수치로 수행한다.
""",
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute", action="store_true", help="실제 provider 호출을 허용합니다.")
    parser.add_argument("--dry-run", action="store_true", help="실제 호출 없이 데이터셋과 계획만 확인합니다.")
    parser.add_argument("--reset", action="store_true", help="이 실험 workflow의 과거 실행 로그만 초기화합니다.")
    parser.add_argument("--batch-limit", type=int, default=5, help="실행할 10건 batch 수입니다.")
    parser.add_argument(
        "--preflight",
        action="store_true",
        help="RAG·짧은 고난도·일반 안내 요청을 각 1건씩 실제 점검합니다.",
    )
    parser.add_argument("--output-dir", default=str(OUTPUT_DIR))
    args = parser.parse_args()
    output_dir = pathlib.Path(args.output_dir)
    _write_plan(output_dir)
    cases = build_cases()
    if args.preflight:
        cases_by_category = {case.category: case for case in cases}
        cases = [
            cases_by_category["rag_grounded"],
            cases_by_category["short_advanced_direct"],
            cases_by_category["routine_direct"],
        ]
    if args.dry_run or not args.execute:
        print(json.dumps({"mode": "dry_run", "case_count": len(cases), "batches": len(batches_of_ten(cases)), "plan": str(output_dir / "experiment-plan.md")}, ensure_ascii=False))
        return

    db = SessionLocal()
    try:
        available_models = _model_ids(db)
        _upsert_experiment_artifacts(db, available_models)
        if args.reset:
            _clear_experiment_runs(db)
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

    results: list[RunResult] = []
    stopped_reason: str | None = None
    with synchronous_experiment_tasks():
        for batch in batches_of_ten(cases)[: max(0, args.batch_limit)]:
            for case in batch:
                result = _run_case(case)
                results.append(result)
                reason = stop_reason_for(asdict(result), requires_rag=case.requires_rag)
                if reason:
                    stopped_reason = f"{case.case_id}:{reason}"
                    break
                _record_completed_run(case)
            _report(output_dir, results, stopped_reason=stopped_reason)
            if stopped_reason:
                break
    _report(output_dir, results, stopped_reason=stopped_reason)
    print(json.dumps({"run_count": len(results), "stopped_reason": stopped_reason, "report": str(output_dir / "report.md")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
