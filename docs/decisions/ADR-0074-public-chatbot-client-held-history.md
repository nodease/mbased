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

1. Public Chatbot Client는 전용 `POST /api/v1/run-public/{url_slug}/chat` 경로로 완료된 `user`/`assistant` turn을 `conversation.history`에 담아 매 요청에 보낸다.
2. 서버는 `system`, `developer`, `tool` role, extra field, 빈 content, 미완성·비교대 순서와 20 turn 초과를 거부한다.
3. 서버는 현재 `inputs`와 history를 다시 계산하고 대화 context가 4,096 token을 넘으면 가장 오래된 완료 turn부터 제거한다. 현재 inputs만으로 상한을 넘으면 provider 호출 전에 거부한다.
4. Client history는 신뢰할 수 없는 대화 맥락일 뿐이며 인증·인가·system policy·provenance의 근거가 될 수 없다.
5. Public 요청에서는 legacy `memory_mode`와 browser-generated `conversation_id`를 거부하고, server-side Conversation Session·Turn·Entry·Transcript·Access Grant를 생성하거나 조회하지 않는다.
6. Public lifecycle API(create/close/reset/delete/transcript/purge-status)는 등록하지 않는다. 새 대화와 reset은 Client가 local history를 버리는 동작이다.
7. 브라우저는 history를 React memory에만 유지한다. refresh·tab 종료 시 history는 사라지며 localStorage/sessionStorage에 자동 복구용 원문을 저장하지 않는다.
8. Workflow/Celery transport는 요청 처리 중 history를 일시 전달할 수 있지만 task 표현을 redaction하고, Public Chatbot WorkflowRun·NodeRun·Trace payload에는 입력·history·prompt·completion 원문을 저장하지 않는다. Result backend 값은 소비 직후 제거하며 장애 시 기존 최대 1시간 TTL을 상한으로 한다.
9. 인증된 조직 내부 Chatbot의 durable Conversation Memory는 별도 후속 이슈로 구현한다.

이 결정은 ADR-0030과 ADR-0033의 Public Chatbot 영구 session/access-grant 선택을 대체한다. 해당 ADR의 durable Memory domain·인증형 내부 Chatbot 목표는 유지한다.

## Rationale

- Public 익명 대화에서 서버가 소유해야 할 identity가 없으므로, 대화 연속성 책임을 요청 Client에 두는 것이 최소 권한과 데이터 최소화 원칙에 맞다.
- Client가 history를 수정할 수 있다는 사실은 Public 사용자가 자신의 prompt를 수정할 수 있다는 범위 안에 머문다. 서버 권한·정책 판단에 history를 사용하지 않으면 authorization 우회로 이어지지 않는다.
- 내부 Chatbot은 authenticated subject와 조직 governance가 있으므로 durable Memory의 비용을 정당화한다. Public과 내부 surface를 분리하면 이미 구현한 안전성 코드를 버리지 않으면서 현재 PR 범위를 줄일 수 있다.

## Implementation Decision: Dedicated Public History Route

### Context

기존 `/api/v1/run-public/{url_slug}`는 Public Chatbot뿐 아니라 Web App과 Widget의 단일 실행에도 사용한다. 요청 body를 endpoint에서 읽은 뒤 Conversation 응답으로 표시하는 방식은 malformed JSON, schema validation error와 CORS preflight처럼 endpoint 진입 전에 끝나는 응답을 보호하지 못한다.

### Options Considered

- 공용 root 경로의 모든 응답에서 CORS를 제거: Web App과 Widget의 기존 브라우저 계약을 깨뜨리므로 선택하지 않는다.
- middleware가 request body 또는 deployment type을 조회: body 재생과 DB 의존성이 transport middleware에 들어가 계층 경계와 실패 처리가 복잡해지므로 선택하지 않는다.
- Public Chatbot history 요청을 전용 `/chat` suffix로 분리: outer middleware가 path만으로 OPTIONS, parser, validation, router failure를 모두 동일하게 보호할 수 있으므로 선택한다.

### Decision And Rationale

Public Chatbot의 client-held history는 `/api/v1/run-public/{url_slug}/chat`만 사용한다. `PublicConversationCorsBoundaryMiddleware`는 이 경로와 router가 canonical path로 돌려보내는 exact trailing-slash `/chat/`을 endpoint 실행 전부터 Conversation transport로 소유하고 모든 응답에서 CORS grant를 제거하며 `Cache-Control: no-store`, `Referrer-Policy: no-referrer`를 강제한다. `/chat/`은 별도 API가 아니라 redirect 전후에 동일한 보안 header를 적용하기 위한 transport alias다. 공용 root `/api/v1/run-public/{url_slug}`는 Web App과 Widget 호환성을 위해 기존 CORS 동작을 유지한다.

### Follow-up Review Notes

- OPTIONS, malformed JSON, 잘못된 content type, schema validation error가 모두 전용 경계에서 안전한 header를 반환하는지 회귀 테스트한다.
- `/chat/` OPTIONS와 redirect 응답이 global CORS를 상속하지 않는지 회귀 테스트한다.
- 공용 root의 비-Chatbot 브라우저 CORS 동작이 유지되는지 별도 회귀 테스트한다.

## Implementation Decision: Shared Sanitized History Projection

### Context

Public history는 provider 대화 맥락뿐 아니라 대명사형 후속 질문의 RAG 검색에도 필요하다. 기존 구현은 provider message를 조립하기 직전에만 history를 읽어서 RAG 검색이 현재 질문만 사용했고, 각 content를 정제한 뒤 serialized JSON 전체를 다시 정제해 redaction marker 자체가 prompt-injection 패턴으로 탐지되는 문제가 있었다.

### Options Considered

- Provider message에만 history를 사용: 일반 멀티턴 답변은 가능하지만 RAG evidence gate가 먼저 종료되는 후속 질문은 이전 대상을 찾지 못하므로 선택하지 않는다.
- RAG와 provider가 history를 각각 정제: 소비자별 규칙 drift와 중복 정제 위험이 있어 선택하지 않는다.
- leaf content를 한 번 정제한 canonical projection을 두 소비자가 공유: 정제 결과와 권한 경계를 일관되게 유지하므로 선택한다.

### Decision And Rationale

LLMNode는 Gateway가 검증·bound한 history의 각 content를 정확히 한 번 정제해 request-local projection으로 만든다. Provider용 JSON은 별도의 framing-only helper로 untrusted block에 넣고 다시 정제하지 않는다. RAG 검색어는 동일 projection의 가장 최근 완료 user/assistant pair와 현재 질문을 사용하되 현재 질문을 우선하고 전체를 1,000자로 제한한다. 들어가지 않는 오래된 pair는 부분 절단하지 않는다. 이 history는 검색 text에만 사용하며 Knowledge candidate resolution, public/private audience와 authorization 판단에는 전달하지 않는다.

### Affected Files

- `apps/shared/utils/prompt_injection_guard.py`
- `apps/workflow_engine/workflow/nodes/llm/llm_node.py`
- `apps/gateway/middleware/public_conversation_cors.py`
- Conversation Memory와 Chatbot Deployment component/API/test 문서 및 관련 테스트

### Follow-up Review Notes

- 정상 turn 사이에 의심 content가 있어도 해당 content만 marker로 바뀌고 다른 turn이 보존되는지 검증한다.
- RAG 검색어가 현재 질문, 최신 완료 pair, 1,000자 상한과 anonymous public-only candidate 결정을 함께 보존하는지 검증한다.
- History가 없는 기존 graph의 RAG 검색어가 바뀌지 않는지 검증한다.

## Affected Files

- `docs/PRD.md`, `docs/architecture.md`, `docs/data_model.md`
- `docs/features/conversation-memory/*`
- `apps/shared/domain/public_chat_history.py`
- `apps/gateway/api/v1/endpoints/run.py`
- `apps/gateway/middleware/public_conversation_cors.py`
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

- 인증형 내부 Chatbot 후속 이슈에서 active durable Memory domain contract를 최신 `dev`와 이 ADR의 Public 경계에 맞게 선별 적용한다.
- 내부 surface는 authenticated execution subject, organization RBAC, CSRF/Origin, retention/legal policy와 operator transcript authorization을 별도로 검토한다.
- Public 대화의 refresh 복구 요구가 생기면 raw browser storage를 바로 추가하지 않고 Option B의 encrypted client-held state를 별도 ADR로 검토한다.
- 운영 검증은 Public WorkflowRun/NodeRun/Trace payload에 원문이 남지 않는지와 Redis result TTL/소비 후 제거를 포함한다.

## Implementation Decision: Typed Consumer, Rollout And Queue Lifetime

### Context

전용 `/chat` route와 client-held history만으로는 실행 경계가 완결되지 않았다. 하나의 graph에 여러 LLM node가 있으면 모든 node가 같은 global execution context를 읽을 수 있었고, 익명 RAG audit은 owner가 아닌 `system`으로 기록되었다. Generic token helper는 tokenizer 실패 시 문자 수 추정값을 반환했고, legacy control 검증은 budget 및 secret migration 뒤에 수행되었다. 또한 Frontend와 Gateway가 서로 다른 revision이면 `/chat` 또는 root 계약이 맞지 않았으며 Redis broker backlog의 raw history는 Gateway timeout 뒤에도 소비될 수 있었다.

### Options Considered

- 모든 LLM node에 history를 계속 제공하고 prompt로 역할을 구분: classifier·router·다른 provider로 불필요한 대화가 전파되어 선택하지 않는다.
- 첫 번째 또는 마지막 LLM node를 자동 추측: multi-LLM graph 의미를 서버가 안전하게 알 수 없어 선택하지 않는다.
- 배포 snapshot에 canonical node location을 명시하고 Worker가 다시 계산: 배포자 선택과 runtime fail-closed 검증을 함께 제공하므로 선택한다.
- 혼합 revision을 즉시 hard cutover: 정상 public Chatbot이 배포 순서에 따라 404/422가 되어 선택하지 않는다.
- legacy root에서 server Memory를 임시 유지: 데이터 최소화 결정을 되돌리므로 선택하지 않는다.
- Celery expiry만 사용: dequeue 시점에 stale 실행을 막는 Worker 검증이 없어 충분하지 않다.

### Final Decision

1. Public Chatbot 배포 config는 `public_chat_conversation.v1`과 정확한 `history_consumer.node_id/container_path`를 저장한다. 대상은 snapshot 안의 `llmNode` 하나여야 한다.
2. Gateway는 `/chat` dispatch에 consumer의 canonical safe reference만 넣고 Worker는 canonical deployment row와 graph snapshot에서 이를 다시 계산한다. LLMNode는 자신의 canonical location과 일치할 때만 provider prompt와 RAG query에 history를 사용한다.
3. Public 실행은 authorization subject와 분리된 `execution_actor={"type":"public"}`를 사용한다. RAG audit은 `actor_id=null`, `actor_type=public`이며 app owner·credential principal·system으로 대체하지 않는다.
4. Public token bound는 실제 tokenizer만 사용한다. model tokenizer를 얻지 못하면 `cl100k_base`를 사용하고 그것도 실패하면 `conversation.token_count_unavailable`로 provider 호출 전에 거부한다. 문자 수 추정은 사용하지 않는다.
5. Envelope와 legacy control 검증, consumer mapping 검증은 budget admission, secret migration, DB mutation과 task publish 전에 끝난다.
6. `compatibility` rollout 동안 public info는 `client_history_v1` 또는 `legacy_v0` capability를 반환한다. 새 Client는 capability가 없거나 legacy이면 legacy control 없이 root를 사용한다. 새 Gateway의 root public Chatbot 호환 요청은 `memory_mode=false`, `conversation_id=null`, content persistence suppression 상태로 무상태 실행한다. `strict` 전환 뒤 root public Chatbot은 history-required로 닫는다.
7. Gateway는 raw history를 600초 TTL의 일회성 Redis key에 저장하고 Celery task에는 128-bit opaque reference만 넣는다. Public transient dispatch는 같은 생성 시각 기준 600초 deadline을 context와 Celery `expires`에 함께 기록한다. Worker는 DB, Knowledge sync, engine과 provider보다 먼저 absolute deadline을 검증한 뒤 history를 atomic GET+DELETE로 한 번만 소비하며 stale/malformed/missing reference를 non-retryable하게 거부한다. Result는 소비 직후 제거한다.

### Rationale

이 결정은 history의 소유자, 유일한 소비자, 실제 actor, 검증 순서, 배포 version 호환성과 transport lifetime을 각각 명시한다. Capability rollout은 가용성을 유지하지만 새 Gateway에서 legacy public server storage를 다시 활성화하지 않는다. Broker header와 Worker absolute deadline을 함께 사용하면 broker 특성이나 consumer 중단 상태와 무관하게 timeout 뒤 provider 실행을 막는다.

### Affected Files

- `apps/shared/domain/public_chat_conversation.py`
- `apps/shared/domain/public_chat_history.py`
- `apps/shared/services/public_chat_history_transient_store.py`
- `apps/shared/schemas/deployment.py`
- `apps/gateway/core/config.py`
- `apps/gateway/api/v1/endpoints/deployment.py`
- `apps/gateway/api/v1/endpoints/run.py`
- `apps/gateway/services/deployment_service.py`
- `apps/workflow_engine/tasks.py`
- `apps/workflow_engine/workflow/nodes/llm/llm_node.py`
- `apps/client/app/features/workflow/components/deployment/*`
- `apps/client/app/features/workflow/hooks/useDeployment.ts`
- `apps/client/app/embed/chat/*`
- 관련 Shared, Gateway, Workflow Engine, Client 테스트와 기능 문서

### Follow-up Review Notes

- 배포 순서는 capability를 이해하는 Frontend, compatibility Gateway, 기존 Client drain, strict Gateway 순으로 운영 검토한다.
- strict 전환 전 active legacy Chatbot 배포를 새 versioned consumer config로 재배포한다.
- Redis TTL 삭제 시점만 신뢰하지 않는다. 보안 불변조건은 opaque reference의 일회 소비와 Worker absolute deadline 재검증이다.
- multi-LLM, nested loop location, tokenizer failure, zero-write invalid request, public audit actor, forged broker mapping과 stalled queue를 회귀 테스트한다.

## Implementation Decision: One-time history completion boundaries

### Context

일회성 Redis history를 Worker 진입 즉시 소비하면 canonical deployment 검증이나 preflight 뒤 발생한 일반 오류를 Celery가 재시도할 때 원문을 다시 얻을 수 없다. 반대로 dequeue 시각에만 absolute deadline을 확인하면 대기·검증 시간이 지난 뒤 provider를 호출할 수 있다. 또한 Client의 top-level node 목록과 표시 문자열 기반 history 판정은 nested Loop consumer와 실패 fallback을 정확히 구분하지 못하며, parent context 전체 복사는 같은 ID의 subworkflow LLM에 history를 전달할 수 있다.

### Options Considered

- Redis history를 실행 시작 즉시 소비하고 모든 오류를 재시도: retry task가 이미 삭제된 reference를 읽으므로 선택하지 않는다.
- Redis lease/ack와 replayable provider idempotency를 이번 범위에 추가: durable protocol과 운영 상태가 커지므로 Public client-held history의 최소 범위에는 선택하지 않는다.
- 검증을 먼저 끝내고 외부 I/O 직전에 한 번 소비하며, 소비 뒤 오류는 재시도하지 않고 Client 재전송으로 복구: 일회성·무저장 계약을 유지하므로 선택한다.
- subworkflow에 consumer location을 다시 결박해 history를 위임: child deployment가 독립적으로 선택되지 않았으므로 선택하지 않는다.

### Final Decision

1. Public absolute deadline은 Celery hard limit과 합성해 더 이른 시각을 runtime monotonic deadline으로 사용하고 Knowledge/provider 외부 I/O 직전에 다시 검사한다.
2. Worker는 canonical deployment, snapshot, runtime configuration과 Knowledge sync를 마친 뒤 history reference를 atomic consume한다. 소비 전 오류는 기존 retry 정책을 사용할 수 있지만 소비 뒤 오류는 Celery retry를 예약하지 않고 내부 `conversation.history_replay_required`로 종료한다. Client가 원래 page-memory history를 포함해 새 요청을 보내는 것이 유일한 replay 경로다.
3. Client는 top-level과 nested Loop graph의 LLM을 재귀적으로 열거하고 `{container_path,node_id}` 전체를 selection key와 deployment config에 사용한다.
4. 공개 UI가 표시하는 fallback/error text는 `historyEligible=false`이며 실제 성공·non-empty final preview만 다음 요청 history에 포함한다.
5. WorkflowNode child context는 public actor, content-persistence suppression과 absolute deadline은 상속하지만 raw history, transient reference와 parent consumer binding은 제거한다.

### Rationale

이 경계는 익명 대화 원문을 저장하지 않으면서도 stale provider 호출, 소진된 reference 재시도, graph identity 혼동과 Client 생성 오류문 재주입을 막는다. Public history는 한 deployment graph의 명시된 canonical consumer 한 곳에서 한 요청 동안만 materialize된다.

### Affected Files

- `apps/workflow_engine/tasks.py`
- `apps/workflow_engine/workflow/nodes/llm/llm_node.py`
- `apps/workflow_engine/workflow/nodes/workflow/workflow_node.py`
- `apps/client/app/features/workflow/components/deployment/*`
- `apps/client/app/features/workflow/utils/publicChatConversationConsumers.ts`
- `apps/client/app/embed/chat/*`
- Conversation Memory와 Chatbot Deployment API/component/test 문서 및 관련 테스트

### Follow-up Review Notes

- Public provider 호출을 서버에서 자동 replay해야 하는 요구가 생기면 일회성 GET+DELETE를 완화하지 말고 별도 lease/ack, idempotency와 expiry protocol을 ADR로 검토한다.
- 새 nested container type을 지원할 때 Client 순회와 Shared canonical location parser를 같은 계약 테스트로 확장한다.
- content suppression과 deadline은 child graph에서도 유지되지만 raw public history가 child provider/RAG에 도달하지 않는지 회귀 테스트한다.

## Implementation Decision: Final projection, common deadline and strict reactivation

### Context

Gateway가 raw history를 4,096 token 안으로 줄여도 prompt-injection 정제 marker와 JSON/untrusted framing이 원문보다 길어질 수 있다. 또한 Public absolute deadline 검사는 LLM/Knowledge 경로에만 있었기 때문에 늦게 실행된 HTTP, Slack, GitHub node는 요청 lifetime 뒤 외부 I/O를 시작할 수 있었다. Strict rollout의 consumer mapping 검증도 create/preflight에는 있었지만 기존 inactive deployment의 toggle 활성화에는 적용되지 않았다.

### Options Considered

- Gateway raw history 검증만 유지: 실제 provider projection이 상한을 넘을 수 있어 선택하지 않는다.
- LLM과 각 외부 node가 개별 deadline을 구현: node 추가 때 누락과 정책 drift가 반복되므로 선택하지 않는다.
- Strict 전환 전에 운영자가 legacy deployment를 수동 정리: toggle API가 invalid active state를 만들 수 있어 선택하지 않는다.
- 최종 projection 재계산, 공통 external-effect deadline guard, activation service의 동일 contract 검증: 기존 정책을 실제 소비·상태 전이 경계에 한 번씩 적용하므로 선택한다.

### Final Decision

1. Worker는 canonical current inputs를 exact tokenizer로 다시 계산해 Public history의 잔여 token budget을 만든다. Queue의 같은 이름 metadata는 신뢰하지 않고 제거한다.
2. 선택된 LLM consumer는 각 history leaf를 정제한 뒤 JSON serialization과 `CLIENT_CONVERSATION_HISTORY` framing까지 완료한 최종 projection을 exact tokenizer로 계산한다. 잔여 예산을 넘으면 가장 오래된 완료 user/assistant pair를 제거하며 pair를 부분 절단하지 않는다. Provider prompt와 RAG query는 이 동일한 bounded projection을 공유한다.
3. `ExternalEffectExecutor`는 공통 monotonic task deadline을 effect claim 전과 provider invoke 직전에 검사한다. Claim 뒤 만료는 `FAILED_BEFORE_EFFECT`, `STOP`, safe `external_effect.deadline_exceeded`로 terminalize하고 provider를 호출하지 않는다. Read-only HTTP/GitHub 경로도 공통 guard를 통과한다.
4. Strict rollout에서 deployment toggle 활성화는 persisted config와 graph snapshot의 `public_chat_conversation.v1` consumer mapping을 knowledge, secret, schedule과 active pointer 변경 전에 검증한다. Legacy/malformed mapping은 content-free `422 conversation.*`로 종료하고 deployment/App/schedule/transaction 상태를 바꾸지 않는다. Compatibility rollout은 legacy 재활성화를 계속 허용한다.

### Rationale

Admission 시점의 raw 표현과 실제 소비 시점의 최종 표현을 같은 것으로 가정하지 않는다. Lifetime과 activation 정책도 특정 node나 create endpoint의 부수 조건이 아니라 공통 외부 I/O 및 deployment lifecycle 불변조건으로 적용한다. 이로써 delayed queue 실행, 정제 확장과 legacy 재활성화가 기존 Public 데이터 최소화·bounded context 계약을 우회하지 못한다.

### Affected Files

- `apps/shared/domain/public_chat_history.py`
- `apps/shared/domain/external_effect_error.py`
- `apps/workflow_engine/application/external_effect.py`
- `apps/workflow_engine/tasks.py`
- `apps/workflow_engine/workflow/nodes/llm/llm_node.py`
- `apps/gateway/services/deployment_service.py`
- `apps/gateway/api/v1/endpoints/deployment.py`
- 관련 Shared, Workflow Engine, Gateway 테스트와 기능 문서

### Follow-up Review Notes

- 새로운 외부 provider node는 write/read 여부와 무관하게 공통 deadline 경계를 우회하지 않는지 검토한다.
- history sanitizer/framing 형식이 바뀌면 raw admission뿐 아니라 최종 projection exact-token 회귀 테스트를 함께 갱신한다.
- Strict rollout 전환 검증은 create, preflight와 inactive deployment reactivation 세 상태 전이를 모두 포함한다.
