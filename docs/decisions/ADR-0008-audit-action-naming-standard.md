# ADR-0008: Audit action naming 표준

Status: Accepted
Date: 2026-06-29 01:31 KST
Original: ADR-202606290131-audit-action-naming-standard
Verified Against: dev @ 860ece0dee7cab3925d27f30ea650baf0cb18b4e (PR #138 docs target, 2026-07-01 KST)
Related ADRs: [ADR-0004-audit-log-rag-trace-storage](ADR-0004-audit-log-rag-trace-storage.md), [ADR-0006-accept-rbac-auth-state-and-user-direct-permission](ADR-0006-accept-rbac-auth-state-and-user-direct-permission.md), [ADR-0013-rag-answer-trace-usage-correlation-boundary](ADR-0013-rag-answer-trace-usage-correlation-boundary.md), [ADR-0070](ADR-0070-organization-detector-provider-and-pre-embedding-local-masking-boundary.md)

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
| team knowledge base permission row 생성/수정 | `team_knowledge_permission.created/updated` | KB permission API/enforcement 현재 구현 |
| team knowledge base permission row 회수 | `team_knowledge_permission.deleted` | KB permission API/enforcement 현재 구현 |
| user knowledge base permission row 생성/수정 | `user_knowledge_permission.created/updated` | user direct KB permission API/enforcement 현재 구현 |
| user knowledge base permission row 회수 | `user_knowledge_permission.deleted` | user direct KB permission API/enforcement 현재 구현 |
| organization member 초대 생성 | `organization.invite` | MVP 2.0 organization membership 현재 구현 |
| organization member 초대 수락 | `organization.member.accept` | MVP 2.0 organization membership 현재 구현 |
| organization member 상태 또는 organization auth_state 변경 | `organization.member.update` | MVP 2.0 organization membership 현재 구현 |
| organization member 제거 | `organization.member.remove` | MVP 2.0 organization membership 현재 구현 |
| 권한 신청 제출 | `permission_request.created` | 권한 신청 기능(PRD FR-041/FR-042) 구현 시 고정 |
| 권한 신청 승인 | `permission_request.approved` | 권한 신청 기능(PRD FR-041/FR-042) 구현 시 고정 |
| 권한 신청 거절 | `permission_request.rejected` | 권한 신청 기능(PRD FR-041/FR-042) 구현 시 고정 |
| user App 생성 권한 row 생성/회수 | `user_app_creation_permission.created`, `user_app_creation_permission.deleted` | 권한 신청 기능([ADR-0016](ADR-0016-permission-request-and-app-creation-permission.md)) 구현 시 고정 |
| workflow 예산 생성/수정 | `workflow_budget.created`, `workflow_budget.updated` | 예산 관리 기능(PRD FR-051/FR-052) 구현 시 고정 |
| data/model/trace policy 차단 | `policy.block` | MVP 2 |
| data/model/trace policy 경고 | `policy.warn` | MVP 2 |
| workflow 실행 시도와 결과 | `workflow.execute` | MVP 1 |
| LLM 호출 | `llm.call` | MVP 1 현재 구현 |
| RAG retrieval 성공 | `rag.retrieve` | MVP 2 목표 |
| Knowledge taxonomy draft 생성/수정 | `knowledge.taxonomy_draft.created`, `knowledge.taxonomy_draft.updated` | MBA-305 목표. normalized no-op은 action을 만들지 않음 |
| Knowledge taxonomy publish | `knowledge.taxonomy.published` | MBA-305 목표. impact acknowledgement와 identity ledger를 같은 transaction에 기록 |
| Knowledge classification manual assignment 생성/수정 | `knowledge.classification_assignment.created`, `knowledge.classification_assignment.updated` | MBA-305 목표. server-derived `manual_assignment` 또는 `manual_takeover` reason만 기록 |
| Knowledge classification axis lock/unlock | `knowledge.classification_assignment.locked`, `knowledge.classification_assignment.unlocked` | MBA-305 목표 |
| Knowledge classification current-validation 성공 | `knowledge.classification.revalidated` | MBA-305 목표 |
| Knowledge classification suggestion 수락/거절 | `knowledge.classification_suggestion.accepted`, `knowledge.classification_suggestion.rejected` | MBA-305 목표 |
| Organization processing profile draft 생성/수정 | `knowledge.processing_profile_draft.created`, `knowledge.processing_profile_draft.updated` | MBA-305 목표. normalized no-op은 action을 만들지 않음 |
| Organization processing profile publish/deprecate | `knowledge.processing_profile.published`, `knowledge.processing_profile.deprecated` | MBA-305 목표 |
| Organization processing profile-policy draft 생성/수정 | `knowledge.processing_profile_policy_draft.created`, `knowledge.processing_profile_policy_draft.updated` | MBA-305 목표. normalized no-op은 action을 만들지 않음 |
| Organization processing profile-policy publish | `knowledge.processing_profile_policy.published` | MBA-305 목표. impact acknowledgement를 같은 transaction에 기록 |
| Platform default processing profile pointer 변경 | `knowledge.processing_profile_default.changed` | MBA-305 목표. Platform registry owner/system actor, safe before/after revision과 catalog revision만 기록 |
| KB processing profile override set/clear | `knowledge.processing_profile_override.set`, `knowledge.processing_profile_override.clear` | MBA-305 목표 |
| Knowledge processing reindex admission 결과 확정 | `knowledge.processing_reindex.admitted` | MBA-305 목표. `status='success'`, safe `result_status`로 결과 구분 |
| Knowledge processing 또는 privacy legacy artifact 물리 purge 완료 | `knowledge.processing_artifact.purged` | MBA-305/MBA-333 Target. Physical deletion 확인 뒤 tombstone·cleanup receipt와 같은 completion transaction에서만 기록. Privacy legacy audit는 safe wave/receipt/tombstone ref를 쓰고 internal exact artifact ref를 노출하지 않음 |
| Knowledge privacy legacy migration wave 생성 | `knowledge.privacy_migration_wave.created` | MBA-333 Target. Final exact-set inventory freeze, frozen platform/Organization validity ref+epoch와 enforcement epoch/rollout marker를 같은 transaction에서 한 번 기록 |
| Knowledge protected raw copy cutover 완료 | `knowledge.raw_copy_cutover.completed` | MBA-333 Target. Organization별 exact inventory의 모든 item이 terminal disposition과 original-copy absence에 수렴한 final readiness marker와 같은 transaction에서 한 번 기록. Safe opaque cutover ref와 bounded disposition count bucket만 허용 |
| RAG Agent answer 요청 | `rag.answer.requested` | RAG 확장 3단계 목표 |
| RAG Agent answer 완료 | `rag.answer.completed` | RAG 확장 3단계 목표 |
| RAG Agent answer 실패 | `rag.answer.failed` | RAG 확장 3단계 목표 |
| RAG Agent answer 취소 | `rag.answer.cancelled` | RAG 확장 3단계 목표 |
| RAG Agent answer retention purge | `rag.answer.purge` | RAG 확장 3단계 목표. aggregate purge 결과만 기록 |
| Conversation Memory session lifecycle | `memory.session.created/closed/reset/delete_requested/purged` | Conversation Memory 목표 ([ADR-0030](ADR-0030-memory-bounded-context.md)). `purged`는 실제 물리 erasure 완료에만 사용 |
| Conversation Memory public grant lifecycle | `memory.grant.issued/rotated/revoked` | Conversation Memory 목표 |
| 인증 전 또는 resource helper 밖의 전역 401/403 | `auth.permission_denied` | MVP 1 |
| deployment 생성 | `workflow.deploy` | MVP 1 |
| deployment 일반 활성/비활성 toggle | `deployment.toggle` | MVP 1 |
| 다른 deployment가 active인 상태에서 이전 deployment 재활성화 | `deployment.activate_previous` | MVP 1 |
| deployment 삭제 | `deployment.delete` | MVP 1 |
| App 인증 secret 최초 발급 또는 rotation | `app.auth_secret.rotated` | MBA-247. `previous_version=0`이면 최초 발급이며 secret·verifier·candidate는 metadata에 저장하지 않음 |
| schedule dispatch outcome unknown 운영 검토 완료 | `schedule_dispatch.outcome_reviewed` | MBA-187 목표. system actor와 exact claim target 사용 |
| schedule WorkflowRun visibility grace 초과 감지 | `schedule_dispatch.workflow_run_missing` | MBA-187 목표. system actor와 exact claim target 사용, replay 없음 |
| schedule claim이 canonical resource/runtime 상태 변경으로 취소됨 | `schedule_dispatch.canceled` | system actor, exact claim target, allowlisted safe reason |
| schedule claim이 일시 장애로 재평가 대기 상태가 됨 | `schedule_dispatch.deferred` | system actor, exact claim target, status success는 재시도 예정이라는 뜻이며 실행 성공이 아님 |
| schedule claim이 재시도 한도 소진으로 terminal 격리됨 | `schedule_dispatch.failed` | system actor, exact claim target, status failure |

`workflow.blocked`는 `audit_logs.action`으로 저장하지 않는다. UI에서 "workflow 차단" 표시가 필요하면 `target_type='workflow'`, `status='failure'`, `action='permission.denied'` 또는 `action='policy.block'`, `audit_metadata.policy_result`를 조합해 파생한다.

Deployment의 기본 권한 enforcement는 MVP 1 구현 기준으로 본다. Deployment 조회/생성/toggle/delete route는 workflow 하위 resource로 판정하며, 각각 workflow `read`, `deploy`, `manage` 권한을 사용한다. MVP 3의 배포 범위는 이 기본 권한이 아니라 deploy checklist, version diff, trigger mode 정합성, operations dashboard 같은 운영 기능 강화다.

별도 rollback API나 rollback 전용 permission은 만들지 않는다. 이전 deployment 재활성화는 기존 `PATCH /deployments/{deployment_id}/toggle` 동작으로 수행하되, 다른 deployment가 이미 active인 상태에서 inactive였던 이전 deployment를 활성화하는 경우에는 `audit_logs.action='deployment.activate_previous'`로 기록한다. 일반 활성/비활성 toggle은 `deployment.toggle`로 기록한다.

## 구현 정합성

현재 코드의 주요 흐름은 이 결정과 같은 방향이다.

- Resource permission helper는 RBAC 거부를 `permission.denied`로 기록한다.
- 전역 HTTP 401/403 handler는 helper에서 이미 기록하지 않은 인증/권한 실패를 `auth.permission_denied`로 기록한다.
- Password login은 invalid/inactive/limited/limiter-unavailable 결과를 `user.login_failed`로 직접 기록하고 error envelope의 `audit_recorded`로 전역 `auth.permission_denied` 중복 생성을 막는다. 이 전용 감사에는 raw email, IP/forwarded header와 limiter fingerprint를 저장하지 않는다.
- 현재 등록된 `/api/v1/permissions/*` router의 team/user permission grant/update/revoke 흐름은 permission row별 data-change action인 `team_workflow_permission.created/updated/deleted`, `user_workflow_permission.created/updated/deleted`, `team_knowledge_permission.created/updated/deleted`, `user_knowledge_permission.created/updated/deleted`, `team_llm_permission.created/updated/deleted`, `user_llm_permission.created/updated/deleted`를 기록한다.
- KB permission API의 Core upsert와 bulk delete 경로는 ORM listener에만 의존하지 않고, 권한 row mutation과 같은 DB transaction에 `team_knowledge_permission.*`/`user_knowledge_permission.*` data-change audit row를 추가한다.
- Organization member invite/accept/update/remove 흐름은 `organization.invite`, `organization.member.accept`, `organization.member.update`, `organization.member.remove`를 기록한다. Member 제거에 따른 permission cleanup aggregate는 `permission.revoke`에 `reason='organization.member.remove'` metadata를 남긴다.
- Workflow 실행 기록은 `workflow.execute`를 사용하고, 성공/실패는 `audit_logs.status`와 metadata로 표현한다.
- Deployment 생성은 `workflow.deploy`, 일반 toggle은 `deployment.toggle`, 이전 deployment 재활성화는 `deployment.activate_previous`, 삭제는 `deployment.delete`를 사용한다.
- App 인증 secret 최초 발급과 rotation은 `app.auth_secret.rotated`를 사용한다. 최초 발급은 별도 action을 만들지 않고 safe metadata의 `previous_version=0`으로 구분하며 secret 원문, verifier, candidate, header와 fingerprint는 저장하지 않는다.
- 현재 코드의 `AuditAction` 상수에는 `llm.call`도 구현되어 있다.
- MBA-305가 명시적으로 확정한 current-validation, suggestion reject, Organization profile publish/deprecate,
  Platform default profile pointer change와 KB profile override set/clear action은 위 canonical 문자열을
  `AuditAction`, audit 검색/UI filter와 계약 테스트에 함께 등록한다. Default change는 platform registry
  owner/system actor, safe before/after profile revision과 catalog revision만 기록하고 raw profile config를 넣지
  않는다. 각 mutation과 audit row는 같은 Unit of Work에서 확정하고 audit 저장 실패 시 mutation을 rollback한다.
- MBA-305 목표 reindex admission은 최초 `unchanged`, `satisfied_existing`, `job_created`, `job_reused` 결과를 `knowledge.processing_reindex.admitted`, `status='success'`, `audit_metadata.result_status=<result>`로 기록한다. Result receipt, 결과별 pointer/job mutation과 audit row는 같은 Unit of Work에서 확정하며 audit 저장 실패 시 모두 rollback한다. 같은 actor/operation/request의 exact authorized receipt replay는 read-only이고 audit row를 추가하지 않는다. Source-hidden 또는 idempotency conflict는 이 success action을 사용하지 않으며 ADR-0017의 target/source 비노출 request-scoped security audit는 별도 action 경계다. Idempotency key, token digest, source capability와 내부 fingerprint는 audit metadata에 저장하지 않는다.
- MBA-43 runtime 차단 중 credential 후보 없음, credential `use` 권한 부족, verified credential-model relation 없음, inactive model, Workflow Engine runtime organization scope 누락/invalid는 `permission.denied`로 기록한다. 해당 차단은 `workflow.blocked`로 저장하지 않는다.
- MBA-43은 application-level model restriction policy를 구현하지 않으므로 model restriction 차단에 `policy.block`을 기록하지 않는다. `policy.block`은 기존 canonical action으로 유지하며 document/model/trace policy enforcement가 실제로 연결되는 후속 구현에서 사용한다.
- `policy.warn`, `policy.block`, `rag.retrieve`는 이 ADR에서 MVP 2 목표 action으로 확정한다. MBA-78 1차 구현은 해당 `AuditAction` 상수와 테스트를 먼저 추가하며, `rag.retrieve`는 RAG retrieval 성공 감사에 사용한다. `policy.warn`/`policy.block`의 실제 document metadata policy enforcement 연결은 후속 구현 범위다.
- `rag.answer.*`는 standalone RAG Agent answer의 사용자-facing 실행 lifecycle 감사 action이다. Retrieval 성공 감사인 `rag.retrieve`, provider 호출 감사인 `llm.call`, answer 실행 상태 record인 `rag_answer_runs.status`를 대체하지 않고, answer 요청 단위의 검색/운영 이벤트로만 사용한다.
- `rag_answer_runs.status="blocked"`는 scope 안 resource가 확인된 뒤 policy 또는 permission 때문에 answer delta를 만들지 못한 경우에만 사용한다. 별도 `rag.answer.blocked` action은 만들지 않는다. PII/classification/metadata policy 차단은 `policy.block`, KB/credential/model permission preflight 차단은 `permission.denied`와 answer run status 조합으로 표현한다. `resource.not_found`, scope 밖, organization mismatch, invalid organization header, validation 실패에는 answer run과 lifecycle audit을 만들지 않는다.
- `rag.answer.purge`는 retention purge aggregate event다. 기본 aggregate event는 `target_type='rag_answer_runs'`, `target_id=null`로 기록하고, `audit_metadata`는 `organization_id`, `cutoff`, `purged_count`, `failed_count`, `retryable`, `status` 같은 운영 summary allowlist로 제한한다. Raw answer/query/chunk content는 metadata에 넣지 않는다.
- Workflow 예산 생성/수정/비활성화는 `workflow_budget.created`/`workflow_budget.updated`를 사용한다. 예산 초과 실행 차단은 별도 결과 중심 action을 만들지 않고 `policy.block`에 `audit_metadata.reason='budget.exceeded'`로 표현한다.
- Schedule의 occurrence 생성, Gateway dispatch, Worker 최종 admission 중 어느 단계에서 예산 초과가 확인되더라도 `policy.block`, `target_type='workflow'`, `trigger_mode='scheduler'` 계약을 동일하게 사용한다. Claim의 operational 상태는 별도 `schedule_dispatch.*` action으로 기록할 수 있지만 예산 차단 원인 action을 대체하지 않는다.
- Schedule dispatch의 outcome unknown acknowledgment는 workflow 실행 성공/실패를 다시 판정하는 action이 아니라 운영 검토 완료 사건이므로 `schedule_dispatch.outcome_reviewed`를 사용한다. `target_type='schedule_dispatch_claim'`, system actor, organization/operation correlation/resolution allowlist만 저장하며 자동 redrive를 의미하지 않는다 ([ADR-0029](ADR-0029-distributed-schedule-dispatch-claim.md)).
- Schedule WorkflowRun visibility grace를 넘긴 signal은 실행 성공/실패를 판정하거나 Log System row를 재구성하는 사건이 아니므로 `schedule_dispatch.workflow_run_missing`을 사용한다. `target_type='schedule_dispatch_claim'`, system actor, canonical organization과 `reason='workflow_run_missing'`만 저장하며 engine이나 external effect를 replay하지 않는다 ([ADR-0029](ADR-0029-distributed-schedule-dispatch-claim.md)).
- Conversation Memory AuditLog lifecycle은 `memory.session.*`, `memory.grant.*`만 사용한다. Public create는 `memory.session.created`와 `memory.grant.issued`, close는 `memory.session.closed`만 기록한다. Reset은 old `memory.session.reset`/grant revoke와 new `memory.session.created`/public grant issue를 각각 한 번 기록하고 별도 `closed`를 중복 생성하지 않는다. Delete request는 `memory.session.delete_requested`와 active public grant revoke를 기록한다. `memory.session.purged`는 content의 실제 물리 erasure 완료에만 기록하며 `completed_with_hold`와 `terminal_failure`에는 기록하지 않는다. 정상 turn/summary 상태는 high-cardinality operational trace/metric이며 AuditLog row를 만들지 않는다. Turn/summary permission·policy 차단은 `permission.denied`/`policy.block`, provider 호출은 `llm.call`, workflow 실행은 `workflow.execute`를 재사용한다. Organization-scoped event는 safe `organization_id`를 필수 metadata로 기록하며 raw transcript/Memory content, public grant token/hash와 private source identity를 저장하지 않는다. Authenticated request actor는 실제 요청 사용자이고 public request/grant actor는 `actor_id=null`, `actor_type='public'`을 사용한다. 비동기 physical purge/compliance completion은 `actor_id=null`, `actor_type='system'`을 사용한다. App/deployment owner를 actor로 합성하지 않는다. 현재 코드 구현 상태가 아니라 ADR-0030 target contract다.
- ADR-0070 Target의 privacy policy/Collection privacy binding/provider/egress-approval lifecycle, manual review mask/approve/reject 또는 artifact-validity transition은 runtime enforcement 실패가 아니므로 `policy.block`을 management/review mutation action으로 재사용하지 않는다. Security-invalidating transition은 affected `platform|organization` validity revision/monotonic epoch의 exact `+1` CAS와 canonical management audit를 같은 Unit of Work에 확정해야 한다. Manual review의 append-only decision과 canonical audit는 같은 Unit of Work이며 audit에는 safe Organization/document/candidate revision/outcome ref만 허용하고 candidate body, mask range, raw/span/digest를 포함하지 않는다. Candidate body의 expiry/terminal purge와 legal-hold cleanup은 이 decision/audit를 cascade 삭제하지 않고 safe receipt와 분리한다. Exact action 이름, actor, safe metadata와 transaction owner가 후속 management API 계약에 추가되기 전에는 해당 mutation surface를 활성화하지 않는다.

## 영향

- MVP 1 요구사항의 `workflow.blocked` action 표기를 제거하고 `permission.denied`와 `auth.permission_denied`로 분리한다.
- MVP 1에서 permission grant/update/revoke audit을 현재 permission row별 data-change action으로 기록하는 것을 명시한다.
- Organization membership API의 invite/accept/update/remove audit action을 canonical action table에 포함한다.
- MVP 2 knowledge base permission API/enforcement를 구현할 때 grant/update/revoke도 같은 permission row별 data-change action 규칙을 고정한다.
- MVP 2 audit search는 `workflow.blocked`가 아니라 `permission.denied`, `policy.warn`, `policy.block`, `rag.retrieve`를 검색 대상으로 삼는다. RAG Agent answer 운영 검색을 구현할 때는 `rag.answer.requested/completed/failed/cancelled`도 canonical action으로 포함한다.
- Data model의 대표 action convention에 `permission.denied`와 `auth.permission_denied`를 포함한다.
- Deployment API 문서는 기본 권한 enforcement 구현 상태와 MVP 3 운영 기능 강화 범위를 구분한다.
- 권한 신청 기능(PRD FR-041/FR-042) 구현 시 제출/승인/거절을 `permission_request.created/approved/rejected`로 기록한다. `permission_request.approved`는 신청 처리 사건만 기록하며, 승인에 따른 실제 권한 부여는 `user_app_creation_permission.created`를 별도로 기록한다 ([ADR-0016](ADR-0016-permission-request-and-app-creation-permission.md)).

## 후속 검토

- Policy enforcement 구현 시 `policy.block`과 `permission.denied`가 섞이지 않도록 service/helper 경계를 테스트한다.
- RAG retrieval 구현 시 성공 감사 action인 `rag.retrieve`와 trace payload kind인 `rag.retrieval`이 섞이지 않도록 상수와 fixture를 분리한다.
- RAG Agent answer 구현 시 `rag.answer.*` lifecycle action, `rag.retrieve`, `llm.call`, `policy.block`/`permission.denied`, `rag.answer.purge`, `rag_answer_runs.status`가 서로 다른 의미로 기록되는지 테스트한다.
- Audit UI가 "workflow 차단" 같은 사용자 친화 라벨을 canonical action에서 파생해 표시하는지 확인한다.
- 권한 신청 구현 시 `permission_request.approved`와 권한 부여 row data-change action이 하나의 승인 흐름에서 각각 기록되는지 테스트한다.
