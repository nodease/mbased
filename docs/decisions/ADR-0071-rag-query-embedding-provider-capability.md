# ADR-0071: RAG query embedding provider capability 경계

Status: Accepted

## Related Decisions

- [ADR-0018](ADR-0018-workflow-rag-anonymous-public-only-runtime.md)
- [ADR-0036](ADR-0036-knowledge-runtime-candidate-resolution.md)
- [ADR-0057](ADR-0057-llm-credential-at-rest-encryption-and-rotation.md)
- [ADR-0064](ADR-0064-provider-execution-capability-boundary.md)
- [ADR-0066](ADR-0066-nested-llm-canonical-node-location.md)
- [ADR-0067](ADR-0067-production-https-and-operation-bound-outbound.md)
- [ADR-0069](ADR-0069-provider-usage-durable-ledger.md)
- [ADR-0070](ADR-0070-organization-detector-provider-and-pre-embedding-local-masking-boundary.md)

## Context

Workflow LLM node의 RAG 검색은 권한을 통과한 Knowledge 후보가 사용하는 embedding
model로 query vector를 만든다. Legacy 구현은 실행 사용자 또는 배포 작성자에서 유도한
사용자 ID로 credential을 다시 선택한다. 이 방식은 main generation의 deployment policy와
독립적이며 public·system 실행에서 Knowledge execution subject, credential principal,
billing principal과 audit actor를 혼동할 수 있다.

ADR-0064는 generation과 Memory summary provider 호출의 capability를 정했지만 query
embedding purpose, embedding model별 policy와 usage lifecycle은 정하지 않았다. 한 node가
서로 다른 embedding model을 쓰는 여러 Knowledge Base를 조회할 수 있으므로 node당
generation policy 하나를 재사용할 수도 없다.

## Decision

1. LLM Credentials의 기존 `ProviderExecutionCapability` aggregate에
   `purpose=query_embedding`을 추가한다. Main generation이나 Memory summary capability를
   query embedding에 재사용하거나 그 반대로 사용하는 것을 금지한다.
2. Deployment credential policy는 `purpose`를 저장한다. 기존 row와 purpose를 생략한 API
   요청은 `main_generation`이다. Main generation은 deployment version과 canonical
   `(container_path, node_id)`당 active row 하나, query embedding은 같은 location과 exact
   embedding model UUID당 active row 하나를 허용한다.
3. Query policy 작성은 active organization manager, immutable deployment의 Knowledge가
   설정된 LLM location, active embedding model, provider-compatible active credential,
   verified relation과 현재 credential `use` 권한을 검증한다. Generation credential,
   execution user, owner, organization default, 최신 row와 환경 변수 fallback은 금지한다.
4. `execution_subject`는 Knowledge 후보·source ACL·evidence 권한에만 사용한다.
   `credential_principal`은 policy에 고정된 사용자, `billing_principal`은 organization,
   `audit_actor`는 실제 user/public/system actor다. Public·system 실행에서도 credential
   principal을 Knowledge subject나 audit actor로 승격하지 않는다.
5. Workflow Engine은 application `QueryEmbeddingExecutionRuntime` port를 사용한다.
   `LLMNode`는 credential ORM/config, capability service, provider SDK와 usage ledger를 직접
   해석하지 않는다. Runtime dependency는 process-local로 주입하며 graph, execution context와
   task payload에 직렬화하지 않는다. Preflight plan은 organization과 canonical node에
   binding하고 execution request 및 opaque adapter state와 불일치하면 model projection이나
   provider I/O 전에 거부한다.
6. 권한을 통과한 후보가 없으면 model projection, policy, capability, credential, usage와
   provider를 호출하지 않는다. 후보가 있으면 distinct canonical embedding model마다 stable
   provider attempt와 capability를 하나 만들고 같은 model 후보는 invocation-local query
   vector를 공유한다. KB ID는 provider attempt 또는 usage dimension이 아니다.
7. Capability는 exact model/provider/credential policy와 revision, permission/relation/pricing/
   egress revision, canonical node invocation, execution admission, provider attempt, input token·
   byte·cost cap과 expiry에 binding한다. Query embedding의 output token cap, admitted output과
   actual completion usage는 항상 `0`이다.
8. Provider client는 query와 model로 실제 요청을 봉인한 opaque single-use invocation과 safe
   request bound만 반환한다. Final admission을 통과한 같은 invocation만 전송한다. Raw query,
   vector, credential material과 provider raw payload는 capability, API, graph, task payload,
   usage, audit, trace와 log에 저장하지 않는다.
9. Capability issue/admission, credential materialization과 model projection은 짧은 DB session에서
   끝내고 provider I/O 전에 commit·close한다. Credential revoke, 권한 회수, relation/model/
   provider/policy/pricing/egress revision 변경이 먼저 commit되면 외부 호출 없이 fail-closed한다.
10. Query outbound는 ADR-0067의 current server-owned provider endpoint와 guarded transport를
    사용한다. Durable intent와 `provider_started`는 전송 전에 ADR-0069 ledger에 commit한다.
    Success, definitive no-send/rejection failure와 outcome unknown은 같은 operation으로 수렴하며
    query 전용 ledger나 egress guard를 만들지 않는다.
11. Provider가 actual input usage를 제공하지 않거나 usage/vector가 malformed·non-finite·cap
    초과이면 성공으로 추정하지 않는다. Provider 전송 뒤 불명확한 결과는 outcome unknown으로
    닫고 자동 재호출하지 않는다.
12. Schema downgrade는 query policy, capability 또는 usage operation이 남아 있으면 중단한다.
    이를 main generation으로 변환하거나 자동 삭제하지 않는다.

## Rollout Boundary

MBA-351은 schema, management API, application port, capability/usage/egress adapter와 테스트를
구현한다. Existing deployment 생성 preflight와 legacy 실행 선택은 바꾸지 않는다. 일반 runtime
activation, mixed-version Gateway/worker drain, rollback과 legacy 제거는 MBA-320이 소유한다.
`QUERY_EMBEDDING_POLICY_WRITE_MODE`는 기본 `disabled`이며 이 상태에서는 main-generation policy
관리만 허용하고 query policy mutation은 safe `503`으로 거부한다. MBA-320은 migration 적용과
purpose-aware Gateway/worker 전체 수렴을 확인한 뒤에만 이 mode와 target runtime을 활성화한다.
Query policy row가 남은 상태에서 purpose-unaware binary로 rollback하지 않는다.

ADR-0070의 local masking은 Knowledge ingestion에서 redacted canonical content를 만드는 경계다.
Runtime RAG query를 detector로 마스킹하거나 retrieval 의미를 바꾸는 정책으로 해석하지 않는다.

## Consequences

- RAG query embedding은 generation과 독립된 명시 policy, capability와 billable usage로 추적된다.
- Public·system 실행에서도 Knowledge 권한과 credential 사용 권한이 섞이지 않는다.
- 같은 model을 사용하는 authorized 후보는 provider 호출과 비용을 invocation당 한 번만 만든다.
- Policy, durable usage 또는 guarded outbound가 준비되지 않은 target path는 fallback 없이 닫힌다.
