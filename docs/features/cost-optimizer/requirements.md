# Cost Optimizer Requirements

Status: Draft
Related Features: workflow, llm-credentials, knowledge, observability
Verified Against: origin/dev @ 79da97f57aa116bdd73ce51cb2ff365b03bed713

## Purpose

Cost Optimizer는 workflow 안의 LLM 노드 비용을 줄이기 위한 기능이다.

## Current Routing Decision

Current code comparison: `origin/dev @ 79da97f57aa116bdd73ce51cb2ff365b03bed713`

[ADR-0059](../../decisions/ADR-0059-judge-bootstrap-incremental-routing.md)는 현재 Superseded된
versioned V1 계약으로서
`judge_bootstrap_incremental_v1`에서 Judge가 후보 모델을 직접 선택한다고 결정했다. 그러나 현재
FR-011 구현은 Requirement Judge가 세 축의 요구 능력만 반환하고 서버가 후보 모델을 선택한다.
이는 V2 의미가 V1 strategy ID에 부분 반영된 과도기 상태이며, ADR-0059를 조용히 재해석하거나
V2 활성화를 승인하는 근거가 아니다. 목표 계약은
[ADR-0073 Capability Routing V2](../../decisions/ADR-0073-requirement-judge-capability-routing-v2.md)과
FR-016을 따른다.

아래 FR-011은 현재 `judge_bootstrap_incremental_v1` 구현을 재현하기 위한 Current 계약이다. 새 workflow가
"정책이 없어서 routing을 시작할 수 없는" 상태에 머물지 않게, 자동 라우팅을 켠 테스트와
배포 실행은 첫 요청부터 runtime Judge가 현재 요청의 요구 수준을 판정하고, 서버가 현재
실행 주체가 사용할 수 있는 후보 중 capability와 비용을 비교해 모델을 선택한다. 정상 실행
결과가 쌓일수록 로컬 라우터가 Judge를 대신한다.

배포 시에는 실행 주체가 사용할 수 있는 후보 모델로 Judge-first policy를 저장한다. 정책 행이
아직 없거나 삭제된 예외 상황에서도 runtime은 같은 후보 모델로 일회성 Judge-first policy를
구성해 요청을 처리한다. `기본 모델`과 Runtime Judge 모델은 분리한다. 기본 모델은 Judge 판단이
실패하거나 사용할 수 없을 때 요청을 처리하는 안전 복귀 모델이고, Judge는 실행 주체가 사용할 수
있는 후보 중 provider별 평가 선호 모델을 별도로 선택한다. OpenAI 후보에는 이전 라우팅 실험에서
검증한 `gpt-5.4-mini`를 우선 사용한다. `실행 실패 대체 모델`은 선택된 처리 모델 호출이 실패했을
때만 사용한다.

일반 실행은 현재 문의의 주제 키워드나 문장 유사도를 저장·검색하지 않는다. 초기 단계에서는
**변수 치환이 끝난 prompt와 들어온 입력**을 Judge에 일시적으로 전달해, 실행 주체가 쓸 수
있는 전체 후보의 요구 수준을 판정하게 한다. 서버는 그 판정과 모델 catalog를 사용해 실제
처리 모델을 고른다. 검증되지 않은 후보도 현재 credential과 capability 조건을 충족하면 첫
요청부터 선택할 수 있으며, 운영 결과는 이후 정책·학습 품질을 판단하는 근거로만 쓴다. Judge
응답은 요구 수준·신뢰도·안전한 근거만 남기며, 원문 prompt, 입력 payload, RAG 문서 원문은
정책 artifact나 API 응답에 저장하지 않는다.

Runtime Judge 입력은 현재 요청의 JSON key와 scalar type을 보존하고, system/user/assistant prompt를
각각 독립된 길이 예산으로 전달한다. RAG 문서 원문은 개인정보와 영업정보 노출 위험 때문에 전달하지
않는다. 대신 검색 context 크기, chunk/source 수, 근거 충분 여부, 부분 결과 여부, query rewrite 여부와
안전한 부족 사유를 전달한다. 현재 `ModelRouter.routing_feature_text()`는 고정 JSON Schema와 출력
형식도 독립 `OUTPUT_CONTRACT` 예산으로 Runtime Judge 입력에 포함한다. 이는 Current 구현 계약이며,
FR-016 Target에서는 서버가 결정적으로 판정하는 structural hard gate로 이동해 Requirement Judge
입력에서 제외한다. Judge는 작업 복잡도, 결정 영향도, 근거 종합 범위만 0~3으로 판단한다. 서버가
관리하는 후보 profile에는 context window를 포함하며, 서버는 필요한 능력을 충족하는 현재 사용 가능
후보 안에서 비용·지연·fallback을 비교한다.

Judge 판정과 그에 따른 서버 선택은 즉시 학습하지 않는다. 실행 중에는 학습 전 로컬 예측을 먼저 계산하고 원문 없는 숫자 vector, Judge가 판단한
`task_complexity`·`decision_impact`·`evidence_synthesis`, 선택 모델만 대기 label로 저장한다. Current
`ModelRoutingLearningBatchService.train_labels()`는 terminal `accepted`와 `rejected`를 모두 조회하고
유효한 `task_requirements`가 있으면 두 상태 모두 candidate classifier에 한 번 학습한다. `accepted`만
selected-model count와 accepted decision cache에 반영하지만, `rejected`도 judged count와 validation
분모에 포함되고 classifier weight를 변경한다. 이는 현재 호환 동작이자 FR-016 Target과의 알려진 gap이며,
V2 Target에서는 모든 terminal `rejected`를 classifier weight와 accepted training count에서 제외한다.
Label은 배포 정책이 아니라 동일한
`organization + workflow + node + task_fingerprint + judge_contract_hash`의 **자동 라우팅 학습기**에 귀속된다.
Celery worker가 학습기별 10건 또는 최대 5분 단위로
candidate artifact를 갱신하므로 workflow 응답은 학습을 기다리지 않는다. local artifact는 **모델 ID를 직접 예측하지 않고 요청이 요구하는
능력 수준을 예측**한다. 완료된 운영 결과가 최소 표본 수, schema/downstream 성공률, fallback
비율, 선택 모델 분포 편향 기준을 통과하면 로컬 분류기가 먼저 요구 수준을 판단한다. 서버는 그
수준을 충족하는 현재 사용 가능 후보만 남긴 뒤 카탈로그 capability 상한을 만족하는 후보 중 비용이
가장 낮은 모델을 선택한다. JSON Schema와 출력 형식은 노드에서 고정인 capability 제약이므로
난이도 학습 입력에는 넣지 않는다. Runtime Judge는 노드의 고정 prompt 계약을 계속 참고하지만,
로컬 학습 vector는 `referenced_variables`의 실행별 값을 핵심 요청, 동적 문맥, 구조화 특징으로
나누어 각각 임베딩하고 `75% / 15% / 10%` 비율로 결합한다. 변하는 RAG 안전 신호는 구조화
특징에 포함한다. 노드 제목·작업 설명·system/user/assistant prompt의 고정 문구는 로컬 학습 입력에서 제외한다.
학습 feature schema, Judge rubric 또는 E5 encoder가 바뀌면 이전 계보를 읽기 전용으로 보존하고
새 학습기를 0부터 시작한다. prompt, 출력 schema, RAG, downstream 계약이 바뀌어
`task_fingerprint`가 달라져도 새 학습기를 만든다. 반대로 작업 지문과 학습 계약이 같으면
재배포 뒤에도 기존 학습기와 검증 버전을 재사용한다. 로컬 신뢰도가 낮거나 선택 모델의
credential 권한이 바뀐 경우에는 Judge를 다시 호출한다.

검증 게이트를 통과한 candidate artifact는 `learner version`으로 한 번 발행한 뒤 수정하지 않는다.
배포 정책은 학습 데이터나 가중치를 직접 소유하지 않고 `learner_id`와
`active_learner_version_id`만 참조한다. 새 버전이 발행되면 같은 학습기에 연결된 활성 정책이
그 버전을 참조하고 정책 버전도 증가한다. 최근 정확도나 실행 계약 품질이 무너지면 새 버전을
발행하지 않으며, 활성 버전의 안전 기준까지 무너지면 Judge-first로 되돌린다.

계약을 통과한 Judge 판정과 서버 선택 결과는 원문 입력 대신 deployment secret으로 만든 HMAC feature hash로
최대 128개까지 재사용할 수 있다. 같은 안전 feature가 다시 들어오고 해당 모델 권한이 여전히
유효하면 Judge 호출 없이 기존 선택을 사용한다. hash key가 없는 환경에서는 이 최적화를
비활성화하고 기존 Judge 경로를 유지한다.

문장 품질을 평가하기 위한 별도 LLM Judge는 자동 모델 라우팅의 운영 성적에 사용하지 않는다.
대신 완료된 배포 실행의 schema 통과, 후속 노드 성공, provider fallback, 실행 성공 신호를
후보 모델별로 누적한다. 다음 Runtime Judge 호출에는 **두 후보 이상이 각각 5건 이상**의 같은 정책
운영 성적을 보유한 경우에만 이를 함께 전달한다. 한 모델만 성적을 보유한 경우에는 그 모델이 자기
성공 이력을 근거로 다시 선택되는 순환을 막기 위해 운영 성적을 전달하지 않는다.
표본이 부족할 때 카탈로그 값은 초기 선택을 위한 약한 사전 정보로만 사용한다.

모델 카탈로그의 초기 정보는 provider 공식 모델 문서에서 확인한 정식 모델 ID, 별칭,
제품 포지셔닝, 범용·추론 중심 역할과 특화 작업 태그만 포함한다. `gpt-5.6`처럼 provider가 공개한 별칭은
정식 ID `gpt-5.6-sol`과 같은 후보로 취급한다. 공급자 설명은 실제 품질 측정값이 아니므로
서버 selector는 이를 약한 사전 정보로만 사용하고, 동일 노드에서 충분한 schema·downstream·fallback
운영 성적이 쌓이면 운영 증거를 우선한다. 출처가 없는 특화 태그나 측정하지 않은 품질·지연
수치는 카탈로그에 넣지 않는다. 단일 `capability_tier`는 비용 역할이나 특정 작업 적합성을
대표하지 않는다. 카탈로그는 `reasoning_profile`, `complexity_ceiling`, `cost_position`,
`task_affinities`를 분리하며, `complexity_ceiling`은 provider 설명을 Nodease 난이도 체계로
정규화한 값임을 명시한다. 서버 selector는 어느 한 축만으로 모델을 선택하지 않는다.

배포 후 성공 운영 실행이 설정 주기만큼 쌓이면 Judge 요구 판정 label 수와 모델별 품질·비용·지연을
재평가한다. local router가 충분히 학습됐는지는 이 시점의 품질 gate로만 전환한다. 정책 갱신은
정적 rule이나 별도 난이도 모델을 만들지 않고 학습 모드만 조정한다.

이 기능의 첫 번째 목표는 workflow 전체를 A/B 테스트하는 것이 아니라, 사용자가 선택한 특정 LLM 노드 하나에 대해 현재 설정과 후보 설정을 같은 입력 기준으로 비교할 수 있게 하는 것이다.

사용자는 비교 결과를 보고 더 저렴하면서도 결과가 충분히 괜찮은 설정을 선택해 현재 LLM 노드에 적용할 수 있어야 한다.

## Problem

LLM 노드는 workflow 실행 비용의 대부분을 차지할 수 있다.

현재 사용자는 실행 로그에서 토큰과 비용을 확인할 수 있지만, 다음 질문에 바로 답하기 어렵다.

- 이 LLM 노드를 더 저렴한 모델로 바꿔도 결과가 유지되는가?
- 프롬프트를 줄이면 비용이 얼마나 줄어드는가?
- `max_tokens`를 낮춰도 필요한 답변이 나오는가?
- 같은 입력에서 후보별 비용, 토큰, 실행 시간, 출력 결과가 어떻게 다른가?
- 더 저렴한 후보를 선택했을 때 뒤쪽 노드가 깨지지 않는가?
- 비교한 후보를 현재 LLM 노드 설정에 안전하게 적용할 수 있는가?

Cost Optimizer는 이 질문에 답하기 위한 기능이다.

## User Stories

- 빌더로서, workflow 안의 특정 LLM 노드를 선택해 비용 비교를 실행하고 싶다.
- 빌더로서, 현재 LLM 노드 설정과 후보 설정을 같은 입력으로 비교하고 싶다.
- 빌더로서, 후보별 비용, 토큰, 실행 시간, 출력 결과를 한 화면에서 보고 싶다.
- 빌더로서, 더 저렴하지만 결과가 충분한 후보를 현재 LLM 노드 설정에 적용하고 싶다.
- 빌더로서, 선택한 후보의 출력이 다음 노드에서 사용할 수 있는 형태인지 확인하고 싶다.
- 빌더로서, 추천 모달을 벗어나지 않고 최신 성공 실행을 기준으로 추천 설정의 비용, 속도, 출력 품질, schema/downstream 안전성을 빠르게 검증하고 싶다.
- 운영자로서, 비용 최적화 비교 실행에서 발생한 LLM 비용도 일반 실행 비용처럼 기록되기를 원한다.

## Current Implementation Snapshot

현재 구현은 Cost Optimizer 비교와 자동 모델 라우팅을 분리한다.

1. `비교 분석 테스트`: 특정 LLM 노드의 과거 `workflow_node_runs.id`를 baseline으로 직접 선택하고, 같은 입력으로 B candidate를 실행해 결과를 비교한다.
2. `자동 모델 라우팅`: LLM 노드 설정에서 Judge-first 정책을 준비한다. 현재 구현은 runtime Judge 또는 로컬 라우터가 요구 능력을 판정하고 서버가 실행 모델을 선택한다.

baseline 선택 UI는 현재 최신 로그를 자동으로 고정하지 않는다. 사용자는 baseline 목록에서 비교 기준 실행 로그를 직접 선택해야 한다. `GET /baselines/latest` API는 추천 모달 검증과 API 호환을 위해 남아 있지만, A/B workspace 진입의 기본 UX는 “선택 없이 최신 baseline 자동 사용”이 아니다.

정책 기반 자동 모델 라우팅의 실행 기준은 별도 policy 저장소다. 현재 코드는 active policy 저장, runtime 일반 rule 평가, 실행 주체 기준 credential Hard Gate, 운영 run 집계와 전역 모델 profile·노드별 운영 성적 반영을 제공한다. 입력군 발견, 의미 유사도 계산, 입력군별 후보 Replay/Judge 검증은 활성 제품 경로에서 제거했다.

파라미터 추천은 미구현이 아니다. `GET /cost-optimizer/parameter-recommendations`는 배포 후 운영 로그 기반 추천을 반환하고, `PATCH /cost-optimizer/apply-recommendations`는 현재 `direct_policy_update` 성격의 추천만 즉시 draft에 반영한다. 일반 파라미터 변경 추천은 A/B 후보 실험을 거쳐 검증하는 흐름으로 다룬다.

추천 모달 내부의 빠른 검증은 Gateway orchestration API, output quality judge, 프론트 modal panel까지 구현됐다. `테스트하기`는 별도 workspace로 즉시 이동하지 않고, 최신 비교 가능한 성공 실행을 A baseline으로 자동 선택해 모달 안에서 B 후보를 한 번 실행하고 결과를 시각화한다.

## Functional Requirements

Functional Requirement 상태는 다음 기준으로 구분한다.

- `구현 완료`: 현재 코드에서 동작이 확인된 요구사항이다.
- `진행중`: 일부 기반은 있으나 Cost Optimizer 1차 구현 요구사항을 완전히 만족하지 못한 상태다.
- `미완료`: 요구사항은 정의됐지만 아직 구현되지 않은 상태다.

상태 상세는 현재 어디까지 진행됐는지를 더 구체적으로 표시한다.

- `문서화`: 요구사항만 문서화된 상태다.
- `구현 기반 있음`: 기존 코드에 재사용 가능한 기반은 있으나 Cost Optimizer 전용 구현은 없는 상태다.
- `구현 필요`: 코드 구현이 필요하다.
- `후속 기능`: 1차 구현 필수 범위가 아니라 후속 이슈로 분리할 기능이다.
- `버그`: 구현은 있으나 요구사항을 만족하지 못하는 결함 상태다.

시연 중요도는 다음 3단계로 구분한다.

- `P1`: 시연 핵심 흐름에 필수다. 없으면 Cost Optimizer 기능 설명이 어렵다.
- `P2`: 시연 품질과 설득력에 중요하다. 없으면 동작은 가능하지만 완성도가 낮아 보인다.
- `P3`: 장기 사용성과 확장성에 중요하다. 시연 필수는 아니며 후속으로 분리할 수 있다.

| ID | 기능명 | 시연 중요도 | 상태 | 상태 상세 | 요약 |
| --- | --- | --- | --- | --- | --- |
| FR-001 | LLM 노드 단위 A/B 테스트 진입 | P1 | `구현 완료` | `테스트 통과` | LLM 노드 상세 화면에서 해당 노드 기준 A/B 테스트 진입 액션과 availability 검증을 제공한다. |
| FR-002 | A baseline 실행 로그 선택 | P1 | `구현 완료` | `테스트 통과` | baseline 목록에서 사용자가 직접 A 기준 실행 로그를 선택한다. 최신 baseline API는 존재하지만 현재 기본 UX는 자동 선택하지 않는다. |
| FR-003 | 비교 가능한 옵션 | P1 | `구현 완료` | `테스트 통과` | 모델, fallback 모델, prompt, Knowledge/RAG, 고급 파라미터, 출력 형식을 바꿔 비교한다. 작업 유형은 사용자 선택값으로 노출하지 않는다. |
| FR-004 | 동일 입력 기준 비교 | P1 | `구현 완료` | `테스트 통과` | A baseline의 target LLM node 입력을 B 후보 실행 입력으로 고정한다. |
| FR-005 | 하이브리드 비교 | P1 | `구현 완료` | `테스트 통과` | A는 과거 로그로 고정하고 B만 새 설정으로 실행해 비교한다. |
| FR-006 | A/B 비교 화면 | P1 | `구현 완료` | `테스트 통과` | A baseline, B candidate, Inspector 3영역으로 비용/토큰/trace를 비교하고, 결과 분석 화면에서 B 후보를 현재 노드에 적용해도 되는지 판단 요약을 제공한다. |
| FR-007 | Downstream 호환성 검증 | P1 | `구현 완료` | `테스트 통과` | baseline graph와 현재 graph의 downstream 호환성을 3상태로 판정하고 결과 분석 화면에 표시한다. warning/incompatible 후보는 적용 전 사용자 확인이 필요하다. |
| FR-008 | 후보 적용 | P1 | `구현 완료` | `테스트 통과` | 사용자가 성공한 B 후보 설정 전체를 현재 target LLM node draft에 적용한다. downstream warning 확인과 schema 실패 후보 차단을 제공한다. draft conflict 처리는 후속 보강 대상이다. |
| FR-009 | 비용 기록 | P1 | `구현 완료` | `테스트 통과` | 결과 분석 화면은 A/B 비용, prompt/completion/total token, latency를 표시한다. 비교 실행은 전용 experiment/candidate row로 저장되고 usage row가 candidate를 직접 참조한다. 과거 결과 재조회 API와 trace metadata retention 기준 정리를 제공한다. |
| FR-010 | 권한 | P1 | `구현 완료` | `UI/API 권한 기반 구현, 테스트 통과` | A/B 테스트와 후보 적용은 builder 이상 권한이 있는 사용자만 수행한다. compare/apply/history API와 모델/Knowledge 후보 사용 가능성 검증이 적용됐다. |
| FR-011 | Judge Bootstrap 점진 학습 자동 모델 라우팅 | P1 | `진행중` | `현재 경로 구현, ADR-0059와 strategy/learner 계약 불일치` | 초기 운영 요청은 Judge가 요구 수준을 판정하고 서버가 모델을 고르지만, V2 의미가 V1 ID에 섞였고 learner gate/rejected label 계약도 어긋난다. 현재 회귀 테스트 통과가 V2 완료를 뜻하지 않는다. |
| FR-012 | LLM 파라미터 추천 룰셋 | P2 | `진행중` | `서비스/API/UI 일부 구현` | 운영 로그 기반 추천 API와 추천 모달이 있다. 모델 라우팅 enable/refresh 같은 `direct_policy_update`는 즉시 적용 가능하고, 일반 파라미터/RAG 조정은 A/B 후보 실험으로 검증한다. |
| FR-013 | Cost Optimizer 후보 검증 및 출력 품질 평가 | P1 | `구현 완료` | `추천 빠른 검증·일반 compare quality judge·이력 저장·결과 분석 UI 및 targeted test 통과` | 추천 모달과 일반 비교 분석 테스트에서 동일 입력의 A/B 출력을 평가해 비용·속도·token·품질 점수·JSON schema·downstream 호환성을 보여주고, 같은 결과를 적용하거나 다시 조회한다. |
| FR-014 | 배포별 자동 파라미터 최적화 | P2 | `진행중` | `배포 설정·운영 수집·상태/예산 UI 구현` | 배포 시 선택한 LLM 노드의 운영 실행을 수집하고, 점검 주기와 월간 검증 예산을 분리해 관리한다. 모델 라우팅·모델 선택·프롬프트 변경은 포함하지 않는다. |
| FR-015 | 제약·난이도 라우터 실험 경계 | P3 | `미구현` | `실험 계약만 작성` | Constraint/prior 전략의 expected result matrix만 정의돼 있다. 명시한 script·test·report artifact는 현재 tree에 없으며 active policy와 제품 UI를 변경하지 않는다. |
| FR-016 | Capability Routing V2 목표 계약 | P1 | `미구현` | `정책·테스트 계약만 작성` | V1과 분리된 strategy, Requirement Judge/서버 selector 책임, contract-bound cache/learner와 activation governance를 정의한다. MBA-372 gate 전에는 기본 비활성이다. |

### FR-001. LLM 노드 단위 A/B 테스트 진입

Cost Optimizer의 비교 단위는 workflow 전체가 아니라 특정 LLM 노드 하나다.

- 사용자는 workflow 편집 화면에서 LLM 노드를 선택해 비용 비교를 시작할 수 있어야 한다.
- LLM 노드 상세 화면에는 이 노드에 대해 A/B 테스트를 시작하는 액션이 있어야 한다.
- 비교 대상은 `llmNode`로 제한한다.
- LLM 노드가 아닌 노드에서는 비용 비교를 실행하지 않는다.

### FR-002. A baseline 실행 로그 선택

사용자는 A/B 테스트를 시작할 때 A 기준이 되는 baseline 실행 로그를 선택해야 한다.

A baseline은 특정 실행 시점의 target LLM node 입력, 출력, 설정, 비용, 토큰, trace를 가진 비교 기준이다.

A baseline의 canonical id는 `workflow_node_runs.id`다. `workflow_runs`는 baseline이 속한 전체 실행 컨텍스트이고, `llm_usage_logs`는 비용/토큰/모델 원천이며, `trace_payloads`는 redaction-safe input/output preview와 trace 존재 여부의 원천이다.

Baseline 후보는 target LLM node가 성공적으로 완료된 `workflow_node_runs` 중 output preview와 usage summary를 모두 제공할 수 있는 기록만 포함한다. 실패한 node run, output preview가 없는 node run, usage summary가 없는 node run은 Cost Optimizer baseline 후보에서 제외하며, 실패 원인 분석이나 불완전한 실행 기록 확인은 실행 로그/trace 화면의 책임으로 둔다.

사용자는 baseline 목록에서 특정 실행 로그를 직접 골라 A baseline을 정한다.

현재 UI는 `최신 실행 로그로 비교하기` CTA로 baseline을 자동 고정하지 않는다. target LLM node의 성공한 실행 기록 중 가장 최근 비교 가능 baseline을 조회하는 API는 존재하지만, Cost Optimizer workspace는 사용자가 기준 실행을 확인하고 선택한 뒤에만 B candidate 편집 영역을 연다.

baseline 선택 화면은 로그 선택 화면을 열고, 사용자가 특정 실행 로그를 직접 고르게 한다.

로그 선택 화면은 다음 정보를 제공해야 한다.

- 실행 시각
- 실행 상태
- 사용 모델
- target LLM node 비용
- target LLM node 토큰
- target LLM node 실행 시간
- 입력 preview
- 출력 preview
- trace 존재 여부
- downstream 호환성 상태

로그 선택 화면은 검색, 필터링, 정렬을 지원해야 한다. 필요한 경우 이를 위한 API를 새로 추가하는 것을 허용한다.

baseline input을 복원할 수 없는 실행 로그도 목록에는 표시한다. 다만 이런 row는 `비교 불가` 상태로 표시하고 A/B 비교 실행은 막는다. output preview 또는 usage summary가 없는 실행 로그는 baseline 목록에서 제외한다.

### FR-003. 비교 가능한 옵션

사용자는 B 후보를 구성할 때 여러 설정을 바꿔가며 최적화할 수 있어야 한다.

B 후보는 빈 설정에서 시작하지 않는다. 사용자가 A/B 비교를 시작하면 B 후보는 현재 LLM 노드 설정의 복사본으로 초기화된다. 사용자는 복사된 설정에서 필요한 항목만 바꾸고 B 후보를 실행한다.

1차 구현에서 후보별로 비교할 수 있는 옵션은 다음과 같다.

- 모델
- fallback 모델
- system prompt
- user prompt
- assistant prompt
- `max_tokens`
- `temperature`
- 출력 형식: text 또는 JSON
- JSON schema
- Knowledge Base 선택
- `topK`
- `scoreThreshold`

모델 후보 목록은 기존 LLM 노드 상세 편집에서 사용하는 모델 조회 경로를 재사용한다. 현재 프론트의 기존 구현은 `GET /api/v1/llm/my-models`와 모델 선택 컴포넌트를 사용한다. Cost Optimizer는 별도 모델 목록 API를 새로 만들기보다, 동일한 모델/credential 접근 기준을 사용한다. 다만 compare API는 최종적으로 선택된 모델과 credential 사용 가능 여부를 다시 검증해야 한다.

일반 LLM 노드 상세 화면과 Cost Optimizer B 후보 화면은 같은 모델 노출 필터를 사용해야 한다. 두 화면에서 선택 가능한 모델이 다르면 사용자가 현재 노드에는 적용할 수 없는 후보를 A/B 테스트하거나, 반대로 원본 노드에서 선택 가능한 모델을 후보에서 찾지 못하는 문제가 생긴다.

모델 노출 정책은 다음을 따른다.

- alias 계열 모델만 노출하고 실행 후보로 사용한다.
- 날짜 suffix가 붙은 고정 버전 모델은 가격·과거 이력 조회에는 남길 수 있지만, 새 LLM 노드 선택과 자동 모델 라우팅 후보에서는 제외한다.
- `gpt-5-mini`는 가격·과거 이력 조회에는 남기지만 모든 새 workflow 실행에서 제외한다. 기존 graph가 직접 참조하더라도 Provider 호출 전에 차단하고, 사용할 수 있는 fallback이 있으면 fallback으로 실행한다.
- embedding, image, audio, realtime, moderation, tts, whisper, transcribe, sora, search-only 계열은 일반 LLM 노드와 Cost Optimizer 후보에서 모두 숨긴다.
- 최신 alias 모델은 provider와 무관하게 whitelist에 포함한다.
- 가격 정보가 없는 모델은 선택 가능하더라도 결과 분석에서 비용 계산 불가 상태로 표시한다.

LLM 노드 상세 화면과 Cost Optimizer B 후보 설정 화면은 `task type`을 사용자가 직접 고르는 입력으로 노출하지 않는다. 현재 legacy 저장값은 호환을 위해 읽을 수 있다. FR-016 Target에서는 누락값을 `unspecified`로 정규화하며 Client 기본값이나 prompt 추측으로 `generate`를 영속하지 않는다.

LLM 노드 상세 화면의 모델 설정 UX는 다음을 따른다.

- 자동 모델 라우팅 OFF 상태에서는 기본 모델과 fallback 모델 선택 UI를 표시한다.
- 자동 모델 라우팅 ON 상태에서는 기본 모델과 fallback 모델 선택 UI를 숨기고 active policy 상태를 표시한다.
- 초기 `judge_first` 상태의 배포 운영 실행은 active policy가 정한 후보 집합 안에서 Judge LLM으로 요구 능력을 판정하고 서버가 모델을 선택한다. 충분한 Judge label과 건강한 운영 결과가 쌓인 `local_first` 상태에서는 로컬 라우터가 요구 능력을 먼저 예측하며, 확신이 낮은 요청만 Judge로 되돌린다.
- 정책 갱신은 새 validated Replay evidence, 운영 evidence threshold, model availability/drift, 사용자의 수동 요청으로 수행한다. `refresh_every_runs` 기본 20회는 호환용 주기 재평가 trigger이며 모델 변경을 보장하지 않는다.
- 작업 유형 입력은 표시하지 않는다.

프롬프트 편집은 기존 LLM 노드 상세 편집과 마찬가지로 변수 삽입을 지원해야 한다. 사용자는 upstream output 변수를 system/user/assistant prompt에 삽입할 수 있어야 하며, 등록되지 않은 변수는 실행 전에 validation으로 드러나야 한다.

고급 설정으로 다음 옵션도 비교할 수 있어야 한다.

- `top_p`
- `presence_penalty`
- `frequency_penalty`
- `stop`

출력 형식과 JSON schema는 downstream 안정성에 영향을 줄 수 있으므로 비용 비교 옵션에 포함한다. 예를 들어 자유 텍스트 출력, JSON 출력, 특정 JSON schema를 만족하는 출력을 비교할 수 있어야 한다.

JSON schema 편집은 1차 구현에서 key-type 행 추가 UI로 제공한다. 각 행은 field key, type, required 여부를 가진다. type 후보는 `string`, `number`, `boolean`, `object`, `array`다.

Nested schema는 `object`나 `array` 타입 필드 안에 다시 하위 필드 구조를 정의하는 schema를 뜻한다. 예를 들어 `customer: { name: string, tier: string }`처럼 객체 안의 속성까지 편집하는 것이다. 1차 UI는 flat key-type 행 편집을 기본으로 하며, `object`와 `array` 타입은 선택할 수 있지만 하위 필드 편집 UI는 후속으로 둔다. nested 구조가 반드시 필요한 경우에는 raw schema 편집 또는 후속 schema editor에서 다룬다.

JSON schema를 지정한 B 후보가 LLM 호출에는 성공했지만 schema 검증에 실패한 경우, 후보 실행 자체는 비용/토큰/시간과 함께 결과로 남긴다. 다만 해당 후보의 결과 상태는 `schema_failed`로 표시하고, 현재 노드에 적용할 수 없게 한다.

Knowledge/RAG 설정은 후보 B에서 편집 가능하다. 같은 baseline input이라도 참조하는 Knowledge Base, 검색 개수, score threshold가 달라지면 출력 품질과 비용이 달라질 수 있기 때문이다.

Knowledge Base는 여러 개 선택할 수 있다.

RAG를 곁들인 LLM 노드는 비용을 줄이더라도 author가 직접 작성한 system/user/assistant prompt를 임의로 자르지 않는다. 비용 최적화 대상은 검색으로 주입되는 동적 context와 근거 품질 검증이다.

Knowledge/RAG 비용 최적화 옵션은 다음 4개를 우선 제공한다.

- 중복 근거 제거: 검색된 문서 조각 중 내용이 거의 같은 근거를 한 번만 사용한다.
- 참조 문서 길이 제한: Knowledge Base에서 가져온 문서 context의 최대 길이를 제한한다. 직접 작성한 prompt 3종은 이 제한 대상이 아니다.
- 검색 문서 압축: 검색된 문서를 그대로 넣지 않고 질문과 관련된 핵심 내용만 줄여 전달한다.
- 답변·검색 문서 어휘 일치도: 생성된 답변과 검색 문서의 lexical overlap metadata를 기록한다. Citation 표시나 답변 차단 기능은 아니다.

B 실행 시 Knowledge/RAG를 사용하면 baseline의 과거 retrieval 결과를 재사용하지 않는다. B candidate의 현재 Knowledge Base 선택, `topK`, `scoreThreshold` 기준으로 retrieval을 새로 수행한다. 그래야 모델/prompt뿐 아니라 retrieval 설정 변경이 실제 후보 결과에 반영된다.

A baseline의 retrieval summary는 비교 기준 정보로만 표시한다. Inspector는 A가 어떤 Knowledge Base와 문서를 참고했는지, B가 새로 어떤 Knowledge Base와 문서를 참고했는지를 나란히 보여준다. 단, raw document content나 secret payload는 표시하지 않는다.

선택 불가능하거나 접근 권한이 없는 Knowledge Base는 프론트 목록에서 제외하는 것을 우선한다. 그러나 보안 경계는 API다. compare API는 request의 `knowledge_base_ids`가 현재 사용자와 organization/workflow scope에서 사용 가능한지 다시 검증하고, 사용할 수 없으면 `422 cost_optimizer.knowledge_unavailable`을 반환한다.

B 후보 설정을 현재 노드에 적용할 때는 선택 항목별 부분 적용을 제공하지 않는다. 사용자는 B 후보 설정 전체를 current draft의 target LLM node에 일괄 적용한다.

B 후보 설정 validation은 두 단계로 처리한다. 프론트는 명백히 잘못된 값이면 B 실행 버튼을 비활성화하거나 field-level message를 표시한다. API는 동일한 규칙을 최종 검증하고 잘못된 후보 설정이면 `400 cost_optimizer.invalid_candidate`를 반환한다. 프론트 validation은 UX이며, API validation이 최종 계약이다.

후보를 실행하면 비교 리포트가 생성된다. 사용자는 리포트에서 A baseline과 B candidate의 출력, 비용, 토큰, latency, schema 검증 상태, retrieval summary, downstream 호환성 상태를 확인한 뒤 B 설정을 적용할지 결정한다.

결과 분석 화면은 단순히 A/B 값을 나열하는 화면이 아니라, B 후보를 현재 LLM 노드에 적용해도 되는지 판단하게 하는 화면이어야 한다. 따라서 결과 분석 화면은 다음 정보를 우선순위 있게 보여준다.

1. 적용 판단 요약
2. 핵심 지표 비교
3. A/B 출력 품질 비교
4. 설정 차이, trace, downstream 영향 같은 상세 근거

적용 판단 요약은 다음 3상태 중 하나로 표시한다.

- `적용 후보로 적합`: B 실행이 성공했고, schema/downstream 치명 문제가 없으며 비용 또는 토큰 개선이 확인되는 상태다.
- `주의 필요`: B 실행은 성공했지만 latency 증가, 비용 증가, schema 경고, downstream warning처럼 적용 전 확인이 필요한 상태다.
- `적용 비추천`: B 실행 실패, schema 실패, downstream incompatible, 비용/토큰 악화만 확인되는 상태다.

판단 요약은 비용만으로 결정하지 않는다. 비용/토큰/latency 변화, schema 검증 상태, downstream 호환성, B 실행 상태를 함께 고려한다.

비교 실행은 일회성 응답으로만 버리지 않는다. B 후보 실행은 LLM 비용을 발생시키므로 비교 실행 기록, 후보 설정, 사용량, schema 검증 결과, retrieval summary, downstream 호환성 상태를 추적 가능하게 저장해야 한다.

RAG strategy 비교, Knowledge Skill version 비교, 최적화 에이전트는 1차 구현의 필수 범위는 아니지만 후속 확장 후보로 둔다. 모델 라우팅은 FR-011의 정책 기반 자동 라우팅으로 별도 정의한다.

### FR-004. 동일 입력 기준 비교

후보 B는 A baseline 실행 로그의 target LLM node 입력을 기준으로 실행되어야 한다.

같은 입력 기준 비교가 필요한 이유는 후보 간 결과 차이가 입력 차이 때문인지 설정 차이 때문인지 섞이지 않게 하기 위해서다.

A baseline은 이미 실행된 로그이므로 A를 다시 실행하지 않아도 된다.

B 후보는 A baseline의 입력을 사용해 새 설정으로 실행한다.

A baseline의 target LLM node input을 복원할 수 없으면 B 후보 실행을 시작하지 않는다. 이 경우 사용자는 해당 실행 로그가 목록에 보이더라도 비교 기준으로 선택할 수 없거나, 선택 후 compare 실행 전에 차단 안내를 받아야 한다.

### FR-005. 하이브리드 비교

Cost Optimizer는 하이브리드 비교 방식을 사용한다.

하이브리드 비교란, 비교 대상 LLM 노드 앞단의 입력은 baseline 실행 로그에서 가져오고, 그 동일한 입력을 후보 LLM 설정에 넣어 비교하는 방식이다.

```text
A baseline 로그 선택
→ A의 target LLM node 입력 고정
→ B 후보 설정 실행
→ 후보별 비용/토큰/시간/결과 비교
```

### FR-006. A/B 비교 화면

A/B 비교 화면은 baseline A와 candidate B를 나란히 비교할 수 있어야 한다.

이 화면은 범용 대시보드가 아니라 특정 workflow 안의 특정 LLM node에 종속된 A/B compare workspace다. 사용자는 workflow 편집 화면에서 target LLM node를 선택해 workspace로 진입하고, 이 workspace 안에서 같은 target node에 대한 baseline 선택, B 후보 편집, B 실행, 결과 비교, 재편집, 재실행, 적용까지 반복할 수 있어야 한다.

비교 루프는 클릭 수가 많지 않아야 한다. 사용자가 B 실행 결과를 확인한 뒤 모델, prompt, schema, Knowledge/RAG, 고급 파라미터를 수정하고 다시 실행하는 흐름은 같은 화면 안에서 이어져야 한다. B 후보 설정을 수정할 때마다 화면을 닫거나 baseline을 다시 선택하게 해서는 안 된다.

실험 설정 화면의 기본 레이아웃은 3개 영역으로 구성한다.

- 왼쪽: A baseline
- 가운데: B candidate
- 오른쪽: Inspector

오른쪽 Inspector는 A 또는 B의 상세 trace와 비교 보조 정보를 볼 수 있는 영역이다.

Inspector에는 다음 정보를 표시할 수 있어야 한다.

- input
- output
- prompt/messages 요약
- token/cost/latency breakdown
- LLM usage trace
- RAG를 사용한 경우 참조한 문서 또는 retrieval summary
- error
- downstream 호환성 상태

기존 고급 설정, 지식 베이스 설정, 비교 설정은 A/B 화면 안의 상단 탭 또는 Inspector 탭으로 접근할 수 있어야 한다. 별도 패널을 계속 중첩해서 열어 화면이 복잡해지지 않게 한다.

결과 분석 화면은 다음 순서로 구성한다.

1. `이전 실험 이력`: 같은 baseline 기준 B 후보 실행 이력을 상단 dense table로 표시하고 분석 대상을 선택한다.
2. `판단 요약`: 적용 후보로 적합, 주의 필요, 적용 비추천 중 하나와 그 이유를 표시한다.
3. `핵심 지표 비교`: 비용, prompt tokens, completion tokens, total tokens, latency, 실행 상태, schema, downstream을 A/B/변화값으로 비교한다.
4. `출력 품질 비교`: A 출력과 B 출력을 나란히 보여주고, JSON/schema가 있으면 필수 필드 충족 여부와 누락/타입 문제를 확인할 수 있게 한다.
5. `상세 Inspector`: 설정 차이, 근거/trace, 후속 노드 영향을 탭으로 제공한다.

각 후보 결과에는 다음 항목을 표시해야 한다.

- 후보 이름
- 모델
- 실행 상태
- 출력 결과 미리보기
- prompt tokens
- completion tokens
- total tokens
- estimated cost
- latency
- error message

비용이 낮더라도 출력 결과가 부적절하면 사용자가 선택하지 않을 수 있어야 한다. 결과 분석 화면은 비용 절감 결과와 품질/호환성 위험을 분리해서 보여줘야 한다.

현재 구현은 Cost Optimizer 전용 workspace에서 B 후보 실행 결과를 A baseline과 비교해 표시한다. 결과 분석 화면은 후보별 출력, 비용, 토큰, latency, schema 검증 상태, retrieval summary, downstream 호환성 상태를 함께 보여준다.

### FR-007. Downstream 호환성 검증

선택한 A baseline 로그의 downstream과 현재 target LLM node의 downstream이 다를 수 있다.

downstream이 달라지면 A/B 비교 자체는 가능하더라도, 그 비교 결과가 현재 workflow에서 그대로 의미 있다고 보기 어렵다. 따라서 Cost Optimizer는 downstream 호환성 상태를 사용자에게 명확히 알려야 한다.

downstream 호환성은 다음 3상태로 표시한다.

- `검증 가능`: baseline 실행 시점의 target node 이후 downstream 구조와 현재 downstream 구조가 동일하거나 호환된다.
- `주의 필요`: downstream 구조는 달라졌지만 target node의 바로 다음 소비 노드는 동일하거나 입력 계약이 유지된다.
- `검증 불가`: target node 이후 소비 노드가 바뀌었거나 필요한 입력 계약이 달라져 downstream 검증을 신뢰할 수 없다.

상태별 의미는 다음과 같다.

- `검증 가능`: A/B 결과와 downstream 계약 검증을 현재 workflow 판단에 사용할 수 있다.
- `주의 필요`: LLM output 비교는 가능하지만, 최종 적용 전 현재 workflow 테스트 실행으로 확인해야 한다.
- `검증 불가`: LLM output 비교만 참고할 수 있고, downstream 성공 여부는 현재 workflow에서 별도로 검증해야 한다.

1차 구현에서는 downstream 전체를 자동 실행하지 않는다.

대신 선택 후보의 LLM output이 다음 소비 노드의 필수 입력 계약을 만족하는지 검증한다.

baseline 생성 또는 baseline 조회 시점에는 target LLM node의 downstream snapshot을 함께 만들어야 한다. 이 snapshot은 A baseline을 선택한 시점의 후속 소비 노드, edge, 입력 selector, 필수 output key/path, side-effect 여부를 담는 safe metadata다. raw payload, credential, prompt 원문, secret 값은 포함하지 않는다.

Compare 실행은 현재 workflow graph만으로 downstream을 판정하지 않는다. `workflow_node_runs.id`로 식별되는 A baseline의 downstream snapshot과 B candidate output을 기준으로 contract check를 수행해야 한다. snapshot이 없으면 legacy/retention 데이터로 보고 `unknown` fallback을 반환할 수 있지만, 신규 baseline 생성/조회 경로에서는 snapshot 누락을 정상 상태로 취급하지 않는다.

예:

- 다음 노드가 변수 추출 노드라면 필요한 값을 추출할 수 있는지 확인한다.
- 다음 노드가 조건 분기 노드라면 조건에 필요한 값이 존재하는지 확인한다.
- 다음 노드가 응답/Slack 노드라면 템플릿에 필요한 변수가 채워질 수 있는지 확인한다.

Slack 전송, HTTP 요청, DB write처럼 외부 side effect가 있는 노드는 1차 구현에서 자동 실행하지 않는다.

### FR-008. 후보 적용

사용자는 비교 결과 중 하나를 선택해 현재 LLM 노드 설정에 적용할 수 있어야 한다.

- 적용 대상은 현재 workflow draft의 target LLM node다.
- 적용 가능한 값은 비교 가능한 옵션과 같다.
- 적용 후 사용자는 기존 workflow 저장/테스트 실행 흐름을 그대로 사용할 수 있어야 한다.
- 저장된 experiment 후보를 적용한 경우, 결과 이력에서 어떤 후보가 적용됐는지 확인할 수 있도록 해당 후보의 적용 상태와 적용 시각/사용자를 기록해야 한다.

후보 적용 시 A baseline의 과거 설정을 현재 draft에 되돌리는 동작이 아니라, 사용자가 선택한 B 후보 설정을 현재 target LLM node에 적용하는 동작이다.

downstream 호환성 상태가 `주의 필요` 또는 `검증 불가`인 경우, 적용 전에 현재 workflow에서 추가 검증이 필요하다는 안내를 표시해야 한다.

### FR-009. 비용 기록

비교 실행에서 발생한 LLM 호출도 일반 workflow 실행과 동일하게 usage log에 기록되어야 한다.

기록 대상은 다음과 같다.

- workflow id
- node id
- model
- prompt tokens
- completion tokens
- total tokens
- cost
- latency
- status

비용 최적화 기능 자체의 비용이 숨겨지면 안 된다.

비교 실행은 다음 두 저장 단위로 추적한다.

- `cost_optimizer_experiments`: 하나의 A/B 테스트 세션을 저장한다. 특정 workflow, target LLM node, A baseline `workflow_node_runs.id`, 시작 사용자, 세션 상태를 가진다.
- `cost_optimizer_candidates`: 하나의 세션 안에서 실행한 B 후보를 저장한다. 후보 설정 snapshot, 후보 실행 run/node run 참조, 비용/토큰/latency, schema 검증 결과, retrieval summary, downstream 호환성 결과, 적용 여부를 가진다.

A/B 테스트 시작 1회는 새 `cost_optimizer_experiments` 1개로 기록한다. 같은 baseline을 사용하더라도 사용자가 나중에 다시 A/B 테스트를 시작하면 기존 experiment를 재사용하지 않고 새 experiment를 만든다. 하나의 experiment 비용 합계는 해당 experiment에 속한 candidate 실행 비용만 포함한다. 같은 baseline 기준 누적 비용이 필요하면 `baseline_node_run_id`가 같은 여러 experiments를 합산한다.

결과 분석 화면은 같은 workflow, 같은 target LLM node, 같은 baseline 기준으로 과거 experiments와 candidates를 다시 조회할 수 있어야 한다. 사용자는 기간, 실행자, 후보 상태, 모델, 적용 여부, schema 검증 상태, downstream 상태 같은 조건으로 이전 실험 결과를 좁혀 볼 수 있어야 한다. 후보 조건을 사용하면 조건에 맞는 experiment 안에서도 일치하는 candidate만 결과에 포함해야 한다.

기존 `workflow_runs`, `workflow_node_runs`, `llm_usage_logs`, `trace_payloads`는 실행/trace/비용의 원천으로 유지한다. Cost Optimizer 전용 테이블은 이 원천 데이터를 대체하지 않고, A baseline과 여러 B 후보 실행을 하나의 비교 흐름으로 묶기 위한 메타데이터를 저장한다.

현재 코드에는 일반 workflow LLM 호출의 token, cost, latency를 `llm_usage_logs`와 workflow run 집계에 기록하는 기반이 있다. Cost Optimizer compare는 `comparison_id`가 되는 `cost_optimizer_experiments` row와 B 후보의 `cost_optimizer_candidates` row를 저장한다. B 후보 실행에서 생성되는 usage row는 `llm_usage_logs.cost_optimizer_candidate_id`로 후보 row를 직접 참조한다. 과거 experiment/candidate summary 재조회 API도 제공한다. experiment/candidate summary는 trace metadata retention 정책의 `metadata_retention_days`를 따르고, 만료된 experiment는 candidate와 함께 정리한다.

### FR-010. 권한

비용 비교와 후보 적용은 workflow를 수정할 수 있는 builder 이상 권한이 있는 사용자만 수행할 수 있다.

Cost Optimizer의 A/B 테스트는 단순 실행 기능이 아니라, LLM 노드 설정 후보를 만들고 현재 draft에 적용할 수 있는 편집 도구다. 따라서 실행 권한만 가진 사용자가 비용 비교를 수행할 수 있게 하지 않는다.

- builder 이상 권한이 없으면 A/B 테스트를 실행할 수 없다.
- builder 이상 권한이 없으면 후보를 현재 노드에 적용할 수 없다.
- 사용할 수 없는 credential/model 후보는 실행하지 않거나 실패 후보로 표시한다.

현재 Gateway의 Cost Optimizer availability, baseline 조회, experiment history, compare, apply API는 workflow `write` 권한을 요구한다. 프론트 진입 액션은 builder 미만 사용자에게 비활성화 상태와 권한 부족 안내를 제공한다. compare/apply API는 선택한 모델 후보가 현재 사용자의 사용 가능 모델 목록에 있는지 확인하고, Knowledge Base 후보가 현재 organization/workflow scope에서 `use` 가능한지 다시 검증한다.

### FR-011. 정책과 분리된 점진 학습 자동 모델 라우팅

자동 모델 라우팅은 입력 문장을 입력군으로 저장하거나 주제 키워드 rule을 하드코딩하지
않는다. 초기에는 Judge가 현재 요청과 노드 계약을 읽어 요구 수준을 판정하고, 서버가
현재 사용 가능한 전체 후보에서 처리 모델을 선택한다. 이후에는 그 판단과 실제 실행
결과로 학습한 로컬 라우터가 먼저 요구 수준을 예측한다.

#### 초기 정책 생성

- 사용자는 LLM 노드에서 자동 라우팅을 켜고 작업 설명, 기본 모델, 기본 대체 모델을 입력한다.
- bootstrap은 system/user/assistant prompt, 입력 매핑, 출력 형식과 schema, RAG 설정,
  후속 노드 계약을 읽어 Judge 요청 계약과 로컬 router artifact 초기 상태를 만든다.
- bootstrap 생성은 외부 LLM을 호출하지 않으며 즉시 `ready` 상태가 된다.
- 초기 Judge는 현재 요청별로 세 요구 축, `confidence`, `reason_code`를 반환한다. 모델 ID는
  반환하지 않는다. 서버는 catalog capability·가격과 현재 credential 조건을 적용해 전체
  후보에서 선택한다. 후보가 아직 운영 검증을 통과하지 않았다는 이유만으로 제외하지 않는다.
  Judge label은 로컬 요구 수준 분류기의 학습 데이터이며 runtime keyword rule이 아니다.
- 실행 중 메모리에 있는 실행 변수 중심 텍스트로 multilingual E5 vector와 **학습 전 예측**을 만든 뒤,
  원문 대신 숫자 vector·예측·Judge 판정·서버 선택 결과만 대기 저장한다. workflow 완료 뒤 schema 통과,
  후속 노드 성공, fallback 미발생을 확인해 label을 확정한다. 실제 가중치 학습은 Celery가
  학습기 행을 잠그고 10건 또는 최대 5분 단위로 처리한다. RAG 문서 원문은 Judge와
  artifact에 넣지 않고 retrieval 사용 여부·문맥 길이·출처 수 같은 구조 정보만 쓴다.
- `judged_request_count`는 요구 수준 3축을 정상 반환한 Judge 판정 수다. 실행 계약을 통과하지
  못한 유효 판정도 품질 gate의 표본과 계약 통과율에는 포함하지만, 실패한 선택을 반복하지
  않도록 로컬 분류기 가중치 학습에는 사용하지 않는다.
- 대기 label 저장 transaction은 최종 작업 모델의 provider 호출 전에 commit 또는 rollback한다.
  같은 학습기를 사용하는 동시 실행이 learner row lock을 외부 네트워크 I/O 구간까지 유지하거나
  서로의 최종 모델 실행과 timeout 처리를 막아서는 안 된다.
- prompt, 입력 매핑, 출력 schema, RAG 또는 downstream 계약이 바뀌면 새 작업 지문의 학습기를
  만든다. 기본·대체 모델이나 점검 주기만 바뀐 경우에는 기존 학습기를 재사용한다.

#### 실행 시 모델 선택과 점진 전환

- `judge_first`: 현재 구현은 처리된 Judge label이 100건 미만이거나 최근 평가 window 50건을 채우지
  못하면 매 운영 요청에 Judge를 호출한다.
- `local_first`: 현재 구현은 Judge 요구 수준 label이 100건 이상 쌓이고 최근 50건의 학습 전 예측
  exact 일치율 75%,
  축별 평균 오차 0.5 이하, 계약 통과율 95%와 완료된 운영 품질 기준을 통과하면 로컬
  E5 vector 기반 선형 분류기가 요구 수준을 먼저 예측한다.
- `local_first` 상태에서 local prediction의 confidence가 기준 미만이거나 선택 모델이 현재
  실행 주체에게 허용되지 않으면 Judge를 호출한다. 별도 `hybrid` 상태값은 두지 않는다.
- 로컬 confidence는 최근 Judge 일치율, 현재 입력과 학습 표본의 거리, 예측 경계 여유,
  최근 계약 통과율을 함께 반영한다. 처음 보거나 모호한 요청은 Judge로 되돌린다.
- 검증 버전을 사용하는 특정 배포에서 완료된 운영 표본이 20건 이상 쌓인 뒤 실행 성공률,
  schema 통과율 또는 downstream 성공률이 95% 미만이 되거나 fallback 비율이 5%를 초과하면,
  학습기와 불변 버전은 보존하되 해당 배포 정책의 활성 버전 연결만 해제해 Judge 우선으로
  되돌린다.
- Judge 호출 실패·형식 오류는 workflow를 실패시키지 않으며 사용자가 지정한 기본 모델, 그 뒤
  기본 대체 모델 순서로 실행한다.
- 실행 trace에는 `decision_source`(`runtime_judge`, `local_router`, `stored_model`), Judge
  호출 여부와 비용, 선택 모델, 대체 모델, 정책 버전, `learner_id/version`,
  `included_in_routing_learning`, 안전한 선택 근거를 남긴다.

#### 배포 후 재평가

- Test Sidebar 실행은 작업 지문이 같은 검증 학습 버전이 있으면 로컬 라우터를 사용하고,
  버전이 없거나 확신이 낮으면 Judge를 사용한다. Judge usage는 해당 테스트 run에만 기록하며,
  학습 label·학습 횟수·운영 성적·정책 갱신 카운터는 변경하지 않는다.
- 성공한 배포 후 운영 실행만 노드별 모델·입력 길이 profile별 성적에 반영한다.
- 설정한 점검 주기에 도달하면 완료된 Judge 표본과 운영 성적을 다시 평가한다.
  최소 표본 수, 최소 두 개 이상의 선택 모델, schema/downstream 성공률, fallback 비율 기준을
  충족하면 `local_first`로 전환한다. 기준을 잃으면 다시 `judge_first`로 돌아간다.
- 재평가는 추가 Planner/Judge 호출 없이 이미 누적된 Judge label과 완료된 운영 결과를 읽는다.

#### 제외 범위

- 입력군, 대표 예문, semantic cohort, embedding 유사도, cohort별 lifecycle과
  cohort별 Replay/Judge gate는 제품 경로와 저장소에서 제거한다.
- 과거 전략 정책은 신규 runtime에서 실행하지 않는다. 구형 정책 소유 학습 label과 JSON
  artifact는 migration에서 폐기하고, 새 학습기에서 0부터 시작한다. 기존 실행 로그와 비용·성능
  데이터는 유지한다.
### FR-012. LLM 파라미터 추천 룰셋

Cost Optimizer는 모델 교체뿐 아니라 LLM 노드의 파라미터 조정 후보도 추천한다.

현재 구현은 `CostOptimizerParameterRecommendationService`와 Gateway의 `GET /cost-optimizer/parameter-recommendations`, `PATCH /cost-optimizer/apply-recommendations`를 통해 일부 추천을 제공한다. 운영 로그가 부족하면 모델 라우팅 enable/refresh 계열 추천만 반환할 수 있고, 충분한 운영 sample이 있으면 `max_tokens`, `temperature`, `top_p`, `frequency_penalty`, RAG context 관련 추천을 만든다.

파라미터 추천의 기본 원칙은 `룰셋 + 운영 로그 통계`다. LLM이 직접 추천 결정을 내리지 않는다. LLM은 프롬프트 축소 후보 생성, 변경안 설명 문장 생성, 샘플 품질 judge 같은 보조 역할로만 사용할 수 있다.

추천 대상은 다음 범위로 제한한다.

| 추천 대상 | 추천 근거 | 1차 추천 방식 | 적용 방식 |
| --- | --- | --- | --- |
| `max_tokens` | 최근 `completion_tokens` p95/p99, 응답 잘림 여부, schema/downstream 성공률 | 통계 기반 룰셋 | A/B 후보 생성 후 적용 |
| `temperature` | 출력 형식, JSON schema 사용 여부, schema 실패율, retry/fallback 추세 | 작업 성격 기반 룰셋 | A/B 후보 생성 후 적용 |
| `top_p` | provider 지원 여부, `temperature`와의 조합, 현재 값이 극단값인지 여부 | 보수적 룰셋 | A/B 후보 생성 후 적용 |
| `frequency_penalty` | 출력 반복 패턴, 동일 문장/토큰 반복률, 사용자-facing 답변 품질 이슈 | 로그/출력 패턴 기반 룰셋 | A/B 후보 생성 후 적용 |
| RAG context 사용량 | `prompt_tokens` 중 retrieval context 비중, `context_token_estimate`, `retrieved_chunk_count`, evidence 충분성 | trace summary 기반 룰셋 | A/B 후보 생성 후 적용 |

다음 항목은 1차 자동 추천에서 제외한다.

- `presence_penalty`: 비용 절감과 직접 연결되는 근거가 약하다.
- `stop`: 출력 패턴 분석과 provider별 finish reason 수집이 더 필요하다.
- 프롬프트 축소 자동 적용: 품질 저하 위험이 커서 LLM이 후보를 만들더라도 반드시 A/B 실험을 거쳐야 한다.

`max_tokens` 추천은 다음 조건을 만족할 때만 생성한다.

- target LLM node의 배포 후 운영 성공 sample이 충분하다.
- 최근 sample의 `completion_tokens` p95가 현재 `max_tokens`보다 충분히 낮다.
- schema 실패율과 downstream 실패율이 허용 기준 이하이다.
- 응답이 길이 제한 때문에 잘린 근거가 없다.

추천값은 `completion_tokens` p95 또는 p99에 안전 여유를 더해 계산한다. 예를 들어 현재 `max_tokens=4096`, 최근 p95가 820이고 길이 잘림이 없다면 `1200~1500` 범위를 추천할 수 있다.

일반 실행은 `finish_reason`, JSON schema 결과, fallback 사용 여부, output 반복률을 `workflow_node_runs.trace_metadata.llm`의 safe summary로 남긴다. `finish_reason` 또는 schema signal이 누락된 표본은 max_tokens 하향 추천의 신뢰도를 낮추거나 추천 자체를 막는다.

파라미터 추천은 현재 draft와 활성 deployment snapshot의 target node 설정 fingerprint가 같은 경우에만 운영 표본을 사용한다. 서로 다른 deployment/version 또는 현재 draft 설정을 섞지 않으며, terminal 실패 run은 schema/downstream/RAG 품질 실패율에 포함한다. 비용·token p95는 성공 usage가 있는 표본에서만 계산한다.

`temperature` 추천은 다음 정책을 따른다.

- JSON 출력, schema 필수, 분류, 추출, routing 판단처럼 일관성이 중요한 노드는 낮은 값을 추천한다.
- `temperature > 0.3`이고 schema 실패 또는 출력 변동성 문제가 있으면 `0.1~0.3` 범위를 추천한다.
- 사용자-facing 답변이나 창의적 생성 노드는 낮추더라도 품질 영향이 있을 수 있으므로 반드시 A/B 후보로만 제안한다.
- `temperature` 추천은 직접 비용 절감보다 실패, 재시도, fallback 비용 감소를 목표로 한다.

`top_p` 추천은 provider/model 호환성과 조합 안정성을 우선한다.

- Anthropic 계열처럼 현재 UI/실행 경로에서 `top_p` 동시 사용을 제한하는 모델과 OpenAI GPT-5/o Responses 계열처럼 중앙 runtime 보정이 `top_p`를 제외하는 모델은 새 값 추천 대상에서 제외하거나 제거 후보로만 표시한다.
- 중앙 runtime 보정은 저장된 node parameter와 호출자가 전달한 message/parameter object를 바꾸지 않고 별도 effective request를 만든다. GPT-5/o Responses 요청에서는 지원하지 않는 `top_p`, `presence_penalty`, `frequency_penalty`, `stop`을 제거하며, `response_format`을 `text.format`으로 합칠 때도 기존 nested `text` object를 변경하지 않는다.
- `temperature`와 `top_p`가 동시에 극단값이면 한쪽만 조정하도록 추천한다.
- 단독 비용 절감 근거가 약하므로 `temperature` 안정화 추천의 보조 항목으로 다룬다.

`frequency_penalty` 추천은 반복 출력이 확인되는 경우에만 생성한다.

- 최근 성공 output에서 동일 문장 반복, 같은 bullet 반복, 동일 n-gram 반복률이 높다.
- 반복 때문에 completion token이 증가하고 있다.
- JSON/schema 노드에는 기본적으로 추천하지 않는다.

RAG context 추천은 다음 데이터를 사용한다.

- `LLMUsageLog.prompt_tokens`
- `workflow_node_runs.trace_metadata` 또는 RAG trace payload의 `context_token_estimate`
- `retrieved_chunk_count`
- `evidence_sufficient`
- `answer_grounding` 또는 downstream 성공 여부

RAG context가 prompt token의 대부분을 차지하고, evidence 충분성이 유지되며, 실제 검색 chunk가 과도하게 많으면 `topK`, `retrievedContextMaxChars`, `retrievedContextCompression` 조정을 추천한다. 이때 author가 작성한 system/user/assistant prompt는 임의로 자르지 않는다. 제한 대상은 Knowledge/RAG context뿐이다.

추천 결과는 다음 판단 정보를 가져야 한다.

| Field | 의미 |
| --- | --- |
| `recommendation_type` | `llm_parameter` |
| `parameter_key` | 추천 대상 파라미터 또는 RAG 옵션 |
| `current_value` | 현재 LLM 노드 설정값 |
| `suggested_value` | 추천 후보값 |
| `confidence` | `high`, `medium`, `low` |
| `risk` | `low`, `medium`, `high` |
| `reason` | 사용자에게 보여줄 근거 |
| `evidence` | sample 수, p95 token, 실패율, context token 비중 같은 safe summary |
| `apply_mode` | `experiment_required` 또는 `direct_policy_update` |

파라미터 추천의 기본 `apply_mode`는 `experiment_required`다. 추천 모달에서 `바로 적용`을 누르더라도 `max_tokens`, `temperature`, RAG context 변경은 직접 draft를 수정하지 않고 Cost Optimizer B candidate를 생성해 A/B 비교 화면으로 넘긴다.

자동 모델 라우팅 정책 갱신처럼 운영 정책만 바꾸는 항목은 후속 구현에서 `direct_policy_update`를 허용할 수 있다. 그러나 현재 LLM node의 prompt, parameter, Knowledge/RAG 설정값을 바꾸는 추천은 A/B 비교와 사용자 확인 없이 적용하지 않는다.

### FR-013. Cost Optimizer 후보 검증 및 출력 품질 평가

추천 모달의 `테스트하기`는 더 이상 Cost Optimizer workspace로 즉시 이동하지 않는다. 사용자가 선택한 추천 설정을 현재 LLM node 설정 복사본에 적용한 B candidate를 만들고, 최신 비교 가능한 성공 실행을 A baseline으로 자동 선택해 모달 안에서 B를 한 번 실행한다.

일반 `비교 분석 테스트`에서 사용자가 baseline을 직접 선택해 B candidate를 실행하는 경로도 같은 출력 품질 평가 계약을 사용한다. B candidate 실행이 끝나면 동일 입력의 A/B 출력을 blind pairwise judge로 평가하고, 결과 분석 화면의 `핵심 지표 비교`에 `출력 품질 점수` 행을 추가한다. 점수와 confidence는 compare 응답과 experiment/candidate 이력에 함께 저장해, 방금 실행한 후보와 이전 실험을 같은 기준으로 다시 확인할 수 있어야 한다.

빠른 검증 baseline은 요청 시점의 target LLM node 실행 중 다음 조건을 모두 만족하는 가장 최근 실행이다.

- 성공한 `workflow_node_runs`다.
- input, output, usage를 복원할 수 있다.
- Cost Optimizer candidate 실행이 아니다.
- 현재 draft와 node 설정 fingerprint가 같은 활성 deployment의 exact `deployment_id`를 가진 운영 실행이다. 실행 로그의 `process_data`는 보안 마스킹될 수 있으므로 baseline cohort를 다시 판정하는 원천으로 사용하지 않는다.

baseline을 찾지 못하면 LLM 호출을 시작하지 않고 `비교 가능한 최신 성공 기록이 없습니다.`를 표시한다. 빠른 검증이 시작된 뒤에는 exact `baseline_node_run_id`를 결과에 고정하며, 실행 도중 더 최신 로그가 생겨도 baseline을 바꾸지 않는다.

빠른 검증은 A를 다시 실행하지 않고, A의 복원된 입력을 B candidate에 고정해 B만 새로 실행한다. 선택한 추천 row 또는 target node draft가 바뀌면 기존 결과를 `stale`로 표시하고 적용을 막으며, 다시 테스트해야 한다.

모달은 각 지표를 서로 다른 단위의 독립된 A/B 막대그래프로 표시한다.

| 지표 | A baseline | B candidate | 변화 표시 |
| --- | --- | --- | --- |
| 비용 | 과거 baseline LLM 비용 | 이번 candidate LLM 비용 | 금액 차이와 절감률 |
| 실행 시간 | baseline node latency | candidate node latency | ms/s 차이와 증감률 |
| 입력/출력/전체 token | baseline usage | candidate usage | token 수와 증감률 |
| 출력 품질 점수 | 같은 rubric으로 평가한 baseline 점수 | 같은 rubric으로 평가한 candidate 점수 | 0~100 점수 차이와 confidence |

비용, latency, token, 품질 점수는 단위와 값 범위가 다르므로 하나의 공통 축에 섞지 않는다. 막대 길이는 각 metric 카드 안에서 A/B 상대 비교에만 사용하고 실제 숫자를 항상 함께 표시한다.

`이번 테스트에서 새로 발생한 비용`은 다음 항목을 분리해 보여준다.

- B candidate 실행 LLM 비용
- 출력 품질 평가 judge 비용
- 두 비용의 합계

A baseline 비용은 과거 실행에서 이미 발생한 참고 비용이므로 이번 테스트 신규 비용 합계에 다시 더하지 않는다. candidate 또는 judge 가격 정보를 계산할 수 없으면 `계산 불가`를 표시하고 임의의 0원으로 보이지 않게 한다.

출력 품질 평가는 Cost Optimizer 전용 LLM judge 기능으로 새로 구현한다.

- baseline output을 정답으로 간주하지 않는다.
- baseline과 candidate를 같은 input, target node prompt 목적, output contract에서 독립적으로 평가한다.
- judge에는 A/B 순서를 무작위로 가린 pairwise payload를 전달해 위치 편향을 줄인다.
- `instruction_fulfillment`, `relevance_completeness`, `clarity_consistency`, RAG 사용 시 `groundedness`를 평가해 0~100 점수와 confidence를 반환한다.
- Judge에 전달하는 `authoritative_evidence_available`은 redaction-safe RAG summary가 `evidence_sufficient=true`이고 `retrieved_chunk_count`가 양의 정수인 경우에만 true다. 빈 summary, 0건, 불충분, boolean/string/소수 또는 그 밖의 malformed count는 fail-closed로 false다.
- JSON schema 통과 여부와 downstream 호환성은 semantic 품질 점수에 섞지 않고 별도 deterministic gate로 표시한다.
- judge가 실패하거나 실행 가능한 credential/model이 없으면 품질 점수만 `평가 불가`로 표시하고 비용·속도·schema·downstream 결과는 유지한다.
- 품질 점수는 추천 근거이며 단독 hard block으로 사용하지 않는다. 낮은 점수 또는 낮은 confidence에서는 적용 전 경고와 명시적 확인을 요구한다.

일반 compare 경로의 품질 judge 호출은 B candidate 실행과 같은 비교 결과에 귀속한다. candidate 실행이 실패하면 judge를 호출하지 않고 `unavailable` 품질 평가를 저장한다. candidate 실행이 성공했지만 judge가 실패해도 compare HTTP 응답은 성공한 candidate 실행 결과를 유지하며, 품질 평가 상태와 safe summary만 `unavailable`로 반환한다.

judge provider 호출이 완료됐지만 응답 JSON 파싱, 필수 dimension 또는 confidence 검증에 실패한 경우에도 실제 발생한 judge usage와 비용은 기록한다. 이 경우 점수는 임의로 보정하지 않고 `unavailable`로 반환하며, 계약에 없는 추가 dimension은 총점 계산에서 제외한다.

출력 schema 검증은 candidate의 `output_format.type=json`일 때만 수행한다.

- JSON schema가 있으면 `passed` 또는 `failed`와 누락 field/type mismatch를 safe summary로 보여준다.
- JSON 출력이지만 schema가 없으면 `not_configured`로 표시한다.
- text 출력이면 `not_applicable`로 표시하고 schema 실패처럼 보이지 않게 한다.

downstream 호환성은 기존 FR-007 contract validator를 재사용한다. `compatible`, `warning`, `incompatible`, `unknown` 상태와 검사한 직접 소비 노드를 표시한다. `incompatible`은 적용을 막고, `warning`은 사용자 확인 후 적용할 수 있다.

빠른 검증 결과는 기존 Cost Optimizer experiment/candidate 이력으로 저장한다. 모달의 `상세 비교 분석하기`는 같은 `comparison_id`와 `candidate_id`를 기존 결과 분석 workspace에 전달하며 B를 다시 실행하거나 비용을 중복 발생시키지 않는다.

결과 분석 workspace는 전달받은 experiment/candidate를 목록 pagination이나 현재 이력 필터에서 검색하지 않고 단건 safe summary API로 복원한다. 선택한 후보가 오래됐거나 실패 상태여도 URL이 유효하고 권한 범위 안이면 같은 결과를 유지해야 한다. workflow, target node 또는 deep link 식별자가 바뀌면 이전 화면 세션의 baseline과 compare result를 초기화한다.

검증 완료 후 모달 하단에는 다음 액션을 제공한다.

| 버튼 | 동작 |
| --- | --- |
| `적용하기` | 성공한 exact candidate settings를 현재 target LLM node draft에 적용한다. schema failed 또는 downstream incompatible이면 비활성화한다. |
| `상세 비교 분석하기` | 같은 experiment/candidate를 기존 Cost Optimizer 결과 분석 화면에서 연다. |
| `닫기` | 결과를 이력에 남기고 모달만 닫는다. draft는 바꾸지 않는다. |

모달 본문은 결과가 길어지면 내부 세로 스크롤을 제공하고, 하단 버튼 영역은 항상 접근할 수 있도록 고정한다. loading 중 중복 실행을 막고, 비용이 발생하는 실제 LLM 호출임을 실행 전에 안내한다.

### FR-014. 배포별 자동 파라미터 최적화

배포된 workflow의 운영 로그 자동 수집 설정은 배포 생성 흐름과 분리해 관리할 수 있어야 한다.

- 배포 모달은 별도의 `운영 비용 자동 최적화` 단계를 표시하지 않고, 자동 최적화를 비활성화한 상태로 배포한다.
- 배포 후 워크플로우 운영 현황의 `자동 최적화 설정`에서 자동 수집 사용 여부, 자동 점검 주기(`20~200회`, 기본 `50회`), 월간 검증 예산(`$0.5~$10`, 기본 `$3`)을 정한다. 대상 LLM 노드는 배포 snapshot에 포함된 전체 LLM node다.
- 수집 대상은 배포 후 `api`, `webhook`, `scheduler`, `app` 실행에서 성공한 LLM node run이다. Test Sidebar와 수동 편집 테스트 실행은 포함하지 않는다.
- 여러 LLM 노드를 선택하면 각 노드의 수집 수가 모두 점검 주기에 도달했을 때만 `점검 준비 완료`가 된다. 어느 한 노드의 운영 표본이 부족하면 계속 수집 상태다.
- 자동 최적화가 수집하는 후보는 응답 길이(`max_tokens`)와 RAG context 설정이다. 모델 선택, 자동 모델 라우팅, fallback 모델, 작성자 prompt는 이 기능이 변경하지 않는다.
- 운영 현황의 비용·예산 사용률·비용 위험 신호는 기존 비용 관측 기능이다. 자동 최적화의 수집 횟수와 월간 검증 예산은 별도 컬럼과 별도 상태로 표시한다.
- 현재 단계에서 주기 도달은 추천/검증을 실행할 수 있는 조건을 뜻한다. 후보 LLM 재실행은 기존 Cost Optimizer의 명시적 `테스트하기` 흐름으로만 발생하며, 수집 자체는 비용을 발생시키지 않는다. 자동 최적화가 켜진 배포의 대상 LLM node를 기준으로 검증하면 후보 실행 비용과 품질 judge 비용만 월간 검증 사용액에 누적한다. 기준 로그 조회와 평소 배포 운영 실행 비용은 이 사용액에 포함하지 않는다.
- 월간 사용액이 한도에 도달한 배포는 새 추천 검증을 시작하지 못한다. 이미 시작한 검증의 실제 비용은 실행 완료 뒤에 기록되므로, 마지막 검증 한 건으로 한도를 조금 넘을 수는 있다.

자동 최적화 상태는 다음과 같다.

| 상태 | 의미 |
| --- | --- |
| `미사용` | 해당 배포에서 자동 최적화 수집을 켜지 않았다. |
| `수집 중` | 대상 LLM 노드의 성공 운영 로그가 점검 주기보다 적다. |
| `점검 준비 완료` | 대상 노드마다 필요한 운영 로그가 모였다. 추천 후보를 검토/검증할 수 있다. |
| `월 예산 도달` | 실제 검증 비용 누적이 월 한도에 도달해 새 검증을 시작할 수 없다. |
| `일시 중지`/`점검 실패` | 운영자가 중지했거나 안전한 점검 상태를 만들 수 없었다. |

### FR-015. 제약·난이도 라우터 실험 경계

FR-015는 production policy나 runtime dispatch를 바꾸지 않는 실험 전용 요구사항이다. 구조 제약,
실행 주체의 후보 사용 가능성, model evidence와 비용을 동일한 fixed fixture/result matrix에서
비교하고 adoption criterion의 통과·실패·미검증 상태를 산출한다.

- 실험은 active policy, learner, cache와 운영 DB를 변경하지 않는다.
- Provider 결과와 RAG retrieval은 동일 입력·모델 기준으로 재사용해 전략별 중복 호출을 피한다.
- 실패·품질 미평가 표본을 분모에서 제거하지 않고 별도 상태로 기록한다.
- Fixed fixture와 수동 prior는 구조 검증 근거일 뿐 production 품질 증거가 아니다.
- Report가 source SHA, dataset, catalog, pricing, selector와 evaluator version을 고정하지 못하면
  activation evidence로 사용하지 않는다.

### FR-016. Capability Routing V2 목표 계약

FR-016은 아직 production 완료가 아닌 Target 요구사항이다. Strategy ID는
`capability_routing_v2`이며 V1 policy, cache와 learner를 자동 재해석하지 않는다. 명시적으로 versioned된
V1은 ADR-0059의 legacy Selection Judge 의미를 유지한다. Contract version 없는 기존 row는
`legacy_ambiguous`로 취급해 dynamic routing을 중단하고 current-valid stored safe model 또는 외부 호출 전
typed failure로 닫은 뒤, 재배포/reissue로만 versioned V1/V2에 들어간다.

1. Requirement Judge는 `task_complexity`, `decision_impact`, `evidence_synthesis`, confidence와
   allowlist reason만 반환하고 모델 ID, 후보, 가격, credential과 provider private state를 다루지
   않는다. `judge_contract_version`과 `rubric_version`은 모델 응답이 아니라 server-owned immutable
   invocation envelope에 고정하고, 모델이 echo하거나 다른 값을 주장하면 unknown field contract
   failure로 처리한다. RAG query embedding은 ADR-0071의 별도 purpose/policy/capability로 먼저 처리하고, 권한 있는
   retrieval의 bounded safe facts만 Judge에 전달한다. V2 candidate policy로 query embedding 권한을
   대신하거나 query embedding 실패를 stored model로 우회하지 않는다.
2. Server selector는 현재 실행의 데이터 접근 범위와 organization scope,
    ADR-0064 deployment policy에서 server-derived한 credential principal, exact-model credential/provider
    lifecycle, current global workflow model execution eligibility, context/output/effect contract와 minimum
    quality hard gate를 먼저 적용한다. 저장 graph/policy가 날짜 고정 ID 또는 전역 실행 제외 모델을
    참조해도 실행 적격성 gate를 우회하지 못한다. Public·schedule·system
    actor를 credential principal로 승격하지 않는다. 인증 interactive 실행의 데이터 범위는 current
    execution subject이고, subject가 없는 public·webhook·schedule·API·system 실행은 ADR-0018의
    Anonymous Public Audience로 낮춰 public visibility/exposure를 통과한 Knowledge만 사용한다. Owner,
    actor, `user_id`와 credential principal을 private data subject로 합성하지 않는다. 통과한 후보만
    전체 예상 비용, 지연과 품질 제약으로
   비교하고 canonical order로 동률을 해소한다. Requirement Judge 호출도 별도 exact Judge model policy,
   조직 data egress와 capability-bound budget admission을 먼저 통과해야 한다. ADR-0064의 현재 node별
   단일 exact-model policy는 다중 후보 권한이 아니므로 Judge와 각 primary/fallback 후보를 승인하는
   별도 Accepted credential policy가 구현되기 전에는 capability-required V2를 effective strategy로
   사용하지 않는다. Judge, primary와 fallback은 같은 Billing Principal 아래 별도 attempt로 예산을
   승인하고 사용량을 종결한다.
3. Canonical task intent는 `unspecified | classify | extract | transform | generate`다. Legacy 누락값은
   `unspecified`이며 Client/prompt hint가 server-derived 안전 하한을 낮출 수 없다. Legacy
   `summarize`는 `transform`, `reason`과 allowlist 밖 자유 문자열은 `unspecified`로 정규화한다.
4. 동적 라우팅 예상 이익이 Judge·retry·fallback 비용과 risk margin을 넘지 못하면 Judge를 호출하지
   않고 current-valid stored safe path를 사용한다. 가격이 없거나 단위가 비교 불가능한 후보를 최저
   비용으로 간주하지 않는다.
5. 미검증 모델은 low-risk, reversible, internal 요청의 bounded canary에서만 탐색한다. Human-facing,
   외부 부수효과와 high-risk 요청은 proven safe baseline을 사용한다.
6. Learner candidate 최소 자격은 새 근거가 승인되기 전 `50 labels / recent 20 / exact 80%`, 축별
   평균 오차 0.5 이하와 계약 통과율 95%를 유지한다. 모든 terminal `rejected` label은 평가 분모와
   계약 통과율에는 한 번 포함하지만 classifier weight와 accepted training count에는 포함하지 않는다.
   Judge 계약 실패, work-model fallback 확정, execution/schema/downstream 실패와 outcome-unknown도
   rejected outcome이면 같은 규칙을 적용한다. `not_applicable` 중립 상태는 rejected로 합성하지 않는다.
   모델 분포, 운영 schema/downstream 성공률과 fallback은 learner 자격이 아니라 production activation
   gate에서 검증한다. Tenant 실행에서 얻은
    operational model evidence는 승인된 익명 집계 contract가 없으면 organization, workflow,
    canonical node location, task semantic fingerprint contract version, task semantic fingerprint,
    `requirement_evidence_cohort_contract_version`/`requirement_evidence_cohort_id`, model ID와 evidence
    contract version이 모두 같을 때만 재사용한다. Cohort는 server가 canonical task intent, bounded requirement 세 축과
    output/effect/context risk class에서 파생한다. 별도 Accepted transfer policy가 없으면 다른 cohort의
    성공 표본을 current minimum-quality 근거로 합치지 않는다. Prompt·RAG·output/schema·downstream 의미
    변경으로 fingerprint가 회전하거나 cohort/model/evidence contract가 다르면 같은 node의 이전 evidence도
    current minimum-quality 근거로 사용하지 않는다. 개별 표본은 ADR-0069의 terminal
    `provider_usage_operation_id`와 evidence contract version에 결합한
    `record_operational_model_evidence` command로만 append한다. Trusted evidence system이 immutable
    operation과 workflow/node outcome에서 scope, invocation/Loop iteration, model/purpose와 task
    fingerprint를 파생하며 Client/task payload 주장을 신뢰하지 않는다. Event, idempotency receipt와
    aggregate delta는 unique identity와 aggregate lock/CAS 아래 같은 transaction에 commit한다. 같은
    operation·contract·safe metric digest 재전달은 기존 receipt를 반환하고 표본 수를 늘리지 않으며,
    다른 digest는 conflict와 zero-write다. 최초 sample은 current ADR-0069 usage revision을 고정한다.
    ADR-0069 correction은 operation을 먼저 잠근 뒤 해당 operation의 모든 evidence-contract sample과
    affected aggregate validity row를 canonical order로 열거한다. Sample별 deterministic correction
    intent, 각 aggregate의 증가한 `operational_evidence_validity_epoch`과 pending correction set을 같은
    Unit of Work에 commit하고 기존 sample identity/digest를 덮어쓰거나 두 번째 표본으로 집계하지 않는다.
    Sample이 아직 없으면 뒤 최초 append가 corrected usage revision을 읽고, append가 먼저 commit했으면
    correction이 그 sample을 pending으로 만든다. 일부 contract/aggregate만 current로 남길 수 없다.
    Trusted rebuild는 current usage snapshot에서 sample count를 유지한 새 aggregate revision을 expected
    validity epoch와 pending-set digest CAS로 만들고 pending을 원자 해제한다.
    `model_evidence_version`은 aggregate revision, validity epoch와 sample usage revision set digest를
    결합한다. Selector가 operational evidence를 사용하면 server가 exact aggregate validity row
    identity/revision set을 canonical order로 해소해 `model_evidence_fence_set_digest`를 만들고
    selector/cache/activation command와 각 ProviderExecutionCapability에 결합한다. Caller/task payload는 row
    set을 주장할 수 없다. Profile propose/holdout/canary start/promotion은 publication·commit 전에, provider
    start는 같은 validity rows를 잠근 뒤 capability-bound revision/digest를 다시 비교한다. Correction winner
    뒤 stale request/start는 zero-write 또는 provider I/O 전 current-valid non-V2/typed unavailable로
    fail-closed한다. Provider-start가 같은 fence에서 먼저 commit한 attempt만 완료할 수 있다. Pending, sample
    usage revision mismatch 또는 stale validity epoch aggregate는 positive quality·비용 근거에서 제외하고 이미
    고정된 evidence snapshot은 자동 갱신하지 않는다. Evidence contract가 허용한 work-model
    purpose의 production `deployed` 또는 등록된 limited-canary attempt만 표본으로 사용한다.
    Judge/query embedding/summary, 일반 test/preview, 격리 benchmark와 outcome-unknown을 positive quality
    표본으로 집계하지 않는다.
7. Learner는 rubric, task intent normalization contract와 canonical normalized task intent, feature,
   label/evaluation 의미와 canonical deployment snapshot의 immutable task semantic fingerprint를 포함한
   requirement contract에 묶인다. Fingerprint는 canonical intent와 normalization version, 고정
   prompt/instruction, variable mapping, output/schema, Knowledge/RAG configuration과 downstream structural
   contract를 포함하고 runtime 원문 값, credential material과 mutable catalog/pricing은 제외한다.
   Canonical intent 또는 작업 의미가 바뀌면 learner lineage를 회전한다. Cache는 selector,
   output/effect/context hard-gate
   policy, catalog/profile, pricing, activation profile ID/revision과 exact requirement source manifest
   digest까지 포함한 selection contract에
   묶인다. Catalog/profile/pricing 등 selection contract가 바뀌면 기존 accepted cache는 mandatory miss이고
   current server selector를 다시 실행한다. Stale model을 현재 gate로 재검증하는 것만으로 hit로
   복구하지 않으며 requirement contract가 같으면 learner는 유지할 수 있다. Cache HMAC key epoch 변경은
   cache miss로 처리하고 동시 fill은 한 immutable 결과로 수렴한다. Cache hit도
   current authorization과 hard gate를 다시 통과한다. Cache namespace는 organization,
   deployment/version, canonical node location과 activation profile/source binding을 포함하고
   tenant·deployment·node·profile/source 간 재사용을 금지한다. Requirement contract가 같아도
   approved learner A에서 B 또는 `judge_only`로 바뀌면 cache는 mandatory miss지만 learner lineage는
   불필요하게 회전하지 않는다.
8. Durable policy/learner identity는 canonical `(container_path, node_id)`를 사용하고 label/attempt는
   invocation과 Loop iteration identity를 추가한다.
9. 관측은 먼저 `requested_strategy_id`, `effective_strategy_id`와 bounded
   `strategy_resolution_reason`을 기록한다. `strategy_resolution_reason`은 요청한 V2가 non-V2 또는
   `null`로 해소된 경우와 격리 benchmark에도 적용한다. 아래 Requirement·selection·reuse source enum만
   effective strategy가 `capability_routing_v2`인 decision에 사용한다. `profile_superseded`는 신규
   실행의 predecessor 해소에만 사용하고 정상 cutover 전에 snapshot을 고정한 in-flight run에 중단
   reason으로 합성하지 않는다.

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
   ```

   Effective V2 decision의 canonical source enum은 다음과 같다.

   ```text
   requirement_source: runtime_judge | local_classifier | not_required | unavailable
   requirement_evaluation_scope: current_execution | accepted_decision_cache | none
   model_selection_source: server_capability_selector | stored_default | stored_fallback
   routing_reuse_source: none | accepted_decision_cache
   ```

   `requirement_source`는 effective requirement의 origin이며 Judge 호출 telemetry가 아니다. Accepted cache
   reuse에는 cached origin, `requirement_evaluation_scope=accepted_decision_cache`,
   `routing_reuse_source=accepted_decision_cache`와 Judge attempt count `0`을 함께 기록한다.
   `requirement_source=not_required`는 effective V2의 economic admission 생략만 뜻한다. V2 off,
   profile·requirement source·scope·credential policy·Worker 부적격은 strategy resolution으로 기록하고 V2 source enum을
   합성하지 않는다.

10. V2 activation profile은 `requirement_source_mode=judge_only | approved_learner`를 고정한다.
    `judge_only`는 exact Judge policy/rubric/contract를, `approved_learner`는 exact immutable learner
    version과 requirement contract digest를 고정한다. Approved learner가 Judge fallback을 허용할 때만
    exact Judge policy/rubric/contract도 함께 고정한다. 각 exact Judge/learner source는 immutable definition
    identity와 별도 monotonic `requirement_source_lifecycle_revision`/`active | revoked | retired` state를
    가지며 profile은 proposal 시점의 active revision을 server-side로 고정한다. Source management mutation은
    같은 lifecycle fence를 exclusive하게 증가시키고 terminal source를 in-place 재활성화하지 않는다.
    Candidate gate 통과나 새 learner version 발행은 active profile을 자동 변경하지 않으며, 다른 source
    mode/version은 새 profile revision, locked holdout, limited canary와 독립 production 승격을 요구한다.
    Profile-bound source를 사용할 수 없고 명시적 current fallback도 없으면 다른 learner/Judge로 대체하지 않고
    `activation_requirement_source_unavailable` resolution과 current-valid non-V2 path 또는 typed
    failure로 닫는다. Server는 exact source manifest의 branch kind와 source version에서 immutable
    `required_evidence_branch` set/digest를 파생한다. `learner_judge_fallback=disabled`이면 learner
    branch 하나, `exact_judge_policy`이면 learner와 exact Judge branch 모두가 필수다. Holdout과 requirement
    판정을 수행한 canary event는 actual branch ID/version별로 분리한다. Branch별 threshold와 전체
    threshold를 모두 통과해야 하며 누락·unknown/version mismatch·cross-branch 혼합은 production 승격을
    거부한다. Client/profile author/evidence producer는 required branch set이나 actual branch를 주장할 수
    없다. Economic admission의 `requirement_source=not_required`는 required branch가 아니며 server가 reserved
    cohort와 null branch/version을 파생한다. 이 표본은 branch gate를 충족시키지 않지만 전용 aggregate와
    비용·실패·지연·품질·high-risk 전체 분모에는 정확히 한 번 포함한다.
11. V2 activation profile은 immutable `rollout family` 안에서 `proposed -> limited_canary ->
    production_active` 순서로 진행한다. Family의 production activation domain은 immutable이며,
    `production_active` predecessor 최대 하나와 `limited_canary` successor 최대 하나만 둘 수 있다. Stable
    assignment가 successor canary에 일치하면 successor를 선택한다. Non-target은 canonical
    `production_route.owner_kind=profile | non_v2_policy | blocked_no_safe_path`를 해소한다. Profile owner는
    exact production profile/generation, non-V2 owner는 exact policy/revision을 고정하고 blocked owner는
    provider I/O 전 typed failure로 닫는다. 최초 rollout처럼 predecessor가 없을 때 canary start는 빈 route를
    profile에 고정된 exact current-valid non-V2 rollback policy owner로 원자 초기화하며, route 부재를
    successor나 임의 default로 보완하지 않는다. Family activation-domain reservation은 profile terminal
    state와 독립된 environment-lifetime overlap 경계이며 별도 Accepted migration/retire 계약 없이 해제하거나
    다른 family로 이전하지 않는다. Runtime role은 `canary_successor`와 `production_route`로 분리하고 exact
    owner kind/ID/revision/generation predicate로만 전이한다.
    Rollout family와 최초 assignment contract는 `platform routing operator`의 idempotent
    `rollout_family_create` command로만 만든다. Coordinator는 environment guard 아래 actor 권한, global kill,
    canonical immutable activation domain과 기존 family overlap을 다시 검증한다. Server는 CSPRNG로 최소
    128-bit non-secret opaque seed를 발급하고 family/domain/initial contract, revision 0의 assignment-registry row, receipt와
    canonical audit를 원자 commit한다. Same actor/request replay만 기존 family를 반환하고 request/overlap
    conflict는 zero-write다. Non-overlap family create는 순차 overlap 검증 후 각각 성공한다.

    기존 family에 algorithm/seed/unit/count가 다른 contract가 필요하면 같은 actor 권한의 idempotent
    `rollout_assignment_contract_issue` command를 사용한다. Request는 exact family/domain, expected
    family·assignment-registry revision과 server-allowed algorithm/unit/count만 받고 raw seed는 받지 않는다.
    Existing family row 다음 family 생성 때 만든 registry row를 잠그고 CAS해 새 immutable contract, 증가한 registry revision, receipt와 canonical audit를
    원자 commit하며 기존 profile/contract를 바꾸지 않는다. Missing registry row를 issue command가 임의 생성하지 않는다. Exact replay만 기존 contract를 반환하고
    Same command ID의 conflicting request는 command conflict, stale/concurrent revision은 state conflict이며 audit/receipt 실패를 포함해 전부 zero-write다.
    Stable assignment는 profile에 사전 등록한 server-issued immutable
    `canary_assignment_contract_version` reference와 target range를 사용한다. Profile 작성 request는 raw
    seed가 아니라 존재하는 family/contract reference만 제출할 수 있다. Server는 environment, family, activation domain, organization, workflow, immutable deployment ID,
    `canary_assignment_contract_version`과 server-held `canary_assignment_seed`를 contract-defined versioned
    canonical bytes로 인코딩하고 `uint64_be(SHA256(bytes)[0:8]) mod canary_bucket_count`로 계산한다. Profile
    revision, safety generation, Worker/process, retry, 시간과 runtime random은 입력에서
    제외한다. V1 bucket count는 10,000이고 target ranges는 `[0, 10000)` 안의 non-empty, 정렬·비중첩
    구간이며 전체를 선택할 수 없다. 같은 contract와 deployment는 Worker/retry/restart가 달라도 같은 bucket을 사용한다.
    Algorithm/seed/unit/count 변경은 위 issue command가 만든 새 immutable contract reference, 새 proposed
    profile과 독립 canary-start 승인을 요구하며 target range 변경은 기존 bucket을 재계산하지 않는다. Client/task payload는 assignment identity, seed와
    bucket을 주장할 수 없고 원 값은 API·trace·audit에 노출하지 않는다.
    Locked holdout은 profile 작성자·production 승인자와 분리된 `platform routing holdout operator`의
    idempotent `activation_holdout_evaluate` command로 시작하고 등록 evaluator/recovery
    dispatcher/finalizer workload만 실행·복구·종결한다. Canonical request, run과 terminal artifact는
    immutable holdout operator principal, profile revision, exact source manifest와 server-derived required
    branch set/digest, `purpose=activation_holdout` dataset ID/version/hash, evaluator·metric·threshold
    contract와 budget·credential·egress policy를 결합한다. Workload/finalizer가 operator principal을
    바꾸지 못한다.
    Admission은 immutable run intent, accepted receipt/audit와 required branch 각각의 deterministic pending
    dispatch intent를 한 transaction에 생성한다. 어느 branch intent 초기화라도 실패하면 전체 rollback하며
    branch ID/version canonical order가 evaluator stage/ordinal/attempt key를 정한다. Provider I/O는 전체
    branch manifest commit 뒤에만 허용한다.

    Recovery dispatcher는 각 pending/expired branch intent를 claim하고 최초·복구 attempt마다 run에 고정된
    holdout operator principal의 current 권한, evaluator workload 등록·purpose/source binding, exact
    profile/source/dataset/evaluator/metric contract와 credential·egress·budget lifecycle을 ADR-0069
    admission 전에 재검증한다. Revoke/stale이면 현재 intent를 `denied`, 미시작 다른 intent를
    `cancelled_due_to_denial`로 닫고 run을 `denial_pending`으로 만든다. 이미 시작된 attempt는
    재호출하지 않고 terminal usage를 정산하며 모든 intent가 terminal일 때만 denied artifact를 publish한다. Branch terminal transaction은
    다음 intent를 새로 만들지 않으므로 어느 branch 직후 crash해도 나머지 admission-time intent를
    recovery할 수 있다. Concurrent recovery는 branch별 attempt 하나로 수렴하고 `provider_started` 또는
    outcome-unknown attempt를 재호출하지 않는다.

    Terminal finalizer는 모든 required branch intent가 terminal일 때만 동일 operator 권한, evaluator
    등록/binding과 lifecycle을 다시 확인한다. Non-terminal branch가 있으면 publication zero-write로
    대기하고 crash를 `incomplete`로 합성하지 않는다. Provider start 뒤 회수됐으면 usage를 보존한 채
    artifact를 `denied`로 닫고, current인 경우에만 unique artifact identity 아래 required branch별·전체
    aggregate를 검증한다. 모든 branch와 전체 threshold가 통과해야 `valid`, terminal branch의
    version mismatch/coverage 부족은 `incomplete`, threshold 위반은 `invalid`, 권한·등록·lifecycle
    회수는 `denied`다. Artifact, 모든 dispatch/run terminal state, receipt와 audit를 한 transaction에
    publish한다. Exact replay는 기존 run/artifact와 같은 branch manifest를 반환하되 pending dispatch를
    취소하지 않고 conflicting command/artifact digest는
    `model_routing.activation_holdout_command_conflict`와 write/I/O zero-write다. Tuning/product
    benchmark는 holdout을 대신하지 못하며 canary start는 exact profile/source branch의 terminal
    `valid` artifact만 인정한다.

    Limited canary 시작은 locked holdout/manifest, current V2 credential policy, 실제 delivery를 소비한
    Worker의 code-owned 최소 runtime capability, stable canary scope와 rollback readiness를 검증한 뒤
    작성자와 다른 actor가 승인한다. 성공 transaction은 새 profile-bound safety generation,
    `canary_successor`, 최초 canonical `production_route`, immutable cadence/schedule revision과 sequence 0
    current·sequence 1 next evidence window, 두 window와 각 required branch·reserved `not_required`·overall empty aggregate,
    current/next window별 각 `activation_canary_runtime` source-instance의 unobserved initial watermark,
    generation `canary_usage_validity_epoch=0`/`canary_usage_correction_pending_count=0`, receipt와 audit를 원자
    commit한다. Schedule/watermark/usage-validity 초기화가 모두 commit된 뒤에만 stable canary scope에 capability를
    발급한다. 기존 route는 유지하고 최초 rollout의 빈 route만 exact current-valid non-V2 rollback policy
    owner로 초기화한다. Remediation start이면 기존 blocked production owner를 operator가 먼저 종료하고
    canonical route를 exact current-valid `non_v2_policy`로 전환했으며 canary role이 비어 있어야 한다.
    Canonical request의 expected domain generation/role/schedule revision이 guard lock 안의 current 값과
    일치할 때만 다음 generation을 한 번 할당한다.
    Evidence row, receipt 또는 audit 초기화 실패와 concurrent loser는 generation/state/role/route를 포함해
    전체 zero-write다. 각 threshold는 immutable 분류(`promotion_only` 또는 `hard_stop`)와 적용 cohort/overall scope를 가진다.
    `promotion_only`는 승격 gate에만 쓰고, `hard_stop`은 limited-canary, current production과 superseded pinned
    generation에서 terminal 전까지 유지한다. 같은 generation의 evidence schedule/source instances는
    current `production_active`와 `superseded` pinned attempt가 terminal할 때까지 safety-monitoring window를
    계속 회전한다. Promotion evidence set end sequence보다 큰 snapshot은 기존 set에 소급 편입하지 않는
    post-promotion monitoring snapshot이며 cutoff 이전 late evidence를 sealed late-correction hard-stop
    경계에서 처리한다. Production 승격은 profile에 고정한 `promotion_evidence_contract_version=1`의
    `promotion_evidence_start_sequence=0`, 최소 연속 window 수/기간과 pre-registered 최소 표본·실패 분모·
    confidence, 품질·전체 비용·p95 latency·fallback·high-risk guardrail을 그 limited-canary generation에서
    통과한 뒤 current revision에 독립 승인을 다시 받아야 한다.

    Canary evidence는 canary start transaction에서 등록한 immutable cadence/window schedule과 append-only
    event로 집계한다. 각 window는 반개구간 `[window_start_at, cutoff)`와 required branch 및 reserved `not_required` cohort별
    monotonic aggregate revision을 가진다. Active current는 이미 생성된 next window를 가리키고, 그 next는 seal rotation transaction에서
    새 tail을 생성해 연결하기 전까지 successor가 없을 수 있다. `platform routing evidence system`의
    `record_canary_evidence`만 manifest source contract에서 server-derived
    `authoritative_event_time`을 계산하고 aggregate lock 전에 assigned window를 결정한다.
    Client/provider timestamp는 authority가 아니다. Current cutoff 이상이고 materialized next interval
    안인 non-blocking event는 seal 전후 모두 next-window branch aggregate에만 append한다. Next cutoff
    이상인 non-blocking event는 `model_routing.canary_evidence_schedule_not_ready`와 zero-write 후 rotation
    뒤 재제출한다. Actor/purpose, source binding/identity/digest와 authoritative time이 유효한 blocking
    event는 schedule lag로 차단을 잃지 않는다. 포함 window가 없으면 nullable window와
    `schedule_disposition=unassigned_schedule_lag`를 가진 immutable event/receipt, exact generation
    block/epoch와 failure audit를 aggregate 없이 원자 commit한다. Invalid actor/source/time 요청은 이
    fail-safe 경로를 사용할 수 없다.

Canonical source-event dedupe identity는 environment/family/profile ID·revision/safety generation,
    source kind, manifest에 등록된 immutable opaque source instance ID와 그 instance namespace의 immutable
    event ID로 구성하고 unique constraint를 둔다. Canary window는 identity가 아니다. Usage-derived
    event에는 별도 `(environment, family, profile revision, safety generation, provider usage operation,
    metric contract, event kind)` canonical sample unique key를 둬 같은 terminal operation이 여러 source
    instance/event ID에서 관측돼도 sample count에 최대 한 번만 기여하게 한다. Server는 terminal attempt/
    execution snapshot에서 `evidence_cohort=required_branch | not_required`를 파생한다. Required branch이면
    actual branch ID/version, economic admission이면 reserved `not_required`와 null branch/version을 사용한다.
    Usage-derived metric event kind는 terminal ADR-0069 `provider_usage_operation_id`가 필수이며 server가
    execution snapshot에서 identity와 current usage revision을 파생한다. Operation ID는 source-event
    comparison digest와 canonical sample key에 포함하고 usage revision/contribution은 별도
    `canary_usage_projection`에 저장한다. Non-usage event의 operation과 sample key는 null이다.
    Authoritative event time, evidence cohort, nullable actual branch/version, metric contract, event kind,
    optional operation ID와 payload digest는 canonical source-event comparison digest를 이룬다.
    Usage-derived event는 ADR-0069 operation -> canonical usage sample key -> source-event identity 순서로
    잠가 terminal/current revision을 확인하고, non-usage는 source-event identity를 바로 조회한다. Existing
    row의 actor/source binding과 digest가 같으면 저장된 event/receipt와 최초 server-owned schedule
    disposition/assigned window를 반환하고 현재 schedule로 재배정하지 않는다. Digest mismatch나 caller의
    window/disposition 주장은 `model_routing.canary_evidence_conflict`와 zero-write다. 새 source identity가
    이미 다른 source에 고정된 usage sample key를 재사용하면
    `model_routing.canary_evidence_duplicate_operation`과 event/projection/aggregate/receipt/audit
    zero-write다. 두 identity가 모두 새일 때만 current schedule에서 assignment를 계산한다. Schedule
    disposition, nullable assigned window와 observed schedule revision은 identity 밖의 immutable processing
    outcome이다. 서로 다른 등록 instance의 같은 event ID는 non-usage 또는 서로 다른 usage sample key일
    때만 별도 event다. Assigned usage event는 current operation revision과 safe contribution digest를 가진
    별도 usage projection을 event/receipt와 함께 원자 생성하고 sample count를 한 번만 반영한다. 모든 assigned event는 server-derived cohort와 overall aggregate를
    canonical key 순서로 잠근다. Direct blocking kind이면 threshold와 무관하게
    aggregate 뒤 exact generation row를 잠가 event/receipt/revisions, deterministic direct breach,
    deny-only block/epoch와 failure audit를 원자 commit한다. 같은 append가 hard-stop도 넘더라도
    aggregate breach나 generic success audit를 중복 만들지 않는다.

    Non-blocking event만 post-append immutable `hard_stop` threshold를 판정한다. First crossing이면
    aggregate 뒤 exact generation row에서 deterministic aggregate breach와 block/audit를 원자 commit한다.
    `promotion_only`는 block을 만들지 않고 crossing 없는 append만 generic evidence success를 기록한다.
    Concurrent append/replay는 direct 또는 first-crossing 하나로 수렴하고 이미 blocked generation의 후속
    terminal evidence는 aggregate만 한 번 갱신한다. Reserved `not_required`는 overall hard-stop에 포함된다.
    Schedule-lag direct blocking event는 aggregate/snapshot에 재분류하지 않고 exact generation block을
    set한다. Sealed direct blocking event도 아래 reconciliation의 모든 지원 state branch에서 generation
    block을 포함한다. 일반 evidence actor는 lifecycle/scope를 활성화하지 못한다. Sealed snapshot의
    pre-cutoff late event는 별도 최소 권한 reconciliation actor/command만 처리한다.

    Activation source kind는 `activation_holdout`과 `activation_canary_runtime`으로 제한한다. 등록된
    holdout evaluator의 immutable `activation_holdout` artifact는 locked holdout branch만 충족하고 canary
    sample/watermark/block에는 포함하지 않는다. `record_canary_evidence`는 trusted profile-bound runtime monitor의 `activation_canary_runtime` event만
    허용한다. 이 source kind는 같은 generation의 `limited_canary`, current `production_active`와
    `superseded` pinned attempt terminal evidence를 포함한다. 새 run admission이 닫힌 뒤에도 pinned attempt,
    ADR-0069 operation, provider projection/receipt, source barrier와 당시 알려진 usage correction이 drain될
    때까지 evidence intake와 window rotation을 유지한다. Canary start는 server-owned
    `canary_generation_drain_summary` revision 0을 만들고 attempt/operation admission, terminal finalizer,
    source watermark/append와 usage correction은 non-terminal/pending count, terminal watermark, source
    coverage digest와 drain revision을 같은 transaction에서 갱신한다. Caller는 summary 값을 주장할 수 없다.
    Role release, expiry, disable, rollback 또는 safety block은 provider-start capability generation을 즉시
    invalid/blocked로 만들고 evidence lifecycle은 `draining`으로 둔다. Trusted `platform routing evidence
    lifecycle system`의 idempotent `close_canary_evidence_generation` command는 profile/generation/schedule,
    exact drain revision/digest, terminal attempt watermark/digest, source-barrier coverage digest와 current
    usage epoch/pending 0을 결합한다. Close는 profile/role -> schedule -> drain summary -> usage validity ->
    generation만 잠그고 개별 operation/projection/source receipt를 역순으로 열거하지 않는다. Summary count
    0과 final-cutoff coverage가 current일 때 final window를 새 tail 없이 봉인하고 schedule/generation
    closed, receipt와 `model_routing.canary_generation.closed` audit를 원자 commit한다. Missing/stale/incomplete
    summary 또는 drain 전은 `model_routing.canary_evidence_drain_pending`, stale/concurrent close는
    `model_routing.canary_generation_close_conflict`와 zero-write다. Operation-first finalizer/reconciler와
    close 사이 lock cycle은 0회이며 close 뒤 새 activation source event/window, admission과 capability는
    0회다. 이후 authoritative billing/usage correction은 schedule/generation을 다시 열지 않는 append-only
    projection과 drain revision으로 immutable final 근거만 재평가한다.
    `execution_mode=benchmark`의 격리 product benchmark는 별도 `product_benchmark` diagnostic namespace를
    사용하며 activation evidence write를 제출할 수 없다. 해당 시도는
    `model_routing.canary_evidence_source_ineligible`과 zero-write다. Learner, optimizer와 scheduler도
    activation evidence producer가 아니다.

    Authoritative source watermark는 exact source instance에 등록된 adapter workload의
    `advance_canary_source_watermark` command로만 전진한다. Canonical request는 profile/generation,
    target window ID, source kind/opaque instance/contract, monotonic barrier·position, observed-through,
    expected revision과 terminal append receipt/barrier proof를 결합한다. Eventless 구간도 authoritative
    scan이 target cutoff까지 완료된 경우에만 전진한다. Transaction은 immutable schedule과 target-window
    source watermark row를 잠그고 window/source-instance binding, monotonicity와 receipt coverage를
    재검증해 watermark/receipt/redacted audit를 원자 commit한다. Exact replay만 기존 watermark를 반환하며
    conflicting payload는 `model_routing.canary_watermark_command_conflict`, stale/regression/incomplete
    coverage 또는 seal된 old-window request는 `model_routing.canary_watermark_stale`과 zero-write다.

    모든 authoritative source watermark가 cutoff를 지난 뒤 `platform routing evidence system`의
    idempotent `seal_canary_evidence_window` command로만 snapshot을 만든다. Request는
    profile/generation, expected schedule revision, pre-registered current/next window ID·sequence와 immutable
    cutoff, expected aggregate revision map과 그 canonical digest, source별 expected watermark를 포함한다. Aggregate map의 key set은 current required branch set + reserved `not_required`와 정확히 같아야 한다. Request
    watermark는 CAS expectation일 뿐 authority가 아니며 tail identity/time은 server가 immutable cadence에서
    `tail_sequence=next_sequence+1`로 파생한다. Transaction은 schedule/pointer와 current/next window를 잠근 뒤 current evidence aggregate row를 reserved key를 포함한 canonical key 순서로,
    server-owned watermark를 고정 순서로 잠근 뒤 generation canary usage validity row를 잠그고
    assigned-window membership, required-branch + reserved `not_required` aggregate revision map과 canonical
    digest, expected usage epoch와 pending count 0을 다시 검증한다. 성공 시 current event를 required branch/version 또는 reserved `not_required` cohort별 sealed snapshot
    ID/revision/hash와 초기 `snapshot_invalidation_revision=0`에 포함하고, snapshot별 required branch·reserved `not_required`·overall sealed late-correction aggregate와 sealed
    usage-correction aggregate를 각각 revision 0으로 초기화한다. Late correction은 cutoff 이전 late event,
    usage correction은 이미 집계된 operation revision 변경을 sample count 보존 delta로 반영하며 둘 다
    immutable snapshot aggregate/hash와 분리된다. 새 tail window·required branch별·reserved `not_required`·overall empty aggregate와 모든 registered
    source-instance의 unobserved initial watermark를 만든다. Next-to-tail link,
    증가한 schedule revision, pointer-to-next, snapshot, receipt와 audit는 한 transaction에 commit한다.
    Tail/audit/receipt 초기화 실패는 전체 rollback한다. Same actor/request replay는 기존 snapshot과 exact
    tail identity를 반환하고 concurrent loser는 zero-write다. Cutoff 이전 append가 먼저 current revision을
    바꾸면 seal은 재제출되지만 cutoff 이후 append는 seal 순서와 무관하게 이미 등록된 next window만
    변경한다.

    Promotion은 profile의 `promotion_evidence_start_sequence=0`부터 request end sequence까지 모든 sealed
    window snapshot을 빠짐없이 ordered immutable `promotion_evidence_set`으로 묶는다. Canonical request와
    identity/hash는 promotion actor/command, exact terminal holdout artifact ID/revision과 immutable
    holdout operator principal, profile/generation, start/end sequence, member별 snapshot
    ID/revision/hash/invalidation revision/usage-correction revision, required branch/version별
    base+late+usage effective aggregate, reserved `not_required` effective aggregate, 전체 effective aggregate,
    evidence contract digest와 generation canary usage validity epoch를 포함한다. Transaction은 holdout
    artifact와 member를 canonical order로 잠그고 promotion actor가 profile author와 artifact-bound holdout
    operator principal 모두와 다른지, contiguous range, watermark, required branch별 gate와
    `not_required`를 포함한 전체 최소 sample/duration/failure/threshold, 모든 member의
    `snapshot_invalidation_revision=0`, current usage-correction revision과
    `canary_usage_correction_pending_count=0`을 재검증한다. 동일 principal 또는 stale artifact
    principal/revision은 `model_routing.activation_approval_required`와 전체 zero-write다. Non-zero revision은 caller가
최신 값을 제출해도 promotion에 영구적으로 ineligible하다. Missing/gap/duplicate/cherry-pick, branch
omission 또는 member mismatch는
    `canary_evidence_required | stale | canary_usage_correction_pending`과
    set/profile/route/receipt/audit zero-write다. 성공 set/member rows와
    production promotion은 한 transaction에 commit한다.

    Cutoff 이전 event가 seal 뒤 도착하면 일반 append는 event를 쓰지 않는다. Evidence intake가 immutable
schedule에서 target sealed snapshot을 server-side로 파생하고 최소 권한
`platform routing evidence reconciliation system`의 idempotent `reconcile_canary_late_evidence` command로
내부 전달한다. Terminal receipt commit 전에는 source에 success를 acknowledge하지 않고 crash 전 receipt가
없으면 동일 source identity/digest로 전체 command를 재시도한다. Request는 authoritative source
identity/comparison digest, exact profile/generation과 server-derived sealed snapshot ID/revision을 결합하고
    invalidation branch만 expected invalidation revision을 추가한다. Monitoring correction revisions는
    caller/request identity가 아니며 Coordinator가 row lock 안에서 current 값으로 증가시킨다. Promotion evidence set, promoted generation과 route
owner는 caller가 제출하지 않고 Coordinator가 profile/role lock 안에서 파생한다.

Profile이 `limited_canary`이고 snapshot이 아직 소비되지 않았으면 environment -> profile runtime-safety ->
    canary role -> snapshot 순서로 잠근다. Non-blocking late event는 `late_sealed` event, 증가한
    invalidation revision과 `pre_promotion_snapshot_invalidated` audit를 기록한다. Direct blocking late
    event는 snapshot -> generation 순서로 invalidation+block과
    `pre_promotion_snapshot_invalidated_generation_blocked` 단일 invalidation audit를 원자 기록한다.
    Snapshot aggregate/hash와 profile/role은 불변이며 non-zero invalidation revision은 영구적인 promotion
    부적격이다.

    Snapshot이 promotion evidence set member이고 current production owner이면 production role ->
    authoritative rollback policy -> exact set -> member -> generation 순서로 잠근다. Policy
    revision/eligibility가 current이면 exact rollback route, revoke/missing이면 `blocked_no_safe_path`를
    선택해 block+disable+route 전이를 원자 수행한다. 같은 member의 superseded owner이면 historical
    profile fence -> exact set -> member -> generation에서 historical block-only를 수행하고 current
    successor는 zero-write다.

    Set end sequence보다 큰 same-generation snapshot은 post-promotion monitoring snapshot이다. Current
    owner는 production role, historical owner는 historical profile fence 뒤 snapshot -> cohort/overall
    late-correction aggregates -> generation 순서로 잠근다. Snapshot/hash/invalidation은 바꾸지 않고
    sealed base+correction fold를 평가한다. Direct event는 threshold와 무관하게, non-blocking correction은
    hard-stop first crossing일 때만 exact current/historical generation block과 `model_routing.canary_breach.detected`를
    원자 commit한다. Crossing 없는 correction은 generic evidence success 하나만 기록하고
    profile/route/current successor는 zero-write다. Concurrent correction/replay는 하나로 수렴한다.

    미승격 terminal profile, set 범위 안인데 member가 아닌 snapshot, 다른 generation/snapshot 또는 지원
    state/snapshot relation 밖의 request는 `model_routing.canary_evidence_stale`과 전체 zero-write다.

    Promotion과 reconciliation은 같은 profile/role fence에서 직렬화한다. Reconciliation이 먼저면 promotion이
증가한 revision을 보고 실패하고, promotion이 먼저면 같은 command가 promoted branch를 수행한다. 따라서
별도 caller 재시도 사이에 invalid production이 노출되는 상태가 없다. Same identity/digest replay는 기존
outcome으로 수렴하고 digest mismatch는 `model_routing.canary_evidence_conflict`다. Transaction 일부나
receipt/audit 실패는 event/correction/invalidation/safety/lifecycle write를 모두 rollback한다.

    ADR-0069 authoritative usage correction은 operation lock 아래 같은 operation의 모든
    `canary_usage_projection`을 열거하고 deterministic correction intent와 증가한 generation
    `canary_usage_validity_epoch`을 원자 commit한다. Evidence generation이 closed여도 correction을 거부하지
    않고 닫힌 schedule/window/admission을 재개하지 않는 append-only projection만 만든다. Pending count는 intent 수가 아니라 authoritative
    revision보다 뒤처진 distinct projection 수다. Current -> pending에서만 증가하고 이미 pending인
    projection의 후속 correction은 active target만 전진시켜 중복 증가하지 않는다. Usage correction이 먼저면
    뒤 최초 append가 current revision으로 projection을 만들고, append가 먼저면 correction이 projection을
    pending으로 만든다. Event identity/digest와 sample count는 변경하지 않는다. Current -> pending
    전이와 generation drain summary의 distinct pending count/drain revision은 같은 transaction에 commit한다.
    Trusted `reconcile_canary_usage_correction`은 provider I/O 없이 operation/projection ->
    profile/role relation -> affected aggregate/correction -> generation drain summary -> usage validity 순서로
    잠근 뒤 latest target을 다시 읽어 old contribution을 한 번 retract하고 current contribution을 한 번
    add한다. 이전 intent는 `superseded`, latest는 `applied`로 terminal 처리하고 projection이 current가
    될 때 pending count를 usage row와 drain summary에서 한 번만 감소시켜 stale worker가 과거 contribution을
    복원하지 못하게 한다. Close는 개별 operation/projection을 잠그지 않으므로 profile/role부터 잠가도
    correction과 lock cycle을 만들지 않는다. Open aggregate는 corrected hard-stop first crossing에서 generation을 차단한다. 미승격 sealed snapshot은
    별도 usage-correction aggregate/revision을 갱신하고 corrected base+late+usage effective aggregate가
    hard-stop을 처음 넘으면 exact generation block/epoch와 canary breach audit를 같은 transaction에
    기록한다. 이미 blocked이면 중복 breach/audit를 만들지 않는다. Promotion은 base+late+usage effective
    gate를 사용한다. Promotion-set member의 corrected gate가 실패해도 immutable promotion set/member identity/hash/revision을
    갱신하지 않는다. `(set, member, provider operation, target usage revision, correction contract version)`
    identity의 append-only `promotion_evidence_reconciliation` row에 safe digest와 재평가 결과를 기록하고,
    current owner는 generation block+disable+current-valid rollback/`blocked_no_safe_path`, superseded owner는
    historical generation block-only를 원자 수행한다. Exact replay는 기존 reconciliation을 반환하고 같은
    identity의 다른 digest는 `model_routing.promotion_evidence_reconciliation_conflict`와 zero-write다. Set end 뒤 monitoring correction은 corrected hard-stop first
    crossing에서 exact current/historical generation만 차단한다. Closed correction은 immutable final snapshot
    또는 promotion/monitoring relation을 같은 state-aware branch로 재평가하고 필요한 historical block이나
    current-owner rollback/`blocked_no_safe_path`만 적용하며 capability를 재발급하지 않는다. Pending 동안 seal·promotion·새 provider start는
    `canary_usage_correction_pending`으로 fail-closed하고 current successor·immutable snapshot/sample count는
    보존한다. Breach/invalidation은 기존 canonical audit를 exactly-once 사용하고 crossing 없는 correction은
    ADR-0069 correction audit/receipt 외 generic evidence audit를 만들지 않는다.

Open assigned direct blocking event, unassigned schedule-lag direct event 또는 post-promotion monitoring
    correction의 direct kind는 threshold와 무관하게, non-blocking event는 open/late fold의 aggregate
    hard-stop first crossing에서 immutable breach event/receipt, exact affected generation block/epoch와
    `model_routing.canary_breach.detected` failure audit를 원자 기록한다. Profile
    state/runtime-safety revision, canary/production role, snapshot invalidation과 rollback route는
    zero-write다. 권한 있는 platform routing operator가 별도 emergency disable/rollback command로
    lifecycle을 종료한다. 유일한 자동 lifecycle 예외는 위 sealed-snapshot pre-cutoff reconciliation의
    current-production branch이며 exact current owner generation block과 disable+rollback/blocked route를 함께
    commit한다.
    Promotion은 expected profile/family/domain-role revision, evidence start/end sequence, ordered member
    snapshot ID/revision/hash/invalidation-revision/usage-correction-revision digest와 expected generation canary
    usage epoch뿐 아니라 commit 직전 operator 권한, DB time/profile state·revision, exact canary
    role/generation, source manifest와 terminal valid branch-partitioned holdout, credential policy, actual Worker
    readiness, global workflow model execution eligibility, exact current-valid rollback, global/scoped safety,
    member base+late+usage effective gate와 usage correction pending 0을 같은 lock order에서 다시 검증한다. 어느 gate든 precheck 뒤 바뀌면 successor/predecessor/production role/receipt/success audit를
    zero-write한다. 성공한 승격은 `production_route`를 successor profile/generation으로 전환하고
    `canary_successor` role을 비우며 successor를 `production_active`로 전이한다. Profile은 immutable
    definition revision, 신규 run 선택용 admission lifecycle revision과 in-flight attempt 안전용 runtime
    safety revision을 분리한다. Predecessor가 있을 때만 같은 transaction에서 admission state를
    `superseded`로 전이하고 admission revision만 증가시키며 predecessor의 runtime safety revision과
    generation은 유지한다. 최초 rollout은 successor state와 두 role 전이만 commit하고 존재하지 않는
    predecessor write/audit를 만들지 않는다. 이미 시작한 run은 기존 definition/runtime-safety snapshot을
    유지한다. Client나
    producer task payload의 revision 주장은 Worker 호환성 근거가 아니다. `disabled`와 `expired`는 runtime-safety terminal 상태이고 `superseded`는 신규 실행 admission
    terminal 상태다. 어느 상태도 다시 활성화하지 않고 변경은 새 `proposed` profile을 요구한다. 신규
    `deployed`와 일반 `test` 실행은 `proposed`, `disabled`, `expired`, `superseded`, 상태가 없거나
    stale한 profile에서 V2 Requirement Judge를 호출하지 않고 별도로 versioning된 current-valid non-V2
    policy, stored safe path 또는 외부 호출 전 typed failure로 닫는다. 다만 정상 promotion 전에 predecessor
    snapshot을 고정한 in-flight run은 `superseded` admission state만으로 중단하지 않는다. 별도 emergency
    disable/block/revoke가 없고 definition/runtime-safety revision과 generation이 current이면 이후 node
    attempt도 기존 snapshot으로 완료한다. Item 15의 격리 `benchmark`만 `proposed` profile의 명시적 예외다. Production scope
    변경은 별도 activation-domain migration이고, 별도 Accepted migration 계약 전 active overlap은 zero-write로
    거부한다. DB current time이 `valid_until`을 지나면 expiry scheduler가 늦더라도 resolver가 즉시 V2를
    차단한다. `platform routing lifecycle system` actor의 deterministic `activation_profile_expire`
    command만 `expired` 전이, receipt와 redacted audit를 원자 commit한다. Proposed expiry는 role/generation을
    바꾸지 않고 limited-canary expiry는 exact `canary_successor` role/generation만 종료한다. Production
    expiry는 exact `production_route` owner/generation을 종료하고 commit 직전 exact current-valid non-V2
    rollback이 있으면 그 route로, 없으면 `blocked_no_safe_path`로 전환한다. 모든 expiry에서 family
    activation-domain reservation은 유지하고 다른 role/profile의 capability를 변경하지 않는다.
12. Strategy 등록자, `platform routing operator`와 workflow `deploy` 권한자의 command와 scope를
    분리한다. Canary 시작과 production 승격을 하나의 approve/promote command로 합치거나
    `proposed -> production_active`로 건너뛰지 않는다. 등록된 activation holdout/canary runtime producer는
    evidence만 기록하며 disable/rollback은 권한 있는 operator의 emergency command로만 수행한다.
    `activation_profile_emergency_disable`은 roleless `proposed` profile의 exact revision을 대상으로
    `disabled`/`proposal_cancelled`, receipt/audit만 기록하고 role/generation/route/reservation은 변경하지
    않는다. `activation_profile_emergency_disable`과 `activation_profile_rollback`의 active target은 exact
    owner profile ID/revision, role, generation과 role revision을 비교한다. Canary owner는 canary role/generation만 종료하고
    production route를 유지한다. Production rollback은 exact current-valid non-V2 policy로 route를 원자
    전환하며 부적격이면 zero-write다. Production emergency disable은 current-valid rollback이 있으면 같은
    route로, 없으면 `blocked_no_safe_path`로 전환한다. 어떤 emergency command도 family reservation을
    해제하지 않는다. 성공 mutation의 Profile
    state/revision, command idempotency receipt와 canonical audit는 같은 DB transaction에서 commit하고
    audit/receipt 실패 시 zero-write한다. Canonical action과 status는 ADR-0073 및 API의
    Capability Routing V2 allowlist만 사용하며 command 이름에서 동적으로 합성하지 않는다. 권한 거부는 profile state/revision/receipt zero-write이나 existing
    security audit policy의 safe `permission.denied` 기록은 허용한다. 현재 platform actor/API가 구현되지
    않았으므로 일반 organization manager UI/API에서 production V2를 활성화하지 않는다. Activation
    receipt는 actor principal과 command kind, environment/family/profile, target state, activation
    domain/scope digest, bounded reason과 rollback policy를 기본 canonical request hash에 결합한다. 사람 주체의
    platform command는 current `actor_platform_authorization_fence_revision`, service command는 immutable
    workload authorization identity/revision을 추가한다. Evidence generation close는 expected schedule revision, server-derived exact drain revision/digest,
    terminal pinned-attempt watermark/digest, source-barrier coverage digest와 current canary usage epoch/pending
    count를 추가한다. Family
    create는 assignment algorithm/unit contract와 bucket count, domain command는 expected profile/family/
    domain-role revision, canary start는 expected domain generation, expiry는 optional expected role/generation,
    promotion은 evidence start/end sequence, ordered member snapshot
    ID/revision/hash/invalidation-revision/usage-correction-revision digest와 expected generation canary usage
    epoch를 추가한다. Global kill command는 expected environment guard state/revision/epoch, command actor의
    platform authorization fence revision과 disable의 exact recovery approval ID/revision·approver authorization
    fence revision을 포함한다. Approval issue/revoke는 bound guard epoch, command actor authorization fence
    revision, approval ID/expected revision, valid_until과 bounded remediation digest를 포함한다. Same command
    ID·actor·request만 기존 receipt를 반환한다. Different actor/request는 command registry가 family
    create/assignment contract issue/global kill/global kill recovery approval/holdout/canary
    evidence/watermark/seal/benchmark/workflow policy에 각각
    `model_routing.rollout_family_command_conflict`, `model_routing.assignment_contract_command_conflict`,
    `model_routing.global_kill_command_conflict`, `model_routing.global_kill_recovery_approval_command_conflict`,
    `model_routing.activation_holdout_command_conflict`, `model_routing.canary_evidence_conflict`,
    `model_routing.canary_evidence_duplicate_operation`,
    `model_routing.canary_watermark_command_conflict`, `model_routing.canary_snapshot_seal_conflict`,
    `model_routing.canary_generation_close_conflict`, `model_routing.promotion_evidence_reconciliation_conflict`,
    `model_routing.benchmark_command_conflict`, `model_routing.workflow_policy_version_conflict`를 반환한다. Assignment issue의
    expected family/assignment-registry revision stale, concurrent winner 또는 missing registry는
    `model_routing.assignment_contract_state_conflict`와 contract/revision/receipt/success-audit zero-write다.
    Approval issue/revoke의 expected row state/revision stale, concurrent issue/revoke/consume winner와
    revoke-after-consume는 `model_routing.global_kill_recovery_approval_state_conflict`와
    approval/receipt/success-audit zero-write다. Disable 시 approval이 missing/expired/revoked/consumed이거나
    actor 독립성, remediation digest, approver current role 또는 approval-bound/current authorization fence가
    유효하지 않으면 `model_routing.global_kill_recovery_approval_required`와 disable success write zero-write다. Family create의
    command ID 재사용만 `model_routing.rollout_family_command_conflict`이고, 별도 command의 동일·overlap domain reservation 충돌과
    lock timeout은 `model_routing.activation_domain_conflict`가 우선한다. 나머지 profile lifecycle command만
    `model_routing.activation_command_conflict`를 사용하며 모든 success write는 zero-write다. Workflow strategy
    select/opt-out은 current workflow policy expected version, server-derived exact organization/workflow
    authorization fence revisions와 canonical request에 결합된 idempotency key를 요구한다. Policy가 없을 때만 `expected_version=null`을 허용해 CAS insert하고 opt-out도 row 삭제가 아닌
    명시적 version으로 저장한다. V2 선택 policy와 canonical request는 exact profile 대신 immutable
    `rollout_family_id`와 `activation_domain_digest`를 고정한다. 같은 key·request 재시도도 organization/workflow
    authorization fences와 current permission을 먼저 재검증한 뒤에만 기존 receipt를 반환한다. 권한 회수 뒤
    replay는 receipt/policy detail을 숨긴 resource-hiding denial이며 key가 같지만 request 또는 bound
    authorization revision이 다르면 conflict와 zero-write다. Transaction은 organization authorization fence -> workflow resource authorization fence -> policy/receipt
    순서로 잠근다. Commit 직전에 bound revisions, workflow `deploy` 권한, active organization/resource scope와
    strategy/family/domain 유효성을 재검증한다. Membership/principal/team-role/resource grant mutation은 같은
    fence를 잠그고 revision을 증가시킨다. Workflow create transaction은 resource fence revision 0을,
    membership/grant management는 actor-organization fence를 미리 만들며 strategy command는 missing fence를
    lazy create하지 않고 fail-closed한다. 새 run은 bound
    family/domain에서 stable assignment와 canonical production route를 함께 해소한다. Target이면 successor
    exact profile definition/runtime-safety revision/generation을, non-target이면 route owner kind에 따라 exact
    production profile/generation, non-V2 policy/revision 또는 blocked typed failure를 execution snapshot에
    고정한다. 최초 rollout의 route 부재를 successor/default로 보완하지 않는다. Successor promotion은 policy
    mutation이나 재배포 없이 canonical production route를 바꿔 새 run에 적용하며 in-flight run은 별도
    safety override가 없는 한 pinned predecessor/route snapshot을 유지한다. 다른 family/domain 전환은 새 workflow policy CAS command를 요구한다. 성공 policy
    version, receipt와 canonical audit는 같은 transaction에서 commit한다. Stale/CAS loser, 권한 회수와
    audit/receipt 실패는 policy write와 success audit를 남기지 않는다. 권한 거부의 safe
    `permission.denied` audit만 기존 security policy에 따라 허용한다.
    rollout family create와 active domain reservation/role을 획득·교체·해제하는 canary start, production promote,
    emergency disable/rollback, expiry/supersede 및 activation-domain migration은 단일
    `RoutingActivationCoordinator`를 통과한다. Coordinator는 사전 생성된 environment activation guard, command actor의 platform authorization fence,
    family/profile/domain-role row를 고정 순서로 잠근다. Actor fence absent/revision 0은 권한 거부이며 일반
    command가 lazy create하지 않는다. 새 target principal/scope의 최초 role grant만 deterministic revision 0
    unique insert와 grant/revision 1을 같은 transaction에 commit한다. 모든 사람 주체의 platform-privileged command는
    canonical request에 `actor_platform_authorization_fence_revision`을 결합하고 current role/revision을
    commit 직전에 재검증한다. Role grant/revoke·mapping·principal lifecycle도 같은 fence를 증가시키므로
    revoke winner 뒤 command는 permission denial과 success write zero-write다. Service actor는 human role을
    합성하지 않고 별도 immutable workload authorization identity/revision을 사용한다. Guard는 overlap/global kill 직렬화 fence이며
    global kill command만 expected guard state/revision/epoch CAS를 사용한다. Family/domain command는 guard
    lock 아래 current kill/overlap을 다시 읽고 expected family/profile/domain-role revision만 검증한다.
    Production promote는 contiguous promotion evidence set, ordered member invalidation revisions, generation safety와 breach,
    operator 권한, DB time/profile state·revision, exact role owner/generation, source/holdout, credential policy, actual Worker
    readiness, global model eligibility와 exact rollback policy를 commit 직전에 추가 검증한다.
    Canary start는 generation/state/role/first route/evidence schedule/aggregates/source watermarks/receipt/audit를
    한 transaction에 초기화하고 전체 commit 뒤 capability를 발급한다. Sealed-snapshot pre-cutoff late
    evidence는 미승격이면 non-zero snapshot invalidation으로 해당 근거를 영구적인 promotion 부적격으로 만들고,
    promotion-set member의 current production owner이면 profile/role 뒤 authoritative rollback policy를
    set/member보다 먼저 잠가 exact generation block과 current-valid route 또는 `blocked_no_safe_path` 전이를,
    같은 member의 superseded historical owner이면 exact historical generation block-only를 수행해 current
    successor를 보존한다. Set end 뒤 monitoring snapshot은 snapshot -> late-correction aggregates -> generation
    순서로 hard-stop을 평가하고 profile/route를 바꾸지 않은 채 exact current/historical generation만 조건부
    차단한다. Overlap winner만
    commit하고 loser/timeout 또는 선행 eligibility 회수는 conflict/typed ineligible과 zero-write다. Non-overlap command는
    unrelated guard 사용 때문에 stale 처리하지 않고 순차 검증 후 각각 commit한다. Runtime routing,
    benchmark와 provider/network I/O 중에는 이 lock을 유지하지 않는다.
    Environment guard는 `global_routing_kill_enabled`, monotonic `global_routing_kill_epoch`, revision과
    current enable actor principal을 소유한다. `(principal, platform role scope)`별
    `platform_authorization_fence_revision`은 effective platform role grant set의 monotonic revision이다. 첫
    grant의 target row가 없으면 권한 관리 service만 deterministic key revision 0 row를 unique insert하고
    같은 transaction에서 grant와 revision 1을 commit한다. Concurrent first grant는 unique/CAS 뒤 current row를
    다시 잠가 순차 수렴하며 absent/revision 0 fence는 actor 권한을 만들지 않는다. Role grant/revoke,
    role-permission mapping과 principal lifecycle mutation이 같은 row를 exclusive하게 잠그고 증가시킨다. 다수 row는 canonical key 순서로 잠근다. 권한 있는 `platform routing operator`의 전용
    `global_routing_kill_enable | global_routing_kill_disable` command만 guard를 변경한다. Enable도 guard ->
    command actor authorization fence에서 current role/revision을 검증한 뒤 actor를 고정하고 즉시
    fail-safe다. Disable용 approval은 enable actor와 다른 current
    `platform routing recovery approver`의 `global_routing_kill_recovery_approval_issue` command로만
    발급한다. Approval은 deterministic actor/request ID, environment/current enabled guard epoch, bounded
    remediation contract digest, DB-time `valid_until`, approver principal, issue 시점의 exact platform
    authorization fence revision과 monotonic approval revision에 결합한 server-owned row이며 stored state는
    `active | revoked | consumed`다. Expiry는 DB clock에서
    파생하고 별도 비직렬화 state write를 하지 않는다.

    Issue는 environment guard -> approver platform authorization fence -> command/approval unique identity
    순서로 exact existing row를 반환하거나 신규 row를 insert하며 current role/fence revision을 재검증해
    approval, bound authorization revision, receipt와 `model_routing.global_kill_recovery_approval.issued` audit를 원자
    commit한다. Revoke는 guard -> revoke actor authorization fence -> exact approval, disable은 guard ->
    disable actor/approval approver authorization fences(canonical order) -> exact approval 순서로 잠근다.
    Revoke는 actor role/fence와 expected revision이 current일 때 active -> revoked 전이, receipt와 전용 audit를
    원자 commit한다. Disable은 lock 안에서 actor role/fence, expected guard state/revision/epoch,
    server-derived current remediation snapshot digest와 approval-bound digest 일치, approval
    active/current/non-expired 상태, bound epoch, approval-bound approver fence revision과 current
    role/revision 일치 및 approval principal이 enable actor와 disable command actor 모두와 다른지 재검증해
    approval consume, enabled=false, 증가한 epoch/revision, receipt와 canonical disabled audit를 한
    transaction에 commit한다. 만료 approval은 state를 암묵적으로 변경하지 않고
    approval-required와 disable success write zero-write로 닫는다. Revoke winner 뒤 disable은
    approval-required zero-write이고 approver-role revoke winner도 stale authorization revision으로 disable을
    zero-write한다. Disable winner 뒤 approval revoke는 state conflict이고 role revoke는 다음 command부터
    적용된다. Exact replay는 기존 receipt를 반환하고 stale/concurrent/권한 회수/audit·receipt 실패는 전체
    zero-write다. Enabled 동안
    capability 발급은 0회이며 disable 뒤에도 이전 capability를 되살리지 않고 모든 current gate를 통과한
    새 capability만 새 epoch에 binding한다.
13. Runtime은 실행 시작 시 strategy/profile/workflow policy version과 immutable profile definition revision,
    신규 run 선택용 admission lifecycle revision, attempt safety용 runtime safety revision을 고정한다.
    Emergency disable, profile `valid_until`, exact requirement-source lifecycle, credential/model/rollback
    policy revoke, provider/organization lifecycle과 global/profile-bound safety generation/epoch는 Judge,
    primary와 fallback provider 호출 직전에 재검증한다. 정상 promotion으로 predecessor의 admission state/revision만
    `superseded`가 된 경우에는 promotion 전에 admit된 pinned run의 다음 attempt를 허용한다. 각 attempt는
    DB current time이 pinned profile `valid_until` 전인지, current definition/runtime-safety revision,
    organization/data scope, exact organization/provider/purpose data-egress policy, credential/model/provider
    lifecycle, rollback과 global/scoped safety를 다시 검증하고 별도의 capability·budget admission과 durable intent를 commit한 뒤에만 provider I/O를
    시작한다. Capability는 exact profile ID/definition revision/`valid_until`, runtime safety revision,
    global/generation epoch, generation canary usage validity epoch, organization authorization, exact
    organization/provider/purpose data-egress policy, decision에 사용한 exact Judge/learner source identity와
    `requirement_source_lifecycle_revision`, credential policy/credential, model/provider lifecycle과 rollback
    policy revision을 binding한다. Accepted cache origin도 current source lifecycle을 재검증하고
    `not_required`만 empty source fence를 사용한다. Operational evidence가 선택에 기여하면 server-derived
    exact aggregate validity row identity/revision set과 `model_evidence_fence_set_digest`도 binding하며,
    미사용은 explicit empty evidence set이다. Admission lifecycle revision은 binding하지 않는다.
    한 attempt의 admission은 다른 attempt에 재사용하지 않는다. 만료를 발견하면 sweeper state와 무관하게
    `provider_started`·provider I/O를 zero-write하고, pinned profile의 exact current-valid non-V2 rollback을
    독립 admission한 뒤 사용하거나 canonical `model_routing.activation_profile_expired` typed failure로 닫는다.
    Strategy resolution의 safe reason `profile_expired`와 외부/worker failure code는 구분한다. Global stale은 모든 family를,
    generation stale/block은 해당 generation만 차단하며 runtime safety revision 불일치는 해당 attempt를
    차단한다. Rollback policy가 current-valid하지 않으면 새 V2 attempt를 시작하지 않고
    `model_routing.rollback_target_invalid`로 닫는다.
    Breach가 난 scoped lifecycle generation의 block은 in-place로 해제하지 않는다. 기존 blocked generation이
    current `production_route` profile owner라면 권한 있는 operator가 emergency disable/rollback으로 그
    profile을 닫고 route를 exact current-valid `non_v2_policy` owner로 먼저 전환한다.
    `blocked_no_safe_path` 또는 blocked profile owner route에서는 remediation canary start를 거부하고
    generation/state/role/evidence/receipt/audit를 zero-write한다. 기존 blocked generation이 canary
    successor role을 소유하면 operator가 그 role도 먼저 terminal하게 닫되 block은 유지한다. Remediation을
    반영한 새 immutable profile은 holdout과 독립 canary-start 승인을 통과한 뒤 transaction 안에서 비어
    있는 canary role과 canonical current-valid non-V2 route를 다시 확인하고 새 generation을
    limited-canary 전용으로 연다. Target만 새 generation, non-target은 canonical non-V2 route를 사용한다.
    새 generation의 canary evidence와 별도 production 승격이 완료된 뒤에만 그 generation을 production
    role로 전환한다.
    `provider_started`는 lifecycle 비교 뒤의 별도 blind write가 아니다. Runtime은 environment guard,
    activation profile runtime-safety/`valid_until`, optional benchmark actor platform authorization fence,
    organization authorization fence, optional benchmark target workflow/deployment resource authorization
    fence, organization/provider/purpose data-egress policy fence, bound requirement-source lifecycle fence,
    credential policy/credential, model/provider lifecycle, rollback policy, bound operational evidence validity
    rows, optional snapshot, generation canary usage validity row, exact generation safety row와 attempt를 고정
    순서로 잠근다. DB clock과 capability-bound definition/runtime-safety revision/state, exact authorization/
    organization data-egress/requirement-source revisions, source current-active state와 selected
    provider/purpose 허용 상태, model-evidence fence set/revisions, canary usage epoch와 pending count 0을
    재검증해 같은 transaction에서 `provider_started`를 commit한다. Bound source가 missing/stale/revoked/
    retired면 해당 V2 attempt는 `model_routing.activation_requirement_source_unavailable`과 provider I/O
    zero-write이며 새 판정/capability 없이 source를 교체하지 않는다. 각 organization/requirement-source/
    credential/model/provider/rollback revoke와 organization data-egress policy mutation도 자신이 바꾸는
    동일 fence를 exclusive하게 획득하고 revision을 증가시킨다. Organization egress fence는
    ADR-0064/0067의 provider catalog/transport `egress_revision`과 별도다. Missing/stale/revoked/denied policy는
    `model_routing.data_egress_policy_denied`와 provider-start/payload/network I/O zero-write로 닫고 다른
    scope/provider detail을 노출하지 않는다. 첫 policy mutation은 egress management service만 nullable-first
    CAS로 deny revision 0 row와 요청 state/revision 1을 원자 commit하며 runtime/start command는 missing row를
    만들지 않는다. Global kill enable은 environment guard를 잠근다.
    Recovery approval issue는 guard -> approver authorization fence -> deterministic command/approval unique
    identity를, revoke/disable은 guard -> canonical actor/approver authorization fences -> exact approval row를
    잠근다. Open assigned direct
    event와 non-blocking hard-stop crossing은 environment shared fence 뒤 aggregate -> generation,
    unassigned schedule-lag direct event는 generation만 잠근다. Sealed reconciliation은 environment -> profile
    runtime-safety -> role 뒤 미승격 non-blocking이면 snapshot, 미승격 direct이면 snapshot -> generation,
    promotion-member current이면 rollback policy -> set -> member -> generation, member historical이면 set ->
    member -> historical generation, post-promotion monitoring이면 snapshot -> correction aggregates ->
    generation 순서로 잠근다. Provider I/O는 start commit 뒤에만 시작한다. 어떤
    revoke/kill/breach/evidence correction이 먼저 commit하면 대기한 start는 `provider_started`와 I/O
    zero-write로 닫고, start가
    먼저 commit한 attempt만 이미 승인된 호출로 취급한다. Bounded lock timeout과 serialization retry
    소진은 fail-closed한다.
    `provider_started` 뒤 outcome-unknown이 된 Judge에는 같은 routing decision의 자동 retry나 secondary
    Judge를 수행하지 않는다. 별도로 versioning된 current-valid non-V2 stored safe path가 있으면 독립
    work-model attempt의 current admission과 durable intent 뒤에만 사용한다. Outcome-unknown primary에도
    같은 run의 자동 retry/fallback을 수행하지 않는다.
    `provider_started` 전 선택 후보만 무효가 되면 같은 pinned contract와 원래 승인 후보 안에서 최대
    한 번 재선택할 수 있고, definitive provider failure 뒤에는 configured fallback만 평가한다.
14. Raw prompt, RAG 원문, credential, private candidate exclusion detail과 provider payload를 routing
    trace, audit, cache와 Git report에 저장하지 않는다.
15. `policy_preview`는 Judge/provider/학습 없이 server-known gate만 평가하고 요구 판정이 없으면 최종
    모델 대신 `preview_status=requirement_pending`을 반환한다. Billable `benchmark`는 논리
    `platform routing benchmark operator` 권한과 target workflow/deployment의 current `execute` 권한을
    모두 가진 actor가 explicit environment/organization scope, proposed profile, budget과 manifest를
    actor/request-bound idempotent command로 제출한 경우에만 V2를 진단 실행할 수 있다. Server는 exact
    platform actor, actor-organization과 target workflow/deployment authorization fence revisions를 canonical
    command와 immutable run intent에 결합한다. Admission은 platform -> organization -> target resource
    authorization fences -> receipt/run intent/audit/첫 pending `benchmark_dispatch_intent` 순서로 잠그고
    current role/scope/permission과 bound revisions를 commit 직전에 다시 검증한다. Permission mutation은
    같은 fence의 exclusive lock/revision 증가를 사용한다. Revoke winner 뒤 admission은 resource-hiding
    denial과 run/receipt/success audit/provider I/O zero-write다. Dispatch는 deterministic
    stage/ordinal/exact model attempt key, monotonic revision, DB-clock available/lease time와 fencing token을
    가진 durable outbox다. Duplicate command도 current 권한을 재검증한 뒤에만 기존 run을 반환하고
    pending/expired dispatch는 terminal denial 또는 기존 진행으로 수렴시킨다.

    Recovery dispatcher는 CAS로 bounded lease를 claim하고 unique attempt intent와 ADR-0069 operation을
    생성하거나 기존 상태에서 재개한다. Run admission 뒤 attempt 생성 전 crash는 lease expiry 후
    재개하며, concurrent recovery는 fencing winner 하나만 진행한다. Attempt intent 뒤 crash는 같은
    operation으로 재개한다. 각 stage terminal transaction은 다음 stage가 필요하면 deterministic next
    dispatch를 함께 생성하고 마지막 stage면 run/current dispatch를 함께 terminal로 전이한다. Non-terminal
    run에 terminal current stage만 있고 next dispatch가 없는 committed 상태는 허용하지 않는다. Commit
    직후 crash와 duplicate finalizer는 이미 생성된 stage/ordinal unique dispatch와 CAS로 같은 outcome에
    수렴한다. `provider_started` 또는 outcome-unknown attempt는 다시 호출하지 않는다.
    각 recovered attempt는 provider I/O 직전 platform benchmark 권한과 target `execute` 권한을 다시
    판정하고 current revisions가 immutable run-bound admission revisions와 정확히 같은 경우에만 run
    authorization identity/revisions를 capability에 binding한다. Run 도중 revision refresh는 금지한다.
    Provider-start가 같은 fences에서 current 권한, run-bound revisions와 capability binding을 재검증하고
    credential·egress·budget admission과 durable intent를 commit한 뒤에만 호출한다. Revoke winner 뒤 해당 attempt와 이후 attempt의 provider
    I/O는 0회이고 run/dispatch를 terminal denied로 종결한다. Start winner exact attempt만 완료하며 다음 attempt도 같은 run-bound revisions를 다시 확인한다. 다른 actor/request는
    `model_routing.benchmark_command_conflict`와 provider I/O/success write zero다.

    `effective_strategy_id=capability_routing_v2`, resolution reason은 `benchmark_isolated`로 기록할 수
    있지만 production activation, policy/learner/cache와 외부 부수효과를 변경하거나 benchmark provenance를
    production 재사용에 쓰지 않는다. 현재 platform benchmark actor/API가 없으므로 일반 product API에서
    billable benchmark를 제공하지 않는다. 구형 Worker는 V2를 V1 의미로 실행하지 않는다.

Learner candidate gate는 production activation gate가 아니다. FR-016은 MBA-366부터 MBA-369와
Provider Candidate Credential Policy, model evidence, cost ledger, benchmark governance 및 activation
governance 후속 구현이 모두 완료되고
MBA-372 통합 검증을 통과하기 전에는 기본 비활성이다.

기존 `judge_bootstrap_incremental_v1` 문자열만 있고 contract version이 없는 policy/cache/learner는
V1/V2 의미를 추정하지 않는다. Dynamic routing을 끄고 current-valid stored safe model로 닫으며 V2
evidence 또는 rollback target으로 자동 사용하지 않는다. 명시적 재배포/reissue 뒤 versioned V1 또는
V2로 전환하고, V2 profile은 재검증·발행한 rollback policy version을 별도로 가리켜야 한다.

## Policies And Edge Cases

- 비교 실행은 실제 LLM 호출이므로 비용이 발생할 수 있다.
- B 후보 실행이 실패해도 A baseline과 기존 experiment history는 유지한다. 해당 실행은 `failed` 후보로 기록하고 실패 사유를 결과 분석 화면에 표시한다.
- 비용 정보가 없는 모델은 비용 비교 불가 상태로 표시한다.
- credential 원문, API key, encrypted config는 응답이나 화면에 표시하지 않는다.
- 비교 결과는 비용만으로 승자를 정하지 않는다. 사용자가 출력 결과를 보고 판단한다.
- downstream 계약 검증은 안전성 보조 기능이며, 전체 workflow 성공을 보장하지 않는다.
- 최종 검증은 기존 workflow 테스트 실행으로 수행할 수 있어야 한다.
- 실패한 LLM node run은 Cost Optimizer baseline 후보에서 제외한다. credential 오류, provider 오류, timeout 같은 실패 원인은 비용 최적화가 아니라 실행 디버깅 영역에서 다룬다.
- output preview 또는 usage summary가 없는 LLM node run은 Cost Optimizer baseline 후보에서 제외한다.
- baseline input이 보관 기간 만료, redaction, retention, 저장 누락으로 복원되지 않는 경우 해당 baseline은 목록에 표시하되 비교 실행은 허용하지 않는다.

## Deferred Scope

다음 항목은 Cost Optimizer 방향에는 포함되지만, 1차 구현의 필수 범위에서는 제외하고 후속 기능으로 분리한다.

- workflow 전체 A/B 테스트
- 사용자 클릭 기반 단발 모델 추천 화면
- 최적화 에이전트
- LLM response cache
- budget guardrail
- 실패 지점부터 partial rerun
- downstream 전체 자동 실행
- side-effect node dry-run 인프라
- RAG strategy A/B 테스트
- Knowledge Skill version/freshness 기반 비교

## Knowledge/RAG Compare Policies

이 섹션은 Cost Optimizer의 기존 1차 범위를 대체하지 않고, RAG 포함 workflow 비교가 추가될 때 필요한 Knowledge/RAG 경계를 정의한다.

- 비교 실행에도 workflow 실행 권한과 대상 credential의 `use` 권한이 필요하다.
- 비교 실행에서 발생한 LLM 호출도 usage/비용으로 기록한다. 최적화 기능 자체의 비용이 숨겨지면 안 된다.
- 후보 모델은 요청 organization에서 사용 가능한(verified credential-model relation이 있는) 모델로 제한한다.
- RAG 포함 비교에서 `general RAG` baseline을 사용하더라도 권한 없는 문서가 prompt, citation, trace, audit, 비교 UI에 들어가면 안 된다.
- 비교 리포트에는 context token estimate, retrieved chunk count, citation count, cost, latency, policy result, query rewrite 적용 여부, evidence sufficiency 결과, source tier summary 같은 safe summary만 표시한다. 권한 없는 문서명/ID, raw source metadata, raw rewritten query, raw prompt/completion, raw chunk content는 표시하지 않는다.
- Skill 기반 비교 리포트에도 raw skill body, hidden source refs, raw source title/path/url, restricted document list, raw eval fixture를 표시하지 않는다.
- `llm_assisted` query rewrite는 별도 승인 전까지 비교 변수로 사용하지 않는다. 승인 후 비교 변수로 삼으면 rewrite LLM call의 usage/cost도 비교 비용에 포함해야 한다.
- 모든 RAG 검색 모드는 권한 검사를 통과한 문서만 검색 후보로 사용한다. A/B의 차이는 권한 적용 여부가 아니라 권한 범위 안에서 근거를 얼마나 정밀하게 선택하느냐다.
- 가격 정보가 없는 모델은 자동 추천 후보에서 제외하고, 수동 비교 시에는 비용 비교 불가 상태를 명시한다.
- 한쪽 variant 실행이 실패하면 성공한 variant의 부분 결과와 실패 원인을 구분해 표시하고, 절감률은 계산하지 않는다.
- 더 저렴한 후보가 없으면 빈 리포트 대신 "절감 가능 없음"을 명시한다.

## Grounding Option Terminology

- 기존 `answerGroundingCheck` API/graph key와 `off|basic|strict` 값은 호환성을 위해 유지한다.
- 사용자 화면에서는 이 옵션을 `답변·검색 문서 어휘 일치도`로 표시하며, Citation 표시 또는 evidence sufficiency 차단 기능으로 설명하지 않는다.
- 사용자 Citation은 별도 `citationDisplayMode`가 소유하며 Cost Optimizer 후보 설정에서도 두 옵션을 독립적으로 보존한다.

## Open Questions

Open Question 중요도는 다음 3단계로 나눈다.

- `Priority 1`: 현재 기능 구현 또는 데모 핵심 흐름을 막는 결정이다. 구현 전에 먼저 정해야 한다.
- `Priority 2`: 데모 안정성과 후속 구현 품질에 영향을 준다. 현재 구현은 fallback으로 진행할 수 있지만 PR 전후로 정리해야 한다.
- `Priority 3`: 장기 사용성, 성능, 확장성 결정이다. 현재 구현을 막지는 않으며 후속 이슈로 분리할 수 있다.

| Priority | 영역 | Question | 왜 중요한가 | 결정 전 임시 처리 |
| --- | --- | --- | --- | --- |
| Priority 1 | 이전 로그 선택 API | target LLM node 실행 로그를 비용/토큰/시간 기준으로 검색·필터·정렬하는 API를 별도로 둘지 | 기존 workflow run list만으로는 노드 기준 baseline 선택 UX를 만들기 어렵다 | 결정: LLM node 기준 baseline latest/list API를 둔다. |
| Priority 1 | downstream 호환성 | baseline 실행 시점 graph와 현재 graph의 호환성을 어떤 기준으로 판정할지 | 다운스트림이 바뀐 상태에서 비교 결과를 잘못 해석할 수 있다 | 결정: `검증 가능`, `주의 필요`, `검증 불가` 3상태와 `unknown` fallback으로 표시한다. |
| Priority 1 | 비교 결과 저장 | 비교 결과를 저장할지, 화면에서만 보여줄지 | 저장 여부에 따라 DB/API/화면 이력이 달라진다 | 결정: experiment/candidate 전용 테이블에 저장하고 usage log는 candidate id로 직접 연결한다. |
| Priority 1 | 적용 방식 | 선택 후보를 draft에 바로 적용할지, versioning과 연결할지 | 사용자가 실수로 기존 설정을 잃을 수 있다 | 결정: 현재 draft target LLM node에 후보 설정 전체를 적용하고 기존 저장/되돌리기 흐름을 따른다. |
| Priority 2 | downstream 계약 검증 | 1차 구현에서 어떤 다음 노드 타입까지 계약 검증할지 | 지원하지 않는 노드가 있으면 검증 결과를 신뢰하기 어렵다 | 결정: target LLM node를 직접 참조하는 variable extraction mapping, condition selector, answer output selector, Slack referenced variable selector를 후보 출력 기준으로 검사한다. |
| Priority 2 | 실패 후보 처리 | B 후보 실행이 실패했을 때 workspace 전체를 실패로 볼지 | 비교 UX가 달라진다 | 해당 B 실행만 `failed` 후보로 기록하고 A baseline과 기존 history는 유지한다 |
| Priority 1 | 품질 점수 rubric/가중치 | instruction fulfillment, relevance/completeness, clarity/consistency, RAG groundedness를 어떤 비율로 100점에 합산할지 | 가중치가 제품의 품질 정의가 되며 node 유형마다 적합도가 다르다 | 1차는 node output contract 기반 공통 rubric을 사용하되 정확한 가중치는 구현 전 확정한다 |
| Priority 1 | 품질 점수 경고 기준 | baseline 대비 몇 점 하락 또는 어느 confidence부터 적용 확인을 요구할지 | 너무 느슨하면 품질 저하를 놓치고 너무 엄격하면 비용 절감 후보를 적용하지 못한다 | 품질 점수 단독 hard block은 금지하고, threshold 미확정 동안 하락 또는 low confidence이면 항상 확인을 요구한다 |
| Priority 2 | 품질 judge 모델 선택 | organization에 여러 provider/model credential이 있을 때 어떤 모델을 judge로 사용할지 | judge 품질과 빠른 검증 비용이 달라진다 | 현재 사용자가 실행 가능한 모델 중 별도 evaluator allowlist를 두고, 없으면 품질 평가만 `unavailable`로 처리한다 |
| Priority 2 | 빠른 검증 baseline 범위 | 최신 성공 기록을 현재 active deployment/config cohort로 제한할지 전체 성공 기록에서 찾을지 | 과거 설정의 로그를 기준으로 추천 후보를 비교하면 결과 해석이 어긋난다 | active deployment와 node setting fingerprint가 같은 최신 성공 운영 로그로 제한한다 |
| Priority 3 | 자동 추천 | 가격표 기반 단발 추천 화면을 별도로 둘지 | 정책 기반 자동 라우팅과 겹치면 사용자가 실행 정책과 단발 추천을 혼동할 수 있다 | FR-011은 정책 기반 자동 라우팅으로 결정하고, 단발 추천 화면은 후속으로 분리한다 |
| Priority 3 | 모델 라우팅 정책 세부 gate | 정책 자동 반영의 정확한 schema/downstream/fallback/confidence 기준값을 어디까지 고정할지 | gate가 느슨하면 품질이 흔들리고, 너무 엄격하면 비용 절감 효과가 낮다 | 기본 원칙은 품질 gate 통과 시 조건부 자동 반영, 미통과 시 `pending_review`로 둔다 |
| Priority 3 | 최적화 에이전트 | 에이전트가 어떤 근거로 모델/프롬프트/파라미터 최적화 후보를 제안할지 | 추천 자체도 비용이 들고 잘못된 추천은 workflow 품질을 해칠 수 있다 | 후속 기능으로 분리하고 자동 적용은 금지한다 |
| Priority 3 | cache/budget | LLM cache와 budget guardrail을 1차 구현에 넣을지 | 실제 비용 절감 효과는 크지만 범위가 커진다 | 별도 follow-up 이슈로 분리한다 |
