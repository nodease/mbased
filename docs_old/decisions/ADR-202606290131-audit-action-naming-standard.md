# ADR-202606290131: Audit action naming 표준

Status: Accepted
Authority: Decision
Source of Truth: Yes
Verified Against: dev @ 860ece0dee7cab3925d27f30ea650baf0cb18b4e (PR #138 docs target, 2026-07-01 KST)
Created At: 2026-06-29 01:31 KST
Related ADRs: [ADR-202606271559-audit-log-rag-trace-storage](ADR-202606271559-audit-log-rag-trace-storage.md), [ADR-202606290116-accept-rbac-auth-state-and-user-direct-permission](ADR-202606290116-accept-rbac-auth-state-and-user-direct-permission.md), [ADR-202607010220-rag-answer-trace-usage-correlation-boundary](ADR-202607010220-rag-answer-trace-usage-correlation-boundary.md)

## 배경

Active 문서 일부는 권한 또는 정책으로 workflow 실행이 막힌 사건을 `workflow.blocked`로 표현했다. 반면 RBAC 정책, API 문서, 구현 코드는 resource permission 부족을 `permission.denied`로 기록하고, workflow 실행 자체는 `workflow.execute`로 기록한다.

`workflow.blocked`는 결과 중심 이름이라 차단 원인이 RBAC인지, data/model/trace policy인지, 인증 실패인지 구분하기 어렵다. Audit 검색, dashboard aggregation, 테스트 기준을 안정화하려면 원인 중심 action 이름을 표준화해야 한다.

## 결정

`audit_logs.action`은 원인과 비즈니스 사건을 구분하는 canonical action 문자열로 기록한다.

| 사건 | Canonical action | 도입 시점 |
| --- | --- | --- |
| RBAC/resource permission 부족 | `permission.denied` | MVP 1 |
| team/user resource permission row 생성/수정 | `team_workflow_permission.created/updated`, `user_workflow_permission.created/updated`, `team_llm_permission.created/updated`, `user_llm_permission.created/updated` | MVP 1 현재 구현 |
| team/user resource permission row 회수 | `team_workflow_permission.deleted`, `user_workflow_permission.deleted`, `team_llm_permission.deleted`, `user_llm_permission.deleted` | MVP 1 현재 구현 |
| team knowledge base permission row 생성/수정 | `team_knowledge_permission.created/updated` | 현재 table/listener 기준. KB permission API/enforcement 연결은 MVP 2 범위 |
| team knowledge base permission row 회수 | `team_knowledge_permission.deleted` | 현재 table/listener 기준. KB permission API/enforcement 연결은 MVP 2 범위 |
| user knowledge base permission row 생성/수정 | `user_knowledge_permission.created/updated` | MVP 2 user direct table/API 구현 시 고정 |
| user knowledge base permission row 회수 | `user_knowledge_permission.deleted` | MVP 2 user direct table/API 구현 시 고정 |
| organization member 초대 생성 | `organization.invite` | MVP 2.0 organization membership 현재 구현 |
| organization member 초대 수락 | `organization.member.accept` | MVP 2.0 organization membership 현재 구현 |
| organization member 상태 또는 organization auth_state 변경 | `organization.member.update` | MVP 2.0 organization membership 현재 구현 |
| organization member 제거 | `organization.member.remove` | MVP 2.0 organization membership 현재 구현 |
| data/model/trace policy 차단 | `policy.block` | MVP 2 |
| data/model/trace policy 경고 | `policy.warn` | MVP 2 |
| workflow 실행 시도와 결과 | `workflow.execute` | MVP 1 |
| LLM 호출 | `llm.call` | MVP 1 현재 구현 |
| RAG retrieval 성공 | `rag.retrieve` | MVP 2 목표 |
| RAG Agent answer 요청 | `rag.answer.requested` | RAG 확장 3단계 목표 |
| RAG Agent answer 완료 | `rag.answer.completed` | RAG 확장 3단계 목표 |
| RAG Agent answer 실패 | `rag.answer.failed` | RAG 확장 3단계 목표 |
| RAG Agent answer 취소 | `rag.answer.cancelled` | RAG 확장 3단계 목표 |
| RAG Agent answer retention purge | `rag.answer.purge` | RAG 확장 3단계 목표. aggregate purge 결과만 기록 |
| 인증 전 또는 resource helper 밖의 전역 401/403 | `auth.permission_denied` | MVP 1 |
| deployment 생성 | `workflow.deploy` | MVP 1 |
| deployment 일반 활성/비활성 toggle | `deployment.toggle` | MVP 1 |
| 다른 deployment가 active인 상태에서 이전 deployment 재활성화 | `deployment.activate_previous` | MVP 1 |
| deployment 삭제 | `deployment.delete` | MVP 1 |

`workflow.blocked`는 `audit_logs.action`으로 저장하지 않는다. UI에서 "workflow 차단" 표시가 필요하면 `target_type='workflow'`, `status='failure'`, `action='permission.denied'` 또는 `action='policy.block'`, `audit_metadata.policy_result`를 조합해 파생한다.

Deployment의 기본 권한 enforcement는 MVP 1 구현 기준으로 본다. Deployment 조회/생성/toggle/delete route는 workflow 하위 resource로 판정하며, 각각 workflow `read`, `deploy`, `manage` 권한을 사용한다. MVP 3의 배포 범위는 이 기본 권한이 아니라 deploy checklist, version diff, trigger mode 정합성, operations dashboard 같은 운영 기능 강화다.

별도 rollback API나 rollback 전용 permission은 만들지 않는다. 이전 deployment 재활성화는 기존 `PATCH /deployments/{deployment_id}/toggle` 동작으로 수행하되, 다른 deployment가 이미 active인 상태에서 inactive였던 이전 deployment를 활성화하는 경우에는 `audit_logs.action='deployment.activate_previous'`로 기록한다. 일반 활성/비활성 toggle은 `deployment.toggle`로 기록한다.

## 구현 정합성

현재 코드의 주요 흐름은 이 결정과 같은 방향이다.

- Resource permission helper는 RBAC 거부를 `permission.denied`로 기록한다.
- 전역 HTTP 401/403 handler는 helper에서 이미 기록하지 않은 인증/권한 실패를 `auth.permission_denied`로 기록한다.
- 현재 등록된 `/api/v1/permissions/*` router의 team/user permission grant/update/revoke 흐름은 permission row별 data-change action인 `team_workflow_permission.created/updated/deleted`, `user_workflow_permission.created/updated/deleted`, `team_llm_permission.created/updated/deleted`, `user_llm_permission.created/updated/deleted`를 기록한다.
- Organization member invite/accept/update/remove 흐름은 `organization.invite`, `organization.member.accept`, `organization.member.update`, `organization.member.remove`를 기록한다. Member 제거에 따른 permission cleanup aggregate는 `permission.revoke`에 `reason='organization.member.remove'` metadata를 남긴다.
- Workflow 실행 기록은 `workflow.execute`를 사용하고, 성공/실패는 `audit_logs.status`와 metadata로 표현한다.
- Deployment 생성은 `workflow.deploy`, 일반 toggle은 `deployment.toggle`, 이전 deployment 재활성화는 `deployment.activate_previous`, 삭제는 `deployment.delete`를 사용한다.
- 현재 코드의 `AuditAction` 상수에는 `llm.call`도 구현되어 있다.
- MBA-43 runtime 차단 중 credential 후보 없음, credential `use` 권한 부족, verified credential-model relation 없음, inactive model, Workflow Engine runtime organization scope 누락/invalid는 `permission.denied`로 기록한다. 해당 차단은 `workflow.blocked`로 저장하지 않는다.
- MBA-43은 application-level model restriction policy를 구현하지 않으므로 model restriction 차단에 `policy.block`을 기록하지 않는다. `policy.block`은 기존 canonical action으로 유지하며 document/model/trace policy enforcement가 실제로 연결되는 후속 구현에서 사용한다.
- `policy.warn`, `policy.block`, `rag.retrieve`는 이 ADR에서 MVP 2 목표 action으로 확정한다. MBA-78 1차 구현은 해당 `AuditAction` 상수와 테스트를 먼저 추가하며, `rag.retrieve`는 RAG retrieval 성공 감사에 사용한다. `policy.warn`/`policy.block`의 실제 document metadata policy enforcement 연결은 후속 구현 범위다.
- `rag.answer.*`는 standalone RAG Agent answer의 사용자-facing 실행 lifecycle 감사 action이다. Retrieval 성공 감사인 `rag.retrieve`, provider 호출 감사인 `llm.call`, answer 실행 상태 record인 `rag_answer_runs.status`를 대체하지 않고, answer 요청 단위의 검색/운영 이벤트로만 사용한다.
- `rag_answer_runs.status="blocked"`는 scope 안 resource가 확인된 뒤 policy 또는 permission 때문에 answer delta를 만들지 못한 경우에만 사용한다. 별도 `rag.answer.blocked` action은 만들지 않는다. PII/classification/metadata policy 차단은 `policy.block`, KB/credential/model permission preflight 차단은 `permission.denied`와 answer run status 조합으로 표현한다. `resource.not_found`, scope 밖, organization mismatch, invalid organization header, validation 실패에는 answer run과 lifecycle audit을 만들지 않는다.
- `rag.answer.purge`는 retention purge aggregate event다. 기본 aggregate event는 `target_type='rag_answer_runs'`, `target_id=null`로 기록하고, `audit_metadata`는 `organization_id`, `cutoff`, `purged_count`, `failed_count`, `retryable`, `status` 같은 운영 summary allowlist로 제한한다. Raw answer/query/chunk content는 metadata에 넣지 않는다.

## 영향

- MVP 1 요구사항의 `workflow.blocked` action 표기를 제거하고 `permission.denied`와 `auth.permission_denied`로 분리한다.
- MVP 1에서 permission grant/update/revoke audit을 현재 permission row별 data-change action으로 기록하는 것을 명시한다.
- Organization membership API의 invite/accept/update/remove audit action을 canonical action table에 포함한다.
- MVP 2 knowledge base permission API/enforcement를 구현할 때 grant/update/revoke도 같은 permission row별 data-change action 규칙을 고정한다.
- MVP 2 audit search는 `workflow.blocked`가 아니라 `permission.denied`, `policy.warn`, `policy.block`, `rag.retrieve`를 검색 대상으로 삼는다. RAG Agent answer 운영 검색을 구현할 때는 `rag.answer.requested/completed/failed/cancelled`도 canonical action으로 포함한다.
- Data model의 대표 action convention에 `permission.denied`와 `auth.permission_denied`를 포함한다.
- Deployment API 문서는 기본 권한 enforcement 구현 상태와 MVP 3 운영 기능 강화 범위를 구분한다.

## 후속 검토

- Policy enforcement 구현 시 `policy.block`과 `permission.denied`가 섞이지 않도록 service/helper 경계를 테스트한다.
- RAG retrieval 구현 시 성공 감사 action인 `rag.retrieve`와 trace payload kind인 `rag.retrieval`이 섞이지 않도록 상수와 fixture를 분리한다.
- RAG Agent answer 구현 시 `rag.answer.*` lifecycle action, `rag.retrieve`, `llm.call`, `policy.block`/`permission.denied`, `rag.answer.purge`, `rag_answer_runs.status`가 서로 다른 의미로 기록되는지 테스트한다.
- Audit UI가 "workflow 차단" 같은 사용자 친화 라벨을 canonical action에서 파생해 표시하는지 확인한다.
