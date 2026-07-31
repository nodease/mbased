"""Cost Optimizer 추천 후보의 출력 품질을 안전하게 비교한다."""

from __future__ import annotations

import json
import math
import secrets
import time
from typing import Any

from apps.gateway.services.llm_service import LLMService
from apps.shared.services.tracing.policy import TracePolicyService
from apps.shared.services.tracing.redaction import TraceRedactionService


class CostOptimizerOutputQualityService:
    """A/B 출력의 품질을 blind pairwise judge로 평가하는 서비스.

    baseline과 candidate라는 이름은 judge prompt에 전달하지 않는다. 평가 결과와
    usage/cost의 safe summary만 caller에게 반환하며, 원문 입력/출력은 저장하지 않는다.
    """

    PROMPT_VERSION = "cost-optimizer-output-quality-judge-v1"
    BASE_DIMENSIONS = (
        "instruction_fulfillment",
        "relevance_completeness",
        "clarity_consistency",
        "factual_reliability",
    )
    JUDGE_OMITTED_FIELDS = frozenset(
        {
            "metadata",
            "rawchunkcontent",
            "rawpayload",
            "usage",
        }
    )
    JUDGE_BLOCKED_MODEL_KEYWORDS = (
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
        "instruct",
    )
    JUDGE_LEGACY_COMPLETION_PREFIXES = (
        "ada",
        "babbage",
        "curie",
        "davinci",
        "text-",
    )

    @classmethod
    def evaluate(
        cls,
        *,
        db: Any,
        workflow: Any,
        current_user: Any,
        node_id: str,
        candidate_row: Any,
        baseline: dict[str, Any],
        candidate_result: dict[str, Any],
        judge_client: Any | None = None,
        judge_model_id: str | None = None,
        pair_order: str | None = None,
    ) -> dict[str, Any]:
        """동일 입력에 대한 두 출력의 상대 품질을 평가한다.

        judge가 없거나 호출에 실패해도 candidate 실행 결과는 유효하므로 unavailable
        응답을 돌려준다. 이 경우 caller는 verification_status를 partial로 결정한다.
        """
        try:
            if judge_client is not None:
                selected_model_id = judge_model_id or cls._select_judge_model(
                    db=db,
                    user_id=current_user.id,
                )
                client = judge_client
            else:
                selected_model_id, client = cls._select_judge_runtime(
                    db=db,
                    user_id=current_user.id,
                    organization_id=getattr(workflow, "organization_id", None),
                    preferred_model_id=judge_model_id,
                )
            if not selected_model_id or client is None:
                return cls._unavailable("품질 평가에 사용할 수 있는 LLM credential/model이 없습니다.")
            order = pair_order or cls._pair_order(baseline, candidate_result)
            expected_dimensions = cls._expected_dimensions(
                baseline,
                candidate_result,
            )
            messages = cls._build_messages(
                baseline=baseline,
                candidate_result=candidate_result,
                pair_order=order,
                dimensions=expected_dimensions,
            )
            started = time.perf_counter()
            response = client.invoke_sync(messages, temperature=0.0, max_tokens=700)
            latency_ms = int((time.perf_counter() - started) * 1000)
            usage = cls._usage_from_response(response)
            usage["latency_ms"] = latency_ms
            cost = LLMService.calculate_cost(
                db,
                selected_model_id,
                int(usage.get("prompt_tokens") or 0),
                int(usage.get("completion_tokens") or 0),
                usage=usage,
            )
            usage_log = LLMService.log_usage(
                db,
                user_id=current_user.id,
                model_id=selected_model_id,
                usage=usage,
                cost=cost,
                organization_id=getattr(workflow, "organization_id", None),
                workflow_id=getattr(workflow, "id", None),
                node_id=f"{node_id}:quality-judge",
                cost_optimizer_candidate_id=getattr(candidate_row, "id", None),
            )
            try:
                parsed = cls._parse_response(cls._content_from_response(response))
                quality = cls._normalize_result(
                    parsed,
                    pair_order=order,
                    expected_dimensions=expected_dimensions,
                )
            except Exception:
                quality = cls._unavailable("품질 평가 응답을 해석하지 못했습니다.")

            quality["judge_cost"] = float(cost) if cost is not None else None
            quality["judge_usage_log_id"] = (
                str(usage_log.id) if usage_log is not None else None
            )
            quality["judge"] = cls._judge_summary(selected_model_id, usage)
            return quality
        except Exception:
            # provider/credential 오류의 원문을 response, audit, 일반 trace에 전파하지 않는다.
            return cls._unavailable("품질 평가를 완료하지 못했습니다.")

    @classmethod
    def _select_judge_model(cls, *, db: Any, user_id: Any) -> str | None:
        models = LLMService.get_my_available_models(db, user_id)
        chat_models = cls._ordered_judge_models(models)
        if not chat_models:
            return None
        return str(getattr(chat_models[0], "model_id_for_api_call", "") or "") or None

    @classmethod
    def _ordered_judge_models(cls, models: Any) -> list[Any]:
        """JSON 품질 평가에 적합한 모델만 남기고 균형형 모델을 우선한다."""

        eligible = [model for model in models if cls._is_usable_judge_model(model)]
        return sorted(eligible, key=cls._judge_model_rank)

    @classmethod
    def _is_usable_judge_model(cls, model: Any) -> bool:
        model_id = cls._normalize_model_id(
            getattr(model, "model_id_for_api_call", "")
        )
        model_name = str(getattr(model, "name", "") or "").lower()
        model_type = str(getattr(model, "type", "") or "").lower()

        if not model_id or model_type != "chat":
            return False
        if getattr(model, "is_active", True) is False:
            return False
        if model_id.startswith(cls.JUDGE_LEGACY_COMPLETION_PREFIXES):
            return False
        if any(keyword in model_id for keyword in cls.JUDGE_BLOCKED_MODEL_KEYWORDS):
            return False
        if any(keyword in model_name for keyword in cls.JUDGE_BLOCKED_MODEL_KEYWORDS):
            return False
        return True

    @classmethod
    def _judge_model_rank(cls, model: Any) -> tuple[int, str]:
        """Provider별 고성능·비용 균형형 계열을 저가형보다 먼저 선택한다."""

        model_id = cls._normalize_model_id(
            getattr(model, "model_id_for_api_call", "")
        )
        if model_id == "gpt-4.1-mini":
            priority = 0
        elif "claude" in model_id and "sonnet" in model_id:
            priority = 0
        elif "gemini" in model_id and "flash" in model_id and "lite" not in model_id:
            priority = 0
        elif "gpt" in model_id and "mini" in model_id and "nano" not in model_id:
            priority = 1
        elif "claude" in model_id and "haiku" in model_id:
            priority = 1
        elif "gemini" in model_id and "pro" in model_id:
            priority = 1
        elif "nano" in model_id or "lite" in model_id:
            priority = 3
        else:
            priority = 2
        return priority, model_id

    @staticmethod
    def _normalize_model_id(model_id: Any) -> str:
        return str(model_id or "").strip().lower().removeprefix("models/")

    @classmethod
    def _judge_summary(
        cls,
        model_id: str,
        usage: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "role": "quality_judge",
            "model_id": model_id,
            "prompt_version": cls.PROMPT_VERSION,
            "usage": {
                "prompt_tokens": int(usage.get("prompt_tokens") or 0),
                "completion_tokens": int(usage.get("completion_tokens") or 0),
                "total_tokens": int(usage.get("total_tokens") or 0),
            },
        }

    @classmethod
    def _select_judge_runtime(
        cls,
        *,
        db: Any,
        user_id: Any,
        organization_id: Any,
        preferred_model_id: str | None,
    ) -> tuple[str | None, Any | None]:
        models = LLMService.get_my_available_models(db, user_id)
        candidate_ids = [
            str(getattr(model, "model_id_for_api_call", "") or "")
            for model in cls._ordered_judge_models(models)
        ]
        candidate_ids = [model_id for model_id in candidate_ids if model_id]
        if preferred_model_id:
            preferred_normalized = cls._normalize_model_id(preferred_model_id)
            candidate_ids = [
                model_id
                for model_id in candidate_ids
                if cls._normalize_model_id(model_id) == preferred_normalized
            ]
        for model_id in candidate_ids:
            try:
                return model_id, LLMService.get_client_for_user(
                    db,
                    user_id,
                    model_id,
                    organization_id,
                )
            except Exception:
                continue
        return None, None

    @classmethod
    def _build_messages(
        cls,
        *,
        baseline: dict[str, Any],
        candidate_result: dict[str, Any],
        pair_order: str,
        dimensions: tuple[str, ...],
    ) -> list[dict[str, str]]:
        baseline_variant = cls._variant_from_result(baseline)
        candidate_variant = cls._variant_from_result(candidate_result)
        left, right = (
            (baseline_variant, candidate_variant)
            if pair_order == "baseline_left"
            else (candidate_variant, baseline_variant)
        )
        payload = {
            "task": "Evaluate two anonymized LLM outputs for the same input.",
            "dimensions": list(dimensions),
            "scoring": "Score each dimension from 0 to 100. Do not assume either variant is correct.",
            "evaluation_policy": {
                "unsupported_specific_claims": "penalize",
                "transparent_uncertainty": "do_not_penalize",
            },
            "dimension_guidance": {
                "factual_reliability": (
                    "Penalize product-specific, organizational, or factual details "
                    "that are not supported by the provided input or authoritative evidence. "
                    "Transparent uncertainty is safer than invented specificity."
                ),
            },
            "response_schema": {
                "variant_left": {dimension: "0..100" for dimension in dimensions},
                "variant_right": {dimension: "0..100" for dimension in dimensions},
                "confidence": "0..1",
            },
            "variant_left": left,
            "variant_right": right,
        }
        return [
            {
                "role": "system",
                "content": (
                    "Return JSON only. Evaluate each anonymized variant independently and fairly. "
                    "Do not identify a baseline, a candidate, or a ground-truth answer. "
                    "When the input provides no authoritative evidence for a product-specific or factual claim, "
                    "penalize unsupported specific instructions in factual_reliability and do not penalize "
                    "transparent uncertainty. Treat authoritative_evidence_available=false as no factual basis "
                    "for organization-specific locations, policies, limits, or procedures. "
                    "Do not quote sensitive source content in the response."
                ),
            },
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ]

    @classmethod
    def _expected_dimensions(
        cls,
        baseline: dict[str, Any],
        candidate_result: dict[str, Any],
    ) -> tuple[str, ...]:
        dimensions = list(cls.BASE_DIMENSIONS)
        if cls._rag_enabled(baseline) or cls._rag_enabled(candidate_result):
            dimensions.append("groundedness")
        return tuple(dimensions)

    @staticmethod
    def _rag_enabled(result: dict[str, Any]) -> bool:
        trace = result.get("trace") if isinstance(result, dict) else None
        if not isinstance(trace, dict):
            return False
        return bool(
            CostOptimizerOutputQualityService._safe_rag_summary(
                trace.get("rag_summary")
            )
        )

    @classmethod
    def _variant_from_result(cls, result: dict[str, Any]) -> dict[str, Any]:
        output = result.get("output") if isinstance(result, dict) else {}
        output = output if isinstance(output, dict) else {"text": output}
        trace = result.get("trace") if isinstance(result, dict) else {}
        trace = trace if isinstance(trace, dict) else {}
        rag_summary = cls._safe_rag_summary(trace.get("rag_summary"))
        return {
            "input": cls._judge_visible_value(
                result.get("input") if isinstance(result, dict) else None
            ),
            "output": cls._judge_visible_value(output),
            "rag_enabled": bool(rag_summary),
            "authoritative_evidence_available": cls._authoritative_evidence_available(
                rag_summary
            ),
            "rag_summary": rag_summary,
        }

    @classmethod
    def _judge_visible_value(cls, value: Any) -> Any:
        """Judge 입력에서 내부 메타데이터를 제거하고 공통 redaction을 적용한다."""
        bounded = cls._bounded_value(value)
        stripped = cls._strip_judge_internal_fields(bounded)
        redaction = TraceRedactionService.redact_payload(
            stripped,
            TracePolicyService.fail_closed_redaction_policy(),
            payload_kind="cost_optimizer_quality_judge",
        )
        if redaction.failed:
            raise ValueError("cost_optimizer.quality_judge_redaction_failed")
        return cls._bounded_value(redaction.redacted_payload)

    @classmethod
    def _strip_judge_internal_fields(cls, value: Any) -> Any:
        if isinstance(value, dict):
            return {
                str(key): cls._strip_judge_internal_fields(item)
                for key, item in value.items()
                if cls._normalized_judge_field(key) not in cls.JUDGE_OMITTED_FIELDS
            }
        if isinstance(value, list):
            return [cls._strip_judge_internal_fields(item) for item in value]
        return value

    @staticmethod
    def _normalized_judge_field(value: Any) -> str:
        return "".join(
            character for character in str(value).lower() if character.isalnum()
        )

    @staticmethod
    def _bounded_value(
        value: Any,
        *,
        max_chars: int = 12000,
        max_items: int = 50,
        depth: int = 5,
    ) -> Any:
        if depth <= 0:
            return "[TRUNCATED]"
        if isinstance(value, str):
            return value[:max_chars]
        if isinstance(value, dict):
            bounded: dict[str, Any] = {}
            for index, (key, item) in enumerate(value.items()):
                if index >= max_items:
                    bounded["__truncated__"] = True
                    break
                bounded[str(key)] = CostOptimizerOutputQualityService._bounded_value(
                    item,
                    max_chars=max_chars,
                    max_items=max_items,
                    depth=depth - 1,
                )
            return bounded
        if isinstance(value, list):
            bounded = [
                CostOptimizerOutputQualityService._bounded_value(
                    item,
                    max_chars=max_chars,
                    max_items=max_items,
                    depth=depth - 1,
                )
                for item in value[:max_items]
            ]
            if len(value) > max_items:
                bounded.append("[TRUNCATED]")
            return bounded
        return value

    @staticmethod
    def _safe_rag_summary(value: Any) -> dict[str, Any] | None:
        if not isinstance(value, dict):
            return None
        allowed = ("retrieved_chunk_count", "context_token_estimate", "evidence_sufficient")
        return {key: value.get(key) for key in allowed if key in value}

    @staticmethod
    def _authoritative_evidence_available(value: Any) -> bool:
        if not isinstance(value, dict) or value.get("evidence_sufficient") is not True:
            return False
        count = value.get("retrieved_chunk_count")
        return not isinstance(count, bool) and isinstance(count, int) and count > 0

    @staticmethod
    def _pair_order(baseline: dict[str, Any], candidate_result: dict[str, Any]) -> str:
        # 호출마다 baseline이 항상 왼쪽에 놓이는 위치 편향을 피한다.
        del baseline, candidate_result
        return "baseline_left" if secrets.randbelow(2) == 0 else "candidate_left"

    @staticmethod
    def _content_from_response(response: Any) -> str:
        if isinstance(response, dict):
            choices = response.get("choices") or []
            if choices and isinstance(choices[0], dict):
                message = choices[0].get("message") or {}
                if isinstance(message, dict) and isinstance(message.get("content"), str):
                    return message["content"]
        return ""

    @staticmethod
    def _usage_from_response(response: Any) -> dict[str, int]:
        usage = response.get("usage") if isinstance(response, dict) else {}
        usage = usage if isinstance(usage, dict) else {}
        prompt_tokens = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
        completion_tokens = int(
            usage.get("completion_tokens") or usage.get("output_tokens") or 0
        )
        return {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": int(usage.get("total_tokens") or prompt_tokens + completion_tokens),
        }

    @staticmethod
    def _parse_response(content: str) -> dict[str, Any]:
        normalized = content.strip()
        if normalized.startswith("```"):
            normalized = normalized.split("\n", 1)[-1]
            if normalized.endswith("```"):
                normalized = normalized[:-3]
        parsed = json.loads(normalized)
        if not isinstance(parsed, dict):
            raise ValueError("judge response must be a JSON object")
        return parsed

    @classmethod
    def _normalize_result(
        cls,
        parsed: dict[str, Any],
        *,
        pair_order: str,
        expected_dimensions: tuple[str, ...],
    ) -> dict[str, Any]:
        raw_left = cls._dimension_scores(parsed.get("variant_left"))
        raw_right = cls._dimension_scores(parsed.get("variant_right"))
        missing_dimensions = [
            dimension
            for dimension in expected_dimensions
            if dimension not in raw_left or dimension not in raw_right
        ]
        if missing_dimensions:
            raise ValueError("judge response is missing required dimensions")
        left = {dimension: raw_left[dimension] for dimension in expected_dimensions}
        right = {dimension: raw_right[dimension] for dimension in expected_dimensions}
        baseline_dimensions, candidate_dimensions = (
            (left, right) if pair_order == "baseline_left" else (right, left)
        )
        dimensions = {
            name: {
                "baseline": baseline_dimensions.get(name),
                "candidate": candidate_dimensions.get(name),
                "delta": cls._delta(
                    baseline_dimensions.get(name), candidate_dimensions.get(name)
                ),
            }
            for name in sorted(set(baseline_dimensions) | set(candidate_dimensions))
        }
        baseline_score = cls._average(baseline_dimensions.values())
        candidate_score = cls._average(candidate_dimensions.values())
        confidence_score = cls._score(parsed.get("confidence"), lower=0, upper=1)
        if confidence_score is None:
            raise ValueError("judge response confidence is missing")
        return {
            "status": "completed",
            "baseline": {"score": baseline_score},
            "candidate": {"score": candidate_score},
            "delta": cls._delta(baseline_score, candidate_score),
            "dimensions": dimensions,
            "confidence": cls._confidence_label(confidence_score),
            "confidence_score": confidence_score,
            "safe_summary": cls._safe_summary(baseline_score, candidate_score),
        }

    @staticmethod
    def _dimension_scores(value: Any) -> dict[str, int]:
        if not isinstance(value, dict):
            raise ValueError("judge dimension scores are missing")
        scores: dict[str, int] = {}
        for key, item in value.items():
            score = CostOptimizerOutputQualityService._score(item, lower=0, upper=100)
            if score is not None:
                scores[str(key)] = int(round(score))
        if not scores:
            raise ValueError("judge dimension scores are empty")
        return scores

    @staticmethod
    def _score(value: Any, *, lower: float, upper: float) -> float | None:
        if isinstance(value, bool):
            return None
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(number) or number < lower or number > upper:
            return None
        return number

    @staticmethod
    def _average(values: Any) -> int | None:
        numbers = [float(value) for value in values if isinstance(value, (int, float))]
        return int(round(sum(numbers) / len(numbers))) if numbers else None

    @staticmethod
    def _delta(baseline: int | None, candidate: int | None) -> int | None:
        if baseline is None or candidate is None:
            return None
        return candidate - baseline

    @staticmethod
    def _confidence_label(value: float | None) -> str:
        if value is None:
            return "low"
        if value >= 0.8:
            return "high"
        if value >= 0.6:
            return "medium"
        return "low"

    @staticmethod
    def _safe_summary(baseline_score: int | None, candidate_score: int | None) -> str:
        if baseline_score is None or candidate_score is None:
            return "출력 품질 점수를 계산하지 못했습니다."
        if candidate_score > baseline_score:
            return "후보 출력의 품질 점수가 기준 출력보다 높게 평가되었습니다."
        if candidate_score < baseline_score:
            return "후보 출력의 품질 점수가 기준 출력보다 낮게 평가되었습니다."
        return "두 출력의 품질 점수가 동일하게 평가되었습니다."

    @staticmethod
    def _unavailable(summary: str) -> dict[str, Any]:
        return {
            "status": "unavailable",
            "baseline": {"score": None},
            "candidate": {"score": None},
            "delta": None,
            "dimensions": {},
            "confidence": "unavailable",
            "safe_summary": summary,
            "judge_cost": None,
            "judge_usage_log_id": None,
        }
