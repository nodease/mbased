# Frontend 문서

Status: Draft
Authority: Frontend Implementation Guide
Source of Truth: No
Verified Against: feature/mba-84 working tree

이 폴더는 Nodease 프론트엔드 작업자가 기능 구현 전에 화면, 상태, API 연결, 권한별 UI 동작을 정리하기 위한 비권위 작업 문서 공간이다.

프론트 문서는 제품 정책이나 API 계약의 최종 기준이 아니다. 정책은 `requirements/`, `architecture/`, `data-model/`, `decisions/`를 따르고, HTTP 계약은 `api/`를 따른다. 이 폴더의 문서는 그 기준들을 실제 화면과 컴포넌트 작업으로 번역하는 역할만 한다.

## 문서 역할

프론트 문서는 보통 백엔드 기능 명세와 다르게 다음 질문에 답해야 한다.

- 사용자는 어떤 화면에서 이 기능을 만나는가?
- 권한, 로딩, 실패, 빈 상태에서 UI가 어떻게 보여야 하는가?
- 어떤 API를 언제 호출하고, 응답을 어떤 화면 상태로 바꾸는가?
- 버튼, 메뉴, 탭, 모달, 토스트, disabled 상태는 어떤 조건으로 바뀌는가?
- 프론트에서 막는 것과 백엔드가 최종 차단하는 것은 어떻게 구분하는가?
- QA가 어떤 사용자 역할과 권한 조합으로 확인해야 하는가?

## 권위 관계

프론트 문서는 아래 문서들을 읽고 구현 단위로 재정리한다.

| 기준 영역                              | 기준 문서                                                               |
| ---------------------------------- | ------------------------------------------------------------------- |
| RBAC 정책                            | `data-model/rbac-permission-policy.md`, `architecture/auth-rbac.md` |
| Organization, team, permission API | `api/organization-rbac.md`                                          |
| App, workflow API                  | `api/apps-workflows.md`                                             |
| MVP 요구사항                           | `requirements/mvp-1-foundation-llmops.md`                           |
| 구현 순서와 이슈                          | `implementation-plan/mvp-1-development-issue-plan.md`               |
| 중요한 정책 변경                          | `decisions/` 아래 Accepted ADR                                        |

프론트 문서와 상위 문서가 충돌하면 이 폴더의 문서를 고친다. 화면에서 필요한 정책이 상위 문서에 없다면 프론트 문서에서 임의로 확정하지 않고, `decisions/` 또는 관련 API/정책 문서에 먼저 반영해야 한다.

## 만들 문서 유형

권장 문서 유형은 다음과 같다.

| 문서 유형       | 목적                                                            | 예시                                   |
| ----------- | ------------------------------------------------------------- | ------------------------------------ |
| 화면/흐름 명세    | 사용자가 어떤 순서로 화면을 사용하고 어떤 상태를 만나는지 정리                           | `rbac-permission-ui-flow.md`         |
| 컴포넌트 작업 명세  | 어떤 컴포넌트를 만들고 어디에 연결할지 정리                                      | `rbac-permission-components.md`      |
| API 연동 명세   | 호출할 API, 요청 시점, 응답 매핑, 에러 처리를 정리                              | `rbac-permission-api-integration.md` |
| 권한별 UI 매트릭스 | viewer/operator/builder/manager/none별 노출, disabled, 접근 차단을 정리 | `rbac-workflow-access-matrix.md`     |
| QA 시나리오     | 실제 테스트할 사용자, 팀, workflow, permission 조합을 정리                   | `rbac-permission-ui-qa.md`           |

처음부터 모든 문서를 나눌 필요는 없다. 작은 기능은 하나의 문서에 화면 흐름, API 연동, QA를 같이 적고, 범위가 커지면 위 유형으로 분리한다.

## 현재 문서

| 문서 | 범위 |
| --- | --- |
| [workflow-canvas-uiux-spec.md](workflow-canvas-uiux-spec.md) | Workflow canvas, node detail view, 변수 칩, 테스트 실행 안정화, 보고 페이지 등 프론트 UI/UX 작업 |
| [workflow-test-run-graph-snapshot-spec.md](workflow-test-run-graph-snapshot-spec.md) | 테스트 실행 시 프론트 graph snapshot 생성, 검증, draft 저장, 실행 요청 흐름 |
| [rbac-permission-ui-overview.md](rbac-permission-ui-overview.md) | RBAC 프론트 작업 범위, 관련 화면, 사용자 역할, 제외 범위 |
| [rbac-workflow-access-matrix.md](rbac-workflow-access-matrix.md) | workflow `none/viewer/operator/builder/manager`별 화면과 action 제어 |
| [rbac-permission-management-flow.md](rbac-permission-management-flow.md) | organization manager 또는 workflow manager의 team/user workflow 권한 부여/회수 흐름 |
| [rbac-permission-api-integration.md](rbac-permission-api-integration.md) | organization/member/team/workflow permission API 연결, active member picker, 상태 반영 기준 |
| [rbac-permission-ui-qa.md](rbac-permission-ui-qa.md) | RBAC workflow 권한 UI QA 시나리오와 기대 결과 |
| [rbac-mvp1-manager-member-home-settings.md](rbac-mvp1-manager-member-home-settings.md) | MBA-71 manager/member 홈·설정 화면 분리와 team 기반 멤버 관리 UX |
| [admin-console-uiux.md](admin-console-uiux.md) | manager-only 관리 콘솔 분리, sidebar `관리`, 상단 탭 IA와 탭별 UI/UX 기준 |
| [module-list-access-operations-ui.md](module-list-access-operations-ui.md) | 내 모듈 화면의 team/RBAC 기반 운영형 목록, 권한/배포/검색·필터 UX |

## 현재 RBAC 프론트 작업 문서

RBAC와 workflow 권한 UI 구현을 위해 현재 정리된 작업 문서는 아래와 같다. 이 목록은 구현 기준 문서 목록이 아니라, 상위 정책/API 문서를 프론트 작업 단위로 나눈 보조 문서 목록이다.

| 문서                                   | 담을 내용                                                                                                  |
| ------------------------------------ | ------------------------------------------------------------------------------------------------------ |
| `rbac-permission-ui-overview.md`     | RBAC 프론트 작업 범위, 관련 화면, 사용자 역할, 구현하지 않을 범위                                                              |
| `rbac-workflow-access-matrix.md`     | `none`, `viewer`, `operator`, `builder`, `manager`별 workflow 목록, 상세, 편집, 실행, 배포, 권한 관리 UI 동작           |
| `rbac-permission-management-flow.md` | organization manager가 team/user에게 workflow 권한을 부여/회수하는 화면 흐름                                           |
| `rbac-permission-api-integration.md` | `/organizations`, organization member, active member picker, `/teams`, `/permissions/workflows` 등 프론트 API 연결 방식 |
| `rbac-permission-ui-qa.md`           | 권한 조합별 테스트 케이스와 기대 결과                                                                                  |
| `rbac-mvp1-manager-member-home-settings.md` | manager/member 홈·설정 화면 분리, team 기반 멤버 관리, member 권한 표시와 제외 범위 |
| `admin-console-uiux.md` | manager-only 관리 콘솔을 Settings에서 분리하는 sidebar/top tab IA와 탭별 UI/UX 기준 |
| `module-list-access-operations-ui.md` | `/dashboard/mymodule`의 운영형 모듈 목록, 권한 badge, 배포 상태, 검색·필터 기준 |

`/api/v1/workflows/{workflow_id}/permissions/me`의 기본 effective permission과 `sources` 계약은 `api/apps-workflows.md`를 따른다. team/direct grant 출처를 보여줄 때는 `sources`를 사용하고, 전체 권한 관리 표가 필요한 화면만 별도 permission 목록 API를 조회한다.

## RBAC 프론트 문서 작성 기준

RBAC 문서는 특히 `none`과 `viewer`를 분리해서 써야 한다.

| 권한         | 프론트 의미                                       |
| ---------- | -------------------------------------------- |
| `none`     | 원칙적으로 workflow 존재를 보여주지 않거나 접근 차단 화면을 보여준다.  |
| `viewer`   | workflow를 볼 수 있지만 수정, 실행, 배포, 권한 관리는 할 수 없다. |
| `operator` | workflow를 볼 수 있고 실행할 수 있지만 수정은 할 수 없다.       |
| `builder`  | workflow를 보고 수정하고 실행할 수 있지만 권한 관리는 할 수 없다.   |
| `manager`  | workflow 권한 관리까지 포함해 전체 조작이 가능하다.            |

목록에서 `none` workflow를 숨길지, 접근 시 403/404 중 무엇을 보여줄지는 프론트 문서에서 임의로 정하지 않는다. 현재 코드와 정책 문서가 불일치할 수 있으므로, 이 부분은 별도 결정 또는 백엔드 이슈와 연결해 기록한다.

## 문서 작성 템플릿

새 프론트 문서는 아래 구조를 기본으로 사용한다.

```md
# 문서 제목

Status: Draft
Authority: Frontend Implementation Guide
Source of Truth: No
Verified Against: <branch> @ <commit>

## 목적

이 문서가 어떤 프론트 작업을 정리하는지 쓴다.

## 범위

구현할 것과 구현하지 않을 것을 나눈다.

## 관련 기준 문서

상위 정책, API, 요구사항 문서를 링크한다.

## 사용자 흐름

사용자가 화면에서 어떤 순서로 행동하는지 쓴다.

## 화면 상태

loading, empty, error, permission denied, readonly, disabled 상태를 쓴다.

## API 연동

호출 API, 호출 시점, 성공/실패 처리, 캐시/상태 반영 방식을 쓴다.

## 권한별 동작

권한별로 보이는 것, 막히는 것, 버튼 상태를 표로 쓴다.

## QA 체크리스트

확인할 시나리오를 짧은 체크리스트로 쓴다.
```

## 작성 원칙

- 프론트 문서는 화면과 사용자 행동을 기준으로 쓴다.
- API request/response의 최종 계약은 `api/` 문서에 둔다.
- 권한 정책의 최종 판단은 `data-model/`, `architecture/`, `decisions/` 문서에 둔다.
- 화면에서 필요한 정책이 비어 있으면 `TODO`로 남기고 담당자 확인이 필요한 질문을 적는다.
- 구현 파일 경로를 쓸 때는 실제 경로를 함께 적는다.
- QA가 바로 테스트할 수 있도록 권한 조합과 기대 결과를 표로 남긴다.
