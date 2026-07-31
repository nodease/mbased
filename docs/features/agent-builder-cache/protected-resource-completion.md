# Agent Builder Cache Protected Resource Completion

Status: Draft

## Applicability

이 기능은 cache key를 organization/user scope로 제한하고, hit 결과가 model credential,
Knowledge/Collection과 workflow graph에 영향을 줄 수 있으므로 protected-resource completion
검토 대상이다. Cache 자체는 protected resource를 durable하게 저장하지 않는다.

현재는 문서 설계 단계다. `후속 이슈` 행은 구현 완료를 뜻하지 않으며 해당 이슈의 필수 검증 전에는
feature 문서와 기능 상태를 완료로 바꾸지 않는다. 구현 전 안전 상태는 cache feature flag 기본 비활성과
기존 Planner 경로 유지다.

| Boundary | 현재 상태 | Decision and safe interim state | Evidence target |
|---|---|---|---|
| 정책·식별자 | 후속 이슈: MBA-343~346, MBA-348~349 | Organization/user scope, selected target과 Knowledge identity는 HMAC 경계 밖으로 노출하지 않는다. 구현 전 cache는 비활성이다. | ADR-0063, ABC-FR-018~026, ABC-T014, ABC-T020~039 |
| 관리 API/UI | 해당 없음 | Cache는 관리 리소스가 아니며 public endpoint, picker, 권한 관리 UI와 client diagnostic을 추가하지 않는다. 기존 Agent Builder UI 계약을 유지한다. | `api_spec.md` External API Boundary, `component_spec.md` PRD and Core Document Impact |
| Cache storage·GraphMutation | 후속 이슈: MBA-343, MBA-345, MBA-348, MBA-349 | Cache-safe typed plan만 TTL 저장한다. Resource ID, handle, credential, graph와 operation을 저장하지 않고 결과는 기존 GraphMutation/CAS로만 저장한다. | ABC-FR-030~036, ABC-FR-044, ABC-T028~035, ABC-T066~068 |
| Cache integrity | 후속 이슈: MBA-343, MBA-345, MBA-349 | Key digest와 payload를 authenticated envelope로 묶고 MAC 확인 전 plan을 사용하지 않는다. 구현 전 cache는 비활성이다. | ABC-FR-026, ABC-FR-034~036, ABC-T031~035 |
| HMAC secret | 후속 이슈: MBA-345, MBA-349 | 기존 untracked/production secret injection 경계만 사용하고 tracked env, repr, log, trace와 audit 노출을 금지한다. | ABC-FR-025, ABC-FR-087~089, ABC-T126~129 |
| Redis connection secret | 후속 이슈: MBA-345, MBA-349 | URL password와 query credential을 configuration error와 diagnostic에 노출하지 않는다. | ABC-FR-087, ABC-T126, ABC-T129 |
| Organization scope | 후속 이슈: MBA-345, MBA-348, MBA-349 | Active organization과 user를 HMAC key material에 포함하고 사용자 간 entry를 공유하지 않는다. | ABC-FR-020~024, ABC-T020~027 |
| Request admission | 후속 이슈: MBA-348, MBA-349 | 인증, 권한, foreground request와 cancellation/version fence 뒤에만 lookup한다. 구현 전에는 기존 admission 뒤 Planner만 호출한다. | ABC-FR-009, ABC-FR-075, ABC-T052~054 |
| Planner model/credential | 후속 이슈: MBA-346, MBA-348, MBA-349 | Hit 전 valid credential, active model, verified relation과 use permission을 다시 검증한다. | ABC-FR-040, ABC-FR-054, ABC-T060~061 |
| Knowledge/Collection | 후속 이슈: MBA-346, MBA-349 | Hit마다 현재 route/use permission, lifecycle/readiness를 다시 계산하고 handle을 새로 발급한다. | ABC-FR-050~054, ABC-T080~087 |
| Workflow target | 후속 이슈: MBA-345, MBA-346, MBA-349 | Selected target identity는 ephemeral HMAC input으로만 사용한다. Hit에서는 server-loaded graph와 target을 다시 resolve하고 UUID를 재사용하지 않는다. | ABC-FR-019, ABC-FR-041~043, ABC-T036, ABC-T062~066 |
| Deployment preflight | 해당 없음 | Cache는 graph/deployment에 reference를 추가하지 않고 생성 단계 plan에만 사용한다. 생성된 graph는 cache outcome과 무관하게 기존 deployment preflight를 그대로 통과한다. | ABC-FR-044, ABC-T142 |
| Runtime/background | 해당 없음 | Cache는 workflow를 실행하거나 background job을 만들지 않는다. Generated graph의 runtime 권한·lifecycle 검사는 기존 계약이 계속 소유한다. | Side-effect boundary regression, ABC-T142~143 |
| Transaction·TOCTOU | 후속 이슈: MBA-345, MBA-348, MBA-349 | Redis/provider wait 중 DB transaction이나 row lock을 유지하지 않고 value 사용 직전 cancellation/version을 다시 검사한다. | ABC-FR-063, ABC-FR-068, ABC-T106, ABC-T110~114 |
| Retry·idempotency | 후속 이슈: MBA-345, MBA-348, MBA-349 | Single-flight는 best-effort 비용 최적화이고 기존 provider usage identity, GraphMutation/CAS idempotency를 대체하지 않는다. | ABC-FR-062~069, ABC-T102~116, ABC-T131~132 |
| Background coordination | 해당 없음 | Lease는 foreground request의 bounded single-flight에만 사용하며 worker claim이나 durable background coordination을 만들지 않는다. | `component_spec.md` Single-Flight, ABC-T102~116 |
| Lifecycle | 후속 이슈: MBA-346, MBA-349 | Revoked/deleted/stale resource는 hit revalidation에서 제외하거나 기존 permission/stale 계약으로 차단한다. | ABC-FR-040~048, ABC-FR-050~054, ABC-T060~068, ABC-T080~084 |
| Cache key rotation·migration | 후속 이슈: MBA-345, MBA-349 | HMAC key/version 변경은 dual-read, migration과 backfill 없이 namespace miss를 만든다. | ABC-FR-082~083, ABC-FR-088, ABC-T125, ABC-T128 |
| Legacy data | 해당 없음 | Cache는 현재 미구현이고 DB schema나 기존 cache data가 없다. 신규 namespace는 이전 value를 읽지 않는다. | ADR-0063 Decision 12~14 |
| 오류·resource hiding | 후속 이슈: MBA-345, MBA-346, MBA-348, MBA-349 | Redis 오류는 safe fail-open, protected-resource permission/CAS 오류는 기존 fail-closed 계약으로 처리하고 식별자를 diagnostic에 노출하지 않는다. | ABC-FR-060~061, ABC-FR-073, ABC-T100, ABC-T120~121 |
| Audit | 후속 이슈: MBA-348, MBA-349 | Cache hit가 workflow mutation audit을 대체하지 않는다. Audit는 cache key/value/plan의 durable copy나 Redis 복구 replay source가 아니다. | ABC-FR-074, ABC-FR-077, ABC-T124, ABC-T130 |
| Usage/cost | 후속 이슈: MBA-348, MBA-349 | Hit는 provider usage가 없고 miss/repair만 기존 recorder를 사용한다. | ABC-FR-070~071, ABC-T048~050, ABC-T122~123 |
| Redaction | 후속 이슈: MBA-344~345, MBA-347~350 | Raw request, cache key digest 전체, protected identity, secret과 provider payload를 log, metric, audit와 artifact에 남기지 않는다. | ABC-FR-006, ABC-FR-032~036, ABC-FR-073, ABC-NFR-014~015, ABC-T008, ABC-T027~035, ABC-T121, ABC-T126~130, ABC-T158, ABC-T165~167 |
| Redis isolation·운영 serving | 후속 이슈: MBA-345, MBA-349 | MBA-345는 전용 URL, no-Celery-fallback과 `NODE_ENV=production|staging` readiness gate를, MBA-349는 운영 evidence 부재 시 fail-closed 통합 검증을 소유한다. 실제 instance/secret wiring/capacity·eviction/failure/network/monitoring/rollback은 별도 운영 범위이며 evidence 전 serving을 비활성으로 유지한다. | ABC-FR-085~094, ABC-T107, ABC-T117~119, 운영 serving 전 evidence |
| 문서·테스트 | 후속 이슈: MBA-343~350 | MBA-339은 계약과 추적표만 확정한다. 구현 위치, 실행 명령, 결과와 revision은 각 이슈 및 PR/CI에 기록하고 MBA-349가 이 matrix의 실제 evidence를 갱신한다. | `requirements.md`, `api_spec.md`, `component_spec.md`, `test_cases.md`, `local/mba-339/test-matrix.md` |

## Merge Blocking Conditions

- Cache value나 diagnostic에 protected resource ID 또는 credential이 노출된다.
- Cache hit가 현재 permission/lifecycle 검사를 생략한다.
- Cache hit가 Agent Builder request admission, cancellation 또는 foreground request 직렬화를 우회한다.
- Cache hit가 기존 graph resource validation 또는 CAS를 우회한다.
- Redis 장애가 Agent Builder 요청 자체를 차단한다.
- HMAC key 또는 Redis connection secret 원문이 configuration error, log, trace, audit 또는 fixture에 노출된다.
- Production 운영 증거 확인 전에 운영자가 ready attestation을 설정하거나 Agent Builder intent cache를 활성화한다.
- 전용 cache Redis URL 없이 Celery broker/result Redis로 자동 fallback한다.
- Cache hit에서 provider usage row를 허위로 만들거나 miss provider cost를 누락한다.
- Cache payload authenticity를 검증하지 않거나 다른 key의 valid payload 교체를 허용한다.
- Cache memory pressure가 Celery broker/result key를 eviction할 수 있는 상태로 production serving을 켠다.

위 항목은 운영 증거 확인 전에는 운영자가 ready attestation을 설정할 수 없다는 의미다. 런타임은 별도 evidence
저장소를 조회하지 않고 attestation과 bounded configuration을 검사한다. Cache 코드·필수 검증 완료는 운영
작업 번호와 독립적으로 판정하며, 실제 Production Redis evidence 전에는 production/staging serving만 비활성으로 유지한다.
