"""계약을 통과한 Judge 선택을 안전하게 재사용하는 작은 cache helper."""

from __future__ import annotations

import hashlib
import hmac
import os
from typing import Any


MAX_ACCEPTED_DECISION_CACHE_ENTRIES = 128


def routing_feature_hash(feature_text: str) -> str | None:
    """원문을 저장하지 않고 동일한 routing feature를 식별한다."""

    secret = (
        os.getenv("MODEL_ROUTING_FEATURE_HASH_KEY")
        or os.getenv("ENCRYPTION_KEY")
        or ""
    ).strip()
    if not secret or not feature_text:
        return None
    return hmac.new(
        secret.encode("utf-8"),
        str(feature_text).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def accepted_decision(
    learning: dict[str, Any],
    *,
    feature_text: str | None,
    available_model_ids: list[str],
) -> dict[str, Any] | None:
    feature_hash = routing_feature_hash(feature_text or "")
    if not feature_hash:
        return None
    cache = learning.get("accepted_decision_cache")
    if not isinstance(cache, dict):
        return None
    decision = cache.get(feature_hash)
    if not isinstance(decision, dict):
        return None
    selected_model_id = str(decision.get("selected_model_id") or "").strip()
    if not selected_model_id or selected_model_id not in set(available_model_ids):
        return None
    return dict(decision)


def remember_accepted_decision(
    learning: dict[str, Any],
    *,
    feature_hash: str | None,
    selected_model_id: str,
    confidence: float | None,
    reason_code: str | None,
) -> None:
    if not feature_hash:
        return
    existing = learning.get("accepted_decision_cache")
    cache = dict(existing) if isinstance(existing, dict) else {}
    cache.pop(feature_hash, None)
    cache[feature_hash] = {
        "selected_model_id": str(selected_model_id),
        "confidence": round(float(confidence or 0), 4),
        "reason_code": str(reason_code or "judge_selected")[:80],
    }
    while len(cache) > MAX_ACCEPTED_DECISION_CACHE_ENTRIES:
        cache.pop(next(iter(cache)))
    learning["accepted_decision_cache"] = cache
