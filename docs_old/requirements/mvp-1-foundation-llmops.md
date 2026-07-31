# MVP 1: 기반 구축 및 LLMOps Observability

Status: Draft
Authority: Requirements
Source of Truth: Yes
Verified Against: feature/mba-78 @ HEAD (base dev caaa4cd)
Related ADRs: [ADR-202606290116-accept-rbac-auth-state-and-user-direct-permission](../decisions/ADR-202606290116-accept-rbac-auth-state-and-user-direct-permission.md), [ADR-202606290131-audit-action-naming-standard](../decisions/ADR-202606290131-audit-action-naming-standard.md), [ADR-202606290145-active-organization-header-context](../decisions/ADR-202606290145-active-organization-header-context.md), [ADR-202606291451-team-router-rbac-service-boundary](../decisions/ADR-202606291451-team-router-rbac-service-boundary.md)

## 목표

MVP 1은 "가장 먼저 설계해야 하는 기반"과 "가장 빨리 보여줄 수 있는 LLMOps 가치"를 함께 만든다.

결과물:

```text
작동하는 워크플로우 빌더
  + RBAC/resource foundation
  + audit/tracing foundation
  + data governance/policy skeleton
  + 노드별 LLMOps observability
  + 브라우저 UI에서 끝까지 수행 가능한 MVP flow
```

MVP 1은 API-only 결과물이 아니다. 사용자는 브라우저 UI에서 organization/team/permission/credential/workflow/observability/compare 흐름을 직접 수행할 수 있어야 한다.

작동하는 MVP 기준:

| 기준 | 내용 |
| --- | --- |
| UI 포함 | backend API와 seed/script만으로 동작하는 상태는 완료로 보지 않는다. |
| 권한 설정 | organization owner/manager가 UI에서 team/member/resource permission을 설정할 수 있어야 한다. |
| 권한 반영 | `viewer`, `operator`, `builder`, `manager` 권한 차이가 UI 버튼/상태와 서버 응답에 모두 반영되어야 한다. |
| 실행 흐름 | LLM node가 포함된 workflow를 UI에서 만들고 저장하고 실행할 수 있어야 한다. |
| 관측성 | 실행 후 UI에서 node별 model/token/cost/latency/status를 확인할 수 있어야 한다. |
| 비교 | UI에서 model 또는 prompt 2개를 같은 입력으로 비교할 수 있어야 한다. |
| 감사 확인 | UI에서 권한 차단과 workflow 실행 이벤트가 기록된 것을 확인할 수 있어야 한다. |

MVP 1 최소 UI:

| UI 영역 | 필요한 기능 |
| --- | --- |
| Organization context | 현재 organization 표시. 다중 organization switcher 완성은 필수 아님 |
| Team/member management | team 생성, member 추가/제거, team 비활성화. 현재 Gateway는 `DELETE /api/v1/teams/{team_id}`를 제공하며 같은 organization scope 안 inactive team 재시도는 idempotent success로 처리한다. |
| Permission management | workflow/LLM credential의 team permission 부여/회수 |
| User direct permission | 특정 user에게 workflow/LLM credential 추가 권한 부여/회수 |
| Credential management | organization-level credential 등록, model sync, credential preview |
| Workflow editor | 권한별 read-only/save/execute 상태 표시 |
| Execution UI | manual execute, stream/result 확인 |
| LLMOps detail | run detail 또는 side panel에서 node별 LLM trace 표시 |
| Canvas observability | LLM node badge 또는 node panel에 cost/token/latency/status 표시 |
| Compare UI | model/prompt A/B 실행 결과, 비용, latency 비교 |
| Audit/activity UI | 권한 차단/실행 이벤트 확인 |

현재 frontend 권한 UI는 완전한 fail-closed 상태가 아니다. `GET /workflows/{workflow_id}/permissions/me` 로드가 실패하거나 아직 `workflowAccess`가 `null`인 동안 일부 editor/test/autosync UI는 `!== false` 조건으로 허용처럼 보일 수 있고, 최종 차단은 backend permission enforcement에 의존한다. MVP 1 완료 기준은 이 fallback을 명확한 loading/error/read-only 상태로 정렬하는 것이다.

현재 Gateway team router는 `DELETE /api/v1/teams/{team_id}`를 노출한다. 요청에는 `X-Organization-Id`와 organization `manager` 권한이 필요하며, active team은 inactive로 전환하고 이미 inactive인 scoped team은 `{"status": "deactivated"}`를 idempotent success로 반환한다. 따라서 MVP 1의 남은 작업은 `/dashboard/settings` UI가 이 계약과 오류 상태를 일관되게 처리하는지 검증하는 것이다.

현재 코드 기준 주요 프론트 라우트:

| 흐름 | 실제 라우트 |
| --- | --- |
| Dashboard home | `/dashboard` |
| My modules | `/dashboard/mymodule` |
| Explore/marketplace | `/dashboard/explore` |
| Statistics | `/dashboard/statistics` |
| Workflow editor | `/modules/{id}` |
| Workflow report/log/monitoring | `/modules/{id}/report` |
| LLM credential provider settings | `/dashboard/settings` (`/dashboard/settings/provider`는 redirect) |
| Organization/team/permission settings 기반 | `/dashboard/settings` |
| Knowledge base list/detail/document | `/dashboard/knowledge`, `/dashboard/knowledge/{id}`, `/dashboard/knowledge/{id}/document/{documentId}` |

## 기존 Moduly 상태와 재사용

현재 Moduly는 주로 owner-based 권한을 가진다.

| 리소스 | 기존 권한 방식 |
| --- | --- |
| App | 생성자/소유자 중심 |
| Workflow | app 소유자 중심 |
| Deployment | app 소유자 중심 |
| Knowledge Base | 현재 사용자 소유 범위 |
| LLM Credential | 현재 사용자 소유 범위 |
| Public Run/Webhook | app `auth_secret` 기반 |

MVP 1에서는 기존 기반을 아래처럼 재사용한다.

| 기존 기반 | 재사용 방식 |
| --- | --- |
| Auth/User | team membership과 user direct grant의 subject |
| App/Workflow | project/resource boundary |
| Workflow Engine | 기존 실행 경로 유지 |
| SSE stream | 실행 중 상태 표시 유지 |
| `workflow_runs` | run 단위 관측성 |
| `workflow_node_runs` | node 단위 관측성 |
| `llm_usage_logs` | LLM token/cost/latency 원천 |
| LLM Model/Pricing | 비용 계산 |
| Settings/Provider UI | organization-level credential 관리 UI의 기반 |
| `knowledge_bases`, `documents`, `document_chunks`, `connections` | MVP 2 governance의 기반으로만 설계 |

## Resource Boundary

MVP 1에서는 `Project` 모델을 새로 만들지 않고, 기존 `App`을 project boundary로 취급한다.

```text
Project boundary = App
  - Workflow
  - Deployment
  - Run
  - Audit Logs
```

이후 조직/테넌트 모델이 커지면 `Project`를 분리할 수 있다.

| 제품/기획 용어 | 현재 Moduly 모델 | MVP 1 처리 |
| --- | --- | --- |
| Project | `apps` | App을 project boundary로 사용 |
| Canvas | `workflows` | Workflow를 canvas resource로 사용 |
| Data Source | `knowledge_bases`, `documents`, `connections` | MVP 2에서 knowledge base `use` 권한과 document metadata policy를 적용하고, connection runtime `use`는 consuming resource 권한으로 허용 |
| Model | `llm_models` | 별도 model permission table 없이 credential `use` 권한과 `llm_rel_credential_models` 검증으로 사용 가능 모델 제한 |

## Resource Type

초기 resource type은 아래로 제한한다.

| Resource Type | 기존 모델 | 권한 필요 이유 | MVP |
| --- | --- | --- | --- |
| `app` | `apps` | 프로젝트 경계 | MVP 1 |
| `workflow` | `workflows` | read/write/execute 분리 | MVP 1 |
| `deployment` | `workflow_deployments` | 기본 read/deploy/manage enforcement는 workflow 권한으로 처리. checklist/diff/trigger logging은 MVP 3에서 강화 | MVP 1 기본 enforcement, MVP 3 운영 강화 |
| `knowledge_base` | `knowledge_bases` | RAG 데이터 사용 권한 | MVP 2 |
| `document` | `documents` | 문서별 민감도/metadata policy. document별 permission table은 만들지 않음 | MVP 2 |
| `connection` | `connections` | secret/manage는 owner 또는 organization owner/manager, runtime `use`는 workflow/knowledge base 권한 | 독립 permission resource 아님 |
| `llm_model` | `llm_models` | credential-model relation으로 사용 가능 모델을 제한. model별 permission table은 만들지 않음 | MVP 1 |
| `llm_credential` | `llm_credentials` | API key 관리 권한 | MVP 1 설계 |

## Permission Vocabulary와 Subject

초기 permission은 작고 명확하게 시작한다.

| Permission | 의미 | 예시 |
| --- | --- | --- |
| `read` | 조회 가능 | workflow 보기, log 보기 |
| `write` | 수정 가능 | workflow graph 수정 |
| `execute` | 실행 가능 | workflow 실행 |
| `use` | 다른 리소스에서 참조/사용 가능 | knowledge base를 LLM node에서 사용 |
| `manage` | 권한/설정 관리 가능 | RBAC 설정, credential 관리 |
| `deploy` | 배포 가능 | deployment 생성/활성화 |

`admin`은 permission이 아니다. `Admin`, `Builder`, `Operator`, `Viewer`, `Auditor`는 DB role이 아니라 team template 또는 UI preset으로 취급한다.

| Subject Type | 설명 | MVP |
| --- | --- | --- |
| `team` | 기본 권한 subject. user는 team membership을 통해 권한을 얻는다. | MVP 1 |
| `user` | 예외적 추가 권한 subject. resource별 `user_*_permissions`로 additive allow만 부여한다. | MVP 1 |

조직 범위는 현재 코드의 `organization`, `organization_memberships`, `teams`, `team_memberships`, `team_*_permissions`를 사용한다. `organization_memberships`는 user와 organization의 직접 소속 전제이고, `team_memberships`는 team 배정 관계다. `roles`, `user_roles`, polymorphic `resource_permissions`는 새로 만들지 않는다.

## 권장 DB 모델

```text
current code baseline
  organization
  organization_memberships
  teams
  team_memberships
  team_workflow_permissions
  team_knowledge_permissions
  team_llm_permissions
  team_audit_permissions

implemented additive user direct grants
  user_workflow_permissions
  user_llm_permissions

planned additive user direct grants
  user_knowledge_permissions
  user_audit_permissions
```

현재 코드에서는 `user_workflow_permissions`, `user_llm_permissions`가 구현되어 있다. `user_knowledge_permissions`는 MVP 2, `user_audit_permissions`는 MVP 3에서 추가한다. 모든 user direct grant는 additive allow만 지원하고 deny는 도입하지 않는다.

## 권한 체크 위치

권한은 API에서만 보면 부족하다. 실행 경로에서도 체크해야 한다.

| 위치 | 체크 대상 |
| --- | --- |
| Gateway API dependency | 화면/API 접근 권한 |
| Workflow save | workflow `write` 권한 |
| Workflow execute | workflow `execute` 권한 |
| LLM node 실행 | credential `use` 권한 + credential-model relation |
| RAG retrieval | knowledge base `use` 권한 + document metadata policy. MVP 2에서 enforcement |
| Deployment create/toggle/delete | 기본 `read`/`deploy`/`manage` 권한은 Gateway route에서 적용. deploy checklist, diff, trigger logging은 MVP 3에서 강화 |
| Audit log query | audit resource `read` 권한. MVP 2-3에서 강화 |

Public Run/Webhook은 기존 Moduly처럼 app `auth_secret`을 계속 사용한다. 다만 내부 실행 context에서는 deployment/workflow/model/data source 정책을 추가로 평가할 수 있게 설계한다.

## Audit / Tracing Foundation

MVP 1에서 audit logging 규칙을 먼저 정한다. 현재 코드에는 `audit_logs`, `workflow_runs`, `workflow_node_runs`, `llm_usage_logs`가 있다. 기업용 audit에는 권한 차단, 변경 이벤트, 배포 이벤트, actor/action/resource 표준화, before/after snapshot, RAG chunk trace metadata, audit 검색 규칙이 필요하다.

사용 모델:

```text
audit_logs
  id
  occurred_at
  actor_id
  actor_type
  category
  action
  target_type
  target_id
  before
  after
  status
  audit_metadata
```

`action`은 `workflow.execute`, `permission.denied`처럼 사람이 읽을 수 있는 canonical action이다. `status`는 현재 코드 enum에 맞춰 `success` 또는 `failure`만 저장한다. `allow`, `deny`, `warn`, `block` 같은 정책 결과는 `audit_metadata.policy_result`에 저장한다. `ip`, `user_agent`, `request_id`는 별도 column이 아니라 `audit_metadata`에 저장한다.

권한 출처 스냅샷이 필요하면 `audit_metadata.effective_permission`, `audit_metadata.source_team_ids`, `audit_metadata.user_direct_permission_id`처럼 metadata에 저장한다. 권한 판단의 원천은 `team_*_permissions`와 `user_*_permissions`다.

현재 코드에 구현된 MVP 1 주요 `audit_logs.action` 값:

| Action | 예시 | MVP |
| --- | --- | --- |
| `app.create` | app 생성 | MVP 1 |
| `app.update` | app 수정 | MVP 1 |
| `app.clone` | app clone | MVP 1 |
| `app.delete` | app 삭제 | MVP 1 |
| `workflow.create` | workflow 생성 | MVP 1 |
| `workflow.update` | workflow graph 수정 | MVP 1 |
| `workflow.execute` | workflow 실행 시도와 결과 | MVP 1 |
| `permission.denied` | RBAC/resource permission 부족으로 거부 | MVP 1 |
| `team_workflow_permission.created/updated/deleted` | team workflow permission row 변경 | MVP 1 |
| `user_workflow_permission.created/updated/deleted` | user workflow permission row 변경 | MVP 1 |
| `team_llm_permission.created/updated/deleted` | team LLM credential permission row 변경 | MVP 1 |
| `user_llm_permission.created/updated/deleted` | user LLM credential permission row 변경 | MVP 1 |
| `auth.permission_denied` | 인증 전 또는 resource helper 밖의 전역 401/403 거부 | MVP 1 |
| `llm.call` | LLM 호출 | MVP 1 |
| `workflow.deploy` | deployment 생성 | MVP 1 |
| `deployment.toggle` | deployment 일반 활성/비활성 toggle | MVP 1 |
| `deployment.activate_previous` | 기존 `toggle`로 이전 deployment를 다시 활성화한 경우 | MVP 1 |
| `deployment.delete` | deployment 삭제 | MVP 1 |
| `organization.update` | organization 이름/options 수정 | MVP 1 |
| `credential.create` | LLM credential 생성 | MVP 1 |
| `credential.delete` | LLM credential 삭제 | MVP 1 |

MVP 2/3 목표 action 중 `policy.warn`, `policy.block`, `rag.retrieve`는 MBA-78에서 `AuditAction` 상수와 테스트로 먼저 고정한다. `rag.retrieve`는 RAG retrieval 성공 감사 action으로 사용하고, `policy.warn`/`policy.block`의 실제 document metadata policy enforcement 연결은 MVP 2 후속 구현 범위다. `deployment.check`, `recommendation.*`는 아직 목표 action이다.

현재 `AuditAction`에는 `permission.grant`, `permission.revoke` 상수가 있지만, 등록된 `/api/v1/permissions/*` router는 권한 부여/수정/회수를 위 permission row별 data-change action으로 기록한다.

`workflow.blocked`는 DB에 저장하는 canonical action으로 쓰지 않는다. UI에서 "workflow 차단" 표시가 필요하면 `target_type='workflow'`와 `permission.denied` 또는 `policy.block` event를 조합해 파생한다.

MVP 1부터 `request_id` 또는 `trace_id` 개념을 둔다.

```text
trace_id
  -> workflow_run
  -> workflow_node_run
  -> llm_usage_log
  -> trace_payloads / trace metadata
  -> audit_logs
```

처음부터 모든 모델에 nullable `trace_id`를 넣지 않아도 되지만, API 응답과 logging service에서는 trace id를 전달할 수 있게 설계한다.

설계 주의점:

- audit log는 business log이며 debug log와 다르다.
- audit schema는 너무 빨리 복잡하게 만들지 않는다.
- 검색 가능한 컬럼과 JSON metadata를 나눈다.
- audit log에는 민감한 prompt/input/output 원문을 기본 저장하지 않는다.
- 저장할 경우 redaction 또는 hash/snapshot 정책을 둔다.

## LLM Trace

MVP 1에서 필요한 LLM trace 정보:

```text
llm_trace
  workflow_run_id
  node_id
  model_id
  credential_id
  prompt_tokens
  completion_tokens
  total_cost
  latency_ms
  status
  error_message
```

기존 `llm_usage_logs`를 최대한 재사용한다. 현재 코드 기준 물리 column과 SQLAlchemy 속성명은 모두 `latency_ms`다. 과거 `atency_ms` column은 `f8a9b0c1d2e3_rename_llm_usage_latency_ms.py` migration에서 `latency_ms`로 정리한다.

MVP 1의 successful LLM usage logging 범위는 workflow LLM node 성공 호출이다. Provider response에 token usage가 없어도 schema가 허용하는 최소 usage row를 남기고, 실제 runtime에서 선택된 credential id를 사용한다. Wizard, RAG/retrieval/ingestion, embedding, LlamaParse/parser usage logging은 후속 범위다.

## Data Governance / Policy Skeleton

MVP 1에서는 policy decision이 audit/tracing에 남을 수 있는 구조를 만든다. 실제 데이터 소스 차단은 MVP 2에서 강화하고, MVP 1의 enforcement는 workflow 권한과 LLM credential `use` 권한 및 credential-model relation 중심이다. MBA-43은 application-level model blacklist/allowlist를 구현하지 않으며, model restriction 차단에 `policy.block`을 기록하지 않는다.

초기 classification:

| Classification | 의미 | 기본 정책 |
| --- | --- | --- |
| `public` | 공개 가능 | 일반 실행 허용 |
| `internal` | 사내 업무 데이터 | 로그인 사용자/프로젝트 범위 허용 |
| `confidential` | 민감 업무 데이터 | 권한 필요, audit 필수 |
| `pii` | 개인정보 가능성 | 경고 또는 차단 |

`phi`, `financial` 등은 MVP 3 이후 확장 후보로 둔다.

정책 결과:

| Decision | 의미 |
| --- | --- |
| `allow` | 실행 허용 |
| `warn` | 실행은 허용하지만 경고와 audit 기록 |
| `block` | 실행 차단 |

정책 대상:

| 대상 | 예시 정책 |
| --- | --- |
| Knowledge Base | 예: HR KB는 허용된 team permission 또는 향후 user direct grant를 받은 user만 `use` 가능 |
| Document | PII 문서는 외부 모델 호출 전 warn/block |
| LLM Credential/Model | 고가 모델은 허용된 credential, credential `use` 권한, verified credential-model relation을 가진 user만 사용 가능 |
| Workflow | `viewer`는 `execute` 불가 |
| Deployment | `operator`/`builder`/`manager` 중 배포 정책에 맞는 state만 `deploy` 가능 |

정책 엔진은 범용 DSL보다 명확한 rule부터 시작한다. audit log에는 민감한 prompt/input/output 원문을 기본 저장하지 않고, 저장할 경우 redaction 또는 hash/snapshot 정책을 둔다.

## MVP 1 사용자 흐름

1. 신규 user가 가입하면 기본 organization/team/membership이 생성된다.
2. organization owner/manager가 UI에서 team을 만들고 user를 team에 넣는다.
3. owner/manager 또는 resource `manager`가 UI에서 workflow/LLM credential 권한을 부여한다.
4. owner/manager가 UI에서 organization-level LLM credential을 등록하고 model sync 결과를 확인한다.
5. `builder` 권한 user는 UI에서 LLM node 포함 workflow를 만들고 저장하고 실행한다.
6. `viewer` 권한 user는 UI에서 workflow를 볼 수 있지만 저장/실행은 차단된다.
7. `operator` 권한 user는 UI에서 workflow를 실행할 수 있지만 저장은 차단된다.
8. `builder` 권한이 있어도 credential `use` 권한이 없거나 verified credential-model relation이 없으면 실행 전 또는 LLM node 실행 시 차단된다.
9. 허용된 LLM node 포함 workflow를 실행한다.
10. UI에서 노드별 model, token, cost, latency, status를 본다.
11. 권한 실패와 LLM call이 audit/trace 구조에 남고 UI에서 확인된다.
12. 같은 입력으로 model 또는 prompt 비교를 수행한다.

## 추가 개발 범위

Foundation:

- resource type enum 정의
- permission vocabulary 정의
- 현재 코드의 team permission과 user direct permission 모델 확정
- permission check helper/API dependency 추가
- workflow read/write/execute와 LLM credential `use` + credential-model relation check 최소 적용
- audit_logs action/metadata skeleton 추가
- trace id 전달 구조 설계
- classification 상수와 metadata convention 설계
- policy decision metadata 구조
- 현재 organization context 표시와 API 요청 scope 전달
- team/member/permission 관리 UI
- 권한별 workflow editor 상태 제어

LLMOps Observability:

- 구현된 LLM trace 조회 API 유지 및 회귀 테스트
- run detail에서 node run과 LLM usage 연결
- editor canvas에 node별 비용/토큰/latency badge
- LLMOps run detail/side panel
- model/prompt comparison UI
- rule-based 추천 skeleton
- audit/activity 확인 UI

## 작업 순서

1. Resource / Permission 설계

작업:

- resource type 정의
- permission enum 정의
- `auth_state` 표준값과 team template 정의
- App을 project boundary로 사용할지 확정
- Workflow를 canvas resource로 매핑
- deployment 전용 `deploy` permission 포함 여부 확정

산출물:

- shared schema 또는 constants
- backend permission helper
- 간단한 설계 문서 주석

검증:

- permission helper unit test

2. RBAC 최소 모델

작업:

- 현재 코드의 `team_workflow_permissions`, `team_llm_permissions` 사용 기준 확정
- `user_workflow_permissions`, `user_llm_permissions` 추가
- migration 작성
- seed team template과 team permission row 추가

검증:

- `builder` 권한 user는 workflow execute 가능
- `viewer` 권한 user는 workflow execute 불가
- 허용되지 않은 LLM credential 또는 credential-model relation 차단

3. Audit Skeleton

작업:

- `audit_logs` 기록 helper 추가
- workflow execute 시작/종료 event 저장
- permission denied event 저장

검증:

- 권한 실패 시 `audit_logs` row 생성
- workflow 실행 시 `audit_logs` row 생성

4. LLM Trace API

작업:

- `llm_usage_logs`와 `workflow_node_runs` 연결 확인
- run id 기준 trace 조회 API 검증
- node id 기준 trace 필터 검증
- latency 컬럼명 정합성 확인

검증:

- LLM node 실행 후 model/token/cost/latency 조회
- 여러 LLM node 실행 시 node별 구분

5. RBAC / Credential UI

작업:

- 현재 organization 표시
- team 생성/수정 UI, member 추가/제거 UI, `DELETE /api/v1/teams/{team_id}` 기반 team 비활성화 UI
- team member 추가/제거 UI
- workflow permission 부여/회수 UI
- LLM credential permission 부여/회수 UI
- user direct permission 부여/회수 UI
- organization-level credential 등록/model sync UI
- 권한별 read-only/save/execute 버튼 상태 처리

검증:

- owner/manager가 UI에서 team/member/permission/credential을 설정 가능
- `viewer`는 editor read-only, 저장/실행 불가
- `operator`는 실행 가능, 저장 불가
- `builder`는 저장/실행 가능

6. Run Detail / Canvas UI

작업:

- run detail panel에 LLM trace 표시
- canvas node badge 추가
- 실패 노드와 비용 큰 노드 표시
- audit/activity panel에서 실행/차단 이벤트 표시

검증:

- 실행 완료 후 UI에서 비용/토큰/latency 확인
- 권한 차단/실행 이벤트가 UI에서 확인됨

7. Model / Prompt Compare

작업:

- 같은 input으로 2개 설정 실행
- 결과, 비용, latency 비교 UI
- 품질 점수는 사용자 평가 또는 rule-based check로 제한

검증:

- model A/B 결과가 저장/표시됨

## 완료 기준

| 영역 | 완료 기준 |
| --- | --- |
| RBAC foundation | `viewer` 실행/수정 차단, `builder` 실행/수정 허용, credential `use` 권한 또는 verified credential-model relation 없는 LLM 사용 차단 |
| Audit foundation | 권한 차단과 workflow 실행 이벤트가 `audit_logs`에 저장됨 |
| LLM trace | run/node/model/token/cost/latency/status 조회 가능 |
| RBAC UI | owner/manager가 team/member/workflow permission/LLM credential permission을 UI에서 관리 가능 |
| Credential UI | organization-level credential 등록, model sync, credential preview 가능 |
| Workflow UI | 권한별 read-only/save/execute 상태가 UI에 반영됨 |
| LLMOps UI | 실행 상세 또는 캔버스에서 LLMOps 정보를 확인 가능 |
| 비교 | 모델 또는 프롬프트 2개를 같은 입력으로 비교 가능 |
| Audit UI | 권한 차단과 workflow 실행 이벤트를 activity/audit UI에서 확인 가능 |
| 안정성 | 기존 workflow 생성/저장/실행 흐름이 깨지지 않음 |

## Demo Script

```text
1. 신규 user로 가입한다.
2. UI에서 현재 organization을 확인한다.
3. owner/manager가 UI에서 team을 생성한다.
4. owner/manager가 UI에서 user를 team에 추가한다.
5. owner/manager가 UI에서 workflow와 LLM credential permission을 부여한다.
6. owner/manager가 UI에서 LLM credential을 등록하고 model sync를 실행한다.
7. `builder` 권한 user가 UI에서 LLM node 포함 workflow를 만들고 저장한다.
8. `viewer` 권한 user로 workflow를 열면 read-only이며 저장/실행이 차단된다.
9. `operator` 권한 user로 workflow 실행은 가능하지만 저장은 차단된다.
10. `builder` 권한 user가 허용되지 않은 모델로 실행하면 차단된다.
11. `builder` 권한 user가 허용된 모델로 실행하면 성공한다.
12. run detail 또는 캔버스에서 노드별 비용/토큰/latency/status를 확인한다.
13. model 또는 prompt 비교를 실행한다.
14. Audit/activity UI에서 실행/차단 이벤트를 확인한다.
```

## 테스트 범위

- permission helper test
- workflow execute permission test
- LLM credential `use` permission과 credential-model relation test
- audit_logs create test
- LLM trace API test
- RBAC management UI smoke test
- credential management UI smoke test
- workflow 권한별 UI state smoke test
- LLMOps run detail/canvas UI smoke test
- model/prompt compare UI smoke test
- client build
- 기존 workflow engine 핵심 테스트

## MVP 1에서 하지 않을 것

- 데이터 소스 권한 enforcement 완성
- RAG chunk lineage 저장
- PII 자동 탐지
- 배포 전 체크
- cache/fallback/retry 실행 정책
- 노드 단위 RBAC
- multi-organization switcher 완성
- 전체 navigation 개편
- 전체 dashboard UI UX 개선
- 상세 role catalog 관리 화면
- 외부 IdP/OIDC 연동
- 복잡한 조직 계층
- row-level permission
- 임시 권한 위임
