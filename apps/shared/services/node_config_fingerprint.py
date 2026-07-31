"""서비스 사이에서 동일하게 사용하는 LLM node runtime 설정 지문."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from apps.shared.services.model_routing_model_filter import (
    normalize_model_routing_model_id,
)

LLM_RUNTIME_RELEVANT_KEYS = (
    "model_id",
    "fallback_model_id",
    "parameters",
    "output_format",
    "system_prompt",
    "user_prompt",
    "assistant_prompt",
    "knowledgeBases",
    "knowledgeCollections",
    "topK",
    "scoreThreshold",
    "retrievedContextMaxChars",
    "dedupeRetrievedContext",
    "retrievedContextCompression",
    "includeSourceMetadata",
    "answerGroundingCheck",
    "ragFailurePolicy",
    "auto_model_routing",
    "model_routing_context",
)


def llm_node_config_fingerprint(node_data: dict[str, Any]) -> str:
    """원문을 반환하지 않고 실행 결과에 영향을 주는 설정만 해시한다."""
    payload = {
        key: node_data.get(key)
        for key in LLM_RUNTIME_RELEVANT_KEYS
        if key in node_data
    }
    routing_policy = node_data.get("model_routing_policy")
    refresh = routing_policy.get("refresh") if isinstance(routing_policy, dict) else None
    if isinstance(refresh, dict) and "refresh_every_runs" in refresh:
        payload["model_routing_refresh_every_runs"] = refresh.get(
            "refresh_every_runs"
        )
    if isinstance(routing_policy, dict):
        excluded_model_ids = routing_policy.get("excluded_model_ids")
        if isinstance(excluded_model_ids, list):
            payload["model_routing_excluded_model_ids"] = sorted(
                {
                    normalize_model_routing_model_id(model_id)
                    for model_id in excluded_model_ids
                    if normalize_model_routing_model_id(model_id)
                }
            )
    serialized = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()
