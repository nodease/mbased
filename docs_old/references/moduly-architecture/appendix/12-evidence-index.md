# 근거 인덱스

> Status: Reference-only historical document.
>
> 이 문서는 과거 `main` 브랜치 기준 역공학 산출물이다. 현재 구현 기준은 active `docs/` 문서와 코드를 따른다.

이 문서는 주요 문서화 항목별로 확인한 근거 파일을 연결한다.

## 제품/기술 스택

- `README.md`
- `apps/client/package.json`
- `apps/gateway/pyproject.toml`
- `apps/workflow_engine/pyproject.toml`
- `apps/sandbox/pyproject.toml`
- `apps/log_system/pyproject.toml`
- `apps/shared/pyproject.toml`

## Frontend

- `apps/client/app/page.tsx`
- `apps/client/app/layout.tsx`
- `apps/client/app/dashboard/layout.tsx`
- `apps/client/app/auth/login/page.tsx`
- `apps/client/app/auth/signup/page.tsx`
- `apps/client/app/dashboard/**/page.tsx`
- `apps/client/app/modules/[id]/page.tsx`
- `apps/client/app/shared/[urlSlug]/page.tsx`
- `apps/client/app/embed/chat/[urlSlug]/page.tsx`
- `apps/client/app/stream-api/workflows/[workflowId]/route.ts`
- `apps/client/lib/apiClient.ts`
- `apps/client/next.config.ts`

## Client API wrappers

- `apps/client/app/features/auth/api/authApi.ts`
- `apps/client/app/features/app/api/appApi.ts`
- `apps/client/app/features/workflow/api/workflowApi.ts`
- `apps/client/app/features/workflow/api/webhookApi.ts`
- `apps/client/app/features/knowledge/api/knowledgeApi.ts`
- `apps/client/app/features/knowledge/api/connectorApi.ts`

## Gateway API

- `apps/gateway/main.py`
- `apps/gateway/api/api.py`
- `apps/gateway/api/v1/endpoints/auth.py`
- `apps/gateway/api/v1/endpoints/app.py`
- `apps/gateway/api/v1/endpoints/workflow.py`
- `apps/gateway/api/v1/endpoints/deployment.py`
- `apps/gateway/api/v1/endpoints/run.py`
- `apps/gateway/api/v1/endpoints/webhook.py`
- `apps/gateway/api/v1/endpoints/llm.py`
- `apps/gateway/api/v1/endpoints/knowledge.py`
- `apps/gateway/api/v1/endpoints/rag.py`
- `apps/gateway/api/v1/endpoints/connectors.py`
- `apps/gateway/api/v1/endpoints/prompt_wizard.py`
- `apps/gateway/api/v1/endpoints/code_wizard.py`
- `apps/gateway/api/v1/endpoints/template_wizard.py`
- `apps/gateway/api/v1/endpoints/health.py`

## Services

- `apps/gateway/services/auth_service.py`
- `apps/gateway/services/app_service.py`
- `apps/gateway/services/workflow_service.py`
- `apps/gateway/services/deployment_service.py`
- `apps/gateway/services/llm_service.py`
- `apps/gateway/services/scheduler_service.py`
- `apps/gateway/services/storage.py`
- `apps/gateway/services/ingestion/service.py`
- `apps/gateway/services/ingestion/factory.py`
- `apps/gateway/services/ingestion/processors/file_processor.py`
- `apps/gateway/services/ingestion/processors/api_processor.py`

## Auth/Security

- `apps/gateway/auth/dependencies.py`
- `apps/gateway/auth/oauth.py`
- `apps/gateway/core/security.py`
- `apps/gateway/utils/encryption.py`
- `apps/shared/utils/encryption.py`
- `apps/workflow_engine/utils/encryption.py`

## DB models

- `apps/shared/db/models/user.py`
- `apps/shared/db/models/app.py`
- `apps/shared/db/models/workflow.py`
- `apps/shared/db/models/workflow_deployment.py`
- `apps/shared/db/models/workflow_run.py`
- `apps/shared/db/models/knowledge.py`
- `apps/shared/db/models/connection.py`
- `apps/shared/db/models/llm.py`
- `apps/shared/db/models/schedule.py`

## Schemas

- `apps/shared/schemas/auth.py`
- `apps/shared/schemas/app.py`
- `apps/shared/schemas/workflow.py`
- `apps/shared/schemas/deployment.py`
- `apps/shared/schemas/log.py`
- `apps/shared/schemas/llm.py`
- `apps/shared/schemas/rag.py`
- `apps/shared/schemas/connector.py`
- `apps/shared/schemas/connector_detail.py`

## Workflow Engine

- `apps/workflow_engine/tasks.py`
- `apps/workflow_engine/workflow/core/workflow_engine.py`
- `apps/workflow_engine/workflow/core/workflow_node_factory.py`
- `apps/workflow_engine/workflow/core/workflow_logger.py`
- `apps/workflow_engine/workflow/nodes/**`

## Sandbox

- `apps/sandbox/main.py`
- `apps/sandbox/config.py`
- `apps/sandbox/api/v1/endpoints/execute.py`
- `apps/sandbox/core/scheduler.py`
- `apps/sandbox/core/executor.py`
- `apps/sandbox/nsjail/wrapper.py`

## Async/Logs

- `apps/shared/celery_app.py`
- `apps/shared/pubsub.py`
- `apps/log_system/tasks.py`
- `apps/log_system/main.py`

## Infra

- `dev/docker-compose.yml`
- `docker/docker-compose.yml`
- `docker/nginx/nginx.conf`
- `docker/proxy/squid.conf`
- `docker/*/Dockerfile`
- `infra/helm/moduly/values.yaml`
- `infra/helm/moduly/values-production.yaml`
- `infra/helm/moduly/templates/*.yaml`
- `infra/k8s/**`
- `infra/terraform/*.tf`

## Scripts/Tests

- `scripts/setup.sh`
- `scripts/dev.sh`
- `scripts/test.sh`
- `apps/gateway/tests/**`
- `apps/workflow_engine/tests/**`
- `apps/sandbox/tests/**`
- `apps/shared/tests/**`
- `tests/evaluation/**`
- `tests/load/**`

## 정적 추출 결과

API route table은 FastAPI endpoint 파일의 `router.get/post/patch/delete` 데코레이터를 정적으로 추출한 뒤 `api/api.py`의 prefix를 적용했다. Sandbox route table도 동일하게 정적 추출했다.
