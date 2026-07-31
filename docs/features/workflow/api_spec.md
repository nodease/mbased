# Workflow API Spec

Status: Draft

## Endpoints

| Method | Path | Description | Auth |
| --- | --- | --- | --- |
| GET | `/api/v1/workflows/{workflow_id}/draft` | canonical workflow draft와 graph hash/`updated_at`을 반환한다. 아직 graph가 저장되지 않은 신규 workflow도 빈 `nodes`/`edges`와 기본 viewport를 반환한다. | workflow read 권한 |
| POST | `/api/v1/workflows/{workflow_id}/stream` | 테스트 실행 스트리밍 이벤트를 반환한다. 기존 구현을 사용한다. | workflow execute 권한 |
| GET | `/api/v1/workflows/{workflow_id}/runs` | 저장된 workflow run 목록을 조회한다. `page`, `limit`, optional `status`, `trigger_mode`로 기준 실행 후보를 좁힌다. | workflow read 권한 |
| GET | `/api/v1/workflows/{workflow_id}/runs/{run_id}` | 저장된 workflow run 및 node run을 조회한다. TestSidebar 복원과 실행 비교는 이 기존 상세 API를 사용한다. | workflow read 권한 |
| GET | `/api/v1/workflows/{workflow_id}/runs/{run_id}/llm-traces` | 선택한 실행의 LLM node 비용·토큰·지연 정보를 조회한다. 실행 비교는 raw prompt가 아닌 safe usage summary만 사용한다. | workflow read 권한 |
| GET | `/api/v1/workflows/{workflow_id}/nodes/{node_id}/execution-logs` | 현재 노드가 실행된 workflow run 목록을 최신순으로 조회한다. 목록 row에 필요한 node-level preview를 포함한다. | workflow read 권한 |
| GET | `/api/v1/workflows/{workflow_id}/nodes/{node_id}/execution-logs/{run_id}` | 선택한 workflow run 안의 현재 노드 input/output/trace/usage 상세를 조회한다. | workflow read 권한 |
| GET | `/api/v1/deployments/{deployment_id}/run-info` | 내부 실행 화면용 safe deployment metadata를 반환한다. | 로그인 + workflow execute 권한 |
| POST | `/api/v1/deployments/{deployment_id}/run` | active deployment snapshot을 current user execution subject로 실행한다. | 로그인 + workflow execute 권한 |

Client 내부 route:

| Method | Path | Description |
| --- | --- | --- |
| POST | `/stream-api/workflows/{workflow_id}` | Next.js route handler가 Gateway `/api/v1/workflows/{workflow_id}/stream`으로 SSE를 proxy한다. Production server에서는 server-only `API_URL`이 필수이며 `NEXT_PUBLIC_API_URL`로 fallback하지 않는다. 비프로덕션에서 `API_URL`이 없을 때만 `http://127.0.0.1:8000`을 사용하고, 선택한 URL의 trailing `/api/v1`은 제거한다. Cookie와 `X-Organization-Id`, `X-Request-Id`, `X-Correlation-Id` 같은 safe context header만 전달한다. |

## Conversation Memory Internal Target Contracts

Workflow test stream은 별도 계약 전 Conversation Memory session을 자동 생성하지 않는다. Target runtime은 node value와 `RuntimeDataDependencyEnvelope`를 함께 전달하고 Condition/Switch/Loop의 active control dependency를 final output까지 보존한다. Main/summary/query-embedding provider 호출은 [LLM Credentials API Spec](../llm-credentials/api_spec.md#target-provider-execution-capability-contract)이 소유하는 opaque `ProviderExecutionCapability` identity/revision을 사용한다. Root와 nested LLM의 internal binding은 Loop-only structured `container_path + node_id`이며 Loop child가 만든 trusted execution control에서만 파생한다. Query embedding의 raw query/vector와 이 contract의 capability token, credential principal, digest 또는 raw scope를 public HTTP request/response에 노출하지 않는다.

## Request And Response Models

### Canonical workflow draft read

`GET /api/v1/workflows/{workflow_id}/draft`는 저장 graph와 함께 server-calculated
`graph_hash`, DB `updated_at`, `workflow_id`를 반환한다. 신규 workflow의 DB graph가
`null`이거나 빈 object여도 Client가 canonical base를 별도로 추측하지 않도록 다음 최소
graph contract를 materialize한다.

```json
{
  "nodes": [],
  "edges": [],
  "viewport": {"x": 0, "y": 0, "zoom": 1},
  "workflow_id": "00000000-0000-0000-0000-000000000000",
  "graph_hash": "sha256",
  "updated_at": "ISO-8601"
}
```

저장 graph에 field가 존재하면 해당 값을 그대로 반환한다. 이 정규화는 malformed node나
edge payload를 빈 graph로 대체하지 않으며, 이후 save의 CAS·graph validation을 우회하지
않는다.

### MBA-233 LLM Knowledge reference graph contract

LLM node `data`는 다음 두 configured reference list를 함께 가질 수 있다.

```json
{
  "knowledgeBases": [
    {
      "id": "00000000-0000-0000-0000-000000000000",
      "name": "직접 선택 KB"
    }
  ],
  "knowledgeCollections": [
    {
      "id": "00000000-0000-0000-0000-000000000000",
      "safeLabel": "사내 문서"
    }
  ]
}
```

- Field 부재는 빈 목록과 같다. Direct-only legacy graph는 그대로 유효하다.
- 각 목록은 최대 20개다. 21번째는 `knowledge_reference_limit_exceeded`로 거부하며
  server와 Client 모두 silent slicing을 하지 않는다.
- Item은 canonical UUID string `id`와 해당 type의 display field만 허용한다.
  `knowledgeBases.name`은 legacy 호환을 위해 빈 문자열을 허용하지만 string이어야
  하고, `knowledgeCollections.safeLabel`은 optional string이다. Present display field는
  255자 이하이고 control character를 포함할 수 없다.
- Unknown item field, non-object item, duplicate field shape, malformed/non-canonical UUID는
  fixed validation code와 safe field path로 거부한다. Error는 item value, label과 raw
  graph를 echo하지 않는다.
- Display field는 Builder snapshot이며 authorization/routing/audit/trace input이 아니다.
  Duplicate reference는 stored graph에서 자동 삭제하지 않고 runtime request에서 첫
  canonical occurrence만 사용한다.

`PUT /api/v1/workflows/{workflow_id}/draft`, Agent Builder apply-save와 graph를 저장하는
optimizer/model-routing path는 위 structural contract와 Knowledge API의 save-time
reference authorization을 모두 적용한다. Direct execute/stream은 structural validation을
통과하고 current invocation audience로 MBA-232 resolver를 호출한다.

Deployment preflight result는 기존 safe summary에 additive
`knowledge_collection_count_bucket`과 `candidate_budget_limited`를 포함할 수 있다.
Reason/action은 fixed allowlist만 사용하며 Collection/child UUID, label, exact hidden count,
permission/source detail을 반환하지 않는다. Malformed/over-limit graph와 anonymous
private/source-public-exposure 위반은 active publish에서 non-downgradable blocker다.

### Agent Builder GraphMutation CAS draft save

일반 editor와 Agent Builder는 같은 optimistic-concurrency 저장 경계를 사용한다. 모든 저장은 current canonical metadata에서 얻은 `expected_graph_hash`와 `expected_updated_at`을 포함하며, Agent Builder가 발급한 operation은 같은 payload에 추가 `mutation_context`를 포함한다.

```json
{
  "nodes": [],
  "edges": [],
  "viewport": {"x": 0, "y": 0, "zoom": 1},
  "features": {},
  "envVariables": [],
  "runtimeVariables": [],
  "expected_graph_hash": "sha256",
  "expected_updated_at": "ISO-8601",
  "mutation_context": {
    "operation_id": "uuid",
    "action": "apply",
    "expected_base_graph_hash": "sha256",
    "expected_workflow_updated_at": "ISO-8601",
    "catalog_version": 3
  }
}
```

Gateway는 모든 save에서 workflow row를 write lock으로 조회하고 active organization/write 권한을 재확인한다. Endpoint 진입 시 권한을 통과했더라도 lock 뒤 권한이 회수되면 `403`으로 닫고 graph, features, task 상태와 audit을 변경하지 않는다. Current canonical graph hash와 `updated_at`이 공통 기대값과 모두 일치해야 한다. Agent Builder save는 추가로 request graph의 canonical hash가 persisted safe operation envelope의 `expected_result_graph_hash`와 같은지 검증한 뒤 catalog/connection/schema validation을 확인한다. Full typed GraphMutation operations는 DB에 저장하거나 이 endpoint에서 재생하지 않는다. Agent Builder GraphMutation graph write와 기존 `add_action_audit`의 canonical audit insert는 같은 SQLAlchemy session과 transaction에서 확정하며 둘 중 하나라도 실패하면 전체를 rollback한다. 일반 editor autosync는 공통 CAS를 통과하지만 이 계약만으로 autosync마다 신규 audit event를 만들지 않는다. 이 경계에 신규 audit outbox나 worker를 추가하지 않는다.

성공 응답:

```json
{
  "status": "success",
  "workflow_id": "uuid",
  "operation_id": "uuid",
  "graph_hash": "sha256",
  "updated_at": "ISO-8601",
  "canonical_deferred_parameters": [
    {
      "node_path": ["loop-node-id", "nested-node-id"],
      "parameter_keys": ["parameter_key"]
    }
  ]
}
```

`canonical_deferred_parameters`는 저장된 전체 graph와 중첩 `subGraph`에서 서버 Catalog 검증 뒤 남은 deferred key를 graph root부터의 `node_path`별로 반환한다. 같은 node id가 서로 다른 subgraph scope에 있어도 path가 다르면 별도 node로 취급한다. Client는 일반 Node Detail 편집값이 비어 있지 않다는 이유만으로 marker를 제거하지 않고, 이 projection을 저장 응답의 canonical 결과로 반영한다. 특정 entry의 빈 `parameter_keys`는 해당 path node의 기존 marker를 제거한다.

Client는 저장 응답의 `workflow_id`가 현재 active Workflow와 같을 때만 projection, dirty 상태와 live editor history를 현재 화면에 반영한다. 저장 도중 다른 Workflow로 전환되면 늦게 도착한 응답은 해당 Workflow의 canonical metadata와 cache에만 반영하고 새 active Workflow의 node, marker, dirty/history를 변경하지 않는다.

Canonical graph hash는 persisted nodes/edges를 stable id와 object key 순으로 정렬한 JSON의 SHA-256이며 node position/data는 포함하고 viewport는 제외한다. 현재 Workflow model에 없는 version/revision 값을 응답에 추가하지 않는다. Expected hash 또는 `updated_at`이 다르면 graph를 쓰지 않고 `409 stale_graph`를 반환한다. Silent overwrite, 자동 merge와 강제 덮어쓰기는 허용하지 않는다. Mutation context 없는 일반 editor save도 같은 CAS를 통과하고 canonical metadata를 반환하지만 Agent Builder acknowledgement 대상은 아니다.

Canonical draft GET 응답도 실제 persisted `workflow_id`, server-calculated `graph_hash`, DB `updated_at`을 반환한다. Agent Builder save/acknowledgement, 일반 autosync, version 복원, test 전 저장, Undo/Redo와 응답 유실 복구는 frontend의 같은 canonical metadata 상태를 공유하고 성공한 GET/POST마다 갱신한다. Canonical 재조회는 pending Agent Builder history를 초기화하지 않는 비파괴 동기화로 처리한다. Out-of-band 저장 뒤 stale metadata를 계속 사용하거나 일반 autosync의 `409 stale_graph`를 조용히 무시하지 않는다.

Dirty editor의 test preflight GET은 local edit base의 canonical metadata를 대체하지 않는다. Server hash 또는 `updated_at`이 local edit base보다 앞서거나 canonical graph가 local snapshot과 다르면 draft POST와 test stream을 모두 시작하지 않는다. Graph와 `features.noteNodes`는 Agent Builder/일반 save 모두 같은 recursive canonical projection과 note schema 검증을 거치며 잘못된 `noteNodes`는 raw validation detail을 노출하지 않는 `422 workflow.features_invalid`로 반환한다.

Graph를 변경하는 `POST /workflows/{workflow_id}/llm-nodes/{node_id}/model-routing/bootstrap`, `PATCH .../model-routing/policy`, `PATCH .../cost-optimizer/apply`, `PATCH .../cost-optimizer/apply-recommendations`도 request body에 `expected_graph_hash`와 `expected_updated_at`을 필수로 포함한다. Gateway는 write lock 뒤 공통 CAS를 검증하고 graph와 연관 bootstrap/policy/candidate 상태를 한 transaction으로 저장한다. 각 API는 성공 응답에 canonical `graph_hash`와 `updated_at`을 반환하며 frontend와 실험 client는 직전 draft read/write 응답의 canonical metadata를 다음 mutation에 전달한다. 기대값이 다르면 부가 상태와 graph를 모두 쓰지 않고 `409 stale_graph`를 반환한다.

Acknowledged Agent Builder persisted Undo는 최초 `initial_graph`/`graph_edit`/`replace_workflow` operation id를 단일 history boundary로 사용한다. 후속 `parameter_update`/`knowledge_binding` acknowledgement는 새 history entry를 만들지 않고 boundary의 최신 final graph hash와 `updated_at`만 갱신한다. 완료 상태의 첫 Undo는 `completed|skipped|deferred` 중 최대 stable order task를 client presentation에서 표시할 뿐 persisted task 상태와 graph를 바꾸지 않으므로 이 endpoint를 호출하지 않는다. Parameter UI가 열린 상태의 다음 Undo 또는 task가 없는 완료 상태의 첫 Undo는 같은 endpoint에 `action="revert"`, boundary operation id, 최신 final graph hash와 current `updated_at`을 전달하고 request graph에는 실행 전 전체 graph를 넣는다. 성공하면 graph와 audit를 같은 transaction에 저장하고 boundary를 `reverted`로 전환하며 모든 ParameterTask/Knowledge resolution을 `canceled`로 닫는다. `parameter_update`/`knowledge_binding` operation id의 개별 revert는 validation failure로 거부한다. Reload 전 전체 Redo는 `action="redo"`와 같은 boundary operation id를 사용해 client memory의 final graph를 CAS 저장하되 canceled task/Knowledge 상태는 변경하지 않는다. 그 뒤 다시 Undo하면 parameter 재진입 없이 같은 boundary를 즉시 revert한다. 동일 operation/action/candidate/expected CAS context 재시도는 중복 write, 상태 전환과 audit 없이 같은 canonical graph hash/`updated_at`을 반환한다. Redo stack과 ParameterTask 재진입 표시는 reload 뒤 복구하지 않는다.

### Unresolved external-action preflight

Workflow test/run과 deployment create/activate는 저장 graph의 catalog required configuration 전체에서 `configuration_state`를 server-side로 다시 계산한다. 하나라도 missing/deferred/invalid인 외부 action node가 있으면 실행 또는 배포 전에 safe node id/type과 machine reason만 포함한 validation error로 차단한다. Client가 저장한 상태값은 권위가 아니며 이 검사 자체는 credential 사용이나 Slack/GitHub/HTTP/Mail 외부 호출을 수행하지 않는다.

### Workflow run actor compatibility

Workflow run list/detail 또는 node execution log가 run actor를 포함하는 경우 `user_id`는 `UUID | null`이다. Null은 canonical schedule claim에서 내부 입력 `schedule`이 저장 계약 `trigger_mode="scheduler"`로 정규화된 system execution에서만 허용한다. Client는 null을 App creator로 대체하지 않고 actor를 표시하는 화면에서는 `System`으로 표현한다. Manual/API/webhook 등 기존 user-attributed run의 non-null 계약은 유지한다.

Schedule claim id, external-effect 내부 identity, provider-visible idempotency key와 outcome review state는 public workflow API response에 추가하지 않는다. Internal Worker correlation은 user-visible output이나 raw durable trace에 포함하지 않는다.

### 1. 실행 편의성

- 이번 UI 변경은 신규 API를 추가하지 않는다.
- 프론트는 기존 스트리밍 이벤트를 사용한다.
  - `workflow_start`: `{ run_id }`. 실행 결과 복원용 식별자만 포함하며 input/output/credential은 포함하지 않는다.
  - `node_start`: `{ node_id }`
  - `node_finish`: `{ node_id, node_type, output, latency_ms, total_tokens, total_cost }`
  - `workflow_finish`: 최종 workflow output
- Gateway는 각 SSE event를 실제 줄바꿈 두 개(`\n\n`)로 끝나는 record로 전송한다. 문자 `\\n\\n`을 본문에 넣어 다음 event와 같은 JSON record로 합쳐지게 해서는 안 된다.
  - `error`: `{ message, node_id?, code?, retryable? }`. 기존 오류는 선택 필드를 생략할 수 있지만, MBA-190의 `external_effect.result_unavailable`, `external_effect.identity_conflict`, `external_effect.outcome_unknown`은 `code`와 `retryable=false`를 반드시 포함한다.
- Gateway는 `X-Organization-Id`가 전달된 테스트 실행 요청에서 active organization membership을 검증하고, 해당 organization이 workflow의 organization과 다르면 scope 밖 resource로 보고 `404`로 숨긴다. Header가 없는 legacy 호출은 기존 workflow row organization 기준 permission check를 유지한다.
- `node_finish` 이벤트의 node-level summary 표준 필드:
  - `latency_ms`: 노드 실행 소요 시간. 서버/엔진 기준 millisecond 단위 값.
  - `total_tokens`: 노드 실행에서 사용한 전체 토큰 수. 토큰 사용이 없는 노드는 null 또는 0을 반환할 수 있다.
  - `total_cost`: 노드 실행에서 발생한 비용. 비용 집계가 없는 노드는 null 또는 0을 반환할 수 있다.
- 프론트는 노드별 토큰/비용/소요 시간을 `node_finish` 표준 필드에서 우선 읽는다.
- `node_finish.latency_ms`가 없으면 프론트는 `node_start` 수신 시각과 `node_finish`/`error` 수신 시각의 차이를 fallback으로 계산할 수 있다.
- `node_finish.total_tokens` 또는 `node_finish.total_cost`가 없으면 해당 값은 `-`로 표시한다. `output.usage`나 `output.cost`를 표준 경로로 간주하지 않는다.
- 화면 완료 시간은 프론트가 테스트 실행 시작 상태로 전환된 시각과 `workflow_finish` 또는 최종 오류 처리 시각의 차이로 계산한다. 이 값은 API response 필드가 아니며 DB에 저장하지 않는다.
- 서버 실행 시간은 백엔드/엔진이 기록한 workflow-level duration을 사용한다. 현재 저장 기준은 `workflow_runs.duration`이며, 단위는 초다.
- `workflow_finish` 이벤트가 workflow-level summary를 제공하는 경우 프론트는 다음 필드를 우선 사용한다.
  - `duration`: 서버 실행 시간. `workflow_runs.duration`과 같은 초 단위 값.
  - `total_tokens`: 서버가 집계한 전체 토큰 사용량.
  - `total_cost`: 서버가 집계한 전체 비용.
- `workflow_finish` 이벤트에 workflow-level summary가 없으면 프론트는 `node_finish.latency_ms` 합산값을 서버 실행 시간 fallback으로 표시한다. 노드 latency도 없을 때만 `-` 또는 `기록 없음`으로 표시한다. 화면 완료 시간은 계속 프론트에서 계산한다.
- 전체 비용과 전체 토큰은 `workflow_finish`가 제공하는 workflow-level summary 값을 우선 사용하고, 없으면 노드별 값의 합산으로 fallback한다.

Example `workflow_finish` event data with server summary:

```json
{
  "run_id": "run-123",
  "output": {
    "answer": "처리 완료"
  },
  "duration": 3.4,
  "total_tokens": 8420,
  "total_cost": 0.0842
}
```

Client-only screen completion summary example:

```json
{
  "screen_completion_duration_ms": 8600
}
```

`screen_completion_duration_ms`는 API response가 아니라 프론트 UI 상태에서 계산되는 값이다.

TestSidebar restore contract:

- Client는 `testRun=<workflow_run_id>`와 optional `testNode=<node_id>`만 같은 workflow의 editor/report URL query에 보관한다.
- 브라우저 새로고침 또는 보고 화면에서 editor로 돌아온 뒤 Client는 `GET /api/v1/workflows/{workflow_id}/runs/{testRun}`을 호출한다.
- Gateway는 기존 workflow read permission을 적용한다. 권한이 없거나 해당 workflow에 속하지 않는 run은 복원하지 않는다.
- `workflow_id`가 UUID가 아닌 editor 초기 placeholder나 malformed 값이면 Gateway는 DB UUID cast를 시도하지 않고 `404 Workflow not found`로 resource hiding한다.
- `WorkflowNodeRun.duration`은 초 단위이며 Client는 표시 전에 millisecond로 변환한다. `trace_metadata`는 노드 상세의 safe model-routing 설명을 복원하는 데만 사용한다.
- `workflow_start`는 durable run 기록 생성보다 먼저 도착할 수 있다. Client는 초기 `404` 또는 `running` 응답을 실패로 바꾸지 않고, 점차 길어지는 제한된 간격으로 재조회한다. terminal run을 정상 복원한 뒤에만 해당 URL run을 복원 완료로 고정한다.
- 재시도 한도를 넘겨도 기존 실행을 `failure`로 덮어쓰지 않는다. Client는 기록 준비 지연 안내와 명시적 재시도 action을 표시한다. 브라우저 앞으로/뒤로가기로 `testRun` query가 바뀌면 Client는 새 URL을 다시 읽고, `testRun`이 제거된 경우 이전 복원 결과를 초기화한다.

TestSidebar execution comparison contract:

- 기준 실행 후보 목록은 `GET /runs`의 서버 필터를 사용한다. Client가 raw input/output 문자열을 검색 인덱스로 만들지 않는다.
- Client는 사용자가 선택한 `baseline_run_id`로 상세와 LLM trace를 조회하며 최신 실행을 자동 고정하지 않는다.
- 비교 대상 실행은 stream의 `workflow_start.run_id`로 식별한다. 상세 로그 반영이 지연되면 Client는 동기화 중 상태를 표시하고 기존 기준 실행을 변경하지 않는다.
- 양쪽 실행은 같은 `workflow_id`의 read permission 경계를 통과해야 한다. 다른 workflow의 run id는 `404`로 숨긴다.
- 노드별 비용·토큰·지연은 node run output/trace metadata와 LLM trace safe summary를 조합하되 credential id와 raw prompt를 표시하지 않는다.
- LLM trace endpoint의 `404` 또는 빈 목록은 화면에 `LLM trace 기록 없음`으로 표시하고 node run 비교를 유지한다. `403`, `5xx`, 네트워크 오류는 node run 비교를 중단하지 않되 `비교 근거 일부를 불러오지 못함` 경고를 표시한다. 이 경우 모델 라우팅·토큰·비용 근거 일부가 누락될 수 있다.

Example `node_finish` event data with node-level summary:

```json
{
  "node_id": "llm-triage",
  "node_type": "llmNode",
  "output": {
    "text": "{\"approvalRequired\": true}"
  },
  "latency_ms": 3571,
  "total_tokens": 361,
  "total_cost": 0.001964
}
```

### 2. 노드 조작 편의성

- 노드 상세 편집 화면의 3패널 리사이즈는 API request/response를 변경하지 않는다.
- 패널 폭과 비율은 workflow graph, node data, deployment snapshot에 저장하지 않는다.
- 패널 비율 영구 저장이 필요해지면 사용자 preference API를 별도 이슈로 정의한다.

### 3. 워크플로우 조작 편의성

- 왼쪽 노드 패널의 `뒤에 추가` 액션은 클라이언트 graph 편집 동작이다.
- `뒤에 추가` 결과 graph는 기존 workflow draft 저장 API를 통해 저장된다. 별도 노드 추가 API, edge 연결 API, 자동 정렬 API를 추가하지 않는다.
- 자동 생성 node와 edge는 기존 workflow graph node/edge schema를 사용한다.
- Backspace/Delete 키 노드 삭제와 자동 재연결은 클라이언트 graph 편집 동작이다.
- 삭제 결과 graph는 기존 workflow draft 저장 API를 통해 저장된다. 별도 삭제 API나 재연결 API를 추가하지 않는다.
- 자동 생성 edge는 기존 edge schema를 사용한다.

### 4. 노드 실행 기록 패널 추가

- 이번 UI는 node_id 기준 실행 기록 목록/상세 API를 추가해 사용한다.
- 목록 API는 workflow run 전체 목록이 아니라, 현재 `node_id` 실행 기록이 포함된 run만 최신순으로 반환한다.
- 목록 API query parameter:
  - `limit`: 한 번에 가져올 최대 row 수. 기본 20, 최대 100.
  - `cursor`: 다음 페이지 조회용 cursor. offset pagination보다 cursor pagination을 우선한다.
  - `status`: optional. `success`, `failed`, `running` 등 node run 상태 필터.
  - `q`: optional. input/output/error preview 검색어.
  - `from`, `to`: optional. 실행 시각 범위 필터.
- 목록 API 응답은 다음 shape를 따른다.
  - `items`: node execution log summary array
  - `next_cursor`: 다음 페이지 cursor. 더 이상 없으면 null 또는 생략.
- node execution log summary는 다음 값을 포함한다.
  - workflow run id/status/started_at/finished_at
  - workflow run 전체 latency 또는 시작/종료 시각 기반 소요 시간
  - workflow run 전체 token/cost summary가 있으면 표시
  - 현재 node_id의 node run status/latency/input/output/error preview
  - 현재 node_id의 LLM usage total_tokens/total_cost/model/provider가 있으면 표시
- preview 필드는 목록에서 빠른 식별을 위해 사용하는 짧은 문자열이다. full input/output은 상세 API에서만 반환한다.
- 상세 API는 선택한 run 안의 현재 node_id에 해당하는 다음 값을 반환한다.
  - node run input/output/error/status
  - trace payload의 redaction-safe metadata
  - llm usage log의 model/provider/token/cost/latency
- `가장 최신 로그 기록 불러오기`는 현재 node_id 기록이 포함된 가장 최신 workflow run을 선택한다.

Example summary response:

```json
{
  "items": [
    {
      "run_id": "run-123",
      "node_run_id": "node-run-456",
      "workflow_status": "success",
      "node_status": "success",
      "started_at": "2026-07-03T09:00:00Z",
      "finished_at": "2026-07-03T09:00:03Z",
      "latency_ms": 842,
      "total_tokens": 1240,
      "total_cost": 0.0123,
      "model_name": "gpt-4o-mini",
      "provider": "openai",
      "input_preview": "휴가 정책 알려줘...",
      "output_preview": "연차는 입사일 기준...",
      "error_preview": null
    }
  ],
  "next_cursor": null
}
```

Example detail response:

```json
{
  "run_id": "run-123",
  "node_run_id": "node-run-456",
  "workflow_summary": {
    "status": "success",
    "started_at": "2026-07-03T09:00:00Z",
    "finished_at": "2026-07-03T09:00:03Z",
    "latency_ms": 3000,
    "total_tokens": 8420,
    "total_cost": 0.0842
  },
  "node_summary": {
    "status": "success",
    "latency_ms": 842,
    "total_tokens": 1240,
    "total_cost": 0.0123,
    "model_name": "gpt-4o-mini",
    "provider": "openai"
  },
  "input": {
    "query": "휴가 정책 알려줘"
  },
  "output": {
    "answer": "연차는 입사일 기준..."
  },
  "error": null,
  "metadata": {
    "trace_id": "trace-789"
  }
}
```

### 5. 인증 내부 챗봇 실행

이 endpoint의 소유 계약은 [Deployment API Spec](../deployment/api_spec.md)을 따른다. `internal_chatbot` run-info는 secret과 graph snapshot을 제외한 safe metadata만 반환하고, run은 current user를 `execution_subject`로 전달한다. 서버는 챗봇 memory mode를 강제하고 인증 `conversation_id`를 deployment와 execution subject 기준으로 namespace 처리한다. 공개 `/api/v1/run-public/{url_slug}`의 `chatbot`은 execution subject 없이 anonymous public-only 경계를 유지한다.

## MBA-219 Configuration Preflight

`POST /api/v1/workflows/{workflow_id}/execute`, `POST /api/v1/workflows/{workflow_id}/stream`과 Compare는 workflow execute 권한, Cost Optimizer candidate는 기존 write/builder 권한과 active organization 검증 뒤 Celery task publish 전에 같은 authenticated configuration preflight를 수행한다. Stream은 SSE response와 Redis subscribe를 시작하기 전에 검사한다. Compare/Cost Optimizer는 base graph가 blocked이면 variant/candidate task를 만들기 전에 request-level error로 종료한다.

Managed node는 `mailNode`, `gmailDraftNode`, `mailAcknowledgeNode`, `slackPostNode`다. 최상위 graph, Loop `subGraph`와 WorkflowNode target snapshot에 current user audience/principal을 전달한다. LLM, HTTP와 GitHub는 이번 범위에서 기존 runtime-authoritative 정책을 유지한다. External node가 registry에 없거나 `implemented=false`이면 실행 전에 fail-closed한다.

Blocking response:

```json
{
  "detail": {
    "error": {
      "code": "workflow.configuration_preflight.blocked",
      "message": "Workflow configuration preflight blocked execution",
      "reason_code": "mail_credential_unavailable",
      "required_actions": ["select_available_mail_credential"],
      "preflight": {
        "status": "blocked",
        "audience": "authenticated_user",
        "safe_summary": {
          "blocked_reason": "mail_credential_unavailable",
          "affected_node_count": 1,
          "affected_kb_count_bucket": "0"
        }
      }
    }
  }
}
```

- HTTP status는 `409 Conflict`다.
- Missing, revoked, cross-organization과 permission-denied Mail credential은 모두 `mail_credential_unavailable`이다.
- Response는 credential ID/name/email, token, Slack Webhook URL/channel/message/body, raw target와 raw exception을 포함하지 않는다.
- Preview는 durable permission audit을 만들지 않는다. Create/toggle 및 authenticated execution enforcement에서 확인된 same-organization `use` 거부만 resource별 정확히 한 번 `permission.denied`로 기록한다.
- Preflight는 provider에 연결하거나 secret을 복호화하지 않는다.
- Runtime은 resource scope/status/use, secret, egress와 provider 계약을 다시 검사한다.
- Node `position` 누락·비유한/비숫자 좌표, 비어 있거나 누락된 edge `id`, malformed edge, dangling endpoint, cycle, 진입점/isolation 오류와 합산 node 1,000개, edge 5,000개 또는 Loop subgraph depth 16 초과는 resource lookup과 task publish 전에 `workflow_graph_invalid`로 차단한다. 최상위 graph는 명시적 trigger/start node 하나, Loop body는 incoming executable edge가 없는 실행 진입점 하나를 요구한다.
- Mail data의 `title`, folder, `max_results`, boolean, filter/date/reference와 processing mode는 Worker schema와 같은 타입·범위로 검사한다.
- Compare와 Cost Optimizer candidate는 preflight를 통과한 server-bound graph에서 파생하며 task 직전에 WorkflowNode target을 다시 binding하지 않는다. Recommendation verification의 완료된 동일 Idempotency-Key safe response는 workflow 권한과 active organization scope를 확인한 뒤 현재 node/graph preflight와 task 없이 replay한다.

### MBA-275 Cross-Surface Admission Contract

- server는 Catalog required configuration에서 `external_read`, `external_write`, `local_execution`을 포함한 unresolved 상태를 재계산한다. client의 `configuration_state=resolved` 위조는 통과하지 않는다.
- `WorkflowNode`는 runtime target인 `appId`만 required configuration이고 `workflowId`는 선택 metadata다. 누락된 `workflowId`는 runtime model에서도 빈 metadata로 정규화한다. `loopNode`는 `subGraph`가 필수이며, `loop_key`가 없거나 빈 값이면 mapped input의 첫 배열을 선택하는 기존 runtime fallback을 허용한다. Loop body의 implicit entry selector만 `loop.item|index`, 상위 실행 입력, Loop까지 방향성 선행 경로가 있는 현재 graph node output과 명시적 mapped input을 직접 사용할 수 있다. Body 후속 노드가 inherited source를 직접 참조하거나 downstream nested Loop로 이를 재전달하거나 input mapping이 parent graph의 후행·형제 source 또는 상위 output 계약에 없는 값을 가리키면 해당 설정을 unresolved로 차단한다. Depth 16을 넘는 subGraph는 configuration admission에서도 fail-closed한다.
- test/run/deployment의 기존 `409 workflow.configuration_preflight.blocked` 응답과 safe `reason_code` 계약을 유지한다. 새 public error code를 만들지 않으며 raw node data, reference, secret과 내부 exception을 반환하지 않는다.
- schedule dispatch는 publish 전에 공통 configuration과 DB 기반 target/policy preflight를 수행한다. Worker는 claim의 locked canonical root identity와 공통 configuration을 다시 검사한 뒤 budget 평가와 `mark_running()`으로 진행하고, blocker는 기존 `configuration_preflight_blocked`로 canceled 처리한다. Publish 뒤 바뀔 수 있는 WorkflowNode/KB/credential 상태와 권한은 runtime authoritative gate가 다시 검사한다.

### MBA-283 Generic HTTP Runtime Egress Contract

- 신규 endpoint나 request/response field는 추가하지 않는다. Generic HTTP node의 URL, method, header, JSON body와 timeout 저장 shape는 유지하되 runtime은 public HTTP/HTTPS 80/443 destination만 허용한다.
- URL userinfo/fragment, 비허용 scheme/port/method, hop-by-hop/proxy 제어 header와 private·local·metadata destination은 provider 호출 전에 차단한다. Response와 stream error에는 전체 URL/query/header/body/resolved IP 또는 내부 exception을 포함하지 않는다.
- Policy 거부는 기존 `external_effect.invalid_prepared_request`, 전송 전 일시 연결 실패는 기존 `external_effect.connection_failed` 또는 retry control, 전송 뒤 response 상실·크기 초과·검증 실패는 기존 `external_effect.outcome_unknown` 계약을 사용한다. 신규 public error code를 만들지 않는다.
- 정상 3xx/4xx/5xx를 포함한 완전한 응답은 기존 `status`, `data`, `headers` output을 유지한다. Redirect는 자동 추적하지 않으므로 3xx의 `Location` destination으로 두 번째 request를 보내지 않는다.

### MBA-356 Fixed SaaS Outbound Contract

- 신규 public endpoint나 graph field를 추가하지 않는다. GitHub `get_pr|comment_pr`와 Slack API/webhook의 기존 request, node output과 safe error 계약을 유지한다.
- Runtime은 server-owned GitHub/Slack operation과 endpoint만 guarded transport에 전달한다. Client가 provider origin, HTTP method, redirect, proxy 또는 timeout 상한을 변경할 수 없다.
- Origin·DNS·peer·response 상한 거부는 raw URL, credential, request/response body 또는 provider exception 없이 기존 safe provider/external-effect 오류로 변환한다. 요청이 처리됐을 수 있는 실패는 전송 전 실패로 낮추지 않는다.

## Errors

### 1. 실행 편의성

- workflow execute 권한이 없으면 403으로 거부한다.
- workflow가 active organization scope 밖이면 404로 숨긴다.
- 스트리밍 중 노드 오류가 발생하면 `error` 이벤트에 `node_id`가 포함될 수 있으며, 프론트는 해당 노드를 실패로 표시한다.
- MBA-219 configuration blocker는 task 또는 SSE가 시작된 뒤 `error` event로 보내지 않고 시작 전 `409 workflow.configuration_preflight.blocked` JSON으로 반환한다.

### MBA-190 외부 부수효과 안전 종료

- MBA-190은 신규 endpoint를 추가하거나 성공 응답의 node output schema를 바꾸지 않는다.
- WorkflowNode target deployment binding은 server-owned internal command/snapshot metadata이며 request field나 response field가 아니다. Client가 같은 이름의 graph metadata를 보내도 서버가 제거·재계산하고 draft/deployment graph 조회, node output, SSE, 오류 응답에 반환하지 않는다.
- `execution_id`, `node_invocation_id`, `effect_input_digest`, provider-visible key와 key fingerprint는 어떤 MBA-190 API/SSE payload에도 추가하지 않는다.
- 외부 작업의 성공 사실은 저장돼 있지만 안전하게 재사용할 node 결과가 없는 중복 전달은 provider를 다시 호출하지 않고 기존 실행 실패 경로로 종료한다.
- 같은 external effect identity로 재진입했지만 provider, operation, contract version, 두 지원 수준 또는 `effect_input_digest`가 최초 attempt와 다르면 provider나 저장 결과를 사용하지 않고 `external_effect.identity_conflict`로 종료한다.
- Provider effect 생성 여부가 불명확하고 frozen replay capability가 `unsupported|unknown`이거나 replay deadline이 끝났으면 provider를 다시 호출하지 않고 `external_effect.outcome_unknown`으로 종료한다.
- Frozen decision이 `replay_same_key`여도 기존 Celery retry budget이 소진되면 ledger decision을 `stop`으로 닫고 기존 safe error 원인은 보존하며 API에는 `external_effect.outcome_unknown`, `retryable=false`를 반환한다. `retry_before_effect` budget 소진은 provider를 호출하지 않고 기존 allowlisted safe provider failure를 사용한다.
- 스트리밍 실행의 `error` 이벤트는 최소 고정 code인 `external_effect.result_unavailable`, `external_effect.identity_conflict`, `external_effect.outcome_unknown` 중 해당 값, digest를 만들 수 없는 준비 실패의 `external_effect.prepare_failed`, claim/repository 대기 재시도 소진의 `external_effect.claim_wait`, 또는 `failed_before_effect + stop`의 기존 allowlisted provider/configuration code를 전달할 수 있다. 모든 경우 safe `message`, terminal 오류의 `retryable=false`와 해당 node를 식별할 수 있을 때 `node_id`를 함께 포함한다.
- 스트리밍이 아닌 Celery task는 non-retryable 외부 effect 오류를 `{"status":"error","error":<safe payload>}` 내부 JSON-safe result로 반환한다. `POST /api/v1/workflows/{workflow_id}/execute`, `POST /api/v1/deployments/{deployment_id}/run`, `POST /api/v1/run/{url_slug}`, `POST /api/v1/run-public/{url_slug}`은 기존 실패 status `500`과 top-level `detail` envelope을 유지하고 그 안에 `code`, `message`, `retryable=false`, optional `node_id`를 mapping한다.
- `POST /api/v1/workflows/{workflow_id}/compare`는 기존처럼 HTTP `200` 안에서 해당 variant를 `status=failed`로 두고 기존 `error` 문자열에 safe message, additive `error_detail`에 safe payload를 넣는다. `POST /api/v1/workflows/{workflow_id}/llm-nodes/{node_id}/cost-optimizer/compare`도 HTTP `200` experiment response 안의 해당 candidate에 기존 `error_message`와 additive `error_detail`을 사용한다. 이 두 additive field를 화면에 표시하는 UI 변경은 MBA-190 범위가 아니다.
- Webhook은 provider 처리 전 접수 응답을 그대로 유지하고 사후 external-effect 실패로 응답을 바꾸지 않는다. Caller가 사용하지 않는 Celery result에는 safe payload를 보존하고 raw provider 오류를 log하지 않는다. Schedule claim에는 새 `external_effect.*` reason을 추가하지 않는다. `external_effect.outcome_unknown`은 기존 `execution_outcome_unknown`, `external_effect.result_unavailable`과 `external_effect.identity_conflict`는 기존 `execution_failed_after_admission`으로 finalization한다. 구체 code는 task-local safe error와 허용된 trace에 두고 claim이나 identity-conflict winner row를 덮어쓰지 않는다. Terminal attempt에 적용 가능한 allowlisted `error_code`는 저장할 수 있다. Schedule claim 상태와 retry 계약은 바꾸지 않는다.
- Node/application error, Workflow Engine, Celery task result, Pub/Sub와 Gateway는 오류를 단순 message 문자열이나 custom exception attribute로만 전달하지 않고 네 필드를 끝까지 보존한다. Non-retryable 오류에는 generic Celery retry를 호출하지 않는다.
- 세 최소 고정 code의 `message`는 결과를 안전하게 다시 제공할 수 없음, 실행 내용이 최초 attempt와 일치하지 않음, 또는 외부 작업 결과를 확인할 수 없음 중 해당하는 일반 문구만 사용한다. 다른 allowlisted provider/configuration code도 code-owned 일반 문구만 사용한다. Provider 원본 응답, 요청 본문/header, `effect_input_digest`, 내부 identity, provider-visible key와 provider exception 원문은 포함하지 않는다.
- `external_effect.retry_allowed`는 Worker 내부 재시도 제어 신호이며 public error allowlist에 포함하지 않는다. Gateway는 모든 동기 실행, deployment/public URL, Compare/Cost Optimizer와 SSE에서 public allowlist와 `retryable=false`를 함께 검증하고, 조건을 만족하지 않는 `external_effect.*` payload를 기존 generic 실행 실패로 치환한다.
- Celery serialization/broker publish 실패도 각 endpoint의 기존 HTTP status와 실패 envelope을 유지하되 raw exception message, traceback, graph/input/context 또는 credential marker를 응답과 log에 넣지 않는다. MBA-190은 이를 위한 새 public error code를 추가하지 않고 기존 generic publish/execution failure를 code-owned 일반 문구로 반환한다.

### 4. 노드 실행 기록 패널 추가

- workflow read 권한이 없으면 node execution log 목록/상세 조회는 403으로 거부한다.
- workflow가 active organization scope 밖이면 node execution log 목록/상세 조회는 404로 숨긴다.
- `node_id`가 workflow graph 또는 해당 run 기록에 없으면 목록 API는 빈 목록을 반환한다.
- 상세 API에서 `run_id`는 존재하지만 현재 node_id 기록이 없으면 404 또는 `node_execution_log.not_found`로 처리한다.
- trace/input/output payload가 redaction 또는 retention 정책으로 누락된 경우 UI는 `표시 가능한 기록 없음`으로 처리한다.

## Permissions

### 1. 실행 편의성

- 테스트 실행은 workflow `operator` 이상 또는 `can_execute=true`인 effective permission이 필요하다.
- 프론트는 권한 없는 사용자에게 테스트 버튼을 disabled 처리하지만, 최종 권한 판정은 Gateway/API가 수행한다.

### 2. 노드 조작 편의성

- 패널 리사이즈 자체는 서버 권한을 요구하지 않는 로컬 UI 조작이다.
- 단, 노드 상세 편집 화면의 입력 수정과 저장은 기존 workflow write 권한 정책을 그대로 따른다.

### 3. 워크플로우 조작 편의성

- 노드 삭제와 자동 재연결은 workflow write 권한이 있는 사용자에게만 허용한다.
- read-only 사용자는 노드 선택은 가능하지만 Backspace/Delete로 graph를 변경할 수 없다.

### 4. 노드 실행 기록 패널 추가

- 노드 실행 기록 조회는 workflow read 권한을 따른다.
- 실행 기록 조회는 현재 workflow graph를 변경하지 않으므로 workflow write 권한을 요구하지 않는다.
- secret, credential 원문, raw prompt 전체 등 민감 정보는 Gateway/API의 응답 정책을 우선하며, 프론트는 표시 단계에서 추가로 allowlist 기반 렌더링을 적용한다.

## Mail Node 저장 계약

- Mail node data는 `credential_id: UUID | null`과 `configuration_state: resolved | unresolved`만 신규 credential 설정으로 허용한다. 구버전 Client가 저장한 `credential_id=null` node에서 `configuration_state`가 아예 없으면 draft 호환을 위해 unresolved로 해석하며, 명시적 null이나 다른 값은 허용하지 않는다.
- `displayNumber`와 `visibleProperties`는 정해진 형식과 값만 갖는 UI metadata로 허용한다.
- `password`, `token`, `email`, `encrypted_secret` 같은 inline Mail identity/secret field가 최상위 또는 중첩 `subGraph`에 있으면 workflow 저장은 `422 mail.credential_reference_required`로 실패한다.
- Non-null `credential_id`는 active organization의 active Mail credential이어야 하며 저장 요청자에게 `use` 권한이 있어야 한다. Organization 밖 reference는 `404`, 같은 organization의 권한 부족은 `403`으로 처리한다.
- Null credential과 unresolved selector는 draft 저장에서 보존할 수 있다. 상태 필드가 누락된 구버전 null Mail node도 이 저장 호환에만 포함한다. Test/active/schedule readiness는 MBA-219 공통 preflight가 다시 평가하며 interactive test의 resource failure는 존재 여부를 숨기기 위해 `mail_credential_unavailable`로 정규화한다.
- `credential_id=null`인 unresolved draft와 상태 필드가 누락된 구버전 null draft는 preview/apply-save를 위해 저장할 수 있다. Active deployment create/toggle과 authenticated test는 공통 preflight에서 `409 deployment.preflight.blocked` 또는 `409 workflow.configuration_preflight.blocked`로 차단한다. Legacy snapshot runtime도 provider 연결 전에 safe reason으로 다시 차단한다.
- `mailNode.processing_mode`는 `search_only | durable`이며 누락 시 `search_only`다. `durable`과 `mark_as_read=true` 조합은 `422 mail.processing_configuration_invalid`로 거부한다.
- 단일 `gmailDraftNode`에 직접 연결되는 source Mail node는 `processing_mode=durable`, `max_results=1`이어야 하며 `processing_ref_selector=[mail_node_id, "processing_ref"]`를 사용한다.
- `gmailDraftNode` data는 `credential_id`, `configuration_state`, `processing_ref_selector`, `reply_body_selector`와 제한된 UI metadata만 허용한다.
- `mailAcknowledgeNode` data는 `processing_ref_selector`, `required_effect_ref_selectors`와 제한된 UI metadata만 허용한다.
- Draft/Acknowledge node에 raw provider message/draft id, recipient override, MIME, token, arbitrary status boolean이나 non-empty `parameters`가 있으면 `422 mail.processing_configuration_invalid`로 거부한다.
- Draft/Acknowledge selector가 존재하지 않는 node, 잘못된 output key, 선행 경로 밖 node 또는 서로 다른 Mail processing source를 가리키면 draft 저장은 `422 mail.processing_configuration_invalid`로, active deployment와 authenticated execution은 공통 preflight `409`로 거부한다. Gmail Draft credential은 source Mail credential과 같고 `provider=gmail`, `auth_type=oauth2`여야 한다.
- OAuth Gmail credential의 Mail 조회와 acknowledgement는 `gmail.modify` 기반 고정 Gmail REST API를 사용한다. `gmail.compose`-only credential은 재인가 전 실행할 수 없고 OAuth credential에는 IMAP fallback이 없다.
- Gmail REST message id는 durable source reference에 암호화 저장되며 `mailNode` output에는 포함되지 않는다.

## Workflow Final Response Citation

- 성공한 test/deployment/public Chatbot 실행의 최종 결과는 authorized prompt evidence가 있고 `citationDisplayMode != hidden`이면 `__nodease_citations` version 1 sidecar를 추가할 수 있다.
- sidecar의 schema와 비노출 필드는 [Knowledge API Spec](../knowledge/api_spec.md)의 `Workflow User Citation Sidecar`를 따른다.
- 기존 final output field와 output schema는 변경하지 않는다. Citation parser가 모르는 version이나 malformed item을 만나면 해당 Citation을 무시하되 최종 답변은 유지한다. TestSidebar는 현재 graph의 node id 집합도 전달하며 reserved key와 충돌하는 stream node-result를 서버 Citation으로 해석하지 않는다.
- CodeNode의 `inputs[].source="node-id.variable"`가 Answer data lineage에 연결되면 해당 source LLM의 Citation도 함께 집계한다. Citation key가 legacy output 또는 stream node id와 충돌하면 기존 결과를 보존하고 Citation만 생략한다.
- `__nodease_citations`는 사용자 응답 전용이다. Workflow Engine은 durable run output을 기록하기 전에 reserved sidecar를 제거한다.

## Slack Node 저장·실행 계약

- `slackPostNode.data`는 `slackMode`, `channel`, `message`, `blocks`, `attachments`, `thread_ts`, `username`, `icon_emoji`, `referenced_variables`와 제한된 UI metadata를 canonical 설정으로 허용한다. 기존 `url`, `authConfig.token`, `method`, `headers`, `body`, `timeout`, `authType`은 명시된 legacy 읽기 호환 범위에서만 허용하며 endpoint/header/body/timeout의 실행 source로 사용하지 않는다.
- Backend draft 저장은 명시 migration을 위해 모든 `*_selector`/`*_selectors` 실행 필드의 제거된 `data`/`headers` output 또는 Webhook mode의 `message_ref` 참조를 보존할 수 있다. Client graph validation은 이를 오류로 표시하고 Deployment validation은 `422 slack.graph_configuration_invalid`, 기존 active snapshot runtime은 `slack.legacy_selector_requires_migration`으로 fail-closed한다. API mode의 `message_ref` 참조는 계속 허용한다.
- API mode deployment는 static token과 channel을 요구한다. Webhook mode deployment는 query, fragment, userinfo, custom port가 없는 정확한 `https://hooks.slack.com/services/{segment}/{segment}/{segment}` 형식만 허용하며, API mode에서 남은 `channel`은 사용하지 않는 호환 잔여값으로 무시한다. 두 mode 모두 공백이 아닌 `message`, 비어 있지 않은 `blocks`, 비어 있지 않은 `attachments` 중 하나 이상을 요구한다. URL과 token은 API 응답, 공개 graph projection, node log, trace metadata에 포함하지 않는다.
- Slack message template은 실제 전송 필드에서 사용한 등록 `referenced_variables`의 `{{name}}` 단순 치환만 지원한다. 미사용 reference는 input을 조회하지 않는다. `blocks`/`attachments`의 JSON template 값은 JSON 문맥에 맞게 escape한 뒤 strict parse하며, 동적 JSON key, Jinja expression, attribute access, filter, statement와 control flow는 `slack.template_render_failed`, `slack.template_value_invalid` 또는 `slack.payload_invalid`로 거부한다.
- 성공 output은 공통 `status`, `delivery_status`, `delivery_mode`를 제공한다. API mode만 검증된 `message_ref`를 제공하며 Webhook mode에서는 `message_ref` selector를 제공하지 않는다. Legacy `data`와 `headers` output은 제공하지 않는다.
- Slack API/Webhook은 각각 `slack.chat.post_message.v1`, `slack.incoming_webhook.post.v1` profile과 ADR-0035의 공통 `ExternalEffectExecutor`를 사용한다. 성공 시 safe output projection을 durable attempt에 저장해 동일 execution slot 재진입에서 provider 호출 없이 재사용한다. `429`와 확인된 rejection은 `failed_before_effect + stop`, 결과가 불명확한 응답/transport 실패는 `effect_outcome_unknown + stop`이며 `Retry-After`는 bounded trace hint일 뿐 generic workflow retry를 허용하지 않는다. 실패는 raw response 대신 공통 safe external-effect code로 반환한다.

## Test 실행 전 canonical 계약

- Test client는 execution stream을 열기 전에 canonical draft를 조회하고 local graph snapshot과 canonical hash를 비교한다. Editor dirty 여부와 관계없이 graph가 다르면 실행을 시작하지 않는다.
- 필요한 draft save는 `expected_graph_hash`와 `expected_updated_at`을 사용하며, save 성공 뒤에도 저장 시작 시점 snapshot과 현재 editor graph가 같을 때만 해당 dirty 상태를 해제한다.
- `409 operation envelope not found`는 Agent Builder session safe operation envelope와 canonical draft 확인 대상으로 분류한다. Client는 canonical hash가 acknowledged result hash와 같은 `applied`, base hash와 같은 terminal `unapplied`, save/ack 처리 중인 `pending`, 어느 쪽도 아닌 `stale`로 구분한다. `applied`에서만 test를 계속하며 typed operation을 재생하거나 `pending|unapplied|stale` graph를 자동 덮어쓰지 않는다.
- Node root의 `width`, `height`, `measured`, `dragging`, `resizing`, `selected`, `positionAbsolute`, node data의 execution `status`, `observability`, editor-only `displayNumber`와 edge selection은 canonical draft request/response 및 graph hash data가 아니다. Client와 Gateway는 최상위 graph, 중첩 `subGraph.nodes`와 `features.noteNodes`에 같은 recursive canonical projection을 적용하며 node `position`과 business configuration은 보존한다.
- Node `configuration_state`는 Client canonical request와 local/canonical comparison data가 아니다. Gateway는 이를 Catalog에서 재계산해 저장 graph와 canonical response에 materialize한다.
- 일반 draft save의 중첩 `subGraph.nodes|edges`가 canonical node/edge schema를 위반하면 Gateway는 DB commit과 audit 전에 HTTP `422`, `workflow.graph_invalid`로 거부한다. Persisted graph의 canonical hash를 계산하는 draft GET에서도 같은 오류 코드를 사용하며 Pydantic detail과 raw graph를 응답하지 않는다.
