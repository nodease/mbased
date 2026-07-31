# MVP 2: Governance 및 RAG Audit

Status: Draft
Authority: Requirements
Source of Truth: Yes
Verified Against: dev @ 860ece0dee7cab3925d27f30ea650baf0cb18b4e (PR #138 docs target, 2026-07-01 KST)
Related ADRs: [ADR-202606290124-mvp2-classification-metadata-storage](../decisions/ADR-202606290124-mvp2-classification-metadata-storage.md), [ADR-202606290131-audit-action-naming-standard](../decisions/ADR-202606290131-audit-action-naming-standard.md), [ADR-202606301045-metadata-aware-hierarchical-rag-boundary](../decisions/ADR-202606301045-metadata-aware-hierarchical-rag-boundary.md), [ADR-202607010220-rag-answer-trace-usage-correlation-boundary](../decisions/ADR-202607010220-rag-answer-trace-usage-correlation-boundary.md)

## 목표

MVP 2는 MVP 1에서 설계한 RBAC/audit/policy 기반을 실제 데이터 소스와 RAG 실행 경로에 적용한다.

이 문서는 MVP 2 목표 상태를 정의한다. 현재 Knowledge Base API는 주로 owner/current-user scope로 동작한다. MBA-78 1차 구현은 RAG search-test와 Workflow Engine runtime retrieval에 KB `use` 권한 enforcement, metadata filter, `rag.retrieve` 감사 action의 최소 경계를 붙인다. `classification` 정책 enforcement, `policy.warn`, `policy.block`, 변경 문서 단위 re-index UI/API, 전체 Knowledge/RAG endpoint scope 정렬은 여전히 목표 범위다.

결과물:

```text
작동하는 워크플로우 빌더
  + 데이터 소스 권한 차단
  + data classification
  + RAG chunk lineage
  + 문서 변경/재색인 흐름
  + audit log 검색
```

## MBA-75 RAG 확장 범위

MBA-75에서 사용하는 RAG 용어는 다음처럼 구분한다.

| 용어 | 요구사항 의미 |
| --- | --- |
| Metadata-aware RAG | document/source/chunk metadata를 retrieval filter, policy decision, ranking hint, citation evidence에 사용 |
| Permission-aware RAG | knowledge base `use` 권한과 document metadata policy를 RAG 실행 경로에서 강제 |
| Hierarchical RAG | parent/child chunk 계층을 indexing/retrieval에 사용 |

RBAC 기반 접근 제어는 Hierarchical RAG가 아니다. RBAC는 Permission-aware RAG의 access-control 경계이고, Hierarchical RAG는 검색/index 구조다.

MBA-75 목표 범위:

- metadata filter는 allowlist 기반 구조화 schema로 정의한다.
- metadata는 permission source of truth가 아니다.
- document metadata source of truth는 `documents.meta_info`다.
- `document_chunks.metadata`는 retrieval/filter/citation 성능을 위한 denormalized cache다.
- RAG search-test와 Workflow Engine runtime retrieval은 같은 filter/policy semantics를 사용한다.
- RAG search-test `chat`/`pure`는 retrieval과 content preview를 수행하므로 KB `use` 권한을 요구한다. 단순 KB/detail/document metadata 조회는 `read` 권한 기준이다.
- KB 권한 검증은 KB의 `organization_id`와 요청의 active organization context를 비교해야 한다. MVP 2 목표 계약은 Knowledge/RAG org-scoped API도 `X-Organization-Id` header를 사용하는 것이다. 현재 Knowledge/RAG API의 primary organization fallback과 owner/current-user scope는 과도기 구현으로만 본다.
- parent chunk는 coarse retrieval/routing에 사용하고, final citation/evidence는 child chunk로 반환한다.
- 기존 flat KB는 parent/child metadata가 없으면 flat retrieval로 fallback한다.
- MBA-85 2단계는 FILE/API source의 opt-in hierarchical ingestion과 parent-child retrieval을 backend 범위에서 먼저 구현한다. DB source hierarchical chunking, frontend hierarchy UI, advanced chunk tuning input, LLM 기반 parent summary 생성은 후속 범위다.

## 의존 기반

| 기반 | 재사용 방식 |
| --- | --- |
| MVP 2-0 organization membership foundation | `organization_memberships`를 organization 소속 기준으로 사용하고, active organization member만 team membership과 user direct permission의 대상이 되도록 전제 |
| MVP 1 permission model | knowledge base `use` 권한과 document metadata policy로 확장. connection runtime `use`는 consuming workflow/knowledge base 권한으로 허용 |
| MVP 1 `audit_logs` | 권한 변경, policy warn/block, RAG/re-index action 저장 |
| MVP 1 policy decision | `allow/warn/block` 결과 저장 |
| Knowledge Base | 데이터 소스 권한 대상 |
| Document `content_hash` | 변경 감지 |
| Document Chunk | lineage 대상 |
| RAG search-test | 검색 결과 UI 기반 |
| LLM Node `knowledgeBases` | 실제 RAG 실행 지점 |
| DB Connector | enterprise data source 시나리오 |
| Workflow Run/Node Run | trace 연결 기준 |

## Governance 범위

MVP 2에서 실제 enforcement를 붙이는 resource:

| 대상 | 정책 |
| --- | --- |
| `knowledge_base` | active organization scope 안에서 허용된 team permission 또는 MVP 2 planned user direct grant를 받은 user만 `use` 가능. HR team은 예시 시나리오다. |
| `document` | PII/confidential 문서는 `documents.meta_info` metadata policy로 warn/block 가능. document별 permission table은 만들지 않음 |
| `connection` | 독립 permission resource가 아니다. secret/manage는 제한하고 runtime `use`는 workflow/knowledge base 권한으로 확인 |
| `llm_model` | MVP 1의 credential `use` + credential-model relation 정책 유지 |
| `workflow` | `viewer` `execute` 차단 유지 |

권한 체크 위치:

```text
Gateway API
  -> knowledge base 설정 권한
  -> audit log 조회 권한

Workflow Engine
  -> knowledge base use 권한과 document metadata policy
  -> LLM node 실행 전 knowledgeBases permission 검증
  -> policy decision 기록
```

API에서만 체크하면 실행 경로 우회 문제가 생기므로 Workflow Engine의 RAG retrieval 직전에도 검사한다.

## Data Classification

지원 classification:

| Classification | 의미 | 기본 정책 |
| --- | --- | --- |
| `public` | 공개 가능 | 일반 실행 허용 |
| `internal` | 사내 업무 데이터 | 로그인 사용자/프로젝트 범위 허용 |
| `confidential` | 민감 업무 데이터 | 권한 필요, audit 필수 |
| `pii` | 개인정보 가능성 | 경고 또는 차단 |

MVP 2에서는 자동 PII 탐지를 완성하지 않는다. 초기 방식은 사용자가 classification을 수동 지정하고, 간단한 regex 기반 PII warning 후보를 제공하며, classification이 `pii`이면 외부 모델 호출 전 warn/block한다. 모든 policy decision은 `audit_logs.audit_metadata.policy_result`에 남긴다.

MBA-75 기본 정책은 external LLM prompt path에서는 `pii`를 `policy.block`으로 차단하고, internal-only search preview에서는 `policy.warn`으로 감사 가능한 경고를 남기는 것이다. `confidential`은 KB `use` 권한을 통과하면 허용하되 audit/trace policy result를 남긴다.

## RAG Retrieval Trace Metadata

MVP 2에서 필요한 workflow runtime RAG trace 정보는 신규 `rag_retrieval_traces` table을 만들지 않고 기존 trace 계열 table에 저장한다. Per-chunk retrieval evidence는 `trace_payloads.payload_kind='rag.retrieval'`의 redacted payload convention으로 저장하고, run/node trace metadata에는 redaction-safe summary만 저장한다.

```text
trace_payloads.redacted_payload / redaction_metadata
  payload_kind = "rag.retrieval"
  workflow_run_id
  workflow_node_run_id
  node_id
  knowledge_base_id
  retrieved_chunks[]
    document_id
    chunk_id
    parent_chunk_id?
    rank
    score
    token_count
    metadata_summary

workflow_runs.trace_metadata / workflow_node_runs.trace_metadata
  knowledge_base_id
  retrieved_chunk_count
  document_ids[]
  citation_ids[]
  score_summary
  hierarchy_fallback
  raw_content_returned
```

이 trace는 workflow 실행에서 "어떤 청크가 모델에 들어갔는가"를 설명하는 핵심 근거다. RAG 없는 LLM node는 기존처럼 동작해야 한다.

Workflow trace/run detail의 기본 응답은 raw chunk content 없이 citation metadata를 반환한다. Search-test response는 KB `use` 권한을 통과한 user에게 chunk content preview를 반환할 수 있지만, 그 content를 trace/audit metadata에 복사하지 않는다.

Workflow run이 없는 standalone RAG Agent answer는 이 section의 `trace_payloads` 저장 계약을 그대로 쓰지 않는다. Standalone answer는 RAG 도메인의 목표 table인 `rag_answer_runs`에 redaction-safe retrieval/citation/answer/usage summary와 nullable answer hash를 저장하고, trace/usage/audit과의 느슨한 연결은 opaque `correlation_id`로 한다. `trace_payloads.rag_answer_run_id`, `llm_usage_logs.rag_answer_run_id`, standalone answer 전용 `rag_retrieval_traces`는 만들지 않는다.

Standalone answer의 기본 durable storage에는 raw user question, raw final answer, raw retrieved chunk content, raw prompt/completion, credential 원문, API key, token, encrypted_config, provider raw response를 저장하지 않는다. Usage summary는 token/cost/latency와 model/credential/provider 식별자 allowlist로 제한한다. `query_hash`/`answer_hash`는 nullable이며, 값을 저장하려면 HMAC-SHA256, server-side secret/pepper, `hash_version`을 함께 사용한다. HMAC secret/pepper가 없으면 hash 값을 저장하지 않고 unsalted hash fallback을 허용하지 않는다.

## RAG 변경 정책과 Re-index

문서 변경 감지는 기존 `documents.content_hash`를 활용한다.

초기 정책:

- hash가 바뀌면 문서 processing status를 새 enum으로 바꾸기보다 `meta_info.needs_reindex=true` 같은 metadata flag를 우선 사용한다.
- full re-index와 partial re-index 선택지를 제공한다.
- MVP 2의 partial re-index는 chunk-level incremental indexing이 아니라 변경된 문서 단위 재색인으로 제한한다.
- MVP 2는 FILE/hash 중심으로 시작한다.
- CDC와 webhook sync는 MVP 3 이후 후보로 둔다.
- re-index action을 `audit_logs`에 저장한다.

## Audit Log Search

UI와 API는 최소한 아래 필터를 제공한다.

- 기간
- actor
- audit action
- target type
- workflow
- run
- node
- deployment
- policy result
- success/failure

MVP 2에서 검색해야 하는 대표 이벤트:

- 현재 ORM data-change action: `team_workflow_permission.*`, `user_workflow_permission.*`, `team_llm_permission.*`, `user_llm_permission.*`, `team_knowledge_permission.*`
- MVP 2 knowledge base permission API 및 user direct grant 목표 action: `user_knowledge_permission.*`. `team_knowledge_permission.*`는 현재 table/listener 기준으로 이미 가능한 action이다. MBA-78 1차는 RAG search-test와 Workflow runtime의 KB `use` enforcement를 먼저 연결했고, 전체 Knowledge/RAG endpoint permission API 정렬과 user direct grant는 후속 범위다.
- `permission.denied`
- `policy.warn`
- `policy.block`
- `rag.retrieve`
- `rag.answer.requested`
- `rag.answer.completed`
- `rag.answer.failed`
- `rag.answer.cancelled`
- `rag.answer.purge`
- re-index 관련 event

`rag.retrieve`는 RAG retrieval 성공 audit action이고, `trace_payloads.payload_kind='rag.retrieval'`는 trace payload 분류값이므로 구현과 테스트에서 분리한다. Standalone Agent answer lifecycle은 `rag.answer.*` action과 `rag_answer_runs.status`로 추적한다. `rag.answer.requested`는 schema validation, organization header validation, active organization scope 확인, KB scope visibility 확인, required credential/model visibility 확인을 모두 통과해 answer run을 생성할 때 남긴다. Scope 안 resource가 확인된 뒤 policy 또는 permission preflight 차단이 발생한 경우에만 `rag_answer_runs.status="blocked"`를 사용한다. PII/classification/metadata policy 차단은 `policy.block`, KB/credential/model permission preflight 차단은 `permission.denied` audit으로 표현하고 별도 `rag.answer.blocked` action은 만들지 않는다. `resource.not_found`, scope 밖, organization mismatch, invalid organization header, validation 실패에는 answer run과 lifecycle audit을 만들지 않는다. Retention purge aggregate는 `rag.answer.purge`로 기록한다. `policy.warn`/`policy.block`은 action 상수와 naming convention을 먼저 고정하고, 실제 document metadata policy enforcement는 후속 구현에서 연결한다. RAG Agent answer 3단계는 external LLM prompt path의 final evidence `pii` block만 이번 범위에 포함하고, search-test/runtime 전체 policy enforcement 확장은 별도 범위다.

## 사용자 흐름

1. organization owner/manager가 예시 HR knowledge base를 만든다.
2. 예시 HR team처럼 허용된 team permission 또는 MVP 2에서 추가할 user direct grant를 받은 user만 해당 knowledge base를 `use`할 수 있게 설정한다.
3. `builder` 권한 user가 HR knowledge base를 사용하는 RAG workflow를 만든다.
4. 권한 없는 사용자의 실행은 차단된다.
5. 권한 있는 사용자의 실행은 성공한다.
6. 실행 상세에서 검색된 document/chunk list를 본다.
7. 문서가 변경되면 `meta_info.needs_reindex=true` 같은 재색인 필요 표시가 뜬다.
8. organization owner/manager 또는 knowledge base `manager`가 partial re-index를 실행한다.
9. audit log에서 권한 차단, 실행, 재색인 이벤트를 검색한다.

## 추가 개발 범위

- RAG search-test/runtime 밖의 knowledge base 권한 enforcement와 document metadata policy
- `user_knowledge_permissions` additive grant 추가. 현재 코드에는 아직 없음
- metadata-aware retrieval filter의 API/UI 확장과 품질 검증
- hierarchical chunk schema 기반 ingestion, parent/child retrieval
- DB connection secret/manage/use 분리 enforcement
- RAG retrieval trace metadata의 run/node summary 확장과 UI/API 노출
- data classification metadata convention 및 API/UI 노출
- policy decision 저장
- audit log 검색 API/UI
- re-index 상태 UI
- 수동 classification과 간단한 regex 후보 기반 PII/confidential warning/block

## 작업 순서

0. MVP 2-0 Organization Membership / Invitation Foundation

상태:

- `organization_memberships` table과 migration/backfill은 dev 기준 완료된 prerequisite이다.
- active organization membership 기반 permission helper 전환도 dev 기준 완료된 prerequisite이다.
- team membership과 user direct permission의 grantee 검증 기준을 organization membership으로 바꾸는 방향은 완료된 foundation 위에서 유지한다.
- organization member/invitation BE API와 accept/remove cleanup audit은 dev 기준 구현된 prerequisite이다.

남은 작업:

- full membership 관리 UI와 Organization Members 화면 반영
- team/direct permission picker 필터 반영
- legacy fallback 축소/제거 시점과 removed/suspended member 정리 정책 확정

검증:

- 기존 team member와 organization creator/manager가 migration 후 active organization member가 됨
- active organization member가 아니면 resource permission row가 있어도 접근 거부됨
- team에 속하지 않은 active organization member에게 direct permission 부여 가능
- member 제거 시 team membership과 user direct permission 정리
- legacy fallback 축소가 기존 MVP 1 workflow/LLM permission demo를 깨지 않음
- MVP 1 workflow/LLM permission demo 회귀 없음

1. Data Source Permission Enforcement

작업:

- MBA-78 1차 완료: RAG search-test와 LLM node runtime의 knowledge base `use` 권한 체크
- 후속: 전체 Knowledge/RAG endpoint permission API 정렬
- 후속: document metadata policy 체크와 connection secret/manage/use 분리 구현
- MBA-78 1차 완료: LLM node 실행 전 knowledgeBases permission 검증
- MBA-78 1차 완료: knowledge base `use` 권한 실패는 `permission.denied`로 `audit_logs`에 저장
- 후속: document metadata/model/trace policy 차단은 `policy.block`으로 `audit_logs`에 저장

검증:

- 권한 없는 RAG workflow 실행 차단
- 권한 있는 실행 성공

2. Data Classification

작업:

- document classification은 `documents.meta_info.classification` metadata convention으로 저장하고, knowledge base classification은 document classification에서 파생
- UI badge 추가
- classification 변경 `audit_logs` row 저장
- `public/internal/confidential/pii` 지원

검증:

- classification 변경/조회
- `audit_logs` row 생성

3. RAG Retrieval Trace Metadata

작업:

- retrieval 결과에서 document id, chunk id, score, rank 추출
- per-chunk evidence는 `trace_payloads.payload_kind='rag.retrieval'`에 저장
- run/node trace metadata에는 retrieved chunk count, document/citation id, score summary, fallback flag 같은 summary만 저장
- workflow run/node id와 trace payload 연결
- run detail API에 trace 포함

검증:

- RAG LLM node 실행 후 chunk trace 조회
- RAG 없는 LLM node는 기존처럼 동작

4. Re-index Flow

작업:

- 기존 `content_hash` 변경 감지 표시
- processing status를 바꾸지 않고 `meta_info.needs_reindex` 같은 metadata flag 정의
- full re-index와 변경 문서 단위 partial re-index action UI
- re-index `audit_logs` row 저장

검증:

- 문서 변경 후 재색인 필요 표시
- 변경 문서 단위 partial re-index 후 metadata flag 갱신

5. Audit Log Search

작업:

- audit log list API
- filters: 기간, actor, action, target, policy result
- dashboard 또는 settings 하위 Audit 화면

검증:

- 권한 차단 이벤트 검색
- RAG retrieval/re-index 이벤트 검색

## 완료 기준

| 영역 | 완료 기준 |
| --- | --- |
| RBAC enforcement | knowledge base `use` 권한이 실행 경로에서 적용됨 |
| RAG trace | 사용된 document/chunk/rank/score가 run detail에서 보임 |
| Data governance | classification과 policy decision이 저장됨 |
| Re-index | 변경 문서에 대해 metadata 기반 재색인 필요 표시와 변경 문서 단위 re-index action이 보임 |
| Audit UI | 실행/차단/재색인/권한 변경 이벤트를 검색 가능 |

## Demo Script

```text
1. organization owner/manager가 예시 HR KB를 만들고 confidential로 분류한다.
2. 예시 HR team처럼 허용된 team permission 또는 MVP 2에서 추가할 user direct grant를 받은 user만 use 가능하게 설정한다.
3. `builder` 권한 user가 해당 KB를 쓰는 RAG workflow를 만든다.
4. 권한 없는 사용자는 실행 차단된다.
5. HR 권한 사용자는 실행 성공한다.
6. 실행 상세에서 사용된 chunk list를 확인한다.
7. 문서를 변경하고 partial re-index를 실행한다.
8. Audit Log에서 모든 이벤트를 확인한다.
```

## 테스트 범위

- knowledge base permission enforcement
- policy decision allow/warn/block
- rag trace metadata create/query
- document re-index state
- audit log filter
- 기존 RAG search-test 회귀 테스트

## MVP 2에서 하지 않을 것

- 전체 CDC
- OIDC/SSO
- 노드 단위 세밀 권한
- 완전 자동 PII redaction
- 컴플라이언스 리포트
