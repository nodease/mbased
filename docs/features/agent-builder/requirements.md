# Agent Builder Requirements

Status: Draft

Related Features: Workflow Editor, Workflow Node Capability Catalog, Knowledge, LLM Credential, Mail Credential, Audit, Workflow Validation, Agent Builder Cache

## 1. Purpose

Agent Builder는 사용자의 자연어 요청을 workflow graph 변경으로 변환하고, 생성된 node에 필요한 설정을 Node Capability Catalog 기준으로 안내한다. 사용자는 graph를 먼저 확인하면서 node별 parameter를 구조화된 control로 입력할 수 있어야 하며, Agent Builder가 만든 전체 graph 변경을 한 번의 Undo로 되돌릴 수 있어야 한다.

이 문서는 Accepted ADR-0045의 Agent Builder direct-edit UX, Accepted ADR-0046의 GraphMutation/CDS 저장 경계와 Accepted ADR-0054의 생성 모드·전환 계약을 구현하기 위한 기능 계약을 정의한다. Model option과 generated LLM node 추천은 Accepted ADR-0040을, planner/repair 사용량 귀속은 Accepted ADR-0055를 따른다. 워크플로우 화면의 비용 표시 범위는 Accepted ADR-0060을 따른다. ADR-0019는 Superseded Preview 기록이며 characterization 외 활성 fallback으로 사용하지 않는다. MBA-293 시점 코드에는 기존 두 mode만 있으므로 세 canonical mode와 빠른 생성은 후속 구현 목표다.

## 2. Design Principles

- 자연어 의미 해석은 planner가 담당하고 node와 parameter의 제품 계약은 catalog가 담당한다.
- graph 변경, parameter 수집, Knowledge 선택, workflow 저장과 실행을 서로 다른 경계로 유지한다.
- parameter마다 LLM을 다시 호출하지 않는다.
- credential 원문과 secret-like 값은 planner, 일반 chat, audit에 전달하지 않는다.
- frontend는 UX를 제공하지만 node 지원 여부, 권한, graph validation의 최종 판단은 backend가 수행한다.
- generated graph는 실행되지 않으며 외부 action node는 설정이 완료될 때까지 unresolved 상태를 유지한다.
- Accepted ADR-0063의 optional intent plan cache는 provider 호출만 생략할 수 있고 planner 결과의 source of truth,
  current permission, Knowledge resolution, GraphMutation, CAS와 acknowledgement 계약을 변경할 수 없다.

## 3. Scope

### 3.1 In Scope

- 신규 workflow 생성과 기존 workflow 수정을 위한 typed GraphMutation 생성
- 기본 `단계별 생성`, 명시적 `빠른 생성`, 고급 `구조만 생성` 모드와 안전한 mode 전환
- 단계별 생성 중 미완료 task만 대상으로 하는 `남은 설정 빠르게 완료`
- Node Capability Catalog 기반 parameter task 생성
- upstream output을 이용한 selector 추천
- node focus와 node별 parameter card
- GraphMutation의 원자적 editor 적용과 단일 Undo
- 저장된 GraphMutation까지 CDS로 되돌리는 persisted Undo
- graph 구조 영향에 따른 Knowledge 선택 시점 분리
- Collection 동적 routing과 직접 KB 고정을 구분하는 계층형 Knowledge 후보·선택
- session 복구, 권한 재확인, stale detection, audit

### 3.2 Out Of Scope

- 일반 chat/planner/typed ParameterDecision/GraphMutation을 통한 credential secret 수집과 신규 managed Slack/GitHub credential resource 또는 picker 추가. Slack/GitHub masked input은 예외적으로 ADR-0062의 인증된 Workflow node secret-write command를 사용하며 graph에는 opaque reference만 반영한다.
- workflow 실행, retrieval 또는 외부 API action 실행
- runtime에 존재하지 않는 node type 구현
- 임의의 plugin parameter schema 추론
- 일반 workflow의 기존 권한 모델 변경. 단, Agent Builder mutation의 CDS 저장과 unresolved 외부 action의 server-side 실행·배포 preflight 차단은 포함한다.
- Agent Builder 별도 서버, 독립 데이터베이스 또는 신규 Agent Builder table
- ADR-0040의 generated LLM model 추천과 intent model 표시 순서 정책 재설계. 최신 dev 동작은 회귀 검증하고 MBA-228이 덮어쓰지 않는다.
- Frontend와 Gateway의 mixed-revision 무중단 배포, staged rollout/rollback, creation gate와 image artifact 검증
- Legacy Preview API/UI, 별도 preview graph store 또는 preview 전용 apply/save 계약 복구

## 4. Functional Requirements

### DBP-FR-001 Generation Mode

- Agent Builder의 canonical mode는 `guided_generate`, `quick_generate`, `structure_only`다.
- 화면 기본값과 mode가 생략된 신규 요청의 기본값은 `guided_generate`다.
- 기존 `configure_and_generate` 입력과 저장 row는 내부 domain에서 `guided_generate`로 읽기 정규화하고 기존 JSON row를 backfill하지 않는다. Request가 없을 때 외부 응답은 `X-Agent-Builder-Mode-Contract`가 없거나 `legacy-v1`이면 legacy 표현을, `canonical-v2`이면 canonical 표현을 반환한다.
- Message request 생성 시 정규화한 `mode_contract_version`을 request `response_payload`에 고정한다. Request id로 대상을 특정하는 후속 조회·변경은 해당 request와 같은 contract만 허용하고 불일치하면 request payload를 projection하지 않은 채 `mode_contract_mismatch`를 반환한다. Raw header와 session 전체 contract는 저장하지 않고 값이 없는 기존 request는 `legacy-v1`로 읽는다.
- 생성 모드 rollout은 Gateway dual-input/legacy-output, Client dual-read/legacy-write, 전체 Gateway replica와 Client gate 확인 뒤 canonical-v2 응답, Client canonical-write 순서다. 구형 Client가 canonical 응답을 먼저 받게 해서는 안 된다. Rollback은 canonical/quick creation을 닫고 foreground 및 open configuration을 포함한 canonical-v2 nonterminal request를 완료·취소해 0건으로 drain한 뒤 legacy-write로 전환할 수 있다. 다만 session GET 보존 기간 내 terminal canonical-v2 request까지 포함한 retained-history aggregate가 0건이 되기 전에는 dual-contract Gateway와 Client dual-read를 제거하거나 legacy-only Client를 배포하지 않는다. Completed quick history를 legacy guided로 축소 projection하거나 숨기지 않는다.
- 사용자는 `canonical-v2`에서 화면 mode control 또는 명시적인 자연어 요청으로 `quick_generate`를 요청할 수 있다. LLM은 mode 의도를 구조화할 수 있지만 eligibility와 권한을 승인하지 않는다.
- Client는 초기 화면값과 사용자의 명시적 선택을 `generation_mode_source=default|explicit_control`로 구분한다. `canonical-v2`에서는 명시적 control 선택, planner가 구조화한 명시적 자연어 mode 의도, 기본 guided 순으로 requested mode를 확정한다. Default guided는 "한 번에 만들어 줘" 같은 명시적 자연어 요청을 막지 않는다. `legacy-v1`은 자연어 quick 의도를 활성화하지 않고 `configure_and_generate`로 처리하며 canonical quick 직접 입력은 request 생성 전에 거부한다.
- `quick_generate`의 최종 허용 여부는 Gateway application policy가 현재 권한, active organization, Catalog, base graph hash, workflow `updated_at`, resource revision과 미해결 선택을 기준으로 결정론적으로 판정한다.
- Quick mode는 모든 capability가 지원되고 parameter가 사용자 요청·기존 graph·단일 selector·안전한 Catalog default로 하나의 값으로 확정되며, 권한 resource와 revision이 현재 유효하고 외부 부수효과를 새로 활성화하지 않을 때만 허용한다.
- Credential, 권한 있는 KB/Collection 선택, 일반 required parameter의 확정 가능한 값 부재, 외부 대상, 의미 있는 복수 후보, Condition branch, HTTP/code/egress, unresolved 외부 action 또는 stale/hidden resource가 남으면 quick proposal을 발급하지 않는다. 일반 설정값 부재는 `configuration_value_required`로 fail-closed한다.
- Quick mode가 불가능하면 graph를 변경하지 않고 safe reason code와 `mode_transition_required`를 반환한다. UI는 민감한 resource 존재를 드러내지 않는 설명과 `단계별 생성으로 계속`/`취소`를 제공한다. 사용자 확인 없이 자동 전환하지 않는다.
- Quick 판정에서 사용자 문장으로부터 읽은 실제 parameter 값은 request/session metadata에 저장하지 않는다. 전환 복구에는 값에 독립적인 structured plan과 재입력이 필요한 Catalog `step_id`/`parameter_key`만 남긴다. `continue_guided`는 해당 값을 추측·재사용하거나 planner를 다시 호출하지 않고 `reconfirmation_required` ParameterTask로 열어 사용자가 typed control에서 다시 입력하게 한다.
- Quick mode가 가능하면 backend는 `workflow_id`, base/result graph hash, expected workflow `updated_at`, Catalog version, full typed operations, affected node ids와 completion context를 모두 포함한 공통 GraphMutation과 redaction-safe 변경 요약을 일회성 응답으로 반환한다. RequestStatus는 `graph_mutation_ready`, 응답 안의 GraphMutationStatus만 `pending_apply`다. Frontend는 editor clone에 dry-run해 동일 validator를 통과시킨 뒤 추가·변경·삭제 node와 남은 차단 사항을 표시한다. 사용자가 `생성 적용`을 명시적으로 선택한 뒤에만 실제 history boundary에 적용하고 CDS CAS 저장한다.
- Quick review는 Legacy Preview가 아니다. Preview session/API, 별도 draft store 또는 preview 전용 save path를 만들지 않으며 guided mode와 같은 GraphMutation, CAS, acknowledgement와 Undo 경계를 사용한다.
- Quick response를 적용 전에 잃거나 reload하면 safe envelope에서 full operations를 복원하지 않는다. 기존 `graph_mutation_ready` request를 contract-neutral cancel로 terminal 처리한 사실을 확인한 뒤에만 새 message request를 생성하며, cancel 결과가 불명확한 동안에는 재제출하지 않는다. CAS 저장 뒤 acknowledgement 유실 복구는 ADR-0046을 따른다.
- `남은 설정 빠르게 완료`는 새 generation mode가 아니라 `guided_generate` request의 범위 축소 명령이다. 이미 acknowledgement된 graph와 완료 task를 보존하고 현재 미완료 task만 현재 권한·Catalog·revision으로 재평가한다. 전체 planner 또는 전체 graph generation을 다시 실행하지 않는다.
- 남은 task 중 canonical graph에 추천값이 이미 materialize되고 recommendation fingerprint가 일치하는 항목만 한 검토안으로 묶는다. 생성과 적용은 `Workflow -> AgentBuilderRequest` lock 안에서 graph revision과 request/task fence를 재검증하는 멱등 batch confirm이며 GraphMutation이나 workflow 저장을 만들지 않는다. 값이 없거나 변경이 필요한 항목, credential, 권한 resource, 외부 부수효과, Condition branch와 복수 후보는 단계별 상태로 유지한다.
- `structure_only`는 parameter task를 시작하지 않는 generation mode다. 빈 workflow의 새 graph는 `initial_graph`, 기존 workflow 부분 변경은 `graph_edit`, 기존 workflow 전체 교체는 `replace_workflow` GraphMutation을 사용하며 mode와 kind는 독립이다.
- `structure_only` 결과의 unresolved configuration은 저장할 수 있지만 test, run과 deployment preflight가 차단하며 생성 완료 또는 실행 준비 상태로 표시하지 않는다.
- Request-scoped `mode_contract_version`, `generation_mode`, `generation_mode_source`, requested/effective mode, transition status와 safe reason code는 각 request의 기존 `AgentBuilderRequest.response_payload`에만 저장한다. Raw negotiation header, 이 값을 위한 session/전용 column, 신규 table 또는 별도 영구 column은 저장·추가하지 않는다.
- Mode transition과 빠른 완료 요청은 client-generated operation id와 expected request/task version을 사용한다. Safe 상태만 반환하는 transition/proposal 재시도는 현재 state를 선택하기 전에 persisted idempotency result를 조회해 같은 canonical 결과를 반환하고 stale 또는 competing 요청은 graph나 task를 변경하지 않은 채 conflict로 닫는다. Full GraphMutation operations를 발급한 transition 응답이 유실되면 같은 operation id 재시도는 중복 mutation을 만들지 않고 `operation_payload_unavailable`을 반환하며, 사용자가 기존 request 취소와 terminal 상태를 확인한 뒤 새 request를 명시적으로 제출해야 한다.
- Request version은 기존 `response_payload` 안에서 1부터 단조 증가한다. Proposal 생성은 `Workflow -> AgentBuilderRequest` lock 아래 expected graph hash/`updated_at`, request/task version을 확인하고 request와 각 confirm 대상 task version을 정확히 한 번 증가시킨다. Pending proposal에는 fenced task id/version, recommendation fingerprint와 canonical graph hash/`updated_at`만 저장하고 실제 parameter 값이나 graph fragment는 복제하지 않는다. Pending proposal은 target task를 예약해 다른 decision을 `task_conflict`로 차단한다. Acknowledge는 같은 lock 순서로 권위 graph를 다시 읽어 revision/fingerprint를 검증한 뒤에만 task version을 다시 증가시킨다. Proposal은 별도 monotonic proposal version을 가지며 `pending -> acknowledged|canceled|stale` 전환 시 request version과 함께 원자적으로 증가하고, 같은 operation 재시도는 어떤 version도 다시 증가시키지 않는다. 신규 DB column은 추가하지 않는다.
- `planning|clarification_required|mode_transition_required|graph_mutation_ready|parameter_configuration`인 모든 비종료 RequestStatus는 contract-neutral request cancel로 종료할 수 있다. `configuration_required`는 terminal 상태로 유지한다. 취소는 request version과 변경되는 task/proposal version을 증가시키고 남은 task, pending proposal, 미완료 Knowledge resolution, 저장 전 operation envelope와 늦은 planner/transition commit을 원자적으로 닫는다. Terminal request 아래에 pending child state를 남기지 않되 이미 저장된 graph와 완료 값은 자동 되돌리지 않는다. Guided request 자체는 유지하면서 remaining quick proposal만 닫을 때는 proposal id/version을 검증하는 전용 cancel을 사용한다.
- Session admission의 foreground 상태는 `planning|clarification_required|mode_transition_required|graph_mutation_ready`다. `parameter_configuration`은 비종료지만 비차단 open 상태이므로 request별 ParameterTask/Knowledge card와 stored mode contract를 보존한 채 하나의 새 foreground request와 공존할 수 있다. Message submit은 session row를 잠가 foreground request가 없음을 확인하고 mode contract, mode source와 요청 귀속 metadata를 포함한 `planning` row를 먼저 commit한다. 명시적 control과 `legacy-v1` default는 canonical requested/effective mode도 이때 저장한다. `canonical-v2` default는 자연어 mode intent가 필요하므로 planning 동안 requested/effective mode를 미확정으로 두고 schema-valid planner 결과에서 intent mode 또는 guided fallback을 같은 request lock 안에서 정확히 한 번 확정한다. Competing foreground submit만 `request_in_progress`로 차단한다.
- 동기식 message 응답이 request id를 반환하기 전에는 client operation id를 받는 session-scoped active-request cancel을 사용한다. Server는 session row를 잠근 뒤 terminal request를 포함한 persisted cancel operation result를 먼저 조회하고, 결과가 없을 때만 응답 전 유일한 `planning` row를 찾아 같은 cancel command로 종료한다. 기존 `parameter_configuration` request를 추측해 취소하지 않으며 대상이 없거나 legacy 이상으로 둘 이상이면 conflict로 닫는다.
- Contract-neutral cancel은 인증된 principal과 persisted session/request 원 소유자가 일치하면 현재 workflow write 권한이나 organization membership이 회수돼도 허용한다. 이 cleanup 예외는 graph/parameter/organization 데이터를 읽거나 수정하는 권한이 아니며 opaque id 소유권 불일치는 resource-hiding으로 처리한다. 인증할 수 없는 만료 request와 rollout drain은 공개 관리자 API가 아니라 내부 expiry/운영 작업이 같은 cancel state machine을 사용한다.

### DBP-FR-002 Structured Planning

- planner는 사용자 message, server-loaded workflow context, 선택된 editor hint, safe Knowledge candidate context, capability 목록과 capability별 Catalog 허용 parameter key/safe label을 입력으로 사용한다.
- 정상 request에서 planner는 provider를 한 번 호출해 node 목적, dependency, edit target, Knowledge 필요성, 명시적 자연어 mode 의도 또는 null과 `step_id`, `parameter_key`, `reason`, `input_guidance`로 구성된 parameter guidance hint를 하나의 구조화 응답으로 반환한다. Mode 의도는 요청에 명시된 생성 방식만 구조화하며 eligibility를 뜻하지 않는다.
- Backend는 hint의 `step_id`가 현재 plan step이고 `parameter_key`가 해당 step capability의 Catalog에 있을 때만 사용한다. Unknown/mismatched hint는 폐기하고 Catalog description으로 fallback한다.
- planner는 parameter key, credential 값, 최종 validation rule의 권위가 아니다.
- 최초 결과가 schema-valid지만 semantic invariant를 위반한 경우에만 safe machine code로 semantic repair를 최대 한 번 수행한다. Provider/JSON/schema 실패에는 repair하지 않고 fail-closed하며, repair 결과가 다시 실패해도 종료한다. 한 request의 provider 호출 총수는 최대 두 번이다.
- Usage attribution은 이 호출 정책을 관찰할 뿐 확대하지 않는다. Mode contract, mode source와 요청 귀속 metadata가 commit된 뒤에만 attempt를 예약하며, 자연어 mode 판정 전 usage fact는 request id로 귀속하고 미확정 mode를 추측하지 않는다. Schema-invalid 응답은 실제 attempt 1 usage를 기록할 수 있어도 repair하지 않는다. 취소 전에 시작된 attempt의 billing fact는 멱등 완료할 수 있지만 다음 repair attempt 전에 request version/status를 재검증하고 canceled/stale이면 provider를 다시 호출하지 않는다. Usage 기록 실패는 새로운 RequestStatus를 만들지 않고 terminal `failed`와 safe issue code로 표현한다.
- 기존 workflow target 또는 삽입 위치를 지정하지 않은 완결된 node 흐름 또는 단일 지원 node 생성 요청은 message에 `workflow`라는 단어가 없어도 `new_workflow`로 구조화한다. Planner는 Catalog의 다국어 `planner_aliases`를 canonical capability ID로 변환하고 사용자가 직접 요청한 ID를 `requested_capabilities`에 포함한다. 예를 들어 `입력 응답 노드를 만들어줘`는 `start_input -> answer` 흐름이고, `깃허브 노드 만들어줘`는 `github_pr_read`와 GitHub PR read action을 포함한 신규 flow다. 명시적인 GitHub PR 생성 요청은 지원하지 않는 action으로 유지한다.
- `request_type=unsupported`는 구조화된 unsupported action 또는 사용자에게 반환할 safe unsupported reason을 포함해야 한다. 둘 다 없는 결과는 `UNSUPPORTED_REASON_REQUIRED` semantic contradiction으로 처리해 한 번 repair한다. 단일 `requested_capabilities`가 Catalog에서 supported이고 `standalone_creation=allowed`인데 unsupported로 반환된 경우만 `UNSUPPORTED_SUPPORTED_NODE_CREATION_CONTRADICTION`으로 한 번 repair한다. `requires_context`, `forbidden`, 다중 또는 unknown capability는 이 경로로 repair하지 않는다. `github/pull_request/create`처럼 서버가 명확히 인식하는 structured unsupported action은 별도 자연어 reason이 없어도 unsupported로 수용한다. Backend는 사용자 message의 정규식 matching으로 capability를 선택하거나 planner 결과를 특정 workflow로 덮어쓰지 않는다.
- `knowledge_required=true`인 결과는 유효한 typed Knowledge placement를 정확히 하나 이상 포함해야 한다. 누락되거나 timing/effect/target이 유효하지 않으면 safe semantic code와 placement 계약을 제공해 한 번 repair하며, repair 뒤에도 유효하지 않으면 fail-closed한다.
- `between` 기존 workflow 수정은 selected edge 또는 `natural_language_edge` source/destination reference 쌍을 사용한다. 자연어 pair는 server-loaded graph에서 source와 destination을 잇는 직접 edge가 정확히 하나일 때만 resolve한다. 0개 또는 복수 direct edge는 edge 선택 clarification이며, 여러 hop path 탐색은 후속 범위다.
- 자연어 edge target은 node `data.title` 일치를 우선 사용한다. 제목이 일치하지 않는 경우에만 예약 구조 node의 안전한 별칭(`입력`/`시작`, `응답`/`출력`)을 `startNode`/`answerNode`에 대응하며, 별칭 후보가 복수이면 자동 선택하지 않는다.
- Parameter, Knowledge와 task 전환에서는 planner를 호출하지 않는다.

### DBP-FR-003 Capability Catalog Authority

- Agent Builder가 생성할 수 있는 node type은 `implemented=true`와 `agent_builder_supported=true`를 모두 만족해야 한다.
- catalog는 node별 parameter key, 표시 이름, 입력 타입, 필수 여부, 기본값 정책, 검증 규칙, 민감도, input/output contract를 제공해야 한다.
- frontend와 backend는 동일 catalog version을 사용해야 한다.
- LLM의 system/user/assistant prompt는 개별 optional task이지만 세 값 중 하나 이상은 필수다. 모두 비어 있으면 세 task를 순차 확인하고 마지막 빈 prompt의 `skip`을 server가 거부해야 한다.
- LLM `output_json_schema`는 `output_format.type=json`일 때만 표시하는 optional 값이다. 비어 있으면 skip할 수 있고, 값이 있으면 JSON object만 허용한다.
- LLM `referenced_variables[].name`은 prompt의 `{{변수명}}` runtime key다. 기존 selector를 다시 저장할 때 이름을 보존하고 새 selector는 Catalog output key를 사용하며 같은 LLM 안의 중복 이름은 거부한다.
- MBA-228 parameter 계약은 단일 catalog v3로 제공한다. Bundled JSON, parser와 backend/frontend parity 검증을 함께 전환하며, 신규 direct-edit session은 v3가 준비되지 않으면 시작하지 않는다. Full typed operations는 발급 API 응답에만 포함하고 기존 `AgentBuilderRequest.response_payload`에는 `catalog_version=3`과 safe operation envelope만 기록한다. 버전이 없거나 `2`인 미적용 legacy envelope는 stale 처리하고, `3`인 envelope만 CDS 저장·acknowledgement 후보로 허용한다. 이 판별이나 operation replay를 위한 신규 DB column/table/migration, encrypted operation store 또는 legacy backfill은 추가하지 않는다.
- catalog에 없는 parameter를 planner 응답만으로 graph에 저장하지 않는다.
- runtime output과 catalog output contract가 다르면 draft generation을 차단하고 catalog/runtime contract test를 실패시킨다.
- `conditionNode`의 동적 case와 `default` branch는 각각 기존 node 또는 명시적 `연결 안 함`을 선택하는 typed ParameterTask로 확인한다. 기존 explicit edge target은 안전한 label과 함께 control 초기값으로 표시하되 모든 branch decision이 확인되기 전에는 Condition configuration을 `unresolved`로 계산한다. `cases` 저장 acknowledgement 뒤 새 handle task를 pending으로 추가하고 사라진 handle task는 canceled로 보존한다.
- Condition outgoing edge는 case id 또는 `default`와 일치하는 명시적 `sourceHandle`을 가져야 한다. 생성기가 만든 단일 선형 downstream edge는 mutation 발급 전에 node를 삭제하지 않고 explicit `default`로 변환할 수 있다. 여러 handle 없는 edge 또는 explicit default와 충돌하는 edge는 추측하지 않고 validation failure로 닫는다. 명시적으로 연결하지 않기로 확인한 branch에는 edge를 만들지 않으며 validator와 runtime은 handle 없는 edge를 default로 보정하지 않는다.

### DBP-FR-004 GraphMutation

- backend는 client가 보낸 raw graph snapshot을 신뢰하지 않는다.
- Direct-edit session은 기존 Editor 생성 흐름으로 만든 workflow id를 요구한다. 신규 workflow 생성은 저장된 빈 workflow shell에 `initial_graph` mutation을 적용하는 의미이며 Agent Builder가 별도 workflow row를 생성하지 않는다.
- 비어 있지 않은 editor workflow에서 Planner가 완결된 `new_workflow`를 반환하거나 사용자가 전체 교체를 명시하면 `replace_workflow`를 발급한다. 기존 edge/node 전체 제거와 새 node/edge 추가는 하나의 typed operation 묶음이어야 하며 raw graph overwrite를 허용하지 않는다.
- 기존 workflow 수정은 server-loaded saved graph와 selected node/edge hint를 기준으로 target을 resolve한다.
- 자연어 edge target은 source/destination reference 각각을 node resolver로 resolve한 뒤 두 node를 직접 잇는 edge 하나만 사용할 수 있다. resolver는 node 또는 edge를 추측하거나 경로 전체를 탐색하지 않는다.
- GraphMutation kind는 `initial_graph`, `graph_edit`, `replace_workflow`, `parameter_update`, `knowledge_binding`으로 제한한다. `structure_only`는 kind로 허용하지 않는다.
- API GraphMutation은 operation id, workflow id, catalog version, non-null base graph hash, non-null expected workflow `updated_at`, 발급 전 검증한 non-null expected result graph hash, typed operations, affected node ids와 선택적 completion context를 포함한다. Full operations는 응답 전용이다. Request/session에는 operations 없이 같은 식별자·hash·completion context를 safe envelope로 저장하고 result graph hash는 저장 뒤 server가 확정한다.
- 지원 operation은 `add_node`, `remove_node`, `add_edge`, `remove_edge`, `replace_node_data`, `replace_node_position`으로 제한한다.
- 다중 target 또는 target 불확실성은 clarification으로 닫는다.
- detached node, 허용되지 않은 node type, invalid handle, Start/terminal 정책 위반 mutation은 반환하지 않는다.
- Mutation lifecycle은 `pending_apply`, `pending_save`, `pending_ack`, `acknowledged` 순서이며 권한, stale 또는 validation 실패는 `blocked`다. Persisted Undo가 확정된 원 operation은 최종 상태 `reverted`가 되며 이는 별도 GraphMutation kind가 아니다.

### DBP-FR-005 Atomic Editor Apply And Undo

- frontend는 GraphMutation 하나를 한 번의 workflow store transaction으로 적용한다.
- node와 edge를 각각 별도 history entry로 기록하지 않는다.
- 최초 `initial_graph`, `graph_edit` 또는 `replace_workflow`는 Agent Builder 시작 전 snapshot과 acknowledgement가 끝난 최종 graph snapshot을 묶는 Workflow history boundary 하나를 만든다. `replace_workflow`의 시작 snapshot은 교체 전 기존 graph 전체다.
- 이후 각 `parameter_update`와 `knowledge_binding`은 CDS 저장과 acknowledgement를 계속 수행하지만 별도 Workflow history transaction을 만들지 않고 같은 boundary의 final graph/hash만 갱신한다.
- 완료 상태에 ParameterTask가 있으면 boundary의 첫 Undo는 graph와 기존 값을 유지한 채 `completed|skipped|deferred` 중 재편집 가능하고 `stable_order`가 가장 큰 task를 client presentation에서 표시해 설정 UI에 재진입한다. Persisted task status/version은 바꾸지 않는다. 재진입 상태의 다음 Undo는 시작 전 snapshot을 서버 graph에도 CDS 저장하고 모든 task/Knowledge 흐름을 `canceled`로 닫는다. Task가 없으면 완료 상태의 첫 Undo가 즉시 전체 복구다.
- ParameterTask 사이 이동은 backend의 `next_task_id`를 client-only presentation cursor로 소비하는 `이전 항목` control로 처리하며 persisted task 상태, graph와 Workflow history를 변경하지 않는다. 완료 뒤의 일반 수동 editor 변경은 boundary보다 먼저 Undo된다.
- Redo history는 client memory에만 유지하고 reload 뒤 복구하지 않는다. 재진입 상태의 Redo는 설정 UI만 닫고, 전체 복구 뒤 Redo는 final graph를 CDS 저장하되 canceled Agent Builder task, Knowledge 흐름이나 대화를 자동 재개하지 않는다. 전체 Redo 뒤 다시 Undo하면 canceled task UI에 재진입하지 않고 같은 boundary의 시작 전 graph로 바로 복구한다.
- Parameter input의 Ctrl+Z는 control 내부 입력만 되돌리고 Agent Builder card/button focus에서는 canvas Undo/Redo를 실행하지 않는다. Workflow Undo/Redo는 canvas 또는 명시적인 전역 Workflow command focus에서만 실행한다.
- mutation의 base graph hash가 current graph와 맞지 않거나 일부 operation만 적용 가능하면 전체 적용을 거부한다.
- frontend는 저장 payload를 mutable React Flow state에서 다시 직렬화하지 않고 canonical base graph에 서버 발급 typed operations를 재생한 결과로 만든다. React Flow가 렌더링 중 추가하는 `width`, `height`, `measured`와 같은 화면 측정값은 editor 표시에는 사용할 수 있지만 workflow 저장 graph와 canonical hash에는 포함하지 않는다.
- direct-edit mutation 적용은 곧바로 workflow 실행이나 배포를 의미하지 않는다.
- Agent Builder transaction 동안 일반 autosync는 같은 graph를 별도로 저장하지 않는다.
- Agent Builder graph save 또는 acknowledgement 결과 확인 중에는 같은 browser editor의 test preflight save와 workflow test 실행을 시작하지 않는다. TestSidebar는 저장 확정 뒤 다시 실행할 수 있으며 클릭을 자동 실행 queue로 보존하지 않는다.
- frontend는 mutation 적용 후 `operation_id`, `expected_base_graph_hash`, `expected_workflow_updated_at`, `catalog_version`을 `mutation_context`로 포함해 일반 workflow draft save endpoint를 호출해야 한다.
- backend는 workflow row를 write lock으로 조회해 권한을 재확인하고 current canonical graph hash와 `updated_at`을 기대값과 비교한다. 하나라도 다르면 저장하지 않고 stale conflict를 반환한다.
- 저장 전 complete candidate graph를 catalog/schema/connection policy로 다시 검증한다.
- 저장 성공 응답은 server가 저장 graph에서 계산한 canonical `graph_hash`, persisted `updated_at`, `workflow_id`, `operation_id`를 반환한다. 존재하지 않는 workflow revision/version 값을 만들지 않는다.
- backend는 candidate graph hash가 발급 시 저장한 `expected_result_graph_hash`와 일치하는지 확인해 CDS 저장하고, canonical `graph_hash`와 `updated_at`을 검증한 acknowledgement 이후에만 parameter task를 활성화한다. Acknowledgement는 graph를 다시 저장하지 않는다.
- 저장 실패 또는 저장 전 종료 상태에서는 parameter task를 시작하지 않는다.
- 최초 graph, 기존 graph 구조 변경, parameter decision과 `after_graph` Knowledge binding은 같은 GraphMutation/CDS 저장/acknowledgement 계약을 사용한다.
- acknowledgement 전에는 해당 operation을 완료로 기록하거나 parameter task를 `active|completed|deferred|skipped`로 전환하지 않는다. 최초 structural operation의 parameter group/task는 `pending_save|pending_ack`/`pending`으로 유지하고 decision API도 제출을 거부한다. `structure_only`는 task를 만들지 않지만 acknowledgement 전에는 operation을 완료로 기록하지 않는다.
- ParameterTask의 `resolution_source=existing_graph`는 mutation 발급 전 canonical base graph에 존재한 node의 실제 설정값에만 허용한다. mutation으로 추가한 node의 Catalog default/template 값과 upstream selector는 각각 `catalog_default`, `upstream_selector`로 표시한다. 안전한 추천값은 structural save/acknowledgement가 확인된 뒤 별도 confirm 없이 completed로 표시하되 언제든 `수정`으로 다시 열 수 있어야 한다.
- 같은 workflow의 동시 mutation은 첫 CDS 저장만 성공하고 뒤 저장은 stale conflict로 닫는다. Silent overwrite, 자동 merge와 CRDT는 제공하지 않는다.
- 동일 operation의 CDS save 응답이 유실되면 client는 같은 mutation context로 한 번 자동 재시도한다. 두 번째 결과도 불명확하면 canonical workflow와 boundary 상태를 조회해 반영됨, 미반영 또는 stale을 판정할 때까지 pending history를 보존한다. Server는 revert와 redo 모두 canonical graph hash와 `updated_at`이 safe envelope의 저장 결과와 일치할 때 새 write, task/Knowledge 전환이나 audit 없이 기존 저장 성공 결과를 반환한다.
- 동일 canonical graph hash와 `updated_at`의 acknowledgement 재시도는 최초 응답과 같은 완료 결과를 반환하고 task version, Knowledge resolution과 audit를 다시 변경하지 않는다.
- 완료 boundary를 전체 Undo하면 frontend는 boundary 시작 snapshot을 복구하고 boundary operation id, boundary final graph hash, current workflow `updated_at`, `action=revert`를 포함해 CDS revert save를 수행한다.
- backend는 현재 canonical graph가 boundary final graph hash이고 복구 candidate hash가 boundary base graph hash일 때만 revert를 저장한다. 다른 편집이 있으면 stale conflict로 닫는다.
- Graph revert, boundary 상태 전환, 모든 ParameterTask/Knowledge resolution의 `canceled` 처리와 기존 transaction-bound audit insert는 같은 DB transaction에서 확정한다. `parameter_update`/`knowledge_binding`별 persisted Undo는 허용하지 않는다.
- 전체 복구 뒤 Redo는 client가 보존한 final graph를 `action=redo` CDS save로 복구할 수 있다. Backend는 graph만 저장하고 canceled task/Knowledge 상태를 변경하지 않는다.
- 저장 또는 acknowledgement가 완료되지 않은 결과는 completed boundary로 취급하지 않는다. 저장 여부가 불명확하면 canonical operation/workflow 상태를 먼저 복구한다.

### DBP-FR-006 Parameter Task Planning

- Agent Builder save/acknowledgement 전역 차단은 겹친 작업 수를 추적하며, 진행 중인 마지막 작업이 끝날 때까지 test, autosync와 Workflow Undo/Redo 차단을 해제하지 않는다.
- 동일 operation의 CDS save는 transport 단절 또는 `5xx`처럼 결과가 불명확할 때만 같은 mutation context로 한 번 자동 재시도한다. 명시적인 `401|403|409|422` 응답은 재전송하지 않고 확정 실패로 처리한다.

- `configure_and_generate`에서 backend는 patch 결과 node를 catalog와 대조해 모든 user-configurable parameter에 parameter task record를 만든다. Start의 `variables`와 Answer의 `outputs`도 사용자 확인·수정 대상으로 포함한다. `agent_builder_task=false`는 runtime 내부 필드처럼 사용자에게 설정 control을 제공해서는 안 되는 값에만 사용한다.
- parameter 값은 사용자 요청의 명시적 값, 기존 workflow 값, 단일 upstream output과 catalog contract로 확정되는 값, catalog의 안전한 기본값 순서로 결정한다.
- 앞 단계에서 값이 확정되면 graph에 값을 반영하고 `resolution_source=user_request|existing_graph|upstream_selector|catalog_default`와 값 원문이 아닌 canonical SHA-256 `recommendation_fingerprint`를 기록한다. 일반 안전 추천은 structural acknowledgement 뒤 completed로 표시할 수 있지만, 새 LLM node의 Catalog 기본 `auto_model_routing=false`는 사용자 확인 전까지 `pending|active`로 유지한다. 실제 값은 task에 복제하지 않고 workflow graph에서 hydrate하며, 확인이 필요한 추천에는 안전한 추천 이유와 확인 또는 수정 control을 표시한다.
- 후보가 2개 이상이면 자동 확정하지 않고 선택 task를 만든다.
- task는 node id, parameter key, label, input type, required 여부, defer policy, safe description, example, validation rule reference, suggestion 목록, status, task version, stable order, 선택적 resolution source와 `reconfirmation_required`를 포함한다. Reconfirmation task는 실제 값과 resolution source 없이 typed `set`만 허용한다.
- safe description은 검증된 planner `reason`/`input_guidance`와 catalog 설명을 조합하되 hint가 없거나 폐기되면 catalog 설명으로 fallback하고 secret 또는 raw payload를 포함하지 않는다.
- 동일 node의 task는 catalog가 정의한 안정적인 순서로 묶는다.
- 여러 node의 task는 graph dependency 순서로 정렬한다.
- parameter task 생성 과정에서 LLM을 추가 호출하지 않는다.

### DBP-FR-007 Parameter Input And State

- parameter 값은 일반 chat text message가 아니라 typed decision payload로 제출한다.
- 지원 input type은 `text`, `textarea`, `code`, `json`, `number`, `boolean`, `select`, `secret`, `resource_ref`, `credential_ref`, `variable_selector`, `variable_selector_list`다.
- Decision value는 input type과 일치하는 discriminator를 사용한다. `variable_selector`는 server-issued `suggestion_id`와 기존 runtime 표준인 `[source_node_id, output_key, ...nested_path]` 배열을 함께 보낸다. `variable_selector_list`는 중복되지 않은 하나 이상의 server-issued selector selection을 보낸다. 임의 selector 문자열은 허용하지 않는다.
- `secret`은 기존 node graph가 직접 소유하는 민감 설정을 위한 Catalog/runtime/preflight 입력 유형이다. Slack Bot Token, Slack Incoming Webhook URL과 GitHub API Token은 direct-edit task와 masked input card로 제공한다. 기존 원문은 hydrate하거나 표시하지 않고 새 입력으로 교체하거나 명시적으로 삭제한다.
- Secret 입력은 일반 typed ParameterDecision이나 GraphMutation payload로 제출하지 않는다. Frontend는 ADR-0062의 인증된 Workflow node secret-write command로 원문을 한 번 제출하고, 서버가 만든 immutable encrypted revision의 opaque reference만 editor/autosync 경로에 반영한다. Agent Builder session/task/audit/log에는 원문이나 ciphertext를 복제하지 않으며 신규 managed credential picker 또는 credential resource를 추가하지 않는다.
- Catalog v3 parameter의 `defer_policy`는 `forbidden` 또는 `allow_unresolved`이며 생략하면 `forbidden`이다.
- `credential_ref`를 포함한 configurable parameter는 현재 reference가 없다는 이유만으로 자동 `deferred` 처리하지 않는다. Agent Builder가 발급하는 task는 `pending|active`에서 사용자 확인을 기다리며, 사용자가 명시적으로 `defer`를 제출하고 Catalog policy가 허용한 경우에만 GraphMutation, CDS 저장과 acknowledgement 뒤 `deferred`가 된다. Permission-filtered picker는 durable credential resource/use 권한 resolver가 이미 존재하는 Mail/Gmail에만 제공한다. Gmail Draft에는 Gmail OAuth2 use-permitted credential만 노출하고 제출도 같은 조건으로 검증한다. Gmail/Mail selector는 defer할 수 없다. Slack/GitHub에는 신규 credential resource나 빈 picker를 만들지 않고 기존 node graph 방식의 masked token/URL control을 제공한다.
- required parameter는 유효한 값 또는 `allow_unresolved` 정책 없이 완료할 수 없다. `forbidden`인 task에는 defer control을 표시하지 않고 backend도 defer 요청을 거부한다.
- optional parameter는 값이 아직 없으면 `skip`으로 건너뛸 수 있다. 기존 graph에 값이 있는 optional parameter를 빈 값으로 적용하면 `skip`으로 상태만 바꾸지 않고 `clear`로 실제 값을 제거해야 한다.
- Frontend는 자동 완료된 추천값의 `수정(set)`, 기존 optional 값의 `clear`, policy가 허용한 `defer`, 값이 없는 optional `skip`, `previous`를 명시적인 control로 제공한다. 기존 session의 active 추천에는 호환용 `confirm`을 제공할 수 있다. Required task에서는 `skip`과 `clear` control을 숨기거나 disabled로 표시하고 server도 같은 요청을 거부한다.
- task 상태는 `pending`, `active`, `completed`, `deferred`, `skipped`, `invalid`, `canceled` 중 하나다.
- completed task의 값을 변경하면 해당 node validation과 downstream suggestion을 다시 계산한다.
- 유효한 `set` decision은 `parameter_update` GraphMutation을 반환하고 현재 task를 유지한다. Frontend가 mutation을 적용하고 CDS 저장과 canonical graph hash/`updated_at` acknowledgement를 완료한 뒤에만 현재 task를 `completed`로 바꾸고 다음 task를 활성화한다.
- 안전한 자동 추천값은 canonical graph와 발급된 recommendation context가 일치하고 관련 structural operation이 `acknowledged`일 때 별도 decision 없이 task를 `completed`로 표시한다. 단, 새 LLM node의 Catalog 기본 `auto_model_routing=false`는 기능 비활성화 확인이 필요하므로 active 상태를 유지한다. 사용자가 미체크 값을 확인하면 `confirm`이 GraphMutation 없이 fingerprint와 canonical graph를 검증해 completed로 전환하고, 체크하면 `set -> parameter_update -> CDS save -> acknowledgement` 순서를 사용한다. Pending save/ack group은 완료 UI나 다음 task 진행을 열지 않으며 새 값을 task/session payload에 복제하지 않는다.
- `allow_unresolved` defer는 `parameter_update` GraphMutation을 반환하고 CDS 저장과 acknowledgement 뒤에만 task를 `deferred`로 전환한다. 값이 없는 optional `skip`은 graph 값이나 GraphMutation을 만들지 않고 operation id/task version 검증 뒤 task를 `skipped`로 전환해 다음 task를 활성화한다. 기존 optional 값 제거인 `clear`는 `parameter_update` GraphMutation으로 canonical node data에서 해당 값만 제거하고 CDS 저장과 acknowledgement 뒤에만 task를 `skipped`로 전환한다. Skipped task를 다시 열어 값을 설정하면 일반 `parameter_update`/CDS/acknowledgement 뒤 `completed`가 된다.
- Node `configuration_state`는 backend가 Catalog의 모든 required configuration을 기준으로 생성, set/defer/skip, Undo, 복구와 실행·배포 preflight마다 다시 계산한다. 하나라도 missing/deferred/invalid이면 `unresolved`, 모두 유효할 때만 `resolved`이며 client가 보낸 상태를 권위로 신뢰하지 않는다. Optional skipped parameter는 Catalog required가 아닌 한 unresolved 원인이 아니다.
- Parameter flow 전체 취소는 남은 task를 `canceled`로 닫고 이미 저장된 graph를 유지한다.
- Decision request는 client-generated operation id와 expected task version을 포함한다. Backend는 parent request row를 잠그고 target task/version과 action별 허용 상태를 확인한다. `confirm`은 active 자동 추천 task에만 허용한다. 값 수정 `set`과 기존 optional 값 제거 `clear`는 `active|completed|skipped|deferred|invalid` task에 허용하고 각각 acknowledgement 뒤 `completed`, `skipped`로 전환한다. `previous`는 현재 presentation task의 canonical 상태가 `active|completed|skipped|deferred`이면 persisted 상태를 변경하지 않고 이전 재편집 가능 task를 반환한다. `pending`은 active 전환 뒤 처리하고 `canceled`는 수정하지 않는다. 같은 operation 재시도는 상태 전이와 mutation을 반복하지 않는다. Full operations가 없는 `confirm|skip|previous|cancel`과 safe invalid 결과는 같은 canonical 결과를 반환하지만, `set|clear|defer` mutation 응답 유실은 `operation_payload_unavailable` 뒤 현재 task/version에서 새 operation id로 재입력한다. 다른 동시 decision은 `409 task_conflict`로 거부한다.
- 같은 operation id와 같은 payload를 재시도해도 GraphMutation 생성, task 전환, DB commit과 audit를 반복하지 않는다. `confirm|skip|cancel`과 Catalog validation으로 `invalid`가 된 `set`은 저장된 safe canonical 결과를 반환한다. Full operations를 발급한 `set|defer` 응답이 유실된 경우에는 최초 payload 대신 `operation_payload_unavailable`을 반환한다. 같은 operation id에 다른 payload가 오면 `409 task_conflict`로 거부한다. Parameter group 취소의 operation id는 같은 group/task/version이 확정될 때까지 유지하며, 응답이 유실되면 같은 id로 한 번 재시도한 뒤 canonical canceled 상태를 조회한다.
- 저장 실패, acknowledgement 유실 또는 stale graph에서는 task 완료 상태를 앞당기지 않으며 operation id와 canonical graph hash/`updated_at`으로 acknowledgement를 재시도·복구할 수 있어야 한다.
- Frontend는 parameter decision 결과가 확정되거나 session recovery가 끝날 때까지 같은 입력의 operation id를 유지한다. 결과와 session 조회가 모두 유실되면 같은 operation id로 재시도하고, recovery가 task를 다시 열면 다음 입력부터 새 operation id를 사용한다.
- active `task_id`가 바뀌면 text, boolean, selector, resource candidate, 검색어와 validation error를 포함한 local input draft를 초기화한다. Completed/skipped/deferred task를 다시 열 때 이전 raw value를 session에서 복원하지 않는다.

### DBP-FR-008 Graph-Aware Suggestions

- suggestion resolver는 current node까지 도달 가능한 upstream node의 catalog output만 사용한다.
- Selector suggestion은 opaque `suggestion_id`, kind, source node id, output key, canonical `value_selector`, 표시용 JSON path, value type, label과 safe description을 포함한다.
- Canonical selector는 `[source_node_id, output_key, ...nested_path]` 배열이며 JSON path는 같은 nested path를 사용자가 이해할 수 있게 표시한 server-issued metadata다. Client가 JSON path를 임의 selector로 재해석하지 않는다.
- 같은 branch에서 도달할 수 없는 output은 추천하지 않는다.
- condition, variable extraction, file extraction, webhook root payload의 runtime output 계약을 그대로 사용한다.
- 연결 가능한 output이 없으면 값을 만들지 않고 manual 입력과 Catalog policy가 허용한 defer만 제공한다.
- LLM은 임의 selector를 생성하지 않는다.

### DBP-FR-009 Parameter Card UX

- 하나의 Agent Builder 결과는 workflow 단위 설정 container 아래 Knowledge 선택, 자동 추천 확인과 수동 parameter 입력을 node별 card로 표시한다.
- 현재 node card 하나만 기본 확장하고 완료된 card는 요약 상태로 접는다.
- card는 node 이름, 필요한 이유, 완료 상태, parameter control을 표시한다.
- 모든 필수 Knowledge 선택과 parameter 확인 및 관련 graph 저장/acknowledgement가 끝나기 전에는 생성 완료를 표시하지 않는다. 조건을 모두 충족하면 같은 container에 명시적인 생성 완료 상태를 표시한다.
- completed task 편집을 열면 생성 완료 표시를 숨기고 `설정 수정 중` 상태를 표시한다. Result group은 active task 유무와 관계없이 수정 취소/닫기를 제공하고, 저장 실패 시 form과 입력값을 유지하며 acknowledgement 성공 뒤 form을 닫고 완료 상태로 돌아간다.
- 자동 추천 출처는 `user_request=사용자 요청에서 확인`, `existing_graph=기존 Workflow 설정 사용`, `upstream_selector=이전 노드 출력에서 연결`, `catalog_default=기본값 추천`의 안전한 문구로만 표시한다. 내부 enum, raw value와 secret은 표시하지 않는다.
- active 또는 client presentation `task_id`가 바뀌면 editor는 해당 node를 선택하고 Agent Builder panel을 제외한 가시 canvas 영역의 중앙으로 이동한다. Node와 주요 handle이 가리지 않는 범위에서 가능한 가장 큰 zoom을 한 번 적용하고 해당 card를 보이게 scroll한다.
- 같은 task의 validation/candidate 갱신과 사용자의 수동 viewport 조작 뒤에는 focus를 반복하지 않는다. 첫 Workflow Undo 직후 input control로 keyboard focus를 강제하지 않고 canvas Workflow Undo 문맥을 유지한다. `이전 항목` 이동은 새 card heading으로 접근성 focus를 옮긴다.
- 새 assistant result 또는 active task 변경 시 chat scroll은 최신 active content가 보이도록 이동한다.

### DBP-FR-010 Knowledge Selection Timing

- Knowledge 선택이 RDG node 추가·제거 또는 graph topology를 바꾸면 `before_graph`다.
- 선택이 이미 생성할 node의 binding만 바꾸면 `after_graph`다.
- `before_graph`는 통합 설정 container의 첫 단계로 graph 생성 전에 표시하고, `after_graph`는 graph 생성 뒤 같은 container의 다음 단계로 표시한다.
- 같은 Knowledge requirement에 legacy clarification selector와 direct-edit Knowledge card를 동시에 표시하지 않는다.
- `direct_edit_v1`의 Knowledge candidate는 `knowledge_resolution.candidates`에만 포함하고 `clarification_options`에 중복하지 않는다. 선택은 전용 Knowledge selection endpoint만 사용하며 message endpoint의 legacy `selected_knowledge_candidate(s)`는 거부한다. `pending_ack|completed` resolution에 대한 legacy/direct 교차 제출도 상태와 audit를 반복하지 않고 충돌로 닫는다.
- Catalog의 `llmNode.knowledgeBases`는 graph binding schema이며 direct-edit ParameterTask 대상이 아니다. direct-edit는 이를 일반 `resource_ref` 후보 picker로 표시하지 않고 `knowledge_resolution` card만 사용한다. 기존 direct-edit session에 남은 generic `knowledgeBases` task는 session 복구 시 제거하고 다음 실제 task 또는 완료 상태로 정리한다.
- `before_graph`가 unresolved이면 GraphMutation을 만들지 않는다.
- `after_graph`에서는 Knowledge 미선택 graph를 먼저 만들 수 있다.
- 사용자는 0개 이상의 Knowledge Base를 선택할 수 있다.
- `knowledge_resolution`은 candidate가 처음부터 0개여도 유지되는 상위 `resolution_id`를 제공해야 하며 frontend는 후보가 비어 있어도 empty-selection card와 CTD를 표시해야 한다.
- `before_graph`에서 선택하지 않은 상태를 제출하면 KB 없이 생성하겠다는 명시적인 empty selection으로 처리한다. 별도 no-KB 후보를 만들거나 다시 선택을 요구하지 않는다.
- `before_graph`는 선택된 Collection/KB가 0개이면 `Knowledge Base 없이 생성`, 하나 이상이면 `선택한 Knowledge로 생성`을 표시한다. `after_graph`는 0개이면 `Knowledge Base 없이 계속`, 하나 이상이면 `선택 적용`을 표시한다.
- 최초 structured plan은 KB와 무관한 base topology와 typed Knowledge placement를 함께 제공한다. 각 placement는 requirement id, `before_graph|after_graph` timing, target step id, `binding_only|insert_step` effect kind를 포함한다. `insert_step`은 Knowledge step id, upstream step id, downstream step id와 empty-selection bridge policy를 추가로 포함한다. Planner는 선택별 완성 graph를 만들지 않는다.
- 웹훅 기반 사내 문서 챗봇의 기본 base topology는 `webhook_trigger -> knowledge_backed_llm -> answer`이고, Knowledge 선택이 기존 LLM binding만 바꾸므로 `after_graph + binding_only`를 사용한다. 사용자가 Knowledge 선택에 따라 node 존재 또는 topology가 달라지는 조건부 구조를 명시한 경우에만 `before_graph + insert_step`을 사용한다.
- Backend는 placement의 step reference, Catalog capability와 selected/empty topology를 검증한다. 선택된 KB가 있으면 Catalog template으로 Knowledge 의존 node/data/edge를 만들고 여러 KB를 같은 requirement binding 목록에 연결한다. Empty selection이면 bridge policy로 Knowledge step을 생략하고 upstream/downstream을 연결하며 Knowledge 선택 뒤 자연어 요청이나 planner를 다시 호출하지 않는다.
- KB 없는 candidate graph가 catalog/schema/connection validation을 통과하지 못하면 일부 graph를 저장하지 않고 validation failure로 닫는다.
- 여러 Knowledge Base 선택은 각 id의 use 권한과 runtime eligibility를 검증해야 한다.
- `after_graph` Knowledge binding은 `knowledge_binding` GraphMutation의 CDS 저장과 canonical graph hash/`updated_at` acknowledgement 이후에만 선택 완료로 기록한다.
- Knowledge 저장 중에는 기존 card와 선택값을 유지하고 control만 잠근다. 선택 사용자 메시지는 save/acknowledgement 성공 뒤 확정한다. 저장 전 실패는 같은 card에서 같은 선택을 재시도할 수 있어야 하며 결과가 불명확하면 canonical session의 안전한 `messages`와 `knowledge_resolution`으로 `pending_ack|completed|unapplied`를 판정한다. `unapplied`는 같은 card를 다시 활성화하고, `pending_ack`는 reconciliation 동안 잠그며, `completed`는 stale clarification card를 닫고 다음 설정 단계로 진행한다. 자연어 요청, planner 또는 유실된 typed operations는 재실행하지 않는다.

### DBP-FR-010a Selected Knowledge Candidate Materialization

- Knowledge selection endpoint는 후보 발급 뒤 사용자가 선택한 opaque candidate handle을 새 추천 점수나 Top-K 결과로 다시 계산하지 않는다.
- 선택 처리 시 resolution에 발급된 전체 후보 집합을 유지하고 active organization, `use`/`route` 권한, lifecycle, runtime eligibility를 다시 검증한다. 현재 추천 순위 또는 표시 Top-K에서 밀려난 handle도 이 검증을 통과하면 materialize한다.
- 추천 탐색의 5,000개 내부 안전 상한은 발급된 handle 적용 재검증에 다시 적용하지 않는다. 제출 가능한 최대 20개 handle은 현재 추천 순위와 무관하게 발급 범위에서 찾아 재검증한다.
- 선택한 handle이 더 이상 권한 또는 runtime eligibility를 통과하지 못하면 graph mutation을 만들거나 저장하지 않고 validation failure로 닫는다.
- Collection/KB 선택은 순서 없는 집합으로 비교한다. 중복 제거와 handle 오름차순 canonicalization 뒤 같은 집합의 재시도는 멱등이고 실제 집합 변경만 conflict다.
- `before_graph` materialization은 placement의 `target_step_id`로 확정한 정확히 하나의 Knowledge-capable LLM node만 변경한다. target이 없거나 모호하면 다른 LLM에 복제하지 않고 validation failure로 닫는다.

### DBP-FR-011 Existing Capability Compatibility

- 기존 KB 후보 3개 높이·최대 20개 표시와 node capability allowlist는 재구현하지 않고 회귀 테스트로 보존한다.
- 기존 intent model 권한 검증과 generated model 추천은 ADR-0040 계약을 재구현하지 않고 회귀 테스트로 보존한다. 후보는 active organization의 valid credential, active chat model, provider 일치, verified relation과 사용자 credential `use` 권한을 모두 통과해야 한다. 같은 model/credential 관계가 중복이면 가장 낮은 relation priority 하나만 사용한다.
- Header와 새 generated LLM node는 같은 후보 집합과 `openai -> anthropic -> google`, provider별 최신 세대, 같은 세대 `general -> mini -> nano -> pro`, 같은 tier 기본형 -> 날짜/release snapshot -> preview -> latest 순서를 공유한다. Header에서 사용자가 선택한 planner model은 generated node에 복사하지 않고, 기존 LLM node의 model도 덮어쓰지 않는다.
- 정규화한 model ID로 확정되는 특수 목적 모델은 Agent Builder 후보에서 제외하고, 세대나 tier를 해석하지 못한 verified chat model은 임의 등급 없이 해당 provider의 해석 가능한 후보 뒤에 안정적으로 유지한다. LlamaParse는 chat model 미지원 disabled group이다.
- 최신 dev의 Mail capability인 `mail_search`, `gmail_reply_draft_create`, `mail_terminal_acknowledgement`를 direct-edit catalog와 GraphMutation에서 보존한다.
- Gmail 답장 초안 workflow는 durable Mail 검색, LLM, Gmail Draft, Mail terminal acknowledgement 순서를 유지한다. Mail terminal acknowledgement를 단독 생성하거나 Gmail Draft 앞에 배치하지 않는다.
- Mail credential은 자동 선택하거나 원문으로 graph에 넣지 않고 unresolved credential reference로 남긴다. Email send, reply-all, attachment 요청은 지원하지 않으며 HTTP node로 대체하지 않는다.
- Mail graph 생성과 parameter 설정 중 OAuth refresh, mailbox 조회, Gmail draft 생성, 읽음 처리 또는 전송을 수행하지 않는다.

### DBP-FR-012 Session Recovery

- session은 만료되지 않은 request의 redaction된 message/response 대화를 시간순으로, request status, protocol version, generation mode, safe operation envelope와 parameter task 상태를 복구할 수 있어야 한다.
- MBA-228은 단일 기능 PR에서 nullable `AgentBuilderSession.protocol_version` migration, null/`direct_edit_v1` mixed read와 request 없는 session 분류를 제공한다.
- 신규 direct-edit session은 `direct_edit_v1`을 저장하고 기존 null row는 backfill하거나 자동 변환하지 않는다. 기존 null Preview session은 `stale_protocol`로 복구해 safe 대화만 표시한다.
- `stale_protocol` 전환은 legacy Preview graph/draft/apply 정보를 복원하지 않는다. Redaction을 통과한 안전한 이전 대화는 읽기 전용으로 유지하고 재제출 안내를 표시하며, 신규 요청은 한 번 생성한 새 `direct_edit_v1` session에서 처리해 stale 전환을 반복하지 않는다.
- Frontend와 Gateway의 무중단 coordinated rollout, 배포 gate, 단계적 rollback과 image artifact 분리는 별도 배포 이슈의 범위이며 MBA-228 완료 조건이 아니다.
- Generation mode/source, request/proposal version, safe operation envelope, catalog version과 task 상태는 기존 `AgentBuilderRequest.response_payload`를 재사용하며 신규 table을 만들지 않는다. Full typed operations와 parameter 값은 저장하지 않는다. Repository는 nested JSON을 제자리 변경하지 않고 새 전체 payload 객체를 column에 재할당한다. Generation mode는 session column에 저장하지 않는다.
- 복구 시 `response_payload`의 `catalog_version`이 없거나 `2`이면 legacy v2 operation으로 stale 처리하고, `3`인 operation만 current catalog 검증으로 진행한다.
- 미완료 parameter group의 affected LLM 복구 범위는 현재 request의 모든 유효하고 `reverted`가 아닌 v3 operation envelope의 `affected_node_ids`와 기존 ParameterTask node id의 합집합이다. Canonical graph에 없는 node는 제거하고 관계없는 기존 LLM으로 범위를 넓히지 않는다.
- 실제 parameter 값은 session/request payload에 복제하지 않고 저장된 workflow graph에서 읽는다. 재진입 control은 Catalog mapping으로 graph의 현재 safe 값을 hydrate한다. 값이 없는 skipped/deferred task는 빈 control을 표시한다. Credential/resource reference는 현재 권한으로 조회 가능한 safe opaque reference만 표시하고 권한 상실·삭제된 reference의 ID/label과 raw secret은 노출하지 않는다.
- raw secret과 credential config는 session에 저장하지 않는다.
- transport 또는 5xx로 session 조회, pending request polling, acknowledgement 확인이 불명확해도 frontend는 local session pointer, 대화, 입력 draft, Knowledge 선택과 history boundary를 삭제하지 않는다. 같은 session context는 `1초 -> 2초 -> 4초` 간격으로 최대 세 번만 자동 확인한다.
- Gateway는 session/request 보존 기간과 별개인 4분 processing 기한을 적용한다. 기한을 넘긴 `processing` request는 canonical session 조회 또는 다음 request admission에서 `failed` terminal 상태로 원자 전환하고 pending gate를 해제한다. 늦게 끝난 처리 결과는 terminal request를 덮어쓰지 않는다.
- 원래 mutation 제출 흐름에서 acknowledgement 응답만 유실되면 같은 `operation_id`와 canonical metadata로 acknowledgement를 정확히 한 번 재시도할 수 있다. 두 번째 결과가 불명확해졌거나 복구·수동 확인 경로에서는 acknowledgement를 다시 발급하지 않고 session 조회만 수행한다.
- 세 번의 자동 확인 뒤에도 applied, unapplied 또는 terminal 상태를 확정하지 못하면 `결과 확인 필요`와 `다시 확인` control을 표시한다. 수동 확인은 같은 session 조회만 다시 시작하며 자연어 요청, planner, GraphMutation 또는 acknowledgement를 새로 발급하지 않는다.
- 성공한 session 응답이 동일한 `pending_request`를 반환하면 60초까지 정상 `계획 중` 상태에서 5초 간격으로 polling한다. 60초 이후에는 `평소보다 오래 걸리고 있습니다`를 표시하고 10초 간격으로 처리 제한시간까지 계속한다. Terminal 응답을 받으면 같은 `request_id`의 planning 표시를 교체한다. Transport 또는 5xx가 발생하면 자동 polling을 중단하고 `결과 확인 필요`와 수동 확인을 표시하며, 서버 처리가 계속된다고 오인하지 않도록 기존 planning card, 진행 banner와 `Workflow 계획 중` 상태를 숨긴다. Session pointer와 canonical 작업 context는 삭제하지 않는다.
- local session pointer 제거와 새 direct-edit session 전환은 `stale_protocol`, server가 명시한 session not found 또는 invalid session에만 허용한다.
- Client는 foreground request 응답과 request별 설정 card의 `mode_contract_version`을 session pointer와 함께 보존하고 각 후속 GET/POST에 해당 contract header를 사용한다. `mode_contract_mismatch`를 받으면 지원하는 contract인 경우 같은 GET을 한 번 다시 보내고, 지원하지 않거나 rollback 중이면 mode-free request cancel 또는 운영 drain으로 종료한다. 다른 contract로 quick 상태를 임의 projection하지 않는다.
- 최신 failed/canceled request가 이전 ready mutation을 현재 결과처럼 복구하면 안 된다.
- 복구된 mutation의 base graph hash 또는 expected `updated_at`이 current workflow와 다르면 stale로 표시하고 자동 적용하지 않는다.
- client가 이미 적용한 operation id를 다시 받으면 중복 적용하지 않는다.
- Full operations 응답이 CDS 저장 전에 유실되면 server는 operation을 재생하지 않고 기존 envelope를 `blocked/operation_payload_unavailable`로 닫는다. Initial/graph-edit/replace는 기존 request를 contract-neutral cancel해 terminal임을 확인한 뒤에만 request 재생성을 허용한다. Parameter decision은 parent request를 유지하고 현재 task/version의 새 operation id 재입력을 요구하며 server task/selection 상태를 완료로 복구하지 않는다.
- Session recovery는 최신 request row를 잠근 뒤 envelope 상태를 다시 확인한다. 저장 결과가 없는 `pending_apply|pending_save`만 차단하고 `pending_ack|acknowledged|blocked|reverted`는 변경하지 않으며 차단과 safe audit를 같은 transaction에서 확정한다.
- 차단된 `parameter_update`의 pending decision은 제거하고 같은 task/version을 다시 `active`로 연다. 일반 initial/graph-edit/replace group은 `blocked`로 닫는다. 다만 `before_graph` Knowledge 선택이 발급한 structural operation의 typed payload가 저장 전에 유실된 경우 연결 resolution은 `unapplied`로 되돌리고 같은 card에서 새 operation으로 재시도할 수 있어야 한다.
- CDS 저장은 끝났지만 acknowledgement 응답이 유실되면 persisted workflow graph와 safe envelope의 expected/saved hash, operation id, workflow `updated_at`으로 acknowledgement와 후속 상태를 복구한다.
- Commit 뒤 request를 다시 조회했을 때 task version, operation id와 상태가 그대로 복구되어야 하며 동시 decision으로 GraphMutation이나 next task가 중복 생성되면 안 된다.
- `reverted` history boundary는 모든 연결 ParameterTask/Knowledge resolution이 `canceled`인 상태로 복구한다. Parameter/Knowledge operation별 `reverted` 상태는 새 계약에서 만들지 않으며 Redo도 canceled 흐름을 자동 완료하거나 재활성화하지 않는다.
- 신규 session은 direct-edit protocol만 사용한다. 기존 Preview session은 `stale_protocol`로 복구하고 safe 대화만 표시하며 이전 preview/draft를 적용하거나 변환하지 않는다. Preview 결과는 parity characterization fixture로만 사용하며 활성 fallback으로 수정·확장하지 않는다.

### DBP-FR-013 Permission, Validation And Execution Boundary

- 모든 request는 인증 사용자와 `X-Organization-Id` 기반 active organization을 확정한다.
- 기존 workflow 수정은 workflow read/write 권한을 요구한다.
- 신규 workflow 생성은 app/workflow creation scope를 요구한다.
- Knowledge, model, credential reference는 각 resource 권한을 별도로 검증한다.
- 모든 Agent Builder CDS 저장은 final candidate graph에서 Catalog `resource_ref`/`credential_ref`, Knowledge/Collection binding, WorkflowNode `appId`/`workflowId`와 기타 resource-bearing field를 서버가 직접 추출해 `managed_reference|legacy_editor_connection|unknown`으로 분류한다. Server-owned policy registry는 field path·mutation kind·resource kind별 resolver, required relation과 최소 permission action을 정의한다. Workflow 저장에는 workflow `write`, Knowledge/Collection과 managed credential binding에는 대상 resource `use`를 요구하며 단순 reference에 대상 resource의 `read|write`를 일괄 요구하지 않는다. Target resource 자체를 변경하는 별도 operation만 그 resource의 `write`를 요구한다. Resolver는 같은 transaction에서 registry가 지정한 최소 action, 현재 organization, lifecycle과 relation을 재검증하며 client 목록이나 발급 시점 allow 결과를 권위로 사용하지 않는다. Policy/resolver 누락, 삭제·비활성·권한 회수·relation 변경과 unknown field는 graph와 audit 전체를 rollback한다. Slack/GitHub secret은 일반 ParameterDecision/GraphMutation이 아니라 기존 editor draft save adapter가 저장하며, Agent Builder operation envelope와 session/audit에는 원문을 포함하지 않는다.
- Backend는 저장 graph의 required configuration 전체에서 `configuration_state`를 다시 계산해야 한다. 하나라도 missing/deferred/invalid인 외부 action node는 저장할 수 있어도 server-side 실행·배포 preflight에서 차단해야 한다. Preflight는 catalog와 저장 graph만 검사하며 credential provider나 외부 API를 호출하지 않는다.
- `pending|active` ParameterTask 자체는 실행·배포 차단 근거가 아니다. 추천값이 canonical graph에 materialize돼 required configuration이 모두 유효하면 Agent Builder 사용자 확인이 남아 있어도 runtime readiness는 통과할 수 있다. 사용자 확인을 별도 admission gate로 만들려면 상위 실행·배포 정책을 별도로 변경해야 한다.
- generation, suggestion, parameter submission 중 workflow 실행, retrieval, 외부 action, credential 사용·변경을 수행하지 않는다.

### DBP-FR-014 Audit And Sensitive Data

- Agent Builder request, GraphMutation 발급, GraphMutation CDS 저장 결과, acknowledgement, parameter/Knowledge 상태 변경, permission/stale/validation 차단을 구분해 audit한다.
- Agent Builder GraphMutation CDS save와 persisted revert/redo save는 기존 `add_action_audit`를 같은 SQLAlchemy session에서 호출한다. Graph write와 audit insert 중 하나라도 실패하면 transaction 전체를 rollback하고 성공을 반환하지 않는다. 일반 editor autosync마다 신규 audit event를 추가하는 것은 이 기능 범위가 아니며 신규 audit outbox나 worker도 추가하지 않는다.
- audit에는 session/request/operation/workflow/node/parameter key와 safe reason만 기록한다.
- Planner 실패 진단은 allowlisted safe code인 `semantic_validation_failed:<codes>`, `schema_validation_failed`, `provider_response_invalid`, `provider_call_failed`, `runtime_loading_failed`, `extraction_failed`로만 축약한다. Unknown/raw exception과 provider 원문을 사용자 응답, audit, trace 또는 log에 반사하지 않는다.
- raw message 중 secret-like span, parameter value 원문, raw KB content, source path/url, credential config, provider raw response는 audit/trace/log에서 제외한다.
- planner와 repair usage/cost attribution은 DBP-FR-015를 따른다.

### DBP-FR-015 Planner And Repair Usage Attribution

- 최초 planner와 최대 한 번의 repair 호출을 attempt 1과 2로 분리하고, 각 provider 호출 전에 기존 `llm_usage_logs`에 `pending` 행을 예약한다.
- 실제 호출에 사용하도록 이미 권한 검증된 user, active organization, direct-edit primary workflow, model DB ID와 credential DB ID를 그대로 귀속하며 기록 단계에서 runtime을 다시 선택하지 않는다.
- Provider 호출 전 request의 유효 workflow, session workflow와 App의 현재 primary workflow가 모두 같아야 한다. 과거 workflow와 실행·배포·감사 기록은 보존하지만 일치하지 않는 scope의 Agent Builder provider 호출은 비용 발생 전에 차단한다.
- 예약 transaction은 request/session/App primary workflow, credential 유효 상태, active chat model, verified credential-model 관계, 사용자 credential use 권한과 active organization membership을 검증하고 당시 가격을 고정한다. 같은 attempt가 이미 예약됐거나 완료됐다면 provider를 다시 호출하지 않는다.
- Provider 응답을 받은 직후 schema/semantic validation 전에 token usage mapping만 raw 응답에서 분리해 latency와 함께 예약된 같은 행에 멱등 저장한다. Normalizer와 recorder에는 raw content/choices/provider 응답 전체를 전달하지 않는다. 응답을 받지 못했거나 검증 가능한 usage가 없으면 pending 행을 삭제하고 요청을 `INTENT_USAGE_RECORDING_FAILED`로 종료한다.
- Provider 호출 중 App primary pointer, model 가격 또는 credential/model lifecycle이 바뀌어도 예약 당시 workflow와 가격으로 같은 행을 완료한다. 삭제로 model/credential FK가 NULL이 된 경우에도 나머지 보존 billing fact가 같으면 완료와 같은 예약의 저장 재시도를 허용한다.
- 사용량 저장 성공을 확인할 수 없으면 pending 증거를 보존하고 `INTENT_USAGE_RECORDING_FAILED`로 요청을 안전하게 종료하며 provider를 다시 호출하지 않는다. 비용·token·호출 수·Top Model 집계는 success인 Agent Builder 행만 포함한다.
- 모델이나 credential이 삭제돼도 과거 token/cost와 user/organization/workflow 귀속은 유지하며 기존 관리 비용, workflow 예산과 워크플로우 화면의 workflow별 월 예상 비용 집계에 계속 포함한다.
- Agent Builder 전용 endpoint 또는 대시보드는 추가하지 않는다. 기존 관리 비용 응답과 워크플로우 화면의 workflow별 비용 응답·화면에는 workflow 실행 비용과 Agent Builder 비용 구분값을 additive하게 제공한다. 워크플로우 화면의 page-level 비용·추세·위험 요약은 표시하지 않는다. 사용자 메시지, prompt/context, provider 응답 원문과 credential secret은 사용량 행, 로그와 오류에 저장하지 않는다.

## 5. Non-Functional Requirements

### DBP-NFR-001 Cohesion

- intent planning, GraphMutation generation, CDS save coordination, parameter task planning, suggestion resolution, editor mutation은 독립 모듈이어야 한다.
- API endpoint와 React component는 orchestration 세부 로직을 직접 소유하지 않는다.

### DBP-NFR-002 Coupling

- frontend는 node type별 parameter 규칙을 switch 문으로 복제하지 않고 catalog-derived view model을 사용한다.
- backend graph generator는 React Flow component 구현에 의존하지 않는다.
- runtime output 변경은 catalog contract test를 통해 Agent Builder에 전파한다.

### DBP-NFR-003 Determinism

- 같은 structured plan, catalog version, base graph에 대한 GraphMutation과 parameter task 순서는 결정적이어야 한다.
- suggestion ranking의 동점 규칙을 고정한다.

### DBP-NFR-004 Accessibility And Layout

- 모든 parameter control은 label, error, keyboard focus를 제공한다.
- desktop Agent Builder panel은 왼쪽 경계를 마우스로 드래그하거나 키보드 방향키로 조절할 수 있어야 한다. 초기 50vw와 조절된 너비 모두 최소 360px부터 viewport 우측 여백 40px을 제외한 최대 범위로 제한하고 같은 화면 세션에서 panel을 닫았다 다시 열어도 조절값을 유지한다. Focusable separator는 현재·최소·최대 픽셀 너비를 `aria-valuenow`, `aria-valuemin`, `aria-valuemax`로 제공한다. mobile에서는 resize handle을 숨기고 기존 좌우 여백 안의 전체 너비를 유지한다.
- Agent Builder의 고정 배치 wrapper는 panel과 launcher 밖의 투명 영역에서 pointer event를 가로채지 않아야 한다. panel을 최소화하거나 닫은 뒤에도 하단 React Flow control을 클릭할 수 있어야 한다.
- card와 canvas focus는 mobile/desktop viewport에서 Agent Builder panel과 선택 node가 겹치지 않도록 가시 영역과 동적 zoom을 계산한다. 공간이 부족하면 zoom을 낮춰 node 식별과 주요 handle을 보장하고 panel control이 서로 겹치거나 화면 밖으로 잘리지 않아야 한다.
- 긴 label과 validation message가 container를 벗어나지 않아야 한다.

## 6. Release Gate

- PRD, architecture, data model, glossary와 ADR-0045/ADR-0046이 direct-edit와 GraphMutation/CDS 계약으로 정합해야 한다.
- catalog parameter schema와 runtime contract test가 통과해야 한다.
- direct graph apply/Undo, 두 생성 모드, KB timing, parameter card, secret boundary integration test가 통과해야 한다.
- local apply 후 CDS workflow 저장, canonical graph hash/`updated_at` acknowledgement, 저장 전 종료·재시도 integration test가 통과해야 한다.
- parameter decision, `after_graph` binding, `initial_graph`, `graph_edit`와 `replace_workflow` operation의 저장/acknowledgement·복구 integration test가 통과해야 한다.
- 모든 GraphMutation kind와 operation schema, 동시 편집 stale conflict, canonical save response와 acknowledgement idempotency API contract test가 통과해야 한다.
- Browser E2E D에서 acknowledged structure-only 다중 node operation을 Ctrl+Z로 persisted revert하고 reload 뒤 server base graph, task/Knowledge 취소와 Redo history 미복구를 확인해야 한다.
- 독립된 browser E2E B에서 Ctrl+Z 뒤 reload 전에 Ctrl+Shift+Z로 final graph를 CDS Redo 저장하고, 이후 reload로 graph 유지와 Agent Builder task 미복구를 확인해야 한다.
- legacy Condition/Variable clarification 결과는 characterization fixture로만 고정한다. Preview를 수정하거나 활성 fallback으로 유지하지 않고 catalog-derived ParameterTask/GraphMutation parity가 확인된 뒤 backend/frontend 분기를 함께 제거해야 한다.
- unresolved 외부 action의 실행·배포 preflight와 mutation audit/redaction integration test가 통과해야 한다.
- DBP-NFR-001~004가 Test Matrix에 명시적으로 매핑되고 실행 결과 또는 차단 사유가 기록되어야 한다.
- actual dev server에서 핵심 smoke flow를 확인해야 한다.
- Migration revision의 Alembic single head, disposable 기존 DB `upgrade head`, request 없는 null/direct session 복구와 additive schema 유지 동작을 검증해야 한다.
- Direct-edit cutover 뒤 legacy Preview session이 `stale_protocol`로 복구되고 이전 draft를 적용할 수 없다는 테스트가 있어야 한다.
- 과거 revision의 test 결과는 완료 근거로 재사용하지 않는다. 현재 revision/worktree에서 실행한 commit, command, test count와 environment가 기록된 결과만 release gate를 통과한 것으로 인정한다.
## 2026-07-14 Runtime Corrections

- `after_graph` Knowledge binding that updates an LLM node's `knowledgeBases` emits every graph reference as safe `{ "id", "name" }`. D binding with an ID alone is invalid and must not be issued.
- A task with an automatic safe recommendation is completed after structural acknowledgement and rendered collapsed. Opening `수정` hydrates the current canonical value into the typed control and keeps the other allowed candidates or Catalog options available; a changed value sends `set`.

- `guided_generate`에서 LLM node가 생성되면 graph 저장 acknowledgement 뒤 하나의 after-graph Knowledge 설정 카드가 표시되어야 한다. 기존 `configure_and_generate` 입력도 같은 흐름이다. 사용자는 여러 Knowledge Base 또는 빈 선택을 확정할 수 있다.
- Knowledge 후보 노출은 active organization의 use 권한 및 source lifecycle을 기준으로 한다. active ready version 부재는 후보 선택을 막지 않으며 실행 및 배포 preflight에서만 unresolved 상태를 차단한다.
- ParameterTask 또는 Knowledge 카드가 표시되는 동안에도 사용자는 다음 자연어 요청을 입력할 수 있다. 진행 중 API 요청과 CDS 저장 중에만 composer 제출을 막는다.
- Agent Builder graph mutation은 server auto-layout으로 계산된 좌표를 포함해야 하고, canonical workflow graph 저장 후 그 좌표가 유지되어야 한다.

## 2026-07-15 External Connection And Recovery Corrections

- Slack/GitHub의 `credential_ref` ParameterTask와 빈 후보 picker는 direct-edit에서 만들지 않는다. Catalog에 정의된 channel, message, action, repository와 PR 번호뿐 아니라 기존 graph 저장 방식이 지원하는 Token/URL secret도 순차 task로 제공한다. Secret은 masked input으로만 새 값을 받고 기존 원문은 hydrate하지 않는다.
- Mail/Gmail은 runtime이 기존 managed credential reference를 요구하므로 permission-filtered reference picker를 유지한다. raw credential 원문 입력은 Agent Builder 범위가 아니다.
- Mail 검색 node의 사용자 설정 항목인 `credential_id`, `keyword`, `sender`, `subject`, `start_date`, `end_date`, `folder`, `max_results`, `unread_only`, `mark_as_read`, `processing_mode`는 모두 Catalog ParameterTask로 제공한다. 생성 template이 미리 채운 값도 사용자 확인 전에는 완료 처리하지 않는다. `referenced_variables`, `configuration_state`, `_deferred_parameters` 같은 graph/runtime 내부 필드는 사용자 task로 노출하지 않는다.
- Gmail Draft와 Mail Acknowledge의 credential 및 selector도 Catalog task로 유지한다. 여러 필수 효과는 `variable_selector_list`로 확인한다. Graph 연결에서 안전하게 자동 추천된 selector는 canonical graph에 반영하고 structural acknowledgement 뒤 completed로 표시하며, 사용자는 완료 요약의 `수정`에서 다른 selector를 선택할 수 있다.
- Slack/GitHub의 기존 runtime 인증 동작은 변경하지 않는다. 향후 공식 관리 credential resource가 제품에 추가될 때 Agent Builder 연계 여부를 별도 결정한다.
- Slack의 전송 방식과 각 parameter 설명은 Catalog의 한국어 문구로 고정한다. API mode에서는 Bot Token, channel과 payload task를, Incoming Webhook mode에서는 Webhook URL과 payload task를 표시한다. Mode별 Bot Token/Webhook URL과 channel은 runtime/preflight 필수 설정이지만 사용자가 `나중에 설정`을 선택하면 `deferred`/`unresolved`로 저장하고 다음 task로 진행한다. 이 defer는 일반 optional `skip`이 아니며 test/run/deploy 차단을 해제하지 않는다. 전송 방식을 바꾸면 반대 mode에만 쓰이는 기존 secret과 channel 값을 canonical node data에서 제거하고 현재 mode의 다음 유효한 task를 활성화한다. `message`에는 생성 시 연결된 `{{result}}`가 이전 node의 `referenced_variables` 출력을 사용한다는 설명을 표시한다. 값이 없는 optional `blocks`와 `attachments`는 빈 `적용`을 `skip`으로 처리하고 완료 흐름을 막지 않는다. 기존 값 편집 중 빈 `적용`은 `clear`로 실제 graph 값을 제거한다.
- 기존 Slack `blocks`/`attachments` JSON 문자열은 Agent Builder 재편집 시 JSON array로 파싱해 typed control에 표시하고 제출 후 한 번만 문자열화한다. 잘못된 JSON 또는 array가 아닌 값은 unavailable로 표시하며 기존 graph 값을 자동 변경하지 않는다.
- Slack의 `message`, `blocks`, `attachments`는 개별 optional task지만 API/Webhook mode 모두 세 값 중 하나 이상이 유효해야 한다. 공백 message, 빈 array, invalid JSON과 `_deferred_parameters`에 포함된 값은 설정으로 인정하지 않으며 configuration 완료와 test/run/deploy preflight를 차단한다.
- 사용자가 일반 Node Detail에서 deferred parameter를 기존 값과 다른 유효한 값으로 직접 편집하면 해당 key만 `_deferred_parameters`에서 제거한다. 다른 field 편집, 같은 값 재전송, 빈 값·invalid 값과 autosync는 marker를 제거하지 않으며 최상위와 중첩 `subGraph` node에 같은 규칙을 적용한다. Gateway는 저장 graph의 `configuration_state`를 다시 계산하고 일반 editor 저장은 Agent Builder task/audit 상태를 바꾸지 않는다.
- 기존 Slack graph의 legacy `body.text|body.channel`은 실제 runtime payload나 channel로 승격하지 않는다. 실제 `channel` 또는 `message|blocks|attachments`가 없으면 configuration과 preflight는 unresolved로 판정하며 graph를 자동 변환하지 않는다.
- GitHub `pr_number` decision은 1 이상의 integer만 받는다. GraphMutation 적용 시 GitHub runtime schema가 요구하는 10진 문자열로 canonicalize하고, JSON 왕복과 CAS 저장 뒤에도 같은 문자열을 유지한다.
- GitHub API Token은 모든 action의 runtime/preflight 필수 설정이며 direct-edit masked input task로 제공한다. 사용자가 `나중에 설정`을 선택하면 unresolved defer로 다음 task를 진행할 수 있지만 test/run/deploy는 token 입력 전까지 차단한다. GitHub `comment_pr`의 `comment_body`는 Catalog 조건부 필수 parameter다. `get_pr`에서는 숨기고 preflight에서 요구하지 않으며 `comment_pr`에서는 ParameterTask와 test/deployment preflight가 모두 빈 본문을 차단한다. Action 저장과 acknowledgement 뒤에는 canonical graph로 Catalog task를 재계획하여 `comment_body`를 즉시 `pending|active`로 다시 열고 갱신된 group을 응답한다.
- `action`이 없는 기존 GitHub graph는 runtime 호환을 위해 Catalog hydration과 preflight에서 `get_pr`로 읽는다. 이 호환 판정은 graph를 자동 저장하지 않고, 명시적인 invalid action에는 적용하지 않는다.
- Mail Acknowledge runtime data는 graph materializer가 서버에서 파생한 `configuration_state`를 허용하되 그 외 미정의 field는 계속 거부한다.
- File Extraction selector는 runtime schema의 `[{name, value_selector}]` 형태로 materialize하며, Slack `blocks`와 `attachments`는 검증된 JSON 값을 node runtime이 사용하는 JSON 문자열 형태로 저장한다.
- `설정하며 생성`에서 현재 operation이 영향을 준 LLM node는 `model_id`, `output_format`, `system_prompt`, `user_prompt`, `assistant_prompt`, 이전 node 출력 연결, `citationDisplayMode`, `task_group=model_routing`인 `auto_model_routing`을 기본 설정 ParameterTask로 제공한다. 세 prompt 중 하나 이상은 runtime 필수 조건이고, JSON Schema는 JSON 출력에서만 보이는 선택 입력이다. 신규 Agent Builder LLM의 `citationDisplayMode=detailed`는 수정 가능한 완료 추천으로 제공한다. 필드가 없는 기존 LLM에는 `detailed`를 자동 주입하지 않고 legacy `hidden` 해석을 보존한 채 선택 task를 제공한다. Knowledge Collection/KB는 전용 Knowledge 흐름을 유지한다. Planner/user request, 기존 graph, upstream selector와 Catalog 기본값의 안전한 추천은 structural acknowledgement 뒤 완료 요약으로 제공하고 수정할 수 있다. 단, Catalog 기본 추천이 `auto_model_routing=false`이면 미체크 상태로 사용자 확인을 받아야 하며 확인 전에는 전체 Workflow 설정을 완료 처리하지 않는다.
- Session read는 완료된 persisted ParameterGroup도 현재 Catalog와 `node_id + parameter_key`로 병합한다. 기존 task 상태/version/`stable_order`를 유지하고 신규 task는 기존 task 뒤에 Catalog 순서로 추가한다. 신규 또는 새로 필수가 된 task에 사용자 입력이 필요할 때만 첫 대상 task를 active로 만들고 group을 다시 active로 열며, canceled group은 열지 않는다. 반복 read는 중복 task를 만들지 않고 `agent_builder_task=false` parameter는 제외한다.
- `auto_model_routing` set은 `parameter_update` GraphMutation, CDS CAS save와 acknowledgement를 사용한다. `auto_model_routing=true`는 graph에만 설정하고 Agent Builder 처리 중 model-routing policy/refresh/cohort API나 workflow 실행을 호출하지 않는다.
- 라우팅 파라미터 계약이 변경될 수 있는 동안 `fallback_model_id`, `model_routing_refresh_every_runs`, `model_routing_validation_budget_usd`, `model_routing_max_cohorts`는 Agent Builder ParameterTask와 복구된 설정 목록에서 제외한다. 기존 graph 값, runtime schema와 고급 LLM Routing control은 제거하거나 초기화하지 않는다.
- `구조만 생성`에서는 Routing ParameterTask를 만들지 않는다. Routing 미설정 LLM의 안내 action은 대상 node의 Routing control을 열어야 하며 focus만 수행하고 끝나면 안 된다. 이 navigation은 routing graph를 자동 변경하지 않는다.
- LLM 기본 설정 task와 session 복구는 GraphMutation의 `affected_node_ids`로 범위를 제한한다. 관계없는 기존 LLM에는 task를 추가하지 않는다. 현재 Catalog의 필수 task가 누락된 영향 대상 group은 완료 session도 identity 기준으로 병합해 다시 열며, 같은 reconciliation을 반복해도 task와 audit을 중복 생성하지 않는다.
- `설정하며 생성`의 `auto_model_routing` task가 terminal이고 전체 workflow 설정이 완료된 뒤에는 기존 LLM Routing control을 여는 고급 설정 action을 제공한다. Fallback, cohort 생성·수정, 수동 refresh와 persisted policy 상태는 Agent Builder ParameterTask로 복제하지 않는다.
- 모든 canonical recovery 경로는 editor-only edge handle display number를 다시 부여한다. server graph와 CAS hash는 이 metadata를 제외한다.
- candidate task는 candidate id와 `reference_value`를 모두 이용해 기존 graph 값을 hydrate한다. 어떤 허용 candidate와도 대응하지 않는 값만 unavailable state로 표시하며, 대체 후보 또는 policy-allowed defer 전에는 완료 처리하지 않는다.

### MBA-275 Direct-Edit And Execution Consistency

- `set` decision은 Catalog v3의 parameter type, validation, `sensitivity`와 `defer_policy`를 재조회한 뒤에만 graph patch를 만든다. `credential_ref`와 `resource_ref`는 서버가 현재 organization과 권한을 검증한 canonical reference만 저장한다.
- 일반 text/JSON/reference decision이나 자연어 message에서 secret-like 값이 확인되거나 secret detector가 실패하면 fail-closed하고 기존 `400 invalid_decision`으로 거부한다. Slack/GitHub `secret` parameter는 safe task metadata와 masked control로 발급하지만 원문을 ParameterDecision으로 제출하지 않는다. 조작된 raw secret decision은 `secret_forbidden`으로 거부한다. 거부된 입력의 원문은 graph, session/request payload, parameter task, audit, trace, log 또는 오류 message에 저장·반사하지 않는다. 일반 Catalog validation issue는 HTTP 거부가 아니라 `status=invalid`, `reason=catalog_validation_failed`인 task 결과로 저장·반환한다.
- WorkflowNode의 실행 필수 reference는 runtime target을 선택하는 `appId`다. `workflowId`는 선택 metadata이며, `appId`를 직접 변경하면 서버는 선택된 App의 canonical Workflow로 이 metadata를 정규화한다. `workflowId`를 직접 변경하거나 이미 정합한 pair를 검증할 때는 같은 organization, 각 resource 권한, `App.workflow_id == Workflow.id` 관계를 모두 통과해야 저장된다. 실패 시 부분 mutation을 남기지 않는다.
- Agent Builder draft CAS는 row lock을 획득한 뒤 DB 최신 Workflow row를 refresh하여 `updated_at`과 graph hash를 비교한다. stale이면 기존 `stale_graph` 409로 종료하고 최신 graph를 덮어쓰지 않는다.
- Catalog required configuration의 server-derived preflight는 `external_read`, `external_write`, `local_execution`을 모두 포함한다. test, run, deployment와 schedule은 같은 unresolved 판정을 사용하며, client가 보낸 `configuration_state`는 권위로 사용하지 않는다.
## Hierarchical Knowledge Selection

- Agent Builder는 Collection과 하위 KB를 계층으로 표시해야 한다.
- Collection 선택은 동적 Collection routing, 하위 KB 선택은 direct KB binding으로 처리해야 한다.
- Collection route 권한만 있는 사용자는 별도 KB use 권한이 없는 하위 KB identity를 볼 수 없어야 한다.
- 동일 KB가 여러 Collection에 표시되면 모든 위치에서 하나의 `selection_key` 상태를 공유해야 한다.
- Collection parent 선택은 현재 표시 가능한 전체 하위 KB를 함께 선택하고 Collection handle과 하위 KB handle을 모두 제출해야 한다. 일부 하위 KB만 남기면 parent는 indeterminate와 `선택 수/전체 수`를 표시하고 Collection route 선택은 해제한 채 남은 KB만 직접 고정해야 한다. 빈 선택과 다중 선택을 지원해야 한다.
- 선택 제출 시 서버는 opaque handle의 권한과 operational 상태를 재검증해야 한다.
- 추천 card 제출은 해당 resolution에 발급된 opaque handle만 허용한다. 발급 뒤 후보가 stale이면 서버는 저장된 structured request로 후보 계층만 다시 계산하고 같은 card를 최신 목록으로 교체하며 이전 Collection/KB 선택을 비워야 한다. `pending_ack|completed` resolution은 stale 갱신이나 재제출을 허용하지 않는다. 이때 planner, 원래 자연어 요청, GraphMutation과 workflow 저장을 다시 실행해서는 안 된다.
- 활성 `after_graph` target의 Node Detail에서 직접 선택한 KB/Collection은 추천 화면 Top-K에 포함되지 않았어도 허용한다. 서버는 real resource ID를 현재 organization의 opaque handle로 변환한 뒤 권한, lifecycle과 operational 상태를 독립적으로 재검증해야 한다.
- 선택 후 planner 또는 원래 자연어 요청을 다시 호출해서는 안 된다.
- Runtime은 direct KB와 Collection child KB를 합집합으로 만들고 동일 KB를 한 번만 검색해야 한다.
- Collection/KB 후보 상한은 권한/lifecycle 필터, 전체 후보 점수 계산과 안정 정렬 뒤에 적용해야 한다.
- 화면 응답은 Collection 최대 20개와 고유 KB 최대 20개로 제한하며 내부 후보 탐색과 권한·점수 계산 범위는 이 표시 상한으로 먼저 자르지 않는다. 내부 고유 KB 안전 상한은 5,000개이며 표시 상한 적용을 미루는 경로에서도 무제한 조회로 바뀌면 안 된다.
- Collection-linked 후보와 Collection에 속하지 않은 direct KB 후보는 같은 5,000개 내부 안전 예산을 사용해야 한다. 표시 가능한 Collection이 있으면 적격 direct 결과에 최대 20개이자 작은 예산에서는 절반 이하인 bounded result slot을 먼저 배정한다. Direct 조회는 안정 정렬된 page를 권한·lifecycle·readiness 검사하며 result slot을 채우거나 공유 평가 예산이 소진될 때까지 진행하고, 실패한 후보도 평가 예산에서 차감한다. 실제 평가한 direct 후보 수를 제외한 나머지만 Collection-linked 후보에 사용한다. Direct 조회는 표시 가능한 Collection membership을 제외하며 두 경로에서 평가·점수 계산한 고유 KB 합계는 5,000개를 넘지 않아야 한다.
- 동일 점수는 safe label 오름차순(없는 label은 마지막), opaque handle 오름차순으로 정렬하며 source tier/availability를 별도 tie-break로 중복 적용하지 않는다.

## Test 실행 및 secret 경계 정합성

- Slack/GitHub Catalog `secret` definition은 Agent Builder card와 response에 safe task metadata로 포함하고 masked input으로 새 값을 받는다. 기존 원문과 opaque reference는 hydrate하지 않는다. 새 입력은 인증된 Workflow node secret-write API로만 보내며, 서버는 암호화된 immutable revision을 만든 뒤 opaque reference만 반환한다. Raw secret을 넣은 조작된 ParameterDecision 요청은 `secret_forbidden`으로 거부한다. Planner, 일반 chat, GraphMutation/Agent Builder API 응답, task/session, workflow graph, deployment/version snapshot, audit, trace와 log에는 원문 또는 ciphertext를 저장하거나 반사해서는 안 된다.
- 일반 text/JSON/reference control과 자연어 message의 secret-like 값은 계속 fail-closed로 차단해야 한다.
- Agent Builder save/acknowledgement와 Workflow test preflight는 같은 workflow save coordinator에서 직렬화되어야 하며, test stream 시작 전 Agent Builder 저장 대기 또는 persisted history boundary의 `acknowledged=false`가 감지되면 test를 자동 실행하지 않고 사용자의 재시도를 요구해야 한다.
- Agent Builder save는 coordinator lock 획득 직후와 canonical draft 조회 직후 active workflow identity를 재검사해야 한다. 시작 workflow와 달라졌으면 mutation, save, acknowledgement와 rollback을 수행하지 않고 새 workflow 상태를 보존해야 한다. Lock 때문에 건너뛴 autosync는 최신 dirty snapshot 하나로 합쳐 같은 workflow가 active일 때만 lock 해제 뒤 한 번 저장해야 한다.
- Test preflight는 editor가 clean이어도 server canonical graph와 local snapshot을 비교해야 한다. 불일치하면 최신 metadata를 local graph에 결합하거나 자동 병합하지 않고 저장과 실행을 차단해야 한다.
- `operation envelope not found`는 권한 오류나 일반 저장 실패가 아니라 Agent Builder session/canonical draft 확인이 필요한 recoverable 충돌로 구분해야 한다. Acknowledged result graph hash와 saved workflow `updated_at`이 canonical draft와 모두 같을 때만 applied이며, 결과가 확정되기 전에는 test를 실행해서는 안 된다.
- Workflow 실행 status와 observability는 presentation state로만 유지하고 canonical graph, autosync dirty state와 Workflow Undo/Redo history에 포함해서는 안 된다.

## Secret 입력 경계와 이전 task action 정합성

- Slack Bot Token, Slack Incoming Webhook URL과 GitHub API Token의 Catalog definition은 Agent Builder task/card와 mode별 masked input으로 제공해야 한다. 기존 원문은 표시하지 않고 새 입력으로 교체하며 Node Detail 강제 이동으로 대체하지 않는다. 일반 Node Detail 기능과 LLM Routing 설정 이동 자체는 유지한다.
- Slack Bot Token, Slack Incoming Webhook URL과 GitHub API Token의 masked 입력창 및 현재 active token task의 `나중에 설정` 버튼은 삭제하거나 숨겨서는 안 된다. Secret 처리 정책 변경만으로 이 입력 흐름을 Node Detail 이동, 외부 설정 안내 또는 credential picker로 대체할 수 없다.
- `이전 항목`으로 표시한 task는 canonical active task ID와 달라도 해당 task의 typed input과 허용 decision action을 제공해야 한다.
- 이전 optional task에 canonical graph 값이 없으면 `건너뛰기`를 제공하고 `skip`으로 처리해야 한다. 기존 일반 값이 있으면 `값 지우고 건너뛰기`를 제공하고 `clear`의 CAS save와 acknowledgement가 끝난 뒤에만 task를 `skipped`로 전환해야 한다. Secret task는 이전 task presentation 대상이 될 수 있지만 raw 기존 값을 hydrate하지 않고 빈 masked control을 제공한다.
- Required task와 `confirmation_required=true` task에는 일반 `건너뛰기` 또는 `값 지우고 건너뛰기`를 제공해서는 안 된다. Catalog가 `allow_unresolved`로 선언한 required task에는 별도 `나중에 설정` defer를 제공할 수 있으며, 이는 required 계약이나 preflight 차단을 해제하지 않는다. 이전 task로 이동하는 행위 자체는 graph, canonical task status/version, 다음 active task와 Workflow history를 변경해서는 안 된다.

## Direct Knowledge 입력 정합성

- `direct_edit_v1` message request의 구형 Knowledge 선택 필드는 `HTTP 422`와 `invalid_request`로 거부하고 전용 Knowledge 선택 화면을 사용하도록 안내해야 한다.
- 활성 `after_graph` resolution의 target LLM에서 사용자가 Knowledge를 직접 변경하면 Agent Builder 전용 selection endpoint, 권한 재확인, CAS 저장과 acknowledgement를 사용해야 한다.
- 직접 node 설정의 target은 canonical Knowledge placement와 일치해야 한다. 선택 resource는 추천 Top-K 포함 여부가 아니라 현재 organization, 권한, lifecycle과 operational 상태를 기준으로 materialize해야 한다.
- `direct_edit_v1` Knowledge 제품 UI는 `collections`와 `ungrouped_kbs` 계층 계약만 사용한다. Flat `candidates`는 과거 session의 안전한 읽기 호환 데이터로만 보존하며 별도 선택 목록으로 렌더링하거나 제출하지 않는다. 계층 데이터 없이 flat candidate만 도착하면 안전한 오류를 표시하고 Knowledge 선택 제출을 차단한다. 계층과 flat 데이터가 함께 있으면 계층 UI만 표시해 후보를 중복 노출하지 않는다.
- Agent Builder provider timeout option은 외부 provider payload에서 제거하되 실제 transport timeout에 적용한다. Gemini intent 요청은 전달된 90초 제한을 사용하고 값이 없는 일반 Gemini chat 요청은 기존 60초 기본값을 유지한다.
- Agent Builder 저장 또는 acknowledgement 확인 중에는 card와 node 설정 양쪽의 Knowledge 제출을 모두 차단해야 한다.
- request 취소 payload에도 full typed operations, raw graph, parameter 원문, raw Knowledge metadata와 secret을 저장해서는 안 된다.
## Workflow Node Secret Reference Boundary

- Slack Bot Token, Slack Incoming Webhook URL과 GitHub API Token의 masked 직접 입력은 Agent Builder 제품 경로에 유지한다. `defer_policy=allow_unresolved`인 active task에는 `나중에 설정`을 유지하며 credential picker 또는 Node Detail 이동으로 대체하지 않는다.
- 원문은 authenticated secret-write 요청에서만 Gateway로 전달한다. Workflow graph, draft/version/deployment snapshot, ParameterTask/session, GraphMutation, audit, trace와 log에는 `workflow-node-secret://<opaque-id>` reference만 저장한다.
- Gateway는 active organization과 Workflow write 권한을 다시 확인하고 원문을 versioned keyring으로 암호화한 immutable revision을 만든다. 교체는 새 revision을 만들며 기존 deployment의 reference를 암묵적으로 바꾸지 않는다.
- Draft 저장과 deployment preflight는 reference 형식뿐 아니라 현재 organization/workflow/node id/node type/parameter key와 active status의 정확한 소유권을 일괄 검증한다. 알 수 없거나 다른 node에서 복제된 reference는 저장·배포 전에 거부한다.
- Node 복제와 붙여넣기는 Slack Bot Token/Webhook URL 및 GitHub API Token reference를 새 node data에서 제거하고 나머지 일반 설정은 보존한다. 새 node는 자체 secret-write command로 새 reference를 받아야 한다.
- Runtime은 provider I/O 직전에 organization/workflow/node type/parameter key/status를 검증하고 짧은 DB session으로 reference를 해석한다. 실패 시 외부 I/O 전에 fail-closed한다.
- 기존 plaintext graph는 legacy migration 대상이다. Draft read 변환은 Workflow row lock을 획득한 뒤 최신 graph를 다시 읽어 수행하고, 동시 CAS save의 최신 변경을 덮어쓰지 않는다. Draft read 또는 신규 deployment 전에 reference로 전환하며 신규 write는 plaintext 표현을 다시 만들지 않는다.
