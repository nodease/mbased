# 보호 리소스 기능 완결성 기준

Status: Active

## 목적

보호 리소스나 외부 실행 기능은 한 계층의 구현만으로 완료되지 않는다. 저장 단계에서 허용된 reference가 관리 화면, deployment preflight, background/runtime, 상태 변경과 감사 경계에서도 같은 의미로 처리되어야 한다.

이 문서는 신규 또는 변경 기능이 실제 소비 경로 전체에서 같은 권한·상태·비노출 계약을 지키는지 검토하는 공통 완료 기준이다. 개별 기능의 정책을 새로 정의하지 않으며 Accepted ADR과 기능 문서를 우선한다.

관련 기존 결정의 예시는 다음과 같다.

- resource hiding과 403/404: [ADR-0010](../decisions/ADR-0010-resource-access-403-404-policy.md)
- 감사 조회와 actor access: [ADR-0023](../decisions/ADR-0023-audit-actor-access-management-boundary.md)
- 외부 부수효과 멱등성: [ADR-0035](../decisions/ADR-0035-external-effect-idempotency-boundary.md)
- Connection 사용 권한: [ADR-0051](../decisions/ADR-0051-connection-use-authorization-boundary.md)
- credential 암호화와 rotation: [ADR-0057](../decisions/ADR-0057-llm-credential-at-rest-encryption-and-rotation.md)

## 적용 대상

다음 중 하나라도 해당하면 이 기준을 적용한다.

- 보호 리소스 ID나 opaque credential reference를 graph, 설정, deployment 또는 작업 payload에 저장한다.
- organization scope, ownership, user/team grant, `use` 또는 `manage` 권한을 판정한다.
- `active`, `revoked`, `deleted`, `expired`, `rotated` 같은 상태가 사용 가능 여부를 바꾼다.
- 저장·배포·실행 사이에 시간이 지나며 preflight와 runtime/background가 리소스를 다시 읽는다.
- secret, PII, 외부 provider 호출 또는 재시도 가능한 외부 부수효과를 다룬다.

단순 문서 교정, 무상태 내부 helper, 보호 리소스를 소비하지 않는 표시 변경은 자동 적용 대상이 아니다. PR에는 비적용 사유를 한 줄로 남긴다.

## 완료 상태

각 경계는 다음 셋 중 하나로 기록한다.

| 상태 | 의미 |
| --- | --- |
| 완료 | 동작 경계에는 계약 증거, 구현 위치와 해당 동작을 실행하는 검증 증거가 모두 있다. 문서·절차 자체가 산출물인 경계만 공식 문서 검토를 검증 증거로 사용할 수 있다. |
| 해당 없음 | 기능 계약상 경계가 존재하지 않는다. 이유를 기록한다. |
| 후속 이슈 | 현재 범위 밖이지만 제품 계약에 필요한 경계다. 이슈 번호와 현재 PR의 안전한 임시 상태를 기록한다. |

권한 우회, secret·PII 노출, 외부 I/O 전 fail-closed 실패, 중복 외부 효과 또는 기존 관리 경로 단절을 만들 수 있는 필수 경계는 `후속 이슈`로 미룬 채 병합하지 않는다.

동작 경계의 증거는 다음 세 종류를 구분한다.

- 계약 증거: Accepted ADR, requirements, API/component/test 계약
- 구현 증거: 실제 판정을 소유하는 service, domain, repository, runtime 또는 UI 위치
- 검증 증거: 해당 동작과 실패 조건을 실행하는 test ID·파일과 결과

공식 문서는 구현할 동작을 정의하는 계약 증거이며, resolver, preflight, runtime, lifecycle과 audit 동작의 구현·검증 증거를 대신하지 않는다. 실행 코드가 필요한 경계에 문서만 연결한 상태는 `완료`가 아니다.

## 기능 완결성 매트릭스

| 경계 | 필수 확인 질문 | 대표 증거 |
| --- | --- | --- |
| 정책·식별자 | resource identity, organization scope, actor와 permission action이 공식 문서에 정의됐는가? opaque ID의 소유 범위를 서버가 검증하는가? | ADR/requirements, domain/service test |
| 관리 API | create/list/read/update/revoke/delete와 grant/revoke-grant 중 필요한 command/query가 모두 존재하는가? revoked 리소스를 관리·정리할 수 있는가? | API spec, service/API test |
| 관리 UI·catalog | picker와 user/team 권한 관리 화면이 resource type을 알고 있는가? UI 차단과 별개로 API가 최종 권한을 강제하는가? | component spec, Vitest/E2E |
| 저장·GraphMutation | durable data에는 opaque reference만 저장되는가? client serializer와 backend allowlist가 같은 필드를 허용하는가? secret·연결 상세를 복제하지 않는가? | schema/allowlist test, graph round-trip test |
| Deployment preflight | reference 존재, scope, provider/type, 상태와 사용 권한을 배포 전에 검증하는가? secret column을 불필요하게 projection하지 않는가? | preflight test, query projection test |
| Runtime/background | 실제 provider 또는 외부 adapter 호출 전에 권위 resolver를 다시 평가하거나 Accepted ADR이 정의한 유효 capability를 검증하는가? background 직접 호출도 endpoint 검증을 우회하지 못하는가? | runtime/service test, adapter-not-called assertion |
| Transaction·TOCTOU | 권한·credential snapshot용 session/row lock이 외부 I/O까지 불필요하게 유지되지 않는가? Accepted ADR이 serialization advisory lock의 장기 수명을 명시하면 획득 순서·범위·해제·timeout을 그 계약대로 검증하는가? stale snapshot 또는 capability가 공식 validity·revision 계약을 넘어 사용되지 않는가? | state-transition test, PostgreSQL lock contract test |
| Retry·idempotency | 같은 논리 작업이 stable effect identity를 사용하는가? duplicate delivery·동시 claim·timeout과 provider 응답 직후 crash 뒤 outcome-unknown replay가 중복 외부 효과를 만들지 않고 terminal acknowledgement 또는 reconciliation으로 수렴하는가? | idempotency/crash-replay test, provider-call count assertion |
| Background coordination | lease, claim 또는 fencing을 사용한다면 stale worker가 heartbeat·finalize하지 못하는가? cancellation과 late completion의 소유권이 정의됐는가? | worker test, PostgreSQL concurrency test |
| Lifecycle | active, revoked, deleted, expired, rotated 상태의 읽기·관리·실행 결과와 오류가 정의됐는가? revoke가 grant 회수나 감사 확인까지 막지 않는가? | lifecycle table, API/runtime test |
| 오류·resource hiding | malformed, missing, 타 조직, 권한 없음이 계약에 맞는 safe error와 reason code를 사용하는가? 존재 여부나 provider 상세를 노출하지 않는가? | negative API/runtime test |
| Audit·redaction | 허용·거부·상태 변경을 필요한 범위에서 감사하는가? metadata에 secret, PII, raw provider payload 또는 숨겨진 resource detail이 없는가? | audit test, response/log assertion |
| Legacy·migration | direct secret, legacy field 또는 구형 상태를 허용한다면 종료·scrub·downgrade 계약이 있는가? 신규 write가 legacy 형식을 다시 만들지 않는가? | migration/compatibility test |
| 문서·테스트 | requirements, API, component, test case와 구현이 같은 상태·오류·권한 의미를 갖는가? 실제 소비 계층별 테스트 책임이 배정됐는가? | 추적성 표, 테스트 매트릭스 |

## 적용 절차

1. 구현 전에 적용 대상을 판단하고 [테스트 매트릭스 템플릿](templates/protected-resource-test-matrix.md)을 복사한다.
2. 정책 불변조건을 `actor → command/query → precondition → scope/ownership → error → audit → test`로 추적한다.
3. 저장된 reference가 실제로 소비되는 모든 진입점을 찾는다. API endpoint만이 아니라 Agent Builder, deployment, worker, retry와 직접 service 호출을 포함한다.
4. 각 경계를 `완료`, `해당 없음`, `후속 이슈`로 표시하고 계약·구현·검증 증거를 분리해 연결한다.
5. 권한·상태 변경 뒤의 runtime 재검증 또는 capability validity 판정과 외부 adapter 미호출을 우선 테스트한다.
6. PR 양식에 적용 여부와 매트릭스 위치를 기록한다.

## 두 종류의 추적성

문서 계약 추적성과 실행 경계 완결성은 서로 대체하지 않는다.

| 구분 | 확인하는 것 | 놓치기 쉬운 예 |
| --- | --- | --- |
| 문서 계약 추적성 | 정책이 actor, command/query, 오류, audit와 test로 이어지는가 | clear command, history query, safe 404 누락 |
| 실행 경계 완결성 | 같은 정책이 저장, UI, preflight, runtime과 상태 전이에서 끝까지 적용되는가 | UI catalog 누락, stale runtime 권한, revoked 관리 불가 |

## 적용 예시

이 기준은 특정 이슈나 credential 종류에 한정되지 않는다. 다음은 대표적인 적용 유형이며, 실제 기능은 위 기능 완결성 매트릭스에서 해당하는 경계를 선택한다.

| 기능 유형 | 대표 상태·참조 | 반드시 이어서 확인할 경계 |
| --- | --- | --- |
| Credential resource | graph 또는 deployment의 opaque credential ID, active/revoked/rotated revision | manager 등록·권한 부여/회수, picker·schema, preflight, provider 호출 전 resolver/capability, secret redaction |
| Connection·connector | owner 또는 organization-scoped Connection ID, 삭제·소유권 변경 | 설정 저장 권한, background ingestion 재검증, outbound egress, 외부 DB 연결 전 session 종료, 연결 상세 비노출 |
| Knowledge ingestion·retrieval | Knowledge/Collection/source reference, source ACL, document version, ingestion lease | 관리 권한, source ACL과 retrieval gate, worker claim·fencing, final evidence 재검증, citation·trace redaction |
| 외부 부수효과 node | logical effect ID, attempt, provider acknowledgement, terminal state | deployment 준비 상태, runtime 권한, retry·timeout 중복 방지, terminal acknowledgement, safe provider 오류·audit |
| Provider execution capability | policy/revision, capability expiry·nonce, credential/billing principal | 발급 권한, admission limit, replay·expiry·revision 검증, provider 호출 전 validity, usage·audit 귀속 |
| 일반 stateful 보호 리소스 | active/revoked/deleted/expired 상태와 user/team grant | command/query 대칭성, resource hiding, authorized cleanup, 상태 변경 뒤 실행 차단, 감사·보존 계약 |

예를 들어 credential 기능에서는 secret 비저장과 rotation이 중심이고, Knowledge 기능에서는 source ACL·final evidence와 background fencing이 중심이며, 외부 부수효과 기능에서는 idempotency와 terminal acknowledgement가 중심이다. 특정 유형에 해당하지 않는 경계는 이유를 적어 `해당 없음`으로 표시한다.

하나의 모델·endpoint 또는 worker가 구현됐다는 사실만으로 기능이 완료되는 것은 아니다. 해당 리소스가 저장·관리·배포·실행·실패·상태 변경 과정에서 실제로 소비되는 경계가 모두 연결돼야 한다.
