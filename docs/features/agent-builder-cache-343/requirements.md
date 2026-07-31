# MBA-343 Agent Builder Cache Spine Requirements

Status: Draft
Related Features: [Agent Builder](../agent-builder/requirements.md)

## 1. Purpose

MBA-343은 Agent Builder의 결정적 Intent Plan Cache를 이후 구현이 안전하게 확장할 수 있도록
application 계층의 공통 계약, 순수 codec, port와 기본 비활성 경계를 만든다. 이 이슈는 캐시를
serving하지 않으며 기존 Planner 호출과 결과를 그대로 유지한다.

이 문서의 상위 결정은 Accepted
[ADR-0063](../../decisions/ADR-0063-agent-builder-deterministic-intent-plan-cache.md)과
[ADR-0022](../../decisions/ADR-0022-incremental-hexagonal-architecture-adoption.md)다.

## 2. Problem Statement

후속 cache 구현이 각각 자신의 DTO와 저장 형식을 만들면 다음 문제가 생긴다.

- normalizer, 저장소와 rehydrator가 provider extraction 또는 Redis 세부사항에 직접 결합한다.
- free-form topic, guidance, graph identity 또는 protected resource reference가 cache value로 유입될 수 있다.
- 비활성 상태에서도 기존 Planner 호출 순서와 usage attribution이 달라질 수 있다.
- schema와 version 검증이 adapter별로 달라져 같은 payload를 다르게 해석할 수 있다.

MBA-343은 이 위험을 막는 최소 spine만 정의한다.

## 3. Design Principles

- cache는 삭제 가능한 최적화이며 Planner, Catalog, permission, Knowledge resolution,
  GraphMutation, CAS와 acknowledgement를 대체하지 않는다.
- application contract는 FastAPI, SQLAlchemy, provider SDK와 Redis client를 알지 않는다.
- cache value는 자유 문자열이나 요청별 identity가 아니라 versioned closed reference로만 구성한다.
- 직렬화 가능한 cache plan과 직렬화하면 안 되는 transient planning context를 분리한다.
- 기본 composition은 명시적으로 disabled boundary를 기존 Planner orchestration seam에 주입한다.
  Disabled boundary는 Planner를 정확히 한 번 그대로 위임하고 cache lookup/store를 수행하지 않는다.
- 후속 구현은 application port를 구현하되 port가 adapter 기술을 노출하도록 확장하지 않는다.

## 4. Scope

### 4.1 In Scope

- strict `CachedIntentPlanV1`과 하위 closed DTO 계약
- non-serializable `IntentPlanningContext`와 ephemeral identity 경계
- canonical encode/decode와 recursive forbidden-field 검증
- normalizer, plan store, rehydrator port
- hit, miss, bypass, error를 표현하는 내부 cache decision 계약
- 어떤 port도 호출하지 않고 기존 Planner를 정확히 한 번 위임하며
  `bypass/feature_disabled`를 반환하는 disabled boundary
- `AgentBuilderComposition`이 disabled boundary를 `AgentBuilderService`에 주입하는 단일 factory/seam
- 기존 Planner와 usage recorder의 동작이 바뀌지 않음을 확인하는 회귀 테스트
- application import 방향과 secret-safe 오류 계약 검증

### 4.2 Out of Scope

- Unicode 정규화 profile, alias 판정과 cache eligibility 알고리즘
- cache key material 생성, HMAC, authenticated envelope와 key rotation
- Redis adapter, TTL, timeout, lease, single-flight와 waiter 제한
- provider extraction을 `CachedIntentPlanV1`으로 projection하는 로직
- cache hit rehydration과 현재 Catalog, graph, permission, lifecycle 재검증
- 실제 Agent Builder Planner 경로의 lookup, hit, miss, put 또는 metric 연결. 단, disabled boundary가
  기존 Planner closure를 그대로 호출하는 no-op seam은 포함한다.
- feature flag와 production/staging readiness gate
- public HTTP API, DB table, migration, Client UI와 workflow runtime 변경
- 의미 유사도, embedding, vector index와 Graph Template RAG

## 5. Functional Requirements

### ABC343-FR-001 Strict Cached Plan

- `CachedIntentPlanV1`은 `extra=forbid`와 immutable 계약을 사용한다.
- root와 모든 nested object는 arbitrary dictionary를 허용하지 않는다.
- plan은 `schema_version=1`과 `canonical_text_registry_version`을 포함한다.
- `request_type`은 actionable한 `new_workflow|modify_workflow`만 허용한다.
- `(request_type, draft_mode)`은 `(new_workflow, new_workflow)`, `(modify_workflow, modify_workflow)` 또는
  `(modify_workflow, replace_workflow)`만 허용한다.
- `draft_mode`, capability, integration action, edit placement와 risk flag는 closed value만 허용한다.
- capability와 capability별 parameter key/input type은 immutable Catalog v3 snapshot의 exact member만 허용하고
  current Catalog drift test가 불일치를 fail-closed한다.
- `canonical_text_registry_version=intent-text-v1`의 topic/guidance ref는 internal-document와 Slack channel
  회귀 fixture를 위한 명시된 최소 v1 member만 허용한다.
- semantic order가 있는 capability, topic reference와 guidance reference는 입력 순서를 보존한다.

### ABC343-FR-002 Minimal Cache Value

- plan은 ordered capability, deterministic logical step reference, selected-target edit placement,
  Knowledge requirement/placement, canonical topic/guidance reference, contract version과 파생할 수 없는
  최소 risk flag만 저장한다.
- logical step은 실제 node ID가 아니라 `capability + 1-based occurrence`로 식별한다.
- `intent_summary`, step purpose와 provider guidance의 rendered text는 저장하지 않는다. `intent-text-v1`은
  current `IntentPlanningContext.full_safe_message`를 입력으로 하는 `summary.current_safe_message.v1` projection
  descriptor와 capability별 canonical purpose의 exact immutable table을 소유한다.
- Summary는 request/draft pair의 공통 문장이 아니라 매 요청의 current safe context에서 결정적으로
  재구성하고, purpose는 ordered capability와 logical step에서 파생한다. Provider summary는 cache-eligible
  canonical rehydration의 source가 아니다.
- required capability, deterministic step ID, dependency, pending resolution과 Catalog purpose처럼
  현재 Catalog/registry에서 재생성할 수 있는 값은 중복 저장하지 않는다.

### ABC343-FR-003 Forbidden Cache Content

- graph, node/edge UUID, 좌표, GraphMutation operation, workflow/request/session/operation ID를 금지한다.
- credential, secret, token, API key, password, Redis URL과 raw provider/audit payload를 금지한다.
- 실제 parameter value와 explicit parameter value를 금지한다.
- KB/Collection ID, 이름, candidate handle과 request-scoped opaque handle을 금지한다.
- natural-language target 기반 modify는 exact alias 여부와 무관하게 strict plan schema가 거부한다.
  Cache 가능한 modify contract는 명시적인 selected node/edge target만 허용한다. Runtime bypass와
  store-ineligible 판정 구현은 후속 projection/coordinator 범위다.
- 전체 교체인 `draft_mode=replace_workflow`는 edit target을 저장하지 않는다.
- topic 또는 guidance가 exact versioned reference로 표현되지 않으면 plan 생성 대상이 아니다.
- 금지 필드 검사는 encode 전과 decode 직후 모두 재귀적으로 수행하며 오류에 원문 값이나 payload를 포함하지 않는다.

### ABC343-FR-004 Canonical Codec

- codec은 하나의 canonical UTF-8 JSON byte representation을 만든다.
- encoder는 `model_dump(mode=json, exclude_none=False)`, sorted key, compact separator, UTF-8 non-ASCII,
  non-finite-number rejection, no BOM/newline 규칙을 고정한다.
- object key order, whitespace와 JSON encoder 기본값 차이가 canonical bytes에 영향을 주지 않는다.
- list order는 semantic order이므로 변경하지 않는다.
- duplicate key, non-UTF-8, non-finite number, unknown schema version, unknown field와 malformed reference를 거부한다.
- decode 후 같은 codec으로 encode한 bytes가 입력과 다르면 non-canonical payload로 거부한다.
- payload size limit은 호출자가 제공하는 bounded 값으로 검사하되 운영 설정이나 Redis 정책은 소유하지 않는다.

### ABC343-FR-005 Transient Planning Context

- `IntentPlanningContext`는 전체 safe request와 절단되지 않은 logical topology를 보유하는 transient 타입이다.
- `full_safe_message`는 future rehydrator가 request-specific `intent_summary`를 만들기 위한 유일한 summary
  source다. MBA-343은 message를 보존하고 projection descriptor를 고정하지만 실제 projection은 구현하지 않는다.
- generation mode, 검증된 model/credential relation fingerprint, Knowledge candidate fingerprint와
  cache contract version 집합을 보유한다.
- actor, organization과 selected target의 실제 identity는 `EphemeralCacheScope`에만 보유하며
  해당 타입은 dump, JSON encode, log representation을 제공하지 않는다.
- context는 SQLAlchemy model, DB session, FastAPI request, raw graph와 provider client를 포함하지 않는다.
- context와 `EphemeralCacheScope`는 Pydantic model/dataclass dump 대상이 아니며 JSON encode를 거부하고
  고정 redacted `repr`만 제공한다.
- context 하위 node/edge, generation mode, provider와 ephemeral scope field는 API specification의 exact
  enum, cardinality와 교차 invariant를 적용한다.
- 두 transient 타입은 `json.dumps`, `vars`, `dataclasses.asdict`, `pickle.dumps`를 `TypeError`로 거부한다.
- MBA-343은 이 context를 production request에서 조립하지 않는다.

### ABC343-FR-006 Narrow Ports

- normalizer port는 context를 받아 strict `IntentNormalizationResult`를 반환하고 eligible/bypass의
  signature/reason invariant를 적용한다.
- store port는 opaque key와 typed plan을 load/save하는 최소 계약만 노출하되 `hit|miss|invalid|unavailable`과
  `stored|unavailable`을 strict result DTO로 구분한다. Redis command, URL, connection pool과 transaction은
  노출하지 않는다.
- rehydrator port는 cached plan과 현재 검증 context를 받아 strict `IntentRehydrationResult`를 반환하고
  success/failure의 structured-request/reason invariant를 적용한다. Future success result는 provider summary나
  request/draft 공통 문장이 아니라 current context의 versioned safe-summary projection을 사용해야 한다.
- port는 서로를 import하지 않고 공통 contract만 import한다.

### ABC343-FR-007 Disabled Boundary

- application의 단일 no-op entry point는
  `IntentPlanCacheBoundary.execute(planner_call, context=None)`다.
- MBA-343의 유일한 concrete boundary는 `planner_call`을 정확히 한 번 실행하고 그 structured request와
  `outcome=bypass`, `reason=feature_disabled`를 반환한다.
- disabled boundary는 normalizer, codec, store와 rehydrator를 호출하지 않는다.
- disabled boundary는 optional context field를 읽거나 복사, 직렬화, log하지 않는다.
- Planner exception은 변환하거나 재시도하지 않고 기존 service error mapping으로 그대로 전달한다.

### ABC343-FR-008 Composition Isolation

- `AgentBuilderComposition.intent_plan_cache()`는 disabled boundary만 반환한다.
- `AgentBuilderComposition.orchestration()`은 이 boundary를 `AgentBuilderService`에 명시적으로 주입한다.
- `AgentBuilderService._structure_request`는 기존 extract/validate/normalize sequence를 하나의 closure로 유지하고
  disabled boundary를 통해 정확히 한 번 실행한다.
- 기존 direct construction 호환을 위해 boundary가 생략되면 같은 disabled implementation을 기본값으로 사용한다.
- MBA-343에는 enabled implementation이 없으며 context builder와 enabled coordinator가 없는 상태에서
  feature가 부분 활성화될 수 없다.
- 후속 integration은 같은 boundary seam에 complete planning context와 coordinator를 함께 연결한다.

### ABC343-FR-009 Existing Behavior Preservation

- 동일 message는 disabled boundary를 통과해도 기존과 같은 extractor를 정확히 같은 횟수로 호출한다.
- provider usage reservation/record/cancel 순서와 오류 mapping을 변경하지 않는다.
- 기존 public Agent Builder request/response schema, status와 audit 결과를 변경하지 않는다.
- disabled boundary의 존재 때문에 cache diagnostic이나 DB row를 추가하지 않는다.

### ABC343-FR-010 Safe Failure Contract

- contract/codec 오류는 stable allowlisted code만 제공한다.
- codec은 public typed `IntentPlanCodecError`만 발생시키고 closed code/path category 밖의 진단을 노출하지 않는다.
- 오류 message, `repr`, test assertion output에 plan payload, safe request 또는 ephemeral identity를 포함하지 않는다.
- schema 또는 codec 오류는 이 이슈에서 Planner 오류로 변환하지 않는다. 실제 fail-open 연결은 후속 integration 범위다.

## 6. Non-functional Requirements

### ABC343-NFR-001 Cohesion

contract, codec, port와 disabled 구현은 `apps/gateway/application/agent_builder/intent_cache/`가 소유한다.
각 module은 한 변경 이유만 갖고 public export는 package `__init__.py`에서 제한한다.

### ABC343-NFR-002 Coupling

application package는 표준 라이브러리, Pydantic과 허용된 shared schema만 import한다. Gateway service,
composition, adapter, SQLAlchemy, FastAPI, provider SDK와 Redis library를 import하지 않는다.

### ABC343-NFR-003 Determinism

동일한 validated plan과 codec version은 process, dictionary construction order와 locale에 관계없이 같은 bytes를 만든다.

### ABC343-NFR-004 Security

forbidden-field 검증은 fail-closed이며 validation 자체가 원문을 log하거나 반환하지 않는다.

### ABC343-NFR-005 Compatibility

새 package를 import하지 않는 기존 호출자와 테스트는 변경 없이 동작해야 한다.

## 7. Acceptance Criteria

- `CachedIntentPlanV1`과 모든 nested DTO가 strict/immutable contract test를 통과한다.
- canonical codec의 round-trip, byte equality와 malformed input test가 통과한다.
- original bytes의 strict Pydantic JSON-mode decode와 public codec error redaction test가 통과한다.
- Catalog parameter input type, request-specific summary projection descriptor 및 canonical purpose snapshot이 exact
  drift/membership test를 통과한다.
- store load/save result가 miss, invalid와 unavailable을 손실 없이 구분한다.
- 금지 field/value corpus가 root와 nested 위치 모두에서 거부된다.
- disabled boundary가 context와 모든 port를 관찰하지 않고 Planner를 정확히 한 번 실행해 정해진 bypass를 반환한다.
- composition factory가 disabled boundary를 실제 orchestration seam에 주입하고 기존 Planner 결과와
  usage wiring을 유지한다.
- Agent Builder application import-boundary test에 신규 위반이 없다.
- 관련 Gateway 단위·composition 회귀 테스트가 통과한다.
- Redis, 환경 설정, DB, Client 또는 public API 변경이 diff에 없다.

## 8. Protected-resource Completion Classification

MBA-343은 protected resource reference를 저장하거나 새로 사용하지 않고 새 외부 I/O 경로도 만들지 않는다.
Disabled seam은 기존 Planner closure를 동일하게 한 번 위임할 뿐 protected identity나 credential을
관찰하지 않는다. 따라서 새 관리 UI, runtime/background, lifecycle, preflight와 audit persistence 경계는
이 이슈에서 `해당 없음`이다. Cache value의 protected identity/secret 비포함과 redaction-safe validation
test만 `완료 필요`다. 이후 serving cache가 permission, Knowledge 후보 또는 credential relation을 소비할 때는
[보호 리소스 기능 완결성 기준](../../engineering/protected-resource-feature-completion.md)을 다시 전체 적용한다.
