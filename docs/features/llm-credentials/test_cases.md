# LLM Credentials Test Cases

Status: Draft
## 단위 테스트

- Credential registration gate는 organization manager만 통과시키고 일반 active member, builder/operator, credential `use` 권한자, credential `manage` 권한자를 새 credential 등록 권한자로 취급하지 않는다.
- Credential response builder는 저장 schema의 `user_id`를 개인 credential owner 표시로 노출하지 않거나, 노출이 필요한 기존 response에서는 등록 행위자 reference로만 취급한다.
- Agent answer option builder는 credential value, encrypted config, API key/token, raw owner metadata, 불필요한 raw timestamp를 제외한다.
- Credential-model relation resolver는 inactive, unverified, wrong-provider, missing relation case를 거부한다.
- LlamaParse credential resolver는 execution subject와 active organization이 모두 있을 때만 같은 organization의 valid `llamaparse` credential을 조회하고 `use` 권한을 다시 확인한다. 다른 user/organization credential, revoke/invalid, provider 불일치, 권한 상실, context 누락은 parser 호출 전에 차단한다.
- LlamaParse resolver는 허용 후보가 하나일 때만 parser 입력을 반환한다. 후보 없음 또는 둘 이상은 created_at/name/latest/default fallback 없이 fail-closed한다.
- LlamaParse credential 후보가 존재하지만 모두 subject의 `use` 권한이 없으면 parser 호출 전에 `permission.denied` audit을 한 번 기록한다. 허용 candidate가 있는 요청에서 다른 후보의 거부 때문에 audit을 추가하지 않으며, audit에는 credential config/API key/provider raw payload를 남기지 않는다.
- Generation credential preflight는 KB permission, collection route permission, source ACL authorization을 충족시키지 않는다.
- ProviderExecutionCapability issuer는 opaque identity/revision, organization/workflow/deployment version, canonical `(container_path, node_id)`, node invocation/execution admission/provider attempt, provider/model/credential, server-derived credential principal, credential permission decision, purpose, verified relation/provider-routing·pricing revision, token·cost cap과 expiry를 모두 고정한다.
- Capability response/trace에는 raw credential, encrypted config와 capability token/scope 원문을 노출하지 않는다.
- Capability consumer가 client 값으로 credential principal 또는 permission revision을 덮어쓰려 하면 발급·사용을 거부한다.
- Credential config service는 active key round trip, 구키 decrypt, canonical JSON과 required `apiKey` validation을 수행한다.
- Encryption metadata가 모두 null인 legacy row만 평문 read를 허용한다. Metadata 일부 누락, unsupported algorithm, unknown key version, 손상 ciphertext 또는 invalid config는 평문 fallback 없이 실패한다.
- Gateway와 Workflow Engine의 신규 credential 등록은 raw config와 다른 ciphertext, active key version과 algorithm을 저장한다.
- Gateway·Workflow Engine·RAG answer·embedding·LlamaParse의 decrypt 실패 테스트는 provider/client mock 호출이 0회임을 검증한다.
- Deployment policy resolver는 immutable deployment graph의 exact `(container_path, node_id)`를 확인한다. Main generation은 graph-owned active `chat` model만, query embedding은 Knowledge가 설정된 node의 active `embedding` model만 허용한다. `credential_id`/`credentialId`, fallback model, auto-routing이 있는 capability-required graph는 거부한다. Terminal/parent ID는 255자, Loop path는 깊이 16까지 허용하며 초과·unknown kind·missing location은 row를 쓰기 전에 거부한다.
- 같은 deployment version/location에 model UUID가 다른 두 active policy를 만들 수 없고, 서로 다른 Loop의 동일 `node_id`에는 별도 active policy를 허용한다. Concurrent 최초 policy write는 canonical deployment와 active policy를 순서대로 lock한 뒤 authorization 근거를 잠가 하나의 active revision으로 수렴하거나 safe `409`로 종료한다.
- Stored digest와 structured path/node가 일치하지 않거나 한 location의 capability를 다른 Loop에서 재사용하면 credential materialization과 provider/client 호출은 0회여야 한다.
- Policy 변경과 runtime permission denial audit은 canonical location에서 계산한 bounded opaque reference만 기록하고 raw `container_path`, nested input, credential/capability 원문을 기록하지 않는다.
- PostgreSQL migration 검증은 기존 row의 root digest backfill, 서로 다른 location의 동일 node ID 허용, 같은 location의 active row 중복 거부와 nested row downgrade 차단을 실행한다.
- Deployment policy write는 manager actor를 server-derived credential principal으로만 사용하고, request의 credential config/principal override를 받지 않는다. Same organization, active credential/provider, verified single relation, credential `use`를 만족하지 않으면 policy row를 만들지 않는다.
- Capability-required LLM node는 trusted node invocation control과 explicit token/cost cap이 없으면 provider client를 만들지 않으며, legacy user/app owner/default/name/order/fallback selection을 호출하지 않는다.
- Capability-required LLM node는 messages와 tools·response schema 등 provider-visible parameter 구조 전체의 UTF-8 byte upper bound, `max_tokens`와 canonical pricing 최대 비용 중 하나라도 cap을 넘으면 provider client/SDK를 호출하지 않는다. Provider-specific JSON schema format을 client materialization 뒤 추가하면 완성된 request로 같은 capability를 재-admission하고 attribution의 admitted token snapshot도 교체한다. 재-admission 실패는 LLM node가 삼키지 않으며 usage intent와 provider I/O를 모두 0회로 유지한다. Output limit이 없으면 server cap을 적용하고 직렬화 불가 parameter, provider-specific output-limit alias와 missing pricing은 fail-closed한다.
- Capability-required LLM node는 request parameters의 `model`을 항상 거부하고 `n`과 `best_of`는 boolean/string을 포함해 정확한 정수 `1`이 아니면 provider client/SDK 호출 전에 거부한다.
- Capability-required LLM node의 authorized Knowledge 후보가 0개면 query policy/capability/usage/provider를 0회로 유지한다. 후보가 있으면 distinct canonical embedding model마다 query capability와 provider attempt를 하나 사용하고 같은 model 후보만 vector를 공유한다. Preflight plan과 execution request/adapter state의 organization 또는 node가 다르면 model projection과 provider 호출은 0회다. Missing/stale query policy나 권한·relation·pricing·egress 실패는 legacy credential와 main provider 호출 전에 fail-closed한다. Query policy write mode 기본값은 disabled이고 활성화 전 mutation은 safe `503`이며 main policy 관리에는 영향을 주지 않는다.
- Policy write와 capability issue/admission은 lock 전에 같은 Session에 적재된 organization, user, membership, grant, model, provider, credential, relation, policy와 capability row를 강제 갱신한다. Lock 대기 중 권한 회수·비활성화가 commit되면 이전 identity-map 상태로 manager/use 권한을 허용하지 않는다.
- Worker process와 PostgreSQL clock이 어긋나도 capability `created_at`, `updated_at`, `expires_at`은 같은 DB wall clock으로 발급되고 `expires_at`은 그 시각 + TTL이어야 하며, admission의 최초·최종 만료 검사는 모두 lock 이후 DB wall clock을 사용하고 worker process clock을 호출하지 않는다.
- 서로 다른 provider의 model row가 같은 API model identifier를 사용해도 capability admission이 고른 canonical model UUID가 비용 계산과 `llm_usage_logs.model_id`에 유지된다. Capability usage는 admission 중 봉인한 pricing revision과 immutable input/output 가격 snapshot을 사용해 provider 호출 중 model row의 가격 필드가 변경돼도 admission과 동일한 비용을 기록하고 catalog로 우회하지 않으며, legacy usage만 같은 상황에서 shared catalog fallback을 유지한다.
- Capability config는 encrypted/legacy row 모두 Shared `LLMCredentialConfigService`만 읽는다. Client는 credential config의 과거 base URL snapshot이 아니라 admission에서 잠근 provider catalog URL을 사용한다. HTTP/non-443 endpoint, private·metadata DNS result, peer mismatch, redirect와 ambient proxy는 provider request 전에 거부하고 current LLM transport profile revision 변경은 stale capability를 차단한다. Config/client materialization 성공 뒤 전용 capability transaction을 provider SDK 호출 전에 commit하며 Workflow legacy/shared session은 전달·commit하지 않고 provider 실패 뒤에도 발급 row가 rollback되지 않는다.
- `LLMNode`는 Shared capability domain/service, concrete adapter 또는 composition module을 import하지 않고 Workflow application port만 소비한다. Runtime router도 Shared capability domain/service와 legacy `LLMService`를 import하지 않고, legacy `LLMService`는 capability 계약을 소유하지 않는다. Shared issue/admission command와 config/client materialization은 capability adapter 내부에만 존재하는 정적 architecture test로 이 경계를 보호한다.
- WorkflowEngine composition은 provider runtime과 usage recorder를 session을 미리 열지 않고 한 번 구성해 node에 주입한다. `WorkflowNode`와 `LoopNode`의 자식 엔진도 같은 immutable dependency 묶음을 받아야 한다. 주입 객체는 execution context, graph와 task payload에 포함되지 않으며 기존 Knowledge runtime dependency를 덮어쓰지 않는다. Runtime router는 capability-required flag의 누락과 명시적 `false`만 legacy로 선택하고 null, 숫자 또는 문자열 같은 malformed 값은 fail-closed한다.
- Capability adapter가 반환한 lease는 control transaction commit과 session close 이후에만 provider를 호출할 수 있다. Capability plan은 한 번만 lease로 resolve할 수 있다. Lease는 admission 이후 caller의 중첩 request 변형을 반영하지 않고 첫 provider 호출 시도 전에 소진되어 같은 승인으로 두 번 호출할 수 없다. Legacy adapter는 resolver가 반환한 model, organization과 credential principal이 요청 scope와 다르면 invocation 전에 fail-closed한다. Usage recorder는 별도 session을 사용하고 admission attribution의 organization, credential principal과 canonical model UUID를 그대로 비용 계산과 usage projection에 전달하며 실패해도 session을 닫는다.
- Guarded provider transport가 response header/body 제한 또는 read/write 결과 불명으로 실패하면 safe `outcome_unknown` phase를 유지한다. Concrete invocation adapter는 이를 Workflow application 오류로 번역하고 LLM node는 Shared client 예외 타입에 의존하지 않는다. LLM node는 명시적 unknown도 같은 실행에서 즉시 durable unknown으로 commit하고 fallback provider를 호출하거나 Workflow task를 자동 재시도하지 않는다. Typed `before_send`는 application `provider_not_sent`로 번역되어 durable definitive failure로 commit되며 unknown으로 축소되지 않는다. Trace에는 raw response/exception 없이 safe reason과 phase만 남긴다.
- OpenAI legacy model의 Chat endpoint가 non-chat을 반환하고 Responses endpoint가 비구조화 `404/405`로 endpoint 미지원을 알린 경우에만 legacy `/completions` 전환을 허용한다. Responses-native model, structured provider 오류, 성공 응답의 malformed/empty/billable validation 실패는 legacy endpoint로 재호출하지 않는다.
- User/anonymous-public/system execution subject는 각각 동일 user/public/system audit actor와만 결합되고 organization billing principal은 capability organization과 일치해야 한다. Capability resolve 권한 거부는 이 typed actor로 `permission.denied`를 한 번 기록하며 synthetic user나 raw credential/provider payload를 남기지 않는다.
- Capability-required LLM node가 legacy `memory_mode`를 만나면 inline summary helper를 skip하고 history query 또는 legacy `get_client_for_user` provider call을 만들지 않는다. Main capability를 summary purpose로 재사용하지 않으며, dedicated Conversation Memory summarizer가 없는 상태에서 summary provider 호출을 추가하지 않는다.
- Capability-required provider usage는 canonical `(container_path, node_id)` admission → intent commit → provider-start commit → opaque lease invoke → terminal commit 순서를 지킨다. 다른 Loop의 동일 `node_id`는 exact replay로 축소하지 않는다. Intent/start commit 실패, duplicate started/succeeded/unknown delivery와 state-version conflict에서는 provider SDK 호출이 0회여야 한다.
- Timeout·connection loss·분류 불가 provider 오류, 필수 token field 누락, non-integer/negative usage, 제공된 total token 불일치, admitted token·cost 상한 초과와 response 수신 뒤 terminal 저장 실패는 outcome unknown으로 수렴하고 fallback·동일 attempt 재호출이 0회여야 한다. Provider adapter의 명시적 outcome-unknown도 reconciler를 기다리지 않고 즉시 같은 상태로 수렴하며, 전송 전 실패만 definitive `provider_not_sent`가 된다.
- 조사 완료된 `outcome_unknown` 운영 해소는 Gateway image의 no-replay CLI만 사용한다. Exact organization/operation/expected state version이 다르면 zero-write로 실패하고, success measurement는 sealed admitted token·immutable price·cost cap을 다시 통과해야 한다. Definitive resolution은 safe `provider_not_sent|provider_rejected`만 허용하며 명령은 provider client, raw request/response, credential을 입력받거나 다시 호출하지 않는다.
- Public/anonymous와 system 실행은 ledger round trip 뒤에도 execution subject/audit actor reference가 null이고 각각 public/system kind를 유지한다. Credential principal user는 compatibility projection에만 사용하며 member usage, 사용자별 Top Models 또는 audit actor로 대체되지 않는다. 사용자별 Top Models는 legacy-only usage와 explicit user execution subject의 canonical success만 중복 없이 합산한다.
- WorkflowRun이 아직 없거나 삭제된 credential/model/workflow/candidate가 있어도 canonical success와 비용은 보존된다. Compatibility projection은 nullable reference로 한 행에 수렴한다. Run이 나중에 생성되면 exact workflow와 organization이 모두 일치할 때 한 번만 연결하고, 불일치하면 terminal projection failure로 닫는다. 서로 다른 operation을 같은 run에 동시에 projection해도 fresh `FOR UPDATE` 아래 token/cost 합계가 모든 delta의 합으로 남아야 한다. Workflow 삭제는 projection의 workflow/run reference만 `SET NULL`로 만들며 ledger와 비용을 삭제하거나 삭제 자체를 막지 않는다. 필수 legacy principal이 사라진 경우에도 retry loop 대신 terminal projection failure가 된다.
- Same operation success replay, projection marker 유실 뒤 replay와 correction replay는 각각 audit outbox 한 건, compatibility usage 한 행과 최신 usage revision 하나로 수렴한다. 6자리 catalog price와 9자리 ledger round trip은 같은 canonical snapshot으로 replay되고, 반올림/precision 초과 값은 intent 전에 거부된다. PostgreSQL unique/CHECK, concurrent insert·terminal classification·same-run projection, late run 연결·삭제와 empty downgrade는 disposable DB CI에서 검증한다. Canonical operation이 하나라도 있으면 downgrade가 DDL 전에 실패하고 schema와 row가 남아야 한다.

- Credential list repository fake는 SQLAlchemy filter 조건을 실제로 적용해야 한다. `organization_id` 또는 `is_valid` predicate를 구현에서 제거하면 cross-organization/revoked fixture가 결과에 들어와 테스트가 실패해야 한다.
- Credential permission helper는 active organization과 credential id를 한 query에 적용하고, 다른 organization row는 organization/resource RBAC 평가 전에 `404`로 종료해야 한다.
## API 테스트

- `GET /api/v1/llm/credentials`는 header로 검증한 active organization의 valid row 중 `read` 가능한 credential만 반환한다. 다른 organization direct/team grant, revoked row와 권한 없는 same-organization row는 포함하지 않는다.
- `GET /api/v1/llm/credentials`의 header 누락/invalid/membership 밖 organization은 canonical `400/422/404`로 종료하며 내부 query 또는 credential 존재를 노출하지 않는다.
- Memory와 Knowledge의 raw fetch client는 credential 목록 요청에 현재 `X-Organization-Id`를 포함한다. 공용 Axios client 경로도 interceptor 계약을 유지한다.
- `POST /api/v1/llm/credentials`는 active organization manager만 성공해야 하며, 일반 member는 `403 permission.denied`로 실패해야 한다.
- `POST /api/v1/llm/credentials`는 organization scope 밖 `organization_id`를 resource hiding 정책에 따라 거부해야 하며, 성공 응답과 audit metadata에 raw API key 또는 `encrypted_config` 원문을 포함하지 않아야 한다.
- `GET /api/v1/llm/agent-answer-options`는 active organization context에서 보이고 verified 상태인 model/credential pair만 반환한다.
- `DELETE /api/v1/llm/credentials/{credential_id}` 성공 뒤 DB row는 남고 `is_valid=false`여야 한다. 기존 credential-model relation과 `llm_usage_logs`가 cascade delete되지 않으며, 이후 option/capability/provider 호출은 거부돼야 한다.
- DELETE의 legacy success message가 `deleted`를 사용하더라도 secret physical purge 완료로 해석하지 않는다. 응답, audit와 log에는 저장 secret 원문을 포함하지 않는다.
- Knowledge target flow에서 `generation_model_id`/`credential_id`가 없거나 보이지 않으면 Knowledge API gate에 따라 answer-run 생성 전에 실패한다.
- Credential `use` denial은 sanitized error/audit metadata에서 KB permission denial 및 source ACL denial과 구분된다.
- `PUT /api/v1/deployments/{deployment_id}/llm-credential-policies/{node_id}`는 active organization manager만 성공한다. Path 생략은 root로 유지하고 nested request/response는 structured `container_path`를 사용하며, response에 digest, credential principal, encrypted config, API key/token, raw capability scope를 포함하지 않는다.
- Policy endpoint는 다른 organization deployment를 `404`로 숨기고 manager denial은 `403 permission.denied`, invalid graph/relation은 safe `422`, concurrent/ambiguous selection은 safe `409`로 반환한다.

## E2E 테스트

- Standalone RAG answer explicit KB mode와 auto collection mode는 preset/default credential ADR이 승인되기 전까지 모두 명시 generation model/credential selection을 요구한다.
- Conversation Memory summary는 `inherit_node`만 허용하고 direct credential ID와 `organization_default`를 거부한다.
- Main capability를 summary purpose로 재사용하거나 summary capability를 main generation에 사용하면 provider 호출 전에 거부한다.
- Capability-required deployment LLM node는 selected model과 다른 fallback/auto-routed model로 provider SDK를 호출하지 않으며, current policy revision 또는 permission/relation/pricing/egress fingerprint가 달라지면 provider call 전에 거부한다.

## 권한 테스트

- Credential 등록 권한은 organization manager 전용이며, credential `use`/`manage` 권한은 등록 권한으로 승격되지 않는다.
- 같은 credential id에 다른 organization의 direct permission 또는 Team permission이 있어도 active organization 목록, revoke와 sync 범위를 확장하지 않는다.
- Credential read/list 권한만 있고 credential `use` 권한이 없는 사용자는 해당 credential로 Agent answer generation을 실행할 수 없다.
- 사용 가능한 credential이라도 요청 model과 verified relation이 없으면 Agent answer generation을 실행할 수 없다.
- Credential revoke/permission decision revision 변경/model relation 또는 provider-routing fingerprint 변경 뒤 stale capability는 새 Memory context claim, budget reservation, provider attempt admission과 provider 호출에 사용할 수 없다. 실제 egress policy 변경 검증은 authoritative LLM outbound guard가 연결된 뒤 해당 revision으로 대체한다.
- Knowledge physical reindex binding 후보를 0/1/2개로 만든다. Exact profile model/provider, same Organization, active credential, verified relation과 immutable job actor `use`를 모두 만족하는 후보 1개만 safe revision binding을 발급한다. 0/2개는 같은 fixed `knowledge.processing_embedding_credential_unavailable`로 provider/job write 없이 닫고 candidate count/credential identity를 노출하지 않는다. Binding 뒤 credential revoke, relation/permission/provider-routing revision 변경은 다음 provider batch와 finalization을 차단하며 existing job을 다른 actor/credential로 rebind하지 않는다.
- Policy write와 final admission은 credential, verified relation, User/Organization 상태와 현재 `use` 판정의 organization membership/direct/team permission 근거 row를 잠근 상태에서 manager/permission 및 revision을 다시 검증한다. Concurrent revoke, 사용자·조직 비활성화 또는 권한 회수가 먼저 commit되면 provider materialization이 0회이고, admission이 먼저 commit되면 해당 provider attempt만 변경보다 앞선 유효 실행으로 직렬화된다.
- Credential principal, billing principal, execution subject와 audit actor가 서로 다른 fixture에서도 credential owner가 private KB subject/public actor로 승격되지 않는다.

## Edge Case

- 여러 credential 또는 model이 있어도 name/order fallback selection을 하지 않는다.
- Default credential/preset ambiguity는 향후 ADR이 selection priority를 정의하기 전까지 gated/unsupported condition으로 반환한다. ADR-0065 Knowledge processing exact-one binding도 복수 후보를 선택하지 않고 fixed unavailable로 닫는다.
- LlamaParse processing failure response, processing metadata, audit/trace/log fixture에는 credential ID, config 원문, API key, decrypted value 또는 provider raw payload가 없어야 한다.
- Capability의 deployment version, node invocation, model, pricing revision, token/cost cap 또는 expiry 중 하나가 mismatch이면 raw secret/provider call 없이 fail-closed한다.
- Capability의 execution admission 또는 provider attempt binding을 다른 run/attempt에서 재사용하면 provider SDK 호출 전에 fail-closed한다.
- Provider call 시작 뒤 credential이 revoke된 ambiguous outcome은 자동 재호출하지 않되 이미 발생한 usage reconciliation은 같은 capability/attempt safe reference로 한 번만 처리한다.
- Plaintext backfill과 key rotation은 batch size/max-batches를 지키고 concurrent worker가 `SKIP LOCKED`로 같은 row를 중복 처리하지 않으며 재실행해도 active row를 다시 쓰지 않는다.
- Malformed row가 포함된 rotation batch는 전체 rollback되고 운영 출력에는 config, API key, ciphertext, key 또는 원본 예외가 없어야 한다.
- Gateway, Workflow Worker와 Knowledge Worker는 missing/invalid keyring, active version 누락 또는 64자를 초과하는 active version에서 시작을 거부한다. Knowledge Worker는 init container와 Celery parent/child process에 같은 keyring을 주입받고, Log System deployment에는 LLM keyring이 주입되지 않는다.
- Encrypted metadata row가 존재하면 schema downgrade는 metadata 유실 전에 fail-closed한다.

## MBA-287 보호 리소스 완료 매트릭스

| 경계 | 상태 | 공식 계약 | 구현 위치 | 실행 가능한 검증 또는 후속 |
| --- | --- | --- | --- | --- |
| Capability·principal binding | 완료 | ADR-0064, ADR-0066, ADR-0067 outbound, ADR-0069 usage | Workflow `ProviderExecutionUsageContext`, capability adapter, Shared ledger snapshot | provider execution/usage adapter와 public/system principal unit tests |
| Durable 저장·migration | 완료 | ADR-0069 canonical key/state/safe snapshot, bounded recovery index와 non-empty downgrade 금지 | `provider_usage_operations`, `provider_usage_corrections`, nullable unique usage projection migration, state/status 부분 복구 인덱스, downgrade empty-ledger guard | schema/migration unit test; disposable PostgreSQL index/unique/CHECK/upgrade/empty·non-empty downgrade test는 CI 실행 |
| Preflight와 runtime 재검증 | 완료 | ADR-0064 final admission, ADR-0066 canonical location, ADR-0069 start fence | capability resolve 뒤 ledger intent, `mark_provider_started`의 exact snapshot 재-admission | LLM node order, stale capability, binding mismatch와 provider 0-call tests |
| Session·transaction·lock 수명 | 완료 | ADR-0069 provider I/O 중 session/lock 금지와 모든 run aggregate writer 직렬화 | 각 ledger mutation의 독립 session/commit, projection과 run-finish의 fresh WorkflowRun `FOR UPDATE` | adapter session close·run lock unit tests와 disposable PostgreSQL same-run concurrency test(CI) |
| 멱등성·crash replay·outcome unknown | 완료 | ADR-0069 no-auto-retry와 exact price snapshot | operation key/state version, fixed-scale price, single-use lease, typed failure terminalization, stale-start reconciler | duplicate delivery, price round trip, typed before-send·401/403 rejection·unknown, 429, timeout, terminal write failure와 projection marker replay tests |
| Projection·canonical 비용 조회 | 완료 | ADR-0055 Agent Builder legacy usage, ADR-0069 mixed read와 incomplete signal | Shared cost read model, Gateway budget/admin/member/My Module usage, compatibility projector, workflow/subject-leading 부분 인덱스 | shared/gateway/client/schema unit tests; PostgreSQL projection/correction·same-run aggregation/index 설치 test는 CI 실행 |
| Background/retry | 완료 | bounded safe reconciliation, no-replay unknown resolution과 전역 스캔 부분 인덱스 | Log System periodic task, Gateway image 운영 reconciliation CLI, projection lease/retry/terminal failure, state/status + `(provider_started_at, id)` 부분 인덱스 | Log System task, CLI organization/CAS/measurement/redaction과 Shared batch/skip-locked/schema unit tests; PostgreSQL index 설치는 CI 실행 |
| Revoke/delete lifecycle | 완료 | control lifecycle과 historical usage 분리 | Ledger는 organization 외 control FK 없음; projector가 삭제된 optional ref를 NULL 처리 | schema FK test, deleted-reference projection test; 구체 retention/purge 기간은 Decision Required |
| Audit exactly-once·redaction | 완료 | ADR-0069 deterministic first classification과 nullable run correlation | terminal transaction의 deterministic `llm.call` Audit Outbox가 ledger `workflow_run_id`를 top-level correlation으로 전달 | audit payload/redaction/run-correlation unit test와 PostgreSQL replay cardinality test(CI) |
| 관리 API/UI | 해당 없음 | 새 ledger는 internal runtime·read-model 계약 | 신규 CRUD/API/UI 없음; 기존 admin/My Module 비용 화면에는 additive completeness만 노출 | admin/App API와 component tests |
| Query embedding purpose | 완료 | ADR-0071 exact model policy, output-zero, guarded outbound와 durable usage | query policy/capability schema, Workflow query application/projection/provider adapters, billable classifier | Shared/Gateway/Workflow unit tests와 migration static test; disposable PostgreSQL unique/race/upgrade/downgrade는 CI 실행 |
| Legacy 전체 activation | 후속 이슈 | ADR-0064 staged activation | capability-required target path만 ledger 사용, legacy recorder 병행 | MBA-320 activation/readiness gate |

## MBA-351 보호 리소스 완료 매트릭스

| 경계 | 상태 | 공식 계약 | 구현 위치 | 실행 가능한 검증 또는 후속 |
| --- | --- | --- | --- | --- |
| Query policy 관리·권한 | 완료 | ADR-0071 purpose/model slot과 manager/current `use` | Gateway deployment policy API, Shared capability policy service | policy API, purpose default, model type와 permission unit tests |
| Durable schema·migration | 완료 | ADR-0071 additive purpose/output-zero와 guarded downgrade | `llm_deployment_credential_policies`, capability/usage operation migration | ORM/static migration tests; disposable PostgreSQL slot uniqueness, concurrent write와 locked upgrade/downgrade는 CI 실행 |
| Candidate·model projection | 완료 | ADR-0036 authorized candidate 이후 model projection | `QueryEmbeddingExecutionService`, bounded PostgreSQL projection | candidate 0, missing/ambiguous/inactive model, session-close tests |
| Preflight·runtime 재검증 | 완료 | ADR-0071 exact purpose/model/revision, no fallback | capability query adapter와 Shared issue/final-admission | cross-purpose, stale revision, public/system principal과 provider 0-call tests |
| Transaction·external I/O | 완료 | ADR-0067, ADR-0069, ADR-0071 | control session close, durable intent/start, guarded typed provider client | commit ordering, unsupported provider, before-send/unknown tests |
| 멱등성·replay | 완료 | model별 stable attempt와 no-auto-retry | invocation model guard와 provider usage operation key/state | same-model reuse, duplicate delivery와 terminal state tests |
| Revoke·background recovery | 완료 | fresh credential/relation/permission/egress revision과 ADR-0069 reconciliation | Shared final admission, common Log System reconciler | revoke/stale admission unit tests; common reconciler 회귀는 기존 suite |
| Audit·redaction | 완료 | deterministic `llm.call`, query/vector/secret 비저장 | Provider usage terminal outbox와 redacted query DTO/lease | audit cardinality, safe failure와 object repr redaction tests |
| 관리 UI | 해당 없음 | 기존 manager policy API의 additive purpose 계약 | 신규 Client surface 없음 | UI 추가는 MBA-351 범위 아님 |
| 일반 runtime activation | 후속 이슈 | ADR-0071 rollout boundary | server-owned router와 default-disabled query policy write gate가 legacy/target을 분리 | MBA-320 mixed-version readiness, write/runtime activation과 rollback |

## MBA-358 보호 리소스 완료 매트릭스

| 경계 | 상태 | 계약·구현·검증 증거 | 해당 없음 또는 후속 |
| --- | --- | --- | --- |
| 정책·organization scope | 완료 | ADR-0009, ADR-0010; Gateway endpoint가 active organization을 해석하고 service/query/helper에 전달 | - |
| 관리 API query/command | 완료 | 목록은 organization+valid 선필터 후 read 평가; 등록·revoke·sync는 canonical organization 사용 | - |
| 관리 UI·picker | 완료 | 공용 Axios interceptor와 두 raw fetch hook이 `X-Organization-Id` 전달 | UI 구조 변경 없음 |
| 저장 schema·migration | 해당 없음 | 기존 `organization_id`, `is_valid` 사용 | 신규 column/index 없음 |
| Runtime/background | 해당 없음 | Workflow Engine의 동명 관리 메서드는 runtime 호출부가 없고 provider execution은 기존 capability 계약이 소유 | MBA-358은 Gateway 관리 API 격리 수정 |
| Transaction·retry·lease | 해당 없음 | read query와 동기 HTTP command의 기존 transaction 유지 | 신규 background effect 없음 |
| Revoke lifecycle | 완료 | revoke query와 권한 판정이 active organization에 고정되고 `is_valid=false` 의미 유지 | secret purge는 기존 후속 계약 |
| 오류·resource hiding | 완료 | body/header mismatch와 cross-organization credential은 `404` | - |
| Audit·redaction | 완료 | 기존 audit decorator/permission audit 유지, response schema에 secret/config/raw payload 없음 | 목록 성공 audit 추가는 계약 대상 아님 |
| 검증 | 완료 | filter-aware service test, helper predicate test, endpoint scope tests, raw fetch header tests | 실제 provider 호출/E2E는 동작 변경 대상 아님 |
| 공식 문서 | 완료 | requirements, API, component, test case 정합화 | - |
