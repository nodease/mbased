# Chatbot Deployment Test Cases

Status: Draft

## Unit Tests

Public Chatbot은 bounded client-held history 계약을 검증한다. `memory_mode`/`conversation_id`/execution-log 테스트는 authenticated legacy compatibility에만 한정하고 Public current contract와 섞지 않는다. Durable internal target 테스트는 [Conversation Memory test cases](../conversation-memory/test_cases.md)를 따른다.

### Gateway — `apps/gateway/tests/services/test_chatbot_deployment_run.py`

- 공개 Chatbot `/run-public/{slug}/chat`은 bounded `conversation.history`를 업무 inputs와 분리해 dispatch하고 `memory_mode=false`, `conversation_id=null`을 유지한다.
- 공개 Chatbot은 history 누락, malformed envelope, 잘못된 role/order/extra field, 21 turn, oversized input과 isolated Unicode surrogate를 provider dispatch 전에 content-free `422 conversation.*`로 거부한다.
- 공개 Chatbot은 legacy `inputs.memory_mode`/`inputs.conversation_id`를 거부하고 server-side execution-log memory를 다시 활성화하지 않는다.
- 비-Chatbot 공개 root 실행(webapp/widget 등)은 client history를 전달하지 않고 기존 CORS/실행 계약을 유지한다.
- 공개 실행은 `execution_subject`를 주입하지 않고 workflow owner 권한으로 private RAG를 fallback하지 않는다.
- 인증 내부 실행(`/deployments/{deployment_id}/run`)은 `execution_context.execution_subject`에 로그인 사용자를 주입하고 예산 actor도 로그인 사용자로 기록한다.
- `internal_chatbot` 인증 실행은 로그인 사용자를 `execution_subject`로 전달하고, 챗봇 `memory_mode`를 강제하며, top-level `conversation.client_id`를 deployment와 사용자 기준 `auth:v1` namespace로 처리한다.
- 인증 내부 실행은 typed conversation control을 업무 `inputs`와 분리하고 선언된 `conversation_id`/`memory_mode` workflow 변수를 그대로 dispatch한다.
- typed control과 선언되지 않은 legacy `inputs.conversation_id`가 동시에 오면 `400`으로 거부하고, non-Chatbot deployment의 typed control도 `400`으로 거부한다.
- legacy authenticated `inputs.conversation_id`는 string·최대 255자·control character 금지 조건을 적용하며 arbitrary object를 stringify하지 않는다.
- 인증 내부 namespace는 같은 deployment/user/client UUID에서 안정적이고, user 또는 deployment가 다르면 달라야 하며 raw client UUID를 포함하지 않는다.
- 인증 내부 실행은 활성 배포가 아니거나 app의 `active_deployment_id`와 일치하지 않는 배포를 거부한다.
- 실행 화면용 run-info는 `auth_secret`, `graph_snapshot`을 반환하지 않고 입력/출력 schema와 표시 metadata만 반환한다.
- 엔진 실패 예외 문자열에 secret-like 값이 있어도 배포 실행 응답 detail에는 원문을 노출하지 않는다.

### Workflow Engine — Public history and authenticated legacy compatibility

- Public history는 system message 뒤, 현재 user prompt 앞에 untrusted block으로 삽입되고 legacy `WorkflowRun` memory 조회를 하지 않는다.
- Gateway가 허용한 4,000자 초과 message도 generic structured-value cutoff로 잘리지 않으며 prompt-injection/secret-like 정제는 유지한다.
- History content는 한 번 정제한 projection을 provider block과 RAG 검색어에 공유하며, redaction marker 재검사로 정상 turn을 함께 지우지 않는다.
- RAG follow-up은 현재 질문과 가장 최근 완료 pair를 1,000자 안에서 검색어에 포함하고 오래된 초과 pair를 제외한다. History가 없으면 현재 질문 검색어가 바뀌지 않으며 candidate·authorization 결과도 동일하다.
- authenticated legacy `conversation_id`가 있으면 `_build_memory_summary`의 기존 격리 query를 유지하고, `memory_mode`가 꺼져 있으면 조회하지 않는다.

### Workflow Logging

- Public content suppression은 input/history/prompt/completion, credential ID와 nested routing input을 저장하지 않는다.
- Public content suppression에서도 provider/model, token count, latency와 routing/fallback outcome 같은 allowlisted scalar metadata는 유지한다.
- authenticated legacy compatibility의 `conversation_id` 저장 테스트는 Public contract와 분리한다.

### Client — deployment UI and authentication return

- 배포 메뉴는 `공개 챗봇`과 `내부 챗봇`을 별도 항목으로 표시하고 각각 `chatbot`, `internal_chatbot` 배포를 생성한다.
- `useDeployment`의 공개 챗봇 결과는 `${origin}/embed/chat/{url_slug}`만 만들고, 내부 챗봇 결과는 `${origin}/modules/{workflow_id}/run?deploymentId={deployment_id}` 인증 링크만 만든다.
- `SuccessStep`은 선택한 챗봇 유형에 맞는 공개 링크 또는 사내 인증 링크만 표시하고 두 보안 경계를 한 배포 결과에서 섞지 않는다. 내부 챗봇에는 public REST API endpoint/secret/test panel을 표시하지 않는다.
- 공개 챗봇 공유 링크 설명은 private Knowledge 접근을 암시하지 않는다.
- 공개 챗봇 페이지는 성공한 최신 20개 turn만 `/run-public/{slug}/chat`의 `conversation.history`로 보내고 conversation ID나 원문을 browser storage에 저장하지 않는다.
- 내부 실행 페이지는 `internal_chatbot`을 실행할 때 업무 `inputs`와 별도의 non-empty canonical `conversation.client_id`를 전송하고 client-controlled `memory_mode`를 보내지 않는다.
- 내부 챗봇 실행 페이지는 사용자 선택기 없이 대화 내용을 위에, 질문 입력창과 전송 버튼을 아래에 표시한다.
- 내부 챗봇 실행 페이지 우상단은 현재 로그인 사용자 이름과 사용자 권한 적용 상태를 함께 표시한다. 사용자 정보 조회 실패 시 이름은 생략하되 실행 화면과 권한 상태 표시는 유지한다.
- 내부 챗봇 실행 페이지는 같은 화면에서 보낸 사용자 질문과 응답을 순서대로 누적하고, 전송 성공 후 질문 입력창을 비운다.
- 내부 챗봇 질문 composer는 입력과 전송 버튼을 세로 중앙 정렬한다. 질문 입력은 내부 스크롤 없이 줄 수에 맞춰 높이가 늘어나고 전송 후 한 줄 높이로 돌아가며, 최종 응답도 카드 내부 스크롤 없이 전체 내용을 펼쳐 표시한다.
- 내부 챗봇은 초록 계열 강조 class와 OS 색상 설정으로 활성화되는 `dark:` class를 렌더링하지 않고 파란 focus 상태를 사용한다. 최종 응답 header의 바깥 배경·테두리 card는 각 class가 개별적으로 제거되었는지 검증하며 실제 응답 box는 icon column까지 span한다.
- 내부 챗봇 text 응답의 Markdown 강조와 목록은 실제 semantic element로 렌더링하고 raw Markdown 표식을 그대로 표시하지 않는다. Markdown 이미지는 `<img>`로 렌더링하지 않고 외부 URL을 요청하지 않는다. 내부 챗봇 제목, 메시지, 입력과 전송 control은 일반 workflow 실행 화면과 같은 기본 크기를 사용한다.
- 내부 챗봇 질문 입력에서 IME 조합 중이 아닌 `Enter`는 한 번만 전송하고 `Shift+Enter`는 전송하지 않은 채 줄바꿈을 허용한다. WebKit에서 `isComposing`이 false인 `keyCode 229` 확정 Enter도 전송하지 않는다. 빈 값과 실행 중 입력은 전송하지 않는다.
- 챗봇이 아닌 인증 배포는 여러 입력 변수를 지원하는 기존 실행 폼과 결과 영역을 유지한다.
- 내부 실행 페이지는 backend의 문서화되지 않은 임의 `detail` string을 표시하지 않고 status별 fixed safe message를 사용한다.
- 내부 실행 링크에서 `401`을 받으면 `/auth/login?next=<원래 내부 실행 경로>`로 이동하고, 이메일/비밀번호와 Google OAuth 로그인 성공 후 safe same-origin `next` 경로로 복귀한다.
- 절대 URL, `//host`, backslash, dot segment, control character, malformed 또는 과다 중첩 encoding처럼 안전하지 않은 `next` 값은 무시하고 `/dashboard`로 이동한다. Google OAuth return context는 10분 만료와 1회 소비를 검증한다.

## API Tests

- `POST /deployments`에 `type: "chatbot"`으로 배포 생성 → 활성 배포 및 `url_slug` 반환.
- `POST /deployments`에 `type: "chatbot"`, `is_active=true`, private KB RAG 후보가 있으면 `409 deployment.preflight.blocked`를 반환한다.
- `GET /deployments/{deployment_id}/run-info`는 workflow `execute` 권한을 요구하고, active organization scope가 app organization과 다르면 404를 반환한다.
- `POST /deployments/{deployment_id}/run`은 workflow `execute` 권한을 요구하고, `inputs`가 object가 아니면 400을 반환한다.
- `POST /deployments/{deployment_id}/run`은 `application/json`만 허용하고 Content-Type 누락, `text/plain`, form-urlencoded, multipart 요청을 415로 거부하며 실행 service를 호출하지 않는다.
- Credentialed CORS preflight는 configured origin에 allow-origin/allow-credentials를 반환하고 unlisted origin에는 allow-origin을 반환하지 않으며 실행 service를 호출하지 않는다. Wildcard·malformed CORS 설정은 Gateway 시작 전에 거부한다.
- `GET /deployments/public/{slug}/info` → `type: "chatbot"` 직렬화 확인.
- Chatbot/Widget create에서 omitted browser policy는 canonical disabled로 저장되고 enabled policy는 exact canonical origins로 응답한다. Internal Chatbot/other type의 non-null policy는 fixed 422다.
- Preflight는 Knowledge passed/warning/blocked와 별개로 valid canonical `normalized_browser_access_policy`를 반환하고 malformed policy는 inactive preview에서도 422다.
- Browser policy revision은 source graph/config/input/output/description을 보존하고 새 inactive version을 만들며 source/current draft/active pointer를 변경하지 않는다. Active revision은 기존 preflight와 single-active transaction을 사용한다.
- Public browser policy projection은 active app ownership/type을 검증하고 safe field만 반환한다. Null/malformed/unknown policy는 disabled, inactive/wrong type/cross-app pointer는 safe 404다.
- Public Chatbot `/run-public/{slug}/chat`과 trailing-slash redirect 경계의 성공, OPTIONS, malformed JSON, wrong content type와 validation error는 configured Origin에도 CORS grant가 없고 no-store/no-referrer를 유지한다. 비-Chatbot 공용 root는 configured first-party global CORS 계약을 유지한다.

## E2E Tests

- `startNode → llmNode → answerNode` 워크플로우를 "공개 챗봇 배포"로 배포하고 `${origin}/embed/chat/{slug}` 공유 링크 확인.
- 챗봇 링크에서 2~3턴 대화 → N턴 응답이 N-1턴 맥락을 반영(기억 동작).
- 다른 브라우저/시크릿에서 열면 공유된 React state가 없어 첫 대화 맥락이 새지 않는다.
- 대화 중 새로고침하면 과거 history가 자동 복구되지 않고 빈 대화로 시작한다.

## Permission Tests

- 배포 생성은 `deploy` 권한 없는 사용자에게 거부(기존 deployment 권한 테스트 범위).
- 내부 챗봇은 authenticated deployment run/run-info surface에서만 허용하고 public info 및 `/run-public` surface에서는 거부한다.
- 내부 챗봇 preflight는 `authenticated_user` audience로 private KB 참조를 허용하되, 실제 실행 시점 권한 검사를 대체하지 않는다.
- 인증 내부 실행은 `execute` 권한 없는 사용자에게 거부한다.
- 공개 실행은 무인증 표면이므로 private Knowledge/RAG 후보를 anonymous public-only 경계 밖으로 확장하지 않는다.
- 공개 챗봇 활성화 preflight는 client-supplied audience hint로 우회할 수 없다.

## MBA-238 Internal Chatbot Subject Integrity Tests

- 인증 실행 request body의 top-level `user_id`, `organization_id`, `execution_subject`와 기타
  unknown field는 schema validation에서 거부하고 실행 service를 호출하지 않는다.
- Gateway가 dispatch하는 subject는 현재 로그인 사용자여야 하며 deployment creator,
  workflow owner 또는 request input으로 대체되지 않는다. Organization은 server가 조회한
  deployment app organization을 사용한다.
- `X-Organization-Id`가 app organization과 다르면 workflow permission check와 dispatch 전에
  resource-hiding 응답으로 차단한다.
- Gateway subject dispatch의 기존 service/API 테스트를 재사용하고, Knowledge 후보 차이는
  production PostgreSQL resolver와 Workflow LLM node 통합 테스트에서 검증한다.

## Target Runtime Boundary Tests

- Public route에 valid login cookie가 있어도 execution principal은 anonymous public audience이며 private KB/Memory를 사용하지 않는다.
- Public history payload로 authenticated internal route를 호출하거나 내부 Chatbot 이용 권한을 얻을 수 없다.
- Authenticated internal Chatbot은 별도 access permission, current KB permission/source ACL, CSRF와 exact Origin을 모두 요구한다. Workflow `execute`만 있는 사용자는 허용되지 않는다.
- Public/internal Chatbot은 시각 message/input component를 재사용하지만 API adapter, credential storage와 session namespace가 섞이지 않는다.
- 별도 internal deployment/access mode가 없는 public Chatbot 성공 화면은 generic run link를 Target 내부 Chatbot으로 표시하지 않는다.
- Public activation은 private KB 후보를 차단하고, future internal activation 결과가 public preflight audience를 완화하지 않는다.
- Missing/null/unlisted Origin, wildcard, client audience/config와 environment fallback은 public iframe embedding bootstrap 전에 거부된다.
- Session은 deployment version/snapshot과 mapping/Memory policy version에 고정되고 active deployment 교체 후 자동 rebind하지 않는다.
- Public request audit actor는 `actor_id=null`, `actor_type='public'`이며 app owner와 credential/billing principal이 actor로 기록되지 않는다. Public history 원문은 audit에 저장하지 않는다.

## Browser Embedding Security Tests

- Exact HTTPS origin, default port, UTS #46 non-transitional IDN, canonical IPv4/IPv6를 정규화한다.
- Wildcard/null/local scheme/userinfo/path/query/fragment/control/trailing-dot/legacy IP, invalid IDNA, production HTTP, canonical duplicate, 21개와 4097-byte header value를 거부한다.
- Dev/test HTTP는 exact `localhost`, `127.0.0.1`, `[::1]`만 허용하고 missing/unknown environment는 production으로 닫는다.
- Allowed parent에서 iframe document와 relative info/run API가 성공하고 unlisted parent에서는 browser가 CSP로 frame을 차단한다.
- 중첩 iframe은 top-level/intermediate ancestor가 모두 allowlist에 있을 때만 성공한다.
- Request Origin/Referer/Host/query와 Gateway projection timeout/404/5xx/malformed response는 allowlist를 넓히지 않고 `'none'`을 유지한다.
- Login cookie가 있는 public iframe과 없는 iframe 모두 anonymous public-only이며 private KB audience로 승격하지 않는다.
- External parent script가 Gateway public endpoint를 직접 fetch해도 deployment parent 기반 ACAO를 받지 않는다.
- Widget도 공유 `/embed/chat` route에서 같은 allowed/denied parent 결과를 가지되 Conversation Session을 생성하지 않는다.
- Release B rollback artifact는 broad `http: https: file: data:`를 복원하지 않고 dynamic policy 또는 deny-all `'none'`을 유지한다.

## Citation Tests

- public/internal Chatbot 성공 응답의 safe Citation sidecar가 assistant message 아래에 표시되는지 검증한다.
- 내부 Chatbot의 citation 포함 assistant 영역은 `dark:` class를 렌더링하지 않고 라이트 전용 텍스트와 테두리를 유지하는지 검증한다.
- public Chatbot에서 private/denied Collection child evidence가 답변과 Citation에 나타나지 않는지 검증한다.
- unknown version, malformed item, 내부 identity, URL/path 또는 secret marker가 있는 Citation은 숨기되 assistant 답변은 유지하는지 검증한다.
- Citation이 없는 no-evidence/legacy 응답과 mobile viewport에서 빈 공간·overflow 없이 렌더링되는지 검증한다.
- `output_schema`가 없거나 비어 있는 legacy public Chatbot이 custom-named text output 하나를 반환해도 해당 답변을 계속 표시하는지 검증한다.

## Edge Cases

- Public 요청에 legacy `conversation_id`/`memory_mode` control을 넣으면 업무 input으로 pop하지 않고 fixed `422`로 거부한다.
- 공개 페이지는 localStorage/sessionStorage 사용 가능 여부와 무관하게 conversation ID 또는 history 원문을 저장하지 않는다. 내부 페이지의 page-session UUID는 별도 authenticated control이다.
- 인증 workflow가 `conversation_id` 또는 `memory_mode`라는 입력 변수를 선언해도 typed control과 혼동하지 않고 업무 값이 보존된다.
- 다른 organization을 active context로 선택한 사용자가 내부 링크를 열면 run-info는 `404`를 반환하며 클라이언트는 링크만으로 organization을 자동 전환하지 않는다.
