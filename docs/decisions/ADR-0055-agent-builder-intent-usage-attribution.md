# ADR-0055: Agent Builder intent 사용량 귀속 경계

Status: Accepted
Related ADRs: [ADR-0022](ADR-0022-incremental-hexagonal-architecture-adoption.md), [ADR-0026](ADR-0026-agent-builder-intent-and-connection-validation.md), [ADR-0040](ADR-0040-agent-builder-unified-model-recommendation.md), [ADR-0045](ADR-0045-agent-builder-direct-edit-parameter-guidance.md), [ADR-0054](ADR-0054-agent-builder-generation-modes.md)

## 배경

Agent Builder는 자연어 요청을 구조화하는 최초 planner 호출과, schema-valid 결과가 의미 규칙을 위반했을 때만 결과를 고치는 semantic repair 호출을 최대 한 번 수행한다. Provider 호출, JSON 파싱 또는 Pydantic 구조 검증 실패에는 repair하지 않는다. 실제로 수행된 호출은 LLM 비용을 발생시키지만 기존 workflow node 실행이 아니어서 `llm_usage_logs`에 기록되지 않았다. 이 누락은 조직 관리 비용, workflow별 비용과 예산, 내 모듈 월 예상 비용을 실제보다 낮게 만든다.

호출 뒤 현재 모델이나 credential을 다시 검색하면 설정 변경 사이에 실제 호출과 다른 대상을 귀속할 수 있다. Provider 응답을 검증한 뒤에만 기록하면 사용량이 발생했지만 응답 schema가 잘못된 호출도 누락된다. 반대로 사용량 저장 실패를 이유로 provider를 다시 호출하면 중복 비용을 만든다.

## 결정

1. Agent Builder intent 호출은 기존 `llm_usage_logs`에 `runtime_surface='agent_builder_intent'`로 기록한다. 별도 사용량 table, Agent Builder 전용 endpoint 또는 전용 대시보드는 만들지 않는다. 기존 관리·내 모듈 비용 응답에는 구분 집계 필드를 additive하게 제공한다.
2. 논리 호출 identity는 `(runtime_surface, runtime_session_id, runtime_request_id, runtime_attempt)`다. `runtime_session_id`와 `runtime_request_id`는 billing provenance를 보존하는 opaque UUID snapshot이며 Agent Builder 정리 정책에 종속되는 FK를 두지 않는다. 최초 planner는 attempt 1, repair는 attempt 2다.
3. 실제 provider 호출에 사용하도록 이미 권한 검증된 model DB ID와 credential DB ID를 runtime에서 그대로 전달한다. 기록 단계에서 모델이나 credential을 다시 선택하지 않는다.
4. 각 Provider 호출 전에 request가 여전히 `processing`인지와 request/session/App primary workflow, credential 유효 상태, active chat model, verified credential-model 관계, 사용자 credential use 권한과 active organization membership을 검증하고 같은 attempt의 `pending` 사용량 행을 짧은 별도 transaction으로 확보한다. 이때 model·credential ID와 가격을 고정한다. 첫 응답 뒤 request가 취소되었으면 attempt 1의 실제 사용량은 완료할 수 있지만 attempt 2를 예약하거나 Provider를 다시 호출하지 않는다. 같은 identity가 이미 예약됐거나 성공으로 끝났다면 provider를 다시 호출하지 않고 fail-closed한다.
5. Provider 응답을 받은 직후 raw content를 해석하거나 schema/semantic validation을 수행하기 전에 응답에서 token usage mapping만 분리한다. 사용량 정규화기와 저장기에는 raw content, choices 또는 provider 응답 전체를 전달하지 않는다. 예약된 같은 행을 token, 고정 가격으로 계산한 cost, latency와 `success` 상태로 완료한다. Provider 응답을 받지 못했거나 검증 가능한 usage가 없으면 `pending` 행을 삭제하고 요청을 안전하게 종료한다.
6. 완료 transaction은 예약 ID와 논리 호출 identity를 함께 잠그고 확인한다. 같은 예약의 commit 응답 유실이나 동시 완료는 저장된 actor, organization, workflow, model, credential, token, cost와 latency가 모두 같을 때만 성공으로 재사용한다. Model 또는 credential이 호출 중 삭제되어 연결 ID가 NULL이 되어도 예약 당시 가격과 나머지 billing fact로 완료하며, 다르면 fail-closed한다.
7. 일시적 DB 실패에는 예약에서 고정한 값으로 완료 저장만 제한적으로 재시도하고 현재 가격을 다시 계산하거나 model/credential을 다시 선택하지 않는다. 저장 성공을 확인할 수 없으면 `pending` 행을 운영 증거로 남기고 Agent Builder 요청을 `INTENT_USAGE_RECORDING_FAILED`로 종료하며 이미 완료된 provider 호출을 다시 실행하지 않는다. 비용·token·호출 수·Top Model 집계는 `success`인 Agent Builder 행만 포함한다.
8. 사용량은 인증 사용자, active organization과 direct-edit primary workflow에 귀속한다. 예약 전에 request workflow, session workflow와 App의 현재 primary workflow가 모두 같은지 확인한다. 과거 workflow와 연결된 실행·배포·감사 이력은 보존하지만 non-primary workflow의 신규 Agent Builder provider 호출은 비용 발생 전에 차단한다. 예약 뒤 provider 호출 중 primary pointer나 model·credential 설정이 바뀌어도 이미 발생한 호출은 예약된 workflow와 가격으로 완료한다. `workflow_run_id`와 `node_id`는 null이다.
9. Model 또는 credential이 이후 삭제되면 `SET NULL`로 연결만 해제한다. 당시 저장된 prompt/completion token, 비용, workflow·조직·사용자 귀속은 유지하며 기존 일반 집계에서도 제외하지 않는다. 신규 예약은 유효한 model과 credential ID를 계속 요구한다.
10. 사용량 행, 로그와 오류에는 사용자 메시지, system prompt, workflow context, provider 응답 원문, credential config, API key 또는 token을 저장하지 않는다.

## 영향

- Agent Builder 최초 planner와 repair는 각각 독립 호출로 비용에 반영된다.
- 응답 schema가 잘못돼도 provider 응답에 검증 가능한 usage가 있으면 attempt 1 비용 기록은 보존되지만 repair Provider 호출은 하지 않는다.
- 모델이 만료되어 카탈로그에서 삭제돼도 과거 총비용과 토큰은 유지되고 모델 연결만 알 수 없는 상태가 된다.
- 관리 페이지와 내 모듈은 기존 총비용 필드를 유지하면서 `runtime_surface='agent_builder_intent'` 비용과 그 밖의 workflow 실행 비용을 additive 필드로 구분한다. 예산 사용률과 전월 추세는 기존 총비용 기준을 유지한다.
- 사용자 Top Model 집계는 활성·비활성 모델과 연결된 과거 행을 모두 표시한다. 모델이 삭제되어 연결이 NULL이 된 행은 순위에서 표시할 모델명이 없으므로 제외하지만, token/cost는 조직·workflow·예산·내 모듈 총비용과 구분 집계에 계속 포함한다.
- Workflow Engine의 기존 LLM node 사용량 기록 구조와 모델 추천 정책은 변경하지 않는다.
