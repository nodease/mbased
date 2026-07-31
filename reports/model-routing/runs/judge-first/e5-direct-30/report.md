# 3차 Judge-first RAG 혼합 라우팅 실험 결과

- 상태: 완료
- 실행 수: 30 / 50
- Runtime Judge 모델: `gpt-5.4-mini` (출력 한도 `768` tokens)
- 중단 사유: `없음`

## 확인한 계약

- Judge에는 기본 모델, fallback 모델, 독립된 node contract를 넣지 않았다.
- RAG 요청은 검색된 원문 대신 `used`, 검색 문서 수, 컨텍스트 길이, 근거 충족 여부만 Judge에 전달했다.
- 비-RAG 요청은 `rag_context.used=false`로 전달했다.
- RAG 원문은 실제 LLM 요청에만 포함되므로, 실제 task 비용에는 반영된다.

## 실행 요약

- RAG 분기 실행: 0건, 근거 충족: 0건
- task 비용 합계: `$0.032492`
- routing Judge 비용 합계: `$0.046254`
- 선택 모델 분포: {'gpt-4.1': 30}
- 결정 출처 분포: {'runtime_judge': 30}

## 실행별 결과

| 요청 | 유형 | RAG | 짧은 사유 | 검토 후보 | 선택 모델 | 결과 |
| --- | --- | --- | --- | ---: | --- | --- |
| advanced-01 | short_advanced_direct | 미사용 | 요구 수준 기반 선택 | 11 | gpt-4.1 | 성공 |
| advanced-02 | short_advanced_direct | 미사용 | 요구 수준 기반 선택 | 11 | gpt-4.1 | 성공 |
| routine-01 | routine_direct | 미사용 | 요구 수준 기반 선택 | 11 | gpt-4.1 | 성공 |
| routine-02 | routine_direct | 미사용 | 요구 수준 기반 선택 | 11 | gpt-4.1 | 성공 |
| balanced-01 | balanced_direct | 미사용 | 요구 수준 기반 선택 | 11 | gpt-4.1 | 성공 |
| balanced-02 | balanced_direct | 미사용 | 요구 수준 기반 선택 | 11 | gpt-4.1 | 성공 |
| advanced-03 | short_advanced_direct | 미사용 | 요구 수준 기반 선택 | 11 | gpt-4.1 | 성공 |
| advanced-04 | short_advanced_direct | 미사용 | 요구 수준 기반 선택 | 11 | gpt-4.1 | 성공 |
| routine-03 | routine_direct | 미사용 | 요구 수준 기반 선택 | 11 | gpt-4.1 | 성공 |
| routine-04 | routine_direct | 미사용 | 요구 수준 기반 선택 | 11 | gpt-4.1 | 성공 |
| balanced-03 | balanced_direct | 미사용 | 요구 수준 기반 선택 | 11 | gpt-4.1 | 성공 |
| balanced-04 | balanced_direct | 미사용 | 요구 수준 기반 선택 | 11 | gpt-4.1 | 성공 |
| advanced-05 | short_advanced_direct | 미사용 | 요구 수준 기반 선택 | 11 | gpt-4.1 | 성공 |
| advanced-06 | short_advanced_direct | 미사용 | 요구 수준 기반 선택 | 11 | gpt-4.1 | 성공 |
| routine-05 | routine_direct | 미사용 | 요구 수준 기반 선택 | 11 | gpt-4.1 | 성공 |
| routine-06 | routine_direct | 미사용 | 요구 수준 기반 선택 | 11 | gpt-4.1 | 성공 |
| balanced-05 | balanced_direct | 미사용 | 요구 수준 기반 선택 | 11 | gpt-4.1 | 성공 |
| balanced-06 | balanced_direct | 미사용 | 요구 수준 기반 선택 | 11 | gpt-4.1 | 성공 |
| advanced-07 | short_advanced_direct | 미사용 | 요구 수준 기반 선택 | 11 | gpt-4.1 | 성공 |
| advanced-08 | short_advanced_direct | 미사용 | 요구 수준 기반 선택 | 11 | gpt-4.1 | 성공 |
| routine-07 | routine_direct | 미사용 | 요구 수준 기반 선택 | 11 | gpt-4.1 | 성공 |
| routine-08 | routine_direct | 미사용 | 요구 수준 기반 선택 | 11 | gpt-4.1 | 성공 |
| balanced-07 | balanced_direct | 미사용 | 요구 수준 기반 선택 | 11 | gpt-4.1 | 성공 |
| balanced-08 | balanced_direct | 미사용 | 요구 수준 기반 선택 | 11 | gpt-4.1 | 성공 |
| advanced-09 | short_advanced_direct | 미사용 | 요구 수준 기반 선택 | 11 | gpt-4.1 | 성공 |
| advanced-10 | short_advanced_direct | 미사용 | 요구 수준 기반 선택 | 11 | gpt-4.1 | 성공 |
| routine-09 | routine_direct | 미사용 | 요구 수준 기반 선택 | 11 | gpt-4.1 | 성공 |
| routine-10 | routine_direct | 미사용 | 요구 수준 기반 선택 | 11 | gpt-4.1 | 성공 |
| balanced-09 | balanced_direct | 미사용 | 요구 수준 기반 선택 | 11 | gpt-4.1 | 성공 |
| balanced-10 | balanced_direct | 미사용 | 요구 수준 기반 선택 | 11 | gpt-4.1 | 성공 |

## 해석 범위

이 실험은 RAG/비-RAG 분기와 실제 Runtime Judge 선택이 끊기지 않는지를 검증합니다. 고정 고가·저가 모델 대비 품질/비용 경제성 평가는 별도 실험에서 비교해야 합니다.
