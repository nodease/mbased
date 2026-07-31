# Workflow Component Spec

Status: Draft
## Condition Exit Layout

- Standard BaseNode input and output handles use the fixed vertical offset calculated from `WORKFLOW_NODE_SIZE.height / 2`, with their centers placed directly on the left and right node boundaries.
- Canonical automatic layout top-aligns every rank instead of centering shorter ranks against the tallest rank.
- A Condition node renders its Default exit first, aligns the Default target with the Condition node, and expands configured branch targets downward.
- The Condition Default output handle uses the same fixed vertical coordinate as the standard input handle. Explicit branch handles are added below it at a stable `40px` interval, and the node shape grows to contain the final handle.

## Shared Connection Policy

- Workflow Node Capability Catalog v2 is the shared source for node role, incoming/outgoing allowance, and outgoing handle policy.
- Start, Webhook Trigger, and Schedule Trigger reject incoming edges. Answer rejects outgoing edges.
- Condition accepts only the Default handle or a configured case handle as an outgoing source handle.
- Frontend handle visibility and graph validation provide immediate UX feedback, while Agent Builder direct-edit GraphMutation의 common CAS save와 canonical acknowledgement validation이 persistence boundary다. Preview/apply-save 제품 경로는 사용하지 않는다.

## Runtime Scheduler Readiness

- Workflow Engine은 selector source를 `data_dependencies`, source별 target 역색인을 `data_dependents`로 계산하고 control edge index와 분리한다. 빈 selector 집합을 control predecessor 부재로 해석하지 않는다.
- 실행 시작 시 각 control edge는 `pending`이다. 일반 node 성공은 모든 outgoing edge를 active로 만들고, `selected_handle`을 반환하는 Condition 계열 node는 일치 edge만 active, 나머지는 inactive로 확정한다.
- incoming edge가 모두 inactive인 node는 실행하지 않고 inactive로 확정하며 그 outgoing edge에도 inactive를 전파한다. 필수 selector source가 inactive인 dependent도 누락된 값으로 실행하지 않는다.
- 명시적 entry 외 node는 모든 incoming edge가 확정되고 active predecessor 결과와 selector source 결과가 모두 준비된 경우에만 `pending -> queued`를 주장한다. Control target과 selector dependent 양쪽을 completion 후보로 재평가하되 lifecycle 전이는 node당 enqueue를 한 번으로 제한한다.
- node 실패와 workflow/node timeout은 현재 fail-fast 경계를 유지한다. 실행 중인 sibling은 취소되고 아직 pending인 downstream node는 시작하지 않는다.
- 이 상태는 실행 순서를 위한 in-memory scheduler state다. ADR-0033의 `RuntimeDataDependencyEnvelope`에 합산되는 active control dependency는 결과 lineage와 Memory authorization용 provenance이며 같은 저장 구조가 아니다.

## Conversation Memory Dispatch Admission Target

Conversation Memory target task는 Workflow inbound adapter가 side effect 전에 application contract를 호출한다.

- `AdmitExecution(dispatch_id, deployment_binding, task_contract, storage_generation, minimum_worker_capability)`: deployment ID/version 또는 snapshot hash, conversation mapping/Memory policy version과 capability를 검증하고 dispatch ID unique admission을 생성하거나 기존 admission을 반환한다.
- `GetExecutionAdmission(dispatch_id)`: Memory dispatch reconciler가 publish/acknowledgement ambiguity를 복구할 safe admission state/reference를 반환한다.
- Workflow가 execution lease, heartbeat, attempt와 node/tool retry policy를 소유한다.
- Memory는 admission/running/terminal safe projection만 받고 Workflow execution row를 직접 변경하지 않는다.

Celery task와 node runtime adapter가 admission table을 직접 insert/update하거나 capability 검증 전에 provider/tool/connector를 호출해서는 안 된다.

### Child Workflow Execution Lifecycle

- Root `WorkflowEngine`만 `WorkflowRun` 생성·완료·실패와 최상위 Redis workflow/node event를 소유한다.
- Loop body와 WorkflowNode target은 공통 child engine factory로 생성한다. Factory는 child lifecycle mode를 강제하고 부모 execution context를 방어적으로 복사한다.
- Child는 부모 `execution_id`와 `workflow_run_id` correlation을 유지한다. Loop는 iteration index, WorkflowNode는 frozen target deployment를 invocation path에 추가해 node invocation identity를 구분한다.
- Child는 부모 run 또는 별도 child run/node log를 직접 변경하지 않는다. 결과·일반 오류·timeout·external-effect control signal을 container에 반환 또는 전파하고 root가 전체 graph outcome을 한 번만 기록한다.
- 현재 durable child observability는 Loop/WorkflowNode container node log와 external-effect attempt의 invocation identity를 사용한다. Child node별 계층형 durable trace를 새로 저장하지 않는다.
- Child cleanup은 best-effort이며 cleanup 실패가 원래 성공 결과, 실행 오류, timeout 또는 external-effect control signal을 덮어쓰지 않는다.

### Runtime Data Dependency Envelope

Workflow Runtime은 node value와 `RuntimeDataDependencyEnvelope`를 하나의 result contract로 전달한다.

| Node/source | 책임 |
| --- | --- |
| Knowledge | Knowledge adapter가 KB/document version, sensitivity와 authorization-safe reference 발급 |
| Connector/tool | 승인된 adapter가 resource/item, source ACL/egress policy revision 발급 |
| Subworkflow | Target deployment version과 child output envelope 합집합 반환 |
| LLM | Prompt input, Memory Context, retrieval와 tool dependency 합집합 상속 |
| Transform/code | 모든 value input dependency 합집합을 그대로 상속하고 canonical dependency를 발급·제거하지 않음 |
| Condition/Switch | Predicate dependency와 선택 route를 active control context에 추가하고 선택된 output에 상속 |
| Loop | Iterable/bound/continue/termination dependency를 body와 loop aggregate에 active control context로 상속 |
| Final output | Answer에 영향을 준 upstream dependency 전체 합집합 전달 |
| System/privacy policy | Policy owner가 classification/redaction policy revision을 발급하고 Runtime이 result envelope에 합산 |

V1 canonical envelope은 값 dependency와 활성 control dependency의 합집합을 모두 필수로 취급한다. 선택된 branch의 상수 output도 predicate dependency를 상속하고 선택되지 않은 branch의 값 dependency는 합산하지 않는다. Code/custom adapter가 provenance를 반환하지 못하면 result를 `provenance_incomplete`로 표시하고 private/sensitive Memory write를 차단한다.

### Provider Execution Capability

Main generation, Memory summary와 RAG query embedding provider adapter는 Workflow admission 안에서 provider effect 없는 server-issued attempt reference를 먼저 만든 뒤 LLM Credentials domain의 authoritative port에서 해당 invocation/admission/attempt/purpose에 binding된 opaque capability identity/revision을 받는다. Query embedding은 authorized 후보의 distinct immutable embedding model마다 별도 attempt를 사용하고 같은 model 후보에만 invocation-local vector를 재사용한다. 상세 schema, credential principal과 permission decision revision은 [LLM Credentials API Spec](../llm-credentials/api_spec.md#target-provider-execution-capability-contract)이 소유한다. Runtime은 capability identity/revision을 Memory context lease, budget reservation, provider attempt와 usage reconciliation에 그대로 전달하고 client/Access Grant/owner 값으로 scope를 바꾸거나 credential principal을 합성하지 않는다.

Capability path의 `ProviderUsageRecorder`는 `LLMNode`가 Shared persistence를 직접 알지 않도록 application port로 유지한다. Recorder adapter는 admitted safe context의 canonical `(container_path, node_id)`를 포함한 exact binding으로 durable intent를 commit하고, final capability 재검증과 provider-start commit이 끝난 뒤에만 opaque lease invocation을 허용한다. Provider call 뒤 성공 measurement는 immutable admission pricing으로 계산하며 malformed 또는 admitted cap을 넘는 usage를 성공으로 축소하지 않는다. Terminal 저장과 compatibility projection은 별도 짧은 session을 사용하고 projection 실패가 이미 확정된 canonical success를 rollback하지 않는다. Log System reconciler는 stale started와 pending projection만 처리하며 provider client나 credential material을 로드하지 않는다.

`QueryEmbeddingExecutionService`는 authorized KB ID를 bounded model projection port로 scalar binding에 투영한 뒤 model별 provider port를 호출한다. Projection session과 capability control session은 provider I/O 전에 닫는다. Query provider port는 ADR-0067 guarded client가 봉인한 single-use request를 final-admit하고 ADR-0069 intent/start/outcome을 사용하며 raw query, vector, KB ID와 provider payload를 durable state에 전달하지 않는다.

## MBA-233 LLM Knowledge Selection And Runtime

- `LLMNodePanel`과 `LLMReferenceSidePanel`은 “고정 지식 베이스”와 “지식
  Collection”을 분리해 표시하고 각 목록의 `n / 20` 상태를 독립적으로 관리한다.
- Direct KB는 `/knowledge/llm-selectable`, Collection은
  `/knowledge/llm-selectable-collections`의 route-safe projection에서 선택한다.
- Saved item이 current picker response에 없으면 generic unavailable 상태로 보존한다.
  Picker load failure와 성공한 empty response를 구분하며 어느 경우에도 자동 삭제하지
  않는다.
- LLM node의 Knowledge-enabled 상태는 두 목록 중 하나라도 non-empty이면 true다.
  Collection-only node도 RAG evidence/failure controls를 사용할 수 있다.
- Gateway save는 Client validation을 신뢰하지 않고 current editor direct KB `use`와
  Collection `route`를 재검증한다. Save denial은 hidden resource identity 없이
  제거/권한 복구가 필요하다는 generic action만 표시한다.
- Workflow Engine은 runtime dependency로 주입된 MBA-232 resolver를 direct-only,
  Collection-only, mixed invocation에 한 번 사용한다. Resolver zero-result와 evidence
  insufficient는 provider 호출 전 safe 결과로 종료하고 infrastructure failure는 Celery
  retry 경계로 전달한다.
- Agent Builder, cost optimizer, compare/apply와 model-routing refresh는 unrelated edit에서
  `knowledgeCollections`를 보존한다. Public app graph와 실행 로그 option projection은
  Collection identity를 표시하지 않는다.

## Screens

- Workflow Builder 화면: 캔버스, 노드 라이브러리, 상단 액션, 테스트 실행 사이드바, 하단 캔버스 도구를 포함한다.
- 실행 이력에서 actor를 표시하는 화면은 schedule system run의 null `user_id`를 `System`으로 표시하고 App creator를 fallback으로 합성하지 않는다.

## Components

### Slack Delivery

- `SlackPostNodePanel`은 delivery mode, mode별 token 또는 Webhook URL, channel, message와 blocks를 편집한다. 이 token/Webhook URL은 현재 graph 내부의 전용 Slack credential 입력이며, generic HTTP endpoint, header, body, auth, timeout 입력은 제공하지 않는다. Credential reference 전환은 ADR-0037의 별도 후속 범위다.
- `SlackPostNode` runtime은 mode별 `SlackEffectAdapter`를 `Node._run_external_effect()`에 전달한다. ADR-0035의 공통 executor가 durable claim/replay를 소유하고 adapter가 endpoint policy, strict response parser, safe failure taxonomy와 trace summary를 소유한다.
- Slack node output handle은 공통 `status`, `delivery_status`, `delivery_mode`와 API mode의 `message_ref`만 제공한다. Webhook mode는 검증 가능한 message reference가 없으므로 `message_ref` selector를 제공하지 않으며, `data`, `headers` selector는 새 graph에서 허용하지 않는다.
- frontend validation은 legacy HTTP 설정을 migration 경고로 표시하고 모든 `*_selector`/`*_selectors` 실행 필드의 제거된 output은 오류로 차단한다. 사용자의 명시 migration command 없이는 legacy field를 제거하거나 active snapshot을 바꾸지 않는다. Backend draft save는 과거 selector를 보존할 수 있지만 deployment validation은 제거된 `data`/`headers` 및 Webhook `message_ref` selector를 차단하며, 기존 active snapshot은 runtime에서 safe migration-required error로 종료한다.
- Agent Builder preview/apply path는 dedicated Slack config와 `delivery_status` downstream selector만 생성한다. frontend와 backend deployment validation은 제거된 selector를 fail-closed한다.
- execution logger는 Slack node의 raw configuration, request, response, `message_ref` 원문과 error cause를 기록하지 않는다. 별도 Slack observer나 ledger를 두지 않고 공통 external-effect attempt가 중첩 실행, Loop error propagation, terminal result reuse와 no-replay를 소유한다.

### Generic HTTP Outbound Egress

- `HttpRequestNode`는 Generic HTTP composition factory만 호출한다. Node와 `GenericHttpEffectAdapter`는 HTTPX/httpcore client, socket, DNS resolver와 concrete egress policy를 생성하지 않는다.
- `GenericHttpEffectAdapter`는 prepared provider call을 transport-neutral outbound HTTP request로 바꾸고 typed port result를 ADR-0035의 provider outcome으로 변환한다. Canonical request와 external-effect application은 network policy를 다시 구현하지 않는다.
- Guarded HTTPX adapter는 Shared `OutboundEgressGuard`로 URL, method, port, header, body와 모든 DNS 결과를 검사한다. Custom httpcore network backend는 검증 IP 목록을 DNS/OS 순서로 사용하고 TCP connect 실패에만 다음 주소로 폴백한다. 연결 뒤에는 현재 선택한 IP와 peer 일치를 확인하며 정책 거부나 peer mismatch에서는 폴백하지 않고, TLS SNI/hostname 검증에는 원래 host를 사용한다.
- Adapter는 `trust_env=false`, redirect off, identity encoding, bounded header/request/response/timeout과 TLS verification을 강제한다. 3xx는 후속 hop을 호출하지 않고 기존 Generic HTTP response로 반환한다.
- Policy/DNS/transport exception은 raw destination이나 payload 없이 typed safe code와 failure phase만 application port로 전달한다. Provider는 pre-send permanent denial, proven pre-send transient failure와 post-send outcome unknown을 기존 effect error로 mapping한다.
- Provider-neutral Helm의 Worker egress NetworkPolicy는 cluster DNS, configured DB/Redis/Sandbox service port와 internal Squid `3129`만 허용한다. Public 80/143/443/993 direct route는 두지 않으며 Generic HTTP의 public 80, HTTPS CONNECT 443과 address-pinned IMAP CONNECT 143/993은 application guard를 통과한 뒤 Squid listener에서만 허용한다. 외부 dependency CIDR은 해당 service port에만 한정한다. Cluster는 policy를 실제 집행하는 CNI를 사용해야 하며, additive allow policy와 node-local/`hostNetwork` 예외가 없는지는 배포 전 positive/negative probe로 확인한다.

### LLM And Remote File Outbound

- LLM provider generation·embedding과 model discovery는 ADR-0067의 operation-bound guarded transport를 사용한다. Client/graph/credential snapshot이 목적지를 선택하지 않으며 current Provider catalog endpoint와 transport profile revision이 권위다.
- `FileExtractionNode`는 `RemoteFileFetcher` application port만 사용한다. Workflow composition은 graph에 file extraction node가 있을 때 guarded adapter를 주입하고 concrete HTTP client, DNS result와 temp path를 execution context나 node output에 전달하지 않는다.
- Remote file policy denial과 fetch/parser failure는 fixed typed reason으로 정규화한다. Signed URL, raw path, response body, resolved IP와 provider exception은 log, trace, audit와 사용자 output에 남기지 않는다.

### Configuration Preflight

- Deployment preflight application의 node validator registry가 Mail/Gmail Draft/Mail Acknowledge/Slack semantic readiness를 소유한다. Workflow endpoint, `WorkflowService`와 Client component에 같은 execution readiness 규칙을 복제하지 않는다.
- Catalog loader는 Gateway composition에서 immutable node side-effect mapping을 만들고 application use case에 주입한다. Application은 catalog 파일/loader, FastAPI, SQLAlchemy와 concrete service를 import하지 않는다.
- Repository adapter는 Mail credential을 organization-scoped active resource, provider/auth type, current principal `use` boolean과 enforcing deny 감사에 필요한 내부 effective auth state만 가진 snapshot으로 변환한다. Secret, email, display name, endpoint와 ciphertext는 application model에 들어가지 않으며 내부 auth state는 public preflight projection에 포함하지 않는다.
- Shared graph validator가 최상위의 명시적 trigger/start 진입점과 Loop body의 단일 implicit 진입점을 포함한 structural contract를 소유하고 endpoint, preflight와 Loop runtime이 같은 판정을 사용한다. Mail permission adapter는 organization 상태와 direct/team grant를 bulk 조회한다.
- Preview는 audit port를 호출하지 않는다. Enforcing use case만 내부 same-organization permission-denial decision을 audit port에 전달하고 public preflight projection에는 resource identity나 effective state를 넣지 않는다.
- Test Sidebar는 stream 시작 전 `409 workflow.configuration_preflight.blocked` 응답의 safe required action label을 표시한다. Generic 실행 실패 문구만 표시하거나 raw response object를 렌더링하지 않는다.
- Compare와 Cost Optimizer는 base graph preflight가 blocked이면 variant/candidate publisher를 시작하지 않는다. 정상 graph의 기존 결과 projection은 유지한다.
- Shared preflight classifier는 Catalog required configuration과 `external_read|external_write|local_execution` side effect를 기준으로 server-derived readiness를 계산한다. WorkflowNode는 `appId`를 필수 target으로 사용하고 runtime model도 선택 metadata인 `workflowId` 부재를 허용한다. Loop는 `subGraph`를 반복식으로 검사하고 `loop_key` 부재 시 mapped-input 첫 배열 fallback을 유지한다. `loop.item|index`, 상위 실행 입력, 현재 Loop까지 방향성 선행 경로가 있는 local output과 검증된 명시적 mapped input은 body implicit entry의 inherited source frame에만 포함된다. 후속 body node는 완료된 local result만 받으므로 inherited selector를 직접 사용하거나 downstream nested Loop child frame으로 전달하지 못한다. Mapping의 `value_selector`가 parent graph의 후행·형제 source 또는 stale output을 가리키면 child frame에 전달하지 않는다. Shared graph depth 상한 16을 넘으면 fail-closed한다. Gateway와 Workflow Engine은 이 판정을 공유하고 node `configuration_state`를 직접 신뢰하지 않는다.
- `CanonicalWorkflowNodeLocation`은 root와 Loop `subGraph`의 ordered path, exact node lookup와 fixed-size digest를 소유하는 Shared pure component다. Gateway graph preflight와 WorkflowNode binding은 같은 walker를 사용하고, Loop child는 `NodeExecutionControl.binding_container_path`에 parent Loop segment를 추가한다. Capability runtime은 이 trusted path만 LLM credential policy/admission에 전달하며 client graph/input의 location override를 사용하지 않는다.
- Gateway schedule adapter는 publish 전에 공통 configuration과 DB 기반 deployment target/policy를 검사한다. Workflow Engine schedule admission adapter는 `lock_canonical_bundle()` 뒤 canonical root identity와 공통 configuration 판정을 재실행한다. Publish 뒤 변경 가능한 WorkflowNode/KB/credential 상태와 권한은 runtime authoritative gate가 다시 검사하며, 이를 위해 Workflow Engine이 Gateway application을 import하거나 DB 정책을 복제하지 않는다.
- blocker는 기존 repository cancel contract와 `configuration_preflight_blocked`를 사용해 `running` 전 `canceled`로 닫고 safe audit만 기록한다. terminal claim 재전달은 duplicate 결과로 끝내며 reopen/retry하지 않는다.

### Mail Credential 설정

- `MailNodePanel`은 active organization에서 현재 사용자가 `use`할 수 있는 safe Mail credential option을 조회해 picker로 표시한다.
- Node data에는 `credential_id`와 `configuration_state`만 유지한다. Mailbox email, password, IMAP host/port와 provider secret 입력란은 Workflow Editor에 두지 않는다.
- `MailNode`와 실행 로그 설정 요약은 `연결됨` 또는 `연결 필요`만 표시하며 credential id, mailbox identity나 credential secret을 렌더링하지 않는다.
- Credential 등록·표시 이름 변경·secret 교체·revoke는 Mail Credentials API의 별도 관리 경계에서 수행한다. Mailbox identity와 IMAP endpoint/TLS mode는 생성 후 변경하지 않는다.
- `MailNodePanel`은 `search_only`와 `durable` 처리 모드를 제공한다. Durable mode에서는 즉시 읽음 설정을 비활성화하고 이유를 표시한다.
- Gmail OAuth 팝업 완료 후 credential을 자동 선택하지 않으며 사용자가 safe option 목록을 다시 조회할 수 있는 새로고침 control을 제공한다.
- `GmailDraftNodePanel`은 Mail processing output과 reply body output selector, Gmail OAuth credential을 선택하게 하되 recipient/send/attachment 입력을 제공하지 않는다.
- `MailAcknowledgeNodePanel`은 processing ref와 required effect output을 연결하며 임의 성공값을 입력받지 않는다.
- Agent Builder preview와 저장 workflow는 두 신규 node의 credential을 unresolved로 보존할 수 있지만 deployment 전 필수 selector와 credential을 해결해야 한다.

### 1. 실행 편의성

- `TestSidebar`
  - 테스트 입력값을 받고 기존 workflow stream 실행을 시작한다.
  - 오른쪽에 고정되며 기본 폭은 `560px`이다. 왼쪽 세로 handle을 드래그해 `440px`에서 `720px` 사이로 폭을 조정한다.
  - 패널 안의 글자는 기존 Tailwind 타이포그래피 단계보다 한 단계 크게 표시한다. 이 규칙은 TestSidebar 안에만 적용한다.
  - `paragraph` 타입 테스트 질문 textarea는 긴 질문을 더 많이 볼 수 있도록 최소 높이 `180px`를 사용하고 기존 세로 크기 조절을 유지한다.
  - 화면 폭이 최소 sidebar 폭과 canvas 가시 영역을 동시에 보장하지 못하면 handle을 숨기고 현재 화면 안에 들어오는 폭으로 표시한다.
  - handle은 keyboard focus가 가능하며 `ArrowLeft`/`ArrowRight`로 `20px`씩, `Home`/`End`로 최소/최대 폭을 조절한다.
  - 조정 폭은 같은 편집 세션의 패널 close/open 동안 유지한다.
  - 실행 중/완료된 노드별 상태, 소요 시간, 비용, 토큰 사용량을 표시한다.
  - 완료된 노드의 소요 시간, 비용, 토큰 사용량은 `node_finish` 이벤트의 `latency_ms`, `total_cost`, `total_tokens` 표준 필드를 우선 사용한다.
  - 표준 필드가 없으면 소요 시간은 프론트 수신 시각 기준 fallback을 사용할 수 있고, 비용/토큰은 `-`로 표시한다.
  - 노드별 상세 output은 필요할 때 JSON 형태로 확인할 수 있다.
  - 성공 결과 상단에는 `최종 응답` 카드를 표시한다.
  - `최종 응답` 카드는 최종 사용자가 받는 응답 preview를 표시하며, 사용자 응답 형태의 workflow output, answer/response node output, LLM node text output 순서로 fallback한다. `workflow output`이 현재 graph의 node id만 key로 갖는 전체 실행 컨텍스트이면 최종 응답으로 사용하지 않고 answer/response node output을 우선한다.
  - JSON 최종 응답은 raw JSON dump 대신 key/value preview로 표시하고, 원본 JSON은 노드별 실행 결과 상세 output에 유지한다.
  - 최종 응답 preview 추출은 `TestSidebar` 렌더링과 분리된 helper에서 수행한다.
  - 워크플로우 테스트가 완료되면 마지막 영역에 서버 실행 시간, 화면 완료 시간, 전체 비용, 전체 토큰 사용량을 최종 요약으로 표시한다.
  - 서버 실행 시간은 주 지표로 표시한다.
  - 화면 완료 시간은 보조 지표로 표시하며, 네트워크/stream/UI 처리 시간이 포함될 수 있음을 tooltip 또는 보조 문구로 설명한다.
  - stream의 `workflow_start.run_id`를 받으면 Client는 실행 식별자와 선택한 노드 id만 URL query에 유지한다.
  - 같은 workflow의 보고 화면을 거쳐 돌아오거나 새로고침하면, URL의 실행 식별자로 권한 확인된 workflow run 상세를 다시 읽어 마지막 실행의 결과·노드 카드·선택 상세를 복원한다. Editor store가 초기 placeholder workflow `default`만 가진 동안에는 복원 API를 호출하지 않고 테스트 실행 버튼을 비활성화한 채 URL의 persisted workflow가 활성화될 때까지 기다린다. 실행 기록이 아직 생성되지 않은 `404` 또는 `running`이면 실행 중 상태를 유지한 채 점차 길어지는 제한된 간격으로 다시 조회하며, 한도를 넘겨도 실패 결과로 바꾸지 않고 재시도 action을 표시한다. 브라우저 앞으로/뒤로가기로 URL의 `testRun`이 바뀌면 새 실행을 복원하고, `testRun`이 사라지면 이전 실행 결과를 초기화한다.
  - URL 실행 복원은 활성 workflow의 canonical draft metadata가 준비될 때까지 기다린다. 로딩 완료 여부를 현재 node 수로 판단하지 않으며, 로드가 끝난 빈 draft에서도 저장된 과거 실행을 복원한다.
  - 복원 결과는 실행 당시 저장된 node run 상태, duration, output, safe trace metadata를 표시한다. 현재 draft 설정을 실행 당시 설정으로 덮어쓰지 않는다.
  - 실행 당시의 노드가 현재 draft에서 삭제되었더라도 저장된 node run의 제목·상태·output·지표로 실행 결과를 표시하며, 현재 node data 부재로 TestSidebar 렌더링이 실패하지 않는다.
  - `다시 테스트하기`는 복원 식별자와 선택 상세를 함께 지우고 입력 폼으로 전환한다.
  - `실행 비교` segmented control을 켜면 기준 실행 선택 목록을 같은 사이드바에 표시한다. 목록은 어떤 실행도 자동 선택하지 않으며 상태는 `전체 상태`, 실행 방식은 `전체 방식`을 기본 필터로 사용한다.
  - `실행 비교` 탭을 누를 때마다 기준 실행 목록을 다시 조회한다. 이미 비교 패널을 열어둔 상태에서 새 테스트가 완료되거나 실패해도 목록을 다시 조회해 최신 실행 로그를 반영한다.
  - 기준 실행 목록의 `상세` 조회 또는 `기준으로 고정` 후 비교 기준 조회가 저장 지연·일시적인 네트워크 오류·`404`·`408`·`429`·`5xx`를 반환하면 한 번 짧게 다시 조회한다. `401`·`403`처럼 재시도로 해결되지 않는 오류는 즉시 원인을 안내한다. 최종 실패 시 사용자가 같은 행 또는 비교 오류 영역에서 `다시 불러오기`를 실행할 수 있어야 한다.
  - 기준 실행 목록 갱신이 실패해도 이미 불러온 실행 행은 숨기거나 삭제하지 않는다. 목록 오류 안내와 `다시 불러오기`를 함께 표시하며, TestSidebar 상단의 실행 기록 복원 `다시 시도`도 기준 실행 목록을 함께 재조회한다.
  - 기준 실행 목록은 실행 시각·실행 방식·대표 입력 한 줄만 표시하는 얇은 행으로 구성한다. 대표 입력은 credential·secret·token 계열 필드를 제외한 입력 문자열 중 식별력이 높은 값을 사용하며, 전체 입력과 실행 지표를 목록에 반복 노출하지 않는다.
  - `상세`를 누른 실행 하나만 상세 API로 조회해 행 아래에 전체 입력, LLM 노드별 실제 모델 라우팅 근거, 상태·시간·비용·토큰을 표시한다. 동시에 하나의 행만 펼치며, 상세를 열지 않은 실행 때문에 추가 상세 요청을 만들지 않는다.
  - 사용자가 `기준으로 고정`을 누른 실행과 비교 패널의 필터·선택 상세는 같은 TestSidebar 세션에서 `단일 결과`와 `실행 비교`를 오가도 유지한다. `기준 변경`을 누르기 전까지 기준 실행을 지우지 않으며, `다시 테스트하기`와 현재 workflow 재실행도 기준 실행을 지우지 않는다. 단, 새 테스트를 준비하거나 시작할 때는 이전 노드 상세 선택과 URL의 비교 노드 식별자를 지워 전체 실행 흐름과 노드 비교 목록부터 표시한다.
  - 테스트 실행에서 보고 화면으로 이동할 때는 현재 실행, 기준 실행, 비교 모드, 선택한 비교 노드를 URL query로 함께 전달한다. 보고 화면의 탭 이동과 `워크플로우 편집` 복귀도 같은 query를 보존하며, 복귀한 TestSidebar는 비교 상세를 다시 조회해 복원한다.
  - 기준만 있고 현재 실행이 없으면 기준 실행 시각과 `현재 설정으로 테스트를 실행하세요` 안내를 표시한다.
  - 전체 실행 비교는 성공·실패 같은 상태를 `기준 실행`과 `현재 실행` 배지로 분리한다. 비용·실행 시간·전체 토큰은 세로로 쌓고, 각 항목 안에서 기준 실행의 회색 가로 막대와 현재 실행의 파란색 가로 막대를 같은 길이 기준에서 비교한다. 변화율은 비용·실행 시간의 감소만 개선 색상으로 표시하며, 토큰 변화는 품질 향상 또는 저하를 단정하지 않는다.
  - 현재 실행이 완료되면 workflow canvas를 유지한 채 노드별 비교 목록을 표시한다. 각 노드는 이름과 node registry의 사람용 유형명, 성공 여부와 실행 시간을 양쪽 값으로 보여준다. `llmNode`에만 provider 사용량에서 나온 비용·토큰을 추가로 표시하며, 다른 노드에는 값 없는 비용·토큰 칸을 만들지 않는다.
  - 현재 실행이 `running`인 동안에는 비교 API를 실패로 처리하지 않는다. 별도 비교 대기 패널을 추가하지 않고 기존 노드 실행 진행 패널 하나에서 `실행이 완료되면 비교 결과를 준비합니다`를 함께 안내한다. terminal 상태로 전환된 뒤에만 현재 실행 상세를 조회하며, node run 기록이 아직 반영되지 않은 `404`/준비 중 상태는 제한된 backoff로 다시 조회한다.
  - stream의 노드 상태·관측값 갱신만으로는 현재 비교 데이터를 다시 조회하거나 로딩 화면으로 전환하지 않으며, 열어 둔 노드 상세도 유지한다. 기준/현재 실행 식별자, 현재 실행의 terminal 전환, 노드 식별·표시 정보 또는 사용자의 재시도 요청이 바뀔 때만 비교 데이터를 다시 읽는다.
  - LLM trace가 없으면 비교는 node run 기록만으로 계속 표시하고 `LLM trace 기록 없음`을 표시한다. trace API가 권한·서버·네트워크 오류로 실패하면 `일부 LLM trace를 불러오지 못했습니다` 경고를 표시해 모델 라우팅·토큰·비용 근거 일부가 누락됐음을 알린다.
  - `노드 상세 비교하기`를 누르면 페이지 이동 없이 TestSidebar 내부 상세로 전환하고, TestSidebar 본문 스크롤을 맨 위로 이동한다. 상세는 실행 상태 → 입력 → 모델 라우팅 → 출력 순서로 양쪽 값을 비교한다. `llmNode`에는 비용·토큰과 모델 라우팅 선택 모델·입력군·매칭 규칙·fallback·정책 버전·학습 포함 여부를 추가로 비교한다.
  - 상세의 `노드 비교 목록으로`를 누르면 canvas와 기준 실행을 유지한 채 목록으로 돌아간다.
- `BottomPanel`
  - 기본 캔버스 조작 도구만 유지한다.
  - 테스트 실행 요약을 표시하지 않는다.
- Node card observability
  - 기존 노드 카드의 running/success/failure 상태와 `observability` 표시를 유지한다.

### 2. 노드 조작 편의성

- `NodeFullscreenEditor`
  - 노드 상세 편집 화면을 구성한다.
  - 화면은 왼쪽 편집 패널, 가운데 작업/미리보기 패널, 오른쪽 보조 설정/참조 패널의 3패널 구조를 가진다.
  - 각 패널 사이에는 드래그 가능한 resizer handle을 둔다.
- Resizable three-panel layout
  - 기본 비율은 왼쪽 28%, 가운데 52%, 오른쪽 20%로 시작한다.
  - 기본 레이아웃은 부모 영역 기준 최대 90% 폭을 사용한다.
  - 왼쪽 패널을 넓힐 때 가운데와 오른쪽 패널은 남은 공간 감소분을 비슷한 비율로 나눠 부담한다.
  - 모든 패널은 `min-width`와 `max-width`를 가진다.
  - 전체 편집 영역은 viewport width의 최대 90%까지 사용하며, 초과 공간은 중앙 정렬 또는 기존 레이아웃 규칙을 따른다.
  - 3패널 본문은 EditorHeader를 제외한 NodeCanvas 가용 높이를 넘지 않으며, 가운데 설정 패널과 오른쪽 보조 패널은 그 높이 안에서 독립적으로 세로 스크롤한다.
  - 오른쪽 보조 패널을 열어도 Grid 높이는 콘텐츠 높이로 확장되지 않고, 패널 위의 wheel 입력은 캔버스 확대/축소로 전달되지 않는다.
  - 패널 폭 계산은 고정된 전체 px 값이나 grid 자기 자신의 현재 폭이 아니라, 실제 렌더된 부모 영역의 가로 폭을 기준으로 한다.
  - 사용자가 아직 직접 조정하지 않았다면 화면 크기 변경에 따라 기본 비율을 다시 계산한다.
  - 사용자가 resizer를 조정한 뒤에는 같은 편집 세션에서 사용자 조정 폭을 유지한다.

### 3. 워크플로우 조작 편의성

- Canvas keyboard delete
  - 캔버스에서 선택된 노드를 Backspace/Delete 키로 삭제한다.
  - 삭제 전 선택된 노드의 incoming edge와 outgoing edge를 수집한다.
  - 삭제 후 남는 upstream/downstream 노드 사이에 유효한 edge를 자동 생성한다.
- Auto reconnect helper
  - 삭제된 노드 집합을 기준으로 upstream candidate와 downstream candidate를 계산한다.
  - 기존 연결 검증 로직을 사용해 허용되는 연결만 생성한다.
  - 이미 같은 source/sourceHandle/target/targetHandle edge가 있으면 중복 생성하지 않는다.

### 4. 노드 실행 기록 패널 추가

- `NodeDetailsPanel` 또는 노드 상세 오른쪽 보조 패널
  - `설정`과 분리된 `실행 기록` 탭을 제공한다.
  - 실행 기록 탭은 현재 선택된 노드의 과거 input/output trace를 읽기 전용으로 표시한다.
- Node execution log default view
  - 기본 상태에는 `실행 목록 검색` 버튼과 `가장 최신 로그 기록 불러오기` 버튼을 표시한다.
  - 선택된 실행 기록이 없으면 빈 상태 안내를 표시한다.
  - 선택된 실행 기록이 있으면 같은 화면에서 상세 내용을 표시한다.
- Node execution log picker view
  - `실행 목록 검색` 클릭 시 오른쪽 패널 전체가 실행 로그 선택 화면으로 전환된다.
  - 상단에는 뒤로가기, 검색 input, 상태 필터, 정렬/기간 필터를 둔다.
  - 본문에는 최신 workflow run 목록을 표시하되, 각 row는 현재 node_id에 해당하는 node run/trace preview를 포함한다.
  - row 선택 시 picker view를 닫고 default view의 상세 상태로 돌아간다.
- Node execution log detail view
  - 선택된 workflow run 요약과 현재 노드 기록을 분리해 표시한다.
  - 현재 노드 기록에는 상태, 소요 시간, 토큰, 비용, 모델/프로바이더, input, output, error, metadata를 표시한다.
  - input/output은 JSON이면 code block 형태로, plain text면 줄바꿈이 보존되는 text block으로 표시한다.

### 5. 공개/내부 챗봇 배포

- Workflow editor 게시하기 메뉴는 공개 `chatbot`과 `internal_chatbot`을 별도 항목으로 제공한다.
- 배포 성공 화면은 공개 링크와 인증 내부 링크를 상호 배타적으로 표시한다. 내부 챗봇에는 public REST API secret/test panel을 표시하지 않는다.
- 내부 실행 페이지는 deployment run-info/run endpoint를 사용하고 `memory_mode: true`와 session `conversation_id`를 전송한다. 상세 UI 계약은 [Chatbot Deployment Component Spec](../chatbot-deployment/component_spec.md)을 따른다.

## States

### 1. 실행 편의성

- `idle`: 테스트 실행 전 상태. 테스트 실행 사이드바는 입력 폼과 실행 버튼을 표시한다.
- `running`: 실행 중 상태. 테스트 실행 사이드바에 실행 중/완료된 노드를 표시한다.
- `success`: 전체 실행 성공 상태. 테스트 실행 사이드바는 마지막 실행 결과와 전체 요약을 유지한다.
- `failure`: 전체 실행 실패 상태. 실패한 노드가 식별되면 해당 노드를 실패로 표시하고, 전체 실패 상태를 함께 표시한다.
- `uploading/preflight`: 파일 업로드, 그래프 검증, 드래프트 저장 중에는 테스트 실행 준비 상태로 본다.
- `agent-builder-saving`: Agent Builder graph save 또는 acknowledgement 결과를 확인하는 동안 테스트 실행 버튼을 비활성화하고 `Agent Builder 변경사항 저장을 확인하는 중입니다.`를 표시한다. 클릭 의도를 queue에 저장해 자동 실행하지 않으며 저장 확정 뒤 사용자가 다시 실행한다.
- Test preflight 저장의 `401`, `403`, `409 stale_graph`, `409 operation envelope not found`, 그 밖의 저장 실패는 서로 다른 안전한 안내를 표시한다. 충돌 중에는 draft를 자동 덮어쓰거나 test stream을 열지 않는다.
- Configuration preflight 차단은 기존 failure 상태를 사용하되 서버가 제공한 safe 설정 보완 action을 오류 문구로 표시한다. Task/SSE가 시작된 것으로 표현하거나 별도 실행 결과를 만들지 않는다.

### 2. 노드 조작 편의성

- `panel-resizing`: 사용자가 노드 상세 편집 화면의 resizer를 드래그하는 상태다. 이 상태에서는 패널 폭만 갱신하고 node data는 변경하지 않는다.
- `panel-default`: 사용자가 아직 폭을 조정하지 않은 기본 3패널 비율 상태다.
- `panel-constrained`: 사용자의 드래그 값이 최소/최대 폭 제약에 걸려 clamp된 상태다.
- `panel-custom`: 사용자가 resizer 또는 키보드 조작으로 기본 비율을 벗어난 상태다.

### 3. 워크플로우 조작 편의성

- `node-selected`: 하나 이상의 노드가 캔버스에서 선택된 상태다. Backspace/Delete 삭제 대상이 된다.
- `node-add-after-available`: 캔버스에 선택 노드가 있거나 단일 terminal node가 있어 왼쪽 패널에서 `뒤에 추가`를 실행할 수 있는 상태다.
- `node-add-after-pending-target`: 선택 노드가 여러 개이거나 분기 handle이 모호해 연결 대상을 더 선택해야 하는 상태다.
- `node-delete-reconnecting`: 선택 노드 삭제와 자동 재연결 edge 계산이 한 번의 graph update로 처리되는 상태다.

### 4. 노드 실행 기록 패널 추가

- `log-empty`: 실행 기록 탭에 진입했지만 선택된 로그가 없는 상태다.
- `log-loading-latest`: `가장 최신 로그 기록 불러오기` 요청이 진행 중인 상태다.
- `log-picker`: 실행 목록 검색/필터/선택 화면이 오른쪽 패널 전체를 차지한 상태다.
- `log-picker-loading`: 실행 로그 목록을 불러오는 상태다.
- `log-detail`: 선택된 workflow run과 현재 노드 기록 상세를 표시하는 상태다.
- `log-error`: 실행 로그 목록 또는 상세를 불러오지 못한 상태다.

## Interactions

### 1. 실행 편의성

- 테스트 버튼 클릭 시 기존 TestSidebar가 열리고 실행 입력을 받을 수 있다.
- 실행이 시작되면 테스트 실행 사이드바가 노드별 실행 카드를 순차적으로 표시한다.
- 실행 중 상태는 각 노드 카드의 상태 배지로 표시하며, 첫 노드 결과를 기다릴 때만 진행 패널 하나를 표시한다. 실행 비교 모드에서도 별도 대기 패널을 추가하지 않고 같은 진행 패널 안에 비교 준비 안내를 함께 표시한다.
- 실행 중인 노드는 캔버스에서 기존처럼 중심 이동/상태 강조를 유지한다.
- 테스트 실행 사이드바의 노드별 실행 카드는 상태, 소요 시간, 비용, 토큰 사용량을 compact 형태로 표시한다.
- 노드별 실행 카드의 숫자 지표는 노드 output 내부 구조를 직접 추측하지 않고, stream 이벤트의 node-level summary 표준 필드를 기준으로 표시한다.
- 워크플로우 테스트가 성공하면 테스트 실행 사이드바는 성공 안내 다음에 `최종 응답` 카드를 먼저 표시하고, 그 아래에 서버 실행 시간, 화면 완료 시간, 전체 비용, 전체 토큰 사용량과 노드별 상세 결과를 표시한다.
- workflow-level 서버 실행 시간이 없으면 노드별 `latency_ms` 합산값을 `서버 실행` fallback으로 표시한다. 노드 latency도 없을 때만 `서버 실행 -` 또는 `서버 실행 기록 없음`으로 표시하고, 화면 완료 시간은 계속 표시한다.
- 사용자가 다시 테스트하기를 누르면 이전 실행 요약은 초기화된다.

### 2. 노드 조작 편의성

- 사용자는 노드 상세 편집 화면에서 패널 경계선을 드래그해 패널 폭을 조정한다.
- 왼쪽 패널 resizer를 오른쪽으로 드래그하면 왼쪽 패널이 넓어지고, 가운데/오른쪽 패널은 비슷한 비율로 좁아진다.
- resizer 드래그 중에는 커서와 handle 상태가 resize 중임을 보여준다.
- 최소/최대 폭에 도달하면 더 이상 같은 방향으로 폭이 변하지 않는다.
- 더블 클릭 또는 별도 reset affordance가 있다면 기본 3패널 비율로 되돌릴 수 있다.

### 3. 워크플로우 조작 편의성

- 왼쪽 노드 패널의 각 노드 항목은 일반 추가와 `뒤에 추가` 액션을 구분해 제공한다.
- 일반 추가는 기존 자유 배치 또는 캔버스 추가 흐름을 유지한다.
- `뒤에 추가`는 현재 선택된 노드를 기준으로 오른쪽에 새 노드를 local placement하고, 선택 노드에서 새 노드로 edge를 생성한다.
- 선택된 노드가 없고 terminal node가 하나뿐이면 `뒤에 추가`는 해당 terminal node를 기준으로 동작할 수 있다.
- sticky note처럼 workflow 실행 graph에 포함되지 않는 보조 노드는 terminal node 개수 계산에서 제외한다.
- 선택 노드가 여러 개이거나 terminal node가 여러 개이거나 condition/switch/loop처럼 연결 handle이 모호한 경우에는 임의 연결하지 않는다. 연결 대상 또는 handle 선택 UI를 표시하거나 액션을 비활성화한다.
- `뒤에 추가`의 edge 생성은 일반 연결 validation을 통과한 경우에만 적용한다. validation이 실패하면 새 노드도 남기지 않는다.
- `뒤에 추가` 실행 후 전체 graph 자동 정렬을 수행하지 않는다. 새 노드와 기준 노드 주변만 겹치지 않게 배치한다.
- `뒤에 추가`는 undo 한 번으로 노드 생성과 edge 생성을 함께 되돌릴 수 있어야 한다.
- 사용자가 캔버스에서 노드를 선택하고 Backspace/Delete를 누르면 선택 노드를 삭제한다.
- 삭제되는 노드의 앞단과 뒷단이 모두 존재하면 삭제 후 앞단 노드에서 뒷단 노드로 자동 edge를 생성한다.
- 입력 요소에 focus가 있는 상태에서는 Backspace/Delete가 텍스트 삭제로 동작하고 노드 삭제를 실행하지 않는다.
- 자동 재연결이 불가능한 경우에는 삭제만 수행하고 새 edge를 만들지 않는다.
- 삭제와 자동 재연결은 undo 한 번으로 되돌릴 수 있는 단일 편집 동작이어야 한다.

### 4. 노드 실행 기록 패널 추가

- 사용자가 `실행 기록` 탭에 진입하면 기본 view를 표시한다.
- 사용자가 `실행 목록 검색`을 클릭하면 오른쪽 패널 전체가 picker view로 전환된다.
- picker view에서 검색어/상태/기간 필터를 변경하면 목록을 갱신한다.
- picker view의 실행 로그 row를 클릭하면 해당 run 안의 현재 노드 기록을 선택하고 detail view로 전환한다.
- 사용자가 `가장 최신 로그 기록 불러오기`를 클릭하면 현재 node_id 기록이 존재하는 가장 최신 workflow run을 찾아 detail view에 표시한다.
- 선택한 run에 현재 node_id 기록이 없으면 상세 대신 `이 실행에서 현재 노드 기록 없음` 안내를 표시한다.
- 실행 기록 탭에서 표시되는 input/output은 현재 노드 설정값을 변경하지 않는다.

### 5. 공개/내부 챗봇 배포

- 내부 실행 페이지가 `401`을 받으면 path/query/hash를 safe `next`로 보존해 로그인 화면으로 이동한다.
- 이메일/비밀번호 로그인 성공 후 same-origin `next`로 복귀하며, unsafe URL은 `/dashboard`로 닫는다. Google OAuth는 기존 dashboard 복귀 계약을 유지한다.

### 6. LLM Citation 설정과 표시

- LLM Reference side panel은 `답변·검색 문서 어휘 일치도`와 `출처 표시`를 별도 select로 제공한다.
- `출처 표시` 옵션은 `숨김`, `기본`, `상세`이며, 상세 mode가 제한된 본문 미리보기를 포함할 수 있음을 helper text로 알린다.
- 새 수동 LLM node와 Agent Builder 생성 node는 `상세`를 기본으로 저장한다. 값이 없는 기존 node는 응답 호환성을 위해 `숨김`으로 표시한다.
- Test sidebar와 인증 실행 화면은 최종 사용자 답변 아래에 공통 `CitationList`를 표시한다. Citation이 없거나 malformed이면 목록만 생략한다.
- Citation 목록은 native disclosure를 사용하고 keyboard로 열 수 있어야 하며 긴 라벨·section·preview는 작은 viewport에서 줄바꿈되어야 한다.

## Accessibility

### 1. 실행 편의성

- 테스트 실행 요약의 상태는 색상뿐 아니라 텍스트(`실행 중`, `성공`, `실패`)로도 표시한다.
- 숫자 정보는 `ms`, `tok` 단위를 함께 표시한다.
- 비용 정보는 통화 단위 또는 소수점 자리수를 일관되게 표시한다.
- 서버 실행 시간과 화면 완료 시간은 서로 다른 라벨로 표시한다. `전체 실행 시간`처럼 둘 중 어느 기준인지 모호한 라벨을 사용하지 않는다.
- 테스트 실행 사이드바가 좁아도 노드명, 상태, 시간 정보가 겹치지 않아야 한다. `llmNode`의 비용·토큰 정보도 같은 기준으로 겹치지 않아야 한다.
- `최종 응답` 카드의 긴 응답은 카드 내부에서 줄바꿈과 스크롤로 처리하며, 다음 실행 요약 또는 노드별 상세 결과를 가리지 않아야 한다.

### 2. 노드 조작 편의성

- resizer handle은 키보드 focus가 가능해야 한다.
- 키보드 사용자는 좌우 방향키로 패널 폭을 일정 step 단위로 조정할 수 있어야 한다.
- resizer handle에는 현재 조정 대상과 조작 방법을 설명하는 accessible label을 제공한다.
- 패널 폭이 바뀌어도 각 패널의 주요 버튼/입력/라벨 텍스트가 겹치지 않아야 한다.

### 3. 워크플로우 조작 편의성

- 키보드 삭제 shortcut은 focus context를 구분해야 하며, 입력 필드 사용자의 기본 Backspace/Delete 조작을 방해하지 않아야 한다.
- 레이아웃 최적화는 하단 툴바의 버튼 액션으로 제공한다.
- 레이아웃 최적화 버튼이나 tooltip에는 아직 shortcut 표기를 노출하지 않는다.
- 노드 삭제 전 별도 확인 모달을 띄우지 않는다면, undo 경로가 명확해야 한다.

### 4. 노드 실행 기록 패널 추가

- picker view의 검색 input과 필터는 label 또는 accessible name을 가져야 한다.
- 실행 로그 row는 상태를 색상뿐 아니라 텍스트(`성공`, `실패`, `실행 중`)로 표시한다.
- input/output block은 긴 텍스트가 패널 밖으로 넘치지 않고 스크롤 또는 줄바꿈으로 읽을 수 있어야 한다.
- 패널 전환 시 focus가 예측 가능해야 한다. picker view 진입 시 검색 input 또는 뒤로가기 버튼에 focus를 둘 수 있다.

### 5. Test preflight save coordinator

- TestSidebar는 canonical GET부터 valid `workflow_start.run_id` 수신까지 `test_preflight` owner를 유지한다.
- Stream 요청 직전에 owner 종류와 무관하게 저장 대기자가 하나라도 있으면 test stream을 열지 않고 lock을 해제해 대기 저장을 먼저 처리한 뒤 사용자의 재시도를 안내한다.
- Stream 요청 뒤 도착한 autosync, Undo/Redo, Agent Builder와 version restore 저장은 `workflow_start.run_id`로 실행 snapshot이 확정될 때까지 기다린다. 실행 시작 실패·취소·timeout에서는 preflight owner를 해제하고, 실행 전체가 끝날 때까지 저장을 차단하지 않는다.
- Clean editor라도 canonical graph 비교를 수행하고 불일치 시 metadata ingest와 실행을 차단한다. 이 실행 전 동일성 비교는 canonical graph hash에서 제외되는 viewport를 무시하며 pan/zoom만 다른 경우 테스트를 허용한다. 실제 draft 저장·조회와 version restore의 viewport 계약은 유지한다.
- `401`, `403`, `409 stale_graph`, `409 operation envelope not found`와 일반 저장 실패를 서로 다른 안내로 표시한다. Stale canonical graph는 local snapshot 비교 전 metadata를 store에 반영하지 않고, operation envelope 복구는 `applied|unapplied|pending|stale` 상태별 안내를 사용한다.
- 실행 중 node status/observability, editor-only `displayNumber`, React Flow measurement/selection field는 비영속 presentation state로 갱신하며 graph edit action을 사용하지 않는다. Agent Builder, autosync, version 복원, test preflight, Undo/Redo와 note 저장 payload는 공통 recursive canonical serializer를 사용한다.
- Version restore는 선택한 workflow의 save lock을 획득한 직후 현재 active workflow를 다시 확인한다. 대기 중 다른 workflow로 전환됐다면 canonical GET/POST, metadata ingest와 editor/history 갱신을 수행하지 않는다.
- Version restore serializer는 modern snapshot의 `features.noteNodes`를 우선하며 명시적 빈 배열을 보존한다. Field가 없는 legacy snapshot은 `nodes`의 Note로 fallback하고 두 표현이 모두 없으면 현재 editor Note를 유지한다. 복원한 Note 집합은 editor nodes와 save payload의 `features.noteNodes`에 동일하게 반영한다.
