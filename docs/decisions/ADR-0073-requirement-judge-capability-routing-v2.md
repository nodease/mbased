# ADR-0073: Requirement Judge와 Capability Routing V2 책임 분리

Status: Accepted

Related ADRs: [ADR-0038](ADR-0038-workflow-aware-adaptive-routing.md), [ADR-0059](ADR-0059-judge-bootstrap-incremental-routing.md), [ADR-0064](ADR-0064-provider-execution-capability-boundary.md), [ADR-0066](ADR-0066-nested-llm-canonical-node-location.md), [ADR-0067](ADR-0067-production-https-and-operation-bound-outbound.md), [ADR-0069](ADR-0069-provider-usage-durable-ledger.md), [ADR-0071](ADR-0071-rag-query-embedding-provider-capability.md), [ADR-0072](ADR-0072-outbound-proxy-only-network-enforcement.md)

> 이 문서의 채택은 V2 production activation 승인이 아니다.
> 아래 활성화 게이트와 MBA-372 통합 검증을 통과하기 전까지 V2는 기본 비활성으로 유지한다.
> ADR-0059의 versioned V1 row와 실행 이력은 호환·rollback을 위해 보존한다.

## 배경

[ADR-0059](ADR-0059-judge-bootstrap-incremental-routing.md)는 초기 운영 요청마다 Judge가
현재 후보 모델 중 하나를 선택하고, 충분한 label이 쌓이면 로컬 분류기가 그 선택을 재현하는
`judge_bootstrap_incremental_v1`을 결정했다. MBA-340은 Judge가 모델 ID 대신 요청에 필요한
능력을 판정하고 서버가 현재 모델 capability와 운영 evidence를 비교하는 V2를 별도 전략으로
제안했다.

현재 구현은 Requirement Judge와 서버 selector를 도입했지만 V2 의미를 V1 strategy ID 아래에서
실행한다. 누락된 `task_type`은 `generate`로 간주되고, learner 전환 기준은 ADR-0059와 다르며,
cache·learner·trace가 현재 routing contract에 완전히 결합되지 않았다. 따라서 현재 코드는 목표
방향의 일부를 구현했지만 V1도 V2도 완결된 상태가 아니다.

2026-07-20부터 2026-07-22까지의 실험은 현재 selector가 일부 고정 baseline보다 비싸거나 느리고
품질도 낮을 수 있으며 local takeover와 locked holdout 비교가 완결되지 않았음을 보여준다. 일부
실험에는 source SHA, dirty state, dataset hash, catalog·pricing·selector·Judge·learner version이
없으므로 production 활성화 근거로 사용할 수 없다.

| 검토 근거 | 직접 관찰 | 허용되는 결론 |
| --- | --- | --- |
| [Contract isolation 80](../../reports/model-routing/runs/judge-first/2026-07-20__contract-isolation-80__judge-gpt-5.4-mini/report.md) | Auto는 mid fixed보다 총비용·평균 LLM node 시간이 높고 품질 통과율이 낮았다 | 현재 selector에 경제성·품질 admission이 필요하다 |
| [Learner blind 40](../../reports/model-routing/runs/judge-first/2026-07-21__learner-v2-blind-50__auto-high-mid/report.md) | Auto가 mid fixed보다 비싸고 느리며 품질 통과율도 낮았다 | 현재 learner/selector 결합을 production 우위로 볼 수 없다 |
| [Learning 100](../../reports/model-routing/runs/judge-first/2026-07-22__routing-learning-100__benchmark-20/learning/report.md) | Judge가 100/100 호출됐고 fixed baseline·독립 품질 평가는 포함되지 않았다 | Learner 효과나 100-label 기준의 타당성을 입증하지 못한다 |
| [Auto vs high 25](../../reports/model-routing/runs/judge-first/2026-07-22__auto-vs-high-30__generative-safe-fallback-v4/benchmark/report.md) | Auto는 high fixed보다 저렴했지만 더 느리고 품질 통과율이 낮았다 | 비용만으로 activation을 승인할 수 없다 |

이 결과는 selector·learner 개선 필요성의 진단 근거다. Manifest와 locked holdout 조건을 충족하지
않으므로 V2 폐기, learner threshold 변경 또는 production activation의 승인 근거로 사용하지 않는다.

이 결정은 V2 방향을 폐기하거나 Judge 직접 선택으로 되돌리지 않는다. V2를 독립된 versioned
전략으로 정의하고, 구현·실험·승인 경계가 모두 준비될 때까지 기본 비활성으로 유지한다.
ADR-0073의 계약 권위는 ADR-0059의 신규 runtime 정책을 대체하되, V1 row와 실행 이력은
역사·호환 데이터로 보존한다.

## 결정

### 1. Requirement Judge는 요구 능력만 판정한다

V2의 Requirement Judge는 현재 rubric version에 따른 bounded 결과만 반환한다.

- `task_complexity`: 0부터 3
- `decision_impact`: 0부터 3
- `evidence_synthesis`: 0부터 3
- `confidence`: 0부터 1
- allowlist 기반 `ambiguity_flags`
- allowlist 기반 `reason_codes`

`judge_contract_version`과 `rubric_version`은 모델 응답 필드가 아니다. Routing application은 provider
호출 전에 두 값을 server-owned immutable invocation envelope에 고정하고, 파싱한 bounded result와
함께 provenance로 결합한다. 모델이 두 version을 echo하거나 다른 version을 주장하면 unknown field
contract failure로 처리하며 그 값을 신뢰하지 않는다. Model response body는 위 bounded field만
허용한다.

모델 ID, 후보 목록, 가격, credential, provider private state와 policy ID는 Judge 입력·출력에서
제외한다. Output schema, tool use, context window와 외부 부수효과처럼 서버가 결정적으로 계산할 수
있는 값은 structural fact와 hard gate로 처리한다.

Judge 요청은 실행 중에만 존재하는 bounded projection이다. 인증 실행의 execution subject 또는
ADR-0018 Anonymous Public Audience는 Knowledge/source 데이터 접근 범위에만 사용하고 credential
principal로 재해석하지 않는다. 호출 전에 ADR-0064의
deployment policy에서 server-derived한 credential principal, exact Judge model policy, provider lifecycle과
조직의 data egress 정책을 검증하고 승인된 provider에만 전달한다. Public·schedule·system actor를
credential principal로 승격하거나 owner credential을 fallback하지 않는다. Raw RAG chunk/document,
credential, 다른 tenant 정보와 private candidate state는 전달하지 않으며 request/response 원문은
trace, audit, cache, label과 실험 report에 영속하지 않는다. Judge provider 호출도 ADR-0064의
server-issued capability와 capability-bound budget admission을 통과해야 한다. Judge, primary와 fallback
attempt는 같은 Billing Principal에 귀속하되 purpose와 attempt identity를 분리하고 ADR-0069 원장에
실제 사용량을 각각 종결한다.

RAG query embedding은 ADR-0071의 별도 `purpose=query_embedding`, exact embedding-model policy와
capability를 사용한다. V2의 Judge/main-generation/candidate credential policy로 query embedding을
승인하거나 query embedding policy를 작업 모델 권한으로 재사용하지 않는다. 권한 있는 retrieval이
완료된 뒤의 bounded safe facts만 Requirement Judge에 전달하며 query embedding policy/capability 실패를
V2 stored default/fallback으로 우회하지 않는다.

이 결정은 ADR-0064의 credential authority를 대체하지 않는다. ADR-0064의 현재 node별 단일 active
exact-model policy는 다중 모델 후보의 사용 권한이 아니다. Capability-required V2를 effective strategy로
사용하려면 별도 Accepted ADR과 구현이 Judge model 및 각 primary/fallback 후보를 model-bound policy로
명시하고, 같은 server-derived credential principal·organization·deployment version에서 current relation과
credential `use`를 검증해야 한다. 이 정책이 없으면 V2 Judge와 selector를 실행하지 않고 versioned
non-V2 policy 또는 stored safe path로 전환하며, 그 경로도 없으면 provider I/O 전 typed failure로 닫는다.

축을 추가·삭제하거나 점수 의미를 바꾸는 것은 rubric version 변경이다. 이 변경은 learner,
cache와 평가 contract를 회전시킨다. 첫 Judge가 계약에 맞는 응답으로 성공했지만 confidence가
versioned policy 기준보다 낮은 경우에만 동일한 bounded schema의 secondary Judge를 최대 한 번 호출할
수 있다. Secondary Judge도 모델을 선택하지 않으며 호출 조건, 병합 규칙, 비용과 지연 budget을
policy에 포함한다. 첫 Judge가 `provider_started` 뒤 `outcome_unknown`으로 종결되면 같은 routing
decision에서 Judge를 자동 재시도하거나 secondary Judge로 대체하지 않는다. 별도로 versioning된
current-valid non-V2 stored safe path가 있으면 독립 work-model attempt의 current
capability·budget·egress admission과 durable intent를 거친 뒤 사용할 수 있고, 그렇지 않으면 provider
호출 전에 typed failure로 닫는다.

### 2. 서버가 현재 상태를 기준으로 최종 모델을 선택한다

서버는 다음 순서를 지킨다.

1. 현재 실행의 데이터 접근 범위와 organization scope를 확정하고, ADR-0064에서 server-derived한
   credential principal 및 각 exact-model policy의 current relation·credential lifecycle·provider
   availability와 current global workflow model execution eligibility를 검증해 사용할 수 없는 후보를
   제외한다. 날짜 고정 ID나 정책상 실행 제외 모델을 저장 graph/policy가 참조해도 이 gate를 우회하지
   못한다.
2. context window, structured output, tool use, output/effect contract와 안전 하한을 hard gate로
   적용한다.
3. 동적 라우팅의 기대 이익이 routing overhead와 risk margin을 넘는지 판정한다.
4. current contract와 일치하는 local classifier 또는 Requirement Judge에서 요구 능력을 얻는다.
5. 요구 능력과 minimum quality evidence를 충족하지 못한 후보를 제외한다.
6. 남은 후보를 예상 전체 비용, 예상 지연과 품질 제약으로 비교한다.
7. 동률은 versioned canonical model order로 해소한다.

인증 interactive 실행의 데이터 접근 범위는 current `execution_subject`다. Subject가 없는 public,
webhook, schedule, API와 system 실행은 ADR-0018의 Anonymous Public Audience로 낮추며 active public
Collection/KB와 source-managed public exposure approval을 통과한 근거만 사용할 수 있다. Owner,
deployment creator, audit actor, `user_id` 또는 credential principal을 synthetic execution subject로
만들지 않는다. 향후 service account를 쓰려면 별도 Accepted ADR과 immutable deployment binding이
먼저 필요하며, 그 전에는 private Knowledge 접근을 승인하지 않는다. Requirement Judge에는 이 데이터
범위와 ADR-0071 query-embedding 경계를 통과한 bounded safe facts만 전달한다. 허용 근거가 없으면
Knowledge의 safe no-result 또는 node의 versioned RAG failure policy를 적용하고, 거부된 resource의
ID·이름·정확한 수를 Judge, trace와 audit에 노출하지 않는다.

Cache, preview, Client 또는 prompt가 전달한 모델 ID와 task hint는 authorization capability가
아니다. 각 primary·fallback provider 호출 직전에도 authorization, credential lifecycle과
provider availability를 다시 검증하고 별도 capability-bound budget admission을 수행한다. Judge
예산을 승인했다는 사실은 primary/fallback 예산 승인이 아니며, budget unavailable·exceeded 또는
가격 불명 상태에서는 해당 provider I/O를 시작하지 않는다.

### 3. task intent 누락은 `unspecified`다

Canonical 내부 값은 다음 enum을 사용한다.

```text
unspecified | classify | extract | transform | generate
```

- 유효한 기존 `task_type`은 canonical `task_intent`로 정규화한다.
- 필드가 없는 legacy graph는 `unspecified`다.
- 누락값을 `generate`로 채우거나 prompt keyword만으로 영속하지 않는다.
- Legacy Cost Optimizer 값은 `classify -> classify`, `extract -> extract`,
  `summarize -> transform`, `generate -> generate`로 정규화한다. `reason`은 추론 난이도이지 작업
  intent가 아니므로 `unspecified`로 두고 Judge 요구 축과 server structural fact가 처리한다.
- `model_routing_context.node_task`처럼 자유 문자열인 legacy hint는 exact allowlist 값일 때만 사용하고
  그 밖의 값은 `unspecified`다. 도메인명·노드명·prompt keyword로 intent를 추측하지 않는다.
- task intent는 routing hint이며 authorization 또는 안전 capability가 아니다.
- explicit `generate` 또는 server가 확인한 human-facing 자유형 출력에만 generative floor를
  적용한다.
- output schema, 외부 부수효과와 high-risk structural fact는 task intent보다 우선한다.

### 4. V2는 독립 strategy로 versioning한다

- `judge_bootstrap_incremental_v1`의 의미를 V2로 바꾸지 않는다.
- V1 policy, cache와 learner를 V2로 자동 재해석하지 않는다.
- V2 strategy ID는 `capability_routing_v2`다.
- V2는 별도 feature flag와 명시적 immutable policy version으로 기본 비활성 도입한다.
- strategy, activation profile, selector, learner와 cache contract가 모두 current일 때만 실행한다.
- unknown 또는 stale version은 V2 Requirement Judge 호출 전에 별도로 versioning된 current-valid
  non-V2 policy, stored safe path 또는 typed failure로 닫는다.
- 명시적으로 versioned된 `judge_bootstrap_incremental_v1`은 ADR-0059의 legacy Selection Judge가
  current authorization으로 거른 후보 중 모델을 직접 선택하는 V1 의미를 유지한다. V2 Requirement
  Judge와 wire schema, cache, learner 및 source enum을 공유하지 않는다.
- 현재 DB row는 `active_policy.strategy_id` 외 immutable `strategy_contract_version`이 없어 어떤 코드
  의미로 발행됐는지 증명할 수 없다. 이 row를 생성 시각이나 현재 코드로 V1/V2에 자동 분류하지
  않는다. `legacy_ambiguous`로 취급해 dynamic Judge/local/cache routing을 끄고 current gate를 통과한
  stored safe model만 사용한다. Safe model도 없으면 provider 호출 전 typed failure로 닫는다.
- Legacy ambiguous row를 versioned V1 또는 V2로 자동 migration하지 않는다. Workflow deploy 권한자가
  current graph와 candidate scope를 다시 검증해 재배포/reissue해야 한다. Historical trace와 usage는
  기존 strategy 문자열을 그대로 보존한다.
- V2 activation profile은 별도로 검증·발행한 non-V2 rollback policy version을 명시해야 한다.
  Profile 부재·stale 상황에서 임의의 V1 row를 찾거나 V2 artifact를 V1로 재해석해 fallback하지
  않는다. Rollback target은 재검증·발행한 명시적 V1 또는 별도 non-V2 stored policy만 허용한다.

### 5. 권한·구조·품질 gate는 최적화보다 우선한다

Authorization, credential, provider capability, context/output/effect contract와 minimum quality는
가중치로 상쇄할 수 없는 hard gate다. 비용과 지연은 이 gate를 통과한 후보끼리만 비교한다.
Client가 낮은 task intent를 보내거나 prompt 문구를 조작해도 human-facing, 금전·권한·보안·법무
또는 외부 부수효과 하한을 낮출 수 없다.

### 6. 동적 라우팅에는 경제성 admission을 적용한다

동적 라우팅 예상 이익이 routing overhead와 risk margin을 넘지 못하면 Judge를 호출하지 않고
검증된 stored baseline을 사용한다.

```text
expected_total_cost =
  routing_cost
  + expected_input_tokens * input_price
  + expected_output_tokens * output_price
  + expected_retry_and_fallback_cost
```

Pre-Judge admission은 서버가 이미 아는 input/output 길이 추정, current safe baseline, catalog
가격과 bounded evidence만 사용한다. Judge 결과가 있어야 계산 가능한 값을 admission 근거로
사용하지 않는다. Stored baseline도 current hard gate를 통과하지 못하면 proven safe candidate를
사용하거나 provider 호출 전에 typed failure로 닫는다.

Current pricing version에 가격이 없거나 단위를 비교할 수 없는 후보는 최저 비용 후보로 간주하지
않는다. 별도 품질·운영 정책이 그 사용을 명시적으로 허용하지 않으면 경제성 최적화 대상에서 제외하고
current-valid stored safe path를 유지한다.

실제 비용에는 Judge, 실패, retry, fallback과 outcome-unknown attempt를 포함한다. 품질 비교용
evaluator 비용은 제품 routing cost와 분리하되 실험 비용으로 기록한다. 모든 비용은 routing이
새 Billing Principal을 합성하지 않고 실행 capability가 확정한 principal과 pricing revision에
귀속한다.

### 7. 모델 profile은 provenance를 가진 versioned evidence다

모델 선택 profile은 다음 증거를 구분한다.

- provider가 선언한 capability와 limit
- 수동 bootstrap prior
- 고정 benchmark 결과
- 운영 success/schema/downstream/fallback/latency evidence

각 evidence는 source, version, observed_at, confidence와 적용 scope를 가진다. 출처를 알 수 없거나
freshness 기준을 넘은 evidence는 강한 자동 선택 근거로 쓰지 않는다. Provider 공개 capability와
가격은 전역 catalog로 사용할 수 있지만 tenant 실행에서 파생한 operational evidence는 승인된 익명
집계 contract가 없으면 다음 identity가 모두 같을 때만 재사용한다.

```text
organization_id
workflow_id
canonical_node_location
task_semantic_fingerprint_contract_version
task_semantic_fingerprint
requirement_evidence_cohort_contract_version
requirement_evidence_cohort_id
model_id
evidence_contract_version
```

Prompt/instruction, variable mapping, output/schema, Knowledge/RAG configuration 또는 downstream
structural contract가 바뀌어 fingerprint가 회전하면 같은 node의 이전 operational evidence도 current
minimum-quality 근거가 아니다. 이전 row를 새 identity로 덮어쓰거나 fingerprint를 current state로
재계산해 되살리지 않는다. Fingerprint 원본 구성 요소와 runtime 입력은 model profile, API, trace와
audit에 저장하지 않는다. 같은 task fingerprint라도 다른 `model_id`의 성적이나 다른
`evidence_contract_version`으로 산출한 성적을 합치지 않는다.

`requirement_evidence_cohort_id`는 server가 해당 실행 snapshot의 canonical `task_intent`, bounded
`task_complexity`·`decision_impact`·`evidence_synthesis`와 output/effect/context risk class를
versioned canonical form으로 결합해 만든 opaque identity다. Requirement source가 없거나 cohort를
확정할 수 없는 표본은 current minimum-quality의 positive evidence로 사용하지 않는다. 별도 Accepted
monotonic transfer policy가 없는 한 다른 cohort의 성공 표본을 현재 cohort의 품질 근거로 합치지 않는다.
Raw requirement 값과 입력은 event, API, trace와 audit에 노출하지 않는다.

Aggregate 재사용 identity와 개별 표본 append identity는 구분한다. 각 model attempt는 ADR-0069의
immutable `provider_usage_operation_id`에 결합한 `record_operational_model_evidence` command로
evidence contract version당 최대 한 표본만 제공한다. Trusted logical actor
`platform routing operational evidence system`은 terminal provider operation과 canonical
workflow/node outcome을 읽어 organization/workflow/deployment, canonical node location,
invocation/Loop iteration, model/purpose와 task fingerprint를 server-side로 파생한다. Client, task
payload나 임의 finalizer가 run/node/model attempt identity를 주장할 수 없고, ADR-0069 operation이 없는
legacy 호출은 V2 operational evidence로 승격하지 않는다. Evidence contract가 명시적으로 허용한
work-model purpose와 production `deployed` 또는 사전 등록 limited-canary execution만 표본이 된다.
Requirement Judge, query embedding, summary, 일반 `test`, `policy_preview`와 격리 `benchmark` operation은
tenant operational model evidence에서 제외한다. `outcome_unknown`은 unknown으로만 보존하며 success,
schema/downstream pass 또는 proven-quality 표본으로 승격하지 않는다.

Canonical append identity는 `(organization_id, provider_usage_operation_id,
evidence_contract_version)`이며 최초 append는 current ADR-0069 `usage_revision`과 canonical safe metric
bundle digest를 함께 고정한다. 같은 identity·digest의 순차/동시 재전달은 기존 event/receipt를 반환하고
aggregate sample count를 다시 증가시키지 않는다. 같은 identity의 다른 digest는
`model_routing.operational_evidence_conflict`와 event/receipt/aggregate zero-write로 닫는다. Event,
idempotency receipt와 aggregate delta는 `provider usage operation -> evidence sample -> affected aggregate
validity rows(canonical key order)` lock 아래 같은 짧은 DB transaction에 commit하며 provider/network I/O를
수행하지 않는다.

ADR-0069의 authoritative usage correction은 기존 sample identity나 digest를 덮어쓰거나 두 번째 표본으로
append하지 않는다. Correction transaction은 provider usage operation을 먼저 잠근 뒤 그 operation을 참조하는
모든 operational evidence sample을 evidence contract/version canonical order로 열거하고, 각 sample이
참여한 모든 aggregate validity row를 canonical aggregate key order로 잠근다. Sample별 deterministic
`operational_evidence_correction_intent(organization, operation, evidence_contract,
target_usage_revision)`를 만들고 각 affected aggregate의 `operational_evidence_validity_epoch`을
한 번 증가시키며 pending correction set에 intent를 추가하는 mutation을 ADR-0069 correction과 같은 Unit
of Work에 commit한다. 따라서 하나의 operation이 여러 evidence contract 또는 aggregate에 반영됐더라도
일부만 current로 남지 않는다. 아직 append된 sample이 없으면 routing correction intent를 만들지 않고,
뒤의 최초 append가 operation lock 아래 이미 보정된 current usage revision을 읽는다.

Initial append와 correction은 모두 operation-first lock을 사용한다. Append가 먼저 commit하면 뒤 correction이
그 sample과 aggregate를 pending으로 만들고, correction이 먼저 commit하면 뒤 append가 corrected revision으로
최초 표본을 만든다. Exact correction replay는 기존 intent/epoch를 반환하고 같은 target revision의 다른
authoritative safe digest는 conflict/zero-write다. 이 순서로 correction commit 뒤 stale token/cost
aggregate가 다시 eligible해지는 구간을 없앤다.

Trusted `rebuild_operational_evidence_aggregate`는 provider I/O 없이 pending set의 모든 sample에 대해
current usage operation revision과 canonical outcome을 server-side로 다시 읽어 기존 sample count를
유지한 새 aggregate revision을 재구축한다. Operation -> sample -> aggregate validity row를 잠그고
expected validity epoch와 pending correction set digest를 재검증한 뒤 correction receipts, 새 metric
aggregate와 `correction_pending=false`를 원자 commit한다. Rebuild 중 새 correction이 먼저 commit하면
CAS loser는 zero-write 후 current epoch로 다시 실행한다.

`model_evidence_version`은 aggregate revision, operational evidence validity epoch와 sample usage revision
set digest를 결합한다. Selector가 operational evidence를 선택 근거로 사용하면 server는 해당 aggregate
validity row의 opaque identity/revision을 canonical order로 해소해 `model_evidence_fence_set_digest`를 만들고,
selector response·decision cache·activation command와 각 ProviderExecutionCapability에 이 digest와 exact
row revision set을 결합한다. Caller/task payload는 row identity나 revision을 주장할 수 없다.

Profile propose/holdout/canary start/production promotion은 사용한 exact model evidence version과 fence set을
고정하고 publication 또는 activation commit 전에 affected validity row를 다시 비교한다. Provider start도
같은 row를 canonical order로 잠그고 capability-bound revision/digest를 재검증한다. ADR-0069 correction은
operation -> sample -> 같은 aggregate validity row 순서로 revision/pending을 변경하므로 correction winner 뒤
stale selector/cache/activation/start는 `operational_evidence_correction_pending`으로 zero-write 또는 provider
I/O 전 current-valid non-V2/typed unavailable로 fail-closed한다. Provider-start가 동일 validity fence에서 먼저
commit한 attempt만 이미 승인된 호출로 완료할 수 있고 이후 decision에는 corrected version이 필요하다.
Pending correction이 하나라도 있거나 sample-bound usage revision/aggregate validity epoch가 current하지 않으면
selector, learner candidate, profile propose, holdout/canary start와 production promotion은 해당 aggregate를
positive quality 또는 비용 개선 근거로 사용하지 않는다. 이미 고정된 evidence snapshot은 새 aggregate
revision으로 조용히 갱신하지 않고 새 snapshot/profile 검증을 요구한다. Raw prompt/output, provider payload,
corrected token/cost 원문과 credential은 event, intent, receipt, digest와 audit에 저장하지 않는다.

### 8. 미검증 후보와 저빈도 workflow를 제한한다

운영 품질 근거가 없는 모델은 low-risk, reversible, internal 요청에서만 versioned canary budget으로
탐색한다. Human-facing, 외부 부수효과, 보안·법무·금전·권한 또는 되돌리기 어려운 요청에는 proven
safe baseline만 사용한다. Guardrail을 넘으면 exploration을 즉시 중단한다.

표본을 장기간 모으기 어려운 workflow는 검증된 fixed stored baseline, 경제성 admission을 통과한
요청의 Judge routing 또는 tenant 경계를 지키는 승인 prior를 사용한다. 조직 간 global learner는
tenant isolation, privacy와 retention을 다루는 별도 ADR 없이는 도입하지 않는다.

### 9. learner candidate와 production activation을 분리한다

새 근거가 승인되기 전 learner candidate 최소 자격은 ADR-0059의 기준을 유지한다.

- 계약에 맞는 Judge label 50건 이상
- 최근 학습 전 예측 20건 이상
- 요구 수준 완전 일치율 80% 이상
- 축별 평균 오차 0.5 이하
- 계약 통과율 95% 이상

모든 terminal `rejected` label은 평가 분모와 계약 통과율에는 한 번 포함하지만 classifier weight와
accepted training count에는 포함하지 않는다. 여기에는 Judge 응답 계약 실패, work-model fallback으로
확정된 label, execution/schema/downstream 실패와 outcome-unknown처럼 `learning_outcome_reason`이
`rejected`인 결과가 포함된다. `schema_status=not_applicable`처럼 평가 대상이 아닌 중립 상태는 rejected로
합성하지 않는다. 이 gate는 local requirement source를 사용할 자격만 부여한다. V2
production activation에는 별도의 locked holdout, 전체 비용, p95 지연, 품질, high-risk 과소판정,
fallback, canary와 rollback 검증이 필요하다. 사전 등록한 cohort별 모델 선택 분포 guardrail,
운영 schema/downstream 성공률 95% 이상과 fallback 5% 이하도 learner 자체의 요구 판정 능력이 아니라
selector와 runtime의 production activation gate에서 검증한다. 단일 모델 수렴 자체를 실패로 보거나
임의의 다양성을 강제하지 않고, 다양한 적격 요구 cohort에서도 profile이 허용한 분포를 벗어나거나
선택 근거로 설명할 수 없는 수렴만 차단한다.

Candidate gate 통과와 immutable learner version 발행은 active V2 policy 또는 activation profile을
자동 변경하지 않는다. Production 실행이 local classifier를 쓰려면 activation profile이 exact
learner version과 requirement contract digest를 승인해야 한다. 새 learner version은 기존 version의
in-place replacement가 아니며 새 profile revision, locked holdout, limited canary와 독립 production
승격을 다시 거친다.

### 10. learner와 cache는 각 소비 의미에 맞는 contract에 종속된다

Requirement contract digest는 최소 다음 version과 contract를 포함한다.

- Judge rubric version
- feature schema와 encoder version
- task intent normalization contract version과 canonical normalized `task_intent`
- learner label/evaluation contract version
- canonical deployment snapshot의 immutable task semantic fingerprint와 fingerprint contract version

Task semantic fingerprint는 canonical node location의 canonical normalized `task_intent`와 normalization
contract version, 고정 prompt/instruction, variable mapping, output/schema, Knowledge/RAG configuration과
downstream structural contract를 server-side canonical form으로 결합한다. Runtime 원문 값, credential
ID/material, mutable catalog/profile/pricing은 포함하지 않는다. 동일 의미의 재배포는 같은 fingerprint를
재사용할 수 있지만 canonical intent 또는 위 작업 의미가 바뀌면 새 learner lineage를 만들며,
fingerprint 원본 구성 요소를 API·trace·audit에 노출하지 않는다.

Selection contract digest는 다음 값을 포함한다.

- strategy와 selector version
- requirement contract digest
- authorization을 제외한 server hard-gate policy와 output/effect/context structural contract version
- model catalog/profile와 pricing version
- activation profile ID/revision과 exact requirement source manifest digest

Top-level routing contract version은 requirement와 selection contract를 함께 가리키는 실행 provenance다.
Learner는 requirement contract가 같으면 catalog·profile·pricing만 바뀌어도 재사용할 수 있지만,
accepted decision cache는 selection contract까지 같아야 한다. Requirement contract가 바뀌면 learner와
cache를 모두 회전한다. Selection contract만 바뀌면 기존 accepted cache entry는 모두 mandatory miss로
처리하고 current contract의 server selector를 다시 실행하되 learner는 불필요하게 재학습하지 않는다.
Stale cache가 가리킨 모델을 current gate로 재검증하는 것만으로 cache hit로 되살리지 않는다.
같은 requirement contract여도 activation profile 또는 exact requirement source가 learner A에서 B,
`approved_learner`에서 `judge_only` 등으로 바뀌면 selection contract와 cache namespace가 회전한다.
이전 profile/source가 만든 requirement 판정과 model recommendation을 새 activation 근거로 재사용하지
않으며, requirement contract 자체가 같으면 learner lineage까지 불필요하게 폐기하지 않는다.

Cache는 추천일 뿐 authorization이나 안전 승인 결과가 아니며, cache hit 후에도 current candidate,
authorization, credential/provider lifecycle과 hard gate를 다시 검증한다. Digest 없는 legacy cache는
miss로 처리한다. HMAC algorithm과 key epoch는 cache namespace identity에 포함한다. Key rotation은
이전 cache를 miss로 만들며 learner 또는 selection 의미를 바꾸지는 않는다. 같은 key/contract의 동시
fill은 immutable compare-and-set 또는 unique identity로 한 결과에 수렴하고 다른 contract artifact를
덮어쓰지 않는다. Cache namespace는 organization, deployment/version, canonical node location,
selection contract, activation profile/source binding과 key epoch를 포함하고 HMAC key material은 cache,
trace와 audit에 저장하지 않는다.
같은 입력이어도 다른 organization·deployment·node cache를 재사용하지 않는다.

### 11. durable node identity와 invocation identity를 분리한다

Policy와 learner의 durable node identity는 ADR-0066의 canonical `(container_path, node_id)`를
사용한다. Label과 attempt의 exactly-once identity에는 run, node invocation과 Loop iteration
identity를 추가한다. Terminal `node_id`만으로 다른 container나 반복 실행을 합치지 않는다. 같은
terminal event의 retry/background finalizer 중복은 하나의 label outcome과 count로 수렴하고,
동시 learner 발행은 expected active generation을 비교해 stale candidate의 승격을 거부한다.

### 12. 요구 판정·선택·재사용 출처를 분리한다

Canonical observability는 먼저 strategy 해석을 기록한다.

```text
requested_strategy_id
effective_strategy_id
strategy_resolution_reason:
  requested_active | economic_admission_skipped | feature_disabled |
  emergency_disabled | profile_missing | profile_stale | profile_disabled |
  profile_expired | profile_superseded | strategy_out_of_scope |
  activation_requirement_source_unavailable | credential_policy_unavailable |
  operational_evidence_correction_pending | canary_usage_correction_pending |
  worker_incompatible | rollback_policy_selected |
  strategy_contract_unresolved | rollback_target_invalid | benchmark_isolated |
  no_safe_path
```

`strategy_resolution_reason`은 bounded allowlist이며 credential, 후보 ID와 private scope detail을 포함하지
않는다. `profile_superseded`는 신규 실행의 predecessor 해소에만 사용하며 정상 cutover 전에
predecessor snapshot을 고정한 in-flight run의 중단 reason으로 합성하지 않는다. Typed failure 전에
실행 가능한 policy가 없으면 `effective_strategy_id=null`이다. 다음 V2 source
필드는 `effective_strategy_id=capability_routing_v2`인 decision에만 존재한다.

```text
requirement_source:
  runtime_judge | local_classifier | not_required | unavailable

model_selection_source:
  server_capability_selector | stored_default | stored_fallback

routing_reuse_source:
  none | accepted_decision_cache

requirement_evaluation_scope:
  current_execution | accepted_decision_cache | none
```

`not_required`는 effective V2 안에서 economic admission으로 요구 판정을 의도적으로 생략한 상태다.
V2 off, profile·requirement source·scope·credential policy·Worker 부적격은 requested/effective
strategy와 resolution reason으로
기록하고 V2 requirement source를 합성하지 않는다. `unavailable`은 effective V2가 요구 판정을 얻으려
했지만 호출·파싱·contract validation에 실패한 상태다. Secondary Judge를 사용해도 requirement source는
`runtime_judge`이며 bounded attempt count와 adjudication 여부로 구분한다.

`requirement_source`는 현재 execution에서 Judge가 실제 호출됐다는 telemetry가 아니라 effective
requirement의 origin을 뜻한다. Accepted decision cache가 requirement와 selection recommendation을 함께
재사용하면 cache row가 보존한 `runtime_judge` 또는 `local_classifier` origin을 유지하고,
`requirement_evaluation_scope=accepted_decision_cache`, `routing_reuse_source=accepted_decision_cache`,
Judge attempt count `0`을 함께 기록한다. UI와 trace consumer는 이 조합을 `이전 판정 재사용`으로
표시하며 이번 execution에서 Judge를 호출했다고 표시하지 않는다. 경제성 생략과 failed source는 각각
`not_required`/`unavailable` 및 `requirement_evaluation_scope=none`으로 기록한다.

기존 `decision_source`와 preview의 `matched_rule | default_model | fallback_model`은 호환 기간의
deprecated projection으로만 유지한다. 이를 V2 canonical source로 재해석하지 않는다. Raw prompt,
RAG 원문, credential, private candidate exclusion detail과 unredacted provider payload는 trace,
audit와 cache에 저장하지 않는다.

### 13. 실패와 fallback은 current gate를 다시 통과한다

Selector가 모델을 정한 뒤 `provider_started` 전에 lifecycle/authorization 변화로 그 후보만 무효가
되면, 같은 pinned selection contract와 원래 승인 후보 집합의 남은 후보를 대상으로 selector를 최대
한 번 다시 실행할 수 있다. 이는 provider 실패 fallback이 아니며 `server_capability_selector` source와
reselection reason을 기록한다. 새 후보를 추가하거나 profile/scope를 넓히지 않는다.

Requirement source/selector를 사용할 수 없거나 1회 reselection에도 후보가 없으면 다음 순서를 사용한다.

1. current data scope와 server-derived credential principal의 model-bound policy를 통과하고 hard gate를
   만족한 stored default
2. 같은 current 검증을 통과한 stored fallback
3. 둘 다 없으면 외부 호출 전 `model_routing.no_usable_model` 계열 typed error

이미 primary provider I/O가 definitive failure로 종결된 뒤에는 stored default로 되돌아가지 않고
current gate를 통과한 configured stored fallback만 평가한다.

Fallback은 권한, credential lifecycle, output/effect contract와 high-risk 하한을 우회하지 않는다.
ADR-0069의 definitive before-send/rejection/failure로 확인된 경우에만 fallback eligibility를 평가한다.
Outcome-unknown provider attempt는 성공 또는 실패로 추측하지 않고 같은 run에서 자동 retry나
fallback provider를 호출하지 않는다. Test Sidebar와 Cost Optimizer
compare 실행은 routing을 평가할 수 있지만 운영 learner label, weight, 성적과 refresh counter를
변경하지 않는다.

### 14. 실험 증거는 재현 가능하고 데이터 최소화돼야 한다

Activation 후보 실험 manifest는 다음 값을 가진다.

- source Git SHA와 dirty flag
- dataset ID/version/hash와 tuning/holdout 구분
- model catalog, pricing, selector, Judge rubric과 learner version
- environment class와 비교에 필요한 bounded 환경 정보
- `valid | invalid | incomplete` run status와 제외 사유
- 데이터 분류, redaction 상태, raw artifact 위치·checksum·retention reference

동일한 locked holdout과 evaluator로 V2, current strategy와 fixed baseline을 비교한다. Invalid,
incomplete 또는 tuning dataset 결과는 activation 계산에서 제외한다. Git에는 redacted aggregate
report와 manifest만 저장하며 raw 입력·출력은 승인된 접근 통제·retention 저장소만 사용한다.

### 15. Activation profile이 없거나 stale하면 V2를 실행하지 않는다

Production activation은 사전 등록된 versioned `routing_activation_profile`을 요구한다. Profile은
최소 다음 값을 가진다.

- immutable requirement source manifest:
  `requirement_source_mode=judge_only | approved_learner`. `judge_only`이면 exact Judge
  policy/rubric/contract version, `approved_learner`이면 exact immutable learner version과 requirement
  contract digest를 고정한다. `approved_learner`는 `learner_judge_fallback=disabled` 또는
  `exact_judge_policy`를 명시하며, 후자일 때만 exact Judge policy/rubric/contract를 추가로 고정한다.
  각 exact Judge policy와 learner version은 immutable definition identity와 별도의 authoritative
  `requirement_source_lifecycle_revision` 및 `active | revoked | retired` state를 가진다. Profile
  proposal은 source identity와 당시 active revision을 manifest에 고정한다. Source registry management
  mutation만 같은 lifecycle fence를 exclusive하게 잠그고 revision을 증가시켜 terminal revoke/retire할 수
  있으며 in-place 재활성화는 금지하고 새 immutable source version과 새 profile을 요구한다.
- 비교 baseline과 locked holdout dataset/evaluator version
- cohort별 최소 표본 수, 실패·미평가 분모 처리와 confidence/non-inferiority 계산 방법
- 최소 비용 개선 또는 최대 허용 비용 증가
- 품질·schema·downstream non-inferiority margin
- 최대 p95 latency 증가
- 최대 fallback·provider failure 비율
- high-risk 과소판정과 미검증 후보 사용 한도
- canary 규모·기간·중단 조건, `promotion_evidence_contract_version=1`의 `promotion_evidence_start_sequence=0`과 최소 연속 window 수/기간
- 각 threshold의 immutable 분류(`promotion_only` 또는 `hard_stop`)와 hard-stop 적용 aggregate/cohort. `hard_stop`은
  limited-canary, current production과 superseded pinned generation에서 exact generation이 terminal할 때까지 유지한다.
- 작성자·승인자, 승인 시각, 유효 기간과 exact current-valid non-V2 rollback policy version
- environment와 organization/workflow/deployment scope 또는 stable canary 규칙
- strategy/selector/catalog/pricing contract digest
- 최소 호환 Worker build 또는 runtime capability revision과 rollout readiness. Revision은 Client나 producer가
  task payload로 주장한 값이 아니라 실제 delivery를 소비한 process의 code-owned build/runtime identity다.

같은 generation의 evidence schedule과 등록 source instance는 `limited_canary` 뒤에도 current
`production_active`와 `superseded` profile의 pinned attempt가 terminal할 때까지 safety-monitoring
window로 계속 회전한다. Promotion evidence set의 end sequence보다 큰 snapshot은 post-promotion
monitoring snapshot이며 기존 promotion set에 소급해 추가하지 않는다. 이 snapshot의 late evidence는
아래 sealed late-correction 계약으로 hard-stop에 반영한다.

Locked holdout과 limited-canary evidence는 exact requirement source manifest의
`required_evidence_branch` 집합에 결합한다. Server는 branch ID를 branch kind와 exact immutable source
version에서 canonical하게 파생한다. `judge_only`는 exact Judge policy/rubric/contract branch 하나,
`approved_learner + fallback=disabled`는 exact learner/requirement-contract branch 하나,
`approved_learner + fallback=exact_judge_policy`는 learner와 Judge 두 branch를 모두 요구한다. Profile
작성자나 evidence producer가 이 집합을 줄이거나 branch/version을 임의 입력할 수 없다.

Holdout dispatch/result와 requirement 판정을 수행한 canary event는 actual
`requirement_source_branch_id`와 exact source version에 결합한다. Branch별 최소 표본·실패 분모·품질·비용·
지연·fallback·high-risk threshold와 전체 threshold를 모두 계산하며 한 branch의 성공으로 다른 required
branch를 대체하거나 합산 결과로 branch 실패를 가릴 수 없다. Required branch 누락, unknown branch,
version mismatch 또는 cross-branch 혼합은 evidence를 `incomplete`/stale로 처리하고 production으로
승격하지 않는다.

Effective V2의 economic admission으로 요구 판정을 생략한 `requirement_source=not_required`는 required
branch가 아니다. Server는 terminal execution snapshot에서 `evidence_cohort=not_required`, null branch ID/source
version과 exact `economic_admission` reason을 파생한다. Canary start와 매 tail rotation은 required branch
aggregate 외에 reserved `not_required` aggregate를 항상 준비한다. 이 표본은 branch별 최소 표본이나
threshold를 충족시키지 않지만 비용·실패·지연·품질·high-risk를 포함한 전체 aggregate와 promotion 분모에는
정확히 한 번 포함된다. 따라서 economic skip이 전체 실패를 숨기거나 required Judge/Learner branch 검증을
대체할 수 없다. `not_required`는 activation holdout work item을 만들지 않는다.

Locked holdout의 authoritative publication은 권한 있는 `platform routing holdout operator`의 idempotent
`activation_holdout_evaluate` command로만 시작한다. 이 actor는 profile 작성자와 production 승인자와
분리하며, 등록된 `platform routing holdout evaluator` workload만 실행과 terminal publication을 맡는다.
Canonical request와 run/artifact는 actor/command ID, immutable `holdout_operator_principal`, proposed profile
ID·revision, exact requirement-source manifest digest, server-derived required branch set/digest,
`purpose=activation_holdout`인 immutable dataset ID/version/hash, evaluator·metric contract version,
threshold contract, budget·credential·egress policy를 결합한다. Holdout workload나 finalizer가 operator
principal을 다른 principal로 바꾸지 못한다.

Admission transaction은 current profile과 artifact binding을 재검증하고 server-derived required branch
각각에 deterministic work item과 pending `activation_holdout_dispatch_intent` 하나를 미리 만든다.
Run intent, 전체 branch dispatch manifest, accepted receipt와 redacted audit는 한 transaction에 commit하며
어느 branch intent 초기화라도 실패하면 전부 rollback한다. Branch ID/source version의 canonical 정렬
순서로 evaluator stage/ordinal을 정하고 deterministic attempt key, monotonic revision, DB-clock lease와
fencing token을 결합한다. Provider I/O는 admission 전체 commit 뒤에만 허용하며 caller는 branch를
추가·누락·재정렬할 수 없다.

Recovery dispatcher는 모든 pending/expired branch intent를 독립적으로 claim한다. 매 최초 또는 recovered
attempt의 ADR-0069 admission 전에 run에 고정된 holdout operator principal의 current 권한, evaluator
workload 등록·purpose/source binding, exact profile/source/dataset/evaluator/metric contract와
credential·egress·budget lifecycle을 다시 검증한다. 모두 current일 때만 attempt admission을 commit하고
provider I/O를 시작한다. 하나라도 revoke/stale이면 fencing winner가 현재 intent를 `denied`, 아직 시작하지 않은 다른 branch
intent를 deterministic `cancelled_due_to_denial`로 닫고 run을 `denial_pending`으로 전이한다. 이미
`provider_started`인 attempt는 재호출하지 않고 terminal usage만 정산한다. 모든 intent가 terminal이면
finalizer가 immutable denied artifact, safe reason receipt와
`model_routing.activation_holdout.completed` audit를 exactly-once commit한다. 시작된 attempt가 없어서
모든 intent를 같은 transaction에서 닫을 수 있을 때만 denied publication도 함께 완료한다. Concurrent recovery는
branch별 fencing winner 하나만 attempt를 만들거나 기존 attempt를 이어가며 `provider_started` 또는
outcome-unknown attempt를 재호출하지 않는다.

각 branch terminal transaction은 자기 dispatch 결과만 확정하며 다음 branch intent를 새로 만들지 않는다.
따라서 한 branch 완료 직후 process가 종료돼도 나머지 branch의 admission-time pending intent를 recovery가
찾을 수 있다. Terminal finalizer는 모든 required branch intent가 terminal인지 먼저 확인하고 non-terminal
intent가 있으면 publication zero-write로 대기한다. 그 뒤 run과 unique artifact identity를 잠그고
admission과 동일한 holdout operator 권한, evaluator workload 등록·purpose/source binding 및 lifecycle을
다시 검증해 required branch별 sample/failure/metric aggregate와 전체 aggregate를 검증한다. Provider
start 뒤 권한이 회수돼도 결과를 `valid/invalid/incomplete`로 승격하지 않고 usage는 보존한 채 terminal
`denied`로 닫는다. 모든 required branch와 전체 threshold가 유효할 때만 `valid`, terminal branch
결과의 version mismatch·coverage 부족은 `incomplete`, threshold 위반은 `invalid`, current
operator/evaluator/lifecycle 재검증 실패는 `denied` artifact를 만든다. Crash로 아직 dispatch되지 않은
branch를 `incomplete`로 합성하지 않는다.

Artifact, terminal dispatch/run state, receipt와 canonical audit는 한 transaction에 commit한다.
Audit/receipt 실패는 publication을 rollback한다. 같은 actor/request replay는 기존 run/artifact와 동일한
branch dispatch manifest를 반환하지만 pending dispatch 처리를 취소하지 않는다. 같은 command 또는
artifact identity의 다른 canonical digest는 `model_routing.activation_holdout_command_conflict`와
run/artifact/dispatch/receipt/success audit/provider I/O zero-write로 닫는다. Concurrent finalizer는
unique identity와 CAS로 terminal artifact 하나에 수렴한다. Tuning dataset과 `product_benchmark` 결과는
이 artifact를 대신할 수 없으며 canary start는 exact profile revision과 required branch set/digest,
branch별 terminal 결과가 모두 결합된 current `valid` artifact만 인정한다.

Limited-canary evidence state는 canary start transaction에서만 생성한다. Start command는 immutable
window cadence/interval contract와 schedule revision, sequence 0의 `[window_start_at, cutoff)` current window,
sequence 1의 pre-registered next window, current/next와 각 required requirement-source branch 및 reserved
`not_required` cohort 조합의 empty aggregate와 window별 overall empty aggregate, 두 window와 manifest의 각
`activation_canary_runtime` source instance 조합에 대한 unobserved
initial watermark row와 generation-scoped `canary_usage_validity_epoch=0`,
`canary_usage_correction_pending_count=0` row를 profile/generation에 결합한다. Profile state/generation,
`canary_successor`, first-rollout `production_route`, evidence schedule/current pointer/aggregates/watermarks,
canary usage validity row, receipt와 audit를 한 transaction에 commit하고 그 뒤에만 successor capability를
발급한다. Schedule, source-instance watermark 또는 usage validity row 초기화가 하나라도 실패하면
generation/state/role/route/evidence/receipt/audit를 모두 zero-write한다. Same command replay는 기존
schedule identity를 반환하고 concurrent start는 unique/CAS winner 하나만 전체 묶음을 생성한다.

각 window는 profile/generation과 immutable cadence에서 server가 파생한 monotonic `window_sequence`,
`window_id`, 반개구간 `[window_start_at, cutoff)`와 monotonic `canary_evidence_revision`을 가진다.
Active current window는 이미 존재하는 next window의 `window_id`를 가리킨다. 그 next window는 pointer
이동 transaction에서 server가 새 tail을 생성해 결합하기 전까지 successor가 없을 수 있다. Current
window의 successor row와 aggregate/watermarks는 pointer가 이동하기 전에 반드시 존재해야 한다. `authoritative_event_time`은 manifest에 등록한 source
contract에 따라 server가 terminal DB commit time 또는 source position에 결합된 immutable timestamp에서
파생하며 client/provider callback의 임의 timestamp를 신뢰하지 않는다. Server는 aggregate lock 전에 이
시간으로 materialized current 또는 next의 `assigned_window_id`를 결정한다. Current cutoff 이상이면서
next interval 안인 non-blocking event는 current가 아직 seal되지 않았어도 next-window aggregate에만
append한다. Next cutoff 이상처럼 materialized schedule 범위를 벗어난 non-blocking event는
`model_routing.canary_evidence_schedule_not_ready`와 event/receipt/aggregate/audit zero-write로 닫고
rotation 뒤 재제출한다.

다만 actor/purpose, manifest source binding, source identity/digest와 authoritative event time이 유효한
blocking event의 generation 차단은 window 배정 성공에 의존하지 않는다. Seal이 한 cadence 이상 지연돼
event time을 포함하는 materialized window가 없으면 server는 immutable event에
`schedule_disposition=unassigned_schedule_lag`, nullable `assigned_window_id`와 관측한 schedule revision을
기록하고 aggregate/watermark/snapshot은 변경하지 않는다. 같은 transaction에서 event/receipt, exact
generation deny-only block/epoch와 failure audit를 commit한다. Exact replay는 이 unassigned event/receipt로
수렴하고 schedule catch-up 뒤에도 이를 aggregate에 재분류하지 않는다. 인증되지 않은 actor, 잘못된 source
binding/identity/digest 또는 권위 있는 event time 자체를 증명할 수 없는 요청은 이 fail-safe block 경로를
사용할 수 없다.

Canonical source-event dedupe identity는 canary window와 독립된
environment/family/profile ID·revision/safety generation, source kind, manifest에 등록된 immutable opaque
source instance ID와 그 instance namespace의 immutable source event ID로 구성하고 unique constraint를
둔다. Source instance ID는 Worker/process 재시작과 무관한 논리 namespace이며 ephemeral process ID를
사용하지 않고 event ID는 그 instance 안에서만 유일하면 된다. Usage-derived event에는 이 identity와 별도로
`canary_usage_sample_key=(environment, family, profile revision, safety generation,
provider_usage_operation_id, metric_contract_version, event_kind)` unique constraint를 둔다. 같은 terminal
provider operation의 같은 metric/event contract는 source instance나 source event ID가 달라도 canary
sample count에 최대 한 번만 기여한다. Server는 event가 참조하는 terminal runtime
attempt/execution snapshot에서 `evidence_cohort=required_branch | not_required`를 파생한다. Required-branch
cohort이면 actual branch ID와 exact source version을 함께 파생하고, economic admission이면
`evidence_cohort=not_required`, null branch/version과 exact skip reason을 사용한다. Producer 입력은 신뢰하지
않는다.

Profile의 metric contract가 usage-derived로 등록한 event kind는 terminal ADR-0069
`provider_usage_operation_id`가 필수다. Server가 execution snapshot에서 immutable operation identity와 current
`usage_revision`을 파생하며 caller/provider callback은 둘을 제출하거나 덮어쓰지 못한다. Operation ID는 같은
source event를 다른 billable attempt에 재결합하지 못하도록 source-event comparison digest와 위 canonical
usage sample key에 포함한다. `usage_revision`과 token/cost metric contribution은 correction 가능한 별도
`canary_usage_projection`에 저장하고 source-event identity/digest와 usage sample key에는 포함하지 않는다.
Non-usage event kind의 operation identity/revision과 usage sample key는 명시적 `null`이다. Source contract에서
파생한 authoritative event time, evidence cohort, nullable actual branch/version, metric contract version,
bounded event kind, optional provider usage operation ID와 redacted payload digest가 canonical source-event
comparison digest를 이룬다.

Usage-derived event는 aggregate나 schedule lock 전에 ADR-0069 operation row -> canonical usage sample key
row -> source-event identity 순서로 잠가 terminal binding과 current usage revision을 검증한다. Non-usage
event는 source-event identity부터 조회한다. 기존 source row가 있으면 actor/source binding과 canonical
source-event comparison digest를 재검증해 같을 때 저장된 event/receipt와 최초
`schedule_disposition`/`assigned_window_id`를 반환하고, 현재 schedule로 window를 다시 계산하거나
aggregate에 재분류하지 않는다. Existing usage projection의 observed revision이 current와 달라도 event
conflict로 바꾸지 않으며 authoritative correction/reconciliation 경계가 처리한다. Digest가 다르면
`model_routing.canary_evidence_conflict`와 zero-write다. 새 source identity라도 usage sample key가 이미 다른
source identity에 고정돼 있으면 `model_routing.canary_evidence_duplicate_operation`과
event/projection/aggregate/receipt/audit zero-write다. 두 unique insert의 concurrent loser도 같은 stored
binding을 재검증한 뒤 exact replay 또는 duplicate-operation 결과로 수렴한다. 두 identity가 모두 새일 때만
current materialized schedule에서 assignment를 계산한다.
`schedule_disposition=assigned | unassigned_schedule_lag | late_sealed`, nullable `assigned_window_id`와 observed schedule
revision은 server-owned immutable processing outcome이며 identity나 producer 입력이 아니다. Caller가
window/disposition을 주장하거나 기존 outcome을 바꾸려 하면 거부한다. 서로 다른 등록 source instance의 같은 event ID는 non-usage event이거나 서로 다른 canonical usage sample
key에 결합된 경우에만 각각 별도 event다. 같은 operation/metric/event contract의 usage observation은 source
instance와 무관하게 하나다.

새 assigned event transaction은 server가 배정한 window의 cohort aggregate와 overall aggregate를 canonical
key 순서로 잠근다. Usage-derived event이면 current operation revision, server-derived safe contribution과
contribution digest를 가진 별도 `canary_usage_projection`을 event/receipt와 같은 transaction에 만들고 sample
count를 한 번만 반영한다. Bounded event kind가 direct blocking이면 threshold 값이나 분류와 무관하게
aggregate 뒤 exact generation safety row를 잠그고 원 source event/usage projection/receipt, aggregate
revisions, deterministic direct breach, deny-only block/epoch와
`model_routing.canary_breach.detected` failure audit를 한 transaction에 commit한다. 같은 append가 hard-stop도 넘더라도 aggregate-derived breach나 generic
`model_routing.canary_evidence.recorded` audit를 추가하지 않는다.

Non-blocking event만 post-append 값으로 profile에 고정된 `hard_stop` threshold를 판정한다.
`promotion_only` threshold는 generation을 차단하지 않는다. 어느 affected cohort 또는 overall aggregate가
처음 hard-stop을 넘으면 aggregate lock 뒤 exact generation safety row를 잠그고 deterministic
`aggregate_threshold_breach` identity에 sorted threshold ID/digest와 first-crossing revisions를 결합한다.
원 source event/receipt, aggregate revisions, immutable breach row, deny-only block/epoch와
`model_routing.canary_breach.detected` failure audit를 한 transaction에 commit하고 generic
`model_routing.canary_evidence.recorded` audit는 추가하지 않는다. Threshold를 넘지 않은 non-blocking append만
`model_routing.canary_evidence.recorded`/`success`/`result_status=non_blocking`을 기록한다.

Concurrent append는 aggregate/generation lock 아래 direct 또는 first-crossing breach 하나로 수렴하고 replay는
기존 receipt/outcome을 반환한다. 이미 blocked generation의 후속 terminal evidence는 aggregate에 한 번
반영하되 새 block/audit를 만들지 않는다. Reserved `not_required`는 branch threshold를 충족하지 않지만
overall hard-stop 판정에는 포함된다.

새 unassigned schedule-lag direct blocking event는 aggregate 없이 exact generation block과 원자 commit한다.
Sealed snapshot에 배정된 direct blocking event는 아래 state-aware reconciliation을 통과하되 모든 지원
state branch에서 exact generation block을 반드시 포함한다. Sealed snapshot row는 cutoff 이전
late-arrival reconciliation이 snapshot invalidation을 기록할 때만 갱신한다. Direct 또는 aggregate-derived
blocking breach evidence actor는 snapshot이나 profile lifecycle/scope를 변경하지 않고 exact generation의
immutable event/receipt, deny-only safety block/epoch와 failure audit만 기록할 수 있다. 단, promotion
evidence member invalidation branch의 lifecycle 전이는 아래 전용 reconciliation 계약을 따른다.

Authoritative source watermark는 등록된 source adapter workload의
`advance_canary_source_watermark` command로만 전진한다. Adapter는 자신에게 등록된 source kind와 opaque
source instance에 대해서만 environment/family/profile revision/generation, target window ID, source
contract version, monotonic source barrier/position, `observed_through`, expected watermark revision과 마지막
committed source receipt/barrier proof를 canonical request로 제출한다. Event가 없는 구간도 authoritative
scan/barrier가 target cutoff까지 완료되고 그 이하 event의 append receipt가 모두 terminal임을 adapter가
증명한 경우에만 전진할 수 있다. Transaction은 immutable window schedule과 target-window source watermark
row를 잠그고 window/source-instance binding, monotonicity, expected revision과 terminal receipt coverage를
재검증한 뒤 watermark, actor/request-bound receipt와 redacted audit를 원자 commit한다. 같은 actor/request
replay는 기존 watermark를 반환하고 같은 command ID의 다른 request는
`model_routing.canary_watermark_command_conflict`, stale expected revision·barrier regression·불완전
receipt coverage 또는 seal된 old-window request는 `model_routing.canary_watermark_stale`과 zero-write다.
Client, provider callback, profile author와 seal request는 watermark를 직접 만들거나 앞당길 수 없다.

각 Production 승격 후보 window snapshot은 논리 service actor `platform routing evidence system`의 idempotent
`seal_canary_evidence_window` command로만 만든다. Canonical request는 environment/family/profile
ID·revision/generation, expected schedule revision, current window ID/sequence/cutoff와 pre-registered next-window
ID/sequence, expected aggregate revision map과 그 canonical digest, authoritative source별 expected watermark를 포함한다. Aggregate map의 key set은
profile의 current required branch set에 reserved `not_required` key를 더한 server-derived set과 정확히 같아야
하며 caller는 key를 생략·추가할 수 없다.
Request watermark는 optimistic expectation일 뿐 authority가 아니며 client는 tail ID/time을 제출하지 않는다.
Server는 immutable cadence에서 `tail_sequence=next_sequence+1`, tail ID와 `[next.cutoff, tail.cutoff)`를
결정한다. Transaction은 schedule/pointer와 current/next window를 잠근 뒤 current evidence aggregate row를 reserved key를 포함한 canonical key 순서로,
server-owned source watermark row를 고정 순서로 잠근 뒤 generation canary usage validity row를 잠근다.
Cutoff, assigned-window membership, branch별 aggregate revision map과 canonical digest, 모든 watermark,
`canary_usage_correction_pending_count=0`과 expected usage validity epoch를 다시 검증한다. Snapshot에는 `authoritative_event_time < cutoff`이고
`assigned_window_id=current_window_id`인 event만 포함한다. 성공 시 current window를 sealed로 전이하고 snapshot의 required branch별·reserved `not_required`·overall
sealed late-correction aggregate와 sealed usage-correction aggregate를 각각 revision 0으로 초기화한다. 두
correction family는 snapshot 원 aggregate/hash와 분리한다. Late-correction은 cutoff 이전 late event의 safety
fold, usage-correction은 이미 집계된 usage-bearing event의 ADR-0069 revision 변경을 sample count 보존
retraction/addition으로 반영한다. 새 tail window와 required branch별·reserved `not_required`·overall empty aggregate, tail window별 모든
registered source-instance의 unobserved initial
watermark를 만들며, 기존 next의 `next_window_id`를 tail에 결합한 뒤 pointer를 next로 이동한다. Immutable
snapshot ID/revision/hash와 초기 `snapshot_invalidation_revision=0`, late/usage correction rows, 봉인 시점
`canary_usage_validity_epoch`, 증가한 schedule revision,
actor/request-bound receipt와 canonical audit도 같은 transaction에
commit한다. Tail 초기화, audit 또는 receipt 중 하나라도 실패하면 window/snapshot/pointer/schedule/tail을
모두 rollback한다. 같은 actor/request 재전달은 기존 snapshot과 exact tail identity를 반환하고, concurrent
seal 또는 같은 command ID의 다른 request는 unique/CAS 아래 하나만 commit하며 loser는
`model_routing.canary_snapshot_seal_conflict`와 zero-write다. Watermark나
cutoff 이전 append가 먼저 current revision을 바꾸면 seal은 current revision으로 재제출한다. Cutoff 이후
append는 seal보다 먼저 commit해도 next-window aggregate만 바꾸므로 current seal revision과 snapshot
membership에 영향을 주지 않는다. Seal이 insufficient watermark 상태에서 먼저 잠그면 snapshot
zero-write로 실패하고, 성공 seal 뒤 old-window watermark advance는 stale다.

Production 승격은 최신 snapshot 하나가 아니라 profile/generation의 immutable
`promotion_evidence_set`을 소비한다. `capability_routing_v2` production promotion은 profile에 고정한
`promotion_evidence_start_sequence=0`부터 request의 end sequence까지 모든 sealed window snapshot을
빠짐없이 포함한다. Canonical set identity는 environment/family/profile revision/generation, start/end
sequence, ordered member별 snapshot ID/revision/hash/`snapshot_invalidation_revision`, sealed usage-correction
revision/digest, required requirement-source branch/version별 effective aggregate, reserved `not_required`
effective aggregate, 전체 effective aggregate, evidence contract version과 generation
`canary_usage_validity_epoch`의 digest다. Effective aggregate는 immutable sealed base에 late-arrival correction과
usage correction을 서로 다른 projection으로 정확히 한 번 fold한 값이다. Server는 window sequence
오름차순으로 모든 member snapshot과 correction rows를 잠그고 연속성, cutoff, source watermark, required
branch별 gate와 `not_required`를 포함한 전체 최소 표본/기간/실패 분모/threshold, 모든 member의
`snapshot_invalidation_revision=0`, current usage-correction revision과
`canary_usage_correction_pending_count=0`을 검증한다. Non-zero
revision은 caller가 최신 값을 제출해도 영구적으로 ineligible하다. 누락·중복·gap·branch omission,
member revision/usage-correction mismatch, pending correction 또는 임의의 좋은 window 선택은
`model_routing.canary_evidence_required | stale | canary_usage_correction_pending`과
set/profile/route/receipt/audit zero-write다. 모든 gate가 current이면 immutable set/member rows를
production promotion, promoted generation과 같은 transaction에 commit한다. 이 set은 개별 window
snapshot을 대체하지 않고 promotion이 실제 소비한 전체 근거 manifest를 고정한다.

Cutoff 이전 event가 seal 전에 append되면 성공 snapshot에 포함된다. 같은 event가 seal 뒤 늦게
도착하면 일반 append로 snapshot aggregate에 합치지 않는다. `record_canary_evidence`는 target window가
sealed임을 확인하면 event write를 하지 않고 최소 권한 논리 actor
`platform routing evidence reconciliation system`의 idempotent
`reconcile_canary_late_evidence` command 경계로 내부 전달한다. Evidence intake가 immutable schedule에서
sealed snapshot ID/revision을 server-side로 파생하며 source caller가 선택할 수 없다. Terminal reconciliation
receipt가 commit되기 전에는 success를 acknowledge하지 않고, crash 전 receipt가 없으면 동일 source
identity/digest로 전체 command를 재시도한다. Canonical request는 원 source event identity와 comparison digest,
exact profile/generation과 server-derived sealed snapshot ID/revision을 결합하고, invalidation branch만 expected
`snapshot_invalidation_revision`을 추가한다. Monitoring late-correction revisions는 caller/request identity가
아니며 Coordinator가 canonical row lock 안에서 current 값으로 읽고 증가시킨다. Promotion evidence set ID, promoted generation과 route owner는
caller 입력이 아니라 Coordinator가 lock 안에서 current state로 파생한다. 같은 event identity의 다른 digest는
`model_routing.canary_evidence_conflict`와 zero-write다.

Coordinator는 `environment guard -> activation profile runtime-safety -> family canary/production role`
순서로 먼저 잠그고 profile state, exact role owner/generation, snapshot sequence와 promotion-set member
relation을 다시 읽는다.

Profile이 여전히 `limited_canary`이고 snapshot이 promotion evidence set에 소비되지 않았다면 canary role
다음 해당 sealed snapshot을 잠근다. Non-blocking late event는 `schedule_disposition=late_sealed` immutable
event, 증가한 snapshot invalidation revision, receipt와
`model_routing.activation_evidence.invalidated`/`failure`/
`result_status=pre_promotion_snapshot_invalidated`를 한 transaction에 commit한다. Direct blocking late
event는 snapshot 뒤 exact generation safety row도 잠가 같은 invalidation, deny-only block/epoch와 단일
`model_routing.activation_evidence.invalidated` audit의
`result_status=pre_promotion_snapshot_invalidated_generation_blocked`를 원자 commit한다. 이 branch는
`model_routing.canary_breach.detected`를 추가하지 않는다. Snapshot 원 aggregate/hash와 profile state/role은 변경하지
않는다. Non-zero invalidation revision인 snapshot은 caller가 최신 revision을 다시 제출해도 promotion에
영구적으로 ineligible하며 operator가 canary를 종료하고 새 profile/generation에서 근거를 다시 수집해야 한다.

Snapshot이 immutable promotion evidence set의 member이고 그 generation이 current `production_route` owner이면
Coordinator는 profile에 고정된 authoritative rollback policy fence를 먼저 잠그고
`rollback policy -> promotion evidence set -> member sealed snapshot -> exact generation safety row ->
command receipt/audit identity` 순서로 잠근다. Lock 안에서 policy current revision/eligibility를 다시 확인해
유효하면 exact non-V2 rollback route를, revoke/missing이면 `blocked_no_safe_path`를 선택한다. Immutable
`late_sealed` event/invalidation, exact generation deny-only block/epoch, profile의
`production_active -> disabled`, 선택한 route, receipt와 같은 failure action의
`result_status=rolled_back | blocked_no_safe_path`를 한 transaction에 기록한다.

Promotion-set member generation이 이후 정상 promotion으로 superseded되어 current owner가 아니면
`environment guard -> historical profile runtime-safety fence -> exact promotion evidence set -> member
sealed snapshot -> exact historical generation safety row -> command receipt/audit identity` 순서로 잠근다.
Immutable `late_sealed` event/invalidation과 exact historical generation deny-only block/epoch, receipt와
같은 failure action의 `result_status=superseded_generation_blocked`만 원자 기록한다. Current successor의
profile state, runtime-safety revision, production role/route와 generation은 zero-write다.

Snapshot sequence가 promotion evidence set end보다 크고 같은 production generation에 속하면
post-promotion monitoring snapshot이다. Current owner branch는 production role, historical branch는
historical profile fence 뒤 `sealed snapshot -> cohort/overall late-correction aggregates(canonical key
order) -> exact generation safety row` 순서로 잠근다. Immutable snapshot aggregate/hash와 invalidation
revision은 바꾸지 않고 source event/receipt와 correction revisions를 정확히 한 번 기록한다. Effective
hard-stop 값은 immutable sealed base aggregate와 correction aggregate의 deterministic fold다. Direct
blocking event는 threshold와 무관하게, non-blocking event는 이 fold가 hard-stop을 처음 넘을 때만
deterministic breach와 exact generation block/epoch 및 `model_routing.canary_breach.detected` failure audit를 원자
commit한다. Crossing이 없는 non-blocking correction은 `model_routing.canary_evidence.recorded` success audit 하나만
기록한다. Current profile lifecycle/route와 current successor는 모두 zero-write이고, superseded branch는
historical generation만 차단한다. Concurrent correction/crossing과 replay는 event/correction/breach/audit
하나로 수렴한다.

미승격 terminal profile, promotion-set 범위 안인데 member가 아닌 snapshot, 다른 generation/snapshot 또는 위
지원 state/snapshot relation에 속하지 않는 request는 `model_routing.canary_evidence_stale`과
event/correction/invalidation/block/lifecycle/receipt/success-audit zero-write다.

Profile/role lock은 production promotion과 같은 직렬화 경계다. Reconciliation이 먼저 잠그면 promotion이
증가한 member invalidation revision을 보고 zero-write하고, promotion이 먼저 commit하면 같은 command가
current/historical promoted branch를 즉시 수행한다. 따라서 caller가 다른 command로 재시도해야 하는
unprotected 중간 상태가 없다. Promoted 두 분기는 invalidation만 commit되고 필요한 generation block이 빠진
상태를 허용하지 않는다. Provider start가 먼저 commit한 attempt만 이미 승인된 호출로 완료하고
reconciliation winner 뒤 affected generation의 모든 새 start는 zero-write다. Transaction 일부 또는
audit/receipt 실패는 event와 correction/invalidation/safety/lifecycle write를 모두 rollback해 동일 source event retry가
전체 전이를 재시도하게 한다.

#### Canary usage correction

ADR-0069 authoritative usage correction은 operation lock 아래 Operational Model Evidence sample뿐 아니라
같은 operation을 참조하는 모든 `canary_usage_projection`을 canonical order로 열거한다. Projection별
old/new safe contribution digest와 target usage revision에 결합한 deterministic correction intent를
만든다. Generation의 `canary_usage_correction_pending_count`는 intent 수가 아니라 current contribution이
authoritative target revision보다 뒤처진 distinct projection 수다. Projection이 current -> pending으로 처음
전이할 때만 pending count를 증가시키고, 이미 pending인 projection에 더 최신 correction이 오면 active target
revision/intent만 전진시키며 count를 다시 늘리지 않는다. Non-replay correction은 operation ->
projection canonical order 뒤 affected generation의 drain summary -> canary usage validity row 순서로 잠근다.
Generation별 `canary_usage_validity_epoch`과 drain revision을 한 번 증가시키고 intent, active target,
projection/summary pending 상태를 같은 Unit of Work에 commit한다. Event identity/comparison digest와 immutable sample count는 바꾸지 않는다. Usage correction이 먼저
commit한 뒤 최초 canary append가 오면 append는 current revision으로 최초 projection을 만들며 pending intent를
추가하지 않는다. Append가 먼저 commit하면 correction이 그 projection을 pending으로 만든다. Exact correction
replay는 기존 intent/epoch를 반환하고 같은 target revision의 다른 safe digest는 conflict/zero-write다.

Canary start transaction은 generation마다 server-owned `canary_generation_drain_summary`를
revision 0으로 초기화한다. Summary는 non-terminal pinned attempt count, non-terminal ADR-0069 provider
operation count, distinct pending usage projection count, terminal attempt watermark/digest, required/current
source-barrier coverage digest와 monotonic `drain_revision`을 가진다. Caller는 count, watermark 또는 digest를
제출할 수 없다. Pinned attempt/operation admission은 dispatch 가능 상태를 commit하기 전에 같은 transaction에서
해당 count를 증가시키고, terminal finalizer는 operation outcome/receipt와 함께 count를 정확히 한 번
감소시키고 terminal watermark/digest를 전진시킨다. Source watermark/append terminal publication과 usage
projection current -> pending/applied 전이도 자신이 소유한 row와 generation summary를 같은 transaction에서
갱신한다. Summary update 실패는 원 operation/finalizer/watermark/correction transition도 rollback한다.

Trusted `reconcile_canary_usage_correction`은 provider I/O 없이 operation -> source event/projection ->
profile/role의 current state relation -> affected open aggregate 또는 sealed snapshot usage-correction rows ->
generation drain summary -> generation usage validity row -> optional exact generation safety row 순서로
잠근다. Projection lock 안에서 authoritative latest target을 다시 읽고, materialized contribution을 한 번
retract한 뒤 latest contribution을 한 번 add해 sample count를 유지한다. 같은 projection의 이전 pending
intent는 `superseded`, latest intent는 `applied`로 terminal 처리하며 projection이 authoritative revision과
일치할 때 distinct-projection pending count를 usage row와 drain summary에서 정확히 한 번 감소시킨다. Stale
worker가 과거 target을 제출해도 aggregate를 과거 값으로 되돌리지 않고 같은 lock에서 latest target으로
수렴한다. Reconciliation commit 뒤 더 최신 correction이 이기면 그 correction이 projection을 다시 pending으로
만들고 epoch와 drain revision을 전진시킨다. Pending count가 0일 때만 generation usage row의 current epoch를
새 capability, seal과 promotion이 사용할 수 있다. Evidence lifecycle이 이미 `closed`여도 ADR-0069의 새
authoritative billing/usage correction은 거부하지 않는다. Operation-bound correction command는 닫힌
schedule/window를 다시 열지 않고 deterministic intent, 증가한 usage epoch와 append-only correction
projection 및 drain-summary revision만 만든다. Finalizer는 immutable final snapshot 또는
promotion/monitoring relation에 최신 contribution을 fold하고 필요한 historical generation block이나
current-owner rollback/`blocked_no_safe_path`를 기존 state-aware branch로 원자 수행한 뒤 pending을
해제한다. Closed generation의 admission, capability와 window rotation은 계속 닫힌 상태이며 correction이
이를 재활성화하지 못한다.

State별 결과는 다음과 같다.

- Open window event는 cohort/overall aggregate에 usage delta를 반영한다. Corrected fold가 `hard_stop`을 처음
  넘으면 deterministic breach, exact generation block/epoch와 `model_routing.canary_breach.detected`를 원자 기록한다.
- 아직 promotion에 소비되지 않은 sealed snapshot은 immutable base/hash/invalidation을 바꾸지 않고 별도
  sealed usage-correction aggregate/revision을 갱신한다. Corrected base+late+usage effective aggregate가
  어느 `hard_stop`을 처음 넘으면 snapshot usage-correction rows 뒤 exact generation safety row를 잠가
  deterministic breach, deny-only block/epoch와 `model_routing.canary_breach.detected` failure audit를 같은
  transaction에 commit한다. 이미 blocked이면 correction만 적용하고 breach/audit를 중복하지 않는다.
  Promotion은 base+late+usage effective aggregate와 current correction revision, pending=false를 제출해야 한다.
- Promotion-set member correction은 immutable ordered set/member row와 그 identity/hash/revision을 갱신하지
  않는다. 대신 `(promotion_evidence_set_id, member_snapshot_id, provider_usage_operation_id,
  target_usage_revision, correction_contract_version)` unique identity의 append-only
  `promotion_evidence_reconciliation` row에 original set/member reference, safe correction digest, 재평가한
  effective gate 결과와 resulting current rollback 또는 historical block을 기록한다. Transaction은
  authoritative rollback policy -> immutable set -> immutable member -> reconciliation identity -> generation
  순서로 잠근다. Current production owner에서 어느 promotion gate가 더는 성립하지 않으면 exact generation
  block, `disabled`와 current-valid rollback 또는 `blocked_no_safe_path`를 원자 commit한다. Superseded owner는
  exact historical generation만 차단하고 current successor는 변경하지 않는다. 같은 identity·digest replay는
  기존 reconciliation/receipt를 반환하고 같은 identity의 다른 digest는
  `model_routing.promotion_evidence_reconciliation_conflict`와 zero-write다.
- Promotion set end 뒤 monitoring snapshot은 sealed usage-correction aggregate를 갱신하고 corrected
  hard-stop first crossing에서 exact current/historical generation만 차단한다. Profile/route와 current
  successor는 변경하지 않는다.

Correction이 canary breach나 promotion evidence invalidation을 만들면 기존
`model_routing.canary_breach.detected` 또는 `model_routing.activation_evidence.invalidated` action만 exactly-once
사용한다. Current promotion member의 safe result는 `usage_correction_rolled_back |
usage_correction_blocked_no_safe_path`, historical member는 `usage_correction_superseded_generation_blocked`다.
Gate를 넘지 않는 open/sealed correction은 ADR-0069 correction audit와 redacted reconciliation receipt만
남기고 generic `model_routing.canary_evidence.recorded`를 새로 만들지 않는다. Raw token/cost, provider payload와 operation
원문 detail은 event, audit, trace와 public error에 저장하지 않는다.

Cutoff 이후 materialized next interval의 non-blocking event는 seal 순서와 무관하게 next window에만 남고
current snapshot revision/hash/invalidation revision을 바꾸지 않는다. Cutoff와 무관한 새 blocking breach는
immutable event/receipt, exact profile-bound generation의 deny-only safety block/epoch와
`model_routing.canary_breach.detected`, `status=failure` audit만 한 transaction에 기록한다. 일반 evidence
workload는 profile state, `canary_successor`/`production_route`, rollback route와 lifecycle command receipt를
변경하지 않는다. 이후 권한 있는 operator의 별도 emergency disable/rollback command가 해당 role을
종결한다. 이 제한은 위의 exact sealed-snapshot late reconciliation 전용 actor/command에는 적용하지 않는다.

Promotion command는 actor/command ID, expected profile/family/domain-role revision, exact terminal holdout
artifact ID/revision과 bound `holdout_operator_principal`, evidence start/end sequence, ordered member snapshot
ID/revision/hash/invalidation-revision/usage-correction-revision digest와 expected generation
`canary_usage_validity_epoch`를 canonical request에 결합한다. Coordinator는 commit 직전 operator 권한과 DB
clock으로 `valid_until`, current `limited_canary` profile/revision과 exact canary role owner/generation,
requirement-source manifest와 terminal `valid` holdout artifact, promotion actor가 profile author와 artifact-bound
holdout operator principal 모두와 다른지, credential policy, 실제 consumer Worker readiness, global workflow
model execution eligibility, exact current-valid non-V2 rollback policy revision, global kill/scoped generation
safety, 모든 member snapshot의 연속성·required branch gate·reserved `not_required`를 포함한
base+late+usage effective overall gate·invalidation/usage-correction revision,
`canary_usage_correction_pending_count=0`과 blocking breach를 한 lock order에서 다시 검증한다. 동일
principal이거나 artifact principal/revision이 stale이면 `model_routing.activation_approval_required`와
promotion/set/route/receipt/success audit zero-write다. 성공
transaction은 immutable `promotion_evidence_set`과 member rows, successor promotion, predecessor
supersede, production route, receipt와 audit를 함께 commit한다. 하나라도 precheck 뒤 변경되면 successor promotion, predecessor supersede,
production role 전환, receipt와 success audit를 모두 zero-write하고 해당 typed stale/ineligible error로
닫는다. 따라서 promotion은 cutoff 이후 non-blocking 트래픽만으로 stale되지 않지만 cutoff 이전 late
arrival, blocking breach 또는 current execution eligibility 회수가 먼저 commit되면 성공할 수 없다.
Cutoff와 무관한 blocking breach는 권한 있는 operator command가 이후 profile lifecycle을 `disabled` 또는
승인 rollback으로 종결한다. 반면 promotion evidence set의 어느 member snapshot에서든 pre-cutoff late event가 오면 위 전용
reconciliation command가 member/set invalidation, generation block과 필요한 current-owner lifecycle/route
전이를 함께 commit한다.

Profile이 없거나 current contract와 다르거나 profile-bound requirement source를 현재 실행에서
사용할 수 없으면 V2 Requirement Judge나 다른 learner로 조용히 대체하지 않는다. Workflow에
별도로 versioning된 current-valid non-V2 policy 또는 stored safe path가 있으면 이를 사용하고, 그런
경로도 없을 때만 provider 호출 전에 typed failure로 닫는다. Legacy artifact를 V2 rollback으로
자동 승격하지 않는다. Profile의 수치 기준과 통계 방법은 locked holdout 결과를 보기 전에 사전
등록한다. 최소 표본이나 confidence 조건이 충족되지 않은 결과를 성공으로 보거나 실패·미평가 표본을
분모에서 제거하지 않는다.

Activation profile lifecycle은 다음 상태를 사용한다.

| 상태 | V2 실행 범위 | 진입 조건 |
| --- | --- | --- |
| `proposed` | 실행 불가 | threshold, canary, rollback과 contract를 immutable revision으로 사전 등록 |
| `limited_canary` | 사전 등록한 stable canary scope만 | valid manifest와 locked holdout, current credential policy, 호환 Worker readiness, rollback target을 검증하고 작성자와 다른 actor가 canary 시작을 승인 |
| `production_active` | family의 immutable production activation domain | `limited_canary`의 사전 등록 최소 표본·기간과 품질·비용·지연·fallback·high-risk guardrail을 통과하고 current revision에 독립 승인을 다시 받음 |
| `disabled` | 신규 V2 실행 불가 | guardrail breach 뒤 권한 있는 emergency disable/rollback command |
| `expired` | 신규 V2 실행 불가 | 유효 기간 종료 |
| `superseded` | 신규 V2 실행 불가. Promotion 전에 admit된 pinned attempt는 runtime safety가 current이면 허용 | 같은 family successor의 production promotion이 atomic cutover를 완료 |

활성화 진행 경로는 `proposed -> limited_canary -> production_active`다. `proposed`, `limited_canary`와
`production_active`는 유효 기간 종료 시 `expired`, 권한 있는 operator의 disable/rollback command 시
`disabled`로 전이할 수 있다. `superseded`는 같은 family successor의 성공한 production promotion이
predecessor를 원자적으로 교체할 때만 사용한다. `disabled`와 `expired`는 runtime-safety terminal 상태이고 `superseded`는 신규 실행 admission terminal
상태다. 어느 상태도 다시 활성화하지 않고 변경된 계약을 새 immutable `proposed` profile로 등록한다. 정상
`superseded` predecessor의 runtime safety revision과 generation은 유지하므로 pre-admitted pinned run은 별도
safety override가 없는 한 완료할 수 있다.

각 profile은 `rollout_family_id`와 family의 immutable production activation domain을 가진다. Family에는
`production_active` predecessor 최대 하나와 `limited_canary` successor 최대 하나만 있을 수 있다.
Successor canary가 stable assignment에 일치하면 successor를 선택한다. Predecessor가 있으면 나머지는
predecessor production revision을 선택하고, 없으면 아래 최초 rollout 계약을 적용한다. 서로 다른 family의
active production/canary domain은 overlap할 수 없다.

Family의 activation-domain reservation은 environment 수명의 immutable overlap 경계다. Profile이
`disabled`, `expired` 또는 `superseded`로 terminal 전이해도 reservation을 해제하지 않으며 remediation은
같은 family 안의 새 profile revision으로 진행한다. 이 결정은 family retire/reassign command를 정의하지
않으므로 별도 Accepted activation-domain migration 계약 없이는 다른 family가 그 domain을 인수할 수 없다.
Profile runtime role claim은 reservation과 분리해 `canary_successor`와 `production_route`로 관리한다.
각 role row는 owner kind, profile/policy ID·revision, optional generation과 monotonic revision을 가지며 모든
전이는 exact owner/role/generation predicate로 다른 role이나 family reservation을 변경하지 않는다.

Rollout family와 최초 assignment contract는 권한 있는 `platform routing operator`의 idempotent
`rollout_family_create` command로만 만든다. Request는 command ID, actor, environment, canonical immutable
activation domain, assignment algorithm/unit contract version과 server-allowed bucket count를 포함하지만 raw
seed를 포함하지 않는다. `RoutingActivationCoordinator`는 environment activation guard를 잠그고 actor 권한,
current global kill state, canonical domain 유효성 및 기존 family와의 overlap을 다시 검증한다. Server는
CSPRNG로 최소 128-bit non-secret opaque seed를 발급하고 immutable family/domain row, initial assignment contract, revision 0의 family assignment-registry row,
domain registry revision, actor/request-bound receipt와 canonical audit를 한 transaction에 commit한다. Audit/
receipt 실패는 모든 write를 rollback한다. 같은 actor/request 재전달만 기존 family/contract를 반환하고 같은
command ID의 다른 request는 `model_routing.rollout_family_command_conflict`, 같은 domain 또는 overlapping
domain의 concurrent creator loser는 `model_routing.activation_domain_conflict`와 zero-write로 닫는다. 서로
겹치지 않는 family create command는 같은 environment guard에서 순차 overlap 검증한 뒤 각각 commit할 수
있다.

기존 family에 algorithm/seed/unit/count가 다른 추가 contract가 필요하면 권한 있는
`platform routing operator`의 idempotent `rollout_assignment_contract_issue` command만 사용한다.
Canonical request는 actor/command ID, exact family/domain, expected family revision과 assignment registry
revision, server-allowed algorithm/unit contract version과 bucket count를 포함하고 raw seed를 포함하지
않는다. Coordinator는 existing family row를 먼저, family 생성 때 만든 assignment-registry row를 다음으로 잠그고 actor 권한, immutable family/domain
binding, current family lifecycle, expected revisions와 algorithm allowlist를 commit 직전에 재검증한다. Missing registry row를 issue
command가 임의 생성하지 않고 state conflict와 zero-write로 닫는다. Server는 새 최소 128-bit
CSPRNG seed를 발급하고 immutable contract, 증가한 assignment registry revision, actor/request-bound
receipt와 `model_routing.assignment_contract.issued` audit를 한 transaction에 commit한다. 기존 profile과
contract의 reference/seed/bucket은 변경하지 않는다. Exact replay만 기존 contract/receipt를 반환하며 같은
command ID의 다른 actor/request는 `model_routing.assignment_contract_command_conflict`, stale revision과
concurrent loser는 `model_routing.assignment_contract_state_conflict`로 contract/revision/receipt/audit zero-write다. Audit/receipt 실패도
전체 rollback한다. Profile propose는 해당 family에 존재하는 immutable assignment contract reference만
받을 수 있다.

Stable canary assignment는 profile에 사전 등록한 immutable
`canary_assignment_contract_version`, non-secret opaque `canary_assignment_seed`,
`canary_bucket_count`와 target bucket range로만 계산한다. Server는 contract가 정의한 canonical encoding으로
environment, `rollout_family_id`, activation domain digest, organization, workflow와 immutable deployment
ID를 결합해 assignment unit을 만든다. Bucket은
`uint64_be(SHA256(canonical_assignment_bytes)[0:8]) mod canary_bucket_count`로 계산하며 canonical bytes에
contract version과 seed도 포함한다. V1의 `canary_bucket_count`는 10,000이고 target ranges는
`[0, 10000)` 안의 non-empty, 정렬·비중첩 구간이며 합계가 10,000보다 작아야 한다. Unknown contract,
잘못된 seed/count/range는 profile propose를 zero-write로 거부한다. Profile
ID/revision, safety generation, Worker/process identity, 실행·재시도 횟수, 시간과 runtime random 값은
bucket 입력이 아니다. Client와 task payload는 assignment unit, seed 또는 bucket을 주장할 수 없다.
Seed는 rollout family 생성 또는 `rollout_assignment_contract_issue`에서 server가 CSPRNG로 발급한 최소
128-bit 값이며 profile 작성 request는 raw seed를 제출하지 않고 immutable contract reference만 고정한다.
같은 family의 새 profile은 기존 contract reference를 재사용할 수 있다. Reseed/algorithm/unit/count 변경은
위 issue command가 만든 server-issued 새 assignment contract와 새 `proposed` profile, 독립
canary-start 승인을 요구한다.
같은 assignment contract와 deployment는 Worker, retry와 process restart가 달라도 같은 bucket을 사용한다.
새 profile이 기존 assignment contract와 seed를 그대로 고정하면 profile revision만으로 rebucketing하지
않는다. Bucket algorithm/seed/unit contract 변경은 새 `proposed` profile과 독립 canary-start 승인을
요구하고, target range 변경은 bucket 값을 바꾸지 않은 채 새 profile에 사전 등록한 eligibility만 바꾼다.
원 assignment unit과 seed는 API, trace와 audit에 노출하지 않고 safe contract version과 range digest만
투영한다.

최초 rollout family에는 production predecessor가 없을 수 있다. `production_route`는 canonical owner
kind를 `profile | non_v2_policy | blocked_no_safe_path`로 구분한다. Canary start는
`canary_successor` role을 새 profile/generation으로 설정하고 기존 `production_route`가 있으면 그대로
유지한다. 최초 rollout처럼 route가 없으면 profile에 고정한 exact current-valid non-V2 rollback policy를
`non_v2_policy` owner로 같은 transaction에서 초기화한다. Runtime resolver는 stable canary target에만
successor V2를 사용하고 non-target은 canonical `production_route`를 해소한다. Route owner가 profile이면
exact profile/generation을, non-V2이면 exact policy/revision을 snapshot에 고정하며
`blocked_no_safe_path`이면 provider I/O 전 typed failure로 닫는다.

최초 production promotion은 `production_route`를 successor `profile` owner/generation으로 바꾸고
`canary_successor` role을 비우며 successor만 `production_active`로 전이한다. 존재하지 않는 predecessor
row나 `superseded` audit는 만들지 않는다. 이후 successor promotion부터만 기존 production predecessor를
같은 transaction에서 `superseded`로 전이한다.

`proposed -> production_active` 직접 전이는 금지한다. Canary 시작은 아직 존재하지 않는 canary 결과를
요구하지 않고 exact manifest의 locked `activation_holdout`과 실행 준비 상태를 요구한다. Production 승격은 실제 limited-canary
근거를 요구한다. 성공한 승격은 `production_route`를 successor profile/generation으로 전환하고 `canary_successor` role을
비운다. Predecessor가 있으면 successor를 `production_active`, predecessor를 `superseded`로 같은
transaction에서 전이한다. 최초 rollout처럼 predecessor가 없으면 successor state와 두 role 전이만 commit한다. 이미 시작한 run은 기존 snapshot을 유지하고 새 run만 successor를 선택한다.
`superseded`는 정상 cutover 상태이므로 새 run의 strategy resolution에서만 predecessor 선택을 막는다.
이미 시작해 predecessor profile/revision을 고정한 run은 global kill, generation block, explicit
disable/rollback, rollback policy·credential·model revoke, provider 비활성화 또는 organization scope
상실 같은 별도 current safety override가 없는 한 이후 node attempt도 기존 snapshot으로 완료한다.
Profile 기준 또는 contract digest 변경은 같은 family의 새 `proposed` revision으로 시작한다. Production
scope 변경은 revision update가 아니라 별도 activation-domain migration이며, 그 migration 계약이 Accepted되기
전에는 overlap profile 생성/start를 zero-write로 거부한다. `limited_canary`와 `production_active` 상태만
effective V2의 current profile이 될 수 있다.

이 profile gate는 production `deployed`와 일반 Test Sidebar `test`가 실제 V2 모델을 선택하는 경로에
적용한다. `policy_preview`는 server-known structural gate와 사용 가능한 기존 local/cache 정보만
평가하고 Judge/provider를 호출하지 않는다. 요구 판정이 없으면 최종 모델을 꾸며내지 않고
`preview_status=requirement_pending` safe 상태를 반환한다. 명시적 `benchmark` harness는 proposed profile과 격리된
예산으로 V2 Judge/provider를 실행할 수 있지만 production policy, learner, cache, activation과 외부
부수효과를 변경하지 않으며 완전한 manifest를 남긴다. `execution_mode=benchmark`에서는
`effective_strategy_id=capability_routing_v2`와 `strategy_resolution_reason=benchmark_isolated`를
진단 provenance로 기록할 수 있지만, 이는 `proposed` profile을 production-effective로 승격하지 않는다.
Benchmark decision은 profile activation, accepted decision cache, learner, workflow policy와 production trace에
재사용할 수 없다.

Billable benchmark는 일반 workflow 실행 command가 아니다. 논리 actor `platform routing benchmark
operator`가 명시적 environment/organization과 target workflow/deployment scope를 요청하고, server는
platform benchmark 권한과 target resource의 current `execute` 권한을 모두 검증한다. Admission command는
server-derived `actor_platform_authorization_fence_revision`,
`actor_organization_authorization_fence_revision`과 exact target의
`workflow_resource_authorization_fence_revision | deployment_resource_authorization_fence_revision`을
canonical request와 immutable run intent에 결합한다. Client나 workload payload는 revision을 선택하거나
덮어쓸 수 없다. Admission transaction은 platform actor authorization fence -> actor organization
authorization fence -> target resource authorization fence -> command receipt/run/첫 dispatch 순서로 잠그고
current role·organization scope·`execute` 권한과 bound revision을 commit 직전에 다시 검증한다.
Membership, principal, team-role 또는 target resource grant mutation도 자신이 바꾸는 같은 fence를
exclusive하게 잠그고 revision을 증가시킨다. Revoke winner 뒤 admission은 resource-hiding denial과
run/receipt/success audit/provider I/O zero-write이고, admission winner만 command receipt, immutable run
intent, accepted audit와 첫 `next_stage=judge`를 가진 durable `benchmark_dispatch_intent`를 원자
commit한다. Dispatch intent는 run ID, deterministic stage/ordinal/exact model attempt key, monotonic
revision, `pending | leased | terminal`, DB-clock `available_at`, bounded `lease_until`과 fencing token을
가진다. API command 재전달도 위 authorization fences와 current permission을 먼저 재검증한 뒤에만 기존
receipt/run을 반환하며, 권한 회수 뒤에는 receipt/run detail을 숨긴다. Pending/expired dispatch 자체는
취소하지 않고 terminal denial로 수렴시킨다.

Recovery dispatcher는 DB clock과 expected revision으로 pending 또는 만료 lease를 claim하고 fencing token을
증가시킨다. 같은 run/stage/ordinal/exact model의 attempt intent와 ADR-0069 provider usage operation은
unique하다. Worker가 admission 뒤 첫 attempt 생성 전에 종료되면 lease 만료 후 다른 Worker가 같은 dispatch를
재개한다. Attempt intent commit 뒤 종료되면 같은 attempt/operation state에서 재개하며
`provider_started` 또는 outcome-unknown attempt를 다시 호출하지 않는다. Concurrent redelivery/recovery는
CAS winner 하나만 attempt를 생성·진행하고 stale fencing token의 write는 zero-write다. Stage terminal
결과가 다음 stage를 요구하면 같은 transaction에서 next stage/ordinal/model key의 deterministic pending
dispatch를 만들고, 마지막 stage면 같은 transaction에서 run과 dispatch를 terminal로 닫는다. 현재 stage만
terminal이고 run은 non-terminal인데 next dispatch가 없는 committed 상태는 허용하지 않는다. Terminal
transaction 직후 process가 종료돼도 recovery는 이미 생성된 next dispatch를 claim하며 duplicate finalizer는
stage/ordinal unique key와 CAS로 같은 dispatch/run outcome에 수렴한다.

같은 Billing Principal 아래 Judge와 각 후보 attempt의 credential, egress, budget admission과 durable
intent를 provider I/O 전에 각각 기록한다. 각 recovered Judge/candidate attempt는 current platform benchmark role과 target `execute` 권한을 다시
판정하고 current fence revisions가 immutable run intent에 고정된 admission revisions와 정확히 같은지
검증한다. ProviderExecutionCapability는 run authorization identity와 그 exact
platform/organization/target-resource revisions를 binding한다. Run 도중 revision을 in-place refresh하지
않으며 어느 revision이 바뀌면 새 benchmark command를 요구한다. Provider-start transaction은 일반 safety
fence에 이어 이 authorization fences를 같은 canonical order로 shared-lock하고 current
role/scope/permission, run-bound revisions와 capability binding을 다시 검증한 뒤에만 `provider_started`를
commit한다. Permission mutation은 같은 fence의 exclusive lock을 사용한다. Revoke
winner 뒤 해당 attempt와 이후 attempt의 provider I/O는 0회이고 run/dispatch는 terminal denied와 redacted
outcome audit로 수렴한다. Start winner가 먼저 commit한 exact attempt만 이미 승인된 호출로 완료하며 다음 attempt는 같은
run-bound revisions의 current 여부를 다시 검증한다. Benchmark command ID는 actor, target/profile, budget,
manifest와 canonical request hash에 결합한다. 다른 actor/request 재사용은
`model_routing.benchmark_command_conflict`와 run/receipt/success audit/provider I/O zero-write로 닫는다.
Dispatcher는 DB lease를 provider I/O 동안 유지하지 않고 attempt의 ADR-0069 상태를 권위 경계로 사용한다.
현재 platform benchmark actor/API가 없으므로 일반 product API에서 billable benchmark를 열지 않고, 구현
전에는 독립 승인된 workload identity와 versioned release artifact만 허용한다.

### 16. Strategy governance를 네 권한 계층으로 분리한다

| 계층 | 책임 주체 | 허용되는 결정 | 금지되는 결정 |
| --- | --- | --- | --- |
| 전략 등록 | 개발자·아키텍트의 검토된 코드 릴리스 | immutable strategy ID/contract version, hard gate와 fallback 등록·폐기 | 배포만으로 production V2 자동 활성화 |
| 플랫폼 활성화 | 논리 actor `platform routing operator` | activation profile 승인, environment/scope/canary/rollback 설정, global kill enable과 독립 승인된 recovery | 전략 코드 변경, tenant 원문 열람, stale profile 강제 활성화, 단독 actor의 global kill 해제 |
| Canary evidence 수집 | 논리 actor `platform routing evidence system`과 등록 source adapter | 일반 evidence append, watermark, seal과 blocking generation block | Profile lifecycle·role·route 변경, activation/scope 확대 |
| 봉인 근거 late reconciliation | 최소 권한 논리 actor `platform routing evidence reconciliation system` | Exact sealed snapshot의 pre-cutoff late event에 대해 미승격 snapshot invalidation, current owner generation block+disable+rollback 또는 superseded historical generation block-only를 상태별 한 transaction에 수행 | 일반 evidence/guardrail breach로 lifecycle 변경, 임의 profile·route 선택, activation/scope 확대 |
| 격리 benchmark | 논리 actor `platform routing benchmark operator` + target resource `execute` 권한 | proposed profile의 사전 승인 scope·budget·manifest로 진단 run 시작 | 일반 workflow 사용자의 platform profile 실행, production artifact 변경 |
| workflow 선택 | workflow `deploy` 권한자 | 플랫폼이 허용한 strategy version과 immutable rollout family/domain 선택 또는 더 안전한 경로로 opt-out | 허용 scope 확대, threshold 변경, 미승인 version·family/domain 선택 |

현재 RBAC에는 platform-scoped routing/benchmark operator, trusted evidence workload identity와 전용 관리
API가 없다. 이 경계가 구현되기 전 production activation과 billable benchmark는 각각 독립 검토를 거친
versioned release/deployment artifact와 workload identity로만 승인하며 일반 organization manager UI/API에서
변경하거나 실행하지 않는다.

Limited canary 시작과 production 승격은 서로 다른 상태 전이다. 각 command는 current expected
revision과 작성자와 다른 독립 승인자를 검증한다. Canary 시작은 `proposed -> limited_canary`만 수행하고
production scope를 열지 않는다. Coordinator는 성공한 canary start transaction에서 새 immutable
profile-bound safety generation을 할당하고 `canary_successor` role, first-rollout canonical
`production_route`, immutable current/next evidence schedule, 두 window와 required branch별 및 reserved
`not_required` empty aggregate, current/next window별 manifest source-instance watermark rows, receipt와 audit를 원자 설정한다. Schedule/watermark 초기화가 모두 commit된 뒤 stable
canary assignment에만 capability를 발급한다. 기존 `production_route`는 유지하고 route가 없을 때만
profile에 고정한 exact current-valid non-V2 rollback policy를 `non_v2_policy` owner로 초기화한다.
Command는 expected domain generation/role/schedule revision과 immutable evidence schedule contract를 canonical
request에 포함하며 guard lock 아래 current 값과 일치할 때만 다음 monotonic generation을 한 번 할당한다.
어느 evidence row 초기화나 receipt/audit가 실패해도 generation/state/role/route를 포함한 전체 transaction을
rollback한다. Production 승격은 `limited_canary -> production_active`만 수행하며 그 generation의
contiguous promotion evidence set에서 required branch별·전체 canary gate가 모두 통과해야 한다.

Activation evidence source kind는 `activation_holdout`과 `activation_canary_runtime`으로 제한한다. 등록된
holdout evaluator의 immutable `activation_holdout` artifact는 locked holdout branch만 충족하며 canary
sample, watermark 또는 safety block으로 집계하지 않는다. `record_canary_evidence`는 trusted evidence workload identity인 profile-bound runtime monitor의
`activation_canary_runtime` event만 actual requirement-source branch/version별 aggregate·snapshot·safety
block에 반영한다. 이 source kind는 명칭과 무관하게 같은 generation의 `limited_canary`, current
`production_active`와 `superseded` pinned attempt terminal evidence를 포함한다. Generation이 더는 새 run을
admit하지 않더라도 이미 pinned된 attempt와 provider usage correction이 끝날 때까지 evidence intake와
window rotation은 열린 상태다.

Role release, expiry, disable, rollback 또는 safety block은 provider start에 쓰는 runtime capability generation을
즉시 invalid/blocked로 만들고 evidence lifecycle은 `draining`으로 전이한다. 이 safety 전이와 evidence
`closed` 전이는 분리한다. Trusted `platform routing evidence lifecycle system`만 idempotent
`close_canary_evidence_generation` command를 제출해 drain이 끝난 evidence generation을 terminal하게
닫는다. Canonical request는 profile revision, generation, expected schedule revision, server가 읽은 exact
`drain_revision`/drain-summary digest, terminal pinned-attempt watermark/digest, authoritative source-barrier
coverage digest와 current `canary_usage_validity_epoch` 및 pending count 0을 결합한다.

Close transaction은 environment/profile/role fence -> schedule -> generation drain summary -> generation usage
validity row -> generation row 순서로 잠근다. 개별 attempt, ADR-0069 operation, source event/projection,
watermark receipt 또는 correction intent를 다시 열거해 역순으로 잠그지 않는다. Lock 안에서 새 run admission이
닫혔는지, summary의 non-terminal attempt/operation/pending-usage count가 모두 0인지, terminal watermark와
source-barrier coverage digest가 final cutoff를 덮는지, summary revision/digest와 usage epoch가 canonical
request와 같은지를 검증한다. Summary가 missing, stale, 불완전하거나 count/digest 불변조건을 만족하지
않으면 `model_routing.canary_evidence_drain_pending` 또는
`model_routing.canary_generation_close_conflict`와 close/receipt/success audit zero-write다. Finalizer,
append, watermark 또는 correction winner가 summary revision을 먼저 바꾸면 close는 conflict로 재제출한다.
Close가 profile/role을 먼저 잡은 동안 operation-first finalizer/reconciler가 기다리더라도 close는 그
operation/projection lock을 기다리지 않으므로 lock cycle이 없다. 반대로 finalizer/reconciler의 summary
commit이 먼저 끝나면 close는 최신 summary를 소비한다.

모든 조건이 current일 때만 마지막 window를 새 tail 없이 봉인하고 schedule과 evidence generation을
`closed`로 전이하며 immutable close receipt/audit를 원자 commit한다. Close winner 뒤에는 새 activation
source event, window, admission과 capability를 만들지 않는다. 이후 도착한 ADR-0069 authoritative usage
correction은 위 closed-generation append-only correction projection과 drain-summary revision으로만 수렴하며
schedule/generation을 다시 열지 않는다. Late correction은 immutable close receipt를 변경하지 않으며 exact
close replay는 current usage epoch/drain revision이 전진했더라도 기존 final snapshot/receipt를 반환한다.
Concurrent close는 unique/CAS winner 하나에 수렴한다.

`execution_mode=benchmark`의 격리 product benchmark는 별도 `product_benchmark` source kind와 diagnostic
namespace만 사용하며 `record_canary_evidence` 또는 다른 activation evidence write를 제출할 수 없다.
그 시도는 `model_routing.canary_evidence_source_ineligible`과 event/receipt/aggregate/snapshot/block/audit
zero-write로 닫는다. Learner, optimizer와 scheduler도 candidate를 제안하거나 lifecycle command를
제출할 뿐 activation evidence producer 권한을 얻지 않는다. 일반 evidence append와 cutoff와 무관한 guardrail breach 뒤 profile state 변경·disable/rollback은 권한 있는
`platform routing operator`의 idempotent emergency command만 수행한다. Sealed snapshot의 cutoff 이전 late
event는 최소 권한 `reconcile_canary_late_evidence`가 promotion 전후를 함께 처리한다. 미승격이면 snapshot
invalidation만 기록해 lifecycle을 바꾸지 않는다. Exact current production owner이면 generation
block+disable+rollback/`blocked_no_safe_path`, superseded owner이면 historical generation block-only를 원자
수행하며 다른 profile/route 선택, 활성화 또는 scope 확대는 할 수 없다.

Profile 유효 기간은 DB current time으로 판정한다. `valid_until`을 지난 profile은 expiry scheduler가
늦더라도 resolver가 즉시 V2 부적격으로 처리한다. Scheduler는 profile row를 직접 변경하지 않고 논리
service actor `platform routing lifecycle system`으로 deterministic `activation_profile_expire` command를
제출한다. Command identity는 profile/family/environment, expected profile revision과 immutable
`valid_until`, state가 role을 가질 때는 expected role/generation에 결합한다. Coordinator는 DB clock,
current non-terminal state와 role claim을 재검증해 `expired` 전이, receipt와 redacted audit를 한
transaction에 commit한다. `proposed` expiry는 role, reservation과 generation을 변경하지 않는다.
`limited_canary` expiry는 exact `canary_successor` owner/generation일 때만 그 role을 비우고 generation을
무효화한다. `production_active` expiry는 exact `production_route` owner/generation을 무효화하고 exact
current-valid non-V2 rollback policy가 있으면 route를 그 policy로, 없으면 `blocked_no_safe_path`로 원자
전이한다. Family activation-domain reservation은 어떤 expiry에서도 유지한다. Role mutation predicate는
owner profile ID/revision, role과 generation을 모두 비교해 다른 profile role을 변경할 수 없으며 중복
delivery는 기존 receipt를 반환한다.

모든 activation/profile command receipt는 command ID만이 아니라 actor principal과 canonical request
hash를 저장한다. Canonical request는 command kind, environment/family/profile, target state,
activation domain/scope digest, bounded reason과 rollback policy version을 기본으로 포함한다. 사람 주체의
platform command는 current `actor_platform_authorization_fence_revision`, service command는 immutable workload
authorization identity/revision을 추가한다. Evidence generation close는 expected schedule revision, server-derived exact drain revision/digest,
terminal pinned-attempt watermark/digest, source-barrier coverage digest와 current canary usage epoch/pending
count를 추가한다. Benchmark command는 server-derived actor platform/organization과 exact target-resource
authorization fence revisions를 추가한다. Family create는
assignment algorithm/unit contract와 bucket count, domain command는 expected profile/family/domain-role
revision, canary start는 expected domain generation, expiry는 optional expected role/generation, promotion은 promotion evidence start/end sequence, ordered member snapshot
ID/revision/hash/invalidation-revision/usage-correction-revision digest와 expected generation
`canary_usage_validity_epoch`를 추가한다. Global kill command는 expected environment guard state/revision/epoch, command actor의 expected platform
authorization fence revision과 disable의 exact recovery approval ID/revision·approver authorization fence
revision을 포함한다. Recovery approval issue/revoke는 bound guard epoch, command actor의 expected platform
authorization fence revision, approval ID/expected revision, `valid_until`과 bounded remediation contract digest를
포함한다. Raw tenant data, incident/remediation
payload와 assignment seed는 포함하지 않는다.
같은 command ID·actor·hash 재시도만 기존 receipt를 반환한다. Actor 또는 canonical request가 다를 때
충돌 코드는 command registry가 다음처럼 결정하고 모든 mutation/receipt/success audit를 zero-write한다:
`rollout_family_create`는 `model_routing.rollout_family_command_conflict`, assignment contract issue의
command ID actor/request 불일치는 `model_routing.assignment_contract_command_conflict`, global kill은
`model_routing.global_kill_command_conflict`, recovery approval issue/revoke는
`model_routing.global_kill_recovery_approval_command_conflict`, holdout은
`model_routing.activation_holdout_command_conflict`, `record_canary_evidence`와
`reconcile_canary_late_evidence`는 `model_routing.canary_evidence_conflict`, usage-derived cross-source 중복은
`model_routing.canary_evidence_duplicate_operation`, watermark advance는
`model_routing.canary_watermark_command_conflict`, snapshot seal은
`model_routing.canary_snapshot_seal_conflict`, evidence generation close는
`model_routing.canary_generation_close_conflict`, promotion evidence correction은
`model_routing.promotion_evidence_reconciliation_conflict`, benchmark는
`model_routing.benchmark_command_conflict`, workflow policy select/opt-out은
`model_routing.workflow_policy_version_conflict`를 사용한다. Profile propose/start/promote/expire/emergency
disable/rollback 등 나머지 lifecycle command만 `model_routing.activation_command_conflict`를 사용한다.
Assignment contract issue의 expected family/assignment-registry revision이 stale하거나 registry row가 없거나
concurrent winner가 먼저 commit한 경우에는 `model_routing.assignment_contract_state_conflict`를 반환하고
contract/revision/receipt/success audit를 zero-write한다. Recovery approval issue/revoke의 expected row state/revision이 stale하거나 issue/revoke/consume winner가
먼저 commit하면 `model_routing.global_kill_recovery_approval_state_conflict`와
approval/receipt/success audit zero-write다. Kill disable의 approval missing/expired/revoked/consumed, actor
비독립성, remediation mismatch 또는 approver current role/fence 불일치는
`model_routing.global_kill_recovery_approval_required`와 disable success write zero-write다.

성공 mutation의 profile state/revision, command idempotency receipt와 canonical audit는 같은 DB transaction에서
commit한다. Capability Routing V2의 canonical `action`/`status`는 이 ADR과 API의 다음 exact allowlist로
고정하며 command 문자열에서 동적으로 합성하거나 같은 terminal event에 여러 action을 만들지 않는다.
`audit_logs.status`는 canonical audited outcome이다. 승인된 management/terminal publication은 `success`,
보안 breach·승격 근거 무효화는 `failure`로 기록하고 holdout·benchmark 세부 결과는 safe
`result_status`로 분리한다.

| 사건 | Canonical action | status | Safe result_status |
| --- | --- | --- | --- |
| Strategy 등록 | `model_routing.strategy.registered` | `success` | - |
| Strategy 폐기 | `model_routing.strategy.retired` | `success` | - |
| Rollout family 생성 | `model_routing.rollout_family.created` | `success` | - |
| Assignment contract 발급 | `model_routing.assignment_contract.issued` | `success` | - |
| Activation profile 제안 | `model_routing.activation_profile.proposed` | `success` | - |
| Holdout admission | `model_routing.activation_holdout.admitted` | `success` | `admitted` |
| Holdout terminal artifact publication | `model_routing.activation_holdout.completed` | `success` | `valid`, `invalid`, `incomplete`, `denied` |
| Limited canary 시작 | `model_routing.activation_canary.started` | `success` | - |
| Non-blocking canary evidence append | `model_routing.canary_evidence.recorded` | `success` | `non_blocking` |
| Canary hard-stop breach (direct event 또는 aggregate crossing) | `model_routing.canary_breach.detected` | `failure` | `generation_blocked` |
| Canary source watermark 전진 | `model_routing.canary_watermark.advanced` | `success` | - |
| Canary snapshot 봉인 | `model_routing.canary_snapshot.sealed` | `success` | - |
| Canary evidence generation drain/종료 | `model_routing.canary_generation.closed` | `success` | `drained` |
| Sealed snapshot late evidence 또는 promoted-member usage correction invalidation | `model_routing.activation_evidence.invalidated` | `failure` | `pre_promotion_snapshot_invalidated`, `pre_promotion_snapshot_invalidated_generation_blocked`, `rolled_back`, `blocked_no_safe_path`, `superseded_generation_blocked`, `usage_correction_rolled_back`, `usage_correction_blocked_no_safe_path`, `usage_correction_superseded_generation_blocked` |
| Production 승격 | `model_routing.activation_profile.promoted` | `success` | - |
| 실제 predecessor 종료 | `model_routing.activation_profile.superseded` | `success` | - |
| Profile 만료 | `model_routing.activation_profile.expired` | `success` | - |
| Workflow V2 family/domain 선택 | `model_routing.workflow_policy.selected` | `success` | - |
| Workflow V2 opt-out | `model_routing.workflow_policy.opted_out` | `success` | - |
| Emergency disable | `model_routing.activation_profile.disabled` | `success` | `proposal_cancelled`, `canary_closed`, `rolled_back`, `blocked_no_safe_path` |
| 명시적 rollback | `model_routing.activation_profile.rolled_back` | `success` | `rolled_back` |
| Global kill recovery approval 발급 | `model_routing.global_kill_recovery_approval.issued` | `success` | `active` |
| Global kill recovery approval 회수 | `model_routing.global_kill_recovery_approval.revoked` | `success` | `revoked` |
| Global kill enable | `model_routing.global_kill.enabled` | `success` | - |
| Global kill disable 및 approval consume | `model_routing.global_kill.disabled` | `success` | `approval_consumed` |
| Benchmark admission | `model_routing.benchmark.admitted` | `success` | `admitted` |
| Benchmark terminal publication | `model_routing.benchmark.completed` | `success` | `succeeded`, `denied`, `failed`, `outcome_unknown` |

Blocking evidence, sealed-snapshot pre-cutoff late evidence reconciliation, promoted-member usage correction과
일반 evidence append는 같은 원인에 대해 서로 배타적인 action 하나만 기록한다. Promotion에서 실제 predecessor가 있을 때만 `model_routing.activation_profile.superseded`를 추가한다.
Exact command 또는 terminal finalizer replay는 기존 audit를 반환하고 새 row를 만들지 않는다. Conflict/stale
loser는 위 success action을 기록하지 않는다. Audit recorder 또는 durable command receipt 기록이 실패하면
상태와 revision을 모두 rollback하고 성공 응답을 반환하지 않는다. 권한 거부 command는 profile state/revision과 command receipt를
만들지 않지만, 기존 security audit policy가 요구하면 safe actor/reason만 담은 transaction-bound
`permission.denied` audit을 남길 수 있다. 이 denial audit은 성공 mutation audit이나 receipt가 아니다.
비동기 notification outbox는 후속 전달에만 사용하며 canonical audit를 대체하지 않는다. Learner,
optimizer, benchmark와 scheduler는 candidate를 제안할 수 있지만 canary 시작, production activation 또는
emergency disable을 직접 쓰지 않는다.

Rollout family create, canary start, production promote, emergency disable/rollback, active role
expiry/supersede와 activation-domain migration은 단일 `RoutingActivationCoordinator` application 경계를
통과한다. Coordinator는 외부 I/O 전에 짧은 DB transaction을 열고 canonical environment activation guard
row를 먼저 잠근 뒤 family/profile/domain-role row를 고정 순서로 잠근다. Production promote는 이어서
holdout/source/credential/Worker/model의 authoritative fence와 profile에 고정된 rollback policy를 잠그고,
그 뒤 promotion evidence set의 ordered member snapshots와 generation safety row를 잠근다. 이 순서는
current-production late reconciliation과 rollback revoke가 사용하는 `rollback policy -> evidence set ->
member snapshot -> generation` 상대 순서와 같다. Guard row는 environment registry와 함께 사전
생성하며 missing row를 command가 임의 생성하지 않고 `model_routing.activation_guard_unavailable`로
fail-closed한다.

권한 있는 사람이 제출하는 모든 platform activation mutation은 global kill에만 한정하지 않고 canonical
request에 `actor_principal`, platform role scope와 caller가 읽은
`actor_platform_authorization_fence_revision`을 결합한다. Lock 순서는 `environment guard -> command actor
platform authorization fence -> family/profile/domain-role 및 command별 resource`다. Coordinator는 같은
transaction에서 current effective role과 fence revision을 commit 직전에 재검증하며, role grant/revoke,
role-permission mapping 또는 principal lifecycle 변경도 동일 fence를 잠근 뒤 revision을 증가시킨다.
Role mutation winner 뒤 stale command는 `permission.denied`와 mutation/receipt/success-audit zero-write이고,
command winner가 먼저 commit한 경우에만 그 command가 성공한다. 여러 actor fence가 필요한 recovery
approval은 principal/scope canonical order를 사용한다. Evidence, expiry와 recovery dispatcher 같은 논리
service actor command는 human platform role을 합성하지 않고 별도 immutable workload authorization
identity/revision을 canonical request와 transaction에 결합한다.

Environment guard row는 active domain overlap과 global kill을 직렬화하는 fence다. Global kill state를
변경하는 command만 guard state/revision/epoch CAS를 사용한다. Family/domain command는 guard lock 아래
current kill state와 전체 domain overlap을 다시 읽되, expected profile/family/domain-role revision만 자신의
optimistic concurrency 조건으로 사용한다. 성공한 domain mutation은 별도 monotonic domain registry revision을
증가시킬 수 있지만 unrelated command의 expected guard revision을 stale하게 만들지 않는다. Production
promote는 expected evidence start/end sequence와 ordered member snapshot digest/invalidation revisions,
blocking breach와 함께 DB
time/profile state·revision, exact role owner/generation, holdout/source manifest, credential policy, actual Worker
readiness, global model eligibility, exact rollback policy와 global/scoped safety를 commit 직전에 다시 판정하고
winner의 profile state, role claim, command receipt와 canonical audit를 함께 commit한다.

같은 environment에서 겹치는 두 family command가 동시에 들어오면 한 command만 승리한다. Loser 또는
bounded lock timeout은 `model_routing.activation_domain_conflict`를 반환하고 profile state/revision,
domain role, receipt와 success audit를 모두 zero-write로 유지한다. 서로 겹치지 않는 두 command는 동일
expected guard revision을 읽었더라도 environment lock 아래 순차 overlap 검증한 뒤 각각 자신의 domain-local
CAS로 정상 commit할 수 있다. Holdout/benchmark 계산, provider 호출, notification 전송과 다른 network I/O
중에는 activation lock을 유지하지 않는다. 이 coarse environment serialization은 드문 control-plane
mutation의 overlap 검증만 직렬화하며 runtime routing request를 직렬화하지 않는다.

Workflow strategy select/opt-out은 profile mutation과 별개의 durable workflow policy mutation이다. Command는
current workflow policy의 expected version과 idempotency key뿐 아니라 서버가 읽은 exact
`actor_organization_authorization_fence_revision`과 `workflow_resource_authorization_fence_revision`을
canonical request에 포함한다. Client는 이 revision을 선택하거나 덮어쓸 수 없다. Policy row가 없을 때만
`expected_version=null`을 허용하며 첫 생성도 CAS insert로 처리한다. Opt-out은 row 삭제가 아니라 명시적
`opt_out` 상태의 새 policy version으로 저장한다. V2 선택 policy는 exact activation profile ID/revision이
아니라 immutable `rollout_family_id`와 `activation_domain_digest`를 고정한다. Idempotency key는 workflow,
expected version, 요청한 strategy/version, V2이면 family/domain binding 또는 opt-out의 canonical request
hash에 결합한다. 같은 key·같은 request 재시도도 organization/workflow authorization fences를 먼저 잠가
bound revisions와 current permission을 재검증한 뒤에만 기존 receipt를 반환한다. 권한이 회수됐으면 receipt나
policy detail을 노출하지 않고 resource-hiding denial이며, 같은 key의 다른 request 또는 authorization revision
mismatch는 conflict와 zero-write로 닫는다. Transaction은 actor의 active organization membership/scope를 대표하는
organization authorization fence -> workflow의 effective `deploy` grant graph를 대표하는 resource
authorization fence -> workflow policy/receipt 순서로 잠근다. 서버는 lock 안에서 bound revision과 current
`deploy` 권한, active organization/resource scope, family/domain binding 및 선택한 strategy의 current
유효성을 다시 검증한다. Membership/principal/team-role/resource grant mutation은 자신이 바꾸는 동일
authorization fence를 exclusive하게 잠그고 revision을 증가시킨다. Workflow create transaction은 resource
fence revision 0을, membership/grant management는 actor-organization fence를 미리 만든다. Missing fence는
fail-closed하며 strategy command가 이를 lazy create하지 않는다. Workflow/deployment가 bound activation
domain에 포함되지 않거나 다른 family/domain을 동적으로 해소해야
하는 policy는 저장하지 않는다.

Runtime resolver는 새 run을 시작할 때 policy의 exact family/domain에서 current canonical
`production_route`와 optional `limited_canary` successor를 함께 읽는다. Stable assignment가 successor
target이면 successor의 exact profile ID/definition revision/runtime safety revision/generation을 execution
snapshot에 고정한다. Non-target이면 `production_route.owner_kind`를 해소해 `profile` owner의 exact
profile/generation, `non_v2_policy` owner의 exact policy/revision 또는 `blocked_no_safe_path` typed
failure를 고정한다. 최초 rollout에서 predecessor가 없다는 이유로 non-target을 successor로 보내거나 route
부재를 임의 default로 보완하지 않는다. Successor promotion 뒤에는 workflow policy mutation이나 재배포
없이 production route가 successor profile로 바뀌므로 새 run만 successor를 선택하고, 이미 시작한 run은
별도 safety override가 없는 한 pinned route를 유지한다. 다른 family/domain으로 전환하려면 workflow
`deploy` actor가 새 policy CAS command를 제출해야 한다.
성공 시 workflow policy version, command receipt와 canonical audit를 같은 DB transaction에서 commit한다.
같은 expected version의 경합 loser와 stale command는 policy·receipt·success audit zero-write로 conflict를
반환하고, audit 또는 receipt 기록 실패도 전체 transaction을 rollback한다. 권한·scope 거부는 policy와
receipt를 만들지 않으며 기존 security audit policy가 요구하는 safe `permission.denied` audit만 허용한다.

### 17. 실행 policy는 고정하되 보안 lifecycle은 재검증한다

Effective V2 strategy는 다음 교집합으로 결정한다.

```text
전역 feature flag와 emergency kill switch가 허용
AND 지원되는 immutable strategy registry
AND current contract와 일치하고 DB current time이 `valid_until` 전이며 상태가 `limited_canary` 또는 `production_active`인 유효 activation profile
AND profile에 고정된 exact Judge-only 또는 approved learner requirement source가 current
AND profile 상태가 허용하는 production scope 또는 stable canary에 포함된 deployment
AND workflow deploy 권한자가 선택한 policy
AND Judge와 각 work candidate/fallback을 승인하는 current V2 credential policy
AND 현재 Worker가 profile의 최소 runtime capability를 충족
AND profile에 고정된 exact non-V2 rollback policy version이 current-valid
AND global kill과 선택 profile-bound safety generation/block/epoch가 current
```

Runtime은 실행 시작 시 strategy contract, activation profile과 workflow policy version을 snapshot으로
고정한다. Profile은 immutable `profile_definition_revision`, 신규 실행 선택을 제어하는
`admission_lifecycle_revision`, 진행 중 실행을 차단하는 `runtime_safety_revision`을 분리한다. 정상 promotion의
predecessor `superseded` 전이는 admission revision만 증가시키고 runtime safety revision과 predecessor
safety generation/epoch를 바꾸지 않는다. 따라서 새 실행은 predecessor를 선택하지 않지만 promotion 전에
admit된 run은 exact profile definition과 generation을 유지하고 이후 node attempt도 완료할 수 있다.
Emergency kill switch, `disabled`/`expired`, profile `valid_until`, generation-scoped safety block, rollback
policy/credential/model revoke, provider 비활성화와 organization scope 상실은 runtime safety revision 또는
각 authoritative fence를 변경해 snapshot보다 우선하며 Judge, primary와 fallback provider 호출 직전에 재검증한다. DB current time이 pinned profile `valid_until` 이상이면 expiry sweeper/state 전이와
무관하게 그 attempt의 `provider_started`와 provider I/O를 zero-write한다. Pinned profile의 exact
current-valid non-V2 rollback policy가 있으면 독립 work-model admission/durable intent 뒤에만 사용하고,
없으면 canonical `model_routing.activation_profile_expired` typed failure로 닫는다. Strategy resolution의
safe reason `profile_expired`와 외부/worker failure code를 구분한다. Provider-start transaction은 profile을 암묵적으로 expire하지
않으며 deterministic expiry command가 lifecycle/role 전이를 별도로 수렴시킨다. Rollback policy가
current-valid하지 않으면 새 V2 attempt를 시작하지 않고 `model_routing.rollback_target_invalid`로 닫는다. 아직 시작하지
않은 V2 판정은 승인된 stored safe path로 전환하고, 이미 발행한 provider attempt의 outcome은 추측하지 않는다.

Emergency lifecycle은 단일 전역 epoch로 모든 family를 함께 무효화하지 않는다. Environment 전체
kill switch는 `global_routing_kill_epoch`로 관리한다. Activation domain guard는 monotonic
`scoped_routing_safety_generation`을 할당하고, profile disable/rollback/expiry와 blocking safety
evidence는 `(environment, rollout_family, activation_domain, generation)`에 결합한
`scoped_routing_safety_epoch`와 deny-only safety block으로 관리한다. ProviderExecutionCapability는 발급 시점의 exact profile ID/`profile_definition_revision`/`valid_until`,
`runtime_safety_revision`, global epoch, exact profile-bound generation/epoch와 `canary_usage_validity_epoch`,
organization authorization fence, exact organization/provider/purpose data-egress policy fence, decision에
사용한 exact Judge/learner requirement source identity와 `requirement_source_lifecycle_revision`,
credential policy/credential, model/provider lifecycle 및 rollback policy의 immutable current revision을
binding한다. Accepted decision cache를 재사용한 경우에도 cache origin의 exact source lifecycle을 current로
재검증하고 같은 revision을 binding하며, economic admission의 `not_required`만 explicit empty source
fence를 사용한다. Operational evidence가 선택에 기여했다면 server-derived exact aggregate validity row
identity/revision set과 `model_evidence_fence_set_digest`도 binding한다. Caller/task payload는 source/evidence
row set이나 epoch를 선택할 수 없다. `admission_lifecycle_revision`은 신규 run 해소에 사용하며 이미 admit된
capability의 start 유효성에는 binding하지 않는다. Global epoch가 stale하면 모든 family가, 선택 generation의 epoch가 stale하거나
block이 set이면 그 generation만 provider I/O 없이 거부된다. Runtime safety revision 또는 다른 bound safety
revision이 stale하거나 current state가 `disabled`/`expired`이면 해당 attempt를 거부한다. Current state가
정상 promotion receipt에 결합된 `superseded`인 것만으로는 promotion 전 admit된 attempt를 거부하지 않는다. Production predecessor와 limited-canary
successor는 각자 generation을 가지므로 한쪽 block이 다른 쪽을 암묵적으로 해제하거나 무효화하지 않는다.
겹치지 않는 다른 family/domain capability도 자신의 global/generation/lifecycle revision이 current이면
유지된다.

`provider_started` 전이는 위 비교와 분리된 blind write가 아니다. Runtime은 짧은 DB transaction에서
동시 provider start를 허용하는 shared fence를 다음 순서로 획득한다.

```text
environment guard
-> activation profile runtime-safety lifecycle and valid_until
-> optional benchmark actor platform authorization fence
-> organization authorization fence
-> optional benchmark target workflow/deployment resource authorization fence
-> organization/provider/purpose data-egress policy fence
-> requirement source lifecycle fence (canonical source identity order, when bound)
-> credential policy and credential lifecycle
-> model and provider lifecycle
-> rollback policy
-> operational model evidence validity rows (canonical order, when bound)
-> optional sealed snapshot
-> generation canary usage validity row
-> exact generation safety row
-> provider attempt
```

Runtime은 DB clock이 pinned profile `valid_until` 전인지, immutable profile definition과
`runtime_safety_revision`, generation/authorization/organization data-egress/requirement-source/credential/
model/provider/rollback revision, capability-bound operational evidence validity row set/revisions와
`model_evidence_fence_set_digest`, `canary_usage_validity_epoch`/pending count 및 attempt expected state를
다시 검증해 `provider_started`를 원자 전이한다. Operational evidence를 사용하지 않은 decision은 명시적
empty evidence fence set을 binding한다. `not_required`가 아닌 decision의 bound requirement source가
missing, non-active 또는 stale revision이면 해당 V2 attempt는
`model_routing.activation_requirement_source_unavailable`과 provider-start/I/O zero-write로 닫는다. Profile에
고정된 다른 current source로 전환하려면 새 requirement evaluation과 attempt capability를 발급해야 하며,
이미 선택한 모델에 source만 바꿔 끼우지 않는다. Canary usage correction이 pending이거나 어느 bound
revision/epoch가 stale하면 provider start와 I/O를 zero-write한다. 정상 promotion receipt에 결합된
`superseded` admission state는 pinned in-flight run에 허용하되 신규 run 해소에서는 거부한다. Organization scope/permission, organization data-egress policy, requirement source, credential
policy/credential, model/provider 또는 rollback policy revoke/disable mutation은 자신이 변경하는 동일
authoritative fence row를 exclusive하게 획득하고 revision을 증가시킨다. Requirement source revoke/retire는
exact source lifecycle fence만 바꾸고 immutable source definition/profile을 덮어쓰지 않으며, start winner가
먼저 commit한 exact attempt만 완료하고 mutation winner 뒤 모든 새 start는 zero-write다. Organization data-egress fence는 organization, exact
provider/endpoint class와 operation purpose의 허용 상태를 나타내며 ADR-0064/0067의 provider catalog 또는
transport `egress_revision`과 별도다. Missing, stale, revoked 또는 selected provider/purpose denied 상태는
`model_routing.data_egress_policy_denied`와 provider-start/payload/network I/O zero-write로 닫고 다른 policy
scope나 provider detail을 노출하지 않는다. 첫 policy mutation은 egress management service만
`expected_egress_policy_revision=null`로 deterministic deny revision 0 row를 unique insert하고 요청 state와
revision 1을 같은 transaction에 commit한다. Concurrent first mutation은 unique/CAS로 수렴하며 runtime/start
command는 missing policy row를 만들지 않는다. 다수 row를 변경하는 mutation도 위 공통 순서를 따른다. Global kill은 environment
guard의 exclusive mutation fence, profile expiry는 environment guard 뒤 exact activation profile runtime-safety
lifecycle fence를 사용한다. Open assigned direct blocking breach는 environment shared fence 뒤 affected
aggregate와 exact generation safety row를, unassigned schedule-lag direct breach는 exact generation row만
잠근다. Non-blocking open hard-stop first crossing은 affected aggregate 뒤 generation을 잠긴다.
Sealed-snapshot pre-cutoff late reconciliation은 environment/profile/current role 뒤 snapshot relation을
파생한다. Unconsumed branch는 snapshot과 direct kind일 때 generation, promotion-member current branch는
rollback policy·set·member·generation, member historical branch는 set·member·generation, post-promotion
monitoring branch는 snapshot·late-correction aggregates·generation 순서로 exclusive fence를 획득한다.
Rollback revoke도 공통 상대 순서를 사용하고 policy revision을 바꾸므로 reconciliation과 provider start가
stale policy를 실행하지 않는다. Scoped breach는 다른 generation start를
직렬화하지 않는다. Provider/network I/O는 provider-start transaction이 commit된 뒤에만 시작한다.

어떤 lifecycle revoke/kill/breach transaction이 먼저 commit하면 대기한 start는 stale revision/state를 보고
`provider_started`와 provider I/O zero-write로 닫는다. Provider-start transaction이 먼저 commit하면 그
attempt만 이미 시작 승인을 얻은 것으로 보며 이후 revoke는 다음 start부터 차단한다. Bounded fence timeout
또는 serialization retry 소진은 fail-closed하며 lock을 잡은 동안 network I/O를 수행하지 않는다. RBAC
판정이 여러 grant row에 의존하면 start와 revoke가 공유하는 monotonic organization authorization fence
revision을 사용해 grant set 변경을 원자적으로 관측한다.

Scoped block은 false에서 true로만 전이하는 안전 marker이며 activation이나 scope 확대에 사용할 수 없다.
Breach가 난 lifecycle generation의 block은 in-place로 해제하지 않는다. Remediation을 반영한 새 immutable
profile은 locked holdout과 독립 canary-start 승인을 통과한 시점에 Coordinator가 새 generation을
할당하고 `limited_canary` scope에만 capability를 발급한다. 기존 blocked generation이 current
`production_route`의 profile owner면 권한 있는 operator의 emergency disable/rollback이 먼저 그 profile을
닫고 route를 exact current-valid `non_v2_policy` owner로 전환해야 한다. `blocked_no_safe_path`이거나 여전히
blocked profile owner인 상태에서는 remediation canary start를 허용하지 않고 generation/state/role/evidence/
receipt/audit를 zero-write한다. 기존 blocked generation이 `canary_successor` role을 소유하면 operator가 그
role을 먼저 terminal하게 닫아 successor slot을 비워야 한다. 이 선행 전이는 기존 generation block을
해제하지 않는다. Canary start는 commit 안에서 canonical production route가 current-valid
`non_v2_policy`인지 다시 검증하고 새 generation만 limited-canary로 연다. Canary target만 새 generation을
사용하고 non-target은 이미 전환된 non-V2 route를 사용한다. 새 generation에서 canary evidence를 충족하고
별도 독립 production 승격을 통과한 뒤에만 같은 generation을 production role으로 전환한다.

권한 있는 operator의 `activation_profile_emergency_disable`과 `activation_profile_rollback`은 profile
state뿐 아니라 role claim을 같은 transaction에서 전이한다. Target이 role을 소유하지 않은 exact
`proposed` profile이면 expected profile revision을 재검증해 `disabled`와
`result_status=proposal_cancelled`, receipt/audit만 원자 기록하고 canary/production role, generation,
route와 family reservation은 변경하지 않는다. Target이 exact `canary_successor`
owner/generation이면 profile을 `disabled`로 만들고 그 canary role과 generation만 닫으며
`production_route`와 family reservation은 유지한다. Target이 exact `production_route` owner이면
`activation_profile_rollback`은 exact current-valid non-V2 rollback policy를 commit 직전에 재검증한 뒤
profile/generation을 닫고 production route를 그 policy로 원자 전환하며 부적격이면
`model_routing.rollback_target_invalid`와 zero-write다. `activation_profile_emergency_disable`은 rollback
policy가 current-valid하면 같은 route 전환을 수행하고, 없으면 profile/generation을 닫은 뒤 route를
`blocked_no_safe_path`로 만들어 신규 실행과 provider I/O를 fail-closed한다. 어떤 command도 family
activation-domain reservation을 해제하지 않는다. 모든 mutation은 expected owner profile ID/revision,
role, generation과 role revision을 비교하며 stale/concurrent loser가 다른 canary/production role을
변경하지 못한다.

Environment guard는 `global_routing_kill_enabled`, monotonic `global_routing_kill_epoch`, guard revision과
현재 enabled transition의 `enable_actor_principal`을 소유한다. 별도
`platform_authorization_fence_revision`은 `(principal, platform role scope)`별 effective platform role grant
set의 monotonic revision이다. Principal/scope의 첫 grant에서 target fence가 없으면 권한 관리 service가
deterministic key의 unique insert로 revision 0 row를 만들고 같은 transaction에서 그 row를 잠근 뒤 grant와
revision 1을 원자 commit한다. `expected_target_fence_revision=null`은 이 absent-first-grant에만 허용한다.
Concurrent 최초 grant는 unique/CAS winner 뒤 current row를 다시 잠가 순차 적용하며, revision 0 또는 absent
fence는 privileged command actor 권한을 만들지 않는다. 일반 platform command가 자기 actor fence를 lazy
create하는 것은 금지한다. Role grant/revoke, role-permission mapping과 principal lifecycle 변경은 해당
fence row를 exclusive하게 잠그고 revision을 증가시킨다. 다수 principal/scope를 바꾸면 canonical key
순서로 잠그며 authorization 판정과 command payload가 이 revision을 직접 만들 수 없다. 권한 있는
`platform routing operator`만 `global_routing_kill_enable | global_routing_kill_disable` command를 제출할 수
있다. False -> true enable도 `environment guard -> command actor platform authorization fence` 순서로 잠가
current role/revision을 commit 직전에 검증하고 actor principal을 guard에 고정한 뒤 즉시 fail-safe 차단한다.

Disable용 recovery approval은 guard에 고정된 enable actor와 다른 current
`platform routing recovery approver`의 `global_routing_kill_recovery_approval_issue` command로만
발급한다. Approval은 deterministic actor/request-bound ID, environment, 현재 enabled guard epoch,
bounded remediation contract digest, DB-clock `valid_until`, approver principal, issue 시점의 exact
`platform_authorization_fence_revision`과 monotonic approval revision에 결합된 server-owned row이며 stored
state는 `active | revoked | consumed`다. Expiry는 immutable
`valid_until`과 DB clock으로 판정하는 effective 상태이며 별도 비직렬화 write로 row를 바꾸지 않는다.
Raw incident, remediation payload와 approval token은 저장하지 않는다.

Issue command는 `environment guard -> approver platform authorization fence -> command receipt/approval unique
identity` 순서로 잠근다. Exact replay이면 authorization fence 뒤 기존 approval row를 잠가 같은
approval/receipt/audit를 반환하고, 신규이면 같은 lock 아래 unique approval row를 insert한다. Kill이 여전히
enabled이고 expected guard revision/epoch와 approver authorization fence revision이 current이며 approver가
current recovery role을 가지고 enable actor와 다른지 commit 직전에 검증한다. Approval, bound authorization
revision, actor/request-bound receipt와 `model_routing.global_kill_recovery_approval.issued` audit는 한
transaction에 commit한다.
Concurrent duplicate insert loser는 existing exact replay가 아니면 state/command conflict와 zero-write다.

권한 있는 recovery approver의 `global_routing_kill_recovery_approval_revoke`는 `environment guard -> revoke
actor platform authorization fence -> exact recovery approval row`, kill disable은 `environment guard ->
disable actor와 approval approver의 platform authorization fence(canonical principal/scope order) -> exact
recovery approval row` 순서로 잠근다. Revoke는 actor role/fence와 expected approval revision이 current인
active row만 revoked로 전이하고 receipt와 `model_routing.global_kill_recovery_approval.revoked` audit를 원자
commit한다. Disable canonical request는 command kind, environment, desired state, expected guard
revision/epoch, bounded reason, exact server-derived remediation contract digest, disable actor authorization
fence revision과 approval ID/revision/approver authorization fence revision을 포함한다. Coordinator는 lock
안에서 kill actor role/fence, expected guard state/revision/epoch, current remediation snapshot의 canonical
digest와 approval-bound digest 일치, approval의 active/current 상태와 DB-time non-expiry, bound guard epoch,
approval에 고정된 approver authorization fence revision과 current revision/role 일치 및 approval principal이
enable actor와 disable command actor 모두와 다른지 재검증한다.
성공하면 approval을 consumed로 전이하면서 enabled=false, global epoch/guard revision 증가, kill command
receipt와 `model_routing.global_kill.disabled` audit를 한 transaction에 commit한다. 만료 approval은
stored state를 암묵적으로 변경하지 않고
`model_routing.global_kill_recovery_approval_required`와 disable success write zero-write로 닫는다.

Approval 또는 approver-role revoke가 먼저 commit하면 disable은 approval-required와 zero-write이고, disable이
먼저 consume하면 뒤 approval revoke는 state conflict, 뒤 role revoke는 다음 command/capability부터 적용된다.
Role revoke와 issue/revoke/disable은 같은 platform authorization fence revision을 공유하므로 권한 검사 직후
회수된 approver로 kill을 해제하는 구간이 없다. 어느 경로도 guard -> canonical authorization fences ->
approval lock 순서를 뒤집지 않는다. Enable의 true 전이와 승인된 disable의 false 전이는 모두 global epoch와 guard
revision을 한 번 증가시킨다. Kill이 enabled인 동안 새 V2 capability를 발급하지 않는다. Disable 뒤에도
기존 capability를 되살리지 않고 current profile·policy·scope·credential·rollback·Worker gate를 다시
통과한 새 capability만 새 global epoch에 binding한다.

같은 command ID·actor·canonical request 재전달은 기존 receipt를 반환하고 epoch나 approval revision을 다시
증가시키지 않는다. Kill command ID의 다른 actor/request는
`model_routing.global_kill_command_conflict`, approval issue/revoke command ID의 다른 actor/request는
`model_routing.global_kill_recovery_approval_command_conflict`, stale expected guard state/revision은 `model_routing.global_kill_state_conflict`다. Approval issue/revoke의
expected approval row/state/revision stale, concurrent issue/revoke/consume winner와 revoke-after-consume는
`model_routing.global_kill_recovery_approval_state_conflict`와 zero-write다. Disable 시 current approval이
없거나 expired/revoked/consumed이고, approver가 enable/disable actor와 독립적이지 않거나 remediation digest가
다르거나 approver의 current role 또는 authorization fence revision이 approval-bound 값과 다르면
`model_routing.global_kill_recovery_approval_required`와
state·epoch·approval-consume·receipt·success audit zero-write다. Disable command actor 자신의 platform role
또는 fence가 current가 아니면 일반 `permission.denied`로 같은 success write를 zero-write한다.
Canonical audit 또는 receipt 기록 실패도 전체 mutation을 rollback한다.

Mixed-worker rollout에서 V2 contract를 지원하지 않는 Worker는 V2 policy를 V1 의미로 실행하지
않는다. Producer task payload나 Client가 전달한 capability revision은 호환성 근거가 아니며 실제 소비
process의 code-owned build/runtime identity를 검사한다. 해당 delivery는 current-valid non-V2 path로 명시적으로 전환하거나 provider I/O 전 typed
failure로 닫으며, 호환 Worker 비율이 profile readiness를 충족하기 전에는 canary scope를 확대하지
않는다.

Strategy 등록·폐기, activation profile 작성·승인·만료·비활성화, activation-domain migration, workflow 선택과
rollback은 old/new version, bounded scope, actor, reason과 결과를 기록한다. Audit에는 raw prompt,
RAG 원문, credential과 provider payload를 넣지 않는다.

## 계약 완결성 매트릭스

이 표는 Target 구현이 한 경계만 부분 적용하지 않도록 불변조건부터 테스트까지 연결한다. 각 구현
이슈는 자신이 소유한 행의 공식 계약, 실제 구현 위치와 실행 가능한 테스트를 함께 제시해야 한다.

| 경계와 불변조건 | 상태 전이 | Actor/command와 canonical request | Lock/transaction | Cache/evidence identity | Provider I/O 순서 | Rollback | Audit | 테스트·소유 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Effective V2는 모든 current gate의 교집합이다 | requested V2 -> effective V2/non-V2/null | Trusted runtime resolver, pinned strategy/profile/workflow policy | 실행 snapshot 뒤 lifecycle은 호출 직전 재검증 | Profile/source/rollback/Worker/global + profile-bound safety generation/epoch | Gate 실패 시 Judge 포함 I/O 0회 | Exact current-valid non-V2 policy만 사용 | requested/effective/reason safe projection | `M365-A07B`~`A07D`, `F11`, `F24`, `F25`; MBA-366·Activation |
| Accepted cache는 승인 source를 바꾸지 않는다 | miss -> immutable fill 또는 exact hit | Runtime cache query/fill, selection contract key | Unique/CAS fill, 다른 contract overwrite 금지 | Organization/deployment/node, selection contract, profile/source, HMAC epoch | Hit 후 current gate와 selector, 필요 attempt admission | Stale/mismatch는 mandatory miss | 원 requirement source + reuse source, raw input 비저장 | `M365-D02`~`D03`, `E07`; MBA-367 |
| Requirement Judge는 billable 독립 attempt다 | needed -> admitted -> started -> terminal/outcome-unknown | Server-owned Judge invocation, exact capability와 budget | Durable intent가 commit된 뒤 provider call | Judge contract/rubric, Billing Principal, attempt ID | Current scope/lifecycle와 capability·budget·egress admission 뒤 Judge 호출 | Outcome-unknown이면 같은 decision의 Judge retry/secondary 0회, stored safe path도 독립 work-model admission | ADR-0069 ledger + bounded source/reason | `M365-A01`, `A07`, `A12`, `B05A`, `B11A`; MBA-366·Ledger |
| Primary/fallback은 각각 current admission을 통과한다 | selected -> admitted -> started -> terminal | Server selector와 attempt별 capability | Attempt intent/start/outcome 원장 경계 | Pinned selection contract와 current scope/model/credential | Current scope/lifecycle와 capability·budget·egress 재검증 뒤 호출 | Outcome-unknown 재시도 금지, definitive failure만 configured fallback | Attempt별 usage와 redacted routing trace | `M365-A07`, `B04`~`B11`; MBA-366·Ledger |
| Provider start와 safety lifecycle mutation은 한 순서를 가진다 | admitted -> provider_started 또는 blocked | Runtime start gate, lifecycle revoke와 kill/breach/correction command | Environment -> profile -> optional benchmark platform auth -> organization/target-resource auth -> egress -> requirement-source lifecycle -> credential -> model/provider -> rollback -> bound operational evidence rows -> optional snapshot -> canary usage validity -> generation -> attempt 공통 fence. Open assigned direct/crossing은 aggregate->generation, unassigned direct는 generation, sealed reconciliation은 state별 snapshot/set/correction->generation을 잠근다 | Bound profile/runtime/epoch, authorization/egress/requirement-source/credential/model/provider/rollback, model-evidence fence set과 canary usage epoch | DB clock과 모든 bound revision/pending count를 start commit 전에 재검증. 정상 supersede는 pinned run 허용, source/revoke/safety/correction winner 뒤 I/O 0회 | Expiry·source stale은 exact non-V2 rollback 또는 typed failure, timeout fail-closed, started winner replay 금지 | Start와 lifecycle audit 분리 | `M365-C08O`, `C08P`~`C08T`, `F08G`~`F08G5`, `F11C`~`F11C1`, `F18B1`, `F20`~`F20A`, `F24E`~`F24E1`; MBA-366·368·Activation·Ledger |
| Operational evidence는 모델·요구 cohort·산출 계약·실행 attempt와 current usage revision을 넘지 않는다 | terminal provider operation -> immutable sample -> scoped aggregate; usage correction -> pending -> rebuilt revision | Operational evidence system의 `record_operational_model_evidence` / `rebuild_operational_evidence_aggregate`, ADR-0069 correction coordinator | Operation -> sample -> aggregate validity rows. Correction intent+epoch/pending 원자 commit, rebuild CAS로 새 aggregate+pending clear. Selector가 사용한 exact validity rows를 capability start와 공유 | Reuse scope + task fingerprint + requirement cohort + model/evidence contract + usage revision/validity epoch, `model_evidence_fence_set_digest`, 허용 operation당 sample 1개 | Append/rebuild 중 I/O 없음. Bound row correction winner 뒤 provider I/O 0회 | Pending/stale aggregate는 selector·activation/start 근거에서 제외, duplicate/rebuild loser는 기존 receipt 또는 zero-write | Safe metric digest/opaque correction ref만, corrected token/cost 원문 비저장 | `M365-C08`~`C08O`; Model Profile·Ledger |
| Billable benchmark는 일반 workflow 실행이 아니다 | authorized command -> isolated run + durable dispatch -> terminal | Benchmark operator + target `execute`, actor/request-bound command와 recovery dispatcher | Admission은 platform -> organization -> target-resource authorization fences -> receipt/run/첫 dispatch. Attempt별 capability와 provider-start가 같은 fences를 다시 공유하고 permission mutation은 exclusive lock. Lease/fencing/CAS와 deterministic attempt key | Proposed profile, target scope, budget, manifest, run/dispatch/attempt ID와 admission/attempt별 authorization fence revisions | Recovery 후 각 attempt의 current 권한을 reauthorize하고 bound revisions를 start commit에서 재검증한 뒤 ADR-0069 admission과 호출 | Revoke winner 뒤 run/receipt 또는 attempt I/O zero, start winner exact attempt만 완료. Admission-after-crash는 lease expiry로 재개, outcome-unknown replay·production 재사용 금지 | Redacted command/run/dispatch audit와 ledger, revoke 뒤 resource hiding | `M365-F26`~`F26F2`; MBA-340·Benchmark |
| Rollout family와 assignment contract는 명시적 command로만 생성한다 | absent -> immutable family + initial contract; existing family -> additional immutable contract | Platform operator의 actor/request-bound `rollout_family_create` / `rollout_assignment_contract_issue` | Family create는 revision 0 assignment-registry row까지 초기화하고, issue는 existing family -> registry 순서로 잠가 CAS한다. CSPRNG seed/receipt/audit 원자 commit | Immutable family/domain/contract revision, raw seed 입력·출력 금지 | Command 중 provider I/O 없음 | Exact replay는 기존 결과, stale/concurrent/conflicting request는 zero-write. 기존 profile/contract 불변 | Safe family/domain/contract digest만 기록 | `M365-F18A4`~`F18A8`; Activation |
| Activation holdout은 모든 required branch durable intent와 trusted evaluator만 발행한다 | proposed profile -> admitted run/all branch dispatches -> terminal `valid/invalid/incomplete/denied` artifact | Holdout operator의 `activation_holdout_evaluate`, 등록 evaluator workload와 recovery dispatcher/finalizer | Run/receipt/audit와 required branch별 pending intent를 원자 commit. Branch별 최초·복구 attempt 전에 권한·등록·lifecycle 재검증, 모든 intent terminal 뒤 artifact 원자 publication | Exact profile/source/required branch set + dataset/evaluator/metric/threshold + branch dispatch/attempt identity | 전체 intent commit 및 attempt별 ADR-0069 admission 뒤에만 I/O | 어느 branch crash도 기존 pending intent로 복구, non-terminal을 incomplete로 합성 금지, revoke는 denied/I/O 0회 | Raw input/output 비저장, branch manifest/admission/terminal publication exactly-once | `M365-F08A1`~`F08A10`; Activation·Benchmark |
| Canary start는 production scope를 열지 않는다 | proposed -> limited_canary + 새 generation/evidence schedule | 독립 platform operator, expected profile/family/domain-role/schedule revision과 domain generation | Coordinator가 guard/actor authorization fence/profile/generation/`canary_successor`/first route/current-next schedule/required-branch + reserved `not_required` + overall aggregates/source watermarks/receipt/audit를 원자 commit | Exact source manifest, valid holdout artifact와 bound holdout operator principal, rollback, Worker readiness, profile-bound generation과 immutable evidence schedule contract | 전체 commit 뒤 canary capability만 발급, commit 전 provider I/O 없음 | Existing production route 유지. First rollout은 canonical non-V2 route를 만들고 remediation은 prior current-valid non-V2 route와 빈 canary role을 요구한다. Evidence 초기화/unsafe route는 전체 zero-write | State/generation/role/route/evidence/receipt/audit exactly-once | `M365-F08B`~`F08C`, `F18E`~`F18H`, `F24D1`~`F24D1A`; Activation |
| Canary assignment와 production route는 process/profile revision에 독립적이다 | deployment -> stable bucket -> successor 또는 canonical production route | Server resolver, `rollout_family_create` assignment contract와 `production_route.owner_kind` | Immutable contract ref/range를 profile propose 때 고정하고 canary start가 first route를 원자 초기화, raw seed 입력 금지 | Environment/family/domain/organization/workflow/deployment + contract/seed + exact route owner/revision, profile revision·runtime random 제외 | Target만 successor V2, non-target은 profile/non-V2/blocked route를 해소. Assignment 중 provider I/O 없음 | 같은 contract는 retry/restart 동일 bucket, first-rollout route 부재를 successor/default로 보완 금지 | Unit/seed 비노출, safe contract/range/route kind만 투영 | `M365-F18`~`F18A6`, `F18H`; Activation |
| Canary evidence는 source event와 usage operation sample당 한 번만 처리하고 usage revision은 별도 보정한다 | authoritative event -> open aggregate 또는 sealed state-aware reconciliation; usage correction -> pending -> retracted/added projection | Evidence system의 `record_canary_evidence`, reconciliation system의 `reconcile_canary_late_evidence` / `reconcile_canary_usage_correction` | Identity를 schedule보다 먼저 dedupe. Usage kind는 operation-first binding. Open은 cohort+overall aggregate, sealed는 unconsumed/monitoring correction과 promotion-member append-only reconciliation row로 처리 | Source instance + event ID가 source identity. Usage는 generation+operation+metric/event sample unique key도 적용. Operation ID는 comparison digest, usage revision/contribution은 별도 projection. Disposition/window는 최초 server outcome | Exact digest replay는 저장된 outcome, correction은 sample count 보존 delta. Pending/identity mismatch/caller window 주장은 fail-closed | Direct kind는 open·sealed·schedule-lag 모두 block. Unconsumed sealed corrected hard-stop은 generation block, consumed promotion gate/hard-stop 실패는 immutable set/member를 유지한 append-only reconciliation으로 current rollback/historical block | `not_required`는 overall에 포함하되 branch gate 대체 금지 | `M365-C08P`~`C08T`, `F08D0L`~`F08D2B`, `F08D7`, `F10D`~`F10E`, `F26E`; Activation·Benchmark·Ledger |
| Aggregate hard-stop은 open/late/usage fold 최초 crossing에서 generation을 차단한다 | below threshold -> first crossing -> deny-only generation block | `record_canary_evidence`, `reconcile_canary_late_evidence`와 `reconcile_canary_usage_correction`의 server-owned threshold evaluator | Open cohort+overall aggregate 또는 sealed snapshot+late/usage-correction canonical lock -> exact generation safety lock. Event/receipt/revisions/breach/block/audit 원자 commit | Profile-bound hard-stop contract + base/correction revisions + sorted threshold digest | Block winner 뒤 새 provider_started/I/O 0회 | `promotion_only`와 crossing 없는 correction은 차단 금지, replay/blocked follow-up은 중복 금지 | `model_routing.canary_breach.detected` 하나만 failure로 기록, raw metric 비저장 | `M365-C08T`, `F08D1H`~`F08D1I`, `F10D`; Activation·Benchmark·Ledger |
| Canary source watermark는 source adapter만 전진한다 | target window의 observed source barrier -> monotonic watermark revision | 등록 source adapter의 `advance_canary_source_watermark` | Immutable window schedule -> target-window/source-instance watermark lock/CAS, terminal receipt coverage 재검증, watermark/receipt/audit 원자 commit | Window/source-instance binding + barrier/position + observed-through + source contract | Watermark mutation 중 provider I/O 없음 | Eventless scan proof 허용, regression/incomplete coverage/conflict/old-window request zero-write | Safe barrier digest와 actor/request receipt만 기록 | `M365-F08D0C`~`F08D0F`; Activation·Benchmark |
| Canary snapshot은 immutable event time으로 봉인하고 drain 뒤 generation을 닫는다 | pre-registered current -> sealed snapshot, pointer -> next, deterministic 새 tail 생성; drained generation -> final seal + closed | Evidence system의 actor/request-bound `seal_canary_evidence_window`, lifecycle system의 `close_canary_evidence_generation` | Rotation은 schedule/pointer -> current/next -> aggregates -> watermarks -> usage validity. Admission/finalizer/watermark/correction은 generation drain summary를 같은 transaction에서 갱신한다. Close는 profile/role -> schedule -> drain summary -> usage validity -> generation만 잠그고 개별 operation/projection을 역순 획득하지 않는다 | Cadence + window sequence/current-next-tail, aggregate/watermark digest, server-owned terminal attempt/source coverage/pending counts의 drain revision/digest, usage epoch와 snapshot hash | Summary missing/stale, pending correction이나 미종결 operation이면 seal/close와 새 provider I/O 0회 | Rotation은 deterministic tail을 만들고 close는 tail 없이 final seal한다. 늦은 billing correction은 schedule/capability를 재개하지 않고 summary revision과 immutable final 근거만 append-only 재평가한다 | Exact replay는 same snapshot/tail 또는 final close receipt, concurrent loser zero-write. Operation-first reconciler와 close lock cycle 및 closed 뒤 새 source event/window 0회 | `M365-C08Q`~`C08T`, `F08D0`~`F08D0P`, `F08D1H`~`F08D1I`; Activation·Benchmark·Ledger |
| Production promote는 연속 sealed evidence set과 current execution eligibility에 결합한다 | limited_canary generation -> production_active + production route, predecessor가 있을 때만 superseded admission state | profile author·holdout operator와 다른 독립 operator, actor/request + expected profile/family/domain-role·holdout artifact/principal, start/end sequence와 ordered member+usage correction digest/epoch | Guard -> profile/family/roles -> holdout/source/credential/Worker/model/rollback -> ordered member snapshots/correction rows -> usage/safety lock. Contiguous set/member rows, route 전환과 canary role 해제 원자 commit | Generation-bound immutable set/member ID/revision/hash, zero invalidation, promotion 시점 usage-correction revision, required branch/`not_required` base+late+usage effective gates. 승격 후 correction은 별도 append-only reconciliation identity | Pending 0/current epoch일 때만 promotion. Transaction 중 I/O 없음 | Member late correction과 append-only usage reconciliation은 immutable set/member를 유지하며 current owner rollback 또는 historical block. Missing/gap/cherry-pick/stale/pending은 zero-write | Promotion과 invalidation/breach branch audit exactly-once | `M365-C08Q`~`C08T`, `F08D`, `F08D0J`~`F08D1J`, `F08D7`, `F18B`~`F18B1`, `F18H`, `F24D2`; Activation·Benchmark |
| Expiry는 scheduler 지연에도 fail-closed다 | active/proposed -> expired | Provider-start DB-time gate + lifecycle system의 deterministic expire command | Every-attempt profile lifecycle/valid_until shared fence; expire command는 expected revision 아래 state/receipt/audit와 exact owner role/generation만 조건부 전이 | Profile ID/revision/valid_until + owner role/generation | Resolver와 Judge/primary/fallback start가 DB time 기준으로 V2 I/O 차단 | Pinned run은 exact non-V2 rollback 독립 admission 또는 `profile_expired`; command는 production route를 rollback/`blocked_no_safe_path`, family reservation 유지 | System actor와 bounded reason만 기록 | `M365-F11C`~`F11C1`, `F18I`~`F18I2`; Activation |
| Environment global kill 해제는 current 독립 approval·권한 fence와 직렬화한다 | enabled + active/current-authorized approval -> disabled + consumed approval, enable은 즉시 차단 | Platform operator kill command와 독립 recovery approver의 approval issue/revoke | Guard -> actor/approver platform authorization fences(canonical order) -> approval. Role mutation은 같은 auth fence revision을 변경하고 state·epoch·receipt/audit 원자 commit | Environment + kill epoch + enable actor + actor/approver auth revisions + approval ID/revision/state/valid_until | Enabled 동안 발급 0회, 승인 consume 뒤 current gate를 통과한 새 capability만 허용 | Approval/role revoke winner는 disable zero-write, disable winner 뒤 revoke는 다음 command부터 적용, expiry fail-closed | Approval issue/revoke와 kill transition exactly-once, raw incident/remediation 비저장 | `M365-F24B`~`F24B8`; Activation |
| Hard-stop breach는 evidence actor의 generation 차단으로 끝난다 | active generation -> deny-only block, profile/role 불변 | Evidence system의 direct blocking append/reconciliation 또는 non-blocking aggregate first-crossing evaluator | Open direct는 aggregate->generation, schedule-lag direct는 generation, sealed direct/crossing은 state fence->snapshot/correction->generation. Event/receipt/revisions/breach/block/audit 원자 commit | Exact profile-bound generation, source event identity와 hard-stop contract/revisions | Blocked generation의 다음 provider_started/I/O 0회 | Promotion-member reconciliation 외 lifecycle/route 변경은 별도 operator command | `model_routing.canary_breach.detected` 또는 invalidation branch의 단일 canonical failure audit | `M365-F08D0M`, `F08D1H`~`F08D1I`, `F10`~`F10E`, `F24C`~`F24D3`; Activation |
| Sealed snapshot late evidence와 operator emergency는 권한을 분리한다 | 미승격 snapshot invalidated(+direct block); promotion member current rollback/historical block; post-promotion monitoring correction | 최소 권한 reconciliation system의 `reconcile_canary_late_evidence` 또는 operator emergency/rollback | Environment -> profile -> role 공통 fence. Unconsumed는 snapshot(-> direct generation), member current는 rollback policy->set->member->generation, member historical은 set->member->generation, monitoring은 snapshot->correction aggregates->generation | Snapshot/source event digest + server-derived set/member/sequence/current-historical relation | Promotion·provider start·correction과 직렬화. Winner 뒤 affected generation I/O 0회 | Monitoring branch는 snapshot/profile/route 불변, member current만 rollback/blocked, historical은 successor 불변 | 한 event당 invalidation 또는 evidence/breach audit 하나 | `M365-F08D0L`~`F08D0M`, `F08D1`~`F08D1I`, `F10`, `F18J`~`F18J4`, `F24`, `F24A`; MBA-366·Activation |
| Workflow 선택은 activation mutation이 아니다 | absent/selected/opt_out -> 새 policy version | Workflow `deploy` actor, workflow request hash | Organization authorization fence -> workflow resource authorization fence -> nullable-first policy CAS, commit-time 권한 재검증과 receipt/audit 원자성 | Workflow policy version + strategy + immutable rollout family/domain binding + server-derived authorization fence revisions, exact profile은 run snapshot에서 해소 | Policy write 자체 provider I/O 없음 | Opt-out은 명시적 non-V2 version, 권한 회수 winner 뒤 zero-write | Same request exactly-once, permission mutation과 직렬화, promotion 뒤 policy write 없이 새 run만 successor | `M365-F19A`~`F19G`; Activation |

## 보호 리소스 기능 완결 상태

Capability Routing V2는 credential reference, platform 권한, durable policy/evidence와 background runtime을
함께 다루므로 보호 리소스 점검표 적용 대상이다. 이 ADR은 Target 계약이며 아래 구현 경계가 모두 완료되기
전에는 V2를 registry에 등록하거나 production 기본값으로 활성화하지 않는다. `후속 이슈`는 안전한 임시
상태와 실행 가능한 검증이 함께 있어야 하며, activation blocker는 단순 문서 완료로 해제할 수 없다.

| 경계 | 상태 | 현재 구현 증거 | Target 검증 | 안전한 임시 상태·후속 |
| --- | --- | --- | --- | --- |
| Strategy/profile/cache/evidence durable 저장과 secret 비저장 | 후속 이슈 | `apps/shared/db/models/model_routing_policy.py`, `apps/workflow_engine/services/model_routing_runtime_judge.py`; V2 profile·evidence schema는 미구현 | `M365-D01`~`D03`, `F08D0`~`F08D2B`: migration, unique/CAS, immutable identity, redaction과 upgrade/downgrade | V2 registry 미등록·비활성; MBA-367·368·372 |
| Platform actor, role grant/revoke와 관리 API/UI | 후속 이슈 | Current `apps/gateway/auth/permissions.py`에는 V2 platform actor 관리 경계가 없음 | `M365-F24B`~`F24B9`: 최초 fence unique 생성, 모든 privileged command의 actor authorization fence, 회수 경합과 resource hiding | Public 관리 API/UI 미노출; MBA-372 activation blocker |
| Workflow 선택 durable CAS와 deployment preflight | 후속 이슈 | `apps/gateway/api/v1/endpoints/workflow.py`의 Current V1 policy 경로만 있고 V2 family/domain binding은 미구현 | `M365-F19A`~`F19G`: deploy 권한, active organization, family/domain lifecycle과 CAS replay | Ambiguous artifact는 dynamic routing 금지; MBA-366·372 |
| Runtime/background current gate와 provider I/O 전 재검증 | 후속 이슈 | `apps/workflow_engine/workflow/nodes/llm/llm_node.py`, `apps/workflow_engine/services/model_router.py`는 Current V1이며 V2 profile/generation capability가 없음 | `M365-A07`~`A12`, `B04`~`B11`, `F25`: Judge·primary·fallback·retry의 current gate | Current-valid non-V2 또는 typed failure; MBA-366·369·372 |
| Canary/production lifecycle, revoke와 TOCTOU | 후속 이슈 | V2 `RoutingActivationCoordinator`, role/generation fence와 evidence close command 미구현 | `M365-F08B`~`F08D2B`, `F18`~`F18J4`, `F24`~`F24D3`: lifecycle·revoke·drain/close 경합 | V2 capability 발급 금지; MBA-372 activation blocker |
| Cache와 evidence identity, 멱등성·crash replay | 후속 이슈 | `apps/workflow_engine/services/model_routing_incremental_learning.py` 등 Current V1 cache/learner만 있고 V2 activation evidence ledger는 미구현 | `M365-C08`~`C08T`, `D02`~`D03`, `F26F`~`F26F2`: HMAC, identity/digest, immutable set과 crash recovery | V1/V2 cache·evidence 공유 금지; MBA-367·368·372 |
| Credential/provider candidate 사용 정책 | 후속 이슈 | Candidate별 purpose/credential scope와 organization data-egress fence의 Accepted 구현 계약 없음 | `M365-A07B`~`A07D`, `B03`~`B03F`, `F24E2`, `F25`: credential use 권한·lifecycle·purpose, organization egress와 revoke | Provider Candidate Credential Policy·organization egress fence 이슈 연결 전 활성화 금지; MBA-372 activation blocker |
| Audit exactly-once와 secret·PII redaction | 후속 이슈 | Canonical 27-action allowlist는 본 ADR/API에만 정의되고 V2 recorder는 미구현 | `M365-E01`~`E10`, `F08D0N`~`F08D0P`, `F24B`: transaction rollback, replay exactly-once와 redaction | V2 management/runtime audit를 구현하기 전 activation 금지; MBA-372 |
| Query-embedding 목적의 V2 적용 | 후속 이슈 | Query embedding target runtime 활성화 전 | `M365-A05A`~`A05B`, `A07D`, `F25`: purpose/policy/capability와 mixed-version rollout | MBA-351 계약 후 MBA-320에서 활성화 |

## MBA-340 계획·구현 Gap 분류

이 표의 분류는 다음 의미다.

- `계획과 다르게 구현`: MBA-340 또는 Accepted 계약과 다른 의미로 현재 코드에 도달했다.
- `계획됨·미완결`: 방향은 있었지만 실행 경계, version 또는 테스트가 완결되지 않았다.
- `계획 누락`: MBA-340에 production 안전을 위해 필요한 계약이 충분히 없었다.

| 영역 | 분류 | Current gap | 이 결정의 Target | 검증·소유 |
| --- | --- | --- | --- | --- |
| V1/V2 격리 | 계획과 다르게 구현 | V2 의미가 V1 strategy ID 아래 실행되고 기존 row에 contract version이 없다 | 명시적 ADR-0059 V1, 독립 V2, ambiguous row safe-path 전환 | `M365-D01`, `M365-D01A`, `M365-D01B`; Strategy Registry/Cutover 후속 |
| Judge 책임·축 | 계획됨·미완결 | 3축 요구 판정은 구현됐지만 strategy·rubric 계보가 섞였고 고정 `OUTPUT_CONTRACT`도 Current Judge 입력에 포함된다 | bounded 3축 Judge, 모델 선택 금지, output/schema는 server structural hard gate, rubric 회전 | Current `FR-011-P13`, Target `M365-A01`, `M365-A02`, `M365-A11`; MBA-366·368 |
| Secondary Judge | 계획 누락 | 호출·병합·비용 상한의 canonical contract가 없다 | 동일 schema, 최대 1회, budget과 adjudication 기록 | MBA-366 또는 별도 후속 |
| Task intent | 계획과 다르게 구현 | 누락값이 `generate`로 승격된다 | 누락=`unspecified`, server-derived 하한 우선 | `M365-A03`~`M365-A06`; MBA-366 |
| Server selector | 계획됨·미완결 | 수동 score와 단순 가격 비교 의존도가 높다 | hard gate 뒤 전체 비용·지연·품질과 deterministic tie-break | `M365-A08`, `M365-A09`, `M365-B01`~`M365-B03`; MBA-366·Ledger 후속 |
| 미검증 후보 | 계획과 다르게 구현 | 일반 운영 경로에서 선택될 수 있다 | low-risk bounded canary 외 proven safe baseline | `M365-C04`~`M365-C07`; MBA-366·Model Profile 후속 |
| Model evidence | 계획됨·미완결 | source/version/freshness, task semantic/model scope, eligible purpose/mode와 duplicate terminal finalizer 수렴이 불완전하다 | 재사용 scope가 같은 versioned profile과 eligible ADR-0069 operation-bound exactly-once sample append | `M365-C01`~`M365-C03`, `M365-C08`~`M365-C08I`; Model Profile·Ledger 후속 |
| Safe fallback | 계획됨·미완결 | pre-I/O reselection, 저장 기본·대체와 current gate가 분산돼 있다 | 원래 후보 내 1회 reselection과 definitive failure 뒤 configured fallback 재검증 | `M365-B01B`, `M365-B06`, `M365-B07`, `M365-B10`, `M365-B10A`; MBA-366 |
| Subject 없는 data scope | 계획 누락 | public·schedule·system actor와 데이터 접근 주체의 관계가 V2에 명시되지 않았다 | ADR-0018 anonymous public-only, synthetic owner/user/credential subject 금지 | `M365-A07A`~`M365-A07A4`; MBA-366·Knowledge 연동 |
| Credential principal·provider purpose policy | 계획 누락 | execution subject와 credential principal이 섞이고 ADR-0064의 단일 exact-model policy로 다중 후보 권한을 표현할 수 없으며 RAG query embedding도 별도 purpose가 필요하다 | server-derived principal, Judge·candidate별 model-bound policy, ADR-0071 query-embedding policy 분리, 미구현 시 V2 비활성 | `M365-A07`, `M365-A07A`, `M365-A07B`, `M365-A07C`; Provider Candidate Credential Policy 후속·MBA-351·320 |
| Requirement/selection contract | 계획됨·미완결 | learner와 cache의 소비 의미가 하나의 계보처럼 섞이고 고정 작업 의미 변경 뒤 learner 재사용 위험이 있다 | task semantic fingerprint가 포함된 requirement digest와 selection digest 분리, top-level provenance | `M365-D02`, `M365-D04`, `M365-D04B`, `M365-D04C`; MBA-367·368 |
| Accepted decision cache | 계획됨·미완결 | task/output/effect/strategy/profile·tenant/node binding이 부족하다 | selection contract, scoped namespace와 cache key epoch binding, current gate 재검증 | `M365-D02`, `M365-D02A`, `M365-D02B`, `M365-D03`, `M365-E07`; MBA-367 |
| Learner gate | 계획과 다르게 구현 | 구현은 100/50/75%, Accepted 계약은 50/20/80%이고 새 candidate가 active policy에 자동 연결될 수 있다 | 50/20/80은 candidate gate일 뿐이며 activation profile이 exact Judge-only 또는 approved learner version을 고정 | `M365-D04`~`M365-D06`, `M365-F08A`, `M365-F08E`~`M365-F08H`; MBA-368·Activation 후속 |
| Rejected label | 계획과 다르게 구현 | batch가 rejected label을 classifier에 반영한다 | 평가 분모에만 포함하고 training count/weight 제외 | `M365-D07`; MBA-368 |
| Nested identity | 계획됨·미완결 | terminal node ID 중심 경로가 남아 있다 | canonical location과 invocation/iteration identity 분리 | `M365-D09`, `M365-D10`; MBA-369 |
| 관측 source | 계획과 다르게 구현 | `decision_source`가 strategy 해석·요구 판정·선택·재사용을 혼합한다 | requested/effective strategy와 resolution reason 뒤 effective V2의 세 source enum 분리 | `M365-E01`~`M365-E11`; MBA-366·367 |
| 경제성 admission·전체 비용 | 계획 누락 | Judge/retry/fallback 비용보다 절감이 작은 호출도 가능하다 | pre-Judge admission과 모든 attempt 비용 | `M365-B01`~`M365-B05`; MBA-366·Ledger 후속 |
| Budget·usage 귀속 | 계획 누락 | Judge·primary·fallback의 budget admission과 Billing Principal 귀속이 selector 계약에 없다 | attempt별 capability/budget admission과 ADR-0069 종결 | `M365-B11`; MBA-366·ADR-0064/0069 연동 |
| 저빈도 workflow | 계획 누락 | local 전환 전 Judge 비용이 장기 지속될 수 있다 | fixed safe path와 경제성 기반 bounded routing | `M365-D08`; MBA-366·368 |
| Cache·learner 동시성 | 계획됨·미완결 | key rotation, concurrent fill·promotion·finalizer 수렴 계약이 불완전하다 | key epoch 회전, CAS/unique identity와 exactly-once count | `M365-D02A`, `M365-D04A`, `M365-D10A`; MBA-367·368·369 |
| 실험 재현성·raw artifact | 계획 누락 | SHA/dataset/contract version, validity와 retention이 불충분하다 | locked holdout, complete manifest, redacted Git report | `M365-E05`, `M365-F01`~`M365-F07`; Benchmark 후속 |
| Activation actor·profile | 계획 누락 | 일반 deploy 권한과 platform rollout 경계가 없고 서로 다른 family의 domain 경합이 직렬화되지 않는다 | exact requirement source가 있는 사전 등록 profile, 독립 승인, stable canary, environment coordinator와 actor 분리 | `M365-F08`~`M365-F18G`, `M365-F21`~`M365-F23`; Activation 후속 |
| 실행 snapshot·긴급 revoke | 계획 누락 | 실행 중 policy 변화와 lifecycle 우선순위가 불명확하다 | strategy/profile/policy pin, revoke/emergency 우선 | `M365-B10`, `M365-F19`, `M365-F20`, `M365-F24`; MBA-366·Activation 후속 |
| Preview·benchmark·mixed worker | 계획 누락 | Side-effect 없는 preview와 billable diagnostic, 구형 Worker의 V2 해석 경계가 불명확하다 | mode 분리, requirement pending, 실제 소비 Worker의 code-owned runtime capability readiness | `M365-A10`, `M365-F25`, `M365-F25A`, `M365-F26`; MBA-366·Benchmark·Activation 후속 |

## Current와 Target

### Current

- 실행 가능한 strategy ID는 `judge_bootstrap_incremental_v1` 하나다.
- Requirement Judge는 세 축 요구 능력만 반환하고 서버가 후보를 선택하므로 ADR-0059의 Judge 직접
  선택 설명과 다르다.
- 누락 task intent는 `generate`로 처리된다.
- learner gate 구현값은 `100 labels / recent 50 / exact 75%`로 ADR-0059와 다르다.
- accepted decision cache는 current output/effect/strategy contract를 완전히 binding하지 않는다.
- Client의 현재 Judge 상세는 구형 candidate-selection 문구와 후보 수 projection을 표시한다.
- routing activation profile, platform operator와 V2 execution snapshot은 구현되지 않았다.
- V1 row에 immutable legacy contract digest가 없으므로 V2 rollback 적격성을 증명할 수 없다.

### Target

- `capability_routing_v2`를 V1과 분리하고 기본 비활성으로 등록한다.
- Requirement Judge와 learner는 requirement contract를, server selector와 cache는 selection contract를
  공유하며 top-level routing contract가 실행 provenance를 결합한다.
- 아래 활성화 게이트를 모두 통과하기 전 production V2 activation은 허용하지 않는다.

## 활성화 게이트

1. 이 결정과 PRD, architecture, requirements, API, component와 test cases가 같은 책임을 설명한다.
2. V2 strategy가 V1 policy/cache/learner와 격리되고 기본 비활성이다.
3. 기존 V1 row는 legacy contract로 식별되고, 재검증·재발행하지 않은 row를 V2 rollback target으로
   사용하지 않는다.
4. MBA-366의 task intent, hard gate, selector, fallback과 source 테스트가 통과한다.
5. MBA-367의 cache contract digest와 current revalidation 테스트가 통과한다.
6. MBA-368의 learner rotation, threshold와 rejected label 테스트가 통과한다.
7. MBA-369의 canonical node location과 invocation identity 테스트가 통과한다.
8. Model profile provenance/freshness와 attempt/cost ledger가 구현돼 versioned evidence를 제공한다.
9. Strategy registry, idempotent rollout family/assignment contract 생성, activation profile 승인·scope,
   stable canary, 명시적 evidence snapshot seal, execution snapshot과 audit가 구현된다.
10. Locked holdout에서 사전 등록 profile의 품질, 전체 비용, p95 지연, high-risk 과소판정과 fallback
   기준을 사전 등록한 표본·confidence 조건과 함께 만족한다.
11. Diagnostic mode 뒤 limited canary와 V2-off rollback을 검증한다.
12. Manifest가 완전하고 run status가 `valid`이며 tuning/holdout 누수가 없다.
13. Activation·selection·rollback audit가 이 ADR과 API의 exact action/status allowlist로 exactly-once 기록되고 secret·PII redaction을 검증한다.
14. Judge·primary·fallback의 capability-bound budget admission, cache/learner 동시 수렴과
    mixed-worker compatibility gate를 검증한다.
15. Activation profile이 exact Judge-only 또는 approved learner version을 고정하고 candidate learner
    발행·교체가 profile을 자동 변경하지 않음을 검증한다.
16. Tenant operational evidence가 organization·workflow·canonical node location, task semantic
    fingerprint/cohort/model/evidence contract와 current ADR-0069 usage revision이 같은 실행에만 재사용됨을
    검증한다. 동일 operation의 순차·동시 finalizer는 표본·receipt 하나로 수렴하고 authoritative usage
    correction은 validity epoch/pending intent를 원자 생성한다. Selector가 사용한 exact aggregate validity row
    set/revision은 `model_evidence_fence_set_digest`로 capability와 provider-start fence에 결합되어 correction
    winner 뒤 stale start/I/O가 0회다. Rebuild는 sample count를 늘리지 않고 current metric aggregate 하나로
    수렴한다.
17. 서로 다른 rollout family의 overlapping activation command는 environment coordinator가 직렬화해
    winner 하나만 domain reservation/role state/receipt/audit를 commit하고, non-overlap command는 같은 guard revision을
    읽었더라도 domain-local CAS로 둘 다 순차 성공함을 검증한다.
18. Subject 없는 public·webhook·schedule·API·system 실행이 ADR-0018 anonymous public-only로 제한되고
    owner·actor·credential principal을 private data subject로 합성하지 않음을 검증한다.
19. ADR-0064를 확장하는 Accepted credential policy가 Judge와 각 primary/fallback exact model을
    server-derived credential principal에 binding하고 public·schedule·system actor 승격을 금지한다.
20. RAG 사용 scope는 ADR-0071 query-embedding policy/capability와 MBA-320 activation을 통과하며,
    V2 Judge/main-generation policy가 query embedding 권한을 대신하지 않는다.
21. Canary evidence의 unique source identity가 immutable source instance와 그 namespace의 event ID를
    포함하고 canary window·payload digest·metric contract·event kind는 포함하지 않음을 검증한다. Actual
    evidence cohort/nullable requirement-source branch-version은 source-event comparison digest다. Schedule
    disposition/nullable window는 최초 server-owned processing outcome이며 caller 입력이나 replay 재계산 대상이
    아니다. 같은 instance event의 digest 변경이나 caller의 window 주장은 conflict/zero-write다. 서로 다른
    registered instance의 같은 event ID는 non-usage 또는 서로 다른 usage sample key일 때만 별도 event이며,
    같은 generation/operation/metric-contract/event-kind usage observation은 source와 무관하게 한 표본이다.
22. Direct blocking event는 trusted source/time 검증 뒤 open assigned이면 aggregate -> generation,
    materialized window가 없으면 unassigned event/receipt -> generation, sealed이면 state-aware reconciliation
    순서로 threshold와 무관하게 exact affected generation deny-only block/epoch와 canonical failure audit를
    원자 기록한다. Assigned non-blocking event와 post-promotion monitoring late correction은 cohort/overall
    hard-stop을 처음 넘을 때 deterministic breach, exact generation block과 failure audit를 한 번만 기록하며
    concurrent crossing/replay는 하나로 수렴한다. Promotion-only와 crossing 없는 correction은 block을 만들지
    않는다. Invalid actor/source는 block zero-write다. 미승격 sealed branch는 snapshot invalidation을 기록하고
    direct kind면 generation도 차단한다. Promotion-set member의 current owner는 rollback policy -> set ->
    member -> generation 순서로 block+rollback/disabled를, historical owner는 exact historical generation
    block-only를 원자 수행한다. Set end 뒤 monitoring snapshot은 base+correction fold만 갱신하고
    profile/route/current successor를 보존한다. Production breach 뒤 remediation은 operator가 기존 owner를
    닫고 canonical non-V2 route로 전환한 뒤 새 generation을 연다. Expiry는 자기 role/generation 밖에
    영향을 주지 않는다.
23. `rollout_family_create`가 family/initial contract를, `rollout_assignment_contract_issue`가 existing
    family의 추가 immutable contract를 CSPRNG seed, assignment-registry CAS, receipt/audit와 원자 생성함을
    검증한다. Exact replay는 기존 결과, stale/concurrent/conflict는 zero-write이고 기존 profile/contract는
    불변이며 non-overlap family는 각각 생성할 수 있다.
24. Canary start가 profile/generation/role/first-rollout canonical route와 immutable cadence/current-next
    schedule, required-branch + reserved `not_required` + overall aggregates, window별 source-instance watermark,
    generation canary usage validity epoch/pending count와 server-owned drain summary revision 0, receipt/audit를 한
    transaction에 생성하고 초기화 실패·concurrent loser가 전체 zero-write임을 검증한다. 각 seal은 pending
    count 0을 확인하고 current snapshot과 required-branch·`not_required`·overall late/usage-correction revision
    0, deterministic tail/동일 aggregate key set/watermarks, link/pointer/schedule revision을 exactly-once로
    원자 생성한다. Tail/correction 초기화 실패·경합은 전체 rollback한다. Rotation은 production/current 및
    superseded pinned generation terminal 전까지 계속하지만 promotion set end 뒤 snapshot은 기존 set에
    소급 편입하지 않는다. Admission/finalizer/source watermark/usage correction이 terminal/pending
    count·watermark·coverage digest와 drain revision을 같은 transaction에서 갱신한다. Generation close는
    server-owned summary의 count 0과 final-cutoff coverage를 profile/role -> schedule -> summary -> usage
    validity -> generation 순서로 검증하고 개별 operation/projection lock을 역순으로 획득하지 않은 채 final
    window를 tail 없이 봉인해 schedule/generation을 closed로 원자 전이한다. Drain 전·summary stale·close
    경합은 zero-write/retry이고 operation-first correction/finalizer와 deadlock 0회이며 close 뒤 새 activation
    source event/window는 0회다. 이후 도착한 authoritative billing correction은 닫힌 schedule을 재개하지
    않는 append-only projection으로 immutable final 근거를 재평가하고 필요한 historical block/current-owner
    rollback만 적용한다. Non-blocking out-of-schedule event는 rotation 뒤 재제출한다. Promotion은 start
    sequence 0부터 end까지 모든 contiguous snapshot, required branch gate와 `not_required` overall gate를
    immutable evidence set에 묶고 gap/cherry-pick/member stale을 zero-write한다.
25. Profile `valid_until`, runtime safety revision, organization authorization, exact organization/provider/
    purpose data-egress policy, decision에 사용한 exact requirement source lifecycle, credential
    policy/credential, model/provider, rollback policy, capability-bound operational evidence validity rows와
    canary usage validity epoch가 provider-start와 같은 safety fence를 사용해 DB-time expiry, source/policy
    revoke 또는 correction winner 뒤 `provider_started`와 provider I/O가 0회임을 검증한다. 정상 promotion의 predecessor
    `superseded` admission revision만 바뀐 경우에는 promotion 전에 admit된 run의 다음 attempt가 계속됨을 검증한다.
26. `activation_holdout_evaluate`가 exact profile/source/dataset/evaluator 계약의 모든 required branch
    pending dispatch intent를 run/receipt/audit와 한 transaction에 exactly-once 생성함을 검증한다. 어느
    branch 완료 직후 crash해도 admission-time 나머지 intent를 lease/fencing recovery가 찾고, non-terminal
    branch를 incomplete로 종결하지 않는다. 최초·복구 attempt마다 operator 권한, evaluator
    등록/binding/lifecycle을 재검증해 revoke winner 뒤 `denied`/I/O 0회로 닫는다.
    `provider_started`/outcome-unknown 재호출, tuning/product benchmark 대체, conflicting replay와
    finalizer 경합은 금지하거나 단일 artifact로 수렴한다.
27. 등록 source adapter의 `advance_canary_source_watermark`만 eventful/eventless barrier를 monotonic하게
    전진시키고 stale·regression·불완전 receipt coverage와 seal 경합을 zero-write로 처리함을 검증한다.
    Usage-derived event는 source identity 외 generation/operation/metric-contract/event-kind sample unique
    key로 cross-instance·new-event-ID 중복 관측을 거부해 같은 provider operation이 canary 분모에 한 번만
    기여해야 한다.
28. Production promotion이 commit 직전 promotion actor가 profile author와 artifact-bound holdout operator
    모두와 다른지, profile expiry/state, role owner, source/holdout artifact principal, credential, 실제
    Worker readiness, global model eligibility, rollback, safety, ordered member usage-correction revision과
    generation canary usage pending/epoch를 모두 재검증해 동일 principal, 선행 revoke/correction 뒤
    cutover가 0회임을 검증한다. Sealed snapshot의 pre-cutoff late evidence가 미승격 상태에서 먼저 도착하면 non-zero
    invalidation으로 promotion을 영구 거부하고 direct kind이면 generation도 차단한다. Promotion-set member의
    late evidence는 current owner generation을 block하고 rollback policy를 snapshot보다 먼저 잠가
    current-valid route 또는 `blocked_no_safe_path`로 전이하며, superseded member는 historical generation만
    차단한다. Set end 뒤 post-promotion monitoring snapshot의 late evidence는 snapshot별 correction에
    exactly-once 반영하고 direct event 또는 hard-stop first crossing에서 exact current/historical generation만
    차단하며 immutable snapshot/profile/route/current successor는 변경하지 않는다. ADR-0069 usage
    correction은 operation-bound canary projection을 sample count 보존 delta로 재구축하고 pending 동안
    seal·promotion·새 provider start를 차단한다. 미승격 sealed member는 base+late+usage effective gate와
    hard-stop first crossing generation block을,
    이미 소비된 promotion member는 immutable set/member를 바꾸지 않는 append-only
    `promotion_evidence_reconciliation`으로 current owner rollback/blocked route 또는 historical generation
    block을 적용하고 monitoring correction은 hard-stop first crossing만 exact generation에 반영한다.
29. Emergency disable이 roleless `proposed` profile에는 state/receipt/audit만 변경하고, canary/production
    owner의 emergency disable/rollback과 active expiry는 exact role만 전이하며 production
    rollback 부적격 시 `blocked_no_safe_path`로 닫으며 family activation-domain reservation은 유지함을
    검증한다.
30. Global kill recovery approval issue가 environment guard, approver platform authorization fence와
    deterministic command/approval identity 아래 exact replay 또는 unique insert로 수렴하고, absent target
    fence의 최초 grant는 unique revision 0 insert와 grant/revision 1을 한 transaction에 commit하며 concurrent
    first grant/privileged command가 revision 경계를 우회하지 않음을 검증한다. Revoke/disable과 role mutation은
    guard -> canonical actor/approver authorization fences -> exact approval 순서로 직렬화됨을 검증한다. Approval 또는 approver-role revoke winner 뒤 disable은 zero-write이고 disable winner는 approval
    consume·kill epoch/guard revision·receipt/audit를 원자 commit한다. DB-time expired, revoked/같은 actor
    approval, remediation digest mismatch와 stale approval/authorization revision은 capability를 재발급하지 못한다.
31. Workflow strategy mutation은 server-derived organization/workflow authorization fence revisions를
    canonical request와 lock order에 결합하고 membership·team/resource grant 회수와 직렬화한다. Organization
    data-egress policy revision도 ProviderExecutionCapability와 provider-start fence에 결합해 policy revoke
    winner 뒤 payload 외부 전송이 0회임을 검증한다.
32. Billable benchmark admission과 exact Judge/candidate provider start가 current platform benchmark actor,
    organization과 target workflow/deployment `execute` authorization fence revisions를 command/run 및
    attempt capability에 결합하고 permission mutation과 같은 lock을 사용함을 검증한다. Revoke winner 뒤
    run/receipt 또는 provider I/O는 0회이고 start winner exact attempt만 완료하며 이후 attempt는 새
    authorization snapshot을 요구한다.

하나라도 충족하지 못하면 V2를 기본 활성화하지 않는다. Learner candidate가 50/20/80 기준을
통과했다는 사실만으로 production activation을 승인하지 않는다.

## 구현 소유권

- MBA-340: 별도 V2 strategy, 실험 harness와 fixed baseline 비교
- MBA-366: task intent, server hard gate/selector, safe fallback과 canonical source
- MBA-367: accepted decision cache contract digest와 current state 재검증
- MBA-368: learner contract rotation, threshold와 rejected label 경계
- MBA-369: canonical nested location과 invocation label identity
- MBA-372: 하위 변경 통합, 활성화 적격성 검증과 최종 `dev` 병합
- MBA-351: RAG query-embedding purpose/policy/capability 경계
- MBA-320: query-embedding target runtime과 mixed-version rollout 활성화
- 후속 이슈: Provider Candidate Credential Policy, model profile evidence registry, routing attempt/cost
  ledger, benchmark governance, V1 compatibility contract tagging/cutover와 strategy activation governance

## 결과

### 장점

- LLM의 요구 분석과 서버의 권한·capability·비용 판정을 분리한다.
- V1/V2 의미와 cache·learner 계보가 섞이지 않는다.
- 최적화 비용이 절감액보다 커지는 요청과 미검증 high-risk 모델 사용을 차단한다.
- 코드 릴리스, 플랫폼 승인과 tenant workflow 선택이 서로의 권한을 대체하지 않는다.
- 실행 의미를 version으로 보존하면서 revoke와 emergency disable은 즉시 적용한다.

### 비용과 복잡성

- Strategy registry, profile evidence, manifest, activation profile과 운영 승인 경계가 필요하다.
- 기존 V1 policy, cache와 learner를 V2로 자동 재사용할 수 없다.
- Source/version 필드, attempt ledger와 rollout 테스트가 늘어난다.
- Platform routing operator 구현 전에는 production 활성화를 release artifact로만 관리해야 한다.

## 검토한 대안

### Judge가 후보 중 모델 ID까지 직접 선택

Credential, provider availability, 가격과 private 운영 상태를 Judge에 노출하거나 응답을 다시
광범위하게 검증해야 하므로 채택하지 않는다.

### 기존 V1 strategy ID의 의미만 변경

기존 policy, cache, learner와 trace를 같은 계약으로 오인하게 하므로 채택하지 않는다.

### 모든 기준을 하나의 가중 score로 계산

가격이나 prior가 권한·기능·안전 hard gate를 상쇄할 수 있으므로 채택하지 않는다.

### 최근 실험값에 맞춰 learner 기준을 `100 / 50 / 75%`로 변경

Locked holdout 근거가 없고 기존 품질 기준을 조용히 낮추므로 채택하지 않는다.

### 개발자 배포 또는 learner gate 통과로 production을 자동 활성화

독립 승인, canary, tenant opt-out과 rollback 준비를 우회하므로 채택하지 않는다.

### 일반 organization manager가 전역 activation profile을 변경

Tenant 관리 권한이 플랫폼 전체 rollout 권한으로 상승하므로 채택하지 않는다.
