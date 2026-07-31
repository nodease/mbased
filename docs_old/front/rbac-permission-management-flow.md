# RBAC Permission Management Flow

Status: Draft
Authority: Frontend Implementation Guide
Source of Truth: No
Verified Against: dev @ ec576b4f24155697aed8843acc6e5a3fc835f7e1

## 목적

이 문서는 organization manager 또는 workflow manager가 team/user에게 workflow 권한을 부여, 수정, 회수하는 프론트 화면 흐름을 정리한다. API 계약의 최종 기준은 `api/organization-rbac.md`이며, 이 문서는 화면 흐름과 상태 반영을 설명하는 비권위 작업 문서다.

## 범위

포함:

- workflow permission 목록 조회
- team permission grant/update/revoke
- user direct permission grant/update/revoke
- grantee 선택 UI
- auth_state 선택 UI
- 권한 변경 후 목록 재조회
- 권한 변경 실패 UI

제외:

- LLM credential permission 상세 UX
- team 생성 정책
- user 초대/가입 정책
- audit log 상세 화면
- backend 권한 enforcement 구현

## 관련 기준 문서

| 문서 | 기준 |
| --- | --- |
| `api/organization-rbac.md` | permission API 계약 |
| `data-model/rbac-permission-policy.md` | 권한 부여 주체와 additive user direct 정책 |
| `architecture/auth-rbac.md` | active organization context |

## 사용자 흐름

### workflow team 권한 부여

```text
Settings > Access 진입
-> active organization 확인
-> workflow 선택
-> Team 권한 목록 조회
-> grantee type = team 선택
-> team 선택
-> auth_state 선택
-> 저장
-> PUT /api/v1/permissions/workflows/{workflow_id}/teams/{team_id}
-> 성공 시 권한 목록 재조회
```

### workflow user direct 권한 부여

```text
Settings > Access 진입
-> workflow 선택
-> grantee type = user 선택
-> organization 소속 user 선택
-> auth_state 선택
-> 저장
-> PUT /api/v1/permissions/workflows/{workflow_id}/users/{user_id}
-> 성공 시 user direct 권한 목록 재조회
```

### 권한 회수

```text
권한 row의 remove action 클릭
-> 확인 dialog
-> DELETE permission endpoint 호출
-> 성공 시 목록에서 제거
```

## 화면 구성

| 영역 | 내용 |
| --- | --- |
| Organization summary | 현재 active organization과 manager 여부 |
| Workflow selector | 권한을 관리할 workflow 선택 |
| Team permission table | team name, auth_state, assigned_at, revoke action |
| User direct permission table | user name/email, auth_state, assigned_at, revoke action |
| Grant form | grantee type, grantee select, auth_state select, submit |
| Error banner/toast | 권한 조회/변경 실패 |

## Form 규칙

| 필드 | 규칙 |
| --- | --- |
| resource type | workflow 권한 문서에서는 `workflow` 고정 |
| workflow | 선택 필수 |
| grantee type | `team` 또는 `user` |
| grantee | active organization 안의 team/user만 선택 |
| auth_state | `viewer`, `operator`, `builder`, `manager` 중 하나 |

`none`은 grant form의 선택지로 제공하지 않는다. 권한 회수는 permission row 삭제로 처리한다.

## 권한별 접근

| 사용자 권한 | 접근 |
| --- | --- |
| organization owner/manager | 모든 workflow permission 관리 가능 |
| workflow manager | 해당 workflow permission 관리 가능 |
| builder/operator/viewer | 권한 관리 화면 숨김 또는 접근 차단 |
| none | 접근 차단 |

## API 연동

| 행동 | API |
| --- | --- |
| workflow 권한 목록 조회 | `GET /api/v1/permissions/workflows/{workflow_id}` |
| team 권한 부여/수정 | `PUT /api/v1/permissions/workflows/{workflow_id}/teams/{team_id}` |
| team 권한 회수 | `DELETE /api/v1/permissions/workflows/{workflow_id}/teams/{team_id}` |
| user direct 권한 부여/수정 | `PUT /api/v1/permissions/workflows/{workflow_id}/users/{user_id}` |
| user direct 권한 회수 | `DELETE /api/v1/permissions/workflows/{workflow_id}/users/{user_id}` |

요청 body:

```json
{
  "auth_state": "builder"
}
```

모든 요청은 `X-Organization-Id` header를 포함한다.

## 성공/실패 처리

| 결과 | UI 처리 |
| --- | --- |
| grant 성공 | 목록 재조회, 성공 toast |
| update 성공 | 기존 row의 auth_state 갱신, 성공 toast |
| revoke 성공 | row 제거, 성공 toast |
| 400 validation | form field error |
| 401 | 로그인 만료 처리 |
| 403 | 권한 부족 안내 |
| 404 | 대상 workflow/team/user를 찾을 수 없거나 scope 밖 안내 |
| 500/network | 재시도 안내 |

## User Direct Permission 설명

user direct permission은 team 권한을 낮추는 용도가 아니다.

예:

| Team 권한 | User direct 권한 | 최종 권한 |
| --- | --- | --- |
| viewer | builder | builder |
| manager | viewer | manager |
| operator | 없음 | operator |

따라서 UI에서는 user direct permission을 "개별 추가 권한"으로 설명한다. "개별 제한" 또는 "차단"처럼 보이게 표현하지 않는다.

## 확인 필요

| 항목 | 이유 |
| --- | --- |
| user 목록 API의 organization filter 계약 | `api/organization-rbac.md` 기준 manager 전용 active organization member 목록으로 정리됨. Team member 추가 대상도 이 목록에서 선택한다. |
| workflow selector가 app primary workflow만 보여줄지 모든 workflow를 보여줄지 | `GET /api/v1/apps`와 `GET /api/v1/workflows/app/{app_id}`의 권한 필터링 정책에 영향 |
| 권한 변경 audit를 UI에서 즉시 보여줄지 | 현재 audit tab과 permission UI의 연결 범위 확인 필요 |

## QA 체크리스트

- [ ] manager가 workflow permission 목록을 조회할 수 있다.
- [ ] team 권한을 `viewer`로 부여할 수 있다.
- [ ] 같은 team 권한을 `builder`로 수정할 수 있다.
- [ ] team 권한을 회수할 수 있다.
- [ ] user direct 권한을 부여할 수 있다.
- [ ] user direct 권한을 회수할 수 있다.
- [ ] `none`은 grant select에 나타나지 않는다.
- [ ] 권한 변경 후 목록이 최신 상태로 갱신된다.
- [ ] 권한 없는 사용자가 permission API 호출 시 403 안내를 본다.
