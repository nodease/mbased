# Cost Optimizer 보호 리소스 테스트 매트릭스

Status: Draft

## 대상

| 항목 | 내용 |
| --- | --- |
| 기능/이슈 | FR-014 비활성 배포의 자동 최적화 대상 메타데이터 보존 |
| 보호 리소스 | deployment-scoped parameter optimization plan |
| durable reference 위치 | `deployment_parameter_optimization_plans.node_ids` |
| 실행 진입점 | deployment create, parameter optimization GET/PATCH |
| 외부 I/O | PostgreSQL |
| 권위 문서 | `requirements.md`, `api_spec.md`, `component_spec.md`, `test_cases.md` |

## 경계 상태

| 경계 | 상태 | 계약 증거 | 구현 위치 | 검증 증거 | 해당 없음 사유 또는 후속 이슈 |
| --- | --- | --- | --- | --- | --- |
| 정책·식별자·organization scope | 해당 없음 |  |  |  | 기존 workflow/deployment permission 경계를 변경하지 않고 새 resource reference를 받지 않음 |
| 관리 API command/query | 완료 | `api_spec.md` Deployment Parameter Optimization Contract | `apps/gateway/api/v1/endpoints/deployment.py`, `deployment_parameter_optimization_service.py` | `test_configure_for_deployment_preserves_targets_for_disabled_management` | 저장→상세 projection→활성 상태 전이를 service 경계에서 검증 |
| 관리 UI·catalog·picker | 완료 | `component_spec.md` FR-014 | `AutomaticOptimizationManagementModal.tsx` | `AutomaticOptimizationManagementModal.test.tsx` |  |
| 저장 schema·GraphMutation·redaction | 완료 | `api_spec.md` disabled plan 계약 | `deployment_parameter_optimization_service.py` | `test_configure_for_deployment_preserves_targets_for_disabled_management` | node id와 수치 설정만 저장하며 secret·payload는 저장하지 않음 |
| Deployment preflight | 해당 없음 |  |  |  | 비활성 메타데이터 보존은 기존 preflight 판정을 변경하지 않음 |
| Runtime/background 재검증 또는 capability validity | 해당 없음 |  |  |  | disabled plan은 runtime 수집·검증 대상이 아니며 기존 enabled gate를 유지함 |
| Transaction·session·TOCTOU | 해당 없음 |  |  |  | deployment 생성 transaction 안의 단일 plan write이며 새 외부 I/O가 없음 |
| Retry·idempotency·terminal acknowledgement | 해당 없음 |  |  |  | 외부 효과나 background command를 추가하지 않음 |
| Background lease·claim·fencing | 해당 없음 |  |  |  | background coordination을 변경하지 않음 |
| Revoke/delete/expire/rotation lifecycle | 완료 | `requirements.md` FR-014 | configure/update/disable plan service | disabled/no-LLM service tests | enabled/disabled 상태만 적용되며 delete/rotation 계약은 없음 |
| 오류·resource hiding·reason code | 완료 | `api_spec.md` fixed reason | `_target_node_ids()` | invalid/no-LLM service tests | 활성화 시 기존 safe reason을 유지함 |
| Audit event 생성·action/status·중복 방지 | 해당 없음 |  |  |  | 새 보안 mutation이나 외부 효과를 추가하지 않음 |
| Audit·trace·secret/PII redaction | 완료 | `api_spec.md` safe projection | summary projection | `test_configure_for_deployment_preserves_targets_for_disabled_management` | node id 외 raw prompt·credential·payload를 다루지 않음 |
| Legacy migration·scrub·호환성 종료 | 완료 | `api_spec.md` optional config 계약 | `config is None` 호환 분기 | 기존 deployment service 테스트 | config가 없는 과거 배포는 plan을 새로 만들지 않음 |
| 공식 문서 정합성 | 완료 | FR-014 문서 세트 | requirements/API/component/test 문서 | diff 및 문서 참조 검사 |  |

## 테스트 시나리오

| ID | 시나리오 | 기대 결과 | 권장 계층 | 증거/상태 |
| --- | --- | --- | --- | --- |
| STORE-01 | LLM node가 있는 배포를 자동 최적화 비활성 상태로 생성한다. | disabled plan이 전체 LLM node id와 기본 주기·예산을 보존한다. | service | `test_configure_for_deployment_preserves_targets_for_disabled_management` |
| LIFE-01 | LLM node가 없는 배포를 자동 최적화 비활성 상태로 생성한다. | 배포는 성공하고 빈 대상의 disabled plan을 만든다. | service | `test_disabled_deployment_without_llm_nodes_keeps_deployment_available` |
| LIFE-02 | disabled plan의 상세 projection을 읽고 활성화한다. | 실제 node id/count가 반환되고 snapshot의 LLM node를 대상으로 collecting 상태로 전환한다. | service | `test_configure_for_deployment_preserves_targets_for_disabled_management` |
