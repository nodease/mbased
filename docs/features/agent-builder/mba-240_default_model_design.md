# MBA-240 Agent Builder GPT-5.5 기본 모델 설계

Status: Superseded

이 문서는 MBA-240 당시 OpenAI `gpt-5.5` 정확 일치 기본값을 도입한 배경과 구현 범위를 보존하는 역사 기록이다. 현재 구현은 [MBA-256 통합 모델 추천 정책 설계](mba-256_unified_model_recommendation_design.md)와 [ADR-0040](../../decisions/ADR-0040-agent-builder-unified-model-recommendation.md)의 공통 추천 정책으로 이 문서의 정확 일치 예외와 분리된 fallback을 대체한다.

## 목적

새 Agent Builder에서 사용할 기본 모델을 provider가 OpenAI이고 정확한 API 모델 ID가 `gpt-5.5`인 후보로 변경한다. 사용자의 권한과 credential-model 관계를 우회하지 않으며, 기존 Agent의 저장 모델과 사용자의 모델 변경 기능을 보존한다.

이 문서는 MBA-240 당시의 구현 경계를 정의한다. Agent Builder Header와 생성 LLM node의 전체 추천 정책을 하나로 통합하는 일은 후속 MBA-256에서 다루기로 했다.

MBA-240은 제품 기본값을 빠르게 바꾸기 위한 임시 정확 일치 예외였다. 당시에는 ADR-0025의 일반적인 세대/tier 선택 정책을 변경하지 않았고, 이후 MBA-256에서 일반 정책 갱신과 두 경로의 통합을 수행하도록 범위를 분리했다.

## 문제 정의

Agent Builder에는 목적과 저장 수명이 다른 두 모델 선택 경로가 있다.

| 경로 | 목적 | 저장 여부 | 현재 기본 결정 방식 |
| --- | --- | --- | --- |
| Header intent model | 사용자 자연어를 구조화하는 내부 planner 실행 | 저장하지 않음 | 권한 있는 option의 첫 항목 |
| Generated LLM node model | 생성 workflow의 LLM node 실행 설정 | workflow graph에 safe model ID 저장 | 별도 draft model 추천 |

현재 Header option 정렬은 최신 세대 안에서 높은 성능 tier를 먼저 두므로 `GPT-5.5 Pro`가 기본이 될 수 있다. 생성 node 추천은 최신 세대 안에서 mini와 낮은 tier를 우선하므로 `GPT-5.5 Mini`가 들어갈 수 있다. 한 경로만 바꾸면 MBA-240의 "초기 기본"과 "저장" 완료 조건을 동시에 만족할 수 없다.

## 요구 동작

### 신규 Agent

- active organization에서 사용자가 실행 권한을 가진 OpenAI의 정확한 `gpt-5.5` 후보가 있으면 Header option의 첫 항목으로 반환한다.
- Client는 첫 항목을 초기 intent model로 표시한다.
- 새로 생성되는 LLM node에도 같은 권한 후보 집합 안의 정확한 `gpt-5.5`를 추천한다.
- 생성 node에는 safe model API ID만 저장하며 credential ID, credential 원문, provider config를 저장하지 않는다.

### 기존 Agent

- 기존 workflow graph에 있는 LLM node의 `model_id`는 Agent Builder 진입, intent 구조화, draft 수정, apply/save 과정에서 자동 변경하지 않는다.
- 수정 요청으로 새 LLM node가 추가될 때만 현재 기본 추천을 적용한다.
- 기존 node의 모델 변경은 사용자가 명시적으로 모델을 변경하는 기존 workflow 편집 동작을 따른다.

### 사용자 선택

- 사용자는 Header에서 다른 권한 있는 intent model을 선택할 수 있다.
- 선택한 credential/model ID는 현재 message request에만 포함하고, 서버는 매 요청에서 권한과 관계를 다시 검증한다.
- Header 선택값을 Agent Builder session, draft metadata, workflow graph 또는 별도 DB column에 저장하지 않는다.
- 사용자는 workflow 편집 화면에서 생성 LLM node의 모델을 다른 사용 가능한 모델로 변경하고 저장할 수 있다.

### `GPT-5.5`를 사용할 수 없는 경우

- 권한 확인 후보에 정확한 `gpt-5.5`가 없으면 Header는 기존 provider, 세대, 성능 tier 정렬 결과의 첫 항목을 사용한다.
- 생성 node 추천은 기존 provider, 세대, mini, 낮은 tier 정렬 결과를 사용한다.
- Header message에 명시적 선택이 없으면 서버가 숨은 고정 모델을 선택하지 않는다.
- 생성 node 추천 후보 자체가 없으면 `model_id`를 비우고 기존 unresolved configuration issue를 반환한다. Draft 생성은 고정 환경변수 부재만으로 실패하지 않는다.

## 모델 식별 규칙

- 선호 대상은 provider가 `openai`이고 정규화한 API model ID가 정확히 `gpt-5.5`인 후보이다.
- `gpt-5.5-pro`, `gpt-5.5-mini`, version suffix가 붙은 다른 ID는 정확 일치 후보가 아니다.
- 표시 이름이 `GPT-5.5`인지만 보고 선택하지 않는다. 정책 비교에는 provider가 실제 호출에 사용하는 safe API model ID를 사용한다.
- 정확 일치 우선순위는 기존 permission-aware query가 반환한 후보에만 적용한다.
- 정확 일치 비교는 앞뒤 공백 제거와 소문자 변환만 수행한다. provider prefix, 날짜 또는 version suffix를 제거해 다른 모델을 `gpt-5.5`로 바꾸지 않는다.

## 책임 배치

### Client

Client는 API option을 표시하고 사용자의 선택 상태를 관리한다. 모델 catalog, credential 권한, 정확한 기본 모델 우선순위를 다시 계산하지 않는다. 이 경계는 화면과 서버의 기본값이 달라지는 중복 정책을 막는다.

### Agent Builder API

Endpoint는 인증 및 active organization context를 연결하고 request/response schema를 유지한다. 모델 정렬이나 추천 판단을 endpoint에 추가하지 않는다.

### LLMService

`LLMService`는 permission-aware model 후보 조회와 Agent Builder 전용 모델 정렬을 이미 소유하고 있었다. MBA-240의 정확 일치 선호 기준도 당시 이 책임 안에 두었다.

Header option 정렬과 draft recommendation 정렬은 OpenAI `gpt-5.5` 정확 일치 여부를 판단하는 작은 공통 helper만 공유했다. 각 경로의 fallback 순서는 기존 함수를 유지했다. 이 방식은 작은 정책 값의 중복을 제거하면서도 당시 목적이 다른 정렬 정책을 결합하지 않는 선택이었다.

### AgentBuilderService

`AgentBuilderService`는 새 LLM node를 조립할 때 `LLMService`의 추천 결과를 graph data에 반영한다. 기존 graph node는 보존하고 새 node에만 추천을 적용한다. 모델 catalog 정렬 규칙은 소유하지 않는다.

## API 계약 영향

### `GET /api/v1/agent-builder/model-options`

Request와 response schema는 변경하지 않는다. 반환 후보의 의미와 permission filter도 유지한다. 변경되는 것은 권한 있는 정확한 `gpt-5.5`가 있을 때 그 option을 첫 번째로 배치하는 정렬 의미뿐이다.

### Agent Builder message endpoint

Request의 `intent_model_selection`과 response schema를 변경하지 않는다. 사용자가 선택한 credential/model ID의 재검증, 누락 선택 fail-closed, raw credential 비노출 계약을 유지한다.

### Draft 및 apply/save

Draft graph schema를 변경하지 않는다. 새 LLM node의 기존 `model_id` 필드에 `gpt-5.5`가 들어가며, apply/save는 draft graph를 기존 workflow 저장 경계로 전달한다.

## 데이터 영향

- DB schema 변경 없음
- migration 없음
- 신규 column 없음
- 기존 Agent backfill 없음
- 기존 workflow graph 일괄 변경 없음
- Agent Builder session schema 변경 없음

## 권한과 보안

- 모델은 active organization에 속한 valid credential과 active chat model의 verified relation을 기준으로 노출한다.
- 사용자의 credential `use` 권한을 통과한 후보만 기본값 또는 대체 후보가 될 수 있다.
- Client가 전송한 credential/model ID는 신뢰하지 않고 서버에서 다시 검증한다.
- credential 원문, API key, token, encrypted config, provider request/response raw payload를 API 응답, draft, workflow graph, audit, trace에 추가하지 않는다.
- 기본값이 없다는 이유로 권한 밖 credential이나 모델을 자동 선택하지 않는다.

## 실패 처리

| 조건 | 동작 |
| --- | --- |
| 정확한 `gpt-5.5` 사용 가능 | 두 경로에서 `gpt-5.5` 우선 |
| `gpt-5.5` 없음 또는 권한 없음 | 각 경로의 기존 fallback 유지 |
| Header option 없음 | Client submit 차단 및 기존 configuration 안내 |
| Header 선택 누락 또는 서버 검증 실패 | hidden fallback 없이 기존 safe error 반환 |
| 생성 node 추천 후보 없음 | unresolved node와 model 설정 필요 issue 반환 |

## 테스트 요구사항

### 정렬 및 정확 일치

- Header 후보에 Pro, 기본, Mini가 함께 있으면 정확한 `gpt-5.5`가 첫 번째다.
- 생성 node 후보에 기본, Mini, Nano가 함께 있으면 정확한 `gpt-5.5`가 추천된다.
- Pro와 Mini는 기본 `gpt-5.5`의 정확 일치로 취급하지 않는다.
- 기본 모델이 없을 때 각 경로의 기존 서로 다른 fallback 순서가 유지된다.

### 권한

- 다른 organization의 `gpt-5.5`는 후보가 아니다.
- credential `use` 권한이 없는 `gpt-5.5`는 후보가 아니다.
- inactive model, invalid credential, unverified relation의 `gpt-5.5`는 후보가 아니다.

### 신규 및 기존 graph

- 신규 Agent의 새 LLM node는 `gpt-5.5`를 저장한다.
- 기존 Agent의 다른 model ID는 수정 요청 후에도 유지된다.
- 기존 Agent에 새 LLM node를 추가하면 새 node에만 `gpt-5.5`를 적용한다.
- 사용자가 다른 LLM node 모델을 명시적으로 선택한 뒤 저장할 수 있다.
- 위 세 저장 동작은 `적용 및 저장` 또는 기존 workflow 저장 API를 완료한 뒤 workflow graph를 다시 조회해 검증한다.

### Client

- API 첫 option인 `GPT-5.5`를 Header 기본값으로 표시한다.
- 다른 option 선택과 message request 전달이 유지된다.
- 현재 선택한 option이 재조회 결과에도 있으면 선택을 보존한다.
- option이 없으면 hidden fallback 없이 submit을 차단한다.

## 권위 문서 반영

MBA-240 당시 Accepted ADR인 `ADR-0025`에는 OpenAI `gpt-5.5` 정확 일치 우선 조건을 한정적으로 반영했다. 기존의 "최신 세대, 높은 tier" 및 "mini 우선" 결정은 정확 일치 후보가 없을 때의 fallback으로 유지했으며, 당시 Agent Builder feature 문서에는 MBA-240의 한정된 변경과 MBA-256 후속 정리 범위를 명시했다.

- `requirements.md`: Header 기본과 신규 node 기본, 기존 node 보존
- `api_spec.md`: option 정렬 의미와 draft recommendation 의미
- `component_spec.md`: Header의 초기 선택 표시
- `test_cases.md`: 정확 일치, fallback, 권한 제외, 기존 graph 보존

이 역사 문서의 `Verified Against`는 MBA-240 코드와 테스트를 실제 검증한 근거 없이 갱신하지 않는다. 통합 정책의 목표와 구현 검증 기준은 MBA-256 설계와 ADR-0040에서 관리한다.

## 범위 제외

- Header와 생성 node의 fallback 정책 전체 통일
- provider 및 tier 순위 재설계
- 모델별 비용 또는 품질 점수 도입
- 모델 catalog 자동 provisioning
- credential 자동 선택 또는 권한 우회
- Header 선택 상태 영속화
- 기존 Agent model backfill
- Agent Builder 외 workflow 생성 경로의 기본 모델 변경

## 후속 작업과의 경계

MBA-256은 Header와 생성 LLM node의 일반적인 모델 추천 정책을 하나로 통일해 이 문서의 임시 fallback 분리를 대체했다. MBA-240은 `gpt-5.5`라는 정확한 기본값만 당시 두 경로에 일관되게 적용하고, `gpt-5.5`가 없을 때의 서로 다른 fallback을 유지한 작업으로 기록한다. 정책 객체 통합, provider/tier 순서 통일, 공통 테스트 행렬 확장은 MBA-256에서 수행했다.

## 운영 준비 조건

제품에서 `GPT-5.5`가 실제 추천값으로 보이려면 active organization의 OpenAI credential에 active chat model `gpt-5.5`와 verified relation이 있어야 한다. Agent Builder는 이 catalog 또는 relation을 자동 생성하지 않는다. 시연 및 배포 검증 환경은 해당 관계가 준비되었는지 별도로 확인해야 하며, 준비되지 않았을 때는 권한을 우회하지 않고 MBA-256 통합 추천 순서의 다음 자격 후보를 사용한다.
