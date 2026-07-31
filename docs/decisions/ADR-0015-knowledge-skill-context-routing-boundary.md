# ADR-0015: Knowledge Skill과 LLM node RAG 옵션 구성 경계

Status: Accepted

Related ADRs: [ADR-0012](ADR-0012-metadata-aware-hierarchical-rag-boundary.md), [ADR-0013](ADR-0013-rag-answer-trace-usage-correlation-boundary.md), [ADR-0014](ADR-0014-knowledge-base-document-atom-and-collection-boundary.md)

## Context

Nodease의 현재 제품 방향은 전역 에이전트가 모든 사용자 질문에 직접 답하는 구조가 아니라, 시나리오 기반 Workflow Builder가 자연어 요청을 받아 실행 가능한 workflow 초안을 만드는 구조다. 사내 지식은 주로 workflow 생성 권한자가 "사내 정책 문서를 검색해 직원 질문에 답변하는 workflow를 만들어줘"처럼 요청했을 때, Builder가 LLM node의 RAG 옵션을 구성하는 데 사용된다.

이때 Builder가 모든 지식을 prompt에 넣거나 하나의 큰 에이전트 규칙으로 업무 지식을 처리하면 context 비용이 커지고, source 선택 기준과 권한 경계가 흐려진다. 따라서 빌더 단계에는 재사용 가능한 절차/context/routing artifact로 LLM node의 RAG 옵션 구성을 돕고, 실행 시점에는 생성된 LLM node의 RAG 옵션이 execution subject 기준 권한 gate를 다시 통과해야 한다.

최근 agent/skill 계열 제품과 문서에서는 domain-specific instruction, metadata, helper resource, evaluation을 묶은 재사용 가능한 "skill" 개념을 제안한다. Nodease도 이 아이디어를 참고할 수 있지만, 특정 provider API 기능에 종속되거나, skill이 KB permission/source ACL을 우회하는 새 경로가 되어서는 안 된다.

## Decision

`Knowledge Skill`은 provider-neutral한 Nodease 내부 artifact로 정의한다. Anthropic, OpenAI, Google 등 특정 provider의 skill/runtime 구현을 의미하지 않는다.

Knowledge Skill은 권한 source, source of truth, retrieval evidence가 아니다. Knowledge Skill은 Workflow Builder가 LLM node의 RAG 옵션을 구성할 때 참고하는 재사용 가능한 절차/context/routing artifact다.

- 어떤 source-of-truth tier를 우선 볼지에 대한 절차.
- 어떤 workflow 생성 요청에서 어떤 collection/KB 후보를 고려할지에 대한 routing hint.
- LLM node의 RAG query template, metadata filter, hierarchy mode, citation requirement 후보.
- 생성된 workflow를 테스트하거나 배포하기 전에 확인해야 하는 validation checklist.
- response shape guidance, evaluation/golden question reference.
- redaction-safe template 또는 checklist resource.

최종 근거는 항상 KB, document version, chunk, ADR, decision record, citation 같은 source-of-truth resource다. Skill은 이 source를 선택하고 LLM node의 RAG 옵션을 구성하는 절차를 설명할 뿐, 그 자체가 정책 원문이나 사실 근거가 되지 않는다.

## Boundary Rules

1. Skill metadata, body, attached resource는 모두 authorization과 redaction boundary 안에 있다. 전역으로 먼저 노출되는 metadata를 만들지 않는다.
2. 빌더 단계에는 active organization, skill visibility, display policy, freshness/eval gate를 통과한 safe skill metadata만 workflow 생성 제안에 사용할 수 있다.
3. Skill이 제안한 collection/KB reference와 LLM node의 RAG 옵션은 실행 시점 권한으로 확정되지 않는다. 생성된 workflow의 LLM node는 실행 시점의 `execution_subject` 기준으로 collection route, KB permission, source ACL/requester authorization, final evidence policy를 다시 통과해야 한다.
4. 이 ADR은 독립형 workflow RAG 실행 노드 도입을 승인하지 않는다. Knowledge retrieval, query rewrite, evidence sufficiency, source tier policy는 LLM node의 RAG 옵션으로 제공한다.
5. Skill name, description, tag, source tier, owner, version도 민감 metadata일 수 있으므로 display policy, redaction, length cap을 적용한다.
6. Skill body/resource에는 raw source content, raw source title/path/url, raw source principal, restricted document list, hidden KB id, raw ACL fact, credential value, prompt/completion/provider raw response를 저장하지 않는다.
7. 실제 문서 내용은 skill body가 아니라 workflow 실행 또는 테스트 실행의 authorized retrieval을 통해 가져온다. Retrieval은 KB `use`, source ACL/requester authorization, final evidence policy를 통과해야 한다.
8. Skill은 permission decision을 수행하지 않는다. Permission helper가 만든 result만 소비하거나 routing hint로 사용할 수 있다.
9. Skill code execution은 이 ADR에서 승인하지 않는다. Code-bearing skill이 필요하면 sandbox, approval workflow, egress guard, dependency policy, audit, timeout, resource cap을 별도 ADR/gate로 확정해야 한다.

## Freshness And Evaluation

Skill은 오래되면 폐기된 source tier나 잘못된 절차를 선택할 수 있다. 따라서 target model은 다음 상태와 검증 정보를 포함해야 한다.

- `skill_version`
- `freshness_state`: `fresh`, `stale`, `review_required`, `deprecated`
- `last_validated_at`
- `source_version_refs` 또는 safe source-of-truth reference
- `eval_status`
- `eval_pass_rate` 또는 bucketed quality marker
- golden question/regression dataset reference

Stale 또는 review-required skill은 운영 workflow 생성 자동 후보나 실행 시점 RAG procedure로 사용하지 않는다. 운영 정책이 허용한 빌더 단계 review/remediation surface에서만 표시하거나 테스트할 수 있다.

## Provenance

Skill을 사용한 workflow draft, LLM node의 RAG 옵션, workflow test run, RAG strategy comparison은 redaction-safe provenance summary를 남길 수 있다.

허용 예시는 다음과 같다.

- skill id
- skill version
- freshness state
- eval status
- safe source-of-truth tier
- safe source/version reference
- validation checklist id

금지 항목은 raw skill body, hidden source reference, raw source title/path/url, raw content, raw ACL fact, exact hidden/denied count, raw prompt/completion/provider response다.

## Consequences

- Knowledge feature는 `KnowledgeCollection`과 document-level KB 외에 Knowledge Skill을 target artifact로 다룬다.
- Workflow Builder 또는 Agent Builder는 workflow 생성/수정 제안에서 Knowledge Skill을 사용할 수 있지만, 실행 시점 RAG 권한은 계속 Knowledge permission helper와 source ACL gate가 결정한다.
- Audit/Trace/Cost Optimizer는 skill usage summary를 기록할 수 있지만, allowlist된 safe summary만 사용한다.
- Skill registry, skill versioning, skill freshness/evaluation, skill publication/approval은 구현 전에 data model/API/UX gate를 거쳐야 한다.

## Non-Goals

- 이 ADR은 특정 provider의 skill API를 채택하지 않는다.
- 이 ADR은 임의 코드 실행 skill을 승인하지 않는다.
- 이 ADR은 skill을 source of truth나 retrieval evidence로 승격하지 않는다.
- 이 ADR은 skill visibility/permission storage schema를 확정하지 않는다.
- 이 ADR은 사용자가 skill을 작성, 테스트, 승인 요청, publish하는 UI/UX를 확정하지 않는다.
- 이 ADR은 Workflow Playground, canvas 작업 공간/사용 공간 분리, workflow 배포 승인 흐름과 skill binding의 연결 방식을 확정하지 않는다.
- 이 ADR은 전역 에이전트 Q&A 구조를 승인하지 않는다.
- 이 ADR은 collection router, multi-KB retrieval, Workflow Builder/Agent Builder 구현을 완료했다는 뜻이 아니다.
