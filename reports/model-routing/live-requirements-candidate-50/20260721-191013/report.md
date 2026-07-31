# Judge-first 자동 모델 라우팅 실제 50건 보고서

## 결과

- 새 workflow: `b28ffb41-9146-4687-8f5e-6f2cf0d316ab`
- 새 deployment: `b9ac5384-be26-4886-9b82-08973857619b`
- bootstrap 상태: `ready`
- 성공: **43/50건**
- 선택된 모델 분포: `{'gpt-4o-mini-2024-07-18': 14, 'gpt-4.1-mini-2025-04-14': 7, 'gpt-5.1-2025-11-13': 1, 'o3-2025-04-16': 2, '확인 불가': 7, 'gpt-5.6-luna': 3, 'gpt-4.1-2025-04-14': 4, 'gpt-4o-2024-11-20': 1, 'gpt-5.6-terra': 10, 'gpt-5-2025-08-07': 1}`
- 라우팅 근거 분포: `{'strict_output_reliability': 18, 'multi_constraint': 22, 'multi_step_reasoning': 1, '확인 불가': 7, 'runtime_judge_unavailable': 1, 'broad_context_synthesis': 1}`
- runtime Judge 호출: **43/50건**
- 주 실행 노드 비용 합계 (Judge 제외): `$0.084221`
- Runtime Judge 비용 합계: `$0.157873`
- 실제 LLM 호출 비용 합계: `$0.242094`

이 실험은 새 workflow에서 자동 모델 라우팅을 켠 뒤, 실제 내부 배포 요청 50건을 실행한 결과다. 각 요청의 요구 수준은 Judge가 평가하고, 서버가 현재 실행 가능한 전체 후보 중 요구 수준에 맞는 모델을 선택한다.

## 판정

- **라우팅 자체는 동작했다.** 성공한 43건에서 9개 모델이 실제로 선택됐고, 단순 문의는 주로 `gpt-4o-mini`, 복합·고위험 성격의 문의는 주로 `gpt-5.6-terra` 또는 `o3`로 분산됐다.
- **경제성은 아직 통과로 볼 수 없다.** 주 실행 비용은 `$0.084221`이지만, 요청마다 실행된 Runtime Judge 비용이 `$0.157873`으로 더 컸다. 현재 구조 그대로라면 라우팅 판단 비용이 절감액을 상쇄할 수 있다.
- **안정성도 미완료다.** 7건은 Gateway/Nginx에서 `504`로 종료됐다. Worker 로그에서는 provider 호출 시 DNS 해석 실패(`Name or service not known`)가 확인됐다. 후보를 권한·모델 목록으로만 걸러서는 부족하고, 실제 연결 가능한 credential endpoint인지까지 검증해야 한다.
- 이 새 workflow에는 private Knowledge Base를 복제해 다시 연결할 권한이 없어 RAG 없이 실행됐다. 따라서 이 보고서는 **RAG가 없는 LLM 노드의 라우팅 검증**이며, RAG context 길이·검색 근거를 반영한 라우팅은 별도 검증이 필요하다.

## 실행별 안전 요약

| # | 입력군 | 모델 | 근거 | Judge | 상태 | 비용 | 시간 |
| ---: | --- | --- | --- | --- | --- | ---: | ---: |
| 1 | economy | gpt-4o-mini-2024-07-18 | strict_output_reliability | 호출 | success/success | $0.000078 | 6.599s |
| 2 | balanced | gpt-4.1-mini-2025-04-14 | strict_output_reliability | 호출 | success/success | $0.000296 | 6.444s |
| 3 | economy | gpt-4o-mini-2024-07-18 | multi_constraint | 호출 | success/success | $0.000083 | 6.132s |
| 4 | balanced | gpt-5.1-2025-11-13 | multi_step_reasoning | 호출 | success/success | $0.004590 | 12.723s |
| 5 | advanced | o3-2025-04-16 | multi_constraint | 호출 | success/success | $0.006194 | 10.247s |
| 6 | economy | - | deployment_http_504 | 미호출 | http_error/unknown | $0.000000 | 0.000s |
| 7 | balanced | gpt-4.1-mini-2025-04-14 | strict_output_reliability | 호출 | success/success | $0.000307 | 7.717s |
| 8 | balanced | gpt-5.6-luna | multi_constraint | 호출 | success/success | $0.001479 | 7.128s |
| 9 | economy | gpt-4o-mini-2024-07-18 | strict_output_reliability | 호출 | success/success | $0.000081 | 6.581s |
| 10 | economy | gpt-4o-mini-2024-07-18 | strict_output_reliability | 호출 | success/success | $0.000087 | 5.796s |
| 11 | advanced | gpt-4.1-2025-04-14 | runtime_judge_unavailable | 호출 | success/success | $0.002198 | 7.253s |
| 12 | balanced | gpt-4o-2024-11-20 | multi_constraint | 호출 | success/success | $0.001653 | 5.532s |
| 13 | economy | gpt-4o-mini-2024-07-18 | strict_output_reliability | 호출 | success/success | $0.000086 | 5.783s |
| 14 | balanced | gpt-4o-mini-2024-07-18 | strict_output_reliability | 호출 | success/success | $0.000093 | 6.250s |
| 15 | balanced | gpt-4.1-2025-04-14 | multi_constraint | 호출 | success/success | $0.001488 | 6.836s |
| 16 | advanced | o3-2025-04-16 | multi_constraint | 호출 | success/success | $0.006780 | 12.368s |
| 17 | balanced | gpt-4.1-mini-2025-04-14 | multi_constraint | 호출 | success/success | $0.000321 | 6.366s |
| 18 | economy | - | deployment_http_504 | 미호출 | http_error/unknown | $0.000000 | 0.000s |
| 19 | economy | gpt-4.1-mini-2025-04-14 | strict_output_reliability | 호출 | success/success | $0.000239 | 6.680s |
| 20 | balanced | gpt-5.6-terra | broad_context_synthesis | 호출 | success/success | $0.003455 | 7.730s |
| 21 | economy | gpt-4o-mini-2024-07-18 | strict_output_reliability | 호출 | success/success | $0.000090 | 6.944s |
| 22 | advanced | gpt-5.6-terra | multi_constraint | 호출 | success/success | $0.005618 | 8.844s |
| 23 | advanced | gpt-5-2025-08-07 | multi_constraint | 호출 | success/success | $0.000324 | 17.611s |
| 24 | advanced | - | deployment_http_504 | 미호출 | http_error/unknown | $0.000000 | 0.000s |
| 25 | advanced | - | deployment_http_504 | 미호출 | http_error/unknown | $0.000000 | 0.000s |
| 26 | balanced | gpt-5.6-terra | multi_constraint | 호출 | success/success | $0.005358 | 8.919s |
| 27 | advanced | gpt-5.6-terra | multi_constraint | 호출 | success/success | $0.006675 | 11.182s |
| 28 | balanced | gpt-4.1-2025-04-14 | multi_constraint | 호출 | success/success | $0.001624 | 6.848s |
| 29 | balanced | - | deployment_http_504 | 미호출 | http_error/unknown | $0.000000 | 0.000s |
| 30 | advanced | gpt-5.6-terra | multi_constraint | 호출 | success/success | $0.003800 | 7.692s |
| 31 | advanced | gpt-5.6-terra | multi_constraint | 호출 | success/success | $0.005275 | 8.508s |
| 32 | economy | gpt-4o-mini-2024-07-18 | strict_output_reliability | 호출 | success/success | $0.000085 | 5.926s |
| 33 | advanced | gpt-5.6-terra | multi_constraint | 호출 | success/success | $0.004215 | 8.045s |
| 34 | economy | gpt-4.1-mini-2025-04-14 | strict_output_reliability | 호출 | success/success | $0.000394 | 9.813s |
| 35 | advanced | gpt-5.6-terra | multi_constraint | 호출 | success/success | $0.005600 | 10.335s |
| 36 | economy | gpt-4o-mini-2024-07-18 | strict_output_reliability | 호출 | success/success | $0.000089 | 6.016s |
| 37 | economy | gpt-4o-mini-2024-07-18 | strict_output_reliability | 호출 | success/success | $0.000083 | 7.184s |
| 38 | advanced | gpt-5.6-terra | multi_constraint | 호출 | success/success | $0.004253 | 7.946s |
| 39 | balanced | - | deployment_http_504 | 미호출 | http_error/unknown | $0.000000 | 0.000s |
| 40 | balanced | gpt-4.1-2025-04-14 | multi_constraint | 호출 | success/success | $0.002378 | 8.559s |
| 41 | balanced | gpt-4o-mini-2024-07-18 | multi_constraint | 호출 | success/success | $0.000110 | 6.415s |
| 42 | economy | gpt-4o-mini-2024-07-18 | strict_output_reliability | 호출 | success/success | $0.000079 | 6.161s |
| 43 | advanced | gpt-5.6-terra | multi_constraint | 호출 | success/success | $0.003857 | 7.243s |
| 44 | advanced | - | deployment_http_504 | 미호출 | http_error/unknown | $0.000000 | 0.000s |
| 45 | balanced | gpt-5.6-luna | multi_constraint | 호출 | success/success | $0.001354 | 10.094s |
| 46 | balanced | gpt-4.1-mini-2025-04-14 | strict_output_reliability | 호출 | success/success | $0.000342 | 7.243s |
| 47 | economy | gpt-4o-mini-2024-07-18 | strict_output_reliability | 호출 | success/success | $0.000083 | 7.107s |
| 48 | economy | gpt-4.1-mini-2025-04-14 | strict_output_reliability | 호출 | success/success | $0.000310 | 6.625s |
| 49 | advanced | gpt-5.6-luna | multi_constraint | 호출 | success/success | $0.002637 | 9.515s |
| 50 | economy | gpt-4o-mini-2024-07-18 | strict_output_reliability | 호출 | success/success | $0.000082 | 5.740s |

## 실행 시각

- 시작: `2026-07-21T10:10:13.299888+00:00`
- 종료: `2026-07-21T10:40:17.123132+00:00`
