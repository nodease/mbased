# ADR-0047: Password login abuse prevention boundary

Status: Accepted

## Context

`POST /api/v1/auth/login`은 현재 account 조회와 password 검증 전에 분산 admission을 수행하지 않는다. 공격자는 같은 account 또는 source network에서 요청을 반복하거나 여러 Gateway replica로 요청을 분산해 password verifier를 제한 없이 호출할 수 있다.

Process-local limiter는 replica 간 상태를 공유하지 못한다. Forwarded address를 무조건 신뢰하면 client가 network identity를 위조할 수 있고, transport peer만 사용하면 trusted reverse proxy 뒤의 모든 client가 하나로 합쳐질 수 있다. Counter와 audit에 raw email/IP를 저장하는 방식은 privacy-safe 운영 계약에도 맞지 않는다.

이 결정은 [ADR-0008](ADR-0008-audit-action-naming-standard.md)의 Auth audit action과 [ADR-0022](ADR-0022-incremental-hexagonal-architecture-adoption.md)의 점진적 application/port/adapter 경계를 따른다. Pre-auth signal을 Security Alert로 승격하는 일은 [ADR-0028](ADR-0028-security-alert-detection-and-lifecycle.md)의 현재 organization-scoped rule 범위에 포함하지 않는다.

## Decision

### Scope

이 경계는 email/password login에만 적용한다. Signup, Google OAuth, 일반 public API, CAPTCHA, MFA, password hashing, JWT/session/cookie 변경과 Security Alert 연동은 포함하지 않는다.

### Identity

- Account는 `EmailStr` 검증 후 Unicode NFKC, trim, `casefold()`로 정규화한다.
- Immediate peer가 configured trusted proxy CIDR에 포함될 때만 forwarded chain을 오른쪽부터 해석하고 첫 untrusted hop을 source address로 사용한다.
- Untrusted peer의 forwarded header는 무시한다. Malformed trusted header는 immediate peer로 닫는다.
- Source network는 IPv4 `/24`, IPv6 `/64`로 정규화하며 loopback direct development는 exact loopback identity를 사용한다.
- Account, network와 account+network는 domain-separated HMAC-SHA-256 fingerprint로 변환한다. Raw identifier와 HMAC key는 Redis, audit, metric과 log에 저장하지 않는다.

### Key Rotation

Production은 dedicated versioned HMAC keyring과 primary version을 요구한다. 최대 한 개 previous version을 rotation overlap으로 허용한다. Overlap 동안 current/previous fingerprint bucket을 같은 admission에서 모두 검사·소비한다. Previous key는 maximum limiter state TTL 이상의 overlap 뒤 제거한다.

Non-production은 명시적 keyring이 없을 때 validated session `SECRET_KEY`에서 domain-separated development key를 파생할 수 있다. Production keyring 오류는 secret 값을 출력하지 않고 startup을 거부한다.

### Distributed Admission

Redis Lua가 server time을 기준으로 세 token-bucket과 active HMAC version을 한 번에 처리한다.

| Dimension | Capacity | Full refill time | Successful login reset |
| --- | ---: | ---: | --- |
| account+network | 5 | 300 seconds | yes |
| account | 20 | 900 seconds | yes |
| network | 100 | 300 seconds | no |

모든 bucket에 token이 있을 때만 모든 bucket에서 하나씩 소비한다. 하나라도 비어 있으면 어떤 bucket도 추가 소비하지 않고 password verifier, JWT 발급과 DB mutation 전에 요청을 차단한다. State key는 full refill 이후 자동 만료되며 같은 Redis Cluster hash tag를 사용한다.

Credential 성공은 active version의 account와 account+network state만 초기화한다. Network state는 성공으로 지우지 않는다. 실패한 credential과 inactive account는 이미 소비한 token을 환급하지 않는다. Request schema `422`는 admission 전이므로 token을 소비하지 않는다.

Server-side progressive sleep은 Gateway resource amplification을 만들 수 있어 사용하지 않는다.

### Errors

- Limited request: `429`, generic Korean detail, rounded `Retry-After` 1~300초
- Limiter unavailable: fail-closed `503`, generic Korean detail, `Retry-After: 30`
- Existing invalid credential: 기존 generic `401` 유지
- Valid credential의 inactive account: 기존 `403` 유지

`429` body는 account 존재 여부, blocked dimension, current count, threshold와 fingerprint를 노출하지 않는다.

### Audit And Observability

새 audit action을 만들지 않는다.

- 성공: `user.login`, reason `auth.login.succeeded`
- invalid credential: `user.login_failed`, reason `auth.login.invalid_credentials`
- inactive: `user.login_failed`, reason `auth.login.inactive`
- limited: `user.login_failed`, reason `auth.login.rate_limited`
- limiter unavailable: `user.login_failed`, reason `auth.login.limiter_unavailable`
- unexpected credential backend failure: `user.login_failed`, reason `auth.login.internal_error`

Login audit는 request context 전체를 복사하지 않고 request ID, reason code, limiter policy version, allowlisted limited dimension과 success actor snapshot만 투영한다. Success actor snapshot은 opaque user ID와 표시 이름만 허용하고 email은 포함하지 않는다. IP/forwarded header, fingerprint, Redis key, password/hash, JWT/cookie와 exception message도 제외한다.

Password login이 `user.login_failed`를 직접 기록한 `401/403`은 HTTP error envelope에 `audit_recorded`를 표시해 전역 `auth.permission_denied` 감사를 중복 생성하지 않는다. 공통 인증 dependency와 permission 경계에서 발생하고 아직 감사되지 않은 다른 `401/403`은 기존 전역 감사 계약을 유지한다.

예상하지 못한 credential backend 예외는 원문을 상위 HTTP/server log로 전파하지 않고 safe typed application error와 고정 `500` 응답으로 변환한다.

Metric/log label은 outcome, allowlisted dimension, policy version과 operation 같은 bounded 값만 사용한다. Account, IP/network, fingerprint, user ID와 request ID를 metric label로 사용하지 않는다.

### Architecture

Password login의 신규 orchestration은 `apps/gateway/application/authentication/`의 framework-independent use case와 port에 둔다. Redis, transport network, legacy AuthService compatibility, audit와 observability는 `apps/gateway/adapters/authentication/`에 둔다. Concrete wiring은 outer composition root가 담당한다.

Endpoint는 request/schema, application error-to-HTTP mapping과 cookie 작성만 담당한다. Signup, OAuth와 JWT token validation은 이번 결정으로 이동하지 않는다.

## Consequences

장점:

- 여러 Gateway replica가 같은 attack budget을 공유한다.
- Concurrent burst와 window-boundary 우회를 줄인다.
- Account enumeration과 raw identity 저장 범위를 줄인다.
- Redis 장애가 limiter bypass로 전환되지 않는다.
- 전체 Auth 재작성 없이 password login 보안 경계를 독립적으로 테스트할 수 있다.

비용과 residual risk:

- Redis 장애 동안 신규 password login은 사용할 수 없다. 기존 authenticated session은 영향을 받지 않는다.
- Shared corporate NAT에서 network bucket 오탐 가능성이 있다.
- 공격자가 특정 account budget을 소진하는 bounded denial은 남는다.
- CAPTCHA/MFA 없이 자동화 공격을 완전히 제거하지는 못한다.
- Production ingress topology에 맞는 trusted proxy CIDR 운영이 필요하다.
- Ingress-backed production Helm release는 trusted proxy CIDR이 비어 있으면 render를 거부한다. Direct Gateway production은 빈 목록으로 forwarded address를 무시할 수 있다.
- Bundled Docker Compose는 local/self-hosted 개발 호환성을 위해 development mode를 기본값으로 사용한다. 운영 사용자는 `NODE_ENV=production`과 dedicated keyring을 명시해야 한다.

## Verification

- Pure policy/application tests는 admission 순서, success reset과 safe error를 검증한다.
- Adapter tests는 trusted proxy spoofing, HMAC rotation과 non-disclosure를 검증한다.
- Actual Redis integration은 multi-instance concurrency, all-or-nothing consume, refill/TTL과 current/previous overlap을 검증한다.
- API tests는 기존 `200/401/403/422` 회귀와 신규 `429/500/503`, verifier pre-call 차단, unexpected backend exception 원문 비노출을 검증한다.

## Follow-Up

- 실제 `429`와 NAT 오탐을 기준으로 capacity/refill을 재검토한다.
- Password hashing algorithm 고도화, signup/OAuth abuse, CAPTCHA/MFA와 pre-auth Security Alert는 별도 이슈로 다룬다.
- Auth foundation 리팩터링에서 legacy compatibility adapter 제거 여부를 검토한다.
