class AuditAction:
    """
    계층 A 사용자 행동 타입.

    값은 "{resource}.{verb}" 형태의 문자열로 DB에 그대로 저장한다.
    resource는 파일명이 아니라 감사 대상 비즈니스 리소스 기준으로 정한다.
    """

    # 인증: 로그인 전이라 데코레이터 대신 auth 엔드포인트에서 직접 기록한다.
    USER_SIGNUP = "user.signup"
    USER_SIGNUP_FAILED = "user.signup_failed"
    USER_LOGIN = "user.login"
    USER_LOGIN_FAILED = "user.login_failed"
    USER_LOGOUT = "user.logout"
    AUTH_PERMISSION_DENIED = "auth.permission_denied"

    PERMISSION_GRANT = "permission.grant"
    PERMISSION_REVOKE = "permission.revoke"
    PERMISSION_DENIED = "permission.denied"

    # 권한 신청 lifecycle과 App 생성 권한 row data-change (ADR-0016).
    PERMISSION_REQUEST_CREATED = "permission_request.created"
    PERMISSION_REQUEST_APPROVED = "permission_request.approved"
    PERMISSION_REQUEST_REJECTED = "permission_request.rejected"
    USER_APP_CREATION_PERMISSION_CREATED = "user_app_creation_permission.created"
    USER_APP_CREATION_PERMISSION_DELETED = "user_app_creation_permission.deleted"

    # 앱/워크플로우/배포: 사용자가 워크플로우 운영 단위에서 수행한 행동.
    ORGANIZATION_UPDATE = "organization.update"
    ORGANIZATION_INVITE = "organization.invite"
    ORGANIZATION_MEMBER_ACCEPT = "organization.member.accept"
    ORGANIZATION_MEMBER_DECLINE = "organization.member.decline"
    ORGANIZATION_MEMBER_UPDATE = "organization.member.update"
    ORGANIZATION_MEMBER_REMOVE = "organization.member.remove"

    APP_CREATE = "app.create"
    APP_UPDATE = "app.update"
    APP_CLONE = "app.clone"
    APP_DELETE = "app.delete"
    APP_AUTH_SECRET_ROTATED = "app.auth_secret.rotated"

    WORKFLOW_CREATE = "workflow.create"
    WORKFLOW_UPDATE = "workflow.update"
    WORKFLOW_DEPLOY = "workflow.deploy"
    WORKFLOW_EXECUTE = "workflow.execute"
    WORKFLOW_BUDGET_CREATED = "workflow_budget.created"
    WORKFLOW_BUDGET_UPDATED = "workflow_budget.updated"

    # Agent Builder draft/preview/apply-save lifecycle.
    AGENT_BUILDER_SESSION_CREATED = "agent_builder_session.created"
    AGENT_BUILDER_REQUEST_SUBMITTED = "agent_builder_request.submitted"
    AGENT_BUILDER_REQUEST_CANCELED = "agent_builder_request.canceled"
    AGENT_BUILDER_REQUEST_FAILED = "agent_builder_request.failed"
    AGENT_BUILDER_DRAFT_CREATED = "agent_builder_draft.created"
    AGENT_BUILDER_GRAPH_MUTATION_ISSUED = "agent_builder_graph_mutation.issued"
    AGENT_BUILDER_GRAPH_MUTATION_BLOCKED = "agent_builder_graph_mutation.blocked"
    AGENT_BUILDER_GRAPH_MUTATION_REVERTED = "agent_builder_graph_mutation.reverted"
    AGENT_BUILDER_PARAMETER_GROUP_CANCELED = "agent_builder_parameter_group.canceled"
    AGENT_BUILDER_KNOWLEDGE_MUTATION_ISSUED = "agent_builder_knowledge_mutation.issued"
    AGENT_BUILDER_PARAMETER_DECISION_RECORDED = "agent_builder_parameter_decision.recorded"
    AGENT_BUILDER_PREVIEW_OPENED = "agent_builder_preview.opened"
    AGENT_BUILDER_PREVIEW_BLOCKED = "agent_builder_preview.blocked"
    AGENT_BUILDER_APPLY_SAVE_REQUESTED = "agent_builder_apply_save.requested"
    AGENT_BUILDER_APPLY_SAVE_SUCCEEDED = "agent_builder_apply_save.succeeded"
    AGENT_BUILDER_APPLY_SAVE_BLOCKED = "agent_builder_apply_save.blocked"
    AGENT_BUILDER_APPLY_SAVE_FAILED = "agent_builder_apply_save.failed"
    AGENT_BUILDER_APPLY_SAVE_CANCELED = "agent_builder_apply_save.canceled"

    DEPLOYMENT_TOGGLE = "deployment.toggle"
    DEPLOYMENT_ACTIVATE_PREVIOUS = "deployment.activate_previous"
    DEPLOYMENT_DELETE = "deployment.delete"
    DEPLOYMENT_LLM_CREDENTIAL_POLICY_UPSERT = (
        "deployment.llm_credential_policy.upsert"
    )

    # 외부 연결 및 LLM 자격증명.
    CONNECTION_CREATE = "connection.create"
    CONNECTION_TEST = "connection.test"
    CONNECTION_DELETE = "connection.delete"

    CREDENTIAL_CREATE = "credential.create"
    CREDENTIAL_DELETE = "credential.delete"

    MAIL_CREDENTIAL_CREATE = "mail_credential.create"
    MAIL_CREDENTIAL_UPDATE = "mail_credential.update"
    MAIL_CREDENTIAL_REVOKE = "mail_credential.revoke"

    MODEL_PRICING_UPDATE = "model.pricing_update"
    LLM_CALL = "llm.call"

    # Conversation Memory lifecycle. Public requests use actor_type="public";
    # asynchronous physical purge completion uses actor_type="system".
    MEMORY_SESSION_CREATED = "memory.session.created"
    MEMORY_SESSION_CLOSED = "memory.session.closed"
    MEMORY_SESSION_RESET = "memory.session.reset"
    MEMORY_SESSION_DELETE_REQUESTED = "memory.session.delete_requested"
    MEMORY_SESSION_PURGED = "memory.session.purged"
    MEMORY_GRANT_ISSUED = "memory.grant.issued"
    MEMORY_GRANT_REVOKED = "memory.grant.revoked"

    # 지식베이스와 문서 수명주기.
    KNOWLEDGE_CREATE = "knowledge.create"
    KNOWLEDGE_UPDATE = "knowledge.update"
    KNOWLEDGE_DELETE = "knowledge.delete"

    DOCUMENT_UPLOAD = "document.upload"
    DOCUMENT_PROCESS = "document.process"
    DOCUMENT_DELETE = "document.delete"
    RAG_RETRIEVE = "rag.retrieve"
    RAG_ANSWER_REQUESTED = "rag.answer.requested"
    RAG_ANSWER_COMPLETED = "rag.answer.completed"
    RAG_ANSWER_FAILED = "rag.answer.failed"
    RAG_ANSWER_CANCELLED = "rag.answer.cancelled"
    RAG_ANSWER_PURGE = "rag.answer.purge"

    POLICY_WARN = "policy.warn"
    POLICY_BLOCK = "policy.block"
