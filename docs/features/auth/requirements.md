# Auth Requirements

Status: Draft
Related Features: organization, audit-tracing, workflow, deployment, chatbot-deployment, conversation-memory

## Purpose

Auth 기능은 사용자의 인증 생명주기를 담당한다. 현재 구현 범위는 이메일/비밀번호 회원가입, 분산 abuse prevention이 적용된 이메일/비밀번호 로그인, Google OAuth 로그인, JWT 세션 쿠키 발급/검증/삭제, 현재 사용자 조회, 클라이언트 인증 리다이렉트 처리와 cookie-authenticated unsafe API의 중앙 CSRF 방어이다.

Auth는 보호된 Gateway API가 `auth_token` 쿠키에서 현재 사용자를 식별할 수 있는 공통 인증 경계를 제공한다. 신규 사용자 생성 시 기본 organization 컨텍스트를 준비하고, 주요 인증 성공/실패 이벤트를 audit로 기록한다. Resource permission 판정, organization 관리, audit 조회/정책 처리는 auth 자체의 책임 범위가 아니다.

## User Stories

- 방문자는 이름, 이메일, 비밀번호로 계정을 생성하고 즉시 로그인된 상태로 대시보드에 진입할 수 있다.
- 방문자는 이메일과 비밀번호로 로그인하고, 안전한 원래 보호 경로가 있으면 그 경로로, 없으면 대시보드로 진입할 수 있다.
- 방문자는 반복 로그인 제한에 도달하면 계정 존재 여부나 제한 원인을 노출하지 않는 안내와 bounded 재시도 시간을 받으며, 영구 계정 잠금 없이 token refill 뒤 다시 시도할 수 있다.
- 방문자는 Google OAuth 로그인을 시작하고, 안전한 원래 보호 경로가 있으면 성공한 콜백 이후 그 경로로, 없으면 대시보드로 진입할 수 있다.
- 이미 로그인된 사용자는 공개 홈 진입 시 대시보드로 자동 이동된다.
- 로그인되지 않은 사용자는 공개 홈과 auth 화면에서 강제 로그인 리다이렉트 없이 오류나 공개 화면을 볼 수 있다.
- 인증된 클라이언트 화면은 현재 사용자 이름과 이메일을 조회할 수 있다.
- 사용자는 로그아웃을 요청해 서버의 인증 쿠키를 삭제하고 로그인 화면으로 이동할 수 있다.
- 보호된 Gateway API는 요청의 `auth_token` 쿠키를 검증해 현재 사용자를 얻을 수 있다.

## Functional Requirements

- AUTH-REQ-001: 시스템은 `POST /auth/signup`으로 `email`, `password`, `name`을 받아 신규 이메일/비밀번호 사용자를 생성해야 한다.
- AUTH-REQ-002: 회원가입 요청의 이메일은 Pydantic `EmailStr` 검증을 통과해야 한다.
- AUTH-REQ-003: 이미 존재하는 이메일로 회원가입하면 시스템은 `400`과 `이미 등록된 이메일입니다`를 반환해야 한다.
- AUTH-REQ-004: 회원가입 성공 시 시스템은 salt가 포함된 SHA-256 비밀번호 해시를 저장하고, `social_provider`를 `none`으로 설정해야 한다.
- AUTH-REQ-005: 회원가입 성공 시 시스템은 신규 사용자의 기본 organization 컨텍스트를 생성해야 한다.
- AUTH-REQ-006: 회원가입 성공 시 시스템은 `last_login_at`을 현재 시각으로 설정하고 6시간 만료 JWT 세션을 생성해야 한다.
- AUTH-REQ-007: 시스템은 `POST /auth/login`으로 `email`, `password`를 받아 기존 이메일/비밀번호 사용자를 인증해야 한다.
- AUTH-REQ-008: 로그인 시 사용자가 없거나, 비밀번호가 없거나, 비밀번호 검증에 실패하면 시스템은 `401`과 `이메일 또는 비밀번호가 올바르지 않습니다`를 반환해야 한다.
- AUTH-REQ-009: 로그인 시 비밀번호 검증을 통과한 뒤 계정이 비활성화되어 있으면 시스템은 `403`과 `비활성화된 계정입니다`를 반환해야 한다.
- AUTH-REQ-010: 로그인 성공 시 시스템은 `last_login_at`을 갱신하고 6시간 만료 JWT 세션을 생성해야 한다.
- AUTH-REQ-011: 회원가입, 로그인, Google OAuth 콜백 성공 시 시스템은 `auth_token` HTTP-only 쿠키를 설정해야 한다.
- AUTH-REQ-012: 이메일/비밀번호 회원가입과 로그인의 `auth_token` 쿠키는 `max_age` 21600초와 `path="/"`를 사용해야 한다.
- AUTH-REQ-013: localhost 또는 `127.0.0.1` 요청에서는 `auth_token` 쿠키를 `secure=false`, `samesite=lax`, domain 미설정으로 발급해야 한다.
- AUTH-REQ-014: non-local 요청에서는 `auth_token` 쿠키를 `secure=true`, `samesite=none`으로 발급하고, `COOKIE_DOMAIN` 또는 요청 host에서 계산한 cookie domain을 사용할 수 있어야 한다.
- AUTH-REQ-015: `GET /auth/me`는 요청 쿠키의 `auth_token`을 검증해 현재 사용자와 세션 정보를 반환해야 한다.
- AUTH-REQ-016: `GET /auth/me`는 `auth_token`이 없으면 `401`과 `로그인이 필요합니다`를 반환해야 한다.
- AUTH-REQ-017: `GET /auth/me`는 JWT가 유효하지 않거나 만료되면 `401`과 `유효하지 않거나 만료된 토큰입니다`를 반환해야 한다.
- AUTH-REQ-018: `GET /auth/me`는 토큰의 사용자 ID로 사용자를 찾을 수 없으면 `401`과 `유저를 찾을 수 없습니다`를 반환해야 한다.
- AUTH-REQ-019: 토큰 검증으로 찾은 사용자가 비활성화되어 있으면 시스템은 `403`과 `비활성화된 계정입니다`를 반환해야 한다.
- AUTH-REQ-020: `POST /auth/logout`은 `auth_token` 쿠키를 삭제하고 로그아웃 확인 응답을 반환해야 한다.
- AUTH-REQ-021: `GET /auth/google/login`은 요청 host를 기반으로 Google OAuth callback URL을 만들고 Google 인증 화면으로 리다이렉트해야 한다.
- AUTH-REQ-022: non-local Google OAuth redirect URI는 `https` 스킴을 사용해야 한다.
- AUTH-REQ-023: Google OAuth callback은 Google 사용자 정보에서 email을 요구해야 하며, token/user info 응답이 없거나 잘못된 타입이거나 email이 없으면 raw provider 값 없이 `400`과 고정 본문 `OAuth authentication failed`를 반환해야 한다.
- AUTH-REQ-024: Google OAuth callback은 email 기준으로 기존 사용자를 찾거나 신규 소셜 사용자를 생성해야 한다.
- AUTH-REQ-025: 기존 소셜 사용자는 provider, social id, avatar URL이 바뀌면 갱신되어야 한다.
- AUTH-REQ-026: Google OAuth 신규 사용자 생성 시 시스템은 기본 organization 컨텍스트를 생성해야 한다.
- AUTH-REQ-027: Google OAuth 성공 시 시스템은 `last_login_at`을 갱신하고 6시간 만료 JWT 세션 쿠키를 설정한 뒤, 서명 세션에서 한 번 소비한 safe same-origin `next`로 리다이렉트해야 한다. 유효한 복귀 경로가 없으면 `/dashboard`를 사용한다.
- AUTH-REQ-028: Gateway host가 정확히 `localhost:8000` 또는 `127.0.0.1:8000`이면 Google OAuth 성공 리다이렉트 대상은 각각 대응하는 `http://<loopback>:3000<safe-next>`여야 한다. 유사 문자열을 포함한 non-local host는 local로 취급하지 않는다.
- AUTH-REQ-029: 회원가입 성공, 로그인 성공, 회원가입 실패, 로그인 실패, 로그아웃은 인증 행위 감사 이벤트로 기록되어야 한다.
- AUTH-REQ-030: 공통 인증 dependency 또는 권한 경계에서 발생하고 다른 helper가 아직 감사하지 않은 401/403 응답은 `auth.permission_denied` 감사 이벤트로 기록되어야 한다. Password login이 `user.login_failed`를 직접 기록한 응답은 중복 전역 감사를 만들지 않아야 한다.
- AUTH-REQ-031: Gateway 공통 인증 의존성은 `auth_token` 쿠키를 읽고 `AuthService.get_user_from_token`으로 현재 사용자를 반환해야 한다.
- AUTH-REQ-032: 클라이언트 `authApi`는 signup, login, logout, me, googleLogin 호출을 제공해야 한다.
- AUTH-REQ-033: 클라이언트 auth API 호출은 credential 포함 요청을 사용해야 한다.
- AUTH-REQ-034: 로그인 화면은 이메일/비밀번호 로그인을 제출하고 성공 시 safe same-origin `next` query가 있으면 그 경로로, 없거나 안전하지 않으면 `/dashboard`로 이동해야 한다.
- AUTH-REQ-035: 로그인 화면은 401, 422, 5xx, 네트워크 실패, 기타 실패를 사용자 메시지와 toast로 표시해야 한다.
- AUTH-REQ-036: 로그인 화면은 Google 로그인 버튼 클릭 시 검증된 `next`를 query로 포함한 Gateway의 `/auth/google/login`으로 브라우저를 이동시켜야 한다.
- AUTH-REQ-037: 회원가입 화면은 이름, 이메일, 비밀번호, 비밀번호 확인을 제출하고 성공 시 성공 toast를 표시한 뒤 `/dashboard`로 이동해야 한다.
- AUTH-REQ-038: 회원가입 화면은 비밀번호와 비밀번호 확인이 다르면 API 호출 전에 `비밀번호가 일치하지 않습니다.` 오류를 표시해야 한다.
- AUTH-REQ-039: 회원가입 화면은 백엔드 오류, 5xx 오류, 네트워크 실패, 기타 실패를 사용자 메시지와 toast로 표시해야 한다.
- AUTH-REQ-040: 홈 화면은 마운트 시 `authApi.me()`를 호출하고, 성공하면 `/dashboard`로 `router.replace`해야 한다.
- AUTH-REQ-041: 홈 화면의 인증 확인이 실패하면 공개 랜딩 화면을 렌더링할 수 있도록 로딩 상태를 해제해야 한다.
- AUTH-REQ-042: 클라이언트 API 인터셉터는 `/auth/*`와 `/`가 아닌 경로에서 401 응답을 받으면 현재 `pathname + search + hash`를 URL-encode한 `/auth/login?next=...`로 이동시켜야 한다. 같은 경로를 대상으로 동시에 발생한 interceptor/page redirect는 짧은 deduplication window에서 한 번만 navigation을 소유해야 한다.
- AUTH-REQ-043: 클라이언트 공통 API 인터셉터는 `/auth/*`와 `/`에서는 401 응답을 자동 리다이렉트하지 않아야 한다.
- AUTH-REQ-044: `next`는 2,048자 이하이고 `/`로 시작하며 URL 파싱·반복 decode·정규화 전후에 클라이언트와 같은 origin을 유지하는 경로만 허용해야 한다. 절대 URL, `//host`, backslash, dot segment, control character, 잘못된 percent encoding, 제한 횟수 안에 안정화되지 않는 중첩 encoding은 `/dashboard`로 fallback해 open redirect와 header injection을 막아야 한다.
- AUTH-REQ-045 (Target Runtime Contract): Auth가 검증한 current user identity만 user형 authenticated execution subject 후보가 될 수 있다. 향후 service account는 별도 Auth/RBAC lifecycle과 승인된 principal type이 필요하다. Resource/organization adapter가 current membership과 permission을 별도로 평가해야 하며 credential/billing principal을 user 또는 service-account identity로 해석해서는 안 된다.
- AUTH-REQ-046 (Target Runtime Contract): Conversation Access Grant와 Purge Receipt는 사용자 authentication이 아닌 scoped capability다. Gateway 공통 `get_current_user` 또는 authenticated endpoint가 이를 JWT/session identity로 받아들여서는 안 된다.
- AUTH-REQ-047 (Target Runtime Contract): Public Chatbot route는 valid login cookie가 함께 있어도 명시적으로 authenticated internal surface로 전환되지 않는 한 anonymous public audience를 유지해야 한다. Optional authentication으로 private Knowledge/Memory 권한을 높여서는 안 된다.
- AUTH-REQ-048 (Target Runtime Contract): Authenticated request audit actor는 실제 current user에서 파생하고 public capability request lifecycle actor는 `actor_id=null`, `actor_type='public'`으로 표현해야 한다. 비동기 purge completion 같은 system operation은 별도 `system` actor를 사용한다. App/deployment owner, credential/billing principal 또는 Access Grant reference를 user actor로 합성해서는 안 된다.
- AUTH-REQ-049: Google OAuth `next`는 client가 callback에 다시 제출하는 권한 값이 아니다. Gateway는 검증한 경로와 발급 시각을 서명된 server session에 저장하고 10분 이내 callback에서 한 번만 소비해야 하며, 만료·미래 시각·재사용·형식 오류는 `/dashboard`로 닫아야 한다.
- AUTH-REQ-050: Google OAuth 시작·token 교환·user info 실패 응답, 로그와 audit metadata에는 provider exception 원문, token, credential 또는 raw payload를 포함하지 않아야 한다. 로그에는 오류 타입, audit에는 고정 reason code만 기록한다.
- AUTH-REQ-051: `NODE_ENV=production`에서는 OAuth session 서명용 `SECRET_KEY`가 없거나 공백이거나 알려진 개발 placeholder이면 Gateway 시작을 거부해야 한다. Credentialed `CORS_ORIGINS`는 명시적인 HTTP(S) origin 목록이어야 하며 `*`, 빈 목록, userinfo/path/query/fragment가 있는 값을 거부해야 한다.
- AUTH-REQ-052: `POST /auth/login`은 account, source network, account+network 세 차원의 분산 admission을 실제 사용자 조회와 password 검증 전에 수행해야 한다.
- AUTH-REQ-053: Account limiter identity는 `EmailStr` 검증 뒤 Unicode NFKC, trim, casefold를 적용한 값에서 파생해야 한다. Raw email/username은 Redis key/value, audit, metric과 log에 저장하지 않아야 한다.
- AUTH-REQ-054: Source network는 immediate peer가 configured trusted proxy CIDR일 때만 forwarded chain의 첫 untrusted hop에서 파생해야 한다. Untrusted peer가 보낸 `X-Forwarded-For`와 `X-Real-IP`는 무시해야 한다.
- AUTH-REQ-055: Source network는 IPv4 `/24`, IPv6 `/64`로 정규화해야 하며 direct loopback development request는 exact loopback identity를 유지해야 한다.
- AUTH-REQ-056: Limiter counter key는 domain-separated versioned HMAC-SHA-256 account/network/account+network fingerprint만 포함해야 한다. HMAC key와 raw identifier는 Redis value, response, audit, metric과 log에 포함하지 않아야 한다.
- AUTH-REQ-057: Production은 dedicated primary HMAC key version을 요구하고 최대 한 개 previous version을 rotation overlap으로 허용해야 한다. Overlap 동안 active version 전체의 bucket을 같은 admission에서 평가·소비해야 한다.
- AUTH-REQ-058: Login admission은 Redis server time을 사용하는 atomic token-bucket으로 구현해야 한다. Account+network는 5 token/300초, account는 20 token/900초, network는 100 token/300초의 초기 정책을 사용해야 한다.
- AUTH-REQ-059: 모든 active-version bucket과 세 dimension에 token이 있을 때만 한 login request를 허용하고 모두에서 token 하나를 소비해야 한다. 하나라도 비면 다른 bucket을 추가 소비하지 않고 password 검증, JWT 발급과 DB mutation 전에 차단해야 한다.
- AUTH-REQ-060: Credential 성공은 account와 account+network bucket만 active version 전체에서 초기화해야 한다. Network bucket과 실패한 login의 token은 유지해야 한다.
- AUTH-REQ-061: 제한된 login은 `429`와 고정된 generic detail을 반환하고, 모든 blocked bucket이 최소 한 token을 회복하는 bounded 시간을 `Retry-After` 1~300초로 반환해야 한다. Body는 account 존재 여부, blocked dimension, count, threshold와 fingerprint를 노출하지 않아야 한다.
- AUTH-REQ-062: Limiter 저장소 또는 script가 admission을 판정할 수 없으면 login은 password 검증 전에 fail-closed `503`과 generic detail, `Retry-After: 30`을 반환해야 한다. Process-local 또는 fail-open fallback은 사용하지 않아야 한다.
- AUTH-REQ-063: Login abuse prevention은 server-side progressive sleep이나 영구 account lock을 사용하지 않아야 한다. 지속 공격은 token refill rate로 제한하고 정상 사용자는 bounded refill 뒤 다시 시도할 수 있어야 한다.
- AUTH-REQ-064: Login 성공은 `user.login`, invalid/inactive/limited/limiter-unavailable 결과는 `user.login_failed`를 사용하고 safe reason code로 구분해야 한다. Login audit는 request ID, policy version, allowlisted limited dimension과 email을 제외한 opaque user ID/표시 이름 success actor snapshot만 허용하며 raw email/IP/fingerprint와 exception message를 제외해야 한다.
- AUTH-REQ-065: Login metric과 structured log는 outcome, allowlisted dimension, policy version과 operation 같은 bounded label만 사용해야 한다. Account, network/IP, fingerprint, user ID와 request ID를 metric label로 사용하지 않아야 한다.
- AUTH-REQ-066: Request schema `422`는 login admission을 소비하지 않아야 한다. Existing invalid credential `401`, valid credential의 inactive `403`, success response와 cookie 계약은 유지해야 한다.
- AUTH-REQ-067: 예상하지 못한 password credential backend 오류는 raw exception과 account 값을 응답·로그에 노출하지 않고 고정 `500` 응답과 `auth.login.internal_error` 감사 reason으로 변환해야 한다.
- AUTH-REQ-068: Production Helm 배포에서 Ingress가 활성화되면 실제 peer topology에 맞는 trusted proxy CIDR이 필수여야 한다. Direct Gateway 배포는 빈 목록으로 forwarded address를 무시할 수 있으며, 광역 CIDR을 추측해 기본값으로 제공하지 않아야 한다.
- AUTH-REQ-069: Bundled Docker Compose는 development mode를 명시적으로 기본 적용해야 하며, 운영 사용 시 `NODE_ENV=production`과 dedicated login fingerprint keyring을 설정해 production startup 검증을 활성화해야 한다.

- AUTH-REQ-070: 시스템은 `GET /auth/csrf`에서 10분 만료 signed double-submit token을 응답 body와 host-only HttpOnly `csrf_token` cookie로 발급해야 한다.
- AUTH-REQ-071: CSRF token은 auth cookie 또는 random anonymous seed, binding 종류와 normalized active organization/account scope에 domain-separated HMAC으로 결박해야 하며 이 값들의 원문을 token payload에 포함하지 않아야 한다.
- AUTH-REQ-072: 인증 cookie가 없는 bootstrap은 host-only HttpOnly `csrf_anon_seed`와 pre-auth token을 발급해야 한다. 유효하지 않은 auth cookie는 anonymous로 조용히 전환하지 않고 `401 auth.invalid`로 닫고 invalid auth/CSRF cookie를 삭제해야 한다.
- AUTH-REQ-073: `csrf_token`과 `csrf_anon_seed`는 `path=/api/v1`, 600초, HttpOnly, host-only여야 한다. Non-local에서는 Secure/SameSite=None, loopback에서는 SameSite=Lax를 사용해야 한다.
- AUTH-REQ-074: Signup, password login, Google OAuth 성공과 logout은 stale CSRF/anonymous cookie를 삭제해야 한다.
- AUTH-REQ-075: 모든 unsafe Gateway route는 `cookie_authenticated`, `pre_auth_session`, `public_anonymous`, `server_credential` 중 정확히 하나로 분류되어야 하며 미분류, 중복 또는 존재하지 않는 명시 예외는 startup과 architecture test를 실패시켜야 한다.
- AUTH-REQ-076: Cookie/pre-auth mutation은 body parsing과 side effect 전에 configured exact Origin을 요구해야 한다. `Sec-Fetch-Site`가 있으면 `same-origin` 또는 `same-site`만 허용해야 한다.
- AUTH-REQ-077: Cookie/pre-auth mutation은 기본적으로 canonical JSON만 허용하고 route inventory에 등록된 multipart와 bodyless 요청만 예외로 허용해야 한다.
- AUTH-REQ-078: Cookie/pre-auth mutation은 `X-CSRF-Token`과 CSRF cookie의 equality, signature, version, expiry, binding kind, auth/anonymous binding과 organization/account scope를 검증해야 한다.
- AUTH-REQ-079: CSRF 검증은 controller, credential verifier, DB, queue, storage, retrieval과 provider 호출보다 먼저 수행되어야 한다. 인증 cookie가 없는 cookie-authenticated mutation은 side effect 전에 `401 auth.required`로 닫아야 한다.
- AUTH-REQ-080: Public anonymous와 server credential route는 login cookie 존재 여부로 audience나 principal을 바꾸지 않고 CSRF token을 private 권한 근거로 사용하지 않아야 한다.
- AUTH-REQ-081: 모든 CSRF 거부는 `403 auth.csrf_validation_failed`와 고정 message를 반환하고, 내부 audit/metric에는 bounded reason, policy, method와 safe request ID만 기록해야 한다. Token, cookie, Origin, session, organization과 path parameter 원문은 기록하지 않아야 한다.
- AUTH-REQ-082: Client는 CSRF token을 module memory에만 보관하고 같은 scope bootstrap을 single-flight해야 한다. LocalStorage, sessionStorage, URL과 log에 token을 남기지 않아야 한다.
- AUTH-REQ-083: Client는 login/signup/logout/OAuth와 active organization 변경 때 cached token을 폐기해야 한다. Invalid auth cookie를 삭제한 bootstrap `401`은 한 번만 재시도할 수 있다.
- AUTH-REQ-084: Client는 CSRF 실패 뒤 PUT/DELETE 또는 idempotency key가 있는 요청만 token refresh 후 최대 한 번 재시도하고, 일반 POST/PATCH를 자동 replay하지 않아야 한다.
- AUTH-REQ-085: OAuth GET navigation/callback은 custom CSRF header 대신 기존 signed, expiring, one-time state를 유지해야 한다.
- AUTH-REQ-086: `CORS_ORIGINS`는 CSRF exact-Origin allowlist에 재사용하되 CORS 허용을 CSRF 성공으로 간주하지 않아야 한다. CORS middleware는 허용된 Client가 CSRF `401/403`을 읽을 수 있도록 CSRF middleware 바깥에 있어야 한다.
- AUTH-REQ-087: Workflow stream proxy는 original Origin, Fetch Metadata, CSRF token과 organization context를 전달하고, 엄격히 검증한 header token만 outbound host-only CSRF cookie로 복제해야 한다. 다른 proxy/public adapter는 이 예외를 일반화하지 않아야 한다.
- AUTH-REQ-088: CSRF enforcement는 development와 production에서 기본 활성화되어야 한다. Disabled mode는 `NODE_ENV=test`에서만 허용하고 production disabled/unknown mode는 startup을 실패시켜야 한다.
## Policies And Edge Cases

- CORS, SameSite와 CSRF token은 서로 대체하지 않는 독립 방어 계층이다.
- Public iframe parent allowlist와 CSRF Origin allowlist는 목적이 다르며 서로 재사용하지 않는다.
- 신·구 Client/Gateway 혼합 revision은 지원하지 않는다. Frontend와 Gateway는 같은 maintenance release로 전환하고 함께 rollback한다.
- Production observation/fail-open mode는 제공하지 않는다.
- Auth 엔드포인트 자체에는 resource permission 검사가 구현되어 있지 않다.
- 사용자 테이블의 email은 unique이며, social id도 값이 있으면 unique이다.
- 비밀번호는 서버에서 salt가 포함된 SHA-256 해시로 저장된다.
- 현재 구현은 비밀번호 길이, 복잡도, 재사용 제한을 강제하지 않는다.
- 현재 구현은 이메일 인증을 요구하지 않는다.
- 현재 구현은 비밀번호 재설정 API나 화면을 제공하지 않는다.
- 이메일/비밀번호 로그인은 account, source network, account+network 기준의 bounded token-bucket을 적용한다. Signup과 Google OAuth에는 이 limiter를 적용하지 않는다.
- 로그인 limiter는 Redis 가용성에 의존한다. Admission 장애는 신규 password login을 `503`으로 닫지만 기존 session 검증에는 영향을 주지 않는다.
- `LoginResponse`는 HTTP-only 쿠키와 별도로 JWT token 값을 응답 본문에도 포함한다.
- Audit metadata에는 actor snapshot, 요청 metadata, 실패 email/error type 또는 OAuth reason code가 포함될 수 있지만, exception 원문과 세션 token 원문은 기록하지 않는다.
- 로그아웃 엔드포인트는 현재 사용자 식별을 요구하지 않으며, cookie 삭제 시점에 actor id 없이 audit을 기록한다.
- Conversation capability authorization header는 `auth_token` cookie/JWT와 다른 scheme·dependency에서 처리한다. Scheme 혼동은 authenticated fallback 없이 fail-closed한다.
- 실제 로그아웃 사용자 경로는 서버 로그아웃 후 로그인 화면으로 이동해야 한다.
- Frontend auth 타입에는 `emailVerified`, `role`, `isActive`, email verification, password reset 관련 타입이 있으나 현재 Gateway auth 응답과 구현된 화면/API는 그 전체 필드를 제공하지 않는다.
- Google OAuth 설정은 `GOOGLE_CLIENT_ID`와 `GOOGLE_CLIENT_SECRET` 환경 변수에 의존한다.
- `next` 복귀는 이메일/비밀번호 로그인과 Google OAuth에 모두 적용한다. Google OAuth는 client query를 callback 권한으로 신뢰하지 않고 서명 세션의 10분·1회용 복귀 컨텍스트를 사용한다.
- JWT와 OAuth session 서명은 현재 같은 `SECRET_KEY` 환경 변수에 의존한다. Production은 누락·공백·개발 placeholder를 시작 시 거부하며, secret 원문은 진단에 출력하지 않는다.

## Open Questions

- 이메일 인증과 비밀번호 재설정을 auth 범위에 포함할지 결정해야 한다.
- 비밀번호 길이·복잡도·재사용과 password hashing algorithm을 별도 Auth foundation 범위에서 고도화할지 결정해야 한다.
- HTTP-only 쿠키를 설정하면서 JWT token을 응답 본문에도 계속 반환할지 결정해야 한다.
- Frontend auth 타입을 현재 Gateway 응답 모델에 맞게 축소할지, 아니면 Gateway 응답을 타입에 맞춰 확장할지 결정해야 한다.
- Google OAuth 실패 시 단순 `400` 본문을 반환할지, 로그인 화면으로 오류 상태를 전달할지 결정해야 한다.
