# AGENTS.md - mbased / Nodease Project

## 프로젝트 개요

mbased는 기존 Moduly 코드를 리팩토링해 Nodease라는 기업 내부 AI workflow/LLMOps 운영 서비스를 만드는 프로젝트다.

현재 코드와 배포 리소스에는 아직 `Moduly` 명칭이 남아 있다. FastAPI title, Docker/Helm 리소스명, container name, README 실행 명령 등 기존 코드/인프라 식별자는 Moduly 기준으로 읽되, 새로 정리하는 제품/문서/기능 방향은 Nodease 기준으로 작성한다.

제품 방향은 기존 AI workflow 생성/실행/배포/RAG/LLM credential 기반 위에 다음 운영 능력을 단계적으로 추가하는 것이다.

- RBAC/resource/permission 기반
- audit/tracing 기반
- LLMOps observability
- RAG 데이터 변경과 chunk lineage
- 데이터 거버넌스와 정책 차단
- 비용/품질 비교와 추천
- 배포 전 체크와 운영 대시보드

## 프로젝트 구조

- `apps/client/`: Next.js 클라이언트. Workflow 편집, 설정, RBAC/observability UI를 담당한다.
- `apps/gateway/`: FastAPI Gateway. 인증된 API 진입점과 resource permission enforcement 경계다.
- `apps/workflow_engine/`: Celery 기반 workflow 실행 엔진과 node runtime.
- `apps/log_system/`: audit/trace/log 계열 비동기 worker.
- `apps/sandbox/`: NSJail 기반 code execution sandbox.
- `apps/shared/`: DB model, schema, 공통 service, tracing/audit utility.
- `docs/`: 프로젝트 전체 요구사항, 아키텍처, 데이터 모델, 용어, ADR.
- `docs/features/<feature-name>/`: 기능별 requirements, API, component, test case 문서.
- `docs_old/`: 과거 문서와 역공학 자료. 참고용이며 active source of truth가 아니다.
- `tests/`: repo 루트 공통 테스트. DB/schema, service-level, RAG evaluation baseline, load test 도구를 포함한다.
- `docker/`, `dev/`, `infra/`, `scripts/`: 배포, 로컬 개발, 운영 스크립트.

## 기술 스택

- Frontend: Next.js 16, React 19, TypeScript, Tailwind CSS, React Flow.
- Backend Gateway: Python 3.11, FastAPI, SQLAlchemy.
- Workflow Runtime: Celery, Redis.
- Database: PostgreSQL, pgvector.
- LLM/RAG: `apps/shared/services/llm_client`의 자체 OpenAI/Anthropic/Google client 계층, RAG ingestion/retrieval 관련 shared service.
- Sandbox: NSJail.
- Infra: Docker Compose, Kubernetes, Helm.
- Test: pytest, Vitest, Next build/lint.

## 프로젝트 문서

작업 전 반드시 관련 문서를 확인한다.

- 제품 요구사항: `docs/PRD.md`
- 전체 아키텍처: `docs/architecture.md`
- 데이터 모델: `docs/data_model.md`
- 용어 정의: `docs/glossary.md`
- 설계 결정: `docs/decisions/`
- 기능별 문서: `docs/features/<feature-name>/`

문서 간 충돌이 있으면 일반적으로 다음 순서를 우선한다.

1. `docs/decisions/`의 Accepted ADR
2. `docs/PRD.md`
3. `docs/architecture.md`
4. `docs/data_model.md`
5. `docs/features/<feature-name>/requirements.md`
6. `docs/features/<feature-name>/api_spec.md`
7. `docs/features/<feature-name>/component_spec.md`
8. `docs/features/<feature-name>/test_cases.md`
9. `docs_old/` 참고 자료

`docs_old/`는 배경 이해와 누락 복구를 위한 참고 자료로만 사용한다. 현재 문서와 충돌하면 `docs/` 아래 active 문서를 기준으로 판단한다.

`docs_old/`는 이관이 끝나면 삭제하는 임시 아카이브다.

- 이관 완료 기준: 특정 영역의 `docs/` 문서가 원본 내용을 흡수하고 코드 대조 검증까지 마쳐 Draft를 벗어나면, `docs_old/`의 해당 원본 문서를 삭제한다.
- 삭제 전 `docs/` 안에서 해당 원본을 가리키는 링크를 확인하고, 남아 있으면 새 문서로 바꾸거나 plain text로 정리한다 (이관된 ADR의 `docs_old/` 링크는 역사 기록이므로 끊겨도 무방하다).
- 모든 영역의 이관이 끝나면 `docs_old/` 디렉토리 자체를 삭제한다. 전체 내용은 아카이브 커밋(eedd820)으로 git history에 남는다.

## 문서 작성 규칙

- 제품 전체 결정은 `docs/`에 둔다.
- 기능별 세부사항은 `docs/features/<feature-name>/`에 둔다.
- 공통 용어는 feature 문서에서 재정의하지 말고 `docs/glossary.md`를 참조한다.
- feature 문서는 `requirements.md`, `api_spec.md`, `component_spec.md`, `test_cases.md` 4종을 기본으로 한다.
- 동작, API, UI flow, 권한, 테스트 기대값이 바뀌면 관련 feature 문서를 함께 수정한다.
- 추상적인 설명보다 검증 가능한 요구사항과 테스트 가능한 문장을 우선한다.
- 문서 메타 블록은 `Status`만 유지한다. `requirements.md`는 `Related Features`를 추가한다. `Owner`, `Last Updated`는 넣지 않는다. 작성자와 수정 시점은 git history가 답한다.
- 코드 검증이 필요한 문서에는 `Verified Against: <branch> @ <commit>` 형식을 사용한다. 실제 코드 확인 없이 이 값을 갱신하지 않는다.
- secret value, credential 원문, API key, token, `encrypted_config` 값/content, raw payload는 문서와 로그에 노출하지 않는다.

## 코딩 컨벤션

### TypeScript / Frontend

- TypeScript strict 기준을 유지한다.
- React는 함수 컴포넌트와 hooks 중심으로 작성한다.
- UI는 `apps/client/`의 기존 컴포넌트, hook, 상태 관리 패턴을 우선 따른다.
- 권한별 UI는 프론트에서 UX 차단을 하되, 최종 보안 판단은 Gateway/API가 수행한다고 전제한다.
- API request/response 타입과 화면 상태가 문서와 어긋나면 문서 또는 구현 중 무엇이 기준인지 먼저 확인한다.

### Python / Backend

- Gateway endpoint는 얇게 유지한다. 요청 파싱, 인증/권한 의존성 연결, service 호출, 응답 반환에 집중한다.
- 비즈니스 판단은 `apps/gateway/services/`, `apps/shared/services/`, helper layer로 이동한다.
- RBAC, audit, tracing은 controller가 아니라 service/helper 경계에서 적용한다.
- 공통 DB model, schema, tracing/audit utility는 `apps/shared/`의 기존 패턴을 우선 사용한다.
- 큰 schema refactor보다 additive migration/extension을 우선한다.

## 개발 규칙

### 반드시 지켜야 할 것

- 새 기능 구현 전 관련 `docs/features/<feature-name>/test_cases.md`를 확인하고, 필요한 테스트를 먼저 추가하거나 갱신한다.
- 권한이 필요한 API는 resource permission 정책을 확인하고, 권한 없는 접근을 API와 실행 경로 모두에서 차단한다.
- workflow 생성, 저장, 실행, 배포의 기존 경로가 깨지지 않도록 한다.
- LLM credential, deployment secret, trace payload, audit metadata를 다룰 때 secret 원문이 응답이나 로그에 노출되지 않도록 확인한다.
- API 변경 시 `api_spec.md`, UI 변경 시 `component_spec.md`, 테스트 기대값 변경 시 `test_cases.md`를 함께 갱신한다.
- 중요한 정책 또는 아키텍처 변경은 `docs/decisions/`에 ADR로 남긴다.

### 하지 말아야 할 것

- 명세에 없는 기능을 임의로 추가하지 않는다.
- `docs_old/`의 내용을 active source of truth처럼 사용하지 않는다.
- feature 문서에서 Organization, Workflow, Agent, Knowledge 같은 공통 용어를 새로 정의하지 않는다.
- secret value, API key, token, credential 원문, raw payload를 문서, 로그, 테스트 fixture에 남기지 않는다.
- 대규모 리팩터링이나 schema 재설계를 기능 구현과 섞지 않는다.
- 권한 차단을 프론트 UI만으로 처리하지 않는다.

### 경계 규칙

- `apps/gateway/`, `apps/shared/`, `apps/workflow_engine/`, `apps/log_system/`, `apps/sandbox/` 작업 시 프론트 변경이 꼭 필요하지 않으면 `apps/client/`를 수정하지 않는다.
- `apps/client/` 작업 시 API 계약 변경이 필요하면 먼저 `docs/features/<feature-name>/api_spec.md`와 Gateway 영향을 확인한다.
- `apps/shared/` 변경은 Gateway, Workflow Engine, Log System, Sandbox에 영향을 줄 수 있으므로 관련 테스트 범위를 넓힌다.
- Workflow Engine은 가능한 경우 user, organization, workflow, run, node 식별자를 포함한 execution context를 전달받아야 한다.
- 공통 tracing/audit service가 trace 접근과 payload 처리의 경계다.
- deployment/runtime 변경은 `docker/`, `dev/`, `infra/`, `scripts/`, `docs/architecture.md`의 정합성을 함께 확인한다.

## Review guidelines

Codex PR 리뷰는 한국어로 작성하고, 실제 장애나 제품 시연 실패로 이어질 수 있는 문제를 우선한다.

리뷰 코멘트에는 가능한 경우 심각도를 `P0`, `P1`, `P2`, `P3` 중 하나로 표시한다.

- `P0`: 제품 시연, 핵심 사용자 흐름, 배포, 데이터 무결성, 보안 경계를 즉시 깨뜨리는 결정적 문제. merge 전에 반드시 수정해야 한다.
- `P1`: 주요 기능 실패, 권한 우회, API 계약 파괴, 재시도/동시성으로 인한 중복 실행처럼 실제 사용 또는 시연에서 높은 확률로 드러나는 문제. merge 전 수정을 강하게 요구한다.
- `P2`: 특정 조건에서 실패하거나 운영 안정성, 성능, 테스트 신뢰도, 유지보수성에 의미 있는 위험을 만드는 문제. 이번 PR 또는 가까운 후속 PR에서 수정해야 한다.
- `P3`: 명확한 개선 여지는 있지만 시연, 보안, 데이터, 핵심 기능에는 직접 영향이 낮은 문제. 선택적 개선으로 다룬다.

- P0/P1 수준의 버그, 데이터 손상, 권한 우회, API 계약 파괴, 배포 장애, 제품 시연 차단 가능성을 먼저 지적한다.
- 변경이 제품 시연에 결정적인 버그인지 판단한다. 로그인, 조직/팀 선택, workflow 생성/편집/실행/배포, RAG 질의, LLM credential 연결, audit/trace 확인 같은 데모 핵심 흐름이 깨지면 높은 우선순위로 표시한다.
- 단순 취향, 네이밍, 사소한 리팩터링, 포맷 차이는 실제 위험과 연결되지 않으면 중요 이슈로 다루지 않는다.
- 인증/인가가 필요한 API, workflow 실행, 배포, credential, organization/team/resource 접근 경로에서 권한 검사가 빠졌는지 확인한다.
- DB model, migration, seed, relation, query 변경은 기존 데이터 호환성, transaction 경계, N+1, cascade/nullable 영향까지 확인한다.
- API request/response schema가 바뀌면 `docs/features/<feature-name>/api_spec.md`, 프론트 호출부, 타입 정의, 테스트가 함께 갱신되었는지 확인한다.
- workflow node runtime, Celery task, Redis pub/sub, schedule, async/background job 변경은 중복 실행, race condition, retry/idempotency 문제를 확인한다.
- audit, tracing, RAG, LLM credential, deployment secret, raw payload를 다루는 변경은 secret 원문이나 민감 데이터가 응답, 로그, trace, fixture에 남지 않는지 확인한다.
- 핵심 비즈니스 로직, 권한 정책, schema, workflow 실행 경로가 바뀌면 관련 테스트 또는 문서가 함께 갱신되었는지 확인한다.
- 변경 범위가 공유 모듈이나 운영 경계에 닿으면 Gateway, Workflow Engine, Log System, Sandbox 중 영향받는 서비스의 테스트 범위가 충분한지 확인한다.
- 리뷰 코멘트는 문제 위치, 재현/영향, 수정 방향이 분명할 때 남긴다. 근거가 약한 추측은 질문이나 확인 요청으로 표현한다.

## 테스트와 검증

- 전체 검증: `./scripts/test.sh`
- Client: `cd apps/client && npm run lint && npm run test && npm run build`
- Gateway: `cd apps/gateway && PYTHONPATH=$(git rev-parse --show-toplevel) .venv/bin/python -m pytest tests`
- Workflow Engine: `cd apps/workflow_engine && PYTHONPATH=$(git rev-parse --show-toplevel) .venv/bin/python -m pytest tests`
- Log System: `PYTHONPATH=$(git rev-parse --show-toplevel) apps/workflow_engine/.venv/bin/python -m pytest apps/log_system/tests`
- Shared: `PYTHONPATH=$(git rev-parse --show-toplevel) apps/gateway/.venv/bin/python -m pytest apps/shared/tests`
- Sandbox: `PYTHONPATH=$(git rev-parse --show-toplevel) apps/workflow_engine/.venv/bin/python -m pytest apps/sandbox/tests`
- Root Tests: `PYTHONPATH=$(git rev-parse --show-toplevel) apps/gateway/.venv/bin/python -m pytest tests/test_permission_schema.py tests/db tests/services tests/evaluation/test_rag_baseline.py`
- Root Evaluation Benchmarks: `tests/evaluation/`에는 RAG 평가용 수동 벤치마크 도구가 있다. 데이터셋 준비, Knowledge Base 인덱싱, `run_benchmark.py` 실행, `reports/` 확인은 `tests/evaluation/README.md`를 따른다.
- Root Load Tests: `tests/load/`는 Locust 기반 수동 부하 테스트다. 서버, `.env`, `LOAD_TEST_DEPLOYMENT_SLUG`, `LOAD_TEST_AUTH_TOKEN` 준비 후 `tests/load/README.md`를 따라 별도로 실행한다.

변경 범위가 작으면 관련 테스트부터 실행하고, 공유 모듈이나 권한/trace/schema 경계를 건드렸으면 더 넓은 테스트를 실행한다. 실행하지 못한 테스트는 최종 응답에 명시한다.
