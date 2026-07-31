# ADR-0035: External Effect Idempotency Boundary

Status: Accepted

## Context

Celery delivery와 Worker 실행은 exactly-once가 아니다. 외부 HTTP, message, email, ticket 또는 DB mutation node가 provider 요청을 보낸 뒤 Worker가 종료되거나 응답을 확정하지 못하면 같은 logical node 실행이 다시 전달될 수 있다. Schedule admission 중복 방지는 ADR-0029가 담당하지만, admission 이후 provider side effect 중복 방지는 node runtime이 별도로 소유해야 한다.

ADR-0029의 “provider별 idempotency와 node side-effect exactly-once는 MBA-190 범위”라는 후속 문구는 책임 이관을 뜻하며 보장 수준을 확정한 문장이 아니다. 이 ADR은 그 범위를 provider가 공식 계약과 테스트로 증명한 수준으로 제한한다.

Linear MBA-190 본문은 capability, identity 전달, provider별 중복 방지, outcome과 검증을 정의하는 필수 범위다. 이슈 댓글은 본문을 축소하거나 대체하지 않고 MBA-217의 Gmail lifecycle, MBA-218의 Slack 응답 판정과 당시 MBA-220의 Gmail draft 작업이 이 공통 계약과 정합해야 한다는 소유 경계를 보충한다. MBA-220은 이후 취소됐고 Gmail draft 범위는 MBA-217에 통합됐다. 다만 먼저 승인·구현된 [ADR-0032](ADR-0032-mail-processing-gmail-draft-idempotency.md)는 MBA-190을 선행 조건으로 두지 않고 Mail/Gmail 전용 port와 ledger를 사용한다. MBA-190은 이 구현을 `workflow_node_effect_attempts`로 이관하거나 이중 기록하지 않으며, 직접 adapter 정렬은 ADR-0032를 갱신하는 후속 작업으로 남긴다.

현재 `workflow_run_id`는 모든 실행 표면의 retry에서 고정된 identity가 아니고, Loop와 subworkflow는 같은 graph node를 한 workflow run 안에서 여러 번 호출할 수 있다. 또한 WorkflowRun과 WorkflowNodeRun은 Log System에 비동기로 저장되므로 provider 호출 전 ledger가 이 row들의 존재를 FK로 요구할 수 없다.

Provider가 공식 idempotency 또는 동등한 deduplication 계약을 제공하지 않으면 Nodease만으로 exactly-once를 보장할 수 없다. 따라서 공통 runtime은 stable identity, durable attempt, 보수적인 replay 판단과 secret-safe observability를 제공하고, 실제 중복 제거 보장 수준은 provider operation별 공식 계약과 테스트로 제한한다.

## Decision

### Stable execution and node invocation identity

- Logical workflow 실행마다 server-issued `execution_id`를 Workflow Engine 시작 또는 queue 발행 전에 한 번 생성한다. Celery retry와 duplicate delivery는 같은 `execution_id`를 유지한다.
- Compare A/B처럼 서로 다른 설정을 의도적으로 각각 실행하는 variant는 별도 logical execution이다. Variant마다 서로 다른 `execution_id`를 발급하고, 각 variant의 retry에서만 최초 값을 유지한다. 같은 compare 요청의 correlation은 기존 request/correlation context를 사용하며 external effect identity를 공유하지 않는다.
- 외부 effect를 만들 수 있는 실제 node 호출마다 opaque `node_invocation_id`를 만든다. V1은 고정된 Nodease UUID namespace와 versioned, length-delimited canonical invocation path를 사용하는 UUIDv5로 계산한다. 이 값은 graph node id뿐 아니라 subworkflow 호출 경로와 Loop iteration을 포함해 같은 logical 호출의 retry에서는 유지되고, 정상적인 다음 반복이나 다른 subworkflow 호출에서는 달라야 한다.
- Invocation ordinal은 node 완료 순서나 전체 workflow 전역 counter가 아니라 같은 parent invocation path 안에서 같은 graph `node_id`가 제출된 횟수로 정한다. 현재 정적 DAG의 일반 node는 첫 제출인 `0`이고, Loop iteration과 subworkflow caller path가 달라지면 ordinal을 공유하지 않는다. 기존 logging용 `_node_sequence`, 다른 node의 추가/완료 순서, process-local hash, dict iteration order, Worker id와 wall clock을 canonical input으로 사용하지 않는다. V1 변경은 기존 retry identity를 깨므로 새 version과 고정 test vector 없이 바꾸지 않는다.
- External effect logical identity는 한 organization 안에서 `execution_id + node_invocation_id + operation + effect_sequence`다. 다만 DB의 충돌 방지 기준은 operation이 바뀌어 새 row로 우회하지 못하도록 `organization_id + execution_id + node_invocation_id + effect_sequence`인 stable effect slot으로 둔다. `effect_sequence`는 operation별 번호가 아니라 한 node invocation 안에서 provider에 보내려 한 외부 작업 전체의 0부터 시작하는 순번이다. 현재 1회 호출 node는 항상 `0`을 사용한다.
- `workflow_run_id`, `node_run_id`, graph `node_id`는 trace correlation 정보이며 external effect uniqueness의 source of truth가 아니다.
- `ExternalEffectContext`는 node invocation마다 새 immutable value로 전달하며 server가 검증한 `organization_id`, 현재 node를 소유한 `app_id`와 `workflow_id`를 포함한다. 세 값이 없거나 canonical resource와 다르면 provider 호출 전에 fail-closed한다. Subworkflow는 부모 `execution_id`를 유지하지만 target deployment의 organization/app/workflow provenance로 바꾸고 caller invocation path를 이어간다. 이 값은 application use case의 별도 typed control parameter로만 전달하고 workflow `inputs`, template/Jinja variable map, LLM prompt/tool input 또는 node output에 병합하지 않는다. 병렬 node가 공유하는 mutable execution context에도 현재 invocation identity나 resource provenance를 덮어쓰지 않는다.
- Application은 server-derived provider/operation과 stable effect slot을 먼저 계산해 repository에서 기존 row를 조회한다. Row가 있으면 그 row의 frozen `provider_contract_version`, 두 지원 수준과 key metadata를 사용하고, row가 없을 때만 해당 provider/operation의 현재 active profile과 active key version을 새 row 후보로 선택한다. 따라서 배포 뒤 active profile이나 active key가 바뀌어도 retry, duplicate delivery와 terminal result 재사용은 최초 row의 계약과 key version을 사용한다. 동시 insert unique 충돌은 winner row를 다시 읽는다. Loser가 선택한 profile version이 winner와 다르면 새 row나 provider 호출 없이 identity conflict로 닫는다. Profile version이 같고 나머지 frozen semantic 값과 digest도 같으면 winner의 key metadata가 source of truth다. Loser가 미리 선택한 candidate key는 전송하지 않으며, key rotation 경쟁만으로 identity conflict를 만들지 않는다.
- Adapter의 network-free `prepare_effect`는 시스템 생성 provider key를 넣기 전의 최종 application request를 `PreparedEffectRequest`로 한 번 만든다. 이 값은 method, rendered target, 사용자가 지정한 header/body와 credential을 보존하고, 예약 key field 충돌을 검사하며, provider key를 제외한 effect-semantic canonical form의 SHA-256 `effect_input_digest`를 제공한다. Raw URL/header/body, credential과 token 또는 별도 credential fingerprint는 저장하지 않는다. 최초 attempt가 organization/app/workflow provenance, provider, operation, contract version, 두 지원 수준과 digest를 고정한다. Operation을 포함해 고정값 하나라도 다르면 새 row를 만들거나 기존 row를 덮어쓰지 않고 provider, 저장 결과와 downstream을 모두 사용하지 않은 채 non-retryable `external_effect.identity_conflict`로 종료한다. Provider contract version은 canonicalization 의미도 함께 고정한다.
- Claim을 실제로 소유한 delivery만 attempt row에 고정된 key version/format으로 raw provider key를 다시 생성하고 저장된 fingerprint와 비교한다. Adapter의 network-free `finalize_provider_call`은 이 검증된 key를 `PreparedEffectRequest`의 예약 위치에 정확히 한 번 넣어 immutable `PreparedProviderCall`을 만든다. `unsupported|unknown`에서는 시스템 key 없이 사용자 request를 그대로 확정한다. 이 단계는 graph/input/template을 다시 읽거나 렌더링하지 않는다. Fingerprint, key 길이 또는 예약 위치 검증이 실패하면 `in_flight` 전이와 provider 호출 전에 safe `failed_before_effect + stop`으로 닫는다. `invoke_effect`는 오직 이 최종 `PreparedProviderCall`만 전송한다.

V1 canonical framing은 domain tag와 순서가 고정된 각 field의 `name`, `value` UTF-8 bytes를 각각 unsigned 4-byte big-endian length와 이어 붙인다. UUID는 lowercase hyphenated 36자, 0 이상 정수는 leading zero 없는 base-10 ASCII로 표현하며 4 GiB 이상 field와 UTF-8 encode 실패를 거부한다. Schedule execution ID는 `uuid5(uuid.NAMESPACE_URL, "nodease:schedule-execution:v1:<lowercase-claim-uuid>")`로 계산한다.

Node invocation V1 outer frame의 field 순서는 `domain=nodease.node-invocation.v1`, `execution_id`, `segment_count`, 이어지는 각 segment의 `segment_kind`, `segment_node_id`, `segment_scope`다. Segment kind는 `root`, `loop`, `subworkflow`, `node`만 허용한다. 첫 segment는 정확히 하나의 `root`이고 `segment_node_id`는 빈 문자열, `segment_scope`는 현재 root Workflow UUID다. 중첩된 Loop iteration은 실제 nesting 순서대로 `loop`, loop graph node ID, 0-based iteration index를 넣는다. 중첩 subworkflow call은 `subworkflow`, caller WorkflowNode graph node ID, server binding으로 고정한 target deployment UUID를 넣는다. 마지막 segment는 정확히 하나의 `node`이고 현재 graph node ID와 parent invocation scope 안의 submit ordinal을 넣는다. Root와 마지막 node 사이에는 실제 nesting 순서의 loop/subworkflow segment만 허용한다. 이 scope path는 DAG predecessor/완료 순서를 나열하지 않는다. `segment_count`는 root와 node를 포함한 전체 segment 수다. Framing 전체의 padding 없는 Base64url을 name으로 사용해 `uuid5(uuid.NAMESPACE_URL, "nodease:node-invocation:v1:<name>")`를 계산한다. Gateway와 Workflow Engine이 함께 쓰는 이 framing은 `apps/shared/domain/workflow_execution_identity.py`가 소유하고 고정 vector와 independent reference test로 검증한다.

### Durable attempt lifecycle

`workflow_node_effect_attempts`를 provider replay 판단의 source of truth로 사용한다. 상태는 다음과 같다.

- `prepared`: ledger row와 실행 권한은 확보했지만 provider 호출은 시작하지 않았다.
- `in_flight`: provider 호출 직전의 durable start boundary를 넘었다. 실제 network write가 아직 없었을 수도 있지만 이 상태부터는 provider 호출이 발생했을 가능성이 있다고 보수적으로 판단한다.
- `terminal`: outcome과 replay decision이 확정됐다.

`prepared` 생성/claim, `in_flight` 전이, `terminal` 확정은 각각 독립된 짧은 DB session/transaction으로 commit한다. Claim을 얻은 뒤 최종 `PreparedProviderCall` 생성과 fingerprint 검증까지 끝난 경우에만 `in_flight`를 commit한다. Provider adapter의 network method는 이 commit 성공 뒤에만 호출하고 network I/O 동안 DB session, transaction 또는 row lock을 유지하지 않는다. `in_flight` commit이 실패하면 provider를 호출하지 않는다. Provider 호출 뒤 terminal 저장이 실패하거나 claim generation compare-and-set이 거부되면 현재 Worker는 node output을 반환하거나 downstream node를 실행하지 않는다. 이미 commit된 `in_flight`는 같은 logical execution의 다음 재진입이 outcome unknown으로 처리한다. Terminal commit이 성공한 뒤에만 최초 node output 또는 저장된 replay result를 Workflow Engine에 넘긴다. 병렬 node는 같은 SQLAlchemy session을 공유하지 않는다.

Provider port는 network-free `prepare_effect`, network-free `finalize_provider_call`과 network I/O를 수행하는 `invoke_effect`를 분리한다. Application은 stable slot 조회로 기존 row의 frozen profile 또는 새 row의 active profile을 먼저 선택한 뒤 `prepare_effect`를 호출한다. `prepare_effect`는 runtime 변수를 반영한 key-free application request를 메모리 안에서 한 번 만들고 provider key를 제외한 digest를 계산하며, 검증/직렬화 결과를 `PreparedEffectRequest` 또는 digest를 포함한 typed `failed_before_effect`로 반환한다. Application은 이 결과와 새 row 후보 key metadata로 attempt를 claim한다. 준비 실패면 `prepared -> terminal`로 닫는다. 호출 가능하고 claim을 소유한 경우에는 row에 실제로 고정된 key metadata로 key를 생성·검증한 뒤 `finalize_provider_call`이 `PreparedProviderCall`을 만든다. Concurrent insert loser의 candidate key는 폐기한다. `invoke_effect`는 이 최종 request를 그대로 전송하고 graph/input/template을 다시 읽거나 URL/header/body를 다시 렌더링하지 않는다. 두 prepared value의 URL/header/body/credential과 raw key는 DB, trace와 log에 저장하지 않는다. Profile integration test는 schema-valid input과 deterministic validation failure 모두 digest를 만들 수 있음을 검증한다. Adapter bug, UTF-8 encode 실패 또는 안전한 canonical bytes를 만들 수 없는 크기/형식처럼 prepare가 digest 자체를 만들지 못하면 application은 digest나 attempt row를 꾸며내지 않는다. 기존 winner row도 변경하지 않고 provider/result/downstream을 모두 사용하지 않은 채 non-retryable `external_effect.prepare_failed`로 종료하며, generic Celery retry를 호출하거나 raw 입력/exception message를 log하지 않는다.

`outcome`과 `replay_decision`은 `terminal`일 때만 non-null이다. Outcome은 `succeeded`, `failed_before_effect`, `effect_outcome_unknown` 중 하나다. `succeeded`는 해당 operation의 기존 node 계약에 따라 완전한 응답을 받아 node output을 확정했거나 안전한 duplicate success를 확인한 상태다. `failed_before_effect`는 local/pre-send 실패이거나 provider가 effect를 만들지 않았음을 명시적으로 확정한 rejection이다. 따라서 provider 요청 자체가 시작됐더라도 검증된 rejection이면 이 outcome을 사용할 수 있다. Generic HTTP에서는 HTTP status code만으로 기존 성공/실패 동작을 새로 정의하지 않는다. Replay decision은 다음 값으로 고정한다.

`provider_started_at`은 effect 생성 여부, request byte 전송 여부나 Python 함수의 실제 진입을 증명하지 않는다. 최종 `PreparedProviderCall` 검증 뒤 `invoke_effect` 호출을 허용하는 `in_flight` 경계를 commit한 시각이다. 따라서 commit 직후 실제 함수 호출 전에 Worker가 종료돼도 non-null이다. `prepared`에서는 null이고 `in_flight`에서는 non-null이다. Terminal `succeeded|effect_outcome_unknown`은 non-null이어야 한다. `failed_before_effect`는 network-free prepare/finalize 단계의 local 실패면 null이고, `in_flight` commit 뒤 transport가 byte 미전송을 증명한 실패나 provider가 effect 부재를 확정한 rejection이면 non-null이다. 따라서 이 outcome은 null과 non-null을 모두 허용한다. `provider_status_code`와 allowlisted `error_code`는 terminal에서만 허용한다. 이 nullability와 outcome/decision 조합은 DB CHECK와 domain validation이 함께 강제하고 reopen은 세 provider 결과 field를 모두 비운다.

- `reuse_result`: 안전하게 저장한 성공 결과를 반환하고 provider를 다시 호출하지 않는다.
- `result_unavailable`: 성공 사실은 알지만 안전하게 저장한 결과가 없다. 최초 실행은 현재 메모리의 검증된 node output으로 계속할 수 있지만, 이후 duplicate delivery는 provider를 다시 호출하지 않고 `external_effect.result_unavailable`로 종료한다.
- `retry_before_effect`: effect가 생기지 않았음이 확정되고 기존 retry 정책도 재시도를 허용한다.
- `replay_same_key`: 결과가 불명확하지만 검증된 provider 계약에 따라 최초 key와 계약 버전으로만 다시 호출할 수 있다.
- `stop`: 자동 재호출을 금지한다.

Validation/configuration처럼 다시 실행해도 같은 실패가 예상되는 `failed_before_effect`는 `stop`, provider에 effect가 생기지 않았고 요청 전송 전의 일시 실패임이 adapter에서 증명된 경우만 `retry_before_effect`를 사용한다. 공통 application policy는 HTTP status나 provider exception 문자열을 다시 해석하지 않고 adapter가 반환한 typed outcome과 retryability만 사용한다. Generic HTTP의 local validation/serialization 실패와 GitHub issue comment의 완전한 `401`, `403`, `404`, `410`, `422` rejection response는 MBA-190에서 `stop`으로 고정한다. Generic HTTP에서 DNS/TCP/TLS 연결 수립 전 실패, connect timeout과 pool timeout처럼 request byte가 전송되지 않았음을 transport가 증명한 일시 장애만 `retry_before_effect`가 될 수 있다. 일부 request byte가 전송됐을 수 있는 write timeout/error, 연결 뒤 disconnect, read timeout/error와 response loss는 `effect_outcome_unknown`이다. Generic HTTP의 완전한 non-JSON response는 기존 text output 계약 때문에 실패로 바꾸지 않는다. GitHub는 Requests가 안전한 재시도를 명시한 `ConnectTimeout`만 `retry_before_effect`로 보고, 일반 `ConnectionError`, `ReadTimeout`, response loss, `201` 뒤 JSON decode 또는 필수 field 검증 실패는 outcome unknown으로 닫는다. `MissingSchema`, `InvalidSchema`, `InvalidURL`, `InvalidHeader`는 호출 전 `stop`이다. `201`이 아닌 완전한 응답 중 위 다섯 rejection status가 아닌 `2xx`, `4xx`, `429`, `5xx`는 effect가 없었다는 공식 근거가 없으므로 `effect_outcome_unknown + stop`으로 닫는다. 명시적으로 pre-send 또는 local failure로 allowlist하지 않은 provider library exception, adapter bug와 새 exception subclass는 `in_flight` 이후 기본값인 `effect_outcome_unknown + stop`으로 닫고 generic Celery retry에 넘기지 않는다. Exception message나 중첩 원인 문자열로 이 분류를 바꾸지 않는다. 이 결정은 기존 retry 횟수와 backoff를 새로 정의하지 않는다.

`claim_owner`, `claim_expires_at`, 증가하는 `claim_generation`을 사용해 유효한 같은 claim generation에서는 한 Worker만 provider를 호출할 수 있게 하고 만료된 이전 Worker의 DB 갱신을 거부한다. `claim_owner`는 claim/reopen을 획득할 때마다 CSPRNG UUID로 새로 만드는 delivery-local opaque token이며 Celery task ID, worker hostname, execution identity나 재시도 횟수에서 파생하지 않는다. 같은 Celery task ID의 retry/redelivery도 다른 owner token을 사용한다. Provider 실행 권한을 가진 delivery의 `prepared -> in_flight|terminal`과 `in_flight -> terminal` mutation은 claim 응답의 owner token, generation과 아직 유효한 expiry를 함께 비교한다. 반면 만료 복구는 예상 status/generation과 `claim_expires_at <= DB clock`, terminal reopen·budget 소진 전이는 terminal status/generation/decision과 `claim_owner IS NULL`, `claim_expires_at IS NULL`을 비교한다. Token은 attempt column 밖의 log, trace, metric과 output에 내보내지 않는다. Fencing은 이미 시작된 network I/O를 취소하지 못하므로 claim 만료 뒤 살아 있는 이전 요청과 `supported` same-key replay가 잠시 겹칠 수 있다. 이 경우의 보장은 동시 provider 요청 금지가 아니라 검증된 provider key 계약에 따른 duplicate effect 방지다. MBA-190은 heartbeat나 원격 요청 강제 취소를 추가하지 않는다.

새 claim TTL은 Workflow Engine Celery task의 active hard time limit에 code-owned terminal commit 여유 30초를 더해 Worker composition에서 계산한다. 현재 hard time limit 600초에서는 630초이며 새 환경변수는 추가하지 않는다. Worker 시작 점검은 hard time limit이 없거나 양수가 아니면 claim을 임의 default로 만들지 않고 시작을 거부한다. Claim loser는 session/lock을 닫은 상태에서 100ms부터 최대 1초까지 증가하는 조회 간격을 사용하되 task 시작 때 계산한 monotonic deadline을 넘지 않는다. DB의 claim 획득·만료 판정은 계속 DB clock만 사용한다. 현재 Worker의 gevent pool은 soft time limit을 구현하지 않고 blocking task에서 hard time limit도 강제하지 않을 수 있으므로 이 600초 값을 process 종료 증거로 사용하지 않는다. 이 값은 claim/polling의 bounded policy input일 뿐이며, 안전성은 만료 뒤에도 이전 요청이 살아 있다고 가정한 supported same-key provider 계약과 unsupported/unknown no-replay에 둔다. Celery 제약 근거: <https://docs.celeryq.dev/en/stable/userguide/workers.html#time-limits>

- 만료된 `prepared`는 provider 호출 전이므로 같은 logical execution의 다음 재진입이 새 generation으로 이어서 실행할 수 있다.
- 만료된 `in_flight`는 같은 logical execution의 다음 재진입이 compare-and-set 조건으로 `effect_outcome_unknown`에 수렴시킨다.
- `effect_outcome_unknown + supported`는 `replay_same_key`로 분류하고 최초 provider key와 계약 버전을 그대로 사용하는 replay만 허용한다. Reopen 시 DB clock이 attempt 생성 때 고정한 `replay_deadline_at`보다 이른지도 검사한다. 이 시각은 DB clock 기준 attempt 생성 시각에 해당 contract의 retention을 더해 계산하므로 실제 최초 provider 호출보다 보수적으로 짧은 window를 만든다.
- `effect_outcome_unknown + unsupported/unknown`은 `stop`으로 분류한다.
- `succeeded + result_reuse_capability=supported`는 canonical JSON 65,536 bytes 이하 safe projection이 있으면 `reuse_result`로 분류하고 `replay_result`를 필수로 저장한다.
- `succeeded + result_reuse_capability=unavailable`은 `result_unavailable`로 분류하고 `replay_result`를 저장하지 않는다. 최초 실행은 adapter가 현재 응답에서 만든 검증된 node output을 반환할 수 있지만, duplicate delivery는 provider를 다시 호출하지 않고 typed non-retryable error로 종료한다.
- 결과 재사용을 `supported`로 선언했더라도 allowlist 적용 후 canonical JSON projection이 65,536 bytes를 넘거나 안전하게 만들 수 없으면 자르거나 raw payload를 저장하지 않는다. 최초 실행은 현재의 검증된 output으로 계속하고 해당 attempt는 `result_unavailable`로 닫는다. 이는 provider contract 위반을 안전하게 축소하는 fallback이며 profile integration test는 정상 결과가 제한 안에 있음을 증명해야 한다.

`terminal + retry_before_effect` 또는 유효 기간 안의 `terminal + replay_same_key`만 같은 ledger row를 새 claim generation의 `prepared`로 원자적으로 다시 열 수 있다. 이때 identity, 두 지원 수준, provider contract version, `replay_deadline_at`, key version/format/fingerprint는 보존하고 outcome, replay decision, provider-start/terminal timestamp와 이전 safe result/error summary는 비운다. `reuse_result`, `result_unavailable`, `stop`은 다시 열 수 없다. `replay_deadline_at`이 지난 `replay_same_key`는 같은 transaction에서 `stop`으로 닫는다. Reopen update는 이전 terminal status/decision/generation과 active claim이 없음을 조건으로 사용하고, 새 CSPRNG owner, expiry와 `generation + 1`을 한 transaction에서 기록한다. Terminal row에는 이전 claim owner/expiry가 남지 않으므로 유효 lease 조건을 요구하지 않는다.

DB CHECK constraint는 status별 nullability, enum 조합과 generation 범위처럼 시간이 지나도 변하지 않는 구조만 검사한다. Claim과 replay deadline의 실제 만료 여부는 claim/reentry/reopen/terminal update가 DB clock으로 검사한다. CHECK constraint에 현재 시각 비교를 넣지 않는다.

MBA-190은 별도 주기적 recovery task나 scheduler를 추가하지 않는다. 같은 logical execution task가 다시 들어왔을 때 자신의 attempt만 조회하고, 만료된 `in_flight`를 같은 status/generation/expiry 조건으로 `effect_outcome_unknown` terminal 상태로 바꾼다. 같은 update에서 attempt에 고정된 provider replay capability와 `replay_deadline_at`을 적용해 유효한 `supported`이면 `replay_same_key`, 그 외에는 `stop`을 기록한다. 이 상태 정리 자체는 provider를 호출하거나 workflow를 새로 시작하지 않는다. 이후 같은 task가 `replay_same_key`를 다시 열 때만 최초 key로 provider 호출을 계속할 수 있다. 재진입하지 않은 만료 row의 전역 정리는 후속 운영 작업으로 남긴다. Celery 전체 retry 횟수, backoff와 delivery/ack 정책은 MBA-190에서 바꾸지 않는다.

같은 attempt의 유효한 claim을 이미 다른 Worker가 소유하면 중복 Worker는 provider를 호출하거나 즉시 거짓 성공/실패를 반환하지 않는다. DB session이나 lock을 유지하지 않는 짧은 조회로 terminal 전이, claim 만료 또는 기존 Celery task deadline 중 먼저 오는 경계까지 제한적으로 기다린다. Terminal이면 먼저 현재 provider/contract/digest가 frozen attempt와 같은지 확인한다. `reuse_result`, `result_unavailable`, `stop`은 저장된 결과를 따르고, `retry_before_effect|replay_same_key`는 provider/reopen 없이 internal retry-permitted 결과를 task orchestration에 반환한다. Task는 기존 Celery retry budget/backoff가 허용할 때만 `self.retry`를 사용하고 실제 row reopen은 다음 delivery의 application 진입에서 수행한다. 기존 정책이 retry를 거부하거나 소진했으면 repository가 terminal outcome, terminal timestamp와 기존 allowlisted `error_code`는 유지한 채 `replay_decision=stop`으로 compare-and-set하고 provider를 호출하지 않는다. `effect_outcome_unknown`은 `external_effect.outcome_unknown`, `failed_before_effect`는 기존 safe provider failure로 현재 task를 종료한다. 이 전이는 기존 retry 횟수/backoff를 새로 정하지 않고 broker redelivery의 budget 우회만 막는다. 만료된 상태가 `in_flight`면 위 결과 불명 상태 정리만 수행하고 같은 진입에서 reopen하거나 provider를 호출하지 않는다. 만료된 상태가 `prepared`면 row를 바꾸거나 claim하지 않고 대기를 끝내며, 다음 동일 execution delivery만 새 generation으로 claim할 수 있다. Task deadline이 먼저 오면 attempt를 변경하지 않고 provider를 호출하지 않은 채 기존 task timeout/retry 경계로 종료한다.

`workflow_run_id`와 `node_run_id`는 nullable correlation column으로 저장할 수 있지만 FK를 두지 않는다. Log System row가 늦게 생성되거나 생성되지 않아도 provider 호출 전 attempt insert가 가능해야 한다.

Workflow Engine은 terminal duplicate delivery에서도 frozen contract로 digest를 다시 계산하고 저장된 decision을 확인해야 하므로, 시작 시 모든 attempt row가 참조하는 distinct `(provider, operation, provider_contract_version)`을 registry에서 찾을 수 있는지 검사한다. 누적 history를 순차 조회하지 않도록 같은 세 컬럼의 일반 index를 둔다. HMAC key readiness는 provider 재호출 가능성이 남은 `prepared`, `in_flight`, `retry_before_effect|replay_same_key` supported row만 대상으로 하며 `(provider, operation, provider_contract_version, key_version)` partial index를 별도로 사용한다. 어느 index도 전역 recovery scanner용이 아니다. Schema readiness는 기존 `apps/shared/services/alembic_readiness.py`의 code head와 DB revision 비교를 재사용하고 external-effect table의 필수 column, constraint와 index shape를 별도로 검사한다. DB의 `alembic_version`이 MBA-190 revision ID와 정확히 같은지만 비교해서는 안 된다. 이후 migration descendant가 적용된 DB도 현재 code head와 일치하고 필수 schema shape가 있으면 유효해야 하며, 별도 revision 판정기를 중복 구현하지 않는다.

### Provider replay capability and result reuse

각 provider operation은 서로 독립적인 두 지원 수준과 versioned provider contract를 선언한다.

Versioned provider contract profile은 다음 값을 명시한다.

- `provider`, canonical `operation`, `contract_version`
- 중복 방지 지원 수준과 결과 재사용 지원 수준
- 중복 방지 key 전달 위치(`header`, `body`, `none`, `unknown`)와 header field 이름 또는 canonical body JSON Pointer
- key alphabet/format, 최대 길이와 provider retention
- replay result allowlist/schema와 전역 canonical JSON 65,536 bytes 이하의 provider별 최대 크기
- effect success/rejection 판정과 duplicate success/conflict/response reuse 의미
- Provider key를 제외한 effect-semantic request의 canonicalization과 `effect_input_digest` 계산 규칙
- 판단 근거가 된 공식 문서의 정적 reference

Canonical operation과 최초 contract version은 다음 값으로 고정한다. 같은 stable effect slot에서 node mode, action 또는 operation이 바뀌면 새 attempt가 아니라 identity conflict다.

| Node 경로 | `provider` | `operation` | `contract_version` | MBA-190 적용 범위 |
| --- | --- | --- | --- | --- |
| `httpRequestNode` | `generic_http` | `generic_http.request` | `generic_http.request.v1` | `POST`, `PUT`, `PATCH`, `DELETE`만 external effect 경계 적용. RFC 9110 safe method인 현재 지원 `GET`은 기존 조회 경로 유지 |
| `slackPostNode` | `slack` | `slack.http.request` | `slack.http.request.v1` | Logical side-effect node이므로 현재 runtime이 허용하는 모든 method, API endpoint 변경과 incoming webhook을 같은 보수적 external-effect profile로 처리 |
| `githubNode`, `comment_pr` | `github` | `github.issue_comment.create` | `github.issue_comment.create.v1` | Issue comment 생성만 적용. `get_pr` 제외 |
| Test-only fake | `fake` | `fake.create_effect` | `fake.create_effect.v1` | Test composition에서만 사용 |

Generic HTTP `GET`과 GitHub `get_pr`는 새 attempt를 만들지 않지만 동일 node invocation의 `effect_sequence=0`에 기존 effect row가 있는지는 먼저 조회한다. Row가 없을 때만 기존 read 경로를 실행한다. Row가 있으면 이전 mutation/comment를 read로 바꿔 stable slot 검사를 우회한 것이므로 read provider, 저장 결과와 downstream을 모두 사용하지 않고 `external_effect.identity_conflict`로 닫는다. 이 조회는 read 성공 이력을 ledger에 새로 기록하지 않는다. `slackPostNode`는 logical side-effect node이므로 method와 무관하게 항상 effect 경계를 통과한다.

`slackPostNode`는 현재 `HttpRequestNode` class와 `HttpRequestNodeData`를 함께 재사용하고 Client가 API endpoint를 바꿀 수 있다. MBA-190은 이 기존 요청 동작을 유지하며 Slack 전용 data schema나 mode/method/target 제한을 새로 추가하지 않는다. Server가 검증한 `NodeSchema.type`이 Slack profile을 선택하고, URL, `slackMode` 또는 header는 capability를 `supported`로 올리거나 다른 profile을 선택할 수 없다. Mode별 provider-aware 응답 판정은 MBA-218이 소유한다.

Generic HTTP와 Slack의 request canonicalization V1은 template 적용 후 실제로 전송할 method, rendered target, 시스템 provider key를 넣기 전의 case-insensitive application header와 body를 length-delimited encoding으로 만든다. 렌더링된 body가 `None` 또는 빈 문자열이면 현재 `if body` 분기와 같이 no-body 요청으로 처리하고 사용자가 지정한 `Content-Type`을 보존한다. 빈 문자열이 아닌 body는 JSON으로 parse한다. Parse 결과가 JSON `null`이면 현재 HTTPX 0.28.1의 `json=None` 동작과 같이 사용자 `Content-Type`은 제거하지만 자동 `application/json`을 추가하지 않고 zero-byte body를 보낸다. 그 밖의 JSON 값은 사용자 `Content-Type` 변형을 제거하고 고정 `content-type: application/json`을 한 번 포함한다. Effective header는 `httpx.Headers(...).multi_items()` 결과를 소문자 이름별로 묶고 이름은 정렬하되 같은 이름의 여러 값은 순서를 보존한다. 인증 header 값도 digest 입력에는 포함한다. Digest용 JSON semantic form은 object key를 정렬해 key 순서만 다른 동등 JSON을 같은 값으로 보지만, `PreparedEffectRequest`는 원래 parsed value의 insertion order를 보존한다. Claim 뒤 `finalize_provider_call`은 시스템 key만 예약 위치에 추가해 `PreparedProviderCall`을 만들고, invoke는 HTTPX 0.28.1의 `ensure_ascii=False`, compact separator, NaN 금지 UTF-8 wire serialization을 그대로 사용한다. Digest 정규화나 key 주입을 이유로 실제 전송 JSON key 순서를 바꾸지 않는다. 공백만 있는 body, NaN/Infinity처럼 HTTPX wire serialization이 거부하는 값과 JSON parse 실패는 `invalid_json` stage tag와 렌더링된 원문 bytes로 deterministic `failed_before_effect + stop`을 만든다. 두 prepared value는 no-body/JSON-null-no-body/JSON 전송 mode, effective header와 parsed JSON 값을 보존해 invoke가 다시 분기하거나 header/body를 조립하지 않게 한다. 이 wire semantics가 dependency 변경으로 달라지면 기존 contract version을 수정하지 않고 새 version을 추가한다. GitHub V1은 canonical repository owner/name, PR number, rendered comment body와 인증 값을 포함하고 Requests가 자동으로 붙이는 transport header는 contract version으로 고정한다. Credential과 원문 canonical bytes는 digest 계산 뒤 보존하지 않는다.

`generic_http.request.v1`의 각 field frame은 `uint32-be(name byte length) + UTF-8 name + uint32-be(value byte length) + value bytes`다. Field 순서는 `domain=nodease.generic-http-request.v1`, `method`, `target`, `header_count`, 정렬된 header마다 `header_name`, `header_value`, 마지막으로 `body_mode`, `body`로 고정한다. `no_body`의 body는 empty bytes, `json_null_no_body`는 canonical `null`, 정상 JSON은 object key를 정렬한 compact UTF-8 JSON, `invalid_json`은 렌더링된 원문 UTF-8 bytes다. 이 field 이름, 순서, 길이 표현과 domain 값은 V1 계약이며 변경하려면 새 request semantics version이 필요하다.

Provider replay `supported` profile의 예약 key header/body field가 준비된 사용자 요청에 이미 있으면 시스템 값을 덮어쓰거나 두 값을 함께 보내지 않는다. Header field 충돌은 RFC case-insensitive 이름으로 비교하고, body field는 profile이 고정한 canonical JSON Pointer의 기존 값 존재 여부로 비교한다. Adapter는 network 전에 allowlisted `provider_key_field_conflict`로 `failed_before_effect + stop`을 반환한다. `unsupported|unknown` profile에서는 시스템 key가 없으므로 사용자 field를 그대로 유지하고 effect input digest에 포함한다.

공식 문서에서 확인할 수 없는 값은 code default, 경험적 추정 또는 사용자 입력으로 보충하지 않고 `unknown`으로 둔다. Generic HTTP node에 사용자가 `Idempotency-Key` 같은 header를 넣어도 대상 API의 공식 계약과 전용 integration test가 없으면 `supported`로 승격하지 않는다. Contract version이 가리키는 profile은 변경 불가능한 항목으로 추가만 하며, 한 번 사용한 과거 version은 수정하거나 제거하지 않는다. Attempt 생성 시 contract retention으로 `replay_deadline_at`을 고정하고 이후 배포의 현재 retention으로 다시 계산하지 않는다. Workflow Engine Worker 시작 점검은 terminal duplicate delivery의 digest 검증까지 가능하도록 모든 attempt row가 참조하는 과거 contract version을 해석할 수 있는지 확인한다. 누락된 version이 있으면 현재 contract로 추측하지 않고 provider task 처리 전에 fail-closed한다.

Provider replay capability:

- `supported`: Key 전달 위치가 `header|body`이고 공식 문서와 adapter integration test가 field 이름, format, length, retention과 duplicate/conflict semantics를 증명한다. MBA-190에서는 key 전달 위치 `none|unknown` profile을 `supported`로 선언하지 않는다.
- `unsupported`: provider가 중복 방지 계약을 제공하지 않거나 적용할 수 없다고 확인됐다.
- `unknown`: 계약이 없거나 아직 충분히 검증하지 못했다. Runtime replay 정책은 `unsupported`와 동일하게 보수적으로 동작한다.

Result reuse capability:

- `supported`: 최초 성공과 duplicate delivery에서 같은 node output schema를 제공할 수 있는 allowlisted `replay_result` projection이 있다. 전역 상한은 allowlist 적용 뒤 `json.dumps(sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")`와 동등한 canonical JSON 65,536 bytes이며 provider contract는 이보다 작은 상한을 둘 수 있다. JSON으로 표현할 수 없는 값과 NaN/Infinity는 projection 실패로 처리한다.
- `unavailable`: raw response/header/body를 저장하지 않고는 결과를 재구성할 수 없다. 최초 실행은 현재 응답으로 정상 진행할 수 있지만 성공 사실만 영속 저장하며 duplicate delivery는 `external_effect.result_unavailable`로 종료한다.

두 지원 수준은 독립적이다. Provider replay가 `supported`여도 안전한 결과 projection이 없으면 result reuse는 `unavailable`일 수 있다. Result reuse가 `supported`여도 provider outcome unknown replay를 허용하려면 provider replay capability가 별도로 `supported`여야 한다.

MBA-190 1차 구현에서 test-only fake provider는 provider replay `supported`의 duplicate semantics와 장애 구간을 검증한다. Fake adapter의 safe result projection 유무로 result reuse `supported|unavailable`을 검증하고, provider replay `unsupported|unknown`은 pure domain/application policy test에서 검증한다. Fake의 명시적 key field는 공통 코드 경로 검증용이며 production provider 지원 또는 exactly-once 주장의 근거가 아니다.

Fake provider contract `fake.create_effect.v1`은 테스트 재현성을 위해 다음 값으로 고정한다.

- Key는 `Idempotency-Key` request header에 전달한다.
- HMAC-SHA256 digest를 padding 없는 Base64url로 표현한 43자 문자열을 사용하며 provider 최대 길이는 64자다.
- Provider retention은 최초 수락 시각부터 24시간이다. Runtime의 `replay_deadline_at`은 attempt 생성 DB 시각부터 24시간으로 더 보수적으로 고정한다.
- 최초 valid request는 effect를 하나 만들고 `201`과 UUID 문자열 `effect_id` 하나만 포함한 `{"effect_id":"..."}` JSON을 반환한다.
- Retention 안에서 같은 key와 같은 canonical request가 오면 effect를 추가로 만들지 않고 `200`과 최초와 같은 `effect_id` JSON을 반환한다.
- 같은 key에 다른 canonical request가 오면 effect를 만들지 않고 `409`를 반환한다.
- Fake adapter가 이 `409`를 받으면 duplicate success나 replay result로 취급하지 않는다. Provider가 새 effect 부재를 확정한 `failed_before_effect + stop`과 allowlisted `provider_key_request_conflict`로 닫는다. 정상 application 경로는 stored digest 불일치에서 먼저 차단하므로 이 응답은 provider conformance와 방어적 adapter test에서만 발생해야 한다.
- 24시간 경계 시각부터 같은 key는 새 effect로 취급하고 다시 `201`을 반환한다. Runtime은 `replay_deadline_at` 경계부터 자동 replay하지 않는다.
- Fake adapter의 result reuse `supported` projection과 최초/중복 node output은 `{"effect_id":"..."}`만 허용한다. `unavailable` variant는 projection을 만들지 않는다.
- Test clock을 주입할 수 있어 경계 직전과 경계 시각을 실제 대기 없이 검증한다.

현재 production operation 분류는 다음과 같다.

| Operation | Provider replay | Result reuse | 근거와 MBA-190 처리 |
| --- | --- | --- | --- |
| Generic HTTP mutation (`POST`, `PUT`, `PATCH`, `DELETE`) | `unknown` | `unavailable` | 목적지별 공식 계약을 알 수 없으므로 사용자 지정 header와 관계없이 보수적으로 분류하고 공통 경계에 연결. 현재 지원 safe method `GET`은 effect ledger 밖의 기존 조회 경로 유지. 완전한 HTTP 응답은 status code와 관계없이 기존 node output을 반환하고 `succeeded`로 닫음. Local validation은 `failed_before_effect + stop`, transport가 request 미전송을 증명한 연결 전 일시 장애만 `retry_before_effect`, write/read timeout·연결 뒤 disconnect·response loss는 `effect_outcome_unknown` |
| Slack HTTP request (모든 현재 허용 method) | `unknown` | `unavailable` | Logical side-effect node가 API endpoint 변경과 incoming webhook을 같은 HTTP runtime으로 처리하므로 method/endpoint별 안전성을 추정하지 않는 단일 보수적 profile로 연결. 공식 `chat.postMessage`와 incoming webhook 문서도 key 형식·길이·보존 기간·duplicate 결과 계약을 완전히 정의하지 않으며 provider-aware 응답 판정은 MBA-218에 유지 |
| GitHub `create issue comment` | `unsupported` | `unavailable` | 공식 endpoint가 comment body만 정의하고 idempotency field를 제공하지 않으므로 공통 경계에 연결하고 outcome unknown 자동 재호출 금지 |

각 production V1 profile의 미확인 값도 다음처럼 명시적으로 고정한다. `null`은 시스템 field가 없다는 뜻이고 `unknown`은 값을 추정하지 않는다는 뜻이다.

| Contract | Key 위치 | 시스템 field | 형식/최대 길이 | Retention | Duplicate/conflict 의미 | Result 의미 |
| --- | --- | --- | --- | --- | --- | --- |
| `generic_http.request.v1` | `unknown` | `null` | `unknown` | `unknown` | 목적지별 공식 계약을 알 수 없어 모두 `unknown`; 시스템 key 생성 없음 | 완전한 최초 응답만 기존 `status/data/headers`로 반환. `replay_result` 없음 |
| `slack.http.request.v1` | `unknown` | `null` | `unknown` | `unknown` | API endpoint와 incoming webhook 전체에 적용할 완전한 duplicate/conflict 계약이 없어 `unknown`; 시스템 key 생성 없음 | 완전한 최초 HTTP 응답만 기존 `status/data/headers`로 반환. `replay_result` 없음 |
| `github.issue_comment.create.v1` | `none` | `null` | 적용 안 함 | 적용 안 함 | 공식 idempotency/duplicate-success field가 없어 적용 가능한 중복 제거 계약 없음 | 검증된 최초 `201`만 기존 `comment_id/comment_url/comment_body`로 반환. `replay_result` 없음 |

Gmail `users.drafts.create`는 현재 공식 문서상 idempotency field가 없어 참고 분류는 `unsupported`지만 MBA-190에서 production adapter를 연결하지 않는다. Gmail inbound fetch/claim/terminal acknowledgement와 reply draft는 MBA-217 및 ADR-0032의 Mail 전용 ledger가 소유하고, Slack `ok=false`, error code와 `Retry-After` 처리는 MBA-218이 소유한다. MBA-190은 실제 production operation을 `supported`로 올리지 않는다.

### Versioned provider key

- Provider replay가 `supported`이고 key 전달 위치가 `header` 또는 `body`인 operation만 provider-visible key를 생성하고 전달한다. `unsupported|unknown` 또는 전달 위치 `none|unknown`에서는 시스템 key를 생성·주입하거나 사용자가 입력한 header/body를 덮어쓰지 않는다.
- Provider-visible key는 전용 idempotency HMAC secret과 canonical identity를 사용해 HMAC-SHA256으로 생성한다. 로그인, session 또는 token 서명용 `SECRET_KEY`를 재사용하지 않는다.
- `key_format_version=hmac-b64url-v1`은 domain tag `nodease.external-effect-key.v1`과 위 V1 framing을 사용한다. Field 순서는 `organization_id`, `app_id`, `workflow_id`, `execution_id`, `node_invocation_id`, `operation`, `effect_sequence`로 고정하고 HMAC-SHA256 digest를 padding 없는 Base64url로 표현한다. Provider profile의 prefix/길이 변환은 이 결과에 적용한다.
- Attempt의 `key_version`, `key_format_version`, key fingerprint와 `replay_deadline_at`은 provider replay가 `supported`일 때만 저장한다. Fingerprint는 최종 provider-visible key ASCII bytes의 SHA-256 lowercase hex 64자이며 operational attempt column에서 같은 key 재생성을 검증하는 데만 사용한다. `unsupported|unknown`에서는 모두 null이다. `provider_contract_version`은 모든 attempt에 저장한다. Provider-visible raw key는 저장하지 않는다.
- 새 attempt는 active key version을 사용하고, retry는 최초 attempt의 key와 format version을 사용한다. 필요한 과거 key가 없으면 새 key를 만들지 않고 fail-closed한다.
- 최초 insert 경쟁 중 candidate active key version이 달라도 winner row의 key version/format/fingerprint가 source of truth다. Claim을 소유하지 못한 delivery는 자기 candidate key를 폐기한다. 이후 claim을 얻은 delivery는 winner row의 key를 재생성해 fingerprint를 검증한 뒤에만 `PreparedProviderCall`에 주입한다.
- 과거 key는 해당 key를 참조하는 nonterminal 또는 replay 가능한 attempt가 존재하는 동안 보존한다.
- 현재 production operation처럼 `supported` profile도, 과거의 재호출 가능한 supported attempt도 없으면 HMAC key ring은 Worker 시작 필수 설정이 아니다. Test-only fake는 production 환경 설정이 아니라 test composition에서 key를 주입한다.
- MBA-190은 attempt cleanup을 추가하지 않는다. 후속 cleanup은 broker/task duplicate-delivery 최대 기간이 끝나고, `replay_deadline_at`이 non-null이면 그 시각도 지났으며, row가 더 이상 reopen될 수 없음을 증명하기 전 row 또는 `replay_result`를 삭제해서는 안 된다.

### Trace and logging

Durable trace와 application log에는 provider, operation, HTTP method, status code, request/response size, latency, 두 지원 수준, outcome, replay decision과 safe error code로 제한한 summary만 허용한다. Generic HTTP가 기존에 기록하던 목적지 `host`와 `path`는 현재 trace 계약을 유지한다. `WorkflowNodeRun`뿐 아니라 `WorkflowRun.outputs`와 run-level trace payload도 같은 경계에 포함한다. 외부 provider node 결과를 전달받는 downstream node는 durable node input/process/output과 durable run output을 저장하지 않는다. 중첩 Workflow/Loop가 외부 provider 결과를 반환하면 server-owned 민감 표시를 부모 실행으로 전달해 컨테이너 노드와 그 downstream에도 같은 정책을 적용한다. 실제 workflow downstream 값과 사용자 응답은 기존 shape를 유지한다. Key fingerprint는 operational attempt column에만 허용하고 durable trace, application log와 metric label에는 넣지 않는다. Trace와 attempt의 연결은 nullable `workflow_run_id`/`node_run_id` correlation column으로 수행한다.

전체 URL, query, fragment, URL user info, request/response header와 body, credential, token, provider-visible raw key, internal identity, provider request identifier와 provider library exception message 원문은 trace 또는 application log로 남기지 않는다. Generic HTTP의 기존 `host` field는 URL user info를 포함하는 `netloc`을 그대로 쓰지 않고 parsed hostname과 명시 port만 조합하며, 기존 `path` field는 유지한다. Path 자체의 추가 비식별화 정책은 MBA-190에서 바꾸지 않고 별도 보안 결정으로 다룬다. Slack은 webhook path가 secret일 수 있고 endpoint도 사용자 설정이므로 URL/host/path 대신 `slack.http.request` canonical operation만 기록한다. 오류는 allowlisted code와 exception class로 변환한다.

Workflow Engine composition은 `httpx`, `httpcore`, `urllib3`와 Requests 호환 transport logger가 전체 URL, header 또는 예외 원문을 독자적으로 출력하지 못하도록 해당 하위 logger record를 Worker에서 억제하고, provider adapter가 위 allowlist safe summary만 별도로 남기게 한다. Gateway/Workflow Engine의 공통 `workflow.*` publish helper도 serialization/broker 실패를 raw `str(exc)`나 traceback으로 기록하지 않고 static operation과 exception class만 남기며 기존 surface의 safe 실패 envelope로 변환한다. 이 설정은 공용 Shared signal이나 Log System Worker에 전역 등록하지 않는다.

현재 Celery는 Worker task event를 보내며 task 수신 INFO log와 `task-received` event가 `argsrepr`/`kwargsrepr`을 사용한다. 저장소 안에서 `app.send_task`로 발행하는 모든 `workflow.*` publisher helper는 ID, graph, input과 internal binding을 포함하지 않는 고정 redacted repr을 message header에 넣는다. Workflow task 전용 Task base는 `apply_async`의 `argsrepr`/`kwargsrepr` 기본값을 같은 상수로 강제해 `.delay`, signature와 `self.retry`의 sender-side `task-sent` event도 보호한다. Workflow task 전용 Request base는 consumer가 받은 값을 수신 INFO log와 `task-received` event 발행 전에 다시 같은 상수로 덮어써 legacy 또는 저장소 밖 publisher도 Worker 쪽에서 보호한다. 실제 broker payload는 task 실행을 위해 유지하지만 log, event와 metric으로 복제하지 않는다. 저장소 밖 publisher가 자기 sender-side `task-sent` telemetry를 운영한다면 같은 redacted repr을 설정해야 하며, Nodease Worker는 외부 publisher의 자체 log를 통제할 수 없다.

Celery 5.6.3의 근거는 [worker strategy](https://github.com/celery/celery/blob/v5.6.3/celery/worker/strategy.py), [request](https://github.com/celery/celery/blob/v5.6.3/celery/worker/request.py), [task publish/retry](https://github.com/celery/celery/blob/v5.6.3/celery/app/task.py), [AMQP message/event](https://github.com/celery/celery/blob/v5.6.3/celery/app/amqp.py) 구현이다. Dependency upgrade로 이 hook 순서나 event field가 바뀌면 integration test를 먼저 갱신한다.

Generic HTTP/Slack의 현재 `status/data/headers`와 GitHub comment의 기존 node output은 workflow 데이터 흐름 계약이어서 MBA-190에서 shape나 값을 바꾸지 않는다. 이 호환 결정은 PRD NFR-004의 raw payload 응답 금지와 이미 존재하는 불일치를 해결하거나 새 raw payload 노출을 허가하는 결정이 아니다. MBA-190은 새 identity/key/fingerprint를 output에 추가하지 않고 Generic HTTP/Slack/GitHub 경로의 durable trace/log raw payload를 제거한다. 기존 external node output의 allowlist/redaction과 downstream 호환 정책은 별도 보안 이슈와 제품 계약 변경으로 다룬다.

External effect 오류는 JSON-safe `{code, message, retryable, node_id?}` payload로 정규화한다. 최소 고정 code는 안전한 결과가 없는 중복 전달의 `external_effect.result_unavailable`, 같은 identity의 frozen 계약 불일치인 `external_effect.identity_conflict`, 결과 불명 `stop`의 `external_effect.outcome_unknown`이다. 세 code는 `retryable=false`다. Node/application error부터 Workflow Engine, Celery task, Pub/Sub와 Gateway까지 단순 문자열이나 custom exception attribute에만 의존하지 않고 payload를 보존한다. Non-retryable task는 Celery exception 직렬화에 맡기지 않고 내부 `status=error` result 또는 stream error payload로 변환하며, Gateway는 기존 endpoint HTTP status와 top-level error envelope을 유지한 채 이 safe payload를 mapping한다. Provider 원문, digest나 내부 identity/key를 넣지 않는다.

Process 내부 전달에는 JSON-safe payload를 가진 `ExternalEffectControlSignal` 계열을 사용하되 Celery serialization 경계까지 예외 객체를 보내지 않는다. `ExternalEffectRetrySignal`과 terminal `ExternalEffectSafeError`는 Loop의 `error_strategy=continue`, Node base의 일반 예외 문자열화, WorkflowEngine의 generic catch보다 먼저 잡아 원형 payload로 re-raise한다. Loop 결과의 `{"error": str(e)}`로 축소하거나 workflow 성공으로 바꾸지 않는다. WorkflowEngine은 control signal을 generic `str(e)` log/PubSub 경로에 넣지 않고 safe run error만 기록한 뒤 task까지 전달한다. Stream task가 safe `error` event를 한 번만 발행하고 task 경계가 retry signal은 기존 `self.retry`, terminal error는 JSON-safe `status=error` result로 변환한다. Root task와 `WorkflowNode`의 nested engine cleanup 및 DB session close는 실행 결과를 바꾸지 않는 best-effort 정리다. 정리 실패는 exception type만 기록하고 원래 control signal, non-retryable safe result 또는 terminal commit 뒤의 성공 output을 덮어쓰거나 generic retry를 시작하지 않는다. `WorkflowEngine.cleanup()`은 child 하나의 정리 실패 뒤에도 나머지 reference를 비우고 호출자에게 정리 예외를 전파하지 않는다.

Gateway surface별 기존 응답 모양은 유지한다. 일반 동기 실행과 deployment run은 기존 HTTP status의 `detail`에 safe payload를 넣고, Compare variant는 기존 safe `error` 문자열과 additive `error_detail`, Cost Optimizer candidate는 기존 safe `error_message`와 additive `error_detail`을 사용한다. Stream은 `error` event data에 네 field를 넣는다. Webhook은 접수 응답을 사후 실패로 바꾸지 않으며 caller가 사용하지 않는 Celery result 안에서 payload를 보존하고 raw 오류를 log하지 않는다. Schedule claim의 enum/check constraint에는 `external_effect.*`를 추가하지 않는다. `external_effect.outcome_unknown`은 기존 `execution_outcome_unknown`, `external_effect.result_unavailable`과 `external_effect.identity_conflict`는 기존 `execution_failed_after_admission`으로 finalization한다. 구체 code는 task-local safe error와 허용된 safe trace에서 전달하되 schedule claim에는 저장하지 않고, identity conflict 때문에 winner attempt row를 덮어쓰지도 않는다. Terminal attempt에 적용 가능한 allowlisted `error_code`는 저장할 수 있다. MBA-187의 claim schema, 상태 전이와 재시도 정책은 바꾸지 않는다.

### Code ownership

- `apps/shared/domain/workflow_execution_identity.py`는 Gateway publisher와 Workflow Engine이 함께 쓰는 `execution_id` 생성/검증 contract만 소유한다.
- `apps/shared/domain/workflow_node_binding.py`는 binding metadata schema, canonical snapshot hash, Loop-aware graph traversal, 공통 cycle/depth constant와 legacy child closure의 effect-capable 판정 같은 DB 비의존 규칙만 소유한다. Domain은 catalog 파일이나 `apps/shared/services/` loader를 import하지 않고, composition이 `apps/shared/services/workflow_node_catalog.py`에서 읽어 불변 값으로 만든 `node_type -> side_effect` mapping을 생성자/함수 인자로 받는다. Effect-capable 판정은 이 mapping의 `external_write`를 기본으로 하고 `httpRequestNode GET`, `githubNode get_pr`처럼 이 ADR이 명시한 read-only action만 낮춘다. Catalog에 없거나 malformed인 node/action과 새 `external_write` node는 안전하다고 추정하지 않는다.
- `apps/shared/services/workflow_task_publisher.py`는 Gateway와 Workflow Engine의 `app.send_task` 기반 `workflow.*` 발행에 redacted repr과 raw publish exception 비노출을 제공하는 infrastructure helper다.
- `apps/gateway/application/deployment/workflow_node_binding.py`는 기존 `preflight.py`와 같은 `DeploymentPreflightRepository` port, shared walker와 정책 constant를 사용하는 binding use case를 소유한다. `apps/gateway/composition/deployment.py`가 기존 SQLAlchemy repository adapter를 조립하고 `DeploymentService`는 호환 facade로 use case를 호출한다. 새 DB resolver나 정책 판단을 `apps/gateway/services/`에 만들지 않는다.
- External-effect schema readiness는 기존 `apps/shared/services/alembic_readiness.py`를 호출하는 Workflow Engine 전용 composition checker가 소유한다. Shared Celery signal에 새 점검을 등록하거나 MBA-190 revision ID의 정확한 일치만 검사하지 않는다.
- `apps/workflow_engine/domain/external_effect.py`는 provider key framing, lifecycle invariant와 두 지원 수준/outcome/replay policy 같은 Workflow Engine 전용 pure rule을 소유한다. 공용 execution/node invocation framing은 Shared 경계에 둔다.
- `apps/workflow_engine/application/`은 attempt claim, provider 호출 허용, terminal update와 replay result 반환 순서를 조율한다.
- `apps/workflow_engine/adapters/db/`는 SQLAlchemy repository를, `apps/workflow_engine/adapters/providers/`는 provider별 network 동작과 지원 수준 profile을 구현한다.
- `apps/workflow_engine/composition/`은 DB session factory, repository, key provider와 provider adapter를 조립한다. 각 persistence operation은 factory에서 새 session을 받는다.
- `apps/workflow_engine/composition/external_effect_logging.py`는 Workflow Engine에만 provider transport logger 억제와 safe adapter summary를 조립한다.
- `apps/shared/db/models/`은 공통 SQLAlchemy model을 소유한다.
- Node class와 Celery task는 application use case를 호출하는 얇은 진입점으로 유지한다.
- `apps/workflow_engine/tasks.py`의 workflow task 전용 Task base는 `apply_async` 기본 repr을, Request base는 수신 직후 repr을 low-cardinality redacted 상수로 고정한다. 각 Gateway/Workflow Engine `app.send_task` publisher도 공통 helper로 같은 값을 넣는다. Task 본문이나 external-effect domain service가 telemetry redaction을 사후 처리하지 않는다.
- Fake provider/adapter/server는 `apps/workflow_engine/tests/fakes/` 또는 integration test support에만 두고 production composition과 environment switch에서 참조하지 않는다.
- External effect domain policy는 Workflow Engine 밖의 실제 consumer가 생기기 전에는 `apps/shared/domain/`으로 이동하지 않는다.
- Table/revision, 과거 provider contract와 필요한 HMAC key ring readiness는 Workflow Engine Worker composition에서만 등록한다. 공용 Celery app이나 Log System Worker 시작 조건으로 연결하지 않는다.

### Execution surface coverage

Draft test execution, legacy deployed execution, deployment ID 기반 API/public/webhook execution, schedule execution, stream execution과 subworkflow는 같은 execution identity contract를 사용한다. 최초 publisher/direct orchestrator가 logical execution마다 `execution_id`를 한 번 생성해 queue payload 또는 immutable command에 넣고 Worker retry는 그대로 보존한다. Compare A/B variant는 각각 별도 logical execution ID를 발급하며 variant 내부 retry에서만 유지한다. 각 진입점은 queue 값만 신뢰하지 않고 canonical DB resource에서 organization/app/workflow provenance를 확인한다. Subworkflow는 부모 `execution_id`를 유지하고 별도 `node_invocation_id`로 호출 위치를 구분하되 target deployment의 app/workflow provenance를 사용한다. Retry 또는 duplicate delivery에서 `execution_id`나 canonical resource provenance가 없으면 새 값을 생성하거나 owner로 fallback하지 않고 provider 호출 전에 fail-closed한다.

Legacy `workflow.execute_deployed`는 현재 저장소 안에 발행자가 없고 기존 command가 `workflow_id`만으로 active deployment를 고른다. Worker가 최초 실행에서 고른 snapshot을 `self.retry` 인자에만 추가하면 브로커의 원본 메시지 재전달에는 적용되지 않으므로 안전한 고정으로 보지 않는다. Effect-capable legacy 호출은 최초 broker message의 additive optional command envelope에 publisher-issued `execution_id`, exact `deployment_id`, `deployment_version`, canonical `snapshot_sha256`를 모두 제공해야 한다. Worker는 네 값을 canonical DB row와 검증하고 같은 envelope을 retry에 유지하며 active deployment를 다시 선택하지 않는다. 같은 hash를 가진 다른 deployment도 bound ID 대신 사용할 수 없다. 기존 positional task 인자는 유지한다. Envelope이 없는 legacy 호출은 외부 effect가 없는 graph에서만 기존 호환 동작을 유지하고, effect boundary에 도달하면 identity 또는 snapshot binding을 만들지 않은 채 provider 전에 fail-closed한다. 저장소 밖 publisher 갱신과 별도 admission ledger는 MBA-190 구현 범위에 포함하지 않는다.

`WorkflowNode`도 parent retry마다 target app의 현재 active deployment를 다시 선택하지 않는다. 배포 생성 시 Gateway가 graph와 Loop subgraph 안의 각 `workflowNode`에 대해 canonical target deployment ID/version/snapshot hash를 server-owned internal binding metadata로 계산해 immutable `graph_snapshot`에 넣는다. Draft/test, Compare와 stream publisher는 각 surface가 기존 계약으로 선택한 실행 graph의 server-owned 복사본에 같은 binding을 계산한 뒤 최초 queue command에 넣는다. DB draft를 사용하는 surface는 그 draft를 유지하고, stream처럼 검증된 request `graph_snapshot` 실행을 이미 허용하는 surface는 저장되지 않은 요청 graph를 그대로 실행 대상으로 유지한다. Binding 계산을 이유로 client graph를 DB draft로 대체하거나 저장하지 않는다. Client가 넣은 internal metadata와 deployment clone, template/import 등 graph 복사 경로에 남은 동일 이름 metadata는 신뢰하지 않고 제거한 뒤 현재 canonical DB target으로 재계산한다. 모든 client-facing draft/deployment/copy/export graph 응답은 이 metadata를 제거한다. Runtime은 binding으로 정확한 target deployment와 snapshot hash를 DB에서 재검증하고 child graph가 가진 다음 단계 binding을 이어 쓴다. MBA-190 이전 snapshot이나 legacy command에 binding이 없으면 동적으로 고른 child graph와 그 하위 closure에 effect-capable node가 없음을 server가 확인한 경우에만 기존 동작을 유지하고, 하나라도 있거나 분류할 수 없으면 child provider 호출 전에 fail-closed한다. 이 판정에는 Generic HTTP mutation, 모든 Slack request와 GitHub comment뿐 아니라 ADR-0032의 별도 ledger를 계속 사용하는 `gmailDraftNode`, `mailAcknowledgeNode`와 향후 catalog의 `external_write` node도 포함한다. Binding 판정에 포함하는 것은 Gmail effect를 `workflow_node_effect_attempts`에 이중 기록한다는 뜻이 아니다. 새 admission table은 추가하지 않는다.

Binding을 생성할 때는 target deployment가 해당 app의 current active deployment이고 WorkflowNode child surface에서 허용되는 type인지 확인한다. Binding을 소비하는 retry/duplicate 실행에서는 exact deployment row 존재, app/workflow/organization provenance, 허용 type, version과 snapshot hash를 검증한다. 새 child D2가 활성화돼 bound D1이 자동 비활성화된 경우에는 `is_active` 또는 app의 현재 pointer가 D1과 같은지 다시 요구하지 않고 D1을 계속 사용한다. 다만 app의 `active_deployment_id`가 null이거나 같은 app의 실제 active deployment row로 해석되지 않으면 운영상 전체 비활성/불일치 kill switch로 보고 bound child도 provider 전에 차단한다. Pointer가 가리키는 현재 deployment를 child graph로 대체하지는 않는다. Bound row가 삭제됐거나 provenance/type/hash가 다른 경우도 fail-closed한다.

Binding metadata V1은 graph top-level `_nodease_runtime.workflow_node_bindings`에 `{version: "workflow-node-bindings.v1", entries: [...]}`로 저장한다. 각 entry는 `container_path`, `workflow_node_id`, `target_app_id`, `deployment_id`, `deployment_version`, `snapshot_sha256`만 가진다. `container_path`는 local graph에서 바깥쪽부터 안쪽까지의 Loop container를 `{kind: "loop", node_id}`로 나열하며 iteration index는 target 선택에 영향을 주지 않으므로 넣지 않는다. Entries는 canonical `container_path + workflow_node_id` 순서로 정렬하고 중복 path/node를 거부한다. `snapshot_sha256`은 target deployment의 전체 `graph_snapshot`을 `json.dumps(sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")`와 동등하게 직렬화한 bytes의 SHA-256 lowercase hex 64자다. Target snapshot 자체의 V1 binding metadata도 hash에 포함한다. Resolver는 existing WorkflowNode same-organization, allowed deployment type, cycle와 maximum nesting depth 정책을 재사용하고 target closure를 안쪽부터 검증한다. Immutable child deployment snapshot을 parent 생성 중 수정하지 않는다. Child의 transitive closure에 external effect가 있는데 child snapshot에 유효한 V1 binding이 없으면 parent 배포/실행을 거부하고 child 재배포를 요구한다. Closure가 external effect를 포함하지 않을 때만 legacy child 호환 경로를 허용한다. Unknown metadata version, extra/malformed field, canonicalization 실패, cycle, depth 초과나 target 불일치는 queue 발행 또는 provider 전에 fail-closed한다.

Schedule은 MBA-187 claim schema를 확장하거나 schedule provider key를 재사용하지 않는다. Admission winner가 고정 namespace와 canonical claim UUID로 UUIDv5 `execution_id`를 계산해 engine command에 넣는다. 같은 claim duplicate delivery는 같은 값을 만들고 다른 occurrence claim은 다른 값을 만든다. 이 값은 `workflow_run_id`, Celery task id와 schedule idempotency key를 external effect identity로 직접 사용하는 것이 아니다. Stream도 Pub/Sub용 `workflow_run_id`와 별도 `execution_id`를 발급한다.

이 계약은 이미 시작된 logical execution 안에서 external effect identity를 유지하기 위한 것이다. 서로 다른 inbound API/public/webhook 요청을 같은 workflow admission으로 합치거나 비-schedule trigger용 공통 admission ledger를 만드는 범위가 아니며, 해당 경계는 ADR-0029의 후속 별도 설계를 따른다.

### Scope boundary

MBA-190은 공통 external-effect contract, durable attempt, safe no-replay, result-unavailable 처리, 같은 execution 재진입 시 지연 상태 복구, test-only fake provider conformance, Workflow Engine 전용 migration/schema readiness와 새 identity/key의 trace/log 비노출을 구현한다. 기존 Generic HTTP mutation, 모든 Slack request, GitHub issue comment 호출을 공통 경계에 연결하고 위 production 지원 수준으로 고정하는 작업도 포함한다. Generic HTTP `GET`과 GitHub `get_pr`는 external effect가 아니므로 기존 조회 경로를 유지한다. 새로운 public endpoint와 Client UI, Slack request schema/mode/target 제한, 별도 recovery scheduler, heartbeat/원격 요청 취소, MBA-187 schedule claim, Celery 전체 retry/backoff/delivery 재설계, Gmail 실제 기능, Slack provider-aware 응답 처리, GitHub의 새 중복 방지 기능과 universal exactly-once는 포함하지 않는다. 기존 실행 실패 전달 형식에 세 최소 external-effect safe code, `external_effect.prepare_failed`와 다른 allowlisted stop code의 `retryable=false`를 node/application error부터 Celery/PubSub/Gateway까지 보존하는 계약은 포함한다.

Linear MBA-190 본문의 필수 요구사항은 이 공통 계약과 현재 production 분류로 충족한다. 댓글의 provider별 세부 동작은 MBA-217/218의 소유 경계를 설명한다. 현재 MBA-217 Mail/Gmail 구현은 ADR-0032의 전용 계약으로 동작하므로 MBA-190 완료를 위해 공통 ledger로 이관하지 않으며, 두 계약의 직접 adapter 정렬은 별도 ADR 갱신과 후속 변경이 필요하다. Production `supported` operation이 없는 동안 test-only fake 검증을 production 지원으로 표현하지 않고 어떤 실제 operation에도 exactly-once를 주장하지 않는다.

## Consequences

장점:

- Celery retry, duplicate delivery, Loop와 subworkflow에서도 logical effect identity를 정확히 구분한다.
- Provider 호출 전 crash와 호출 후 outcome unknown을 durable state로 구분한다.
- 검증되지 않은 provider를 자동 재호출하지 않아 중복 업무 위험을 줄인다.
- 안전한 result reuse가 가능한 operation은 duplicate success에서 기존 node output schema를 유지하고, 불가능한 operation은 provider 재호출 없이 명시적 오류로 닫힌다.
- Provider key, URL secret과 raw payload가 trace/log로 유출되는 경로를 닫는다.
- Domain rule, transaction orchestration, DB와 provider 호출 코드의 책임이 분리된다.

비용:

- 새 migration, attempt lifecycle, claim fencing, 재진입 시 상태 복구, Workflow Engine worker readiness와 adapter별 replay projection이 필요하다. HMAC key rotation 설정은 production `supported` operation 또는 재호출 가능한 과거 supported attempt가 있을 때만 필요하다.
- Provider 공식 계약이 부족하면 자동 복구 대신 non-retryable outcome unknown으로 종료된다.
- Provider replay가 `supported`인 attempt의 재호출 가능 수명 동안 해당 versioned HMAC key를 운영해야 한다.
- 후속 retention/cleanup 계약이 도입되기 전에는 attempt row가 계속 누적된다.
- 기존 HTTP trace의 `host`와 `path`는 유지하지만 query, header/body와 raw request/response 상세는 durable하게 보존하지 않는다.
- Result reuse가 unavailable인 성공 실행의 duplicate delivery는 downstream 실행을 계속하지 못한다.

## Alternatives Considered

- **`workflow_run_id + node_id`를 identity로 사용**: 구현은 단순하지만 일반 Celery retry에서 run id가 바뀔 수 있고 Loop/subworkflow의 정상 반복을 구분하지 못해 거절한다.
- **WorkflowRun/WorkflowNodeRun FK를 필수로 사용**: Log System이 row를 비동기로 생성하므로 provider 호출 전 insert를 막을 수 있어 거절한다.
- **Unique constraint만 두고 lifecycle/claim을 생략**: 최초 Worker가 insert 뒤 종료한 상태와 provider 호출 중인 상태를 구분하지 못하고 stale Worker 갱신을 막을 수 없어 거절한다.
- **Provider raw key 또는 raw response를 저장**: 재생은 단순하지만 secret/raw payload 비노출 원칙과 retention 위험 때문에 거절한다.
- **Slack을 현재 `supported`로 선언**: 공식 문서가 retention과 duplicate response semantics를 충분히 증명하지 않으므로 거절한다.
- **관련 없는 새 provider를 추가해 supported 사례를 확보**: 제품 범위를 넓히고 MBA-190의 공통 runtime 목적을 흐리므로 거절한다.
- **별도 주기적 recovery scheduler 추가**: 현재 Workflow Engine 배포에는 이를 실행하는 scheduler가 없고 Linear 요구사항은 같은 execution 재진입에서 자기 attempt를 안전하게 닫는 방식으로 충족할 수 있으므로 MBA-190 범위에서는 거절한다.

## Affected Documentation

- `docs/architecture.md`
- `docs/data_model.md`
- `docs/features/workflow/requirements.md`
- `docs/features/workflow/api_spec.md`
- `docs/features/workflow/mba-190_external_effect_idempotency_design.md`
- `docs/features/workflow/test_cases.md`
- `docs/glossary.md`
- `local/mba-190/implementation_plan.md`

## Implementation Status

Migration, model, application use case, provider adapter 연결, 재진입 복구, Workflow Engine 전용 readiness와 자동화 테스트가 MBA-190 작업 트리에 구현되었다. 운영 attempt가 존재하는 schema downgrade는 데이터 손실을 막기 위해 거부한다. Migration upgrade/readiness, 동시 claim 승자, lock 대기 중 lease/deadline 만료, operation drift, key version 최초 경쟁, stale generation과 downgrade를 검증하는 disposable PostgreSQL 테스트를 PR PostgreSQL workflow에 연결했다. 작업 트리가 아직 커밋으로 확정되지 않았으므로 `Verified Against`는 추가하지 않는다.
