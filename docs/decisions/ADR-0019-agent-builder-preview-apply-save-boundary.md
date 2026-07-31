# ADR-0019: Agent Builder preview apply/save boundary

Status: Superseded by ADR-0045

Related ADRs: [ADR-0008](ADR-0008-audit-action-naming-standard.md), [ADR-0009](ADR-0009-active-organization-header-context.md), [ADR-0010](ADR-0010-resource-access-403-404-policy.md), [ADR-0016](ADR-0016-permission-request-and-app-creation-permission.md), [ADR-0017](ADR-0017-knowledge-integration-provisional-implementation-baseline.md), [ADR-0018](ADR-0018-workflow-rag-anonymous-public-only-runtime.md)

## Context

Agent Builder는 Workflow Editor 안에서 사용자의 자연어 요청을 workflow draft로 바꾼다. 이 draft는 기존 workflow graph를 수정하거나 새 workflow graph를 제안할 수 있으므로, 저장 전 사용자가 실제 workflow 구조와 node 내부 설정을 확인할 수 있어야 한다.

초기 설계에서는 "approval 후 canvas local state 반영"과 "workflow 저장은 별도 save flow"를 분리하는 선택지가 있었다. 그러나 사용자가 이미 Preview Mode에서 draft graph와 Node Detail Panel을 읽기 전용으로 확인한 뒤 명시적으로 `적용 및 저장`을 선택한다면, 다시 별도 저장 action을 요구하는 흐름은 데모와 실제 사용 모두에서 중복 동작이 된다.

동시에 `적용 및 저장`이 workflow 실행, Knowledge Base retrieval, Slack 전송, credential 사용/변경, 외부 시스템 변경까지 암묵적으로 수행하면 Agent Builder의 안전 경계가 깨진다. 따라서 preview, 검토, 저장, 실행의 경계를 명확히 고정해야 한다.

## Decision

Agent Builder의 validation 통과 draft는 Workflow Editor의 Preview Mode에서 검토한다.

- Chatbot panel은 draft 요약과 `도안 보기` 진입점을 제공한다.
- Preview Mode는 agent draft graph를 `previewGraph`로 렌더링하고, 실제 editor graph인 `actualEditorGraph`와 섞지 않는다.
- Preview Mode에서 node를 선택하면 Node Detail Panel은 node type, 주요 설정, Knowledge Base binding, Slack channel binding, credential 참조 상태, input/output mapping, validation 상태를 읽기 전용으로 표시한다.
- Preview Mode에서 사용자는 node 설정, edge, credential, Knowledge Base binding을 직접 편집하지 않는다. 변경이 필요하면 채팅 후속 요청으로 새 draft를 만든다.
- `취소`는 preview graph를 폐기하고 Preview Mode를 종료한다. Preview Mode 진입만으로 actual editor graph가 변경되지 않아야 하므로 rollback에 의존하지 않는다.

사용자가 `적용 및 저장`을 선택하면 backend는 workflow graph 저장까지 수행할 수 있다. 단, 저장 전 아래 조건을 모두 다시 확인해야 한다.

- 원 draft metadata가 존재하고 만료되지 않았다.
- 요청의 active organization context가 유효하다.
- 기존 workflow 수정 draft는 workflow read/write 권한을 다시 확인한다.
- 새 workflow draft는 app 또는 workflow 생성 scope 권한을 다시 확인한다.
- 전체 교체 draft는 기존 workflow read/write 권한과 교체 validation을 모두 만족해야 한다.
- draft 생성 시점의 `base_graph_hash`와 workflow `version` 또는 `updated_at`이 적용 시점 최신 값과 일치한다.
- validation 결과가 여전히 통과 상태다.

Stale check는 `base_graph_hash`와 workflow `version` 또는 `updated_at`을 함께 사용한다. `base_graph_hash`는 workflow 실행 의미에 영향을 주는 node id, node type, node data/config, edge source/target/handle만 포함한다. Viewport, selection, panel state, preview state, timestamp, UI-only metadata, note/memo node와 해당 note/memo node에만 연결된 non-runtime edge는 hash 입력에서 제외한다.

MVP에서는 Workflow Editor에 저장되지 않은 변경이 있으면 Agent Builder draft 생성, Preview Mode 진입, `적용 및 저장`을 차단한다. 사용자는 먼저 기존 변경을 저장하거나 폐기해야 한다. 후속 확장에서는 server-validated client graph snapshot을 draft base로 쓰는 방식을 별도 결정할 수 있다.

`적용 및 저장`은 workflow graph 저장을 의미하지만 아래 side effect를 수행하지 않는다.

- workflow 실행
- Knowledge Base retrieval
- Slack/Jira/GitHub/Wiki 전송 또는 변경
- credential 사용 또는 변경
- 외부 시스템 변경

저장 성공 시 Preview Mode를 종료하고 Workflow Editor는 저장된 최신 workflow graph를 표시한다. `outcome=saved`는 apply/save audit event 기록 성공을 전제로 하며, `audit_recorded=false`인 저장 성공 응답은 허용하지 않는다. 저장 성공으로 응답하기 전 apply/save audit event는 canonical audit store에 기록되었거나, workflow graph 저장과 같은 transaction 또는 동등한 내구성 경계의 outbox/durable queue에 enqueue되어야 한다. 저장 시도 후 audit 기록이 실패하면 저장 성공으로 취급하지 않고 `failed` outcome과 safe failure reason으로 처리한다. 저장 차단 또는 실패 시 Preview Mode를 유지하고 `actualEditorGraph`를 변경하지 않는다. 사용자는 차단 사유 또는 실패 사유를 확인한 뒤 재시도, 취소, 또는 채팅 후속 요청으로 draft 수정을 선택할 수 있어야 한다. `blocked` outcome은 metadata, permission, stale, validation, unsaved editor change 같은 조건 미충족을 뜻하며 `block_reason`으로 표현한다. `failed` outcome은 backend 저장 시도 자체 또는 apply/save audit 기록 실패 같은 safe failure를 뜻하며 `failure_reason`으로 표현한다.

Agent Builder apply/save audit은 draft preview 생성, Preview Mode 진입, 적용 및 저장 요청, 저장 차단, 저장 성공, 저장 실패, 취소 event를 구분한다. Audit metadata는 draft id, request id, apply id, session id, workflow id 또는 새 workflow 생성 scope, preview graph hash, base graph hash, latest graph hash, workflow version 또는 updated_at, draft mode, apply/save outcome, block reason, failure reason, permission recheck outcome, stale state, validation state, saved workflow id, timestamp 같은 safe metadata만 포함할 수 있다.

Audit, trace, prompt, preview, validation message에는 credential 원문, API key, token, raw KB content, raw source path/url/title, hidden KB/resource detail, raw provider response, secret-like user input 원문을 포함하지 않는다.

## Consequences

- Agent Builder는 draft 검토를 chatbot text preview가 아니라 Workflow Editor Preview Mode에서 수행한다.
- 구현자는 `previewGraph`와 `actualEditorGraph`를 분리해야 한다.
- `적용 및 저장`은 별도 workflow save action이 아니라 Agent Builder apply/save boundary가 된다.
- 이 경계는 저장까지 허용하지만 실행과 외부 action은 계속 별도 사용자 action 또는 workflow runtime flow로 남긴다.
- 저장 전 권한 재확인과 stale check가 실패하면 fail-closed로 동작한다.
- 저장 실패 또는 차단 시 원본 editor graph는 오염되지 않아야 한다.
- 기존 "canvas local apply 후 별도 save" 또는 "approval은 local state 반영까지만 판단" 흐름은 이 ADR 범위의 Agent Builder MVP에는 적용하지 않는다.

## Non-Goals

- 이 ADR은 workflow runtime 실행 방식을 변경하지 않는다.
- 이 ADR은 Knowledge Base retrieval runtime 권한을 변경하지 않는다.
- 이 ADR은 Slack/Jira/GitHub/Wiki 연동을 새로 도입하지 않는다.
- 이 ADR은 credential 자동 선택, 자동 생성, 원문 사용을 허용하지 않는다.
- 이 ADR은 모든 node type 자동 생성을 승인하지 않는다.
- 이 ADR은 unsaved editor graph snapshot 기반 draft 생성을 승인하지 않는다. 해당 확장은 후속 결정이 필요하다.
