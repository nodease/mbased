# 보호 리소스 테스트 매트릭스 템플릿

Status: Active

이 파일은 보호 리소스 또는 외부 실행 기능의 구현 계획과 PR 증거로 복사해 사용한다. 모든 행을 무조건 테스트하지 않는다. 적용되는 위험만 선택하고, 비적용 행에는 계약상 이유를 기록한다.

## 대상

| 항목 | 내용 |
| --- | --- |
| 기능/이슈 | `<기능명 또는 MBA-번호>` |
| 보호 리소스 | `<resource type>` |
| durable reference 위치 | `<graph/config/deployment/payload 또는 해당 없음>` |
| 실행 진입점 | `<endpoint/service/worker/runtime>` |
| 외부 I/O | `<provider/DB/storage 또는 해당 없음>` |
| 권위 문서 | `<Accepted ADR와 feature 문서>` |

## 경계 상태

| 경계 | 상태 | 계약 증거 | 구현 위치 | 검증 증거 | 해당 없음 사유 또는 후속 이슈 |
| --- | --- | --- | --- | --- | --- |
| 정책·식별자·organization scope |  |  |  |  |  |
| 관리 API command/query |  |  |  |  |  |
| 관리 UI·catalog·picker |  |  |  |  |  |
| 저장 schema·GraphMutation·redaction |  |  |  |  |  |
| Deployment preflight |  |  |  |  |  |
| Runtime/background 재검증 또는 capability validity |  |  |  |  |  |
| Transaction·session·TOCTOU |  |  |  |  |  |
| Retry·idempotency·terminal acknowledgement |  |  |  |  |  |
| Background lease·claim·fencing |  |  |  |  |  |
| Revoke/delete/expire/rotation lifecycle |  |  |  |  |  |
| 오류·resource hiding·reason code |  |  |  |  |  |
| Audit event 생성·action/status·중복 방지 |  |  |  |  |  |
| Audit·trace·secret/PII redaction |  |  |  |  |  |
| Legacy migration·scrub·호환성 종료 |  |  |  |  |  |
| 공식 문서 정합성 |  |  |  |  |  |

## 테스트 시나리오

| ID | 시나리오 | 기대 결과 | 권장 계층 | 증거/상태 |
| --- | --- | --- | --- | --- |
| AUTH-01 | 정상 actor가 같은 organization의 active resource를 사용한다. | 허용되고 올바른 resource만 resolve된다. | service/API/runtime |  |
| AUTH-02 | 다른 사용자 또는 다른 organization의 ID를 제출한다. | resource hiding 정책에 따라 거부되고 상세가 노출되지 않는다. | API/service |  |
| AUTH-03 | user/team `use` 권한을 부여한 뒤 회수한다. | 부여 전·후·회수 후 결과가 계약대로 바뀐다. | service/API |  |
| STORE-01 | client가 허용된 reference와 editor metadata를 저장하고 다시 읽는다. | round-trip 후 의미가 유지된다. | schema/component/API |  |
| STORE-02 | inline secret, unknown field 또는 provider 불일치 reference를 제출한다. | 저장 전에 거부되고 durable data에 남지 않는다. | schema/API |  |
| PREFLIGHT-01 | 유효한 reference로 배포 검사를 수행한다. | 성공하며 필요한 최소 column만 읽는다. | service/repository |  |
| PREFLIGHT-02 | missing, revoked, 타 organization 또는 권한 없는 reference로 검사한다. | 배포 전에 fail-closed한다. | service/API |  |
| RUNTIME-01 | preflight 뒤 권한 회수, revoke, delete 또는 rotation을 수행한다. | 즉시 회수 계약이면 차단되고, capability/lease 계약이면 validity·revision 범위대로 판정된다. | runtime/integration |  |
| RUNTIME-02 | worker 또는 processor service를 endpoint 없이 직접 호출한다. | 같은 resolver와 정책이 적용된다. | worker/runtime |  |
| RUNTIME-03 | runtime 권한 검증이 실패한다. | provider/DB/storage adapter가 호출되지 않는다. | unit/runtime |  |
| TX-01 | 권한·credential snapshot resolver의 session과 row lock 수명을 관찰한다. | Accepted ADR의 serialization lock이 아닌 resolver transaction/session은 외부 I/O 전에 종료된다. | unit/PostgreSQL |  |
| TX-02 | Accepted ADR이 장기 transaction advisory lock을 요구하는 writer를 실행한다. | lock을 지정 순서와 범위에서 획득하고 apply/finalization commit·rollback에 맞춰 해제하며 timeout·deadlock 계약을 지킨다. | PostgreSQL |  |
| CONN-01 | Connection 삭제·소유권 변경 또는 사용 권한 회수 뒤 background ingestion을 실행한다. | 외부 DB dial 전에 fail-closed하고 연결 상세를 노출하지 않는다. | worker/runtime |  |
| KNOW-01 | 검색 후보 선택 뒤 source ACL 또는 evidence 권한이 바뀐다. | 최종 근거 경계에서 제외되고 prompt, citation과 trace에 유입되지 않는다. | retrieval/runtime |  |
| EFFECT-01 | 같은 stable logical effect identity를 duplicate delivery와 동시 claim으로 실행한다. | 하나의 실행만 provider를 호출하고 나머지는 같은 durable outcome을 재사용한다. | runtime/PostgreSQL |  |
| EFFECT-02 | provider 성공 응답 직후 local terminal acknowledgement 전 crash를 주입하고 outcome-unknown 작업을 재실행한다. | 정책이 정한 lookup·reconciliation 또는 idempotency key로 중복 외부 효과 없이 terminal 상태에 수렴한다. | runtime/PostgreSQL |  |
| LEASE-01 | 이전 worker의 lease가 만료되고 새 worker가 claim한 뒤 이전 worker가 finalize한다. | fencing·소유권 검증으로 stale finalize가 거부된다. | worker/PostgreSQL |  |
| CAP-01 | expired, replayed 또는 stale revision capability를 제출한다. | provider 호출 전에 거부되고 안전한 reason code만 남는다. | service/runtime |  |
| LIFE-01 | revoked resource를 실행에 사용한다. | safe error로 거부된다. | API/runtime |  |
| LIFE-02 | authorized manager가 revoked resource를 조회·정리하거나 grant를 회수한다. | 계약된 관리 경로는 유지된다. | service/API/component |  |
| LIFE-03 | secret rotation 중 구키·신키 또는 revision 경계를 검증한다. | stale revision 사용과 평문 fallback이 차단된다. | service/PostgreSQL |  |
| HIDE-01 | malformed, missing, unauthorized ID의 응답과 audit를 비교한다. | 숨겨야 할 존재·provider·owner 정보가 구분 가능하게 노출되지 않는다. | API/audit |  |
| AUDIT-01 | 계약상 감사 대상인 허용·거부·revoke·권한 회수·상태 변경을 실행한다. | 각 동작의 canonical action/status audit가 정확히 한 번 생성되며 0건 또는 중복 기록은 실패한다. | service/API/PostgreSQL |  |
| REDACT-01 | 성공·거부·provider 실패의 response, audit와 trace를 검사한다. | secret, 암호문, token, raw payload와 불필요한 PII가 없다. | API/audit/runtime |  |
| UI-01 | 관리자 resource catalog에서 user/team 권한을 부여·회수한다. | API 계약과 같은 resource type/action을 사용한다. | component/E2E |  |
| LEGACY-01 | legacy direct-secret 또는 구형 reference를 읽고 다시 저장한다. | 공식 호환·scrub 정책대로 처리되며 신규 legacy write가 생기지 않는다. | migration/API/runtime |  |

## 테스트 선택 원칙

- 발견된 위험과 공식 계약을 직접 검증하는 최소 테스트를 작성한다.
- 동작 경계의 `완료`에는 계약 문서뿐 아니라 구현 위치와 실행 가능한 검증 증거를 모두 기록한다.
- 동일 동작을 여러 계층에서 반복 검증하지 말고 최종 보안 판단 계층과 실제 소비 계층을 우선한다.
- PostgreSQL lock, migration, 실제 provider와 E2E는 필요한 테스트 코드를 준비하되 저장소 운영 규칙에 따라 CI 책임을 명시할 수 있다.
- 테스트 fixture에는 실제 secret, token, credential, PII 또는 raw provider payload를 사용하지 않는다.
- 미실행 테스트는 실행 환경과 남은 위험을 PR에 기록한다.
