# Trace 접근 제어 정책

## 목적

Trace 조회 권한을 역할, app ownership, system admin visibility policy에 따라 제어하는 방식을 정의한다.

## 역할

### System admin

- 전체 trace metadata 조회 가능
- 전체 app/workflow trace 조회 가능
- app owner의 trace 조회 권한 허용 또는 차단 가능
- raw inputs/outputs 조회 허용 또는 차단 가능
- prompt/completion 조회 허용 또는 차단 가능
- redaction policy 설정 가능
- retention policy 설정 가능
- visibility policy 설정 가능

### App owner

- 기본적으로 본인 app/workflow trace metadata 조회 가능
- system admin 정책에 의해 trace 조회가 차단될 수 있음
- redacted payload 조회는 별도 정책에 따름
- raw inputs/outputs 조회는 별도 정책에 따름
- prompt/completion 조회는 별도 정책에 따름

### Regular user

- 기본적으로 trace 조회 불가
- 별도 역할 또는 권한이 있을 때만 조회 가능

## 핵심 원칙

- App ownership alone does not guarantee unrestricted trace access.
- System admin policy can restrict app owner visibility.
- Raw payload access requires stronger permission than metadata access.
- Prompt/completion access can be restricted separately from generic input/output access.
- raw view 권한이 없으면 기본적으로 403을 반환한다.
- 자동 redacted downgrade는 명시 정책이 있을 때만 허용한다.
- raw payload 조회 시도는 허용/차단 모두 기록한다.

## App owner 판별 계약

`workflow_runs.user_id`를 app owner 판별에 사용하지 않는다.

신규 run 기본 경로:

```text
workflow_runs.app_id
  -> apps.id
  -> apps.created_by
```

과거 데이터 fallback 경로:

```text
workflow_runs.workflow_id
  -> workflows.id
  -> workflows.app_id
  -> apps.id
  -> apps.created_by
```

배포 실행 보조 검증 경로:

```text
workflow_runs.deployment_id
  -> workflow_deployments.id
  -> workflow_deployments.app_id
  -> apps.id
```

해석:

- `workflow_runs.user_id`는 실행자다.
- `apps.created_by`는 1차 구현의 기본 app owner다.
- organization/team permission 기반 공동 소유권은 RBAC 연동 단계에서 추가한다.

## 정책 모델

테이블: `trace_visibility_policies`

필수 정책:

- `owner_trace_access_enabled`
- `owner_redacted_payload_access_enabled`
- `owner_raw_payload_access_enabled`
- `owner_prompt_completion_access_enabled`
- `admin_raw_payload_access_enabled`
- `admin_prompt_completion_access_enabled`
- `deny_owner_trace_access`
- `default_view_level`
- `scope_type`
- `scope_id`

정책 우선순위:

1. app
2. organization
3. global

deny 정책은 allow 정책보다 우선한다.

## Policy API 권한

1차 구현에서는 policy 변경 권한을 보수적으로 제한한다.

- global policy 조회/수정: system admin만 가능
- organization policy 조회/수정: system admin만 가능
- app policy 조회/수정: system admin만 가능
- app owner self-visibility 설정: 별도 요구가 확정될 때까지 비활성

이 정책은 app owner가 본인 trace를 볼 수 없게 system admin이 차단할 수 있어야 한다는 요구사항을 우선한다.

## 조회 수준

### Metadata view

민감 원문 없이 실행 구조만 보여준다.

metadata view에서는 `inputs`, `outputs`, `process_data`, `error_message`를 반환하지 않는다. `trace_metadata`도 scope별 allowlist sanitizer를 통과한 요약 필드만 반환한다.

### Redacted payload view

마스킹된 입력/출력/prompt/completion을 보여준다.

### Raw payload view

원문 입력/출력/prompt/completion을 보여준다. 강한 권한과 raw storage 활성화가 필요하다.

## 권한 결정 흐름

```text
1. 사용자 인증 확인
2. trace 조회
3. trace app_id 확인 또는 fallback join으로 app_id 유도
4. system admin 여부 확인
5. app owner 여부 확인
6. trace visibility policy resolution
7. 요청 view level 확인
8. payload kind별 permission 확인
9. raw view 요청이면 TracePayloadAccessEvent 기록
10. 응답 또는 403 반환
```

## RBAC 연계

Organization/team permission 모델은 다음 테이블을 기준으로 한다.

- `organization`
- `team_permission`
- `user_team_permissions`
- `workflow_team_permissions`

Tracing 1차 구현은 RBAC 모델을 직접 controller에서 조회하지 않는다. 접근 결정은 `TraceAccessService`와 `TraceRbacService` 경계에서 수행한다.

Tracing은 다음 interface만 기대한다.

- 현재 사용자가 system admin인지 확인
- 현재 사용자가 app owner인지 확인
- 현재 사용자가 특정 app/workflow trace를 조회할 수 있는지 확인
- 현재 사용자가 redacted payload를 조회할 수 있는지 확인
- 현재 사용자가 raw payload를 조회할 수 있는지 확인

1차 구현의 기본 app owner 판별은 `apps.created_by`를 사용한다. `team_permission.auth_state` 기반 공동 소유권과 payload view별 세부 권한은 후속 RBAC 연동에서 추가한다.

## Access event 기록 범위

1차 구현에서 `trace_payload_access_events`는 raw payload 조회 시도만 필수 기록한다.

- raw view 허용: 기록
- raw view 차단: 기록
- metadata view: 기록하지 않음
- redacted view: 기록하지 않음

metadata/redacted 조회 기록은 Audit 전체 시스템에서 담당한다.

사용자 삭제가 감사 이벤트 삭제로 이어지지 않도록 `actor_user_id`는 nullable로 유지하고, 상관분석이 필요한 경우 `TRACE_AUDIT_ACTOR_REF_SECRET` 기반 HMAC `actor_user_ref`를 저장한다. secret이 없으면 단순 hash로 fallback하지 않는다.

## 테스트 기준

- system admin은 전체 trace metadata를 조회할 수 있다.
- app owner는 본인 app trace metadata를 조회할 수 있다.
- app owner 조회가 정책으로 차단되면 접근할 수 없다.
- app owner 판별은 `workflow_runs.user_id`가 아니라 app 관계를 사용한다.
- app owner raw payload 조회가 정책으로 차단되면 403이다.
- regular user는 기본적으로 trace를 조회할 수 없다.
- redacted view에서는 secret이 반환되지 않는다.
- raw payload 조회 시도가 access event로 기록된다.
