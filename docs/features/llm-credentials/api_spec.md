# LLM Credentials API Spec

Status: Draft
## Endpoints

| Method | Path | 설명 | 권한 |
| --- | --- | --- | --- |
| GET | `/api/v1/llm/agent-answer-options` | RAG Agent answer generation에 사용할 수 있는 safe model/credential pair를 반환한다 | Active organization scope, credential `use`, verified credential-model relation |
| GET | `/api/v1/llm/my-models` | 현재 model listing surface | 현재 동작 |
| GET | `/api/v1/llm/credentials` | active organization에서 읽을 수 있는 valid credential 목록을 반환한다 | Active organization scope, credential `read` |
| POST | `/api/v1/llm/credentials` | active organization에 LLM credential을 등록하고 provider 모델 relation을 동기화한다 | Organization manager only |
| DELETE | `/api/v1/llm/credentials/{credential_id}` | active organization의 LLM credential을 revoke한다. Current 구현은 `is_valid=false`이며 row를 hard delete하지 않는다 | Organization manager 또는 credential `manage` |
| POST | `/api/v1/llm/credentials/{credential_id}/sync-models` | active organization credential의 provider model relation을 재동기화한다 | Credential `manage`/`write` |
| GET | `/api/v1/deployments/{deployment_id}/llm-credential-policies` | immutable deployment version의 active LLM credential policy를 safe projection으로 조회한다 | Active organization manager only |
| PUT | `/api/v1/deployments/{deployment_id}/llm-credential-policies/{node_id}` | immutable deployment LLM node의 purpose/model별 server-owned credential policy revision을 생성한다. Purpose 생략은 main generation이다 | Active organization manager only |

## 요청과 응답 모델

### Credential Listing And Active Organization

`GET /api/v1/llm/credentials`는 `X-Organization-Id`를 필수 active organization context로 사용한다. Gateway는 header UUID와 active membership을 검증하고, service에는 이 canonical organization id를 명시적으로 전달한다.

Database 후보는 `(organization_id = active organization, is_valid = true)`로 먼저 제한한다. 그 뒤 같은 organization 안에서 credential `read` 권한을 평가한다. 다른 organization에 대한 direct permission 또는 Team permission이 있어도 현재 목록에 합쳐지지 않으며, revoked credential도 반환하지 않는다. 응답은 `LLMCredentialResponse`의 safe projection만 사용하고 raw API key, `encrypted_config` 또는 provider raw payload를 포함하지 않는다.

Header 누락은 `400 organization.required`, 잘못된 UUID는 `422 validation.failed`, active membership 밖 organization은 `404 resource.not_found`로 처리한다.
### Credential Registration

Credential registration은 개인 사용자 credential 생성 API가 아니다. 요청은 active organization context에서 처리되며, 요청자는 해당 organization manager여야 한다. 일반 member는 credential `use` 또는 기존 credential `manage` 권한을 갖고 있어도 새 credential을 등록할 수 없다.

Request:

- `provider_id`
- `organization_id`: optional compatibility field다. 값이 있으면 header 기반 active organization과 반드시 일치해야 한다. 저장 대상은 body가 아니라 server-validated active organization이며 default organization fallback을 사용하지 않는다.
- `credential_name`
- `api_key`: raw secret. 저장 직후 응답, audit, trace, usage metadata에 원문을 반환하지 않는다.

Response는 `LLMCredentialResponse`를 사용할 수 있지만, `encrypted_config`, raw `api_key`, provider raw response는 포함하지 않는다. `user_id`가 포함되는 구현에서는 등록 행위자 reference로만 해석하고 개인 credential 소유권으로 표시하지 않는다.

### Credential Revocation

현재 `DELETE /api/v1/llm/credentials/{credential_id}`는 이름과 legacy 성공 message에 `delete/deleted`를 사용하지만 의미는 revoke다. DELETE와 model sync는 모두 server-validated active organization 안에서 credential을 먼저 찾고 다른 organization id는 `404`로 숨긴다. Service는 `llm_credentials.is_valid=false`만 commit하며 credential row, credential-model relation, 기존 `llm_usage_logs`와 저장된 secret material을 삭제하지 않는다. Revoke 뒤 신규 option 선택, capability 발급과 provider 호출은 fail-closed해야 한다.

Secret physical purge 또는 crypto-shred는 이 endpoint의 현재 계약이 아니다. 이를 추가할 때는 historical usage/audit 보존, FK nullability 또는 tombstone/snapshot, retention/legal-hold와 실패 복구를 함께 정의해야 하며, 단순 hard delete로 현재 DELETE의 의미를 바꾸지 않는다.

### Deployment Credential Policy Management

이 API는 graph에 credential ID를 저장하지 않고, 배포된 LLM node의 provider credential 선택을 server-owned row로 분리한다. 두 endpoint 모두 `X-Organization-Id`의 active organization과 대상 deployment의 App/Workflow organization을 다시 대조한다.

`PUT` request:

- `model_id`: LLM catalog UUID
- `credential_id`: active organization의 LLM credential UUID
- `purpose`: optional `main_generation|query_embedding`. 생략하면 `main_generation`이다.
- `container_path`: optional ordered Loop segment 배열. 생략 또는 `[]`는 root이며 각 entry는 `{kind: "loop", node_id: <1~255자>}`다. 최대 깊이는 16이다.

Path의 terminal `node_id`와 각 parent Loop ID는 1~255자로 제한한다. Server는 `container_path + node_id`를 immutable graph에서 exact lookup하며, 초과·unknown kind·잘못된 순서·missing location이면 row를 쓰기 전에 `422 configuration_required`로 거부한다. Client가 `node_location_digest`, credential principal 또는 capability scope를 보내면 strict request schema가 거부한다.

`credential_principal_user_id`, credential config, API key, provider request option은 request에 포함할 수 없다. Server는 policy write actor가 organization manager인지 확인하고 그 actor를 credential principal로 server-side 파생한다. Main generation은 immutable graph의 exact `llmNode.model_id`와 active chat model을 검증한다. Query embedding은 같은 node에 Knowledge Base 또는 Collection 설정이 있고 request model이 active embedding model인지 검증한다. 두 purpose 모두 selected model/provider, credential valid state, same-organization scope, verified credential-model relation과 credential `use`를 확인한다.

Capability-required policy는 direct `credential_id`/`credentialId` graph field, `fallback_model_id`, `auto_model_routing`을 허용하지 않는다. 이들은 현재 target policy의 명시 model/credential binding을 흐리므로 `422 configuration_required`로 fail-closed한다.

Main generation의 같은 `(organization, deployment, deployment_version, container_path, node_id)` slot을 교체하면 기존 main row만 비활성화한다. Query embedding은 여기에 `model_id`를 더한 slot을 교체하므로 다른 embedding model과 main policy는 유지한다. 새 row는 `policy_revision`을 증가시키며 다른 container의 동일 `node_id`는 별도 policy다. GET은 현재 deployment version의 active row만 반환한다. Response에는 `id`, `deployment_id`, `deployment_version`, canonical `container_path`, `node_id`, `purpose`, `model_id`, `credential_id`, `policy_revision`, `is_active`, timestamps만 포함하며 digest, credential principal, encrypted config, API key/token, raw capability scope는 포함하지 않는다.

오류는 `404 Deployment not found`(다른 organization 포함 resource hiding), `403 permission.denied`, `409 selection_ambiguous`, `422 configuration_required|relation_unavailable`의 safe code로 제한한다.

### Agent Answer Option

Auto collection mode를 포함한 target Agent answer flow는 별도 ADR이 preset/default selection을 추가하기 전까지 명시 `generation_model_id`와 `credential_id`를 요구한다.

Option response는 전체 credential read schema가 아니라 실행 선택을 위한 safe schema다. 포함할 수 있는 값은 다음과 같다.

- `model_id`, `model_name`, provider/model display field, model type, context window
- `credential_id`, `credential_name`, provider id/name, `config_preview`, `is_valid`
- 진단에 필요한 경우 verified relation id 또는 relation status

별도 API 계약이 명시적으로 허용하지 않는 한 credential value, encrypted config, API key/token, selection에 필요 없는 raw quota detail, raw timestamp, owner/user metadata를 포함하지 않는다.

## 오류

- LLM provider/model/credential 조회 중 예상하지 못한 DB 또는 내부 오류가 발생하면 `500`으로 실패하되 SQL, table/column 이름, credential config 또는 내부 예외 문자열을 응답에 포함하지 않는다. 서버 로그에는 operation과 예외 type 같은 안전한 진단 정보만 남긴다.
- Credential 등록 요청자가 target organization manager가 아니면 `403 permission.denied`로 실패해야 한다.
- Organization scope 밖 `organization_id` 또는 credential id는 resource hiding 정책에 따라 `404 resource.not_found`로 숨긴다.
- Credential DELETE 성공은 revoke를 의미한다. 이미 기록된 usage/audit를 cascade delete하거나 secret이 물리 삭제됐다고 응답해서는 안 된다.
- Knowledge target flow에서 generation model/credential이 없거나 보이지 않으면 answer run 생성 전에 실패한다.
- Credential `use` denial은 KB permission 및 source ACL authorization과 독립된 permission failure다.
- Verified credential-model relation이 없으면 fail-closed로 처리하며, client는 fallback model/credential selection을 추론하면 안 된다.
- Default credential/preset ambiguity는 향후 ADR/API 계약이 정의하기 전까지 이 API가 처리하지 않는다.

## Target Provider Execution Capability Contract

이 contract는 Workflow/Conversation Memory composition이 호출하는 internal application port이며 raw credential을 반환하는 public HTTP endpoint가 아니다.

입력:

- canonical organization/workflow/deployment ID와 immutable version 또는 snapshot hash
- node/invocation reference와 Workflow가 승인한 execution admission reference
- 해당 invocation에서 미리 생성한 server-issued provider attempt reference
- execution subject 또는 public audience
- `purpose=main_generation | memory_summary | query_embedding`
- server-owned input/output token과 cost ceiling

Workflow Runtime은 provider SDK 호출 전에 attempt reference를 먼저 생성하되 provider effect를 시작하지 않는다. Issuer는 이 canonical attempt를 다른 invocation/admission에 재사용할 수 없는지 검증한 뒤 capability를 발급한다.

출력 opaque capability:

- opaque capability identity/revision
- provider/model/credential safe reference와 verified relation revision
- server-derived credential principal safe reference와 credential permission decision revision
- provider-routing fingerprint와 pricing revision
- approved token/cost cap, purpose와 expiry
- node invocation, execution admission과 server-issued provider attempt binding

Memory summary 초기 정책은 `inherit_node`만 허용한다. Main node의 approved scope에서 별도 `memory_summary` capability를 발급하며 direct credential ID, name/order fallback과 `organization_default`를 거부한다. Capability identity/revision은 client에 해석 가능한 scope를 노출하지 않는 opaque reference다. Credential revoke, credential permission decision revision 변경, model relation/provider-routing/pricing revision mismatch, wrong deployment/node/invocation/admission/provider-attempt/purpose 또는 expiry는 새 context claim·budget reservation·provider attempt admission·provider call 전에 fail-closed한다. 이미 시작된 provider attempt의 normalized usage reconciliation은 새 outbound call 권한과 분리한다. `outcome_unknown` 해소는 public HTTP API가 아니라 platform IAM으로 제한한 no-replay 운영 명령이며 exact organization/operation/state version과 safe measurement 또는 definitive reason만 받는다. Capability, credential principal과 public Access Grant는 execution subject나 audit actor가 아니다.

현재 구현에서 capability issue/admission은 public HTTP endpoint가 아니라 `provider_execution_capability_required=true`인 server-owned runtime context만 사용할 수 있는 internal application port다. 이 mode는 trusted Workflow Engine execution control, canonical deployment/version/node invocation, explicit user/anonymous-public/system execution identity, organization billing principal, bounded token/cost cap을 요구한다. Capability path에서는 legacy `user_id`, `credential_principal`, App/deployment owner, fallback model, name/order/default candidate를 credential selection에 사용하지 않는다. Durable usage intent/start/outcome과 compatibility projection도 public HTTP API를 추가하지 않고 Workflow application port와 Log System reconciler가 처리한다. Gateway/deployed task composition이 flag를 주입하지 않으므로 policy API 설정만으로 live provider selection이 전환되지 않으며 Existing legacy runtime의 activation/migration은 MBA-320 범위다.

Admission은 messages와 tools·response schema를 포함한 provider-visible parameter 구조 전체의 UTF-8 byte 길이를 provider-independent input token upper bound로 사용하고, provider request의 generic `max_tokens`를 output 요청량으로 사용한다. `n`과 `best_of`는 boolean/string을 포함해 정확한 정수 `1`이 아니면 거부하며 `max_tokens`가 없으면 server cap을 request limit으로 적용한다. Canonical model input/output price로 최대 비용을 micro-USD 올림 계산하며 missing pricing, provider-specific output-limit alias, 직렬화 불가 값, 다중 생성 parameter, 음수·boolean·상한 초과 요청은 provider client 생성 전에 `capability_stale|configuration_required`로 닫는다. Provider client가 provider-specific JSON schema format을 만든 경우 adapter는 완성된 parameters로 같은 capability를 다시 admission하고, 실패한 lease는 usage intent 또는 provider I/O에 사용할 수 없다. Admission이 확정한 canonical model UUID와 immutable input/output 가격 snapshot은 provider 호출 뒤 비용 계산과 usage 기록까지 그대로 전달하며, provider API model identifier의 전역 첫 row를 다시 선택하지 않는다. Capability와 admission row는 Shared config 복호화와 provider client materialization이 성공한 뒤 network 호출 전에 commit한다.

Policy write와 capability issue/admission에서 잠근 ORM row는 현재 Session에 이미 존재해도 강제로 재적재한 뒤 manager/use 권한, active/revoked 상태와 revision을 다시 판정한다. Capability의 `expires_at`은 발급 시점 PostgreSQL wall clock + TTL로 계산하고 admission도 lock 이후 같은 DB wall clock으로 최종 만료를 판정한다.

`egress_revision`은 현재 provider catalog routing field의 변경 fingerprint이며 capability client의 실제 base URL도 admission에서 잠근 provider catalog 값을 사용한다. Credential config의 과거 base URL snapshot은 이 목적지를 덮어쓰지 않는다. 이 revision은 URL 허용이나 중앙 outbound authorization capability가 아니며, credential config 변경은 relation revision으로 stale 처리하고 authoritative LLM egress policy/guard 연결은 별도 egress 범위가 소유한다.

현재 legacy LLM node의 inline memory summary는 Conversation Session/Access Grant/lease와 source authorization 재검증을 거치지 않으므로 capability-required path에서 실행하지 않는다. 이 경로는 `memory_summary` capability를 main generation capability로 바꾸거나 legacy user/owner credential fallback으로 호출하지 않고 summary를 생략한다. 별도 Conversation Memory summarizer가 lifecycle·budget·usage 계약을 갖춘 뒤에만 `inherit_node` 정책의 distinct `memory_summary` capability를 provider call에 소비한다.

Target LLM node RAG query embedding은 authorized 후보의 distinct canonical embedding model마다 `query_embedding` capability와 durable provider usage operation을 하나 사용한다. Query provider client는 exact query payload를 single-use invocation으로 봉인하고 actual input usage와 finite vector를 typed result로 반환해야 한다. Output cap/admission/usage는 `0`이다. Candidate가 없으면 policy/capability/usage/provider를 호출하지 않으며 missing/stale policy, credential 권한·relation, pricing 또는 egress 경계는 main generation capability나 owner/user fallback 없이 `configuration_required`로 닫는다. 이 internal port와 schema 구현은 live target activation을 뜻하지 않으며 전면 전환은 MBA-320이 소유한다.

## 권한

- Credential 등록은 organization manager 전용이다. Credential `use`, credential `manage`, workflow manager, builder/operator 권한은 새 credential 등록 권한을 부여하지 않는다.
- 등록된 credential은 active organization scope에 속한다. 개인 사용자 credential scope를 만들지 않는다.
- Credential 목록은 active organization의 valid row만 DB에서 먼저 제한한 뒤 `read` 권한을 적용한다. Cross-organization direct/team grant는 현재 목록을 확장하지 않는다.
- Agent answer generation preflight는 active organization scope, model visibility, credential visibility, credential `use`, verified credential-model relation을 검증해야 한다.
- UI option API는 현재 active organization context에서 실행 가능한 safe pair만 보여줄 수 있다.
- Credential 존재 또는 사용 가능 상태는 KB content permission, collection routing permission, source ACL authorization을 부여하지 않는다.
- ProviderExecutionCapability는 credential secret을 포함하거나 client/API response에 노출되지 않는다. Consumer는 safe opaque reference/revision만 전달한다.
