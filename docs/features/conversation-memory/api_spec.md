# Conversation Memory API Specification

Status: Implemented Public client-held history; authenticated durable Memory target

## Contract Status

Public Chatbot의 현재 계약은 [ADR-0074](../../decisions/ADR-0074-public-chatbot-client-held-history.md)의 client-held history 방식이다. `POST /api/v1/run-public/{url_slug}/chat` 외 Public Conversation lifecycle route는 등록하지 않는다. 서버 durable Session/Turn/Entry/Transcript API와 아래 internal application contract는 authenticated internal Chatbot 후속 target이다.

현재 Public Client는 'inputs.memory_mode', 'inputs.conversation_id', Conversation bearer capability를 보내지 않는다.

## Authentication And Scope

| Surface | Principal | Conversation access |
| --- | --- | --- |
| Public chatbot | 사용자 identity 없음 | Client가 보낸 untrusted bounded history만 사용. 서버 Session/Access Grant 없음 |
| Authenticated internal Chatbot | 로그인 사용자 | Current user + organization + 별도 내부 Chatbot 이용 권한 + internal deployment/session scope. 후속 구현 전 미지원 |
| Workflow editor test / batch trigger | surface별 기존 principal | Conversation Session 자동 생성 없음 |
| Subworkflow | Parent runtime | Parent의 content-persistence suppression과 bounded context를 상속 |

Public history는 인증·인가·resource provenance·credential·billing principal 또는 audit actor가 아니다. 로그인 cookie나 임의 Authorization header가 함께 와도 private Memory 또는 Knowledge 권한으로 승격하지 않는다. Public run은 Conversation bearer token, session cookie, browser-generated conversation ID와 lifecycle idempotency key를 사용하지 않는다.

Public iframe document는 relative same-origin으로 `POST /api/v1/run-public/{url_slug}/chat`을 호출한다. 응답과 endpoint 진입 전 validation error는 `Cache-Control: no-store`, `Referrer-Policy: no-referrer`이며 CORS grant를 제공하지 않는다. Client는 대화 원문을 React memory에만 두고 URL, localStorage, sessionStorage, audit, trace와 metric label에 남기지 않는다.

Authenticated internal Chatbot은 Public route에 optional login을 붙이지 않고 별도 authentication/authorization, CSRF/Origin, storage namespace와 retention 계약으로 구현한다.

## HTTP Surface

### Public Conversation

| Method | Path | 목적 | 상태 |
| --- | --- | --- | --- |
| POST | `/api/v1/run-public/{url_slug}/chat` | 현재 inputs와 Client가 보낸 bounded history로 Public Chatbot 실행 | 구현 |

다음 legacy target route는 등록하지 않으며 '404'다.

- `POST /run-public/{url_slug}/conversations`
- `POST /run-public/{url_slug}/conversation/close`
- `POST /run-public/{url_slug}/conversation/reset`
- `DELETE /run-public/{url_slug}/conversation`
- `GET /run-public/{url_slug}/conversation/transcript`
- `GET /run-public/{url_slug}/conversation/turns/{turn_id}`
- `GET /run-public/{url_slug}/conversation/purge-status`

### Authenticated Conversation

아래 `/internal-chatbots` prefix는 별도 `authenticated_internal_chatbot` deployment surface가 활성화된 뒤의 목표 prefix다. 최종 route naming은 내부 Chatbot 기능 문서가 소유한다. Public Chatbot route나 current generic `/deployments/{deployment_id}/run`에 optional login을 붙여 private Knowledge를 허용하는 방식으로 구현하지 않는다. 같은 시각 Chatbot component는 재사용할 수 있지만 API adapter, authentication/CORS/Origin, deployment access policy와 session namespace는 분리한다.

| Method | Path | Purpose | Authorization |
| --- | --- | --- | --- |
| POST | `/api/v1/internal-chatbots/{deployment_id}/conversations` | 내부 Chatbot session 생성 | 로그인 + 별도 내부 Chatbot 이용 권한 |
| POST | `/api/v1/internal-chatbots/{deployment_id}/run` | 내부 Chatbot run에 target `conversation` envelope 추가 | 로그인 + 별도 내부 Chatbot 이용 권한 |
| GET | `/api/v1/internal-chatbots/{deployment_id}/conversations/{session_id}/turns/{turn_id}` | Turn 상태/완료 결과 조회 | 현재 subject/session scope |
| GET | `/api/v1/internal-chatbots/{deployment_id}/conversations/{session_id}/transcript` | Redacted transcript 조회 | 현재 subject/session scope |
| POST | `/api/v1/internal-chatbots/{deployment_id}/conversations/{session_id}/close` | Session close | 현재 subject/session scope |
| POST | `/api/v1/internal-chatbots/{deployment_id}/conversations/{session_id}/reset` | Close + 새 session 생성 | 현재 subject/session scope |
| DELETE | `/api/v1/internal-chatbots/{deployment_id}/conversations/{session_id}` | 접근 차단과 purge 요청 | 현재 subject/session scope |
| GET | `/api/v1/internal-chatbots/{deployment_id}/conversation-purges/{purge_request_id}` | Purge 상태 조회 | 현재 subject/deployment scope |

Authenticated endpoint는 client-supplied subject, organization, workflow와 internal deployment scope를 신뢰하지 않는다. Gateway가 현재 user와 active organization, App/Workflow/Deployment를 canonical하게 구성한다. 내부 Chatbot 접근 권한이 없으면 workflow 편집/관리 권한만으로 실행을 허용하지 않는다.

## HTTP Status And Idempotency

Public run은 기존 동기 deployment response를 유지한다.

| Condition | Status | Code |
| --- | ---: | --- |
| 정상 Public Chatbot 실행 | 200 | 기존 deployment result |
| malformed history/envelope | 422 | conversation.* |
| Public Chatbot envelope 누락 | 422 | conversation.history_required |
| legacy memory_mode/conversation_id | 422 | conversation.legacy_control_forbidden |
| `/chat` deployment_version 누락 또는 형식 오류 | 422 | conversation.deployment_version_invalid |
| 조회한 version과 active deployment 불일치 | 409 | conversation.deployment_version_changed |
| 현재 inputs만으로 4,096 token 초과 | 422 | conversation.current_input_too_large |
| current inputs canonical JSON이 131,072 bytes 초과 | 422 | conversation.inputs_too_large |
| Public `/chat` HTTP body가 393,216 bytes 초과 | 413 | conversation.request_too_large |

Public history contract는 Conversation bearer token, If-Match와 lifecycle Idempotency-Key를 사용하지 않는다. 대신 Public Client가 public info의 정수 `version`을 request body `deployment_version`으로 보내 active deployment와 결박한다. Authenticated internal session mutation의 status/idempotency 계약은 후속 API activation 전에 확정한다.

## Session Models (Authenticated Internal Target)

### No Public Conversation Session

Public Session과 Access Grant response는 존재하지 않는다. 첫 요청은 `conversation.history=[]`를 보내며 reset/new conversation은 Client가 local history를 비운다.

### Create Authenticated Conversation Response

```json
{
  "conversation": {
    "session_id": "cvs_opaque_id",
    "lifecycle_revision": 1,
    "memory_contract_version": "memory-v1",
    "expires_at": "2026-08-01T00:00:00Z"
  }
}
```

`session_id`는 organization/user/deployment scope 밖에서 사용할 수 없다. UUID 여부나 DB primary key를 API contract로 노출하지 않는다.

## Run Request Envelope

Legacy처럼 runtime metadata를 업무 `inputs` 안에 넣지 않는다.

### Public Run

```json
{
  "inputs": {
    "question": "현재 질문"
  },
  "conversation": {
    "history": [
      {"role": "user", "content": "이전 질문"},
      {"role": "assistant", "content": "이전 답변"}
    ]
  }
}
```

- 첫 요청은 `history: []`다.
- message key는 정확히 `role`, `content`만 허용한다.
- role은 완료된 `user` → `assistant` pair 순서만 허용한다.
- message content는 Unicode scalar 기준 최대 32,768 characters이고 history JSON은 UTF-8 최대 131,072 bytes다.
- current `inputs` canonical JSON은 UTF-8 최대 131,072 bytes이며 이 상한은 tokenizer 호출 전에 검증한다.
- endpoint 전체 HTTP request body는 최대 393,216 bytes이며 JSON parsing 전에 검증한다.
- 최대 20 turn이며 서버가 현재 inputs와 함께 4,096-token 상한을 다시 적용한다.
- 오래된 context 제거는 완료 turn 두 message 단위로 수행한다.
- Authorization, Conversation token, `Idempotency-Key`, `If-Match`는 Public history 계약에 사용하지 않는다.

### Authenticated Run

```json
{
  "inputs": {
    "question": "내부 출장 규정을 알려주세요"
  },
  "conversation": {
    "session_id": "cvs_opaque_id",
    "expected_lifecycle_revision": 1
  }
}
```

`Idempotency-Key` header는 public run과 동일하게 필수다. Gateway가 이를 canonical request ID로 정규화하며 body의 임의 request ID는 계약에 포함하지 않는다.

### Success Response

```json
{
  "status": "success",
  "results": {
    "answer": "..."
  },
  "conversation": {
    "session_id": "cvs_opaque_id",
    "turn_id": "turn_opaque_id",
    "turn_sequence": 4,
    "lifecycle_revision": 1,
    "content_revision": 4,
    "memory_status": "applied"
  }
}
```

Public response는 `session_id`를 생략하거나 public-safe opaque reference만 반환한다. `memory_status`는 `applied`, `not_requested`, `degraded` 같은 safe enum만 허용하며 denied source identity/count와 provider detail을 포함하지 않는다.

Mapped user/final assistant turn write는 conversational surface에서 required다. CompleteTurn이 실패하면 `success` 또는 `memory_status=applied`를 반환하지 않는다. Provider/execution 결과가 durable하게 남아 있으면 retry/reconciliation은 해당 결과를 재사용해 CompleteTurn만 idempotent하게 수행하고 provider와 arbitrary node side effect를 다시 실행하지 않는다.

### Accepted Turn Response (Authenticated Internal Target)

동기 wait budget 안에 완료되지 않은 정상 접수는 timeout 오류로 위장하지 않고 다음 `202 Accepted`를 반환한다.

```json
{
  "status": "accepted",
  "conversation": {
    "turn_id": "turn_opaque_id",
    "turn_state": "pending_dispatch",
    "status_path": "/api/v1/.../conversation/turns/turn_opaque_id"
  }
}
```

Turn status는 `pending_dispatch | queued | running | completed | failed | cancelled`만 노출한다. Pending/failed detail에는 queue name, Worker identity, broker error와 raw exception을 넣지 않는다.

## Transcript Model (Authenticated Internal Target)

```json
{
  "conversation": {
    "state": "active",
    "lifecycle_revision": 1,
    "content_revision": 4,
    "expires_at": "2026-08-01T00:00:00Z"
  },
  "turns": [
    {
      "turn_id": "turn_opaque_id",
      "sequence": 1,
      "state": "completed",
      "user": {"content": "redacted display content"},
      "assistant": {"content": "redacted display content"},
      "created_at": "2026-07-11T10:00:00Z"
    }
  ],
  "next_cursor": null
}
```

- Transcript는 bounded page size와 opaque cursor를 사용한다.
- MBA-317의 안전한 빈 projection도 같은 response envelope을 사용해 `conversation.state`, lifecycle/content revision, 실제 접근 `expires_at`, 빈 `turns`와 `next_cursor`를 반환한다. 내부 application DTO 이름이나 legacy `status/entries` shape를 공개 계약으로 노출하지 않는다.
- Raw prompt, Memory summary, Data Dependency, private source identity와 authorization reason을 반환하지 않는다.
- Public transcript를 지원하면 해당 public session에서 생성된 redacted display turn만 반환한다.
- Failed/cancelled turn은 state, timestamp와 safe failure reason만 표시한다. Authenticated owner에게 redacted user display entry를 반환할 수 있지만 public transcript에는 failed assistant content와 partial output을 반환하지 않는다.
- Transcript가 보인다는 사실이 같은 turn이 현재 LLM Memory Context에 포함된다는 뜻은 아니다.
- Closed session은 retention 기간 동안 authenticated owner 또는 transcript-only로 제한된 public grant에 redacted transcript를 반환할 수 있지만 runtime context, turn write와 reset은 차단한다. Privacy delete는 transcript-only grant로도 허용하고 grant를 즉시 revoke한다. Delete-pending/deleted session은 transcript 대신 resource-hiding response를 반환한다.

## Lifecycle Requests (Authenticated Internal Target)

Close/reset/delete는 stale client가 최신 session을 변경하지 못하도록 lifecycle revision을 body와 중복하지 않고 `If-Match` header로만 전달한다. Mutation idempotency는 별도 header를 사용하며 parsed lifecycle revision은 canonical idempotency fingerprint의 precondition 필드다.

Create/run/transcript/turn/close/reset response는 현재 session의 `ETag: "lifecycle-revision-N"`을 반환한다. Reset은 새 session ETag를 response header에 두고 old terminal revision은 body의 `previous.lifecycle_revision`에 포함한다.

```http
If-Match: "lifecycle-revision-1"
Idempotency-Key: req_lifecycle_unique_value
```

Reset success는 새 session 또는 새 public access token과 기존 session의 terminal 상태를 함께 반환한다. Delete는 접근 차단이 durable하게 기록된 뒤 성공해야 하며 물리 purge 완료를 거짓으로 동기 응답하지 않는다.

완료된 close/reset/delete의 exact scope, idempotency key와 fingerprint가 일치하면 grant/session의 temporal expiry보다 먼저 bounded replay를 판별한다. Delete는 물리 purge로 Grant/Session row가 삭제된 뒤에도 organization, stable App ID, operation, idempotency identity와 versioned access-token verifier에 결합된 content-free authorization tombstone으로 동일 요청임을 검증한다. Raw access token은 tombstone에 저장하지 않는다. 반환 lifecycle, revision과 ETag는 최초 성공 snapshot을 사용하므로 이후 close/delete/purge 진행 상태가 기존 응답을 바꾸지 않는다. 이는 응답 유실 복구만 허용하며, 새 idempotency key나 다른 fingerprint 또는 다른 verifier는 current grant/session usability를 다시 검증하거나 resource-hiding으로 거부한다. 일반 mutation idempotency record와 authorization tombstone은 최대 24시간이고 purge receipt 자체의 최대 8일 수명과 분리한다.

Reset response의 새 session revision과 이전 terminal revision은 다음처럼 분리한다.

```json
{
  "conversation": {
    "access_token": "opaque-secret",
    "lifecycle_revision": 1
  },
  "previous": {
    "lifecycle": "closed",
    "lifecycle_revision": 2
  }
}
```

```json
{
  "status": "delete_pending",
  "purge_request_id": "purge_opaque_id",
  "purge_receipt": "opaque-secret-returned-for-public-delete-only"
}
```

Authenticated caller는 `purge_request_id`와 현재 authentication으로 상태를 조회한다. Public delete는 grant를 즉시 revoke하므로 별도 short-lived `Authorization: Purge <receipt>` capability를 사용한다. Receipt source-of-truth는 verifier hash만 저장하고 URL에 넣지 않는다. Purge Job은 organization, stable App ID, 발급 시점의 deployment ID/version과 public audience snapshot을 receipt와 함께 보존한다. 상태 조회는 현재 URL slug를 stable App identity로 해석해 이 snapshot과 대조하므로 같은 App의 재배포는 receipt를 무효화하지 않지만 slug가 다른 App으로 재할당되면 resource-hiding으로 거부한다. 발급 시점 deployment snapshot은 purge ownership과 감사 근거이며 장기 조회 identity를 대신하지 않는다. Delete 응답 유실 뒤 같은 idempotency key에 receipt를 재반환해야 하면 최대 24시간의 encrypted response replay store와 versioned verifier-bound authorization tombstone을 사용한다. 상태 응답은 `pending | running | completed | completed_with_hold | retryable_failure | terminal_failure`와 safe timestamp/reason만 반환한다. Public purge는 발급 후 7일 안에 terminal 상태로 전이하며 receipt는 terminal 후 최소 24시간, 발급 후 최대 8일까지 유효하다. V1은 terminal 전 receipt expiry를 갱신하는 별도 상태 전이를 두지 않으므로 발급 시 receipt lifetime을 정확히 8일로 고정해 최악의 7일 terminal 경계에서도 이후 24시간을 보장한다. Public response는 legal-hold 내부 사유를 숨기고 retention notice만 표시한다. `completed_with_hold`는 runtime/compliance 격리가 durable하다는 뜻이며 물리 삭제나 `memory.session.purged` 완료를 뜻하지 않는다.

Response/log redaction middleware는 `access_token`/`purge_receipt` field와 versioned Access Grant/purge receipt가 포함된 자유 텍스트를 동일한 secret으로 처리한다.

## Internal Application Contracts

HTTP/Celery adapter는 다음 framework-independent command/query를 호출한다. 이름은 목표 capability이며 ORM CRUD API가 아니다.

### ResolveConversationSession

입력:

- canonical organization/app/workflow/deployment ID와 immutable version 또는 snapshot hash
- conversation mapping/Memory policy version
- verified execution subject 또는 public audience
- validated Access Grant hash/reference
- runtime surface (`public_chatbot | authenticated_internal_chatbot`). 초기 구현은 `public_chatbot`만 허용하며 `workflow_editor_test`는 별도 계약 전 거부한다.

출력:

- internal session reference
- lifecycle/content revision
- active turn 상태
- retention policy reference
- deployment/mapping/Memory policy binding
- memory contract/storage generation

기존 session binding과 요청 deployment version이 다르면 active deployment pointer로 자동 rebind하지 않는다. Public route는 session 존재를 숨기고 새 conversation을 요구한다. Authenticated internal route는 `memory.deployment_version_changed`와 safe new-session action을 반환한다.

### StartTurn

입력:

- session reference
- canonical request ID와 bounded fingerprint
- expected lifecycle revision
- redacted user display/memory projection
- server-derived RuntimeDataDependencyEnvelope

출력:

- turn ID/sequence/version
- started lifecycle revision
- durable dispatch job ID/state
- idempotent existing result 여부

### Dispatch Commands

`ClaimTurnDispatch`, `MarkTurnDispatchPublished`, `ObserveWorkflowAdmission`, `ObserveTurnExecutionState`, `ReconcileTurnDispatch`는 dispatch ID, expected state/version, fencing generation과 safe outcome을 입력으로 받는다. 각 command는 typed next state와 idempotent existing result를 반환한다.

Workflow `AdmitExecution(dispatch_id)`은 별도 Workflow application contract다. Memory는 Workflow admission lookup port로 acknowledgement 유실을 복구하지만 execution lease/heartbeat를 변경하지 않는다. Persistence/Celery adapter는 command 없이 dispatch/turn state를 직접 변경할 수 없다.

### BuildMemoryContext

입력:

- session/turn/node reference
- canonical execution subject/audience
- versioned node Memory policy
- server-issued `purpose=main_generation` ProviderExecutionCapability reference
- bounded token/turn budget

출력:

- opaque ContextMaterializationPlan handle
- short-lived context lease
- source/content revision
- safe decision metadata
- summary usage result 또는 degraded reason

`BuildMemoryContext`는 summary가 필요하면 provider/usage/projection side effect가 있으므로 순수 query로 간주하지 않는다. Raw context는 이 응답에 포함하지 않는다.

### ClaimMemoryContextLease

Main provider adapter가 provider 호출 직전에 사용하는 internal contract다.

입력:

- context handle과 lease
- session/turn/node invocation
- canonical execution subject/audience
- server-issued provider attempt ID
- server-issued ProviderExecutionCapability

출력:

- idempotent claimed raw bounded context 또는 fail-closed decision
- materialized context와 정확히 대응하는 server-derived RuntimeDataDependencyEnvelope
- authorization decision/resource/policy revision set와 principal kind
- provider attempt state와 claim deadline

Context handle은 raw text가 아니라 ordered entry/summary reference, version과 server-keyed content digest를 가진 short-lived materialization plan이다. Digest는 API/telemetry에 노출하지 않는다. Lease claim, ProviderExecutionCapability 검증, current authorization 재검증과 raw context materialization은 같은 trusted adapter operation이다. 같은 attempt retry는 idempotent하고 다른 attempt의 active claim 탈취, capability/expiry/invalidation/version mismatch는 context를 반환하지 않는다.

### ResolveProviderExecutionCapability

LLM Credentials domain의 authoritative internal port를 호출해 opaque capability identity/revision을 받는다. 상세 입력·출력 schema와 credential principal/permission decision revision 의미는 [LLM Credentials API Spec](../llm-credentials/api_spec.md#target-provider-execution-capability-contract)이 소유한다. Workflow Runtime은 provider effect 전에 server-issued provider attempt reference를 먼저 만들고 capability 발급 입력에 포함한다. Memory는 main-generation capability를 `BuildMemoryContext` 전에 받아 lease에 binding하고 claim에서 같은 identity/revision, session/deployment version, node invocation, purpose와 provider attempt를 다시 검증한다. Client, Access Grant와 Memory adapter는 provider/model/credential을 직접 선택하거나 capability scope를 확장할 수 없다. Credential revoke 또는 permission/relation/egress revision 변경 뒤 stale capability는 새 claim·reservation·provider attempt admission과 outbound call 전에 거부한다.

### MarkMemoryContextProviderStarted

Provider adapter는 outbound call 직전에 provider attempt ID와 expected attempt version을 전달해 `provider_started`를 durable하게 기록한다. Marker commit이 실패하면 provider를 호출하지 않는다. Claim 후 이 marker 전 crash만 claim expiry 뒤 새 lease/attempt로 재승인할 수 있다.

### RecordMemoryContextProviderOutcome

Provider attempt ID, expected version, terminal/unknown state와 normalized usage reference를 기록한다. `provider_started` 이후 timeout/crash/ambiguous outcome은 `outcome_unknown`이며 provider를 자동 재호출하지 않고 reconciliation 또는 safe node failure로 닫는다.

### RuntimeDataDependencyEnvelope

Workflow Runtime 내부 result/task contract이며 business `inputs`, client response와 arbitrary node payload에 넣지 않는다.

| Field | Contract |
| --- | --- |
| `envelope_version` | Server-supported version. Unknown version은 fail-closed |
| `completeness` | `complete` 또는 `unknown`. V1은 `complete`만 Memory write에 허용 |
| `dependencies` | Bounded, deduplicated server-derived dependency list. 값 dependency와 결과를 선택한 활성 control dependency의 필수 합집합. Complete empty list는 값·제어 source 영향이 없음을 producer가 확인한 경우에만 허용 |

각 dependency는 server-issued opaque dependency reference, source kind, organization safe reference, canonical resource/version safe reference, sensitivity, current authorization adapter가 사용할 authorization-safe lookup reference를 포함한다. Raw source title/path/URL/content/ACL, client subject, optional/required flag와 provider credential을 포함하지 않는다. Condition/Switch는 predicate와 선택 route, Loop는 iterable/bound/continue/termination 판단의 dependency를 active control context로 전달한다. 선택된 branch의 상수 output도 control dependency를 상속하고 선택되지 않은 branch의 값 dependency는 제외한다. Missing envelope, `unknown` completeness, unknown source kind, duplicate conflict, organization mismatch와 count/size cap 초과는 `memory.provenance_incomplete` 또는 `memory.provenance_invalid`로 처리하고 private/sensitive Memory write를 거부한다.

### EvaluateSourceAuthorization

Source-owning domain port의 bulk 응답은 각 dependency에 대해 `decision`, `principal_kind`, opaque `authorization_decision_revision`, `resource_revision`, `policy_revision`, `evaluated_at`을 반환한다. Source ACL이 있는 resource는 ACL revision을 decision revision에 반영한다. Public audience는 `anonymous_public_audience` principal kind를 사용하며 subject revision을 합성하지 않는다. 필요한 revision 또는 source result가 없으면 `unknown`으로 취급하고 entry를 제외한다.

### CompleteTurn

입력:

- session/turn/request/execution reference
- expected turn version와 started lifecycle revision
- mapped final assistant display/memory projection
- final RuntimeDataDependencyEnvelope
- execution terminal state

출력:

- terminal turn state
- content revision
- idempotent existing result 여부

Turn terminal state, Session active-turn/content revision, final entry/projection과 required outbox는 같은 UnitOfWork에서 commit/rollback한다.

## Node Memory Configuration

목표 graph schema 초안:

```json
{
  "memory": {
    "enabled": true,
    "channel": "conversation",
    "readSource": "conversation_turns",
    "writeMode": "none",
    "maxTurns": 5,
    "maxContextTokens": 1200,
    "strategy": "window_then_summary",
    "summaryModelPolicy": "inherit_node",
    "failurePolicy": "continue_without_memory"
  }
}
```

초기 제한:

- `enabled`: default false
- `channel`: bounded normalized identifier
- `readSource`: `conversation_turns | selected_nodes`
- `writeMode`: `none | node_output`
- `selectedNodeIds`: 같은 graph 안의 allowlist
- `maxTurns`: bounded positive integer
- `maxContextTokens`: server upper bound 이하
- `strategy`: `window | window_then_summary`
- `summaryModelPolicy`: 초기에는 `inherit_node`만 허용. 별도 organization model/credential preset ADR과 구현 전에는 `organization_default` 미지원
- `failurePolicy`: `continue_without_memory | fail_node`

Arbitrary SQL/filter, free-form provider options와 user-global scope는 허용하지 않는다. Numeric bound는 finite server configuration으로 제공하고 설정 누락 시 안전 기본값을 적용한다. Client 값으로 server upper bound를 완화할 수 없다.

## Error Contract

| Code | HTTP | Meaning |
| --- | ---: | --- |
| conversation.envelope_invalid | 422 | conversation object shape 오류 |
| conversation.history_invalid | 422 | message object/list shape 오류 |
| conversation.role_invalid | 422 | user/assistant 외 role |
| conversation.history_order_invalid | 422 | 미완성 또는 비교대 turn |
| conversation.content_invalid | 422 | 빈 값 또는 message 상한 초과 |
| conversation.turn_limit_exceeded | 422 | 20 turn 초과 |
| conversation.history_too_large | 422 | encoded envelope byte 상한 초과 |
| conversation.current_input_too_large | 422 | 현재 inputs만으로 token 상한 초과 |
| conversation.inputs_too_large | 422 | 현재 inputs canonical JSON byte 상한 초과 |
| conversation.request_too_large | 413 | Public `/chat` HTTP body byte 상한 초과 |
| conversation.token_count_unavailable | 422 | server token validation 실패 |
| conversation.history_required | 422 | Public Chatbot envelope 누락 |
| conversation.legacy_control_forbidden | 422 | legacy Public memory control 사용 |
| conversation.deployment_version_invalid | 422 | `/chat` deployment_version 누락 또는 positive integer가 아님 |
| conversation.deployment_version_changed | 409 | active deployment가 public info 조회 뒤 교체됨 |

Error detail은 request content, history, prompt, completion과 internal deployment/session 존재를 반사하지 않는다.

## Compatibility

- 새 Public Client와 Gateway는 `conversation.history` 계약으로 함께 배포한다.
- Public Chatbot 요청에 envelope이 없으면 `conversation.history_required`다.
- Public Chatbot의 legacy `inputs.memory_mode`와 `inputs.conversation_id`는 거부한다.
- WEBAPP/WIDGET 등 non-Chatbot public 실행은 기존 single-run contract를 유지한다.
- Authenticated internal Chatbot의 subject-bound legacy control은 후속 durable Memory cutover 전까지 유지한다.

## MBA-318 Public Conversation Deployment Contract

Public Chatbot deployment `config`:

```json
{
  "public_conversation": {
    "contract_version": "public_chat_conversation.v1",
    "history_consumer": {
      "node_id": "final-answer-llm",
      "container_path": []
    }
  }
}
```

`history_consumer`는 snapshot 안의 정확히 한 `llmNode` canonical location이어야 한다. Client는 top-level과 nested Loop LLM을 열거하고 중첩 대상은 각 Loop의 `{kind:"loop",node_id}` segment를 바깥쪽부터 `container_path`에 직렬화한다. `/chat`은 mapping 누락·not-found·non-LLM을 content-free `409 conversation.consumer_mapping_*`로 거부한다. Preflight/create는 제공된 mapping을 `422`로 검증하며 strict rollout에서는 누락도 거부한다.

`GET /api/v1/deployments/public/{url_slug}/info`는 Chatbot에 `public_conversation_contract: "client_history_v1" | "legacy_v0"`와 정수 `version`을 `Cache-Control: no-store`로 반환한다. 필드가 없는 구 Gateway는 Client가 `legacy_v0`로 취급한다. 새 Client는 root와 `/chat` body에 조회한 `deployment_version`을 포함한다. Gateway는 active deployment와 다르면 budget, transient store와 task publish 전에 `409 conversation.deployment_version_changed`로 종료한다. Client는 info를 다시 조회하고 이전 version history를 폐기한 뒤 현재 입력을 한 번만 재시도한다.

전용 `/chat` route는 `deployment_version` 생략을 허용하지 않는다. Reverse proxy가 393,216-byte body 상한을 먼저 적용해도 Gateway와 같은 content-free `413 conversation.request_too_large`와 no-store/no-referrer headers를 반환한다. Docker Nginx와 Helm Ingress의 proxy read/send timeout은 Public absolute request deadline 600초보다 긴 610초다.

Compatibility mode에서 root public Chatbot 요청은 legacy `memory_mode`/`conversation_id`를 제거한 무상태 실행으로 처리한다. `/chat` 요청의 legacy control은 계속 `422 conversation.legacy_control_forbidden`이다. Strict mode에서 root public Chatbot은 `422 conversation.history_required`다.

Gateway는 raw history를 600초 TTL의 일회성 Redis key에 저장하고 transient public task에는 opaque reference, absolute `public_request_deadline_at`과 Celery `expires`만 전달한다. Worker는 queued raw `public_chat_history`를 DB 접근 전에 거부하고 deadline과 canonical deployment/preflight를 검증한 뒤 Knowledge sync 후 reference를 atomic GET+DELETE로 소비한다. Celery hard deadline은 absolute public deadline을 넘지 않으며 Knowledge/provider 외부 I/O 직전에 다시 확인한다. `conversation.history_store_unavailable`만 소비가 확인되기 전 기존 Celery retry 대상이다. Invalid/missing/corrupt reference와 소비 뒤 오류는 자동 replay하지 않고 각각 내부 `conversation.history_unavailable` 또는 `conversation.history_replay_required`로 종료해 Client가 원래 history로 새 요청을 보내게 한다. Stale task는 `conversation.request_expired`, missing/malformed deadline은 `conversation.request_deadline_invalid`로 외부 I/O 전에 거부한다. 이 내부 code, reference와 deadline 값은 public API response에 반사하지 않는다.

Public transient dispatch는 `workflow.execute_public_chat.v1` task를 `workflow-public-chat-v1` queue로 publish한다. 새 Workflow Worker는 rollout 동안 일반 `workflow` queue와 이 전용 queue를 함께 소비한다. 구 Worker는 전용 queue를 구독하지 않으므로 Public payload를 실행하지 않는다. 일반 `workflow.execute`에 public history reference, stateless compatibility marker 또는 public actor marker가 잘못 도착하면 DB·Redis·Engine 접근 전에 내부 `conversation.task_contract_mismatch`로 종료한다.

History는 raw admission과 sanitizer 이후 final projection을 같은 validator로 중복 검사하지 않는다. Gateway raw request에 message/envelope 상한을 적용하고, Worker 내부 sanitizer가 pair 한쪽을 비우면 완료 pair 전체를 제거한다. Sanitizer가 만든 marker가 raw message 상한을 넘더라도 raw admission을 다시 적용하지 않으며 실제 provider framing을 포함한 final projection token 상한으로 제한한다.

Gateway의 transient history 저장은 async Redis와 bounded 2초 timeout을 사용한다. Query embedding fan-out은 model group마다 provider invoke 직전에 같은 Public absolute deadline guard를 실행하며 `safe_no_result` 정책도 deadline 만료를 provider 실패로 삼켜 다음 호출을 계속하지 않는다. Rollout mode는 Compose 환경변수와 Helm `gateway.env.PUBLIC_CHAT_CONVERSATION_ROLLOUT_MODE`에서 설정한다.

`suppress_content_persistence=true`인 Public execution은 model routing judge의 content-derived learning label을 queue하지 않는다. Model selection과 content-free usage 통계는 실행에 사용할 수 있지만 visitor input의 feature text/vector/hash를 durable learner dataset에 포함하지 않는다.
