# ADR-0022: Incremental Hexagonal Architecture Adoption

Status: Accepted
Related ADRs: [ADR-0008](ADR-0008-audit-action-naming-standard.md), [ADR-0010](ADR-0010-resource-access-403-404-policy.md), [ADR-0011](ADR-0011-team-router-rbac-service-boundary.md), [ADR-0018](ADR-0018-workflow-rag-anonymous-public-only-runtime.md), [ADR-0019](ADR-0019-agent-builder-preview-apply-save-boundary.md), [ADR-0021](ADR-0021-webhook-capture-helper-security-boundary.md)

## Context

Nodease는 이미 `apps/client`, `apps/gateway`, `apps/workflow_engine`, `apps/log_system`, `apps/sandbox`, `apps/shared`로 논리 서비스가 나뉘어 있다. `docs/architecture.md`도 Gateway endpoint를 얇게 유지하고 RBAC/audit/tracing 판단을 service/helper 경계에서 수행한다는 목표를 명시한다.

하지만 현재 구현은 과도기 상태다. 일부 Gateway router는 DB query, permission decision, mutation, audit 기록을 직접 조합한다. `apps/shared`에는 Gateway 구현을 import하는 seed/helper 코드가 남아 있고, Gateway와 Workflow Engine에는 LLM/RAG runtime 정책이 중복된 대형 service가 존재한다. 이 상태에서 전체 repository를 한 번에 재배치하면 권한, 배포, Knowledge/RAG, workflow runtime 경계가 동시에 흔들릴 수 있다.

## Decision

헥사고널 아키텍처를 전면 재작성 방식이 아니라 점진 도입 방식으로 채택한다.

새 business flow는 가능한 경우 다음 runtime call flow로 작성한다.

```text
Inbound adapter -> Application use case -> Port -> Outbound adapter
                                  |
                                  v
                            Domain policy
```

의존성은 바깥쪽 adapter에서 안쪽 application/domain 쪽으로만 흐른다. Domain policy는 FastAPI request/response, SQLAlchemy session, Celery app, provider SDK, storage client를 직접 알지 않는다.

## Scope

이번 결정은 backend application boundary에 적용한다.

포함:

- Gateway API business flow
- Workflow Engine runtime use case
- Gateway/Workflow Engine이 공유하는 순수 policy 또는 contract
- repository, audit, queue, storage, provider client adapter 경계

제외:

- 기존 대형 router/service 대량 이동
- SQLAlchemy model의 순수 domain entity 전면 변환
- Client UI 구조 변경
- Log System/Sandbox 구조 변경
- public API schema 변경
- DB migration

## Standard Layers

| Layer | 책임 | 허용 | 금지 |
| --- | --- | --- | --- |
| Inbound adapter | FastAPI router, Celery task, CLI 같은 진입점 | request parsing, dependency 연결, command 생성, response mapping | DB query로 정책 판단, transaction orchestration |
| Application use case | 업무 흐름 조율 | transaction 경계, permission/policy 호출 순서, port 호출, audit recorder/outbox port 호출 | SQLAlchemy query expression, provider SDK 호출, HTTP response shape 결정 |
| Domain policy | DB 없이 테스트 가능한 업무 규칙 | 권한 상태 비교, lifecycle 전이, deployment audience, retrieval 후보 규칙 | DB session, FastAPI request, Celery task, storage path 접근 |
| Port | use case가 필요한 외부 행위의 interface | repository, UnitOfWork, audit recorder, queue, storage, provider client protocol | 특정 ORM table CRUD를 그대로 노출하는 거대한 generic repository |
| Outbound adapter | 기술 세부사항 구현 | SQLAlchemy query, Celery enqueue, storage/provider SDK 호출 | 업무 정책 독자 결정 |

이 ADR에서 inbound port는 use case command/handler interface로 취급하고, `Port` 명칭은 주로 outbound port에 사용한다.

## Package Direction

초기 bootstrap에서 물리적으로 생성한 첫 package scaffold는 Gateway deployment domain package와 필요한 parent package로 제한했다. 당시 scaffold는 package boundary만 만들었고, 실제 deployment preflight use case/port 이관은 후속 pilot에서 진행하도록 결정했다.

다른 도메인도 동일한 router/use case/domain policy/port/adapter 기준을 따른다. 다만 `permissions`, `knowledge`, `llm`, `workflow_management`, `runtime_retrieval` 같은 domain package는 빈 구조로 선생성하지 않고, 해당 도메인의 첫 리팩터링 PR에서 실제 use case/port와 함께 만든다. Deployment preflight pilot은 use case/port/adapter 이관이 완료된 뒤 이후 도메인 리팩터링의 reference implementation으로 사용한다.

Allowed first scaffold:

```text
apps/gateway/
  application/
    __init__.py
    deployment/
      __init__.py
  adapters/
    __init__.py
    db/
      __init__.py
    audit/
      __init__.py
```

Longer-term target package names should make ownership explicit. Gateway workflow management code uses `workflow_management`; Workflow Engine runtime retrieval uses `runtime_retrieval`.

`apps/shared/domain/*` package는 실제 Gateway와 Workflow Engine이 함께 쓰는 순수 policy 또는 contract가 생길 때만 만든다. `apps/shared`는 Gateway 또는 Workflow Engine의 concrete implementation을 import하지 않는다.

## Initial Pilot

첫 pilot은 `deployment preflight`로 고정한다.

이유:

- 기존 service가 비교적 독립적이다.
- public/API/webhook/schedule/workflow_node active surface 정책을 작은 범위에서 검증할 수 있다.
- private KB 차단, workflow_node target 검증, 409 보존 같은 운영/보안 가치가 큰 규칙을 포함한다.
- Permission mutation보다 resource type matrix가 좁고, Knowledge ingestion/retrieval보다 외부 adapter 수가 적다.

구현 상태:

- `apps/gateway/application/deployment/`에 framework-independent result/error, repository port와 preflight use case를 둔다.
- SQLAlchemy query는 `apps/gateway/adapters/db/deployment_preflight_repository.py`가 pure snapshot으로 변환한다.
- `apps/gateway/composition/deployment.py`가 concrete dependency를 조립한다.
- 기존 service facade는 application blocked error를 기존 `409 deployment.preflight.blocked` envelope으로 변환해 public API contract를 보존한다.
- Application import boundary와 기존 preflight behavior 회귀 테스트를 함께 유지한다.
- 이 read-only policy pilot에는 UnitOfWork가 필요하지 않다. 이후 permission mutation pilot은 이 구조에 transaction owner, UnitOfWork, transaction-bound audit port를 추가해야 한다.

후속 pilot 순서는 다음을 기본값으로 둔다.

1. Permission mutation
2. Knowledge ingestion/retrieval
3. LLM credential/runtime selection
4. Workflow execution dispatch

## Composition Root

Concrete adapter와 use case 조립은 outer boundary에서 수행한다.

| Runtime | Composition root |
| --- | --- |
| Gateway API | `apps/gateway/api/deps.py` 또는 endpoint module의 dependency factory |
| Gateway background/helper | 필요할 때만 `apps/gateway/composition.py` 또는 `apps/gateway/composition/<domain>.py` |
| Workflow Engine task | `apps/workflow_engine/tasks.py` 또는 `apps/workflow_engine/composition/<domain>.py` |

Composition root는 concrete adapter와 application use case를 함께 import할 수 있으므로 application/domain package 안에 두지 않는다. Router function 본문에서 여러 concrete repository/adapter를 직접 조립하지 않는다. Composition root는 concrete dependency wiring만 담당하고 업무 정책을 판단하지 않는다.

## Transaction Boundary

신규 use case의 commit owner는 use case 또는 UnitOfWork다.

- Repository adapter는 기본적으로 `commit()` 또는 `rollback()`을 호출하지 않는다.
- Repository adapter는 필요한 경우 `flush()`까지만 수행한다.
- Audit recorder는 같은 transaction에 기록하거나 durable outbox에 기록한다.
- Permission, deployment activation, Knowledge lifecycle처럼 보안/운영 상태를 바꾸는 mutation은 audit 또는 outbox 기록 실패 시 성공으로 처리하지 않는다.
- Legacy service facade가 기존 commit을 유지하는 과도기 예외는 PR 본문에 명시한다.

## Error Mapping

신규 application/domain error는 FastAPI `HTTPException`에 의존하지 않는다. Inbound adapter가 HTTP response로 변환한다. 기존 `apps/gateway/services/*`의 `HTTPException` 사용은 과도기 예외이며, 동작 보존 테스트 없이 일괄 수정하지 않는다.

| Application/domain error | HTTP status | 원칙 |
| --- | ---: | --- |
| `AuthenticationRequired` | 401 | 로그인 또는 인증 필요 |
| `ResourceHidden` | 404 | organization scope 밖 또는 safe hiding 대상 |
| `PermissionDenied` | 403 | scope 안 action 권한 부족 |
| `DeploymentPreflightBlocked` | 409 | active deployment surface 생성 차단 |
| `Conflict` / `StaleState` | 409 | stale version, 중복 mutation, lifecycle race |
| `InputValidationError` | 422 | request schema 검증과 구분되는 application-level 입력 제약 위반 |
| `ExternalAdapterUnavailable` | 502, 503 또는 timeout 계약의 504 | provider/storage/parser 같은 외부 adapter 실패 |
| `InvariantViolation` | 500 | 사용자가 해결할 수 없는 내부 불변식 위반 |

기존 API contract가 이미 다른 status를 사용한다면 리팩터링 PR에서 임의로 바꾸지 않는다. 변경이 필요하면 feature `api_spec.md`와 test case를 함께 갱신한다.

## Testing And Coverage

Coverage 50%는 초기 baseline target으로 본다. 기준 단위는 CI gate 적용 전에 backend Python package와 frontend Vitest suite를 구분해 확정한다. 별도 언급이 없으면 line coverage를 기준으로 하되, 숫자만 맞추기보다 permission, deployment, Knowledge/RAG, LLM credential, workflow runtime 같은 critical path의 policy/use case 테스트를 우선한다.

Coverage gate를 CI에 적용하기 전 다음을 확인한다.

- Python app의 `pytest-cov` 또는 동등한 coverage tool 설치 여부
- Vitest coverage provider 설치 여부
- 최초 baseline report
- threshold 적용 단계와 예외 기준

초기 단계에서는 report 생성만 필수로 하고, threshold와 예외 기준은 baseline 확인 후 후속 PR에서 단계적으로 적용한다.

## Import Boundary Verification

`apps/shared` production code는 Gateway 또는 Workflow Engine concrete implementation을 새로 import하지 않는다.

Non-test code 검증은 다음 명령을 기준으로 한다. 이 명령은 현재 0건이어야 통과하는 gate가 아니라 startup/development seed와 local demo seed를 포함한 known violation baseline을 확인하는 scan이다. 기존 known violation은 transitional exception으로 기록하고, 이후 PR에서는 새 production code의 역방향 import가 추가되지 않았는지 확인한다.

```powershell
rg -n "from apps\\.gateway|import apps\\.gateway|from apps\\.workflow_engine|import apps\\.workflow_engine" apps/shared -g "*.py" -g "!apps/shared/tests/**"
```

전체 baseline audit은 tests/manual 경로까지 포함해 다음 명령으로 확인한다.

```powershell
rg -n "from apps\\.gateway|import apps\\.gateway|from apps\\.workflow_engine|import apps\\.workflow_engine" apps/shared -g "*.py"
```

기존 위반은 startup/development seed(`apps/shared/db/seed.py`), local demo seed(`apps/shared/db/demo_seed.py`), manual/integration test 경로로 구분해 별도 정리 대상으로 기록한다. 특히 startup seed는 Gateway lifespan에서 호출될 수 있으므로 단순 test-only 예외로 보지 않는다. 새 production code에는 역방향 import를 추가하지 않는다.

## Transitional Exceptions

이 결정은 목표 구조와 신규 코드 작성 기준을 정의한다. 현재 구현에는 과도기 예외가 있다.

- `users.py`, `permissions.py` 계열 endpoint에는 router에서 DB query와 permission helper를 직접 조합하는 코드가 남아 있다.
- Gateway와 Workflow Engine에는 LLM/RAG runtime 정책이 중복된 service가 남아 있다.
- `apps/shared/db/seed.py`에는 Gateway lifespan에서 호출될 수 있는 startup/development seed 역방향 import가 남아 있고, `apps/shared/db/demo_seed.py`에는 local demo seed/helper 역방향 import가 남아 있다.
- 기존 service가 transaction commit을 소유하는 경로가 남아 있다.

해당 영역을 수정할 때는 이 ADR의 경계로 이관하되, 동작 보존 테스트 없이 대량 이동하지 않는다.

## Consequences

장점:

- 정책 위치를 추적하기 쉬워진다.
- 권한, 배포, Knowledge/RAG 경계의 단위 테스트가 쉬워진다.
- Gateway와 Workflow Engine 사이의 policy duplication을 점진적으로 줄일 수 있다.
- `apps/shared`의 책임을 cross-runtime contract와 pure policy 중심으로 제한할 수 있다.

비용:

- 초기에는 legacy service facade와 새 use case가 공존한다.
- 작은 port/interface 파일이 늘어난다.
- Composition root와 UnitOfWork 규칙을 리뷰에서 지속적으로 확인해야 한다.

## Follow-up

- Deployment preflight pilot에서 use case/port/adapter 구조를 검증했고, 이후 변경에서 import boundary와 behavior 회귀 테스트를 유지한다.
- Permission mutation을 두 번째 pilot으로 분리한다.
- Knowledge ingestion/retrieval을 세 번째 pilot으로 분리한다.
- Import boundary 검증을 자동화할지 결정한다.
- Coverage baseline 생성 후 threshold 적용 단계를 결정한다.
