# Cost Optimizer API Spec

Status: Draft
Verified Against: feature/mba-270 @ d2d416b9

## Purpose

이 문서는 `requirements.md`의 FR-001부터 FR-014까지를 API 계약 관점에서 정리한다.
FR-011은 `judge_bootstrap_incremental_v1` strategy의 무수동-bootstrap 정책으로 다룬다. 초기 운영 요청은 runtime Judge가
요구 수준을 판정하고, 서버가 실행 주체가 사용할 수 있는 전체 후보에서 capability와 가격을
비교해 모델을 선택한다. Judge 요구 수준 label과 완료된 운영 결과는 배포 정책과 분리된
자동 라우팅 학습기에 쌓인다. Celery가 학습기 행을 잠그고 10건 또는 최대 5분 batch로
candidate artifact를 학습한다. 최근
20건의 학습 전 예측 정확도와 계약 품질을 통과한 artifact만 로컬 라우터로 승격한다. local prediction이 불확실하거나
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
| FR-011 | policy 조회/설정/refresh/preview API와 배포 후 run event가 policy table을 관리한다. Judge bootstrap 선택, local router 전환 상태, 실행 주체 후보 모델과 노드별 운영 성적을 사용해 runtime 모델 선택을 제공한다. |
| FR-012 | 운영 로그와 trace summary를 분석해 LLM 파라미터/모델 라우팅 추천을 반환한다. `direct_policy_update`만 즉시 적용하고, 일반 파라미터 조정은 A/B candidate 생성 경로로 보낸다. |
| FR-013 | 추천 빠른 검증과 사용자가 baseline을 고르는 일반 compare 모두 candidate 실행 후 semantic 품질 평가를 수행하고 점수·confidence·safe summary를 응답과 이력에 저장한다. |
| FR-014 | 배포별 자동 파라미터 최적화 설정과 수집/예산 safe summary를 제공한다. 비용 위험 신호와 모델 라우팅 정책은 포함하지 않는다. |

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
| FR-011 | Judge-first policy persistence, event idempotency, refresh, credential guard, trace | `apps/shared/db/models/model_routing_policy.py`, `apps/gateway/api/v1/endpoints/workflow.py`, `apps/workflow_engine/tasks.py`, `apps/workflow_engine/services/model_routing_policy_refresh_task.py`, `apps/workflow_engine/workflow/nodes/llm/llm_node.py` | 구현 완료 | FR-011 targeted tests | 통과 |
| FR-011 | Runtime Judge, 점진 학습 local classifier, runtime candidate selection | `apps/workflow_engine/services/model_routing_runtime_judge.py`, `model_routing_incremental_learning.py`, `model_routing_local_classifier.py`, `model_router.py`, `llm_node.py` | 구현 완료 | `test_model_router.py`, `test_model_routing_incremental_learning.py`, `test_model_routing_policy_refresh_task.py`, `test_llm_node_runtime.py` | 집중 테스트 통과 |
| FR-011 | 정책 독립 학습기, 불변 버전, 학습 전 예측과 Celery batch | `apps/workflow_engine/services/model_routing_learner_store.py`, `apps/workflow_engine/services/model_routing_learning_batch.py`, `apps/workflow_engine/tasks.py`, `apps/shared/db/models/model_routing_policy.py` | 구현 완료 | `test_model_routing_learner_store.py`, `test_model_routing_learning_batch.py`, `test_model_routing_policy_tasks.py`, `test_model_routing_requirement_learning.py` | 집중 테스트 통과 |
| FR-011 | 과거 정책 호환 경계 | refresh 시 Judge-first로 1회 이관하고 신규 runtime에서는 레거시 rule을 실행하지 않음 | 구현 완료 | `test_model_routing_policy_refresh_task.py`, `test_llm_node_runtime.py` | 통과 |
| FR-012 | LLM parameter recommendation contract | `apps/gateway/services/cost_optimizer_parameter_recommendation_service.py`, `apps/gateway/api/v1/endpoints/workflow.py` | 구현 완료 | `apps/gateway/tests/api/cost_optimizer/test_parameter_recommendations_api.py`, `apps/gateway/tests/api/cost_optimizer/test_cost_optimizer_api.py` | 통과 기록 있음 |
| FR-013 | Recommendation/compare verification orchestration, quality judge, history summary, modal/result-analysis UI | `apps/gateway/services/cost_optimizer_recommendation_verification_service.py`, `apps/gateway/services/cost_optimizer_output_quality_service.py`, `apps/gateway/api/v1/endpoints/workflow.py`, `apps/shared/db/models/cost_optimizer.py`, `apps/client/app/features/workflow/components/costOptimizer/OptimizationRecommendationModal.tsx`, `apps/client/app/features/workflow/api/workflowApi.ts`, `apps/client/app/modules/[id]/cost-optimizer/[nodeId]/page.tsx` | 구현 완료 | `apps/gateway/tests/api/cost_optimizer/test_cost_optimizer_api.py`, `apps/gateway/tests/api/cost_optimizer/test_recommendation_verification_api.py`, `apps/gateway/tests/services/test_cost_optimizer_output_quality_service.py`, `apps/client/app/features/workflow/tests/costOptimizer/fr13-recommendation-inline-verification.test.tsx`, `apps/client/app/features/workflow/tests/costOptimizer/fr13-recommendation-verification-api-client.test.ts`, `apps/client/app/features/workflow/tests/costOptimizer/fr6-playground-mode-switch.test.tsx` | Gateway/frontend targeted test 통과 |
| FR-014 | deployment config, plan persistence, operations summary, verification spend guard | `apps/shared/db/models/deployment_parameter_optimization.py`, `apps/gateway/services/deployment_parameter_optimization_service.py`, `apps/gateway/api/v1/endpoints/deployment.py`, `apps/gateway/api/v1/endpoints/workflow.py`, `apps/gateway/services/app_service.py` | 수집/상태/예산 설정과 실제 검증 비용 누적 구현 | `apps/gateway/tests/services/test_deployment_parameter_optimization_service.py`, `apps/gateway/tests/api/cost_optimizer/test_recommendation_verification_api.py`, operations API targeted test | 통과 |

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

Runtime Judge provider request는 외부 API 계약이 아니라 Workflow Engine 내부 계약이다. user content에는
`request_feature`, `rag_context`, `candidate_models`만 보낸다. `request_feature`는 JSON 구조를 보존한
`CURRENT_REQUEST_JSON`과 길이 제한된 세 prompt, 출력 계약을 포함한다. `rag_context`는 검색량과
근거 충분성 같은 safe signal만 허용하며 chunk/document 원문은 금지한다. `candidate_models`에는 가격,
운영 계약 성적, 공식 역할·특화 태그, 난이도 상한, 비용 역할과 `context_window`를 포함할 수 있다.
`cost_position`은 모델 능력값이 아니며 `capability_tier` 단독으로 후보를 선택하거나 제외하지 않는다.
정상 응답은 선택 결과 외에
다음 선택적 요구 능력 요약을 반환할 수 있다.

~~~json
{
  "selected_model_id": "gpt-5-mini",
  "confidence": 0.86,
  "reason_short": "근거 종합 필요",
  "reason_code": "evidence_synthesis",
  "task_requirements": {
    "task_complexity": 2,
    "decision_impact": 1,
    "evidence_synthesis": 3,
    "output_precision": 2
  }
}
~~~

각 요구 능력 점수는 0~3 정수이며, 계약 밖의 값은 trace에서 폐기한다. 정상 Judge 출력 한도는
768 token이다.

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
충분히 확신하면 local 선택을, 그렇지 않으면 기본 또는 대체 모델을 예상값으로 반환한다.
Preview와 runtime은 같은 `ModelRouter.routing_feature_text()` builder와 side-effect 없는 prompt renderer로 현재 입력, 노드 제목,
작업 설명, 현재 입력으로 렌더링된 system/user/assistant prompt를 구성한다. JSON output schema 지시는 긴 prompt에 밀려나지 않도록
구조 요약과 함께 별도 `OUTPUT_CONTRACT` 섹션 및 독립 길이 예산으로 구성한다. Preview는 실제 RAG retrieval 결과를 만들지 않으므로
동적 RAG signal은 포함하지 않는다. 따라서 preview의 모델은 배포 실행에서 Judge가 고를 실제 모델을
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
| `confidence`, `reason_code`, `reason_short`, `cost` | `status=selected`일 때의 구조화된 선택 근거. `reason_short`은 사전 정의된 코드에 대응하는 짧은 안전 설명이며, Judge 자유 문장은 durable trace에 저장하지 않는다. |
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

Judge가 선택한 실행은 처음에는 `learning_status=pending_contract`로 기록한다. workflow
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
`judged_request_count`는 안전한 3축 요구 수준이 있는 처리 완료 Judge label 수다. 계약 실패
label도 이 건수와 최근 계약 통과율에는 포함되지만 candidate classifier artifact의 가중치에는
반영하지 않는다.
`schema_status=not_applicable`는 schema 검사가 실패한 것이 아니라 수행되지 않은 상태이므로 학습
거절 사유나 schema 평가·통과 집계의 분모에 포함하지 않는다. 반면 선언된 JSON schema가 유효하지 않아
검사를 완료하지 못한 `schema_status=not_evaluated`는 `schema_failed` 학습 거절로 처리하고 schema 평가
분모에는 포함하되 통과 건수에는 포함하지 않는다.

`accepted` label에는 원문이 아닌 `routing_feature_hash`만 저장한다. 이를 이용해 같은 feature의
계약 통과 Judge 선택을 재사용할 수 있으며, 모델 사용 권한이 바뀌었거나 hash key가 없으면
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
| llm_node_model_routing_learners | 작업 지문과 Judge/E5 계약별 지속 학습 상태, candidate artifact, 최근 20건 평가. 배포 정책과 독립적으로 재사용한다. |
| llm_node_model_routing_learner_versions | 품질 gate를 통과해 발행한 변경 불가능한 로컬 분류기 버전과 평가 요약. |
| llm_node_model_routing_learning_labels | 필수 learner와 선택적 source policy를 참조하는 Judge 정답, 안전한 vector, 학습 전 예측, 실행/schema/downstream/fallback 결과와 비동기 처리 상태. 원문 prompt/input은 저장하지 않는다. |
| llm_model_routing_global_profiles | Judge-first runtime이 후보 모델의 초기 품질·지연·fallback 사전 정보를 읽는 전역 catalog profile. 실행 주체가 사용할 수 있으면서 명시적 catalog에 등록된 모델만 후보가 된다. |

Test Sidebar 실행은 설정 지문이 같은 검증 learner version을 사용할 수 있으면 로컬 라우터를
사용하고, 없거나 확신이 낮으면 runtime Judge를 호출한다. 어느 경우에도 Judge label,
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
