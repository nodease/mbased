import copy
import re
from dataclasses import dataclass
from typing import Any

from apps.shared.domain.app_auth_secret import APP_AUTH_SECRET_PREFIX
from apps.shared.services.tracing.policy import ResolvedRedactionPolicy

DEFAULT_REPLACEMENT = "[REDACTED]"
SECRET_KEYWORDS = {
    "api_key",
    "apikey",
    "authorization",
    "auth_secret",
    "bearer",
    "cloud_provider_credential",
    "connection_string",
    "cookie",
    "credential",
    "database_url",
    "encrypted_config",
    "oauth_token",
    "password",
    "private_key",
    "proxy-authorization",
    "purge_receipt",
    "secret",
    "session_token",
    "set-cookie",
    "ssh_private_key",
    "token",
    "x-api-key",
    "x-auth-token",
    "x-webhook-secret",
}
SAFE_TOKEN_COUNT_KEYS = {
    "cached_tokens",
    "completion_tokens",
    "context_token_estimate",
    "input_tokens",
    "max_completion_tokens",
    "max_output_tokens",
    "max_tokens",
    "output_tokens",
    "prompt_tokens",
    "reasoning_tokens",
    "token_count",
    "total_tokens",
}
DEFAULT_SENSITIVE_HEADERS = {
    "authorization",
    "cookie",
    "set-cookie",
    "x-api-key",
    "x-auth-token",
    "x-webhook-secret",
    "proxy-authorization",
}

PII_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("email", re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")),
    ("phone", re.compile(r"(?<!\w)\+?(?:\d{1,3}[-.\s]?)?(?:\d{2,4}[-.\s]?){2,4}\d{2,4}\b")),
    ("credit_card", re.compile(r"\b(?:\d[ -]*?){13,19}\b")),
    ("ip_address", re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")),
)
SECRET_VALUE_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("bearer_token", re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+")),
    ("api_key_value", re.compile(r"(?i)\b(?:api[_-]?key|token|secret)\s*[:=]\s*['\"]?[^'\"\s,}]+")),
    (
        "github_token",
        re.compile(r"\b(?:github_pat_|gh[oprsu]_)[A-Za-z0-9_]{20,}\b"),
    ),
    ("aws_access_key", re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")),
    ("google_api_key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("slack_token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{8,}\b")),
    (
        "app_auth_secret",
        re.compile(
            rf"(?<![A-Za-z0-9_-]){re.escape(APP_AUTH_SECRET_PREFIX)}[A-Za-z0-9_-]{{32,}}(?![A-Za-z0-9_-])"
        ),
    ),
    (
        "public_conversation_access_grant",
        re.compile(
            r"(?<![A-Za-z0-9_-])cag_v1_[A-Za-z0-9_-]{43,128}(?![A-Za-z0-9_-])"
        ),
    ),
    (
        "public_purge_receipt",
        re.compile(
            r"(?<![A-Za-z0-9_-])cpr_v1_[A-Za-z0-9_-]{43,128}(?![A-Za-z0-9_-])"
        ),
    ),
    (
        "private_key",
        re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S),
    ),
)


@dataclass(frozen=True)
class RedactionResult:
    redacted_payload: Any
    redaction_applied: bool
    pii_detected: bool
    secret_detected: bool
    redaction_metadata: dict[str, Any]
    failed: bool = False


class TraceRedactionService:
    """추적 페이로드가 비동기 경계를 넘기 전에 정책 기반 마스킹을 적용."""

    @staticmethod
    def redact_payload(
        payload: Any,
        policy: ResolvedRedactionPolicy,
        payload_kind: str = "input",
    ) -> RedactionResult:
        replacement = policy.replacement or DEFAULT_REPLACEMENT
        metadata: dict[str, Any] = {
            "fields": [],
            "rule_ids": [],
            "detector_types": [],
            "payload_kind": payload_kind,
        }

        try:
            redacted = copy.deepcopy(payload)
            redacted = TraceRedactionService._redact_value(
                redacted,
                path="payload",
                policy=policy,
                replacement=replacement,
                metadata=metadata,
            )
            fields = metadata["fields"]
            rule_ids = metadata["rule_ids"]
            detector_types = metadata["detector_types"]
            return RedactionResult(
                redacted_payload=redacted,
                redaction_applied=bool(fields),
                pii_detected=any(rule in {"email", "phone", "credit_card", "ip_address"} for rule in rule_ids),
                secret_detected=any(
                    detector == "secret" or rule in SECRET_KEYWORDS
                    for detector, rule in zip(detector_types, rule_ids)
                ),
                redaction_metadata={
                    "redaction": {
                        "applied": bool(fields),
                        "pii_detected": any(
                            rule in {"email", "phone", "credit_card", "ip_address"}
                            for rule in rule_ids
                        ),
                        "secret_detected": any(
                            detector == "secret" or rule in SECRET_KEYWORDS
                            for detector, rule in zip(detector_types, rule_ids)
                        ),
                        "policy_id": str(policy.id) if policy.id else None,
                        "fields": fields,
                        "rule_ids": rule_ids,
                        "detector_types": detector_types,
                    }
                },
            )
        except Exception:
            return RedactionResult(
                redacted_payload={"redaction_failed": True},
                redaction_applied=True,
                pii_detected=False,
                secret_detected=True,
                redaction_metadata={
                    "redaction": {
                        "applied": True,
                        "failed": True,
                        "policy_id": str(policy.id) if policy.id else None,
                        "fields": ["payload"],
                        "rule_ids": ["redaction_failure"],
                        "detector_types": ["safety_fallback"],
                    }
                },
                failed=True,
            )

    @staticmethod
    def _record(
        metadata: dict[str, Any],
        path: str,
        rule_id: str,
        detector_type: str,
    ) -> None:
        metadata["fields"].append(path)
        metadata["rule_ids"].append(rule_id)
        metadata["detector_types"].append(detector_type)

    @staticmethod
    def _is_sensitive_key(
        key: str, value: Any, policy: ResolvedRedactionPolicy
    ) -> bool:
        normalized = key.lower().replace("-", "_")
        raw_key = key.lower()
        policy_headers = {str(h).lower() for h in policy.sensitive_headers or ()}
        policy_keywords = {str(k).lower() for k in policy.sensitive_keywords or ()}
        if raw_key in DEFAULT_SENSITIVE_HEADERS or raw_key in policy_headers:
            return True
        if any(keyword in normalized for keyword in policy_keywords):
            return True
        # 토큰 제한과 사용량은 비교 가능한 운영 지표다. 숫자 또는 null인
        # 명시적 allowlist만 보존하고, 문자열이나 알 수 없는 *_token은 가린다.
        if normalized in SAFE_TOKEN_COUNT_KEYS and (
            value is None
            or (isinstance(value, (int, float)) and not isinstance(value, bool))
        ):
            return False
        return any(keyword in normalized for keyword in SECRET_KEYWORDS)

    @staticmethod
    def _path_matches(path: str, policy: ResolvedRedactionPolicy) -> bool:
        configured_paths = {str(p).lower() for p in policy.sensitive_json_paths or ()}
        return path.lower() in configured_paths

    @staticmethod
    def _redact_string(
        value: str,
        path: str,
        policy: ResolvedRedactionPolicy,
        replacement: str,
        metadata: dict[str, Any],
    ) -> str:
        redacted = value

        # 비밀값 패턴은 정책의 마스킹 사용 여부와 무관하게 항상 적용합니다.
        for rule_id, pattern in SECRET_VALUE_RULES:
            if pattern.search(redacted):
                redacted = pattern.sub(replacement, redacted)
                TraceRedactionService._record(metadata, path, rule_id, "secret")

        if policy.redaction_enabled and policy.pii_detection_enabled:
            for rule_id, pattern in PII_RULES:
                if pattern.search(redacted):
                    redacted = pattern.sub(replacement, redacted)
                    TraceRedactionService._record(metadata, path, rule_id, "regex")

        if policy.redaction_enabled:
            for rule in policy.regex_rules or ():
                try:
                    rule_id = str(rule.get("id") or rule.get("name") or "custom_regex")
                    pattern_text = str(rule.get("pattern") or "")
                    if not pattern_text:
                        continue
                    pattern = re.compile(pattern_text)
                    if pattern.search(redacted):
                        redacted = pattern.sub(replacement, redacted)
                        TraceRedactionService._record(metadata, path, rule_id, "regex")
                except Exception:
                    continue

        return redacted

    @staticmethod
    def _redact_value(
        value: Any,
        path: str,
        policy: ResolvedRedactionPolicy,
        replacement: str,
        metadata: dict[str, Any],
    ) -> Any:
        if isinstance(value, dict):
            redacted: dict[str, Any] = {}
            for key, child_value in value.items():
                child_path = f"{path}.{key}"
                # 비밀값 키는 정책의 마스킹 사용 여부와 무관하게 항상 마스킹합니다.
                if TraceRedactionService._path_matches(
                    child_path, policy
                ) or TraceRedactionService._is_sensitive_key(
                    str(key), child_value, policy
                ):
                    redacted[key] = replacement
                    TraceRedactionService._record(
                        metadata, child_path, str(key).lower(), "secret"
                    )
                    continue
                redacted[key] = TraceRedactionService._redact_value(
                    child_value, child_path, policy, replacement, metadata
                )
            return redacted

        if isinstance(value, list):
            return [
                TraceRedactionService._redact_value(
                    item, f"{path}[{index}]", policy, replacement, metadata
                )
                for index, item in enumerate(value)
            ]

        if isinstance(value, str):
            return TraceRedactionService._redact_string(
                value, path, policy, replacement, metadata
            )

        return value
