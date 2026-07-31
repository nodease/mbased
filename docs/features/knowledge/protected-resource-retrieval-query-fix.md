# Knowledge Retrieval Query 보호 리소스 완결성 매트릭스

Status: Active

## 대상

| 항목 | 내용 |
| --- | --- |
| 기능/이슈 | Workflow RAG 검색어 오염 및 CrossEncoder 암호문 입력 회귀 수정 |
| 보호 리소스 | 권한을 통과한 Knowledge chunk와 ephemeral retrieval query |
| durable reference 위치 | 기존 graph/deployment의 `context_variable`; 신규 secret 또는 content 저장 없음 |
| 실행 진입점 | Workflow Engine LLM node, Gateway/Workflow retrieval service |
| 외부 I/O | 기존 embedding/LLM provider 호출. 신규 호출 없음 |
| 권위 문서 | Knowledge requirements, component specification, test cases, ADR-0013, ADR-0014 |

## 경계 상태

| 경계 | 상태 | 계약·구현·검증 증거 | 해당 없음 사유 |
| --- | --- | --- | --- |
| 정책·식별자·organization scope | 완료 | 기존 runtime candidate resolver와 KB `use`/source ACL 경계를 변경하지 않는다. `test_workflow_llm_node_applies_selected_kb_chunks_to_llm_prompt`가 허용 후보 뒤 query 소비 경로를 실행한다. |  |
| 관리 API·UI | 해당 없음 |  | 신규 command, query, picker 또는 권한 surface가 없다. |
| 저장·GraphMutation | 완료 | 기존 allowlist field인 `context_variable`만 demo graph에 설정하며 raw query와 chunk 평문을 저장하지 않는다. `test_new_employee_onboarding_chatbot_graph_matches_demo_contract`가 graph 계약을 검증한다. |  |
| Deployment preflight | 해당 없음 |  | 기존 optional scalar field의 소비 의미만 복구하며 protected reference나 credential 계약을 추가하지 않는다. |
| Runtime/background | 완료 | `LLMNode._rag_search_query`가 명시된 referenced variable만 bounded query로 만들고, 두 `RetrievalService._rerank` 구현이 authorized chunk를 메모리에서 복호화한다. Workflow/Gateway targeted tests가 query와 model input을 검증한다. |  |
| Transaction·TOCTOU·retry·lease | 해당 없음 |  | DB mutation, claim, lease, retry 또는 외부 부수효과를 추가하지 않는다. |
| Lifecycle·resource hiding | 완료 | 후보 권한·lifecycle 판정 순서와 safe no-evidence 경로를 유지한다. 검색어 선택은 후보 identity와 거부 사유를 노출하지 않는다. |  |
| Audit·trace·secret/PII redaction | 완료 | query는 ephemeral이고 기존 RAG trace allowlist에 추가되지 않는다. rerank는 암호문 fallback 없이 redacted canonical text만 사용하며 테스트 fixture는 synthetic text만 사용한다. |  |
| Legacy·migration | 완료 | `context_variable`이 없는 graph는 기존 rendered-prompt query를 유지한다. schema/migration 변경은 없다. |  |
| 공식 문서 정합성 | 완료 | Knowledge component specification과 test cases에 query/rerank 계약을 함께 기록한다. |  |

## 테스트 시나리오

| ID | 시나리오 | 기대 결과 | 증거 |
| --- | --- | --- | --- |
| RUNTIME-01 | `온보딩 질문: {{ question }}` prompt와 `context_variable=question`으로 실행한다. | embedding query는 `첫주차 일정 알려줘`만 받고 생성 prompt는 안내 문구를 유지한다. | `apps/workflow_engine/tests/nodes/test_llm_node_runtime.py` |
| REDACT-01 | CrossEncoder 후보 content가 암호문인 상태로 rerank한다. | model은 복호화된 redacted canonical text만 받는다. | Gateway/Workflow retrieval tests |
| LEGACY-01 | `context_variable`이 없는 기존 graph를 실행한다. | 기존 rendered user prompt query를 유지한다. | 기존 Workflow LLM node retrieval tests |
