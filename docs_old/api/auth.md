# 인증 API

Status: Draft
Authority: API
Source of Truth: Yes
Verified Against: dev @ ec576b4f24155697aed8843acc6e5a3fc835f7e1
Related ADRs: [ADR-202606290145-active-organization-header-context](../decisions/ADR-202606290145-active-organization-header-context.md)
Background ADRs: [ADR-202606271559-active-organization](../decisions/ADR-202606271559-active-organization.md)

## 범위

인증, session cookie, 현재 사용자 조회, active organization context 전달 계약을 정의한다.

## 현재 구현 엔드포인트

| Status | Method | Path | Request | Response | 설명 |
| --- | --- | --- | --- | --- | --- |
| Implemented | `POST` | `/api/v1/auth/signup` | `SignupRequest` | `LoginResponse` | email/password/name 기반 가입 후 session 발급 |
| Implemented | `POST` | `/api/v1/auth/login` | `LoginRequest` | `LoginResponse` | 로그인 후 session 발급 |
| Implemented | `POST` | `/api/v1/auth/logout` | 없음 | `{ "message": string }` | session cookie 제거 |
| Implemented | `GET` | `/api/v1/auth/me` | 없음 | `LoginResponse` | 현재 session 사용자 조회 |
| Implemented | `GET` | `/api/v1/auth/google/login` | 없음 | redirect | Google OAuth 시작 |
| Implemented | `GET` | `/api/v1/auth/google/callback` | OAuth callback | redirect | Google OAuth 완료 |

## 스키마

### `SignupRequest`

| Field | Type | Required | 설명 |
| --- | --- | --- | --- |
| `email` | email | Yes | 사용자 email |
| `password` | string | Yes | 사용자 password |
| `name` | string | Yes | 사용자 표시 이름 |

### `LoginRequest`

| Field | Type | Required | 설명 |
| --- | --- | --- | --- |
| `email` | email | Yes | 사용자 email |
| `password` | string | Yes | 사용자 password |

### `LoginResponse`

| Field | Type | 설명 |
| --- | --- | --- |
| `user` | `UserResponse` | 사용자 정보 |
| `session` | `SessionInfo` | token과 만료 시각 |

`SessionInfo.token`은 민감 값으로 취급한다. 로그나 문서 예시에 원문 token을 남기지 않는다.

## Active Organization Context

Active organization은 request header로 전달한다. 서버는 active organization을 session/cookie에 저장하지 않는다.

| Status | Header/Cookie | 설명 |
| --- | --- | --- |
| Implemented | `X-Organization-Id` | API 요청에서 명시적으로 active organization을 전달한다. |
| Not selected | session/cookie context | 서버 session이나 cookie에 active organization을 저장하지 않는다. |
| Legacy fallback | 없음 | organization context가 없는 과도기 경로에서 첫 active organization membership을 primary organization으로 사용할 수 있다. |

`GET /api/v1/organizations/current`는 `X-Organization-Id` 값을 검증해 현재 요청의 active organization을 반환한다. 상세 endpoint는 [organization-rbac.md](organization-rbac.md)를 따른다.

## Cookie 및 Audit 구현 기준

- `POST /api/v1/auth/signup`, `POST /api/v1/auth/login`, Google OAuth callback은 자체 JWT를 `auth_token` cookie에 저장한다.
- email/password signup/login은 local host에서 `SameSite=Lax`, `Secure=false`로 설정한다.
- email/password signup/login은 production host에서 `SameSite=None`, `Secure=true`로 설정하고, `COOKIE_DOMAIN`이 있으면 해당 domain을 사용한다. 없으면 요청 host의 상위 domain을 추론한다.
- Google OAuth callback은 현재 코드 기준 host가 production이면 `Secure=true`와 추론/환경변수 기반 domain을 사용하지만, `SameSite`는 항상 `Lax`로 설정한다.
- `auth_token` cookie의 `max_age`는 현재 코드 기준 6시간이다.
- `GET /api/v1/auth/me`는 cookie에서 token을 읽어 `LoginResponse`를 재구성한다. 별도 bearer token header를 읽지 않는다.
- signup/login 성공은 `user.signup`, `user.login` audit action으로 기록한다.
- signup/login 실패는 `user.signup_failed`, `user.login_failed` audit action으로 기록하며 actor는 `system`이다.
- logout은 cookie를 삭제하고 `user.logout`을 기록하지만, 현재 구현은 logout 시점의 user id를 별도 검증하지 않아 actor id 없이 기록한다.
