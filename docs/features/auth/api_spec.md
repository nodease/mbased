# Auth API Spec

Status: Draft

기본 경로: `/api/v1`

## Endpoints

| 메서드 | 경로 | 설명 | 인증 |
| --- | --- | --- | --- |
| GET | `/auth/csrf` | Cookie-authenticated/pre-auth mutation용 10분 signed CSRF token과 host-only HttpOnly cookie를 발급한다. | Safe bootstrap; resource permission 없음 |
| POST | `/auth/signup` | 이메일/비밀번호 사용자를 생성하고, 6시간짜리 JWT 세션을 만들며, `auth_token` 쿠키를 설정한 뒤 사용자/세션 데이터를 반환한다. | Pre-auth CSRF; resource permission 없음 |
| POST | `/auth/login` | 분산 account/network admission 뒤 이메일/비밀번호 사용자를 인증하고, 6시간짜리 JWT 세션을 만들며, `auth_token` 쿠키를 설정한 뒤 사용자/세션 데이터를 반환한다. | Pre-auth CSRF; resource permission 없음 |
| POST | `/auth/logout` | auth/CSRF cookie를 삭제하고 로그아웃 확인 응답을 반환한다. | Pre-auth 또는 auth-bound CSRF; resource permission 없음 |
| GET | `/auth/me` | 쿠키에서 `auth_token`을 읽어 검증하고 현재 사용자/세션 데이터를 반환한다. | `auth_token` 쿠키 필요 |
| GET | `/auth/google/login` | 선택적 safe `next`를 서명 세션에 저장하고 Google 인증 화면으로 리디렉션한다. | 공개 |
| GET | `/auth/google/callback` | Google OAuth를 완료하고, 소셜 사용자를 생성하거나 갱신하며, `auth_token` 쿠키를 설정한 뒤 1회용 safe 복귀 경로로 리디렉션한다. | Google OAuth 콜백 |

## Request And Response Models

### `GET /auth/csrf`

요청 본문: 없음.

필수 browser context와 선택 binding 입력:

| 입력 | 의미 |
| --- | --- |
| `X-CSRF-Bootstrap: 1` | Browser script가 의도적으로 token을 요청했음을 증명한다. 단순 image/navigation GET에는 이 header가 없어 발급 전에 거부된다. |
| `Origin` / `Sec-Fetch-Site` | 교차 출처 요청은 `CORS_ORIGINS` exact Origin을 요구한다. Origin이 생략된 same-origin GET은 `Sec-Fetch-Site: same-origin`이어야 한다. 존재하는 Fetch Metadata의 `cross-site` 값은 거부한다. |
| `auth_token` cookie | 존재하면 유효한 활성 사용자 session인지 검증하고 token을 그 cookie에 결박한다. Invalid 또는 inactive session은 `401 auth.invalid`과 삭제 Set-Cookie를 반환하며 anonymous로 같은 응답에서 전환하지 않는다. |
| `X-Organization-Id` | 존재하면 token MAC의 active organization scope에 포함한다. 없으면 account scope를 사용한다. |
| `csrf_anon_seed` cookie | auth cookie가 없을 때 유효한 random seed를 재사용하며, 없거나 malformed이면 새 seed를 발급한다. |

성공 응답: `200 OK`.

```json
{
  "token": "<signed-csrf-token>",
  "expires_at": "2026-07-29T00:10:00Z"
}
```

응답은 같은 token을 host-only HttpOnly `csrf_token` cookie로 설정한다. Anonymous bootstrap은 host-only HttpOnly `csrf_anon_seed`도 설정한다. `Cache-Control: no-store`, `Pragma: no-cache`가 필수다. Token은 ASCII `v1.expiry.nonce.mac` 형식이며 auth cookie, user와 organization 원문을 포함하지 않는다. 비ASCII token은 equality 비교 전에 `token_invalid`로 닫고, equality를 통과한 비정규 token은 canonical parsing에서 `token_invalid`로 닫는다. Header/Origin/Fetch Metadata 검증 실패는 cookie를 설정하거나 회전시키지 않고 `403 auth.csrf_validation_failed`를 반환한다.

`X-Request-ID`는 canonical RFC 4122 UUID만 보존한다. 다른 값은 서버가 생성한 UUID로 대체하며 입력 원문을 응답이나 CSRF 감사 metadata에 복사하지 않는다.

### `POST /auth/signup`

요청 본문:

| 필드 | 타입 | 필수 | 비고 |
| --- | --- | --- | --- |
| `email` | `EmailStr` | 예 | Pydantic 이메일 검증을 통과해야 한다. |
| `password` | `string` | 예 | salt가 포함된 SHA-256 비밀번호 해시로 저장된다. |
| `name` | `string` | 예 | 사용자 표시 이름이다. |

성공 응답: `200 OK`, `LoginResponse`, `auth_token` 쿠키 설정.

### `POST /auth/login`

요청 본문:

| 필드 | 타입 | 필수 | 비고 |
| --- | --- | --- | --- |
| `email` | `EmailStr` | 예 | Pydantic 이메일 검증을 통과해야 한다. |
| `password` | `string` | 예 | 저장된 비밀번호 해시와 비교된다. |

성공 응답: `200 OK`, `LoginResponse`, `auth_token` 쿠키 설정.

Password 검증 전 admission:

| Dimension | Capacity | Full refill time | 성공 시 reset |
| --- | ---: | ---: | --- |
| account+network | 5 | 300초 | 예 |
| account | 20 | 900초 | 예 |
| network | 100 | 300초 | 아니요 |

Gateway는 raw email과 source address를 Redis에 저장하지 않고 versioned HMAC fingerprint를 사용한다. Source network는 trusted proxy peer에서 온 forwarded chain만 해석한다. 모든 bucket에 token이 있을 때만 password 검증을 수행한다.

제한 응답: `429 Too Many Requests`.

```json
{
  "detail": "로그인 시도가 너무 많습니다. 잠시 후 다시 시도해주세요."
}
```

응답에는 `Retry-After: <1..300>` header가 포함된다. Body와 header는 account 존재 여부, blocked dimension, current count, threshold와 fingerprint를 포함하지 않는다.

Limiter가 admission을 판정할 수 없는 경우: `503 Service Unavailable`.

```json
{
  "detail": "로그인을 일시적으로 사용할 수 없습니다."
}
```

응답에는 `Retry-After: 30` header가 포함되며 password 검증, JWT 발급과 `last_login_at` mutation은 발생하지 않는다.

### `POST /auth/logout`

요청 본문: 엔드포인트 구현상 필수 요청 본문은 없다.

성공 응답: `200 OK`, `auth_token` 쿠키 삭제, 아래 본문 반환.

```json
{
  "message": "Logged out successfully"
}
```

### `GET /auth/me`

요청 본문: 없음.

인증 입력: `auth_token` 쿠키.

성공 응답: `200 OK`, `LoginResponse`.

### `GET /auth/google/login`

요청 본문: 없음.

Query:

| 필드 | 타입 | 필수 | 제약 |
| --- | --- | --- | --- |
| `next` | `string` | 아니요 | 최대 2,048자. Gateway가 상대 same-origin 경로로 다시 검증하며 안전하지 않으면 `/dashboard`를 저장한다. |

성공 응답: Google OAuth 인증 화면으로 이동하는 리디렉션 응답.

Gateway는 검증한 `next`와 발급 시각을 서명 세션에 저장한다. 이 컨텍스트는 10분 안에 한 번만 소비할 수 있고 callback query나 provider payload로 대체할 수 없다.

### `GET /auth/google/callback`

요청 본문: 없음.

OAuth 입력: Google OAuth 콜백 요청과 세션 상태.

성공 응답: `302 Found`, `auth_token` 쿠키 설정, 소비된 safe `next`로 리디렉션. 복귀 컨텍스트가 없거나 만료·재사용·형식 오류이면 `/dashboard`를 사용한다. Gateway 호스트가 정확히 `localhost:8000` 또는 `127.0.0.1:8000`이면 각각 대응하는 client origin의 3000 포트로 이동한다. `AUTH_FRONTEND_ORIGIN`이 설정되어 있으면 Gateway가 검증한 해당 HTTP(S) origin을 사용한다.

### CSRF mutation 요청 계약

`cookie_authenticated`와 `pre_auth_session`으로 분류된 `POST`, `PUT`, `PATCH`, `DELETE`는 다음 값을 함께 보내야 한다.

| 입력 | 계약 |
| --- | --- |
| `Origin` | `CORS_ORIGINS`의 canonical origin 중 하나와 exact match |
| `Sec-Fetch-Site` | Header가 있으면 `same-origin` 또는 `same-site` |
| `X-CSRF-Token` | `/auth/csrf` body에서 받은 token |
| `csrf_token` cookie | Header token과 같은 host-only HttpOnly cookie |
| `X-Organization-Id` | Organization-scoped token을 발급받은 요청은 같은 값 |
| `Content-Type` | 기본 `application/json`(선택적 `charset=utf-8`), inventory에 등록된 multipart 또는 bodyless route만 예외. `BODY_OPTIONAL`은 transfer encoding 없이 `Content-Length: 0`인 경우 Axios의 빈 POST media type도 허용 |

현재 명시 예외는 Public Chatbot/Public run의 `public_anonymous`, app secret run/webhook의 `server_credential`, signed one-time state를 사용하는 OAuth GET route다. Login cookie가 예외 route에 포함돼도 cookie user principal이나 private permission으로 승격하지 않는다.

CSRF 실패는 endpoint body parsing보다 먼저 아래 고정 응답으로 종료된다.

```json
{
  "error": {
    "code": "auth.csrf_validation_failed",
    "message": "CSRF validation failed.",
    "request_id": "..."
  }
}
```
### 공통 응답 모델

`LoginResponse`:

| 필드 | 타입 | 비고 |
| --- | --- | --- |
| `user` | `UserResponse` | 현재 인증된 사용자이다. |
| `session` | `SessionInfo` | JWT 세션 데이터이다. |

`UserResponse`:

| 필드 | 타입 |
| --- | --- |
| `id` | `string` |
| `email` | `string` |
| `name` | `string` |
| `created_at` | `datetime` |

`SessionInfo`:

| 필드 | 타입 | 비고 |
| --- | --- | --- |
| `token` | `string` | JWT 액세스 토큰이다. signup/login/OAuth 콜백에서 같은 값이 `auth_token` 쿠키에 설정된다. |
| `expires_at` | `datetime` | 현재 시각에 6시간을 더해 계산된다. |

`LoginResponse` 예시:

```json
{
  "user": {
    "id": "00000000-0000-0000-0000-000000000000",
    "email": "user@example.com",
    "name": "홍길동",
    "created_at": "2026-07-04T00:00:00Z"
  },
  "session": {
    "token": "<jwt-token>",
    "expires_at": "2026-07-04T06:00:00Z"
  }
}
```

### 쿠키 동작

CSRF cookie 계약:

| cookie | path | max-age | JS 접근 | domain | 환경 속성 |
| --- | --- | ---: | --- | --- | --- |
| `csrf_token` | `/api/v1` | 600초 | HttpOnly | 미설정(host-only) | loopback: SameSite=Lax, non-local: Secure/SameSite=None |
| `csrf_anon_seed` | `/api/v1` | 600초 | HttpOnly | 미설정(host-only) | loopback: SameSite=Lax, non-local: Secure/SameSite=None |

Signup, login, OAuth 성공과 logout은 두 CSRF cookie를 삭제한다. Invalid 또는 inactive auth cookie가 있는 bootstrap은 auth/CSRF cookie를 삭제하고 `401`을 반환한다. Client는 삭제 반영 뒤 anonymous bootstrap을 최대 한 번 재시도한다.
회원가입, 로그인, Google 콜백은 `auth_token`을 `max_age` 6시간의 HTTP-only 쿠키로 설정한다.

이메일/비밀번호 signup 및 login의 경우:

| 환경 | `path` | `secure` | `samesite` | `domain` |
| --- | --- | --- | --- | --- |
| Localhost 또는 `127.0.0.1` 호스트 | `/` | `false` | `lax` | 설정하지 않음 |
| Non-local 호스트 | `/` | `true` | `none` | `COOKIE_DOMAIN`, 또는 마지막 두 호스트 라벨 앞에 `.`를 붙인 값 |

Google 콜백에서 `secure`는 같은 운영 환경 감지 방식을 따르고, `samesite`는 `lax`이다.
Google 콜백은 코드에서 `path`를 명시하지 않는다.

`path="/"`는 브라우저가 같은 도메인의 전체 경로에 `auth_token` 쿠키를 보낼 수 있다는 뜻이다. `POST /auth/logout`은 `path="/"`로 `auth_token` 쿠키를 삭제한다.

## Errors

HTTP 예외는 다음 형식으로 반환된다.

```json
{
  "detail": "..."
}
```

검증 오류는 다음 형식으로 반환된다.

```json
{
  "error": {
    "code": "validation.failed",
    "message": "Request validation failed.",
    "request_id": "...",
    "details": {
      "errors": []
    }
  }
}
```

구현된 auth 오류 사례:

| 상태 | 엔드포인트 | 상세 / 본문 | 조건 |
| --- | --- | --- | --- |
| 401 | `GET /auth/csrf` | `auth.invalid` envelope과 auth/CSRF cookie 삭제 | 존재하는 `auth_token`이 유효하지 않거나 비활성 계정에 결박됐다. Anonymous fallback은 같은 응답에서 수행하지 않는다. |
| 403 | `GET /auth/csrf`와 모든 cookie/pre-auth unsafe route | `auth.csrf_validation_failed` 고정 envelope | Bootstrap proof, Origin, Fetch Metadata, content type, token equality/signature/binding/scope/expiry 중 하나가 실패한다. Bootstrap 실패는 cookie를 설정하지 않는다. |
| 400 | `POST /auth/signup` | `이미 등록된 이메일입니다` | 이메일이 이미 존재한다. |
| 400 | `GET /auth/google/callback` | `OAuth authentication failed` | token 교환, token/user info 타입, user info 조회 또는 email 검증에 실패한다. Provider exception 원문은 반환하지 않는다. |
| 503 | `GET /auth/google/login` | `OAuth login is unavailable` | provider authorization 시작에 실패한다. Exception 원문은 반환하지 않는다. |
| 401 | `POST /auth/login` | `이메일 또는 비밀번호가 올바르지 않습니다` | 사용자가 없거나, 비밀번호가 없거나, 비밀번호 검증에 실패한다. |
| 429 | `POST /auth/login` | `로그인 시도가 너무 많습니다. 잠시 후 다시 시도해주세요.` | Account, source network 또는 account+network admission token이 부족하다. `Retry-After`는 1~300초이며 제한 차원은 노출하지 않는다. |
| 503 | `POST /auth/login` | `로그인을 일시적으로 사용할 수 없습니다.` | Redis limiter 연결, script 또는 result 판정에 실패한다. `Retry-After: 30`, credential verifier 미호출. |
| 500 | `POST /auth/login` | `로그인을 처리할 수 없습니다.` | 예상하지 못한 credential backend 오류를 safe typed error로 변환한다. Raw exception과 account 값은 응답·로그에 포함하지 않는다. |
| 401 | `GET /auth/me` | `로그인이 필요합니다` | `auth_token` 쿠키가 없다. |
| 401 | `GET /auth/me` | `유효하지 않거나 만료된 토큰입니다` | JWT 검증에 실패한다. |
| 401 | `GET /auth/me` | `유저를 찾을 수 없습니다` | 토큰의 user id가 사용자로 해석되지 않는다. |
| 403 | `POST /auth/login`, `GET /auth/me`, `GET /auth/google/callback` | `비활성화된 계정입니다` | 조회된 사용자에 `deactivated_at`이 설정되어 있다. |
| 422 | `POST /auth/signup`, `POST /auth/login` | 검증 오류 envelope | 요청 본문이 Pydantic 검증에 실패한다. |

## Permissions

이 엔드포인트들에는 resource permission 검사가 구현되어 있지 않다.

`GET /auth/me`는 `AuthService.get_user_from_token`을 통해 `auth_token` 쿠키를 검증해서 인증한다.

회원가입, 로그인, 로그아웃, Google OAuth 진입/콜백은 resource permission이 없는 인증 생명주기 엔드포인트이다. 공개라는 의미는 CSRF나 password login admission을 우회한다는 뜻이 아니다. Unsafe signup/login/logout은 pre-auth 또는 auth-bound CSRF 검증을 먼저 통과한다. OAuth GET은 signed one-time state 계약을 사용한다.

회원가입, 로그인, 로그아웃, Google 로그인 성공, 인증 실패는 Gateway에 감사 이벤트를 기록한다. Password login은 성공에 `user.login`, invalid/inactive/limited/limiter-unavailable/internal-error에 `user.login_failed`를 사용하고 safe reason code로 구분한다. Password login이 전용 감사를 기록한 `401/403`은 전역 `auth.permission_denied` 감사를 중복 생성하지 않는다. Login audit의 성공 actor snapshot은 opaque user ID와 표시 이름만 포함하며 raw email, IP/forwarded header, HMAC fingerprint, Redis key와 exception message를 저장하지 않는다.

## Session And Browser Configuration

- `NODE_ENV=production`에서는 `SECRET_KEY`가 없거나 공백이거나 알려진 개발 placeholder이면 Gateway가 시작되지 않는다. 검사와 오류 메시지는 secret 값을 출력하지 않는다.
- Credentialed CORS는 `CORS_ORIGINS`의 명시적인 HTTP(S) origin만 허용한다. Wildcard, 빈 목록, userinfo/path/query/fragment가 있는 origin은 시작 시 거부한다.
- Production password login limiter는 dedicated versioned HMAC keyring과 primary version을 요구한다. Trusted proxy CIDR은 실제 ingress topology에 맞게 명시하며 direct Gateway deployment는 빈 목록으로 forwarded address를 무시한다. Production Helm에서 Ingress가 활성화됐는데 이 값이 비면 chart render를 거부한다.
- Bundled Docker Compose는 local/self-hosted 개발 호환성을 위해 `NODE_ENV=development`를 기본값으로 전달한다. 운영 배포로 사용할 때는 `NODE_ENV=production`과 dedicated keyring을 명시해야 한다.
- Login limiter는 기존 Redis host/port/password와 전용 logical DB를 사용한다. Admission dependency가 실패하면 password login만 `503`으로 닫고 기존 JWT session 검증은 유지한다.
- Non-secret 설정은 `AUTH_LOGIN_LIMITER_REDIS_DB`(기본 `2`), `AUTH_LOGIN_LIMITER_POLICY_VERSION`, `AUTH_LOGIN_FINGERPRINT_PRIMARY_VERSION`, `AUTH_LOGIN_TRUSTED_PROXY_CIDRS`를 사용한다. Production HMAC keyring JSON은 `AUTH_LOGIN_FINGERPRINT_KEYS` Secret으로 주입하고 실제 값은 manifest, log와 진단 응답에 출력하지 않는다.
- 초기 capacity/full-refill 값은 ADR의 versioned policy로 고정한다. 값을 변경하면 policy version, requirements와 Redis integration test를 함께 갱신한다.
- CORS allowlist와 CSRF exact-Origin 검사는 같은 canonical origin parser를 사용하지만 독립적으로 판정한다. CORS middleware는 CSRF middleware 바깥에서 허용된 Client가 고정 `401/403` 응답을 읽게 한다.
- CSRF enforcement는 development/production에서 기본 활성화된다. `CSRF_ENFORCEMENT_MODE=disabled`는 `NODE_ENV=test`에서만 허용하며 다른 환경의 disabled/unknown 값은 startup 오류다.
- 신·구 Frontend/Gateway 혼합 revision은 지원하지 않으며 같은 maintenance release로 전환·rollback한다.

## Target Runtime Principal Boundary

- `auth_token` cookie/JWT가 검증한 current user만 authenticated identity를 제공한다. Organization membership, resource permission, LLM credential과 billing scope는 각 소유 도메인이 별도로 평가한다.
- `Authorization: Conversation <token>`과 `Authorization: Purge <receipt>`는 public Conversation Memory capability이며 `get_current_user`, `/auth/me`와 authenticated route의 user identity로 수용하지 않는다.
- Public Chatbot route는 login cookie가 함께 있어도 anonymous public audience를 유지한다. Cookie-authenticated Gateway mutation은 중앙 CSRF token/exact-Origin 경계를 사용한다. Authenticated internal Chatbot은 이 공통 경계를 상속해야 하지만 별도 access grant와 durable Conversation namespace는 후속 범위다.
- Public create/close/reset/delete request와 capability lifecycle AuditLog는 `actor_id=null`, `actor_type='public'`을 사용한다. 비동기 purge completion은 `system` actor를 사용한다. App/deployment owner, credential/billing principal과 capability reference를 user actor로 합성하지 않는다.

이 section은 ADR-0030 target integration contract이며 현재 auth endpoint 구현이 Conversation Memory capability를 이미 제공한다는 뜻이 아니다.
