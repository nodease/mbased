# ADR-0027: Agent Builder Pre-Intent Safe KB Context

Status: Accepted
Related ADRs: [ADR-0017](ADR-0017-knowledge-integration-provisional-implementation-baseline.md), [ADR-0026](ADR-0026-agent-builder-intent-and-connection-validation.md)

## Context

Intent LLM이 사용자 문장만 보고 `knowledge_required`와 `knowledge_topics`를 생성하면 새로 추가되는 Knowledge Base 이름과 topic을 알 수 없다. 그 결과 명시적인 사내 문서 chatbot 요청도 일반 LLM 요청으로 구조화되어 KB resolver 자체가 실행되지 않을 수 있다. 제품별 키워드를 코드에 계속 추가하는 방식은 KB 증가와 이름 변경을 따라갈 수 없다.

반대로 organization의 KB 전체 목록이나 raw document metadata를 prompt에 넣으면 권한 없는 resource 노출, prompt 크기 증가, hidden resource 추론 위험이 생긴다.

## Decision

Agent Builder는 Intent LLM 호출 전에 Knowledge 도메인의 기존 permission-aware candidate resolver를 실행한다. Resolver는 active organization, builder actor의 KB `use`, intended execution subject의 runtime availability, retrieval-visible active ready version 정책을 통과한 후보만 반환한다.

전체 inventory 대신 deterministic metadata relevance ranking 상위 20개만 bounded safe context로 투영한다. Intent LLM에 허용되는 field는 다음과 같다.

- server-issued opaque `candidate_handle`
- `safe_label`
- `kb_safe_topics`
- `kb_safe_description`
- `runtime_availability`
- 요청 시점에 계산된 bounded `relevance_score`

Raw KB UUID, collection id, source/document/chunk id, source path/URL/title, permission row, hidden/denied count, credential, raw content는 prompt에 포함하지 않는다. Source-managed KB는 display-policy-approved safe metadata만 사용할 수 있다.

Intent LLM은 `knowledge_required`, `knowledge_topics`, `knowledge_backed_llm` capability와 관련 가능성이 있는 `knowledge_candidate_handles`만 제안한다. 후보가 존재한다는 사실만으로 Knowledge 사용을 강제하지 않으며, LLM은 prompt에 제공되지 않은 handle을 만들 수 없다. Schema-valid 결과에 미발급 handle이 있으면 safe validation code로 1회 repair한 뒤 fail-closed한다.

LLM이 제안한 handle은 자동 선택이나 권한 부여가 아니다. Structured knowledge requirement에 safe hint로만 보존하고, 기존 Recommendation Adapter가 현재 권한과 safe metadata로 전체 후보를 다시 조회하고 점수를 계산한다. 사용자가 선택한 handle은 apply/save 직전 다시 materialize하며 권한 또는 readiness가 바뀌었으면 저장을 차단한다.

Pre-intent candidate 조회가 실패하거나 후보가 없더라도 Intent LLM 호출은 빈 candidate context로 계속할 수 있다. LLM은 일반적인 지식 필요성을 독립적으로 표현할 수 있고, raw fallback이나 hidden inventory 추론은 허용하지 않는다.

## Consequences

- 새 KB는 safe metadata와 permission/readiness 조건을 충족하면 코드 키워드 변경 없이 Intent LLM context 후보에 들어간다.
- KB candidate 조회가 Agent Builder message마다 추가되므로 조회는 20개 projection으로 제한하고 기존 resolver/ranking 경계를 재사용한다.
- LLM의 의미 판단과 backend의 permission/ranking/materialization 책임이 분리된다.
- DB schema는 추가하지 않는다. Opaque handle hint는 기존 structured request JSON 경계에만 저장된다.
