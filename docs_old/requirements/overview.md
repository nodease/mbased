# Nodease 개발 계획 개요

Status: Draft
Authority: Requirements
Source of Truth: Yes
Verified Against: dev @ 860ece0dee7cab3925d27f30ea650baf0cb18b4e (PR #138 docs target, 2026-07-01 KST)
Related ADRs: [ADR-202607010220-rag-answer-trace-usage-correlation-boundary](../decisions/ADR-202607010220-rag-answer-trace-usage-correlation-boundary.md)

## 작성 기준

이 문서는 기존 `requirements`의 번호 문서 전체를 5개 문서로 병합한 최상위 개요다. 사용자는 "11개의 설계"라고 표현했지만, 병합 시점의 실제 번호 문서는 `00`부터 `11`까지 12개였으므로 데이터 유실을 막기 위해 12개 번호 문서 전체를 병합 대상으로 삼았다.

기준 문서:

- 기존 Moduly 명세: `references/moduly-architecture`
- Nodease 방향 메모: 삭제된 로컬 참고 자료이며, 구현 기준은 이 requirements 문서와 ADR을 따른다.

## 최종 문서 구조

요구사항 문서는 MVP 기준으로 읽히도록 재배치했다. Foundation 문서는 따로 남기지 않고, 각 MVP가 실제로 필요로 하는 설계 항목 안으로 흡수했다. 리스크와 정합성 검증 문서는 현재 [implementation-plan/risk-consistency-verification.md](../implementation-plan/risk-consistency-verification.md)에 둔다.

| 문서 | 병합된 원문 | 목적 |
| --- | --- | --- |
| `overview.md` | 기존 `00-overview.md`, README | 전체 방향, MVP 의존성, 핵심 설계 결정 |
| `mvp-1-foundation-llmops.md` | 기존 `01`, `02`, `03`, `04`, `05` | RBAC/resource/audit/policy 기반과 LLMOps 관측성 MVP |
| `mvp-2-governance-rag-audit.md` | 기존 `02`, `03`, `06`, `07` | 데이터 소스 권한, RAG trace metadata, 재색인, audit 검색 MVP |
| `mvp-3-enterprise-ops.md` | 기존 `01`, `02`, `03`, `08`, `09` | 배포 체크, version diff, 비용 추천, 운영 대시보드 MVP |
| `../implementation-plan/risk-consistency-verification.md` | 기존 `10`, `11` | 리스크, 품질 게이트, 문서 정합성 감사 |

## 목적

기존 Moduly를 기반으로 Nodease를 기업용 AI Workflow / LLMOps 운영 플랫폼으로 발전시킨다. 계획의 기준은 "시연하기 쉬운 기능"이 아니라 "먼저 설계해야 나중에 덜 갈아엎는 기반"이다. 따라서 RBAC/resource model을 MVP 1의 맨 앞에 둔다.

Moduly는 이미 워크플로우 편집, 실행, 배포, RAG, LLM credential, usage log, schedule/webhook/API 실행 기반을 가지고 있다. Nodease는 여기에 다음 운영 능력을 추가한다.

- RBAC/resource/permission 기반
- audit/tracing 기반
- LLMOps 관측성
- RAG 데이터 변경과 chunk lineage
- 데이터 거버넌스와 정책 차단
- 비용/품질 비교와 추천
- 배포 전 체크와 운영 대시보드

이 requirements 문서는 현재 `dev @ 860ece0dee7cab3925d27f30ea650baf0cb18b4e` 구현 사실과 MVP 목표 상태를 함께 다룬다. "현재 코드", "현재 구현"으로 표시한 내용은 이미 코드 기준으로 확인한 동작이고, MVP 2/3의 governance, deploy checklist, recommendation, operations dashboard 항목은 목표 범위다.

## 왜 RBAC가 먼저인가

RBAC는 단일 기능이 아니라 이후 모든 기능의 기준 좌표다.

- 어떤 모델을 쓸 수 있는가
- 어떤 데이터 소스를 쓸 수 있는가
- 어떤 워크플로우를 실행/수정/배포할 수 있는가
- audit log의 actor/event/resource를 어떻게 해석할 것인가
- RAG chunk trace를 누구에게 보여줄 것인가
- 비용 집계를 user/app/project/team 단위로 어떻게 볼 것인가

따라서 MVP 1에서 완성형 RBAC를 만들지는 않더라도, resource model과 permission check 구조는 먼저 설계해야 한다.

## 3단계 MVP

| 단계 | 이름 | 작동하는 결과물 |
| --- | --- | --- |
| MVP 1 | 기반 구축 및 LLMOps Observability | 권한/감사 기반을 깔고, 기존 워크플로우 실행 후 노드별 모델/토큰/비용/지연시간/실패를 확인한다 |
| MVP 2 | Governance 및 RAG Audit | 데이터 소스 권한 차단, RAG 변경/청크 추적, audit log 검색이 실제 실행 경로에서 작동한다 |
| MVP 3 | 최적화 및 엔터프라이즈 운영 | 비용 최적화 추천, 배포 전 체크, version diff, 운영 대시보드, 배포 재현성까지 연결된다 |

## MVP별 설계 범위

RBAC 범위:

| 단계 | 범위 |
| --- | --- |
| MVP 1 | resource/permission 모델, workflow read/write/execute, LLM credential `use`와 credential-model relation check |
| MVP 2 | knowledge base `use` enforcement, document metadata policy, DB connection runtime use enforcement, HR 데이터 차단 시나리오 |
| MVP 3 | deploy/manage 권한, audit log 접근 제어, 운영 dashboard scope |

Audit/Tracing 범위:

| 단계 | 범위 |
| --- | --- |
| MVP 1 | workflow execute, LLM call, permission denied skeleton |
| MVP 2 | permission row 변경 audit, RAG retrieve, policy warn/block |
| MVP 3 | deployment diff/check, recommendation, cache/fallback event |

Data Governance 범위:

| 단계 | 범위 |
| --- | --- |
| MVP 1 | classification 상수/metadata convention 설계, policy decision event 구조. 실제 데이터 차단은 LLM credential `use`와 credential-model relation 중심 |
| MVP 2 | knowledge base `use`, document metadata policy, credential `use`와 credential-model relation 기반 사용 정책 실제 차단 |
| MVP 3 | deploy checklist에서 PII/RAG/model policy 위험 표시 |

## MVP 간 의존성

```text
Team/User Permission Model
  -> Audit Logs / Trace Payload Model
  -> Data Governance Policy
  -> MVP 1 Observability
  -> MVP 2-0 Organization Membership / Invitation Foundation
  -> MVP 2 Governance Enforcement + RAG Audit
  -> MVP 3 Optimization + Operations
```

MVP 2 본작업 전제인 [MVP 2-0 Organization Membership / Invitation Foundation](../implementation-plan/mvp-2-0-organization-membership-invitation-foundation.md)은 dev 기준 DB/model/migration/backfill, permission helper 전환, organization member/invitation BE API까지 완료됐다. 남은 범위는 full membership 관리 UI, team/direct permission picker 필터 반영, legacy owner/manager fallback 축소 정책이다.

## 작동하는 MVP의 기준

각 MVP는 다음을 만족해야 한다.

- 기존 workflow 생성/저장/실행이 깨지지 않는다.
- 최소 1개의 LLM node 포함 workflow가 끝까지 실행된다.
- 새로 추가한 기능이 실제 실행 경로에 연결된다.
- UI에서 사용자가 결과를 확인할 수 있다.
- API는 사용자 소유 범위나 permission을 벗어난 리소스를 막는다.
- 권한 없는 접근은 API와 실행 경로에서 차단된다.
- 새 DB 모델은 migration 또는 명확한 schema 생성 전략을 가진다.
- Client build와 Gateway/Workflow Engine 핵심 테스트가 통과한다.
- 테스트 또는 재현 가능한 demo fixture가 있다.

## 핵심 설계 결정

1. MVP 1에서는 기존 `App`을 project boundary로 사용하고, 독립 `Project` 모델 도입 시점은 이후에 결정한다.
2. `Canvas`라는 제품 용어는 현재 구현의 `Workflow`에 매핑한다.
3. `KnowledgeBase`, `Document`, `LLMModel`, `LLMCredential`, `Workflow`, `Deployment`를 resource 개념으로 표준화한다. 현재 코드는 model별 permission table을 만들지 않고 `LLMCredential` 권한과 `llm_rel_credential_models`로 model 사용 가능 여부를 제한한다. `Connection`은 독립 permission resource로 두지 않는다. 현재 구현은 connector API와 DB source upload에서 `connections.user_id` owner 기준을 주로 사용하고, 저장된 DB source sync/processor 경로는 문서 metadata의 `connection_id`로 server-side secret을 사용한다. consuming workflow/knowledge base 권한으로 runtime `use`를 허용하는 정책은 MVP 2 목표다.
4. permission vocabulary는 `read`, `write`, `execute`, `use`, `manage`, `deploy`로 시작한다.
5. `Admin`, `Builder`, `Operator`, `Viewer`, `Auditor`는 DB role이 아니라 team template 또는 UI preset으로 취급한다.
6. audit은 신규 `audit_events`가 아니라 현재 코드의 `audit_logs`를 사용한다.
7. canonical action은 `audit_logs.action`에 저장하고, 정책 결과는 `audit_logs.audit_metadata.policy_result`에 저장한다.
8. Workflow runtime RAG trace는 신규 `rag_retrieval_traces`가 아니라 기존 trace 계열 table을 사용한다. Per-chunk retrieval evidence는 `trace_payloads.payload_kind='rag.retrieval'`에 저장하고, run/node metadata에는 retrieved chunk count, document/citation id, score summary 같은 요약 field만 저장한다. Workflow run이 없는 standalone RAG Agent answer는 workflow trace table에 RAG 전용 FK를 추가하지 않고, RAG 도메인의 `rag_answer_runs`와 opaque `correlation_id`로 trace/usage/audit을 느슨하게 연결한다.
9. 비용 추천과 quality score는 MVP 3까지 rule-based 또는 사용자 평가 중심으로 제한한다.
10. 현재 Moduly에는 명시적 rollback API가 없다. 이전 배포 활성화는 `toggle` 기반 `deployment.activate_previous` 이벤트로 표현하고 별도 rollback permission은 두지 않는다.
11. "컴플라이언스 준수"라고 과장하지 않고 "컴플라이언스 대응 가능한 audit/data governance 구조"라고 표현한다.

## 개발 원칙

1. 기존 Moduly의 워크플로우 실행, LLM 사용량, RAG, 배포, 로그 기반을 최대한 재사용한다.
2. 각 단계는 발표용 mock이 아니라, 실제 워크플로우를 만들고 실행할 수 있는 작동 MVP여야 한다.
3. RBAC/resource/audit 모델은 MVP 1에서 먼저 설계한다.
4. 기능 범위를 넓히기보다, 기업 운영 관점에서 보이는 증거를 먼저 만든다.
5. 완전 자동 최적화보다 추적 가능성, 비교 가능성, 감사 가능성을 우선한다.
6. 노드 단위 RBAC, 컴플라이언스 리포트, MCP Gateway, 고급 CDC는 후순위로 둔다.
