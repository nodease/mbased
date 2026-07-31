# Nodease 물리 데이터 모델

Status: Draft
Authority: Data Model
Source of Truth: Yes
Verified Against: feature/mba-89 @ 3a1d6799118f5a6bb50414914b40865e6375e35f (base dev @ 5e67adba265346009fbbc691ee16e287cd89548e, PR #142 follow-up, 2026-07-01 KST)
Related ADRs: [ADR-202606271559-audit-log-rag-trace-storage](../decisions/ADR-202606271559-audit-log-rag-trace-storage.md), [ADR-202606271559-data-model-document-structure](../decisions/ADR-202606271559-data-model-document-structure.md), [ADR-202606290116-accept-rbac-auth-state-and-user-direct-permission](../decisions/ADR-202606290116-accept-rbac-auth-state-and-user-direct-permission.md), [ADR-202606290124-mvp2-classification-metadata-storage](../decisions/ADR-202606290124-mvp2-classification-metadata-storage.md), [ADR-202606290131-audit-action-naming-standard](../decisions/ADR-202606290131-audit-action-naming-standard.md), [ADR-202606301045-metadata-aware-hierarchical-rag-boundary](../decisions/ADR-202606301045-metadata-aware-hierarchical-rag-boundary.md), [ADR-202607010220-rag-answer-trace-usage-correlation-boundary](../decisions/ADR-202607010220-rag-answer-trace-usage-correlation-boundary.md)

## 목적

이 문서는 현재 workspace code의 물리 데이터 모델을 기준선으로 보존하면서, MVP 1, MVP 2, MVP 3 목표 상태에서 필요한 table, 주요 column, 참조관계, schema extension 기준을 정리한다.

이 문서는 기존 DB table, column, relationship을 대체하거나 되돌리는 설계를 하지 않는다. 추가로 필요한 table은 현재 코드의 물리 데이터 모델과 충돌하지 않는 additive extension으로만 정의한다. Mermaid 관계도는 별도 시각화 문서로 분리한다.

## 기준

| 항목 | 기준 |
| --- | --- |
| 기준 브랜치 | `dev` |
| 확인 commit | `860ece0dee7cab3925d27f30ea650baf0cb18b4e` |
| 기준 모델 경로 | `apps/shared/db/models/*` |
| 기준 migration 경로 | `apps/shared/alembic/versions/*` |
| 기존 목표 초안 | 삭제된 로컬 폐기 초안. 구현 기준이 아니다. |

## 설계 원칙

| 원칙 | 내용 |
| --- | --- |
| 현재 물리 데이터 모델 보존 | 현재 코드에 존재하는 table과 column을 삭제, rename, 대체하지 않는다. |
| 추가 schema 최소화 | MVP 목표 상태 기능은 우선 현재 코드의 기존 table 조합으로 구현하고, 필요한 경우 additive table만 추가한다. |
| RBAC 기준 | `roles`, `user_roles`, polymorphic `resource_permissions`를 새로 만들지 않는다. 현재 코드는 `organization_memberships`를 organization 소속과 manager 판정의 우선 기준으로 사용하고, `teams`, `team_memberships`, `team_*_permissions`를 team 배정과 team resource permission 기준으로 유지한다. |
| User direct grant | 현재 코드는 `user_workflow_permissions`, `user_llm_permissions`를 구현한다. 이후 개별 user 예외 권한은 resource별 `user_*_permissions` table로만 추가한다. direct grant는 additive allow 전용이다. |
| Organization owner/manager | active `organization_memberships.organization_auth_state == "manager"`를 우선 기준으로 판정한다. Membership row 자체가 없는 legacy data에서만 `organization.created_by` 또는 `organization.managed_by` user를 manager fallback으로 인정한다. Invited/suspended/removed row가 있으면 fallback 없이 fail-closed 처리한다. |
| Audit 기준 | `audit_events`를 새로 만들지 않는다. 현재 코드의 `audit_logs`를 canonical audit table로 사용한다. |
| Trace 기준 | `rag_retrieval_traces`를 새로 만들지 않는다. 현재 코드의 `workflow_runs`, `workflow_node_runs`, `trace_payloads`, `trace_*_policies`, `trace_payload_access_events`를 workflow trace 기준으로 사용한다. Standalone RAG Agent answer는 trace table에 RAG 전용 FK를 추가하지 않고 RAG-owned answer run과 `correlation_id`로 연결한다. |
| Tenant 기준 | `tenant_id`를 새로 설계하지 않는다. 현재 코드의 `organization_id`를 조직 범위 기준으로 사용한다. |
| Project boundary | 제품상의 project boundary는 `apps`로 본다. 단, 상위 조직 범위는 `organization`이다. |
| Dashboard | 별도 aggregate table, materialized view, dashboard 전용 table을 만들지 않고 raw query로 시작한다. |

## 표기 규칙과 한계

이 문서는 현재 물리 데이터 모델을 보존하기 위한 table 설계 기준서다. 따라서 아래 규칙을 따른다.

| 항목 | 규칙 |
| --- | --- |
| table 이름 | 현재 코드의 SQLAlchemy model `__tablename__`을 기준으로 쓴다. |
| column 목록 | 전체 DDL 명세가 아니라 MVP 목표 상태 설계 판단에 필요한 주요 column 목록이다. nullable, index, ondelete의 최종 근거는 현재 코드 model과 Alembic migration이다. |
| 관계 표기 | `A.column -> B.column`은 FK가 `A.column`에서 `B.column`을 참조한다는 뜻이다. 특정 table 상세의 "관계" 목록은 해당 table이 참조하는 outgoing FK와 다른 table이 해당 table을 참조하는 incoming FK를 모두 포함할 수 있으므로, 방향을 column 기준으로 읽는다. JSONB metadata에 id를 넣는 방식은 관계가 아니라 application-level convention이다. |
| `organization_id` | 현재 코드에서 nullable인 기존 column은 이 문서에서 non-null로 바꾸지 않는다. |
| `auth_state` | 현재 DB enum이 아니다. 허용값과 의미는 `data-model/rbac-permission-policy.md`의 application-level matrix를 따른다. |
| `audit_logs.status` | 현재 코드 기준 `success`/`failure` 상태만 저장한다. `pass`/`warn`/`block` 같은 정책 결과는 `audit_logs.audit_metadata.policy_result`에 저장한다. |

## 기존 문서에서 변경된 결정

이 문서는 이전 ERD 초안의 결정을 다음처럼 바꾼다.

| 이전 결정 | 재설계 결정 |
| --- | --- |
| `roles` 신규 생성 | 생성하지 않는다. 현재 코드의 `teams`와 permission table을 사용한다. |
| `user_roles` 신규 생성 | 생성하지 않는다. 사용자-organization 직접 소속은 `organization_memberships`를 사용하고, 사용자-team 배정은 `team_memberships`를 사용한다. |
| `resource_permissions` 신규 생성 | 생성하지 않는다. team 권한은 `team_workflow_permissions`, `team_knowledge_permissions`, `team_llm_permissions`, `team_audit_permissions`를 사용한다. user 직접 권한은 resource별 `user_*_permissions`를 사용한다. |
| `audit_events` 신규 생성 | 생성하지 않는다. `audit_logs`를 사용한다. |
| `rag_retrieval_traces` 신규 생성 | 생성하지 않는다. trace 계열 테이블과 JSONB payload convention으로 처리한다. |
| RAG answer를 trace/usage table FK로 연결 | `trace_payloads.rag_answer_run_id`, `llm_usage_logs.rag_answer_run_id`를 추가하지 않는다. Standalone answer는 `rag_answer_runs`와 opaque `correlation_id`로 연결한다. |
| `deployment_check_runs`, `deployment_check_items` 필수 생성 | 현재 물리 데이터 모델 보존 조건에서는 생성하지 않는다. check 결과가 필요하면 `audit_logs`에 action/result metadata로 남긴다. |
| `recommendation_events` 필수 생성 | 현재 물리 데이터 모델 보존 조건에서는 생성하지 않는다. recommendation lifecycle은 `audit_logs` action과 metadata로 남긴다. |
| `tenant_id` 유지 | 유지하지 않는다. 현재 코드의 `organization_id`를 따른다. |
| group/organization model 범위 밖 | 현재 코드에 이미 있으므로 MVP 목표 상태 기준선에 포함한다. |
| `knowledge_bases.classification`, `documents.classification` 추가 | 현재 물리 데이터 모델 보존 조건에서는 column을 추가하지 않는다. classification이 필요하면 별도 schema 변경 승인 전까지 metadata/audit/trace payload 수준에서만 다룬다. |
| 개별 user direct permission | resource별 user permission table을 추가한다. polymorphic table은 만들지 않는다. |

## Table 분류

### Current Code Table

현재 코드에 이미 존재하며, MVP 목표 상태에서도 그대로 사용하는 table이다.

| Table | MVP 목표 상태 역할 |
| --- | --- |
| `users` | 사용자, 실행 actor, resource owner |
| `organization` | 조직 범위, tenant-like boundary |
| `organization_memberships` | User와 organization의 직접 소속 관계. MBA-67에서 permission helper와 organization/team/user/permission API 일부가 이 기준으로 전환됐다. MBA-71에서 active organization과 manager/member 화면 분기가 일부 반영됐다. Organization member/invitation BE API는 구현됐고, full membership 관리 UI는 후속 범위 |
| `teams` | 조직 내 권한 부여 단위 |
| `team_memberships` | 사용자와 팀의 소속 관계 |
| `team_workflow_permissions` | 팀 단위 workflow 권한 |
| `team_knowledge_permissions` | 팀 단위 knowledge base 권한 |
| `team_llm_permissions` | 팀 단위 LLM credential 권한 |
| `team_audit_permissions` | 팀 단위 audit visibility 권한 |
| `apps` | project boundary, endpoint boundary |
| `workflows` | Canvas, draft graph, 실행 대상 workflow |
| `workflow_deployments` | 배포 snapshot, version, deployment type |
| `schedules` | schedule deployment 실행 설정 |
| `workflow_runs` | workflow 실행 이력, dashboard raw query 원천 |
| `workflow_node_runs` | node 실행 이력, node 단위 observability |
| `trace_redaction_policies` | trace payload redaction 정책 |
| `trace_retention_policies` | trace payload retention 정책 |
| `trace_visibility_policies` | trace payload visibility 정책 |
| `trace_payloads` | 실행/노드 단위 trace payload 저장소 |
| `trace_payload_access_events` | trace payload 접근 감사 |
| `audit_logs` | 사용자 action과 data change 감사 로그 |
| `knowledge_bases` | RAG data source 상위 단위 |
| `documents` | RAG 문서 단위 |
| `document_chunks` | RAG retrieval 최소 검색 단위 |
| `connections` | 외부 DB data source |
| `llm_providers` | LLM provider catalog |
| `llm_models` | LLM model catalog |
| `llm_credentials` | 사용자/조직 LLM credential |
| `llm_rel_credential_models` | credential-model 사용 가능 관계 |
| `llm_usage_logs` | LLM token/cost/latency 원천 |

### Implemented Foundation Table

아래 table은 MBA-66에서 DB/model/migration foundation으로 추가되었다. MBA-67에서 permission helper와 일부 API endpoint가 organization membership 기준으로 전환됐고, MBA-71에서 active organization과 manager/member 화면 분기가 일부 반영됐다. Organization member/invitation BE API는 구현됐고, full membership 관리 UI는 아직 후속 범위다.

| Table | 도입 단계 | 목표 역할 |
| --- | --- | --- |
| `organization_memberships` | MBA-66 / MVP 2-0 | 사용자와 organization의 직접 소속 관계. active organization 목록, organization manager 판정, team membership, user direct permission의 전제 조건 |

### Implemented Additive Table

현재 코드에서 개별 user direct permission을 지원하기 위해 추가된 table이다. 이 table들은 기존 team permission table을 대체하지 않고, additive allow extension으로만 동작한다.

| Table | 도입 MVP | 현재 코드 역할 |
| --- | --- | --- |
| `user_workflow_permissions` | MVP 1 | 특정 user에게 workflow 직접 추가 권한 부여 |
| `user_llm_permissions` | MVP 1 | 특정 user에게 LLM credential 직접 추가 권한 부여 |

### Planned Target Tables

아래 table은 최신 코드에는 아직 없고, 해당 MVP에서 추가할 목표 schema다. Permission additive table과 RAG domain table을 함께 나열하되, 각 table의 소유 도메인은 목표 역할에 명시한다.

| Table | 도입 MVP | 목표 역할 |
| --- | --- | --- |
| `user_knowledge_permissions` | MVP 2 | 특정 user에게 knowledge base 직접 추가 권한 부여 |
| `user_audit_permissions` | MVP 3 | 특정 user에게 target organization audit visibility 직접 추가 권한 부여 |

`rag_answer_runs`는 RAG 확장 3단계/MBA-89에서 additive current table로 도입한다. Workflow run이 없는 standalone RAG Agent answer 실행 anchor이며, trace/usage table에는 RAG 전용 FK를 추가하지 않고 `correlation_id`로 느슨하게 연결한다.

이전 문서에서 신규 table로 다루던 기능은 다음 기준 table로 정리한다.

| 기능 | 기준 Table |
| --- | --- |
| team 기반 역할/권한 | `organization`, `teams`, `team_memberships`, `team_*_permissions` |
| user 직접 추가 권한 | 현재 구현: `user_workflow_permissions`, `user_llm_permissions`. 목표: `user_knowledge_permissions`, `user_audit_permissions` 추가 |
| 감사 로그 | `audit_logs` |
| LLM/RAG trace | `workflow_runs`, `workflow_node_runs`, `trace_payloads`, `trace_*_policies` |
| trace payload 접근 감사 | `trace_payload_access_events` |
| deploy checklist 결과 | `audit_logs.audit_metadata` |
| recommendation lifecycle | `audit_logs.audit_metadata` |
| operations dashboard | 기존 log/run/trace/usage table raw query |

### 만들지 않는 Table

다음 항목은 현재 물리 데이터 모델 보존 조건에서 별도 table로 만들지 않는다.

| 항목 | 결정 |
| --- | --- |
| `roles` | 만들지 않는다. |
| `user_roles` | 만들지 않는다. |
| `resource_permissions` | 만들지 않는다. |
| `audit_events` | 만들지 않는다. |
| `rag_retrieval_traces` | 만들지 않는다. |
| `deployment_check_runs` | 만들지 않는다. |
| `deployment_check_items` | 만들지 않는다. |
| `recommendation_events` | 만들지 않는다. |
| 독립 `projects` table | 만들지 않는다. `apps`를 project boundary로 사용한다. |
| workflow node table | 만들지 않는다. `workflows.graph` 내부 node id를 string reference로 사용한다. |
| node-level permission table | 만들지 않는다. |
| `user_connection_permissions` | 만들지 않는다. connection `use`는 consuming workflow/knowledge base 권한으로 허용하고, secret/manage만 connection owner 또는 organization owner/manager로 제한한다. |
| `user_app_permissions` | 만들지 않는다. app 권한은 workflow 권한으로 대체한다. |
| `user_document_permissions` | 만들지 않는다. document 권한은 knowledge base 권한으로 대체한다. |
| `user_model_permissions` | 만들지 않는다. model 사용 제한은 credential 권한과 model relation으로 처리한다. |
| dashboard aggregate table | 만들지 않는다. |
| materialized view | 만들지 않는다. |
| MCP Gateway schema | 만들지 않는다. |

## Table 상세

### `users`

현재 코드 기준 table이다.

역할:

- 로그인 사용자
- app/workflow/knowledge base/connection/LLM credential owner
- workflow run actor
- audit log actor
- organization/team 생성자 또는 관리자

주요 column:

- `id`
- `email`
- `name`
- `password`
- `social_provider`
- `social_id`
- `avatar_url`
- `deactivated_at`
- `last_login_at`
- `created_at`
- `updated_at`

관계:

아래는 `users.id`를 참조하는 incoming FK다.

- `apps.created_by -> users.id`
- `workflows.created_by -> users.id`
- `workflows.updated_by -> users.id`
- `workflow_deployments.created_by -> users.id`
- `workflow_runs.user_id -> users.id`
- `knowledge_bases.user_id -> users.id`
- `connections.user_id -> users.id`
- `llm_credentials.user_id -> users.id`
- `llm_usage_logs.user_id -> users.id`
- `organization.created_by -> users.id`
- `organization.managed_by -> users.id`
- `organization_memberships.user_id -> users.id`
- `organization_memberships.invited_by -> users.id`
- `teams.created_by -> users.id`
- `teams.managed_by -> users.id`
- `team_memberships.user_id -> users.id`
- `audit_logs.actor_id -> users.id`

MVP 목표 상태 결정:

- `users`에 `tenant_id`를 추가하지 않는다.
- 사용자 상태는 현재 코드의 `deactivated_at`, `last_login_at`를 사용한다.

### `organization`

현재 코드 기준 table이다.

역할:

- 조직 범위
- tenant-like boundary
- app/workflow/knowledge base/LLM credential/usage log의 상위 scope
- team permission의 조직 기준

주요 column:

- `id`
- `name`
- `options`
- `flags`
- `created_by`
- `managed_by`
- `is_active`
- `created_at`
- `updated_at`
- `deactivated_at`

관계:

- `organization.created_by -> users.id`
- `organization.managed_by -> users.id`
- `apps.organization_id -> organization.id`
- `workflows.organization_id -> organization.id`
- `knowledge_bases.organization_id -> organization.id`
- `llm_credentials.organization_id -> organization.id`
- `llm_usage_logs.organization_id -> organization.id`
- `teams.organization_id -> organization.id`
- `organization_memberships.organization_id -> organization.id`
- `team_memberships.grantee_organization_id -> organization.id`
- `team_*_permissions.grantee_organization_id -> organization.id`
- `team_audit_permissions.target_organization_id -> organization.id`

MVP 목표 상태 결정:

- `organization`을 제거하거나 `tenant_id`로 되돌리지 않는다.
- 조직별 설정이 필요하면 현재 코드의 `options`, `flags`를 우선 사용한다.

### `teams`

현재 코드 기준 table이다.

역할:

- 조직 내 권한 부여 단위
- 사용자 그룹
- workflow, knowledge base, LLM credential, audit visibility 권한의 subject

주요 column:

- `id`
- `organization_id`
- `name`
- `options`
- `flags`
- `description`
- `created_by`
- `managed_by`
- `is_active`
- `is_auto_add`
- `created_at`
- `updated_at`
- `deactivated_at`

관계:

- `teams.organization_id -> organization.id`
- `teams.created_by -> users.id`
- `teams.managed_by -> users.id`
- `team_memberships.team_id -> teams.id`
- `team_workflow_permissions.team_id -> teams.id`
- `team_knowledge_permissions.team_id -> teams.id`
- `team_llm_permissions.team_id -> teams.id`
- `team_audit_permissions.team_id -> teams.id`

MVP 목표 상태 결정:

- 별도 `roles` catalog를 만들지 않는다.
- Admin/Builder/Operator/Viewer 같은 제품 역할은 필요하면 team naming, `options`, `flags`, application-level policy로 표현한다.

### `team_memberships`

현재 코드 기준 table이다.

역할:

- 사용자를 팀에 소속시킨다.
- 현재 코드 기준 team permission 계산의 team 배정 원천이다.
- 과거/legacy backfill에서는 organization 소속을 유도하는 입력으로 사용됐지만, 현재 organization 소속과 manager/member 판정의 기준은 `organization_memberships`다.

주요 column:

- `id`
- `grantee_organization_id`
- `team_id`
- `user_id`
- `assigned_by`
- `assigned_at`
- `options`
- `flags`

관계:

- `team_memberships.grantee_organization_id -> organization.id`
- `team_memberships.team_id -> teams.id`
- `team_memberships.user_id -> users.id`
- `team_memberships.assigned_by -> users.id`

제약:

- 현재 코드 기준 unique constraint는 조직, 사용자, 팀 조합의 중복 소속을 막는다.

### `organization_memberships`

MBA-66에서 추가된 MVP 2-0 foundation table이다. MBA-67 이후 permission helper와 일부 API endpoint는 이 table을 organization scope와 manager 판정의 우선 기준으로 사용한다. Organization member/invitation BE API는 구현됐고, full membership 관리 UI는 아직 후속 범위다.

역할:

- user와 organization의 직접 소속 관계를 표현한다.
- active organization 목록 조회의 기준이다.
- organization manager 권한의 기준이다.
- team membership, user direct permission, MVP 2 knowledge permission의 전제 조건이다.

주요 column:

- `id`
- `organization_id`
- `user_id`
- `membership_state`
- `organization_auth_state`
- `invited_by`
- `invited_at`
- `accepted_at`
- `removed_at`
- `created_at`
- `updated_at`
- `options`
- `flags`

관계:

- `organization_memberships.organization_id -> organization.id`
- `organization_memberships.user_id -> users.id`
- `organization_memberships.invited_by -> users.id`

제약:

- `(organization_id, user_id)`는 unique여야 한다.
- `membership_state`는 `invited`, `active`, `suspended`, `removed`를 사용한다.
- `organization_auth_state`는 `member`, `manager`를 사용하며 resource `auth_state`와 섞지 않는다.
- resource 접근은 active organization membership만으로 허용하지 않고, organization manager override 또는 resource permission을 함께 평가한다.
- MBA-66 schema migration은 `ix_organization_memberships_organization_id`, `ix_organization_memberships_user_id`, `ix_organization_memberships_membership_state`, `ix_organization_memberships_org_state`, `ix_organization_memberships_user_state`를 만든다.
- `(organization_id, membership_state, organization_auth_state)` index는 MBA-66 범위에서 만들지 않고 MBA-67 helper query 확정 후 검토한다.

### `team_workflow_permissions`

현재 코드 기준 table이다.

역할:

- 팀에 workflow resource 권한을 부여한다.

주요 column:

- `id`
- `grantee_organization_id`
- `team_id`
- `workflow_id`
- `auth_state`
- `assigned_by`
- `assigned_at`
- `options`
- `flags`

관계:

- `team_workflow_permissions.workflow_id -> workflows.id`
- `team_workflow_permissions.team_id -> teams.id`
- `team_workflow_permissions.grantee_organization_id -> organization.id`
- `team_workflow_permissions.assigned_by -> users.id`

MVP 목표 상태 결정:

- workflow 권한은 `resource_permissions.resource_type='workflow'`로 표현하지 않는다.
- 현재 코드의 `auth_state`와 application-level policy를 사용한다.
- `auth_state` 값 집합은 DB enum으로 고정하지 않는다. MVP 목표 상태의 application-level 표준값과 의미는 `data-model/rbac-permission-policy.md`를 따른다.
- 현재 team resource permission tables의 `auth_state`는 문자열 column이며 DB check constraint로 값 집합을 제한하지 않는다. canonical 값 제한은 API/service validation과 application-level normalization에서 수행한다.

### `team_knowledge_permissions`

현재 코드 기준 table이다.

역할:

- 팀에 knowledge base resource 권한을 부여한다.

주요 column:

- `id`
- `grantee_organization_id`
- `team_id`
- `knowledge_base_id`
- `auth_state`
- `assigned_by`
- `assigned_at`
- `options`
- `flags`

관계:

- `team_knowledge_permissions.knowledge_base_id -> knowledge_bases.id`
- `team_knowledge_permissions.team_id -> teams.id`
- `team_knowledge_permissions.grantee_organization_id -> organization.id`
- `team_knowledge_permissions.assigned_by -> users.id`

MVP 목표 상태 결정:

- RAG data source permission은 이 테이블을 기준으로 판정한다.
- `auth_state` 값 집합과 의미는 `data-model/rbac-permission-policy.md`의 application-level matrix를 따른다.

### `team_llm_permissions`

현재 코드 기준 table이다.

역할:

- 팀에 LLM credential 권한을 부여한다.

주요 column:

- `id`
- `grantee_organization_id`
- `team_id`
- `llm_credential_id`
- `auth_state`
- `assigned_by`
- `assigned_at`
- `options`
- `flags`

관계:

- `team_llm_permissions.llm_credential_id -> llm_credentials.id`
- `team_llm_permissions.team_id -> teams.id`
- `team_llm_permissions.grantee_organization_id -> organization.id`
- `team_llm_permissions.assigned_by -> users.id`

MVP 목표 상태 결정:

- LLM credential 사용 권한은 이 테이블을 기준으로 판정한다.
- model catalog 권한이 별도로 필요하면 새 테이블을 만들지 않고 credential-model 관계와 application-level policy로 제한한다.
- `auth_state` 값 집합과 의미는 `data-model/rbac-permission-policy.md`의 application-level matrix를 따른다.

### `team_audit_permissions`

현재 코드 기준 table이다.

역할:

- 팀에 특정 조직의 audit visibility 권한을 부여한다.

주요 column:

- `id`
- `grantee_organization_id`
- `team_id`
- `target_organization_id`
- `auth_state`
- `assigned_by`
- `assigned_at`
- `options`
- `flags`

관계:

- `team_audit_permissions.target_organization_id -> organization.id`
- `team_audit_permissions.team_id -> teams.id`
- `team_audit_permissions.grantee_organization_id -> organization.id`
- `team_audit_permissions.assigned_by -> users.id`

MVP 목표 상태 결정:

- audit log 조회 권한은 이 테이블을 기준으로 판정한다.
- `auth_state` 값 집합과 의미는 `data-model/rbac-permission-policy.md`의 application-level matrix를 따른다.

### `user_workflow_permissions`

현재 코드에 구현된 additive table이다.

역할:

- 특정 user에게 workflow 직접 추가 권한을 부여한다.
- team 권한으로 처리하기 어려운 예외 권한을 저장한다.
- team permission을 낮추거나 deny하지 않고 additive allow로만 동작한다.

주요 column:

- `id`
- `grantee_organization_id`
- `user_id`
- `workflow_id`
- `auth_state`
- `assigned_by`
- `assigned_at`
- `options`
- `flags`

관계:

- `user_workflow_permissions.grantee_organization_id -> organization.id`
- `user_workflow_permissions.user_id -> users.id`
- `user_workflow_permissions.workflow_id -> workflows.id`
- `user_workflow_permissions.assigned_by -> users.id`

제약:

- `(grantee_organization_id, user_id, workflow_id)`는 unique여야 한다.
- 현재 코드에는 `auth_state IN ('none', 'viewer', 'operator', 'builder', 'manager')` check constraint가 있다.
- `auth_state='none'`은 직접 권한 없음과 동일하게 처리한다.
- direct user `auth_state`가 team `auth_state`보다 약해도 team 권한을 낮추지 않는다.

### `user_knowledge_permissions`

MVP 2 목표 table이다. 현재 코드에는 아직 구현되어 있지 않다.

역할:

- 특정 user에게 knowledge base 직접 추가 권한을 부여한다.
- RAG data source 접근의 예외 허용을 저장한다.
- team permission을 낮추거나 deny하지 않고 additive allow로만 동작한다.

주요 column:

- `id`
- `grantee_organization_id`
- `user_id`
- `knowledge_base_id`
- `auth_state`
- `assigned_by`
- `assigned_at`
- `options`
- `flags`

관계:

- `user_knowledge_permissions.grantee_organization_id -> organization.id`
- `user_knowledge_permissions.user_id -> users.id`
- `user_knowledge_permissions.knowledge_base_id -> knowledge_bases.id`
- `user_knowledge_permissions.assigned_by -> users.id`

제약:

- `(grantee_organization_id, user_id, knowledge_base_id)`는 unique여야 한다.
- document별 직접 권한은 만들지 않는다.
- document 접근 예외가 필요하면 knowledge base 단위로 부여한다.

### `user_llm_permissions`

현재 코드에 구현된 additive table이다.

역할:

- 특정 user에게 LLM credential 직접 추가 권한을 부여한다.
- workflow 실행 또는 workflow 설정에서 credential 사용 예외를 허용한다.
- team permission을 낮추거나 deny하지 않고 additive allow로만 동작한다.

주요 column:

- `id`
- `grantee_organization_id`
- `user_id`
- `llm_credential_id`
- `auth_state`
- `assigned_by`
- `assigned_at`
- `options`
- `flags`

관계:

- `user_llm_permissions.grantee_organization_id -> organization.id`
- `user_llm_permissions.user_id -> users.id`
- `user_llm_permissions.llm_credential_id -> llm_credentials.id`
- `user_llm_permissions.assigned_by -> users.id`

제약:

- `(grantee_organization_id, user_id, llm_credential_id)`는 unique여야 한다.
- 현재 코드에는 `auth_state IN ('none', 'viewer', 'operator', 'builder', 'manager')` check constraint가 있다.
- model별 직접 권한은 만들지 않는다.
- model 사용 제한은 credential 권한과 `llm_rel_credential_models`를 함께 평가한다.

### `user_audit_permissions`

MVP 3 목표 table이다. 현재 코드에는 아직 구현되어 있지 않다.

역할:

- 특정 user에게 target organization audit visibility 직접 추가 권한을 부여한다.
- 감사 조회 또는 raw trace 조회 예외를 저장한다.
- team permission을 낮추거나 deny하지 않고 additive allow로만 동작한다.

주요 column:

- `id`
- `grantee_organization_id`
- `user_id`
- `target_organization_id`
- `auth_state`
- `assigned_by`
- `assigned_at`
- `options`
- `flags`

관계:

- `user_audit_permissions.grantee_organization_id -> organization.id`
- `user_audit_permissions.user_id -> users.id`
- `user_audit_permissions.target_organization_id -> organization.id`
- `user_audit_permissions.assigned_by -> users.id`

제약:

- `(grantee_organization_id, user_id, target_organization_id)`는 unique여야 한다.
- raw trace 조회는 이 테이블만으로 허용하지 않는다.
- raw trace 조회는 `trace_visibility_policies`와 함께 평가한다.

### `apps`

현재 코드 기준 table이다.

역할:

- MVP 목표 상태의 project boundary
- public/API endpoint boundary
- active deployment pointer 보유

주요 column:

- `id`
- `organization_id`
- `name`
- `description`
- `icon`
- `workflow_id`
- `active_deployment_id`
- `url_slug`
- `auth_secret`
- `is_api_enabled`
- `api_req_per_minute`
- `api_req_per_hour`
- `is_market`
- `forked_from`
- `created_by`
- `created_at`
- `updated_at`

관계:

- `apps.organization_id -> organization.id`
- `apps.created_by -> users.id`
- `apps.workflow_id -> workflows.id`
- `workflows.app_id -> apps.id`
- `workflow_deployments.app_id -> apps.id`
- `workflow_runs.app_id -> apps.id`

주의:

- `active_deployment_id`는 현재 코드 기준 FK column이 아니다.
- relationship은 `WorkflowDeployment.id`를 viewonly로 참조한다.

MVP 목표 상태 결정:

- 독립 `projects` table을 만들지 않는다.
- `apps`는 project boundary로 유지한다.
- 상위 조직 범위가 필요할 때는 `apps.organization_id`를 사용한다.

### `workflows`

현재 코드 기준 table이다.

역할:

- Canvas
- workflow draft graph 저장
- 실행/배포/check/recommendation의 중심 resource

주요 column:

- `id`
- `organization_id`
- `app_id`
- `graph`
- `features`
- `env_variables`
- `runtime_variables`
- `created_by`
- `created_at`
- `updated_by`
- `updated_at`

관계:

- `workflows.organization_id -> organization.id`
- `workflows.app_id -> apps.id`
- `workflows.created_by -> users.id`
- `workflows.updated_by -> users.id`
- `workflow_runs.workflow_id -> workflows.id`
- `llm_usage_logs.workflow_id -> workflows.id`
- `team_workflow_permissions.workflow_id -> workflows.id`

MVP 목표 상태 결정:

- workflow node를 별도 테이블로 정규화하지 않는다.
- graph 내부 node id는 `workflow_node_runs.node_id`, `llm_usage_logs.node_id`, trace payload metadata에서 string으로 참조한다.

### `workflow_deployments`

현재 코드 기준 table이다.

역할:

- 배포 snapshot 저장
- app 기준 version 관리
- schedule/webhook/API/webapp 실행 기준

주요 column:

- `id`
- `app_id`
- `version`
- `type`
- `graph_snapshot`
- `config`
- `input_schema`
- `output_schema`
- `description`
- `created_by`
- `created_at`
- `is_active`

관계:

- `workflow_deployments.app_id -> apps.id`
- `workflow_deployments.created_by -> users.id`
- `schedules.deployment_id -> workflow_deployments.id`
- `workflow_runs.deployment_id -> workflow_deployments.id`

MVP 목표 상태 결정:

- rollback 전용 테이블은 만들지 않는다.
- 이전 배포를 다시 활성화하는 동작은 `audit_logs.action='deployment.activate_previous'` 같은 action으로 기록한다.
- deployment checklist 전용 테이블은 만들지 않는다. checklist 결과 저장이 필요하면 `audit_logs.audit_metadata`에 summary/items를 저장한다.

### `schedules`

현재 코드 기준 table이다.

역할:

- schedule deployment 실행 설정
- APScheduler 등록 정보

주요 column:

- `id`
- `deployment_id`
- `node_id`
- `cron_expression`
- `timezone`
- `last_run_at`
- `next_run_at`
- `created_at`
- `updated_at`

관계:

- `schedules.deployment_id -> workflow_deployments.id`

MVP 목표 상태 결정:

- 별도 변경 없음.
- scheduler trigger mode 정합성은 schema 변경이 아니라 실행 코드에서 보정한다.

### `workflow_runs`

현재 코드 기준 table이다.

역할:

- workflow 실행 단위 observability
- dashboard raw query의 핵심 원천
- trace, LLM usage, audit metadata 연결 기준

주요 column:

- `id`
- `workflow_id`
- `user_id`
- `app_id`
- `deployment_id`
- `workflow_version`
- `status`
- `trigger_mode`
- `inputs`
- `outputs`
- `error_message`
- `started_at`
- `finished_at`
- `duration`
- `meta_info`
- `correlation_id`
- `request_id`
- `workflow_task_id`
- `trace_metadata`
- `redaction_applied`
- `pii_detected`
- `redaction_policy_id`
- `retention_policy_id`
- `visibility_policy_id`
- `payload_storage_mode`
- `retention_purged_at`
- `total_tokens`
- `total_cost`

관계:

- `workflow_runs.workflow_id -> workflows.id`
- `workflow_runs.user_id -> users.id`
- `workflow_runs.app_id -> apps.id`
- `workflow_runs.deployment_id -> workflow_deployments.id`
- `workflow_node_runs.workflow_run_id -> workflow_runs.id`
- `llm_usage_logs.workflow_run_id -> workflow_runs.id`
- `trace_payloads.workflow_run_id -> workflow_runs.id`
- `trace_payload_access_events.workflow_run_id -> workflow_runs.id`

MVP 목표 상태 결정:

- RAG retrieval lineage를 별도 table로 만들지 않고, run/node와 trace payload를 연결해서 표현한다.
- 물리 column/enum에는 `manual`/`api`/`webhook`/`scheduler`/`app` trigger mode가 있지만, 현재 실행 로그 정규화 경로는 배포 App/API 중심으로 기록될 수 있다. API/Webhook/Scheduler/App 구분 보존은 MVP 3의 trigger mode normalization 대상이다.

### `workflow_node_runs`

현재 코드 기준 table이다.

역할:

- node 실행 이력
- node 단위 latency/status/error 관측
- trace payload 연결 기준

주요 column:

- `id`
- `workflow_run_id`
- `node_id`
- `node_type`
- `status`
- `inputs`
- `process_data`
- `outputs`
- `error_message`
- `started_at`
- `finished_at`
- `duration`
- `trace_metadata`
- `redaction_applied`
- `pii_detected`
- `redaction_policy_id`
- `parent_node_run_id`
- `sequence`
- `retry_count`

관계:

- `workflow_node_runs.workflow_run_id -> workflow_runs.id`
- `workflow_node_runs.parent_node_run_id -> workflow_node_runs.id`
- `trace_payloads.workflow_node_run_id -> workflow_node_runs.id`

MVP 목표 상태 결정:

- 별도 workflow node definition table은 만들지 않는다.
- node id는 `workflows.graph` 내부 id를 string으로 참조한다.

### `trace_redaction_policies`

현재 코드 기준 table이다.

역할:

- trace payload redaction 정책
- raw payload 저장 여부와 prompt/completion 저장 여부 제어

주요 column:

- `id`
- `scope_type`
- `scope_id`
- `redaction_enabled`
- `raw_payload_storage_enabled`
- `prompt_completion_storage_enabled`
- `pii_detection_enabled`
- `store_redacted_copy_only`
- `sensitive_headers`
- `sensitive_json_paths`
- `sensitive_keywords`
- `regex_rules`
- `replacement`
- `is_active`
- `updated_by`
- `created_at`
- `updated_at`

MVP 목표 상태 결정:

- prompt/input/output 원문 저장 정책은 이 테이블을 기준으로 한다.
- 별도 `audit_events.policy_result` column을 만들지 않는다.

### `trace_retention_policies`

현재 코드 기준 table이다.

역할:

- trace metadata와 payload 보관 기간 정책

주요 column:

- `id`
- `scope_type`
- `scope_id`
- `metadata_retention_days`
- `raw_payload_retention_days`
- `redacted_payload_retention_days`
- `prompt_completion_retention_days`
- `failed_trace_retention_days`
- `retention_action`
- `is_active`
- `updated_by`
- `created_at`
- `updated_at`

MVP 목표 상태 결정:

- trace payload 보관/삭제는 이 테이블을 기준으로 한다.

### `trace_visibility_policies`

현재 코드 기준 table이다.

역할:

- owner/admin이 어떤 trace payload view level까지 볼 수 있는지 제어한다.

주요 column:

- `id`
- `scope_type`
- `scope_id`
- `owner_trace_access_enabled`
- `owner_redacted_payload_access_enabled`
- `owner_raw_payload_access_enabled`
- `owner_prompt_completion_access_enabled`
- `admin_raw_payload_access_enabled`
- `admin_prompt_completion_access_enabled`
- `deny_owner_trace_access`
- `default_view_level`
- `is_active`
- `updated_by`
- `created_at`
- `updated_at`

MVP 목표 상태 결정:

- 현재 trace 상세/payload 접근은 system admin, app owner, workflow effective RBAC와 이 visibility policy를 함께 판정한다.
- 목표 organization-wide audit search/view_raw는 audit permission model과 visibility policy를 통합한다.

### `trace_payloads`

현재 코드 기준 table이다.

역할:

- workflow run 또는 node run에 연결되는 trace payload 저장
- redacted payload와 encrypted raw payload 저장
- RAG retrieval, LLM prompt/completion, tool call payload를 schema 변경 없이 수용할 수 있는 저장소

주요 column:

- `id`
- `workflow_run_id`
- `workflow_node_run_id`
- `scope`
- `payload_kind`
- `sequence`
- `attempt`
- `redacted_payload`
- `raw_payload_encrypted`
- `redaction_applied`
- `pii_detected`
- `secret_detected`
- `redaction_metadata`
- `storage_mode`
- `retention_expires_at`
- `retention_purged_at`
- `created_at`

관계:

- `trace_payloads.workflow_run_id -> workflow_runs.id`
- `trace_payloads.workflow_node_run_id -> workflow_node_runs.id`

MVP 목표 상태 결정:

- `rag_retrieval_traces` table을 만들지 않는다.
- RAG retrieval 결과를 저장해야 할 때는 `payload_kind`와 `redacted_payload`/`redaction_metadata`에 retrieval 결과를 저장한다.
- 이 문서는 구체적인 `payload_kind` 문자열을 DB enum으로 확정하지 않는다. application-level convention으로 관리한다.

### `trace_payload_access_events`

현재 코드 기준 table이다.

역할:

- trace raw payload 조회 시도 기록
- `view=raw` 접근 감사. prompt/completion payload도 raw view에서 접근될 때 기록된다. redacted/metadata 조회는 현재 이 테이블에 기록하지 않는다.

주요 column:

- `id`
- `payload_id`
- `workflow_run_id`
- `actor_user_id`
- `actor_user_ref`
- `view_level`
- `allowed`
- `reason_code`
- `created_at`

관계:

- `trace_payload_access_events.payload_id -> trace_payloads.id`
- `trace_payload_access_events.workflow_run_id -> workflow_runs.id`
- `trace_payload_access_events.actor_user_id -> users.id`

MVP 목표 상태 결정:

- trace payload 접근 감사는 `audit_logs`와 별개로 이 테이블을 사용한다.

### `audit_logs`

현재 코드 기준 table이다.

역할:

- 사용자 action 감사
- data change 감사
- 권한 변경, 배포 활성화, checklist 실행, recommendation lifecycle 기록

주요 column:

- `id`
- `occurred_at`
- `actor_id`
- `actor_type`
- `category`
- `action`
- `target_type`
- `target_id`
- `before`
- `after`
- `status`
- `audit_metadata`

관계:

- `audit_logs.actor_id -> users.id`

MVP 목표 상태 결정:

- `audit_events`를 만들지 않는다.
- canonical action은 `audit_logs.action`에 저장한다.
- 정책 결과, request id, ip, user agent, role/team snapshot, checklist result, recommendation context는 `audit_metadata`에 저장한다.
- `before`/`after`는 data change 감사에 사용한다.
- `audit_logs.status`에는 현재 코드 enum인 `success` 또는 `failure`만 저장한다.
- `warn`, `block`, `pass` 같은 정책/검사 결과는 `audit_metadata.policy_result`에 저장한다.

대표 action convention:

현재 코드에 구현된 action과 MVP 2/3 목표 action을 함께 나열한다. 아직 `AuditAction` 상수에 없는 목표 action은 해당 기능 구현 시 상수와 테스트를 추가한다.

| 기능 | action 예시 |
| --- | --- |
| team workflow 권한 생성/수정/삭제 | `team_workflow_permission.created`, `team_workflow_permission.updated`, `team_workflow_permission.deleted` |
| user workflow 권한 생성/수정/삭제 | `user_workflow_permission.created`, `user_workflow_permission.updated`, `user_workflow_permission.deleted` |
| team LLM credential 권한 생성/수정/삭제 | `team_llm_permission.created`, `team_llm_permission.updated`, `team_llm_permission.deleted` |
| user LLM credential 권한 생성/수정/삭제 | `user_llm_permission.created`, `user_llm_permission.updated`, `user_llm_permission.deleted` |
| 현재 ORM data-change listener가 기록할 수 있는 team knowledge base 권한 생성/수정/삭제 | `team_knowledge_permission.created`, `team_knowledge_permission.updated`, `team_knowledge_permission.deleted` |
| MVP 2 user knowledge permission table/API 구현 시 고정할 user knowledge base 권한 생성/수정/삭제 | `user_knowledge_permission.created`, `user_knowledge_permission.updated`, `user_knowledge_permission.deleted` |
| organization member 초대 생성 | `organization.invite` |
| organization member 초대 수락 | `organization.member.accept` |
| organization member 상태 또는 organization auth_state 변경 | `organization.member.update` |
| organization member 제거 | `organization.member.remove` |
| 권한 부족 거부 | `permission.denied` |
| 인증 전 또는 전역 401/403 거부 | `auth.permission_denied` |
| workflow 실행 | `workflow.execute` |
| LLM 호출 | `llm.call` |
| 현재 MBA-78 1차: RAG retrieval 성공 | `rag.retrieve` |
| 목표: RAG Agent answer 요청 | `rag.answer.requested` |
| 목표: RAG Agent answer 완료 | `rag.answer.completed` |
| 목표: RAG Agent answer 실패 | `rag.answer.failed` |
| 목표: RAG Agent answer 취소 | `rag.answer.cancelled` |
| 목표: RAG Agent answer retention purge | `rag.answer.purge` |
| 배포 생성 | `workflow.deploy` |
| 배포 일반 toggle | `deployment.toggle` |
| 이전 배포 활성화 | `deployment.activate_previous` |
| 배포 삭제 | `deployment.delete` |
| 목표: 정책 차단 | `policy.block` |
| 목표: 정책 경고 | `policy.warn` |
| 목표: 배포 check 실행 | `deployment.check` |
| 목표: 추천 생성 | `recommendation.created` |
| 목표: 추천 적용 | `recommendation.applied` |
| 목표: 추천 무시 | `recommendation.ignored` |

주의:

- 위 action 값은 DB enum이 아니다.
- action 문자열 표준화는 application code와 테스트로 관리한다.

### `knowledge_bases`

현재 코드 기준 table이다.

역할:

- RAG data source 상위 단위
- team permission 대상
- retrieval trace payload의 대상 리소스

주요 column:

- `id`
- `organization_id`
- `name`
- `description`
- `embedding_model`
- `top_k`
- `similarity_threshold`
- `user_id`
- `created_at`
- `updated_at`

관계:

- `knowledge_bases.organization_id -> organization.id`
- `knowledge_bases.user_id -> users.id`
- `documents.knowledge_base_id -> knowledge_bases.id`
- `document_chunks.knowledge_base_id -> knowledge_bases.id`
- `team_knowledge_permissions.knowledge_base_id -> knowledge_bases.id`

MVP 목표 상태 결정:

- `classification` column을 추가하지 않는다.
- classification 기능은 `documents.meta_info`나 trace/audit metadata convention으로 처리한다. `knowledge_bases.classification` column 기반 필터링이 필요하면 별도 schema 변경으로 분리한다.
- metadata는 permission source of truth가 아니다. Active organization membership은 KB organization scope와 permission subject의 전제 조건이고, 이 membership만으로 KB `read`/`use`를 허용하지 않는다. Knowledge base resource 허용은 organization manager override와 `team_knowledge_permissions`, 목표 `user_knowledge_permissions`의 effective permission으로 판정한다.

### `documents`

현재 코드 기준 table이다.

역할:

- RAG 문서 단위
- re-index 상태 표시의 후보 위치

주요 column:

- `id`
- `knowledge_base_id`
- `filename`
- `file_path`
- `source_type`
- `content_hash`
- `status`
- `error_message`
- `chunk_size`
- `chunk_overlap`
- `meta_info`
- `embedding_model`
- `created_at`
- `updated_at`

관계:

- `documents.knowledge_base_id -> knowledge_bases.id`
- `document_chunks.document_id -> documents.id`

MVP 목표 상태 결정:

- `classification` column을 추가하지 않는다.
- classification 값은 `documents.meta_info.classification` metadata convention으로 저장할 수 있다.
- `classification` 허용값은 MVP 2 기준 `public`, `internal`, `confidential`, `pii`다. 누락 시 application layer에서 `internal`로 해석한다.
- MBA-78 1차 retrieval filter key는 `classification`, `tags`, `source_type`, `effective_from`, `effective_to` convention에 한정한다. `source_hash`, `document_version`, `metadata_version` 같은 metadata key는 citation evidence 또는 후속 filter 확장 후보로 사용할 수 있다.
- `effective_from`/`effective_to`는 `documents.meta_info`에 UTC ISO 문자열(`YYYY-MM-DDTHH:MM:SS+00:00`)로 정규화해 저장한다. 비어 있거나 누락된 값은 열린 구간으로 해석하고, 값이 있지만 형식이 다르면 retrieval filter에서 match하지 않는다. `document_chunks.metadata`에 복제된 값은 denormalized cache/citation evidence이며, MBA-78 1차 filter source로 보지 않는다.
- `needs_reindex` 같은 상태가 필요하면 현재 코드의 `meta_info`에 application-level metadata로 저장한다.
- MBA-85 hierarchical ingestion provenance는 `documents.meta_info.chunking_mode`, `hierarchy_version`, `hierarchy_parent_target_size`, `hierarchy_parent_overlap`, `hierarchy_child_size`, `hierarchy_child_overlap`, `chunking_fingerprint_hash` 같은 application-level metadata로 저장할 수 있다.
- `chunking_fingerprint_hash`는 `content_hash` 기반 재처리 skip이 chunking 설정 변경을 놓치지 않도록 사용하는 application-level fingerprint다. 입력에는 `chunking_mode`, `hierarchy_version`, `chunk_size`, `chunk_overlap`, `segment_identifier`, preprocess flag, selection setting 같은 redaction-safe 설정만 포함하고 secret, header, credential, raw content는 포함하지 않는다.
- Hierarchy provenance metadata는 권한 source가 아니며, retrieval filter의 canonical source도 아니다. 권한은 KB organization scope와 effective permission으로 판단하고, retrieval filter는 문서 metadata allowlist 계약을 따른다.
- re-index 때문에 `status` enum/table을 새로 만들지 않는다.

### `document_chunks`

현재 코드 기준 table이다.

역할:

- RAG retrieval 최소 검색 단위
- embedding vector 저장

주요 column:

- `id`
- `document_id`
- `knowledge_base_id`
- `content`
- `embedding`
- `chunk_index`
- `parent_chunk_id`
- `chunk_level`
- `section_path`
- `heading`
- `token_count`
- `metadata`

관계:

- `document_chunks.document_id -> documents.id`
- `document_chunks.knowledge_base_id -> knowledge_bases.id`

MVP 목표 상태 결정:

- chunk-level incremental indexing table은 만들지 않는다.
- retrieval 결과의 chunk reference는 trace payload 내부 metadata로 저장한다.
- `document_chunks.metadata`는 retrieval/filter/citation 성능을 위한 denormalized cache다. `documents.meta_info`와 충돌하면 document metadata를 우선한다.
- MBA-78 1차 구현은 nullable `parent_chunk_id`, `chunk_level`, `section_path`, `heading` column을 추가한다. 기존 row의 `chunk_level IS NULL`은 application layer에서 `flat`으로 해석한다.
- `parent_chunk_id`, `chunk_level`, `section_path`, `heading`은 hierarchy의 canonical field다. 같은 값을 JSON metadata에 중복 저장하지 않는다.
- Hierarchical retrieval index는 1차로 `(knowledge_base_id, chunk_level)`, `(parent_chunk_id)`를 추가한다. `(document_id, chunk_index)`와 JSONB GIN index는 실제 retrieval/query pattern이 확정된 뒤 추가 여부를 판단한다.

`chunk_level` 역할:

| 값 | 의미 | 최종 evidence 반환 |
| --- | --- | --- |
| `parent` | Coarse retrieval/routing chunk. LLM summary가 아니라 원문 기반 큰 routing chunk다. | 기본 반환하지 않음 |
| `child` | Parent 아래 final citation/evidence chunk | 반환 가능 |
| `flat` | 기존 flat chunk 또는 hierarchy 미적용 chunk | 반환 가능 |
| `NULL` | Legacy row | application layer에서 `flat`으로 해석 |

MBA-85 2단계는 parent/child row를 같은 `document_chunks` table에 저장한다. Parent/child 모두 embedding을 가질 수 있지만, parent는 후보 routing에 사용하고 final response/trace evidence는 child 또는 legacy/flat chunk 기준으로 둔다. `parent`, `child`, `flat`, `NULL` 외 `chunk_level` 값은 application layer에서 evidence 후보로 사용하지 않고 warning/metric 대상으로 둔다.

### `connections`

현재 코드 기준 table이다.

역할:

- 외부 DB data source

주요 column:

- `id`
- `user_id`
- `name`
- `description`
- `type`
- `host`
- `port`
- `database`
- `username`
- `encrypted_password`
- `use_ssh`
- `ssh_host`
- `ssh_port`
- `ssh_username`
- `ssh_auth_type`
- `encrypted_ssh_password`
- `encrypted_ssh_private_key`

관계:

- `connections.user_id -> users.id`

MVP 목표 상태 결정:

- 현재 물리 데이터 모델에는 connection 전용 team permission table이 없다.
- MVP 목표 상태에서는 connection 자체를 독립 permission resource로 만들지 않는다.
- connection `secret/manage` 권한은 `connections.user_id` owner 또는 organization owner/manager로 제한한다.
- `connections` 자체에는 `organization_id`가 없으므로, 직접 connection CRUD API는 `connections.user_id` owner를 기본 기준으로 삼는다. organization owner/manager 판정은 connection이 active organization의 workflow/knowledge base에 연결되어 scope가 식별되는 경우에 적용한다.
- connection `use` 권한은 connection을 직접 기준으로 판정하지 않고, connection을 소비하는 workflow 또는 knowledge base 권한으로 판정한다.
- 연결된 workflow/knowledge base가 없거나 active organization scope를 단일하게 식별할 수 없으면 organization owner/manager override를 적용하지 않고 deny한다.
- 하나의 connection이 서로 다른 organization의 resource와 충돌하는 방식으로 연결되면 implicit sharing으로 해석하지 않고 deny한다. Cross-organization sharing이 필요하면 별도 schema/permission extension 승인이 필요하다.
- workflow/knowledge base 실행 중 connection credential은 사용자에게 노출하지 않고 server-side runtime에서만 사용한다.
- 연결된 외부 DB 내부의 table/row 권한은 Nodease RBAC에서 대신 관리하지 않는다. 외부 DB credential 자체의 권한 범위가 최종 DB 접근 범위를 제한한다.
- connection을 workflow/knowledge base와 독립적으로 team/user에게 공유해야 하는 요구가 생기면 별도 schema extension 승인이 필요하다.

### `llm_providers`

현재 코드 기준 table이다.

역할:

- LLM provider catalog

주요 column:

- `id`
- `name`
- `description`
- `type`
- `base_url`
- `auth_type`
- `doc_url`
- `created_at`
- `updated_at`

관계:

- `llm_models.provider_id -> llm_providers.id`
- `llm_credentials.provider_id -> llm_providers.id`

### `llm_models`

현재 코드 기준 table이다.

역할:

- LLM model catalog
- cost 계산 기준
- recommendation 근거

주요 column:

- `id`
- `provider_id`
- `model_id_for_api_call`
- `name`
- `type`
- `context_window`
- `input_price_1k`
- `output_price_1k`
- `is_active`
- `metadata`
- `created_at`
- `updated_at`

관계:

- `llm_models.provider_id -> llm_providers.id`
- `llm_rel_credential_models.model_id -> llm_models.id`
- `llm_usage_logs.model_id -> llm_models.id`

MVP 목표 상태 결정:

- model catalog 자체의 team permission table은 만들지 않는다.
- model 사용 가능 여부는 credential-model 관계, credential permission, application-level validation 조합으로 판정한다.

### `llm_credentials`

현재 코드 기준 table이다.

역할:

- 사용자/조직 LLM credential
- team LLM permission 대상

주요 column:

- `id`
- `provider_id`
- `user_id`
- `organization_id`
- `credential_name`
- `encrypted_config`
- `config_preview`
- `is_valid`
- `quota_type`
- `quota_limit`
- `quota_used`
- `last_used_at`
- `created_at`
- `updated_at`

관계:

- `llm_credentials.provider_id -> llm_providers.id`
- `llm_credentials.user_id -> users.id`
- `llm_credentials.organization_id -> organization.id`
- `llm_rel_credential_models.credential_id -> llm_credentials.id`
- `llm_usage_logs.credential_id -> llm_credentials.id`
- `team_llm_permissions.llm_credential_id -> llm_credentials.id`

MVP 목표 상태 결정:

- `encrypted_config` column은 그대로 둔다.
- 현재 구현은 `encrypted_config`에 `{"apiKey": "...", "baseUrl": "..."}` JSON string을 그대로 저장한다. column 이름과 달리 암호화가 적용되어 있지 않으므로, 실제 암호화는 별도 보안 변경으로 처리해야 한다.
- `tenant_id`를 사용하지 않는다.
- credential 권한은 `team_llm_permissions`를 기준으로 한다.

### `llm_rel_credential_models`

현재 코드 기준 table이다.

역할:

- credential과 model의 사용 가능 관계
- model 목록 표시와 embedding model 목록 조회에 사용

주요 column:

- `id`
- `credential_id`
- `model_id`
- `is_verified`
- `priority`
- `created_at`

관계:

- `llm_rel_credential_models.credential_id -> llm_credentials.id`
- `llm_rel_credential_models.model_id -> llm_models.id`

MVP 목표 상태 결정:

- runtime client 선택은 credential permission과 이 관계를 함께 검증해야 한다.

### `llm_usage_logs`

현재 코드 기준 table이다.

역할:

- LLM token/cost/latency 원천
- operations dashboard raw query 원천
- recommendation 생성 근거

주요 column:

- `id`
- `user_id`
- `organization_id`
- `credential_id`
- `model_id`
- `workflow_id`
- `workflow_run_id`
- `node_id`
- `prompt_tokens`
- `completion_tokens`
- `total_cost`
- `latency_ms`
- `status`
- `error_message`
- `created_at`

관계:

- `llm_usage_logs.user_id -> users.id`
- `llm_usage_logs.organization_id -> organization.id`
- `llm_usage_logs.credential_id -> llm_credentials.id`
- `llm_usage_logs.model_id -> llm_models.id`
- `llm_usage_logs.workflow_id -> workflows.id`
- `llm_usage_logs.workflow_run_id -> workflow_runs.id`

MVP 목표 상태 결정:

- 현재 코드 기준 SQLAlchemy 속성명과 실제 DB column명은 모두 `latency_ms`다.
- 과거 `atency_ms` column은 `f8a9b0c1d2e3_rename_llm_usage_latency_ms.py` migration에서 `latency_ms`로 rename하거나, 누락된 경우 `latency_ms` column을 생성한다.
- 비용/사용량 dashboard는 `workflow_runs`, `workflow_node_runs`, `llm_usage_logs`, `llm_models` raw query로 계산한다.
- 현재 코드 model에는 `credential_id`, `model_id`가 `ondelete='SET NULL'`이지만 nullable은 `False`인 정합성 이슈가 있다. 이 문서는 해당 schema를 수정하지 않고, 별도 migration 판단 대상으로만 남긴다.
- Standalone RAG Agent answer와 usage를 연결해야 할 때 `llm_usage_logs.rag_answer_run_id` 같은 RAG 전용 FK를 추가하지 않는다. Usage 도메인이 generic `correlation_id` 또는 usage metadata extension을 제공하는 별도 설계가 있기 전까지 answer run과 usage log의 강한 FK join을 계약으로 보지 않는다.

## 기능별 데이터 사용 방식

### 권한

권한은 현재 코드의 조직/팀 모델을 기본으로 처리하고, 구현된 개별 user 예외 권한은 workflow/LLM credential의 `user_*_permissions` table로 처리한다. knowledge base와 audit user direct permission은 MVP 2/3 목표 schema다.

| 대상 | 기준 Table |
| --- | --- |
| 사용자 organization 소속 | `organization_memberships`. Membership row가 없는 legacy owner/manager만 제한적으로 `organization.created_by`/`managed_by` fallback |
| 사용자 team 배정 | `team_memberships` |
| workflow team 권한 | `team_workflow_permissions` |
| workflow user 직접 권한 | `user_workflow_permissions` |
| knowledge base team 권한 | `team_knowledge_permissions` |
| knowledge base user 직접 권한 | 목표: `user_knowledge_permissions` |
| LLM credential team 권한 | `team_llm_permissions` |
| LLM credential user 직접 권한 | `user_llm_permissions` |
| audit visibility team 권한 | `team_audit_permissions` |
| audit visibility user 직접 권한 | 목표: `user_audit_permissions` |

권한 판정 순서:

1. 사용자의 active organization을 확인한다. MBA-67 이후 permission helper/API 전환 범위에서는 `organization_memberships` row를 organization 소속의 기본 전제로 확인한다.
2. Active row는 `organization_auth_state`에 따라 member/manager로 판정하고, invited/suspended/removed row는 fail-closed 처리한다.
3. Membership row 자체가 없고 user가 `organization.created_by` 또는 `organization.managed_by`이면 legacy 호환으로 해당 organization scope 안에서 `manager`로 판정한다.
4. 사용자가 속한 team을 `team_memberships`에서 조회한다.
5. resource별 permission table에서 `auth_state`를 확인한다.
6. 현재 구현된 workflow/LLM credential은 user direct permission table에서 해당 user의 `auth_state`를 확인한다. knowledge/audit user direct permission table은 MVP 2/3 목표 schema다.
7. team 권한과 user 직접 권한 중 가장 강한 허용 상태를 적용한다.
8. application-level policy가 필요한 경우 `options`, `flags`, resource 상태를 함께 평가한다.
9. 권한 부여/회수/차단 결과는 `audit_logs`에 기록한다.

제약:

- user direct permission은 additive allow 전용이다.
- user direct permission은 team permission을 deny하거나 낮출 수 없다.
- explicit deny는 MVP 목표 상태 범위에 포함하지 않는다.
- 권한 부여/회수는 organization owner/manager 또는 해당 resource의 effective `manager`가 수행할 수 있다.

### Audit

감사는 `audit_logs`를 사용한다.

| 이전 목표 | 현재 코드 기준 저장 위치 |
| --- | --- |
| canonical action | `audit_logs.action` |
| actor | `audit_logs.actor_id`, `audit_logs.actor_type` |
| target | `audit_logs.target_type`, `audit_logs.target_id` |
| before/after snapshot | `audit_logs.before`, `audit_logs.after` |
| policy result | `audit_logs.audit_metadata.policy_result` |
| request metadata | `audit_logs.audit_metadata` |
| role/team snapshot | `audit_logs.audit_metadata.actor_snapshot` |

### RAG Retrieval Trace

RAG retrieval 전용 table은 만들지 않는다.

저장 기준:

- workflow 실행 단위: `workflow_runs`
- node 실행 단위: `workflow_node_runs`
- retrieval payload: `trace_payloads`
- payload 접근 감사: `trace_payload_access_events`
- 문서/청크 원천: `documents`, `document_chunks`

위 저장 기준은 workflow runtime RAG에 대한 것이다. Standalone RAG Agent answer는 workflow run이 없을 수 있으므로 `trace_payloads`를 실행 anchor로 사용하지 않는다.

`trace_payloads.redacted_payload` 또는 `trace_payloads.redaction_metadata`에는 per-chunk evidence로 다음 정보를 application-level convention으로 저장할 수 있다.

- `payload_kind = "rag.retrieval"`
- `knowledge_base_ids`
- `workflow_run_id`
- `node_id`
- `document_id`
- `chunk_id`
- `parent_chunk_id`
- `rank`
- `score`
- `token_count`
- `metadata_summary`
- `result_count`
- `policy_result`
- `raw_content_returned`

`workflow_node_run_id`는 payload body에 중복 저장하지 않고 `trace_payloads.workflow_node_run_id` 컬럼으로 연결한다.

`audit_logs.action='rag.retrieve'`는 성공한 retrieval 감사 event 이름이다. `payload_kind='rag.retrieval'`은 trace payload 분류값이며 audit action을 대체하지 않는다.

이 구조는 DB FK를 추가하지 않는다. 따라서 RAG lineage의 강한 참조 무결성이 필요하면 현재 물리 데이터 모델 보존 조건 밖의 별도 설계가 필요하다.

Run/node trace metadata allowlist는 `knowledge_base_id`, `retrieved_chunk_count`, `document_ids`, `citation_ids`, score summary, hierarchy fallback flag, `raw_content_returned` 같은 요약 field로 제한한다. `retrieved_chunks` 배열과 raw chunk content는 run/node metadata에 복사하지 않는다. 현재 tracing metadata sanitizer는 RAG summary field와 payload id reference만 허용하며, legacy `retrieval_results` 입력은 저장하지 않고 summary로 변환한다.

RAG trace metadata에는 raw chunk content, raw prompt, credential 원문, API key, token, encrypted_config, secret value, provider raw response를 기본 저장하지 않는다. `credential_id` 같은 식별자는 권한 보호된 trace 응답 whitelist 안에서만 허용할 수 있다. Search-test response는 KB `use` 권한 통과 user에게 chunk content preview를 반환할 수 있지만, workflow trace/run detail 기본 응답은 redaction-safe citation metadata를 반환한다.

### RAG Agent Answer Runs

`rag_answer_runs`는 RAG 확장 3단계/MBA-89에서 도입한 additive table이다. Standalone RAG Agent answer의 실행 anchor 역할을 하며, workflow trace table을 대체하지 않고 workflow가 없는 질문/답변 실행을 RAG 도메인 안에서 추적한다.

역할:

- 사용자 질문에서 retrieval, generation, final answer까지 이어지는 RAG answer 실행 단위
- redaction-safe retrieval summary와 citation summary 저장
- redaction-safe answer summary와 nullable top-level answer hash 저장. Raw final answer 또는 provider raw completion은 기본 저장하지 않는다.
- policy result, answer status, latency/token/cost snapshot 저장
- trace/usage/audit와 느슨하게 연결하기 위한 `correlation_id` 보관

목표 주요 column:

- `id`
- `organization_id`
- `user_id`
- `actor_user_ref`
- `knowledge_base_id`
- `knowledge_base_ref`
- `correlation_id`
- `status`
- `query_hash`
- `retrieval_summary`
- `citation_summary`
- `answer_summary`
- `answer_hash`
- `hash_version`
- `policy_result`
- `generation_model_id`
- `generation_model_snapshot`
- `generation_credential_id`
- `generation_credential_ref`
- `usage_summary`
- `error_code`
- `retention_expires_at`
- `created_at`
- `started_at`
- `completed_at`

목표 관계:

- `rag_answer_runs.organization_id -> organization.id`
- `rag_answer_runs.user_id -> users.id`
- `rag_answer_runs.knowledge_base_id -> knowledge_bases.id`
- `rag_answer_runs.generation_model_id -> llm_models.id`
- `rag_answer_runs.generation_credential_id -> llm_credentials.id`

MVP 목표 상태 결정:

- `rag_answer_runs.organization_id`는 tenant scope 기준이므로 non-null로 유지한다. Organization hard delete가 필요하면 implicit FK cascade로 숨은 삭제를 만들지 말고, tenant data purge 절차에서 answer run과 관련 audit/summary 보존 범위를 명시적으로 처리한다.
- `rag_answer_runs.user_id`는 nullable FK로 두고 user hard delete 시 `SET NULL`을 목표로 한다. 사용자 삭제 뒤에도 retention 기간 동안 운영 추적이 필요하면 `actor_user_ref` 같은 non-secret actor reference만 남기며, raw email/profile snapshot을 기본 저장하지 않는다.
- `rag_answer_runs.knowledge_base_id`는 nullable FK로 두고 KB hard delete 시 `SET NULL`을 목표로 한다. Citation과 retrieval summary는 raw chunk content 없이 `knowledge_base_ref`, document/chunk id, score summary 같은 안전한 snapshot만 남긴다. KB 삭제가 모든 파생 answer summary 삭제를 요구하는 정책으로 바뀌면 별도 retention/purge ADR로 다룬다.
- `rag_answer_runs.generation_model_id`와 `rag_answer_runs.generation_credential_id`는 nullable FK로 두고 model/credential hard delete 시 `SET NULL`을 목표로 한다. 삭제 이후 운영 분석에 필요한 값은 `generation_model_snapshot`, `generation_credential_ref`, `usage_summary`의 provider/model/credential 식별자 allowlist로 보존하되 credential 원문, API key, token, encrypted_config는 저장하지 않는다.
- 위 FK 정책은 기존 `llm_usage_logs.credential_id/model_id`의 `SET NULL` + `nullable=False` 정합성 문제를 반복하지 않기 위한 목표 계약이다. 최종 migration은 nullable과 `ondelete`를 함께 맞춰야 한다.
- `correlation_id`는 application-level convention이다. DB FK가 아니며, 강한 참조 무결성을 보장하지 않는다.
- `correlation_id`는 resource key가 아니므로 전역 unique로 강제하지 않는다. Server-generated 값은 answer run 단위로 충분히 고유하게 생성하고, client supplied 값은 여러 실행이 같은 값을 공유할 수 있는 grouping key로만 취급한다.
- 목표 index는 `(organization_id, correlation_id, created_at)`이다. Correlation 조회는 organization/time 범위와 함께 사용해야 하며, `correlation_id` 단독 조회를 tenant scope 판정에 사용하지 않는다.
- `status` 목표 값은 `requested`, `running`, `completed`, `failed`, `cancelled`, `blocked`이다. `blocked`는 scope 안 resource가 확인된 뒤 policy 또는 permission 때문에 answer delta를 생성하지 않은 상태를 표현한다.
- 상태 전이 기준은 `requested -> blocked`, `requested -> running -> completed|failed|cancelled|blocked` 두 경로다. Request schema validation, invalid `correlation_id`, invalid organization header, missing required header/body field처럼 answer 실행 시작 전 확인되는 오류는 row를 만들지 않는다. KB 없음, inactive KB, scope 밖 KB, organization mismatch처럼 `404 resource.not_found`로 숨겨야 하는 경우도 resource hiding을 유지하기 위해 row를 만들지 않는다. Schema validation, organization header validation, active organization scope 확인, KB scope visibility 확인, required credential/model visibility 확인을 모두 통과한 뒤 row를 생성하고 `requested`로 시작한다. KB/credential/model `use` 권한 또는 verified relation preflight 차단은 retrieval/generation 전 `requested -> blocked`로 닫는다. Retrieval/generation을 시작하기 전에 `running`으로 전환한다. 정상 종료는 `completed`, provider 또는 내부 오류는 `failed`, terminal status 전 client disconnect 또는 명시 취소는 `cancelled`, retrieval 이후 PII/classification policy 차단은 `running -> blocked`로 닫는다. 이미 `completed`, `failed`, `blocked`로 마감된 뒤 response delivery 중 연결이 끊기면 기존 terminal status와 lifecycle audit을 유지한다.
- Lifecycle audit은 상태 전이를 그대로 대체하지 않는다. Answer run row 생성 시 `rag.answer.requested`, 정상 종료 시 `rag.answer.completed`, provider/internal 오류 시 `rag.answer.failed`, terminal status 전 client disconnect/명시 취소 시 `rag.answer.cancelled`를 남긴다. Validation 400/422, invalid organization header, `resource.not_found`, scope 밖, organization mismatch처럼 row를 만들지 않는 오류는 `rag.answer.*` lifecycle audit 대상이 아니다. `blocked` 상태는 PII/classification/metadata policy 차단이면 `policy.block`, KB/credential/model permission preflight 차단이면 `permission.denied` audit과 함께 표현한다. 별도 `rag.answer.blocked` action은 만들지 않는다.
- `trace_payloads.rag_answer_run_id`, `llm_usage_logs.rag_answer_run_id`를 추가하지 않는다.
- Workflow runtime RAG evidence는 계속 `trace_payloads.payload_kind='rag.retrieval'`에 저장한다.
- Standalone answer의 retrieval evidence는 `rag_answer_runs.retrieval_summary`와 `citation_summary`에 redaction-safe summary로 저장한다.
- `retrieval_summary` field allowlist는 `knowledge_base_id`, `hierarchy_mode`, `retrieved_chunk_count`, `document_ids`, `citation_ids`, `score_summary`, `latency_ms`, `raw_content_returned`로 제한한다. `raw_content_returned`의 durable 저장값은 기본 `false`여야 한다.
- `citation_summary` field allowlist는 citation별 `citation_id`, `document_id`, `chunk_id`, `rank`, `score`, `filename`, `heading`, `hierarchy_path`, `metadata_summary`로 제한한다. `metadata_summary`는 classification, tags, source_type, effective range 같은 safe metadata만 포함하고 chunk content를 포함하지 않는다.
- `answer_hash`는 nullable이며, `rag_answer_runs.answer_hash` top-level column을 canonical 위치로 둔다. `answer_summary.answer_hash` mirror를 별도로 만들지 않는다.
- `answer_summary` field allowlist는 `answer_length`, `cited_document_count`, `citation_ids`, `policy_result`, `completion_status`, 선택적 `redacted_summary`로 제한한다. `redacted_summary`를 저장할 때도 raw final answer 재구성이 가능할 정도의 긴 본문은 저장하지 않는다.
- `usage_summary`는 answer 실행 시점에 캡처한 denormalized snapshot이다. Canonical LLM token/cost/latency 원천은 `llm_usage_logs`이며, usage 도메인에 generic `correlation_id` 또는 metadata extension이 추가되기 전까지 `usage_summary`와 `llm_usage_logs` 사이의 강한 FK 정합성을 보장하지 않는다.
- Durable/internal `usage_summary` field allowlist는 `prompt_tokens`, `completion_tokens`, `total_tokens`, `total_cost`, `latency_ms`, `model_id`, `model_name`, `provider`, `credential_id` 같은 집계/식별자 값으로 제한한다. Credential 원문, API key, token, encrypted_config, raw prompt/completion, provider raw response는 저장하지 않는다.
- User-facing response의 `usage_summary`는 API별 whitelist를 따르며 일반 사용자 응답에는 `credential_id`와 internal `model_id`를 기본 노출하지 않는다.
- `generation_credential_id`와 durable/internal `usage_summary.credential_id`는 secret이 아니라 credential 식별자다. Credential 원문 조회 권한을 의미하지 않으며, 응답 노출 여부는 API별 권한/whitelist에서 별도로 결정한다.
- Agent answer lifecycle audit은 `rag.answer.requested`, `rag.answer.completed`, `rag.answer.failed`, `rag.answer.cancelled`를 사용한다. Retrieval 성공 감사 `rag.retrieve`, provider 호출 감사 `llm.call`, answer 상태 `rag_answer_runs.status`와 의미를 섞지 않는다.
- Raw user question, raw final answer, raw retrieved chunk content, raw prompt/completion, credential 원문, API key, token, encrypted_config, provider raw response는 기본 저장하지 않는다.
- Answer history/replay가 필요하면 raw provider response가 아니라 별도 redacted answer snapshot, retention, access control을 공식 문서와 ADR로 먼저 확정한다.
- `query_hash`, `answer_hash`, `hash_version`은 nullable이다. 값을 저장하려면 HMAC-SHA256, server-side secret/pepper, `hash_version`을 함께 사용해야 한다. HMAC secret/pepper가 설정되지 않았으면 값을 `null`로 두며, 일반 SHA-256 같은 unsalted hash fallback은 허용하지 않는다. Hash algorithm 또는 HMAC 정책을 바꿀 수 있도록 `hash_version` 또는 동등한 metadata convention을 함께 저장한다.
- `retention_expires_at`은 answer run 생성 시점에 설정해야 하며 indefinite retention을 기본값으로 보지 않는다. 3단계 기본 보존 기간은 trace metadata 기본값과 맞춰 90일로 둔다. Purge는 RAG 도메인 service와 `log.rag_answer_retention_purge` Celery task가 소유하고, 실패 시 다음 scheduled/task run에서 idempotent하게 재시도한다. Purge 성공/실패 aggregate는 `audit_logs.action='rag.answer.purge'`로 남기되, 기본 aggregate event는 `target_type='rag_answer_runs'`, `target_id=null`로 기록한다. `audit_metadata` allowlist는 `organization_id`, `cutoff`, `purged_count`, `failed_count`, `retryable`, `status`로 제한하고 raw answer, raw query, raw chunk content는 포함하지 않는다. 3단계 기본 API에 list/detail/delete/purge를 포함하지 않으며, 수동 purge API를 별도로 만들 경우 권한, hard delete/soft delete 여부, 상세 응답 schema는 별도 API/ADR에서 확정한다.
- Usage 도메인의 정확한 answer-run correlation이 필요하면 `llm_usage_logs`에 RAG 전용 FK를 추가하지 않고 generic `correlation_id` 또는 metadata extension을 별도 ADR로 설계한다.
- `rag_answer_runs` 조회 권한은 KB `use` 권한, answer 생성자, organization manager override, retention/access policy를 함께 고려해야 한다. KB `read`만으로 answer content 성격의 summary/snapshot을 노출할지는 후속 API 문서에서 별도 확정한다.

### Deployment Checklist

`deployment_check_runs`, `deployment_check_items`를 만들지 않는다.

저장 기준:

- check 실행 event: `audit_logs.action='deployment.check'`
- 대상 app/workflow/deployment: `audit_logs.target_type`, `audit_logs.target_id`, `audit_logs.audit_metadata`
- check summary: `audit_logs.audit_metadata.summary`
- check items: `audit_logs.audit_metadata.items`
- 실행 저장 성공/실패: `audit_logs.status`
- pass/warn/block 결과: `audit_logs.audit_metadata.policy_result`

주의:

- checklist를 독립 검색/필터/통계의 1급 리소스로 만들어야 한다면 별도 table이 필요하다.
- 이 문서는 현재 물리 데이터 모델 보존 조건 때문에 그 table을 확정하지 않는다.

### Recommendation

`recommendation_events`를 만들지 않는다.

저장 기준:

- 추천 생성: `audit_logs.action='recommendation.created'`
- 추천 적용: `audit_logs.action='recommendation.applied'`
- 추천 무시: `audit_logs.action='recommendation.ignored'`
- 추천 대상 workflow/run/node/model: `audit_logs.target_*`, `audit_logs.audit_metadata`
- 비용 근거: `llm_usage_logs`, `llm_models`

주의:

- recommendation을 사용자에게 장기간 노출하고 상태 전이를 1급 데이터로 관리해야 한다면 별도 table이 필요하다.
- 이 문서는 현재 물리 데이터 모델 보존 조건 때문에 그 table을 확정하지 않는다.

### Operations Dashboard

MVP 목표 상태에서 operations dashboard는 별도 aggregate table을 만들지 않는다.

Dashboard API는 raw query로 아래 기존 테이블을 조회한다.

| 지표 | 원천 테이블 |
| --- | --- |
| 조직별 앱/워크플로우 | `organization`, `apps`, `workflows` |
| workflow 실행 수 | `workflow_runs` |
| 성공/실패율 | `workflow_runs.status` |
| 실행 latency | `workflow_runs.duration`, `workflow_node_runs.duration` |
| node별 실패 | `workflow_node_runs.status`, `workflow_node_runs.error_message` |
| trace payload 현황 | `trace_payloads` |
| trace 접근 감사 | `trace_payload_access_events` |
| LLM token/cost | `llm_usage_logs.prompt_tokens`, `llm_usage_logs.completion_tokens`, `llm_usage_logs.total_cost` |
| model별 비용 | `llm_usage_logs`, `llm_models` |
| 권한 변경/정책 이벤트 | `audit_logs` |
| deploy checklist 이력 | `audit_logs.action='deployment.check'` |
| recommendation 이력 | `audit_logs.action LIKE 'recommendation.%'` |

명시적 제외:

- `dashboard_aggregates` table
- `workflow_daily_metrics` table
- materialized view
- dashboard 저장 전용 table

## 참조관계 시각화

이 문서의 table별 관계 설명이 source of truth다. Mermaid 시각화는 [diagrams/data-model-overview.md](diagrams/data-model-overview.md)를 참고한다.

## MVP별 구현 기준

### MVP 1: Foundation / LLMOps

DB 변경:

- 현재 코드의 team permission table은 수정하지 않는다.
- `user_workflow_permissions`를 생성한다.
- `user_llm_permissions`를 생성한다.
- `roles`, `user_roles`, `resource_permissions`, `audit_events`를 만들지 않는다.

구현:

1. organization/team 기반 권한 판정 helper를 구현한다.
2. team permission과 user direct permission을 합산하는 effective permission helper를 구현한다.
3. workflow/LLM credential 조회 및 실행 API에 permission enforcement를 적용한다.
4. user direct permission은 additive allow로만 처리한다.
5. 권한 부여/회수/차단/실행 event를 `audit_logs`에 기록한다.
6. LLM usage dashboard는 `llm_usage_logs` raw query로 구현한다.
7. `llm_usage_logs.latency_ms` 물리 column과 ORM 속성을 기준으로 사용한다.

작동하는 MVP 산출물:

- 조직/팀 기반 권한과 user direct 추가 권한으로 workflow와 LLM credential 접근이 제한된다.
- LLM token/cost/latency가 dashboard query로 조회된다.
- 주요 사용자 action이 `audit_logs`에 남는다.

### MVP 2: Governance / RAG / Trace

DB 변경:

- `rag_retrieval_traces`를 만들지 않는다.
- `knowledge_bases.classification`, `documents.classification`을 추가하지 않는다.
- MBA-78 1차 구현에서 `document_chunks.parent_chunk_id`, `document_chunks.chunk_level`, `document_chunks.section_path`, `document_chunks.heading` nullable column을 추가한다.
- `user_knowledge_permissions`를 생성한다. 현재 코드에는 아직 없다.
- RAG Agent answer 실행 anchor가 필요하면 `rag_answer_runs` additive table로 분리한다. `trace_payloads`와 `llm_usage_logs`에는 answer 전용 FK를 추가하지 않는다.

구현:

1. Workflow runtime RAG retrieval 결과를 `workflow_runs`, `workflow_node_runs`, `trace_payloads`로 연결해 저장한다.
2. retrieval payload의 민감 정보는 trace redaction policy를 적용한다.
3. trace raw/redacted payload 접근은 `trace_payload_access_events`에 기록한다.
4. RAG data source 접근은 우선 `team_knowledge_permissions`로 제한하고, `user_knowledge_permissions` 추가 후 user direct grant를 합산한다.
5. document re-index 필요 상태는 `documents.meta_info` metadata로 관리한다.
6. 감사 검색 API는 `audit_logs`와 `trace_payload_access_events`를 구분해서 조회한다.
7. Hierarchical retrieval은 parent chunk를 coarse retrieval에 사용하고 child chunk를 final evidence로 반환한다.
8. Standalone RAG Agent answer는 `rag_answer_runs`와 `correlation_id`로 trace/usage/audit을 느슨하게 연결한다.

작동하는 MVP 산출물:

- RAG 실행 결과가 run/node trace에서 확인된다.
- trace payload 접근이 정책과 감사 로그로 통제된다.
- knowledge base 접근 권한이 team permission과 user direct permission으로 제한된다.

### MVP 3: Enterprise Ops

DB 변경:

- `deployment_check_runs`, `deployment_check_items`, `recommendation_events`를 만들지 않는다.
- dashboard aggregate table을 만들지 않는다.
- `user_audit_permissions`를 생성한다. 현재 코드에는 아직 없다.

구현:

1. deployment checklist는 실행 시점에 계산하고 결과를 `audit_logs`에 저장한다.
2. recommendation은 `llm_usage_logs`와 `llm_models`를 근거로 계산하고 lifecycle event를 `audit_logs`에 저장한다.
3. operations dashboard는 `workflow_runs`, `workflow_node_runs`, `trace_payloads`, `llm_usage_logs`, `audit_logs` raw query로 구현한다.
4. audit visibility는 우선 `team_audit_permissions`로 평가하고, `user_audit_permissions` 추가 후 user direct grant를 합산한다.
5. checklist와 recommendation의 장기 상태 관리가 필요하면 별도 schema 변경 요청으로 분리한다.

작동하는 MVP 산출물:

- 배포 전 check 결과가 사용자에게 표시되고 `audit_logs`에 남는다.
- 비용/운영 추천이 생성되고 적용/무시 event가 `audit_logs`에 남는다.
- 운영 dashboard가 raw query로 실행 상태, 비용, trace, audit 현황을 보여준다.

## 별도 승인이 필요한 Schema Extension

아래 요구가 확정되면 현재 물리 데이터 모델 보존 조건을 넘어서므로 별도 설계 문서와 migration 승인이 필요하다.

| 요구 | 필요한 schema 후보 |
| --- | --- |
| 역할 catalog를 DB에서 1급으로 관리 | `roles`, `user_roles` |
| 임의 resource polymorphic permission | `resource_permissions` |
| RAG retrieval FK 무결성 보장 | `rag_retrieval_traces` |
| RAG answer와 trace/usage 사이의 강한 FK lineage | trace/usage 도메인의 generic subject 또는 generic `correlation_id` extension. RAG 전용 FK column은 추가하지 않는다. |
| deployment checklist 독립 검색/통계 | `deployment_check_runs`, `deployment_check_items` |
| recommendation 장기 상태 관리 | `recommendation_events` |
| connection을 workflow/knowledge base와 독립적으로 team/user에게 공유 | `team_connection_permissions`, `user_connection_permissions` |
| KB/document classification DB 필터링 | `knowledge_bases.classification`, `documents.classification` |
| dashboard 성능 병목 해소 | view, materialized view, aggregate table |

## 검증 기준

MVP 목표 데이터 모델 구현은 아래 조건을 만족해야 한다.

1. 현재 코드에 이미 존재하는 table과 column을 삭제, rename, 대체하지 않는다.
2. `roles`, `user_roles`, `resource_permissions`, `audit_events`를 생성하지 않는다.
3. 현재 기본 권한은 `organization_memberships` active row를 organization 소속 전제 조건으로 사용하고, `teams`, `team_memberships`, `team_*_permissions`를 team 배정과 team resource permission 기준으로 사용해야 한다.
4. 현재 코드의 user direct 권한은 `user_workflow_permissions`, `user_llm_permissions` 기준으로 additive allow만 제공해야 한다. MVP 2/3에서 `user_knowledge_permissions`, `user_audit_permissions`를 추가할 때도 같은 규칙을 따른다.
5. user direct 권한은 team 권한을 deny하거나 낮추면 안 된다.
6. audit은 `audit_logs` 기준으로 동작해야 한다.
7. trace는 `workflow_runs`, `workflow_node_runs`, `trace_payloads`, `trace_*_policies`, `trace_payload_access_events` 기준으로 동작해야 한다.
8. `tenant_id`를 새로 도입하지 않는다.
9. `apps`는 project boundary로 유지한다.
10. workflow node는 별도 table이 아니라 graph 내부 id string reference로 유지한다.
11. dashboard API는 raw query로 구현한다.
12. 이 문서에 current 또는 planned table로 명시되지 않은 추가 신규 table이 필요해지는 요구는 별도 schema extension 문서로 분리한다.
