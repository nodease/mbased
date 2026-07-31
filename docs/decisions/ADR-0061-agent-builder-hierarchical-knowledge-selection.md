# ADR-0061: Agent Builder Hierarchical Knowledge Selection

Status: Accepted

Related ADR: [ADR-0039](ADR-0039-knowledge-workflow-collection-routing-integration.md)

## Context

Agent Builder의 평면 Knowledge Base 후보 목록은 Collection 자동 라우팅과 특정 KB 직접 고정을 구분하지 못한다. 동일 KB가 여러 Collection에 연결된 경우에도 화면 위치별 상태가 분리되어 중복 선택과 중복 검색 위험이 있다.

## Decision

- 이 결정은 ADR-0039의 "Agent Builder recommendation은 direct KB만 materialize" 및 이를 후속 이슈로 미룬 조항을 대체한다. ADR-0039의 additive graph, save authorization, runtime 재검증, observability 비노출 경계는 그대로 유지한다.
- 계층형 Collection/KB 선택과 ranking 정책의 기능 소유 이슈는 MBA-294다. 현재 구현은 MBA-277 안정화 PR에서 함께 전달하되, 문서와 테스트는 MBA-294의 구현·미구현 상태를 별도로 추적한다. 이 통합 전달은 두 변경이 독립적으로 merge 또는 rollback되었다는 의미가 아니다.

- Agent Builder는 route 권한이 있고 operational 상태인 Collection과, 별도로 `use` 권한 및 operational 상태를 통과한 하위 KB만 계층 응답으로 제공한다.
- Collection route 권한은 하위 KB identity 열람 권한을 포함하지 않는다. 권한 없는 하위 KB의 ID, 이름, 개수는 응답하지 않는다.
- 외부 응답과 제출에는 실제 UUID 대신 서버가 발급한 opaque `collection_handle`, `kb_handle`, `selection_key`만 사용한다.
- 동일 KB는 어느 Collection에 나타나도 같은 `kb_handle`과 `selection_key`를 사용한다.
- Collection 선택은 실행 시 Collection membership을 해석하는 동적 라우팅이고, KB 선택은 graph에 직접 고정하는 참조다.
- Collection parent를 선택하면 현재 사용자에게 표시 가능한 전체 하위 KB도 같은 `selection_key` 기준으로 선택한다. 제출에는 Collection handle과 선택된 하위 KB handle을 함께 포함한다.
- Collection 하위 KB 일부만 남기면 parent는 indeterminate 상태와 `선택 수/전체 수`를 표시한다. 이 경우 Collection 동적 라우팅은 해제하고 남은 하위 KB만 직접 고정해, 보이지 않는 하위 KB가 의도와 다르게 runtime 대상에 포함되지 않게 한다.
- 동일 KB가 여러 Collection에 나타나면 한 위치의 선택 변경을 모든 위치에 동기화한다. Collection parent 전체 선택과 직접 KB 선택은 제출 전에 opaque handle 집합으로 canonicalize한다.
- GraphMutation은 `knowledgeCollections`와 `knowledgeBases`를 별도로 materialize한다. 선택 후 planner를 다시 호출하지 않는다.
- KB 점수는 관련도 0.70, source tier 0.10, availability 0.10, freshness 0.10으로 요청 시 계산한다.
- Collection 점수는 권한과 operational 검사를 통과한 전체 고유 하위 KB를 기준으로 최고 하위 KB 0.60, 상위 3개 평균 0.30, 안전한 Collection metadata 관련도 0.10으로 계산한다. 화면 표시 상한으로 잘린 KB도 점수에는 반영한다. Collection 크기 가산점과 중복 패널티는 사용하지 않는다.
- Collection/KB 표시 상한은 권한 및 lifecycle 필터, 전체 후보 점수 계산과 안정 정렬이 끝난 뒤에만 적용한다. 이름순 또는 조회순으로 먼저 자른 후보 집합에서 점수를 계산하지 않는다.
- 화면에 반환하는 Collection은 최대 20개, 고유 KB 후보는 최대 20개다. UI는 약 3개 행 높이를 유지하고 나머지는 스크롤로 탐색한다. 내부 후보 탐색과 권한·점수 계산에 사용하는 고유 KB 안전 상한은 5,000개이며 화면 표시 상한과 분리한다. 표시 상한 적용을 점수 계산 뒤로 미루더라도 이 내부 안전 상한은 해제하지 않는다.
- Collection-linked 후보와 비소속 direct KB 후보는 이 5,000개 내부 안전 상한을 공유한다. 표시 가능한 Collection이 있으면 적격 direct 결과에 최대 20개이자 작은 예산에서는 절반 이하인 bounded result slot을 먼저 배정한다. Direct 조회는 안정 정렬된 page를 순차 평가해 result slot을 채우거나 공유 평가 예산이 소진되면 중단하며, 권한·lifecycle·readiness를 통과하지 못한 행도 평가 예산에 포함한다. 실제 평가한 direct 후보 수를 제외한 나머지만 Collection-linked 후보에 사용한다. Direct 조회는 표시 가능한 Collection membership을 제외하며 두 경로의 권한·lifecycle·readiness·점수 계산 고유 KB 합계는 내부 상한을 넘지 않는다.
- 점수는 DB와 graph에 저장하지 않는다. 점수 내림차순, safe label 오름차순(없는 label은 마지막), opaque handle 오름차순으로 안정 정렬한다. source tier와 availability는 이미 KB 점수에 포함되므로 별도 tie-break로 다시 적용하지 않는다.
- 발급된 후보 handle의 적용은 새 추천 또는 현재 Top-K에 의존하지 않는다. 서버는 해당 resolution에 발급된 handle을 보존하고 적용 시 active organization, 권한, lifecycle과 runtime eligibility만 다시 검증한다.
- 발급 뒤 card handle이 stale이면 저장된 structured request로 Knowledge hierarchy만 다시 계산해 같은 `unapplied` resolution을 갱신하고 이전 Collection/KB 선택을 비운 뒤 사용자가 다시 선택하게 한다. 이미 `pending_ack|completed`인 resolution은 갱신하거나 재제출하지 않고 `knowledge_resolution_already_submitted`로 닫는다. 이 처리는 planner, 원래 자연어 요청, GraphMutation, workflow save와 acknowledgement를 실행하지 않는다.
- 활성 `after_graph` target의 Node Detail에서 선택한 real KB/Collection은 추천 response Top-K에 포함되지 않았어도 된다. 서버가 active organization의 opaque handle로 변환하고 권한, lifecycle과 runtime eligibility를 독립적으로 검증한다.
- 5,000개 내부 후보 상한은 추천 탐색과 점수 계산 경계다. 이미 발급되어 사용자가 제출한 최대 20개 handle을 적용할 때는 이 추천 상한으로 다시 자르지 않고 발급 범위 전체에서 재검증한다.
- Collection/KB 선택 배열은 순서가 없는 집합이다. 서버는 중복 제거 후 handle 오름차순으로 canonicalize하며, 같은 집합의 순서만 바꾼 재시도는 같은 결정으로 처리하고 실제 집합이 달라진 재시도만 conflict로 닫는다.
- `before_graph` Knowledge placement는 `target_step_id`로 해석되는 정확히 하나의 Knowledge-capable LLM node만 변경한다. target이 없거나 둘 이상이면 다른 LLM 전체에 복제하지 않고 validation failure로 닫는다.
- Runtime은 직접 KB와 선택 Collection의 authorized child KB를 ID 합집합으로 해석하고 KB당 한 번만 검색한다. 내부 provenance에는 직접 선택과 기여한 모든 Collection을 보존한다.
- 실행 직전에 Collection route 권한, KB use 권한, lifecycle과 operational 상태를 다시 확인한다.

## Consequences

- 기존 graph의 additive `knowledgeBases`와 `knowledgeCollections`를 사용하므로 DB migration은 필요하지 않다.
- 콘텐츠가 같지만 ID가 다른 KB의 의미 중복 판정은 이 결정의 범위가 아니다.
- 과거 direct-edit 세션의 평면 후보는 읽기 호환 경로로만 유지하며 제품 선택 UI나 제출 경로로 다시 활성화하지 않는다. `direct_edit_v1` 선택 화면은 `collections`와 `ungrouped_kbs` 계층 계약만 사용하고 flat-only 응답은 안전 오류로 차단한다.
