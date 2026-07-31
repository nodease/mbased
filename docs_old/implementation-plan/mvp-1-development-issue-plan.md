# MVP 1 개발 이슈 생성 계획서

Status: Draft
Authority: Implementation Plan
Source of Truth: Yes
Verified Against: dev @ ec576b4f24155697aed8843acc6e5a3fc835f7e1
Related ADRs: [ADR-202606290116-accept-rbac-auth-state-and-user-direct-permission](../decisions/ADR-202606290116-accept-rbac-auth-state-and-user-direct-permission.md), [ADR-202606290131-audit-action-naming-standard](../decisions/ADR-202606290131-audit-action-naming-standard.md), [ADR-202606290145-active-organization-header-context](../decisions/ADR-202606290145-active-organization-header-context.md)

## 1. 목적

이 문서는 `requirements/mvp-1-foundation-llmops.md`까지 실제 개발하기 위해 어떤 개발 이슈를 생성해야 하는지 정리한다.

기준은 다음 세 가지다.

| 기준 | 내용 |
| --- | --- |
| 현재 코드 구현 | 로컬 `dev` 브랜치의 `ec576b4f24155697aed8843acc6e5a3fc835f7e1` 코드 |
| MVP 1 목표 문서 | `requirements/mvp-1-foundation-llmops.md` |
| Linear 참고 | Linear All Issues에서 확인한 미완료 이슈 목록 |

이 문서는 Linear 이슈를 너무 잘게 쪼개지 않는다. AI와 함께 구현할 수 있도록, 실제로 동작하는 MVP 단위의 굵은 작업으로 묶는다.

## 2. 확인 기준

| 항목 | 값 |
| --- | --- |
| 확인 브랜치 | `dev` |
| 확인 commit | `ec576b4f24155697aed8843acc6e5a3fc835f7e1` |
| commit 요약 | `Merge pull request #118 from nodease/feature/mba-71` |
| 확인 시점 | 2026-06-30 |

참고한 주요 문서:

- `requirements/mvp-1-foundation-llmops.md`
- `data-model/physical-data-model.md`
- `data-model/rbac-permission-policy.md`
- `api/organization-rbac.md`
- `api/llm-credentials.md`
- `architecture/tracing-audit.md`
- `api/tracing-audit.md`

Linear 확인 범위:

- Computer Use로 Linear의 `mbased > All issues` 목록을 확인했다.
- 확인한 정보는 issue id, title, status, priority, label 수준이다.
- 각 Linear issue의 상세 본문과 acceptance criteria 전체는 모두 열람하지 않았다.
- 따라서 이 문서는 Linear 목록 기반의 이슈 재구성 계획이며, 실제 Linear 생성/수정 전에는 관련 기존 issue 본문을 한 번 더 확인해야 한다.

## 3. MVP 1 목표 요약

MVP 1은 다음이 동시에 동작해야 한다.

```text
작동하는 워크플로우 빌더
  + RBAC/resource foundation
  + audit/tracing foundation
  + data governance/policy skeleton
  + 노드별 LLMOps observability
```

완료 기준은 다음으로 압축한다.

| 영역 | MVP 1 완료 기준 |
| --- | --- |
| RBAC foundation | team permission과 user direct permission으로 workflow/LLM credential 접근을 제한한다. |
| Workflow 권한 | `viewer`는 조회만 가능하고, `operator`는 실행 가능하며, `builder`는 수정/실행 가능하다. |
| LLM 권한 | credential `use` 권한이 없거나 verified credential-model relation이 없는 LLM 사용은 실행에서 차단된다. |
| Audit | 권한 차단과 workflow 실행 이벤트가 `audit_logs`에 남는다. |
| LLM trace | run/node/model/token/cost/latency/status를 조회할 수 있다. |
| UI | 실행 상세 또는 캔버스에서 LLMOps 정보를 확인할 수 있다. |
| 비교 | 모델 또는 프롬프트 2개를 같은 입력으로 비교할 수 있다. |
| 안정성 | 기존 workflow 생성/저장/실행 흐름이 깨지지 않는다. |

작동하는 MVP의 기준:

- Backend API만 완성된 상태는 MVP 완료가 아니다.
- seed/script만으로 권한을 세팅해야 하는 상태는 MVP 완료가 아니다.
- 사용자는 브라우저 UI에서 MVP1 핵심 흐름을 직접 수행할 수 있어야 한다.
- UI는 모든 enterprise 기능을 갖출 필요는 없지만, demo script를 끝까지 수행할 수 있어야 한다.
- 이 원칙은 MVP 2, MVP 3 개발 이슈 계획에도 동일하게 적용한다.

MVP1에서 필요한 최소 UI:

| UI 영역 | MVP1에서 반드시 동작해야 하는 것 |
| --- | --- |
| Organization context | 로그인 후 현재 organization scope를 인지할 수 있다. 단일 organization만 지원해도 된다. |
| Team/member 관리 | organization owner/manager가 team을 만들고 user를 team에 넣을 수 있다. |
| Permission 관리 | workflow와 LLM credential에 team/user direct permission을 부여/회수할 수 있다. |
| LLM credential 관리 | organization owner/manager가 credential을 등록하고, 사용 가능한 model 목록을 확인할 수 있다. |
| Workflow editor | 권한에 따라 read-only, save 가능, execute 가능 상태가 UI에 반영된다. |
| Execution UI | workflow 실행과 streaming/result 확인이 가능하다. |
| LLMOps run detail | node별 model/token/cost/latency/status를 확인할 수 있다. |
| Canvas observability | 최근 실행 기준으로 LLM node badge 또는 side panel에서 비용/토큰/latency를 볼 수 있다. |
| Model/prompt compare | 같은 입력으로 두 설정을 실행하고 결과/비용/latency를 비교할 수 있다. |
| Audit 확인 | 최소한 권한 차단과 실행 이벤트가 남았는지 사용자 UI에서 확인할 수 있다. |

MVP 1에서 하지 않는 것은 명확히 제외한다.

- RAG chunk lineage 저장
- RAG data source permission enforcement 완성
- PII 자동 탐지
- Guardrail 노드 완성
- retry/cache/fallback 실행 정책
- API key별 cost 제한
- deployment checklist
- organization-wide raw trace access를 audit permission model과 통합
- 외부 IdP/OIDC
- 별도 `roles`, `user_roles`, polymorphic `resource_permissions`
- 별도 `audit_events`

## 4. 현재 코드 구현 상태

### 4.1 이미 있는 기반

| 영역 | 현재 구현 |
| --- | --- |
| Organization | `organization` 모델이 있고 `created_by`, `managed_by`, `is_active`, `options`, `flags`가 있다. |
| Team | `teams`, `team_memberships`가 있다. |
| Team permission | `team_workflow_permissions`, `team_knowledge_permissions`, `team_llm_permissions`, `team_audit_permissions`가 있다. |
| User schema | `users.organization_id`는 없다. 조직 소속은 team membership 기준이다. |
| Audit | `audit_logs`, audit decorator, SQLAlchemy listener, Celery `audit.record` task가 있다. |
| Permission denied audit | gateway exception handler에서 401/403 audit 기록 기반이 있다. |
| Workflow run log | `workflow_runs`, `workflow_node_runs`가 있다. |
| LLM usage | `llm_usage_logs`에 user/model/credential/run/node/token/cost/latency/status 기반이 있다. |
| LLM catalog | `llm_providers`, `llm_models`, `llm_credentials`, `llm_rel_credential_models`가 있다. |
| Frontend workflow | Canvas, editor, workflow API client, run list/detail/log UI 기반이 있다. |
| Dashboard/Statistics | workflow stats와 user audit log를 조회하는 일부 UI/API가 있다. |

### 4.2 MVP 1 대비 주요 gap

| 영역 | gap |
| --- | --- |
| User direct permission | workflow/LLM credential의 `user_workflow_permissions`, `user_llm_permissions`는 구현됨. `user_knowledge_permissions`, `user_audit_permissions`는 MVP 2/3 범위다. |
| Permission vocabulary | `none/viewer/operator/builder/manager/auditor/raw_auditor` 표준과 `read/write/execute/admin` legacy mapping이 구현됨. |
| Gateway permission dependency | workflow와 LLM credential permission helper, permission API, permission denied audit가 구현됨. endpoint별 회귀 테스트가 남아 있다. |
| Organization bootstrap | default organization/team/membership helper가 구현됐고 signup/social signup 경로에서 호출됨. |
| Active organization | `X-Organization-Id` header 방식이 organization/team/permission API와 app 생성/목록/복제, workflow 생성 API에 구현됨. header 없는 fallback은 user directory, LLM credential 생성의 optional `organization_id`, service-level legacy 보정처럼 제한된 경로에 남아 있다. |
| App/Workflow scope | 현재 Gateway `POST /api/v1/apps`, `GET /api/v1/apps`, `POST /api/v1/apps/{app_id}/clone`, `POST /api/v1/workflows`는 `X-Organization-Id`를 요구한다. `AppService.create_app`/`WorkflowService.create_workflow`에는 직접 service 호출 또는 legacy app 보정용 default organization fallback이 남아 있다. |
| Workflow enforcement | workflow read/write/execute/stream/runs/stats 주요 endpoint에 `ensure_workflow_permission`이 적용됨. 생성/list/app 경계 회귀 테스트가 남아 있다. |
| LLM credential scope | credential 등록은 organization manager를 요구하고 `organization_id`를 저장한다. list는 credential `read`, delete/sync는 credential `write`로 필터링한다. |
| LLM runtime use check | workflow engine LLM node 실행 시 credential `use` 권한과 verified credential-model relation을 평가한다. |
| LLM trace API | run/node 기준 `GET /workflows/{workflow_id}/runs/{run_id}/llm-traces`가 구현됨. UI 연결과 회귀 테스트가 남아 있다. |
| Audit action 표준 | `permission.denied`, `workflow.execute`, `workflow.deploy`, `deployment.toggle`, `deployment.activate_previous` 등 canonical action 상수가 구현됨. 현재 permission API의 grant/update/revoke는 permission row별 `*_permission.created/updated/deleted` data-change action으로 기록된다. |
| FE RBAC | 조직/팀/권한 관리 화면, active organization UI, 권한별 enable/disable 처리가 없다. |
| FE LLMOps | run detail/canvas badge/model-prompt compare를 MVP1 demo 기준으로 연결해야 한다. |

## 5. Linear 미완료 이슈 분류

### 5.1 MVP 1에 포함하거나 병합할 이슈

| Linear | 현재 상태 | MVP 1 처리 |
| --- | --- | --- |
| `MBA-36` RBAC 수정으로 인한 모듈 생성 버그 | In Progress, Bug, Urgent | 포함. App/Workflow organization scope와 RBAC enforcement 이슈에 병합한다. 상세 원인은 구현 전 기존 issue 본문 확인 필요. |
| `MBA-34` RBAC role/permission matrix 확정 | In Progress, docs, High | 새 개발 이슈로 쪼개지 않는다. `data-model/rbac-permission-policy.md` 기준 검수 후 완료 후보로 둔다. |
| `MBA-15` RBAC 기반 Organization 및 Team Permission 관리 API | Todo, Feature, Medium | 포함. RBAC foundation 이슈에 병합한다. |
| `MBA-14` RBAC기반 회원가입 시 Organization 생성 및 관리 | Todo, Feature, Medium | 포함. Organization bootstrap 이슈에 병합한다. |
| `MBA-17` LLM routing 중앙화, organization-level credential management | Backlog, Feature, High | 포함. LLM credential routing 이슈에 병합한다. |
| `MBA-18` LLM Provider Organization단위 관리로 변경 | Backlog, Feature, High | 포함. `MBA-17`과 분리하지 않고 같은 LLM credential 이슈로 묶는다. |
| `MBA-25` LLM token/cost/latency 기록 및 집계 기반 구현 | Backlog, Feature, Medium | 부분 포함. usage log와 run/node trace API는 있으므로 UI 연결, organization scope, 회귀 테스트 보강만 MVP1에 포함한다. |
| `MBA-20` 노드 구성 및 상세 편집 UX 개편 | In Progress, Medium | 부분 포함. MVP1 LLMOps 표시와 직접 관련된 UI만 포함한다. 일반 UX 개편은 후순위. |
| `MBA-6` 워크플로우(Canvas) UI UX 개선 | In Progress, Medium | 부분 포함. Canvas node badge와 실행 상세 연결만 포함한다. |
| `MBA-30` Enterprise LLMOps E2E 통합 테스트 작성 | Backlog, Feature, Medium | 포함. 단, 전체 enterprise가 아니라 MVP1 acceptance regression으로 scope를 줄인다. |

### 5.2 후순위로 미뤄도 되는 이슈

| Linear | 현재 상태 | 후순위 사유 |
| --- | --- | --- |
| `MBA-40` Audit 관리자 페이지 구현 | Todo, Feature, High | MVP1은 audit skeleton과 기록 검증이 목표다. 조직 단위 audit 관리자 페이지는 MVP2-3 audit search/visibility와 함께 가는 것이 자연스럽다. |
| `MBA-38` 감사 로그 필터가 최근 50건에만 적용되는 문제 | In Progress, Bug, Low | MVP1에서 lightweight activity panel로 실행/차단 이벤트만 확인한다면 후순위 가능하다. 기존 audit list/filter 화면을 MVP1 demo에 재사용하면 trace/audit API 이슈에 포함한다. |
| `MBA-39` 감사 로그 API pagination 테스트가 실제 offset/limit을 검증하지 못하는 문제 | Backlog, Bug, Low | lightweight activity panel을 쓰면 후순위 가능하다. 기존 audit list/pagination API를 MVP1 UI에 재사용하면 테스트 보강으로 함께 처리한다. |
| `MBA-21` 대시보드 UI UX 개선 | Todo, Feature, Medium | MVP1은 LLMOps 최소 관측성이다. 전체 dashboard UX 개선은 MVP3 operations dashboard에서 처리한다. |
| `MBA-7` Stitch 디자인 세트 적용 | In Progress, Medium | MVP1 기능 완성과 직접 관계가 없다. |
| `MBA-13` Edge 연결 Handle 시각화 개선 | Todo, Feature, Medium | Canvas UX polish다. MVP1의 LLMOps badge와 직접 관련이 없다. |
| `MBA-12` 번호 기반 Edge 연결 UX 추가 | Todo, Feature, Medium | Canvas UX polish다. |
| `MBA-9` Canvas 다중 선택 UX 개선 | Todo, Feature, Medium | Canvas UX polish다. |
| `MBA-5` Guardrail 노드 구현 | Todo, Feature, Low | MVP1은 policy skeleton만 만든다. Guardrail 노드 실행/정책 완성은 MVP2 이후가 맞다. |
| `MBA-32` API key 별 Cost 제한 기능 구현 | Backlog, Medium | cost 제한은 운영/거버넌스 기능이다. MVP1은 token/cost/latency 기록과 조회까지만 한다. |
| `MBA-29` Workflow 실행 retry 정책 및 실패 격리 기준 정의 | Backlog, Feature, Medium | retry/cache/fallback은 MVP1 제외 범위다. |
| `MBA-28` 평가 데이터 후보 추출 API 구현 | Backlog, Feature, Medium | 평가/추천 고도화는 MVP3 이후가 맞다. |
| `MBA-27` Retrieved chunk lineage 저장 | Backlog, Feature, Medium | RAG lineage는 MVP2 범위다. |
| `MBA-26` 문서 변경 감지 기반 Partial Re-index MVP 구현 | Backlog, Feature, Medium | RAG indexing 고도화는 MVP2 범위다. |

### 5.3 결정이 필요해서 바로 포함 여부를 확정하지 않는 이슈

| Linear | 현재 상태 | 필요한 결정 |
| --- | --- | --- |
| `MBA-16` 기존 Nav를 Organization 기준으로 변경 | Backlog, Feature, High | 부분 포함. MVP1에는 현재 organization 표시와 기본 scope 인지가 필요하다. 다중 organization switcher와 nav 전체 개편은 active organization 방식이 확정될 때 포함한다. |

## 6. 생성할 개발 이슈

아래 6개 이슈를 생성하면 MVP1 범위를 과도하게 쪼개지 않으면서 구현 순서를 유지할 수 있다. 현재 코드 기준으로 Issue 1~3의 backend foundation은 상당 부분 구현되어 있으므로, 신규 이슈는 "신규 구현"보다 "검증, endpoint coverage, FE 연결, 테스트 보강" 중심으로 재작성한다.

### Issue 1. `[BE][Infra][VERIFY] MVP1 RBAC/Organization Foundation 정렬`

목표:

- 현재 코드의 조직/팀 물리 데이터 모델을 보존하면서 MVP1 permission foundation을 검증한다.
- 구현된 `none/viewer/operator/builder/manager/auditor/raw_auditor` 표준과 legacy mapping이 모든 permission 경로에서 일관되게 쓰이는지 확인한다.
- 구현된 `user_workflow_permissions`, `user_llm_permissions` additive allow 동작을 테스트로 고정한다.

관련 Linear:

- `MBA-14`
- `MBA-15`
- `MBA-34`
- `MBA-36` 일부

구현 범위:

| 작업 | 내용 |
| --- | --- |
| Permission constants | 구현된 `none/viewer/operator/builder/manager/auditor/raw_auditor`와 permission vocabulary를 테스트로 고정한다. |
| Effective permission helper | organization owner/manager 자동 `manager`, team permission, user direct permission 합산 결과를 검증한다. |
| User direct tables | 구현된 `user_workflow_permissions`, `user_llm_permissions` 모델과 migration을 검증한다. |
| Additive allow | user direct permission이 team permission을 낮추거나 deny하지 않는지 검증한다. |
| Gateway dependency | 구현된 `ensure_workflow_permission`, `ensure_llm_credential_permission`의 endpoint 적용 범위를 점검한다. |
| Organization bootstrap | signup/social signup에서 기본 organization/team/membership 생성이 동작하는지 회귀 테스트한다. |
| Team management API | team 생성/수정, membership 추가/제거, `DELETE /api/v1/teams/{team_id}` 비활성화, resource permission grant/revoke API를 검증한다. Team 관리 권한 판정과 team/team member 조회는 `TeamService`가 소유하며, inactive team 비활성화 재시도는 같은 organization manager scope 안에서 idempotent success로 처리한다. |
| Audit | permission row 생성/수정/삭제와 `permission.denied`가 `audit_logs`에 기록되는지 검증한다. |
| Trace RBAC 정렬 | trace 접근 제어가 MVP 표준 상태와 legacy mapping을 일관되게 해석하는지 확인한다. |
| 기존 데이터 호환 | 기존 DB row에 `read/write/execute/admin`이 있으면 현재 compatibility mapping이 의미를 유지하는지 검증한다. |

구현 방법:

1. `apps/shared/db/models/team.py`의 team permission table과 user direct permission model을 현재 코드 기준으로 검증한다.
2. Alembic migration과 실제 model이 `user_workflow_permissions`, `user_llm_permissions`를 같은 schema로 생성하는지 확인한다.
3. 각 user direct table은 아래 공통 column을 가진다.

```text
id
grantee_organization_id
user_id
workflow_id 또는 llm_credential_id
auth_state
assigned_by
assigned_at
options
flags
```

4. unique constraint는 organization, user, resource 조합 중복을 막는다.
5. permission helper는 다음 순서를 따른다.

```text
1. user 인증
2. active organization 결정
3. resource organization scope 확인
4. organization.created_by 또는 organization.managed_by이면 manager
5. active organization의 team membership 조회
6. team permission 조회
7. user direct permission 조회
8. team/user direct 중 가장 강한 auth_state 선택
9. permission action 허용 여부 반환
10. 거부 또는 permission row 생성/수정/삭제는 audit_logs 기록
```

6. `auth_state` 강도는 다음으로 고정한다.

```text
none < viewer < operator < builder < manager
auditor < raw_auditor < manager
```

7. `admin`은 permission으로 쓰지 않는다. 기존 `admin` 문자열이 남아 있으면 `manager` 의미로만 호환한다.
8. `roles`, `user_roles`, `resource_permissions`는 만들지 않는다.

Acceptance Criteria:

- 조직 owner/manager는 해당 organization scope 안에서 `manager`로 판정된다.
- 권한 row가 없으면 fail-closed로 거부된다.
- `viewer`는 workflow read만 가능하다.
- `operator`는 workflow execute 가능, write 불가다.
- `builder`는 workflow write/execute 가능, deploy/manage 불가다.
- `manager`는 workflow deploy/manage까지 가능하다.
- team `viewer` + user direct `builder`이면 effective permission은 `builder`다.
- team `manager` + user direct `viewer`이면 effective permission은 `manager`다.
- LLM credential `operator` 또는 `builder`는 `use` 가능하다.
- LLM credential `viewer`는 `use` 불가다.
- permission row 생성/수정/삭제와 `permission.denied`가 `audit_logs`에 남는다.
- tracing RBAC 테스트가 MVP 표준 `auth_state`를 기준으로 통과한다.

Out of Scope:

- `user_knowledge_permissions`
- `user_audit_permissions`
- node-level permission
- connection 전용 permission table
- external IdP/OIDC

### Issue 2. `[BE][FIX/VERIFY] App/Workflow 권한 enforcement와 모듈 생성 버그 수정`

목표:

- workflow/app 접근 제어가 현재 구현된 MVP1 RBAC helper를 일관되게 사용하는지 검증한다.
- app/workflow Gateway 생성 경로가 `X-Organization-Id` 계약을 지키고, service-level default organization fallback이 신규 route에서 과도하게 열리지 않는지 검증한다.
- Linear `MBA-36`의 모듈 생성 버그 회귀 조건을 이 범위 안에서 검증한다.

관련 Linear:

- `MBA-36`
- `MBA-15` 일부

구현 범위:

| 작업 | 내용 |
| --- | --- |
| Active organization 적용 | App/Workflow 생성, 복제, 조회에서 organization scope가 일관되는지 검증하고 FE header 전달 누락을 보강한다. |
| App as project boundary | app 전용 permission table을 만들지 않고 primary workflow permission으로 app 접근을 판단한다. |
| Workflow read | workflow 상세, draft 조회, runs, stats에 적용된 workflow `read` 권한을 검증한다. |
| Workflow write | draft 저장에 적용된 workflow `write` 권한을 검증한다. |
| Workflow execute | execute, stream에 적용된 workflow `execute` 권한을 검증한다. |
| Existing owner fallback | legacy resource처럼 organization scope가 없는 경우에만 `created_by` fallback을 제한적으로 허용한다. |
| Audit | resource permission helper에서 발생한 RBAC 거부는 `audit_logs.action='permission.denied'`로 기록한다. 인증 실패나 helper 밖의 전역 401/403은 `auth.permission_denied`로 기록한다. |
| Regression fix | module 생성, app 생성, workflow 생성, draft 저장, stream 실행이 깨지지 않도록 보장한다. |

구현 방법:

1. `apps/gateway/api/v1/endpoints/app.py`에서 app 생성/목록/복제가 `resolve_active_organization_id`를 호출하고, `AppService.create_app` fallback은 service-level legacy 보정으로만 남는지 확인한다.
2. `apps/gateway/api/v1/endpoints/workflow.py`와 `apps/gateway/services/workflow_service.py`에서 workflow 생성 route는 header organization을 전달하고, service fallback은 organization scope 없는 legacy app 보정에만 쓰이는지 확인한다.
3. app 생성 시 생성되는 primary workflow도 같은 organization scope를 가진다.
4. workflow endpoint의 permission helper 적용 범위를 점검한다.
5. `get_workflow_runs`, `get_workflow_run_detail`, `get_workflow_stats`의 read check를 테스트한다.
6. `execute_workflow`, `stream_workflow`의 execute check를 테스트한다.
7. `sync_draft_workflow`의 write check를 테스트한다.
8. 403은 공통 audit handler가 기록하도록 하되, 필요한 경우 permission helper에서 권한 출처 metadata를 추가한다.

Acceptance Criteria:

- `viewer`는 workflow 상세/draft/runs/stats를 볼 수 있지만 저장/실행할 수 없다.
- `operator`는 실행할 수 있지만 draft 저장은 거부된다.
- `builder`는 draft 저장과 실행이 가능하다.
- 권한 없는 user가 organization scope 밖 resource에 접근하면 `404`로 숨기고, 같은 scope 안에서 action 권한만 부족하면 `403`으로 거부한다.
- Gateway app 생성 시 app과 primary workflow의 `organization_id`가 `X-Organization-Id`와 일치한다. service-level fallback은 직접 service 호출 또는 legacy app 보정 경로에서만 사용된다.
- workflow 생성/저장/실행 기존 happy path가 통과한다.
- Linear `MBA-36`의 재현 조건이 있으면 같은 테스트로 회귀 방지한다.

Out of Scope:

- deployment 권한 강화
- marketplace 공개 정책
- app 전용 permission table
- multi-organization switcher UI

### Issue 3. `[BE][Infra][VERIFY] Organization-level LLM credential routing과 runtime use 권한 적용`

목표:

- 현재 구현된 LLM credential organization scope와 permission filter를 검증한다.
- LLM node 실행 시 credential `use` 권한과 credential-model relation을 함께 평가하는 동작을 테스트로 고정한다.
- user-scoped 통계 API와 organization-scoped credential API의 경계를 문서화한다.

관련 Linear:

- `MBA-17`
- `MBA-18`
- `MBA-25` 일부

구현 범위:

| 작업 | 내용 |
| --- | --- |
| Credential scope | `llm_credentials.organization_id` 저장/조회 동작을 검증한다. |
| Credential read | credential list/preview가 credential `read` 권한 기준으로 제한되는지 검증한다. |
| Credential create | 새 credential 생성이 organization owner/manager에게만 허용되는지 검증한다. 생성 직후 권한 row를 어떻게 만들지는 8.3의 결정에 따른다. |
| Credential write/manage | 기존 credential 삭제/sync-models/권한 관리가 credential `write` 또는 `manager` 권한 기준으로 제한되는지 검증한다. |
| Runtime use | workflow engine LLM node가 credential `use` 권한을 확인하는지 검증한다. |
| Model relation | model 사용 가능 여부는 `llm_rel_credential_models.is_verified`와 credential permission을 함께 평가한다. |
| Usage log | `llm_usage_logs.organization_id`, `workflow_id`, `workflow_run_id`, `node_id`를 가능한 범위에서 채운다. |
| Wizard hardening | prompt/code/template Wizard helper를 authenticated LLM runtime으로 보고 credential `use`, verified relation, active model을 적용한다. |
| Runtime block audit | workflow LLM node와 Wizard POST runtime 차단을 `permission.denied`로 기록한다. |
| Cost query | 현재 `top-models`는 user-scoped다. organization/workflow scope 통계가 필요하면 별도 보강한다. |

구현 방법:

1. `apps/gateway/services/llm_service.py`의 credential 조회가 permission-aware인지 확인한다.
2. credential 등록 시 request `organization_id` 또는 default organization을 저장하는지 확인한다.
3. credential 생성은 organization owner/manager를 기준으로 제한하고, 삭제/동기화는 permission helper를 거치는지 확인한다.
4. LLM client 선택은 다음 순서를 따른다.

```text
1. 요청 model_id 확인
2. active organization 확인
3. 해당 model을 지원하는 verified credential 후보 조회
4. 후보 credential 중 current user가 use 권한을 가진 credential만 남김
5. 우선순위가 있으면 llm_rel_credential_models.priority, 없으면 안정적인 기준으로 하나 선택
6. 없으면 fail-closed
```

5. Workflow Engine LLM runtime service는 명시적으로 전달된 valid organization_id를 요구한다. organization_id가 없거나 invalid하면 `organization_scope_missing`으로 fail-closed 처리하고, fallback model을 시도하지 않는다.
6. Workflow Engine runtime은 `llm_credentials.organization_id IS NULL` legacy credential을 사용하지 않는다. legacy null credential은 organization backfill 또는 reassignment 이후 runtime 후보가 될 수 있다.
7. workflow engine으로 전달되는 execution context에 user_id, organization_id, workflow_id, workflow_run_id를 전달한다.
8. LLM node 실행 시 gateway와 workflow_engine 양쪽 서비스가 같은 permission 판단을 재사용하도록 shared helper를 둔다.
9. model 전용 permission table은 만들지 않는다.
10. Wizard helper는 provider/model map 순서를 먼저 따르고, 같은 Wizard model 후보 안에서 `llm_rel_credential_models.priority`, credential 생성일, credential id 순으로 runtime credential을 선택한다.
11. Successful workflow LLM node usage logging은 실제 실행 credential id를 사용하고, usage logging 단계에서 credential을 다시 선택하지 않는다.

Acceptance Criteria:

- credential `viewer`는 credential preview 조회만 가능하고 runtime use는 거부된다.
- credential `operator` 또는 `builder`는 LLM node 실행에서 credential을 사용할 수 있다.
- organization owner/manager는 새 credential을 생성할 수 있다.
- credential `manager`는 기존 credential 삭제/동기화/권한 관리를 할 수 있다.
- verified relation이 없는 model은 credential 권한이 있어도 사용할 수 없다.
- credential `use` 권한이 없거나 verified credential-model relation이 없는 조합으로 workflow를 실행하면 LLM node 실행 전 또는 실행 중 명확히 차단된다.
- Workflow Engine runtime organization scope가 없거나 invalid하면 `organization_scope_missing`으로 차단되고 fallback model을 시도하지 않는다.
- workflow LLM node 또는 Wizard POST runtime 차단 이벤트는 `permission.denied` audit에 남는다.
- 성공한 workflow LLM node call은 provider usage가 없더라도 schema가 허용하는 최소 `llm_usage_logs` row를 남기고 organization/run/node/model/credential/token/cost/latency를 가능한 범위에서 채운다.

Out of Scope:

- API key별 cost limit
- model 전용 permission table
- application-level model blacklist/allowlist policy
- `policy.block` recording for model restrictions
- `X-Organization-Id` mandatory migration and default organization fallback removal
- Wizard usage logging
- RAG/retrieval/ingestion usage logging and runtime block audit
- Gateway ingestion embedding credential routing through permission-aware LLM credential selection
- LlamaParse/parser credential policy
- Workflow engine credential management method refactor
- Workflow Engine model listing helper refactor
- 외부 provider별 고급 routing 정책
- retry/fallback/cache

### Issue 4. `[BE][Data][VERIFY] Audit/LLM trace API foundation`

목표:

- MVP1에서 필요한 audit skeleton과 구현된 LLM trace 조회 API를 검증한다.
- 기존 `audit_logs`, `workflow_runs`, `workflow_node_runs`, `llm_usage_logs`를 재사용한다.
- 새 audit table이나 dashboard aggregate table을 만들지 않는다.

관련 Linear:

- `MBA-25` 일부
- `MBA-38` 조건부
- `MBA-39` 조건부

구현 범위:

| 작업 | 내용 |
| --- | --- |
| Audit action 정리 | workflow execute, permission denied, llm call에 필요한 action을 기존 audit 체계에 맞춘다. |
| Workflow execute audit | execute 시작/성공/실패 또는 최소 성공/실패 이벤트를 남긴다. |
| Permission denied audit | 권한 실패 metadata에 resource/action/effective permission을 남긴다. |
| LLM trace query | 구현된 `/workflows/{workflow_id}/runs/{run_id}/llm-traces`가 run id와 node id 기준으로 `llm_usage_logs`를 조회하는지 검증한다. |
| Run detail 확장 | run detail 응답 또는 별도 endpoint에서 node별 LLM usage를 반환한다. |
| Latency 정합성 | 현재 코드에서는 ORM 속성과 물리 column 모두 `latency_ms`를 쓴다. 과거 `atency_ms`는 migration에서 정리한다. |
| Audit pagination | MVP1 UI/API에서 audit list를 사용한다면 `MBA-38`, `MBA-39`를 함께 처리한다. 사용하지 않으면 후순위로 둔다. |

구현 방법:

1. `audit_logs`는 canonical audit table로 유지한다.
2. `audit_logs.status`에는 현재 코드 enum 기준 `success` 또는 `failure`만 저장한다.
3. `allow/deny/warn/block` 같은 정책 결과는 `audit_metadata.policy_result`에 저장한다.
4. LLM trace API는 현재 구현된 SQLAlchemy query와 응답 schema를 검증한다.
5. trace 조회는 workflow `read` 권한을 통과한 user만 가능하게 한다.
6. `llm_usage_logs.latency_ms` ORM 속성과 DB physical column을 기준으로 테스트한다.

API 계약은 [api/tracing-audit.md](../api/tracing-audit.md)의 LLM trace 항목을 따른다. 현재 코드는 별도 `llm-traces` endpoint를 채택했으므로 중복 API를 만들지 않는다.

Acceptance Criteria:

- workflow 실행 성공/실패가 audit에 남는다.
- 권한 차단이 audit에 남는다.
- LLM node 실행 후 run_id 기준으로 model/token/cost/latency/status를 조회할 수 있다.
- 여러 LLM node가 있어도 node_id로 구분된다.
- trace API는 workflow `read` 권한 없이는 거부된다.
- `latency_ms` column이 모델과 migration/test 기준으로 일관된다.

Out of Scope:

- 조직 전체 audit 관리자 페이지
- raw trace payload permission 완성
- RAG retrieval trace
- dashboard aggregate table

### Issue 5. `[FE][BE] MVP1 End-to-End UI: RBAC, credential, observability, compare`

목표:

- MVP1이 backend-only가 아니라 브라우저에서 작동하는 제품 흐름이 되게 한다.
- organization/team/permission/credential/workflow/observability/compare의 최소 UI를 제공한다.
- run detail 또는 canvas에서 node별 cost/token/latency/status를 확인할 수 있게 한다.
- 같은 입력으로 model 또는 prompt 2개를 비교할 수 있게 한다.

관련 Linear:

- `MBA-6` 일부
- `MBA-20` 일부
- `MBA-16` 일부

구현 범위:

| 작업 | 내용 |
| --- | --- |
| Run detail | node별 model/token/cost/latency/status 표시 |
| Canvas badge | 최근 실행 기준으로 node badge에 cost/token/latency/status 표시 |
| Failure emphasis | 실패 node와 비용이 큰 node를 시각적으로 구분 |
| Organization indicator | 현재 organization scope를 nav/header/settings 중 한 곳에 표시 |
| Team/member UI | team 생성, member 추가/제거의 최소 화면 제공. team 비활성화는 구현된 `DELETE /api/v1/teams/{team_id}` 계약과 idempotent success/error 처리를 기준으로 UI를 연결한다. |
| Permission UI | workflow permission과 LLM credential permission grant/revoke 화면 제공 |
| User direct grant UI | 특정 user에게 workflow/LLM credential 추가 권한을 부여/회수할 수 있는 최소 UI 제공 |
| Credential management UI | organization-level credential 등록, model sync, credential preview 확인 |
| Permission-aware editor | 권한에 따라 canvas/editor의 저장/실행 버튼과 read-only 상태 반영 |
| Credential permission UX | 권한 없는 credential 또는 verified relation 없는 model은 선택 불가 또는 실행 전 명확한 오류 표시 |
| Model/prompt compare | 같은 입력으로 두 설정을 실행하고 결과, 비용, latency를 비교 |
| Audit confirmation UI | 권한 차단/실행 이벤트를 확인할 수 있는 최소 audit list 또는 activity panel 제공 |
| Organization nav | `MBA-16`에서 다루는 nav 전체 개편은 후순위 가능하지만, 현재 organization 표시와 MVP1 이동 경로는 포함 |

구현 방법:

1. 기존 `apps/client/app/features/workflow/api/workflowApi.ts`의 run/detail API를 확장한다.
2. `LogDetail`, `LogList`, monitoring/statistics 컴포넌트의 기존 표시 구조를 우선 재사용한다.
3. Canvas node badge는 최근 run detail에서 받은 node별 usage summary를 표시한다.
4. 비교 기능은 새 table을 만들지 않고 기존 workflow run과 llm usage를 사용한다.
5. 비교 결과를 장기 상태로 관리하지 않는다. 장기 저장이 필요하면 MVP3 이후 schema extension으로 분리한다.
6. RBAC 관리 UI는 별도 대형 admin console이 아니라 settings 또는 organization 관리 화면의 최소 CRUD로 시작한다.
7. 권한이 없는 action은 버튼을 숨기기보다 disabled 상태와 서버 에러 메시지를 함께 처리한다.
8. viewer 계정으로 들어왔을 때 workflow editor가 read-only로 보이고 저장/실행이 막히는지 확인한다.
9. builder/operator/manager별로 같은 화면에서 허용 action이 다르게 보이도록 한다.

Acceptance Criteria:

- organization owner/manager가 UI에서 team을 만들고 user를 team에 넣을 수 있다.
- organization owner/manager 또는 resource `manager`가 UI에서 workflow/LLM credential 권한을 부여/회수할 수 있다.
- user direct permission을 UI에서 부여/회수할 수 있다.
- organization owner/manager가 UI에서 LLM credential을 등록하고 model sync 결과를 확인할 수 있다.
- `viewer`는 workflow editor를 read-only로 볼 수 있고 저장/실행 버튼이 막힌다.
- `operator`는 실행 가능하지만 저장 버튼이 막힌다.
- `builder`는 저장과 실행이 가능하다.
- LLM node 포함 workflow 실행 후 UI에서 node별 token/cost/latency/status를 확인할 수 있다.
- 여러 LLM node가 있을 때 node별 값이 섞이지 않는다.
- 권한 없는 credential 또는 verified relation 없는 model로 실행하려 하면 사용자가 이유를 이해할 수 있는 오류가 보인다.
- model A/B 또는 prompt A/B 결과, 비용, latency를 한 화면에서 비교할 수 있다.
- audit UI 또는 activity panel에서 권한 차단/실행 이벤트를 확인할 수 있다.
- 기존 Canvas 주요 기능이 깨지지 않는다.

Out of Scope:

- Stitch 디자인 세트 전체 적용
- Edge handle 시각화 개선
- 번호 기반 edge 연결
- Canvas 다중 선택 UX
- 전체 dashboard UI UX 개선
- multi-organization switcher 완성
- 상세 role catalog 관리 화면

### Issue 6. `[Test][QA] MVP1 acceptance regression과 Linear 정리`

목표:

- MVP1의 실제 완료 여부를 검증한다.
- 기존 기능이 RBAC 적용으로 깨지지 않았는지 확인한다.
- Linear 미완료 이슈를 새 MVP1 이슈와 후순위 이슈로 정리한다.

관련 Linear:

- `MBA-30`
- `MBA-34`
- `MBA-38` 조건부
- `MBA-39` 조건부

구현 범위:

| 작업 | 내용 |
| --- | --- |
| Unit test | permission helper, auth_state mapping, user direct additive allow |
| API test | workflow read/write/execute, LLM credential read/use/manage, permission denied audit |
| Service test | organization bootstrap, app/workflow organization scope |
| Engine test | LLM node runtime credential use check |
| Trace test | run/node LLM usage query |
| FE test/build | client build, 핵심 UI smoke test |
| UI E2E smoke | 브라우저에서 owner/manager, builder, viewer 흐름을 최소 1회 검증 |
| Linear cleanup | 기존 issue를 MVP1 새 이슈에 연결하거나 후순위 라벨/코멘트로 정리 |

필수 시나리오:

```text
1. 신규 user signup
2. UI에서 현재 organization 확인
3. owner/manager가 UI에서 team 생성
4. owner/manager가 UI에서 user를 team에 추가
5. owner/manager가 UI에서 workflow/LLM credential permission 부여
6. owner/manager가 UI에서 LLM credential 등록 및 model sync
7. builder 권한 user가 UI에서 workflow 생성/저장/실행
8. viewer 권한 user가 UI에서 workflow 조회 성공
9. viewer 권한 user가 UI에서 저장/실행 불가 상태 확인
10. builder 권한 user가 credential `use` 권한이 없거나 verified relation 없는 model로 실행 실패
11. builder 권한 user가 허용된 credential과 verified model relation으로 실행 성공
12. audit UI 또는 activity panel에서 실행/차단 이벤트 확인
13. run detail에서 node별 model/token/cost/latency 확인
14. model 또는 prompt 비교 결과 확인
```

Acceptance Criteria:

- MVP1 demo script가 처음부터 끝까지 통과한다.
- backend unit/API/service test가 통과한다.
- workflow engine 핵심 테스트가 통과한다.
- client build가 통과한다.
- 브라우저 UI smoke test가 통과한다.
- 새로 만든 MVP1 이슈와 기존 Linear 이슈의 관계가 문서화된다.
- 후순위로 미룬 Linear issue에 후순위 사유가 남아 있다.

Out of Scope:

- 전체 enterprise E2E
- 성능 부하 테스트
- RAG governance E2E
- deployment checklist E2E

## 7. 권장 구현 순서

```text
Issue 1 RBAC/Organization Foundation
  -> Issue 2 App/Workflow enforcement
  -> Issue 3 LLM credential routing/use enforcement
  -> Issue 4 Audit/LLM trace API
  -> Issue 5 FE end-to-end UI
  -> Issue 6 MVP1 regression/Linear cleanup
```

병렬 가능:

| 병렬 가능 구간 | 조건 |
| --- | --- |
| Issue 2와 Issue 3 | Issue 1의 permission helper interface가 먼저 고정되어야 한다. |
| Issue 4 일부와 Issue 5 일부 | trace API schema가 확정되면 FE는 mock 또는 fixture로 먼저 진행 가능하다. |
| Issue 6 테스트 작성 | 각 issue acceptance criteria가 확정되면 병렬 작성 가능하다. |

순서상 먼저 고정해야 하는 것:

1. active organization header 적용 범위
2. `auth_state` 표준값과 compatibility mapping
3. permission helper interface
4. workflow/app organization scope 처리
5. LLM credential runtime 선택 규칙
6. LLM trace API shape
7. MVP1 UI navigation과 settings 배치

## 8. 개발 전 결정 필요 사항

아래는 문서만 보고 임의로 확정하면 안 되는 항목이다. 실제 Linear 이슈 생성 전에 결정이 필요하다.

### 8.1 Active organization 적용 범위

MVP 1의 active organization 전달 방식은 `X-Organization-Id` header로 확정한다. 현재 코드에는 organization/team/permission API와 app 생성/목록/복제, workflow 생성 API에서 header를 검증하는 흐름이 있다. header가 없는 fallback은 user directory, LLM credential 생성의 optional `organization_id`, service-level legacy 보정처럼 제한된 경로에 남아 있다.

검증 필요:

- App/Workflow Gateway 생성/목록/복제 경로와 LLM credential 생성/조회가 각자의 organization source를 일관되게 사용하는지
- header 없는 legacy/service-level fallback이 신규 Gateway route에서 과도하게 열리지 않는지
- FE nav에서 선택한 organization이 모든 RBAC/permission API 요청에 전달되는지

영향:

- `MBA-16` 포함 여부
- 모든 permission helper의 입력값
- App/Workflow/LLM credential 생성 scope

### 8.2 신규 가입 시 organization 이름과 입력 방식

현재 signup schema는 user email/name/password 중심이며, 코드에서는 signup/social signup 후 기본 organization/team/membership을 자동 생성한다.

결정 필요:

- signup request에 `organization_name`을 추가할지
- 아니면 user 이름/email 기반으로 기본 organization 이름을 자동 생성할지
- social signup의 bootstrap은 현재 코드에서 적용되어 있으므로, 이름 정책만 맞추면 된다.

영향:

- `MBA-14` 구현 범위
- FE signup form 변경 여부
- seed/default team naming

### 8.3 새 resource 생성자의 기본 권한

문서상 organization owner/manager는 organization scope에서 자동 `manager`다. 그러나 organization owner가 아닌 `builder`가 새 workflow를 만들 때, 해당 workflow에 대해 생성자를 어떤 권한으로 둘지는 별도 결정이 필요하다.

결정 필요:

- 생성자를 해당 workflow `manager`로 자동 grant할지
- 생성자를 `builder`로만 두고 권한 관리는 organization owner/manager에게만 맡길지
- app/primary workflow 생성 시 default team permission row를 어디까지 자동 생성할지

영향:

- workflow create API
- permission grant UX
- demo user setup

### 8.4 MVP1 권한 관리 UI의 위치와 깊이

MVP1에는 최소 권한 관리 UI를 포함한다. API와 seed만으로 권한을 세팅하는 방식은 작동 MVP로 보지 않는다.

결정 필요:

- 기존 settings/nav 안에 둘지
- organization 전용 settings page를 만들지
- workflow/credential detail 안에서 resource permission drawer로 둘지

영향:

- FE issue scope
- `MBA-16` 포함 여부
- QA/demo script

### 8.5 Model/prompt compare 저장 방식

MVP1 완료 기준에는 model 또는 prompt 비교가 있다. 물리 데이터 모델 문서상 compare 전용 table은 만들지 않는다.

결정 필요:

- 비교 결과를 UI-only로 볼지
- 기존 `workflow_runs` 2개를 선택해 비교할지
- 두 실행을 묶는 `compare_group_id`를 metadata/request context로 남길지

영향:

- trace API shape
- FE compare UX
- audit metadata

## 9. 모순 검사 결과

### 9.1 문서와 이슈 계획 간 모순 없음

| 기준 | 검사 결과 |
| --- | --- |
| `roles`, `user_roles` 미생성 | 지킴. 이슈 계획에 포함하지 않음. |
| polymorphic `resource_permissions` 미생성 | 지킴. resource별 user direct table만 추가. |
| 현재 물리 데이터 모델 보존 | 지킴. 기존 table 삭제/rename 없음. 단, `user_workflow_permissions`, `user_llm_permissions`는 additive extension. |
| team 중심 RBAC | 지킴. 기본 subject는 team, user direct는 additive allow. |
| explicit deny 미도입 | 지킴. deny table/column 없음. |
| `admin` permission 미사용 | 지킴. `admin`은 compatibility에서만 `manager` 의미로 다룸. |
| App as project boundary | 지킴. app permission table 만들지 않음. |
| LLM model permission table 미생성 | 지킴. credential permission과 credential-model relation으로 처리. |
| Audit table | 지킴. `audit_logs`만 사용. |
| Audit status | 지킴. `success/failure`만 status, policy result는 metadata. |
| Dashboard aggregate table 미생성 | 지킴. raw query와 기존 table 사용. |
| RAG/Guardrail/Retry 후순위 | 지킴. MVP1 issue에서 제외. |
| 작동 MVP UI 기준 | 보강함. MVP1은 API-only가 아니라 browser UI에서 demo script가 통과해야 함. |

### 9.2 현재 코드와 계획 간 확인된 gap

아래 항목은 현재 코드 기준으로 남은 검증 또는 보강 대상이다.

| gap | 처리 |
| --- | --- |
| trace 접근 제어와 legacy `read/write/execute/admin` 호환성 | Issue 1에서 MVP 표준 `auth_state` 기준으로 검증 |
| signup/social signup bootstrap | Issue 1에서 기본 organization/team/membership 생성 회귀 테스트 |
| app/workflow 생성 scope | Issue 2에서 Gateway header contract와 service-level legacy fallback 경계를 검증하고 FE header wiring 보강 |
| workflow permission endpoint coverage | Issue 2에서 주요 read/write/execute 경로 회귀 테스트 |
| LLM credential organization/RBAC scope | Issue 3에서 create/read/write/use 경로 회귀 테스트 |
| LLM runtime credential use 권한 | Issue 3에서 engine-level check 회귀 테스트 |
| LLM trace UI/테스트 보강 | Issue 4에서 API 회귀 테스트, Issue 5에서 UI 연결 |
| FE RBAC/LLMOps 표시 미완성 | Issue 5에서 구현 |

### 9.3 후순위 처리와 MVP1 목표 간 모순 없음

후순위로 둔 이슈들은 MVP1의 필수 완료 기준을 직접 막지 않는다.

예외:

- `MBA-38`, `MBA-39`는 lightweight activity panel 대신 기존 audit list/search UI를 MVP1 demo에 사용하기로 결정하면 Issue 4 또는 Issue 6에 포함해야 한다.
- `MBA-16`은 현재 organization 표시와 MVP1 이동 경로만 Issue 5에 포함하고, 다중 organization switcher와 nav 전체 개편은 active organization 방식이 확정될 때 포함한다.

## 10. Linear issue 생성 형태

실제 Linear에는 아래 6개 issue를 새로 만들거나 기존 issue에 병합하는 방식이 적절하다.

| 순서 | Linear issue title 후보 | 분야 |
| --- | --- | --- |
| 1 | `MVP1 RBAC/Organization Foundation 정렬` | `[BE][Infra][FIX]` |
| 2 | `MVP1 App/Workflow 권한 enforcement와 모듈 생성 버그 수정` | `[BE][FIX]` |
| 3 | `MVP1 Organization-level LLM credential routing과 runtime use 권한 적용` | `[BE][Infra]` |
| 4 | `MVP1 Audit/LLM trace API foundation` | `[BE][Data][FIX]` |
| 5 | `MVP1 End-to-End UI: RBAC, credential, observability, compare` | `[FE][BE]` |
| 6 | `MVP1 acceptance regression과 Linear 정리` | `[Test][QA]` |

기존 Linear issue와의 관계:

| 새 이슈 | 연결할 기존 Linear |
| --- | --- |
| Issue 1 | `MBA-14`, `MBA-15`, `MBA-34`, `MBA-36` 일부 |
| Issue 2 | `MBA-36`, `MBA-15` 일부 |
| Issue 3 | `MBA-17`, `MBA-18`, `MBA-25` 일부 |
| Issue 4 | `MBA-25` 일부, `MBA-38` 조건부, `MBA-39` 조건부 |
| Issue 5 | `MBA-6` 일부, `MBA-20` 일부, `MBA-16` 일부 |
| Issue 6 | `MBA-30`, `MBA-34`, 조건부 bug cleanup |

후순위 후보 issue에는 다음 코멘트를 남긴다.

```text
MVP1 Foundation & LLMOps Observability 범위에서는 core acceptance criteria를 막지 않으므로 후순위로 둔다.
MVP2 Governance/RAG/Audit 또는 MVP3 Enterprise Ops 계획에서 다시 끌어온다.
```

단, `MBA-38`, `MBA-39`는 audit UI 구현 방식에 따라 MVP1 포함으로 바뀔 수 있으므로 단순 후순위 확정 코멘트를 남기지 않는다. `MBA-16`은 현재 organization 표시와 MVP1 이동 경로만 포함하고, 다중 organization switcher와 nav 전체 개편은 별도 후순위 범위로 남긴다.
