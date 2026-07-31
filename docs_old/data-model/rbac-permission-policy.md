# RBAC 권한 정책

Status: Draft
Authority: Data Model
Source of Truth: Yes
Verified Against: feature/mba-78 @ HEAD (base dev d0c858e)
Related ADRs: [ADR-202606290116-accept-rbac-auth-state-and-user-direct-permission](../decisions/ADR-202606290116-accept-rbac-auth-state-and-user-direct-permission.md), [ADR-202606290131-audit-action-naming-standard](../decisions/ADR-202606290131-audit-action-naming-standard.md), [ADR-202606290145-active-organization-header-context](../decisions/ADR-202606290145-active-organization-header-context.md), [ADR-202606301045-metadata-aware-hierarchical-rag-boundary](../decisions/ADR-202606301045-metadata-aware-hierarchical-rag-boundary.md)

## 목적

이 문서는 현재 workspace code의 조직/팀 권한 관련 물리 데이터 모델을 기준으로, 현재 구현과 MVP 목표 상태에서 사용할 권한 정책과 matrix를 정의한다.

이 문서는 [physical-data-model.md](physical-data-model.md)를 따른다. 따라서 `roles`, `user_roles`, polymorphic `resource_permissions`를 새로 만들지 않는다. MBA-67 이후 organization scope는 `organization_memberships`를 organization 소속의 전제 조건으로 사용하고, `team_memberships`는 team permission 계산을 위한 team 배정 관계로 유지한다. 현재 코드는 workflow/LLM credential에 대해서만 user direct 권한을 구현하며, knowledge/audit user direct 권한은 MVP 2/3 목표 schema다.

## 빠른 구현 기준

- 물리 table 기준은 [physical-data-model.md](physical-data-model.md)를 따른다.
- 권한 해석 기준은 이 문서의 `auth_state`, resource matrix, enforcement point를 따른다.
- 구현자는 권한 row 없음, `auth_state='none'`, user direct additive allow, audit 기록 조건을 우선 테스트한다.
- `roles`, `user_roles`, polymorphic `resource_permissions`, `user_connection_permissions`, `user_app_permissions`, `user_document_permissions`, `user_model_permissions`는 만들지 않는다.
- 현재 코드의 user direct table은 `user_workflow_permissions`, `user_llm_permissions`뿐이다.
- `organization_memberships`는 MBA-66에서 DB/model/migration foundation으로 추가되었고, MBA-67 이후 permission helper/API의 organization scope 기준으로 사용한다.

## 기준 테이블

| Table | 역할 |
| --- | --- |
| `organization` | 조직 범위. app/workflow/knowledge base/LLM credential의 상위 scope |
| `organization_memberships` | MBA-66 DB foundation: user와 organization의 직접 소속 관계 |
| `teams` | 권한 부여 subject. 기존 role 개념은 team template으로 흡수 |
| `team_memberships` | organization 안의 team 배정 관계. Team permission 계산에만 사용하고 organization scope의 전제 조건으로 사용하지 않는다. |
| `team_workflow_permissions` | team별 workflow 권한 |
| `team_knowledge_permissions` | team별 knowledge base 권한 |
| `team_llm_permissions` | team별 LLM credential 권한 |
| `team_audit_permissions` | team별 audit visibility 권한 |
| `user_workflow_permissions` | user별 workflow 추가 권한 |
| `user_knowledge_permissions` | 목표: MVP 2에서 user별 knowledge base 추가 권한 |
| `user_llm_permissions` | user별 LLM credential 추가 권한 |
| `user_audit_permissions` | 목표: MVP 3에서 user별 audit visibility 추가 권한 |

## 설계 원칙

| 원칙 | 내용 |
| --- | --- |
| Team 우선 | 기본 권한 subject는 team이다. |
| Organization membership 전제 | Active `organization_memberships` row가 organization scope와 resource permission subject의 필요조건이다. Invited/suspended/removed row는 fail-closed 처리한다. Membership row 자체가 없는 legacy owner/manager만 호환 fallback으로 manager scope를 인정한다. |
| User direct grant | 예외적 추가 권한은 user별 permission table로 부여한다. |
| Organization scope | team과 permission은 조직 범위 안에서 해석한다. |
| `auth_state` 단일 상태 | 현재 권한 모델은 permission boolean set이 아니라 `auth_state` 문자열 하나를 가진다. |
| Matrix 해석 | `auth_state` 값은 DB enum이 아니라 application-level matrix로 해석한다. |
| Deny by default | 명시 권한이 없거나 `auth_state='none'`이면 거부한다. |
| Additive only | user direct permission은 권한을 추가로 허용할 뿐, team 권한을 낮추거나 deny하지 않는다. |
| Organization owner/manager 자동 권한 | Active organization membership의 `organization_auth_state='manager'`를 우선 기준으로 삼는다. Membership row 자체가 없을 때만 `organization.created_by` 또는 `organization.managed_by` user를 legacy fallback manager로 판정한다. |
| Public endpoint 분리 | public run/webhook은 `apps.auth_secret` 인증을 유지하되 내부 실행에서 workflow/credential/data 권한을 추가 평가할 수 있다. |
| 감사 기록 | 권한 부여, 회수, 차단은 `audit_logs`에 남긴다. |

## 권한 어휘

행동 권한은 다음 단어로 통일한다.

| Permission | 의미 | 대표 사용처 |
| --- | --- | --- |
| `read` | 리소스를 조회할 수 있다. | workflow 보기, KB 보기, audit 보기 |
| `write` | 리소스 내용을 수정할 수 있다. | workflow graph 수정, KB 설정 수정 |
| `execute` | workflow를 실행할 수 있다. | manual/API/schedule 실행 |
| `use` | 다른 리소스에서 참조하거나 런타임에 사용할 수 있다. | LLM credential 사용, KB retrieval 사용 |
| `deploy` | deployment를 만들거나 활성화할 수 있다. | 배포 생성, 이전 배포 활성화 |
| `manage` | 권한/설정/위험 작업을 관리할 수 있다. | team permission 변경, credential 관리 |
| `view_raw` | redaction 전 raw/prompt payload를 볼 수 있다. | trace raw payload 조회 |

주의:

- `admin`은 permission이 아니다.
- `Admin`, `Builder`, `Operator`, `Viewer`, `Auditor`는 DB role이 아니라 team template 이름으로 취급한다.

## `auth_state` 표준값

현재 DB는 `auth_state`를 string으로 저장한다. 현재 코드와 MVP 목표 상태에서는 아래 값을 application-level 표준으로 사용한다.

| `auth_state` | 의미 | 포함 permission |
| --- | --- | --- |
| `none` | 명시 권한 없음 | 없음 |
| `viewer` | 조회만 가능 | `read` |
| `operator` | 조회와 실행/사용 가능 | `read`, `execute`, `use` |
| `builder` | 생성/수정/실행 가능 | `read`, `write`, `execute`, `use` |
| `manager` | 권한과 설정까지 관리 가능 | `read`, `write`, `execute`, `use`, `deploy`, `manage` |
| `auditor` | 감사 조회 가능 | `read` |
| `raw_auditor` | 감사 조회와 raw trace 조회 가능 | `read`, `view_raw` |

제약:

- `auth_state`는 모든 permission table에서 같은 문자열을 쓸 수 있지만, 실제 의미는 resource type별 matrix로 제한한다.
- 예를 들어 `team_audit_permissions.auth_state='auditor'`는 의미가 있지만, `team_workflow_permissions.auth_state='auditor'`는 사용하지 않는다.
- 기존 DB row 호환을 위해 `read -> viewer`, `execute -> operator`, `write -> builder`, `admin -> manager`로 먼저 normalize한다.
- normalize 후에도 허용되지 않은 `auth_state` 값은 `none`과 동일하게 fail-closed 처리한다.
- 신규 permission grant API request는 resource matrix의 canonical 값만 받는다. 예를 들어 workflow/LLM credential grant request에 `admin`을 보내면 validation에서 거부된다.
- 이 문서의 `auth_state` 표준값은 MVP 목표 상태 기준으로 확정한다.

## Team Template

Team template은 seed나 UI preset으로 만들 수 있지만 DB role table은 만들지 않는다.

| Team Template | 기본 목적 |
| --- | --- |
| `Admin` | 조직 설정, 권한, 주요 리소스 관리 |
| `Builder` | workflow와 RAG/LLM 설정을 만들고 수정 |
| `Operator` | 배포된 workflow 실행과 운영 확인 |
| `Viewer` | 조회 전용 |
| `Auditor` | audit/trace 조회 전용 |

Team template은 권한을 자동으로 보장하지 않는다. 실제 권한은 resource별 `team_*_permissions` row가 결정한다.

## User Direct Permission

User direct permission은 team 권한으로 표현하기 어려운 예외적 추가 허용을 저장한다. 이 권한은 additive allow 전용이다.

| Table | 대상 resource | 현재 상태 |
| --- | --- | --- |
| `user_workflow_permissions` | `workflows` | 구현됨. 특정 user에게 workflow 추가 권한 부여 |
| `user_knowledge_permissions` | `knowledge_bases` | MVP 2 목표. 현재 코드에는 없음 |
| `user_llm_permissions` | `llm_credentials` | 구현됨. 특정 user에게 LLM credential 추가 권한 부여 |
| `user_audit_permissions` | `organization` | MVP 3 목표. 현재 코드에는 없음 |

공통 column:

| Column | 설명 |
| --- | --- |
| `id` | permission row id |
| `grantee_organization_id` | 권한을 받는 user가 속한 organization |
| `user_id` | 권한을 직접 부여받는 user |
| resource FK | `workflow_id`, `knowledge_base_id`, `llm_credential_id`, `target_organization_id` 중 하나 |
| `auth_state` | 직접 부여된 권한 상태 |
| `assigned_by` | 권한 부여자 user |
| `assigned_at` | 권한 부여 시각 |
| `options` | 확장 설정 |
| `flags` | 확장 flag |

제약:

- user direct permission은 team permission보다 우선해서 deny하지 않는다.
- user direct permission이 더 약한 상태여도 team permission을 낮추지 않는다.
- effective permission은 team permission과 user direct permission 중 가장 강한 허용 상태다.
- `user_connection_permissions`, `user_app_permissions`, `user_document_permissions`, `user_model_permissions`는 이 문서에서 만들지 않는다.
- connection `use`는 목표 상태에서 connection permission table이 아니라 consuming workflow/knowledge base 권한으로 허용한다. 현재 코드는 connector API와 DB source upload에서 owner 기준을 주로 사용하고, 저장된 DB source sync/processor 경로는 document metadata의 `connection_id`를 사용한다.

## Resource별 Matrix

### Workflow

기준 table:

- `team_workflow_permissions`
- `user_workflow_permissions`

| `auth_state` | `read` | `write` | `execute` | `deploy` | `manage` | 설명 |
| --- | --- | --- | --- | --- | --- | --- |
| `none` | No | No | No | No | No | 접근 불가 |
| `viewer` | Yes | No | No | No | No | workflow 조회만 가능 |
| `operator` | Yes | No | Yes | No | No | 실행 가능, 수정 불가 |
| `builder` | Yes | Yes | Yes | No | No | graph 수정과 실행 가능 |
| `manager` | Yes | Yes | Yes | Yes | Yes | 배포/권한 관리까지 가능 |

적용 위치:

- workflow 목록/상세 조회: `read`
- workflow graph 저장: `write`
- workflow manual/API 실행: `execute`
- deployment 생성/활성화/이전 배포 활성화: `deploy`
- workflow permission 변경: `manage`

### Knowledge Base

기준 table:

- `team_knowledge_permissions`
- 목표: `user_knowledge_permissions`

Active `organization_memberships` row는 KB가 속한 organization scope 안의 resource permission subject인지 확인하는 전제 조건이다. 이 membership만으로 KB `read`/`use`를 허용하지 않으며, 실제 허용은 organization manager override와 KB permission table의 effective permission으로 판정한다.

현재 코드의 Knowledge Base API는 `KnowledgeBase.user_id == current_user.id` 같은 owner/current-user scope를 주로 사용한다. RAG API는 endpoint별로 차이가 있으며 기존 KB upload, analyze, confirm, progress 경로는 owner/scope 검증이 약하다. 아래 matrix는 MVP 2에서 team permission과 planned user direct grant를 적용할 목표 정책이다.

| `auth_state` | `read` | `write` | `use` | `manage` | 설명 |
| --- | --- | --- | --- | --- | --- |
| `none` | No | No | No | No | 접근 불가 |
| `viewer` | Yes | No | No | No | 목록/문서 조회만 가능 |
| `operator` | Yes | No | Yes | No | RAG retrieval에서 사용 가능 |
| `builder` | Yes | Yes | Yes | No | 문서 추가/재색인/설정 수정 가능 |
| `manager` | Yes | Yes | Yes | Yes | 권한과 설정 관리 가능 |

적용 위치:

- KB 목록/상세 조회: `read`
- 문서 업로드/삭제/재색인: `write`
- RAG search-test `chat`/`pure`: retrieval과 content preview를 수행하므로 목표 `use`
- workflow RAG node 실행: `use`
- KB permission 변경: `manage`

주의:

- 현재 물리 데이터 모델에는 document별 permission table이 없다.
- document별 차단이 필요하면 `knowledge_base` 권한과 document metadata 정책으로 먼저 처리한다.
- document별 강한 권한이 필요하면 별도 schema extension이 필요하다.
- Metadata는 permission source of truth가 아니다. `documents.meta_info`와 `document_chunks.metadata`는 retrieval filter, policy decision, citation evidence에 사용할 수 있지만 권한 판정 source로 쓰지 않는다. Organization membership은 scope 전제 조건이고, KB `read`/`use` 허용은 organization manager override와 KB permission table의 effective permission을 따른다.
- `classification=pii`는 external LLM prompt path에서 `policy.block`, internal-only search preview에서 `policy.warn`을 기본값으로 둔다.
- `classification=confidential`은 KB `use` 권한을 통과하면 허용하되 audit/trace policy result를 남긴다.

### LLM Credential

기준 table:

- `team_llm_permissions`
- `user_llm_permissions`

| `auth_state` | `read` | `use` | `write` | `manage` | 설명 |
| --- | --- | --- | --- | --- | --- |
| `none` | No | No | No | No | 접근 불가 |
| `viewer` | Yes | No | No | No | credential 이름/preview 조회만 가능 |
| `operator` | Yes | Yes | No | No | workflow 실행에서 credential 사용 가능 |
| `builder` | Yes | Yes | No | No | workflow 설정에서 credential 선택 가능 |
| `manager` | Yes | Yes | Yes | Yes | credential 생성/수정/삭제/권한 관리 가능 |

적용 위치:

- credential 목록/preview 조회: `read`
- LLM node 실행: `use`
- credential 생성/수정/삭제: `write`
- credential permission 변경: `manage`

주의:

- 현재 물리 데이터 모델에는 `llm_model` 전용 team permission table이 없다.
- model 사용 제한은 `llm_rel_credential_models`, credential 권한, application policy를 함께 평가한다.

### Audit

기준 table:

- `team_audit_permissions`
- 목표: `user_audit_permissions`

현재 코드에는 `team_audit_permissions` 물리 table과 trace redaction/visibility policy 기반이 있다. 현재 trace 상세/payload 접근 제어는 구현되어 있지만 `team_audit_permissions`를 직접 조회하지 않고 system admin, app owner, workflow effective RBAC, visibility policy를 조합한다. 다만 현재 등록된 audit log 조회 API는 `GET /api/v1/users/me/audit-logs` 자기 범위 조회뿐이며, organization-wide `/api/v1/audit/logs` 검색 API와 audit permission 기반 통합 조회는 아직 없다. `user_audit_permissions`는 아직 구현되지 않은 MVP 3 목표 table이다.

| `auth_state` | `read` | `view_raw` | `manage` | 설명 |
| --- | --- | --- | --- | --- |
| `none` | No | No | No | audit 접근 불가 |
| `auditor` | Yes | No | No | 목표: audit log와 redacted trace 조회 가능 |
| `raw_auditor` | Yes | Yes | No | 목표: raw/prompt payload 조회 가능 |
| `manager` | Yes | Yes | Yes | 목표: audit visibility policy 관리 가능 |

적용 위치:

- `audit_logs` 검색: 목표 `read`. 현재 구현은 자기 audit log 조회만 제공한다.
- `trace_payloads.redacted_payload` 조회: 현재 `TraceAccessService`가 system admin, app owner, workflow effective auth state, visibility policy를 함께 평가한다.
- `trace_payloads.raw_payload_encrypted` 복호화 조회: 현재 system admin은 admin raw policy, app owner는 owner raw policy, workflow RBAC 사용자는 workflow effective `manager`와 owner raw policy가 필요하다. prompt/completion payload raw 접근은 추가로 prompt/completion visibility flag가 허용되어야 한다.
- audit visibility permission 변경: 목표 `manage`. 현재 policy management API는 `TraceRbacService` system admin provider를 사용한다.

추가 정책:

- raw trace 조회는 현재 `team_audit_permissions`를 사용하지 않는다.
- 현재 trace 접근 제어는 audit permission table을 직접 조회하기보다 app owner/system admin/workflow effective RBAC와 `trace_visibility_policies`를 함께 적용한다. `team_audit_permissions` 기반 audit visibility는 organization-wide audit search가 추가될 때의 목표 모델이다.
- raw trace 조회 시도는 성공/실패 모두 `trace_payload_access_events`에 기록한다. 허용된 raw 응답은 감사 기록이 먼저 성공해야 한다.

### App / Project Boundary

기준 table: 없음. `apps`는 project boundary지만 현재 물리 데이터 모델에는 app 전용 team permission table이 없다.

확정 처리:

| 행위 | 권한 기준 |
| --- | --- |
| app 목록/상세 | organization owner/manager 또는 app에 연결된 primary workflow의 `read` 권한 |
| app 설정 수정 | organization owner/manager 또는 primary workflow `manage` |
| public endpoint secret 회전 | primary workflow `manage` |
| marketplace 공개 설정 | primary workflow `manage` |

주의:

- app 자체 권한을 workflow와 분리해야 하면 `team_app_permissions`가 필요하다.
- 현재 문서에서는 현재 물리 데이터 모델 보존 조건 때문에 `team_app_permissions`를 만들지 않는다.
- legacy workflow처럼 `organization_id`가 비어 있는 경우에만 active `created_by` user의 manager fallback을 제한적으로 허용한다. Organization-scoped app/workflow에는 이 fallback을 적용하지 않는다.

### Deployment

기준 table: 없음. `workflow_deployments`는 workflow의 하위 resource로 본다.

확정 처리:

| 행위 | 권한 기준 |
| --- | --- |
| deployment 조회 | workflow `read` |
| deployment 생성 | workflow `deploy` |
| deployment 활성화 | workflow `deploy` |
| 이전 deployment 활성화 | workflow `deploy` |
| deployment 삭제 또는 위험 변경 | workflow `manage` |

주의:

- rollback 전용 permission은 두지 않는다.
- 이전 deployment 활성화는 `deployment.activate_previous` audit action으로 남긴다.

### Connection

기준 table: 없음. 현재 물리 데이터 모델에는 connection 전용 team permission table이 없다.

여기서 connection 권한은 `connections` 테이블에 저장된 외부 DB 연결정보를 조회, 수정, 삭제하거나 workflow 실행에서 사용하는 권한을 뜻한다. 연결된 외부 DB 내부의 table/row 권한을 Nodease DB에서 대신 관리한다는 뜻은 아니다.

`connections` 자체에는 `organization_id`가 없다. 따라서 직접 connection CRUD API는 `connections.user_id` owner를 기본 기준으로 삼고, organization owner/manager 판정은 connection이 active organization의 workflow/knowledge base에 연결되어 scope가 식별되는 경우에 적용한다.

현재 코드 기준 직접 connector API와 신규 DB source upload는 `connections.user_id == current_user.id` owner scope를 확인한다. DB source가 저장된 뒤의 sync/processor 경로는 `KnowledgeBase.user_id == execution user`와 document metadata의 `connection_id`를 기준으로 connection을 조회하며, 별도 connection permission이나 consuming workflow/knowledge base 권한 기반 runtime `use` enforcement는 아직 없다. 아래 matrix는 MVP 2 목표 정책이며, 현재 구현과 다른 항목은 current note를 우선한다.

확정 처리:

| 행위 | 권한 기준 |
| --- | --- |
| connection metadata 조회 | connection owner, organization owner/manager, 또는 consuming workflow/knowledge base `builder` 이상 |
| connection secret 조회 | connection owner 또는 organization owner/manager |
| connection 생성 | 인증된 user. 생성자는 `connections.user_id` owner가 된다. |
| connection 수정/삭제/secret rotation | connection owner 또는 organization owner/manager |
| workflow DB node에서 connection 선택/설정 | workflow `builder` 이상. 단, secret은 노출하지 않는다. |
| knowledge base DB source에서 connection 선택/설정 | knowledge base `builder` 이상. 단, secret은 노출하지 않는다. |
| runtime connection 사용 | 목표: consuming workflow `execute` 또는 knowledge base `use`. 현재 DB source sync/processor 경로는 저장된 `connection_id`를 server-side에서 사용 |

주의:

- 목표 상태의 connection `use`는 connection owner에게만 제한하지 않는다. 그렇게 제한하면 owner가 아닌 team member가 DB 기반 workflow/knowledge base를 실행할 수 없다.
- 연결된 workflow/knowledge base가 없거나 active organization scope를 단일하게 식별할 수 없으면 organization owner/manager override를 적용하지 않고 deny한다.
- 하나의 connection이 서로 다른 organization의 resource와 충돌하는 방식으로 연결되면 implicit sharing으로 해석하지 않고 deny한다. Cross-organization sharing이 필요하면 별도 schema/permission extension으로 다룬다.
- runtime은 encrypted credential을 server-side에서만 복호화해서 사용하고, client/API 응답에 secret을 반환하지 않는다.
- 연결된 외부 DB 내부의 table/row 권한은 Nodease RBAC에서 대신 관리하지 않는다. 외부 DB credential 자체의 권한 범위가 최종 DB 접근 범위를 제한한다.
- connection을 workflow/knowledge base와 독립적으로 team/user에게 공유해야 하는 요구가 생기면 `team_connection_permissions`/`user_connection_permissions`를 별도 schema extension으로 검토한다.

## Action별 Enforcement Matrix

| 위치 | 필요한 permission | 기준 table |
| --- | --- | --- |
| Gateway workflow read API | workflow `read` | `team_workflow_permissions`, `user_workflow_permissions` |
| Workflow save API | workflow `write` | `team_workflow_permissions`, `user_workflow_permissions` |
| Workflow execute API | workflow `execute` | `team_workflow_permissions`, `user_workflow_permissions` |
| Workflow engine LLM node | credential `use` | `team_llm_permissions`, `user_llm_permissions` |
| Workflow engine RAG node | knowledge base `use` | 현재 코드: MBA-78 1차에서 LLM node RAG retrieval은 KB `use`를 적용한다. Phase 1 권한 원천은 organization manager override와 `team_knowledge_permissions`이며, `user_knowledge_permissions` additive grant는 table/API 추가 후 연결한다. |
| Workflow engine DB node | workflow `execute`; connection secret은 server-side runtime만 사용 | `team_workflow_permissions`, `user_workflow_permissions`, `connections` |
| Knowledge base DB source 사용 | 목표: knowledge base `use`; connection secret은 server-side runtime만 사용 | 현재 코드: DB source upload는 `connections.user_id == current_user.id`를 확인하지만 기존 KB scope 검증은 약함. 목표: `team_knowledge_permissions`, `user_knowledge_permissions`, `connections` |
| Connection secret/manage | connection owner 또는 organization owner/manager | `connections` |
| Deployment create | workflow `deploy` | `team_workflow_permissions`, `user_workflow_permissions` |
| Deployment activate | workflow `deploy` | `team_workflow_permissions`, `user_workflow_permissions` |
| Permission grant/revoke | organization owner/manager 또는 target resource `manage` | resource별 team/user permission table |
| Audit log search | 목표: audit `read`; 현재: own audit read | 현재 API는 `users/me/audit-logs`; 목표: `team_audit_permissions`, MVP 3 `user_audit_permissions` |
| Raw trace payload read | current trace access + trace visibility policy | 현재: system admin + admin raw policy, app owner + owner raw policy, workflow effective `manager` + owner raw policy. prompt/completion raw는 추가 visibility flag 필요. 목표: audit `view_raw`와 통합 |
| Dashboard workflow metrics | workflow `read` | `team_workflow_permissions`, `user_workflow_permissions` |
| Dashboard cost metrics | workflow `read` + credential visibility policy | workflow/credential의 team/user permission |

## 권한 부여 주체

권한 부여와 회수는 다음 user만 수행할 수 있다.

| 행위 | 허용 주체 |
| --- | --- |
| team 생성/수정/비활성화 | organization owner/manager |
| team membership 추가/제거 | organization owner/manager |
| team resource permission 부여/회수 | organization owner/manager 또는 해당 resource의 effective `manager` |
| user direct permission 부여/회수 | organization owner/manager 또는 해당 resource의 effective `manager` |
| audit visibility permission 부여/회수 | organization owner/manager 또는 audit effective `manager` |

여기서 effective `manager`는 team permission과 user direct permission을 합산한 결과가 `manager`인 user를 뜻한다.

## 권한 판정 순서

1. 사용자를 인증한다.
2. 요청의 active organization을 결정한다.
3. `organization_memberships` row 존재 여부를 먼저 확인한다.
4. Active row는 active user와 active organization 안에서만 `organization_auth_state`에 따라 member 또는 manager로 판정하고, invited/suspended/removed row는 fail-closed 처리한다.
5. Membership row 자체가 없고 user가 `organization.created_by` 또는 `organization.managed_by`이면 legacy 호환으로 해당 organization scope 안에서 `manager`로 판정한다.
6. 사용자의 active organization 안 team 배정과 team permission은 `team_memberships`에서 조회한다.
7. 대상 resource의 organization scope를 확인한다.
8. resource별 permission table에서 team들의 `auth_state`를 조회한다.
9. 현재 구현된 workflow/LLM resource는 user direct permission table에서 해당 user의 `auth_state`를 조회한다. knowledge/audit user direct 조회는 MVP 2/3 table 추가 후 적용한다.
10. team permission과 user direct permission 중 가장 강한 허용 상태를 적용한다.
11. trace/raw/audit처럼 별도 visibility policy가 있으면 추가 평가한다.
12. 최종 decision을 반환한다.
13. 거부 또는 민감 action은 `audit_logs` 또는 `trace_payload_access_events`에 기록한다.

## 우선순위 규칙

현재 물리 데이터 모델에는 explicit deny column이 없다. MVP 목표 상태에서도 user direct deny를 도입하지 않는다. 따라서 기본 우선순위는 아래와 같다.

1. 비활성 user/team/organization이면 거부한다.
2. resource가 요청 organization 밖이면 거부한다.
3. organization owner/manager는 organization scope 안에서 `manager`로 판정한다.
4. permission row가 없으면 거부한다.
5. `auth_state='none'`이면 거부한다.
6. 여러 team 권한이 있으면 가장 높은 state를 적용한다.
7. user direct permission이 있으면 team permission과 비교해 더 강한 state를 적용한다.
8. user direct permission이 더 약해도 team permission을 낮추지 않는다.
9. 현재 raw trace는 system admin, app owner, workflow effective `manager` 수준 RBAC 중 하나와 `trace_visibility_policies`가 허용해야 한다. audit team/user permission과의 통합은 목표 상태다.
10. legacy resource처럼 organization scope가 없는 경우에만 creator fallback을 제한적으로 적용한다.

권한 강도 순서:

```text
none < viewer < operator < builder < manager
auditor < raw_auditor < manager
```

## Team Template 기본 Matrix

Team template은 신규 조직 생성 시 기본 권한 row를 만들기 위한 preset이다.

| Template | Workflow | Knowledge Base | LLM Credential | Audit |
| --- | --- | --- | --- | --- |
| `Admin` | `manager` | `manager` | `manager` | `manager` |
| `Builder` | `builder` | `builder` | `builder` | `none` |
| `Operator` | `operator` | `operator` | `operator` | `none` |
| `Viewer` | `viewer` | `viewer` | `viewer` | `none` |
| `Auditor` | `viewer` | `viewer` | `viewer` | `auditor` |

주의:

- 위 matrix는 default preset이다.
- 실제 권한은 resource별 permission row가 결정한다.
- `Auditor`는 목표 audit visibility model에서도 raw payload를 볼 수 없다. 현재 trace raw payload는 `raw_auditor`가 아니라 system admin/app owner/workflow effective `manager`와 visibility policy로 판정한다.

## Audit 기록 기준

권한/정책 관련 event는 `audit_logs`에 기록한다. 현재 등록된 `/api/v1/permissions/*` router는 grant/revoke를 permission row별 data-change action으로 기록한다. 아래 표는 permission row 변경, permission denial, policy enforcement 관련 audit event 기준이다. RAG retrieval 성공 같은 일반 실행 감사 action은 [physical-data-model.md](physical-data-model.md)와 [ADR-202606290131](../decisions/ADR-202606290131-audit-action-naming-standard.md)을 따른다.

| Event | `audit_logs.action` | 기록 조건 |
| --- | --- | --- |
| team workflow 권한 생성/수정 | `team_workflow_permission.created` 또는 `team_workflow_permission.updated` | team workflow permission row 생성/변경 |
| team workflow 권한 회수 | `team_workflow_permission.deleted` | team workflow permission row 삭제 |
| user workflow 권한 생성/수정 | `user_workflow_permission.created` 또는 `user_workflow_permission.updated` | user workflow permission row 생성/변경 |
| user workflow 권한 회수 | `user_workflow_permission.deleted` | user workflow permission row 삭제 |
| team LLM credential 권한 생성/수정 | `team_llm_permission.created` 또는 `team_llm_permission.updated` | team LLM permission row 생성/변경 |
| team LLM credential 권한 회수 | `team_llm_permission.deleted` | team LLM permission row 삭제 |
| user LLM credential 권한 생성/수정 | `user_llm_permission.created` 또는 `user_llm_permission.updated` | user LLM permission row 생성/변경 |
| user LLM credential 권한 회수 | `user_llm_permission.deleted` | user LLM permission row 삭제 |
| team knowledge base 권한 생성/수정 | `team_knowledge_permission.created` 또는 `team_knowledge_permission.updated` | 현재 `team_knowledge_permissions` row 생성/변경. MBA-78 1차는 RAG search-test와 Workflow runtime의 KB `use` enforcement를 먼저 연결했고, 전체 KB permission API 정렬은 후속 범위 |
| team knowledge base 권한 회수 | `team_knowledge_permission.deleted` | 현재 `team_knowledge_permissions` row 삭제. MBA-78 1차는 RAG search-test와 Workflow runtime의 KB `use` enforcement를 먼저 연결했고, 전체 KB permission API 정렬은 후속 범위 |
| user knowledge base 권한 생성/수정 | `user_knowledge_permission.created` 또는 `user_knowledge_permission.updated` | MVP 2 `user_knowledge_permissions` table/API 구현 시 row 생성/변경 |
| user knowledge base 권한 회수 | `user_knowledge_permission.deleted` | MVP 2 `user_knowledge_permissions` table/API 구현 시 row 삭제 |
| organization member 초대 생성 | `organization.invite` | 기존 가입 user를 organization member로 초대 |
| organization member 초대 수락 | `organization.member.accept` | 초대받은 user가 본인 초대를 수락 |
| organization member 상태 또는 organization auth_state 변경 | `organization.member.update` | member state 또는 organization auth state 변경 |
| organization member 제거 | `organization.member.remove` | membership soft remove와 같은 transaction의 permission cleanup 시작 |
| organization member 제거 cleanup aggregate | `permission.revoke` | `audit_metadata.reason='organization.member.remove'`와 cleanup count 저장 |
| 권한 차단 | `permission.denied` | API 또는 engine에서 거부 |
| 정책 경고 | `policy.warn` | 실행은 허용하지만 위험 표시 |
| 정책 차단 | `policy.block` | data/model/trace policy로 차단 |
| raw trace 조회 | `trace_payload_access_events` | `view=raw` 조회 성공/실패는 전용 access event table에 기록한다. prompt/completion은 raw view에서 payload kind로 평가될 때 기록된다. redacted/metadata 조회는 현재 이 테이블에 기록하지 않는다. `audit_logs`에 중복 action을 만들지 않는다. |

`audit_logs.status`는 현재 코드 enum에 맞춰 `success` 또는 `failure`만 저장한다. `allow`, `deny`, `warn`, `block`은 `audit_logs.audit_metadata.policy_result`에 저장한다.

권한 변경 event의 `audit_metadata`에는 권한 출처를 구분할 수 있도록 아래 값을 남긴다.

| Key | 값 |
| --- | --- |
| `grant_subject_type` | `team` 또는 `user` |
| `grant_subject_id` | team id 또는 user id |
| `resource_type` | `workflow`, `knowledge_base`, `llm_credential`, `audit` |
| `auth_state` | 부여 또는 회수된 권한 상태 |

## MVP별 적용 범위

### MVP 1

- workflow `read/write/execute`
- deployment 조회/생성/toggle/delete의 workflow `read/deploy/manage` enforcement
- LLM credential `read/use`
- permission helper와 API dependency
- workflow engine LLM node의 credential `use` check
- `user_workflow_permissions`, `user_llm_permissions` additive grant
- permission denied audit
- 이전 deployment 재활성화 시 `deployment.activate_previous` audit action 기록

### MVP 2

- knowledge base `read/write/use` endpoint 전체 정렬
- `user_knowledge_permissions` additive grant 추가
- RAG node knowledge base `use` check의 전체 회귀 고정과 stale legacy path 축소
- organization-wide audit search permission과 audit permission 기반 조회 통합
- 현재 trace redaction/visibility policy와 audit permission model의 관리/검색 통합

### MVP 3

- deploy checklist, version diff, trigger mode 정합성
- dashboard scope filtering
- organization-wide raw trace access 권한을 audit permission model과 통합
- audit visibility 관리 UI/API
- `user_audit_permissions` additive grant 추가

## 확정 완료 항목

현재 본 문서의 RBAC/permission matrix에서 남은 미결 항목은 없다.

| 항목 | 확정 |
| --- | --- |
| connection 권한 | 현재 직접 connector API와 신규 DB source upload는 owner 기준이다. 목표 상태에서는 `secret/manage`를 owner 또는 organization owner/manager로 제한하고, runtime `use`는 consuming workflow/knowledge base 권한으로 허용한다. |
| connection 전용 permission table | MVP 목표 상태에서는 만들지 않는다. 독립 connection 공유가 필요해질 때만 `team_connection_permissions`/`user_connection_permissions`를 별도 schema extension으로 검토한다. |

## 검증 기준

권한 Matrix 구현은 아래 조건을 만족해야 한다.

1. 권한 row가 없으면 접근이 거부된다.
2. `auth_state='none'`이면 접근이 거부된다.
3. workflow `viewer`는 조회만 가능하고 수정/실행/배포는 거부된다.
4. workflow `operator`는 실행 가능하지만 수정/배포는 거부된다.
5. workflow `builder`는 수정/실행 가능하지만 배포는 거부된다.
6. workflow `manager`는 배포와 권한 관리가 가능하다.
7. LLM node 실행 시 credential `use` 권한이 없으면 실행이 차단된다.
8. MVP 2 목표: RAG node 실행 시 knowledge base `use` 권한이 없으면 실행이 차단된다. 현재 코드는 KB owner scope와 일부 RAG endpoint의 약한 id 기반 조회가 섞여 있다.
9. 현재 audit log 조회는 자기 audit log API만 구현되어 있다. organization-wide audit 조회는 목표 상태에서 `team_audit_permissions`의 유효 권한이 없으면 거부하고, MVP 3에서 `user_audit_permissions`를 추가하면 함께 합산한다.
10. 현재 raw trace 조회는 system admin/app owner/workflow effective `manager` 수준과 `trace_visibility_policies`가 모두 허용해야 한다. audit permission 기반 raw trace 조회는 목표 상태다.
11. team `viewer` + user direct `builder`이면 최종 권한은 `builder`다.
12. team `manager` + user direct `viewer`이면 최종 권한은 `manager`다.
13. user direct permission은 team permission을 deny하거나 낮출 수 없다.
14. 권한 차단은 `audit_logs`에 남는다.
15. raw trace 접근 시도는 `trace_payload_access_events`에 남는다.
