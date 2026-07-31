# Workflow 테스트 실행 그래프 스냅샷 명세

Status: Draft
Authority: Frontend Implementation Guide
Source of Truth: No
Verified Against: dev @ ec576b4f24155697aed8843acc6e5a3fc835f7e1

## 목적

워크플로우 빌더에서 사용자가 보고 있는 그래프와 실제 테스트 실행에 사용되는 그래프가 달라지는 문제를 방지한다.

현재 문제는 프론트 화면에서는 삭제되었거나 보이지 않는 엣지가 DB draft에 남아 있고, 테스트 실행 시 백엔드가 DB draft를 기준으로 실행하면서 순환 그래프 오류가 발생하는 형태로 드러났다.

대표 사례:

- 화면상 그래프는 `입력 -> 템플릿 -> 응답` 구조로 보인다.
- DB draft에는 숨은 엣지 `템플릿 -> 입력`이 남아 있다.
- React Flow는 `startNode`에 없는 `target` handle로 연결된 엣지를 화면에 그리지 못한다.
- 백엔드는 DB draft의 숨은 엣지를 포함해 그래프를 검사하고 cycle을 감지한다.
- 사용자는 화면에서 보이지 않는 연결 때문에 테스트가 실패한다고 느낀다.

이 명세는 테스트 실행 시점에 현재 프론트 그래프를 기준으로 snapshot을 만들고, 검증하고, 저장한 뒤, 같은 snapshot으로 실행하는 구조를 정의한다.

이 문서는 프론트 구현 제안이며 최종 API 계약이나 백엔드 런타임 정책의 source of truth가 아니다. 요청/응답 형태, 백엔드 검증 책임, graphSnapshot 우선순위는 `api/` 문서 또는 관련 ADR에 반영되어야 최종 계약으로 본다.

## 용어

### Graph snapshot

테스트 버튼을 누른 순간 프론트 store에 있는 워크플로우 그래프 상태를 복사한 실행 기준 데이터다.

포함 대상:

- nodes
- edges
- viewport
- features
- envVariables
- runtimeVariables

snapshot은 실행 도중 사용자가 그래프를 수정하더라도 테스트 시작 시점의 그래프를 고정하기 위해 사용한다.

### Draft 저장

현재 편집 중인 워크플로우 그래프를 서버 DB의 draft 상태로 저장하는 작업이다.

Draft 저장의 목적은 테스트 실행이 아니라 편집 상태의 영속화다. 새로고침, 페이지 이동, 다음 테스트 실행, 다른 화면 진입 시에도 같은 그래프가 유지되어야 한다.

### 실행 snapshot 전달

테스트 실행 API에 graph snapshot을 직접 전달하는 작업이다.

실행 snapshot 전달의 목적은 이번 테스트 1회가 사용자가 방금 본 화면과 같은 그래프로 실행되도록 보장하는 것이다.

## 현재 문제 분석

### 1. 화면과 DB draft가 분리되어 있다

프론트의 `nodes`, `edges`는 브라우저 메모리에 있는 상태다. 사용자가 노드나 엣지를 수정하면 화면은 즉시 바뀐다.

DB draft는 자동 저장 또는 명시적 저장이 성공해야 바뀐다.

따라서 다음 상황이 가능하다.

```text
1. 사용자가 엣지를 삭제한다.
2. 프론트 화면에서는 엣지가 사라진다.
3. 자동 저장이 실패하거나, 잘못된 엣지가 이미 저장되어 있다.
4. DB draft에는 이전 그래프가 남아 있다.
5. 테스트 실행 시 백엔드가 DB draft를 읽는다.
6. 사용자가 보는 화면과 다른 그래프로 실행된다.
```

### 2. invalid edge가 내부 edges에 들어갈 수 있다

React Flow의 `edges`는 화면에 그려진 선만 의미하지 않는다. 내부 상태에는 화면에 그릴 수 없는 엣지도 들어갈 수 있다.

예시:

```ts
{
  source: 'template-reply',
  sourceHandle: 'source',
  target: 'start-customer',
  targetHandle: 'target'
}
```

`startNode`는 입력 포트가 없어야 하는 노드인데, 번호 기반 빠른 연결 로직이 대상 노드의 실제 입력 가능 여부를 검증하지 않고 `targetHandle: 'target'`으로 연결을 생성할 수 있다.

이 경우 React Flow는 다음 경고를 출력한다.

```text
Couldn't create edge for target handle id: "target"
```

하지만 내부 `edges`와 DB draft에는 해당 엣지가 남을 수 있다.

### 3. 테스트 실행이 DB draft 기준이면 사용자가 본 화면과 달라질 수 있다

현재 테스트 실행 API가 DB draft를 기준으로 실행하면, 프론트 화면의 최신 상태와 DB draft가 불일치할 때 테스트 결과를 신뢰하기 어렵다.

따라서 테스트 실행 전 다음 절차가 필요하다.

```text
테스트 클릭
-> 현재 프론트 그래프 snapshot 생성
-> 프론트 검증
-> DB draft 저장
-> 저장 성공 시 같은 snapshot으로 실행
```

## 목표 동작

테스트 버튼을 누르면 다음 순서로 동작해야 한다.

```text
1. 현재 프론트 store에서 그래프 snapshot 생성
2. snapshot에 대해 프론트 검증 수행
3. 검증 실패 시 실행 중단 및 UI에 명확히 표시
4. 검증 성공 시 draft 강제 저장
5. 저장 실패 시 실행 중단 및 UI에 명확히 표시
6. 저장 성공 시 동일 snapshot을 실행 API에 전달
7. API 계약이 확정된 경우 백엔드는 전달받은 snapshot을 우선 사용해 테스트 실행
8. 실행 상태와 결과를 테스트 패널/미니 상태바에 표시
```

## 구현 범위

### 1. 그래프 로딩 시 invalid edge 자동 정리 UI

대상 파일:

- `apps/client/app/features/workflow/store/useWorkflowStore.ts`
- `apps/client/app/features/workflow/hooks/useAutoSync.ts`
- 필요 시 `apps/client/app/features/workflow/components/editor/EditorHeader.tsx`
- 필요 시 `apps/client/app/features/workflow/components/editor/NodeCanvas.tsx`

현재 구현에서 draft 로딩과 cleanup 실행은 `useAutoSync.ts`가 담당한다. `useWorkflowAppSync.ts`는 workflow/app metadata, active organization, permission 상태를 동기화한다.

이미 DB draft에 저장된 invalid edge는 테스트 직전 검증만으로는 해결되지 않는다. 매번 테스트를 누를 때마다 검증 실패가 발생하고, 사용자는 화면에 보이지 않는 엣지를 직접 삭제할 수도 없다.

따라서 워크플로우 그래프를 로딩하는 시점에 invalid edge를 감지하고, 사용자가 이해할 수 있는 방식으로 정리해야 한다.

#### 정리 대상

다음 edge는 로딩 시점에 invalid edge로 판단한다.

| 유형 | 예시 | 처리 |
| --- | --- | --- |
| source node 없음 | 삭제된 노드에서 나가는 edge | 제거 대상 |
| target node 없음 | 삭제된 노드로 들어가는 edge | 제거 대상 |
| 시작/트리거 노드 incoming | `templateNode -> startNode` | 제거 대상 |
| 종료 노드 outgoing | `answerNode -> llmNode` | 제거 대상 |
| source-only node incoming | `startNode.target` | 제거 대상 |
| invalid condition sourceHandle | 삭제된 IF/ELSE 분기 handle | 제거 대상 |
| 중복 edge | 같은 source/target/handle 조합 반복 | 현재 검증 warning. 자동 제거 대상 아님 |
| cycle edge | cycle을 만드는 edge | 자동 제거보다는 우선 경고 |

cycle edge는 자동으로 어느 edge를 제거해야 할지 판단이 애매할 수 있다. 따라서 1차 구현에서는 cycle은 자동 삭제보다 경고와 실행 차단을 우선한다.

#### UX 정책

로딩 시 invalid edge가 발견되면 화면에는 다음처럼 표시한다.

```text
워크플로우 연결 일부를 정리했습니다.

화면에 표시할 수 없거나 실행할 수 없는 연결 1개를 제거했습니다.
예: 응답 초안 생성 -> 고객 문의 입력
```

표시 위치:

- 우선순위 1: 상단 헤더 근처 toast 또는 banner
- 우선순위 2: 테스트 패널 내 경고
- 우선순위 3: 콘솔 로그만 출력하는 방식은 금지

사용자에게 edge ID만 보여주지 않는다. 노드명 기준으로 보여준다.

```text
나쁜 예:
xy-edge__template-replysource-start-customertarget 제거됨

좋은 예:
응답 초안 생성 -> 고객 문의 입력 연결을 제거했습니다.
```

#### 자동 저장 정책

invalid edge를 로딩 시 제거했다면, 정리된 그래프를 DB draft에 다시 저장해야 한다.

권장 흐름:

```text
1. 서버에서 draft 로딩
2. validateWorkflowGraph로 invalid edge 감지
3. 자동 정리 가능한 edge 제거
4. 프론트 store에는 정리된 nodes/edges 반영
5. 사용자에게 정리 결과 표시
6. 정리된 draft를 서버에 저장
7. 저장 실패 시 "정리 결과 저장 실패" 표시
```

주의:

- 정리된 결과 저장이 실패하면 사용자가 새로고침했을 때 같은 invalid edge가 다시 나타날 수 있다.
- 따라서 저장 실패를 숨기면 안 된다.

#### 자동 정리와 사용자 확인의 기준

자동 제거해도 되는 것:

- 존재하지 않는 노드를 참조하는 edge
- 시작/트리거 노드로 들어가는 edge
- 종료 노드에서 나가는 edge
- conditionNode의 존재하지 않는 sourceHandle을 참조하는 edge

자동 제거하지 않고 사용자에게 확인시키는 것이 좋은 것:

- cycle을 만드는 여러 edge 중 어느 것을 삭제해야 할지 불명확한 경우
- conditionNode의 분기 구조와 관련된 edge인데 복구 가능성이 있는 경우
- 백엔드 실행 규칙은 위반하지만 화면 표현은 가능한 edge
- 완전히 동일한 중복 edge. 현재 구현은 validation warning으로만 표시하고 cleanup에서 자동 제거하지 않는다.
- 일반 node의 targetHandle 존재 여부. 현재 구현은 source-only incoming과 condition sourceHandle만 명시적으로 검증한다.

이번 문제의 `template-reply -> start-customer` 숨은 엣지는 자동 제거 대상이다.

#### 구현 방식

`validateWorkflowGraph`는 단순히 검증 결과만 반환하고, 별도의 정리 함수가 제거 가능한 edge를 계산한다.

현재 구현 함수:

```ts
export type GraphCleanupResult = {
  graph: WorkflowDraftRequest;
  removedIssues: GraphValidationIssue[];
  unresolvedIssues: GraphValidationIssue[];
};

export function cleanupInvalidEdges(
  graphSnapshot: WorkflowDraftRequest,
): GraphCleanupResult;
```

분리 이유:

- 검증은 테스트 직전에도 사용한다.
- 정리는 그래프 로딩 시점에만 자동으로 수행한다.
- 테스트 직전에 사용자가 모르는 자동 삭제가 발생하면 혼란스러울 수 있다.

로딩 시점에는 자동 정리:

```text
load draft -> cleanupInvalidEdges -> setWorkflowData -> save cleaned draft
```

테스트 시점에는 실행 차단:

```text
test click -> validateWorkflowGraph -> show errors -> stop
```

### 2. 테스트 실행 직전 snapshot 생성

대상 파일:

- `apps/client/app/features/workflow/components/editor/TestSidebar.tsx`
- `apps/client/app/features/workflow/store/useWorkflowStore.ts`

테스트 실행 핸들러에서 store의 최신 상태를 가져온다.

```ts
const {
  nodes,
  edges,
  viewport,
  features,
  envVariables,
  runtimeVariables,
} = useWorkflowStore.getState();
```

snapshot은 참조 공유를 피하기 위해 복사해서 만든다.

```ts
const graphSnapshot = structuredClone({
  nodes,
  edges,
  viewport,
  features,
  envVariables,
  runtimeVariables,
});
```

주의:

- 실행 중 사용자가 그래프를 수정해도 이미 시작된 테스트는 시작 시점 snapshot으로 실행되어야 한다.
- snapshot 생성 후 검증/저장/실행에 모두 같은 객체를 사용해야 한다.

### 3. 프론트 그래프 검증 유틸 추가

추천 파일:

- `apps/client/app/features/workflow/utils/validateWorkflowGraph.ts`

검증 함수 책임:

- 실행 가능한 그래프인지 판단한다.
- 사용자에게 보여줄 수 있는 오류 메시지를 만든다.
- 오류가 있으면 테스트 실행을 막는다.

추천 타입:

```ts
export type GraphValidationIssue = {
  level: 'error' | 'warning';
  code:
    | 'MISSING_SOURCE_NODE'
    | 'MISSING_TARGET_NODE'
    | 'START_NODE_HAS_INCOMING_EDGE'
    | 'TRIGGER_NODE_HAS_INCOMING_EDGE'
    | 'TERMINAL_NODE_HAS_OUTGOING_EDGE'
    | 'INVALID_CONDITION_SOURCE_HANDLE'
    | 'DUPLICATE_EDGE'
    | 'CYCLE_DETECTED';
  message: string;
  edgeId?: string;
  nodeId?: string;
  sourceNodeId?: string;
  targetNodeId?: string;
};

export type GraphValidationResult = {
  ok: boolean;
  errors: GraphValidationIssue[];
  warnings: GraphValidationIssue[];
};
```

필수 검증 항목:

| 검증 항목 | 실패 예시 | 처리 |
| --- | --- | --- |
| source node 존재 여부 | edge.source가 nodes에 없음 | error |
| target node 존재 여부 | edge.target이 nodes에 없음 | error |
| 시작/트리거 노드 incoming 금지 | `templateNode -> startNode` | error |
| 종료 노드 outgoing 금지 | `answerNode -> llmNode` | error |
| target handle 유효성 | `startNode`에 `target` 연결 | error |
| condition source handle 유효성 | 삭제된 분기 handle에 edge 연결 | error |
| 중복 edge | 같은 source/target/handle 조합 반복 | warning 또는 error |
| cycle 감지 | `A -> B -> A` | error |

이번 문제를 직접 막는 핵심 로직:

```ts
const sourceOnlyNodeTypes = new Set([
  'startNode',
  'webhookTrigger',
  'scheduleTrigger',
]);

if (sourceOnlyNodeTypes.has(targetNode.type)) {
  errors.push({
    level: 'error',
    code: 'START_NODE_HAS_INCOMING_EDGE',
    message: '입력/트리거 노드에는 다른 노드를 연결할 수 없습니다.',
    edgeId: edge.id,
    sourceNodeId: edge.source,
    targetNodeId: edge.target,
  });
}
```

### 4. 검증 실패 UI

대상 파일:

- `apps/client/app/features/workflow/components/editor/TestSidebar.tsx`

검증 실패 시 실행하지 않는다.

상태 예시:

```ts
const [validationErrors, setValidationErrors] = useState<GraphValidationIssue[]>([]);
```

실행 전 처리:

```ts
const validation = validateWorkflowGraph(graphSnapshot);

if (!validation.ok) {
  setExecutionStatus('failed');
  setValidationErrors(validation.errors);
  setErrorMessage('워크플로우 연결에 문제가 있어 테스트를 실행할 수 없습니다.');
  return;
}
```

UI 문구 원칙:

- edge ID를 그대로 보여주지 않는다.
- 노드명을 함께 보여준다.
- 사용자가 다음에 무엇을 해야 하는지 알려준다.

예시:

```text
실행 전 검증 실패

입력 노드에는 다른 노드를 연결할 수 없습니다.
문제 연결: 응답 초안 생성 -> 고객 문의 입력

문제 연결을 삭제한 뒤 다시 테스트해주세요.
```

### 5. Draft 강제 저장

대상 파일:

- `apps/client/app/features/workflow/api/workflowApi.ts`
- `apps/client/app/features/workflow/components/editor/TestSidebar.tsx`

테스트 실행 전 자동 저장을 기다리지 말고 명시적으로 draft 저장 API를 호출한다.

현재 API:

```ts
syncDraftWorkflow: async (
  workflowId: string,
  graphSnapshot: WorkflowDraftRequest,
) => {
  const response = await api.post(`/workflows/${workflowId}/draft`, graphSnapshot);
  return response.data;
}
```

이미 동일한 draft 저장 함수가 있다면 새로 만들지 않고 재사용한다.

저장 실패 시 실행은 중단한다.

```ts
try {
  setPreflightStatus('saving');
  await workflowApi.syncDraftWorkflow(workflowId, graphSnapshot);
} catch (error) {
  setExecutionStatus('failed');
  setErrorMessage('현재 워크플로우 저장에 실패해서 테스트를 실행하지 않았습니다.');
  return;
}
```

### 6. 저장 실패 UI

저장 실패는 실행 실패와 구분해서 표시한다.

예시:

```text
저장 실패

현재 화면의 변경사항을 서버에 저장하지 못해 테스트를 실행하지 않았습니다.
로그인이 만료되었거나 서버 연결에 문제가 있을 수 있습니다.
```

상태별 안내:

| 실패 유형 | 사용자 안내 |
| --- | --- |
| 401 Unauthorized | 로그인이 만료되었습니다. 다시 로그인 후 테스트해주세요. |
| 403 Forbidden | 이 워크플로우를 저장할 권한이 없습니다. |
| 500 Server Error | 서버 저장 중 오류가 발생했습니다. |
| Network Error | 서버에 연결할 수 없습니다. 백엔드 실행 상태를 확인해주세요. |

중요 정책:

- 저장 실패 시 snapshot만으로 테스트를 실행하지 않는다.
- 저장 실패 상태를 사용자에게 숨기지 않는다.
- 자동 저장 실패도 별도 상태로 표시하는 것이 좋다.

### 7. 실행 API에 snapshot 직접 전달

대상 파일:

- `apps/client/app/features/workflow/api/workflowApi.ts`
- `apps/client/app/features/workflow/components/editor/TestSidebar.tsx`
- 백엔드 실행 스트림 endpoint

프론트 요청 형태 예시:

```ts
await workflowApi.executeWorkflowStream({
  workflowId,
  userInput,
  graphSnapshot,
});
```

API 계약 보강 TODO:

```text
현재 스냅샷 우선 실행은 UI 테스트 실행의 SSE 스트리밍 경로에 적용된다.
프론트는 /stream-api/workflows/{workflowId} Next.js 프록시로 요청하고, 프록시는 backend /api/v1/workflows/{workflow_id}/stream으로 전달한다.
stream 요청 body에 graph_snapshot이 있으면 graph_snapshot을 우선 사용한다.
graph_snapshot이 없으면 기존 호환성을 위해 DB draft를 사용한다.
일반 /api/v1/workflows/{workflow_id}/execute는 현재 DB draft만 사용한다.
```

위 동작은 이 프론트 명세의 권장안이며, 최종 계약은 `api/` 문서에 별도로 기록해야 한다.

권장 실행 순서:

```text
1. 프론트가 graphSnapshot 검증
2. 프론트가 draft 저장
3. 프론트가 graph_snapshot 포함해 stream 실행 요청
4. 백엔드 검증 정책이 확정된 경우 graphSnapshot을 다시 검증
5. API 계약이 확정된 경우 graphSnapshot 기준으로 Celery/Gateway 실행
```

백엔드 재검증이 필요한 이유:

- 프론트 검증은 UX 개선용이다.
- 실제 안전성은 백엔드에서도 보장해야 한다.
- 외부 클라이언트가 잘못된 graphSnapshot을 직접 보낼 수 있다.

## 상태 관리

테스트 실행 상태는 실행 전 준비 단계와 실제 실행 단계를 구분하는 것이 좋다.

추천 상태:

```ts
type TestPreflightStatus =
  | 'idle'
  | 'validating'
  | 'saving'
  | 'executing'
  | 'failed'
  | 'success';
```

버튼 문구:

| 상태 | 버튼/패널 문구 |
| --- | --- |
| validating | 검증 중... |
| saving | 저장 중... |
| executing | 실행 중... |
| failed | 다시 테스트하기 |
| success | 다시 테스트하기 |

버튼 비활성화 조건:

- validating
- saving
- executing

## Autosync 실패 표시 개선

테스트 직전 강제 저장과 별개로, 평소 자동 저장 실패도 사용자에게 보여야 한다.

현재처럼 자동 저장 실패를 조용히 무시하면 사용자는 저장된 줄 알고 작업을 이어가게 된다.

권장 store 상태이며, 현재 구현에는 아직 상단 헤더 저장 상태로 연결되어 있지 않다. 현재 autosync 실패는 hook 내부에서 console 경고 후 조용히 무시될 수 있다.

목표 store 상태:

```ts
type SyncStatus = 'idle' | 'saving' | 'saved' | 'error';

type SyncError = {
  message: string;
  status?: number;
  occurredAt: string;
};
```

상단 헤더 또는 미니 상태바 표시:

```text
저장 중...
저장됨
저장 실패
```

저장 실패 클릭 시:

```text
마지막 자동 저장에 실패했습니다.
테스트 실행 전에는 강제 저장을 다시 시도합니다.
```

## invalid edge 생성 방지

테스트 직전 검증은 마지막 방어선이다. 애초에 잘못된 edge가 만들어지지 않게 해야 한다.

수정 대상:

- `apps/client/app/features/workflow/store/useWorkflowStore.ts`
- `apps/client/app/features/workflow/components/editor/NodeCanvas.tsx`
- `apps/client/app/features/workflow/hooks/useNodeCreation.ts`

필수 정책:

```text
onConnect는 모든 연결 생성의 중앙 관문이어야 한다.
번호 연결, 드래그 연결, 자동 연결 모두 최종적으로 같은 validateConnection을 거쳐야 한다.
```

현재 구현 상태:

- React Flow drag connection과 번호 기반 연결은 `NodeCanvas`를 통해 store `onConnect`를 호출하고 `validateConnection`을 거친다.
- `useNodeCreation`의 node drop 자동 연결은 현재 `setEdges([...edges, newEdge])`를 직접 호출하므로 `onConnect`/`validateConnection` 중앙 관문을 우회한다. 이 경로는 목표 정책과 다른 현재 예외다.

추천 함수:

```ts
validateConnection({
  nodes,
  edges,
  connection,
}): { ok: boolean; reason?: string }
```

차단해야 하는 연결:

- 시작/트리거 노드로 들어오는 연결
- 응답/종료 노드에서 나가는 연결
- 존재하지 않는 node로 향하는 연결
- conditionNode의 존재하지 않는 sourceHandle에서 나가는 연결
- cycle을 만드는 연결
- 동일한 source/target/handle 조합의 중복 연결은 현재 warning이며, `validateConnection`에서는 차단하지 않는다.

## 기존 데이터 정리

이미 DB draft에 저장된 invalid edge는 연결 생성 방어 로직만으로 사라지지 않는다. 따라서 이번 작업 범위에 그래프 로딩 시 invalid edge 자동 정리 UI를 포함한다.

예시 invalid edge:

```text
xy-edge__template-replysource-start-customertarget
```

정리 방법:

1. 워크플로우 로딩 시 invalid edge를 필터링하고 사용자에게 알린다.
2. 자동 정리 가능한 edge는 제거한 뒤 정리된 draft를 저장한다.
3. 자동 정리하기 애매한 edge는 사용자에게 경고하고 테스트 실행을 막는다.
4. 테스트 전 검증에서 남아 있는 invalid edge를 error로 표시하고 실행을 막는다.
5. 필요하면 개발용 seed/mock 데이터에서 invalid edge를 제거한다.
6. 운영 데이터가 있다면 migration 또는 admin cleanup 스크립트를 별도로 준비한다.

자동 삭제는 신중해야 한다.

- 사용자가 모르는 사이 그래프가 바뀔 수 있다.
- 삭제 전에 어떤 edge가 제거되는지 표시하는 편이 안전하다.

## 사용자 경험 기준

### 검증 실패

검증 실패는 테스트 실패가 아니다. 실행 전에 발견한 그래프 구성 오류다.

권장 UI:

```text
실행 전 검증 실패
워크플로우 연결에 문제가 있어 테스트를 실행하지 않았습니다.

문제 연결
- 응답 초안 생성 -> 고객 문의 입력
  입력 노드에는 다른 노드를 연결할 수 없습니다.
```

### 저장 실패

저장 실패도 테스트 실패가 아니다. 현재 그래프를 서버에 반영하지 못한 상태다.

권장 UI:

```text
저장 실패
현재 화면의 변경사항을 서버에 저장하지 못해 테스트를 실행하지 않았습니다.
```

### 실행 실패

검증과 저장이 성공한 뒤 백엔드 실행 중 발생한 오류만 실행 실패로 표시한다.

예:

- Provider API key 없음
- LLM 호출 실패
- HTTP request 노드 실패
- Celery/Redis 연결 실패
- 런타임 예외

## 검증 시나리오

### 정상 시나리오

```text
1. 입력 노드 -> 템플릿 노드 -> 응답 노드 구성
2. 테스트 버튼 클릭
3. snapshot 생성
4. 검증 통과
5. draft 저장 성공
6. 같은 snapshot으로 실행
7. 실행 성공 표시
```

기대 결과:

- 화면과 실행 결과의 그래프가 일치한다.
- 테스트 패널에 실행 결과가 표시된다.
- DB draft도 같은 그래프로 저장된다.

### 숨은 invalid edge 시나리오

```text
1. DB draft에 template-reply -> start-customer 엣지가 존재
2. 화면에서는 해당 엣지가 보이지 않음
3. 테스트 버튼 클릭
4. snapshot 검증에서 startNode incoming edge 감지
5. 실행 중단
```

기대 결과:

- 백엔드 cycle error까지 가지 않는다.
- 사용자에게 문제 연결을 노드명 기준으로 보여준다.

### 저장 실패 시나리오

```text
1. 그래프 수정
2. 테스트 버튼 클릭
3. 검증 통과
4. draft 저장 API 401 또는 500 반환
5. 실행 중단
```

기대 결과:

- snapshot만으로 실행하지 않는다.
- 저장 실패 메시지를 표시한다.
- 사용자에게 로그인/서버 상태 확인을 안내한다.

### 실행 실패 시나리오

```text
1. snapshot 검증 통과
2. draft 저장 성공
3. snapshot으로 실행 요청
4. 백엔드 실행 중 Provider 오류 발생
```

기대 결과:

- 저장/검증 실패가 아니라 실행 실패로 표시한다.
- 어떤 노드에서 실패했는지 표시한다.

## 완료 기준

이 섹션은 구현 작업의 acceptance criteria다. 문서 이동 시점에 아래 항목이 모두 검증 완료되었다는 기록은 아니며, 실제 완료 여부는 구현 PR과 CI/QA 결과를 기준으로 확인한다.

이 작업은 다음 조건을 만족하면 완료로 본다.

- 워크플로우 로딩 시 invalid edge를 감지한다.
- 자동 정리 가능한 invalid edge를 제거하고 사용자에게 정리 결과를 표시한다.
- 정리된 그래프를 DB draft에 다시 저장한다.
- 정리 결과 저장 실패 시 사용자에게 저장 실패를 표시한다.
- 테스트 버튼 클릭 시 현재 프론트 그래프 snapshot을 생성한다.
- 실행 전 프론트 그래프 검증을 수행한다.
- 검증 실패 시 실행하지 않고 UI에 오류를 표시한다.
- 검증 통과 시 draft 저장을 명시적으로 수행한다.
- draft 저장 실패 시 실행하지 않고 UI에 오류를 표시한다.
- draft 저장 성공 시 같은 snapshot으로 실행한다.
- API 계약이 확정된 경우 백엔드가 요청의 graphSnapshot을 우선 사용한다.
- 시작/트리거 노드로 들어오는 invalid edge를 생성 시점 또는 실행 전 검증에서 차단한다.
- 기존 숨은 invalid edge가 있어도 사용자가 이해 가능한 방식으로 문제를 확인할 수 있다.
- lint/typecheck/build 또는 프로젝트에서 합의한 검증 명령을 통과한다.

## 향후 분리 가능한 후속 작업

이 명세의 핵심 작업과 별도로 다음 작업은 후속 PR로 분리할 수 있다.

- autosync 실패 상태를 상단 헤더에 상시 표시
- 연결 생성 시 toast 또는 inline feedback
- cycle을 만드는 연결을 드래그 중 미리 차단
- 백엔드 graph validation 결과를 프론트 검증 메시지와 동일한 형식으로 표준화
- 저장 실패 복구 버튼 제공
- 테스트 실행 중 그래프 수정 시 경고 또는 실행 snapshot 보기 기능
