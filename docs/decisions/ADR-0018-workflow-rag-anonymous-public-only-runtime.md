# ADR-0018: Workflow RAG anonymous public-only runtime

Status: Accepted

Related ADRs: [ADR-0014](ADR-0014-knowledge-base-document-atom-and-collection-boundary.md), [ADR-0017](ADR-0017-knowledge-integration-provisional-implementation-baseline.md), [ADR-0020](ADR-0020-knowledge-mcp-incremental-sync-boundary.md)

## Context

ADR-0017은 모든 Workflow runtime RAG 실행에 명시적 `execution_subject`가 있는 보수적 목표 구조를 선택했다. 이 목표 구조에서는 schedule, webhook, API trigger 실행이 private KB retrieval을 수행하려면 deployment에서 승인된 service account 또는 assigned operator가 필요하다.

현재 MVP에는 이 구조가 필요 이상으로 무겁다. Interactive 실행은 로그인 사용자를 execution subject로 전달할 수 있지만 public app, webhook, schedule, API secret 실행에는 아직 service account/operator 지정이 없다. `execution_subject`가 없다는 이유만으로 모든 RAG를 실패시키면 공개해도 되는 public documentation search까지 막게 된다.

동시에 architecture는 workflow owner, deployment owner, app creator, `user_id`를 data-access subject로 조용히 대체해서는 안 된다. `user_id`는 actor, credential, audit context로 남을 수 있지만 private KB authorization fallback은 아니다.

## Decision

MVP에서 Workflow LLM node RAG는 아래 runtime contract를 따른다.

- Interactive authenticated workflow execution은 `execution_subject=current_user`를 전달한다.
- `execution_subject`가 있으면 runtime KB access는 Knowledge permission path에서 해당 subject 기준으로 평가한다.
- `execution_subject`가 없으면 runtime RAG는 그 이유만으로 실패하지 않는다. 대신 anonymous public-only retrieval로 낮춘다.
- Anonymous public-only retrieval은 `safe_metadata["visibility"]`가 `"public"`인 active Knowledge Collection에 연결된 active KB만 검색할 수 있다. Source-managed KB는 [ADR-0020](ADR-0020-knowledge-mcp-incremental-sync-boundary.md)의 source/connector public exposure approval도 통과해야 한다.
- `visibility`가 없거나 `"public"`이 아닌 값, archived/deleted collection, archived/deleted KB는 anonymous runtime에서 private 또는 unavailable로 본다.
- Anonymous public-only filtering 이후 candidate KB 또는 evidence가 없으면 node는 safe no-result를 반환한다. 단, 설정된 `ragFailurePolicy`가 node failure를 요구하면 실패로 처리한다.
- Workflow owner, deployment owner, app creator, builder, `user_id`는 private KB retrieval의 data-access fallback이 아니다.
- MBA-176은 이 runtime contract를 활성 배포 경로에 적용하기 위한 deployment preflight를 승인한다. Preflight는 subject 없는 public/API/webhook/schedule/chatbot/MCP surface에서 private KB 후보를 활성 배포로 올리는 것을 차단하되, service account 또는 assigned operator를 도입하지 않는다.

`KnowledgeCollection.safe_metadata["visibility"] == "public"`은 Nodease MVP runtime flag다. Source ACL, raw content approval, authenticated subject-based retrieval의 child KB permission grant가 아니다. Operator는 anonymous use 대상으로 연결된 모든 KB가 organization policy상 공개 가능할 때만 collection을 public으로 표시해야 한다. Source-managed KB는 collection public flag만으로 anonymous 후보가 되지 않으며, 별도 source/connector public exposure approval, scope-target consistency, expiry/revocation policy를 통과해야 한다.

## Consequences

- ADR-0017은 non-interactive run의 private RAG 목표 구조로 남지만 service account와 assigned operator는 후속 기능으로 분리한다. MBA-176 preflight는 해당 기능이 없을 때 private KB가 public/automatic surface에 배포되는 것을 막는 안전장치다.
- Public app, webhook, schedule, API secret 실행은 private KB access 없이 public Knowledge Collection을 사용할 수 있다. 단, source-managed KB는 ADR-0020의 public exposure approval을 함께 만족해야 한다.
- Automatic/non-interactive run에서 private KB access가 필요하면 명시적 execution subject를 resolve하고 audit하는 후속 기능이 필요하다.
- 문서와 테스트는 `execution_subject` 부재와 owner fallback을 구분해야 한다. 부재는 anonymous public-only이며 owner private access가 아니다.
- MVP의 기본 public/private 경계는 collection 단위다. Source-managed KB의 public exposure 보강 gate는 ADR-0020을 따르며, 이 결정은 document/chunk-level ACL을 도입하지 않는다.
- Preflight preview는 blocked 상태도 검사 결과로 반환할 수 있지만, 실제 active create/activation은 blocked 상태를 오류로 보존해야 한다.

## Non-Goals

- 이 ADR은 service account 또는 assigned operator 저장 schema를 승인하지 않는다.
- 이 ADR은 private RAG를 자동 실행에서 사용하게 하는 service account/operator UX/API를 승인하지 않는다.
- 이 ADR은 document/chunk ACL을 도입하지 않는다.
- 이 ADR은 source public ACL을 organization-wide KB `use`로 취급하지 않는다.
- 이 ADR은 raw source content, hidden KB id, exact denied count를 prompt, trace, audit, citation summary에 노출하는 것을 허용하지 않는다.
