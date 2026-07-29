from fastapi import APIRouter

from apps.gateway.api.v1.endpoints import (
    admin,
    agent_builder,
    app,
    auth,
    code_wizard,
    connectors,
    deployment,
    health,
    knowledge,
    llm,
    mail_credentials,
    notification,
    organization,
    permission_request,
    permissions,
    prompt_wizard,
    rag,
    run,
    template_wizard,
    team,
    tracing,
    users,
    webhook,
    workflow,
)

# 메인 API 라우터 생성
api_router = APIRouter()

# Health Check (독립 엔드포인트)
api_router.include_router(health.router, tags=["health"])

# 워크플로우 엔드포인트 등록
# /workflows 경로에 workflow.router의 모든 엔드포인트 추가
api_router.include_router(workflow.router, prefix="/workflows", tags=["workflows"])

# 앱 엔드포인트 등록
api_router.include_router(app.router, prefix="/apps", tags=["apps"])
api_router.include_router(
    agent_builder.router, prefix="/agent-builder", tags=["agent-builder"]
)

# 추가 엔드포인트가 있다면 여기에 계속 등록
# 예: api_router.include_router(user.router, prefix="/users", tags=["users"])
api_router.include_router(auth.router, prefix="/auth", tags=["auth"])
api_router.include_router(users.router, prefix="/users", tags=["users"])
api_router.include_router(
    organization.router, prefix="/organizations", tags=["organizations"]
)
api_router.include_router(team.router, prefix="/teams", tags=["teams"])
api_router.include_router(
    permissions.router, prefix="/permissions", tags=["permissions"]
)
api_router.include_router(
    permission_request.router,
    prefix="/permission-requests",
    tags=["permission-requests"],
)
api_router.include_router(admin.router, prefix="/admin", tags=["admin"])
api_router.include_router(
    notification.router, prefix="/notifications", tags=["notifications"]
)
api_router.include_router(llm.router, prefix="/llm", tags=["llm"])
api_router.include_router(
    mail_credentials.router, prefix="/mail", tags=["mail-credentials"]
)
api_router.include_router(
    prompt_wizard.router, prefix="/prompt-wizard", tags=["prompt-wizard"]
)
api_router.include_router(
    code_wizard.router, prefix="/code-wizard", tags=["code-wizard"]
)
api_router.include_router(
    template_wizard.router, prefix="/template-wizard", tags=["template-wizard"]
)

# Knowledge & RAG (Dev A)
api_router.include_router(knowledge.router, prefix="/knowledge", tags=["knowledge"])
api_router.include_router(rag.router, prefix="/rag", tags=["rag"])
api_router.include_router(connectors.router, prefix="/connectors", tags=["connectors"])

# Deployment & Run (Dev B)
api_router.include_router(
    deployment.router, prefix="/deployments", tags=["deployments"]
)
api_router.include_router(run.router, tags=["run"])

# Webhook (External Trigger)
api_router.include_router(webhook.router, tags=["webhook"])

# 추적 (워크플로우 단위 추적/스팬/페이로드 API)
api_router.include_router(tracing.router, tags=["tracing"])
