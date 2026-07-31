# ADR-0020: Knowledge MCP incremental sync boundary

Status: Accepted

Related ADRs: [ADR-0014](ADR-0014-knowledge-base-document-atom-and-collection-boundary.md), [ADR-0017](ADR-0017-knowledge-integration-provisional-implementation-baseline.md), [ADR-0018](ADR-0018-workflow-rag-anonymous-public-only-runtime.md)

## Context

ADR-0017은 source-managed Knowledge Base retrieval이 mbased KB `use`와 fresh source ACL/requester authorization 두 gate를 모두 통과해야 한다고 결정했다. 이후 MCP/API connector 기반으로 Slack, Drive, Wiki 같은 외부 source를 incremental sync하는 구현 계획을 검토하면서 다음 세부 경계가 추가로 필요해졌다.

- MCP 서버를 LLM이 자유롭게 호출하는 tool surface로 둘지, Knowledge Source Connector 뒤의 제한된 adapter로 둘지.
- Live-linked mode가 source-side search를 사용할 때 권한 없는 title, snippet, count, score 같은 metadata side-channel을 어떻게 막을지.
- Runtime source authorization을 단건 per-candidate check로 구현할 때 source API rate limit과 latency가 병목이 되는 문제.
- Anonymous public-only runtime에 source-managed KB를 포함할 때 collection public flag만으로 충분한지, 별도 source public exposure approval을 어떻게 검증할지.
- Archived copy와 raw artifact 보존이 RAG/embedding/prompt input으로 오해되지 않도록 할 필요.
- 외부 source에서 가져온 파일, 페이지, archive, HTML, Office/PDF 같은 artifact가 macro, script, embedded object, executable, parser exploit, prompt injection 문구를 포함할 수 있으므로 network/egress gate 이후에도 content safety와 parser isolation gate가 필요하다.

## Options Considered

1. MCP 서버를 LLM tool-use surface로 직접 노출한다.
   - 장점: 구현 경로가 짧고 source별 기능을 빠르게 실험할 수 있다.
   - 단점: LLM이 raw source data, tool error, source metadata를 prompt와 trace에 직접 흘릴 수 있어 source ACL, prompt injection, secret/logging 경계가 약해진다.
2. MCP/API source를 Knowledge Source Connector 뒤의 server-side allowlist adapter로 제한한다.
   - 장점: operation, input/output field, timeout, retry, raw field 저장 금지, runtime authorization을 도메인 경계에서 검증할 수 있다.
   - 단점: source별 adapter contract와 테스트를 먼저 정의해야 하므로 초기 구현량이 늘어난다.
3. Source-managed KB public exposure를 collection visibility flag 하나로 처리한다.
   - 장점: anonymous public-only runtime 구현이 단순하다.
   - 단점: source owner가 승인하지 않은 외부 source item이 collection 공개만으로 public candidate가 될 수 있다.
4. Source/connector public exposure approval을 collection visibility와 분리한다.
   - 장점: source-managed KB의 원천 공개 승인, 만료, 회수, broad connector approval을 추적할 수 있다.
   - 단점: 별도 policy row와 helper 검증이 필요하다.
5. Content safety를 redaction/prompt injection guard만으로 처리한다.
   - 장점: 별도 scan/quarantine 상태 없이 redacted canonical text pipeline에 집중할 수 있다.
   - 단점: active content, archive 내부 executable, parser exploit, macro/script가 text redaction 이전 단계에서 worker와 storage/log 경계를 오염시킬 수 있다.
6. External source content를 별도 content safety와 parser isolation gate로 처리한다.
   - 장점: 지원 파일 타입, active content 차단, archive cap, parser sandbox, malware scan hook, quarantine/remediation 상태를 ingestion boundary에서 fail-closed로 고정할 수 있다.
   - 단점: source별 file type policy와 parser execution environment를 먼저 정해야 한다.

## Decision

MCP/API 기반 Knowledge incremental sync는 다음 boundary를 따른다.

### MCP and connector trust boundary

- MCP 서버는 Knowledge Source Connector 뒤에 있는 제한된 data access provider다.
- LLM runtime이 임의 MCP tool을 선택해 source raw data를 가져오고 prompt/context에 직접 삽입하는 구조는 허용하지 않는다.
- Nodease server-side connector adapter만 승인된 operation allowlist를 호출한다.
- Operation allowlist는 최소한 `list_sources`, `list_changed_items(cursor)`, `fetch_item_content(source_item_ref)`, `list_acl_changes(acl_cursor)`, `check_access_batch(subject_ref, source_item_refs[])`, bounded single `check_access(subject_ref, source_item_ref)`, `get_tombstones(cursor)`, 필요한 경우 `list_capture_events(cursor)`로 제한한다.
- 모든 operation은 input/output field allowlist, timeout, page size, response size, retry, rate limit, safe reason code, raw field 저장 금지 테스트를 가져야 한다.

### Source selection and storage modes

- 기본 수집 대상은 document-like artifact다. Drive/Wiki/Confluence/Notion page/file, Slack/Teams uploaded file/canvas, approved pinned/captured message artifact, approved bot-generated summary가 우선 후보다.
- 일반 channel 대화 전체, DM/private 1:1 대화, raw audio, raw transcript, source 권한과 provenance가 불명확한 copied message bundle은 별도 opt-in policy 전까지 기본 제외한다.
- Chat 기반 지식 수집은 `/nodease-save`, 승인 reaction, pin, publish action 같은 명시적 capture signal을 KB 생성 후보로 사용할 수 있지만, capture signal은 runtime retrieval 권한이 아니다.
- `indexed_cache`가 MVP 기본 mode다. Nodease는 redacted canonical chunk, embedding, safe metadata, protected source identity, source authorization provenance만 기본 저장한다.
- `live_linked` mode는 Nodease 내부 chunk/embedding이 없으므로 일반 vector RAG 후보가 아니다. Requester-scoped source-side search API가 있거나, source-side search 결과가 opaque source ref만 반환되고 runtime authorization 이후에만 metadata가 노출될 때만 검색 후보가 될 수 있다.
- `archived_copy` mode는 기본 비활성이다. Protected raw artifact는 opt-in raw/compliance policy, encryption, retention/legal hold/purge, access audit, fresh source ACL gate가 닫힌 뒤에만 보존할 수 있다. Raw artifact는 RAG, embedding, prompt, answer stream, citation summary input으로 사용하지 않고 redacted canonical text만 사용한다.

### Content safety and parser isolation

- Outbound/network guard를 통과한 외부 content도 trusted content가 아니다.
- Source artifact는 지원 file type/content type allowlist와 parser policy를 통과해야 extraction 대상이 된다.
- Parser와 extractor는 macro, script, embedded object, external reference, executable payload를 실행하지 않는 least-privilege 또는 sandboxed worker에서 동작해야 한다.
- Archive는 depth, expanded size, file count, nested archive cap을 적용하고 executable, macro-enabled document, script-bearing file은 별도 opt-in policy 전까지 기본 quarantine/remediation 또는 fail-closed 처리한다.
- Malware/content scan integration은 provider-neutral hook으로 둔다. Hook이 없거나 scan 결과가 `unknown`이면 high-risk binary/Office/archive ingestion은 indexing-visible artifact로 진행하지 않는다.
- Content safety failure, unsupported type, scan timeout/unknown은 redacted canonical text, chunk, embedding, prompt, citation input을 만들지 않고 safe reason code와 remediation state만 남긴다.
- 의심 raw content, active content marker, parser exception raw detail은 audit, trace, log, retry/dead-letter payload, user-facing response에 저장하지 않는다.

### Runtime source authorization

- Runtime source authorization primitive는 `check_access_batch(subject_ref, source_item_refs[])`를 우선한다.
- Source가 batch를 지원하지 않으면 bounded concurrency, per-call timeout, aggregate timeout을 적용한 single `check_access` fallback만 허용한다.
- Runtime authorization primitive 자체가 없으면 private source-managed KB retrieval은 fail-closed다.
- Source subject mapping 상태 `unmapped`, `ambiguous`, `stale` revalidate 실패, `revoked`는 private source-managed retrieval에서 fail-closed다.
- Runtime access cache는 short-lived로만 허용한다. Cache key에는 organization, connector, protected source identity, source item 또는 document version, execution subject, mapping epoch, source ACL freshness epoch, operation을 포함한다. Subject-level `allowed`를 다른 source item에 재사용하지 않는다.
- Redaction 전 ephemeral content handle은 process/run-scoped short TTL handle이어야 하며 durable DB, retry/dead-letter payload, audit, trace, log, user-facing response에 handle value나 raw content를 저장하지 않는다.

### Public exposure

- `KnowledgeCollection.safe_metadata["visibility"] == "public"`은 anonymous public-only candidate inclusion flag일 뿐 source-managed KB public exposure approval이 아니다.
- Source-managed KB를 anonymous public-only 후보에 포함하려면 collection public visibility approval과 별도 source/connector public exposure approval을 모두 통과해야 한다.
- Public exposure approval은 `approval_scope` bounded enum(`connector`, `source_identity`, `collection`, `knowledge_base`)과 target field consistency를 검증한다.
- `approval_scope=connector`는 broad approval이므로 organization manager approval, explicit acknowledgement, `expires_at`, reverification cadence, revocation behavior가 모두 필요하다.
- Scope와 target field가 맞지 않거나 target이 없는 approval row는 invalid policy로 보고 public-only 후보에서 제외한다.
- Source public ACL만으로 organization-wide read/use 또는 anonymous public exposure를 만들지 않는다.

### Sync cursor and observability

- Content cursor와 ACL/permission watermark는 분리한다.
- Content cursor는 active document version finalization 이후 전진하고, ACL watermark는 source authorization provenance commit과 candidate cache invalidation 이후 전진한다.
- 일부 item 실패 뒤 cursor 전진은 source replay/dedup contract와 idempotency key가 있을 때만 허용한다. 없으면 실패 item 뒤 cursor를 기본 전진하지 않는다.
- Max attempts 이후 `dead_lettered` 격리는 허용하지만 re-drive/remediation path는 raw payload나 secret을 노출하지 않는다.
- Audit, trace, log, usage summary, citation summary에는 raw source content, raw source URL/path/title/principal, raw ACL row, raw MCP response/tool error, hidden KB id/name, exact denied count를 저장하지 않는다.

## Rationale

최종 결정은 option 2, option 4, option 6을 채택한다. Knowledge/RAG는 source-managed KB에서 mbased KB `use`와 source ACL/requester authorization을 모두 요구하므로, MCP/API connector도 같은 two-gate 모델을 깨지 않는 server-side adapter여야 한다. Runtime authorization은 batch 우선으로 정의해 대규모 retrieval의 source API rate limit과 latency를 낮추고, batch 미지원 source는 bounded fallback으로만 제한한다. Public exposure는 anonymous public-only runtime의 공개 범위를 넓히는 결정이므로 collection flag만으로 처리하지 않고 source/connector 승인 사실, scope-target consistency, expiry, revocation behavior를 별도로 검증한다. Content safety는 prompt injection/redaction보다 앞선 ingestion boundary 문제이므로, active content와 parser 위험은 redacted canonical text 생성 전에 fail-closed 또는 remediation으로 분리한다.

## Consequences

- Knowledge Source Connector의 MCP/API adapter 구현자는 operation allowlist와 safe normalized output contract를 구현해야 한다.
- Ingestion 구현자는 content safety scan hook, file type allowlist, parser isolation, active content quarantine/remediation 상태를 redaction/indexing pipeline 앞에 둬야 한다.
- Workflow/Builder/Runtime은 raw permission row, raw source ACL, raw MCP response를 직접 소비하지 않고 Knowledge Permission Helper와 safe candidate set만 사용한다.
- Live-linked source-side search를 지원하지 않거나 side-channel-safe opaque ref flow를 제공하지 못하는 source는 catalog/metadata 탐색 또는 manual open action으로 제한한다.
- Public exposure helper는 collection visibility JSON만 직접 신뢰하지 않고 approval scope validation을 수행해야 한다.
- Source별 cadence와 runtime authorization timeout/cache TTL은 공식 Knowledge implementation baseline과 load test를 기준으로 source policy에서 조정한다.

## Affected Official Documents

- [docs/architecture.md](../architecture.md)
- [docs/data_model.md](../data_model.md)
- [docs/glossary.md](../glossary.md)
- [docs/features/knowledge/requirements.md](../features/knowledge/requirements.md)
- [docs/features/knowledge/api_spec.md](../features/knowledge/api_spec.md)
- [docs/features/knowledge/component_spec.md](../features/knowledge/component_spec.md)
- [docs/features/knowledge/implementation_baseline.md](../features/knowledge/implementation_baseline.md)
- [docs/features/knowledge/test_cases.md](../features/knowledge/test_cases.md)
- [docs/features/connectors/requirements.md](../features/connectors/requirements.md)
- [docs/features/connectors/api_spec.md](../features/connectors/api_spec.md)
- [docs/features/connectors/component_spec.md](../features/connectors/component_spec.md)
- [docs/features/connectors/test_cases.md](../features/connectors/test_cases.md)

## Follow-up Review Notes

- Source별 connector implementation PR은 `check_access_batch` 지원 여부, fallback concurrency/timeout, source subject mapping state transition, source-side search side-channel behavior를 testable contract로 고정해야 한다.
- Binary/Office/PDF/archive ingestion을 구현하는 PR은 file type allowlist, parser sandbox, active content 차단, scan timeout/unknown handling, quarantine/remediation state, parser failure raw leakage negative test를 포함해야 한다.
- `source_public_exposure_policies` migration을 만들 때 `approval_scope`와 target field consistency constraint, expiry/revocation handling, audit-safe reason code를 함께 정의해야 한다.
- Live-linked mode를 실제 구현하는 PR은 requester-scoped source-side search가 없는 source를 일반 vector RAG 후보처럼 취급하지 않는 negative test를 포함해야 한다.
- Raw artifact opt-in storage, raw/compliance permission enum, service-account private runtime, destructive migration은 이 ADR의 승인 범위 밖이므로 별도 ADR 또는 feature decision이 필요하다.

## Non-Goals

- 이 ADR은 production destructive migration, raw artifact opt-in storage finalization, raw/compliance permission enum, code-bearing Knowledge Skill, global/main Agent retrieval path를 승인하지 않는다.
- 이 ADR은 Workflow runtime 전체 egress guard를 승인하지 않는다. Knowledge/RAG source collection surface에 대한 connector/adapter boundary만 다룬다.
- 이 ADR은 service account 또는 assigned operator를 통한 non-interactive private RAG를 승인하지 않는다.
- 이 ADR은 source public ACL만으로 KB `use`나 anonymous public exposure를 승인하지 않는다.
