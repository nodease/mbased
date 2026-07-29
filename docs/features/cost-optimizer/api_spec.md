# Cost Optimizer API Spec

Status: Draft
Verified Against: origin/dev @ 79da97f57aa116bdd73ce51cb2ff365b03bed713

## Purpose

이 문서는 `requirements.md`의 FR-001부터 FR-016까지를 API 계약 관점에서 정리한다.
FR-015는 비운영 실험 경계, FR-016은 아직 구현되지 않은 Capability Routing V2 Target 계약이다.

Current routing code comparison: `origin/dev @ 79da97f57aa116bdd73ce51cb2ff365b03bed713`

현재 FR-011은 `judge_bootstrap_incremental_v1` strategy의 무수동-bootstrap 구현을 기술한다. 초기 운영 요청은 runtime Judge가
요구 수준을 판정하고, 서버가 실행 주체가 사용할 수 있는 전체 후보에서 capability와 가격을
비교해 모델을 선택한다. Judge 요구 수준 label과 완료된 운영 결과는 배포 정책과 분리된
자동 라우팅 학습기에 쌓인다. Celery가 학습기 행을 잠그고 10건 또는 최대 5분 batch로
candidate artifact를 학습한다. 현재 구현은 처음 50건을 학습한 뒤 다음 50건의 학습 전 예측으로
총 100 labels, recent evaluation 50건과 exact 75% 및 계약 품질을 통과한 artifact만 로컬 라우터로
승격한다. 이 값은 ADR-0059와 다른 Current 회귀값이며 FR-016 Target gate가 아니다. Local prediction이 불확실하거나
권한 모델이 바뀐 경우에는 Judge로 돌아간다. 원문 prompt/input/KB 내용은 API 응답, policy
artifact, 학습 이력에 저장하지 않는다. 검증을 통과한 artifact는 불변 learner version으로
발행하며, 배포 정책은 `learner_id`와 `active_learner_version_id`만 참조한다.

bootstrap API는 과거 draft 데이터 조회 호환용이다. 신규 UI와 배포 preflight는 bootstrap 생성을
요구하지 않으며, policy 행이 없는 runtime도 일회성 Judge-first policy를 구성해 실행한다.
이전 전략의 정적 rule이나 semantic cohort는 신규 runtime의 source of truth가 아니다.

Cost Optimizer API는 특정 workflow의 특정 LLM node를 기준으로 baseline 실행 로그를 선택하고, 같은 입력으로 B 후보 설정을 실행한 뒤, 선택한 후보를 현재 draft에 적용하는 흐름을 지원한다.

모델 라우팅 policy 전용 REST API와 `llm_node_model_routing_policies` table이 자동 라우팅의 source of truth다. LLM node data의 `auto_model_routing`, `refresh_every_runs`는 policy 설정을 갱신하는 입력이며, 일반 배포 runtime은 DB의 active policy snapshot만 읽는다.

파라미터 추천 API는 구현되어 있다. `GET /cost-optimizer/parameter-recommendations`는 운영 로그 기반 추천을 반환하고, `PATCH /cost-optimizer/apply-recommendations`는 `direct_policy_update` 추천만 현재 draft에 즉시 반영한다.

## FR Mapping

| FR | API 책임 |
| --- | --- |
| FR-001 | LLM node인지 확인하고 A/B 테스트 진입 가능 여부를 판단한다. |
| FR-002 | target LLM node의 baseline 실행 로그 목록과 최신 baseline을 제공한다. |
| FR-003 | B 후보 설정 request schema를 정의한다. |
| FR-004 | baseline input을 B 후보 실행 입력으로 고정한다. |
| FR-005 | A는 재실행하지 않고 B 후보만 실행하는 compare API를 제공한다. |
| FR-006 | A/B 결과와 trace/Inspector에 필요한 데이터를 반환한다. |
| FR-007 | baseline graph와 current graph의 downstream 호환성 상태를 반환한다. |
| FR-008 | 선택한 B 후보 설정을 current draft target LLM node에 적용한다. |
| FR-009 | 비교 실행에서 발생한 LLM usage/cost를 기록한다. |
| FR-010 | builder 이상 권한을 API에서 강제한다. |
| FR-011 | policy 조회/설정/refresh/preview API와 배포 후 run event가 policy table을 관리한다. Judge bootstrap 요구 판정, local router 전환 상태, 실행 주체 후보 모델과 노드별 운영 성적을 사용해 서버의 runtime 모델 선택을 제공한다. |
| FR-012 | 운영 로그와 trace summary를 분석해 LLM 파라미터/모델 라우팅 추천을 반환한다. `direct_policy_update`만 즉시 적용하고, 일반 파라미터 조정은 A/B candidate 생성 경로로 보낸다. |
| FR-013 | 추천 빠른 검증과 사용자가 baseline을 고르는 일반 compare 모두 candidate 실행 후 semantic 품질 평가를 수행하고 점수·confidence·safe summary를 응답과 이력에 저장한다. |
| FR-014 | 배포별 자동 파라미터 최적화 설정과 수집/예산 safe summary를 제공한다. 비용 위험 신호와 모델 라우팅 정책은 포함하지 않는다. |
| FR-015 | Production API와 active policy를 변경하지 않는 실험 manifest/result matrix 계약을 제공한다. |
| FR-016 | `capability_routing_v2`의 canonical source, activation profile, actor/scope, typed error와 호환 projection Target 계약을 정의한다. |

## Endpoints

| Method | Path | Description | Related FR | Auth |
| --- | --- | --- | --- | --- |
| GET | `/api/v1/workflows/{workflow_id}/llm-nodes/{node_id}/cost-optimizer/availability` | A/B 테스트 진입 가능 여부 조회 | FR-001, FR-010 | builder 이상 |
| GET | `/api/v1/workflows/{workflow_id}/llm-nodes/{node_id}/cost-optimizer/baselines/latest` | 최신 baseline 실행 로그 조회 | FR-002, FR-004, FR-007 | builder 이상 |
| GET | `/api/v1/workflows/{workflow_id}/llm-nodes/{node_id}/cost-optimizer/baselines` | baseline 실행 로그 목록 검색/필터/정렬 | FR-002, FR-004, FR-007 | builder 이상 |
| GET | `/api/v1/workflows/{workflow_id}/llm-nodes/{node_id}/cost-optimizer/experiments` | 과거 experiment/candidate 결과 목록 조회 | FR-006, FR-009, FR-010 | builder 이상 |
| GET | `/api/v1/workflows/{workflow_id}/llm-nodes/{node_id}/cost-optimizer/experiments/{experiment_id}/candidates/{candidate_id}` | 결과 분석 deep link용 experiment/candidate 단건 safe summary 조회 | FR-006, FR-009, FR-010, FR-013 | builder 이상 |
| GET | `/api/v1/workflows/{workflow_id}/llm-nodes/{node_id}/cost-optimizer/parameter-recommendations` | 운영 로그 기반 LLM 파라미터/모델 라우팅 추천 조회 | FR-012 | builder 이상 |
| PATCH | `/api/v1/workflows/{workflow_id}/llm-nodes/{node_id}/cost-optimizer/apply-recommendations` | `direct_policy_update` 추천을 현재 draft에 즉시 적용 | FR-012 | builder 이상 |
| POST | `/api/v1/workflows/{workflow_id}/llm-nodes/{node_id}/cost-optimizer/recommendations/verify` | 최신 성공 baseline으로 추천 candidate를 한 번 실행하고 품질·schema·downstream·신규 비용을 평가 | FR-013 | builder 이상 |
| POST | `/api/v1/workflows/{workflow_id}/llm-nodes/{node_id}/cost-optimizer/compare` | 선택 baseline input으로 B 후보를 실행하고 A/B 출력 품질을 평가 | FR-003, FR-004, FR-005, FR-006, FR-009, FR-010, FR-013 | builder 이상 |
| PATCH | `/api/v1/workflows/{workflow_id}/llm-nodes/{node_id}/cost-optimizer/apply` | 선택한 B 후보 설정을 current draft에 적용 | FR-008, FR-010 | builder 이상 |
| GET | `/api/v1/workflows/{workflow_id}/llm-nodes/{node_id}/model-routing/policy` | 현재 policy 상태, 누적 운영 run 수, active/pending policy 조회 | FR-011 | builder 이상 |
| GET | `/api/v1/workflows/{workflow_id}/llm-nodes/{node_id}/model-routing/bootstrap-preview` | 동일 작업 지문의 안전한 운영 로그 수와 초기 생성 방식 미리 보기 | FR-011 | builder 이상 |
| GET | `/api/v1/workflows/{workflow_id}/llm-nodes/{node_id}/model-routing/bootstrap` | 현재 bootstrap artifact와 가림 처리한 표본 summary 조회 | FR-011 | builder 이상 |
| POST | `/api/v1/workflows/{workflow_id}/llm-nodes/{node_id}/model-routing/bootstrap` | 작업 설명과 기본·대체 모델로 초안 Judge-first bootstrap을 동기 생성 | FR-011 | builder 이상 |
| POST | `/api/v1/workflows/{workflow_id}/llm-nodes/{node_id}/model-routing/preview` | active deployment policy를 기록 없이 한 번 평가해 선택 모델과 근거를 반환 | FR-011 | execute |
| PATCH | `/api/v1/workflows/{workflow_id}/llm-nodes/{node_id}/model-routing/policy` | 자동 라우팅 ON/OFF, 기본 모델/대체 모델, 정책 점검 주기 변경 | FR-011 | builder 이상 |
| POST | `/api/v1/workflows/{workflow_id}/llm-nodes/{node_id}/model-routing/policy/refresh` | 현재 운영 성적으로 policy 재평가 작업을 예약 | FR-011 | builder 이상 |
| GET | `/api/v1/deployments/{deployment_id}/parameter-optimization` | 배포별 자동 파라미터 최적화의 대상 노드 수, 운영 수집 수, 점검 상태, 월간 검증 예산/사용액 조회 | FR-014 | workflow read |
| PATCH | `/api/v1/deployments/{deployment_id}/parameter-optimization` | 재배포 없이 자동 최적화 사용 여부, 대상 LLM node, 점검 주기, 월간 검증 예산 수정 | FR-014 | workflow deploy |

## Implementation Tracking

현재 문서는 Cost Optimizer API 계약과 구현 추적 상태를 함께 기록한다. 실제 router/service/schema 파일명은 Gateway의 기존 workflow/app API 구조에 맞춰 확정한다.

| FR | API/계약 단위 | 예상 코드 위치 | 구현 상태 | API 테스트 코드 | 테스트 통과 여부 |
| --- | --- | --- | --- | --- | --- |
| FR-001 | `GET availability` | `apps/gateway/api/v1/endpoints/workflow.py` | 구현 완료 | `apps/gateway/tests/api/cost_optimizer/test_cost_optimizer_api.py` | 통과 |
| FR-002 | `GET baselines/latest`, `GET baselines` | `apps/gateway/api/v1/endpoints/workflow.py` | 구현 완료 | `apps/gateway/tests/api/cost_optimizer/test_cost_optimizer_api.py` | 통과 |
| FR-003 | compare candidate request schema | `apps/gateway/api/v1/endpoints/workflow.py` | 구현 완료 | `apps/gateway/tests/api/cost_optimizer/test_cost_optimizer_api.py` | 통과 |
| FR-004 | baseline input restore/lock | `apps/gateway/api/v1/endpoints/workflow.py` | 구현 완료 | `apps/gateway/tests/api/cost_optimizer/test_cost_optimizer_api.py` | 통과 |
| FR-005 | hybrid compare execution | `apps/gateway/api/v1/endpoints/workflow.py`, `apps/workflow_engine/` | 구현 완료 | `apps/gateway/tests/api/cost_optimizer/test_cost_optimizer_api.py` | 통과 |
| FR-006 | compare response trace/diff | `apps/gateway/api/v1/endpoints/workflow.py` | 구현 완료 | `apps/gateway/tests/api/cost_optimizer/test_cost_optimizer_api.py` | 통과 |
| FR-007 | downstream compatibility response | `apps/gateway/api/v1/endpoints/workflow.py`, workflow graph helper | 구현 완료 | `apps/gateway/tests/api/cost_optimizer/test_cost_optimizer_api.py` | 통과 |
| FR-008 | `PATCH apply` | `apps/gateway/api/v1/endpoints/workflow.py` | 구현 완료 | `apps/gateway/tests/api/cost_optimizer/test_cost_optimizer_api.py` | 통과 |
| FR-009 | LLM usage/cost logging/history | `apps/gateway/api/v1/endpoints/workflow.py`, `apps/workflow_engine/`, `apps/shared/db/models/cost_optimizer.py`, `apps/shared/services/cost_optimizer_retention.py` | 구현 완료 | `apps/gateway/tests/api/cost_optimizer/test_cost_optimizer_api.py`, `apps/workflow_engine/tests/nodes/test_llm_node_runtime.py`, `apps/shared/tests/services/test_cost_optimizer_retention.py` | 통과 |
| FR-010 | builder permission enforcement | `apps/gateway/api/v1/endpoints/workflow.py`, `apps/gateway/auth/permissions.py` | 구현 완료 | `apps/gateway/tests/api/cost_optimizer/test_cost_optimizer_api.py` | 통과 |
| FR-011 | Judge-first policy persistence, event idempotency, refresh, credential guard, trace | `apps/shared/db/models/model_routing_policy.py`, `apps/gateway/api/v1/endpoints/workflow.py`, `apps/workflow_engine/tasks.py`, `apps/workflow_engine/services/model_routing_policy_refresh_task.py`, `apps/workflow_engine/workflow/nodes/llm/llm_node.py` | 현재 경로 구현. V1/V2 contract 분리 미완료 | FR-011 targeted tests | 현재 구현 회귀 통과 |
| FR-011 | Runtime Judge, 점진 학습 local classifier, runtime candidate selection | `apps/workflow_engine/services/model_routing_runtime_judge.py`, `model_routing_incremental_learning.py`, `model_routing_local_classifier.py`, `model_router.py`, `llm_node.py` | 현재 경로 구현. Strategy ID·task intent·learner gate가 Target과 불일치 | `test_model_router.py`, `test_model_routing_incremental_learning.py`, `test_model_routing_policy_refresh_task.py`, `test_llm_node_runtime.py` | 현재 구현 회귀 통과 |
| FR-011 | 정책 독립 학습기, 불변 버전, 학습 전 예측과 Celery batch | `apps/workflow_engine/services/model_routing_learner_store.py`, `apps/workflow_engine/services/model_routing_learning_batch.py`, `apps/workflow_engine/tasks.py`, `apps/shared/db/models/model_routing_policy.py` | 현재 경로 구현. Rejected label/contract rotation 보완 필요 | `test_model_routing_learner_store.py`, `test_model_routing_learning_batch.py`, `test_model_routing_policy_tasks.py`, `test_model_routing_requirement_learning.py` | 현재 구현 회귀 통과 |
| FR-011 | 과거 정책 호환 경계 | refresh 시 Judge-first로 1회 이관하고 신규 runtime에서는 레거시 rule을 실행하지 않음 | 구현 완료 | `test_model_routing_policy_refresh_task.py`, `test_llm_node_runtime.py` | 통과 |
| FR-012 | LLM parameter recommendation contract | `apps/gateway/services/cost_optimizer_parameter_recommendation_service.py`, `apps/gateway/api/v1/endpoints/workflow.py` | 구현 완료 | `apps/gateway/tests/api/cost_optimizer/test_cost_optimizer_api.py`의 `test_fr12_*` | 테스트 존재 확인. 이번 문서 PR에서 재실행하지 않음 |
| FR-013 | Recommendation/compare verification orchestration, quality judge, history summary, modal/result-analysis UI | `apps/gateway/services/cost_optimizer_recommendation_verification_service.py`, `apps/gateway/services/cost_optimizer_output_quality_service.py`, `apps/gateway/api/v1/endpoints/workflow.py`, `apps/shared/db/models/cost_optimizer.py`, `apps/client/app/features/workflow/components/costOptimizer/OptimizationRecommendationModal.tsx`, `apps/client/app/features/workflow/api/workflowApi.ts`, `apps/client/app/modules/[id]/cost-optimizer/[nodeId]/page.tsx` | 구현 완료 | `apps/gateway/tests/api/cost_optimizer/test_cost_optimizer_api.py`, `apps/gateway/tests/api/cost_optimizer/test_recommendation_verification_api.py`, `apps/gateway/tests/services/test_cost_optimizer_output_quality_service.py`, `apps/client/app/features/workflow/tests/costOptimizer/fr13-recommendation-inline-verification.test.tsx`, `apps/client/app/features/workflow/tests/costOptimizer/fr13-recommendation-verification-api-client.test.ts`, `apps/client/app/features/workflow/tests/costOptimizer/fr6-playground-mode-switch.test.tsx` | Gateway/frontend targeted test 통과 |
| FR-014 | deployment config, plan persistence, operations summary, verification spend guard | `apps/shared/db/models/deployment_parameter_optimization.py`, `apps/gateway/services/deployment_parameter_optimization_service.py`, `apps/gateway/api/v1/endpoints/deployment.py`, `apps/gateway/api/v1/endpoints/workflow.py`, `apps/gateway/services/app_service.py` | 수집/상태/예산 설정과 실제 검증 비용 누적 구현 | `apps/gateway/tests/services/test_deployment_parameter_optimization_service.py`, `apps/gateway/tests/api/cost_optimizer/test_recommendation_verification_api.py`, operations API targeted test | 통과 |
| FR-015 | constraint/prior 실험 manifest와 result matrix | 후속 `tests/experiments/`, `scripts/experiment_constraint_difficulty_router.py` | 계약만 정의. 명시한 script·test·report artifact는 현재 tree에 없고 Production API도 없음 | experiment targeted tests 계획 | 미구현·미실행 |
| FR-016 | Capability Routing V2 strategy/source/profile/activation governance | MBA-366~369 및 별도 profile/ledger/benchmark/activation 이슈 | Target 계약만 확정. Production 구현·활성화 안 됨 | `test_cases.md`의 M365 matrix | 미구현 |

## Deployment Parameter Optimization Contract

`DeploymentCreate`와 `DeploymentPreflightRequest`는 선택적으로 아래 `parameter_optimization` object를 받는다.

```json
{
  "enabled": true,
  "node_ids": ["llm-triage"],
  "check_every_runs": 50,
  "monthly_validation_budget_usd": 3.0
}
```

`enabled=true`에서 `node_ids`가 비어 있으면 해당 deployment snapshot의 모든 `llmNode`를 뜻한다. 지정한 node id가 snapshot에 없거나 LLM node가 아니면 `422 deployment.parameter_optimization_invalid_node`으로 거부한다. LLM node가 하나도 없으면 `422 deployment.parameter_optimization_no_llm_node`으로 거부한다.

`enabled=false`인 배포도 관리 화면에서 실제 대상을 표시하고 이후 재배포 없이 활성화할 수 있도록 deployment snapshot의 전체 LLM node id, 점검 주기와 월간 검증 예산을 `disabled` plan에 보존한다. LLM node가 없는 비활성 배포는 빈 대상 목록으로 정상 생성하며, 활성화할 때만 `deployment.parameter_optimization_no_llm_node`로 거부한다.

상태 응답은 raw prompt, completion, credential, recommendation patch를 포함하지 않는다. 목록 API의 `automatic_optimization`은 node id를 제외한 safe summary만 반환하고, 상세 관리 API는 설정을 유지하기 위해 `node_ids`를 함께 반환한다.

`POST /cost-optimizer/recommendations/verify`는 latest operation baseline의 `deployment_id`가 enabled plan을 가리키고 target `node_id`가 plan의 대상일 때만 월간 검증 예산을 확인한다. 이미 사용액이 한도 이상이면 `409 deployment.parameter_optimization_validation_budget_exhausted`로 후보 LLM 실행 전에 차단한다.

후보 실행이 끝나면 실제로 확인된 `candidate_execution_cost`와 `quality_judge_cost`만 합산해 `validation_spend_usd`에 기록한다. baseline 읽기, 일반 compare, 일반 배포 실행 비용은 이 필드에 기록하지 않는다. quality judge 비용이 없거나 계산할 수 없어도 확인 가능한 후보 실행 비용은 기록한다.

## Model Routing Policy Contract

현재 자동 모델 라우팅은 `judge_bootstrap_incremental_v1` strategy의 무수동-bootstrap 경로를 사용한다. 입력군, 대표 예문,
embedding vector, semantic cohort endpoint는 제공하지 않는다. 초기 운영 요청은 runtime Judge가
요구 수준만 판정하고, 서버가 현재 실행 주체가 사용할 수 있는 전체 후보 중 capability와 가격을
비교해 처리 모델을 선택한다. 후보의 운영 검증 상태는 첫 사용을 막지 않으며, 이후 Judge 요구
수준 label과 완료된 운영 결과를 바탕으로 local router가 먼저 요구 수준을 예측한다.

Runtime Judge 요청에는 후보 모델 목록을 넣지 않는다. Judge에는 현재 요청·노드 계약과
`input_token_bucket`, `schema_required`, `knowledge_enabled`, `retrieved_source_count` 같은
코드 계산 사실만 전달한다. 서버는 provider 공식 문서로 관리하는 `canonical_model_id`,
`reasoning_profile`, `complexity_ceiling`, `cost_position`, `task_affinities`를 사용해 현재
실행 가능한 후보를 비교한다. 공식 별칭과 정식 ID가 동시에 실행 가능하면 정식 ID 하나를
선택하고, 별칭만 실행 가능하면 credential 조회가 가능한 별칭을 유지한다.
날짜 suffix 고정 버전과 전역 workflow 실행 제외 모델인 `gpt-5-mini`는 후보에서 제거한다.
기존 graph 또는 저장 policy가 `gpt-5-mini`를 직접 참조하더라도 Provider 호출 전에 차단하고,
유효한 fallback이 있으면 fallback으로 실행한다.

### Capability Routing V2 Target Contract

이 절은 [ADR-0073 Capability Routing V2](../../decisions/ADR-0073-requirement-judge-capability-routing-v2.md)의
미구현 Target API·wire 계약이다. 현재 endpoint가 이 필드를 반환하거나 platform activation command를
지원한다고 해석하지 않는다.

V2 strategy ID는 `capability_routing_v2`다. Workflow policy가 V2를 선택하더라도 서버는 다음
조건의 교집합이 유효할 때만 이를 effective strategy로 사용한다.

```text
feature flag와 emergency kill switch 허용
AND immutable strategy contract 등록
AND immutable rollout family 안에서 current contract를 만족하는 `limited_canary` 또는 `production_active` profile 존재
AND profile이 exact Judge-only 또는 approved learner requirement source manifest를 고정하고 현재 사용 가능
AND family selection으로 deployment가 stable-canary successor 또는 predecessor production profile에 포함
AND workflow deploy actor가 해당 strategy version과 immutable rollout family/activation domain 선택
AND Judge와 각 primary/fallback exact model을 승인하는 current V2 credential policy 유효
AND Judge와 각 primary/fallback이 current global workflow model execution eligibility 충족
AND 실제 delivery를 소비한 Worker가 profile의 최소 runtime capability revision 충족
AND 재검증·발행된 non-V2 rollback policy version 유효
AND environment global kill과 선택 profile-bound safety generation/block/epoch가 current
```

하나라도 충족하지 못하면 V2 Requirement Judge를 호출하지 않는다. Workflow에 별도로 versioning된
current-valid non-V2 policy 또는 stored safe path가 있으면 이를 사용하고, 그런 경로도 없으면 외부
provider 호출 전에 typed error로 닫는다. Contract digest가 없는 legacy V1 artifact를 임의의 V2
rollback target으로 사용하지 않는다. Rollout family는 immutable activation domain을 가진다. 같은 family에는
`production_active` predecessor 최대 하나와 `limited_canary` successor 최대 하나만 공존할 수 있다. Stable
canary 배정이 successor에 일치하면 successor를 사용한다. Predecessor가 있으면 나머지는 predecessor를,
없으면 exact current-valid non-V2 rollback policy를 사용한다. Promotion은 predecessor가 있을 때만 같은
transaction에서 successor를 `production_active`로 전이하고 predecessor의 admission state를
`superseded`로 전이한다. Profile은 immutable definition revision, 신규 run 선택용 admission lifecycle
revision, in-flight attempt 안전용 runtime safety revision을 분리한다. 정상 promotion은 predecessor의
admission revision만 증가시키고 runtime safety revision과 generation은 유지한다. 최초 promotion은
successor와 domain role만 갱신한다. `superseded`는 새 run의 predecessor 선택만 막는다. Promotion 전에
predecessor를 고정한 in-flight run은 definition/runtime-safety revision과 generation이 current이고 별도
emergency disable/block/revoke가 없는 한 이후 node attempt도 완료하며, 새 run만 successor를 선택한다.
Profile은 server-issued immutable `canary_assignment_contract_version` reference와 target range를 가진다.
Assignment contract는 rollout family 생성 또는 기존 family의
`rollout_assignment_contract_issue` command에서 CSPRNG로 발급한 최소 128-bit non-secret opaque seed와
bucket count를 내부에 가진다. Family 생성은 initial contract와 revision 0의 assignment-registry row를 함께 만든다. Issue command는 expected family/assignment-registry revision과
algorithm/unit/count를 CAS하고 기존 profile/contract를 변경하지 않는다. Profile 작성 request는 raw
seed를 제출할 수 없다. Server는 environment/family/domain/organization/workflow/immutable deployment ID,
`canary_assignment_contract_version`과 server-held `canary_assignment_seed`를 contract-defined versioned
canonical bytes로 인코딩하고 `uint64_be(SHA256(bytes)[0:8]) mod canary_bucket_count`로 계산한다. Profile revision, safety generation,
Worker/process, retry, 시간과 runtime random은 입력에 포함하지 않는다. 같은 contract의 retry/restart는
같은 bucket을 사용한다. V1 bucket count는 10,000이고 target ranges는 `[0, 10000)` 안의 non-empty,
정렬·비중첩 구간이며 전체를 선택할 수 없다. Contract/seed/unit 변경은 새 proposed profile과
canary-start 승인을 요구한다.
Client/task payload는 assignment input이나 bucket을 제출할 수 없으며 API는 원 unit/seed 대신 safe
contract version과 range digest만 투영한다.
Activation domain/scope 변경은 같은 family revision이 아니라 명시적 migration contract가 필요한 별도
전환이며, 그 contract가 없으면 활성 domain overlap을 zero-write로 거부한다.
Candidate learner가 gate를 통과하거나 새 version이 발행돼도 current activation profile은 자동으로
바뀌지 않는다. 다른 learner version 또는 Judge-only 전환은 새 profile revision과 activation 절차를
요구한다.

#### Canonical decision safe projection

V2 runtime decision safe projection은 구현 이슈에서 기존 응답에 additive하게 다음 필드를 도입한다.
Policy와 preview는 적용 가능한 필드만 반환한다. `policy_preview`는 provider/Judge를 호출하거나
학습에 포함되지 않으며 current requirement source가 없으면 최종 모델을 확정하지 않는다.

~~~json
{
  "requested_strategy_id": "capability_routing_v2",
  "effective_strategy_id": "capability_routing_v2",
  "strategy_resolution_reason": "requested_active",
  "strategy_contract_version": "opaque-version",
  "routing_contract_version": "opaque-version",
  "requirement_contract_version": "opaque-version",
  "selection_contract_version": "opaque-version",
  "requirement_rubric_version": "opaque-version",
  "model_evidence_version": "opaque-version",
  "pricing_version": "opaque-version",
  "activation_profile_version": "opaque-version",
  "activation_requirement_source_mode": "judge_only",
  "activation_requirement_source_version": "opaque-version",
  "workflow_policy_version": "opaque-version",
  "worker_runtime_capability_revision": "opaque-revision",
  "learner_version": null,
  "execution_mode": "deployed",
  "requirement_source": "runtime_judge",
  "requirement_evaluation_scope": "current_execution",
  "model_selection_source": "server_capability_selector",
  "routing_reuse_source": "none",
  "judge_attempt_count": 1,
  "requirement_adjudicated": false,
  "selected_model_id": "current-authorized-model",
  "fallback_model_id": "current-authorized-fallback",
  "reason_codes": ["bounded_allowlist_code"],
  "included_in_routing_learning": true
}
~~~

`requested_strategy_id`, `effective_strategy_id`와 bounded `strategy_resolution_reason`은 요청 policy와
실제 실행 policy를 구분한다. V2 off, profile/requirement source/scope/credential policy 또는 Worker 부적격으로 non-V2
path가 effective이면 V2 decision object를 만들지 않고 이 strategy resolution과 해당 non-V2
projection만 반환한다. 실행 가능한 non-V2 path도 없어 provider I/O 전 typed failure로 닫으면
`effective_strategy_id=null`이다. 기존 `strategy_id`는 호환 기간에 non-null `effective_strategy_id`를
투영하는 deprecated
field일 뿐 requested/effective 차이를 대체하지 않는다.

`requirement_contract_version`은 Judge/learner 의미, task intent normalization contract와 canonical
normalized `task_intent`, internal task semantic fingerprint를,
`selection_contract_version`은 selector/cache 의미를 고정한다. Task semantic fingerprint는 canonical
deployment snapshot의 canonical intent/normalization version, 고정 prompt/instruction, variable mapping,
output/schema, Knowledge/RAG configuration과 downstream structural contract가 바뀌면 함께 회전하지만
fingerprint와 원본 구성 요소를 API에 노출하지 않는다. `routing_contract_version`은 두 값을 결합한
server-side top-level provenance를 가리키는 safe
opaque version이며 raw digest 구성 요소를 노출하지 않는다. `activation_requirement_source_mode`은
`judge_only | approved_learner`이고 `activation_requirement_source_version`은 profile이 고정한 exact
Judge source manifest 또는 approved learner manifest의 opaque version이다. `learner_version`은 local
classifier를 실제 사용한 경우에만 값이 있다. Policy preview에서는 `judge_attempt_count=0`,
`included_in_routing_learning=false`이고, requirement source가 없으면 `selected_model_id`를 생략한다.

Accepted decision cache의 internal selection contract와 namespace는 organization, deployment/version,
canonical node location, activation profile ID/revision, exact requirement source manifest digest,
selector/catalog/profile/pricing contract와 HMAC key epoch를 결합한다. Profile/source가 learner A에서
B 또는 `judge_only`로 바뀌면 requirement contract가 같아도 mandatory miss다. 이전 source가 만든
requirement/model recommendation을 새 profile에서 재사용하지 않으며 raw digest 구성 요소는 응답,
trace와 audit에 노출하지 않는다.

`policy_preview` 전용 `preview_status`는 `complete | requirement_pending | blocked`다. `complete`는
current local/cache requirement와 server-known gate만으로 safe selection projection을 계산한 경우,
`requirement_pending`은 Judge 호출 없이는 요구 판정이 없는 경우, `blocked`는 activation/profile 또는
server hard gate가 실행을 막은 경우다. `requirement_pending`과 `blocked`에서는 최종 선택 모델을
꾸며내지 않는다.

`profile_superseded`는 신규 실행이 predecessor를 해소할 때만 사용한다. 정상 cutover 전에
predecessor snapshot을 고정한 in-flight run에는 이 reason을 합성해 중단하지 않는다.

Exact enum은 다음과 같다.

```text
strategy_resolution_reason:
  requested_active | economic_admission_skipped | feature_disabled |
  emergency_disabled | profile_missing | profile_stale | profile_disabled |
  profile_expired | profile_superseded | strategy_out_of_scope |
  activation_requirement_source_unavailable | credential_policy_unavailable |
  operational_evidence_correction_pending | canary_usage_correction_pending |
  worker_incompatible | rollback_policy_selected |
  strategy_contract_unresolved | rollback_target_invalid | benchmark_isolated |
  no_safe_path

requirement_source:
  runtime_judge | local_classifier | not_required | unavailable

activation_requirement_source_mode:
  judge_only | approved_learner

requirement_evaluation_scope:
  current_execution | accepted_decision_cache | none

model_selection_source:
  server_capability_selector | stored_default | stored_fallback

routing_reuse_source:
  none | accepted_decision_cache

execution_mode:
  deployed | test | policy_preview | benchmark
```

`not_required`는 effective V2 안에서 economic admission으로 요구 판정을 의도적으로 생략한 상태다.
V2 off, profile/requirement source/scope/credential policy 또는 Worker 부적격에는 V2 source enum을
합성하지 않는다.
`unavailable`은 effective V2가 Judge/local contract를 얻으려 했지만 호출·파싱·contract validation이 실패한
상태다. Accepted decision cache 재사용은 원 decision의 `requirement_source`를 보존하고
`requirement_evaluation_scope=accepted_decision_cache`, `routing_reuse_source=accepted_decision_cache`,
`judge_attempt_count=0`을 기록한다. 이는 현재 실행의 Judge 호출을 뜻하지 않으며 UI도 "이전 결정 재사용"으로
표시한다. `not_required`와 `unavailable`은 `requirement_evaluation_scope=none`이다. Cache 추천이 있어도
최종 선택은 current gate와 selector를 다시 통과하며 selection source와 reuse source를 각각 기록한다.

`deployed`와 일반 `test`는 current activation profile이 있어야 V2를 실행한다. `policy_preview`는
server-known hard gate와 이미 존재하는 current local/cache 정보만 평가하고 부족하면
`preview_status=requirement_pending`을 반환한다. `benchmark`는 논리 `platform routing benchmark operator`
권한과 target workflow/deployment의 current `execute` 권한을 모두 가진 actor가 explicit
environment/organization scope, proposed profile, 명시적 budget과 완전한 manifest를 actor/request-bound
idempotent command로 제출한 격리 harness에서만 billable Judge/provider 호출을 허용한다. Server는
`actor_platform_authorization_fence_revision`, `actor_organization_authorization_fence_revision`과 exact
target의 `workflow_resource_authorization_fence_revision | deployment_resource_authorization_fence_revision`을
canonical command와 immutable run intent에 결합한다. Client/workload는 이를 제출하거나 덮어쓰지 않는다.

Admission transaction은 platform actor authorization fence -> actor organization authorization fence ->
target resource authorization fence -> receipt/run intent/audit/첫 `benchmark_dispatch_intent` 순서로 잠그고
current role/scope/`execute` 권한과 bound revisions를 commit 직전에 재검증한다. Membership/principal/
team-role/resource grant mutation은 같은 fence를 exclusive하게 잠그고 revision을 증가시킨다. Revoke winner
뒤 admission은 resource-hiding permission denial과 run/receipt/success audit/provider I/O zero-write다.
Admission winner만 durable dispatch를 commit한다. Dispatch는 deterministic stage/ordinal/exact model
attempt key, revision, pending/leased/terminal state, DB-clock available/lease time과 fencing token을 가진
durable outbox다. Duplicate API delivery도 current authorization fences를 먼저 재검증한 뒤에만 기존
receipt/run을 반환하며, revoke 뒤 detail을 숨긴다. Pending 또는 만료 dispatch는 recovery dispatcher가
terminal denial 또는 기존 run 진행으로 계속 수렴시킨다. Dispatcher는 CAS로 bounded lease를 claim하고
같은 run/stage/ordinal/model의 unique attempt intent와 ADR-0069 operation을 생성하거나 기존 상태에서
재개한다. Admission commit 뒤 attempt 생성 전 crash는 lease expiry 뒤 재개하고, attempt intent 뒤 crash는
같은 operation으로 재개한다. 각 stage terminal transaction은 다음 stage가 필요하면
next stage/ordinal/model key의 deterministic pending dispatch를 함께 생성하고 마지막 stage면 run과 현재
dispatch를 함께 terminal로 전이한다. Current stage만 terminal이고 non-terminal run에 next dispatch가 없는
committed 상태는 허용하지 않는다. Commit 직후 crash는 이미 생성된 next dispatch에서 재개하고 duplicate
finalizer는 stage/ordinal unique key와 CAS로 같은 outcome에 수렴한다. `provider_started` 또는
outcome-unknown attempt는 재호출하지 않으며 concurrent recovery의 stale fencing write는 zero-write다.

각 recovered attempt는 platform benchmark 권한과 target resource `execute` 권한을 다시 판정하고
current fence revisions가 immutable run intent의 admission revisions와 정확히 같은지 검증한다.
ProviderExecutionCapability는 run authorization identity와 exact platform/organization/target-resource
revisions를 binding한다. Run 도중 revision refresh는 금지한다. Provider-start transaction은 같은
authorization fences를 shared-lock하고 current role/scope/permission, run-bound revisions와 capability
binding을 다시 검증한 뒤 current credential·egress·budget durable admission을 commit하고 provider를
호출한다. Permission mutation은 같은
fence의 exclusive lock을 사용한다. Revoke winner 뒤 해당 attempt와 이후 attempt의 provider I/O는 0회이고
run/dispatch는 terminal denied로 종결한다. Start winner가 먼저 commit한 exact attempt만 완료하며 다음 attempt도 같은 run-bound revisions를
다시 확인한다. 다른 organization actor나 같은 command ID의 다른
target/profile/budget/manifest/actor는 `model_routing.benchmark_command_conflict` 또는 resource-hiding
permission denial과 success write/I/O zero로 닫는다. Benchmark는 diagnostic provenance로만
`effective_strategy_id=capability_routing_v2`와 `strategy_resolution_reason=benchmark_isolated`을 기록할
수 있지만 proposed profile을 production-effective로 만들지 않으며, 이 결과를 production policy, learner,
accepted cache, activation 또는 workflow selection에 재사용하지 않는다. 외부 부수효과도 변경하지 않는다.
현재 platform benchmark actor/API가 없으므로 일반 product API에서 billable benchmark를 제공하지 않는다.

`worker_runtime_capability_revision`은 실제 delivery를 소비한 Worker process의 code-owned build/runtime
identity에서 server-side로 계산한다. Client나 producer가 task payload에 넣은 값을 신뢰하거나 current
consumer 확인 없이 enqueue 시점의 revision으로 호환성을 판정하지 않는다.

기존 `decision_source`는 구형 Client의 additive 호환 projection으로만 유지한다. V2 canonical
source를 하나의 문자열로 축약하거나 legacy preview의 `matched_rule | default_model |
fallback_model`을 Requirement Judge source로 재해석하지 않는다.

#### Requirement Judge internal wire contract

V2 canonical `task_intent`는 existing candidate `task_type` 문자열을 그대로 신뢰하지 않는다.

| Legacy input | V2 canonical value |
| --- | --- |
| missing, empty, unknown 자유 문자열 | `unspecified` |
| `classify` | `classify` |
| `extract` | `extract` |
| `summarize` | `transform` |
| `generate` | `generate` |
| `reason` | `unspecified` |

`reason`의 난이도는 Requirement Judge의 bounded 축으로 판정한다. Node title, domain-specific
`node_task`와 prompt keyword로 intent를 추측하거나 canonical 값을 영속하지 않는다.

Routing application은 Client가 보낸 actor/owner 값이 아니라 trusted execution context에서 내부
`execution_data_scope_kind`를 결정한다.

```text
authenticated_subject | anonymous_public_only
```

`authenticated_subject`는 current execution subject 기준 Knowledge/source permission을 적용한다.
Execution subject가 없는 public·webhook·schedule·API·system 실행은 ADR-0018의
`anonymous_public_only`이며 active public Collection/KB와 source-managed public exposure approval만
사용한다. Owner, deployment creator, audit actor, `user_id` 또는 credential principal을 synthetic
subject로 만들지 않는다. 향후 service account mode는 별도 Accepted 계약과 immutable deployment
binding 없이는 이 enum에 추가하지 않는다. 이 내부 kind와 subject ID, 거부된 resource detail은
public decision projection에 포함하지 않는다.

Requirement Judge의 model-visible request body에는 다음 safe input만 포함한다.

- bounded `request_feature`
- server-derived `structural_facts`
- raw chunk/document content가 없는 `rag_context`

Provider 호출 전에 routing application이 server-owned immutable invocation envelope에
`judge_contract_version`과 `rubric_version`을 고정한다. 이 metadata는 Client 입력이나 모델 응답에서
받지 않으며 파싱된 bounded result의 provenance와 결합한다.

`rag_context`는 ADR-0071의 별도 `purpose=query_embedding` policy/capability와 Knowledge 권한 retrieval이
완료된 뒤 생성한 bounded safe projection이다. Judge/main-generation/candidate policy는 query embedding
권한을 대신하지 않으며 query embedding policy/capability 실패 시 Judge 호출이나 stored-model 전환으로
우회하지 않는다.

이 projection도 무조건 외부로 보낼 수 있는 공개 데이터가 아니다. 위 execution data scope와
ADR-0071 query-embedding 경계를 통과한 bounded safe facts만 포함한다. 호출 전에 ADR-0064 deployment
policy에서 server-derived한 credential principal, exact Judge model policy, provider lifecycle과
organization data egress 정책을 검증하고 승인된 provider에만 일시적으로 전달한다.
Public·schedule·system actor를 credential principal로 승격하거나 owner credential을 fallback하지 않는다.
Request/response 원문은 durable trace, audit, cache, label과 Git report에 저장하지 않는다.

정상 응답은 다음 bounded object다.

~~~json
{
  "task_complexity": 2,
  "decision_impact": 1,
  "evidence_synthesis": 3,
  "confidence": 0.86,
  "ambiguity_flags": [],
  "reason_codes": ["broad_context_synthesis"]
}
~~~

모델 ID, 후보 목록, 가격, credential, provider private state, policy ID 또는 unknown field를
반환하면 contract failure다. 모델이 `judge_contract_version` 또는 `rubric_version`을 echo하거나 다른
값을 주장해도 unknown field로 거부하며 server-owned envelope 값을 덮어쓰지 않는다. Secondary Judge는
첫 Judge가 계약 응답에 성공했지만 confidence가 versioned policy 기준보다 낮을 때만 동일 schema로
최대 한 번 호출하고 budget, attempt count와 adjudication 여부를 남긴다. 첫 Judge가
`provider_started` 뒤 `outcome_unknown`으로 종결되면 같은 routing decision의 Judge retry와 secondary
호출은 0회다. 별도로 versioning된 current-valid non-V2 stored safe path만 독립 work-model
capability·budget·egress admission과 durable intent 뒤에 사용할 수 있다.

#### Activation and benchmark command boundary

Target command는 다음 actor와 scope를 분리한다. 구체 REST path와 persistence schema는 activation
governance 후속 이슈에서 확정하며, 현재 organization management API에 임시로 추가하지 않는다.

Profile propose request는 server-known immutable artifact를 가리키는 requirement source manifest를
포함한다.

```text
requirement_source_mode: judge_only | approved_learner
requirement_source_version: opaque immutable version
judge_policy_version: opaque exact policy | null
judge_contract_version: opaque exact contract | null
rubric_version: opaque exact rubric | null
approved_learner_version: opaque exact version | null
approved_learner_requirement_contract_version: opaque version | null
learner_judge_fallback: not_applicable | disabled | exact_judge_policy
```

`judge_only`에서는 exact Judge policy/contract/rubric이 필수이고 learner 관련 필드는 `null`,
`learner_judge_fallback=not_applicable`이어야 한다. `approved_learner`에서는 exact learner와 requirement
contract가 필수이며 Judge fallback을 허용할 때만 exact current Judge policy/contract/rubric을 함께
고정한다. 각 exact Judge/learner source reference는 server가 읽은 immutable source identity와
`requirement_source_lifecycle_revision`을 포함해야 하고 state가 `active`일 때만 profile을 제안할 수
있다. Client/profile author가 revision/state를 주장하지 못한다. Source revoke/retire는 authoritative
lifecycle fence를 증가시키며 terminal source를 in-place 재활성화하지 않는다. Candidate gate 통과나
learner 발행 이벤트는 이 manifest를 변경하지 않는다. Locked holdout과 limited-canary evidence는 exact
manifest에서 실제 활성화한 source branch를 포함해야 한다.
`learner_judge_fallback=disabled`이면 learner branch만 검증하고, `exact_judge_policy`이면 learner와
해당 exact Judge branch를 모두 요구한다. Server는 branch kind와 exact source version에서 opaque
`requirement_source_branch_id`와 required branch set/digest를 파생한다. Client, profile author와 evidence
producer는 branch를 추가·제거하거나 version을 덮어쓸 수 없다. Holdout artifact와 requirement 판정을 수행한 canary event는 branch/version별로 분리하고 required branch
하나라도 누락·혼합되면 promotion을 거부한다. Economic admission의 `requirement_source=not_required`는
server-derived reserved cohort이며 branch ID/version은 `null`이다. 이 표본은 branch gate를 충족시키지 않지만
전용 aggregate와 전체 비용·실패·지연·품질·high-risk 분모에 포함한다.

| Command | Actor | Preconditions | Result |
| --- | --- | --- | --- |
| strategy register/retire | 검토된 코드 릴리스 | immutable contract와 compatibility 검증 | registry version 추가 또는 폐기. Production 활성화는 변경하지 않음 |
| `rollout_family_create` | 권한 있는 `platform routing operator` | actor/request-bound command, environment, canonical immutable activation domain, assignment algorithm/unit contract와 server-allowed bucket count. Raw seed 입력 금지 | Guard lock 아래 domain overlap을 재검증하고 CSPRNG seed, immutable family/domain/initial assignment contract/revision 0 registry row, receipt와 canonical audit를 원자 생성. Same request replay는 기존 family, conflict는 zero-write |
| `rollout_assignment_contract_issue` | 권한 있는 `platform routing operator` | actor/request-bound command, exact existing family/domain, expected family·assignment-registry revision, server-allowed algorithm/unit/count. Raw seed 입력 금지 | Existing family row 다음 family 생성 때 만든 registry row를 잠그고 CAS해 새 CSPRNG seed의 immutable contract, 증가한 registry revision, receipt와 `model_routing.assignment_contract.issued` audit를 원자 생성. Missing registry는 state conflict이며 임의 생성 금지. 기존 profile/contract 불변. Exact replay만 기존 contract 반환. Command ID conflict와 stale/concurrent state conflict, audit 실패는 전체 zero-write |
| activation profile propose | `platform routing operator` 후보 작성자 | immutable requirement source manifest와 server-derived exact source lifecycle revisions/current-active state, existing rollout family/activation domain, server-issued canary assignment contract reference와 bucket range, locked holdout 이전 threshold별 `promotion_only` 또는 `hard_stop` 분류·적용 aggregate/cohort, canary와 exact current-valid non-V2 rollback policy 사전 등록 | immutable `proposed` profile 생성, V2 실행 불가. Raw assignment seed와 caller-provided source revision/state 입력 금지 |
| `activation_holdout_evaluate` | profile 작성자·production 승인자와 분리된 `platform routing holdout operator`; 등록 evaluator/recovery dispatcher/finalizer workload | immutable holdout operator principal, proposed profile/revision, exact source manifest와 server-derived required branch set/digest, `purpose=activation_holdout` dataset ID/version/hash, evaluator·metric·threshold contract, budget·credential·egress policy | Operator principal을 run/artifact에 고정하고 run/receipt/audit와 required branch별 deterministic pending dispatch intent를 한 transaction에 생성. 모든 intent commit 뒤에만 I/O, 각 최초·복구 attempt에서 권한·등록·lifecycle 재검증. Branch crash는 기존 pending intent로 복구하고 모든 intent terminal 뒤에만 `valid/invalid/incomplete/denied` artifact publication |
| limited canary start | 작성자와 다른 `platform routing operator` | current guard/profile expected revision과 expected domain generation/role revision, exact source/valid holdout, credential·Worker·rollback, immutable cadence/schedule contract. Remediation이면 비어 있는 canary role과 exact current-valid `non_v2_policy` production route | `proposed -> limited_canary`, generation, `canary_successor`, sequence 0 current/sequence 1 next schedule, required-branch·reserved `not_required`·overall empty aggregates, source-instance watermarks, `canary_usage_validity_epoch=0`/pending count 0, receipt/audit를 한 transaction에 생성. 전체 commit 뒤에만 capability 발급. Evidence 초기화 실패 또는 unsafe remediation route는 전체 zero-write |
| `record_canary_evidence` | `platform routing evidence system` workload identity | exact profile-bound generation, manifest source kind/opaque instance와 event ID, server-derived authoritative time/cohort/nullable branch-version, metric/event contract와 redacted payload digest. Usage-derived kind는 terminal ADR-0069 operation 필수 | Operation ID는 comparison digest와 generation/operation/metric/event canonical sample unique key, usage revision/contribution은 별도 projection에 저장한다. Cross-source duplicate operation은 zero-write다. Open interval은 event/projection/receipt와 cohort+overall aggregate를 원자 commit한다. Direct kind와 hard-stop first crossing은 generation block/audit를 함께 기록한다. Sealed target은 late reconciliation로 전달하고 usage revision 변경은 별도 usage-correction command가 처리한다 |
| `advance_canary_source_watermark` | exact source instance에 등록된 authoritative canary source adapter workload | profile/generation/target window ID, source kind/opaque instance/contract, monotonic barrier·position, observed-through, expected watermark revision, terminal append receipt/barrier proof | Immutable schedule/target-window source watermark lock 아래 monotonicity와 receipt coverage를 재검증하고 watermark/receipt/redacted audit 원자 commit. Eventless authoritative scan 허용, sealed old-window request와 client/provider callback/seal의 직접 갱신 금지 |
| `seal_canary_evidence_window` | `platform routing evidence system` workload identity | profile/generation, expected schedule revision, current/next sequence·cutoff, aggregate digest, source watermarks, expected canary usage epoch와 pending count 0 | Schedule/pointer/current-next/aggregate/watermark/usage-validity lock 아래 snapshot과 branch·`not_required`·overall late/usage correction revision 0, deterministic tail의 branch·`not_required`·overall aggregates와 watermarks, pointer/schedule revision을 원자 commit한다. Pending correction, 일부 초기화 실패와 concurrent loser는 전체 zero-write |
| `close_canary_evidence_generation` | `platform routing evidence lifecycle system` workload identity | profile revision/generation, expected schedule revision, server-derived exact drain revision/digest, terminal pinned-attempt watermark/digest, source-barrier coverage digest, current usage epoch와 pending count 0 | Canary start가 만든 server-owned drain summary를 admission/finalizer/watermark/correction이 같은 transaction에서 갱신한다. Close는 profile/role -> schedule -> drain summary -> usage validity -> generation만 잠그고 개별 operation/projection을 역순 획득하지 않는다. Count 0과 final-cutoff coverage가 current일 때 final window를 새 tail 없이 봉인하고 schedule/generation closed, receipt와 audit를 원자 commit한다. Missing/stale/incomplete summary 또는 drain 전에는 `model_routing.canary_evidence_drain_pending`, stale/concurrent close는 `model_routing.canary_generation_close_conflict`와 zero-write다. Close 뒤 새 source event/window/admission은 금지하되 늦은 authoritative correction은 별도 append-only projection과 drain revision으로 처리하고 immutable close receipt의 exact replay를 유지한다 |
| `reconcile_canary_late_evidence` | 최소 권한 `platform routing evidence reconciliation system` workload identity | cutoff 이전 authoritative source event, exact profile/generation과 sealed snapshot ID/revision. Invalidation branch만 expected invalidation revision을 사용하며 correction revision과 set/generation/route owner caller 입력 금지 | Coordinator가 profile/role lock 안에서 state를 파생한다. 미승격은 snapshot을 영구 무효화하며 direct event면 generation도 차단한다. Promotion-set member는 current owner rollback/`blocked_no_safe_path` 또는 historical generation block을 수행한다. Set end 뒤 monitoring snapshot은 immutable base+late-correction hard-stop을 평가해 direct 또는 first crossing 때 exact current/historical generation만 차단한다. Promotion/revoke/correction 경합은 공통 lock 순서로 직렬화 |
| `reconcile_canary_usage_correction` | trusted routing evidence correction workload | ADR-0069 correction이 만든 operation-bound intent, expected target usage revision, generation usage epoch와 server-derived event/state relation. Caller의 aggregate/snapshot/set/route/drain summary 선택 금지 | Provider I/O 없이 operation/projection -> profile/role -> aggregate/correction -> drain summary -> usage validity 순서로 old contribution retract+current contribution add를 수행해 sample count를 유지하고 pending count/drain revision을 같은 transaction에서 갱신한다. Open은 corrected hard-stop, 미승격 sealed는 별도 usage-correction aggregate와 corrected base+late+usage hard-stop generation block 및 promotion effective gate, promoted member는 immutable promotion set/member를 바꾸지 않는 append-only `promotion_evidence_reconciliation`으로 current rollback/blocked 또는 historical block을 기록하고, monitoring은 corrected hard-stop을 상태별 원자 처리한다. Closed generation도 schedule/window/capability를 재개하지 않는 append-only correction projection으로 처리한다. Pending 0 전 seal/promotion/start 금지 |
| production promote | profile author와 holdout operator 모두와 다른 current revision의 독립 `platform routing operator` | actor/command ID, expected profile/family/domain-role revision, exact terminal holdout artifact ID/revision과 bound operator principal, evidence start/end, ordered member snapshot ID/revision/hash/invalidation/usage-correction digest와 generation usage epoch. Commit 직전 actor separation, current profile/role/source/holdout principal/credential/Worker/model/rollback/safety, base+late+usage effective gates와 pending 0 재검증 | 모든 contiguous member/correction/gate가 current일 때 immutable promotion set/member rows와 route/state를 원자 commit한다. 승격 후 usage correction은 set/member를 갱신하지 않고 append-only reconciliation row로 재평가 결과를 기록한다. Missing/gap/cherry-pick/stale/pending은 zero-write. Predecessor가 있을 때만 `superseded` |
| `activation_profile_expire` | `platform routing lifecycle system` workload identity | DB current time >= immutable `valid_until`, current expected profile revision과 optional exact role/generation | `expired` 전이·receipt·redacted audit 원자 commit. Proposed는 role 불변, canary owner는 자기 role/generation만 종료, production owner는 exact current-valid non-V2 rollback route 또는 `blocked_no_safe_path`로 전환. Family reservation 유지 |
| workflow strategy select/opt-out | workflow `deploy` 권한자 | command idempotency key와 current workflow policy expected version, server-derived organization/workflow authorization fence revisions, V2이면 immutable rollout family/domain binding | Organization authorization fence -> workflow resource authorization fence -> policy/receipt 순서로 잠그고 commit 직전 `deploy` 권한·active organization/resource scope·strategy/family/domain 유효성을 재검증해 exact profile이 아닌 family/domain policy version, receipt와 canonical audit를 원자 갱신한다 |
| `activation_profile_emergency_disable` | 권한 있는 `platform routing operator` | incident, expected profile/revision과 optional expected role/generation/revision | Roleless `proposed`는 `disabled`/`proposal_cancelled`, receipt/audit만 기록한다. Canary owner는 canary role/generation만 종료하고 production route 유지한다. Production owner는 generation을 종료하고 current-valid rollback route 또는 `blocked_no_safe_path`로 전환한다. Family reservation은 모두 유지한다 |
| `activation_profile_rollback` | 권한 있는 `platform routing operator` | expected profile/role/generation/revision. Production role target이면 exact current-valid non-V2 rollback policy 필수 | Canary owner는 canary role/generation만 종료하고 production route 유지. Production owner는 rollback policy로 원자 route 전환. Rollback이 commit 직전 부적격이면 `model_routing.rollback_target_invalid`와 zero-write |
| `global_routing_kill_enable` | 권한 있는 `platform routing operator` | current expected guard state/revision/epoch, bounded reason과 actor platform authorization fence revision | Guard -> actor auth fence에서 current role/revision을 재검증하고 enabled=true, global epoch/revision 증가, receipt/audit 원자 commit |
| `global_routing_kill_recovery_approval_issue` | enable actor와 다른 current `platform routing recovery approver` | current guard epoch/revision, approver authorization fence revision, remediation digest와 DB-time valid_until | Guard -> approver auth fence -> unique identity 아래 exact replay 또는 approval insert. Approval에 approver fence revision을 결합하고 receipt/audit 원자 commit |
| `global_routing_kill_recovery_approval_revoke` | 권한 있는 `platform routing recovery approver` | exact active approval, expected guard/approval과 revoke actor authorization revisions | Guard -> revoke actor auth fence -> approval에서 current role/revision을 재검증해 revoked/receipt/audit 원자 commit |
| `global_routing_kill_disable` | 권한 있는 `platform routing operator` | current guard, disable actor authorization revision, remediation digest와 exact active/current/non-expired approval ID/revision/approver authorization revision | Guard -> disable actor/approver auth fences(canonical order) -> approval에서 role·revision을 재검증해 consume, enabled=false, epoch/revision과 receipt/audit 원자 commit. Approval/role revoke winner는 zero-write, 기존 capability는 stale |
| isolated benchmark execute | `platform routing benchmark operator` + target workflow/deployment `execute` 권한자 | actor/request-bound command, explicit environment/organization scope, proposed profile, budget, manifest, server-derived platform/organization/target-resource authorization fence revisions와 current provider admission | Admission은 세 authorization fences -> 격리 run/receipt/audit/첫 dispatch를 원자 생성한다. 각 recovered attempt는 current 권한과 revisions가 immutable run binding과 일치할 때만 그 revisions를 capability에 binding하고 provider start가 같은 fences를 공유한다. Run 도중 revision refresh는 금지한다. Permission mutation winner 뒤 run 또는 attempt I/O zero, start winner exact attempt만 완료한다. Lease/fencing recovery로 미시작 attempt를 재개하고 deterministic attempt/ADR-0069 operation은 중복하지 않는다. Production policy·learner·cache·activation·외부 부수효과 write 없음 |

Canonical audit `action`과 `status`는 ADR-0073의 다음 allowlist로 고정한다. 구현은 command 이름이나
state 문자열로 action을 동적 합성하지 않는다. `audit_logs.status`는 canonical audited outcome이다. 승인된 management/terminal publication은 `success`,
보안 breach·승격 근거 무효화는 `failure`로 기록하고 holdout·benchmark의 세부 사업 결과는 safe
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

Blocking evidence는 generic `model_routing.canary_evidence.recorded`를, sealed-snapshot late reconciliation과 promoted-member
usage correction은 generic evidence action이나 `model_routing.activation_profile.disabled`를 추가로 만들지 않고 위 전용
failure action 하나만 기록한다.
Promotion에서 predecessor가 실제 존재할 때만 `model_routing.activation_profile.superseded`를 추가한다. Exact command
replay와 terminal finalizer replay는 기존 receipt/audit를 반환하고 새 audit row를 만들지 않는다. Conflict와
stale loser는 success action zero-write이며 권한 거부에는 기존 `permission.denied`만 허용한다.

활성화 진행 경로는 `proposed -> limited_canary -> production_active`다. 유효 기간 종료는 `expired`,
권한 있는 operator의 disable/rollback은 `disabled`, successor의 production promotion은 predecessor
admission state를 `superseded`로 전이한다. `disabled`와 `expired`는 runtime-safety terminal 상태이고
`superseded`는 신규 실행 admission terminal 상태다. 어느 상태도 다시 활성화하지 않고 변경은 새
`proposed` profile을 요구하지만, 정상 `superseded` predecessor를 고정한 in-flight run은 별도 safety
override가 없는 한 완료할 수 있다. Family activation-domain reservation은 profile terminal 상태와 분리되어 유지되며
별도 Accepted migration/retire 계약 없이 다른 family로 이전하지 않는다. Runtime role은
`canary_successor`와 `production_route`로 나누고 exact owner kind/ID/revision/generation predicate로만 전이한다.

최초 rollout family에는 predecessor가 없을 수 있다. `production_route.owner_kind`는
`profile | non_v2_policy | blocked_no_safe_path`다. Canary start는 canary role과 함께 빈 production
route를 profile의 exact current-valid non-V2 rollback policy인 `non_v2_policy` owner로 초기화한다. Stable
canary target만 successor V2를 사용하고 non-target은 이 canonical route를 해소한다. 최초 production
promotion은 production route를 successor `profile` owner로 전환하고 canary role을 비우며 predecessor
update나 `superseded` audit를 합성하지 않는다.

`activation_holdout_evaluate` admission은 immutable run intent, accepted receipt/audit와 server-derived
required branch 각각의 pending `activation_holdout_dispatch_intent`를 원자 commit한다. 어느 branch intent
초기화 실패도 전체 rollback하고 branch ID/version canonical order가 deterministic evaluator
stage/ordinal/attempt key를 정한다. Provider I/O는 전체 branch manifest commit 뒤에만 허용한다.

Recovery dispatcher는 각 pending/expired branch intent를 claim한다. 최초·복구 attempt마다 run에 고정된
operator의 current 권한, evaluator workload 등록·purpose/source binding,
profile/source/dataset/evaluator/metric contract와 credential·egress·budget lifecycle을 ADR-0069 admission
전에 재검증한다. Revoke/stale이면 현재 intent를 `denied`, 미시작 다른 intents를 `cancelled_due_to_denial`로 닫고 run을
`denial_pending`으로 만든다. 이미 시작된 attempt는 재호출하지 않고 terminal usage를 정산하며 모든
intents가 terminal일 때만 denied artifact를 publish한다. Branch terminal transaction은 다음 intent를 생성하지 않으므로 어느 branch
직후 crash해도 나머지 admission-time intent를 recovery할 수 있다. Concurrent recovery는 branch별 attempt
하나로 수렴하고 `provider_started` 또는 outcome-unknown attempt를 재호출하지 않는다.

Finalizer는 모든 required branch intent가 terminal일 때만 current operator/evaluator/lifecycle을 다시
검증한다. Non-terminal intent가 있으면 publication zero-write로 대기하고 crash를 incomplete로 합성하지
않는다. Provider start 뒤 회수됐으면 usage는 보존한 채 denied로 닫고, current인 경우에만 branch별·전체
metric에서 valid/invalid/incomplete를 판정한다. Artifact, dispatch/run terminal state, receipt/audit는
unique identity 아래 한 transaction에 publish한다. Same actor/request replay는 기존 run/artifact와 동일한
branch manifest를 반환하고 pending dispatch를 취소하지 않는다. 다른 digest는
`model_routing.activation_holdout_command_conflict`와 success write/provider I/O zero-write다.

Canary evidence schedule은 canary start transaction에서만 초기화한다. Immutable cadence/schedule revision,
sequence 0 current와 sequence 1 next window, 두 window와 각 required requirement-source branch 조합의
empty aggregate, current pointer와 두 window별 각 `activation_canary_runtime` source instance의 unobserved
initial watermark를 profile/generation/state/role/
first route/receipt/audit와 함께 commit한다. 하나라도 실패하면 전체 zero-write이며 capability는 commit 뒤에만
발급한다. 각 window는 반개구간 `[window_start_at, cutoff)`를 가진다. Active current만 이미 존재하는 next
window를 가리키고, 그 next는 seal rotation에서 새 tail이 생성되기 전까지 successor가 없을 수 있다. Server는 manifest source contract에서 파생한
`authoritative_event_time`으로 aggregate lock 전에 materialized current/next 중 `assigned_window_id`를
결정한다. Current cutoff 이상이고 next interval 안인 non-blocking event는 current가 아직 seal되지
않았어도 next-window aggregate에만 append한다. Next cutoff 이상인 non-blocking event는
`model_routing.canary_evidence_schedule_not_ready`와 zero-write 후 rotation 뒤 재제출한다. 유효한 trusted
blocking event는 schedule lag만으로 block을 잃지 않는다. 포함 window가 없으면 nullable window와
`schedule_disposition=unassigned_schedule_lag`를 기록하고 aggregate 없이 exact generation
event/receipt/block/audit를 원자 commit한다. Invalid actor/source identity/time은 이 경로를 사용할 수 없다.

Canonical source-event dedupe identity는 canary window와 독립된 environment/family/profile
ID·revision/safety generation, source kind, manifest에 등록된 immutable opaque source instance ID와 그
instance namespace의 immutable source event ID다. Usage-derived event에는 별도로
`(environment, family, profile revision, safety generation, provider usage operation, metric contract,
event kind)` canonical sample unique key를 둬 같은 terminal operation이 여러 source instance/event ID로
관측돼도 sample count에 최대 한 번만 기여하게 한다. Server는 terminal runtime attempt/execution
snapshot에서 `required_branch | not_required` evidence cohort, nullable actual requirement-source branch
ID와 exact source version을 파생하고 producer 입력을 신뢰하지 않는다. Event ID는 instance 안에서만
유일하면 된다. Usage-derived event kind는 terminal ADR-0069 operation이 필수다. Server-derived operation
ID는 comparison digest와 canonical sample key에 포함하고 current usage revision과 safe contribution은
별도 `canary_usage_projection`에 저장한다. Non-usage event의 operation과 sample key는 null이다.
Authoritative event time, evidence cohort, nullable actual branch/version, metric contract version, bounded
event kind, optional operation ID와 redacted payload digest는 canonical source-event comparison digest를
이룬다.

Usage-derived event는 ADR-0069 operation -> canonical usage sample key -> source-event identity 순서로 잠가
terminal/current revision을 확인하고, non-usage event는 source identity부터 조회한다. 기존 source row의
actor/source binding과 digest가 같으면 저장된 event/receipt 및 최초 server-owned schedule
disposition/assigned window를 반환하고 현재 schedule로 재배정하지 않는다. Digest mismatch나 caller의
window/disposition 주장은 `model_routing.canary_evidence_conflict`와 zero-write다. 새 source identity가
이미 다른 source에 고정된 sample key를 재사용하면
`model_routing.canary_evidence_duplicate_operation`과 event/projection/aggregate/receipt/audit
zero-write다. 두 identity가 모두 새일 때만 current schedule에서 assignment를 계산한다.
`schedule_disposition`, nullable assigned window와 observed schedule revision은 identity 밖의 immutable
processing outcome이다. 서로 다른 등록 instance의 같은 event ID는 non-usage 또는 서로 다른 usage sample
key일 때만 별도 event다. 새 assigned usage append는 current operation revision/contribution digest
projection을 event/receipt와 함께 생성하고 sample count를 한 번만 반영한다.
모든 assigned append는 cohort와 overall aggregate를 canonical key 순서로 잠근다. Direct blocking kind이면 threshold와 무관하게 aggregate 뒤
exact generation row를 잠가 source event/receipt, revisions, deterministic direct breach, deny-only
block/epoch와 `model_routing.canary_breach.detected` failure audit를 원자 commit한다. 같은 append가 hard-stop도
넘더라도 aggregate breach나 generic evidence audit를 추가하지 않는다.

Non-blocking event만 post-append immutable `hard_stop` threshold를 판정한다. First crossing이면 aggregate
뒤 exact generation row를 잠가 deterministic aggregate breach와 block/audit를 원자 commit한다.
`promotion_only`는 block을 만들지 않고 crossing 없는 append만 generic evidence success를 기록한다.
Concurrent append/replay는 direct 또는 first crossing 하나로 수렴하고 이미 blocked generation의 후속
terminal evidence는 aggregate에 한 번 반영하되 breach/audit를 반복하지 않는다. Reserved
`not_required`는 overall hard-stop에 포함된다. Sealed direct event도 reconciliation의 모든 지원 branch에서
exact generation block을 포함한다.

Authoritative watermark는 exact source instance에 등록된 adapter workload의
`advance_canary_source_watermark` command만 변경한다. Canonical request는 target window ID, source
binding/contract, monotonic barrier·position, observed-through, expected revision과 terminal
receipt/barrier proof를 포함한다. Eventless interval도 authoritative scan이 cutoff까지 완료된 경우에만
허용한다. Same request는 기존 watermark를 반환하고 다른 payload, stale/regression, 불완전 coverage 또는
seal winner 뒤 old-window request는 command-specific conflict/stale 오류와 zero-write다.

Sealed snapshot은 `seal_canary_evidence_window` command로만 만든다. Request는 profile/generation,
expected schedule revision, pre-registered current/next window ID·sequence, immutable cutoff, expected aggregate
revision map과 그 canonical digest, authoritative source별 expected watermark를 포함한다. Aggregate map key
set은 current required branch set + reserved `not_required`와 정확히 같아야 한다. Request watermark는
expectation일 뿐 authority가 아니며 client는 tail identity/time을 제출하지 않는다. Server는 immutable
cadence에서 `tail_sequence=next_sequence+1`, tail ID와 interval을 파생한다. Transaction은 schedule/pointer와 current/next window를 잠근 뒤 current evidence aggregate row를 reserved
key를 포함한 canonical key 순서로, watermark row를 고정 순서로, generation canary usage validity row를
마지막으로 잠그고 assigned-window membership, exact aggregate revision map/digest, expected usage epoch와
pending count 0을 다시 검증한다. 성공 시 current event와 초기 `snapshot_invalidation_revision=0`을 snapshot ID/revision/hash에 포함하고,
snapshot별 required branch·reserved `not_required`·overall sealed late-correction aggregate와 sealed
usage-correction aggregate를 revision 0으로 초기화한다. 두 correction은 immutable snapshot aggregate/hash와
분리되며 usage correction은 sample count 보존 delta만 저장한다. 새 tail window·required
branch별 및 reserved `not_required` empty aggregate·모든 registered source-instance의 unobserved initial
watermark를 만들며 next를 tail에 연결한 뒤 schedule revision을 증가시키고 pointer를 next로 이동한다. Snapshot, tail, pointer, receipt와 audit는 한
transaction이다. Tail/audit/receipt 초기화 실패는 전부 rollback한다. 같은 actor/request는 기존 snapshot과
exact tail identity를 반환하고 concurrent seal 또는 다른 request 재사용은 zero-write다. Cutoff 이전
append가 먼저 current revision을 바꾸면 seal은 재제출된다. Cutoff 이후 append는 seal 전후 모두 이미
등록된 next window만 변경한다. Same-generation schedule은 current production과 superseded pinned
attempt가 terminal할 때까지 safety-monitoring window를 회전한다. Promotion set end보다 큰 snapshot은
기존 set에 소급 편입하지 않는다.

Production promotion은 profile의 `promotion_evidence_start_sequence=0`부터 request end sequence까지
빠짐없는 ordered sealed snapshot을 immutable `promotion_evidence_set`으로 묶는다. Set identity/hash는
profile/generation, start/end sequence, member별 snapshot ID/revision/hash/invalidation/usage-correction revision,
required branch/version별 base+late+usage effective aggregate, reserved `not_required` effective aggregate, 전체
effective aggregate, evidence contract와 generation canary usage epoch를 포함한다. Transaction은 member snapshot을 sequence
오름차순으로 잠그고 contiguous range, source watermark, required branch별 gate와 `not_required`를 포함한 전체 sample/duration/failure/threshold 및 모든 member의
`snapshot_invalidation_revision=0`, current member usage-correction revision과 generation pending count 0을
검증한다. Non-zero revision은 최신 값을 제출해도 promotion 부적격이다. Missing/gap/duplicate/cherry-pick, branch omission 또는 member revision mismatch는
`canary_evidence_required | stale | canary_usage_correction_pending`과
set/profile/route/receipt/audit zero-write다. 성공 set/member rows는
production promotion과 같은 transaction에 commit한다.

Cutoff 이전 event가 seal 뒤 도착하면 일반 append는 event를 쓰지 않는다. Evidence intake가 immutable
schedule에서 target sealed snapshot을 server-side로 파생하고 `reconcile_canary_late_evidence`로 내부
전달한다. Terminal reconciliation receipt commit 전에는 source에 success를 acknowledge하지 않고 crash 전
receipt가 없으면 같은 source identity/digest로 전체 command를 재시도한다. Request는 source
identity/comparison digest, exact profile/generation과 server-derived sealed snapshot ID/revision을 고정하고
invalidation branch만 expected invalidation revision을 추가한다. Monitoring correction revisions는 request
identity가 아니며 Coordinator가 row lock 안에서 current 값으로 증가시킨다. Coordinator는 environment guard -> profile runtime-safety -> canary/production role을 먼저 잠그고
snapshot sequence, promotion-set membership과 route owner를 server-side로 파생한다.

미승격 `limited_canary` snapshot이면 canary role -> snapshot 순서로 잠근다. Non-blocking late event는
`late_sealed` event, invalidation과 `pre_promotion_snapshot_invalidated` audit를 기록한다. Direct
blocking late event는 snapshot -> generation 순서로 invalidation+block과
`pre_promotion_snapshot_invalidated_generation_blocked` 단일 invalidation audit를 기록한다. Snapshot
aggregate/hash와 profile/role은 불변이고 non-zero invalidation은 영구적인 promotion 부적격이다.

Snapshot이 promotion evidence set member이고 current production owner이면 authoritative rollback policy ->
exact set -> member -> generation 순서로 잠가 current-valid rollback 또는 `blocked_no_safe_path`와
block+disable을 원자 commit한다. 같은 member의 superseded owner이면 historical profile fence -> exact
set -> member -> historical generation에서 block-only를 수행하고 current successor는 zero-write다.

Set end sequence보다 큰 same-generation snapshot은 post-promotion monitoring snapshot이다. Current owner는
production role, historical owner는 historical profile fence 뒤 snapshot -> cohort/overall late-correction
aggregates -> generation 순서로 잠근다. Snapshot/hash/invalidation은 바꾸지 않고 sealed base+correction
fold를 평가한다. Direct event는 threshold와 무관하게, non-blocking correction은 hard-stop first crossing
때 exact current/historical generation block과 `model_routing.canary_breach.detected`를 원자 commit한다. Crossing 없는
correction은 generic evidence success 하나만 기록하며 profile/route/current successor는 zero-write다.
Correction race/replay는 event/revision/breach/audit 하나로 수렴한다.

미승격 terminal profile, set 범위 안인데 member가 아닌 snapshot, 다른 generation/snapshot 또는 지원
state/snapshot relation 밖의 request는 `model_routing.canary_evidence_stale`과 전체 zero-write다.

같은 profile/role lock을 promotion도 사용한다. Reconciliation winner 뒤 promotion은 member invalidation
mismatch로 실패하고, promotion winner 뒤 같은 reconciliation command는 promoted branch를 즉시 수행하므로
late event가 미처리되는 중간 상태가 없다. Same source identity/digest replay는 기존 terminal outcome을
반환하고 다른 digest는 `model_routing.canary_evidence_conflict`다. Audit/receipt 실패는 event,
correction/invalidation, safety/lifecycle write를 모두 rollback한다.

ADR-0069 usage correction은 operation-bound canary projection마다 deterministic correction intent와 증가한
generation `canary_usage_validity_epoch`을 원자 기록한다. Generation이 closed여도 authoritative correction을
거부하지 않으며 닫힌 schedule/window/admission을 재개하지 않는 append-only projection만 만든다. Pending count는 intent 수가 아니라
authoritative revision보다 뒤처진 distinct projection 수이며 current -> pending에서만 증가한다. 이미 pending인
projection의 후속 correction은 active target만 전진시킨다. Trusted
`reconcile_canary_usage_correction`은 projection lock에서 latest target으로 coalesce하고 이전 intent를
`superseded`, latest를 `applied`로 terminal 처리한다. Old contribution을 한 번 retract하고 current
contribution을 한 번 add해 sample count를 유지하며 projection이 current가 될 때 pending count를 한 번만
감소시킨다. Open correction은 corrected hard-stop, 미승격 sealed correction은 별도 usage-correction aggregate와
corrected base+late+usage hard-stop first crossing의 exact generation block 및 promotion effective gate,
consumed promotion member는 current owner
rollback/`blocked_no_safe_path` 또는 historical generation block, monitoring correction은 corrected hard-stop을
상태별 처리한다. Closed correction도 immutable final snapshot 또는 promotion/monitoring relation을 같은
state-aware branch로 재평가하고 필요한 block/rollback만 적용하며 capability를 재발급하지 않는다. Pending
동안 seal/promotion/provider start는 `model_routing.canary_usage_correction_pending`으로 fail-closed한다. Event identity, immutable snapshot과 current
successor는 보존하고 breach/invalidation은 기존 canonical audit만 exactly-once 사용한다.

Direct blocking event는 open·sealed·schedule-lag 상태 모두 threshold와 무관하게, non-blocking event는
open aggregate 또는 sealed monitoring base+correction hard-stop first crossing에서 exact affected generation
block/epoch와 canonical failure audit를 원자 기록한다. Assigned/correction 경로는 event/receipt와 aggregate
revision도 같은 transaction에 포함한다. Profile state/role/route는 zero-write이며 별도 operator가 lifecycle을
종료한다. Promotion-set member late reconciliation만 current owner의 block+disable+rollback/blocked route 또는
historical block-only를 같은 transaction에 수행한다.
Promotion request는 expected profile/family/domain-role revision, evidence start/end sequence와 ordered
member snapshot ID/revision/hash/invalidation-revision digest를 제출한다. Transaction은 모든 contiguous
member와 required branch gate 및 reserved `not_required`를 포함한 overall gate뿐 아니라 DB time/profile state, exact role/generation, source/holdout,
credential policy, actual Worker readiness, global model eligibility, rollback과 global/scoped safety를
commit 직전에 재검증한다. Evidence invalidation 또는 어느 eligibility
회수든 먼저 commit되면 production cutover, receipt와 success audit를 zero-write한다.

등록된 holdout evaluator의 immutable `activation_holdout` artifact는 locked holdout branch만 충족하며
canary sample/watermark/block으로 집계하지 않는다. `record_canary_evidence`는 trusted profile-bound runtime monitor의 `activation_canary_runtime` event만
허용한다. 이 source kind는 같은 generation의 `limited_canary`, current `production_active`와 `superseded`
pinned attempt terminal evidence를 포함한다. Role release/expiry/disable/rollback/safety block은 provider-start capability를 즉시 invalid/blocked로
만들고 evidence lifecycle은 `draining`으로 둔다. Canary start는 server-owned generation drain summary를
초기화하고 pinned attempt/operation admission, terminal finalizer, source watermark/append와 usage correction은
non-terminal/pending count·terminal watermark·source coverage digest·revision을 같은 transaction에서
갱신한다. 새 run admission이 닫힌 뒤에도 summary가 final cutoff의 drain을 증명할 때까지 intake와
rotation을 유지한다. `close_canary_evidence_generation`은 profile/role -> schedule -> drain summary -> usage
validity -> generation 순서만 사용해 개별 operation/projection lock과 역순 cycle을 만들지 않고
schedule/generation을 closed로 commit한다. Close 뒤에는 새 activation source event/window/admission을 만들지
않는다. 이후 authoritative billing correction은 closed schedule을 재개하지 않는 append-only projection과
drain revision으로만 처리한다. 격리 product benchmark는
별도 `product_benchmark` diagnostic namespace만 사용하며 `record_canary_evidence`, activation
aggregate/snapshot 또는 safety block write를 제출하면
`model_routing.canary_evidence_source_ineligible`과 zero-write로 거부한다. Learner, optimizer와
scheduler도 activation evidence producer가 아니며 profile state, revision 또는 command receipt를 직접
변경할 수 없다. Emergency command는 활성화, threshold 완화 또는 scope 확대에 사용할 수 없고, 권한
있는 `platform routing operator`의 idempotent command만 V2 disable 또는 승인 rollback을 수행한다.
`proposed -> production_active` 직접 전이와 canary 시작/production 승격을 하나의 command로 합치는 것도
거부한다. Activation/profile mutation은 command identity, expected revision과 독립 승인자를 검증한다.
DB current time이 `valid_until`을 지나면 resolver가 scheduler 상태와 무관하게 V2를 즉시 차단한다.
Scheduler는 profile row를 직접 변경하지 않고 deterministic expiry command만 제출한다. Expiry는 모든 current non-terminal profile에 state/receipt/audit를 기록하지만 exact owner
ID/revision/role/generation만 전이한다. Proposed는 role을 바꾸지 않고 canary expiry는 canary role/generation만
종료한다. Production expiry는 current-valid rollback route 또는 `blocked_no_safe_path`로 전환하며 family
reservation은 유지한다.

Activation/profile receipt는 command ID, actor principal과 canonical request hash를 저장한다. Hash는
command kind, environment/family/profile, target state, activation domain/scope digest, bounded reason과
rollback policy version을 기본으로 포함한다. 사람 주체의 platform command는 current
`actor_platform_authorization_fence_revision`, service command는 immutable workload authorization
identity/revision을 추가한다. Evidence generation close는 expected schedule revision, server-derived
exact drain revision/digest, terminal pinned-attempt watermark/digest, source-barrier coverage digest와 current
canary usage epoch/pending count를 추가한다. Workflow policy command는 server-derived organization/workflow
authorization fence revisions를 추가한다. Benchmark command는 server-derived actor platform/organization과
exact target workflow/deployment resource authorization fence revisions를 추가한다. Family create는
assignment algorithm/unit contract와 bucket
count, domain command는 expected profile/family/domain-role revision, canary start는 expected domain
generation, expiry는 optional expected role/generation, promotion은 evidence start/end sequence, ordered member snapshot ID/revision/hash/invalidation-revision/
usage-correction-revision digest와 expected generation canary usage epoch를 추가한다. Global kill command는
expected guard state/revision/epoch, command actor platform authorization fence revision과 disable의 exact
approval ID/revision/approver authorization fence revision을 포함한다. Approval issue/revoke는 bound guard
epoch, command actor authorization fence revision, approval ID/expected revision, valid_until과 remediation
digest를 포함한다. Raw seed, incident/remediation payload와 tenant data는
제외한다. Same command ID·actor·hash replay만 기존 receipt를 반환한다. Different actor/request conflict는
command registry가 family create, assignment contract issue, global kill, global kill recovery approval,
holdout, canary evidence, watermark, seal, evidence close, promotion reconciliation, benchmark와 workflow policy에 각각
`model_routing.rollout_family_command_conflict`, `model_routing.assignment_contract_command_conflict`,
`model_routing.global_kill_command_conflict`, `model_routing.global_kill_recovery_approval_command_conflict`,
`model_routing.activation_holdout_command_conflict`, `model_routing.canary_evidence_conflict`,
`model_routing.canary_evidence_duplicate_operation`,
`model_routing.canary_watermark_command_conflict`, `model_routing.canary_snapshot_seal_conflict`,
`model_routing.canary_generation_close_conflict`, `model_routing.promotion_evidence_reconciliation_conflict`,
`model_routing.benchmark_command_conflict`, `model_routing.workflow_policy_version_conflict`를 반환한다. Assignment contract issue의
expected family/assignment-registry revision stale/missing/concurrent winner는
`model_routing.assignment_contract_state_conflict`다. Recovery approval issue/revoke의 expected row state/revision stale,
concurrent issue/revoke/consume winner와 revoke-after-consume는
`model_routing.global_kill_recovery_approval_state_conflict`다. Kill disable에서 approval이
missing/expired/revoked/consumed이거나 actor 독립성, remediation digest, approver current role 또는
approval-bound/current authorization fence가 유효하지 않으면
`model_routing.global_kill_recovery_approval_required`다. 모든 경우 mutation/receipt/success-audit는
zero-write다. 나머지 profile lifecycle
command만 `model_routing.activation_command_conflict`를 사용하며 모든 mutation/receipt/success audit는 zero-write다.
성공 mutation의 profile state/revision, command idempotency receipt와 canonical audit는 같은 DB transaction에서
commit하며 audit recorder 또는 receipt 기록이 실패하면 모든 mutation write를 rollback하고 성공 응답을 반환하지
않는다. 권한 거부에서는 profile state/revision/receipt가 zero-write이며, 기존 보안 정책의 transaction-bound safe
`permission.denied` audit는 기록할 수 있다. 이는 성공 command receipt나 profile mutation audit가 아니다. 비동기
notification outbox는 canonical audit를 대체하지 않는다. 현재 RBAC에 platform actor가 없으므로 관리 API/UI가
구현되기 전에는 일반 organization owner/manager 요청을 위 zero-write 규칙으로 거부한다.

Rollout family create와 active domain reservation/role을 획득·교체·해제하는 canary start, production
promote, emergency disable/rollback, expiry/supersede 및 activation-domain migration은 단일
`RoutingActivationCoordinator`가 처리한다. Coordinator는 외부 I/O 전에 짧은 DB transaction을 열고
사전 생성된 environment activation guard row, command actor authorization fence, family/profile/domain-role
row를 이 순서로 잠근다. Actor fence absent/revision 0은 권한 거부이며 일반 command가 lazy create하지 않는다.
새 target principal/scope의 최초 role grant만 deterministic revision 0 unique insert와 grant/revision 1을 같은
transaction에 commit한다. Missing guard를 command가 임의 생성하지 않고
`model_routing.activation_guard_unavailable`로 fail-closed한다. 모든 사람 주체의 platform-privileged command는
canonical request에 `actor_platform_authorization_fence_revision`을 포함하고 `environment guard -> actor
platform authorization fence -> family/profile/domain-role 및 command별 resource` 순서로 잠근 뒤 current
role/revision을 commit 직전에 재검증한다. Role grant/revoke·mapping·principal lifecycle도 같은 fence를
증가시키며 revoke winner 뒤 command는 permission denial과 success write zero-write다. Evidence/expiry/recovery
같은 service command는 별도 immutable workload authorization identity/revision을 사용한다. Guard는 overlap/global kill 직렬화
fence이며 global kill command만 expected guard state/revision/epoch CAS를 사용한다. Family/domain
command는 guard lock 아래 current kill/overlap을 다시 읽고 expected family/profile/domain-role revision만
검증한다. Production promote는 contiguous promotion evidence set, ordered member invalidation revisions,
generation safety와 blocking breach뿐 아니라 operator 권한, DB time/profile state·revision, exact role
owner/generation, source/holdout, credential policy, actual Worker readiness, global model eligibility와 exact
rollback policy를 commit 직전에 재검증한다. Sealed snapshot pre-cutoff late evidence reconciliation은 environment -> profile runtime-safety -> role을 먼저
잠근다. 미승격이면 canary role -> snapshot에서 invalidation을 수행하고 direct event면 generation도 차단한다.
Promotion-set member의 current production은 production role -> authoritative rollback policy -> evidence set ->
member -> generation에서 disable/rollback 또는 `blocked_no_safe_path`를, superseded historical은 historical
profile fence -> evidence set -> member -> historical generation에서 block-only를 수행한다. Set end 뒤
monitoring snapshot은 current/historical fence -> snapshot -> late-correction aggregates -> generation 순서로
hard-stop을 평가하고 profile/route를 바꾸지 않은 채 exact generation만 조건부 차단한다. Promotion과 같은 profile/role
fence로 직렬화해 어느 winner에서도 event를 누락하지 않고 current successor를 보존한다. 겹치는 경합은
winner 하나만 commit하고 loser는 `model_routing.activation_domain_conflict`와 zero-write다. Non-overlap
command는 unrelated domain mutation 때문에 guard revision이 바뀌었다는 이유로 stale 처리하지 않고 lock
아래 순차 검증 후 각각 commit한다. Bounded lock timeout도 conflict/zero-write이며 holdout, benchmark,
provider, notification과 runtime request 동안에는 lock을 유지하지 않는다.

Environment 전체 kill switch는 `global_routing_kill_epoch`로 관리한다. Domain guard는 monotonic
profile-bound safety generation을 할당하고 profile disable/rollback/expiry와 blocking evidence는
generation별 scoped epoch와 deny-only block으로 분리한다. Profile은 immutable
`profile_definition_revision`, 신규 run 선택용 `admission_lifecycle_revision`, in-flight attempt 안전용
`runtime_safety_revision`을 분리한다. ProviderExecutionCapability는 exact profile ID/definition
revision/`valid_until`, runtime safety revision, global/generation epoch, generation canary usage validity
epoch, organization authorization, exact organization/provider/purpose data-egress policy, decision에 사용한
exact Judge/learner source identity와 `requirement_source_lifecycle_revision`, credential policy/credential,
model/provider lifecycle과 rollback policy revision을 binding한다. Accepted cache origin도 current source
lifecycle을 재검증하고, `not_required`만 explicit empty source fence를 사용한다. Operational evidence가
선택에 기여하면 server-derived exact aggregate validity row identity/revision set과
`model_evidence_fence_set_digest`도 binding하고 미사용은 explicit empty evidence set이다. Admission
lifecycle revision은 capability safety identity에 포함하지 않는다. Judge, primary와 fallback은 각각
current scope/lifecycle/egress, capability·budget admission과 durable intent를 독립 수행한다.

`provider_started` transaction은 environment guard -> activation profile runtime-safety/`valid_until` ->
optional benchmark actor platform authorization fence -> organization authorization fence -> optional benchmark
target workflow/deployment resource authorization fence -> organization/provider/purpose data-egress policy
fence -> bound requirement-source lifecycle fence(canonical source identity order) -> credential
policy/credential -> model/provider lifecycle -> rollback policy -> bound operational evidence validity
rows(canonical order) -> optional snapshot -> generation canary usage validity -> generation safety -> attempt
순으로 shared fence를 획득한다. DB current time이 pinned profile `valid_until` 전인지와 capability-bound
definition/runtime-safety revision, exact authorization/organization data-egress/requirement-source revisions,
source current-active state와 selected provider/purpose 허용 상태, model evidence fence set/revisions, canary
usage epoch/pending count와 state를 다시 검증한다. Bound source가 missing/stale/revoked/retired면 해당 V2
attempt는 `model_routing.activation_requirement_source_unavailable`과 provider-start/I/O zero-write다. 다른
profile-bound source를 사용하려면 새 requirement evaluation과 attempt capability를 발급해야 한다.
정상 promotion으로 admission state/revision만 `superseded`가 된 predecessor는 pre-admitted pinned run의
attempt를 차단하지 않는다. 만료를 발견하면 sweeper state와 무관하게 `provider_started`와 provider I/O를
zero-write한다. Pinned profile의 exact current-valid non-V2 rollback을 독립 admission한 뒤 사용하거나
canonical `model_routing.activation_profile_expired` typed failure로 닫는다. Strategy resolution의 safe
reason `profile_expired`와 이 외부/worker failure code는 별도 필드다. Provider-start transaction은 profile state를 암묵적으로 변경하지 않는다. 각 revoke/disable
mutation과 organization data-egress 또는 requirement-source lifecycle 변경도 자신이 변경하는 동일
authoritative fence를 exclusive하게 획득하고 revision을 증가시킨다. Requirement source는 immutable
definition/profile을 덮어쓰지 않고 terminal lifecycle 뒤 새 version/profile로만 대체한다. Organization egress fence는 ADR-0064/0067의 provider
catalog/transport `egress_revision`과 별도다. Missing/stale/revoked/denied policy는
`model_routing.data_egress_policy_denied`와 provider-start/payload/network I/O zero-write로 닫고 다른 scope나
provider detail을 숨긴다. 첫 policy mutation은 egress management service만 nullable-first CAS로 deny revision
0 row와 요청 state/revision 1을 원자 commit하며 runtime/start command는 missing row를 만들지 않는다. Global kill enable은 environment guard를 잠근다. Recovery approval issue는 guard -> approver authorization fence -> deterministic command/approval unique
identity를, revoke/disable은 guard -> canonical actor/approver authorization fences -> exact approval row를
잠근다. Open assigned direct event와 non-blocking
hard-stop crossing은 environment shared fence 뒤 affected aggregate -> generation, unassigned schedule-lag
direct event는 generation만 잠근다. Sealed reconciliation은 state relation 뒤 unconsumed direct의
snapshot -> generation, promotion-member current의 rollback policy -> set -> member -> generation,
historical member의 set -> member -> generation 또는 monitoring snapshot -> correction aggregates ->
generation 순서를 사용한다. Provider I/O는 start commit 뒤에만 시작한다. 어떤
revoke/kill/breach/evidence correction이 먼저 commit하면 대기한 start는 `provider_started`와 provider I/O
zero-write로 닫고,
start가 먼저 commit한 attempt만 이미 승인된 호출로 취급한다. Bounded timeout과 serialization retry
소진은 fail-closed한다.
Breach가 난 scoped generation의 deny-only block은 in-place로 해제하지 않는다. 기존 blocked generation이
current `production_route` profile owner라면 권한 있는 operator가 emergency disable/rollback으로 그
profile을 닫고 route를 exact current-valid `non_v2_policy` owner로 먼저 전환한다. Route가
`blocked_no_safe_path`이거나 여전히 blocked profile owner이면 remediation canary start는
generation/state/role/evidence/receipt/audit zero-write다. 기존 blocked generation이 canary successor role을
소유하면 operator가 그 role도 먼저 terminal하게 닫아야 한다. Remediation을 반영한 새 immutable profile이
holdout과 독립 canary-start 승인을 통과하면 Coordinator는 commit 안에서 비어 있는 canary role과 canonical
current-valid non-V2 production route를 다시 확인하고 새 generation을 limited-canary 전용으로 연다. 기존
block은 유지하고 target만 새 generation, non-target은 canonical non-V2 route를 사용한다. 새 generation의
canary evidence와 별도 production 승격 뒤에만 그 generation을 production role로 전환한다. Blocking
evidence ingestion은 immutable breach event/receipt, exact generation의 scoped block/epoch와 redacted
security audit만 같은 transaction에 기록하고 profile/role/rollback route는 변경하지 않는다.

Environment guard는 `global_routing_kill_enabled`, monotonic `global_routing_kill_epoch`, revision과 current
enable actor principal을 소유한다. `(principal, platform role scope)`별
`platform_authorization_fence_revision`은 effective role grant set의 monotonic revision이며 role
grant/revoke, role-permission mapping과 principal lifecycle mutation이 같은 row를 exclusive하게 잠그고
증가시킨다. `platform routing operator`의 전용 kill command만 guard를 변경하고 enable도 guard -> actor
authorization fence에서 current role/revision을 검증한 뒤 즉시 fail-safe다. Disable용 approval은 enable actor와 다른
current recovery approver가 deterministic actor/request ID, guard epoch, bounded remediation digest, DB-time
`valid_until`과 issue 시점 approver authorization fence revision에 결합해 발급한다. Stored state는 `active | revoked | consumed`이고 expiry는
DB clock으로 파생한다.

Approval issue는 environment guard -> approver authorization fence -> command/approval unique identity,
revoke는 guard -> revoke actor authorization fence -> approval, disable은 guard -> disable actor/approval
approver authorization fences(canonical order) -> approval 순서로 잠근다. Issue는 current approver
role/revision을 approval에 결합하고, revoke는 current actor role/revision을, disable은 command actor와
approval-bound approver의 current role/fence revisions, guard/approval/remediation digest와 actor 독립성을
재검증한다. 각 command는 approval state, epoch/revision, receipt와 canonical audit를 한 transaction에
commit한다. 만료 approval은 stored state를
암묵적으로 변경하지 않고 `model_routing.global_kill_recovery_approval_required`와 disable success
write zero-write다. Approval 또는 approver-role revoke winner이면 disable은 approval-required/stale authorization으로
zero-write한다. Disable winner 뒤 approval revoke는
`model_routing.global_kill_recovery_approval_state_conflict`, role revoke는 다음 command부터 적용된다. Same request replay는 기존
receipt를 반환하고 stale/권한 회수/audit·receipt 실패는 전체 zero-write다. Enabled 동안 V2 capability
발급은 0회이고 disable 뒤에도 이전 capability를 되살리지 않으며 모든 current gate를 다시 통과한 새
capability만 새 global epoch에 binding한다.

Workflow strategy select/opt-out은 activation/profile command와 별도의 workflow policy CAS boundary다. Policy
row가 없을 때만 `expected_version=null`을 허용해 CAS insert하며 opt-out도 row 삭제가 아니라 명시적 새 policy
version으로 저장한다. V2 policy는 exact activation profile이 아니라 immutable `rollout_family_id`와
`activation_domain_digest`를 저장한다. Idempotency key는 workflow, expected version, 요청
strategy/version, V2이면 family/domain binding 또는 opt-out의 canonical request hash에 결합한다. 같은
key·request 재시도도 organization/workflow authorization fences와 current `deploy` permission을 먼저
재검증한 뒤에만 기존 receipt를 반환한다. 권한 회수 뒤 replay는 receipt/policy detail을 숨긴 denial이고 key가
같지만 request 또는 bound authorization revision이 다르면 conflict와 zero-write다.
같은 expected version을 사용한 동시 요청은 한 요청만 성공하고 loser는 conflict로 닫는다. Canonical request는 server-derived organization authorization fence revision과 workflow resource authorization
fence revision을 포함한다. Transaction은 organization authorization fence -> workflow resource authorization
fence -> policy/receipt 순서로 잠근다. Commit 직전에 bound revisions, current `deploy` permission, active
organization/resource scope와 workflow/deployment가 bound domain에 속하고 actor가 해당 family/domain을
선택할 수 있는지 재검증한다. Membership/team/resource grant mutation은 같은 fence를 잠그고 revision을
증가시킨다. Workflow create transaction은 resource fence revision 0을, membership/grant management는
actor-organization fence를 미리 만들며 strategy command는 missing fence를 lazy create하지 않고
fail-closed한다. 새 run은 policy가 고정한 family/domain에서 stable assignment target과 canonical
`production_route`를 함께 해소한다. Target이면 successor exact profile
ID/definition/runtime-safety revision/generation을, non-target이면 route owner kind에 따라 exact production
profile/generation, non-V2 policy/revision 또는 `blocked_no_safe_path` failure를 execution snapshot에
고정한다. 최초 rollout의 route 부재를 successor/default로 보완하지 않는다. Promotion 뒤 policy mutation이나
재배포 없이 production route가 successor로 바뀌므로 새 run만 successor를 선택하며 in-flight run은 별도
safety override가 없는 한 pinned predecessor/route snapshot을 유지한다. 다른 family/domain 전환은 새 CAS command를 요구한다. 성공 policy write,
command receipt와 canonical audit는 같은 transaction에서 commit한다. Stale command, commit-time 권한·scope
거부와 audit/receipt 실패는 workflow policy와 success audit zero-write다. 권한 거부에서는 기존 보안 정책의
transaction-bound safe `permission.denied` audit만 허용하며 resource 존재나 다른 scope의 policy detail을
노출하지 않는다.

#### Target typed errors

구체 HTTP status와 public projection은 구현 이슈에서 기존 error policy와 함께 확정한다. 내부
canonical reason은 최소 다음 상태를 구분하며 raw candidate, credential 또는 다른 scope 정보를
message에 포함하지 않는다.

| Code | 의미 |
| --- | --- |
| `model_routing.strategy_not_available` | unknown, retired 또는 미지원 strategy/version |
| `model_routing.activation_profile_required` | V2에 current activation profile이 없음 |
| `model_routing.activation_profile_stale` | profile contract가 current selector/catalog/pricing과 다름 |
| `model_routing.activation_profile_expired` | DB current time이 pinned profile `valid_until` 이상이고 exact current-valid non-V2 rollback도 없어 provider-start를 fail-closed함. Strategy resolution reason `profile_expired`와 구분 |
| `model_routing.activation_requirement_source_unavailable` | profile에 고정된 Judge-only 또는 approved learner source와 명시적 fallback을 현재 사용할 수 없음 |
| `model_routing.activation_guard_unavailable` | environment registry의 사전 생성 activation guard가 없어 domain mutation을 안전하게 직렬화할 수 없음 |
| `model_routing.activation_domain_conflict` | 별도 command가 같은 environment의 동일하거나 겹치는 family/domain reservation과 충돌하거나 activation guard lock을 bounded 시간 안에 얻지 못함. Unique constraint 형태와 무관하게 이 코드가 우선함 |
| `model_routing.rollout_family_command_conflict` | 같은 family create command ID가 다른 actor 또는 canonical request에 재사용됨. Exact actor/request/hash replay만 기존 family를 반환하며 domain reservation 충돌에는 사용하지 않음 |
| `model_routing.assignment_contract_command_conflict` | 같은 assignment contract issue command ID가 다른 actor 또는 canonical request에 재사용됨. Exact actor/request replay만 기존 contract를 반환함 |
| `model_routing.assignment_contract_state_conflict` | expected family/assignment-registry revision이 stale하거나 concurrent issue가 먼저 commit함. Contract/revision/receipt/success audit zero-write |
| `model_routing.activation_command_conflict` | profile propose/start/promote/expire/emergency disable/rollback command ID가 다른 actor 또는 canonical request에 재사용됨 |
| `model_routing.activation_holdout_command_conflict` | 같은 holdout command 또는 artifact identity가 다른 profile/source/dataset/evaluator/threshold canonical digest로 재사용됨 |
| `model_routing.global_kill_command_conflict` | 같은 global kill command ID가 다른 actor 또는 canonical request에 재사용됨 |
| `model_routing.global_kill_state_conflict` | expected global kill state/revision/epoch가 stale하거나 동시 command가 먼저 commit함 |
| `model_routing.global_kill_recovery_approval_command_conflict` | 같은 recovery approval issue/revoke command ID가 다른 actor 또는 canonical request에 재사용됨 |
| `model_routing.global_kill_recovery_approval_state_conflict` | Approval issue/revoke의 expected row state/revision stale, concurrent issue/revoke/consume winner 또는 revoke-after-consume. Disable의 approver role/fence 부적격에는 사용하지 않음 |
| `model_routing.global_kill_recovery_approval_required` | active/current/non-expired approval이 없거나 actor 독립성, approval-bound/current approver authorization fence revision·role 또는 remediation digest가 유효하지 않음. Resource detail 비노출 |
| `model_routing.operational_evidence_source_required` | V2 evidence append에 terminal ADR-0069 provider usage operation이 없거나 server-derived binding이 불완전함 |
| `model_routing.operational_evidence_ineligible` | Provider purpose 또는 execution mode가 operational model evidence 계약에 부적격함 |
| `model_routing.operational_evidence_conflict` | 같은 provider usage operation/evidence contract append 또는 correction identity가 다른 safe metric digest로 재사용됨 |
| `model_routing.operational_evidence_correction_pending` | Usage correction 뒤 aggregate rebuild가 완료되지 않았거나 sample usage revision/validity epoch가 stale해 selection·activation 근거로 사용할 수 없음 |
| `model_routing.canary_usage_source_required` | Usage-derived canary event에 terminal ADR-0069 provider usage operation binding이 없거나 server-derived operation/model/purpose가 event와 다름 |
| `model_routing.canary_usage_correction_pending` | Operation correction 뒤 canary projection/aggregate reconciliation이 완료되지 않았거나 generation usage epoch가 stale해 seal·promotion·provider start에 사용할 수 없음 |
| `model_routing.strategy_out_of_scope` | deployment가 승인 scope/stable canary 밖임 |
| `model_routing.credential_policy_required` | Judge 또는 primary/fallback exact model을 승인하는 current V2 credential policy가 없음 |
| `model_routing.data_egress_policy_denied` | Exact Organization/provider/purpose data-egress policy가 missing, stale, revoked 또는 denied여서 payload 외부 전송을 fail-closed함. 다른 scope/provider detail 비노출 |
| `model_routing.worker_runtime_incompatible` | 실제 delivery를 소비한 Worker가 activation profile의 최소 runtime capability revision을 충족하지 못함 |
| `model_routing.activation_approval_required` | Production promotion actor가 profile author 또는 holdout artifact에 고정된 operator principal과 같거나 독립 승인/actor 조건이 current하지 않음 |
| `model_routing.canary_evidence_required` | production 승격에 필요한 current limited-canary 표본·기간·guardrail 근거 미충족 |
| `model_routing.canary_evidence_stale` | promotion request의 ordered member snapshot ID/revision/hash/invalidation-revision digest가 current와 다르거나 member의 invalidation revision이 non-zero임. Non-zero snapshot은 최신 revision을 제출해도 promotion 부적격이다. Reconciliation target이 미승격 terminal profile, promotion-set 범위 안인데 member가 아닌 snapshot, unrelated generation/snapshot 또는 지원 state/snapshot relation 밖이어도 이 오류와 zero-write다. Cutoff 이후 non-blocking event의 open-window revision 증가는 이 오류가 아님 |
| `model_routing.canary_evidence_conflict` | 같은 authoritative source event identity가 다른 evidence cohort/nullable branch-version, authoritative time, metric contract, event kind 또는 redacted payload digest로 재사용되거나 caller가 server-owned disposition/window를 주장함 |
| `model_routing.canary_evidence_duplicate_operation` | Usage-derived event의 generation/operation/metric-contract/event-kind sample key가 이미 다른 source identity에 고정되어 같은 terminal provider operation을 중복 집계하려 함 |
| `model_routing.canary_evidence_schedule_not_ready` | 유효한 non-blocking event의 authoritative time을 포함하는 materialized current/next window가 없어 rotation 뒤 재제출해야 함. Blocking event의 generation 차단에는 사용하지 않음 |
| `model_routing.canary_watermark_command_conflict` | 같은 watermark command ID가 다른 source binding, barrier/position 또는 canonical request에 재사용됨 |
| `model_routing.canary_watermark_stale` | expected watermark revision이 stale하거나 barrier가 regression하고 terminal append coverage가 불완전함 |
| `model_routing.canary_snapshot_seal_conflict` | 같은 seal command ID가 다른 cutoff/revision/watermark request에 재사용되거나 open-window revision이 먼저 변경됨 |
| `model_routing.canary_evidence_drain_pending` | pinned attempt·provider operation/projection·source barrier 또는 usage correction이 final cutoff까지 terminal/materialized되지 않아 generation close 불가 |
| `model_routing.canary_generation_close_conflict` | 같은 close command ID의 다른 request, expected schedule/usage revision stale 또는 append/finalizer/correction concurrent winner로 close 재제출 필요 |
| `model_routing.promotion_evidence_reconciliation_conflict` | 같은 promotion set/member/operation/target revision identity가 다른 safe correction digest로 재사용됨 |
| `model_routing.canary_evidence_source_ineligible` | `activation_holdout`, 격리 `product_benchmark` 또는 등록되지 않은 producer가 canary runtime evidence command를 제출함 |
| `model_routing.benchmark_command_conflict` | 같은 benchmark command ID가 다른 actor, target, profile, budget 또는 manifest canonical request에 재사용됨 |
| `model_routing.workflow_policy_version_conflict` | expected workflow policy version 또는 idempotency key의 canonical request가 current state와 충돌 |
| `model_routing.rollback_target_invalid` | 승인 profile의 non-V2 rollback policy가 없거나 current contract를 통과하지 못함 |
| `model_routing.no_usable_model` | current authorization과 모든 hard gate를 통과한 model이 없음 |
| `model_routing.strategy_contract_unresolved` | legacy row에 immutable contract version이 없어 V1/V2 dynamic routing을 실행할 수 없음 |

Budget unavailable·exceeded와 price unavailable은 기존 ProviderExecutionCapability/Workflow budget의
typed error를 그대로 사용한다. 이를 `no_usable_model`로 축약하거나 다른 Billing Principal의 여유
예산으로 재시도하지 않는다.

#### Operational model evidence identity

Tenant execution에서 파생한 operational evidence의 내부 write/read identity는 승인된 익명 집계
contract가 없는 한 다음 값을 모두 포함한다.

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

API와 Client는 fingerprint 원문 구성 요소나 runtime payload를 제공하지 않는다. Server가 immutable
deployment snapshot에서 identity를 계산한다. Prompt/instruction, variable mapping, output/schema,
Knowledge/RAG configuration 또는 downstream structural contract 변경으로 fingerprint가 달라지면
이전 evidence는 조회 결과와 minimum-quality gate에서 제외한다. 이전 row를 current fingerprint로
update하거나 fallback scope로 읽지 않는다. Public safe projection은 bounded
`model_evidence_version`만 반환한다.

Server는 canonical task intent, bounded requirement 세 축과 output/effect/context risk class를
versioned canonical form으로 결합해 opaque `requirement_evidence_cohort_id`를 파생한다. Cohort가
없거나 다른 표본은 current minimum-quality의 positive evidence로 사용하지 않으며, 별도 Accepted
transfer policy 없이 cross-cohort quality reuse를 허용하지 않는다. Raw requirement 값은 API,
trace와 audit에 노출하지 않는다.

개별 evidence sample append는 product API가 아니라 trusted internal
`record_operational_model_evidence` command다. Canonical source는 ADR-0069의 terminal
`provider_usage_operation_id`이며 server는 immutable operation/current `usage_revision`과 canonical
workflow/node outcome에서 scope, invocation/Loop iteration, model/purpose, task fingerprint와 safe metric을
파생한다. Client, Celery payload와 finalizer 인자는 identity/usage revision을 덮어쓰지 못한다.
ADR-0069 operation이 없거나 terminal binding이 불완전하면
`model_routing.operational_evidence_source_required`와 zero-write다. 허용 work-model
deployed/registered-canary operation만 수용하고 Judge/embedding/summary/test/preview/benchmark는
`model_routing.operational_evidence_ineligible`로 거부한다. Outcome-unknown은 positive quality가 아니다.

Canonical append identity는 `(organization_id, provider_usage_operation_id,
evidence_contract_version)`이다. Initial event는 current usage revision과 safe metric bundle digest를
저장한다. Same identity/digest replay는 기존 event/receipt를 반환하고 sample count를 늘리지 않으며 다른
digest는 `model_routing.operational_evidence_conflict`와 zero-write다. Operation -> sample -> affected
aggregate validity rows lock 아래 event/receipt/delta를 원자 commit하고 provider I/O는 수행하지 않는다.

ADR-0069 authoritative correction은 operation lock 아래 해당 operation의 모든 evidence-contract sample과
affected aggregate validity row를 canonical order로 열거한다. Sample별 deterministic
`operational_evidence_correction_intent`, 각 aggregate의 증가한
`operational_evidence_validity_epoch`과 pending correction set을 같은 Unit of Work에 원자 commit한다.
기존 sample을 update하거나 새 sample로 append하지 않는다. Sample이 아직 없으면 뒤 최초 append가
corrected usage revision을 읽고, append가 먼저 commit했으면 correction이 그 sample을 pending으로 만든다.
Exact replay는 기존 intent/epoch로 수렴하고 같은 target revision의 다른 safe digest는 conflict다.

Trusted `rebuild_operational_evidence_aggregate`는 pending set 전체의 current usage snapshot과 canonical
outcome에서 sample count를 유지한 새 aggregate revision을 expected epoch/pending-set digest CAS로 만들고
pending을 해제한다. Concurrent correction winner는 rebuild를 stale zero-write하고 current epoch 재실행을 요구한다.
`model_evidence_version`은 aggregate revision, validity epoch와 sample usage revision set digest를 결합한다.
Selector가 evidence를 사용하면 server가 exact aggregate validity row identity/revision set을 canonical order로
해소해 `model_evidence_fence_set_digest`를 만들고 selector/cache/activation request와 각
ProviderExecutionCapability에 결합한다. Caller/task payload는 row set을 제출하지 않는다. Profile
propose/holdout/canary start/promotion은 publication/commit 전에, provider start는 같은 validity rows를 잠근 뒤
capability-bound revision/digest를 다시 비교한다. Correction winner 뒤 stale request/start는
`model_routing.operational_evidence_correction_pending`으로 zero-write 또는 provider I/O 전 non-V2/typed
unavailable로 닫는다. Provider-start가 같은 fence에서 먼저 commit한 attempt만 완료할 수 있다.
Pending/mismatched usage revision/stale epoch aggregate는 positive quality·비용 근거로 사용할 수 없고 이미
고정된 evidence snapshot은 새 aggregate로 자동 변경하지 않는다. Raw prompt/output, provider payload,
corrected token/cost 원문과 credential은 event/intent/receipt에 포함하지 않는다.

#### Experiment manifest contract

Activation evidence manifest는 source Git SHA/dirty flag, dataset ID/version/hash와 tuning/holdout 구분,
catalog/pricing/selector/Judge/learner/evaluator version, environment class, redaction/retention reference와
`valid`, `invalid`, `incomplete` 상태를 가진다. Invalid·incomplete·tuning run은 activation 계산에서
제외한다. API와 Git report에는 raw prompt/output, RAG 원문과 credential을 포함하지 않는다.

### Legacy Bootstrap Contract

이 API는 기존 bootstrap 데이터를 조회하는 호환 경로다. 신규 자동 라우팅은 이 artifact를 읽거나
생성을 요구하지 않는다.

| Endpoint | 권한 | 설명 |
| --- | --- | --- |
| GET /workflows/{workflow_id}/llm-nodes/{node_id}/model-routing/bootstrap-preview | write | 현재 node 설정에서 사용할 운영 표본 수와 예상 bootstrap source를 미리 본다. |
| GET /workflows/{workflow_id}/llm-nodes/{node_id}/model-routing/bootstrap | write | 현재 작업 지문과 일치하는 bootstrap safe summary를 조회한다. |
| POST /workflows/{workflow_id}/llm-nodes/{node_id}/model-routing/bootstrap | write | Judge-first artifact를 동기 생성하고 node draft에 bootstrap 참조를 저장한다. |

POST bootstrap request:

~~~json
{
  "task_description": "고객 문의를 JSON으로 분류하고 문서 근거가 없으면 안전하게 답변합니다.",
  "default_model_id": "gpt-4.1-mini",
  "fallback_model_id": "gpt-4.1",
  "expected_graph_hash": "sha256",
  "expected_updated_at": "ISO-8601"
}
~~~

task_description은 10~4,000자다. 기본/대체 모델은
현재 편집자가 실제로 사용할 수 있는 chat model이어야 하며 서로 같을 수 없다. 두 expected
필드는 canonical draft read/write 응답에서 받은 최신 값이어야 한다. Stale 값이면 artifact나
graph를 쓰지 않고 `409 stale_graph`로 닫는다.

Bootstrap response는 id, status, source, task_fingerprint, default_model_id,
fallback_model_id, generation_summary, stale_reason과 canonical `graph_hash`, `updated_at`을 반환한다. `generation_summary`에는
strategy ID, 후보 모델 ID, 참고용 동일 작업 운영 로그 수, `planner_called=false`가 포함된다.
원문 prompt, 입력, 검색 문서 원문, embedding vector는 반환하지 않는다.

### Policy Contract

| Endpoint | 권한 | 설명 |
| --- | --- | --- |
| GET /workflows/{workflow_id}/llm-nodes/{node_id}/model-routing/policy | read | 현재 배포 policy 상태와 최근 운영 실행의 안전한 라우팅 결과를 조회한다. |
| POST /workflows/{workflow_id}/llm-nodes/{node_id}/model-routing/preview | execute | active deployment policy가 현재 입력에 대해 고를 모델을 실행 없이 계산한다. |
| PATCH /workflows/{workflow_id}/llm-nodes/{node_id}/model-routing/policy | deploy | 자동 라우팅 ON/OFF, 점검 주기, 기본/대체 모델을 변경한다. |
| POST /workflows/{workflow_id}/llm-nodes/{node_id}/model-routing/policy/refresh | deploy | 현재 active policy의 재평가 task를 요청한다. |

PATCH policy request:

~~~json
{
  "enabled": true,
  "refresh_every_runs": 20,
  "default_model_id": "gpt-4.1-mini",
  "fallback_model_id": "gpt-4.1"
}
~~~

refresh_every_runs는 5~100이다. enabled 상태에서 기본 모델은 필수이며, 정책 row가 이미
있다면 해당 배포의 execution subject가 두 모델을 실제 사용할 수 있는지도 검사한다.

Policy response는 다음 구조를 사용한다.

~~~json
{
  "enabled": true,
  "status": "active",
  "policy_id": "uuid",
  "bootstrap_id": "uuid",
  "policy_version": "bootstrap-xxxxxxxx",
  "active_policy": {
    "strategy_id": "judge_bootstrap_incremental_v1",
    "default_model_id": "gpt-4.1-mini",
    "fallback_model_id": "gpt-4.1",
    "judge_model_id": "gpt-5.4-mini"
  },
  "learner": {
    "id": "uuid",
    "status": "ready",
    "mode": "local_first",
    "task_fingerprint": "sha256",
    "judged_request_count": 57,
    "pending_count": 2,
    "accepted_count": 57,
    "rejected_count": 4,
    "active_version": 3,
    "recent_evaluation": {
      "judge_match_rate": 0.85,
      "contract_pass_rate": 0.97
    }
  },
  "refresh": {
    "refresh_every_runs": 20,
    "eligible_runs_since_last_refresh": 7,
    "next_refresh_after_runs": 13
  },
  "last_decision": {
    "selected_model_id": "gpt-4.1-mini",
    "fallback_model_id": "gpt-4.1",
    "fallback_used": false,
    "decision_source": "runtime_judge",
    "reason_code": "multi_constraint",
    "reason_label": "여러 조건 종합",
    "created_at": "2026-07-18T09:30:00+00:00"
  }
}
~~~

`judge_model_id`는 `default_model_id`와 독립적으로 결정한다. 기존 정책처럼 두 값이 같으면
runtime은 이를 레거시 결합으로 간주하고, 현재 실행 주체가 사용할 수 있는 동일 provider의
Judge 선호 모델로 교체한다. 명시적으로 다른 Judge가 저장돼 있고 여전히 사용 가능하면 그 값을
유지한다. 선호 Judge가 없을 때만 기본 모델을 Judge로 사용한다.

Current Runtime Judge provider request는 외부 API 계약이 아니라 Workflow Engine 내부 계약이다.
User content에는 bounded `request_feature`, server-derived `structural_facts`, raw chunk/document 원문이
없는 `rag_context`와 `rubric_version`만 보낸다. 후보 모델, 가격, credential과 provider private
state는 보내지 않는다. 정상 응답은 모델 선택이 아닌 세 축의 요구 능력 판정이다.

~~~json
{
  "task_complexity": 2,
  "decision_impact": 1,
  "evidence_synthesis": 3,
  "confidence": 0.86,
  "ambiguity_flags": [],
  "reason_codes": ["broad_context_synthesis"]
}
~~~

각 요구 능력 점수는 0~3 정수이며 unknown field, `selected_model_id` 또는 `candidate_models`가
있으면 응답 전체를 contract failure로 거부한다. 정상 Judge 출력 한도는 768 token이다. 서버는
판정 뒤 current candidate와 structural hard gate를 적용해 최종 모델을 선택한다. 이 current 동작이
V1 strategy ID 아래 있다는 사실은 Target V2 activation을 승인하지 않는다.

`last_decision`은 현재 active deployment에서 해당 LLM 노드가 마지막으로 완료한
실행의 선택 결과다. 패널에서 실제 선택 모델과 판단 사유를 보여주기 위한 값이며,
입력 원문, 프롬프트, RAG 검색 문서 원문은 포함하지 않는다. 실행 이력이 없거나
라우팅 metadata가 없는 경우 `null`이다.

### Routing Preview And Trace Contract

Preview request는 raw 입력을 server 내부에서만 평가하고 response에는 raw input을 넣지 않는다.

~~~json
{
  "inputs": {"message": "결제 영수증을 다시 받고 싶습니다."}
}
~~~

Preview response는 deployment_version, policy_version, decision_source,
selected_model_id, fallback_model_id, default_model_id, configured_fallback_model_id,
matched_rule_id, reason_code, strategy_id, decision_factors, runtime_context을
반환한다. Preview는 운영 요청이 아니므로 Judge를 호출·학습·과금하지 않는다. local artifact가
충분히 확신하면 local 요구 판정에 따른 서버 선택을, 그렇지 않으면 기본 또는 대체 모델을 예상값으로 반환한다.
Preview와 runtime은 같은 `ModelRouter.routing_feature_text()` builder와 side-effect 없는 prompt renderer로 현재 입력, 노드 제목,
작업 설명, 현재 입력으로 렌더링된 system/user/assistant prompt를 구성한다. JSON output schema 지시는 긴 prompt에 밀려나지 않도록
구조 요약과 함께 별도 `OUTPUT_CONTRACT` 섹션 및 독립 길이 예산으로 구성한다. Preview는 실제 RAG retrieval 결과를 만들지 않으므로
동적 RAG signal은 포함하지 않는다. 따라서 preview의 모델은 배포 실행에서 서버 selector가 고를 실제 모델을
확정한 결과가 아니다.
배포 prompt template을 렌더링할 수 없으면 저장된 원문으로 fallback하지 않고
`409 model_routing.prompt_render_failed`로 fail-closed하며 raw template/input을 응답에 포함하지 않는다.

실행 trace의 `llm.model_routing`에는 정책 ID/version, learner ID/version, 선택·대체 모델,
strategy ID, reason code, runtime context, `decision_source`, `judge_called`,
`included_in_routing_learning`을 남긴다. `judge_called`은
**실제 Judge provider 호출을 시도했는지**만 뜻하며, 선택 성공 여부를 뜻하지 않는다.

`judge`는 자동 라우팅 실행마다 다음 안전 요약을 남긴다.

| 필드 | 의미 |
| --- | --- |
| `status` | `selected`, `failed`, `unavailable`, `not_called` 중 하나 |
| `attempted` | Judge provider 호출을 실제로 시도했는지 |
| `model` | 호출한 Judge 모델. 준비 전에 실패하면 없을 수 있음 |
| `candidate_model_count` | Judge가 비교하려던 실행 가능 후보 수 |
| `confidence`, `reason_code`, `reason_short`, `cost` | `status=selected`일 때의 구조화된 요구 판정 근거. `selected`는 Judge 결과를 사용했다는 legacy 상태명이지 Judge가 모델을 선택했다는 뜻이 아니다. `reason_short`은 사전 정의된 코드에 대응하는 짧은 안전 설명이며, Judge 자유 문장은 durable trace에 저장하지 않는다. |
| `usage.latency_ms` | 첫 Judge provider 요청부터 재시도 응답까지의 총 대기 시간. Judge 전용 `llm_usage_logs` 행에도 같은 값이 저장되며 최종 작업 모델의 latency와 합치지 않는다. |
| `error_code` | `failed` 또는 `unavailable`일 때의 안전 오류 코드 |
| `not_called_reason` | local router 선택, 정책 없음, 테스트 preview 등 미호출 이유 |
| `learning_status`, `learning_not_queued_reason` | label이 `pending_contract`로 저장됐는지, 저장하지 못했다면 안전한 실패 코드 |

따라서 `failed`는 “Judge를 호출했지만 결과를 사용할 수 없어 기본 모델로 회귀함”이고,
`not_called`은 “이번 실행에서는 Judge 호출 자체가 없었음”이다. 이전 trace에 이 구조가
없으면 UI는 실패로 추측하지 않고 `Judge 실행 정보 없음`으로 표시한다. 로컬 라우터가
선택한 경우 `decision_factors`에는 learning mode, confidence, 후보 확률의 요약만 남긴다.
원문 prompt/입력, 검색 문서 원문, embedding vector는 반환하거나 저장하지 않는다.
Judge 자유형 설명은 원문 요청이나 개인정보를 반복할 수 있으므로 durable trace에 저장하지 않는다. UI는 `reason_code`와 `reason_short`의 안전한 구조화된 값으로 설명을 만든다. 원문 prompt/input, 검색 문서 원문, 개인식별 정보는 저장하지 않으며, 알 수 없는 reason code는 `judge_reason_unrecognized`로 일반화한다.

Judge 요구 판정을 사용한 실행은 처음에는 `learning_status=pending_contract`로 기록한다. workflow
완료 후 node 성공, schema/downstream 계약, fallback 여부를 확인해 `accepted` 또는
`rejected`와 `learning_outcome_reason`으로 갱신한다. 요청 중 계산한 로컬 예측과 confidence,
학습 표본 거리, 예측 경계 여유도 함께 저장한다. label은 필수 `learner_id`와 선택적
`source_policy_id`를 가지며, 실행 성공·schema·downstream·fallback 결과를 별도 필드로 저장한다.
Celery는 학습기 행을 잠근 뒤 확정 label을 10건 또는 첫 label 확정 후 최대 5분 단위로
직렬화해 학습하고 `learning_processed_at`으로 중복 처리를 막는다.
학습용 vector는 원문을 저장하지 않고 `primary_request`, `dynamic_context`,
`structured_features` 세 그룹을 각각 multilingual E5로 인코딩한 뒤 `0.75 / 0.15 / 0.10`으로
정규화 결합한다. `feature_schema_version=grouped_runtime_variables_v3`가 아닌 기존 artifact는
실행에 사용하지 않고 새 label부터 다시 학습한다.
학습기 응답에는 상태, 건수, 최근 일치율과 활성 버전만 포함한다. feature vector, 원문,
Judge 내부 입력과 분류기 artifact는 API에 반환하지 않는다.
`judged_request_count`는 안전한 3축 요구 수준이 있는 처리 완료 Judge label 수다. Current
`ModelRoutingLearningBatchService`는 terminal `accepted`와 `rejected`를 모두 classifier에 학습하고,
`rejected`를 validation의 `contract_passed=false`와 judged count에도 반영한다. Selected-model count와
accepted decision cache만 `accepted`로 제한한다. 이는 현재 호환 동작이자 알려진 gap이다.

FR-016 Target에서는 모든 terminal `rejected` label을 평가 분모와 최근 계약 통과율에는 한 번 포함하되
accepted training count와 candidate classifier artifact의 가중치에는 반영하지 않는다. 계약 실패뿐 아니라
fallback 확정, execution/schema/downstream 실패와 outcome-unknown도
`learning_outcome_reason=rejected`이면 같은 규칙을 적용한다.
`schema_status=not_applicable`는 schema 검사가 실패한 것이 아니라 수행되지 않은 중립 상태이므로 학습
거절 사유나 schema 평가·통과 집계의 분모에 포함하지 않는다. 반면 선언된 JSON schema가 유효하지 않아
검사를 완료하지 못한 `schema_status=not_evaluated`는 `schema_failed` 학습 거절로 처리하고 schema 평가
분모에는 포함하되 통과 건수에는 포함하지 않는다.

`accepted` label에는 원문이 아닌 `routing_feature_hash`만 저장한다. 이를 이용해 같은 feature의
계약 통과 요구 판정과 서버 선택 추천을 재사용할 수 있으며, 모델 사용 권한이 바뀌었거나 hash key가 없으면
cache hit로 처리하지 않는다.

### Persistence Model

| 테이블 | 역할 |
| --- | --- |
| llm_node_model_routing_bootstraps | 초안 단계부터 존재하는 작업 지문별 Judge-first artifact. 과거 Planner 컬럼은 migration 호환용이며 신규 API에서 사용하지 않는다. |
| llm_node_model_routing_bootstrap_samples | 과거 bootstrap 표본 호환 table. 신규 Judge-first 생성 경로는 row를 만들지 않는다. |
| llm_node_model_routing_policies | 배포/node별 active 및 pending policy, 점검 상태 |
| llm_node_model_routing_policy_updates | 정책 재평가의 trigger, 안전한 입력/출력 요약, 결과 |
| llm_node_model_routing_policy_run_events | 배포 후 운영 실행의 중복 없는 점검 카운터 |
| llm_node_model_routing_performances | 배포/node/model/입력 길이 profile별 운영 성적 |
| llm_node_model_routing_learners | 작업 지문과 Judge/E5 계약별 지속 학습 상태, candidate artifact, 현재 구현의 최근 50건 평가 window. 배포 정책과 독립적으로 재사용한다. ADR-0059/V2 Target의 20건 최소 gate와 동일한 의미로 보지 않는다. |
| llm_node_model_routing_learner_versions | 품질 gate를 통과해 발행한 변경 불가능한 로컬 분류기 버전과 평가 요약. |
| llm_node_model_routing_learning_labels | 필수 learner와 선택적 source policy를 참조하는 Judge 정답, 안전한 vector, 학습 전 예측, 실행/schema/downstream/fallback 결과와 비동기 처리 상태. 원문 prompt/input은 저장하지 않는다. |
| llm_model_routing_global_profiles | Judge-first runtime이 후보 모델의 초기 품질·지연·fallback 사전 정보를 읽는 전역 catalog profile. 실행 주체가 사용할 수 있으면서 명시적 catalog에 등록된 모델만 후보가 된다. |

Test Sidebar 실행은 설정 지문이 같은 검증 learner version을 사용할 수 있으면 로컬 라우터로
요구 능력을 예측하고, 없거나 확신이 낮으면 runtime Judge를 호출한 뒤 서버가 모델을 선택한다. 어느 경우에도 Judge label,
learner count, 운영 정책 카운터, 성적을 변경하지 않는다. Cost Optimizer candidate 비교 실행도
운영 학습과 정책 카운터에 포함하지 않는다.

정책 점검은 완료된 운영 표본이 20건 이상일 때 활성 learner version의 실행 계약도 다시
확인한다. 실행 성공률, schema 통과율, downstream 성공률은 각각 95% 이상이어야 하고 fallback
비율은 5% 이하여야 한다. 기준이 무너지면 learner/version row를 삭제하지 않고 해당 배포
policy의 `active_learner_version_id`만 `null`로 바꾸며, 점검 결과 reason code는
`operational_contract_degraded`다. 이후 runtime은 새 검증 버전이 다시 연결될 때까지 Judge를
우선 사용한다.
## LLM Parameter Recommendation Contract

관련 FR: FR-012

LLM 파라미터 추천은 모델 라우팅과 별도 계약으로 다룬다. 이 API는 현재 draft와 동일한 활성 deployment snapshot의 target LLM node 배포 후 운영 로그를 분석해 `max_tokens`, `temperature`, RAG context 같은 조정 후보를 반환한다. Cost Optimizer 후보 실험은 운영 통계에 섞지 않는다.

예상 service/API entrypoint:

```python
LLMParameterRecommendationService.recommend(
    context: LLMParameterRecommendationContext,
) -> LLMParameterRecommendationResponse
```

후속 HTTP endpoint를 둔다면 다음 경로를 사용한다.

| Method | Path | Description | Auth |
| --- | --- | --- | --- |
| GET | `/api/v1/workflows/{workflow_id}/llm-nodes/{node_id}/cost-optimizer/parameter-recommendations` | target LLM node의 파라미터 추천 목록 조회 | builder 이상 |

`LLMParameterRecommendationContext`는 다음 정보를 포함한다.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `organization_id` | UUID string | yes | workflow, credential, Knowledge scope |
| `user_id` | UUID string | yes | private Knowledge와 credential 사용 가능성 판단 |
| `workflow_id` | UUID string | yes | 대상 workflow |
| `node_id` | string | yes | 대상 LLM node |
| `current_node_options` | object | yes | 현재 target LLM node의 model, prompt, parameters, output_format, Knowledge/RAG 설정 |
| `node_profile` | object | no | 배포 후 운영 node run 기반 sample 수, 성공률, schema/downstream 상태 |
| `usage_profile` | object | no | prompt/completion token, cost, latency의 avg/p50/p95/p99 |
| `rag_profile` | object | no | context token estimate, retrieved chunk count, evidence sufficiency summary |
| `candidate_history` | array | no | 기존 Cost Optimizer candidate 결과 요약 |

`LLMParameterRecommendationResponse`는 다음 정보를 반환한다.

| Field | Type | Description |
| --- | --- | --- |
| `analysis_stage` | `insufficient_logs` \| `recommendations_available` \| `draft_not_deployed` \| `deployment_unavailable` | 추천 가능한 운영 로그와 현재 draft/deployment cohort 상태 |
| `recommendations` | array | 파라미터 추천 목록 |
| `warnings` | array | 추천 불가 또는 적용 주의 사유 |
| `policy_version` | string | 추천 룰셋 버전 |

추천 row shape:

| Field | Type | Description |
| --- | --- | --- |
| `recommendation_type` | `llm_parameter` | 추천 종류 |
| `parameter_key` | string | `max_tokens`, `temperature`, `top_p`, `frequency_penalty`, `rag.top_k`, `rag.retrieved_context_max_chars`, `rag.retrieved_context_compression` 중 하나 |
| `current_value` | any | 현재 설정값 |
| `suggested_value` | any | 추천 후보값 |
| `confidence` | `high` \| `medium` \| `low` | 근거 신뢰도 |
| `risk` | `low` \| `medium` \| `high` | 품질 저하 가능성 |
| `reason` | string | 사용자에게 보여줄 추천 근거 |
| `evidence` | object | safe summary. raw prompt, raw completion, credential, raw chunk content를 포함하지 않는다 |
| `apply_mode` | `experiment_required` | 파라미터 추천은 직접 적용하지 않고 A/B 후보를 만든다 |
| `candidate_patch` | object | 기존 `CostOptimizerCandidateRequest`에 merge 가능한 변경 patch |

`candidate_patch`는 `POST /compare`의 `candidate` shape와 호환되어야 한다.

예:

```json
{
  "recommendation_type": "llm_parameter",
  "parameter_key": "max_tokens",
  "current_value": 4096,
  "suggested_value": 1400,
  "confidence": "high",
  "risk": "low",
  "reason": "최근 배포 후 성공 실행 80회에서 completion token p95가 820이고 길이 잘림 근거가 없습니다.",
  "evidence": {
    "sample_count": 80,
    "completion_tokens_p95": 820,
    "schema_pass_rate": 1.0,
    "downstream_success_rate": 1.0
  },
  "apply_mode": "experiment_required",
  "candidate_patch": {
    "parameters": {
      "max_tokens": 1400
    }
  }
}
```

추천 룰셋은 다음 원천만 사용한다.

| Source | Usage |
| --- | --- |
| `workflow_node_runs` | target LLM node의 status, duration, output 존재 여부, trace metadata |
| `workflow_runs` | 배포 후 운영 실행 여부, 전체 성공/실패 상태 |
| `llm_usage_logs` | prompt/completion token, cost, latency, model, node id |
| canonical `trace_metadata.llm` | finish reason, schema/downstream status, fallback used, output repetition rate, routing safe summary |
| canonical `trace_metadata.rag` | `context_token_estimate`, `retrieved_chunk_count`, `evidence_sufficient` |

추천 profile에는 `workflow_runs.deployment_id IS NOT NULL`인 배포 후 terminal 운영 실행만 포함한다. 대상은 활성 deployment snapshot과 비용/품질 관련 node 설정 fingerprint가 같은 target node다. 배포 전 테스트 실행과 Cost Optimizer compare 실행은 운영 profile에 포함하지 않는다. 성공 usage는 token/cost p95에 사용하고, terminal 실패 node/workflow는 schema/downstream/RAG 품질 실패율에 포함한다.

`max_tokens` 추천은 성공 usage의 `completion_tokens` p95/p99와 현재 `parameters.max_tokens`를 비교한다. `finish_reason` 또는 structured output의 schema signal이 누락되면 confidence를 낮추거나 추천을 만들지 않는다.

`temperature` 추천은 output format, JSON schema, schema 실패율, retry/fallback 추세를 사용한다. JSON/schema/분류/추출 성격의 노드에서 `temperature`가 높고 실패율이 있으면 낮은 후보값을 제안한다.

RAG context 추천은 `prompt_tokens` 중 retrieval context 비중과 RAG trace summary를 사용한다. author prompt를 줄이지 않고 `topK`, `retrievedContextMaxChars`, `retrievedContextCompression`만 후보로 제안한다.

파라미터 추천 API는 current draft를 수정하지 않는다. FR-013에서 사용자가 추천 row를 선택하면 서버가 recommendation id를 다시 검증하고 `candidate_patch`를 current node 설정 복사본에 merge해 모달 내부 빠른 검증을 실행한다.

## Recommendation Inline Verification Contract

관련 FR: FR-013

빠른 검증 endpoint는 추천 조회, 최신 baseline 확정, candidate 실행, deterministic gate, semantic quality judge, usage 합계를 orchestration한다. 프론트가 임의로 candidate patch를 다시 조립해 보내지 않고 recommendation id를 전달하며, 서버는 현재 recommendation service 결과와 대조해 stale 또는 변조된 추천을 차단한다.

### Request

```json
{
  "recommendation_ids": ["max_tokens", "rag.top_k"],
  "baseline_mode": "latest_success"
}
```

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `recommendation_ids` | string[] | yes | 현재 recommendation response에서 사용자가 선택한 row key. 빈 배열은 허용하지 않는다. |
| `baseline_mode` | `latest_success` | yes | target node의 최신 비교 가능한 성공 운영 실행을 서버가 고른다. |
| `recommendation_policy_version` | string | no | modal이 추천 목록을 조회한 시점의 policy version. 현재 서버 version과 다르면 stale로 반환한다. |
| `node_config_fingerprint` | string | no | modal이 추천 목록을 조회한 시점의 target node 설정 fingerprint. 현재 draft와 다르면 stale로 반환한다. |
| `Idempotency-Key` header | string | yes | 더블 클릭·네트워크 재시도로 candidate/judge LLM 호출이 중복되는 것을 막는다. |

서버는 요청 처리 시작 시 exact `baseline_node_run_id`, current node setting fingerprint, recommendation policy version을 고정한다. 이후 더 최신 실행이나 draft 변경이 생겨도 이번 결과의 기준은 바뀌지 않는다.

latest baseline은 다음 필터를 모두 적용한 뒤 `WorkflowNodeRun.started_at DESC` 첫 row다.

- 현재 workflow, node, active deployment
- `WorkflowNodeRun.status=success`
- input/output/usage available
- Cost Optimizer candidate/quality judge 실행 제외
- current draft와 활성 deployment node setting fingerprint 일치

마지막 fingerprint 검사는 current draft와 활성 deployment snapshot 사이에서 수행한다. Baseline 실행은 exact active `deployment_id`로 같은 불변 snapshot 실행임을 판정하며, 보안 마스킹된 `WorkflowNodeRun.process_data.node_options` fingerprint를 추가 필터로 사용하지 않는다.

### Response

```json
{
  "verification_status": "completed",
  "comparison_id": "uuid",
  "candidate_id": "uuid",
  "baseline": {
    "workflow_node_run_id": "workflow-node-run-uuid",
    "label": "최신 비교 가능한 성공 기록",
    "executed_at": "2026-07-11T12:00:00Z",
    "model": "gpt-4.1",
    "metrics": {"total_tokens": 1086, "cost": 0.02937, "latency_ms": 32900}
  },
  "candidate": {
    "status": "success",
    "model": "gpt-4.1-mini",
    "metrics": {"total_tokens": 563, "cost": 0.000332, "latency_ms": 3600}
  },
  "metrics": {
    "cost": {"baseline": 0.02937, "candidate": 0.000332, "delta": -0.029038, "change_rate": -0.9887},
    "latency_ms": {"baseline": 32900, "candidate": 3600, "delta": -29300, "change_rate": -0.8906},
    "total_tokens": {"baseline": 1086, "candidate": 563, "delta": -523, "change_rate": -0.4816}
  },
  "quality_evaluation": {
    "status": "completed",
    "baseline": {"score": 86},
    "candidate": {"score": 82},
    "delta": -4,
    "confidence": "medium",
    "dimensions": {
      "instruction_fulfillment": {"baseline": 88, "candidate": 84, "delta": -4},
      "relevance_completeness": {"baseline": 85, "candidate": 80, "delta": -5},
      "clarity_consistency": {"baseline": 85, "candidate": 82, "delta": -3}
    },
    "safe_summary": "후보 출력의 품질 점수가 기준 출력보다 낮게 평가되었습니다."
  },
  "schema_validation": {"status": "passed", "issues": []},
  "downstream_compatibility": {"status": "compatible", "checked_node_count": 2},
  "incurred_cost": {
    "candidate_execution_cost": 0.000332,
    "quality_judge_cost": 0.00008,
    "total_new_cost": 0.000412,
    "currency": "USD"
  },
  "apply": {"allowed": true, "requires_confirmation": true, "reasons": ["quality_score_decreased"]}
}
```

`verification_status`는 `completed`, `partial`, `failed`, `stale` 중 하나다. candidate 실행이 성공하고 judge만 실패하면 `partial`이며 deterministic 결과와 candidate usage는 유지한다.

freshness 검증에 실패한 `stale` 응답은 candidate 실행과 judge 호출을 시작하지 않는다. 따라서 실행 결과 식별자와 A/B 결과는 `null`이며, 프론트는 이 상태를 completed/partial/failed 결과와 구분해 결과 panel 대신 재조회 안내를 표시해야 한다.

```json
{
  "verification_status": "stale",
  "comparison_id": null,
  "candidate_id": null,
  "baseline": null,
  "candidate": null,
  "metrics": {},
  "quality_evaluation": {
    "status": "unavailable",
    "safe_summary": "추천 설정이 최신 node 설정과 일치하지 않습니다."
  },
  "schema_validation": {"status": "not_applicable", "issues": []},
  "downstream_compatibility": {"state": "unknown"},
  "incurred_cost": {
    "candidate_execution_cost": null,
    "quality_judge_cost": null,
    "total_new_cost": null,
    "currency": "USD"
  },
  "apply": {
    "allowed": false,
    "requires_confirmation": false,
    "reasons": ["recommendation_stale"]
  }
}
```

### Output Quality Judge

품질 평가는 baseline을 정답으로 취급하지 않는 blind pairwise 평가다. 서버는 A/B 순서를 무작위로 바꾸고 응답에서 원래 variant에 다시 매핑한다.

judge 입력은 다음 범위로 제한한다.

- target node의 user-visible 목적과 output contract
- 복원된 동일 input
- baseline output과 candidate output
- JSON schema 또는 RAG evidence summary가 있으면 해당 safe contract

동일 input과 output은 평가에 필요한 user-visible 값만 전달한다. `usage`, 실행 `metadata`, credential·secret 계열 필드와 raw RAG chunk는 Judge payload에서 제거하고, RAG 정보는 허용된 summary 필드만 전달한다.

judge 출력은 0~100 점수, dimension 점수, confidence, safe summary다. raw judge prompt/output은 API response, audit metadata, 일반 trace에 노출하지 않는다. judge 호출은 현재 사용자가 실행 가능한 provider/model credential로 수행하며 usage/cost를 candidate experiment에 `quality_judge` 역할로 연결한다.

응답은 요청한 모든 dimension과 confidence를 포함해야 한다. 필수 값이 없거나 JSON을 해석할 수 없으면 `quality_evaluation.status=unavailable`로 처리하고 점수를 반환하지 않는다. 계약에 없는 추가 dimension은 무시한다. provider 응답을 이미 받은 경우에는 해석 실패와 무관하게 실제 judge usage/cost와 usage log id를 기록하고 반환한다.

schema/downstream 결과는 judge 점수와 별도로 계산한다.

| 조건 | `schema_validation.status` |
| --- | --- |
| text output | `not_applicable` |
| JSON output, schema 없음 | `not_configured` |
| JSON parse/schema 통과 | `passed` |
| JSON parse 또는 schema 실패 | `failed` |

적용 hard block은 candidate 실행 실패, `schema_validation=failed`, `downstream=incompatible`, stale fingerprint다. 품질 점수 감소 또는 confidence low는 hard block이 아니라 `requires_confirmation=true`와 reason code로 반환한다.

빠른 검증은 기존 Cost Optimizer experiment/candidate row를 생성하고 candidate LLM usage를 연결한다. quality judge usage도 별도 역할로 연결해 `incurred_cost.total_new_cost`에 포함한다. baseline cost는 과거 비용이므로 신규 비용 합계에서 제외한다.

같은 `created_by + Idempotency-Key` 요청은 `cost_optimizer_recommendation_verifications`에 저장한다. 완료된 동일 요청은 저장한 safe response를 반환하고, 실행 중인 동일 요청은 중복 candidate/judge 호출 없이 `verification_in_progress`으로 거부한다. 이 레코드에는 raw prompt/completion/RAG chunk를 저장하지 않는다.

`상세 비교 분석하기`는 response의 `comparison_id`와 `candidate_id`로 기존 결과 분석 화면을 연다. 같은 candidate를 다시 실행하지 않는다. `적용하기`는 기존 `PATCH /cost-optimizer/apply`를 exact `comparison_id`와 검증된 candidate settings로 호출한다.

### Actual Provider Verification Contract

`scripts/verify_model_router_actual.py`는 제품 HTTP API가 아니라 FR-011 라우터 정책을 실제 provider 호출로 검증하는 운영/개발용 스크립트다. 이 스크립트는 다음 계약을 따른다.

| Option | Description |
| --- | --- |
| `--provider auto` | 현재 organization/user가 실행 가능한 provider preset을 OpenAI, Anthropic, Google 순서로 탐색한다. |
| `--provider openai\|anthropic\|google` | 지정 provider preset만 사용한다. 해당 credential/model relation/use 권한이 없으면 provider 호출 전에 실패한다. |
| `--cheap-model`, `--mid-model`, `--high-model` | preset의 실행 대상 모델을 명시적으로 덮어쓴다. 세 모델은 같은 provider여야 한다. |
| `--judge-model` | LLM judge에 사용할 모델을 덮어쓴다. 실행 가능하면 실행 대상 provider와 달라도 허용한다. |
| `--dry-run` | provider 호출 없이 credential/model relation/use 권한과 모델 해석만 검증한다. |

기본 provider preset은 다음 의미를 가진다.

| Provider | Cheap | Mid | High | Judge |
| --- | --- | --- | --- | --- |
| OpenAI | `gpt-4o-mini` | `gpt-4.1-mini` | `gpt-4.1` | `gpt-4.1-mini` |
| Anthropic | `claude-haiku-4-5-20251001` | `claude-sonnet-4-5-20250929` | `claude-opus-4-5-20251101` | `claude-sonnet-4-5-20250929` |
| Google | `gemini-2.5-flash-lite` | `gemini-2.5-flash` | `gemini-2.5-pro` | `gemini-2.5-flash` |

Judge 호출은 OpenAI `response_format`에 의존하지 않는다. provider 공통 prompt로 compact JSON을 요청하고, 응답 text에서 JSON object를 파싱한다. 이 방식은 Anthropic/Google만 쓰는 organization에서도 API key와 model relation이 있으면 같은 품질 gate 검증을 수행하기 위한 최소 공통 계약이다.

## Common Path Parameters

| Name | Type | Description |
| --- | --- | --- |
| `workflow_id` | UUID string | Cost Optimizer를 실행할 workflow id |
| `node_id` | string | target LLM node id |

## Baseline Data Sources

관련 FR: FR-002, FR-004

Baseline의 canonical id는 `workflow_node_runs.id`다.

Baseline API는 다음 저장소를 조합해 row와 detail을 만든다.

| Source | Usage |
| --- | --- |
| `workflow_node_runs` | baseline id, target node id/type, node status, node-level latency, node trace metadata |
| `workflow_runs` | workflow run id, 전체 run 상태, 실행 시각, workflow/app/deployment context |
| `llm_usage_logs` | model, prompt tokens, completion tokens, total tokens, cost, LLM latency |
| `trace_payloads` | redaction-safe input/output preview, input 복원 가능 여부, trace 존재 여부 |

`workflow_runs.id`는 baseline의 전체 실행 컨텍스트이고, `workflow_node_runs.id`가 사용자가 선택하는 baseline 식별자다.

Baseline API는 target LLM node의 `workflow_node_runs.status=success`이고 `output_available=true`, `usage_available=true`인 기록만 반환한다. 실패한 node run, output preview가 없는 node run, usage summary가 없는 node run은 baseline 후보에서 제외하고, 실패 원인 분석이나 불완전한 실행 기록 확인은 workflow 실행 로그/trace API에서 다룬다.

Cost Optimizer의 B 후보 실행은 일반 workflow 실행 로그와 같은 `workflow_runs`/`workflow_node_runs`에 저장되지만, A baseline 후보로 다시 선택되면 안 된다. 따라서 baseline API는 `llm_usage_logs.cost_optimizer_candidate_id`가 있거나 `cost_optimizer_candidates.candidate_workflow_run_id`로 연결된 run을 제외한다.

`trace_payloads` retention, redaction, 저장 누락으로 target LLM node input을 복원할 수 없는 경우에도 baseline row는 목록에 포함한다. 다만 response는 `input_available=false`, `compare_available=false`를 반환하고, compare API는 해당 baseline으로 B 후보 실행을 시작하지 않는다.

## Persistence Model

관련 FR: FR-005, FR-006, FR-008, FR-009

Cost Optimizer 비교 실행은 기존 run/usage/trace 테이블을 원천으로 사용하되, A/B 테스트 세션과 후보 실행을 묶기 위해 전용 테이블을 추가한다.

### `cost_optimizer_experiments`

하나의 A/B 테스트 세션을 나타낸다. 사용자가 특정 workflow의 특정 LLM node에서 baseline을 선택해 A/B 테스트 workspace를 시작하면 생성된다. 같은 baseline을 사용하더라도 사용자가 나중에 다시 A/B 테스트를 시작하면 기존 experiment를 재사용하지 않고 새 experiment를 생성한다.

주요 필드:

| Field | Type | Description |
| --- | --- | --- |
| `id` | UUID | experiment id. compare/apply response의 상위 식별자다. |
| `organization_id` | UUID | organization scope |
| `workflow_id` | UUID | target workflow |
| `app_id` | UUID | workflow가 속한 app |
| `node_id` | string | target LLM node id |
| `baseline_node_run_id` | UUID | A baseline의 `workflow_node_runs.id` |
| `baseline_workflow_run_id` | UUID | A baseline이 속한 `workflow_runs.id` |
| `baseline_node_options` | JSONB | baseline 실행 시점의 LLM node 설정 snapshot |
| `baseline_usage_summary` | JSONB | baseline 비용/토큰/latency safe summary. 비용/토큰 원천은 `llm_usage_logs`이고, latency는 `llm_usage_logs.latency_ms`가 0 또는 누락이면 `workflow_node_runs.duration`을 ms로 환산해 사용한다. |
| `baseline_trace_summary` | JSONB | baseline input/output/retrieval safe summary. raw payload와 secret은 포함하지 않는다. |
| `baseline_downstream_snapshot` | JSONB | baseline 생성/조회 시점의 downstream safe snapshot. compare의 contract check 기준이며 raw payload와 secret은 포함하지 않는다. |
| `usage_summary` | JSONB | 이 experiment에 속한 candidate 실행 비용/토큰/latency 합계 |
| `status` | string | `draft`, `running`, `completed`, `applied`, `failed`, `archived` |
| `created_by` | UUID | experiment 생성 사용자 |
| `created_at`, `updated_at` | datetime | 생성/수정 시각 |
| `retention_expires_at` | datetime nullable | trace metadata retention 정책 기준 experiment/candidate summary 만료 시각 |

### `cost_optimizer_candidates`

하나의 experiment 안에서 실행한 B 후보 하나를 나타낸다. 같은 workspace 안에서 후보 설정을 바꿔 여러 번 실행할 수 있으므로 experiment와 candidate는 1:N 관계다.

주요 필드:

| Field | Type | Description |
| --- | --- | --- |
| `id` | UUID | candidate id |
| `experiment_id` | UUID | `cost_optimizer_experiments.id` |
| `name` | string | UI의 테스트명 |
| `model_id` | string | 결과분석 필터용 후보 기본 모델 id |
| `fallback_model_id` | string nullable | 결과분석 필터용 후보 fallback 모델 id |
| `task_type` | string nullable | 결과분석 필터용 후보 작업 유형 |
| `candidate_settings` | JSONB | B 후보 LLM node 설정 safe snapshot. prompt 본문은 redacted summary로 저장하고, apply 검증용 `_settings_fingerprint`를 포함한다. |
| `candidate_workflow_run_id` | UUID nullable | B 후보 실행으로 생성된 `workflow_runs.id` |
| `candidate_node_run_id` | UUID nullable | B 후보 target node의 `workflow_node_runs.id` |
| `total_cost` | Numeric nullable | 후보 실행 비용 합계. 조회 편의를 위한 summary이며 원천은 `llm_usage_logs`다. |
| `total_tokens` | Integer nullable | 후보 실행 토큰 합계 |
| `latency_ms` | Integer nullable | 후보 target LLM node latency |
| `schema_status` | string nullable | `not_checked`, `pass`, `failed` |
| `downstream_state` | string nullable | `compatible`, `warning`, `incompatible`, `unknown` |
| `usage_summary` | JSONB | model, token, cost, latency summary. 원천은 `llm_usage_logs`다. |
| `schema_validation` | JSONB | output format/schema 검증 결과 |
| `retrieval_summary` | JSONB | B 후보 Knowledge/RAG safe retrieval summary. raw chunk content와 raw source metadata는 포함하지 않는다. |
| `downstream_compatibility` | JSONB | downstream 호환성 판정 결과 |
| `diff_summary` | JSONB | A/B 비용, 토큰, latency, 출력 차이 요약 |
| `status` | string | `draft`, `running`, `success`, `failed`, `schema_failed` |
| `is_applied` | boolean | 현재 draft에 적용된 후보 여부 |
| `applied_at` | datetime nullable | 적용 시각 |
| `applied_by` | UUID nullable | 후보를 현재 draft에 적용한 사용자 |
| `applied_llm_node_version_id` | UUID nullable | 적용으로 생성된 `llm_node_versions.id` |
| `created_at`, `updated_at` | datetime | 생성/수정 시각 |

원천 데이터 관계:

- A baseline의 실행/입출력/비용 원천은 `workflow_node_runs`, `workflow_runs`, `llm_usage_logs`, `trace_payloads`다. Baseline 실행 시간은 LLM usage latency가 없을 수 있으므로 `workflow_node_runs.duration` fallback을 허용한다.
- B candidate의 실제 실행/입출력/비용 원천도 동일한 기존 테이블이다.
- B candidate 실행에서 생성되는 `workflow_runs.id`는 `cost_optimizer_candidates.candidate_workflow_run_id`로 저장한다. 이 값은 candidate 실행 로그가 최신 baseline 후보로 다시 잡히지 않게 하는 1차 식별자다.
- B candidate 실행에서 생성되는 `llm_usage_logs` row는 `cost_optimizer_candidate_id`로 `cost_optimizer_candidates.id`를 직접 참조한다. worker 전파가 지연되거나 누락되어도 Gateway는 `candidate_workflow_run_id` 기준으로 usage row를 candidate에 다시 연결한다.
- `cost_optimizer_experiments`와 `cost_optimizer_candidates`는 원천 로그를 복제하기 위한 테이블이 아니라, baseline과 여러 candidate 실행을 하나의 비교 흐름으로 묶는 메타 저장소다.
- experiment의 `usage_summary`는 해당 experiment에 속한 candidate 비용만 합산한다. 같은 baseline을 기준으로 여러 experiment가 있으면 baseline 누적 비용은 `baseline_node_run_id`가 같은 experiments를 별도로 합산해 계산한다.
- raw prompt, credential 원문, API key, encrypted config, secret payload는 두 테이블에 저장하지 않는다.
- `cost_optimizer_candidates.candidate_settings`의 `system_prompt`, `user_prompt`, `assistant_prompt`는 `{ redacted, present, length }` 형태의 요약만 저장한다. compare/apply 동일 후보 검증은 원문 prompt가 아니라 비가역 `_settings_fingerprint`로 수행한다.
- `baseline_trace_summary`와 `retrieval_summary`에는 `retrieved_chunk_count`, `knowledge_base_count`, `source_summary`, `score_summary`, `hierarchy_fallback` 같은 safe summary만 저장한다. `raw_chunk_content`, `source_metadata`, raw document name/file name 계열 값은 저장하거나 반환하지 않는다. safe summary의 list 값은 최대 20개, string 값은 최대 200자로 제한한다.
- `baseline_downstream_snapshot`에는 target node 이후 직접/간접 소비 노드의 id, type, edge, selector/path 계약, side-effect 여부, topology hash만 저장한다. 노드 실행 raw output, prompt 원문, credential, 외부 전송 payload는 저장하지 않는다.

조회/제약 권장 사항:

| 대상 | 권장 사항 | 목적 |
| --- | --- | --- |
| `cost_optimizer_experiments` | index `(workflow_id, node_id, baseline_node_run_id, created_at DESC)` | 결과분석에서 같은 workflow/node/baseline 기준 최신 실험 재조회 |
| `cost_optimizer_experiments` | index `(organization_id, created_by, created_at DESC)` | 사용자별 실험 이력 조회 |
| `cost_optimizer_candidates` | index `(experiment_id, status, is_applied)` | experiment 상세에서 후보 상태/적용 여부 조회 |
| `cost_optimizer_candidates` | index `(model_id, created_at DESC)` | 모델 기준 결과 분석 필터 |
| `cost_optimizer_candidates` | partial unique `(experiment_id) WHERE is_applied = true` | 하나의 experiment 안에서 적용 후보를 최대 1개로 제한 |

`DESC`는 최신 항목을 먼저 조회하기 위한 내림차순 정렬이다.

저장/보관 정책:

- Cost Optimizer experiment/candidate summary는 trace metadata retention 정책의 `metadata_retention_days`를 따른다.
- 만료 기준은 `cost_optimizer_experiments.retention_expires_at`이다. candidate는 experiment 삭제 cascade로 함께 정리된다.
- `llm_usage_logs.cost_optimizer_candidate_id`는 candidate 삭제 시 `SET NULL`이 되며, usage log 자체 보관은 기존 usage/trace 보관 경계를 따른다.
- candidate 하나가 retry/fallback 등으로 여러 `llm_usage_logs`를 만들 때는 `llm_usage_logs.cost_optimizer_candidate_id` 기준으로 합산한다.
- `baseline_trace_summary`와 `retrieval_summary`의 redaction 기준은 위 safe summary 규칙을 따른다. list 값은 최대 20개, string 값은 최대 200자로 제한한다.

## Common Response Fragments

### DownstreamCompatibility

관련 FR: FR-007

```json
{
  "state": "compatible",
  "label": "검증 가능",
  "message": "baseline 실행 시점의 downstream과 현재 downstream이 호환됩니다.",
  "baseline_downstream_hash": "string",
  "current_downstream_hash": "string",
  "first_consumer_status": "same",
  "contract_check": {
    "status": "pass",
    "checked_node_ids": ["extract-result"],
    "warnings": []
  }
}
```

`state` 값:

- `compatible`: 검증 가능
- `warning`: 주의 필요
- `incompatible`: 검증 불가
- `unknown`: 판정 불가

`contract_check`는 B candidate output이 현재 target LLM node의 직접 소비 노드가 요구하는 입력 selector를 만족하는지 검사한 결과다. 현재 1차 구현은 다음 직접 소비 노드 계약을 검사한다.

contract check의 기준은 baseline 생성/조회 시점에 만든 downstream snapshot이다. Compare API는 `baseline_id`로 baseline row를 복원하고, 해당 row의 `baseline_downstream_snapshot`과 B candidate output을 사용해 계약을 검사한다. 현재 workflow graph는 snapshot과의 topology/hash 비교 및 적용 위험 안내에 사용한다.

신규 baseline 생성/조회 경로는 `baseline_downstream_snapshot`을 만들 수 있어야 한다. 기존 데이터, retention 만료, 또는 graph 복원 불가로 snapshot이 없을 때만 `state=unknown`, `contract_check.status=skipped`, `warnings=["baseline_downstream_snapshot_unavailable"]` 형태의 fallback을 허용한다.

| Node type | 검사 기준 |
| --- | --- |
| `variableExtractionNode` | `source_selector`가 target LLM node를 가리키면 `mappings[].json_path`가 candidate output JSON에 존재해야 한다. |
| `conditionNode` | `cases[].conditions[].variable_selector`가 target LLM node를 가리키면 selector key가 candidate output에 존재해야 한다. |
| `answerNode` | `outputs[].value_selector`가 target LLM node를 가리키면 selector key가 candidate output에 존재해야 한다. |
| `slackPostNode` | `referenced_variables[].value_selector`가 target LLM node를 가리키면 selector key가 candidate output에 존재해야 한다. |

후보 출력의 `text`가 JSON 문자열이면 JSON으로 파싱해 selector/path 존재 여부를 확인한다. 필수 path가 없으면 topology가 같아도 `state=incompatible`, `contract_check.status=failed`로 반환한다.

### LLMNodeUsageSummary

관련 FR: FR-006, FR-009

```json
{
  "model": "gpt-4.1-mini",
  "prompt_tokens": 1200,
  "completion_tokens": 240,
  "total_tokens": 1440,
  "cost": 0.00123,
  "latency_ms": 1840,
  "status": "success"
}
```

### LLMNodeTraceSummary

관련 FR: FR-006

```json
{
  "input_preview": "string",
  "output_preview": "string",
  "messages_preview": [
    {
      "role": "system",
      "content_preview": "string"
    }
  ],
  "rag_summary": {
    "retrieved_chunk_count": 3,
    "knowledge_base_count": 1,
    "source_summary": ["HR 정책 문서"]
  },
  "error_message": null
}
```

Trace summary는 credential 원문, API key, encrypted config, raw secret payload를 포함하지 않는다.

## Endpoint Details

### GET availability

관련 FR: FR-001, FR-010

`GET /api/v1/workflows/{workflow_id}/llm-nodes/{node_id}/cost-optimizer/availability`

대상 node가 Cost Optimizer를 사용할 수 있는지 반환한다.

Response:

```json
{
  "available": true,
  "reason": null,
  "workflow_id": "uuid",
  "node_id": "llm-triage",
  "node_type": "llmNode",
  "permission": {
    "can_compare": true,
    "can_apply": true,
    "required_auth_state": "builder"
  }
}
```

`available=false` 사유 예:

- target node가 존재하지 않음
- target node가 `llmNode`가 아님
- builder 이상 권한 없음
- draft를 조회할 수 없음

### GET latest baseline

관련 FR: FR-002, FR-004, FR-007

`GET /api/v1/workflows/{workflow_id}/llm-nodes/{node_id}/cost-optimizer/baselines/latest`

target LLM node의 가장 최근 비교 가능 실행 로그를 baseline으로 반환한다.
반환 대상은 `node_status=success`, `input_available=true`, `output_available=true`, `usage_available=true`인 기록으로 제한한다.

응답 생성 시 Gateway는 baseline row 내부에 `baseline_downstream_snapshot`을 생성해야 한다. API response에는 snapshot 원문을 노출하지 않고 `downstream_compatibility` summary만 반환한다. snapshot은 이후 `POST /compare`에서 B candidate output contract check의 기준으로 사용된다.

Response:

```json
{
  "baseline": {
    "baseline_id": "workflow-node-run-id",
    "baseline_source": "workflow_node_run",
    "source_workflow_node_run_id": "workflow-node-run-id",
    "workflow_run_id": "workflow-run-id",
    "workflow_id": "workflow-id",
    "node_id": "llm-triage",
    "run_started_at": "2026-07-04T00:00:00Z",
    "node_status": "success",
    "model": "gpt-4.1",
    "input_available": true,
    "output_available": true,
    "usage_available": true,
    "trace_available": true,
    "compare_available": true,
    "unavailable_reason": null,
    "input": {},
    "output": {},
    "usage": {
      "model": "gpt-4.1",
      "prompt_tokens": 1200,
      "completion_tokens": 240,
      "total_tokens": 1440,
      "cost": 0.0123,
      "latency_ms": 2100,
      "status": "success"
    },
    "trace": {
      "input_preview": "string",
      "output_preview": "string",
      "messages_preview": [],
      "rag_summary": null,
      "error_message": null
    },
    "downstream_compatibility": {
      "state": "compatible",
      "label": "검증 가능",
      "message": "string"
    }
  }
}
```

### GET baseline list

관련 FR: FR-002, FR-004, FR-007

`GET /api/v1/workflows/{workflow_id}/llm-nodes/{node_id}/cost-optimizer/baselines`

target LLM node가 포함된 실행 로그 목록을 검색/필터/정렬한다.
반환 대상은 `node_status=success`, `output_available=true`, `usage_available=true`인 기록으로 제한한다. `input_available=false`인 row는 목록에 포함하되 `compare_available=false`로 반환한다.

목록 row도 baseline 후보로 선택될 수 있으므로 각 row를 만들 때 downstream snapshot을 생성하거나, 사용자가 해당 row를 baseline으로 선택하는 시점에 동일한 규칙으로 snapshot을 생성해야 한다. 신규 compare 가능 row에서 snapshot 생성 실패가 발생하면 `compare_available=false` 또는 `downstream_compatibility.state=unknown`과 명확한 unavailable reason을 반환한다.

Query:

| Name | Type | Default | Description |
| --- | --- | --- | --- |
| `model` | string | optional | 모델 필터 |
| `q` | string | optional | input/output preview 검색어 |
| `date_from` | ISO datetime | optional | 시작 시각 |
| `date_to` | ISO datetime | optional | 종료 시각 |
| `sort` | string | `started_at_desc` | `started_at_desc`, `cost_desc`, `cost_asc`, `tokens_desc`, `latency_desc` |
| `compare_available` | boolean | optional | input 복원 가능 여부 기준 비교 가능 row 필터 |
| `limit` | integer | 20 | page size |
| `offset` | integer | 0 | offset |

Response:

```json
{
  "items": [
    {
      "baseline_id": "workflow-node-run-id",
      "baseline_source": "workflow_node_run",
      "source_workflow_node_run_id": "workflow-node-run-id",
      "workflow_run_id": "workflow-run-id",
      "run_started_at": "2026-07-04T00:00:00Z",
      "workflow_run_status": "success",
      "node_status": "success",
      "model": "gpt-4.1",
      "cost": 0.0123,
      "total_tokens": 1440,
      "latency_ms": 2100,
      "input_available": true,
      "output_available": true,
      "usage_available": true,
      "trace_available": true,
      "compare_available": true,
      "unavailable_reason": null,
      "input_preview": "string",
      "output_preview": "string",
      "has_trace": true,
      "downstream_compatibility": {
        "state": "compatible",
        "label": "검증 가능",
        "message": "string"
      }
    }
  ],
  "total": 1,
  "limit": 20,
  "offset": 0
}
```

### GET experiments

관련 FR: FR-006, FR-009, FR-010, FR-013

`GET /api/v1/workflows/{workflow_id}/llm-nodes/{node_id}/cost-optimizer/experiments`

같은 workflow, target LLM node 기준으로 저장된 Cost Optimizer experiment와 B candidate 요약을 조회한다. 결과 분석 화면에서 과거 비교 결과를 다시 열거나, 같은 baseline 기준 후보 실행 이력을 확인할 때 사용한다.

Query:

| Name | Type | Description |
| --- | --- | --- |
| `baseline_id` | UUID optional | 특정 A baseline `workflow_node_runs.id` 기준으로 좁힌다. |
| `date_from`, `date_to` | datetime optional | experiment 생성 시각 범위 |
| `created_by` | UUID optional | experiment 생성 사용자 |
| `candidate_status` | string optional | `success`, `failed`, `schema_failed`, `running` |
| `model` | string optional | 후보 기본 모델 id |
| `is_applied` | boolean optional | 현재 draft에 적용된 후보 포함 여부 |
| `schema_status` | string optional | `not_checked`, `pass`, `failed` |
| `downstream_state` | string optional | `compatible`, `warning`, `incompatible`, `unknown` |
| `limit`, `offset` | integer | pagination |

candidate 조건 중 하나 이상을 전달하면 서버는 조건에 맞는 candidate가 있는 experiment만 선택하고, 각 `items[].candidates`에도 같은 조건과 일치하는 candidate만 포함한다. 한 experiment 안의 다른 상태·모델 후보를 함께 반환하지 않는다.

Response:

```json
{
  "total": 1,
  "limit": 20,
  "offset": 0,
  "items": [
    {
      "experiment_id": "uuid",
      "workflow_id": "uuid",
      "app_id": "uuid",
      "node_id": "llm-triage",
      "baseline_node_run_id": "uuid",
      "baseline_workflow_run_id": "uuid",
      "status": "completed",
      "created_by": "uuid",
      "created_at": "2026-07-05T01:30:00+00:00",
      "usage_summary": {
        "total_tokens": 240,
        "cost": 0.0006,
        "quality_judge_cost": 0.00008
      },
      "candidates": [
        {
          "candidate_id": "uuid",
          "name": "비용 절감 후보",
          "status": "success",
          "model_id": "gpt-4.1-mini",
          "fallback_model_id": null,
          "task_type": "generate",
          "total_cost": 0.0006,
          "total_tokens": 240,
          "latency_ms": 1200,
          "schema_status": "pass",
          "downstream_state": "compatible",
          "quality_evaluation": {
            "status": "completed",
            "baseline": {"score": 86},
            "candidate": {"score": 82},
            "delta": -4,
            "dimensions": {},
            "confidence": "medium",
            "safe_summary": "후보 출력의 품질 점수가 기준 출력보다 낮게 평가되었습니다.",
            "judge_cost": 0.00008
          },
          "is_applied": false,
          "created_at": "2026-07-05T01:30:00+00:00"
        }
      ]
    }
  ]
}
```

이 응답은 summary 조회용이다. raw prompt, credential 원문, secret payload는 포함하지 않는다. 상세 trace 원천은 기존 trace/usage 조회 경계에서 권한과 redaction 정책을 거쳐 조회한다.

### GET experiment candidate detail

관련 FR: FR-006, FR-009, FR-010, FR-013

`GET /api/v1/workflows/{workflow_id}/llm-nodes/{node_id}/cost-optimizer/experiments/{experiment_id}/candidates/{candidate_id}`

`comparisonId`와 `candidateId`를 포함한 결과 분석 deep link를 목록 pagination이나 현재 필터와 무관하게 복원한다. 서버는 workflow, node, organization 범위 안에서 experiment와 candidate를 함께 확인하고, builder 이상 권한이 없거나 범위 밖이면 `404 resource.not_found` 또는 기존 권한 오류를 반환한다.

응답은 `GET experiments`의 experiment safe summary에서 `candidates` 목록을 제거하고, 요청한 후보 하나를 `candidate`에 담는다.

```json
{
  "experiment_id": "uuid",
  "workflow_id": "uuid",
  "node_id": "llm-triage",
  "baseline_summary": {
    "baseline_id": "uuid",
    "model": "gpt-4.1",
    "cost": 0.0012,
    "total_tokens": 249,
    "latency_ms": 1600
  },
  "candidate": {
    "candidate_id": "uuid",
    "status": "schema_failed",
    "model_id": "gpt-4.1-mini",
    "quality_evaluation": {
      "status": "completed",
      "baseline": { "score": 86 },
      "candidate": { "score": 82 },
      "confidence": "medium"
    }
  }
}
```

이 endpoint도 목록과 동일한 redaction 경계를 사용한다. candidate settings 원문, credential, raw trace, raw RAG chunk는 추가로 노출하지 않는다.

### POST compare

관련 FR: FR-003, FR-004, FR-005, FR-006, FR-009, FR-010, FR-013

`POST /api/v1/workflows/{workflow_id}/llm-nodes/{node_id}/cost-optimizer/compare`

A baseline input을 사용해 B 후보 설정을 실행한다. A baseline은 재실행하지 않는다. B candidate 실행이 성공하면 Recommendation Inline Verification과 같은 `CostOptimizerOutputQualityService`로 동일 입력의 A/B 출력을 평가한다. candidate 실행이 실패하면 judge를 호출하지 않고 `quality_evaluation.status=unavailable`을 반환한다.

`baseline_id`는 `workflow_node_runs.id`다. 해당 baseline의 target LLM node input을 복원할 수 없으면 API는 B 후보 실행을 시작하지 않고 `400 cost_optimizer.baseline_input_unavailable`을 반환한다.

Compare는 `baseline_id`에 연결된 downstream snapshot을 복원한 뒤 B candidate output에 대해 contract check를 수행한다. snapshot이 있으면 downstream 상태는 snapshot 기반 검사 결과를 우선한다. snapshot이 없으면 신규 데이터 누락으로 보고 서버 로그에 남기며, response는 `state=unknown`, `contract_check.status=skipped`, `warnings=["baseline_downstream_snapshot_unavailable"]`를 반환한다.

Compare request의 후보 설정 필드명은 `candidate`다. Apply request의 후보 설정 필드명은 `candidate_settings`다.

Request:

```json
{
  "baseline_id": "workflow-node-run-id",
  "candidate": {
    "label": "B",
    "model_id": "gpt-4.1-mini",
    "fallback_model_id": "gpt-4.1",
    "task_type": "generate",
    "system_prompt": "string",
    "user_prompt": "string",
    "assistant_prompt": "string",
    "parameters": {
      "max_tokens": 800,
      "temperature": 0.2,
      "top_p": 1,
      "presence_penalty": 0,
      "frequency_penalty": 0,
      "stop": []
    },
    "output_format": {
      "type": "json",
      "schema": {
        "type": "object",
        "properties": {
          "severity": { "type": "string" },
          "approvalRequired": { "type": "boolean" },
          "replyDraft": { "type": "string" }
        },
        "required": ["severity", "approvalRequired", "replyDraft"]
      }
    },
    "knowledge": {
      "knowledge_base_ids": ["knowledge-base-id"],
      "top_k": 5,
      "score_threshold": 0.7,
      "dedupe_retrieved_context": true,
      "retrieved_context_max_chars": 6000,
      "retrieved_context_compression": "off",
      "answer_grounding_check": "basic"
    }
  }
}
```

Candidate request schema:

| Field | Type | Required | Validation |
| --- | --- | --- | --- |
| `baseline_id` | string | yes | target workflow/node scope의 `workflow_node_runs.id`여야 한다. |
| `candidate.label` | string | no | UI 표시용 라벨이다. 기본값은 `B`다. |
| `candidate.model_id` | string | yes | 현재 사용자와 organization scope에서 사용 가능한 model이어야 한다. |
| `candidate.fallback_model_id` | string or null | no | 지정하면 사용 가능한 model이어야 하며 `model_id`와 같으면 invalid candidate다. |
| `candidate.task_type` | `classify`, `extract`, `summarize`, `generate`, `reason` | no | 기본값은 현재 node 설정 또는 `generate`다. 원본 LLM 노드 상세 편집과 같은 값 체계를 사용한다. |
| `candidate.system_prompt` | string | no | 생략하면 현재 node 설정을 보존한다. 세 prompt를 모두 명시하면서 전부 비우면 invalid candidate다. |
| `candidate.user_prompt` | string | no | 변수 참조는 baseline input/upstream output 기준으로 resolve 가능해야 한다. |
| `candidate.assistant_prompt` | string | no | 변수 참조는 baseline input/upstream output 기준으로 resolve 가능해야 한다. |
| `candidate.referenced_variables` | array | no | prompt token을 upstream output selector로 렌더링하기 위한 `{ name, value_selector }` 목록이다. 각 `value_selector`는 최소 `[node_id, output_key]` 형태여야 한다. |
| `candidate.parameters.max_tokens` | number | yes | 1~8192 |
| `candidate.parameters.temperature` | number | yes | 0~2 |
| `candidate.parameters.top_p` | number | no | 0~1 |
| `candidate.parameters.presence_penalty` | number | no | -2~2 |
| `candidate.parameters.frequency_penalty` | number | no | -2~2 |
| `candidate.parameters.stop` | string[] | no | 최대 4개 |
| `candidate.output_format.type` | `text` or `json` | yes | `json`이면 schema 검증을 수행한다. |
| `candidate.output_format.schema` | object | no | 1차 UI는 flat key-type schema를 만든다. API는 valid JSON schema object만 허용하며, `required`에 들어간 field는 `properties`에 정의돼 있어야 한다. |
| `candidate.knowledge.knowledge_base_ids` | string[] | no | 모두 현재 사용자/organization/workflow scope에서 사용 가능해야 한다. |
| `candidate.knowledge.top_k` | number | no | Knowledge/RAG 사용 시 retrieval 개수다. |
| `candidate.knowledge.score_threshold` | number | no | Knowledge/RAG 사용 시 retrieval score threshold다. |
| `candidate.knowledge.dedupe_retrieved_context` | boolean | no | `true`이면 검색된 문서 조각 중 중복 근거를 제거한다. |
| `candidate.knowledge.retrieved_context_max_chars` | number or null | no | 검색으로 주입되는 Knowledge/RAG context의 최대 글자 수다. `null`이면 제한 없음이며 author prompt는 제한 대상이 아니다. |
| `candidate.knowledge.retrieved_context_compression` | `off`, `light`, `strong` | no | 검색 문서 압축 강도다. |
| `candidate.knowledge.answer_grounding_check` | `off`, `basic`, `strict` | no | 답변이 검색 근거로 뒷받침되는지 확인하는 수준이다. 생략 시 기본값은 `basic`이다. |

B candidate가 Knowledge/RAG를 사용하면 compare API는 baseline의 과거 retrieval 결과를 재사용하지 않고, request의 `candidate.knowledge` 설정으로 retrieval을 새로 수행한다. Response는 baseline retrieval summary와 candidate retrieval summary를 구분해 반환해야 한다.

Response:

```json
{
  "comparison_id": "uuid",
  "workflow_id": "uuid",
  "node_id": "llm-triage",
  "baseline": {
    "baseline_id": "workflow-node-run-id",
    "label": "A",
    "settings": {},
    "input": {},
    "output": {},
    "usage": {},
    "trace": {}
  },
  "candidate": {
    "label": "B",
    "candidate_workflow_run_id": "uuid",
    "settings": {},
    "output": {},
    "usage": {
      "model": "gpt-4.1-mini",
      "prompt_tokens": 900,
      "completion_tokens": 180,
      "total_tokens": 1080,
      "cost": 0.0011,
      "cost_unavailable": false,
      "latency_ms": 1600,
      "status": "success"
    },
    "schema_validation": {
      "status": "valid",
      "errors": []
    },
    "trace": {},
    "error_message": null
  },
  "diff": {
    "cost_delta": -0.0112,
    "cost_delta_percent": -91.0,
    "token_delta": -360,
    "latency_delta_ms": -500
  },
  "quality_evaluation": {
    "status": "completed",
    "baseline": {"score": 86},
    "candidate": {"score": 82},
    "delta": -4,
    "confidence": "medium",
    "dimensions": {
      "instruction_fulfillment": {"baseline": 88, "candidate": 84, "delta": -4},
      "relevance_completeness": {"baseline": 85, "candidate": 80, "delta": -5},
      "clarity_consistency": {"baseline": 85, "candidate": 82, "delta": -3}
    },
    "safe_summary": "후보 출력의 품질 점수가 기준 출력보다 낮게 평가되었습니다.",
    "judge_cost": 0.00008
  },
  "downstream_compatibility": {
    "state": "compatible",
    "label": "검증 가능",
    "message": "string"
  }
}
```

비교 실행에서 발생한 LLM call은 `llm_usage_logs`에 기록되어야 한다. 또한 비교 실행 자체도 `comparison_id`로 재조회하거나 추적할 수 있도록 저장한다.

품질 judge usage는 같은 candidate에 `quality_judge` 역할로 연결하고, 품질 평가 safe summary는 `cost_optimizer_candidates.diff_summary.quality_evaluation`에 저장한다. experiment history의 candidate summary는 `quality_evaluation`을 반환해 결과 분석 화면에서 이전 실험을 선택해도 같은 품질 점수 행을 복원할 수 있어야 한다. judge 실패는 candidate 실행 실패로 취급하지 않으며 `quality_evaluation.status=unavailable`과 safe summary를 반환한다.

JSON schema 검증에 실패한 경우에도 HTTP response는 200으로 반환할 수 있다. 이 경우 후보 LLM call은 성공한 것이므로 비용/토큰/시간을 반환하고, `candidate.usage.status` 또는 `candidate.schema_validation.status`를 `schema_failed`로 표시한다. schema 실패 후보는 apply API에서 거부한다.

모델 가격 정보가 없어 비용을 계산할 수 없으면 `candidate.usage.cost`는 `null`, `candidate.usage.cost_unavailable`은 `true`로 반환한다. 이 경우 토큰과 latency를 계산할 수 있다면 그대로 반환한다.

### PATCH apply

관련 FR: FR-008, FR-010

`PATCH /api/v1/workflows/{workflow_id}/llm-nodes/{node_id}/cost-optimizer/apply`

선택한 B 후보 설정을 current draft의 target LLM node에 적용한다.

적용은 부분 적용이 아니라 B 후보 설정 전체 일괄 적용이다.

Apply request의 후보 설정 필드명은 `candidate_settings`다. 이 schema는 compare request의 `candidate`와 같은 설정 구조를 사용하되, 이미 생성된 비교 결과를 적용하는 API이므로 `comparison_id`를 함께 받는다.

Request:

```json
{
  "comparison_id": "comparison-id",
  "candidate_settings": {
    "model_id": "gpt-4.1-mini",
    "fallback_model_id": "gpt-4.1",
    "task_type": "generate",
    "system_prompt": "string",
    "user_prompt": "string",
    "assistant_prompt": "string",
    "parameters": {
      "max_tokens": 800,
      "temperature": 0.2,
      "top_p": 1,
      "presence_penalty": 0,
      "frequency_penalty": 0,
      "stop": []
    },
    "output_format": {
      "type": "json",
      "schema": {}
    },
    "knowledge": {
      "knowledge_base_ids": ["knowledge-base-id"],
      "top_k": 5,
      "score_threshold": 0.7
    }
  },
  "acknowledge_downstream_warning": false
}
```

Response:

```json
{
  "workflow_id": "uuid",
  "node_id": "llm-triage",
  "applied": true,
  "downstream_compatibility": {
    "state": "compatible",
    "label": "검증 가능",
    "message": "string"
  },
  "updated_draft_revision": "string-or-number"
}
```

`downstream_compatibility.state`가 `warning` 또는 `incompatible`인 저장 후보를 적용하는 경우, API는 `acknowledge_downstream_warning=true` 없이는 `400 cost_optimizer.downstream_ack_required`로 적용을 거부한다.

`comparison_id`에 해당하는 저장 후보 중 request의 `candidate_settings`와 일치하는 후보가 있으면, 적용 성공 시 해당 `cost_optimizer_candidates` row의 `is_applied`를 `true`로 바꾸고 `applied_at`, `applied_by`를 기록한다. 같은 experiment 안의 다른 후보는 `is_applied=false`로 정리해 experiment history에서 적용된 후보를 하나만 표시한다.

`comparison_id`가 없거나 UUID로 해석할 수 없거나, 해당 `comparison_id`에 저장된 후보가 없거나, request의 `candidate_settings`와 일치하는 저장 후보가 없으면, API는 검증되지 않은 후보 적용으로 보고 `400 cost_optimizer.candidate_not_found`를 반환한다.

현재 apply request에는 기대 draft revision을 전달하는 필드가 없다. 따라서 현재 계약은 적용 성공 후 `updated_draft_revision`을 반환하는 데까지를 보장한다. baseline 비교 이후 current draft가 바뀌었는지 감지해 `409 cost_optimizer.draft_conflict`로 막는 동작은 expected draft revision 계약을 추가하는 후속 보강 범위다.

## Errors

| Status | Code | Description | Related FR |
| --- | --- | --- | --- |
| 400 | `cost_optimizer.not_llm_node` | target node가 `llmNode`가 아님 | FR-001 |
| 400 | `cost_optimizer.no_baseline` | baseline으로 사용할 로그가 없음 | FR-002 |
| 400 | `cost_optimizer.invalid_candidate` | B 후보 설정이 유효하지 않음 | FR-003 |
| 400 | `cost_optimizer.schema_failed_candidate` | schema 검증 실패 후보를 적용하려고 함 | FR-003, FR-008 |
| 400 | `cost_optimizer.candidate_not_found` | apply 요청의 `comparison_id`가 없거나, 해당 comparison 안에서 request 후보 설정과 일치하는 저장 후보를 찾을 수 없음 | FR-008, FR-009 |
| 400 | `cost_optimizer.baseline_input_unavailable` | baseline input을 복원할 수 없음 | FR-004 |
| 400 | `cost_optimizer.downstream_ack_required` | downstream warning 확인 없이 적용 요청 | FR-007, FR-008 |
| 403 | `permission.denied` | builder 이상 권한 없음 | FR-010 |
| 404 | `resource.not_found` | workflow, node, baseline, experiment 또는 candidate가 없거나 요청한 workflow/node/organization scope 밖임 | FR-001, FR-002, FR-006, FR-009, FR-013 |
| 422 | `cost_optimizer.model_unavailable` | 사용할 수 없는 credential/model 후보 | FR-003, FR-010 |
| 422 | `cost_optimizer.knowledge_unavailable` | 사용할 수 없거나 접근 권한이 없는 Knowledge Base 후보 | FR-003, FR-010 |
| 500 | `cost_optimizer.compare_failed` | B 후보 실행 결과를 failed candidate로 기록할 수 없는 예기치 않은 서버 오류 | FR-006 |

후속 보강 오류:

| Status | Code | Description | Related FR |
| --- | --- | --- | --- |
| 409 | `cost_optimizer.draft_conflict` | expected draft revision 계약 추가 후 현재 draft가 baseline 비교 이후 충돌됨 | FR-008 |

## Permissions

관련 FR: FR-010

Cost Optimizer API는 builder 이상 권한을 요구한다.

- A/B 테스트 진입 가능 여부 조회: builder 이상
- baseline 조회: builder 이상
- experiment/candidate 이력 목록 조회: builder 이상
- experiment/candidate 단건 상세 조회: builder 이상
- B 후보 실행: builder 이상
- 후보 적용: builder 이상

API는 프론트의 UI 차단과 별개로 서버에서 권한을 강제해야 한다.

workflow 실행 권한만 있는 사용자는 Cost Optimizer를 사용할 수 없다.

## Security And Redaction

관련 FR: FR-006, FR-009, FR-010

- credential 원문, API key, encrypted config는 응답에 포함하지 않는다.
- raw prompt 전체는 정책에 따라 preview 또는 redacted summary로 제한할 수 있다.
- RAG retrieval summary는 권한이 허용된 safe summary만 반환한다.
- 권한 없는 문서명, raw chunk content, raw source metadata는 반환하지 않는다.
- 비교 실행에서 발생한 LLM usage는 비용 추적에서 누락되지 않아야 한다.
