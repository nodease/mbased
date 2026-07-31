# Module List Access Operations UI

Status: Draft
Authority: Frontend Implementation Guide
Source of Truth: No
Verified Against: feature/mba-74 @ PR #131 head

## 목적

이 문서는 `/dashboard/mymodule`의 `내 모듈` 화면을 카드형 모듈 갤러리에서 team 기반 workflow 접근 현황을 확인하고 탐색할 수 있는 운영형 화면으로 바꾸기 위한 프론트 작업 기준을 정리한다.

현재 화면은 `AppCard` grid 중심이라 사용자가 "이 workflow에 내가 왜 접근 가능한지", "어느 team 권한으로 어떤 action이 가능한지", "배포 상태와 최근 실행 상태가 어떤지"를 한 번에 파악하기 어렵다.

이 작업의 목표는 모듈을 예쁘게 나열하는 것이 아니라, 반복 사용자가 빠르게 판단하고 행동할 수 있는 목록 화면을 만드는 것이다.

## 요청 유형 분류

이 문서는 구현 따라하기 문서에 가깝다.

- 화면/API/상태/권한별 동작을 구현 전에 정리한다.
- RBAC 개념 설명은 필요한 만큼만 포함한다.
- 실제 코드 수정은 이 문서 범위에 포함하지 않는다.

## 관련 기준 문서

| 영역 | 문서 |
| --- | --- |
| 프론트 문서 기준 | `docs/front/README.md` |
| Manager/member 홈·설정 분리 | `docs/front/rbac-mvp1-manager-member-home-settings.md` |
| Workflow 권한 UI | `docs/front/rbac-workflow-access-matrix.md` |
| RBAC API 연동 | `docs/front/rbac-permission-api-integration.md` |
| App/Workflow API | `docs/api/apps-workflows.md` |
| RBAC 정책 | `docs/data-model/rbac-permission-policy.md` |
| Active organization | `docs/architecture/auth-rbac.md` |

## 현재 상태

### 화면

현재 `/dashboard/mymodule`은 다음 구조다.

| 위치 | 현재 역할 |
| --- | --- |
| `apps/client/app/dashboard/mymodule/page.tsx` | 운영 summary 목록 렌더링, 검색/권한/배포/실행 필터, row action 제어 |
| `apps/client/app/features/app/api/moduleOperationsApi.ts` | `/apps/operations` 호출과 `AppOperationRow` 응답 정규화 |
| `apps/client/app/features/app/api/appApi.ts` | app 생성/수정/삭제, 상세 조회, deployment 토글 API |

현재 화면 UI는 `/apps/operations`의 서버 query를 사용해 검색/권한/배포/실행 필터와 pagination을 처리한다. 검색어는 300ms debounce 후 적용하고, 필터 select는 즉시 적용한다. 첫 page는 `limit=100&offset=0`으로 가져오고, 더 볼 row가 있으면 `offset`을 증가시켜 `더 보기`로 이어 붙인다.

화면 summary와 목록 count는 현재 로드된 row 기준이다. 전체 total count는 API가 아직 제공하지 않으므로 "전체 시스템 기준"으로 표현하지 않는다.

### 현재 운영 summary 응답으로 가능한 표시

MBA-76 `/apps/operations` 응답 기준으로 프론트가 바로 표시할 수 있는 값은 다음이다.

| 필드 | 화면 의미 |
| --- | --- |
| `app.name` | 모듈명 |
| `app.description` | 모듈 설명 |
| `app.workflow_id` | workflow 진입 기준 |
| `app.owner_name` | 소유자 표시 이름 |
| `app.created_at`, `app.updated_at` | 생성/수정 시각 |
| `permission.auth_state` | 현재 user의 effective workflow 권한 |
| `permission.can_read/write/execute/deploy/manage` | row action 제어 기준 |
| `deployment.state` | `active`, `inactive`, `undeployed` 배포 상태 |
| `deployment.deployment_id`, `deployment.type`, `deployment.is_active` | 배포 토글과 배포 타입 표시 |
| `latest_run.state` | 최근 실행 상태 |
| `latest_run.started_at`, `latest_run.finished_at` | 최근 실행 시각 |

### MBA-74 기준 운영 summary 응답으로 가능한 표시

사용자가 요청한 "어느 team으로서 어떤 권한으로 접근 가능한지"는 `permission_sources`로 표시한다. MBA-74 기준 `/apps/operations.permission_sources`는 `GET /api/v1/workflows/{workflow_id}/permissions/me.sources`와 같은 schema로 team/user direct source를 반환한다.

| 필요한 정보 | API 상태 | 판단 |
| --- | --- | --- |
| effective workflow 권한 | `/apps/operations`의 `permission`으로 가능 | 구현됨 |
| team 권한 출처 | `/apps/operations.permission_sources` | 가능 |
| user direct permission 출처 | `/apps/operations.permission_sources` | 가능 |
| 최근 실행 상태 | `/apps/operations.latest_run`으로 가능 | 구현됨 |
| 오류 상태 | `/apps/operations.latest_run`으로 가능. raw error는 노출하지 않음 | 구현됨 |
| 배포 상세 상태 | `/apps/operations.deployment`으로 가능 | 구현됨 |

## 목표 화면

### 화면 성격

`내 모듈`은 "개요"라는 추상 용어보다 `운영 현황`이 더 적합하다.

이 화면은 사용자가 다음 질문에 답할 수 있어야 한다.

- 내가 접근 가능한 workflow는 무엇인가?
- 각 workflow에서 내가 할 수 있는 action은 무엇인가?
- 이 권한은 어느 team 또는 direct grant에서 온 것인가?
- 지금 배포되어 있는가?
- 최근 실행이 정상인지, 실패가 있는지?
- 누가 소유하거나 관리하는 workflow인가?
- 내가 바로 실행/수정/배포/권한 관리할 수 있는가?

### 권장 레이아웃

| 영역 | 목적 | 형태 |
| --- | --- | --- |
| 상단 헤더 | 페이지 목적과 주요 action | `DashboardPageHeader` 계열 |
| 운영 현황 | 실행 중/오류/배포/권한 상태를 빠르게 훑기 | compact list 또는 metric row |
| 검색/필터 바 | 반복 사용자의 탐색 속도 개선 | 검색 input + 필터 chips/select |
| 모듈 목록 | 카드 대신 정보 밀도 높은 table/list | row 기반 list |
| 상세 drawer | row 클릭 시 권한 출처, 배포, 최근 실행 상세 | drawer 또는 side panel |

카드 grid는 시각적으로는 편하지만 RBAC와 운영 상태를 비교하기에는 정보 밀도가 낮다. MVP1에서는 list/table 형태가 더 적합하다.

## 운영 현황 영역

상단의 "운영 현황"은 개별 카드를 크게 나열하지 말고, 빠르게 훑을 수 있는 리스트 또는 compact metric row로 둔다.

권장 항목은 다음이다.

| 항목 | 의미 | 현재 가능 여부 |
| --- | --- | --- |
| 배포 중 | `deployment.state === "active"` | 가능 |
| 배포 꺼짐 | `deployment.state === "inactive"` | 가능 |
| 미배포 | `deployment.state === "undeployed"` | 가능 |
| 오류 있음 | `latest_run.state === "failed"` | 가능 |
| 내가 워크플로우 수정 가능 | `permission.can_write === true` | 가능 |
| 내가 실행 가능 | `permission.can_execute === true` | 가능 |
| 내가 워크플로우 관리 가능 | `permission.can_manage === true` | 가능 |

예시 문구:

- `배포 중 3`
- `미배포 2`
- `최근 오류 1`
- `워크플로우 수정 가능 4`
- `실행 가능 5`
- `워크플로우 관리 가능 1`

`최근 오류`는 `/apps/operations.latest_run` 기준으로 계산한다. 권한 출처는 `permission_sources`의 team/user direct source label을 표시한다. `permission_sources=[]`이면 source가 없는 override 또는 legacy fallback일 수 있으므로 오류로 보지 않는다.

## 모듈 목록 row 설계

카드 대신 row 기반으로 바꾼다.

| 컬럼 | 표시 내용 | 근거 |
| --- | --- | --- |
| 모듈 | icon, name, description, updated_at | `app` safe summary |
| 소유자 | `app.owner_name` | manager 목록이 아니라 생성자 표시 이름 |
| 내 권한 | `viewer/operator/builder/manager` badge | `permission` |
| 접근 경로 | team 이름 또는 direct grant | `permission_sources` |
| 배포 | 배포 중/꺼짐/미배포, type | `deployment` |
| 실행 상태 | 최근 성공/실패/실행 중 | `latest_run` |
| 마지막 활동 | updated_at 또는 최근 run time | `app.updated_at`, `latest_run.started_at` |
| action | 열기, 실행, 수정, 배포, 권한 관리 | permission boolean |

### Action 노출 기준

| action | 조건 | UI |
| --- | --- | --- |
| 열기 | `can_read` | 기본 row click |
| 실행 | `can_execute` | 버튼 활성 |
| 워크플로우 수정 | `can_write` | 버튼 활성 |
| 배포 생성/활성화 | `can_deploy` | 버튼 활성 |
| 배포 삭제/위험 변경 | `can_manage` | 버튼 활성 |
| 권한 관리 | `can_manage` 또는 organization manager | 버튼 활성 |
| 앱 정보 수정 | organization owner/manager 또는 primary workflow `can_manage` | 버튼 활성 |

권한이 부족한 action은 숨김보다 disabled + tooltip을 우선 검토한다. 단, member에게 manager 전용 관리 action이 과하게 보이면 정보 노이즈가 커지므로 secondary menu 안에 제한적으로 둔다.

## 검색과 필터

### 검색

현재 검색 대상:

- 모듈명
- 설명

후속 검색 대상:

- 소유자 이름
- team 이름

현재 구현은 `/apps/operations?q=`로 모듈명/설명을 서버 검색한다. 소유자 이름과 team 이름 검색은 API query가 지원된 뒤 보강한다.

### 필터

권장 필터는 다음이다.

| 필터 | 값 | 현재 가능 여부 |
| --- | --- | --- |
| 내 권한 | 전체, 실행 가능, 워크플로우 수정 가능, 워크플로우 관리 가능 | 가능 |
| 배포 상태 | 전체, 배포 중, 배포 꺼짐, 미배포 | 가능 |
| 실행 상태 | 전체, 실행 중, 오류만 | 가능 |
| 접근 경로 | 전체, team, direct grant | 표시는 가능. 필터 query는 후속 |
| team | team 목록 | 표시는 가능. team filter query는 후속 |
| 소유자 | 소유자 이름 | `owner_name` 기반 제한적 가능 |
| 마켓 공개 | 전체, 공개, 비공개 | `/apps/operations` safe summary에는 없음. 필요하면 별도 정책 결정 |

기본 필터 추천:

- 검색 input
- 권한 segmented control: `전체`, `실행 가능`, `워크플로우 수정 가능`, `워크플로우 관리 가능`
- 배포 상태 select: `전체`, `배포 중`, `미배포`
- 실행 상태 select: `전체`, `실행 중`, `오류만`

## 권한 출처 표시

### 현재 가능한 최소 표시

현재는 effective permission만 표시한다.

예:

- `조회 가능`
- `실행 가능`
- `편집 가능`
- `관리자`

### 목표 표시

사용자가 원하는 정보는 아래 형태다.

```text
편집 가능
워크플로우 빌더팀 권한
```

또는 direct grant인 경우:

```text
실행 가능
개인 직접 권한
```

복수 team이 같은 workflow 권한을 주는 경우:

```text
관리자
AI 운영팀 외 2개 team
```

### 권한 출처 표시 기준

`GET /api/v1/workflows/{workflow_id}/permissions/me.sources`와 `/apps/operations.permission_sources`는 같은 schema를 사용한다.

예시:

```json
{
  "workflow_id": "...",
  "auth_state": "builder",
  "can_read": true,
  "can_write": true,
  "can_execute": true,
  "can_deploy": false,
  "can_manage": false,
  "sources": [
    {
      "type": "team",
      "team_id": "...",
      "team_name": "워크플로우 빌더팀",
      "auth_state": "builder"
    },
    {
      "type": "user",
      "user_id": "...",
      "auth_state": "operator"
    }
  ]
}
```

표시 기준은 다음과 같다.

- 첫 source가 team이면 `team_name`을 우선 표시한다.
- 첫 source가 user이면 `user_name` 유무와 관계없이 `개인 직접 권한`으로 표시한다.
- 복수 source면 `첫 source label 외 N개`로 축약한다.
- `sources=[]`이면 권한 출처가 없는 override 또는 legacy fallback일 수 있으므로 오류로 보지 않는다.
- `sources=[]`이면 `권한 출처 없음`처럼 중립 라벨로 표시하고, `출처 연동 예정`처럼 미구현 상태로 보이게 하지 않는다.

## 실행 상태 표시

사용자가 말한 "실행중이고 오류고 그런 정보"는 workflow runtime 상태다. MBA-76에서는 `/apps/operations.latest_run`으로 표시한다.

권장 상태:

| 상태 | 의미 |
| --- | --- |
| 실행 중 | 최근 run이 running/pending |
| 정상 | 최근 run이 success |
| 오류 | 최근 run이 failure/error |
| 기록 없음 | run이 없음 |

`latest_run.error_message`는 raw error를 그대로 노출하지 않고, backend가 안전하게 일반화한 문자열만 사용한다.

## API 연동 설계

### 기본 연동

MBA-76 이후 `/dashboard/mymodule`은 `/apps/operations`를 기본 데이터 소스로 사용한다. 이전의 `/apps`, `permissions/me`, row별 run API 조합 adapter는 유지하지 않는다.

```text
GET /api/v1/apps/operations?q={검색어}&capability={execute|write|manage}&deployment_state={active|inactive|undeployed}&run_state={running|failed}&limit=100&offset={offset}
-> AppOperationRow[]
-> 화면 row view model normalize
-> 첫 page는 replace, 더 보기는 append
```

`더 보기` 노출 기준은 응답 row 수로 판단한다. 응답 row 수가 `limit`보다 작으면 다음 page가 없다고 보고 버튼을 숨기고, `limit`과 같으면 다음 `offset` 요청이 가능하다고 본다.

현재 구현에서 가능한 것:

- 카드 grid를 list/table로 전환
- 소유자 이름 표시
- 배포/미배포/배포 off 표시
- effective permission 표시
- 권한 기반 action disabled
- 이름/설명 검색
- 배포 상태 필터
- 권한 필터
- 실행 상태 필터
- 최근 오류/실행 중/기록 없음 표시
- `더 보기` pagination

현재 구현에서 남는 것:

- team 필터
- 권한 출처 검색 query
- 전체 total count 표시. API가 total을 반환하지 않으므로 현재 로드된 범위만 표시한다.

### MBA-76 구현 API

MBA-76에서 다음 화면 전용 summary API를 구현했다. 프론트는 이 API를 기본 데이터 소스로 사용하고, 이전 FE-only 조합 adapter는 유지하지 않는다.

```text
GET /api/v1/apps/operations
```

응답 예시:

```json
[
  {
    "app": {
      "id": "...",
      "name": "고객 문의 분류",
      "description": "...",
      "icon": { "type": "emoji", "content": "📨", "background_color": "#E0F2FE" },
      "workflow_id": "...",
      "owner_name": "어드민",
      "created_at": "...",
      "updated_at": "..."
    },
    "permission": {
      "workflow_id": "...",
      "organization_id": "...",
      "auth_state": "builder",
      "can_read": true,
      "can_write": true,
      "can_execute": true,
      "can_deploy": false,
      "can_manage": false
    },
    "permission_status": "loaded",
    "permission_sources": [
      {
        "type": "team",
        "team_id": "...",
        "team_name": "워크플로우 빌더팀",
        "auth_state": "builder"
      },
      {
        "type": "user",
        "user_id": "...",
        "user_name": "혜연",
        "auth_state": "operator"
      }
    ],
    "deployment": {
      "state": "active",
      "deployment_id": "...",
      "type": "webhook",
      "is_active": true
    },
    "latest_run": {
      "state": "success",
      "run_id": "...",
      "raw_status": "success",
      "started_at": "...",
      "finished_at": "...",
      "error_message": null
    }
  }
]
```

실제 응답 field contract는 `docs/api/apps-workflows.md`의 `AppOperationRow` 계약을 우선한다. 이 API는 FE에서 N+1 permission/run 조회를 줄이고, 검색/필터를 서버로 넘길 수 있게 한다.

## 화면 상태

| 상태 | UI |
| --- | --- |
| loading | table skeleton, 운영 현황 skeleton |
| empty | "접근 가능한 모듈이 없습니다" + 생성 권한이 있으면 새 모듈 CTA |
| no search result | 필터 초기화 버튼 |
| permission fetch partial failure | 해당 row에 `권한 확인 실패` badge |
| deployment unknown | `배포 상태 확인 필요` |
| run status unavailable | 실행 상태 컬럼을 숨기거나 `연동 예정` 표시 |
| API error | 전체 실패 banner와 retry |

## Manager/member 차이

| 기능 | manager | member |
| --- | --- | --- |
| 전체 모듈 조회 | organization scope + read 가능 목록 | read 가능 목록 |
| team 권한 출처 | 표시 | 표시 |
| team 출처 표시 | 표시 가능 | 표시 가능 |
| 권한 관리 action | 가능 | 숨김 |
| 배포 생성/활성화 action | `can_deploy` | 권한 있을 때만 가능 |
| 배포 삭제/위험 변경 action | `can_manage` | 권한 있을 때만 가능 |
| 새 모듈 생성 | organization policy에 따라 가능 | 현재 앱 생성 권한 정책 확인 필요 |

`새 모듈` 버튼은 현재 UI에 항상 보인다. 하지만 RBAC 화면으로 정리하려면 생성 권한 정책을 확인해야 한다. `POST /api/v1/apps`는 active organization scope만 요구하고 생성자에게 manager 권한을 부여한다. 따라서 member에게도 새 모듈 생성을 허용할지, manager/builder로 제한할지는 제품 결정이 필요하다.

## 적용 선택지

### 지금 적용

- `/apps/operations` 기반으로 `내 모듈`을 list/table 중심으로 재구성
- `DashboardPageHeader`, `DashboardPanel` 같은 dashboard 공통 UI 재사용
- 배포 상태, 최근 run 상태, 소유자, 권한 badge 표시
- 검색/권한/배포 필터
- 실행 상태 필터
- 권한별 action enable/disable
- 수정 modal은 목록 safe summary가 아니라 `GET /apps/{app_id}` full 응답으로 열기

### 짧게 소개하고 보류

- team 필터
- 소유자/team/권한 출처 검색 query

이 항목들은 검색/filter query 계약 보강 전까지 정확한 값을 만들기 어렵다.

### 이번 범위에서 제외

- 전체 리브랜딩
- 조직 전환 UI 완성
- organization membership 기반 member 관리
- 전체 total count 표시
- workflow run observability 상세 화면 개편

## 구현 단계 초안

1. `ModuleOperationRow` view model을 `/apps/operations` 응답 기준으로 정의한다.
2. `moduleOperationsApi.listModuleOperations()`에서 `q`, `capability`, `deployment_state`, `run_state`, `limit`, `offset`을 query로 전달한다.
3. 목록 app 요약은 safe summary로 취급하고, 수정 modal을 열 때는 `appApi.getApp(app.id)`로 full `AppResponse`를 다시 조회한다.
4. deployment 상태와 permission 상태로 운영 현황 값을 계산한다.
5. 기존 `AppCard` grid를 운영형 table/list로 교체한다.
6. 검색, 권한 필터, 배포 필터, 실행 필터 변경 시 첫 page부터 다시 조회한다.
7. `더 보기`를 누르면 다음 offset page를 append한다.
8. row action을 permission boolean과 `deployment.deployment_id` 기준으로 제어한다.
9. `permission_sources`가 있으면 team/user direct 접근 경로를 표시하고, 빈 배열이면 source가 없는 override 또는 legacy fallback으로 취급한다.

## QA 체크리스트

- [ ] localStorage에 active organization이 없어도 `/dashboard/mymodule` 직접 진입 시 organization 목록 조회 또는 선택 화면 redirect로 후보를 정한 뒤, `X-Organization-Id`가 준비되면 `/organizations/current`와 org-scoped API를 호출한다.
- [ ] manager는 모듈 목록에서 소유자 이름, 배포 상태, 권한 badge를 볼 수 있다.
- [ ] member는 접근 가능한 모듈만 볼 수 있다.
- [ ] `viewer`는 수정/실행/배포 action이 막힌다.
- [ ] `operator`는 실행 가능, 수정 불가로 표시된다.
- [ ] `builder`는 수정/실행 가능으로 표시된다.
- [ ] `manager`는 권한 관리 action이 가능하다.
- [ ] 미배포 모듈과 배포 중 모듈을 필터링할 수 있다.
- [ ] 검색어로 모듈명/설명을 필터링할 수 있다.
- [ ] 권한 조회 실패 row가 전체 목록을 깨지 않는다.
- [ ] 실행 상태/오류 상태는 `/apps/operations.latest_run` 값 기준으로 표시하고 raw error를 노출하지 않는다.

## 남은 결정

| 결정 | 질문 | 권장 |
| --- | --- | --- |
| 운영 summary API | `/apps/operations`를 기본 데이터 소스로 사용할까? | MBA-76 이후 기본 사용 |
| member의 새 모듈 생성 | member도 app을 만들 수 있는가? | 제품 결정 필요 |
| 실행 상태 연동 | 최근 run을 row별 호출할까, summary API로 묶을까? | `/apps/operations.latest_run` 사용 |
| 목록 형태 | table vs dense list | 운영 화면이면 dense table/list 권장 |

## 구현 요청 프롬프트

아래 프롬프트를 다음 구현 작업 요청으로 사용할 수 있다.

```text
MBA-79 기준 `/dashboard/mymodule` 화면을 `/apps/operations` 기반 운영형 모듈 목록으로 정리해줘.

목표:
- 사용자가 각 모듈/workflow에 대해 어떤 권한으로 접근 가능한지 한눈에 볼 수 있어야 함.
- 카드 grid보다는 정보 밀도 높은 list/table 중심 UI로 바꿀 것.
- 상단에는 "개요" 대신 `운영 현황` 성격의 compact summary를 배치할 것.
- 모듈별 소유자 이름, 배포/미배포/배포 off 상태, 내 effective permission badge를 표시할 것.
- 검색과 필터를 제공할 것.

현재 API로 구현할 범위:
- `GET /api/v1/apps/operations`로 운영 summary row를 조회.
- app 목록, effective permission, 배포 상태, 최근 run 상태는 이 API 응답을 기본 source로 사용할 것.
- MBA-74 이후 권한 출처는 `permission_sources`의 team/user direct source를 사용할 것.
- 목록의 `app`은 safe summary이므로 수정 modal을 열 때는 `GET /api/v1/apps/{app_id}`로 full `AppResponse`를 다시 조회할 것.
- 배포 토글은 `deployment.deployment_id`를 기준으로 수행할 것.
- 소유자 표시는 `app.owner_name`을 사용할 것. `owner_name`은 RBAC `manager` 목록이 아니다.
- 권한 필터: 전체, 실행 가능, 워크플로우 수정 가능, 워크플로우 관리 가능.
- 배포 필터: 전체, 배포 중, 배포 꺼짐, 미배포.
- 실행 필터: 전체, 실행 중, 오류만.
- 검색 대상: 모듈명, 설명. 소유자와 권한 출처 검색은 API query가 지원된 뒤 보강한다.

아직 구현하지 말고 TODO로 남길 범위:
- team 필터.
- 소유자/권한 출처 검색 query.

`permission_sources` schema는 MBA-74 `permissions/me.sources`와 같은 계약을 따른다.

UI/UX 기준:
- SaaS 운영 도구처럼 조용하고 정보 밀도 있게 만들 것.
- landing/marketing hero처럼 크게 꾸미지 말 것.
- 버튼은 권한 boolean에 따라 disabled/hidden을 결정하고, disabled일 때 이유를 tooltip 또는 보조 문구로 설명할 것.
- manager/member 모두 같은 목록을 보되 action 가능 여부가 권한에 따라 달라지게 할 것.
- 직접 QA는 manager `dev@moduly.app`, member `hyeyeon@moduly.app`로 확인할 것.

검증:
- `npx eslint app/dashboard/mymodule/page.tsx app/features/app/api/moduleOperationsApi.ts`
- `npm run build`
- localStorage에서 `moduly_active_organization_id`를 지운 뒤 `/dashboard/mymodule`에 직접 진입하면 organization 목록 조회 또는 선택 화면 redirect로 후보를 정한 뒤, `X-Organization-Id`가 준비된 상태에서 `/organizations/current`와 모듈 API를 호출해야 함. Header 없이 `/organizations/current`나 org-scoped API가 성공해야 한다는 의미가 아님.
- Network에서 `/api/v1/apps/operations`가 `q`, `capability`, `deployment_state`, `run_state`, `limit`, `offset` query로 호출되고 `/api/v1/apps` 기반 조합 adapter 호출이 없는지 확인.
- 100개 초과 목록은 `더 보기`를 눌러 다음 `offset` page를 불러올 수 있어야 함.
```
