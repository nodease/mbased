# Workflow Test Cases

Status: Draft

## Test File Mapping

- Top-aligned workflow ranks, boundary-centered BaseNode handles, Default/input alignment, and downward Condition branches: `apps/client/app/features/workflow/utils/nodeHandleLayout.test.ts`, `apps/client/app/features/workflow/utils/arrangeConditionNodes.test.ts`, `apps/shared/tests/test_workflow_layout.py`

- 실행 편의성: `apps/client/app/features/workflow/tests/execution-convenience.test.ts`
- 테스트 실행 사이드바 폭 조절: `apps/client/app/features/workflow/tests/test-sidebar-resize.test.tsx`
- 노드 조작 편의성: `apps/client/app/features/workflow/tests/node-panel-resize.test.ts`
- 워크플로우 조작 편의성: `apps/client/app/features/workflow/tests/workflow-delete-reconnect.test.tsx`
- 노드 실행 기록 패널 추가: `apps/client/app/features/workflow/tests/node-execution-log-panel.todo.test.ts`
- 공통 그래프 검증: `apps/client/app/features/workflow/tests/utils/validateWorkflowGraph.test.ts`
- 공통 catalog/Backend 연결 정책: `apps/shared/tests/test_workflow_node_catalog.py`, `apps/gateway/tests/services/test_agent_builder_service.py`
- 인증 배포 실행/RAG 경계: `apps/gateway/tests/services/test_chatbot_deployment_run.py`, `apps/gateway/tests/api/test_deployment_permissions.py`, `apps/client/app/features/app/tests/moduleRunNavigation.test.ts`, `apps/client/app/features/workflow/tests/deploymentRunResult.test.ts`
- 공개/내부 챗봇 UI·인증 복귀: `apps/client/app/features/workflow/hooks/useDeployment.test.tsx`, `apps/client/app/features/workflow/components/deployment/SuccessStep.test.tsx`, `apps/client/app/modules/[id]/run/page.test.tsx`, `apps/client/lib/authReturn.test.ts`, `apps/client/app/auth/login/page.test.tsx`
- Deployment type/audience 정책: `apps/shared/tests/domain/test_deployment_runtime_policy.py`, `apps/gateway/tests/application/deployment/test_preflight_use_case.py`
- MBA-190 외부 부수효과 멱등성: `apps/workflow_engine/tests/domain/test_external_effect_contract.py`, `apps/workflow_engine/tests/domain/test_external_effect_identity_runtime.py`, `apps/workflow_engine/tests/application/test_external_effect_executor.py`, `apps/workflow_engine/tests/adapters/test_external_effect_repository.py`, `apps/workflow_engine/tests/adapters/test_external_effect_provider_adapters.py`, `apps/workflow_engine/tests/composition/test_external_effect_readiness.py`, `apps/workflow_engine/tests/fakes/external_effects.py`, `apps/workflow_engine/tests/nodes/test_http_node.py`, `apps/workflow_engine/tests/nodes/test_loop_external_effect_control.py`, `apps/workflow_engine/tests/nodes/test_workflow_node.py`, `apps/workflow_engine/tests/services/test_workflow_engine_tracing.py`, `apps/workflow_engine/tests/services/test_workflow_logger_tracing.py`, `apps/workflow_engine/tests/test_workflow_tasks_rag_sync.py`, `apps/log_system/tests/test_node_log_retry_flow.py`, `apps/gateway/tests/api/test_workflow_execution_subject.py`, `apps/gateway/tests/api/test_workflow_external_effect_error_contract.py`, `apps/gateway/tests/application/deployment/test_workflow_node_binding.py`, `apps/shared/tests/test_external_effect_attempt_schema.py`, `apps/shared/tests/db/test_external_effect_disposable_postgres.py`, `apps/shared/tests/domain/test_workflow_execution_identity.py`, `apps/shared/tests/domain/test_workflow_node_binding.py`, `apps/shared/tests/services/test_external_effect_trace_capture.py`, `apps/shared/tests/services/test_workflow_task_publisher.py`
- Workflow log 발행 격리: `apps/workflow_engine/tests/services/test_workflow_logger_tracing.py`에서 직렬화/Celery 발행 실패가 실행 결과를 실패로 바꾸지 않고, 민감한 예외 원문을 로그에 남기지 않는지 검증한다.
- MBA-283 Generic HTTP egress: `apps/workflow_engine/tests/adapters/test_guarded_outbound_http.py`, `apps/workflow_engine/tests/adapters/test_external_effect_provider_adapters.py`, `apps/workflow_engine/tests/nodes/test_http_node.py`, `apps/shared/tests/deployment/test_workflow_worker_egress_policy.py`
- MBA-286 scheduler readiness: `apps/workflow_engine/tests/nodes/test_workflow_scheduler_readiness.py`, `apps/workflow_engine/tests/nodes/test_parallel_execution_optimization.py`, `apps/workflow_engine/tests/test_condition_branch_routing.py`
- MBA-285 child lifecycle: `apps/workflow_engine/tests/services/test_child_execution_lifecycle.py`, `apps/workflow_engine/tests/nodes/test_loop_graph_entry.py`, `apps/workflow_engine/tests/nodes/test_workflow_node.py`, `apps/workflow_engine/tests/domain/test_external_effect_identity_runtime.py`
- MBA-287 durable provider usage: `apps/shared/tests/domain/test_provider_usage_ledger.py`, `apps/shared/tests/services/test_provider_usage_ledger.py`, `apps/shared/tests/services/test_provider_usage_cost_read_model.py`, `apps/shared/tests/db/test_provider_usage_ledger_disposable_postgres.py`, `apps/workflow_engine/tests/adapters/test_provider_usage.py`, `apps/workflow_engine/tests/nodes/test_llm_node_provider_execution_capability.py`, `apps/log_system/tests/test_provider_usage_tasks.py`, Gateway budget/admin/member usage service tests
- 동시성 처리: `apps/gateway/tests/integration/test_agent_builder_workflow_cas.py`에서 독립 PostgreSQL session/transaction으로 autosync 대 autosync 및 autosync 대 Agent Builder 저장 경쟁을 실행하고 한 요청만 성공하며 다른 요청이 `409 stale_graph`인지 검증한다.
- 테스트 실행 전 저장: `TestSidebar` component test에서 canonical draft GET의 `graph_hash`/`updated_at`이 save request에 전달되고 성공 응답 metadata가 shared Workflow store에 반영되며 stale save는 실행을 시작하지 않는지 검증한다.
- Agent Builder 저장 중 test preflight: 같은 workflow의 Agent Builder save/acknowledgement 중에는 TestSidebar 저장과 실행 API가 호출되지 않고, 확정 뒤 버튼이 다시 활성화되는지 검증한다.
- Workflow save coordinator: test preflight, Agent Builder, autosync, Undo/Redo와 version restore가 같은 workflow에서 동시에 draft POST를 시작하지 않고 다른 workflow 저장은 독립적인지 검증한다. Execution stream 직전에 이 owner 중 하나라도 대기하면 실행을 시작하지 않으며, 대기자가 없으면 valid `workflow_start.run_id`까지 lock을 유지해 그 사이 도착한 저장이 snapshot 확정 뒤에만 시작되는지 검증한다. 실행 시작 실패·취소·timeout에서도 lock이 해제되는지 검증한다.
- Autosync lock coalescing: test preflight 또는 Agent Builder가 lock을 보유한 동안 여러 autosync 회차가 발생해도 대기 작업은 하나이고, lock 해제 뒤 같은 active workflow의 최신 dirty graph만 한 번 저장하는지 검증한다. 대기 중 workflow가 바뀌면 이전 workflow와 새 workflow 모두에 저장하지 않는다.
- Agent Builder workflow switch: Agent Builder가 save lock을 기다리거나 canonical draft를 조회하는 동안 active workflow가 바뀌면 mutation/save/acknowledgement/rollback을 수행하지 않고 새 workflow의 graph, metadata와 Undo/Redo history를 보존하는지 검증한다.
- Version restore workflow switch: Agent Builder save lock을 기다리는 동안 active workflow가 바뀌면 version restore가 canonical draft GET/POST를 호출하지 않고 새 workflow의 graph, metadata와 Undo/Redo history를 보존하는지 검증한다.
- Version restore Note precedence: modern `features.noteNodes`, 명시적 빈 배열, legacy `nodes` Note fallback과 Note 표현이 없는 legacy snapshot의 현재 Note 보존을 각각 검증한다.
- Test save recovery: `409 stale_graph` 뒤 canonical graph가 editor snapshot과 같을 때만 metadata를 수용하고 재저장 없이 실행하며, 다르면 metadata 갱신, 실행과 overwrite를 모두 차단하는지 검증한다. `operation envelope not found`에서는 Agent Builder safe envelope와 canonical hash/`updated_at`으로 `applied|unapplied|pending|stale`을 판정한다. acknowledged hash만 같고 timestamp가 다르면 `applied`가 아니며, 두 값이 모두 같은 `applied`에서만 실행하고 typed operation을 재생하지 않는다.
- Test save error UX: `401`, `403`, `409 stale_graph`, `409 operation envelope not found`, 일반 `4xx`, network/`5xx`가 구분되고 모든 실패에서 test stream이 열리지 않는지 검증한다.
- Dirty test preflight stale protection: local edit base보다 앞선 canonical metadata를 dirty graph에 주입하지 않고 draft POST와 test stream을 모두 차단하는지 검증한다.
- Locked save permission recheck: endpoint permission 통과 뒤 write 권한을 회수한 경쟁 상황에서 row lock 이후 일반 save와 Agent Builder save가 모두 `403`이고 graph/features/audit이 불변인지 검증한다.
- Recursive feature projection: Agent Builder와 일반 save가 최상위/중첩 edge selection 및 note presentation field를 같은 결과로 제거하고, malformed `features.noteNodes`를 safe `422`로 닫는지 검증한다.
- 빈 canonical draft 조회: `apps/gateway/tests/services/test_workflow_draft_read.py`에서 DB graph가 `null` 또는 빈 object인 신규 workflow도 빈 `nodes`/`edges`, 기본 viewport와 canonical metadata를 반환하는지 검증한다.

`*.todo.test.ts`의 `it.todo` 항목은 아직 대응 구현 또는 API 계약이 없는 테스트 케이스다. 구현 시 같은 파일에서 실제 assertion 테스트로 전환한다.

Frontend 공통 그래프 검증은 catalog v2의 incoming/outgoing 금지 정책과 parity를 유지해야 한다. Start/Webhook/Schedule incoming, Answer outgoing, 잘못된 Condition source handle을 거부하고, Agent Builder direct-edit GraphMutation/CAS save도 같은 graph를 다시 거부하는지 검증한다.

## Runtime Scheduler Readiness Tests

- selector가 없는 fan-in은 첫 predecessor 결과만으로 실행되지 않고 실제 active incoming predecessor가 모두 성공한 뒤 정확히 한 번 실행된다.
- Condition case/default 중 선택되지 않은 edge와 downstream 경로는 inactive로 전파되며 join은 해당 branch를 기다리지 않는다. 여러 inactive branch가 중간 join에서 다시 합쳐져도 상태 변화마다 downstream을 재평가하며, 선택되지 않은 branch의 node와 side effect는 실행되지 않는다.
- 별도 control path에 있는 selector source를 기다리는 target은 control edge만 준비된 시점에는 실행되지 않으며, selector source 완료가 target을 다시 깨워 한 번 실행한다.
- selector source가 inactive 경로에 있으면 dependent node는 누락된 값으로 실행되지 않고 inactive로 전파된다.
- control completion과 selector completion이 같은 target을 중복 후보로 만들더라도 `pending -> queued` 전이는 한 번이고 node 실행 cardinality도 1이다.
- active predecessor 실패 또는 workflow/node timeout 뒤 join과 downstream side effect는 시작되지 않으며 기존 fail-fast error 경계를 유지한다.
- 서로 다른 active sibling node의 기존 병렬 실행은 유지한다. selector가 특정 predecessor 하나만 참조하더라도 target으로 들어오는 다른 active control edge가 있으면 해당 predecessor도 완료될 때까지 기다린다.

## Conversation Memory Runtime Target Tests

- Memory-enabled task는 contract version, storage generation과 minimum Worker capability가 일치할 때만 외부 node side effect를 시작한다.
- Rolling deployment의 versioned/capability queue에서 구버전 Worker가 target task를 소비하지 않는다.
- 잘못 라우팅된 target task는 runtime admission에서 fail-closed하고 node/tool/provider를 호출하지 않는다.
- 같은 Memory dispatch가 중복 전달되어도 하나의 Workflow execution admission만 생성된다.
- `AdmitExecution` same-dispatch retry는 같은 admission reference를 반환하고 다른 dispatch는 별도 admission을 만든다.
- Memory acknowledgement가 유실되면 `GetExecutionAdmission(dispatch_id)`으로 queued/running/terminal safe projection을 복구한다.
- Memory adapter는 Workflow execution lease/heartbeat를 갱신할 수 없다.
- Workflow node/tool의 외부 side effect retry는 해당 node idempotency 계약을 따르며 Memory dispatcher가 arbitrary execution을 무조건 재실행하지 않는다.
- Knowledge, connector/tool, subworkflow, LLM, transform/code, Condition/Switch/Loop와 final output은 RuntimeDataDependencyEnvelope source-owner/union contract를 보존한다.
- LLM output은 Memory Context dependency를 prompt/retrieval/tool dependency와 합산해 final output까지 전달한다.
- Transform/code node가 dependency를 제거하거나 canonical dependency를 발급하면 거부하고, provenance-incomplete private/sensitive output은 Memory write 전에 fail-closed 한다.
- Optional dependency flag를 주입해도 V1은 모든 값·활성 control dependency를 필수로 평가한다.
- Private predicate가 선택한 상수 branch output은 predicate dependency를 상속하고, 선택되지 않은 branch의 값 dependency는 final envelope에서 제외한다.
- Loop iterable/bound/continue/termination dependency는 실행된 body와 loop aggregate/final output까지 전파되며 control lineage가 unknown이면 private/sensitive Memory write를 거부한다.
- Explicit complete empty envelope은 source 없는 pure input/transform에서 허용하지만 missing/unknown envelope을 empty로 승격하지 않는다.
- Subworkflow는 target deployment version과 child envelope 합집합을 parent output에 전달한다.
- Memory admission/task는 deployment version/snapshot과 mapping/Memory policy version에 고정되고 active deployment 교체 후 새 graph로 자동 rebind하지 않는다.
- Main/summary/query-embedding provider는 LLM Credentials가 발급한 opaque ProviderExecutionCapability identity/revision과 deployment/canonical node location/admission/provider-attempt/purpose binding이 일치할 때만 호출한다.
- Usage ledger round trip은 structured `container_path`를 보존하고, 다른 Loop에서 같은 `node_id`를 사용한 replay는 binding conflict로 닫는다.
- Capability provider는 durable intent와 provider-start fence가 각각 commit된 뒤 정확히 한 번 호출된다. Duplicate delivery가 started/succeeded/outcome-unknown operation을 찾으면 provider를 다시 호출하지 않는다.
- Provider timeout·response loss·unknown exception, malformed 또는 admitted cap 초과 usage와 success 저장 실패는 outcome unknown으로 수렴하고 routing fallback·Celery retry·동일 lease 재호출을 허용하지 않는다.
- Provider HTTP `401`/`403`은 typed `provider_rejected` definitive failure로 즉시 terminalize하고, `429`/`5xx`는 definitive rejection으로 오분류하지 않는다.
- WorkflowRun이 provider success보다 늦게 생성되어도 canonical usage/cost는 즉시 보존되고 compatibility row는 nullable run으로 먼저 수렴한다. Exact workflow correlation이 확인될 때만 run을 연결하며 mismatch는 canonical ledger를 바꾸지 않는다.
- Compatibility projection과 run-finish legacy 합계 재계산이 경합해도 같은 WorkflowRun fresh `FOR UPDATE`를 사용해 committed token/cost delta를 잃지 않는다.
- Authenticated/public/system 실행의 execution subject, credential principal, billing principal과 audit actor는 ledger round trip에서 독립적으로 보존된다. Public/system actor를 credential principal 또는 deployment creator로 바꾸면 실패한다.
- Same terminal replay와 correction/projection replay는 operation당 `llm.call` audit 한 건과 `llm_usage_logs` 한 행으로 수렴한다. Ledger-linked projection은 budget/admin 합계에 중복 포함되지 않고 unresolved operation은 active budget을 fail-closed한다.
- Credential revoke/permission decision revision 또는 verified relation/provider-routing revision 변경 뒤 stale capability는 새 claim/reservation/attempt/provider call에 사용할 수 없다. 이 fingerprint는 중앙 egress authorization을 대체하지 않는다.
- Public Access Grant, credential/billing principal과 app owner는 execution subject 또는 audit actor로 승격되지 않는다.
- Preflight 뒤 Worker pool capability가 바뀌어도 runtime guard가 incompatible task를 거부한다.

## Mail Credential Reference Tests

- Mail node editor는 safe credential option을 표시하고 선택 시 graph에 `credential_id`만 저장한다.
- Client node, panel, visible properties와 실행 로그 설정 요약은 email/password/token/ciphertext와 credential UUID를 렌더링하지 않고 연결 상태만 표시한다.
- Workflow 저장은 최상위와 중첩 `subGraph`의 inline Mail secret field, 잘못된 UUID, 다른 organization reference, revoked credential과 `use` 권한 없는 reference를 provider 호출 없이 거부한다. 제한된 UI metadata는 허용한다.
- Agent Builder가 생성한 unresolved Mail node는 direct-edit GraphMutation/CAS save가 가능하지만 deployment 생성·활성화와 runtime 실행은 credential reference를 요구한다. Preview/apply-save 제품 경로는 사용하지 않는다. `configuration_state` 도입 전 Client가 만든 `credential_id=null` Mail node는 상태 필드가 누락돼도 draft 저장과 inactive warning은 유지하며, 명시적 null 상태는 invalid이고 실행은 unresolved로 차단한다.
- 기존 Mail graph는 `processing_mode`가 없으면 `search_only`로 역직렬화된다.
- Durable Mail graph는 `mark_as_read=true`를 거부하고 Mail Acknowledge node가 required effect 성공 뒤에만 읽음 처리한다.
- Gmail Draft node는 processing/reply selectors와 OAuth credential만 허용하고 send/recipient/MIME/provider id field를 거부한다.
- Mail Acknowledge node는 같은 organization/workflow processing/effect reference만 허용하고 client success boolean을 거부한다.
- 동일 processing/effect task 재전달과 provider outcome-unknown에서 Gmail create 호출 횟수는 1회를 넘지 않는다.
- 기존 활성 Draft claim을 본 중복 실행은 Gmail provider를 호출하지 않는다.
- OAuth Gmail Mail node는 REST provider를 사용하고 IMAP/XOAUTH2를 열지 않으며 provider message id를 node output에 노출하지 않는다.
- OAuth Gmail acknowledgement는 REST `messages.modify`를 사용하고, provider 401/403/429/5xx/timeout은 safe code로 분류한다.
- Mail node output이 downstream LLM/Draft input에 중첩되어도 durable trace에는 body/subject/recipient/processing ref가 없고 count/folder 또는 input count 요약만 남는다.
- 단일 Gmail Draft source가 `max_results != 1`이거나 top-level `processing_ref` output을 제공하지 않으면 배포를 거부한다.
- Unresolved Draft/Acknowledge node는 편집 draft로 저장할 수 있지만 source/effect selector, graph path, 동일 Gmail OAuth credential이 해결되지 않으면 배포할 수 없다.
- Runtime은 인증 test/deployment 표면에서 canonical organization, 명시 execution subject, active 상태와 `use` 권한을 재검증한다. Public/schedule 표면은 App/workflow owner의 `user_id`로 fallback하지 않고 명시 주체가 없으면 차단한다.

## Slack Delivery Runtime Tests

- Slack Web API는 `200` JSON `ok=true`와 valid `ts`만 성공으로 판정하고, malformed JSON, `ok=false`, missing `ts`, non-`200`은 explicit allowlist에 따라 safe rejection 또는 outcome-unknown으로 분류한다. Incoming Webhook은 `200`과 정확한 `ok`만 성공이다.
- Incoming webhook은 `200` UTF-8 `ok`만 성공으로 판정한다. JSON body, redirect, oversized/invalid-encoding body, read/write timeout은 성공으로 처리하지 않는다.
- URL/egress 검증은 API fixed endpoint와 commercial webhook exact host/path만 허용하고 private target, userinfo, fragment, non-default port, redirect, proxy environment override를 거부한다. GovSlack API와 webhook은 별도 profile 승인 전 거부한다.
- credential, webhook target, raw request/response/body/header와 provider exception은 node output, trace, audit, log, frontend error에서 노출되지 않는다. secret wrapper는 serialization/copy/pickle 경로로도 원문을 노출하지 않는다.
- Slack provider failure와 outcome-unknown은 Celery generic retry를 발생시키지 않는다. `429`의 bounded `Retry-After`도 trace hint일 뿐 재시도를 예약하지 않는다. 동일 stable slot의 성공 duplicate는 공통 durable attempt의 safe projection을 반환하고 provider 호출은 1회를 유지한다. 결과 불명·mode/operation/payload 충돌도 provider를 다시 호출하지 않는다.
- editor, save/deploy validation, Agent Builder는 generic HTTP Slack 설정과 `data`/`headers` selector를 새 graph에 만들지 않는다. `value_selector`, `variable_selector`, `source_selector`, 단일·복수형 Mail selector를 포함한 모든 `*_selector`/`*_selectors` 필드에서 제거된 raw output을 탐지하고, Client/deployment/legacy runtime에서 차단한다. Webhook mode의 `message_ref`도 차단하되 API mode의 `message_ref`는 허용한다. Backend draft와 명시 migration은 과거 snapshot을 자동 변형하지 않는다.
- unit, adapter, runtime, client validation 및 authenticated browser smoke는 API/webhook success/failure, legacy compatibility, no-retry, safe observability, output selector와 migration warning을 각각 검증한다.
- Slack template은 실제 사용된 `{{name}}` 등록 변수만 조회하며 미사용 reference의 누락 input은 무시한다. JSON 문자열 안의 quote/bracket payload는 escape 후에도 원래 scalar로 남고 구조를 바꾸지 못해야 한다. Jinja attribute/global 접근, expression, filter, statement/control flow와 동적 JSON key는 provider 호출 전에 거부한다.
- deployment validation은 canonical commercial webhook URL과 mode별 credential을 검사하고, 공백이 아닌 `message`/비어 있지 않은 `blocks`/비어 있지 않은 `attachments` 중 하나 이상을 요구한다. Webhook mode의 숨은 레거시 `channel`은 무시하며 request payload에 포함하지 않는다. Client는 API mode에서 URL을 요구하지 않으며 API/Webhook mode별 output과 nested legacy selector 경고를 다르게 표시한다.
- DNS/egress preflight와 HTTP connect/read는 각각 bounded timeout을 가지며, provider 실패 trace는 raw payload 없이 safe outcome과 status만 보존한다.
- IMAP resolver는 private/loopback/metadata target과 `143/993` 이외 포트를 거부하고, `143`에서는 로그인 전에 STARTTLS를 강제한다. Local/test direct mode는 DNS 검증 IP에 socket 연결을 고정한다. Production proxy mode는 같은 IP를 Worker-only CONNECT authority로 보내고 TLS hostname 검증은 canonical hostname으로 수행하며, tunnel 거부·timeout 뒤 origin direct dial은 0회다.
- Legacy inline password graph는 validation error에 secret 값을 포함하지 않고 fail-closed한다.

## MBA-356 Fixed SaaS Outbound Tests

- GitHub pull request 조회/comment와 Slack API/webhook happy path가 기존 request와 safe output projection을 유지하면서 operation-bound guarded transport를 사용하는지 검증한다.
- Wrong origin, private/mixed DNS, peer mismatch, redirect와 response byte 상한 위반은 추가 provider 요청 없이 차단되고 전송 전 실패와 outcome unknown을 구분하는지 검증한다.
- Slack의 connect/write/read/pool timeout은 operation profile 상한 안에서 기존 단계별 값이 유지되며 상한 초과 설정은 client 생성 전에 거부되는지 검증한다.
- 대상 production node/service/adapter가 직접 `requests` 또는 `httpx.Client|AsyncClient`를 생성하지 않는 architecture test를 유지한다.
- GitHub read는 effect ledger 밖의 기존 조회 계약, GitHub comment와 Slack mutation은 기존 replay/result reuse 계약을 유지하며 transport 이관이 provider 재호출을 추가하지 않는지 검증한다.

## Spec Document Mapping

`Demo Test Priority` 표의 `영역` 컬럼은 아래 spec 문서 섹션과 대응된다. 테스트를 구현하거나 우선순위를 바꿀 때는 대응하는 `requirements.md`, `api_spec.md`, `component_spec.md`를 함께 확인한다.

| 테스트 영역 | requirements.md | api_spec.md | component_spec.md | 문서 상태 |
| --- | --- | --- | --- | --- |
| 실행 편의성 | `docs/features/workflow/requirements.md`의 `### 1. 실행 편의성` | `docs/features/workflow/api_spec.md`의 `### 1. 실행 편의성` | `docs/features/workflow/component_spec.md`의 `### 1. 실행 편의성` | 매핑됨 |
| 노드 조작 편의성 | `docs/features/workflow/requirements.md`의 `### 2. 노드 조작 편의성` | `docs/features/workflow/api_spec.md`의 `### 2. 노드 조작 편의성` | `docs/features/workflow/component_spec.md`의 `### 2. 노드 조작 편의성` | 매핑됨 |
| 워크플로우 조작 편의성 | `docs/features/workflow/requirements.md`의 `### 3. 워크플로우 조작 편의성` | `docs/features/workflow/api_spec.md`의 `### 3. 워크플로우 조작 편의성` | `docs/features/workflow/component_spec.md`의 `### 3. 워크플로우 조작 편의성` | 매핑됨 |
| 노드 실행 기록 패널 추가 | `docs/features/workflow/requirements.md`의 `### 4. 노드 실행 기록 패널 추가` | `docs/features/workflow/api_spec.md`의 `### 4. 노드 실행 기록 패널 추가` | `docs/features/workflow/component_spec.md`의 `### 4. 노드 실행 기록 패널 추가` | 매핑됨 |
| 동시성 처리 | 문서 보완 필요. 현재 `requirements.md`에 독립 요구사항 섹션이 없다. | 문서 보완 필요. 현재 `api_spec.md`에 draft revision, stream session, cursor 동시성 계약이 없다. | 문서 보완 필요. 현재 `component_spec.md`에 stale response, conflict UI, 중복 실행 방지 상태가 없다. | 미매핑 |
| 정책 확정 필요 | `docs/features/workflow/requirements.md`의 `## Open Questions`와 연결 | `docs/features/workflow/api_spec.md`의 `## Errors` 또는 `## Permissions` 보완 필요 | 정책 확정 후 관련 interaction/accessibility 섹션 보완 필요 | 부분 매핑 |

## Demo Test Priority

데모 전까지 모든 테스트를 같은 비중으로 구현하지 않는다. 안정적으로 시연되는 workflow 생성/편집/테스트 실행 경로를 우선하며, 테스트 케이스는 다음 3등급으로 나눈다.

- `Priority 1`: 데모 전에 반드시 구현하고 통과시킨다. 실패하면 핵심 시연 흐름이 깨지거나 사용자에게 오류로 보인다.
- `Priority 2`: 데모 안정성을 높이는 항목이다. 시간이 남으면 구현하고, 데모 전에는 수동 QA 또는 todo로 추적한다.
- `Priority 3`: 운영 품질, 대규모 사용, 장기 안정성 항목이다. 데모 이후 별도 이슈로 구현한다.

| Priority | 영역 | 테스트/요구 항목 | 현재 상태 | 미구현/미통과 사유 | 대응 파일 |
| --- | --- | --- | --- | --- | --- |
| 1 | 실행 편의성 | 노드 output token/cost 읽기와 `-` fallback 표시 | 통과 | 구현 및 unit test 완료 | `apps/client/app/features/workflow/tests/execution-convenience.test.ts` |
| 1 | 실행 편의성 | 노드별 실행 상태·시간과 LLM 사용량 표시 | 통과 | 모든 노드는 상태·시간을 표시하고, `llmNode`만 비용·토큰을 표시한다. node summary parser와 `TestSidebar` 표시 경로 구현 완료 | `apps/client/app/features/workflow/tests/execution-convenience.test.ts`, `apps/client/app/features/workflow/tests/test-sidebar-node-detail.test.tsx`, `apps/client/app/features/workflow/components/editor/TestSidebar.tsx` |
| 1 | 실행 편의성 | 실행 중 상태 중복 표시 방지 | 통과 | 진행 상태는 각 노드 카드에서 표시하고 노드 목록 위에 전역 `테스트 실행 중` 제목을 중복 표시하지 않는다 | `apps/client/app/features/workflow/tests/test-sidebar-execution-comparison.test.tsx` |
| 1 | 실행 편의성 | `node_finish` 표준 필드 `latency_ms`, `total_tokens`, `total_cost` 우선 표시 | 통과 | 프론트 summary parser unit test 완료. Gateway/engine API contract test는 별도 API test infra에서 다룬다. | `apps/client/app/features/workflow/tests/execution-convenience.test.ts` |
| 1 | 실행 편의성 | 최종 서버 실행 시간, 화면 완료 시간, 비용, 토큰 요약 표시 | 통과 | workflow-level summary 우선 사용과 node 합산 fallback unit test 완료 | `apps/client/app/features/workflow/tests/execution-convenience.test.ts` |
| 1 | 실행 편의성 | 테스트 성공 후 최종 사용자가 받는 `최종 응답` 카드 표시 | 통과 | 사용자 응답 형태의 workflow output, answer/response node, LLM output fallback helper와 카드 render test 완료. 전체 node 실행 컨텍스트는 Answer node 출력보다 우선하지 않는다. | `apps/client/app/features/workflow/tests/testExecutionFinalResponse.test.ts`, `apps/client/app/features/workflow/tests/test-sidebar-final-response-card.test.tsx`, `apps/client/app/features/workflow/components/editor/TestSidebar.tsx` |
| 1 | 실행 편의성 | JSON/긴/빈/정책 차단성 최종 응답 preview 처리 | 통과 | JSON key/value preview, empty state, 긴 응답 보존, 민감 key redaction unit test 완료 | `apps/client/app/features/workflow/tests/testExecutionFinalResponse.test.ts`, `apps/client/app/features/workflow/tests/test-sidebar-final-response-card.test.tsx` |
| 1 | 실행 편의성 | 서버 실행 시간과 화면 완료 시간을 서로 다른 라벨로 표시 | 통과 | `TestSidebar`가 `서버 실행`/`화면 완료` 라벨을 분리하고 summary unit test 완료 | `apps/client/app/features/workflow/tests/execution-convenience.test.ts`, `apps/client/app/features/workflow/components/editor/TestSidebar.tsx` |
| 1 | 실행 편의성 | 테스트 실행 중복 클릭 방지 또는 기존 stream 정리 | 통과 | 실행 중/업로드/저장 중/권한 없음 disabled 조건 unit test 완료 | `apps/client/app/features/workflow/tests/execution-convenience.test.ts` |
| 1 | 실행 편의성 | stream 실패 시 사용자에게 실패 상태 표시 | 통과 | 실패 상태 store transition unit test와 `TestSidebar` 실패 UI 구현 완료 | `apps/client/app/features/workflow/store/useWorkflowStore.test.ts`, `apps/client/app/features/workflow/components/editor/TestSidebar.tsx` |
| 1 | 실행 편의성 | 테스트 실행 사이드바 기본 폭·드래그 최대 폭·키보드 최소 폭·패널 글자 크기 | 통과 | 기본 `560px`, `440px`~`720px` clamp, 왼쪽 handle pointer/keyboard 조작과 패널 내 글자 한 단계 확대 unit test 완료 | `apps/client/app/features/workflow/tests/test-sidebar-resize.test.tsx`, `apps/client/app/features/workflow/components/editor/TestSidebar.tsx` |
| 1 | 실행 편의성 | 테스트 질문 textarea 기본 높이 | 통과 | `paragraph` 타입 질문 입력은 최소 높이 `180px`를 사용하고 기존 세로 크기 조절을 유지한다 | `apps/client/app/features/workflow/tests/test-sidebar-final-response-card.test.tsx`, `apps/client/app/features/workflow/components/editor/TestSidebar.tsx` |
| 1 | 실행 편의성 | 같은 workflow 재동기화 뒤 최신 테스트 실행 상태 유지 | 통과 | 같은 `activeWorkflowId` 재설정은 TestSidebar 실행 상태를 idle로 초기화하지 않는다 | `apps/client/app/features/workflow/store/useWorkflowStore.test.ts` |
| 1 | 실행 편의성 | 저장된 workflow run을 TestSidebar 복원 상태로 변환 | 통과 | node run status/duration/usage/cost/safe trace metadata를 복원하고 duration을 ms로 변환한다. `running` run은 실패로 바꾸지 않는다. | `apps/client/app/features/workflow/tests/test-execution-restore.test.ts` |
| 1 | 실행 편의성 | 빈 draft의 URL 실행 복원 | 통과 | canonical draft metadata가 준비되기 전에는 복원을 기다리고, 준비된 draft의 node가 0개여도 `testRun` 상세를 조회해 저장된 과거 node 결과를 복원한다. | `apps/client/app/features/workflow/tests/test-sidebar-run-restore.test.tsx`, `apps/client/app/features/workflow/components/editor/TestSidebar.tsx` |
| 1 | 실행 편의성 | 현재 graph에 없는 과거 노드 실행 결과 표시 | 통과 | 복원된 node run의 노드가 현재 draft에서 삭제되어 node data가 없고 token/cost가 누락돼도 TestSidebar가 오류 없이 저장된 실행 결과를 표시한다. | `apps/client/app/features/workflow/tests/test-sidebar-execution-comparison.test.tsx` |
| 1 | 실행 편의성 | 실행 기록 생성 지연·브라우저 히스토리 중 TestSidebar 복원 | 통과 | 초기 placeholder `default`에서는 복원 API를 호출하지 않고 테스트 실행 버튼을 비활성화한다. persisted workflow가 활성화된 뒤 초기 `404`와 `running` run은 점차 긴 제한된 재조회 뒤 terminal 결과로 복원하며, 재시도 한도 전에는 오류를 표시하지 않는다. `testRun` URL 복원은 기록이 지연돼도 패널을 열고, `testNode`가 없으면 실행 전체 결과를 위해 선택 노드를 비운다. 앞으로/뒤로가기로 새 `testRun`을 복원하고 `testRun`이 사라지면 이전 결과를 초기화한다. Gateway 권한 helper는 malformed workflow ID를 DB 조회 전 `404`로 닫는다. | `apps/client/app/features/workflow/tests/test-sidebar-run-restore.test.tsx`, `apps/gateway/tests/api/test_permission_helpers.py` |
| 1 | 실행 편의성 | stream 시작 시 큐 등록·복원용 run id·실제 SSE record delimiter 전달 | 통과 | Gateway는 Redis 구독 뒤 workflow task를 큐에 등록한 다음 `workflow_start` UUID를 보내며, 각 event를 실제 `\n\n` record delimiter로 끝내 다음 JSON event와 분리한다 | `apps/gateway/tests/api/test_workflow_stream_start_contract.py` |
| 1 | 실행 비교 | 기준 실행 목록 서버 필터 | 통과 | status와 trigger mode를 limit 전에 적용하고 다른 workflow run을 노출하지 않는다 | `apps/gateway/tests/api/test_workflow_run_comparison_api.py` |
| 1 | 실행 비교 | 기준 실행 목록 기본 필터 | 통과 | 비교 모드 진입 시 상태는 `전체 상태`, 실행 방식은 `전체 방식`으로 시작하고 제한 없는 목록 조회에는 status와 trigger mode를 전송하지 않는다 | `apps/client/app/features/workflow/tests/test-sidebar-execution-comparison.test.tsx` |
| 1 | 실행 비교 | 기준 실행 목록 자동 새로고침 | 통과 | `실행 비교` 탭을 누를 때마다 목록을 다시 조회하고, 열린 패널은 새 테스트가 완료되거나 실패하면 최신 실행 로그를 다시 조회한다 | `apps/client/app/features/workflow/tests/test-sidebar-execution-comparison.test.tsx`, `apps/client/app/features/workflow/components/editor/TestSidebar.tsx` |
| 1 | 실행 비교 | 기준 실행 식별 행과 요청 시 상세 조회 | 통과 | 접힌 행에는 실행 시각·방식·대표 입력을 표시하고, `상세`를 누르기 전에는 상세 API를 호출하지 않는다. 상세 조회와 `기준으로 고정` 후 비교 기준 조회의 일시적인 네트워크 오류·`404`·`408`·`429`·`5xx`는 제한적으로 재조회하고, `401`·`403`은 즉시 안내하며 최종 실패에는 `다시 불러오기`를 제공한다. 펼친 행에는 전체 입력·모델 라우팅·실행 지표를 표시한다 | `apps/client/app/features/workflow/tests/test-sidebar-execution-comparison.test.tsx` |
| 1 | 실행 비교 | 목록 갱신 실패 시 기존 기록 유지와 재시도 | 통과 | 기준 실행 목록 재조회가 실패해도 기존 행을 유지하고, 목록 또는 TestSidebar 상단 재시도 신호가 실제 목록 API를 다시 호출해 복구한다 | `apps/client/app/features/workflow/tests/test-sidebar-execution-comparison.test.tsx` |
| 1 | 실행 비교 | 최신 실행 자동 선택 금지와 명시적 기준 고정 | 통과 | 비교 모드 진입 시 선택이 비어 있고 사용자가 기준 고정 버튼을 눌러야 상세를 조회한다 | `apps/client/app/features/workflow/tests/test-sidebar-execution-comparison.test.tsx` |
| 1 | 실행 비교 | 재실행 중 기준 실행 유지와 상세 선택 초기화 | 통과 | 다시 테스트하기와 새 현재 실행은 baseline run id를 유지하지만 이전 비교 노드 선택과 URL 식별자는 지워 전체 노드 비교 목록부터 표시한다 | `apps/client/app/features/workflow/tests/test-sidebar-execution-comparison.test.tsx` |
| 1 | 실행 비교 | 단일 결과 전환 중 기존 비교 분석 유지 | 통과 | 같은 TestSidebar 세션에서 단일 결과를 거쳐 다시 실행 비교를 열어도 기준 실행, 선택 상세와 이미 불러온 분석을 유지한다 | `apps/client/app/features/workflow/tests/test-sidebar-execution-comparison.test.tsx` |
| 1 | 실행 비교 | 보고 화면 왕복 뒤 비교 상세 복원 | 통과 | URL에 보존한 현재 실행, 기준 실행, 비교 모드, 선택 노드를 읽어 보고 화면 복귀 뒤 동일한 노드 상세 비교를 다시 표시한다 | `apps/client/app/features/workflow/tests/test-sidebar-comparison-restore.test.tsx` |
| 1 | 실행 비교 | 실시간 노드 상태 갱신 중 비교 화면 유지 | 통과 | 같은 실행과 같은 노드 표시 정보를 유지한 상태 갱신은 비교 API 재조회와 로딩 화면 전환을 만들지 않는다 | `apps/client/app/features/workflow/tests/test-sidebar-execution-comparison.test.tsx` |
| 1 | 실행 비교 | 전체 실행 기준·현재 가로 막대와 상태 배지 | 통과 | 비용·실행 시간·전체 토큰을 세로로 쌓고, 각 항목에서 기준 실행·현재 실행의 가로 막대와 변화율을 표시한다. 상태는 별도 배지로 분리한다 | `apps/client/app/features/workflow/tests/test-sidebar-execution-comparison.test.tsx` |
| 1 | 실행 비교 | 현재 실행 중 단일 진행 패널과 완료 뒤 자동 동기화 | 통과 | 현재 실행이 `running`인 동안 노드 대기와 비교 준비 안내를 하나의 진행 패널에 표시하고, terminal 상태 전환 뒤 비교 결과를 자동으로 읽는다 | `apps/client/app/features/workflow/tests/test-sidebar-execution-comparison.test.tsx` |
| 1 | 실행 비교 | 노드 목록 지표와 상세 비교 | 통과 | 목록에는 노드 이름·사람용 유형명·상태·시간을 표시하고, `llmNode`에만 비용·토큰을 추가한다. 상세 진입 시 TestSidebar 본문을 맨 위로 이동하고 실행 상태 → 입력 → 모델 라우팅 → 출력 순서로 양쪽 값을 표시한다 | `apps/client/app/features/workflow/tests/test-sidebar-execution-comparison.test.tsx` |
| 1 | 실행 비교 | LLM trace 기록 없음과 조회 실패 구분 | 통과 | `404`/빈 trace는 `LLM trace 기록 없음`으로 표시하고, `403`/`5xx`/네트워크 실패는 비교를 유지하면서 근거 일부 누락 경고를 표시한다 | `apps/client/app/features/workflow/tests/test-sidebar-execution-comparison.test.tsx` |
| 1 | 노드 조작 편의성 | 3패널 기본 표시 | 통과 | 기본 3패널 폭 산출 unit test와 `NodeFullscreenEditor` grid 구현 완료 | `apps/client/app/features/workflow/tests/node-panel-resize.test.ts`, `apps/client/app/features/workflow/components/editor/NodeFullscreenEditor.tsx` |
| 1 | 노드 조작 편의성 | 3패널 resize 계산의 min/max clamp | 통과 | layout 계산 unit test 완료 | `apps/client/app/features/workflow/tests/node-panel-resize.test.ts` |
| 1 | 노드 조작 편의성 | viewport width 90% 안에서 편집 화면 표시 | 통과 | layout 계산 unit test 완료. 실제 DOM 폭은 수동 QA 필요 | `apps/client/app/features/workflow/tests/node-panel-resize.test.ts` |
| 1 | 노드 조작 편의성 | 기본 패널 폭을 부모 영역 기준 28/52/20 비율로 계산 | 통과 | layout 계산 unit test 완료 | `apps/client/app/features/workflow/tests/node-panel-resize.test.ts` |
| 1 | 노드 조작 편의성 | Knowledge 보조 패널을 열어도 중앙/우측 독립 스크롤과 캔버스 wheel 격리 유지 | 통과 | 전체화면 3패널 높이/overflow/nowheel component contract test 완료. 실제 wheel 스크롤은 수동 QA 병행 | `apps/client/app/features/workflow/tests/node-fullscreen-scroll.test.tsx`, `apps/client/app/features/workflow/components/editor/NodeFullscreenEditor.tsx` |
| 1 | 워크플로우 조작 편의성 | Delete/Backspace로 선택 노드 삭제 | 통과 | shortcut hook test 완료 | `apps/client/app/features/workflow/tests/workflow-delete-reconnect.test.tsx` |
| 1 | 워크플로우 조작 편의성 | A -> B -> C 구조에서 B 삭제 시 A -> C 자동 재연결 | 통과 | store unit test 완료 | `apps/client/app/features/workflow/tests/workflow-delete-reconnect.test.tsx` |
| 1 | 워크플로우 조작 편의성 | 입력 필드 focus 중 Backspace/Delete가 노드 삭제로 동작하지 않음 | 통과 | shortcut hook test 완료 | `apps/client/app/features/workflow/tests/workflow-delete-reconnect.test.tsx` |
| 1 | 워크플로우 조작 편의성 | 삭제 후 undo 복구 | 통과 | store unit test 완료 | `apps/client/app/features/workflow/tests/workflow-delete-reconnect.test.tsx` |
| 1 | 워크플로우 조작 편의성 | 왼쪽 노드 패널의 `뒤에 추가`가 선택 노드 오른쪽에 새 노드와 edge를 생성 | 통과 | 선택 노드 우선, 미선택 시 단일 terminal만 허용, note 제외, undo 단위, validation 실패 rollback unit test 완료 | `apps/client/app/features/workflow/tests/workflow-add-after-selected-node.test.ts` |
| 1 | 동시성 처리 | 자동 저장 응답이 늦게 도착해도 최신 화면 상태를 이전 상태로 되돌리지 않음 | 통과 | active workflow가 아닌 data 적용 시 현재 화면 nodes/edges를 덮지 않는 store test 완료 | `apps/client/app/features/workflow/store/useWorkflowStore.test.ts` |
| 1 | 동시성 처리 | 테스트 실행 중 graph를 수정해도 실행 결과가 현재 편집 중인 설정값을 덮어쓰지 않음 | 통과 | 실행 결과 observability merge가 기존 node 설정값을 유지하는 store test 완료 | `apps/client/app/features/workflow/store/useWorkflowStore.test.ts` |
| 2 | 실행 편의성 | 권한 없는 테스트 실행의 403 처리 | 미구현 테스트 | Gateway/API contract test infra가 필요하다 | `apps/client/app/features/workflow/tests/execution-convenience.test.ts` |
| 2 | 실행 편의성 | scope 밖 workflow 테스트 실행의 404 처리 | 미구현 테스트 | Gateway/API contract test infra가 필요하다 | `apps/client/app/features/workflow/tests/execution-convenience.test.ts` |
| 2 | 실행 편의성 | 캔버스에 별도 테스트 실행 요약 패널이 표시되지 않는지 UI test로 고정 | 미구현 테스트 | BottomPanel/Canvas render test가 아직 없다 | `apps/client/app/features/workflow/tests/execution-convenience.test.ts` |
| 2 | 노드 조작 편의성 | 실제 pointer drag interaction test | 미구현 테스트 | DOM pointer event test가 아직 없다 | `apps/client/app/features/workflow/tests/node-panel-resize.test.ts` |
| 2 | 노드 조작 편의성 | 닫았다가 다시 열었을 때 세션 내 패널 비율 유지 정책 고정 | 미구현 테스트 | 세션 유지 범위에 대한 UI test가 필요하다 | `apps/client/app/features/workflow/tests/node-panel-resize.test.ts` |
| 2 | 노드 조작 편의성 | read-only 사용자의 리사이즈 가능/수정 불가 구분 | 미구현 테스트 | read-only render fixture가 아직 없다 | `apps/client/app/features/workflow/tests/node-panel-resize.test.ts` |
| 2 | 워크플로우 조작 편의성 | incoming only/outgoing only 삭제 edge 정리 | 부분 구현 | 구현은 포함되어 있으나 별도 명시 테스트가 없다 | `apps/client/app/features/workflow/tests/workflow-delete-reconnect.test.tsx` |
| 2 | 워크플로우 조작 편의성 | 여러 incoming/outgoing 조합 재연결 중복 방지 | 통과 | store unit test 완료 | `apps/client/app/features/workflow/tests/workflow-delete-reconnect.test.tsx` |
| 2 | 워크플로우 조작 편의성 | 여러 노드 동시 삭제 재연결 후보 제외 | 부분 구현 | 구현은 남는 노드 기준으로 동작하나 다중 삭제 fixture test가 없다 | `apps/client/app/features/workflow/tests/workflow-delete-reconnect.test.tsx` |
| 2 | 노드 실행 기록 패널 추가 | 실행 기록 탭 기본 상태 | 미구현 | UI가 아직 없다 | `apps/client/app/features/workflow/tests/node-execution-log-panel.todo.test.ts` |
| 2 | 노드 실행 기록 패널 추가 | 빈 상태 안내 | 미구현 | UI가 아직 없다 | `apps/client/app/features/workflow/tests/node-execution-log-panel.todo.test.ts` |
| 2 | 노드 실행 기록 패널 추가 | 최신 기록 불러오기 | 미구현 | UI/API가 아직 없다 | `apps/client/app/features/workflow/tests/node-execution-log-panel.todo.test.ts` |
| 2 | 노드 실행 기록 패널 추가 | JSON/plain text input/output 읽기 표시 | 미구현 | detail renderer가 아직 없다 | `apps/client/app/features/workflow/tests/node-execution-log-panel.todo.test.ts` |
| 2 | 동시성 처리 | 같은 workflow를 두 브라우저 세션에서 열고 수정할 때 충돌 또는 최신 상태 갱신 안내 | 미구현 | 충돌 감지 정책/API가 없다 | TBD |
| 2 | 동시성 처리 | 권한 회수 후 열린 탭에서 저장/배포/실행 시 최신 권한 기준으로 거부 | 미구현 테스트 | Gateway 권한 재검증 test와 UI error handling test가 필요하다 | TBD |
| 3 | 노드 실행 기록 패널 추가 | node execution log 목록/상세 API 전체 | 미구현 | API가 아직 없다 | `apps/client/app/features/workflow/tests/node-execution-log-panel.todo.test.ts` |
| 3 | 노드 실행 기록 패널 추가 | `limit`, `cursor`, `status`, `q`, `from`, `to` query 처리 | 미구현 | API가 아직 없다 | `apps/client/app/features/workflow/tests/node-execution-log-panel.todo.test.ts` |
| 3 | 노드 실행 기록 패널 추가 | preview/full payload redaction 정책 | 미구현 | API response/redaction 정책 구현이 없다 | `apps/client/app/features/workflow/tests/node-execution-log-panel.todo.test.ts` |
| 3 | 노드 실행 기록 패널 추가 | 120개 이상 실행 로그 pagination | 미구현 | API/UI pagination 구현이 없다 | `apps/client/app/features/workflow/tests/node-execution-log-panel.todo.test.ts` |
| 3 | 노드 실행 기록 패널 추가 | 실행 로그 목록 실패 retry UI | 미구현 | error/retry UI가 아직 없다 | `apps/client/app/features/workflow/tests/node-execution-log-panel.todo.test.ts` |
| 3 | 동시성 처리 | draft revision/ETag 기반 충돌 API | 미구현 | draft revision contract가 없다 | TBD |
| 3 | 동시성 처리 | 배포 snapshot revision 고정 | 미구현 | 배포 API snapshot/revision contract가 없다 | TBD |
| 3 | 동시성 처리 | cursor pagination 중 동시 insert 중복/누락 방지 | 미구현 | cursor 정렬 기준과 API test가 없다 | TBD |
| 3 | 동시성 처리 | 오프라인 편집 후 온라인 복귀 충돌 확인 | 미구현 | offline edit flow가 없다 | TBD |
| 3 | 동시성 처리 | 실행 stream 재연결 시 이전 이벤트와 새 실행 결과 격리 | 미구현 | stream session id 또는 cleanup 정책이 없다 | TBD |
| 3 | 정책 확정 필요 | 시작 트리거/삭제 제한 노드 삭제 정책 | 정책 필요 | 현재 legacy `deletable:false` 제거 정책과 삭제 제한 요구가 충돌한다 | `apps/client/app/features/workflow/tests/workflow-delete-reconnect.test.tsx` |
| 3 | 정책 확정 필요 | run은 존재하지만 현재 node_id 기록이 없는 상세 조회 오류 형식 | 문서 보완 필요 | 404와 `node_execution_log.not_found` 중 하나로 확정해야 테스트를 고정할 수 있다 | `apps/client/app/features/workflow/tests/node-execution-log-panel.todo.test.ts` |

## Todo Test Coverage Register

다음 항목은 테스트 케이스에는 존재하지만 아직 실행 가능한 assertion 테스트로 전환되지 않았다. 각 항목은 미완료 단계가 해소되면 대응 테스트 파일에서 `it.todo`를 실제 `it(...)`로 바꾼다.

### 실행 편의성

| Todo | 미완료 단계 | 이유 | 대응 파일 |
| --- | --- | --- | --- |
| 테스트 실행 스트리밍 API가 `node_start`, `node_finish`, `workflow_finish` 이벤트를 반환한다 | API test infra | 현재 client Vitest에서 Gateway streaming contract를 직접 검증하지 않는다. API integration/e2e 테스트 경계가 필요하다. | `apps/client/app/features/workflow/tests/execution-convenience.test.ts` |
| `node_finish` 이벤트는 `latency_ms`, `total_tokens`, `total_cost`를 표준 필드로 반환한다 | API test infra | Gateway/engine streaming contract test가 필요하다. | `apps/client/app/features/workflow/tests/execution-convenience.test.ts` |
| 권한 없는 사용자의 테스트 실행 요청은 403으로 거부된다 | API test infra | Gateway 권한 응답 검증이 client unit test 범위 밖이다. API test 또는 MSW 기반 contract test가 필요하다. | `apps/client/app/features/workflow/tests/execution-convenience.test.ts` |
| scope 밖 workflow 테스트 실행 요청은 404로 처리된다 | API test infra | active organization scope 검증은 Gateway/API 통합 테스트가 필요하다. | `apps/client/app/features/workflow/tests/execution-convenience.test.ts` |
| 캔버스에는 별도 테스트 실행 요약 패널이 표시되지 않는다 | UI test | 캔버스/BottomPanel render test가 아직 없다. | `apps/client/app/features/workflow/tests/execution-convenience.test.ts` |
| 프론트 버튼 disabled여도 API 직접 호출 권한 검증은 Gateway에서 유지된다 | API test infra | Gateway authorization contract test가 필요하다. | `apps/client/app/features/workflow/tests/execution-convenience.test.ts` |

### 노드 조작 편의성

| Todo | 미완료 단계 | 이유 | 대응 파일 |
| --- | --- | --- | --- |
| 패널을 최대/최소 폭까지 드래그해도 UI가 겹치거나 화면 밖으로 밀려나지 않는다 | UI test | pointer drag 기반 DOM interaction test가 아직 없다. | `apps/client/app/features/workflow/tests/node-panel-resize.test.ts` |
| 노드 상세 편집 화면을 닫았다가 같은 세션에서 다시 열었을 때 세션 내 비율 유지 정책이 의도대로 동작한다 | UI behavior | 현재 구현은 컴포넌트 생명주기 안의 state 유지 기준이다. 닫기/재열기 정책을 UI test로 고정해야 한다. | `apps/client/app/features/workflow/tests/node-panel-resize.test.ts` |
| read-only 사용자는 패널 리사이즈는 할 수 있지만 node data 수정/저장은 할 수 없다 | UI/permission test | read-only 상태에서 리사이즈와 수정 차단을 함께 검증하는 render test가 없다. | `apps/client/app/features/workflow/tests/node-panel-resize.test.ts` |
| 드래그 중 마우스가 편집 영역 밖으로 나가도 pointer capture 또는 window event 처리로 리사이즈가 끊기지 않는다 | UI test | window pointer event 기반 interaction test가 필요하다. | `apps/client/app/features/workflow/tests/node-panel-resize.test.ts` |
| 드래그 종료 후 텍스트 선택 상태나 캔버스 pan 상태가 남지 않는다 | UI test | `document.body.style` cleanup과 canvas pan 상태를 검증하는 DOM test가 없다. | `apps/client/app/features/workflow/tests/node-panel-resize.test.ts` |

### 워크플로우 조작 편의성

| Todo | 미완료 단계 | 이유 | 대응 파일 |
| --- | --- | --- | --- |
| read-only 사용자는 Backspace/Delete로 노드 삭제 또는 자동 재연결을 수행할 수 없다 | UI permission test | `NodeCanvas`의 `isReadOnly` 조건과 shortcut hook 연결을 render test로 검증해야 한다. | `apps/client/app/features/workflow/tests/workflow-delete-reconnect.test.tsx` |
| 삭제 대상 노드에 incoming edge만 있거나 outgoing edge만 있으면 재연결 없이 해당 노드와 연결 edge만 제거한다 | Unit test | 구현은 이 동작을 포함하지만 별도 명시 테스트가 아직 없다. | `apps/client/app/features/workflow/tests/workflow-delete-reconnect.test.tsx` |
| 삭제 대상이 시작 트리거 노드 또는 삭제 제한 노드라면 기존 삭제 제한 정책을 따른다 | Policy/implementation | 현재 legacy `deletable:false` 제거 정책과 삭제 제한 정책이 충돌한다. 제품 정책 확정 후 테스트가 필요하다. | `apps/client/app/features/workflow/tests/workflow-delete-reconnect.test.tsx` |
| 여러 노드를 동시에 삭제할 때 삭제되는 노드끼리의 edge는 재연결 후보에서 제외한다 | Unit test | 구현은 삭제 후 남는 노드 기준으로 계산하지만 다중 삭제 fixture 테스트가 아직 없다. | `apps/client/app/features/workflow/tests/workflow-delete-reconnect.test.tsx` |

### 노드 실행 기록 패널 추가

| Todo | 미완료 단계 | 이유 | 대응 파일 |
| --- | --- | --- | --- |
| 실행 기록 탭 기본 상태는 실행 목록 검색과 가장 최신 로그 기록 불러오기 버튼을 표시한다 | UI implementation | 노드 실행 기록 패널 UI가 아직 구현되지 않았다. | `apps/client/app/features/workflow/tests/node-execution-log-panel.todo.test.ts` |
| 선택된 실행 기록이 없으면 빈 상태 안내를 표시한다 | UI implementation | 빈 상태 UI가 아직 구현되지 않았다. | `apps/client/app/features/workflow/tests/node-execution-log-panel.todo.test.ts` |
| 실행 목록 검색 버튼을 누르면 오른쪽 패널이 picker view로 전환된다 | UI implementation | picker view 전환 UI가 아직 구현되지 않았다. | `apps/client/app/features/workflow/tests/node-execution-log-panel.todo.test.ts` |
| picker view는 workflow run 목록을 최신순으로 표시한다 | UI/API implementation | 목록 API와 picker UI가 아직 구현되지 않았다. | `apps/client/app/features/workflow/tests/node-execution-log-panel.todo.test.ts` |
| picker row는 workflow run 요약과 현재 node_id의 node run/trace preview를 함께 표시한다 | UI/API implementation | summary response와 row UI가 아직 구현되지 않았다. | `apps/client/app/features/workflow/tests/node-execution-log-panel.todo.test.ts` |
| picker row를 선택하면 detail view로 전환되고 선택한 run 안의 현재 노드 input/output을 표시한다 | UI/API implementation | detail API와 detail view가 아직 구현되지 않았다. | `apps/client/app/features/workflow/tests/node-execution-log-panel.todo.test.ts` |
| 가장 최신 로그 기록 불러오기 버튼은 현재 node_id 기록이 포함된 가장 최신 run을 선택한다 | UI/API implementation | latest selection API/query 동작이 아직 구현되지 않았다. | `apps/client/app/features/workflow/tests/node-execution-log-panel.todo.test.ts` |
| 현재 node_id 기록이 없는 run은 row에서 제외되거나 현재 노드 기록 없음으로 표시된다 | UI/API implementation | node_id 기반 목록 필터링 정책은 문서화됐지만 구현이 없다. | `apps/client/app/features/workflow/tests/node-execution-log-panel.todo.test.ts` |
| input/output JSON 값은 읽기 가능한 형태로 렌더링된다 | UI implementation | detail renderer가 아직 없다. | `apps/client/app/features/workflow/tests/node-execution-log-panel.todo.test.ts` |
| plain text input/output 값은 줄바꿈이 보존되어 렌더링된다 | UI implementation | detail renderer가 아직 없다. | `apps/client/app/features/workflow/tests/node-execution-log-panel.todo.test.ts` |
| workflow read 권한 사용자는 node execution log 목록 API로 현재 node_id 기록 포함 run 목록을 조회할 수 있다 | API implementation | node execution log 목록 API가 아직 구현되지 않았다. | `apps/client/app/features/workflow/tests/node-execution-log-panel.todo.test.ts` |
| node execution log 목록 API는 `limit`, `cursor`, `status`, `q`, `from`, `to` query를 처리한다 | API implementation | query contract는 문서에 있으나 Gateway 구현이 없다. | `apps/client/app/features/workflow/tests/node-execution-log-panel.todo.test.ts` |
| node execution log 목록 API는 full input/output이 아니라 preview 문자열만 반환한다 | API implementation | response redaction/preview contract 구현이 없다. | `apps/client/app/features/workflow/tests/node-execution-log-panel.todo.test.ts` |
| node execution log 상세 API는 선택한 run 안의 현재 node_id input/output/trace/usage 상세를 반환한다 | API implementation | detail API가 아직 구현되지 않았다. | `apps/client/app/features/workflow/tests/node-execution-log-panel.todo.test.ts` |
| workflow read 권한이 없는 사용자의 실행 로그 조회 요청은 403으로 거부된다 | API implementation | node execution log API 권한 검증 구현이 없다. | `apps/client/app/features/workflow/tests/node-execution-log-panel.todo.test.ts` |
| active organization scope 밖 workflow의 실행 로그 조회 요청은 404로 처리된다 | API implementation | node execution log API scope masking 구현이 없다. | `apps/client/app/features/workflow/tests/node-execution-log-panel.todo.test.ts` |
| 현재 node_id 기록이 없는 workflow는 목록 API에서 빈 목록을 반환한다 | API implementation | node_id filter 구현이 없다. | `apps/client/app/features/workflow/tests/node-execution-log-panel.todo.test.ts` |
| run은 존재하지만 현재 node_id 기록이 없는 상세 조회는 404 또는 `node_execution_log.not_found`로 처리한다 | API/document gap | 문서가 404 또는 error code 둘 다 허용한다. 하나로 확정해야 테스트를 고정할 수 있다. | `apps/client/app/features/workflow/tests/node-execution-log-panel.todo.test.ts` |
| 상태 필터를 실패로 바꾸면 실패 run 또는 실패 node 기록만 목록에 남는다 | UI/API implementation | status filter API와 UI 연결이 없다. | `apps/client/app/features/workflow/tests/node-execution-log-panel.todo.test.ts` |
| 검색어를 입력하면 input/output/error preview에 해당 검색어가 포함된 실행 기록을 찾을 수 있다 | UI/API implementation | `q` 검색 API와 UI 연결이 없다. | `apps/client/app/features/workflow/tests/node-execution-log-panel.todo.test.ts` |
| workflow read 권한만 있는 사용자는 실행 기록을 조회할 수 있지만 노드 설정을 수정할 수 없다 | UI/permission implementation | read-only detail view와 edit control 차단 UI가 아직 없다. | `apps/client/app/features/workflow/tests/node-execution-log-panel.todo.test.ts` |
| 실행 기록 조회는 workflow write 권한을 요구하지 않는다 | API implementation | read-only 권한으로 조회 가능한 API contract test가 필요하다. | `apps/client/app/features/workflow/tests/node-execution-log-panel.todo.test.ts` |
| 현재 node_id 기록이 있는 실행 로그가 없으면 이 노드의 실행 기록이 없습니다 안내를 표시한다 | UI/API implementation | empty API response와 empty UI가 아직 구현되지 않았다. | `apps/client/app/features/workflow/tests/node-execution-log-panel.todo.test.ts` |
| run detail에는 있으나 input/output payload가 retention 또는 redaction으로 비어 있으면 표시 가능한 기록 없음으로 표시한다 | UI/API implementation | redaction-aware detail response와 UI가 아직 없다. | `apps/client/app/features/workflow/tests/node-execution-log-panel.todo.test.ts` |
| 실행 로그가 120개 이상 있어도 초기 목록은 제한된 개수만 렌더링하고 더 보기로 확장한다 | UI/API implementation | pagination API와 더 보기 UI가 아직 없다. | `apps/client/app/features/workflow/tests/node-execution-log-panel.todo.test.ts` |
| 실행 로그 목록 조회 실패 시 오류 안내와 다시 시도 액션을 표시한다 | UI implementation | 목록 error state와 retry UI가 아직 없다. | `apps/client/app/features/workflow/tests/node-execution-log-panel.todo.test.ts` |

### 동시성 처리

| Todo | 미완료 단계 | 이유 | 대응 파일 |
| --- | --- | --- | --- |
| 같은 workflow draft를 두 탭에서 동시에 편집하면 뒤늦은 저장이 최신 변경을 조용히 덮어쓰지 않는다 | PostgreSQL integration/E2E | 충돌 감지 기준은 `graph_hash + updated_at` CAS로 확정됐다. 남은 작업은 독립 PostgreSQL transaction과 브라우저 UX에서 `409 stale_graph` 안내·재조회 경로를 검증하는 것이다. | `apps/gateway/tests/integration/test_agent_builder_workflow_cas.py` |
| 자동 저장 요청이 연속 발생할 때 오래된 응답이 최신 로컬 상태를 되돌리지 않는다 | UI implementation/test | canonical metadata는 successful GET/POST 결과로만 갱신한다. 남은 작업은 빠른 연속 autosync와 response ordering이 dirty editor 상태를 지우지 않는지 component/E2E로 고정하는 것이다. | `apps/client/app/features/workflow/hooks/useAutoSync.test.ts` |
| 테스트 실행 중 사용자가 workflow graph를 수정해도 실행 요청은 시작 시점 draft snapshot 기준으로 처리된다 | UI/API contract | 실행 snapshot 생성 시점과 이후 편집 상태의 분리 기준을 API/UI 테스트로 고정해야 한다. | TBD |
| 동시에 두 번 테스트 실행을 눌러도 중복 stream이 열리거나 결과가 섞이지 않는다 | UI implementation/test | 실행 버튼 disable, in-flight guard, stream cleanup 테스트가 필요하다. | TBD |
| 배포 요청과 draft 저장 요청이 겹쳐도 배포 snapshot은 의도한 버전의 graph를 사용한다 | API/UX policy | 배포 시 draft revision 고정 정책이 문서/API에 확정되어 있지 않다. | TBD |
| workflow run 목록/노드 실행 기록 조회 중 새 run이 생성되어도 pagination cursor가 중복/누락 없이 동작한다 | API implementation | cursor 정렬 기준과 동시 insert 처리 정책이 필요하다. | TBD |
| 같은 노드를 여러 사용자가 동시에 수정하면 충돌 안내 또는 최신 상태 재조회 경로를 제공한다 | UX/API policy | 협업 편집을 허용할지, last-write-wins를 허용할지 정책 결정이 필요하다. | TBD |

## Unit Tests

### 1. 실행 편의성

- `node_finish.total_tokens`가 있으면 테스트 실행 사이드바에 해당 토큰 수를 표시한다.
- `node_finish.total_cost`가 있으면 테스트 실행 사이드바에 해당 비용을 표시한다.
- `node_finish.latency_ms`가 있으면 노드 소요 시간은 해당 값을 우선 표시한다.
- 토큰 정보가 없으면 `-`를 표시한다.
- 비용 정보가 없으면 `-`를 표시한다.
- `node_start` 후 `node_finish`를 받으면 소요 시간이 ms 단위로 표시 가능한 값으로 계산된다.
- 전체 테스트 실행 완료 시 화면 완료 시간, 전체 비용, 전체 토큰 사용량이 계산된다.
- `workflow_finish`가 서버 실행 시간 summary를 제공하면 최종 요약은 서버 실행 시간을 주 지표로 표시한다.
- `workflow_finish`가 서버 실행 시간 summary를 제공하지 않으면 최종 요약은 서버 실행 시간 fallback과 화면 완료 시간을 함께 표시한다.
- 최종 응답 helper는 사용자 응답 형태의 `workflow_finish.output`, answer/response node output, LLM node text 계열 output 순서로 preview 후보를 선택한다. `workflow_finish.output`이 graph node id만 key로 갖는 전체 실행 컨텍스트이면 Answer node output을 우선한다.
- JSON 최종 응답은 raw dump 대신 key/value preview로 요약한다.
- 권한/정책 차단성 최종 응답은 사용자 메시지를 표시하되 hidden KB id, source path/url/title, credential, raw trace payload를 preview에서 제외한다.
- 빈 최종 응답은 empty state로 표시할 수 있는 값을 반환한다.

### 2. 노드 조작 편의성

- 3패널 layout 계산에서 각 패널 폭은 최소/최대 폭 제약을 넘지 않는다.
- 사용자가 아직 직접 조정하지 않은 기본 패널 폭은 부모 영역 기준 왼쪽 28%, 가운데 52%, 오른쪽 20% 비율로 계산된다.
- 왼쪽 패널 폭을 늘리면 가운데와 오른쪽 패널 폭이 비슷한 비율로 줄어든다.
- 전체 편집 영역은 viewport width의 90%를 초과하지 않는다.
- 패널 폭 계산은 실제 렌더된 부모 영역 폭이 바뀌면 그 폭을 기준으로 다시 clamp된다.
- Knowledge 보조 패널이 열리면 3패널 본문은 NodeCanvas 가용 높이 안에 유지되고 가운데와 오른쪽 패널은 독립적인 scroll boundary를 가진다.
- 노드 전체화면 편집 영역은 `nowheel` 경계를 가져 wheel 입력이 캔버스 확대/축소 handler로 전달되지 않는다.
- reset 동작이 있다면 패널 폭이 기본 비율로 복구된다.

### 3. 워크플로우 조작 편의성

- 선택 노드가 있는 상태에서 왼쪽 노드 패널의 `뒤에 추가`를 실행하면 선택 노드 오른쪽에 새 노드가 생성되고 선택 노드에서 새 노드로 edge가 생성된다.
- 일반 노드 클릭/추가는 기존 자유 배치 흐름을 유지하고, `뒤에 추가` 액션과 동작이 섞이지 않는다.
- 선택 노드가 없고 terminal node가 하나뿐이면 `뒤에 추가`는 terminal node 뒤에 연결할 수 있다.
- 선택 노드가 없고 terminal node가 여러 개이면 `뒤에 추가`는 임의 연결하지 않고 실행하지 않는다.
- sticky note처럼 workflow 실행 graph에 포함되지 않는 보조 노드는 terminal node 계산에서 제외한다.
- 선택 노드가 여러 개이면 `뒤에 추가`는 임의 연결하지 않고 실행하지 않는다.
- condition/switch/loop처럼 handle이 모호한 노드는 연결 handle이 명확할 때만 자동 edge를 생성한다.
- `뒤에 추가`는 전체 graph 정렬을 실행하지 않고 새 노드 주변 local placement만 수행한다.
- `뒤에 추가`는 노드 생성과 edge 생성을 undo 한 번으로 되돌릴 수 있어야 한다.
- `뒤에 추가` edge 생성이 일반 연결 validation을 통과하지 못하면 새 노드도 남기지 않는다.
- 단일 중간 노드를 삭제하면 incoming source와 outgoing target 사이에 새 edge가 생성된다.
- 자동 재연결은 기존 연결 검증 규칙을 통과하는 경우에만 edge를 생성한다.
- 여러 incoming/outgoing edge가 있는 노드를 삭제하면 가능한 유효 조합만 생성하고 중복 edge는 만들지 않는다.
- 입력 필드에 focus가 있을 때 Backspace/Delete를 눌러도 노드 삭제 함수가 호출되지 않는다.

### 4. 노드 실행 기록 패널 추가

- 실행 기록 탭 기본 상태는 `실행 목록 검색`과 `가장 최신 로그 기록 불러오기` 버튼을 표시한다.
- 선택된 실행 기록이 없으면 빈 상태 안내를 표시한다.
- 실행 목록 검색 버튼을 누르면 오른쪽 패널이 picker view로 전환된다.
- picker view는 workflow run 목록을 최신순으로 표시한다.
- picker row는 workflow run 요약과 현재 node_id의 node run/trace preview를 함께 표시한다.
- picker row를 선택하면 detail view로 전환되고 선택한 run 안의 현재 노드 input/output을 표시한다.
- 가장 최신 로그 기록 불러오기 버튼은 현재 node_id 기록이 포함된 가장 최신 run을 선택한다.
- 현재 node_id 기록이 없는 run은 row에서 제외되거나 `현재 노드 기록 없음`으로 표시된다.
- input/output JSON 값은 읽기 가능한 형태로 렌더링된다.
- plain text input/output 값은 줄바꿈이 보존되어 렌더링된다.

### 5. 동시성 처리

- 자동 저장 요청이 빠르게 여러 번 발생하면 마지막 요청 결과만 현재 편집 상태에 반영된다.
- 테스트 실행 중 graph를 수정해도 실행 결과는 실행 시작 시점의 draft snapshot에 대응된다.
- 테스트 실행 stream이 이미 진행 중이면 중복 실행 요청을 막거나 이전 stream을 명확히 정리한다.
- undo/redo 중 자동 저장이 발생해도 로컬 graph와 저장된 draft가 서로 다른 revision으로 조용히 엇갈리지 않는다.

## API Tests

### 1. 실행 편의성

- 기존 테스트 실행 스트리밍 API가 `node_start`, `node_finish`, `workflow_finish` 이벤트를 반환한다.
- `node_finish` 이벤트는 node-level summary 표준 필드 `latency_ms`, `total_tokens`, `total_cost`를 포함한다.
- `workflow_finish` 이벤트가 workflow run summary를 제공하는 경우 `run_id`, `duration`, `total_tokens`, `total_cost`를 포함한다.
- `workflow_finish.duration`은 `workflow_runs.duration`과 같은 서버 실행 시간 기준이다.
- 권한 없는 사용자의 테스트 실행 요청은 403으로 거부된다.
- scope 밖 workflow 테스트 실행 요청은 404로 처리된다.

### 4. 노드 실행 기록 패널 추가

- workflow read 권한이 있는 사용자는 node execution log 목록 API로 현재 node_id 기록이 포함된 run 목록을 조회할 수 있다.
- node execution log 목록 API는 `limit`, `cursor`, `status`, `q`, `from`, `to` query를 처리한다.
- node execution log 목록 API는 full input/output이 아니라 preview 문자열만 반환한다.
- node execution log 상세 API는 선택한 run 안의 현재 node_id input/output/trace/usage 상세를 반환한다.
- workflow read 권한이 없는 사용자의 실행 로그 조회 요청은 403으로 거부된다.
- active organization scope 밖 workflow의 실행 로그 조회 요청은 404로 처리된다.
- 현재 node_id 기록이 없는 workflow는 목록 API에서 빈 목록을 반환한다.
- run은 존재하지만 현재 node_id 기록이 없는 상세 조회는 404 또는 `node_execution_log.not_found`로 처리한다.

### 5. 동시성 처리

- draft 저장 API는 클라이언트가 보낸 `expected_graph_hash` 또는 `expected_updated_at`이 서버 최신 canonical graph와 다르면 `409 stale_graph`를 반환한다.
- 동일 workflow에 대해 동시 저장 요청 2개가 같은 base `graph_hash + updated_at`으로 도착하면 서버는 row lock 뒤 하나만 성공시키고 다른 요청은 `409 stale_graph`로 닫는다.
- 최상위 request schema를 통과한 Loop `subGraph`에서 position/data가 누락된 node 또는 source/target이 누락된 edge가 있으면 일반 draft save와 metadata draft read는 raw validation detail 없이 `422 workflow.graph_invalid`를 반환하고 DB commit/audit을 수행하지 않는다.
- 배포 API는 요청 시점에 지정한 draft revision 또는 snapshot id를 기준으로 배포한다.
- 실행 로그 목록 API는 새 run이 조회 중 생성되어도 cursor pagination에서 중복 row를 반환하지 않는다.

## E2E Tests

### 1. 실행 편의성

- 빌더가 워크플로우 테스트를 실행하면 테스트 실행 사이드바에 노드별 상태가 표시된다.
- 실행 중인 노드는 `실행 중`, 완료된 노드는 `성공`, 실패한 노드는 `실패`로 표시된다.
- `llmNode`의 `node_finish` 이벤트에 토큰/비용 표준 필드가 있으면 테스트 실행 사이드바에 토큰 수와 비용이 표시된다. 다른 노드는 해당 값이 없어도 빈 비용·토큰 칸을 표시하지 않는다.
- 테스트 성공 후 테스트 실행 사이드바 상단에 최종 사용자가 받는 `최종 응답` 카드가 표시된다.
- JSON 최종 응답은 카드에서 읽기 쉬운 preview로 표시되고, 원본 JSON은 노드별 실행 결과 상세 영역에 유지된다.
- 테스트 완료 후 테스트 실행 사이드바 마지막 영역에 서버 실행 시간, 화면 완료 시간, 전체 비용, 전체 토큰 사용량이 표시된다.
- 서버 실행 시간과 화면 완료 시간은 `서버 실행`, `화면 완료`처럼 서로 다른 라벨로 구분된다.
- 캔버스에는 별도 테스트 실행 요약 패널이 표시되지 않는다.
- 다시 테스트하기를 누르면 이전 실행 요약이 초기화되고 새 실행 결과로 갱신된다.
- 테스트 실행 사이드바는 기본 `560px`로 열리고, 왼쪽 handle을 드래그해 `440px`~`720px` 범위에서 폭을 조정할 수 있다. 패널 안의 글자는 기존보다 한 단계 크게 표시된다.
- 테스트 실행 사이드바 폭은 keyboard `ArrowLeft`/`ArrowRight`와 `Home`/`End`로도 조정할 수 있으며, 패널을 닫고 다시 열어도 같은 편집 세션에서는 유지된다.

### 2. 노드 조작 편의성

- 노드 상세 편집 화면을 열면 3패널이 기본 비율로 표시된다.
- 노드 상세 편집 화면은 넓은 화면에서도 viewport width의 90% 안에서 표시된다.
- 노드 상세 편집 화면은 넓은 화면에서 고정 px 기본값에 갇히지 않고 부모 영역 기준 최대 90% 폭을 사용한다.
- 사용자가 왼쪽 패널 resizer를 드래그하면 왼쪽 패널은 넓어지고 가운데/오른쪽 패널은 같이 줄어든다.
- 패널을 최대/최소 폭까지 드래그해도 UI가 겹치거나 화면 밖으로 밀려나지 않는다.
- LLM Knowledge 보조 패널을 연 뒤 가운데 설정과 오른쪽 Knowledge 목록을 각각 끝까지 스크롤할 수 있고, 이 동작이 캔버스 zoom을 변경하지 않는다.
- 브라우저 높이와 패널 폭을 변경한 뒤에도 가운데/오른쪽의 독립 스크롤이 유지된다.
- 노드 상세 편집 화면을 닫았다가 같은 세션에서 다시 열었을 때 세션 내 비율 유지 정책이 의도대로 동작한다.

### 3. 워크플로우 조작 편의성

- 캔버스에서 노드를 클릭한 뒤 Backspace를 누르면 해당 노드가 삭제된다.
- 캔버스에서 노드를 클릭한 뒤 Delete를 누르면 해당 노드가 삭제된다.
- A → B → C 구조에서 B를 삭제하면 A → C edge가 생성된다.
- A → B → C 구조에서 B 삭제 후 undo를 실행하면 B와 기존 edge가 복구된다.
- LLM prompt textarea에 focus가 있는 상태에서 Backspace를 눌러도 선택 노드는 삭제되지 않는다.

### 4. 노드 실행 기록 패널 추가

- 노드 상세 패널에서 실행 기록 탭을 열면 기본 view가 표시된다.
- 실행 목록 검색을 클릭하면 오른쪽 패널 전체가 검색/필터/선택 화면으로 바뀐다.
- 상태 필터를 `실패`로 바꾸면 실패 run 또는 실패 node 기록만 목록에 남는다.
- 검색어를 입력하면 input/output/error preview에 해당 검색어가 포함된 실행 기록을 찾을 수 있다.
- 실행 로그 row를 선택하면 선택 화면이 닫히고 상세 view에 input/output/error/metadata가 표시된다.
- 가장 최신 로그 기록 불러오기를 클릭하면 목록 검색 없이 최신 노드 기록 상세가 표시된다.
- 실행 기록 상세를 보는 동안 현재 노드 설정값은 변경되지 않는다.

### 5. 동시성 처리

- 같은 workflow를 두 브라우저 세션에서 열고 각각 수정하면 충돌 또는 최신 상태 갱신 안내가 표시된다.
- 같은 base graph hash와 workflow `updated_at`으로 두 Agent Builder GraphMutation 저장을 순차 제출하면 첫 저장만 성공하고 두 번째는 `409 stale_graph`이며 첫 저장 graph를 덮어쓰지 않는다.
- Agent Builder mutation 저장은 workflow row lock 안에서 expected graph hash와 `updated_at`을 모두 비교한다.
- mutation request graph hash가 persisted safe operation envelope의 `expected_result_graph_hash`와 다르면 저장하지 않는다. Full typed operations를 DB에서 읽거나 재생하지 않는다.
- Agent Builder GraphMutation graph write와 같은 SQLAlchemy session에서 `add_action_audit` insert가 실패하면 graph, workflow `updated_at`과 operation 상태를 모두 rollback하고 성공을 반환하지 않는다. 일반 editor autosync는 신규 audit event 없이 공통 CAS와 canonical projection을 통과한다.
- Acknowledged operation의 `action=revert` 저장은 current graph가 원 result hash이고 candidate가 원 base hash일 때만 성공하며 graph와 audit를 원자적으로 저장하고 operation을 `reverted`로 전환한다.
- Revert 전 다른 editor 저장이 있으면 `409 stale_graph`로 닫고 해당 변경을 덮어쓰지 않는다.
- Persisted Undo는 최초 `initial_graph`/`graph_edit`/`replace_workflow`의 단일 Agent Builder history boundary로 동작한다. 후속 `parameter_update`/`knowledge_binding`은 새 Workflow history entry를 만들지 않고 final graph metadata만 전진한다. 완료 상태 첫 Undo는 graph/value와 persisted task 상태를 유지한 채 `completed|skipped|deferred` 중 최대 stable order ParameterTask UI를 다시 열고, 그 상태의 다음 Undo 또는 task가 없는 경우 첫 Undo는 `action=revert` CAS로 실행 전 graph 전체를 복구하며 모든 ParameterTask/Knowledge 흐름을 `canceled`로 닫는다. `parameter_update`/`knowledge_binding` 개별 revert는 거부한다. Reload 전 `action=redo` CAS는 final graph만 복구하고 canceled 흐름은 재실행하지 않으며, 그 뒤 Undo는 parameter 재진입 없이 즉시 boundary revert한다. 동일 revert/redo response-loss 재시도는 canonical 결과와 audit를 중복 생성하지 않고, reload 뒤에는 Redo stack과 parameter 재진입 상태가 복구되지 않는다.
- 성공 응답은 canonical graph hash, persisted `updated_at`, workflow id와 operation id를 반환하고 workflow version/revision을 합성하지 않는다.
- mutation context 없는 일반 editor save는 유지되지만 Agent Builder acknowledgement 근거로 거부된다.
- 테스트 실행 버튼을 연속 클릭해도 실행 사이드바에는 하나의 실행 흐름만 표시된다.
- `/execute`와 `/stream`은 unresolved/invalid Mail 또는 Slack, unavailable Mail credential을 workflow task publish 전에 `409 workflow.configuration_preflight.blocked`로 차단한다. Stream은 Redis subscribe와 SSE response 시작 전 같은 JSON error를 반환한다.
- 한 Slack node의 unresolved 설정과 다른 node를 가리키는 legacy `data`/`headers` 또는 Webhook `message_ref` selector가 함께 있으면 unresolved와 invalid를 모두 보존하고 inactive에서도 invalid로 차단한다.
- Mail `title`, folder, `max_results`, boolean, filter/date/reference와 processing mode의 잘못된 타입·범위는 Worker Pydantic 실패까지 전달되지 않고 같은 preflight 409로 차단한다.
- Node `position` 누락·비유한/비숫자 좌표, edge `id` 누락·빈 값, dangling edge, cycle, duplicate node ID, 진입점 오류와 고립 node는 최상위와 Loop subgraph에서 `workflow_graph_invalid`로 차단한다. 최상위 graph의 명시적 trigger/start 하나와 Loop body의 incoming executable edge가 없는 실행 진입점 하나는 통과하고, Loop body에 별도 trigger가 없어도 실행 진입점이 유일하면 허용한다. Loop의 implicit entry 일반 노드는 매 iteration의 `loop.item`, `loop.index`와 외부 입력을 첫 실행 context로 받으며, 후속 노드는 완료된 선행 결과를 받는다. 합산 node 1,000개, edge 5,000개와 Loop subgraph depth 16은 통과하고 각각 1개 초과하면 차단하며 task publish는 0회다.
- Compare와 Cost Optimizer compare/recommendation verification은 base graph configuration preflight가 blocked이면 variant/candidate Celery task를 하나도 발행하지 않고 request-level safe 409를 반환한다. 성공 경로는 preflight가 검사한 server-bound graph를 재사용하고 task 직전에 WorkflowNode target을 다시 binding하지 않는다. 완료된 recommendation verification idempotent replay는 workflow 권한과 active organization scope를 확인하되 현재 node/graph preflight와 task를 다시 시작하지 않는다.
- Configuration preflight는 workflow execute 권한과 active organization 검증 이후에 실행되며 권한 없는 요청의 hidden resource를 조회하거나 노출하지 않는다.
- Preview permission denial은 audit 0건이고 execute/stream/Compare/Cost Optimizer enforcement는 same-organization denial을 resource별 정확히 한 번 감사한다. 감사 metadata는 검증된 organization과 middleware request ID를 포함하되 raw path/header는 포함하지 않는다.
- 여러 Mail credential을 포함한 graph도 scalar permission과 같은 allow/deny 결과를 내며 organization/user/membership query가 credential마다 증가하지 않는다. Revoked credential의 잔존 grant는 scalar/bulk 모두 `none`이다.
- Test Sidebar는 safe required action label을 표시하고 credential ID/name/email, Slack token/Webhook URL/channel/payload와 raw response object를 표시하지 않는다.
- 테스트 실행 중 노드 설정을 수정한 뒤 결과가 도착해도 현재 편집 중인 설정값이 실행 결과 payload로 덮어써지지 않는다.
- 저장 중 네트워크 지연이 발생한 뒤 이전 저장 응답이 늦게 도착해도 최신 화면 상태가 이전 상태로 되돌아가지 않는다.

## Permission Tests

### 1. 실행 편의성

- `can_execute=false`인 사용자는 테스트 버튼을 실행할 수 없다.
- 프론트에서 테스트 버튼이 disabled여도 API 직접 호출 권한 검증은 Gateway에서 유지된다.

### 2. 노드 조작 편의성

- read-only 사용자는 패널 리사이즈는 할 수 있지만 node data 수정/저장은 할 수 없다.

### 3. 워크플로우 조작 편의성

- read-only 사용자는 Backspace/Delete로 노드 삭제 또는 자동 재연결을 수행할 수 없다.

### 4. 노드 실행 기록 패널 추가

- workflow read 권한만 있는 사용자는 실행 기록을 조회할 수 있지만 노드 설정을 수정할 수 없다.
- workflow read 권한이 없는 사용자는 실행 기록 탭에서 목록/상세를 조회할 수 없다.
- 실행 기록 조회는 workflow write 권한을 요구하지 않는다.

### 5. 동시성 처리

- write 권한이 없는 사용자의 stale draft 저장 재시도는 충돌 처리 이전에 권한 오류로 거부된다.
- manager가 권한을 회수한 뒤 열린 탭에서 저장/배포/실행을 시도하면 최신 권한 기준으로 거부된다.

## Edge Cases

- LLM provider와 model discovery는 HTTP/non-443, current Provider origin 밖 redirect, private·metadata DNS result, peer mismatch와 ambient proxy에서 provider request를 보내지 않는다. Stored credential의 stale `baseUrl`은 current Provider endpoint를 덮어쓰지 못하고 transport profile revision 변경은 stale capability를 provider call 전에 거부한다.
- FileExtraction remote URL은 injected fake port로 node behavior를 단위 검증하고 guarded adapter에서 public HTTPS success, HTTP/private/metadata/redirect denial, 2xx 외 status의 body 저장 전 거부, response/content type cap, chunk-to-temp streaming, timeout, partial temp cleanup과 raw URL/path/exception redaction을 검증한다. Async guarded transport의 DNS 검증은 event loop 밖에서 connect timeout을 적용한다. Remote response 전체를 buffered helper에 적재하거나 Node와 factory가 Requests/HTTPX concrete client를 직접 생성하면 architecture test가 실패한다.

### 1. 실행 편의성

- 토큰 사용량이 없는 non-LLM 노드는 토큰 칸에 `-`를 표시한다.
- 서버 실행 시간 summary가 없는 테스트 실행 결과는 서버 실행 시간 칸에 `-` 또는 `기록 없음`을 표시하고 화면 완료 시간은 유지한다.
- 화면 완료 시간이 서버 실행 시간보다 길어도 그 차이를 순수 UI 처리 시간으로 표시하지 않는다.
- 노드 실행 중 스트리밍이 실패하면 전체 실패 상태와 식별 가능한 노드 실패 상태를 함께 표시한다.
- 실행 결과가 매우 많은 경우 테스트 실행 사이드바 내부에서 스크롤되며 캔버스에는 별도 요약 패널을 만들지 않는다.

### 2. 노드 조작 편의성

- 화면 폭이 3패널 최소 폭 합보다 좁으면 resizer를 비활성화하거나 responsive fallback을 사용한다.
- 드래그 중 마우스가 편집 영역 밖으로 나가도 pointer capture 또는 window event 처리로 리사이즈가 끊기지 않는다.
- 드래그 종료 후 텍스트 선택 상태나 캔버스 pan 상태가 남지 않는다.

### 3. 워크플로우 조작 편의성

- 삭제 대상 노드에 incoming edge만 있거나 outgoing edge만 있으면 재연결 없이 해당 노드와 연결 edge만 제거한다.
- 삭제 대상이 시작 트리거 노드 또는 삭제 제한 노드라면 기존 삭제 제한 정책을 따른다.
- 여러 노드를 동시에 삭제할 때 삭제되는 노드끼리의 edge는 재연결 후보에서 제외한다.

### 4. 노드 실행 기록 패널 추가

- 현재 node_id 기록이 있는 실행 로그가 없으면 `이 노드의 실행 기록이 없습니다` 안내를 표시한다.
- run detail에는 있으나 input/output payload가 retention 또는 redaction으로 비어 있으면 `표시 가능한 기록 없음`으로 표시한다.
- 실행 로그가 120개 이상 있어도 초기 목록은 제한된 개수만 렌더링하고 `더 보기`로 확장한다.
- 긴 input/output은 패널을 깨지 않고 스크롤 또는 줄바꿈으로 표시된다.
- 실행 로그 목록 조회 실패 시 오류 안내와 다시 시도 액션을 표시한다.

### 5. 동시성 처리

- 저장 요청이 취소되거나 timeout된 뒤 재시도할 때 같은 draft revision을 중복 생성하지 않는다.
- 사용자가 오프라인 상태에서 편집한 뒤 온라인으로 돌아오면 서버 최신 revision과 충돌 여부를 확인한다.
- 실행 stream 연결이 끊긴 뒤 재연결하거나 재실행할 때 이전 stream 이벤트가 새 실행 결과에 섞이지 않는다.
- 배포 중 draft가 추가로 수정되면 배포 완료 알림은 배포된 snapshot과 현재 draft가 다를 수 있음을 구분한다.


## Runtime RAG Boundary Tests

- Workflow run context resolver는 interactive user를 `execution_subject`로 만들고, approved service account 또는 assigned operator는 후속 private RAG 기능으로 구분한다.
- Missing execution subject는 anonymous public-only 결과를 반환하고 workflow owner fallback을 만들지 않는다. Ambiguous execution subject는 private retrieval fail-closed로 처리한다.
- LLM node의 RAG 옵션은 Builder-time skill selection과 runtime data access 권한을 분리한다.

## MBA-233 Workflow Knowledge Collection Tests

- Builder는 direct KB와 Collection을 별도 group으로 선택하고 각각 20개 cap을 적용한다.
  Collection-only/mixed graph가 save/reload 뒤 ID, configured order와 bounded display
  snapshot을 보존한다.
- Current picker에서 사라진 saved reference는 stale label 대신 generic unavailable로
  남고 picker failure/empty response가 selection을 자동 삭제하지 않는다. 새 save가
  server authorization에 실패하면 hidden identity 없이 제거/권한 복구 action을 표시한다.
- Client validation을 우회한 malformed/over-limit graph는 draft save, direct stream,
  deployment와 Worker NodeFactory에서 provider 실행 전 거부된다.
- Agent Builder apply, cost optimizer candidate/apply/compare, model routing refresh와 graph
  copy path는 기존 `knowledgeCollections`를 보존하고 Collection을 자동 추천하지 않는다.
- `knowledgeBases` 또는 `knowledgeCollections` 중 하나라도 있으면 model router와 LLM
  runtime은 Knowledge-enabled로 판정한다. Collection-only node는 MBA-232 resolver 결과를
  기존 bounded retrieval/evidence path에 전달한다.
- Authenticated execution은 explicit user subject, subject 부재는 anonymous public
  audience를 사용한다. Credential principal/owner/builder가 Collection route를 가져도
  execution subject가 denied이면 candidate를 얻지 못한다.
- Resolver zero-result는 LLM provider를 호출하지 않고 infrastructure failure는 retryable
  task failure다. Authorized resolution 뒤 일부 retrieval timeout만 partial-result policy를
  사용할 수 있다.
- Public graph, node option display, trace/log/audit/error/SSE는 Collection identity와 child
  structure를 노출하지 않는다. Runtime trace는 routing mode와 count/limit/failure safe
  summary만 허용한다.

## MBA-238 Authenticated Subject To RAG Integration Tests

- 인증 내부 챗봇의 serialized `execution_context.execution_subject`는 LLM node에서
  `AuthenticatedAudience`로 해석되고, organization과 user ID가 production candidate
  resolver 요청까지 그대로 유지되어야 한다.
- 같은 graph를 서로 다른 로그인 사용자가 실행하면 resolver가 각 사용자의 PostgreSQL
  team/user permission으로 후보를 다시 계산해야 한다. workflow owner, deployment creator,
  credential principal 또는 직전 실행 사용자의 후보를 fallback/cache하면 테스트 실패다.
- LLM node의 retrieval fan-out은 resolver가 확정한 canonical 후보만 받아야 한다. 권한 없는
  direct KB와 Collection child는 vector search 전에 제외되고 provider prompt, citation,
  trace와 audit에 나타나지 않아야 한다.
- candidate 0 경로의 provider 미호출, resolver 1회 호출, Collection child identity redaction은
  기존 LLM node regression을 재사용하고, MBA-238에서는 production PostgreSQL adapter와
  연결된 사용자별 통합 경로만 추가한다.

## API Tests

- 로그인 LLM node의 RAG 옵션 실행 요청은 Knowledge service에 `execution_subject=current_user`를 전달한다.
- Catalog required configuration 전체를 server가 다시 계산했을 때 `configuration_state=unresolved`인 Slack/GitHub/HTTP/Mail 외부 action node가 있으면 test/run과 deployment create/activate가 차단된다. Client가 `resolved`로 변조해도 통과하지 않는다.
- unresolved graph의 draft 편집·저장은 허용하며 preflight 중 credential provider와 외부 API를 호출하지 않는다.
- 모든 required configuration을 catalog 계약대로 채운 graph는 기존 권한과 validation을 통과한 뒤 실행·배포할 수 있다.
- LLM node RAG 선택 UI는 legacy owner-filtered `/knowledge` 목록이 아니라 active organization, KB `use` 권한, completed retrieval-visible document chunk 기준을 통과한 LLM-selectable 후보만 표시한다. 빈 KB 또는 `pending`/`failed` 문서만 있는 KB는 경고 없이 선택 가능한 후보로 노출하지 않는다. 후보가 없으면 "완료된 문서가 있는 지식 베이스가 없습니다." 같은 safe 안내를 표시한다.
- 인증 배포 실행 화면은 `GET /deployments/{deployment_id}/run-info` safe metadata만 사용하고, `auth_secret` 또는 `graph_snapshot`을 받지 않는다.
- 로그인 사용자의 배포 실행 요청(`/deployments/{deployment_id}/run`)은 workflow `execute` 권한을 재검증하고, active deployment snapshot을 `execution_subject=current_user`로 실행한다.
- Legacy 인증 배포 실행의 `conversation_id`는 서버에서 deployment와 execution subject 기준으로 namespace 처리되어 다른 사용자 execution-log memory context와 섞이지 않는다. Target Conversation Session은 별도 server-issued session과 immutable deployment binding을 사용한다.
- `/run-public/{url_slug}` 공개 실행은 `execution_subject`를 주입하지 않으며 workflow owner 권한으로 private RAG를 fallback하지 않는다.
- Execution subject가 없으면 public collection 소속 active KB는 검색 가능하고 private collection 소속 KB는 검색되지 않는다. Source-managed KB는 valid source/connector public exposure approval이 없으면 public collection에 연결되어도 검색되지 않는다.
- Execution context에 `user_id`만 있고 `execution_subject`가 없으면 `user_id` 권한으로 private KB access를 fallback하지 않는다.
- Schedule/webhook/API trigger 실행은 배포 시 승인된 service account 또는 정책상 지정된 execution subject가 없으면 anonymous public-only로 Knowledge retrieval을 실행한다.
- System schedule run은 `WorkflowRun.user_id=null`, execution audit system actor이며 App/deployment creator나 workflow owner가 executor/RAG subject로 전파되지 않는다. 기존 manual/API/webhook user-attributed run은 non-null actor 계약을 유지한다.
- System schedule의 legacy LLM credential principal은 locked canonical deployment creator에서만 구성되고 queue가 덮어쓸 수 없다. Capability target의 main/query credential principal은 manager가 exact deployment policy에 고정한 사용자이며 schedule execution subject나 creator fallback에서 다시 만들지 않는다. 두 경로 모두 private Knowledge retrieval은 execution subject 부재에 따른 anonymous public-only 경계를 유지하고 malformed/unknown principal type은 fail-closed한다.
- System schedule의 `rag.retrieve`와 RAG evidence `policy.block` audit은 credential principal이 있어도 system actor를 사용한다. Interactive 실행은 credential principal과 execution subject가 달라도 실제 execution subject만 user actor로 기록한다.
- Schedule claim 실행 전 Knowledge sync가 connector/DB 예외로 실패해도 engine은 기존 index로 실행되고 claim은 engine 결과에 따라 finalize된다. Sync 결과는 `sync_failed` 같은 safe reason만 포함하고 connector 예외 원문을 노출하지 않는다.
- Workflow Worker의 `gevent` monkey patch 이후에도 blocking PostgreSQL client와 OS-native Connector relay가 함께 진행되어야 한다. Deployment allowlist에 포함된 custom DB port의 정상 sync를 relay 교착으로 `sync_failed` 처리하거나 기존 index로 조용히 fallback하면 테스트 실패다.
- Code node는 interactive/system schedule 여부와 무관하게 canonical `execution_context.organization_id`를 sandbox tenant로 전달한다. `user_id`가 null인 schedule도 organization tenant를 유지하고, organization이 없을 때 user를 tenant로 승격하지 않는다.
- Duplicate schedule task delivery는 stable workflow run id 하나를 재사용하고 engine admission을 한 번만 허용한다. Admission 이후 outcome unknown은 자동 replay하지 않는다.
- Schedule occurrence idempotency key는 MBA-187 dispatch/admission context 밖의 `ExternalEffectContext`, node/provider adapter와 provider key builder에 전달되지 않는다. Canonical claim UUID에서 계산한 별도 `execution_id`만 external-effect identity에 사용되고 schedule key와 새 identity는 prompt/output/raw trace/metric label에 없다.
- 배포 preflight는 LLM node RAG 옵션의 KB/collection 후보가 deployment type에서 파생한 runtime audience에게 사용 가능한지 검증하고, unavailable/unknown/private 후보가 있으면 hidden id/count 없이 safe reason과 required action만 반환한다.
- Public/API/webhook/schedule/공개 `chatbot`/MCP surface는 subject가 없으므로 private KB 후보가 있으면 활성 배포 create/toggle에서 `409 deployment.preflight.blocked`를 반환한다.
- `internal_chatbot` preflight는 `authenticated_user` audience이므로 private KB 참조만으로 차단하지 않지만, Gateway는 workflow organization active membership과 workflow `execute`를 dispatch 전에 검사하고 `X-Organization-Id`가 있으면 app organization 일치도 확인한다. Runtime은 current user 기준 KB permission/source ACL을 다시 검사한다.
- 공개/내부 챗봇 배포는 타입과 결과 링크를 분리한다. 내부 실행은 memory mode와 non-empty conversation id를 전송하고, `401` 인증 복귀는 path/query/hash를 보존하되 open redirect 입력은 `/dashboard`로 닫는다.
- MBA-219 managed preflight는 Mail/Gmail Draft/Mail Acknowledge/Slack을 최상위 graph와 다단계 Loop `subGraph`, WorkflowNode target에서 같은 audience/principal로 검사한다. Missing/revoked/cross-organization/permission-denied Mail credential은 모두 `mail_credential_unavailable`로 보인다.
- Catalog external node가 registry에 없거나 `implemented=false`이면 test/active/schedule 전에 `node_configuration_validator_unavailable`로 차단한다. LLM/HTTP/GitHub는 runtime-authoritative로 명시돼 unclassified fallback을 사용하지 않는다.
- Preflight가 provider network adapter나 secret decrypt helper를 호출하지 않으며, 통과 뒤 runtime에서 credential revoke/permission 회수를 다시 차단한다.
- Workflow-node runtime은 parent execution context를 상속한다. Parent subject가 있으면 해당 subject 기준 KB permission/source ACL을 사용하고, subject가 없으면 anonymous public-only로 낮춘다.
- Workflow-node preflight는 `workflowNode.data.appId`로 target app active deployment를 찾는다. `workflowId`로 target을 잘못 해석하면 테스트 실패다.

## MBA-285 Child Execution Lifecycle Tests

- 두 개 이상 Loop iteration이 성공해도 child는 부모 `WorkflowRun` create/finish/error log와 최상위 Redis workflow/node event를 제출하지 않는다. Root finish와 `workflow_finish`만 전체 graph 종료 뒤 정확히 한 번 발생한다.
- Loop child의 일반 실패와 timeout은 child run error/event를 만들지 않고 부모에 전달된다. `error_strategy=end`에서는 root error lifecycle이 한 번 발생하고 `continue`에서는 기존 iteration 오류 수집 뒤 root가 전체 결과를 한 번만 완료 처리한다.
- `ExternalEffectRetrySignal`과 terminal `ExternalEffectError`는 `continue`에서도 문자열 iteration 결과로 축소되지 않으며 child와 root가 동일 오류 event를 중복 발행하지 않는다.
- Loop와 WorkflowNode는 공통 child factory를 사용하고 호출자가 child lifecycle mode를 해제할 수 없다. Factory는 caller의 execution context를 변경하지 않는다.
- Loop child는 부모 `execution_id`, task deadline, WorkflowNode binding과 Loop container path를 유지하고 iteration별 invocation path를 추가한다. WorkflowNode child는 target organization/app/workflow/deployment provenance와 subworkflow path를 유지한다.
- Loop 안 WorkflowNode와 WorkflowNode 안 Loop에서도 root run terminal update와 최상위 event는 outcome당 한 번뿐이다. Child cleanup 실패는 원래 성공·실패·timeout 또는 external-effect control signal을 덮어쓰지 않는다.
- 현재 child node별 durable log는 만들지 않고 부모 Loop/WorkflowNode container log와 external-effect attempt의 `node_invocation_id`로 correlation한다.

## MBA-190 External Effect Idempotency Tests

### 실행 식별자와 병렬 실행

- 같은 logical workflow task가 Celery retry 또는 duplicate delivery되면 `execution_id`가 유지되고 새 `workflow_run_id`가 생겨도 external effect identity는 바뀌지 않는다.
- Draft test, legacy deployed, authenticated deployment, URL slug API/public, webhook, schedule과 stream 실행은 같은 생성·검증 helper로 logical execution마다 `execution_id`를 한 번만 발급한다. Authenticated/API/public은 `workflow.execute`, webhook은 `workflow.execute_by_deployment` task 경로를 사용한다.
- Authenticated deployment와 URL slug API/public의 `workflow.execute`는 command의 deployment ID/version/graph를 DB의 canonical deployment snapshot과 비교하고 불일치하면 provider 호출 전에 실패한다. Webhook Worker는 deployment ID로 canonical snapshot을 직접 읽으며 queue provenance나 graph를 신뢰하지 않는다.
- Legacy `workflow.execute_deployed`는 기존 positional task 인자를 유지하고 최초 broker message의 additive optional envelope로 publisher-issued `execution_id`, exact `deployment_id`, `deployment_version`, canonical `snapshot_sha256`를 모두 받는다. Worker는 canonical deployment/App/Workflow와 네 값을 다시 확인하고 Celery retry에 같은 envelope을 유지한다. Bound row가 없거나 version/hash가 다르거나 같은 hash의 다른 deployment만 존재하면 active row로 대체하지 않고 provider 전에 실패한다.
- Legacy original broker message를 동일 task ID로 다시 전달하는 사이 active deployment가 바뀌어도 envelope이 가리키는 snapshot만 실행한다. Worker가 최초 resolve 값을 `self.retry`에만 추가하는 구현은 테스트에서 거부한다.
- 각 실행 진입점은 queue/client의 organization/app/workflow 값을 source of truth로 사용하지 않고 canonical DB resource에서 provenance를 다시 확인한다. 값 누락, tenant 불일치와 owner fallback은 provider 호출 전에 실패한다.
- Compare A와 B는 서로 다른 `execution_id`를 사용하고 각 variant의 Celery retry는 최초 variant ID를 유지한다. 기존 request/correlation context는 두 variant를 같은 compare 요청으로 묶되 external effect identity에는 포함되지 않는다.
- Retry 또는 duplicate delivery payload에 `execution_id`가 없으면 새 값을 생성하지 않고 provider 호출 전에 fail-closed한다.
- External effect node가 없는 legacy task는 envelope 누락만으로 조기 실패하지 않지만, effect boundary에 도달한 task는 누락 identity/deployment binding/provenance를 새로 만들지 않고 provider 호출 전에 fail-closed한다. 현재 저장소 안에 legacy publisher가 없다는 정적 호출부 검사도 유지한다.
- 같은 schedule claim UUID는 `uuid5(uuid.NAMESPACE_URL, "nodease:schedule-execution:v1:<lowercase-claim-uuid>")`로 process restart 뒤에도 같은 `execution_id`를 만들고 다른 occurrence claim은 다른 값을 만든다. Schedule idempotency key, Celery task id와 `workflow_run_id`를 provider key로 직접 재사용하지 않는다.
- Stream의 Pub/Sub `workflow_run_id`와 external-effect `execution_id`는 서로 다른 값이며 각각 correlation과 logical effect identity 역할만 수행한다.
- Subworkflow는 부모 `execution_id`를 유지하고 호출 위치를 나타내는 별도 `node_invocation_id`를 사용한다.
- Subworkflow child context와 attempt는 target deployment의 organization/app/workflow를 사용하고 root app/workflow provenance를 재사용하지 않는다. Loop subgraph는 같은 workflow provenance와 iteration별 invocation path를 사용한다.
- 배포 생성 시 root graph와 Loop subgraph 안의 각 WorkflowNode target deployment ID/version/snapshot hash가 server-owned internal binding으로 고정된다. Client 입력뿐 아니라 deployment clone, template/import와 다른 graph 복사 경로에 같은 이름 metadata가 있어도 제거 후 현재 canonical target으로 재계산된다. Draft/deployment/copy/export를 포함한 모든 client-facing graph 응답, node output, trace와 log에는 binding이 없다.
- Stream에 유효한 아직 저장되지 않은 request `graph_snapshot`을 주면 publisher는 그 graph의 server-owned 복사본에 binding만 계산해 실행하고 DB draft로 대체하거나 저장하지 않는다. MBA-190 적용 전과 같은 unsaved node/data/edge가 실행되며 client가 넣은 internal binding metadata만 제거·재계산된다. DB draft를 사용하던 Draft/Compare surface는 기존 source를 그대로 유지한다.
- Binding V1 entry는 Loop-only `container_path`, WorkflowNode/target app/deployment ID, deployment version과 lowercase 64자 snapshot SHA-256만 가지며 canonical path/node 순으로 정렬된다. Duplicate entry, unknown version, extra/malformed field, NaN과 canonicalization 불가 graph는 queue 발행 또는 provider 전에 실패한다.
- Shared node-location walker는 root, 단일·중첩 Loop에서 ordered `container_path + node_id`를 계산하고 Gateway preflight와 Workflow Runtime이 같은 fixture에서 같은 location을 사용해야 한다. 다른 Loop의 동일 `node_id`는 서로 다른 digest/policy로 분리하고 wrong path capability는 config decrypt, provider client와 SDK 호출 전에 거부한다.
- Snapshot hash는 target graph의 `_nodease_runtime.workflow_node_bindings`까지 포함한 sorted-key/compact/UTF-8/non-ASCII 유지 canonical JSON bytes로 independent reference implementation과 같은 값을 만든다. Target metadata나 graph 한 field를 바꾸면 hash가 달라진다.
- Binding resolver는 same-organization, allowed WorkflowNode deployment type, cycle와 maximum nesting depth를 기존 runtime 정책과 같은 값으로 검사한다. `apps/gateway/application/deployment/preflight.py`와 `workflow_node_binding.py`가 동일한 `DeploymentPreflightRepository` port, shared Loop-aware walker와 depth constant를 호출하고 `apps/gateway/composition/deployment.py`가 기존 SQLAlchemy adapter를 조립한다. 새 resolver나 target DB query 정책을 `apps/gateway/services/`에 만들면 import/architecture test가 실패한다. Cycle/depth 초과나 다른 organization target은 두 경로에서 같은 safe preflight error로 닫힌다.
- `apps/shared/domain/workflow_node_binding.py`는 `apps/shared/services.workflow_node_catalog` 또는 catalog file/path를 import하지 않는다. Gateway/Workflow Engine composition이 기존 catalog loader 결과에서 immutable side-effect mapping을 만들고 두 경로에 주입하며, 같은 catalog fixture에서 classifier 결과가 같아야 한다.
- 새 parent deployment가 effect-capable node를 가진 과거 child snapshot을 참조하지만 child에 V1 binding이 없으면 resolver는 child snapshot을 수정하거나 active grandchild를 대신 고르지 않고 safe preflight error로 거부한다. Child를 V1 metadata가 포함되도록 재배포한 뒤에만 parent binding이 생성된다. Canonical node catalog의 `side_effect=external_write`를 기본으로 하고 `httpRequestNode GET`, `githubNode get_pr`만 명시적으로 read-only로 낮춘다. `gmailDraftNode`, `mailAcknowledgeNode`, malformed/unknown action과 미래 `external_write` node도 effect-capable이므로 legacy fallback을 허용하지 않는다. Nested closure에 effect-capable node가 없는 legacy child만 기존 결과를 유지한다.
- Parent task의 retry와 original broker redelivery 사이 target app의 active deployment를 바꿔도 WorkflowNode는 최초 command/snapshot binding의 child graph만 실행한다. Runtime은 deployment/App/Workflow와 snapshot hash를 DB에서 검증하고 불일치하면 child node나 provider를 호출하지 않는다.
- Binding 생성은 당시 current active target만 허용한다. 이후 child D2를 활성화해 bound D1이 `is_active=false`이고 app pointer도 D2를 가리켜도 기존 parent snapshot/command의 retry와 새 실행은 D1의 exact graph를 사용한다. Runtime verifier가 pointer 일치를 요구하거나 D2로 대체하면 테스트가 실패한다. App active pointer가 null이거나 없는 row/다른 app/inactive row를 가리키는 전체 비활성·불일치 상태, D1 row 삭제, target app/workflow/organization/type/version/hash 불일치에서는 provider 호출 0회로 실패한다.
- MBA-190 이전 deployment snapshot 또는 legacy command에 child binding이 없으면 transitive child closure에 effect-capable node가 없는 경우만 기존 subworkflow 결과를 반환한다. Generic HTTP mutation, Slack request, GitHub comment, Gmail Draft/Acknowledge, catalog의 새 `external_write` 또는 분류 불가 node가 하나라도 있으면 첫 child provider 호출 전에 fail-closed하며 common attempt row와 downstream 호출은 0이다. Gmail node를 차단 기준에 넣어도 `workflow_node_effect_attempts`에 이중 기록하지 않고 ADR-0032 ledger만 유지한다.
- 같은 logical node invocation retry는 같은 `node_invocation_id`를 사용한다.
- Node invocation V1 고정 test vector는 outer `domain`, `execution_id`, `segment_count`, 반복되는 `segment_kind`, `segment_node_id`, `segment_scope` 순서와 unsigned 4-byte big-endian length framing을 사용한다. `root/빈 node ID/root workflow UUID`로 시작하고 `node/current graph node ID/submit ordinal`로 끝나며, 중간 `loop/loop node ID/iteration index`와 `subworkflow/caller node ID/frozen target deployment UUID`를 실제 nesting 순서로 넣는다. Padding 없는 Base64url UUIDv5 name은 process restart 뒤에도 같은 UUID를 만들고 Python hash seed, dict insertion order, DAG predecessor/완료 순서에 영향을 받지 않는다. Field 순서, segment kind/value, UUID/정수 표기 규칙이 다른 vector는 거부된다.
- 병렬 node 완료 순서가 바뀌어도 deterministic submit ordinal과 `node_invocation_id`는 바뀌지 않는다.
- 관련 없는 graph node를 추가하거나 logging용 `_node_sequence`가 달라져도 기존 node의 `(parent path, node id)` submit ordinal은 밀리지 않는다. 현재 정적 DAG의 첫 제출 ordinal은 0이다.
- Loop가 같은 subgraph engine을 여러 iteration에서 재사용해도 iteration별 immutable parent path와 submit count가 섞이지 않는다.
- 다음 Loop iteration과 별도 subworkflow 호출은 같은 graph `node_id`를 사용하더라도 서로 다른 `node_invocation_id`를 사용한다.
- 같은 organization/execution/node invocation의 `effect_sequence=0`에서 operation이 바뀌어도 새 attempt row가 생기지 않는다. DB stable slot unique 충돌 뒤 winner row를 다시 읽고 `external_effect.identity_conflict`로 닫으며 provider와 downstream 호출 횟수는 0이다.
- `effect_sequence`는 operation별 counter가 아니라 invocation 전체의 provider effect submit 순번이다. 현재 한 번만 호출하는 Generic HTTP mutation, Slack, GitHub comment와 fake adapter는 `0`을 사용한다.
- 병렬 node는 각자 immutable `ExternalEffectContext`를 받아 다른 node의 invocation identity를 관찰하지 않는다.
- `ExternalEffectContext`, `execution_id`, `node_invocation_id`와 `effect_input_digest`는 node의 일반 `inputs`, template/Jinja context, LLM prompt/tool input과 node output에 key/value 또는 문자열 형태로 나타나지 않는다. 외부 효과 application use case의 별도 typed parameter로만 전달되며 기존 template/LLM 입력 snapshot은 MBA-190 적용 전과 같다.
- Immutable context에 SQLAlchemy session이 포함되지 않고 병렬 node는 서로 다른 session을 사용한다.

### PostgreSQL migration과 저장 계약

- 실제 PostgreSQL에서 migration upgrade 후 `workflow_node_effect_attempts` table, NOT NULL `organization_id`/`app_id`/`workflow_id`/`effect_input_digest`, organization-scoped identity unique constraint, `replay_deadline_at`, 구조 CHECK, correlation index, 모든 row의 contract readiness 일반 index와 재호출 가능한 supported row만 포함하는 HMAC readiness partial index가 생성된다. 별도 recovery scan index는 생성하지 않는다.
- Migration head/revision 점검과 SQLAlchemy model parity test가 column type, nullability, default, constraint와 index 불일치를 탐지한다.
- 격리된 downgrade test는 MBA-190 revision의 table과 부속 객체만 제거하고 이전 revision의 객체를 보존한다.
- DB CHECK에는 현재 시각 비교가 없고 status별 nullability, 허용된 enum 조합과 generation 범위만 포함된다.
- 같은 organization과 `execution_id + node_invocation_id + effect_sequence` stable slot로 두 Worker가 경쟁하면 operation이 같거나 달라도 실제 PostgreSQL unique constraint를 통과한 하나의 attempt만 생성된다. Operation이 다르면 loser는 winner row를 읽고 identity conflict로 닫는다.
- 서로 다른 organization은 나머지 logical identity 값이 같아도 별도 row로 분리되고 provider key도 달라진다. 같은 organization에서 current app/workflow가 frozen row와 다르면 row 내용을 노출하지 않고 safe identity conflict로 닫는다.
- 같은 identity로 경쟁한 두 요청의 provider, contract, 지원 수준 또는 `effect_input_digest`가 다르면 최초 row만 유지되고 다른 요청은 provider와 downstream을 호출하지 않은 채 `external_effect.identity_conflict`로 종료된다.
- 두 Worker가 같은 prepared attempt를 동시에 claim해도 한 owner/generation만 provider call 권한을 얻는다.
- 같은 Celery task ID로 전달된 retry/redelivery 두 건도 서로 다른 CSPRNG `claim_owner` token을 사용한다. Task ID, worker hostname, execution ID와 retry count를 token으로 사용하지 않으며 token은 attempt column 밖의 log/trace/metric/output mock에 없다.
- 이전 delivery가 가진 owner token과 generation으로 새 generation의 `in_flight` 또는 terminal mutation을 시도하면 같은 task ID여도 compare-and-set이 0 rows를 갱신하고 output/downstream을 사용하지 않는다.
- 유효한 claim을 얻지 못한 중복 Worker는 provider를 호출하거나 즉시 성공/실패하지 않는다. Session/transaction/row lock을 유지하지 않는 짧은 조회로 terminal, claim 만료 또는 기존 task deadline 중 먼저 오는 경계까지 기다린다.
- Claim loser가 terminal `retry_before_effect|replay_same_key`를 보면 같은 application 진입에서 row를 reopen하거나 provider를 호출하지 않고 internal retry-permitted 결과만 task에 반환한다. 기존 Celery retry budget이 있으면 다음 delivery가 reopen한다. Budget이 소진되면 같은 terminal outcome, timestamp와 기존 allowlisted `error_code`를 유지한 채 decision만 `stop`으로 CAS되고 provider 호출은 0회다. 이후 original broker redelivery도 row를 reopen하지 않는다. Outcome unknown은 `external_effect.outcome_unknown`, failed-before-effect는 기존 safe provider failure로 반환된다.
- Winner가 terminal이면 loser는 frozen provider/contract/digest 일치 확인 뒤 `reuse_result`, `result_unavailable` 또는 `stop`을 그대로 따른다. `in_flight` claim이 먼저 만료되면 loser는 outcome-unknown 상태 정리만 수행하고, `prepared` claim이 먼저 만료되면 row 변경·재claim 없이 대기를 끝낸다. 어느 경우에도 같은 진입에서 provider를 호출하지 않는다.
- 유효 claim 대기 중 기존 task deadline이 먼저 오면 loser는 attempt를 변경하거나 provider를 호출하지 않고 기존 timeout/retry 경계로 종료한다. 이 경로가 새 retry 횟수나 backoff를 만들지 않는다.
- `in_flight` transaction commit이 실패하면 provider 호출 횟수는 0이다.
- `prepared` 생성·claim, `in_flight` 전이와 `terminal` 확정은 각각 새 session에서 commit하며 provider adapter 호출 중에는 DB session, transaction과 row lock이 열려 있지 않다.
- `in_flight` commit 직후 provider 호출 전 Worker가 종료돼도 row는 rollback되지 않고 recovery에서 outcome unknown으로 수렴한다.
- Provider 성공 뒤 terminal commit이 실패하면 durable `in_flight`가 남고 recovery에서 outcome unknown으로 수렴한다.
- Provider 성공 뒤 terminal commit 실패 또는 stale claim generation으로 terminal compare-and-set이 거부되면 현재 Worker는 node output을 반환하지 않고 downstream node를 실행하지 않는다. Terminal commit 성공 뒤에만 최초 output 또는 `replay_result`가 Workflow Engine으로 전달된다.
- Provider 호출 전 `in_flight` commit/claim 검증이 실패하면 provider 호출 없이 `external_effect.claim_wait` 재시도 신호로 처리한다. 유효한 다른 claim을 기다리는 마지막 delivery는 새 retry를 요청하지 않고 같은 코드를 `retryable=false`로 모든 실행 표면에서 닫는다. Provider 호출 후 terminal commit이 실패하면 output/downstream을 사용하지 않고 최종 안전 코드를 `external_effect.outcome_unknown`으로 둔다. 재시도 예산이 남은 stream delivery는 terminal event를 발행하지 않고, 예산이 없으면 같은 안전 코드의 `retryable=false` 오류로 닫는다.
- Claim이 만료된 뒤 이전 generation Worker의 terminal update는 compare-and-set에서 거부된다.
- Provider 호출이 claim 만료 뒤에도 살아 있는 시나리오에서 `supported` replay는 최초와 같은 key만 사용한다. 두 network 요청이 잠시 겹칠 수 있지만 fake provider effect counter는 1이고, `unsupported|unknown`은 두 번째 provider 요청을 만들지 않는다.
- `prepared` 상태에서 Worker가 종료되면 새 generation이 이어서 provider를 호출할 수 있다.
- Provider 실행 Worker의 prepared/in-flight mutation은 status, owner, generation과 `claim_expires_at > DB clock`을 사용한다. 만료 복구는 status/generation과 `claim_expires_at <= DB clock`, terminal reopen·budget 소진은 terminal status/generation/decision과 `claim_owner|claim_expires_at IS NULL`을 사용한다. 모든 DB 시각 판정은 Worker local clock 변화에 영향을 받지 않는다.
- Terminal `retry_before_effect|replay_same_key` row는 이전 owner/expiry가 null인 상태에서만 새 CSPRNG owner, 새 expiry와 `generation + 1`을 원자적으로 얻는다. 두 delivery의 동시 reopen 경쟁에서는 하나만 `prepared`가 되고 loser는 provider를 호출하지 않는다.
- Claim TTL은 주입한 active Celery hard time limit에 code-owned 30초를 더해 계산된다. 현재 600초 설정에서는 630초이고 새 환경변수를 읽지 않는다. Hard limit이 없거나 0/음수면 Worker readiness가 실패한다. DB claim 판정은 DB clock을 사용하고 loser 조회는 session을 닫은 채 100ms에서 최대 1초까지 간격을 늘리며 task 시작 때 만든 monotonic deadline을 넘지 않는다.
- Gevent에서 soft time limit이 전달되지 않거나 blocking task가 configured hard limit 뒤에도 살아 있는 시나리오를 process 종료로 가정하지 않는다. Claim 만료 뒤 이전 요청과 supported replay가 겹쳐도 fake effect는 하나이고, unsupported/unknown은 새 provider 요청을 만들지 않는다.
- `workflow_run_id`와 `node_run_id` Log System row가 아직 없어도 FK 오류 없이 attempt를 만들 수 있다.
- `prepared`와 `in_flight`에서는 outcome/replay decision이 null이고 `terminal`에서만 allowlisted 값이 non-null이다.
- `provider_started_at`은 `prepared`와 network-free prepare/finalize 실패에서 null이고, 최종 call 검증 뒤 `in_flight` commit 시 non-null이다. 이 commit 직후 Python `invoke_effect` 진입 전에 Worker를 종료해도 marker는 non-null이고 다음 재진입은 outcome unknown으로 수렴한다. `terminal + succeeded|effect_outcome_unknown`, 호출 허용 경계 뒤 byte 미전송이 증명된 transport 실패와 provider rejection에서도 non-null이다. `failed_before_effect`는 두 nullability를 모두 허용한다. 잘못된 status/outcome/marker 조합은 PostgreSQL CHECK와 domain validation 모두에서 거부된다.
- `provider_status_code`와 allowlisted `error_code`는 terminal에서만 허용되며 reopen 뒤에는 이전 값이 남지 않는다.

### 복구와 재호출 판단

- 별도 주기적 recovery task, Celery Beat 또는 다른 scheduler를 만들지 않는다.
- 같은 logical execution task가 재진입하면 identity unique key로 자기 attempt를 찾고, 만료된 `in_flight`만 같은 status/generation/expiry 조건으로 `effect_outcome_unknown` terminal 상태로 바꾼다. Frozen provider replay가 `supported`이고 DB clock이 저장된 `replay_deadline_at`보다 이르면 `replay_same_key`, 그 외에는 `stop`을 같은 update에 기록한다.
- 재진입 상태 정리 단계는 provider adapter, workflow 신규 실행 함수와 node input/request 재구성 함수를 호출하지 않는다. 이후 같은 task가 유효한 `replay_same_key`를 다시 열었을 때만 최초 key로 provider 호출을 계속할 수 있다.
- 만료된 `prepared`는 다음 동일 execution task가 새 generation으로 reclaim한다.
- 재진입 복구가 실행돼도 Celery retry 횟수, backoff, delivery/ack 설정은 바뀌지 않는다.
- 재진입하지 않은 만료 row가 남아 있어도 전역 scanner를 요구하지 않는다. 이 row를 정리하는 운영 기능은 MBA-190 완료 조건이 아니다.
- Adapter가 요청 전송 전의 일시 실패임을 증명한 경우만 `failed_before_effect + retry_before_effect`, validation/configuration과 완전한 영구 rejection은 `failed_before_effect + stop`으로 분류한다. 공통 application service가 raw HTTP status/body/exception을 다시 해석하지 않는지 검증한다.
- 현재 claim owner의 invoke가 `retry_before_effect` 또는 유효한 `replay_same_key`로 terminal commit되면 application은 node output/downstream을 반환하지 않고 internal retry permit을 task에 전달한다. 기존 retry budget이 있으면 task가 한 번 `self.retry`하고 다음 delivery만 row를 reopen한다. Schedule처럼 budget이 0이거나 소진된 surface는 decision을 `stop`으로 CAS하고 provider를 다시 호출하지 않는다.
- `retry_before_effect`와 `replay_same_key` terminal row만 이전 status/decision/generation compare-and-set을 거쳐 새 generation의 `prepared`로 다시 열린다.
- Reopen은 identity, 두 지원 수준, provider contract, `replay_deadline_at`과 key version/fingerprint를 보존하고 이전 outcome/decision/provider timestamp/result/error summary를 비운다.
- `reuse_result`, `result_unavailable`와 `stop` terminal row의 reopen은 거부된다.
- `effect_outcome_unknown + unsupported`와 `effect_outcome_unknown + unknown`은 non-retryable이며 Generic Celery retry가 provider를 다시 호출하지 않는다.
- `effect_outcome_unknown + stop`과 replay deadline이 지난 `replay_same_key`는 safe `external_effect.outcome_unknown`, `retryable=false` payload로 종료된다.
- `effect_outcome_unknown + supported`는 최초 key version, key format version과 provider contract version을 사용하는 replay만 허용한다.
- DB clock이 저장된 `replay_deadline_at`보다 이른 `replay_same_key`만 reopen되고, 경계 시각부터는 같은 transaction에서 `stop`으로 바뀌며 provider 호출 횟수는 증가하지 않는다.
- `replay_deadline_at`은 attempt 생성 DB 시각과 당시 contract retention으로 한 번 계산된다. 배포 뒤 현재 contract retention이나 Worker local clock 변화로 값이 바뀌지 않는다.

### 결과 재사용과 provider 계약

- 외부 서비스 중복 방지 지원 여부와 안전한 결과 재사용 가능 여부는 독립된 column과 domain value로 저장되고 한 값이 다른 값을 암묵적으로 결정하지 않는다.
- Versioned provider contract는 provider/operation/version, 두 지원 수준, key 전달 위치와 field 이름, format/alphabet/최대 길이, retention, provider key를 제외한 effect input canonicalization, effect success/rejection과 duplicate 응답 의미, 공식 근거를 빠짐없이 정의한다. `unknown` profile은 확인되지 않은 값을 임의 default로 채우지 않는다. 이미 사용된 version을 수정하거나 삭제하는 변경은 immutable profile test가 실패한다.
- Provider replay `supported` profile은 key transport `header|body`와 모든 key 계약값이 있어야 하며 `none|unknown` transport를 supported로 등록하면 profile validation이 실패한다.
- Production profile ID는 `generic_http.request.v1`, 역사적 `slack.http.request.v1`, 전용 `slack.chat.post_message.v1`, `slack.incoming_webhook.post.v1`, `github.issue_comment.create.v1`으로 고정되고 provider/operation/version 조합이 다르면 registry lookup이 실패한다. Test-only profile은 `fake.create_effect.v1`이다. 역사적 Slack profile은 기존 attempt 해석에만 사용하고 새 `slackPostNode`가 선택하지 않는다.
- Registry는 operation마다 active version을 정확히 하나 요구하고, production adapter는 해당 operation의 active version과 모든 과거 version을 함께 받는다. Active V2 전환 뒤 저장된 V1 retry는 V1의 요청 정규화·응답 해석·결과 projection handler와 최종 call profile을 유지한다. 등록되지 않았거나 adapter가 지원하지 않는 handler 식별자는 현재 handler로 대체하지 않고 adapter 구성 단계에서 실패한다.
- Generic HTTP와 역사적 Slack V1은 key 위치 `unknown`, 시스템 field `null`, 형식·최대 길이·retention·duplicate/conflict 의미 `unknown`, result reuse `unavailable`이어야 한다. 전용 Slack API/Webhook V1도 provider replay와 key 위치는 `unknown`이고 시스템 key를 주입하지 않지만, 검증된 safe output의 result reuse는 `supported`여야 한다. GitHub V1은 key 위치 `none`, 시스템 field `null`, key 제약 적용 안 함, duplicate-success 계약 없음, result reuse `unavailable`이어야 한다. 이 null/unknown 값을 code default나 사용자 header로 채우면 profile snapshot test가 실패한다.
- V1 attempt가 생성된 뒤 같은 provider/operation의 active profile을 V2로 바꿔도 retry와 terminal duplicate delivery는 stable slot을 먼저 읽고 frozen V1 canonicalizer/capability를 사용한다. 같은 semantic request는 현재 V2로 잘못 비교해 identity conflict가 되지 않고 저장된 V1 decision을 따르며, 새 execution만 V2를 사용한다.
- 최초 attempt insert를 서로 다른 active profile version의 Worker가 동시에 경쟁하면 unique winner 하나만 남고, 다른 version을 선택한 loser는 winner를 현재 version으로 바꾸거나 provider를 호출하지 않은 채 identity conflict로 종료된다.
- 같은 profile/digest지만 서로 다른 active HMAC key version을 선택한 Worker가 최초 insert를 경쟁하면 unique winner의 key version/format/fingerprint만 고정된다. Loser candidate key는 provider/finalize mock에 전달되지 않고 identity conflict도 만들지 않는다. 이후 claim owner와 replay는 winner key를 재생성해 같은 fingerprint를 확인하며 provider가 관찰한 key는 하나뿐이다.
- Provider 호출 코드는 raw response/exception 대신 outcome, 현재 실행용 검증된 output, 선택적 safe replay projection, status와 safe error code만 담은 typed result를 공통 application service에 전달한다. 공통 service가 provider body를 직접 파싱하지 않는다.
- `prepare_effect`는 network/client method를 호출하지 않고 시스템 key가 없는 `PreparedEffectRequest`와 deterministic validation/serialization failure 모두에 `effect_input_digest`를 제공한다. 준비 실패는 `prepared -> terminal(failed_before_effect + stop)`으로 닫히고 `provider_started_at`과 provider 호출 횟수는 비어 있거나 0이다. Claim owner의 `finalize_provider_call`만 winner row의 frozen key를 재생성해 fingerprint/길이를 확인하고 예약 field에 한 번 넣은 `PreparedProviderCall`을 만든다. Fingerprint, key 길이 또는 예약 위치 검증 실패는 `in_flight` 전 `failed_before_effect + stop`이고 provider 호출은 0회다. `invoke_effect`는 최종 call을 그대로 사용하며 graph/input/template renderer나 header/body/key builder를 다시 호출하지 않는다.
- Prepare 중 adapter bug, UTF-8 encode 실패와 canonical input 한도 초과처럼 deterministic digest 자체를 안전하게 만들 수 없는 경우에는 attempt row와 provider/result/downstream 호출이 0이고 `external_effect.prepare_failed`, `retryable=false`로 종료된다. 기존 stable slot winner도 변경하지 않고 generic Celery retry를 호출하지 않으며 raw 입력과 exception message는 log/trace/task result에 없다.
- Generic HTTP와 역사적 Slack V1 digest는 template 적용 후 method, rendered target, `httpx.Headers(...).multi_items()`로 정규화한 시스템 key 주입 전 effective application header, 인증 값과 effect-semantic body를 포함한다. 렌더링된 body가 `None` 또는 빈 문자열이면 no-body mode로 user `Content-Type`을 보존하고 `content=None`을 보낸다. 빈 문자열이 아닌 JSON `null`은 HTTPX 0.28.1 `json=None`과 같은 JSON-null-no-body mode로 user `Content-Type`을 제거하되 자동 `application/json` 없이 zero-byte body를 보낸다. `{}`, `[]`, `false`, `0`, JSON string처럼 null이 아닌 JSON 값은 user `Content-Type`을 제거하고 고정 `content-type: application/json`을 정확히 한 번 사용한다. Digest용 JSON object key는 정렬하므로 key 순서만 바꾼 요청은 같은 digest지만, `PreparedEffectRequest`, 최종 `PreparedProviderCall`과 invoke mock은 원래 parsed value의 insertion order와 HTTPX 0.28.1 compact/non-ASCII/NaN 금지 UTF-8 wire bytes를 그대로 유지한다. Digest를 같게 만들거나 key를 넣기 위해 실제 전송 JSON key 순서를 정렬하면 테스트가 실패한다. 공백만 있는 body, `NaN`, `Infinity`, `-Infinity`와 JSON parse 실패는 rendered bytes의 deterministic `invalid_json`, `failed_before_effect + stop`이고 provider 호출은 0회다. 각 mode에서 두 prepared value의 body mode/parsed JSON은 같고 finalization은 system key field만 바꾼다. Invoke mock argument는 최종 call과 같으며 invoke가 다시 분기하거나 header/body/key를 만들지 않는다. Header 이름은 소문자 이름순으로 정렬하지만 같은 이름의 여러 값 순서는 보존한다. 서로 다른 header 이름의 입력 순서만 바꾼 요청은 같은 digest이고, 같은 이름의 실제 전송 header 값 순서나 method/target/query/body/인증 값이 바뀐 요청은 다른 digest다. 나머지 HTTPX 기본 transport header 변화는 digest를 바꾸지 않는다. HTTPX dependency를 바꿔 위 wire semantics가 달라지는데 contract version을 유지하면 고정 integration test가 실패한다. Canonical bytes, Authorization과 credential 원문은 repository/log/trace mock에 전달되지 않는다. 전용 Slack V1 digest는 mode, strict canonical payload와 credential 원문을 framing한 뒤 SHA-256으로만 고정하고 원문을 attempt, log 또는 trace에 저장하지 않는다.
- Provider replay `supported` profile에서 사용자 요청이 예약 key field를 이미 포함하면 system key로 덮어쓰거나 중복 전송하지 않고 `provider_key_field_conflict`, `failed_before_effect + stop`, provider 호출 0회로 닫힌다. Header는 `Idempotency-Key`, `idempotency-key` 같은 case 변형을 모두 충돌로 보고 body는 profile의 canonical JSON Pointer에 값이 있을 때 충돌로 본다. `unsupported|unknown`에서는 같은 사용자 field가 보존되고 generated key가 추가되지 않는다.
- GitHub V1 digest는 canonical repository owner/name, PR number, rendered comment body와 인증 값을 포함한다. Requests 자동 transport header 변경은 같은 contract version 안에서 허용하지 않고 version 변경 없이 digest 의미를 바꾸는 registry 수정은 실패한다.
- `invoke_effect`는 유효한 owner/generation에서 frozen key로 완성한 `PreparedProviderCall`의 fingerprint 검증과 `in_flight` commit이 모두 성공한 뒤에만 호출된다. `PreparedEffectRequest`, 최종 call의 URL/header/body/credential과 raw key 원문은 repository, trace와 log mock에 전달되지 않는다.
- Graph 설정, runtime variable, upstream output 또는 provider 선택 변화로 같은 identity의 canonical effect input이 달라지면 SHA-256 digest 비교가 provider 호출과 저장 결과 재사용 전에 실패한다. Raw canonical input과 digest는 attempt column 밖의 응답/log/trace/metric에 나타나지 않는다.
- `succeeded + result reuse supported`의 duplicate delivery는 provider를 다시 호출하지 않고 allowlisted `replay_result`를 반환한다.
- `succeeded + reuse_result` terminal row는 `replay_result`가 없으면 DB constraint와 domain validation에서 거부된다.
- 최초 성공과 duplicate success node output은 같은 schema를 사용하고 그 output을 소비하는 다음 node도 정상 실행된다.
- `replay_result`는 adapter allowlist 뒤 `sort_keys=True`, compact separators, `ensure_ascii=False`, `allow_nan=False` canonical JSON UTF-8 전체 크기가 65,536 bytes 이하일 때만 저장하고 raw provider response/header/body를 포함하지 않는다. 65,535와 65,536 bytes는 저장되고 65,537 bytes, JSON 불가 값, NaN/Infinity는 잘리거나 문자열 변환되지 않고 projection 실패다.
- Result reuse `supported` projection이 없거나 canonical JSON 65,536 bytes를 넘으면 최초 실행은 현재의 검증된 output으로 downstream 실행을 계속하지만 attempt는 `result_unavailable`로 닫힌다. 이후 duplicate는 provider/downstream을 호출하지 않고 typed 오류를 반환한다.
- `succeeded + result reuse unavailable`의 최초 실행은 현재 응답에서 만든 검증된 node output으로 downstream node를 정상 실행하지만 `replay_result`는 저장하지 않는다. 이후 duplicate delivery는 provider를 다시 호출하거나 downstream node를 실행하지 않고 non-retryable `external_effect.result_unavailable`를 반환한다.
- Provider replay가 `supported`이고 result reuse가 `unavailable`인 조합과 provider replay가 `unknown`이고 result reuse가 `supported`인 조합도 각각 독립된 replay matrix대로 처리한다.
- Test-only `fake.create_effect.v1`은 `Idempotency-Key` header로 padding 없는 43자 Base64url HMAC-SHA256 key를 받고 최대 64자를 허용한다. 다른 alphabet, 길이 초과와 누락 key를 provider 호출 계약 오류로 거부한다.
- Fake provider는 최초 valid request에서 effect counter를 1 늘리고 `201`과 UUID 문자열 `effect_id`만 포함한 JSON을 반환한다. 최초 수락부터 24시간 미만에는 같은 key와 같은 canonical request의 duplicate에 `200`과 최초와 같은 `effect_id`를 반환하며 effect counter는 1로 유지된다.
- Fake provider는 retention 안에서 같은 key와 다른 canonical request를 받으면 effect를 만들지 않고 `409`를 반환한다. Fake adapter는 이를 duplicate success/result projection으로 바꾸지 않고 `failed_before_effect + stop`, allowlisted `provider_key_request_conflict`, non-null provider-start marker로 닫는다. 정상 application 경로의 다른 digest는 provider 전에 identity conflict로 막히므로 이 adapter 분기는 직접 conformance fixture에서 검증한다.
- 주입한 fake clock이 최초 수락부터 정확히 24시간 경계에 도달하면 같은 key를 새 effect로 처리하고 `201`과 새 `effect_id`를 반환하지만, Runtime은 attempt 생성 DB 시각부터 24시간으로 고정한 `replay_deadline_at` 경계부터 자동 provider replay를 수행하지 않는다.
- Fake adapter result reuse `supported`의 allowlist projection과 최초/duplicate node output은 `{"effect_id":"<uuid>"}` 하나로 같고, `unavailable` variant는 projection을 반환하지 않는다.
- 다른 key는 retention과 관계없이 새 effect로 처리한다.
- Fake provider는 timeout, disconnect, provider accept 후 response loss와 malformed response를 재현해 올바른 lifecycle/outcome으로 수렴한다.
- Fake provider/adapter/server는 `apps/workflow_engine/tests/fakes/` 또는 integration support 밖의 production module, composition과 environment switch에서 import하거나 선택할 수 없다.
- Fake provider의 명시적 key field와 duplicate 결과는 공통 `supported` 분기를 검증하지만 production capability registry에 등록되지 않고 exactly-once 문구의 근거로 사용되지 않는다.
- Generic HTTP `POST|PUT|PATCH|DELETE`와 method에 무관한 `slackPostNode` request는 1차 production provider replay capability가 `unknown`, GitHub `comment_pr`는 `unsupported`이고 세 operation의 result reuse capability는 `unavailable`이다.
- Generic HTTP `GET`과 GitHub `get_pr`는 external-effect attempt를 만들지 않고 기존 조회 실행과 retry 동작을 유지한다. Legacy deployed effect 실행은 최초 broker message의 immutable deployment binding을 사용하므로 같은 execution 안에서 활성 배포 변경으로 `GET`이 mutation으로 바뀌지 않는다. 이미 attempt가 있는 mutation의 action/method/operation 변경은 stable effect slot의 identity conflict로 닫힌다.
- Generic HTTP `GET`은 기존 `status/data/headers` output과 safe `host`/`path` metadata를 유지하지만 request/response header/body, 전체 URL/query/user info는 durable trace와 log에 남기지 않는다. GET에는 attempt row와 provider key가 생기지 않는다.
- 같은 node invocation의 `effect_sequence=0`에 기존 Generic HTTP mutation 또는 GitHub comment row가 있는 상태에서 retry graph가 `GET|get_pr`로 바뀌면 read provider와 downstream 호출 횟수는 0이고 `external_effect.identity_conflict`로 종료된다. 기존 row가 없는 정상 `GET|get_pr`는 read 결과를 반환하되 attempt row를 만들지 않는다. 전용 `slackPostNode`는 HTTP method를 사용자 입력으로 받지 않고 mode별 operation으로 항상 attempt를 만들며, 같은 slot의 mode/operation/payload 변경은 provider 호출 전 identity conflict로 종료한다.
- Slack 문서의 duplicate 오류에 `client_msg_id`가 등장해도 key format/length/retention과 duplicate success/result 계약이 모두 확인되지 않으면 profile validation은 `supported` 승격을 거부한다.
- Generic HTTP node에 사용자가 `Idempotency-Key` 또는 유사 header를 설정해도 capability는 `supported`로 바뀌지 않고 outcome unknown 자동 재호출은 차단된다.
- Generic HTTP, 역사적 Slack 및 전용 Slack의 provider replay `unknown` attempt와 GitHub `unsupported` attempt는 key version/format/fingerprint와 replay deadline이 null이고 시스템이 generated key를 header/body에 추가하거나 사용자 값을 덮어쓰지 않는다. 전용 Slack의 result reuse `supported`는 provider 재호출이 아니라 성공 safe projection의 로컬 재사용만 허용한다.
- Generic HTTP mutation의 local validation/serialization 실패는 `failed_before_effect`로 닫힌다. 완전한 HTTP 응답은 status code와 관계없이 `2xx`, `4xx`, `5xx` 모두 기존 output shape를 반환하고 `succeeded + result_unavailable`로 닫힌다. Non-JSON body도 기존 text `data`로 성공하며 malformed failure로 바꾸지 않는다. MBA-190 적용 전후 status/body/header output이 같아야 한다.
- Generic HTTP DNS/TCP/TLS 연결 수립 전 실패와 connect/pool timeout은 adapter가 request byte 미전송을 증명할 때만 `failed_before_effect + retry_before_effect`로 닫힌다. 이 실패는 `in_flight` 호출 허용 경계 뒤 발생하므로 `provider_started_at`은 non-null이다. Write/read timeout, 연결 뒤 disconnect와 response loss처럼 request write 여부가 불명확한 transport failure는 `effect_outcome_unknown + stop`으로 닫힌다. Non-2xx 자체를 실패로 분류하는 테스트는 MBA-190에 추가하지 않는다.
- HTTPX mapping은 `ConnectError|ConnectTimeout|PoolTimeout`만 pre-send transient 후보로 허용하고, `InvalidURL|UnsupportedProtocol|LocalProtocolError`는 `failed_before_effect + stop`, `WriteError|WriteTimeout|ReadError|ReadTimeout|RemoteProtocolError`는 `effect_outcome_unknown + stop`으로 고정한다. 예외 message 문자열로 분류를 바꾸지 않는다.
- HTTPX/Requests의 새 subclass, allowlist에 없는 provider exception과 invoke 중 adapter bug는 `in_flight` 이후 기본 `effect_outcome_unknown + stop`으로 terminal 처리되고 generic Celery retry와 두 번째 provider 호출은 0회다. Exception message와 nested cause를 바꿔도 결과는 같다.
- `slackPostNode`는 전용 `SlackPostNode` class와 data schema를 사용하고 server-derived mode로 API의 `slack.chat.post_message.v1` 또는 Webhook의 `slack.incoming_webhook.post.v1`을 선택한다. Generic HTTP class, URL, header, body, timeout 또는 client-supplied operation으로 profile과 capability를 바꿀 수 없다. 과거 `slack.http.request.v1`은 기존 attempt lookup에만 남는다.
- API mode는 고정 `https://slack.com/api/chat.postMessage`, Webhook mode는 exact commercial incoming webhook URL과 POST만 허용한다. API의 arbitrary endpoint 및 Webhook의 query/fragment/userinfo/custom port, redirect, proxy 환경 override, private response peer는 거부한다. 실제 mode, canonical payload 또는 credential이 같은 stable slot에서 바뀌면 `effect_input_digest` 또는 frozen operation 불일치로 identity conflict가 된다.
- GitHub `get_pr`는 external effect attempt를 만들지 않고 `comment_pr`만 GitHub issue comment profile과 durable attempt를 사용한다.
- GitHub `comment_pr`는 `201`, 정수 `id`, non-empty 문자열 `html_url`, 문자열 `body` 검증을 모두 통과해야 성공이다. 누락, null과 잘못된 type fixture는 malformed response다. 완전한 `401`, `403`, `404`, `410`, `422`와 Requests `MissingSchema|InvalidSchema|InvalidURL|InvalidHeader`는 `failed_before_effect + stop`, `ConnectTimeout`만 `failed_before_effect + retry_before_effect`로 분류한다. Network-free prepare/finalize가 검출한 URL/header 오류는 `provider_started_at`이 null이다. `in_flight` commit 뒤의 `ConnectTimeout`, 같은 invalid-request 예외와 완전한 다섯 rejection 응답은 marker가 non-null이고 rejection 응답에는 status code도 남는다. 일반 `ConnectionError`, `ReadTimeout`, disconnect/response loss, `201` 뒤 JSON decode/필수 field 검증 실패와 `201` 아닌 응답 중 위 다섯 rejection status가 아닌 `2xx`, `4xx`, `429`, `5xx`는 `effect_outcome_unknown + stop`이다. Exception message와 nested cause 문자열은 결과를 바꾸지 않는다.
- GitHub `comment_pr`의 최초 `201` 성공은 MBA-190 적용 전과 같은 `comment_id`, `comment_url`, `comment_body` node output shape와 값을 downstream에 전달하되 replay result에는 저장하지 않는다. 같은 logical effect가 다시 전달되면 provider를 다시 호출하지 않고 `external_effect.result_unavailable`로 닫는다. Timeout/response loss로 outcome이 불명확해도 `unsupported + stop`으로 닫는다.
- Gmail `users.drafts.create`는 MBA-190 production composition에 추가되지 않았고 Gmail lifecycle/draft는 MBA-217 계약을 따른다. Slack `ok=false`, strict Webhook success, `Retry-After`, no-replay와 safe result reuse는 ADR-0037/MBA-218 테스트가 소유한다.
- 기존 MBA-217 Gmail 실행은 `mail_message_processings`/`mail_draft_effects`만 사용하고 `workflow_node_effect_attempts`를 만들거나 두 ledger에 이중 기록하지 않는다.
- Slack `ok=false`는 allowlist된 확정 rejection과 outcome-unknown을 구분하고, `429`의 bounded `Retry-After`는 trace hint만 남긴다. 두 경우 모두 common executor의 terminal `stop`을 사용해 같은 slot 재진입에서 provider를 다시 호출하지 않는다.

### Codex 리뷰 회귀 테스트

- `failed_before_effect + retry_before_effect` terminal 또는 만료된 prepared attempt를 새 WorkflowRun/NodeRun이 다시 claim하면 row의 `workflow_run_id`와 `node_run_id`는 현재 context 값으로 바뀌고 이후 성공 trace는 이전 실패 run이 아니라 현재 run으로 조회된다.
- GitHub `comment_pr`의 완전한 `401|403|404|410|422` 응답은 `failed_before_effect`, `provider_rejected_request`, `retryable=false`이고 provider status를 보존한다. `401`을 `effect_outcome_unknown`으로 저장하지 않는다.
- Canvas 전용 `note`만 포함한 binding 없는 legacy child graph는 external effect가 없는 것으로 판정한다. `note`와 분류 불가 node가 함께 있으면 분류 불가 node 때문에 계속 fail-closed한다.
- Stream external-effect retry signal이 Celery max retries를 소진하면 Pub/Sub에 원래 safe code와 `retryable=false`, optional `node_id`를 가진 `error` event를 정확히 한 번 발행하고 최종 task 실패를 전파한다. 재시도 예산이 남아 `celery.exceptions.Retry`가 발생한 delivery는 terminal error event를 발행하지 않는다.
- Generic HTTP, 역사적 Slack과 GitHub comment 성공·실패에서는 해당 계약의 기존 downstream output을 유지하지만 Workflow Engine log command와 Log System 저장 row/payload에는 safe provider summary만 남는다. 전용 Slack은 `status`, `delivery_status`, `delivery_mode`와 API의 검증된 `message_ref`만 downstream에 제공하고 raw `data`/`headers`는 제공하지 않는다. 모든 경로에서 opaque body/header/input, provider exception 원문과 encrypted raw payload는 남지 않는다.
- PostgreSQL row lock을 claim expiry 전부터 이후까지 유지한 경쟁에서 대기 Worker는 lock 획득 후 현재 DB wall clock으로 stale claim을 거부한다. Replay deadline 경계에서 repository가 decision을 `stop`으로 낮추면 application은 Celery retry를 요청하지 않는다.
- Legacy deployed D1 task 발행 후 D2가 활성화된 redelivery는 D1 exact snapshot을 실행한다. Current pointer가 null이거나 같은 app의 active row로 해석되지 않으면 kill switch로 차단한다.
- Generic HTTP의 `b"\\xff"`와 잘못된 charset 응답은 완전한 응답으로 text fallback되어 기존 output을 반환한다. Downgrade는 table exclusive lock 후 empty를 확인한다.
- PR PostgreSQL workflow는 schedule-dispatch와 external-effect disposable test를 함께 실행한다.
- GitHub comment node의 실제 `process_data.node_options.action=comment_pr` 구조를 시작 로그 단계에서 인식해 input/process data와 input trace payload에 원문을 저장하지 않는다.
- Stream 완료 응답은 기존 provider/downstream output을 유지하지만 durable node log에서는 provider node를 safe summary로 저장하고 그 결과를 전달받는 downstream node의 input/process/output을 빈 값으로 저장한다. `WorkflowRun.outputs`와 run-level payload에서도 provider node는 safe summary로, downstream node는 빈 값으로 저장하며 관련 없는 node output은 유지한다. 중첩 Workflow/Loop 시작 로그에는 판정 전 입력·설정 payload가 없고, 외부 결과가 없는 성공 finish만 보류 값을 기록한다. 외부 결과가 있으면 부모 컨테이너 노드와 그 downstream까지 같은 정책을 적용하며 create/finish 순서와 무관하게 원문 trace payload가 한 번도 durable row로 남지 않는다.
- 비동기 node create와 성공/오류 finish insert가 충돌하면 finish task가 winner row를 다시 읽어 terminal 상태와 metadata-only 정리를 반영할 때까지 재시도한다.
- Public stream external-effect 오류는 `data.code`, `data.message`, `data.retryable`, optional `data.node_id`를 직접 제공하며 중첩 `data.error`를 요구하지 않는다.

### 설정, 비밀정보와 회귀

- Workflow Engine Worker startup readiness는 기존 shared Alembic helper 기준 current code head와 DB revision이 다르거나 필수 external-effect table/column/constraint/index shape가 없으면 provider task를 받기 전에 fail-closed한다. DB가 MBA-190 revision에 정확히 머문 경우만 허용하는 검사는 실패해야 하며, 이후 descendant migration을 적용한 current head와 필수 shape가 있으면 통과한다.
- 필수 schema 이름이 모두 있어도 counter CHECK가 `effect_sequence >= 0` 또는 `claim_generation > 0`과 다른 연산 방향을 사용하거나, 여섯 시간 column 중 하나라도 `TIMESTAMP WITH TIME ZONE`이 아니면 readiness는 실패한다.
- Workflow Engine Worker startup readiness는 Celery hard time limit이 없거나 0/음수여서 30초 여유를 더한 claim TTL을 계산할 수 없으면 provider task를 받기 전에 fail-closed한다.
- Workflow Engine Worker startup readiness는 terminal을 포함한 모든 attempt가 참조하는 distinct 과거 provider contract version을 현재 registry에서 찾을 수 있는지 검사한다. 하나라도 누락되면 현재 version으로 대체하지 않고 startup을 거부한다.
- 영구 종료된 `reuse_result`, `result_unavailable`, `stop` row도 duplicate delivery에서 frozen digest/decision 검증이 필요하므로 contract readiness 대상이다. 다만 provider 재호출이 없으므로 HMAC key readiness 대상은 아니다.
- Production `supported` profile이나 재호출 가능한 supported attempt가 없으면 HMAC 설정 없이 Workflow Engine Worker가 시작된다. 둘 중 하나가 있으면 active/required version 누락, key 누락, 중복 version, 형식 오류와 허용하지 않은 `SECRET_KEY` 재사용을 startup에서 거부한다.
- 같은 설정에서 Log System Worker는 MBA-190 table/provider/HMAC readiness와 무관하게 시작되며 공용 Celery app이 Workflow Engine 전용 검사를 등록하지 않는다.
- Shared SQLAlchemy model 추가 뒤 Gateway, Log System과 Sandbox import/startup smoke가 통과하고 MBA-190 readiness는 Workflow Engine Worker composition에서만 실행된다.
- Contract readiness query plan은 `(provider, operation, provider_contract_version)` 일반 index로 distinct version을 찾고 영구 terminal history만 있는 대량 fixture도 전체 순차 조회하지 않는다. HMAC readiness query는 재호출 가능한 supported row의 partial index predicate를 사용한다.
- 같은 canonical identity와 key version은 process restart 뒤에도 같은 `hmac-b64url-v1` provider key와 최종 key ASCII bytes의 SHA-256 lowercase hex 64자 fingerprint를 만든다. Raw key는 assertion failure output, log와 trace에 출력하지 않는다.
- HMAC test key는 test process에서 매번 생성해 dependency로 주입하고 repository fixture, snapshot, 문서 또는 assertion message에 원문을 고정하지 않는다.
- Active HMAC key가 교체돼도 기존 attempt replay는 저장된 이전 key version을 사용한다.
- 필요한 이전 key가 없으면 새 key로 provider를 호출하지 않고 fail-closed한다.
- Length-delimited canonical encoding은 field 연결 방식의 충돌을 만들지 않으며 provider별 길이 제한 뒤에도 deterministic key를 만든다.
- Provider key canonical input은 organization/app/workflow provenance를 포함해 같은 logical identity라도 다른 organization이나 target workflow에서 다른 key를 만든다.
- Provider-visible raw key, raw internal identity, `effect_input_digest`, provider request identifier와 HMAC secret은 prompt, user-visible node output/응답, durable trace, metric label과 일반 log에 포함되지 않는다. Internal identity, digest와 key fingerprint는 각각 정의된 operational attempt column에만 저장할 수 있다.
- 저장소 안 Draft/deployment/API/public/webhook/schedule/stream/Compare/Cost Optimizer 및 model-routing publisher는 `app.send_task` 공통 helper를 통해 Celery message에 low-cardinality 고정 `argsrepr`/`kwargsrepr`을 설정한다. Literal `workflow.*` 발행 지점을 정적 inventory로 대조해 helper 우회가 하나라도 있으면 실패한다. Workflow task 전용 Task base는 `apply_async` 기본 repr을 강제해 `.delay`, signature와 `self.retry`의 `task-sent` event를 보호한다. Request base는 악의적이거나 누락된 publisher repr을 수신 INFO log와 `task-received` event 발행 전에 같은 상수로 덮어쓴다. Worker log/event와 publisher `task-sent` event fixture에는 execution/node identity, claim/deployment/workflow UUID, graph/input, WorkflowNode binding, credential marker와 raw payload가 없다. 실제 task args decode와 실행 결과는 바뀌지 않는다.
- Generic HTTP trace는 기존 목적지 `host`와 `path`를 계속 제공한다. URL에 `user:password@host:port`가 있어도 `host`는 parsed hostname과 명시 port만 포함하고 user info는 포함하지 않는다. `https`의 `443`, `http`의 `80`처럼 scheme 기본 포트를 명시한 경우에도 `host`에서 포트를 제거하지 않으며, 포트를 생략한 URL에는 기본 포트를 합성하지 않는다. MBA-190 적용 전후 두 field 이름과 user-info가 없는 일반 URL의 값은 유지된다.
- Generic HTTP 현재 실행은 MBA-190 적용 전과 같은 `status`, `data`, `headers` node output을 반환하지만 attempt/replay result에는 이 raw output을 저장하지 않는다. Redaction 규칙은 현재 실행 output 계약과 durable trace/ledger 비노출을 구분한다.
- 전체 URL, query, fragment, URL user info, request/response header/body와 provider exception message에 secret marker가 있어도 durable trace/log에 남지 않는다. `host`와 `path` 자체의 추가 비식별화는 이 테스트 범위가 아니다.
- Fake provider integration에서 `httpx`, `httpcore`, `urllib3`/Requests logger를 DEBUG/INFO로 활성화해도 Workflow Engine이 수집한 log에는 URL/query/user info/header/body/key 또는 provider exception marker가 없고 adapter의 safe summary만 남는다. 이 억제 설정 때문에 Log System Worker logger 구성이 바뀌지 않는다.
- 각 `workflow.*` publisher에서 serialization/broker 예외 message와 nested cause에 graph/input/credential marker를 넣어도 log, traceback과 API/Compare 오류 문자열에는 marker가 없고 static publish operation, exception class와 기존 safe 실패 envelope만 남는다.
- Generic HTTP, Slack과 GitHub external-effect trace/log는 provider, canonical operation, method, status code, request/response size, latency, 두 지원 수준, outcome/replay decision과 safe error code만 남기고 Generic HTTP에 한해 기존 `host`/`path`를 유지한다. Base Node error log에도 provider exception 원문이 들어가지 않는다. Key fingerprint는 operational attempt column에만 있고 durable trace, 일반 log와 metric label에는 없다. Trace와 attempt는 `workflow_run_id`/`node_run_id`로 연결된다.
- Schedule dispatch idempotency key는 workflow admission 경계에 남고 provider-visible raw key로 직접 재사용되지 않는다.
- MBA-190은 새 Public Workflow endpoint, 성공 node output schema나 Client UI를 추가하지 않는다. 기존 스트리밍 `error` 이벤트와 구조화된 실행 실패 응답은 최소 `external_effect.result_unavailable`, `external_effect.identity_conflict`, `external_effect.outcome_unknown` 중 해당 code와 `retryable=false`를 보존한다. `failed_before_effect + stop`의 기존 allowlisted provider/configuration code도 같은 JSON-safe shape로 전달하고 raw 예외 문구를 사용하지 않는다.
- 세 최소 오류, `external_effect.prepare_failed`와 다른 allowlisted stop 오류 모두 Node/application error부터 Workflow Engine, JSON-safe Celery task result, Pub/Sub와 Gateway까지 `code`, safe `message`, `retryable=false`, optional `node_id`가 message 문자열이나 custom exception attribute로 축소되지 않는다. Non-retryable task는 generic `self.retry`를 호출하지 않는다.
- Gateway의 일반 실행, deployment/public URL, Compare/Cost Optimizer와 SSE projection은 미등록 `external_effect.*`, 내부 `external_effect.retry_allowed`와 `retryable=true` payload를 외부에 전달하지 않는다. 등록된 terminal payload의 message와 추가 field는 public code-owned shape로 정규화하고, 거부된 payload의 원문 message는 generic 실행 실패 응답에 포함하지 않는다.
- Generic HTTP V1 digest 테스트는 framing helper를 재사용하지 않는 고정 vector로 `uint32-be length + field` 순서, domain, header 정렬·중복 순서, body mode와 sorted-key JSON bytes를 검증한다. 같은 V1에서 정렬 JSON object 전체를 직접 hash하는 구현은 이 vector를 통과하지 못해야 한다.
- `LoopNode`의 `error_strategy=continue` 안에서 Generic HTTP, Slack 또는 GitHub external-effect가 retry signal, `result_unavailable`, `identity_conflict`, `outcome_unknown`, `prepare_failed` 또는 다른 terminal safe error를 반환하면 Loop가 이를 `{"error": str(e)}` 결과로 바꾸거나 다음 iteration/downstream node를 실행하지 않고 동일한 구조화 payload를 task 경계까지 다시 전달한다. Nested WorkflowNode 안에서 발생한 동일 signal도 parent Loop와 parent WorkflowEngine을 그대로 통과한다.
- Base Node와 WorkflowEngine의 일반 예외 log/Pub/Sub 분기는 external-effect control signal을 문자열로 바꾸거나 provider 원문을 기록하지 않는다. WorkflowEngine은 safe run error를 한 번 기록하고 stream task만 동일 payload의 `error` event를 정확히 한 번 발행한다.
- Root task와 `WorkflowNode`의 nested engine cleanup 또는 DB session close가 예외를 내도 exception type만 safe log에 남고 원래 retry signal, terminal safe payload와 terminal commit 뒤 성공 output이 그대로 유지된다. Cleanup 실패 때문에 generic `self.retry`가 추가 호출되거나 stream `error` event가 중복 발행되지 않는다. 한 child cleanup 실패 뒤에도 다른 child와 engine reference가 모두 정리된다.
- 일반 동기 Gateway `/workflows/{id}/execute`와 authenticated/API/public deployment run은 기존 HTTP `500`과 top-level `detail` envelope을 유지한 채 safe error payload를 mapping하고, stream은 같은 필드를 `error.data`에 넣는다. Compare와 Cost Optimizer compare는 기존 HTTP `200` response 안에서 해당 variant/candidate를 failed로 두며 각각 기존 safe `error`/`error_message`와 additive `error_detail`에 payload를 보존한다. Provider 원문과 internal field는 모든 경로에서 없다.
- Webhook은 enqueue 접수 응답을 유지하고 사후 오류를 응답으로 바꾸지 않으며 caller가 사용하지 않는 task result와 safe log만 사용한다. Schedule `external_effect.outcome_unknown`은 기존 `execution_outcome_unknown`, `external_effect.result_unavailable`과 `external_effect.identity_conflict`는 기존 `execution_failed_after_admission`으로 dead-letter finalization된다. Claim `safe_reason_code` check에 `external_effect.*`를 추가하지 않는다. 구체 code는 task-local safe error/trace에서 전달되고, terminal attempt에 적용 가능한 allowlisted `error_code`는 저장할 수 있지만 identity conflict는 기존 winner attempt를 변경하지 않는다. Schedule task retry 횟수, backoff와 claim state machine은 바꾸지 않는다.
- `external_effect.result_unavailable` 오류의 message, 저장 실행 오류와 SSE event에는 provider 응답 원문, header/body, 내부 identity, provider-visible key와 provider exception 원문이 없다.
- Workflow draft 저장/조회, test/deployed execution과 MBA-187 schedule admission regression이 통과한다.
- Migration, constraint와 concurrency 완료는 SQLite나 in-memory repository test만으로 주장하지 않고 실제 PostgreSQL integration test로 검증한다.
- MBA-190 구현은 attempt cleanup으로 row 또는 `replay_result`를 조기 삭제하지 않는다.

## E2E Tests

- `팀별 온보딩 문서 접근 제어 데모`는 active Internal Chatbot으로 seed되고 플랫폼개발팀·영업팀은 `operator`, People 팀은 `manager` Workflow 권한을 가진다.
- runtime OpenAI credential 모드의 demo seed는 fixture 재생성 없이도 `demodata/` 온보딩 PDF 네 개를 대응 KB에 실제 파싱·embedding하여 위 Internal Chatbot에서 즉시 검색 가능하게 만든다. 이 경로는 로컬 법령 PDF 원본을 요구하지 않는다.
- 같은 배포를 김서연과 이준호가 실행할 때 LLM node의 direct KB 설정은 같아도 runtime candidate resolver 결과는 execution subject의 Team Knowledge 권한에 따라 달라야 한다. Workflow owner나 People 관리자 권한으로 fallback하면 실패한다.
- 수동 PDF가 아직 업로드되지 않은 빈 KB 상태에서는 LLM node가 다른 팀 자료를 추측하지 않고 safe no-evidence 응답으로 닫혀야 한다.

- 배포된 workflow의 LLM node의 RAG 옵션은 execution subject가 있으면 해당 subject 기준으로 KB permission/source ACL gate를 다시 평가하고, subject가 없으면 anonymous public-only gate를 사용한다. Builder actor 권한으로 fallback하지 않는다.
- Evidence sufficiency가 insufficient인 경우 workflow는 추측 답변을 생성하지 않고 safe no-result 응답 또는 명시된 분기 결과를 반환한다.
- Query rewrite가 켜진 LLM node의 RAG 옵션도 권한 없는 KB 또는 requester source authorization denied 문서를 prompt, citation, trace에 포함하지 않는다.
- 배포 시점에 available이던 KB가 실행 시점 source ACL stale/revoked 상태가 되면 workflow는 설정된 failure policy에 따라 safe no-result, fallback branch, 또는 node/workflow failure로 닫고 세부 source ACL reason을 사용자에게 노출하지 않는다.

## Permission Tests

- Workflow owner가 KB `use` 권한을 갖고 있어도 execution subject가 권한을 갖지 않으면 RAG retrieval은 실패하거나 resource-hidden/no-result matrix를 따른다. Execution subject가 없으면 owner 권한 대신 public-only 후보만 사용한다.
- Skill visibility 또는 workflow 작성 권한만으로 runtime KB permission/source ACL gate가 충족되지 않는다.
- RAG를 포함한 workflow compare/A-B 실행도 로그인 실행에서는 일반 workflow 실행과 같은 execution subject를 사용하고, subject가 없으면 public-only gate를 사용한다.
- Public Chatbot route에 login cookie가 있어도 anonymous public audience를 유지하고 private KB/Memory를 허용하지 않는다. Target authenticated internal Chatbot은 별도 access policy/runtime namespace가 구현된 경우에만 user execution subject를 사용한다.
- Active deployment 변경과 queued old-session task 경합에서 Worker는 pinned deployment snapshot을 사용하거나 side effect 전에 version conflict로 닫고 current graph를 임의 실행하지 않는다.
- LLM Credentials가 발급한 ProviderExecutionCapability identity/revision의 purpose, deployment/node/admission/provider-attempt binding 또는 current validity mismatch는 context materialization, provider call과 budget reservation 전에 fail-closed 한다.
- Capability-required LLM node는 `n|best_of`가 정확한 정수 `1`이 아니면 provider 호출 전에 차단한다. Knowledge 후보가 0개면 embedding capability와 provider operation을 만들지 않고, 후보가 있으면 distinct canonical embedding model마다 `query_embedding` capability/attempt를 하나 사용한다. Model별 policy, ADR-0067 egress 또는 ADR-0069 durable operation 경계가 없으면 legacy credential selection과 embedding/main provider 호출을 수행하지 않는다.

## Edge Cases

- `llm_assisted` query rewrite는 별도 승인 전까지 실행 가능한 variant가 아니다. 승인 후 실패하면 승인된 fallback 정책에 따라 원 query 사용 또는 terminal error로 처리하고, raw rewritten query를 durable metadata에 저장하지 않는다.
- 일부 authorized KB retrieval만 operational failure가 발생하면 Knowledge partial-result 정책에 맞춘 safe summary만 반환한다.
- Workflow runtime outbound egress guard는 Knowledge source collection egress boundary와 별도 gate이므로, Knowledge source connector guard가 workflow HTTP node 전체를 보호한다고 가정하지 않는다.

## Workflow Draft CAS Regression

- 서로 다른 workflow의 Agent Builder 저장이 겹쳐도 먼저 끝난 작업이 전역 저장 차단을 해제하지 않고 마지막 작업 뒤에만 test/autosync/Undo를 허용하는지 검증한다.
- transport/`5xx` 결과 유실만 같은 mutation context로 한 번 재시도하고 명시적인 `401|403|409|422`는 두 번째 draft POST 없이 실패하는지 검증한다.
- Mutation acknowledgement도 transport/`5xx`처럼 결과가 불명확한 경우에만 같은 payload로 한 번 재시도하고, 명시적인 `401|403|409|422`는 즉시 원래 오류를 반환하는지 검증한다.

- Canonical draft GET과 successful draft POST가 실제 persisted `workflow_id`, server-calculated `graph_hash`, DB `updated_at`을 반환하고 synthetic revision을 반환하지 않는지 확인한다. Agent Builder 저장, version 복원 또는 test 전 저장 뒤 수동 편집 autosync가 직전 POST metadata를 사용하며 stale metadata로 409에 빠지지 않는지 확인한다.
- 일반 autosync 두 개가 같은 expected hash/timestamp로 경쟁하면 row lock 뒤 하나만 성공하고 다른 요청은 `409 stale_graph`인지 확인한다.
- 일반 autosync와 Agent Builder mutation save가 같은 base에서 경쟁해도 공통 CAS가 silent overwrite를 막고 Agent Builder의 expected-result validation은 추가로 유지되는지 확인한다.
- Agent Builder acknowledgement 뒤 stale snapshot을 가진 Model Routing bootstrap POST/policy PATCH 또는 Cost Optimizer apply/recommendation apply가 실행되면 workflow row lock 뒤 `409 stale_graph`로 닫히고 graph와 bootstrap/policy/candidate 부가 상태를 모두 보존하는지 확인한다. 성공 경로는 canonical `graph_hash`와 `updated_at`을 반환하고 frontend 공통 metadata를 갱신해야 한다.
- Canonical draft 재조회가 local Workflow history, Agent Builder pending boundary와 ambiguous save context를 초기화하지 않는지 확인한다.
- Save/ack/revert/redo response loss에서 canonical graph metadata로 applied/unapplied/stale을 판정하고 결과 확정 전 pending history를 삭제하지 않는지 확인한다.

### MBA-275 Configuration And Schedule Admission Regression

- `WorkflowNode`(`local_execution`)는 `appId`가 missing/deferred이면 test/run/deployment/schedule에서 동일한 preflight blocker로 차단되고 client가 resolved 상태를 위조해도 통과하지 않는다. 유효한 `appId`만 있고 `workflowId` key가 아예 없는 graph도 NodeFactory에서 생성되어야 하며, 빈 `workflowId`를 가진 기존 modal 생성 graph도 오탐 없이 통과한다.
- `loopNode.subGraph`는 필수다. 문자열, 빈 값 또는 누락된 `loop_key`는 mapped input의 첫 배열을 쓰는 runtime fallback에 따라 오탐 없이 통과하고, subGraph 내부의 unresolved local/external node는 같은 규칙으로 차단된다. Body implicit entry의 `loop.item|index`, 상위 실행 입력, Loop까지 방향성 선행 경로가 있는 output과 유효한 명시적 mapped input selector는 통과한다. 후속 body node가 같은 inherited source를 직접 참조하거나 downstream nested Loop child로 우회 전달하면 차단되고 완료된 local predecessor output만 사용할 수 있다. Parent graph의 후행·형제 node source, unknown loop key와 삭제된 상위 output을 가리키는 mapping은 Loop와 해당 child에서 차단한다. Depth 16은 통과하고 17은 `RecursionError` 없이 fail-closed한다.
- required configuration이 없는 local node와 완전히 resolved graph는 오탐 없이 기존 실행 경로를 통과한다.
- Gateway schedule dispatch는 unresolved configuration과 DB 기반 target/policy blocker를 publish 전에 차단하고 broker publisher를 호출하지 않는다. Worker는 locked canonical root identity와 공통 configuration을 재검사하며, target resource의 publish 이후 변경은 각 runtime authoritative gate가 차단한다.
- worker가 받은 unresolved claim은 locked snapshot preflight 뒤 `configuration_preflight_blocked`로 한 번만 canceled 처리한다. `workflow_run_id`/`started_at`은 생성되지 않고 budget, `mark_running()`, Knowledge sync, engine/provider와 Celery retry는 호출되지 않는다.
- 같은 claim 재전달은 terminal duplicate 결과로 억제되며 claim을 reopen하거나 다시 실행하지 않는다. resolved claim의 기존 성공·실패 경로는 회귀하지 않는다.

### MBA-322 Workflow Citation Regression

- 새 LLM node와 Agent Builder node는 `citationDisplayMode=detailed`, legacy missing field는 `hidden`인지 검증한다.
- Grounding lexical metadata 옵션과 Citation 표시 옵션을 서로 독립적으로 변경할 수 있는지 검증한다.
- Answer data ancestry에 있는 LLM Citation만 최종 응답에 병합하고 control-only node와 subworkflow reserved key를 제외하는지 검증한다.
- `LLM -> CodeNode inputs[].source -> Answer` data path도 Citation lineage에 포함되는지 검증한다.
- 조건 분기로 이번 실행에서 skip된 LLM node가 이전 실행의 ephemeral Citation을 재사용하지 않는지 검증한다.
- final response sidecar는 최대 8개, 전역 rank, stable dedupe를 적용하고 durable run output에서는 제거되는지 검증한다.
- legacy output 또는 stream node id가 sidecar key와 충돌하면 기존 output·durable output을 보존하고 Citation만 생략하는지 검증한다.
- detailed preview는 공통 fail-closed redaction을 거치며 redaction 실패 또는 PII/secret 검출 시 preview만 생략하고 답변은 유지하는지 검증한다. 표시명 lazy-load 실패도 generic label로 격리한다.
- Client parser는 unknown version, extra/identity field, URL/file path/secret marker, rank mismatch와 현재 graph node id에 충돌하는 reserved key를 거부하면서 답변 렌더링은 유지하는지 검증한다.
- Test sidebar와 인증 실행 화면이 같은 Citation component를 사용하고 keyboard/mobile-safe markup을 유지하는지 검증한다.

### MBA-283 Generic HTTP Egress Regression

- URL userinfo, localhost, RFC1918, IPv6 ULA/loopback/link-local, carrier-grade NAT, reserved, unspecified, multicast와 metadata target은 TCP dial 전에 `invalid_prepared_request`로 차단되고 오류·trace에 query/header/body/resolved IP가 남지 않는다.
- DNS가 public과 차단 IP를 함께 반환하면 전체 요청을 거부한다. 모든 주소가 안전하면 custom network backend는 DNS/OS 순서대로 dial하고 첫 주소의 TCP connect 실패 시 다음 검증 주소로 폴백한다. 모든 검증 주소가 실패하면 `connection_failed`로 닫고, 첫 연결의 peer mismatch에서는 다음 주소를 시도하지 않는다. 검증 뒤 hostname DNS 응답이 바뀌어도 검증 목록 밖의 주소로 dial하지 않는다.
- Public HTTP/HTTPS 80/443 happy path는 HTTPX 0.28 Generic HTTP V1 canonical digest, application header/JSON wire serialization, status/data/headers output과 non-2xx 성공 판정을 유지한다.
- URL fragment는 network target에 전달하지 않고 userinfo, hop-by-hop/proxy header, 비허용 method/scheme/port와 request body/header 상한 초과를 pre-send permanent failure로 닫는다. Environment proxy는 사용하지 않는다.
- GitHub comment의 request 상한은 comment 문자열 단독 크기가 아니라 compact UTF-8 JSON `{"body": ...}` 전체 wire 크기로 판단한다. 따옴표·역슬래시 escape와 JSON field overhead를 포함해 profile 상한과 정확히 같은 body는 준비되고 이를 초과하면 provider 호출과 durable attempt 이전에 거부되며 safe trace의 `request_size`도 같은 wire 크기를 사용한다.
- Redirect-to-private 응답은 첫 3xx status/data/headers를 반환하고 두 번째 connection을 만들지 않는다. Peer mismatch는 request byte 전송 전 non-retryable 실패로 닫고, 압축·oversized response와 read 실패는 output을 사용하지 않고 outcome unknown으로 닫는다.
- Connect 전에 전송이 없다고 증명되는 일시 실패만 retry-before-effect가 될 수 있다. Write/read timeout, response loss와 전송 뒤 검증 실패를 안전한 재시도로 바꾸지 않는다.
- `HttpRequestNode`와 Generic HTTP provider는 `httpx.Client`를 직접 생성하지 않고 application outbound port를 사용한다. Production import/architecture contract가 직접 client 회귀를 탐지한다.
- Helm render의 Worker egress NetworkPolicy는 cluster DNS, configured PostgreSQL port, Redis 6379, Sandbox 8194와 internal Squid 3129만 허용하고 public 80/143/443/993 direct route를 두지 않는다. Squid 3129는 Worker source의 IMAP 143/993 CONNECT만 별도로 허용한다. External dependency CIDR은 해당 configured service port에만 적용되며 public catch-all CIDR을 허용하지 않아야 한다. Canary selector는 proxy revision label을 요구하고 final selector는 모든 Worker pod를 포함한다.
- Pinned kind+Calico IPv4 runtime에서 Worker-shaped probe의 direct public HTTPS는 실패하고 Squid `3129` 경유 HTTP/HTTPS는 성공해야 한다. Unauthorized Sandbox-shaped probe는 Squid ingress에 실패하며 proxy replica가 unavailable해도 direct fallback은 없어야 한다. Dual-stack과 실제 배포 CNI는 release 환경에서 같은 probe를 통과해야 한다.

## Test preflight와 실행 presentation state

- Test preflight 중 Agent Builder save가 대기하면 stream을 호출하지 않고 coordinator를 해제한 뒤 Agent Builder save가 진행되는지 검증한다.
- Clean 표시가 누락됐어도 local graph와 canonical server graph가 다를 때 서버의 `graph_hash + updated_at`이 마지막 local 기준점과 같으면 현재 snapshot을 CAS 저장한 뒤 테스트를 실행한다. 서버 기준점이 달라진 실제 동시 변경이면 test와 save를 차단하고 최신 metadata를 stale local graph에 적용하지 않는다.
- `operation envelope not found`는 Agent Builder 결과를 `applied|unapplied|pending|stale`로 구분하고 권한 오류나 일반 저장 실패로 표시하지 않는지 검증한다.
- Node root의 React Flow measurement/selection field와 node data의 실행 status, observability, editor-only `displayNumber` 갱신이 최상위, 중첩 `subGraph.nodes`와 `features.noteNodes`의 autosync, draft payload, canonical hash와 Workflow Undo/Redo history에 포함되지 않는지 검증한다. 모든 client save path와 Gateway save가 같은 projection을 사용하는지 함께 검증한다.
- Server-derived node `configuration_state` 차이만으로 Client canonical 비교가 실패하지 않고, Client payload에서는 최상위 및 중첩 값이 제거된 뒤 Server가 재계산하는지 검증한다.
- Canonical server draft와 local snapshot의 viewport만 다르면 test preflight graph 비교는 일치로 판정하지만 일반 full snapshot 비교와 실제 draft 저장·복원은 viewport 차이를 유지하는지 검증한다.
- Test 전 save 중 발생한 별도 수동 편집은 dirty 상태와 history를 유지하는지 검증한다.
