# RBAC Workflow Access Matrix

Status: Draft
Authority: Frontend Implementation Guide
Source of Truth: No
Verified Against: dev @ ec576b4f24155697aed8843acc6e5a3fc835f7e1

## 목적

이 문서는 workflow 권한 상태별로 프론트 UI가 무엇을 보여주고, 무엇을 막고, 어떤 API 실패를 어떻게 처리해야 하는지 정리한다. API 계약의 최종 기준은 `api/` 문서이며, 이 문서는 권한별 화면 동작을 정리하는 비권위 작업 문서다.

권한의 최종 의미는 `data-model/rbac-permission-policy.md`의 Workflow matrix를 따른다.

## 범위

대상 권한:

- `none`
- `viewer`
- `operator`
- `builder`
- `manager`

대상 화면:

- app/workflow 목록
- workflow 상세 및 draft 조회
- workflow editor
- 테스트 실행
- 배포
- 권한 관리
- 실행 로그/통계/LLM trace

## 관련 기준 문서

| 문서 | 기준 |
| --- | --- |
| `data-model/rbac-permission-policy.md` | `none < viewer < operator < builder < manager` 권한 단계 |
| `api/apps-workflows.md` | workflow read/write/execute/deploy API |
| `api/organization-rbac.md` | workflow permission grant/revoke API |
| `decisions/ADR-202606291315-resource-access-403-404-policy.md` | org scope 밖 404, scope 안 권한 부족 403 |

## 권한별 의미

| auth_state | 포함 permission | 프론트 의미 |
| --- | --- | --- |
| `none` | 없음 | 접근 불가. 원칙적으로 존재를 노출하지 않음 |
| `viewer` | `read` | 조회 전용 |
| `operator` | `read`, `execute` | 조회와 실행 가능 |
| `builder` | `read`, `write`, `execute` | 조회, 수정, 실행 가능 |
| `manager` | `read`, `write`, `execute`, `deploy`, `manage` | 배포와 권한 관리까지 가능 |

## 화면별 매트릭스

| 화면/행동 | none | viewer | operator | builder | manager |
| --- | --- | --- | --- | --- | --- |
| app/workflow 목록 노출 | 숨김 권장, 확인 필요 | 표시 | 표시 | 표시 | 표시 |
| workflow metadata 조회 | 차단 | 허용 | 허용 | 허용 | 허용 |
| draft graph 조회 | 차단 | 허용 | 허용 | 허용 | 허용 |
| editor 진입 | 차단 | readonly | readonly | editable | editable |
| 노드 추가/삭제/연결 | 차단 | disabled | disabled | 허용 | 허용 |
| node setting 수정 | 차단 | disabled | disabled | 허용 | 허용 |
| draft 저장 | 차단 | disabled/API 403 | disabled/API 403 | 허용 | 허용 |
| 테스트 실행 | 차단 | disabled/API 403 | 허용 | 허용 | 허용 |
| stream 실행 | 차단 | disabled/API 403 | 허용 | 허용 | 허용 |
| run list/detail 조회 | 차단 | 허용 | 허용 | 허용 | 허용 |
| stats/monitoring 조회 | 차단 | 허용 | 허용 | 허용 | 허용 |
| deployment 목록 조회 | 차단 | 허용 | 허용 | 허용 | 허용 |
| deployment 생성/활성화 | 차단 | disabled | disabled | disabled | 허용 |
| workflow 권한 목록 조회 | 차단 | 숨김 | 숨김 | 숨김 | 허용 |
| team/user 권한 부여/회수 | 차단 | 숨김 | 숨김 | 숨김 | 허용 |

## 사용자 흐름

### workflow 진입

```text
workflow route 진입
-> GET /api/v1/workflows/{workflow_id}
-> GET /api/v1/workflows/{workflow_id}/permissions/me
-> auth_state를 store에 저장
-> editor/report UI를 권한별로 렌더링
```

### 권한 없음

```text
직접 URL 접근
-> GET /api/v1/workflows/{workflow_id}
-> 403 또는 404
-> PermissionDenied 또는 NotFound 화면 표시
```

프론트 문구는 보안을 위해 resource 존재를 과도하게 설명하지 않는다.

권장 문구:

```text
이 워크플로우에 접근할 수 없습니다.
필요한 경우 조직 관리자에게 권한을 요청하세요.
```

## 화면 상태

| 상태 | 처리 |
| --- | --- |
| permission loading | editor action을 잠시 disabled 처리 |
| permission fetch 403 | 접근 차단 화면 |
| permission fetch 404 | not found 화면 또는 접근 차단 화면 |
| auth_state none | 접근 차단 또는 목록 숨김 |
| auth_state viewer/operator | readonly editor |
| auth_state builder/manager | editable editor |

## API 연동

| API | 필요한 권한 | 프론트 사용 |
| --- | --- | --- |
| `GET /api/v1/workflows/{workflow_id}` | `read` | workflow metadata 로드 |
| `GET /api/v1/workflows/{workflow_id}/draft` | `read` | editor graph 로드 |
| `GET /api/v1/workflows/{workflow_id}/permissions/me` | `read` | 내 권한 상태 로드 |
| `POST /api/v1/workflows/{workflow_id}/draft` | `write` | 저장 |
| `POST /api/v1/workflows/{workflow_id}/execute` | `execute` | 실행 |
| `POST /api/v1/workflows/{workflow_id}/stream` | `execute` | 스트리밍 실행 |
| `GET /api/v1/workflows/{workflow_id}/runs` | `read` | 실행 이력 |
| `GET /api/v1/workflows/{workflow_id}/stats` | `read` | 모니터링 |
| deployment create/activate | `deploy` | 배포 |
| permission grant/revoke | `manage` | 권한 관리 |

`permissions/me`는 `api/apps-workflows.md`의 공식 계약에 포함된 endpoint이며 workflow `read` 권한을 요구한다. 따라서 권한이 전혀 없는 사용자는 이 endpoint의 `none` payload를 기대하기보다, 선행 workflow 조회 또는 permission 조회 단계의 403/404를 접근 차단 상태로 처리한다.

## UI 제어 기준

프론트는 권한별로 다음 중 하나를 선택한다.

| 방식 | 사용 위치 |
| --- | --- |
| 숨김 | 권한 관리 메뉴, manager 전용 위험 action |
| disabled | 저장/실행/배포처럼 사용자가 왜 막혔는지 알아야 하는 action |
| readonly | editor에서 graph와 node setting을 볼 수 있지만 수정 불가 |
| route guard | 직접 URL 접근 또는 `none` 상태 |

## 확인 필요

| 항목 | 결정 필요 |
| --- | --- |
| `none` workflow의 app 내부 목록 노출 여부 | 숨김 권장이지만 `GET /api/v1/workflows/app/{app_id}`의 workflow별 권한 필터링 정책 확인 필요 |
| `builder`의 deployment 조회 범위 | 조회는 `read`, 생성/활성화는 `deploy`로 분리되는지 UI에서 명확히 표현 필요 |
| 권한 없는 workflow 접근 시 403 화면과 404 화면 구분 | API 정책은 구분하지만 사용자 메시지는 보안상 통합 가능 |

## QA 체크리스트

- [ ] `none` 사용자는 workflow 상세 URL 접근 시 차단된다.
- [ ] `viewer`는 graph와 logs를 볼 수 있다.
- [ ] `viewer`는 save/test/deploy 버튼을 사용할 수 없다.
- [ ] `operator`는 test/execute를 사용할 수 있다.
- [ ] `operator`는 node 수정과 draft 저장을 할 수 없다.
- [ ] `builder`는 node 수정과 draft 저장을 할 수 있다.
- [ ] `builder`는 권한 관리 메뉴를 볼 수 없다.
- [ ] `manager`는 권한 관리 메뉴와 grant/revoke UI를 볼 수 있다.
- [ ] 권한 API가 403을 반환하면 프론트 state와 버튼 상태가 실패 상태로 돌아간다.
