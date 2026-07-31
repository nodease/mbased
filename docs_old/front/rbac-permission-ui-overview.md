# RBAC Permission UI Overview

Status: Draft
Authority: Frontend Implementation Guide
Source of Truth: No
Verified Against: feature/mba-84 working tree

## 목적

이 문서는 RBAC와 workflow 권한 UI를 프론트에서 구현할 때의 작업 범위, 관련 화면, 사용자 역할, 제외 범위를 정리한다.

이 문서는 정책의 최종 기준이 아니다. 권한 정책은 `data-model/rbac-permission-policy.md`, RBAC 아키텍처는 `architecture/auth-rbac.md`, API 계약은 `api/organization-rbac.md`와 `api/apps-workflows.md`를 따른다.

## 범위

구현할 범위:

- active organization 선택과 현재 organization context 표시
- organization manager가 team과 member를 확인하는 화면
- organization manager 또는 workflow manager가 workflow 권한을 부여/회수하는 화면
- team workflow permission과 user direct workflow permission 표시
- workflow 권한별 목록, 상세, 편집, 실행, 배포, 권한 관리 UI 제어
- `none`, `viewer`, `operator`, `builder`, `manager` 상태별 readonly, disabled, permission denied UI
- 권한 API 실패 시 사용자에게 이해 가능한 오류 표시

구현하지 않을 범위:

- RBAC 정책 자체 변경
- `team_app_permissions` 같은 신규 권한 테이블 추가
- explicit deny 정책
- knowledge/audit user direct permission UI
- raw audit payload visibility UI
- 백엔드 enforcement 대체

프론트는 사용자의 실수를 줄이기 위해 버튼을 숨기거나 비활성화할 수 있지만, 최종 권한 차단은 항상 백엔드 API가 수행해야 한다.

## 관련 기준 문서

| 문서 | 기준 |
| --- | --- |
| `data-model/rbac-permission-policy.md` | 권한 단계, resource matrix, user direct additive 정책 |
| `architecture/auth-rbac.md` | active organization, RBAC enforcement 구조 |
| `api/organization-rbac.md` | organization, team, permission 관리 API |
| `api/apps-workflows.md` | app/workflow API와 권한 요구사항 |
| `decisions/ADR-202606290145-active-organization-header-context.md` | `X-Organization-Id` header 정책 |
| `decisions/ADR-202606291315-resource-access-403-404-policy.md` | 403/404 노출 정책 |

## 관련 화면

| 화면 | 역할 |
| --- | --- |
| Dashboard sidebar/header | active organization 표시와 전환 진입점 |
| Admin Console | organization manager 전용 member/team/resource/credential/knowledge/audit 관리 |
| Settings | 개인/계정 설정과 member read-only 확인. manager-only 조직 운영 기능은 Admin Console로 이동 |
| Workflow app/module list | 권한 있는 app/workflow만 탐색하거나 권한 부족 상태 표시 |
| Workflow editor | 권한별 readonly, 실행, 저장, 배포 버튼 상태 제어 |
| Workflow report/log page | `read` 권한 기준으로 run/log/stat 조회 |
| Permission denied 화면 | 직접 URL 접근 또는 권한 만료 시 차단 안내 |

현재 코드에서 Admin Console 화면은 `apps/client/app/dashboard/admin/page.tsx`에 있고, Settings 화면은 `apps/client/app/dashboard/settings/page.tsx`에서 credential/activity 중심의 read-only 화면으로 정리되어 있다. Workflow 권한 조회는 `apps/client/app/features/workflow/api/workflowApi.ts`의 `getWorkflowPermission`에서 `/workflows/{workflow_id}/permissions/me`를 호출한다. 이 endpoint의 기본 계약과 `read` 권한 요구사항은 `api/apps-workflows.md`를 따른다. 권한 출처 표시가 필요하면 `api/organization-rbac.md`의 permission 목록 API를 함께 사용한다.

## 사용자 역할

| 사용자 | 프론트 관점 |
| --- | --- |
| Organization owner/manager | organization scope 안에서 manager로 취급되며 team/member/resource permission 관리 가능 |
| Workflow manager | 해당 workflow의 권한 관리, 배포, 수정, 실행 가능 |
| Workflow builder | workflow 조회, 수정, 실행 가능. 권한 관리와 배포는 불가 |
| Workflow operator | workflow 조회와 실행 가능. 수정/배포/권한 관리는 불가 |
| Workflow viewer | workflow 조회만 가능 |
| No permission user | 원칙적으로 workflow 존재를 보여주지 않거나 접근 차단 |

## 사용자 흐름

### 권한 관리 흐름

```text
Admin Console 진입
-> active organization 확인
-> team/member 목록 로드
-> workflow 선택
-> 현재 team/user permission 목록 조회
-> grantee type(team/user), grantee, auth_state 선택
-> 권한 부여 또는 회수
-> 권한 목록 재조회
```

### workflow 사용 흐름

```text
App 또는 workflow 진입
-> workflow metadata 조회
-> 내 workflow permission 조회
-> 권한 상태를 editor/report/store에 반영
-> 권한에 따라 저장/실행/배포/권한관리 UI 제어
```

## 화면 상태

| 상태 | UI 기준 |
| --- | --- |
| loading | organization, team, workflow, permission 로딩 skeleton 또는 spinner |
| empty organization | 접근 가능한 organization 없음 안내 |
| empty team | manager에게 team 생성 CTA 제공 |
| empty permission | 해당 workflow에 명시 권한이 없음을 표시 |
| permission denied | 권한 부족 안내와 돌아가기 제공 |
| readonly | viewer/operator에게 editor 내용은 보이되 수정 controls 비활성화 |
| disabled action | 권한 부족 버튼은 tooltip 또는 inline reason과 함께 disabled |
| API error | HTTP status와 사용자 행동 기준 메시지 표시 |

## API 연동

상세 API 연결은 `rbac-permission-api-integration.md`를 따른다.

프론트 구현에서 필요한 API:

- `GET /api/v1/organizations`
- `GET /api/v1/organizations/current`
- `GET /api/v1/teams`
- `GET /api/v1/teams/{team_id}/members`
- `GET /api/v1/permissions/workflows/{workflow_id}`
- `PUT /api/v1/permissions/workflows/{workflow_id}/teams/{team_id}`
- `DELETE /api/v1/permissions/workflows/{workflow_id}/teams/{team_id}`
- `PUT /api/v1/permissions/workflows/{workflow_id}/users/{user_id}`
- `DELETE /api/v1/permissions/workflows/{workflow_id}/users/{user_id}`

내 권한 조회 API:

- `GET /api/v1/workflows/{workflow_id}/permissions/me`: `api/apps-workflows.md`의 공식 계약이며, workflow `read` 권한이 있는 사용자의 effective permission을 반환한다.

모든 organization/team/permission 관리 API는 active organization을 `X-Organization-Id` header로 전달해야 한다.

## 권한별 동작

권한별 상세 UI는 `rbac-workflow-access-matrix.md`를 따른다.

요약:

| 권한 | 프론트 기본 동작 |
| --- | --- |
| `none` | 목록 숨김 또는 접근 차단 |
| `viewer` | 조회 전용 |
| `operator` | 조회와 실행 가능 |
| `builder` | 조회, 수정, 실행 가능 |
| `manager` | 조회, 수정, 실행, 배포, 권한 관리 가능 |

## 확인 필요

| 항목 | 이유 |
| --- | --- |
| `GET /api/v1/workflows/app/{app_id}`에서 `none` workflow를 목록에서 숨길지 | 정책상 숨김이 자연스럽지만 현재 API는 app read 이후 workflow별 필터링 여부가 명확하지 않음 |
| app list에서 primary workflow 권한이 없는 app을 숨길지 | app 전용 permission table이 없고 primary workflow 권한으로 판정하기 때문 |
| workflow 생성 직후 기본 team 권한 부여 여부 | 생성자 user direct manager는 부여되지만 기존 team 자동 grant 정책은 별도 확인 필요 |

## QA 체크리스트

- [ ] manager는 Admin Console에 접근할 수 있다.
- [ ] manager는 workflow team/user 권한 목록을 볼 수 있다.
- [ ] manager는 team workflow 권한을 부여/수정/회수할 수 있다.
- [ ] manager는 user direct workflow 권한을 부여/수정/회수할 수 있다.
- [ ] viewer는 editor를 읽을 수 있지만 저장/실행/배포할 수 없다.
- [ ] operator는 실행할 수 있지만 저장/배포할 수 없다.
- [ ] builder는 저장/실행할 수 있지만 권한 관리는 할 수 없다.
- [ ] manager는 권한 관리 화면에 진입할 수 있다.
- [ ] 권한 없는 workflow 직접 접근은 차단된다.
