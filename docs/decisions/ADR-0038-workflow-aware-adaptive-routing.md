# ADR-0038: Workflow-Aware Adaptive Routing

Status: Superseded

Superseded By: [ADR-0059](ADR-0059-judge-bootstrap-incremental-routing.md)

Related ADRs: [ADR-0022](ADR-0022-incremental-hexagonal-architecture-adoption.md)

## Context

현재 자동 모델 라우팅에는 다음 기반이 이미 있다.

- 배포별 LLM node policy 저장소
- active policy rule을 평가하는 runtime evaluator
- 현재 실행 주체가 사용할 수 없는 credential/model을 제외하는 Hard Gate
- 운영 run 중복 집계 방지와 비동기 policy refresh
- 선택 모델, fallback, rule, policy version을 남기는 safe trace

하지만 현재 bootstrap policy는 저장 모델을 그대로 유지하고 rule을 만들지 않는다.
운영 profile은 Cost Optimizer candidate 실행을 제외하며, 새 모델을 active policy로
승격하려면 해당 모델의 품질 표본이 필요하다. 따라서 사용자가 Replay 비교를 통해
새 모델을 검증해도 그 증거가 policy refresh에 연결되지 않고, 운영 runtime도 새
모델을 탐색하지 않으므로 후보 증거를 얻을 수 없는 순환 문제가 생긴다.

이 상태에서 Judge LLM에 모델별 전체 평균만 전달해 rule set을 만들게 하면 Judge가
관측하지 않은 입력 유형별 성능을 추정하게 된다. 또한 고정된 운영 run 수만으로
refresh를 실행해도 새 증거가 없다면 정책은 달라질 이유가 없다.

## Decision

Nodease의 모델 라우팅은 **Workflow-Aware Adaptive Routing**을 사용한다.

Workflow-Aware는 모델을 입력 문장 하나만 보고 고르는 것이 아니라, target LLM
node의 출력 계약, RAG 사용 여부, downstream 의존성, 과거 입력군 비중과 검증된
후보 성능을 함께 본다는 뜻이다. Adaptive는 매 요청마다 Judge를 호출한다는 뜻이
아니라, 새로운 검증 증거가 쌓였을 때 저장 policy를 갱신한다는 뜻이다.

전체 흐름은 다음 순서를 따른다.

```text
버전이 지정된 Semantic Route catalog
  -> 대표 문장 embedding 사전 계산

운영 로그 + Cost Optimizer Replay 후보
  -> Evidence Adapter
  -> Hard Gate
  -> Routing Eligibility Analyzer
  -> 품질/효율 Gate
  -> Deterministic Policy Optimizer
  -> Policy Proposal 또는 Fixed Model 권고
  -> Active Policy
  -> Runtime Semantic Cohort Matcher + Rule Evaluator
  -> Decision Trace
  -> 다음 Evidence Loop
```

### Evidence sources

증거는 출처를 섞지 않고 다음 source로 구분한다.

| Source | 의미 | 현재 단계 사용 |
| --- | --- | --- |
| `operational` | 배포 후 실제 운영 run | 사용 |
| `replay` | 같은 baseline input으로 실행한 Cost Optimizer candidate | 사용 |
| `shadow` | 운영 응답에는 영향 없이 후보를 병렬 실행한 결과 | 후속 |
| `canary` | 제한된 운영 traffic에 후보를 적용한 결과 | 후속 |

`operational`과 `replay`는 같은 숫자로 합산하지 않는다. Replay는 후보가 품질
계약을 통과할 가능성을 검증하는 증거이고, 운영 로그는 실제 traffic 비중과 현재
정책 성능을 계산하는 증거다. Policy proposal은 두 source를 함께 참고할 수 있지만
각 metric에는 source와 sample count가 남아야 한다.

초기 구현은 새 evidence table을 추가하지 않는다. 다음 기존 원천을 adapter가
공통 `RoutingEvidenceSummary`로 정규화한다.

- 운영 증거: `workflow_runs`, `workflow_node_runs`, `llm_usage_logs`
- Replay 증거: `cost_optimizer_experiments`, `cost_optimizer_candidates`
- Policy와 갱신 이력: `llm_node_model_routing_policies`,
  `llm_node_model_routing_policy_updates`

Shadow/Canary를 도입하거나 evidence query 비용이 커지면 별도 normalized evidence
table을 추가하는 ADR을 작성한다.

### Hard Gate

Hard Gate를 통과하지 못한 모델은 점수 계산 전에 후보에서 제거한다.

- 현재 organization과 execution subject가 credential `use` 권한을 가진다.
- credential-model relation이 verified이고 model이 active chat model이다.
- node의 context/output limit, JSON/schema, tool/file/image 기능과 호환된다.
- RAG와 provider 기능을 포함한 node 실행 요구사항을 만족한다.
- organization/provider 정책에서 금지되지 않는다.
- 삭제, 비활성, 만료 또는 scope 밖 resource를 참조하지 않는다.

권한 또는 capability를 통과하지 못한 후보를 fallback으로도 저장하지 않는다.

### Routing eligibility

모든 LLM node에 adaptive routing을 강제하지 않는다. Eligibility Analyzer는 다음
결과 중 하나를 반환한다.

| 결과 | 의미 |
| --- | --- |
| `eligible` | 둘 이상의 실행 가능한 후보와 검증 가능한 품질 계약이 있다. |
| `needs_evidence` | 후보는 있지만 Replay/운영 표본이 부족하다. |
| `fixed_model_recommended` | 모델을 나눠 쓸 예상 이익이 작거나 품질 검증이 불가능하다. |
| `blocked` | 권한, credential, capability 또는 데이터 계약 문제로 분석할 수 없다. |

Eligibility 판단은 최소한 다음을 본다.

- 실행 가능한 후보가 둘 이상인지
- schema/downstream/quality score 중 품질을 비교할 수 있는 기준이 있는지
- 운영 입력군을 일반 feature cohort로 구분할 수 있는지
- 후보의 예상 절감액이 Replay/Judge/fallback 비용을 포함해 양수인지
- 후보 검증 표본이 gate profile의 최소 표본 수를 만족하는지

Eligibility가 `fixed_model_recommended`이면 억지로 rule을 만들지 않고 현재 모델을
유지한다.

### Workflow-aware cohorts

Workflow-aware cohort는 두 종류의 신호를 함께 사용한다.

1. **결정론적 workflow 신호**: node 설정과 입력 shape에서 바로 계산할 수 있는 값
2. **semantic cohort**: 입력 문장의 의미가 사전에 정의된 Route 중 어디에 가까운지
   embedding 유사도로 판정한 값

결정론적 workflow 신호는 runtime과 optimizer가 공통으로 이해하는 다음 값만
사용한다.

- `output_format`
- `schema_required`
- `knowledge_enabled`
- `has_file_input`
- `input_length_bucket`
- `prompt_length_bucket`
- 명시적으로 저장된 `customer_facing`
- 명시적으로 저장된 `node_task`

런타임 코드에는 고객지원, SLA, 법무 같은 제품 도메인 키워드를 하드코딩하지
않는다. 의미 판정이 필요한 경우 코드의 `keyword_any`가 아니라 versioned Semantic
Route catalog를 사용한다.

### Semantic Route catalog와 runtime matcher

Semantic routing은 Aurelio Semantic Router의 정적 Route 방식을 참고한다. 자동으로
K-means 군집을 만드는 것이 아니라, policy가 활성화되기 전에 Route와 각 Route를
설명하는 대표 문장을 준비한다. 첫 구현의 대표 문장은 운영 raw input을 복사하지
않고 node에 명시적으로 등록한 문장 또는 검토된 synthetic 문장만 사용한다.

각 catalog는 최소한 다음 정보를 가진다.

- `route_catalog_version`
- embedding provider/model/version
- runtime input에서 의미 분류에 사용할 명시적인 `input_paths`
- `top_k`와 aggregation 방식
- 애매한 분류를 거부하기 위한 `min_margin`
- Route별 `cohort_id`, 사용자 친화 label, threshold
- 대표 문장의 hash와 사전 계산한 embedding vector 및 Route centroid vector

대표 문장 embedding과 Route centroid는 policy 생성 또는 활성화 시 한 번 계산한다.
일반 workflow 실행마다 Route 대표 문장을 다시 embedding하지 않는다. 실제 입력은
`input_paths`로 선택한 업무 본문만 runtime에서 한 번 embedding하고 벡터나 원문을
trace에 저장하지 않는다. 객체 key, customer tier, 실행 식별자처럼 의미 분류 대상이
아닌 주변 metadata를 임의로 합쳐 embedding하지 않는다.

Runtime matcher는 다음 순서를 따른다.

1. `input_paths`에서 추출한 실제 업무 본문을 catalog와 같은 encoder로 embedding한다.
2. 기본 `centroid` 방식에서는 query vector와 사전 계산 Route centroid의 cosine
   similarity를 계산한다.
3. 기존 policy의 `mean`, `max`, `sum` 방식은 호환 경로로 유지하고 이 경우 전체 대표
   문장 중 `top_k` 결과를 Route별로 집계한다.
4. 새 catalog는 대표 문장 하나의 우연한 고득점에 좌우되지 않도록 `centroid`를
   기본 aggregation으로 사용한다.
5. 최고 Route 점수가 해당 threshold 이상이고 2위와의 차이가 `min_margin` 이상일
   때만 semantic cohort를 확정한다.
6. threshold 또는 margin을 통과하지 못하면 `no_match` 또는 `ambiguous`로 닫고
   policy의 보수적인 default model을 사용한다.

Semantic matcher는 cohort만 판정한다. 어떤 모델을 사용할지는 semantic cohort와
결정론적 workflow 신호, 검증 evidence를 입력받은 active policy가 결정한다. Route에
연결되는 selected/fallback model은 해당 cohort에서 품질 gate를 통과한 모델만
허용한다.

단일 dense Route는 `일반 결제 문의`와 `결제 수단 탈취 사고`처럼 주제는 같고 위험도는
다른 입력을 서로 배타적인 한 군으로 오분류할 수 있다. 따라서 안전 Route는 catalog에
versioned lexical signal, weight와 threshold를 둘 수 있다. Runtime은 policy에 저장된
signal을 일반적인 문자열 matcher로 먼저 평가하고, 안전 threshold를 통과한 경우에만
dense Route보다 우선한다. 도메인 keyword는 runtime 코드에 두지 않으며 입력 원문과
signal 원문은 trace/API에 노출하지 않는다. 안전 override가 없을 때만 기존 centroid
Route 판정을 사용한다.

Embedding 호출 자체에도 비용과 latency가 있으므로 적합성 분석과 예상 순절감액은
`embedding_routing_overhead`를 포함한다. 예상 절감액이 이 비용보다 작으면
`fixed_model_recommended`로 닫는다.

### Candidate validation and quality gate

새 모델은 `CostOptimizerCandidate` row가 있다는 이유만으로 active policy에 들어가지
않는다. 다음 검증을 통과한 candidate만 `validated`로 취급한다.

- candidate 실행이 성공했다.
- JSON/schema가 필요한 경우 schema가 통과했다.
- downstream contract가 `compatible` 또는 승인 가능한 `warning`이다.
- 자유형 출력은 quality judge 결과와 confidence가 gate를 통과한다.
- baseline 대비 비용 또는 latency 개선이 있다.
- fallback/retry 증가가 허용 범위 이내다.
- candidate settings와 현재 배포 node fingerprint가 비교 가능한 상태다.

정확한 threshold는 여러 service에 숫자로 흩어 놓지 않고 versioned
`gate_profile`에서 관리한다. Policy와 trace에는 사용한 `gate_profile_version`을
남긴다.

### Judge boundary

Judge LLM은 정책의 최종 결정권자가 아니다.

Judge가 담당하는 일:

- 자유형 A/B 출력 품질 점수와 근거 생성
- 안전하게 요약된 evidence의 설명 문구 생성
- 후보 policy 초안에 대한 보조 신호 생성

Judge가 하면 안 되는 일:

- 권한 또는 model capability gate 우회
- 검증 표본이 없는 모델을 active rule에 직접 추가
- raw prompt, raw output, credential 또는 RAG chunk 원문 수신
- runtime 요청마다 호출되어 모델을 결정
- Judge JSON을 검증 없이 active policy로 저장

최종 policy는 deterministic optimizer가 검증된 candidate와 관측 metric만 사용해
만든다.

### Deterministic policy optimizer

Optimizer는 다음 우선순위를 지킨다.

1. Hard Gate와 quality gate를 통과한 후보만 남긴다.
2. 성공/schema/downstream 같은 이진 품질은 작은 표본을 100%로 믿지 않도록
   Wilson lower bound를 계산한다.
3. 자유형 quality score는 표본 수와 분산을 반영한 보수적 lower bound를 계산한다.
4. 각 후보가 `baseline_quality_lower_bound - allowed_quality_drop` 이상인 경우에만
   cohort의 품질 floor를 통과한다.
5. 품질 floor를 통과한 후보 중 사용자가 선택한 목적 함수에 따라 expected cost 또는
   latency를 최소화한다. 동률이면 보수적으로 현재 모델을 우선한다.
6. historical traffic share로 전체 예상 비용을 계산한다.
7. fallback, Replay/Judge, embedding routing overhead를 포함한 예상 순절감액이 양수일
   때만 변경한다.
8. 근거가 부족한 cohort는 현재 모델을 유지한다.

이 구조는 RouteLLM에서 참고한 `품질을 만족할 확률/threshold를 먼저 판단한 뒤 약한
모델을 선택한다`는 경계를 multi-model 환경으로 확장한 것이다. RouteLLM의 이진
strong/weak 선택기를 그대로 복사하지 않고, Nodease의 schema/downstream gate와
여러 provider 후보에 맞게 constrained optimization으로 구현한다.

Policy rule은 최소한 `cohort_id`, `when`, `selected_model_id`,
`fallback_model_id`, `reason_code`, `evidence_version`, `gate_profile_version`을
가진다. Fallback은 현재 execution subject가 실제 사용할 수 있고 해당 cohort에서
검증된 모델만 허용한다.

### Runtime

Runtime은 Judge나 optimizer를 호출하지 않는다. DB의 active policy snapshot을 읽고
일반 feature와 semantic cohort를 계산해 우선순위 rule을 평가한다. 사용할 수 없는
rule model은 건너뛰고 검증된 fallback을 사용하며, 사용할 수 있는 policy model이
없으면 provider 호출 전에 fail-closed한다. Semantic encoder를 사용할 수 없거나
분류가 애매하면 임의 Route를 선택하지 않고 default model로 닫는다.

Trace에는 다음 safe metadata를 남긴다.

- `strategy=workflow_aware_adaptive`
- policy id/version
- evidence version과 gate profile version
- selected/fallback model
- matched cohort/rule id
- semantic Route label, similarity, threshold, margin과 match status
- route catalog/encoder version
- decision source와 reason code
- fallback/escalation 여부와 사유
- `judge_called=false`

### Refresh triggers

`refresh_every_runs`는 호환용 주기 trigger로 유지할 수 있지만, 고정 N회는 policy
변경의 충분조건이 아니다. Policy 재평가는 다음 trigger로 수행한다.

- 새 validated Replay candidate 생성
- 새 운영 evidence가 gate 최소 표본을 충족
- 모델/credential availability 변경
- 품질, 비용 또는 latency drift 감지
- 사용자 수동 갱신
- 호환용 `auto_n_runs`

새 evidence가 없으면 refresh 결과는 `kept_current`이고 policy version을 불필요하게
증가시키지 않는다.

## Compatibility And Migration

- 기존 policy table, update table, run event table과 runtime evaluator를 재사용한다.
- 기존 bootstrap policy는 저장 모델을 유지하는 안전한 fallback으로 남긴다.
- 기존 `GET/PATCH/POST .../model-routing/policy` API를 유지한다.
- Policy refresh service에 operational/replay evidence adapter와 deterministic
  optimizer를 추가한다.
- 기존 `ModelRouter.resolve()`의 stage 기반 경로는 새 policy 생성의 source of truth로
  사용하지 않고 compatibility helper로 격리하거나 제거한다.
- 기존 synthetic E2E는 evaluator 단위 테스트로 재분류하고, DB Replay evidence에서
  active policy와 실제 runtime model 변경까지 이어지는 통합 테스트를 추가한다.

## Delivery Boundary

### 구현 순서와 코드 위치

Nodease의 test-first 규칙에 따라 아래 순서로 진행한다.

1. **실패 테스트 작성**
   - `apps/workflow_engine/tests/services/test_model_routing_evidence.py`
   - `apps/workflow_engine/tests/services/test_model_routing_eligibility.py`
   - `apps/workflow_engine/tests/services/test_model_routing_policy_optimizer.py`
   - `apps/workflow_engine/tests/integration/test_workflow_aware_adaptive_routing.py`
   - 먼저 Replay row가 policy로 연결되지 않는 현재 실패를 재현한다.

2. **공통 contract 정의**
   - `apps/shared/schemas/model_routing.py`
   - `RoutingEvidenceSummary`, `RoutingCandidate`, `RoutingEligibility`,
     `RoutingPolicySnapshot`, `RoutingDecisionTrace`를 정의한다.
   - API와 runtime이 서로 다른 string/status 의미를 만들지 않게 한다.

3. **Evidence adapter 구현**
   - `apps/workflow_engine/services/model_routing_evidence.py`
   - 기존 `ModelRouter.collect_profile()`의 operational query를 adapter로 감싼다.
   - `CostOptimizerExperiment`와 `CostOptimizerCandidate`를 읽는 Replay adapter를
     추가한다.
   - source, cohort, fingerprint, sample count와 safe metric을 보존한다.

4. **Hard Gate와 적합성 분석 구현**
   - `apps/workflow_engine/services/model_routing_eligibility.py`
   - 기존 `LLMService.get_runtime_available_model_ids_for_user()`와 workflow chat
     model filter를 재사용한다.
   - 권한/capability를 통과한 후보가 둘 미만이거나 품질 계약이 없으면
     `fixed_model_recommended` 또는 `needs_evidence`로 닫는다.

5. **결정론적 optimizer 구현**
   - `apps/shared/services/model_routing_policy_optimizer.py`
   - DB나 provider 호출이 없는 pure function으로 만든다.
   - validated candidate, cohort traffic share, versioned gate profile을 입력받아
     proposal과 reason code를 반환한다.
   - Judge output을 직접 입력 계약으로 삼지 않고 normalized quality metric만
     받는다.

6. **기존 refresh pipeline 연결**
   - `apps/workflow_engine/services/model_routing_policy_refresh.py`
   - `apps/workflow_engine/services/model_routing_policy_refresh_task.py`
   - 현재 Judge 중심 policy normalization 앞에 evidence/eligibility/optimizer를
     연결한다.
   - 새 evidence가 없으면 `kept_current`, gate 실패면 `pending_review`, 검증
     proposal이면 `applied`로 기존 update lifecycle을 재사용한다.

7. **Runtime trace 확장**
   - `apps/workflow_engine/workflow/nodes/llm/llm_node.py`
   - 기존 `ModelRouter.resolve_policy()`의 결정론적 rule 평가를 유지한다.
   - strategy, matched cohort, evidence/gate version과 escalation/fallback reason을
     metadata에 추가한다.
   - runtime에서 evidence query, optimizer 또는 Judge를 호출하지 않는다.

8. **실제 DB 통합 검증**
   - baseline experiment/candidate row를 DB에 저장한다.
   - refresh task를 통해 active policy를 만든다.
   - 두 cohort 입력으로 LLM node runtime을 실행한다.
   - 선택 모델과 trace가 policy와 일치하는지 확인한다.
   - 기존 `test_model_routing_policy_e2e.py`의 미리 주입한 profile은 회귀
     테스트로 유지하되 새 통합 테스트를 대체하지 않는다.

9. **밤 UI 연결**
   - `apps/client/app/modules/[id]/model-routing/[nodeId]/page.tsx`
   - LLM node policy panel과 실행 로그 상세를 연결한다.
   - Backend analysis API가 준비되기 전에는 가짜 적합성/절감 데이터를 만들지
     않는다.

### 유지할 기존 경계

- Gateway가 Workflow Engine package를 직접 import하지 않는다.
- 기존 policy/update/run-event table과 unique/idempotency 경계를 유지한다.
- 기존 terminal workflow 완료 후 운영 run 집계 경계를 유지한다.
- 기존 execution subject credential guard를 우회하지 않는다.
- Cost Optimizer candidate의 raw prompt/output을 policy update에 복사하지 않는다.
- Semantic query 원문과 embedding vector를 trace/API에 노출하지 않는다.
- 현재 core 변경에 Shadow/Canary용 schema migration을 섞지 않는다.

### 현재 목표 완료 범위

- 공통 evidence/candidate/policy/decision 계약 정의
- operational/replay evidence adapter 구현
- Hard Gate와 Routing Eligibility Analyzer 구현
- validated Replay candidate만 사용하는 deterministic optimizer 구현
- 기존 refresh task에서 새 pipeline 호출
- active policy와 runtime trace에 cohort/evidence/gate version 연결
- DB Replay 결과부터 runtime model 선택까지 실제 통합 테스트
- versioned Semantic Route catalog와 Aurelio식 matcher 구현
- 서로 다른 의미 입력이 서로 다른 검증 모델로 선택되는 runtime/trace 검증

완료 판정은 한 workflow의 한 LLM node에서 다음 흐름이 모두 증명되는 것이다.

1. 현재 모델과 저비용 후보를 같은 입력군으로 Replay한다.
2. 후보가 schema/downstream/quality gate를 통과한다.
3. refresh가 검증 후보로 policy proposal을 만든다.
4. active policy가 해당 cohort에서 후보 모델을 선택한다.
5. 다른 cohort 또는 근거 부족 입력은 현재 모델을 유지한다.
6. trace에서 선택 모델, cohort, rule, policy/evidence version과 사유를 확인한다.
7. gate 실패 후보는 active policy에 들어가지 않는다.

### 후속 제품 범위

- 라우팅 적합성, evidence gap, 예상 절감, policy diff UI
- 실제 선택 모델, matched rule, fallback, policy version 실행 로그 UI
- 사용자 activation/rollback과 정책 이력 UI
- traffic share와 counterfactual 예상 절감 계산 보강
- 시연용 토글 → 배포 → Replay 검증 → policy 반영 → 서로 다른 모델 선택 QA

### 후속 범위

- Shadow와 Canary 실행
- Selective Cascade
- provider SLO와 cross-provider fallback 고도화
- 고정 N회 대신 drift/증거 기반 adaptive refresh 전면 전환
- Semantic Route catalog를 운영 evidence로 안전하게 보정하는 승인 flow
- Contextual Bandit

Shadow, Canary, Cascade, Contextual Bandit을 현재 구현 완료 대상으로 보지 않는다.
현재 핵심은 versioned Semantic Route가 검증 evidence, active policy, runtime 선택과
trace로 이어지는 끊기지 않은 한 경로다.

## Consequences

장점:

- 모델을 싸다는 이유만으로 운영에 투입하지 않는다.
- Replay에 이미 지불한 비교 비용이 policy 개선 증거로 재사용된다.
- Judge 비용이 runtime 요청 수에 비례하지 않는다.
- 모델이 바뀌지 않은 경우에도 `근거 부족`, `고정 모델 권고`처럼 이유를 설명할 수
  있다.
- 정책 선택이 trace와 evidence version으로 재현 가능하다.

비용:

- evidence source, cohort, gate profile version을 함께 관리해야 한다.
- 자유형 출력은 Judge 품질 평가 비용이 발생한다.
- Replay input이 실제 traffic을 대표하지 않으면 잘못된 policy를 만들 수 있으므로
  traffic share와 표본 편향을 표시해야 한다.
- Shadow/Canary가 없기 전에는 Replay에서 검증한 결과와 실제 운영 결과 사이의 차이를
  완전히 제거할 수 없다.

## Rejected Alternatives

1. 매 요청마다 Judge가 모델을 고르게 하는 방식: 비용과 latency가 runtime traffic에
   비례하고, Judge 장애가 서비스 장애가 된다.
2. 가격순 cheap/mid/high 자동 분류: 가격은 capability와 품질을 보장하지 않는다.
3. 20회마다 무조건 모델 변경: 새 증거가 없는 상태에서 정책을 바꾸는 근거가 없다.
4. 운영 runtime이 임의로 저비용 모델을 탐색: 사용자 응답 품질을 검증 없이 위험에
   노출한다.
5. Semantic similarity만으로 모델을 직접 선택: 의미가 비슷하다는 사실은 해당
   모델의 품질을 보장하지 않으므로 Replay/운영 quality gate와 분리할 수 없다.
6. 처음부터 Contextual Bandit 도입: 후보 evidence와 안전한 탐색 경계가 없는 상태의
   online exploration은 운영 품질을 위험에 노출한다.
