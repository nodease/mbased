# API 명세서

Status: Draft
Authority: API
Source of Truth: Yes
Verified Against: dev @ ec576b4f24155697aed8843acc6e5a3fc835f7e1

API 문서는 HTTP 계약의 source of truth다. 모든 endpoint는 기본적으로 `/api/v1` prefix 아래에 있다.

## 문서

| 문서 | 범위 |
| --- | --- |
| [auth.md](auth.md) | 인증, session/cookie, active organization context |
| [organization-rbac.md](organization-rbac.md) | organization, team, permission 관리 API |
| [apps-workflows.md](apps-workflows.md) | app/workflow CRUD, draft, execute, stream, run detail |
| [llm-credentials.md](llm-credentials.md) | LLM provider, credential, model, pricing |
| [knowledge-rag.md](knowledge-rag.md) | knowledge base, document, RAG retrieval |
| [tracing-audit.md](tracing-audit.md) | trace 조회, LLM trace, audit search, raw payload access |
| [deployments.md](deployments.md) | deployment 생성, 활성화, public info, run/webhook |
| [supporting-endpoints.md](supporting-endpoints.md) | health, DB connector, prompt/code/template wizard helper API |
| [errors.md](errors.md) | 공통 error response와 reason code |

## 표기

| Status | 의미 |
| --- | --- |
| `Implemented` | 현재 코드의 Gateway route에 존재한다. |
| `Planned` | MVP 요구사항 또는 구현 계획에 있는 목표 계약이다. |
| `Proposed` | ADR 승인 또는 세부 설계 확정 전 후보 계약이다. |
| `Partial` | route 또는 권한 관문은 있으나 request/response 세부 계약이 완성되지 않았다. |

## 공통 규칙

- 대부분의 인증된 Gateway API는 현재 코드 기준 `auth_token` HTTP-only cookie를 사용한다. `Authorization: Bearer ...`는 일반 사용자 세션 인증이 아니라 `/api/v1/run/{url_slug}`와 `/api/v1/hooks/{url_slug}`에서 app public secret을 전달하는 방식 중 하나다.
- secret 원문은 API 응답, 로그, audit metadata에 노출하지 않는 것이 목표 보안 원칙이다. 현재 코드 기준 `AppResponse`와 `DeploymentResponse`에는 `auth_secret` 원문이 포함될 수 있으므로, masking/removal 전까지 current behavior로 문서화한다.
- 권한이 필요한 API는 [data-model/rbac-permission-policy.md](../data-model/rbac-permission-policy.md)의 permission matrix를 따른다.
- active organization은 [ADR-202606290145-active-organization-header-context](../decisions/ADR-202606290145-active-organization-header-context.md)에 따라 `X-Organization-Id` header로 전달한다.
- 오류 응답은 [errors.md](errors.md)를 따른다.
- Gateway는 요청마다 `X-Request-ID` 응답 header를 보장한다. 요청 header에 `X-Request-ID`가 없으면 서버가 UUID를 생성하고 audit metadata에 전파한다.

## 현재 Gateway 라우터

현재 `apps/gateway/api/api.py`가 등록하는 router prefix는 아래와 같다.

| Prefix | Router |
| --- | --- |
| `/api/v1/health` | `health.router` |
| `/api/v1/auth` | `auth.router` |
| `/api/v1/users` | `users.router` |
| `/api/v1/organizations` | `organization.router` |
| `/api/v1/teams` | `team.router` |
| `/api/v1/permissions` | `permissions.router` |
| `/api/v1/apps` | `app.router` |
| `/api/v1/workflows` | `workflow.router` |
| `/api/v1/llm` | `llm.router` |
| `/api/v1/knowledge` | `knowledge.router` |
| `/api/v1/rag` | `rag.router` |
| `/api/v1/connectors` | `connectors.router` |
| `/api/v1/deployments` | `deployment.router` |
| `/api/v1/prompt-wizard` | `prompt_wizard.router` |
| `/api/v1/code-wizard` | `code_wizard.router` |
| `/api/v1/template-wizard` | `template_wizard.router` |
| `/api/v1/run`, `/api/v1/run-public` | `run.router` |
| `/api/v1/hooks` | `webhook.router` |
| `/api/v1/traces`, `/api/v1/tracing/*` | `tracing.router` |
