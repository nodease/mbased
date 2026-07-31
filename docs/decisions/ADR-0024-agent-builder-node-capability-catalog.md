# ADR-0024: Agent Builder Node Capability Catalog

Status: Accepted
Related ADRs: [ADR-0019](ADR-0019-agent-builder-preview-apply-save-boundary.md), [ADR-0021](ADR-0021-webhook-capture-helper-security-boundary.md), [ADR-0026](ADR-0026-agent-builder-intent-and-connection-validation.md), [ADR-0045](ADR-0045-agent-builder-direct-edit-parameter-guidance.md), [ADR-0046](ADR-0046-agent-builder-graph-mutation-and-cas-save.md)

## Context

Workflow Editor의 node library와 React Flow renderer, Workflow Engine runtime에는 같은 구현 node type 집합이 존재하지만 Agent Builder는 별도 상수와 분기문으로 일부 node type만 허용했다. 이 중복 때문에 실제로 구현된 `githubNode`가 자연어 요청의 draft에 생성되지 않는 것처럼 catalog와 Builder 지원 범위가 어긋날 수 있다.

Node type이 runtime에 존재한다는 사실만으로 Agent Builder가 안전한 draft를 만들 수 있는 것은 아니다. 외부 action node는 capability, side effect, 필수 설정, secret 처리 정책이 함께 정의되어야 한다.

## Decision

버전 관리되는 공통 Workflow Node Capability Catalog를 node type 계약의 단일 기준으로 사용한다.

Catalog는 최소한 다음 정보를 가진다.

- React Flow와 Workflow Engine이 공유하는 canonical `node_type`
- 실제 구현 여부
- Agent Builder allowlist 포함 여부
- node가 제공하는 capability 목록
- 외부 읽기/쓰기 등 side effect 분류
- 실행 전에 사용자가 채워야 하는 필수 설정 이름
- configurable parameter의 stable key/order, input type, required 여부, validation/default 정책과 input/output contract
- parameter별 `defer_policy=forbidden|allow_unresolved`; 생략하면 `forbidden`

Workflow Editor node registry, React Flow `nodeTypes`, Workflow Engine `NodeFactory.NODE_REGISTRY`, Agent Builder validator/generator가 catalog와 일치하는지 정적 또는 단위 테스트로 검증한다. 각 runtime은 class, React component, default data factory처럼 언어별 실행 객체를 계속 코드로 소유하지만 지원 node type 집합을 독립적으로 결정하지 않는다.

현재 구현된 16개 node type 중 다음 15개를 Agent Builder allowlist에 포함한다.

- `startNode`
- `webhookTrigger`
- `scheduleTrigger`
- `llmNode`
- `workflowNode`
- `codeNode`
- `conditionNode`
- `fileExtractionNode`
- `variableExtractionNode`
- `answerNode`
- `httpRequestNode`
- `slackPostNode`
- `templateNode`
- `githubNode`
- `mailNode`

`loopNode`는 runtime에 구현되어 있으므로 `implemented=true`를 유지하지만 현재 editor의 제품 가용성이 비활성 상태이므로 `agent_builder_supported=false`로 둔다. 반복 기능이 editor, draft template, 연결 정책, 검증 테스트와 함께 공개되기 전에는 Agent Builder가 제안하지 않는다.

`githubNode`는 `github_pr_read`와 `github_pr_comment` capability를 구분한다. PR review 자동화 요청은 필요한 경우 조회 node와 댓글 node를 각각 생성한다.

Agent Builder가 외부 action node를 draft에 포함하더라도 legacy Preview와 ADR-0045/ADR-0046의 GraphMutation 생성, editor 적용, CAS 저장 및 acknowledgement는 해당 action을 실행하지 않는다. Credential, token, password, repository, channel, URL, target workflow처럼 자동으로 안전하게 확정할 수 없는 값은 원문을 생성하거나 추론하지 않고 빈 값과 `configuration_state=unresolved`로 남긴다. Unresolved external-action 실행·배포 server preflight는 ADR-0045/ADR-0046을 따른다.

ADR-0045 direct-edit에서 Catalog v3는 모든 configurable parameter의 task 생성 권위다. 자동 확정값도 completed task record로 남기며 실제 값은 workflow graph에만 저장한다. Required parameter의 defer는 Catalog가 `allow_unresolved`로 명시한 경우에만 허용하고, 이 decision은 node의 `configuration_state=unresolved`를 저장하는 GraphMutation acknowledgement 뒤 완료한다. Planner나 frontend가 Catalog에 없는 defer 허용 여부를 추론하지 않는다.

Catalog에 등록되지 않은 임의 node type, `implemented=false` node type, `agent_builder_supported=false` node type은 Agent Builder allowlist에 포함하지 않는다. Catalog의 연결 정책과 backend 검증 경계는 ADR-0026을 따른다.

## Consequences

- 실제 구현 node 목록과 Agent Builder validator 목록의 drift를 테스트에서 차단할 수 있다.
- 새 node type은 frontend/runtime 구현, catalog 등록, Builder draft template과 검증 테스트를 함께 추가해야 한다.
- 구현 node와 Builder 제품 가용성을 분리하며, 허용된 node만 draft에 제안할 수 있다. 외부 action과 secret 사용은 자동 승인되지 않는다.
- Catalog를 runtime에서 원격 조회하지 않으므로 배포 버전별 결과가 결정적이며 네트워크 실패가 Builder validation에 영향을 주지 않는다.
