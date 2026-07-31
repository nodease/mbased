# MBA-190 외부 부수효과 Node 멱등성 설계

Status: Draft

## Scope

이 문서는 [ADR-0035](../../decisions/ADR-0035-external-effect-idempotency-boundary.md)을 Workflow Runtime에 적용하는 상세 설계다. HTTP 요청, message, email, ticket, webhook과 외부 DB mutation처럼 Nodease 밖의 상태를 바꾸는 node 실행을 대상으로 한다.

Schedule occurrence claim과 Workflow Engine admission 중복 방지는 [ADR-0029](../../decisions/ADR-0029-distributed-schedule-dispatch-claim.md)와 MBA-187이 소유한다. MBA-190은 admission 이후 node invocation이 provider effect를 만들기 직전부터 결과를 확정할 때까지의 identity, durable state와 자동 재호출 정책을 소유한다.

1차 구현은 공통 계약, durable attempt, 같은 execution 재진입 시 상태 복구, Workflow Engine Worker 시작 시 스키마·과거 provider contract와 필요한 경우의 HMAC 설정 점검, test-only fake provider 검증을 포함한다. 기존 Generic HTTP, Slack, GitHub issue comment 호출을 공통 경계에 연결한다. 실제 Gmail lifecycle과 draft는 MBA-217 및 ADR-0032의 Mail 전용 ledger가, Slack provider-aware 응답 판정은 MBA-218이 소유한다. MBA-190은 Gmail row를 공통 ledger로 이관하거나 이중 기록하지 않고 production provider를 `supported`로 올리지 않는다.

## Design Principles

- External effect idempotency는 schedule 또는 graph scheduler가 아니라 Workflow node runtime의 책임이다.
- 같은 logical effect retry는 같은 identity를 사용하고 정상 Loop/subworkflow 반복은 다른 identity를 사용한다.
- Provider 호출 전에 durable attempt와 단일 Worker claim을 확보한다.
- Provider 호출을 시작한 뒤 결과가 불명확하면 외부 서비스의 중복 방지 지원이 `supported`인 operation만 최초 key로 다시 호출할 수 있다.
- 외부 서비스의 중복 방지 지원 여부와 안전한 결과 재사용 가능 여부는 서로 독립적으로 선언한다.
- Provider-visible key, 새 internal identity, URL의 query/user info, raw request/response와 provider exception 원문은 durable trace와 log에 남기지 않는다. Generic HTTP의 기존 목적지 `host`와 `path` 기록은 유지한다.
- Node, DB와 provider 호출 코드는 책임별 package로 분리한다.

## Runtime Boundary

현재 Workflow Engine은 `NodeFactory.create(schema, context=execution_context)`로 node instance를 만들고 gevent로 여러 node를 실행할 수 있다. Node instance가 공용 mutable execution context를 공유하므로 현재 invocation identity를 이 dict에 덮어쓰면 병렬 node 사이에서 값이 섞일 수 있다.

현재 `slackPostNode`는 `HttpRequestNode` class와 `HttpRequestNodeData`를 재사용하며 Client는 기본 `chat.postMessage` 외의 Slack API endpoint와 incoming webhook URL도 설정할 수 있다. MBA-190은 이 요청 동작을 유지하고 새 Slack data schema나 mode/method/target 제한을 추가하지 않는다. Profile resolver는 server가 검증한 `NodeSchema.type`으로 `slackPostNode`와 `httpRequestNode`를 구분한다. `slackPostNode`는 logical side-effect node이므로 method, endpoint와 `slackMode`에 무관하게 단일 `slack.http.request` profile을 사용한다. URL, mode, method, header나 node data가 provider replay capability를 `supported`로 올릴 수 없다. `githubNode`는 `comment_pr`만 external effect로 분류하며 `get_pr`는 이 ledger를 만들지 않는다.

MBA-190은 다음 경계를 추가한다.

1. Draft test, legacy deployed, deployment ID 기반 API/public/webhook, schedule, stream과 subworkflow의 최초 발행자 또는 직접 실행 조정자가 logical execution마다 `execution_id`를 한 번 발급한다. Compare A/B는 variant마다 별도 logical execution ID를 발급한다.
2. Node submit 경계가 현재 invocation path로 `node_invocation_id`를 만든다.
3. Canonical DB resource에서 organization과 현재 node의 app/workflow provenance를 확인하고 node invocation마다 이를 포함한 immutable `ExternalEffectContext`를 별도 typed control parameter로 생성한다. Workflow input, template/Jinja variable map, LLM prompt/tool input이나 node output에는 합치지 않는다.
4. 외부 effect application use case가 stable effect slot을 먼저 조회한다. 기존 row가 있으면 frozen provider contract profile을, 없으면 server-derived provider/operation의 active profile을 선택한다.
5. Provider adapter가 선택된 profile로 시스템 provider key를 넣기 전의 `PreparedEffectRequest`를 한 번 만들고 effect-semantic request를 canonicalize해 SHA-256 `effect_input_digest`를 계산한다.
6. Application use case가 새 DB session/transaction으로 durable attempt를 claim하고 provider/operation/contract/capability/digest와 supported candidate key metadata를 고정한 뒤 session을 닫는다. 동시 insert 충돌은 winner row를 다시 읽고 frozen version까지 비교하며 loser candidate key는 폐기한다.
7. 허용된 Worker가 winner row의 key version으로 key를 재생성하고 fingerprint/길이를 검증한 뒤 `finalize_provider_call`로 immutable `PreparedProviderCall`을 만든다. 이 단계가 끝난 경우에만 또 다른 짧은 DB session/transaction으로 `in_flight` 전이를 commit하고 session을 닫은 뒤 provider를 호출한다.
8. Provider 호출이 끝나면 새 DB session/transaction으로 terminal state를 확정한다.
9. Generic Celery retry도 provider 호출 직전에 같은 application use case를 다시 거친다.
10. 같은 logical execution의 재진입은 자기 attempt의 만료된 `in_flight`를 결과 불명 상태로 먼저 정리한다. 이 정리 단계는 provider를 호출하거나 workflow를 새로 시작하지 않는다.

Node는 schedule claim, Celery retry 횟수, SQLAlchemy query와 provider별 중복 제거 정책을 직접 판단하지 않는다. Immutable node context에는 DB session을 넣지 않고 병렬 node도 같은 SQLAlchemy session을 공유하지 않는다.

Provider port는 `prepare_effect`, `finalize_provider_call`, `invoke_effect`로 나눈다. Application은 stable slot 조회 결과로 기존 row의 frozen profile 또는 새 row의 active profile을 고른 뒤 prepare를 호출한다. Prepare는 network를 열지 않고 시스템 key를 넣기 전의 최종 method/target/header/body/credential을 메모리에서 구성해 provider key 제외 digest와 `PreparedEffectRequest`를 반환한다. JSON validation/serialization 같은 확정 가능한 실패도 같은 semantic input의 digest와 typed `failed_before_effect`로 반환한다. Application은 prepare 결과와 supported candidate key metadata로 attempt를 claim하고 준비 실패면 `prepared`에서 곧바로 terminal 처리한다. 실제 호출이 가능한 claim owner만 row에 고정된 key를 재생성·검증하고 finalize가 시스템 key를 예약 위치에 한 번 넣어 최종 `PreparedProviderCall`을 만든다. Finalize/fingerprint 실패는 `in_flight` 전에 stop한다. 실제 호출이 가능한 경우에만 `in_flight`를 commit한 뒤 invoke한다. 두 prepared request와 raw key는 use case 수명 안의 메모리에만 두고 DB, trace와 log로 넘기지 않는다. Invoke는 최종 call을 그대로 전송하고 graph/input/template/header/body/key를 다시 만들지 않는다. Active profile이나 key가 바뀐 뒤의 retry와 terminal duplicate delivery도 frozen profile/key metadata를 사용하며 현재 값으로 대체하지 않는다.

## Identity Contract

### Execution Identity

`execution_id`는 logical workflow 실행의 stable server-issued identity다. Queue 발행 전 또는 direct execution의 첫 node 실행 전에 한 번 생성한다. Celery retry와 duplicate delivery는 새 값을 만들지 않고 원래 값을 전달한다. Retry 또는 duplicate delivery payload에 이 값이 없으면 새 값을 보충하지 않고 provider 호출 전에 실패시킨다.

Draft test, legacy deployed, deployment ID 기반 API/public/webhook, schedule과 stream 실행은 같은 생성·검증 helper를 사용한다. Queue/client가 보낸 organization/app/workflow 값은 source of truth가 아니며 각 진입점이 canonical DB resource로 다시 확인한다. Compare A/B variant는 서로 다른 logical execution이므로 각각 별도 `execution_id`를 발급하고 각 variant retry에서만 최초 값을 유지한다. Subworkflow는 부모 `execution_id`를 유지하고 별도 `node_invocation_id`로 호출 위치를 구분하되 target deployment의 organization/app/workflow provenance로 전환한다. Identity나 provenance가 누락되면 owner로 fallback하지 않고 provider 호출 전에 실패한다.

이 helper는 이미 시작된 logical execution의 identity를 유지할 뿐 서로 다른 inbound 요청의 workflow admission을 하나로 합치지 않는다. 비-schedule trigger의 공통 admission 중복 방지는 MBA-190 범위가 아니다.

`workflow_run_id`는 Log System correlation이다. 일반 retry에서 새 run row가 생길 수 있으므로 external effect identity의 일부로 사용하지 않는다.

| 실행 표면 | `execution_id` 발급 위치와 유지 규칙 |
| --- | --- |
| Draft/test `workflow.execute` | Gateway publisher가 enqueue마다 새 UUID를 command에 넣고 Celery retry는 원본 argument를 유지 |
| Cost optimizer/Compare A/B | Candidate/variant마다 별도 UUID를 발급. 같은 compare request correlation은 기존 request context만 사용 |
| Legacy deployed `workflow.execute_deployed` | 기존 positional 인자는 유지하고 최초 broker message의 optional envelope로 publisher-issued UUID, exact deployment ID/version과 canonical snapshot SHA-256을 모두 받음. Worker가 active deployment를 골라 retry에만 고정하거나 같은 hash의 다른 row를 대체하지 않으며 envelope 누락 effect는 provider 전에 차단 |
| Authenticated deployment와 URL slug API/public `workflow.execute` | Gateway가 UUID와 canonical deployment ID/version/snapshot을 command에 넣고 Worker가 DB deployment/App/Workflow provenance와 snapshot 일치를 재검증 |
| Webhook `workflow.execute_by_deployment` | Gateway publisher가 UUID와 deployment ID를 넣고 Worker가 canonical deployment snapshot과 App/Workflow provenance를 재구성 |
| Schedule `workflow.execute_scheduled_deployment` | Admission winner가 고정 namespace와 canonical claim UUID로 UUIDv5를 계산. MBA-187 table 변경과 schedule idempotency key 재사용 없음 |
| Stream `workflow.stream` | Gateway가 Pub/Sub용 `workflow_run_id`와 별도 UUID를 발급 |
| Subworkflow | 부모 `execution_id`를 유지하고 target resource provenance와 caller invocation path만 변경 |

현재 저장소에는 `workflow.execute_deployed` 발행자가 없다. 외부 effect가 없는 legacy task invocation은 기존 호환 동작을 유지할 수 있지만 external effect boundary에 도달했을 때 `execution_id`, immutable deployment binding 또는 canonical resource provenance가 없으면 그 자리에서 fail-closed한다. 저장소 밖 publisher 변경이나 새 admission ledger는 이 구현 범위에 포함하지 않는다.

Parent retry의 `WorkflowNode`가 target app의 새 active deployment를 선택하지 않도록 server-owned child binding을 사용한다. 배포 생성은 graph와 Loop subgraph의 각 WorkflowNode에 target deployment ID/version/snapshot hash를 계산해 immutable snapshot 내부 metadata에 저장한다. Draft/test, Compare와 stream publisher는 각 surface가 기존 계약으로 선택한 검증된 실행 graph의 server-owned 복사본에 같은 metadata를 계산해 최초 queue command에 포함한다. Stream의 유효한 request `graph_snapshot`처럼 저장되지 않은 graph 실행을 이미 허용하는 경로는 그대로 유지하며 binding 계산을 이유로 DB draft로 대체하거나 저장하지 않는다. Client 입력, deployment clone, template/import와 다른 graph 복사 경로에 남은 metadata는 제거하고 현재 canonical target으로 재계산하며 모든 client-facing draft/deployment/copy/export graph 응답에서 숨긴다. Child Runtime은 binding을 DB와 재검증하고 child deployment snapshot에 저장된 다음 단계 binding을 이어 쓴다. 과거 snapshot처럼 binding이 없으면 canonical node catalog의 `side_effect=external_write`를 기본으로 한 shared pure classifier가 선택한 child graph의 transitive closure에 effect-capable node가 없다고 확인한 경우만 호환 실행한다. `httpRequestNode GET`과 `githubNode get_pr`만 명시적 read-only로 낮추고 malformed/unknown action, Gmail Draft/Acknowledge와 미래 `external_write` node는 effect-capable로 본다. Gmail을 binding 안전 판정에 포함해도 실제 effect는 ADR-0032 ledger만 사용한다.

Resolver는 binding 생성 시 current active target과 child surface type을 확인한다. Runtime verifier는 새 D2 활성화로 bound D1이 자동 비활성화됐더라도 exact ID/version/hash와 canonical app/workflow/organization/type이 맞으면 D1을 사용한다. 현재 pointer와 D1의 일치를 요구하거나 D2로 대체하지 않는다. 다만 app active pointer가 null이거나 같은 app의 실제 active row를 가리키지 않는 전체 비활성/불일치 상태는 kill switch로 차단한다. 삭제되거나 provenance/type/hash가 달라진 row도 차단한다.

Metadata V1은 `_nodease_runtime.workflow_node_bindings = {version: "workflow-node-bindings.v1", entries: [...]}`다. Entry는 local Loop container path, WorkflowNode ID, target app/deployment ID, deployment version과 canonical full-snapshot SHA-256만 가진다. Loop path는 iteration과 무관한 `{kind:"loop", node_id}` 순서이며 entries는 path/node로 정렬하고 중복을 거부한다. Snapshot hash는 target graph의 자체 binding metadata까지 포함한 sorted-key, compact, UTF-8, `ensure_ascii=False`, `allow_nan=False` canonical JSON bytes를 사용한다. Pure schema/hash/Loop-aware traversal, effect-capable classifier와 공통 depth constant는 `apps/shared/domain/workflow_node_binding.py`가 소유한다. 이 domain module은 catalog file/service를 import하지 않고 Gateway와 Workflow Engine composition이 `apps/shared/services/workflow_node_catalog.py`로 읽은 immutable `node_type -> side_effect` mapping을 주입받는다. Gateway binding use case는 `apps/gateway/application/deployment/workflow_node_binding.py`에 두고 기존 `preflight.py`와 같은 `DeploymentPreflightRepository` port, walker와 organization/type/cycle/depth 정책을 사용한다. `apps/gateway/composition/deployment.py`가 기존 SQLAlchemy adapter를 조립하고 `DeploymentService`는 facade로 호출한다. 새 resolver나 DB query policy를 `apps/gateway/services/`에 만들지 않는다. Worker DB 검증은 WorkflowNode/runtime adapter가 소유한다.

### Node Invocation Identity

`node_invocation_id`는 graph node가 실제로 호출된 한 번의 logical invocation을 나타내는 opaque identity다. Canonical input은 다음을 포함한다.

- `execution_id`
- Root부터 현재 node까지의 invocation path
- Subworkflow caller invocation
- Loop iteration index
- Graph node id
- 같은 path 안에서의 invocation ordinal

V1 framing은 domain tag와 순서가 고정된 field의 name/value UTF-8 bytes 각각에 unsigned 4-byte big-endian length를 붙인다. UUID는 lowercase hyphenated 36자, 0 이상 정수는 leading zero 없는 base-10 ASCII로 표현한다. Outer field 순서는 `domain`, `execution_id`, `segment_count`, 각 segment의 `segment_kind`, `segment_node_id`, `segment_scope`다. 첫 segment는 `root / "" / root workflow UUID`, 마지막은 `node / current graph node ID / submit ordinal`이다. 그 사이는 실제 nesting 순서대로 `loop / loop node ID / iteration index`와 `subworkflow / caller WorkflowNode ID / frozen target deployment UUID`만 허용한다. DAG predecessor나 완료 순서는 path에 넣지 않는다. Framing 전체의 padding 없는 Base64url을 name으로 사용해 `uuid5(uuid.NAMESPACE_URL, "nodease:node-invocation:v1:<name>")`를 계산한다. Schedule은 `uuid5(uuid.NAMESPACE_URL, "nodease:schedule-execution:v1:<lowercase-claim-uuid>")`를 사용한다. Invocation ordinal은 node 완료 시점이나 WorkflowEngine 전역 `_node_sequence`가 아니라 `(parent invocation path, graph node id)`별 submit count다. 현재 정적 DAG의 첫 제출은 항상 0이고 다른 node의 추가·완료 순서가 이 값을 바꾸지 않는다. Python process-local hash, dict iteration order, Worker id와 wall clock은 입력으로 사용하지 않는다.

같은 logical invocation의 retry는 같은 값을 만들고 다음 Loop iteration, 다른 subworkflow 호출 또는 같은 parent path에서 같은 node의 별도 submit은 다른 값을 만든다. Loop가 subgraph engine을 재사용해도 iteration별 immutable invocation scope를 전달하고 parent-path별 submit count를 섞지 않는다. V1 canonical encoding과 namespace는 고정 test vector로 검증하며 새 version과 호환 경로 없이 변경하지 않는다.

### External Effect Identity

External effect full logical identity는 organization scope 안에서 다음 네 값으로 구성한다.

- `execution_id`
- `node_invocation_id`
- `operation`
- `effect_sequence`

`operation`은 contract registry가 정한 canonical operation name이다. `effect_sequence`는 operation별 순번이 아니라 한 node invocation 안에서 provider에 보내려 한 모든 외부 작업의 0-based 순번이다. 현재 Generic HTTP mutation, 모든 Slack request, GitHub comment와 fake node는 invocation당 한 번만 호출하므로 `0`을 사용한다.

DB unique constraint는 operation 변경으로 새 row를 만들 수 없게 `organization_id + execution_id + node_invocation_id + effect_sequence` stable effect slot을 묶는다. `operation`, `app_id`와 `workflow_id`는 최초 attempt에 frozen 값으로 저장하고 재진입에서 같아야 한다. Repository는 slot로 먼저 조회하고 동시 insert unique 충돌도 winner row를 다시 읽는다. Graph `node_id`, `workflow_run_id`, `node_run_id`는 조회와 correlation에만 사용한다.

### Effect Input Stability

같은 stable effect slot은 같은 provider 요청 의미만 가리켜야 한다. Adapter는 runtime variable과 node 설정을 해석한 뒤, 시스템이 생성할 provider-visible idempotency key만 제외하고 실제 effect 의미에 영향을 주는 provider, target, method, header/body, 인증 값과 operation input을 contract version별 canonical form으로 만든다. Header 이름/순서와 JSON object key처럼 같은 전송 의미를 가진 표현은 contract가 정한 방식으로 정규화한다. Validation 전이라 최종 request를 만들 수 없는 입력도 stage tag와 렌더링된 값으로 결정적인 canonical bytes를 만든다. Credential과 raw canonical bytes는 메모리에서 digest 입력으로만 사용하고 저장하지 않으며 SHA-256 `effect_input_digest`만 attempt에 저장한다.

최초 insert가 provider, operation, provider contract version, 두 지원 수준과 digest를 고정한다. Slot unique 충돌, retry 또는 duplicate delivery에서 operation을 포함한 현재 값이 하나라도 다르면 별도 row를 만들거나 저장 row를 덮어쓰지 않고 provider/result/downstream을 사용하지 않은 채 `external_effect.identity_conflict`, `retryable=false`로 종료한다. Digest와 canonical input은 응답, log, trace와 metric label에 노출하지 않는다.

## Attempt Lifecycle

`workflow_node_effect_attempts`는 provider replay 판단의 source of truth다.

### Status

- `prepared`: attempt와 Worker claim은 만들었지만 provider 호출은 시작하지 않았다.
- `in_flight`: provider 호출 직전의 durable start boundary를 넘었다. 실제 network write 전 crash도 중복 방지를 위해 호출 여부가 불명확한 것으로 처리한다.
- `terminal`: provider outcome과 replay decision을 확정했다.

`outcome`은 terminal에서만 다음 값 중 하나를 가진다.

- `succeeded`: 해당 operation의 기존 node 계약이 완전한 응답을 받아 node output을 확정했거나 같은 provider key replay에 대해 안전한 duplicate success를 반환했다. Generic HTTP는 status code와 관계없이 기존 완전 응답 계약을 유지한다.
- `failed_before_effect`: validation/serialization 같은 local·pre-send 실패이거나, provider의 완전한 rejection 응답으로 effect가 생기지 않았음을 확정한 실패다. Provider invoke가 시작됐는지와 effect 부재가 확정됐는지는 별개다.
- `effect_outcome_unknown`: provider 호출 시작 후 timeout, disconnect, provider accept 뒤 response loss 또는 response parse failure처럼 effect 생성 여부를 확정할 수 없다.

`provider_started_at`은 effect 생성 여부, request byte 전송 여부나 Python 함수의 실제 진입을 기록하지 않는다. 최종 `PreparedProviderCall` 검증 뒤 `invoke_effect` 호출을 허용하는 `in_flight` 경계를 commit한 시각이다. 따라서 commit 직후 실제 함수 호출 전에 Worker가 종료돼도 non-null이다. `prepared`에서는 null이고 `in_flight`와 `terminal + succeeded|effect_outcome_unknown`에서는 non-null이다. `failed_before_effect`는 network-free prepare/finalize 단계에서 닫혔으면 null이고, `in_flight` commit 뒤 byte 미전송이 증명된 transport 실패 또는 provider rejection으로 닫혔으면 non-null이다. `provider_status_code`와 allowlisted `error_code`는 terminal에서만 허용한다. DB CHECK와 domain validation이 이 조합을 함께 강제하고 reopen은 세 provider 결과 field를 모두 비운다.

### Worker Claim

Attempt는 `claim_owner`, `claim_expires_at`, `claim_generation`을 사용한다. `claim_owner`는 claim/reopen마다 CSPRNG UUID로 새로 만드는 delivery-local token이며 task ID, worker hostname, execution identity와 retry count를 재사용하지 않는다. 같은 Celery task ID의 retry/redelivery도 다른 token을 사용한다. Claim을 새로 획득할 때 generation을 증가시킨다. Provider 실행 권한을 가진 delivery의 prepared/in-flight 전이는 claim 응답의 owner, generation과 유효 expiry를 비교한다. 만료 복구는 예상 status/generation과 만료된 expiry를, terminal reopen·budget 소진 전이는 terminal status/generation/decision과 active claim null을 비교한다. Token은 attempt column 밖으로 내보내지 않는다. 유효한 같은 generation에서는 한 Worker만 provider 호출 권한을 얻고 만료된 이전 Worker는 provider 결과를 뒤늦게 확정할 수 없다. Fencing은 이미 시작된 network I/O를 취소하지 못하므로 claim 만료 뒤 이전 요청과 `supported` same-key replay가 잠시 겹칠 수 있다. 이 경우 보장하는 것은 동시 요청 금지가 아니라 provider 계약에 따른 duplicate effect 방지이며 MBA-190은 heartbeat나 원격 요청 강제 취소를 추가하지 않는다.

`prepared` claim, `in_flight` 전이와 `terminal` update는 각각 session factory에서 새 session을 받아 독립된 짧은 transaction으로 commit하고 닫은 뒤 다음 단계로 이동한다. Claim 뒤 row의 frozen key를 재생성하고 `PreparedProviderCall`을 확정하는 network-free 단계에는 DB session을 유지하지 않는다. 이 단계가 실패하면 claim CAS로 `prepared -> terminal` 처리하고 `in_flight`로 넘어가지 않는다. Network I/O 동안 DB session, transaction이나 row lock을 유지하지 않는다. `in_flight` commit 실패 시 provider를 호출하지 않는다. Provider 결과를 받은 뒤 terminal update의 owner/generation compare-and-set 또는 commit이 실패하면 현재 Worker는 node output을 반환하거나 downstream node를 실행하지 않는다. Terminal commit이 성공한 뒤에만 현재 output 또는 저장된 replay result를 Workflow Engine에 넘긴다. `in_flight` commit 성공 뒤 실제 provider 함수 호출 또는 terminal 저장 전에 Worker가 종료되면 같은 logical execution의 다음 재진입이 보수적으로 outcome unknown을 선택한다.

Claim TTL은 active Workflow Engine Celery hard time limit에 code-owned terminal commit 여유 30초를 더해 composition에서 계산한다. 현재 hard limit 600초에서는 630초이고 새 환경변수는 추가하지 않는다. Hard limit이 없거나 양수가 아니면 readiness가 Worker 시작을 거부한다. DB의 claim 획득/만료 비교는 DB clock을 사용하고, claim loser는 session을 닫은 채 100ms부터 최대 1초까지 늘어나는 간격으로 조회하되 task 시작 때 만든 monotonic deadline을 넘지 않는다. Gevent pool은 soft time limit을 구현하지 않고 blocking task에서 hard limit도 강제하지 않을 수 있으므로 600초를 이전 process/network 종료 증거로 사용하지 않는다. 이 값은 bounded policy input이고 만료 후 중첩 안전성은 supported same-key provider 계약과 unsupported/unknown no-replay가 담당한다. 공식 근거: <https://docs.celeryq.dev/en/stable/userguide/workers.html#time-limits>

- 만료된 `prepared`: 같은 logical execution의 다음 재진입이 새 generation을 claim하고 provider 호출을 시작할 수 있다.
- 만료된 `in_flight`: 같은 logical execution의 다음 재진입이 compare-and-set 조건으로 `effect_outcome_unknown`에 수렴시킨다.
- `terminal`: active claim을 갖지 않는다.

유효한 claim을 다른 Worker가 소유하면 중복 Worker는 provider를 호출하거나 즉시 성공/실패를 반환하지 않는다. DB session/transaction/lock을 유지하지 않는 짧은 조회로 terminal, claim 만료 또는 기존 Celery task deadline 중 먼저 오는 경계까지 제한적으로 기다린다. Terminal이면 frozen provider/contract/digest 일치 확인 뒤 저장된 `reuse_result`, `result_unavailable` 또는 `stop`을 따른다. 만료된 `in_flight`는 결과 불명 상태 정리만 수행하고 같은 진입에서 reopen하거나 provider를 호출하지 않는다. 만료된 `prepared`는 row를 바꾸거나 claim하지 않고 대기를 끝내며 다음 동일 execution delivery만 새 generation을 claim한다. Task deadline이 먼저 오면 row를 변경하지 않고 provider를 호출하지 않은 채 기존 timeout/retry 경계로 종료한다.

DB CHECK는 status별 nullability, 허용된 enum 조합과 generation 범위처럼 시간이 지나도 바뀌지 않는 구조만 강제한다. Claim과 replay deadline의 실제 만료 여부는 claim/reentry/reopen/terminal update가 DB clock으로 판단하며 CHECK에서 현재 시각과 비교하지 않는다.

MBA-190은 별도 주기적 recovery task나 scheduler를 추가하지 않는다. 같은 logical execution task가 재진입할 때 identity unique key로 자기 attempt를 찾고, 만료된 `in_flight`를 같은 status/generation/expiry 조건으로 `effect_outcome_unknown` terminal 상태로 바꾼다. 같은 update에서 frozen provider replay capability와 저장된 `replay_deadline_at`을 적용해 유효한 `supported`이면 `replay_same_key`, 그 외에는 `stop`을 기록한다. 이 정리 단계는 provider를 호출하거나 workflow를 새로 시작하지 않는다. 이후 같은 task가 유효한 `replay_same_key`를 다시 열 때만 최초 key로 호출을 계속한다. 재진입하지 않은 만료 row의 전역 정리는 후속 운영 작업으로 남긴다.

Terminal replay decision은 `reuse_result`, `result_unavailable`, `retry_before_effect`, `replay_same_key`, `stop` 중 하나다. `retry_before_effect` 또는 `replay_same_key`만 같은 row를 새 claim generation의 `prepared`로 원자적으로 다시 열 수 있다. Reopen은 다음 규칙을 따른다.

Claim loser나 다음 delivery가 `retry_before_effect|replay_same_key`를 읽으면 같은 application 진입에서 바로 provider를 호출하지 않고 기존 Celery retry 경계에 internal permit을 반환한다. Existing retry budget/backoff가 허용한 다음 delivery만 reopen한다. Budget이 거부/소진되면 repository가 terminal outcome, timestamp와 기존 allowlisted safe error code를 보존한 채 decision만 `stop`으로 CAS하고 generation/provider 호출 없이 이후 redelivery reopen을 막는다. 이는 retry 횟수나 backoff를 새로 정하는 동작이 아니다.

- Identity, 두 지원 수준, provider contract version, `replay_deadline_at`, key version/format/fingerprint를 그대로 보존한다.
- Outcome, replay decision, provider-start/terminal timestamp, 이전 safe result/error summary를 비운다.
- 이전 terminal status, replay decision, claim generation과 active claim null을 compare-and-set 조건에 포함하고 새 CSPRNG owner/expiry와 증가한 generation을 같은 update에 기록한다. 이전 terminal row의 claim expiry 유효성을 요구하지 않는다.
- `reuse_result`, `result_unavailable`와 `stop` row는 다시 열지 않는다.

## Provider Replay And Result Reuse

각 provider operation은 versioned contract와 함께 서로 독립적인 두 지원 수준을 선언한다.

Versioned contract profile은 아래 값을 하나의 정적 계약으로 묶는다.

| 값 | 규칙 |
| --- | --- |
| Provider/operation/version | `provider`, canonical `operation`, 변경 불가능한 `contract_version` |
| 지원 수준 | `provider_replay_capability`, `result_reuse_capability` |
| Key 전달 | `header`, `body`, `none`, `unknown` 중 하나와 header field 이름 또는 canonical body JSON Pointer |
| Key 제약 | alphabet/format, 최대 길이, provider retention |
| Response 의미 | effect success/rejection 판정, duplicate success/conflict와 기존 응답 재사용 의미 |
| Input canonicalization | Provider key를 제외한 effect-semantic request와 SHA-256 digest 계산 규칙 |
| 실행 의미 식별자 | 요청 정규화, 응답 해석, 결과 재사용 projection을 버전별 handler에 연결하는 고정 식별자 |
| Evidence | 공식 문서의 정적 reference. Runtime trace/log에는 기록하지 않음 |

공식 문서에서 확인할 수 없는 값은 `unknown`으로 둔다. Code default나 사용자 입력으로 빈 정보를 채우지 않는다. Generic HTTP node에서 사용자가 임의의 idempotency header를 설정해도 대상 provider의 공식 계약과 전용 integration test가 없으면 `supported`로 승격하지 않는다. Provider contract registry는 operation마다 현재 실행에 사용할 active version을 정확히 하나 지정하고 version을 추가만 하며, adapter composition에는 active version과 저장 row가 참조할 수 있는 모든 과거 version을 함께 전달한다. 한 번 사용한 과거 `contract_version`의 의미를 바꾸거나 제거하지 않는다. Adapter는 registry의 모든 profile이 가리키는 요청 정규화·응답 해석·결과 재사용 handler를 지원하는지 시작 단계에서 확인하고, 준비된 최종 호출에도 선택된 profile을 넣어 active version이 바뀐 뒤의 retry가 당시 처리 규칙을 그대로 사용하게 한다. 알 수 없는 handler 식별자는 현재 규칙으로 추정하지 않고 시작 단계에서 차단한다. Attempt 생성 때 contract retention으로 `replay_deadline_at`을 고정하고 이후 배포의 현재 retention으로 다시 계산하지 않는다.

외부 서비스의 중복 방지 지원 여부(`provider_replay_capability`):

- `supported`: Key 전달 위치가 `header|body`이고 공식 idempotency field, key format/length/retention, duplicate response semantics와 adapter test가 모두 준비됐다. `none|unknown` transport는 MBA-190에서 `supported`가 될 수 없다.
- `unsupported`: provider가 중복 방지 계약을 제공하지 않거나 안전하게 적용할 수 없다고 확인됐다.
- `unknown`: 공식 계약이 없거나 충분히 검증하지 못했다.

`unknown`은 자동 재호출 정책에서 `unsupported`와 동일하게 동작한다.

안전한 결과 재사용 가능 여부(`result_reuse_capability`):

- `supported`: allowlist를 적용한 `replay_result`로 최초 성공과 중복 전달에 같은 node output schema를 제공할 수 있다. 전역 상한은 `json.dumps(sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")`와 동등한 canonical JSON 65,536 bytes이며 provider profile은 더 작은 상한을 둘 수 있다. JSON 불가 값과 NaN/Infinity는 projection 실패다.
- `unavailable`: raw response/header/body를 저장하지 않고는 같은 결과를 재구성할 수 없다. 성공 사실만 저장하고 중복 전달은 `external_effect.result_unavailable` 오류로 종료한다.

외부 서비스의 중복 방지가 `supported`여도 결과 재사용은 `unavailable`일 수 있다. 반대로 결과를 재사용할 수 있어도 결과 불명 상태의 외부 서비스 재호출은 중복 방지 지원이 별도로 `supported`여야 한다.

Replay matrix:

| Outcome | 외부 서비스 중복 방지 | 결과 재사용 | 자동 동작 |
| --- | --- | --- | --- |
| `failed_before_effect` | 모든 값 | 모든 값 | Adapter가 요청 전송 전의 일시 실패임을 증명하면 `retry_before_effect`, validation/configuration과 완전한 영구 rejection은 `stop` |
| `succeeded` | 모든 값 | `supported`, canonical JSON 65,536 bytes 이하 projection 있음 | `reuse_result`. Provider를 다시 호출하지 않고 `replay_result` 반환 |
| `succeeded` | 모든 값 | `supported`, projection 부재/초과 | `result_unavailable`. 값을 자르거나 raw payload를 저장하지 않는 방어적 fallback |
| `succeeded` | 모든 값 | `unavailable` | `result_unavailable`. 최초 실행은 현재의 검증된 node output으로 계속하고, 이후 duplicate delivery는 provider를 다시 호출하지 않고 non-retryable `external_effect.result_unavailable` 반환 |
| `effect_outcome_unknown` | `supported` | 모든 값 | `replay_same_key`. 최초 provider key/contract version을 사용하고 frozen retention window 안에서만 replay 허용 |
| `effect_outcome_unknown` | `unsupported`, `unknown` | 모든 값 | `stop`. Non-retryable로 종료하고 provider 자동 재호출 금지 |

필요한 이전 HMAC key 또는 provider contract version을 복원할 수 없으면 새 key나 새 contract로 대체하지 않고 fail-closed한다. Workflow Engine Worker 시작 점검은 terminal을 포함한 모든 attempt가 참조하는 과거 contract version을 모두 해석할 수 있는지 확인하고, provider 재호출 가능성이 남은 supported operation/attempt에 필요한 HMAC key만 검사한다. Reopen은 DB clock이 저장된 `replay_deadline_at`보다 이른 경우에만 허용한다. 경계 시각부터는 같은 transaction에서 decision을 `stop`으로 바꾸고 provider를 호출하지 않는다.

## Replay Result

결과 재사용을 `supported`로 선언한 operation만 adapter별 allowlist를 적용한 `replay_result`를 저장한다. Allowlist 뒤 위 canonical JSON 직렬화의 전체 byte 길이는 65,536 bytes와 provider profile의 더 작은 상한을 모두 통과해야 한다. JSON 불가 값과 NaN/Infinity는 실패하고 값을 자르거나 문자열로 임의 변환하지 않는다. 최초 성공과 duplicate delivery는 저장 JSON으로 같은 node output schema를 반환한다. 외부 서비스의 중복 방지를 `supported`로 선언한 operation은 provider가 명시한 duplicate-success response를 같은 projection으로 변환할 수 있어야 한다. 다른 canonical request에 대한 conflict는 성공 projection으로 바꾸지 않고 provider contract가 effect 부재를 확정한 경우에만 `failed_before_effect + stop`의 allowlisted safe error로 처리한다.

규칙:

- 최초 node output과 duplicate replay output은 같은 schema를 사용한다.
- 다음 node가 사용하는 최소 필드만 허용한다.
- Raw provider response, response header/body와 credential은 저장하지 않는다.
- `replayed=true` 같은 새 사용자-facing field를 임의로 추가하지 않는다. Replay 여부는 internal trace summary에만 기록한다.
- Safe schema-compatible projection을 영속 저장용으로 정의할 수 없는 operation은 결과 재사용을 `unavailable`로 선언한다. 최초 성공은 현재 응답에서 만든 검증된 node output으로 downstream 실행을 계속할 수 있지만, 중복 전달에서는 provider를 다시 호출하거나 downstream 실행을 계속하지 않고 `external_effect.result_unavailable`로 닫는다.
- Projection이 없거나 한도를 넘으면 자르거나 raw payload를 저장하지 않는다. 최초 실행은 현재의 검증된 output으로 계속하고 해당 attempt를 `result_unavailable`로 닫는다. 이는 contract 위반을 안전하게 축소하는 fallback이며 profile integration test는 정상 projection이 한도 안에 있음을 검증한다.

## Provider-visible Key

Provider-visible key는 provider replay가 `supported`이고 contract의 전달 위치가 `header` 또는 `body`인 operation에서만 만든다. `unsupported|unknown` 또는 전달 위치 `none|unknown`에서는 시스템 key를 생성·주입하거나 사용자가 설정한 header/body를 덮어쓰지 않는다. Key를 만드는 경우에도 internal identity를 그대로 노출하지 않는다.

- 전용 idempotency HMAC secret과 HMAC-SHA256을 사용한다.
- 로그인, session, token 서명용 `SECRET_KEY`를 재사용하지 않는다.
- Canonical input은 format version과 length-delimited `organization_id`, `app_id`, `workflow_id`, `execution_id`, `node_invocation_id`, `operation`, `effect_sequence`를 포함한다.
- Provider별 prefix, alphabet과 길이 제한은 versioned provider contract가 적용한다.
- Attempt는 provider replay가 `supported`일 때만 `key_version`, `key_format_version`, `replay_deadline_at`과 key fingerprint를 저장하고 `unsupported|unknown`에서는 null로 둔다. `provider_contract_version`은 모든 attempt에 저장한다.
- Provider-visible raw key는 DB, response, trace, log와 metric label에 저장하지 않는다.
- 새 attempt는 active key version을 사용하고 retry는 최초 version을 고정한다.
- 최초 insert 경쟁에서 candidate active key version이 달라도 unique winner row의 key version/format/fingerprint가 source of truth다. Loser candidate key는 폐기하고, 실제 claim owner만 winner row의 key를 재생성·검증해 최종 call에 넣는다.
- 과거 key는 이를 참조하는 nonterminal 또는 replay 가능한 attempt가 존재하는 동안 보존한다.
- Production `supported` operation도 재호출 가능한 과거 supported attempt도 없으면 HMAC key ring은 Worker 시작 필수 설정이 아니다. Fake provider key는 test composition에서만 주입한다.

## Provider Scope

1차 production 지원 수준과 canonical profile:

| Node 경로 | `provider / operation / contract_version` | 중복 방지 | 결과 재사용 | MBA-190 동작 |
| --- | --- | --- | --- | --- |
| Generic HTTP `POST|PUT|PATCH|DELETE` | `generic_http / generic_http.request / generic_http.request.v1` | `unknown` | `unavailable` | 공통 경계에 연결. 사용자 지정 header로 지원 수준을 올리지 않음 |
| Generic HTTP `GET` | 적용 안 함 | 적용 안 함 | 적용 안 함 | RFC 9110 safe method이므로 external effect ledger 밖의 기존 조회 경로 유지 |
| `slackPostNode` 모든 현재 허용 method | `slack / slack.http.request / slack.http.request.v1` | `unknown` | `unavailable` | Logical side-effect node의 API endpoint 변경과 incoming webhook을 같은 보수적 profile로 연결. Slack 공식 문서가 완전한 key/retention/duplicate 계약을 제공하지 않으므로 method, endpoint나 mode로 지원을 추정하지 않음. `ok=false`, safe error와 `Retry-After`는 MBA-218 |
| `githubNode` / `comment_pr` | `github / github.issue_comment.create / github.issue_comment.create.v1` | `unsupported` | `unavailable` | 공통 경계에 연결. 최초 `201`은 기존 comment output을 유지하되 저장하지 않음. 성공 후 duplicate는 provider를 다시 호출하지 않고 결과 재사용 불가 오류, outcome unknown은 `stop` |
| `githubNode` / `get_pr` | 적용 안 함 | 적용 안 함 | 적용 안 함 | 읽기 경로이므로 attempt를 만들지 않음 |

Production profile의 나머지 key/result field는 다음 값으로 고정한다.

| Contract | Key 위치 | 시스템 field | 형식/최대 길이 | Retention | Duplicate/conflict 의미 | Result 의미 |
| --- | --- | --- | --- | --- | --- | --- |
| `generic_http.request.v1` | `unknown` | `null` | `unknown` | `unknown` | 목적지마다 달라 확인 불가; 시스템 key 없음 | 최초 완전 응답의 기존 `status/data/headers`만 반환, replay projection 없음 |
| `slack.http.request.v1` | `unknown` | `null` | `unknown` | `unknown` | API/webhook 전체에 적용할 완전한 계약이 없어 확인 불가; 시스템 key 없음 | 최초 완전 HTTP 응답의 기존 `status/data/headers`만 반환, replay projection 없음 |
| `github.issue_comment.create.v1` | `none` | `null` | 적용 안 함 | 적용 안 함 | 적용 가능한 idempotency/duplicate-success 계약 없음 | 검증된 최초 `201`의 기존 comment output만 반환, replay projection 없음 |

Generic HTTP `GET`과 GitHub `get_pr`는 새 attempt를 만들지 않지만 현재 node invocation의 stable slot에 기존 effect row가 있는지 먼저 조회한다. Row가 없으면 기존 read 동작을 유지하고, row가 있으면 이전 mutation/comment를 read로 바꾼 operation downgrade이므로 read provider와 downstream 호출 전에 `external_effect.identity_conflict`로 종료한다. Read 성공 자체는 ledger에 기록하지 않는다. Slack은 method와 무관하게 effect 경계를 통과한다.

참고 대상인 Gmail `users.drafts.create`는 현재 공식 문서상 중복 방지 field가 없어 `unsupported`로 판단하지만 MBA-190에서 production 호출을 연결하지 않는다. 현재 Gmail 계약과 구현은 [ADR-0032](../../decisions/ADR-0032-mail-processing-gmail-draft-idempotency.md)의 `mail_draft_effects`가 소유한다. 공통 계약과 직접 adapter로 정렬하려면 ADR-0032를 갱신하는 후속 변경이 필요하다.

공식 근거:

- HTTP safe method: <https://www.rfc-editor.org/rfc/rfc9110.html#name-safe-methods>
- GitHub issue comment 생성: <https://docs.github.com/en/rest/issues/comments#create-an-issue-comment>
- Slack `chat.postMessage`: <https://docs.slack.dev/reference/methods/chat.postMessage/>
- Slack incoming webhook: <https://docs.slack.dev/messaging/sending-messages-using-incoming-webhooks>
- Gmail `users.drafts.create`: <https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.drafts/create>
- HTTPX exception hierarchy: <https://www.python-httpx.org/exceptions/>
- Locked HTTPX 0.28.1 exception definitions: <https://github.com/encode/httpx/blob/0.28.1/httpx/_exceptions.py>
- Requests exception hierarchy: <https://requests.readthedocs.io/en/latest/api/#exceptions>
- Locked Requests 2.34.2 `ConnectTimeout` safe-retry contract: <https://github.com/psf/requests/blob/v2.34.2/src/requests/exceptions.py>

Generic HTTP와 Slack V1 canonicalization은 template 적용 뒤 실제로 보낼 method, rendered target, 시스템 provider key를 넣기 전의 application header와 body를 versioned length-delimited encoding으로 만든다. 현재 runtime의 header dict merge 뒤 렌더링된 body가 `None` 또는 빈 문자열이면 no-body로 보내고 사용자 `Content-Type`을 보존한다. 빈 문자열이 아닌 body를 parse한 결과가 JSON `null`이면 HTTPX 0.28.1 `json=None`과 동일하게 사용자 `Content-Type`은 제거하지만 자동 `application/json` 없이 zero-byte body를 보낸다. 그 밖의 JSON 값은 사용자 `Content-Type` 변형을 제거하고 고정 `content-type: application/json`을 포함한다. 그 effective header를 `httpx.Headers(...).multi_items()`로 정규화해 이름은 소문자로 묶고 이름순으로 정렬하되 같은 이름의 여러 값 순서는 보존한다. Node가 명시하지 않은 나머지 HTTPX 기본 transport header는 제외한다. 인증 header 값도 요청 주체를 바꾸므로 digest 입력에는 포함하지만 저장하거나 별도 fingerprint로 남기지 않는다. Digest용 JSON semantic form은 object key를 정렬하지만 `PreparedEffectRequest`와 최종 `PreparedProviderCall`은 원래 parsed value의 insertion order를 보존하고 invoke는 HTTPX 0.28.1의 compact/non-ASCII/NaN 금지 UTF-8 wire serialization을 그대로 사용한다. Finalize는 시스템 key만 예약 위치에 추가한다. 공백만 있는 body, NaN/Infinity와 JSON parse 실패는 `invalid_json` stage tag와 렌더링된 body bytes로 deterministic `failed_before_effect + stop`을 만든다. 최종 call은 no-body/JSON-null-no-body/JSON mode, effective header와 parsed JSON 값을 그대로 invoke에 전달한다. 이 wire semantics가 dependency 변경으로 달라지면 새 contract version을 추가한다. GitHub V1은 canonical repository owner/name, PR number, rendered comment body와 인증 값을 포함하며 Requests 자동 transport header는 contract version 자체로 고정한다. Canonical bytes와 credential은 digest 계산 뒤 폐기한다.

V1 field frame은 4-byte big-endian name byte length, UTF-8 name, 4-byte big-endian value byte length와 value bytes 순서다. Field는 `domain=nodease.generic-http-request.v1`, `method`, `target`, `header_count`, header별 `header_name/header_value`, `body_mode`, `body` 순서로 고정한다. Body bytes는 no-body의 empty bytes, JSON-null의 `null`, 정상 JSON의 sorted-key compact UTF-8 JSON, invalid JSON의 렌더링된 원문 UTF-8 bytes다. 고정 digest vector로 이 framing을 독립 검증한다.

Provider replay `supported` profile의 예약 key header/body field가 사용자 요청에 이미 있으면 덮어쓰거나 중복 값을 보내지 않고 network 전 `provider_key_field_conflict`, `failed_before_effect + stop`으로 닫는다. `unsupported|unknown`은 시스템 key를 만들지 않으므로 사용자 field를 보존하고 digest에 포함한다. `prepare_effect`는 key-free `PreparedEffectRequest`를 만들고 claim owner의 `finalize_provider_call`만 frozen key를 한 번 주입한다. 그 결과인 `PreparedProviderCall`이 invoke 단계의 유일한 request source이며 어느 단계도 graph/input/template을 다시 렌더링하지 않는다.

Slack profile은 server-derived logical node type으로만 선택한다. 현재 Client가 허용하는 endpoint 변경, mode와 method를 MBA-190에서 새 validation failure로 바꾸지 않는다. 실제 method와 rendered target은 effect input digest에 포함되므로 같은 stable slot에서 요청이 바뀌면 identity conflict로 닫히지만, URL이나 mode가 capability를 높이지는 않는다. Slack logical `ok=false` 판정과 mode별 provider 정책은 MBA-218이 소유한다.

Test support:

- Fake provider: test-only provider replay `supported` duplicate semantics와 장애 구간 검증용
- Fake adapter: safe result projection 유무로 result reuse `supported|unavailable` 검증용
- Pure domain/application test: provider replay `unsupported|unknown` no-replay 정책 검증용

Fake provider와 adapter는 `apps/workflow_engine/tests/fakes/` 또는 integration test support에만 두며 production composition이나 환경 설정으로 선택할 수 없게 한다. Fake provider는 duplicate, timeout, disconnect, provider accept 후 response loss와 malformed response를 재현한다. Fake adapter는 safe schema-compatible replay result를 제공하거나 의도적으로 제공하지 않는 variant를 둔다. Fake contract의 key field와 duplicate behavior는 공통 분기 검증용이며 실제 provider 지원 또는 exactly-once의 근거가 아니다.

Test-only contract `fake.create_effect.v1`은 다음 값으로 고정한다.

| 항목 | 고정값 |
| --- | --- |
| Key 전달 | `Idempotency-Key` request header |
| Key 형식 | HMAC-SHA256 digest의 padding 없는 Base64url 43자 문자열 |
| Provider 최대 길이 | 64자 |
| Provider retention | 최초 수락 시각부터 24시간 |
| Runtime deadline | Attempt 생성 DB 시각부터 24시간. Provider window보다 같거나 짧은 보수적 경계 |
| 같은 key, 같은 canonical request | Effect를 추가하지 않고 `200`과 최초 `effect_id` 반환 |
| 같은 key, 다른 canonical request | Effect를 만들지 않고 `409` 반환 |
| Retention 이후 같은 key | 새 effect로 처리. Runtime 자동 replay는 금지 |

Fake clock은 주입 가능해야 하며 24시간 경계 직전과 경계 시각을 실제 대기 없이 검증한다. 이 내부 테스트 계약에는 production provider의 공식 근거 요건을 적용하지 않으며 production profile registry에 등록하지 않는다.

Fake adapter는 같은 key/다른 request `409`를 duplicate success나 result projection으로 바꾸지 않는다. Provider가 새 effect 부재를 보장한 `failed_before_effect + stop`과 allowlisted `provider_key_request_conflict`로 mapping한다. 정상 application 경로에서는 stored digest mismatch가 provider 전에 먼저 차단되므로 직접 provider conformance 또는 방어적 adapter test에서만 이 분기가 나타난다.

Slack `ok=false`, provider error code와 `Retry-After`는 MBA-218의 책임이다. MBA-190은 Slack의 outcome unknown 자동 재호출을 막는 공통 경계만 제공한다. 공식 key format, retention과 duplicate response semantics가 준비되기 전에는 Slack `supported` 또는 exactly-once를 주장하지 않는다.

## Persistence

Table: `workflow_node_effect_attempts`

| Field group | Fields | Rule |
| --- | --- | --- |
| Identity | `id`, `organization_id`, `execution_id`, `node_invocation_id`, `operation`, `effect_sequence` | Full identity에는 operation이 포함되지만 DB unique는 operation 변경 우회를 막는 `organization_id + execution_id + node_invocation_id + effect_sequence` stable slot |
| Resource provenance | `app_id`, `workflow_id` | NOT NULL, FK 없음. 현재 node의 canonical owner를 attempt 생성 시 고정하고 subworkflow에서는 target resource 사용 |
| Correlation | `workflow_run_id`, `node_run_id`, `node_id` | Nullable correlation. Workflow/NodeRun FK 없음 |
| Provider contract | `provider`, `provider_contract_version`, `provider_replay_capability`, `result_reuse_capability` | Attempt 생성 시 고정 |
| Effect input | `effect_input_digest` | Provider key를 제외한 contract-versioned canonical request의 SHA-256. Raw input과 digest 노출 금지 |
| Replay deadline | `replay_deadline_at` | Provider replay `supported`일 때만 non-null. Attempt 생성 DB 시각과 당시 contract retention으로 고정 |
| Lifecycle | `status`, `outcome`, `replay_decision` | Outcome/decision은 terminal에서만 non-null |
| Claim | `claim_owner`, `claim_expires_at`, `claim_generation` | Generation CAS로 stale Worker 차단 |
| Key | `key_version`, `key_format_version`, `idempotency_key_fingerprint` | Provider replay `supported`일 때만 non-null. Raw key 저장 금지 |
| Replay | `replay_result` | `succeeded + reuse_result`에서 필수, `succeeded + result_unavailable`과 non-success에서는 null. Allowlist 뒤 canonical JSON UTF-8 최대 65,536 bytes |
| Provider summary | `provider_status_code`, `error_code` | Status와 allowlisted safe error code만 |
| Time | `provider_started_at`, `terminal_at`, `created_at`, `updated_at` | `provider_started_at`은 최종 call 검증 뒤 호출을 허용한 `in_flight` commit 시각이며 실제 함수 진입/byte 전송 증거가 아님. Status별 nullability와 허용된 reopen constraint |

Effect attempt는 audit 원장이나 raw provider response 저장소가 아니다. Credential, Authorization, API key, token, encrypted config, URL, header/body, prompt/user output와 provider exception 원문을 저장하지 않는다.

Provider replay capability가 `supported`면 `replay_deadline_at`이 non-null이어야 하고 `unsupported|unknown`이면 null이어야 한다. 이 구조 규칙은 DB CHECK로 고정할 수 있지만 현재 시각과의 비교는 runtime mutation에서만 수행한다.

Provider replay capability가 `supported`면 key version/format/fingerprint도 모두 non-null이고 `unsupported|unknown`이면 모두 null이어야 한다. `succeeded + reuse_result`는 result reuse capability가 `supported`이고 canonical JSON 65,536 bytes 이하 replay result가 있어야 한다. `succeeded + result_unavailable`은 capability가 `unavailable`이거나 supported projection 생성/크기 검증이 실패한 방어적 fallback에서 허용한다.

새 row 후보가 선택한 active key version은 unique winner가 되기 전까지 확정값이 아니다. Concurrent insert loser는 candidate key를 폐기하고 winner row의 key metadata를 사용한다. 실제 claim owner만 frozen key를 재생성해 fingerprint/길이를 확인하고 `PreparedProviderCall`을 완성한다. Fingerprint 불일치는 `in_flight`와 provider 호출 전에 stop한다.

MBA-190은 별도 recovery task나 scheduler를 추가하지 않는다. 같은 logical execution의 재진입이 identity unique key로 자기 attempt를 찾아 만료된 `in_flight`를 정리한다. Row 삭제를 수행하는 cleanup job도 추가하지 않는다. 후속 cleanup은 broker/task duplicate-delivery 최대 기간이 끝나고, `replay_deadline_at`이 non-null이면 그 시각도 지났으며, row가 더 이상 reopen될 수 없음을 증명하기 전 attempt row 또는 `replay_result`를 삭제할 수 없다.

Workflow Engine 시작 점검은 모든 attempt row가 참조하는 distinct `(provider, operation, provider_contract_version)`을 일반 index로 조회해 terminal duplicate delivery에 필요한 frozen profile까지 확인한다. HMAC key readiness는 provider 재호출 가능성이 남은 supported nonterminal row와 terminal `retry_before_effect|replay_same_key` row만 포함하는 `(provider, operation, provider_contract_version, key_version)` partial index를 사용한다. Schema revision은 기존 `apps/shared/services/alembic_readiness.py`로 현재 code head와 DB revision을 비교하고 external-effect table의 필수 column/constraint/index shape를 함께 검사한다. MBA-190 revision ID와 DB revision의 정확한 일치만 요구하지 않으므로 이후 descendant migration이 적용된 현재 head도 유효하다. 두 index 모두 만료 row를 전역 복구하기 위한 scan index가 아니다.

## Code Ownership

- `apps/shared/domain/workflow_execution_identity.py`: 모든 실행 표면이 함께 쓰는 `execution_id` 생성/검증 계약
- `apps/shared/domain/workflow_node_binding.py`: DB 비의존 binding schema/hash, Loop-aware walker, 주입된 immutable side-effect mapping 기반 classifier와 공통 depth constant. Shared service/catalog file을 역으로 import하지 않음
- `apps/shared/domain/external_effect_error.py`: Gateway, Workflow Engine과 trace capture가 함께 쓰는 공개 safe error code wire contract. Lifecycle/replay 판단은 포함하지 않음
- `apps/shared/services/external_effect_trace_capture.py`: Workflow Engine과 Log System이 함께 적용하는 metadata-only durable capture policy
- `apps/shared/services/workflow_node_catalog.py`: 기존 canonical catalog를 읽어 composition에 immutable `node_type -> side_effect` mapping을 제공
- `apps/shared/services/workflow_task_publisher.py`: `app.send_task` 기반 `workflow.*` 발행의 redacted repr과 raw publish exception 비노출을 제공하는 infrastructure helper
- `apps/gateway/application/deployment/workflow_node_binding.py`: 기존 deployment preflight와 같은 `DeploymentPreflightRepository` port, shared walker/정책을 쓰는 binding use case
- `apps/gateway/composition/deployment.py`: 기존 SQLAlchemy preflight repository adapter와 binding use case 조립. `DeploymentService`는 호환 facade만 담당
- `apps/shared/services/alembic_readiness.py`: current code head와 DB revision의 공통 판정. Workflow Engine 전용 checker는 이를 재사용하고 external-effect schema shape만 추가 검사
- `apps/workflow_engine/domain/external_effect.py`: provider key framing, lifecycle invariant와 두 지원 수준/outcome/replay pure rule. 공용 execution/node invocation framing은 `apps/shared/domain/workflow_execution_identity.py`가 소유
- `apps/workflow_engine/application/`: attempt claim, provider 호출 허용, terminal update와 replay result 반환
- `apps/workflow_engine/adapters/db/`: SQLAlchemy repository와 recovery query
- `apps/workflow_engine/adapters/providers/`: server-derived logical node type와 canonical operation으로 선택하는 provider profile과 network 동작
- `apps/workflow_engine/composition/`: DB session factory, repository, key provider와 provider adapter 조립
- `apps/workflow_engine/composition/external_effect_logging.py`: Workflow Engine 전용 provider transport logger 억제와 safe adapter summary wiring
- `apps/shared/db/models/`: SQLAlchemy table model
- `apps/workflow_engine/tests/fakes/`: production에서 참조할 수 없는 fake provider/adapter/server
- `apps/workflow_engine/tasks.py`: 모든 workflow task에 적용하는 redacted Celery Task/Request base와 safe result mapping. Task base는 `apply_async` 발행 기본값을, Request base는 수신 직후 repr을 고정한다.

현재 코드 기준 실제 연결 지점은 다음과 같다.

- `apps/gateway/api/v1/endpoints/workflow.py`: Draft 실행, Cost Optimizer candidate, Compare A/B, stream publisher에서 실행 ID를 발급하고 surface별 safe error를 mapping한다.
- `apps/gateway/services/deployment_service.py`: API/public 배포 실행의 최초 `workflow.execute` command에 ID를 넣고 deployment run 오류를 기존 `detail`에 mapping한다.
- `apps/gateway/application/deployment/preflight.py`와 `workflow_node_binding.py`: 동일한 `DeploymentPreflightRepository` port, organization/type/cycle/depth 정책과 shared Loop-aware walker를 사용하며 별도 target query 정책을 만들지 않는다.
- `apps/gateway/application/deployment/models.py`, `ports.py`와 `apps/gateway/adapters/db/deployment_preflight_repository.py`: 기존 target snapshot/port/adapter를 deployment ID/version/type/workflow/organization 검증에 필요한 pure field로 additive 확장한다.
- `apps/gateway/api/v1/endpoints/webhook.py`: `workflow.execute_by_deployment` command에 ID를 넣는다. 접수 응답은 유지한다.
- `apps/gateway/adapters/queue/celery_schedule_publisher.py`와 `apps/workflow_engine/tasks.py`: MBA-187 claim ID는 그대로 전달하고 admission winner가 schedule execution UUIDv5를 만든다. Task 함수는 `workflow.execute`, `workflow.execute_deployed`, `workflow.execute_by_deployment`, `workflow.execute_scheduled_deployment`, `workflow.stream`의 ID 보존, canonical provenance, non-retryable result와 schedule finalization을 담당한다.
- `apps/workflow_engine/workflow/core/workflow_engine.py`, `workflow_node_factory.py`, `workflow/nodes/loop/loop_node.py`, `workflow/nodes/workflow/workflow_node.py`: Root/Loop/subworkflow invocation path, stable effect sequence와 immutable `ExternalEffectContext`를 전달한다.
- `apps/workflow_engine/workflow/nodes/http/http_node.py`와 `workflow/nodes/github/github_node.py`: 기존 node output을 유지하는 얇은 진입점이 되고 provider adapter의 prepare/invoke result를 mapping한다.

Node와 Celery task는 application use case를 호출하고 result/error를 mapping하는 얇은 진입점으로 유지한다. DB query, provider SDK와 replay 정책을 Node class에 함께 넣지 않는다. Persistence operation은 session factory에서 매번 새 session을 받고 immutable node context에는 session을 넣지 않는다. Shared에는 여러 실행 표면이 실제로 소비하는 execution identity 생성·검증 계약, metadata-only capture 정책과 공개 safe error code wire contract만 둔다. External effect lifecycle, replay와 provider contract 정책은 Workflow Engine에 둔다. Gateway는 미등록 `external_effect.*` 코드를 외부 응답으로 전달하지 않고 등록된 코드의 message를 code 자체로 정규화한다.

Table/revision, 과거 provider contract와 필요한 HMAC key readiness 등록은 Workflow Engine composition이 소유한다. 공용 Celery app과 Log System Worker는 이 점검 때문에 시작을 거부하지 않는다.

Celery task 수신 INFO log와 `task-received|task-sent` event가 args repr을 포함하므로, 저장소 안에서 `app.send_task`를 호출하는 모든 `workflow.*` publisher는 공통 helper로 ID/graph/input/binding을 포함하지 않는 고정 `argsrepr`/`kwargsrepr`을 설정한다. Workflow task 전용 Task base는 `apply_async`에 같은 기본값을 강제해 `.delay`, signature와 `self.retry`의 sender event를 방어한다. Request base는 수신 log/event 발행 전에 consumer repr을 다시 같은 상수로 덮어써 legacy·외부 publisher도 Worker 측에서 방어한다. 실제 broker payload는 task 실행에만 사용하고 event/log/metric으로 복제하지 않는다.

## Error Handling

Adapter는 provider 호출 시작 전과 시작 후를 보수적으로 구분한다.

Provider 호출 코드는 raw response나 exception을 공통 application service에 넘기지 않고 typed `ProviderCallResult`로 바꾼다. 이 결과는 `outcome`, 요청 전송 전 실패의 검증된 retryability, 현재 실행에서만 사용하는 검증된 node output, 선택적인 allowlisted replay projection, HTTP status와 safe error code만 포함한다. 공통 service는 provider body/status/exception을 다시 해석하지 않고 lifecycle/replay policy만 적용한다. Network-free prepare가 deterministic digest를 만들지 못한 예상 밖 예외/canonicalization 실패는 application이 `external_effect.prepare_failed`로 바꾸며 attempt나 provider 호출 없이 stop한다. 기존 winner row, 저장 result와 downstream도 사용하지 않고 raw 입력/exception은 전달하거나 기록하지 않는다.

- Local validation/serialization 실패: `failed_before_effect + stop`
- DNS/TCP/TLS 연결 수립 전 실패와 connect/pool timeout처럼 request byte 미전송과 일시 장애를 transport가 함께 확정할 수 있는 실패: `failed_before_effect + retry_before_effect`
- 일부 request byte가 전송됐을 수 있는 write timeout/error, 연결 뒤 disconnect, read timeout/error, response loss/parse failure: `effect_outcome_unknown`
- Transport가 request write 여부를 증명하지 못하는 그 밖의 network error: `effect_outcome_unknown`
- 성공 사실은 확정했지만 안전한 결과 projection이 없는 중복 전달: non-retryable `external_effect.result_unavailable`
- 같은 identity의 provider/operation/contract/capability/digest 불일치: provider 호출과 저장 결과 재사용 전 non-retryable `external_effect.identity_conflict`
- 결과 불명이고 replay가 허용되지 않거나 deadline이 끝난 `stop`: non-retryable `external_effect.outcome_unknown`

1차 production 판정은 다음처럼 보수적으로 고정한다.

- Generic HTTP mutation은 local validation/JSON serialization 실패를 `failed_before_effect + stop`으로 본다. HTTPX `ConnectError|ConnectTimeout|PoolTimeout`은 request byte 미전송이 보장되는 경우에만 `failed_before_effect + retry_before_effect`, `InvalidURL|UnsupportedProtocol|LocalProtocolError`는 `failed_before_effect + stop`이다. `WriteError|WriteTimeout|ReadError|ReadTimeout|RemoteProtocolError`와 연결 뒤 disconnect/response loss는 `effect_outcome_unknown`으로 닫는다. 완전한 HTTP 응답은 status code와 관계없이 기존과 같은 node output을 반환하고, JSON이 아닌 body도 기존 text output으로 처리해 `succeeded`로 닫는다. Non-2xx나 non-JSON을 새 실패로 바꾸는 정책은 MBA-190 범위가 아니다.
- GitHub `comment_pr`는 공식 success status `201`과 `id` 정수, non-empty `html_url` 문자열, `body` 문자열을 모두 확인했을 때만 `succeeded`로 본다. 완전한 `401`, `403`, `404`, `410`, `422` rejection response와 Requests `MissingSchema|InvalidSchema|InvalidURL|InvalidHeader`는 `failed_before_effect + stop`이다. URL/header 오류를 network-free prepare/finalize가 검출하면 provider-start marker는 null이고, `in_flight` commit 뒤 같은 예외를 받으면 non-null이다. Requests가 공식적으로 안전한 재시도를 명시한 `ConnectTimeout`만 `failed_before_effect + retry_before_effect`로 분류하며 호출 허용 경계 뒤이므로 marker는 non-null이다. 일반 `ConnectionError`, `ReadTimeout`, response loss, `201` 뒤 JSON decode/필수 field type 검증 실패와 `201` 아닌 응답 중 위 다섯 rejection status가 아닌 `2xx`, `4xx`, `429`, `5xx`는 `effect_outcome_unknown + stop`으로 닫는다. Exception message나 중첩 원인 문자열로 더 낙관적인 분류를 만들지 않는다.
- Slack은 MBA-190에서 transport-level 결과만 공통 계약에 연결한다. HTTP 200 body의 `ok=false`, safe Slack error와 `Retry-After` 해석은 MBA-218이 typed result mapper에 추가한다. MBA-190 완료만으로 Slack logical success 판정이 해결됐다고 주장하지 않는다.

사용자-facing error와 log에는 provider exception message를 넣지 않는다. Adapter 내부에는 `timeout`, `connection_failed`, `response_malformed` 같은 allowlisted safe code를 사용한다. 외부 전달의 최소 고정 code는 결과 재사용 불가 `external_effect.result_unavailable`, frozen 요청 불일치 `external_effect.identity_conflict`, 결과 불명 stop `external_effect.outcome_unknown`이다. Application은 이를 JSON-safe `{code, message, retryable, node_id?}`로 만들고 세 오류에는 `retryable=false`를 사용한다. Non-retryable Celery task는 custom exception attribute 직렬화에 기대지 않고 내부 `status=error, error=<payload>` result로 반환하며, stream은 같은 payload를 Pub/Sub `error` event에 넣는다. 일반 동기 실행과 deployment run은 기존 HTTP status의 `detail`, Compare variant는 기존 safe `error` 문자열과 additive `error_detail`, Cost Optimizer candidate는 기존 safe `error_message`와 additive `error_detail`을 사용한다. Webhook은 접수 응답을 사후 실패로 바꾸지 않고 caller가 사용하지 않는 task result에 payload를 보존한다. Schedule claim에는 새 enum을 추가하지 않는다. Outcome unknown은 기존 `execution_outcome_unknown`, result unavailable과 identity conflict는 `execution_failed_after_admission`으로 finalization한다. 구체 code는 task-local safe error와 허용된 trace에만 두고 claim이나 identity-conflict winner row를 덮어쓰지 않는다. MBA-187 상태 전이와 retry는 바꾸지 않는다. Provider 응답 원문, digest나 내부 identity/key는 오류에 포함하지 않는다.

Process 내부에는 `ExternalEffectControlSignal` 아래 retry signal과 terminal safe error를 둔다. `LoopNode`는 `error_strategy=continue`보다 먼저 이 signal을 잡아 그대로 re-raise하고, Base Node와 WorkflowEngine도 generic `str(e)` log/error event 분기 전에 safe payload를 보존한다. Engine은 stream event를 직접 중복 발행하지 않고 stream task가 한 번만 발행한다. Task가 retry signal을 existing `self.retry`로, terminal error를 JSON-safe task result로 바꾸므로 Celery backend는 custom exception class/attribute에 의존하지 않는다. Root task와 `WorkflowNode`의 nested engine cleanup 및 DB session close는 best-effort로 실행하고 exception type만 기록한다. `WorkflowEngine.cleanup()`은 child별 실패를 격리한 뒤 모든 reference를 비우며 예외를 전파하지 않는다. Cleanup 실패가 원래 control signal, non-retryable result 또는 terminal commit 뒤 성공 output을 덮어쓰거나 generic retry를 시작할 수 없다.

### Codex 리뷰 보강

- 기존 terminal retry 또는 만료된 prepared attempt를 새 delivery가 claim하면 stable slot, provider contract와 replay metadata는 유지하고 `workflow_run_id`와 `node_run_id`만 현재 run으로 갱신한다. 이후 성공·실패 trace는 실제 claim 소유 run에 연결한다.
- GitHub `401`은 만료되거나 잘못된 credential에 대한 완전한 인증 거부이므로 `403|404|410|422`와 함께 `failed_before_effect + provider_rejected_request + stop`으로 분류한다.
- Workflow Engine이 실행에서 건너뛰는 canvas 전용 `note`는 binding 없는 legacy child graph의 classifier에서도 무시한다. Catalog에 없는 다른 node와 malformed/unknown action의 fail-closed는 유지한다.
- 마지막 delivery에서 유효한 다른 claim을 기다려야 하면 application이 `external_effect.claim_wait`, `retryable=false`로 닫아 모든 실행 표면에 구조화 오류를 반환한다. Stream task에서 예외적으로 external-effect retry signal이 Celery max retries를 소진해도 같은 terminal payload를 Pub/Sub `error` event로 한 번 발행한 뒤 최종 task 실패를 전파한다. 일반 `celery.exceptions.Retry`는 중간 delivery이므로 terminal event를 발행하지 않는다.
- Provider node의 실행 반환값은 downstream과 사용자 응답에서 기존 shape를 유지한다. 비동기 로그로 보낼 때는 Shared metadata-only capture policy가 provider node의 input/process/output과 payload record를 safe summary로 바꾸며 Log System task가 같은 정책을 다시 적용한다. Provider 결과를 전달받는 downstream node는 durable node input/process/output을 빈 값으로 저장한다. `WorkflowRun.outputs`와 run-level payload에서도 provider node 결과는 safe summary로, downstream node 값은 빈 값으로 저장한다. 중첩 Workflow/Loop는 시작 로그에서 입력·설정 payload를 보류하고, 외부 결과가 없는 안전한 성공 finish에서만 보류 값을 기록한다. 외부 결과가 있으면 server-owned 민감 표시를 부모 실행으로 전달해 컨테이너 노드와 그 downstream을 동일하게 처리하고 기존 payload가 있으면 같은 finish transaction에서 제거한다. 판별기는 Workflow Engine이 전달하는 `process_data.node_options` 구조를 직접 처리한다. 실패한 호출도 adapter metadata와 terminal outcome/replay decision을 합쳐 저장하되 raw request/response는 전달하지 않는다.
- SQL repository는 `SELECT ... FOR UPDATE` 뒤 `clock_timestamp()`를 읽어 lease와 replay deadline을 판정한다. Application은 `finish()`가 반환한 terminal decision만 retry의 기준으로 사용한다.
- Provider 호출 전 claim 확정 저장 실패는 provider를 호출하지 않고 `external_effect.claim_wait` 재시도 신호로 바꾸며, provider 호출 후 terminal 저장 실패는 output/downstream을 사용하지 않고 최종 안전 코드를 `external_effect.outcome_unknown`으로 둔다. Application은 repository 예외 원문을 stream event나 task 결과에 전달하지 않는다.
- Legacy deployed redelivery는 envelope의 exact frozen deployment를 조회한다. App의 current active deployment는 같은 app의 active row로 확인되는지만 kill switch로 검사하고 frozen graph를 current graph로 바꾸지 않는다.
- Generic HTTP의 완전한 응답에서 JSON UTF-8 decode가 실패하면 `response.text`로 fallback한다. External-effect PostgreSQL 증거 테스트는 일반 PR PostgreSQL CI에 포함한다. Downgrade empty-check는 table exclusive lock 뒤 수행한다.

## Trace And Logging

허용되는 durable summary:

- Provider와 canonical operation
- HTTP method와 status code
- Request/response size
- Latency
- 두 지원 수준, outcome, replay decision
- Safe error code
- Generic HTTP의 기존 목적지 `host`와 `path`
- Key fingerprint. Operational attempt column에만 허용하고 node output, 사용자 응답, 오류, durable trace, 일반 log와 metric label에는 사용하지 않음

금지되는 값:

- 전체 URL, query, fragment, URL user info
- Request/response header와 body
- Credential, Authorization, API key, token, encrypted config
- Internal identity, `effect_input_digest`, provider request identifier와 provider-visible raw key
- Provider library exception message 원문
- Celery task INFO/event의 실제 args/kwargs repr. Workflow command는 low-cardinality redacted 상수만 허용

Generic HTTP의 기존 목적지 `host`와 `path` field는 유지하되 `host`는 URL user info를 포함하는 `netloc` 대신 parsed hostname과 명시 port만 조합한다. Query, user info와 request/response raw payload는 제거하고 safe summary만 생성한다. `path` 자체의 추가 비식별화는 별도 보안 결정으로 남긴다. Slack은 incoming webhook path가 secret일 수 있고 endpoint도 사용자 설정이므로 URL/host/path 대신 `slack.http.request` canonical operation만 기록한다.

Workflow Engine 전용 composition은 `httpx`, `httpcore`, `urllib3`/Requests transport logger record를 억제하고 adapter가 위 safe summary만 남기게 한다. 공통 `workflow.*` publish helper는 serialization/broker 오류를 raw exception message나 traceback으로 기록하지 않고 static operation과 exception class만 기록한다. 두 정책은 Shared Celery signal 또는 Log System Worker에 전역 등록하지 않는다.

## Implementation Plan

1. ADR, architecture, data model, requirements와 test cases를 정렬한다.
2. Draft test, legacy deployed, deployment ID API/public/webhook, schedule, stream과 subworkflow에 공통 `execution_id` 생성/검증 계약을 전달하고 compare A/B에는 variant별 별도 ID를 발급한다.
3. Root, Loop, subworkflow invocation path builder와 immutable context를 추가한다.
4. Migration과 SQLAlchemy model을 추가한다.
5. Provider contract별 key-free effect input canonicalizer/digest, frozen key finalize 단계, pure domain rule, 독립 session을 사용하는 DB repository와 application use case를 추가한다.
6. 유효 claim loser의 lock 없는 제한적 대기와 같은 logical execution 재진입 시 자기 attempt의 만료된 `in_flight`만 정리하는 application 경로를 추가한다. 별도 scheduler는 추가하지 않는다.
7. Supported operation에만 적용하는 versioned HMAC key provider, 과거 provider contract availability와 Workflow Engine Worker 전용 startup/schema readiness check를 추가한다.
8. Generic HTTP mutation과 모든 Slack request를 외부 서비스 중복 방지 `unknown`, GitHub issue comment를 `unsupported`로 분류하고 세 operation의 결과 재사용을 `unavailable`로 고정해 공통 경계에 연결한다. Production profile의 unknown/none key field를 명시하고 Generic HTTP `GET`과 GitHub `get_pr`는 새 attempt를 만들지 않되 기존 stable slot effect row가 있으면 provider 호출 전에 identity conflict로 차단한다.
9. 고정된 `fake.create_effect.v1` contract를 따르는 test-only fake provider와 allowlisted replay result를 추가한다.
10. Generic HTTP, Slack과 GitHub external-effect 경로에 safe trace/log summary와 typed safe error mapping을 추가하고 오류 code/retryability를 Celery/PubSub/Gateway까지 보존한다. Base Node log와 Celery task INFO/event에도 provider 원문, workflow args와 새 identity가 도달하지 않게 한다.
11. 실제 PostgreSQL migration/model/readiness, concurrency, claim loser/expiry overlap, failure window, retry, compare variant, Loop/subworkflow, 조건부 key rotation, canonical JSON 65,536 bytes replay result와 redaction test를 추가한다.
12. 기존 workflow 저장/조회/실행과 MBA-187 경계를 회귀 검증한다.
13. 구현 검증 뒤에만 `Verified Against`와 data model current 상태를 갱신한다.

## Test Plan

- Domain: identity와 effect input canonicalization, frozen provider/contract/capability/digest 충돌, lifecycle invariant, 두 지원 수준/outcome/replay matrix
- PostgreSQL: migration upgrade/downgrade, model parity, stable effect slot unique 경쟁과 operation drift 차단, concurrent claim, lease expiry, replay deadline과 stale generation 차단
- Readiness: 기존 shared Alembic helper 기준 current code head/DB revision 불일치, 필수 table/column/constraint/index shape 누락, counter CHECK의 `effect_sequence >= 0`·`claim_generation > 0` 연산 방향 불일치, 시간 column의 `timezone=True` 불일치, Celery hard time limit 누락/비양수로 30초 여유를 더한 claim TTL을 계산할 수 없음, 필요한 과거 provider contract version 누락 또는 supported operation/attempt에 필요한 HMAC 설정 오류에서 Workflow Engine Worker만 startup fail-closed하고 Log System은 영향받지 않음. MBA-190 revision의 exact equality는 요구하지 않고 이후 descendant head를 허용
- Execution: 모든 실행 표면의 identity 유지, compare A/B ID 분리와 variant retry 안정성, Celery retry 누락 identity 차단, parallel node context와 DB session 격리
- Invocation: Loop iteration과 subworkflow path 분리, canonical catalog의 모든 `external_write` node를 포함하는 legacy child closure fail-closed와 Gmail 별도-ledger 비이중기록
- Failure: prepared crash, in-flight crash, timeout, disconnect, accept 후 response loss, GitHub/fake malformed response, terminal commit 실패와 stale generation output/downstream 차단. Generic HTTP 완전한 non-JSON response는 기존 text success 유지
- Recovery: 유효 claim loser는 lock 없이 terminal/만료를 제한적으로 기다리고, 같은 execution 재진입은 자기 만료 `in_flight`만 terminal unknown으로 바꾸며 상태 정리 중 provider나 workflow를 호출하지 않음. 별도 scheduler가 없음
- Replay: 결과 재사용 `supported`의 duplicate output schema, canonical JSON 65,535/65,536/65,537 bytes와 결과 재사용 `unavailable`의 typed 종료
- Key: supported operation의 restart/rotation/old key missing/provider length limit, active key rotation과 최초 insert 경쟁에서 winner key만 사용, fingerprint/finalize 실패의 pre-in-flight stop과 unsupported/unknown no-generation/no-injection
- Provider: 고정 provider/operation/version과 canonicalization, production unknown/none key field matrix, 공식 근거, test-only `fake.create_effect.v1`의 key/길이/24시간/duplicate/conflict 규칙과 `409` safe stop, fake adapter의 result reuse `supported|unavailable`, Generic HTTP mutation/모든 Slack request replay `unknown`, GitHub issue comment replay `unsupported`, 세 production operation result reuse `unavailable`, Generic HTTP `GET`과 GitHub `get_pr` no-new-attempt 및 downgrade guard
- Security: 기존 Generic HTTP `host`/`path` 유지, 전체 URL/query/user info, header/body, exception message, raw key와 identity 비노출, publisher와 Worker Request 양쪽의 Celery argsrepr/kwargsrepr redaction
- Regression: workflow draft 저장/조회, test/deployed execution과 schedule admission

Concurrency 완료는 SQLite 또는 in-memory fake만으로 주장하지 않고 실제 PostgreSQL integration test로 검증한다.

## Implementation Status

공통 계약과 SQL repository, migration/model/readiness, Generic HTTP·Slack·GitHub 연결, 실행 identity와 WorkflowNode target binding, safe error 전달 및 자동화 단위·회귀 테스트가 MBA-190 작업 트리에 구현되어 있다. Production adapter는 contract registry에서 active version과 과거 version을 함께 받고, 최종 provider call까지 frozen profile을 전달한다. Readiness는 counter CHECK 연산 방향과 모든 시간 column의 timezone 포함 여부까지 검사한다. Migration downgrade는 attempt row가 있으면 중단한다. Migration upgrade/readiness, 동시 claim 승자, lock 대기 중 lease/deadline 만료, operation drift, key version 최초 경쟁, stale generation과 downgrade를 검증하는 disposable PostgreSQL 테스트를 PR PostgreSQL workflow에 연결했다. 커밋 기반 `Verified Against`는 작업 트리가 커밋으로 확정된 뒤 기록한다.

## Out Of Scope

- Universal exactly-once 보장
- MBA-187 schedule dispatch/admission 재구현
- 전체 workflow/node retry 횟수와 backoff 재설계
- Celery delivery/ack 정책 재설계
- Claim heartbeat와 이미 시작된 provider 요청의 원격 강제 취소
- Slack provider-aware 오류 처리와 production idempotency 지원
- Slack request schema, mode, method 또는 target 제한
- Gmail provider 적용, GitHub의 새 중복 방지 기능 또는 새 connector
- 새로운 Public Workflow endpoint, 성공 node output/data schema와 Client UI 변경. 기존 실행 실패 계약과 Compare/Cost Optimizer의 실패 항목에 additive `error_detail`을 추가하는 것은 포함
- 만료 attempt를 전역 조회하는 주기적 recovery scheduler와 운영 정리 작업
- 수동 replay 관리 화면
- 검증되지 않은 provider deduplication 추정

## Linear Scope Traceability

Linear MBA-190 본문은 필수 범위이며 댓글은 후속 provider 작업의 소유 경계를 보충한다.

| 본문 요구 | 이 설계의 충족 위치 |
| --- | --- |
| `supported`, `unsupported`, `unknown`, key 형식·길이·보존 기간·응답 의미 | `Provider Replay And Result Reuse`, versioned contract profile과 `Provider Scope` |
| Execution identity를 외부 호출 코드까지 전달하고 비노출 | `Runtime Boundary`, `Identity Contract`, `Trace And Logging` |
| 지원 provider 공식 field 적용, 미지원 결과 불명 자동 재호출 금지 | Production `supported`가 없음을 명시하고 fake로 공통 분기 검증. `unsupported/unknown + effect_outcome_unknown -> stop` |
| `succeeded`, `failed_before_effect`, `effect_outcome_unknown`과 재호출 판단 | `Attempt Lifecycle`, replay matrix와 `Error Handling` |
| Duplicate/failure/secret 검증 | `Test Plan`과 `test_cases.md`의 PostgreSQL, fake, redaction 시나리오 |
| Exactly-once 표현 제한 | Production operation 모두 `unknown/unsupported`; fake를 production 증거로 사용하지 않음 |

| 댓글 경계 | MBA-190 제공 | 후속 이슈 소유 |
| --- | --- | --- |
| Gmail inbound fetch/claim/terminal acknowledgement | 공통 outcome 용어와 정합하되 현재 전용 ledger를 이관·이중 기록하지 않음 | MBA-217 / ADR-0032 |
| Slack `HTTP 200 + ok=false`, retryability와 provider response | Slack을 공통 경계에 `unknown/unavailable`로 연결 | MBA-218 |
| Gmail draft 생성과 중복 방지 | 현재 Mail 전용 durable claim/no-replay를 유지하고 공통 ledger에는 연결하지 않음 | 취소된 MBA-220 범위를 통합한 MBA-217 / ADR-0032 |
| 공통 identity/outcome/replay | 이 문서 전체 | MBA-190 |

## Completion Gate

이 문서는 합의된 MBA-190 공통 계약을 설명하는 Draft다. Migration, runtime, 현재 Generic HTTP/Slack/GitHub 연결, 재진입 복구와 readiness 관련 자동화 테스트를 구현했고 disposable PostgreSQL 증거 테스트를 PR workflow에 연결했다. 전체 자동 검토와 커밋 확정 전에는 `Verified Against`를 기록하거나 이슈를 Done으로 처리하지 않는다.

Production `supported` operation을 새로 만드는 것은 완료 조건이 아니다. 현재 operation의 정직한 `unknown|unsupported` 분류, 결과 불명 자동 재호출 차단, test-only fake의 supported 분기 검증과 실제 operation에 exactly-once를 주장하지 않는 조건을 모두 만족해야 한다. Gmail·Slack 후속 세부 기능은 MBA-217/218 완료를 기다리지 않는다.
