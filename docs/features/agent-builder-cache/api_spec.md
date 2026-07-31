# Agent Builder Cache API Specification

Status: Draft

MBA-343에서 최초 구현하는 cache spine의 field-level strict schema와 disabled execution contract는
[MBA-343 internal API](../agent-builder-cache-343/api_spec.md)가 이 문서를 구체화한다. Admission,
rehydration과 serving 동작은 이 문서가 계속 소유한다.

## External API Boundary

이 기능은 public HTTP endpoint를 추가하거나 기존 Agent Builder message response를 변경하지 않는다.
Cache hit와 miss는 client에 동일한 structured request, GraphMutation, Knowledge resolution,
parameter group과 error contract를 반환한다. Cache control은 Gateway 내부 설정과 application
port로만 제공한다.

## Internal Planning Request

`AgentBuilderService`는 다음 server-derived context를 transient planning DTO로 만들어 cache coordinator에
전달한다. 이 DTO는 request 처리 중에만 존재하며 DB, Redis value, audit, trace와 log에 저장하지 않는다.

| Field | Contract |
|---|---|
| `full_safe_message` | secret sanitizer를 통과한 전체 현재 요청. 저장하거나 log하지 않음. Planner prompt용 truncated summary와 분리 |
| `organization_id` | active organization scope. HMAC key material에만 사용 |
| `user_id` | 현재 actor scope. HMAC key material에만 사용 |
| `planner_runtime` | 검증된 provider/model identity와 credential relation fingerprint |
| `generation_mode` | 현재 request의 canonical generation mode |
| `workflow_context` | 허용된 node type, safe label/role, 전체 logical topology와 selection projection |
| `selected_target_scope` | 명시적 selected node/edge의 type과 실제 identity. Non-serializable이며 HMAC key 생성에만 사용하고 즉시 폐기 |
| `knowledge_context_fingerprint` | 현재 권한·lifecycle을 통과한 후보의 canonical digest. 후보 원문은 포함하지 않음 |
| `contract_versions` | normalizer/cache/planner/catalog/`canonical_text_registry_version`/materializer version |

Raw graph, credential config, parameter value와 hidden Knowledge metadata를 coordinator input으로
전달하지 않는다. `full_safe_message`와 workflow topology projection은 중간 절단을 허용하지 않는다.
전체 canonical input을 만들 수 없으면 normalization 결과는 `bypass`다.
Workflow/node/edge/Knowledge resource ID, position, request/operation ID도 직렬화 가능한 projection에서
제외한다. 예외는 명시적인 selected target identity와 Knowledge candidate fingerprint를 만드는 resource
identity다. 이 identity는 각 domain-separated HMAC builder의 non-serializable 입력으로만 transient하게
사용하고 즉시 폐기한다. Cache payload와 diagnostics에는 최종 digest만 포함하며 Planner prompt용 2,000자
summary와 50-node projection은 이 DTO의 입력이 아니다.

## Knowledge Candidate Fingerprint

1. Active organization, Collection route, KB use 권한과 lifecycle을 통과한 후보만 입력으로 받는다.
2. Collection과 KB를 실제 resource identity로 중복 제거한다. 같은 KB가 여러 Collection에 속해도 한 번만
   identity 집합에 포함하되 허용된 hierarchy 관계는 모두 보존한다.
3. 실제 identity는 `agent-builder-cache:knowledge-resource:<version>` domain의 HMAC으로 즉시 변환한다.
4. Resource kind, identity digest, safe metadata digest, hierarchy digest, lifecycle state와 policy revision으로
   canonical projection을 만든다. Safe label이나 이름 원문은 projection에 넣지 않는다.
5. Projection은 identity digest와 resource kind를 기준으로 안정 정렬하고 최종 candidate-set digest를 만든다.
6. UI 위치, 추천 순위, score와 request-scoped selection/collection/KB handle은 입력에서 제외한다.
7. Planning context와 cache key material에는 최종 digest만 전달한다. Fingerprint builder의 입력과 중간
   projection은 serialization, logging, metric label과 audit 대상이 아니다.

## Normalization Result

| Field | Type | Description |
|---|---|---|
| `status` | `eligible \| bypass` | deterministic lookup 가능 여부 |
| `signature` | string or null | eligible일 때만 존재하는 canonical signature |
| `reason_code` | safe enum or null | bypass 이유 |
| `normalizer_version` | string | key namespace version |
| `sensitive_input_detected` | boolean | true이면 lookup/store 금지 |

Allowed bypass reason codes:

- `sensitive_input`
- `explicit_value_suspected`
- `unknown_token_sequence`
- `ambiguous_target`
- `unsupported_request_shape`
- `normalizer_disabled`
- `input_projection_truncated`
- `graph_projection_incomplete`

Reason에는 사용자 문자열을 포함하지 않는다.

## Versioned Normalization Profile

Normalizer eligibility는 코드에 고정된 versioned profile로 판정한다. Profile은 다음을 포함한다.

- Catalog capability와 server action의 canonical alias 및 token/phrase boundary
- 허용되는 의미 없는 조사, 정중 표현과 공백·case 변형
- `앞에`, `뒤에`, `사이에` 같은 위치 표현과 단계 연결 표현
- 인용문, 숫자, 부정 표현, node label과 parameter-like span을 alias보다 먼저 보호하는 우선순위
- 처리 뒤 미인식 잔여 표현이 하나라도 남으면 전체 요청을 bypass하는 규칙

Profile은 UI 문구나 Planner prompt에서 동적으로 파생하지 않는다. Alias canonical target 중복과 부분 문자열
충돌은 startup/static validation으로 거부한다. Profile 또는 corpus 변경은 `normalizer_version`을 올려 기존
entry를 namespace miss로 만들고, 동등·비동등·부정·순서·인용·explicit value·충돌 사례를 포함한 versioned
regression corpus를 함께 갱신한다.

## Cache Key Material

Canonical JSON field order는 schema로 고정하며, 최종 Redis key는 다음 형식이다.

```text
agent-builder:intent-plan:<key-version>:<hmac-sha256>
```

HMAC input은 다음 필드만 포함한다.

| Field | Notes |
|---|---|
| `organization_scope` | UUID를 canonical string으로 사용하되 Redis key에는 노출하지 않음 |
| `actor_scope` | 초기 버전은 user UUID |
| `planner_runtime_fingerprint` | provider/model과 검증 관계의 digest |
| `generation_mode` | canonical mode |
| `intent_signature` | deterministic signature |
| `workflow_context_fingerprint` | layout과 raw config를 제외한 safe structural digest |
| `selection_fingerprint` | selected node/edge type과 실제 target identity를 ephemeral HMAC input으로 만든 digest |
| `knowledge_context_fingerprint` | 현재 표시 가능한 후보 집합과 policy revision digest |
| `versions` | normalizer/cache/planner/catalog/`canonical_text_registry_version`/materializer/HMAC key version |

Timestamp, request/session ID와 workflow ID는 key material에 넣지 않는다. Workflow identity 대신
현재 의미 구조 fingerprint를 사용한다.

Key HMAC은 `agent-builder-cache:key:<version>` domain을 사용한다. 같은 key material을 다른 HMAC
용도에 재사용하지 않는다.

## CachedIntentPlan

`CachedIntentPlan`은 strict, versioned internal schema다.

| Field | Description |
|---|---|
| `schema_version` | cache value version |
| `request_type` | cache admission을 통과한 actionable request type |
| `draft_mode` | current generation/edit mode |
| `ordered_capabilities` | Catalog capability ID의 순서 있는 배열 |
| `logical_steps` | request-local UUID가 아닌 deterministic logical reference |
| `edit_placement` | selected target 기반 typed placement. 실제 node/edge ID 없음 |
| `integration_actions` | allowlisted provider/resource/action enum |
| `parameter_guidance_refs` | logical step ref, Catalog `parameter_key`, closed `reason_template_ref`와 `input_guidance_template_ref`의 순서 있는 배열 |
| `knowledge_requirements` | `required`, evidence kind, logical target ref와 순서 있는 closed `topic_refs`. 자유 형식 topic 없음 |
| `knowledge_placements` | before/after graph typed placement와 logical refs |
| `risk_flags` | allowlisted enum only |
| `contract_versions` | 저장 당시 contract versions |

다음 field가 발견되면 decode를 거부하고 miss 처리한다.

- graph/node/edge/workflow UUID
- GraphMutation operation ID
- request/session/draft ID
- candidate/collection/KB handle 또는 ID
- credential ID/config
- parameter value
- raw prompt/summary, rendered topic/guidance, 자유 형식 template argument 또는 provider payload
- raw audit payload 또는 audit metadata 원문
- URL, path와 secret-like key

## Authenticated Cache Envelope

Redis value는 `CachedIntentPlan`만 직렬화하지 않고 다음 envelope로 저장한다.

| Field | Contract |
|---|---|
| `envelope_version` | strict envelope schema version |
| `payload` | canonical JSON으로 직렬화한 `CachedIntentPlan` |
| `payload_mac` | 현재 Redis key digest와 canonical payload를 묶은 HMAC-SHA256 |

`payload_mac`은 `agent-builder-cache:value:<version>` domain, Redis key digest와 canonical payload를
순서대로 포함한다. Decode 뒤 MAC을 constant-time으로 확인한 다음에만 payload schema를 사용한다.
다른 key에서 옮긴 valid envelope, payload 한 byte 변조와 이전 key version envelope는 invalid miss로
처리하고 best-effort로 삭제한다. Key HMAC과 value HMAC은 같은 secret을 사용할 수 있지만 반드시
domain separation을 적용한다.

## Canonical Topic, Guidance, Summary and Purpose Contracts

`CachedIntentPlan`은 topic/guidance 문자열 대신 다음 safe reference를 사용한다.

| Reference | Fields | Rehydrated target |
|---|---|---|
| `CanonicalKnowledgeTopicRef` | closed `topic_ref` | registry의 고정 canonical string 한 개 |
| `CachedParameterGuidanceRef` | `logical_step_ref`, Catalog `parameter_key`, closed `reason_template_ref`, closed `input_guidance_template_ref` | `AgentBuilderParameterGuidanceHint` |

Reference namespace는 `topic.<slug>.v1`, `guidance.reason.<slug>.v1`,
`guidance.input.<slug>.v1` 형식이다. 형식 일치만으로 ref를 허용하지 않고
`intent_cache/contracts.py`의
`CachedIntentPlan` closed enum membership을 codec에서 검사한다. Registry manifest는 이 enum 집합을
빠짐없이 한 번씩 구현해야 한다. `contract_versions.canonical_text_registry_version`은 manifest의
`registry_version`과 같아야 한다.

Canonical text registry는 server-owned 정적 table이며 Planner prompt, provider 응답 또는 UI 문구에서
동적으로 만들지 않는다. 각 `topic_ref`는 고정 `query_topic` 문자열 하나를, 각 guidance template ref는
고정 template과 허용 가능한 Catalog `input_type` 집합을 소유한다. Template은 현재 Catalog의
`parameter_key`, safe label과 input type만 사용할 수 있으며 자유 형식 argument를 받지 않는다.
같은 registry version은 current `IntentPlanningContext.full_safe_message`를 사용하는 request-specific
summary projection descriptor와 capability별 canonical step purpose도 소유한다. Exact `intent-text-v1`
summary projection/purpose contract는
[MBA-343 internal API](../agent-builder-cache-343/api_spec.md)의 contract snapshot을 따른다.
Registry manifest는 다음 strict field만 가진다.

| Entry | Required fields |
|---|---|
| manifest | `registry_version`, `summary_projection`, ordered `topics`, ordered `reason_templates`, ordered `input_guidance_templates` |
| topic | `ref`, `canonical_text`, ordered explicit `aliases` |
| guidance template | `ref`, `canonical_template`, ordered explicit `aliases`, ordered `allowed_input_types` |
| summary projection | `projection_id=summary.current_safe_message.v1`, `source=full_safe_message`, `max_codepoints=240`, `whitespace_profile=python-split-v1`, `redaction_profile=agent-builder-safe-summary-v1` |
| capability purpose | exact Catalog capability, `canonical_text` |

Closed enum 대비 누락·초과 ref, alias와 canonical text 중복, 지원하지 않는 placeholder, alias의 다중 ref
매핑과 빈 `allowed_input_types`는 startup/static validation 실패다. Registry manifest 또는 enum 변경은
`canonical_text_registry_version`을 올리고 namespace miss와 registry regression fixture 갱신을 요구한다.

Projection은 NFKC, 공백과 case 정규화 뒤 명시적으로 등록된 exact alias만 canonical ref로 바꾼다.
Embedding, fuzzy match 또는 의미 유사도는 사용하지 않는다. Provider가 반환한 topic 순서는 유지하고
동일 ref는 첫 occurrence만 남긴다. Guidance는 `(logical_step_ref, parameter_key)` 순서를 유지한다.
다음 중 하나이면 final extraction 전체가 store-ineligible이다.

- `knowledge_required=true`인데 provider topic이 비어 있거나 모두 순서 있는 `topic_refs`로 표현되지 않음
- guidance의 `reason` 또는 `input_guidance` 중 하나라도 등록된 template ref로 표현할 수 없음
- guidance target이 logical step 또는 현재 Catalog parameter와 정확히 일치하지 않음

Store-ineligible은 cold-miss rehydration failure가 아니다. Planner가 이미 만든 원본 extraction을 기존
non-cache downstream에 전달하고 cache put을 수행하지 않는다. Projection이 성공한 경우에는 cold miss와
warm hit 모두 registry version과 현재 Catalog applicability를 검증한 뒤 같은 canonical 문자열을 렌더링한다.
Unknown ref, registry version mismatch 또는 current Catalog incompatibility는 valid plan으로 보정하지 않는다.
Registry version 변경은 namespace miss를 만들며 old entry를 migrate하지 않는다.

### Request-specific `intent_summary`

Cache-eligible request의 `intent_summary`는 cache value나 provider summary에서 복원하지 않는다. Cold miss와
warm hit 모두 현재 요청의 transient `IntentPlanningContext.full_safe_message`를 다음
`summary.current_safe_message.v1` contract로 projection한다.

1. Python `" ".join(value.split())`과 같은 규칙으로 Unicode whitespace run을 ASCII space 하나로 합치고
   양 끝 whitespace를 제거한다.
2. 현재 Agent Builder `_safe_summary`가 사용하는 fail-closed trace redaction, auth/secret/URL/path pattern과
   `[REDACTED]` → `[redacted]` canonical marker 규칙을 `agent-builder-safe-summary-v1` regression corpus로
   고정한다.
3. Redaction 뒤 첫 240 Unicode code point만 사용한다.
4. 결과가 비어 있거나 `[redacted]` marker를 포함하면 cache admission 또는 rehydration을 fail-closed한다.

Provider가 반환한 자유 형식 `intent_summary`는 projection 전 validation과 non-cache fallback에는 남아 있지만,
cache-safe plan projection에 성공한 cold miss의 downstream structured request에는 사용하지 않는다. Rehydrator는
plan의 request/draft pair로 공통 summary를 선택하지 않으며, 같은 safe request에는 같은 summary를 만들고
서로 다른 safe request는 각 current context에서 독립적으로 summary를 만든다. Projection descriptor와
redaction corpus가 바뀌면 `canonical_text_registry_version`을 올려 namespace miss를 만들어야 한다. Summary
문자열, `full_safe_message`와 provider summary는 cache value, key plaintext, diagnostic, metric과 audit에
저장하지 않는다.

## Cache Port

Application layer는 concrete Redis 타입 대신 다음 의미 계약을 사용한다.

| Operation | Result | Failure behavior |
|---|---|---|
| `get(key)` | hit, miss, invalid 또는 unavailable | invalid는 best-effort delete 후 miss, unavailable은 miss |
| `put(key, plan, ttl)` | stored 또는 unavailable | 현재 request 성공에는 영향 없음 |
| `acquire_lease(key, owner, ttl)` | acquired 또는 contended(observed lease generation)/unavailable | unavailable이면 single-flight 없이 진행 |
| `complete_without_value(key, owner, lease_generation, ttl)` | signaled_and_released, ignored 또는 unavailable | owner/generation이 일치할 때만 짧은 완료 신호와 release를 원자적으로 수행 |
| `wait_for_value(key, lease_generation, deadline, cancellation_fence)` | hit, owner_completed_without_value, canceled, stale, timeout 또는 unavailable | 관찰한 generation과 일치하는 완료 신호만 사용하고 짧은 bounded slice마다 fence 확인 |
| `release_lease(key, owner)` | released 또는 ignored | owner token이 일치할 때만 release |

MBA-343 spine의 typed `load/save` subset은 위 `get/put` 상태를 손실 없이 표현한다. Load result는
`hit|miss|invalid|unavailable`, save result는 `stored|unavailable`을 closed DTO로 반환한다. `None`, boolean 또는
자유 형식 exception으로 상태를 합치지 않는다. Lease와 waiter operation은 후속 adapter/concurrency 범위다.

Value put과 lease release는 원자성 순서를 보장해야 한다. Follower가 partial payload를 읽을 수
없도록 완성된 serialized value를 한 번에 기록한다.
Owner는 valid value 저장 여부와 관계없이 모든 정상 terminal path에서 lease를 해제한다. Cache에 저장할 수
없는 정상 결과는 관찰된 lease generation과 묶인 `owner_completed_without_value` 완료 신호를 게시한 뒤
release하며 follower는 같은 generation 신호일 때만 wait 잔여 시간을 소비하지 않고 즉시 Planner로 진행한다.
Signal write와 release는 owner token 비교를 포함한 하나의 atomic adapter operation이다. 이전 generation의
stale signal은 새 owner/follower에 적용하지 않는다. Process crash처럼 완료 신호를 남길 수 없는 경우에만
lease TTL이 복구 경계다. 완료 신호는 cache value나 negative result cache가 아니며 최대 기존 lease TTL을
넘지 않는 coordination 수명만 가진다.

## Lookup and Miss Contract

1. 인증, active organization, Agent Builder permission과 session/request admission을 완료한다.
2. Foreground request uniqueness와 cancellation/request-version fence를 확인한다.
3. Planner runtime selection을 검증하되 provider usage reservation은 아직 만들지 않는다.
4. Safe Knowledge context와 workflow context를 만든다.
5. Normalizer가 bypass이면 기존 Planner를 호출한다.
6. Eligible이면 HMAC key를 만들고 cache를 조회한다.
7. Hit envelope의 MAC, schema/version/context를 검증한다.
8. Valid hit를 현재 context에 rehydrate한다.
9. Warm-hit rehydration이 실패하면 hit를 폐기하고 miss로 전환해 Planner를 최대 한 번 호출한다.
10. Miss는 single-flight admission 뒤 기존 Planner를 호출한다. 이때만 usage reservation을 시작한다.
11. Planner 결과의 schema/semantic validation 뒤 모든 Knowledge topic과 parameter guidance의 canonical
    reference projection을 포함해 cache eligibility를 다시 판정한다. Reference로 완전히 표현할 수 없으면
    원본 extraction을 기존 non-cache downstream으로 전달하고 put하지 않는다.
12. Eligible final plan을 `CachedIntentPlan`으로 projection하고 current request의 versioned safe-summary projection,
    canonical text registry와 Catalog에서 즉시 rehydrate한다.
13. Cold-miss rehydration이 실패하면 원본 LLM extraction을 사용하거나 Planner를 다시 호출하지 않는다.
    이미 발생한 provider/repair usage를 기록하고 cache put, GraphMutation과 save 없이 기존 terminal error로 닫는다.
14. 성공한 miss downstream에는 provider summary를 포함한 원본 LLM extraction이 아니라 current safe request에서
    summary를 재구성한 canonical structured request를 전달한다.
15. 같은 plan을 best-effort로 put한다.

Cache hit에서도 기존 structured request validation을 다시 실행한다. Cache가 반환한 plan을
validation 없이 GraphMutation builder에 전달하지 않는다. Cache hit는 API/request rate limit,
session의 단일 foreground request와 stale/canceled 판단을 생략하지 않는다.
Cache eligible miss와 hit는 current safe request별 summary, logical step purpose, Knowledge recommendation `query_topics`,
parameter guidance, ParameterTask와 graph materialization에 같은 canonical structured request를 사용한다.

## Knowledge Rehydration

- Cache value의 closed Knowledge requirement는 current candidate resolver의 입력일 뿐 선택 결과가 아니다.
- Ranking용 `query_topics`는 순서 있는 `topic_refs`를 현재 registry version의 고정 canonical string으로 렌더링하며 cache value에는 문자열을 저장하지 않는다.
- 현재 Collection/KB 권한과 lifecycle을 조회해 hierarchy와 score를 새로 계산한다.
- Suggested handle이 필요하면 현재 resolution에서 새로 발급한다.
- Cache 당시 candidate가 없어졌거나 순위가 바뀌어도 stale handle을 복원하지 않는다.
- Knowledge 선택 뒤 Planner를 다시 호출하지 않는 기존 계약은 유지한다.

## Parameter Guidance Rehydration

- `logical_step_ref`를 현재 structured request의 step ID로 bind한다.
- 현재 Catalog에서 `parameter_key`와 template의 허용 `input_type`을 다시 검증한다.
- `reason_template_ref`와 `input_guidance_template_ref`를 registry의 고정 template으로 렌더링한다.
- Cache value, diagnostic과 audit에는 렌더링된 reason/input guidance를 남기지 않는다.
- Unknown ref, 사라진 parameter 또는 template applicability mismatch를 provider 자유 텍스트나 fallback 문구로 보정하지 않는다.

## Target Rehydration

- `new_workflow`와 `replace_workflow`는 current base graph snapshot을 cache하지 않는다.
- `modify_workflow`는 selected node/edge가 현재 server-loaded graph에 존재하고 key의 selection
  fingerprint와 일치할 때만 hit를 사용할 수 있다.
- Natural-language node/edge target, 다중 target과 graph ambiguity는 cache bypass 대상이다.
- Current target resolution 실패는 cached target ID로 대체하지 않는다.

## Usage and Diagnostics

Cache diagnostic event는 다음 safe field만 사용할 수 있다.

| Field | Values |
|---|---|
| `outcome` | `hit \| miss \| bypass \| error` |
| `reason_code` | allowlisted enum |
| `normalizer_version` | version |
| `cache_schema_version` | version |
| `latency_bucket` | bounded bucket |
| `singleflight_role` | `owner \| follower \| overflow \| none` |

Cache key digest, raw request와 plan payload는 log/audit/metric label로 사용하지 않는다. Cache hit는
`llm_usage_logs`를 만들지 않는다. Miss/repair provider attempt만 기존 usage recorder를 통과한다.
Single-flight role은 `owner|follower|overflow|none`이며 process-wide waiter slot을 얻지 못한 요청은
`overflow`, reason은 `waiter_capacity_exceeded`로 기록한다. PostgreSQL audit는 cache value 복구나
replay에 사용하지 않고 allowlisted outcome/reason/latency만 기록한다.

## Configuration

| Environment variable | Purpose |
|---|---|
| `NODE_ENV` | 기존 환경 판정 재사용. `production`과 `staging`은 동일한 cache readiness gate 적용 |
| `AGENT_BUILDER_INTENT_CACHE_ENABLED` | cache serving on/off |
| `AGENT_BUILDER_INTENT_CACHE_TTL_SECONDS` | bounded entry TTL; initial default 900 |
| `AGENT_BUILDER_INTENT_CACHE_TIMEOUT_MS` | Redis operation timeout |
| `AGENT_BUILDER_INTENT_CACHE_MAX_BYTES` | serialized payload limit |
| `AGENT_BUILDER_INTENT_CACHE_HMAC_KEY` | dedicated key material; absent means disabled |
| `AGENT_BUILDER_INTENT_CACHE_HMAC_KEY_VERSION` | namespace rotation version |
| `AGENT_BUILDER_INTENT_CACHE_LEASE_SECONDS` | single-flight lease TTL |
| `AGENT_BUILDER_INTENT_CACHE_WAIT_MS` | follower bounded wait |
| `AGENT_BUILDER_INTENT_CACHE_MAX_FOLLOWER_WAITERS` | process-wide follower waiter 상한 |
| `AGENT_BUILDER_INTENT_CACHE_REDIS_URL` | cache 전용 Redis endpoint; production serving 기본 경계 |
| `AGENT_BUILDER_INTENT_CACHE_PRODUCTION_READY` | 외부 운영 검증을 완료한 배포 운영자가 설정하는 attestation |

Invalid configuration disables the cache and emits a safe startup/configuration signal. It must not silently
fall back to a weak unhashed key.

초기 설정 범위는 TTL 30~3600초(기본 900), Redis operation timeout 10~500ms(기본 100), payload
4~64KiB(기본 32KiB), lease 5~300초(기본 240), follower wait 0~60000ms(기본 45000), follower
waiter 1~64개(기본 8)다. 실제 follower
deadline은 설정값과 현재 Agent Builder request의 남은 deadline 중 짧은 값이다. HMAC key는 최소
32 random bytes를 요구한다.

Follower는 wait 전체를 한 번에 block하지 않고 짧은 bounded slice로 나눈다. 각 slice 뒤와 cache value를
사용하기 직전에 cancellation/request version을 다시 확인한다. Process-wide slot은 non-blocking으로
획득하며 overflow는 wait 없이 기존 Planner로 진행한다. Timeout, cancellation, version change, Redis error와
예외를 포함한 모든 경로에서 slot을 `finally` 의미로 반환한다.

HMAC key와 credential이 포함될 수 있는 Redis URL은 tracked 환경 파일, log, audit와 trace에 원문을
남기지 않는다. Local은 untracked environment, production은 기존 secret injection 경계를 사용한다.
Key version 변경은 dual-read 없이 새 namespace를 사용한다. Invalid secret 또는 URL은 safe reason
code만 기록하고 cache를 비활성화한다.

Cache flag의 기본값은 false다. Cache 전용 URL이 없을 때 Celery broker/result Redis URL로 자동 fallback하지
않는다. 환경 판정은 기존 `NODE_ENV`를 재사용한다. `NODE_ENV=production`과 `NODE_ENV=staging`에서
serving하려면 전용 Redis URL, HMAC key/version, 유효한 TTL/max payload/timeout/lease/wait/waiter 설정과
`AGENT_BUILDER_INTENT_CACHE_PRODUCTION_READY=true`가 모두 필요하다. `development`, `test`와 미설정
환경에서는 cache가 기본 비활성이다. 개발 환경에서 명시적으로 cache를 활성화했더라도 Redis 오류는
기존 Planner로 fail-open한다. 운영자는 외부 Redis 검증을 완료한 뒤에만 ready flag를 설정하며, 런타임은
별도 evidence 저장소를 조회하지 않고 이 attestation과 나머지 configuration의 유효성을 판정한다.

Cache 구현은 전용 Redis URL을 받을 수 있는 adapter/configuration과 production/staging cache disable gate까지만
제공한다. 전용 Production Redis instance 생성, Helm/Kubernetes secret/URL wiring, capacity·eviction,
failure/network 검증, monitoring/rollback과 staged rollout은 별도 후속 운영 범위다. 해당 후속 이슈의
진행 여부는 cache 코드·필수 검증 완료와 분리한다. 운영 증거가 없으면 운영자가 ready attestation을
설정하지 않아야 하며 production/staging cache는 비활성으로 유지된다.

## HTTP Error Projection

Cache-specific failures are not exposed as new HTTP errors. Planner/runtime, validation, permission, stale,
CAS와 acknowledgement 오류는 기존 Agent Builder contract로 반환한다. Cache diagnostic information은
response body에 넣지 않는다.

## Internal Benchmark Contract

Latency evaluation을 위한 public endpoint는 추가하지 않는다. Live collector는 기존 Agent Builder message,
request/session 조회와 canonical workflow 경계를 사용한다. Planning latency는 coordinator 내부 monotonic
clock으로 측정하고 end-to-end latency는 collector가 기존 API 호출 전후를 측정한다.

Benchmark 실행 설정은 원문 API token이 아니라 `NODEASE_EVAL_USER_ID`,
`NODEASE_EVAL_ORGANIZATION_ID`, `NODEASE_EVAL_CREDENTIAL_ID`, `NODEASE_EVAL_MODEL_ID` 같은 로컬 프로세스
환경변수의 UUID reference를 사용한다. Runtime loader는 active organization, credential `use`, verified model
relation과 실제 API model ID `gpt-5.5`를 재검증하고 하나라도 다르면 측정을 시작하지 않는다. HTTP
end-to-end collector에 필요한 Nodease access token은 tracked 파일과 report에 기록하지 않고 실행 프로세스
환경에서만 제공한다.

Report sample에는 case/pair/run ID, comparison group, planning/end-to-end latency, benchmark 전용 cache outcome
(`disabled|hit|miss|bypass|error`),
provider/repair call count, terminal status, validation 결과와 비가역 fingerprint만 허용한다. Raw prompt,
provider payload, credential config/ID와 user/organization/workflow ID는 금지한다.
`disabled`는 cache-off baseline sample에만 허용하며 cache-on sample은 실제 `hit|miss|bypass|error`를 기록한다.

개발 중 exploratory result는 ignored 경로에 둔다. PPT/README에서 인용하는 최종 bundle은
`docs/features/agent-builder-cache/benchmarks/<benchmark-id>/`에 Git으로 보존한다. `<benchmark-id>`는
`<run-date>-<git-sha>-<dataset-version>-<cache-contract-version>`으로 식별한다. Bundle에는 `README.md`,
`runs.csv`, `summary.json`, `summary.csv`, `latency-comparison.svg`, `cache-hit-scenarios.svg`를 포함한다.
`README.md`는 핵심 측정·modeled-estimate 요약과 함께 회차별 data, 두 SVG graph와 JSON/CSV summary의
동일 bundle 기준 상대 경로를 모두 기록한다.
안전한 합성 dataset은 추적할 수 있지만 report는 prompt 대신 `case_id`만 기록한다. 별도 artifact store는
deterministic cache의 최종 evidence 보존 경로로 사용하지 않는다.

Summary는 측정된 warm-hit mean `W`, cold-miss mean `C`와 hit rate `h`로
`expected_mean(h) = h * W + (1 - h) * C`를 계산한다. `h=0.25, 0.50, 0.75`의 planning과 end-to-end 값을
각각 생성하고 반드시 `modeled_estimate`로 표시한다. Semantic bypass와 Redis fail-open은 miss 경로로
분류한다. 이 모델링 계산 자체는 provider를 호출하지 않는다.
