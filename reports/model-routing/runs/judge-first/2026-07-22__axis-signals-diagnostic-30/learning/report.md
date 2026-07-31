# Judge-first 자동 모델 라우팅 30회 학습 검증 보고서

## 한눈에 보는 결론

이 단계는 자동 라우팅만 30회 실행해 첫 50건으로 학습하고 다음 50건으로 일반화 성능과 학습 버전 발행 여부를 확인합니다.

- 자동 라우팅 총 제품 비용: $0.000000
- 고가 고정 비교: 이번 실행에서 제외
- 중간 고정 비교: 이번 실행에서 제외
- 저가 고정 비교: 이번 실행에서 제외
- 자동 라우팅에서 Runtime Judge가 실제 호출된 횟수: 0/30

자동 라우팅 비용에는 요청 처리 모델 비용과 Runtime Judge 비용을 모두 포함했습니다. 실험의 품질 평가 Judge 비용은 제품 기능의 런타임 비용이 아니므로 별도로 표시합니다.

## 실험 조건

- 실행 시각: 2026-07-21T21:06:27.886772+00:00
- workflow: `98000000-0000-0000-0000-000000000002` / LLM node: `llm-triage`
- 실행 방식: 실제 WorkflowEngine, 실제 OpenAI provider 호출, 실제 배포 run/node run/usage log 기록
- 자동 라우팅 전략: `judge_bootstrap_incremental_v1`
- 자동 라우팅 후보: gpt-4o-mini, gpt-4.1-mini, gpt-4.1, gpt-4o, gpt-5-mini, gpt-5.4-mini, gpt-5.4, gpt-5.6-sol, gpt-5.6-luna, gpt-5.6-terra, o3
- 고정 비교 모델: 
- 라우팅 Judge: `gpt-5.4-mini`
- 독립 품질 평가 Judge: 학습 단계에서는 호출하지 않음
- RAG: 미사용. 이번 비교에서는 KB 검색 품질 변수를 빼고 모델 라우팅 자체의 비용·속도·출력 품질만 측정했습니다.
- 정책 refresh: 100회. 30회 실험 동안 정책 교체를 막고, Judge label을 누적한 local router의 전환만 측정했습니다.

## 데이터셋

이 단계의 30개 요청은 여러 업무 유형과 난이도를 섞었고, 동일 문장을 반복하지 않았습니다.

| 예상 난이도 | 건수 |
| --- | ---: |
| advanced | 13 |
| balanced | 8 |
| economy | 9 |

## 비용·속도·품질 비교

| 방식 | 처리 모델 비용 | 라우팅 Judge 비용 | 총 제품 비용 | 평균 LLM 노드 시간 | 평균 전체 시간 | 평균 품질 점수 | JSON 계약 통과 | 품질 통과 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 자동 라우팅 | $0.000000 | $0.000000 | $0.000000 | 185ms | 239ms | 미평가 | 0.0% | - |

## 자동 라우팅이 실제로 고른 모델

- `None`: 30회
- 사전 가설 기준 적중: 0/30건
- 성능 부족 선택: 0건 / 과도한 선택: 0건 / 미분류: 30건

## 로컬 라우터 학습 수준

- Judge label 누적: 0건
- 실제 선택 모델 종류: 1개 ({'None': 30})
- 처리 출처 분포: {'unknown': 30}
- 로컬 라우터가 Judge 없이 직접 선택한 횟수: 0회
- 로컬 라우터가 자신 없어 Judge로 되돌린 횟수: 0회
- Judge 신뢰도 평균 / P95: 0.000 / 0.000
- 로컬 takeover 최소 신뢰도: 0.78
- 최종 저장 정책 학습 모드: judge_first
- 최종 저장 학습 표본: Judge 0건 / 운영 완료 0건
- 학습 artifact: 없음 (-)
- artifact 학습 예시 수: 0건 / artifact 모델 label: []

이 수치는 로컬 모델이 단순히 label을 저장했는지뿐 아니라, 실제로 충분한 자신감을 얻어 Judge 호출을 대신했는지를 보여 줍니다. takeover가 낮으면 현재 학습 구조가 비용 절감에는 불리하다는 뜻이며, 이 경우 Judge-first를 제품의 장기 기본값으로 두면 안 됩니다.

## 실험 평가 비용

- 독립 품질 Judge 총비용: $0.000000
- 독립 품질 Judge 총호출: 0회
- 학습 단계에는 품질 평가 비용이 발생하지 않습니다.

## 요청별 결과

| # | 주제 | 예상 난이도 | 자동 선택 모델 | 자동 품질 | 자동 총비용 |
| ---: | --- | --- | --- | ---: | ---: |
| 1 | refund_intent | economy | None | 미평가 | $0.000000 |
| 2 | refund_intent | advanced | None | 미평가 | $0.000000 |
| 3 | formal_policy_reasoning | advanced | None | 미평가 | $0.000000 |
| 4 | security_privacy_incident | advanced | None | 미평가 | $0.000000 |
| 5 | analytics_reporting | advanced | None | 미평가 | $0.000000 |
| 6 | customer_operations | balanced | None | 미평가 | $0.000000 |
| 7 | release_operations | economy | None | 미평가 | $0.000000 |
| 8 | routine_usage_guidance | economy | None | 미평가 | $0.000000 |
| 9 | concurrency_code_review | advanced | None | 미평가 | $0.000000 |
| 10 | routine_usage_guidance | economy | None | 미평가 | $0.000000 |
| 11 | integration_support | balanced | None | 미평가 | $0.000000 |
| 12 | service_reliability | advanced | None | 미평가 | $0.000000 |
| 13 | data_reconciliation | balanced | None | 미평가 | $0.000000 |
| 14 | routine_usage_guidance | economy | None | 미평가 | $0.000000 |
| 15 | account_access_request | balanced | None | 미평가 | $0.000000 |
| 16 | service_reliability | balanced | None | 미평가 | $0.000000 |
| 17 | risk_triage | advanced | None | 미평가 | $0.000000 |
| 18 | routine_usage_guidance | economy | None | 미평가 | $0.000000 |
| 19 | finance_closing_approval | advanced | None | 미평가 | $0.000000 |
| 20 | security_privacy_incident | advanced | None | 미평가 | $0.000000 |
| 21 | account_access_request | balanced | None | 미평가 | $0.000000 |
| 22 | routine_usage_guidance | economy | None | 미평가 | $0.000000 |
| 23 | finance_closing_approval | advanced | None | 미평가 | $0.000000 |
| 24 | data_operations | balanced | None | 미평가 | $0.000000 |
| 25 | customer_operations | advanced | None | 미평가 | $0.000000 |
| 26 | billing_operations | advanced | None | 미평가 | $0.000000 |
| 27 | routine_usage_guidance | economy | None | 미평가 | $0.000000 |
| 28 | security_privacy_incident | advanced | None | 미평가 | $0.000000 |
| 29 | account_access_request | balanced | None | 미평가 | $0.000000 |
| 30 | identity_operations | economy | None | 미평가 | $0.000000 |

## 해석 시 주의점

- 품질 점수는 독립 Judge가 동일한 계약으로 평가한 상대 지표입니다. 실제 고객 만족도나 사람 검수 결과를 완전히 대체하지는 않습니다.
- 자동 라우팅의 전체 시간에는 Judge 호출 시간이 포함됩니다. 처리 모델 시간만 보면 절감돼도 전체 시간은 늘어날 수 있습니다.
- JSON 계약 실패와 workflow 실패는 실행 품질 지표에 포함합니다. 품질 Judge 시스템 오류는 최대 3회 재시도하고, 끝내 실패하면 품질 평균에서 제외한 뒤 실패 건수를 별도로 표시합니다.
