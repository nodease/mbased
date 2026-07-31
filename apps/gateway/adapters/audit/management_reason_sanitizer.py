from apps.shared.services.tracing.policy import TracePolicyService
from apps.shared.services.tracing.redaction import TraceRedactionService


class ManagementReasonSanitizer:
    def sanitize(self, reason: str | None) -> str | None:
        if reason is None:
            return None
        result = TraceRedactionService.redact_payload(
            reason,
            TracePolicyService.fail_closed_redaction_policy(),
            payload_kind="management_reason",
        )
        if result.failed or not isinstance(result.redacted_payload, str):
            raise ValueError("Management reason redaction failed")
        return result.redacted_payload
