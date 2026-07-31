"""Judge-first + 점진적 local learning 모델 라우팅 runtime."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Iterable, Mapping, Optional

from jinja2 import Environment, meta
from sqlalchemy.orm import Session

from apps.shared.db.models.llm import (
    LLMCredential,
    LLMModel,
    LLMRelCredentialModel,
)
from apps.shared.services.model_routing_global_profile_catalog import (
    canonical_model_routing_id,
    catalog_metadata_for_model_id,
    is_workflow_execution_model_excluded,
    normalize_model_id as normalize_model_routing_id,
)
from apps.shared.services.llm_model_pricing import get_model_pricing
from apps.workflow_engine.services.llm_output_contract import (
    build_json_output_schema_instruction,
    response_format_requires_json_instruction,
)
from apps.workflow_engine.services.model_routing_judge_first_policy import (
    JUDGE_FIRST_STRATEGY_ID,
)
from apps.workflow_engine.services.model_routing_bootstrap_score import (
    model_bootstrap_score,
    required_bootstrap_score,
    required_safe_fallback_score,
)
from apps.workflow_engine.services.model_routing_decision_cache import (
    accepted_decision,
)
from apps.workflow_engine.services.model_routing_local_classifier import (
    MultilingualE5TaskRequirementClassifier,
)
from apps.workflow_engine.services.model_routing_incremental_learning import (
    TASK_REQUIREMENT_FEATURE_SCHEMA_VERSION,
)


_routing_jinja_env = Environment(autoescape=False)

WORKFLOW_CHAT_MODEL_ALIASES = {
    "gpt-5.5",
    "gpt-5.5-pro",
    "gpt-5.6",
    "gpt-5.6-sol",
    "gpt-5.6-terra",
    "gpt-5.6-luna",
    "gpt-5.4-pro",
    "gpt-5.4",
    "gpt-5.4-mini",
    "gpt-5.4-nano",
    "gpt-5.2",
    "gpt-5.1",
    "gpt-5",
    "o3-pro",
    "o3",
    "gpt-4.1",
    "gpt-4o",
    "gpt-5-nano",
    "gpt-4.1-mini",
    "gpt-4o-mini",
    "claude-fable-5",
    "claude-opus-4-8",
    "claude-opus-4-7",
    "claude-opus-4-6",
    "claude-sonnet-5",
    "claude-sonnet-4-6",
    "claude-haiku-4-5",
    "gemini-3.5-flash",
    "gemini-3.1-pro-preview",
    "gemini-3.1-flash-lite",
    "gemini-3-flash-preview",
    "gemini-2.5-pro",
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
}
BLOCKED_WORKFLOW_MODEL_KEYWORDS = (
    "embedding",
    "image",
    "audio",
    "realtime",
    "moderation",
    "tts",
    "whisper",
    "transcribe",
    "sora",
    "search",
)
BLOCKED_WORKFLOW_MODEL_TYPES = {
    "embedding",
    "image",
    "audio",
    "realtime",
    "moderation",
}


class ModelRoutingPromptRenderError(ValueError):
    """Routing feature용 prompt template을 안전하게 렌더링할 수 없다."""


@dataclass(frozen=True)
class ModelCandidate:
    model_id: str
    display_name: str
    input_price_1k: Optional[float] = None
    output_price_1k: Optional[float] = None

    @property
    def price_score(self) -> float:
        prices = [
            price
            for price in (self.input_price_1k, self.output_price_1k)
            if price is not None
        ]
        return float(sum(prices)) if prices else float("inf")

    @classmethod
    def from_model(cls, model: Any) -> "ModelCandidate":
        return cls(
            model_id=str(model.model_id_for_api_call),
            display_name=str(getattr(model, "name", None) or model.model_id_for_api_call),
            input_price_1k=_float_or_none(getattr(model, "input_price_1k", None)),
            output_price_1k=_float_or_none(getattr(model, "output_price_1k", None)),
        )


@dataclass
class ModelPerformance:
    model_id: str
    run_count: int = 0
    success_count: int = 0
    schema_pass_count: int = 0
    schema_eval_count: int = 0
    downstream_success_count: int = 0
    downstream_eval_count: int = 0
    fallback_count: int = 0
    retry_count: int = 0
    total_cost: float = 0.0
    total_tokens: int = 0
    total_latency_ms: int = 0

    @property
    def success_rate(self) -> Optional[float]:
        return _ratio(self.success_count, self.run_count)

    @property
    def schema_pass_rate(self) -> Optional[float]:
        return _ratio(self.schema_pass_count, self.schema_eval_count)

    @property
    def downstream_success_rate(self) -> Optional[float]:
        return _ratio(self.downstream_success_count, self.downstream_eval_count)

    @property
    def fallback_rate(self) -> Optional[float]:
        return _ratio(self.fallback_count, self.run_count)

    @property
    def avg_cost(self) -> Optional[float]:
        return self.total_cost / self.run_count if self.run_count > 0 else None

    @property
    def avg_total_tokens(self) -> Optional[float]:
        return self.total_tokens / self.run_count if self.run_count > 0 else None

    @property
    def avg_latency_ms(self) -> Optional[float]:
        return self.total_latency_ms / self.run_count if self.run_count > 0 else None

    def as_summary(self) -> dict[str, Any]:
        return {
            "run_count": self.run_count,
            "success_rate": self.success_rate,
            "schema_pass_rate": self.schema_pass_rate,
            "downstream_success_rate": self.downstream_success_rate,
            "fallback_rate": self.fallback_rate,
            "retry_count": self.retry_count,
            "avg_cost": self.avg_cost,
            "avg_total_tokens": self.avg_total_tokens,
            "avg_latency_ms": self.avg_latency_ms,
        }


@dataclass
class NodeRunProfile:
    operational_usable_runs: int = 0
    model_performance: dict[str, ModelPerformance] = field(default_factory=dict)
    segment_performance: dict[str, dict[str, Any]] = field(default_factory=dict)

    def as_snapshot(self) -> dict[str, Any]:
        return {
            "operational_usable_runs": self.operational_usable_runs,
            "model_performance": {
                model_id: performance.as_summary()
                for model_id, performance in self.model_performance.items()
            },
            "segment_performance": [
                {
                    "conditions": segment["conditions"],
                    "model_performance": {
                        model_id: performance.as_summary()
                        for model_id, performance in segment[
                            "model_performance"
                        ].items()
                    },
                }
                for _key, segment in sorted(self.segment_performance.items())
            ],
        }


@dataclass(frozen=True)
class ModelRoutingRuntimeContext:
    text: str
    intent: str
    risk_level: str
    customer_facing: bool
    knowledge_enabled: bool
    output_format: str
    schema_required: bool
    has_file_input: bool
    input_length: int
    input_length_bucket: str
    prompt_length: int
    prompt_length_bucket: str
    node_task: str

    def as_metadata(self) -> dict[str, Any]:
        return {
            "intent": self.intent,
            "risk_level": self.risk_level,
            "customer_facing": self.customer_facing,
            "knowledge_enabled": self.knowledge_enabled,
            "output_format": self.output_format,
            "schema_required": self.schema_required,
            "has_file_input": self.has_file_input,
            "input_length": self.input_length,
            "input_length_bucket": self.input_length_bucket,
            "prompt_length": self.prompt_length,
            "prompt_length_bucket": self.prompt_length_bucket,
            "node_task": self.node_task,
        }


@dataclass(frozen=True)
class ModelRoutingPolicyDecision:
    selected_model_id: str
    fallback_model_id: Optional[str]
    matched_rule_id: Optional[str]
    reason_code: str
    runtime_context: ModelRoutingRuntimeContext
    decision_source: str
    strategy_id: str
    decision_factors: dict[str, Any] = field(default_factory=dict)
    requires_runtime_judge: bool = False


class ModelRoutingUnavailableError(ValueError):
    pass


class ModelRouter:
    """Judge-first와 충분히 학습된 local-first 사이만 조정한다."""

    LOCAL_ROUTER_AUDIT_RATE = 0.10
    MIN_OPERATIONAL_EVIDENCE_RUNS = 5
    MIN_OPERATIONAL_SUCCESS_RATE = 0.95
    MIN_OPERATIONAL_CONTRACT_PASS_RATE = 0.95
    MAX_OPERATIONAL_FALLBACK_RATE = 0.05

    # Judge 입력에서 이번 요청은 매 실행 달라지는 핵심 신호다. 고정 노드 프롬프트가
    # 길어도 요청 원문이 잘리지 않도록 별도 예산을 둔다.
    _JUDGE_REQUEST_CHAR_BUDGET = 2_200
    _JUDGE_TASK_DESCRIPTION_CHAR_BUDGET = 600
    _JUDGE_PROMPT_SECTION_CHAR_BUDGET = 480
    _JUDGE_OUTPUT_CONTRACT_CHAR_BUDGET = 900
    _PRIMARY_LEARNING_VARIABLE_NAMES = {
        "input",
        "inputtext",
        "instruction",
        "message",
        "prompt",
        "query",
        "question",
        "request",
        "text",
        "userinput",
    }
    _CONTEXT_LEARNING_VARIABLE_NAMES = {
        "constraints",
        "content",
        "context",
        "conversation",
        "documents",
        "evidence",
        "history",
    }
    _NON_EVIDENCE_CONTEXT_NAMES = {
        "constraint",
        "constraints",
        "instruction",
        "instructions",
        "outputmode",
        "rule",
        "rules",
    }
    _INFORMATION_REQUEST_PATTERNS = (
        r"설명(?:해|하|을|이)",
        r"알려\s*(?:줘|주세요|주십시오)",
        r"(?:조회|확인|요약|정리|검색|찾아)\s*(?:해|하|해줘|해주세요)",
        r"\b(?:explain|describe|summarize|show|check|lookup|find)\b",
    )
    _INFORMATION_SCOPE_PATTERNS = (
        r"(?:방법|절차|정책|기준|조건|여부|가능한지|할\s*수\s*있는지)",
        r"\b(?:how|what|why|whether|procedure|policy|guide)\b",
    )
    _RECOMMENDATION_PATTERNS = (
        r"(?:추천|비교|제안|대안|선택지)",
        r"\b(?:recommend|compare|suggest|advise|options?)\b",
    )
    _APPROVAL_PATTERNS = (
        r"(?:승인|거절|허가|판정|판단|결정)(?:을|를)?\s*(?:해|하|내려)",
        r"(?:^|\bplease\s+)(?:approve|reject|authorize|decide|determine)\b",
    )
    _STATE_CHANGE_PATTERNS = (
        r"(?:처리|실행|변경|수정|삭제|생성|등록|적용|취소|차단|지급|환불)\s*(?:해|하|해줘|해주세요|해\s*주세요)",
        r"(?:^|\bplease\s+)(?:execute|run|change|update|delete|create|register|apply|cancel|block|pay|refund)\b",
    )
    _EXTERNAL_SEND_PATTERNS = (
        r"(?:전송|발송)\s*(?:해|하|해줘|해주세요|해\s*주세요)",
        r"보내\s*(?:줘|주세요|주십시오|세요)",
        r"(?:^|\bplease\s+)(?:send|dispatch|publish|notify)\b",
    )
    _NEGATED_ACTION_PATTERNS = (
        r"(?:하지|처리하지|실행하지|변경하지|삭제하지|승인하지|전송하지)\s*(?:마|말|않)",
        r"(?:안|못)\s*(?:해|하|처리|실행|변경|승인|전송)",
        r"\b(?:do\s+not|don't|never)\b",
    )
    _COMPARISON_REQUEST_PATTERNS = (
        r"(?:비교|대조|차이|각각|장단점)",
        r"\b(?:compare|contrast|difference|versus|pros\s+and\s+cons)\b",
    )
    _SYNTHESIS_REQUEST_PATTERNS = (
        r"(?:종합|결합|교차\s*검증|상충|모순|근거를\s*(?:바탕|토대))",
        r"\b(?:synthesize|combine|reconcile|cross-check|based\s+on\s+the\s+evidence)\b",
    )
    _COMPARISON_CRITERION_PATTERNS = (
        r"(?:비용|가격|원가|cost|price)",
        r"(?:속도|지연|응답\s*시간|latency|speed)",
        r"(?:품질|정확도|quality|accuracy)",
        r"(?:성능|처리량|performance|throughput)",
        r"(?:안정성|신뢰성|stability|reliability)",
        r"(?:보안|위험|security|risk)",
    )

    @classmethod
    def resolve_policy(
        cls,
        policy: dict[str, Any],
        *,
        inputs: dict[str, Any],
        node_data: Any,
        available_model_ids: Optional[Iterable[str]] = None,
        routing_feature_text: str | None = None,
        learning_feature_text: str | None = None,
        node_profile: NodeRunProfile | None = None,
    ) -> ModelRoutingPolicyDecision:
        del node_profile  # 운영 품질은 refresh에서 학습 모드 전환에만 사용한다.
        active_policy = policy.get("active_policy") if isinstance(policy, dict) else None
        active_policy = active_policy if isinstance(active_policy, dict) else {}
        if active_policy.get("strategy_id") != JUDGE_FIRST_STRATEGY_ID:
            raise ModelRoutingUnavailableError(
                "Only judge_bootstrap_incremental_v1 policies are executable."
            )

        default_model_id = cls._first_non_empty(
            active_policy.get("default_model_id"),
            cls._node_data_value(node_data, "model_id"),
        )
        fallback_model_id = cls._first_non_empty(
            active_policy.get("fallback_model_id"),
            cls._node_data_value(node_data, "fallback_model_id"),
        )
        if not default_model_id:
            raise ModelRoutingUnavailableError("default model is required.")

        availability_is_enforced = available_model_ids is not None
        executable_model_ids = cls._unique_model_ids(available_model_ids or [])
        configured_candidates = cls._unique_model_ids(
            active_policy.get("candidate_model_ids") or []
        )
        # policy에 저장된 후보는 정책 생성 당시의 snapshot이다. 이후 credential에
        # 새 모델이 연결되거나 예전 policy가 기본 모델 하나만 가진 경우에도 새 후보를
        # 평가할 수 있어야 검증 기회가 사라지는 순환을 피할 수 있다.
        candidates = cls._unique_model_ids(
            [*configured_candidates, *executable_model_ids]
        )
        if availability_is_enforced:
            executable_by_canonical_id: dict[str, str] = {}
            for model_id in executable_model_ids:
                normalized_model_id = cls.normalize_model_id(model_id)
                canonical_id = canonical_model_routing_id(model_id)
                existing = executable_by_canonical_id.get(canonical_id)
                # canonical ID와 별칭이 함께 있으면 canonical API ID를 우선한다.
                # 단, 별칭만 credential에 연결된 경우에는 그 별칭을 보존한다.
                if existing is None or normalized_model_id == canonical_id:
                    executable_by_canonical_id[canonical_id] = model_id

            def available_representative(model_id: str | None) -> str | None:
                if not model_id:
                    return model_id
                return executable_by_canonical_id.get(
                    canonical_model_routing_id(model_id), model_id
                )

            default_model_id = available_representative(default_model_id)
            fallback_model_id = available_representative(fallback_model_id)
            candidates = [
                executable_by_canonical_id[canonical_model_routing_id(model_id)]
                for model_id in candidates
                if canonical_model_routing_id(model_id) in executable_by_canonical_id
            ]
            if not candidates and executable_model_ids:
                candidates = executable_model_ids
            allowed_models: set[str] | None = {
                cls.normalize_model_id(model_id) for model_id in executable_model_ids
            }
        else:
            allowed_models = None

        runtime_context = cls.infer_runtime_context(inputs, node_data)
        effective_routing_feature = routing_feature_text or cls.routing_feature_text(
            inputs, node_data
        )
        structural_facts = cls.runtime_requirement_facts(
            inputs=inputs,
            node_data=node_data,
        )
        default_selected = cls.first_available_model(
            [default_model_id, fallback_model_id, *candidates],
            allowed_models,
        )
        if not default_selected:
            raise ModelRoutingUnavailableError(
                "No Judge-first model is available to the execution subject."
            )
        resolved_fallback = cls.first_available_model(
            [fallback_model_id, default_model_id, *candidates],
            allowed_models,
            exclude=default_selected,
        )

        # 학습 artifact는 배포 policy JSON이 아니라 독립 learner/version에서 온다.
        learning = policy.get("learner")
        learning = learning if isinstance(learning, dict) else {}
        cached_decision = accepted_decision(
            learning,
            feature_text=effective_routing_feature,
            available_model_ids=candidates,
        )
        if cached_decision:
            cached_selected = cls.first_available_model(
                [cached_decision.get("selected_model_id")], allowed_models
            )
            if cached_selected:
                return ModelRoutingPolicyDecision(
                    selected_model_id=cached_selected,
                    fallback_model_id=resolved_fallback,
                    matched_rule_id="accepted-judge-decision-cache",
                    reason_code=str(cached_decision.get("reason_code") or "judge_selected"),
                    runtime_context=runtime_context,
                    decision_source="accepted_judge_cache",
                    strategy_id=JUDGE_FIRST_STRATEGY_ID,
                    decision_factors={
                        "learning_mode": str(learning.get("mode") or "judge_first"),
                        "cached_judge_confidence": cached_decision.get("confidence"),
                        "candidate_model_count": len(candidates),
                    },
                )
        artifact = learning.get("local_requirement_artifact")
        artifact_is_current = (
            isinstance(artifact, dict)
            and artifact.get("feature_schema_version")
            == TASK_REQUIREMENT_FEATURE_SCHEMA_VERSION
        )
        min_confidence = cls._confidence(
            learning.get("local_confidence_threshold"),
            default=0.78,
        )
        if learning.get("mode") == "local_first" and artifact_is_current:
            low_confidence: float | None = None
            try:
                prediction = MultilingualE5TaskRequirementClassifier.predict(
                    artifact,
                    text=learning_feature_text or cls.learning_feature_text(inputs, node_data),
                )
                if prediction is None:
                    raise ValueError("local requirement prediction unavailable")
                selected = cls.select_candidate_for_requirements(
                    candidate_model_ids=candidates,
                    requirements=prediction.requirements,
                    default_model_id=default_selected,
                    structural_facts=structural_facts,
                )
                if selected and prediction.confidence >= min_confidence:
                    if cls.should_audit_local_prediction(
                        learning_feature_text
                        or cls.learning_feature_text(inputs, node_data)
                    ):
                        return ModelRoutingPolicyDecision(
                            selected_model_id=default_selected,
                            fallback_model_id=resolved_fallback,
                            matched_rule_id=None,
                            reason_code="local_router_audit_sample",
                            runtime_context=runtime_context,
                            decision_source="local_router_audit",
                            strategy_id=JUDGE_FIRST_STRATEGY_ID,
                            decision_factors={
                                "learning_mode": "local_first",
                                "local_confidence": prediction.confidence,
                                "local_prediction": prediction.requirements,
                                "audit_rate": cls.LOCAL_ROUTER_AUDIT_RATE,
                            },
                            requires_runtime_judge=True,
                        )
                    return ModelRoutingPolicyDecision(
                        selected_model_id=selected,
                        fallback_model_id=resolved_fallback,
                        matched_rule_id="incremental-local-router",
                        reason_code="local_router_confident",
                        runtime_context=runtime_context,
                        decision_source="local_router",
                        strategy_id=JUDGE_FIRST_STRATEGY_ID,
                        decision_factors={
                            "learning_mode": "local_first",
                            "local_confidence": prediction.confidence,
                            "local_confidence_threshold": min_confidence,
                            "candidate_model_count": len(candidates),
                            "task_requirements": prediction.requirements,
                            "selection_method": "catalog_capability_then_price",
                        },
                    )
                low_confidence = prediction.confidence
            except (RuntimeError, ValueError):
                pass
            return ModelRoutingPolicyDecision(
                selected_model_id=default_selected,
                fallback_model_id=resolved_fallback,
                matched_rule_id=None,
                reason_code="local_router_uncertain",
                runtime_context=runtime_context,
                decision_source="local_router_uncertain",
                strategy_id=JUDGE_FIRST_STRATEGY_ID,
                decision_factors={
                    "learning_mode": "local_first",
                    "local_confidence": low_confidence,
                    "local_confidence_threshold": min_confidence,
                    "candidate_model_count": len(candidates),
                },
                requires_runtime_judge=True,
            )

        return ModelRoutingPolicyDecision(
            selected_model_id=default_selected,
            fallback_model_id=resolved_fallback,
            matched_rule_id=None,
            reason_code="judge_bootstrap_required",
            runtime_context=runtime_context,
            decision_source="runtime_judge_pending",
            strategy_id=JUDGE_FIRST_STRATEGY_ID,
            decision_factors={
                "learning_mode": "judge_first",
                "candidate_model_count": len(candidates),
                "judged_request_count": int(learning.get("judged_request_count") or 0),
            },
            requires_runtime_judge=True,
        )

    @classmethod
    def should_audit_local_prediction(cls, feature_text: str) -> bool:
        """재현 가능한 요청 표본 일부를 Judge 감사 대상으로 선택한다."""

        digest = hashlib.sha256(str(feature_text or "").encode("utf-8")).digest()
        bucket = int.from_bytes(digest[:4], "big") / float(2**32)
        return bucket < cls.LOCAL_ROUTER_AUDIT_RATE

    @classmethod
    def routing_feature_text(
        cls,
        inputs: dict[str, Any],
        node_data: Any,
        *,
        rendered_prompt_parts: Iterable[str] | None = None,
        rag_metadata: dict[str, Any] | None = None,
    ) -> str:
        """Judge/local router에 노드 작업 계약과 이번 요청의 feature를 전달한다.

        세 프롬프트는 노드가 어떤 업무를 수행하는지 알려주는 최소 작업 계약이다.
        현재 요청과 RAG runtime 신호는 같은 노드 안에서도 매 실행 달라지는 판단 재료다.
        """

        if rendered_prompt_parts is None:
            rendered_prompt_parts = cls.render_prompt_parts(inputs, node_data)

        prompt_values = list(rendered_prompt_parts)[:3]
        prompt_values.extend([""] * (3 - len(prompt_values)))
        prompt_sections = (
            ("SYSTEM_PROMPT", prompt_values[0]),
            ("USER_PROMPT", prompt_values[1]),
            ("ASSISTANT_PROMPT", prompt_values[2]),
        )
        prompt_feature = "\n\n".join(
            f"{label}:\n{cls._judge_prompt_excerpt(value)}"
            for label, value in prompt_sections
        )
        task_description = cls._judge_task_description(node_data)
        node_title = str(cls._node_data_value(node_data, "title") or "").strip()
        task_contract_parts = []
        if node_title:
            task_contract_parts.append(f"NODE_TITLE: {node_title[:120]}")
        if task_description:
            task_contract_parts.append(f"TASK_DESCRIPTION:\n{task_description}")
        task_contract_parts.append(f"PROMPT_CONSTRAINTS:\n{prompt_feature}")
        output_contract = cls._judge_output_contract(node_data)
        if output_contract:
            task_contract_parts.append(f"OUTPUT_CONTRACT:\n{output_contract}")
        request_json = cls._judge_request_json(
            cls._routing_runtime_variables(inputs, node_data)
        )
        safe_rag_metadata = {
            key: value
            for key, value in (rag_metadata or {}).items()
            if key
            in {
                "used",
                "retrieved_context_token_estimate",
                "retrieved_context_chars",
                "retrieved_chunk_count",
                "source_count",
                "evidence_sufficient",
                "partial_result",
                "query_rewrite_applied",
                "insufficiency_reason",
                "source_tier_used",
            }
            and isinstance(value, (bool, int, float, str))
        }
        parts = [
            f"CURRENT_REQUEST_JSON:\n{request_json}" if request_json else "",
            "NODE_TASK_CONTRACT:\n" + "\n\n".join(task_contract_parts),
        ]
        if safe_rag_metadata:
            parts.append(
                "RAG_RUNTIME_SIGNALS:\n"
                + json.dumps(safe_rag_metadata, ensure_ascii=False, sort_keys=True)
            )
        return "\n\n".join(part for part in parts if part)

    @classmethod
    def learning_feature_text(
        cls,
        inputs: dict[str, Any],
        node_data: Any,
        *,
        rendered_prompt_parts: Iterable[str] | None = None,
        rag_metadata: dict[str, Any] | None = None,
        effect_profile: Mapping[str, Any] | None = None,
    ) -> str:
        """요청 난이도 학습용 feature를 만든다.

        노드 제목과 prompt 같은 고정 계약은 같은 policy의 모든 실행에 반복되므로
        제외한다. referenced variable의 실행값과 RAG runtime 신호만 남겨 local
        router가 요청별 차이를 학습하게 한다. 변수 메타데이터가 없는 레거시
        노드는 전체 runtime input을 안전하게 축약해 사용한다.
        """

        del rendered_prompt_parts  # routing_feature_text만 고정 prompt 계약을 사용한다.
        runtime_variables = cls._routing_runtime_variables(inputs, node_data)
        feature_groups = cls._learning_feature_groups(runtime_variables)
        routing_contract = cls._learning_routing_contract(
            runtime_variables,
            node_data,
            effect_profile=effect_profile,
        )
        feature_groups["structured_features"]["routing_contract"] = routing_contract
        feature_groups["structured_features"]["requested_action"] = (
            cls._requested_action_features(
                feature_groups["primary_request"],
                routing_contract=routing_contract,
            )
        )
        safe_rag_metadata = {
            key: value
            for key, value in (rag_metadata or {}).items()
            if key
            in {
                "used",
                "retrieved_context_token_estimate",
                "retrieved_context_chars",
                "retrieved_chunk_count",
                "source_count",
                "evidence_sufficient",
                "partial_result",
                "query_rewrite_applied",
                "insufficiency_reason",
                "source_tier_used",
            }
            and isinstance(value, (bool, int, float, str))
        }
        if safe_rag_metadata:
            feature_groups["structured_features"]["rag"] = safe_rag_metadata
        feature_groups["structured_features"]["request_evidence"] = (
            cls._request_evidence_features(
                feature_groups["primary_request"],
                feature_groups["dynamic_context"],
                rag_metadata=safe_rag_metadata,
            )
        )
        bounded_groups = {
            group_name: json.loads(cls._judge_request_json(group_values))
            for group_name, group_values in feature_groups.items()
        }
        return json.dumps(
            bounded_groups,
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )

    @classmethod
    def _requested_action_features(
        cls,
        primary_request: Mapping[str, Any],
        *,
        routing_contract: Mapping[str, Any],
    ) -> dict[str, Any]:
        """명확한 요청 행위만 추출하고 모호한 문장은 보수적으로 남긴다."""

        text = re.sub(r"\s+", " ", cls._flatten_text(primary_request)).casefold()

        def matches(patterns: Iterable[str]) -> bool:
            return any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in patterns)

        negated = matches(cls._NEGATED_ACTION_PATTERNS)
        information_signal = matches(cls._INFORMATION_REQUEST_PATTERNS)
        informational_scope = matches(cls._INFORMATION_SCOPE_PATTERNS)
        recommendation_signal = matches(cls._RECOMMENDATION_PATTERNS)
        approval_signal = matches(cls._APPROVAL_PATTERNS)
        state_change_signal = matches(cls._STATE_CHANGE_PATTERNS)
        external_send_signal = matches(cls._EXTERNAL_SEND_PATTERNS)

        # 실행 동사가 설명·절차·가능 여부의 목적어인 경우에는 실제 실행 요청으로
        # 보지 않는다. 부정 요청도 실행 특징으로 학습하지 않는다.
        action_suppressed = negated or information_signal or informational_scope
        approval_decision = approval_signal and not action_suppressed
        state_change_requested = state_change_signal and not action_suppressed
        external_send_requested = external_send_signal and not action_suppressed
        information_request = information_signal or informational_scope
        recommendation_request = recommendation_signal and not negated

        workflow_effect_match = bool(
            (approval_decision and routing_contract.get("control_gate_present"))
            or (
                state_change_requested
                and (
                    routing_contract.get("external_write_reachable")
                    or routing_contract.get("local_execution_reachable")
                    or routing_contract.get("irreversible_effect_possible")
                )
            )
            or (
                external_send_requested
                and routing_contract.get("customer_output_reachable")
            )
        )
        explicit_signals = sum(
            bool(value)
            for value in (
                information_request,
                recommendation_request,
                approval_decision,
                state_change_requested,
                external_send_requested,
            )
        )
        confidence = 0.0 if not explicit_signals else (0.65 if negated else 1.0)
        return {
            "information_request": information_request,
            "recommendation_request": recommendation_request,
            "approval_decision": approval_decision,
            "state_change_requested": state_change_requested,
            "external_send_requested": external_send_requested,
            "negated_action": negated,
            "informational_scope": informational_scope,
            "workflow_effect_match": workflow_effect_match,
            "confidence": confidence,
        }

    @classmethod
    def _request_evidence_features(
        cls,
        primary_request: Mapping[str, Any],
        dynamic_context: Mapping[str, Any],
        *,
        rag_metadata: Mapping[str, Any],
    ) -> dict[str, Any]:
        """요청마다 달라지는 근거의 양과 결합 요구만 안전한 숫자로 만든다."""

        request_text = re.sub(
            r"\s+",
            " ",
            cls._flatten_text(primary_request),
        ).casefold()

        def matches(patterns: Iterable[str]) -> bool:
            return any(
                re.search(pattern, request_text, flags=re.IGNORECASE)
                for pattern in patterns
            )

        evidence_context = {
            name: value
            for name, value in dynamic_context.items()
            if "".join(char for char in str(name).lower() if char.isalnum())
            not in cls._NON_EVIDENCE_CONTEXT_NAMES
        }
        context_value_count = cls._meaningful_leaf_count(evidence_context)
        context_chars = len(cls._flatten_text(evidence_context))
        if context_chars < 64:
            context_char_bucket = 0
        elif context_chars < 256:
            context_char_bucket = 1
        elif context_chars < 1024:
            context_char_bucket = 2
        else:
            context_char_bucket = 3
        raw_source_count = rag_metadata.get(
            "source_count",
            rag_metadata.get("retrieved_chunk_count", 0),
        )
        try:
            retrieved_source_count = max(0, min(16, int(raw_source_count or 0)))
        except (TypeError, ValueError):
            retrieved_source_count = 0
        return {
            "context_field_count": min(8, len(evidence_context)),
            "context_value_count": min(16, context_value_count),
            "context_char_bucket": context_char_bucket,
            "comparison_requested": matches(cls._COMPARISON_REQUEST_PATTERNS),
            "synthesis_requested": matches(cls._SYNTHESIS_REQUEST_PATTERNS),
            "retrieved_source_count": retrieved_source_count,
            "multi_source_context": max(
                context_value_count,
                retrieved_source_count,
            )
            >= 2,
            "comparison_criterion_count": min(
                8,
                sum(
                    int(re.search(pattern, request_text, flags=re.IGNORECASE) is not None)
                    for pattern in cls._COMPARISON_CRITERION_PATTERNS
                ),
            ),
        }

    @classmethod
    def _meaningful_leaf_count(cls, value: Any) -> int:
        if isinstance(value, Mapping):
            return sum(cls._meaningful_leaf_count(item) for item in value.values())
        if isinstance(value, (list, tuple, set)):
            return sum(cls._meaningful_leaf_count(item) for item in value)
        if value is None or value is False or value == "":
            return 0
        return 1

    @classmethod
    def _learning_routing_contract(
        cls,
        runtime_variables: dict[str, Any],
        node_data: Any,
        *,
        effect_profile: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """임베딩하지 않고 숫자 특징으로 결합할 실행·노드 계약만 만든다."""

        output_format = cls._node_data_value(node_data, "output_format", default={})
        output_format = output_format if isinstance(output_format, dict) else {}
        knowledge_bases = cls._node_data_value(node_data, "knowledgeBases", default=[])
        knowledge_collections = cls._node_data_value(
            node_data,
            "knowledgeCollections",
            default=[],
        )
        flattened_length = len(cls._flatten_text(runtime_variables))
        if flattened_length < 256:
            input_token_bucket = 0
        elif flattened_length < 1024:
            input_token_bucket = 1
        elif flattened_length < 4096:
            input_token_bucket = 2
        else:
            input_token_bucket = 3
        routing_context = cls._node_data_value(
            node_data,
            "model_routing_context",
            default={},
        )
        routing_context = routing_context if isinstance(routing_context, Mapping) else {}
        effects = effect_profile if isinstance(effect_profile, Mapping) else {}
        return {
            "schema_required": bool(output_format.get("schema")),
            "knowledge_enabled": bool(knowledge_bases or knowledge_collections),
            "downstream_contract_required": bool(
                effects.get("downstream_contract_required")
                or cls._node_data_value(
                    node_data, "downstream_contract_required", default=False
                )
            ),
            "customer_facing": bool(
                effects.get("customer_output_reachable")
                or routing_context.get("customer_facing")
            ),
            "has_file_input": cls._contains_file_input(runtime_variables),
            "input_token_bucket": input_token_bucket,
            "external_write_reachable": bool(
                effects.get("external_write_reachable")
            ),
            "external_read_reachable": bool(effects.get("external_read_reachable")),
            "local_execution_reachable": bool(
                effects.get("local_execution_reachable")
            ),
            "customer_output_reachable": bool(
                effects.get("customer_output_reachable")
            ),
            "control_gate_present": bool(effects.get("control_gate_present")),
            "irreversible_effect_possible": bool(
                effects.get("irreversible_effect_possible")
            ),
            "reachable_effect_count": max(
                0,
                int(effects.get("reachable_effect_count") or 0),
            ),
            "human_approval_required": bool(
                effects.get("human_approval_required")
                or routing_context.get("human_approval_required")
            ),
        }

    @classmethod
    def _contains_file_input(cls, value: Any) -> bool:
        if isinstance(value, dict):
            lowered_keys = {str(key).lower() for key in value}
            if lowered_keys & {"file", "files", "filename", "mime_type", "content_type"}:
                return True
            return any(cls._contains_file_input(item) for item in value.values())
        if isinstance(value, (list, tuple, set)):
            return any(cls._contains_file_input(item) for item in value)
        return False

    @classmethod
    def _learning_feature_groups(
        cls,
        runtime_variables: dict[str, Any],
    ) -> dict[str, dict[str, Any]]:
        """실행 변수를 의미·문맥·구조 그룹으로 분리한다."""

        groups: dict[str, dict[str, Any]] = {
            "primary_request": {},
            "dynamic_context": {},
            "structured_features": {},
        }
        for name, value in runtime_variables.items():
            normalized_name = "".join(
                char for char in str(name).lower() if char.isalnum()
            )
            if normalized_name in cls._PRIMARY_LEARNING_VARIABLE_NAMES:
                groups["primary_request"][name] = value
            elif normalized_name in cls._CONTEXT_LEARNING_VARIABLE_NAMES:
                groups["dynamic_context"][name] = value
            elif isinstance(value, (dict, list, tuple, set)):
                groups["dynamic_context"][name] = value
            elif isinstance(value, str) and len(value.strip()) > 64:
                groups["dynamic_context"][name] = value
            else:
                groups["structured_features"][name] = value

        if not groups["primary_request"] and runtime_variables:
            # 이름이 낯선 사용자 정의 변수도 가장 정보량이 큰 실행값을 요청 본문으로
            # 사용할 수 있게 한다. 고정 prompt나 변수명별 제품 하드코딩은 사용하지 않는다.
            primary_name = max(
                runtime_variables,
                key=lambda key: len(cls._flatten_text(runtime_variables[key])),
            )
            groups["primary_request"][primary_name] = runtime_variables[primary_name]
            groups["dynamic_context"].pop(primary_name, None)
            groups["structured_features"].pop(primary_name, None)
        return groups

    @classmethod
    def _learning_runtime_variables(
        cls,
        inputs: dict[str, Any],
        node_data: Any,
    ) -> dict[str, Any]:
        """호환용 별칭. 학습과 Judge는 동일한 실행 입력을 사용한다."""

        return cls._routing_runtime_variables(inputs, node_data)

    @classmethod
    def _routing_runtime_variables(
        cls,
        inputs: dict[str, Any],
        node_data: Any,
    ) -> dict[str, Any]:
        """현재 LLM 프롬프트가 실제로 참조하는 실행값만 Judge에 전달한다.

        변수 참조 메타데이터가 없는 기존 노드는 어떤 입력이 실행에 영향을 주는지
        안전하게 판별할 수 없으므로 이전 계약대로 전체 입력을 유지한다.
        """

        referenced_variables = cls._node_data_value(
            node_data,
            "referenced_variables",
            default=None,
        )
        if not isinstance(referenced_variables, list) or not referenced_variables:
            return inputs

        prompt_variable_names = cls._prompt_variable_names(node_data)
        if prompt_variable_names is None:
            return inputs

        values: dict[str, Any] = {}
        for variable in referenced_variables:
            name = str(
                cls._node_data_value(variable, "name", default="") or ""
            ).strip()
            selector = cls._node_data_value(
                variable,
                "value_selector",
                default=[],
            )
            if (
                not name
                or name not in prompt_variable_names
                or not isinstance(selector, list)
                or not selector
            ):
                continue

            source = inputs.get(str(selector[0]))
            value = cls._nested_value(source, selector[1:])
            if value is not None:
                values[name] = value
        return values

    @classmethod
    def _prompt_variable_names(cls, node_data: Any) -> set[str] | None:
        """세 prompt template에서 실제로 읽는 변수명을 찾는다.

        파싱할 수 없는 template은 runtime에서도 안전하게 렌더할 수 없는 상태이므로,
        라우팅 입력을 줄이지 않고 기존 전체 입력을 유지한다.
        """

        names: set[str] = set()
        try:
            for field in ("system_prompt", "user_prompt", "assistant_prompt"):
                template = str(cls._node_data_value(node_data, field, default="") or "")
                if template:
                    names.update(meta.find_undeclared_variables(_routing_jinja_env.parse(template)))
        except Exception:
            return None
        return names

    @classmethod
    def select_candidate_for_requirements(
        cls,
        *,
        candidate_model_ids: Iterable[str],
        requirements: Mapping[str, Any],
        candidate_profiles: Iterable[Mapping[str, Any]] | None = None,
        default_model_id: str | None = None,
        structural_facts: Mapping[str, Any] | None = None,
    ) -> str | None:
        """현재 사용 가능한 후보 중 요구 수준을 충족하는 가장 경제적인 모델을 고른다.

        운영 성적은 정책 갱신과 local router 전환을 판단하는 데 사용한다. 새 후보를
        runtime에서 배제하면 검증할 기회 자체가 사라지므로, Judge가 평가한 현재 요청의
        요구 수준과 현재 credential/capability 경계 안에서는 전체 후보를 비교한다.
        """

        complexity = cls._requirement_score(requirements.get("task_complexity"))
        impact = cls._requirement_score(requirements.get("decision_impact"))
        evidence = cls._requirement_score(requirements.get("evidence_synthesis"))
        required_ceiling = max(complexity, impact, evidence)
        required_model_score = required_bootstrap_score(
            {
                "task_complexity": complexity,
                "decision_impact": impact,
                "evidence_synthesis": evidence,
            },
            structural_facts=structural_facts,
        )
        ceiling_rank = {"routine": 1, "multi_constraint": 2, "complex_professional": 3}
        reasoning_rank = {
            "non_reasoning": 0,
            "general_reasoning": 2,
            "frontier_reasoning": 3,
            "specialized_reasoning": 3,
        }
        profile_by_model = {
            cls.normalize_model_id(str(profile.get("model_id") or "")): profile
            for profile in (candidate_profiles or [])
            if isinstance(profile, Mapping) and profile.get("model_id")
        }
        normalized_default = cls.normalize_model_id(default_model_id or "")
        facts = structural_facts if isinstance(structural_facts, Mapping) else {}
        task_intent = str(facts.get("task_intent") or "").strip().lower()
        eligible: list[tuple[float, str, Mapping[str, Any]]] = []
        for model_id in cls._unique_model_ids(candidate_model_ids):
            normalized_model_id = cls.normalize_model_id(model_id)
            profile = profile_by_model.get(normalized_model_id, {})
            is_default = bool(normalized_default) and normalized_model_id == normalized_default
            metadata = catalog_metadata_for_model_id(model_id)
            if not metadata:
                if is_default:
                    eligible.append((float("inf"), model_id, profile))
                continue
            bootstrap_score = model_bootstrap_score(model_id)
            if bootstrap_score is None or bootstrap_score < required_model_score:
                continue
            model_ceiling = ceiling_rank.get(str(metadata.get("complexity_ceiling")), 0)
            reasoning = reasoning_rank.get(str(metadata.get("reasoning_profile")), 0)
            if model_ceiling < required_ceiling:
                continue
            if evidence >= 2 and reasoning < 2:
                continue
            # 저가 추론 모델은 제한된 출력 예산을 내부 추론에 대부분 쓸 수 있다.
            # 분류·추출에는 허용하되, 빈 응답이 곧 비싼 재호출로 이어지는 답변
            # 생성 작업에서는 후보에서 제외한다.
            if (
                task_intent in {"generate", "generation", "respond", "response", "chat"}
                and str(metadata.get("capability_tier") or "") == "economy"
                and str(metadata.get("reasoning_profile") or "") != "non_reasoning"
                and str(metadata.get("complexity_ceiling") or "") == "routine"
            ):
                continue
            input_price = profile.get("input_price_per_1k")
            output_price = profile.get("output_price_per_1k")
            if isinstance(input_price, (int, float)) and isinstance(
                output_price, (int, float)
            ):
                price = float(input_price) + float(output_price)
            else:
                pricing = get_model_pricing(model_id)
                price = (
                    float(pricing.standard_input_per_1k + pricing.standard_output_per_1k)
                    if pricing is not None
                    else float("inf")
                )
            eligible.append((price, model_id, profile))
        if not eligible:
            return cls.first_available_model(
                [default_model_id],
                {cls.normalize_model_id(model_id) for model_id in candidate_model_ids},
            )

        # 두 모델 이상이 같은 workflow 정책에서 충분한 운영 계약 성적을 보유한
        # 경우에만, 단순 가격보다 검증된 후보군을 우선한다. 한 모델만 검증된 동안
        # 전체 후보를 막으면 새 모델이 관측될 기회가 사라지는 순환이 생긴다.
        proven = [
            row
            for row in eligible
            if cls._has_qualified_operational_evidence(row[2])
        ]
        selection_pool = proven if len(proven) >= 2 else eligible
        return min(selection_pool, key=lambda row: (row[0], row[1]))[1]

    @classmethod
    def _has_qualified_operational_evidence(
        cls,
        profile: Mapping[str, Any],
    ) -> bool:
        """실제 성공·계약·fallback 성적이 안정적인 후보인지 판정한다."""

        run_count = cls._nonnegative_int(profile.get("operational_run_count"))
        if run_count < cls.MIN_OPERATIONAL_EVIDENCE_RUNS:
            return False
        if cls._rate(profile.get("operational_success_rate")) < cls.MIN_OPERATIONAL_SUCCESS_RATE:
            return False
        if cls._rate(profile.get("operational_fallback_rate")) > cls.MAX_OPERATIONAL_FALLBACK_RATE:
            return False
        for key in (
            "operational_schema_pass_rate",
            "operational_downstream_success_rate",
        ):
            value = profile.get(key)
            if value is not None and cls._rate(value) < cls.MIN_OPERATIONAL_CONTRACT_PASS_RATE:
                return False
        return True

    @classmethod
    def select_safe_fallback_for_requirements(
        cls,
        *,
        candidate_model_ids: Iterable[str],
        requirements: Mapping[str, Any],
        candidate_profiles: Iterable[Mapping[str, Any]] | None = None,
        default_model_id: str | None = None,
        structural_facts: Mapping[str, Any] | None = None,
    ) -> str | None:
        """불확실한 판정에서 약한 configured fallback으로 바로 내려가지 않는다."""

        candidates = cls._unique_model_ids(candidate_model_ids)
        safe_floor = required_safe_fallback_score(
            requirements,
            structural_facts=structural_facts,
        )
        safe_candidates = [
            model_id
            for model_id in candidates
            if (model_bootstrap_score(model_id) or -1.0) >= safe_floor
        ]
        if safe_candidates:
            selected = cls.select_candidate_for_requirements(
                candidate_model_ids=safe_candidates,
                requirements=requirements,
                candidate_profiles=candidate_profiles,
                default_model_id=default_model_id,
                structural_facts=structural_facts,
            )
            if selected:
                return selected

        normalized_default = cls.normalize_model_id(default_model_id or "")
        scored_candidates = [
            (score, cls.normalize_model_id(model_id) == normalized_default, model_id)
            for model_id in candidates
            if (score := model_bootstrap_score(model_id)) is not None
        ]
        if scored_candidates:
            return max(scored_candidates, key=lambda row: (row[0], row[1]))[2]
        return cls.first_available_model(
            [default_model_id],
            {cls.normalize_model_id(model_id) for model_id in candidates},
        )

    @staticmethod
    def _nonnegative_int(value: Any) -> int:
        try:
            return max(0, int(value))
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _rate(value: Any) -> float:
        try:
            return max(0.0, min(1.0, float(value)))
        except (TypeError, ValueError):
            return 0.0

    @classmethod
    def runtime_requirement_facts(
        cls,
        *,
        inputs: Mapping[str, Any],
        node_data: Any,
        rag_metadata: Mapping[str, Any] | None = None,
        downstream_contract_required: bool = False,
        effect_profile: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """LLM이 추측할 필요가 없는 실행 구조 사실을 계산한다."""

        runtime_inputs = cls._routing_runtime_variables(dict(inputs), node_data)
        output_format = cls._node_data_value(node_data, "output_format", default={})
        output_format = output_format if isinstance(output_format, dict) else {}
        output_type = str(output_format.get("type") or "text").strip().lower()
        serialized_size = len(
            json.dumps(runtime_inputs, ensure_ascii=False, default=str)
        )
        if serialized_size < 2_000:
            input_bucket = "short"
        elif serialized_size < 12_000:
            input_bucket = "medium"
        else:
            input_bucket = "long"
        knowledge_bases = cls._node_data_value(
            node_data, "knowledgeBases", default=[]
        ) or cls._node_data_value(node_data, "knowledge_bases", default=[])
        rag = rag_metadata if isinstance(rag_metadata, Mapping) else {}
        effects = effect_profile if isinstance(effect_profile, Mapping) else {}
        routing_context = cls._node_data_value(
            node_data,
            "model_routing_context",
            default={},
        )
        routing_context = routing_context if isinstance(routing_context, Mapping) else {}
        task_intent = cls._first_non_empty(
            routing_context.get("intent"),
            routing_context.get("node_task"),
            routing_context.get("category"),
            cls._node_data_value(node_data, "task_type"),
        ) or "generate"
        return {
            "task_intent": task_intent,
            "input_token_bucket": input_bucket,
            "schema_required": bool(output_format.get("schema")),
            "knowledge_enabled": bool(knowledge_bases) or bool(rag.get("used")),
            "retrieved_source_count": max(
                0,
                int(rag.get("source_count") or rag.get("retrieved_chunk_count") or 0),
            ),
            "output_format": output_type,
            "downstream_contract_required": bool(
                downstream_contract_required
                or effects.get("downstream_contract_required")
            ),
            "file_input_present": cls._contains_file_like_value(runtime_inputs),
            "customer_facing": bool(
                effects.get("customer_output_reachable")
                or routing_context.get("customer_facing")
            ),
            "external_write_reachable": bool(
                effects.get("external_write_reachable")
            ),
            "external_read_reachable": bool(effects.get("external_read_reachable")),
            "local_execution_reachable": bool(
                effects.get("local_execution_reachable")
            ),
            "customer_output_reachable": bool(
                effects.get("customer_output_reachable")
            ),
            "control_gate_present": bool(effects.get("control_gate_present")),
            "irreversible_effect_possible": bool(
                effects.get("irreversible_effect_possible")
            ),
            "reachable_effect_count": max(
                0,
                int(effects.get("reachable_effect_count") or 0),
            ),
            "human_approval_required": bool(
                effects.get("human_approval_required")
                or routing_context.get("human_approval_required")
            ),
        }

    @classmethod
    def _contains_file_like_value(cls, value: Any) -> bool:
        if isinstance(value, Mapping):
            if any(
                str(key).lower() in {"file", "files", "filename", "mime_type"}
                for key in value
            ):
                return True
            return any(cls._contains_file_like_value(child) for child in value.values())
        if isinstance(value, (list, tuple)):
            return any(cls._contains_file_like_value(child) for child in value)
        return False

    @staticmethod
    def _requirement_score(value: Any) -> int:
        if isinstance(value, bool):
            return 0
        try:
            return max(0, min(3, int(value)))
        except (TypeError, ValueError):
            return 0

    @classmethod
    def _judge_request_json(cls, inputs: dict[str, Any]) -> str:
        """Preserve request keys and scalar types within the Judge budget."""

        for string_limit, item_limit in ((900, 24), (360, 16), (160, 10)):
            bounded = cls._bounded_judge_value(
                inputs,
                string_limit=string_limit,
                item_limit=item_limit,
            )
            serialized = json.dumps(
                bounded,
                ensure_ascii=False,
                sort_keys=True,
                default=str,
            )
            if len(serialized) <= cls._JUDGE_REQUEST_CHAR_BUDGET:
                return serialized

        preview_budget = max(cls._JUDGE_REQUEST_CHAR_BUDGET - 80, 0)
        return json.dumps(
            {
                "_truncated": True,
                "text_preview": cls._flatten_text(inputs)[:preview_budget],
            },
            ensure_ascii=False,
            sort_keys=True,
        )

    @classmethod
    def _bounded_judge_value(
        cls,
        value: Any,
        *,
        string_limit: int,
        item_limit: int,
    ) -> Any:
        if value is None or isinstance(value, (bool, int, float)):
            return value
        if isinstance(value, str):
            if len(value) <= string_limit:
                return value
            return value[: string_limit - 1].rstrip() + "…"
        if isinstance(value, dict):
            items = list(value.items())
            result = {
                str(key): cls._bounded_judge_value(
                    child,
                    string_limit=string_limit,
                    item_limit=item_limit,
                )
                for key, child in items[:item_limit]
            }
            if len(items) > item_limit:
                result["_omitted_key_count"] = len(items) - item_limit
            return result
        if isinstance(value, (list, tuple, set)):
            items = list(value)
            result = [
                cls._bounded_judge_value(
                    child,
                    string_limit=string_limit,
                    item_limit=item_limit,
                )
                for child in items[:item_limit]
            ]
            if len(items) > item_limit:
                result.append({"_omitted_item_count": len(items) - item_limit})
            return result
        return str(value)[:string_limit]

    @classmethod
    def _judge_prompt_excerpt(cls, value: Any) -> str:
        text = str(value or "").strip()
        if len(text) <= cls._JUDGE_PROMPT_SECTION_CHAR_BUDGET:
            return text
        return text[: cls._JUDGE_PROMPT_SECTION_CHAR_BUDGET - 1].rstrip() + "…"

    @classmethod
    def _judge_output_contract(cls, node_data: Any) -> str:
        """구조화 출력 복잡도 신호를 prompt 예산과 독립적으로 보존한다."""

        parameters = cls._node_data_value(node_data, "parameters", default={})
        parameters = parameters if isinstance(parameters, Mapping) else {}
        output_format = cls._node_data_value(node_data, "output_format")
        force_json_object = response_format_requires_json_instruction(
            parameters.get("response_format")
        )
        schema_instruction = build_json_output_schema_instruction(
            output_format,
            force_json_object=force_json_object,
        )
        if not schema_instruction:
            return ""

        schema = (
            output_format.get("schema") if isinstance(output_format, dict) else None
        )
        properties = schema.get("properties") if isinstance(schema, dict) else None
        required = schema.get("required") if isinstance(schema, dict) else None
        summary = (
            "OUTPUT_MODE: json_object\n"
            "SCHEMA_PRESENT: "
            f"{str(isinstance(schema, dict) and bool(schema)).lower()}\n"
            "TOP_LEVEL_TYPE: "
            f"{schema.get('type', 'unspecified') if isinstance(schema, dict) else 'unspecified'}\n"
            "TOP_LEVEL_PROPERTY_COUNT: "
            f"{len(properties) if isinstance(properties, dict) else 0}\n"
            "REQUIRED_PROPERTY_COUNT: "
            f"{len(required) if isinstance(required, list) else 0}"
        )
        contract = f"{summary}\n\n{schema_instruction}"
        if len(contract) <= cls._JUDGE_OUTPUT_CONTRACT_CHAR_BUDGET:
            return contract
        return (
            contract[: cls._JUDGE_OUTPUT_CONTRACT_CHAR_BUDGET - 1].rstrip() + "…"
        )

    @classmethod
    def _judge_task_description(cls, node_data: Any) -> str:
        """사용자가 적은 작업 설명을 고정 작업 계약의 중심 정보로 사용한다."""

        value = str(
            cls._node_data_value(node_data, "model_routing_task_description") or ""
        ).strip()
        if len(value) <= cls._JUDGE_TASK_DESCRIPTION_CHAR_BUDGET:
            return value
        return value[: cls._JUDGE_TASK_DESCRIPTION_CHAR_BUDGET - 1].rstrip() + "…"

    @classmethod
    def infer_runtime_context(
        cls,
        inputs: dict[str, Any],
        node_data: Any,
    ) -> ModelRoutingRuntimeContext:
        runtime_inputs = cls._routing_runtime_variables(inputs, node_data)
        text = cls._flatten_text(runtime_inputs)
        prompts = " ".join(
            prompt
            for prompt in (
                str(cls._node_data_value(node_data, field) or "").strip()
                for field in ("system_prompt", "user_prompt", "assistant_prompt")
            )
            if prompt
        )
        routing_context = cls._node_data_value(node_data, "model_routing_context")
        routing_context = routing_context if isinstance(routing_context, dict) else {}
        output_format_value = cls._node_data_value(node_data, "output_format")
        knowledge_enabled = bool(
            cls._node_data_value(node_data, "knowledgeBases")
            or cls._node_data_value(node_data, "knowledgeCollections")
        )
        node_task = cls._first_non_empty(
            routing_context.get("node_task"),
            routing_context.get("category"),
            cls._node_data_value(node_data, "task_type"),
        ) or "generate"
        return ModelRoutingRuntimeContext(
            text=text,
            intent=cls._first_non_empty(routing_context.get("intent"), node_task)
            or "generate",
            risk_level=cls._first_non_empty(routing_context.get("risk_level"))
            or "medium",
            customer_facing=bool(routing_context.get("customer_facing", False)),
            knowledge_enabled=knowledge_enabled,
            output_format=cls._output_format_name(output_format_value),
            schema_required=cls._schema_required(output_format_value),
            has_file_input=cls._has_file_input(runtime_inputs),
            input_length=len(text),
            input_length_bucket=cls._length_bucket(len(text)),
            prompt_length=len(prompts),
            prompt_length_bucket=cls._length_bucket(len(prompts)),
            node_task=node_task,
        )

    @classmethod
    def collect_candidates(
        cls,
        db: Session,
        *,
        organization_id: uuid.UUID,
    ) -> list[ModelCandidate]:
        models = (
            db.query(LLMModel)
            .join(LLMRelCredentialModel, LLMRelCredentialModel.model_id == LLMModel.id)
            .join(LLMCredential, LLMCredential.id == LLMRelCredentialModel.credential_id)
            .filter(LLMModel.is_active.is_(True))
            .filter(LLMModel.type == "chat")
            .filter(LLMCredential.organization_id == organization_id)
            .filter(LLMCredential.is_valid.is_(True))
            .filter(LLMRelCredentialModel.is_verified.is_(True))
            .all()
        )
        by_model_id: dict[str, ModelCandidate] = {}
        for model in models:
            if cls.is_workflow_chat_model(model):
                candidate = ModelCandidate.from_model(model)
                by_model_id[candidate.model_id] = candidate
        return list(by_model_id.values())

    @classmethod
    def is_workflow_chat_model(cls, model: Any) -> bool:
        raw_model_id = (
            model
            if isinstance(model, str)
            else getattr(model, "model_id_for_api_call", None)
            or getattr(model, "model_id", "")
        )
        model_id = cls.normalize_model_id(raw_model_id)
        model_name = str(getattr(model, "name", "") or "").lower()
        model_type = str(getattr(model, "type", "") or "").lower()
        if not bool(getattr(model, "is_active", True)):
            return False
        if is_workflow_execution_model_excluded(raw_model_id):
            return False
        if model_type in BLOCKED_WORKFLOW_MODEL_TYPES:
            return False
        if any(keyword in model_id for keyword in BLOCKED_WORKFLOW_MODEL_KEYWORDS):
            return False
        if any(keyword in model_name for keyword in BLOCKED_WORKFLOW_MODEL_KEYWORDS):
            return False
        return model_id in WORKFLOW_CHAT_MODEL_ALIASES

    @staticmethod
    def normalize_model_id(model_id: Any) -> str:
        return normalize_model_routing_id(model_id)

    @classmethod
    def first_available_model(
        cls,
        model_ids: Iterable[Any],
        allowed_models: Optional[set[str]],
        *,
        exclude: Optional[str] = None,
    ) -> Optional[str]:
        normalized_exclude = cls.normalize_model_id(exclude)
        for model_id in model_ids:
            candidate = cls._first_non_empty(model_id)
            if not candidate:
                continue
            normalized_candidate = cls.normalize_model_id(candidate)
            if normalized_exclude and normalized_candidate == normalized_exclude:
                continue
            if allowed_models is not None and normalized_candidate not in allowed_models:
                continue
            return candidate
        return None

    @staticmethod
    def _first_non_empty(*values: Any) -> Optional[str]:
        for value in values:
            text = str(value or "").strip()
            if text:
                return text
        return None

    @classmethod
    def render_prompt_parts(
        cls,
        inputs: dict[str, Any],
        node_data: Any,
    ) -> tuple[str, str, str]:
        """Preview와 runtime routing이 공유하는 side-effect 없는 prompt renderer."""

        values: dict[str, Any] = {}
        referenced_variables = cls._node_data_value(
            node_data,
            "referenced_variables",
            default=[],
        )
        if not isinstance(referenced_variables, list):
            referenced_variables = []
        for variable in referenced_variables:
            name = str(cls._node_data_value(variable, "name", default="") or "").strip()
            selector = cls._node_data_value(variable, "value_selector", default=[])
            if not name or not isinstance(selector, list) or not selector:
                continue
            source = inputs.get(str(selector[0]))
            value = cls._nested_value(source, selector[1:])
            values[name] = value if value is not None else ""
        rendered = {
            field: cls._render_template(
                cls._node_data_value(node_data, field, default=""), values
            )
            for field in ("system_prompt", "user_prompt", "assistant_prompt")
        }
        return (
            rendered["system_prompt"],
            rendered["user_prompt"],
            rendered["assistant_prompt"],
        )

    @staticmethod
    def _node_data_value(value: Any, field: str, *, default: Any = None) -> Any:
        if isinstance(value, Mapping):
            return value.get(field, default)
        return getattr(value, field, default)

    @staticmethod
    def _nested_value(value: Any, path: list[Any]) -> Any:
        current = value
        for key in path:
            if not isinstance(current, Mapping):
                return None
            current = current.get(str(key))
        return current

    @staticmethod
    def _render_template(template: Any, context: dict[str, Any]) -> str:
        source = str(template or "")
        if not source:
            return ""
        try:
            return _routing_jinja_env.from_string(source).render(**context)
        except Exception as exc:
            raise ModelRoutingPromptRenderError(
                "model_routing.prompt_render_failed"
            ) from exc

    @classmethod
    def _flatten_text(cls, value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, str):
            return value
        if isinstance(value, (int, float, bool)):
            return str(value)
        if isinstance(value, dict):
            return " ".join(
                f"{key}: {child_text}"
                for key, child in value.items()
                if (child_text := cls._flatten_text(child))
            )
        if isinstance(value, (list, tuple, set)):
            return " ".join(cls._flatten_text(item) for item in value)
        try:
            return json.dumps(value, ensure_ascii=False, default=str)
        except TypeError:
            return str(value)

    @staticmethod
    def _output_format_name(output_format: Any) -> str:
        if isinstance(output_format, dict):
            return str(output_format.get("type") or "text").lower()
        return str(output_format or "text").lower()

    @staticmethod
    def _schema_required(output_format: Any) -> bool:
        return (
            isinstance(output_format, dict)
            and str(output_format.get("type") or "").lower() == "json"
            and isinstance(output_format.get("schema"), dict)
            and bool(output_format.get("schema"))
        )

    @classmethod
    def _has_file_input(cls, value: Any) -> bool:
        if isinstance(value, dict):
            for key, item in value.items():
                if str(key).casefold() in {
                    "file",
                    "files",
                    "file_id",
                    "filename",
                    "attachment",
                    "attachments",
                } and cls._has_meaningful_value(item):
                    return True
                if cls._has_file_input(item):
                    return True
        if isinstance(value, list):
            return any(cls._has_file_input(item) for item in value)
        return False

    @classmethod
    def _has_meaningful_value(cls, value: Any) -> bool:
        if value in (None, ""):
            return False
        if isinstance(value, dict):
            return any(cls._has_meaningful_value(item) for item in value.values())
        if isinstance(value, list):
            return any(cls._has_meaningful_value(item) for item in value)
        return True

    @staticmethod
    def _length_bucket(length: int) -> str:
        if length <= 500:
            return "short"
        if length <= 2_000:
            return "medium"
        return "long"

    @staticmethod
    def _confidence(value: Any, *, default: float) -> float:
        try:
            return max(0.0, min(1.0, float(value)))
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _unique_model_ids(model_ids: Iterable[Any]) -> list[str]:
        seen: set[str] = set()
        result: list[str] = []
        for model_id in model_ids:
            value = str(model_id or "").strip()
            if value and value not in seen:
                seen.add(value)
                result.append(value)
        return result


def _ratio(numerator: int, denominator: int) -> Optional[float]:
    return numerator / denominator if denominator > 0 else None


def _float_or_none(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return float(value)
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
