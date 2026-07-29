# Auth Component Spec

Status: Draft

## Screens

- 스크린샷: 없음

## Components

### LandingPage

- 출처: `apps/client/app/landing/page.tsx`
- 경로: `/`에서 비인증 사용자에게 표시
- 책임: Nodease 제품 소개와 로그인·회원가입 진입점을 제공한다.
- 헤더 렌더링:
  - 왼쪽 브랜드 링크는 이미지 없이 `Nodease` 텍스트만 표시하고 `/`로 이동한다.
  - 오른쪽에는 `Sign in`과 `Start for free` 링크만 표시한다.
  - GitHub 외부 링크와 리다이렉트 아이콘은 표시하지 않는다.

### LoginPage

- 출처: `apps/client/app/auth/login/page.tsx`
- 경로: `/auth/login`
- 책임: 이메일/비밀번호 로그인과 Google OAuth 로그인 진입점을 제공한다.
- 렌더링:
  - 페이지 제목 `로그인`과 부제 `계정에 로그인하세요`가 있는 중앙 정렬 인증 카드
  - `error` 상태가 비어 있지 않을 때 표시되는 오류 알림
  - 인라인 Google 로고 SVG가 포함된 Google 로그인 버튼
  - 구분 텍스트 `또는`
  - 제어되는 이메일 입력 필드(`name="email"`, `type="email"`, 필수)
  - 제어되는 비밀번호 입력 필드(`name="password"`, `type="password"`, 필수)
  - `isLoading`이 true일 때 `로그인 중...`을 표시하고 제출 중 비활성화되는 제출 버튼
  - `/` 및 `/auth/signup`으로 이동하는 내비게이션 링크

### SignupPage

- 출처: `apps/client/app/auth/signup/page.tsx`
- 경로: `/auth/signup`
- 책임: 신규 사용자의 이름, 이메일, 비밀번호 기반 회원가입 폼을 제공한다.
- 렌더링:
  - 페이지 제목 `회원가입`과 부제 `새 계정을 만드세요`가 있는 중앙 정렬 인증 카드
  - `error` 상태가 비어 있지 않을 때 표시되는 오류 알림
  - 제어되는 이름 입력 필드(`name="name"`, `type="text"`, 필수)
  - 제어되는 이메일 입력 필드(`name="email"`, `type="email"`, 필수)
  - 제어되는 비밀번호 입력 필드(`name="password"`, `type="password"`, 필수)
  - 제어되는 비밀번호 확인 입력 필드(`name="confirmPassword"`, `type="password"`, 필수)
  - `isLoading`이 true일 때 `처리 중...`을 표시하고 제출 중 비활성화되는 제출 버튼
  - `/` 및 `/auth/login`으로 이동하는 내비게이션 링크

### LogoutButton

- 출처: `apps/client/app/features/auth/components/LogoutButton.tsx`
- 책임: auth 기능 소비자가 재사용할 수 있도록 구현된 로그아웃 버튼을 제공한다.
- 사용 상태: 현재 앱 화면에서 직접 import되거나 렌더링되는 사용처가 확인되지 않은 미사용 auth 컴포넌트다.
- 렌더링:
  - `로그아웃` 라벨이 있는 빨간색 버튼
- 현재 사용 참고:
  - 문서에는 auth 기능 범위의 로그아웃 버튼 구현으로 남기되, 현재 동작하는 사용자 경로에 연결된 컴포넌트로 보지는 않는다.
  - 대시보드 사이드바는 이 컴포넌트를 사용하지 않고 자체 로그아웃 메뉴를 구현한다.

## States

### LoginPage

- 초기 상태:
  - `formData.email = ''`
  - `formData.password = ''`
  - `isLoading = false`
  - `error = ''`
- 편집 상태:
  - 이메일과 비밀번호 입력은 `formData`로 제어된다.
  - `handleChange`는 입력의 `name`과 일치하는 필드를 갱신한다.
- 제출 중 상태:
  - `handleSubmit`은 `error`를 비우고 `isLoading = true`로 설정한다.
  - `isLoading`이 true인 동안 제출 버튼은 비활성화된다.
  - 제출 버튼 라벨은 `로그인`에서 `로그인 중...`으로 바뀐다.
- 성공 상태:
  - 로그인 제출 성공 시 별도 성공 메시지는 렌더링하지 않는다.
  - query의 `next`가 safe same-origin 경로면 그 경로로 이동한다.
  - `next`가 없거나 절대 URL, protocol-relative URL 등 안전하지 않은 값이면 `/dashboard`로 이동한다.
- 오류 상태:
  - `finally`에서 `isLoading`은 false로 재설정된다.
  - `error`는 선택된 사용자 표시용 메시지로 설정된다.
  - `429`는 account 존재 여부, 제한 차원과 count를 표시하지 않고 `로그인 시도가 너무 많습니다. 잠시 후 다시 시도해주세요.`를 표시한다.
  - Limiter unavailable `503`은 기존 5xx generic server 오류 메시지를 표시한다.
  - 인라인 빨간색 오류 알림은 `error`가 비어 있지 않을 때만 렌더링된다.

### SignupPage

- 초기 상태:
  - `formData.name = ''`
  - `formData.email = ''`
  - `formData.password = ''`
  - `formData.confirmPassword = ''`
  - `isLoading = false`
  - `error = ''`
- 편집 상태:
  - 이름, 이메일, 비밀번호, 비밀번호 확인 입력은 `formData`로 제어된다.
  - `handleChange`는 입력의 `name`과 일치하는 필드를 갱신한다.
- 클라이언트 검증 오류 상태:
  - 비밀번호가 일치하지 않으면 `error = '비밀번호가 일치하지 않습니다.'`로 설정한다.
  - 비밀번호가 일치하지 않으면 `isLoading = true`로 설정하기 전에 반환한다.
- 제출 중 상태:
  - 유효한 제출은 `error`를 비우고 `isLoading = true`로 설정한다.
  - `isLoading`이 true인 동안 제출 버튼은 비활성화된다.
  - 제출 버튼 라벨은 `회원가입`에서 `처리 중...`으로 바뀐다.
- 성공 상태:
  - 회원가입 제출 성공 시 성공 토스트를 표시한다.
  - 페이지는 `/dashboard`로 이동한다.
- 오류 상태:
  - `finally`에서 `isLoading`은 false로 재설정된다.
  - `error`는 백엔드 `detail`이 있으면 그 값으로, 없으면 대체 메시지로 설정된다.
  - 인라인 빨간색 오류 알림은 `error`가 비어 있지 않을 때만 렌더링된다.

### LogoutButton

- 사용 상태:
  - 현재 앱에서 직접 렌더링되지 않는 미사용 컴포넌트이므로 아래 상태는 컴포넌트 자체 구현 기준이다.
- 로컬 상태:
  - 저장되는 React 상태는 없다.
- 대기 상태:
  - 로그아웃 요청이 진행 중이어도 버튼은 비활성화되거나 라벨이 바뀌지 않는다.
- 성공 상태:
  - 로그아웃 성공 시 로컬 저장소 정리를 수행하고 router가 `/auth/login`으로 이동한다.
- 오류 상태:
  - 로그아웃 실패는 콘솔에 기록된다.
  - 인라인 오류나 토스트 상태는 렌더링하지 않는다.

## Interactions

### Auth API Wrapper Calls

- 출처: `apps/client/app/features/auth/api/authApi.ts`
- 이 래퍼는 UI를 렌더링하지 않으며 React 컴포넌트 상태를 소유하지 않는다.
- `LoginPage`는 `authApi.login({ email, password })`을 호출하며, 이 함수는 `publicApiClient`를 통해 `/auth/login`에 POST한다.
- `SignupPage`는 `authApi.signup({ name, email, password })`을 호출하며, 이 함수는 `publicApiClient`를 통해 `/auth/signup`에 POST한다.
- `LogoutButton`은 `authApi.logout()`을 호출하며, 이 함수는 빈 body로 `/auth/logout`에 POST한다.
- `useAuthRedirect`는 `authApi.me()`를 호출하며, 이 함수는 `/auth/me`를 GET한다.
- `LoginPage`는 검증된 `next`로 `authApi.googleLogin(returnPath)`을 호출하며, 이 함수는 `window.location.href`를 `${apiBaseUrl}/auth/google/login?next=<encoded-safe-path>`로 설정한다.
- Auth API 요청 실패는 래퍼에서 정규화하지 않는다. 각 호출자가 거부된 Axios 요청을 처리하거나 로컬에서 실패를 무시한다.

### Auth HTTP Client Redirects

- 출처: `apps/client/lib/apiClient.ts`, `apps/client/app/features/workflow/api/workflowApi.ts`, `apps/client/lib/authReturn.ts`
- `publicApiClient`, `apiClient`, workflow API client는 `withCredentials: true`로 생성되므로 auth 관련 API 호출에 브라우저 credential이 함께 전송된다.
- 세 클라이언트는 401 응답 인터셉터를 연결한다.
- `/auth/*` 및 `/` 바깥에서 401이 발생하면 공통 client와 workflow client는 현재 `pathname + search + hash`를 보존한 `/auth/login?next=...`로 이동한다. 같은 경로를 대상으로 interceptor와 page handler가 동시에 이동을 요청하면 `claimLoginRedirectPath`가 2초 동안 한 번만 navigation을 허용한다.
- `resolveSafeAuthReturnPath`는 길이와 percent encoding을 확인하고 최대 5회 반복 decode한다. 각 단계에서 절대/protocol-relative URL, backslash, control character, dot segment와 origin 변화를 거부하며 안정화되지 않는 중첩 encoding은 `/dashboard`로 fallback한다.
- `/auth/*` 또는 `/`에서 401이 발생하면 현재 페이지가 자체 오류나 공개 상태를 표시할 수 있도록 인터셉터는 리다이렉트하지 않는다.

### Auth Redirect Hook

- 출처: `apps/client/app/features/auth/hooks/useAuthRedirect.ts`
- 소비자: `apps/client/app/page.tsx`
- 이 hook은 컴포넌트가 아니며, 인증 상태 확인과 라우트 교체를 조율한다.
- 마운트 시 `authApi.me()`를 호출한다.
- `authApi.me()`가 성공하면 `router.replace(redirectTo)`로 리다이렉트한다.
- `authApi.me()`가 실패하면 호출자가 공개 라우트를 렌더링할 수 있도록 `isLoading = false`로 설정한다.
- `apps/client/app/page.tsx`는 `/dashboard`를 전달하고, `isLoading`이 true인 동안 `null`을 렌더링한다.

### Login Interactions

- 입력 편집:
  - 이메일 입력에 타이핑하면 `formData.email`이 갱신된다.
  - 비밀번호 입력에 타이핑하면 `formData.password`가 갱신된다.
  - 유효한 폼 제출 전에 브라우저는 `required`, `type="email"`, `type="password"` 기반 기본 검증을 적용한다.
- 이메일/비밀번호 제출:
  - 사용자가 로그인 폼을 제출한다.
  - `LoginPage`는 기존 `error`를 비우고 `isLoading = true`로 설정한 뒤 `authApi.login`을 호출한다.
  - 성공 시 safe same-origin `next`가 있으면 그 경로로, 없거나 안전하지 않으면 `/dashboard`로 이동한다.
  - 401 실패는 `이메일 또는 비밀번호가 올바르지 않습니다.`를 표시한다.
  - 429 실패는 backend의 고정 detail `로그인 시도가 너무 많습니다. 잠시 후 다시 시도해주세요.`를 인라인 오류와 toast에 표시한다. `Retry-After`를 countdown으로 표시하거나 account/network 제한 원인을 노출하지 않는다.
  - 배열 `detail`이 있는 422 실패는 `입력한 값이 올바르지 않습니다.`를 표시한다.
  - 배열 `detail`이 없는 422 실패는 `입력 형식이 올바르지 않습니다.`를 표시한다.
  - 5xx 실패는 `서버에 문제가 발생했습니다. 잠시 후 다시 시도해주세요.`를 표시한다.
  - `response`가 없는 네트워크 실패는 `네트워크 연결을 확인해주세요.`를 표시한다.
  - 그 외 실패는 문자열 `detail`이 있으면 해당 값을, 없으면 `로그인 중 오류가 발생했습니다. 잠시 후 다시 시도해주세요.`를 표시한다.
  - 선택된 실패 메시지는 인라인 `error`에 기록되고 `toast.error`에 전달된다.
  - `finally`에서 `isLoading = false`로 재설정한다.
- Google 로그인:
  - 사용자가 `구글로 로그인`을 클릭한다.
  - `LoginPage`는 query의 `next`를 같은 validator로 제한한 뒤 `authApi.googleLogin`에 전달한다.
  - 브라우저 내비게이션은 백엔드 Google OAuth 로그인 URL로 넘겨진다.
  - Gateway는 safe `next`를 서명 세션에 10분·1회용으로 보관하고 Google callback 성공 후 해당 경로로 이동한다. 유효한 컨텍스트가 없으면 `/dashboard`로 이동한다.
- Auth 페이지 내비게이션:
  - `← 홈으로 돌아가기`를 클릭하면 `/`로 이동한다.
  - 회원가입 링크를 클릭하면 `/auth/signup`으로 이동한다.

### Signup Interactions

- 입력 편집:
  - 이름 입력에 타이핑하면 `formData.name`이 갱신된다.
  - 이메일 입력에 타이핑하면 `formData.email`이 갱신된다.
  - 비밀번호 입력에 타이핑하면 `formData.password`가 갱신된다.
  - 비밀번호 확인 입력에 타이핑하면 `formData.confirmPassword`가 갱신된다.
  - 유효한 폼 제출 전에 브라우저는 `required`, `type="email"`, `type="text"`, `type="password"` 기반 기본 검증을 적용한다.
- 사용자가 회원가입 폼을 제출한다.
- `SignupPage`는 기존 `error`를 비운다.
- `password`와 `confirmPassword`가 다르면 `error = '비밀번호가 일치하지 않습니다.'`로 설정하고 API 제출 전에 중단한다.
- 비밀번호가 일치하면 `isLoading = true`로 설정하고 `authApi.signup`을 호출한다.
- 성공 시 성공 토스트를 표시하고 `/dashboard`로 이동한다.
- 실패 시 백엔드 `detail`이 있으면 해당 값을 선택한다.
- 백엔드 `detail`이 없고 5xx 실패이면 `서버에 문제가 발생했습니다. 잠시 후 다시 시도해주세요.`를 표시한다.
- 백엔드 `detail`이 없고 Axios `response`가 없으면 네트워크 실패로 보고 `네트워크 연결을 확인해주세요.`를 표시한다.
- 백엔드 `detail`이 없는 그 외 실패는 `회원가입에 실패했습니다. 다시 시도해주세요.`를 표시한다.
- 선택된 실패 메시지는 인라인 `error`에 기록되고 `toast.error`에 전달된다.
- `finally`에서 `isLoading = false`로 재설정한다.
- Auth 페이지 내비게이션:
  - `← 홈으로 돌아가기`를 클릭하면 `/`로 이동한다.
  - 로그인 링크를 클릭하면 `/auth/login`으로 이동한다.

### Logout Interactions

- `LogoutButton`:
  - 현재 앱에서 직접 연결된 사용자 진입점은 확인되지 않았으며, 아래 흐름은 컴포넌트가 렌더링될 경우의 구현 동작이다.
  - 사용자가 `로그아웃`을 클릭한다.
  - 컴포넌트는 `authApi.logout`을 호출한다.
  - 성공 시 로컬 저장소에서 `moduly_session_token`과 `moduly_user`를 제거하고 `/auth/login`으로 이동한다.
  - 실패 시 `로그아웃 실패:`를 기록하고 이동하지 않는다.

## CSRF Browser Boundary

### Gateway Route Policy Registry

- Gateway composition은 모든 unsafe route를 `cookie_authenticated`, `pre_auth_session`, `public_anonymous`, `server_credential` 중 하나로 분류한다.
- `get_current_user` dependency route는 자동으로 cookie policy가 되며, 자체 cookie helper를 사용하는 Team/Permission route와 Public/server 예외는 exact method/path inventory로 관리한다.
- 미분류 route, stale 예외, duplicate unsafe route와 승인되지 않은 protected media type은 application startup과 architecture test를 실패시킨다.
- OAuth login/callback GET은 custom header 대신 signed one-time state를 사용하는 별도 safe-method 예외다.

### Gateway CSRF Guard

- Guard는 endpoint보다 먼저 Origin, Fetch Metadata, content type, double-submit equality와 HMAC/session/scope를 검증한다.
- Header/cookie token은 constant-time equality 전에 bounded ASCII 형식인지 확인해 비ASCII 입력을 exception 없는 `token_invalid`로 닫는다.
- 실패 body는 고정 `auth.csrf_validation_failed`만 노출한다. Bootstrap organization scope는 token 발급 전에 길이와 제어 문자를 검증하고 실패를 `organization_scope_invalid` bounded reason으로 변환한다. Bounded reason은 metric/audit adapter 내부에서만 사용한다.
- Gateway ingress와 CSRF guard는 같은 request ID helper를 사용한다. Canonical RFC 4122 UUID만 보존하고 그 밖의 header 원문은 새 UUID로 대체한다.
- 동기 audit/metric callback은 middleware와 bootstrap endpoint가 공유하는 process-shared 전용 capacity limiter의 worker thread에서 실행한다. 요청 coroutine은 결과를 기다리되 DB commit으로 event loop와 공용 sync worker를 막지 않으며 callback exception은 고정 응답 뒤로 격리한다.
- 인증 cookie가 없는 protected mutation은 token을 identity로 사용하지 않고 `401 auth.required`로 종료한다.
- CORS는 guard 바깥에서 허용 origin이 오류 응답을 읽게 하고, Public Conversation CORS와 webhook query redaction의 더 바깥 경계를 유지한다.

### Client CSRF Token Manager

- `csrfToken.ts`는 실제 mutation origin의 `/api/v1/auth/csrf`에 `X-CSRF-Bootstrap: 1`을 보내고 응답을 runtime 검증한 뒤 token과 expiry를 module memory에만 저장한다.
- Origin마다 현재 organization/account scope의 token 하나만 유지한다. 같은 mutation origin과 scope의 동시 요청만 하나의 bootstrap Promise와 cached token을 공유하며, scope 전환은 같은 origin의 이전 token을 대체한다. 다른 origin, reload와 tab은 token을 공유하지 않는다.
- Axios request interceptor는 active organization header가 결정된 뒤 unsafe request에 `X-CSRF-Token`을 추가한다. Response interceptor는 고정 CSRF error가 현재 cache의 동일 origin/scope/token을 거부한 경우에만 generation을 올린다. 동일 token을 사용한 동시 `403`은 한 refresh bootstrap을 공유하며 PUT/DELETE 또는 idempotency key 요청만 최대 한 번 재시도한다.
- `csrfFetch`는 Settings, Wizard, RAG stream과 Workflow stream처럼 Axios를 통하지 않는 protected mutation에 같은 계약을 제공한다. Workflow organization을 받은 Code/Prompt/Template Wizard는 그 authoritative ID를 body와 `X-Organization-Id`에 함께 보내 ambient active organization fallback이 token scope를 바꾸지 못하게 한다. Public Chatbot/Public run, app-secret 실행과 presigned object upload에는 적용하지 않는다.
- Signup/login/logout 성공, OAuth navigation과 `nodease-active-organization-changed` event는 cache generation을 올리고 cached token을 폐기한다. 이전 generation의 진행 중 bootstrap은 cache를 되살리지 못하며, 같은 origin의 새 bootstrap은 이전 요청 정리 뒤 cookie를 마지막으로 갱신한다. Invalid 또는 inactive-session HttpOnly auth cookie bootstrap `401`은 cookie 삭제 반영을 위해 최대 한 번만 재시도한다.

### Workflow Stream Proxy

- Browser는 same-origin `/stream-api/workflows/{workflowId}`에 CSRF header를 보낸다.
- Next route는 original Origin, `Sec-Fetch-Site`, CSRF token, organization과 bounded request context만 전달한다.
- API host-only CSRF cookie가 Next host에 전달되지 않는 경우를 위해 strict 문자·길이 검사를 통과한 header token만 outbound `csrf_token` cookie로 복제한다. 기존 `csrf_token` cookie는 제거한 뒤 하나만 전달하며 Authorization은 전달하지 않는다.
- Gateway가 최종 signature/session/organization 검증을 수행하므로 proxy 복제는 인증이나 권한 판단을 대체하지 않는다.
## Accessibility

### LoginPage

- 라벨:
  - 이메일 라벨은 `htmlFor="email"`을 사용하고 입력은 `id="email"`을 사용한다.
  - 비밀번호 라벨은 `htmlFor="password"`를 사용하고 입력은 `id="password"`를 사용한다.
- 입력:
  - 이메일 입력은 `type="email"`과 `required`를 사용한다.
  - 비밀번호 입력은 `type="password"`와 `required`를 사용한다.
  - 입력은 기본 `<input>` 요소이며 키보드 포커스가 가능하다.
  - 입력은 `focus:outline-none`으로 기본 outline을 제거하고 `focus:ring-blue-500` 및 `focus:border-blue-500`으로 포커스 피드백을 제공한다.
- 버튼과 링크:
  - 이메일/비밀번호 제출은 기본 `<button type="submit">`을 사용한다.
  - 제출 버튼은 로딩 중 기본 `disabled`를 사용한다.
  - 제출 버튼은 명시적인 포커스 링 클래스를 제공한다.
  - Google 로그인은 기본 `<button type="button">`을 사용하고 보이는 텍스트 `구글로 로그인`을 포함한다.
  - Google 로그인 버튼은 명시적인 포커스 링 클래스를 제공한다.
  - 홈 및 회원가입 내비게이션은 Next `Link`를 사용하며 앵커 형태의 내비게이션으로 렌더링된다.
- 오류:
  - `error`가 비어 있지 않을 때 오류 텍스트는 Google 로그인 버튼 위에 인라인으로 렌더링된다.
  - 오류 스타일은 빨간색 배경, 테두리, 텍스트를 사용한다.
  - 현재 코드는 오류 컨테이너에 `role="alert"`나 `aria-live`를 설정하지 않는다.
  - 현재 코드는 오류 메시지를 입력과 `aria-describedby`로 연결하지 않는다.
  - 현재 코드는 유효하지 않은 입력에 `aria-invalid`를 설정하지 않는다.
- 현재 코드 참고:
  - 최상단의 보이는 헤딩은 `h1`이 아니라 `h2`이다.
  - 인라인 Google SVG는 `aria-hidden`을 설정하지 않는다.
  - 입력은 `autoComplete` 속성을 설정하지 않는다.

### SignupPage

- 라벨:
  - 이름 라벨은 `htmlFor="name"`을 사용하고 입력은 `id="name"`을 사용한다.
  - 이메일 라벨은 `htmlFor="email"`을 사용하고 입력은 `id="email"`을 사용한다.
  - 비밀번호 라벨은 `htmlFor="password"`를 사용하고 입력은 `id="password"`를 사용한다.
  - 비밀번호 확인 라벨은 `htmlFor="confirmPassword"`를 사용하고 입력은 `id="confirmPassword"`를 사용한다.
- 입력:
  - 이름 입력은 `type="text"`와 `required`를 사용한다.
  - 이메일 입력은 `type="email"`과 `required`를 사용한다.
  - 비밀번호와 비밀번호 확인 입력은 `type="password"`와 `required`를 사용한다.
  - 입력은 기본 `<input>` 요소이며 키보드 포커스가 가능하다.
  - 입력은 `focus:outline-none`으로 기본 outline을 제거하고 `focus:ring-blue-500` 및 `focus:border-blue-500`으로 포커스 피드백을 제공한다.
- 버튼과 링크:
  - 회원가입 제출은 기본 `<button type="submit">`을 사용한다.
  - 제출 버튼은 로딩 중 기본 `disabled`를 사용한다.
  - 제출 버튼은 명시적인 포커스 링 클래스를 제공한다.
  - 홈 및 로그인 내비게이션은 Next `Link`를 사용하며 앵커 형태의 내비게이션으로 렌더링된다.
- 오류:
  - `error`가 비어 있지 않을 때 오류 텍스트는 폼 위에 인라인으로 렌더링된다.
  - 비밀번호 불일치는 동일한 인라인 오류 컨테이너를 통해 표시된다.
  - 오류 스타일은 빨간색 배경, 테두리, 텍스트를 사용한다.
  - 현재 코드는 오류 컨테이너에 `role="alert"`나 `aria-live`를 설정하지 않는다.
  - 현재 코드는 오류 메시지를 입력과 `aria-describedby`로 연결하지 않는다.
  - 현재 코드는 유효하지 않은 입력에 `aria-invalid`를 설정하지 않는다.
- 현재 코드 참고:
  - 최상단의 보이는 헤딩은 `h1`이 아니라 `h2`이다.
  - 입력은 `autoComplete` 속성을 설정하지 않는다.

### LogoutButton

- 현재 미사용 컴포넌트이므로 아래 접근성 내용은 컴포넌트 자체 JSX 기준이다.
- 보이는 텍스트 `로그아웃`이 있는 기본 `<button>` 요소를 사용한다.
- 버튼은 기본적으로 키보드 포커스가 가능하다.
- 현재 코드는 로그아웃 중 대기/로딩 라벨이나 비활성 상태를 제공하지 않는다.
- 현재 코드는 로그아웃 실패 시 접근 가능한 오류 메시지를 렌더링하지 않으며, 실패는 콘솔에만 기록된다.
