# MBA-343 Agent Builder Cache Spine Component Specification

Status: Draft

## 1. Component Goal

MBA-343은 application 내부의 cache contract를 응집된 하위 package로 만들고 outer composition이
disabled boundary만 선택하도록 한다. Redis나 현재 Agent Builder service의 복잡성을 package 안으로
끌어오지 않는다.

## 2. Target Ownership

| Path | Responsibility | Must Not Own |
| --- | --- | --- |
| `apps/gateway/application/agent_builder/intent_cache/catalog_snapshot.py` | Catalog v3 node type/role/capability/parameter key·input type, `summary.current_safe_message.v1` descriptor와 `intent-text-v1` purpose immutable snapshot | runtime Catalog load, summary/structured request rendering, alias policy |
| `apps/gateway/application/agent_builder/intent_cache/contracts.py` | strict DTO, enum/ref, safe decision/error | Pydantic JSON codec 세부 구현, Redis, service orchestration |
| `apps/gateway/application/agent_builder/intent_cache/codec.py` | canonical encode/decode, duplicate-key와 forbidden-content guard | cache key/HMAC, Redis, metrics |
| `apps/gateway/application/agent_builder/intent_cache/ports.py` | normalizer/store/rehydrator/boundary Protocol와 immutable `IntentPlanExecution` | concrete adapter, business fallback orchestration |
| `apps/gateway/application/agent_builder/intent_cache/disabled.py` | Planner closure 1회 위임과 constant disabled decision | context inspection, provider/port/codec 직접 호출 |
| `apps/gateway/application/agent_builder/intent_cache/__init__.py` | deliberate public exports | wildcard export, outer-layer import |
| `apps/gateway/composition/agent_builder.py` | disabled concrete boundary factory | cache policy, config parsing, Redis construction |
| `apps/gateway/services/agent_builder_service.py` | 기존 Planner closure를 boundary로 한 번 실행하는 no-op seam | cache eligibility, context build, lookup/store와 enabled 정책 |

새 production file ownership은 위 경계에 한정한다. `apps/shared`로 contract를 올리지 않는다. 현재 이
contract를 Gateway 밖 runtime이 소비하지 않으므로 shared package는 결합도만 높인다.

## 3. Cohesion Decisions

### 3.1 Contracts and Codec Are Separate

DTO 변경 이유는 product contract 변화이고 codec 변경 이유는 representation/validation 변화다.
같은 file에 두면 schema import만 필요한 port가 JSON 구현까지 의존한다. 따라서 분리하되 같은 bounded
subpackage 안에서만 public surface를 관리한다.

### 3.2 Ports Stay Narrow

Normalizer, store와 rehydrator는 서로 다른 변화 축이다. Store port가 normalizer 결과나 current graph를
받지 않고, rehydrator가 Redis bytes를 받지 않도록 한다. Coordinator가 생기기 전까지 port 사이의 호출 순서는 없다.

### 3.3 Disabled Boundary Is an Application Object

Disabled는 외부 기술 adapter가 아니라 application 정책의 안전한 기본값이다. Cache/provider I/O를 직접
소유하지 않고 기존 Planner closure가 가진 I/O만 정확히 한 번 위임하며 항상 같은 cache decision을 반환한다.
따라서 application package가 소유한다. Environment에 따른 enabled/disabled 선택은 후속 composition/config
범위이며 MBA-343에서는 선택지가 없다.

### 3.4 Disabled Planner Seam Without Cache Activation

현재 `AgentBuilderService`는 safe workflow context를 만들고 `LLMAgentBuilderIntentExtractor`가 model,
credential과 Knowledge candidate context를 내부에서 확정한다. MBA-343은 이 사이에 lookup을 삽입하지 않고
기존 Planner 실행을 그대로 감싸는 disabled seam만 만든다. 따라서 다음을 지킨다.

- `AgentBuilderService` constructor는 optional boundary를 받고 생략 시 disabled implementation을 사용한다.
- `_structure_request`의 기존 extract/validate/normalize sequence는 인수 없는 closure로 유지한다.
- disabled boundary는 closure를 정확히 한 번 실행하고 동일 structured request를 반환한다.
- `LLMAgentBuilderIntentExtractor`를 cache-aware wrapper로 감싸지 않는다.
- production `IntentPlanningContext`를 구성하거나 읽지 않고 lookup/store/diagnostic을 수행하지 않는다.
- 후속 integration은 complete context builder와 enabled coordinator를 같은 seam에 함께 연결한다.

이 경계는 Linear acceptance에 필요한 실제 composition seam을 제공하면서 incomplete context로 cache를
부분 활성화하지 않는다.

## 4. Dependency Rules

- `catalog_snapshot.py`는 표준 라이브러리만 import하며 runtime Catalog file/service를 읽지 않는다. Static test만
  current Catalog의 node type/role/capability/parameter key·input type과 exact 비교한다.
- Summary snapshot은 request/draft 공통 문자열을 소유하지 않고 current `full_safe_message`를 입력으로 하는
  `summary.current_safe_message.v1` descriptor만 소유한다. 실제 request-specific projection은 후속
  rehydration이 구현한다.
- `contracts.py`는 표준 라이브러리, Pydantic과 `catalog_snapshot.py`만 import한다.
- `codec.py`는 표준 라이브러리, `contracts.py`만 import한다.
- `ports.py`는 `typing`, `contracts.py`와 기존 downstream 계약인
  `apps.shared.schemas.agent_builder.AgentBuilderStructuredRequest`만 import할 수 있다.
- `disabled.py`는 `contracts.py`, `ports.py`만 import한다.
- application package는 `apps.gateway.services`, `apps.gateway.adapters`, `apps.gateway.composition`,
  FastAPI, SQLAlchemy, Redis/provider library를 import하지 않는다.
- `AgentBuilderService`는 application package의 public boundary contract만 import할 수 있다.
- composition은 application public package를 import할 수 있다.
- 기존 endpoint는 새 package를 직접 import하지 않는다.

기존 `test_agent_builder_import_boundaries.py`가 이 방향을 자동 검증한다. Cache subpackage 재귀 file도
누락 없이 검사되어야 한다.

Catalog drift test만 test layer에서 `apps.shared.services.workflow_node_catalog`를 import해 v3 snapshot의
node type/role, capability와 capability별 parameter key를 비교할 수 있다. Production package는 이 비교를
수행하지 않는다.

## 5. Forbidden-content Guard

Guard는 schema validation의 대체가 아니라 defense in depth다.

- encode: typed model dump 후 허용 tree shape와 forbidden category를 검사한다.
- decode: duplicate-key detection이 있는 JSON parser로 읽고 raw object tree를 먼저 검사한 뒤 strict model을 만든다.
- post-decode: typed model을 다시 검사하고 canonical re-encode equality를 확인한다.
- error: category와 safe path class만 반환하며 value, 전체 path, payload excerpt와 underlying exception chain을
  보존하지 않는다.
- strict tuple: duplicate/non-finite/raw guard용 object parse 뒤 같은 원본 bytes를 Pydantic JSON mode로 검증한다.
  Python `list`를 strict tuple field에 전달하거나 schema-aware coercion helper를 추가하지 않는다.

정확히 허용된 `parameter_key`, `target_reference_type`, `canonical_text_registry_version`은 이름 일부가
금지 단어와 비슷하더라도 거부하지 않는다. Denylist substring 하나에 의존하지 않고 schema field allowlist와
identity-bearing exact category를 함께 사용한다.

## 6. Composition Behavior

`AgentBuilderComposition.intent_plan_cache()`는 매 호출에 stateless immutable disabled boundary를 반환하거나
module-level immutable instance를 반환할 수 있다. Identity는 계약이 아니며 동작만 검증한다.

`orchestration()`은 `intent_plan_cache()`를 호출하고 그 결과를 `AgentBuilderService` constructor에 주입한다.
직접 `AgentBuilderService`를 구성하는 기존 호출자는 인수를 생략할 수 있고 동일 disabled implementation을
기본값으로 얻는다. `mutation_lifecycle()`, `parameter_tasks()`, `knowledge_selection()`과 `model_options()`의
dependency graph는 변경하지 않는다. 신규 factory는 DB session, user와 organization ID를 disabled object에
전달하지 않는다.

## 7. Implementation Sequence

1. strict DTO와 forbidden corpus의 실패 테스트를 먼저 작성한다.
2. Catalog v3 snapshot drift test, request-summary projection descriptor와 최소 closed-ref contract를 구현하고
   schema invariant를 통과시킨다.
3. codec round-trip/negative test를 먼저 작성한 뒤 canonical codec을 구현한다.
4. port Protocol과 dependency-free disabled boundary behavior test를 작성하고 구현한다.
5. composition factory, service no-op seam과 회귀 test를 추가한다.
6. application import boundary와 관련 Agent Builder 단위 test를 실행한다.

## 8. Protected-resource Boundary

이 component는 새 protected resource 조회, 저장 또는 실행 경로를 만들지 않는다. Disabled boundary가 위임하는
기존 Planner closure의 I/O는 기존 경로와 동일하며 boundary가 protected identity나 credential을 관찰하지 않는다.
`EphemeralCacheScope`는 향후 key builder에 identity를 전달하기 위한 non-serializable type contract일 뿐
MBA-343의 production request path에서 구성하거나 소비하지 않는다. Forbidden-content test는 credential,
Knowledge identity와 opaque handle이 cache plan에 들어가지 않음을 검증한다.

## 9. Completion Evidence

구현 PR은 다음 evidence를 남겨야 한다.

- targeted pytest command와 결과
- application import-boundary 결과
- canonical codec deterministic fixture 결과
- Catalog v3 snapshot drift 결과
- request-specific summary projection descriptor, canonical purpose snapshot과 parameter input-type applicability 결과
- typed store result와 public codec error redaction 결과
- forbidden-content parameterized corpus 결과
- `git diff --name-only`에 Redis/config/DB/Client/public API 변경이 없다는 범위 확인

문서 작성만 완료된 현재 상태는 구현 완료가 아니다.
