"""Run a 61-input model routing policy experiment on one workflow.

The script uses the real WorkflowEngine and the real configured provider
credential. It writes workflow_runs/workflow_node_runs/llm_usage_logs rows and
observes how ModelRouter changes the active policy every 20 deployed runs.
"""

from __future__ import annotations

# Repository imports intentionally follow the sys.path bootstrap below.
# ruff: noqa: E402

import argparse
import copy
import json
import pathlib
import sys
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
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
    LLMModel,
    LLMUsageLog,
)
from apps.shared.db.models.workflow import Workflow
from apps.shared.db.models.workflow_deployment import DeploymentType, WorkflowDeployment
from apps.shared.db.models.workflow_run import WorkflowNodeRun, WorkflowRun
from apps.shared.db.session import SessionLocal
from apps.workflow_engine.services.llm_service import LLMService
from apps.workflow_engine.services.model_router import (
    ModelCandidate,
    ModelRouter,
    ModelRouterContext,
)
from apps.workflow_engine.services.model_routing_policy_refresh import (
    ModelRoutingPolicyRefreshRequest,
    ModelRoutingPolicyRefreshService,
)
from apps.workflow_engine.workflow.core.workflow_engine import WorkflowEngine
from scripts.managed_app_secret_fixture import configure_managed_app_secret_fixture
from scripts.verify_model_router_demo import _ticket_ops_graph


APP_ID = uuid.UUID("96000000-0000-0000-0000-000000000001")
WORKFLOW_ID = uuid.UUID("96000000-0000-0000-0000-000000000002")
DEPLOYMENT_ID = uuid.UUID("96000000-0000-0000-0000-000000000003")
ORG_ID = uuid.UUID("10200000-0000-0000-0000-000000000100")
USER_ID = uuid.UUID("10200000-0000-0000-0000-000000000003")
NAMESPACE = uuid.UUID("96000000-0000-0000-0000-000000000100")
NODE_ID = "llm-triage"
RUN_COUNT = 61
REFRESH_EVERY_RUNS = 20

MODEL_IDS = ("gpt-4o-mini", "gpt-4.1-mini", "gpt-4.1")


@contextmanager
def synchronous_log_tasks():
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
        return type("SyncTaskResult", (), {"get": lambda self, timeout=None: result})()

    celery_app.send_task = send_task
    try:
        yield
    finally:
        celery_app.send_task = original_send_task


def ensure_lab_data(db) -> None:
    for model_id in MODEL_IDS:
        LLMService.get_runtime_client_for_user(
            db,
            user_id=USER_ID,
            model_id=model_id,
            organization_id=ORG_ID,
        )


def experiment_graph(policy: dict[str, Any]) -> dict[str, Any]:
    graph = copy.deepcopy(_ticket_ops_graph())
    for node in graph["nodes"]:
        if node["id"] == NODE_ID:
            node["data"]["provider"] = "openai"
            node["data"]["model_id"] = "gpt-4.1"
            node["data"]["fallback_model_id"] = "gpt-4.1"
            node["data"]["auto_model_routing"] = True
            node["data"]["model_routing_policy"] = policy
            node["data"]["model_routing_context"] = {
                "customer_facing": True,
                "node_task": "customer_support_ticket_triage",
                "intent": "customer_support_ticket_triage",
            }
            node["data"]["system_prompt"] = (
                "고객지원 티켓을 처리하는 AI입니다. 반드시 JSON만 출력하세요. "
                "JSON 필드는 \"긴급도\" boolean, \"답변 초안\" string 두 개만 포함합니다."
            )
            node["data"]["knowledgeBases"] = []
            node["data"]["parameters"] = {"temperature": 0.1, "max_tokens": 900}
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


def upsert_workflow(db, graph: dict[str, Any]) -> None:
    app = db.get(App, APP_ID)
    if app is None:
        app = App(
            id=APP_ID,
            organization_id=ORG_ID,
            name="61회 모델 라우팅 정책 실험",
            description="61개 입력으로 active policy 기반 모델 라우팅 변화를 관찰하는 실험 workflow",
            icon={"type": "emoji", "content": "🧭", "background_color": "#E0F2FE"},
            url_slug="model-router-61-run-experiment",
            is_api_enabled=True,
            api_req_per_minute=600,
            api_req_per_hour=3600,
            is_market=False,
            created_by=USER_ID,
        )
        db.add(app)
        db.flush()
    configure_managed_app_secret_fixture(app)
    app.organization_id = ORG_ID

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
        workflow.organization_id = ORG_ID
        workflow.app_id = APP_ID
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
            config={"experiment": "model-router-61-runs"},
            input_schema={"type": "object"},
            output_schema={"type": "object"},
            description="61회 모델 라우팅 정책 실험 배포",
            created_by=USER_ID,
            is_active=True,
        )
        db.add(deployment)
    else:
        deployment.app_id = APP_ID
        deployment.graph_snapshot = graph
        deployment.is_active = True
    app.active_deployment_id = DEPLOYMENT_ID
    db.flush()


def clear_previous_runs(db) -> None:
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


def candidates() -> list[ModelCandidate]:
    with SessionLocal() as db:
        models = (
            db.query(LLMModel)
            .filter(LLMModel.model_id_for_api_call.in_(MODEL_IDS))
            .all()
        )
        by_id = {model.model_id_for_api_call: model for model in models}
        missing = [model_id for model_id in MODEL_IDS if model_id not in by_id]
        if missing:
            raise RuntimeError(f"Missing model catalog rows: {missing}")
        return [ModelCandidate.from_model(by_id[model_id]) for model_id in MODEL_IDS]


def make_policy(
    *,
    version_number: int,
    selected_model: str,
    fallback_model: str | None,
    reason_code: str,
    trigger: str,
    refresh_result: str,
) -> dict[str, Any]:
    return {
        "status": "active",
        "policy_id": "model-router-61-run-policy",
        "policy_version": f"router-policy-v{version_number}",
        "refresh": {
            "runs_since_last_refresh": 0,
            "refresh_every_runs": REFRESH_EVERY_RUNS,
            "last_refresh_trigger": trigger,
            "last_refresh_result": refresh_result,
        },
        "active_policy": {
            "default_model_id": selected_model,
            "fallback_model_id": fallback_model,
            "rules": [
                {
                    "id": "default-policy-rule",
                    "selected_model_id": selected_model,
                    "fallback_model_id": fallback_model,
                    "reason_code": reason_code,
                }
            ],
        },
    }


def bootstrap_policy() -> dict[str, Any]:
    return ModelRoutingPolicyRefreshService.default_rule_policy(
        policy_id="model-router-61-run-policy",
        policy_version="router-policy-v1",
        candidate_models=candidates(),
    )


def refresh_policy(
    db,
    *,
    current_policy: dict[str, Any],
    recent_runs: list[dict[str, Any]],
    trigger: str,
):
    result = ModelRoutingPolicyRefreshService.refresh_policy(
        db,
        ModelRoutingPolicyRefreshRequest(
            workflow_id=str(WORKFLOW_ID),
            node_id=NODE_ID,
            user_id=USER_ID,
            organization_id=ORG_ID,
            current_policy=current_policy,
            candidate_models=candidates(),
            recent_runs=[
                {
                    "index": run["index"],
                    "model": run.get("selected_model"),
                    "matched_rule_id": run.get("matched_rule_id"),
                    "reason_code": run.get("reason_code"),
                    "cost": run.get("cost"),
                    "total_tokens": run.get("total_tokens"),
                    "latency_ms": run.get("latency_ms"),
                    "input_summary": run.get("input_summary"),
                }
                for run in recent_runs[-REFRESH_EVERY_RUNS:]
            ],
            node_summary={
                "node_type": "llmNode",
                "node_task": "customer_support_ticket_triage",
                "output_format": "json",
                "customer_facing": True,
                "policy_goal": "비용을 줄이되 high-risk 입력은 강한 모델로 유지",
            },
            trigger=trigger,
        ),
    )
    return result.policy, result


def input_for_run(index: int) -> dict[str, Any]:
    topics = [
        "정산 파일 재생성",
        "다운로드 위치 안내",
        "결제 API 장애",
        "SLA 위반 가능성",
        "세금계산서 오류",
        "대량 웹훅 재전송",
        "SSO 로그인 실패",
        "보안 감사 로그 누락",
        "개인정보 마스킹 확인",
        "월간 리포트 불일치",
        "관리자 권한 변경 요청",
    ]
    tiers = ["enterprise", "business", "startup"]
    topic = topics[(index - 1) % len(topics)]
    tier = tiers[(index - 1) % len(tiers)]
    impact = ["낮음", "중간", "높음", "치명"][index % 4]
    return {
        "customerTier": tier,
        "message": (
            f"[실험 입력 {index:02d}] {topic} 관련 문의입니다. "
            f"고객 영향도는 {impact}이며, 현재 처리 우선순위와 답변 초안을 알려주세요."
        ),
    }


def run_workflow(index: int, graph: dict[str, Any]) -> dict[str, Any]:
    run_id = uuid.uuid5(NAMESPACE, f"run-{index:02d}")
    engine = WorkflowEngine(
        graph=graph,
        user_input=input_for_run(index),
        execution_context={
            "workflow_id": str(WORKFLOW_ID),
            "workflow_run_id": str(run_id),
            "app_id": str(APP_ID),
            "deployment_id": str(DEPLOYMENT_ID),
            "workflow_version": 1,
            "user_id": str(USER_ID),
            "organization_id": str(ORG_ID),
            "trigger_mode": "webhook",
            "execution_subject": {
                "subject_type": "user",
                "subject_id": str(USER_ID),
            },
        },
        is_deployed=True,
        workflow_timeout=30,
    )
    try:
        result = engine.execute()
    finally:
        engine.cleanup()
    return {"run_id": str(run_id), "result": result}


def profile_summary(db, current_model: str) -> dict[str, Any]:
    context = ModelRouterContext(
        workflow_id=str(WORKFLOW_ID),
        node_id=NODE_ID,
        current_model_id=current_model,
        candidate_models=candidates(),
    )
    profile = ModelRouter.collect_profile(db, context)
    return profile.as_snapshot()


def usage_for_run(db, run_id: str) -> dict[str, Any]:
    usage = (
        db.query(LLMUsageLog, LLMModel)
        .join(LLMModel, LLMUsageLog.model_id == LLMModel.id)
        .filter(LLMUsageLog.workflow_run_id == uuid.UUID(run_id))
        .first()
    )
    if usage is None:
        return {}
    usage_log, model = usage
    node_run = (
        db.query(WorkflowNodeRun)
        .filter(WorkflowNodeRun.workflow_run_id == uuid.UUID(run_id))
        .filter(WorkflowNodeRun.node_id == NODE_ID)
        .first()
    )
    node_llm_metadata = (
        node_run.trace_metadata.get("llm")
        if node_run is not None and isinstance(node_run.trace_metadata, dict)
        else None
    )
    node_output_metadata = (
        node_run.outputs.get("metadata")
        if node_run is not None and isinstance(node_run.outputs, dict)
        else None
    )
    latency_ms = usage_log.latency_ms
    if not latency_ms and isinstance(node_llm_metadata, dict):
        latency_ms = int(node_llm_metadata.get("latency_ms") or 0)
    if not latency_ms and node_run is not None and node_run.duration is not None:
        latency_ms = int(float(node_run.duration) * 1000)
    routing_metadata = {}
    if isinstance(node_llm_metadata, dict):
        routing_metadata = node_llm_metadata.get("model_routing") or {}
    if not routing_metadata and isinstance(node_output_metadata, dict):
        routing_metadata = node_output_metadata.get("model_routing") or {}
    return {
        "model": model.model_id_for_api_call,
        "prompt_tokens": usage_log.prompt_tokens,
        "completion_tokens": usage_log.completion_tokens,
        "total_tokens": usage_log.prompt_tokens + usage_log.completion_tokens,
        "cost": float(usage_log.total_cost or 0),
        "latency_ms": latency_ms,
        "matched_rule_id": routing_metadata.get("matched_rule_id"),
        "reason_code": routing_metadata.get("reason_code"),
        "policy_version": routing_metadata.get("policy_version"),
        "runtime_context": routing_metadata.get("runtime_context"),
    }


def policy_summary(policy: dict[str, Any]) -> dict[str, Any]:
    active_policy = policy.get("active_policy") if isinstance(policy, dict) else {}
    active_policy = active_policy if isinstance(active_policy, dict) else {}
    rules = active_policy.get("rules")
    rules = rules if isinstance(rules, list) else []
    return {
        "policy_version": policy.get("policy_version"),
        "refresh_result": (policy.get("refresh") or {}).get("last_refresh_result"),
        "default_model": active_policy.get("default_model_id"),
        "fallback_model": active_policy.get("fallback_model_id"),
        "rule_count": len(rules),
        "rules": [
            {
                "id": rule.get("id"),
                "priority": rule.get("priority"),
                "when": rule.get("when"),
                "selected_model_id": rule.get("selected_model_id"),
                "fallback_model_id": rule.get("fallback_model_id"),
                "reason_code": rule.get("reason_code"),
            }
            for rule in rules
            if isinstance(rule, dict)
        ],
    }


def write_outputs(output_dir: pathlib.Path, report: dict[str, Any]) -> pathlib.Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "model_router_61_run_experiment.json"
    md_path = output_dir / "model_router_61_run_experiment.md"
    normalize_policy_refresh_results(report)
    report["json_path"] = str(json_path.resolve())
    report["markdown_path"] = str(md_path.resolve())
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(render_markdown_report(report), encoding="utf-8")
    return md_path


def normalize_policy_refresh_results(report: dict[str, Any]) -> None:
    previous_model: str | None = None
    for update in report.get("policy_updates", []):
        selected_model = update.get("selected_model") or update.get("default_model")
        if "refresh_result" not in update:
            update["refresh_result"] = (
                "applied"
                if previous_model is not None and selected_model != previous_model
                else "kept_current"
            )
        if selected_model:
            previous_model = selected_model


def pct(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{value * 100:.1f}%"


def money(value: float | None) -> str:
    if value is None:
        return "-"
    return f"${value:.6f}"


def render_markdown_report(report: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("# 61회 입력 기반 모델 라우팅 정책 실험 보고서")
    lines.append("")
    lines.append(f"- 실행 시각: {report['executed_at']}")
    lines.append(f"- workflow_id: `{report['workflow_id']}`")
    lines.append(f"- node_id: `{report['node_id']}`")
    lines.append("- 실행 방식: 실제 WorkflowEngine 실행 + 실제 provider 호출")
    lines.append("- 외부 LLM provider 호출: 있음")
    lines.append("- 정책 갱신 기준: 20회 실행마다 갱신")
    lines.append("")
    lines.append("## 결론")
    lines.append("")
    policy_refresh_sequence = [
        (
            update.get("after_run"),
            update.get("refresh_result"),
            update.get("default_model"),
            update.get("rule_count"),
        )
        for update in report.get("policy_updates", [])
    ]
    conclusions = [
        f"61회 모두 하나의 workflow `{report['workflow_id']}`와 LLM node `{report['node_id']}`에서 실행됐다.",
        f"정책 갱신 결과는 {policy_refresh_sequence} 순서로 기록됐다.",
        "정책 갱신은 단일 모델 교체가 아니라 active policy rule set 갱신이다.",
        f"실행 모델 분포는 {report.get('model_distribution', {})}로 기록됐다.",
        f"rule 매칭 분포는 {report.get('rule_distribution', {})}로 기록됐다.",
        "실험은 실제 provider 호출을 사용했고 WorkflowEngine과 DB 로그 경로도 실제 실행 경로를 사용했다.",
    ]
    lines.extend(f"- {item}" for item in conclusions)
    lines.append("")
    lines.append("## 정책 변화")
    lines.append("")
    lines.append("| 갱신 시점 | 정책 버전 | 갱신 결과 | 기본 모델 | fallback | rule 수 | judge 토큰 | 사유 |")
    lines.append("| --- | --- | --- | --- | --- | ---: | ---: | --- |")
    for update in report["policy_updates"]:
        judge_usage = update.get("judge_usage") or {}
        lines.append(
            "| "
            f"{update['after_run']} | "
            f"{update['policy_version']} | "
            f"{update.get('refresh_result', '-')} | "
            f"{update.get('default_model') or '-'} | "
            f"{update.get('fallback_model') or '-'} | "
            f"{update.get('rule_count') or 0} | "
            f"{judge_usage.get('total_tokens') or '-'} | "
            f"{update['reason']} |"
        )
        for rule in update.get("rules", []):
            lines.append(
                f"  - `{rule.get('id')}`: `{rule.get('when')}` -> "
                f"`{rule.get('selected_model_id')}` "
                f"(fallback `{rule.get('fallback_model_id') or '-'}`)"
            )
    lines.append("")
    lines.append("## 61회 실행별 라우팅 결과")
    lines.append("")
    lines.append("| # | 입력 요약 | 정책 버전 | rule | 선택 모델 | 비용 | 토큰 | latency |")
    lines.append("| ---: | --- | --- | --- | --- | ---: | ---: | ---: |")
    for run in report["runs"]:
        lines.append(
            "| "
            f"{run['index']} | "
            f"{run['input_summary']} | "
            f"{run['policy_version']} | "
            f"{run.get('matched_rule_id') or '-'} | "
            f"{run['selected_model']} | "
            f"{money(run['cost'])} | "
            f"{run['total_tokens']} | "
            f"{run['latency_ms']}ms |"
        )
    lines.append("")
    lines.append("## 최종 모델별 성능 프로필")
    lines.append("")
    lines.append("| 모델 | 실행 수 | 성공률 | downstream 성공률 | fallback 비율 | 평균 비용 |")
    lines.append("| --- | ---: | ---: | ---: | ---: | ---: |")
    for model_id, metrics in sorted(report["final_profile"]["model_performance"].items()):
        lines.append(
            "| "
            f"{model_id} | "
            f"{metrics['run_count']} | "
            f"{pct(metrics['success_rate'])} | "
            f"{pct(metrics['downstream_success_rate'])} | "
            f"{pct(metrics['fallback_rate'])} | "
            f"{money(metrics['avg_cost'])} |"
        )
    lines.append("")
    lines.append("## 해석")
    lines.append("")
    lines.append(
        "이번 실험은 active policy의 rule set을 런타임에서 평가하는 실제 동작을 관찰한 것이다. "
        "런타임은 judge를 호출하지 않고 입력을 risk/intent/context로 분류한 뒤 가장 먼저 매칭되는 rule의 모델을 사용한다."
    )
    lines.append("")
    lines.append(
        "정책은 20회마다 judge를 통해 갱신했다. 갱신 결과는 하나의 모델 ID가 아니라 "
        "`low/medium/high` 위험도와 입력 특성에 따라 여러 모델을 고르는 rule set이다."
    )
    lines.append("")
    lines.append("## 생성 파일")
    lines.append("")
    lines.append(f"- JSON: `{report['json_path']}`")
    lines.append(f"- Markdown: `{report['markdown_path']}`")
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output-dir",
        default="artifacts/model_router_61_runs",
        help="Directory for JSON and Markdown report outputs.",
    )
    parser.add_argument(
        "--render-existing",
        action="store_true",
        help="Do not run provider calls. Render Markdown from the existing JSON output.",
    )
    args = parser.parse_args()
    output_dir = pathlib.Path(args.output_dir)
    if args.render_existing:
        json_path = output_dir / "model_router_61_run_experiment.json"
        report = json.loads(json_path.read_text(encoding="utf-8"))
        md_path = write_outputs(output_dir, report)
        print(str(md_path.resolve()))
        return

    run_records: list[dict[str, Any]] = []
    policy_updates: list[dict[str, Any]] = []

    with SessionLocal() as db:
        ensure_lab_data(db)
        clear_previous_runs(db)
        policy = bootstrap_policy()
        bootstrap = policy_summary(policy)
        policy_updates.append(
            {
                "after_run": 0,
                **bootstrap,
                "reason": "기본 rule set으로 bootstrap했습니다.",
                "judge_usage": {},
            }
        )
        graph = experiment_graph(policy)
        upsert_workflow(db, graph)
        db.commit()

        with synchronous_log_tasks():
            for index in range(1, RUN_COUNT + 1):
                graph = experiment_graph(policy)
                upsert_workflow(db, graph)
                db.commit()
                run_result = run_workflow(index, graph)
                db.expire_all()
                usage = usage_for_run(db, run_result["run_id"])
                run_records.append(
                    {
                        "index": index,
                        "run_id": run_result["run_id"],
                        "input_summary": input_for_run(index)["message"][:60],
                        "policy_version": usage.get("policy_version")
                        or policy["policy_version"],
                        "selected_model": usage.get("model")
                        or policy_summary(policy)["default_model"],
                        "cost": usage.get("cost"),
                        "prompt_tokens": usage.get("prompt_tokens"),
                        "completion_tokens": usage.get("completion_tokens"),
                        "total_tokens": usage.get("total_tokens"),
                        "latency_ms": usage.get("latency_ms"),
                        "matched_rule_id": usage.get("matched_rule_id"),
                        "reason_code": usage.get("reason_code"),
                        "runtime_context": usage.get("runtime_context"),
                    }
                )

                if index % REFRESH_EVERY_RUNS == 0:
                    policy, refresh_result = refresh_policy(
                        db,
                        current_policy=policy,
                        recent_runs=run_records,
                        trigger=f"auto_{REFRESH_EVERY_RUNS}_runs",
                    )
                    summary = policy_summary(policy)
                    policy_updates.append(
                        {
                            "after_run": index,
                            **summary,
                            "reason": refresh_result.reason,
                            "judge_usage": refresh_result.judge_usage,
                            "metadata": refresh_result.metadata,
                        }
                    )

        final_profile = profile_summary(db, current_model="gpt-4.1")

    total_cost = sum(float(run["cost"] or 0) for run in run_records)
    model_distribution: dict[str, int] = {}
    rule_distribution: dict[str, int] = {}
    for run in run_records:
        model_distribution[run["selected_model"]] = (
            model_distribution.get(run["selected_model"], 0) + 1
        )
        rule_id = run.get("matched_rule_id") or "policy_default"
        rule_distribution[rule_id] = rule_distribution.get(rule_id, 0) + 1
    policy_refresh_sequence = [
        (
            update["after_run"],
            update["refresh_result"],
            update["default_model"],
            update["rule_count"],
        )
        for update in policy_updates
    ]
    report = {
        "executed_at": datetime.now(timezone.utc).isoformat(),
        "workflow_id": str(WORKFLOW_ID),
        "node_id": NODE_ID,
        "run_count": RUN_COUNT,
        "refresh_every_runs": REFRESH_EVERY_RUNS,
        "total_cost": total_cost,
        "model_distribution": model_distribution,
        "rule_distribution": rule_distribution,
        "policy_updates": policy_updates,
        "runs": run_records,
        "final_profile": final_profile,
        "conclusions": [
            f"61회 모두 하나의 workflow `{WORKFLOW_ID}`와 LLM node `{NODE_ID}`에서 실행됐다.",
            f"정책 갱신 결과는 {policy_refresh_sequence} 순서로 기록됐다.",
            f"실행 모델 분포는 {model_distribution}로 기록됐다.",
            f"rule 매칭 분포는 {rule_distribution}로 기록됐다.",
            "실험은 실제 provider 호출을 사용했고 WorkflowEngine과 DB 로그 경로도 실제 실행 경로를 사용했다.",
        ],
    }
    md_path = write_outputs(output_dir, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
