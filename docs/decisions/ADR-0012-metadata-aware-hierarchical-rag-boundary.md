# ADR-0012: Metadata-aware 및 Hierarchical RAG 경계

Status: Accepted
Date: 2026-06-30 10:45 KST
Original: ADR-202606301045-metadata-aware-hierarchical-rag-boundary
Verified Against: feature/mba-78 @ HEAD (base dev caaa4cd)
Related ADRs: [ADR-0004-audit-log-rag-trace-storage](ADR-0004-audit-log-rag-trace-storage.md), [ADR-0007-mvp2-classification-metadata-storage](ADR-0007-mvp2-classification-metadata-storage.md), [ADR-0006-accept-rbac-auth-state-and-user-direct-permission](ADR-0006-accept-rbac-auth-state-and-user-direct-permission.md)

## Context

MBA-75는 RAG를 metadata-aware retrieval, permission-aware retrieval, hierarchical retrieval, trace/citation observability 방향으로 확장한다. 이 작업은 DB schema, API request/response, RBAC, audit/trace redaction boundary에 모두 영향을 줄 수 있으므로 용어와 경계를 먼저 고정해야 한다.

기존 권위 문서는 다음 기준을 이미 둔다.

- `rag_retrieval_traces` 신규 table을 만들지 않는다.
- `knowledge_bases.classification`, `documents.classification` column을 만들지 않고 `documents.meta_info` metadata convention을 사용한다.
- document별 permission table을 만들지 않는다.
- Knowledge base user direct permission은 `user_knowledge_permissions`로 다룬다.

## Options Considered

| Option | 설명 | 장점 | 단점 |
| --- | --- | --- | --- |
| Metadata-augmented RAG | 사용자 표현을 그대로 사용 | 요청과 일치 | prompt augmentation과 retrieval filter/policy가 혼동될 수 있음 |
| Metadata-aware RAG | retrieval/filter/policy에서 metadata를 인식한다는 의미로 사용 | 구현 범위가 명확 | 사용자 표현과 완전히 같지는 않음 |
| RBAC를 Hierarchical RAG로 포함 | 권한 계층을 hierarchy로 해석 | 설명이 단순 | access control과 retrieval/index structure가 섞임 |
| RBAC와 Hierarchical RAG 분리 | Permission-aware RAG와 Hierarchical RAG를 별도 축으로 정의 | 권한 우회와 검색 품질 문제를 분리해 설계 가능 | 문서에서 두 축을 모두 설명해야 함 |
| Free-form metadata filter | 임의 metadata filter 허용 | 확장 빠름 | JSONPath/raw query/민감 key/검색 semantics drift 위험 |
| Allowlist metadata filter | 허용 key/operator만 명시 | 안전하고 테스트 가능 | schema 변경 시 코드/API 문서 수정 필요 |
| Trace에 raw chunk content 저장 | UI 구현이 단순 | evidence 표시가 쉬움 | raw payload visibility/redaction 정책을 우회할 수 있음 |
| Trace에는 citation metadata만 저장 | redaction boundary 유지 | 민감정보 노출 위험 축소 | 본문 조회가 필요하면 별도 권한/API가 필요 |

## Decision

1. 공식 용어는 `Metadata-aware RAG`, `Permission-aware RAG`, `Hierarchical RAG`로 나눈다.
2. RBAC는 Permission-aware RAG 경계이고, parent/child chunk 구조는 Hierarchical RAG 경계다.
3. Metadata는 permission source of truth가 아니다. Active organization membership은 KB organization scope와 resource permission subject의 전제 조건이고, 이 membership만으로 KB `read`/`use`를 허용하지 않는다. Resource 허용은 organization manager override와 team/user knowledge permission의 effective permission으로 판정한다.
4. Metadata filter API는 allowlist 기반 구조화 schema만 허용한다. Free-form dict, JSONPath, raw SQL fragment, secret/header/prompt/completion/raw response 관련 key는 허용하지 않는다.
5. Document metadata source of truth는 `documents.meta_info`다. `document_chunks.metadata`는 denormalized cache이며 충돌 시 document metadata를 우선한다.
6. `pii` metadata policy는 external LLM prompt path에서 `policy.block`, internal-only search preview에서 `policy.warn`을 기본값으로 둔다.
7. `confidential`은 KB `use` 통과 시 허용하되 audit/trace policy result를 남긴다.
8. Hierarchical retrieval은 parent chunk를 coarse retrieval/routing에 사용하고, final citation/evidence는 child chunk로 반환한다.
9. RAG trace에는 raw chunk content를 기본 저장하지 않는다. Per-chunk evidence는 `trace_payloads.payload_kind='rag.retrieval'` convention으로 chunk id, document id, rank, score, token count, metadata summary를 저장한다. Run/node metadata에는 retrieved chunk count, document/citation id, score summary, fallback flag 같은 redaction-safe summary만 저장한다. 성공한 retrieval 감사는 별도 `audit_logs.action='rag.retrieve'`로 기록한다.
10. `user_knowledge_permissions`는 Knowledge Base user direct permission의 현재 table이다. "추가 여부"를 다시 정책적으로 선택하는 문제가 아니다.

## Rationale

Metadata filter와 RAG trace는 보안, 권한, 검색 품질이 동시에 걸린 경계다. Free-form filter나 raw chunk trace 저장을 허용하면 vector/keyword search 간 semantics drift, 민감 metadata 노출, raw payload visibility 우회 위험이 커진다.

Access control과 retrieval hierarchy를 분리하면 KB `use` 권한 실패, document metadata policy 차단, parent/child retrieval fallback을 각각 테스트할 수 있다. 또한 기존 flat KB를 깨지 않고 nullable hierarchical field와 flat fallback으로 단계적 migration이 가능하다.

## Consequences

- `docs/architecture.md`가 Knowledge/RAG service boundary와 runtime flow의 active architecture 기준이다.
- `docs/features/knowledge/api_spec.md`는 metadata filter, hierarchy mode, trace/citation contract를 current/proposed 상태로 분리해야 한다.
- `docs/data_model.md`는 MBA-78 1차에서 추가한 nullable hierarchical chunk column을 current schema로 기록하고, full parent-child ingestion/ranking은 후속 구현 범위로 분리해야 한다.
- `docs/features/knowledge/requirements.md`와 `docs/data_model.md`는 KB `use` runtime enforcement와 metadata-not-permission-source 경계를 명시한다.
- 구현 시 Gateway search-test와 Workflow Engine runtime retrieval은 같은 filter/policy helper를 공유해야 한다.

## Follow-up Review

- MBA-176에서 `user_knowledge_permissions`가 구현됐으므로, 후속 Knowledge/RAG 작업은 team/user KB permission helper 결과를 공통 경계로 사용한다.
- Hierarchical chunk column에 대한 실제 Alembic migration은 MBA-78 1차 구현에서 추가됐으며, full parent-child ingestion/ranking 동작은 후속 PR에서 별도 검증한다.
- `policy.warn`, `policy.block`, `rag.retrieve` AuditAction 상수와 테스트는 MBA-78 1차 구현에서 먼저 고정한다. `rag.retrieve`는 RAG retrieval 성공 감사에 사용하고, `rag.retrieval`은 trace payload kind로만 사용한다. `policy.warn`/`policy.block`의 실제 document metadata policy enforcement는 후속 구현 범위다.
- Search preview content와 workflow trace metadata-only 응답 경계가 UI에서 섞이지 않는지 browser smoke로 확인한다.
- Knowledge/RAG API를 `X-Organization-Id` 기반 active organization 계약으로 전환하고, primary organization fallback은 current behavior 호환 경로로만 유지할 범위를 테스트로 고정한다.
