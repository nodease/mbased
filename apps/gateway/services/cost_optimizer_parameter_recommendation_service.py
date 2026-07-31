from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from statistics import mean
from typing import Any

from sqlalchemy.orm import Session

from apps.shared.db.models.app import App
from apps.shared.db.models.llm import LLMUsageLog
from apps.shared.db.models.workflow_run import (
    NodeRunStatus,
    RunStatus,
    RunTriggerMode,
    WorkflowNodeRun,
    WorkflowRun,
)
from apps.shared.db.models.workflow_deployment import WorkflowDeployment
from apps.shared.services.node_config_fingerprint import (
    llm_node_config_fingerprint,
)

POLICY_VERSION = "llm-parameter-recommendation-rules-v1"
MIN_OPERATION_SAMPLES = 20
MODEL_ROUTING_REFRESH_RECOMMENDED_MIN = 20
MODEL_ROUTING_REFRESH_RECOMMENDED_MAX = 50
OPERATION_TRIGGER_MODES = {"api", "webhook", "scheduler", "app"}
OPERATION_TRIGGER_MODE_ENUMS = (
    RunTriggerMode.API,
    RunTriggerMode.WEBHOOK,
    RunTriggerMode.SCHEDULER,
    RunTriggerMode.APP,
)


@dataclass(frozen=True)
class _NodeRunSample:
    prompt_tokens: int
    completion_tokens: int
    latency_ms: int
    cost: float
    finish_reason: str | None
    schema_status: str | None
    downstream_status: str | None
    retry_count: int
    fallback_used: bool
    context_token_estimate: int
    retrieved_chunk_count: int
    evidence_sufficient: bool | None
    repetition_rate: float | None
    has_successful_usage: bool
    node_succeeded: bool
    workflow_succeeded: bool


class CostOptimizerParameterRecommendationService:
    """LLM 파라미터 추천 룰셋.

    추천은 배포 후 운영 실행의 safe summary만 사용한다. raw prompt,
    raw completion, raw Knowledge chunk content, credential 원문은 응답에
    포함하지 않는다.
    """

    @classmethod
    def recommend(
        cls,
        db: Session,
        *,
        workflow,
        node_id: str,
    ) -> dict[str, Any]:
        node = _find_node(workflow.graph, node_id)
        node_data = node.get("data") if isinstance(node, dict) else {}
        node_data = node_data if isinstance(node_data, dict) else {}
        cohort = _resolve_operation_cohort(
            db,
            workflow=workflow,
            node_id=node_id,
            current_node_data=node_data,
        )
        warnings: list[dict[str, str]] = []
        routing_recommendations = cls._recommend_model_routing_controls(node_data)

        if cohort["status"] == "draft_not_deployed":
            return _recommendation_response(
                analysis_stage="draft_not_deployed",
                node_data=node_data,
                recommendations=routing_recommendations,
                warnings=[
                    {
                        "code": "draft_config_not_deployed",
                        "message": (
                            "현재 draft LLM 설정이 활성 배포 설정과 달라 운영 로그를 "
                            "섞어 파라미터 추천을 만들지 않았습니다. 먼저 배포하거나 "
                            "A/B 후보 실험으로 검증하세요."
                        ),
                    }
                ],
                profile={"sample_count": 0},
            )

        if cohort["status"] == "deployment_unavailable":
            return _recommendation_response(
                analysis_stage="deployment_unavailable",
                node_data=node_data,
                recommendations=routing_recommendations,
                warnings=[
                    {
                        "code": "active_deployment_unavailable",
                        "message": "활성 배포 설정을 찾지 못해 파라미터 추천을 만들지 않았습니다.",
                    }
                ],
                profile={"sample_count": 0},
            )

        samples = cls._collect_samples(
            db,
            workflow.id,
            node_id,
            deployment_id=cohort["deployment_id"],
        )

        profile = _profile(samples)
        if profile["successful_usage_sample_count"] < MIN_OPERATION_SAMPLES:
            return _recommendation_response(
                analysis_stage="insufficient_logs",
                node_data=node_data,
                recommendations=routing_recommendations,
                warnings=[
                    {
                        "code": "operation_logs_insufficient",
                        "message": (
                            "같은 활성 배포 설정의 성공 운영 usage 로그가 충분하지 않아 "
                            "파라미터 추천을 만들지 않았습니다."
                        ),
                    }
                ],
                profile={
                    "sample_count": profile["sample_count"],
                    "successful_usage_sample_count": profile[
                        "successful_usage_sample_count"
                    ],
                },
            )

        recommendations: list[dict[str, Any]] = [*routing_recommendations]
        max_tokens = cls._recommend_max_tokens(node_data, profile, warnings)
        if max_tokens:
            recommendations.append(max_tokens)

        temperature = cls._recommend_temperature(node_data, profile)
        if temperature:
            recommendations.append(temperature)

        top_p = cls._recommend_top_p_compatibility(node_data)
        if top_p:
            recommendations.append(top_p)

        frequency_penalty = cls._recommend_frequency_penalty(node_data, profile)
        if frequency_penalty:
            recommendations.append(frequency_penalty)

        rag_recommendations = cls._recommend_rag_context(node_data, profile, warnings)
        recommendations.extend(rag_recommendations)

        if _author_prompt_token_pressure(node_data, profile):
            warnings.append(
                {
                    "code": "author_prompt_not_trimmed",
                    "message": (
                        "작성자가 입력한 system/user/assistant prompt는 "
                        "자동으로 자르지 않습니다. 프롬프트 축소는 별도 "
                        "후보 실험으로 검증해야 합니다."
                    ),
                }
            )

        return _recommendation_response(
            analysis_stage="recommendations_available",
            node_data=node_data,
            recommendations=recommendations,
            warnings=warnings,
            profile={
                "sample_count": profile["sample_count"],
                "successful_usage_sample_count": profile[
                    "successful_usage_sample_count"
                ],
                "schema_fail_rate": profile["schema_fail_rate"],
                "downstream_fail_rate": profile["downstream_fail_rate"],
                "truncation_rate": profile["truncation_rate"],
            },
        )

    @staticmethod
    def _recommend_model_routing_controls(
        node_data: dict[str, Any],
    ) -> list[dict[str, Any]]:
        recommendations: list[dict[str, Any]] = []
        auto_model_routing = bool(node_data.get("auto_model_routing"))
        if not auto_model_routing:
            recommendations.append(
                _recommendation(
                    recommendation_type="model_routing_policy",
                    parameter_key="model_routing.enable",
                    current_value=False,
                    suggested_value=True,
                    confidence="high",
                    risk="low",
                    reason=(
                        "수동 모델 고정 상태입니다. 자동 모델 라우팅을 켜면 "
                        "저장된 정책이 실행 입력과 운영 로그를 기준으로 모델을 "
                        "선택할 수 있습니다."
                    ),
                    evidence={"current_auto_model_routing": False},
                    apply_mode="direct_policy_update",
                    candidate_patch={"auto_model_routing": True},
                )
            )

        refresh = _model_routing_refresh(node_data)
        current_refresh_every_runs = _int_or_none(refresh.get("refresh_every_runs"))
        if current_refresh_every_runs is None:
            return recommendations

        if current_refresh_every_runs > MODEL_ROUTING_REFRESH_RECOMMENDED_MAX:
            recommendations.append(
                _recommendation(
                    recommendation_type="model_routing_policy",
                    parameter_key="model_routing.refresh_interval_shorten",
                    current_value=current_refresh_every_runs,
                    suggested_value=MODEL_ROUTING_REFRESH_RECOMMENDED_MIN,
                    confidence="medium",
                    risk="medium",
                    reason=(
                        "정책 점검 주기가 길어 운영 패턴 변화가 늦게 반영될 수 "
                        "있습니다. 권장 범위 안에서 더 자주 점검하는 후보입니다."
                    ),
                    evidence={
                        "current_refresh_every_runs": current_refresh_every_runs,
                        "recommended_min": MODEL_ROUTING_REFRESH_RECOMMENDED_MIN,
                        "recommended_max": MODEL_ROUTING_REFRESH_RECOMMENDED_MAX,
                    },
                    apply_mode="direct_policy_update",
                    candidate_patch={
                        "model_routing_policy": {
                            "refresh": {
                                "refresh_every_runs": MODEL_ROUTING_REFRESH_RECOMMENDED_MIN
                            }
                        }
                    },
                )
            )
        elif current_refresh_every_runs < MODEL_ROUTING_REFRESH_RECOMMENDED_MIN:
            recommendations.append(
                _recommendation(
                    recommendation_type="model_routing_policy",
                    parameter_key="model_routing.refresh_interval_relax",
                    current_value=current_refresh_every_runs,
                    suggested_value=MODEL_ROUTING_REFRESH_RECOMMENDED_MIN,
                    confidence="medium",
                    risk="low",
                    reason=(
                        "정책 점검 주기가 너무 짧으면 운영 로그가 충분히 쌓이기 "
                        "전에 재평가가 반복될 수 있습니다. 최소 권장 주기로 "
                        "완화하는 후보입니다."
                    ),
                    evidence={
                        "current_refresh_every_runs": current_refresh_every_runs,
                        "recommended_min": MODEL_ROUTING_REFRESH_RECOMMENDED_MIN,
                        "recommended_max": MODEL_ROUTING_REFRESH_RECOMMENDED_MAX,
                    },
                    apply_mode="direct_policy_update",
                    candidate_patch={
                        "model_routing_policy": {
                            "refresh": {
                                "refresh_every_runs": MODEL_ROUTING_REFRESH_RECOMMENDED_MIN
                            }
                        }
                    },
                )
            )
        return recommendations

    @staticmethod
    def _collect_samples(
        db: Session,
        workflow_id: Any,
        node_id: str,
        *,
        deployment_id: Any | None,
    ) -> list[_NodeRunSample]:
        if hasattr(db, "workflow_runs"):
            return _collect_samples_from_fake_db(
                db,
                workflow_id,
                node_id,
                deployment_id=deployment_id,
            )
        return _collect_samples_from_query(
            db,
            workflow_id,
            node_id,
            deployment_id=deployment_id,
        )

    @staticmethod
    def _recommend_max_tokens(
        node_data: dict[str, Any],
        profile: dict[str, Any],
        warnings: list[dict[str, str]],
    ) -> dict[str, Any] | None:
        parameters = _parameters(node_data)
        current = _number_or_none(parameters.get("max_tokens"))
        completion_p95 = profile["completion_tokens_p95"]
        if current is None or current <= 0 or completion_p95 <= 0:
            return None

        quality_unstable = (
            profile["truncation_rate"] > 0.05
            or profile["schema_fail_rate"] > 0.1
            or profile["downstream_fail_rate"] > 0.1
        )
        if _is_schema_or_structured_node(node_data) and not profile[
            "schema_signal_complete"
        ]:
            warnings.append(
                {
                    "code": "schema_signal_incomplete",
                    "message": "schema 검증 결과가 누락된 운영 표본이 있어 max_tokens 하향 추천을 만들지 않았습니다.",
                }
            )
            return None
        if quality_unstable:
            warnings.append(
                {
                    "code": "quality_signal_unstable",
                    "message": (
                        "최근 실행에서 길이 잘림, schema 실패 또는 downstream "
                        "실패가 감지되어 max_tokens 하향 추천을 만들지 않았습니다."
                    ),
                }
            )
            return None

        suggested = max(256, _round_up_to_hundred(int(math.ceil(completion_p95 * 1.3))))
        if suggested >= current * 0.9:
            return None

        finish_reasons_known = profile["finish_reason_known_count"] >= profile[
            "successful_usage_sample_count"
        ] * 0.8
        confidence = "high" if finish_reasons_known else "medium"
        reason = (
            f"최근 배포 후 성공 usage {profile['successful_usage_sample_count']}회에서 "
            f"completion token p95가 {completion_p95}입니다."
        )
        if not finish_reasons_known:
            reason += " 길이 잘림 근거 부족으로 신뢰도를 medium으로 낮췄습니다."

        return _recommendation(
            parameter_key="max_tokens",
            current_value=int(current),
            suggested_value=suggested,
            confidence=confidence,
            risk="low" if confidence == "high" else "medium",
            reason=reason,
            evidence={
                "sample_count": profile["successful_usage_sample_count"],
                "completion_tokens_p95": completion_p95,
                "schema_pass_rate": round(1 - profile["schema_fail_rate"], 4),
                "downstream_success_rate": round(
                    1 - profile["downstream_fail_rate"], 4
                ),
                "truncation_rate": profile["truncation_rate"],
            },
            candidate_patch={"parameters": {"max_tokens": suggested}},
        )

    @staticmethod
    def _recommend_temperature(
        node_data: dict[str, Any],
        profile: dict[str, Any],
    ) -> dict[str, Any] | None:
        parameters = _parameters(node_data)
        current = _number_or_none(parameters.get("temperature"))
        if current is None or current <= 0.3:
            return None
        if not _is_schema_or_structured_node(node_data):
            return None
        if (
            profile["schema_fail_rate"] == 0
            and profile["retry_rate"] == 0
            and profile["fallback_rate"] == 0
        ):
            return None

        suggested = 0.2
        return _recommendation(
            parameter_key="temperature",
            current_value=current,
            suggested_value=suggested,
            confidence="medium",
            risk="medium",
            reason=(
                "JSON/schema 또는 추출 성격의 노드에서 temperature가 높고 "
                "schema/retry/fallback 신호가 있어 안정성을 높이는 후보입니다."
            ),
            evidence={
                "sample_count": profile["sample_count"],
                "schema_fail_rate": profile["schema_fail_rate"],
                "retry_rate": profile["retry_rate"],
                "fallback_rate": profile["fallback_rate"],
            },
            candidate_patch={"parameters": {"temperature": suggested}},
        )

    @staticmethod
    def _recommend_top_p_compatibility(node_data: dict[str, Any]) -> dict[str, Any] | None:
        parameters = _parameters(node_data)
        model_id = str(node_data.get("model_id") or "").lower()
        if not model_id.startswith("claude") or "top_p" not in parameters:
            return None
        return _recommendation(
            parameter_key="top_p",
            current_value=parameters.get("top_p"),
            suggested_value=None,
            confidence="high",
            risk="low",
            reason=(
                "Claude 계열 모델은 현재 실행 경로에서 temperature/top_p "
                "동시 사용 충돌을 피해야 하므로 top_p 제거 후보를 제안합니다."
            ),
            evidence={"model_family": "anthropic", "compatibility": "remove_top_p"},
            candidate_patch={"parameters": {"top_p": None}},
        )

    @staticmethod
    def _recommend_frequency_penalty(
        node_data: dict[str, Any],
        profile: dict[str, Any],
    ) -> dict[str, Any] | None:
        if _is_schema_or_structured_node(node_data):
            return None
        repetition_rate = profile["repetition_rate"]
        if repetition_rate is None or repetition_rate < 0.15:
            return None
        parameters = _parameters(node_data)
        current = _number_or_none(parameters.get("frequency_penalty")) or 0
        suggested = min(0.5, round(current + 0.2, 2))
        if suggested <= current:
            return None
        return _recommendation(
            parameter_key="frequency_penalty",
            current_value=current,
            suggested_value=suggested,
            confidence="medium",
            risk="medium",
            reason=(
                "최근 output 반복률이 높고 completion token 증가와 연결되어 "
                "반복 억제 후보를 제안합니다."
            ),
            evidence={
                "sample_count": profile["sample_count"],
                "repetition_rate": repetition_rate,
                "completion_tokens_p95": profile["completion_tokens_p95"],
            },
            candidate_patch={"parameters": {"frequency_penalty": suggested}},
        )

    @staticmethod
    def _recommend_rag_context(
        node_data: dict[str, Any],
        profile: dict[str, Any],
        warnings: list[dict[str, str]],
    ) -> list[dict[str, Any]]:
        if not _has_rag_settings(node_data):
            return []
        if not profile["rag_signal_complete"]:
            warnings.append(
                {
                    "code": "rag_signal_incomplete",
                    "message": "RAG 검색 요약이 누락된 운영 표본이 있어 context 축소 추천을 만들지 않았습니다.",
                }
            )
            return []
        if profile["rag_evidence_insufficient_rate"] > 0:
            warnings.append(
                {
                    "code": "rag_evidence_insufficient",
                    "message": (
                        "검색 근거가 부족한 실행이 있어 RAG context 축소 추천을 "
                        "만들지 않았습니다."
                    ),
                }
            )
            return []
        if profile["downstream_fail_rate"] > 0.1:
            warnings.append(
                {
                    "code": "quality_signal_unstable",
                    "message": "후속 노드 실패가 감지되어 RAG context 축소 추천을 만들지 않았습니다.",
                }
            )
            return []

        prompt_p95 = profile["prompt_tokens_p95"]
        context_p95 = profile["context_token_estimate_p95"]
        if prompt_p95 <= 0 or context_p95 / prompt_p95 < 0.5:
            return []
        if profile["retrieved_chunk_count_p95"] <= 5:
            return []

        current_top_k = _int_or_none(node_data.get("topK")) or int(
            profile["retrieved_chunk_count_p95"]
        )
        suggested_top_k = max(3, math.ceil(current_top_k * 0.5))
        recommendations = [
            _recommendation(
                parameter_key="rag.top_k",
                current_value=current_top_k,
                suggested_value=suggested_top_k,
                confidence="medium",
                risk="medium",
                reason=(
                    "검색 context가 prompt token의 큰 비중을 차지하고 "
                    "근거 충분성이 유지되어 검색 개수 축소 후보를 제안합니다."
                ),
                evidence={
                    "sample_count": profile["sample_count"],
                    "context_token_estimate_p95": context_p95,
                    "prompt_tokens_p95": prompt_p95,
                    "retrieved_chunk_count_p95": profile[
                        "retrieved_chunk_count_p95"
                    ],
                },
                candidate_patch={"knowledge": {"top_k": suggested_top_k}},
            )
        ]
        if node_data.get("retrievedContextCompression") not in {"light", "strong"}:
            recommendations.append(
                _recommendation(
                    parameter_key="rag.retrieved_context_compression",
                    current_value=node_data.get("retrievedContextCompression") or "off",
                    suggested_value="light",
                    confidence="medium",
                    risk="medium",
                    reason=(
                        "검색 문서를 그대로 넣는 대신 질문 관련 핵심 내용만 "
                        "압축해 context 비용을 줄이는 후보입니다."
                    ),
                    evidence={
                        "sample_count": profile["sample_count"],
                        "context_token_estimate_p95": context_p95,
                    },
                    candidate_patch={
                        "knowledge": {"retrieved_context_compression": "light"}
                    },
                )
            )
        max_chars = _int_or_none(node_data.get("retrievedContextMaxChars"))
        if max_chars and max_chars > 4000:
            recommendations.append(
                _recommendation(
                    parameter_key="rag.retrieved_context_max_chars",
                    current_value=max_chars,
                    suggested_value=4000,
                    confidence="medium",
                    risk="medium",
                    reason=(
                        "참조 문서 길이 제한이 높아 검색 context 비용이 커질 수 "
                        "있어 길이 제한 후보를 제안합니다."
                    ),
                    evidence={
                        "sample_count": profile["sample_count"],
                        "context_token_estimate_p95": context_p95,
                    },
                    candidate_patch={
                        "knowledge": {"retrieved_context_max_chars": 4000}
                    },
                )
            )
        return recommendations


def _collect_samples_from_fake_db(
    db,
    workflow_id: Any,
    node_id: str,
    *,
    deployment_id: Any | None,
) -> list[_NodeRunSample]:
    operation_runs = [
        run
        for run in getattr(db, "workflow_runs", [])
        if getattr(run, "workflow_id", None) == workflow_id
        and getattr(run, "deployment_id", None) is not None
        and (deployment_id is None or getattr(run, "deployment_id", None) == deployment_id)
        and _enum_value(getattr(run, "trigger_mode", None)) in OPERATION_TRIGGER_MODES
        and _enum_value(getattr(run, "status", None))
        in {RunStatus.SUCCESS.value, RunStatus.FAILED.value}
    ]
    run_by_id = {run.id: run for run in operation_runs}
    node_runs = {
        node_run.workflow_run_id: node_run
        for node_run in getattr(db, "node_runs", [])
        if node_run.workflow_run_id in run_by_id
        and getattr(node_run, "node_id", None) == node_id
        and _enum_value(getattr(node_run, "status", None))
        in {NodeRunStatus.SUCCESS.value, NodeRunStatus.FAILED.value}
    }
    usage_by_run_id = {
        usage.workflow_run_id: usage
        for usage in getattr(db, "usage_logs", [])
        if getattr(usage, "workflow_id", None) == workflow_id
        and getattr(usage, "workflow_run_id", None) in node_runs
        and getattr(usage, "node_id", None) == node_id
        and getattr(usage, "cost_optimizer_candidate_id", None) is None
        and getattr(usage, "status", "success") == "success"
    }
    return [
        _sample_from_usage_and_node_run(
            usage_by_run_id.get(run_id),
            node_run,
            workflow_status=_enum_value(getattr(run_by_id[run_id], "status", None)),
        )
        for run_id, node_run in node_runs.items()
    ]


def _collect_samples_from_query(
    db: Session,
    workflow_id: Any,
    node_id: str,
    *,
    deployment_id: Any | None,
) -> list[_NodeRunSample]:
    query = db.query(WorkflowRun).filter(
        WorkflowRun.workflow_id == workflow_id,
        WorkflowRun.deployment_id.isnot(None),
        WorkflowRun.status.in_([RunStatus.SUCCESS, RunStatus.FAILED]),
        WorkflowRun.trigger_mode.in_(OPERATION_TRIGGER_MODE_ENUMS),
    )
    if deployment_id is not None:
        query = query.filter(WorkflowRun.deployment_id == deployment_id)
    runs = query.order_by(WorkflowRun.started_at.desc()).limit(200).all()
    run_by_id = {run.id: run for run in runs}
    run_ids = list(run_by_id)
    if not run_ids:
        return []

    node_runs = (
        db.query(WorkflowNodeRun)
        .filter(
            WorkflowNodeRun.workflow_run_id.in_(run_ids),
            WorkflowNodeRun.node_id == node_id,
            WorkflowNodeRun.status.in_([NodeRunStatus.SUCCESS, NodeRunStatus.FAILED]),
        )
        .all()
    )
    node_run_by_run_id = {node_run.workflow_run_id: node_run for node_run in node_runs}
    if not node_run_by_run_id:
        return []

    usage_logs = (
        db.query(LLMUsageLog)
        .filter(
            LLMUsageLog.workflow_id == workflow_id,
            LLMUsageLog.workflow_run_id.in_(list(node_run_by_run_id)),
            LLMUsageLog.node_id == node_id,
            LLMUsageLog.cost_optimizer_candidate_id.is_(None),
            LLMUsageLog.status == "success",
        )
        .all()
    )
    usage_by_run_id = {usage.workflow_run_id: usage for usage in usage_logs}
    return [
        _sample_from_usage_and_node_run(
            usage_by_run_id.get(run_id),
            node_run,
            workflow_status=_enum_value(getattr(run_by_id[run_id], "status", None)),
        )
        for run_id, node_run in node_run_by_run_id.items()
    ]


def _sample_from_usage_and_node_run(
    usage,
    node_run,
    *,
    workflow_status: str | None,
) -> _NodeRunSample:
    trace_metadata = getattr(node_run, "trace_metadata", None)
    trace_metadata = trace_metadata if isinstance(trace_metadata, dict) else {}
    llm_summary = trace_metadata.get("llm")
    llm_summary = llm_summary if isinstance(llm_summary, dict) else trace_metadata
    rag_summary = trace_metadata.get("rag")
    rag_summary = (
        rag_summary
        if isinstance(rag_summary, dict)
        else trace_metadata.get("rag_summary")
    )
    rag_summary = rag_summary if isinstance(rag_summary, dict) else {}
    node_status = _enum_value(getattr(node_run, "status", None))
    inferred_downstream_status = _string_or_none(
        llm_summary.get("downstream_status")
    )
    if inferred_downstream_status is None:
        if node_status == NodeRunStatus.FAILED.value:
            inferred_downstream_status = "failed"
        elif workflow_status == RunStatus.SUCCESS.value:
            inferred_downstream_status = "passed"
        elif workflow_status == RunStatus.FAILED.value:
            inferred_downstream_status = "failed"
    return _NodeRunSample(
        prompt_tokens=int(getattr(usage, "prompt_tokens", 0) or 0),
        completion_tokens=int(getattr(usage, "completion_tokens", 0) or 0),
        latency_ms=int(getattr(usage, "latency_ms", 0) or 0),
        cost=float(getattr(usage, "total_cost", 0) or 0),
        finish_reason=_string_or_none(llm_summary.get("finish_reason")),
        schema_status=_string_or_none(llm_summary.get("schema_status")),
        downstream_status=inferred_downstream_status,
        retry_count=int(getattr(node_run, "retry_count", 0) or 0),
        fallback_used=bool(llm_summary.get("fallback_used")),
        context_token_estimate=int(rag_summary.get("context_token_estimate") or 0),
        retrieved_chunk_count=int(rag_summary.get("retrieved_chunk_count") or 0),
        evidence_sufficient=rag_summary.get("evidence_sufficient"),
        repetition_rate=_number_or_none(llm_summary.get("repetition_rate")),
        has_successful_usage=bool(
            usage is not None and getattr(usage, "status", "success") == "success"
        ),
        node_succeeded=node_status == NodeRunStatus.SUCCESS.value,
        workflow_succeeded=workflow_status == RunStatus.SUCCESS.value,
    )


def _profile(samples: list[_NodeRunSample]) -> dict[str, Any]:
    sample_count = len(samples)
    successful_usage_samples = [
        sample
        for sample in samples
        if sample.has_successful_usage and sample.node_succeeded
    ]
    successful_usage_sample_count = len(successful_usage_samples)
    finish_reason_known_count = sum(
        1 for sample in successful_usage_samples if sample.finish_reason
    )
    schema_evaluated_samples = [
        sample
        for sample in samples
        if sample.schema_status
        in {"passed", "pass", "valid", "failed", "schema_failed", "truncated"}
    ]
    schema_evaluated_count = len(schema_evaluated_samples)
    schema_fail_count = sum(
        1
        for sample in schema_evaluated_samples
        if sample.schema_status in {"failed", "schema_failed"}
    )
    downstream_fail_count = sum(
        1
        for sample in samples
        if sample.downstream_status in {"failed", "incompatible"}
    )
    truncation_count = sum(
        1
        for sample in successful_usage_samples
        if sample.finish_reason in {"length", "max_tokens"} or sample.schema_status == "truncated"
    )
    rag_evaluated_samples = [
        sample for sample in samples if sample.evidence_sufficient is not None
    ]
    rag_evidence_insufficient_count = sum(
        1 for sample in rag_evaluated_samples if sample.evidence_sufficient is False
    )
    repetition_values = [
        sample.repetition_rate
        for sample in successful_usage_samples
        if sample.repetition_rate is not None
    ]
    return {
        "sample_count": sample_count,
        "successful_usage_sample_count": successful_usage_sample_count,
        "completion_tokens_p95": _percentile(
            [sample.completion_tokens for sample in successful_usage_samples], 0.95
        ),
        "prompt_tokens_p95": _percentile(
            [sample.prompt_tokens for sample in successful_usage_samples], 0.95
        ),
        "context_token_estimate_p95": _percentile(
            [sample.context_token_estimate for sample in successful_usage_samples], 0.95
        ),
        "retrieved_chunk_count_p95": _percentile(
            [sample.retrieved_chunk_count for sample in successful_usage_samples], 0.95
        ),
        "schema_fail_rate": round(
            schema_fail_count / schema_evaluated_count, 4
        )
        if schema_evaluated_count
        else 0.0,
        "downstream_fail_rate": round(downstream_fail_count / sample_count, 4)
        if sample_count
        else 0.0,
        "truncation_rate": round(
            truncation_count / successful_usage_sample_count, 4
        )
        if successful_usage_sample_count
        else 0.0,
        "retry_rate": round(
            sum(1 for sample in samples if sample.retry_count > 0) / sample_count,
            4,
        )
        if sample_count
        else 0.0,
        "fallback_rate": round(
            sum(1 for sample in successful_usage_samples if sample.fallback_used)
            / successful_usage_sample_count,
            4,
        )
        if successful_usage_sample_count
        else 0.0,
        "rag_evidence_insufficient_rate": round(
            rag_evidence_insufficient_count / len(rag_evaluated_samples),
            4,
        )
        if rag_evaluated_samples
        else 0.0,
        "finish_reason_known_count": finish_reason_known_count,
        "schema_signal_complete": bool(samples)
        and schema_evaluated_count == sample_count,
        "rag_signal_complete": bool(samples)
        and len(rag_evaluated_samples) == sample_count
        and all(sample.context_token_estimate >= 0 for sample in rag_evaluated_samples),
        "repetition_rate": round(mean(repetition_values), 4)
        if repetition_values
        else None,
    }


def _recommendation(
    *,
    recommendation_type: str = "llm_parameter",
    parameter_key: str,
    current_value: Any,
    suggested_value: Any,
    confidence: str,
    risk: str,
    reason: str,
    evidence: dict[str, Any],
    candidate_patch: dict[str, Any],
    apply_mode: str = "experiment_required",
) -> dict[str, Any]:
    return {
        "recommendation_type": recommendation_type,
        "parameter_key": parameter_key,
        "current_value": current_value,
        "suggested_value": suggested_value,
        "confidence": confidence,
        "risk": risk,
        "reason": reason,
        "evidence": _safe_evidence(evidence),
        "apply_mode": apply_mode,
        "candidate_patch": candidate_patch,
    }


def _find_node(graph: dict[str, Any], node_id: str) -> dict[str, Any] | None:
    nodes = graph.get("nodes") if isinstance(graph, dict) else []
    for node in nodes if isinstance(nodes, list) else []:
        if isinstance(node, dict) and node.get("id") == node_id:
            return node
    return None


def _resolve_operation_cohort(
    db: Session,
    *,
    workflow: Any,
    node_id: str,
    current_node_data: dict[str, Any],
) -> dict[str, Any]:
    """추천 표본을 현재 활성 배포 node 설정 하나로 한정한다.

    테스트용 in-memory DB는 active_deployment를 선택적으로 제공한다. 실제 DB에서는
    App.active_deployment_id가 가리키는 deployment snapshot을 기준으로 한다.
    """
    if hasattr(db, "workflow_runs"):
        deployment = getattr(db, "active_deployment", None)
        if deployment is None:
            return {"status": "legacy_test_cohort", "deployment_id": None}
    else:
        app_id = getattr(workflow, "app_id", None)
        if app_id is None:
            return {"status": "deployment_unavailable", "deployment_id": None}

        app = db.query(App).filter(App.id == app_id).first()
        active_deployment_id = getattr(app, "active_deployment_id", None)
        if active_deployment_id is None:
            return {"status": "deployment_unavailable", "deployment_id": None}

        deployment = (
            db.query(WorkflowDeployment)
            .filter(
                WorkflowDeployment.id == active_deployment_id,
                WorkflowDeployment.app_id == app_id,
                WorkflowDeployment.is_active.is_(True),
            )
            .first()
        )
        if deployment is None:
            return {"status": "deployment_unavailable", "deployment_id": None}

    deployment_graph = getattr(deployment, "graph_snapshot", None)
    deployed_node = _find_node(
        deployment_graph if isinstance(deployment_graph, dict) else {}, node_id
    )
    deployed_node_data = (
        deployed_node.get("data") if isinstance(deployed_node, dict) else None
    )
    if not isinstance(deployed_node_data, dict) or _node_config_fingerprint(
        current_node_data
    ) != _node_config_fingerprint(deployed_node_data):
        return {"status": "draft_not_deployed", "deployment_id": None}

    return {"status": "active_deployment", "deployment_id": deployment.id}


def _node_config_fingerprint(node_data: dict[str, Any]) -> str:
    """원문을 노출하지 않고 비용/품질에 영향을 주는 node 설정만 비교한다."""
    return llm_node_config_fingerprint(node_data)


def _recommendation_response(
    *,
    analysis_stage: str,
    node_data: dict[str, Any],
    recommendations: list[dict[str, Any]],
    warnings: list[dict[str, str]],
    profile: dict[str, Any],
) -> dict[str, Any]:
    """조회 시점의 node 설정과 추천 payload를 검증 가능한 식별자로 고정한다."""
    fingerprint_payload = {
        "policy_version": POLICY_VERSION,
        "recommendations": recommendations,
    }
    serialized = json.dumps(
        fingerprint_payload,
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )
    return {
        "analysis_stage": analysis_stage,
        "policy_version": POLICY_VERSION,
        "recommendation_fingerprint": hashlib.sha256(
            serialized.encode("utf-8")
        ).hexdigest(),
        "recommendations": recommendations,
        "warnings": warnings,
        "profile": {
            **profile,
            "node_config_fingerprint": _node_config_fingerprint(node_data),
        },
    }


def _parameters(node_data: dict[str, Any]) -> dict[str, Any]:
    parameters = node_data.get("parameters")
    return parameters if isinstance(parameters, dict) else {}


def _model_routing_refresh(node_data: dict[str, Any]) -> dict[str, Any]:
    policy = node_data.get("model_routing_policy")
    policy = policy if isinstance(policy, dict) else {}
    refresh = policy.get("refresh")
    return refresh if isinstance(refresh, dict) else {}


def _is_schema_or_structured_node(node_data: dict[str, Any]) -> bool:
    output_format = node_data.get("output_format")
    output_format = output_format if isinstance(output_format, dict) else {}
    task_type = str(node_data.get("task_type") or "").lower()
    return output_format.get("type") == "json" or task_type in {"classify", "extract"}


def _has_rag_settings(node_data: dict[str, Any]) -> bool:
    knowledge_bases = node_data.get("knowledgeBases")
    return bool(knowledge_bases) or any(
        key in node_data
        for key in (
            "topK",
            "scoreThreshold",
            "retrievedContextMaxChars",
            "retrievedContextCompression",
        )
    )


def _author_prompt_token_pressure(
    node_data: dict[str, Any],
    profile: dict[str, Any],
) -> bool:
    prompt_length = sum(
        len(str(node_data.get(key) or ""))
        for key in ("system_prompt", "user_prompt", "assistant_prompt")
    )
    return prompt_length > 1000 and profile["prompt_tokens_p95"] > 2000


def _safe_evidence(evidence: dict[str, Any]) -> dict[str, Any]:
    safe: dict[str, Any] = {}
    for key, value in evidence.items():
        if isinstance(value, (str, int, float, bool)) or value is None:
            safe[key] = value
    return safe


def _percentile(values: list[int], percentile: float) -> int:
    if not values:
        return 0
    sorted_values = sorted(values)
    index = max(0, math.ceil(len(sorted_values) * percentile) - 1)
    return int(sorted_values[index])


def _round_up_to_hundred(value: int) -> int:
    return int(math.ceil(value / 100) * 100)


def _enum_value(value: Any) -> str | None:
    if value is None:
        return None
    return str(getattr(value, "value", value))


def _string_or_none(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _number_or_none(value: Any) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return None


def _int_or_none(value: Any) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None
