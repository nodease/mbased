# LLM Credentials Component Spec

Status: Draft
## 화면

- Credential management/listing surface는 active organization의 credential 상태를 표시한다. 개인 사용자 credential 등록 화면을 제공하지 않는다.
- Axios `apiClient`를 우회해 credential 목록을 호출하는 Memory/Knowledge fetch hook도 저장된 active organization을 읽어 `X-Organization-Id`를 명시한다. Header가 없을 때 이전 조직이나 개인 기본 조직으로 추론하지 않는다.
- Credential 제거 action은 물리 삭제로 오해되지 않도록 revoke/사용 중지 의미와 기존 usage·audit 이력 보존을 안내한다. Current API의 legacy `deleted` message를 secret purge 완료로 표시하지 않는다.
- Credential 등록 UI는 organization manager에게만 노출한다. 일반 member, builder/operator, credential `use` 권한자에게는 등록 control을 숨기고 서버 403을 최종 경계로 둔다.
- Agent answer option surface는 실행 가능한 safe model/credential pair만 표시한다.

## 컴포넌트

- `CredentialRegistrationGate`: active organization manager 여부를 확인하고 credential 등록 control 노출을 결정한다.
- `CredentialPermissionService`: credential visibility와 `use` permission을 평가한다.
- `CredentialModelRelationResolver`: credential-model pair가 active이고 verified 상태인지 확인한다.
- `KnowledgeProcessingEmbeddingBindingIssuer`: ADR-0065 physical reindex admission에서 exact profile model/provider, Organization과 immutable job execution actor를 받아 active credential, verified relation과 current `use` 후보가 정확히 하나일 때만 safe credential/model/provider refs 및 lifecycle/relation/permission/provider-routing revision binding을 발급한다. Fresh job commit 직전 같은 revisions를 serializable하게 재검증하고 worker/finalizer에도 authoritative revalidation을 제공한다. Raw config/client를 반환하거나 0/복수 후보를 name/order/default로 선택하지 않는다.
- `LlamaParseCredentialResolver`: document parsing 직전에 execution subject, active organization, `llamaparse` provider 호환성, valid 상태와 credential `use` 권한을 확인하고 단일 허용 credential의 parser 입력만 반환한다. FileProcessor는 ORM row나 저장 config를 직접 해석하지 않는다.
- `AgentAnswerOptionProvider`: credential secret이나 전체 owner metadata를 반환하지 않고 standalone RAG answer flow용 safe option schema를 만든다.
- `CanonicalWorkflowNodeLocation`: DB와 framework를 import하지 않는 Shared pure contract다. Root/Loop structured path validation, graph traversal, exact lookup와 versioned digest를 소유하고 WorkflowNode binding, Gateway graph preflight, policy service와 Workflow Runtime이 함께 사용한다. Manager API는 structured path를 사용하지만 일반 audit·trace는 `workflow-node-location:v1:<digest>` opaque reference만 사용한다.
- `DeploymentCredentialPolicyService`: manager-only deployment policy write/read boundary다. Canonical deployment row를 lock하고 immutable deployment graph의 exact `(container_path, node_id)`, purpose에 맞는 chat/embedding model, organization-scoped credential, verified relation과 credential `use`를 server-side에서 대조한다. Main generation은 location당 하나, query embedding은 location/model당 하나의 active revisioned policy row를 만든다. Digest 조회 뒤 structured location을 다시 비교하며 graph, request body, public runtime actor가 credential principal이나 digest를 선택하지 못하게 한다.
- `ProviderExecutionCapabilityIssuer`: 이 capability 계약의 authoritative owner다. Canonical runtime scope와 admission, server-derived credential principal, credential `use` decision revision, purpose에 맞는 active model, verified relation, provider-routing/pricing revision과 실제 request token·cost upper bound를 검증해 invocation/provider-attempt에 binding된 short-lived opaque capability identity/revision을 발급한다. Query embedding은 exact policy model을 요구하고 output cap/request를 0으로 고정한다. Raw credential을 application/Memory/Knowledge에 반환하지 않으며 Workflow legacy/shared session과 분리한 전용 unit-of-work에서 provider 호출 전에 control row를 commit한다.
- `ProviderExecutionRuntime`: Workflow application port다. `LLMNode`에는 preflight, resolve와 safe attribution만 제공하고 Shared capability command, ORM row, credential config와 provider SDK client를 노출하지 않는다.
- `ProviderExecutionRuntimeRouter`: server-owned `provider_execution_capability_required` boolean control로 legacy 또는 capability strategy 하나를 선택하고 opaque plan을 같은 strategy로 되돌린다. 누락 또는 `false`만 legacy로 해석하고 malformed 값은 fail-closed한다. Shared capability domain/service와 legacy `LLMService`를 import하지 않으며 MBA-320 rollout 정책은 이 선택 경계만 교체한다.
- `CapabilityProviderExecutionAdapter`: trusted runtime control의 `binding_container_path`와 node ID를 canonical location으로 검증해 capability issue/admission command로 변환하고, 전용 session에서 authorization 재검증·config materialization·client 생성을 완료한 뒤 commit/close한다. Admission model, pricing revision·input/output 가격 snapshot과 user credential principal을 materialization 전에 재검증하고 capability plan 하나에서 lease를 한 번만 resolve한다. Plan은 발급 입력의 redaction-safe typed audit actor를 보존해 resolve 거부 시 `LLMNode`가 동일 actor로 `permission.denied`를 기록하게 한다.
- `LegacyProviderExecutionAdapter`: 기존 user-scoped credential selection과 test client override를 담당한다. Capability command나 policy를 합성하지 않고, 자체 짧은 session을 provider 호출 전에 닫는다.
- `ProviderClientInvocationLease`: 두 strategy가 공통으로 반환하는 opaque adapter 객체다. Raw SDK client를 Node에 노출하지 않고 safe attribution과 한 번의 `invoke()` 표면만 제공한다. 승인된 request payload를 생성 시점에 봉인하고 첫 호출 시도 전에 lease를 소진해 outcome-unknown 재호출을 막는다.
- `ProviderUsageRecorder`: Workflow application port다. Legacy path는 기존 response-after usage 기록을 유지하고 capability path는 admission attribution의 exact canonical `(container_path, node_id)` binding, 네 principal, canonical provider/model/credential, revision, immutable pricing과 admitted cap으로 durable attempt를 시작한다. `LLMNode`는 이 port를 통해 intent/start/outcome 순서만 조정하고 Shared ledger service/model 또는 SQLAlchemy session을 직접 알지 않는다.
- `ProviderUsageLedgerService`: organization/provider-attempt/purpose key의 intent를 멱등 예약하고 final capability를 재검증한 뒤 provider-start fence와 terminal outcome/correction/audit outbox를 각각 짧은 transaction으로 기록한다. Provider network I/O나 raw request/response를 소유하지 않는다.
- `ProviderUsageCompatibilityProjector`: succeeded canonical operation을 nullable `WorkflowRun`/`llm_usage_logs` 호환 표면으로 operation당 한 행에 수렴시킨다. 삭제된 optional live reference를 NULL로 내리고 exact workflow correlation만 나중에 연결한다. Canonical 비용과 correction revision은 ledger에서 읽는다.
- `ProviderUsageReconciler`: Log System periodic task다. Bounded `SKIP LOCKED` batch와 lease로 stale started를 provider 재호출 없이 outcome unknown으로 분류하고 pending/retryable projection을 처리한다. 한 row의 safe 실패가 같은 batch의 다른 row를 막지 않는다.
- `QueryEmbeddingExecutionService`: authorized KB ID를 bounded model projection으로 immutable binding에 투영하고 distinct model별 provider port를 한 번씩 호출해 invocation-local vector를 같은 model 후보에만 재사용한다. Candidate가 없으면 projection과 provider를 호출하지 않으며 raw query/vector와 KB ID를 durable output으로 만들지 않는다.
- `CapabilityQueryEmbeddingAdapter`: exact model의 query policy/capability를 issue/final-admit하고 control session을 닫은 뒤 ADR-0069 intent/start fence와 ADR-0067 guarded provider client를 사용한다. Provider가 actual input usage를 주지 않거나 vector/usage가 malformed이면 success를 합성하지 않고 outcome unknown으로 닫는다.
- `WorkflowRuntimeDependencies`: WorkflowEngine composition root가 provider runtime과 usage recorder를 한 번 구성해 LLM node에 process-local로 주입한다. `WorkflowNode`와 `LoopNode`는 같은 immutable dependency 묶음을 자식 엔진에 전달한다. 이 객체들은 graph, execution context 또는 Celery payload에 직렬화하지 않는다.
- `LLMCredentialConfigService`: canonical config JSON을 active key로 암호화하고 row metadata를 기준으로 encrypted/legacy read를 구분한다. active version은 공백이 아니고 `llm_credentials.encryption_key_version` 저장 길이인 최대 64자를 넘지 않아야 한다. 복호화, JSON/schema validation과 safe error normalization의 유일한 application 경계다.
- `LLM Outbound Operation Profile`: generation·embedding과 model discovery의 HTTPS/443, method, timeout, request/response/content type 상한과 revision을 소유한다. Shared guarded transport가 모든 DNS result, dial peer와 original-host TLS를 검증하고 environment proxy와 redirect를 사용하지 않는다.
- `LLMCredentialRotationService`: legacy 평문과 non-active key row를 stable order와 `FOR UPDATE SKIP LOCKED` 제한 batch로 active key에 재암호화하고 남은 대상 수만 반환한다. 운영 명령은 migration·readiness 완료 뒤 Gateway image에서 실행하며 plaintext, ciphertext 또는 key를 출력하지 않는다.
- `LLMCredentialKeyringReadiness`: Gateway, Workflow Worker와 Knowledge Worker 시작 시 keyring JSON, Fernet key와 active version을 검증한다. Celery Worker는 parent와 child process에서 모두 검증한다.
- Legacy `LLMNode` inline summary helper는 Conversation Memory summarizer component가 아니다. Capability-required LLM execution에서는 helper를 skip해 legacy `user_id`/owner credential provider call을 만들지 않으며, future summarizer만 별도 `memory_summary` capability를 consume할 수 있다.

## 상태

- credential: `valid`, `revoked/invalid`, `not_visible`, `use_denied`
- credential-model relation: `verified`, `not_verified`, `inactive`, `missing`

## 상호작용

- Active organization을 전환하면 credential 목록은 새 organization header로 다시 조회하며 이전 organization의 direct/team 권한 credential을 화면에 유지하지 않는다.
- Organization manager가 credential을 등록하면 provider key 검증과 credential-model relation sync가 수행되고, UI는 raw key를 다시 표시하지 않는다.
- Credential revoke 성공 뒤 UI는 해당 credential을 실행 가능한 option에서 제거하고 상태를 다시 조회한다. Secret physical purge가 완료됐다는 문구는 표시하지 않는다.
- 일반 member가 직접 credential 등록 endpoint를 호출하면 UI 노출 여부와 무관하게 서버가 거부해야 한다.
- Standalone RAG answer는 answer-run 생성 전에 credential/model preflight를 호출한다.
- Auto collection mode는 explicit KB mode와 같은 generation credential/model preflight를 사용한다.
- Embedding credential readiness는 generation credential selection과 별개이며 `credential_id`에서 추론하면 안 된다.
- Knowledge processing embedding binding은 profile/request credential field가 아니라 LLM Credentials-owned server port가 발급하고 durable job에 고정한다. `job_reused`는 original actor/binding을 교체하지 않으며 revoke/relation/permission revision 변경 뒤 다음 provider batch와 finalization은 fail-closed한다.
- LlamaParse parsing은 provider 호출 전에 credential resolver를 통과해야 한다. 후보 없음/복수, revoke/invalid, 권한 상실, provider 불일치 또는 context 누락은 safe reason으로 종료하며 전역/환경 변수 fallback을 사용하지 않는다.
- Main generation, Memory summary와 query embedding은 각각 purpose가 고정된 ProviderExecutionCapability를 사용한다. Summary는 `inherit_node`에서 별도 capability를 발급하고 query embedding은 exact embedding-model policy를 사용하며 둘 다 organization default/owner credential을 추론하지 않는다. Query policy management는 기본-disabled write mode를 사용하고 MBA-320이 purpose-aware process drain과 rollback 조건을 충족한 뒤에만 활성화한다.
- Capability identity/revision은 Memory lease, Budget reservation, provider attempt와 usage reconciliation에 전달한다. Credential revoke/permission revision 변경과 scope/admission/attempt/expiry mismatch는 새 claim·reservation·provider SDK 호출 전에 거부한다. Consumer는 credential principal이나 capability revision을 자체 합성하지 않는다. Provider-start 뒤의 reconciliation은 해당 snapshot을 historical 근거로만 사용하며 stale capability로 새 provider lease를 만들지 않는다.
- 신규 credential write는 active encryption version만 사용한다. Legacy read는 encryption metadata 두 값이 모두 null인 row에만 허용하며 metadata pair 불일치 또는 encrypted row decrypt 실패에는 평문 fallback을 하지 않는다.
- Rotation은 신·구키 동시 배포, active version 전환, batch 재암호화, 구키 참조 0 확인, 구키 제거 순서로 수행한다. Alembic migration은 key를 읽거나 row를 암호화하지 않는다.
- Capability-required LLM node path는 trusted Workflow Engine control과 explicit bounded cap을 가진 server runtime에서만 활성화한다. 이 path는 client override, `fallback_model_id`, automatic model routing, legacy `user_id`/owner credential selection을 사용하지 않는다. Policy가 고른 safe credential reference는 내부 client materialization에만 사용하고 component response, trace, audit에는 raw config를 전달하지 않는다.
- Capability runtime adapter는 messages와 tools·response schema 등 provider-visible parameter 구조 전체의 UTF-8 byte upper bound와 generic `max_tokens`를 admission 요청량으로 전달하고 canonical pricing으로 최대 비용을 검증한다. Request-owned `model`은 거부하고 completion count `n`은 정수 `1`만 허용해 승인된 model과 token/cost upper bound를 provider payload가 덮어쓰지 못하게 한다. Admission이 선택한 canonical model UUID는 client selection부터 비용 계산과 usage row까지 보존하고, 중복 가능한 provider API model identifier로 전역 재조회하지 않는다. Credential materialization은 `LLMCredentialConfigService`를 통과하되 client base URL은 admission에서 잠근 provider catalog 값을 사용하고 LLM operation profile revision을 egress fingerprint에 포함한다. 전용 capability transaction은 provider network I/O 전에 commit하며 Legacy/shared Workflow session은 이 경계에 전달하지 않는다.
- `LLMNode`는 main generation runtime preflight를 prompt와 외부 I/O보다 먼저 호출한다. Query embedding은 권한 기반 candidate resolution이 0개인지 먼저 확인하고, 후보가 있을 때만 query runtime preflight와 bounded model projection을 수행한다. Capability-required RAG의 auto routing, fallback, client override 또는 필수 control 누락은 provider I/O 전에 거부한다. Query와 prompt가 완성된 뒤 adapter가 sealed invocation을 final-admit하고 control session을 닫은 다음에만 durable provider-start fence와 network I/O를 수행한다.
- Policy write는 canonical deployment와 active policy를 먼저 잠근 뒤 model/provider/credential/relation, User/Organization 상태와 membership/direct/team 권한 근거를 잠가 manager 및 credential `use`를 최종 재검증한다. Final capability admission도 policy와 capability row 뒤 같은 authorization 근거를 control transaction commit까지 잠근다. 먼저 commit된 admission만 이후 revoke·비활성화·relation/permission 변경보다 앞선 유효 실행으로 취급하며, 변경이 먼저 commit되면 revision 검증에서 provider materialization 전에 거부한다.

## 접근성

- Credential/model option error는 text label을 가져야 하며 색상만으로 상태를 전달하지 않는다.
