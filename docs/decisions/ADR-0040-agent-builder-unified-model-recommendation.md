# ADR-0040: Agent Builder Unified Model Recommendation

Status: Accepted
Related ADRs: [ADR-0019](ADR-0019-agent-builder-preview-apply-save-boundary.md), [ADR-0022](ADR-0022-incremental-hexagonal-architecture-adoption.md), [ADR-0025](ADR-0025-agent-builder-intent-model-selection.md)

## Context

ADR-0025와 MBA-240은 Agent Builder Header의 내부 intent planner와 generated LLM node에 OpenAI `gpt-5.5` 정확 일치 후보를 우선하도록 정했다. 그러나 `gpt-5.5`가 없을 때 Header는 높은 성능 tier를 먼저 두고 generated LLM node는 mini와 낮은 tier를 먼저 두므로 두 경로의 기본 모델이 달라질 수 있다.

MBA-256은 두 경로가 같은 권한 후보와 같은 추천 순서를 사용하도록 통합한다. 이 과정에서 provider 동기화가 명확히 분류하지 못한 모델을 기본 `chat` type으로 저장할 수 있다는 현재 제약을 고려하되, 정상 chat 모델을 이름 해석 실패만으로 Header에서 제거해 기존 수동 선택 기능을 축소하지 않아야 한다.

## Decision

Agent Builder Header와 generated LLM node는 Gateway의 하나의 결정적 추천 정책을 공유한다.

- DB 후보는 active organization에 속하고 `LLMCredential.is_valid=true`, `LLMModel.is_active=true`, `LLMModel.type=chat`, model과 credential의 provider가 일치하고 `LLMRelCredentialModel.is_verified=true`, 사용자 credential `use` 권한을 모두 통과해야 한다. 같은 model/credential의 verified relation이 중복이면 가장 낮은 relation priority 하나만 추천 후보로 사용한다.
- Provider는 `openai`, `anthropic`, `google` 순서로 평가한다. 각 provider 안에서는 해석 가능한 최신 세대, 같은 세대의 `general`, `mini`, `nano`, `pro`, 같은 세대와 tier의 기본형, 날짜 또는 명시 release snapshot, `preview`, `latest` 순서로 평가한다.
- 그 뒤에는 verified relation priority, 안전한 model/credential 표시 이름과 안정적인 식별자를 사용해 순서를 결정한다. 서로 다른 provider의 세대 숫자는 직접 비교하지 않는다.
- Provider별 세대와 tier parser는 작은 명시적 정책표로 관리한다. 현재 지원하는 `gpt-4o`, `gpt-4o-mini`, `o` 계열, 제품명 우선 및 세대명 우선 Claude 형식, Gemini 형식을 포함하고 원래 model ID는 변경하지 않는다.
- Sora, audio, transcribe, TTS, speech, Whisper, realtime, live, image, embedding, moderation, search, Codex, computer-use, robotics처럼 정규화한 API model ID로 특수 목적이 확정된 모델은 Agent Builder Header option과 generated LLM node 추천에서 제외한다. 특수 목적 판정은 앞뒤 공백 제거, 소문자화, 선행 `models/` 제거 후 `-`, `_`, `.`, `/`, `:` 구분자로 나눈 완전한 token 또는 `computer-use` 같은 연속 token과 provider별로 등록한 전체 model ID 일치 규칙에만 적용한다. 단순 부분 문자열 일치는 사용하지 않으며 전역 model catalog row는 변경하지 않는다.
- 정규화한 API model ID로 특수 목적이라고 확정할 수 없지만 세대나 tier를 해석하지 못한 verified chat 모델은 제거하지 않는다. 임의의 세대나 성능을 부여하지 않고 해당 provider의 해석 가능한 모델 뒤에 안정적인 순서로 둔다.
- Header는 provider group을 `openai`, `anthropic`, `google`, `llamaparse` 순서로 표시하고 전체 option의 첫 사용 가능 조합을 초기값으로 사용한다. 사용자는 다른 option을 선택할 수 있다.
- Header의 사용자 선택은 현재 message request에서만 사용하고 session, draft metadata, workflow graph 또는 별도 model-selection column에 저장하지 않으며 generated LLM node model로 복사하지 않는다.
- Generated LLM node는 같은 후보 집합과 추천 정책의 첫 model ID를 사용한다. 기존 node와 사용자가 저장 후 변경한 model은 덮어쓰지 않고 새로 생성한 node에만 추천한다.
- 추천 후보가 없으면 Header는 configuration-required 상태로 message 전송을 막는다. Generated LLM node는 `model_id`가 비어 있는 `configuration_state=unresolved` node와 설정 필요 warning을 만들고 draft 생성은 계속한다.
- Preview Mode에서는 generated node model을 읽기 전용으로 표시한다. 사용자는 `적용 및 저장` 성공 후 일반 Workflow Editor에서 model을 변경할 수 있다.
- Server는 message와 workflow 실행 요청에서 credential/model 관계와 권한을 다시 검증한다. 추천은 실행 권한을 영구 부여하지 않는다.
- API response, graph, prompt, trace, audit, session에는 credential 원문, API key, token, encrypted config 또는 raw provider response를 포함하지 않는다. Generated graph에는 safe model ID만 저장한다.

추천 정책은 Gateway application 경계의 순수 모듈로 둔다. SQLAlchemy session, ORM model, FastAPI, API response schema, credential service, provider SDK를 import하지 않으며 `LLMService`가 DB 후보를 안전한 정책 입력으로 변환하고 기존 호출부와 연결한다.

## Consequences

- 같은 사용자, organization과 후보 집합에서 Header 초기값과 generated LLM node 기본 model이 일치한다.
- MBA-240의 `gpt-5.5` 사례는 같은 세대에서 `general`을 먼저 두는 일반 규칙으로 유지되며, 더 최신 세대가 있으면 최신 세대를 먼저 추천한다.
- 모델 이름을 해석하지 못해도 verified chat 후보의 수동 선택 기능은 유지된다.
- 특수 목적 모델의 잘못된 `chat` 분류가 Agent Builder 기본값으로 노출되는 위험은 명시적 제외 정책으로 줄인다.
- API와 DB schema, 기존 workflow와 기존 Agent의 저장 model은 변경하지 않는다.

## Non-Goals

- 기존 workflow 또는 Agent의 `model_id`를 일괄 변경하지 않는다.
- Workflow node credential을 자동 선택하거나 graph에 저장하지 않는다.
- 권한이 없는 모델을 fallback으로 사용하지 않는다.
- 모델 성능, 비용 또는 품질을 외부 benchmark로 동적 평가하지 않는다.
- 새 provider, model 또는 credential 등록 체계를 만들지 않는다.
