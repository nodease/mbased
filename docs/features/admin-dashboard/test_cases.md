# Admin Dashboard Test Cases

Status: Draft

Security Alert FR-013의 상세 rule/worker/API/component/E2E matrix는 [Security Alert test cases](../security-alert/test_cases.md)가 소유한다. 이 문서는 Admin Dashboard 통합 경계와 기존 탭 회귀를 검증한다.

## Security Alert Integration

- Given organization owner/manager, When `/dashboard/admin?tab=security-alerts`로 진입하면, Then `보안 알림` 탭이 감사 로그 앞에 표시되고 전용 목록 API를 사용한다.
- Given audit `auditor`/`raw_auditor` only user, When Admin Dashboard를 열거나 Security Alert API를 호출하면, Then audit list/detail은 기존 권한대로 사용할 수 있지만 Security Alert 탭/API는 허용되지 않는다.
- Given Security Alert deep link의 유효한 `alertId`, When 새로고침하면, Then 같은 tab/detail이 복원된다. Invalid/cross-org ID는 safe 404로 처리한다.
- Given Alert detail에서 `사용자 접근 관리` 선택, When ActorAccessDrawer로 전환하면, Then 두 drawer가 겹치지 않고 기존 organization access-management 정책을 재사용하며 alert를 자동 resolve하지 않는다.
- Given 보안 알림 상세, 감사 로그 상세 또는 ActorAccessDrawer를 열 때, When drawer content를 표시하면, Then 기존 text 계층보다 한 단계 큰 글꼴을 사용하고 최대 폭을 각각 `4xl`, `xl`, `4xl`로 확보한다.
- Given Security Alert 기능 활성화, When audit/비용/권한 탭의 권한 신청·App 생성 권한 카드를 사용하면, Then 기존 API, 권한, pagination, drawer 흐름이 회귀하지 않는다.
- Given 관리자가 상위 탭을 선택한다, When 조직 구성·권한·비용을 포함한 탭이 활성화되면, Then 선택된 탭은 설정 화면과 같은 파란 글자와 파란 밑줄로 표시되고 나머지 탭은 중립 색상을 유지한다.
검증 값은 MBA-188 actor access와 audit detail 확장 case에 적용한다. 기존 비용/권한 신청 case의 기준은 해당 feature 문서와 git history를 따른다.

[requirements.md](requirements.md)의 FR-011~FR-018과 [api_spec.md](api_spec.md), [component_spec.md](component_spec.md)를 검증한다. 신청 제출 측(FR-041)의 인수 조건은 [organization](../organization/requirements.md) 범위이며, 여기서는 관리자 측 흐름과 E2E 연결만 다룬다.

## Acceptance Criteria

### Durable provider usage completeness

- Given 성공한 provider operation의 compatibility usage projection이 아직 생성되지 않았다, When workflow usage와 organization summary를 조회하면, Then canonical ledger 비용과 token/call count가 즉시 한 번만 합산되고 `usage_data_complete=true`다.
- Given 같은 성공 operation의 compatibility usage projection이 존재한다, When 조회하면, Then operation reference가 있는 projection은 legacy 합계에서 제외되어 비용이 두 번 합산되지 않는다.
- Given 기간 안에 `provider_started` 또는 `outcome_unknown` operation이 있다, When 조회하면, Then 확정된 비용은 그대로 반환하고 해당 workflow 및 전체 응답의 `usage_data_complete=false`, `unresolved_provider_call_count`는 미해결 건수를 반환한다.
- Given eligible workflow는 존재하지만 요청 page가 전체 범위를 벗어나 item이 비어 있다, When workflow usage를 조회하면, Then response-level `usage_data_complete`와 `unresolved_provider_call_count`는 빈 page가 아니라 모든 eligible workflow의 전역 상태를 계속 반환한다.
- Given 월 경계 전에 시작한 provider operation의 projection이 다음 달에 늦게 생성된다, When 두 달을 각각 조회하면, Then 비용은 projection 생성 시각이 아니라 `provider_started_at`이 속한 달에만 귀속된다.
- Given 기존 usage와 durable ledger operation이 함께 존재한다, When 조회하면, Then operation reference가 없는 legacy usage와 canonical succeeded operation만 합산하고 raw prompt/completion/provider 오류는 응답하지 않는다.

### AC-1. audit log 검색/상세 (FR-011)

- Given 조직 A의 audit `auditor` 이상 권한 사용자, When `GET /admin/audit-logs`를 호출하면, Then 조직 A scope의 audit log만 `occurred_at` 내림차순으로 반환된다.
- Given 감사 로그 첫 page 응답의 `next_cursor`, When 같은 필터로 다음 page를 조회하면, Then `(occurred_at, id)` 이후 row가 중복 없이 반환되고 후속 page는 COUNT 없이 `total=null`을 반환하며 malformed cursor는 `400`이다. Client는 첫 page의 total snapshot을 유지한다.
- Given 행위자/action/대상/기간/status 필터, When 각각 또는 조합(AND)으로 조회하면, Then 조건에 맞는 row만 반환된다. action 값은 canonical action 문자열 기준이다 ([ADR-0008](../../decisions/ADR-0008-audit-action-naming-standard.md)).
- Given `startAt`/`endAt` 기간 필터, When `occurred_at`이 `startAt`과 정확히 같은 row와 `endAt`과 정확히 같은 row가 있으면, Then 전자는 포함되고 후자는 제외된다 (`[start, end)`).
- Given 개별 로그 상세 조회, When `GET /admin/audit-logs/{id}`를 호출하면, Then actor, action, target, status, timestamp와 allowlist metadata만 반환되고 raw payload/secret 계열 값은 포함되지 않는다.
- Given 저장된 user actor snapshot과 same-organization allowlisted target, When 목록/상세를 조회하면, Then canonical UUID를 유지한 채 actor/target safe display projection을 함께 반환하고 UI는 표시명과 UUID를 병기한다.
- Given snapshot 없는 current member actor 또는 organization/user/team/workflow primary App/App/Knowledge Base target, When 조회하면, Then current organization 범위의 안전한 이름만 반환한다.
- Given 삭제·미확인·미지원·cross-organization target 또는 display resolver 실패, When 조회하면, Then 이름을 노출하지 않고 UUID-only fallback을 유지하며 목록/상세 전체는 정상 응답한다.
- Given 한 page에 같은 target type의 audit가 여러 건, When 목록을 조회하면, Then 표시명은 batch resolution되고 row별 N+1 조회를 만들지 않는다.
- Given allowlisted metadata/change summary UUID, When detail `resolved_references`가 해당 UUID를 안전하게 resolve하면, Then UI는 표시명과 UUID를 병기하고 map에 없는 UUID는 추론하지 않는다.
- Given Security Alert 관리자 API의 `permission.denied`, When 상세를 조회하면, Then 고정 allowlist의 시도한 작업·필요 권한·거부 사유가 사용자 문장으로 표시되고 raw URL/path/query/header/body는 포함되지 않는다.
- Given audit 권한 없는 조직 member, When 검색/상세를 호출하면, Then `403`과 `permission.denied` audit이 기록된다.

### AC-2. workflow별 비용 집계 (FR-012)

- Given 기간 미지정 조회, When `GET /admin/usage/workflows`를 호출하면, Then 이번 달(KST 달력 월) 기준으로 집계된다.
- Given 조직 A에 App primary workflow가 여러 개 있다, When `GET /admin/usage/workflows`를 호출하면, Then 기간 안의 usage 존재 여부와 무관하게 조직 A의 App primary workflow 전체가 반환된다.
- Given 기간 안에 usage row가 없는 workflow, When 집계를 조회하면, Then 해당 workflow는 응답에 포함되고 prompt/completion tokens, `call_count`, `total_cost`, `workflow_execution_cost`, `agent_builder_cost`가 모두 0이다.
- Given 조직 A의 `llm_usage_logs`, When 집계를 조회하면, Then workflow별 합계(prompt/completion tokens, call_count, total_cost)가 원천 row 합산과 일치하고, `total_cost`가 NULL인 row는 0으로 합산된다.
- Given 조직 A primary workflow에 `runtime_surface=agent_builder_intent`인 planner와 repair usage가 있다, When workflow 비용과 조직 월간 비용을 조회하면, Then 두 attempt의 token/cost/call count가 기존 usage와 함께 합산되고 별도 Agent Builder row나 전용 API 없이 기존 응답의 `agent_builder_cost`로 구분된다.
- Given Agent Builder usage가 참조하던 model 또는 credential이 삭제됐다, When 비용을 조회하면, Then NULL이 된 연결 ID와 무관하게 보존된 token/cost가 조직과 workflow 합계에 포함된다.
- Given 조직 A primary workflow에 조직 A usage, NULL organization legacy usage, 조직 B로 명시된 usage가 함께 있다, When 조직 A로 조회하면, Then 조직 A와 NULL usage만 합산하고 조직 B usage는 token, call count, 비용과 budget 상태에서 제외한다.
- Given 조직 A App의 `workflow_id`가 조직 B Workflow를 가리키거나 존재하지 않는 Workflow를 가리킨다, When 조직 A로 조회하면, Then 해당 App은 목록과 `total`에서 제외되고 workflow 이름, usage, budget link를 노출하지 않는다.
- Given 집계 결과, Then 목록은 `total_cost` 내림차순이고, 비용이 같은 row는 workflow 이름/id 순서로 안정 정렬되며, 비용 값은 반올림 없이 원본 정밀도로 반환된다.
- Given 조직 B의 usage 데이터, When 조직 A로 조회하면, Then 조직 B의 workflow는 응답에 포함되지 않는다.

### AC-3. 권한 신청 목록/승인/거절 (FR-014)

- Given 관리자가 Admin Dashboard를 열었을 때, Then 별도 `권한 신청` 메뉴는 없고 신청 목록과 App 생성 권한 보유 목록은 `권한` 탭에 표시된다.
- Given 기존 `/dashboard/admin?tab=permission-requests` 주소로 직접 접근하거나 새로고침했을 때, Then `/dashboard/admin?tab=permissions`로 정규화되고 통합 화면이 표시된다.
- Given pending 신청이 있는 조직, When owner/manager가 `GET /admin/permission-requests`를 호출하면, Then 요청자, 요청 권한(`app.create`), 신청 사유, 신청일이 포함된 pending 목록이 기본 반환된다.
- Given pending 신청, When 승인하면, Then 같은 트랜잭션에서 (1) status가 `approved`로 바뀌고 decided_by/decided_at이 기록되고, (2) 신청자의 `user_app_creation_permissions` row가 생성되고, (3) `permission_request.approved`와 `user_app_creation_permission.created` audit이 각각 기록된다.
- Given 승인된 신청자, When App 생성(`POST /apps`)을 시도하면, Then 성공한다.
- Given pending 신청, When 거절하면, Then status가 `rejected`로 바뀌고 `permission_request.rejected` audit이 기록되며, `user_app_creation_permissions` row는 생성되지 않는다. 거절된 신청자는 재신청할 수 있다.
- Given 이미 처리된(approved/rejected) 신청, When 다시 승인/거절을 요청하면, Then `409`가 반환되고 상태와 권한 row는 변하지 않는다.

### AC-4. 조직 월간 비용/예산 위험 요약 (FR-015)

- Given 조직 A의 이번 달 usage, When `GET /admin/summary`를 호출하면, Then 이번 달(KST) 조직 LLM 비용 합계가 반환된다.
- Given KST 월 경계 근처의 usage row (예: KST 7월 1일 00:30 = UTC 6월 30일 15:30 저장), When 7월 요약을 조회하면, Then 해당 row는 7월 집계에 포함된다.
- Given 활성 예산 workflow가 없는 조직, When 요약을 조회하면, Then `budget` 블록은 null이고 이는 오류가 아니다.
- Given 예산이 설정된 workflow, When 사용률이 80% 이상이면 위험, 100%를 초과하면 초과로 분류되고, 반올림 전 값으로 판정된다. 예산 미설정 workflow는 판정 대상에서 제외된다.
- Given 위험 1개와 초과 1개가 있는 조직, When 상단 예산 카드를 확인하면, Then 대표 값은 비율이 아니라 `2개 위험`이고 노란색 점의 `예산 임박 1`, 빨간색 점의 `예산 초과 1` 텍스트가 표시된다.
- Given 데스크톱에서 상단 요약을 확인, Then `비용·예산`, `조직 구성`, `운영 리소스` 3장이 한 줄로 표시되고 모바일에서는 1열로 전환된다.
- Given 비용·예산 카드를 확인, Then 이번 달 LLM 비용은 `text-2xl` 크기로 표시되고 예산 상태는 같은 카드 안에 표시된다.
- Given 활성 예산은 있지만 위험/초과 workflow가 없는 조직, When 카드를 확인하면, Then `0개 위험`, `예산 임박 0`, `예산 초과 0`이 표시된다.
- Given 비용·예산 카드를 확인, Then 가장 위험한 workflow 미리보기나 개별 workflow 사용액은 표시하지 않는다.
- Given 비용·예산 카드를 키보드 또는 포인터로 선택, Then `/dashboard/admin?tab=usage`로 이동하고 접근 가능한 이름으로 링크 목적이 제공된다.
- Given 조직 구성 카드의 활성 멤버 또는 활성 팀 지표를 선택, Then 각각 `/dashboard/admin?tab=organization-structure&view=members` 또는 `view=teams`로 이동한다.
- Given 운영 리소스 카드의 LLM Credentials 또는 지식 기반 지표를 선택, Then 각각 `/dashboard/admin?tab=credentials` 또는 `/dashboard/admin?tab=knowledge`로 이동한다.

### AC-5. 권한 경계

- Given `auditor`/`raw_auditor` 전용 사용자, Then audit 검색/상세(AC-1)만 접근할 수 있고, usage/summary/permission-requests는 `403`이다. UI의 감사 로그 탭 단독 노출은 후순위다 (데모 시나리오 미사용, component_spec 참조).
- Given audit 권한 없는 일반 member, Then 모든 admin API가 `403`이다.
- Given organization owner/manager, Then 모든 admin API에 접근할 수 있다.
- Given 다른 조직의 `audit_log_id`/`request_id`, When 조회/처리를 시도하면, Then `404`로 존재가 숨겨진다 ([ADR-0010](../../decisions/ADR-0010-resource-access-403-404-policy.md)).

### AC-6. App 생성 권한 보유 목록/회수 (FR-014 회수 확장)

- Given `user_app_creation_permissions` row 보유자가 있는 조직, When owner/manager가 `GET /admin/app-creation-permissions`를 호출하면, Then 보유자(이름/이메일), 부여자, 부여 시각이 포함된 목록이 `assigned_at` 내림차순으로 반환된다. row 없이 허용되는 owner/manager는 목록에 포함되지 않는다.
- Given 보유 row, When `DELETE /admin/app-creation-permissions/{permission_id}`로 회수하면, Then row가 삭제되고 `user_app_creation_permission.deleted` audit이 기록되며, 과거 approved 신청의 상태는 변하지 않는다.
- Given 회수된 사용자, When App 생성(`POST /apps`)을 시도하면, Then `403 permission.denied`로 다시 차단된다.
- Given 회수된 사용자, When 권한을 재신청(`POST /permission-requests`)하면, Then 보유/pending 없음 조건이 재충족되어 pending 신청이 생성된다.
- Given 이미 회수됐거나 다른 조직의 `permission_id`, When 회수를 요청하면, Then `404`로 숨겨지고 `user_app_creation_permission.deleted` audit은 기록되지 않는다.

### AC-7. Audit actor access management (FR-016~FR-018)

- Given organization manager와 current organization user actor, When actor button을 클릭하면, Then actor access drawer가 열리고 membership/role/team/App-creation/direct/team source가 organization scope 안에서만 표시된다.
- Given actor access profile을 표시할 때, When 멤버십 요약을 렌더링하면, Then 상태·조직 역할·계정·유효 접근 값을 영문 API enum이 아닌 `활성`/`정지`, `멤버`/`관리자`, `활성`/`비활성`, `허용`/`차단`으로 표시한다.
- Given target이 여러 team membership을 가짐, When team source 영역을 탐색하면, Then profile은 count만 반환하고 active/inactive row는 stable paginated endpoint로 조회된다.
- Given audit `auditor`/`raw_auditor`만 가진 사용자, When audit API와 access profile/action API를 호출하면, Then audit detail은 조회할 수 있지만 actor API는 `403`이다. Auditor-only admin page 노출은 후순위이며, isolated AuditSearchTab test에서도 management control을 렌더링하지 않는다.
- Given system/null/historical actor, When audit row를 조회하면, Then audit detail은 유지되지만 access management control은 제공되지 않는다.
- Given active member, When suspend action을 confirm하면, Then membership만 suspended가 되고 stored permission row는 유지되며 effective organization access는 fail-closed다.
- Given suspended member, When reactivate action을 confirm하면, Then stored source를 다시 평가하고 canonical audit에 before/after/reason을 기록한다.
- Given suspended member, When role control을 확인하면, Then manager-to-member cleanup은 가능하지만 member-to-manager promotion은 재활성화 전 disabled이고 direct API도 409다.
- Given globally deactivated target, When actor drawer를 열면, Then stored source와 effective disabled를 표시하고 cleanup revoke/remove만 허용한다.
- Given organization manager target, When state-changing resource/team/App-creation action을 시도하면, Then UI는 disabled이고 direct API는 `409 manager_override_active`다. 이미 desired state인 retry는 unchanged일 수 있다.
- Given direct permission과 team source가 함께 있는 member, When direct permission을 revoke하면, Then remaining team source와 effective auth_state가 표시된다.
- Given team membership을 remove하면, Then 해당 team에서 파생된 resource source만 사라지고 unrelated direct/team source는 유지된다.
- Given direct permission restore, When manager가 resource와 canonical auth_state를 선택하면, Then deleted row/AuditLog 자동 복원이 아니라 새 grant/upsert가 발생한다.
- Given access action, When audit recorder add/flush가 실패하면, Then mutation은 rollback되고 UI는 성공 상태로 반영하지 않는다.
- Given supported access-management audit event, When detail을 조회하면, Then target allowlist 기반 `change_summary`만 반환되고 raw before/after/secret/hidden resource는 포함되지 않는다.
- Given historical permission audit, When stored grantee organization이 request organization과 일치하면, Then opaque resource id만 표시하고 current name/path는 resolve하지 않는다. Provenance가 없거나 다르면 summary는 null이다.
- Given concurrent last-two-manager mutation 또는 permission revoke, When 요청이 경합하면, Then 불변식을 지키며 canonical audit은 applied mutation당 정확히 한 건이다.
- Given actor drawer snapshot 이후 global user state, membership identity/role 또는 source row가 바뀜, When 이전 action을 제출하면, Then identity/global-state/ABA mismatch는 no-op보다 먼저 409가 되고 profile을 갱신하며 자동 재시도하지 않는다. Same-row desired-state retry만 unchanged다.
- Given team/resource source가 여러 page에 걸쳐 있음, When catalog item을 선택하면, Then `teamId`/`resourceId` exact 조회로 다른 page의 existing row를 확인하고 row id/auth-state 또는 absence precondition을 구성한다.
- Given team/resource exact 조회가 실패함, When catalog item이 선택되어 있어도, Then 실패를 absence로 해석하지 않고 추가/부여 action을 disabled 처리하며 재조회 control을 제공한다.
- Given access action이 404/409를 반환함, When 최신 profile/source를 재조회하면, Then 기존 confirm dialog와 payload는 폐기되고 사용자가 새 snapshot에서 action을 다시 선택해야 한다.
- Given reason에 common secret/PII pattern 또는 forbidden control/bidi가 포함됨, When action을 제출하면, Then raw reason은 audit/detail에 남지 않고 validation/redaction failure 시 mutation도 성공하지 않는다.
- Given sanitized reason에 HTML/script-like text가 포함됨, When audit detail을 렌더링하면, Then text node로 표시되고 HTML 실행이나 `dangerouslySetInnerHTML` 경로를 사용하지 않는다.
- Given scope 안 actor policy block, When audit detail을 열면, Then `policy.block`, failure status, scoped target user, requested action, machine policy reason와 sanitized optional reason만 safe metadata로 표시된다. Scope가 확인된 opaque resource/team id 외 `change_summary`/name/email/raw request/expected snapshot은 없다.
- Given `summary` 외 allowlist metadata key에 nested object, malformed UUID, unknown resource/policy reason 또는 boolean count가 저장됨, When audit detail을 열면, Then 해당 malformed field는 생략되고 detail 전체가 500으로 실패하지 않는다. 기존 `summary`는 secret-like nested key를 제거하는 sanitized JSON 계약을 유지한다.

### AC-8. Resource permission 표 선택 modal

- Given 관리자가 `권한` 탭에서 `권한 부여`를 선택했을 때, Then resource table, grantee table, permission radio group이 있는 modal이 열린다.
- Given permission card를 확인했을 때, Then modal을 여는 action은 `권한 부여` 하나만 표시되고 selected resource 영역에 같은 역할의 중복 button이 없다.
- Given 관리자가 기존 권한을 조회하거나 회수하려 할 때, When 본문의 `리소스 필터 변경`을 펼치면, Then 리소스 유형과 이름 검색 결과가 현재 선택 조건 아래에 넓게 표시된다.
- Given 본문 리소스 필터에서 결과를 선택했을 때, Then 별도 저장이나 권한 부여 없이 selected resource와 permission 목록이 즉시 해당 리소스로 바뀐다.
- Given resource table에서 이름을 검색하거나 resource type을 변경했을 때, Then 일치하는 현재 organization resource만 표시되고 checkbox로 하나 이상을 선택할 수 있다.
- Given grantee table에서 team/user direct 유형을 변경하거나 이름을 검색했을 때, Then active team 또는 active organization member만 표시되고 checkbox로 하나 이상을 선택할 수 있다.
- Given 이미 선택한 resource 또는 grantee가 검색 결과에서 숨겨졌을 때, Then 숨겨진 선택값으로 권한을 저장할 수 없고 다시 표시하거나 새 항목을 선택해야 한다.
- Given resource 또는 grantee 결과가 많을 때, Then 각 table은 modal 전체 높이를 늘리지 않고 제한된 내부 영역에서 독립적으로 스크롤하며 header를 고정한다.
- Given modal에서 다른 resource/grantee/auth state를 선택하거나 취소했을 때, Then 바깥 page의 selected resource와 permission 목록은 바뀌지 않는다.
- Given bulk grant POST 요청이 진행 중일 때, When 배경/X/취소/Escape로 닫기를 시도하거나 resource/grantee/auth state 입력을 조작하면, Then modal은 닫히지 않고 모든 입력은 disabled 상태를 유지한다.
- Given 복수 resource, 복수 grantee, permission을 선택해 저장했을 때, Then `POST /permissions/bulk-grants`를 한 번 호출하고 성공한 경우에만 modal을 닫고 첫 번째 selected resource permission 목록을 갱신한다.
- Given resource×grantee 선택이 50건을 초과했을 때, Then 저장 action을 비활성화하고 API도 422로 거부한다.

## Unit Tests

단위 테스트는 endpoint/TestClient보다 service/helper method 계약을 우선 검증한다. 아래 class명은 구현 경계의 권장 이름이다. 구현 과정에서 이름이 달라지더라도 동일한 책임 단위가 보존되어야 한다.

### `AdminPermissionGuard`

- `require_audit_reader(db, user, organization_id)`
  - Given organization owner/manager, When audit reader 검사를 수행하면, Then 통과한다.
  - Given audit `auditor`, When audit reader 검사를 수행하면, Then 통과한다.
  - Given audit `raw_auditor`, When audit reader 검사를 수행하면, Then 통과한다.
  - Given audit auth_state가 없고 organization member인 사용자, When audit reader 검사를 수행하면, Then `403` 예외와 `permission.denied` audit 기록 요청을 만든다.
  - Given inactive/suspended/removed membership 또는 비활성화된 team의 audit grant, When audit reader 검사를 수행하면, Then fail-closed로 `403`이다.
  - Given 사용자가 여러 team audit grant를 갖고 있고 첫 row는 `none`, 다른 row는 `auditor` 이상, When audit reader 검사를 수행하면, Then row 순서와 무관하게 통과한다.
  - Given audit reader 권한이 없는 같은 organization scope 사용자, When audit reader 검사를 수행하면, Then `permission.denied` audit은 `resource_type="audit"`, `resource_id=<organization_id>`, `organization_id=<organization_id>`, `permission_action="read"`를 포함하고 전역 `auth.permission_denied` 중복 기록은 억제된다.
- `require_org_manager(db, user, organization_id)`
  - Given organization owner/manager, When manager 검사를 수행하면, Then 통과한다.
  - Given audit `auditor` 또는 `raw_auditor`만 가진 사용자, When manager 검사를 수행하면, Then `403`이다.
  - Given 일반 active member, When manager 검사를 수행하면, Then `403`이다.
  - Given invalid `organization_id`, When manager 검사를 수행하면, Then `400` 또는 validation error로 닫힌다.
- `hide_cross_org_resource(resource_organization_id, requested_organization_id)`
  - Given 같은 organization id, When scope 검사를 수행하면, Then 통과한다.
  - Given 다른 organization id, When scope 검사를 수행하면, Then `404 resource.not_found` 예외를 반환한다.
  - Given resource가 존재하지 않는 경우와 scope 밖 resource인 경우, Then API 계층에서 같은 `404` shape로 매핑된다.

### `AdminAuditLogService`

- `resolve_period(start_at, end_at, default_month=False)`
  - Given timezone offset이 있는 ISO datetime, When 기간을 해석하면, Then UTC 비교 가능한 aware datetime으로 변환한다.
  - Given timezone offset 없는 ISO datetime, When 기간을 해석하면, Then KST(Asia/Seoul)로 해석한다.
  - Given `endAt <= startAt`, When 기간을 해석하면, Then `400`에 매핑 가능한 validation error를 반환한다.
  - Given 시작/끝이 모두 없는 audit 검색, When 기간을 해석하면, Then 기간 필터를 적용하지 않는다.
- `build_audit_filters(actor_id, action, target_type, target_id, status, period)`
  - Given 각 필터 단독 입력, When query filter를 만들면, Then 해당 column 조건만 생성한다.
  - Given 여러 필터 조합, When query filter를 만들면, Then 조건은 OR가 아니라 AND로 결합된다.
  - Given canonical action 문자열, When action filter를 만들면, Then 사용자 친화 label이 아니라 raw action 값으로 비교한다.
  - Given `startAt`과 정확히 같은 `occurred_at`, Then 포함 조건(`>=`)을 만든다.
  - Given `endAt`과 정확히 같은 `occurred_at`, Then 제외 조건(`<`)을 만든다.
- `list_audit_logs(db, user, organization_id, filters, page, limit)`
  - Given 조직 A의 로그와 조직 B의 로그가 섞여 있을 때, When 조직 A로 조회하면, Then 조직 A scope의 row만 반환한다.
  - Given 여러 row, When 조회하면, Then `occurred_at` 내림차순으로 정렬한다.
  - Given `page`/`limit`, When 조회하면, Then `{total, items}`에서 `total`은 필터 전체 건수이고 `items`는 해당 page slice다.
  - Given 결과가 없을 때, When 조회하면, Then `{total: 0, items: []}`를 반환한다.
- `get_audit_log_detail(db, user, organization_id, audit_log_id)`
  - Given 같은 조직의 log id, When 상세를 조회하면, Then actor/action/target/status/timestamp와 sanitized metadata를 반환한다.
  - Given 다른 조직의 log id, When 상세를 조회하면, Then `404`를 반환한다.
  - Given 존재하지 않는 log id, When 상세를 조회하면, Then 다른 조직 id와 구분되지 않는 `404`를 반환한다.
  - Given supported actor access target/action, Then target별 allowlist로 만든 safe `change_summary`를 반환한다.
  - Given unknown target/action 또는 allowlist 밖 before/after field, Then `change_summary`에서 제외한다.
- `sanitize_audit_metadata(metadata)`
  - Given allowlist key(`request_id`, `reason`, `summary`, `organization_id` 등), When sanitize하면, Then 값이 유지된다.
  - Given `raw_payload`, `payload`, `encrypted_config`, `api_key`, `token`, `secret`, `password`, `authorization` 계열 key, When sanitize하면, Then key 또는 value가 응답에서 제거된다.
  - Given nested metadata 안에 secret 계열 key가 있을 때, When sanitize하면, Then 중첩 secret도 제거된다.
  - Given metadata가 `None` 또는 빈 dict, When sanitize하면, Then 빈 dict를 반환한다.

### `AdminUsageService`

- `resolve_month_period_kst(now)`
  - Given KST 2026-07-15, When 이번 달 기간을 계산하면, Then start=`2026-07-01T00:00:00+09:00`, end=`2026-08-01T00:00:00+09:00`이다.
  - Given UTC 저장 row `2026-06-30T15:30:00Z`, When 2026년 7월 KST 기간과 비교하면, Then 포함된다.
  - Given UTC 저장 row `2026-07-31T15:00:00Z`, When 2026년 8월 KST 시작 경계와 비교하면, Then 7월 집계에서는 제외된다.
- `resolve_period(start_at, end_at, default_month=True)`
  - Given 기간 미지정, When usage 조회 기간을 해석하면, Then KST 이번 달을 기본값으로 반환한다.
  - Given offset 없는 `startAt`/`endAt`, When 해석하면, Then KST 기준으로 aware datetime을 만든다.
  - Given `startAt`/`endAt` 중 한쪽만 제공, When 해석하면, Then validation error를 반환한다.
  - Given `endAt <= startAt`, When 해석하면, Then validation error를 반환한다.
- `coalesce_cost(value)`
  - Given `None`, When 비용을 합산 전 정규화하면, Then `Decimal("0")`을 반환한다.
  - Given `Decimal("12.345678")`, When 정규화하면, Then 반올림 없이 같은 값을 반환한다.
- `aggregate_workflow_usage(db, organization_id, period, page, limit)`
  - Given organization scope 안의 App primary workflow 여러 개, When 집계하면, Then usage row 유무와 무관하게 전체 primary workflow가 응답 대상이 된다.
  - Given workflow별 usage row 여러 개, When 집계하면, Then prompt tokens/completion tokens/call_count/total_cost가 원천 row 합산과 일치한다.
  - Given workflow 실행 row와 `runtime_surface=agent_builder_intent` row가 함께 있다, When 집계하면, Then `workflow_execution_cost`와 `agent_builder_cost`로 구분되고 두 값의 합이 `total_cost`와 같다.
  - Given usage row가 없는 workflow, When 집계하면, Then prompt tokens/completion tokens/call_count/total_cost/workflow_execution_cost/agent_builder_cost는 모두 0이다.
  - Given App primary workflow가 있을 때, When 집계 응답 item을 만들면, Then `workflow_name`은 primary workflow를 가리키는 `App.name`이다.
  - Given `total_cost`가 `NULL`인 row, When 집계하면, Then 0으로 합산한다.
  - Given 조직 B UUID가 명시된 usage row와 NULL organization legacy row가 조직 A primary workflow에 함께 있다, When 조직 A로 조회하면, Then 전자는 제외하고 후자는 합산한다.
  - Given App organization은 A지만 primary Workflow organization이 B이거나 Workflow row가 없다, When 조직 A로 조회하면, Then 해당 App은 응답과 `total`에서 제외한다.
  - Given organization eligibility가 없는 usage만 있는 정상 primary workflow, When 조직 A로 조회하면, Then workflow row는 유지하고 사용량 필드는 0이다.
  - Given 집계 결과, When 정렬하면, Then `total_cost` 내림차순이고 동률은 workflow 이름/id 오름차순이다.
  - Given 비용 값 `12.345678`, When 응답 모델을 만들면, Then service 응답은 `12.345678` 원본 정밀도를 유지한다.
  - Given page/limit, When 응답을 만들면, Then `total`은 usage row가 있는 workflow 수가 아니라 응답 대상 App primary workflow 전체 건수이고 `items`는 page slice다.
- `get_organization_summary(db, organization_id, now)`
  - Given 이번 달 usage row, When summary를 조회하면, Then 조직 월간 `total_cost` 합계를 반환한다.
  - Given 이번 달 Agent Builder usage가 있다, When summary를 조회하면, Then 총비용을 유지하면서 workflow 실행 비용과 Agent Builder 비용을 별도 필드로 반환한다.
  - Given `total_cost`가 `NULL`인 row, When summary를 조회하면, Then 0으로 합산한다.
  - Given 활성 예산 workflow가 0개인 조직, When summary를 조회하면, Then `budget`은 `None`이다.
- `classify_budget_usage(total_cost, budget_amount)`
  - Given 사용률이 79.9999%, When 판정하면, Then 정상이다.
  - Given 사용률이 80.0000%, When 판정하면, Then 위험이다.
  - Given 사용률이 100.0000%, When 판정하면, Then 위험이며 초과는 아니다.
  - Given 사용률이 100.0001%, When 판정하면, Then 초과다.
  - Given 예산이 없거나 0 이하, When 판정하면, Then 위험/초과 판정 대상에서 제외한다. 비율의 분모는 활성 예산 workflow 수다.

### `PermissionRequestService`

- `list_requests(db, organization_id, status, page, limit)`
  - Given status 미지정, When 목록을 조회하면, Then `pending` 신청만 기본 반환한다.
  - Given `approved` 또는 `rejected` status, When 목록을 조회하면, Then 해당 상태만 반환한다.
  - Given 조직 A/B 신청이 섞여 있을 때, When 조직 A로 조회하면, Then 조직 A 신청만 반환한다.
  - Given 여러 신청, When 조회하면, Then `created_at` 내림차순으로 정렬한다.
  - Given 목록 item을 만들 때, Then 요청자 id/name/email, `requested_permission`, `reason`, `status`, `created_at`, `decided_by`, `decided_at`을 포함한다.
- `ensure_request_processable(request, organization_id)`
  - Given pending 신청과 같은 organization id, When 처리 가능성을 확인하면, Then 통과한다.
  - Given approved/rejected 신청, When 처리 가능성을 확인하면, Then `409`에 매핑 가능한 conflict를 반환한다.
  - Given 다른 organization id의 신청, When 처리 가능성을 확인하면, Then `404`에 매핑 가능한 not found를 반환한다.
- `ensure_requester_is_active_member(db, request)`
  - Given 신청자가 active member, When 승인 전 검사를 수행하면, Then 통과한다.
  - Given 신청자가 removed/suspended/invited 상태, When 승인 전 검사를 수행하면, Then `409`를 반환한다.
  - Given 신청자 user가 deactivated 된 상태, When 승인 전 검사를 수행하면, Then `409`를 반환한다.
- `approve_request(db, request_id, organization_id, decided_by)`
  - Given pending 신청, When 승인하면, Then 같은 transaction에서 status=`approved`, `decided_by`, `decided_at`을 기록한다.
  - Given pending 신청, When 승인하면, Then `user_app_creation_permissions` row를 생성한다.
  - Given 이미 같은 user/org 권한 row가 있는 비정상 pending 신청, When 승인하면, Then `409`를 반환하고 신청 상태, 권한 row, audit은 변하지 않는다. 정상 제출 경로에서는 기존 권한 row 보유자가 pending 신청을 만들 수 없어야 한다.
  - Given 승인 성공, Then `permission_request.approved`와 `user_app_creation_permission.created` audit 이벤트를 각각 만든다.
  - Given 권한 row 생성 또는 audit 준비 중 실패, When 승인하면, Then transaction은 rollback되어 신청 상태와 권한 row가 남지 않는다.
  - Given 동시 approve/reject 경합, When 한 transaction이 먼저 처리하면, Then 나머지는 `409`이고 중복 권한 row가 없다.
- `reject_request(db, request_id, organization_id, decided_by)`
  - Given pending 신청, When 거절하면, Then status=`rejected`, `decided_by`, `decided_at`을 기록한다.
  - Given 거절 성공, Then `permission_request.rejected` audit 이벤트를 만든다.
  - Given 거절 성공, Then `user_app_creation_permissions` row는 생성하지 않는다.
  - Given rejected 신청자, When 다시 신청 제출 흐름으로 넘어가면, Then pending 중복 규칙만 없다면 재신청 가능해야 한다.
- `grant_app_creation_permission(db, request, decided_by)`
  - Given `requested_permission="app.create"`, When 권한을 부여하면, Then `user_app_creation_permissions`에 organization/user/assigned_by를 기록한다.
  - Given 지원하지 않는 requested permission, When 권한 부여를 시도하면, Then validation error로 닫는다.
  - Given organization/user가 invalid UUID, When 권한 부여를 시도하면, Then 권한 row를 생성하지 않는다.

### `AppCreationPermissionService`

- `list_permissions(db, organization_id, page, limit)`
  - Given 조직 A/B의 권한 row가 섞여 있을 때, When 조직 A로 조회하면, Then 조직 A row만 반환한다.
  - Given 여러 row, When 조회하면, Then `assigned_at` 내림차순으로 정렬한다.
  - Given 목록 item을 만들 때, Then row id, 보유자 id/name/email, `assigned_by`, `assigned_at`을 포함한다.
  - Given row가 없을 때, When 조회하면, Then `{total: 0, items: []}`를 반환한다.
- `revoke_permission(db, permission_id, organization_id, revoked_by)`
  - Given 같은 조직의 보유 row, When 회수하면, Then row를 삭제하고 `user_app_creation_permission.deleted` audit 이벤트를 만든다 (target_type `user_app_creation_permission`, target_id는 row id).
  - Given 회수 성공, Then 해당 사용자의 과거 `permission_requests` 상태(approved)는 변하지 않는다.
  - Given 다른 조직의 row 또는 존재하지 않는 row, When 회수하면, Then `404`에 매핑 가능한 not found를 반환하고 audit을 만들지 않는다.
  - Given 같은 row에 대한 동시 회수 경합, When 한 transaction이 먼저 삭제하면, Then 나머지는 `404`이고 audit은 한 번만 기록된다.

## API Tests

- (FR-011) 검색 필터가 각각, 그리고 조합(AND)으로 동작한다. 정렬은 `occurred_at` 내림차순, pagination은 `page`/`limit`(최대 100)과 `{total, items}` 형식을 따른다.
- (FR-011) 상세 응답에 allowlist metadata만 포함되고 raw payload/secret 값이 없다.
- (FR-018) 상세 응답의 `change_summary`는 정확한 supported target/action 조합과 create/delete/update별 organization provenance를 통과한 safe field만 포함한다. Target만 맞고 action이 다르거나 필요한 provenance가 없으면 null이다.
- (FR-016/FR-017) actor access profile/team-membership/resource list/action은 organization manager 전용이고, paginated team/direct source와 single-action schema를 따른다.
- (FR-012) 기간 미지정 시 이번 달(KST) 기본, 응답의 `period`가 적용 기간을 반환한다. 목록은 App primary workflow 전체를 반환하고, usage가 없는 workflow는 0 row로 포함하며, 정렬은 비용 내림차순과 동률 안정 정렬을 따른다.
- (FR-014) 목록 기본 status 필터가 `pending`이고, `approved`/`rejected` 필터가 동작한다.
- (FR-014) 승인 성공 응답에 `status`, `decided_by`, `decided_at`이 포함된다. 승인/거절의 side effect(AC-3)가 DB와 audit에 반영된다.
- (FR-014) 이미 처리된 신청 재처리 → `409`. 동시 승인/거절 경합은 한쪽만 성공하고 나머지는 `409`를 받는다 (중복 부여 없음).
- (FR-014 회수) 보유 목록이 `page`/`limit`과 `{total, items}` 형식, `assigned_at` 내림차순을 따른다.
- (FR-014 회수) 회수 성공 응답에 `id`, `user_id`가 포함되고, side effect(AC-6)가 DB와 audit에 반영된다.
- (FR-014 회수) 이미 회수됐거나 타 조직의 `permission_id` → `404`.
- 공통: `X-Organization-Id` 누락/invalid → `400`, `endAt ≤ startAt` → `400`, `limit > 100` → `422`, 미인증 → `401`.
- Usage 집계: `startAt`/`endAt` 중 한쪽만 제공 → `400`.
- 공통: 검색 결과 없음은 `{ "total": 0, "items": [] }` 정상 응답이다. 단, usage 조회는 기간 안의 usage 존재 여부가 아니라 App primary workflow 존재 여부가 빈 목록 기준이다.

## E2E Tests

- **PRD 시나리오 1→2 연결 완주**: 권한 없는 신입 계정의 App 생성 차단(403) → 권한 신청 제출 → 관리자가 권한 탭의 신청 카드에서 승인 → 신입 계정 App 생성 성공 → 관리자 audit 탭에서 `permission_request.created/approved`, `user_app_creation_permission.created`, App/workflow 생성 기록 확인.
- **PRD 시나리오 2 완주**: 관리자가 audit 검색으로 권한 신청/승인, workflow 생성/배포/실행 기록을 확인하고, 상단 요약 카드에서 이번 달 조직 비용과 예산 위험/초과 workflow 비율을 확인한다.
- **Audit actor 제어 연결**: 관리자가 audit actor를 열어 member를 정지하고 재활성화한 뒤, 같은 audit tab에서 `organization.member.update`의 optional reason과 safe change summary를 확인한다.
- 비용 탭에서 비용 상위 workflow를 확인하고 해당 workflow 화면으로 이동한다 (진입만 — 비교/최적화는 cost-optimizer 범위).
- 승인 흐름 UI: 승인 버튼 → 확인 다이얼로그(요청자/권한/사유 표시) → 확정 → 성공 toast → 목록에서 pending 제거.
- 이미 처리된 신청을 다른 세션에서 재처리 → "이미 처리된 신청" 안내 후 목록 갱신.
- 회수 흐름 UI: 보유 권한 섹션의 회수 버튼 → 확인 다이얼로그(보유자/권한 표시) → 확정 → 성공 toast → 보유 목록에서 제거.
- **회수→재차단→재신청 연결**: 승인으로 권한을 얻은 사용자의 권한을 관리자가 회수 → 해당 사용자의 App 생성이 다시 403으로 차단 → 권한 신청 UI에서 재신청 성공 → 관리자 audit 탭에서 `user_app_creation_permission.deleted`와 새 `permission_request.created` 기록 확인.
- 이미 회수된 권한을 다른 세션에서 재회수 → "이미 회수된 권한" 안내 후 목록 갱신.

## Permission Tests

- `auditor` 사용자: audit 검색/상세 200, usage/summary/permission-requests/app-creation-permissions 전부 403 (`permission.denied` audit 기록).
- `auditor`/`raw_auditor` 사용자: actor access profile/team-membership/resource list/action도 403이며 audit visibility를 mutation capability로 사용하지 않는다.
- audit 권한 없는 일반 member: 모든 admin API 403.
- organization owner/manager: 모든 admin API 200.
- 다른 organization의 audit/usage/신청 데이터가 응답에 포함되지 않고, 타 조직 id 직접 조회는 404다.
- raw payload 조회는 admin API로 불가능하다 — `raw_auditor`의 `view_raw`는 trace visibility policy 경로에서만 판정된다.
- (후순위) UI: `auditor` 로그인 시 감사 로그 탭만 렌더링되고 요약 카드가 표시되지 않는다 (프론트 노출 제어는 보조이며, API 403이 최종 경계임을 함께 검증). auditor 전용 노출 제어 구현 시 복원한다.

## Edge Cases

- audit/permission 검색 결과가 없는 기간/필터 조합 → 빈 목록 정상 응답, UI는 empty state 표시.
- usage 조회에서 기간 안에 사용량이 없는 workflow → 빈 목록이 아니라 사용량 0 row로 표시. App primary workflow 자체가 없을 때만 empty state 표시.
- `audit_metadata`에 저장된 secret 계열 값이 목록/상세 어디에도 노출되지 않는다 (NFR-004).
- Generic AuditLog before/after에 allowlist 밖 field나 nested secret이 있어도 `change_summary`에 포함되지 않는다. Update의 before/after 한쪽 organization provenance가 누락되거나 불일치해도 partial summary를 만들지 않는다.
- Actor id가 user UUID여도 current organization membership이 없으면 access control을 제공하지 않고 cross-org 정보는 404로 숨긴다.
- Access action no-op은 audit을 만들지 않고, audit add/flush 실패는 mutation을 rollback한다.
- Access action common membership/source precondition 누락·혼합은 422이고 row ABA는 409다.
- 가격 미등록 모델의 usage(`total_cost=0.0`)는 집계에 0으로 반영된다 — "미산정 구분 불가"는 수용된 한계이며 테스트는 0 합산 동작만 검증한다.
- 예산 `budget` null 상태에서 UI 요약 카드가 "예산 미설정"을 표시한다 (오류 아님).
- 승인 시점에 신청자가 조직의 active member가 아니면(제거/정지) 승인이 `409`로 거부되고, 권한 row와 audit(`user_app_creation_permission.created`)이 생성되지 않는다.
- 멤버 제거 시 해당 user의 `user_app_creation_permissions` row가 permission cleanup으로 삭제되고, 이후 그 user의 App 생성은 다시 차단된다 (승인·제거 경합의 최종 상태 정리 — [ADR-0016](../../decisions/ADR-0016-permission-request-and-app-creation-permission.md)).
- 멤버 제거 cleanup(aggregate `permission.revoke` audit)과 개별 회수(`user_app_creation_permission.deleted` audit)는 audit action이 서로 다르고, 개별 회수는 멤버 상태를 바꾸지 않는다.
- 개별 회수 후 멤버 제거가 이어져도 cleanup은 이미 없는 row를 중복 삭제하지 않는다 (cleanup count에 미포함).
- timestamp 표시는 사용자 로컬 시간대, `<time datetime>`은 ISO 값을 유지한다.
