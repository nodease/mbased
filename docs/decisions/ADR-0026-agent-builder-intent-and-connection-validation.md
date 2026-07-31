# ADR-0026: Agent Builder Intent And Connection Validation

Status: Accepted
Related ADRs: [ADR-0019](ADR-0019-agent-builder-preview-apply-save-boundary.md), [ADR-0024](ADR-0024-agent-builder-node-capability-catalog.md), [ADR-0025](ADR-0025-agent-builder-intent-model-selection.md), [ADR-0045](ADR-0045-agent-builder-direct-edit-parameter-guidance.md), [ADR-0046](ADR-0046-agent-builder-graph-mutation-and-cas-save.md)

## Context

Agent Builder는 permission-aware LLM structured output으로 자연어 의미를 추출하지만, schema가 유효하다는 사실만으로 요청 유형, 수정 대상, 배치 방향이 서로 일관된 것은 아니다. 또한 frontend handle과 graph validator가 차단하는 entry/terminal/Condition 연결을 backend가 같은 정책으로 재검증하지 않으면 legacy Preview 결과 또는 Accepted direct-edit GraphMutation/CAS 저장에서 잘못된 graph가 확정될 수 있다.

Workflow runtime 구현 여부와 Agent Builder 제품 가용성도 같은 의미가 아니다. `loopNode`는 runtime registry에 구현되어 있지만 현재 editor에서 사용할 수 없는 기능으로 표시되므로 Agent Builder가 제안해서는 안 된다.

## Decision

Workflow Node Capability Catalog v2가 다음 연결 계약을 도입했다. Accepted ADR-0045/0046은 parameter/input/output 계약을 추가한 단일 v3 catalog와 direct-edit를 권위 설계로 채택한다. 신규 direct-edit session은 v3 schema와 parser가 함께 배포된 코드에서 시작하며 기존 null Preview session은 `stale_protocol`로 닫는다.

- `implemented`: frontend/runtime 구현 집합
- `agent_builder_supported`: Agent Builder에서 현재 제안 가능한 제품 가용성
- `connection_policy`: node role, incoming/outgoing 허용 여부, outgoing handle 규칙

현재 구현 node type은 16개지만 Agent Builder 허용 node type은 `loopNode`를 제외한 15개다. Runtime 구현만으로 Agent Builder 허용 상태가 되지 않으며, 제품 가용성과 draft template 및 검증 테스트가 모두 준비되어야 `agent_builder_supported=true`로 변경할 수 있다.

LLM structured output은 신뢰 경계 밖의 의미 후보로 취급한다. Schema 검증 뒤에 다음 semantic invariant를 서버에서 검증한다.

- 새 workflow 요청은 `request_type=new_workflow`, `draft_mode=new_workflow`이며 edit target을 포함하지 않는다.
- 기존 workflow 수정은 `request_type=modify_workflow`, workflow context, 신규 capability, target과 placement를 포함한다.
- 전체 교체는 `request_type=modify_workflow`, `draft_mode=replace_workflow`이며 개별 edit target을 포함하지 않는다.
- selected node/edge target은 같은 요청의 server-validated selection context가 있을 때만 사용한다.
- 명시적 GitHub Pull Request 요청은 capability 선택 전에 provider/resource/operation 의미를 `github`/`pull_request`/`read|comment|create`로 분리한다. 지원 중인 read/comment operation과 capability가 불일치하면 semantic repair하고, 현재 미지원인 create operation은 generic HTTP capability로 대체하지 않고 `unsupported`로 닫는다.

Backend는 redacted request에 명시된 GitHub provider와 Pull Request resource가
`http_request` capability로 반환됐는데 `integration_actions`가 비어 있는 경우
`GITHUB_INTEGRATION_ACTION_REQUIRED` safe code로 repair를 요구할 수 있다. 이 검사는
provider/resource 누락과 HTTP 대체를 찾는 fail-closed guard일 뿐이며, raw message의
동사로 `read`, `comment`, `create` operation을 추론하거나 action을 직접 주입하지
않는다. 기존 GitHub node를 edit target으로만 지칭하고 새 capability가 HTTP가 아닌
요청은 이 guard 대상이 아니다.

정상 request의 planner provider 호출은 한 번이다. 최초 결과가 schema-valid지만 semantic invariant를 위반한 경우에만 extractor는 safe validation code로 repair를 정확히 한 번 수행할 수 있으므로 총 호출 수는 최대 두 번이다. 최초 또는 repair 호출에서 provider/JSON/schema 오류가 발생하거나 repair 결과가 semantic invariant를 다시 위반하면 `INTENT_EXTRACTION_FAILED`로 종료한다. Parameter task, Knowledge 선택과 task 전환에서는 planner를 다시 호출하지 않는다. Raw provider payload, raw message, credential, URL, path, node ID, edge ID는 repair prompt, 응답, mutation, trace, audit에 저장하지 않는다. 정규식 기반 graph 생성으로 fallback하지 않는다.

Backend는 catalog의 connection policy로 complete candidate graph를 검증한다. Cutover 전 legacy Preview/apply-save 결과는 characterization 경계에서 같은 정책으로 고정하고, Accepted direct-edit 경로는 GraphMutation 발급과 CAS workflow save에서 검증한다. 최소 차단 코드는 다음과 같다.

- `START_NODE_HAS_INCOMING_EDGE`
- `TRIGGER_NODE_HAS_INCOMING_EDGE`
- `TERMINAL_NODE_HAS_OUTGOING_EDGE`
- `INVALID_CONDITION_SOURCE_HANDLE`

Selected edge는 raw message 정규식이 아니라 검증된 structured edit target과 server-loaded workflow graph의 실제 edge 존재 여부로 확정한다. LLM은 node/edge ID나 graph를 직접 만들지 않는다.

## Consequences

- Frontend와 backend가 같은 catalog policy를 테스트해 entry, trigger, terminal, Condition 규칙의 drift를 차단한다.
- Cutover 전 legacy Preview/apply-save characterization과 Accepted GraphMutation/CAS save 경계에서 같은 invalid graph가 같은 이유로 차단되며 저장되지 않는다. Preview는 활성 fallback이나 장기 병행 protocol로 유지하지 않는다.
- 일시적인 semantic 불일치는 한 번 보정할 수 있지만 무한 재시도와 조용한 fallback은 발생하지 않는다.
- `loopNode`는 runtime 구현을 유지하되 Agent Builder capability guide, allowlist, draft generation에서 제외된다.
- 실제 planner 품질 평가는 credential이 필요한 별도 evaluation으로 관리하며 deterministic unit test와 분리한다.
- GitHub PR 생성 runtime이 추가되기 전에도 planner는 create 의미를 보존하므로 HTTP node로 조용히 우회하지 않는다.
