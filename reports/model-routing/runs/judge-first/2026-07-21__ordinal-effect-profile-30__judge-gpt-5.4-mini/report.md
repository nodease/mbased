# Judge-first 자동 모델 라우팅 30회 경제성 실험 보고서

## 한눈에 보는 결론

이 실험은 같은 기업 요청 처리 워크플로우를 30개의 서로 다른 요청으로 실행해, 새 요구 수준 판정과 자동 모델 선택이 실제 provider 호출에서 끝까지 동작하는지 확인한 결과입니다. 이번 실행은 자동 라우팅만 실행했으므로 고정 모델 대비 경제성 우위는 판정하지 않습니다.

- 자동 라우팅 총 제품 비용: $0.103351
- 고가 고정 비교: 이번 실행에서 제외
- 중간 고정 비교: 이번 실행에서 제외
- 저가 고정 비교: 이번 실행에서 제외
- 자동 라우팅에서 Runtime Judge가 실제 호출된 횟수: 30/30

핵심 판정은 다음과 같습니다.

- 30건 모두 workflow와 JSON schema 검사를 통과했습니다.
- 실제 실행 모델은 3종으로 분산됐지만, 25건은 `gpt-5.4-mini` 계열에 집중됐습니다.
- 독립 품질 Judge 기준 평균 점수는 82.4점이고 계약 통과는 27/30건입니다.
- DB에는 learner와 학습 label이 생성되지 않아, 이번 실험은 로컬 라우터 학습이나 전환을 검증하지 못했습니다.
- 고정 고가·중간·저가 arm을 실행하지 않았으므로 비용 절감률을 주장할 수 없습니다.

자동 라우팅 비용에는 요청 처리 모델 비용과 Runtime Judge 비용을 모두 포함했습니다. 실험의 품질 평가 Judge 비용은 제품 기능의 런타임 비용이 아니므로 별도로 표시합니다.

## 실험 조건

- 실행 시각: 2026-07-21T13:53:40.527248+00:00
- workflow: `98000000-0000-0000-0000-000000000002` / LLM node: `llm-triage`
- 실행 방식: 실제 WorkflowEngine, 실제 OpenAI provider 호출, 실제 배포 run/node run/usage log 기록
- 자동 라우팅 전략: `judge_bootstrap_incremental_v1`
- 자동 라우팅 후보: gpt-4o-mini, gpt-4.1-mini, gpt-4.1, gpt-4o, gpt-5-mini, gpt-5.4-mini, gpt-5.4, gpt-5.6-sol, gpt-5.6-luna, gpt-5.6-terra, o3
- 고정 비교 모델: 미실행
- 라우팅 Judge: `gpt-5.4-mini`
- 독립 품질 평가 Judge: `gpt-5-mini`
- RAG: 미사용. 이번 비교에서는 KB 검색 품질 변수를 빼고 모델 라우팅 자체의 비용·속도·출력 품질만 측정했습니다.
- 정책 refresh: 100회. 30회 실험 동안 정책 교체를 막고, Judge label을 누적한 local router의 전환만 측정했습니다.

## 데이터셋

80개 고정 데이터셋에서 30개를 선택했습니다. 고객 사용 안내, 계정·접근 권한, 재무 결산·승인, 보안·개인정보 사고, 장애·신뢰성, 분석 보고, 연동 지원, 위험 분류가 섞여 있습니다. 동일 문장을 반복하지 않았고 실행 순서는 고정 난수로 섞어 특정 범주가 초반에 몰리지 않게 했습니다.

| 예상 난이도 | 건수 |
| --- | ---: |
| advanced | 14 |
| balanced | 7 |
| economy | 9 |

## 비용·속도·품질 비교

| 방식 | 처리 모델 비용 | 라우팅 Judge 비용 | 총 제품 비용 | 평균 LLM 노드 시간 | 평균 전체 시간 | 평균 품질 점수 | JSON 계약 통과 | 품질 통과 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 자동 라우팅 | $0.066688 | $0.036663 | $0.103351 | 14420ms | 14612ms | 82.4 | 100.0% | 90.0% |

## 자동 라우팅이 실제로 고른 모델

- `gpt-5-mini-[REDACTED]`: 2회
- `gpt-5-nano-[REDACTED]`: 3회
- `gpt-5.4-mini-[REDACTED]`: 25회
- 사전 가설 기준 적중: 측정 불가
- 이유: trace 보안 정규화가 날짜 버전 suffix를 `[REDACTED]`로 바꿔, 기존 benchmark model ID 표와 일치하지 않았습니다. `미분류 30건`은 라우팅 실패 30건이라는 뜻이 아닙니다.

## 3축 요구 수준 판정

Runtime Judge가 요청마다 작업 복잡도, 판단 영향도, 근거 종합도를 0~3으로 판정한 분포입니다.

| 축 | 0점 | 1점 | 2점 | 3점 |
| --- | ---: | ---: | ---: | ---: |
| 작업 복잡도 | 0 | 8 | 16 | 6 |
| 판단 영향도 | 1 | 10 | 9 | 10 |
| 근거 종합도 | 2 | 6 | 22 | 0 |

| 사전 난이도 | 건수 | 작업 복잡도 평균 | 판단 영향도 평균 | 근거 종합도 평균 |
| --- | ---: | ---: | ---: | ---: |
| advanced | 14 | 2.36 | 2.64 | 2.00 |
| balanced | 7 | 2.00 | 1.86 | 1.86 |
| economy | 9 | 1.22 | 0.89 | 1.00 |

사전 난이도가 높아질수록 세 축 평균도 함께 높아졌습니다. 특히 이번에 구조적 실행 효과를 별도로 넣은 판단 영향도는 economy 0.89, balanced 1.86, advanced 2.64로 구분됐습니다. 다만 이 수치만으로 사람 정답과의 정확성을 증명할 수는 없고, 사람이 라벨링한 holdout 정답과 추가 비교가 필요합니다.

## 로컬 라우터 학습 수준

- 실행 trace에서 관찰한 Judge 판정: 30건
- 실제 선택 모델 종류: 3개 ({'gpt-5.4-mini-[REDACTED]': 25, 'gpt-5-nano-[REDACTED]': 3, 'gpt-5-mini-[REDACTED]': 2})
- 처리 출처 분포: {'runtime_judge': 30}
- 로컬 라우터가 Judge 없이 직접 선택한 횟수: 0회
- 로컬 라우터가 자신 없어 Judge로 되돌린 횟수: 0회
- Judge 신뢰도 평균 / P95: 0.919 / 0.970
- 로컬 takeover 최소 신뢰도: 0.78
- 최종 저장 정책 학습 모드: judge_first
- 최종 저장 학습 표본: Judge 0건 / 운영 완료 0건
- 학습 artifact: 없음 (-)
- artifact 학습 예시 수: 0건 / artifact 모델 label: []

DB를 직접 확인한 결과도 learner 0건, 학습 label 0건입니다. 실험용 정책에 `learner_id`가 연결되지 않았고, trace의 `included_in_routing_learning`도 전부 `false`였습니다. 또한 23건은 `requirement_adjudication_required` 때문에 학습 대상에서 명시적으로 제외됐습니다.

따라서 이번 30건으로 확인된 것은 Runtime Judge 기반 모델 선택까지입니다. 새 128차원 E5 + 9개 순서형 출력 학습, learner version 발행, local-first 전환은 확인되지 않았습니다. 실제 제품 경로의 문제인지 실험 fixture가 새 learner 생성 경로를 우회한 것인지는 별도 회귀 테스트가 필요합니다.

이 수치는 로컬 모델이 단순히 label을 저장했는지뿐 아니라, 실제로 충분한 자신감을 얻어 Judge 호출을 대신했는지를 보여 줍니다. takeover가 낮으면 현재 학습 구조가 비용 절감에는 불리하다는 뜻이며, 이 경우 Judge-first를 제품의 장기 기본값으로 두면 안 됩니다.

## 실험 평가 비용

- 독립 품질 Judge 총비용: $0.019238
- 독립 품질 Judge 총호출: 30회
- Runtime Judge는 제품 비용의 35.5%인 $0.036663을 사용했습니다.
- 처리 모델과 Runtime Judge를 합친 제품 비용은 $0.103351입니다.
- 독립 품질 평가까지 포함한 전체 실험 비용은 약 $0.122589입니다.
- 독립 품질 Judge 비용은 측정 비용이며 제품 운영비에는 포함하지 않았습니다.

## 실패와 fallback 관찰

- 최종 workflow 성공률: 100%
- JSON schema 통과율: 100%
- 독립 품질 계약 통과율: 90%
- 처리 모델 평균 시간: 14.4초, P95: 60.8초
- provider의 `responses_incomplete`가 5번 발생했고 fallback이 실행됐습니다. 최종 workflow는 모두 성공했지만, 현재 보고서 데이터에는 어느 요청이 fallback됐는지 구조화된 필드가 없어 개별 run과 정확히 연결하지 못했습니다.
- 품질 계약 미통과 3건 중 2건은 품질 Judge 응답의 해당 output row가 비어 0점 처리됐습니다. 나머지 1건은 82점이지만 계약 미통과로 판정됐습니다. 따라서 90%는 전부 실제 task 품질 실패라고 해석하면 안 됩니다.

## 최종 평가

1. **실행 가능성: 통과.** 30개 실제 입력이 모두 workflow 완료와 JSON schema 통과까지 도달했습니다.
2. **요구 수준 분리: 1차 확인.** 사전 난이도가 높을수록 3축 평균이 높았고 판단 영향도도 구간별 차이를 보였습니다.
3. **모델 다양성: 부분 통과.** 3개 모델이 선택됐지만 83.3%가 `gpt-5.4-mini` 계열에 몰렸습니다.
4. **품질 안정성: 주의 필요.** 평균 82.4점, 계약 통과 90%였고 provider fallback 때문에 P95 지연이 60.8초까지 늘었습니다.
5. **경제성: 판정 불가.** 고정 모델 arm을 실행하지 않아 절감률 비교 기준이 없습니다.
6. **로컬 학습: 미검증.** learner와 학습 label이 0건이므로 현재 결과로는 E5 순서형 로컬 라우터가 학습된다고 말할 수 없습니다.

## 요청별 결과

| # | 주제 | 예상 난이도 | 자동 선택 모델 | 자동 품질 | 자동 총비용 |
| ---: | --- | --- | --- | ---: | ---: |
| 1 | security_privacy_incident | advanced | gpt-5.4-mini-[REDACTED] | 92.0 | $0.003241 |
| 2 | integration_support | balanced | gpt-5-nano-[REDACTED] | 85.0 | $0.004392 |
| 3 | security_privacy_incident | advanced | gpt-5.4-mini-[REDACTED] | 88.0 | $0.003392 |
| 4 | routine_usage_guidance | economy | gpt-5.4-mini-[REDACTED] | 82.0 | $0.002449 |
| 5 | concurrency_code_review | advanced | gpt-5-mini-[REDACTED] | 92.0 | $0.005851 |
| 6 | account_access_request | balanced | gpt-5.4-mini-[REDACTED] | 92.0 | $0.003194 |
| 7 | integration_support | advanced | gpt-5.4-mini-[REDACTED] | 88.0 | $0.004204 |
| 8 | product_guidance | economy | gpt-5.4-mini-[REDACTED] | 90.0 | $0.003536 |
| 9 | security_privacy_incident | advanced | gpt-5.4-mini-[REDACTED] | 92.0 | $0.002968 |
| 10 | routine_usage_guidance | economy | gpt-5.4-mini-[REDACTED] | 0.0 | $0.002434 |
| 11 | security_privacy_incident | advanced | gpt-5.4-mini-[REDACTED] | 88.0 | $0.003442 |
| 12 | product_guidance | economy | gpt-5-nano-[REDACTED] | 86.0 | $0.003926 |
| 13 | account_access_request | balanced | gpt-5.4-mini-[REDACTED] | 88.0 | $0.002669 |
| 14 | integration_support | advanced | gpt-5.4-mini-[REDACTED] | 92.0 | $0.004405 |
| 15 | risk_triage | advanced | gpt-5.4-mini-[REDACTED] | 88.0 | $0.003528 |
| 16 | analytics_reporting | advanced | gpt-5.4-mini-[REDACTED] | 92.0 | $0.003890 |
| 17 | account_access_request | balanced | gpt-5.4-mini-[REDACTED] | 86.0 | $0.003121 |
| 18 | finance_closing_approval | advanced | gpt-5.4-mini-[REDACTED] | 0.0 | $0.003367 |
| 19 | service_reliability | balanced | gpt-5-mini-[REDACTED] | 86.0 | $0.004766 |
| 20 | security_privacy_incident | advanced | gpt-5.4-mini-[REDACTED] | 92.0 | $0.002923 |
| 21 | account_access_request | balanced | gpt-5.4-mini-[REDACTED] | 90.0 | $0.003168 |
| 22 | routine_usage_guidance | economy | gpt-5.4-mini-[REDACTED] | 82.0 | $0.003226 |
| 23 | product_guidance | economy | gpt-5.4-mini-[REDACTED] | 92.0 | $0.002508 |
| 24 | risk_triage | advanced | gpt-5.4-mini-[REDACTED] | 92.0 | $0.004328 |
| 25 | security_privacy_incident | advanced | gpt-5.4-mini-[REDACTED] | 88.0 | $0.003436 |
| 26 | routine_usage_guidance | economy | gpt-5.4-mini-[REDACTED] | 86.0 | $0.002814 |
| 27 | routine_usage_guidance | economy | gpt-5.4-mini-[REDACTED] | 86.0 | $0.002687 |
| 28 | finance_closing_approval | advanced | gpt-5.4-mini-[REDACTED] | 86.0 | $0.002932 |
| 29 | routine_usage_guidance | economy | gpt-5-nano-[REDACTED] | 86.0 | $0.003965 |
| 30 | account_access_request | balanced | gpt-5.4-mini-[REDACTED] | 85.0 | $0.002589 |

## 해석 시 주의점

- 품질 점수는 독립 Judge가 동일한 계약으로 평가한 상대 지표입니다. 실제 고객 만족도나 사람 검수 결과를 완전히 대체하지는 않습니다.
- 자동 라우팅의 전체 시간에는 Judge 호출 시간이 포함됩니다. 처리 모델 시간만 보면 절감돼도 전체 시간은 늘어날 수 있습니다.
- JSON 계약 실패와 workflow 실패는 실행 품질 지표에 포함합니다. 품질 Judge 시스템 오류는 최대 3회 재시도하고, 끝내 실패하면 품질 평균에서 제외한 뒤 실패 건수를 별도로 표시합니다.
