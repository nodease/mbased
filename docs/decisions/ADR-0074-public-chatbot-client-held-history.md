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
5. Public `/chat` 요청에서는 legacy `memory_mode`와 browser-generated `conversation_id`를 거부하고, server-side Conversation Session·Turn·Entry·Transcript·Access Grant를 생성하거나 조회하지 않는다. Compatibility root에만 구 Gateway owner-memory fallback을 차단하는 요청별 일회성 `conversation_id`를 허용하며 새 Gateway는 이를 dispatch 전에 제거한다.
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
6. `compatibility` rollout 동안 public info는 `client_history_v1` 또는 `legacy_v0` capability를 반환한다. 새 Client는 capability가 없거나 legacy이면 history와 `memory_mode` 없이 요청마다 새 secure-random `conversation_id` 하나만 root에 보낸다. 구 Gateway는 이를 owner-memory 조회를 차단하는 일회성 scope로 사용하고, 새 Gateway는 해당 control을 제거해 `memory_mode=false`, `conversation_id=null`, content persistence suppression 상태로 무상태 실행한다. `strict` 전환 뒤 root public Chatbot은 history-required로 닫는다.
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
## Implementation Decision: Queue representation, consume retry and deployment transition

### Context

Gateway가 opaque reference만 publish하더라도 Worker가 reserved raw history field를 다시 허용하면 구형·오작동 producer가 broker 비저장 계약을 우회할 수 있다. Redis atomic consume 호출은 store unavailable, invalid reference, missing value와 corrupt value를 구분하지 않아 소비 전 일시 장애까지 non-retryable하게 종료했다. 또한 열린 Embed Chat은 같은 slug의 public info를 최초 한 번만 읽어 active deployment가 바뀌어도 이전 capability와 history를 계속 사용했다.

### Options Considered

- Gateway producer만 신뢰하고 Worker의 raw field 호환을 유지: broker 원문과 redelivery replay를 허용하므로 선택하지 않는다.
- 모든 Redis consume 오류를 retry 또는 non-retryable 한쪽으로 통일: availability 또는 at-most-once history 경계 중 하나를 깨므로 선택하지 않는다.
- Client가 요청 전 info를 매번 조회하되 version precondition은 두지 않음: info와 run 사이 active pointer race가 남으므로 선택하지 않는다.
- Worker raw field fail-closed, typed consume error, request deployment version precondition과 Client one-shot recovery: 각 신뢰·불확실성·상태 전이 경계를 직접 닫으므로 선택한다.

### Final Decision

1. `workflow.execute`는 queued context에 `public_chat_history`가 있으면 DB, Redis, Knowledge와 Engine 전에 `conversation.history_payload_forbidden`으로 종료한다. `public_chat_history_ref`만 public transport 표식이며 raw history는 Worker가 validated reference를 atomic consume한 뒤 invocation-local context에만 만든다.
2. Transient store error는 safe code를 보존한다. `conversation.history_store_unavailable`만 history 소비가 확인되기 전 기존 bounded Celery retry를 사용한다. Invalid/missing/corrupt reference는 non-retryable이고, Redis가 consume 뒤 응답을 잃은 경우 retry가 missing으로 닫히더라도 provider를 호출하거나 history를 replay하지 않는다.
3. 새 Public Client는 public info의 `version`을 root와 `/chat` body의 `deployment_version`으로 보낸다. Gateway는 active deployment row와 일치하지 않으면 budget, transient store와 task publish 전에 `409 conversation.deployment_version_changed`를 반환한다. Public info는 `Cache-Control: no-store`다.
4. Embed Chat은 version conflict에서 public info를 다시 조회하고 이전 deployment의 완료 history를 폐기한다. 현재 사용자 입력만 새 version의 input schema와 capability로 다시 구성해 한 번 재시도하며 두 번째 실패는 일반 오류로 종료한다.

### Rationale

Raw/reference 표현, atomic consume 결과와 active deployment version은 caller가 추측할 값이 아니라 각 소유 경계가 검증해야 하는 상태다. Retry는 provider effect 전에만 허용하고 version이 바뀐 history는 자동 이관하지 않아 broker 비저장, at-most-once consume와 deployment snapshot 의미를 함께 보존한다.

### Affected Files

- `apps/shared/services/public_chat_history_transient_store.py`
- `apps/workflow_engine/tasks.py`
- `apps/gateway/api/v1/endpoints/run.py`
- `apps/gateway/api/v1/endpoints/deployment.py`
- `apps/gateway/services/deployment_service.py`
- `apps/client/app/embed/chat/*`
- Conversation Memory requirements, API, component와 test case 문서

### Follow-up Review Notes

- 새 producer 또는 task envelope revision은 raw public history field를 다시 허용하지 않는다.
- Redis client/library 변경 시 pre-consume unavailable과 consumed/missing/corrupt 분류가 provider replay를 만들지 않는지 검토한다.
- Public info capability가 추가되면 slug가 아니라 deployment version을 conversation reset 경계로 유지한다.

## Implementation Decision: Bounded admission stages and versioned public task queue

### Context

Public current inputs는 history와 별도로 크기 상한이 없어 exact tokenizer가 매우 큰 JSON을 처리할 수 있었고, Nginx의 일반 100 MB body 상한도 anonymous `/chat`에 그대로 적용됐다. Client는 turn 수만 제한해 server message/envelope 상한을 넘는 성공 응답을 다음 요청에 반복 전송할 수 있었다. 또한 sanitizer 이후 projection이 raw validator를 다시 사용해 안전 marker 확장이나 empty redaction 결과를 잘못 거부했다. Public context를 기존 `workflow.execute`/`workflow` queue에 실으면 rolling deployment 중 구 Worker가 새 marker를 무시하고 기존 content persistence 경로를 실행할 수 있었다.

### Options Considered

- Token 상한만 유지: tokenizer 전 CPU/memory admission이 없어 선택하지 않는다.
- Nginx 전역 100 MB 상한만 사용: anonymous Chatbot payload의 실제 계약보다 지나치게 커 선택하지 않는다.
- Client turn 상한만 적용하고 server 422에서 복구: oversized assistant 응답이 이후 모든 요청을 오염시키므로 선택하지 않는다.
- Sanitizer 출력에 raw validator 재사용: trusted transformation의 확장과 빈 pair 제거를 raw input 위반으로 오인하므로 선택하지 않는다.
- Public task를 기존 task/queue에 유지하고 context marker만 추가: rolling 구 Worker가 marker를 이해하지 못해 fail-open할 수 있으므로 선택하지 않는다.
- 단계별 byte/token 상한, Client 동일 상한, sanitized projection 전용 검사와 versioned task/queue: 각 신뢰 경계에서 비용과 호환성을 직접 제한하므로 선택한다.

### Final Decision

1. Shared raw admission은 current inputs canonical JSON을 UTF-8 131,072 bytes 이하인지 exact tokenizer 전에 확인한다. Gateway transport middleware와 Nginx는 정확한 Public `/chat` POST body를 393,216 bytes 이하로 제한하고 초과 요청은 content-free `413 conversation.request_too_large`로 종료한다.
2. Browser history builder는 server와 동일하게 content를 Unicode scalar 최대 32,768 characters, encoded history 최대 131,072 bytes로 제한한다. Invalid/oversized 완료 pair는 history에서 제외하되 UI 성공 응답은 유지하고, envelope 초과는 oldest completed pair 단위로 제거한다.
3. Sanitized projection은 role/order/shape/Unicode 구조만 다시 검증한다. 정제로 한쪽이 빈 pair는 전체 제거하고 sanitizer marker 확장에는 raw message/envelope 상한을 재적용하지 않는다. 실제 framing을 포함한 final exact-token 상한이 consumer 비용을 제한한다.
4. Gateway는 Public transient context를 versioned `workflow.execute_public_chat.v1` task와 `workflow-public-chat-v1` queue에만 publish한다. 새 Worker는 rollout 동안 일반 queue와 전용 queue를 함께 소비한다. 일반 `workflow.execute`는 public marker가 있으면 DB·Redis·외부 I/O 전에 `conversation.task_contract_mismatch`로 fail-closed한다.

### Rationale

HTTP body, current inputs, raw history와 sanitized final projection은 서로 다른 표현·신뢰 단계이므로 각 단계의 자원 비용과 의미에 맞는 상한을 적용한다. Client는 서버 계약을 선제적으로 지켜 한 번의 oversized 응답이 대화를 영구적으로 중단하지 않게 한다. Task name과 queue를 versioning하면 mixed Worker 배포에서 새 producer가 구 consumer에 도달하는 것을 구조적으로 차단하고, 잘못 라우팅된 payload도 새 일반 Worker가 fail-closed한다.

### Affected Files

- `apps/shared/domain/public_chat_history.py`
- `apps/gateway/middleware/public_conversation_cors.py`
- `apps/gateway/api/v1/endpoints/run.py`
- `apps/shared/services/workflow_task_publisher.py`
- `apps/shared/celery_app.py`
- `apps/gateway/services/deployment_service.py`
- `apps/workflow_engine/tasks.py`
- `docker/nginx/nginx.conf`
- `docker/workflow_engine/docker-entrypoint.sh`
- `scripts/dev.sh`
- `apps/client/app/embed/chat/*`
- Conversation Memory requirements, API, component와 test case 문서 및 관련 테스트

### Follow-up Review Notes

- Public input schema가 커지면 상한을 완화하기 전에 anonymous admission 비용과 reverse-proxy 설정을 함께 검토한다.
- Sanitizer 또는 provider framing이 바뀌면 trusted projection 구조 검사와 final exact-token test를 함께 갱신한다.
- Public task contract v2는 새 task name/queue와 explicit worker rollout evidence 없이는 기존 queue에 혼합하지 않는다.

## Implementation Decision: End-to-end admission and lifetime ownership

### Context

전용 `/chat`이 `deployment_version` 생략을 허용하면 Client가 조회한 deployment와 실행 대상을 결박하는 검사가 우회된다. Query embedding fan-out은 RAG 시작 전 한 번만 deadline을 확인해 여러 model provider를 순차 호출하는 중 absolute lifetime을 넘을 수 있었다. Gateway의 동기 Redis `SET`은 Redis 지연 시 단일 async event loop를 막았다. 표준 배포에는 strict rollout 환경변수 전달 경로가 없었고 Nginx는 Gateway보다 짧은 기본 upstream timeout과 별도 형식의 413을 반환했다.

### Options Considered

- 모든 public root route에 version을 즉시 필수화: legacy compatibility rollout을 깨므로 선택하지 않는다.
- RAG fan-out 전체 앞에서 deadline 한 번만 검사: 앞선 provider 지연 뒤 다음 provider를 시작할 수 있어 선택하지 않는다.
- 동기 Redis client에 socket timeout만 설정: event loop blocking 자체가 남으므로 선택하지 않는다.
- Nginx body 상한을 Gateway보다 크게 해 413을 모두 Gateway에 위임: proxy 단계의 익명 자원 admission을 잃으므로 선택하지 않는다.
- 전용 `/chat` version 필수화, provider별 guard, async bounded Redis, 표준 rollout env wiring과 proxy-owned 동일 오류 계약: 각 경계가 소유한 상태와 lifetime을 직접 검증하므로 선택한다.

### Final Decision

1. `/chat`은 positive integer `deployment_version`을 필수로 검증하고 누락도 `conversation.deployment_version_invalid`로 종료한다. Compatibility root route의 optional version은 유지한다.
2. Query embedding execution request는 deadline guard를 전달받고 각 model group provider invoke 직전에 실행한다. Guard 예외는 provider failure policy 밖에서 전파한다.
3. Gateway transient history 저장은 async Redis `SET NX EX`와 2초 timeout을 사용한다. Timeout·연결 오류는 content-free `conversation.history_store_unavailable`로 분류한다.
4. Compose와 Helm values→ConfigMap→Gateway env가 `PUBLIC_CHAT_CONVERSATION_ROLLOUT_MODE`를 전달한다.
5. Nginx Public `/chat`은 610초 read/send timeout을 사용하고 자체 413에도 Gateway와 같은 safe JSON code, no-store와 no-referrer headers를 반환한다.

### Rationale

Version, lifetime, storage availability와 HTTP admission은 각각 API adapter, provider invocation, async infrastructure adapter와 reverse proxy가 검증해야 하는 불변조건이다. 이 경계를 실제 소비 직전에 닫아 stale deployment 실행, deadline 이후 외부 호출, event loop 정지와 proxy/Gateway 오류 계약 drift를 방지한다.

### Affected Files

- `apps/gateway/api/v1/endpoints/run.py`
- `apps/gateway/services/deployment_service.py`
- `apps/shared/services/public_chat_history_transient_store.py`
- `apps/workflow_engine/application/query_embedding_execution.py`
- `apps/workflow_engine/workflow/nodes/llm/llm_node.py`
- `docker/docker-compose.yml`
- `docker/nginx/nginx.conf`
- `infra/helm/moduly/*`
- Conversation Memory requirements, API, component, test case 문서와 관련 회귀 테스트

### Follow-up Review Notes

- Public request lifetime이 바뀌면 Gateway timeout, Celery expires, Worker task deadline과 Nginx read/send timeout을 한 계약으로 갱신한다.
- Redis library 변경 시 async cancellation과 timeout 예외가 event loop를 막거나 내부 오류를 public response에 노출하지 않는지 검토한다.
- 새 multi-provider fan-out은 loop 시작 전 검사만으로 만족하지 말고 각 외부 invoke 직전 deadline guard를 호출한다.

## Implementation Decision: Helm lifetime parity and routing-learning suppression

### Context

Docker Nginx에만 610초 timeout을 설정하면 Helm Ingress의 기본 upstream timeout이 Public absolute deadline보다 먼저 끝날 수 있다. 또한 `suppress_content_persistence`는 WorkflowLogger content만 닫고 model-routing judge가 익명 입력에서 만든 feature text/vector/hash learning label은 DB에 계속 저장했다.

### Options Considered

- Helm timeout은 operator 문서에만 맡김: 기본 chart가 비용 발생 중인 Worker보다 먼저 연결을 끊을 수 있어 선택하지 않는다.
- Public request deadline을 일반 Ingress 기본값 60초로 축소: 기존 동기 RAG/provider 실행 계약을 크게 바꾸므로 선택하지 않는다.
- Public routing에서 model selection 자체를 끔: content-free selection과 usage 기능까지 불필요하게 잃으므로 선택하지 않는다.
- Helm에 610초 read/send timeout을 렌더링하고 Public execution에서 content-derived learning label만 억제: lifetime과 비저장 경계를 최소 변경으로 완결하므로 선택한다.

### Final Decision

1. Helm Ingress는 `ingress.publicChatTimeoutSeconds`를 필수로 읽고 600초 이하를 template render 단계에서 거부한다.
2. 기본·production 값은 610초이며 ingress-nginx read/send timeout annotations로 렌더링한다. 다른 operator annotation은 보존하지만 timeout key는 검증된 chart value가 소유해 더 짧은 arbitrary override를 허용하지 않는다.
3. `suppress_content_persistence` execution은 model-routing judge와 model selection·content-free usage를 유지하되 `queue_runtime_judge_label`을 호출하지 않는다.
4. Suppressed learning은 원문 없이 `suppressed_content_persistence` safe status로만 표시한다.

### Rationale

HTTP client lifetime과 durable data minimization은 Docker 단일 경로가 아니라 모든 지원 배포와 모든 content-derived write 경로에 적용해야 한다. Proxy가 먼저 종료된 뒤 외부 비용이 계속되는 상황과 익명 입력 파생 벡터가 학습 데이터로 남는 상황을 각각 Helm render 및 LLM learning write 직전에 차단한다.

### Affected Files

- `infra/helm/moduly/templates/ingress.yaml`
- `infra/helm/moduly/values.yaml`
- `infra/helm/moduly/values-production.yaml`
- `apps/workflow_engine/workflow/nodes/llm/llm_node.py`
- Gateway architecture 및 Workflow Engine LLM node 회귀 테스트
- Conversation Memory requirements, API, component와 test case 문서

### Follow-up Review Notes

- ingress-nginx가 아닌 controller는 `ingress.annotations`로 동일하거나 더 긴 controller-specific timeout을 설정하고 rendered manifest를 배포 전에 검증한다.
- Public request deadline을 변경할 때 Docker Nginx와 Helm Ingress timeout을 함께 갱신한다.
- 새 model-routing learner write 경로는 `suppress_content_persistence`를 우회하지 않는지 검토한다.

## Implementation Decision: Worker consume, Judge attempt and legacy-control isolation

### Context

Gateway Redis 저장에만 timeout을 적용하면 Worker의 동기 atomic consume은 Redis가 연결을 유지한 채 응답하지 않을 때 Celery slot을 absolute deadline 뒤까지 점유할 수 있다. LLM main provider와 RAG embedding에 deadline guard가 있어도 그 전에 실행되는 model-routing Judge의 최초·compact retry provider 호출은 같은 lifetime 밖에서 시작할 수 있었다. 또한 Gateway가 safe `memory_mode=false`, `conversation_id=null`을 보내더라도 전용 task가 위조된 true/non-null 값을 검증하지 않고 canonical context에 보존하면 legacy execution-log Memory 조회가 Public prompt에 섞일 수 있다.

### Options Considered

- Redis consume에 Public 600초 deadline만 적용: 한 stalled request가 장시간 Worker slot을 점유하므로 선택하지 않는다.
- 동기 consume을 thread future로 감싸기: timeout 후에도 blocking thread와 socket 작업을 중단할 수 없어 선택하지 않는다.
- Runtime Judge 앞에서 한 번만 deadline 검사: incomplete compact retry 직전 만료를 놓치므로 선택하지 않는다.
- Legacy control을 canonicalization에서 safe 값으로 덮어쓰기만 함: forged producer와 contract drift를 숨기므로 선택하지 않는다.
- Redis socket timeout을 남은 deadline과 2초로 합성하고, Judge provider attempt마다 guard를 호출하며, unsafe legacy control을 DB 전에 거부한 뒤 safe sentinel도 제거: 각 실제 소비 경계에서 fail-closed하므로 선택한다.

### Final Decision

1. Worker history consume은 request-local Redis client의 connect/read timeout을 `min(2초, task_deadline - monotonic_now)`로 설정하고 atomic EVAL 뒤 client를 닫는다.
2. Store unavailable timeout 뒤 absolute deadline이 남아 있을 때만 기존 소비 전 bounded Celery retry를 허용한다. Deadline에 도달하면 `conversation.request_expired`로 non-retryable 종료한다.
3. `ModelRoutingRuntimeJudge`는 optional deadline guard를 최초 provider와 incomplete compact retry 직전에 각각 호출한다.
4. LLMNode는 공통 Public external-I/O deadline guard를 Judge에 주입하고 `NonRetryableWorkflowError`를 judge unavailable fallback으로 변환하지 않는다.
5. 전용 Public task는 `memory_mode=true` 또는 non-null `conversation_id`를 Session 생성 전에 `conversation.task_contract_mismatch`로 거부한다. 허용된 false/null sentinel은 canonical execution context에서 제거한다.

### Rationale

Absolute lifetime과 Public 데이터 격리는 요청 진입 시점의 한 번짜리 검증이 아니라 blocking I/O와 content-bearing provider 호출 및 legacy Memory 진입 직전의 불변조건이다. Short-lived socket timeout은 Worker slot을 bounded하게 회수하고 provider-attempt guard는 retry 비용을 막으며 task-level legacy validation은 위조·구형 producer가 server-side history를 다시 활성화하지 못하게 한다.

### Affected Files

- `apps/shared/pubsub.py`
- `apps/shared/services/public_chat_history_transient_store.py`
- `apps/workflow_engine/tasks.py`
- `apps/workflow_engine/services/model_routing_runtime_judge.py`
- `apps/workflow_engine/workflow/nodes/llm/llm_node.py`
- Conversation Memory requirements, API, component, test case 문서와 관련 회귀 테스트

### Follow-up Review Notes

- Redis client/library 변경 시 connect/read timeout과 retry policy의 총 대기 시간이 Public deadline보다 길어지지 않는지 검토한다.
- Runtime Judge에 새 provider attempt 또는 adjudication을 추가하면 같은 guard를 실제 invoke 직전에 호출한다.
- Public task contract version을 올릴 때 safe legacy sentinel을 완전히 제거할 수 있는 Gateway/Worker rollout 순서를 검토한다.

## Implementation Decision: ASGI ingress에서 시작하는 단일 Public request lifetime

### Context

Gateway가 budget admission, legacy secret migration과 runtime preflight를 마친 뒤 600초 deadline을 만들면 reverse proxy의 610초 lifetime은 이미 그만큼 소비된 상태다. Provider와 Worker는 늦게 생성된 deadline을 유효하다고 판단하므로 Client가 504를 받은 뒤에도 외부 I/O를 시작할 수 있다.

### Options Considered

- Task publish 직전에 600초 deadline 생성: pre-dispatch 소요 시간을 포함하지 못하므로 선택하지 않는다.
- FastAPI endpoint 진입 때 생성: middleware의 request-body buffering과 JSON parsing 시간을 포함하지 못하므로 선택하지 않는다.
- Reverse proxy timeout을 더 크게 늘림: application lifetime과 edge lifetime의 불일치를 숨길 뿐 동일한 race를 제거하지 못하므로 선택하지 않는다.
- ASGI middleware가 body buffering 전에 deadline을 한 번 생성하고 이후 모든 경계가 남은 시간만 사용: 전체 요청 lifetime을 동일한 clock boundary로 결박하므로 선택한다.

### Final Decision

1. Public root와 `/chat` POST는 `PublicConversationCorsBoundaryMiddleware`가 request body를 읽기 전에 UTC-aware absolute deadline을 scope state에 기록한다.
2. Endpoint는 그 값을 `DeploymentService`로 전달하고 Public transient execution은 새 deadline을 생성하지 않는다.
3. Gateway는 consumer mapping, budget, secret migration, preflight와 publish 경계에서 만료를 재검사한다. 만료되면 content-free 504로 종료하고 이후 부수효과를 시작하지 않는다.
4. Raw history TTL은 600초와 남은 정수 lifetime 중 더 짧은 값이며 Redis I/O timeout은 2초와 남은 lifetime 중 더 짧은 값이다.
5. Celery `expires`, execution context, Worker와 Gateway result polling은 모두 같은 absolute deadline을 사용한다.

### Rationale

Public 요청의 실제 lifetime은 task publish가 아니라 edge가 request를 받은 순간 시작한다. 하나의 absolute value를 transport, admission, broker와 Worker에 전달하면 각 단계의 지연이 다음 단계의 실행 예산에서 자동으로 차감되고, proxy 종료 뒤 새 external effect가 시작되는 경로를 닫을 수 있다.

### Affected Files

- `apps/gateway/middleware/public_conversation_cors.py`
- `apps/gateway/api/v1/endpoints/run.py`
- `apps/gateway/services/deployment_service.py`
- Gateway API/service 회귀 테스트
- Conversation Memory requirements, API, component, test case와 architecture 문서

### Follow-up Review Notes

- Public request body를 처리하는 middleware 순서를 바꿀 때 deadline stamp가 모든 buffering보다 앞서는지 검토한다.
- 새 Public pre-dispatch I/O 또는 retry를 추가하면 남은 deadline으로 timeout을 제한한다.
- Reverse proxy timeout은 application deadline보다 길게 유지하되 그 차이를 application lifetime 연장에 사용하지 않는다.

## Implementation Decision: 편집 graph, 공통 node deadline과 strict revision 재활성화

### Context

Public conversation consumer는 현재 편집 graph에서 선택되지만 Client deployment 요청이 `graph_snapshot`을 생략하면 Gateway는 마지막 저장 draft를 검증해 config와 graph가 어긋날 수 있다. Worker의 external-effect executor와 일부 LLM/RAG 호출만 deadline을 검사하면 직접 remote fetch를 수행하는 File Extraction 같은 node는 absolute deadline 뒤에도 외부 I/O를 시작할 수 있다. 또한 strict rollout 검증이 일반 create/toggle에만 있으면 browser-access policy revision의 active 생성이 legacy mapping 없는 snapshot을 새 active version으로 만들 수 있다.

### Options Considered

- 배포 전에 draft save를 강제: 저장 실패·동시 편집과 별도 lifecycle을 추가하고 preflight/create 사이 snapshot 일치를 직접 보장하지 못해 선택하지 않는다.
- 외부 I/O node를 발견할 때마다 개별 deadline guard 추가: 새 node가 누락될 수 있고 동일 불변조건이 여러 구현에 분산되어 선택하지 않는다.
- Browser revision 생성 후 public info/runtime에서 legacy capability로 완화: strict mode의 활성화 차단 계약을 깨므로 선택하지 않는다.
- 현재 graph를 두 deployment 요청에 결박하고, 모든 node의 공통 실행 직전에 deadline을 검사하며, active browser revision guard에서 strict consumer 계약을 재검증: 세 경계에서 각각 원인을 직접 차단하므로 선택한다.

### Final Decision

1. Client는 배포 동작을 시작할 때 현재 `{nodes, edges}`를 하나의 graph snapshot으로 잡고 동일 값을 preflight와 create에 보낸다.
2. Workflow Engine은 각 node의 `execute` 호출 직전에 task deadline을 검사한다. 만료 시 node 구현을 호출하지 않고 non-retryable `external_effect.deadline_exceeded`로 종료한다.
3. Browser-access revision은 active 요청일 때 source를 잠근 뒤 public conversation config와 graph를 strict rollout 설정으로 검증하고, 그 다음 mail/runtime activation preflight와 revision row 생성을 진행한다.
4. Inactive browser revision은 staging을 허용하지만 이후 active 전환은 동일한 strict 검증을 통과해야 한다.

### Rationale

선택·검증·저장에 같은 graph를 사용해야 canonical consumer location이 안정적이다. Deadline은 개별 provider adapter 목록이 아니라 공통 node 실행 경계에 있어야 direct/future external I/O에도 fail-closed한다. Strict rollout은 활성 상태로 들어가는 모든 lifecycle 경로에 동일해야 우회 activation이 생기지 않는다.

### Affected Files

- `apps/client/app/features/workflow/hooks/useDeployment.ts`
- `apps/client/app/features/workflow/components/editor/NodeCanvas.tsx`
- `apps/gateway/application/deployment/browser_access_errors.py`
- `apps/gateway/adapters/deployment_browser_access_activation.py`
- `apps/gateway/composition/deployment.py`
- `apps/gateway/api/v1/endpoints/deployment.py`
- `apps/workflow_engine/workflow/core/workflow_engine.py`
- 관련 Client, Gateway, Workflow Engine 회귀 테스트
- Chatbot Deployment와 Conversation Memory 문서 및 architecture

### Follow-up Review Notes

- 새로운 deployment UI는 preflight와 create에 서로 다른 graph source를 사용하지 않는지 검토한다.
- 새 node가 direct network/file/provider I/O를 추가해도 공통 deadline 경계를 우회하지 않아야 한다.
- Public deployment의 신규 activation/revision lifecycle은 strict consumer contract와 active pointer mutation 순서를 함께 검토한다.

## Implementation Decision: 혼합 Gateway의 Public chat fallback과 owner-memory 격리

### Context

Compatibility rolling deployment에서는 구·신 Gateway Pod가 같은 Service 뒤에 동시에 존재할 수 있다. `/info`가 신 Pod의 `client_history_v1` capability를 반환한 뒤 `/chat` POST가 구 Pod에 도달하면 구 버전에는 route가 없어 404가 발생하며, session affinity가 없는 배포에서는 공개 대화가 간헐적으로 중단된다. 그러나 history와 `conversation_id`가 모두 없는 legacy root는 구 Gateway가 앱 소유자 `user_id` 범위의 과거 성공 run을 Memory로 조회하게 만들어 서로 다른 익명 방문자의 대화를 섞을 수 있다.

### Options Considered

- Gateway Service에 session affinity 적용: capability 조회와 실행이 같은 세대로 간다는 보장이 rollout 인프라 설정에 결합되고 모든 환경에 동일하게 적용하기 어려워 선택하지 않는다.
- `/chat` 404를 즉시 사용자 오류로 종료: 정상 rolling deployment에서 높은 확률로 가용성 단절을 만들므로 선택하지 않는다.
- `conversation_id` 없는 history-free root: 구 Gateway의 owner 범위 Memory fallback으로 익명 방문자 간 대화가 섞일 수 있어 선택하지 않는다.
- Page/session 단위 격리 ID를 재사용: 구 Gateway server-side Memory를 익명 대화 저장소로 계속 사용하게 되므로 선택하지 않는다.
- 요청마다 secure-random 일회성 격리 ID를 포함한 root를 사용: 구 Gateway의 owner fallback을 차단하고 새 Gateway가 control을 제거할 수 있어 선택한다.

### Final Decision

1. Embed Chat은 `client_history_v1`으로 만든 `/chat` 요청이 404일 때만 legacy root 요청을 정확히 한 번 보낸다.
2. Capability가 legacy이거나 `/chat` 404 fallback인 모든 root 요청은 같은 current inputs와 `deployment_version`, 요청마다 새로 생성한 `public-once-v1:<UUIDv4>` 격리 ID만 `inputs.conversation_id`로 보낸다. `conversation`과 `memory_mode`는 보내지 않는다.
3. 격리 ID는 React state나 browser storage에 보관·재사용하지 않는다. Secure UUID를 만들 수 없으면 legacy root를 보내지 않고 fail-closed한다.
4. 새 Gateway compatibility adapter는 격리 ID를 다른 legacy control과 함께 제거하고 무상태 실행한다. 구 Gateway는 이를 해당 요청만의 scope로 사용해 앱 소유자 범위 과거 run을 조회하지 않는다.
5. Legacy root의 실패에는 다시 fallback하지 않으며 `/chat`의 404 외 status는 기존 처리 계약을 유지한다.
6. Deployment version 409는 기존처럼 info를 no-store로 갱신하고 이전 history를 폐기한 뒤 현재 입력을 한 번 재시도한다. 갱신된 capability가 `client_history_v1`이고 그 `/chat`이 구 Pod의 404를 받는 경우에도 동일한 1회 fallback을 적용한다.

### Rationale

404 route mismatch는 rolling deployment 중 transport generation 차이에서 발생한다. Root에는 전체 history가 아니라 current inputs만 전달하고 매 요청의 격리 ID를 재사용하지 않으므로 구 Gateway의 owner-memory 혼합과 대화 연속 저장을 동시에 차단한다. 새 Gateway는 ID를 제거해 현재 무상태 계약을 유지하며, 비재귀 1회 처리로 요청 폭증이나 무한 재시도를 방지한다.

### Affected Files

- `apps/client/app/embed/chat/publicConversationHistory.ts`
- `apps/client/app/embed/chat/[urlSlug]/page.tsx`
- `apps/client/app/embed/chat/publicConversationHistory.test.ts`
- `apps/client/app/embed/chat/[urlSlug]/page.test.tsx`
- Chatbot Deployment와 Conversation Memory requirements/component/test case 문서

### Follow-up Review Notes

- Compatibility mode 제거 시 active Client cache와 구 Gateway Pod가 모두 사라진 뒤 fallback 제거 여부를 검토한다.
- Public route status 계약이 바뀌면 404가 route 미지원 외 application 오류를 포함하는지 다시 검토한다.
- Gateway routing에 capability generation affinity를 도입하면 Client fallback과 중복되지 않는지 확인한다.
- Legacy root ID를 session/page 단위로 재사용하거나 browser storage에 보관하지 않는지 Client 변경 시 검토한다.
