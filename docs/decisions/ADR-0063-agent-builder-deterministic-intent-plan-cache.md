# ADR-0063: Agent Builder Deterministic Intent Plan Cache

Status: Accepted

## Context

Agent Builder는 자연어 요청을 provider LLM으로 구조화하고, schema와 semantic
규칙을 통과한 결과만 workflow materialization에 사용한다. 동일하거나 명시적으로
동등한 요청도 매번 provider를 호출하면 지연과 비용이 반복되고, 결과의 작은 편차가
후속 graph 생성에 영향을 줄 수 있다.

반대로 자연어 유사도만으로 과거 결과를 재사용하면 다른 대상, 순서, 부정 표현,
parameter 값 또는 권한 컨텍스트를 같은 요청으로 오인할 수 있다. 완성 graph를
캐시하면 node/edge UUID, protected resource reference, GraphMutation operation과
CAS 기준까지 과거 요청에서 재생하는 더 큰 위험이 생긴다.

## Decision

1. Agent Builder 앞에 삭제 가능한 L1 최적화인 deterministic intent plan cache를 둔다.
   캐시는 의미 판단의 source of truth가 아니며 현재 Planner, Catalog, validation,
   GraphMutation, CAS와 acknowledgement 경계를 대체하지 않는다.
2. 정규화는 Unicode NFKC, 공백, 영문 대소문자와 승인된 전체-token/phrase 별칭만
   처리한다. 위치, 순서, 부정, 숫자, 인용문, node label과 parameter 값은 보존한다.
   규칙이 요청 전체를 안전하게 설명하지 못하면 cache를 우회하고 Planner를 호출한다.
   정규화 가능 여부는 versioned normalization profile로 판정한다. Profile은 허용 alias,
   의미 없는 조사·정중 표현, 위치 표현, token/phrase boundary, 인용·숫자·부정·parameter-like
   span의 보호 우선순위와 미인식 잔여 표현의 bypass를 고정한다. Profile 변경은 normalizer
   version 변경과 adversarial regression corpus 갱신을 요구한다.
   Cache normalization과 context fingerprint는 Planner prompt용으로 축약된 summary나
   일부 node projection을 재사용하지 않는다. 허용된 전체 safe request와 전체 logical
   topology의 canonical digest를 만들 수 없거나 중간 절단이 발생하면 lookup과 store를
   모두 우회한다.
   `AgentBuilderService`가 cache coordinator에 전달할 transient planning context DTO를
   소유한다. 이 DTO에는 전체 safe request, 전체 logical topology, actor/organization scope,
   검증된 planner model과 credential relation fingerprint, generation mode, selected target과
   Knowledge candidate fingerprint, contract version만 포함한다. Workflow/node/edge/Knowledge
   resource ID, 좌표, raw node parameter, token, credential config, request/operation ID는 포함하지
   않는다. 단, 명시적으로 선택된 node/edge의 실제 identity는 서로 다른 편집 대상을 구분하기
   위해 non-serializable ephemeral key material로만 key builder까지 전달하고 즉시 HMAC 처리한다.
   Actor/organization과 selected target identity는 cache value, log, audit와 metric에 남기지 않는다.
   Knowledge candidate fingerprint는 active organization, Collection route, KB use 권한과 lifecycle을
   통과한 후보만 대상으로 한다. Fingerprint builder는 실제 resource identity로 후보를 먼저 중복 제거하고,
   identity를 domain-separated HMAC으로 즉시 변환한 뒤 resource kind, safe metadata digest, 허용된 hierarchy,
   lifecycle과 policy revision을 canonical projection에 포함한다. Projection은 UI 표시 순서와 무관하게 안정
   정렬하고 최종 digest만 planning context와 cache key material에 전달한다. 실제 Collection/KB ID, 이름,
   request-scoped handle과 추천 표시 순서는 직렬화 가능한 DTO, cache value, log, audit와 metric에 포함하지 않는다.
3. 정규식이나 tokenizer는 LLM 결과를 수정하거나 모호한 표현을 capability로
   강제 변환하지 않는다. 의미 유사도와 Graph Template RAG는 별도 기능이다.
4. Cache value는 자유 텍스트와 요청별 식별자를 제거한 versioned
   `CachedIntentPlan`이다. Closed enum, 순서가 있는 capability, deterministic logical
   reference와 versioned canonical text registry의 reference만 허용한다. Knowledge requirement는
   순서 있는 `topic_ref`를, `parameter_guidance_refs`는 logical step, Catalog `parameter_key`,
   `reason_template_ref`와 `input_guidance_template_ref`를 저장할 수 있다. Registry가 렌더링한
   topic, reason과 input guidance 문자열 자체는 저장하지 않는다. 완성 graph, 좌표,
   workflow/node/edge UUID, GraphMutation operation, request/session ID, credential, 실제 parameter 값,
   raw provider 응답과 raw audit payload도 저장하지 않는다.
   Projection은 provider가 반환한 모든 Knowledge topic과 guidance를 versioned explicit alias/template
   table로 정확히 표현할 수 있을 때만 성공한다. Embedding, fuzzy 또는 의미 유사도 매핑은 사용하지 않는다.
   하나라도 표현할 수 없으면 해당 Planner 결과는 store-ineligible이며 원본 extraction을 기존 non-cache
   downstream으로 전달한다. 성공한 projection은 최초 miss와 hit 모두 현재 registry와 Catalog에서 같은
   canonical topic/guidance 문자열로 렌더링한다. Registry version은 key namespace와 plan contract version에
   `canonical_text_registry_version`으로 포함하며 ref는 versioned namespace와 current manifest exact
   membership으로 검증한다.
   Rendered `intent_summary`도 cache value에 저장하지 않는다. Cache-eligible request의 summary는 매 요청의
   transient planning context에 있는 전체 `full_safe_message`를 `summary.current_safe_message.v1`로 projection해 만든다.
   이 projection은 현재 `_safe_summary(request.message, limit=240)`의 whitespace collapse, fail-closed
   redaction과 240-code-point 상한을 versioned regression contract로 고정한다. Empty 또는 redaction marker가
   남은 결과는 cache admission을 통과할 수 없다. Provider가 반환한 자유 형식 `intent_summary`는
   cache-eligible cold miss의 canonical downstream source로 사용하지 않으며, plan의 request/draft type만으로
   고정 문장을 선택하지도 않는다. 같은 current safe request의 cold miss와 hit는 같은 summary를 만들고,
   서로 다른 safe request는 각 current request에서 독립적으로 summary를 재구성한다.
5. Cache key는 plaintext 요청이 아니라 canonical key material의 HMAC-SHA256이다.
   Key material에는 organization, user, 선택한 planner model, generation mode,
   safe workflow context, selected target, 현재 Knowledge 후보 집합과 contract version을
   포함한다. 명시적인 selected node/edge 기반 modify에서는 실제 target identity를 HMAC input에만
   포함해 구조가 같은 서로 다른 target도 다른 key를 만든다. 초기 범위는 organization+user
   scope이며 사용자 또는 서로 다른 selected target 간에 entry를 공유하지 않는다.
   Cache value는 key digest와 canonical payload를 domain-separated HMAC으로 묶은
   authenticated envelope로 저장한다. MAC 불일치와 다른 key에서 옮긴 value는
   invalid miss로 폐기한다. HMAC key와 credential이 포함될 수 있는 Redis URL은 기존
   secret 주입 경계로만 제공하고 log, audit, trace와 tracked 환경 파일에 원문을 남기지
   않는다. Key version rotation은 dual-read 없이 새 namespace miss를 만든다.
6. Cache hit 전에도 선택한 model/credential의 현재 사용 가능성과 사용자 권한을
   검증한다. Hit 뒤에는 현재 server-loaded graph에서 target을 다시 resolve하고
   Catalog로 새 node/edge UUID와 operation ID를 발급한다.
7. Knowledge requirement, closed `topic_ref`와 typed placement만 cache할 수 있다. KB/Collection UUID,
   이름과 request-scoped opaque handle은 cache하지 않는다. 현재 권한과 lifecycle로
   후보를 다시 계산하고 handle을 새로 발급한다.
8. Schema와 semantic validation을 통과한 actionable plan만 저장한다. Clarification,
   unsupported, permission denial, stale state, provider/schema failure, secret-like 입력과
   explicit parameter value가 있는 요청은 저장하지 않는다. Repair가 성공했다면 최종
   valid plan만 저장한다.
   Cache admission을 통과한 miss도 final valid extraction을 `CachedIntentPlan`으로
   projection한 뒤 즉시 현재 request, canonical text registry와 Catalog에서 rehydrate한 canonical structured
   request를 downstream에 전달한다. Summary는 저장된 plan이 아니라 현재 request의 versioned
   `summary.current_safe_message.v1` projection에서 만들며 provider summary를 재사용하지 않는다. 따라서 최초 miss와 이후 hit는
   request-specific summary, step purpose,
   Knowledge recommendation의 `query_topics`, parameter guidance와 task를 포함해 같은 canonical
   materialization 입력을 사용한다. Provider topic/guidance가 canonical reference로 완전히 projection되지
   않는 경우는 rehydration failure가 아니라 store-ineligible non-cache 결과다.
   Warm hit rehydration이 실패하면 해당 hit를 버리고 정상 Planner 경로를 한 번 수행한다.
   반면 cold miss에서 provider 호출과 projection을 마친 뒤 canonical rehydration이 실패하면
   원본 LLM extraction을 우회 사용하거나 Planner를 다시 호출하지 않는다. 이 경우 cache put,
   GraphMutation과 저장을 수행하지 않고 이미 발생한 provider/repair usage는 기록한 뒤 기존
   clarification, stale, permission, validation 또는 intent failure 계약으로 종료한다.
9. Cache hit에는 provider 호출이 없으므로 planner usage reservation/log를 만들지 않는다.
   대신 raw key나 prompt를 포함하지 않는 hit/miss/bypass/error metric을 남긴다.
   Cache miss와 repair의 비용 귀속은 ADR-0055를 그대로 따른다.
   PostgreSQL audit는 cache 복구 저장소가 아니다. Audit에는 allowlisted cache outcome과 reason,
   latency 같은 추적 정보만 기록할 수 있고 request, cache key/value 또는 plan payload를 저장하지
   않는다. Redis data loss는 audit replay가 아니라 cold miss와 정상 Planner 호출로 복구한다.
10. Redis는 source of truth가 아니다. Timeout, 연결 실패, decode 실패, oversized
    payload와 version mismatch는 안전한 cache miss로 처리한다. 캐시 오류 때문에
    Agent Builder 요청을 실패시키지 않는다.
11. 동일 key의 동시 miss는 bounded single-flight lease로 중복 provider 호출을 줄인다.
    이는 correctness lock이나 provider 단일 호출 보장이 아니다. Lease를 획득하지 못한
    요청은 설정된 시간과 현재 request의 남은 deadline 중 짧은 시간까지만 기다린다.
    Owner가 그 안에 value를 저장하면 재사용하고, timeout, Redis 장애나 lease 만료 시
    기존 Planner 경로로 진행한다. 초기 follower wait는 기본 45초, 최대 60초로 제한하되
    짧은 bounded slice로 나누고 각 slice와 value 사용 직전에 cancellation/request version을
    다시 검사한다. Cancellation 또는 version 변경은 동시에 도착한 cache value보다 우선한다.
    Process-wide follower waiter slot은 기본 8개, 허용 범위 1~64개로 제한하고 non-blocking으로
    획득한다. 상한을 넘은 요청은 기다리지 않고 기존 Planner로 진행하며 모든 종료 경로에서
    slot을 반환한다. 이는 best-effort 비용 최적화이므로 overflow와 timeout의 중복 provider
    호출을 허용한다.
    Owner가 clarification, unsupported, provider failure 또는 다른 store-ineligible terminal result로
    정상 종료해 cache value를 만들지 못하면 lease를 즉시 해제하고 follower에
    `owner_completed_without_value`를 알린다. Follower는 configured wait를 끝까지 소비하지 않고 즉시
    기존 Planner 경로로 진행한다. 완료 신호와 release는 owner token 및 lease generation을 검증해
    원자적으로 수행하며 follower는 자신이 관찰한 generation의 신호만 사용한다. Owner의 비정상 종료로
    완료 신호를 남길 수 없는 경우에만 lease TTL과 bounded wait를 복구 경계로 사용한다.
    DB transaction이나 workflow row lock을 잡은 채 Redis 또는 provider를 기다리지 않는다.
12. Normalizer, cache schema, Planner contract, Catalog, canonical text registry, materializer와 HMAC key version을
    key namespace에 포함한다. Version 변경은 기존 entry를 읽지 않는 방식으로
    무효화하며 migration이나 cache backfill을 하지 않는다.
13. 기존 Agent Builder message response와 direct-edit API 계약은 변경하지 않는다.
    Cache는 Gateway 내부 application port와 Redis adapter로 구현하고 별도 DB table,
    public cache endpoint 또는 operation replay 저장소를 만들지 않는다.
14. Cache 구현은 전용 Redis URL을 주입할 수 있는 adapter/configuration 경계와 안전하지
    않은 production 설정에서 cache를 비활성화하는 gate까지만 구현한다. Cache feature flag의
    기본값은 false이며 Celery broker/result Redis URL로 자동 fallback하지 않는다. 환경 판정은
    저장소의 기존 `NODE_ENV`를 재사용한다. `production`과 `staging`에서 serving하려면 전용 Redis URL,
    HMAC key/version, 유효한 TTL/payload/timeout/waiter 설정과 명시적
    `AGENT_BUILDER_INTENT_CACHE_PRODUCTION_READY=true`가 모두 필요하다. `development`, `test`와
    미설정 환경에서는 cache가 기본 비활성이고, 명시적으로 활성화한 개발 cache의 Redis 장애도
    기존 Planner로 fail-open한다. 운영자는 외부 운영 검증을 마친 뒤에만 이 flag를 설정한다. 런타임은
    별도 evidence 저장소를 조회하지 않고 이 attestation과 나머지 configuration의 유효성을 판정한다.
    전용 Production Redis instance 생성, Helm/Kubernetes secret과 URL wiring, 용량·eviction,
    장애·네트워크, 모니터링·rollback과 단계적 rollout은 별도 후속 운영 이슈다.
    Cache 코드와 필수 검증은 후속 운영 이슈 번호와 독립적으로 완료할 수 있다. 운영 증거가 없으면 운영자가 attestation을
    설정하지 않아야 하며 production 또는 staging cache serving은 비활성으로 유지된다.
15. Cache lookup은 인증, active organization, Agent Builder permission, session/request admission과
    cancellation/version fence를 통과한 뒤 수행하고 provider usage reservation보다 앞에 둔다.
    Hit는 provider-specific 비용 reservation만 생략하며 요청 rate limit, foreground request
    직렬화, stale/canceled 판단과 workflow mutation audit를 생략하지 않는다.
16. Cache의 성능 효과는 GPT-5.5를 사용하는 내부 live benchmark로 검증한다. Benchmark는
    cache disabled baseline, cold miss, exact warm hit, 승인된 normalization warm hit,
    semantic bypass와 negative control을 분리하고 planning latency와 사용자 관점의
    end-to-end latency를 모두 기록한다. 동일 case의 전후 결과는 같은 초기 graph와
    permission/model context에서 paired comparison으로 측정한다. 회차별 원본 데이터,
    모든 회차가 보이는 vector graph와 요약 결과를 별도 산출물로 생성한다. 이는
    wall-clock latency를 CI의 절대 pass/fail 기준으로 만들지 않으며 provider call count,
    cache outcome과 validation 결과를 deterministic gate로 사용한다. 의미만 비슷한 요청은
    이 benchmark에서 bypass 안전성만 확인하고 Graph RAG 성능으로 해석하지 않는다.
    개발 중 exploratory 산출물은 ignored 경로에 두고, PPT와 README에서 인용하는 최종 safe
    bundle은 실행 날짜, git SHA, dataset/cache contract version으로 식별한다. 합성 dataset은
    추적할 수 있지만 report에는 prompt 대신 case ID만 남긴다. 최종 bundle은
    `docs/features/agent-builder-cache/benchmarks/<benchmark-id>/`에 고정하고 `README.md`, `runs.csv`,
    `summary.json`, `summary.csv`, `latency-comparison.svg`, `cache-hit-scenarios.svg`를 포함한다.
    Benchmark row의 `disabled` outcome은 cache-off baseline에만 허용하고 cache-on 회차는
    `hit|miss|bypass|error` 중 실제 결과를 기록한다. Bundle `README.md`는 핵심 요약과 함께
    동일 bundle 안의 회차별 원본 data, graph와 기계 판독 summary의 상대 경로를 모두 기록한다.
    Warm-hit와 cold-miss 측정 평균으로 hit rate 25%,
    50%, 75%의 planning/end-to-end 예상 평균을 계산하되 실제 측정이 아닌 모델링 추정치로
    명시한다.
17. 의미가 비슷하지만 결정적으로 동등하지 않은 요청은 deterministic cache hit로 처리하지 않는다.
    이들은 semantic bypass/negative control로만 측정한다. Embedding, vector index, graph example
    retrieval/ranking, confidence gate와 해당 latency는 별도 Graph RAG 설계·구현 범위가 소유하며
    deterministic cache의 구현 완료 조건으로 사용하지 않는다. 검색 결과는
    곧바로 cache hit처럼 적용하지 않고 Planner와 Schema Validator의 최종 생성·검증 입력으로만 사용한다.

## Consequences

- 반복되는 명시적 요청은 provider 호출 없이도 같은 typed planning 계약으로 진입할 수 있다.
- 모든 hit가 현재 권한과 graph를 다시 검증하므로 cache lookup 뒤에도 일정한 처리 비용은 남는다.
- 사용자별 scope와 보수적인 eligibility 때문에 초기 hit ratio는 제한될 수 있다.
- Provider topic/guidance가 canonical registry로 완전히 표현되지 않으면 기능 결과는 기존 non-cache 경로로
  유지되지만 해당 요청의 cache hit ratio는 낮아진다.
- Redis 데이터가 전부 사라져도 기능은 기존 Planner 경로로 정상 동작한다.
- Strict schema뿐 아니라 authenticated envelope가 key/value 교체와 변조를 차단한다.
- Production Redis 분리와 용량 검증은 후속 운영 이슈이며, 완료 전에는 cache를 비활성화한다.
- 의미가 비슷하지만 결정적으로 동등하지 않은 요청은 이 ADR의 대상이 아니며 Graph RAG
  후속 기능에서 별도 confidence gate로 다룬다.
- Live latency 결과는 provider 상태와 실행 환경에 영향을 받으므로 환경, revision, 반복 횟수와
  실패율을 함께 기록해야 하며 평균값만으로 correctness를 판단하지 않는다.

## Rejected Alternatives

- **완성 graph cache**: stale UUID, protected reference와 operation replay 위험 때문에 거부한다.
- **Raw prompt를 Redis key/value에 저장**: secret과 내부 정보 노출 위험 때문에 거부한다.
- **Sanitized topic/guidance 문자열을 value에 저장**: secret/PII 부재와 canonical parity를 증명할 수 없어 거부한다.
- **Provider topic/guidance를 버리고 현재 request/Catalog에서만 추정**: provider가 만든 KB ranking·guidance
  의미를 동일하게 복원할 수 없어 거부한다.
- **Embedding 유사도만으로 direct hit 처리**: 의미 충돌을 cache correctness 문제로 숨기므로 거부한다.
- **Redis fail-closed**: 비핵심 최적화 장애가 핵심 생성 흐름을 차단하므로 거부한다.
- **사용자 간 cache 공유**: 권한 fingerprint가 완전히 검증되기 전에는 정보 경계가 넓어져 거부한다.
- **Schema validation만으로 Redis value 신뢰**: 다른 key의 valid payload 교체를 잡지 못하므로 거부한다.
