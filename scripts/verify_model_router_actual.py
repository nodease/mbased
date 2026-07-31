"""실제 LLM provider 호출로 모델 라우터 cheap/mid/high 판정을 검증한다.

이 스크립트는 fake LLM client를 쓰지 않는다. 현재 organization/user가
사용할 수 있는 provider credential과 verified model relation을 확인한 뒤,
실제 provider 응답을 workflow_runs, workflow_node_runs, llm_usage_logs에
기록하고 ModelRouter가 그 운영 로그를 어떻게 해석하는지 확인한다.

기본 실행은 ``--provider auto``이며, OpenAI/Anthropic/Google preset 중
현재 DB에서 실행 가능한 첫 provider를 사용한다. 특정 provider만 쓰는
조직을 검증하려면 ``--provider anthropic`` 또는 ``--provider google``처럼
명시한다.
"""

from __future__ import annotations

# Repository imports intentionally follow the sys.path bootstrap below.
# ruff: noqa: E402

import argparse
import asyncio
import json
import pathlib
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Sequence

ROOT = pathlib.Path(__file__).resolve().parents[1]
PARENT_OF_ROOT = ROOT.parent
for path in (ROOT, PARENT_OF_ROOT):
    if str(path) not in sys.path:
        sys.path.append(str(path))

from apps.shared.db.models.app import App
from apps.shared.db.models.llm import LLMModel
from apps.shared.db.models.llm import LLMUsageLog
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
from apps.workflow_engine.services.llm_service import (
    LLMCredentialNotAvailableError,
    LLMService,
)
from apps.workflow_engine.services.model_router import ModelRouter, ModelRouterContext
from scripts.managed_app_secret_fixture import configure_managed_app_secret_fixture


DEFAULT_ORG_ID = uuid.UUID("10200000-0000-0000-0000-000000000100")
DEFAULT_USER_ID = uuid.UUID("10200000-0000-0000-0000-000000000003")
DEFAULT_APP_ID = uuid.UUID("92000000-0000-0000-0000-000000000001")
DEFAULT_WORKFLOW_ID = uuid.UUID("92000000-0000-0000-0000-000000000002")
DEFAULT_DEPLOYMENT_ID = uuid.UUID("92000000-0000-0000-0000-000000000003")
DEFAULT_NAMESPACE = uuid.UUID("92000000-0000-0000-0000-000000000100")
NODE_ID = "llm-router-verification"


@dataclass(frozen=True)
class ProviderPreset:
    provider: str
    cheap_model: str
    mid_model: str
    high_model: str
    judge_model: str


MODEL_PRESETS: dict[str, ProviderPreset] = {
    "openai": ProviderPreset(
        provider="openai",
        cheap_model="gpt-4o-mini",
        mid_model="gpt-4.1-mini",
        high_model="gpt-4.1",
        judge_model="gpt-4.1-mini",
    ),
    "anthropic": ProviderPreset(
        provider="anthropic",
        cheap_model="claude-haiku-4-5-20251001",
        mid_model="claude-sonnet-4-5-20250929",
        high_model="claude-opus-4-5-20251101",
        judge_model="claude-sonnet-4-5-20250929",
    ),
    "google": ProviderPreset(
        provider="google",
        cheap_model="gemini-2.5-flash-lite",
        mid_model="gemini-2.5-flash",
        high_model="gemini-2.5-pro",
        judge_model="gemini-2.5-flash",
    ),
}


@dataclass(frozen=True)
class VerificationConfig:
    org_id: uuid.UUID
    user_id: uuid.UUID
    app_id: uuid.UUID
    workflow_id: uuid.UUID
    deployment_id: uuid.UUID
    namespace: uuid.UUID
    execution_provider: str
    cheap_model: str
    mid_model: str
    high_model: str
    judge_model: str

    @property
    def model_order(self) -> list[str]:
        return [self.cheap_model, self.mid_model, self.high_model]


@dataclass(frozen=True)
class QualityJudgement:
    schema_pass: bool
    downstream_pass: bool
    score: float
    reason: str


def _usage(result: dict[str, Any]) -> dict[str, int]:
    usage = result.get("usage") or {}
    return {
        "prompt_tokens": int(usage.get("prompt_tokens") or 0),
        "completion_tokens": int(usage.get("completion_tokens") or 0),
        "total_tokens": int(usage.get("total_tokens") or 0),
    }


def _text(result: dict[str, Any]) -> str:
    if isinstance(result.get("text"), str):
        return result["text"]
    if isinstance(result.get("content"), str):
        return result["content"]
    choices = result.get("choices")
    if isinstance(choices, list) and choices:
        message = choices[0].get("message") if isinstance(choices[0], dict) else None
        if isinstance(message, dict) and isinstance(message.get("content"), str):
            return message["content"]
        if isinstance(choices[0].get("text"), str):
            return choices[0]["text"]
    return json.dumps(result, ensure_ascii=False)[:2000]


def _json_from_text(text: str) -> dict[str, Any]:
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            return {}
        try:
            parsed = json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return {}
    return parsed if isinstance(parsed, dict) else {}


def _expected_contract(scenario: str) -> dict[str, Any]:
    if scenario == "cheap-pass":
        return {
            "description": "간단한 내부 분류 응답",
            "required_fields": ["status", "priority", "reply"],
            "field_constraints": {
                "status": "string",
                "priority": "string",
                "reply": "string",
            },
            "minimum_score": 0.72,
        }
    if scenario == "mid-pass":
        return {
            "description": "운영 리스크 triage 응답",
            "required_fields": [
                "status",
                "priority",
                "risk_score",
                "escalation_required",
                "reply",
            ],
            "field_constraints": {
                "status": "string",
                "priority": "string",
                "risk_score": "number from 0 to 100",
                "escalation_required": "boolean",
                "reply": "string",
            },
            "minimum_score": 0.55,
        }
    return {
        "description": "고객-facing SLA/보상/법무 리스크 응답",
        "required_fields": [
            "status",
            "priority",
            "risk_score",
            "escalation_required",
            "legal_review_required",
            "reply",
        ],
        "field_constraints": {
            "status": "string",
            "priority": "string",
            "risk_score": "number from 0 to 100",
            "escalation_required": "boolean",
            "legal_review_required": "boolean",
            "reply": "string",
        },
        "minimum_score": 0.86,
    }


def _run_id(config: VerificationConfig, scenario: str, index: int) -> uuid.UUID:
    return uuid.uuid5(config.namespace, f"{scenario}-run-{index}")


def _node_run_id(config: VerificationConfig, scenario: str, index: int) -> uuid.UUID:
    return uuid.uuid5(config.namespace, f"{scenario}-node-{index}")


def _ensure_workflow(db, config: VerificationConfig) -> None:
    app = db.get(App, config.app_id)
    if app is None:
        app = App(
            id=config.app_id,
            organization_id=config.org_id,
            name=f"모델 라우터 실제 호출 검증 ({config.execution_provider})",
            description=(
                "실제 provider 호출 로그로 모델 라우팅 cheap/mid/high 판정을 "
                "검증합니다."
            ),
            icon={"type": "emoji", "content": "🧪", "background_color": "#E0F2FE"},
            url_slug=f"model-router-actual-{config.execution_provider}",
            is_api_enabled=True,
            api_req_per_minute=60,
            api_req_per_hour=3600,
            is_market=False,
            created_by=config.user_id,
        )
        db.add(app)
        db.flush()
    configure_managed_app_secret_fixture(app)

    graph = {
        "nodes": [
            {
                "id": NODE_ID,
                "type": "llmNode",
                "position": {"x": 100, "y": 100},
                "data": {
                    "title": "모델 라우터 검증",
                    "provider": config.execution_provider,
                    "model_id": config.high_model,
                    "fallback_model_id": config.high_model,
                    "system_prompt": "짧고 명확한 JSON으로 답합니다.",
                    "user_prompt": "{{ message }}",
                    "parameters": {"temperature": 0, "max_tokens": 96},
                    "knowledgeBases": [],
                },
            }
        ],
        "edges": [],
    }
    workflow = db.get(Workflow, config.workflow_id)
    if workflow is None:
        workflow = Workflow(
            id=config.workflow_id,
            organization_id=config.org_id,
            app_id=config.app_id,
            graph=graph,
            features={},
            env_variables=[],
            runtime_variables=[],
            created_by=config.user_id,
            updated_by=config.user_id,
        )
        db.add(workflow)
    else:
        workflow.organization_id = config.org_id
        workflow.app_id = config.app_id
        workflow.graph = graph
        workflow.updated_by = config.user_id
        workflow.updated_at = datetime.now(timezone.utc)
    db.flush()

    deployment = db.get(WorkflowDeployment, config.deployment_id)
    if deployment is None:
        deployment = WorkflowDeployment(
            id=config.deployment_id,
            app_id=config.app_id,
            version=1,
            type=DeploymentType.WEBHOOK,
            graph_snapshot=graph,
            config={"demo": "model-router-actual"},
            input_schema={"type": "object"},
            output_schema={"type": "object"},
            description="모델 라우터 실제 호출 검증용 배포",
            created_by=config.user_id,
            is_active=True,
        )
        db.add(deployment)
    else:
        deployment.app_id = config.app_id
        deployment.graph_snapshot = graph
        deployment.is_active = True
    app.workflow_id = config.workflow_id
    app.active_deployment_id = config.deployment_id
    db.flush()


def _contract_shape_errors(
    contract: dict[str, Any], parsed_output: dict[str, Any]
) -> list[str]:
    errors: list[str] = []
    for field in contract["required_fields"]:
        if field not in parsed_output:
            errors.append(f"{field} missing")
            continue
        value = parsed_output[field]
        expected = str(contract.get("field_constraints", {}).get(field, ""))
        if expected == "string" and not isinstance(value, str):
            errors.append(f"{field} must be string")
        if expected == "boolean" and not isinstance(value, bool):
            errors.append(f"{field} must be boolean")
        if expected == "number from 0 to 100":
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                errors.append(f"{field} must be number")
            elif value < 0 or value > 100:
                errors.append(f"{field} must be 0..100")
    return errors


def _clear_runs(db, config: VerificationConfig) -> None:
    run_ids = [
        row[0]
        for row in db.query(WorkflowRun.id)
        .filter(WorkflowRun.workflow_id == config.workflow_id)
        .all()
    ]
    db.query(LLMUsageLog).filter(
        LLMUsageLog.workflow_id == config.workflow_id
    ).delete(synchronize_session=False)
    if run_ids:
        db.query(WorkflowNodeRun).filter(
            WorkflowNodeRun.workflow_run_id.in_(run_ids)
        ).delete(synchronize_session=False)
        db.query(WorkflowRun).filter(WorkflowRun.id.in_(run_ids)).delete(
            synchronize_session=False
        )
    db.flush()


async def _invoke_actual(
    config: VerificationConfig, model_id: str, message: str
) -> tuple[str, dict[str, int], float]:
    started = datetime.now(timezone.utc)
    db = SessionLocal()
    try:
        selection = LLMService.get_runtime_client_for_user(
            db,
            config.user_id,
            model_id,
            config.org_id,
        )
        result = await selection.client.invoke(
            [
                {"role": "system", "content": "Return only a compact JSON object."},
                {"role": "user", "content": message},
            ],
            temperature=0,
            max_tokens=96,
        )
        usage = _usage(result)
        return _text(result), usage, (
            datetime.now(timezone.utc) - started
        ).total_seconds()
    finally:
        db.close()


async def _judge_quality(
    config: VerificationConfig,
    *,
    scenario: str,
    input_message: str,
    output_text: str,
) -> QualityJudgement:
    contract = _expected_contract(scenario)
    judge_contract = {
        key: value
        for key, value in contract.items()
        if key != "minimum_score"
    }
    parsed_output = _json_from_text(output_text)
    shape_errors = _contract_shape_errors(contract, parsed_output)
    judge_prompt = {
        "task": "Evaluate whether an LLM node output is safe to use for model routing quality gates.",
        "scenario": scenario,
        "contract": judge_contract,
        "input": input_message,
        "candidate_output": output_text,
        "parsed_output": parsed_output,
        "shape_errors": shape_errors,
        "instructions": [
            "Return only JSON.",
            "schema_pass must be false if shape_errors is not empty.",
            "risk_score uses a 0 to 100 scale, not a 0 to 1 scale.",
            "Do not compare risk_score to the quality score threshold. risk_score is just candidate output data.",
            "downstream_pass means the output can be consumed by downstream workflow nodes without manual repair.",
            "score is a number between 0 and 1.",
            "Do not reward cheaper or faster models. Judge only output quality and contract fit.",
        ],
        "response_schema": {
            "schema_pass": "boolean",
            "downstream_pass": "boolean",
            "score": "number",
            "reason": "short Korean string",
        },
    }
    db = SessionLocal()
    try:
        selection = LLMService.get_runtime_client_for_user(
            db,
            config.user_id,
            config.judge_model,
            config.org_id,
        )
        result = await selection.client.invoke(
            [
                {
                    "role": "system",
                    "content": "You are a strict workflow output quality judge. Return only compact JSON.",
                },
                {
                    "role": "user",
                    "content": json.dumps(judge_prompt, ensure_ascii=False),
                },
            ],
            temperature=0,
            max_tokens=160,
        )
    finally:
        db.close()

    judgement = _json_from_text(_text(result))
    score = _float_between_zero_and_one(judgement.get("score"))
    schema_pass = not shape_errors
    downstream_pass = schema_pass and score >= float(contract["minimum_score"])
    reason = str(judgement.get("reason") or "").strip()
    if shape_errors:
        reason = (
            f"계약 위반: {', '.join(shape_errors)}"
            if not reason
            else f"{reason} / 계약 위반: {', '.join(shape_errors)}"
        )
    if not reason:
        reason = "LLM judge 품질 평가 결과"
    return QualityJudgement(
        schema_pass=schema_pass,
        downstream_pass=downstream_pass,
        score=score,
        reason=reason,
    )


def _float_between_zero_and_one(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    if number < 0:
        return 0.0
    if number > 1:
        return 1.0
    return number


def _record_actual_run(
    db,
    config: VerificationConfig,
    *,
    scenario: str,
    index: int,
    model_id: str,
    input_message: str,
    output_text: str,
    usage: dict[str, int],
    latency_seconds: float,
    quality_judgement: QualityJudgement,
) -> None:
    now = datetime.now(timezone.utc) - timedelta(seconds=300 - index)
    run_id = _run_id(config, scenario, index)
    total_cost = LLMService.calculate_cost(
        db,
        model_id,
        usage["prompt_tokens"],
        usage["completion_tokens"],
    )

    workflow_run = WorkflowRun(
        id=run_id,
        workflow_id=config.workflow_id,
        user_id=config.user_id,
        app_id=config.app_id,
        deployment_id=config.deployment_id,
        workflow_version=1,
        status=RunStatus.SUCCESS,
        trigger_mode=RunTriggerMode.WEBHOOK,
        inputs={"message": input_message},
        outputs={"text": output_text},
        started_at=now,
        finished_at=now + timedelta(seconds=latency_seconds),
        duration=latency_seconds,
        meta_info={
            "scenario": scenario,
            "model_id": model_id,
            "actual_provider": True,
            "execution_provider": config.execution_provider,
        },
        trace_metadata={
            "scenario": scenario,
            "actual_provider": True,
            "execution_provider": config.execution_provider,
        },
        total_tokens=usage["total_tokens"],
        total_cost=total_cost,
        redaction_applied=False,
        pii_detected=False,
        payload_storage_mode="redacted_only",
    )
    db.add(workflow_run)
    db.flush()

    db.add(
        WorkflowNodeRun(
            id=_node_run_id(config, scenario, index),
            workflow_run_id=run_id,
            node_id=NODE_ID,
            node_type="llmNode",
            status=NodeRunStatus.SUCCESS,
            inputs={"message": input_message},
            process_data={"scenario": scenario, "model_id": model_id},
            outputs={"text": output_text, "model": model_id},
            started_at=now,
            finished_at=now + timedelta(seconds=latency_seconds),
            duration=latency_seconds,
            trace_metadata={
                "schema_status": (
                    "passed" if quality_judgement.schema_pass else "failed"
                ),
                "downstream_status": (
                    "compatible"
                    if quality_judgement.downstream_pass
                    else "broken"
                ),
                "quality_judge": {
                    "model": config.judge_model,
                    "score": quality_judgement.score,
                    "reason": quality_judgement.reason,
                },
                "llm": {"fallback_used": False},
                "actual_provider": True,
                "execution_provider": config.execution_provider,
            },
            redaction_applied=False,
            pii_detected=False,
            sequence=1,
            retry_count=0,
        )
    )
    LLMService.log_usage(
        db,
        user_id=config.user_id,
        model_id=model_id,
        usage=usage,
        cost=total_cost,
        organization_id=config.org_id,
        workflow_id=config.workflow_id,
        workflow_run_id=run_id,
        node_id=NODE_ID,
    )
    db.flush()


def _decision(
    db, config: VerificationConfig, *, customer_facing: bool = False
) -> dict[str, Any]:
    candidates = [
        candidate
        for candidate in ModelRouter.collect_candidates(db, organization_id=config.org_id)
        if candidate.model_id in config.model_order
    ]
    decision = ModelRouter.resolve(
        ModelRouterContext(
            workflow_id=str(config.workflow_id),
            node_id=NODE_ID,
            current_model_id=config.high_model,
            fallback_model_id=config.high_model,
            candidate_models=candidates,
            customer_facing=customer_facing,
            output_format="json" if customer_facing else None,
        ),
        db=db,
    )
    profile = ModelRouter.collect_profile(
        db,
        ModelRouterContext(
            workflow_id=str(config.workflow_id),
            node_id=NODE_ID,
            current_model_id=config.high_model,
            candidate_models=candidates,
        ),
    )
    return {
        "stage": decision.routing_stage,
        "selected_model": decision.selected_model_id,
        "fallback_model": decision.fallback_model_id,
        "reason": decision.reason,
        "usable_runs": profile.operational_usable_runs,
        "models": {
            model_id: performance.as_summary()
            for model_id, performance in profile.model_performance.items()
        },
    }


async def _seed_model_runs(
    db,
    config: VerificationConfig,
    *,
    scenario: str,
    start_index: int,
    model_id: str,
    count: int,
    prompt_contract: str,
    judge_contract: str,
) -> int:
    index = start_index
    for offset in range(count):
        index += 1
        message = (
            f"{scenario} 검증 입력 {offset + 1}. "
            f"{', '.join(_expected_contract(prompt_contract)['required_fields'])} "
            "필드를 포함한 JSON으로 짧게 답하세요."
        )
        output_text, usage, latency = await _invoke_actual(config, model_id, message)
        quality_judgement = await _judge_quality(
            config,
            scenario=judge_contract,
            input_message=message,
            output_text=output_text,
        )
        _record_actual_run(
            db,
            config,
            scenario=scenario,
            index=index,
            model_id=model_id,
            input_message=message,
            output_text=output_text,
            usage=usage,
            latency_seconds=latency,
            quality_judgement=quality_judgement,
        )
        db.commit()
    return index


async def _run_scenario(
    config: VerificationConfig,
    scenario: str,
    calls: Iterable[tuple[str, int, str, str]],
    *,
    customer_facing: bool = False,
) -> dict[str, Any]:
    db = SessionLocal()
    try:
        _clear_runs(db, config)
        db.commit()
        index = 0
        for model_id, count, prompt_contract, judge_contract in calls:
            index = await _seed_model_runs(
                db,
                config,
                scenario=scenario,
                start_index=index,
                model_id=model_id,
                count=count,
                prompt_contract=prompt_contract,
                judge_contract=judge_contract,
            )
        summary = _decision(db, config, customer_facing=customer_facing)
        return summary
    finally:
        db.close()


def _assert(summary: dict[str, Any], expected_stage: str, expected_model: str) -> None:
    if summary["stage"] != expected_stage:
        raise AssertionError(f"expected stage={expected_stage}, got {summary['stage']}")
    if summary["selected_model"] != expected_model:
        raise AssertionError(
            f"expected selected_model={expected_model}, got {summary['selected_model']}"
        )


def _print_summary(name: str, summary: dict[str, Any]) -> None:
    print(name)
    print(
        f"- usable_runs={summary['usable_runs']} stage={summary['stage']} "
        f"selected={summary['selected_model']} fallback={summary['fallback_model']}"
    )
    print(f"- reason={summary['reason']}")
    for model_id, metrics in sorted(summary["models"].items()):
        print(f"  - {model_id}: {metrics}")


def _parse_uuid(value: str, field_name: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"{field_name} must be UUID: {value}") from exc


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="실제 provider 호출로 ModelRouter cheap/mid/high 판정을 검증합니다."
    )
    parser.add_argument(
        "--provider",
        choices=["auto", *sorted(MODEL_PRESETS)],
        default="auto",
        help="실행 모델 preset provider. auto는 현재 credential로 실행 가능한 preset을 고릅니다.",
    )
    parser.add_argument("--org-id", default=str(DEFAULT_ORG_ID))
    parser.add_argument("--user-id", default=str(DEFAULT_USER_ID))
    parser.add_argument("--app-id", default=str(DEFAULT_APP_ID))
    parser.add_argument("--workflow-id", default=str(DEFAULT_WORKFLOW_ID))
    parser.add_argument("--deployment-id", default=str(DEFAULT_DEPLOYMENT_ID))
    parser.add_argument("--namespace", default=str(DEFAULT_NAMESPACE))
    parser.add_argument("--cheap-model")
    parser.add_argument("--mid-model")
    parser.add_argument("--high-model")
    parser.add_argument("--judge-model")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="credential/model relation 검증과 설정 출력만 수행하고 provider 호출은 하지 않습니다.",
    )
    return parser.parse_args()


def _provider_for_model(db, model_id: str) -> str | None:
    model = (
        db.query(LLMModel)
        .filter(
            LLMModel.model_id_for_api_call == model_id,
            LLMModel.is_active.is_(True),
        )
        .first()
    )
    if not model:
        return None
    return str(model.provider.name) if model.provider else None


def _runtime_model_available(
    db,
    *,
    user_id: uuid.UUID,
    org_id: uuid.UUID,
    model_id: str,
) -> tuple[bool, str | None]:
    try:
        LLMService.get_runtime_client_for_user(db, user_id, model_id, org_id)
    except LLMCredentialNotAvailableError as exc:
        return False, f"{model_id}: {exc.reason}"
    except Exception as exc:
        return False, f"{model_id}: {type(exc).__name__}: {exc}"
    return True, None


def _validate_models_available(
    db,
    *,
    user_id: uuid.UUID,
    org_id: uuid.UUID,
    models: Sequence[str],
) -> list[str]:
    errors: list[str] = []
    seen: set[str] = set()
    for model_id in models:
        if model_id in seen:
            continue
        seen.add(model_id)
        available, reason = _runtime_model_available(
            db,
            user_id=user_id,
            org_id=org_id,
            model_id=model_id,
        )
        if not available and reason:
            errors.append(reason)
    return errors


def _preset_from_args(args: argparse.Namespace, preset: ProviderPreset) -> ProviderPreset:
    return ProviderPreset(
        provider=preset.provider,
        cheap_model=args.cheap_model or preset.cheap_model,
        mid_model=args.mid_model or preset.mid_model,
        high_model=args.high_model or preset.high_model,
        judge_model=args.judge_model or preset.judge_model,
    )


def _resolve_config(args: argparse.Namespace) -> VerificationConfig:
    org_id = _parse_uuid(args.org_id, "org-id")
    user_id = _parse_uuid(args.user_id, "user-id")
    app_id = _parse_uuid(args.app_id, "app-id")
    workflow_id = _parse_uuid(args.workflow_id, "workflow-id")
    deployment_id = _parse_uuid(args.deployment_id, "deployment-id")
    namespace = _parse_uuid(args.namespace, "namespace")

    with SessionLocal() as db:
        selected_preset: ProviderPreset | None = None
        provider_errors: dict[str, list[str]] = {}

        if args.provider == "auto":
            for provider, preset in MODEL_PRESETS.items():
                resolved = _preset_from_args(args, preset)
                errors = _validate_models_available(
                    db,
                    user_id=user_id,
                    org_id=org_id,
                    models=[
                        resolved.cheap_model,
                        resolved.mid_model,
                        resolved.high_model,
                        resolved.judge_model,
                    ],
                )
                if not errors:
                    selected_preset = resolved
                    break
                provider_errors[provider] = errors
            if selected_preset is None:
                details = "; ".join(
                    f"{provider}: {', '.join(errors)}"
                    for provider, errors in provider_errors.items()
                )
                raise RuntimeError(
                    "실행 가능한 provider preset을 찾지 못했습니다. "
                    "조직 credential, verified model relation, credential use 권한을 확인하세요. "
                    f"details={details}"
                )
        else:
            selected_preset = _preset_from_args(args, MODEL_PRESETS[args.provider])
            errors = _validate_models_available(
                db,
                user_id=user_id,
                org_id=org_id,
                models=[
                    selected_preset.cheap_model,
                    selected_preset.mid_model,
                    selected_preset.high_model,
                    selected_preset.judge_model,
                ],
            )
            if errors:
                raise RuntimeError(
                    f"{args.provider} preset을 실행할 수 없습니다. "
                    "credential/model relation/use 권한을 확인하세요. "
                    f"details={', '.join(errors)}"
                )

        execution_providers = {
            _provider_for_model(db, model_id)
            for model_id in (
                selected_preset.cheap_model,
                selected_preset.mid_model,
                selected_preset.high_model,
            )
        }
        execution_providers.discard(None)
        if len(execution_providers) != 1:
            raise RuntimeError(
                "cheap/mid/high 실행 모델은 같은 provider여야 합니다. "
                f"providers={sorted(execution_providers)}"
            )
        execution_provider = next(iter(execution_providers))

    return VerificationConfig(
        org_id=org_id,
        user_id=user_id,
        app_id=app_id,
        workflow_id=workflow_id,
        deployment_id=deployment_id,
        namespace=namespace,
        execution_provider=execution_provider,
        cheap_model=selected_preset.cheap_model,
        mid_model=selected_preset.mid_model,
        high_model=selected_preset.high_model,
        judge_model=selected_preset.judge_model,
    )


def _print_config(config: VerificationConfig) -> None:
    print("Actual provider model router verification config")
    print(f"- org_id={config.org_id}")
    print(f"- user_id={config.user_id}")
    print(f"- execution_provider={config.execution_provider}")
    print(f"- cheap_model={config.cheap_model}")
    print(f"- mid_model={config.mid_model}")
    print(f"- high_model={config.high_model}")
    print(f"- judge_model={config.judge_model}")


async def main() -> None:
    args = _parse_args()
    config = _resolve_config(args)
    _print_config(config)
    if args.dry_run:
        print("dry-run completed: provider/model/credential resolution passed.")
        return

    with SessionLocal() as db:
        _ensure_workflow(db, config)
        db.commit()

    cheap_summary = await _run_scenario(
        config,
        "cheap-pass",
        [
            (config.high_model, 20, "cheap-pass", "cheap-pass"),
            (config.cheap_model, 10, "cheap-pass", "cheap-pass"),
            (config.mid_model, 60, "cheap-pass", "cheap-pass"),
        ],
    )
    _assert(cheap_summary, "optimized", config.cheap_model)

    mid_summary = await _run_scenario(
        config,
        "mid-pass",
        [
            (config.high_model, 20, "mid-pass", "mid-pass"),
            (config.cheap_model, 10, "cheap-pass", "mid-pass"),
            (config.mid_model, 60, "mid-pass", "mid-pass"),
        ],
    )
    _assert(mid_summary, "optimized", config.mid_model)

    high_summary = await _run_scenario(
        config,
        "high-risk",
        [
            (config.high_model, 90, "high-risk", "high-risk"),
        ],
        customer_facing=True,
    )
    _assert(high_summary, "optimized", config.high_model)

    print("Actual provider model router verification")
    _print_summary("cheap optimization scenario", cheap_summary)
    _print_summary("mid optimization scenario", mid_summary)
    _print_summary("high-cost retention scenario", high_summary)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except RuntimeError as exc:
        print(f"verification setup failed: {exc}", file=sys.stderr)
        sys.exit(2)
