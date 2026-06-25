# Tracing 문서

## 목적

이 디렉터리는 워크플로우 실행 Tracing의 요구사항, 설계, 정책, 구현 계약, 테스트 기준을 정리한다.

Tracing은 기존 workflow 실행 로그를 확장해 trace/span, redacted/raw payload 분리 저장, redaction, retention, visibility policy, 신규 trace API를 제공한다.

## 디렉터리 구조

```text
docs/
  data-model/
    system-data-model-design.md
  tracing/
    README.md
    requirements/
      tracing-requirements.md
    design/
      current-system.md
      trace-architecture.md
      data-model.md
      collection-flow.md
      api-design.md
    policies/
      policy-decisions.md
      redaction-policy.md
      retention-policy.md
      access-control-policy.md
    implementation/
      full-implementation-contract.md
      implementation-plan.md
      test-plan.md
```

## 읽는 순서

전체 맥락을 확인할 때는 다음 순서로 읽는다.

1. `../data-model/system-data-model-design.md`
2. `requirements/tracing-requirements.md`
3. `policies/policy-decisions.md`
4. `implementation/full-implementation-contract.md`
5. `design/current-system.md`
6. `design/trace-architecture.md`
7. `design/data-model.md`
8. `design/collection-flow.md`
9. `design/api-design.md`
10. `policies/redaction-policy.md`
11. `policies/retention-policy.md`
12. `policies/access-control-policy.md`
13. `implementation/implementation-plan.md`
14. `implementation/test-plan.md`

특정 작업별 권장 문서:

- 요구사항 확인: `requirements/tracing-requirements.md`
- 전체 데이터 모델 확인: `../data-model/system-data-model-design.md`
- 구현 중 흔들리면 안 되는 결정: `policies/policy-decisions.md`, `implementation/full-implementation-contract.md`
- 현재 코드 구조 분석: `design/current-system.md`
- 데이터 모델 및 마이그레이션: `design/data-model.md`, `implementation/implementation-plan.md`, `implementation/test-plan.md`
- 로그 수집 경로: `design/collection-flow.md`, `design/current-system.md`
- 민감정보 마스킹: `policies/redaction-policy.md`, `policies/policy-decisions.md`
- 보관/삭제 정책: `policies/retention-policy.md`
- 조회 권한: `policies/access-control-policy.md`
- API 설계: `design/api-design.md`, `policies/access-control-policy.md`
- 테스트 작성: `implementation/test-plan.md`

## 문서 역할

- `requirements/`: 무엇을 반드시 만족해야 하는가
- `design/`: 어떤 구조로 구현할 것인가
- `policies/`: 보안, 보관, 접근 제어의 기본 정책
- `implementation/`: 어떤 순서와 계약으로 구현하고 검증할 것인가

## 구현 원칙

- 먼저 관련 문서를 읽고 영향 파일과 구현 계획을 제시한다.
- `.env`, secret, credential 값을 출력하지 않는다.
- unrelated file을 수정하지 않는다.
- 큰 리팩터링보다 작은 단계별 변경을 우선한다.
- RBAC 기반 상세 권한 통합, Audit 전체 구현, 신규 관리자 UI 구현은 Tracing 1차 구현 범위와 분리한다.
- 코드 변경 후 가능한 테스트를 실행하고, 실행하지 못한 테스트는 이유를 기록한다.

## 현재 1차 구현 범위

1차 구현은 내부 DB 기반 backend workflow-level tracing을 가능한 한 완결된 형태로 구현한다.

포함:

- `WorkflowRun = trace`
- `WorkflowNodeRun = span`
- span duration 및 trace metadata 기반
- `workflow_runs.app_id` 기반 trace 조회 최적화
- 신규 trace router
- 백엔드 실행 데이터 수집
- LLM/RAG/HTTP/Sandbox metadata 수집 구조
- Moduly Guardrail node metadata 수집
- raw/redacted payload 분리 저장
- redaction service 및 policy model
- retention policy 및 purge 기반
- visibility policy 및 access event
- 기존 run detail API와의 호환

제외:

- 프론트엔드 상호작용 이벤트 추적
- 신규 관리자 UI
- OpenTelemetry/Jaeger 연동
- RBAC 기반 상세 권한 통합
- Audit 전체 구현
- 전역 AWS Bedrock Guardrail payload 내부 저장
