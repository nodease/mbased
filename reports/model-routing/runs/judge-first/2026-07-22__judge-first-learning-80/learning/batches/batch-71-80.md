# Judge-first 자동 모델 라우팅 80회 학습 검증 보고서

## 한눈에 보는 결론

이 단계는 자동 라우팅만 80회 실행해 첫 50건으로 학습하고 다음 50건으로 일반화 성능과 학습 버전 발행 여부를 확인합니다.

- 자동 라우팅 총 제품 비용: $0.697263
- 고가 고정 비교: 이번 실행에서 제외
- 중간 고정 비교: 이번 실행에서 제외
- 저가 고정 비교: 이번 실행에서 제외
- 자동 라우팅에서 Runtime Judge가 실제 호출된 횟수: 80/80

자동 라우팅 비용에는 요청 처리 모델 비용과 Runtime Judge 비용을 모두 포함했습니다. 실험의 품질 평가 Judge 비용은 제품 기능의 런타임 비용이 아니므로 별도로 표시합니다.

## 실험 조건

- 실행 시각: 2026-07-22T04:15:07.698623+00:00
- workflow: `98000000-0000-0000-0000-000000000002` / LLM node: `llm-triage`
- 실행 방식: 실제 WorkflowEngine, 실제 OpenAI provider 호출, 실제 배포 run/node run/usage log 기록
- 자동 라우팅 전략: `judge_bootstrap_incremental_v1`
- 자동 라우팅 후보: gpt-4o-mini, gpt-4.1-mini, gpt-4.1, gpt-4o, gpt-5-mini, gpt-5.4-mini, gpt-5.4, gpt-5.6-sol, gpt-5.6-luna, gpt-5.6-terra, o3
- 고정 비교 모델: 
- 라우팅 Judge: `gpt-5.4-mini`
- 독립 품질 평가 Judge: 학습 단계에서는 호출하지 않음
- RAG: 미사용. 이번 비교에서는 KB 검색 품질 변수를 빼고 모델 라우팅 자체의 비용·속도·출력 품질만 측정했습니다.
- 학습 상태: 80회 동안 Judge label을 누적하고 local router 전환 가능성을 측정했습니다.

## 데이터셋

이 단계의 80개 요청은 여러 업무 유형과 난이도를 섞었고, 동일 문장을 반복하지 않았습니다.

| 예상 난이도 | 건수 |
| --- | ---: |
| advanced | 40 |
| balanced | 22 |
| economy | 18 |

## 비용·속도·품질 비교

| 방식 | 처리 모델 비용 | 라우팅 Judge 비용 | 총 제품 비용 | 평균 LLM 노드 시간 | 평균 전체 시간 | 평균 품질 점수 | JSON 계약 통과 | 품질 통과 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 자동 라우팅 | $0.539545 | $0.157718 | $0.697263 | 10781ms | 11017ms | 미평가 | 91.2% | - |

## 자동 라우팅이 실제로 고른 모델

- `gpt-4.1`: 2회
- `gpt-4.1-mini`: 33회
- `gpt-5-mini`: 4회
- `gpt-5.4-mini`: 22회
- `gpt-5.6-sol`: 19회
- 사전 가설 기준 적중: 65/80건
- 성능 부족 선택: 10건 / 과도한 선택: 5건 / 미분류: 0건

## 로컬 라우터 학습 수준

- Judge 학습 label 누적: 80건
- 실제 선택 모델 종류: 5개 ({'gpt-4.1-mini': 33, 'gpt-5.6-sol': 19, 'gpt-5-mini': 4, 'gpt-5.4-mini': 22, 'gpt-4.1': 2})
- 처리 출처 분포: {'runtime_judge': 80}
- 로컬 라우터가 Judge 없이 직접 선택한 횟수: 0회
- 로컬 라우터가 자신 없어 Judge로 되돌린 횟수: 0회
- Judge 신뢰도 평균 / P95: 0.906 / 0.950
- 로컬 takeover 최소 신뢰도: 0.78
- 최종 저장 정책 학습 모드: judge_first
- 최종 저장 학습 표본: Judge 59건 / 운영 완료 59건
- 학습 artifact: multilingual_e5_task_requirements_ordinal_v3 (intfloat/multilingual-e5-base)
- artifact 학습 예시 수: 59건 / artifact 모델 label: ['gpt-4.1-mini', 'gpt-5-mini', 'gpt-5.4-mini', 'gpt-5.6-sol']

이 수치는 로컬 모델이 단순히 label을 저장했는지뿐 아니라, 실제로 충분한 자신감을 얻어 Judge 호출을 대신했는지를 보여 줍니다. takeover가 낮으면 현재 학습 구조가 비용 절감에는 불리하다는 뜻이며, 이 경우 Judge-first를 제품의 장기 기본값으로 두면 안 됩니다.

## 실험 평가 비용

- 독립 품질 Judge 총비용: $0.000000
- 독립 품질 Judge 총호출: 0회
- 학습 단계에는 품질 평가 비용이 발생하지 않습니다.

## 요청별 결과

| # | 주제 | 예상 난이도 | 자동 선택 모델 | 자동 품질 | 자동 총비용 |
| ---: | --- | --- | --- | ---: | ---: |
| 1 | refund_intent | economy | gpt-4.1-mini | 미평가 | $0.002103 |
| 2 | refund_intent | advanced | gpt-5.6-sol | 미평가 | $0.016806 |
| 3 | formal_policy_reasoning | advanced | gpt-5.6-sol | 미평가 | $0.034438 |
| 4 | security_privacy_incident | advanced | gpt-5.6-sol | 미평가 | $0.030171 |
| 5 | analytics_reporting | advanced | gpt-4.1-mini | 미평가 | $0.002762 |
| 6 | customer_operations | balanced | gpt-4.1-mini | 미평가 | $0.002452 |
| 7 | release_operations | economy | gpt-4.1-mini | 미평가 | $0.002408 |
| 8 | routine_usage_guidance | economy | gpt-4.1-mini | 미평가 | $0.002750 |
| 9 | concurrency_code_review | advanced | gpt-5-mini | 미평가 | $0.004957 |
| 10 | routine_usage_guidance | economy | gpt-4.1-mini | 미평가 | $0.002496 |
| 11 | integration_support | balanced | gpt-4.1-mini | 미평가 | $0.002510 |
| 12 | service_reliability | advanced | gpt-5.4-mini | 미평가 | $0.005846 |
| 13 | data_reconciliation | balanced | gpt-4.1-mini | 미평가 | $0.002660 |
| 14 | routine_usage_guidance | economy | gpt-4.1-mini | 미평가 | $0.002317 |
| 15 | account_access_request | balanced | gpt-5.6-sol | 미평가 | $0.016909 |
| 16 | service_reliability | balanced | gpt-5.4-mini | 미평가 | $0.004422 |
| 17 | risk_triage | advanced | gpt-5.6-sol | 미평가 | $0.031031 |
| 18 | routine_usage_guidance | economy | gpt-4.1-mini | 미평가 | $0.002436 |
| 19 | finance_closing_approval | advanced | gpt-5.4-mini | 미평가 | $0.004286 |
| 20 | security_privacy_incident | advanced | gpt-5.6-sol | 미평가 | $0.031110 |
| 21 | account_access_request | balanced | gpt-5.6-sol | 미평가 | $0.019235 |
| 22 | routine_usage_guidance | economy | gpt-4.1-mini | 미평가 | $0.002368 |
| 23 | finance_closing_approval | advanced | gpt-4.1-mini | 미평가 | $0.002793 |
| 24 | data_operations | balanced | gpt-4.1-mini | 미평가 | $0.002346 |
| 25 | customer_operations | advanced | gpt-5.4-mini | 미평가 | $0.004470 |
| 26 | billing_operations | advanced | gpt-5.4-mini | 미평가 | $0.005439 |
| 27 | routine_usage_guidance | economy | gpt-4.1-mini | 미평가 | $0.002650 |
| 28 | security_privacy_incident | advanced | gpt-5.6-sol | 미평가 | $0.028351 |
| 29 | account_access_request | balanced | gpt-5.6-sol | 미평가 | $0.016705 |
| 30 | identity_operations | economy | gpt-4.1-mini | 미평가 | $0.002262 |
| 31 | contract_compliance | advanced | gpt-5.4-mini | 미평가 | $0.005031 |
| 32 | finance_closing_approval | advanced | gpt-4.1-mini | 미평가 | $0.002212 |
| 33 | risk_triage | advanced | gpt-5-mini | 미평가 | $0.004510 |
| 34 | security_privacy_incident | advanced | gpt-5.6-sol | 미평가 | $0.025710 |
| 35 | compliance_operations | advanced | gpt-5.6-sol | 미평가 | $0.029230 |
| 36 | risk_triage | advanced | gpt-5.6-sol | 미평가 | $0.035943 |
| 37 | service_reliability | balanced | gpt-5.4-mini | 미평가 | $0.004616 |
| 38 | incident_response | advanced | gpt-5-mini | 미평가 | $0.005185 |
| 39 | data_governance | advanced | gpt-5.6-sol | 미평가 | $0.033658 |
| 40 | account_access_request | balanced | gpt-5.4-mini | 미평가 | $0.004002 |
| 41 | finance_closing_approval | advanced | gpt-5.4-mini | 미평가 | $0.004447 |
| 42 | data_operations | economy | gpt-4.1-mini | 미평가 | $0.002650 |
| 43 | integration_support | advanced | gpt-5.4-mini | 미평가 | $0.004174 |
| 44 | security_privacy_incident | advanced | gpt-5.6-sol | 미평가 | $0.023413 |
| 45 | incident_response | balanced | gpt-4.1-mini | 미평가 | $0.002283 |
| 46 | data_governance | balanced | gpt-4.1-mini | 미평가 | $0.002533 |
| 47 | incident_response | economy | gpt-4.1-mini | 미평가 | $0.002336 |
| 48 | routine_usage_guidance | economy | gpt-4.1-mini | 미평가 | $0.002356 |
| 49 | analytics_reporting | balanced | gpt-4.1-mini | 미평가 | $0.002346 |
| 50 | finance_closing_approval | advanced | gpt-4.1-mini | 미평가 | $0.002389 |
| 51 | routine_usage_guidance | economy | gpt-5-mini | 미평가 | $0.003509 |
| 52 | security_privacy_incident | advanced | gpt-5.6-sol | 미평가 | $0.026615 |
| 53 | finance_closing_approval | advanced | gpt-5.4-mini | 미평가 | $0.004812 |
| 54 | product_guidance | economy | gpt-4.1-mini | 미평가 | $0.002113 |
| 55 | routine_usage_guidance | economy | gpt-4.1-mini | 미평가 | $0.002199 |
| 56 | contract_compliance | advanced | gpt-5.4-mini | 미평가 | $0.003275 |
| 57 | finance_closing_approval | advanced | gpt-5.4-mini | 미평가 | $0.003790 |
| 58 | routine_usage_guidance | economy | gpt-4.1-mini | 미평가 | $0.002235 |
| 59 | identity_operations | balanced | gpt-5.4-mini | 미평가 | $0.004261 |
| 60 | account_access_request | balanced | gpt-5.6-sol | 미평가 | $0.020428 |
| 61 | analytics_reporting | balanced | gpt-4.1-mini | 미평가 | $0.002210 |
| 62 | finance_closing_approval | advanced | gpt-5.4-mini | 미평가 | $0.004555 |
| 63 | account_access_request | balanced | gpt-5.4-mini | 미평가 | $0.004706 |
| 64 | billing_operations | balanced | gpt-4.1-mini | 미평가 | $0.002532 |
| 65 | contract_compliance | advanced | gpt-5.4-mini | 미평가 | $0.005161 |
| 66 | security_privacy_incident | advanced | gpt-5.6-sol | 미평가 | $0.023633 |
| 67 | release_operations | advanced | gpt-5.4-mini | 미평가 | $0.005916 |
| 68 | security_privacy_incident | advanced | gpt-5.6-sol | 미평가 | $0.024196 |
| 69 | account_access_request | balanced | gpt-5.4-mini | 미평가 | $0.004030 |
| 70 | account_access_request | balanced | gpt-5.6-sol | 미평가 | $0.015919 |
| 71 | customer_operations | economy | gpt-4.1-mini | 미평가 | $0.002035 |
| 72 | finance_closing_approval | advanced | gpt-5.4-mini | 미평가 | $0.004493 |
| 73 | finance_closing_approval | advanced | gpt-5.4-mini | 미평가 | $0.004287 |
| 74 | algorithmic_reasoning | advanced | gpt-4.1 | 미평가 | $0.008209 |
| 75 | security_privacy_incident | advanced | gpt-4.1-mini | 미평가 | $0.002291 |
| 76 | analytics_reporting | advanced | gpt-4.1 | 미평가 | $0.006116 |
| 77 | routine_usage_guidance | economy | gpt-4.1-mini | 미평가 | $0.002569 |
| 78 | data_operations | advanced | gpt-5.4-mini | 미평가 | $0.005920 |
| 79 | contract_compliance | balanced | gpt-4.1-mini | 미평가 | $0.001970 |
| 80 | account_access_request | balanced | gpt-4.1-mini | 미평가 | $0.002765 |

## 해석 시 주의점

- 품질 점수는 독립 Judge가 동일한 계약으로 평가한 상대 지표입니다. 실제 고객 만족도나 사람 검수 결과를 완전히 대체하지는 않습니다.
- 자동 라우팅의 전체 시간에는 Judge 호출 시간이 포함됩니다. 처리 모델 시간만 보면 절감돼도 전체 시간은 늘어날 수 있습니다.
- JSON 계약 실패와 workflow 실패는 실행 품질 지표에 포함합니다. 품질 Judge 시스템 오류는 최대 3회 재시도하고, 끝내 실패하면 품질 평균에서 제외한 뒤 실패 건수를 별도로 표시합니다.
