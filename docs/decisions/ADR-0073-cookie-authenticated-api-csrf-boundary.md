# ADR-0073: Cookie-authenticated API CSRF boundary

Status: Accepted

## Context

Gateway 사용자 인증은 `auth_token` HttpOnly JWT cookie를 사용한다. Non-local 환경에서는 credentialed cross-origin Client를 지원하기 위해 `Secure`, `SameSite=None`을 사용할 수 있다. CORS는 응답을 읽을 수 있는 origin을 제한하지만 요청의 의도나 mutation 권한을 증명하지 않으며, SameSite도 이 배포 형태의 독립된 CSRF 방어가 아니다.

Gateway에는 공통 `get_current_user` dependency를 사용하는 route 외에도 자체 cookie 인증 helper, 로그인 전 mutation, 익명 Public 실행, app secret 기반 실행과 webhook이 함께 존재한다. Cookie 존재 여부만으로 정책을 추론하면 Public audience를 사용자 권한으로 승격하거나 보호 mutation을 누락할 수 있다.

Safe GET도 응답의 `Set-Cookie` 부수효과를 가진다. Ambient cross-site image/navigation GET이 bootstrap token을 회전시키면 Client memory header와 host-only cookie가 달라져 non-replayable workflow mutation을 지속적으로 막을 수 있다. 또한 Client가 same-origin reverse proxy와 별도 공개 API origin을 함께 사용하면 token body와 host-only cookie가 서로 다른 host에 놓일 수 있다.

## Options Considered

### Option A: CORS와 SameSite만 유지

- 장점: Client 변경이 없다.
- 단점: 서버 mutation 승인 증거가 없고 CORS 구성 오류와 cross-site cookie 전송에 취약하다.

### Option B: Server-side synchronizer token 저장소 도입

- 장점: 중앙 revoke와 단일 사용 token을 구현할 수 있다.
- 단점: 현재 stateless JWT 인증에 별도 session 저장소와 가용성·정리 정책이 추가된다.

### Option C: Session·organization에 결박한 signed double-submit token

- 장점: 별도 durable session row 없이 cookie injection, token 변조와 다른 session·organization replay를 막을 수 있다.
- 단점: Client bootstrap/lifecycle, route inventory와 배포 순서를 함께 관리해야 한다.

Bootstrap의 `Set-Cookie` 회전 방어에서는 exact Origin만 요구하는 방안, 기존 cookie가 있을 때 회전하지 않는 방안, custom header와 Origin/Fetch Metadata를 결합하는 방안을 비교했다. Exact Origin만 요구하면 same-origin safe GET에서 브라우저가 Origin을 생략하는 경우를 지원하지 못한다. 기존 cookie 재사용만으로는 첫 ambient 요청과 organization scope 전환을 막지 못한다. 따라서 custom header로 cross-origin 요청을 preflight에 묶고, exact allowlisted Origin 또는 same-origin Fetch Metadata를 추가 검증하는 방안을 선택했다.

## Decision

Option C를 채택한다.

### Token과 bootstrap

1. `GET /api/v1/auth/csrf`는 `v1.expiry.nonce.mac` 형식의 10분 HMAC token을 응답 body와 host-only HttpOnly `csrf_token` cookie에 함께 발급한다.
2. Token payload에는 auth cookie, 사용자, organization 또는 그 fingerprint 원문을 넣지 않는다. MAC은 domain-separated key, binding 종류, auth cookie 또는 anonymous seed의 HMAC, active `X-Organization-Id` 또는 account sentinel을 포함한다.
3. 인증 cookie가 없으면 host-only HttpOnly random `csrf_anon_seed`에 결박한 pre-auth token을 발급한다. 유효하지 않거나 비활성 계정에 결박된 `auth_token`이 있으면 anonymous로 조용히 전환하지 않고 `401 auth.invalid`로 닫고 invalid auth/CSRF cookie를 삭제한다. Client는 cookie 삭제가 반영된 뒤 bootstrap을 한 번만 다시 시도할 수 있다.
4. Bootstrap은 `X-CSRF-Bootstrap: 1`을 필수로 요구한다. Origin이 있으면 credentialed CORS allowlist와 exact match해야 하고, Origin이 생략된 same-origin GET은 `Sec-Fetch-Site: same-origin`이어야 한다. 존재하는 Fetch Metadata의 cross-site 값은 거부한다. 이 검증은 token service, DB와 Set-Cookie보다 먼저 수행한다.
5. Bootstrap 응답은 `Cache-Control: no-store`, `Pragma: no-cache`를 사용한다. Token과 seed cookie는 `/api/v1`, 600초, HttpOnly, host-only이며 non-local에서는 Secure와 SameSite=None, loopback에서는 SameSite=Lax를 사용한다.
6. Signup, password login, Google OAuth 성공과 logout은 이전 CSRF/anonymous cookie를 삭제한다. Client는 인증 전환과 active organization 변경 시 memory token을 폐기한다.

### 중앙 route policy와 검증 순서

1. 모든 unsafe Gateway route는 `cookie_authenticated`, `pre_auth_session`, `public_anonymous`, `server_credential` 중 정확히 하나로 분류한다. OAuth GET 진입/콜백은 기존 signed one-time state를 사용하는 `oauth_state` 예외로 별도 확인한다.
2. 신규 unsafe route가 미분류되거나 명시 예외가 실제 route와 어긋나면 Gateway startup과 architecture test를 실패시킨다.
3. Cookie/pre-auth mutation은 body parsing, DB, queue, storage와 provider 호출 전에 다음 순서로 검증한다.
   - `CORS_ORIGINS`와 정확히 일치하는 `Origin`
   - 존재하는 경우 `Sec-Fetch-Site`가 `same-origin` 또는 `same-site`
   - JSON 또는 route inventory에 등록한 multipart/bodyless 계약. `BODY_OPTIONAL`은 transfer encoding 없이 `Content-Length: 0`인 요청을 media type과 무관하게 빈 본문으로 허용한다.
   - header/cookie equality와 token signature, binding kind, session/anonymous seed, organization scope, expiry
4. 인증 cookie가 없는 `cookie_authenticated` 요청은 CSRF token으로 사용자 identity를 만들지 않고 side effect 전에 `401 auth.required`로 닫는다.
5. `public_anonymous`와 `server_credential` route는 login cookie가 우연히 포함돼도 CSRF cookie 또는 사용자 principal을 사용하지 않는다. Public Chatbot, Public run과 app-secret webhook/run의 기존 audience를 유지한다.
6. CORS middleware는 CSRF middleware 바깥에서 허용된 Client origin이 안전한 `401/403` body를 읽게 한다. Public Conversation CORS boundary는 그 바깥에서 Public iframe 정책을 계속 소유하고, webhook query redaction은 최외곽 transport sanitizer를 유지한다.

### 오류, 관측과 Client

1. CSRF 실패는 항상 `403 auth.csrf_validation_failed`와 고정 message를 반환한다. 내부에서는 bounded reason, policy, method와 검증된 request ID만 metric/audit에 기록하며 token, cookie, Origin, session, organization과 path parameter 원문을 기록하지 않는다.
2. Client token은 module memory에만 저장하고 localStorage, sessionStorage, URL과 log에 남기지 않는다. Origin마다 현재 organization/account scope token 하나만 유지하고, 실제 mutation origin과 scope가 같은 동시 bootstrap만 하나로 합치며 host-only cookie와 bootstrap endpoint를 mutation origin에 맞춘다. Lifecycle generation 이전에 시작한 bootstrap은 cache를 되살리지 못하고, 같은 origin의 새 bootstrap은 이전 요청이 정리된 뒤 cookie를 갱신한다.
3. 공통 Axios client와 보호된 직접 fetch는 unsafe method에 token을 자동 첨부한다. CSRF 실패 시 PUT/DELETE 또는 idempotency key가 있는 요청만 새 token으로 최대 한 번 재시도한다. 일반 POST/PATCH는 자동 replay하지 않는다.
4. Workflow SSE의 same-origin Next proxy는 API host-only CSRF cookie를 직접 받을 수 없다. 이 단일 proxy는 엄격한 token 문자·길이 검사를 거친 `X-CSRF-Token`을 outbound `csrf_token` cookie로 복제하고, 원래 Origin, Fetch Metadata, organization과 request context를 Gateway에 전달한다. Gateway는 동일한 HMAC/session/scope 검증을 수행한다.

### Enforcement와 배포

1. Development와 production은 별도 opt-in 없이 enforcement가 기본이다. `CSRF_ENFORCEMENT_MODE=disabled`는 `NODE_ENV=test`에서 기존 비-CSRF 단위 테스트를 격리하는 용도로만 허용하며 다른 환경의 disabled/unknown 값은 startup을 실패시킨다.
2. 구 Client와 신 Gateway, 신 Client와 구 Gateway의 혼합 revision은 지원 계약이 아니다. Frontend와 Gateway를 같은 maintenance release로 전환하고 readiness 뒤 트래픽을 열어야 한다. Rollback도 두 component를 같은 계약 revision으로 되돌린다.
3. Production observation/fail-open mode를 두지 않는다. 혼합 revision 무중단 전환이 필요해지면 별도의 versioned protocol ADR과 제거 기한을 먼저 정의한다.

## Rationale

- Signed token을 기존 검증된 `SECRET_KEY`에서 domain separation해 파생하면 secret 원문이나 신규 durable session 저장소 없이 현재 인증 구조에 맞출 수 있다.
- Route audience를 먼저 분류하면 login cookie의 우연한 포함이 Public 또는 server credential route의 principal을 바꾸지 않는다.
- Exact Origin, Fetch Metadata, content type와 token을 독립적으로 검증하면 어느 한 방어 계층의 오구성이 곧바로 mutation 허용으로 이어지지 않는다.
- Custom bootstrap header는 ambient image/navigation GET을 차단하고 cross-origin script 요청을 CORS preflight에 묶는다. Same-origin Fetch Metadata fallback은 safe GET에서 Origin이 생략되는 브라우저 동작을 지원한다.
- Host-only cookie는 origin 간 공유되지 않으므로 cache와 bootstrap도 실제 mutation origin별로 분리해야 header/cookie equality를 보장할 수 있다.
- Non-idempotent 자동 replay를 금지하면 token expiry 복구가 중복 side effect로 바뀌지 않는다.

## Affected Files

- `apps/gateway/application/csrf/*`
- `apps/gateway/adapters/csrf/*`
- `apps/gateway/composition/csrf.py`
- `apps/gateway/middleware/csrf.py`
- `apps/gateway/api/v1/endpoints/auth.py`
- `apps/gateway/main.py`
- `apps/shared/schemas/csrf.py`
- `apps/client/lib/csrfToken.ts`, `apps/client/lib/apiClient.ts`
- 보호 Axios/direct-fetch consumer와 Workflow stream proxy
- Auth·Architecture·Chatbot/Memory 문서 및 관련 테스트

## Consequences

- 첫 unsafe browser mutation 전에 safe CSRF bootstrap 요청이 하나 추가될 수 있다.
- Organization 변경, 인증 전환과 10분 만료 뒤 새 token이 필요하다.
- 다른 API origin으로 mutation을 보내면 같은 organization scope라도 해당 origin에서 별도 bootstrap을 수행한다.
- 잘못된 Origin, content type, stale scope 또는 누락 token은 endpoint와 side effect에 도달하지 않는다.
- 테스트 프로필은 중앙 middleware 자체 테스트와 route inventory test를 제외한 기존 API 테스트에서 enforcement를 비활성화할 수 있다.
- Public와 server credential 호출에는 CSRF header를 추가하지 않으며 해당 route가 cookie principal을 사용하지 않는 별도 계약이 계속 필요하다.

## Follow-up Review

- 새 unsafe route와 multipart route는 route inventory, audience와 protected-resource 완결성 증거를 함께 추가한다.
- 인증형 내부 Chatbot과 durable Conversation Memory route는 동일한 cookie policy를 상속하되 별도 access permission, storage namespace와 retention 계약을 구현한다.
- 실제 배포 절차에서 Frontend/Gateway maintenance cutover와 rollback이 같은 contract revision을 유지하는지 검증한다.
- 지원 브라우저 변경 시 custom header preflight와 `Sec-Fetch-Site` same-origin fallback 호환성을 다시 검토한다.
- 향후 server-side user session을 도입하면 외부 header/error 계약을 유지하면서 token binding validator 교체를 검토한다.
