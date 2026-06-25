# Redaction 정책

## 목적

Trace에 입력, 출력, prompt, completion, HTTP payload, stdout, stderr, retrieved context, guardrail reason을 저장할 때 PII와 secret을 안전하게 처리하기 위한 정책을 정의한다.

## 원칙

- secret 원문은 trace, log, test output, Celery payload에 저장하지 않는다.
- PII는 관리자 정책에 따라 탐지하고 마스킹한다.
- raw payload와 redacted copy는 분리 저장한다.
- redacted copy는 기본 저장 대상이다.
- raw payload 저장은 기본 비활성이다.
- raw payload 저장은 암호화 경로가 준비된 경우에만 허용한다.
- raw payload 암호화 실패 시 원문 저장으로 fallback하지 않는다.
- prompt/completion 저장 여부는 정책으로 제어한다.
- redaction은 저장 전 적용한다.
- redaction 결과는 metadata로 남긴다.
- redaction 실패 시 raw 저장으로 fallback하지 않는다.

## 항상 마스킹할 secret 계열

- API key
- Bearer token
- OAuth token
- session token
- password
- Authorization header
- Cookie header
- Set-Cookie header
- X-API-Key header
- SSH private key
- database connection string
- cloud provider credential
- app `auth_secret`
- workflow `env_variables` 중 secret성 값
- LLM credential config value

## PII 후보

- 이메일 주소
- 전화번호
- 주민등록번호 또는 국가 식별번호 형태
- 신용카드 번호
- 계좌번호
- 주소
- 이름과 식별번호가 결합된 데이터
- IP 주소
- 위치 정보

## 정책 모델

테이블: `trace_redaction_policies`

필수 필드:

- `scope_type`
- `scope_id`
- `redaction_enabled`
- `raw_payload_storage_enabled`
- `prompt_completion_storage_enabled`
- `pii_detection_enabled`
- `store_redacted_copy_only`
- `sensitive_headers`
- `sensitive_json_paths`
- `sensitive_keywords`
- `regex_rules`
- `replacement`
- `is_active`
- `updated_by`

정책 우선순위:

1. app
2. organization
3. global

deny 정책은 allow 정책보다 우선한다.

## Redaction 처리 위치

민감정보는 Celery broker에도 남을 수 있으므로 가능한 한 Celery log task 전송 전에 redaction한다.

권장 위치:

- WorkflowLogger 호출 전 공통 redaction service
- 또는 `WorkflowLogger`에서 Celery task payload 생성 전

처리 순서:

```text
1. trace/app/workflow context로 redaction policy resolution
2. payload kind별 redaction rule 적용
3. secret detector 강제 적용
4. redacted payload 생성
5. raw 저장 허용 여부 확인
6. raw 저장 허용 시 encryption service로 암호화
7. trace_payloads 저장 payload 생성
8. Celery log task에는 raw plaintext를 넣지 않음
```

## 저장 정책

- 기존 `workflow_runs.inputs`, `workflow_runs.outputs`, `workflow_node_runs.inputs`, `workflow_node_runs.outputs`에는 redacted copy만 저장한다.
- `trace_payloads.redacted_payload`에는 redacted copy를 저장한다.
- `trace_payloads.raw_payload_encrypted`에는 암호화된 raw payload만 저장할 수 있다.
- raw storage가 비활성인 경우 `raw_payload_encrypted`는 null이다.
- raw encryption service가 없으면 raw payload는 저장하지 않는다.
- raw encryption service가 있지만 encryption에 실패하면 raw payload는 저장하지 않고 전용 로그/메트릭을 남긴다.
- secret이 탐지된 field는 raw storage가 활성이어도 raw에 포함하지 않는다.
- prompt/completion 저장이 비활성인 경우 payload metadata만 저장한다.

## Prompt/Completion 저장 기준

- prompt payload는 provider 호출 직전 rendered messages 기준으로 저장한다.
- system, developer, user, assistant role 정보는 metadata로 유지할 수 있다.
- system safety prompt도 redaction 대상이다.
- raw prompt 저장은 기본 비활성이다.
- completion payload는 provider 응답 text 기준으로 저장한다.
- tool call, function call, structured output은 provider 응답 payload에서 민감정보를 제거한 뒤 redacted payload로 저장한다.

## Redaction 결과 metadata

```json
{
  "redaction": {
    "applied": true,
    "pii_detected": true,
    "secret_detected": false,
    "policy_id": "policy-id",
    "fields": ["inputs.email", "outputs.phone"],
    "rule_ids": ["email", "phone"],
    "detector_types": ["regex"]
  }
}
```

값이 아니라 경로와 rule id만 저장한다.

## Header 마스킹 기본값 후보

- `authorization`
- `cookie`
- `set-cookie`
- `x-api-key`
- `x-auth-token`
- `x-webhook-secret`
- `proxy-authorization`

대소문자는 구분하지 않는다.

## Guardrail reason

Moduly Guardrail node의 차단 사유는 다음 기준으로 저장한다.

- `reason_code`: 저장 가능
- `reason_redacted`: 저장 가능
- 차단 대상 원문: raw 저장 금지
- rule detail 중 민감 데이터 값: 저장 금지

## 테스트 기준

- 이메일이 마스킹된다.
- 전화번호가 마스킹된다.
- Authorization header가 저장되지 않는다.
- Cookie header가 저장되지 않는다.
- nested JSON path가 마스킹된다.
- prompt/completion에도 동일 정책이 적용된다.
- redaction metadata가 저장된다.
- 정책이 꺼진 경우에도 secret 계열은 원문 저장되지 않는다.
- raw storage가 꺼져 있으면 raw payload가 저장되지 않는다.
- raw storage가 켜져 있어도 secret field는 raw payload에 포함되지 않는다.
