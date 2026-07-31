# Judge-first 자동 모델 라우팅 30회 경제성 비교 보고서

## 한눈에 보는 결론

이 단계는 학습에 쓰지 않은 30개 요청을 자동 라우팅, 고가 모델 고정에 똑같이 보내 비용·속도·품질을 비교합니다.

- 자동 라우팅 총 제품 비용: $0.114996
- 고가 고정 대비 자동 라우팅 순절감: $0.652029 (절감)
- 중간 고정 비교: 이번 실행에서 제외
- 저가 고정 비교: 이번 실행에서 제외
- 자동 라우팅에서 Runtime Judge가 실제 호출된 횟수: 29/30

자동 라우팅 비용에는 요청 처리 모델 비용과 Runtime Judge 비용을 모두 포함했습니다. 실험의 품질 평가 Judge 비용은 제품 기능의 런타임 비용이 아니므로 별도로 표시합니다.

## 실험 조건

- 실행 시각: 2026-07-21T23:10:07.509384+00:00
- workflow: `98000000-0000-0000-0000-000000000002` / LLM node: `llm-triage`
- 실행 방식: 실제 WorkflowEngine, 실제 OpenAI provider 호출, 실제 배포 run/node run/usage log 기록
- 자동 라우팅 전략: `judge_bootstrap_incremental_v1`
- 자동 라우팅 후보: gpt-4o-mini, gpt-4.1-mini, gpt-4.1, gpt-4o, gpt-5-mini, gpt-5.4-mini, gpt-5.4, gpt-5.6-sol, gpt-5.6-luna, gpt-5.6-terra, o3
- 고정 비교 모델: 고가 `gpt-5.6-sol`
- 라우팅 Judge: `gpt-5.4-mini`
- 독립 품질 평가 Judge: `gpt-5-mini`
- RAG: 미사용. 이번 비교에서는 KB 검색 품질 변수를 빼고 모델 라우팅 자체의 비용·속도·출력 품질만 측정했습니다.
- 정책·학습 상태: 비교 중에는 고정했습니다. 이 요청들은 학습 label이나 정책 갱신 횟수에 포함하지 않았습니다.

## 데이터셋

이 단계의 30개 요청은 여러 업무 유형과 난이도를 섞었고, 동일 문장을 반복하지 않았습니다.

| 예상 난이도 | 건수 |
| --- | ---: |
| advanced | 11 |
| balanced | 11 |
| economy | 8 |

## 비용·속도·품질 비교

| 방식 | 처리 모델 비용 | 라우팅 Judge 비용 | 총 제품 비용 | 평균 LLM 노드 시간 | 평균 전체 시간 | 평균 품질 점수 | JSON 계약 통과 | 품질 통과 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 자동 라우팅 | $0.058038 | $0.056958 | $0.114996 | 8545ms | 8805ms | 76.7 | 96.7% | 50.0% |
| 고가 고정 (gpt-5.6-sol) | $0.767025 | $0.000000 | $0.767025 | 16145ms | 16398ms | 90.0 | 100.0% | 93.3% |

## 자동 라우팅이 실제로 고른 모델

- `None`: 1회
- `gpt-4.1`: 8회
- `gpt-4o-mini`: 9회
- `gpt-5-mini`: 11회
- `gpt-5.4-mini`: 1회
- 사전 가설 기준 적중: 18/30건
- 성능 부족 선택: 11건 / 과도한 선택: 0건 / 미분류: 1건

## 로컬 라우터 학습 수준

- Judge label 누적: 29건
- 실제 선택 모델 종류: 5개 ({'gpt-4o-mini': 9, 'gpt-4.1': 8, 'gpt-5-mini': 11, 'gpt-5.4-mini': 1, 'None': 1})
- 처리 출처 분포: {'runtime_judge': 29, 'unknown': 1}
- 로컬 라우터가 Judge 없이 직접 선택한 횟수: 0회
- 로컬 라우터가 자신 없어 Judge로 되돌린 횟수: 0회
- Judge 신뢰도 평균 / P95: 0.911 / 0.950
- 로컬 takeover 최소 신뢰도: 0.78
- 최종 저장 정책 학습 모드: judge_first
- 최종 저장 학습 표본: Judge 24건 / 운영 완료 24건
- 학습 artifact: multilingual_e5_task_requirements_ordinal_v3 (intfloat/multilingual-e5-base)
- artifact 학습 예시 수: 24건 / artifact 모델 label: ['gpt-4.1-mini', 'gpt-4o-mini', 'gpt-5-mini', 'gpt-5.4-mini']

이 수치는 로컬 모델이 단순히 label을 저장했는지뿐 아니라, 실제로 충분한 자신감을 얻어 Judge 호출을 대신했는지를 보여 줍니다. takeover가 낮으면 현재 학습 구조가 비용 절감에는 불리하다는 뜻이며, 이 경우 Judge-first를 제품의 장기 기본값으로 두면 안 됩니다.

## 실험 평가 비용

- 독립 품질 Judge 총비용: $0.033772
- 독립 품질 Judge 총호출: 30회
- 이 비용은 2개 방식의 결과를 공정하게 비교하기 위한 측정 비용이며 제품 운영비 비교에는 포함하지 않았습니다.

## 요청별 결과

| # | 주제 | 예상 난이도 | 자동 선택 모델 | 자동 품질 | 자동 총비용 | 고가 품질 | 고가 비용 |
| ---: | --- | --- | --- | ---: | ---: | ---: | ---: |
| 1 | holdout_product_help | economy | gpt-4o-mini | 62.0 | $0.001983 | 88.0 | $0.014080 |
| 2 | holdout_access | balanced | gpt-4.1 | 84.0 | $0.005495 | 92.0 | $0.016610 |
| 3 | holdout_finance | balanced | gpt-5-mini | 92.0 | $0.003992 | 84.0 | $0.023615 |
| 4 | holdout_access | advanced | gpt-4.1 | 74.0 | $0.005697 | 92.0 | $0.041285 |
| 5 | holdout_reliability | advanced | gpt-5-mini | 92.0 | $0.004199 | 96.0 | $0.031120 |
| 6 | holdout_governance | balanced | gpt-5-mini | 92.0 | $0.004796 | 90.0 | $0.034190 |
| 7 | holdout_access | balanced | gpt-5-mini | 92.0 | $0.003510 | 85.0 | $0.015200 |
| 8 | holdout_finance | economy | gpt-4o-mini | 65.0 | $0.002075 | 92.0 | $0.010870 |
| 9 | holdout_security | balanced | gpt-5-mini | 92.0 | $0.003577 | 86.0 | $0.025265 |
| 10 | holdout_finance | advanced | gpt-5-mini | 92.0 | $0.004486 | 89.0 | $0.039180 |
| 11 | holdout_access | balanced | gpt-5-mini | 92.0 | $0.003801 | 88.0 | $0.019845 |
| 12 | holdout_finance | advanced | gpt-4.1 | 72.0 | $0.005734 | 92.0 | $0.022875 |
| 13 | holdout_security | advanced | gpt-5.4-mini | 86.0 | $0.004765 | 92.0 | $0.033315 |
| 14 | holdout_reliability | balanced | None | 0.0 | $0.000000 | 92.0 | $0.041070 |
| 15 | holdout_product_help | economy | gpt-4o-mini | 72.0 | $0.002236 | 88.0 | $0.015355 |
| 16 | holdout_product_help | economy | gpt-4o-mini | 75.0 | $0.002374 | 85.0 | $0.010345 |
| 17 | holdout_data | advanced | gpt-4.1 | 72.0 | $0.005311 | 92.0 | $0.032330 |
| 18 | holdout_product_help | economy | gpt-4o-mini | 65.0 | $0.001663 | 90.0 | $0.012195 |
| 19 | holdout_security | advanced | gpt-4.1 | 78.0 | $0.006388 | 92.0 | $0.042275 |
| 20 | holdout_data | balanced | gpt-5-mini | 88.0 | $0.003832 | 92.0 | $0.023815 |
| 21 | holdout_builder_help | economy | gpt-4o-mini | 70.0 | $0.002604 | 85.0 | $0.010210 |
| 22 | holdout_cost_help | economy | gpt-4o-mini | 60.0 | $0.002294 | 88.0 | $0.014175 |
| 23 | holdout_knowledge_help | economy | gpt-4o-mini | 70.0 | $0.002534 | 90.0 | $0.013060 |
| 24 | holdout_customer_operations | balanced | gpt-5-mini | 92.0 | $0.003751 | 86.0 | $0.023985 |
| 25 | holdout_release_operations | balanced | gpt-4o-mini | 60.0 | $0.001870 | 92.0 | $0.030155 |
| 26 | holdout_usage_reconciliation | balanced | gpt-5-mini | 88.0 | $0.003949 | 92.0 | $0.030305 |
| 27 | holdout_payment_incident | advanced | gpt-4.1 | 74.0 | $0.005809 | 92.0 | $0.039815 |
| 28 | holdout_privacy_incident | advanced | gpt-4.1 | 80.0 | $0.006440 | 93.0 | $0.039645 |
| 29 | holdout_legal_retention | advanced | gpt-4.1 | 82.0 | $0.005489 | 94.0 | $0.029445 |
| 30 | holdout_multi_region_incident | advanced | gpt-5-mini | 88.0 | $0.004342 | 92.0 | $0.031395 |

## 해석 시 주의점

- 품질 점수는 독립 Judge가 동일한 계약으로 평가한 상대 지표입니다. 실제 고객 만족도나 사람 검수 결과를 완전히 대체하지는 않습니다.
- 자동 라우팅의 전체 시간에는 Judge 호출 시간이 포함됩니다. 처리 모델 시간만 보면 절감돼도 전체 시간은 늘어날 수 있습니다.
- JSON 계약 실패와 workflow 실패는 실행 품질 지표에 포함합니다. 품질 Judge 시스템 오류는 최대 3회 재시도하고, 끝내 실패하면 품질 평균에서 제외한 뒤 실패 건수를 별도로 표시합니다.
