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

### 구현 및 테스트 운영

코드 변경은 원칙적으로 TDD로 진행한다.

1. 버그 또는 요구사항을 재현하는 테스트를 먼저 작성한다.
2. 테스트가 의도한 이유로 실패하는지 확인한다.
3. 테스트를 통과시키는 최소 구현을 작성한다.
4. 중복이나 구조적 문제가 있을 때만 제한적으로 리팩터링한다.
5. 정상 경로와 함께 실제 위험이 있는 실패·경계·상태 전이를 검증한다.

테스트는 발견된 버그, 공식 계약·보안 불변조건, 경계값·실패·재시도·동시성·멱등성 또는 회귀 위험이 높은 공유 계약 중 하나 이상을 검증해야 한다. 동일 동작을 여러 계층에서 반복 검증하거나 구현 세부사항에 과도하게 결합된 테스트는 추가하지 않는다. 문서만 수정하거나 테스트를 먼저 작성할 수 없는 기계적 변경은 TDD 예외로 처리하되 이유를 기록한다.

로컬에서는 변경된 도메인의 빠른 단위 테스트와 필요한 type check, lint, import·문서 정합성 검사만 실행한다. 실제 PostgreSQL 통합 테스트, 전체 migration upgrade/downgrade, 전체 E2E, Docker/Kubernetes 통합 검증과 전체 저장소 회귀는 원칙적으로 원격 CI에 위임하되 필요한 테스트 코드는 작성한다. DB 변경은 로컬 실행 여부와 무관하게 migration chain, head, nullable, index, FK, cascade와 downgrade 계약을 정적으로 검토한다.

전체 회귀는 공유 계층이나 핵심 계약 변경으로 반드시 필요한 경우에만 PR 직전 한 번 실행하고, 실행 전 필요성을 보고한다. CI 실패는 직접 원인과 근본 원인을 구분하고, 증상 우회나 고정값 추가 대신 범위 내 회귀 테스트와 함께 수정한다. 같은 결과를 확인하기 위한 불필요한 재실행을 반복하지 않는다.

### 서브에이전트 운영

서브에이전트 수에는 고정 상한을 두지 않지만 독립적으로 분리 가능한 작업에만 사용하고 중복 조사를 금지한다. 메인 에이전트는 각 서브에이전트에 관점, 대상 파일, 산출물과 수정 권한을 명시하고 findings의 중복 제거, 심각도 판단과 최종 통합을 담당한다. 여러 에이전트가 같은 파일을 동시에 수정하지 않는다.

모델과 추론 수준을 선택할 수 있는 환경에서는 작업 난이도와 위험에 맞춰 비용을 최적화한다.

- 파일 탐색, 테스트 목록 작성, 문서 비교는 저비용 모델과 낮음~중간 추론을 사용한다.
- 일반 구현과 단위 테스트 검토는 중간급 모델과 중간 추론을 사용한다.
- 보안, 권한, 동시성, migration, 분산 실행과 아키텍처는 필요한 경우에만 고성능 모델과 높은 추론을 사용한다.
- 최종 적대적 검토는 위험도가 높을 때만 고성능 모델을 사용한다.

### 보호 리소스 기능 완결성

다음 중 하나에 해당하는 기능은 `docs/engineering/protected-resource-feature-completion.md`를 적용한다.

- 보호 리소스 ID 또는 credential reference를 graph, 설정, deployment 등 durable data에 저장한다.
- user/team 권한, organization scope, owner 또는 `use`/`manage` 권한을 판정한다.
- `active`, `revoked`, `deleted`, `rotated` 같은 상태 전이가 실행 가능 여부에 영향을 준다.
- preflight와 runtime/background 실행이 같은 리소스를 서로 다른 시점에 사용한다.
- secret, PII, 외부 provider 호출 또는 외부 부수효과를 다룬다.

적용 대상 작업은 저장·관리 API/UI·preflight·runtime/background·lifecycle·audit/redaction·테스트 경계를 하나의 기능 단위로 검토한다. 각 경계는 `완료`, `해당 없음` 또는 `후속 이슈`로 기록하고 코드, 테스트, 문서 또는 이슈를 증거로 연결한다. 권한 우회, secret 노출, 외부 I/O 이전 fail-closed 실패 또는 기존 관리 경로 단절을 만드는 필수 경계는 후속 이슈로 미룬 채 병합하지 않는다.

단순 문서 교정, 무상태 내부 helper 또는 해당 경계에 영향을 주지 않는 변경은 비적용 사유만 기록한다. 모든 테스트를 반복 실행하는 것이 목적이 아니며, 실제 소비 경계와 상태 전이를 검증하는 최소 테스트를 선택한다.

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

## Code Review Rules

Codex PR 리뷰는 한국어로 작성하고, 실제 장애나 제품 시연 실패로 이어질 수 있는 문제를 우선한다.

### 심각도

리뷰 코멘트에는 가능한 경우 심각도를 `P0`, `P1`, `P2`, `P3` 중 하나로 표시한다.

- `P0`: 제품 시연, 핵심 사용자 흐름, 배포, 데이터 무결성, 보안 경계를 즉시 깨뜨리는 결정적 문제. merge 전에 반드시 수정해야 한다.
- `P1`: 주요 기능 실패, 권한 우회, API 계약 파괴, 재시도/동시성으로 인한 중복 실행처럼 실제 사용 또는 시연에서 높은 확률로 드러나는 문제. merge 전 수정을 강하게 요구한다.
- `P2`: 특정 조건에서 실패하거나 운영 안정성, 성능, 테스트 신뢰도, 유지보수성에 의미 있는 위험을 만드는 문제. 이번 PR 또는 가까운 후속 PR에서 수정해야 한다.
- `P3`: 명확한 개선 여지는 있지만 시연, 보안, 데이터, 핵심 기능에는 직접 영향이 낮은 문제. 선택적 개선으로 다룬다.

P2/P3는 재현 가능한 실패 조건이나 구체적인 운영·회귀 위험이 있을 때만 사용한다. 단순 취향, 네이밍,
사소한 리팩터링, 포맷 차이와 근거 없는 미래 가능성은 finding으로 남기지 않는다.

### 고위험 경계

- 로그인, 조직/팀 선택, workflow 생성·편집·실행·배포, RAG 질의, LLM credential 연결과 audit/trace 확인 같은 데모 핵심 흐름의 회귀를 우선 확인한다.
- 인증·인가가 필요한 API와 runtime/background 경로에서 active organization, resource permission과 lifecycle을 protected row 조회 또는 외부 부수효과 전에 확인하는지 검토한다.
- DB model, migration, seed, relation과 query 변경은 기존 데이터 호환성, transaction 경계, concurrency, nullable/index/FK/cascade와 rollback 영향을 확인한다.
- workflow node, Celery task, Redis pub/sub, schedule과 background job은 중복 실행, race, retry, idempotency, lease/fencing과 부분 성공 가능성을 확인한다.
- audit, tracing, RAG, credential, deployment secret과 raw payload 변경은 secret·PII가 응답, 로그, trace, audit와 fixture에 남지 않는지 확인한다.
- API 또는 공유 계약 변경은 영향을 받는 Client, Gateway, Workflow Engine, Log System, Sandbox, 공식 문서와 테스트를 필요한 범위에서 함께 대조한다.

### Finding 품질

- 변경된 전체 흐름과 관련 테스트를 먼저 읽고, 같은 근본 원인에서 파생된 위치는 하나의 finding으로 묶어 영향 범위를 함께 적는다.
- Finding은 현재 PR의 최신 HEAD에서 재현되고, 현재 diff가 문제를 새로 만들었거나 악화했거나 기존 문제를 새로운 실행 경로에서 도달 가능하게 만든 경우에만 남긴다. 현재 diff와 무관한 기존 문제는 PR finding으로 남기지 않고 필요한 경우 별도 이슈 후보로 구분한다.
- 확인 가능한 범위에서 최신 HEAD에서 이미 수정된 문제, outdated line context 또는 같은 근본 원인을 다루는 기존 활성 review thread를 반복해서 코멘트하지 않는다.
- 문제 위치, 재현 또는 실패 조건, 사용자·보안·데이터 영향, 충돌하는 공식 계약과 수정 방향이 분명할 때만 코멘트를 남긴다. 근거가 약하면 단정하지 말고 질문으로 표현한다.
- formatting, lint, import 정렬과 생성 파일 정합성처럼 결정적으로 자동 검사할 수 있는 항목은 CI에 맡긴다. 단, PR이 검사 규칙 자체를 약화하거나 CI 실패를 유발한 경우는 finding 대상이다.
- 테스트 누락은 발견된 버그, 보안 불변조건, 상태 전이, concurrency·retry·idempotency 또는 공유 계약 회귀를 구체적으로 보호할 때만 지적한다.
- finding이 없으면 GitHub review 결과에 지원되지 않는 별도 prose나 필드를 추가하지 않는다. 별도 summary를 지원하는 실행 환경에서만 finding이 없음을 밝히고, 실행하지 않은 검증과 남은 환경·통합 위험을 기록한다.

## 테스트와 검증

- 전체 검증: `./scripts/test.sh`
- Client: `cd apps/client && npm run lint && npm run test && npm run build`
- Gateway: `cd apps/gateway && PYTHONPATH=$(git rev-parse --show-toplevel) .venv/bin/python -m pytest tests`
- Workflow Engine: `cd apps/workflow_engine && PYTHONPATH=$(git rev-parse --show-toplevel) .venv/bin/python -m pytest tests`
- Log System: `PYTHONPATH=$(git rev-parse --show-toplevel) apps/workflow_engine/.venv/bin/python -m pytest apps/log_system/tests`
- Shared: `PYTHONPATH=$(git rev-parse --show-toplevel) apps/workflow_engine/.venv/bin/python -m pytest apps/shared/tests`
- Sandbox: `PYTHONPATH=$(git rev-parse --show-toplevel) apps/workflow_engine/.venv/bin/python -m pytest apps/sandbox/tests`
- Root Tests: `PYTHONPATH=$(git rev-parse --show-toplevel) apps/gateway/.venv/bin/python -m pytest tests/test_permission_schema.py tests/db tests/services tests/evaluation/test_rag_baseline.py`
- Root Evaluation Benchmarks: `tests/evaluation/`에는 RAG 평가용 수동 벤치마크 도구가 있다. 데이터셋 준비, Knowledge Base 인덱싱, `run_benchmark.py` 실행, `reports/` 확인은 `tests/evaluation/README.md`를 따른다.
- Root Load Tests: `tests/load/`는 Locust 기반 수동 부하 테스트다. 서버, `.env`, `LOAD_TEST_DEPLOYMENT_SLUG`, `LOAD_TEST_AUTH_TOKEN` 준비 후 `tests/load/README.md`를 따라 별도로 실행한다.

변경 범위가 작으면 관련 테스트부터 실행하고, 공유 모듈이나 권한/trace/schema 경계를 건드렸으면 더 넓은 테스트를 실행한다. 실행하지 못한 테스트는 최종 응답에 명시한다.
