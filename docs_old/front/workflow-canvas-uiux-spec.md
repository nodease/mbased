# Workflow Canvas UI/UX 기능 명세

Status: Draft
Authority: Frontend Implementation Guide
Source of Truth: No
Verified Against: dev @ ec576b4f24155697aed8843acc6e5a3fc835f7e1

## 문서 목적

이 문서는 워크플로우 Canvas UI/UX 작업 범위를 프론트 구현, 리뷰, QA 관점에서 정리한다. 초기 MBA-6 작업 명세에서 출발했지만, 현재 기준은 최신 dev의 front/API 문서와 충돌하지 않는 범위다.

기존 기능을 단순히 시각적으로 바꾸는 작업이 아니라, 다음 사용 흐름을 개선하는 것이 목표다.

- 캔버스에서 노드를 빠르게 조작한다.
- 노드 상세 편집 화면에서 입출력 변수를 명확히 확인한다.
- 앞 노드 출력 변수를 다음 노드 설정에 안전하게 삽입한다.
- 테스트 실행 시 현재 화면의 그래프와 실제 실행 그래프가 어긋나지 않게 한다.
- 로그/모니터링을 워크플로우 편집 화면과 분리된 보고 페이지에서 확인한다.

## 작업 범위 요약

### 1. Mock 모드

프론트 UI/UX 작업 중 백엔드 실행 환경에 덜 의존하도록 개발용 mock 모드를 추가했다.

- production이 아닌 환경에서 `/modules/mock` 경로 또는 workflow id가 `mock`인 경우 workflow API 응답을 mock 데이터로 대체한다.
- mock 실행 결과는 고정 문자열이 아니라 입력값 기반으로 생성한다.
- 실제 Provider credential 없이도 워크플로우 화면의 주요 UI를 확인할 수 있다.

주요 파일:

- `apps/client/app/features/workflow/utils/mockMode.ts`
- `apps/client/app/features/workflow/mock/mockWorkflow.ts`
- `apps/client/app/features/workflow/api/workflowApi.ts`

### 2. 캔버스 단축키

워크플로우 빌더에서 반복 조작을 줄이기 위해 캔버스 단축키를 추가했다.

| 단축키 | 동작 |
| --- | --- |
| `Ctrl/Cmd + C` | 선택 노드 복사 |
| `Ctrl/Cmd + V` | 복사한 노드 붙여넣기 |
| `Ctrl/Cmd + D` | 선택 노드 즉시 복제 |
| `Ctrl/Cmd + Z` | Undo |
| `Ctrl/Cmd + Shift + Z` | Redo |
| `Ctrl + Y` | Redo |
| `Ctrl/Cmd + B` | 왼쪽 노드 라이브러리 열기/닫기 |
| `Delete` | 선택한 노드/엣지 삭제 |
| `Escape` | 메뉴/패널 닫기, 선택 해제 |

단축키 충돌 방지 규칙:

- `input`, `textarea`, `select`, `contenteditable`, `role="textbox"` 포커스 중에는 캔버스 단축키를 무시한다.
- modal/dialog가 열려 있으면 캔버스 단축키를 무시한다.
- `Backspace`로 노드/엣지를 삭제하지 않는다. 텍스트 편집 중 실수 삭제를 막기 위함이다.
- `Delete`도 선택 요소가 있을 때만 동작한다.

주요 파일:

- `apps/client/app/features/workflow/hooks/useCanvasKeyboardShortcuts.ts`
- `apps/client/app/features/workflow/hooks/useCanvasKeyboardShortcuts.test.ts`
- `apps/client/app/features/workflow/store/useWorkflowStore.ts`

### 3. Grid Snap

노드 위치의 시각적 통일감을 위해 grid snap을 추가했다.

- 기본 이동 단위는 `10px`이다.
- 사용자는 `off`, `5px`, `10px`, `20px` 중 선택할 수 있다.
- 여러 노드를 함께 이동할 때는 그룹 기준점의 snap delta를 적용해 상대 위치를 유지한다.
- `Alt`를 누르면 snap을 임시 해제할 수 있다.

주요 파일:

- `apps/client/app/features/workflow/utils/gridSnap.ts`
- `apps/client/app/features/workflow/utils/gridSnap.test.ts`
- `apps/client/app/features/workflow/components/editor/BottomPanel.tsx`

### 4. 노드 번호 표시와 번호 기반 연결

각 노드에 `displayNumber`를 부여하고, 출구에서 연결을 시작한 뒤 대상 노드 번호를 입력해 빠르게 연결할 수 있게 했다.

#### 번호 표시 규칙

- 번호는 캔버스 실행 노드에 부여한다.
- `note` 노드는 번호 발급 대상에서 제외한다. 현재 코드 기준 제외 조건은 `node.type !== 'note'`다.
- 번호는 노드의 입구/출구 handle 근처에 표시한다.
- 같은 유형/같은 이름의 노드가 여러 개 있어도 번호 pill로 구분할 수 있다.

#### 번호 발급 규칙

- `1~9` 중 비어 있는 번호가 있으면 먼저 재사용한다.
- `1~9`가 모두 사용 중이면 두 자리 번호를 부여한다.
- 두 자리 번호는 `21 -> 31 -> 41`처럼 앞자리가 분산되도록 부여한다.
- `10, 11, 12...`처럼 같은 prefix로 몰리는 순차 부여는 피한다.
- `10~99`가 모두 사용 중이면 `100`부터 가장 작은 미사용 번호를 fallback으로 사용한다.

이 정책은 번호 입력 연결 UX에서 후보가 한 prefix에 몰리는 문제를 줄이기 위한 것이다.

#### 번호 입력 연결 규칙

- 출구 handle을 클릭하거나 연결을 시작하면 번호 입력 모드가 열린다.
- 숫자를 입력했을 때 대상 후보가 1개면 즉시 연결한다.
- 후보가 2개 이상이면 Enter로 확정한다.
- 없는 번호를 입력하고 Enter를 누르면 연결 모드를 취소한다.
- 연결 불가능한 노드, 존재하지 않는 handle, 순환 그래프는 validation에서 막는다.

주요 파일:

- `apps/client/app/features/workflow/utils/nodeNumbering.ts`
- `apps/client/app/features/workflow/utils/nodeNumbering.test.ts`
- `apps/client/app/features/workflow/components/nodes/BaseNode.tsx`
- `apps/client/app/features/workflow/components/editor/NodeCanvas.tsx`
- `apps/client/app/features/workflow/store/useWorkflowStore.ts`

### 5. 노드 상세 편집 화면

노드 편집을 기존 우측 패널 중심에서 Node Detail View 중심으로 재구성했다.

#### 화면 구조

- 상단: 이전 노드 / 현재 노드 summary / 다음 노드 / 닫기 버튼
- 좌측: 입력 패널과 출력 패널
- 중앙: 현재 노드 설정
- 우측: 보조 탭 패널

#### 연결 노드 네비게이션

- 이전 연결 노드가 1개면 바로 이동 버튼을 보여준다.
- 이전 연결 노드가 2개 이상이면 popover에서 선택한다.
- 다음 연결 노드가 1개면 바로 이동 버튼을 보여준다.
- 다음 연결 노드가 2개 이상이면 목록 선택을 기본으로 한다.
- 선택 시 NDV를 닫지 않고 현재 상세 화면만 대상 노드로 전환한다.
- 이전 대표 노드는 현재 노드가 실제 참조 중인 변수 source를 우선한다.

#### URL 상태

- NDV 열린 상태는 URL query로 표현한다.
- 예: `/modules/{id}?node={nodeId}`
- 브라우저 뒤로가기를 누르면 NDV만 닫히고 워크플로우 빌더에 남는다.
- 새로고침해도 query가 있으면 해당 노드 상세를 다시 연다.

주요 파일:

- `apps/client/app/features/workflow/components/editor/NodeFullscreenEditor.tsx`
- `apps/client/app/features/workflow/hooks/useNodeNavigation.ts`
- `apps/client/app/features/workflow/store/useWorkflowStore.ts`
- `apps/client/app/modules/[id]/page.tsx`

### 6. 노드 UI와 collapsed 표시

노드 카드의 정보 구조를 정리하고, 축소 상태에서도 필요한 속성을 볼 수 있게 했다.

- 노드 상단에 노드 유형과 노드명을 더 명확히 표시한다.
- 노드 이름은 편집 가능하다.
- collapsed 상태에서 볼 속성은 property 단위로 visible/invisible을 설정할 수 있다.
- expanded 상태에서는 visible 요약 섹션을 별도로 중복 노출하지 않는다.
- visible 속성은 collapsed 카드 내부에서 `라벨: 값` 형태로 표시한다.

주요 파일:

- `apps/client/app/features/workflow/components/nodes/BaseNode.tsx`
- `apps/client/app/features/workflow/components/nodes/VisiblePropertiesControl.tsx`
- `apps/client/app/features/workflow/components/nodes/VisiblePropertySummary.tsx`
- `apps/client/app/features/workflow/components/nodes/ui/PropertyVisibilityToggle.tsx`
- `apps/client/app/features/workflow/utils/visibleNodeProperties.tsx`

### 7. 입력/출력 변수 패널

노드 간 변수 흐름을 더 명확히 보기 위해 입력 패널과 출력 패널을 정리했다.

#### 입력 패널

- 현재 노드에서 사용할 수 있는 이전 노드의 출력 변수를 보여준다.
- source 노드별로 그룹화한다.
- source 노드가 2개 이상이면 기본적으로 노드 단위로 접어서 볼 수 있다.
- 같은 source 노드에서 온 변수는 같은 색상 계열로 표시한다.
- source 노드가 같고 노드명도 같은 경우 node number pill을 함께 보여 구분한다.
- 안내 문구는 섹션급 큰 박스가 아니라 보조 설명 수준으로 낮춘다.

#### 출력 패널

- 노드가 생성하는 출력 변수를 row 형태로 보여준다.
- 각 출력 변수는 label, key, 설명, data type badge를 가진다.
- label은 사용자가 읽는 이름이다.
- key는 실행 엔진과 데이터 매핑에서 쓰는 식별자다.
- 출력 변수 섹션은 expanded 상태의 노드 상세 하단에 배치한다.
- 출력 변수 섹션은 접고 펼 수 있다.

주요 파일:

- `apps/client/app/features/workflow/hooks/useNodeIO.ts`
- `apps/client/app/features/workflow/components/nodes/NodeInlinePanel.tsx`
- `apps/client/app/features/workflow/components/nodes/NodeOutputsSection.tsx`
- `apps/client/app/features/workflow/utils/nodeVariablePorts.ts`
- `apps/client/app/features/workflow/utils/nodeOutputLabels.ts`

### 8. 변수 칩 클릭 삽입 UX

`{{변수명}}`을 사용자가 직접 외워서 입력하는 방식 대신, 입력 패널에서 변수 칩을 클릭해 삽입하는 UX로 바꿨다.

초기 구현에는 드래그 앤 드롭 삽입도 있었지만, 텍스트 중간 삽입 위치와 드래그 preview가 사용자의 실제 마우스 위치와 다르게 느껴지는 문제가 있었다. 또한 클릭 삽입과 DnD 삽입이 공존하면 입력 위치 기준이 흐려져서 사용자가 어떤 위치에 변수가 들어갈지 예측하기 어려웠다.

따라서 현재 구현 기준은 DnD를 제거하고, `입력 필드 선택 -> 좌측 입력 패널 칩 클릭` 흐름으로 통일한다.

#### 원칙

- 사용자는 보이지 않는 key를 외워서 직접 입력하지 않는다.
- 텍스트 필드에서는 현재 커서 위치에 변수 칩을 삽입한다.
- 텍스트 필드에 타자하거나 클릭하면 해당 필드가 active target이 된다.
- 변수 칩을 한 번 삽입한 뒤에도 active target과 caret을 유지해 연속 삽입할 수 있어야 한다.
- 변수 칩은 라벨 중심으로 보여준다.
- 내부 저장값은 실행을 위해 key 기반 참조를 유지한다.
- 등록되지 않은 `{{...}}` 토큰은 정상 변수 칩처럼 보이지 않게 처리한다.
- 변수 칩은 contenteditable 내부에서 원자적 토큰처럼 다룬다.
- Backspace/Delete로 칩을 삭제할 때 보이지 않는 caret boundary 때문에 두 번 눌러야 하는 일이 없어야 한다.

#### 지원 대상

- LLM prompt 계열 필드
- Template node 템플릿 필드
- Logic/Condition 계열 변수 참조 필드
- HTTP/Slack/GitHub/Mail/File extraction 등 변수 참조가 필요한 노드 설정
- Answer node 반환값 매핑

#### 텍스트형 입력

- 클릭 삽입: 커서를 둔 필드에 입력 패널 칩을 클릭하면 삽입한다.
- 칩은 텍스트 중간에서도 원자적 토큰처럼 보이도록 렌더링한다.
- 칩 삽입 후 caret은 삽입된 칩 바로 뒤에 유지한다.
- caret 복원은 DOM Range만 믿지 않고 serialized offset 기준으로 보정한다.
- 칩 앞뒤에는 caret이 위치할 수 있는 zero-width boundary를 둔다.
- serialization 시 zero-width boundary는 저장값에서 제거한다.
- Delete/Backspace는 zero-width boundary만 삭제하지 않고 인접 칩을 한 번에 삭제하도록 boundary를 건너뛴다.
- 입력 패널 칩 클릭은 `onMouseDown`에서 editor focus를 빼앗지 않게 처리한다.

#### 선택형 입력

- combo/select처럼 텍스트가 아닌 입력은 직접 `{{...}}`를 쓰지 않고 변수 선택 슬롯으로 받는다.
- 필요한 경우 입력 슬롯을 선택한 뒤 입력 패널에서 칩을 클릭해 값을 연결한다.
- 슬롯은 focus 시에도 active target이 되어 키보드 탐색 후 칩 클릭 흐름을 지원한다.

#### DnD 제거 범위

- 좌측 입력 패널 칩의 `draggable`, `onDragStart`, `dataTransfer` 처리를 제거한다.
- `VariableTokenEditor`의 외부 변수 drop, 내부 칩 drag move, drop caret, drag preview 처리를 제거한다.
- `VariableSelectorSlot`의 drop target 처리를 제거한다.
- 출력 변수 패널과 캔버스 hover 출력 칩의 drag source 처리를 제거한다.
- “드롭해서 추가” 안내 문구를 “커서를 둔 뒤 좌측 입력 패널에서 클릭” 기준으로 수정한다.

#### active target 유지 규칙

- 입력 필드 안에서 타자/클릭/칩 삽입 시 active target을 유지한다.
- 칩 삽입 후 value 또는 token label이 바뀌어 editor가 재렌더되어도 active target이 유지되어야 한다.
- React effect 재등록 과정에서 기존 target cleanup이 먼저 실행되어도 같은 target이 곧 재등록되면 active target을 지우지 않는다.
- `VariableTokenEditor`는 target 등록 effect를 값 변경마다 재등록하지 않고, handler ref만 최신 함수로 갱신한다.
- 다른 필드를 클릭하면 active target은 새 필드로 바뀐다.
- 편집 대상이 아닌 영역 클릭 시 active target 해제는 별도 UX 정책으로 다룬다.

주요 파일:

- `apps/client/app/features/workflow/components/nodes/ui/VariableInsertionProvider.tsx`
- `apps/client/app/features/workflow/components/nodes/ui/VariableInsertionProvider.test.tsx`
- `apps/client/app/features/workflow/components/nodes/ui/VariableTokenEditor.tsx`
- `apps/client/app/features/workflow/components/nodes/ui/VariableSelectorSlot.tsx`
- `apps/client/app/features/workflow/components/nodes/ui/useVariableInsertion.ts`
- `apps/client/app/features/workflow/components/nodes/ui/variableInsertionContext.ts`
- `apps/client/app/features/workflow/components/nodes/ui/ReferencedVariablesControl.tsx`
- `apps/client/app/features/workflow/components/editor/NodeFullscreenEditor.tsx`
- `apps/client/app/features/workflow/components/nodes/NodeOutputsSection.tsx`
- `apps/client/app/features/workflow/utils/nodeVariablePorts.ts`

### 9. Answer node 반환값 매핑

응답 노드는 최종 출력으로 내보낼 값을 이전 노드 출력 변수와 반환 key로 매핑한다.

- 왼쪽 슬롯에는 이전 노드의 출력 칩을 클릭해 넣는다.
- 오른쪽 반환 key는 최종 응답 객체의 key다.
- 반환 key는 영문/숫자/underscore 중심으로 제한한다.
- 최대 길이는 32자다.
- 잘못된 입력은 안내 메시지로 알려준다.
- 빈 값 상태에서 Backspace를 눌러도 노드가 삭제되지 않아야 한다.

주요 파일:

- `apps/client/app/features/workflow/components/nodes/answer/components/AnswerNodePanel.tsx`
- `apps/workflow_engine/workflow/nodes/answer/entities.py`
- `apps/workflow_engine/tests/nodes/test_answer_node.py`

### 10. 테스트 실행 안정화

기존에는 프론트 화면의 그래프와 DB에 저장된 draft 그래프가 어긋날 수 있었다. 이로 인해 화면에서는 삭제된 edge가 실제 실행에서는 남아 순환 오류를 만드는 문제가 있었다.

이를 막기 위해 프론트 구현 흐름은 다음과 같이 정리되어 있다. 백엔드 snapshot 우선 실행과 최종 HTTP 계약은 `docs/api/apps-workflows.md`와 상세 snapshot 문서의 비권위 보조 설명을 따른다.

1. 테스트 클릭
2. 현재 프론트 그래프 snapshot 생성
3. 프론트 graph validation
4. draft 저장
5. 저장 성공 시 동일 snapshot으로 실행 요청
6. 실행 상태를 전역 store에 유지
7. NDV를 열고 닫아도 실행 상태와 결과 패널 접근성을 유지

추가 규칙:

- invalid edge는 로딩 시 자동 정리한다.
- 테스트 직전 draft 저장 실패는 사용자에게 명확히 보여준다. 평소 autosync 실패 상태의 상단 표시 UI는 별도 보강 대상이다.
- stream idle timeout을 둬서 무한 실행 중 상태를 줄인다.
- 실행 중에는 상단 헤더에 상태 pill과 결과 보기 버튼을 표시한다.

상세 문서:

- [workflow-test-run-graph-snapshot-spec.md](workflow-test-run-graph-snapshot-spec.md)

주요 파일:

- `apps/client/app/features/workflow/components/editor/TestSidebar.tsx`
- `apps/client/app/features/workflow/components/editor/EditorHeader.tsx`
- `apps/client/app/features/workflow/utils/validateWorkflowGraph.ts`
- `apps/client/app/features/workflow/utils/workflowDraftPayload.ts`
- `apps/client/app/features/workflow/tests/utils/validateWorkflowGraph.test.ts`
- `apps/gateway/api/v1/endpoints/workflow.py`

### 11. 보고 페이지

로그와 모니터링을 워크플로우 편집 화면의 탭이 아니라 별도 보고 페이지로 분리했다.

- 편집 화면 헤더에서 보고 페이지로 이동할 수 있다.
- 보고 페이지에는 워크플로우 편집으로 돌아가는 버튼이 있다.
- 보고 페이지 내부에서 로그/모니터링을 탭으로 전환한다.
- 편집 화면의 빌더 헤더와 테스트 버튼은 보고 페이지에 그대로 노출하지 않는다.
- 기존 `LogTab`, `MonitoringTab` 기능은 재사용 가능한 보고 페이지 컴포넌트로 분리한다.

주요 파일:

- `apps/client/app/modules/[id]/report/page.tsx`
- `apps/client/app/features/workflow/components/editor/tabs/LogTab.tsx`
- `apps/client/app/features/workflow/components/editor/tabs/MonitoringTab.tsx`
- `apps/client/app/features/workflow/components/editor/EditorHeader.tsx`

### 12. 디자인 톤 정리

`presentation` 프로젝트의 UI 톤을 참고하되, 기능 없는 와이어프레임을 그대로 옮기지 않고 워크플로우 실제 기능에 맞게 톤만 맞췄다.

- dashboard, app card, sidebar, workflow 화면의 색상/간격/버튼 톤을 일부 정리했다.
- 노드 카드와 패널의 radius, border, shadow, text contrast를 개선했다.
- placeholder와 흐린 텍스트 색상을 더 읽기 쉬운 색상으로 조정했다.

주요 파일:

- `apps/client/app/globals.css`
- `apps/client/app/dashboard/**`
- `apps/client/app/features/app/components/AppCard.tsx`
- `apps/client/app/features/dashboard/components/Sidebar.tsx`
- `apps/client/app/features/workflow/components/**`

## QA 체크리스트

### 캔버스 조작

- [ ] `Ctrl/Cmd + C/V/D`가 선택 노드에만 동작한다.
- [ ] `Ctrl/Cmd + Z`, `Ctrl/Cmd + Shift + Z`, `Ctrl + Y`가 undo/redo로 동작한다.
- [ ] 입력 필드 포커스 중에는 캔버스 단축키가 동작하지 않는다.
- [ ] `Backspace`로 노드가 삭제되지 않는다.
- [ ] `Delete`는 선택 요소가 있을 때만 삭제한다.
- [ ] grid snap off/5/10/20 설정이 노드 이동에 반영된다.

### 노드 번호/연결

- [ ] 새 노드는 빈 한 자리 번호를 먼저 받는다.
- [ ] `1~9`가 모두 차면 `21,31,41...`처럼 분산된 두 자리 번호를 받는다.
- [ ] 출구에서 번호 입력으로 대상 노드 연결이 가능하다.
- [ ] 없는 번호 입력 후 Enter 시 연결 모드가 취소된다.
- [ ] IF/ELSE 분기 handle에서도 번호 기반 연결이 가능하다.

### 노드 상세 편집

- [ ] 노드 더블 클릭 또는 확장 버튼으로 NDV가 열린다.
- [ ] URL에 `node=` query가 생기고 뒤로가기로 NDV만 닫힌다.
- [ ] 이전/다음 연결 노드 네비게이션이 동작한다.
- [ ] 좌측 입력/출력 패널과 중앙 설정 패널, 우측 보조 탭 패널이 유지된다.

### 변수 칩

- [ ] 입력 패널 칩 클릭 시 현재 커서 위치에 삽입된다.
- [ ] 칩을 연속으로 클릭해도 같은 active target에 계속 삽입된다.
- [ ] 칩 삽입 후 caret이 삽입된 칩 바로 뒤에 유지된다.
- [ ] 칩 앞뒤에서 Delete/Backspace 한 번으로 칩이 삭제된다.
- [ ] 변수 칩 DnD는 동작하지 않는다.
- [ ] 등록되지 않은 `{{...}}`는 정상 칩처럼 보이지 않는다.
- [ ] 같은 이름의 source 노드가 여러 개여도 node number로 구분된다.
- [ ] 텍스트 필드와 선택형 필드 모두 변수 삽입 UX가 깨지지 않는다.

### 테스트 실행

- [ ] 테스트 실행 전 현재 그래프가 저장된다.
- [ ] 저장 실패 시 실행하지 않고 오류가 표시된다.
- [ ] invalid edge가 있는 경우 실행 전 검증에서 막힌다.
- [ ] 실행 중 NDV를 열고 닫아도 실행 상태 확인이 가능하다.
- [ ] stream이 멈춘 경우 무한 실행 중으로 남지 않는다.

### 보고 페이지

- [ ] 편집 화면에서 보고 페이지로 이동할 수 있다.
- [ ] 보고 페이지에서 워크플로우 편집 화면으로 돌아갈 수 있다.
- [ ] 로그/모니터링 탭 전환이 동작한다.
- [ ] 보고 페이지에는 빌더 조작 헤더가 노출되지 않는다.

## 검증 명령

이 섹션은 이 작업 문서가 제안하는 검증 명령 목록이다. 문서 이동 시점에 아래 명령을 재실행했다는 완료 기록은 아니며, 실제 검증 결과는 PR/CI 결과를 기준으로 확인한다.

```bash
cd apps/client
npx tsc --noEmit
npx vitest run app/features/workflow/hooks/useCanvasKeyboardShortcuts.test.ts
npx vitest run app/features/workflow/components/nodes/ui/VariableInsertionProvider.test.tsx
npx vitest run app/features/workflow/utils/gridSnap.test.ts
npx vitest run app/features/workflow/utils/nodeNumbering.test.ts
npx vitest run app/features/workflow/store/useWorkflowStore.test.ts
npx vitest run app/features/workflow/tests/utils/validateWorkflowGraph.test.ts
```

```bash
python -m py_compile apps/gateway/api/v1/endpoints/workflow.py
```

## 리뷰 시 주의할 점

- PR diff가 넓기 때문에 UI 변경과 실행 안정화 변경을 섞어 보지 않는 것이 좋다.
- 변수 칩 UX는 display label과 engine key가 분리되어 있으므로, 화면 표시와 실행 payload를 함께 확인해야 한다.
- 변수 칩 삽입 UX는 DnD가 아니라 active target 기반 클릭 삽입이 기준이다. 리뷰 시 드래그 동작이 남아 있으면 제거 대상이다.
- contenteditable 내부의 zero-width boundary는 저장값에 포함되면 안 된다. `serializeNode`에서 제거되는지 확인해야 한다.
- 테스트 실행 안정화는 프론트와 gateway가 함께 바뀌므로, 프론트만 확인하면 snapshot 전달 문제를 놓칠 수 있다.
- `uv.lock`, seed script 등 개발 환경 관련 파일은 의도된 변경인지 PR 리뷰에서 한 번 더 확인해야 한다.
