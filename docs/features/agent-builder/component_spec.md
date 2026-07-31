# Agent Builder Component Specification

Status: Draft

## 1. Architecture Goal

Agent Builder의 자연어 해석, 생성 mode 정책, graph 작성, parameter 계약, UI presentation을 한 service 또는 한 React component에 모으지 않는다. 각 모듈은 하나의 변경 이유를 가지며, module 간 교환 값은 typed schema로 제한한다. Direct-edit와 저장 경계는 ADR-0045/0046, canonical mode와 전환은 ADR-0054를 따른다. MBA-293 시점 코드는 기존 mode만 구현했으며 이 문서의 quick mode component는 후속 구현 목표다.

목표 dependency 방향:

```text
API/UI -> application orchestration -> domain services -> catalog/policy ports
UI -> editor adapter -> workflow store
runtime contract -> catalog validation -> Agent Builder
```

planner는 catalog와 graph policy를 우회할 수 없고, frontend는 node별 규칙을 복제하지 않는다.

## 2. Backend Components

### 2.1 AgentBuilderEndpoint

위치: `apps/gateway/api/v1/endpoints/agent_builder.py`

책임:

- request parsing
- 인증 사용자와 active organization dependency 연결
- application service 호출
- response schema 반환

금지:

- graph operation 직접 생성
- parameter task 순서 계산
- Knowledge 후보 ranking
- permission policy 재구현

### 2.2 AgentBuilderApplicationService

목표 위치: `apps/gateway/application/agent_builder/service.py`

책임:

- session과 request lifecycle orchestration
- 신규 session에 `direct_edit_v1` protocol을 기록하고 기존 null Preview session은 `stale_protocol`로 복구함. Preview fallback이나 자동 변환은 제공하지 않음
- requested mode 정규화, quick eligibility, 명시적 guided transition과 남은 설정 quick-completion orchestration
- planner, target resolver, catalog, GraphMutation builder, parameter planner 호출 순서 관리. Mode transition과 remaining quick completion에서는 전체 planner를 다시 호출하지 않음
- transaction, audit, idempotency 경계 관리
- legacy Preview session을 `stale_protocol`로 복구하고 direct-edit operation 적용 차단

현재 대형 `AgentBuilderService`는 전환 기간 facade로 남기고 새 endpoint를 application use case에 위임한다.

Application package는 shared Pydantic request/response contract를 사용할 수 있지만 FastAPI, SQLAlchemy, DB model, concrete Gateway service/adapter를 직접 import하지 않는다. 현재 DB session, 권한 query, audit와 transaction을 직접 조율하는 `KnowledgeSelectionService`, `GraphMutationLifecycleService`, `ParameterCandidateProvider`, `ParameterTaskService`는 과도기 concrete coordinator로 `apps/gateway/services/agent_builder/`에 둔다. 이 위치는 헥사고널 이관 완료를 의미하지 않으며, 이후 use case/port 분리는 동작 보존 테스트와 함께 작은 단위로 진행한다.

#### GenerationModePolicy

목표 위치: `apps/gateway/application/agent_builder/generation_mode_policy.py`

책임:

- `configure_and_generate`를 canonical `guided_generate`로 정규화하고 mode 생략 시 guided 기본값 결정
- `generation_mode_source=default|explicit_control`과 구조화된 자연어 mode intent를 합성해 명시적 control, 명시적 자연어, default guided 순으로 requested mode 결정
- requested/effective mode, source, request/proposal version과 transition 상태를 framework-independent result로 반환
- 사용자 확인 없는 quick-to-guided 전환 금지

Transport adapter는 `X-Agent-Builder-Mode-Contract`를 읽어 legacy/canonical 외부 표현을 application의 canonical result와 양방향 변환한다. Request가 없을 때는 호출별 표현만 선택하고, message request 생성 시 정규화한 contract를 DB adapter가 기존 request metadata에 고정한다. Request id로 대상을 특정하는 후속 호출은 해당 request와 같은 contract만 허용하며 불일치는 payload projection 없이 `mode_contract_mismatch`로 반환한다. 이 협상은 GenerationModePolicy의 domain 판단과 canonical mode 저장값을 바꾸지 않는다.

#### QuickGenerationEligibilityPolicy

목표 위치: `apps/gateway/application/agent_builder/quick_generation_policy.py`

책임:

- Catalog capability, unresolved parameter, permission/resource snapshot, graph/resource revision과 side-effect classification을 입력으로 받는 pure fail-closed 판정
- credential, 권한 resource 선택, 일반 required parameter의 확정 가능한 값 부재, 외부 대상, Condition branch, HTTP/code/egress, unresolved external action, 복수 후보와 stale context를 allowlisted safe reason code로 반환. 일반 값 부재는 `configuration_value_required`로 분류
- 대상 resource의 이름, id, 정확한 후보 수 또는 secret을 result에 포함하지 않음
- initial quick generation과 guided의 remaining quick completion에 같은 primitive를 사용하되 evaluation scope를 구분

금지:

- LLM 호출 또는 planner reasoning을 eligibility 증거로 사용
- DB/FastAPI/React 타입 import
- 권한 query 직접 수행. Application이 port를 통해 만든 현재 snapshot만 입력으로 사용

#### Existing model recommendation boundary

- `LLMService.get_agent_answer_options()`는 active organization의 valid credential, active chat model, provider 일치, verified relation과 사용자 credential `use` 권한을 통과한 후보를 조회한다.
- `LLMService._order_agent_builder_options()`는 중복 relation 중 가장 낮은 priority 하나를 남기고 공통 추천 정책 입력으로 변환한다.
- `model_recommendation_policy.sort_model_candidates()`는 ADR-0040의 provider, generation, `general|mini|nano|pro`, suffix와 특수 목적 제외 정책을 결정론적으로 계산한다. 이름을 해석하지 못한 verified chat model은 provider 후순위 fallback으로 유지한다.
- `AgentBuilderService._recommended_draft_model_id()`는 같은 권한 후보의 첫 model id를 새 generated LLM node에만 적용한다. Header 사용자 선택과 credential은 graph에 복사하지 않고 기존 node model은 보존한다.

### 2.3 IntentPlanningService

기존 위치: `apps/gateway/services/agent_builder_intent_service.py`

입력:

- safe user summary
- server-loaded workflow summary
- selected target hint
- safe Knowledge candidate context
- allowed capability summary

출력:

- request type
- 사용자 요청에 명시된 `guided_generate|quick_generate|structure_only` mode intent 또는 null. Eligibility나 권한 승인으로 사용하지 않으며 `legacy-v1` quick intent는 application resolver가 활성화하지 않음
- planned steps와 dependency
- edit target reference
- typed Knowledge placement requirement. Requirement id, timing, target step, effect kind와 insert step의 upstream/downstream/empty-selection bridge를 포함하고 선택별 완성 graph는 포함하지 않음
- `step_id`, Catalog-listed `parameter_key`, `reason`, `input_guidance`로 구성된 parameter guidance hints

제약:

- 정상 request당 provider 호출 한 번. 최초 schema-valid 결과의 semantic invariant 위반에만 safe-code repair 최대 한 번
- provider 호출, content 추출, JSON 파싱, non-object JSON 또는 Pydantic 구조 schema 실패에는 repair하지 않고 fail-closed
- parameter, Knowledge와 task 전환 중 추가 planner 호출 금지
- secret-like input 차단 또는 redaction 후 호출
- parameter key와 validation rule을 결정하지 않음

### 2.4 WorkflowContextService

목표 위치: `apps/gateway/application/agent_builder/workflow_context.py`

책임:

- workflow id를 active organization과 permission 범위에서 조회
- direct-edit session에서 workflow id 누락을 거부하고 workflow row를 직접 생성하지 않음
- saved draft graph의 canonical graph hash와 workflow `updated_at` 조회
- selected node/edge hint 검증
- runtime과 무관한 UI metadata를 semantic hash에서 제외

client graph snapshot은 받지 않는다.

### 2.5 CapabilityCatalogProvider

기존 위치: `apps/shared/services/workflow_node_catalog.py`

책임:

- node 지원 여부와 connection policy
- parameter schema, stable order와 `defer_policy=forbidden|allow_unresolved`
- input/output contract
- side effect와 required configuration
- Knowledge selection timing 기본 정책
- 최신 dev의 `mail_search`, `gmail_reply_draft_create`, `mail_terminal_acknowledgement` capability와 required credential reference

MBA-228 direct-edit는 단일 catalog v3 parameter/input/output schema를 사용한다. Catalog JSON schema는 startup/static test에서 검증한다. Frontend는 API가 반환한 catalog-derived view model을 사용하고 JSON 파일을 별도 해석하지 않는다. 새 GraphMutation의 API 응답은 full operations를 포함하지만 기존 request JSON에는 operations를 제외한 safe envelope와 `catalog_version=3`만 기록한다. 복구된 envelope의 버전이 없거나 `2`이면 legacy stale, `3`이면 current validation 후보로 분류한다. Cutover 뒤 legacy Preview session은 catalog upgrade 대상이 아니라 `stale_protocol`이다.

### 2.6 TargetResolver

목표 위치: `apps/gateway/application/agent_builder/target_resolver.py`

책임:

- selected node/edge hint와 natural-language reference를 server graph에 resolve
- `before`, `after`, `between` insertion 위치 확정
- `natural_language_edge`는 source/destination 각각을 resolve한 뒤 직접 edge가 정확히 하나일 때만 `between` target으로 확정
- 후보 0개/다중 후보를 clarification으로 반환

금지:

- target이 불확실할 때 새 Start/Answer wrapper 생성
- client가 보낸 label만으로 node 확정
- multi-hop path 탐색 또는 자연어 edge 후보의 임의 선택

### 2.7 GraphMutationBuilder

목표 위치: `apps/gateway/application/agent_builder/graph_mutation_builder.py`

책임:

- structured plan과 resolved target을 `initial_graph`, `graph_edit`, `replace_workflow`, `parameter_update`, `knowledge_binding` mutation으로 변환. `generation_mode`는 kind와 독립적으로 처리
- catalog connection policy와 handle 적용
- node template 생성
- auto layout 적용
- `add_node`, `remove_node`, `add_edge`, `remove_edge`, `replace_node_data` operation만 생성
- durable Gmail 답장 초안 흐름의 Mail 검색 -> LLM -> Gmail Draft -> Mail Acknowledge 순서 보존
- Mail send capability나 arbitrary HTTP fallback을 생성하지 않음
- base graph hash, expected workflow `updated_at`, completion context 생성
- complete candidate graph validation과 canonical `expected_result_graph_hash` 계산

출력은 graph snapshot이 아니라 server-loaded base에 대한 GraphMutation이다. Full operations는 API response mapper에 전달하고 persistence에는 safe envelope만 전달한다. Builder는 database session, HTTP client, LLM client에 직접 의존하지 않는다.

### 2.8 ParameterTaskPlanner

목표 위치: `apps/gateway/application/agent_builder/parameter_tasks.py`

책임:

- mutation result node와 catalog parameter schema 대조
- 사용자 명시값, 기존 node 값, 단일 upstream 값, 안전한 catalog 기본값 순서로 parameter materialization
- Quick-to-guided 전환에서 persisted safe descriptor의 `step_id`/`parameter_key`에 해당하는 사용자 명시값은 materialize하지 않고 `resolution_source=null`, `reconfirmation_required=true` task로 생성. 처리 replica 메모리나 raw prompt에서 값을 복원하지 않음
- 모든 configurable parameter에 task record 생성
- materialization된 parameter는 graph와 `resolution_source=user_request|existing_graph|upstream_selector|catalog_default`에 기록한다. 일반 안전 추천은 structural acknowledgement 뒤 `completed`로 표시할 수 있지만, 새 LLM node의 Catalog 기본 `auto_model_routing=false`는 사용자 확인 전까지 `pending|active` task로 유지한다.
- required/optional task 생성
- planner guidance hint의 step/key를 Catalog로 검증하고 unknown/mismatched/secret-like hint를 폐기
- 검증된 `reason`/`input_guidance`와 catalog 설명을 안전하게 합성하며 hint 부재 시 catalog 설명으로 fallback
- dependency 순서와 node 내부 stable order 계산
- task version, defer policy, 명시적 `skipped` 상태와 state transition 검증
- LLM의 세 prompt가 모두 비어 있으면 세 task를 pending 순서로 계획하고 마지막 빈 prompt skip을 거부
- 현재 request의 모든 유효한 non-reverted v3 envelope affected node와 기존 task node를 canonical graph 범위에서 합쳐 미완료 affected LLM task를 복구
- ParameterTask `이전 항목`의 `next_task_id` presentation cursor와 완료 boundary에서 `completed|skipped|deferred` 중 최대 `stable_order` task 재진입 표시를 계산. Persisted task 상태와 Workflow history를 변경하지 않음

planner LLM을 호출하지 않는다.

### 2.9 ParameterSuggestionResolver

목표 위치: `apps/gateway/application/agent_builder/parameter_suggestions.py`

책임:

- graph reachability와 branch context 계산
- upstream output contract와 current input type 비교
- opaque suggestion id, canonical `[source_node_id, output_key, ...nested_path]`, 표시용 JSON path, value type과 safe label/description을 포함한 variable selector/resource reference suggestion 생성
- deterministic ranking과 deduplication
- LLM selector를 다시 저장할 때 canonical `referenced_variables[].name`을 보존하고 신규 selector에는 Catalog output key를 부여하며 중복 runtime 이름을 거부

runtime output과 catalog output이 다르면 추천을 만들지 않고 contract validation issue를 반환한다.

### 2.10 KnowledgeTimingResolver

목표 위치: `apps/gateway/application/agent_builder/knowledge_timing.py`

책임:

- Knowledge 선택이 graph topology에 미치는 영향 판정
- `before_graph` 또는 `after_graph` 확정
- selected candidate permission/runtime eligibility 재검증
- typed placement의 requirement/step/effect/bridge reference 검증
- selected candidate를 같은 requirement binding 목록에 연결하고 Catalog template으로 Knowledge node/data/edge 구성
- empty `before_graph` selection을 bridge policy에 따라 KB-free base topology로 결정적으로 변환하고 Knowledge 의존 node/data/edge를 생략

Knowledge ranking 자체는 Knowledge Recommendation Adapter에 위임한다.

### 2.11 AgentBuilderPolicyService

목표 위치: `apps/gateway/application/agent_builder/policy.py`

책임:

- workflow/app/Knowledge/model/credential reference 권한 확인
- Final candidate graph와 Catalog schema/reference policy registry에서 모든 resource-bearing field를 추출해 `managed_reference|legacy_editor_connection|unknown`으로 분류. Managed reference는 resource kind별 server-owned resolver로 current organization, lifecycle, relation과 required permission을 재검증한다. Resolver 미구현 Slack/GitHub 연결은 Agent Builder GraphMutation에서 base graph와 connection-relevant 값이 같은 불변 carry-forward만 허용한다. Slack/GitHub secret의 raw 값은 GraphMutation에 넣지 않고 masked control에서 기존 Workflow editor save bridge로 추가·교체한다. Unknown field는 차단한다.
- generation 중 side effect 금지
- secret input boundary
- Catalog required configuration 전체에서 `configuration_state`를 생성, set/defer/skip, Undo, 복구와 실행·배포 preflight마다 재계산
- unresolved configuration의 server-side 실행·배포 preflight 연결

API endpoint와 graph builder가 permission query를 직접 작성하지 않는다.

### 2.12 AgentBuilderPersistenceAdapter

목표 위치: `apps/gateway/adapters/db/agent_builder_repository.py`

책임:

- 기존 session/request row 조회, session protocol 판별과 request row lock
- 신규 session에 `AgentBuilderSession.protocol_version=direct_edit_v1` 저장. Null legacy session은 `stale_protocol`로 분류하고 backfill하지 않음
- `AgentBuilderRequest.response_payload`에 monotonic request/proposal version, canonical/requested/effective generation mode, mode transition/proposal의 safe 상태, operations를 제외한 safe operation envelope, acknowledgement와 parameter task safe metadata/version 저장. Mode transition에는 값 독립 structured plan과 재입력 대상 `step_id`/`parameter_key`만 저장하고 실제 parameter 값은 저장하지 않음. Current payload를 복사한 새 전체 객체를 column에 재할당하고 nested dict를 제자리 변경하지 않음
- 새 mutation과 복구 metadata의 `catalog_version` 저장 및 누락·`2`·`3` version gate 적용
- latest request와 mutation의 관계 보장
- request row lock, operation id idempotency, expected task version과 action별 active/completed 허용 상태 동시성 검사
- `AgentBuilderRequest`를 mode transition, safe envelope, ParameterTask, Knowledge resolution, quick-completion proposal와 idempotency result의 aggregate root로 취급하고 terminal parent 아래 pending child가 남지 않도록 같은 transaction에서 child 상태/version을 닫음
- session row lock으로 foreground request 단일성을 보장한다. `parameter_configuration` request는 비차단 open 상태로 request별 card/history를 유지하며 새 foreground request와 공존할 수 있다. Message 응답 전 session-scoped cancel operation id와 canceled request 결과를 멱등 저장하고 같은 cancel 재시도는 current `planning` request를 찾기 전에 terminal row의 persisted result를 조회
- Remaining quick proposal 생성 시 `Workflow -> AgentBuilderRequest` lock 아래 graph revision, request/task version을 확인하고 값 없이 task id/version/fingerprint와 graph hash/`updated_at`만 저장한다. Pending proposal target task의 competing decision을 차단하고 acknowledge는 같은 lock 순서로 권위 graph를 재검증한다. Acknowledge/cancel/stale에서 예약을 해제하고 acknowledge만 task version을 다시 증가시켜 completed 처리
- acknowledgement 뒤 completion context에 연결된 task/Knowledge resolution 전환
- CAS 저장 전 full operations 응답 유실은 initial/graph-edit/replace request를 먼저 cancel한 뒤 재생성하거나 현재 parameter task를 새 operation id로 재입력하는 방식으로 닫고, 저장 뒤 acknowledgement 유실만 canonical graph와 expected/saved hash로 복구
- completed history boundary의 전체 persisted revert 뒤 boundary를 `reverted`로 전환하고 모든 parameter group/Knowledge resolution을 `canceled`로 닫음. Parameter/Knowledge operation별 revert는 거부함

저장 금지:

- raw secret
- credential config
- raw KB content
- provider raw response

### 2.13 WorkflowDraftCASService

기존 위치: `apps/gateway/services/workflow_service.py`의 draft save use case를 additive하게 확장한다.

책임:

- Agent Builder `mutation_context` 필수 검증
- workflow row write lock과 active organization/write 권한 재확인
- Agent Builder save와 request cancel에서 `Workflow` row 다음 parent request row의 공통 lock order와 version/status 재조회 적용
- AgentBuilderPolicyService가 final candidate graph의 모든 resource-bearing field를 분류한다. Server-owned registry는 field path·mutation kind·resource kind별 resolver, relation과 최소 permission action을 지정하고 resolver는 같은 transaction 안에서 current organization, lifecycle, relation과 그 action만 확인한다. Workflow 저장은 workflow `write`, Knowledge/Collection과 managed credential binding에는 대상 `use`를 요구하며 단순 reference에 대상 `read|write`를 일괄 요구하지 않는다. Policy/resolver 누락은 fail-closed한다. Resolver 미구현 Slack/GitHub 연결은 Agent Builder GraphMutation에서 persisted base와 canonical field/connection-relevant data가 같은 carry-forward만 허용한다. Slack/GitHub secret은 safe task와 masked control로 입력하되 raw 값은 GraphMutation이 아니라 기존 Workflow editor save bridge로만 추가·교체한다. Unknown field는 차단하고 Client reference inventory와 발급 시점 allow 결과는 사용하지 않는다.
- current canonical graph hash와 workflow `updated_at` compare-and-swap
- request graph hash와 persisted safe envelope의 `expected_result_graph_hash` 일치 확인. Full operations는 DB에서 재생하지 않음
- catalog/schema/connection validation 재실행
- graph save 또는 persisted revert와 기존 `add_action_audit` insert를 같은 SQLAlchemy session/transaction에서 commit
- canonical persisted graph hash와 `updated_at` 반환
- revert에서 current graph가 원 result hash인지, candidate가 원 base hash인지 검증

일반 editor의 mutation context 없는 save 호환성은 유지한다. Agent Builder acknowledgement는 이 CAS save 결과만 사용할 수 있다.

### 2.14 IntentUsageRecorder

위치:

- 순수 계약: `apps/gateway/application/agent_builder/intent_usage.py`
- 영속 구현: `apps/gateway/services/agent_builder/intent_usage_service.py`
- 조립: `apps/gateway/composition/agent_builder.py`

책임:

- intent runtime이 실제 사용할 model DB ID와 credential DB ID로 provider 호출 전 pending 사용량 행과 가격을 예약
- 최초 planner와 repair를 request의 attempt 1, 2로 분리
- pending 예약 전에 request/session/App primary workflow와 model/credential 관계 확인. 과거 workflow 이력은 유지하되 non-primary scope의 신규 호출은 차단
- provider 응답 직후 content/schema/semantic validation 전에 token·latency로 예약된 같은 행을 success로 완료. Raw content/choices/provider 응답 전체는 normalizer와 recorder에 전달하지 않음
- 예약에서 model/credential/가격을 고정하고 완료 저장 재시도에서 현재 가격이나 runtime을 다시 조회하지 않음
- 별도 SQLAlchemy session/transaction과 PostgreSQL partial unique key로 중복 provider 호출과 중복 완료 방지
- 호출 실패 또는 usage 누락은 pending 행을 삭제하고, 호출 중 model/credential 삭제로 FK가 NULL이 되어도 예약된 행의 나머지 fact가 같으면 완료
- pending Agent Builder 행은 비용·token·호출 수·Top Model 집계에서 제외
- 기존 organization/workflow 비용, workflow budget과 App operation metric이 합산할 `llm_usage_logs` row 생성
- 저장 성공을 확인할 수 없을 때 provider 재호출 없이 안전 오류 반환

저장 금지:

- 사용자 message와 workflow context
- system prompt와 provider response content
- credential config, API key, token과 exception 원문

모델 또는 credential 삭제 뒤에는 usage row의 연결 ID만 NULL이 될 수 있다. 당시 token/cost와 user/organization/workflow 귀속은 보존한다. Agent Builder 전용 endpoint/대시보드와 Workflow Engine usage 재설계는 이 component 범위가 아니며, 기존 관리 화면과 워크플로우 화면의 workflow별 비용 영역에 additive 비용 구분만 제공한다. 워크플로우 화면의 page-level 비용·추세·위험 요약은 ADR-0060에 따라 표시하지 않는다.

## 3. Shared Schemas

기존 `apps/shared/schemas/agent_builder.py`에 canonical GenerationMode, ModeTransition, QuickReview/QuickCompletionProposal과 GraphMutation schema를, `apps/shared/schemas/workflow.py`에 additive `mutation_context`와 canonical save response schema를 둔다. 기존 `configure_and_generate`는 transport parsing compatibility에서 허용하고 domain/application result는 canonical mode만 사용한다. API adapter는 negotiated mode contract에 따라 legacy/canonical response representation을 선택한다. Request/response schema는 framework-independent Pydantic contract로 유지하고 node별 parameter form schema를 추가하지 않는다.

Concrete dependency 조립은 `apps/gateway/composition/agent_builder.py`가 담당한다. Agent Builder 별도 server, RPC, queue, 독립 DB는 이번 구조에 포함하지 않는다.

## 4. Frontend Components

### 4.1 AgentBuilderLauncher

책임:

- panel open/minimize/close
- 현재 workflow context와 launcher availability 표시

### 4.2 AgentBuilderPanel

목표 책임:

- conversation timeline composition
- generation mode와 intent model selection
- request submit/cancel. Composer submit은 foreground request만 차단하고 기존 `parameter_configuration` result/card를 유지한다. Cancellation-only path는 정확한 원 소유자에게 권한 회수 뒤에도 safe terminalization만 허용하며 graph 읽기·수정은 허용하지 않는다
- message response 전 `submitting|planning`에서는 session-scoped active-request cancel, request id가 확인된 뒤에는 request-scoped cancel 사용. 두 경로는 같은 backend cancel command와 terminal 결과를 소비
- Full operations 유실 뒤 재생성 action은 기존 request의 terminal cancel 결과를 먼저 확인하고 새 message를 제출함. Cancel 결과 확인 중에는 submit control을 열지 않음
- result group과 오류 상태 연결
- mobile viewport에서는 좌우 여백 안의 전체 너비를 사용한다. desktop viewport에서는 `clamp(360px, 50vw, calc(100vw - 40px))`로 시작하고 왼쪽 resize handle의 pointer drag 또는 방향키로 같은 최소·최대 범위 안에서 너비를 조절한다. 조절값은 component session 동안 유지하고 viewport가 줄면 다시 화면 안으로 clamp한다. Focusable separator는 현재·최소·최대 픽셀 너비와 읽기 쉬운 픽셀 문구를 ARIA value 속성으로 노출한다. 고정 최대 높이를 두지 않고 viewport 기준 높이를 사용해 panel 상단이 editor 상단 영역까지 확장된다. Launcher는 하단 Flow 설정 island와 같은 높이의 bottom control row에 배치한다.
- 고정 배치 wrapper 자체는 pointer event를 받지 않고 실제 panel과 launcher만 받는다. 따라서 panel open/minimize/close 상태와 무관하게 wrapper의 투명 영역 아래 React Flow canvas와 하단 control island가 클릭 가능해야 한다.

금지:

- graph hashing
- node별 parameter form switch
- graph mutation과 Undo stack 직접 조작
- Knowledge ranking 로직

현재 단일 파일에 있는 session 복구, clarification form, graph apply 로직은 hook/component로 분리한다.

### 4.3 GenerationModeControl

segmented control로 `단계별 생성`, `빠른 생성`, `구조만 생성`을 제공하고 기본값은 `단계별 생성`이다. Client는 초기값이면 `generation_mode_source=default`, 사용자가 control을 조작하면 `explicit_control`을 전송한다. `canonical-v2`에서는 default guided가 자연어의 명시적인 "한 번에" 요청을 막지 않으며 explicit control 선택은 자연어 intent보다 우선한다. `legacy-v1`에서는 빠른 생성 control과 자연어 quick 전환을 노출하지 않는다. `구조만 생성`은 고급 option menu로 둘 수 있으나 keyboard와 screen reader로 같은 값에 접근할 수 있어야 한다. Request 진행 중에는 새 request mode를 변경할 수 없고, quick eligibility 실패는 별도 전환 확인으로 처리한다.

### 4.3.1 ModeTransitionPrompt

- `mode_transition_required`에서 allowlisted 사용자 설명, `단계별 생성으로 계속`과 `취소`를 표시한다.
- Hidden resource 이름, 정확한 후보 수, credential 상태 원문과 내부 reason enum을 표시하지 않는다.
- 확인은 server-issued transition id, client operation id와 expected request version을 사용하며 자동 제출하지 않는다.
- 확인 뒤 값 독립 structured plan으로 guided 결과를 표시하고 기존 사용자 prompt를 다시 전송하지 않는다.
- `reconfirmation_required` descriptor가 있으면 실제 값을 미리 채우지 않은 typed ParameterTask를 표시하고 다시 입력해야 한다는 safe 안내를 제공한다. Client memory에 최초 값이 남아 있어도 자동 제출하지 않는다.

### 4.3.2 QuickReviewPresenter

- Initial quick proposal과 remaining-configuration proposal을 구분해 추가·변경·삭제 node, 자동 완료 설정 수와 단계별로 남는 설정 수를 안전하게 요약한다.
- `생성 적용` 전 actual editor graph를 변경하지 않고 AgentBuilderEditorAdapter의 clone dry-run 결과만 표시한다.
- `생성 적용`, `취소`를 명시적 command로 제공하고 loading/error 중 중복 제출을 막는다.
- Initial quick review 취소는 request cancel, remaining quick review 취소는 proposal cancel을 호출해 guided request/task를 유지한다.
- Legacy Preview component, preview session 또는 preview 전용 API를 import하거나 재사용하지 않는다.

### 4.4 WorkflowResultGroup

한 user request의 결과를 묶는다. 현재 결과 container는 가장 최근 terminal assistant response의 `request_id`에만 연결한다. 새 요청이 planning 또는 terminal `configuration_required|failed|unsupported|validation_failed`가 되면 이전 request의 ParameterTask, Knowledge card, 완료 상태와 routing 안내를 현재 결과처럼 재사용하지 않는다. `configuration_required`는 intent model/credential route 선택 action을 표시하고 같은 request를 비종료 상태처럼 재개하지 않는다. 이전 대화 항목은 읽기 전용 이력으로 유지한다.

- request 요약
- graph 적용 상태
- graph 전후 Knowledge 선택 상태
- 전체 node 설정 진행률
- node parameter card 목록
- 자동 추천값 확인 상태
- guided 진행 중 `남은 설정 빠르게 완료` command와 그 검토 결과
- 명시적인 취소/생성 완료 상태

같은 Knowledge requirement의 legacy clarification과 direct-edit Knowledge card를 동시에 렌더링하지 않는다. `before_graph`, `after_graph`, 자동 추천 완료 요약/수정과 수동 입력은 이 container의 한 순차 흐름으로 표시한다.

card 안에 또 다른 decorative card를 중첩하지 않는다.

WorkflowResultGroup은 `설정하며 생성`에서 backend가 graph topology로 부여한 `stable_order`에 따라 node card를 순차 표시한다. affected LLM card에는 모델, 출력 형식, 세 prompt, 이전 node 출력 연결, 인용 표시와 `auto_model_routing`만 기본 설정으로 표시한다. 인용 표시는 `hidden|basic|detailed` select로 제공하며 신규 Agent Builder LLM의 `detailed`는 수정 가능한 완료 추천으로 표시한다. 필드가 없는 기존 LLM에는 `detailed`를 자동 주입하지 않는다. Text 출력에서는 JSON Schema를 숨기고 JSON 출력에서만 선택 입력으로 펼친다. JSON Schema는 빈 값으로 skip할 수 있지만 입력값은 JSON object여야 하며 검증 실패 시 form 값을 유지한다. 세 prompt가 모두 비어 있으면 세 task를 순차 표시하고 마지막 prompt는 다른 prompt가 유효하게 설정되기 전까지 건너뛸 수 없다. 일반 `model_id`는 permission-filtered resource select로 표시하고, `task_group=model_routing`에는 `auto_model_routing` checkbox만 표시한다. Catalog 기본 추천이 `false`이면 미체크 active 확인 항목으로 펼쳐 표시하고, 확인 전에는 완료 상태와 고급 Routing action을 열지 않는다. Fallback model, refresh interval, validation budget와 maximum cohort control은 Agent Builder 카드에 만들지 않는다. 현재 result group에 Routing task가 있으면 별도 Routing 안내를 중복 표시하지 않는다. `auto_model_routing` task와 전체 workflow 설정이 완료되면 기존 fullscreen LLM Routing control을 여는 `고급 Routing 설정` action을 표시한다. `구조만 생성`처럼 task가 없는 결과에서만 `auto_model_routing=true`가 아닌 affected LLM을 하나의 안내로 묶고, `Routing 설정으로 이동`은 대상 node focus 뒤 fullscreen Routing control을 연다. 두 navigation 모두 GraphMutation, workflow save, policy API 또는 planner 호출을 만들지 않는다. 설정 card가 active여도 새 자연어 요청 composer는 사용할 수 있고 pending request 또는 CAS 저장 중에만 잠근다.

### 4.5 NodeParameterCard

책임:

- node 이름, purpose, configuration status 표시
- active/completed/deferred 상태
- 현재 task의 `ParameterInputRenderer` 표시
- node focus command 발생

자동 추천값 task는 resolution source와 canonical graph의 현재 값을 사용해 structural acknowledgement 뒤 completed로 표시하고 기본 접힘 상태로 둔다. 단, LLM의 `auto_model_routing=false` Catalog 추천은 `confirmation_required=true`인 active 확인 task로 펼쳐 미체크 checkbox와 `자동 추천 · 확인 필요`를 표시한다. 이 task에는 건너뛰기를 제공하지 않는다. 사용자가 그대로 제출하면 `confirm`, 체크해 제출하면 `set`을 사용한다. 그 밖의 완료 추천은 `수정`을 열어 기존값과 허용된 다른 후보를 확인하고 값이 달라지면 `set`을 제출한다. Optional control이 처음부터 비어 있으면 `skip`으로 완료하되, canonical graph에서 hydrate한 기존 optional 값을 사용자가 비워 적용하면 `clear`를 제출해 CAS/acknowledgement 뒤 실제 값을 제거한다. Optional selector list도 이 상태 구분을 그대로 사용하므로 신규 빈 제출은 `skip`, 기존 선택 전체 해제는 `clear`다. Completed task와 사용자가 건너뛴 skipped task는 요약 card로 남긴다. Active card 하나만 자동 확장한다. 일반 active task와 완료 boundary Undo로 다시 연 presentation reentry는 별도 result-group UI 상태로 구분하며 `presentationTaskId` 일치만으로 reentry를 추론하지 않는다. Slack/GitHub는 direct-edit `credential_ref` task와 빈 후보 picker를 만들지 않고 mode에 맞는 token/URL secret task와 masked control을 표시한다. Existing raw secret은 복구하거나 표시하지 않는다.

### 4.6 ParameterInputRenderer

catalog-derived `input_type`을 공통 control로 변환한다.

| Input type | Control |
|---|---|
| text | text input |
| textarea | textarea |
| code | code textarea |
| json | JSON textarea |
| secret | 기존 값은 표시하지 않는 password input. 새 입력은 editor draft save bridge로 전달하고 raw ParameterDecision은 발급하지 않음 |
| select | catalog option select |
| number | numeric input/stepper |
| boolean | checkbox 또는 toggle |
| select | menu |
| resource_ref | searchable resource picker |
| credential_ref | durable credential resource/use resolver가 있는 provider의 permission-filtered picker. Node runtime provider/auth compatibility를 함께 적용하며 Gmail Draft는 Gmail OAuth2만 노출한다. Slack/GitHub는 direct-edit `credential_ref` task 범위에서 제외한다. |
| variable_selector | server-issued upstream output suggestion picker. Source node/output key/JSON path/value type을 함께 표시 |
| variable_selector_list | server-issued upstream output suggestion checkbox list. 하나 이상 선택하며 각 source node/output key/JSON path/value type을 표시 |

node type 분기는 일반적으로 허용하지 않되 Catalog의 값 계약이 더 좁은 LLM JSON Schema는 JSON object 전용 검증을 적용한다. Catalog parameter의 `validation.visible_when`과 `required_when`은 같은 node의 canonical parameter 값을 기준으로 task 표시, 필수 여부와 순차 활성화를 제어한다. Slack mode와 GitHub action은 task의 표시·필수 조건을 제어하고 mode 전환 materializer는 반대 mode의 기존 값을 제거한다. Slack/GitHub secret task는 현재 mode에서만 masked input으로 렌더링한다. 자동 추천값의 active task에는 `confirm`, Optional task에는 `skip`, 재편집 가능한 `active|completed|skipped|deferred|invalid` task에는 `set`을 제공하며 `previous`는 이전 재편집 가능 task가 있을 때 제공한다. Optional text/JSON control에서 빈 `적용`은 같은 `skip` action을 사용하되 세 prompt 중 마지막 남은 빈 task는 server가 거부한다. `defer`는 Catalog가 `allow_unresolved`로 선언한 task에만 제공하고 생략되었거나 `forbidden`이면 렌더링하지 않는다. Required task의 `skip|clear`는 숨기거나 disabled로 표시하고 Backend도 거부한다. Confirm과 skip은 graph 저장 없이 각각 명시적 `completed`, `skipped` 상태를 표시한다. 재진입 control은 canonical workflow graph의 safe current value로 초기화하고 task/session에 값을 복제하지 않는다. Secret control은 예외적으로 canonical raw value를 hydrate하지 않는다. Runtime canonical graph가 GitHub PR 번호를 10진 문자열로 저장하면 number control은 안전하게 integer로 hydrate한다. LLM selector는 canonical name을 보존하고 File Extraction의 named selector object는 대응하는 selector suggestion으로 hydrate하며 Slack JSON 문자열은 JSON control에서 편집 가능한 값으로 변환한다.

Condition branch `select`는 `validation.option_labels`의 안전한 node label과 `연결 안 함`을 표시하고 제출에는 대응하는 canonical node id/sentinel을 사용한다. 초기값은 node data의 server-owned branch target map에서 hydrate한다. Dynamic `cases` acknowledgement 뒤 추가된 branch task는 같은 node card의 순차 task로 합쳐지고 제거된 branch task는 canceled summary로 남는다.

### 4.7 ParameterTaskController

목표 위치: `apps/client/app/features/workflow/components/agentBuilder/useParameterTasks.ts`

책임:

- active task 계산
- confirm/set/defer/skip/previous API 호출
- client operation id와 expected task version 전송, pending 제출 중 control 중복 실행 차단
- input type과 decision discriminator 일치 검증 및 server-issued selector suggestion id/value_selector 전달
- reference control 재진입 시 canonical graph 값과 candidate `candidate_id|reference_value`를 비교하고 decision에는 candidate id만 제출
- optional/required 정책에 따른 skip control 상태 관리
- optional skip을 GraphMutation 없이 `skipped`로 전환하고 skipped task 재설정을 일반 set/CAS 흐름으로 전달
- 기존 session의 값이 바뀌지 않은 active 자동 추천 confirm을 GraphMutation/save 없이 완료하고 중복 응답을 멱등 reconcile
- optimistic state와 server response reconcile
- parameter update GraphMutation을 editor adapter에 전달
- GraphMutation의 CAS workflow save/acknowledgement가 끝날 때까지 현재 task를 유지하고 control을 중복 제출할 수 없게 함
- acknowledgement 성공 뒤 완료 task와 next task를 reconcile
- completed group 복구에서도 현재 Catalog와 `node_id + parameter_key`를 병합하고, 기존 task 상태/version을 보존한 채 신규 입력 task만 순차 활성화
- `409 task_conflict`에서 server current task를 다시 읽고 제출값을 자동 재적용하지 않음
- Pending quick-completion proposal이 예약한 task는 편집 control을 잠그고 proposal acknowledge/cancel/stale 뒤 canonical task version을 다시 읽음. 다른 tab의 예약 task decision conflict도 자동 재적용하지 않음

### 4.8 KnowledgeSelectionControl

책임:

- 0개 이상 후보 선택
- 후보 목록과 선택 상태 분리
- WorkflowResultGroup 안에서 `before_graph` blocking 상태와 `after_graph` binding 상태를 순차 표현
- 같은 requirement의 legacy clarification control이 별도 표시되지 않도록 direct-edit ownership 적용
- `direct_edit_v1` 제품 선택 UI는 `knowledge_resolution.collections`와 `knowledge_resolution.ungrouped_kbs`만 렌더링한다. Flat `candidates`는 과거 응답의 안전한 읽기 호환 데이터로만 유지하며 `clarification_options`와 함께 후보 fallback으로 해석하지 않는다.
- `llmNode.knowledgeBases`는 일반 ParameterTask card로 렌더링하지 않으며 KB 후보와 empty selection은 통합 Knowledge card에서만 제공
- 기존 최대 표시 높이 3개와 최대 20개 scroll 정책을 회귀 없이 유지
- 추천 점수 내림차순과 안정 tie-break 순서를 안내하고 순위를 표시하되 raw 내부 signal과 candidate id는 노출하지 않음

미선택은 KB 없이 생성하겠다는 empty selection이며 별도 가짜 KB candidate나 재질문을 요구하지 않는다. `before_graph` empty selection은 자연어 요청 또는 planner 재호출 없이 backend가 KB-independent base topology를 사용한다.

### 4.9 AgentBuilderEditorAdapter

목표 위치: `apps/client/app/features/workflow/components/agentBuilder/useAgentBuilderEditor.ts`

책임:

- current workflow id, canonical graph hash와 workflow `updated_at`을 mutation base와 비교
- GraphMutation dry-run
- Quick mode에서는 actual editor와 분리된 cloned nodes/edges에만 dry-run하고 `생성 적용` 전 workflow store transaction을 만들지 않음
- mutation 전체를 workflow store의 atomic mutation API로 적용
- operation id 중복 방지
- affected node selection과 focus
- Agent Builder transaction 중 일반 autosync 일시 중지
- 모든 GraphMutation의 `mutation_context` 포함 CAS workflow draft 저장
- CAS save 응답 유실 시 같은 operation으로 idempotent save를 한 번 재시도하고, 재시도 응답도 유실되면 canonical workflow와 session의 matching `pending_ack|acknowledged|reverted` safe envelope로 반영됨, 미반영 또는 stale을 판정할 때까지 pending history를 유지
- server가 같은 operation을 `blocked/operation_payload_unavailable`로 확인한 경우에만 local mutation rollback. 결과가 불명확하면 canonical workflow를 다시 불러오고 일반 autosync로 우회 저장하지 않음
- canonical graph hash와 `updated_at` acknowledgement 전송
- acknowledgement 응답 유실 시 같은 operation id, graph hash와 `updated_at` payload로 한 번 재시도. 두 번째 응답도 유실되면 session recovery에서 같은 operation의 acknowledged 상태와 canonical hash/timestamp를 확인해 local boundary를 reconcile하고 persisted local graph를 rollback하지 않음
- Knowledge 선택 결과가 불명확하면 canonical session의 안전한 `messages`와 `knowledge_resolution`을 conversation/result state에 upsert한다. `unapplied`는 기존 card와 선택값을 유지해 다시 활성화하고, `pending_ack`는 잠근 채 reconciliation하며, `completed`는 stale clarification card를 닫고 다음 설정 단계로 이동한다. Planner, 자연어 요청과 유실된 typed operations는 재실행하지 않음
- server layout position은 graph에 적용하되 화면 맞춤과 node focus는 viewport만 변경하고 canonical graph/hash를 다시 만들지 않음
- 최초 `initial_graph`/`graph_edit`/`replace_workflow`에서 시작 전 snapshot과 최종 graph를 묶는 Agent Builder history boundary 하나를 생성
- 후속 `parameter_update`/`knowledge_binding`은 별도 Workflow history entry 없이 boundary final snapshot/hash만 acknowledgement 결과로 갱신
- 완료 상태 첫 Undo는 최대 `stable_order`의 재편집 가능 ParameterTask를 client presentation에서 표시하고 panel을 열어 최소화를 해제한다. 재진입 상태 다음 Undo는 boundary 시작 snapshot CAS revert와 전체 task/Knowledge cancel로 처리하며 Task가 없으면 첫 Undo가 즉시 전체 revert
- Parameter 카드의 `이전 항목`은 backend `next_task_id`를 presentation cursor로 소비하고 Workflow history와 persisted task 상태를 사용하지 않는다. Input/card/button key event는 canvas Undo/Redo로 전파하지 않음
- 전체 revert 뒤 Redo는 memory의 final graph를 CAS 저장하되 canceled task/Knowledge 흐름을 재실행하지 않음. 전체 Redo 뒤 다음 Undo는 parameter 재진입 없이 즉시 boundary revert하며, 재진입 상태 Redo는 UI만 닫고 reload 뒤 Redo stack은 복구하지 않음

최초 생성, 부분 구조 변경, 전체 workflow 교체, parameter update와 Knowledge binding은 같은 `GraphMutation` view model과 dispatcher를 사용한다. 전체 교체 전 graph는 history boundary의 시작 snapshot으로 보존하며 parameter/Knowledge acknowledgement가 끝날 때마다 final snapshot을 갱신한다.

React Flow API와 workflow store를 아는 유일한 Agent Builder 모듈이다.

### 4.10 NodeFocusController

- active 또는 presentation `task_id`가 바뀔 때 해당 node를 선택하고 Agent Builder panel을 제외한 가시 canvas 영역 중앙으로 이동한다.
- Node와 주요 handle이 panel에 가리지 않는 범위에서 가능한 가장 큰 zoom을 한 번 적용하고 해당 card를 보이게 scroll한다. 좁은 mobile viewport에서는 zoom을 낮추되 node 식별, handle과 panel control 접근성을 보장한다.
- 같은 task의 validation/candidate 갱신과 사용자의 수동 viewport 조작 뒤에는 반복 focus하지 않는다.
- 첫 Workflow Undo 직후 input으로 keyboard focus를 강제하지 않아 canvas Undo 문맥을 유지한다. `previous` 이동은 새 card heading으로 접근성 focus를 옮긴다.

## 5. Workflow Store Extension

기존 `setNodes`와 `setEdges`를 연속 호출하면 history가 둘로 나뉠 수 있다. 다음 atomic action을 추가한다.

```text
applyGraphTransaction(nextNodes, nextEdges, metadata)
```

계약:

- 호출 직전 nodes/edges를 undo stack에 한 번만 기록
- nodes, edges, active workflow mirror, unsaved flag를 한 번의 state update로 변경
- redo stack 초기화
- transaction id와 source(`agent_builder`)를 UI-only metadata로 기록 가능
- selection/viewport 변경은 graph history에 포함하지 않음

## 6. Data Flow

### 6.1 Guided Generate

1. 사용자가 mode와 intent model을 선택하고 message를 제출한다.
2. backend가 permission과 saved workflow context를 확정한다.
3. planner가 structured plan과 safe purpose hint를 만든다.
4. Knowledge timing이 `before_graph`면 통합 WorkflowResultGroup의 첫 단계에서 typed placement를 검증하고 selection을 완료한다.
5. TargetResolver와 GraphMutationBuilder가 validated mutation을 만든다.
6. ParameterTaskPlanner와 SuggestionResolver가 모든 configurable parameter의 task group을 graph topology 순서로 만든다. Condition은 case/default branch target task도 함께 만들고 기존 single downstream edge를 node 삭제 없이 explicit default edge로 명시화한다. 안전한 자동 추천값은 graph에 반영하고 completed task로 계획하되 최초 structural operation acknowledgement 전에는 group `pending_save|pending_ack`가 완료 표시와 상호작용을 차단한다. Acknowledgement 뒤 첫 미설정 pending task만 active로 전환한다.
7. frontend adapter가 mutation을 atomic transaction으로 local editor에 적용하고 상태를 `pending_save`로 바꾼다. Server graph와 CAS hash에서는 제외된 화면 전용 `displayNumber`는 이 시점에 editor가 누락된 node마다 다시 부여해 BaseNode의 연결 handle 번호가 유지되게 한다.
8. frontend가 expected base graph hash/`updated_at`을 포함한 CAS workflow draft save를 호출한다.
9. backend가 row lock, 권한, compare-and-swap과 graph validation 뒤 canonical graph hash/`updated_at`을 반환한다.
10. frontend가 canonical 저장 결과를 acknowledgement하고, backend는 그 뒤 같은 WorkflowResultGroup에서 `after_graph` Knowledge 또는 첫 parameter 확인 task를 활성화해 focus한다.
11. 각 typed decision은 현재 task를 유지한 `parameter_update` GraphMutation을 반환한다.
12. frontend가 mutation 적용, CAS 저장과 acknowledgement를 완료한다.
13. acknowledgement 성공 뒤에만 backend가 현재 task를 완료하고 다음 task를 활성화한다. Condition `cases`가 바뀌면 이 시점에 새/제거 handle task를 canonical graph와 재조정한다.
14. 모든 필수 Knowledge 선택과 task 확인이 끝나고 관련 save/acknowledgement가 완료되면 group 안에 명시적인 생성 완료 상태를 표시한다. 이 전에는 완료 history boundary를 활성화하지 않는다.

### 6.2 Quick Generate

1. 사용자가 `빠른 생성`을 선택하거나 자연어로 명시하고 message를 제출한다.
2. Application이 permission, saved workflow context와 requested mode를 확정하고 planner를 정상 한 번 호출한다.
3. QuickGenerationEligibilityPolicy가 Catalog, 현재 resource snapshot, graph/revision과 side-effect 분류를 fail-closed로 판정한다.
4. Ineligible이면 graph mutation을 만들지 않고 ModeTransitionPrompt에서 guided 전환 또는 취소를 기다린다. 전환 확인은 보존된 값 독립 structured plan과 safe reconfirmation descriptor를 사용하며 planner를 다시 호출하지 않는다. 최초 요청에서 읽은 실제 값은 typed task에서 다시 입력하기 전까지 graph에 넣지 않는다.
5. Eligible이면 GraphMutationBuilder가 공통 필수 CAS metadata와 full operations를 모두 가진 validated mutation과 safe summary를 만든다. RequestStatus는 `graph_mutation_ready`, nested GraphMutationStatus만 `pending_apply`다.
6. AgentBuilderEditorAdapter가 cloned graph에 dry-run하고 QuickReviewPresenter가 변경 요약을 표시한다.
7. 사용자가 `생성 적용`을 선택한 뒤에만 실제 editor history boundary에 mutation을 적용하고 CDS save/acknowledgement를 수행한다.
8. Acknowledgement가 끝난 뒤 request를 완료한다. 적용 전 응답 유실/reload는 full operations를 복원하지 않고 기존 request cancel의 terminal 결과를 확인한 뒤에만 새 request를 제출한다.

### 6.3 Guided Remaining Quick Completion

1. 사용자가 active guided result에서 `남은 설정 빠르게 완료`를 선택한다.
2. Application은 acknowledged graph와 completed/skipped/deferred task를 보존하고 미완료 task snapshot만 읽는다.
3. QuickGenerationEligibilityPolicy가 `remaining_configuration` scope로 각 task를 판정한다. Planner와 전체 GraphMutation planning은 다시 호출하지 않는다.
4. 안전한 task만 하나의 proposal로 묶는다. 생성 transaction은 `Workflow -> AgentBuilderRequest` 순서로 잠근 뒤 request와 각 confirm 대상 task version을 증가시켜 fenced id/version/fingerprint와 canonical graph hash/`updated_at`만 저장하고 pending proposal이 해당 task를 예약한다. 실제 parameter 값과 graph fragment는 저장하지 않는다. Credential, 권한 resource, 외부 부수효과, Condition branch와 복수 후보 task는 guided 상태로 남긴다.
5. Client는 confirmable task의 fenced version, remaining count와 safe summary를 검토하며 graph mutation dry-run이나 workflow save를 만들지 않는다. Pending proposal target task의 개별 편집은 잠근다.
6. 사용자가 적용하면 `Workflow -> AgentBuilderRequest` 순서로 잠그고 권위 graph를 다시 읽어 stored graph hash/`updated_at`, recommendation fingerprint와 fenced task version을 재검증한다. 값 변경 없는 confirm만 원자적으로 완료하고 `request_version`, `proposal_version`과 완료되는 각 task의 `task_version`을 다시 정확히 한 번 증가시켜 갱신된 task id/version/status를 응답과 멱등 결과에 포함한다.
7. 일부가 stale하거나 원자적으로 적용할 수 없으면 proposal을 terminal `stale`로 전환해 예약을 해제하고 기존 guided task 값과 graph를 유지한다.
8. 사용자가 proposal을 취소하면 request/proposal version을 같은 lock에서 각각 증가시키고 proposal만 canceled로 닫아 예약을 해제한다. 생성 시 증가한 task version은 되돌리지 않고 graph와 guided task 값을 그대로 유지한다.

### 6.4 Structure Only

1. 1~5단계는 동일하다.
2. frontend가 빈 workflow의 새 graph면 `initial_graph`, 기존 workflow 부분 변경이면 `graph_edit`, 전체 교체면 `replace_workflow` GraphMutation을 atomic transaction으로 적용한다. `generation_mode=structure_only`이므로 parameter group은 만들지 않는다.
3. frontend가 CAS workflow draft save와 canonical graph hash/`updated_at` acknowledgement를 완료한다.
4. acknowledgement 성공 뒤 operation을 완료로 표시하되 parameter group은 만들지 않고 unresolved issue 요약만 표시한다.
5. 사용자는 기존 Node Detail Panel에서 직접 설정하거나 Undo한다.

## 7. State Ownership

| State | Owner | Persistence |
|---|---|---|
| RequestStatus | backend | DB; foreground `planning|clarification_required|mode_transition_required|graph_mutation_ready`, 비차단 open `parameter_configuration`, terminal request lifecycle |
| GraphMutationStatus | backend | 기존 request safe operation envelope; `pending_apply|pending_save|pending_ack|acknowledged|blocked|reverted` |
| QuickCompletionProposalStatus/version/fenced task snapshot | backend | 기존 request `response_payload`; `pending|acknowledged|canceled|stale`, target task id/version/fingerprint만 저장하고 실제 값 제외 |
| structured plan | backend | safe metadata only |
| session protocol | backend | nullable `AgentBuilderSession.protocol_version`; null은 legacy, 신규는 `direct_edit_v1` |
| request-scoped mode contract, canonical/requested/effective generation mode와 source, monotonic request/proposal version, transition/proposal safe state, operation envelope/acknowledgement metadata | backend | 기존 `AgentBuilderRequest.response_payload`; raw header와 full operations 제외, contract 누락 row는 legacy로 읽고 새 전체 JSON 객체 재할당 |
| actual editor graph와 parameter 값 | workflow store/workflow draft | existing draft save; 재진입 input의 유일한 value source |
| node `configuration_state` | backend derived policy | Catalog required configuration에서 재계산해 graph에 materialize; client 입력은 비권위 |
| undo/redo graph history | workflow store | client memory |
| persisted Undo 상태 | backend/workflow draft | 원 operation의 `reverted` 상태와 canonical graph |
| parameter task status/version/resolution source/reconfirmation flag | backend | 기존 `AgentBuilderRequest.response_payload`; `skipped` 포함, 실제 parameter 값은 제외 |
| test/run/deploy readiness | backend preflight | 저장 graph와 Catalog에서 missing/deferred/invalid configuration을 매번 재계산; ParameterTask 상태는 비권위 |
| Slack payload readiness | backend Catalog/preflight | `message|blocks|attachments` 중 유효한 값 하나 이상을 요구하고 deferred/공백/빈 array/invalid JSON은 미설정으로 판정 |
| input draft before submit | frontend card | client memory |
| credential secret | Agent Builder가 소유하지 않음 | 기존 credential 경계 |
| viewport/focus | React Flow | client memory |

## 8. Failure Handling

- planner failure: graph를 변경하지 않고 safe failure 표시
- quick eligibility failure: graph를 변경하지 않고 `mode_transition_required`; 사용자 확인 없는 guided 전환 금지
- mode contract mismatch: 대상 request body를 다른 표현으로 projection하지 않고 safe required contract만 반환. 지원 Client는 같은 GET을 한 번 재시도하고 rollback/미지원 Client는 mode-free cancel 또는 운영 drain으로 종료
- quick review 취소: editor/workflow/request graph 상태를 변경하지 않고 request만 canceled 처리
- quick 또는 `continue_guided` mutation 발급 뒤 apply 전 reload/response loss: 같은 operation 재시도에서 operations를 복원하지 않고 `operation_payload_unavailable`과 safe 상태를 표시한 뒤 기존 request의 terminal cancel을 확인하고 명시적으로 신규 request를 제출함. Cancel 확인 전 submit 금지
- remaining quick-completion stale/conflict: proposal을 terminal `stale`로 닫아 target task 예약을 해제하고 기존 guided task 값/graph를 유지하며 자동 재적용하지 않음
- Knowledge before-graph unresolved: selection UI만 표시
- stale mutation: base graph hash나 expected workflow `updated_at`이 다르거나 `catalog_version`이 없거나 `2`이면 적용하지 않고 parent request terminal cancel 뒤 재생성 안내
- partial mutation failure: 전체 rollback, acknowledgement 미전송
- parameter validation failure: node graph 유지, task를 invalid로 표시
- parameter decision conflict: request row lock 뒤 먼저 commit한 decision만 유지하고 뒤 요청은 `409 task_conflict`; GraphMutation과 next task를 중복 생성하지 않음
- session recovery before save: full operations 응답이 유실되면 기존 envelope를 `blocked/operation_payload_unavailable`로 닫고 자동 재생하지 않는다. Initial/graph-edit/replace는 parent request의 terminal cancel 확인 뒤 request 재생성, parameter decision은 parent request를 유지한 현재 task/version의 새 operation id 재입력을 요구
- session recovery after save: acknowledgement만 유실됐으면 persisted graph와 expected/saved hash로 복구하고 operation을 재적용하지 않음
- local apply 후 CAS workflow save 실패: parameter task를 열지 않고 local unsaved 상태와 재시도 안내
- workflow save 후 acknowledgement 실패: canonical graph hash/`updated_at`과 operation id로 acknowledgement 재시도
- parameter 또는 `after_graph` Knowledge mutation 저장 실패: 현재 task/selection을 완료하지 않고 같은 operation을 재시도
- acknowledgement 후 reload: server workflow graph와 request task 상태를 함께 복구
- completed boundary Undo: ParameterTask가 있으면 첫 Undo는 graph/value와 persisted task 상태를 유지하고 최대 stable order task를 표시하는 UI 재진입이며, 다음 Undo는 current graph가 boundary final hash일 때만 시작 전 snapshot을 CAS 저장해 모든 task/Knowledge 흐름을 취소함
- task가 없는 completed boundary는 첫 Undo에서 즉시 전체 CAS revert함. Reload 뒤에는 parameter 재진입 표시와 Redo history를 복구하지 않음
- 전체 Undo 뒤 reload 전 Redo: memory의 final graph를 CAS 저장하고 canceled task/Knowledge 흐름은 재활성화하지 않음. 전체 Redo 뒤 다음 Undo는 즉시 boundary revert하며 재진입 상태 Redo는 UI만 닫음
- permission loss: 이후 task 변경을 차단하되 이미 생성된 local graph를 임의 삭제하지 않음
- request cancel: 모든 비종료 RequestStatus를 parent request lock에서 terminal `canceled`로 전환하기 전에 남은 task/Knowledge resolution, pending proposal와 저장 전 envelope를 같은 transaction에서 닫고 변경되는 task/proposal version을 증가시킴. Persisted graph와 완료 값은 유지하고 저장 전 local mutation만 Undo
- submitting/planning cancel: Client가 request id를 아직 받지 못하면 session-scoped cancel operation id를 사용한다. Backend는 terminal request를 포함한 persisted operation result를 `planning` request보다 먼저 조회하므로 같은 operation 재시도가 새 request나 기존 `parameter_configuration` request를 취소하지 않는다. 원 소유자는 workflow write/organization membership 회수 뒤에도 cancellation-only 결과만 받을 수 있고, 인증 불가·만료 drain은 내부 작업이 같은 state machine을 사용한다. In-flight provider usage는 완료할 수 있어도 다음 repair attempt와 늦은 planner commit은 차단
- intent usage attribution failure: terminal `failed`와 safe issue code로 표시하고 별도 RequestStatus를 만들거나 provider를 재호출하지 않음
- concurrent save: 첫 CAS save만 성공하고 뒤 요청은 stale 안내. 자동 merge하지 않음
- legacy Preview recovery: `stale_protocol`로 표시하고 이전 preview/draft 적용 금지

- frontend session recovery는 `stale_protocol`, server가 명시한 session not found 또는 invalid session만 local session key를 제거하고 새 `direct_edit_v1` session을 생성한다. Message 전송 중 stale 응답을 받은 경우에도 이 복구를 정확히 한 번만 수행하며, 새 session도 stale이면 반복 재시도하지 않고 세션 전환 실패를 안내한다. transport/5xx는 local session key와 대화/입력/Knowledge/history context를 보존한다.
- frontend 오류 표시는 allowlist 형식의 server error code와 HTTP status만 분류에 사용한다. `stale_protocol`, workflow context/CAS/task conflict, 4xx validation/permission, 502/503/504 availability, network failure를 구분해 안내하고 raw response detail은 toast나 대화 이력에 출력하지 않는다.

### 8.1 Unified Decision And Recovery UX

- Direct-edit Knowledge는 `knowledge_resolution.collections`와 `knowledge_resolution.ungrouped_kbs` 계층 데이터만 렌더링한다. Flat `candidates`만 남은 응답은 안전 오류로 차단하고 선택 목록과 제출 action을 만들지 않는다. 같은 requirement의 legacy clarification selector는 만들지 않으며 정상 계층 선택은 전용 endpoint만 호출한다.
- Candidate가 0개인 direct resolution도 상위 `resolution_id`로 설정 card를 렌더링하고 timing에 맞는 KB-free CTA를 제공한다.
- `before_graph` CTA는 Collection 또는 KB 선택 시 `선택한 Knowledge로 생성`, 빈 선택 시 `Knowledge Base 없이 생성`이다. `after_graph` CTA는 선택 시 `선택 적용`, 빈 선택 시 `Knowledge Base 없이 계속`이다.
- Collection/KB 후보는 권한 필터와 전체 점수 계산 뒤 점수 내림차순, safe label 오름차순(없는 label은 마지막), opaque handle 오름차순으로 표시한다. 표시 상한을 먼저 적용해 높은 점수 후보를 누락하지 않는다.
- Collection/KB checkbox state는 화면 위치가 아니라 opaque handle/`selection_key` 집합으로 관리하며 같은 KB가 여러 Collection에 나타나면 모든 위치가 동시에 변경된다. 제출 배열 순서는 의미가 없다.
- Collection과 고유 KB는 각각 최대 20개까지만 렌더링하며 목록 높이는 약 3개 행으로 고정하고 나머지는 내부 스크롤로 탐색한다.
- Knowledge 저장 중에는 card와 선택값을 유지하고 control만 잠근다. 성공 acknowledgement 뒤에만 선택 사용자 메시지를 확정하며, pre-save 실패는 같은 card에서 재시도한다.
- 자동 추천 출처는 `사용자 요청에서 확인`, `기존 Workflow 설정 사용`, `이전 노드 출력에서 연결`, `기본값 추천`으로 표시한다. 내부 enum, raw value와 secret은 렌더링하지 않는다.
- Knowledge 추천 사유도 allowlisted 사용자 문구로만 표시하며 알 수 없는 reason enum은 숨긴다.
- Quick ineligible `configuration_value_required`는 resource 존재나 후보 수를 언급하지 않고 `필수 설정값을 확인해야 합니다`로 표시한다.
- Slack/GitHub는 managed credential picker를 열지 않고 mode에 맞는 masked secret control을 표시한다. 기존 node graph secret은 Agent Builder persisted state나 input value로 가져오지 않으며 미설정 인증값은 server-derived unresolved 상태와 preflight 차단으로 표현한다.
- completed task 편집은 WorkflowResultGroup의 explicit editing state다. 이 상태에서는 생성 완료 표시를 숨기고 `설정 수정 중`을 표시하며, active task가 없어도 취소/닫기를 제공한다. 저장 실패 시 form 값을 보존하고 acknowledgement 성공 뒤 완료 상태로 돌아간다.
- 완료 boundary의 첫 Undo는 sensitivity나 node type과 무관하게 최대 `stable_order`의 `completed|skipped|deferred` task를 표시한다. Raw secret과 권한 없는 reference는 hydration하지 않는다.
- Canonical graph/session 복구는 결과를 판정하는 동안 workflow history boundary, pending operation과 redo memory를 지우지 않는 non-destructive sync를 사용한다. Acknowledgement 뒤 session 조회가 실패하면 `완료 확인 중`을 표시하고 `1초 -> 2초 -> 4초` 간격으로 최대 세 번 재조회하며 terminal 상태 전에는 완료 표시와 완료 Undo를 활성화하지 않는다. 세 번 모두 실패하면 `결과 확인 필요`와 `다시 확인` control을 표시한다. Undo/Redo 저장도 불명확하고 최종 canonical graph가 요청 graph와 반대 graph 모두 아닌 제3 상태로 확인되면 canonical graph/metadata를 editor에 반영하고 해당 workflow의 Agent Builder pending context와 Undo/Redo memory를 비운 뒤 clean stale 복구 안내로 종료한다.
- Pending request 조회가 성공하지만 같은 request가 계속 processing이면 60초까지 정상 planning 안내와 5초 간격 조회를 유지한다. 60초 이후에는 `평소보다 오래 걸리고 있습니다`를 표시하고 10초 간격으로 전환한다. Terminal 응답 수신 시 같은 request의 planning card를 교체하고 설정 상태를 failed 또는 후속 상태로 전환한다. 조회 중 transport/5xx가 발생하면 자동 조회를 중단하고 `결과 확인 필요`를 표시한다. 이 상태에서는 planning conversation card와 진행 banner를 숨기고 Workflow 결과 상태를 `서버 확인 대기`로 표시해 무한 처리 중처럼 보이지 않게 하며, 수동 `다시 확인`이 성공하면 canonical 상태 표시를 재개한다.
- Gateway가 4분 processing 기한을 넘긴 request를 terminal `failed`로 반환하면 client는 같은 `request_id`의 `planning` 대화와 `Workflow 계획 중` 상태를 실패 결과로 교체하고 입력을 다시 사용할 수 있게 한다. 자연어 요청을 자동 재제출하지 않는다.
- Parameter group 취소와 task decision은 결과가 확정될 때까지 같은 operation id를 유지한다. 동일 operation/payload retry는 사용자에게 중복 card/message를 만들지 않는다.
- Reload와 `stale_protocol`은 만료되지 않은 request의 safe redacted conversation 전체를 시간순으로 보여준다. `stale_protocol`은 이를 읽기 전용으로 유지하고 legacy preview/draft/apply 정보는 복원하지 않는다. 재제출 안내와 새 direct-edit session 하나를 사용하며 전환 loop를 만들지 않는다.

## 9. Migration

1. 기존 Condition/Variable clarification 결과를 characterization fixture로 먼저 고정한다. Preview 경로는 수정·확장하거나 활성 fallback으로 유지하지 않는다.
2. 단일 catalog v3 parameter/input/output schema, parser와 parity test를 원자적으로 추가하고 기존 `response_payload` JSON에 catalog version gate를 연결한다. 버전 누락·`2`는 미적용 legacy stale, `3`은 current 후보로 처리하며 catalog version은 별도 column이나 backfill을 추가하지 않는다.
3. MBA-228 단일 기능 PR에서 Gateway application/DB adapter/composition 경계, GraphMutation, WorkflowDraft CAS, generic ParameterTask와 nullable protocol migration/mixed read를 추가한다.
4. workflow store atomic transaction과 단일 GraphMutation dispatcher, frontend result group/card/typed input을 추가하고 legacy clarification과 결과 parity를 검증한 뒤 Preview 전용 component/API를 제거한다.
5. CAS 저장/acknowledgement/복구, Agent Builder history boundary persisted Undo/Redo, task 동시성, transaction-bound audit/redaction, unresolved 실행·배포 preflight를 구현하고 검증한다.
6. Alembic single head, disposable 기존 DB upgrade와 request 없는 null/direct mixed protocol recovery를 검증한다.
7. Frontend와 Gateway의 무중단 mixed-revision rollout, staged rollback, 배포 gate와 image artifact 검증은 별도 배포 작업으로 넘긴다.
8. Gateway는 두 mode 입력을 수용하고 내부 canonical로 정규화하되 header가 없거나 legacy인 Client에는 `configure_and_generate` 응답을 유지한다. Client는 두 응답을 읽되 먼저 legacy-write로 배포한다. 모든 Gateway replica와 Client dual-read gate가 확인된 뒤에만 `X-Agent-Builder-Mode-Contract: canonical-v2`와 canonical-write를 순서대로 활성화한다. 새 request에는 normalized contract를 고정하고 contract가 없는 기존 request는 legacy로 읽되 JSON을 backfill하지 않는다.
9. Quick endpoint, eligibility policy, clone dry-run, transition/remaining-completion integration과 E2E가 함께 준비되고 canonical-v2가 협상된 revision에서만 `빠른 생성` control을 노출한다. Rollback은 quick/canonical creation gate를 먼저 닫고 foreground 및 open configuration을 포함한 canonical-v2 nonterminal request를 완료·mode-free cancel해 0건임을 확인한 뒤 legacy-write로 전환할 수 있다. Session GET 보존 기간 내 terminal canonical-v2 request까지 포함한 retained-history aggregate가 0건이 되기 전에는 dual-contract Gateway와 Client dual-read를 제거하거나 legacy-only Client를 배포하지 않는다.
## 2026-07-15 Connection Navigation And Completion Correction

- WorkflowResultGroup does not render Slack/GitHub managed credential tasks, empty credential pickers, or raw secret values. It renders Catalog-declared Slack/GitHub `secret` tasks as empty masked controls and renders `나중에 설정` when their Catalog defer policy is `allow_unresolved`. Mail/Gmail continue to use the existing managed credential picker.
- Mail/Gmail retain their typed managed credential picker. Agent Builder never accepts or retains a raw credential value.
- The Mail search card is catalog-driven for every user-configurable search field: credential, keyword, sender, subject, date range, folder, result limit, unread/read handling, and processing mode. Safe generated template values and Gmail processing selectors are completed after structural acknowledgement and remain editable; runtime-only graph fields are not rendered as user tasks.
- Configure-and-generate renders Catalog routing tasks in the LLM parameter card and suppresses duplicate routing guidance. Structure-only keeps the `Routing 설정으로 이동` action, which opens the target LLM Routing control without creating a mutation or saving the graph.
- A safe recommended value becomes a completed collapsed task after structural acknowledgement even when it is already materialized in the graph. Opening edit hydrates the preselected value and alternate allowed choices; a changed value uses `set`.
- Only after every required Knowledge/task acknowledgement is terminal does the result show `Workflow 생성 완료`. Completed node cards collapse to a summary with an explicit edit action; opening edit hides completion and reopens that task.
- A failed Knowledge selection keeps its card and current choice visible, with a safe status-specific message and retry only through the dedicated selection endpoint.

## 2026-07-14 Composer And Knowledge Card Behavior

- Knowledge 후보가 둘 이상이면 선택 card는 상단부터 추천 점수 내림차순이라는 설명과 각 후보의 순위를 표시한다. 동점은 safe label 오름차순(없는 label은 마지막), opaque handle 오름차순으로 결정한다. Source tier와 availability는 이미 점수에 포함되므로 동점 규칙에서 다시 적용하지 않으며, UI는 raw score signal이나 내부 candidate ID를 노출하지 않는다.

- An after-graph Knowledge selection mutation materializes each selected KB as `{ id, name }`, so the workflow graph schema can persist it. If the subsequent draft save fails, the Knowledge card and its selected values remain available for retry.
- A recommended parameter is rendered collapsed as completed. When the user opens edit, its typed input hydrates the current value and keeps the other viable choices available.
- `LLM settings open` focuses the target node and opens the editor's node settings view. It does not create a GraphMutation, save the workflow, or call the planner.

- LLM 생성 결과의 Knowledge 선택은 Workflow 설정 결과 안의 단일 after-graph 카드로 표시한다. legacy clarification selector를 동시에 표시하지 않는다.
- 후보 목록에는 권한 있는 KB를 표시하며, 사용자는 복수 선택하거나 `Knowledge Base 없이 계속`을 누를 수 있다.
- Knowledge 카드와 ParameterTask 카드가 active인 `parameter_configuration` request는 비차단 open 상태이므로 composer와 Send control을 사용할 수 있다. 새 메시지는 기존 card/result를 request별 history에 유지한 채 별도 `planning` request를 만든다. Foreground request 또는 CAS 저장 중에만 disabled 상태가 되며, 과거 card action은 원 request/version과 최신 workflow graph revision을 재검증한다.
- mutation에 포함된 server layout 좌표를 editor가 적용한 뒤, 화면 맞춤은 viewport 동작만 수행한다. viewport 동작은 저장 graph를 변경하지 않는다.
- GraphMutation 저장 payload는 canonical base에 typed operations를 재생해 구성한다. React Flow node instance에 뒤늦게 붙는 `width`, `height`, `measured`는 화면 배치용으로만 유지하고 저장 payload를 다시 만드는 입력으로 사용하지 않는다.
### MBA-275 Validation Boundaries

- 일반 Parameter decision application은 Catalog parameter schema, sensitivity와 기존 fail-closed secret detector를 적용한 뒤에만 GraphMutationBuilder를 호출한다. detector 실패도 허용하지 않으며 값은 persistence/audit 경계에 도달하기 전에 폐기한다. Slack/GitHub `secret` task는 safe metadata와 masked control만 발급하며 조작된 raw ParameterDecision은 이 경계에서 거부한다.
- `parameter_tasks.validate_direct_set_value`는 순수 Catalog/type/sensitivity 판정을 담당하고, `ParameterTaskService`는 DB-backed reference와 Workflow/App relation을 검증한다. resource resolver는 canonical opaque id만 graph patch에 전달하며 raw config/secret을 application model에 넣지 않는다.
- `appId`는 WorkflowNode runtime target을 정하는 필수 reference이고 `workflowId`는 선택 metadata다. `appId` 직접 변경 시 service는 선택된 App과 canonical Workflow의 organization scope·양쪽 read 권한을 확인하고 `workflowId`를 정규화한다. `workflowId` 직접 변경과 이미 정합한 pair는 현재 node data와 patch의 합성 결과에서 `App.workflow_id == Workflow.id`를 강제한 뒤에만 task 완료와 graph patch를 원자적으로 확정한다.
- WorkflowDraftCASService는 `populate_existing().with_for_update()`에 해당하는 locked refresh 계약으로 최신 row를 비교하며 stale conflict 시 graph/audit/task를 쓰지 않는다.

## Hierarchical Knowledge Control

- `direct_edit_v1` 제품 화면은 `collections`와 `ungrouped_kbs`를 사용하는 계층형 UI와 전용 제출 callback만 사용한다. 계층 데이터 없이 flat candidate만 남은 기존/복구 응답은 대화 복구용 안전 데이터로만 유지하고 별도 flat 선택 UI를 렌더링하지 않는다. 이 경우 계층 정보를 다시 확인해야 한다는 오류를 표시하고 empty/selection 제출을 모두 차단한다. 두 형식이 함께 있으면 계층형 UI만 표시한다.
- Legacy Preview session은 `stale_protocol` 읽기 전용 대화 복구 경계를 따르며 flat candidate를 다시 선택하거나 legacy message 선택 필드로 제출하지 않는다. 이 복구에서 planner를 다시 호출하지 않는다.
- `planning`은 같은 `request_id`의 terminal 응답으로 교체한다. 60초 이후 장기 처리 안내를 표시하고, transport 오류가 없다면 server의 4분 processing deadline까지 polling한다.

- 목록 상단에 추천 점수 내림차순임을 표시한다.
- Collection 행은 parent checkbox와 "실행 시 Collection에서 자동 라우팅" 안내를 제공한다. 일부 child만 선택된 parent는 indeterminate와 `선택 수/전체 수`를 표시한다. 이 상태에서 parent를 누르면 표시 가능한 전체 child와 Collection route를 선택하고, 선택된 parent를 다시 누르면 해당 Collection의 개별 child 선택 기록까지 제거해 전체 해제한다. 다른 선택된 Collection이 소유한 공유 KB 상태는 유지한다.
- 하위 KB checkbox는 `selection_key`로 상태를 관리한다. 동일 KB의 어느 위치를 조작해도 모든 위치가 동기화된다.
- `shared_collection_count > 1`이면 `[공유 KB]`를 표시한다.
- Collection에 속하지 않은 권한 확인 KB는 `직접 연결된 KB` 영역에 표시한다.
- 빈 선택 CTA와 선택 적용 CTA는 before/after graph timing을 유지한다.
- Candidate 제출이 `knowledge_selection_stale`이면 기존 card를 닫거나 새 대화 요청을 만들지 않는다. Canonical session에서 최신 계층을 읽어 같은 card의 목록을 교체하고 이전 Collection/KB checkbox state를 초기화한 뒤 `Knowledge 후보가 변경되어 최신 목록으로 갱신했습니다. 다시 선택해주세요.`를 표시한다. 다른 오류나 unrelated rerender에서는 현재 선택을 유지한다.
- 완료된 Knowledge 요약은 선택 종류를 구분해 `Collection N개 선택`, `Knowledge Base N개 선택` 또는 `Collection N개 · Knowledge Base M개 선택`으로 표시한다. Session 복구에서는 현재 계층에 표시 가능한 safe label만 복원하고 권한이 없거나 숨겨진 resource label은 노출하지 않는다.

## Test preflight 연동과 secret 입력 경계

- `ParameterInputRenderer`는 Catalog가 발급한 Slack/GitHub `secret` task에 기존 값을 비운 password input을 표시하고 새 입력을 Workflow node secret-write API로 전달한다. 성공 응답의 opaque reference만 editor graph에 반영한다. Required secret의 `나중에 설정`은 raw value 없는 typed `defer`를 보내고 node를 unresolved로 유지한다. Backend는 조작된 raw secret decision을 방어적으로 `secret_forbidden`으로 거부한다.
- WorkflowResultGroup는 현재 active Slack Bot Token, Slack Incoming Webhook URL 또는 GitHub API Token task에서 masked 입력창과 `나중에 설정` 버튼을 함께 렌더링한다. 이 control을 Node Detail 이동 버튼, 안내 전용 card 또는 credential picker로 대체하거나 secret task 자체를 렌더링에서 제외하지 않는다.
- TestSidebar는 canonical 확인부터 valid `workflow_start.run_id` 수신까지 test preflight owner를 유지한다. Persisted Agent Builder history boundary가 아직 acknowledgement되지 않았거나 Agent Builder를 포함한 어떤 저장 owner라도 stream 시작 시점에 대기 중이면 stream을 열지 않는다. Agent Builder acknowledgement에는 기존 전용 안내를, 일반 저장 대기에는 `Workflow 변경사항을 저장하는 중입니다. 저장 완료 후 다시 실행해주세요.`를 표시하며 자동 재실행하지 않는다.
- Agent Builder editor bridge는 save coordinator lock 획득 직후와 canonical draft 조회 직후 active workflow id를 확인한다. Workflow가 바뀌면 이전 mutation/save/acknowledgement/rollback을 중단하고 `Workflow가 전환되어 이전 Agent Builder 작업을 적용하지 않았습니다.` 계열의 안전한 안내를 표시한다.
- Autosync는 lock miss를 workflow별 하나의 대기 작업으로 합치고, lock 해제 뒤 store에서 다시 읽은 최신 dirty snapshot만 저장한다. 대기 중 workflow 전환 또는 clean 전환이 발생하면 저장하지 않는다.
- Clean editor에서도 canonical graph와 캡처한 local graph를 비교한다. 불일치 시 metadata만 수용하지 않고 명시적인 최신 상태 재동기화를 요구한다.
- `operation envelope not found`는 Agent Builder session/canonical draft 확인 UI로 연결하며 TestSidebar가 typed operation을 재생하거나 test를 자동 재실행하지 않는다. Acknowledged applied 표시는 canonical graph hash와 `updated_at`이 envelope 저장 결과와 모두 일치할 때만 사용한다.
- Node 실행 status와 observability는 store의 비영속 execution presentation action으로 갱신한다. 이 action은 workflow dirty flag, autosync와 Undo/Redo stack을 변경하지 않는다.

## LLM Node Detail Knowledge 연동

- 활성 `after_graph` Knowledge card가 가리키는 LLM의 Node Detail에서 Knowledge를 변경하면 card와 별도 상태로 저장하지 않고 Agent Builder selection event로 전달한다.
- Node Detail picker는 추천 card의 Top-K로 제한하지 않는다. 사용자가 현재 권한으로 조회한 KB/Collection을 선택하면 서버가 독립적으로 권한과 lifecycle을 검증하며, 추천 목록에 없었다는 이유만으로 선택을 막지 않는다.
- Agent Builder는 event target이 response의 safe `target_node_id`와 일치할 때만 처리하며, 동일 selection endpoint와 CAS/acknowledgement 완료 뒤 card와 node를 갱신한다.
- Agent Builder mutation 저장, acknowledgement 확인, 다른 request 제출 또는 unsaved editor 충돌 중에는 Node Detail 제출도 card와 동일하게 잠근다.
- 활성 resolution이 없거나 target이 다른 일반 LLM 설정은 기존 Node Detail 저장 동작을 유지한다.
## Secret task와 이전 항목 action

- `ParameterInputRenderer`는 Catalog `input_type=secret`을 password control로 렌더링하고 기존 raw 값 또는 opaque reference는 hydrate하지 않는다. 새 입력은 `onSecretSubmit`에서 Workflow node secret-write API로만 전달하며 일반 `onSubmit` ParameterDecision을 사용하지 않는다. Node Detail 강제 이동은 제공하지 않고 일반 Node Detail 기능과 LLM Routing 이동 action은 유지한다.
- Workflow store의 node 복제·붙여넣기 정규화는 Slack `authConfig.token`, Slack webhook `url`, GitHub `api_token`이 `workflow-node-secret://` reference인 경우 새 node data에서 제거한다. Channel, message, repository, PR 번호와 그 밖의 일반 설정은 보존하며 다른 node id에 기존 reference를 재사용하지 않는다.
- `WorkflowResultGroup`의 presentation task가 `이전 항목`으로 바뀌면 `NodeParameterCard`는 canonical active task ID가 아니라 presentation task의 required/confirmation 상태와 canonical graph hydration 결과로 action을 계산한다.
- Optional presentation task에 값이 없으면 `건너뛰기`, 값이 있으면 `값 지우고 건너뛰기`를 표시한다. 전자는 `skip`, 후자는 `clear`를 호출한다. Required 또는 confirmation-required task에는 표시하지 않는다.
- Previous 이동, secret save 실패 또는 clear acknowledgement 실패 시 card와 입력값을 유지한다. 성공한 secret save는 canonical metadata를 반영한 뒤 configured/unconfigured session reconciliation만 수행한다.
- 일반 Node Detail이 deferred parameter의 실제 값을 변경해 저장하면 해당 key의 deferred marker만 해제한다. 관련 없는 field 편집, 동일 값 재전송, 빈 값·invalid 값, viewport 변경은 marker를 유지하며 Loop 내부 Node Detail에도 같은 동작을 적용한다.
## Workflow Node Secret Inputs

- Agent Builder는 Slack Bot Token, Slack Incoming Webhook URL과 GitHub API Token을 password 형태의 빈 masked control로 표시한다. 기존 값과 opaque reference는 control value로 hydrate하지 않는다.
- `적용`은 secret-write API가 성공해 opaque reference를 반환한 뒤 해당 graph field를 reference로 갱신한다. 저장 중에는 같은 control만 잠그고 실패하면 입력값과 현재 task를 유지한다.
- `나중에 설정`은 Catalog가 허용한 active secret task에서 계속 표시한다. 이 control을 credential picker, Node Detail 이동 또는 안내 전용 버튼으로 바꾸지 않는다.
- Node Detail의 동일 secret field도 같은 secret-write API를 사용한다. 이미 설정된 reference는 `설정됨`으로만 표시하고 새 값 입력으로 교체하며 plaintext/reference를 input value로 표시하지 않는다.
- Secret-write 응답이 늦게 도착했는데 같은 field가 다시 편집됐다면 늦은 reference를 최신 입력 위에 적용하지 않는다.
