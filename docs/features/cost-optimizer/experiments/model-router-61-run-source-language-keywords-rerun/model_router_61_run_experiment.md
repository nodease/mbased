# 61회 입력 기반 모델 라우팅 정책 실험 보고서

- 실행 시각: 2026-07-07T17:21:29.893388+00:00
- workflow_id: `96000000-0000-0000-0000-000000000002`
- node_id: `llm-triage`
- 실행 방식: 실제 WorkflowEngine 실행 + 실제 provider 호출
- 외부 LLM provider 호출: 있음
- 정책 갱신 기준: 20회 실행마다 갱신

## 결론

- 61회 모두 하나의 workflow `96000000-0000-0000-0000-000000000002`와 LLM node `llm-triage`에서 실행됐다.
- 정책 갱신 결과는 [(0, 'bootstrap', 'gpt-4.1-mini', 4), (20, 'applied', 'gpt-4o-mini', 2), (40, 'applied', 'gpt-4o-mini', 2), (60, 'applied', 'gpt-4o-mini', 2)] 순서로 기록됐다.
- 정책 갱신은 단일 모델 교체가 아니라 active policy rule set 갱신이다.
- 실행 모델 분포는 {'gpt-4.1-mini': 20, 'gpt-4o-mini': 31, 'gpt-4.1': 10}로 기록됐다.
- rule 매칭 분포는 {'customer-facing-balanced': 20, 'default_cost_saving_model': 31, 'high_risk_input_strong_model': 10}로 기록됐다.
- 실험은 실제 provider 호출을 사용했고 WorkflowEngine과 DB 로그 경로도 실제 실행 경로를 사용했다.

## 정책 변화

| 갱신 시점 | 정책 버전 | 갱신 결과 | 기본 모델 | fallback | rule 수 | judge 토큰 | 사유 |
| --- | --- | --- | --- | --- | ---: | ---: | --- |
| 0 | router-policy-v1 | bootstrap | gpt-4.1-mini | gpt-4.1 | 4 | - | 기본 rule set으로 bootstrap했습니다. |
  - `long-input-strong-model`: `{'input_length_bucket': 'long'}` -> `gpt-4.1` (fallback `-`)
  - `customer-facing-balanced`: `{'customer_facing': True}` -> `gpt-4.1-mini` (fallback `gpt-4.1`)
  - `knowledge-enabled-balanced`: `{'knowledge_enabled': True}` -> `gpt-4.1-mini` (fallback `gpt-4.1`)
  - `short-json-no-knowledge`: `{'output_format': 'json', 'knowledge_enabled': False, 'input_length_bucket': 'short'}` -> `gpt-4o-mini` (fallback `gpt-4.1-mini`)
| 20 | router-policy-v2 | applied | gpt-4o-mini | gpt-4.1 | 2 | 3366 | judge rule set applied |
  - `high_risk_input_strong_model`: `{'keyword_any': ['치명', '고객 영향도는 치명', 'high-risk', 'critical'], 'customer_facing': True, 'output_format': 'json', 'node_task': 'customer_support_ticket_triage'}` -> `gpt-4.1` (fallback `-`)
  - `default_cost_saving_model`: `{'customer_facing': True, 'output_format': 'json', 'node_task': 'customer_support_ticket_triage'}` -> `gpt-4o-mini` (fallback `gpt-4.1-mini`)
| 40 | router-policy-v3 | applied | gpt-4o-mini | gpt-4.1-mini | 2 | 3373 | judge rule set applied |
  - `high_risk_input_strong_model`: `{'keyword_any': ['치명', '고객 영향도는 치명', 'high-risk', 'critical'], 'customer_facing': True, 'output_format': 'json', 'node_task': 'customer_support_ticket_triage'}` -> `gpt-4.1` (fallback `-`)
  - `default_cost_saving_model`: `{'customer_facing': True, 'output_format': 'json', 'node_task': 'customer_support_ticket_triage'}` -> `gpt-4o-mini` (fallback `gpt-4.1-mini`)
| 60 | router-policy-v4 | applied | gpt-4o-mini | gpt-4.1-mini | 2 | 3371 | judge rule set applied |
  - `high_risk_input_strong_model`: `{'keyword_any': ['치명', '고객 영향도는 치명', 'high-risk', 'critical'], 'customer_facing': True, 'output_format': 'json', 'node_task': 'customer_support_ticket_triage'}` -> `gpt-4.1` (fallback `-`)
  - `default_cost_saving_model`: `{'customer_facing': True, 'output_format': 'json', 'node_task': 'customer_support_ticket_triage'}` -> `gpt-4o-mini` (fallback `gpt-4.1-mini`)

## 61회 실행별 라우팅 결과

| # | 입력 요약 | 정책 버전 | rule | 선택 모델 | 비용 | 토큰 | latency |
| ---: | --- | --- | --- | --- | ---: | ---: | ---: |
| 1 | [실험 입력 01] 정산 파일 재생성 관련 문의입니다. 고객 영향도는 중간이며, 현재 처리 우선순위와 답변  | router-policy-v1 | customer-facing-balanced | gpt-4.1-mini | $0.000205 | 257 | 2147ms |
| 2 | [실험 입력 02] 다운로드 위치 안내 관련 문의입니다. 고객 영향도는 높음이며, 현재 처리 우선순위와 답변 | router-policy-v1 | customer-facing-balanced | gpt-4.1-mini | $0.000202 | 253 | 2263ms |
| 3 | [실험 입력 03] 결제 API 장애 관련 문의입니다. 고객 영향도는 치명이며, 현재 처리 우선순위와 답변  | router-policy-v1 | customer-facing-balanced | gpt-4.1-mini | $0.000202 | 254 | 1803ms |
| 4 | [실험 입력 04] SLA 위반 가능성 관련 문의입니다. 고객 영향도는 낮음이며, 현재 처리 우선순위와 답변 | router-policy-v1 | customer-facing-balanced | gpt-4.1-mini | $0.000203 | 255 | 1711ms |
| 5 | [실험 입력 05] 세금계산서 오류 관련 문의입니다. 고객 영향도는 중간이며, 현재 처리 우선순위와 답변 초 | router-policy-v1 | customer-facing-balanced | gpt-4.1-mini | $0.000205 | 257 | 1777ms |
| 6 | [실험 입력 06] 대량 웹훅 재전송 관련 문의입니다. 고객 영향도는 높음이며, 현재 처리 우선순위와 답변  | router-policy-v1 | customer-facing-balanced | gpt-4.1-mini | $0.000204 | 259 | 1935ms |
| 7 | [실험 입력 07] SSO 로그인 실패 관련 문의입니다. 고객 영향도는 치명이며, 현재 처리 우선순위와 답변 | router-policy-v1 | customer-facing-balanced | gpt-4.1-mini | $0.000217 | 263 | 1702ms |
| 8 | [실험 입력 08] 보안 감사 로그 누락 관련 문의입니다. 고객 영향도는 낮음이며, 현재 처리 우선순위와 답 | router-policy-v1 | customer-facing-balanced | gpt-4.1-mini | $0.000208 | 259 | 4396ms |
| 9 | [실험 입력 09] 개인정보 마스킹 확인 관련 문의입니다. 고객 영향도는 중간이며, 현재 처리 우선순위와 답 | router-policy-v1 | customer-facing-balanced | gpt-4.1-mini | $0.000217 | 264 | 1776ms |
| 10 | [실험 입력 10] 월간 리포트 불일치 관련 문의입니다. 고객 영향도는 높음이며, 현재 처리 우선순위와 답변 | router-policy-v1 | customer-facing-balanced | gpt-4.1-mini | $0.000228 | 273 | 2146ms |
| 11 | [실험 입력 11] 관리자 권한 변경 요청 관련 문의입니다. 고객 영향도는 치명이며, 현재 처리 우선순위와  | router-policy-v1 | customer-facing-balanced | gpt-4.1-mini | $0.000180 | 241 | 1457ms |
| 12 | [실험 입력 12] 정산 파일 재생성 관련 문의입니다. 고객 영향도는 낮음이며, 현재 처리 우선순위와 답변  | router-policy-v1 | customer-facing-balanced | gpt-4.1-mini | $0.000221 | 267 | 1755ms |
| 13 | [실험 입력 13] 다운로드 위치 안내 관련 문의입니다. 고객 영향도는 중간이며, 현재 처리 우선순위와 답변 | router-policy-v1 | customer-facing-balanced | gpt-4.1-mini | $0.000202 | 253 | 1855ms |
| 14 | [실험 입력 14] 결제 API 장애 관련 문의입니다. 고객 영향도는 높음이며, 현재 처리 우선순위와 답변  | router-policy-v1 | customer-facing-balanced | gpt-4.1-mini | $0.000202 | 254 | 1922ms |
| 15 | [실험 입력 15] SLA 위반 가능성 관련 문의입니다. 고객 영향도는 치명이며, 현재 처리 우선순위와 답변 | router-policy-v1 | customer-facing-balanced | gpt-4.1-mini | $0.000212 | 261 | 1793ms |
| 16 | [실험 입력 16] 세금계산서 오류 관련 문의입니다. 고객 영향도는 낮음이며, 현재 처리 우선순위와 답변 초 | router-policy-v1 | customer-facing-balanced | gpt-4.1-mini | $0.000211 | 261 | 1602ms |
| 17 | [실험 입력 17] 대량 웹훅 재전송 관련 문의입니다. 고객 영향도는 중간이며, 현재 처리 우선순위와 답변  | router-policy-v1 | customer-facing-balanced | gpt-4.1-mini | $0.000222 | 270 | 1629ms |
| 18 | [실험 입력 18] SSO 로그인 실패 관련 문의입니다. 고객 영향도는 높음이며, 현재 처리 우선순위와 답변 | router-policy-v1 | customer-facing-balanced | gpt-4.1-mini | $0.000210 | 259 | 1807ms |
| 19 | [실험 입력 19] 보안 감사 로그 누락 관련 문의입니다. 고객 영향도는 치명이며, 현재 처리 우선순위와 답 | router-policy-v1 | customer-facing-balanced | gpt-4.1-mini | $0.000222 | 268 | 2161ms |
| 20 | [실험 입력 20] 개인정보 마스킹 확인 관련 문의입니다. 고객 영향도는 낮음이며, 현재 처리 우선순위와 답 | router-policy-v1 | customer-facing-balanced | gpt-4.1-mini | $0.000180 | 241 | 1366ms |
| 21 | [실험 입력 21] 월간 리포트 불일치 관련 문의입니다. 고객 영향도는 중간이며, 현재 처리 우선순위와 답변 | router-policy-v2 | default_cost_saving_model | gpt-4o-mini | $0.000080 | 264 | 1993ms |
| 22 | [실험 입력 22] 관리자 권한 변경 요청 관련 문의입니다. 고객 영향도는 높음이며, 현재 처리 우선순위와  | router-policy-v2 | default_cost_saving_model | gpt-4o-mini | $0.000070 | 245 | 1651ms |
| 23 | [실험 입력 23] 정산 파일 재생성 관련 문의입니다. 고객 영향도는 치명이며, 현재 처리 우선순위와 답변  | router-policy-v2 | high_risk_input_strong_model | gpt-4.1 | $0.001160 | 274 | 2051ms |
| 24 | [실험 입력 24] 다운로드 위치 안내 관련 문의입니다. 고객 영향도는 낮음이며, 현재 처리 우선순위와 답변 | router-policy-v2 | default_cost_saving_model | gpt-4o-mini | $0.000063 | 232 | 1282ms |
| 25 | [실험 입력 25] 결제 API 장애 관련 문의입니다. 고객 영향도는 중간이며, 현재 처리 우선순위와 답변  | router-policy-v2 | default_cost_saving_model | gpt-4o-mini | $0.000073 | 249 | 1577ms |
| 26 | [실험 입력 26] SLA 위반 가능성 관련 문의입니다. 고객 영향도는 높음이며, 현재 처리 우선순위와 답변 | router-policy-v2 | default_cost_saving_model | gpt-4o-mini | $0.000077 | 257 | 1727ms |
| 27 | [실험 입력 27] 세금계산서 오류 관련 문의입니다. 고객 영향도는 치명이며, 현재 처리 우선순위와 답변 초 | router-policy-v2 | high_risk_input_strong_model | gpt-4.1 | $0.001112 | 268 | 1595ms |
| 28 | [실험 입력 28] 대량 웹훅 재전송 관련 문의입니다. 고객 영향도는 낮음이며, 현재 처리 우선순위와 답변  | router-policy-v2 | default_cost_saving_model | gpt-4o-mini | $0.000074 | 255 | 1661ms |
| 29 | [실험 입력 29] SSO 로그인 실패 관련 문의입니다. 고객 영향도는 중간이며, 현재 처리 우선순위와 답변 | router-policy-v2 | default_cost_saving_model | gpt-4o-mini | $0.000076 | 254 | 1917ms |
| 30 | [실험 입력 30] 보안 감사 로그 누락 관련 문의입니다. 고객 영향도는 높음이며, 현재 처리 우선순위와 답 | router-policy-v2 | default_cost_saving_model | gpt-4o-mini | $0.000074 | 252 | 2716ms |
| 31 | [실험 입력 31] 개인정보 마스킹 확인 관련 문의입니다. 고객 영향도는 치명이며, 현재 처리 우선순위와 답 | router-policy-v2 | high_risk_input_strong_model | gpt-4.1 | $0.001158 | 273 | 2457ms |
| 32 | [실험 입력 32] 월간 리포트 불일치 관련 문의입니다. 고객 영향도는 낮음이며, 현재 처리 우선순위와 답변 | router-policy-v2 | default_cost_saving_model | gpt-4o-mini | $0.000078 | 261 | 2252ms |
| 33 | [실험 입력 33] 관리자 권한 변경 요청 관련 문의입니다. 고객 영향도는 중간이며, 현재 처리 우선순위와  | router-policy-v2 | default_cost_saving_model | gpt-4o-mini | $0.000077 | 257 | 1659ms |
| 34 | [실험 입력 34] 정산 파일 재생성 관련 문의입니다. 고객 영향도는 높음이며, 현재 처리 우선순위와 답변  | router-policy-v2 | default_cost_saving_model | gpt-4o-mini | $0.000070 | 245 | 1490ms |
| 35 | [실험 입력 35] 다운로드 위치 안내 관련 문의입니다. 고객 영향도는 치명이며, 현재 처리 우선순위와 답변 | router-policy-v2 | high_risk_input_strong_model | gpt-4.1 | $0.001258 | 284 | 2121ms |
| 36 | [실험 입력 36] 결제 API 장애 관련 문의입니다. 고객 영향도는 낮음이며, 현재 처리 우선순위와 답변  | router-policy-v2 | default_cost_saving_model | gpt-4o-mini | $0.000078 | 257 | 1788ms |
| 37 | [실험 입력 37] SLA 위반 가능성 관련 문의입니다. 고객 영향도는 중간이며, 현재 처리 우선순위와 답변 | router-policy-v2 | default_cost_saving_model | gpt-4o-mini | $0.000075 | 253 | 1710ms |
| 38 | [실험 입력 38] 세금계산서 오류 관련 문의입니다. 고객 영향도는 높음이며, 현재 처리 우선순위와 답변 초 | router-policy-v2 | default_cost_saving_model | gpt-4o-mini | $0.000077 | 257 | 1630ms |
| 39 | [실험 입력 39] 대량 웹훅 재전송 관련 문의입니다. 고객 영향도는 치명이며, 현재 처리 우선순위와 답변  | router-policy-v2 | high_risk_input_strong_model | gpt-4.1 | $0.001198 | 281 | 2447ms |
| 40 | [실험 입력 40] SSO 로그인 실패 관련 문의입니다. 고객 영향도는 낮음이며, 현재 처리 우선순위와 답변 | router-policy-v2 | default_cost_saving_model | gpt-4o-mini | $0.000069 | 242 | 2149ms |
| 41 | [실험 입력 41] 보안 감사 로그 누락 관련 문의입니다. 고객 영향도는 중간이며, 현재 처리 우선순위와 답 | router-policy-v3 | default_cost_saving_model | gpt-4o-mini | $0.000079 | 261 | 1856ms |
| 42 | [실험 입력 42] 개인정보 마스킹 확인 관련 문의입니다. 고객 영향도는 높음이며, 현재 처리 우선순위와 답 | router-policy-v3 | default_cost_saving_model | gpt-4o-mini | $0.000068 | 242 | 1463ms |
| 43 | [실험 입력 43] 월간 리포트 불일치 관련 문의입니다. 고객 영향도는 치명이며, 현재 처리 우선순위와 답변 | router-policy-v3 | high_risk_input_strong_model | gpt-4.1 | $0.001116 | 270 | 1709ms |
| 44 | [실험 입력 44] 관리자 권한 변경 요청 관련 문의입니다. 고객 영향도는 낮음이며, 현재 처리 우선순위와  | router-policy-v3 | default_cost_saving_model | gpt-4o-mini | $0.000074 | 251 | 1597ms |
| 45 | [실험 입력 45] 정산 파일 재생성 관련 문의입니다. 고객 영향도는 중간이며, 현재 처리 우선순위와 답변  | router-policy-v3 | default_cost_saving_model | gpt-4o-mini | $0.000073 | 251 | 2691ms |
| 46 | [실험 입력 46] 다운로드 위치 안내 관련 문의입니다. 고객 영향도는 높음이며, 현재 처리 우선순위와 답변 | router-policy-v3 | default_cost_saving_model | gpt-4o-mini | $0.000064 | 234 | 1525ms |
| 47 | [실험 입력 47] 결제 API 장애 관련 문의입니다. 고객 영향도는 치명이며, 현재 처리 우선순위와 답변  | router-policy-v3 | high_risk_input_strong_model | gpt-4.1 | $0.001292 | 289 | 1611ms |
| 48 | [실험 입력 48] SLA 위반 가능성 관련 문의입니다. 고객 영향도는 낮음이며, 현재 처리 우선순위와 답변 | router-policy-v3 | default_cost_saving_model | gpt-4o-mini | $0.000072 | 249 | 1637ms |
| 49 | [실험 입력 49] 세금계산서 오류 관련 문의입니다. 고객 영향도는 중간이며, 현재 처리 우선순위와 답변 초 | router-policy-v3 | default_cost_saving_model | gpt-4o-mini | $0.000080 | 262 | 13644ms |
| 50 | [실험 입력 50] 대량 웹훅 재전송 관련 문의입니다. 고객 영향도는 높음이며, 현재 처리 우선순위와 답변  | router-policy-v3 | default_cost_saving_model | gpt-4o-mini | $0.000081 | 266 | 2107ms |
| 51 | [실험 입력 51] SSO 로그인 실패 관련 문의입니다. 고객 영향도는 치명이며, 현재 처리 우선순위와 답변 | router-policy-v3 | high_risk_input_strong_model | gpt-4.1 | $0.001260 | 285 | 1602ms |
| 52 | [실험 입력 52] 보안 감사 로그 누락 관련 문의입니다. 고객 영향도는 낮음이며, 현재 처리 우선순위와 답 | router-policy-v3 | default_cost_saving_model | gpt-4o-mini | $0.000076 | 255 | 1943ms |
| 53 | [실험 입력 53] 개인정보 마스킹 확인 관련 문의입니다. 고객 영향도는 중간이며, 현재 처리 우선순위와 답 | router-policy-v3 | default_cost_saving_model | gpt-4o-mini | $0.000074 | 252 | 1714ms |
| 54 | [실험 입력 54] 월간 리포트 불일치 관련 문의입니다. 고객 영향도는 높음이며, 현재 처리 우선순위와 답변 | router-policy-v3 | default_cost_saving_model | gpt-4o-mini | $0.000074 | 253 | 2086ms |
| 55 | [실험 입력 55] 관리자 권한 변경 요청 관련 문의입니다. 고객 영향도는 치명이며, 현재 처리 우선순위와  | router-policy-v3 | high_risk_input_strong_model | gpt-4.1 | $0.000990 | 252 | 1961ms |
| 56 | [실험 입력 56] 정산 파일 재생성 관련 문의입니다. 고객 영향도는 낮음이며, 현재 처리 우선순위와 답변  | router-policy-v3 | default_cost_saving_model | gpt-4o-mini | $0.000070 | 246 | 1521ms |
| 57 | [실험 입력 57] 다운로드 위치 안내 관련 문의입니다. 고객 영향도는 중간이며, 현재 처리 우선순위와 답변 | router-policy-v3 | default_cost_saving_model | gpt-4o-mini | $0.000064 | 233 | 1547ms |
| 58 | [실험 입력 58] 결제 API 장애 관련 문의입니다. 고객 영향도는 높음이며, 현재 처리 우선순위와 답변  | router-policy-v3 | default_cost_saving_model | gpt-4o-mini | $0.000075 | 253 | 1976ms |
| 59 | [실험 입력 59] SLA 위반 가능성 관련 문의입니다. 고객 영향도는 치명이며, 현재 처리 우선순위와 답변 | router-policy-v3 | high_risk_input_strong_model | gpt-4.1 | $0.001166 | 274 | 1307ms |
| 60 | [실험 입력 60] 세금계산서 오류 관련 문의입니다. 고객 영향도는 낮음이며, 현재 처리 우선순위와 답변 초 | router-policy-v3 | default_cost_saving_model | gpt-4o-mini | $0.000077 | 258 | 1993ms |
| 61 | [실험 입력 61] 대량 웹훅 재전송 관련 문의입니다. 고객 영향도는 중간이며, 현재 처리 우선순위와 답변  | router-policy-v4 | default_cost_saving_model | gpt-4o-mini | $0.000077 | 259 | 1822ms |

## 최종 모델별 성능 프로필

| 모델 | 실행 수 | 성공률 | downstream 성공률 | fallback 비율 | 평균 비용 |
| --- | ---: | ---: | ---: | ---: | ---: |
| gpt-4.1 | 10 | 100.0% | 100.0% | 0.0% | $0.001171 |
| gpt-4.1-mini | 20 | 100.0% | 100.0% | 0.0% | $0.000208 |
| gpt-4o-mini | 31 | 100.0% | 100.0% | 0.0% | $0.000074 |

## 해석

이번 실험은 active policy의 rule set을 런타임에서 평가하는 실제 동작을 관찰한 것이다. 런타임은 judge를 호출하지 않고 입력을 risk/intent/context로 분류한 뒤 가장 먼저 매칭되는 rule의 모델을 사용한다.

정책은 20회마다 judge를 통해 갱신했다. 갱신 결과는 하나의 모델 ID가 아니라 `low/medium/high` 위험도와 입력 특성에 따라 여러 모델을 고르는 rule set이다.

## 생성 파일

- JSON: `C:\Dev\Crafton-Jungle\Nodease\artifacts\model_router_61_run_source_language_keywords_rerun\model_router_61_run_experiment.json`
- Markdown: `C:\Dev\Crafton-Jungle\Nodease\artifacts\model_router_61_run_source_language_keywords_rerun\model_router_61_run_experiment.md`
