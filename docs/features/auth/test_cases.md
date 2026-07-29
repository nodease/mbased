# Auth Test Cases

Status: Draft

## Minimum Failure Rule

이 문서는 정상 시나리오를 길게 반복하지 않고, 각 auth 조건을 깨뜨리는 최소 입력, 상태, 또는 관찰값을 기준으로 테스트 케이스를 정의한다.

각 테스트는 해당 최소 조건 하나만으로 실패를 유도하거나, 성공 경로의 필수 관찰값 하나가 빠졌을 때 실패로 판단할 수 있어야 한다.

## Unit Tests

| ID | 검증 조건 | 최소 실패 조건 | 기대 결과 |
| --- | --- | --- | --- |
| AUTH-TC-U001 | 비밀번호 해시는 원문과 구분되어야 한다. | 저장된 password 값이 원문과 같거나 `salt$hash` 형식이 아니다. | 테스트 실패. |
| AUTH-TC-U002 | 같은 비밀번호는 저장된 해시와 일치해야 한다. | 원문 비밀번호 한 글자만 다른 값을 입력한다. | `verify_password`가 false를 반환한다. |
| AUTH-TC-U003 | 잘못된 해시 형식은 인증되지 않아야 한다. | 저장된 해시 문자열에 `$` 구분자가 없다. | `verify_password`가 false를 반환한다. |
| AUTH-TC-U004 | 회원가입은 중복 이메일을 거부해야 한다. | DB 조회 결과에 같은 email 사용자가 이미 있다. | `400`, `이미 등록된 이메일입니다`. |
| AUTH-TC-U005 | 회원가입 성공은 이메일/비밀번호 사용자를 만들어야 한다. | 신규 사용자 저장값의 `social_provider`가 `none`이 아니거나 password가 해시 형식이 아니다. | 테스트 실패. |
| AUTH-TC-U006 | 회원가입 성공은 초기 세션 상태를 만들어야 한다. | `last_login_at`이 비어 있거나 JWT payload에 `user_id` 또는 `exp`가 없다. | 테스트 실패. |
| AUTH-TC-U007 | 회원가입 성공은 기본 organization 컨텍스트를 생성해야 한다. | 신규 사용자는 저장되지만 기본 organization 컨텍스트가 준비되지 않는다. | 테스트 실패. |
| AUTH-TC-U008 | 로그인은 없는 사용자를 거부해야 한다. | DB 조회 결과가 없다. | `401`, `이메일 또는 비밀번호가 올바르지 않습니다`. |
| AUTH-TC-U009 | 로그인은 비밀번호 없는 사용자를 거부해야 한다. | 사용자 row는 있으나 password 값이 비어 있다. | `401`, `이메일 또는 비밀번호가 올바르지 않습니다`. |
| AUTH-TC-U010 | 로그인은 잘못된 비밀번호를 거부해야 한다. | 저장된 해시와 입력 비밀번호가 일치하지 않는다. | `401`, `이메일 또는 비밀번호가 올바르지 않습니다`. |
| AUTH-TC-U011 | 로그인은 비활성 사용자를 거부해야 한다. | 올바른 비밀번호를 입력했지만 `deactivated_at`이 설정되어 있다. | `403`, `비활성화된 계정입니다`, `last_login_at` 미갱신. |
| AUTH-TC-U012 | 로그인은 비밀번호 검증 전 비활성 상태를 노출하지 않아야 한다. | 비활성 사용자에게 틀린 비밀번호를 입력한다. | `401`, `last_login_at` 미갱신. |
| AUTH-TC-U013 | 토큰 인증은 토큰 없음을 거부해야 한다. | `auth_token`이 `None` 또는 빈 값이다. | `401`, `로그인이 필요합니다`. |
| AUTH-TC-U014 | 토큰 인증은 유효하지 않은 JWT를 거부해야 한다. | JWT decode가 실패하거나 `user_id`를 반환하지 않는다. | `401`, `유효하지 않거나 만료된 토큰입니다`. |
| AUTH-TC-U015 | 토큰 인증은 삭제된 사용자를 거부해야 한다. | JWT의 `user_id`로 DB 사용자를 찾을 수 없다. | `401`, `유저를 찾을 수 없습니다`. |
| AUTH-TC-U016 | 토큰 인증은 비활성 사용자를 거부해야 한다. | JWT는 유효하지만 조회된 사용자에 `deactivated_at`이 설정되어 있다. | `403`, `비활성화된 계정입니다`. |
| AUTH-TC-U017 | 기존 소셜 사용자는 provider, social id, avatar 변경을 반영해야 한다. | 같은 email 사용자의 소셜 필드가 입력값과 다르다. | 변경된 필드가 저장된다. |
| AUTH-TC-U018 | 신규 소셜 사용자는 기본 organization 컨텍스트를 생성해야 한다. | 같은 email 사용자가 없고 Google user info가 유효하지만 기본 organization 컨텍스트가 준비되지 않는다. | 테스트 실패. |
| AUTH-TC-U019 | 비활성 소셜 사용자는 재로그인할 수 없어야 한다. | 같은 email 사용자에 `deactivated_at`이 설정되어 있다. | `403`, `비활성화된 계정입니다`. |
| AUTH-TC-U020 | Auth return validator는 중첩 encoding과 URL 정규화 우회를 막아야 한다. | 절대 URL, protocol-relative URL, backslash, dot segment, control character, malformed/과다 중첩 encoding 중 하나를 입력한다. | `/dashboard` fallback. |
| AUTH-TC-U021 | OAuth return context는 짧은 수명과 1회 소비를 강제해야 한다. | 같은 session context를 두 번 소비하거나 발급 10분 후 또는 미래 issued-at으로 소비한다. | 첫 정상 소비만 원래 경로, 나머지는 `/dashboard`. |
| AUTH-TC-U022 | Production session 서명키 구성은 fail-closed해야 한다. | `NODE_ENV=production`에서 키가 누락·공백·개발 placeholder 중 하나다. | Gateway 구성 오류. Secret 원문 미출력. |
| AUTH-TC-U023 | Account limiter identity는 표기 변형으로 우회되지 않아야 한다. | 같은 valid email에 공백, case 또는 Unicode compatibility form 하나만 바꾼다. | 같은 normalized account와 HMAC fingerprint. |
| AUTH-TC-U024 | HMAC fingerprint는 domain과 key version을 분리해야 한다. | 같은 input을 account/network domain 또는 다른 active key version으로 계산한다. | 서로 다른 digest. Raw input/key 비노출. |
| AUTH-TC-U025 | Untrusted peer의 forwarded header는 source network를 바꾸지 않아야 한다. | Trusted CIDR 밖 peer가 `X-Forwarded-For`를 제출한다. | Direct peer network 사용. |
| AUTH-TC-U026 | Trusted proxy chain은 오른쪽부터 첫 untrusted hop을 선택해야 한다. | Trusted peer가 client와 복수 proxy가 포함된 valid chain을 전달한다. | 첫 untrusted hop의 canonical network 사용. |
| AUTH-TC-U027 | Malformed forwarded chain은 안전하게 fallback해야 한다. | Trusted peer의 chain 항목 하나가 valid IP가 아니다. | Immediate peer network 사용, header 원문 미로깅. |
| AUTH-TC-U028 | Source network prefix는 IPv4 `/24`, IPv6 `/64`여야 한다. | 같은 prefix와 경계 밖 address를 각각 입력한다. | 같은 prefix만 동일 identity. |
| AUTH-TC-U029 | Limited admission은 authenticator를 호출하지 않아야 한다. | Limiter가 blocked decision을 반환한다. | Password/JWT/DB adapter 호출 0회. |
| AUTH-TC-U030 | Limiter unavailable은 fail-closed해야 한다. | Limiter port가 unavailable error를 던진다. | Authenticator 호출 0회, typed temporary-unavailable error. |
| AUTH-TC-U031 | Login 성공은 account와 pair만 reset해야 한다. | Admission 뒤 credential이 성공한다. | Active version account/pair reset, network reset 없음. |
| AUTH-TC-U032 | Login 실패는 admission token을 환급하지 않아야 한다. | Invalid credential 또는 inactive 결과다. | Reset 호출 없음. |
| AUTH-TC-U033 | Login audit projection은 raw identity를 제거해야 한다. | Request context에 raw email/IP와 exception marker가 있다. | Request ID, reason, policy version과 allowlisted dimension만 기록. |
| AUTH-TC-U034 | Login metric label은 bounded해야 한다. | Unknown outcome/dimension/operation을 전달한다. | `unknown` allowlist 값으로 수렴하고 account/IP/fingerprint label 없음. |

## API Tests

| ID | 검증 조건 | 최소 실패 조건 | 기대 결과 |
| --- | --- | --- | --- |
| AUTH-TC-A001 | `POST /auth/signup`은 이메일 형식을 검증해야 한다. | `email` 값이 EmailStr 검증을 통과하지 않는다. | `422` 검증 오류 envelope. |
| AUTH-TC-A002 | `POST /auth/signup`은 필수 필드를 요구해야 한다. | `email`, `password`, `name` 중 하나가 없다. | `422` 검증 오류 envelope. |
| AUTH-TC-A003 | `POST /auth/signup` 성공은 LoginResponse를 반환해야 한다. | 응답에 `user.id`, `user.email`, `user.name`, `user.created_at`, `session.token`, `session.expires_at` 중 하나가 없다. | 테스트 실패. |
| AUTH-TC-A004 | `POST /auth/signup` 성공은 `auth_token` 쿠키를 설정해야 한다. | 성공 응답에 `auth_token` Set-Cookie가 없다. | 테스트 실패. |
| AUTH-TC-A005 | `POST /auth/login`은 필수 필드를 요구해야 한다. | `email` 또는 `password` 중 하나가 없다. | `422` 검증 오류 envelope. |
| AUTH-TC-A006 | `POST /auth/login`은 잘못된 credential을 거부해야 한다. | 없는 email, password 없는 사용자, 또는 틀린 password 중 하나만 충족한다. | `401`, `이메일 또는 비밀번호가 올바르지 않습니다`. |
| AUTH-TC-A007 | `POST /auth/login`은 비활성 사용자를 거부해야 한다. | 올바른 credential의 사용자에 `deactivated_at`이 설정되어 있다. | `403`, `비활성화된 계정입니다`. |
| AUTH-TC-A008 | `POST /auth/login` 성공은 LoginResponse와 `auth_token` 쿠키를 반환해야 한다. | 응답 body의 session token이 비어 있거나 Set-Cookie가 없다. | 테스트 실패. |
| AUTH-TC-A009 | localhost signup/login 쿠키는 secure를 끄고 발급해야 한다. | Host가 `localhost` 또는 `127.0.0.1`인데 `auth_token` 쿠키에 `secure=true`가 적용된다. | 테스트 실패. |
| AUTH-TC-A010 | localhost signup/login 쿠키는 `samesite=lax`여야 한다. | Host가 `localhost` 또는 `127.0.0.1`인데 `auth_token` 쿠키의 samesite가 `lax`가 아니다. | 테스트 실패. |
| AUTH-TC-A011 | localhost signup/login 쿠키는 전체 경로에 적용되어야 한다. | Host가 `localhost` 또는 `127.0.0.1`인데 `auth_token` 쿠키 path가 `/`가 아니다. | 테스트 실패. |
| AUTH-TC-A012 | localhost signup/login 쿠키는 6시간 만료여야 한다. | Host가 `localhost` 또는 `127.0.0.1`인데 `auth_token` 쿠키 max-age가 21600초가 아니다. | 테스트 실패. |
| AUTH-TC-A013 | localhost signup/login 쿠키는 domain을 설정하지 않아야 한다. | Host가 `localhost` 또는 `127.0.0.1`인데 `auth_token` 쿠키 domain이 설정된다. | 테스트 실패. |
| AUTH-TC-A014 | non-local signup/login 쿠키는 secure로 발급되어야 한다. | Host가 non-local인데 `auth_token` 쿠키에 `secure=true`가 적용되지 않는다. | 테스트 실패. |
| AUTH-TC-A015 | non-local signup/login 쿠키는 `samesite=none`이어야 한다. | Host가 non-local인데 `auth_token` 쿠키의 samesite가 `none`이 아니다. | 테스트 실패. |
| AUTH-TC-A016 | non-local signup/login 쿠키는 전체 경로에 적용되어야 한다. | Host가 non-local인데 `auth_token` 쿠키 path가 `/`가 아니다. | 테스트 실패. |
| AUTH-TC-A017 | non-local signup/login 쿠키는 6시간 만료여야 한다. | Host가 non-local인데 `auth_token` 쿠키 max-age가 21600초가 아니다. | 테스트 실패. |
| AUTH-TC-A018 | non-local signup/login 쿠키는 설정 가능한 domain을 사용해야 한다. | `COOKIE_DOMAIN` 또는 요청 host에서 계산 가능한 domain이 있는데 `auth_token` 쿠키 domain이 적용되지 않는다. | 테스트 실패. |
| AUTH-TC-A019 | `POST /auth/logout`은 요청 본문 없이 성공해야 한다. | body 없이 요청한다. | `200`, 로그아웃 확인 응답. |
| AUTH-TC-A020 | `POST /auth/logout`은 `auth_token` 쿠키를 삭제해야 한다. | 응답에 `auth_token` 삭제 Set-Cookie가 없다. | 테스트 실패. |
| AUTH-TC-A021 | `GET /auth/me`는 쿠키 없음을 거부해야 한다. | 요청에 `auth_token` 쿠키가 없다. | `401`, `로그인이 필요합니다`. |
| AUTH-TC-A022 | `GET /auth/me`는 invalid token을 거부해야 한다. | `auth_token`이 JWT 검증을 통과하지 않는다. | `401`, `유효하지 않거나 만료된 토큰입니다`. |
| AUTH-TC-A023 | `GET /auth/me`는 삭제된 사용자를 거부해야 한다. | JWT는 유효하지만 user id로 DB 사용자를 찾지 못한다. | `401`, `유저를 찾을 수 없습니다`. |
| AUTH-TC-A024 | `GET /auth/me`는 비활성 사용자를 거부해야 한다. | JWT는 유효하지만 사용자의 `deactivated_at`이 설정되어 있다. | `403`, `비활성화된 계정입니다`. |
| AUTH-TC-A025 | `GET /auth/me` 성공은 현재 사용자와 세션을 반환해야 한다. | 유효한 `auth_token` 요청의 응답에서 user 또는 session 필드가 빠진다. | 테스트 실패. |
| AUTH-TC-A026 | `GET /auth/google/login`은 non-local redirect URI를 https로 만들어야 한다. | Host가 non-local인데 OAuth redirect URI가 `http://`로 전달된다. | 테스트 실패. |
| AUTH-TC-A027 | Google OAuth callback은 token 교환 실패를 안전하게 거부해야 한다. | `authorize_access_token`이 raw marker를 포함한 예외를 던진다. | `400`, 고정 본문 `OAuth authentication failed`; 응답·로그·audit에 raw marker 없음. |
| AUTH-TC-A028 | Google OAuth callback은 malformed/email 없는 identity를 거부해야 한다. | token 또는 user info가 mapping이 아니거나 Google user info에 `email`이 없다. | `400`, 고정 본문 `OAuth authentication failed`. |
| AUTH-TC-A029 | Google OAuth callback 성공은 세션 쿠키를 설정해야 한다. | 유효한 user info인데 `auth_token` 쿠키가 없다. | 테스트 실패. |
| AUTH-TC-A030 | Google OAuth callback 성공은 서명 session의 safe `next`로 리다이렉트해야 한다. | 유효한 user info와 10분 이내 복귀 컨텍스트가 있는데 원래 path/query/hash로 `302` redirect하지 않는다. | 테스트 실패. |
| AUTH-TC-A031 | localhost Google OAuth callback은 대응하는 client origin으로 redirect해야 한다. | Host가 정확히 `localhost:8000` 또는 `127.0.0.1:8000`인데 대응하는 3000 포트의 safe `next`가 아니다. | 테스트 실패. |
| AUTH-TC-A032 | 회원가입 성공은 audit 이벤트를 기록해야 한다. | signup 성공 응답이 반환되었는데 audit 기록 호출이 없다. | 테스트 실패. |
| AUTH-TC-A033 | 회원가입 실패는 audit 이벤트를 기록해야 한다. | signup 실패 응답이 반환되었는데 audit 기록 호출이 없다. | 테스트 실패. |
| AUTH-TC-A034 | 로그인 성공은 audit 이벤트를 기록해야 한다. | login 성공 응답이 반환되었는데 audit 기록 호출이 없다. | 테스트 실패. |
| AUTH-TC-A035 | 로그인 실패는 audit 이벤트를 기록해야 한다. | login 실패 응답이 반환되었는데 audit 기록 호출이 없다. | 테스트 실패. |
| AUTH-TC-A036 | Google OAuth 성공은 audit 이벤트를 기록해야 한다. | Google OAuth callback 성공 응답이 반환되었는데 audit 기록 호출이 없다. | 테스트 실패. |
| AUTH-TC-A037 | 로그아웃은 audit 이벤트를 기록해야 한다. | logout 성공 응답이 반환되었는데 audit 기록 호출이 없다. | 테스트 실패. |
| AUTH-TC-A038 | Google OAuth 시작 실패는 fixed safe 응답을 반환해야 한다. | `authorize_redirect`가 raw marker를 포함한 예외를 던진다. | `503`, `OAuth login is unavailable`; 응답·로그·audit에 raw marker 없음. |
| AUTH-TC-A039 | Google OAuth return context replay는 기본 경로로 닫혀야 한다. | 성공 callback 뒤 같은 signed session으로 callback을 다시 호출한다. | 첫 호출은 safe `next`, 두 번째는 `/dashboard`. |
| AUTH-TC-A040 | Google OAuth unsafe `next`는 session에 권한 경로로 저장되지 않아야 한다. | 절대/protocol-relative/중첩-encoded/dot-segment 값으로 login을 시작한다. | callback은 `/dashboard`, 외부 host 비노출. |
| AUTH-TC-A041 | Non-local 유사 loopback host는 HTTPS callback을 사용해야 한다. | Host가 `localhost.attacker.example`처럼 loopback 문자열만 포함한다. | HTTPS non-local callback; local client redirect 미적용. |
| AUTH-TC-A042 | Credentialed CORS 구성은 wildcard와 malformed origin을 거부해야 한다. | `*`, 빈 목록, userinfo/path/query/fragment 또는 비-HTTP(S) 값 중 하나를 설정한다. | Gateway 구성 오류. |
| AUTH-TC-A043 | `POST /auth/login` 제한은 generic `429`를 반환해야 한다. | Existing 또는 missing account의 account/network/pair bucket 하나가 비어 있다. | 동일 detail과 `Retry-After` 1~300, 제한 차원·count 비노출. |
| AUTH-TC-A044 | `POST /auth/login` limiter 장애는 generic `503`으로 닫혀야 한다. | Redis connection/script/result 판정이 실패한다. | 동일 detail, `Retry-After: 30`, password/JWT/DB mutation 없음. |
| AUTH-TC-A045 | Login schema 오류는 admission token을 소비하지 않아야 한다. | Invalid email 또는 필수 field 누락으로 `422`가 발생한다. | Limiter 미호출. |
| AUTH-TC-A046 | Existing/missing/passwordless account는 같은 invalid credential 계약을 유지해야 한다. | 세 account 상태에 같은 wrong password를 제출한다. | Byte-equivalent `401` detail, login failure reason은 external 비노출. |
| AUTH-TC-A047 | Inactive account는 password 검증 전 노출되지 않아야 한다. | Inactive account에 wrong password를 제출한다. | `401`; valid password만 기존 `403`. |
| AUTH-TC-A048 | Login 성공 뒤 pair/account 제한은 복구되고 network 사용량은 유지되어야 한다. | Capacity 내 오타 뒤 valid credential로 성공한다. | 다음 same-account login 가능, network token은 이전 시도만큼 소비 상태. |
| AUTH-TC-A049 | Untrusted direct request는 spoofed XFF로 bucket을 바꾸지 못해야 한다. | 같은 peer가 매 요청 다른 XFF를 제출한다. | 동일 network bucket이 소진되어 `429`. |
| AUTH-TC-A050 | Login audit는 raw email/IP/fingerprint/secret을 저장하지 않아야 한다. | Credential, limited, unavailable 각각에 unique sentinel을 넣는다. | Response, audit, metric, log에 sentinel 없음. |
| AUTH-TC-A051 | Password login 실패는 전용 감사만 한 번 기록해야 한다. | Invalid credential `401` 또는 inactive account `403`을 발생시킨다. | `user.login_failed`가 한 번 기록되고 전역 `auth.permission_denied`는 추가되지 않음. |
| AUTH-TC-A052 | 예상하지 못한 credential backend 오류는 원문을 노출하지 않아야 한다. | Account sentinel이 포함된 DB/provider 예외를 발생시킨다. | 고정 `500`, `auth.login.internal_error`; response/log/audit에 sentinel 없음. |

## Distributed Login Admission Integration Tests

| ID | 검증 조건 | 최소 실패 조건 | 기대 결과 |
| --- | --- | --- | --- |
| AUTH-TC-I001 | Pair capacity는 concurrent burst에서 초과되지 않아야 한다. | Empty state에서 동일 pair로 6개 동시 admission을 수행한다. | 정확히 5개 allowed, 1개 blocked. |
| AUTH-TC-I002 | Account capacity는 여러 network에 공유되어야 한다. | 같은 account와 서로 다른 network로 21개 admission을 수행한다. | 정확히 20개 allowed, 나머지 blocked. |
| AUTH-TC-I003 | Network capacity는 여러 account에 공유되어야 한다. | 같은 network와 서로 다른 account로 101개 admission을 수행한다. | 정확히 100개 allowed, 나머지 blocked. |
| AUTH-TC-I004 | Gateway replica는 같은 Redis state를 공유해야 한다. | 서로 다른 limiter instance에서 같은 identity로 capacity+1 요청을 나눈다. | 전체 합계가 capacity를 넘지 않음. |
| AUTH-TC-I005 | Admission은 세 dimension all-or-nothing이어야 한다. | Account bucket만 비운 뒤 pair/network state를 관찰한다. | Blocked request가 pair/network token을 추가 소비하지 않음. |
| AUTH-TC-I006 | Token refill은 Redis server time과 TTL을 따라야 한다. | 최소 refill interval 전후로 같은 bucket을 요청한다. | 전에는 blocked, 이후 한 token allowed; full refill 뒤 key 만료 또는 full-equivalent. |
| AUTH-TC-I007 | HMAC rotation overlap은 old limit을 우회하지 않아야 한다. | Old version bucket을 소진한 뒤 new primary/old previous keyring으로 admission한다. | New bucket이 비어 있어도 blocked; all-or-nothing 계약에 따라 어떤 version/dimension state도 추가 소비하지 않음. |
| AUTH-TC-I008 | Success reset은 active version account/pair key만 제거해야 한다. | Current+previous keyring에서 성공 reset한다. | 두 version account/pair 삭제, network key 유지. |

## CSRF Boundary Tests

| ID | 검증 조건 | 최소 실패 조건 | 기대 결과 |
| --- | --- | --- | --- |
| AUTH-TC-CS001 | Signed token은 session/organization 원문을 포함하지 않고 canonical version/expiry/nonce/MAC만 사용해야 한다. | Token에서 auth cookie 또는 organization sentinel을 검색하거나 비정규 expiry/Base64를 검증한다. | 원문 없음, 비정규 encoding은 `token_invalid`. |
| AUTH-TC-CS002 | Header/cookie double-submit 값은 constant-time equality와 signature를 모두 통과해야 한다. | Header/cookie 누락·불일치·MAC 변조 중 하나를 보낸다. | Fixed `403 auth.csrf_validation_failed`, endpoint 미진입. |
| AUTH-TC-CS003 | Token은 다른 auth cookie, anonymous seed, binding kind와 organization/account scope에서 replay되지 않아야 한다. | 한 binding에서 발급한 token을 다른 binding/scope에 사용한다. | `token_invalid`. |
| AUTH-TC-CS004 | Expired token과 현재 시각보다 TTL+skew를 초과해 미래인 token을 거부해야 한다. | 만료 뒤 또는 발급 시각보다 31초 이전 시각에서 검증한다. | `token_expired` 또는 `token_invalid`. |
| AUTH-TC-CS005 | Browser-proven anonymous `/auth/csrf` bootstrap은 no-store body token과 host-only HttpOnly token/seed cookie를 발급해야 한다. | Bootstrap header와 same-origin Fetch Metadata를 포함하고 Auth cookie 없이 GET한다. | `200`, body/cookie token 일치, Domain 미설정, Path `/api/v1`. |
| AUTH-TC-CS006 | Authenticated bootstrap은 auth cookie를 검증하고 stale anonymous seed를 삭제해야 한다. | Valid auth cookie와 기존 seed를 함께 보낸다. | Auth-bound token, seed `Max-Age=0`. |
| AUTH-TC-CS007 | Invalid 또는 inactive-session auth cookie bootstrap은 anonymous로 같은 응답에서 전환하지 않아야 한다. | Invalid JWT와 inactive account cookie로 각각 GET한다. | 모두 `401 auth.invalid`, auth/CSRF cookie 삭제; Client는 한 번만 재bootstrap. |
| AUTH-TC-CS008 | Signup/login/logout은 pre-auth 또는 auth-bound token 없이 credential/DB lifecycle에 진입하지 않아야 한다. | Exact Origin은 있지만 token이 없거나 forged token이다. | Fixed 403, endpoint effect 0. |
| AUTH-TC-CS009 | Login/signup/OAuth success와 logout은 stale CSRF cookie family를 삭제해야 한다. | 각 성공 응답의 Set-Cookie를 검사한다. | `csrf_token`, `csrf_anon_seed` 삭제. |
| AUTH-TC-CS010 | 모든 unsafe route는 정확히 하나의 route policy를 가져야 한다. | 신규 unauthenticated POST를 registry 없이 추가하거나 명시 예외를 삭제한다. | Startup/architecture test 실패. |
| AUTH-TC-CS011 | Public run/Chatbot과 app-secret run/webhook은 login cookie가 있어도 cookie policy로 바뀌지 않아야 한다. | Login/CSRF cookie를 예외 route에 함께 보낸다. | 기존 anonymous/server credential audience 유지. |
| AUTH-TC-CS012 | Missing/null/unlisted Origin은 body parsing과 side effect 전에 거부해야 한다. | Valid token에 Origin을 누락하거나 attacker Origin을 보낸다. | Fixed 403, body/endpoint effect 0. |
| AUTH-TC-CS013 | Fetch Metadata가 있으면 same-origin/same-site만 허용해야 한다. | `Sec-Fetch-Site: cross-site`를 보낸다. | Fixed 403; header가 없고 다른 증거가 valid이면 호환 허용. |
| AUTH-TC-CS014 | JSON route는 UTF-8 JSON만 허용해야 한다. | text/plain, form, JSON profile/latin1 parameter를 보낸다. | `content_type_invalid`, body/endpoint effect 0. |
| AUTH-TC-CS015 | 승인 multipart와 bodyless route만 해당 content 예외를 사용해야 한다. | Valid multipart upload, malformed boundary, form media type과 명시적 zero length인 빈 Axios POST, non-empty form POST를 각각 보낸다. | Valid multipart/zero-length POST 성공, malformed multipart와 non-empty form POST는 403. |
| AUTH-TC-CS016 | 인증 cookie 없는 cookie-authenticated mutation은 token으로 identity를 만들지 않아야 한다. | Origin과 payload만 보호 route에 보낸다. | Side effect 전 `401 auth.required`. |
| AUTH-TC-CS017 | CSRF denial 관측값은 bounded label만 포함해야 한다. | Token/Origin/session/org/path sentinel을 실패 요청에 주입한다. | Response/log/audit/metric에 sentinel 없음; reason/policy/method/request ID만 기록. |
| AUTH-TC-CS018 | Production/development enforcement는 기본 활성화되고 test만 disable할 수 있어야 한다. | Production disabled 또는 unknown mode로 구성한다. | Startup 오류; `NODE_ENV=test` disabled만 허용. |
| AUTH-TC-CS019 | Client token manager는 origin별 current-scope token 하나, same-origin-and-scope single-flight와 memory-only storage를 유지해야 한다. | 같은 scope로 web/API origin mutation을 보내고, 같은 origin에서 A→B→A scope로 전환하며 storage spy를 사용한다. | Origin별 bootstrap/cookie 분리, 같은 origin의 이전 scope cache 재사용 없음, local/session storage write 0회. |
| AUTH-TC-CS020 | Organization/auth lifecycle은 cached token을 폐기해야 한다. | Organization 변경, signup/login/logout/OAuth 전환 뒤 다음 mutation을 보낸다. | 새 scope/session bootstrap; old token replay 실패. |
| AUTH-TC-CS021 | Fixed CSRF 오류의 자동 replay는 안전한 요청 한 번으로 제한해야 한다. | PUT과 idempotency 없는 POST에서 첫 요청을 403으로 만든다. | PUT 최대 1회 재시도, POST 재시도 0회, 무한 loop 없음. |
| AUTH-TC-CS022 | 보호 direct fetch는 token과 active organization header를 함께 보내야 한다. | Settings/Wizard/RAG stream mutation을 호출한다. | `X-CSRF-Token`, credential, 동일 organization scope 포함. |
| AUTH-TC-CS023 | Workflow stream proxy는 browser security context와 host-only token cookie를 안전하게 중계해야 한다. | Origin, Fetch Metadata, header token, stale cookie와 Authorization을 함께 보낸다. | Context/token 전달, stale CSRF cookie 교체, Authorization 미전달; Gateway가 최종 검증. |
| AUTH-TC-CS024 | Middleware 순서는 허용된 Client가 안전한 CSRF 오류를 읽고 Public/webhook 외곽 경계를 유지해야 한다. | `app.user_middleware` 순서를 검사한다. | Webhook redaction → Public CORS → credentialed CORS → CSRF → Session 순서. |
| AUTH-TC-CS025 | Ambient cross-site GET은 bootstrap cookie를 회전시키지 않아야 한다. | Custom bootstrap header 없이 cross-site image/navigation 요청을 보내거나 unlisted same-site Origin에서 header를 보낸다. | Fixed 403, Set-Cookie 없음, token service/DB 미진입. |
| AUTH-TC-CS026 | Auth/organization lifecycle 전환 전의 in-flight bootstrap은 stale token을 되살리지 않아야 한다. | Bootstrap A가 pending인 동안 cache를 invalidate하고 같은 origin/scope bootstrap B를 시작한 뒤 A를 늦게 완료한다. | A caller는 mutation 전 실패, B는 A 정리 뒤 발급되어 최종 cookie/cache를 소유하고 이후 요청이 B를 재사용. |
| AUTH-TC-CS027 | 동일한 만료 token을 사용한 동시 안전 요청은 refresh를 서로 무효화하지 않아야 한다. | 두 PUT/DELETE가 같은 token으로 403을 받고 첫 refresh가 pending인 동안 두 번째 403을 처리한다. | Generation 폐기와 bootstrap 각 1회, 두 요청 모두 새 token으로 한 번만 재시도해 성공. |
| AUTH-TC-CS028 | 비ASCII double-submit token은 exception 없이 거부해야 한다. | Header/cookie에 같은 비ASCII 문자열을 보낸다. | `compare_digest` 전에 `token_invalid`, fixed 403, endpoint effect 0. |
| AUTH-TC-CS029 | CSRF 감사 request ID는 token/PII header를 반사하지 않아야 한다. | 유효한 CSRF token 또는 임의 문자열을 `X-Request-ID`에도 넣고 거부를 유도한다. | 응답과 audit에는 새 canonical UUID만 있고 입력 원문은 없음. |
| AUTH-TC-CS030 | 동기 CSRF 거부 감사 persistence는 event loop를 점유하지 않아야 한다. | Sync callback에서 DB/I/O 대기를 모사하고 callback thread를 기록한다. | Callback은 bounded worker thread에서 실행되고 고정 403 계약 유지. |
| AUTH-TC-CS031 | Auth API endpoint 행은 endpoint inventory에만 있어야 한다. | `/auth/csrf` endpoint 행을 field/cookie/error table에 중복한다. | 문서 구조 테스트 실패; endpoint 행 정확히 1개. |
| AUTH-TC-CS032 | Wizard mutation의 리소스 organization과 CSRF scope가 일치해야 한다. | LocalStorage는 조직 A지만 Workflow prop은 조직 B인 상태에서 Code/Prompt/Template 요청을 보낸다. | Body와 `X-Organization-Id`가 모두 조직 B이고 bootstrap/token scope도 B. |
| AUTH-TC-CS033 | Malformed bootstrap organization scope는 고정 CSRF 오류로 닫혀야 한다. | 129자 scope 또는 제어 문자를 포함한 scope로 bootstrap한다. | Token/cookie/DB effect 0, `organization_scope_invalid` 감사와 fixed 403. |
| AUTH-TC-CS034 | Bootstrap과 middleware 거부 감사는 같은 bounded 실행 경계를 사용해야 한다. | 동기 callback을 동시에 limiter 초과 실행하고 bootstrap request thread를 기록한다. | 동시 callback 최대 4, bootstrap 검증 thread와 audit worker thread 분리, 고정 오류 유지. |

## Component And Hook Tests

| ID | 검증 조건 | 최소 실패 조건 | 기대 결과 |
| --- | --- | --- | --- |
| AUTH-TC-C001 | `authApi.signup`은 `/auth/signup`으로 POST해야 한다. | signup 호출이 다른 path 또는 GET/PUT으로 나간다. | 테스트 실패. |
| AUTH-TC-C002 | `authApi.login`은 `/auth/login`으로 POST해야 한다. | login 호출이 다른 path 또는 GET/PUT으로 나간다. | 테스트 실패. |
| AUTH-TC-C003 | `authApi.logout`은 `/auth/logout`으로 POST해야 한다. | logout 호출이 다른 path로 나가거나 body 없는 POST를 처리하지 못한다. | 테스트 실패. |
| AUTH-TC-C004 | `authApi.me`는 `/auth/me`로 GET해야 한다. | me 호출이 다른 path 또는 POST로 나간다. | 테스트 실패. |
| AUTH-TC-C005 | `authApi.googleLogin`은 safe 복귀 경로와 함께 Google login endpoint로 이동시켜야 한다. | 호출 후 `window.location.href`가 `${apiBaseUrl}/auth/google/login?next=<encoded-safe-path>`가 아니다. | 테스트 실패. |
| AUTH-TC-C006 | API client는 credential 포함 요청을 사용해야 한다. | `publicApiClient` 또는 `apiClient`의 `withCredentials`가 false이다. | 테스트 실패. |
| AUTH-TC-C007 | 401 인터셉터는 보호 경로에서 로그인으로 보내고 중복 이동을 조정해야 한다. | 현재 path가 `/auth/*`도 `/`도 아닌데 401 후 `/auth/login`으로 이동하지 않거나 같은 path의 동시 interceptor/page redirect가 2초 안에 둘 다 navigation을 소유한다. | 테스트 실패. |
| AUTH-TC-C008 | 401 인터셉터는 auth 화면에서 자동 이동하지 않아야 한다. | 현재 path가 `/auth/login` 또는 `/auth/signup`인데 401 후 `window.location.href`가 바뀐다. | 테스트 실패. |
| AUTH-TC-C009 | 401 인터셉터는 홈에서 자동 이동하지 않아야 한다. | 현재 path가 `/`인데 401 후 `window.location.href`가 바뀐다. | 테스트 실패. |
| AUTH-TC-C010 | `useAuthRedirect`는 인증 성공 시 지정 경로로 replace해야 한다. | `authApi.me()`가 resolve되지만 `router.replace(redirectTo)`가 호출되지 않는다. | 테스트 실패. |
| AUTH-TC-C011 | `useAuthRedirect`는 인증 실패 시 공개 화면을 렌더링 가능하게 해야 한다. | `authApi.me()`가 reject되었는데 `isLoading`이 false가 되지 않는다. | 테스트 실패. |
| AUTH-TC-C012 | `useAuthRedirect`는 인증 확인 중 loading을 유지해야 한다. | `authApi.me()`가 pending인데 `isLoading`이 false이다. | 테스트 실패. |

## UI Flow Tests

| ID | 검증 조건 | 최소 실패 조건 | 기대 결과 |
| --- | --- | --- | --- |
| AUTH-TC-E001 | 로그인 화면은 이메일/비밀번호 성공 후 대시보드로 이동해야 한다. | `authApi.login`이 성공했는데 `/dashboard`로 이동하지 않는다. | 테스트 실패. |
| AUTH-TC-E002 | 로그인 화면은 401을 사용자 메시지로 표시해야 한다. | login 요청이 401로 reject된다. | 인라인 오류와 toast에 `이메일 또는 비밀번호가 올바르지 않습니다.` 표시. |
| AUTH-TC-E003 | 로그인 화면은 배열 detail 422를 사용자 메시지로 표시해야 한다. | login 요청이 `status=422`, `detail=[]`로 reject된다. | `입력한 값이 올바르지 않습니다.` 표시. |
| AUTH-TC-E004 | 로그인 화면은 비배열 detail 422를 사용자 메시지로 표시해야 한다. | login 요청이 `status=422`, 배열이 아닌 detail로 reject된다. | `입력 형식이 올바르지 않습니다.` 표시. |
| AUTH-TC-E005 | 로그인 화면은 5xx를 사용자 메시지로 표시해야 한다. | login 요청이 500 이상으로 reject된다. | `서버에 문제가 발생했습니다. 잠시 후 다시 시도해주세요.` 표시. |
| AUTH-TC-E006 | 로그인 화면은 네트워크 실패를 사용자 메시지로 표시해야 한다. | Axios error에 `response`가 없다. | `네트워크 연결을 확인해주세요.` 표시. |
| AUTH-TC-E007 | 로그인 화면은 Google 로그인 버튼을 safe 복귀 경로가 있는 OAuth 진입점에 연결해야 한다. | `구글로 로그인` 클릭 후 검증된 `next`가 `authApi.googleLogin`에 전달되지 않는다. | 테스트 실패. |
| AUTH-TC-E008 | 회원가입 화면은 비밀번호 불일치를 API 호출 전에 막아야 한다. | `password`와 `confirmPassword`가 다르다. | `authApi.signup` 미호출, `비밀번호가 일치하지 않습니다.` 표시. |
| AUTH-TC-E009 | 회원가입 화면은 성공 후 대시보드로 이동해야 한다. | `authApi.signup`이 성공했는데 성공 toast 또는 `/dashboard` 이동 중 하나가 없다. | 테스트 실패. |
| AUTH-TC-E010 | 회원가입 화면은 backend detail을 우선 표시해야 한다. | signup 요청이 `{ detail: "..." }`로 reject된다. | 해당 detail이 인라인 오류와 toast에 표시. |
| AUTH-TC-E011 | 회원가입 화면은 5xx를 사용자 메시지로 표시해야 한다. | signup 요청이 500 이상이고 detail이 없다. | `서버에 문제가 발생했습니다. 잠시 후 다시 시도해주세요.` 표시. |
| AUTH-TC-E012 | 회원가입 화면은 네트워크 실패를 사용자 메시지로 표시해야 한다. | Axios error에 `response`가 없다. | `네트워크 연결을 확인해주세요.` 표시. |
| AUTH-TC-E013 | 홈 화면은 인증 성공 시 landing을 보여주지 않아야 한다. | `authApi.me()`가 성공한다. | `/dashboard`로 replace되고 landing이 렌더링되지 않는다. |
| AUTH-TC-E014 | 홈 화면은 인증 실패 시 landing을 보여야 한다. | `authApi.me()`가 실패한다. | loading이 해제되고 landing이 렌더링된다. |
| AUTH-TC-E015 | 실제 로그아웃 사용자 경로는 서버 로그아웃 후 로그인 화면으로 이동해야 한다. | 연결된 로그아웃 UI에서 `authApi.logout()` 성공 후 `/auth/login`으로 이동하지 않는다. | 테스트 실패. |
| AUTH-TC-E016 | 로그인 화면은 `429`를 generic 사용자 메시지로 표시해야 한다. | Login 요청이 고정 detail의 `429`로 reject된다. | 인라인 오류와 toast에 고정 메시지 표시, 제한 차원/count 미표시. |
| AUTH-TC-E017 | 랜딩 헤더는 텍스트 브랜드와 인증 진입점만 표시해야 한다. | `Nodease` 브랜드 대신 이미지가 표시되거나 GitHub 외부 링크가 남아 있다. | `/` 브랜드 링크, `Sign in`, `Start for free` 표시. 이미지 브랜드와 GitHub 링크 미표시. |

## Permission Tests

| ID | 검증 조건 | 최소 실패 조건 | 기대 결과 |
| --- | --- | --- | --- |
| AUTH-TC-P001 | auth 공개 엔드포인트는 resource permission을 요구하지 않아야 한다. | signup, login, logout, Google login, Google callback 중 하나가 resource permission dependency를 요구한다. | 테스트 실패. |
| AUTH-TC-P002 | `GET /auth/me`의 인증 경계는 `auth_token` 쿠키여야 한다. | Authorization header만 있고 `auth_token` 쿠키가 없다. | `401`, `로그인이 필요합니다`. |
| AUTH-TC-P003 | Gateway 공통 인증 dependency는 쿠키 토큰을 AuthService로 위임해야 한다. | `get_current_user`가 `auth_token` 쿠키를 `AuthService.get_user_from_token`에 전달하지 않는다. | 테스트 실패. |
| AUTH-TC-P004 | 공통 인증 dependency의 unaudited 401은 permission denied audit로 기록되어야 한다. | Password login 전용 실패가 아닌 인증 dependency 401인데 `auth.permission_denied` 감사 이벤트가 없다. | 테스트 실패. |
| AUTH-TC-P005 | 공통 권한 경계의 unaudited 403은 permission denied audit로 기록되어야 한다. | Password login 전용 실패가 아닌 권한 경계 403인데 `auth.permission_denied` 감사 이벤트가 없다. | 테스트 실패. |
| AUTH-TC-P006 | Conversation/Purge capability는 current user 인증으로 해석되지 않아야 한다. | Capability header만으로 `get_current_user` 또는 `/auth/me`가 user를 반환한다. | 401 또는 capability 전용 dependency에서만 처리. |
| AUTH-TC-P007 | Public Chatbot은 login cookie가 있어도 anonymous audience를 유지해야 한다. | Public route가 cookie user를 execution subject로 승격해 private resource를 허용한다. | Public-only authorization. |
| AUTH-TC-P008 | Authenticated internal surface는 public capability fallback을 허용하지 않아야 한다. | Expired/missing auth cookie를 valid Conversation grant로 대체한다. | 401/403, user identity 미생성. |
| AUTH-TC-P009 | Public capability lifecycle audit은 synthetic user actor를 만들지 않아야 한다. | App/deployment owner, credential/billing principal 또는 grant reference가 `actor_id`로 기록된다. | `actor_id=null`, `actor_type='public'`. |

## Edge Cases

| ID | 검증 조건 | 최소 실패 조건 | 기대 결과 |
| --- | --- | --- | --- |
| AUTH-TC-X001 | LoginResponse의 session token은 쿠키와 별도로 body에 존재해야 한다. | signup/login 성공 body에서 `session.token`이 빠진다. | 테스트 실패. |
| AUTH-TC-X002 | audit metadata는 세션 token 원문을 남기지 않아야 한다. | signup, login, logout, auth failure audit metadata에 JWT 또는 `auth_token` 원문이 포함된다. | 테스트 실패. |
| AUTH-TC-X003 | logout audit은 actor id 없이도 기록될 수 있어야 한다. | logout 요청에 현재 사용자 식별이 없다는 이유만으로 audit 기록이 실패한다. | 테스트 실패. |
| AUTH-TC-X004 | OAuth 실패 audit은 raw provider 예외를 저장하지 않아야 한다. | provider 예외 문자열에 credential-like marker를 포함한다. | audit에는 fixed reason code만 있고 raw marker는 없다. |
| AUTH-TC-X005 | Production HMAC keyring은 fail-fast해야 한다. | Keyring 누락, primary 불일치, 짧은 key 또는 active version 3개 중 하나다. | Gateway 구성 오류, key 원문·길이·digest 미출력. |
| AUTH-TC-X006 | Non-production key fallback은 production에서 활성화되지 않아야 한다. | Production에서 dedicated keyring 없이 valid session key만 제공한다. | Gateway 구성 오류. |
| AUTH-TC-X007 | Redis malformed result는 fail-open으로 처리되지 않아야 한다. | Lua result가 contract와 다른 타입/길이를 반환한다. | `503`, verifier 미호출. |
| AUTH-TC-X008 | Success reset 실패는 이미 성공한 login을 거짓 실패로 바꾸지 않아야 한다. | Credential/DB commit 뒤 Redis delete가 실패한다. | Login 성공 유지, safe limiter failure metric, token state 보수적 유지. |
| AUTH-TC-X009 | Redis integration cleanup은 다른 key를 삭제하지 않아야 한다. | Test prefix 밖 sentinel key를 함께 둔다. | Test 종료 뒤 sentinel 유지, `FLUSHDB` 미사용. |
| AUTH-TC-X010 | Ingress-backed production Helm은 trusted proxy CIDR 누락을 거부해야 한다. | `NODE_ENV=production`, Ingress enabled, trusted proxy CIDR empty로 chart를 render한다. | Helm render/lint 실패; direct Gateway 또는 development profile은 빈 목록 허용. |
| AUTH-TC-X011 | Bundled Compose의 environment mode가 명시적이어야 한다. | `NODE_ENV` override 없이 Compose config를 render한다. | Gateway에 `NODE_ENV=development`; production override 시 `production`과 dedicated keyring startup 검증 적용. |
