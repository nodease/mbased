# ADR-0016: 권한 신청과 App 생성 권한 모델

Status: Accepted
Related ADRs: [ADR-0006-accept-rbac-auth-state-and-user-direct-permission](ADR-0006-accept-rbac-auth-state-and-user-direct-permission.md), [ADR-0008-audit-action-naming-standard](ADR-0008-audit-action-naming-standard.md), [ADR-0010-resource-access-403-404-policy](ADR-0010-resource-access-403-404-policy.md)

## 배경

PRD FR-041/FR-014는 workflow 생성/배포 권한이 없는 사용자가 권한을 신청하고 관리자가 승인/거절하는 흐름을 요구한다. 현재 코드에는 이 흐름이 없고, 다음 사실이 설계를 제약한다.

- "새 모듈" 생성은 App 생성(`POST /apps`)이며, 현재 인증과 organization 확인만 하고 권한 검사가 없다. 조직 멤버면 누구나 생성할 수 있다.
- App 생성 시 기본 workflow가 함께 생성되고, 생성자에게 해당 workflow의 manager permission row가 자동 부여된다(`_grant_workflow_manager_permission`). manager는 deploy를 포함하므로 자기 workflow의 배포는 생성 능력에 따라온다.
- 기존 resource permission row(`user_workflow_permissions` 등)는 존재하는 리소스를 가리키는 NOT NULL FK를 가지므로, "아직 만들지 않은 workflow를 만들 권한"을 표현할 수 없다.
- 기존 App에 workflow를 추가하는 경로(`create_workflow`)는 이미 App `manage` 권한을 검사한다.

## 결정

### 1. App 생성 권한 검사 도입

`POST /apps`(App 생성)에 권한 검사를 도입한다. 판정 규칙:

- organization owner/manager는 허용한다.
- 그 외에는 `user_app_creation_permissions`에 (organization, user) row가 있어야 허용한다.
- 차단은 [ADR-0010](ADR-0010-resource-access-403-404-policy.md)에 따라 `403 permission.denied` + audit으로 기록하고, 클라이언트는 이 응답에서 차단 안내와 권한 신청 UI로 연결한다.

기존 App에 workflow를 추가하는 경로는 현행 App `manage` 검사를 유지하며 이 ADR로 변경하지 않는다. 배포 권한은 생성자 자동 manager row로 따라오므로 별도 신청 항목을 만들지 않는다.

### 2. 신규 테이블 `user_app_creation_permissions`

조직 수준 App 생성 능력을 user에게 부여하는 테이블. row 존재가 곧 허용이며, 단일 능력이므로 `auth_state` 컬럼을 두지 않는다.

| 컬럼 | 타입 | 제약 |
| --- | --- | --- |
| id | UUID | PK |
| grantee_organization_id | UUID | NOT NULL, FK→organization.id |
| user_id | UUID | NOT NULL, FK→users.id |
| assigned_by | UUID | NOT NULL, FK→users.id |
| assigned_at | DATETIME | NOT NULL |
| options | JSONB | NOT NULL, 기본 `{}` |
| flags | BIGINT | NOT NULL, 기본 0 |

- UNIQUE (grantee_organization_id, user_id).
- 회수는 row 삭제로 표현한다. 회수 UI는 이번 범위가 아니다. (후속: 관리자 회수 API/UI가 admin-dashboard feature의 FR-014 확장으로 추가되었다. 회수 메커니즘은 이 ADR의 row 삭제 + `user_app_creation_permission.deleted` audit 그대로다.)

### 3. 신규 테이블 `permission_requests`

권한 신청의 저장 모델.

| 컬럼 | 타입 | 제약 |
| --- | --- | --- |
| id | UUID | PK |
| organization_id | UUID | NOT NULL, FK→organization.id |
| user_id | UUID | NOT NULL, FK→users.id — 신청자 |
| requested_permission | VARCHAR | NOT NULL, CHECK: `app.create`만 허용 |
| reason | TEXT | NOT NULL — 신청 사유 |
| status | VARCHAR | NOT NULL, CHECK: `pending / approved / rejected`, 기본 `pending` |
| decided_by | UUID | NULL, FK→users.id — 처리한 관리자 |
| decided_at | DATETIME | NULL |
| created_at / updated_at | DATETIME | NOT NULL |
| options | JSONB | NOT NULL, 기본 `{}` |
| flags | BIGINT | NOT NULL, 기본 0 |

- Partial unique index: (organization_id, user_id, requested_permission) WHERE status = 'pending' — 같은 조직에 처리 대기 신청은 1건만 허용한다.
- 거절된 뒤에는 재신청할 수 있다.
- `requested_permission` 값 확장(새 권한 종류)은 이 ADR 갱신으로 정한다.

### 4. 승인/거절 처리와 audit

- 승인: `permission_requests.status`를 `approved`로 갱신하고 `user_app_creation_permissions` row를 생성한다. audit은 `permission_request.approved`(신청 처리)와 `user_app_creation_permission.created`(권한 부여)를 각각 기록한다.
- 거절: status를 `rejected`로 갱신하고 `permission_request.rejected`를 기록한다.
- 제출: `permission_request.created`를 기록한다.
- 이미 처리된 신청의 중복 처리 요청은 거부한다.

### 5. 기존 데이터 처리

기존 member에 대한 backfill 마이그레이션은 하지 않는다. 실서비스 데이터가 없으므로 기존 DB 데이터는 보존 대상이 아니다. 데모/개발 환경은 seed가 계정별 권한을 구성한다 — 관리자는 owner/manager로 자동 허용, 기존 author 계정은 `user_app_creation_permissions` row를 보유, 신입 계정은 row 없음(신청 흐름 시연용).

### 6. 채택하지 않은 대안

- **`organization_auth_state`에 `builder` 값 확장**: 조직 직급 축(member/manager)에 기능 능력이 섞이고, resource auth_state의 `builder`와 이름이 겹쳐 의미가 혼동된다.
- **Builder team 배정**: team template preset 자동 생성이 미구현이고, "특정 팀 소속 = 조직 수준 능력"이라는 새 판정 규칙이 필요해 team의 의미(preset일 뿐, 실권한은 row)와 충돌한다.
- **기존 resource permission row 재사용**: 생성 권한은 가리킬 리소스가 없어 표현 불가.

## 영향

- [ADR-0008](ADR-0008-audit-action-naming-standard.md) canonical action 표에 `user_app_creation_permission.created/deleted`를 추가한다. `permission_request.created/approved/rejected`는 이미 등록되어 있다.
- [data_model.md](../data_model.md) 계획 테이블에 `permission_requests`, `user_app_creation_permissions`를 추가한다.
- organization feature(FR-041)가 차단/신청 제출과 이 테이블들을 소유하고, admin-dashboard feature(FR-014)가 목록 조회/승인/거절 표면을 소유한다.
- 데모 seed에 계정별 App 생성 권한 구성이 추가된다.
- organization member 제거 시 permission cleanup 대상에 `user_app_creation_permissions`를 포함한다. 승인과 멤버 제거가 경합해도 최종 상태가 정리되는 안전망이며(제거가 나중이면 cleanup이 지우고, 승인이 나중이면 active member 검사가 거부), 기존 aggregate audit(`permission.revoke` + `reason='organization.member.remove'`) 규칙을 따른다.

## 후속 검토

- 승인 흐름에서 `permission_request.approved`와 `user_app_creation_permission.created`가 하나의 트랜잭션에서 각각 기록되는지 테스트한다.
- pending 신청의 동시 승인/거절 경합이 중복 부여나 이중 처리로 이어지지 않는지 테스트한다.
- 클라이언트 차단 팝업이 App 생성 403 응답에서 신청 UI로 자연스럽게 연결되는지 확인한다.
