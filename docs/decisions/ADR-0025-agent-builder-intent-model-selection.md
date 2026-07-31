# ADR-0025: Agent Builder Intent Model Selection

Status: Superseded
Related ADRs: [ADR-0009](ADR-0009-active-organization-header-context.md), [ADR-0019](ADR-0019-agent-builder-preview-apply-save-boundary.md)

## Context

Agent Builder는 사용자 자연어를 안전한 의미 후보로 구조화하기 위해 내부 LLM intent planner를 사용한다. 기존 구현은 provider별 고정 저비용 모델 이름을 순회해 사용할 credential/model 조합을 내부에서 자동 선택했다. 이 방식은 실제 사용 모델을 사용자가 확인하거나 변경할 수 없고, 조직에 허용된 최신 모델 목록과도 쉽게 어긋난다. 생성 workflow의 LLM node도 별도 고정 환경변수 model route를 사용해, 사용자의 active organization 권한 후보와 무관한 model id가 저장되거나 환경변수가 없다는 이유만으로 draft가 실패할 수 있었다.

ADR-0019는 workflow node credential의 자동 선택, graph 주입, 원문 사용을 금지한다. 그러나 내부 intent planner의 permission-aware LLM 호출은 workflow node 실행이 아니며, 자연어 구조화에 필요한 별도 운영 경계다. 두 credential 사용 목적을 같은 규칙으로 처리하면 planner 모델을 숨겨 선택하거나 planner 자체를 사용할 수 없는 모순이 생긴다.

## Decision

Agent Builder 내부 intent planner는 active organization에서 사용할 수 있는 credential/model 조합을 명시적으로 선택한다.

- 선택 후보는 active organization의 valid credential, active chat model, verified credential-model relation, 사용자 `use` 권한을 모두 통과한 조합만 포함한다.
- Agent Builder header는 provider를 `openai`, `anthropic`, `google`, `llamaparse` 순서로 표시한다.
- MBA-240의 한정된 기본값 변경으로, 권한 확인 후보에 provider가 OpenAI이고 API model ID가 정확히 `gpt-5.5`인 조합이 있으면 해당 provider의 첫 option으로 표시한다. 해당 후보가 없으면 아래의 기존 세대/tier 정렬을 적용한다. Header와 생성 LLM node의 전체 fallback 정책 통합은 MBA-256에서 수행한다.
- 각 provider 안에서는 모델 id의 provider별 세대 표기를 기준으로 최신 세대를 먼저 표시하고, 같은 세대에서는 성능 tier가 높은 모델을 먼저 표시한다. 이후 relation priority와 안전한 표시 이름으로 순서를 안정화한다.
- Provider별 세대/tier parser는 작은 allowlist 규칙으로 관리한다. 알 수 없는 이름은 임의 성능을 추론하지 않고 안정적인 후순위로 둔다.
- `llamaparse`는 chat model provider가 아니므로 group은 표시하되 선택 불가 상태와 `chat_model_not_supported` reason을 반환한다.
- Client는 첫 번째 사용 가능 조합을 화면의 기본 선택으로 표시하며 사용자가 다른 조합으로 변경할 수 있다. 서버가 숨은 고정 모델 map으로 Agent Builder 모델을 대신 선택하지 않는다.
- Message request는 선택한 `credential_id`와 DB model row의 `model_id`만 전달한다. 선택 상태는 Agent Builder session, draft metadata, workflow graph 또는 별도 model-selection DB column에 저장하지 않는다. Permission/runtime 차단 audit은 기존 정책에 따라 safe resource ID, model ID, reason, runtime surface를 기록할 수 있지만 선택 상태나 credential 원문을 복구할 수 있는 payload를 저장하지 않는다.
- Server는 모든 message request에서 selected credential의 organization scope, validity, `use` 권한, selected model의 active chat type과 provider 일치, verified relation을 다시 확인한 뒤 client를 생성한다.
- 선택이 없거나 재검증에 실패하면 partial draft 또는 deterministic parser fallback을 만들지 않고 configuration-required 또는 safe failure로 닫는다.
- API response, prompt, trace, audit, session에는 credential 원문, API key, token, encrypted config, raw provider response를 포함하지 않는다.

생성 workflow LLM node의 기본 model 추천은 intent planner 선택과 별도 정책으로 처리한다.

- 추천 후보는 intent planner와 동일하게 active organization의 valid credential, active chat model, verified credential-model relation, 사용자 `use` 권한을 모두 통과한 조합으로 제한한다.
- MBA-240의 한정된 기본값 변경으로, 권한 확인 후보에 provider가 OpenAI이고 API model ID가 정확히 `gpt-5.5`인 조합이 있으면 generated LLM node에 먼저 추천한다. 해당 후보가 없으면 아래의 기존 provider/세대/tier 추천 순서를 적용한다.
- Provider는 `openai`, `anthropic`, `google` 순서로 평가한다. 각 provider에서는 최신 세대를 우선하고, 같은 세대에 `mini`가 있으면 먼저 추천한다. `mini`가 없으면 `nano` 또는 provider별 낮은 성능 tier부터 평가한 뒤 높은 tier로 진행한다.
- 추천이 있으면 generated `llmNode.model_id`에 safe model id만 저장한다. 추천에 사용된 credential id, credential 원문, provider config는 workflow graph, session, draft metadata, audit에 저장하지 않는다.
- 추천 후보가 없으면 draft 생성을 실패시키지 않고 `model_id`를 비운 `configuration_state=unresolved` node와 `model_id` 설정 필요 warning을 만든다. 실제 실행 전 사용자가 model을 선택해야 한다.
- 저장 후 workflow 실행은 현재 권한과 verified credential-model relation을 기존 runtime 경계에서 다시 확인한다. Draft 추천은 실행 권한을 영구 부여하지 않는다.
- 고정 `AGENT_BUILDER_DRAFT_MODEL_ID` 환경변수는 생성 node 추천 또는 fallback으로 사용하지 않는다.

ADR-0019의 workflow node credential 자동 선택 금지는 유지한다. Intent planner는 명시적으로 선택한 credential/model 조합을 요청마다 재검증하고, generated workflow node에는 별도의 권한 기반 추천 model id만 넣을 수 있다. Generated node의 credential 또는 target은 자동 설정하지 않는다.

## Consequences

- 사용자는 Agent Builder가 자연어 구조화에 사용할 모델을 확인하고 변경할 수 있다.
- 권한 또는 credential-model relation이 요청 사이에 변경되면 다음 요청에서 즉시 차단된다.
- 모델 catalog가 갱신되어도 provider별 작은 세대/tier parser 외에 전체 모델 목록을 하드코딩할 필요가 없다.
- 모델 선택은 화면 상태이므로 새로고침 후 서버에서 후보를 다시 조회하고 기본 선택을 다시 확정한다.
- 생성 workflow의 LLM node는 고정 환경변수가 아니라 현재 사용자 권한 후보에서 model을 추천받으며, 후보가 없으면 편집 가능한 unresolved 상태로 남는다.
- Workflow 실행, draft preview, apply/save의 side-effect 경계는 변경되지 않는다.

## Non-Goals

- Workflow node credential을 자동으로 선택하거나 저장하지 않는다. Generated LLM node에 위 결정의 권한 기반 model id를 추천하는 것은 허용한다.
- Credential 원문을 browser 또는 Agent Builder metadata에 제공하지 않는다.
- LlamaParse를 chat completion provider로 취급하지 않는다.
- 모델 성능을 benchmark 결과로 동적으로 평가하거나 provider 간 단일 성능 순위를 만들지 않는다.
