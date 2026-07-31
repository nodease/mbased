# Cost Optimizer Test Cases

Status: Draft
Verified Against: `feature/mba-247 @ 311a4bc2`

## Purpose

이 문서는 `requirements.md`의 FR-001부터 FR-015까지를 테스트 관점에서 검증 가능한 형태로 정리한다.
FR-011은 정책과 분리된 자동 라우팅 학습기로 다룬다. 배포 실행 1~50회는 runtime Judge가
현재 요청의 요구 수준을 판정하고, 서버가 실행 주체가 사용할 수 있는 전체 후보 중 capability와
비용을 비교해 모델을 선택한다. 후보가 아직 운영 검증을 통과하지 않았다는 이유만으로 첫 사용을
막지 않는다. workflow 완료 뒤 schema·후속 노드·fallback 계약을 통과한 label의 요청 요구 능력만
별도 learner의 local router 학습에 반영한다. 50건 이상이고 선택 모델 분포가 한 모델에
과도하게 쏠리지 않았을 때 local router가 먼저 요청 요구 능력을 예측한다. 서버는 capability를
충족하는 후보 중 비용이 낮은 모델을 선택하며, 확신이 낮으면 runtime Judge로 되돌아간다. JSON
Schema 같은 고정 출력 계약은 후보 capability 검사로만 쓴다. Local 난이도 학습 feature는
`referenced_variables`의 실행값을 핵심 요청 75%, 동적 문맥 15%, 구조화 특징 10%의 분리
임베딩으로 결합하고, 변하는 RAG 안전 신호만 구조화 특징에 포함하며 고정 prompt 계약은 제외한다.

테스트는 LLM 노드 단위 Cost Optimizer 흐름을 기준으로 한다. 모델 라우팅은 자동 라우팅 토글과 active policy 평가뿐 아니라, operational/replay evidence 출처 분리, Hard Gate, 적합성 분석, candidate 품질 gate, 결정론적 optimizer와 decision trace를 검증한다. 고정 20회는 호환 trigger 테스트일 뿐 adaptive routing 완료 기준이 아니다.

FR-011 Runtime Judge 테스트는 provider 공식 문서 기반 특화 태그와 운영 측정값을 구분하고,
공식 별칭을 후보 하나로 정규화하며, Sol 같은 일반 전문 업무 모델과 o3 같은 전문 추론 모델이
동일한 `advanced` 후보로 뭉개지지 않는지 검증한다. 특화 태그는 모델 선택을 확정하는 품질
점수가 아니며 실제 운영 계약 성적이 충분하면 운영 증거가 우선한다.
또한 능력, 추론 방식, 권장 복잡도 상한, 비용 역할을 각각 `capability_tier`,
`reasoning_profile`, `complexity_ceiling`, `cost_position`으로 분리한다. GPT-4.1 같은
비추론 모델이 최신 frontier reasoning 모델과 같은 복잡 업무 상한으로 전달되지 않고,
Terra·Haiku·Gemini Flash처럼 가격 역할과 능력 위치가 다른 모델을 단일 등급으로 뭉개지 않는지 검증한다.
기본 처리 모델과 Judge 모델은 분리해 검증한다. 레거시 정책이 두 모델을 같은 값으로 저장했으면
사용 가능한 provider별 Judge 선호 모델로 전환하고, 명시적으로 다른 Judge를 저장한 정책은 해당
모델을 유지해야 한다.
Judge 입력은 요청 JSON 구조와 type, 길이 제한된 세 prompt만 사용하며, 고정 output contract는 요청별 난이도 신호로 전달하지 않아야 한다.
RAG는 문서 원문 없이 검색량·근거 충분성·부분 결과·query rewrite 같은 safe signal만 전달한다.
후보 profile은 context window를 유지하고, Judge 요구 능력 4축은 0~3 정수일 때만 safe metadata에
남긴다. 정상 호출과 incomplete retry의 출력 한도는 모두 768 token이다.

현재 구현 기준으로 baseline 선택 UI는 최신 baseline을 자동 고정하지 않는다. 테스트는 baseline 목록에서 사용자가 row를 직접 선택한 뒤 B candidate 영역이 열리는 흐름을 기준으로 한다.

## Test Matrix

| FR | Component Spec | API Spec | Test Focus | 테스트 코드 상태 | 테스트 통과 여부 |
| --- | --- | --- | --- | --- | --- |
| FR-001 | LLM node detail action | GET availability | LLM 노드에서만 A/B 테스트 진입 가능 | 작성 완료 | 통과 |
| FR-002 | Baseline selection, baseline log picker | GET latest baseline, GET baselines | 최신/이전 baseline 로그 선택 | 작성 완료 | 통과 |
| FR-003 | Candidate editor | POST compare request candidate schema | B 후보 설정 입력과 검증 | 작성 완료 | 통과 |
| FR-004 | Baseline input lock display | baseline input 고정 | B 실행 입력이 A baseline 입력으로 고정됨 | 작성 완료 | 통과 |
| FR-005 | Hybrid compare flow | POST compare | A는 재실행하지 않고 B만 실행 | 작성 완료 | 통과 |
| FR-006 | A/B compare workspace, result analysis, Inspector | compare response trace/diff | A/B 결과, 적용 판단 요약, 핵심 지표 비교, Inspector 데이터 표시 | 작성 완료 | 통과 |
| FR-007 | Downstream compatibility badge | downstream compatibility fragment | downstream 호환성 3상태 표시와 차단/경고 | 작성 완료 | 통과 |
| FR-008 | Apply candidate action | PATCH apply | B 후보 설정을 current draft에 적용 | 작성 완료 | 통과 |
| FR-009 | Cost/usage display | llm usage logging | 비교 실행 비용/토큰/latency 기록과 표시 | 작성 완료 | 통과 |
| FR-010 | Permission-gated UI | builder permission enforcement | builder 이상 권한 강제 | 작성 완료 | 통과 |
| FR-011 | Judge-first routing / policy-independent learner | Runtime Judge, learner label, 불변 learner version, local router | 작업 지문 재사용·계약 계보 분리·운영 label 확정·50건 local 전환·테스트 실행 비학습·safe trace | 작성 완료 | `test_model_routing_learner_store.py`, `test_model_routing_learning_batch.py`, `test_model_routing_policy_tasks.py`, `test_judge_first_model_routing_e2e.py`, `test_llm_node_runtime.py` 집중 통과 |
| FR-012 | Optimization recommendation modal | Parameter recommendation API | 운영 로그 기반 추천 조회, `direct_policy_update` 적용, 일반 추천의 A/B 후보 실험 연결 | 작성 완료 | 부분 통과 |
| FR-013 | Recommendation verification / compare quality row | Recommendation verification·compare API | 최신 성공 또는 사용자 선택 baseline, candidate 1회 실행, 품질 judge, schema/downstream gate, 품질 점수 이력, 적용/상세 분석 연결 | Gateway/frontend 테스트 작성 완료 | Gateway/frontend targeted test 통과 |
| FR-014 | 배포별 자동 파라미터 최적화 | deployment config / operations summary | 배포 모달 설정, 대상 LLM node 검증, 배포 후 운영 실행 수집, 별도 예산/관리 UI | Gateway/frontend targeted test 작성 완료 | 통과 |
| FR-015 | 제품 UI 없음 | 독립 constraint strategy/result-matrix contract | 구조 제약 추출, Hard Gate, 안전 기본 모델, 실제 semantic matcher fixture, 결과/RAG 재사용, 채택 기준 판정 | Workflow Engine/experiment test 작성 완료 | 통과 |

## Test Implementation Tracking

테스트 파일명은 구현 시점에 실제 컴포넌트/API 파일명에 맞춰 조정할 수 있다. 단, FR과 테스트 파일의 역참조는 유지해야 한다.

| FR | 테스트 레이어 | 예상 테스트 파일 | 검증 대상 | 작성 상태 | 실행 명령 | 통과 여부 |
| --- | --- | --- | --- | --- | --- | --- |
| FR-001 | Frontend component | `apps/client/app/features/workflow/tests/costOptimizer/fr1-entry-action.test.tsx` | LLM 노드 전용 진입 액션, non-LLM 차단, availability 기반 비활성화, 저장되지 않은 draft 안내 | 작성 완료 | `cd apps/client && npm run test -- --run app/features/workflow/tests/costOptimizer/fr1-entry-action.test.tsx` | 통과 |
| FR-001 | Gateway API | `apps/gateway/tests/api/cost_optimizer/test_cost_optimizer_api.py` | availability, not-LLM, not-found, builder 권한 강제 | 작성 완료 | `PYTHONPATH=$(git rev-parse --show-toplevel) apps/gateway/.venv/Scripts/python.exe -m pytest apps/gateway/tests/api/cost_optimizer/test_cost_optimizer_api.py` | 통과 |
| FR-002 | Frontend component | `apps/client/app/features/workflow/tests/costOptimizer/fr2-baseline-selection.test.tsx` | 최신/이전 baseline 선택, API row 기반 모델 필터, preview 전체 표시, JSON Schema title label/원본 key fallback, 최신 비교 가능 로그 없음 CTA 차단, 필터/정렬 UI | 작성 완료 | `cd apps/client && npm run test -- --run app/features/workflow/tests/costOptimizer/fr2-baseline-selection.test.tsx` | 통과 |
| FR-002 | Gateway API | `apps/gateway/tests/api/cost_optimizer/test_cost_optimizer_api.py` | latest/list baseline query, 필터/정렬/pagination, secret redaction, trace payload availability | 작성 완료 | `PYTHONPATH=$(git rev-parse --show-toplevel) apps/gateway/.venv/Scripts/python.exe -m pytest apps/gateway/tests/api/cost_optimizer/test_cost_optimizer_api.py` | 통과 |
| FR-003 | Frontend component | `apps/client/app/features/workflow/tests/costOptimizer/fr3-candidate-editor.test.tsx` | 후보 모델/대체 모델 선택 UI, JSON schema key-type row 추가, prompt variable token editor 재사용, compare request schema 변환 | 작성 완료 | `cd apps/client && npm run test -- --run app/features/workflow/tests/costOptimizer/fr3-candidate-editor.test.tsx` | 통과 |
| FR-003 | Frontend component | `apps/client/app/features/workflow/tests/costOptimizer/fr3-llm-node-routing.test.tsx` | 원본 LLM 노드 상세 설정의 모델 선택 UI 유지, 모델 라우팅 최적화 진입 버튼, task type 선택 미노출 | 작성 완료 | `cd apps/client && npm run test -- --run app/features/workflow/tests/costOptimizer/fr3-llm-node-routing.test.tsx` | 통과 |
| FR-003 | Frontend component | `apps/client/app/features/workflow/tests/costOptimizer/fr3-rag-cost-options.test.tsx` | RAG 비용 최적화 옵션 노출/변경 | 작성 완료 | `cd apps/client && npm run test -- --run app/features/workflow/tests/costOptimizer/fr3-rag-cost-options.test.tsx` | 통과 |
| FR-003 | Workflow runtime | `apps/workflow_engine/tests/nodes/test_llm_node_runtime.py` | 중복 근거 제거, 참조 문서 길이 제한, 검색 문서 압축, 답변·검색 문서 어휘 일치도 | 작성 완료 | `PYTHONPATH=$(git rev-parse --show-toplevel) apps/workflow_engine/.venv/Scripts/python.exe -m pytest apps/workflow_engine/tests/nodes/test_llm_node_runtime.py` | 통과 |
| FR-003 | Gateway API | `apps/gateway/tests/api/cost_optimizer/test_cost_optimizer_api.py` | candidate schema validation, Knowledge Base/model 사용 가능성 검증, schema_failed response/apply 차단 | 작성 완료 | `PYTHONPATH=$(git rev-parse --show-toplevel) apps/gateway/.venv/Scripts/python.exe -m pytest apps/gateway/tests/api/cost_optimizer/test_cost_optimizer_api.py` | 통과 |
| FR-004 | Frontend component | `apps/client/app/features/workflow/tests/costOptimizer/fr4-fr5-hybrid-compare-flow.test.tsx` | baseline input lock 표시, compare request에 임의 input을 넣지 않음 | 작성 완료 | `cd apps/client && npm run test -- --run app/features/workflow/tests/costOptimizer/fr4-fr5-hybrid-compare-flow.test.tsx` | 통과 |
| FR-004 | Gateway service/API | `apps/gateway/tests/api/cost_optimizer/test_cost_optimizer_api.py` | baseline input restore, wrong baseline scope | 작성 완료 | `PYTHONPATH=$(git rev-parse --show-toplevel) apps/gateway/.venv/Scripts/python.exe -m pytest apps/gateway/tests/api/cost_optimizer/test_cost_optimizer_api.py` | 통과 |
| FR-005 | Frontend component | `apps/client/app/features/workflow/tests/costOptimizer/fr4-fr5-hybrid-compare-flow.test.tsx` | A 고정, B running/result 상태 | 작성 완료 | `cd apps/client && npm run test -- --run app/features/workflow/tests/costOptimizer/fr4-fr5-hybrid-compare-flow.test.tsx` | 통과 |
| FR-005 | Frontend API client | `apps/client/app/features/workflow/tests/costOptimizer/fr4-fr5-compare-api-client.test.ts` | compare API path와 request body 계약 | 작성 완료 | `cd apps/client && npm run test -- --run app/features/workflow/tests/costOptimizer/fr4-fr5-compare-api-client.test.ts` | 통과 |
| FR-005 | Gateway service/API | `apps/gateway/tests/api/cost_optimizer/test_cost_optimizer_api.py` | A 미재실행, B만 실행 | 작성 완료 | `PYTHONPATH=$(git rev-parse --show-toplevel) apps/gateway/.venv/Scripts/python.exe -m pytest apps/gateway/tests/api/cost_optimizer/test_cost_optimizer_api.py` | 통과 |
| FR-006 | Frontend component | `apps/client/app/features/workflow/tests/costOptimizer/fr6-playground-mode-switch.test.tsx` | 실험 설정/결과 분석 mode switch, stale 안내, 적용 판단 요약, 핵심 지표 비교, Inspector 탭, schema 검증 결과, A/B retrieval summary 표시, 직접 URL 진입 권한 차단 | 작성 완료 | `cd apps/client && npm run test -- --run app/features/workflow/tests/costOptimizer/fr6-playground-mode-switch.test.tsx` | 통과 |
| FR-006 | Gateway API | `apps/gateway/tests/api/cost_optimizer/test_cost_optimizer_api.py` | compare response shape, safe trace, RAG raw content/source metadata redaction, 비용/토큰/latency diff, 실패 후보 response | 작성 완료 | `PYTHONPATH=$(git rev-parse --show-toplevel) apps/gateway/.venv/Scripts/python.exe -m pytest apps/gateway/tests/api/cost_optimizer/test_cost_optimizer_api.py` | 통과 |
| FR-007 | Frontend component | `apps/client/app/features/workflow/tests/costOptimizer/fr7-downstream-compatibility.test.tsx` | compare 응답의 downstream 3상태 라벨, 설명, 검사 노드 표시 | 작성 완료 | `cd apps/client && npm run test -- --run app/features/workflow/tests/costOptimizer/fr7-downstream-compatibility.test.tsx` | 통과 |
| FR-007 | Gateway service/API | `apps/gateway/tests/api/cost_optimizer/test_cost_optimizer_api.py` | compatible/warning/incompatible 판정, `variableExtractionNode`/`conditionNode`/`answerNode`/`slackPostNode` 직접 소비 노드 contract 검사, compare response 연결 | 작성 완료 | `PYTHONPATH=$(git rev-parse --show-toplevel) apps/gateway/.venv/Scripts/python.exe -m pytest apps/gateway/tests/api/cost_optimizer/test_cost_optimizer_api.py` | 통과 |
| FR-008 | Frontend API client | `apps/client/app/features/workflow/tests/costOptimizer/fr8-apply-api-client.test.ts` | apply API path와 `candidate_settings` request body 계약 | 작성 완료 | `cd apps/client && npm run test -- --run app/features/workflow/tests/costOptimizer/fr8-apply-api-client.test.ts` | 통과 |
| FR-008 | Frontend component | `apps/client/app/features/workflow/tests/costOptimizer/fr8-apply-flow.test.tsx` | 성공한 B 후보 적용 버튼 활성화, 적용 전 변경 요약 모달, downstream warning 확인, apply 호출, 적용 성공 안내 | 작성 완료 | `cd apps/client && npm run test -- --run app/features/workflow/tests/costOptimizer/fr8-apply-flow.test.tsx` | 통과 |
| FR-008 | Gateway API | `apps/gateway/tests/api/cost_optimizer/test_cost_optimizer_api.py` | apply가 current draft target LLM node data를 B candidate의 모델, prompt, 고급 파라미터, 출력 형식, Knowledge/RAG 비용 최적화 설정으로 갱신 | 작성 완료 | `PYTHONPATH=$(git rev-parse --show-toplevel) apps/gateway/.venv/Scripts/python.exe -m pytest apps/gateway/tests/api/cost_optimizer/test_cost_optimizer_api.py` | 통과 |
| FR-009 | Frontend component | `apps/client/app/features/workflow/tests/costOptimizer/fr9-usage-display.test.tsx` | A/B prompt/completion/total token, 비용, latency, experiment history 필터 UI 표시 | 작성 완료 | `cd apps/client && npm run test -- --run app/features/workflow/tests/costOptimizer/fr9-usage-display.test.tsx` | 통과 |
| FR-009 | Frontend API client | `apps/client/app/features/workflow/tests/costOptimizer/fr9-experiment-history-api-client.test.ts` | experiment history API path와 baseline/date/creator/status/model/applied/schema/downstream filter query 계약 | 작성 완료 | `cd apps/client && npm run test -- --run app/features/workflow/tests/costOptimizer/fr9-experiment-history-api-client.test.ts` | 통과 |
| FR-009 | Gateway/service | `apps/gateway/tests/api/cost_optimizer/test_cost_optimizer_api.py` | compare response usage/diff, 비용 계산 불가 상태, `cost_optimizer_compare` trigger context, comparison_id experiment/candidate 저장, candidate id context 전달, raw candidate prompt 저장 방지, RAG summary redaction/size cap, 실패 후보 저장, experiment/candidate history 조회 | 작성 완료 | `PYTHONPATH=$(git rev-parse --show-toplevel) apps/gateway/.venv/Scripts/python.exe -m pytest apps/gateway/tests/api/cost_optimizer/test_cost_optimizer_api.py` | 통과 |
| FR-009 | Workflow runtime | `apps/workflow_engine/tests/nodes/test_llm_node_runtime.py` | Cost Optimizer candidate id를 LLM usage log 호출로 전달 | 작성 완료 | `PYTHONPATH=$(git rev-parse --show-toplevel) apps/workflow_engine/.venv/Scripts/python.exe -m pytest apps/workflow_engine/tests/nodes/test_llm_node_runtime.py` | 통과 |
| FR-009 | Shared service | `apps/shared/tests/services/test_cost_optimizer_retention.py` | trace metadata retention 기준 만료일 계산, expired experiment purge, dry run, limit validation | 작성 완료 | `PYTHONPATH=$(git rev-parse --show-toplevel) apps/gateway/.venv/Scripts/python.exe -m pytest apps/shared/tests/services/test_cost_optimizer_retention.py` | 통과 |
| FR-010 | Frontend component | `apps/client/app/features/workflow/tests/costOptimizer/fr1-entry-action.test.tsx`, `apps/client/app/features/workflow/tests/costOptimizer/fr6-playground-mode-switch.test.tsx` | builder 미만 진입 액션 disabled, availability 기반 차단, 직접 URL 진입 시 draft 로드 차단 | 작성 완료 | `cd apps/client && npm run test -- --run app/features/workflow/tests/costOptimizer/fr1-entry-action.test.tsx app/features/workflow/tests/costOptimizer/fr6-playground-mode-switch.test.tsx` | 통과 |
| FR-010 | Gateway API | `apps/gateway/tests/api/cost_optimizer/test_cost_optimizer_api.py` | availability, baseline latest/list, experiment history, compare, apply가 builder/write 권한 경계를 사용하고 모델/Knowledge 후보 사용 가능성을 검증 | 작성 완료 | `PYTHONPATH=$(git rev-parse --show-toplevel) apps/gateway/.venv/Scripts/python.exe -m pytest apps/gateway/tests/api/cost_optimizer/test_cost_optimizer_api.py` | 통과 |
| FR-011 | Frontend component | `apps/client/app/features/workflow/tests/costOptimizer/fr3-llm-node-routing.test.tsx` | 자동 라우팅 토글, 기본/fallback 모델, 배포 시 첫 정책 안내, 모델별 운영 성적, 정책 교체 보호 기준 표시, 고정 횟수 slider 미노출 | 작성 완료 | `cd apps/client && npm run test -- --run app/features/workflow/tests/costOptimizer/fr3-llm-node-routing.test.tsx` | 통과 |
| FR-011 | Routing preview | `apps/gateway/tests/services/test_model_routing_preview_service.py`, `apps/gateway/tests/api/cost_optimizer/test_cost_optimizer_api.py`, `apps/client/app/features/workflow/tests/costOptimizer/fr11-routing-preview.test.tsx`, `apps/client/app/features/workflow/tests/test-sidebar-routing-preview.test.tsx` | active deployment policy 평가, rule/default/fallback/no-model 상태, execute 권한, draft 불일치, 입력 변경 재평가, run/usage/policy event 무기록 | 작성 완료 | Gateway/Client targeted test | 통과 |
| FR-011 | Frontend route | `apps/client/app/features/workflow/tests/costOptimizer/fr2-entry-to-baseline-connection.test.tsx` | `모델 라우팅 최적화` 버튼이 기존 A/B workspace가 아니라 전용 model-routing route로 이동 | 작성 완료 | `cd apps/client && npm run test -- --run app/features/workflow/tests/costOptimizer/fr2-entry-to-baseline-connection.test.tsx` | 통과 |
| FR-011 | Workflow engine service | `apps/workflow_engine/tests/services/test_model_router.py` | `ModelRouter.resolve_policy()`가 저장된 policy의 일반 조건을 평가하고 canonical LLM trace의 terminal 성공/실패 품질 신호와 segment 성능을 profile로 만든다. | 작성 완료 | `PYTHONPATH=$(git rev-parse --show-toplevel) apps/workflow_engine/.venv/Scripts/python.exe -m pytest apps/workflow_engine/tests/services/test_model_router.py` | 통과 |
| FR-011 | Incremental routing mode transition | `apps/workflow_engine/tests/services/test_model_routing_incremental_learning.py` | Judge-first에서는 Runtime Judge가 필요하고 current learner artifact와 충분한 confidence가 있으면 local-router로 전환한다. 이 테스트는 선택 결과가 실행 가능한 후보 안에 있는지 검증하며, catalog capability·bootstrap score·가격에 따른 exact model 선택은 `test_model_router.py`가 소유한다. | 작성 완료 | `PYTHONPATH=$(git rev-parse --show-toplevel) apps/workflow_engine/.venv/Scripts/python.exe -m pytest apps/workflow_engine/tests/services/test_model_routing_incremental_learning.py` | 통과 |
| FR-011 | Learning path E2E | `apps/workflow_engine/tests/e2e/test_judge_first_model_routing_e2e.py` | 첫 Judge label이 `accepted`로 확정돼 count 1이 되고, 서로 다른 계약 통과 label 50건 뒤 51회차가 `local_router` source로 선택되는지 확인한다. | 작성 완료 | `PYTHONPATH=$(git rev-parse --show-toplevel) apps/workflow_engine/.venv/Scripts/python.exe -m pytest apps/workflow_engine/tests/e2e/test_judge_first_model_routing_e2e.py` | 통과 |
| FR-011 | Judge learning observability | `apps/workflow_engine/tests/nodes/test_llm_node_runtime.py`, `apps/workflow_engine/tests/services/test_model_router.py` | label queue 실패는 trace에 원인 코드로 남기며, HMAC hash가 같은 accepted 선택은 Judge 재호출 없이 재사용한다. | 작성 완료 | Workflow Engine 집중 pytest | 통과 |
| FR-011 | Judge label transaction | `apps/workflow_engine/tests/services/test_model_routing_policy_store.py` | 성공 label은 즉시 commit하고 저장하지 않는 경로는 rollback하여 policy row lock을 최종 provider 호출 전에 해제한다. | 작성 완료 | `PYTHONPATH=$(git rev-parse --show-toplevel) apps/workflow_engine/.venv/bin/python -m pytest apps/workflow_engine/tests/services/test_model_routing_policy_store.py` | 통과 |
| FR-011 | Workflow runtime | `apps/workflow_engine/tests/nodes/test_llm_node_runtime.py` | 자동 라우팅 ON 실행은 active Judge-first policy를 우선 사용한다. 테스트 또는 배포 실행에 policy row가 없으면 실행 주체가 사용할 수 있는 후보로 일회성 Judge-first policy를 구성해 runtime Judge를 호출하며, 테스트와 policy id 없는 예외 실행은 학습 label을 만들지 않는다. 현재 사용자에게 사용할 모델이 없으면 provider 호출 전에 차단한다. | 작성 완료 | `PYTHONPATH=$(git rev-parse --show-toplevel) apps/workflow_engine/.venv/Scripts/python.exe -m pytest apps/workflow_engine/tests/nodes/test_llm_node_runtime.py -k auto_model_routing` | 통과 |
| FR-011 | Trace metadata | `apps/shared/tests/services/test_tracing_metadata.py` | model routing decision summary가 safe metadata allowlist로 보존되고 raw prompt/secret은 제거됨 | 작성 완료 | `PYTHONPATH=$(git rev-parse --show-toplevel) apps/workflow_engine/.venv/Scripts/python.exe -m pytest apps/shared/tests/services/test_tracing_metadata.py` | 통과 |
| FR-011 | Policy lifecycle | `apps/workflow_engine/tests/services/test_model_routing_policy_lifecycle.py`, `apps/workflow_engine/tests/services/test_model_routing_policy_tasks.py`, `apps/workflow_engine/tests/services/test_model_routing_operational_performance.py`, `apps/log_system/tests/test_model_routing_policy_hook.py` | terminal workflow의 성공·실패 운영 표본을 중복 없이 모델별 누계에 반영하고, 최소 새 표본 이후 품질·비용·지연 변화가 의미 있을 때만 refresh task를 예약 | 작성 완료 | targeted pytest | 통과 |
| FR-011 | Policy change guard | `apps/workflow_engine/tests/services/test_model_routing_policy_change_guard.py` | 선택 모델이 같거나 개선이 10% 미만이면 기존 version 유지, 품질 하락 시 차단, 품질을 유지한 10% 이상 비용·지연 개선만 적용 | 작성 완료 | targeted pytest | 통과 |
| FR-011 | Persisted refresh | `apps/workflow_engine/tests/services/test_model_routing_policy_refresh_task.py`, `apps/workflow_engine/tests/services/test_model_routing_prior_guided_policy.py` | 실행 주체가 사용할 수 있는 모델 catalog와 모델·입력 길이별 운영 누계로 정책을 재계산하고, 서로 다른 입력 길이의 성적이 섞이지 않으며, Judge 없이 적용 또는 기존 policy 유지 결과를 저장 | 작성 완료 | targeted pytest | 통과 |
| FR-011 | Synthetic policy evaluator regression | `apps/workflow_engine/tests/e2e/test_model_routing_policy_e2e.py` | 미리 주입한 모델 profile로 61회 동안 refresh trigger와 저장 rule evaluator를 검증한다. 실제 Replay evidence 획득 E2E로 보지 않는다. | 작성 완료 | `apps/workflow_engine/.venv/Scripts/python.exe -m pytest apps/workflow_engine/tests/e2e/test_model_routing_policy_e2e.py` | 통과 |
| FR-011 | Evidence adapters | `apps/workflow_engine/tests/services/test_model_routing_evidence.py` | operational/replay 출처 분리, candidate fingerprint, schema/downstream/quality safe summary | 작성 완료 | `apps/workflow_engine/.venv/Scripts/python.exe -m pytest apps/workflow_engine/tests/services/test_model_routing_evidence.py` | 통과 |
| FR-011 | Eligibility/optimizer | `apps/workflow_engine/tests/services/test_model_routing_eligibility.py`, `apps/workflow_engine/tests/services/test_model_routing_policy_optimizer.py` | fixed model 권고, Hard Gate, validated 후보만 policy에 반영, 예상 순절감 | 작성 완료 | 두 targeted pytest 파일 실행 | 통과 |
| FR-011 | Prior-guided compiler/runtime | `apps/workflow_engine/tests/services/test_model_routing_prior_guided_policy.py`, `apps/workflow_engine/tests/nodes/test_llm_node_runtime.py` | 모델 catalog·운영 집계 기반 profile 생성, semantic embedding 미호출, generic rule 선택, credential 후보 제한 | 작성 완료 | targeted pytest | 통과 |
| FR-011 | Gateway policy API | `apps/gateway/tests/api/cost_optimizer/test_cost_optimizer_api.py` | policy 조회/설정/갱신/preview, 입력군 endpoint 미제공, safe summary | 작성 완료 | targeted pytest | 통과 |
| FR-011 | Redeployment policy inheritance | `apps/shared/tests/services/test_model_routing_policy_inheritance.py`, `apps/gateway/tests/services/test_deployment_preflight.py::test_active_redeployment_inherits_model_routing_state` | 새 활성 deployment는 같은 자동 라우팅 LLM node이면서 cohort/evidence node fingerprint가 새 snapshot과 모두 일치할 때만 active policy, cohort, 대표 예문, evidence를 복제하고 cohort ID 참조를 새 row로 재연결한다. 설정이 달라지면 상속하지 않는다. 운영 run 카운터와 refresh 예약 상태는 초기화한다. | 작성 완료 | `PYTHONPATH=$(git rev-parse --show-toplevel) apps/gateway/.venv/Scripts/python.exe -m pytest apps/shared/tests/services/test_model_routing_policy_inheritance.py apps/gateway/tests/services/test_deployment_preflight.py` | 이번 변경 targeted test |
| FR-011 | DB adaptive routing integration | 실제 PostgreSQL + worker가 만든 Replay row에서 proposal, active policy, 서로 다른 cohort runtime 모델과 trace까지 연결 | 부분 완료 | 실제 Provider 50회 실험 보고서 | 통과한 deployment/seed에 한정됨. 독립 PostgreSQL integration pytest는 미작성 |
| FR-011 | Workflow-Aware analysis UI | `apps/client/app/features/workflow/tests/costOptimizer/fr11-workflow-aware-routing.test.tsx` | 적합성/evidence gap/policy diff UI | 미작성 | targeted Vitest | 미실행 |
| FR-011 | Prior-guided routing trace UI | `apps/client/app/features/workflow/tests/costOptimizer/fr11-prior-guided-routing-trace.test.tsx`, `apps/client/app/features/workflow/components/logs/LogDetail.test.tsx` | 테스트 실행과 실행 로그에서 전략 ID, 입력 길이 profile, 선택/fallback 모델, rule/reason, 검토/제외 모델 수, 품질 하한, 예상 비용·지연을 표시하고 입력군·유사도를 표시하지 않음 | 작성 완료 | targeted Vitest | 통과 |
| FR-011 | Three-arm routing value benchmark | `tests/experiments/test_fresh_routing_benchmark.py`, `scripts/experiment_fresh_routing_benchmark.py` | 대표·Replay 입력과 겹치지 않는 동일 holdout을 고성능 고정, 저비용 고정, 자동 라우팅에 실행한다. 성공/schema/downstream, blind pairwise 품질, 비용/지연, rule 적용 범위·적용 정확도, 고위험 보호율, 검증 비용과 손익분기를 함께 보고한다. 안전하게 기본 모델로 닫힌 `no_match`/`ambiguous`는 오분류와 구분한다. | 작성 완료 | targeted pytest + `--confirm-live` 실제 Provider 실행 | 1차 실제 Provider 48건: 비용 15.3% 절감·고성능 대비 품질 +0.7·저비용 대비 품질 +8.8, 다만 보안 입력 2건 오분류로 안전성 미달. 안전 보호 입력군 catalog 유지 수정 후 재실험 예정. 1차 보고서 `reports/model-routing/fresh-routing-benchmark/routing-value-live-20260715-v1/report.md` |
| FR-011 | Gateway policy API | `apps/gateway/tests/api/cost_optimizer/test_cost_optimizer_api.py` | GET/PATCH/POST policy의 builder 권한, 상태 조회, 주기 변경, refresh 예약과 마지막 갱신 safe summary를 검증 | 작성 완료 | `apps/gateway/.venv/Scripts/python.exe -m pytest apps/gateway/tests/api/cost_optimizer/test_cost_optimizer_api.py` | 통과 |
| FR-011 | Actual provider verification | `scripts/verify_model_router_actual.py` | fake LLM client 없이 실제 provider 응답과 usage를 기록하고, OpenAI/Anthropic/Google preset 또는 명시 모델로 LLM judge 품질평가를 실행한 뒤 `workflow_node_runs.trace_metadata.schema_status/downstream_status`에 반영하고 cheap/mid/high 라우팅 판정을 검증 | 수동 검증 대기 | `apps/workflow_engine/.venv/Scripts/python.exe scripts/verify_model_router_actual.py --dry-run`, 실제 호출은 `apps/workflow_engine/.venv/Scripts/python.exe scripts/verify_model_router_actual.py --provider <provider>` | 로컬 계정에서 OpenAI/Anthropic/Google credential 사용 권한이 없어 dry-run이 `credential_use_denied`/`credential_not_available`로 중단됨. 실제 provider 호출은 실행하지 않음 |
| FR-012 | Gateway/service | `apps/gateway/tests/api/cost_optimizer/test_parameter_recommendations_api.py`, `apps/gateway/tests/api/cost_optimizer/test_cost_optimizer_api.py` | 운영 로그/trace 기반 LLM 파라미터 추천 룰셋, safe evidence, `direct_policy_update` 적용 경계 | 작성 완료 | `PYTHONPATH=$(git rev-parse --show-toplevel) apps/gateway/.venv/Scripts/python.exe -m pytest apps/gateway/tests/api/cost_optimizer/test_parameter_recommendations_api.py` | 통과 기록 있음 |
| FR-012 | Frontend component/API client | `apps/client/app/features/workflow/tests/costOptimizer/fr8-apply-api-client.test.ts`, `apps/client/app/features/workflow/tests/costOptimizer/fr2-entry-to-baseline-connection.test.tsx` | 최적화 추천 모달, 추천 row 선택, 테스트하기 CTA, `direct_policy_update`만 직접 적용 | 작성 완료 | 관련 targeted test | 통과 기록 있음 |
| FR-013 | Frontend component | `apps/client/app/features/workflow/tests/costOptimizer/fr13-recommendation-inline-verification.test.tsx`, `apps/client/app/features/workflow/tests/costOptimizer/fr6-playground-mode-switch.test.tsx` | 모달 내부 state 전이, 기준 실행 안내, 독립 metric bar, schema/downstream/quality 상태, scroll/sticky footer, apply/detail/close 액션, comparison/candidate deep link 결과 분석 진입 | 작성 완료 | `cd apps/client && npm run test -- --run app/features/workflow/tests/costOptimizer/fr13-recommendation-inline-verification.test.tsx app/features/workflow/tests/costOptimizer/fr6-playground-mode-switch.test.tsx` | 통과 |
| FR-013 | Frontend API client | `apps/client/app/features/workflow/tests/costOptimizer/fr13-recommendation-verification-api-client.test.ts` | verify endpoint, Idempotency-Key, response type, 상세 분석 deep link | 작성 완료 | `cd apps/client && npm run test -- --run app/features/workflow/tests/costOptimizer/fr13-recommendation-verification-api-client.test.ts` | 통과 |
| FR-013 | Gateway API/service | `apps/gateway/tests/api/cost_optimizer/test_cost_optimizer_api.py`, `apps/gateway/tests/api/cost_optimizer/test_recommendation_verification_api.py`, `apps/gateway/tests/services/test_cost_optimizer_output_quality_service.py` | latest success 또는 사용자 선택 baseline, recommendation stale 검증, candidate/judge usage, 품질 평가와 이력, schema/downstream gate, apply payload | 작성 완료 | `PYTHONPATH=$(git rev-parse --show-toplevel) apps/gateway/.venv/Scripts/python.exe -m pytest apps/gateway/tests/api/cost_optimizer/test_cost_optimizer_api.py apps/gateway/tests/api/cost_optimizer/test_recommendation_verification_api.py apps/gateway/tests/services/test_cost_optimizer_output_quality_service.py` | 통과 |
| FR-014 | deployment/service | `apps/gateway/tests/services/test_deployment_parameter_optimization_service.py` | 비활성 배포도 실제 LLM node 대상 메타데이터를 보존하고 no-LLM 비활성 배포는 허용한다. 활성 설정의 비 LLM/missing node는 거부하며, 대상 node의 실제 검증 비용만 누적하고 한도 도달 뒤 새 검증을 차단한다. | 작성 완료 | targeted pytest | 통과 |
| FR-014 | frontend deployment/manage | `apps/client/app/features/workflow/components/deployment/DeploymentFlowModal.test.tsx`, `apps/client/app/features/workflow/components/deployment/AutomaticOptimizationManagementModal.test.tsx`, `apps/client/app/features/workflow/tests/costOptimizer/fr14-dashboard-automatic-optimization-management.test.tsx` | 배포 모달은 자동 최적화 단계를 생략하고 비활성 기본값으로 즉시 배포한다. 워크플로우 리스트 보기의 배포별 `관리`는 정확한 deployment 설정을 조회해 관리 modal을 열고, 그리드 카드는 자동 최적화 영역을 숨기며, 관리 modal은 대상 node를 보존한 채 주기/예산을 수정한다. | 작성 완료 | targeted Vitest | 통과 |
| FR-015 | Workflow Engine service | `apps/workflow_engine/tests/services/test_constraint_difficulty_router.py` | 의미 필드 비사용, 실행 주체 모델 권한, context/strict output Hard Gate, 다차원 signature, tier에 맞는 안전 모델과 fallback, 필수 입력·파일 입력, 증거 격리, trace | 작성 완료 | `PYTHONPATH=$(git rev-parse --show-toplevel) apps/workflow_engine/.venv/Scripts/python.exe -m pytest apps/workflow_engine/tests/services/test_constraint_difficulty_router.py` | 통과 |
| FR-015 | Experiment | `tests/experiments/test_constraint_difficulty_routing_experiment.py` | 3 workflow×20 입력×4전략, 실제 `SemanticRouteMatcher` fixture, 입력·모델 result matrix, RAG 1회 재사용, 실패 표본 포함 지표, 채택 기준 자동 판정 | 작성 완료 | `PYTHONPATH=$(git rev-parse --show-toplevel) apps/workflow_engine/.venv/Scripts/python.exe -m pytest tests/experiments/test_constraint_difficulty_routing_experiment.py` | 통과 |

## Workflow-Aware Adaptive Routing Tests

> 현재 FR-011의 신규 기준은 bootstrap 난이도 routing이다. 아래 legacy semantic cohort test는
> 이력/호환 범위이며 신규 bootstrap 완료 판정에는 사용하지 않는다.

| ID | 구분 | Given | When | Then | 코드 |
| --- | --- | --- | --- | --- | --- |
| FR-011-B01 | 로그 없음 | 같은 작업 지문의 성공 운영 run이 없다 | bootstrap을 생성한다 | Planner 합성 표본으로 `synthetic` artifact와 3개 난이도 분류기가 생성된다 | `test_model_routing_bootstrap.py` |
| FR-011-B02 | 부분 로그 | 성공 운영 run이 1~11건이다 | bootstrap을 생성한다 | 운영 표본을 우선 사용하고 부족한 난이도만 합성 표본으로 보완한 `hybrid` artifact가 생성된다 | `test_model_routing_bootstrap.py` |
| FR-011-B03 | 충분한 로그 | 성공 운영 run이 12건 이상이다 | bootstrap을 생성한다 | 최대 24개 대표 운영 표본만 사용하며 합성 표본은 만들지 않는다 | `test_model_routing_bootstrap.py` |
| FR-011-B04 | 작업 지문 | 모델만 바뀌거나 prompt/RAG/schema가 바뀐다 | fingerprint를 계산한다 | 모델만 바뀌면 같고, 실제 작업 계약이 바뀌면 달라진다 | `test_model_routing_bootstrap.py` |
| FR-011-B05 | legacy bootstrap 전체 후보 선택 | ready legacy bootstrap artifact, 전역 profile catalog, 실행 주체가 사용할 수 있는 여러 모델이 있다 | 과거 호환 테스트를 실행한다 | legacy 분류 확률과 같은 입력 길이의 node-local 성적을 사용해 현재 사용 가능 후보를 다시 점수화한다. 이 결과는 신규 learner version 발행 근거로 사용하지 않는다 | `test_model_routing_bootstrap_runtime.py` |
| FR-011-B05A | 전역 profile 후보 점수 | 실행 주체가 사용할 수 있는 4개 이상의 catalog 모델과 난이도 확률이 있다 | `ModelRoutingGlobalProfileScorer.rank()`를 호출한다 | 모든 후보의 품질 하한, 예상 비용, 예상 지연 시간, fallback 위험을 계산하고 난이도별 품질 floor를 통과한 후보 중 최종 모델을 선택한다. 가격순 3개 대표 모델로 축소하지 않는다 | `test_model_routing_global_profiles.py` |
| FR-011-B05B | 노드별 운영 성적 보정 | 같은 policy/model/input length profile에 성공·schema·downstream·비용·지연 성적이 쌓였다 | 같은 input profile로 점수화한다 | 전역 profile은 그대로 두고 해당 노드 성적만 posterior 품질, 비용, 지연, fallback 점수에 반영한다. 다른 input profile 또는 다른 policy 성적은 섞지 않는다 | `test_model_routing_global_profiles.py`, `test_model_routing_operational_performance.py` |
| FR-011-B05C | bootstrap profile 매칭 | bootstrap 생성 시 실행 가능한 catalog 모델이 여러 개다 | 난이도별 초기 선택을 만든다 | economy/balanced/advanced마다 모든 모델을 전역 profile로 비교하고 선택 모델, 비교 모델 수, 품질 하한, 비용, 지연, profile 출처를 safe summary로 저장한다 | `test_model_routing_global_profiles.py`, `test_model_routing_bootstrap.py` |
| FR-011-B05D | 실행 주체 권한 변경 | 저장 기본 모델은 더 이상 사용할 수 없지만 global profile snapshot 안에 다른 사용 가능 후보가 있다 | bootstrap runtime을 평가한다 | 기본 모델 부재로 실패하지 않고 현재 사용 가능한 후보만 다시 점수화해 선택한다 | `test_model_routing_bootstrap_runtime.py` |
| FR-011-B06 | 안전성 | Planner/분류기/trace에 raw input, prompt, credential, KB 원문이 있다 | artifact/API/trace를 확인한다 | safe summary와 feature hash만 저장/반환한다 | Gateway integration test 추가 필요 |
| FR-011-B07 | 배포 후 재평가 | 성공 운영 run이 설정 주기만큼 쌓이고 monthly budget이 남아 있다 | refresh task를 실행한다 | 제한된 replay 결과가 품질 gate를 통과한 난이도 모델만 교체한다 | 통합 test 추가 필요 |

이 섹션은 FR-011의 새 완료 기준이다. 단위 테스트에서 모델별 profile dict를 미리
주입해 rule evaluator를 통과시키는 것만으로 E2E 통과로 판정하지 않는다.

### Adaptive evidence pipeline

| ID | 검증 영역 | Given | When | Then |
| --- | --- | --- | --- | --- |
| FR-011-A01 | source separation | 같은 node에 운영 run 20개와 Replay candidate 8개가 있다 | evidence를 수집한다 | `operational=20`, `replay=8`로 분리하며 합산 sample count를 운영 품질 표본처럼 사용하지 않는다. |
| FR-011-A02 | replay adapter | candidate가 성공하고 schema/downstream/quality 결과와 비용/latency를 가진다 | Replay evidence를 정규화한다 | baseline/candidate model, cohort, paired metric, source, fingerprint를 가진 safe summary를 만든다. raw prompt/output은 포함하지 않는다. |
| FR-011-A03 | stale replay | candidate의 node fingerprint가 현재 deployment와 비교 불가능하다 | evidence를 수집한다 | candidate를 `rejected` 또는 evidence gap으로 분류하고 policy 입력에서 제외한다. |
| FR-011-A04 | credential Hard Gate | 조직에는 모델이 있지만 execution subject가 credential `use` 권한이 없다 | candidate를 수집한다 | scoring 전에 후보와 fallback에서 제외한다. |
| FR-011-A05 | capability Hard Gate | 모델이 node의 context/output/schema/tool 요구사항을 만족하지 않는다 | candidate를 수집한다 | `blocked_capability` reason으로 제외한다. |
| FR-011-A06 | eligibility eligible | 실행 가능한 검증 후보가 둘 이상이고 품질 계약과 예상 순절감 근거가 있다 | 적합성을 분석한다 | `eligible`과 근거/evidence gap/예상 순절감을 반환한다. |
| FR-011-A07 | fixed model | 후보가 하나뿐이거나 라우팅 평가 비용까지 포함한 순절감이 0 이하이다 | 적합성을 분석한다 | 오류가 아닌 `fixed_model_recommended`를 반환하고 rule을 만들지 않는다. |
| FR-011-A08 | needs evidence | 후보는 있으나 paired Replay 또는 품질 score가 부족하다 | 적합성을 분석한다 | `needs_evidence`와 필요한 모델/cohort/sample을 반환한다. |
| FR-011-A09 | structured quality gate | JSON/schema node 후보가 schema 또는 downstream contract에 실패한다 | candidate를 검증한다 | 비용이 싸도 `rejected`이고 active policy에 포함하지 않는다. |
| FR-011-A10 | free-form quality gate | 자유형 출력 후보의 Judge score/confidence가 gate보다 낮다 | candidate를 검증한다 | Judge 설명은 저장할 수 있지만 후보는 `validated`가 되지 않는다. |
| FR-011-A11 | deterministic optimizer | 두 validated 후보가 있고 cohort별 품질/비용/latency와 traffic share가 있다 | proposal을 생성한다 | 품질 floor를 먼저 적용한 뒤 예상 순절감이 가장 큰 검증 후보를 선택한다. Judge JSON을 직접 policy로 사용하지 않는다. |
| FR-011-A12 | cohort retention | 한 cohort만 저비용 후보의 근거가 충분하다 | proposal을 생성한다 | 해당 cohort만 새 모델 rule을 만들고 나머지는 현재 모델을 유지한다. |
| FR-011-A13 | evidence-free refresh | 새 validated evidence나 drift가 없다 | refresh를 실행한다 | `kept_current`이고 policy/evidence version을 불필요하게 변경하지 않는다. |
| FR-011-A14 | replay trigger | 새 validated Replay candidate가 저장된다 | refresh eligibility를 평가한다 | 고정 N회를 기다리지 않고 `validated_replay_created` 재평가 대상이 된다. |
| FR-011-A15 | runtime isolation | active policy가 있다 | 일반 workflow를 실행한다 | Judge/optimizer/evidence query를 호출하지 않고 저장 rule만 평가하며 `judge_called=false`를 남긴다. |
| FR-011-A16 | decision trace | cohort rule로 후보 모델이 선택된다 | node run trace를 저장한다 | strategy, selected/fallback model, matched cohort/rule, policy/evidence/gate version, reason을 확인할 수 있다. |
| FR-011-A17 | actual DB path | DB에 baseline experiment와 validated candidate를 만들고 refresh한다 | 서로 다른 두 cohort 입력으로 runtime을 실행한다 | 검증 cohort는 후보 모델, 근거 부족 cohort는 현재 모델을 선택하며 실제 trace가 이를 증명한다. |
| FR-011-A18 | gate failure E2E | 더 싼 후보가 schema 또는 quality gate에 실패한다 | DB 통합 흐름을 실행한다 | 후보가 active policy에 들어가지 않고 현재 모델이 유지된다. |
| FR-011-A19 | provider fallback | selected model 호출이 실패하고 검증된 사용 가능 fallback이 있다 | runtime을 실행한다 | fallback을 한 번 사용하고 원인 code를 trace에 남긴다. 미검증/권한 없는 모델로 확장하지 않는다. |
| FR-011-A20 | secret safety | evidence와 Judge input 원천에 raw prompt/output/RAG chunk/credential이 있다 | safe summary를 생성한다 | 원문과 secret이 API, update row, trace, test snapshot에 남지 않는다. |
| FR-011-A21 | semantic catalog compile | 세 Route와 검토된 synthetic 대표 문장, encoder가 있다 | catalog를 활성화한다 | 대표 문장을 한 번 embedding해 versioned internal catalog에 저장하고 API summary에서는 vector/원문을 제외한다. |
| FR-011-A22 | Aurelio-style route score | query가 한 Route의 여러 대표 문장과 가깝다 | semantic matcher를 실행한다 | 전체 top-k를 Route별로 묶고 기본 mean 점수가 가장 높은 Route를 선택한다. |
| FR-011-A23 | threshold rejection | 최고 Route 점수가 해당 threshold보다 낮다 | semantic matcher를 실행한다 | `no_match`로 닫고 active policy의 default model을 사용한다. |
| FR-011-A24 | ambiguous margin | 1위와 2위 Route 점수가 모두 높지만 차이가 min-margin보다 작다 | semantic matcher를 실행한다 | `ambiguous`로 닫고 저비용 Route를 임의 선택하지 않는다. |
| FR-011-A25 | validated model mapping | semantic cohort는 일치하지만 연결 모델의 Replay/품질 gate가 통과하지 않았다 | policy proposal을 만든다 | 해당 모델을 rule에 연결하지 않고 현재 모델을 유지한다. |
| FR-011-A26 | semantic runtime isolation | active policy에 사전 계산 Route vector가 있다 | 같은 policy로 여러 입력을 실행한다 | query만 실행당 한 번 embedding하고 대표 문장은 다시 embedding하지 않으며 Judge/optimizer를 호출하지 않는다. |
| FR-011-A27 | encoder failure fallback | encoder credential을 사용할 수 없거나 embedding 호출이 실패한다 | LLM node를 실행한다 | provider 호출 전 routing을 실패시키지 않고 검증된 default model로 닫으며 safe reason을 trace에 남긴다. |
| FR-011-A28 | semantic decision trace | semantic Route가 threshold와 margin을 통과한다 | node run trace와 프론트 상세를 확인한다 | Route label, similarity, threshold, margin, catalog version, 실제 모델과 사용자 친화 근거를 표시하고 query/vector는 노출하지 않는다. |
| FR-011-A29 | 60-query deployed holdout accuracy | calibration 및 대표 문장과 겹치지 않는 3개 입력군의 60개 holdout payload와 기대 label이 있다 | 실제 배포 endpoint를 호출한다 | 전체 cohort 정확도 90% 이상, 고위험 recall 95% 이상, 권한 없는 모델 선택 0건, trace 필수 필드 100%를 만족한다. |
| FR-011-A30 | experiment feedback loop | calibration 또는 holdout 기준 중 하나라도 실패한다 | Route별 후보·점수·탈락 원인을 분석한다 | 대표 문장 범위와 threshold/min-margin을 calibration으로만 수정하고, 최종 평가는 문장이 겹치지 않는 새 holdout으로 재실행한다. |
| FR-011-A31 | rejected Route diagnostics | 최고 Route가 있지만 threshold 또는 min-margin을 통과하지 못한다 | semantic matcher와 실행 상세를 확인한다 | 확정 cohort는 비워 default model을 유지하되 최고 후보 Route의 id/label/점수는 표시하고 input 원문과 vector는 노출하지 않는다. |
| FR-011-A32 | threshold calibration isolation | label이 있는 calibration set과 별도 holdout set이 있다 | Route threshold/min-margin을 탐색한다 | calibration 성능으로만 기준값을 선택하고 holdout 입력을 학습 대표 문장이나 기준 탐색에 사용하지 않는다. |
| FR-011-A33 | Judge catalog prompt budget | active policy에 여러 Route의 representative hash와 embedding vector가 있다 | 정책 갱신 Judge prompt를 만든다 | current policy에는 catalog version, Route id/label/threshold, representative count만 포함하고 hash/vector는 제외한다. |
| FR-011-A34 | experiment dataset separation | 실험 스크립트에 calibration과 final holdout을 등록한다 | 데이터셋 계약 테스트와 CLI 선택을 확인한다 | 두 데이터셋은 각각 3개 cohort별 20개, 총 60개이며 input 문장 교집합이 0개다. `execute` 최종 보고는 holdout을 사용하고 calibration은 threshold/min-margin 보정에만 사용한다. |
| FR-011-A35 | semantic input path isolation | payload에 업무 본문과 customer tier, 실행 식별자가 함께 있다 | catalog의 `input_paths`로 semantic query를 만든다 | 지정한 업무 본문 값만 embedding하고 주변 metadata와 객체 key는 제외한다. 지정 path가 비면 semantic routing을 unavailable로 닫는다. |
| FR-011-A36 | centroid route scoring | 잘못된 Route에 query와 우연히 매우 가까운 대표 문장 하나가 있지만 Route 전체 중심은 멀다 | `centroid` matcher를 실행한다 | 단일 대표 문장 최고점이 아니라 사전 계산한 Route centroid와의 cosine similarity로 올바른 Route를 선택한다. |
| FR-011-A37 | embedding experiment transient retry | 실제 encoder가 429, 5xx, timeout 또는 connection 오류를 일시적으로 반환한다 | calibration/holdout preflight를 실행한다 | 제한된 횟수만 지수 backoff로 재시도하고, 인증·계약 오류 같은 비일시 오류는 즉시 실패한다. |
| FR-011-A38 | independent Replay evidence acquisition | calibration/holdout과 겹치지 않는 routine·billing 증거 입력이 cohort별 5개 있고 현재 배포 node 설정과 실행 가능한 후보 모델이 있다 | 실험 스크립트의 evidence 수집 mode를 실행한다 | 실제 배포 endpoint로 baseline을 만든 뒤 동일 baseline input으로 Cost Optimizer compare API를 호출한다. routine은 `gpt-4o-mini`, billing은 `gpt-4.1-mini` 후보를 각각 5회 실행하며 실제 candidate/Judge 비용과 품질 결과를 저장한다. 임의 DB insert와 final holdout 재사용은 금지한다. |
| FR-011-A39 | untouched final deployment evaluation | threshold 보정, 1차 holdout 확인, Replay 증거 수집에 사용하지 않은 입력이 semantic cohort별 20개씩 있다 | 최종 실험 데이터셋 계약을 검증하고 실제 배포 endpoint로 60개를 한 번 실행한다 | 최종 입력 60개는 기존 calibration·holdout·Replay evidence와 문장 교집합이 없고 cohort별 20개로 균형을 이룬다. 결과를 확인한 뒤 같은 입력으로 기준을 다시 조정해 최종 성능이라고 보고하지 않는다. |
| FR-011-A40 | policy-derived expected model | 최종 실행 직전에 active policy snapshot을 고정했고 일부 cohort만 검증된 model rule을 가진다 | 각 입력의 사람이 정한 기대 cohort로 예상 모델을 계산한다 | 해당 cohort 전용 rule이 있으면 rule 모델을, 없으면 active default 모델을 예상값으로 사용한다. 실험 코드에 cohort별 모델을 하드코딩하지 않는다. 실행 중 policy refresh로 기대값이 바뀌지 않게 snapshot/version을 보고서에 남긴다. |
| FR-011-A41 | optimizer rejection evidence summary | Replay 후보가 품질 gate에서 거절되었고 후보·baseline 보수적 품질 하한값이 계산됐다 | policy refresh 결과를 저장한다 | 선택 모델 유지 여부뿐 아니라 후보와 baseline의 품질 하한값을 safe optimizer summary에 남겨 `quality_floor_failed` 근거를 UI·보고서에서 설명할 수 있다. |
| FR-011-A42 | calibrated ambiguity safety margin | calibration과 1차 validation에서 결제·고위험 Route가 모두 높은 점수를 받는 입력이 있다 | `min_margin` 후보값별 모델 선택 결과를 비교한다 | 결제 cohort의 검증된 저비용 모델 선택을 유지하면서 고위험 입력이 저비용 모델로 내려가지 않는 가장 작은 안전 여백을 catalog에 버전과 함께 고정한다. 점수 차이가 안전 여백보다 작으면 `ambiguous`로 닫고 default 모델을 사용한다. |
| FR-011-A43 | policy-driven hybrid safety override | dense embedding은 일반 결제 Route를 가리키지만 active catalog의 safety Route에만 사고 signal과 threshold가 있다 | 동일 입력 원문과 query vector로 hybrid matcher를 실행한다 | signal threshold를 통과하면 safety Route를 우선 선택한다. signal이 없거나 일반 결제 표현만 있으면 dense Route를 유지한다. runtime 코드에는 도메인 keyword 상수가 없고 trace에는 signal 원문 대신 matcher, 점수, 매칭 개수만 남는다. |
| FR-011-A44 | candidate actual-model guard | 후보 Replay가 `gpt-5-nano`를 요청했지만 provider 응답의 `model`이 `gpt-4.1`이고 runtime fallback trace가 없을 수 있다 | validation item 결과를 저장하고 cohort gate를 계산한다 | `execution_summary.requested_model_id=gpt-5-nano`, `actual_model_id=gpt-4.1`, `fallback_used=true`를 저장한다. 이 표본은 schema/downstream/judge 결과와 무관하게 `fallback_gate_failed`로 후보 승격에서 제외한다. |
| FR-011-A45 | manual cohort wizard | builder가 대표 문의 하나만 입력했다 | wizard를 실행한다 | 이름, 영문 key, 고위험 여부와 어순·표현·상황이 다른 중복 없는 합성 예문 3~5개를 반환한다. 일반 입력군은 3개, 고위험 입력군은 5개 미만이면 저장할 수 없다. 사용자는 예문을 삭제할 수 있으며 policy rule과 selected model은 변경하지 않는다. |
| FR-011-A46 | manual cohort capacity | active/proposed/validating 입력군이 policy `max_cohorts`에 도달했다 | 직접 입력군을 등록하거나 자동 발견을 실행한다 | 새 입력군을 만들지 않는다. dormant/retired 입력군은 한도에 포함하지 않는다. |
| FR-011-A47 | manual cohort activation guard | 대표 문의로 직접 입력군을 생성했다 | 바로 다음 runtime을 실행한다 | `proposed` 상태만으로는 저비용 rule에 매핑되지 않으며, 운영 관찰과 Replay/Judge gate 뒤에만 `active`가 된다. |
| FR-011-A48 | cohort lifecycle | 비고정 입력군 traffic share가 3개 점검 구간 연속 5% 이하다 | lifecycle을 갱신한다 | `dormant`가 되고 이후 10% 이상으로 회복하면 `active`, 90일 동안 휴면이면 `retired`가 된다. `fixed=true`인 직접 등록 입력군과 안전 보호 입력군은 자동 휴면 처리하지 않는다. |
| FR-011-A49 | pre-deployment cohort draft | 자동 라우팅을 켰지만 아직 policy row가 없는 workflow draft다 | 직접 입력군을 생성·수정·삭제한다 | provider 호출 없이 graph의 `cohort_drafts`를 갱신하고 stable UUID와 `status=draft`를 반환한다. |
| FR-011-A50 | draft cohort materialization | 배포 snapshot에 직접 입력군 초안 3개가 있다 | 자동 라우팅 설정을 포함해 배포를 commit하고 bootstrap task를 실행한다 | 첫 운영 실행 전에 기본 policy row를 만들고, 초안 UUID를 보존한 `source=manual`, `status=proposed` DB row와 대표 문의·합성 예문 각각의 embedding을 만든다. embedding이 일시 실패해도 배포는 유지되며 task 재시도·수동 갱신·다음 성공 운영 실행에서 다시 처리한다. |
| FR-011-A51 | excluded model boundary | node draft의 `excluded_model_ids`에 후보 모델이 있다 | bootstrap, runtime resolve, Replay validation, policy refresh를 실행한다 | 제외 모델은 어떤 단계에서도 기본·fallback·검증 후보·active rule로 선택되지 않는다. |
| FR-011-A52 | remote 61-run trend experiment | `팀별 온보딩 문서 접근 제어`를 복제한 demo workflow에 Luna 기본 모델, 입력군 초안 3개, 5회 점검 주기, 월 $3 검증 한도가 있다 | `scripts/experiment_team_onboarding_adaptive_routing.py --mode execute --confirm-live`를 원격 API에 실행한다 | DB 직접 접근 없이 61건을 실행하고 재무 입력군 자동 발견, 비고정 영업 입력군 휴면과 재활성화 판정 결과, 모델 분포, RAG 출처, policy/validation 변화를 JSON과 Markdown 보고서로 남긴다. 재활성화 기준이 미충족이면 이를 성공으로 꾸미지 않고 관찰 수·traffic share·점검 구간을 근거로 남긴다. 보수적 provider 호출 상한은 500회 이하이고 제한 시간은 120분 이하다. |
| FR-011-A53 | adaptive route match boundary | 각 입력군에 예문 3~5개와 다른 입력군의 반례 vector가 있다 | 적응형 runtime catalog와 observation matcher를 실행한다 | 같은 입력군 내부 유사도 하한과 다른 입력군 유사도 상한으로 입력군별 threshold를 계산한다. 예문이 3개 미만이면 보수적 기본값을 유지한다. 분포 간격이 0.05 미만이면 `conservative_overlap`을 사용하되 threshold가 양성 유사도 하한을 넘지 않는다. runtime과 관찰 저장은 같은 aggregation·threshold와 1·2위 최소 점수 차이를 사용한다. |
| FR-011-A57 | overlapping calibration boundary | 입력군별 예시는 3개 이상이지만 내부 최저 유사도와 다른 입력군 최고 유사도의 간격이 0.05 미만이다 | route threshold를 calibration한다 | 측정값과 무관한 공통 기본값으로 되돌리지 않고 `conservative_overlap` 상태를 저장한다. threshold는 `min(positive_floor, negative_ceiling + 0.05)`이며, 최고 route가 이 기준과 1·2위 최소 점수 차이를 모두 통과할 때만 route를 사용한다. |
| FR-011-A55 | multiple representative examples | 일반 입력군에 예문 3개 이상, 고위험 입력군에 예문 5개가 저장됐다 | 운영 문의를 관찰 저장 및 runtime에서 판정한다 | 입력군별 예문 유사도 상위 2개 평균으로 동일한 cohort를 선택하고, 예문 하나의 우연한 고점만으로 확정하지 않는다. |
| FR-011-A56 | high-risk replay gate | 안전 보호 고위험 입력군에 저비용 후보의 Replay 결과가 있다 | 후보 evidence를 판정한다 | Replay 5개 미만, 품질 신뢰도 0.85 미만, 품질 점수 하한 85점 미만 또는 기준 모델 대비 평균 품질 하락이 있으면 저비용 모델을 active rule에 넣지 않는다. |
| FR-011-A58 | unvalidated safety cohort runtime boundary | 안전 보호 입력군의 저비용 후보가 품질 gate를 통과하지 못해 cohort가 `proposed`이고 active model rule이 없다 | runtime semantic catalog를 생성하고 해당 입력을 실행한다 | 안전 보호 입력군은 catalog에서 제거되지 않는다. semantic match는 유지하되 저비용 rule을 만들지 않고 고성능 기본 모델로 실행한다. 일반 `proposed` 입력군은 catalog와 runtime rule에 들어가지 않는다. |
| FR-011-A59 | semantic aggregation consistency | node semantic router가 `aggregation=centroid`, `top_k=3`, `min_margin=0.08`로 배포됐다 | 적응형 입력군의 threshold를 calibration하고 runtime catalog를 생성한다 | policy가 설정을 `top_k_mean`으로 덮어쓰지 않는다. calibration, 관찰 저장과 runtime matcher가 모두 centroid와 동일한 margin을 사용한다. |
| FR-011-A60 | calibrated dense safety guard | 일반 입력군 점수가 가장 높지만 안전 입력군 dense score도 calibration의 `negative_ceiling + min_margin` 이상인 입력이 있다 | runtime catalog를 생성하고 semantic matcher를 실행한다 | safety Route에 `dense_override_threshold=max(route.threshold, negative_ceiling + min_margin)`를 저장하고 안전 입력군을 우선 선택한다. 경계 미달인 일반 입력은 기존 일반 Route를 유지하며 runtime 코드에는 도메인 keyword 상수가 없다. |
| FR-011-A61 | independent economic holdout after safety guard | 안전 경계 보정과 이전 경제성 평가에 사용하지 않은 입력군별 12개, 총 48개 V5 입력이 있다 | 고정 고가·고정 저가·자동 라우팅을 동일 입력으로 실행한다 | 자동 라우팅은 고위험 보호율 100%, 검증 모델 정밀도 95% 이상, trace 완전성 100%를 만족하고 고정 고가 대비 paired 비용 절감의 95% 신뢰구간 하한이 0보다 크다. 고정 저가 대비 심각한 품질 저하를 줄인 결과와 검증비 회수 요청 수도 보고서·그래프로 남긴다. |
| FR-011-A62 | per-replay quality outlier gate | 후보의 평균 품질과 보수적 하한은 기준을 넘지만 대표 입력 하나가 baseline보다 10점을 초과해 낮다 | candidate evidence를 평가한다 | 입력군 위험도와 무관하게 `quality_outlier_gate_failed`로 거절하고 `quality_delta_minimum`만 safe summary에 남긴다. 평균 85점 이상, 보수적 하한 76.5점 이상, 기준 대비 평균 하락 2점 이내도 함께 검사한다. |
| FR-011-A66 | quality-margin candidate selection | 같은 입력군에서 품질 하한 87인 후보와 더 싸지만 하한 81.5인 후보가 모두 validated다 | active route 후보를 선택한다 | 최저가 후보를 바로 고르지 않고 최고 품질 하한 3점 이내 후보만 남겨 하한 87인 모델을 선택한다. 더 싼 후보의 하한이 85라면 품질 여유 범위 안이므로 더 싼 모델을 선택한다. |
| FR-011-A67 | factual reliability judge | authoritative evidence가 없는 동일 입력에 한 출력은 확인되지 않은 제품 메뉴·정책을 단정하고 다른 출력은 근거 부족을 명시한다 | blind 품질 Judge를 실행한다 | `factual_reliability`가 필수 dimension으로 포함되고, 근거 없는 단정은 감점하되 안전한 불확실성 표현 자체는 감점하지 않는다. `authoritative_evidence_available`은 safe RAG summary의 `evidence_sufficient=true`와 양의 정수 retrieval count가 모두 확인될 때만 true다. Boolean, string, 소수 count는 false다. |
| FR-011-A68 | bidirectional benchmark judge | 동일 A/B 출력 쌍이 있고 Judge 위치 편향을 확인하려 한다 | 보고서용 품질 평가를 실행한다 | `baseline_left`, `candidate_left` 두 순서로 평가하고 A/B 점수와 confidence를 평균내며 Judge 비용은 두 호출 합계로 보고한다. 한 pass가 실패하면 해당 pair는 품질 평가 불가로 처리한다. |
| FR-011-A69 | runtime Judge 판단 근거 trace | runtime Judge가 모델을 선택한다 | 실행 trace와 노드 상세 UI를 확인한다 | `judge.reason_code`와 `judge.reason_short`만 trace에 저장하고 UI가 이를 한국어 설명으로 표시한다. Judge 자유 문장, 원문 요청·개인정보·RAG 문서 원문은 durable trace에 저장하거나 표시하지 않는다. |
| FR-011-A63 | production active route revalidation and revocation | active 입력군 rule의 현재 모델과 새로운 운영 observation window가 있다 | 자동 또는 수동 정책 갱신을 실행한다 | 현재 active 모델을 새 후보보다 먼저 재검증한다. 실패하면 해당 입력군 rule만 철회하고 default model로 복귀하며, 다른 입력군의 active rule은 유지한다. |
| FR-011-A64 | isolated candidate execution | bootstrap 후보 모델이 JSON 응답을 만들지 못하거나 provider 호출에 실패한다 | 후보 Replay를 실행한다 | `fallback_model_id`를 비운 후보 graph를 사용하고 item을 실패로 기록한다. 기준 모델을 대신 실행한 출력을 후보 품질·비용 증거로 사용하지 않는다. |
| FR-011-A65 | independent holdout after V14 correction | V14까지 사용하지 않은 입력군별 12개, 총 48개 V15 입력이 있다 | 고정 고가·고정 저가·자동 라우팅을 동일 입력으로 실행한다 | 입력군 최소 점수 차이 0.05와 강화한 품질 gate를 적용한다. 자동 라우팅은 고성능 고정 대비 비용을 줄이고 심각 품질 저하 0건, 검증 모델 적합률 95% 이상, 고위험 보호율 100%, trace 기록률 100%를 만족해야 한다. 결과와 경제성 그래프를 별도 보고서에 남긴다. |
| FR-011-A54 | automatic cohort semantic identity | 자동 발견 입력군에 서로 가까운 가림 처리 문의가 3~5개 있고 이름이 `자동 발견 입력군 N`이다 | 정책 갱신의 입력군 이름 생성을 실행한다 | 한국어 업무명과 영문 snake_case key로 바꾸고 active rule/catalog 참조도 함께 갱신한다. 생성 실패 시 기존 식별자를 유지하며 라우팅과 검증은 계속된다. |

### 기존 policy/runtime 회귀 테스트

아래 테스트는 현재 구현 기반을 보호한다. 이 테스트가 모두 통과해도 A01~A20의
evidence pipeline이 없으면 Workflow-Aware Adaptive Routing 구현 완료로 표시하지
않는다. 자동 라우팅 ON 상태의 workflow runtime은 저장된 active policy rule만
평가하며, 런타임 코드는 도메인 키워드 목록을 내장하지 않는다.

| ID | 라우팅 상태 | Given | When | Then |
| --- | --- | --- | --- | --- |
| FR-011-R01 | runtime policy | active policy에 `when.keyword_any=["SLA", "보상", "장애"]` rule이 있고 입력에 해당 단어가 있다 | `ModelRouter.resolve_policy()`를 호출한다 | 런타임 코드의 키워드 상수 없이 해당 policy rule이 매칭되고 rule의 `selected_model_id`를 선택한다. |
| FR-011-R02 | runtime context | 입력에 SLA/보상/장애 같은 도메인 단어가 있지만 active policy에 `keyword_any` rule이 없다 | `ModelRouter.infer_runtime_context()`를 호출한다 | `risk_level`을 자동으로 high로 바꾸지 않는다. 일반 feature인 `output_format`, `schema_required`, `knowledge_enabled`, `input_length_bucket`, `prompt_length_bucket`, `node_task`만 계산한다. |
| FR-011-R03 | bootstrap policy | bootstrap policy를 생성한다 | `default_rule_policy()`를 호출한다 | 배포 node의 저장 `model_id`/`fallback_model_id`만 보존하고 rules는 빈 배열이다. 가격 tier나 모델 capability 추정으로 모델을 변경하지 않는다. |
| FR-011-R04 | judge policy refresh | 최근 운영 profile, 노드 설정, 일반 feature segment 성능을 judge에게 전달한다 | `refresh_policy()`를 호출한다 | raw input/output/credential은 전달하지 않는다. `keyword_any` rule은 차단하고, 조건 rule은 동일 `when`의 segment 품질 gate 근거가 있을 때만 반영한다. |
| FR-011-R05 | runtime non-interference | 자동 라우팅 ON이고 active policy가 있다 | workflow engine이 LLM node를 실행한다 | judge를 호출하지 않고 active policy rule만 평가한다. trace metadata에 `judge_called=false`, policy id/version, matched rule, runtime context safe summary를 남긴다. |
| FR-011-R06 | unavailable rule model | 매칭된 rule의 `selected_model_id`가 현재 사용 가능한 모델 목록에 없다 | `ModelRouter.resolve_policy()`를 호출한다 | 해당 rule을 건너뛰고 default/fallback 정책으로 닫는다. |
| FR-011-R07 | credential guard | 현재 organization/user가 사용할 수 있는 policy default/rule/fallback model이 없다 | `LLMNode._resolve_model_routing_policy()`를 호출한다 | LLM provider 호출 전에 `model_routing_no_available_model` 오류를 반환한다. credential 원문은 노출하지 않는다. |
| FR-011-R07A | workflow 전역 실행 제외 모델 | UI 목록, Cost Optimizer 후보, 저장 graph 또는 policy가 `gpt-5-mini`를 참조한다 | 모델 후보를 조회하고 LLM node를 실행한다 | UI와 라우팅 후보에서 제외하고 Provider 호출 전에 차단한다. 유효한 fallback이 있으면 fallback만 실행한다. |
| FR-011-R08 | policy refresh retention | 정책 갱신 시 judge가 실행 가능한 rule을 만들지 못한다 | `refresh_policy()`를 호출한다 | 기존 active policy를 유지하고 결과를 `pending_review` 또는 `kept_current`로 기록한다. |
| FR-011-R09 | task/category hint | 사용자가 task type을 직접 입력하지 않는다 | router가 context를 구성한다 | 노드에 명시된 `model_routing_context.node_task` 또는 내부 기본 `task_type`을 `node_task`로 사용한다. 도메인 키워드를 task로 추론하지 않는다. |
| FR-011-R10 | prompt/input bucket | 입력과 prompt 길이가 달라진다 | `ModelRouter.infer_runtime_context()`를 호출한다 | `input_length_bucket`, `prompt_length_bucket`이 `short`, `medium`, `long` 중 하나로 계산된다. |
| FR-011-R10A | general condition routing matrix | 입력 길이, 출력 형식, schema 필요 여부, Knowledge Base/Collection, 파일 입력, prompt 길이, customer-facing, node task 조건을 각각 가진 active policy rule이 있다 | `ModelRouter.resolve_policy()`를 각 조건으로 호출한다 | 모든 조건이 runtime context에 반영되고 일치하는 rule의 모델을 선택한다. 조건이 실제로 없으면 default model을 사용한다. |
| FR-011-R10B | empty file guard | 입력 payload에 `file_id=null`, `attachments=[]`가 있거나 실제 `file_id`가 있다 | `ModelRouter.infer_runtime_context()`를 호출한다 | 빈 필드는 파일 입력으로 보지 않고, 값이 있는 `file_id`만 `has_file_input=true`로 판정한다. 정책 생성기와 runtime의 판정이 같다. |
| FR-011-R10C | deployment constraint profile | LLM node가 strict JSON schema와 Knowledge Base의 긴 검색 context를 요구한다 | `PriorGuidedPolicyCompiler.compile()`을 호출한다 | 모든 입력 길이 profile에 strict JSON, schema 복잡도, RAG context 크기, 요구 capability tier가 반영되고 조건을 만족하는 모델만 선택한다. |
| FR-011-R11 | keyword rule guard | judge가 `keyword_any` rule을 생성한다 | 생성된 policy를 정규화한다 | raw 입력·도메인 키워드 근거가 없으므로 rule을 저장하지 않고 기존 policy를 `pending_review` 또는 `kept_current`로 유지한다. |
| FR-011-R12 | canonical terminal profile | 배포 후 운영 실행의 canonical `trace_metadata.llm`에 schema/downstream/fallback/routing context가 있고 workflow가 success 또는 failed로 닫힌다 | `ModelRouter.collect_profile()`을 호출한다 | 성공/실패를 품질 profile에 함께 반영하고, workflow terminal 상태를 downstream fallback 근거로 사용한다. 배포 전 테스트 실행은 집계하지 않는다. |
| FR-011-R13 | high-risk retention | 고객-facing/schema 계약이 있고 보상/SLA/법무 리스크가 큰 입력이 들어온다 | `keyword_any` rule이 포함된 active policy를 평가한다 | 도메인 위험도 판단은 저장된 policy rule로만 수행되고 런타임 상수에는 의존하지 않는다. |
| FR-011-R14 | multi-provider judge | organization이 Anthropic 또는 Google credential만 가지고 있고 해당 provider의 cheap/mid/high/judge model relation과 `use` 권한이 verified 상태다 | `scripts/verify_model_router_actual.py --provider anthropic` 또는 `--provider google`을 실행한다 | OpenAI 모델 hardcode 없이 해당 provider preset으로 실제 후보 실행과 LLM judge 평가를 수행한다. credential이 없으면 provider 호출 전에 명확한 실패 사유를 출력한다. |
| FR-011-R15 | provider dry run | 실제 API key가 없거나 비용 발생 없이 설정만 확인하고 싶다 | `scripts/verify_model_router_actual.py --dry-run`을 실행한다 | provider 호출 없이 현재 organization/user가 실행 가능한 preset, cheap/mid/high model, judge model을 출력한다. |
| FR-011-R16 | 운영 run 누적 | 자동 라우팅 ON인 배포 workflow가 완료되고 target LLM node가 실행됐다 | 완료 hook을 두 번 호출한다 | `workflow_run_id`당 event는 한 건만 저장되고 eligible count는 한 번만 증가한다. |
| FR-011-R17 | 자동 갱신 예약 | 마지막 갱신 후 eligible 운영 run이 `refresh_every_runs - 1`개 누적됐다 | target LLM node가 성공한 다음 배포 후 운영 workflow가 terminal 상태가 된다 | downstream trace를 확정한 뒤 policy status를 `refreshing`으로 전환하고 `auto_n_runs` refresh task를 한 번만 예약한다. 테스트/compare/배포 없는 run은 카운트하지 않는다. |
| FR-011-R18 | 자동 갱신 결과 | judge가 policy를 반환했지만 품질 gate를 통과한 변경 rule이 없다 | refresh task를 실행한다 | update는 `kept_current` 또는 `pending_review`로 저장되고 기존 active policy/version은 유지된다. |
| FR-011-R19 | runtime DB policy | active policy row가 있고 LLM node data에는 legacy policy JSON이 다르다 | 배포 runtime을 실행한다 | DB active policy를 우선 평가하고 trace에 DB policy id/version과 `judge_called=false`를 남긴다. bootstrap classifier 경로라면 `difficulty`, 확률, 0~100 `difficulty_score`도 남긴다. |
| FR-011-R20 | terminal workflow hook | LLM node가 성공했지만 workflow가 아직 `running`이다 | log task가 policy run record hook을 호출한다 | 운영 표본 집계 task를 예약하지 않는다. workflow가 `success` 또는 `failed` terminal 상태가 된 뒤에만 한 번 예약하고 `downstream_status`를 trace에 기록한다. |
| FR-011-R21 | judge usage tracking | policy refresh가 실제 judge를 호출한다 | refresh 결과를 저장한다 | `judge_provider`, `judge_model`, `judge_usage_log_id`, judge 비용 safe summary를 update row와 policy 조회 응답에서 확인할 수 있다. |
| FR-011-R22 | deployed bootstrap | 자동 라우팅 ON인 LLM node를 새로 배포한다 | 배포 transaction을 commit한다 | 첫 운영 실행 전 저장 `model_id`/`fallback_model_id`와 빈 rule을 가진 policy row를 생성하고, commit 뒤 `deployment_bootstrap` 검증 task를 한 번 예약한다. |
| FR-011-R23 | bootstrap candidate promotion | bootstrap rule 또는 fallback에 있던 모델을 새 default/rule primary model로 승격한다 | refresh policy를 정규화한다 | 해당 모델의 독립 운영 품질 표본이 없으면 `pending_review`로 보류하고 기존 active policy를 유지한다. |
| FR-011-R24 | efficiency evidence | 변경 모델과 현재 primary model의 평균 비용/latency가 모두 있다 | refresh policy를 정규화한다 | 변경 모델이 비용과 latency 모두 더 나쁘면 `kept_current`로 기록하고 기존 active policy를 유지한다. |
| FR-011-R25 | deployment bootstrap boundary | draft에서만 자동 라우팅을 ON으로 저장했고 deployment snapshot에는 해당 설정이 없다 | 운영 실행을 완료한다 | policy row/event를 만들거나 refresh를 예약하지 않는다. 자동 라우팅 설정을 포함해 새로 배포한 시점부터 policy row를 만들고 운영 표본은 이후 terminal 실행부터 집계한다. |
| FR-011-R26 | initial manual refresh guard | policy id가 없는 `collecting` 상태다 | LLM node panel을 렌더링한다 | `자동 정책 갱신하기`를 disabled 처리하고 자동 라우팅 설정을 포함해 배포하라는 안내를 표시한다. |
| FR-011-R29 | bootstrap baseline first | 배포된 입력군에 합성 대표 예시가 있고 아직 운영 관찰값은 없다 | `deployment_bootstrap` batch를 실행한다 | 예시마다 저장 기본 모델을 먼저 실행한다. 기준 모델이 schema 또는 downstream 계약을 실패하면 해당 예시의 후보 모델과 Judge를 호출하지 않고 `baseline_failed`로 기록한다. |
| FR-011-R30 | bootstrap paid candidate gate | 기준 모델이 대표 예시 계약을 통과하고 월간 검증 예산이 남아 있다 | 후보 Replay와 품질 Judge를 실행한다 | 기준·후보·Judge 비용을 검증 예산에 기록하고, 실행·schema·downstream·품질 하한·fallback·순절감 gate를 모두 통과한 입력군에만 `validated_adaptive_cohort` rule을 추가한다. |
| FR-011-R31 | bootstrap staged candidate search | 첫 bootstrap batch의 후보가 모두 탈락했고 같은 설정 지문에 실행 가능한 미검증 후보와 월간 예산이 남아 있다 | 완료 batch를 확정한다 | 운영 실행 횟수를 기다리지 않고 다음 `deployment_bootstrap` batch를 즉시 한 번 예약한다. 이미 검증한 후보를 다시 실행하지 않고, active route가 생긴 입력군은 후속 검증에서 제외한다. |
| FR-011-R32 | bootstrap search stop | 입력군에 검증된 route가 생겼거나 미검증 후보가 소진됐거나 월간 검증 예산이 부족하거나 3번째 bootstrap wave가 끝났다 | 완료 bootstrap batch의 후속 탐색을 계획한다 | 새 batch를 만들지 않고 검증된 route 또는 저장 기본 모델을 유지한다. 품질 gate를 낮춰 후보를 강제 활성화하거나 월간 예산을 초기 탐색에서 무제한 소진하지 않는다. |
| FR-011-R33 | bootstrap rejection diagnostics | bootstrap 후보가 품질 또는 계약 gate에서 탈락한다 | batch를 완료한다 | raw 입력·출력 없이 후보별 입력군, 모델, 상태, 표본 수, `reason_code`, 기준/후보 평균 품질, 순절감률과 후속 탐색 상태를 `error_summary`에 기록한다. `rejected_or_waiting_cohort_count`는 후보 수가 아니라 중복 제거한 입력군 수다. |
| FR-011-R34 | required manual cohort matching before model validation | 필수 직접 입력군의 대표 예시 embedding은 준비됐지만 저비용 후보가 탈락해 `proposed` 또는 `validated_waiting`이다 | runtime semantic catalog를 만들고 해당 입력을 실행한다 | 입력군은 정확히 매칭되지만 검증된 adaptive rule이 없으므로 저장 기본 모델을 사용한다. 일반 optional 자동 발견 입력군은 검증 전 catalog에 포함하지 않는다. |
| FR-011-R35 | rejected candidate revalidation | 후보 모델의 이전 evidence가 `rejected`이고 이후 같은 설정 지문에 새로운 성공 운영 observation이 최소 표본 수만큼 쌓였으며 월간 예산이 남아 있다 | 자동 또는 수동 refresh 후보를 계획한다 | 과거 탈락 이력은 보존하면서 새 observation window의 validation batch를 만든다. 동일 설정의 `validated` evidence가 있을 때만 중복 검증을 생략한다. |
| FR-011-R27 | gevent provider isolation | 일반 LLM node 실행과 policy Judge 갱신이 같은 gevent worker에서 동시에 OpenAI chat model을 호출한다 | provider sync 경로를 실행한다 | 각 호출은 공유 asyncio event loop를 만들지 않고 독립적인 동기 HTTP 요청으로 완료된다. 한 Judge 호출이 일반 workflow 실행을 멈추게 하지 않는다. |
| FR-011-R28 | stale refresh recovery | worker 종료나 task 유실로 policy가 갱신 제한 시간보다 오래 `refreshing` 상태에 남아 있다 | 다음 eligible 운영 run event를 반영한다 | 기존 active policy는 유지하면서 stale 요청 시각을 갱신하고 refresh task를 한 번 다시 예약한다. 제한 시간 안의 정상 갱신은 중복 예약하지 않는다. |
| FR-011-P01 | preview matched rule | active deployment policy에 short 입력 rule이 있고 실행 주체가 rule model을 사용할 수 있다 | preview API를 호출한다 | runtime과 같은 `ModelRouter.resolve_policy()`가 rule model과 profile/rule/reason safe summary를 반환한다. |
| FR-011-P05 | preview feature parity | 배포 node에 작업 설명, 변수 템플릿과 JSON output schema가 있고 preview 입력이 있다 | preview API를 호출한다 | runtime과 같은 side-effect 없는 prompt renderer와 `routing_feature_text()` builder가 치환된 system/user/assistant prompt, 독립 `OUTPUT_CONTRACT`의 JSON schema 지시, 현재 입력과 작업 계약을 resolver에 전달한다. 저장된 `{{variable}}` 원문을 feature로 사용하지 않으며 Preview는 실제 retrieval을 수행하거나 RAG runtime signal을 합성하지 않는다. |
| FR-011-P13 | fixed output contract isolation | system prompt가 prompt section 예산보다 길고 JSON schema가 설정되어 있다 | runtime routing feature를 생성한다 | system prompt는 제한되지만 고정 `OUTPUT_CONTRACT`와 JSON mode/schema 정보는 feature에 포함되지 않는다. 구조화 출력 계약이 요청별 난이도·모델 선택 신호가 되지 않는다. |
| FR-011-P11 | invalid schema learning rejection | JSON schema 선언이 유효하지 않아 runtime trace가 `schema_status=not_evaluated`다 | 운영 성적과 학습 label을 확정한다 | 실행은 `schema_failed`로 거절되고 schema 평가 분모 1, 통과 건수 0으로 누적된다. Schema가 없는 `not_required`만 중립 처리한다. |
| FR-011-P12 | preview prompt render failure | 배포 snapshot의 prompt template이 렌더링 불가능하다 | preview API를 호출한다 | 저장된 template 원문으로 fallback해 모델을 선택하지 않고 `model_routing.prompt_render_failed` safe blocked 상태로 종료한다. Raw template과 input은 응답에 포함하지 않는다. |
| FR-011-P02 | preview default | active policy에 일치하는 rule이 없다 | preview API를 호출한다 | 규칙 미일치 시 active policy의 default model과 `default_model` source를 반환한다. |
| FR-011-P03 | preview fallback | default model이 실행 주체에게 사용 불가하고 fallback model은 사용 가능하다 | preview API를 호출한다 | provider completion 없이 fallback model과 `fallback_model` source를 반환한다. |
| FR-011-P04 | preview blocked | default/fallback model 모두 실행 주체에게 사용 불가하다 | preview API를 호출한다 | `409 model_routing.no_available_model` safe error를 반환한다. |
| FR-011-P05 | preview deployment boundary | current draft의 target node 설정이 deployment snapshot과 다르다 | preview API를 호출한다 | draft가 아니라 deployment snapshot/persisted policy를 평가하고 `draft_matches_deployment=false`를 반환한다. |
| FR-011-P06 | preview no side effect | preview를 한 번 호출한다 | DB write/task spy를 확인한다 | workflow run, node run, usage log, policy event, refresh counter, refresh task가 생성 또는 변경되지 않는다. |
| FR-011-P06A | Test Sidebar 실행 비교 폭 | Test Sidebar가 열린 상태다 | `실행 비교` 탭을 선택한다 | Sidebar가 화면 우측 여백 24px을 제외한 최대 너비로 확장되고, 비교 결과를 같은 화면에서 표시한다. |
| FR-011-P07 | Test Sidebar 실행 노드 상세 | 자동 라우팅 trace가 있는 LLM node 테스트 실행이 완료됐다 | 해당 node의 `상세 보기`를 누른다 | 페이지 이동 없이 같은 Sidebar의 본문을 맨 위로 이동하고 실행 상태(상태·비용·시간·토큰), 입력, 실행 모델/자동 라우팅, 출력 순서로 표시한다. 입력 길이 profile·검토/제외 모델 수·품질 하한·예상 비용/지연·선택 이유·policy version을 표시한다. raw input/prompt는 노출하지 않는다. Sidebar와 비교 영역 바깥의 중복 학습 제외 안내는 표시하지 않고, 배포 정책·임시 정책 또는 이전 test trace용 학습 제외 안내를 Judge·학습 상태 다음 상세의 마지막에 한 번 표시한다. `테스트 결과로 돌아가기`로 목록에 복귀한다. |
| FR-011-P08 | Test Sidebar 실제 fallback | 자동 라우팅 LLM node가 primary 호출 실패 뒤 fallback으로 성공했다 | node 상세를 연다 | 최초 선택 모델, 안전한 실패 사유, 실제 사용한 fallback 모델을 `실제 대체 실행` block으로 표시한다. 계획된 fallback만 있는 정상 실행과 혼동하지 않는다. |
| FR-011-P09 | Test Sidebar 자동 라우팅 실행 | 자동 라우팅 LLM node를 Test Sidebar에서 실행한다 | 활성 배포 정책 유무와 관계없이 테스트한다 | 설정 fingerprint가 같은 활성 배포 정책이 정확히 하나일 때만 재사용한다. 여러 활성 배포가 일치하면 잘못된 정책을 고르지 않고 임시 Judge-first 정책으로 실행한다. 작업 지문이 같은 검증 learner version이 있으면 로컬 라우터를 사용하고, 없거나 불확실하면 Judge를 사용한다. trace에는 실제 선택 경로인 `decision_source=runtime_judge \| local_router \| stored_model`, 실행 맥락인 `execution_mode=test`, `policy_source=active_deployment 또는 test_ephemeral`, `included_in_routing_learning=false`를 분리해 남긴다. workflow run의 `deployment_id`는 비우고 learner label·학습 횟수·운영 성적·정책 갱신 카운터를 변경하지 않는다. |
| FR-011-P09A | Judge 실행 상태 trace/UI | Judge 성공, Judge 호출 후 실패, Judge 미호출, 이전 trace 상태가 각각 있다 | 로그 또는 Test Sidebar에서 LLM node 상세를 연다 | 공통 상세 화면은 성공 시 모델·확신도·후보 수·비용을, 호출 후 실패 시 기본 모델 회귀와 오류 코드를, 미호출 시 미호출 이유를 구분해 표시한다. 상태 없는 이전 trace는 실패로 단정하지 않고 `Judge 실행 정보 없음`으로 표시한다. Judge 성공 상세의 header에는 중복된 `Judge가 모델 선택` 배지를 표시하지 않는다. |
| FR-011-P10 | Test Sidebar 정책 stale 차단 | current draft의 자동 라우팅 LLM node 설정 fingerprint가 활성 deployment snapshot과 다르다 | Test Sidebar에서 실행한다 | 오래된 deployment policy는 평가하지 않는다. 현재 draft와 실행 주체가 사용할 수 있는 모델로 임시 Judge-first 정책을 만들어 Judge를 호출하되 정책·학습 데이터는 저장하지 않는다. |
| FR-011-L01 | 요청 경로와 학습 분리 | 운영 요청에서 Judge가 모델을 선택했다 | workflow가 terminal 상태가 된다 | 실행 중에는 학습 전 예측과 안전 label만 저장하고 가중치를 변경하지 않는다. 확정 label은 Celery 학습 task로 전달된다. |
| FR-011-L02 | 학습 batch 경계 | 같은 learner에 확정 label 10건이 쌓이거나 첫 label 후 5분이 지났다 | local router 학습 task를 실행한다 | learner 행을 잠근 뒤 최대 10건을 한 번 처리하고 각 label에 `learning_processed_at`을 기록한다. 남은 label은 후속 task로 처리한다. |
| FR-011-L03 | 최근 20건 전환 gate | Judge label은 50건 이상이지만 최근 학습 전 예측 비교가 20건 미만이거나 일치율·축 오차·계약 통과율 기준을 충족하지 못한다 | 학습 모드를 재평가한다 | candidate artifact를 실행용으로 승격하지 않고 `judge_first`를 유지한다. |
| FR-011-L04 | 예측 붕괴 차단 | 최근 Judge 정답은 여러 요구 수준인데 로컬 예측은 한 요구 수준으로만 수렴한다 | 학습 모드를 재평가한다 | 높은 표본 수만으로 `local_first`로 전환하지 않는다. |
| FR-011-L05 | 미지 입력 Judge 회귀 | local-first 상태에서 현재 vector가 학습 표본과 멀거나 예측 경계가 모호하다 | 운영 요청을 실행한다 | 로컬 confidence가 기준 미만이 되어 Runtime Judge가 모델을 선택하고 새 비교 label을 남긴다. |
| FR-011-L06 | 그룹 분리 학습 feature | 같은 LLM 노드에서 실행 변수는 같고 제목·작업 설명·system/user/assistant prompt의 고정 문구만 다르다 | local learning feature와 vector를 만든다 | 두 feature는 같아야 한다. 핵심 요청, 동적 문맥, 구조화 특징을 별도 임베딩하고 `75% / 15% / 10%`로 정규화 결합한다. 변하는 RAG 안전 신호는 구조화 특징에 포함하고, 미참조 upstream 값과 고정 prompt 문구는 제외한다. 변수 메타데이터가 없는 레거시 노드는 가장 정보량이 큰 runtime 값을 핵심 요청으로 사용한다. |
| FR-011-L07 | 학습 feature 버전 격리 | 이전 prompt-context artifact가 있는 정책에 변수 중심 feature 코드가 배포된다 | 운영 요청과 batch 학습을 실행한다 | 이전 artifact로 local 예측하지 않고 Judge-first로 돌아간다. 새 feature label부터 학습 횟수·최근 평가를 다시 시작하고 서로 다른 feature schema의 vector를 섞지 않는다. |
| FR-011-L08 | Runtime Judge 시간 분리 저장 | Judge provider 호출이 재시도를 포함해 완료된다 | Judge usage와 실행 trace를 저장한다 | 첫 요청부터 최종 재시도 응답까지의 총 경과 시간을 `usage.latency_ms`와 Judge 전용 `llm_usage_logs.latency_ms`에 저장한다. 최종 작업 모델 latency와 섞거나 중복 합산하지 않는다. |
| FR-011-L09 | 재배포 학습 재사용 | 같은 organization/workflow/node의 새 배포가 이전과 같은 task fingerprint와 Judge 계약을 가진다 | 자동 라우팅 policy를 생성한다 | 기존 learner와 최신 검증 learner version을 재사용하고 학습 횟수를 0으로 되돌리지 않는다. |
| FR-011-L10 | 작업 변경 계보 분리 | prompt, RAG, 출력 schema 또는 downstream 계약이 바뀌어 task fingerprint가 달라진다 | 자동 라우팅 policy를 생성한다 | 새 learner를 만들고 이전 learner/version은 수정하지 않는다. |
| FR-011-L11 | 학습 계약 변경 계보 분리 | Judge rubric, feature schema 또는 E5 encoder 식별자가 바뀐다 | learner를 조회하거나 생성한다 | 새 judge contract hash의 learner를 만들고 기존 learner는 `stale` 읽기 전용 계보로 보존한다. |
| FR-011-L12 | policy 누락 운영 실행 | 자동 라우팅 배포 실행인데 persisted policy row가 누락됐다 | LLM node를 실행하고 workflow를 완료한다 | 임시 Judge-first 실행을 허용하되 task fingerprint 기준 learner를 조회·생성하고 운영 label은 해당 learner에 저장한다. |
| FR-011-L13 | 테스트 실행 비학습 | 같은 작업 지문의 검증 learner version이 있거나 Judge 호출이 필요하다 | Test Sidebar에서 실행한다 | 로컬 라우터 또는 Judge로 모델을 선택하지만 learner label, 학습 횟수, 운영 성적, policy refresh counter를 변경하지 않는다. trace에는 `included_in_routing_learning=false`를 남긴다. |
| FR-011-L14 | 불변 버전 발행·정책 반영 | Judge label 50건, 최근 평가 20건, 일치율·축 오차·계약 품질 gate를 모두 충족한다 | batch 학습을 완료한다 | candidate artifact hash로 중복 발행을 막고 새 learner version을 발행한다. 같은 hash의 과거 version이 있으면 새 row를 만들지 않고 기존 불변 version을 재활성화한다. 같은 learner를 참조하는 활성 policy는 해당 version을 참조한다. |
| FR-011-L15 | 비정형 출력 중립 schema gate | 출력 schema가 없는 자유형 LLM node의 운영 label이 모두 `not_applicable`이다 | learner version 발행 gate를 계산한다 | schema 미검사를 실패로 취급하지 않고 schema pass rate를 중립 100%로 계산한다. 실행·downstream·fallback 및 최근 Judge 평가가 다른 gate를 충족하면 version을 발행할 수 있다. |
| FR-011-L16 | 구형 학습 데이터 폐기 migration | policy 소유 label과 `active_policy.learning` artifact가 있는 DB를 신규 head로 올린다 | migration을 실행한다 | 구형 label/artifact를 복원 없이 폐기하고 learner/version/신규 label schema를 만든다. 기존 policy, 실행 로그, 비용·성능 데이터는 유지하며 Alembic head는 하나다. |
| FR-011-L17 | 활성 학습 버전 운영 품질 저하 | 특정 배포 정책이 검증된 learner version을 사용 중이고 완료된 운영 표본이 20건 이상이다 | 정책 점검에서 실행 성공률, schema 통과율 또는 downstream 성공률이 95% 미만이거나 fallback 비율이 5%를 초과한다 | learner와 불변 version은 감사용으로 보존하되 해당 배포 정책의 `active_learner_version_id`만 해제한다. 다음 실행은 Runtime Judge 우선으로 돌아가며 정책 점검 결과에는 `operational_contract_degraded`를 남긴다. |

## Constraint-Difficulty Router Experimental Tests

관련 FR: FR-015

이 섹션은 운영 active policy와 DB를 변경하지 않는 독립 실험 전략을 검증한다. fixed fixture 결과는 구조와 재사용 계약의 증거일 뿐 실제 Provider 품질 증거로 사용하지 않는다.

| ID | 검증 영역 | Given | When | Then |
| --- | --- | --- | --- | --- |
| FR-015-R01 | semantic isolation | node data에 intent/task type/customer-facing/domain hint가 있다 | 제약 signature를 생성한다 | 해당 의미 필드는 signature와 선택 근거에 포함되지 않는다. |
| FR-015-R02 | execution subject availability | catalog 모델 중 실행 주체가 사용할 수 없는 모델이 있다 | 후보를 필터링한다 | `model_not_available`로 제외한다. |
| FR-015-R03 | context Hard Gate | input+prompt+RAG+reserved output 추정 token이 모델 context window를 넘는다 | 후보를 필터링한다 | `context_window_insufficient`로 제외한다. |
| FR-015-R04 | strict output Hard Gate | strict JSON Schema 출력이 필수이고 모델이 strict Structured Output을 지원하지 않는다 | 후보를 필터링한다 | 해당 모델을 제외한다. 일반 JSON 출력에서는 이 capability만으로 제외하지 않는다. |
| FR-015-R05 | simple JSON validation candidate | 짧은 입력과 단순 JSON 계약이며 검증 증거가 없다 | 라우팅한다 | 안전 기본 모델을 유지하고 더 저렴한 실행 가능 모델을 검증 후보로 반환한다. |
| FR-015-R06 | high structural constraints | 긴 RAG 또는 복잡한 Schema+엄격 downstream이 있다 | 라우팅한다 | low tier 기본 모델을 그대로 사용하지 않고 요구 tier를 만족하는 안전 모델을 선택한다. fallback도 같은 tier 기준을 만족한다. |
| FR-015-R07 | freeform safety | 자유형 출력에 자동 품질 증거가 부족하다 | 라우팅한다 | 안전 기본 모델을 유지하고 `freeform_quality_evidence_insufficient`를 남긴다. |
| FR-015-R08 | exact signature evidence | 쉬운 조건에서 저가 모델 증거가 있지만 현재 요청은 더 긴 RAG/복잡한 계약이다 | 라우팅한다 | 쉬운 signature의 증거를 재사용하지 않는다. |
| FR-015-R09 | required input/file dimensions | 필수 참조 입력이 없거나 파일 입력이 있다 | signature와 후보를 계산한다 | 누락 입력은 모델 호출 전에 차단하고 파일 여부는 독립 차원과 최소 tier에 반영한다. |
| FR-015-R10 | real semantic matcher fixture | 고정 representative/query vector가 있다 | 네 전략 실험을 실행한다 | semantic arm은 미리 지정한 모델 ID가 아니라 실제 `SemanticRouteMatcher`의 match 결과를 사용한다. |
| FR-015-R11 | provider result reuse | 여러 전략이 같은 입력에 같은 모델을 선택한다 | result matrix를 조회한다 | `(요청 fingerprint, 모델)` 결과를 한 번만 만들고 전략 간 재사용한다. 입력 또는 RAG 결과가 바뀌면 재사용하지 않는다. |
| FR-015-R12 | RAG retrieval reuse | RAG 입력 20개를 네 전략으로 비교한다 | 실험을 실행한다 | 입력당 retrieval은 한 번만 수행하며 네 전략이 동일한 actual RAG token 수를 사용한다. |
| FR-015-R13 | failure denominator | Schema/downstream 필수 케이스가 출력 검증 전에 실패해 상태가 `None`이다 | 지표를 집계한다 | 실패 표본을 분모에서 제거하지 않고 실패로 계산한다. 품질 미평가 비율은 별도 표시한다. |
| FR-015-R14 | trace | 실행 권한·context로 일부 모델이 제외된다 | decision metadata를 만든다 | strategy, selected/fallback model, signature, 제외 모델과 이유, reason code, `judge_called=false`가 남는다. |
| FR-015-R15 | adoption criteria | 3 workflow×20 입력의 4전략 결과가 있다 | 보고서를 생성한다 | 목표의 10개 채택 기준을 통과/실패/실제 검증 필요로 판정하고 하나라도 미확정이면 교체 후보로 표시하지 않는다. |
| FR-015-R16 | semantic regression | 신규 모듈을 추가한다 | 기존 semantic router 단위 테스트를 실행한다 | 기존 테스트가 수정 없이 통과하고 운영 dispatch/active policy는 변경되지 않는다. |
| FR-015-R17 | malformed boolean / empty file | `strict`, downstream `required`가 문자열 `"false"`이거나 file 필드 값이 비어 있다 | signature를 만든다 | 엄격 계약 또는 파일 입력으로 잘못 승격하지 않는다. |
| FR-015-R18 | unbounded RAG preflight | RAG가 켜져 있고 최대 context 글자 수가 없다 | retrieval 전 signature를 만든다 | `topK`와 KB/Collection 참조 수 기반의 보수적 token 상한을 사용하며 임의 2,000 token으로 축소하지 않는다. |
| FR-015-R19 | unknown model price | 구조·품질 gate를 통과했지만 가격이 없는 모델이 있다 | 경제적 후보를 선택한다 | 가격 없는 모델을 최저 비용 모델로 승격하지 않고 안전 기본 모델을 유지한다. |
| FR-015-R20 | critical quality failure | 모델 호출과 Schema 검증은 성공했지만 evaluator가 치명적 품질 실패를 명시했다 | 채택 기준을 계산한다 | 점수 임계값 추측 없이 `critical_quality_failure`를 사용해 운영 교체 후보를 차단한다. |
| FR-015-R21 | cold-start routing cycle | 실행 가능한 저가·중간·고성능 모델에 global prior가 있고 같은 node/signature의 검증 증거는 없다 | `PriorGuidedAdaptiveRouter`로 라우팅한다 | 검증 전용 전략처럼 기본 모델에 고착되지 않고 품질 하한과 효용을 만족한 후보를 선택하며 안전 기본 모델을 fallback으로 둔다. |
| FR-015-R22 | transferable evidence | 더 어려운 compatible signature에서 후보 모델의 성공 증거가 있고 현재 요청은 더 쉽다 | posterior를 계산한다 | 증거를 낮은 가중치로 반영해 유효 표본 수를 늘리고 불확실성을 낮춘다. 쉬운 증거는 더 어려운 요청에 재사용하지 않는다. |
| FR-015-R23 | bounded exploration safety | 불확실하지만 유망한 저비용 모델과 탐색 예산이 있다 | low/high constraint 요청을 각각 라우팅한다 | low constraint의 deterministic 표본에서만 후보를 탐색하고 high constraint에서는 안전 모델을 유지한다. |
| FR-015-R24 | prior-guided trace | prior와 관련 evidence로 후보 점수를 계산했다 | decision metadata를 만든다 | 후보별 품질 평균·하한·불확실성·예상 총비용·fallback 비율·유효 증거 표본·prior source를 남기고 raw 입력은 남기지 않는다. |
| FR-015-R25 | cold-start comparison experiment | 같은 node/signature 검증 증거가 없는 3 workflow×20 입력이 있다 | 5개 전략 fixed-fixture 실험을 실행한다 | 검증 전용 constraint의 기본 모델 고착 횟수와 prior-guided 전략의 모델 분산·품질·비용을 같은 result matrix로 비교한다. |

## LLM Parameter Recommendation Tests

관련 FR: FR-012

이 섹션은 LLM 파라미터 추천 룰셋을 검증한다. 추천은 운영 로그와 safe trace summary에 근거해야 하며, raw prompt, raw completion, raw Knowledge chunk content, credential 원문을 사용자의 추천 UI나 응답에 노출하면 안 된다.

| ID | 추천 대상 | Given | When | Then |
| --- | --- | --- | --- | --- |
| FR-012-R01 | `max_tokens` | target LLM node의 배포 후 성공 운영 sample이 충분하고, 최근 `completion_tokens` p95가 현재 `max_tokens`보다 낮으며 schema/downstream 실패가 없다 | 파라미터 추천 API를 호출한다 | `max_tokens` 하향 추천 row를 반환한다. `confidence=high`, `risk=low`, `apply_mode=experiment_required`이며 `candidate_patch.parameters.max_tokens`를 포함한다. |
| FR-012-R02 | `max_tokens` | `completion_tokens` p95는 낮지만 provider finish reason 또는 길이 잘림 여부를 알 수 없다 | 파라미터 추천 API를 호출한다 | 추천을 반환하더라도 `confidence`를 `medium` 이하로 낮추고 reason에 길이 잘림 근거 부족을 표시한다. |
| FR-012-R03 | `max_tokens` | 최근 output이 잘렸거나 schema/downstream 실패가 증가했다 | 파라미터 추천 API를 호출한다 | `max_tokens` 하향 추천을 만들지 않거나 `적용 비추천` warning을 반환한다. |
| FR-012-R04 | `temperature` | JSON/schema/분류/추출 성격의 node에서 `temperature > 0.3`이고 schema 실패 또는 retry/fallback 증가가 있다 | 파라미터 추천 API를 호출한다 | `temperature`를 `0.1~0.3` 범위로 낮추는 후보를 반환한다. reason은 안정성/실패 비용 감소를 설명해야 한다. |
| FR-012-R05 | `temperature` | 사용자-facing 창의 생성 node이고 schema/downstream 실패가 없다 | 파라미터 추천 API를 호출한다 | 비용 절감 근거만으로 `temperature` 하향 추천을 만들지 않는다. |
| FR-012-R06 | `top_p` | Anthropic 계열처럼 `top_p` 동시 사용을 제한하는 모델이거나 OpenAI GPT-5/o Responses 계열이다 | 파라미터 추천 API를 호출한다 | `top_p` 값을 새로 추천하지 않고 제거 후보 또는 호환성 warning만 반환한다. |
| FR-012-R06a | Responses runtime compatibility | GPT-5/o LLM node에 `top_p`, `presence_penalty`, `frequency_penalty`, `stop`, nested `text`, `response_format`이 저장되어 있다 | OpenAI client가 동기 또는 비동기 Responses 요청을 만든다 | 외부 요청에서는 지원하지 않는 parameter를 제외하고 `response_format`을 복사된 `text.format`에 반영한다. 호출 뒤 원본 parameter dictionary, nested `text` object와 message list는 호출 전 값과 동일하다. |
| FR-012-R07 | `frequency_penalty` | 최근 output에서 동일 문장 또는 n-gram 반복률이 높고 completion token 증가와 연결된다 | 파라미터 추천 API를 호출한다 | 낮은 위험의 `frequency_penalty` 증가 후보를 반환하되 A/B 후보 생성으로만 연결한다. |
| FR-012-R08 | `frequency_penalty` | JSON/schema node다 | 파라미터 추천 API를 호출한다 | 반복률 근거가 명확하지 않으면 `frequency_penalty` 추천을 만들지 않는다. |
| FR-012-R09 | RAG context | `context_token_estimate / prompt_tokens` 비중이 높고 evidence sufficiency가 유지되며 retrieved chunk 수가 과도하다 | 파라미터 추천 API를 호출한다 | `topK`, `retrievedContextMaxChars`, `retrievedContextCompression` 중 하나 이상의 RAG context 조정 후보를 반환한다. |
| FR-012-R10 | RAG context | RAG evidence가 부족하거나 downstream 실패가 있다 | 파라미터 추천 API를 호출한다 | RAG context 축소 추천을 반환하지 않고 근거 부족 또는 적용 비추천 warning을 반환한다. |
| FR-012-R11 | prompt safety | prompt token 비중이 높다 | 파라미터 추천 API를 호출한다 | author prompt를 임의로 자르는 patch를 반환하지 않는다. 프롬프트 축소는 별도 LLM 보조 후보 생성과 A/B 실험 필요 상태로만 표시한다. |
| FR-012-R12 | UI modal | workflow 목록에서 `워크플로우 최적화 권장` 항목을 클릭한다 | 최적화 추천 모달을 연다 | 추천 row에 현재값, 추천값, 예상 효과, 근거, 위험도, 액션이 표시된다. |
| FR-012-R13 | UI action | 파라미터 추천 row를 선택한다 | `선택 항목으로 실험 만들기`를 클릭한다 | current draft를 직접 수정하지 않고 `candidate_patch`가 merge된 B candidate로 Cost Optimizer A/B workspace에 진입한다. |
| FR-012-R14 | UI direct apply guard | 추천 유형이 `max_tokens`, `temperature`, RAG context다 | 모달을 렌더링한다 | 단일 `바로 적용` 버튼으로 draft를 수정할 수 없어야 한다. 직접 적용은 정책 갱신류 추천에만 분리해서 허용한다. |
| FR-012-R15 | canonical trace contract | 일반 workflow LLM 실행이 finish reason, schema, fallback, RAG summary를 남긴다 | 파라미터 추천 API를 호출한다 | `trace_metadata.llm`과 `.rag`의 canonical safe summary를 읽고, top-level legacy key가 없어도 동일하게 분석한다. |
| FR-012-R16 | terminal failure quality gate | 성공 usage 20건과 terminal schema/downstream 실패 run이 함께 있다 | 파라미터 추천 API를 호출한다 | 실패 run을 품질 실패율에 포함하고 `max_tokens` 또는 RAG context 축소 추천을 만들지 않는다. |
| FR-012-R17 | deployment/config cohort | 현재 draft node 설정이 활성 deployment snapshot과 다르거나 다른 deployment run이 섞여 있다 | 파라미터 추천 API를 호출한다 | `draft_not_deployed` 또는 warning을 반환하고, 다른 설정의 token/cost 표본을 현재값 추천에 사용하지 않는다. |
| FR-012-R18 | incomplete quality signal | structured output node의 schema signal 또는 RAG node의 retrieval summary가 일부 run에서 누락됐다 | 파라미터 추천 API를 호출한다 | schema/RAG 축소 추천을 만들지 않고 signal incomplete warning을 반환한다. |

## FR-013 Cost Optimizer 후보 검증 및 출력 품질 평가

| ID | 영역 | Given | When | Then |
| --- | --- | --- | --- | --- |
| FR-013-R01 | modal flow | 추천 row가 선택되어 있다 | `테스트하기`를 클릭한다 | 기존 Cost Optimizer route로 즉시 이동하지 않고 모달 안에서 baseline 조회와 candidate 실행 상태를 표시한다. |
| FR-013-R02 | latest baseline | 같은 active deployment/config cohort에 input/output/usage를 복원할 수 있는 성공 run과 더 최신의 실패·candidate run이 있다. 과거 성공 run의 `process_data.node_options.parameters.max_tokens`가 공통 redaction으로 마스킹된 경우도 포함한다. | 빠른 검증을 요청한다 | exact active `deployment_id` 조건을 만족하는 가장 최근 성공 운영 node run 하나를 baseline으로 고정하고 실패·candidate run은 제외한다. 마스킹된 process data fingerprint만으로 동일 배포 run을 제외하지 않는다. |
| FR-013-R03 | no baseline | 비교 가능한 최신 성공 run이 없다 | 빠른 검증을 요청한다 | provider를 호출하지 않고 `비교 가능한 최신 성공 기록이 없습니다.`를 표시한다. |
| FR-013-R04 | hybrid execution | baseline이 확정되고 recommendation id가 유효하다 | 빠른 검증을 실행한다 | A는 재실행하지 않고 동일 input으로 B만 한 번 실행하며 experiment/candidate row와 usage를 저장한다. |
| FR-013-R05 | recommendation freshness | recommendation policy version 또는 node setting fingerprint가 응답 이후 바뀌었다 | 빠른 검증을 요청한다 | `stale`로 거부하고 이전 candidate patch를 실행하지 않는다. |
| FR-013-R06 | duplicate submit | 동일 Idempotency-Key로 요청이 재전송된다 | verify endpoint가 처리한다 | candidate와 quality judge를 중복 호출하지 않고 첫 verification 결과를 반환한다. |
| FR-013-R07 | metric chart | A/B usage가 있다 | 결과 panel을 렌더링한다 | 비용, latency, token, 품질 점수를 서로 다른 metric card의 A/B 막대와 실제 값/delta로 표시한다. 다른 단위를 하나의 axis에 섞지 않는다. |
| FR-013-R08 | baseline context | 결과 panel을 렌더링한다 | 사용자가 A 기준을 확인한다 | `최신 비교 가능한 성공 기록`, 실행 시각, 모델, baseline cost/latency/token을 표시한다. |
| FR-013-R09 | quality judge | baseline/candidate output과 동일 input, node 목적이 있다 | 품질 평가를 실행한다 | A를 정답으로 취급하지 않는 blind pairwise judge가 두 variant의 0~100 점수, dimension, confidence, safe summary를 반환한다. |
| FR-013-R10 | quality unavailable | candidate는 성공했지만 judge credential/model이 없거나 judge 호출이 실패했다 | 응답을 만든다 | `partial`과 `품질 평가 불가`를 반환하고 비용·latency·token·schema·downstream 결과는 유지한다. |
| FR-013-R11 | JSON schema | candidate output format이 JSON이고 schema가 있다 | candidate output을 검증한다 | parse/schema 통과는 `passed`, 누락 field/type mismatch는 `failed`와 safe issue summary를 반환한다. |
| FR-013-R12 | non-JSON schema | output format이 text이거나 JSON schema가 없다 | 결과 panel을 렌더링한다 | text는 `검사 대상 아님`, schema 없는 JSON은 `스키마 미설정`으로 표시하며 실패로 오인시키지 않는다. |
| FR-013-R13 | downstream | candidate output을 직접 참조하는 후속 노드가 있다 | 기존 contract validator를 실행한다 | `compatible`, `warning`, `incompatible`, `unknown`과 검사 node 수를 반환한다. incompatible은 적용을 막는다. |
| FR-013-R14 | incurred cost | candidate 실행과 quality judge 호출이 완료됐다 | 결과 panel을 렌더링한다 | candidate cost, judge cost, 신규 발생 합계를 구분하고 과거 baseline 비용은 합계에 더하지 않는다. 가격 정보가 없으면 0이 아닌 `계산 불가`로 표시한다. |
| FR-013-R15 | apply gate | candidate 성공, schema passed/not-applicable, downstream compatible이고 result가 stale하지 않다 | `적용하기`를 누른다 | exact comparison/candidate settings를 기존 apply API로 current draft에 적용한다. |
| FR-013-R16 | quality warning | candidate quality score가 baseline보다 낮거나 confidence가 low다 | `적용하기`를 누른다 | 품질 점수만으로 hard block하지 않고 경고와 명시적 확인을 요구한다. |
| FR-013-R17 | detail analysis | 빠른 검증 결과가 있다 | `상세 비교 분석하기`를 누른다 | 같은 comparison_id/candidate_id의 기존 결과 분석 화면을 열며 B를 다시 실행하거나 LLM 비용을 중복 발생시키지 않는다. |
| FR-013-R18 | scroll/footer | recommendation, chart, gate, cost content가 modal max height를 넘는다 | modal을 스크롤한다 | body만 세로 스크롤되고 `적용하기`, `상세 비교 분석하기`, `닫기` footer는 계속 접근 가능하다. |
| FR-013-R19 | selection changed | 빠른 검증 이후 추천 선택 또는 target node draft가 바뀐다 | 기존 결과로 적용을 시도한다 | 결과를 `stale`로 표시하고 적용을 비활성화하며 다시 테스트하도록 안내한다. |
| FR-013-R20 | manual compare quality | 사용자가 baseline을 직접 선택했고 B candidate 실행이 성공한다 | 일반 compare API를 호출한다 | 같은 A/B 출력에 blind pairwise judge를 실행하고 `quality_evaluation`의 baseline/candidate 0~100 점수, delta, confidence, safe summary를 compare 응답과 candidate 이력에 저장한다. |
| FR-013-R21 | result analysis quality row | compare 응답 또는 선택한 이전 실험에 품질 평가가 있다 | 결과 분석 화면의 핵심 지표 표를 렌더링한다 | `출력 품질 점수` 행에 A/B 점수, 상승·하락 방향, confidence를 표시한다. judge가 unavailable이면 행을 유지하고 `평가 불가`를 표시한다. |
| FR-013-R22 | judge response contract | judge provider 호출은 완료됐지만 JSON, 필수 dimension 또는 confidence가 유효하지 않다 | 품질 결과를 정규화한다 | 점수는 `unavailable`로 처리하고, 이미 발생한 judge usage/cost와 usage log id는 candidate에 기록한다. 계약에 없는 추가 dimension은 총점에서 제외한다. |
| FR-013-R23 | judge payload safety | 동일 입력과 A/B 출력에 credential 변형, 실행 metadata, raw RAG chunk 또는 과대 payload가 있다 | judge payload를 만든다 | 공통 fail-closed redaction과 depth/item/string 제한을 provider 호출 전에 적용하고, A/B 위치를 무작위로 바꾼 뒤 결과를 원래 variant로 복원한다. |
| FR-013-R24 | detail deep link | 선택 candidate가 이력 첫 20개 밖이거나 `schema_failed/failed` 상태다 | `comparisonId/candidateId` 결과 분석 URL을 연다 | 단건 상세 API로 exact candidate를 복원하고, 기본 성공 이력 목록 조회가 완료돼도 선택 결과를 유지한다. B와 judge를 다시 실행하지 않는다. |
| FR-013-R25 | route session reset | 한 LLM node의 비교 결과를 본 상태에서 workflow/node/deep link URL이 바뀐다 | 같은 route 컴포넌트가 새 식별자로 렌더링된다 | 이전 baseline, compare result, 선택 이력을 표시하지 않고 새 node의 baseline 선택 상태로 초기화한다. |
| FR-013-R26 | explicit history filter | 결과 분석 이력 패널에서 실행자·모델·기간 필터를 입력한다 | 입력 중에는 대기하고 `필터 적용`을 실행한다 | 입력 중 추가 목록 요청을 보내지 않고 적용 시점에 정규화된 query로 한 번 조회한다. |
| FR-013-R27 | stale response shape | recommendation policy version 또는 node fingerprint가 현재 상태와 다르다 | 빠른 검증 API를 호출한다 | provider를 호출하지 않고 `verification_status=stale`, null comparison/candidate/baseline과 `apply.allowed=false`를 반환하며, 프론트는 결과 값을 읽지 않고 재시도 안내를 표시한다. |
| FR-013-R28 | draft/deployment mismatch | current draft의 target node 설정과 `App.active_deployment_id`가 가리키는 deployment snapshot 설정이 다르다 | 빠른 검증 API를 호출한다 | baseline 또는 candidate/provider 호출을 시작하지 않고 `verification_status=stale`로 종료한다. 단순히 `is_active=true`인 최신 deployment를 active pointer 대신 사용하지 않는다. |
| FR-013-R29 | judge model eligibility | 사용 가능한 모델 목록의 앞에 `babbage-002`, embedding, realtime 같은 품질 Judge 비호환 모델이 있고 뒤에 OpenAI·Anthropic·Google의 생성형 chat model이 있다 | 품질 Judge runtime을 선택한다 | 비호환 모델은 `type=chat`으로 잘못 분류돼도 제외한다. 사용 가능한 provider 안에서 JSON 평가에 적합한 균형형 chat model을 우선 선택하며, 특정 provider만 가진 organization에서도 평가를 실행한다. |

## FR-001 LLM 노드 단위 A/B 테스트 진입
## Knowledge/RAG Compare Tests

이 섹션은 기존 LLM 노드 단위 Cost Optimizer 테스트를 대체하지 않고, RAG 포함 workflow 비교가 추가될 때 검증해야 할 Knowledge/RAG 경계를 정의한다.

| ID | 검증 조건 | 최소 실패 조건 | 기대 결과 |
| --- | --- | --- | --- |
| COST-KNOW-TC-001 | compare 실행은 workflow 실행 권한과 대상 credential `use` 권한을 요구해야 한다. | viewer 권한 사용자가 compare를 실행한다. | `403 permission.denied`. |
| COST-KNOW-TC-002 | 후보 모델은 verified credential-model relation이 있는 모델로 제한되어야 한다. | verified relation이 없는 모델을 후보로 지정한다. | 후보 거부 또는 제외. |
| COST-KNOW-TC-003 | organization scope 밖 workflow 비교는 존재 추론 없이 숨겨야 한다. | 다른 조직 workflow id로 compare를 요청한다. | `404 resource.not_found`. |
| COST-KNOW-TC-004 | RAG 포함 workflow compare는 workflow runtime의 `execution_subject` 기준으로 KB permission, source ACL, final evidence gate를 적용해야 한다. | 실행 주체가 볼 수 없는 KB가 candidate에 포함된다. | prompt, citation, trace, 비교 UI 어디에도 포함되지 않음. |
| COST-KNOW-TC-005 | Query rewrite와 evidence sufficiency 옵션이 켜진 variant도 권한 없는 KB 또는 requester source authorization denied 문서를 후보로 만들지 못해야 한다. | rewrite 결과가 권한 없는 KB를 검색 후보로 확장한다. | 후보 제외 또는 safe no-result. |
| COST-KNOW-TC-006 | RAG 포함 비교 리포트는 safe summary만 표시해야 한다. | raw rewritten query, raw prompt/completion, raw chunk content, hidden KB id/name, raw source metadata가 표시된다. | 테스트 실패. |
| COST-KNOW-TC-007 | 별도 승인 전 `llm_assisted` query rewrite는 비교 변수로 사용하지 않아야 한다. | 승인 없이 `llm_assisted` variant가 생성된다. | 후보 생성 거부 또는 명시 제외. |

## FR-001 LLM 노드 단위 A/B 테스트 진입

### Component Tests

- LLM 노드 상세 화면에는 `모델 라우팅 최적화` 액션이 표시된다.
- `A/B 테스트` 액션은 LLM 노드 상세 패널의 상단 헤더 우측 보조 액션 영역에 표시된다.
- LLM 노드가 아닌 노드 상세 화면에는 `모델 라우팅 최적화` 액션이 표시되지 않는다.
- builder 이상 권한이 없는 사용자는 액션이 disabled 상태로 보이고 권한 부족 안내를 확인할 수 있다.
- baseline 실행 로그가 없으면 사용자는 `비교할 실행 로그가 없습니다. 먼저 테스트 실행을 완료해 주세요.` 안내를 확인할 수 있다.
- 저장되지 않은 draft가 있으면 사용자는 저장 후 비교를 시작해야 한다는 안내를 확인할 수 있다.

### API Tests

- `GET /cost-optimizer/availability`는 target node가 `llmNode`이면 `available=true`를 반환한다.
- target node가 `llmNode`가 아니면 `available=false` 또는 `cost_optimizer.not_llm_node` 오류를 반환한다.
- 존재하지 않는 node id는 scope 밖 resource와 구분되지 않도록 `404 resource.not_found`로 처리한다.

### Scenario Tests

- 사용자가 LLM 노드에서 `모델 라우팅 최적화`를 누르면 모델 추천 전용 화면으로 이동한다.
- 사용자가 모델 추천 전용 화면에서 `후보 실험 만들기`를 누르면 baseline 선택 화면으로 이동한다.
- 사용자가 일반 노드, webhook 노드, variable extraction 노드를 선택하면 Cost Optimizer 흐름이 시작되지 않는다.
- 사용자가 baseline 선택 전에는 A/B compare workspace로 바로 진입하지 않는다.

## FR-002 A Baseline 실행 로그 선택

### Component Tests

- baseline 선택 화면은 검색/필터/정렬 가능한 baseline 목록을 바로 제공한다.
- baseline 선택 화면은 각 baseline row에 실행 시각, 모델, 토큰, 비용, 실행 시간, 입력 preview, 출력 preview를 표시한다.
- `input_available=true`, `output_available=true`, `usage_available=true`를 모두 만족하는 성공 실행 로그가 없으면 baseline 목록은 비어 있고 로그 없음 안내가 표시된다.
- baseline 목록은 실행 시각, 상태, 모델, 비용, 토큰, 실행 시간, 입력 preview, 출력 preview, trace 존재 여부, downstream 상태를 표시한다.
- baseline 목록은 성공한 LLM node run만 표시하고 실패한 node run은 표시하지 않는다.
- baseline 목록은 output preview 또는 usage summary가 없는 node run을 표시하지 않는다.
- baseline 목록은 input 복원 불가 baseline row도 표시하되 `비교 불가` 상태로 표시한다.
- picker 검색은 입력/출력 preview 기준으로 동작한다.
- picker 필터는 모델, 날짜 범위, 비교 가능 여부를 지원한다.
- picker 정렬은 최신순, 비용 높은순, 비용 낮은순, 토큰 높은순, 실행 시간 긴순을 지원한다.

### API Tests

- `GET /baselines/latest`는 target LLM node의 성공 로그 중 `input_available=true`, `output_available=true`, `usage_available=true`를 모두 만족하는 가장 최근 로그를 반환한다.
- `GET /baselines/latest`는 실패한 node run을 baseline 후보로 반환하지 않는다.
- `GET /baselines/latest`는 output preview 또는 usage summary가 없는 node run을 baseline 후보로 반환하지 않는다.
- `GET /baselines`는 pagination metadata와 baseline row 목록을 반환한다.
- `GET /baselines`의 `q`, `model`, `date_from`, `date_to`, `sort`, `compare_available` query가 API 계약대로 적용된다.
- `GET /baselines`는 실패한 node run을 목록에 포함하지 않는다.
- `GET /baselines`는 output preview 또는 usage summary가 없는 node run을 목록에 포함하지 않는다.
- baseline query는 `workflow_node_runs.outputs`만으로 DB boundary에서 후보를 제외하지 않는다. trace payload에 redaction-safe output이 있으면 row 생성 단계에서 output availability를 판정한다.
- `GET /baselines`는 `workflow_node_runs.id`를 `baseline_id`로 반환한다.
- input을 복원할 수 없는 baseline row는 `input_available=false`, `compare_available=false`, `unavailable_reason=input_payload_unavailable`을 반환한다.
- baseline row는 credential 원문, API key, encrypted config를 포함하지 않는다.
- baseline row의 `llm_usage_logs.latency_ms`가 0 또는 누락된 경우 `workflow_node_runs.duration`을 ms로 환산해 실행 시간으로 반환한다.

### Scenario Tests

- 사용자가 baseline 목록에서 비교 가능한 row를 직접 선택하면 해당 baseline이 고정되고 A/B compare workspace로 이동한다.
- 사용자가 baseline 목록에서 특정 row를 선택하면 해당 로그가 A baseline으로 고정된다.
- 사용자가 `비교 불가` baseline row를 선택하면 A/B compare workspace로 이동하지 않고 input 복원 불가 안내를 본다.

## FR-003 B 후보 설정 입력

### Component Tests

- B candidate 영역은 현재 LLM 노드 설정 복사본으로 초기화된다.
- B candidate 영역은 `후보 옵션` 같은 중복 제목 대신 `테스트명` 입력을 제공한다.
- 원본 LLM 노드 상세 설정은 자동 라우팅 토글을 제공한다.
- 원본 LLM 노드 상세 설정은 자동 라우팅 OFF일 때 기본 모델과 fallback 모델 선택 UI를 표시하고, ON일 때는 policy 상태 panel을 표시한다.
- 원본 LLM 노드 상세 설정은 `모델 라우팅 최적화` 진입 버튼을 제공한다.
- `모델 라우팅 최적화` 진입 버튼은 기존 A/B workspace가 아니라 `/modules/{workflowId}/model-routing/{nodeId}` 전용 추천 화면으로 이동한다.
- 모델 라우팅 추천 화면의 `후보 실험 만들기` 보조 액션만 기존 `/cost-optimizer/{nodeId}` workspace로 이동한다.
- 원본 LLM 노드 상세 설정은 task type 선택 UI를 제공하지 않는다.
- B candidate 영역은 모델, fallback 모델, system prompt, user prompt, assistant prompt, `max_tokens`, `temperature`, 출력 형식을 편집할 수 있다.
- 일반 LLM 노드 상세 화면과 B candidate 영역은 같은 workflow LLM 모델 필터를 사용한다.
- 모델 후보 목록은 alias 계열 모델을 노출하되 날짜 suffix 모델과 `gpt-5-mini`는 숨긴다.
- 모델 후보 목록은 embedding, image, audio, realtime, moderation, tts, whisper, transcribe, sora, search-only 계열을 숨긴다.
- B candidate의 task type은 사용자 입력 UI가 아니라 내부 기본값으로 Cost Optimizer local draft에서 원본 LLM node data 변환까지 보존된다.
- B candidate prompt 입력은 upstream output 변수 삽입을 지원한다.
- prompt 변수 삽입은 candidate `referenced_variables`를 함께 갱신하고 compare/apply request에 포함한다.
- 출력 형식은 text와 JSON을 선택할 수 있다.
- 출력 형식이 JSON이면 JSON schema 편집 영역이 활성화된다.
- 출력 형식이 text이면 JSON schema 편집 영역은 비활성화되거나 숨겨진다.
- JSON schema는 key-type 행 추가 UI로 필드명, 타입, 필수 여부를 편집할 수 있다.
- JSON schema type 후보는 `string`, `number`, `boolean`, `object`, `array`다.
- 1차 UI는 nested field editor를 제공하지 않고 flat key-type row만 편집한다.
- B candidate 영역은 여러 Knowledge Base, `topK`, `scoreThreshold`를 편집할 수 있다.
- B candidate 영역은 중복 근거 제거, 참조 문서 길이 제한, 검색 문서 압축, 답변·검색 문서 어휘 일치도를 편집할 수 있다.
- 새 LLM 노드는 중복 근거 제거, 참조 문서 길이 제한, 검색 문서 압축, 답변·검색 문서 어휘 일치도 기본값을 명시적으로 가지며, 해당 값은 `basic`이 기본이다.
- 참조 문서 길이 제한은 Knowledge/RAG context에만 적용되며 system/user/assistant prompt를 임의로 자르지 않는다.
- 참조 문서 길이 제한이 비어 있으면 compare request는 `retrieved_context_max_chars: null`을 보낼 수 있고, API는 이를 제한 없음으로 허용한다.
- 중복 근거 제거가 켜지면 동일한 retrieved chunk content는 한 번만 LLM context에 들어간다.
- 검색 문서 압축이 켜지면 검색 query와 관련된 문장을 우선 남겨 Knowledge/RAG context를 줄인다.
- 답변·검색 문서 어휘 일치도가 켜지면 LLM 응답과 Knowledge/RAG context의 기본 overlap 결과를 safe metadata로 남긴다.
- B candidate 영역은 고급 파라미터 섹션에서 `top_p`, `presence_penalty`, `frequency_penalty`, `stop`을 편집할 수 있다.
- 고급 파라미터 validation은 기존 LLM node 고급 설정 범위를 따른다.
- 필수 후보 설정이 누락되면 `B 실행` 버튼이 disabled 상태가 되거나 validation message를 표시한다.
- 사용할 수 없는 credential/model 후보는 선택할 수 없거나 실패 후보로 명확히 표시된다.

### API Tests

- `POST /compare`는 `candidate.model_id`, 내부 task type 기본값, prompt, parameters, output_format, knowledge를 request로 받는다.
- `POST /compare`는 `top_p`, `presence_penalty`, `frequency_penalty`, `stop`을 후보 파라미터로 받을 수 있다.
- 잘못된 task type, `max_tokens`, `temperature`, 고급 파라미터, output_format schema, `properties`에 없는 field를 `required`로 지정한 schema, 세 prompt를 모두 명시적으로 비운 후보는 `400 cost_optimizer.invalid_candidate`를 반환한다.
- `candidate.knowledge.retrieved_context_max_chars=null`은 제한 없음으로 허용하고, `0` 이하 값은 `400 cost_optimizer.invalid_candidate`를 반환한다.
- 사용할 수 없는 Knowledge Base 또는 접근 권한이 없는 Knowledge Base는 `422 cost_optimizer.knowledge_unavailable`을 반환한다.
- 현재 Gateway 테스트는 `invalid_candidate`, `knowledge_unavailable`, `model_unavailable`을 검증한다.
- 사용할 수 없는 model 또는 credential은 `422 cost_optimizer.model_unavailable`을 반환한다.
- schema 검증에 실패한 B 후보는 LLM 비용/토큰/시간을 반환하되 `schema_failed` 상태와 `schema_validation.errors`를 포함한다.
- schema 검증에 실패한 B 후보를 apply하려고 하면 `400 cost_optimizer.schema_failed_candidate`를 반환한다.
- 비교 실행은 `comparison_id`로 식별 가능한 `cost_optimizer_experiments` 기록과 `cost_optimizer_candidates` 후보 기록으로 저장된다.
- 현재 Gateway 테스트는 compare 응답의 `schema_failed` 상태, 사용량 보존, comparison_id 저장, schema 실패 후보 apply 차단을 검증한다.

### Scenario Tests

- 사용자가 모델만 바꾸고 B를 실행하면 A baseline과 같은 입력으로 후보 실행 결과가 생성된다.
- 사용자가 prompt와 parameter를 함께 바꿔도 compare request는 하나의 B 후보 설정으로 전송된다.
- 사용자가 Knowledge Base 또는 검색 설정을 바꾸고 B를 실행하면 baseline retrieval을 재사용하지 않고 B 후보 설정 기준으로 retrieval을 새로 수행한다.
- 비교 리포트는 A baseline retrieval summary와 B candidate retrieval summary를 구분해 표시한다.
- 사용자가 JSON schema를 지정하고 B를 실행하면 후보 출력은 schema 검증 결과와 함께 표시된다.
- schema 검증에 실패한 후보는 비용과 출력 preview를 확인할 수 있지만 `현재 노드에 적용`은 사용할 수 없다.
- 사용자가 B 후보를 적용하면 모델, prompt, parameters, output_format, schema, Knowledge/RAG 설정이 일괄 적용된다.

## FR-004 동일 입력 기준 비교

### Component Tests

- A baseline input은 읽기 전용 잠금 상태로 표시된다.
- B candidate 영역은 현재 입력 필드를 직접 수정하지 않고 A baseline input을 사용한다는 안내를 표시한다.
- baseline을 바꾸면 B 실행 전 비교 입력 기준도 함께 바뀐다.

### API Tests

- `POST /compare`는 request의 임의 입력값이 아니라 `baseline_id`에 연결된 target node input을 사용한다.
- baseline input을 복원할 수 없으면 `400 cost_optimizer.baseline_input_unavailable`을 반환한다.
- baseline이 다른 workflow나 node에 속하면 `404 resource.not_found`를 반환한다.

### Scenario Tests

- 사용자가 현재 workflow draft의 앞단 노드 값을 수정해도 이미 선택된 baseline input은 바뀌지 않는다.
- 동일 baseline으로 모델만 바꿔 여러 번 B를 실행하면 입력 preview는 동일하게 유지된다.

## FR-005 하이브리드 비교 실행

### Component Tests

- 화면은 A baseline이 과거 로그이고 B candidate만 새로 실행된다는 점을 표시한다.
- B 실행 중에는 B 영역만 running 상태가 되고, 실행 버튼은 spinner와 `B 실행 중` 텍스트를 함께 표시한다.
- A baseline 영역에는 재실행 spinner나 새 run id가 표시되지 않는다.

### API Tests

- `POST /compare`는 A baseline을 재실행하지 않는다.
- `POST /compare`는 B candidate 실행 결과와 A baseline summary를 함께 반환한다.
- B candidate 실행으로 생성된 run/trace는 `candidate_workflow_run_id`와 `cost_optimizer_candidate_id`로 compare 실행임을 구분할 수 있어야 하며, baseline 후보 조회에는 다시 포함되지 않아야 한다.

### Scenario Tests

- A baseline 로그의 비용/토큰/latency는 B 실행 전후로 변하지 않는다.
- B 실행 실패 시에도 A baseline 정보는 유지되고 실패 원인은 B candidate에만 표시된다.

## FR-006 A/B 비교 화면과 Inspector

### Component Tests

- A/B compare workspace의 실험 설정 mode는 A 실행 시점 옵션, B candidate, 기준 실행 정보 3영역으로 구성된다.
- A/B compare workspace는 `실험 설정`과 `결과 분석` mode switch를 제공한다.
- baseline 선택 전에는 B candidate, 기준 실행 정보, mode switch를 표시하지 않는다.
- baseline 선택 전 첫 화면은 A/B 테스트 기준 선택에 집중한다.
- baseline 선택 단계의 `닫기`와 workspace의 `워크플로우로 돌아가기`는 `/modules/{workflowId}?node={nodeId}`로 이동해 target LLM node 상세 화면을 다시 연다.
- baseline 선택 후 기본 mode는 `실험 설정`이다.
- 사용자는 `결과 분석` mode로 전환할 수 있다.
- B 실행 결과가 없으면 `결과 분석` mode는 B 실행 후 결과 분석이 표시된다는 empty state를 보여준다.
- A/B compare workspace는 특정 workflow와 특정 LLM node의 context를 상단 context bar에 표시한다.
- context bar는 workflow 이름, target LLM node 이름, baseline 실행 시각, 같은 입력 기준 badge, downstream 상태 badge를 표시한다.
- A 실행 시점 옵션 영역은 읽기 전용이고 baseline 실행 당시의 기본 설정, 고급 설정, 지식 베이스 설정을 표시한다.
- 기준 실행 정보 영역은 baseline 모델, 비용, 토큰, latency, 기준 입력 preview, 기준 출력 preview를 표시한다.
- 기준 입력/출력 preview의 긴 값과 여러 JSON field는 `...` 또는 row 개수 제한으로 임의 truncation하지 않고 전체 값을 표시한다.
- B candidate 영역은 `테스트명` 입력을 제공하고 설정 패널의 중복 제목은 표시하지 않는다.
- B candidate 영역은 후보 설정, 실행 상태, 출력 preview, 토큰, 비용, latency, error를 표시한다.
- B 후보 실행 후에도 사용자는 같은 workspace 안에서 B 후보 설정을 수정하고 같은 baseline으로 다시 실행할 수 있다.
- B 후보 설정이 마지막 실행 이후 변경되면 기존 B 결과는 stale 상태로 표시된다.
- B 후보의 RAG 비용 최적화 옵션이 마지막 실행 이후 변경되면 기존 B 결과는 stale 상태로 표시된다.
- 결과 분석 mode 상단에는 접기 가능한 `이전 실험 이력` 패널이 표시된다.
- 이전 실험 이력 패널은 시작일, 종료일, 실행자, 적용 여부, 후보 상태, 모델, Schema 상태, Downstream 상태 필터를 제공한다.
- 이전 실험 이력은 card list가 아니라 실행 시각, 테스트명, 모델, 비용, 토큰, 시간, Schema, Downstream을 열로 갖는 dense table로 표시된다.
- 이전 실험 이력 table body는 내부 스크롤을 사용하고 결과 분석 본문을 과도하게 아래로 밀지 않는다.
- B 후보 실행 후 결과 분석 mode로 이동하면 방금 실행한 후보가 이전 실험 이력 table에서 자동 선택된다.
- 이전 실험 이력에서 다른 row를 선택하면 아래 결과 분석 본문은 해당 후보 기준으로 갱신된다.
- 이전 실험 이력 조회에 실패해도 이미 보유한 compare result가 있으면 결과 분석 본문은 유지된다.
- 결과 분석 mode는 상단에 `적용 후보로 적합`, `주의 필요`, `적용 비추천` 중 하나의 판단 요약을 표시한다.
- 판단 요약은 비용 변화율, token 변화율, latency 변화, B 실행 상태, schema 검증 상태, downstream 호환성 상태를 근거로 표시한다.
- 결과 분석 mode는 비용, prompt tokens, completion tokens, total tokens, latency, 실행 상태, schema, downstream을 A/B/변화값 형태로 비교한다.
- 결과 분석 mode는 A 출력과 B 출력을 나란히 표시하고 긴 값을 임의 truncation하지 않는다.
- JSON 출력과 schema가 있으면 결과 분석 mode는 필수 field 충족 여부, 누락 field, type mismatch를 표시한다.
- 결과 분석 mode의 Inspector는 `설정 차이`, `근거/Trace`, `후속 노드 영향` 탭을 제공한다.
- `설정 차이` 탭은 모델, fallback 모델, task type, prompt, parameter, 출력 형식, JSON schema, Knowledge/RAG 설정 차이를 표시한다.
- `근거/Trace` 탭은 A/B usage trace와 A/B retrieval summary를 구분해 표시한다.
- `후속 노드 영향` 탭은 downstream 호환성 상태, 검사 노드, 계약 검증 warning, side-effect node 자동 실행 제외 안내를 표시한다.

### API Tests

- `POST /compare` response는 `baseline`, `candidate`, `diff`, `downstream_compatibility`를 포함한다.
- `baseline.trace`와 `candidate.trace`는 safe summary만 포함한다.
- candidate 실행 실패 시 response는 부분 결과와 `error_message`를 구분해 반환한다.
- candidate task 제출 또는 대기 중 예외가 발생해도 response는 `candidate.status=failed`와 `error_message`를 반환하고 experiment/candidate row를 failed 상태로 저장한다.

### Scenario Tests

- B 실행 성공 후 사용자는 A와 B의 출력, 비용, 토큰, latency를 한 화면에서 비교하고 적용 판단 요약을 확인할 수 있다.
- 비용은 줄었지만 downstream warning이 있으면 결과 분석 화면은 `주의 필요`로 표시한다.
- B 실행 실패, schema 실패, downstream incompatible 중 하나가 있으면 결과 분석 화면은 `적용 비추천` 또는 그에 준하는 강한 경고를 표시한다.
- B 실행 실패 후에도 사용자는 A baseline과 실패 사유를 볼 수 있다.
- 사용자가 B 실행 결과를 본 뒤 prompt 또는 model을 수정하면 workspace를 닫지 않고 같은 baseline으로 재실행할 수 있다.
- stale 상태의 B 결과는 참고용으로 남지만 현재 후보 설정의 결과가 아니라는 안내를 표시한다.

## FR-007 Downstream 호환성 검증

### Component Tests

- downstream 상태는 `검증 가능`, `주의 필요`, `검증 불가` 중 하나의 명확한 라벨로 표시된다.
- 상태는 색상만으로 구분하지 않고 텍스트 설명을 함께 제공한다.
- `주의 필요` 또는 `검증 불가` 상태에서는 후보 적용 전 추가 확인이 필요하다는 안내를 표시한다.
- side-effect node는 자동으로 downstream 실행하지 않는다는 안내를 표시한다. UI 문구는 `외부 전송이나 쓰기 작업이 있는 downstream 노드는 자동 실행하지 않습니다.`를 포함한다.

### API Tests

- `GET /baselines/latest`는 compare 가능한 baseline을 반환할 때 downstream snapshot을 생성하고, response에는 snapshot 원문이 아니라 `downstream_compatibility` summary만 반환한다.
- `GET /baselines`는 목록 row 생성 또는 baseline 선택 시점에 downstream snapshot을 만들 수 있어야 하며, snapshot 생성 실패 시 compare 가능 여부와 unavailable reason을 명확히 반환한다.
- baseline downstream과 current downstream이 같으면 `compatible`을 반환한다.
- downstream 구조가 일부 달라졌지만 첫 consumer 계약 검증이 가능하면 `warning`을 반환한다.
- target LLM node의 출력 소비자가 사라졌거나 계약 검증이 불가능하면 `incompatible` 또는 `unknown`을 반환한다.
- `POST /compare`는 현재 graph만 보지 않고 baseline downstream snapshot과 B candidate output을 기준으로 `contract_check`를 수행한다.
- current downstream topology가 같더라도 B candidate output이 직접 소비 노드의 필수 selector/path를 만족하지 못하면 `incompatible`과 `contract_check.status=failed`를 반환한다.
- baseline downstream snapshot이 없는 legacy/retention row는 `unknown`과 `contract_check.status=skipped`, `baseline_downstream_snapshot_unavailable` warning을 반환한다.
- `variableExtractionNode`의 `source_selector`가 target LLM node를 가리키면 `mappings[].json_path`가 B candidate output JSON에 존재하는지 검사한다.
- `conditionNode`, `answerNode`, `slackPostNode`는 target LLM node를 참조하는 selector key가 B candidate output에 존재하는지 검사한다.
- `PATCH /apply`는 downstream warning 확인 없이 적용하는 요청을 `400 cost_optimizer.downstream_ack_required`로 거부한다.

### Scenario Tests

- baseline 이후 현재 workflow에서 다음 노드 입력 계약이 바뀐 경우 UI는 `주의 필요`를 표시한다.
- downstream이 크게 바뀐 경우 UI는 `검증 불가`를 표시하고 `workflow 전체 테스트 실행으로 downstream 성공 여부를 별도 확인하세요.` fallback 안내를 제공한다.

## FR-008 B 후보 적용

### Component Tests

- B 후보가 성공 상태일 때만 `현재 노드에 적용` 액션이 활성화된다.
- 적용 전 확인 모달은 변경되는 모델, prompt, 고급 parameter(`max_tokens`, `temperature`, `top_p`, penalty, `stop`), 출력 형식, JSON schema, Knowledge/RAG 설정, downstream 상태를 보여준다.
- 적용 성공 후 현재 LLM node draft는 B 후보 설정으로 갱신된다.
- 적용 후 기존 workflow 저장/테스트 실행 흐름은 유지된다.

### API Tests

- `PATCH /apply`는 B 후보 설정을 current draft target LLM node에 적용한다.
- downstream warning 상태에서 `acknowledge_downstream_warning=false`이면 적용을 거부한다.
- 적용 response는 `updated_draft_revision`을 반환한다.
- `PATCH /apply`는 request의 `candidate_settings`와 일치하는 저장 후보를 `is_applied=true`로 표시하고 `applied_at`, `applied_by`를 기록한다. 같은 experiment의 다른 후보는 적용 표시를 해제한다.
- `PATCH /apply`는 `comparison_id`가 없거나, `comparison_id` 안에서 request의 `candidate_settings`와 일치하는 저장 후보가 없으면 `400 cost_optimizer.candidate_not_found`로 적용을 거부한다.
- draft revision 충돌 감지는 현재 request 계약에 expected draft revision 필드가 없어 후속 보강 범위다.

### Scenario Tests

- 사용자가 B 후보 적용 후 노드 상세 화면으로 돌아오면 변경된 모델과 prompt가 표시된다.
- 적용 후 사용자가 workflow 저장을 수행하면 기존 저장 API 흐름을 그대로 사용한다.

## FR-009 비용 기록과 표시

### Component Tests

- A baseline과 B candidate는 각각 비용, prompt tokens, completion tokens, total tokens, latency를 표시한다.
- 비교 실행 비용은 일반 workflow 실행 비용과 분리되지 않고 추적 대상이라는 안내를 제공한다.
- 가격 정보가 없는 모델은 비용 비교 불가 상태로 표시한다.
- 결과 분석 화면은 이전 experiment 이력을 기간, 실행자, 후보 상태, 모델, 적용 여부, schema 상태, downstream 상태로 필터링할 수 있다.

### API Tests

- B candidate 실행에서 발생한 LLM call은 `llm_usage_logs`에 기록된다.
- usage log에는 workflow id, node id, model, prompt tokens, completion tokens, total tokens, cost, latency, status가 포함된다.
- Cost Optimizer 비교 실행에서 생성되는 usage log는 `cost_optimizer_candidate_id`로 B candidate row를 직접 참조한다.
- `GET /cost-optimizer/experiments`는 같은 workflow/node 기준으로 저장된 experiment와 candidate summary를 조회한다.
- candidate 상태·모델·적용 여부·schema·downstream 필터를 전달하면 조건에 맞는 experiment만 조회하고, 응답의 각 `candidates` 배열에서도 일치하지 않는 후보를 제외한다.
- Cost Optimizer experiment/candidate summary는 trace metadata retention 기준으로 만료일을 계산하고, 만료된 experiment를 정리하면 candidate도 함께 정리된다.
- RAG safe summary는 raw chunk content/source metadata/document filename 계열 값을 제거하고, list 값은 20개, string 값은 200자로 제한한다.
- 비용 계산이 불가능한 모델은 response에 비용 불가 상태를 명확히 반환한다.
- usage response에는 credential 원문, API key, encrypted config가 포함되지 않는다.
- 저장된 candidate summary에는 raw system/user/assistant prompt가 포함되지 않고 redacted summary와 apply 검증용 fingerprint만 남는다.

### Scenario Tests

- 사용자가 B 후보를 여러 번 실행하면 각 실행 비용이 누락 없이 기록된다.
- B 후보 실행 실패 시에도 실패 상태와 측정 가능한 latency/usage가 있으면 기록된다.

## FR-010 권한

### Component Tests

- builder 이상 권한 사용자는 A/B 테스트 진입, B 후보 실행, 후보 적용을 사용할 수 있다.
- viewer/operator 또는 builder 미만 사용자는 Cost Optimizer 액션을 사용할 수 없다.
- 권한 부족 상태는 숨김 또는 disabled 처리와 함께 사유를 표시한다.

### API Tests

- builder 이상 권한이 없는 사용자의 availability, baseline 조회, compare, apply 요청은 `403 permission.denied`를 반환한다.
- 프론트가 버튼을 숨기더라도 API는 서버에서 builder 이상 권한을 다시 검증한다.
- 다른 organization의 workflow/node/baseline 접근은 `404 resource.not_found`로 숨김 처리한다.
- 사용할 수 없는 credential/model 후보는 실행하지 않거나 실패 후보로 반환한다.

### Scenario Tests

- builder 사용자는 baseline 선택부터 B 후보 적용까지 전체 흐름을 완료할 수 있다.
- member/viewer 사용자는 직접 URL 접근을 시도해도 Cost Optimizer API를 사용할 수 없다.

## MBA-322 Grounding/Citation Terminology

- 새 LLM node의 lexical grounding 기본값과 Citation 기본값을 각각 검증한다.
- Cost Optimizer 후보 patch가 `answerGroundingCheck`는 기존 계약대로 변경하되 `citationDisplayMode`는 변경·제거하지 않고 보존하는지 검증한다.
- UI가 lexical overlap 옵션을 Citation 또는 답변 차단 기능으로 오해하게 만드는 기존 `답변 근거 확인` 명칭을 표시하지 않는지 검증한다.

## Regression Tests

- 기존 workflow 생성, 편집, 저장, 테스트 실행, 배포 흐름은 Cost Optimizer 문서 계약 추가 이후에도 변경되지 않는다.
- Cost Optimizer response에는 secret value, credential 원문, API key, token, encrypted config가 노출되지 않는다.
- Cost Optimizer experiment/candidate 저장 summary에는 raw prompt, credential 원문, API key, token, encrypted config가 남지 않는다.
- baseline log picker와 compare response는 권한 없는 RAG source의 raw content, raw source metadata, 숨겨진 문서명을 노출하지 않는다.
