from apps.shared.audit.actions import AuditAction


def test_mba_44_canonical_audit_actions_are_defined():
    assert AuditAction.WORKFLOW_EXECUTE == "workflow.execute"
    assert AuditAction.PERMISSION_DENIED == "permission.denied"
    assert AuditAction.LLM_CALL == "llm.call"
    assert AuditAction.RAG_RETRIEVE == "rag.retrieve"
    assert AuditAction.RAG_ANSWER_REQUESTED == "rag.answer.requested"
    assert AuditAction.RAG_ANSWER_COMPLETED == "rag.answer.completed"
    assert AuditAction.RAG_ANSWER_FAILED == "rag.answer.failed"
    assert AuditAction.RAG_ANSWER_CANCELLED == "rag.answer.cancelled"
    assert AuditAction.RAG_ANSWER_PURGE == "rag.answer.purge"
    assert AuditAction.POLICY_WARN == "policy.warn"
    assert AuditAction.POLICY_BLOCK == "policy.block"
    assert AuditAction.DEPLOYMENT_ACTIVATE_PREVIOUS == "deployment.activate_previous"
    assert AuditAction.CONNECTION_DELETE == "connection.delete"


def test_organization_membership_audit_actions_are_defined():
    assert AuditAction.ORGANIZATION_INVITE == "organization.invite"
    assert AuditAction.ORGANIZATION_MEMBER_ACCEPT == "organization.member.accept"
    assert AuditAction.ORGANIZATION_MEMBER_DECLINE == "organization.member.decline"
    assert AuditAction.ORGANIZATION_MEMBER_UPDATE == "organization.member.update"
    assert AuditAction.ORGANIZATION_MEMBER_REMOVE == "organization.member.remove"


def test_connector_test_audit_action_is_defined():
    assert AuditAction.CONNECTION_TEST == "connection.test"
