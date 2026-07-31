"""Runtime Judge가 요청의 요구 수준만 안전하게 판정하는 경계다.

모델 후보, 가격, credential 권한은 이 모듈 밖의 ``ModelRouter``가 담당한다.
Judge는 요청이 필요한 능력 수준만 반환하므로 후보 목록의 변화가 학습 label을
흔들지 않는다.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol


class RuntimeJudgeResponseError(ValueError):
    """Judge가 요구 수준 판정 계약 밖의 응답을 반환했을 때 사용한다."""


class RuntimeJudgeClient(Protocol):
    def invoke_sync(self, messages: list[dict[str, str]], **kwargs: Any) -> dict[str, Any]: ...


@dataclass(frozen=True)
class RuntimeJudgeDecision:
    """서버의 후보 선택까지 합쳐진 최종 runtime decision trace다."""

    selected_model_id: str | None
    confidence: float
    reason_short: str | None
    reason_code: str
    usage: dict[str, Any]
    reason_factors: list[str] | None = None
    task_requirements: dict[str, int] | None = None

    def safe_metadata(self) -> dict[str, Any]:
        metadata = {
            "confidence": self.confidence,
            "reason_code": self.reason_code,
            "usage": dict(self.usage),
        }
        if self.selected_model_id:
            metadata["selected_model"] = self.selected_model_id
        if self.reason_short:
            metadata["reason_short"] = self.reason_short
        if self.reason_factors:
            metadata["reason_factors"] = list(self.reason_factors)
        if self.task_requirements:
            metadata["task_requirements"] = dict(self.task_requirements)
        return metadata


@dataclass(frozen=True)
class RuntimeRequirementAssessment:
    """후보 모델과 독립적인 현재 요청의 요구 수준 판정이다."""

    task_requirements: dict[str, int]
    confidence: float
    ambiguity_flags: list[str]
    reason_codes: list[str]
    usage: dict[str, Any]
    rubric_version: str

    @property
    def requires_safe_fallback(self) -> bool:
        """불확실한 고위험 요청은 server fallback으로 보수적으로 닫는다."""

        return (
            self.confidence < 0.60
            or "high_impact_uncertainty" in self.ambiguity_flags
        )

    def to_decision(
        self,
        *,
        selected_model_id: str,
        reason_code: str,
    ) -> RuntimeJudgeDecision:
        return RuntimeJudgeDecision(
            selected_model_id=selected_model_id,
            confidence=self.confidence,
            reason_short=ModelRoutingRuntimeJudge._REASON_SHORT_BY_CODE.get(
                reason_code,
                "요구 수준 기반 선택",
            ),
            reason_code=reason_code,
            usage=dict(self.usage),
            reason_factors=list(self.reason_codes[:3]),
            task_requirements=dict(self.task_requirements),
        )


class ModelRoutingRuntimeJudge:
    """요청 요구 수준만 판정하는 runtime Judge boundary."""

    MAX_FEATURE_CHARS = 7_000
    COMPACT_RETRY_FEATURE_CHARS = 1_200
    MAX_OUTPUT_TOKENS = 768
    REQUIREMENT_RUBRIC_VERSION = "routing-requirements-v4"
    _RETRYABLE_PROVIDER_REASON_CODES = {"responses_incomplete"}
    _AMBIGUITY_FLAGS = {
        "boundary_score",
        "conflicting_evidence",
        "high_impact_uncertainty",
        "insufficient_context",
        "novel_request",
    }
    _REASON_FACTOR_CODES = {
        "high_decision_impact",
        "security_or_compliance_risk",
        "multi_step_reasoning",
        "evidence_conflict",
        "broad_context_synthesis",
        "long_context_handling",
    }
    _REASON_SHORT_BY_CODE = {
        "requirement_default_selected": "요구 수준에 맞는 기본 모델 선택",
        "requirements_candidate_selected": "요구 수준에 맞는 후보 선택",
        "requirement_judge_low_confidence_fallback": (
            "요구 수준 판정 확신도가 낮아 대체 모델 사용"
        ),
    }

    @classmethod
    def assess_requirements(
        cls,
        *,
        client: RuntimeJudgeClient,
        routing_feature_text: str,
        structural_facts: dict[str, Any] | None = None,
        rag_context: dict[str, Any] | None = None,
        deadline_guard: Callable[[], None] | None = None,
    ) -> RuntimeRequirementAssessment:
        """후보 모델을 보지 않고 3축 요구 수준만 판정한다."""

        response, usage = cls._invoke_assessment(
            client=client,
            routing_feature_text=routing_feature_text,
            structural_facts=structural_facts,
            rag_context=rag_context,
            deadline_guard=deadline_guard,
        )
        try:
            payload = json.loads(cls._response_content(response))
        except (TypeError, json.JSONDecodeError) as exc:
            raise RuntimeJudgeResponseError("invalid JSON response") from exc
        if not isinstance(payload, dict):
            raise RuntimeJudgeResponseError("response must be an object")
        if "selected_model_id" in payload or "candidate_models" in payload:
            raise RuntimeJudgeResponseError("requirement judge must not select model")

        requirements = cls._safe_task_requirements(payload)
        if requirements is None:
            raise RuntimeJudgeResponseError("task requirements are missing")
        try:
            confidence = float(payload.get("confidence"))
        except (TypeError, ValueError) as exc:
            raise RuntimeJudgeResponseError("invalid confidence") from exc
        if not 0.0 <= confidence <= 1.0:
            raise RuntimeJudgeResponseError("confidence must be between 0 and 1")

        return RuntimeRequirementAssessment(
            task_requirements=requirements,
            confidence=confidence,
            ambiguity_flags=cls._safe_string_codes(
                payload.get("ambiguity_flags"),
                allowed=cls._AMBIGUITY_FLAGS,
            ),
            reason_codes=cls._safe_string_codes(
                payload.get("reason_codes"),
                allowed=cls._REASON_FACTOR_CODES,
            ),
            usage=usage,
            rubric_version=cls.REQUIREMENT_RUBRIC_VERSION,
        )

    @classmethod
    def _invoke_assessment(
        cls,
        *,
        client: RuntimeJudgeClient,
        routing_feature_text: str,
        structural_facts: dict[str, Any] | None,
        rag_context: dict[str, Any] | None,
        deadline_guard: Callable[[], None] | None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Provider incomplete만 같은 Judge 계약으로 한 번 짧게 재시도한다."""

        attempts: list[dict[str, Any]] = []
        started_at = time.perf_counter()
        try:
            if deadline_guard is not None:
                deadline_guard()
            response = client.invoke_sync(
                messages=cls._requirement_messages(
                    routing_feature_text=routing_feature_text,
                    structural_facts=structural_facts,
                    rag_context=rag_context,
                ),
                temperature=0,
                max_tokens=cls.MAX_OUTPUT_TOKENS,
                response_format={"type": "json_object"},
            )
        except Exception as exc:
            if str(getattr(exc, "reason_code", "")) not in cls._RETRYABLE_PROVIDER_REASON_CODES:
                raise
            attempts.append(cls._safe_usage(getattr(exc, "usage", None)))
            if deadline_guard is not None:
                deadline_guard()
            response = client.invoke_sync(
                messages=cls._requirement_messages(
                    routing_feature_text=routing_feature_text,
                    structural_facts=structural_facts,
                    rag_context=rag_context,
                    compact=True,
                ),
                temperature=0,
                max_tokens=cls.MAX_OUTPUT_TOKENS,
                response_format={"type": "json_object"},
            )

        usage = cls._aggregate_usages(
            [
                *attempts,
                cls._safe_usage(response.get("usage") if isinstance(response, dict) else None),
            ]
        )
        usage["latency_ms"] = max(
            1,
            int(round((time.perf_counter() - started_at) * 1_000)),
        )
        return response, usage

    @classmethod
    def _requirement_messages(
        cls,
        *,
        routing_feature_text: str,
        structural_facts: dict[str, Any] | None,
        rag_context: dict[str, Any] | None,
        compact: bool = False,
    ) -> list[dict[str, str]]:
        instruction = (
            "당신은 요청의 요구 능력만 판정하는 Requirement Judge입니다. "
            "모델을 선택하거나 모델 ID, 가격, 지연 시간을 언급하지 마세요. "
            "작업 복잡도(task_complexity): 0=단순 전달·추출, 1=한 단계 변환·분류, "
            "2=여러 조건을 비교하는 다단계 처리, 3=복합 전문 추론과 충돌 해결. "
            "결정 영향도(decision_impact): 0=외부 영향 없는 내부 참고, "
            "1=상태를 바꾸지 않는 일반 안내, 2=금전·권한·보상 또는 사용자 행동에 영향을 주는 판단, "
            "3=법무·보안·개인정보·대규모 장애 또는 되돌리기 어려운 실행. "
            "근거 종합도(evidence_synthesis): 0=외부 근거 불필요, 1=단일 사실 확인, "
            "2=여러 근거 결합·조건 비교, 3=충돌 근거를 해석해 결론 도출. "
            "STRUCTURAL_FACTS는 서버가 계산한 사실이므로 다시 추측하지 마세요. "
            "customerTier, outputMode 같은 업무 속성만으로 점수를 올리지 마세요. "
            "같은 주제라도 설명 요청과 승인·실행 요청은 결정 영향도를 다르게 판단하세요. "
            "confidence는 답변 품질이 아니라 3축 분류의 자신감입니다. "
            "ambiguity_flags에는 boundary_score, conflicting_evidence, high_impact_uncertainty, "
            "insufficient_context, novel_request 중 필요한 값만 넣으세요. "
            "reason_codes에는 high_decision_impact, security_or_compliance_risk, "
            "multi_step_reasoning, evidence_conflict, broad_context_synthesis, "
            "long_context_handling 중 최대 3개만 넣으세요. 요청 원문을 출력하지 마세요. "
            "JSON object 하나만 반환하세요: "
            '{"task_complexity":0,"decision_impact":0,"evidence_synthesis":0,'
            '"confidence":0.0,"ambiguity_flags":[],"reason_codes":[]}.'
        )
        feature_limit = (
            cls.COMPACT_RETRY_FEATURE_CHARS if compact else cls.MAX_FEATURE_CHARS
        )
        body = {
            "request_feature": str(routing_feature_text or "")[:feature_limit],
            "structural_facts": cls._safe_structural_facts(structural_facts),
            "rag_context": cls._safe_rag_context(rag_context),
            "rubric_version": cls.REQUIREMENT_RUBRIC_VERSION,
        }
        return [
            {"role": "system", "content": instruction},
            {"role": "user", "content": json.dumps(body, ensure_ascii=False)},
        ]

    @staticmethod
    def _safe_structural_facts(value: dict[str, Any] | None) -> dict[str, Any]:
        if not isinstance(value, dict):
            return {}
        allowed = {
            "downstream_contract_required",
            "file_input_present",
            "input_token_bucket",
            "knowledge_enabled",
            "output_format",
            "retrieved_source_count",
            "schema_required",
            "customer_facing",
            "external_write_reachable",
            "external_read_reachable",
            "local_execution_reachable",
            "customer_output_reachable",
            "control_gate_present",
            "irreversible_effect_possible",
            "reachable_effect_count",
            "human_approval_required",
        }
        return {
            key: raw
            for key, raw in value.items()
            if key in allowed and isinstance(raw, (bool, int, str))
        }

    @staticmethod
    def _safe_string_codes(value: Any, *, allowed: set[str]) -> list[str]:
        if not isinstance(value, list):
            return []
        result: list[str] = []
        for raw in value:
            code = str(raw or "").strip()
            if code in allowed and code not in result:
                result.append(code)
            if len(result) == 3:
                break
        return result

    @staticmethod
    def _safe_rag_context(value: dict[str, Any] | None) -> dict[str, Any]:
        """Judge에는 문서 원문 없이 검색량과 안전한 상태만 전달한다."""

        if not isinstance(value, dict) or not bool(value.get("used")):
            return {"used": False}
        safe: dict[str, Any] = {"used": True}
        for key in (
            "retrieved_context_token_estimate",
            "retrieved_context_chars",
            "retrieved_chunk_count",
            "source_count",
        ):
            raw = value.get(key)
            if isinstance(raw, int) and raw >= 0:
                safe[key] = raw
        if isinstance(value.get("evidence_sufficient"), bool):
            safe["evidence_sufficient"] = value["evidence_sufficient"]
        for key in ("partial_result", "query_rewrite_applied"):
            if isinstance(value.get(key), bool):
                safe[key] = value[key]
        for key in ("insufficiency_reason", "source_tier_used"):
            raw = value.get(key)
            if isinstance(raw, str) and raw:
                safe[key] = raw[:80]
        return safe

    @staticmethod
    def _safe_task_requirements(value: Any) -> dict[str, int] | None:
        if not isinstance(value, dict):
            return None
        result: dict[str, int] = {}
        for key in ("task_complexity", "decision_impact", "evidence_synthesis"):
            raw = value.get(key)
            if isinstance(raw, bool) or not isinstance(raw, (int, float)):
                return None
            if isinstance(raw, float) and not raw.is_integer():
                return None
            score = int(raw)
            if not 0 <= score <= 3:
                return None
            result[key] = score
        return result

    @staticmethod
    def _response_content(response: Any) -> str:
        try:
            content = response["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeJudgeResponseError("response content is missing") from exc
        if not isinstance(content, str) or not content.strip():
            raise RuntimeJudgeResponseError("response content is empty")
        return content

    @staticmethod
    def _safe_usage(usage: Any) -> dict[str, Any]:
        if not isinstance(usage, dict):
            return {}
        return {
            key: value
            for key, value in usage.items()
            if key in {"prompt_tokens", "completion_tokens", "total_tokens"}
            and isinstance(value, (int, float))
        }

    @staticmethod
    def _aggregate_usages(usages: list[dict[str, Any]]) -> dict[str, int | float]:
        non_empty = [usage for usage in usages if usage]
        if len(non_empty) == 1:
            return dict(non_empty[0])
        totals: dict[str, int | float] = {}
        for key in ("prompt_tokens", "completion_tokens"):
            value = sum(
                usage.get(key, 0)
                for usage in usages
                if isinstance(usage.get(key), (int, float))
            )
            if value:
                totals[key] = value
        if totals:
            totals["total_tokens"] = sum(totals.values())
            return totals
        total_tokens = sum(
            usage.get("total_tokens", 0)
            for usage in usages
            if isinstance(usage.get("total_tokens"), (int, float))
        )
        return {"total_tokens": total_tokens} if total_tokens else {}
