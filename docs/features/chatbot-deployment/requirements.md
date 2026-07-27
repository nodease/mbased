# Chatbot Deployment Requirements

Status: Draft
Related Features: deployment, workflow, conversation-memory, llm-credentials, audit-tracing

## Purpose

`시작 입력 노드`(코드 타입 `startNode`)로 시작하는 워크플로우를 **공개 챗봇** 또는 로그인 사용자 권한을 적용하는 **내부 챗봇**으로 배포하는 기능을 제공한다. 챗봇은 사용자와 여러 턴에 걸쳐 대화하며, **이전 대화 맥락을 기억**한다.

이 feature는 [deployment](../deployment/requirements.md)의 배포 타입/공개 실행 표면 위에 챗봇 전용 배포 타입과 bounded client-held 대화 맥락을 additive로 추가한다. 공개 챗봇은 기존 임베드 챗 페이지(`app/embed/chat/[urlSlug]`)를 사용하고, 인증 내부 챗봇은 별도 실행 페이지(`app/modules/[id]/run`)를 사용한다.

## Contract Status

Public Chatbot의 현재 계약은 [ADR-0074](../../decisions/ADR-0074-public-chatbot-client-held-history.md)와 [Conversation Memory](../conversation-memory/requirements.md)의 bounded client-held history다. Public route는 `memory_mode`, browser `conversation_id`, execution-log memory와 server-side Conversation Session을 사용하지 않는다. 현재 `internal_chatbot`은 인증 deployment run/run-info surface에서 active membership·workflow `execute` 확인과 current-user execution subject를 적용하는 실행 계약을 제공한다. 별도 내부 Chatbot access grant와 durable Conversation Session은 후속 범위다.

## User Stories

- 빌더로서, `startNode`로 시작하는 워크플로우를 클릭 한 번으로 공개 챗봇 링크로 배포하고 싶다.
- 조직 구성원으로서, 내 workflow·Knowledge 권한을 적용하는 인증 내부 챗봇을 실행하고 싶다.
- 챗봇 방문자로서, 현재 열린 화면의 여러 턴에 걸쳐 이전 대화 맥락을 챗봇이 반영하기를 원한다.
- 챗봇 방문자로서, 새로고침 후에는 이전 대화가 서버에서 자동 복구되지 않기를 원한다.
- 챗봇 방문자로서, 다른 방문자의 대화 내용이 내 대화에 섞이지 않기를 원한다.

## Functional Requirements

- CBOT-REQ-001: 배포 진입점("게시하기" 드롭다운)은 **"공개 챗봇 배포"**와 **"내부 챗봇 배포"**를 별도 항목으로 표시한다. 두 항목은 시작 노드가 `startNode`인 워크플로우에서만 노출하고 `webhookTrigger`/`scheduleTrigger` 시작 노드에는 노출하지 않는다.
- CBOT-REQ-002: 공개 챗봇은 `DeploymentType.chatbot`, 내부 챗봇은 `DeploymentType.internal_chatbot` 타입의 배포를 생성한다. 그래프 스냅샷, `url_slug` 지연 생성, 단일 활성 배포와 input/output 스키마 추출은 기존 배포 경로를 사용한다. App 인증 secret은 배포 생성 응답에서 만들거나 반환하지 않고 [deployment](../deployment/requirements.md)의 별도 lifecycle API에서 발급·교체한다.
- CBOT-REQ-003: 배포 성공 시 `${origin}/embed/chat/{url_slug}` 형태의 공개 챗봇 공유 링크를 제공한다. 이 링크는 무인증 공개 실행 표면(`POST /run-public/{url_slug}/chat`)을 사용한다.
- CBOT-REQ-003a: 공개 챗봇 활성 배포 생성/전환은 deployment preflight를 통과해야 한다. `/run-public`은 사용자 execution subject를 주입하지 않으므로 private KB 후보가 있으면 `409 deployment.preflight.blocked`로 활성화를 차단한다.
- CBOT-REQ-003b: 내부 챗봇은 인증 deployment run/run-info endpoint만 사용한다. Gateway는 대상 workflow organization의 active membership과 workflow `execute` 권한을 확인하고, `X-Organization-Id`가 전달되면 배포 앱 organization과의 일치도 확인한 뒤 현재 로그인 사용자를 runtime `execution_subject`로 전달한다.
- CBOT-REQ-003c: 비로그인 사용자가 내부 챗봇 실행 링크를 열어 run-info에서 `401`을 받으면 클라이언트는 `/auth/login?next=<원래 path+query+hash>`로 이동한다. 이메일/비밀번호와 Google OAuth 로그인 모두 같은 origin의 안전한 `next`로 복귀하고, 외부·프로토콜 상대·malformed/중첩-encoded URL은 `/dashboard`로 fallback한다. Google OAuth 복귀 컨텍스트는 서명 session에서 10분 안에 한 번만 소비한다.
- CBOT-REQ-003d: 인증 실행 요청은 업무 `inputs`와 별도의 top-level `conversation.client_id`에 canonical UUID를 전달한다. Gateway는 이 값을 deployment와 current user에 결박한 versioned internal namespace로 바꾸며, raw client id를 workflow input, response, audit 또는 log에 노출하지 않는다.
- CBOT-REQ-003e: 인증 실행의 `conversation.client_id`는 Chatbot deployment에서만 허용한다. `inputs`에 선언된 `conversation_id` 또는 `memory_mode` workflow 변수는 업무 입력으로 보존하며, typed control과 선언되지 않은 legacy reserved control이 동시에 오면 모호한 요청으로 거부한다.
- CBOT-REQ-003f: 현재 인증 실행 mutation은 `Content-Type: application/json`만 허용하고, 누락·`text/plain`·form-urlencoded·multipart는 workflow dispatch 전에 `415`로 거부한다. Credentialed CORS는 명시적 HTTP(S) allowlist를 사용하고 wildcard 구성을 거부한다. 이는 현행 browser 경계이며 Target의 별도 CSRF token/exact-Origin/access grant를 대체하지 않는다.
- CBOT-REQ-003g: 인증 내부 챗봇의 text 응답은 raw HTML 실행 없이 GitHub Flavored Markdown의 제목, 강조, 목록, 인용, inline code, link와 table을 렌더링한다. Markdown 이미지는 렌더링하지 않아 응답 열람이 외부 이미지 요청을 발생시키지 않는다. 질문 composer는 IME 조합 중이 아닐 때 `Enter`로 전송하고 `Shift+Enter`로 줄바꿈한다. 내부 실행 화면의 제목, 메시지, 상태, 입력과 전송 control은 일반 workflow 실행 화면과 같은 기본 크기를 사용한다.
- CBOT-REQ-004: 공개 챗봇 Client는 현재 React state의 완료된 `user`/`assistant` pair만 `conversation.history`에 담아 전용 `/run-public/{url_slug}/chat` 경로로 보낸다. 첫 요청은 빈 history다.
- CBOT-REQ-005: Gateway는 Public history의 exact shape, role 교대, 20 turn, message 크기, UTF-8과 현재 inputs를 포함한 4,096-token 상한을 provider dispatch 전에 검증한다. 오래된 맥락 제거는 완료 turn 단위로만 수행한다.
- CBOT-REQ-006: Public history는 신뢰할 수 없는 대화 맥락으로만 사용한다. system/developer/tool role, extra field와 legacy `memory_mode`/`conversation_id` control을 거부하고 인증·인가·resource provenance 판단에는 사용하지 않는다.
- CBOT-REQ-007: 공개 챗봇은 server-side 대화 row와 execution-log memory를 생성하거나 조회하지 않는다. Client는 history를 React memory에만 유지하며 refresh/new tab에서 복구하지 않고 localStorage/sessionStorage에 원문이나 conversation ID를 저장하지 않는다.
- CBOT-REQ-008 (Authenticated Internal Target): Chatbot deployment는 Conversation Session surface를 제공할 수 있지만 모든 LLM node의 Memory를 강제하지 않는다. Node별 versioned Memory config의 기본값은 OFF다.
- CBOT-REQ-009 (Authenticated Internal Target): 내부 Chatbot은 authenticated subject와 organization에 결박된 Conversation Session을 사용하고 client-generated UUID를 접근 capability로 사용하지 않는다.
- CBOT-REQ-010 (Authenticated Internal Target): durable Conversation metadata는 업무 `inputs`와 분리된 envelope로 전달하며 dedicated Memory store가 source of truth다. Workflow execution log는 conversation reader가 아니다.
- CBOT-REQ-011: 공개·인증 챗봇은 시각 컴포넌트를 재사용할 수 있지만 anonymous client-history API와 cookie-authenticated API/CSRF 경계를 분리한다.
- CBOT-REQ-012 (Target): `public_chatbot`과 `authenticated_internal_chatbot`은 backend runtime policy, route, authentication/CORS/Origin, deployment access permission과 session namespace를 분리해야 한다. Public route의 optional login으로 내부 권한을 허용해서는 안 된다.
- CBOT-REQ-013: Public Chatbot은 login cookie 존재 여부와 무관하게 anonymous public-only RAG를 사용하고 private KB 후보가 있으면 activation preflight를 차단해야 한다.
- CBOT-REQ-014 (Target): Authenticated internal Chatbot은 별도 내부 Chatbot 이용 권한과 current user의 KB permission/source ACL을 모두 통과해야 한다. Workflow `execute` 또는 Public history payload만으로 내부 이용 권한을 대체해서는 안 된다.
- CBOT-REQ-015 (Target): 별도 기능 이슈에서 deployment access mode와 permission/default grant 정책이 구현되면 현재 `internal_chatbot`의 active membership·workflow `execute`·KB permission 재검사 위에 추가한다. Conversation Memory 설계만으로 Target access/session 계약이 현재 동작한다고 간주하지 않는다.
- CBOT-REQ-016: Public browser Chatbot과 같은 `/embed/chat/{slug}` route의 Widget은 deployment-owned immutable `browser_access_policy`를 가져야 한다. Parent 허용은 iframe HTML response의 CSP `frame-ancestors`만 소유하며 first-party iframe API origin과 external direct JavaScript CORS origin을 같은 목록으로 취급해서는 안 된다 ([ADR-0043](../../decisions/ADR-0043-deployment-browser-origin-and-embedding-boundary.md)).
- CBOT-REQ-016a: 신규 `chatbot`/`widget` policy 생략은 disabled canonical policy로 저장하고 legacy null, malformed/unknown policy, lookup timeout과 inactive/wrong-type deployment는 `frame-ancestors 'none'`으로 fail-closed해야 한다. `internal_chatbot`과 다른 type의 non-null policy는 거부한다.
- CBOT-REQ-016b: Parent origin은 exact canonical HTTPS만 허용한다. Dev/test HTTP는 `localhost`, `127.0.0.1`, `[::1]`만 허용하며 wildcard/null/local scheme/userinfo/path/query/fragment/control, canonical duplicate, 20개·4096-byte 상한 초과와 environment/client fallback을 거부한다.
- CBOT-REQ-016c: Public iframe은 relative first-party info/run API를 유지하고 public endpoint의 수동 wildcard CORS를 제거해야 한다. No-Origin non-browser 호출은 public route를 사용할 수 있지만 CORS를 authentication으로 사용하지 않으며 external direct JavaScript SDK는 V1에서 지원하지 않는다.
- CBOT-REQ-016d: Browser policy 변경은 active row PATCH가 아니라 source snapshot을 복제한 새 deployment version을 만들고 기본 inactive로 저장해야 한다. Active pointer 변경은 이미 로드된 document CSP를 바꾸지 않으며 authenticated internal Conversation Session도 새 version으로 자동 rebind하지 않는다.
- CBOT-REQ-017 (Authenticated Internal Target): Conversation Session은 deployment ID와 immutable version 또는 snapshot hash, conversation mapping/Memory policy version에 고정하며 active deployment 변경에 자동 rebind하지 않아야 한다.
- CBOT-REQ-018: Public request lifecycle audit은 `actor_id=null`, `actor_type='public'`을 사용하고 대화 원문을 저장하지 않는다. 인증 내부 durable lifecycle의 audit actor는 canonical authenticated subject 또는 system actor를 사용한다.

## Policies And Edge Cases

- 공개 실행에서 앱 소유자를 사용하더라도 이는 deployment policy가 정한 credential/billing principal일 뿐 execution subject나 audit actor가 아니다. Public history는 방문자 identity나 권한 주체가 아니다.
- 공개 챗봇 실행은 anonymous public-only RAG 경계다. 현재 `internal_chatbot` 인증 실행은 로그인 사용자의 KB 권한을 평가하지만, target private-KB 내부 Chatbot 이용 권한·session 정책은 별도 기능으로 추가한다.
- Public `conversation.history`는 prompt injection/secret-like marker를 정제하되 허용된 message 크기를 별도의 더 작은 런타임 상한으로 다시 자르지 않는다.
- Public WorkflowRun/NodeRun/Trace는 운영 상태, token, latency와 routing 결과 같은 content-free metadata만 유지하고 input/history/prompt/completion 원문은 저장하지 않는다.
- 현재 인증 실행은 typed conversation control을 사용해 신규 내부 호출의 reserved-input collision을 제거한다. Legacy authenticated compatibility는 Public client-held contract와 분리한다.
- 현재 내부 실행 UI는 first-party configured CORS origin에서 JSON 요청만 보낸다. 브라우저의 unlisted-origin JSON 요청은 preflight에서 차단되고 simple cross-site content type은 `415`로 dispatch 전에 차단되지만, 별도 CSRF token과 exact-Origin 검사는 아직 Target이다.

## User-visible Citations

- 공개 Chatbot과 인증 내부 Chatbot은 Workflow final response의 safe Citation sidecar를 동일하게 표시한다.
- Citation 표시는 LLM node의 `citationDisplayMode`를 따르며 공개 route에서는 anonymous public-only runtime gate를 통과한 evidence만 허용한다.
- Citation을 만들 수 없거나 malformed sidecar를 받더라도 답변은 계속 표시하고 Citation 목록만 생략한다.

## Open Questions

- 다중 입력 변수 챗봇 지원(현재는 사용자 메시지를 첫 입력 변수에 매핑).
