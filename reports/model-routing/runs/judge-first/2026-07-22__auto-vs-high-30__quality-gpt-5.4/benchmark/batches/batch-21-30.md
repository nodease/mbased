# Judge-first 자동 모델 라우팅 30회 경제성 비교 보고서

## 한눈에 보는 결론

이 단계는 학습에 쓰지 않은 30개 요청을 자동 라우팅, 고가 모델 고정에 똑같이 보내 비용·속도·품질을 비교합니다.

- 자동 라우팅 총 제품 비용: $0.122735
- 고가 고정 대비 자동 라우팅 순절감: $0.569435 (절감)
- 중간 고정 비교: 이번 실행에서 제외
- 저가 고정 비교: 이번 실행에서 제외
- 자동 라우팅에서 Runtime Judge가 실제 호출된 횟수: 30/30

자동 라우팅 비용에는 요청 처리 모델 비용과 Runtime Judge 비용을 모두 포함했습니다. 실험의 품질 평가 Judge 비용은 제품 기능의 런타임 비용이 아니므로 별도로 표시합니다.

## 실험 조건

- 실행 시각: 2026-07-21T23:16:34.169926+00:00
- workflow: `98000000-0000-0000-0000-000000000002` / LLM node: `llm-triage`
- 실행 방식: 실제 WorkflowEngine, 실제 OpenAI provider 호출, 실제 배포 run/node run/usage log 기록
- 자동 라우팅 전략: `judge_bootstrap_incremental_v1`
- 자동 라우팅 후보: gpt-4o-mini, gpt-4.1-mini, gpt-4.1, gpt-4o, gpt-5-mini, gpt-5.4-mini, gpt-5.4, gpt-5.6-sol, gpt-5.6-luna, gpt-5.6-terra, o3
- 고정 비교 모델: 고가 `gpt-5.6-sol`
- 라우팅 Judge: `gpt-5.4-mini`
- 독립 품질 평가 Judge: `gpt-5.4`
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
| 자동 라우팅 | $0.064734 | $0.058001 | $0.122735 | 8362ms | 60339ms | 69.8 | 100.0% | 36.7% |
| 고가 고정 (gpt-5.6-sol) | $0.692170 | $0.000000 | $0.692170 | 15153ms | 69236ms | 87.3 | 93.3% | 90.0% |

## 자동 라우팅이 실제로 고른 모델

- `gpt-4.1`: 11회
- `gpt-4o-mini`: 9회
- `gpt-5-mini`: 9회
- `gpt-5.4-mini`: 1회
- 사전 가설 기준 적중: 18/30건
- 성능 부족 선택: 12건 / 과도한 선택: 0건 / 미분류: 0건

## 로컬 라우터 학습 수준

- Judge label 누적: 30건
- 실제 선택 모델 종류: 4개 ({'gpt-4o-mini': 9, 'gpt-4.1': 11, 'gpt-5-mini': 9, 'gpt-5.4-mini': 1})
- 처리 출처 분포: {'runtime_judge': 30}
- 로컬 라우터가 Judge 없이 직접 선택한 횟수: 0회
- 로컬 라우터가 자신 없어 Judge로 되돌린 횟수: 0회
- Judge 신뢰도 평균 / P95: 0.913 / 0.950
- 로컬 takeover 최소 신뢰도: 0.78
- 최종 저장 정책 학습 모드: judge_first
- 최종 저장 학습 표본: Judge 24건 / 운영 완료 24건
- 학습 artifact: multilingual_e5_task_requirements_ordinal_v3 (intfloat/multilingual-e5-base)
- artifact 학습 예시 수: 24건 / artifact 모델 label: ['gpt-4.1-mini', 'gpt-4o-mini', 'gpt-5-mini', 'gpt-5.4-mini']

이 수치는 로컬 모델이 단순히 label을 저장했는지뿐 아니라, 실제로 충분한 자신감을 얻어 Judge 호출을 대신했는지를 보여 줍니다. takeover가 낮으면 현재 학습 구조가 비용 절감에는 불리하다는 뜻이며, 이 경우 Judge-first를 제품의 장기 기본값으로 두면 안 됩니다.

## 실험 평가 비용

- 독립 품질 Judge 총비용: $0.249250
- 독립 품질 Judge 총호출: 30회
- 이 비용은 2개 방식의 결과를 공정하게 비교하기 위한 측정 비용이며 제품 운영비 비교에는 포함하지 않았습니다.

## 요청별 결과

| # | 주제 | 예상 난이도 | 자동 선택 모델 | 자동 품질 | 자동 총비용 | 고가 품질 | 고가 비용 |
| ---: | --- | --- | --- | ---: | ---: | ---: | ---: |
| 1 | holdout_product_help | economy | gpt-4o-mini | 41.0 | $0.002045 | 91.0 | $0.011830 |
| 2 | holdout_access | balanced | gpt-4.1 | 74.0 | $0.005284 | 92.0 | $0.016640 |
| 3 | holdout_finance | balanced | gpt-5-mini | 82.0 | $0.004123 | 91.0 | $0.024005 |
| 4 | holdout_access | advanced | gpt-4.1 | 76.0 | $0.005776 | 94.0 | $0.035825 |
| 5 | holdout_reliability | advanced | gpt-5-mini | 86.0 | $0.004112 | 95.0 | $0.029290 |
| 6 | holdout_governance | balanced | gpt-5-mini | 84.0 | $0.004826 | 93.0 | $0.038600 |
| 7 | holdout_access | balanced | gpt-5.4-mini | 82.0 | $0.003751 | 95.0 | $0.019970 |
| 8 | holdout_finance | economy | gpt-4o-mini | 34.0 | $0.001998 | 91.0 | $0.011200 |
| 9 | holdout_security | balanced | gpt-4.1 | 62.0 | $0.005383 | 96.0 | $0.024155 |
| 10 | holdout_finance | advanced | gpt-5-mini | 84.0 | $0.004163 | 93.0 | $0.032610 |
| 11 | holdout_access | balanced | gpt-4.1 | 78.0 | $0.004780 | 94.0 | $0.018345 |
| 12 | holdout_finance | advanced | gpt-4.1 | 90.0 | $0.006022 | 0.0 | $0.000000 |
| 13 | holdout_security | advanced | gpt-4.1 | 71.0 | $0.005974 | 95.0 | $0.034455 |
| 14 | holdout_reliability | balanced | gpt-5-mini | 78.0 | $0.004588 | 94.0 | $0.034650 |
| 15 | holdout_product_help | economy | gpt-4o-mini | 54.0 | $0.001865 | 92.0 | $0.015535 |
| 16 | holdout_product_help | economy | gpt-4o-mini | 41.0 | $0.001946 | 92.0 | $0.011455 |
| 17 | holdout_data | advanced | gpt-4.1 | 90.0 | $0.005319 | 0.0 | $0.000000 |
| 18 | holdout_product_help | economy | gpt-4o-mini | 42.0 | $0.001766 | 94.0 | $0.011745 |
| 19 | holdout_security | advanced | gpt-4.1 | 66.0 | $0.006402 | 96.0 | $0.044465 |
| 20 | holdout_data | balanced | gpt-4o-mini | 68.0 | $0.001899 | 95.0 | $0.023845 |
| 21 | holdout_builder_help | economy | gpt-4o-mini | 38.0 | $0.001650 | 94.0 | $0.010060 |
| 22 | holdout_cost_help | economy | gpt-4o-mini | 48.0 | $0.002443 | 92.0 | $0.014115 |
| 23 | holdout_knowledge_help | economy | gpt-4o-mini | 68.0 | $0.002651 | 90.0 | $0.011650 |
| 24 | holdout_customer_operations | balanced | gpt-5-mini | 78.0 | $0.003611 | 93.0 | $0.025005 |
| 25 | holdout_release_operations | balanced | gpt-5-mini | 84.0 | $0.004231 | 94.0 | $0.033905 |
| 26 | holdout_usage_reconciliation | balanced | gpt-5-mini | 74.0 | $0.003960 | 91.0 | $0.027425 |
| 27 | holdout_payment_incident | advanced | gpt-4.1 | 68.0 | $0.005854 | 94.0 | $0.036425 |
| 28 | holdout_privacy_incident | advanced | gpt-4.1 | 84.0 | $0.006288 | 97.0 | $0.031815 |
| 29 | holdout_legal_retention | advanced | gpt-4.1 | 78.0 | $0.005416 | 94.0 | $0.033375 |
| 30 | holdout_multi_region_incident | advanced | gpt-5-mini | 90.0 | $0.004609 | 96.0 | $0.029775 |

## 해석 시 주의점

- 품질 점수는 독립 Judge가 동일한 계약으로 평가한 상대 지표입니다. 실제 고객 만족도나 사람 검수 결과를 완전히 대체하지는 않습니다.
- 자동 라우팅의 전체 시간에는 Judge 호출 시간이 포함됩니다. 처리 모델 시간만 보면 절감돼도 전체 시간은 늘어날 수 있습니다.
- JSON 계약 실패와 workflow 실패는 실행 품질 지표에 포함합니다. 품질 Judge 시스템 오류는 최대 3회 재시도하고, 끝내 실패하면 품질 평균에서 제외한 뒤 실패 건수를 별도로 표시합니다.
