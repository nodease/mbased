# RBAC Permission UI QA

Status: Draft
Authority: Frontend Implementation Guide
Source of Truth: No
Verified Against: dev @ ec576b4f24155697aed8843acc6e5a3fc835f7e1

## 목적

이 문서는 RBAC workflow 권한 UI를 QA할 때 필요한 사용자, team, workflow, permission 조합과 기대 결과를 정리한다.

## 범위

포함:

- workflow 권한별 editor/report 동작
- team permission과 user direct permission 합산
- permission management UI
- API error 처리
- active organization context

제외:

- 백엔드 단위 테스트
- DB migration 검증
- LLM credential permission 전체 QA
- audit raw payload permission QA

## 관련 기준 문서

| 문서                                     | 기준           |
| -------------------------------------- | ------------ |
| `rbac-workflow-access-matrix.md`       | 권한별 UI 기대 결과 |
| `rbac-permission-management-flow.md`   | 권한 부여/회수 흐름  |
| `rbac-permission-api-integration.md`   | API와 오류 처리   |
| `data-model/rbac-permission-policy.md` | 최종 권한 계산 방식  |

## 테스트 데이터

권장 최소 데이터:

| 대상                 | 예시                                                             |
| ------------------ | -------------------------------------------------------------- |
| Organization       | `Org A`                                                        |
| Manager user       | `manager@example.com`                                          |
| Viewer user        | `viewer@example.com`                                           |
| Operator user      | `operator@example.com`                                         |
| Builder user       | `builder@example.com`                                          |
| No permission user | `none@example.com`                                             |
| Team               | `Viewer Team`, `Operator Team`, `Builder Team`, `Manager Team` |
| Workflow           | `Workflow A`, `Workflow B`                                     |

## 권한 조합

| Case | Team 권한  | User direct 권한 | 기대 최종 권한 |
| ---- | -------- | -------------- | -------- |
| A    | 없음       | 없음             | none     |
| B    | viewer   | 없음             | viewer   |
| C    | operator | 없음             | operator |
| D    | builder  | 없음             | builder  |
| E    | manager  | 없음             | manager  |
| F    | viewer   | builder        | builder  |
| G    | manager  | viewer         | manager  |
| H    | operator | manager        | manager  |

## 권한별 화면 QA

### none

- [ ] workflow 직접 URL 접근 시 editor가 열리지 않는다.
- [ ] 접근 차단 또는 not found 화면이 표시된다.
- [ ] draft, run, stats API 실패가 사용자에게 과도한 resource 정보를 노출하지 않는다.
- [ ] app 내부 workflow 목록에서 노출 여부는 정책 결정에 맞게 동작한다.

### viewer

- [ ] workflow editor에 진입할 수 있다.
- [ ] graph와 node setting을 볼 수 있다.
- [ ] node 추가/삭제/연결이 disabled 또는 readonly다.
- [ ] draft 저장 버튼을 사용할 수 없다.
- [ ] 테스트 실행 버튼을 사용할 수 없다.
- [ ] run list/detail과 stats를 볼 수 있다.
- [ ] 권한 관리 메뉴를 볼 수 없다.

### operator

- [ ] workflow editor에 진입할 수 있다.
- [ ] graph와 node setting을 볼 수 있다.
- [ ] node 수정과 draft 저장은 불가하다.
- [ ] 테스트 실행은 가능하다.
- [ ] run list/detail과 stats를 볼 수 있다.
- [ ] 배포와 권한 관리는 불가하다.

### builder

- [ ] node 추가/삭제/연결이 가능하다.
- [ ] node setting 수정이 가능하다.
- [ ] draft 저장이 가능하다.
- [ ] 테스트 실행이 가능하다.
- [ ] deployment 생성/활성화는 불가하다.
- [ ] 권한 관리 메뉴를 볼 수 없다.

### manager

- [ ] editor 수정과 실행이 가능하다.
- [ ] deployment 생성/활성화가 가능하다.
- [ ] workflow 권한 관리 화면에 진입할 수 있다.
- [ ] team permission을 부여/수정/회수할 수 있다.
- [ ] user direct permission을 부여/수정/회수할 수 있다.

## 권한 관리 QA

| 시나리오                              | 기대 결과                         |
| --------------------------------- | ----------------------------- |
| manager가 team viewer 권한 부여        | team permission table에 row 추가 |
| manager가 team viewer를 builder로 수정 | row auth_state가 builder로 갱신   |
| manager가 team 권한 회수               | row 삭제                        |
| manager가 user direct builder 부여   | user permission table에 row 추가 |
| manager가 user direct 권한 회수        | row 삭제                        |
| builder가 permission API 접근        | 403 또는 UI 접근 차단               |
| 권한 부여 중 network error             | form state 복구, 오류 표시          |

## Active Organization QA

- [ ] organization 목록이 로드된다.
- [ ] active organization이 없으면 설정 화면에서 명확한 안내를 보여준다.
- [ ] active organization 변경 시 team/member/workflow/permission 데이터가 새로 로드된다.
- [ ] 이전 organization의 permission 데이터가 남아 보이지 않는다.
- [ ] `X-Organization-Id`가 없는 permission 요청은 실패하거나 요청 전 차단된다.

## API Error QA

| Error   | 테스트 방법                              | 기대 UI              |
| ------- | ----------------------------------- | ------------------ |
| 401     | session 만료                          | 로그인 안내 또는 redirect |
| 403     | 권한 없는 user로 저장/실행/permission API 호출 | 권한 부족 안내           |
| 404     | 다른 organization resource id 접근      | not found 또는 접근 차단 |
| 500     | 서버 오류 mock                          | 재시도 안내             |
| Network | gateway 중지                          | 연결 실패 안내           |

## Regression 체크리스트

- [ ] 기존 workflow canvas 조작이 manager/builder에서 그대로 동작한다.
- [ ] viewer/operator readonly 상태에서 keyboard shortcut이 graph를 변경하지 않는다.
- [ ] autosave가 viewer/operator에서 실행되지 않는다.
- [ ] TestSidebar는 viewer에게 실행 CTA를 제공하지 않는다.
- [ ] 권한 변경 후 같은 브라우저에서 새로고침하면 변경된 권한이 반영된다.
- [ ] 권한 변경 후 다른 로그인 사용자 세션에서도 변경된 권한이 반영된다.

## 확인 필요

| 항목                         | 이유                                                       |
| -------------------------- | -------------------------------------------------------- |
| `none` workflow 목록 노출 여부   | 정책과 API 구현 정합성 확인 필요                                     |
| app card 노출 기준             | app 전용 permission table이 없어 primary workflow 기준 여부 확인 필요 |
| 생성 직후 workflow의 team 기본 권한 | 생성자 manager 외 기존 team 자동 grant 정책 확인 필요                  |

## 완료 기준

이 섹션은 RBAC 프론트 작업의 QA acceptance criteria다. 문서 작성 시점에 모든 항목이 통과했다는 검증 기록은 아니며, 실제 완료 여부는 구현 PR, CI, 수동 QA 결과로 확인한다.

- 권한별 QA 시나리오가 모두 통과한다.
- 권한 관리 grant/update/revoke 후 UI와 서버 응답이 일치한다.
- 권한 부족 상태가 버튼 disabled, readonly, 접근 차단 중 의도한 방식으로 표현된다.
- `none`과 `viewer`가 명확히 다르게 동작한다.
- QA 중 발견한 정책 미결정 항목은 별도 이슈 또는 결정 문서로 연결한다.
