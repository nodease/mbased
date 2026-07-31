# 프로젝트 개요

> Status: Reference-only historical document.
>
> 이 문서는 과거 `main` 브랜치 기준 역공학 산출물이다. 현재 구현 기준은 active `docs/` 문서와 코드를 따른다.

## 제품 성격

Moduly는 드래그 앤 드롭 방식으로 AI 자동화 워크플로우를 설계하고 실행하는 오픈소스 플랫폼이다. 사용자는 “앱”을 만들고, 해당 앱에 연결된 워크플로우 그래프를 편집한 뒤 테스트하거나 배포할 수 있다.

확정 근거:

- `README.md`: 제품 소개, 핵심 기능, 기술 스택
- `apps/client/app/features/workflow/config/nodeRegistry.tsx`: UI에서 제공하는 노드 목록
- `apps/workflow_engine/workflow/core/workflow_node_factory.py`: 실제 실행 가능한 노드 타입

## 상위 구성

| 영역 | 구현 위치 | 책임 |
| --- | --- | --- |
| Client | `apps/client` | Next.js UI, 워크플로우 편집기, 대시보드, 공개 실행 화면 |
| Gateway | `apps/gateway` | FastAPI API 서버, 인증, 앱/워크플로우/배포/RAG/LLM API |
| Workflow Engine | `apps/workflow_engine` | Celery 워커, 워크플로우 그래프 실행, 노드 실행 |
| Sandbox | `apps/sandbox` | NSJail 기반 Python 코드 실행 API |
| Log System | `apps/log_system` | Celery 로그 태스크, 실행/노드 로그 DB 반영 |
| Shared | `apps/shared` | DB 모델, 스키마, Celery 설정, Redis Pub/Sub, 공통 서비스 |
| Infra | `docker`, `dev`, `infra` | Docker Compose, 개발 인프라, Helm/K8s/Terraform |
| Tests | `apps/*/tests`, `tests` | 단위/통합/평가/부하 테스트 |

## 기술 스택

| 계층 | 기술 |
| --- | --- |
| Frontend | Next.js 16, React 19, TypeScript, Tailwind CSS, React Flow, Zustand, Axios |
| Backend API | FastAPI, SQLAlchemy 2.0, Pydantic v2, Authlib, APScheduler |
| Async/Queue | Celery, Redis |
| Workflow | gevent pool, Jinja2, LiteLLM 계열 추상화, 자체 NodeFactory |
| Database | PostgreSQL, pgvector, Alembic |
| RAG | LangChain text splitters, PyMuPDF, LlamaParse, custom parsers/chunkers |
| Sandbox | FastAPI, NSJail, priority/fair scheduler |
| Infra | Docker Compose, Nginx, Squid Proxy, Kubernetes, Helm, Terraform |

## 핵심 도메인 개념

| 개념 | 설명 | 주요 모델/API |
| --- | --- | --- |
| User | 이메일/비밀번호 또는 Google OAuth 사용자 | `users`, `/auth/*` |
| App | 사용자가 만드는 모듈 단위. URL slug와 auth secret을 가진다 | `apps`, `/apps/*` |
| Workflow | App에 연결되는 편집 가능한 그래프 초안 | `workflows`, `/workflows/*` |
| Deployment | 특정 시점의 워크플로우 스냅샷과 배포 타입 | `workflow_deployments`, `/deployments/*` |
| Workflow Run | 워크플로우 실행 이력 | `workflow_runs`, `workflow_node_runs` |
| LLM Provider/Model/Credential | 공급자, 모델 카탈로그, 사용자 API Key | `llm_*`, `/llm/*` |
| Knowledge Base | 문서 묶음과 검색 설정 | `knowledge_bases`, `/knowledge/*` |
| Document/Chunk | 파일/API/DB 소스와 벡터 검색 단위 | `documents`, `document_chunks`, `/rag/*` |
| Connection | 외부 DB 연결 정보 | `connections`, `/connectors/*` |
| Schedule | 스케줄 트리거 배포의 cron 정보 | `schedules` |

## 주요 사용자 흐름

1. 회원가입 또는 로그인
2. 내 모듈에서 App 생성
3. 워크플로우 편집기에서 시작/LLM/조건/코드/API/응답 등 노드 구성
4. 초안 저장
5. 테스트 실행 또는 스트리밍 실행
6. 배포 생성
7. 공개 URL, API, Webhook, 스케줄 등으로 실행
8. 실행 로그와 통계 확인

## 구현상 중요한 정책

- 앱 생성 시 기본 Workflow도 함께 생성된다.
- App의 `url_slug`, `auth_secret`은 외부 실행 API와 Webhook에서 사용된다.
- Deployment는 `graph_snapshot`을 저장하고, 활성 배포는 App의 `active_deployment_id`와 동기화된다.
- 한 App에서 활성 배포는 하나만 유지하는 정책이 서비스 코드에 있다.
- Workflow Engine은 시작 노드를 하나만 허용하고, cycle과 고립 노드를 검증한다.
- Code Node는 직접 실행하지 않고 Sandbox API에 위임한다.
- 실행 이벤트는 Redis Pub/Sub로 발행되어 Gateway/Client에서 SSE 형태로 소비한다.
