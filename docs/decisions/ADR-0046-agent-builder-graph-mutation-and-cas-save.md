# ADR-0046: Agent Builder GraphMutation and CAS save

Status: Accepted

Related ADRs: [ADR-0019](ADR-0019-agent-builder-preview-apply-save-boundary.md), [ADR-0024](ADR-0024-agent-builder-node-capability-catalog.md), [ADR-0026](ADR-0026-agent-builder-intent-and-connection-validation.md), [ADR-0045](ADR-0045-agent-builder-direct-edit-parameter-guidance.md)

## Context

MBA-228은 Agent Builder가 workflow graph를 실제 editor에 직접 적용하고 일반 workflow draft 저장을 거쳐 확정하는 구조를 채택한다. 그러나 현재 코드와 API에는 문서가 전제하던 공통 `GraphPatch` schema, 저장 revision, mutation acknowledgement가 없다. 현재 workflow draft 저장은 마지막 요청이 이전 값을 덮어쓰며 저장된 graph hash와 `updated_at`도 반환하지 않는다.

이 상태에서 frontend가 graph를 먼저 적용하고 저장 성공 뒤 별도 acknowledgement만 보내면 다음 문제가 생긴다.

- 같은 workflow를 편집하는 다른 사용자나 autosync가 중간에 저장해도 충돌을 감지하지 못한다.
- 최초 생성, parameter 변경과 Knowledge binding이 서로 다른 optional payload와 복구 분기를 만든다.
- 저장 실패 또는 acknowledgement 응답 유실 뒤 parameter task와 실제 workflow graph가 서로 다른 상태로 복구될 수 있다.
- 기존 Preview session과 direct-edit session이 섞이면 이전 draft를 새 protocol로 적용할 수 있다.
- unresolved 외부 action node가 저장된 뒤 실행 또는 배포 경계에서 차단되지 않을 수 있다.

따라서 MBA-228 안에서 공통 graph mutation 계약과 compare-and-swap 저장 경계를 함께 도입한다.

## Decision

Workflow node secret persistence is governed by [ADR-0062](ADR-0062-workflow-node-secret-reference-boundary.md). CAS candidate graphs, history snapshots and deployment snapshots contain only opaque workflow-node secret references. Any older wording in this ADR that permits Slack/GitHub plaintext to be added through the editor draft save bridge is superseded; the direct masked input UX itself is unchanged.

### 1. Common Mutation Contract

Agent Builder가 workflow graph를 바꾸는 모든 응답은 `GraphMutation`을 사용한다.

`kind`는 다음 값만 허용한다.

- `initial_graph`: 빈 workflow 또는 새 workflow에 최초 graph를 구성한다.
- `graph_edit`: 기존 workflow의 구조를 변경한다. Parameter task 생성 여부는 별도 `generation_mode`가 결정한다.
- `replace_workflow`: 비어 있지 않은 기존 workflow의 edge/node 전체를 typed remove operation으로 제거하고 새 workflow의 node/edge를 typed add operation으로 구성한다.
- `parameter_update`: 하나의 typed parameter decision을 node data에 반영한다.
- `knowledge_binding`: 확정된 Knowledge Base 선택을 기존 graph에 반영한다.

`operations`는 discriminated union이며 다음 operation만 허용한다.

- `add_node`
- `remove_node`
- `add_edge`
- `remove_edge`
- `replace_node_data`

임의 JSON Patch path, raw workflow snapshot과 node type별 optional mutation field는 허용하지 않는다. `replace_workflow`도 raw graph payload를 받지 않고 공통 discriminated operation만 사용한다.

### 2. API Mutation And Persisted Safe Envelope

발급 API 응답의 `GraphMutation`은 client가 한 번 적용할 typed `operations`와 다음 metadata를 포함한다.

- `operation_id`
- `workflow_id`
- `kind`
- `status`
- `catalog_version`
- non-null `base_graph_hash`
- non-null `expected_workflow_updated_at`
- server가 발급 전에 candidate graph를 검증하고 계산한 non-null `expected_result_graph_hash`
- typed `operations`
- `affected_node_ids`
- 선택적 `completion_context.parameter_task_id`
- 선택적 `completion_context.knowledge_resolution_id`

Full typed `operations`는 API 응답 전용이며 DB, session 또는 request payload에 저장하지 않는다. 기존 `AgentBuilderRequest.response_payload`에는 `operations`를 제외한 위 식별자·hash·affected node·completion context와 저장 뒤 server가 확정하는 `result_graph_hash`만 safe operation envelope로 저장한다. Parameter 값, raw graph snapshot, raw Knowledge metadata, credential 또는 secret도 envelope에 포함하지 않는다. 별도 Agent Builder table, encrypted operation payload 또는 replay store를 만들지 않는다.

Lifecycle은 `pending_apply -> pending_save -> pending_ack -> acknowledged` 순서다. 권한, stale, schema 또는 graph validation 실패는 `blocked`로 닫고, 저장된 결과가 persisted Undo로 복구되면 원 operation을 최종 상태 `reverted`로 전환한다. `reverted`는 GraphMutation kind가 아니다. 같은 `operation_id`의 적용, 저장, acknowledgement와 revert는 idempotent해야 하며 이미 acknowledged되거나 reverted된 operation을 새 operation처럼 다시 적용하지 않는다.

### 3. Frontend Transaction

Frontend의 단일 editor adapter가 모든 `GraphMutation`을 dry-run validation한다. 최초 `initial_graph`, `graph_edit` 또는 `replace_workflow`는 Agent Builder 시작 전 snapshot과 최종 graph snapshot을 가진 Workflow history boundary 하나를 만든다. `replace_workflow`의 시작 snapshot은 교체 전 기존 graph 전체다. 이후 `parameter_update`와 `knowledge_binding`은 같은 dispatcher와 CAS 저장 경계를 사용하지만 독립 history entry를 추가하지 않고 boundary의 final snapshot/hash를 acknowledgement 결과로 갱신한다. 일반 수동 editor 변경은 별도 history entry로 boundary 뒤에 쌓인다.

Agent Builder transaction 동안 일반 autosync는 같은 graph를 별도로 저장하지 않는다. local apply 또는 저장이 실패하면 acknowledgement를 보내지 않고 parameter task나 Knowledge selection을 완료로 전환하지 않는다.

### 4. CAS Workflow Draft Save

Direct-edit session은 기존 Workflow Editor 생성 흐름으로 만들어진 workflow row를 대상으로 한다. 새 workflow도 저장된 빈 workflow shell에 `initial_graph` mutation을 적용하며 Agent Builder session/message endpoint가 workflow row를 별도로 생성하지 않는다.

Agent Builder mutation이 사용하는 일반 workflow draft 저장 요청에는 `mutation_context`를 추가한다.

- `operation_id`
- `expected_base_graph_hash`
- `expected_workflow_updated_at`
- `catalog_version`

Server는 workflow row를 write lock으로 조회하고 권한을 다시 확인한 뒤 현재 canonical graph hash와 `updated_at`을 두 기대값과 비교한다. 둘 중 하나라도 다르면 저장하지 않고 stale conflict를 반환한다. 일치하면 request candidate graph의 canonical hash가 발급 시 safe envelope에 기록한 `expected_result_graph_hash`와 같은지 확인하고, 동일한 catalog connection/schema validation과 derived configuration validation을 다시 수행한 뒤 한 transaction으로 저장한다. Server-stored typed operations를 재생해 비교하지 않는다.

CAS 저장은 workflow write 권한만 다시 확인하는 경계가 아니다. Server는 final candidate graph와 Catalog v3 및 server-owned reference policy registry에서 `resource_ref`, `credential_ref`, Knowledge/Collection binding과 WorkflowNode `appId`/`workflowId` 등 모든 resource-bearing field를 직접 추출하고 `managed_reference|legacy_editor_connection|unknown`으로 분류한다. Policy registry는 field path·mutation kind·resource kind별 authoritative resolver, required relation과 최소 permission action을 명시한다. Workflow draft 저장 자체에는 workflow `write`, Knowledge/Collection과 managed credential binding에는 대상 resource `use`를 요구하며 단순 reference 때문에 대상 resource의 `read|write`를 일괄 요구하지 않는다. Target resource를 실제 변경하는 별도 operation만 해당 resource의 `write`를 요구한다. Resolver는 registry가 정한 최소 action, 현재 organization, 존재/lifecycle과 relation을 같은 DB transaction 안에서 재검증하고 policy 또는 resolver 누락은 fail-closed한다. `legacy_editor_connection`은 ADR-0045의 resolver 미구현 Slack/GitHub 호환 allowlist에 속한다. Agent Builder GraphMutation에서는 persisted base graph와 canonical 값 및 connection-relevant node data가 동일한 경우에만 불변 carry-forward하고, 해당 field의 추가·교체·삭제나 새 node 복제는 전체 save와 audit를 rollback한다. Slack/GitHub token과 webhook URL은 safe task metadata와 masked control로 입력하되 raw 값은 Agent Builder decision, GraphMutation, `mutation_context` 또는 session에 넣지 않고 기존 Workflow editor draft save bridge로만 추가·교체한다. Unknown field, managed resolver 누락, 삭제·비활성·권한 회수·relation 변경은 계속 fail-closed한다. Client inventory나 mutation 발급 시점 allow 판정을 권위값으로 사용하지 않고, safe operation envelope에 resource identifier나 revision snapshot을 추가하지 않는다. Carry-forward는 credential use 승인이나 runtime capability가 아니며 기존 Editor 검증과 실행·배포 preflight를 완화하지 않는다. 이 규칙은 `initial_graph|graph_edit|replace_workflow|parameter_update|knowledge_binding`과 quick/guided/structure-only 모두에 동일하게 적용한다.

Agent Builder CAS save와 request cancel이 경쟁할 때는 모두 `Workflow` row, parent `AgentBuilderRequest` row 순서로 lock을 얻고 현재 request/operation/save metadata를 다시 읽는다. Session-scoped cancel만 이 순서 앞에 `AgentBuilderSession` row lock을 추가한다. Save가 먼저 확정되면 cancel은 persisted graph를 유지하고 request와 모든 비종료 child state를 닫으며, cancel이 먼저 확정되면 뒤 save는 canceled/version mismatch로 graph를 쓰지 않는다. Request가 terminal로 전환될 때 pending quick-completion proposal, 남은 task/Knowledge resolution과 저장 전 safe envelope도 같은 transaction에서 닫고 변경되는 proposal/task version을 증가시킨다. 서로 다른 lock 순서를 사용하거나 Client의 local 상태로 승자를 추론하지 않는다.

일반 editor save도 현재 canonical graph hash와 workflow `updated_at`을 optimistic concurrency 입력으로 전달한다. Server는 mutation context 유무와 관계없이 모든 editor save에서 workflow row를 write lock으로 조회한 뒤 active organization 범위의 write 권한을 다시 확인하고 두 기대값이 일치할 때만 저장하며 뒤늦은 autosync는 `409 stale_graph`로 닫는다. Endpoint 진입 권한 검사는 빠른 차단일 뿐 최종 권위 판단이 아니며, lock 뒤 권한이 회수됐으면 graph, features, parameter 상태와 audit을 변경하지 않고 `403`으로 종료한다. Frontend는 Agent Builder 저장, 일반 autosync, version 복원, test 전 저장과 Undo/Redo가 공유하는 canonical metadata 상태 하나를 사용하고 성공한 canonical GET/POST마다 이를 갱신한다. Out-of-band 저장 뒤 오래된 metadata로 autosync를 계속하거나 stale 오류를 조용히 무시하지 않는다. `mutation_context`는 Agent Builder safe operation envelope와 candidate hash를 추가 검증하는 additive 경계이며 일반 저장도 silent overwrite 예외가 아니다.

같은 browser editor에서 Agent Builder save/acknowledgement, test preflight save, autosync와 Undo/Redo는 workflow별 save coordinator를 공유한다. Agent Builder 저장 또는 결과 확인 중에는 test 실행을 시작하지 않고 사용자가 저장 확정 뒤 다시 실행하도록 한다. Test preflight가 이미 점유한 경우 Agent Builder 저장은 앞선 저장 해제 뒤 진행하고, lock 획득 직후와 canonical draft 조회 직후 active workflow id를 다시 확인한다. 어느 확인에서든 시작 workflow와 다르면 graph mutation, draft save, acknowledgement와 local rollback을 수행하지 않고 새 workflow의 graph, canonical metadata와 history를 보존한 채 안전한 workflow 전환 오류로 종료한다. 일반 autosync가 lock 때문에 현재 회차를 건너뛰면 unsaved 상태를 보존하고 workflow별 대기 작업 하나로 합친다. Lock 해제 뒤 active workflow와 dirty 상태를 다시 확인하고 최신 editor snapshot만 정확히 한 번 저장하며, 이전 snapshot을 순서대로 재생하거나 다른 workflow에 저장하지 않는다. `409 stale_graph` 뒤 canonical graph가 test 대상 editor snapshot과 동일한 경우에만 이미 적용된 저장으로 인정할 수 있으며, 비교가 끝나기 전에 canonical metadata를 local store에 주입해서는 안 된다. 다르면 자동 overwrite·merge·metadata 갱신·test 실행을 모두 금지한다. `operation envelope not found` 복구는 해당 Agent Builder session의 safe operation envelope와 canonical draft를 함께 조회해 `applied|unapplied|pending|stale`을 판정하며 test surface가 typed operation을 재생하지 않는다. `applied`는 acknowledged envelope의 result graph hash와 saved workflow `updated_at`이 canonical draft의 두 값과 모두 같은 경우에만 성립한다. Hash만 같거나 timestamp가 다르면 applied로 추측하지 않고 stale로 닫는다. Base hash가 같고 operation이 terminal failure/revert이면 `unapplied`, operation이 save/ack 처리 중이면 `pending`, 어느 쪽에도 해당하지 않으면 `stale`이다.

Test preflight의 canonical 확인부터 execution stream 요청 시작까지는 하나의 직렬화 경계다. Test가 coordinator를 소유한 동안 Agent Builder 저장이 대기 상태가 되면 test는 stream을 열지 않고 coordinator를 해제하며, Agent Builder 저장이 끝난 뒤 사용자가 명시적으로 다시 실행한다. Test는 coordinator를 해제한 뒤 Agent Builder 상태를 확인하지 않은 채 실행을 시작할 수 없다. 자동 test 재실행, operation 재생과 graph 강제 덮어쓰기는 허용하지 않는다.

Test preflight는 editor가 clean이라고 표시되어도 canonical server graph와 캡처한 local graph를 공통 canonical projection으로 비교한다. Dirty editor는 편집이 시작된 기존 canonical hash와 `updated_at`을 save 기준으로 유지하고, preflight GET에서 더 최신 metadata를 받았다는 이유로 이를 local graph에 주입하지 않는다. Server metadata가 local edit base보다 앞서 있거나 canonical graph와 local snapshot이 다르면 저장과 test를 모두 차단한다. Projection은 node root의 `width`, `height`, `measured`, `dragging`, `resizing`, `selected`, `positionAbsolute`, node data의 `displayNumber`, 실행 `status`, `observability`와 edge의 presentation selection을 제거한다. 같은 규칙을 모든 중첩 `subGraph.nodes`, `subGraph.edges`와 `features.noteNodes`에 재귀 적용하되 node `position`과 business configuration은 보존하고 canonical `features.noteNodes`를 빈 editor note 목록으로 덮어쓰지 않는다. `features.noteNodes`가 배열이 아니거나 유효한 note node가 아닌 항목을 포함하면 Gateway는 DB를 변경하지 않고 safe `422` validation failure로 닫는다. 두 graph가 같을 때만 canonical hash와 `updated_at`을 local metadata로 수용한다. 다르면 최신 metadata를 오래된 local graph와 결합하지 않고 test와 저장을 차단한다. `operation envelope not found`는 현재 Agent Builder session과 canonical draft로 결과를 확인하는 recoverable 상태이며, acknowledged result가 확인된 `applied`에서만 test를 계속한다. `pending`은 제한된 canonical 조회만 수행하고 `unapplied|stale` 또는 session 부재는 명시적인 오류로 닫는다.
최상위 request schema를 통과했더라도 중첩 `subGraph` node/edge가 canonical schema를 위반하면 recursive materializer는 공통 `workflow.graph_invalid`로 정규화한다. 일반 draft save는 DB write와 audit 전에 safe HTTP `422`로 닫고, persisted graph의 canonical hash를 계산하는 draft read도 같은 safe `422`를 반환하며 raw Pydantic detail이나 graph payload를 노출하지 않는다.

Workflow 실행 중 presentation field는 canonical graph, graph hash, draft payload, autosync dirty flag와 Workflow Undo/Redo history에 포함하지 않는다. Agent Builder 저장과 일반 저장의 graph 및 `features.noteNodes`, 일반 autosync, version 복원, test 전 저장, Undo/Redo와 note 저장은 같은 client canonical serializer를 사용하고 Gateway도 저장 직전에 같은 재귀 projection을 적용한다. Test 전 저장이 성공해도 저장 시작 이후 별도 editor 변경이 발생했다면 그 변경의 dirty 상태를 지우지 않는다.

Node `configuration_state`는 server-derived 표시 상태이므로 Client canonical request와 local/canonical 비교 projection에서 최상위 및 중첩 node 모두 제거한다. Gateway는 제거된 Client 값을 요구하거나 신뢰하지 않고 Catalog로 다시 계산해 저장 graph와 canonical response에 materialize한다. Version restore는 snapshot에 `features.noteNodes` field가 있으면 그 배열을 권위로 사용하며 명시적 빈 배열은 Note 전체 삭제를 뜻한다. 해당 field가 없는 legacy snapshot은 `nodes`의 Note를 사용하고, 두 표현이 모두 없는 legacy snapshot은 현재 editor Note를 보존해 field 부재만으로 Note를 삭제하지 않는다.

Workflow graph를 함께 바꾸는 Model Routing policy PATCH와 Cost Optimizer candidate/recommendation apply도 out-of-band 예외가 아니다. 이 API들은 현재 canonical `expected_graph_hash`와 `expected_updated_at`을 필수로 받고, 권한 확인 뒤 workflow row를 write lock으로 다시 조회한 상태에서 같은 CAS를 검증한다. Graph 변경과 policy/candidate 부가 상태 변경은 같은 transaction으로 확정하며 성공 응답의 `graph_hash`와 `updated_at`으로 frontend 공통 canonical metadata를 갱신한다.

### 5. Canonical Save Result

같은 브라우저에서 서로 다른 workflow를 포함한 save/acknowledgement 작업이 겹칠 수 있으므로 전역 실행 차단 상태는 단순 boolean 덮어쓰기가 아니라 진행 중인 작업 수를 기준으로 계산한다. 먼저 끝난 작업이 상태를 해제해도 다른 Agent Builder 저장이 남아 있으면 test, autosync와 Workflow Undo/Redo 차단을 유지하고, 모든 작업이 끝난 뒤에만 해제한다.

CDS save 자동 재시도는 transport 단절 또는 `5xx`처럼 서버 적용 여부를 확인할 수 없는 결과에만 같은 mutation context로 한 번 허용한다. `401`, `403`, `409`, `422`처럼 서버가 명시적으로 거부한 결과는 적용되지 않은 확정 실패이므로 같은 POST를 반복하지 않고 local mutation을 복구한 뒤 원래 오류를 반환한다.

저장 성공 응답은 최소 다음 값을 반환한다.

- `workflow_id`
- canonical persisted `graph_hash`
- persisted `updated_at`
- `operation_id`

현재 Workflow model에 존재하지 않는 `workflow_version` 또는 `revision`을 문서나 응답에서 만들지 않는다. `graph_hash`는 저장된 nodes와 edges의 stable-id 정렬 및 object-key 정렬 canonical JSON에 대한 SHA-256이며 viewport는 제외한다. Server가 저장 뒤 다시 계산한 값만 canonical 결과다.

Agent Builder와 일반 editor draft save 응답 및 canonical draft read 응답은 persisted `graph_hash`와 `updated_at`을 같은 이름으로 반환한다. Recovery test는 production endpoint가 반환하지 않는 metadata를 fixture에 임의로 추가하지 않는다.

### 6. Acknowledgement

Frontend는 저장 성공 응답의 `workflow_id`, `graph_hash`, `updated_at`을 동일한 `operation_id` acknowledgement에 전달한다. Backend는 operation metadata와 현재 저장 graph를 다시 대조하고 일치할 때만 `acknowledged`로 전환한다.

Acknowledgement는 graph를 다시 저장하지 않는다. 성공 뒤에만 연결된 parameter task 또는 Knowledge resolution을 완료하고 다음 task를 활성화한다. 이미 canonical graph에 반영된 자동 추천값의 `confirm`은 관련 structural operation이 acknowledged인 경우에만 GraphMutation이나 workflow 저장 없이 task를 완료하며, 같은 operation 재시도는 같은 결과를 반환한다. 원래 mutation 제출 흐름에서 acknowledgement 응답만 유실되면 같은 payload로 정확히 한 번 재시도할 수 있고, 중복 acknowledgement는 같은 결과를 반환한다. 두 번째 결과도 불명확하거나 복구·수동 재확인 경로에 진입한 뒤에는 acknowledgement를 다시 보내지 않고 canonical session 조회로만 `acknowledged|pending_ack|unapplied|stale`를 판정한다.

### 7. Agent Builder History Boundary, Persisted Undo And Redo

모든 필수 Knowledge 선택과 parameter 확인이 끝나고 모든 Agent Builder graph save와 acknowledgement가 끝난 명시적 완료 상태에서만 history boundary Undo를 시작한다. 저장되지 않았거나 acknowledgement가 불명확한 operation은 완료 boundary로 노출하지 않고 canonical operation과 workflow 상태를 먼저 복구한다.

ParameterTask가 있으면 boundary의 첫 Undo는 graph 저장 없이 `completed|skipped|deferred` 중 재편집 가능하고 `stable_order`가 가장 큰 task를 client presentation에서 다시 표시하는 parameter 재진입 단계다. Graph, 기존 parameter 값과 persisted task status/version은 그대로 유지한다. ParameterTask 사이 이동은 backend가 반환한 `next_task_id`를 client-only presentation cursor로 소비하는 `이전 항목` control로 처리하며 Workflow history를 소비하지 않는다. Parameter 재진입 상태의 다음 Undo 또는 task가 없는 완료 상태의 첫 Undo는 일반 workflow draft 저장 endpoint에 boundary operation id, `action=revert`, boundary의 최종 graph hash, 현재 workflow `updated_at`을 전달한다. Server는 write lock과 권한 재확인 뒤 current canonical graph hash가 boundary final hash와 일치하고 복구 candidate hash가 시작 전 base hash와 일치할 때만 저장한다. 다른 수동 편집이 있으면 그 편집의 일반 history entry가 먼저 Undo되어야 하며 stale 상태를 덮어쓰지 않는다.

Graph 복구, boundary 상태 전환, 모든 ParameterTask/Knowledge resolution의 `canceled` 처리와 기존 transaction-bound audit insert는 같은 DB transaction에서 commit한다. 생성 node/edge, parameter 설정과 KB binding을 포함한 Agent Builder 결과 전체를 시작 전 snapshot으로 복구한다. `replace_workflow` candidate는 교체 전 graph 전체여야 한다. `parameter_update`와 `knowledge_binding`별 persisted revert 및 Workflow Undo 계약은 폐기한다. 전체 복구 뒤 reload 전 Redo는 client memory의 final snapshot을 다시 적용하고 CAS 저장하되 canceled task/Knowledge 흐름을 재실행하지 않는다. 전체 Redo 뒤 다시 Undo하면 canceled task UI에 재진입하지 않고 같은 boundary의 시작 전 graph로 바로 복구한다. Parameter 재진입 상태의 Redo는 graph 저장 없이 UI만 완료 상태로 닫는다. Redo stack과 재진입 표시는 reload 뒤 복구하지 않는다.

Revert와 Redo는 같은 boundary operation id, action, candidate graph hash와 expected CAS hash/`updated_at`을 가진 재요청에 대해 멱등하다. 최초 canonical graph hash/`updated_at`을 반환하고 graph write, task/Knowledge 전환과 audit를 반복하지 않는다. Client는 network outcome이 불명확하면 같은 context를 한 번 자동 재시도하며, 두 번째 결과도 불명확하면 canonical workflow와 boundary 상태를 조회해 반영됨, 미반영 또는 stale로 판정할 때까지 memory history와 pending context를 보존한다.

Acknowledgement 응답 유실 뒤 session recovery에서 같은 operation이 이미 `acknowledged`이고 canonical graph hash/`updated_at`이 일치하면 client history boundary도 acknowledged로 reconcile한다. Acknowledgement와 audit를 중복 생성하지 않으며 hash 또는 timestamp가 다르면 완료로 추측하지 않고 stale/conflict로 닫는다.

Acknowledgement가 성공한 뒤 즉시 session read만 실패하면 client는 acknowledgement를 반복하거나 boundary를 미완료로 영구 고정하지 않는다. `completion_confirming` presentation과 pending history를 유지하고 canonical session을 제한적으로 재조회해 terminal 상태를 확인한다. Save/session 결과가 불명확할 때 canonical graph를 다시 표시하더라도 `applied|unapplied|stale` 판정 전에는 일반 workflow load로 Undo/Redo와 pending context를 초기화하지 않는다. 제한된 재조회 뒤 canonical graph가 operation의 base hash와 expected result hash 어느 쪽에도 일치하지 않는 제3 상태로 확정되면 server canonical graph와 graph hash/`updated_at`을 editor에 반영하고, 해당 workflow의 Agent Builder pending revert/apply context와 in-memory Undo/Redo history를 폐기한다. 이 경로는 typed operation, ParameterTask 또는 Knowledge flow를 재생하지 않고 server graph를 자동 병합하거나 덮어쓰지 않으며 editor를 clean 상태로 닫고 명시적인 stale 복구 안내를 표시한다.

### 8. Recovery And Protocol Migration

Session protocol은 MBA-228 단일 기능 PR의 additive migration으로 추가하는 nullable `AgentBuilderSession.protocol_version`에 저장한다. 신규 direct-edit session은 `direct_edit_v1`을 기록하고 기존 null row는 legacy Preview session으로 분류한다. Legacy row를 backfill하지 않는다. 이 session-level marker로 request가 하나도 없는 session도 안전하게 분류한다. 기존 Preview session은 `stale_protocol`로 복구하고 안전한 대화 이력만 표시한다. 이전 preview/draft는 적용하거나 GraphMutation으로 변환하지 않으며 사용자가 요청을 다시 제출해야 한다.

Migration revision은 구현 시점의 단일 Alembic head 뒤에 연결한다. MBA-228 release evidence에는 single-head 검사, disposable 기존 DB의 `upgrade head`, null/direct protocol read, request 없는 session 복구와 schema downgrade 없이 additive migration을 유지하는 검증을 포함한다. Preview는 fallback이 아니며 기존 결과는 parity characterization fixture로만 사용한다. Preview API/UI는 direct-edit parity와 필수 integration/E2E 검증을 통과한 뒤 같은 기능 PR에서 제거한다.

Frontend와 Gateway의 서로 다른 revision이 동시에 동작하는 운영 전환, 단계적 rollout/rollback, session creation gate와 독립 image artifact는 이 기능 프로토콜의 일부가 아니다. 해당 무중단 배포 계약은 별도 배포 ADR과 후속 이슈에서 정의한다.

`mode_contract_version`, canonical `generation_mode`, requested/effective mode와 source, catalog version, safe operation envelope와 task safe state는 기존 `AgentBuilderRequest.response_payload`에 저장한다. Full typed operations와 parameter 값은 저장하지 않는다. Repository는 SQLAlchemy가 nested JSON 변경을 놓치지 않도록 current payload를 복사해 새 전체 객체를 만들고 column에 재할당한다. Mode contract와 source는 planner 호출과 request row 생성 전에 확정한다. 명시적 control과 자연어 quick 의도를 활성화하지 않는 `legacy-v1` default는 request 생성 시 canonical requested/effective mode를 저장한다. `canonical-v2` default처럼 planner의 명시적 자연어 mode intent가 필요한 경우에는 `planning` row의 requested/effective mode를 미확정으로 두고 schema-valid planner 결과에서 guided fallback 또는 intent mode를 같은 request lock 안에서 정확히 한 번 확정한다. Legacy `configure_and_generate` 입력은 저장 전에 guided로 정규화하고 외부 응답에서만 negotiated legacy 표현으로 projection한다. Mode가 없는 기존 row는 read 시 guided로 해석하되 backfill하지 않는다. 한 번 확정된 requested mode는 불변이고 effective mode는 ADR-0054의 명시적 quick-to-guided transition에서 expected request version을 검증한 경우에만 변경한다. Generation mode는 session column이나 workflow graph에 저장하지 않는다.

Full operations 응답을 받은 뒤 CAS 저장 전에 client 응답이나 page state가 유실되면 server는 mutation을 재생할 수 없다. 해당 safe envelope를 `blocked`와 machine reason `operation_payload_unavailable`로 닫는다. Initial/graph-edit/replace request는 parent request를 contract-neutral cancel해 terminal 상태를 확인한 뒤에만 새 message request로 재생성한다. Parameter decision은 parent request를 유지하고 현재 task/version에서 새 operation id로 다시 입력하며 이전 envelope를 자동 적용하지 않는다. `before_graph` Knowledge 선택이 structural mutation을 발급한 경우에는 completion context의 resolution을 `unapplied`로 되돌리고 blocked structural boundary가 새 선택 operation을 막지 않게 정리한다. 반대로 CAS 저장은 완료됐지만 acknowledgement 응답만 유실된 경우에는 persisted workflow graph hash, `expected_result_graph_hash`, saved `result_graph_hash`, operation id와 workflow `updated_at`을 대조해 acknowledgement와 후속 task 상태를 복구한다.

### 9. Audit And Runtime Boundary

Mutation 발급, CAS 저장 결과, stale/permission/validation 차단, acknowledgement와 task/resolution 전환은 safe operation/workflow/node 식별자와 machine reason만 audit한다. Parameter 값 원문, raw user message, raw URL/path, raw Knowledge metadata, credential config와 secret은 mutation metadata, audit, trace 또는 repair prompt에 저장하지 않는다.

Canonical graph를 client가 non-destructive recovery로 다시 적용할 때는 server graph에 없는 editor-only presentation metadata를 복구할 수 있다. `displayNumber` 같은 metadata는 local node rendering에만 쓰고 canonical graph hash, CAS candidate, acknowledgement 또는 server 저장 graph에 포함하지 않는다. recovery는 Workflow history boundary, pending operation, redo memory를 초기화하거나 삭제하지 않는다.

Agent Builder GraphMutation CAS save와 persisted revert/redo save는 기존 transaction-bound `add_action_audit`를 같은 SQLAlchemy session으로 호출한다. Graph write와 이 Agent Builder audit insert 중 하나라도 실패하면 같은 transaction을 rollback하고 저장 성공을 반환하지 않는다. 일반 editor autosync는 공통 CAS와 canonical projection을 사용하지만 이 ADR에서 autosync마다 신규 audit event를 추가하지 않는다. 일반 editor 저장 감사 확대는 별도 후속 정책으로 다룬다. MBA-228에서는 신규 durable outbox, worker 또는 audit table을 추가하지 않는다.

GraphMutation 생성, local 적용, 저장과 acknowledgement 중 workflow 실행, Knowledge retrieval, 외부 action 호출 또는 credential 사용은 발생하지 않는다.

Node `configuration_state`는 client 또는 마지막 task action이 정하는 입력값이 아니라 Catalog `required_configuration` 전체에서 server가 계산하는 파생 상태다. 생성, parameter set/defer/skip, persisted Undo, recovery와 workflow test/run·deployment preflight마다 다시 계산한다. 필수 설정 하나라도 missing, deferred 또는 invalid면 `unresolved`이고 모두 유효할 때만 `resolved`다. Optional skipped parameter는 Catalog가 required로 선언하지 않은 한 unresolved 원인이 아니다. 계산 결과는 표시와 저장을 위해 graph에 materialize할 수 있지만 client 값은 권위로 신뢰하지 않는다. `unresolved` 외부 action node는 editor에서 저장할 수 있지만 server-side workflow test/run과 deployment create/activate preflight에서 차단한다. 모든 차단 surface는 HTTP `409`, `workflow.configuration_preflight.blocked`와 safe reason/action을 포함한 공통 preflight projection을 반환한다. Preflight는 catalog required configuration과 저장 graph만 검사하며 credential provider나 외부 API를 호출하지 않는다.

### 10. Planner Calls

정상 request는 planner provider를 한 번 호출한다. Schema-valid 결과가 semantic invariant만 위반하면 safe machine code만 사용해 repair를 최대 한 번 호출할 수 있으므로 provider 호출 총수는 최대 두 번이다. Provider, JSON 또는 schema 실패에는 repair하지 않고 fail-closed한다. Parameter, Knowledge와 task 전환은 planner를 다시 호출하지 않는다.

### 11. Parameter Decision Concurrency

각 Parameter task는 request `response_payload` 안에 단조 증가하는 `task_version`을 가진다. Decision request는 client-generated `operation_id`와 `expected_task_version`을 전달한다. Backend는 parent `AgentBuilderRequest` row를 write lock으로 조회하고 task id, version과 action별 허용 status를 확인한 뒤 같은 transaction에서 task state와 새 payload 객체를 저장한다. 일반 진행 decision은 active task만 변경하고, 값 설정 `set`은 `active|completed|skipped|deferred|invalid`에 허용하며 `pending|canceled`에는 허용하지 않는다. Optional `skip`은 graph를 바꾸거나 GraphMutation을 발급하지 않고 acknowledgement 없이 task를 `skipped`로 전환한 뒤 다음 task를 활성화한다. Required task의 skip은 거부한다.

ADR-0054의 remaining quick-completion proposal도 같은 동시성 경계를 사용한다. Proposal 생성은 `Workflow -> AgentBuilderRequest` 순서로 잠그고 expected graph hash/`updated_at`, request version과 대상 task version을 확인한다. Request와 각 confirm 대상 task version을 정확히 한 번 증가시킨 뒤 증가된 task id/version, recommendation fingerprint와 canonical graph hash/`updated_at`만 pending proposal에 저장하며 실제 parameter 값이나 graph fragment는 복제하지 않는다. Pending proposal이 예약한 task의 다른 decision은 proposal이 terminal이 될 때까지 `409 task_conflict`로 닫는다. Acknowledgement도 같은 lock 순서로 권위 workflow graph를 다시 읽어 저장된 graph revision, fenced version과 fingerprint를 검증하고 `completed`로 전환되는 모든 task version을 다시 정확히 한 번 증가시킨다. Revalidation 실패는 proposal을 `stale`로 terminal 처리하고 task 값과 graph를 변경하지 않는다. 응답과 persisted idempotency result에는 각 task id, terminal status와 증가된 version을 포함한다. 같은 operation/payload 재시도는 저장된 version을 그대로 반환하고 다시 증가시키지 않으며, proposal 이전 version을 사용한 늦은 `set`은 `409 task_conflict`로 닫는다.

같은 `operation_id`와 동일 payload의 `confirm|set|defer|skip|cancel` 재시도는 기존 safe operation 결과를 그대로 반환하고 GraphMutation 재구성, next task activation, commit 또는 audit를 반복하지 않는다. Catalog validation으로 `invalid`가 된 `set`도 safe operation 결과로 기록해 응답 유실 재시도에서 같은 validation issue와 task 상태를 반환한다. 같은 operation id에 다른 payload가 오면 충돌로 거부한다. Client는 같은 group/task/version 취소 재시도에 operation id를 재사용하고 응답이 불명확하면 canonical session의 `canceled` 상태를 reconcile한다. 같은 task/version에 서로 다른 operation이 동시에 도착하면 먼저 잠금을 획득해 commit한 요청만 GraphMutation 또는 task transition을 만들고, 나중 요청은 `409 task_conflict`로 닫는다. 중복 next task activation, duplicate GraphMutation과 nested JSON in-place mutation은 허용하지 않는다. Commit 뒤 새 session에서 request를 다시 조회해도 같은 task/version/operation 상태가 복구되어야 한다.

## Consequences

- Agent Builder 시작 전 graph와 최종 graph는 하나의 Workflow history boundary이며, parameter 변경과 Knowledge binding은 별도 Undo entry를 만들지 않고 같은 boundary의 final snapshot/hash만 갱신한다. 모든 mutation은 같은 lifecycle, recovery와 audit 경계를 사용한다.
- 같은 workflow의 동시 편집은 첫 CAS 저장만 성공하고 뒤 요청은 stale conflict가 된다. 실시간 merge, CRDT와 silent overwrite는 제공하지 않는다.
- 이 동시성 경계는 동일한 단일 session의 순차 stale 재현만으로 완료 판정하지 않고, 독립 PostgreSQL session/transaction의 autosync 대 autosync 및 autosync 대 Agent Builder 저장 경쟁으로 검증한다.
- 같은 parameter task/version의 동시 decision은 첫 commit만 성공하고 뒤 요청은 task conflict가 된다. 동일 operation 재시도는 같은 결과를 반환한다.
- 일반 workflow save API에는 Agent Builder용 additive `mutation_context`와 canonical 저장 결과가 추가된다.
- Full typed operations는 응답 유실 뒤 server에서 재생할 수 없지만 request/session 저장소에 graph 변경 원문이나 parameter 값을 남기지 않는다. CAS 저장 전 유실은 재생성·재입력하고 저장 뒤 acknowledgement 유실만 canonical graph와 hash로 복구한다.
- Preview protocol을 direct-edit protocol과 병행 fallback으로 운영하지 않는다. Legacy 미적용 작업은 `stale_protocol`로 닫고 재생성한다.
- unresolved graph는 편집·저장할 수 있지만 실행·배포할 수 없다.

## Non-Goals

- 실시간 공동 편집, 자동 merge 또는 CRDT
- Agent Builder 전용 신규 DB table이나 독립 server
- Agent Builder 전용 credential resource, secret 저장소 또는 secret replay payload 추가
- workflow 생성 중 외부 action 실행
- runtime에 존재하지 않는 신규 node capability 추가
- Agent Builder intent model 표시 순서 변경
## 2026-07-14 Correction: Canonical Layout Mutation

- `initial_graph`, `replace_workflow`, `graph_edit` mutation은 server auto-layout 결과를 candidate graph에 적용한다.
- 새 node 위치는 `add_node.node.position`에, 기존 node가 이동한 위치는 `replace_node_position` typed operation에 포함한다.
- client는 server가 발급한 위치 operation만 적용하고 저장 후 viewport를 맞출 수는 있어도 별도의 client layout 저장을 수행하지 않는다.
- mutation 적용 전 client는 canonical draft graph를 base로 사용한다. 화면에 남은 stale node는 server operation의 base가 될 수 없다. 동일 node의 display number는 화면 전용 값으로만 보존한다.

## 2026-07-18 Correction: Cancellation Safe Envelope

- Direct-edit request가 취소되어 terminal response를 저장할 때도 일반 성공·실패 응답과 같은 safe operation envelope serializer를 사용한다.
- 취소 응답에는 full typed `operations`, raw graph snapshot, parameter 원문, raw Knowledge metadata, credential 또는 secret을 저장하지 않는다.
- 취소 여부와 관계없이 persisted request/session payload만으로 GraphMutation을 재생할 수 없어야 한다.

## 2026-07-19 Correction: Terminal Third-Graph Reconciliation

- Undo/Redo 저장 결과가 두 번 불명확하고 canonical graph가 요청 graph와 반대 graph 모두 아닌 것으로 확인되면 더 이상 이전 history boundary를 재시도하지 않는다.
- Client는 canonical graph와 graph hash/`updated_at`을 같은 workflow editor state에 반영하고 `pendingAgentBuilderRevert`, pending Redo memory, Agent Builder apply snapshot과 해당 workflow의 Undo/Redo stack을 비운다.
- 이 reconciliation은 server graph를 권위로 수용하는 fail-safe 종료이며 typed mutation 재생, task 재활성화, 자동 merge 또는 추가 workflow save를 수행하지 않는다.

## 2026-07-19 Correction: Existing Editor Secret Save Bridge

- ADR-0045가 허용하는 Slack Bot Token, Slack Incoming Webhook URL과 GitHub API Token은 Agent Builder 카드에서 masked input으로 수집하고 기존 Workflow editor draft save adapter로 저장한다.
- Raw secret은 Agent Builder ParameterDecision, typed GraphMutation, safe operation envelope, session/task persistence, acknowledgement payload, audit, trace 또는 log에 포함하지 않는다.
- 이 correction은 기존 secret task 제외 계약을 대체한다. Mail/Gmail managed credential reference 계약은 변경하지 않는다.
