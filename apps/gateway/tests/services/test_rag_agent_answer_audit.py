import uuid
from types import SimpleNamespace

from apps.gateway.services import rag_agent_answer_audit
from apps.gateway.services.rag_agent_answer_audit import RAGAgentAnswerAuditRecorder


def test_pii_policy_block_records_top_level_canonical_policy_reason(monkeypatch):
    user_id = uuid.uuid4()
    organization_id = uuid.uuid4()
    run = SimpleNamespace(
        id=uuid.uuid4(),
        correlation_id="correlation-safe",
    )
    policy_result = {
        "result": "block",
        "reason_code": "pii_policy_blocked",
    }
    events = []
    monkeypatch.setattr(
        rag_agent_answer_audit,
        "record_audit",
        lambda **event: events.append(event),
    )

    RAGAgentAnswerAuditRecorder(
        SimpleNamespace(),
        user_id=user_id,
        organization_id=organization_id,
    ).record_policy_block(run, policy_result)

    assert len(events) == 1
    event = events[0]
    assert event["action"] == "policy.block"
    assert event["actor_id"] == user_id
    assert event["actor_type"] == "user"
    assert event["category"] == "action"
    assert event["status"] == "failure"
    assert event["target_type"] == "rag_answer_run"
    assert event["target_id"] == run.id
    assert event["metadata"]["organization_id"] == str(organization_id)
    assert event["metadata"]["policy_reason"] == "rag.pii_evidence_detected"
    assert event["metadata"]["policy_result"] == policy_result
