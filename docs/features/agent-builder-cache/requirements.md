# Agent Builder Cache Requirements

Status: Draft

Related Features: Agent Builder, Workflow Editor, LLM Cost and Usage

## Purpose

Agent Builder의 명시적으로 동등한 반복 요청에 대해 provider LLM 호출을 생략하되,
현재 권한, Knowledge 후보, workflow graph, Catalog validation과 CAS 저장 계약을 그대로
유지하는 deterministic L1 intent plan cache를 정의한다.

## Authority

- 캐시의 목표 계약은 ADR-0063을 따른다.
- MBA-343 cache spine의 strict DTO, codec, port, disabled runtime seam과 세부 파일 소유권은
  [MBA-343 requirements](../agent-builder-cache-343/requirements.md),
  [internal API](../agent-builder-cache-343/api_spec.md)와
  [component specification](../agent-builder-cache-343/component_spec.md)이 이 문서를 구체화한다.
  두 문서가 충돌하면 admission과 제품 동작은 이 문서를 따르고, MBA-343의 내부 module 분리는
  MBA-343 component specification을 따른다.
- Planner 호출과 semantic repair는 기존 Agent Builder intent 계약을 따른다.
- Cache miss와 repair의 usage attribution은 ADR-0055를 따른다.
- GraphMutation과 저장은 현재 Agent Builder direct-edit/CAS 권위 계약을 따른다.
- 계층형 Collection/KB 후보의 현재 권한과 lifecycle은 ADR-0061을 따른다.

## Feature Terms

- **Deterministic signature**: 승인된 정규화 규칙만 적용한 요청의 canonical 표현이다.
- **Cache-safe plan**: graph와 요청별 식별자 없이 capability, logical step과 typed placement만
  가진 재구성 가능한 intent plan이다.
- **Context fingerprint**: 현재 요청의 의미에 영향을 주는 safe graph, selection, mode,
  Knowledge 후보와 version을 canonicalize한 digest다.
- **Cache bypass**: cache correctness를 증명할 수 없어 lookup 또는 store를 수행하지 않는 결정이다.
- **Rehydration**: cache-safe plan을 현재 권한과 graph에 맞춰 새 structured request로 복원하는 과정이다.
- **Production readiness attestation**: 전용 Redis 인프라와 운영 검증이 완료됐음을 배포 설정이
  명시적으로 선언하는 값이다. 이 값 자체가 인프라 준비를 증명하지는 않는다.

## Functional Requirements

### Normalization

- ABC-FR-001: Server는 cache lookup 전에 secret detection/redaction 결과와 safe request만 사용해야 한다.
- ABC-FR-002: Normalizer는 versioned normalization profile에 정의된 Unicode NFKC, 연속 공백,
  영문 case, 의미 없는 조사·정중 표현과 승인된 alias만 정규화해야 한다.
- ABC-FR-003: Normalizer는 위치, 단계 순서, 부정, 숫자, 인용문, node label과 parameter 값을 보존해야 한다.
- ABC-FR-004: Alias와 허용 문장 요소는 Node Capability Catalog 또는 versioned server allowlist에서만
  제공해야 하며 token/phrase boundary, 위치 표현과 보호 span 우선순위를 profile에 고정해야 한다.
- ABC-FR-005: Versioned profile이 요청 전체를 설명하지 못하거나 미인식 잔여 표현이 남으면
  `not_eligible`로 반환하고 Planner 결과를 덮어쓰지 않아야 한다.
- ABC-FR-006: Secret-like content나 redaction marker가 있으면 lookup과 store를 모두 우회해야 한다.
- ABC-FR-007: Cache normalization은 API가 허용한 전체 safe request를 사용하고 Planner prompt용 truncated summary를 재사용하지 않아야 한다.
- ABC-FR-008: 전체 safe request 또는 전체 logical graph topology의 canonical digest를 만들 수 없거나 projection이 잘리면 lookup과 store를 모두 우회해야 한다.

### Cache Admission

- ABC-FR-009: Cache lookup은 인증, active organization, Agent Builder permission, session/request admission과 cancellation/version fence 뒤에 수행해야 한다.
- ABC-FR-010: Cache는 schema와 semantic validation을 통과한 actionable plan만 저장해야 한다.
- ABC-FR-011: `clarification`, `unsupported`, validation/provider failure, stale와 permission denial은 저장하지 않아야 한다.
- ABC-FR-012: Explicit parameter value가 있는 요청은 초기 버전에서 cache하지 않아야 한다.
- ABC-FR-013: Natural-language target만으로 수정 대상을 찾아야 하는 ambiguous modify 요청은 cache하지 않아야 한다.
- ABC-FR-014: `new_workflow`, `replace_workflow`와 명시적인 selected node/edge 기반 modify만 admission 후보가 될 수 있다.
- ABC-FR-015: Semantic repair 후 성공한 경우 최종 valid plan만 저장하고 invalid attempt는 저장하지 않아야 한다.
- ABC-FR-016: Cache admission을 통과한 miss는 final valid extraction을 cache-safe plan으로 projection한 뒤 즉시 current request, canonical text registry와 Catalog에서 rehydrate해야 한다. Provider가 반환한 Knowledge topic 또는 parameter guidance를 모두 canonical reference로 표현할 수 없으면 해당 결과를 store-ineligible로 판정하고 저장하지 않은 채 기존 non-cache downstream에서 원본 extraction을 사용해야 한다. Projection에 성공한 cache-eligible request의 provider `intent_summary`는 canonical downstream source로 사용하지 않아야 한다.
- ABC-FR-017: Cache eligible miss와 hit는 현재 planning context의 `full_safe_message`에 versioned `summary.current_safe_message.v1`을 적용한 request-specific summary, step purpose, Knowledge recommendation `query_topics`, parameter guidance, ParameterTask와 graph materialization에서 같은 canonical structured request를 사용해야 한다. 서로 다른 safe request가 같은 request/draft type과 logical plan을 만들더라도 summary를 request/draft pair의 공통 문장으로 합치지 않아야 한다.
- ABC-FR-018: `AgentBuilderService`는 cache coordinator용 transient planning context DTO를 소유하고,
  전체 safe request, 전체 logical topology, actor/organization scope, planner model/credential relation,
  generation mode, selected target, Knowledge candidate와 contract version fingerprint를 절단 없이 제공해야 한다.
- ABC-FR-019: Planning context DTO에는 token, credential config, raw node parameter, position,
  workflow/node/edge/Knowledge resource ID, request ID와 operation ID를 직렬화 가능한 projection으로
  포함하지 않아야 한다. 명시적으로 선택된 node/edge identity만 non-serializable ephemeral HMAC input으로
  허용하며 key 생성 직후 폐기하고 cache value와 diagnostics에 전달하지 않아야 한다.

### Key and Scope

- ABC-FR-020: Redis key에는 raw request, safe summary, graph JSON, user/organization UUID를 plaintext로 포함하지 않아야 한다.
- ABC-FR-021: Key는 versioned canonical key material에 HMAC-SHA256을 적용해 생성해야 한다.
- ABC-FR-022: Key material은 organization, user, planner model, generation mode, context fingerprint,
  selected hint, Knowledge candidate fingerprint와 contract versions를 포함해야 한다. 명시적인 selected
  target 기반 modify는 실제 target identity를 HMAC input에만 포함해 서로 다른 target이 같은 key를 만들지
  않아야 한다. Knowledge candidate fingerprint는 권한과 lifecycle을 통과한 후보를 실제 resource identity로
  중복 제거하고, identity를 domain-separated HMAC으로 변환한 canonical projection을 안정 정렬해 계산해야
  한다. Safe metadata, hierarchy, lifecycle 또는 policy revision 변경은 fingerprint를 변경해야 하며 UI 순서,
  추천 순서와 request-scoped handle은 fingerprint에 영향을 주지 않아야 한다.
- ABC-FR-023: Timestamp와 request ID는 key material에서 제외해야 한다.
- ABC-FR-024: 초기 cache entry는 organization+user scope를 넘어서 재사용하지 않아야 한다.
- ABC-FR-025: HMAC key가 없거나 유효하지 않으면 cache를 비활성화하고 Planner 경로를 유지해야 한다.
- ABC-FR-026: Cache envelope는 final key digest와 canonical payload를 domain-separated HMAC으로 묶고 constant-time으로 검증해야 한다.

### Cache Value

- ABC-FR-030: Cache value는 별도 version을 가진 strict `CachedIntentPlan` schema로 검증해야 한다.
- ABC-FR-031: Value에는 request/draft type, ordered capability, deterministic logical step reference,
  typed placement, closed integration action, closed Knowledge requirement enum, 순서 있는 canonical
  `topic_ref`와 `parameter_guidance_refs`의 logical step/Catalog `parameter_key`/closed guidance template ref만 포함할 수 있다.
- ABC-FR-032: Value에는 graph, 좌표, UUID, operation ID, credential, parameter value, raw prompt,
  raw provider response, 자유 형식 summary/topic/guidance, KB/Collection identity 또는 opaque handle을 포함하지 않아야 한다.
- ABC-FR-033: Canonical topic/guidance registry는 server-owned versioned explicit alias/template table이어야 한다.
  Ref는 `topic.<slug>.v1`, `guidance.reason.<slug>.v1`, `guidance.input.<slug>.v1` namespace를 사용하되
  형식이 아니라 `CachedIntentPlan` closed enum membership으로 codec에서 검증해야 한다. Registry manifest는
  이 enum을 빠짐없이 한 번씩 구현해야 하며 누락·초과 ref, ref/alias/canonical text 중복, alias의 다중 ref
  매핑, 지원하지 않는 template placeholder와 빈 input-type applicability는 startup/static validation에서
  거부해야 한다.
  Projection은 provider의 모든 topic과 `reason`/`input_guidance`가 이 table에 정확히 매핑될 때만 성공하며
  embedding, fuzzy 또는 의미 유사도 매핑을 사용하지 않아야 한다. Rehydration은 `topic_ref`를 고정
  `query_topics` 문자열로, guidance ref를 registry의 고정 template과 현재 Catalog의 allowlisted
  parameter metadata만으로 렌더링해야 한다. 렌더링 문자열이나 자유 형식 template argument는 value에
  저장하지 않아야 한다. 같은 `canonical_text_registry_version`은 current `full_safe_message`를 입력으로 하는
  `summary.current_safe_message.v1` projection descriptor와 capability별 canonical step purpose의 exact table도 함께
  versioning해야 한다. Summary/purpose 문자열은 value에 저장하지 않는다. Summary는 plan의 request/draft
  type이 아니라 매 요청의 transient safe context에서, purpose는 logical capability에서 파생해야 하며 exact
  v1 descriptor와 membership은 MBA-343 internal API contract를 따라야 한다.
- ABC-FR-034: Decode, schema, size, version, key binding 또는 payload MAC validation에 실패한 value는 삭제 가능한 miss로 처리해야 한다.
- ABC-FR-035: Value에는 request/session ID와 raw audit payload 또는 audit metadata 원문을 포함하지 않아야 한다.
- ABC-FR-036: Strict codec는 allowlisted schema 밖의 중첩 field와 forbidden key pattern을 decode와 encode 양쪽에서 거부해야 한다.

### Hit Revalidation and Rehydration

- ABC-FR-040: Cache hit 전에 선택된 planner model과 credential의 현재 관계, 상태와 use 권한을 다시 검증해야 한다.
- ABC-FR-041: Hit는 현재 server-loaded workflow context를 사용해 target을 다시 resolve해야 한다.
- ABC-FR-042: Node/edge UUID와 GraphMutation operation ID는 현재 요청에서 새로 발급해야 한다.
- ABC-FR-043: 현재 canonical text registry와 Catalog template/schema로 topic/guidance와 graph를 materialize하고 structured request와 graph validation을 다시 수행해야 한다.
- ABC-FR-044: Cache hit와 miss는 동일한 GraphMutation, CAS save, acknowledgement와 audit 경계를 사용해야 한다.
- ABC-FR-045: Context 또는 version이 key와 다르면 stale plan을 고쳐 쓰지 않고 miss 처리해야 한다.
- ABC-FR-046: Warm hit의 canonical rehydration이 실패하면 hit를 폐기하고 기존 Planner 경로를 최대 한 번 수행해야 한다.
- ABC-FR-047: Cold miss에서 provider 호출 뒤 canonical rehydration이 실패하면 원본 extraction 사용,
  Planner 재호출, cache put, GraphMutation과 workflow save를 수행하지 않아야 한다.
- ABC-FR-048: Cold-miss rehydration failure도 이미 수행한 provider와 semantic repair usage를 ADR-0055에 따라 기록하고,
  기존 clarification, stale, permission, validation 또는 intent failure로 종료해야 한다.

### Knowledge and Protected Resources

- ABC-FR-050: Cache는 Knowledge requirement, 순서 있는 closed `topic_ref`와 typed timing/placement만 보존할 수 있으며 current resolver에는 registry가 렌더링한 `query_topics`만 전달해야 한다.
- ABC-FR-051: Hit 시 현재 organization, Collection route, KB use 권한, lifecycle과 operational readiness로 후보를 다시 계산해야 한다.
- ABC-FR-052: Candidate와 selection handle은 현재 resolution에서 새로 발급해야 한다.
- ABC-FR-053: 현재 후보가 없어지거나 ambiguity가 생기면 cache plan을 강제 적용하지 않고 기존 empty/clarification 경계로 전환해야 한다.
- ABC-FR-054: Cache hit는 credential, Knowledge 또는 다른 protected resource의 과거 authorization decision을 재사용하지 않아야 한다.

### Failure, Concurrency and Lifecycle

- ABC-FR-060: Redis timeout, unavailable, decode error와 oversized payload는 safe miss로 처리해야 한다.
- ABC-FR-061: Cache adapter 오류는 Agent Builder API의 실패 상태가 되어서는 안 된다.
- ABC-FR-062: 동일 key의 동시 miss는 bounded single-flight lease로 중복 호출을 제한해야 한다.
- ABC-FR-063: Lease 대기 중 DB transaction과 workflow/session row lock을 유지하지 않아야 한다.
- ABC-FR-064: Lease owner가 cache value 없이 정상 종료하면 lease를 즉시 해제하고 follower에
  `owner_completed_without_value`를 알려 즉시 Planner로 진행시켜야 한다. 완료 신호와 release는 owner token과
  lease generation을 검증해 원자적으로 수행하고 follower는 자신이 관찰한 generation과 일치하는 신호만
  사용해야 한다. Owner가 비정상 종료해 신호를 남기지 못한 경우에는 TTL로 자동 해제되고 follower가 bounded
  wait 뒤 Planner로 진행할 수 있어야 한다.
- ABC-FR-065: Cache put 실패는 이미 검증된 현재 요청 결과를 실패시키지 않아야 한다.
- ABC-FR-066: Follower wait는 설정값과 현재 request의 남은 deadline 중 짧은 값으로 제한하며 초기 기본값은 45초, 최대값은 60초여야 한다.
- ABC-FR-067: Single-flight는 owner가 wait 안에 완료된 경우만 provider 단일 호출을 보장하고 timeout 뒤 중복 Planner 호출을 허용하는 best-effort 비용 최적화여야 한다.
- ABC-FR-068: Follower wait는 짧은 bounded slice로 나누고 각 slice와 cache value 사용 직전에
  cancellation/request version을 재검사해야 하며, cancellation 또는 version 변경이 value 도착보다 우선해야 한다.
- ABC-FR-069: Follower waiter는 process-wide non-blocking slot으로 제한해야 한다. 초기 기본 상한은 8,
  허용 범위는 1~64이며 overflow는 대기 없이 기존 Planner 경로로 진행하고 모든 종료 경로에서 slot을 반환해야 한다.

### Usage and Observability

- ABC-FR-070: Cache hit에는 provider 호출이 없으므로 planner usage reservation과 usage log를 생성하지 않아야 한다.
- ABC-FR-071: Miss와 semantic repair provider attempt는 기존 usage attribution을 그대로 유지해야 한다.
- ABC-FR-072: `hit|miss|bypass|error`와 allowlisted reason, latency, contract version metric을 기록해야 한다.
- ABC-FR-073: Metric, log, trace와 audit에는 raw request, cache key digest 전체, graph, candidate identity와 secret을 남기지 않아야 한다.
- ABC-FR-074: Cache hit가 workflow mutation audit를 제거하거나 대체하지 않아야 한다.
- ABC-FR-075: Cache hit는 provider 비용 reservation만 생략하며 Agent Builder request admission, rate limit과 foreground request 직렬화를 우회하지 않아야 한다.
- ABC-FR-076: Single-flight metric은 `owner|follower|overflow|none` role과 allowlisted
  `waiter_capacity_exceeded` reason을 지원하되 key, actor, organization과 request identity를 label로 사용하지 않아야 한다.
- ABC-FR-077: PostgreSQL audit는 cache 복구 저장소로 사용하지 않아야 한다. Audit에는 safe outcome,
  reason과 latency만 남기고 request, cache key/value와 plan payload를 저장하거나 Redis data loss 복구에 재생하지 않아야 한다.

### Configuration and Invalidation

- ABC-FR-080: Enabled flag, TTL, operation timeout, max payload, single-flight lease/wait와 HMAC key version을 환경 설정으로 제공해야 한다.
- ABC-FR-081: TTL은 bounded range로 검증하고 Redis entry 자체의 expiry로 적용해야 한다.
- ABC-FR-082: Normalizer, cache schema, Planner contract, Catalog, `canonical_text_registry_version`와 materializer version 변경은 namespace miss를 만들어야 한다.
- ABC-FR-083: Cache migration과 backfill은 수행하지 않아야 한다.
- ABC-FR-084: Cache를 비활성화하거나 Redis 데이터를 삭제해도 기존 Planner 기능이 정상 동작해야 한다.
- ABC-FR-085: Adapter는 cache 전용 Redis URL을 지원하고 production 운영 증거가 없는 설정에서는 cache serving을 비활성화해야 한다.
- ABC-FR-086: Cache adapter는 broad `SCAN`, `KEYS`, `FLUSHDB` 또는 `FLUSHALL`을 정상 운영 경로에서 사용하지 않아야 한다.
- ABC-FR-087: HMAC key와 credential이 포함될 수 있는 Redis URL은 기존 secret 주입 경계로만 제공하고 tracked 환경 파일, log, trace와 audit에 원문을 남기지 않아야 한다.
- ABC-FR-088: HMAC key version rotation은 dual-read, migration 또는 backfill 없이 새 namespace miss를 만들어야 한다.
- ABC-FR-089: HMAC key 누락·길이 부족 또는 Redis URL/configuration 오류는 safe reason code와 함께 cache만 비활성화하고 Planner를 유지해야 한다.
- ABC-FR-090: Cache feature flag 기본값은 false여야 하며 cache 전용 Redis URL이 없을 때 Celery
  broker/result Redis URL로 자동 fallback하지 않아야 한다.
- ABC-FR-091: 환경 판정은 저장소의 기존 `NODE_ENV`를 재사용해야 한다. `production`과 `staging` serving은
  명시적인 `AGENT_BUILDER_INTENT_CACHE_PRODUCTION_READY=true`를 함께 요구해야 하며,
  `development`, `test`와 미설정 환경의 cache 기본값은 비활성이어야 한다.
- ABC-FR-092: Production readiness gate는 전용 Redis URL, HMAC key/version, TTL, max payload,
  operation timeout, lease/wait와 waiter cap 설정을 모두 검증하고 하나라도 불완전하면 cache만 비활성화해야 한다.
- ABC-FR-093: Production readiness flag는 운영 attestation일 뿐 자동 검증 증거로 취급하지 않아야 하며,
  실제 Redis instance, secret/URL wiring, capacity/eviction, failure/network, monitoring/rollback과 staged rollout은
  별도 후속 운영 이슈와 evidence를 가져야 한다.
- ABC-FR-094: Production Redis 운영 이슈와 evidence는 cache 코드·필수 검증 완료와 분리하되,
  evidence가 없으면 production 또는 staging serving을 활성화하지 않아야 한다. Graph RAG 설계·구현은
  deterministic cache의 완료 조건으로 사용하지 않아야 한다.

## Non-Functional Requirements

- ABC-NFR-001: Cache lookup 오류는 설정된 짧은 timeout 안에 종료되어야 한다.
- ABC-NFR-002: Warm hit는 provider 호출 없이 deterministic하게 동일한 typed plan을 재구성해야 한다.
- ABC-NFR-003: Cache payload는 설정된 최대 byte 크기를 초과할 수 없다.
- ABC-NFR-004: Cache adapter는 application domain이 Redis client 타입에 직접 의존하지 않게 해야 한다.
- ABC-NFR-005: Cache key와 value serializer는 canonical order를 사용해 process 간 동일 결과를 내야 한다.
- ABC-NFR-006: Cache가 꺼진 상태에서 기존 Agent Builder unit/service 동작이 바뀌지 않아야 한다.
- ABC-NFR-007: Cache는 전용 Redis URL만 사용하고 Celery broker/result Redis로 fallback하지 않아야 하며,
  TTL, payload 크기, operation timeout과 waiter 상한으로 cache resource 사용을 제한해야 한다. 실제 Redis
  workload의 용량, eviction과 가용성 검증은 후속 운영 rollout 범위다.
- ABC-NFR-008: 내부 live benchmark는 GPT-5.5와 동일한 actor, organization, credential/model relation,
  generation mode와 초기 logical graph context를 사용해 cache 전후를 paired comparison해야 한다.
- ABC-NFR-009: Benchmark는 planning latency와 end-to-end latency를 별도 surface로 측정해야 한다.
- ABC-NFR-010: Benchmark는 cache disabled, cold miss, exact warm hit, 승인된 normalization warm hit,
  semantic bypass와 negative control을 서로 다른 comparison group으로 기록해야 한다.
- ABC-NFR-011: 각 회차의 성공/실패, latency, benchmark cache outcome
  (`disabled|hit|miss|bypass|error`), provider/repair call count와 validation 결과를
  원본 `runs.csv`에 보존하고, 실패를 성공 latency 평균과 분리해야 한다. `disabled`는 cache-off
  baseline 회차에만 사용할 수 있고 cache-on 회차는 실제 `hit|miss|bypass|error`를 기록해야 한다.
- ABC-NFR-012: Benchmark는 평균, P50, P95, 표준편차, 최소·최대, 실패율, paired 단축 시간,
  개선율과 speedup을 계산하고 모든 회차가 보이는 graph와 요약 결과를 생성해야 한다.
- ABC-NFR-013: Live provider latency는 CI 절대 시간 gate로 사용하지 않는다. Warm hit의 provider call 0회,
  예상 hit/miss/bypass, 결과 validation과 cache on/off materialization parity를 deterministic gate로 사용한다.
- ABC-NFR-014: Dataset 외 결과물에는 raw 자연어, access token, API key, credential config/ID,
  user/organization/workflow ID와 provider payload를 기록하지 않아야 한다.
- ABC-NFR-015: Exploratory benchmark 산출물은 ignored 경로에 두고, PPT/README에서 인용하는 최종 safe
  bundle은 `docs/features/agent-builder-cache/benchmarks/<benchmark-id>/`에 Git으로 보존해야 한다.
  `<benchmark-id>`는 실행 날짜, git SHA, dataset/cache contract version으로 식별해야 한다. Bundle의
  `README.md`는 핵심 요약과 회차별 `runs.csv`, graph 및 JSON/CSV summary의 상세 상대 경로를 모두 기록해야 한다.
- ABC-NFR-016: 최종 summary는 warm-hit와 cold-miss 측정값으로 hit rate 25%, 50%, 75%의 planning과
  end-to-end 예상 평균을 계산하고 이를 실제 측정이 아닌 모델링 추정치로 표시해야 한다.

## Exclusions

- Graph Template RAG, embedding, vector index, semantic similarity와 reranking
- 의미 기반 동등성 판정, Graph RAG retrieval/ranking 품질과 의미 유사 요청의 cache hit 성능 주장
- 완성 graph와 GraphMutation operation replay cache
- 사용자 간 또는 organization 간 shared semantic cache
- 신규 public endpoint와 DB table
- Planner prompt, repair 횟수 또는 supported capability 정책 변경
- Credential 원문 수집·저장
- Production 전용 Redis instance 생성, Helm/Kubernetes rollout, 공유 Redis capacity·eviction 검증과 capacity planning
- Production Redis secret/URL wiring, failure/network 검증, monitoring/rollback과 staged rollout

## Completion Criteria

- 모든 functional requirement가 코드 또는 테스트 증거에 연결된다.
- 구현 evidence matrix가 각 functional requirement를 담당 작업, Test ID, 예상/실제 파일과 실행 결과에 연결한다.
- Normalization collision, secret boundary, permission revalidation, Knowledge reissue와 Redis fail-open 테스트가 통과한다.
- Cache hit에서 provider와 usage recorder가 호출되지 않고 miss/repair 기록은 회귀하지 않는다.
- Cache on/off가 동일한 downstream graph validation, CAS와 acknowledgement 계약을 사용한다.
- GPT-5.5 live latency runner와 report generator가 cache 전후 paired data, graph와 summary를 생성한다.
- 실행 환경이 준비되지 않아 live 측정을 수행할 수 없으면 runner/report 구현 결과와 blocked evidence를 Test
  Matrix에 남길 수 있지만, 이를 cache 기능의 최종 검증 완료로 처리하지 않는다.
- Cache 기능의 최종 benchmark 검증에는 comparison group별 30회 live 측정과 최종 benchmark bundle이 필요하다.
- PPT/README에서 사용한 최종 benchmark bundle과 25/50/75% modeled hit-rate 결과가
  `docs/features/agent-builder-cache/benchmarks/<benchmark-id>/`에 안전하고 재현 가능한 식별자로 고정된다.
- Production Redis 운영 evidence 전에는 production/staging serving을 활성화하지 않는다. Production Redis
  운영과 Graph RAG 설계·구현은 deterministic cache 코드의 필수 검증 완료와 별도로 추적한다.
- 관련 문서와 실제 구현이 검증된 revision에서만 `Verified Against`를 추가한다.
