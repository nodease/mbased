# App Management Requirements

Status: Draft
Related Features: auth, organization, workflow, deployment, budget-management, audit-tracing, cost-optimizer, agent-builder, connectors

## Purpose

사용자가 접근 가능한 App을 찾고 운영 상태를 확인하며, 더 이상 필요 없는 App을 운영 이력 보존 정책을 깨뜨리지 않고 안전하게 삭제할 수 있게 한다.

이 feature의 범위에는 앱 탐색/마켓플레이스(`dashboard/explore` 화면, `apps.is_market`)와 앱 복제(`apps.forked_from`)를 포함한다. 마켓플레이스 공개 정책 고도화는 PRD 제외 범위이며, 여기서는 이미 구현된 탐색·복제 동작을 다룬다.

## User Stories

- Builder는 자신이 관리하는 App의 배포·비용·최근 실행 상태를 한 화면에서 확인할 수 있다.
- Manager는 실행 중인 작업이나 보존 이력을 손상시키지 않고 App과 편집·배포 설정을 삭제할 수 있다.

## Functional Requirements

- APP-REQ-010: `GET /apps`는 active organization context 기준으로 현재 사용자가 읽을 수 있는 App 목록을 반환한다. 각 App은 기본 정보, primary `workflow_id`, 활성 배포 요약, owner 표시명을 포함한다.
- APP-REQ-020: `GET /apps/operations`는 `/dashboard/mymodule`의 원천으로, 현재 사용자가 운영할 수 있는 App/Workflow row를 반환한다. 운영 가능 기준은 organization manager 또는 workflow `write` 이상 권한이다. Workflow `execute` 전용 사용자는 최종 사용자 실행 주체이므로 이 목록에 포함하지 않는다. 각 row는 App summary, workflow permission summary, deployment state, latest run state를 포함한다.
- APP-REQ-030: Budget Management 확장 시 `GET /apps`의 `AppResponse.budget_status`와 `GET /apps/operations`의 `row.app.budget_status`는 같은 안전 요약 shape를 사용한다. 계산 규칙, null 조건, 노출 금지 필드는 [budget-management requirements](../budget-management/requirements.md)의 BGT-REQ-011, BGT-REQ-022~023을 따른다. App의 primary workflow(`apps.workflow_id`)만 기준으로 하며, 같은 `app_id`의 과거/보조 Workflow row는 예산 상태 후보가 아니다. 사용량 합산은 실행 차단 판정과 동일하게 primary workflow id와 KST 월 경계 기준이며, `llm_usage_logs.organization_id`가 NULL인 기존 로그를 제외하지 않는다.
- APP-REQ-040: `GET /apps/operations`의 `row.app.operation_metrics`는 `/dashboard/mymodule`의 월 예상 비용, 전월 대비 증가 추세, 최적화 권장 표시의 원천이다. 계산은 App의 primary workflow(`apps.workflow_id`)에 연결된 `llm_usage_logs.total_cost`를 KST 달력 월 기준으로 grouped aggregate 한다. 당월 비용은 현재 KST 월 `[start, end)` 비용 합계이며, 월 예상 비용은 당월 경과 비율로 단순 projection 한다. 전월 비용이 0이거나 없으면 `trend_percent`는 null이다.
- APP-REQ-050: `DELETE /apps/{app_id}`는 App, 연결된 Workflow, Workflow permission, budget, deployment/schedule, active model routing policy와 App-owned LLM node version을 하나의 transaction에서 hard delete한다.
- APP-REQ-051: 삭제된 App/Workflow의 run·node run·trace, LLM usage, audit, Cost Optimizer, model routing history, Mail processing/draft effect, external effect attempt, schedule dispatch claim은 각 retention 정책까지 보존한다. 보존 row의 resource ID는 삭제 시 지우거나 null로 바꾸지 않는 immutable provenance다.
- APP-REQ-052: 삭제 use case는 App을 먼저 잠그고 연결 Workflow를 UUID 순서로 잠근 뒤 active organization scope와 manage 권한을 다시 검사한다. 새 run/schedule/Mail processing admission도 durable active 상태 commit 전에 같은 App→Workflow shared lifecycle lock과 resource 재검사를 사용한다. 경합 결과는 삭제 성공 또는 active blocker 409 중 하나이며, 어느 단계든 실패하면 전체 transaction을 rollback한다.
- APP-REQ-053: running run, nonterminal schedule claim, 진행 중이거나 결과 불명인 external effect, 완료되지 않았거나 결과 불명인 Mail processing/effect가 있으면 아무 row도 바꾸지 않고 `409 app.delete_in_progress`를 반환한다.
- APP-REQ-054: 연결 Workflow는 `apps.workflow_id`와 `workflows.app_id`의 합집합으로 찾는다. same-organization legacy Workflow는 최대 100개까지 정리하며, 상한 초과나 organization/연결 무결성 손상은 `409 app.delete_requires_repair`로 전체 rollback한다.
- APP-REQ-055: App이 없거나 active organization 밖이거나 이미 삭제됐으면 `404 resource.not_found`, 같은 organization 안에서 manage 권한만 없으면 `403 permission.denied`를 반환한다. 반복 DELETE는 `404 resource.not_found`다.
- APP-REQ-056: 성공한 삭제는 같은 transaction에 `app.delete`와 permission 삭제 audit를 기록한다. Audit 실패 시 삭제도 rollback하며, 성공 event는 commit 뒤에만 발행한다. Audit metadata에는 검증된 ID와 대상별 개수만 넣고 secret, graph, raw payload를 넣지 않는다.
- APP-REQ-057: 다른 Workflow graph의 WorkflowNode target reference는 자동 수정하지 않는다. Deployment preflight는 `workflow_node_target_unavailable`, runtime은 `workflow_node.target_unavailable`로 삭제된 target을 provider effect 전에 차단한다.

## Policies And Edge Cases

- `budget_status`는 사용률과 상태만 포함한다. 예산 금액, 당월 비용 원문, credential, raw payload, secret 값은 App Management 응답에 포함하지 않는다.
- `operation_metrics`는 `/apps/operations` 전용 운영 지표다. `budget_status` shape를 확장하지 않으며, `GET /apps` 응답에는 포함하지 않는다.
- `/dashboard/mymodule`은 운영 표면이다. 일반 사원처럼 배포된 workflow를 실행만 하는 사용자는 챗봇 배포 링크나 내부 실행 링크를 사용하며, 비용/최근 실행/최적화 같은 운영 지표를 보지 않는다.
- `budget_status`가 null이어도 App 접근 권한, 배포 상태, 실행 상태의 기존 응답 의미는 바뀌지 않는다.
- App의 `workflow_id`가 null이거나 primary workflow에 활성 예산이 없으면 `budget_status`는 null이다. 같은 `app_id`의 다른 Workflow row에 활성 예산이 있어도 이를 대체값으로 사용하지 않는다.
- App의 `workflow_id`가 null이거나 해당 workflow에 당월 사용 로그가 없으면 `operation_metrics`는 null 또는 0 비용 지표로 처리하며, 클라이언트는 더미 비용/추세를 만들지 않는다.
- App/operations 목록 조회와 예산 수정·비활성화·삭제가 경합해도 목록 API는 5xx 없이 완료되어야 한다. 예산 상태는 같은 조회 스냅샷 기준으로 일관되게 계산하고, 경합 결과 활성 예산을 찾을 수 없으면 null로 반환한다.
- 삭제는 Agent Builder session/draft의 자체 만료 정책을 바꾸지 않고 App/Workflow reference만 분리한다.
- usage, Mail, model routing history에 별도 purge 정책이 아직 없어도 App 삭제가 그 이력을 대신 지우지 않는다.
- DB 제약 이름, SQL, 내부 row 존재 여부는 삭제 오류 응답과 audit metadata에 노출하지 않는다.
- Schema migration을 service 변경보다 먼저 배포한다. Mixed-version 구간에는 App DELETE를 일시 차단하거나 upgraded Gateway로만 보내 기존 bulk delete가 새 FK 상태에서 실행되지 않게 한다.

## Open Questions

- MBA-87 범위의 삭제·보존 정책 결정은 [ADR-0045](../../decisions/ADR-0045-app-workflow-hard-delete-retention-boundary.md)으로 닫혔다. 도메인별 purge 기간 변경은 별도 요구사항으로 다룬다.
