# ADR-0043: Deployment Browser Origin And Embedding Boundary

Status: Accepted
Related ADRs: [ADR-0010](ADR-0010-resource-access-403-404-policy.md), [ADR-0018](ADR-0018-workflow-rag-anonymous-public-only-runtime.md), [ADR-0022](ADR-0022-incremental-hexagonal-architecture-adoption.md), [ADR-0030](ADR-0030-memory-bounded-context.md), [ADR-0033](ADR-0033-conversation-memory-contract-completion.md)

## Context

공개 Chatbot과 Widget은 외부 페이지가 Nodease의 `/embed/chat/{url_slug}` 문서를 iframe으로 로드하고, 해당 문서가 자신의 origin에 상대적인 public API를 호출한다. 이 구조에는 서로 다른 세 browser origin 경계가 존재한다.

1. iframe을 포함하는 외부 ancestor origin
2. Nodease iframe document와 first-party API origin
3. 외부 JavaScript가 Gateway를 직접 호출하는 CORS origin

기존 구현은 `/embed/*`에 `frame-ancestors http: https: file: data:`를 정적으로 적용하고 public info/run endpoint가 `Access-Control-Allow-Origin: *`를 직접 추가한다. 이 설정은 배포별 parent 제한을 제공하지 않으며, iframe 표시와 무관한 외부 direct JavaScript response read까지 허용한다.

ADR-0030은 public/authenticated Chatbot surface 분리와 deployment-owned Origin/embed 설정의 필요성을 기록했지만 browser header, 저장, API와 실패 계약의 소유자는 아니었다. 이 ADR이 해당 세부 계약을 소유하며 ADR-0030 본문은 역사 기록으로 보존한다.

## Decision

### 1. 세 경계를 분리한다

| 경계 | 정책 소유자 | 집행 수단 |
| --- | --- | --- |
| External iframe ancestor | immutable deployment version | iframe HTML response의 CSP `frame-ancestors` |
| Nodease iframe document API | Nodease first-party routing | relative `/api/v1/...` URL과 Next rewrite |
| External direct JavaScript | route-aware CORS contract | V1 미지원. deployment parent 목록을 사용하지 않음 |

Parent origin은 API principal, execution subject, permission, Conversation Access Grant 또는 CORS grant가 아니다. Public route에 login cookie가 포함되어도 anonymous public-only runtime을 유지한다.

### 2. 정책은 deployment version이 소유한다

`workflow_deployments.browser_access_policy` nullable JSONB를 additive로 추가한다.

```json
{
  "contract_version": "deployment_browser_access.v1",
  "embedding": {
    "enabled": true,
    "parent_origins": ["https://portal.example.com"]
  }
}
```

- `chatbot`과 현재 같은 `/embed/chat/{slug}` route를 사용하는 `widget`에만 적용한다.
- `internal_chatbot`과 다른 deployment type의 non-null policy는 거부한다.
- 신규 Chatbot/Widget에서 생략하면 canonical disabled policy를 저장한다.
- legacy null, malformed persisted JSON과 unknown contract version은 embedding disabled로 해석한다.
- active row를 PATCH하지 않는다. 변경은 source deployment snapshot을 복제한 새 immutable version을 생성한다.
- 과거 deployment를 재활성화하면 그 version의 policy도 함께 복원된다.

Generic deployment `config`에는 저장하지 않는다. Browser policy는 별도 typed validation과 safe public projection이 필요한 보안 집행 입력이다.

### 3. Origin은 exact canonical 값만 허용한다

- Production은 HTTPS만 허용한다.
- Dev/test HTTP는 `localhost`, `127.0.0.1`, `[::1]`만 허용한다. Missing/unknown environment는 production으로 본다.
- DNS host는 UTS #46 non-transitional + STD3로 lowercase A-label을 만든다.
- IPv4는 canonical dotted decimal, IPv6는 bracketed compressed literal만 허용한다.
- wildcard, `null`, local/opaque scheme, userinfo, path/query/fragment, trailing-dot host, control character와 legacy numeric IP 표기는 거부한다.
- 각 raw origin은 512 UTF-8 bytes 이하, canonical origin은 최대 20개다.
- canonical duplicate는 자동 제거하지 않고 거부한다.
- CSP header value는 `frame-ancestors ` prefix를 포함해 4096 UTF-8 bytes 이하로 제한한다.
- origin은 canonical lexicographic order로 저장하며 `'self'`를 자동 추가하지 않는다.

Gateway application validator가 canonical form의 최종 권위다. Client validation은 UX-only이며 기존 deployment preflight가 `normalized_browser_access_policy`를 반환한다.

### 4. CSP는 request boundary에서 fail-closed한다

Next.js `proxy.ts`가 `/embed/:path*`의 response boundary를 소유한다.

1. 기본값을 `frame-ancestors 'none'`으로 둔다.
2. exact `/embed/chat/{canonical-slug}`만 Gateway safe projection으로 조회한다.
3. active pointer, app ownership, active 상태와 `type in {chatbot, widget}`을 모두 검증한 projection만 사용한다.
4. 1초 timeout, 404/5xx, malformed response와 unknown version은 `'none'`을 유지한다.
5. HTML과 projection response는 초기 구현에서 `Cache-Control: no-store`다.
6. request `Origin`, `Referer`, `Host`, query와 client hint로 allowlist를 만들지 않는다.

`frame-ancestors`는 모든 ancestor를 검사한다. 중첩 iframe에서는 top-level과 intermediate ancestor가 모두 policy source list에 포함되어야 한다. `'none'`은 iframe embedding만 차단하며 top-level direct navigation은 차단하지 않는다.

Embed response에는 하나의 CSP owner만 둔다. `/embed/*`의 기존 broad static header를 제거하고 embed route에 `X-Frame-Options`를 추가하지 않는다. `/shared/*`의 별도 제품 계약은 이 ADR 범위가 아니다.

### 5. Public direct JavaScript CORS는 V1에서 지원하지 않는다

Public iframe은 relative same-origin API URL을 유지한다. Public info/run endpoint의 수동 wildcard CORS header를 제거한다.

- No Origin server-to-server request는 anonymous public route를 계속 사용할 수 있지만 CORS grant를 만들지 않는다.
- `Origin: null`과 unlisted Origin에는 ACAO를 반환하지 않는다.
- First-party 운영 topology의 configured credentialed `CORS_ORIGINS`는 유지하지만 deployment parent policy가 아니다.
- CORS는 authentication이나 non-browser caller 차단 수단이 아니다.

향후 external direct JavaScript SDK가 필요하면 parent 목록과 독립된 contract version, credentials=false exact origin reflection, `Vary: Origin`, method/header/preflight 계약을 별도 ADR로 정의한다.

### 6. API와 audit

- `POST /api/v1/deployments`와 preflight request에 `browser_access_policy`를 추가한다.
- `POST /api/v1/deployments/{source_deployment_id}/browser-access-revisions`는 source snapshot을 복제한 새 version을 만들며 기본값은 inactive다.
- `GET /api/v1/deployments/public/{url_slug}/browser-access`는 CSP에 필요한 safe projection만 반환한다.
- Management read는 workflow `read`, create/revision은 workflow `deploy` 권한과 기존 resource-hiding 계약을 유지한다.
- Policy validation은 inactive create에서도 warning으로 완화하지 않는다.
- Policy revision, activation과 audit은 기존 deployment lifecycle lock/transaction을 사용한다.

Audit은 기존 canonical deployment create/toggle action을 사용한다. Metadata에는 contract version, enabled, origin count, canonical digest, source/new version과 activation 여부만 두고 raw origin list, request header, cookie, graph/config 원문을 기록하지 않는다.

### 7. Session과 internal Chatbot을 분리한다

Public policy는 `internal_chatbot` 접근 권한, CSRF, private KB permission 또는 Conversation Session capability가 아니다. Internal Chatbot은 별도 authenticated top-level surface를 유지한다.

새 navigation은 current active deployment policy를 사용하지만 이미 로드된 document의 CSP는 pointer 변경으로 교체되지 않는다. 기존 Conversation Session은 생성 당시 deployment ID/version에 고정하며 active pointer 변경으로 자동 rebind하지 않는다. 실제 session enforcement는 Memory bounded context가 소유한다.

### 8. Rollout과 rollback

Legacy unrestricted embed는 두 단계로 전환한다.

1. Configure release: column/schema/API/UI를 배포하고 legacy active Chatbot/Widget을 inventory한다. Broad header가 남은 동안 UI는 policy를 `집행 대기`로 표시한다.
2. Enforce release: readiness 확인 후 dynamic CSP를 활성화하고 broad static CSP와 endpoint wildcard CORS를 제거한다. Legacy null은 `'none'`이다.

Enforce 이후 pre-enforcement frontend artifact로 rollback하면 broad CSP가 복원될 수 있으므로 security rollback target으로 사용하지 않는다. 승인된 rollback artifact/edge rule은 dynamic enforcement를 유지하거나 모든 embed를 `'none'`으로 닫아야 한다.

## Consequences

- 공개 embed는 배포 version별 exact parent에서만 동작한다.
- Gateway policy 조회 장애는 embed 가용성보다 fail-closed를 우선한다.
- Public policy endpoint에서 parent 목록은 관찰 가능하다. 이는 동일 값이 CSP response header에 포함되므로 허용하되 다른 deployment/organization/graph 정보는 반환하지 않는다.
- Widget은 parent policy만 Chatbot과 공유하며 Conversation Memory/session 의미는 공유하지 않는다.
- `/shared`의 broad header는 별도 security inventory 대상이다.
- Deployment create/revision과 CSP enforcement는 Gateway, Shared DB/schema와 Client를 함께 변경하므로 scoped cross-domain 회귀가 필요하다.

## Alternatives Rejected

- **Parent를 request Origin/Referer에서 추론**: navigation header가 안정적이지 않고 ancestor chain authorization을 제공하지 않는다.
- **Parent 목록을 public CORS allowlist로 재사용**: iframe document origin과 parent origin이 다르며 direct API 권한을 불필요하게 넓힌다.
- **Environment fallback**: 배포 version history를 잃고 policy 누락을 unrestricted로 만든다.
- **Generic config JSONB 재사용**: safe projection과 typed security validation 경계가 흐려진다.
- **Active row in-place PATCH**: 같은 deployment version의 behavior가 시간에 따라 달라진다.
- **CSP meta tag**: `frame-ancestors`는 HTTP response header에서 집행되어야 한다.
- **Failure 시 broad cache/header 사용**: policy 장애를 fail-open으로 바꾼다.
