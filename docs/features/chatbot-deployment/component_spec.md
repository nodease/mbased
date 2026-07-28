# Chatbot Deployment Component Spec

Status: Draft

## Screens

- 워크플로우 에디터 상단 "게시하기" 드롭다운 (`components/editor/NodeCanvas.tsx`)
- 배포 플로우 모달 (`components/deployment/DeploymentFlowModal.tsx` → `SuccessStep.tsx`)
- 공개 챗봇 페이지 (`app/embed/chat/[urlSlug]/page.tsx`) — 기존 임베드 챗 UI 재사용
- 인증 내부 챗봇 실행 페이지 (`app/modules/[id]/run/page.tsx`) — 로그인 사용자 권한으로 `internal_chatbot` 배포 snapshot을 실행한다. Target access grant/session surface는 별도 후속 계약이다.

## Components

- "게시하기" 드롭다운: 시작 노드가 `startNode`일 때 **"공개 챗봇 배포"**와 **"내부 챗봇 배포"**를 별도 항목으로 노출한다. 각각 `handlePublishAsChatbot`과 `handlePublishAsInternalChatbot` 호출로 `deploymentType='chatbot'` 또는 `'internal_chatbot'`인 모달을 연다.
- `useDeployment.handleDeploy`: 현재 편집 중인 동일 `{nodes, edges}` snapshot을 preflight와 create에 함께 보내 consumer 선택과 검증 graph가 어긋나지 않게 한다. `chatbot`은 `${origin}/embed/chat/{url_slug}` 공개 링크만 결과에 넣고, `internal_chatbot`은 `${origin}/modules/{workflow_id}/run?deploymentId={deployment_id}` 인증 실행 링크만 넣는다.
- `DeploymentFlowModal.getDeploymentTypeName`: `chatbot` → `"공개 챗봇"`, `internal_chatbot` → `"내부 챗봇"`.
- `SuccessStep`: `chatbot`은 공개 챗봇 공유 카드만, `internal_chatbot`은 사내 인증 실행 카드만 표시해 두 보안 경계를 한 배포 결과에서 섞지 않는다.
- 공개 `chatbot`과 `widget` 배포 form은 “외부 사이트에 삽입 허용” toggle과 exact parent origin 목록 editor를 제공한다. 기본값은 disabled이고 enabled 상태에서 1~20개 origin이 없으면 submit하지 않는다. `internal_chatbot`, `webapp`과 다른 type에는 표시하지 않는다.
- Client validation과 normalized preview는 UX-only다. Deployment preflight가 반환한 `normalized_browser_access_policy`를 final preview와 create request에 사용하고 local parser 결과로 Gateway rejection을 완화하지 않는다.
- 기존 deployment의 parent policy 변경은 in-place edit이 아니라 browser-access revision endpoint로 새 inactive version을 만든다. 사용자가 별도로 활성화하기 전 current active pointer는 유지한다.
- `SuccessStep`은 embedding disabled면 direct link만 표시하고 enabled면 iframe snippet과 bounded parent origin 요약을 함께 표시한다. Parent 목록을 CORS/API 권한으로 설명하지 않는다.
- 챗봇 페이지(`app/embed/chat/[urlSlug]/page.tsx`): 메시지 버블/입력창/환영 메시지/입력 중 표시(기존). 전송 시 `POST /api/v1/run-public/{urlSlug}/chat`에 현재 React state에서 만든 bounded `conversation.history`를 함께 보내며, private Knowledge/RAG 후보를 workflow owner 권한으로 넓히지 않는다.
- 인증 내부 실행 페이지(`app/modules/[id]/run/page.tsx`): `GET /api/v1/deployments/{deployment_id}/run-info`와 JSON `POST /api/v1/deployments/{deployment_id}/run`을 사용한다. `internal_chatbot`은 사용자 선택기 없이 대화 내역을 위에 누적하고 질문 composer를 아래에 배치한다. 페이지 우상단 권한 배지는 `authApi.me()`로 확인한 현재 로그인 사용자 이름과 사용자 권한 적용 상태를 함께 표시하며, 사용자 정보 조회 실패 시 이름만 생략한다. 질문 입력과 전송 버튼은 세로 중앙 정렬한다. 질문 입력은 내부 스크롤 없이 줄 수에 맞춰 자동으로 높아지고 전송 후 한 줄 높이로 돌아간다. IME 조합 중이 아닌 `Enter`는 form을 전송하고 `Shift+Enter`는 줄바꿈을 유지한다. text 최종 응답은 raw HTML을 실행하지 않는 GitHub Flavored Markdown으로 렌더링하며 카드 내부 스크롤 없이 전체 내용을 펼친다. 내부 챗봇은 초록 계열 강조를 사용하지 않고 중립 색상과 파란 focus 상태를 사용한다. 최종 응답은 바깥 배경·테두리 card 없이 header를 표시하며 실제 응답 box는 왼쪽 icon 시작선부터 전체 너비를 사용한다. 현재 라이트 전용 화면 정책에 따라 내부 챗봇 최종 응답에는 OS 색상 설정으로 활성화되는 `dark:` 변형을 적용하지 않는다. 내부 챗봇도 일반 workflow 실행 화면과 같은 기본 제목, 메시지, 입력, 전송 control 크기를 사용한다. page-session UUID를 top-level `conversation.client_id`로 보내고 업무 `inputs`에는 `memory_mode`/`conversation_id` control을 추가하지 않는다. LLM node RAG는 로그인 사용자를 execution subject로 전달받는다. 챗봇이 아닌 배포는 다중 입력용 기존 form/result layout을 유지한다.
- `FinalResponseCard`의 Markdown 렌더러는 `img` 요소를 금지해 외부 이미지를 자동 로드하지 않는다. 내부 챗봇 입력은 WebKit의 조합 확정 `keyCode 229` Enter도 전송에서 제외한다.
- 인증 내부 실행 오류: 문서화된 HTTP status를 fixed 사용자 메시지로 mapping하고, 알 수 없는 `response.data.detail` 원문을 화면에 표시하지 않는다.
- 인증 복귀: run-info가 `401`을 반환하면 원래 path/query/hash를 인코딩한 `/auth/login?next=...`로 이동한다. 공통 redirect helper가 API interceptor와 page handler의 중복 이동을 조정한다. 이메일/비밀번호와 Google OAuth 로그인 성공 후 safe same-origin `next`로 복귀하고 외부·malformed URL은 `/dashboard`로 닫는다.

## States

- 내부 `conversationId`: 내부 실행 페이지 마운트 시 생성하는 canonical UUID다. `localStorage`에 저장하지 않고 top-level `conversation.client_id`로만 전송한다. 서버가 deployment/current user에 결박한 `auth:v1` namespace로 바꾼다.
- 공개 `messages`: 현재 page lifetime에서 성공한 사용자 질문과 assistant 응답을 순서대로 표시하는 React state다. welcome/error/pending message를 제외한 최신 20개 완료 turn만 다음 요청 history로 보낸다. 서버에는 Public Conversation Session이나 execution-log memory를 만들지 않는다.
- 공개 표시 응답 중 실제 성공·non-empty final preview만 `historyEligible=true`다. 실패/empty 결과의 UI fallback은 화면에 표시해도 다음 history에서는 제외한다.
- 공개 배포 `history_consumer`: top-level과 nested Loop의 모든 LLM을 canonical `{container_path,node_id}`로 구분한다. Select value는 전체 location을 직렬화한 key이고 deployment config에는 구조화된 path를 저장한다.
- 공개 페이지는 conversation ID와 대화 원문을 localStorage/sessionStorage에 저장하지 않는다. refresh/new tab은 빈 history로 시작한다.

인증 내부 durable Session과 transcript projection은 [Conversation Memory component spec](../conversation-memory/component_spec.md)의 후속 target이다. 같은 채팅 시각 컴포넌트는 재사용하되 public/authenticated backend surface, API/auth adapter, CORS/Origin, deployment access policy, session namespace와 secret storage는 분리한다.

## Interactions

- 공개 메시지 전송: 사용자 입력을 첫 입력 변수에 매핑하고, 완료된 최신 20개 turn을 sibling `{ conversation: { history } }`에 담는다. `memory_mode`, `conversation_id`, capability token은 보내지 않는다.
- 내부 메시지 전송: 업무 입력은 `inputs`에 그대로 두고 `{ conversation: { client_id } }` control을 sibling field로 전송한다. 서버가 Chatbot memory mode를 강제하므로 Client가 `inputs.memory_mode`를 보내지 않는다.
- 내부 링크 인증 복귀: 인증이 없거나 만료하면 현재 실행 링크를 `next`로 보존한다. 이메일/비밀번호 로그인은 검증된 `next`로 즉시 복귀하고 Google OAuth는 Gateway 서명 session에 저장된 10분·1회용 `next`를 callback에서 소비해 복귀한다.
- 응답 추출: `results`에서 `answer` 필드를 가진 노드 결과를 찾아 assistant 메시지로 표시한다(기존 로직 유지).

Authenticated internal target interaction은 durable conversation envelope과 idempotency key를 사용하고 node별 Memory config를 deployment snapshot에서 읽는다. Public UI의 React state는 다음 요청에 전달할 일시적 맥락의 source이며 durable transcript가 아니다.

Public Client는 deployment-owned parent embedding policy가 확인된 surface만 iframe으로 제공한다. 이 정책은 CSP `frame-ancestors`에만 사용하고 direct API CORS 허용으로 재사용하지 않는다. Login 상태를 감지해 public adapter를 authenticated mode로 바꾸지 않으며, 내부 Chatbot link/component는 별도 access bootstrap 성공 후에만 렌더링한다.

`/embed/chat/{urlSlug}` document는 계속 relative info/run API만 사용한다. Parent origin, `document.referrer`, request Origin과 current location을 API body/header/query 또는 policy fallback으로 보내지 않는다.

## Citation List

- 공개 embed Chatbot과 인증 내부 Chatbot은 assistant 답변 아래에 동일한 `CitationList`를 표시한다.
- 인증 내부 Chatbot은 `CitationList`에 `appearance="chat"`을 전달해 라이트 전용 응답 영역에 `dark:` 변형을 렌더링하지 않는다. 공개 embed Chatbot과 일반 실행 화면은 기존 기본 appearance를 유지한다.
- 기본 상태는 접힌 목록이고 summary에 출처 수를 표시한다. keyboard로 열고 닫을 수 있어야 한다.
- `basic`은 라벨과 page/section, `detailed`는 추가로 정제된 preview를 표시한다. raw URL/path나 내부 식별자를 링크로 만들지 않는다.

## Accessibility

- 내부 질문 입력은 자동 높이 `textarea`, 전송은 `button[type=submit]`이다. `Enter` 전송과 `Shift+Enter` 줄바꿈 안내를 composer 아래에 표시하고 전송 중에는 입력/버튼을 비활성화한다.

## Public Conversation Consumer Selection

- 공개 Chatbot 배포 모달은 “대화 기록을 사용할 LLM 노드” select를 표시한다.
- LLM node가 하나면 해당 node를 선택 상태로 표시하고, 여러 개면 사용자가 하나를 고르기 전 배포 버튼을 비활성화한다.
- 선택값은 preflight/create의 동일한 versioned config로 보내며 최종 runtime은 Gateway와 Worker의 canonical snapshot 검증 결과를 사용한다.
- Embed Chat은 public info의 `client_history_v1` capability에서만 `/chat` history envelope을 보낸다.
- capability가 `legacy_v0`이거나 누락되면 mixed-revision 호환 root를 사용하되 `memory_mode`와 `conversation_id`를 보내지 않는다.
- 새 Gateway compatibility adapter는 이 root 요청을 server-side Memory가 아닌 stateless public 실행으로 변환한다.
- strict rollout 전환 뒤 legacy capability deployment는 새 consumer config version으로 재배포해야 한다.
