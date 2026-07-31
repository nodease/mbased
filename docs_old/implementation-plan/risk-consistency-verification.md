# 리스크, 정합성 및 검증

Status: Draft
Authority: Implementation Plan
Source of Truth: Yes
Verified Against: dev @ ec576b4f24155697aed8843acc6e5a3fc835f7e1

## 점검 범위

점검 대상:

- `requirements`

기준 문서:

- 기존 Moduly 명세: `references/moduly-architecture`
- Nodease 방향 메모: 삭제된 로컬 참고 자료이며, 구현 기준은 active requirements/data-model/API 문서를 따른다.

점검 관점:

- 문서 간 모순
- 기준 문서와의 불일치
- 구현 기반을 과장한 표현
- 모호한 용어와 범위
- 병합 과정에서 누락될 수 있는 원문 항목

## 주요 리스크

| 리스크 | 영향 | 대응 |
| --- | --- | --- |
| RBAC를 나중에 붙이면 API/실행 경로를 대거 수정해야 함 | 구조 재작업 | MVP 1에서 resource/permission/checker 먼저 설계 |
| 과거 `llm_usage_logs.atency_ms` 컬럼 오타 | 집계/조회 오류 | 현재 코드의 `f8a9b0c1d2e3_rename_llm_usage_latency_ms.py` migration과 `LLMUsageLog.latency_ms` 모델/test 기준으로 정리됨. 새 작업은 `latency_ms`만 사용 |
| trigger mode 로그 매핑이 부정확함 | audit 신뢰도 저하 | MVP 3 전 반드시 수정 |
| `create_all`과 Alembic 혼재 | 운영 schema 불안정 | 현재 Docker Gateway entrypoint는 `alembic upgrade head`를 실행하고, Gateway lifespan도 `Base.metadata.create_all()`을 수행한다. MVP 2부터가 아니라 현재 운영 schema 전략 리스크로 추적하며, migration-only 원칙 또는 create_all의 개발 전용화 여부를 결정해야 한다. |
| RAG source별 변경 감지 수준이 다름 | partial re-index 품질 편차 | MVP 2는 FILE/hash 중심으로 시작 |
| quality score 정의가 모호함 | 추천 신뢰도 저하 | MVP 1~3 모두 rule-based/사용자 평가로 제한 |
| RBAC 범위 과확장 | 일정 지연 | 프로젝트/캔버스/데이터 소스/모델 중심 |
| audit log에 민감 데이터 저장 | 보안 리스크 | snapshot/redaction/hash 정책 적용 |

## 공통 품질 게이트

각 MVP는 다음을 만족해야 한다.

- 기존 workflow 생성/저장/실행이 깨지지 않는다.
- 최소 1개의 LLM node 포함 workflow가 끝까지 실행된다.
- 권한 없는 접근은 API와 실행 경로에서 차단된다.
- 새 DB 모델은 migration 또는 명확한 schema 생성 전략을 가진다.
- Client build가 통과한다.
- Gateway/Workflow Engine 핵심 테스트가 통과한다.
- demo fixture로 주요 시나리오를 재현할 수 있다.

## 단계별 검증

| 단계 | 핵심 검증 |
| --- | --- |
| MVP 1 | `builder`/`viewer` 권한, LLM trace, node별 비용/토큰/latency |
| MVP 2 | data source 권한 차단, RAG chunk trace, audit log 검색 |
| MVP 3 | deploy checklist, version diff, cost recommendation, dashboard aggregation |

## 권장 테스트

```bash
./scripts/test.sh
```

추가 권장:

```bash
cd apps/client
npm run build
```

```bash
cd apps/gateway
.venv/bin/python -m pytest tests
```

```bash
cd apps/workflow_engine
.venv/bin/python -m pytest tests
```

## 명시적으로 제외할 것

- 완전한 컴플라이언스 인증
- 노드 단위 세밀 RBAC 전체 구현
- 복잡한 OIDC/IdP 연동
- 모든 데이터 소스에 대한 CDC 실시간 동기화
- 완전 자동 품질 평가 시스템
- MCP Gateway 전체 제품화
- 대규모 멀티테넌트 과금 시스템

## 수정한 불일치와 병합 반영

| 항목 | 문제 | 병합 후 처리 |
| --- | --- | --- |
| Permission vocabulary | `deploy` 권한이 일부 문서에는 빠지고 일부 문서에는 등장 | `read/write/execute/use/manage/deploy`로 통일 |
| Project/Canvas 용어 | Nodease 방향 문서의 프로젝트/캔버스가 Moduly 모델에 어떻게 대응되는지 불명확 | `Project=App`, `Canvas=Workflow`로 MVP 1 기준 명시 |
| MVP 1 RBAC 범위 | 일부 문서는 LLM 사용 차단만 말하고, 일부 문서는 workflow 권한만 말함 | MVP 1에 credential `use` 권한과 credential-model relation 기반 LLM 사용 차단을 포함 |
| Audit action schema | `event_type`과 `action`이 중복될 수 있었음 | 현재 코드에 맞춰 `audit_logs.action`을 canonical action으로 사용하고 정책 결과는 `audit_logs.audit_metadata.policy_result`로 이동 |
| Actor 권한 snapshot | 한 사용자가 여러 team/user direct grant를 가질 수 있음 | `audit_metadata.effective_permission`, `source_team_ids`, `user_direct_permission_id` 같은 metadata로 명확화 |
| RAG re-index | `needs_reindex`가 문서 status처럼 보일 수 있었음 | 기존 status를 바꾸지 않고 `meta_info.needs_reindex` 같은 metadata flag로 명시 |
| Partial re-index | chunk-level incremental indexing으로 오해될 수 있었음 | MVP 2에서는 변경 문서 단위 재색인으로 제한 |
| MVP 3 기존 기반 | `audit_events`, `RAG traces`를 기존 Moduly 기반처럼 표현 | `audit_logs`와 RAG trace metadata 의존으로 수정 |
| Deployment rollback | 기존 Moduly에 명시적 rollback API는 없지만 `toggle`로 이전 deployment를 다시 활성화할 수 있음 | 별도 rollback 권한은 두지 않고 workflow `deploy/manage`로 다루며, 이전 deployment 활성화 이벤트는 `deployment.activate_previous`로 기록 |
| Deployment secret 응답 | 보안 원칙은 secret 원문 비노출이지만 현재 생성 응답은 `auth_secret`을 포함할 수 있음 | 현재 코드 현실은 API 문서에 명시하고, masking/removal을 보안 보강 후보로 둔다. |
| `system admin` provider | trace/LLM pricing 관리 API는 system admin을 요구하지만 기본 Trace RBAC provider는 deny-all | 운영 환경에서 provider 설정 전까지 policy/pricing 관리 API가 `403 system_admin_required`로 차단됨을 문서화하고, provider 연결을 별도 작업으로 추적 |

## 기준 문서와 정합성이 확인된 항목

| 항목 | 근거 |
| --- | --- |
| App을 project boundary로 쓰는 계획 | `references/moduly-architecture/01-project-overview.md`의 App 중심 구조와 `workflow_id`, `active_deployment_id` |
| Workflow를 canvas로 보는 계획 | `references/moduly-architecture/sections/04-frontend-screens.md`의 `/modules/[id]` 워크플로우 편집기 |
| LLMOps 관측성 기반 | `workflow_runs`, `workflow_node_runs`, `llm_usage_logs`, `llm_models` 모델 |
| RAG audit 기반 | `knowledge_bases`, `documents`, `document_chunks`, `content_hash` |
| trigger mode 정합성 리스크 | `references/moduly-architecture/sections/03-runtime-architecture.md`, `06-data-model.md`, `07-workflow-engine.md`의 주의사항 |
| migration/create_all 리스크 | `references/moduly-architecture/sections/03-runtime-architecture.md`의 Gateway startup 흐름 |
| 노드 단위 RBAC 후순위 | 삭제된 Nodease 메모의 해석 점검 내용은 active requirements와 RBAC 정책 문서에 반영된 범위만 따른다. |

## 남은 모호성

아래는 문서상 의도적으로 남겨둔 결정 사항이다.

| 항목 | 현재 처리 |
| --- | --- |
| `Project` 독립 모델 도입 여부 | MVP 1에서는 App을 project boundary로 사용, 이후 확장 |
| 조직/그룹 모델 | 현재 코드의 organization/team을 사용하고, 별도 group/role table은 만들지 않음 |
| PII 탐지 수준 | MVP 2는 수동 classification과 간단한 regex 후보로 제한 |
| Quality score | MVP 1-3 모두 rule-based/사용자 평가 중심 |
| MCP Gateway | MVP 범위 밖, 아키텍처 경계 후속 결정 |
| CDC | MVP 2는 FILE/hash 중심, 고급 CDC는 후순위 |

## 병합 검증 매트릭스

| 원문 | 병합 위치 |
| --- | --- |
| `00-overview.md` | `requirements/overview.md` |
| `01-foundation-rbac-resource-model.md` | `requirements/mvp-1-foundation-llmops.md`, 배포 관련 항목은 `requirements/mvp-3-enterprise-ops.md` |
| `02-foundation-audit-tracing-model.md` | `requirements/mvp-1-foundation-llmops.md`, RAG/audit 검색은 `requirements/mvp-2-governance-rag-audit.md`, 배포/추천 event는 `requirements/mvp-3-enterprise-ops.md` |
| `03-foundation-data-governance-policy.md` | `requirements/mvp-1-foundation-llmops.md`, enforcement와 re-index는 `requirements/mvp-2-governance-rag-audit.md`, deploy checklist 위험 표시는 `requirements/mvp-3-enterprise-ops.md` |
| `04-mvp-1-scope.md` | `requirements/mvp-1-foundation-llmops.md` |
| `05-mvp-1-work-breakdown.md` | `requirements/mvp-1-foundation-llmops.md` |
| `06-mvp-2-scope.md` | `requirements/mvp-2-governance-rag-audit.md` |
| `07-mvp-2-work-breakdown.md` | `requirements/mvp-2-governance-rag-audit.md` |
| `08-mvp-3-scope.md` | `requirements/mvp-3-enterprise-ops.md` |
| `09-mvp-3-work-breakdown.md` | `requirements/mvp-3-enterprise-ops.md` |
| `10-risk-verification.md` | `implementation-plan/risk-consistency-verification.md` |
| `11-consistency-audit.md` | `implementation-plan/risk-consistency-verification.md` |

## 현재 결론

현재 `requirements`와 물리 데이터 모델 문서(`data-model/physical-data-model.md`, `data-model/rbac-permission-policy.md`)는 current/target 경계를 분리한다. 현재 코드는 organization/team permission과 구현된 workflow/LLM credential user direct permission을 기준으로 한다. MVP 2-0 목표 상태에서는 `organization_memberships`를 organization 소속의 전제 조건으로 추가하고, `team_memberships`는 team 배정 관계로 유지한다. `roles`, `user_roles`, polymorphic `resource_permissions`, `audit_events`, `rag_retrieval_traces`는 MVP 목표 상태에서 새로 만들지 않는다. audit은 `audit_logs`로, RAG trace는 `trace_payloads`/metadata로 처리한다.
