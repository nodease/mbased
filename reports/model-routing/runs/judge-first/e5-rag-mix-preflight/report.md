# 3차 Judge-first RAG 혼합 라우팅 실험 결과

- 상태: 중단
- 실행 수: 1 / 50
- Runtime Judge 모델: `gpt-5.4-mini` (출력 한도 `768` tokens)
- 중단 사유: `rag-20:schema_contract_failed`

## 확인한 계약

- Judge에는 기본 모델, fallback 모델, 독립된 node contract를 넣지 않았다.
- RAG 요청은 검색된 원문 대신 `used`, 검색 문서 수, 컨텍스트 길이, 근거 충족 여부만 Judge에 전달했다.
- 비-RAG 요청은 `rag_context.used=false`로 전달했다.
- RAG 원문은 실제 LLM 요청에만 포함되므로, 실제 task 비용에는 반영된다.

## 실행 요약

- RAG 분기 실행: 1건, 근거 충족: 0건
- task 비용 합계: `$0.000000`
- routing Judge 비용 합계: `$0.000000`
- 선택 모델 분포: {'gpt-4.1': 1}
- 결정 출처 분포: {'unknown': 1}

## 실행별 결과

| 요청 | 유형 | RAG | 짧은 사유 | 검토 후보 | 선택 모델 | 결과 |
| --- | --- | --- | --- | ---: | --- | --- |
| rag-20 | rag_grounded | 사용 | - | - | gpt-4.1 | 실패 |

## 중단 해석

10회 batch의 안전 규칙이 실패를 감지해 이후 요청을 실행하지 않았습니다. `result.json`의 마지막 행과 trace metadata를 우선 점검해야 합니다.
