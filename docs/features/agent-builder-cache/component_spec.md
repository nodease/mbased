# Agent Builder Cache Component Specification

Status: Draft

MBA-343 cache spine의 내부 module 분리와 disabled runtime seam은
[MBA-343 component specification](../agent-builder-cache-343/component_spec.md)이 이 문서의
`intent_cache/` package 내부 소유권을 구체화한다. 전체 coordinator, Redis adapter와 rehydration의
책임 경계는 이 문서가 계속 소유한다.

## Design Goals

- Cache correctness를 자연어 의미 추론과 분리한다.
- Application domain이 Redis와 provider client 구현에 직접 결합되지 않게 한다.
- Cache hit와 miss가 structured request 이후 동일한 direct-edit 경계를 사용하게 한다.
- Cache가 protected resource authorization이나 durable workflow state를 소유하지 않게 한다.
- Cache 삭제, 장애와 version rotation이 기능 rollback 수단이 되게 한다.

## Component Boundaries

| Component | Responsibility | Must not do |
|---|---|---|
| `DeterministicIntentNormalizer` | allowlist normalization, eligibility와 signature 생성 | capability 추론, LLM 결과 수정 |
| `IntentContextFingerprinter` | safe graph/selection/Knowledge context의 canonical digest | raw graph/config 저장 |
| `IntentPlanCacheKeyBuilder` | versioned key material과 HMAC key 생성 | plaintext identity/key 노출 |
| `IntentPlanCacheEnvelopeCodec` | canonical payload와 key를 인증하고 strict value를 decode | MAC 미검증 payload 반환 |
| `IntentPlanCachePolicy` | lookup/store admission과 bypass reason 결정 | provider 호출, graph 생성 |
| `IntentPlanCachePort` | cache/lease 의미 계약 | Redis 타입 노출 |
| `RedisIntentPlanCacheAdapter` | bounded Redis get/put/lease/wait | business validation |
| `CachedIntentPlanCodec` | strict serialization, max size와 forbidden field validation | Pydantic validation 우회 |
| `CanonicalIntentTextRegistry` | provider topic/guidance의 exact ref projection, request-specific safe-summary projection descriptor와 purpose fixed rendering | fuzzy/semantic mapping, 자유 형식 template argument |
| `RequestIntentSummaryProjector` | current `full_safe_message`에서 versioned bounded `intent_summary` 생성 | provider summary 또는 request/draft 공통 문장 재사용 |
| `CachedIntentPlanRehydrator` | current context와 canonical registry에서 structured request 재구성 | 과거 UUID/handle 또는 provider text 재사용 |
| `AgentBuilderIntentCoordinator` | cache, Planner와 rehydration orchestration | GraphMutation/CAS 소유 |
| Existing Planner Adapter | provider invoke, schema parse, semantic repair와 usage | cache policy 결정 |
| Existing `AgentBuilderService` | full safe planning context DTO 생성, target resolve, Catalog materialization, validation | DTO 절단, Redis 직접 접근 |

## Dependency Direction

```text
HTTP endpoint
  -> Agent Builder composition
      -> existing auth / organization / request admission
      -> AgentBuilderIntentCoordinator
          -> application normalizer/policy/fingerprinter/rehydrator
          -> IntentPlanCachePort
              <- RedisIntentPlanCacheAdapter
          -> existing Planner port
              <- provider-backed intent extractor
      -> existing structured request / GraphMutation / CAS services
```

Application types는 `redis.Redis`, FastAPI Request와 SQLAlchemy model을 import하지 않는다. Composition이
현재 user/organization과 concrete adapter를 조립한다.

Cache coordinator는 기존 request admission과 cancellation/version fence 뒤에 호출한다. Cache miss의
provider invoke 직전에만 기존 usage reservation을 시작한다. Hit는 request admission과 session 직렬화를
우회하는 fast path가 아니다.

## Proposed File Ownership

| Path | Content |
|---|---|
| `apps/gateway/application/agent_builder/intent_normalization.py` | signature와 normalization policy |
| `apps/gateway/application/agent_builder/intent_cache/` | MBA-343의 `catalog_snapshot.py`, `contracts.py`, `codec.py`, `ports.py`, `disabled.py`; Catalog v3 node/capability/parameter key·input type, request-summary projection descriptor와 canonical purpose snapshot, cache DTO, strict codec, ports와 no-op 경계 |
| `apps/gateway/application/agent_builder/intent_cache_coordinator.py` | cache hit/miss/bypass, Planner, rehydration과 usage orchestration |
| `apps/gateway/application/agent_builder/intent_rehydration.py` | current-context rehydration |
| `apps/gateway/application/agent_builder/intent_rehydration_registry.py` | versioned topic/guidance/purpose manifest, request-summary projection과 fixed template rendering |
| `apps/gateway/adapters/cache/agent_builder_intent_plan.py` | Redis adapter와 codec boundary |
| `apps/gateway/composition/agent_builder.py` | configuration, adapter와 service wiring |
| `apps/gateway/services/agent_builder_service.py` | transient full safe planning context DTO와 coordinator seam |
| `apps/gateway/services/agent_builder_intent_service.py` | provider-backed extraction과 repair 유지 |

`intent_cache_coordinator.py`는 `intent_cache/`의 DTO와 port를 의존할 수 있지만 반대 방향 import는
허용하지 않는다. Redis client 타입은 adapter 밖의 application module에 노출하지 않는다.
현재 분할 구현에서는 위 responsibility boundary를 가로질러 module을 합치지 않는다. 같은 responsibility
안의 세부 helper만 합칠 수 있으며 normalization, cache adapter, provider invoke와 graph materialization
책임은 계속 분리한다. Boundary 간 파일 통합이 필요하면 현재 기능 전달 뒤 별도 refactor로 결정한다.

## Normalization Pipeline

1. 기존 secret 경계가 API에서 허용된 전체 요청을 검사하고 저장되지 않는 `full_safe_message`를 만든다.
2. Unicode NFKC와 whitespace normalization을 적용한다.
3. 영문 token에 casefold를 적용한다.
4. Versioned alias table을 longest-token, boundary-aware 방식으로 적용한다.
5. Versioned profile의 허용 조사·정중 표현과 위치/연결 표현만 canonicalize한다.
6. 인용문, 숫자, 부정, node label과 parameter-like span을 alias 적용보다 먼저 보호한다.
7. 위치/순서/부정/수량/인용 영역을 invariant로 검사한다.
8. Planner prompt용 truncated summary와 분리된 전체 safe request를 안전하게 canonicalize할 수 있으면 signature를 반환한다.
9. 입력 또는 graph topology projection이 잘렸거나 미인식 잔여 표현 또는 explicit value 가능성이 있으면 bypass한다.

Alias table은 UI 설명이나 LLM prompt에서 파생하지 않는다. Catalog capability alias와 별도 server
action alias를 정적 검증하고 중복 canonical target을 허용하지 않는다. Profile과 adversarial corpus는 같은
`normalizer_version`으로 추적하며 profile 변경은 namespace miss를 만든다.

## Workflow Context Fingerprint

Fingerprint projection은 다음만 포함한다.

- node type, sanitized role/label과 configuration presence
- source/target logical topology
- selected node/edge의 존재와 structural role
- workflow present/blank context
- generation mode

Position, measured size, viewport, raw node data, credential/resource reference와 parameter value는 제외한다.
Node label이 target 의미에 필요하면 plaintext를 저장하지 않고 key HMAC input의 canonical projection에만
사용한다. Node 개수 상한으로 topology 일부를 버리지 않는다. 전체 logical topology digest를 계산할 수
없으면 cache를 우회한다.

`AgentBuilderService`는 Planner prompt용 2,000자 summary 또는 50-node projection을 재사용하지 않고
cache coordinator 전용 transient DTO를 만든다. Actor/organization scope와 명시적인 selected node/edge
identity는 HMAC material 생성에만 사용한다. Selected target identity는 non-serializable ephemeral field로
key 생성 직후 폐기하며, 그 밖의 workflow/node/edge/Knowledge resource identity, request/operation identity와
raw configuration은 DTO에 넣지 않는다.

## Cache State Model

| State | Transition |
|---|---|
| `disabled` | configuration 또는 feature flag로 Planner 직행 |
| `bypass` | normalization/admission 불가로 Planner 직행 |
| `miss` | lease admission 뒤 Planner 호출 |
| `hit_candidate` | value decode와 version 검증 진행 |
| `rehydrating` | current permission/context로 structured request 재구성 |
| `hit_valid` | 기존 structured validation으로 전달 |
| `hit_rejected` | current mismatch로 miss 전환 |
| `cache_error` | safe metric 후 miss 전환 |

이 상태는 durable session 상태가 아니며 DB에 저장하지 않는다.

## Single-Flight

- Lease key는 cache value key와 분리된 namespace를 사용한다.
- Owner token은 random request-local value이며 log하지 않는다.
- `SET NX EX`와 owner-token 비교 release를 사용한다.
- Owner는 provider 호출 전 lease를 얻고 valid plan put 뒤 release한다.
- Follower는 configured wait와 현재 request의 남은 deadline 중 짧은 시간까지 value만 조회하며 owner의
  provider response를 직접 공유하지 않는다. 초기 configured wait는 기본 45초, 최대 60초다.
- Wait는 짧은 bounded slice로 나누고 slice 사이와 value 사용 직전에 cancellation/request version fence를
  확인한다. Cancellation/version change와 value 도착이 경합하면 cancellation/version change가 우선한다.
- Follower wait는 process-wide non-blocking slot을 사용한다. 기본 8개, 허용 범위 1~64개이며 상한을 넘은
  요청은 `overflow`로 기록하고 기다리지 않은 채 기존 Planner를 호출한다.
- Slot은 timeout, cancellation, version change, Redis error와 예외를 포함한 모든 종료 경로에서 반환한다.
- Owner는 valid plan 저장, clarification, unsupported, provider failure와 store-ineligible 결과를 포함한 모든
  정상 terminal path에서 lease를 해제한다. Value가 없는 정상 종료는 owner token을 비교해 관찰된 lease
  generation과 묶인 짧은 coordination signal인 `owner_completed_without_value` 기록과 release를 원자적으로
  수행한다. Follower는 자신이 관찰한 generation과 일치할 때만 즉시 Planner로 진행한다. 이 신호는 cache
  value나 negative result cache가 아니며 이전 generation 신호는 새 lease에 적용하지 않는다.
- Owner가 wait 안에 value를 저장한 경우에만 provider 단일 호출을 보장한다. Timeout 뒤 follower는
  Planner를 호출할 수 있다. Lease가 correctness lock은 아니므로 요청 실패나 무한 대기를 만들지 않는다.
- Provider 호출과 follower wait 동안 DB transaction/row lock을 유지하지 않는다.

## Hit Rehydration

Cache admission을 통과한 miss도 valid extraction을 `CachedIntentPlan`으로 projection한 직후 이 rehydration을
거친다. 최초 miss와 이후 hit는 같은 canonical structured request를 downstream에 전달한다. LLM extraction의
자유 형식 summary/guidance를 cache-eligible miss에서만 직접 사용하는 별도 경로를 두지 않는다.
Store-ineligible result는 기존 non-cache 경로이므로 이 제한의 대상이 아니다.

`intent_summary`는 plan이나 request/draft 공통 template에서 만들지 않는다. Rehydrator는 매 요청의 transient
`IntentPlanningContext.full_safe_message`를 `summary.current_safe_message.v1` descriptor에 따라 whitespace
collapse, fail-closed redaction과 240-code-point 상한으로 projection한다. Empty 또는 redaction marker가 남으면
rehydration을 fail-closed한다. Cache-eligible cold miss에서는 provider summary 대신 이 projection을 사용하므로
같은 current request의 miss/hit는 같고, 같은 logical plan을 만든 서로 다른 safe request는 각 request-specific
summary를 유지한다. Summary projection은 cache value, diagnostic, metric과 audit에 문자열을 남기지 않는다.

Projection은 provider의 모든 `knowledge_topics`와 `parameter_guidance_hints`를 versioned registry의 closed
reference로 표현할 수 있을 때만 cache-safe plan을 만든다. Topic은 순서 있는 `topic_ref`, guidance는
logical step ref, Catalog `parameter_key`와 reason/input-guidance template ref로 투영한다. Exact alias/template
match만 허용하며 하나라도 매핑되지 않으면 전체 result를 store-ineligible로 분류한다. 이 경우 coordinator는
원본 extraction을 기존 non-cache downstream에 전달하고 put하지 않는다. 이는 projection 성공 뒤의
cold-miss rehydration failure와 구분한다.

Warm hit의 rehydration failure는 해당 hit를 버리고 Planner를 최대 한 번 호출하는 miss로 전환한다.
Cold miss의 rehydration failure는 이미 provider attempt가 발생한 terminal failure다. 원본 extraction을
우회 사용하거나 같은 요청에서 Planner cycle을 다시 시작하지 않으며 cache put, GraphMutation과 save를
수행하지 않는다. 이미 발생한 provider/repair usage는 그대로 기록한다.

### Planner Runtime

Cache hit도 active organization membership, credential status, model status, verified relation과 use permission을
검사한다. Cache가 이전 authorization 결과를 저장하지 않는다. 검증 실패는 기존 permission/runtime error로
닫고 다른 model의 cached plan을 자동 사용하지 않는다.

### Target

Logical edit placement를 현재 selected node/edge와 server-loaded graph에 다시 bind한다. Binding이 유일하지
않으면 hit를 버리고 Planner 또는 clarification 경계로 진행한다. Cache plan 안에 target UUID를 넣지 않는다.

### Knowledge

Cache에는 closed requirement, 순서 있는 `topic_ref`와 placement만 둔다. Rehydrator는 현재 registry version으로
각 ref를 순서 있는 canonical `query_topics` 문자열로 렌더링한다. Collection/KB 권한, lifecycle, readiness와 score를
다시 계산하고 새로운 handle을 발급한다. Cache 당시 추천 순서나 선택을 자동 복원하지 않는다.

### Parameter Guidance

Cache에는 logical step ref, Catalog `parameter_key`, `reason_template_ref`와
`input_guidance_template_ref`만 둔다. Rehydrator는 현재 logical step을 bind하고 Catalog parameter 및
template input-type applicability를 검증한 뒤 registry의 고정 template을 렌더링한다. Template에는 현재
Catalog의 allowlisted key, safe label과 input type만 주입할 수 있고 자유 형식 argument는 없다. Unknown ref,
registry version mismatch와 Catalog incompatibility를 fallback text로 보정하지 않는다.

Registry module은 `topic.<slug>.v1`, `guidance.reason.<slug>.v1`, `guidance.input.<slug>.v1` namespace와
strict manifest를 소유한다. 같은 version은 `summary.current_safe_message.v1` projection descriptor와
capability별 step purpose의 exact table도 소유한다. Closed ref enum, summary projection descriptor와 purpose
contract snapshot은 `intent_cache/` package가 소유하고 codec가 unknown ref를 거부한다.
Startup/static validation은 enum 대비 누락·초과 ref, ref/alias/canonical text 중복, alias의 다중 ref 매핑,
지원하지 않는 placeholder와 빈 input-type applicability를 거부한다. Manifest 또는 enum 변경은
`canonical_text_registry_version`과 regression fixture를 함께 변경한다.

### Graph

현재 Catalog template으로 node와 edge를 새로 만들고 전체 graph를 검증한다. Cache hit는 GraphMutation
operation ID, base hash, expected result hash와 updated_at을 현재 요청에서 새로 계산한다. 이후 저장과
acknowledgement는 miss 경로와 같다.

## Payload Safety

Codec는 allowlist schema 외 field를 거부한다. Serialization 전과 decode 후 다음 방어를 적용한다.

- maximum depth/list length/string length
- maximum encoded bytes
- forbidden key pattern for token, secret, credential, URL/path와 raw payload
- request/session/draft ID와 raw audit payload/metadata 원문 금지
- UUID-like protected identity가 허용 field에 들어오지 않는지 검사
- deterministic canonical JSON serialization
- final Redis key digest와 canonical payload를 domain-separated HMAC으로 묶은 authenticated envelope
- constant-time MAC comparison before plan use

Forbidden payload는 cache에 쓰지 않고 safe reason metric만 기록한다.

## Observability

Metric cardinality를 제한하기 위해 user, organization, workflow, model ID와 key digest를 label로 사용하지 않는다.
허용 label은 outcome, reason code, schema/normalizer version, latency bucket과 single-flight role이다.
운영 지표는 hit ratio, bypass ratio, error ratio, lookup latency, avoided provider attempts와 single-flight contention이다.
Single-flight role은 `owner|follower|overflow|none`이고 overflow reason은
`waiter_capacity_exceeded`다. Process/request/cache key identity는 label에 사용하지 않는다.

Cache hit는 비용 0인 LLM usage row를 만들지 않는다. Provider가 호출된 miss/repair만 기존 usage recorder가
기록한다. Workflow mutation audit은 hit 여부와 무관하게 기존 경계에서 남는다.
PostgreSQL audit는 Redis cache value의 durable copy나 replay source가 아니다. Redis data loss는 cold miss와
Planner로 복구하며 audit에는 allowlisted outcome/reason/latency만 남긴다.

## Deployment and Rollback

1. Cache disabled 상태로 코드와 configuration validation을 배포한다.
2. Development/test에서 deterministic key와 payload safety test를 통과한다.
3. 관찰 모드가 필요하면 lookup 결과를 serving하지 않고 eligibility/miss metric만 확인한다.
4. 작은 TTL로 serving을 활성화하고 hit rejection과 provider call reduction을 확인한다.
5. 문제 발생 시 feature flag를 끄거나 namespace version을 올린다.

Redis flush, TTL expiry와 HMAC rotation은 DB migration을 요구하지 않는다. Cache disabled 상태가 기능의
정상 rollback 경로다.

Feature flag 기본값은 false다. Production과 staging serving은 cache 전용 Redis endpoint만 사용하며 URL이
없을 때 Celery broker/result Redis로 자동 fallback하지 않는다. 환경 판정은 기존 `NODE_ENV`를 재사용하고,
`production|staging`에서는 전용 URL, HMAC key/version과 유효한 bounded configuration이 모두 존재하며
명시적인 production-ready attestation이 true일 때만 serving을 허용한다. `development`, `test`와 미설정
환경에서는 cache가 기본 비활성이다. 운영자는 외부 검증을 완료한 뒤 attestation을 설정하고, 런타임은
별도 evidence 저장소를 조회하지 않고 해당 선언과 configuration만 검사한다.

Cache 구현은 전용 URL 지원과 증거가 없을 때 cache를 끄는 configuration gate까지만 포함한다. 전용 instance,
Helm/Kubernetes secret과 URL wiring, capacity/eviction, failure/network test, monitoring/rollback과 staged rollout은
별도 후속 운영 이슈다. Adapter는 자기 key의 direct get/set/delete만 사용하고 broad scan/flush를 하지 않는다.
후속 운영 작업의 진행 여부는 cache 코드·필수 검증 완료와 분리한다. 운영 증거가 없으면 attestation을 설정하지
않아야 하며 production/staging serving은 비활성이다.

HMAC key와 Redis URL은 기존 secret injection 경계를 사용하며 configuration repr, startup error, log,
trace와 audit에 원문을 노출하지 않는다. Key version rotation은 dual-read 없이 namespace miss로 처리한다.

## PRD and Core Document Impact

사용자 기능, API와 workflow 결과를 바꾸지 않는 내부 최적화이므로 PRD 요구사항을 추가하지 않는다.
전체 architecture에는 Gateway 내부 cache component와 Redis의 비권위적 역할을 추가하고 glossary에 용어를
등록한다. 구현이 사용자에게 cache 상태나 제어 UI를 노출하게 된다면 별도 제품 결정을 먼저 요구한다.

## Internal Latency Benchmark Boundary

Latency benchmark는 제품 API나 cache diagnostic UI를 추가하지 않는 내부 evaluation 도구다.

- Planning timer는 safe request/context 준비 뒤 cache coordinator 진입부터 canonical structured request
  반환까지 측정한다.
- End-to-end timer는 기존 Agent Builder message API 제출 직전부터 terminal response와 canonical
  reconciliation 완료까지 측정한다.
- Raw LLM extractor를 직접 호출하는 기존 intent accuracy benchmark는 cache coordinator를 우회하므로
  deterministic cache latency 근거로 사용하지 않는다.
- Live collector는 DB에 등록된 permission-aware credential/model selection을 사용하고 코드나 report에
  API token을 받지 않는다.
- 같은 paired run은 graph mutation의 영향을 제거하기 위해 동일한 초기 graph snapshot 또는 동등한
  fresh workflow에서 실행한다.
- Report generator는 회차별 `runs.csv`, vector graph와 JSON/CSV/Markdown summary를 생성한다.
- Benchmark row의 cache outcome은 `disabled|hit|miss|bypass|error`이며 런타임 diagnostic outcome과 별도다.
- `disabled`는 cache-off baseline row에만 허용하고 cache-on row는 실제 `hit|miss|bypass|error`를 기록한다.
- Exploratory 실행은 ignored path에 두고, PPT/README에서 인용한 최종 safe bundle은
  `docs/features/agent-builder-cache/benchmarks/<benchmark-id>/`에 Git으로 고정한다. Report row에는
  prompt 대신 case ID를 사용한다.
- Bundle `README.md`는 핵심 요약과 회차별 data, graph, JSON/CSV summary의 상세 상대 경로를 모두 기록한다.
- 최종 summary는 측정 warm-hit/cold-miss mean에서 hit rate 25%, 50%, 75%의 planning/end-to-end 예상값을
  계산하고 `modeled_estimate`로 표시한다. 이 모델링 계산 자체는 provider를 호출하지 않는다.

의미가 비슷하다는 이유만으로 같은 plan을 기대하는 dataset은 후속 Graph RAG 이슈가 소유한다.
이 benchmark에서는 승인된 deterministic alias만 warm normalization hit로 측정하며 semantic-only 문장은
bypass/negative control로만 남긴다.
