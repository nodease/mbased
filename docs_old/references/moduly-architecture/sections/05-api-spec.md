# API 명세

> Status: Reference-only historical document.
>
> 이 문서는 과거 `main` 브랜치 기준 역공학 산출물이다. 현재 구현 기준은 active `docs/` 문서와 코드를 따른다.

기준:

- Gateway base path: `/api/v1`
- Sandbox base path: `/v1/sandbox`
- Gateway root: `/` returns `{"status":"ok","service":"gateway"}`
- Sandbox root: `/` returns service metadata/status
- Sandbox root health: `/health` returns health status

## 인증 방식

대부분의 Gateway API는 `auth_token` HttpOnly 쿠키를 통해 현재 사용자를 식별한다. 의존성 함수는 `apps/gateway/auth/dependencies.py`의 `get_current_user`다.

## Gateway API

### Health

| Method | Path | 함수 | 설명 |
| --- | --- | --- | --- |
| GET | `/api/v1/health` | `health_check` | Gateway health check |

### Auth

| Method | Path | 함수 | 응답 |
| --- | --- | --- | --- |
| POST | `/api/v1/auth/signup` | `signup` | `LoginResponse` |
| POST | `/api/v1/auth/login` | `login` | `LoginResponse` |
| POST | `/api/v1/auth/logout` | `logout` | message |
| GET | `/api/v1/auth/me` | `get_current_user` | `LoginResponse` |
| GET | `/api/v1/auth/google/login` | `google_login` | redirect |
| GET | `/api/v1/auth/google/callback` | `auth_google_callback` | redirect 또는 400 |

요청:

- `LoginRequest`: `email`, `password`
- `SignupRequest`: `email`, `password`, `name`

쿠키:

- key: `auth_token`
- max age: 6시간
- local: `secure=false`, `samesite=lax`
- production 추정: `secure=true`, `samesite=none`

### Apps

| Method | Path | 함수 | 응답 |
| --- | --- | --- | --- |
| PATCH | `/api/v1/apps/{app_id}` | `update_app` | `AppResponse` |
| POST | `/api/v1/apps` | `create_app` | `AppResponse` |
| GET | `/api/v1/apps/explore` | `list_explore_apps` | `List[AppResponse]` |
| GET | `/api/v1/apps` | `list_apps` | `List[AppResponse]` |
| GET | `/api/v1/apps/{app_id}` | `get_app` | `AppResponse` |
| POST | `/api/v1/apps/{app_id}/clone` | `clone_app` | `AppResponse` |
| DELETE | `/api/v1/apps/{app_id}` | `delete_app` | message |

주요 규칙:

- 앱 생성 시 URL slug와 auth secret 생성
- 같은 tenant 내 앱 이름 중복 금지
- 앱 생성 시 기본 Workflow 생성 후 `app.workflow_id` 연결
- 앱 소유자만 상세/수정/삭제 가능
- 복제는 활성 배포 snapshot 기반이며 민감 정보 일부 제거
- 복제 앱은 마켓 공개 불가

### Workflows

| Method | Path | 함수 | 응답 |
| --- | --- | --- | --- |
| GET | `/api/v1/workflows/{workflow_id}/runs` | `get_workflow_runs` | `WorkflowRunListResponse` |
| GET | `/api/v1/workflows/{workflow_id}/runs/{run_id}` | `get_workflow_run_detail` | `WorkflowRunSchema` |
| GET | `/api/v1/workflows/{workflow_id}/stats` | `get_workflow_stats` | `DashboardStatsResponse` |
| POST | `/api/v1/workflows` | `create_workflow` | `WorkflowResponse` |
| GET | `/api/v1/workflows/{workflow_id}` | `get_workflow` | `WorkflowResponse` |
| GET | `/api/v1/workflows/app/{app_id}` | `list_workflows_by_app` | `List[WorkflowResponse]` |
| POST | `/api/v1/workflows/{workflow_id}/draft` | `sync_draft_workflow` | status |
| GET | `/api/v1/workflows/{workflow_id}/draft` | `get_draft_workflow` | graph data |
| POST | `/api/v1/workflows/{workflow_id}/execute` | `execute_workflow` | result |
| POST | `/api/v1/workflows/{workflow_id}/stream` | `stream_workflow` | SSE |

주요 규칙:

- `draft` 저장 시 nodes, edges, viewport, features, env variables, runtime variables를 JSONB에 저장
- streaming은 Redis Pub/Sub 이벤트를 SSE로 반환
- 실행 전 graph validation은 Workflow Engine에서 수행

### Deployments

| Method | Path | 함수 | 응답 |
| --- | --- | --- | --- |
| POST | `/api/v1/deployments` | `create_deployment` | `DeploymentResponse` |
| GET | `/api/v1/deployments` | `get_deployments` | `List[DeploymentResponse]` |
| GET | `/api/v1/deployments/nodes` | `list_workflow_nodes` | `List[dict]` |
| GET | `/api/v1/deployments/{deployment_id}` | `get_deployment` | `DeploymentResponse` |
| GET | `/api/v1/deployments/public/{url_slug}/info` | `get_deployment_info_public` | deployment info |
| PATCH | `/api/v1/deployments/{deployment_id}/toggle` | `toggle_deployment` | `DeploymentResponse` |
| DELETE | `/api/v1/deployments/{deployment_id}` | `delete_deployment` | message |

주요 규칙:

- 생성 시 app 소유자 권한 확인
- snapshot이 없으면 현재 draft를 사용
- app 기준 version 자동 증가
- Start/Webhook/Answer 노드에서 input/output schema 추출
- 활성 배포 생성 시 같은 app의 기존 활성 배포 비활성화
- ScheduleTrigger가 있으면 `schedules` 생성 및 APScheduler 등록

### Public Run

| Method | Path | 함수 | 인증 |
| --- | --- | --- | --- |
| POST | `/api/v1/run/{url_slug}` | `run_workflow` | App `auth_secret` 필요 |
| POST | `/api/v1/run-public/{url_slug}` | `run_workflow_public` | 인증 생략 |

`/run/{url_slug}`는 API 실행용이고, `/run-public/{url_slug}`는 공개 WebApp/Widget 실행용이다.
`/run/{url_slug}`의 secret은 `Authorization: Bearer <secret>` 또는 `X-Auth-Secret`에서 추출해 App `auth_secret`과 비교한다.

주의: endpoint는 REST API 실행에 `trigger_mode="api"`를 전달하지만, 현재 `DeploymentService.run_deployment()`가 Celery에 넘기는 `execution_context.trigger_mode`는 `"app"`으로 고정되어 있다.

### Webhook

| Method | Path | 함수 | 설명 |
| --- | --- | --- | --- |
| POST | `/api/v1/hooks/{url_slug}` | `receive_webhook` | 외부 Webhook payload 수신/실행 |
| GET | `/api/v1/hooks/{url_slug}/capture/start` | `start_capture` | 테스트 payload capture 시작 |
| GET | `/api/v1/hooks/{url_slug}/capture/status` | `get_capture_status` | capture 상태 조회 |

Webhook 인증은 `?token=...`, `Authorization: Bearer ...`, `X-Webhook-Secret`을 지원한다. capture payload는 프로세스 메모리의 `CAPTURE_SESSIONS`에 저장되고, captured 상태 조회 시 삭제된다.

### LLM

| Method | Path | 함수 | 응답 |
| --- | --- | --- | --- |
| GET | `/api/v1/llm/providers` | `get_system_providers` | `List[LLMProviderResponse]` |
| GET | `/api/v1/llm/my-models` | `get_my_models` | `List[LLMModelResponse]` |
| GET | `/api/v1/llm/my-embedding-models` | `get_my_embedding_models` | `List[LLMModelResponse]` |
| GET | `/api/v1/llm/credentials` | `get_my_credentials` | `List[LLMCredentialResponse]` |
| POST | `/api/v1/llm/credentials` | `register_credential` | `LLMCredentialResponse` |
| DELETE | `/api/v1/llm/credentials/{credential_id}` | `delete_credential` | message |
| POST | `/api/v1/llm/credentials/{credential_id}/sync-models` | `sync_credential_models` | sync result |
| GET | `/api/v1/llm/stats/top-models` | `get_top_expensive_models` | stats |
| POST | `/api/v1/llm/models/sync-pricing` | `sync_system_pricing` | sync result |
| PUT | `/api/v1/llm/models/{model_id}/pricing` | `update_model_pricing` | update result |

주요 provider seed:

- openai
- anthropic
- google
- llamaparse

### Wizard APIs

| 영역 | Method | Path | 설명 |
| --- | --- | --- | --- |
| Prompt | GET | `/api/v1/prompt-wizard/check-credentials` | 위저드용 LLM credential 확인 |
| Prompt | POST | `/api/v1/prompt-wizard/improve` | 프롬프트 개선 |
| Code | GET | `/api/v1/code-wizard/check-credentials` | 위저드용 LLM credential 확인 |
| Code | POST | `/api/v1/code-wizard/generate` | Python 코드 생성 |
| Template | GET | `/api/v1/template-wizard/check-credentials` | 위저드용 LLM credential 확인 |
| Template | POST | `/api/v1/template-wizard/improve` | 템플릿 개선 |

### Knowledge

| Method | Path | 함수 | 응답 |
| --- | --- | --- | --- |
| POST | `/api/v1/knowledge` | `create_knowledge_base` | `KnowledgeBaseResponse`, 201 |
| GET | `/api/v1/knowledge` | `list_knowledge_bases` | `List[KnowledgeBaseResponse]` |
| GET | `/api/v1/knowledge/{kb_id}` | `get_knowledge_base` | `KnowledgeBaseDetailResponse` |
| PATCH | `/api/v1/knowledge/{kb_id}` | `update_knowledge_base` | 204 |
| DELETE | `/api/v1/knowledge/{kb_id}` | `delete_knowledge_base` | 204 |
| GET | `/api/v1/knowledge/{kb_id}/documents/{document_id}` | `get_document` | `DocumentResponse` |
| GET | `/api/v1/knowledge/{kb_id}/documents/{document_id}/content` | `get_document_content` | file/content response |
| POST | `/api/v1/knowledge/{kb_id}/documents/{document_id}/process` | `process_document` | 202 |
| POST | `/api/v1/knowledge/{kb_id}/documents/{document_id}/preview` | `preview_document_chunking` | `DocumentPreviewResponse` |
| POST | `/api/v1/knowledge/{kb_id}/documents/{document_id}/sync` | `sync_document` | 202 |

주의:

- Client의 `knowledgeApi.updateKnowledgeBase()` 타입은 `KnowledgeBaseResponse`를 기대하지만, API 데코레이터는 204 No Content로 정의되어 있다.

### RAG

| Method | Path | 함수 | 응답 |
| --- | --- | --- | --- |
| POST | `/api/v1/rag/upload/presigned-url` | `generate_presigned_url` | upload URL 또는 backend proxy 지시 |
| POST | `/api/v1/rag/upload` | `upload_document` | `IngestionResponse` |
| POST | `/api/v1/rag/document/{document_id}/analyze` | `analyze_document` | `DocumentAnalyzeResponse` |
| POST | `/api/v1/rag/document/{document_id}/confirm` | `confirm_document_parsing` | processing result |
| DELETE | `/api/v1/rag/document/{document_id}` | `delete_document` | message |
| POST | `/api/v1/rag/search-test/chat` | `search_test_chat` | `RAGResponse` |
| POST | `/api/v1/rag/search-test/pure` | `search_test_pure` | `List[ChunkPreview]` |
| GET | `/api/v1/rag/document/{document_id}/progress` | `get_document_progress` | SSE/progress |
| POST | `/api/v1/rag/proxy/preview` | `proxy_api_preview` | preview data |

### Connectors

| Method | Path | 함수 | 응답 |
| --- | --- | --- | --- |
| POST | `/api/v1/connectors/test` | `test_db_connection` | `DBConnectionTestResponse` |
| POST | `/api/v1/connectors` | `create_connection` | 201 |
| GET | `/api/v1/connectors/{connection_id}` | `get_connection_details` | `DBConnectionDetailResponse` |
| GET | `/api/v1/connectors/{connection_id}/schema` | `get_connection_schema` | schema |

현재 구현에서 명확히 확인된 DB connector는 PostgreSQL이다.

## Sandbox API

Sandbox 앱에는 v1 라우터 외에 최상위 헬스 엔드포인트도 있다.

| Method | Path | 설명 |
| --- | --- | --- |
| GET | `/` | Sandbox service root |
| GET | `/health` | Sandbox root health check |

### Sandbox v1

| Method | Path | 함수 | 응답 |
| --- | --- | --- | --- |
| POST | `/v1/sandbox/execute` | `execute_code` | `ExecuteResponse` |
| GET | `/v1/sandbox/metrics` | `get_metrics` | `MetricsResponse` |
| GET | `/v1/sandbox/health` | `health_check` | status |

### ExecuteRequest

| 필드 | 설명 |
| --- | --- |
| `code` | `def main(inputs): ...` 형태의 Python 코드 |
| `inputs` | 코드에 전달되는 dict |
| `timeout` | 1-60초 |
| `priority` | `high`, `normal`, `low`, null |
| `trigger_type` | manual, schedule, webhook, batch 등 |
| `enable_network` | 네트워크 허용 여부 |
| `tenant_id` | 공정 스케줄링용 tenant/user id |

### ExecuteResponse

| 필드 | 설명 |
| --- | --- |
| `success` | 실행 성공 여부 |
| `result` | 사용자 코드 반환값 |
| `error` | 에러 메시지 |
| `error_type` | 에러 타입 |
| `execution_time_ms` | 실행 시간 |
| `memory_used_mb` | 메모리 사용량 |

## Next API Route

| Method | Path | 설명 |
| --- | --- | --- |
| POST | `/stream-api/workflows/{workflowId}` | Next.js가 Gateway SSE를 직접 프록시한다 |

이 라우트는 `/api/*` rewrite가 SSE를 버퍼링하는 문제를 피하기 위해 별도로 구현되어 있다.

## 주의/검증 필요

- API 명세는 정적 FastAPI 데코레이터 추출과 파일 확인 기준이다. 실제 OpenAPI schema 생성은 실행 환경 의존성이 필요하므로 별도 검증 대상이다.
- 일부 endpoint 응답은 Pydantic response_model이 없는 dict/stream response다. 세부 payload는 함수 본문 기준으로 더 세분화할 수 있다.
