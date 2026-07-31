# Runtime Judge 5건 라우팅 점검

Status: Draft

## 목적

Runtime Judge가 현재 실행 가능한 OpenAI 후보 중에서 요청의 내용, 구조, 길이, RAG 근거량을 보고
저비용·균형·고성능 모델을 구분해 선택하는지 확인한다. 이 점검은 모델 선택 단계만 실제 호출한다.
선택된 후보 모델로 본문 workflow를 다시 실행하거나 답변 품질을 Judge로 채점하지 않는다.

## 실행 환경

- Judge: `gpt-5.4-mini`
- 후보: `gpt-4o-mini`, `gpt-4.1-mini`, `gpt-4.1`, `gpt-5-mini`, `gpt-5.4-mini`, `gpt-5.4`
- 실행 명령:

```powershell
.\apps\workflow_engine\.venv\Scripts\python.exe scripts\verify_runtime_judge_diagnostics.py --execute --judge-model gpt-5.4-mini
```

## 요청과 결과

| 요청 | 의도한 난도 | 실제 선택 | 판정 |
| --- | --- | --- | --- |
| 비밀번호 재설정 메뉴 위치를 한 문장으로 안내 | 저 | `gpt-4o-mini` | 일치 |
| 주문 번호·제품명·연락 수단 세 필드 JSON 추출 | 저 | `gpt-4o-mini` | 일치 |
| 결제·초대 메일·SSO 상태를 순서대로 점검 | 중 | `gpt-4o-mini` | 저비용 쪽으로 치우침 |
| 장문 장애 보고 3개를 중복 없이 실행 항목으로 종합 | 중 | `gpt-5-mini` | 일치 |
| 개인정보 삭제와 법적 보존이 충돌하는 RAG 판단 | 고 | `gpt-5.4` | 일치 |

`capability_tier`를 Judge 입력에서 제거한 뒤 다시 실행한 결과, 의도한 고·중·저 구간 기준으로
5건 중 4건이 일치했다.

## 해석

단순 요청을 저비용 모델로, 고위험 RAG 판단을 고성능 모델로 보내는 큰 방향은 확인됐다.
다만 계정·SSO 점검 요청은 Judge가 이를 "절차 안내"로 해석해 `gpt-4o-mini`를 선택했다.
즉 모델의 고정 tier를 없애도, 요청을 실제로 얼마나 많은 조건과 판단이 필요한 일로 해석하는지가
중간 난도 선택의 핵심이다. 현재 전역 profile의 사전 품질값도 여러 후보에서 거의 같으므로,
중간 난도 구간은 아직 운영 계약 성적이 쌓일수록 더 안정화돼야 한다.

이 결과는 라우터가 모든 난도에서 안정적으로 원하는 모델을 고른다고 말하기에는 부족하다.
다음 보완은 특정 모델을 강제로 고정하는 것이 아니라, 모델 catalog의 사전 profile을 실제 capability
차이가 드러나도록 정리하고, 배포 후 schema·후속 노드·fallback 성적을 후보별로 누적해 선택 근거를
보정하는 것이다.

## 로컬 학습 안전장치

이번 변경부터 Judge의 선택은 실행 직후 local router에 학습되지 않는다.

1. 실행 시 원문 없이 숫자 vector, 선택 모델, 후보 목록만 대기 label로 저장한다.
2. workflow 완료 후 schema 통과, 후속 노드 성공, fallback 미발생을 확인한다.
3. 세 조건을 통과한 label만 mDeBERTa local artifact에 반영한다.
4. 실패하거나 fallback된 선택은 `rejected` 상태로 남기고 학습하지 않는다.

따라서 잘못 선택돼 계약을 깨뜨린 모델이 이후 local router에 그대로 강화되는 문제를 줄인다.
