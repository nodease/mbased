# Admin Dashboard Requirements

Status: Draft
Related Features: auth, organization, audit-tracing, security-alert, budget-management, cost-optimizer

## Purpose

이미 축적되는 `audit_logs`, `llm_usage_logs`, `workflow_runs`와 영속 Security Alert를 organization 관리자와 권한 있는 감사자가 조회하는 관리자 화면을 제공한다. [PRD](../../PRD.md)의 FR-011~FR-018을 담당한다. 이 feature의 범위는 조회/집계 UI와 그 권한 경계, 권한 신청의 관리자 측 처리, 부여된 App 생성 권한 관리, audit actor에서 Organization/RBAC access-management flow로 연결하는 관리자 표면이다. Actor mutation 정책과 API는 organization/access-management 경계, Security Alert 탐지·lifecycle·전용 API/UI 계약은 [security-alert](../security-alert/requirements.md) feature가 소유한다.

권한 신청의 제출(신청자 측 차단 안내와 신청 폼)은 [organization](../organization/requirements.md) 범위(PRD FR-041)이고, workflow 예산의 설정/수정은 [budget-management](../budget-management/requirements.md) 범위(PRD FR-051)다. 이 feature는 그 결과 데이터를 조회하고 처리하는 표면이다.

## User Stories

- Organization 관리자로서, 누가 언제 무엇을 했는지 audit log를 검색하고 개별 기록의 상세를 확인하고 싶다.
- Organization 관리자로서, audit log의 user actor를 현재 organization member와 연결해 상태와 permission source를 확인하고 필요한 access action을 수행하고 싶다.
- Organization 관리자로서, workflow별 LLM 사용량과 비용을 확인해 비용이 큰 workflow를 찾고 싶다.
- Organization 관리자로서, 멤버의 workflow 생성/배포 권한 신청을 확인하고 승인/거절하고 싶다.
- Organization 관리자로서, 현재 App 생성 권한을 보유한 멤버를 확인하고, 더 이상 필요하지 않은 권한을 회수하고 싶다.
- Organization 관리자로서, 이번 달 조직 LLM 비용과 예산 위험/초과 workflow 개수를 한눈에 확인하고 싶다.
- Organization owner/manager로서, 반복 권한 거부와 정책 차단에서 생성된 위험 신호를 별도 보안 알림 탭에서 확인·조사·해결하고 싶다.
- 감사자로서, 관리 권한 없이도 감사 목적의 audit 조회를 하고 싶다.

## Functional Requirements

- FR-011: audit log를 행위자, action, 대상, 기간으로 검색/필터링하고, 개별 로그의 actor, action, target, status, timestamp를 상세 조회한다. action은 [ADR-0008](../../decisions/ADR-0008-audit-action-naming-standard.md)의 canonical action을 기준으로 하며, "workflow 차단" 같은 사용자 친화 라벨이 필요하면 canonical action에서 파생해 표시한다.
- FR-012: workflow별 LLM 사용량/비용을 집계해 표시한다. 목록 기준은 요청 organization에 App과 Workflow가 모두 속하는 App primary workflow(`apps.workflow_id`) 전체이며, 사용량 원천은 `llm_usage_logs`다. 기존 `total_cost`를 유지하고 `runtime_surface=agent_builder_intent`인 행은 `agent_builder_cost`, NULL과 그 밖의 행은 `workflow_execution_cost`로 구분한다. 두 구분값의 합은 총비용과 같아야 하며 Agent Builder 전용 대시보드는 만들지 않는다. App과 Workflow의 organization이 불일치하거나 primary Workflow를 확인할 수 없으면 해당 row를 반환하지 않는다. 후보 workflow의 usage는 요청 organization과 같은 `organization_id` 또는 legacy/migration `organization_id IS NULL` row만 합산하고, 다른 organization UUID가 명시된 row는 제외한다. 기간 안에 eligible usage row가 없는 workflow도 응답에 포함하고 prompt/completion tokens, `call_count`, `total_cost`와 두 구분 비용은 0으로 반환한다. usage row의 `total_cost`가 NULL인 경우도 0으로 합산한다. 기본 조회 기간은 이번 달이고, 시작/끝 기간 필터를 제공한다. 목록은 총비용 내림차순 정렬을 제공해 비용이 큰 workflow를 바로 찾을 수 있게 하며, 비용이 같은 row는 workflow 이름과 id로 안정적으로 정렬한다. 같은 eligible usage 정책은 조직 구성 member 목록의 이번 달 사용자별 비용 묶음에도 적용하며, membership state는 비용 합계의 포함 조건이 아니다. 비용 집계 표시까지가 이 feature의 범위이며, 모델 비교/최적화 실행은 workflow 문맥의 [cost-optimizer](../cost-optimizer/requirements.md) 범위다 — 대시보드는 해당 workflow로 이동하는 진입만 제공한다.
- FR-013: 검증된 organization과 인증 actor가 있는 `permission.denied` 및 allowlist `policy.block`을 [ADR-0028](../../decisions/ADR-0028-security-alert-detection-and-lifecycle.md)의 versioned 규칙으로 탐지해 영속 Security Alert로 표시한다. 현재 organization owner/manager는 Sidebar open badge와 Admin Dashboard `보안 알림` 탭에서 alert, safe evidence, lifecycle을 관리한다. Audit 전용 `auditor`/`raw_auditor`는 이 권한을 얻지 않는다. 상세 요구사항은 [Security Alert requirements](../security-alert/requirements.md)가 소유한다.
- FR-014: workflow 생성/배포 권한 신청 목록을 조회하고 승인/거절한다. 목록에는 요청자, 요청 권한, 신청 사유를 표시한다. 요청 권한의 실체는 조직 수준 App 생성 능력(`app.create`)이며, 원천은 `permission_requests` 테이블이다 ([ADR-0016](../../decisions/ADR-0016-permission-request-and-app-creation-permission.md)). 승인 시 요청된 권한이 부여되고, 신청 제출/승인/거절은 canonical action `permission_request.created`/`permission_request.approved`/`permission_request.rejected`로 audit에 기록한다 (PRD FR-042, [ADR-0008](../../decisions/ADR-0008-audit-action-naming-standard.md)).
- FR-014 (회수 확장): 부여된 App 생성 권한(`user_app_creation_permissions` row) 보유 목록을 조회하고 개별 회수한다. 목록에는 보유자, 부여자, 부여 시각을 표시한다. 회수는 row 삭제로 표현하고 canonical action `user_app_creation_permission.deleted`로 audit에 기록한다 ([ADR-0016](../../decisions/ADR-0016-permission-request-and-app-creation-permission.md), [ADR-0008](../../decisions/ADR-0008-audit-action-naming-standard.md)). 회수된 사용자의 App 생성은 다시 `403 permission.denied`로 차단되고, 사용자는 권한을 재신청할 수 있다.
- FR-015: 조직의 이번 달 LLM 비용 합계와 예산 위험/초과 workflow 개수를 요약한다. 총비용 카드에는 workflow 실행 비용과 Agent Builder 비용을 함께 구분 표시한다. 예산 위험 판정은 기존 총비용 기준이며 Agent Builder 전용 예산은 만들지 않는다. 예산 사용률(당월 비용 / 예산)이 80% 이상이면 위험, 100%를 초과하면 초과로 판정한다. 비용·예산 위험은 FR-013 Security Alert와 분리한다.
- FR-016: Audit log의 user actor를 클릭하면 current organization member access drawer를 열어 membership state, organization role, team membership, App 생성 권한, workflow/Knowledge Base/LLM credential direct 및 team-inherited permission source를 조회한다. Access profile과 mutation은 organization manager 전용이며, auditor-only 사용자는 기존 audit list/detail만 사용할 수 있다 ([ADR-0023](../../decisions/ADR-0023-audit-actor-access-management-boundary.md)).
- FR-017: Actor access drawer는 suspend/reactivate, desired role별 role set, team membership add/remove, direct permission grant/revoke, App creation grant/revoke를 항목별로 제공한다. Suspended 또는 globally deactivated target은 promotion/add/grant를 제공하지 않고 cleanup action만 허용한다. 모든 action은 confirm dialog와 optional reason을 거치고 한 요청에서 한 항목만 변경한다.
- FR-018: Audit detail은 supported access-management target/action에 대해 target별 allowlist로 생성한 optional change summary를 표시한다. Generic raw before/after, raw payload, secret, hidden resource reference는 표시하지 않는다.

## Policies And Edge Cases

- 대시보드 조회 자체에도 서버(Gateway) 권한 판정이 필요하다 (NFR-001). audit 검색/상세(FR-011)는 audit auth_state `auditor` 이상, raw payload 접근은 `raw_auditor` 이상과 trace visibility policy를 따른다.
- 비용/예산 요약(FR-012, FR-015)과 권한 신청 목록/승인/거절, App 생성 권한 보유 목록/회수(FR-014)는 organization owner/manager 전용이다. `auditor`/`raw_auditor`는 audit 조회(FR-011)만 접근할 수 있다.
- Security Alert 탭, Sidebar summary, alert detail/evidence와 lifecycle mutation(FR-013)은 현재 active organization owner/manager 전용이다. Audit visibility만 있는 `auditor`/`raw_auditor`와 일반 member에게 노출하거나 API를 허용하지 않는다.
- Actor access profile과 모든 access action(FR-016, FR-017)은 ADR-0009의 organization manager 판정을 통과한 caller 전용이다. Audit actor가 clickable user처럼 보여도 `auditor`/`raw_auditor`에게 mutation control을 노출하거나 API를 허용하지 않는다. Auditor-only admin page 노출은 기존 후순위 범위이며, MBA-188이 그 page gate를 확장하지 않는다.
- 조회 범위는 `X-Organization-Id` 요청 organization scope 안으로 제한한다 ([ADR-0009](../../decisions/ADR-0009-active-organization-header-context.md), NFR-002).
- audit metadata의 raw payload, secret 계열 값은 대시보드 응답에 노출하지 않는다 (NFR-004).
- Audit actor id는 scope proof가 아니다. Gateway는 target user의 current organization membership과 변경 대상 team/resource organization을 다시 검증하고 scope 밖 대상은 404로 숨긴다.
- System/null actor와 current organization에서 active/suspended membership이 없는 invited/removed/missing historical actor는 audit detail만 표시하고 access drawer control을 제공하지 않는다.
- 권한 신청 승인은 신청자에게 `user_app_creation_permissions` row를 생성해 조직 수준 App 생성 능력을 부여한다 ([ADR-0016](../../decisions/ADR-0016-permission-request-and-app-creation-permission.md)). 승인 audit은 `permission_request.approved`(신청 처리)와 `user_app_creation_permission.created`(권한 부여)를 각각 기록한다. 배포 권한은 생성자에게 자동 부여되는 workflow manager permission으로 따라오므로 별도 부여가 없다.
- 이미 처리된(승인/거절) 권한 신청에 대한 중복 처리 요청은 거부한다.
- 승인 시점에 신청자가 조직의 active member가 아니면(제거/정지) 승인을 거부한다. 권한 부여와 부여 audit은 발생하지 않는다.
- App 생성 권한 보유 목록은 `user_app_creation_permissions` row 보유자만 포함한다. organization owner/manager는 row 없이 허용되므로([ADR-0016](../../decisions/ADR-0016-permission-request-and-app-creation-permission.md)) 목록에 나타나지 않고 회수 대상이 아니다. 승인 없이 seed로 부여된 row도 목록과 회수 대상에 포함된다.
- 회수는 `permission_requests`의 과거 신청 상태(approved)를 바꾸지 않는다. 신청 이력은 역사 기록으로 남는다.
- 이미 회수됐거나 존재하지 않는 권한 row의 회수 요청은 `404`로 숨긴다 ([ADR-0010](../../decisions/ADR-0010-resource-access-403-404-policy.md) 패턴, resource permission DELETE와 동일).
- 회수된 사용자는 보유 권한과 pending 신청이 없는 상태로 돌아가므로 organization feature의 신청 규칙(ORG-REQ-050)에 따라 재신청할 수 있다.
- 멤버 제거에 따른 일괄 cleanup(aggregate `permission.revoke` audit)과 개별 회수(`user_app_creation_permission.deleted` audit)는 별개 경로다. 개별 회수가 멤버 상태를 바꾸지 않는다.
- 예산이 설정되지 않은 workflow는 예산 위험/초과 판정 대상에서 제외한다. 예산이 0 이하인 경우도 미설정으로 취급한다.
- 시간대 규칙: 저장은 UTC(timestamptz) 그대로 두고, "이번 달" 경계와 예산 위험/초과 판정 같은 집계 경계는 KST(Asia/Seoul) 고정으로 계산한다. FR-011/FR-012의 기간 필터 입력도 KST 기준으로 해석한다. 개별 timestamp의 화면 표시만 사용자 로컬 시간대로 렌더링한다.
- 기간 필터와 집계 경계는 반개구간 `[start, end)`로 판정한다. 시작 시각과 정확히 같은 row는 포함하고, 끝 시각과 정확히 같은 row는 제외한다. 연속한 두 기간을 이어 붙여도 row가 중복되거나 누락되지 않는다.
- 비용 탭은 기간 안의 usage 존재 여부와 무관하게 App primary workflow 전체를 반환한다. 기간 안에 사용량이 없는 workflow는 사용량 0으로 표시한다. organization scope 안에 표시할 primary workflow 자체가 없을 때만 빈 목록 정상 응답으로 처리한다.
- 비용 탭의 App primary workflow는 App과 Workflow의 organization이 모두 요청 organization과 일치해야 한다. usage organization 조건은 outer join의 `ON` 절에서 적용해, 명시적 타 organization usage를 제외하면서 eligible usage가 없는 정상 primary workflow의 0 row를 보존한다.
- 비용 통화는 USD다 (LLM provider 크레딧이 USD 기준). 예산(PRD FR-051)의 통화도 USD를 전제하며, 이 전제는 예산 관리 feature 문서 작성 시 함께 확정한다.
- 비용 표시 자릿수: 노드/단건 상세는 소수점 6자리, workflow별 집계와 조직 합계는 소수점 2자리로 표시한다. 집계는 원본 정밀도(`NUMERIC(10,6)`)로 합산하고 반올림은 표시 직전에 한 번만 적용한다. 예산 사용률의 위험/초과 판정(FR-015)은 반올림 전 값으로 계산한다.
- 비용 집계에서 usage row가 없거나 `total_cost`가 NULL인 row는 0으로 합산한다. 현재 기록 경로는 비용을 산정하지 못해도 NULL이 아니라 `0.0`을 기록하므로, NULL 처리는 legacy/예외 row 방어 목적이다.
- 알려진 한계 (수용): 가격 정보가 없는 모델의 호출은 `total_cost=0.0`으로 기록되어 "실제 비용 0"과 "가격 미산정"이 구분되지 않고, `llm_models`에 등록되지 않은 모델의 호출은 usage log 자체가 남지 않는다. usage log가 남지 않아도 workflow row는 비용 탭에 표시되지만 비용 합계는 0 또는 실제보다 낮은 값으로 보일 수 있다. 미산정 구분 기록(기록 경로 변경)은 이 feature 범위 밖이다.

## Open Questions

- Security Alert의 MVP 탐지 기준과 처리 방식은 ADR-0028에서 해소했다. 인증 전/IP 기반 탐지, 운영 이상과 외부 전달은 Security Alert 후속 범위다.
- (해소) FR-015 응답의 호환용 `ratio` 분모는 활성 예산 workflow 수로 확정했다. 관리 대시보드 카드의 대표 값은 `ratio`가 아니라 `at_risk_count + exceeded_count`이며, 예산 데이터 원천과 판정 규칙은 [budget-management requirements](../budget-management/requirements.md)를 따른다.
