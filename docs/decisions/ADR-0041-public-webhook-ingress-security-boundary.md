# ADR-0041: Public webhook ingress security boundary

Status: Accepted
Related ADRs: ADR-0018, ADR-0021, ADR-0022

## Context

`POST /api/v1/hooks/{url_slug}`는 로그인 세션 없이 외부 시스템이 workflow를 실행하는 공개 진입점이다. 기존 구현은 App secret을 query `token`, Bearer header 또는 `X-Webhook-Secret`으로 받을 수 있고 JSON body의 media type, 실제 byte 크기, 구조 복잡도와 전체 처리 시간을 application 경계에서 제한하지 않는다.

URL query에 포함된 secret은 browser history, reverse proxy와 access log, monitoring 도구에 남을 수 있다. 또한 인증된 caller라도 과대하거나 비정상적인 JSON을 queue admission 전에 보내 Gateway 자원을 소비할 수 있다. Capture helper의 로그인·권한·redacted preview 경계는 ADR-0021이 소유하며 public trigger 입력 경계와 분리해야 한다.

## Options Considered

1. Query secret과 제한 없는 JSON body를 유지한다.
   - 기존 consumer 호환성은 높지만 credential leakage와 application resource exhaustion 위험을 유지한다.
2. Nginx 또는 production ingress 제한에만 의존한다.
   - 앞단 buffering은 줄일 수 있지만 Gateway 직접 접근과 환경별 proxy 차이를 통제하지 못한다.
3. Header-only credential과 Gateway-owned bounded JSON policy를 적용하고 edge guard를 함께 둔다.
   - Consumer migration이 필요하지만 모든 실행 환경에서 동일한 보안 계약을 강제할 수 있다.

## Decision

Public webhook credential은 `Authorization: Bearer <secret>`을 primary로, `X-Webhook-Secret: <secret>`을 compatibility source로 사용한다. 한 요청에는 정확히 하나의 source만 허용한다. Query에 `token` key가 있으면 값이나 유효 header의 존재와 관계없이 `400 webhook.query_secret_not_supported`로 거부하고 query 값을 읽거나 비교·기록하지 않는다. 두 header source 또는 같은 credential header occurrence가 중복되면 `400 webhook.credential_ambiguous`로 거부한다.

Gateway ASGI transport middleware는 outermost user middleware로 등록한다. `/api/v1/hooks` 경로에 도달한 query에서 exact 또는 percent-encoded `token` key field를 다른 middleware와 Uvicorn access logging 전에 제거한다. 값은 decode하거나 request state에 보존하지 않고 boolean presence marker만 남긴다. Public trigger endpoint는 이 marker로 기존 400 rejection을 유지한다. 다른 query field는 capture/provider contract를 깨뜨리지 않도록 원래 bytes로 보존한다. Endpoint를 직접 호출하는 test와 대체 ASGI host를 위해 endpoint adapter도 남아 있는 raw query의 token key를 fail-closed하게 확인한다.

Credential candidate는 1~512 ASCII bytes로 제한하고 App의 현재 active secret과 constant-time 비교한다. Missing, malformed, oversized, non-ASCII, invalid credential과 invalid server-side verifier state는 동일한 `403 webhook.authentication_failed`로 fail-closed한다. MBA-93은 secret issuance, response removal 또는 rotation schema를 추가하지 않으며 해당 lifecycle은 MBA-247이 소유한다.

Payload는 `application/json` 또는 `application/*+json`만 허용한다. `charset`은 생략하거나 UTF-8이어야 하고 `Content-Encoding`은 생략하거나 단일 `identity`여야 한다. Duplicate/malformed media metadata와 압축 body는 `415 webhook.payload.unsupported_media_type`으로 거부한다.

Gateway는 다음 V1 limit을 immutable application policy로 적용한다.

| Limit | Value |
| --- | ---: |
| Actual streamed body | 1,048,576 bytes |
| Ingress processing deadline | 5 seconds |
| JSON depth | 20, root depth 1 |
| Total JSON nodes | 10,000, root included |

`Content-Length`는 early rejection 최적화일 뿐 actual streamed bytes가 최종 기준이다. Duplicate, negative 또는 base-10 integer가 아닌 `Content-Length`, client disconnect, UTF-8 BOM, invalid UTF-8/JSON, non-finite number, depth/node 초과는 `400 webhook.payload.invalid`로 닫는다. Declared 또는 actual size 초과는 `413 webhook.payload.too_large`, ingress deadline 초과는 `408 webhook.payload.timeout`을 사용한다.

Root JSON은 현재 Workflow Engine `Dict[str, Any]` input contract에 맞춰 object만 허용한다. Array, string, number, boolean과 null root는 `400 webhook.payload.invalid`로 downstream 전에 거부하며 nested value는 표준 JSON type을 보존한다. 서버는 non-object를 `{"value": ...}`로 자동 포장하지 않는다. Parsed object는 bounded workflow input으로만 전달하고 payload field를 ORM 또는 server-derived execution context에 merge하지 않는다. Duplicate valid delivery는 별도 요청으로 각각 admission하며 inbound exactly-once나 payload-hash deduplication을 보장하지 않는다. Header credential은 body signature, freshness 또는 replay protection을 제공하지 않는다.

처리 순서는 App lookup, query/header source validation, credential authentication, media/declared-size validation, bounded body receive, strict JSON validation, capture, active deployment/runtime policy, budget, background publish registration 순이다. 인증 또는 payload 검증 실패는 body 불필요 read, capture mutation, deployment/budget 조회, BackgroundTasks 등록과 Celery publish를 수행하지 않는다.

FastAPI/SQLAlchemy에 의존하지 않는 policy와 typed error를 `apps/gateway/application/webhook_ingress/`에 두고 ASGI stream, HTTP mapping과 기존 orchestration은 endpoint adapter가 담당한다. 이번 결정은 기존 capture, deployment, budget, publish orchestration 전체를 재작성하지 않는다.

Repository Nginx의 `/api/v1/hooks/` 경로는 access log를 끄고 location error log도 억제한다. Nginx의 413 같은 edge rejection은 기본 error log에 query를 포함한 request target을 기록할 수 있기 때문이다. 이 경로에는 `client_max_body_size 1m`, `client_body_timeout 5s`, `proxy_request_buffering off`를 적용한다. Request body를 Gateway로 즉시 stream해 application deadline이 첫 upstream read부터 적용되게 한다. Nginx timeout은 연속 body read 사이 idle timeout이며 Gateway의 전체 deadline을 대체하지 않는다. Production ALB/Ingress의 실제 logging과 body guard는 rollout 전 별도 운영 검증을 요구한다. Webhook edge 관측은 raw request target을 남기지 않는 Gateway의 status/metric/audit으로 수행한다.

## Rationale

- URL에서 secret을 제거해 proxy, history와 monitoring 경로의 신규 노출을 줄인다.
- 인증 전에 body를 읽지 않고 queue admission 전에 bounded validation을 끝내 공개 고비용 경계를 보호한다.
- Application과 edge 제한을 함께 사용해 direct Gateway 접근과 proxy buffering을 모두 다룬다.
- Pure policy로 분리해 framework 없이 경계값을 검증하면서 기존 webhook 동작의 변경 범위를 제한한다.
- Static error code와 generic authentication failure는 parser·secret 내부 상태 노출을 막는다.

## Consequences

- Query token consumer는 배포 전 header 방식으로 이관해야 한다. 확인되지 않은 query consumer가 있으면 hard cutover를 진행하지 않는다.
- 과거 query URL에 사용된 secret은 이미 upstream log에 남았을 수 있으므로 MBA-247 rotation 대상으로 취급한다.
- Header 설정을 지원하지 않는 provider는 자동 예외를 받지 않으며 provider-specific signed adapter 또는 명시적 정책 결정이 필요하다.
- MBA-247 병합 전에는 current single active secret만 검증하고 rotation overlap race를 보장하지 않는다.
- Provider별 HMAC signature, timestamp/nonce, inbound deduplication과 분산 rate limit은 각각 후속 범위다.
- Object가 아닌 native payload가 필요한 provider는 별도 adapter가 provider schema를 검증하고 명시적인 canonical object로 변환해야 한다. Generic endpoint는 암묵적 wrapping을 제공하지 않는다.

## Affected Files

- `apps/gateway/application/webhook_ingress/`
- `apps/gateway/middleware/webhook_query_redaction.py`
- `apps/gateway/main.py`
- `apps/gateway/api/deps.py`
- `apps/gateway/api/v1/endpoints/webhook.py`
- `apps/client/app/features/workflow/components/deployment/SuccessStep.tsx`
- `apps/client/app/features/workflow/components/nodes/webhook/components/WebhookTriggerNodePanel.tsx`
- `docker/nginx/nginx.conf`
- `docs/features/deployment/`

## Follow-up Review Notes

- MBA-247의 one-time issuance/rotation verifier가 병합되면 이 endpoint를 해당 verifier에 연결하고 current/previous overlap과 revoke 경합을 통합 테스트한다.
- MBA-94는 공개·고비용 endpoint의 분산 rate limit을 별도로 도입한다.
- Provider signature 요구가 생기면 raw-byte canonicalization, timestamp skew, event identity와 retention을 provider별 ADR에서 정의한다.
- Production ingress가 query/header safe logging과 bounded body guard를 제공하지 못하면 rollout blocker 또는 별도 infrastructure change로 다룬다.
- ASGI server 또는 access logger를 교체할 때는 middleware가 변경한 `scope["query_string"]`을 logger가 사용하는지 synthetic marker 통합 테스트로 다시 확인한다.
