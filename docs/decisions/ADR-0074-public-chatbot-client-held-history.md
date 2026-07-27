# ADR-0074: Public Chatbot client-held conversation history

Status: Accepted

## Context

ADR-0030과 ADR-0033은 Public Chatbot을 최초 Conversation Memory session surface로 두고, 서버가 Session·Turn·Entry·Transcript와 Access Grant를 영구 저장하는 모델을 정의했다. MBA-316과 MBA-317은 이 목표의 persistence 및 public lifecycle 기반을 구현했고, MBA-318의 최초 구현은 이를 Workflow provider 실행까지 연결했다.

Public 방문자는 인증된 사용자나 조직 subject가 아니다. 서버가 익명 대화 원문을 영구 저장하면 방문자에게 session을 다시 연결하기 위한 bearer capability, transcript lifecycle, retention, purge, 암호화와 abuse-control 경계가 모두 필요해진다. 이는 Public Chatbot의 단순 멀티턴 요구보다 구현·운영·review 범위를 크게 확장한다.

한편 인증된 조직 내부 Chatbot은 권한 주체, 조직 정책, 운영 transcript와 감사 요구가 있으므로 durable Conversation Memory가 필요하다. 이미 구현한 Memory aggregate, encrypted persistence, retention/purge, admission/lease/fencing, provider usage와 concurrency 계약은 이 후속 surface에서 재사용 가치가 있다.

## Options Considered

### Option A: Public 대화를 서버에 영구 저장

- 장점: 새로고침·다중 기기 복구, transcript 조회, 서버 측 idempotent replay가 가능하다.
- 단점: 익명 bearer capability와 대규모 lifecycle·privacy·purge 운영 경계가 필요하고 공격 표면과 PR 범위가 커진다.

### Option B: 서버가 발급한 암호화 상태를 Client가 보관

- 장점: DB transcript 없이 tamper resistance와 deployment binding을 제공할 수 있다.
- 단점: key rotation, token 크기, rollback/replay 정책과 별도 상태 protocol이 필요하다.

### Option C: Client가 bounded user/assistant history를 매 요청에 전송

- 장점: 별도 public session·grant·transcript 저장 없이 기존 동기 HTTP 실행 흐름에 맞고 계약이 작다.
- 단점: Client가 history를 수정할 수 있고 refresh 후 복구되지 않으며, 서버가 응답 유실 뒤 원문을 재생할 수 없다.

## Decision

MBA-318 Public Chatbot은 Option C를 사용한다.

1. Public Chatbot Client는 완료된 `user`/`assistant` turn을 `conversation.history`로 매 요청에 보낸다.
2. 서버는 `system`, `developer`, `tool` role, extra field, 빈 content, 미완성·비교대 순서와 20 turn 초과를 거부한다.
3. 서버는 현재 `inputs`와 history를 다시 계산하고 대화 context가 4,096 token을 넘으면 가장 오래된 완료 turn부터 제거한다. 현재 inputs만으로 상한을 넘으면 provider 호출 전에 거부한다.
4. Client history는 신뢰할 수 없는 대화 맥락일 뿐이며 인증·인가·system policy·provenance의 근거가 될 수 없다.
5. Public 요청에서는 legacy `memory_mode`와 browser-generated `conversation_id`를 거부하고, server-side Conversation Session·Turn·Entry·Transcript·Access Grant를 생성하거나 조회하지 않는다.
6. Public lifecycle API(create/close/reset/delete/transcript/purge-status)는 등록하지 않는다. 새 대화와 reset은 Client가 local history를 버리는 동작이다.
7. 브라우저는 history를 React memory에만 유지한다. refresh·tab 종료 시 history는 사라지며 localStorage/sessionStorage에 자동 복구용 원문을 저장하지 않는다.
8. Workflow/Celery transport는 요청 처리 중 history를 일시 전달할 수 있지만 task 표현을 redaction하고, Public Chatbot WorkflowRun·NodeRun·Trace payload에는 입력·history·prompt·completion 원문을 저장하지 않는다. Result backend 값은 소비 직후 제거하며 장애 시 기존 최대 1시간 TTL을 상한으로 한다.
9. 인증된 조직 내부 Chatbot의 durable Conversation Memory는 별도 후속 이슈로 구현한다. 기존 durable 구현은 `backup/mba-318-durable-memory-e5ed60fa`에 보존한다.

이 결정은 ADR-0030과 ADR-0033의 Public Chatbot 영구 session/access-grant 선택을 대체한다. 해당 ADR의 durable Memory domain·인증형 내부 Chatbot 목표는 유지한다. MBA-318 최초 durable public runtime 설계는 백업 브랜치의 역사 기록으로만 보존하며 현재 Public 계약이 아니다.

## Rationale

- Public 익명 대화에서 서버가 소유해야 할 identity가 없으므로, 대화 연속성 책임을 요청 Client에 두는 것이 최소 권한과 데이터 최소화 원칙에 맞다.
- Client가 history를 수정할 수 있다는 사실은 Public 사용자가 자신의 prompt를 수정할 수 있다는 범위 안에 머문다. 서버 권한·정책 판단에 history를 사용하지 않으면 authorization 우회로 이어지지 않는다.
- 내부 Chatbot은 authenticated subject와 조직 governance가 있으므로 durable Memory의 비용을 정당화한다. Public과 내부 surface를 분리하면 이미 구현한 안전성 코드를 버리지 않으면서 현재 PR 범위를 줄일 수 있다.

## Affected Files

- `docs/PRD.md`, `docs/architecture.md`, `docs/data_model.md`
- `docs/features/conversation-memory/*`
- `apps/shared/domain/public_chat_history.py`
- `apps/gateway/api/v1/endpoints/run.py`
- `apps/gateway/services/deployment_service.py`
- `apps/gateway/api/api.py`, `apps/gateway/main.py`
- `apps/workflow_engine/workflow/nodes/llm/llm_node.py`
- `apps/workflow_engine/workflow/core/workflow_logger.py`
- `apps/client/app/embed/chat/*`
- 관련 Gateway, Shared, Workflow Engine, Client 테스트

## Consequences

- Public 대화는 refresh·다른 브라우저·다른 기기에서 복구되지 않는다.
- 응답이 provider 성공 뒤 Client에 도착하지 않으면 서버 transcript에서 복구할 수 없다. 같은 질문을 다시 보내면 provider가 다시 호출될 수 있다.
- 20 turn/4,096 token은 Client 최적화가 아니라 서버 권위 제한이다.
- Public WorkflowRun/NodeRun은 운영 상태·시간·usage 같은 content-free metadata만 유지한다.
- 기존 Public Conversation persistence/lifecycle 기반은 active API가 아니며 신규 public row를 만들지 않는다.

## Follow-up Review

- 인증형 내부 Chatbot 후속 이슈에서 `backup/mba-318-durable-memory-e5ed60fa`의 aggregate, persistence, retention/purge, admission/lease/fencing과 테스트를 현재 `dev`에 맞게 선별 재적용한다.
- 내부 surface는 authenticated execution subject, organization RBAC, CSRF/Origin, retention/legal policy와 operator transcript authorization을 별도로 검토한다.
- Public 대화의 refresh 복구 요구가 생기면 raw browser storage를 바로 추가하지 않고 Option B의 encrypted client-held state를 별도 ADR로 검토한다.
- 운영 검증은 Public WorkflowRun/NodeRun/Trace payload에 원문이 남지 않는지와 Redis result TTL/소비 후 제거를 포함한다.
