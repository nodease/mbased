# Agent Builder Protected Resource Completion Matrix

Status: Verification Blocked

Verified Against: feature/mba-277 @ 9341ed3d

Working Tree Base: feature/mba-277 @ d8c75e79 (uncommitted changes; commit-pinned verification unavailable)

이 문서는 MBA-331의 보호 리소스 기능 완결성 기준을 Agent Builder direct-edit 변경에 적용한 PR-visible 증거다. 상세 실행 이력은 로컬 작업 기록과 분리하며, 아래 행은 현재 계약·구현·검증 증거만 유지한다.

`122 passed`와 `121 passed` 증거는 위 historical commit에 고정한다. 이후 working tree correction은 별도 RED/GREEN 결과로 기록하며 commit 전에는 `Verified Against` SHA에 포함하지 않는다. 아래에서 역사적이라고 표시한 수치는 현재 완료 증거로 사용하지 않으며, 현재 작업 트리는 계속 `Verification Blocked`로 판정한다.

## 대상

| 항목 | 내용 |
| --- | --- |
| 기능/이슈 | MBA-277 Agent Builder direct-edit bug correction |
| 보호 리소스 | Knowledge Base, Knowledge Collection, Mail/Gmail credential reference, encrypted Slack/GitHub workflow-node secret revision |
| durable reference 위치 | Workflow graph node data의 opaque resource/credential/`workflow-node-secret://` reference, Agent Builder safe operation envelope, ParameterTask safe metadata |
| 실행 진입점 | Agent Builder message/Knowledge/parameter endpoints, workflow draft CAS save, deployment/test preflight, Workflow Engine runtime |
| 외부 I/O | Agent Builder 생성 중 없음. 저장된 workflow 실행 시 Knowledge retrieval과 provider adapter가 수행함 |
| 권위 문서 | ADR-0045, ADR-0046, ADR-0061, ADR-0062, `docs/features/agent-builder/{requirements,api_spec,component_spec,test_cases}.md` |

## 경계 상태

| 경계 | 상태 | 계약 증거 | 구현 위치 | 검증 증거 | 해당 없음 사유 또는 남은 검증 |
| --- | --- | --- | --- | --- | --- |
| 정책·식별자·organization scope | 완료 | ADR-0045/0061 Knowledge handle과 ADR-0062 workflow/node/type/key-scoped secret reference | `KnowledgeSelectionService`, `KnowledgeCandidateResolver`, `WorkflowNodeSecretService`, workflow permission helpers | 현재 작업 트리의 exact organization/workflow/node/type/key/status 단위 테스트와 PostgreSQL draft-save 거부 테스트 통과 | 인증 browser에서 organization 전환 UX 미검증 |
| 관리 API command/query | 완료 | ADR-0062 authenticated secret-write command, read-back 원문 금지 | `POST /api/v1/workflows/{workflow_id}/node-secrets`, `WorkflowService.store_node_secret` | Gateway/shared secret service 테스트에서 active organization 전달과 row-lock 재검증 통과 | 인증 browser에서 403/404/422 안내 UX 미검증 |
| 관리 UI·catalog·picker | 완료 | ADR-0045 typed ParameterTask, ADR-0061 hierarchy-only Knowledge card, ADR-0062 masked input과 `나중에 설정` 유지 | `KnowledgeSelectionControl`, `ParameterInputRenderer`, `AgentBuilderPanel`, Slack/GitHub Node Detail | Client full 139 files / 1160 passed / 1 skipped; Node Detail late-response 2 files / 9 passed | 인증된 실제 browser smoke는 미실행 |
| 저장 schema·GraphMutation·redaction | 완료 | ADR-0062 encrypted immutable revision과 graph opaque reference-only 계약 | `workflow_node_secrets`, secret service, draft/deployment persistence guards와 response redaction | Shared full 1173 passed/32 skipped, focused Gateway/shared 103 passed, PostgreSQL CAS file 18 passed | 인증 browser network response 미검증 |
| Deployment/test preflight | 완료 | Catalog 전체 required configuration과 selector validity를 같은 의미로 검사 | `workflow_configuration_preflight.py`, `workflow_node_catalog.py` | 이번 correction은 preflight 계약을 변경하지 않음; 517 passed는 이전 변경의 역사적 결과로만 유지 |  |
| Runtime/background 재검증 또는 capability validity | 완료 | Catalog/runtime parameter parity, unresolved 실행 차단, ADR-0062 provider I/O 직전 scope/status/key-version 검증 | Slack/GitHub Workflow Engine nodes, `resolve_runtime_workflow_node_secret` | Workflow Engine GitHub/Slack opaque-reference runtime 집중 테스트 2 passed | 전체 Workflow Engine 회귀는 원격 CI에 위임 |
| Transaction·session·TOCTOU | 완료 | ADR-0046 graph hash와 `updated_at` CAS, Knowledge handle 및 secret reference 제출 시 권한·lifecycle 재검증 | workflow draft save service, `KnowledgeSelectionService`, `WorkflowNodeSecretService` | 실제 PostgreSQL에서 draft read migration 대 concurrent CAS 및 unknown reference save 거부를 포함한 CAS file 18 passed | 전체 PostgreSQL suite는 원격 CI에 위임 |
| Retry·idempotency·terminal acknowledgement | Verification Blocked | ADR-0046 operation id, task version, canonical acknowledgement/reconciliation | parameter decision service, mutation lifecycle, frontend save coordinator | Current working tree related suite 296 passed; legacy completed group reconciliation 1 passed | Current revision acknowledgement 재계획 PostgreSQL 미실행 |
| Background lease·claim·fencing | 해당 없음 | Agent Builder direct-edit 요청은 background lease/claim을 도입하지 않음 | 해당 없음 | 해당 없음 | Workflow runtime worker lease 정책은 변경하지 않음 |
| Revoke/delete/expire/rotation lifecycle | Verification Blocked | Knowledge는 선택/실행 전 lifecycle 재검증, secret 교체는 immutable 새 revision이며 기존 deployment ref를 변경하지 않음 | Knowledge resolvers, `WorkflowNodeSecret.status`, workflow cascade | Unit scope/status mismatch tests | Secret revoke command는 이번 범위가 아니며 PostgreSQL cascade/legacy deployment 실행 검증이 남음 |
| 오류·resource hiding·reason code | 완료 | safe 403/404/409/422와 stale selection 계약 | Agent Builder endpoints/services | API/service negative tests |  |
| Audit event 생성·action/status·중복 방지 | 완료 | GraphMutation/decision idempotency와 secret-write request body 비수집 | parameter task service, mutation lifecycle, Workflow update audit decorator | duplicate decision/audit와 secret schema redaction tests | Secret 원문은 audit metadata 대상이 아님 |
| Audit·trace·secret/PII redaction | 완료 | planner/chat/task/session/graph response/audit/trace/log에 raw secret 또는 ciphertext 금지 | secret decision rejection, draft/deployment response redaction, safe errors | secret persistence/redaction tests | 인증 browser network/console smoke 미실행 |
| Legacy migration·scrub·호환성 종료 | Verification Blocked | legacy Preview는 `stale_protocol`; legacy plaintext draft/deployment는 bounded read/execution 경계에서 encrypted reference로 변환 | Alembic `f5b6c7d8e9fa`, draft/deployment lazy migration, response fail-safe redaction | Migration head/unit tests 통과 | 실제 PostgreSQL legacy row migration과 deployment execution 미실행 |
| 공식 문서 정합성 | 완료 | ADR-0045/0046/0061/0062와 Agent Builder feature 문서 | 이 매트릭스와 권위 문서 | secret task/save-bridge 충돌 문구 제거 | Current working tree이므로 commit-pinned 증거 아님 |

## 현재 변경의 소비 경계 추적

| 계약 단위 | Schema/Catalog | 저장 정규화 | UI/ParameterTask | GraphMutation/CAS | Preflight/runtime | 검증 |
| --- | --- | --- | --- | --- | --- | --- |
| Optional selector list | `variable_selector_list` | 신규 empty는 skip, 기존 값 전체 해제는 clear | checkbox selection과 clear action 분리 | clear만 parameter mutation/CAS 수행 | stored selector 정규화 후 source/output 검증 | frontend focused suite, backend/shared focused suite |
| File Extraction selector | Catalog `variable_selector` | stored `referenced_variables`를 canonical selector로 변환 | server-issued selector만 제출 | canonical graph mapping 유지 | 같은 normalized value로 preflight 검증 | `test_agent_builder_unresolved_preflight.py` |
| Collection/KB 선택 | opaque handle과 editor resource ID 경로 분리 | 추천 탐색은 5,000개 상한을 유지하고, 발급 resolution은 server-only handle-to-resource binding을 보존해 제출된 최대 20개 resource만 재검증 | Collection/child 선택 상태와 payload 의미 분리 | `knowledge_binding` CAS/acknowledgement | runtime KB union/dedup과 stale resource 차단 | Current revision Knowledge service tests; PostgreSQL cap test blocked |
| Slack/GitHub secret | Catalog `secret` + mode별 `visible_when|required_when`; opaque ref schema | 원문을 전용 command에서 검증·암호화하고 graph에는 reference만 저장; 신규 raw save 거부 | masked password input과 `나중에 설정` 유지, Node Detail 강제 이동 없음, 늦은 응답 격리 | Agent Builder decision/GraphMutation/session에는 raw value 없음; secret-write 뒤 reference만 autosync/CAS | 미설정 node는 preflight 차단, 유효한 opaque reference는 Catalog/preflight에서 허용, 설정 node는 provider I/O 직전 scope/status/key-version 재검증 | Client full 1160 passed, Gateway 1627 passed/64 skipped, Root 162 passed/1 skipped, Shared 1165 passed/32 skipped; PostgreSQL/browser/Slack runtime blocked |
| Workflow save coordination | canonical hash와 `updated_at` | 모든 save owner를 workflow별 직렬화 | pending/confirming 상태 보존 | CAS/acknowledgement 후에만 완료 | test 실행은 저장 완료 전 차단 | Frontend focused test 통과; current revision PostgreSQL CAS blocked |
| Server-derived readiness와 version Note | `configuration_state`는 Catalog 파생 상태 | Client save/compare projection에서 제거하고 Server가 재계산 | version restore의 modern/legacy Note 출처를 구분 | 복원 payload와 editor Note 집합을 동일하게 유지 | canonical comparison에서 파생 상태 차이를 제외 | Mail Acknowledge materialize/runtime contract와 frontend focused test 통과 |
| Scoped deferred projection | `node_path[] + parameter_keys[]` | Gateway가 root 기준 path projection을 저장 응답에 계산 | exact active Workflow와 path가 일치하는 node에만 marker 반영 | 일반/Agent Builder save가 같은 projection 사용 | ParameterTask/audit 상태는 변경하지 않음 | frontend 8-file suite 258 passed와 CAS service tests 통과 |
| 늦은 저장 응답 격리 | 응답 `workflow_id` | 비활성 Workflow metadata/cache만 갱신 | `default`를 포함해 ID가 다른 현재 live graph, marker, dirty/history 보존 | 응답 도착 뒤 exact active identity 재검사 | 다른 Workflow의 autosync를 유발하지 않음 | A→B와 A→default deferred-promise 회귀 통과 |
| Knowledge hierarchy-only UI | `collections + ungrouped_kbs` | flat candidate는 읽기 호환 데이터로만 유지 | flat-only direct 응답은 오류와 제출 차단 | 전용 Knowledge endpoint만 유지 | planner/runtime 변경 없음 | component 회귀와 Knowledge service 회귀 통과 |
| Knowledge 후보 공유 예산 | ADR-0061 고유 KB 5,000 상한 | 적격 direct 결과 최대 20개를 bounded pagination으로 채운 뒤 실제 평가 수를 제외한 예산만 linked 후보에 사용 | 표시 상한 20은 scoring 뒤 적용 | 발급 handle 적용 계약은 변경 없음 | 권한 거부 후보는 readiness 조회 전에 제외하고 legacy chunk visibility는 허용 후보 page별 bulk 조회 | permission/recommendation/selection service tests와 bulk lookup 회귀 통과 |
| Workflow node secret ownership | `workflow-node-secret://<uuid>` 형식과 binding tuple | 저장·배포 전에 reference row를 한 번에 조회해 active organization/workflow/node id/type/key/status를 정확히 검증하고 모호한 중첩 identity를 차단 | masked input과 `나중에 설정`을 유지하고 node 복제 시 secret reference만 제거 | 일반 draft save도 active organization을 검사하고 ownership 실패를 422로 닫으며 legacy read migration은 locked 최신 graph만 변경 | runtime은 provider I/O 직전에 같은 binding을 다시 검증 | shared/gateway 103 passed, Shared full 1173 passed/32 skipped, PostgreSQL CAS 18 passed, frontend copy 90 passed, runtime 2 passed |

## 실행 결과

| 범위 | 명령 | 결과 |
| --- | --- | --- |
| Historical frontend focused | `npm run test -- app/features/workflow/components/agentBuilder/AgentBuilderPanel.test.tsx` | 이전 변경 1 file / 59 passed; 현재 working tree 증거로 사용하지 않음 |
| Historical backend/shared/runtime related | `.ignore/codex-py311-venv/Scripts/python.exe -m pytest <previous 13-file suite> -q` | 이전 변경 517 passed; 현재 working tree 증거로 사용하지 않음 |
| Current frontend focused | `npm run test -- app/features/workflow/store/useWorkflowStore.test.ts app/features/workflow/hooks/useAutoSync.test.ts app/features/workflow/components/agentBuilder/useAgentBuilderEditor.test.ts app/features/workflow/utils/workflowDraftSaveCoordinator.test.ts app/features/workflow/tests/test-sidebar-final-response-card.test.tsx app/features/workflow/components/agentBuilder/ParameterInputRenderer.test.tsx app/features/workflow/components/agentBuilder/AgentBuilderPanel.test.tsx app/features/workflow/components/agentBuilder/WorkflowResultGroup.routing.test.tsx` | 8 files / 258 passed |
| Current backend Knowledge/CAS/ParameterTask related | `.ignore/codex-py311-venv/Scripts/python.exe -m pytest apps/gateway/tests/services/test_agent_builder_workflow_cas.py apps/gateway/tests/services/test_knowledge_permission_phase2.py apps/gateway/tests/services/test_knowledge_rag_recommendation_service.py apps/gateway/tests/services/test_agent_builder_knowledge_selection.py apps/gateway/tests/services/test_agent_builder_parameter_tasks.py apps/shared/tests/test_workflow_node_catalog.py apps/gateway/tests/services/test_agent_builder_parameter_runtime_contract.py apps/gateway/tests/integration/test_agent_builder_unresolved_preflight.py -q -p no:cacheprovider` | 296 passed; 4 existing Pydantic deprecation warnings only |
| Current legacy completed group reconciliation | `.ignore/codex-py311-venv/Scripts/python.exe -m pytest apps/gateway/tests/services/test_agent_builder_service.py -q -k "direct_session_recovery_adds_catalog_tasks_missing_from_legacy_group" -p no:cacheprovider` | 1 passed / 153 deselected; 4 existing Pydantic deprecation warnings only |
| Working tree direct candidate pagination | `.ignore/codex-py311-venv/Scripts/python.exe -m pytest apps/gateway/tests/services/test_knowledge_permission_phase2.py::test_builder_hierarchy_pages_past_denied_direct_kbs_within_shared_budget -q -p no:cacheprovider`; related four-file suite | RED 1 failed at 20 evaluated; GREEN single test passed; related suite 122 passed with 3 existing Pydantic warnings |
| Working tree Client CI reproduction | `npm run lint`; `npm run typecheck`; `npm run test -- --changed=cebb34178c82830e02e3f5b3d2124821c3c1fd1f --passWithNoTests` | Initial test run 1 failed / 1137 passed; hierarchy empty-handle expectation correction 뒤 138 files / 1138 passed / 1 skipped; lint 0 errors / 248 warnings; typecheck passed |
| Current secret boundary Shared full | `.ignore/codex-py311-venv/Scripts/python.exe -m pytest -q -p no:cacheprovider apps/shared/tests` | 1165 passed / 32 skipped / 5 existing warnings |
| Current Gateway CI-equivalent | `.ignore/codex-py311-venv/Scripts/python.exe -m scripts.ci.select_pytest_targets --component gateway --base b759c838137a063200049d5f151392f9131ac747 --head dbf7830dffc2889c0087593ecca20111434e9ea2 --broad true --run-with .ignore/codex-py311-venv/Scripts/python.exe` | 1627 passed / 64 skipped / 14 existing warnings |
| Current Root CI-equivalent | `.ignore/codex-py311-venv/Scripts/python.exe -m scripts.ci.select_pytest_targets --component root --base b759c838137a063200049d5f151392f9131ac747 --head dbf7830dffc2889c0087593ecca20111434e9ea2 --broad true --run-with .ignore/codex-py311-venv/Scripts/python.exe` | 162 passed / 1 skipped / 2 existing warnings |
| Current Client full | `npm run test -- --changed=b759c838137a063200049d5f151392f9131ac747 --passWithNoTests`; `npm run typecheck`; `npm run lint`; `npm run build` | Windows npm이 changed option을 npm config warning으로 처리해 Vitest 전체 실행: 139 files / 1160 passed / 1 skipped; typecheck passed; lint 0 errors / 248 existing warnings; production build passed |
| Collection cap PostgreSQL | current revision disposable DB | 미실행; explicit disposable DB environment가 없음 |
| Workflow CAS PostgreSQL | current revision disposable DB | 미실행; explicit disposable DB environment가 없음 |
| Python lint | `uvx --from ruff==0.15.20 ruff check apps/gateway/services/knowledge_candidate_resolver.py apps/gateway/tests/services/test_knowledge_permission_phase2.py` | passed |
| Python/import and syntax | `.ignore/codex-py311-venv/Scripts/python.exe -c "from apps.gateway.services.knowledge_candidate_resolver import KnowledgeCandidateResolver; print('import ok')"`; `.ignore/codex-py311-venv/Scripts/python.exe -m py_compile apps/gateway/services/knowledge_candidate_resolver.py apps/gateway/tests/services/test_knowledge_permission_phase2.py` | passed |
| Catalog JSON | `Get-Content -Raw -Encoding UTF8 apps/shared/config/workflow_node_catalog.json \| ConvertFrom-Json \| Out-Null` | passed |
| Client static | `npm run typecheck`; `npm run lint`; `npm run build` | typecheck passed; full lint 0 errors / 248 existing warnings; production build passed |
| Git static | `git diff --check`; `git diff --name-status --diff-filter=D` | passed; whitespace errors 0, deleted files 0 |
| Authenticated browser | Agent Builder secret set/delete, Knowledge selection, save/acknowledgement recovery | 미실행; 인증 organization fixture 필요 |
| Working tree secret ownership focused | `.ignore\codex-py311-venv\Scripts\python.exe -m pytest -p no:cacheprovider apps/shared/tests/test_workflow_node_secret_service.py apps/gateway/tests/services/test_workflow_node_secret_service.py apps/gateway/tests/api/test_active_organization_app_workflow.py apps/gateway/tests/services/test_deployment_preflight.py -q` | 103 passed; active organization mismatch와 모호한 nested identity 회귀 포함 |
| Working tree secret ownership Shared full | `.ignore\codex-py311-venv\Scripts\python.exe -m pytest -p no:cacheprovider apps/shared/tests -q` | 1173 passed / 32 skipped |
| Working tree secret ownership PostgreSQL | `NODEASE_RUN_DISPOSABLE_DB_TEST=1`과 로컬 disposable DB 환경에서 `.ignore\codex-py311-venv\Scripts\python.exe -m pytest -p no:cacheprovider apps/gateway/tests/integration/test_agent_builder_workflow_cas.py -q` | 18 passed; legacy migration/CAS race와 unknown reference save 거부 포함 |
| Working tree secret copy frontend | `npm exec vitest run app/features/workflow/store/useWorkflowStore.test.ts` | 1 file / 90 passed |
| Working tree secret runtime | `.ignore\workflow-ci-venv\Scripts\python.exe -m pytest -p no:cacheprovider apps/workflow_engine/tests/nodes/test_github_node.py::test_get_pr_resolves_opaque_secret_reference_at_runtime apps/workflow_engine/tests/nodes/test_slack_post_node.py::test_node_resolves_opaque_secret_reference_only_at_runtime -q` | 2 passed |
| Working tree static | `npm run typecheck`; targeted ESLint; `uvx --from ruff==0.15.20 ruff check <changed Python files>`; `git diff --check` | passed; ESLint 0 errors / 6 existing warnings |

## 완료 Gate

- Current revision unit/component/service, TypeScript, lint와 static diff 검증은 통과했다.
- 현재 변경의 secret ownership과 migration/CAS PostgreSQL 집중 검증은 통과했다. Collection cap 전체 통합과 인증된 browser smoke를 실행하기 전까지 문서 전체 상태를 `Verification Blocked`로 유지한다.
- 인증된 browser smoke는 미실행 사실과 남은 위험을 PR에 기록한다. 이를 실행하지 않은 상태를 browser 검증 완료로 표시하지 않는다.
