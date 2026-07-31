from __future__ import annotations

ACCESS_MANAGEMENT_POLICY_REASONS = frozenset(
    {
        "access_management.self_control_forbidden",
        "access_management.last_active_manager",
        "access_management.manager_override_active",
        "access_management.member_state_not_manageable",
        "access_management.target_user_inactive",
        "access_management.stale_state",
    }
)

RAG_PII_POLICY_REASON = "rag.pii_evidence_detected"
LEGACY_RAG_PII_POLICY_REASON = "pii_policy_blocked"
BUDGET_EXCEEDED_POLICY_REASON = "budget.exceeded"

SECURITY_ALERT_POLICY_REASONS = ACCESS_MANAGEMENT_POLICY_REASONS | frozenset(
    {RAG_PII_POLICY_REASON}
)
