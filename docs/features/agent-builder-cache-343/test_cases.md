# MBA-343 Agent Builder Cache Spine Test Cases

Status: Draft

## 1. Test Policy

MBA-343은 순수 application contract와 기본 비활성 composition만 검증한다. Redis, provider live call,
database integration과 latency benchmark는 실행하지 않는다. 오류 assertion에는 원문 request, identity 또는
payload를 포함하지 않는다.

## 2. Contract Tests

| ID | Scenario | Expected Result |
| --- | --- | --- |
| ABC343-T001 | 최소 actionable new-workflow plan 생성 | `CachedIntentPlanV1` 생성 성공, immutable |
| ABC343-T002 | valid modify plan과 selected target logical contract | actual node/edge ID 없이 생성 성공 |
| ABC343-T003 | root unknown field 주입 | strict schema 거부 |
| ABC343-T004 | nested Knowledge/guidance unknown field 주입 | strict schema 거부 |
| ABC343-T005 | unsupported/clarification request type 사용 | schema 또는 semantic invariant 거부 |
| ABC343-T006 | malformed canonical ref와 registry version 사용 | safe validation code로 거부 |
| ABC343-T007 | logical step occurrence가 0 또는 capability와 불일치 | 거부 |
| ABC343-T008 | guidance의 parameter key가 허용 형식 밖 | 거부 |
| ABC343-T009 | plan mutation 시도 | frozen contract가 변경 차단 |
| ABC343-T010 | ordered capabilities/topics/guidance 구성 | 입력 semantic order 보존 |

## 3. Forbidden-content Tests

| ID | Scenario | Expected Result |
| --- | --- | --- |
| ABC343-T011 | graph/nodes/edges/position field를 root 또는 nested에 주입 | `forbidden_cache_content` |
| ABC343-T012 | workflow/node/edge/request/session/operation UUID 주입 | `forbidden_cache_content` |
| ABC343-T013 | credential, token, API key, password, Redis URL category 주입 | `forbidden_cache_content` |
| ABC343-T014 | explicit/actual parameter value 주입 | `forbidden_cache_content` |
| ABC343-T015 | KB/Collection ID, label, candidate/opaque handle 주입 | `forbidden_cache_content` |
| ABC343-T016 | raw provider response/audit payload field 주입 | `forbidden_cache_content` |
| ABC343-T017 | allowed `parameter_key`와 template ref 사용 | false positive 없이 허용 |
| ABC343-T018 | validation exception string/repr 검사 | payload value, UUID, request text가 없음 |

각 corpus는 encode와 decode 양쪽 경로에서 검증한다. Raw unsafe fixture는 test 함수 안에서 즉시 구성하고
snapshot, evidence 또는 log file에 기록하지 않는다.

## 4. Codec Tests

| ID | Scenario | Expected Result |
| --- | --- | --- |
| ABC343-T019 | tuple field를 포함한 valid plan encode/decode | JSON array가 strict tuple로 Pydantic JSON-mode 검증되고 structural equality와 byte-stable round-trip |
| ABC343-T020 | 같은 plan을 다른 dict construction order로 생성 | identical canonical bytes |
| ABC343-T021 | list order 변경 | 의미가 달라진 bytes; codec이 재정렬하지 않음 |
| ABC343-T022 | duplicate JSON key | `invalid_json` |
| ABC343-T023 | invalid UTF-8 | `invalid_utf8` |
| ABC343-T024 | trailing whitespace 또는 non-canonical key/spacing | `non_canonical_payload` |
| ABC343-T025 | NaN/Infinity 또는 non-JSON number | `invalid_json` |
| ABC343-T026 | unknown schema version | `unsupported_schema_version` |
| ABC343-T027 | configured max bytes 초과 | parse 전 `payload_too_large` |
| ABC343-T028 | object가 아닌 JSON root | `invalid_json` |

## 5. Port and Disabled Boundary Tests

| ID | Scenario | Expected Result |
| --- | --- | --- |
| ABC343-T029 | Protocol-compatible fake normalizer/store/rehydrator | typed load/save result를 포함한 type/interface contract 충족 |
| ABC343-T030 | disabled boundary `execute(planner_call, context)` | 동일 structured result와 `bypass/feature_disabled` 반환 |
| ABC343-T031 | count 가능한 Planner와 context field access를 감지하는 sentinel 전달 | Planner 1회, context field access 0회 |
| ABC343-T032 | disabled boundary constructor/signature 검사 | normalizer/store/rehydrator dependency 인수 0개 |
| ABC343-T033 | Planner가 기존 exception을 발생 | 변환·재시도 없이 같은 exception 전파, 호출 1회 |

## 6. Composition and Regression Tests

| ID | Scenario | Expected Result |
| --- | --- | --- |
| ABC343-T034 | `AgentBuilderComposition.orchestration()` 구성 | factory가 만든 disabled boundary를 service에 주입, DB/user/org 미전달 |
| ABC343-T035 | 기존 `orchestration()` dependency 구성 | 기존 `LLMAgentBuilderIntentExtractor`와 durable usage recorder 유지 |
| ABC343-T036 | 기존 valid intent request | extractor/provider call count와 structured result가 baseline과 동일 |
| ABC343-T037 | 기존 provider/schema/semantic failure | 기존 safe error mapping과 repair 상한 유지 |
| ABC343-T038 | cache package import scan | outer layer/Redis/provider import 0건 |
| ABC343-T039 | endpoint source scan | endpoint가 cache concrete type을 직접 조립하지 않음 |
| ABC343-T040 | repository diff scope scan | Redis/config/DB/migration/Client/public schema 변경 0건 |

## 7. Eligibility and Transient Context Tests

| ID | Scenario | Expected Result |
| --- | --- | --- |
| ABC343-T041 | natural-language node/edge target ref로 strict plan 생성 | schema 거부; runtime projection/bypass 구현은 호출하지 않음 |
| ABC343-T042 | 같은 logical plan 입력이지만 서로 다른 `full_safe_message`를 가진 valid `IntentPlanningContext` 두 개 구성 | 전체 safe message가 절단·공통 summary 치환 없이 각각 보존되고 topology, runtime·Knowledge fingerprint와 contract version 보존 |
| ABC343-T043 | context/scope `json.dumps`, `vars`, `dataclasses.asdict`, `pickle.dumps`, `model_dump`/`dict` 부재와 `repr` 검사 | 네 serialization API는 `TypeError`, dump method 없음, 고정 redacted repr에 identity/request 없음 |
| ABC343-T044 | `modify_workflow/replace_workflow` plan과 잘못된 request/draft pair | replace는 target 없이 허용, 그 외 잘못된 pair와 replace target은 거부 |

## 8. Closed Membership and Result Contract Tests

| ID | Scenario | Expected Result |
| --- | --- | --- |
| ABC343-T045 | Catalog v3 snapshot을 current workflow node Catalog와 비교 | node type/role, capability와 capability별 parameter key가 exact match |
| ABC343-T046 | 형식은 맞지만 snapshot/registry에 없는 capability, parameter key와 topic/guidance ref | 모두 strict schema 또는 codec에서 거부 |
| ABC343-T047 | 최소 plan canonical encode | 문서의 encoder option과 inline literal golden bytes가 exact match |
| ABC343-T048 | eligible/bypass `IntentNormalizationResult`의 valid/invalid field 조합 | valid union만 허용하고 unknown reason 또는 모순 조합 거부 |
| ABC343-T049 | success/failure `IntentRehydrationResult`의 valid/invalid field 조합 | `summary_projection_failed`를 포함한 closed failure reason과 valid union만 허용하고 unknown reason 또는 모순 조합 거부 |
| ABC343-T050 | invalid generation mode/provider/node role/edge branch와 dangling logical ref | transient context 생성 거부 |
| ABC343-T051 | scope target type/id 중 하나만 제공하거나 public dump/property 접근 시도 | constructor invariant 거부, public identity view 없음 |
| ABC343-T052 | strict `IntentCacheKey`와 hit/miss/bypass/error decision 조합 | malformed digest와 outcome별 plan/reason 모순 거부 |
| ABC343-T053 | boundary 인수 없이 직접 `AgentBuilderService` 구성 후 valid intent 실행 | disabled 기본값 사용, Planner 1회와 baseline structured result 유지 |
| ABC343-T054 | Catalog v3 snapshot의 parameter input type을 current Catalog와 비교 | capability별 parameter key/input type이 exact match하고 drift는 fail-closed |
| ABC343-T055 | known topic ref를 `knowledge_backed_llm`이 아닌 target에 사용 | strict schema 거부 |
| ABC343-T056 | known Slack guidance pair를 다른 capability/parameter/input type에 사용하거나 ref 한쪽만 사용 | strict schema 거부 |
| ABC343-T057 | `intent-text-v1` request-summary projection descriptor와 19개 capability purpose snapshot | generic request/draft summary table이 없고 `summary.current_safe_message.v1`의 source/whitespace/redaction/240-code-point/failure/provider-summary 비재사용/non-persistence 계약과 모든 capability purpose가 exact match하며 누락·초과 없음 |
| ABC343-T058 | load hit/miss/invalid/unavailable과 save stored/unavailable result 조합 | valid union만 허용하고 unknown status/reason 또는 plan/reason 모순 거부 |
| ABC343-T059 | codec의 모든 failure code/path category와 string/repr/cause 검사 | public `IntentPlanCodecError`만 발생하고 payload/value/full path/chained parser error 없음 |
| ABC343-T060 | duplicate guard parse 뒤 tuple plan decode | original bytes의 strict Pydantic JSON-mode 검증으로 성공하고 Python-mode list coercion helper 없음 |

## 9. Suggested Commands

- `pytest apps/gateway/tests/application/agent_builder/test_intent_cache_contracts.py`
- `pytest apps/gateway/tests/application/agent_builder/test_intent_cache_codec.py`
- `pytest apps/gateway/tests/application/agent_builder/test_intent_cache_disabled.py`
- `pytest apps/gateway/tests/composition/test_agent_builder_composition.py`
- `pytest apps/gateway/tests/architecture/test_agent_builder_import_boundaries.py apps/gateway/tests/architecture/test_agent_builder_module_boundaries.py`
- 변경된 기존 intent 경계가 있지 않더라도 regression 확인용으로
  `pytest apps/gateway/tests/services/test_agent_builder_intent_service.py`를 한 번 실행한다.

전체 repository 회귀, PostgreSQL integration, Docker와 live provider test는 이 범위에서 필요하지 않다.
