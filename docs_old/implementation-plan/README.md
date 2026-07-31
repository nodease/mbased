# 구현 계획

Status: Draft
Authority: Implementation Plan
Source of Truth: Yes
Verified Against: dev @ 860ece0dee7cab3925d27f30ea650baf0cb18b4e (PR #138 docs target, 2026-07-01 KST)

구현 계획 문서는 source-of-truth 문서를 실제 작업 순서와 이슈로 분해한다.

| 문서 | 역할 |
| --- | --- |
| [mvp-1-development-issue-plan.md](mvp-1-development-issue-plan.md) | MVP 1 개발 이슈 생성 계획 |
| [mvp-2-0-organization-membership-invitation-foundation.md](mvp-2-0-organization-membership-invitation-foundation.md) | MVP 2 본작업 전 organization membership 및 초대 기반 구현 계획 |
| [rag-agent-answer-phase-3.md](rag-agent-answer-phase-3.md) | RAG 확장 3단계 Agent answer 구현 계획 |
| [risk-consistency-verification.md](risk-consistency-verification.md) | 리스크, 정합성, 검증 매트릭스 |
| [implementation-decision-log.md](implementation-decision-log.md) | 구현 중 작은 결정, 기본값, 임시 호환 처리 기록 |

## 진행 순서

현재 진행 계획은 MVP 1 foundation 위에서 [MVP 2-0 Organization Membership / Invitation Foundation](mvp-2-0-organization-membership-invitation-foundation.md)의 완료된 foundation을 전제로 MVP 2 Governance/RAG/Audit 본작업을 진행하는 것이다.

```text
MVP 1 Foundation / LLMOps Observability
  -> MVP 2-0 Organization Membership / Invitation Foundation
  -> MVP 2 Data Source Permission Enforcement
  -> MVP 2 Data Classification
  -> MVP 2 RAG Retrieval Trace Metadata
  -> RAG Extension Phase 3 Agent Answer
  -> MVP 2 Re-index Flow
  -> MVP 2 Audit Log Search
```

MVP 2-0의 `organization_memberships` DB/model/migration/backfill, permission helper 전환, member/invitation BE API는 dev 기준 완료된 prerequisite이다. 이후 남은 organization membership 범위는 full membership 관리 UI, team/direct permission picker 필터 반영, legacy owner/manager fallback 축소 정책이다. MBA-78에서 RAG search-test와 LLM node runtime의 knowledge base `use` enforcement 및 RAG node execution check는 완료됐으므로, 이후 RAG 권한 범위는 `user_knowledge_permissions`, 전체 Knowledge/RAG endpoint permission 정렬, document metadata policy enforcement 확장에 집중한다.

## 권위

- 구현 계획이 requirements, architecture, data-model, api 문서와 충돌하면 구현 계획을 수정한다.
- 미결정 ADR을 구현 전제로 삼지 않는다.
- 새 policy, architecture, security, data-storage 결정은 [decisions/](../decisions/README.md)에 기록한다.
- 작은 구현 결정은 [implementation-decision-log.md](implementation-decision-log.md)에 기록하되, 상위 source-of-truth와 충돌하면 상위 문서를 따른다.
