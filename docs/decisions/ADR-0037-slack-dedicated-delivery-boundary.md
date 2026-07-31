# ADR-0037: Slack 전용 전달과 외부 부수효과 경계

Status: Accepted
Related ADRs: ADR-0019, ADR-0035

## 배경

ADR-0035와 MBA-190은 기존 `slackPostNode`의 Generic HTTP 동작을 변경하지 않은 채 모든 Slack 요청을 보수적인 `slack.http.request.v1` profile로 durable external-effect 경계에 연결했다. 이 profile은 Slack 응답 본문을 해석하지 않으므로 HTTP `200`의 `ok=false`도 성공으로 처리하고, 성공 결과를 안전하게 재사용하지 못하며, Generic HTTP URL/header/body 편집 기능과 raw `data`/`headers` output도 유지한다.

MBA-218은 다음 현재 제품 계약을 확정해야 한다.

- Slack Web API와 Incoming Webhook 응답을 각 provider 계약에 맞게 판정한다.
- 같은 logical execution이 재진입해도 이미 성공한 메시지를 다시 보내지 않는다.
- provider 결과가 불명확한 경우 중복 위험이 있는 자동 replay를 하지 않는다.
- Slack token, webhook URL, message와 provider 원문을 log/trace/public graph에 남기지 않는다.
- 기존 graph를 조용히 변환하지 않으면서 제거된 output 의존성을 배포와 runtime에서 차단한다.

## 결정

### 1. Slack은 전용 node와 두 개의 operation을 사용한다

`slackPostNode`는 `HttpRequestNode`를 재사용하지 않고 전용 data/node/adapter를 사용한다.

| mode | provider operation | contract version |
| --- | --- | --- |
| Slack Web API | `slack.chat.post_message` | `slack.chat.post_message.v1` |
| Incoming Webhook | `slack.incoming_webhook.post` | `slack.incoming_webhook.post.v1` |

두 profile 모두 Slack이 Nodease idempotency key 계약을 제공하지 않으므로 provider replay capability는 `unknown`이고 시스템 key를 생성하거나 전달하지 않는다. 기존 `slack.http.request.v1`은 이미 생성된 attempt와 과거 계약 해석을 위해 immutable history로 등록 상태를 유지하며 새 전용 node가 선택하지 않는다.

같은 stable effect slot에서 API와 Webhook mode 또는 과거 operation이 바뀌면 새 row를 만들지 않고 `external_effect.identity_conflict`로 종료한다.

### 2. 성공한 안전 결과만 로컬에서 재사용한다

두 전용 profile의 result reuse capability는 `supported`다. 최초 성공 때 다음 allowlist projection만 `workflow_node_effect_attempts.replay_result`에 저장한다.

- 공통: `status=200`, `delivery_status=delivered`, `delivery_mode`
- Web API 전용: 형식을 검증한 `message_ref`

동일 execution slot이 다시 전달되면 provider를 호출하지 않고 이 projection을 반환한다. Slack raw response, header, token, webhook URL, channel, message 또는 blocks는 replay result에 저장하지 않는다.

### 3. Slack 실패는 자동 재전송하지 않는다

Slack adapter가 provider 응답과 transport phase를 판정하고 공통 `ExternalEffectExecutor`는 그 typed outcome만 처리한다.

- Web API 성공은 HTTP `200`, JSON media type, `ok=true`, 유효한 `ts`를 모두 요구한다.
- Incoming Webhook 성공은 HTTP `200`과 정확한 `ok` 응답을 요구한다.
- 공식 의미가 확인된 인증·권한·payload rejection과 `429`는 `failed_before_effect + stop`으로 기록한다.
- `fatal_error`, `internal_error`, 미확인 `ok=false`, malformed success, redirect, write/read timeout, 응답 유실, `5xx`와 응답 이후 peer 검증 실패는 `effect_outcome_unknown + stop`으로 기록한다.
- DNS/egress/local validation과 connect 실패도 이번 계약에서는 자동 replay하지 않고 `failed_before_effect + stop`으로 종료한다.
- `Retry-After`는 하나의 bounded decimal seconds 값일 때만 운영 hint로 trace에 남기며 Celery 재시도 지시로 사용하지 않는다.

이 정책은 Slack provider exactly-once를 주장하지 않는다. 성공 결과가 durable terminal commit되기 전에 Worker가 종료되는 창에서는 결과를 알 수 없으므로 중복 가능성이 있는 replay보다 보수적인 중단을 선택한다.

### 4. 템플릿과 graph 호환성은 fail-closed한다

Slack template은 등록된 `{{name}}` 단순 치환만 허용한다. 실제 Slack 필드에서 사용된 변수만 selector로 해석하며, 미사용 reference의 누락 input은 실행을 막지 않는다. `blocks`와 `attachments` JSON 문자열 안의 값은 JSON 문맥에 맞게 escape한 뒤 strict parse하고, expression/filter/attribute/statement/control flow와 동적 JSON key는 provider 호출 전에 거부한다.

Draft backend는 명시 migration을 위해 과거 selector를 보존할 수 있지만 Client validation, deployment와 legacy active snapshot runtime은 다음 의존성을 오류로 차단한다.

- Slack의 제거된 `data`, `headers` output
- Webhook mode에서 제공하지 않는 `message_ref`

기존 HTTP-shaped field는 migration 표시와 호환 검증에만 사용하고 endpoint/header/body/timeout request source로 사용하지 않는다. API mode는 고정 `chat.postMessage` endpoint, token과 channel을 사용하고 Webhook mode는 정확한 commercial Slack webhook URL만 허용한다.

### 5. MBA-190 공통 실행·추적 경계를 재사용한다

별도 execution-local observer나 Slack 전용 operation hash/ledger를 추가하지 않는다. `Node._run_external_effect()`와 ADR-0035의 stable slot, durable claim, generation fencing, terminal replay decision을 그대로 사용한다. 따라서 Loop의 `error_strategy=continue`, nested Workflow와 Celery task 경계도 공통 `ExternalEffectError` 전파 규칙을 따른다.

Slack adapter는 중앙 `OutboundEgressGuard`를 사용하고 commercial Web API 고정 URL 또는 정확한 incoming webhook URL, HTTPS 443, POST만 허용한다. Proxy 환경변수를 신뢰하지 않고 TLS 검증을 켜며 redirect, compressed response, 과대 request/response와 private peer를 차단한다. GovSlack은 이번 profile에서 추정 지원하지 않으며 별도 공식 endpoint/profile 결정 전에는 허용하지 않는다.

Trace에는 provider/operation, mode, safe delivery status/reason, HTTP status, request/response size, latency, bounded retry hint, outcome/replay decision과 allowlist error code만 남긴다. `message_ref`는 존재 여부만 남기며 원문은 trace에 저장하지 않는다. 공개 graph projection은 최상위와 중첩 graph의 token과 webhook URL을 제거한다.

## 검토한 대안

### 기존 Generic HTTP Slack runtime에 응답 parser만 추가

Raw output과 사용자 지정 endpoint/header/body capability가 계속 남고 API/Webhook operation identity와 안전한 replay projection을 분리할 수 없어 채택하지 않았다.

### 모든 pre-effect 및 429 실패를 자동 재시도

Slack provider idempotency key가 없고 request byte 미전송 증명과 task 재진입 경계가 항상 일치하지 않는다. 데모 편의보다 중복 메시지 방지를 우선해 자동 replay를 허용하지 않았다.

### 별도 Slack delivery ledger와 observer 유지

ADR-0035의 stable slot/claim/replay/error 전파와 책임이 중복되고 두 ledger의 원자성 문제가 생긴다. 공통 executor adapter로 통합했다.

## 결과

- HTTP `200`의 Slack logical failure를 성공으로 오인하지 않는다.
- 동일 execution의 성공 재전달은 Slack을 다시 호출하지 않고 같은 안전 output을 반환한다.
- 결과 불명 요청과 `429`는 자동 재전송하지 않는다.
- 새 Slack graph는 Generic HTTP raw output과 arbitrary request 설정에 의존하지 않는다.
- 과거 `slack.http.request.v1` attempt는 해석 가능하지만 새 전용 operation과 섞이지 않는다.
- Slack secret과 provider payload는 public graph, durable trace와 일반 log에 남지 않는다.

## 후속 검토

- Slack credential reference와 rotation/revoke lifecycle은 별도 credential 도메인 결정이 필요하다.
- GovSlack 지원은 공식 API/Webhook endpoint와 tenant isolation 근거를 갖춘 별도 profile로 검토한다.
- 실제 Slack sandbox를 사용하는 smoke test는 운영 secret 주입이 가능한 별도 E2E 환경에서 수행한다.
