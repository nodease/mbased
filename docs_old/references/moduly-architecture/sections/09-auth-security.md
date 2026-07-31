# 인증과 보안

> Status: Reference-only historical document.
>
> 이 문서는 과거 `main` 브랜치 기준 역공학 산출물이다. 현재 구현 기준은 active `docs/` 문서와 코드를 따른다.

## 인증 모델

구현:

- 이메일/비밀번호 JWT 인증
- Google OAuth 로그인
- JWT는 `auth_token` HttpOnly cookie에 저장

주요 파일:

- `apps/gateway/api/v1/endpoints/auth.py`
- `apps/gateway/services/auth_service.py`
- `apps/gateway/auth/dependencies.py`
- `apps/gateway/auth/oauth.py`
- `apps/client/app/features/auth/api/authApi.ts`

## 이메일/비밀번호 인증

Signup:

1. 이메일 중복 확인
2. password hash 생성
3. User 생성
4. JWT 생성
5. `auth_token` cookie 설정

Login:

1. email로 User 조회
2. password hash 검증
3. JWT 생성
4. `auth_token` cookie 설정

Password hash:

- SHA-256 + random salt
- 저장 형태: `{salt}${hash}`

주의:

- 보통 운영 인증에는 bcrypt/argon2가 권장된다. 현재 코드에는 SHA-256 salt 방식이 구현되어 있다.

## JWT

파일: `apps/gateway/services/auth_service.py`

| 항목 | 값 |
| --- | --- |
| secret | `SECRET_KEY` 환경변수, 기본값 `<placeholder-secret>` |
| algorithm | HS256 |
| expiry | 6시간 |
| payload | `user_id`, `exp` |

## 쿠키 정책

파일: `apps/gateway/api/v1/endpoints/auth.py`

Local:

- `secure=false`
- `samesite=lax`
- `path=/`
- `max_age=21600`

Production 판단:

- request host에 `localhost`, `127.0.0.1`이 없으면 production으로 판단
- `secure=true`
- signup/login은 `samesite=none`
- Google callback은 `samesite=lax`
- `COOKIE_DOMAIN`이 있으면 사용, 없으면 host에서 상위 domain 추정

## Google OAuth

파일: `apps/gateway/auth/oauth.py`

환경변수:

- `GOOGLE_CLIENT_ID`
- `GOOGLE_CLIENT_SECRET`

scope:

- `openid email profile`

callback 흐름:

1. Google access token 수신
2. userinfo에서 email/name/sub/picture 추출
3. email 기준 기존 유저 조회 또는 생성
4. 자체 JWT 발급
5. `auth_token` cookie 설정
6. `/dashboard`로 redirect

local 개발에서는 Gateway host가 `localhost:8000`이면 `http://localhost:3000/dashboard`로 redirect한다.

## 권한 규칙

명확히 확인된 규칙:

| 기능 | 권한 |
| --- | --- |
| 앱 상세 조회 | 소유자만 |
| 앱 수정 | 생성자만 |
| 앱 삭제 | 생성자만 |
| 앱 생성 | 로그인 사용자 |
| 워크플로우 생성 | app 소유자만 |
| 배포 생성 | app 소유자만 |
| 배포 노드 목록 | 현재 사용자 소유 workflow_node 배포만 |
| 지식베이스 | 현재 사용자 소유 범위 |
| LLM credential | 현재 사용자 소유 범위 |

## API/Webhook secret

App 모델은 `url_slug`와 `auth_secret`을 가진다.

사용처:

- `/api/v1/run/{url_slug}` 인증
- `/api/v1/hooks/{url_slug}` Webhook 인증
- 배포 성공 화면에서 API URL/secret 표시

## 암호화

### MASTER_KEY / AES-GCM

파일: `apps/gateway/core/security.py`

- `MASTER_KEY` 필요
- Base64 decode 후 32 bytes여야 함
- AES-256-GCM
- nonce 12 bytes

### ENCRYPTION_KEY / Fernet

파일:

- `apps/gateway/utils/encryption.py`
- `apps/shared/utils/encryption.py`
- `apps/workflow_engine/utils/encryption.py`

용도:

- 외부 DB connection password
- SSH password/private key
- 일부 LLM credential/config 흐름

주의:

- `MASTER_KEY`와 `ENCRYPTION_KEY`가 모두 존재한다. 어떤 데이터가 어떤 키로 암호화되는지 도메인별로 정리/통합이 필요하다.

## 프론트 iframe 보안

파일: `apps/client/next.config.ts`

- `/embed/*`, `/shared/*`: 외부 iframe embedding 허용
- 나머지 경로: `SAMEORIGIN`, `frame-ancestors 'self'`

## Sandbox 보안

주요 정책:

- NSJail 기반 프로세스 격리
- Docker Compose에서 sandbox container는 `privileged: true`
- 기본 네트워크 비활성화: `SANDBOX_ENABLE_NETWORK=false`
- timeout, memory, output size 제한
- tenant별 동시 실행 제한

## Proxy

Compose에는 Squid proxy가 있다.

역할:

- 외부 API 호출 시 사설 IP/AWS metadata 접근 차단 목적
- Gateway/Workflow Engine에 `HTTP_PROXY`, `HTTPS_PROXY`, `NO_PROXY` 설정

## 보안상 주의할 점

- `SECRET_KEY` 기본값은 운영에서 반드시 변경해야 한다.
- `MASTER_KEY`, `ENCRYPTION_KEY` 분실 시 암호화된 데이터 복구 불가.
- 현재 `.env` 실제 파일은 읽거나 문서화하지 않았다.
- GitHub/Mail/HTTP/DB 노드가 외부와 통신하므로 SSRF, credential leakage, log redaction 검토가 필요하다.
- App clone은 일부 민감정보를 제거하지만 모든 노드의 민감필드가 포함되었는지 지속 검토가 필요하다.
