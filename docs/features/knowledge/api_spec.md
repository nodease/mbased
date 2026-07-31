# Knowledge API Spec

Status: Draft
Verified Against: feature/mba-302 @ b2d6467002b7becf1daa0badfe6fc155b3edaa57
이 문서는 Knowledge feature의 현재 API baseline과 목표 KB 통합 API 계약을 함께 기록한다. MBA-105 목표 API는 [ADR-0017](../../decisions/ADR-0017-knowledge-integration-provisional-implementation-baseline.md)의 임시 구현 baseline, Workflow RAG anonymous public-only runtime은 [ADR-0018](../../decisions/ADR-0018-workflow-rag-anonymous-public-only-runtime.md), MCP/API source connector와 incremental sync 경계는 [ADR-0020](../../decisions/ADR-0020-knowledge-mcp-incremental-sync-boundary.md), MBA-231 위임 관리와 KB RBAC cutover는 [ADR-0034](../../decisions/ADR-0034-knowledge-delegated-administration-and-rbac-boundary.md), direct KB와 명시 selected Collection의 internal runtime resolver는 [ADR-0036](../../decisions/ADR-0036-knowledge-runtime-candidate-resolution.md), KC 운영 관리 계약은 [ADR-0044](../../decisions/ADR-0044-knowledge-collection-operational-management-boundary.md), 세부 구현 기준은 [implementation_baseline.md](implementation_baseline.md)를 따른다. Knowledge Skill 관련 API 경계는 [ADR-0015](../../decisions/ADR-0015-knowledge-skill-context-routing-boundary.md)를 따른다.
KC sync 요청·상태 조회와 durable execution 계약은 [ADR-0048](../../decisions/ADR-0048-knowledge-collection-sync-execution-boundary.md)을 따른다.
Organization Detector Provider와 embedding 전 local masking Target은 [ADR-0070](../../decisions/ADR-0070-organization-detector-provider-and-pre-embedding-local-masking-boundary.md)을 따른다. MBA-362 전까지 이 계약의 provider management/runtime endpoint가 구현됐다는 뜻은 아니다.

## Current Baseline Endpoints

| Method | Path | 목적 | 권한 경계 |
| --- | --- | --- | --- |
| GET | `/api/v1/knowledge` | 현재 KB 목록 | Active organization에서 KB `read`가 허용된 active KB만 반환하고 unauthorized row/count는 생략한다 |
| GET | `/api/v1/knowledge/llm-selectable` | Workflow LLM node RAG picker용 KB 후보 목록 | `X-Organization-Id` active organization 필수. active lifecycle이고 `sync_state != source_deleted`이며 caller가 KB `use` 권한을 가진 KB만 반환한다. 반환 후보는 retrieval-visible `completed` document chunk가 1개 이상 있어야 하며, runtime은 실행 시점 execution subject 기준으로 다시 권한을 평가한다 |
| POST | `/api/v1/knowledge` | 빈 KB 생성 | Active organization에 KB, 생성자의 user-direct `manager`, canonical audit를 한 transaction에서 생성한다. 필수 schema가 준비되지 않으면 `503 knowledge.schema_not_ready`로 fail-closed 처리한다 |
| GET | `/api/v1/knowledge/{kb_id}` | 현재 KB 상세와 문서 상태 | Active organization + KB `read`. Detail capability는 `can_read/use/write/read_content/manage`와 빈 active manual KB에 최초 source를 등록할 수 있는 `can_register_initial_document`를 반환한다 |
| GET | `/api/v1/knowledge/{kb_id}/documents/{document_id}/edit-config` | Document preview/process 설정 복원 | Active organization + KB `write`. Bounded property/aggregate/serialized-size allowlist만 반환하고 read detail과 encrypted source config를 재사용하지 않는다. 성공 응답은 `Cache-Control: no-store`다 |
| POST | `/api/v1/knowledge/{kb_id}/documents/{document_id}/preview` | Document 설정 미리보기 | Active organization + KB `write`. DB source는 submitted/fallback opaque Connection reference가 current user 소유인지 확인한 뒤 processor를 호출한다. Resolver 저장소 장애는 `503 connection.reference_unavailable`, processor 직전 재검증의 temporary failure는 `503 source.temporarily_unavailable`로 닫는다 |
| POST | `/api/v1/knowledge/{kb_id}/documents/{document_id}/process` | Document 설정 저장과 durable 처리 시작 | Active organization + KB `write`. DB source는 Connection owner 검증과 설정 allowlist를 통과한 뒤 owner Connection row를 잠그고 sanitized metadata·queued Document projection·job commit까지 유지해 Connector 삭제와 직렬화한다. Worker는 dial 직전에 같은 owner 정책을 재검증한다. 성공한 `202`는 opaque `job_id`, `reused`, `dispatch_deferred`만 추가 반환한다. Missing/malformed/other-owner reference는 `404 resource.hidden`, lock 또는 commit의 transient contention은 `503 connection.reference_busy`, 기타 persistence failure는 `503 connection.reference_unavailable`로 닫는다 |
| POST | `/api/v1/knowledge/{kb_id}/documents/{document_id}/sync` | 기존 설정으로 durable sync 시작 | Active organization + KB `write` 및 source sync에는 `sync_manage`. DB Connection reference를 다시 잠그고 job UUID만 발행한다 |
| GET | `/api/v1/knowledge/{kb_id}/documents/{document_id}/ingestion` | 최신 ingestion job safe status | Active organization + KB `read`. `Cache-Control: no-store`; raw source/provider/error/token 없이 operation, status, attempt, retryability와 safe reason/timestamp만 반환한다 |
| POST | `/api/v1/knowledge/{kb_id}/documents/{document_id}/ingestion/retry` | Retryable dead-letter의 새 generation 생성 | Active organization + KB `write`; 이전 operation이 sync면 `sync_manage`도 재검사한다. Latest job ID를 고정해 권한 확인과 redrive 사이 TOCTOU를 차단한다 |
| GET | `/api/v1/knowledge/{kb_id}/safe-metadata` | allowlisted KB recommendation metadata 조회 | active organization, KB `manage`; 권한 없는 resource는 404로 숨긴다 |
| PATCH | `/api/v1/knowledge/{kb_id}/safe-metadata` | `safe_label`, `kb_safe_description`, `kb_safe_topics` 수정 | active organization, KB `manage`, sanitizer, audit. 일반 KB 설정 PATCH와 분리한다 |
| POST | `/api/v1/knowledge/{kb_id}/archive`, `/restore` | Manual KB lifecycle 전이 | KB `manage` 또는 domain `lifecycle_manage`; source-managed KB는 source-owned로 차단 |
| DELETE | `/api/v1/knowledge/{kb_id}?acknowledged_hard_delete=true` | Manual KB hard delete | Organization manager 전용, explicit acknowledgement, approved retention/legal-hold gate. Production gate가 연결되지 않은 현재 baseline은 `403 policy.denied`로 fail-closed하며, allow된 경우에만 permission cleanup과 audit를 같은 DB transaction에서 처리한다 |
| POST | `/api/v1/knowledge/candidates/resolve` | Builder/deployment preflight용 safe KB 후보 조회 | active organization, collection route 또는 explicit KB helper |
| POST | `/api/v1/knowledge/rag-recommendations` | Workflow Builder용 LLM node RAG option 추천 | active organization, candidate resolver safe set, KB 단위 recommendation |
| POST | `/api/v1/rag/upload` | 빈 manual KB의 최초 문서 등록/색인 요청 | `X-Organization-Id` active organization 필수. 신규 KB는 active organization에 귀속하며 primary organization fallback을 사용하지 않는다. 기존 KB는 KB `write`, active/manual/non-source-managed와 빈 document slot을 요구한다. DB source는 owner preflight 후 KB/slot을 먼저 확인하고, 등록 직전에 MBA-273 reference lock을 획득해 document commit까지 유지한다. Manual KB에서는 active version pointer가 있는 completed Document를 포함해 모든 상태의 기존 Document가 slot을 점유하며 두 번째 독립 source는 `409 knowledge.document_slot_occupied`다. Source-managed/non-manual KB는 slot 존재를 노출하기 전에 `knowledge.document_registration_not_allowed`로 거부한다 |
| POST | `/api/v1/rag/upload/presigned-url` | FILE 또는 Workflow 입력용 임시 upload URL | Knowledge 최초 등록 호출은 `knowledgeBaseId`를 전달하고 active organization, KB `write`와 현재 빈 document slot을 fast precheck한다. 기존 Workflow 입력 파일 호출은 KB 식별자 없이 사용할 수 있다. 최종 Knowledge `/rag/upload`는 KB row lock 아래에서 cardinality를 다시 검증한다 |
| POST | `/api/v1/rag/search-test/pure` | 검색 테스트 | active organization, KB use |
| POST | `/api/v1/rag/search-test/chat` | 검색+답변 테스트 | active organization, KB use, LLM credential |
| POST | `/api/v1/rag/agent/answer` | 명시 `knowledge_base_id` 기반 standalone Agent answer | KB use, generation model/credential use |
| POST | `/api/v1/rag/agent/answer/stream` | standalone Agent answer SSE | KB use, generation model/credential use |
| GET | `/api/v1/rag/document/{document_id}/progress?organizationId={active_organization_id}` | 문서 처리 상태 SSE | Native EventSource의 custom header 제약 때문에 active organization을 query parameter로 전달한다. Gateway는 stream 생성 전에 active organization + KB `read`를 검증하며, 권한 없는 document의 상태·오류·Redis progress를 노출하지 않는다. 응답은 `Cache-Control: no-cache, no-store`, `X-Accel-Buffering: no`를 사용한다 |
| POST | `/api/v1/rag/document/{document_id}/confirm?strategy={strategy}` | 승인 대기 Document의 durable resume | Active organization + KB `write`, `waiting_for_approval`, allowlisted strategy를 요구한다. Job UUID만 queue에 발행하고 `job_id/reused/dispatch_deferred`를 반환한다 |
| GET | `/api/v1/permissions/knowledge-bases/{knowledge_base_id}` | KB에 부여된 team/user direct permission 목록 | manager 또는 KB `manage`, active organization |
| PUT | `/api/v1/permissions/knowledge-bases/{knowledge_base_id}/teams/{team_id}` | team KB permission 생성/갱신 | manager 또는 KB `manage`, active organization |
| PUT | `/api/v1/permissions/knowledge-bases/{knowledge_base_id}/users/{user_id}` | user direct KB permission 생성/갱신 | manager 또는 KB `manage`, active organization |
| DELETE | `/api/v1/permissions/knowledge-bases/{knowledge_base_id}/teams/{team_id}` | team KB permission 회수 | manager 또는 KB `manage`, active organization |
| DELETE | `/api/v1/permissions/knowledge-bases/{knowledge_base_id}/users/{user_id}` | user direct KB permission 회수 | manager 또는 KB `manage`, active organization |

현재 `POST /api/v1/knowledge`는 공백뿐인 `name`, 255자를 초과하는 `name`, 비어 있거나 secret-like/token-like 또는 allowlist 밖 문자를 포함한 `embedding_model`을 DB insert 전에 safe validation error로 거부한다. Validation error response는 raw request value를 echo하지 않고 reason code만 반환한다. KB `name`은 사용자 표시용 label이며 resource identity가 아니므로 같은 organization 안의 동일 `name` 생성을 이름만으로 거부하지 않는다. 같은 제목의 서로 다른 문서, 수동 KB, source-managed KB는 `knowledge_base_id`, protected source identity, sync/lifecycle state, safe metadata로 구분한다. 단, 같은 문서의 version은 여러 개가 동시에 retrieval-visible한 resource로 취급하지 않는다. 내부 문서는 active/head pointer가 가리키는 ready version만 검색 노출하고, 외부 source-managed 문서는 정상 sync/finalization이 완료되면 최신 active ready version으로 교체한다. Sync 실패나 stale 상태에서는 기존 active ready version만 warning과 함께 유지할 수 있으며, 이전/superseded/pre-finalized version은 selectable-ready 또는 retrieval evidence 후보가 아니다. Source-managed KB의 동일 source item 중복 방지는 `source_identity_id`와 source sync lineage invariant로 다루며, KB `name` conflict로 대체하지 않는다.

MBA-231 cutover 이후 `/api/v1/knowledge/*`의 list/detail/settings/document/process/preview/sync와 `/api/v1/rag/upload`, document analyze/confirm/delete/progress는 active organization과 canonical KB action helper를 사용한다. `knowledge_bases.user_id`는 생성자/귀속 정보이며 이 표면의 권한 우회가 아니다. MBA-273부터 Knowledge 최초 등록을 위한 presigned upload 호출은 대상 `knowledgeBaseId`를 전달하고 active organization, KB `write`, initial document slot fast precheck를 통과해야 한다. 같은 endpoint를 사용하는 Workflow 입력 파일은 아직 KB가 확정되지 않은 별도 storage surface이므로 KB 식별자 없이 기존 authenticated storage 경계를 따른다. URL/proxy preview처럼 아직 KB가 확정되지 않은 표면은 별도 storage/egress 경계를 따르며, raw/source-derived content는 승인된 `content_read` 또는 후속 raw/compliance 정책 없이 노출하지 않는다.

`GET /api/v1/knowledge/{kb_id}`의 `can_register_initial_document`는 서버가 계산한 UI capability다. Caller가 KB `write`를 가지고, KB가 active manual/non-source-managed이며 현재 `Document`가 없을 때만 true다. Field 누락이나 false는 fail-closed다. 이 값은 mutation authorization token이 아니며 `/rag/upload`는 동일 정책을 KB row lock 아래에서 다시 확인한다. 과거 삭제가 남긴 active version pointer는 canonical registration에서 version의 `legacy_document_id`와 `source_identity_id`가 이미 제거된 경우에만 `superseded`로 전환하고 pointer를 해제한다. Live document/source identity 또는 source-managed state는 자동 복구하지 않는다. Independent source append conflict는 raw filename/path나 기존 document identity를 포함하지 않는 다음 safe response를 사용한다.

```json
{
  "detail": {
    "error": {
      "code": "knowledge.document_slot_occupied",
      "message": "This Knowledge Base already has a source document.",
      "request_id": "<request-id>",
      "details": {}
    }
  }
}
```

Document `read` response의 `meta_info`는 status/progress allowlist이며 edit form의
source가 아니다. `GET /knowledge/{kb_id}/documents/{document_id}/edit-config`는 KB
`write`를 통과한 caller에게 `chunk_size`, `chunk_overlap`, `chunking_mode`,
`segment_identifier`, processing option, selection option과 DB edit allowlist를
반환한다. DB allowlist는 opaque `connection_id`, bounded table/column selection,
sensitive-column marking, alias, template와 join shape만 허용한다. API source는 method,
configured/header/body presence 같은 safe summary만 반환하고 URL/header/body 원문,
encrypted field, connection credential/detail은 반환하지 않는다. Stored config가
malformed 또는 bound 밖이면 raw fallback 대신 `editable=false`와 safe reason code를
반환하고 Client는 preview/process를 차단한다. 개별 field cap을 모두 통과하더라도
aggregate item 또는 serialized response budget을 초과하면 같은 unavailable 결과로
닫는다. Client는 권한과 hydration 상태를 현재 KB/document id scope에 결박하고, route
전환 뒤 늦게 도착한 이전 scope 응답이나 fetch failure로 action을 다시 열지 않는다.

DB source upload/process/preview의 `connection_id`는 opaque input일 뿐 권한 증명이 아니다.
Gateway는 document/설정 mutation 전에 Shared Connection Use Resolver로
`Connection.user_id == current_user.id`를 확인하고, process 저장 시 nested config에서
Connection reference와 Connection detail field를 제거해 top-level opaque `connection_id`만
canonical reference로 남긴다. Background Gateway ingestion과 Workflow Engine KC sync는 외부
DB dial 직전에 current execution subject로 같은 resolver를 다시 호출해 최신 권한 스냅샷을 확인한다.
Missing, malformed, deleted, owner 변경과 non-owner reference는 모두 `404
resource.hidden`으로 일반화하며 Connection id/name/owner/host/database/username/credential을
응답·audit·processing metadata에 넣지 않는다. Credential 복호화 실패는
`configuration.invalid`로 닫고 저장 암호문을 adapter credential로 fallback하지 않는다.
Resolver 저장소 장애는 Gateway에서 `503 connection.reference_unavailable`, processor에서
`source.temporarily_unavailable`로 정규화한다. Runtime row lock과 실행 도중 revoke 취소는
MBA-302의 별도 transaction/lock 계약 범위다.

KB detail/direct document의 `error_message`와 progress SSE의 `message`/`error`는
persisted 원문이 아니다. Gateway가 status를 fixed public message로 투영하며 failure는
generic safe message만 반환한다. SSE progress는 0..100 범위로 제한하고 unauthorized
또는 concurrent-delete path에서도 raw DB/Redis/exception text를 event에 넣지 않는다.
Redis progress는 `indexing`/`processing`에서만 사용하며 `pending`과
`waiting_for_approval`은 stale Redis 값과 무관하게 0이다.

## Target Endpoint Groups

| 그룹 | 목표 path | 목적 |
| --- | --- | --- |
| Collections | `/api/v1/knowledge/collections`, `/api/v1/knowledge/collections/{collection_id}` | Collection 목록, safe metadata, route/manage/sync operation |
| Collection items | `/api/v1/knowledge/collections/{collection_id}/items` | Document-level KB link/unlink. KB content permission을 부여하지 않음 |
| Collection permissions | `/api/v1/knowledge/collections/{collection_id}/permissions` | Collection `read`/`route`/`manage`/`sync` grant/revoke. Additive allow만 제공 |
| Collection visibility | `/api/v1/knowledge/collections/{collection_id}/visibility` | Anonymous public-only runtime 후보 여부를 safe metadata flag로 전환. Source-managed KB public exposure approval은 별도 정책 row로 검증 |
| Document-level KBs | `/api/v1/knowledge/kbs/{kb_id}` | KB detail, active version, sync state, remediation summary |
| Document versions | `/api/v1/knowledge/kbs/{kb_id}/versions/*` | Version history, active version, re-index state |
| Raw/compliance view | `/api/v1/knowledge/kbs/{kb_id}/raw-artifacts/*` | Raw/compliance gate 이후 선택적 protected raw content access. RAG answer API에서 사용하지 않음 |
| Source connectors | `/api/v1/knowledge/sources/*` | Source connection, sync, tombstone, ACL status, remediation |
| Knowledge skills | `/api/v1/knowledge/skills/*` | Provider-neutral skill registry, version, freshness/eval status, safe metadata. 주 사용처는 빌더 단계 LLM node의 RAG 옵션 구성 |
| Document type registry | `/api/v1/knowledge/document-types` | Platform-supported type definition과 exact published registry version의 safe projection |
| Organization taxonomy | `/api/v1/organizations/{organization_id}/knowledge-taxonomies/*` | Draft/validate/publish/impact-preview와 current/draft/history version list/detail 조회 |
| Processing profile policy | `/api/v1/organizations/{organization_id}/knowledge-processing-profile-policies/*` | Immutable profile mapping policy draft/validate/publish와 current/draft/history version list/detail 조회 |
| Document classification | `/api/v1/knowledge/kbs/{kb_id}/classification/*` | Effective assignment 조회, atomic replacement, lock/unlock과 suggestion review |
| Processing profile | `/api/v1/knowledge/kbs/{kb_id}/processing-profile/*` | Explicit override options/read/set/clear, deterministic preview와 create-only reindex admission |
| 실행 시점 RAG candidate resolution/retrieval | 내부 service call | MBA-232는 direct KB + 명시 selected Collection을 current audience로 재평가하는 Workflow Engine internal resolver contract만 제공한다. Builder/preflight `/api/v1/knowledge/candidates/resolve`, public API, graph/LLM/retrieval wiring은 분리하며 실제 연결은 MBA-233 범위다 |
| Knowledge domain permissions | `/api/v1/knowledge/domain-permissions`, `/api/v1/knowledge/domain-capabilities` | Organization manager가 Team/User 관리 action을 위임하고 caller의 safe capability를 조회 |

공개 HTTP path가 필요한 경우에는 별도 API gate review에서 path 이름과 JSON/SSE shape를 확정한다. MBA-105의 필수 계약은 collection listing(`collection.read`), collection routing(`collection.route`), KB content permission, source ACL state, document version citation identity의 분리다. Skill authoring, test, submit-for-review, publish/deprecate, Workflow Playground skill binding API는 아직 승인된 계약이 아니다.

### MBA-305 Classification And Profile Target Contract

이 subsection은 [ADR-0065](../../decisions/ADR-0065-knowledge-classification-taxonomy-and-processing-profile.md)이
승인한 목표 API다. 현재 endpoint가 구현됐다는 뜻은 아니며 physical schema와 최종 route
registration은 taxonomy/manual classification 구현 PR에서 확정한다. 구현 PR은 아래 권한,
atomic replacement, safe projection과 오류 의미를 바꾸지 않는다.

#### Registry And Taxonomy

```text
GET    /api/v1/knowledge/document-types
GET    /api/v1/organizations/{organization_id}/knowledge-taxonomies/current
GET    /api/v1/organizations/{organization_id}/knowledge-taxonomies/versions
GET    /api/v1/organizations/{organization_id}/knowledge-taxonomies/versions/{version_id}
POST   /api/v1/organizations/{organization_id}/knowledge-taxonomies/versions
PATCH  /api/v1/organizations/{organization_id}/knowledge-taxonomies/versions/{version_id}
POST   /api/v1/organizations/{organization_id}/knowledge-taxonomies/versions/{version_id}/validate
POST   /api/v1/organizations/{organization_id}/knowledge-taxonomies/versions/{version_id}/impact-preview
POST   /api/v1/organizations/{organization_id}/knowledge-taxonomies/versions/{version_id}/publish
```

- Document type 목록은 stable type ID, bounded display label, capability flags, state와 exact
  registry version만 반환한다. Runtime implementation detail과 arbitrary organization extension은
  반환하지 않는다.
- Taxonomy path의 organization은 active `X-Organization-Id`와 일치해야 한다. Full taxonomy
  version/history와 author/publish surface는 Organization manager만 사용할 수 있다. KB 편집자는
  organization-wide management projection 대신 아래 KB-scoped `classification/options`의 bounded
  current picker projection만 사용한다.
- Version list는 bounded cursor pagination과 stable `created_at DESC, version_id DESC` tie-break를
  사용하고 state, exact version, safe validation/publish summary만 반환한다. Version detail은 manager가
  기존 draft를 다시 열거나 immutable published/superseded snapshot을 검사할 수 있는 bounded authoring
  projection과 server-owned `label_normalization_contract_version`을 반환한다. Client가 보낸 comparison key는
  저장하거나 authority로 사용하지 않는다. List/detail은 impact 대상 KB identity와 exact count를 포함하지 않는다.
- Draft만 PATCH할 수 있다. Published version은 immutable하다.
- Draft create response와 list/detail의 draft projection은 `draft_revision`을 반환한다. Published와
  superseded projection은 immutable version을 반환하고 mutable draft revision을 authority로 제공하지
  않는다. PATCH는
  `expected_draft_revision`을 요구하고 material change마다 revision을 증가시킨다. Current revision과
  normalized payload가 같으면 `status=unchanged`이며 revision/audit를 만들지 않고, stale expected
  revision은 payload가 같아도 `409 knowledge.taxonomy_draft_revision_conflict`다.
- V1 version create/PATCH는 `topics` complete array 하나를 사용하고 detail도 topic pagination 없이 complete
  forest를 반환한다. Topic definition은 최대 1,000개, 모든 topic의 candidate replacement reference 합계는
  최대 2,000개다. Server는 duplicate 제거와 normalization 전에 raw array count와 각 raw label의 UTF-8
  512 byte bound를 검사한다. Pinned normalization 직후 normalized comparison key의 non-empty와 UTF-8
  512 byte bound를 검사하고 그 뒤에만 sibling uniqueness, graph traversal과 DB/provider/mutation을 수행한다.
  각 초과 입력은 `422 knowledge.taxonomy_topic_count_limit_exceeded`,
  `422 knowledge.taxonomy_replacement_edge_limit_exceeded` 또는
  `422 knowledge.taxonomy_label_invalid`이며 draft revision과 audit를 만들지 않는다.
- Stable `topic_id`는 server-issued다. Existing topic entry는 `topic_id`를 요구하고, 새 topic entry는
  `topic_id` 없이 draft 안에서 unique한 UUID `draft_topic_key`를 요구한다. `parent_topic_ref`와
  `replacement_topic_refs` element는 `{"topic_id": "opaque"}` 또는
  `{"draft_topic_key": "uuid"}` 중 정확히 하나인 discriminated reference다.
  새 key는 같은 allocation request의 relation에서만 반복 참조할 수 있다. Allocation 성공 뒤 subsequent
  mutation은 detail mapping으로 복구한 server `topic_id`를 사용한다.
- Successful create/PATCH response와 mutable draft detail은 `created_topic_mappings`의
  `{"draft_topic_key": "uuid", "topic_id": "opaque"}` pair와 각 새 topic entry의 server-issued `topic_id`를
  반환한다. Mapping은 current complete-tree topic에 한정해 보존하고 수는 current topic 수 이하, 최대 1,000개다.
  Complete PATCH가 topic을 제거하면 mapping도 같은 CAS transaction에서 제거한다. 이후 같은 key를 다시 제출하면
  새 stable ID를 발급하고 제거된 ID를 재사용하지 않는다. Topic definition key는 allocation request 안에서 unique하다.
- Missing/invalid/non-unique/out-of-draft key, key와 existing `topic_id` 동시 지정 또는 잘못된 discriminated
  reference shape는 mutation 없이 `422 knowledge.taxonomy_draft_topic_reference_invalid`다. Current-mapped key를
  이후 mutation의 topic definition 또는 relation reference로 다시 제출하면
  `409 knowledge.taxonomy_draft_topic_key_conflict`이며 ID/mapping/revision partial write가 없다. 응답 유실 뒤
  old revision retry는 unchanged 성공이 아니라 stale conflict이고 Client는 detail에서 current revision과 mapping을
  복구한다. Published/superseded version은 `draft_topic_key`와 `created_topic_mappings`를 반환하지 않는다.
- Topic 0개인 draft는 valid empty forest다. Validate/publish는 empty를 별도 schema error로 거부하지 않고
  cycle, orphan, duplicate stable ID, invalid parent/replacement, deprecated reference와 current profile-policy
  compatibility를 검사한다. Published history에서 deprecated 또는 제거되어 tombstone이 된 stable ID를
  active topic이나 다른 logical identity로 재사용하면
  `409 knowledge.taxonomy_topic_id_reuse_conflict`다. Current policy에 topic rule이 남아 있으면 empty
  taxonomy publish는 conflict다.
- Server는 Unicode Character Database 14.0.0을 고정한 `unicode_14_0_nfkc_casefold_ws_v1` contract의
  NFKC, full Unicode case-fold, 재-NFKC와 Unicode
  whitespace trim/collapse로 label comparison key를 만든다. 같은 direct parent의 key는 유일하며 root topic은
  implicit parent를 공유한다. Empty key는 `knowledge.taxonomy_label_invalid`, sibling collision은
  `knowledge.taxonomy_sibling_label_conflict` fixed safe violation이고 unresolved violation이 있는 draft는 publish할
  수 없다. 다른 parent 아래의 같은 key는 유효하다.
- Deprecated topic request의 `replacement_topic_refs`는 같은 Organization의 candidate version에서 active인 topic만
  resolve하며 persisted/published projection은 server-issued `replacement_topic_ids`만 사용한다.
  Existing published replacement edge와 candidate edge를 합친 graph는 self-reference, missing/cross-organization target,
  cycle과 8 edge 초과 path를 거부한다. Safe violation code는
  `knowledge.taxonomy_replacement_invalid` 또는 `knowledge.taxonomy_replacement_depth_exceeded`이며 replacement는
  review hint일 뿐 assignment를 자동 변경하지 않는다.
- First-published identity와 terminal topic tombstone append는 taxonomy version/current pointer/canonical
  audit와 같은 publish transaction이다. Ledger write 또는 audit 실패는 version/pointer/tombstone을 모두
  rollback한다.
- Validate와 impact preview는 `expected_draft_revision`을 요구하고 검사한 exact draft revision과
  compatibility snapshot인 nullable current taxonomy/profile-policy version, document-type registry version,
  Organization/Platform profile catalog revision을 각각 반환한다. Impact preview는 추가로 server-owned
  `assignment_impact_snapshot_revision`, bounded affected/reindex bucket, 최대 10분 수명의 opaque
  `impact_preview_revision`과 expiry를 반환한다. Token은 Organization/actor/candidate/draft 및 전체 snapshot에
  bind된 non-capability이고 response는 `no-store`다. Publish는 `expected_draft_revision`, nullable
  `expected_current_taxonomy_version`, nullable `expected_current_profile_policy_version`,
  `expected_document_type_registry_version`, `expected_organization_profile_catalog_revision`,
  `expected_platform_profile_catalog_revision`,
  `expected_impact_bucket_contract_version`,
  `expected_impact_preview_revision`과 literal `impact_acknowledged=true`를 모두 요구한다.
- 최초 publish의 `expected_current_taxonomy_version=null`은 current taxonomy pointer가 실제로 없다는 exact
  precondition이다. Publish transaction은 pointer absence를 잠그거나 conditional CAS해 null에서 candidate로
  전환한다. 이미 current가 있는데 null을 보내거나 current가 없는데 non-null version을 보내면 아래 snapshot
  conflict이며, 최초 profile-policy publish의 nullable current policy pointer에도 같은 규칙을 적용한다.
- Publish transaction은 candidate taxonomy에 대해 current profile policy를 다시 validate하고 위 pointer/
  revision을 conditional CAS 또는 동등한 serializable boundary로 검증한다. 어느 하나라도 stale이면
  mutation/audit 없이 `409 knowledge.taxonomy_publish_snapshot_conflict`이며 last-write-wins로 current
  version을 교체하지 않는다.
- Taxonomy와 profile-policy publish는 같은 Organization classification-policy coordination row 또는 동등한
  shared serialization primitive를 사용한다. 각 endpoint가 자기 nullable current pointer만 CAS해서는 안 된다.
  Catalog lifecycle/reference race를 포함한 lock order는 applicable Organization catalog, Platform catalog,
  coordination, taxonomy current, profile-policy current 순서이며 사용하지 않는 catalog row만 생략한다.
- Impact preview는 affected assignment/reindex 수를 bounded bucket으로 반환하고 hidden KB,
  document, source identity와 exact denied count를 노출하지 않는다. Impact가 0개이거나 candidate가 non-empty여도
  publish precondition을 생략하지 않는다. Publish transaction은 permission, token scope/actor/expiry,
  exact draft/current compatibility와 assignment impact snapshot revision을 다시 검증한다. Missing, false,
  expired 또는 stale acknowledgement는 mutation/audit 없이
  `409 knowledge.taxonomy_impact_preview_stale`다.
- Impact bucket contract는 `taxonomy_impact_bucket_v1`이다. 두 count를 같은 authoritative snapshot에서
  독립 계산하고 `none=0`, `small=1..10`, `medium=11..100`, `large=101+`로 변환한다.
  `affected_assignment_count`는 같은 Organization의 processing-eligible active KB/document current
  assignment 중 candidate와 current taxonomy의 topic-axis projection tuple이 다른 row를 하나씩 센다. Tuple은
  freshness와 resolver-eligible topic ID set/primary다. Exact taxonomy version
  ID 차이만으로 세지 않고 이미 stale인 assignment가 candidate에서도 같은 tuple을 유지하면 제외한다.
  Null-taxonomy empty assignment의 first-publish 또는 prior-version empty assignment의 current-validation처럼
  tuple이 바뀌는 영향은 포함하고 assignment가 없는 KB는 제외한다.
  `reindex_candidate_count`는 affected subset 중 current active-ready artifact가 있는 row만 평가한다. Current
  Processing Decision과 비교 가능한 `materialization_input_fingerprint`가 있으면 topic replacement나 human
  confirmation을 가정하지 않은 candidate fingerprint가 다른 row만 센다. Active-ready legacy artifact는 있지만
  current decision 또는 비교 가능한 fingerprint가 없으면 동일 materialization을 증명할 수 없으므로
  보수적으로 포함한다. Resolved scoped profile ref revision/source/status만 바뀌고 normalized materialization
  fingerprint가 같음이 증명되면 제외하며 새 Decision Manifest와 `satisfied_existing`으로 처리한다. 항상
  affected count 이하다.
  Archived/deleted KB/document 또는 processing-ineligible target은 두 count에서 제외한다. Artifact availability
  `purging|purged`는 assignment projection 차이가 있으면 affected에는 포함하지만 active-ready artifact가 아니므로
  reindex candidate에서는 제외한다. Response/publish
  precondition과 opaque token은 `impact_bucket_contract_version`을 bind하고, threshold/predicate가 바뀌면
  새 contract version을 사용해 old token을 stale 처리한다. Exact count와 hidden identity는 response,
  token plaintext와 audit에 넣지 않으며 publish는 assignment 변경이나 reindex job 생성을 뜻하지 않는다.
- Assignment current pointer, canonical content 또는 아래 processing impact basis를 바꾸는 mutation은 같은
  transaction에서 `assignment_impact_snapshot_revision`을 전진시킨다. 비동기 projection이나 우연히 같은 impact
  bucket은 old acknowledgement를 유효하게 만들지 않는다.
- 증가 집합은 normalized no-op을 제외한 assignment create/replace/delete, manual takeover, suggestion accept,
  current-validation, axis lock/unlock, 새 canonical materialization input, impact scope를 바꾸는 KB/document lifecycle,
  explicit profile override set/change/clear, current Processing Decision pointer와 active artifact pointer 또는
  `ready|purging|purged` availability 전이를 포함한다. Suggestion create/reject/expire, read/preview, 같은 canonical input의
  source generation, staging build/job/manifest 생성과 current pointer/availability no-op은 증가시키지 않는다.
  일반 authoritative mutation은 revision 증가, pointer/availability mutation과 canonical audit를 같은 Unit of
  Work에서 commit 또는 rollback한다. Artifact purge는 pre-delete transaction에서 `purging`, durable intent와
  첫 impact revision을 함께 확정한 뒤 external delete를 수행한다. Physical deletion 확인 뒤 completion
  transaction에서 tombstone, `purged`, 다음 impact revision과
  `knowledge.processing_artifact.purged` audit를 함께 확정한다. Completion이 실패하면 삭제된 artifact를
  `ready`로 rollback하지 않고 non-retrievable `purging`으로 유지해 reconciler가 같은 generation으로
  idempotent하게 완성한다.

Impact preview response와 publish request의 contract shape는 다음 필드를 포함한다.

```json
{
  "draft_revision": 7,
  "current_taxonomy_version": "opaque-version-or-null",
  "current_profile_policy_version": "opaque-version-or-null",
  "document_type_registry_version": "opaque-version",
  "organization_profile_catalog_revision": "opaque-revision",
  "platform_profile_catalog_revision": "opaque-revision",
  "assignment_impact_snapshot_revision": "opaque-revision",
  "impact_bucket_contract_version": "taxonomy_impact_bucket_v1",
  "affected_assignment_bucket": "none|small|medium|large",
  "reindex_candidate_bucket": "none|small|medium|large",
  "impact_preview_revision": "opaque-token",
  "expires_at": "RFC3339"
}
```

```json
{
  "expected_draft_revision": 7,
  "expected_current_taxonomy_version": "opaque-version-or-null",
  "expected_current_profile_policy_version": "opaque-version-or-null",
  "expected_document_type_registry_version": "opaque-version",
  "expected_organization_profile_catalog_revision": "opaque-revision",
  "expected_platform_profile_catalog_revision": "opaque-revision",
  "expected_impact_bucket_contract_version": "taxonomy_impact_bucket_v1",
  "expected_impact_preview_revision": "opaque-token",
  "impact_acknowledged": true
}
```

- Version create/PATCH는 각각 `knowledge.taxonomy_draft.created`, `knowledge.taxonomy_draft.updated`, publish는
  `knowledge.taxonomy.published` canonical audit와 같은 transaction이다. Normalized no-op, list/detail,
  validate와 impact preview는 mutation audit를 만들지 않으며 manager authoring response는 `Cache-Control:
  no-store`를 사용한다.

#### Effective Classification

```text
GET   /api/v1/knowledge/kbs/{kb_id}/classification
GET   /api/v1/knowledge/kbs/{kb_id}/classification/options
PUT   /api/v1/knowledge/kbs/{kb_id}/classification
POST  /api/v1/knowledge/kbs/{kb_id}/classification/validate-current
POST  /api/v1/knowledge/kbs/{kb_id}/classification/lock
POST  /api/v1/knowledge/kbs/{kb_id}/classification/unlock
GET   /api/v1/knowledge/kbs/{kb_id}/classification/suggestions
POST  /api/v1/knowledge/kbs/{kb_id}/classification/suggestions/{suggestion_id}/accept-preview
POST  /api/v1/knowledge/kbs/{kb_id}/classification/suggestions/{suggestion_id}/accept
POST  /api/v1/knowledge/kbs/{kb_id}/classification/suggestions/{suggestion_id}/reject
```

`classification` GET은 KB `read`, `classification/options`는 KB `write` 또는 각각의 Organization manager
override를 요구한다. Source-managed KB의 모든 classification read/mutation은 parent ownership과 required KB
action을 확인한 뒤 assignment/lock row의 lookup, effective axis/security projection 또는 mutation 전에 fresh
requester source authorization과 applicable display policy를 통과해야 한다. GET/options뿐 아니라 assignment PUT,
current-validation과 lock/unlock에도 같은 gate를 적용한다. Missing/revoked/stale/denied는
`404 resource.hidden`이며 assignment 존재, stable ID, axis source/lock과 security classification을 반환하거나
변경하지 않는다. Mutation은 external authorization session을 종료한 뒤 bounded revision/watermark를 commit
직전에 current KB authority와 함께 다시 검증하고 revoke winner는 assignment/lock, impact snapshot, audit와
reindex projection을 만들지 않는다. Manual KB에는 이 source gate를 적용하지 않는다.

Target `PUT`은 partial topic merge가 아니라 complete effective set과 변경할 axis를 함께 제출해 server가
atomic assignment revision을 만드는 command다. Request 후보는 다음 필드를 가진다.

```json
{
  "canonical_content_revision_id": "opaque-uuid",
  "expected_assignment_revision": null,
  "manual_axes": ["document_type", "topics"],
  "document_type_registry_version": "opaque-version",
  "document_type_id": "opaque-type-id",
  "taxonomy_version_id": "opaque-version",
  "topic_ids": ["opaque-topic-id"],
  "primary_topic_id": "opaque-topic-id-or-null"
}
```

Current taxonomy pointer가 없는 Organization의 type-only candidate는
`taxonomy_version_id=null`, `topic_ids=[]`, `primary_topic_id=null`을 사용한다. Current taxonomy가
존재하면 topic set이 비어 있더라도 exact current taxonomy version을 사용해야 한다.

`expected_assignment_revision`은 nullable이지만 생략할 수 없는 exact precondition이다. Current assignment가
없는 최초 PUT은 `null`을 보내고 server는 KB-scoped current assignment 부재를 잠그거나 conditional
insert/CAS해 revision 1을 만든다. Current가 있는데 null을 보내거나 current가 없는데 non-null revision을
보내면 `409 knowledge.classification_assignment_revision_conflict`다. Sentinel revision과 read 뒤 무조건
insert를 사용하지 않으며 같은 부재를 관찰한 first PUT과 suggestion accept는 하나만 commit한다.

- Server는 request의 organization, display label, path, confidence와 client-supplied resolved profile ref를 authority로
  사용하지 않는다.
- Manual assignment request는 `reason_code` 또는 free-text reason을 받지 않는다. First create 또는 selected
  axis 중 하나라도 normalized value가 바뀌면 server가 fixed `manual_assignment`, selected value는 모두 같고
  non-manual source만 `manual`로 바뀌면 fixed `manual_takeover`를 파생한다. Value와 authority가 함께 바뀌면
  `manual_assignment`가 우선한다. `reason_code`를 포함한 unknown top-level field는 DB 조회와
  assignment/audit mutation 전에 `422 knowledge.classification_request_invalid`로 거부하고 canonical audit에는
  server-derived reason만 기록한다.
Successful first manual PUT은 `knowledge.classification_assignment.created`, existing assignment replace/takeover는
`knowledge.classification_assignment.updated`를 사용한다. Lock/unlock은 각각
`knowledge.classification_assignment.locked`, `knowledge.classification_assignment.unlocked`, current-validation은
`knowledge.classification.revalidated`, suggestion accept/reject는 각각
`knowledge.classification_suggestion.accepted`, `knowledge.classification_suggestion.rejected`를 사용한다. 각 action은 authoritative assignment/outcome과
같은 transaction에서 한 번만 기록하며 normalized unchanged 또는 exact terminal retry는 새 action을 만들지 않는다.


- GET response는 ADR-0007의 `security_classification`을 read-only로 포함할 수 있지만 이 `PUT`,
  lock/unlock과 suggestion review는 security classification을 변경하지 않는다.
- `topic_ids`는 중복 없는 최대 32개 배열이고 `primary_topic_id`는 null이거나 같은 set의 member다. 배열 길이는
  중복 제거 전에 검사하며 duplicate를 자동 제거하지 않는다. 33개 이상은 DB/provider 조회 전에
  `422 knowledge.classification_topic_limit_exceeded`다. Server는 유효한 배열만 unordered canonical set으로
  정규화한다.
- `taxonomy_version_id`는 nullable이지만 생략할 수 없는 required field다. Null인데 topic set이 non-empty이거나
  primary가 non-null이면 `422 knowledge.classification_taxonomy_shape_invalid`다.
- Request taxonomy version은 nullable current pointer의 exact snapshot이다. Current taxonomy가 없을 때만
  null + empty topic set + null primary를 허용하고, current pointer가 존재하면 empty taxonomy version을
  포함해 exact version을 요구한다. Request version과 current taxonomy/registry/assignment가 다르면
  mutation/audit 없이 `409 knowledge.classification_taxonomy_snapshot_conflict`다. Cross-organization 또는
  hidden version은 snapshot 비교 전에 ownership-first safe `404`다.
- `manual_axes`는 non-empty, duplicate 없는
  `document_type|topics` subset이다. 선택한 axis만 unlocked manual replacement/takeover 대상이고, 선택하지 않은
  axis의 normalized request value는 current effective value와 같아야 하며 server가 value/source/lock과 최초
  assigned snapshot을 그대로 보존한다. `document_type` axis value는 `document_type_id`, `topics` axis value는
  unordered `topic_ids`와 `primary_topic_id` tuple이다. Registry/taxonomy revision fields는 두 axis의 current
  validation precondition이며 unselected axis provenance를 다시 쓰라는 뜻이 아니다. Current assignment가 없는
  first create는 두 axis를 모두 선택해야 한다.
  Empty/duplicate/unknown axis 또는 incomplete first create는
  `422 knowledge.classification_manual_axes_invalid`, unselected axis value mismatch는
  `409 knowledge.classification_unselected_axis_conflict`, selected locked axis는
  `409 knowledge.classification_manual_authority_conflict`이며 assignment/audit가 없다.
- Current expected revision, normalized complete set과 selected axes가 current state와 같고 selected axis source가
  모두 이미 `manual`이면 `200 status=unchanged`로 current result를 반환하고 revision/audit/reindex를 만들지
  않는다. Selected axis에 deterministic rule, `accepted_suggestion`, fallback 등 non-manual source가 있으면
  같은 value의 명시적 `PUT`도 그 axis의 manual takeover이므로 새 complete assignment revision과 canonical
  audit를 만들고 selected source만 `manual`로 바꾼다. Unselected non-manual 또는 locked axis는 보존한다. Current
  resolver state와 `reindex_required` projection을 다시 계산하지만 durable reindex job이나 active pointer를
  자동 변경하지 않는다. 이후 명시적 admission에서 materialization input이 같으면
  `satisfied_existing`이다. Expected revision이 stale하면 desired set이 같아도 `409`다.
  Canonical audit는 동일 effective IDs와 assignment revision, before/after axis별 authority source 및 fixed manual
  takeover reason을 저장하되 raw labels/content를 포함하지 않는다.
- Suggestion candidate는 `proposed_axes`를 가진다. Accept request는 required nullable
  `expected_assignment_revision`과 non-empty `accepted_axes`를 보내며, accepted axes는 candidate의 subset이어야
  한다. Server는 선택하지 않은 axis의 value/source/lock을 보존하고 immutable candidate의 선택 axis를 적용한
  complete assignment를 materialize해 한 revision으로 commit한다. 이는 Client-supplied partial merge가 아니다.
  `accepted_axes`의 값은 `document_type|topics`이고 empty, duplicate, unknown 또는 candidate 밖 axis는
  `422 knowledge.classification_suggestion_axes_invalid`다. Current assignment가 없으면 selected candidate로
  required document type과 유효한 complete assignment를 만들 수 있어야 하며 그렇지 않으면 같은 fixed error다.
  성공한 subset accept도 suggestion row 전체를 terminal `accepted`로 바꾸고 provenance에 `accepted_axes`를
  저장한다. 선택하지 않은 proposed axis는 적용하지 않고 폐기하며 이후 retry나 별도 reviewable child로 남기지
  않는다. Suggestion candidate의 topic axis와 accept 결과도 최대 32개 bound를 적용한다.
  Expired/rejected/abstained suggestion은 accept할 수 없다. 선택한 current axis가 locked 또는 unlocked manual assignment이면
  `409 knowledge.classification_manual_authority_conflict`이며 assignment와 suggestion state를 모두
  유지한다. 선택하지 않은 manual axis는 그대로 보존할 수 있다. Blocked candidate를 의도적으로 채택하려면
  일반 `PUT`의 complete set과 blocked candidate axis를 `manual_axes`로 제출한다. Selected source만 `manual`로
  전환하고 unselected value/source/lock은 보존한다.
- Suggestion accept transaction은 assignment revision, suggestion `accepted` outcome과 canonical audit를
  함께 확정한다. Assignment/audit에는 opaque suggestion ref, canonical content/taxonomy/registry refs,
  accepted axes와 tagged `generator_kind=deterministic_rule|ai_classifier` provenance를 남긴다. 두 kind 모두
  immutable `generator_contract_ref`가 필수다. Deterministic kind는 approved rule-set/version만 저장하고
  provider model, prompt-template, calibration 및 confidence field는 absent다. AI kind는 classifier policy,
  prompt-template contract, model catalog/effective model, calibration revision/state와 bounded confidence bucket이
  필수다. Kind와 맞지 않는 fake/non-applicable ref를 만들지 않는다. Raw prompt/completion/rationale, raw score,
  provider response와 content는 저장하지 않으며 suggestion row purge가 snapshot을 cascade 삭제하지 않는다.
- Reject는 current `suggested` row와 fresh source authorization을 다시 검증하고 assignment/reindex를 변경하지
  않은 채 `rejected` outcome, bounded fixed `reason_code`와
  `knowledge.classification_suggestion.rejected` canonical audit를 같은 transaction에서 확정한다. Audit failure는
  outcome을 rollback하고 exact terminal retry는 기존 safe result를 반환하며 새 audit를 만들지 않는다.
- Current taxonomy가 없을 때 suggestion의 taxonomy version도 null이고 topic candidate는 empty여야 한다.
  최초 taxonomy publish를 포함해 current pointer가 바뀌면 아직 `suggested`인 type-only candidate는
  expired가 되며 새 taxonomy version으로 자동 승계하거나 topic을 보충하지 않는다. 이미 accepted/rejected/
  abstained인 terminal outcome은 다시 쓰지 않는다.
- Suggestion accept는 nullable current taxonomy pointer와 candidate snapshot을 assignment revision, terminal
  outcome과 canonical audit를 확정하는 transaction 안에서 다시 검증한다. Null-to-version race loser는
  assignment/outcome/audit를 남기지 않는다. Accept가 먼저 commit되면 뒤의 taxonomy publish는 accepted
  outcome/provenance를 유지하고 assignment만 current-validation-required로 projection한다.
- Lock/unlock은 KB `manage` 또는 ADR-0034 Organization manager override가 필요하다. Lock request는 target
  `axis`, non-null `expected_assignment_revision`과 `expected_canonical_content_revision_id`를 요구한다. Server는
  current assignment와 canonical content pointer를 같은 transaction에서 잠그거나 동등한 CAS로 다시 검증하고
  mismatch를 `409 knowledge.classification_lock_snapshot_conflict`로 닫는다. Unlocked axis의 실제
  `false -> true` 전이는 target freshness가 `current`일 때만 허용한다. `current_validation_required`는
  `validate-current`, `review_required`는 current option을 사용하고 target axis를 `manual_axes`에 넣은 complete
  assignment `PUT`을 먼저 요구하고
  invalid axis가 이미 locked이면 `unlock` 뒤 complete `PUT` 순서로 복구한다. Write-only actor에게 unlock을
  허용하지 않는다. 그렇지 않으면 `409 knowledge.classification_lock_not_current`이며 assignment/audit를 변경하지 않는다. 이미
  locked인 exact-state request는 `status=unchanged`일 수 있다. Unlock request는 target `axis`와 non-null
  `expected_assignment_revision`만 요구한다. Unlock은 authority-reduction recovery이므로 stale content 또는
  missing/deprecated reference에서도 허용하되 current assignment를 transaction 안에서 다시 검증하고 complete
  revision의 freshness/resolver projection을 server current state로 재계산한다. Locked axis를 일반 `PUT` 또는
  suggestion accept로 덮어쓰면 `409`다.
- Current canonical materialization input hash가 assignment의 last-validated content와 다르면 GET은 axis별
  `freshness=current|review_recommended|current_validation_required|review_required`와 `resolver_eligible`을
  반환한다. Locked axis는 `review_recommended`, eligible이고 unlocked axis는 source가 manual/rule/
  accepted-suggestion/fallback인지와 무관하게 `current_validation_required`, ineligible이다. Ineligible value는
  provenance/display에는 남지만 hard filter, ranking, profile mapping과 materialized assignment field에 사용하지
  않는다. Resolver는 eligible axis 또는 general profile로 fallback한다. Canonical input hash가 같은 source
  generation/profile-only output 변화는 freshness를 바꾸지 않는다.
- `POST .../classification/validate-current`는 assignment replacement가 아니다. Request는
  `canonical_content_revision_id`, `expected_assignment_revision`,
  `expected_document_type_registry_version`, nullable `expected_taxonomy_version_id`와
  `validation_reason_code`를 요구한다. Reason은
  `content_reviewed|taxonomy_updated|registry_updated|combined_review` bounded enum이다.
  Server는 current canonical content revision/hash와 current pointers 및 assignment를 다시 읽고 last-validated
  snapshot 대비 changed dimensions를 `content|taxonomy|document_type_registry` 집합으로 계산한다. 정확히 하나만
  바뀌면 각각 `content_reviewed|taxonomy_updated|registry_updated`, 둘 이상이면 `combined_review`만 허용한다.
  Changed dimension이 없으면 request reason을 해석하거나 audit에 저장하지 않고 `status=unchanged`로 종료한다.
  Content가 바뀌었는데 content-confirming reason이 아니면
  `422 knowledge.classification_content_confirmation_required`, 그 밖의 reason/change-set mismatch는
  `422 knowledge.classification_validation_reason_mismatch`이며 revision/audit/projection이 없다.
  Content-confirming은 server-computed set에 `content`가 포함된 validation만 뜻하며 KB `write`와 별도로 effective
  `content_read` 및 applicable current display/raw policy를 요구한다.
  Source-managed KB는 changed dimension과 무관하게 assignment lookup/mutation 전에 fresh source
  authorization/display gate를 평가하고 authoritative revision/watermark를 validation commit과 직렬화한다.
  Taxonomy/registry-only validation은 `content_read`와 raw policy를 요구하지 않지만 이 resource-hiding gate는
  생략하지 않는다. Organization manager도 이를 우회하지 않으며 revoked/stale/denied는
  `404 resource.hidden`으로 content와 validation 결과를 숨긴다.
  Stable IDs가 모두 유효하면 effective set,
  axis별 source/lock, effective-from과 최초 assigned snapshot을 보존한 새 revision으로
  `last_validated_*`만 전진시키고 `validation_source=manual_confirmation`을 기록한다. Original axis source를
  manual로 변경하지 않는다. 성공은
  `knowledge.classification.revalidated` canonical audit와 같은 transaction에서 확정하고 current resolver
  state와 `reindex_required` projection을 다시 계산한다. Validation 자체는 reindex job 또는 active pointer
  변경을 시작하지 않는다. 이미 current면 `status=unchanged`이고 revision/audit/reindex projection을
  바꾸지 않는다. Missing/deprecated reference는
  `200 status=review_required`로 assignment와 last-validated refs를 유지한다. 이 결과는 같은 validation 재시도를
  권장하지 않으며 Client는 current option과 invalid axis의 `manual_axes`를 사용한 complete assignment
  replacement를 제공하고 반대 axis의 value/source/lock을 보존한다. Invalid axis가
  locked이면 KB `manage` 또는 Organization manager의 unlock이 먼저 필요하다. Stale expected
  revision/version은 값이 여전히 유효해 보여도 `409`다.
- Empty-topic assignment 뒤 assigned taxonomy snapshot보다 새 taxonomy가 publish된 상태는 invalid topic이
  아니므로 GET은 topic axis를 `freshness=current_validation_required`, `resolver_eligible=false`,
  `review_required=false`로 구분하고 document-type axis freshness는 독립적으로 유지한다. 여기에는
  null-taxonomy assignment 뒤 최초 empty/non-empty publish와 prior-taxonomy empty-topic assignment 뒤 empty
  successor가 포함된다. Assignment-level
  `current_validation_required=true`는 UI용 derived summary일 뿐 resolver 판단을 대체하지 않는다. Explicit
  current-validation은 effective set과 axis별 source/lock/최초 assigned snapshot을 보존하고 last-validated
  taxonomy ref만 전진시킨다.

```json
{
  "canonical_content_revision_id": "opaque-uuid",
  "expected_assignment_revision": 4,
  "expected_document_type_registry_version": "opaque-version",
  "expected_taxonomy_version_id": "opaque-version-or-null",
  "validation_reason_code": "content_reviewed"
}
```

- Target document-level KB cutover 전 여러 Document가 있는 legacy KB에는 canonical route를
  사용하지 않는다. Transitional API는 exact
  `/api/v1/knowledge/{kb_id}/documents/{document_id}/classification`과
  `POST /api/v1/knowledge/{kb_id}/documents/{document_id}/classification/validate-current` 및
  legacy `document_version_id`를 요구한다. Server는 이를 canonical content revision/hash에 매핑하고
  대상이 모호하면 `409 knowledge.classification_migration_required`다.

Safe response는 다음 범위로 제한한다.

- Stable type/topic IDs와 bounded approved labels
- Nullable exact taxonomy pointer와 registry/assignment revision. Current taxonomy가 없으면 options response는
  taxonomy version `null`과 empty topic options를 반환한다.
- Assignment의 effective-from/last-validated canonical content safe refs
- `authority.document_type`과 `authority.topics` 각각의
  `source=manual|rule|accepted_suggestion|fallback`, `locked`,
  `freshness=current|review_recommended|current_validation_required|review_required`, `resolver_eligible`.
  단일 합성 `source`나 assignment-level freshness로 mixed authority를 축약하지 않는다.
- Assignment-level `reindex_required` boolean
- Axis freshness의 OR projection인 derived `current_validation_required` boolean. Resolver와 권한 판정은 이
  summary가 아니라 각 axis의 `freshness`와 `resolver_eligible`을 사용한다. Assigned taxonomy snapshot이
  current보다 뒤처졌지만 invalid topic reference가 없는 경우와 null-taxonomy empty set 뒤 최초 publish도
  summary에 포함한다.
- Profile safe ID/revision은 profile preview 또는 effective manifest projection에서만 제공
- Suggestion의 bounded state, calibrated 여부/confidence bucket과 fixed safe reason

Raw document excerpt, source title/path/URL, prompt/completion/rationale, model raw response,
credential, hidden candidate ID와 exact denied count는 반환하지 않는다. Classification 응답은
`Cache-Control: no-store`를 사용한다.

`GET .../classification/options`는 target KB `write` 또는 같은 active organization의 Organization manager
override를 요구하고 current platform document type
registry와 organization taxonomy에서 신규 assignment에 사용할 수 있는 bounded option만 반환한다.
Deprecated/hidden topic, authoring metadata, impact count와 다른 KB assignment를 반환하지 않는다.
Response는 exact registry/taxonomy version과 stable ID, bounded approved label/path, disabled reason만
포함하며 Client는 이 version을 mutation request에 그대로 돌려보낸다. Existing assignment가
`current_validation_required`이면 options response는 server-computed bounded
`current_validation_changed_dimensions: content|taxonomy|document_type_registry[]`와 generic
`can_confirm_current_content` boolean도 반환한다. 이 값은 raw content/hash/source identity를 포함하지 않고
submit capability가 아니며 validation transaction이 current snapshot과 authorization을 다시 계산한다.

`GET .../classification/suggestions`는 optional
`state=suggested|accepted|rejected|abstained|expired`, 기본 `suggested`, 기본 `limit=25`, 최대 `limit=50`과
opaque `cursor`를 받는다. Ordering은 stable `created_at DESC, suggestion_id DESC` keyset이고 cursor는
organization, KB, state filter와 ordering tuple에 bind된다. 동일 timestamp, purge/expiry 사이의 page에서도
duplicate/skip을 만들지 않으며 `total_count`와 hidden/denied count를 반환하지 않는다. Malformed 또는 다른
scope/filter cursor는 입력을 echo하지 않는 `422 knowledge.classification_suggestion_cursor_invalid`다.

List response는 `items`와 nullable `next_cursor`만 가진다. 각 item은 opaque `suggestion_id`, state,
`proposed_axes`, allowlisted candidate stable IDs와 bounded labels, created/expires timestamp, fixed safe reason,
`generator_kind`와 opaque `generator_contract_ref`를 공통으로 포함한다. Deterministic item에는 approved
rule-set/version safe projection만, AI item에는 calibrated 여부와 bounded confidence bucket만 추가하고
non-applicable provider field는 absent다. `total_count`, raw score, content excerpt, source identity,
prompt/rationale와 provider payload는 포함하지 않는다.

Accept-preview는 accept와 같은 `expected_assignment_revision` 및 non-empty `accepted_axes` request shape를
사용한다.

```json
{
  "expected_assignment_revision": null,
  "accepted_axes": ["document_type", "topics"]
}
```

Response는 다음 bounded safe projection이다.

```json
{
  "accept_preview_revision": "opaque-preview-revision",
  "expires_at": "2026-01-01T00:10:00Z",
  "assignment_effect": {
    "document_type": "authority_changed",
    "topics": "value_changed"
  },
  "profile_resolution_changed": true,
  "reindex_required": true,
  "active_ready_available": true
}
```

`assignment_effect` 값은 `unchanged|value_changed|authority_changed` enum이다. Value가 바뀌면 source/authority도
함께 바뀌는지와 무관하게 `value_changed`, value는 같고 source/authority만 바뀌면 `authority_changed`, 둘 다
같으면 `unchanged`다. `accepted_axes`에 없는 axis도 `unchanged`로 반환한다.

Preview는 parent/KB permission과 applicable fresh source authorization/display gate를 통과한 뒤 immutable
candidate를 hypothetically 적용한다. Organization/KB/actor, suggestion/state/candidate snapshot,
accepted axes, current canonical content, nullable assignment/taxonomy, registry, profile policy/catalog,
override, resolved scoped profile ref, nullable current Processing Decision Manifest ref와
resolver/materialization input fingerprints, nullable active Artifact Build Manifest ref·generation·availability와
build materialization/integrity fingerprints를 최대 10분 opaque token의 server-side validation state에 bind한다.
Raw content, hidden identity, exact count/cost와 내부 fingerprint는 response 또는 token plaintext에 포함하지 않고
`Cache-Control: no-store`를 사용한다. Token은 capability가 아니다.

Accept는 `expected_accept_preview_revision`을 필수로 보낸다. Server는 fresh source/display gate를 candidate와
token lookup보다 먼저 재평가하고 외부 authorization session을 닫은 뒤 bounded revision/watermark만 mutation
transaction에 전달한다. 같은 transaction에서 scope, expiry, 전체 preview vector와 authorization
revision/watermark를 commit 직전에 다시 검증한다. Candidate/state, accepted axes, assignment,
content/taxonomy/registry, resolver/materialization input, current decision 또는 active artifact identity/state가
바뀌면 response boolean이 같아도 mutation/audit 없이
`409 knowledge.classification_suggestion_accept_preview_stale`다. Fresh authorization을 통과한 exact terminal
retry는 terminal outcome에 저장한 non-reversible accept request fingerprint가 같을 때 preview expiry보다
먼저 기존 safe result를 반환하고 assignment/audit/reindex intent를 반복하지 않는다. 다른 terminal request는
기존 outcome을 변경하지 않는다. Raw preview token/digest는 assignment, audit, log와 trace에 저장하지 않는다.
Accept와 reject request는 다음 shape를 사용한다.

```json
{
  "expected_assignment_revision": null,
  "expected_accept_preview_revision": "opaque-preview-revision",
  "accepted_axes": ["document_type", "topics"]
}
```

```json
{
  "reason_code": "incorrect_topics"
}
```

Reject `reason_code`는 `incorrect_type|incorrect_topics|ambiguous|outdated|other_safe` bounded enum이고
free-text reason을 받지 않는다.

Source-managed KB에서는 suggestion list의 각 page와 accept-preview/accept/reject가 parent ownership 및 KB permission을
확인한 뒤 suggestion row를 scan/lookup하거나 candidate를 projection하기 전에 fresh source authorization과
display policy를 다시 평가한다. Review mutation은 source authorization revision/watermark를 commit 직전에
다시 검증한다. Revoked/stale/denied이면 `404 resource.hidden`이고 candidate identity/state/count/confidence,
terminal outcome, assignment와 audit를 반환하거나 변경하지 않는다. Organization manager, KB `manage`, cursor와
suggestion ID는 source gate를 우회하는 capability가 아니다.

#### Organization Processing Profile Catalog Lifecycle

```text
GET    /api/v1/knowledge/processing-profiles/platform-options
GET    /api/v1/organizations/{organization_id}/knowledge-processing-profiles
GET    /api/v1/organizations/{organization_id}/knowledge-processing-profiles/{profile_id}
GET    /api/v1/organizations/{organization_id}/knowledge-processing-profiles/{profile_id}/revisions
GET    /api/v1/organizations/{organization_id}/knowledge-processing-profiles/{profile_id}/revisions/{revision_id}
POST   /api/v1/organizations/{organization_id}/knowledge-processing-profiles
POST   /api/v1/organizations/{organization_id}/knowledge-processing-profiles/{profile_id}/revisions
PATCH  /api/v1/organizations/{organization_id}/knowledge-processing-profiles/{profile_id}/revisions/{revision_id}
POST   /api/v1/organizations/{organization_id}/knowledge-processing-profiles/{profile_id}/revisions/{revision_id}/validate
POST   /api/v1/organizations/{organization_id}/knowledge-processing-profiles/{profile_id}/revisions/{revision_id}/publish
POST   /api/v1/organizations/{organization_id}/knowledge-processing-profiles/{profile_id}/revisions/{revision_id}/deprecate
```

Organization path는 active organization과 일치해야 하고 모든 catalog read/author/lifecycle operation은
Organization manager 전용이다. Organization catalog list/detail/lifecycle response는
`organization_profile_catalog_revision`을 반환한다. `platform-options`는 current authenticated Organization
manager에게 approved published platform revision의 stable safe ID, bounded label, compatibility/selectability,
non-null exact `platform_default_profile_revision`과 `platform_profile_catalog_revision`을 반환하는 read-only
authoring option endpoint다. Organization API로 platform identity/revision/default pointer를 수정하거나
deprecate할 수 없다.

Platform default pointer 변경은 Organization API가 아닌 platform registry control-plane command다. Platform
registry owner/system actor만 required exact `expected_platform_profile_catalog_revision`과 `target_profile_revision_id`를
제출할 수 있다. Command는 같은 Platform catalog serialization boundary에서 target의 approved
published/selectable 상태를 다시 확인하고 pointer, incremented catalog revision과 canonical
`knowledge.processing_profile_default.changed` audit를 한 Unit of Work에서 확정한다. Stale expected revision은
`409 knowledge.processing_profile_catalog_revision_conflict`로 pointer/catalog/audit 없이 종료하고 audit 실패도
전체 mutation을 rollback한다. Audit metadata는 safe actor와 before/after Platform profile revision 및 resulting
catalog revision만 포함하며 raw profile config를 포함하지 않는다.

`POST .../knowledge-processing-profiles`는 required body로 exact empty JSON object만 받는다.

```json
{}
```

성공은 `201 Created`와 정확히 다음 first-draft projection을 반환한다.

```json
{
  "profile_id": "opaque-profile-id",
  "organization_profile_catalog_revision": "opaque-revision",
  "draft": {
    "profile_revision_id": "opaque-profile-revision-id",
    "revision_state": "draft",
    "base_revision_id": null,
    "draft_revision": 0,
    "config": null
  }
}
```

Server는 stable identity, incomplete first-draft shell과
`knowledge.processing_profile_draft.created` audit를 한 transaction에서 만든다. Draft는 selectable하지 않으므로
response의 current `organization_profile_catalog_revision`을 전진시키지 않는다. Missing body, `null`, array,
scalar 또는 어떤 unknown field라도 포함한 object는 profile repository 조회 전에
`422 knowledge.processing_profile_invalid`이며 identity/draft/audit가 0개다. First draft는 첫 successful complete
config PATCH 전까지 list/detail로 복구할 수 있지만 validate/publish할 수 없다. 첫 PATCH와 이후 PATCH는 모두
complete `processing_profile_schema_v1` config 및 `expected_draft_revision`을 요구하고 partial merge를 하지 않는다.

Successor `POST .../{profile_id}/revisions` request body는 정확히 다음 shape다.

```json
{
  "base_revision_id": "opaque-profile-revision-id"
}
```

`base_revision_id`는 path의 same-organization profile identity에 속한 immutable `published` revision이어야 한다.
Server는 지정된 revision의 allowlisted normalized config만 복제하고 새 draft에 immutable lineage로
`base_revision_id`를 기록하며 위 first-draft response와 같은 envelope에 cloned config, `draft_revision=0`과
non-null base ID를 반환한다. Missing/malformed/extra request
field는 DB 조회 전 `422 knowledge.processing_profile_invalid`, cross-organization 또는 다른 profile identity의
base는 ownership-first safe `404`, same-scope draft/deprecated/non-published base는
`409 knowledge.processing_profile_successor_base_invalid`로 draft, catalog revision과 audit 없이 닫는다. Draft
detail은 manager가 편집할 수 있는 allowlisted normalized fields만 반환한다.

- Required config는 tokenizer stable ID/version, `chunk_unit=tokens`, bounded `chunk_size_tokens`,
  `chunk_overlap_tokens`, boundary/parser contract, retrieval representation, embedding/model catalog reference와
  algorithm version이다. Overlap은 size보다 작아야 하고 legacy character 숫자를 token 값으로 변환하지 않는다.
- V1 config는 required `profile_schema_version="processing_profile_schema_v1"`을 가진다.
  `chunk_size_tokens`는 strict integer `64..8192` inclusive,
  `chunk_overlap_tokens`는 strict integer `0..floor(chunk_size_tokens / 2)` inclusive다. Boolean, string,
  float, null, 음수와 범위 밖 값은 coercion하지 않고 `422 knowledge.processing_profile_invalid`로 닫는다.
  Selected tokenizer/parser/representation/embedding catalog capability가 더 작은 current published limit를
  가지면 validate/publish는 그 limit를 추가 적용하고 V1 envelope을 넓히지 않는다. 이 transport safety bound는
  품질 default가 아니며 변경은 새 schema version으로만 도입한다.
- Credential ID/value, endpoint/secret, arbitrary provider config, executable parser option과 raw implementation
  payload는 request/response/profile/audit에서 허용하지 않는다.
- Draft PATCH/validate는 `expected_draft_revision`을 요구한다. Normalized no-op은 `status=unchanged`이고 stale
  request는 `409 knowledge.processing_profile_draft_revision_conflict`다.
- Publish는 `expected_draft_revision`과 `expected_organization_profile_catalog_revision`을 요구한다. Config schema,
  current tokenizer/parser/embedding/model capability와 owner scope를 transaction 안에서 다시 검증하고 immutable
  published revision, incremented Organization catalog revision과
  `knowledge.processing_profile.published` canonical audit를 함께 확정한다. Stale catalog는
  `409 knowledge.processing_profile_catalog_revision_conflict`다.
- Published config는 PATCH/hard-delete할 수 없다. Deprecate는 published revision과
  `expected_organization_profile_catalog_revision`을 요구하고 selectability lifecycle event, catalog revision과
  `knowledge.processing_profile.deprecated` audit를 같은 transaction에서 확정한다. Current mapping policy 또는
  current KB override direct reference가 하나라도 있으면 bounded existence만 보고
  `409 knowledge.processing_profile_in_use`로 거부한다. Historical reference와 active-ready artifact는 삭제하지
  않지만 deprecated revision은 신규 policy/override option과 `satisfied_existing` 후보에서 제외한다.
- Policy publish, override set/clear와 Platform default pointer 변경도 적용되는 exact Organization/Platform
  catalog revision을 검증한다. 이 mutation들과 deprecate는 각 catalog serialization boundary를 공유하고 두 row가
  모두 필요하면 Organization 뒤 Platform의 deterministic lock order를 사용해 deprecated revision을 가리키는
  새 current reference/default와 deprecate가 동시에 commit되지 않게 한다. Platform deprecate는 current
  policy/override direct reference뿐 아니라 current default pointer도 in-use로 취급한다. Default pointer 변경은
  새 target selectability를 같은 transaction에서 검증하고 `platform_profile_catalog_revision`을 전진시킨다.
  Deprecate가 먼저 commit되어 mandatory profile-policy preview의 catalog snapshot을 바꾸면 policy publish는
  `knowledge.processing_profile_policy_impact_preview_stale`, override set/default mutation은 각 snapshot
  conflict로 닫힌다. 새 current reference/default가 먼저 commit되면
  deprecate는 `knowledge.processing_profile_in_use`로 닫히며 어느 순서도 partial lifecycle/reference/audit를 남기지 않는다.
- Profile identity list는 `created_at DESC, profile_id DESC`, revision list는
  `created_at DESC, revision_id DESC`의 bounded cursor pagination과 stable tie-break를 사용한다. Safe list/detail은
  hidden KB/ref exact count와 raw profile config를 반환하지 않고 manager response는
  `Cache-Control: no-store`다. Version create/PATCH는 각각
  `knowledge.processing_profile_draft.created`, `knowledge.processing_profile_draft.updated`, publish/deprecate는 각각
  `knowledge.processing_profile.published`, `knowledge.processing_profile.deprecated` canonical audit와 같은 transaction이고 normalized no-op,
  read/validate는 mutation audit를 만들지 않는다.

#### Processing Profile Policy Lifecycle

```text
GET    /api/v1/organizations/{organization_id}/knowledge-processing-profile-policies/current
GET    /api/v1/organizations/{organization_id}/knowledge-processing-profile-policies/versions
GET    /api/v1/organizations/{organization_id}/knowledge-processing-profile-policies/versions/{version_id}
POST   /api/v1/organizations/{organization_id}/knowledge-processing-profile-policies/versions
PATCH  /api/v1/organizations/{organization_id}/knowledge-processing-profile-policies/versions/{version_id}
POST   /api/v1/organizations/{organization_id}/knowledge-processing-profile-policies/versions/{version_id}/validate
POST   /api/v1/organizations/{organization_id}/knowledge-processing-profile-policies/versions/{version_id}/impact-preview
POST   /api/v1/organizations/{organization_id}/knowledge-processing-profile-policies/versions/{version_id}/publish
```

Draft create `POST` body는 정확히 `{"policy": <processing_profile_policy_v1-document>}`이고 성공 response는
server-issued version ID와 `draft_revision=0`을 반환한다. Server가 omitted field를 default/merge하지 않으며
두 nullable general field가 포함된 incomplete document도 만들 수 있다. Unknown wrapper field 또는
`expected_draft_revision`이 들어오면 `422 knowledge.processing_profile_policy_invalid`다.

Draft PATCH body는 다음 complete request shape를 사용한다.

```json
{
  "expected_draft_revision": 3,
  "policy": {
    "policy_schema_version": "processing_profile_policy_v1",
    "rules": [
      {
        "match": {
          "kind": "type_primary",
          "document_type_id": "opaque-type-id",
          "primary_topic_id": "opaque-topic-id"
        },
        "profile_revision_ref": {
          "catalog_scope": "organization",
          "profile_revision_id": "opaque-profile-revision-id"
        }
      }
    ],
    "organization_general_profile": null,
    "platform_general_profile": {
      "catalog_scope": "platform",
      "profile_revision_id": "opaque-profile-revision-id"
    }
  }
}
```

`match`는 다음 strict discriminated union이다.

- `type_primary`: `document_type_id`, `primary_topic_id`만 required
- `type`: `document_type_id`만 required
- `primary`: `primary_topic_id`만 required

`rules` raw array는 duplicate 제거 전에 `0..2000`개다. Server는 matcher kind와 exact ID tuple을
normalization key로 사용하고 같은 specificity의 duplicate/conflict를 허용하지 않는다.
`profile_revision_ref`는 `catalog_scope=organization|platform`과 exact `profile_revision_id`만 가진다.
General fallback도 같은 shape지만 `organization_general_profile`은 organization scope,
`platform_general_profile`은 platform scope만 허용한다. 두 field는 required nullable이라 incomplete draft를
null로 복구할 수 있지만 validate/impact-preview/publish는 non-null Platform general을 필수로 요구한다.
PATCH wrapper는 `expected_draft_revision`과 `policy`만 허용한다. PATCH는 policy 전체 replacement이고 omitted
rule merge, JSON Patch와 per-rule partial operation을 지원하지
않는다. First draft는 `rules=[]`와 두 null general을 반환할 수 있다.
Unknown field, kind와 맞지 않는 field, invalid/missing ID shape는
`422 knowledge.processing_profile_policy_invalid`, raw 2,001번째 rule은
`422 knowledge.processing_profile_policy_rule_limit_exceeded`, normalized duplicate matcher는
`422 knowledge.processing_profile_policy_rule_conflict`다. Cross-organization/hidden reference는 safe `404`,
same-scope deprecated/incompatible reference와 publish-time Platform general null은 fixed validation failure이며
Full policy read/author/publish는 Organization manager만 가능하다. Draft rule은 stable
document-type/topic ID와 exact profile revision만 참조하고 동일 specificity duplicate/conflict,
missing required platform general fallback, deprecated/incompatible profile과 cross-organization reference를
validate에서 거부한다. Organization general fallback은 optional이며 부재 자체는 validation error가 아니다.
Validate/impact preview는 exact draft revision과 current taxonomy,
document-type registry, Organization/Platform profile catalog 및 current policy version의 compatibility snapshot을
반환하며 catalog fields는 `organization_profile_catalog_revision`과
`platform_profile_catalog_revision`으로 분리한다.
Publish는 `expected_draft_revision`, nullable `expected_current_profile_policy_version`, nullable
`expected_current_taxonomy_version`, `expected_document_type_registry_version`과
`expected_organization_profile_catalog_revision`, `expected_platform_profile_catalog_revision`을 모두 요구한다.
모든 publish는 추가로 `expected_impact_bucket_contract_version="profile_policy_impact_bucket_v1"`,
`expected_impact_preview_revision`과 literal `impact_acknowledged=true`를 요구한다. 이 세 field는 compatibility
snapshot을 대체하지 않는다.
Publish transaction은 Organization catalog row 뒤 Platform catalog row, coordination/current pointer의 deterministic
lock order 또는 동등한 serializable CAS 아래 current snapshot의 모든 rule/general fallback reference를 다시
validate한다. Preview-bound expected field/token claim이 request와 current snapshot에서 다르면 mutation/audit 없이
`409 knowledge.processing_profile_policy_impact_preview_stale`다. Fresh token과 전체 snapshot을 검증하고
serialization boundary를 획득한 뒤 coordination/current policy pointer CAS 경합을 잃은 경우에만
`409 knowledge.processing_profile_policy_publish_snapshot_conflict`를 사용한다.
`expected_current_profile_policy_version=null`은 current policy pointer가 실제로 없는 최초 publish에서만 유효하며
null-to-candidate 전환을 CAS한다.
Publish는 기존 KB reindex를 자동 시작하지 않는다. Impact preview request body는 정확히 다음 shape다.

```json
{
  "expected_draft_revision": 3
}
```

Fresh response는 다음 필드를 반환하고 `Cache-Control: no-store`를 사용한다.

```json
{
  "draft_revision": 3,
  "current_profile_policy_version": null,
  "current_taxonomy_version": null,
  "document_type_registry_version": "opaque-version",
  "organization_profile_catalog_revision": "opaque-revision",
  "platform_profile_catalog_revision": "opaque-revision",
  "assignment_impact_snapshot_revision": "opaque-revision",
  "impact_bucket_contract_version": "profile_policy_impact_bucket_v1",
  "affected_resolution_bucket": "none|small|medium|large",
  "reindex_candidate_bucket": "none|small|medium|large",
  "impact_preview_revision": "opaque-token",
  "expires_at": "RFC3339"
}
```

Current policy/taxonomy가 존재하면 위 nullable field는 exact opaque version을 반환한다. Publish request body는
preview의 client-echo compatibility field를 다음 exact shape로 제출한다.

```json
{
  "expected_draft_revision": 3,
  "expected_current_profile_policy_version": null,
  "expected_current_taxonomy_version": null,
  "expected_document_type_registry_version": "opaque-version",
  "expected_organization_profile_catalog_revision": "opaque-revision",
  "expected_platform_profile_catalog_revision": "opaque-revision",
  "expected_impact_bucket_contract_version": "profile_policy_impact_bucket_v1",
  "expected_impact_preview_revision": "opaque-token",
  "impact_acknowledged": true
}
```

Nullable current field는 preview가 반환한 null 또는 exact opaque version을 그대로 사용한다. Unknown field와
preview snapshot에서 유래하지 않은 fallback value는 허용하지 않는다.
`assignment_impact_snapshot_revision`은 opaque preview token에 bind되는 server-owned precondition이며 별도
publish request field로 받지 않는다. Server는 token claim과 current revision을 transaction 안에서 비교한다.

`profile_policy_impact_bucket_v1`은 두 internal count를 같은 authoritative snapshot에서 계산하고 각각
`none=0`, `small=1..10`, `medium=11..100`, `large=101+`로 변환한다.

- `affected_resolution_count`는 같은 Organization의 processing-eligible active KB/document를 current resolver와
  candidate resolver로 평가해 `effective_resolution_status`, `resolution_source` 또는 strict scoped
  `resolved_profile_revision_ref`가 달라지는 target을 하나씩 센다. 두 resolver는 같은 current assignment 또는
  assignment 부재, taxonomy/registry, profile catalog, override와 canonical input을 사용한다. Assignment가 없는
  target도 current Platform default/current policy general과 candidate policy general fallback으로 resolve하므로
  포함한다. Valid explicit override가 두 resolver에서 계속 우선하거나 invalid override가 계속
  `review_required`로 candidate policy 선택을 막으면 제외한다.
- `reindex_candidate_count`는 affected subset 중 current active-ready artifact가 있는 target만 평가한다. Current
  Processing Decision과 비교 가능한 `materialization_input_fingerprint`가 있으면 candidate fingerprint가 다른
  target만 세고, active-ready legacy artifact는 있지만 current decision 또는 비교 가능한 fingerprint가 없으면
  동일 materialization을 증명할 수 없으므로 보수적으로 포함한다. Active artifact가 없는 target은 affected에는
  포함될 수 있지만 reindex candidate에서는 제외한다.
- Archived/deleted KB/document 또는 processing-ineligible target은 두 count에서 제외한다. Artifact availability
  `purging|purged`는 resolver 차이를 지우지 않으므로 active·processing-eligible target이면 affected count에는
  포함할 수 있지만 active-ready artifact가 아니므로 reindex candidate에서는 제외한다. Exact count, KB/document/
  source identity와 exact denied count는 response, token plaintext와 audit에 넣지 않는다.

Opaque preview token은 최대 10분이고 Organization/actor/candidate/draft/current policy/taxonomy/registry, 두
catalog revision, assignment impact snapshot과 bucket contract version을 bind한 non-capability다. Publish는 위
request shape의 client-echo compatibility field와 acknowledgement를 제출하고 server-owned assignment impact
snapshot은 token claim으로 전달한다. Transaction 안에서 permission, token scope/expiry와
snapshot을 다시 검증한다. Missing/expired/wrong actor·scope·candidate, changed snapshot 또는 contract mismatch는
`409 knowledge.processing_profile_policy_impact_preview_stale`로 policy version/current pointer/audit 없이 닫는다.
Valid token 검증 뒤 commit CAS에서 경합을 잃은 경우는
`409 knowledge.processing_profile_policy_publish_snapshot_conflict`다. Impact가 `none`이어도 preview와
acknowledgement를 생략하지 않으며 preview/publish는 assignment 또는 reindex job을 만들지 않는다.

Draft create response와 list/detail의 draft projection은 `draft_revision`을 반환한다. Published와
superseded projection은 immutable version만 반환한다. PATCH, validate와 impact preview는
`expected_draft_revision`을 요구한다. Publish는 위 compatibility snapshot 전체를 함께 CAS한다. Current
normalized no-op PATCH는 revision/audit를 만들지 않으며 stale PATCH는 payload가 같아도
`409 knowledge.processing_profile_policy_draft_revision_conflict`다.

Version list/detail은 taxonomy lifecycle과 같은 bounded cursor pagination, stable tie-break와
ownership-first safe hiding을 사용한다. List는 safe state/version/publish summary만, detail은 기존
draft를 재개하거나 immutable published/superseded mapping을 검사하는 bounded authoring projection만
반환한다. Profile implementation config, hidden KB identity와 exact impact count는 반환하지 않는다.
Version create/PATCH는 각각 `knowledge.processing_profile_policy_draft.created`, `knowledge.processing_profile_policy_draft.updated`, publish는
`knowledge.processing_profile_policy.published` canonical audit와 같은 transaction이다. Publish audit은 safe
preview digest, acknowledged flag, bounded impact bucket과 impact snapshot revision만 기록한다. Normalized no-op,
list/detail, validate와 impact preview는 mutation audit를 만들지 않으며 manager authoring response는
`Cache-Control: no-store`를 사용한다.

#### Processing Profile Override, Preview And Reindex

```text
GET  /api/v1/knowledge/kbs/{kb_id}/processing-profile/options
GET  /api/v1/knowledge/kbs/{kb_id}/processing-profile/override
PUT  /api/v1/knowledge/kbs/{kb_id}/processing-profile/override
POST /api/v1/knowledge/kbs/{kb_id}/processing-profile/override/clear
POST /api/v1/knowledge/kbs/{kb_id}/processing-profile/resolve-preview
POST /api/v1/knowledge/kbs/{kb_id}/processing-profile/reindex
```

Source-managed KB의 override GET/options/set/clear와 resolve-preview는 parent ownership과 required KB action을
확인한 뒤 override/profile/resolver-specific row lookup 또는 projection 전에 fresh requester source authorization과
applicable display policy를 통과해야 한다. Missing/revoked/stale/denied는 `404 resource.hidden`이며 override 존재,
profile option, resolver status/revision과 bounded estimate를 반환하거나 변경하지 않는다. Set/clear는 external
authorization session을 종료한 뒤 bounded revision/watermark를 transaction에 전달하고 commit 직전에 current KB
authority와 함께 다시 검증한다. Revoke winner는 override, impact snapshot, audit와 `reindex_required` projection을
만들지 않는다. Manual KB에는 source gate를 적용하지 않는다.

Options는 KB `manage` 또는 같은 active organization의 Organization manager actor에게 current
organization/platform catalog에서 override로 선택 가능한 immutable profile revision의 strict
`profile_revision_ref={catalog_scope: organization|platform, profile_revision_id}`, bounded label,
compatibility flags와 Organization/Platform catalog revision을 `organization_profile_catalog_revision`,
`platform_profile_catalog_revision`으로 각각 반환한다. 두 catalog에 같은 opaque revision ID가 있어도
`catalog_scope`가 option과 set target을 결정한다. Null-policy
resolver가 사용하는 non-null exact `platform_default_profile_revision`도 read-only로 반환한다. Raw
parser/provider config와 다른 organization profile identity는 반환하지 않는다.

Override `GET`은 nullable current override, `override_revision`, nullable current `profile_policy_version`,
valid override일 때의 strict scoped `profile_revision_ref`와
`override_status=absent|valid|missing|deprecated|incompatible`를 반환한다. Invalid current override에서는
scoped/raw profile identity 대신 generic unavailable state를 사용하되 clear에 필요한 KB-owned
override revision은 유지한다. Response는 target profile row 존재를 clear precondition으로 만들지 않는다.
Effective projection은 `effective_resolution_status=resolved|review_required|platform_default_unavailable`과 nullable
strict scoped `resolved_profile_revision_ref={catalog_scope, profile_revision_id}`를 반환한다. Null-policy이고 valid
override가 없는 default-required branch가
unavailable이어도 GET/options는 `200`을 유지해 selectable override set/change recovery를 허용하고 clear는
아래 fixed `503`을 따른다. 또한 current canonical content, nullable assignment, registry/nullable taxonomy,
nullable mapping policy, nullable override, Platform default ref, Organization/Platform profile
catalog/selectability와 nullable resolved scoped profile ref/status를 organization/KB/actor scope와 함께
non-reversible하게 bind한 opaque `override_resolver_revision`을 반환한다. 이 token은 capability가 아니며 response는
`no-store`다.

Override `PUT`은 profile-policy와 같은 strict
`profile_revision_ref={catalog_scope: organization|platform, profile_revision_id}`, required nullable
`expected_assignment_revision`, required nullable `expected_profile_policy_version`,
`expected_override_revision`과 `expected_override_resolver_revision`을 요구한다. Ref 자체가 null 또는 object가
아니거나, `catalog_scope`가 missing/unknown/non-string이거나, `profile_revision_id`가 empty/non-string이거나,
bare `profile_revision_id` 또는 extra field가 있으면 DB 조회 전에
`422 knowledge.processing_profile_override_invalid`로 거부한다. Organization scope는 active Organization
소유 published/selectable revision을 ownership-first로 resolve하고 cross-organization ref는 safe `404`다. Platform scope는 approved
published/selectable registry revision만 허용한다. 최초 set에서만 current override가 없음을 나타내는 null
revision을 허용한다. Clear도 required nullable
`expected_assignment_revision`, required nullable `expected_profile_policy_version`과 non-null
`expected_override_revision`, `expected_override_resolver_revision`을 요구하며 현재 override가 missing/deprecated/incompatible여도 target
profile row를 먼저 조회하지 않고 KB-owned override를 잠가 복구할 수 있어야 한다. Set/clear는 KB
`manage` 또는 same-organization Organization manager override와 active organization을 다시 확인한다. Server는
전체 resolver input vector를 transaction 안에서
다시 resolve하고 pointer/revision row lock, conditional CAS 또는 동등한 serializable validation을 사용한다.

Override set/clear의 `expected_assignment_revision`과 `expected_profile_policy_version`은 nullable required exact
snapshot이다. Assignment 또는 current profile-policy가 없는 platform-default resolver 상태에서는 각 null이
유효하고, command 사이에 pointer가 생기거나 사라지거나 Platform default ref/catalog revision이 바뀌면 full
resolver snapshot conflict다. Opaque resolver token이 이 명시적 precondition을 대체하지 않으며 null은
permission이나 resolver validation을 생략하는 신호가 아니다.

Set/clear 성공은 새 override revision과 canonical
`knowledge.processing_profile_override.set` 또는 `knowledge.processing_profile_override.clear` audit를
같은 transaction에서 확정한다. Set audit에는 새 target의 safe scoped profile reference와 before/after state를
포함한다. Clear audit은 이전 override가 valid하고 caller에게 visible할 때만 그 safe scoped ref를 포함한다.
Missing/deprecated/incompatible/cross-organization 등 generic unavailable state를 clear할 때는 KB-owned override
revision과 fixed unavailable state만 기록하고 hidden target profile identity와 raw profile config를 저장하지
않는다. Mutation은 검증한 같은 resolver snapshot의 provenance와 materialization-input-fingerprint로
`reindex_required`를 갱신하지만 reindex를 자동 시작하거나 active pointer를 변경하지 않는다. Canonical
content, assignment, registry/taxonomy, mapping policy, Platform default ref, Organization/Platform profile
catalog/selectability, resolved scoped profile ref 또는 override 중 하나라도 expected vector와 다르면
override/audit/projection 전체를 rollback하고
`409 knowledge.processing_profile_override_snapshot_conflict`다.

Preview는 nullable current assignment, nullable taxonomy/registry, nullable profile mapping policy와 exact profile
revision을 서버가 다시 resolve한다. Valid explicit override는 current profile-policy 존재 여부와 무관하게 항상
먼저 평가한다. Explicit override가 missing/deprecated/incompatible이면 fallback하지 않고
`review_required`로 차단한다. Override가 없을 때 current profile-policy가 null이면 exact
`platform_default_profile_revision`을 사용하고, current policy가 있으면 그 policy의 rule/general chain을
사용하며 Platform default를 policy fallback으로 혼합하지 않는다. Client-supplied profile 결과는 authority가
아니다. Response에는 resolution source
(`override/type_primary/type/primary/org_general/platform_general/platform_default`), fallback, exact safe
revisions, reindex-required, current active-ready 존재 여부와 bounded chunk/token/cost estimate 및 opaque
`resolution_revision`만 포함한다. Null-policy이고 valid explicit override가 없어 default branch가 필요한데
target이 missing/deprecated/incompatible이면 `503 knowledge.processing_platform_default_unavailable`로
mutation/token 없이 닫고 기존 active-ready artifact를 유지한다. Selectable explicit override가 있으면 이 오류를
적용하지 않는다. Resolution revision은 organization/KB/actor, current canonical content ref, nullable policy pointer,
Platform default ref/catalog revision과 내부 두 input fingerprint를 non-reversible하게 bind하는 최대 10분 수명의
server-issued validation token이다. Raw content/hash와 내부 fingerprint는 반환하지 않는다. Preview 성공과 token은 reindex 또는 activation capability가 아니다.

Reindex wire request는 다음처럼 idempotency key를 header에만 둔다.

```http
POST /api/v1/knowledge/kbs/{kb_id}/processing-profile/reindex
Idempotency-Key: 00000000-0000-4000-8000-000000000000
Content-Type: application/json

{
  "expected_assignment_revision": null,
  "expected_profile_policy_version": null,
  "expected_resolution_revision": "opaque-resolution-revision"
}
```

`Idempotency-Key` 값은 ASCII lower-case hyphenated canonical UUID 36자
`xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx`와 정확히 같아야 한다. Header name은 case-insensitive지만 nil UUID,
upper-case, leading/trailing whitespace, braces/URN, malformed/다른 길이, comma-joined 값, duplicate 또는 missing
header는 trim/case-fold/coercion 없이 `422 knowledge.processing_idempotency_key_invalid`다. Body의
idempotency key field도 허용하지 않고 같은 fixed code로 거부한다.

Server는 canonical ASCII 36 byte의 SHA-256 digest만 organization/KB/actor/operation receipt key로 저장한다.
Raw header는 response, application/access log, audit와 trace에 반사하지 않는다. Typed body tuple의 canonical
request digest는 key digest와 분리한다. 같은 scoped key와 exact body tuple만 token expiry보다 먼저 replay하고
같은 key의 다른 tuple은 `409 knowledge.processing_idempotency_conflict`다. Upper-case나 brace 형식을 받아
canonical key로 바꾸지 않으므로 textual alias가 같은 receipt에 합쳐지는 동작도 없다. Header shape validation은
resource existence와 무관한 transport validation이고, valid request의 receipt lookup/result projection 전
parent/KB/source authorization 순서는 아래 계약대로 유지한다.

Reindex admission은 KB `write` 또는 same-organization Organization manager override, nullable required current
expected assignment/profile-policy revision,
`expected_resolution_revision`과 durable idempotency key를 요구한다. Server는 parent ownership, current
KB `write` 또는 Organization manager authority와 applicable fresh source authorization을
organization/KB/actor/operation-scoped receipt 조회보다 먼저 평가한다. Manual KB는 source gate가
`not_applicable`이다. Source-managed KB의 denied/revoked/stale/unknown gate는
`404 resource.hidden`이며 receipt/decision/satisfaction/job identity와 기존 result status를 반환하거나
변경하지 않는다.

위 gate를 통과한 뒤에만 protected key digest로 Processing Admission Receipt를 조회한다. Existing receipt의
canonical request digest가 exact request와 같으면 token expiry보다 먼저
`unchanged/satisfied_existing/job_created/job_reused`의 기존 safe result를 반환하고 mutation을 반복하지
않는다. 같은 key의 다른 request는 `409 knowledge.processing_idempotency_conflict`다. Receipt가 없을
때만 token scope/expiry/current input을 검증한다. External source check가 필요하면 authorization
session/transaction을 mutation 전에 종료하고 nullable bounded revision/watermark만 전달한다. Replay return과
fresh admission commit 직전에 current permission과 그 revision/watermark를 authoritative CAS 또는 동등한
serialization으로 다시 검증하며 revoke winner는 safe `404`로 닫는다.

Fresh admission에서 Server는 canonical content revision ref/hash와 nullable assignment, current
registry/nullable taxonomy, nullable mapping policy, nullable override, Platform default ref,
Organization/Platform profile catalog/selectability revision과 resolved exact scoped profile ref를
`resolver_input_fingerprint`로 고정한다. Null-policy이고 valid explicit override가 없어 Platform default가
필요한데 unavailable이면 receipt, decision, satisfaction, job 또는 success audit를 만들기 전에
`503 knowledge.processing_platform_default_unavailable`로 닫고 기존 active-ready artifact를 유지한다.
Server는 canonical materialization input hash, immutable materialization configuration과 output에
materialize되는 normalized assignment-derived field로 `materialization_input_fingerprint`를 admission
전에 계산한다. Output DocumentVersion/index ID와 생성 결과 bytes는 input fingerprint에 포함하지
않는다. Receipt가 없는 fresh admission에서 Client token은 authority가 아니며
scope/expiry/current recomputation 중 하나라도 다르면 mutation/job 없이
`409 knowledge.processing_preview_stale`다.

Admission 결과는 job 생성 여부와 무관하게 같은 transaction의 durable receipt에 기록한다. Receipt는 raw
token 대신 token digest와 canonical request digest, safe result ref만 저장하며 연결된 job/result retention과
같은 기본 30일 동안 유지한다. Receipt의 actor ref와 nullable safe source authorization revision/watermark는
worker가 current execution authorization을 다시 평가할 provenance이며 capability가 아니다. Current permission
또는 applicable source authorization을 잃은 actor는 receipt 존재 여부와 무관하게 replay result를 조회할 수
없다. Revoke가 replay/fresh-admission serialization winner이면 receipt, decision, satisfaction, current
decision pointer, job과 success audit를 만들거나 반환하지 않는다.

Admission은 immutable Processing Decision Manifest를 만든다. Current active Artifact Build Manifest의
materialization input fingerprint가 같고 stored integrity가 valid하면 `satisfied_existing` Decision
Satisfaction과 current decision pointer만 같은 CAS transaction에서 확정하고
`status=satisfied_existing`을 반환한다. 새 job, DocumentVersion/index와 active artifact pointer
변경은 없다. Physical build가 필요하면 durable job identity는 canonical content revision ref와 두 input
fingerprint를 모두 사용한다. 셋이 모두 같은 active job만 dedupe하고 일부만 같은 job을 current
decision으로 재사용하지 않는다. 최초 `job_created` receipt actor를 job execution actor로 불변 bind한다.
다른 authorized actor의 `job_reused` receipt는 이 actor를 교체하지 않는다. Original actor의 authorization
loss로 job이 terminal cancel되면 다른 actor에게 승계하지 않고, 현재 authorized actor가 fresh preview와
새 idempotency request로 새 generation을 admission해야 한다.

Physical embedding build가 필요한 fresh `job_created` admission은 LLM Credentials authoritative port에서
resolved profile의 exact embedding model/provider, active Organization과 job execution actor를 기준으로 현재
active credential, verified model relation과 credential `use`를 평가한다. Eligible credential이 정확히 하나일
때만 safe credential/model/provider refs, credential lifecycle revision, relation revision, permission decision
revision과 provider-routing revision을 immutable Knowledge processing embedding binding으로 job/attempt에 저장한다.
후보 0개 또는 복수는 name/order/priority/owner/default/preset fallback 없이
`409 knowledge.processing_embedding_credential_unavailable`로 receipt, decision, job과 success audit 없이
종료한다. Binding issue 뒤 fresh job commit 직전 lifecycle/relation/permission/provider-routing revision을 row
lock/CAS 또는 동등한 serializable validation으로 다시 확인하고 revoke/change winner도 같은 fixed 409
zero-write로 닫는다. `job_reused` admission은 caller credential을 선택하지 않고 original actor와 binding을
그대로 반환하며 다음 worker gate가 invalid binding을 terminal cancellation으로 수렴시킨다. Profile, request/response,
receipt, task payload, audit와 trace에는 credential value, encrypted config와 provider raw config를 넣지 않는다.

Worker는 claim과 각 source/provider external I/O batch 직전에 job execution actor의 current active
organization membership, effective KB `write` 또는 Organization manager authority와 applicable source
authorization 및 bound embedding credential의 active lifecycle, exact model/provider relation, current `use`와
binding revisions를 짧은 session에서 다시 평가하고 외부 I/O 전에 session/transaction을 종료한다. 어느
revoke/change가 gate 전에 commit되면 해당 batch adapter를 호출하지 않는다. Gate 뒤 commit된 revoke는 이미 시작한 bounded
call을 강제 중단하지 않지만 다음 external batch와 finalization을 차단한다. Receipt, job ID, preview token,
owner/fencing token과 admission 당시 authorization/binding 결과는 capability가 아니다.

Reindex admission의 public `status` enum은 `unchanged`, `satisfied_existing`, `job_created`, `job_reused`로
고정한다. Decision Satisfaction 내부 link는 `satisfied_existing|satisfied_new`이며 public admission status에
별도 `*_artifact` alias를 만들지 않는다. Durable receipt replay는 최초 응답의 같은 public status를 그대로
반환한다.

최초 successful admission은 public result 네 종류 모두 canonical
`knowledge.processing_reindex.admitted`, `audit_logs.status="success"`를 사용하고 allowlisted
`audit_metadata.result_status`로 구분한다. `unchanged`도 durable receipt와 이 audit 한 건을 같은 Unit of
Work에서 만들며, `satisfied_existing`, `job_created`, `job_reused`는 각 result에 필요한
decision/satisfaction/job/current-pointer mutation, receipt와 audit를 원자 commit한다. Audit 실패는 모든
admission write를 rollback한다. Exact authorized receipt replay는 read-only recovery이므로 새 audit를 만들지
않고 최초 result를 반환한다. Source-hidden, idempotency conflict와 preview stale은 이 success action을 만들지
않는다. Embedding credential unavailable도 이 success action을 만들지 않는다. Audit metadata에는
organization/KB/safe actor, opaque receipt/result/job ref와 safe revision만 허용한다.
Idempotency key, token digest, internal fingerprint, raw source identity, source capability, credential identity/
candidate count와 revoked membership/grant/credential relation·permission detail은 저장하지 않는다.

같은 canonical content ref와 두 input fingerprint의 decision이 이미 current면 `status=unchanged`를
반환하고 새 manifest/satisfaction/job을 만들지 않는다. Durable idempotency retry와 concurrent same-
decision admission은 같은 result로 수렴하며 duplicate satisfaction을 만들지 않는다.

Build completion은 normalized chunk/representation/index manifest와 embedding completeness로
`artifact_integrity_hash`를 계산한다. 이 output hash는 job identity나 admission dedupe 입력이 아니다.
Finalization 직전 applicable source authorization과 bound embedding credential lifecycle/relation/permission을
fresh gate로 평가하고 외부 호출/session을 종료한 뒤
bounded decision revision/watermark만 finalizer에 전달한다. Finalizer는 current execution actor
membership/effective authority, canonical content, 전체 resolver input revision vector와 current profile
selectability 및 embedding binding revisions를 짧은 transaction boundary에서 다시 확인하고 두 input
fingerprint를 재계산한다. Fresh source authorization 결과의 revision/watermark, current permission revision과
embedding binding revisions는 revoke와 직렬화되는 authoritative CAS 또는 동등한
serializable validation으로 pointer swap까지 current임을 확인한다. Pointer/revision row lock, conditional
CAS 또는 동등한 serializable validation으로 compare와 pointer swap 사이의 변경도 차단한다. Admission
snapshot과 하나라도 다르면 `knowledge.processing_decision_stale`로 attempt를 닫고, authorization을 잃으면
fixed `knowledge.processing_execution_not_authorized`, embedding binding을 잃으면 fixed
`knowledge.processing_embedding_credential_unavailable` reason의 terminal `cancelled`로 닫으며 어느 경우에도
active pointer와 Decision Satisfaction을 commit하지 않는다. Current job fence/lease가 유효한 stale/cancelled
finalizer는 terminal attempt와 해당 attempt가 소유한 staging artifact의 idempotent cleanup outbox intent만
같은 transaction에서 확정한다. Fence를 잃은 worker는 commit하지 않으며 reconciler가 terminal/orphan
attempt에 누락되거나 미완료된 cleanup intent를 생성·재시도한다. Cleanup handler는 generation/fence와
current active/decision reference 부재를 다시 확인하고 참조 중인 artifact를 삭제하지 않는다. Current하고
authorized된 attempt만 immutable Artifact Build Manifest와 `satisfied_new` link를 만들고 current
decision/active artifact pointer를 함께 교체한다.

#### Permission And Error Matrix

| 조건 | HTTP / 결과 |
| --- | --- |
| Missing/cross-organization/hidden KB 또는 organization-scoped taxonomy/policy version, assignment/suggestion, profile/override, reindex reference | Parent ownership을 semantic state보다 먼저 확인하고 ADR-0010 safe `404` |
| Unknown/hidden platform document-type 또는 profile registry reference | Fixed `404 knowledge.classification_reference_unavailable`; registry catalog 내부 상태를 반환하지 않음 |
| Scope 안 classification read 권한 부족 | Existing Knowledge permission contract의 `403` |
| Unlocked mutation, current-validation, suggestion review, preview/reindex 권한 부족 | KB `write` 또는 같은 active organization의 Organization manager override 기준 `403` |
| Lock/unlock 또는 override options/read/set/clear 권한 부족 | KB `manage` 또는 같은 active organization의 Organization manager override 기준 `403` |
| Full taxonomy/profile catalog/profile policy current/history read, author, publish 또는 profile deprecate 권한 부족 | Organization manager 기준 `403` |
| Source-managed classification/override/resolve operation의 fresh source authorization denied/revoked/stale | Assignment/lock/override/resolver row lookup·projection·mutation 전에 `404 resource.hidden`; assignment/override/profile/resolver identity와 status 비노출, mutation/impact snapshot/audit/reindex projection 없음. Manual KB는 비적용 |
| Source-managed suggestion list/accept/reject의 fresh source authorization denied/revoked/stale | Candidate row 조회·projection·mutation 전에 `404 resource.hidden`; suggestion identity/state/count/confidence 및 outcome 비노출 |
| Source-managed suggestion accept-preview의 fresh source authorization denied/revoked/stale | Candidate projection/hypothetical resolve 전에 `404 resource.hidden`; preview token, candidate identity/confidence와 impact 비노출 |
| Source-managed reindex admission/receipt replay의 fresh source authorization denied/revoked/stale/unknown | Receipt lookup/result projection과 admission mutation 전에 `404 resource.hidden`; receipt/result/job identity 비노출, decision/satisfaction/pointer와 `knowledge.processing_reindex.admitted` success audit 없음. ADR-0017의 target/source 비노출 request-scoped security audit는 admission result audit와 별도로 허용 |
| Malformed ID, topic set, primary 또는 schema | `422` |
| Manual PUT의 `reason_code`, free-text reason 또는 unknown top-level field | DB/assignment/audit 전에 `422 knowledge.classification_request_invalid`; 입력값 비반사·비저장 |
| Client assignment/review topic array 33개 이상 | DB/provider 호출 전 `422 knowledge.classification_topic_limit_exceeded`; duplicate 제거로 limit을 우회하지 않음 |
| Rule/classifier adapter output topic 33개 이상 | `knowledge.classification_topic_limit_exceeded` safe suggestion failure; durable suggestion/assignment/audit 없음 |
| Malformed 또는 scope/filter가 다른 suggestion cursor | `422 knowledge.classification_suggestion_cursor_invalid`; cursor 원문 비반사 |
| Empty/duplicate/unknown/candidate 밖 accepted axis 또는 absent assignment를 complete하게 만들 수 없는 accept | `422 knowledge.classification_suggestion_axes_invalid`; assignment/outcome/audit 없음 |
| Suggestion accept preview 누락/expired/wrong actor·scope 또는 candidate/assignment/content/taxonomy/registry/resolver vector stale | `409 knowledge.classification_suggestion_accept_preview_stale`; assignment/outcome/audit/reindex intent 없음 |
| Required `taxonomy_version_id` 누락 또는 null taxonomy와 non-empty topic/primary 조합 | 누락은 schema `422`, 잘못된 조합은 `422 knowledge.classification_taxonomy_shape_invalid` |
| Content가 바뀐 current-validation에 taxonomy/registry-only reason 사용 | `422 knowledge.classification_content_confirmation_required`; assignment/audit/reindex projection 없음 |
| Current-validation reason이 server-computed changed dimension set과 다름 | `422 knowledge.classification_validation_reason_mismatch`; last-validated refs/audit/reindex projection 없음 |
| Current-validation의 source/display gate 실패 또는 content changed set의 `content_read`/raw policy 실패 | Source-managed denied/revoked/stale은 changed set과 무관하게 `404 resource.hidden`; content-plane action 부족은 content가 포함될 때만 `403`; validation result/mutation 비노출 |
| Lock request의 expected assignment 또는 canonical content revision mismatch | `409 knowledge.classification_lock_snapshot_conflict`; lock/audit 없음 |
| Unlocked target axis가 `current_validation_required` 또는 `review_required`인 lock request | `409 knowledge.classification_lock_not_current`; 각각 current-validation 또는 complete assignment replacement를 먼저 요구 |
| Manual PUT의 empty/duplicate/unknown `manual_axes` 또는 first create의 incomplete axes | `422 knowledge.classification_manual_axes_invalid`; assignment/audit 없음 |
| Manual PUT의 unselected axis request value가 current effective value와 다름 | `409 knowledge.classification_unselected_axis_conflict`; server는 unselected value/source/lock을 보존하고 partial mutation 없음 |
| Manual PUT이 selected locked axis를 변경 또는 takeover | `409 knowledge.classification_manual_authority_conflict`; unlock 없이 assignment/audit 없음 |
| Stale assignment/draft/resolver/preview/compatibility snapshot, locked/manual authority axis, same-scope deprecated/incompatible new mutation candidate, publish conflict | `409` + fixed domain code. Existing assignment current-validation의 deprecated/missing ref는 위 `200 review_required` |
| Nullable assignment 부재/current revision mismatch | `409 knowledge.classification_assignment_revision_conflict`; first mutation partial write/audit 없음 |
| Override set/clear의 full resolver vector mismatch | `409 knowledge.processing_profile_override_snapshot_conflict`; override/audit/reindex projection 전체 rollback |
| Override set의 null/non-object ref, missing/unknown/non-string catalog scope, empty/non-string profile revision ID, bare revision ID 또는 extra ref field | DB 조회 전 `422 knowledge.processing_profile_override_invalid`; override/audit/reindex projection 없음 |
| Same-scope nullable taxonomy current pointer mismatch | `409 knowledge.classification_taxonomy_snapshot_conflict` |
| Published/tombstoned topic stable ID를 active topic 또는 다른 identity로 재사용 | `409 knowledge.taxonomy_topic_id_reuse_conflict` |
| Taxonomy version topic raw array 1,001개 이상, candidate replacement ref 합계 2,001개 이상, raw label 또는 normalized comparison key UTF-8 512 byte 초과 | Raw count/label은 dedupe·normalization 전, normalized key는 normalization 직후 sibling/graph/DB 전에 각각 `422 knowledge.taxonomy_topic_count_limit_exceeded`, `422 knowledge.taxonomy_replacement_edge_limit_exceeded`, `422 knowledge.taxonomy_label_invalid`; draft/audit 없음 |
| New topic의 missing/invalid/non-unique/out-of-draft `draft_topic_key`, key와 existing ID 동시 지정 또는 current-mapped key의 subsequent mutation 재제출 | Shape/reference는 `422 knowledge.taxonomy_draft_topic_reference_invalid`, consumed current key 재제출은 `409 knowledge.taxonomy_draft_topic_key_conflict`; server ID/mapping/revision partial write 없음. Topic 제거는 mapping도 같은 CAS에서 제거하고 이후 같은 key에는 새 server ID를 발급 |
| Taxonomy empty canonical label, sibling collision 또는 invalid replacement graph | Validate는 `knowledge.taxonomy_label_invalid`, `knowledge.taxonomy_sibling_label_conflict`, `knowledge.taxonomy_replacement_invalid` 또는 `knowledge.taxonomy_replacement_depth_exceeded` safe violation을 반환하고 unresolved draft publish는 `409 knowledge.taxonomy_publish_validation_failed`; raw comparison key 비반사 |
| Taxonomy impact preview 누락/false acknowledgement/expired/wrong actor·scope/stale snapshot 또는 bucket contract version mismatch | `409 knowledge.taxonomy_impact_preview_stale`; version/current pointer/ledger/audit 없음 |
| Organization profile malformed config 또는 unsupported capability | `422 knowledge.processing_profile_invalid`; raw provider/parser detail 비반사 |
| First profile create의 missing/non-object/non-empty body | DB 조회 전 `422 knowledge.processing_profile_invalid`; identity/draft/audit 및 catalog revision 변경 없음 |
| Successor profile의 missing/malformed/extra `base_revision_id`, wrong owner/identity 또는 non-published base | Shape는 DB 조회 전 `422 knowledge.processing_profile_invalid`, cross-organization/wrong identity는 safe `404`, same-scope draft/deprecated/non-published는 `409 knowledge.processing_profile_successor_base_invalid`; draft/catalog/audit 없음 |
| Profile-policy unknown/mismatched field 또는 invalid ID/ref shape | `422 knowledge.processing_profile_policy_invalid`; draft/current/audit 없음 |
| Profile-policy raw rule 2,001개 또는 normalized duplicate matcher | 각각 `422 knowledge.processing_profile_policy_rule_limit_exceeded`, `422 knowledge.processing_profile_policy_rule_conflict`; dedupe/partial draft로 우회하지 않음 |
| Organization profile stale draft/catalog revision | `409 knowledge.processing_profile_draft_revision_conflict` 또는 `409 knowledge.processing_profile_catalog_revision_conflict`; revision/catalog/audit 없음 |
| Platform default pointer command의 stale `expected_platform_profile_catalog_revision` | `409 knowledge.processing_profile_catalog_revision_conflict`; pointer/catalog/audit 없음 |
| Profile-policy impact preview/token 누락, false acknowledgement, expiry, wrong actor·scope·candidate, Organization/Platform catalog를 포함한 token-bound snapshot 변경 또는 request/token/contract mismatch | `409 knowledge.processing_profile_policy_impact_preview_stale`; policy version/current pointer/audit 없음 |
| Fresh profile-policy preview와 snapshot 검증 뒤 coordination/current policy pointer CAS loser | `409 knowledge.processing_profile_policy_publish_snapshot_conflict`; policy/current pointer/audit 없음 |
| Current policy/override가 참조하는 Organization/Platform profile 또는 current Platform default target deprecate | `409 knowledge.processing_profile_in_use`; referring organization/KB identity와 exact count 비노출 |
| Null-policy이고 valid explicit override가 없는 default-required resolver의 Platform default missing/deprecated/incompatible | `503 knowledge.processing_platform_default_unavailable`; preview/reindex admission과 override clear의 token·mutation·receipt·audit 없음, valid selectable override set/change는 recovery 허용, 기존 active-ready artifact 유지, mutable latest/env/legacy fallback 금지 |
| 기존 receipt가 없는 expired preview token | `409 knowledge.processing_preview_stale`; current KB authority와 applicable source gate를 통과한 exact idempotency replay만 token expiry보다 먼저 기존 safe result 반환 |
| Reindex `Idempotency-Key` missing, duplicate, nil, non-canonical case/whitespace/braces/URN, comma-joined, malformed/다른 길이 또는 body-only field | `422 knowledge.processing_idempotency_key_invalid`; receipt lookup/write와 audit 없음, raw value 비반사 |
| 같은 idempotency key의 다른 canonical request | `409 knowledge.processing_idempotency_conflict`; 기존 receipt/result identity 비노출 |
| Reindex worker/finalizer의 current organization membership, KB authority 또는 applicable source authorization 회수 | Gate 뒤 새 external batch를 호출하지 않고 terminal `cancelled` + fixed `knowledge.processing_execution_not_authorized`; current unauthorized requester에게 job/receipt identity 비노출, active pointer/Satisfaction 없음 |
| Physical build admission의 embedding credential 후보 0개/복수 또는 worker/finalizer의 bound credential lifecycle/relation/`use`/revision 상실 | Admission은 `409 knowledge.processing_embedding_credential_unavailable`과 zero receipt/decision/job/audit, runtime은 다음 provider batch 0회 및 같은 fixed reason의 terminal `cancelled`; credential identity/count/detail 비노출, active pointer/Satisfaction 없음, fallback/rebinding 없음 |
| Ambiguous legacy multi-document target | `409 knowledge.classification_migration_required` |
| Classifier/provider unavailable | Authoritative assignment mutation 없음. Suggestion unavailable safe result/error |
| Reindex infrastructure failure | Safe retryable error, 기존 active ready version 유지 |

#### Classification And Profile Contract Trace

| Operation | Actor와 fresh gate | Required precondition/snapshot | Commit/result | Audit |
| --- | --- | --- | --- | --- |
| Classification GET/options | GET은 KB `read`, options는 KB `write`; 각각 Organization manager override 허용. Source-managed는 assignment lookup 전 fresh source/display gate 필수 | Current canonical content, nullable assignment, registry/taxonomy, source authorization freshness | Axis별 authority와 bounded safe projection, `no-store`; source gate 실패는 assignment/type/topic/security 비노출 `404` | Read audit 없음 |
| Assignment PUT | KB `write` 또는 Organization manager override; source-managed는 row lookup 전과 commit 직전 fresh source/display gate | Required nullable assignment revision, canonical content, registry/taxonomy, complete effective set, non-empty unique `manual_axes`, topic limit 32와 source revision/watermark; Client reason/unknown field 금지 | Selected unlocked axis만 manual replace/takeover하고 unselected value/source/lock 보존; first create는 두 axis 필수, stale/locked/mismatch/oversized/revoked는 전체 rollback | First create `knowledge.classification_assignment.created`, replace/takeover `knowledge.classification_assignment.updated`와 mutation 원자적; revoke는 audit 없음 |
| Current validation | KB `write` 또는 Organization manager override; source-managed는 changed set과 무관하게 lookup 전/commit 직전 fresh source/display gate, content가 포함되면 추가 `content_read`/raw policy | Non-null assignment revision, canonical content, registry/taxonomy, server-computed changed dimension set, exact fixed reason와 source revision/watermark | Effective set/axis authority 보존, reviewed dimension의 current snapshot으로 last-validated refs와 manual-confirmation provenance 전진; reason/gate mismatch 전체 rollback | `knowledge.classification.revalidated`와 성공 revision 원자적; revoke는 audit 없음 |
| Lock | KB `manage` 또는 Organization manager override; source-managed는 row lookup 전과 commit 직전 fresh source/display gate | Non-null assignment revision, exact current canonical content revision, target axis `current` freshness와 source revision/watermark | Complete locked revision 또는 exact-state unchanged; stale/non-current/revoked는 전체 rollback | `knowledge.classification_assignment.locked`와 transition 원자적; revoke는 audit 없음 |
| Unlock | KB `manage` 또는 Organization manager override; source-managed는 row lookup 전과 commit 직전 fresh source/display gate | Non-null assignment revision, target axis와 source revision/watermark; content freshness는 recovery blocker가 아님 | Complete unlocked revision; 다른 axis 보존, current projection 재계산; revoke는 전체 rollback | `knowledge.classification_assignment.unlocked`와 transition 원자적; revoke는 audit 없음 |
| Suggestion list | KB `write` 또는 Organization manager override + page마다 fresh source authorization/display gate | State-bound cursor, limit 1..50 | Stable keyset page, total/hidden count 없음 | Read audit 없음 |
| Suggestion accept preview | KB `write` 또는 Organization manager override + candidate projection 전 fresh source/display gate | Required nullable assignment revision, non-empty candidate axis subset, current candidate/content/taxonomy/registry/resolver vector | Opaque max-10-minute `accept_preview_revision`, bounded axis/profile/reindex booleans, `no-store`; accept가 exact token을 소비하며 stale vector는 전체 rollback | Preview/read audit 없음, raw token/digest 비저장 |
| Suggestion accept/reject | KB `write` 또는 Organization manager override + candidate lookup 전과 commit 전 fresh source gate | Suggestion state/snapshot, accept의 required nullable assignment revision과 non-empty candidate axis subset | Accept는 complete assignment revision+terminal outcome, reject는 terminal outcome만 확정; stale/denied 전체 rollback | 각각 `knowledge.classification_suggestion.accepted`, `knowledge.classification_suggestion.rejected`와 outcome 원자적 |
| Taxonomy impact preview/publish | Organization manager | Exact draft/current policy/registry, Organization/Platform catalog, assignment-impact snapshot, `taxonomy_impact_bucket_v1`, opaque preview revision와 acknowledgement | Processing-eligible current assignment predicate의 versioned bounded preview 또는 immutable publish; stale/missing acknowledgement 전체 rollback, publish 자체는 assignment/job을 만들지 않음 | Preview audit 없음, publish와 `knowledge.taxonomy.published` 원자적 |
| Organization profile config authoring | Organization manager | `processing_profile_schema_v1`, strict size `64..8192`, overlap `0..floor(size/2)`, current capability limits | Bound/capability-valid draft 또는 fixed validation; no coercion | Read/validate audit 없음, lifecycle mutation audit는 아래 행 |
| Profile-policy draft authoring | Organization manager | `processing_profile_policy_v1` complete document, raw rules `0..2000`, strict matcher union, required nullable general fields | Complete draft revision 또는 shape/cap/conflict 전체 rollback | `knowledge.processing_profile_policy_draft.created`, `knowledge.processing_profile_policy_draft.updated`와 draft mutation 원자적 |
| Organization profile draft/publish/deprecate | Organization manager | First create의 exact `{}`, complete config PATCH, successor의 exact same-identity published `base_revision_id`, draft revision 또는 Organization catalog revision CAS, owner/capability/current-reference gate | First create는 null config/base의 revision 0 draft shell과 unchanged catalog revision, 이후 base lineage를 가진 draft/immutable published/selectability lifecycle; wrong shape/base는 zero write | `knowledge.processing_profile_draft.created`, `knowledge.processing_profile_draft.updated`, `knowledge.processing_profile.published`, `knowledge.processing_profile.deprecated` 중 해당 action과 mutation 원자적 |
| Platform profile options/default projection | Organization manager | Current Platform catalog revision과 non-null exact default pointer | Approved options, `platform_default_profile_revision`과 catalog revision의 read-only safe projection | Read audit 없음, Organization mutation API 없음 |
| Platform default pointer control-plane mutation | Platform registry owner/system actor | Required exact `expected_platform_profile_catalog_revision`과 approved published/selectable `target_profile_revision_id` | Pointer와 incremented catalog revision 또는 stale/audit-failure 전체 rollback | `knowledge.processing_profile_default.changed`와 pointer/catalog mutation이 같은 Unit of Work; safe before/after revision과 resulting catalog revision만 기록 |
| Profile-policy impact preview/publish | Organization manager | Exact draft/current policy/taxonomy/registry, Organization/Platform catalog, assignment-impact snapshot, `profile_policy_impact_bucket_v1`, opaque preview revision와 acknowledgement | Versioned bounded resolution/reindex preview 또는 immutable policy/current pointer; stale/missing acknowledgement 전체 rollback, publish 자체는 assignment/job을 만들지 않음 | Preview audit 없음, publish와 `knowledge.processing_profile_policy.published` 원자적 |
| Override GET/options | KB `manage` 또는 Organization manager override; source-managed는 override/resolver lookup 전 fresh source/display gate | Current full resolver vector, null-policy Platform default ref/catalog revision과 source authorization freshness | Options, valid override와 nullable resolved profile은 strict scoped `profile_revision_ref`, effective status와 opaque resolver revision, `no-store`; source-hidden은 identity/status 비노출, invalid override identity는 generic이고 default unavailable도 200 recovery projection | Read audit 없음 |
| Override set/clear | KB `manage` 또는 Organization manager override; source-managed는 lookup 전과 commit 직전 fresh source/display gate | Set의 strict scoped `profile_revision_ref`, required nullable assignment/profile-policy, nullable override, Platform default ref, opaque full resolver revision과 source revision/watermark | Override revision과 같은 snapshot의 `reindex_required`; invalid ref shape는 422, stale/revoked는 전체 rollback, default unavailable clear는 fixed 503, selectable set/change는 recovery 허용 | 성공 Override와 canonical audit 같은 transaction; revoke는 audit 없음 |
| Resolve preview | KB `write` 또는 Organization manager override; source-managed는 resolver lookup 전 fresh source/display gate | Server-resolved current full vector, null-policy Platform default ref와 source authorization freshness | Source에 `platform_default`를 포함한 opaque short-lived resolution revision과 bounded estimate; source-hidden/default-required unavailable은 zero token/write | Mutation audit 없음 |
| Reindex idempotency transport | Schema validation; valid key 뒤 admission actor/gate 적용 | Single canonical lower-case UUID `Idempotency-Key` header, duplicate/body key 금지 | Invalid/missing은 fixed 422와 zero receipt; canonical bytes SHA-256 digest만 저장, same key/different typed tuple은 409 | Raw key/digest 비감사·비로그 |
| Reindex admission | KB `write` 또는 Organization manager override + applicable fresh source authorization; receipt lookup/replay 전과 return/commit 직전 gate | Receipt digest 또는 current resolution revision/full vector, nullable source authorization revision/watermark; physical build는 exact-one current embedding credential/model binding | `unchanged`, `satisfied_existing`, `job_created`, `job_reused` 중 하나의 durable result; revoke winner는 safe 404와 zero write, ambiguous credential은 fixed 409 zero write | 최초 result의 receipt/decision/satisfaction/job/current pointer와 `knowledge.processing_reindex.admitted` success audit 원자 commit; exact replay는 중복 audit 없음 |
| Reindex worker/finalizer | `job_created` receipt에 불변 bind된 execution actor와 embedding binding의 current active membership, effective KB `write`/Organization manager, source authorization, credential lifecycle/relation/`use` | 각 external batch 전 fresh gate, finalization authorization/binding revision, resolver vector와 job fence/lease | Authorized current attempt만 `satisfied_new`와 pointer swap; actor/source/credential revoke는 cancelled+cleanup으로 수렴하고 cross-actor/credential reuse는 binding을 교체하지 않음 | Fixed safe reason만 기록하고 hidden resource/source/credential detail 비노출 |

### MBA-333 Privacy Detector Target Contract

MBA-333은 새 public management endpoint를 추가하지 않는다. Existing/target document
`process`, `sync`, `resume`, `reindex` admission은 provider id, endpoint, config,
credential, trust tier, policy mode 또는 raw content fingerprint를 request body/header/
query/graph에서 받지 않는다. Unknown provider-control field는 target schema에서 `422`와
zero policy/provider lookup/mutation으로 거부한다. Server가 active Organization의 exact
effective Privacy Policy Snapshot을 resolve한다.

Policy/validity bootstrap은 public request field나 runtime default가 아니다. Enforcement migration은
current global platform Privacy Artifact Validity Revision/epoch을 먼저 provision하고, 각 Existing
Organization에 current platform contract를 참조하는 server-owned initial `baseline_only` policy와
initial same-Organization validity revision/epoch을 authoritative Unit of Work에서 함께 생성한다. 이후
Organization creation도 policy와 Organization validity를 같은 creation Unit of Work에 포함한다.
Bootstrap 실패는 해당 migration/creation을 rollback하고 runtime은 missing policy/validity를 implicit
default로 해석하지 않는다.

Target policy management schema/application은 다음 closed compatibility matrix를 함께 검증한다.

| Mode | `provider_revision` | Review readiness |
| --- | --- | --- |
| `baseline_only` | 반드시 `null` | Effective action policy가 `manual_review`를 만들 수 있을 때 필수 |
| `enterprise_detector_required` | exactly one same-Organization active revision | Effective action policy가 `manual_review`를 만들 수 있을 때 필수 |
| `manual_review_required` | `null` 또는 exactly one same-Organization active revision | 항상 필수 |

Non-null provider는 exact current Detector Egress Approval Revision과 credential/capability readiness를
추가로 요구한다. Invalid combination/readiness 부재는 policy publish/activate를 zero-write로 거부하고
provider 제거, baseline-only 또는 `mask`로 downgrade하지 않는다. 이 management endpoint 자체는
MBA-333 범위가 아니며 exact request/error/action은 후속 구현 전 승인해야 한다.

`external_approved`는 ADR-0067 public-address operation profile을 사용한다. `organization_private`는
public guard의 allow-private flag가 아니라 server-owned exact host/port/CIDR allowlist, all-DNS-result
validation, address pinning, peer/Host/TLS SNI, HTTPS+mTLS와 dedicated worker/network isolation을 가진
별도 `knowledge.detector.organization_private` profile이다. Loopback/link-local/cloud metadata/public/
미승인 private 주소 또는 profile/network readiness 부재는 policy activation과 provider call 0회다.
Ingestion/runtime API는 endpoint, CIDR, operation profile 또는 transport 선택 field를 제공하지 않는다.
Organization RBAC 밖의 deployment/change-control operator만 server-owned private profile registry와
network isolation revision을 변경한다. Future authorized provider management는 그 registry의 profile만
참조해 immutable provider revision을 bind하며 Organization actor가 endpoint/CIDR/profile/transport를
만들거나 확장하는 field를 제공하지 않는다.

Effective resolver는 platform hard baseline, current Organization base, KB에 명시적으로 연결된 모든 active
Collection Privacy Policy Binding의 applicable stricter revision, source revision과 KB revision을 모두 합성한다. Category별
action은 `block > manual_review > mask`, detector/manual-review requirement는 OR이며 하위 scope의
미지정/삭제는 상위 값을 약화하지 않는다. Collection UUID 정렬은 deterministic digest용이지 우선순위가
아니다. Distinct non-null provider revision이 복수이거나 required detector가 null이거나 scope/provider가
cross-Organization/inactive면 `knowledge.privacy_policy_unavailable`로 external parser/provider/embedding
전에 닫는다. Snapshot은 모든 exact scoped revision, Collection privacy/source/KB binding revision,
compiled digest와 derived mode/action/provider를 고정하고 finalization에서 다시 resolve한다. Client는
scope set, precedence 또는 provider를 제출할 수 없다.

Default parser는 network-disabled local isolated parser다. External parser는 detector path와 분리된
protected raw egress이며 explicit Organization opt-in과 source-managed document의 exact source scope 또는
manual document의 exact KB scope opt-in, exact Raw Parser Egress Approval Revision, server-owned parser
revision/credential capability/`knowledge.parser.external_approved` operation profile, ADR-0067 public-address
guarded transport와 readiness를 모두 요구한다. V1은 approved public HTTPS parser만 지원하고 private raw
parser destination은 별도 Accepted ADR과 dedicated isolation profile 전까지 거부한다. Current
`llamaparse` credential의 Organization/`use` 확인만으로는 부족하다. Target enforcement는
준비 전 `llamaparse`를 포함한 external parser strategy를 request validation/admission에서
`knowledge.raw_parser_egress_unavailable`로 차단하고 raw upload, detector, embedding을 0회로 유지한다.

Legacy migration inventory/wave, freeze 당시 exact platform/Organization validity ref+epoch,
`legacy_unverified` 표시, admission deadline과 cutoff도
server-owned rollout state다. Existing/target public request는 artifact enrollment, legacy
selection, later-wave 이동, deadline/cutoff extension 또는 cleanup receipt 뒤 rollback field를
받지 않는다. `admission_deadline_at`은 새 reindex admission, `retrieval_cutoff_at`은 legacy
visibility를 닫으며 전자는 후자보다 늦을 수 없다. 두 값은 UTC이며 server의 authoritative DB
time으로 half-open 비교한다. Runtime/preflight는 frozen/current 두 validity epoch equality도 요구하며
invalidating epoch commit은 item projection/cleanup을 기다리지 않고 legacy를 닫는다. Client clock,
scheduler 지연, stale cache 또는 vector result가 cutoff나 validity를 연장하지 않는다. V1은 code-owned `privacy_legacy_grace_v1 = 30 * 24 hours`를 사용하고
`retrieval_cutoff_at <= enforcement_activated_at + 30 * 24 hours`를 강제한다. Deployment rollout은
상한을 줄일 수만 있고 지원되는 contract/activation time readiness가 없거나 상한을 넘으면
wave/rollout/audit zero-write다. Allowlist/wave 부재는 legacy grace가 아니라 safe unavailable이다.
Cutoff 뒤 current-valid compliant active-ready version이 없는 document는 기존 resource-hiding 계약을 유지한
safe retrieval-unavailable 상태로 응답하며 legacy/raw/staging artifact를 반환하지 않는다.
Internal inventory는 Organization/KB/document/nullable version과 exact opaque
`legacy_artifact_ref`로 versioned/unversioned chunk 및 vector/keyword/hierarchy generation의
frozen set을 결속한다. Public process/status/detail/list response는 이 ref, wave/item identity,
exact chunk/index membership 또는 nullable-version migration fact를 노출하지 않는다.

Privacy-compliant active pointer finalization은 같은 transaction에서 해당 document의 frozen legacy
eligibility를 비가역적으로 retire한다. 이후 active manifest가 stale/invalid가 되거나 physical cleanup이
남아 있어도 legacy를 다시 ready로 선택하지 않는다. Legacy 예외는 compliant active pointer가 한 번도
확정되지 않은 document에만 적용한다.

Artifact의 active/ready 상태만으로 retrieval을 허용하지 않는다. Target manifest는 server-owned global
platform과 same-Organization Privacy Artifact Validity Revision ref/monotonic `validity_epoch`의 정확한 두 요소를
snapshot한다. Retrieval prefilter와 final evidence gate는 두 snapshot epoch가 current 두 epoch와 모두
같을 때만 evidence를 허용한다. Preserving transition은 current revision이 바뀌어도 epoch을 유지한다.
Server compatibility validator가 `artifact_invalidating`으로 분류한 baseline security supersession은
platform epoch을, stronger Organization action/new required detector 또는 provider/egress-approval security
invalidation은 해당 Organization epoch을 정확히 1 증가시키고 revision/epoch CAS와 canonical audit를
원자 commit한다. V1 Organization invalidation은 해당 Organization의 모든 privacy-gated artifact에
적용한다. Unknown transition과 missing/malformed/stale epoch도 fail-closed이며 Organization/client-supplied
scope, epoch 또는 grace는 없다. Credential rotation이나 operational endpoint 변경은 protection 의미가
보존된 `artifact_preserving` transition일 때만 과거 manifest를 유지한다.

Public process/sync/reindex request와 graph/document metadata는 validity scope, revision ref 또는 epoch을
받지 않는다. Future management command의 expected current revision/epoch은 authorized concurrency
precondition일 뿐 effect/scope를 선택하는 입력이 아니며, server compatibility validator가
`platform|organization` scope와 preserving/invalidating effect를 파생한다.

| Operation | Actor/fresh gate | Server-owned input | Result |
| --- | --- | --- | --- |
| process/reprocess admission | KB `write` 또는 Organization manager; source-managed는 fresh source gate | effective scoped privacy policy/baseline/action/normalization/masking, nullable exact provider, detector/raw-parser approval revision | 기존 ingestion job/status envelope. Missing/stale policy는 no external parser/provider/embedding |
| sync/reindex worker | admission에 불변 bind된 actor | claim/fence, current membership/KB/source/effective-policy/parser/provider/approval과 platform/Organization validity revision/epoch | Gate 전 revoke는 external adapter 0회, gate 뒤 revoke는 next batch/finalize 차단 |
| privacy review list/detail | Organization manager; source-managed는 projection 직전 fresh requester source authorization/display policy | DB-time 기준 non-expired staged redacted candidate, bounded safe outcome, opaque manifest/generation ref와 safe expiry | Revoke/expiry winner는 `404 resource.hidden`, candidate/ref/state 비노출. Manual KB는 source gate 비적용 |
| privacy review mask/approve/reject | Organization manager; source-managed는 mutation commit 직전 fresh requester source authorization/display policy 재검증, raw 확인은 별도 raw/compliance permission | exact pending non-expired generation/candidate revision, staged redacted candidate와 bounded safe outcome | Revoke/expiry winner는 `404 resource.hidden`, zero mutation/audit. Successor mask는 최초 expiry를 연장하지 않는다. Management/review API와 canonical action 승인 전 endpoint 없음, mode disabled |
| Collection privacy bind/unbind | Organization manager | active same-Organization routing membership, exact published Collection policy revision, bounded impact preview/acknowledgement와 expected membership/binding/policy/current Organization validity revision | Binding mutation, invalidating Organization epoch `+1`과 canonical management audit를 한 Unit of Work에 확정. Stale loser는 zero-write. Management API 승인 전 endpoint 없음 |
| deployment privacy preflight | 기존 deployment command actor와 server-derived audience | runtime과 같은 current-valid manifest 또는 frozen/current validity epoch equality+legacy-retirement+cutoff resolver | `knowledge_privacy_artifact_unavailable` + `reprocess_or_remove_unavailable_knowledge`; standalone preview는 `200 OK`와 safe `status="blocked"` 또는 허용된 inactive `warning`, active create/enable/toggle은 `409 deployment.preflight.blocked`로 차단 |
| privacy status projection | 기존 document/job read authority | safe attempt state/reason/retryability/timestamps | Raw/provider identity/span/digest/count 비노출, `Cache-Control: no-store` |

Target manual review command는 provider 호출을 요구하지 않는다. Provider가 null인
`manual_review_required` candidate도 다음 불변조건을 만족하면 처리할 수 있다.

- Mask command는 exact pending generation, expected candidate revision과 candidate-relative UTF-8 byte
  half-open range 목록만 받는다. Replacement text, raw content, 전체 candidate body와 provider span은 받지 않는다.
- Server는 range/cap/boundary를 검증하고 기존 staged candidate에서 정보가 줄어드는 새 immutable candidate
  revision과 candidate-bound manifest를 deterministic masking으로 생성한다. Local hard baseline을 다시
  실행하며 성공해도 상태는 `pending_review`이고 최초 generation의 `retention_expires_at`을 그대로 승계한다.
  Candidate/manifest와 append-only `mask` decision, canonical
  review audit는 한 Unit of Work이고 submitted range는 durable record나 audit에 저장하지 않는다.
- Approve command는 exact current candidate revision, effective policy/Collection privacy binding,
  platform/Organization validity와 actor/source authorization을 commit 직전에 재검증한 뒤에만 canonical
  content와 embedding/finalization을 진행한다. Candidate를 덮어쓰지 않고 append-only approve decision,
  generation review-state와 canonical review audit를 원자 확정한 뒤 embedding을 transaction 밖에서 재개하며
  finalizer가 같은 fence를 다시 검증한다. Reject도 append-only decision과 terminal review-state를 원자 확정하고
  artifact를 만들지 않는다.
- 보지 않은 미래 원문, KB 전체 또는 이후 generation에 대한 blanket pre-approval은 허용하지 않는다.
  Candidate/content/policy/binding/validity/source revision이 달라진 approval은 stale conflict이며 zero-write다.
- Platform hard baseline 또는 effective action의 terminal `block`은 mask/approve surface에 올리지 않으며
  Organization manager도 override하지 못한다.
- Candidate body는 encrypted protected staging에서만 읽고 최초 생성 DB time부터 code-owned
  `privacy_review_candidate_ttl_v1 = 7 * 24 hours`를 적용한다. Approve decision은 candidate를 list/detail과
  mutation에서 닫되 exact body를 TTL 안의 `approved_pending_finalization` input으로 유지한다. Finalizer가
  fresh authority/policy/validity/fence와 exact candidate를 재검증해 canonical active artifact를 commit하면
  그 transaction에서 `purge_pending`으로 전환한다. 그 전 transient failure는 TTL 안에서만 같은 immutable
  input으로 재시도한다. `db_now >= retention_expires_at`, reject, canonical finalization commit, successor 확정,
  generation-bound source/ingestion authority revoke 또는 stale/abandoned generation은 즉시 `purge_pending`으로
  전환한다. 개별 reviewer의 manager/source-display 권한 회수는 해당 요청만 hidden zero-write로 닫고 candidate를
  purge하지 않는다. Cleanup reconciler는 current claim/fence와
  legal hold를 확인해 24시간 안에 body를 purge한다. Legal hold는 physical deletion만 보류하며 API
  projection/approval/TTL을 재개하지 않는다. Safe append-only Decision/audit/manifest와 purge receipt는
  candidate body와 분리 보존한다.

Target Collection privacy binding은 일반 Collection item API와 별도 protected command/query다. 현재 route는
없다. 향후 binding create/replace/delete는 Organization manager만 호출하고 exact active membership과
published Collection privacy policy revision을 참조한다. Preview는 safe affected-artifact count bucket과
availability impact만 반환하며 target KB/document/provider identity나 exact count를 노출하지 않는다.
Mutation request는 preview token/acknowledgement와 expected membership/binding/policy/current Organization
validity revision을 모두 요구한다. `catalog_manage`, `collection.manage`, KB `manage` 또는 item ordering
capability만으로 privacy binding을 만들거나 제거할 수 없다.

Collection archive/restore는 routing lifecycle만 전이하고 active privacy binding을 바꾸지 않는다. Archived
Collection의 active binding은 effective policy resolver에 계속 포함되며 lifecycle mutation 자체는 Organization
validity epoch을 전진시키지 않는다. Active binding이 있는 Collection hard delete 또는 referenced policy purge는
safe `knowledge_collection_privacy_binding_active`로 zero-write 차단하고 Organization manager가 먼저 exact
impact/expected-revision unbind를 완료해야 한다. Cascade delete는 허용하지 않는다.

현재 `KnowledgeDeploymentPreflightService`는 privacy manifest/validity/legacy-retirement/cutoff를
검사하지 않는다. 따라서 이 행은 MBA-362의 필수 후속 구현 경계이며, 현재 active deployment가
Target privacy readiness를 보장한다고 주장하지 않는다.

Privacy outcome은 existing async job/status의 allowlist field로만 투영한다. Target safe
reason code는 다음과 같다.

| 조건 | `safe_reason_code` | Retry 의미와 side effect |
| --- | --- | --- |
| policy missing/stale | `knowledge.privacy_policy_unavailable` | 정책 remediation 뒤 fresh admission; provider/embedding/pointer write 없음 |
| raw parser approval/profile/readiness missing | `knowledge.raw_parser_egress_unavailable` | external parser upload/provider/embedding 없음 |
| local baseline failure | `knowledge.privacy_baseline_failed` | 원인별 bounded retry 가능; provider/embedding 없음 |
| baseline/provider action policy의 terminal block | `knowledge.sensitive_content_detected` | baseline block은 provider 0회, 모두 no embedding/finalization; canonical `policy.block` reason과 동일 |
| provider missing/inactive/revoked/timeout | `knowledge.detector_provider_unavailable` | required mode quarantine, baseline-only downgrade 없음 |
| malformed/fingerprint/coverage/span/response cap | `knowledge.detector_response_invalid` | non-retryable contract failure, partial span 폐기 |
| manual review/action 필요 | `knowledge.privacy_review_required` | review surface 전 production mode disabled |
| normalized input platform cap 초과 | `knowledge.privacy_input_too_large` | non-retryable, provider/embedding 없음 |

이 reason은 privacy/provider resource identity가 이미 authorized된 status context에서만
보인다. Parent/Organization/source scope 밖, hidden, denied 또는 stale source는 먼저
ADR-0010/ADR-0017의 safe `404 resource.hidden`을 적용하고 privacy mode/provider 존재를
노출하지 않는다. 이 ADR은 기존 HTTP status/error envelope을 일괄 변경하지 않는다.

Status, SSE, audit와 trace response에는 다음 값을 포함하지 않는다.

- raw/normalized/provider-view/canonical/chunk body
- parser/provider endpoint/name/config/credential/request id와 exception
- exact span/confidence/category count 또는 source/span/canonical digest
- raw source title/path/url, principal/ACL, hidden resource/count
- internal policy/provider/fence/claim identity
- internal `legacy_artifact_ref`, internal migration wave/item primary key와 exact chunk/index membership

허용되는 값은 authorization된 document/job ref, safe state/reason/retryability,
bounded latency/outcome bucket, opaque attempt/manifest correlation과 non-sensitive contract
revision이다. Canonical migration-wave/cleanup AuditLog에만 server-issued opaque wave/receipt/tombstone
ref를 exact allowlist로 허용하며 이 값은 internal primary key나 membership을 복원할 수 없고 public
status/SSE/trace/log/metric response에는 나타나지 않는다. Provider/policy/egress-approval management 및 review endpoint를 후속 이슈에서 만들 때는
actor, exact revision CAS, same-Organization scope, credential capability, revoke/expiry,
mode-provider-action compatibility, expected current validity revision/epoch, server-derived
`platform|organization` scope/effect와 canonical audit transaction을 safe list/detail projection과 같은
API contract에서 닫아야 한다. Review detail은 redacted candidate와 bounded safe outcome만
허용하되 source-managed candidate는 projection 직전 fresh requester source authorization/display
policy를 통과해야 한다. Mask/approve/reject도 commit 직전에 같은 gate를 다시 확인하며 revoke winner는
`404 resource.hidden`, candidate/ref/state 비노출과 zero mutation/audit로 닫는다. Raw 확인은
ADR-0014가 정의한 미래 dedicated raw/compliance
surface의 별도 permission/source/audit/retention 경계를 사용해야 한다. Current
`GET /api/v1/knowledge/{kb_id}/documents/{document_id}/content`는 이 surface가 아니며 해당
권한/audit/retention 계약 없이 review/compliance endpoint로 재사용하지 않는다. Target enforcement
cutover는 Nodease가 보존한 upload/fetch 원문 copy를 exact inventory로 freeze하고 valid opt-in과
retention/legal-hold 보존 조건을 모두 충족한 item만 encrypted protected raw artifact로 이관한다.
Protected migration 조건을 충족하지 않고 hold가 삭제를 막지 않는 item은 non-readable fence 뒤 물리 삭제해야 하며, no-opt-in
legal-hold conflict는 자동 이관하지 않고 activation을 차단한다.
Destination integrity 또는 purge와 original-copy physical absence를 확인한 terminal
`protected_migrated|purged` disposition이 하나라도 없으면 enforcement를 활성화하지 않는다. Raw body
response는 별도로 dedicated raw permission, fresh source ACL, pre-response access audit와
retention/legal-hold/purge gate를 모두 통과한 `protected_migrated` item만 dedicated surface에서 반환한다.
Current route 차단은 storage migration/purge의 대안이 아니며 raw-derived retrieval은 frozen legacy wave
계약만 따른다.

### MBA-232 Workflow Runtime Candidate Resolver

이 계약은 public HTTP request/response가 아니라 Workflow Engine 내부 application/port
contract다. Gateway의 Builder/deployment-preview `KnowledgeCandidateResolver`와 다른
책임을 가진다. MBA-232에서 `/api/v1/*` path, Workflow graph field, Client schema,
deployment preflight와 LLM node wiring은 추가하거나 변경하지 않는다.

Input:

| 필드 | 규칙 |
| --- | --- |
| `audience` | `AuthenticatedAudience(organization_id, user_id)` 또는 `AnonymousPublicAudience(organization_id)` closed union. Owner/builder/deployment owner/credential principal/service account fallback 금지 |
| `direct_kb_ids` | Server-owned configured order. Duplicate는 first position 유지. Defensive cap 20 |
| `collection_ids` | Server-owned 명시 selected Collection configured order. Missing/empty는 stream 0개이며 organization-wide fallback 금지. Defensive cap 20 |
| `candidate_budget` | Server-owned unique KB cap. 1 이상 20 이하 |
| `candidate_scan_cap` | Server operations cap. Selected Collection을 공정하게 scan하며 response에 exact hidden 구조를 노출하지 않음 |

Authenticated resolution은 direct KB에 KB `use`와 applicable materialized source gate를
요구하고 Collection `route`는 요구하지 않는다. Collection child는 active Collection
`route`, membership, child KB `use`, applicable materialized source gate를 모두 요구한다.
Knowledge domain permission과 Collection `read/manage/sync`는 이 gate를 대체하지 않는다.

Anonymous resolution은 selected active public Collection child 또는 active public
Collection에 연결된 direct manual KB만 허용한다. Team/User/domain grant를 사용하지
않는다. Source/connector public exposure primitive가 현재 없으므로 source-managed KB는
모두 제외한다.

Output:

| 필드 | 규칙 |
| --- | --- |
| `status` | `resolved` 또는 `safe_no_result` |
| `candidates` | 최대 20개의 authorized canonical KB identity와 internal first provenance. Direct configured order 우선, Collection round-robin, canonical KB dedupe |
| `routing_mode` | `direct`, `collection`, `mixed`, `none` 중 fixed safe value |
| count summary | Configured/evaluated/eligible 수의 safe bucket만 허용. Hidden/denied exact count 금지 |
| `budget_limited` / warning | Deterministic budget 또는 bounded scan 도달 여부와 fixed safe warning |

Candidate policy exclusion은 safe omission이고 candidate 0개는 provider/retrieval 전
`safe_no_result`다. DB session, PostgreSQL snapshot, repository 또는 authorization helper
infrastructure failure는 partial candidate를 반환하지 않는 typed retryable
whole-resolution error다. Error는 fixed code/retryability만 가지며 raw SQL/exception,
identifier, source metadata 또는 payload를 포함하지 않는다. Partial KB retrieval timeout은
이 internal resolver output이 아니라 MBA-233/downstream Retrieval Orchestrator 계약이다.

Resolver adapter는 invocation마다 fresh PostgreSQL transaction을 열고 첫 query 전에
`REPEATABLE READ, READ ONLY`를 적용한다. Current Collection/membership/KB
lifecycle/readiness/permission/materialized provenance는 같은 snapshot에서 읽고 candidate
또는 authorization 결과를 invocation 사이에 cache하지 않는다. Live connector
`check_access*`와 runtime source authorization cache는 MBA-232에서 호출하지 않는다.
Membership은 configured Collection별 ordered LATERAL cap을 먼저 적용한 bounded
intermediate relation에서 round-robin ranking한다. Source-policy/provenance expiry는 같은
transaction에서 한 번 읽은 `transaction_timestamp()`를 전체 invocation에 재사용한다.

### MBA-233 Workflow Builder Collection Picker

`GET /api/v1/knowledge/llm-selectable-collections`는 authenticated Workflow Builder
전용 route-safe projection이다. Collection 관리 목록이나 MBA-232 runtime resolver
response를 재사용하지 않는다.

Request context:

| 항목 | 규칙 |
| --- | --- |
| Authentication | 로그인 사용자 필수 |
| Organization | `X-Organization-Id`로 해석한 active organization |
| Permission | active lifecycle이고 `sync_state != source_deleted`인 Collection에 대한 current user effective `route` |

서버는 organization/lifecycle과 effective `route`를 SQL query scope에 먼저 적용한 뒤
최신순 최대 500개를 반환한다. Unauthorized 최근 row를 먼저 500개로 자른 뒤
authorization하지 않는다. 따라서 500개보다 오래된 authorized Collection도 authorized
result cap 안에 있으면 후보에 포함된다.

Response:

```json
{
  "collections": [
    {
      "id": "00000000-0000-0000-0000-000000000000",
      "safe_label": "사내 문서"
    }
  ]
}
```

`safe_label`은 optional이며 approved safe metadata에 값이 없거나 display policy를
통과하지 못하면 `null`이다. Response에는 raw Collection name/description,
organization ID, lifecycle/source/system-managed field, member KB ID, exact child count,
permission row/capability, hidden/unavailable total을 포함하지 않는다. `read`, `manage`,
`sync` 또는 Knowledge domain action만 있고 `route`가 없는 Collection은 반환하지 않는다.
다른 organization, inactive/archived/deleted 또는 `source_deleted` Collection은 존재
여부를 구분하지 않고 생략한다. Schema/DB failure는 raw SQL/exception 없이 fixed safe
error envelope로 닫는다.

### MBA-233 Editable Graph Reference Authorization

Workflow draft save, Agent Builder apply, optimizer/model-routing graph persistence는
graph structural validation 뒤 current editor와 active organization으로 Knowledge
reference를 다시 authorize한다.

| Reference | Save-time gate |
| --- | --- |
| Direct KB | same organization, active, `sync_state != source_deleted`, retrieval-selectable, effective KB `use`, applicable materialized source authorization |
| Selected Collection | same organization, active, `sync_state != source_deleted`, effective Collection `route` |

Collection child membership/KB/source authorization은 save-time에 열거하지 않는다.
Graph에 direct KB와 Collection reference가 모두 없으면 구조 검증 뒤 authorization context와
DB permission query를 생략하여 organization이 없는 legacy non-RAG draft 저장을 유지한다.
Malformed Knowledge field는 이 short-circuit 전에 거부한다.
Reference 하나라도 실패하면 전체 write와 success audit을 commit하지 않는다. Hidden,
cross-organization, missing, inactive, revoked와 denied 상태는 외부에서 구분하지 않는
`knowledge_reference_unavailable` 계열 fixed code와 safe field path만 반환하며 UUID,
label, raw graph와 permission reason을 echo하지 않는다. Permission query/DB failure는
retryable safe infrastructure error이고 partial graph를 만들지 않는다.

Save authorization result, picker item과 preflight 결과는 capability/token이 아니다.
Direct execute/stream graph는 같은 structural contract를 통과하고 invocation-time
MBA-232 resolver로 current audience를 authorize한다.

### KB Permission Endpoints

MBA-176의 Knowledge 직접 권한 API는 Organization resource permission surface와 같은 응답 envelope를 사용한다. KB 권한은 organization membership의 대체물이 아니며, active organization member에게만 effective permission으로 적용된다.

List response는 `ResourcePermissionListResponse`를 사용하고 `resource_type`은 `"knowledge_base"`다.

```json
{
  "resource_type": "knowledge_base",
  "resource_id": "00000000-0000-0000-0000-000000000000",
  "organization_id": "00000000-0000-0000-0000-000000000000",
  "team_permissions": [],
  "user_permissions": [
    {
      "id": "00000000-0000-0000-0000-000000000000",
      "grantee_type": "user",
      "grantee_id": "00000000-0000-0000-0000-000000000000",
      "grantee_name": "User",
      "auth_state": "operator",
      "assigned_at": "2026-07-04T00:00:00Z"
    }
  ]
}
```

Grant request:

```json
{
  "auth_state": "operator"
}
```

- 허용 값은 `viewer`, `operator`, `builder`, `manager`다. `none`은 직접 grant request에서 거부하고, 권한 회수는 DELETE endpoint를 사용한다.
- Team grant는 기존 `team_knowledge_permissions`를 생성/갱신한다. User grant는 `user_knowledge_permissions`를 생성/갱신한다.
- 요청자는 organization manager 또는 해당 KB `manage` 권한을 가져야 한다.
- `X-Organization-Id`는 KB의 organization과 일치해야 한다. 다른 organization KB, hidden/deleted/archived KB, 존재를 드러내면 안 되는 대상은 safe 404/resource-hidden 계약을 따른다.
- User grant 대상은 같은 active organization member여야 한다. invited/suspended/removed/non-member 사용자에게는 grant를 생성하지 않는다.
- Grant/revoke 성공은 permission row 변경과 같은 DB transaction 안에 `team_knowledge_permission.*` 또는 `user_knowledge_permission.*` data-change audit row를 정확히 한 번 기록해야 한다. Core upsert와 bulk delete 경로는 ORM listener에만 의존하지 않는다.
- Effective KB permission은 organization manager override와 team/user direct grant 중 가장 강한 additive allow다. 직접 grant는 team grant를 낮추거나 deny할 수 없다.
- Source-managed KB retrieval에서는 KB `use` grant가 있어도 source ACL/requester authorization gate와 final evidence policy를 다시 통과해야 한다.

MBA-231부터 list/grant/revoke는 Organization manager, 해당 KB `manage`, 또는
domain `permission_delegate`를 허용한다. Domain delegator가 자신 또는 자신이
active member인 Team에 `viewer/operator/builder/manager` grant를 만드는 요청은
`409 policy.blocked`와 safe `policy_reason=knowledge.self_escalation`으로 차단한다.
Resource manager는 이미 해당 KB의 content-plane `manager`이므로 자기 grant를
갱신하는 행위가 권한을 상승시키지 않지만, 마지막 관리 경로 제거는 recovery
authority가 남아 있는지 검증한다.

### Knowledge Domain Permission Endpoints

| Method | Path | 목적 | 권한 |
| --- | --- | --- | --- |
| GET | `/api/v1/knowledge/domain-capabilities` | 현재 actor의 effective domain action과 UI capability 조회 | active organization member |
| GET | `/api/v1/knowledge/domain-permissions` | Team/User domain grant 목록 | Organization manager |
| GET | `/api/v1/knowledge/domain-delegation-subjects` | bounded Team/User safe 위임 대상 page | Organization manager |
| PUT | `/api/v1/knowledge/domain-permissions/teams/{team_id}/{permission_action}` | Team domain grant upsert | Organization manager |
| DELETE | `/api/v1/knowledge/domain-permissions/teams/{team_id}/{permission_action}` | Team domain grant revoke | Organization manager |
| PUT | `/api/v1/knowledge/domain-permissions/users/{user_id}/{permission_action}` | User domain grant upsert | Organization manager |
| DELETE | `/api/v1/knowledge/domain-permissions/users/{user_id}/{permission_action}` | User domain grant revoke | Organization manager |

`permission_action`은 `catalog_manage`, `permission_delegate`,
`lifecycle_manage`, `sync_manage`만 허용한다. PUT body는 optional `expires_at`만
받고 unknown field를 거부한다. Team은 같은 organization의 active Team, User는
같은 organization의 active member여야 한다. Expired row는 list history에 safe
상태로 표시할 수 있지만 effective capability에는 포함하지 않는다. Domain
grant/revoke와 audit는 한 transaction이며, raw principal, request payload,
resource label이나 source metadata를 audit에 저장하지 않는다.

Active subject 조건은 PUT grant에만 적용한다. DELETE revoke는 inactive Team,
deactivated/removed User를 다시 활성화하거나 membership을 복원하도록 요구하지
않고, active organization 안에서 `organization_id + subject_type/id + action`에
해당하는 기존 permission row를 `FOR UPDATE` 또는 동등한 row lock으로 고정한 뒤
삭제한다. 기존 row가 없으면 idempotent `204`이며 audit를 만들지 않는다. Row가
있으면 permission delete와 canonical audit를 같은 transaction에서 commit하고,
둘 중 하나라도 실패하면 모두 rollback한다.

Domain subject 응답은 active Team/User의 opaque id와 safe label만 반환하며 email,
raw principal, source identity를 포함하지 않는다. UI는 Team을 기본 선택으로 두고
User direct domain grant는 예외 경로로 제공한다.

Collection 관리 Client는 domain-derived control의 canonical source로
`GET /api/v1/knowledge/domain-capabilities`를 사용한다. 특히 private Manual
Collection 생성은 `can_create_collection`, public visibility control은
`can_change_public_visibility`를 각각 사용하고 Organization role이나 `actions` 배열을
Client에서 다시 조합하지 않는다. Collection list의 같은 이름 capability는 기존
관리 projection 호환을 위해 유지하고 정상 상태에서는 domain capability와 일치해야
하지만, 두 응답이 일시적으로 불일치하면 Client는 domain capability를 따르고 최종
인가 판단은 POST/visibility API가 다시 수행한다. Capability refresh가 실패하면
Client는 이전 허용 상태를 유지하지 않고 fail-closed한다.

### MBA-231 KB Object/Property Authorization Inventory

| Surface/path group | Gate | Scope/hidden response | Response boundary | Audit |
| --- | --- | --- | --- | --- |
| `POST /knowledge` | active organization member; creator `manager` bootstrap | active organization required | created KB safe metadata only | KB + creator grant + canonical audit in one transaction |
| `GET /knowledge`, `GET /knowledge/{kb_id}`, `GET /knowledge/{kb_id}/documents/{document_id}`, document safe status, RAG document progress SSE | `read` | active organization; list omits denied rows, direct hidden is 404, visible action denial is 403 | safe KB/document status와 allowlisted operational metadata만 반환. Encrypted config, connection/source identifier, raw/unknown nested metadata와 hidden count는 제외 | read/status polling has no mutation audit |
| `GET /knowledge/llm-selectable`, search-test, standalone Agent answer/stream | `use` + source authorization where applicable | active organization; hidden/source denial does not reveal KB/source identity | retrieval-visible evidence and redaction-safe citation/summary only | retrieval/answer canonical audit; no raw query/evidence payload |
| `POST /knowledge/candidates/resolve`, RAG recommendation, Agent Builder internal safe-reference consumption | caller-specific `read/use/route` composition | active organization and server-resolved candidate set | safe handles/labels/reason codes; hidden IDs, names, counts excluded | decision/audit summary uses safe reason codes only |
| KB settings PATCH, RAG upload to existing KB, document analyze/confirm/delete/process/preview | `write` | active organization; document must belong to authorized KB | mutation result and safe processing metadata only | settings uses KB update audit; upload/confirm/delete/process use document action audit; analyze/preview are non-mutating and emit no mutation audit; payload/content excluded |
| Document sync | `write` or bounded domain `sync_manage` | active organization; source-owned policy remains authoritative | safe queued/status response | document process action audit without credential/source payload |
| Manual original content | `content_read` | `X-Organization-Id` active organization; document must belong to authorized manual KB | validated content response; supported PDF is inline and every original file response uses `X-Content-Type-Options: nosniff`. Browser preview는 organization-scoped API client로 bytes를 받은 뒤 ephemeral Blob URL로 렌더링하며 native navigation으로 이 endpoint를 직접 열지 않는다 | access path must not place content in audit/trace |
| Source-managed original content | `content_read` + requester source authorization + approved display/raw policy | missing primitive/policy is fail-closed | no raw response in MBA-231 baseline | denied/safe decision only; no raw source metadata |
| KB permission list/grant/revoke | Organization manager, KB `manage`, or bounded domain `permission_delegate` | active organization; self/own-Team escalation is 409 policy block | safe Team/User permission projection | permission row and canonical audit in one transaction |
| KB safe catalog metadata | `manage` | active organization | allowlisted `safe_label`, description, topics only | metadata change audit in the mutation transaction |
| KB/Collection archive/restore | resource `manage` or matching domain `lifecycle_manage` | active organization; source-managed lifecycle mutation denied | 204/safe lifecycle projection | lifecycle row and canonical audit in one transaction |
| KB hard delete | Organization manager + `acknowledged_hard_delete=true` + approved retention/legal-hold gate | active organization; source-managed/retention policy fail-closed. Gate 미구성 baseline은 403 | allow된 경우 204; deleted resource is subsequently hidden | allow된 경우 permission cleanup + canonical audit + DB delete in one transaction; default deny는 mutation/audit 없음 |
| Private Collection link/unlink/reorder of KB routing membership | Collection/KB resource manage combination or domain `catalog_manage` | active organization; public Collection uses stronger exposure gate; active privacy binding이 있는 unlink는 safe conflict | safe Collection item projection; linking grants no KB content action or privacy policy binding | membership mutation and canonical audit in one transaction |
| Public Collection membership/visibility | Organization manager + explicit acknowledgement + source public approval | active organization; absent approval fails closed | safe visibility/membership state only | exposure mutation and canonical audit in one transaction |

위 `Manual original content` 행은 MBA-231 current surface를 기술한다. Current
`documents.file_path` 또는 content endpoint의 존재는 ADR-0070 Target의 protected raw artifact
opt-in, encryption, raw/compliance permission, access audit와 retention/legal-hold/purge 충족 증거가
아니다. Target cutover에서 이 route를 그대로 compliance surface로 간주하지 않는다. Nodease가
보존하는 upload/fetch 원문은 exact inventory에서 valid opt-in과 retention/legal-hold 보존 조건을 모두
충족한 item만 dedicated protected raw/compliance store로 이관한다. Protected migration 조건을 충족하지 않고 hold가 삭제를 막지
않는 item은 non-readable fence 뒤 물리 삭제하고 original-copy absence와 terminal disposition을
증명해야 한다. No-opt-in legal-hold conflict는 자동 이관하지 않고 activation을 차단한다. Current content route의 raw response는 이 storage disposition과 별개로
dedicated gate 준비 전 fail-closed해야 하며,
source system이 자체 보유하는 object의 opaque protected reference만 raw content copy와 구분한다.

Knowledge 최초 등록용 presigned upload은 대상 `knowledgeBaseId`와 active organization을
받아 KB `write` 및 initial document slot을 fast precheck한다. Workflow 입력 파일용 generic
presigned upload은 KB 식별자를 요구하지 않는다. 이 precheck는 concurrent request를 직렬화하지
않으므로 subsequent `/rag/upload`의 canonical row-lock check를 대체하지 않는다. 이미
업로드된 presigned object는 registration conflict에서 ownership이 명확하지 않으면 임의로
삭제하지 않는다. URL/proxy preview처럼 KB identifier가 없는 표면은 authenticated storage
ownership, filename/key validation, egress guard, size/content-type cap과 safe error contract를
계속 적용한다. Backend-owned artifact도 canonical conflict 또는 DB flush 이전 실패처럼
commit이 시작되지 않았음이 확실할 때만 보상 삭제한다. Commit 호출 이후 결과가
불명확하면 이미 커밋된 Document reference를 깨뜨릴 수 있으므로 자동 삭제하지 않는다.
Request-bound presigned upload intent와 만료·orphan reconciler는 `MBA-295`의 target이며,
그 계약이 없는 현재 baseline은 caller prefix만으로 direct object ownership을 확정하지 않는다.

DB source UI가 이번 요청에서 새 Connection을 먼저 생성한 뒤 canonical registration에서
`document_slot_occupied`, source policy, resource/permission rejection을 받으면 owner-scoped
`DELETE /api/v1/connectors/{connection_id}`로 보상 정리한다. 이 endpoint는 Connection row를
잠그고 어떤 Document의 allowlisted top-level 또는 `db_config.connection_id` metadata에도 참조되지 않은 경우에만
`204`로 삭제하며, 참조 중이면 `409 connection.in_use`, unknown/other-owner resource는
`404 resource.hidden`, persistence failure는 `503 connection.delete_unavailable`로 닫는다.
DB source registration은 같은 Connection row lock을 commit까지 유지해 reference 생성과
보상 삭제의 경합을 직렬화한다. Commit 결과가 불명확한 registration 오류에서는 Client가
Connection을 자동 삭제하지 않는다. 기존 DB Document의 process 설정에서 새
`db_config.connection_id`를 저장할 때도 같은 owner-scoped Connection row lock을 metadata
commit까지 유지한다. 따라서 delete가 먼저 commit되면 설정 저장은 `404 resource.hidden`,
설정 저장이 먼저 commit되면 delete는 `409 connection.in_use`로 닫힌다. 설정 화면의 최초
조회 뒤 다른 요청이 같은 Document를 먼저 갱신하면 Connection 다음 Document를 잠근 writer가
`updated_at`을 재검증하고 stale 요청을 `409 connection.reference_conflict`로 전체 rollback한다.
Reference metadata commit에서 발생한 PostgreSQL `40001`, `40P01`, `55P03`, `57014`도 lock 획득 실패와 같은 `503 connection.reference_busy`로 정규화하고, 기타 SQLAlchemy commit 오류는 `503 connection.reference_unavailable`로 닫는다. 두 경우 모두 background ingestion을 등록하지 않고 전체 transaction을 rollback한다.

Direct resource는 active organization으로 먼저 scope를 고정한다. Unknown,
cross-organization, deleted 또는 invisible resource는 `404 resource.hidden`,
same-scope visible resource의 action 부족은 `403 permission.denied`다. 목록은
unauthorized row와 hidden count를 반환하지 않는다.

Builder와 deployment preflight가 사용할 Gateway MBA-105 candidate resolver contract는 다음 shape를 지켜야 한다. 이 contract의 missing Collection scope fallback과 safe metadata response는 위 MBA-232 Workflow runtime resolver에 적용하지 않는다.

| 필드 | 규칙 |
| --- | --- |
| `actor` | Builder 또는 deployer subject. Candidate metadata 표시 권한의 기준 |
| `intended_execution_subject_id` / `audience` | Runtime availability 계산 기준. 없으면 availability를 `unknown` 또는 `unavailable`로 낮춘다. Phase 7 baseline은 요청 필드를 받되 runtime에서는 execution_subject 기준으로 다시 판정한다 |
| `mode` | `auto_collection` 또는 `explicit_kb` |
| `collection_ids` | Auto collection mode에서 서버가 해석한 route scope 후보. 누락 시 actor가 route할 수 있는 safe subset만 사용 |
| `knowledge_base_ids` | Explicit KB mode에서 서버가 safe handle, authorized picker, 또는 trusted backend context로 해석한 KB 후보. Collection route는 생략할 수 있지만 KB visibility/use/source ACL/final evidence preflight는 수행 |
| `purpose` | `builder_suggestion`, `deployment_preflight`, `runtime_preview` 같은 bounded enum |
| `max_collections` / `max_candidate_kbs` | 서버 cap. Baseline은 `max_collections <= 100`, `max_candidate_kbs <= 5000`을 강제한다. Cap은 route/use/source ACL helper를 통과한 authorized subset에 적용하며, 임의 row를 먼저 자른 뒤 authorization하지 않는다 |

Response는 safe candidate list와 summary만 포함한다. 각 candidate는 `candidate_id`, `candidate_type`, safe label, route availability, runtime availability(`available`, `warning`, `unavailable`, `unknown`), safe reason code, required action을 반환할 수 있다. Hidden KB id/name, exact denied count, raw source path/title/url, hidden source distribution은 반환하지 않는다.

### Workflow Builder RAG Recommendation

`POST /api/v1/knowledge/rag-recommendations`는 Workflow Builder/Agent Builder가 `StructuredRequest`에서 파생한 safe intent summary, 지식 요구사항, pending resolution을 기준으로 현재 LLM node schema에 맞는 RAG option 후보를 받기 위한 Builder 단계 API다. Agent Builder client가 직접 호출하는 public client endpoint가 아니라, Agent Builder backend가 인증 사용자, active organization, workflow/app scope를 server-resolved context로 확정한 뒤 호출하는 Knowledge domain boundary로 취급한다. HTTP request는 `KnowledgeCandidateResolver`가 만든 server-issued safe candidate set reference 또는 server-resolved scope hint만 전달하며, full safe candidate set 객체는 같은 backend 내부 service call에서만 소비할 수 있다. Request의 collection/KB scope 값은 candidate resolver hint일 뿐이며, ranking 단계가 raw KB id나 raw source metadata를 직접 해석해서 권한 후보를 만들면 안 된다.

Request body는 raw user input 전체가 아니라 Agent Builder가 구조화한 safe summary로 간주한다. Raw natural language 전체, raw prompt, hidden source 정보는 이 endpoint 입력이 아니다.

Public HTTP boundary에서는 client가 raw KB id를 보내 `explicit_kb` mode로 recommendation scope를 여는 요청을 허용하지 않는다. `explicit_kb`는 Agent Builder backend 또는 Knowledge domain 내부 service call처럼 safe handle, authorized picker, server-resolved context를 이미 통과한 trusted boundary에서만 사용할 수 있다. Public HTTP request에서 `mode=explicit_kb`가 오면 safe validation error로 닫고, `mode=auto`에 raw KB/collection id가 섞여 있으면 권한 판단에 사용하지 않고 무시한다.

| 필드 | 규칙 |
| --- | --- |
| `intent_summary` | 필수. `StructuredRequest`에서 만든 redaction-safe intent summary. 길이 cap과 control character normalization을 적용한다 |
| `target_step_ref` | KB 추천이 필요한 planned step reference |
| `node_purpose_summary` | LLM node 목적 safe 요약. Raw text는 durable metadata에 저장하지 않는다 |
| `safe_workflow_context_summary` | 현재 workflow 목적, 기존 KB 참조, 관련 노드 역할을 요약한 safe context. Raw graph payload, hidden source 정보, raw KB content를 포함하지 않는다 |
| `knowledge_requirement` | `requirement_id`, `query_topics`, `expected_evidence_type`, `required` 같은 지식 요구사항 |
| `safe_query_topics` | Agent Builder 구조화 단계에서 생성한 KB 추천용 safe topics. Adapter는 이 값을 1차 relevance 입력으로 사용하고 raw workflow/action term은 점수 입력에서 제외한다 |
| `pending_resolution_ref` | `resolution_id`, `slot_type=knowledge_base`, `slot_key`, `blocking` 같은 unresolved slot reference |
| `authorized_safe_candidate_set_ref` | 선택. KnowledgeCandidateResolver가 만든 safe candidate set의 server-issued reference. 없으면 Knowledge domain이 아래 scope hint를 기준으로 candidate resolver를 먼저 수행하고, recommendation ranking은 그 결과만 사용한다 |
| `mode` | `auto`, `auto_collection`, `explicit_kb`. `auto`는 adapter 내부 편의값이며 resolver 호출 전 bounded mode로 변환한다. Public HTTP boundary에서 `explicit_kb`는 허용하지 않으며 trusted backend/internal service boundary에서만 사용할 수 있다 |
| `collection_ids` | 선택. 서버가 active organization과 actor 권한 기준으로 해석한 route scope hint. Agent Builder client가 HTTP body로 보낸 raw collection id는 권한/scope 판단에 사용하지 않고 무시한다. Field 생략은 trusted backend/service boundary에서 actor가 route할 수 있는 서버 정책상 collection subset을 뜻하며, 같은 trusted boundary에서 명시적으로 `[]`를 전달한 경우에만 빈 scope로 해석해 recommendation을 만들지 않는다. Collection은 recommendation item으로 반환하지 않고 safe summary로만 제공한다 |
| `knowledge_base_ids` | 선택. 서버가 safe handle, authorized picker, 또는 trusted backend context에서 해석한 explicit KB 후보. Public HTTP `explicit_kb` 요청은 거부하고, Agent Builder client가 HTTP body로 보낸 raw KB id는 그대로 전달하거나 권한 판단에 사용하지 않고 무시한다 |
| `intended_execution_subject_id` | 선택. Runtime availability warning 계산용. 실행 권한 보장이 아니며 runtime은 다시 검증한다 |
| `max_recommendations` | 서버 cap. 초기 기본값은 5, 최대 20 |
| `max_collections` | Auto collection 후보 탐색 cap. 서버 기본값 20, 최대 100 |
| `max_candidate_kbs` | Auto collection에서 resolver가 만들 수 있는 KB 후보 cap. 서버 기본값과 최대값은 5000이며, 실제 response recommendation 수는 `max_recommendations`가 다시 제한한다 |
| `high_risk_domain` | Builder hint. `strict_citation` 같은 option recommendation에만 사용하며 권한, policy block, compliance decision에 사용하지 않는다 |
| `allow_query_rewrite` | `high_risk_domain`이 있는 경우 safe template 기반 `queryRewriteMode=template` 추천을 허용할지 결정한다. 이 값은 권한 후보를 넓히거나 LLM-assisted rewrite를 승인하지 않는다 |

HTTP request body는 full `authorized_safe_candidate_set` 객체를 받지 않는다. 같은 backend 내부 service call에서는 full safe candidate set 객체를 넘길 수 있지만, HTTP 또는 serialized boundary에서는 `authorized_safe_candidate_set_ref` 또는 server-resolved scope hint만 사용한다.

Response는 Agent Builder 내부 adapter 계약과 같은 top-level envelope를 반환한다. Agent Builder backend가 내부 service call이 아니라 HTTP boundary를 사용하더라도 같은 envelope를 소비해야 하며, recommendation item list만 단독으로 반환하지 않는다.

| 필드 | 규칙 |
| --- | --- |
| `status` | `recommended`, `clarification_required`, `no_candidate`, `unavailable` |
| `resolution_id` | 해결 대상 pending resolution id |
| `requirement_id` | 해결 대상 knowledge requirement id |
| `recommendations` | safe KB recommendation item 목록. `status=recommended`일 때 포함하며 각 item은 아래 허용 response field를 따른다 |
| `clarification_options` | 권한 확인된 추천 후보가 있는 경우, 또는 adapter unavailable fallback에서 사용자에게 표시할 safe option 목록. Agent Builder는 후보가 1개여도 이 목록을 사용자 선택 UI로 표시한다 |
| `user_safe_warning` | partial access, runtime availability, unavailable fallback 같은 사용자 표시 경고 |
| `fallback_reason` | `adapter_unavailable`, `no_candidate` 같은 safe reason code. Hidden resource identity나 exact count를 포함하지 않는다 |

Adapter가 unavailable이지만 권한 확인된 safe 후보 선택지를 제공할 수 있으면 `status=clarification_required`, `fallback_reason=adapter_unavailable`, `clarification_options`를 반환한다. Safe 후보 선택지도 제공할 수 없으면 `status=unavailable`, `fallback_reason=adapter_unavailable` 또는 동등한 safe reason code를 반환한다.

Recommendation item은 초기 구현에서 `candidate_type="knowledge_base"`만 반환한다. Collection label과 linked KB count는 `source_collection_summary` safe metadata로만 제공한다. 현재 Workflow LLM node는 `knowledgeBases`를 실행 입력으로 사용하므로 Agent Builder backend 내부 service call은 recommendation result를 runtime KB reference로 materialize할 수 있다. 단, HTTP 또는 serialized boundary의 response는 safe handle과 safe metadata만 반환하며 raw runtime KB id를 담은 materialized reference를 노출하지 않는다.

Apply/save 직전 materialization은 recommendation list의 현재 top-N 결과를 다시 소비하는 방식이 아니라, 이전에 발급한 safe candidate handle을 KnowledgeCandidateResolver의 권한 확인 candidate set 안에서 직접 재검증하고 runtime KB reference로 해석하는 backend/internal service boundary여야 한다. Ranking 변화 때문에 여전히 권한상 유효한 handle이 top-N 밖으로 밀렸다는 이유만으로 저장을 차단하지 않는다.

허용 response field:

| 필드 | 규칙 |
| --- | --- |
| `recommendation_id` | Opaque id. Hidden resource identity를 인코딩하지 않는다 |
| `recommendation_mode` | `auto_collection` 또는 `explicit_kb` |
| `candidate_type` | 초기 구현은 `knowledge_base`만 허용 |
| `candidate_id` | Agent Builder-facing server-issued safe candidate handle. Raw source id/path/url/title 또는 client-stable raw KB id를 직접 노출하지 않는다 |
| `safe_label` | Display-policy-approved label. 없으면 raw KB name fallback 금지, `null` 또는 generic label만 허용 |
| `materialized_knowledge_bases` | HTTP response에서는 empty/suppressed여야 한다. 같은 backend 내부 service call에서만 LLM node `knowledgeBases`로 변환 가능한 권한 확인 runtime KB ref list를 포함할 수 있으며, `MAX_RAG_RETRIEVAL_KBS=20` 이하로 제한한다 |
| `score` | Recommendation ranking에 사용한 normalized score. Raw retrieval/provider score를 직접 노출하지 않는다 |
| `confidence` | `high`, `medium`, `low` 중 하나. 추천 강도를 표시하며 Agent Builder는 이 값만으로 KB를 자동 선택하지 않는다 |
| `reason_category` | 추천 근거의 safe category. 예: topic keyword match, metadata match, collection context match |
| `threshold_result` | `high_confidence`, `close_score`, `below_threshold` 등 추천 강도, warning, failure 분기를 설명하는 safe 결과 |
| `recommended_options` | `queryRewriteMode`, `queryRewriteTemplate`, `evidenceSufficiencyPolicy`, `ragFailurePolicy`, `sourceTierPolicy`, `scoreThreshold`, `topK` allowlist만 허용. 검색 기본값은 `scoreThreshold=0.3`, `topK=5` |
| `source_collection_summary` | Safe collection id/label, route scope type, bucketed linked KB count 정도만 허용 |
| `provenance` | `recommendation_strategy`, `safe_reason_code`, `used_signals`, `matched_safe_terms`, bucketed counts 같은 redaction-safe summary |
| `runtime_availability` | `available`, `warning`, `unavailable`, `unknown`. Intended subject가 없으면 private 후보를 `available`로 올리지 않는다 |
| `warnings` | Safe warning code/message만 허용 |
| `summary` | Candidate/recommendation/warning/hidden-or-unavailable count는 bucketed 값만 포함한다 |
| `reason_code` | Recommendation이 없을 때만 safe reason code를 반환한다. Hidden resource identity나 exact count는 포함하지 않는다 |

`score`는 DB 저장값이 아니라 추천 요청 시점에 계산한 KB 단위 ranking 값이다. Ranking은 `safe_query_topics`와 KB safe metadata의 `kb_relevance`를 0.70 비중으로 두고, `source_tier`, `runtime_availability`, `sync_freshness`를 각각 0.10 비중으로 더한다. Relevance 입력은 KB candidate의 `safe_label`, `kb_safe_description`, `kb_safe_topics` 같은 allowlisted safe comparison text로 제한한다. `collection_safe_label`, `collection_safe_topics`, Collection name/description, collection id/count는 route/permission boundary와 `source_collection_summary`에만 사용하며 KB relevance score 계산에는 사용하지 않는다. 계층형 사용자 선택 응답은 0점 후보를 표시할 수 있지만 Intent LLM에 전달하는 `knowledge_candidates`에는 `score > 0`인 후보만 최대 20개 포함한다. Manual KB의 `name`/`description`은 sanitizer, length cap, secret/url/path 제거를 통과한 뒤 safe label/topics comparison text로 자동 생성할 수 있다. Source-managed KB는 display-policy-approved source safe metadata만 이 경로에 사용할 수 있다.

Manual KB는 `KnowledgeBaseResponse.safe_metadata`로 allowlisted safe metadata를 반환할 수 있다. Safe metadata 조회·수정은 `GET/PATCH /api/v1/knowledge/{kb_id}/safe-metadata`를 사용하며 `safe_label`, `kb_safe_description`, `kb_safe_topics`만 저장 대상으로 허용하고 secret/url/path/raw source key는 sanitizer 또는 allowlist에서 제거한다. 일반 `PATCH /api/v1/knowledge/{kb_id}`는 KB `write` 이름·설명·embedding model 경로이며 `safe_metadata` payload를 422로 거부한다. Detail 응답은 `can_edit_settings`와 `can_manage_safe_metadata`를 분리하고 각각 effective KB `write`와 `manage` action으로 계산한다. Resource manager는 additive RBAC에 따라 두 capability를 모두 가지며 creator 여부는 권한을 높이거나 낮추지 않는다. Safe metadata 변경의 action/data-change audit은 KB target id와 마스킹된 변경 필드를 남기고 metadata 원문 값은 저장하지 않는다. Source-managed KB recommendation은 저장된 manual override가 아니라 display-policy-approved source safe metadata만 사용한다.

KnowledgeCandidateResolver와 recommendation ranking은 retrieval-visible active version 경계를 지켜야 한다. `sync_state=source_deleted`인 KB와 current-valid active ready document version이 없는 KB는 원칙적으로 recommendation candidate에서 제외한다. ADR-0070 enforcement 뒤 legacy 예외는 privacy-compliant active pointer가 한 번도 확정되지 않은 document에서 frozen `legacy_unverified` wave membership, 아직 retire되지 않은 eligibility, frozen platform/Organization validity epoch와 current 두 epoch equality, `privacy_legacy_grace_v1` hard max 및 authoritative DB-time half-open cutoff를 prefilter/final evidence gate 모두 통과할 때만 허용한다. Invalidating epoch commit은 item projection/cleanup을 기다리지 않고 관련 legacy를 즉시 제외한다. Compliant pointer가 존재하거나 과거에 확정된 뒤 manifest가 stale/invalid가 된 document는 physical legacy artifact와 남은 cutoff에 관계없이 legacy로 fallback하지 않는다. Allowlist/wave 부재, stale validity epoch, cutoff equality/경과와 stale cache/vector result도 ready 근거가 아니다. Active document version이 `ready`가 아니거나 manifest가 current validity를 잃으면 selectable ready candidate가 아니며, response는 이를 권한 없음이나 hidden resource로 표현하지 않고 safe `candidate_not_ready` 또는 `indexing_in_progress` warning/fallback reason으로 표시할 수 있어야 한다. 기존 current-valid active ready version은 유지되지만 최신 sync 상태가 `stale` 또는 `failed`인 KB는 후보로 남길 수 있으나, safe warning과 score penalty 또는 낮은 confidence를 함께 제공해야 한다. 이 경고는 raw source path/title/url, raw source error, hidden document count를 포함하지 않는다.

Auto recommendation에서 route-allowed collection link 후보가 없고 client가 collection scope를 명시하지 않은 경우, resolver는 같은 active organization 안의 직접 권한 확인된 retrieval-visible KB를 safe candidate set fallback으로 평가할 수 있다. 명시적으로 빈 collection scope는 후보 없음으로 유지하며 direct KB fallback을 적용하지 않는다.

금지: raw workflow intent, raw node purpose, raw natural language 전체, raw source id/url/path/title, raw ACL fact, raw principal, raw skill body, hidden KB id/name, exact denied/hidden count, raw prompt/completion/provider response.

Validation 실패 응답도 같은 금지선을 따른다. Safe summary 입력이라도 Pydantic/FastAPI validation detail의 `input` 값으로 echo하지 않고, field path/type/message 수준의 sanitized error만 반환한다.

## Document Processing Status

Document processing status endpoints, including `GET /api/v1/knowledge/{kb_id}/documents/{document_id}`, `GET .../ingestion` and `GET /api/v1/rag/document/{document_id}/progress`, use the durable job when present. `pending`, due-policy 안의 `retry_scheduled`, valid running lease와 recent heartbeat를 legacy enqueue timeout만으로 `failed`로 바꾸지 않는다. Expired lease는 recovery task가 retry 또는 dead-letter로 전환하고 fixed safe message를 Document projection에 반영한다.

Redis progress and Redis lock availability are not part of the public contract. The API must not require Redis to avoid infinite `processing`; Redis unavailable paths either continue through local processing fallback or become a safe terminal failure.

새 ingestion table을 읽거나 쓰는 endpoint는 resource authorization 뒤 schema readiness를 검사한다. Missing/incomplete schema는 `503 knowledge.ingestion_schema_not_ready`와 allowlisted `details.reason`만 반환하며 DB exception, missing SQL, source config를 반사하지 않는다.

## Request Model

### Explicit KB Answer

Explicit KB mode는 알려진 `knowledge_base_id`를 입력받는다. 이 직접 모드에서는 collection route permission을 요구하지 않을 수 있지만, KB helper, source ACL/requester authorization, metadata filter, hierarchy mode, final evidence policy는 항상 적용한다.

MBA-105 standalone `/api/v1/rag/agent/answer`와 `/api/v1/rag/agent/answer/stream`은 `evidence_sufficiency_policy`를 `minimum_evidence` 기본값으로 평가한다. Evidence가 없으면 LLM을 호출하지 않고 safe no-result로 닫으며, evidence score 또는 strict citation 기준이 부족하면 safe insufficient-evidence 응답으로 닫는다. 이 응답은 hidden KB id/name, 권한 없는 문서명, exact denied count를 포함하지 않는다.

필수 목표 field:

| 필드 | 규칙 |
| --- | --- |
| `knowledge_base_id` | 필수. Active organization scope 안에서만 평가하고, scope 밖이거나 사용할 수 없으면 resource-hiding matrix를 따른다 |
| `generation_model_id` / `credential_id` | 필수. Preset/default credential selection은 별도 ADR이 승인되기 전까지 허용하지 않는다 |
| `query` | 필수. Raw query는 기본적으로 durable 저장하지 않는다 |
| `metadata_filter` | Permission/source ACL gate 이후 허용된 candidate 안에서만 적용 |
| `hierarchy_mode` | 현재 metadata-aware/hierarchical RAG 계약을 따른다 |
| `query_rewrite_mode` | 선택 목표 옵션. 기본값 `off`; deterministic/template rewrite는 opt-in. Rewrite는 접근 범위를 넓히지 않는다 |
| `evidence_sufficiency_policy` | MBA-105 standalone Agent answer에서 기본값 `minimum_evidence`로 적용한다. `strict_citation`은 더 엄격한 citation 개수 검증 후보이며, `off`는 운영 runtime에서 허용하지 않는다 |
| `source_tier_policy` | 선택 목표 옵션. Source-of-Truth Tier를 authorized evidence 안에서 ranking/tie-break/conflict hint로만 사용한다 |

### Auto Collection Answer

Auto mode는 arbitrary KB id를 permission bypass로 받지 않는다. 먼저 safe candidate set을 구성한다.

| 필드 | 규칙 |
| --- | --- |
| `collection_ids` | 선택. 있으면 먼저 collection `route` 권한을 확인한다 |
| `skill_ids` | 빌더 단계 선택 후보. 있으면 skill visibility, freshness/eval, display policy를 확인한다. Skill만으로 KB permission/source ACL gate를 충족할 수 없다 |
| `generation_model_id` / `credential_id` | 필수. Auto mode는 preset/default credential selection을 의미하지 않는다 |
| `max_collections` / `max_candidate_kbs` / `max_retrieval_kbs` | 서버가 강제하는 cap. 초기 baseline은 `max_route_collections=20`, `max_candidate_kbs=5000`, `max_retrieval_kbs=20`, `max_chunks_per_kb=8`, `max_total_chunks=50`이며 운영 설정으로 조정 가능하다. 제품의 영구 고정 계약이 아니다 |
| `metadata_filter` | Permission/source ACL candidate filtering 이후 적용 |
| `query_rewrite_mode` | 선택 목표 옵션. 기본값 `off`; rewrite는 접근 범위를 넓히지 않고 raw rewritten query는 durable metadata에 저장하지 않는다 |
| `evidence_sufficiency_policy` | 선택 목표 옵션. 기본값 `minimum_evidence`; 근거 부족 시 safe no-result 또는 insufficient-evidence 응답 |
| `source_tier_policy` | 선택 목표 옵션. 공통 LLM node의 RAG 옵션이며 ADR-0017의 source tier baseline을 따른다 |
| `query` | Candidate routing과 retrieval에 사용한다. Permission decision에는 사용하지 않는다 |

Router는 authorized safe candidate와 safe metadata만 받는다. Raw source ACL fact, hidden KB id, raw source title/path/url, exact hidden count, raw content는 router input에 포함하지 않는다. `collection_ids`가 없을 때 candidate source는 조직 전체 collection이 아니라 서버 정책상 actor가 route할 수 있는 collection subset이다.

Router candidate metadata는 safe identifier와 coarse summary로 제한한다. 예시는 `knowledge_base_id`, optional `collection_id`, safe redacted display label, coarse source type, safe classification/category/tag, coarse sync/source ACL state, request-scoped ranking hint다. Raw source id/url/path/title, raw principal, raw ACL row, exact hidden/denied count, credential value, prompt/completion, raw content는 router input이 아니다.

Skill candidate metadata도 같은 boundary를 따른다. Workflow Builder가 받을 수 있는 skill field는 safe skill id, skill version, safe display label, source-of-truth tier, freshness state, eval status, validation checklist id, redaction-safe routing hint 정도로 제한한다. Raw skill body, hidden source reference, raw source title/path/url, restricted document list, raw content, prompt/completion, provider raw response는 Builder input이 아니다.

## Manual Collection Management

Manual Collection 관리 API는 Knowledge 관리 영역에서 사용한다. Workflow Builder가 Collection을 생성/삭제하거나 권한을 관리하는 surface가 아니다.

### Collection CRUD

| Method | Path | 목적 | 권한 |
| --- | --- | --- | --- |
| GET | `/api/v1/knowledge/collections` | Collection 목록. content 권한이 없는 domain 관리자는 safe 관리 projection만 조회 | `collection.read`, Knowledge domain 관리 action, 또는 organization manager override |
| POST | `/api/v1/knowledge/collections` | private Manual Collection 생성 | organization manager 또는 domain `catalog_manage` |
| GET | `/api/v1/knowledge/collections/{collection_id}` | Collection 상세 | `collection.read`, Knowledge domain 관리 action, 또는 organization manager override |
| PATCH | `/api/v1/knowledge/collections/{collection_id}` | safe name/description/metadata 수정 | `collection.manage`, private manual Collection의 domain `catalog_manage`, 또는 organization manager override |
| DELETE | `/api/v1/knowledge/collections/{collection_id}` | physical delete가 아니라 archive 전이 | `collection.manage`, domain `lifecycle_manage`, 또는 organization manager override |
| POST | `/api/v1/knowledge/collections/{collection_id}/restore` | archived manual Collection을 active로 복구 | `collection.manage`, domain `lifecycle_manage`, 또는 organization manager override |

List response는 `collections`, `can_create_collection`, `can_change_public_visibility`를 포함한다. 각 Collection row는 `id`, `name`, `description`, `is_system_managed`, `sync_state`, `lifecycle_state`, `visibility`, bucketed linked/active KB count, caller action flags, `safe_metadata`, timestamps만 포함한다. Raw source title/path/url/principal, hidden KB name/id, exact denied count는 반환하지 않는다.

Create request는 `name`, optional `description`, optional allowlisted `safe_metadata`만 받는다. Manual Collection 관리 UI는 새 Collection에 관리용 `name`과 별도 nonblank `safe_metadata.safe_label`을 함께 보내지만, 기존 API/internal caller 호환성을 위해 request schema에서 label 자체는 optional이다. 제공된 `safe_label`은 string이어야 하며 Gateway가 공통 safe-text sanitizer, 255자 cap, control character·secret-like text·URL·email·path 제거를 적용한다. 정제 후 안전한 텍스트가 남지 않거나 타입이 잘못되면 입력값을 echo하지 않는 `400 validation.failed`로 거부한다. Client는 `is_system_managed=true`, source identity, raw source URL/path/title, permission row를 create body에 넣을 수 없다. Duplicate safe name은 safe `409 conflict`로 반환한다.

Update request는 visibility를 바꾸지 않는다. Public/private 전환은 별도 visibility endpoint만 사용한다. Manual Collection 관리 UI는 현재 `safe_metadata`를 보존하면서 `safe_label`을 교체해 label 수정이 다른 허용 metadata를 제거하지 않게 한다. Label이 없는 기존 Manual Collection은 raw `name` 자동 복사나 일괄 backfill 없이 edit surface에서 명시적으로 보완한다. System-managed Collection은 connector/sync가 소유하므로 manual update는 safe override가 승인된 field로 제한한다.

`safe_metadata.safe_label`은 표시용 metadata일 뿐 권한이나 runtime capability가 아니다. Collection picker는 저장된 manual safe label을 다시 정제해 반환하고 값이 없으면 `null`을 반환한다. Raw Collection `name`/`description`을 fallback으로 반환하지 않으며 Client는 `null`에 generic `지식 Collection` label만 사용할 수 있다.

List의 `lifecycle_state` query는 `active`, `archived`, `deleted` 중 하나이며 관리 UI는 active와 archived를 별도 page로 조회한다. Archive와 restore는 Collection row를 잠근 뒤 상태와 권한을 다시 평가한다. Restore는 manual archived Collection만 `active`로 전이하며 active Collection에는 새 mutation/audit 없이 idempotent `204`를 반환한다. `deleted` 또는 organization 밖 대상은 hidden 처리하고 system-managed Collection은 source owner 경계로 거부하며 `sync_state=source_deleted`는 safe `409`로 차단한다. Restore는 기존 permission과 membership을 보존하지만 새 permission, child KB `use`, Workflow `route`를 만들지 않는다.

### Collection Item Management

| Method | Path | 목적 | 권한 |
| --- | --- | --- | --- |
| GET | `/api/v1/knowledge/collections/{collection_id}/items` | linked KB item 목록 | `collection.read` |
| POST | `/api/v1/knowledge/collections/{collection_id}/items` | KB link | private: `collection.manage` + KB `manage` 또는 `catalog_manage`; public: Organization manager + acknowledgement |
| DELETE | `/api/v1/knowledge/collections/{collection_id}/items/{item_id}` | KB unlink | private: `collection.manage` + KB `manage` 또는 `catalog_manage`; public: Organization manager + acknowledgement |
| PATCH | `/api/v1/knowledge/collections/{collection_id}/items/reorder` | deterministic rank 변경 | private: `collection.manage` 또는 `catalog_manage`; public: Organization manager + acknowledgement |
| GET | `/api/v1/knowledge/collections/{collection_id}/link-candidates` | link 가능한 KB 후보 | 해당 membership mutation 권한의 safe 후보만 반환 |

`GET /items`, link와 reorder 성공 response는 `items`, opaque `order_revision`, `reorder_supported`, optional fixed `safe_reason_code`를 포함하고 항상 최신 전체 ordered item projection을 반환한다. 각 item은 `item_id`, `knowledge_base_id`, safe label, lifecycle/sync state, rank, caller action flags만 포함한다. Safe label은 유효한 `KnowledgeBase.safe_metadata.safe_label`, display-policy-approved source safe label, caller가 독립 KB `read`를 통과한 manual KB `name` 순으로 선택하고, 모두 사용할 수 없으면 generic `Knowledge Base`를 반환한다. Domain `catalog_manage`만으로 raw manual KB `name`을 fallback하지 않으며 link-candidate response도 같은 projection을 사용한다. `can_use_kb=false`인 item이 보일 수 있지만, 이는 runtime retrieval 가능성을 의미하지 않는다. Standalone GET은 `collection.read`를 요구하지만 link/reorder 성공 응답은 완료한 mutation authority를 다시 확인한 safe management projection이므로 별도 `read` grant를 만들지 않는다. Link/unlink는 같은 organization KB만 허용하며 archived/deleted KB는 link 대상에서 제외한다. Private Collection membership은 `collection.manage` + KB `manage`, 또는 domain `catalog_manage`로 관리할 수 있다. Public Collection의 link/unlink/reorder는 visibility 변경과 같은 public exposure mutation이므로 Organization manager와 `acknowledged_public_runtime_exposure=true`를 요구한다. Source identity/connector Collection 또는 source-managed child가 연관된 public link/reorder는 approval primitive 부재 상태에서 `source_public_exposure_required`로 차단한다. Duplicate link는 MVP에서 idempotent success로 처리할 수 있다. Link request의 optional `rank`는 legacy caller 호환용 deprecated field이며 서버는 값을 무시하고 Collection lock 아래 끝에 append한 뒤 전체 rank를 연속값으로 정규화한다.

이 membership은 검색 grouping/routing만 소유한다. Link/reorder는 Collection privacy policy를 KB에
자동 적용하거나 Organization privacy validity epoch을 변경하지 않는다. Active Collection Privacy Policy
Binding이 있는 item의 unlink는 membership/audit를 바꾸지 않고 safe `409`와
`safe_reason_code=knowledge_collection_privacy_binding_active`,
`required_action=remove_privacy_binding_before_unlink`로 차단한다. Organization manager가 별도 privacy
impact flow에서 binding을 제거한 뒤 fresh membership revision으로 unlink해야 하며, `catalog_manage`만으로
binding을 암묵적으로 제거할 수 없다.

Reorder request는 empty Collection을 포함한 현재 전체 item을 `{item_id, rank}`로 보내고 `expected_order_revision`을 반드시 포함한다. Item id와 rank는 각각 unique이고 rank는 정확히 `0..N-1`이어야 한다. 서버는 Collection과 membership row를 잠근 뒤 current revision, 현재 전체 item set과 request를 비교한다. Stale revision, 누락·추가 item 또는 concurrent link/unlink는 어떤 rank도 바꾸지 않는 safe `409`다. 같은 순서의 no-op은 새 audit를 만들지 않는다. 초기 관리 surface는 item 500개 이하만 reorder하며 초과 response는 `reorder_supported=false`, `safe_reason_code=item_reorder_limit_exceeded`로 고정한다. `order_revision`은 권한이나 조회 capability가 아니다.

### Collection Sync Jobs (MBA-265)

MBA-265는 KC `sync` action과 domain `sync_manage`를 durable asynchronous job에 연결한다.
초기 실행 대상은 active Manual Collection에 연결된 active/non-source-managed KB 중 legacy
`documents` row가 정확히 한 개이고 그 문서가 DB type인 document-level KB다. DB 문서와 FILE
또는 다른 DB 문서가 한 KB에 함께 있는 legacy multi-document KB, API child, source-managed
child는 실행하지 않는다. 신규 connector protocol이나 source-managed sync를 포함하지 않는다.

Collection management response의 `can_sync`는 caller 권한이고 `sync_supported`는 현재 adapter가
해당 Collection의 현재 child source 구성을 실행할 수 있는지 나타내는 safe boolean이다.
Projection과 POST는 같은 canonical eligibility scan을 사용하고 UI는 두 값이 모두 참일 때만
실행 버튼을 활성화한다. 이 boolean은 source/connection identity나 unsupported target count를
공개하지 않는다.

| Method | Path | 목적 | 권한 |
| --- | --- | --- | --- |
| POST | `/api/v1/knowledge/collections/{collection_id}/sync-jobs` | KC sync job 생성 또는 기존 single-flight job 재사용 | Organization manager, Collection `sync`, domain `sync_manage` |
| GET | `/api/v1/knowledge/collections/{collection_id}/sync-jobs/latest` | 현재 caller에게 허용된 최신 job safe projection | 요청 endpoint와 같은 current authority |
| GET | `/api/v1/knowledge/collections/{collection_id}/sync-jobs/{job_id}` | 특정 job safe projection | 요청 endpoint와 같은 current authority |

POST는 canonical UUID 형식의 `Idempotency-Key` header를 필수로 받는다. 서버는 원문을
response/audit에 반사하지 않고 SHA-256 hash로만 저장한다. 같은 organization, Collection,
key의 요청은 기존 job을 반환하고, 다른 key라도 queued/running job이 있으면 active job을
재사용한다. Accepted/reused job은 `202`와 다음 safe envelope를 반환한다.

```json
{
  "job": {
    "job_id": "00000000-0000-0000-0000-000000000000",
    "collection_id": "00000000-0000-0000-0000-000000000000",
    "status": "queued",
    "progress": "none",
    "safe_reason_code": null,
    "retryable": true,
    "requested_at": "2026-01-01T00:00:00Z",
    "started_at": null,
    "completed_at": null
  },
  "reused": false,
  "dispatch_deferred": false
}
```

`status`는 `queued`, `running`, `succeeded`, `partially_failed`, `failed`, `cancelled`로
제한한다. `progress`는 `none`, `started`, `progressing`, `most`, `complete` 중 하나다.
Response에는 job item, KB/document/source identity, exact total/success/failure count, raw
processor/connector error, connection/config/SQL/credential field를 추가하지 않는다. 알 수 없는
내부 reason은 `sync.internal_error`로 일반화한다.

Resource hiding은 active organization 밖 Collection/job 또는 Collection/job mismatch를
`404 resource.hidden`으로 처리한다. Same-scope visible Collection의 sync authority 부족은
`403 permission.denied`다. Archived/deleted/source-managed/system-managed/unsupported source는
mutation 전에 safe `409 policy.blocked` 또는 `sync.not_supported`로 닫는다. Sync 가능한 DB
target이 없으면 `409 sync.no_eligible_targets`, target cap 초과는 `409 sync.target_limit_exceeded`
를 사용하며 child identity와 exact count를 반환하지 않는다. 이 세 sync policy code만 error
code로 승격하고 알 수 없는 reason은 `409 policy.blocked`와 `sync.internal_error` projection으로
일반화한다.

Gateway는 job/audit/Collection pending commit 뒤 `workflow.knowledge_collection_sync.execute`
task를 발행한다. Publish 실패는 raw broker 오류를 반환하지 않고 `dispatch_deferred=true`인
queued job을 유지한다. Recovery task가 due/stale job을 다시 발행하므로 API caller가 새 key로
반복 요청할 필요가 없다.

Legacy DB connection은 organization column이 없으므로 worker의 organization authority gate와
별도로 Connection Use Resolver가 `connection.user_id == execution subject user_id`와 지원 DB
type을 검증한다. 문서당 source row limit은 1,000으로 상한 처리한다. Connection identifier,
selection/SQL, credential과 processor 원문 오류는 job response·task result·audit·log에 포함하지
않는다.

DB processor 결과에는 문서에 저장된 flat `selection_mode`, `chunk_range`, `keyword_filter`를 기존
ingestion과 같은 selection helper로 적용한다. 선택 결과가 비거나 malformed이면 새 active
version으로 전환하지 않고 safe configuration failure로 닫아 기존 active ready version을 유지한다.

### Collection Permission Management

| Method | Path | 목적 | 권한 |
| --- | --- | --- | --- |
| GET | `/api/v1/knowledge/collections/{collection_id}/permissions` | permission grant 목록 | `collection.manage`, domain `permission_delegate`, 또는 organization manager |
| GET | `/api/v1/knowledge/collections/{collection_id}/delegation-subjects` | bounded active Team/User safe 대상 page | permission 변경과 동일 |
| POST | `/api/v1/knowledge/collections/{collection_id}/permissions` | team/user 단일 action grant | permission 변경과 동일 |
| POST | `/api/v1/knowledge/collections/{collection_id}/permissions/bundles` | role bundle을 explicit action row로 원자 적용 | permission 변경과 동일 |
| POST | `/api/v1/knowledge/collections/{collection_id}/permissions/bundles/revoke` | bundle action 집합의 explicit row를 원자 회수 | permission 변경과 동일 |
| DELETE | `/api/v1/knowledge/collections/{collection_id}/permissions/{permission_id}` | grant revoke | permission 변경과 동일 |
| POST | `/api/v1/knowledge/collection-permissions/bulk-bundles` | 같은 subject/bundle을 1~50개 Collection에 원자 grant/revoke | 모든 target에 permission 변경 authority |

단일 grant request는 `subject_type=team|user`, `subject_id`, `permission_action=read|route|manage|sync`만 허용한다. Bundle request의 `role_bundle`은 `viewer`, `workflow_router`, `maintainer`, `sync_operator`이며 각각 ADR-0034의 explicit action 집합을 한 transaction에서 upsert한다. 별도 role row나 inheritance를 만들지 않는다. Bundle revoke는 저장된 role을 찾지 않고 현재 존재하는 매핑 action row만 삭제한다. 따라서 Maintainer(`read+manage`)를 부여한 뒤 Viewer(`read`)를 회수하면 `manage` row는 유지되며 UI도 이를 다시 Maintainer role로 추론하지 않는다. 없는 row의 회수는 idempotent unchanged다. Domain delegator의 self/own-Team grant는 `409 policy.blocked`로 차단하고, 마지막 manage 경로 회수는 safe denial 또는 Organization manager recovery를 요구한다.

Delegation subject query는 `subject_type=team|user`를 필수로 받고 optional `query`(정규화된 safe prefix, 최대 100자), opaque `cursor`, `limit`(기본 25, 최대 50)를 사용한다. Response는 `subjects[{subject_type, subject_id, subject_safe_label}]`와 optional `next_cursor`만 반환한다. 서버는 endpoint별 authority를 먼저 검증한 뒤 current organization의 active Team 또는 active member User를 UUID keyset으로 `limit + 1` 조회한다. Team/User name만 검색하고 email, login principal, raw source identity와 total count는 검색하거나 반환하지 않는다. 같은 page 계약을 Organization manager 전용 `/api/v1/knowledge/domain-delegation-subjects`에도 적용하며 cursor는 subject type과 정규화된 query가 바뀌면 거부한다.

Bulk bundle request는 `collection_ids`(unique, 1~50), `operation=grant|revoke`, `subject_type`, `subject_id`, `role_bundle`을 받는다. 서버는 UUID 정렬 순서로 Collection을 잠그고 모든 target의 organization scope, permission authority, self/own-Team grant 차단과 last-manage revoke 조건을 mutation 전에 검증한다. Actor가 모든 target의 effective `manage`를 가진 경우 resource authority를 domain `permission_delegate`보다 우선하고, 일부 target만 `manage` 가능한 경우 domain-delegate self/own-Team 차단을 유지한다. 하나라도 실패하면 permission과 audit 전체를 rollback한다. Grant는 active subject만 허용하고 revoke는 inactive Team 또는 removed/deactivated User의 기존 row 정리를 허용한다. Response는 `operation`, `subject_type`, `role_bundle`, `target_count_bucket`, `changed_count_bucket`, `unchanged_count_bucket`만 반환하고 Collection/subject id, label 또는 실패 target index를 반복하지 않는다.

### Public Visibility

```text
POST /api/v1/knowledge/collections/{collection_id}/visibility
```

Request:

```json
{
  "visibility": "public",
  "acknowledged_public_runtime_exposure": true
}
```

MVP에서 public/private visibility 전환은 organization manager만 허용한다. Public 전환에는 explicit acknowledgement가 필요하다. 전환 전 summary는 linked KB count bucket, active KB count bucket, safe sensitive-content warning, anonymous public-only runtime 영향 요약만 포함한다. Raw KB title/path/url, hidden KB id/name, exact denied count는 포함하지 않는다.

`safe_metadata["visibility"] == "public"`은 anonymous public-only runtime의 collection candidate inclusion flag다. 인증 사용자 KB `use`, source ACL requester authorization, final evidence policy를 대체하지 않는다.

Source-managed Collection 또는 source-managed KB가 anonymous public-only 후보가 되려면 collection public visibility와 별도 source/connector public exposure approval을 모두 통과해야 한다. Approval row는 `approval_scope`, scope별 target id, `approved_by`, `approved_at`, `expires_at`, `source_identity_id` 또는 connector/source target, `revocation_behavior`, reverification cadence, explicit acknowledgement를 저장해야 한다. `approval_scope`와 target field가 일치하지 않거나 expiry/reverification/revocation 조건이 빠진 broad connector-wide approval은 public-only 후보에서 제외한다.

MBA-176에서 source/connector public exposure approval primitive가 아직 구현되지 않은 경우, source-managed Collection은 manual child KB만 포함해도 anonymous candidate stream을 만들지 않고 source-managed KB도 public collection에 연결되어 있어도 anonymous public-only 후보로 승격하지 않는다. Deployment preflight와 runtime availability preview는 이를 warning이 아니라 `source_public_exposure_required` blocked reason으로 반환한다.

### Workflow Runtime RAG Execution Subject

Workflow runtime에서 RAG를 호출하는 API나 내부 service call은 server-resolved execution audience를 명시한다. MBA-232 contract는 interactive/current user를 `AuthenticatedAudience`로, subject 부재를 synthetic identity 없는 `AnonymousPublicAudience`로 표현한다. 승인된 service account/operator audience는 별도 lifecycle/approval 계약 전까지 MBA-232 closed union에 포함하지 않는다. Subject가 없으면 retrieval은 실패가 아니라 anonymous public-only로 낮아진다.

`/api/v1/deployments/{deployment_id}/run`의 `internal_chatbot`은 current user를 user execution subject로 주입하고 Runtime은 해당 user의 KB permission/source ACL을 다시 검사한다. `/api/v1/run-public/{url_slug}`의 공개 `chatbot`은 subject를 주입하지 않으며 `internal_chatbot`은 public surface에서 허용하지 않는다.

필수 계약:

| 항목 | 규칙 |
| --- | --- |
| `execution_subject` | Workflow run context에서 명시적으로 resolve한 current user. 있으면 `AuthenticatedAudience`의 KB permission과 materialized source authorization 평가 기준 |
| `subject_resolution_reason` | interactive user 또는 anonymous public-only 같은 sanitized reason. Service account/assigned operator는 별도 승인 전 MBA-232에 입력할 수 없다 |
| `workflow_owner_id` | 감사/소유권 표시에는 사용할 수 있지만, 명시 설정 없이 retrieval 권한 fallback으로 사용하지 않는다 |
| missing subject | Anonymous public-only retrieval. Active public collection에 연결된 active KB만 후보로 남기며 silent owner/user_id fallback은 금지 |
| ambiguous or unsupported subject | Private retrieval fail-closed. Anonymous downgrade가 안전하게 판정되지 않으면 safe no-result 또는 failure policy를 따른다 |

모든 운영 RAG mode는 `execution_subject` 기준의 KB permission/source ACL/final evidence gate 또는 anonymous public-only gate를 통과해야 한다. `general RAG`는 authorized/public resource 안에서 넓게 검색하는 mode이고, `task-aware` 또는 `permission-scoped RAG`는 authorized/public resource 안에서 후보를 더 정밀하게 줄이는 mode다.

### LLM node RAG 품질 옵션

Workflow Builder가 LLM node의 RAG 옵션을 구성할 때 다음 목표 옵션을 제안할 수 있다. 이 옵션은 전역 에이전트 기능이나 독립형 RAG 실행 노드 기능이 아니라 생성된 LLM node의 retrieval/generation 정책이다.

| 필드 | 의미 |
| --- | --- |
| `query_rewrite_mode` | `off`, `template`, `llm_assisted` 후보. MBA-105 runtime은 `off` 기본값과 `template` opt-in만 구현한다. Rewrite는 user query와 safe skill/template만 입력으로 사용하고, permission/source ACL candidate scope를 넓히지 않는다 |
| `evidence_sufficiency_policy` | `minimum_evidence`, `strict_citation` 후보. 운영 runtime에서는 `off`를 허용하지 않는다. 근거가 부족하면 safe no-result 또는 insufficient-evidence 응답으로 닫는다 |
| `rag_failure_policy` | 근거 부족 또는 실행 시점 availability 실패를 처리하는 정책. MBA-105 runtime의 구현 기본값은 `safe_no_result`이며, `fail_node`는 node 실패로 닫는다. Permission/source ACL failure는 hidden-safe reason만 허용한다 |
| `source_tier_policy` | Source-of-Truth Tier를 authorized evidence 안에서 ranking, tie-break, conflict resolution hint로 사용할지 나타내는 목표 옵션. Baseline candidate enum은 `legal_regulation`, `contract`, `company_policy`, `adr_decision`, `official_documentation`, `semantic_definition`, `operational_runbook`, `curated_query_corpus`, `conversation_or_thread`이며 최종 enum은 Legal/Compliance review에서 확정한다 |

현재 workflow graph의 LLM node data는 기존 camelCase convention을 유지하므로 구현 필드는 `queryRewriteMode`, `queryRewriteTemplate`, `evidenceSufficiencyPolicy`, `ragFailurePolicy`, `sourceTierPolicy`다. 공식 계약에서 snake_case로 설명한 값과 의미는 같으며, 공개 API shape를 새로 만들 때는 별도 API review에서 casing을 고정한다.

`query_rewrite_mode`가 켜져도 raw rewritten query는 raw prompt와 유사한 민감 입력으로 취급한다. Durable audit/trace/usage metadata에는 rewrite 적용 여부, 전략, safe template id 같은 summary만 저장한다.

`llm_assisted` query rewrite는 LLM 호출이므로 별도 승인 전까지 구현하지 않는다. 승인 시 execution subject, generation model/credential, credential `use` 권한, usage/cost 기록, timeout, token/cost budget, 실패 시 fallback을 확정해야 한다. Workflow runtime에서 실행되면 rewrite LLM call도 workflow 실행 주체 기준의 권한과 비용 기록을 따라야 한다.

## Response Model

### Knowledge Base Detail

`GET /api/v1/knowledge/{kb_id}`의 `documents[].chunk_count`는 물리적으로 저장된 모든 chunk row 수가 아니라, LLM RAG 후보 판단에 사용할 수 있는 retrieval-visible chunk 수다. Document-level KB에서 current-valid active ready document version이 있으면 해당 version에 연결된 chunk만 센다. Current MBA-105 compatibility에서 active version pointer가 아직 없는 전환기 legacy KB는 `document_chunks.document_version_id IS NULL`인 legacy unversioned chunk를 fallback으로 셀 수 있다. ADR-0070 enforcement 뒤에는 privacy-compliant active pointer가 한 번도 확정되지 않았고 frozen `legacy_unverified` membership과 아직 retire되지 않은 eligibility, frozen platform/Organization validity epoch와 current 두 epoch equality, `privacy_legacy_grace_v1` hard max 및 authoritative DB-time half-open cutoff를 통과한 chunk만 센다. Compliant pointer가 존재하거나 과거에 확정된 뒤 stale/invalid가 된 document, allowlist/wave 부재, stale validity epoch, cutoff equality/경과와 stale cache/vector result는 legacy `chunk_count`를 0건으로 처리한다. `documents.status`가 `completed`가 아니거나 active version이 `ready`가 아니거나 manifest의 platform/Organization validity epoch가 current 두 epoch와 일치하지 않는 artifact는 `chunk_count`와 selectable-ready 판단의 근거가 아니다.

이 값은 KB 상세 화면과 LLM node Knowledge Base picker가 같은 ready/not-ready 경계를 쓰도록 제공하는 safe availability signal이다. Raw source title/path/url, hidden document count, 권한 없는 document 존재 여부, non-allowlisted metadata는 포함하지 않는다.

`documents[].meta_info`와 `GET /api/v1/knowledge/{kb_id}/documents/{document_id}`의
`meta_info`는 동일한 fail-closed projection을 사용한다. 허용 후보는 bounded
`progress`/`processing_progress`, safe processing timestamp,
`processing_recovered_from_timeout`, bounded `chunking_mode`/`strategy`/
`upload_method`와 non-negative finite numeric `cost_estimate`다. 각 field는 기대
type, 범위, 길이 또는 enum 검증을 통과해야 한다. `api_config` 전체와
`url_encrypted`, `headers_encrypted`, `body_encrypted`, `connection_id`,
`source_identity_id`, connector/source ref, `db_config`, connection label/config,
unknown key/nested object는 반환하지 않는다. 내부 저장값이 encrypted ciphertext여도
API-safe metadata가 아니며, 새 field는 allowlist와 negative test가 함께 추가되기
전까지 응답에서 생략한다.

### Citation Identity

목표 citation field:

| 필드 | 의미 |
| --- | --- |
| `citation_id` | 이 응답 안에서 사용하는 opaque citation identity |
| `knowledge_base_id` | Document-level KB identity |
| `document_version_id` | Evidence로 사용한 active version 또는 historical version identity |
| `chunk_id` | Evidence chunk |
| `collection_id` | Collection을 통해 선택됐을 때의 선택적 attribution |
| `safe_source_ref` | 선택적 protected/HMAC source reference. Raw source id/url/path/principal을 대체하며 display policy와 protected source identity boundary를 따른다 |
| `rank` / `score` | Retrieval ranking summary |
| `metadata_summary` | Redaction-safe allowlist만 허용 |
| `content_preview` | 선택적 user-facing redacted/capped preview. Durable audit/trace/usage summary에는 기본 저장하지 않는다 |

### Skill provenance

Skill을 사용한 workflow draft, LLM node의 RAG 옵션, workflow test run은 다음 redaction-safe provenance를 선택적으로 반환할 수 있다.

| 필드 | 의미 |
| --- | --- |
| `skill_id` | Provider-neutral Knowledge Skill identity |
| `skill_version` | 사용한 skill version |
| `skill_freshness_state` | `fresh`, `stale`, `review_required`, `deprecated` 같은 freshness state |
| `skill_eval_status` | 평가 통과/주의/미실행 같은 safe eval 상태 |
| `source_tier` | 정책 문서, ADR/decision record, semantic definition, curated query corpus 등 safe source-of-truth tier |
| `provenance_summary` | raw source name/path/url 없이 source tier, validation checklist, safe source/version ref만 포함한 요약 |

Skill provenance는 source of truth를 대체하지 않는다. 실행 시점 citation은 계속 KB/document version/chunk/decision record 같은 근거 resource를 가리켜야 한다.

### Partial Result

Operational partial failure는 반환되는 모든 evidence가 KB permission, source ACL, final policy gate를 통과한 경우에만 safe partial result로 반환할 수 있다.

허용되는 safe marker:

- `partial_result=true`
- bucketed failed candidate count
- `some_sources_unavailable` 같은 safe reason summary
- retryability flag

기본 금지 항목:

- exact failed KB id
- exact hidden/denied count
- unavailable document를 추론하게 하는 source distribution
- raw exception message

### RAG Strategy Summary

A/B 테스트, 비용 최적화, trace side panel은 다음 redaction-safe summary만 사용할 수 있다.

| 필드 | 의미 |
| --- | --- |
| `retrieval_strategy` | `general`, `permission_scoped`, `task_aware`, `metadata_aware`, `hierarchical` 같은 실행 전략 |
| `rag_mode` | UI/실행 설정에 표시되는 RAG mode |
| `selected_collection_count` / `selected_kb_count` | authorized subset 기준 count. hidden/denied resource를 추론할 수 있으면 bucket 처리 |
| `retrieved_chunk_count` / `citation_count` | 실제 evidence로 사용된 chunk/citation 수 |
| `context_token_estimate` | RAG context token 추정치 |
| `retrieval_latency_ms` | Retrieval latency |
| `permission_filter_applied` | KB permission/source ACL gate 적용 여부. 운영 실행에서는 항상 true여야 한다 |
| `policy_result` | Final evidence policy 결과 |
| `partial_result` | Safe partial result 여부 |
| `safe_exclusion_summary` | 정확한 문서명/ID 없이 bucketed reason만 제공 |
| `evidence_sufficient` | Evidence sufficiency 결과. 권한 없는 resource 존재를 암시하지 않는 boolean 또는 safe status만 허용 |
| `insufficiency_reason` | `no_evidence`, `low_score`, `insufficient_citation`, `policy_filtered`, `operational_partial` 같은 safe reason class |
| `query_rewrite_applied` | Query rewrite 적용 여부 |
| `query_rewrite_strategy` | `template`, `llm_assisted` 같은 safe strategy summary. Raw rewritten query는 포함하지 않는다 |

Collection-derived candidate가 하나라도 있는 실행은 `authorized_kb_count`와
`selected_kb_count` exact 값을 생략하고 각각의 `_bucket` field만 저장한다. Candidate
수에서 유도되는 actual `fanout_concurrency`도 생략한다. Collection-derived evidence는
child KB별 결과 경계를 재구성할 수 있는 per-KB `rank`도 생략한다. 대신 최종 전역
정렬·dedupe·top-k 이후 1부터 부여한 `evidence_rank`는 result와 품질 trace에 저장할 수
있다. 이 값은 해당 invocation의 최종 evidence 순서이며 child KB 경계를 뜻하지 않는다.
Direct-only 실행은 current user가
명시적으로 선택하고 authorization된 KB의 기존 exact operational summary를 유지할 수 있다.
| `source_tier_used` | Authorized evidence 안에서 사용한 safe source tier summary |
| `evidence_count` / `min_score_bucket` | 실제 evidence 기준 count와 bucketed score summary. Hidden/denied count는 포함하지 않는다 |
| `skill_id` / `skill_version` | 사용한 Knowledge Skill 식별자와 version. 표시 가능 여부는 skill display policy를 따른다 |
| `skill_freshness_state` / `skill_eval_status` | Skill freshness/eval summary. Raw eval fixture나 hidden source ref는 포함하지 않는다 |

이 summary에는 raw chunk content, raw source title/path/url, raw ACL row, 권한 없는 KB/document id, exact denied count, raw rewritten query, raw prompt/completion/provider response를 포함하지 않는다.

## Permission And Error Contract

| Mode | 필수 gate |
| --- | --- |
| Auto collection mode | active organization, generation model/credential visibility, credential `use`, verified credential-model relation, listing surface의 collection `read`, router scope의 collection `route`, KB use helper, source-managed KB의 source ACL/requester authorization, final evidence policy |
| Explicit KB mode | active organization, generation model/credential visibility, credential `use`, verified credential-model relation, KB visibility/resource hiding, KB use helper, source-managed KB의 source ACL/requester authorization, final evidence policy |
| 빌더 단계 Knowledge Skill mode | active organization, skill visibility, skill safe metadata display, skill freshness/eval gate. Skill visibility는 collection route, KB permission, source ACL gate를 대체하지 않는다 |
| 실행 시점 LLM node의 RAG 옵션 | execution subject가 있으면 해당 subject 기준 KB permission/source ACL gate와 final evidence policy. execution subject가 없으면 anonymous public-only gate와 final evidence policy. Explicit KB mode는 collection route를 생략할 수 있지만 KB visibility/use/source ACL/final evidence gate 또는 anonymous public-only gate를 생략하지 않는다. 빌더 단계 skill selection이나 workflow 작성자 권한을 실행 시점 data access로 전파하지 않는다 |
| MBA-232 runtime selected Collection | explicit authenticated/anonymous audience, 명시 selected active Collection, authenticated `route` + child KB `use` + materialized source provenance 또는 anonymous public membership, active/ready KB, deterministic budget. Missing/empty Collection IDs는 fallback 없음 |
| Anonymous public-only Workflow RAG | active organization, active Knowledge Collection with `safe_metadata.visibility == "public"`, active linked manual KB, final evidence policy. Public exposure approval primitive가 없는 MBA-232에서는 source-managed 후보를 모두 제외한다. Workflow owner/deployment owner/app creator/`user_id` fallback 금지 |
| Collection management | `collection.manage`; 기존 KB linking에는 `kb.manage`도 필요 |
| Collection sync/remediation | `collection.sync` 또는 organization/admin operation policy. Raw content access를 의미하지 않는다 |
| Raw content/export | Dedicated raw/compliance endpoint only. Raw/compliance permission, source-managed KB의 fresh source ACL, retention/legal-hold/purge check, response 전 raw access audit이 필요하다. 최종 enum 이름은 RBAC ADR에서 확정한다 |

Response summary와 citation은 허용된 KB/document version/chunk identity, citation id, direct KB-local `rank`, 최종 `evidence_rank`, score, hierarchy path, safe filename/display label, safe metadata summary, policy result, partial marker, bucketed count, retryability, opaque correlation/request id 같은 redaction-safe field만 포함할 수 있다. Collection-derived evidence에는 child identity, optional collection id, KB-local `rank`를 포함하지 않는다.

Raw source id/url/path/title, raw source ACL, raw principal, raw source exception, raw query, raw rewritten query, raw answer, raw prompt/completion, raw provider response, raw skill body, hidden skill source reference, content preview, credential value는 durable audit/trace/usage metadata에 저장하지 않는다. `content_preview`는 user-facing response 전용이며 redacted/capped 상태로만 반환하고 durable summary에서 제외한다. Raw artifact를 활성화하더라도 dedicated raw/compliance flow에서만 노출하며 Agent answer, retrieval context, prompt construction, SSE stream에는 사용하지 않는다. Raw/compliance access audit은 safe reference, decision, reason code, retention/legal-hold summary, request/correlation identifier만 저장한다.

### Resource Hiding / No-result / Evidence Insufficiency Matrix

Resource hiding/no-result/evidence insufficiency API matrix는 [ADR-0017](../../decisions/ADR-0017-knowledge-integration-provisional-implementation-baseline.md)의 safe hidden/no-result/partial-result baseline과 [implementation_baseline.md](implementation_baseline.md)의 matrix를 따른다. MBA-105 구현은 아래 safe envelope를 testable contract로 사용한다.

- Active organization scope 밖, organization mismatch, deleted/archived hidden resource, requester source authorization denied 또는 source ACL stale/unmapped/ambiguous/unverified/revoked 상태가 존재를 드러낼 수 있는 경우.
- Scope 안에서 이미 보이는 resource의 KB `use` 또는 credential `use` 권한 부족.
- 허용된 evidence candidate resolution 이후 policy block.
- Permission/source ACL gate를 통과한 뒤 발생한 source/connector operational failure.
- Auto mode에서 권한 있는 candidate가 없는 경우.
- Anonymous public-only mode에서 public candidate가 없는 경우.
- MBA-232 snapshot/repository/authorization infrastructure failure. 이 경우 candidate partial result를 만들지 않고 retrieval/provider 전에 fixed safe retryable error로 종료한다.
- 권한 gate 이후 evidence가 없는 경우.
- Evidence score, citation coverage, source tier policy 기준으로 근거가 부족한 경우.
- Evidence sufficiency policy가 `policy_filtered` 또는 `operational_partial` reason을 반환하는 경우.

기준은 hidden KB/version/chunk identity를 드러내는 answer run, `rag.retrieve` success audit, citation id, trace metadata, durable summary를 만들지 않는 것이다. Scope 밖, organization mismatch, hidden deleted/archived resource, existence inference가 가능한 requester source authorization denied 또는 source ACL stale/unmapped/ambiguous/unverified/revoked 상태는 resource-hidden/404 또는 safe no-result로 닫는다. MBA-232 candidate resolver의 DB/snapshot/authorization infrastructure failure에는 partial result를 허용하지 않는다. Partial result는 complete candidate authorization 이후 downstream retrieval에서 발생한 operational failure에만 허용한다.

JSON/pre-stream error envelope는 `error.code`, `error.reason_code`, `error.message`, optional `correlation_id`, optional `retryable`만 포함한다. Hidden/resource-hidden path의 `message`는 generic text를 사용하고 target KB id/name/source path/count를 포함하지 않는다. Hidden/resource-hidden path의 external `reason_code`는 `resource.hidden`으로 일반화하며, `source_authorization.denied` 또는 `source_acl.stale/unmapped/ambiguous/unverified/revoked` 같은 세부 reason은 이미 존재가 authorized context에서 보이는 resource, admin/remediation context, 또는 내부 safe audit/trace allowlist에서만 사용할 수 있다. Stream 시작 후에는 HTTP status를 바꾸지 않고 `event: error` terminal event에 같은 semantic `code`/`reason_code`/`correlation_id`/`retryable` allowlist를 넣는다.

Safe no-result/insufficient-evidence response는 `status`, `evidence_sufficient=false`, `insufficiency_reason`, optional `partial_result`, optional bucketed failed candidate count, safe retryability만 포함한다. Hidden candidate id/name/count/source distribution은 포함하지 않는다.

현재 구현된 standalone single-KB `/api/v1/rag/agent/answer`와 `/api/v1/rag/agent/answer/stream` lifecycle, same-scope permission preflight blocked status, trace/usage correlation 경계는 [ADR-0013](../../decisions/ADR-0013-rag-answer-trace-usage-correlation-boundary.md)을 따른다. Source-managed KB, auto collection, multi-KB mode의 target resource hiding 확장은 [ADR-0017](../../decisions/ADR-0017-knowledge-integration-provisional-implementation-baseline.md)의 provisional baseline을 따른다. 이 기준은 ADR-0013의 현재 단일 KB 계약을 재정의하지 않는다.

## Workflow User Citation Sidecar

Workflow/Chatbot 최종 응답은 사용자 표시용 Citation이 있을 때만 아래의 additive reserved sidecar를 포함할 수 있다. 이 계약은 privileged lineage Citation과 별개이며 내부 resource identity를 제공하지 않는다.

```json
{
  "__nodease_citations": {
    "version": 1,
    "items": [
      {
        "citation_id": "evidence-1",
        "evidence_rank": 1,
        "label": "공통 휴가 정책",
        "page_number": 3,
        "section": "연차 신청",
        "content_preview": null
      }
    ]
  }
}
```

- item은 최대 8개이며 `citation_id`는 응답 내 전역 `evidence_rank`와 일치한다.
- `content_preview`는 `detailed` mode에서만 최대 300자의 정제된 prompt evidence를 담는다. 공통 fail-closed redaction을 먼저 적용하므로 secret/PII 검출 또는 redaction 실패 시 preview를 생략한다.
- `knowledge_base_id`, `collection_id`, `document_id`, `document_version_id`, `chunk_id`, raw filename/path/URL, score와 child-local rank는 금지한다.
- sidecar는 서버가 만든 projection만 추가한다. legacy final output 또는 stream node-result map에 같은 key가 이미 있으면 기존 output을 덮어쓰거나 제거하지 않고 Citation sidecar만 생략한다.
- Citation이 없거나 설정이 `hidden`이면 sidecar를 생략한다. sidecar 생성 실패는 답변 자체를 실패시키지 않는다.
- 이 sidecar는 사용자 응답용이며 durable run output과 일반 audit/trace payload에는 저장하지 않는다.

## Trace And Audit

- Multi-KB 또는 collection-routed answer는 `trace_payloads.rag_answer_run_id`나 `llm_usage_logs.rag_answer_run_id`를 추가하지 않는다.
- Standalone Agent answer lifecycle은 [ADR-0013](../../decisions/ADR-0013-rag-answer-trace-usage-correlation-boundary.md)에 따라 `rag.answer.*`와 `rag_answer_runs`를 사용한다.
- Workflow runtime RAG evidence는 계속 `trace_payloads.payload_kind='rag.retrieval'`를 사용할 수 있다. Standalone answer는 summary/citation을 RAG-owned record에 저장한다.
- Trace side panel에는 RAG strategy summary, citation id, 허용된 KB/document version/chunk identity, direct KB-local rank, 최종 evidence rank/score, safe metadata summary, token/cost/latency summary만 표시한다. Collection-derived evidence에는 child identity와 KB-local rank를 표시하지 않는다. 권한 없는 문서명/ID, raw source metadata, raw content, raw prompt/completion은 표시하지 않는다.
- Skill usage summary는 workflow draft, LLM node의 RAG 옵션, workflow test run, RAG strategy comparison에서 skill id, skill version, freshness state, eval status, safe source tier, safe provenance refs만 포함할 수 있다. Raw skill body, raw source title/path/url, hidden source refs는 표시하지 않는다.
