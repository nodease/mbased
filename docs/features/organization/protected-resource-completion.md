# Resource Permission Bulk Grant 완결성 매트릭스

Status: Draft

## 범위

Admin `권한 부여` modal에서 하나의 resource type과 grantee type 안의 복수 resource×grantee에 같은 canonical permission을 부여하는 기능이다. Workflow, Knowledge Base, LLM Credential, Mail Credential을 포함한다.

## 완결성 매트릭스

| 경계 | 상태 | 계약·구현·검증 증거 |
| --- | --- | --- |
| 정책·식별자 | 완료 | `requirements.md` ORG-REQ-079, `BulkPermissionGrantRequest`; server-owned organization header와 UUID를 사용한다. |
| 관리 API | 완료 | `POST /api/v1/permissions/bulk-grants`, `apps/gateway/tests/api/test_bulk_permissions_api.py`, `tests/test_permission_schema.py`. |
| 관리 UI·catalog | 완료 | `PermissionsTab.tsx`의 checkbox 다중 선택과 `page.test.tsx`의 복수 resource/grantee 제출 테스트. Active team/member catalog만 사용한다. |
| 저장·GraphMutation | 해당 없음 | 신규 durable reference를 graph/deployment에 저장하지 않고 기존 permission row만 upsert한다. |
| Deployment preflight | 해당 없음 | 부여 API가 deployment reference를 생성하지 않는다. 기존 preflight permission 소비 계약은 변경하지 않는다. |
| Runtime/background | 해당 없음 | Runtime permission resolver와 authorization decision 계약을 변경하지 않는 관리 mutation이다. |
| Transaction·TOCTOU | 완료 | ID 정렬 lock 순서, Workflow subject→App lifecycle→permission scope, active subject 재잠금을 적용한다. Mail bulk는 모든 credential을 먼저 잠근 뒤 grantee 처리를 시작한다. `test_execute_many_*`와 Mail bulk service test가 단일 commit, 잠금 순서, 실패 전체 rollback을 검증한다. |
| Retry·idempotency | 완료 | 기존 natural key upsert를 재사용하며 같은 auth state는 duplicate row를 만들지 않는다. 요청 ID duplicate는 schema가 mutation 전 거부한다. |
| Background coordination | 해당 없음 | 동기 Gateway transaction으로 실행하며 worker/lease를 사용하지 않는다. |
| Lifecycle | 완료 | 기존 resource-specific authorize helper와 Mail credential active 검증을 모든 pair에 mutation 전 적용한다. |
| 오류·resource hiding | 완료 | 기존 organization scope 404, scope 내 manage 403, active grantee 404 계약을 pair별로 재사용한다. 한 pair 실패 시 나머지 target identity나 partial result를 응답하지 않는다. |
| Audit·redaction | 완료 | Workflow/Knowledge/LLM은 기존 row별 canonical data-change audit, Mail은 기존 safe permission audit을 같은 transaction에 저장한다. Request/response에 secret/raw credential은 없다. |
| Legacy·migration | 해당 없음 | Schema/table migration 없이 기존 scalar PUT과 DELETE endpoint를 그대로 유지한다. |
| 문서·테스트 | 완료 | `requirements.md`, `api_spec.md`, `component_spec.md`, organization/admin-dashboard `test_cases.md` 갱신과 focused Python/Vitest 테스트를 연결했다. |
