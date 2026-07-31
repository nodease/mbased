# Agent Builder Cache Test Cases

Status: Draft

## Test Strategy

테스트는 cache hit 자체보다 잘못된 hit가 발생하지 않는지, hit가 기존 권한·validation·CAS 경계를
우회하지 않는지를 우선 검증한다. Unit test는 fake cache/clock과 deterministic codec을 사용하고 Redis 및
PostgreSQL integration은 별도 환경에서 순차 실행한다.

## Normalization

| ID | Case | Expected |
|---|---|---|
| ABC-T001 | `LLM 노드 생성`, 승인된 정중 표현과 case 변형 | 같은 signature |
| ABC-T002 | `LLM 뒤에 Slack`, `Slack 뒤에 LLM` | 다른 signature |
| ABC-T003 | 생성과 생성하지 않음 | 다른 signature |
| ABC-T004 | 숫자, 인용문, node label이 다름 | 다른 signature 또는 bypass |
| ABC-T005 | 승인되지 않은 의미 유사 표현 | bypass, LLM 결과 override 없음 |
| ABC-T006 | Unicode 호환문자와 연속 공백 | canonical normalization |
| ABC-T007 | token boundary 밖 부분 문자열 alias | 치환하지 않음 |
| ABC-T008 | secret-like prefix 또는 redaction 발생 | lookup/store 모두 호출하지 않음 |
| ABC-T009 | explicit parameter value 가능성이 있는 요청 | lookup/store bypass |
| ABC-T010 | alias table canonical target 중복 | startup/static validation 실패 |
| ABC-T011 | 2,000자 이후만 다른 허용 길이 요청 | 같은 signature를 만들지 않고 전체 canonicalization 또는 bypass |
| ABC-T012 | 51번째 이후 node/topology만 다른 workflow | 다른 context fingerprint 또는 bypass |
| ABC-T013 | request 또는 graph projection 절단 감지 | lookup/store 모두 bypass |
| ABC-T014 | `AgentBuilderService` planning DTO | 전체 safe request/topology 사용, 2,000자·50-node 절단 없음, selected target의 ephemeral HMAC material 외 raw config/resource/request/operation ID 없음 |
| ABC-T015 | versioned normalization profile manifest | alias, 허용 조사·정중 표현, 위치 표현, 보호 span 우선순위와 version이 deterministic하게 로드됨 |
| ABC-T016 | profile이 허용한 정중 표현·조사·위치 표현만 다른 요청 | 같은 signature |
| ABC-T017 | profile 처리 뒤 미인식 잔여 표현 | 전체 요청 bypass, 부분 signature 생성 없음 |
| ABC-T018 | 인용문·숫자·부정·node label·parameter-like span과 alias가 겹침 | 보호 span이 먼저 적용되고 값이 바뀌지 않음 |
| ABC-T019 | versioned adversarial corpus | 동등·비동등·충돌 사례가 process 간 같은 결과를 내고 profile 변경 시 normalizer version 변경 |

## Key and Value

| ID | Case | Expected |
|---|---|---|
| ABC-T020 | 같은 scope/context/version | 같은 HMAC key |
| ABC-T021 | user 또는 organization 변경 | 다른 key |
| ABC-T022 | planner model, generation mode 변경 | 다른 key |
| ABC-T023 | graph topology 또는 selected edge identity 변경 | 실제 UUID는 노출되지 않지만 다른 HMAC key |
| ABC-T024 | layout/viewport만 변경 | 같은 context fingerprint |
| ABC-T025 | Knowledge 후보의 권한, lifecycle, safe metadata 또는 policy revision 변경 | 다른 key |
| ABC-T026 | normalizer/catalog/materializer version 변경 | miss |
| ABC-T027 | Key/cache/log/metric capture 검사 | raw request, candidate ID/handle/name과 resource UUID가 없음 |
| ABC-T028 | cache value round-trip | strict schema와 canonical bytes 유지 |
| ABC-T029 | graph/UUID/credential/parameter/raw prompt field 삽입 | codec 거부 |
| ABC-T030 | oversized, unknown version 또는 corrupt payload | safe miss, best-effort delete |
| ABC-T031 | 한 key의 valid envelope를 다른 key로 이동 | payload MAC 실패, safe miss |
| ABC-T032 | canonical payload 또는 MAC 한 byte 변조 | plan 미사용, safe miss |
| ABC-T033 | manifest member인 `topic_ref`/`parameter_guidance_refs` round-trip 후 unknown ref, 자유 형식 topic/summary/guidance 또는 template argument 삽입 | member ref는 canonical bytes 유지, unknown/자유 형식 field는 strict codec 거부 |
| ABC-T034 | request/session/draft ID 또는 raw audit payload/metadata 삽입 | encode/decode codec 거부 |
| ABC-T035 | nested object에 forbidden key 삽입 | strict codec 거부 |
| ABC-T036 | 구조와 역할이 같은 서로 다른 selected edge/node | 실제 target identity를 HMAC input에만 사용해 다른 key, payload와 diagnostic에는 UUID 없음 |
| ABC-T037 | application cache module import 검사 | Redis client 타입을 직접 import하지 않고 cache port만 의존 |
| ABC-T038 | 같은 Knowledge 후보 집합을 다른 traversal/UI 순서로 제공 | 중복 제거와 안정 정렬 뒤 같은 fingerprint |
| ABC-T039 | safe label이 같지만 실제 identity가 다른 Knowledge 후보 | 다른 fingerprint이며 DTO/cache/log/metric에 ID, handle 또는 이름 원문 없음 |

## Admission and Planner Calls

| ID | Case | Expected |
|---|---|---|
| ABC-T040 | 모든 provider topic/guidance가 exact canonical ref로 표현되는 valid actionable new workflow | store eligible |
| ABC-T041 | valid replace workflow without explicit value | store eligible |
| ABC-T042 | selected edge 기반 modify | matching context에서 eligible |
| ABC-T043 | natural-language target modify | bypass |
| ABC-T044 | clarification/unsupported | not stored |
| ABC-T045 | schema/provider failure | not stored |
| ABC-T046 | semantic repair 최종 성공 | final plan만 한 번 저장 |
| ABC-T047 | repair 최종 실패 | not stored |
| ABC-T048 | warm valid hit | provider invoke 0회, usage reserve 0회 |
| ABC-T049 | miss without repair | provider/usage attempt 1회 |
| ABC-T050 | miss with repair | provider/usage attempt 2회 |
| ABC-T051 | cache put failure after valid plan | 현재 request 정상 진행 |
| ABC-T052 | 인증/조직/권한 또는 foreground admission 실패 | cache lookup과 provider 호출 모두 0회 |
| ABC-T053 | lookup 전 request canceled/version stale | hit plan 미사용, 기존 terminal contract |
| ABC-T054 | warm hit가 반복 rate limit을 초과 | 기존 request admission 정책 적용 |
| ABC-T055 | provider topic/guidance가 모두 exact ref로 표현되는 cold miss와 하나라도 표현되지 않는 cold miss | 전자는 ref projection 뒤 canonical `query_topics`/guidance를 즉시 downstream에 사용하고 put, 후자는 원본 extraction을 non-cache downstream에 사용하고 put 없음 |
| ABC-T056 | warm hit canonical rehydration failure | hit 폐기, 기존 Planner 최대 1회, 성공 시 canonical result 사용 |
| ABC-T057 | cold miss canonical rehydration failure | 원본 extraction 미사용, Planner 재호출·cache put·GraphMutation·save 0회, 기존 terminal error |
| ABC-T058 | cold miss rehydration failure 전 provider/repair attempt | 이미 발생한 usage는 ADR-0055대로 기록, 실패 때문에 추가 attempt 없음 |

## Revalidation and Rehydration

| ID | Case | Expected |
|---|---|---|
| ABC-T060 | hit 뒤 credential revoked | 기존 runtime/permission error, graph 생성 없음 |
| ABC-T061 | hit 뒤 model relation invalid | 다른 model로 fallback하지 않고 차단 |
| ABC-T062 | selected target 삭제/변경 | hit reject 후 Planner/clarification 경계 |
| ABC-T063 | 동일 plan을 두 요청에서 materialize | 새 node/edge UUID와 operation ID |
| ABC-T064 | registry manifest/version, safe-summary projection descriptor·redaction corpus·provider-summary 비재사용·non-persistence, purpose membership 또는 Catalog parameter input-type applicability validation 실패 | 보정하지 않고 저장하지 않음 |
| ABC-T065 | canonical topic/guidance ref와 같은 logical plan을 만드는 서로 다른 두 safe request의 cold miss와 warm hit | 각 request 안에서는 `summary.current_safe_message.v1` summary, step purpose, KB recommendation `query_topics`, response guidance, ParameterTask와 topology가 동일하고 current UUID만 다름; 두 request의 summary는 generic request/draft 문장으로 합쳐지지 않음 |
| ABC-T066 | cache hit GraphMutation | current base hash/updated_at과 expected result hash 사용 |
| ABC-T067 | stale CAS | 기존 stale_graph conflict, silent overwrite 없음 |
| ABC-T068 | acknowledgement response loss | 기존 canonical recovery 사용, cache replay 없음 |

## Knowledge

| ID | Case | Expected |
|---|---|---|
| ABC-T080 | Knowledge cache value inspection | 순서 있는 closed `topic_ref`만 있고 rendered topic, KB/Collection ID, 이름과 handle 없음 |
| ABC-T081 | hit 시 같은 권한 후보 | 새 opaque handle 발급 |
| ABC-T082 | hit 전 KB use 권한 제거 | 후보에서 제외, 과거 추천 미복원 |
| ABC-T083 | Collection lifecycle 변경 | 현재 hierarchy 결과 사용 |
| ABC-T084 | 후보가 모두 사라짐 | empty/clarification 경계, 강제 binding 없음 |
| ABC-T085 | before_graph Knowledge plan | 현재 template으로 selected/empty graph 구성 |
| ABC-T086 | after_graph Knowledge plan | 현재 target에 binding-only 적용 |
| ABC-T087 | Knowledge 선택 이후 | Planner 재호출 없음 |

## Redis Failure and Single-Flight

| ID | Case | Expected |
|---|---|---|
| ABC-T100 | Redis connection/timeout | Planner 정상 호출, safe error metric |
| ABC-T101 | cache disabled/HMAC key absent | Redis 접근 없이 Planner 호출 |
| ABC-T102 | owner가 follower wait 안에 완료되는 동시 동일 miss | lease owner만 provider 호출, follower는 value 재사용 |
| ABC-T103 | owner crash | lease TTL 뒤 follower 진행 가능 |
| ABC-T104 | owner가 follower wait를 초과 | 무한 대기 없이 follower도 Planner 호출 가능 |
| ABC-T105 | wrong owner lease release | lease 유지 |
| ABC-T106 | DB transaction probe | Redis/provider wait 중 workflow row lock 없음 |
| ABC-T107 | `NODE_ENV=production|staging`에서 운영 evidence 미완료로 ready attestation을 설정하지 않음 | cache disabled, Planner 정상 호출 |
| ABC-T108 | adapter command capture | broad scan/keys/flush 명령 없음 |
| ABC-T109 | default/max wait configuration | 기본 45초, 최대 60초, request 남은 deadline보다 길게 기다리지 않음 |
| ABC-T110 | follower wait 중 request cancel | 다음 slice에서 종료, value/provider/GraphMutation 사용 없음 |
| ABC-T111 | follower wait 중 request version 변경 | stale terminal contract, cache value와 Planner 사용 없음 |
| ABC-T112 | value 도착과 cancel/version 변경 경합 | value 사용 직전 fence가 취소/stale을 우선 |
| ABC-T113 | process-wide waiter 기본 상한에서 8개 대기 후 9번째 요청 | 9번째는 즉시 overflow Planner 경로, API 실패 없음 |
| ABC-T114 | timeout/cancel/version/Redis error/exception | follower slot이 항상 반환되고 다음 요청에서 누수 없음 |
| ABC-T115 | 서로 다른 cache key의 follower 동시 대기 | key별이 아니라 process-wide 상한 적용 |
| ABC-T116 | waiter capacity overflow | 중복 provider 호출 허용, `overflow`와 safe reason 기록, 정상 결과 반환 |
| ABC-T117 | `NODE_ENV=production|staging`에서 ready flag 또는 전용 URL 누락 | cache disabled, Planner 정상 호출 |
| ABC-T118 | cache URL 누락과 Celery Redis 설정 존재 | Celery Redis로 자동 fallback하지 않음 |
| ABC-T119 | 운영 evidence 확인 뒤 `NODE_ENV=production|staging`의 URL/HMAC/bounded config/ready attestation이 모두 유효 | configuration gate 통과, cache serving 활성화 가능 |
| ABC-T131 | owner가 clarification/unsupported/provider failure/store-ineligible 결과로 정상 종료 | lease 즉시 해제, follower가 `owner_completed_without_value`를 받고 wait 잔여 시간 없이 Planner 진행, negative cache 없음 |
| ABC-T132 | 이전 lease generation의 no-value signal 뒤 새 owner가 lease 획득 | stale signal은 새 follower를 깨우지 않고 현재 generation 결과만 사용 |

## Observability and Security

| ID | Case | Expected |
|---|---|---|
| ABC-T120 | hit/miss/bypass/error | allowlisted metric만 기록 |
| ABC-T121 | log/metric capture | raw prompt, key digest 전체, IDs와 secret 없음 |
| ABC-T122 | cache hit | `llm_usage_logs` 신규 row 없음 |
| ABC-T123 | cache miss/repair | ADR-0055 usage rows 유지 |
| ABC-T124 | workflow mutation after hit | 기존 permission/audit 기록 유지 |
| ABC-T125 | cache flush/namespace rotation | 기능 정상, cold miss만 발생 |
| ABC-T126 | HMAC key/Redis URL을 포함한 configuration repr와 startup error | secret/password 원문 없음 |
| ABC-T127 | HMAC key 누락 또는 길이 부족 | safe disabled reason, Redis 접근 없이 Planner 호출 |
| ABC-T128 | HMAC key version rotation | dual-read 없이 새 namespace cold miss |
| ABC-T129 | tracked env와 capture fixture 검사 | HMAC key, Redis password와 raw audit payload 없음 |
| ABC-T130 | cache audit capture와 Redis data loss | safe outcome/reason/latency만 기록, key/value/plan 없음, audit replay 없이 cold miss |

## Regression

| ID | Case | Expected |
|---|---|---|
| ABC-T140 | feature flag off | 현재 Agent Builder planner/service tests 동일 통과 |
| ABC-T141 | guided/quick/structure-only | mode별 key 분리, 기존 mode 동작 유지 |
| ABC-T142 | existing direct-edit new/modify/replace | GraphMutation/CAS/ack 회귀 없음 |
| ABC-T143 | pending/failed request recovery | cache가 session/request 상태를 변경하지 않음 |
| ABC-T144 | multiple worker processes | 동일 canonical key/value codec 결과 |

## Internal Latency Evaluation

Live latency evaluation은 deterministic unit test와 분리한 수동 benchmark다. 절대 시간은 CI gate가
아니지만 sample schema, 통계, 산출물, cache outcome과 provider call count는 unit test로 고정한다.

| ID | Case | Expected |
|---|---|---|
| ABC-T150 | GPT-5.5 benchmark preflight | active organization, credential `use`, verified model relation과 실제 API model `gpt-5.5` 확인; token 원문 입력 없음 |
| ABC-T151 | cache disabled baseline | 매 성공 회차 provider 호출 1회 또는 repair 시 최대 2회, cache outcome `disabled`; `disabled`는 이 cache-off baseline에만 허용 |
| ABC-T152 | cache enabled cold miss | lookup/store overhead 포함, provider 호출 1회 또는 repair 시 최대 2회, outcome `miss` |
| ABC-T153 | exact/approved normalization warm hit | provider와 usage reservation 0회, outcome `hit`, canonical materialization parity 통과 |
| ABC-T154 | semantic-only 유사 표현 | outcome `bypass`, provider 호출 유지, deterministic cache 개선 결과에 포함하지 않음 |
| ABC-T155 | negative control | 순서·부정·수량·target이 다른 요청은 hit하지 않음 |
| ABC-T156 | paired context reset | 같은 pair는 동일 초기 graph, actor, organization, model, generation mode와 safe candidate fingerprint 사용 |
| ABC-T157 | latency surfaces | planning과 end-to-end를 각각 기록하며 planning은 cache coordinator 경계, end-to-end는 기존 message API 요청부터 terminal response까지 측정 |
| ABC-T158 | round data safety | 각 회차를 `runs.csv`로 보존하되 raw prompt, token, credential/user/org/workflow ID와 provider payload 없음 |
| ABC-T159 | statistics and failures | 평균·P50·P95·표준편차·최소·최대·실패율·paired delta 계산, 실패는 성공 평균과 분리, 이상치 임의 삭제 없음 |
| ABC-T160 | artifact generation | 회차별 point graph, 평균, percentile, paired delta, provider call과 cache outcome vector graph 및 JSON/CSV/Markdown summary 생성 |
| ABC-T161 | repetition protocol | 개발 확인은 group별 10회, 최종 발표 측정은 group별 30회; warm-up 1~2회는 별도 기록하고 요약 통계에서 제외 |
| ABC-T162 | execution order | baseline과 warm 측정을 교차 순서로 실행해 provider 시간대 편향을 줄임 |
| ABC-T163 | report generator unit test | synthetic samples로 통계와 산출물 생성 결과를 deterministic하게 검증하고 wall-clock threshold는 사용하지 않음 |
| ABC-T164 | exploratory benchmark 실행 | ignored path에 기록되고 최종 README/PPT evidence에 자동 포함되지 않음 |
| ABC-T165 | 최종 benchmark bundle | `docs/features/agent-builder-cache/benchmarks/<benchmark-id>/`에 날짜·git SHA·dataset/cache version 식별, 약정된 README/CSV/JSON/SVG 구성, prompt 대신 case ID, README에 핵심 요약과 회차별 data·graph·summary 상대 경로 모두 기록 |
| ABC-T166 | artifact safety inspection | raw user prompt, credential/token, actor/org/workflow/request ID와 provider payload 없음 |
| ABC-T167 | artifact retention policy | 최종 safe bundle은 Git 고정 경로에 추적되고 exploratory 결과는 ignored 경로에만 존재 |
| ABC-T168 | 25/50/75% hit-rate estimate | 측정 warm/cold mean으로 planning/end-to-end 공식 결과가 정확히 계산됨 |
| ABC-T169 | modeled estimate presentation | 실제 측정값과 구분되고 `modeled_estimate`로 표시 |
| ABC-T170 | semantic bypass와 Redis fail-open sample | modeled hit-rate 계산에서 miss 경로로 분류하고 실제 실행은 기존 miss provider 호출 계약을 유지하며 모델링 계산 자체는 provider를 호출하지 않음 |

## Verification Commands

구현 시 저장소의 실제 환경을 확인한 뒤 다음 범주를 순서대로 실행한다.

- Python import와 Pydantic schema 검사
- Normalizer/cache policy/codec unit tests
- Agent Builder intent service와 usage attribution tests
- Knowledge selection, GraphMutation, workflow CAS service tests
- Redis adapter integration test
- PostgreSQL CAS integration test
- Agent Builder cache latency report generator unit test
- 승인된 환경에서 GPT-5.5 cache on/off live latency benchmark
- `git diff --check`

실제 Redis/PostgreSQL 환경이 없으면 integration test 코드는 작성하되 Test Matrix를 blocked로 유지한다.
Cache는 frontend contract를 변경하지 않으므로 신규 browser UI E2E는 요구하지 않는다. 기존 Agent Builder
smoke는 cache on/off에서 동일 결과를 확인하는 회귀 검증으로 수행할 수 있다.
Live benchmark 실행에 필요한 credential, 비용 승인 또는 서버 환경이 없으면 runner와 report generator
unit test는 구현하되 실제 측정 행은 blocked로 남기고 cache 기능의 최종 검증을 완료 처리하지 않는다. 최종 완료에는
comparison group별 30회 live 측정과 약정된 benchmark bundle이 필요하다. Graph RAG 후속 이슈는 의미 유사 요청의 retrieval,
ranking과 confidence 품질을 별도 dataset과 지표로 검증하며 semantic bypass 결과를 hit 성과로 재사용하지 않는다.
Production/staging serving은 실제 Redis evidence 없이는 활성화 검증 완료로 표시하지 않는다. Production Redis
운영과 Graph RAG 설계·구현은 deterministic cache 코드의 필수 검증 완료와 별도로 추적한다.
