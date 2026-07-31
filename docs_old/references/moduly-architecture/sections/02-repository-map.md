# 저장소 구조

> Status: Reference-only historical document.
>
> 이 문서는 과거 `main` 브랜치 기준 역공학 산출물이다. 현재 구현 기준은 active `docs/` 문서와 코드를 따른다.

## 루트 구조

| 경로 | 역할 |
| --- | --- |
| `README.md` | 제품/설치/아키텍처/사용법 소개 |
| `apps/client` | Next.js 프론트엔드 |
| `apps/gateway` | FastAPI Gateway API |
| `apps/workflow_engine` | Celery 기반 워크플로우 실행 워커 |
| `apps/sandbox` | Python 코드 실행 샌드박스 서비스 |
| `apps/log_system` | 로그 저장 Celery 워커 |
| `apps/shared` | 공통 DB 모델, 스키마, Celery, Redis, ingestion/LLM 공통 로직 |
| `dev` | 로컬 개발용 Docker Compose와 환경 예시 |
| `docker` | 전체 서비스 Docker Compose, Dockerfile, Nginx/Squid 설정 |
| `infra` | Helm, Kubernetes manifest, Terraform |
| `scripts` | setup/dev/test/clean 스크립트 |
| `tests` | RAG 평가, 부하 테스트 |
| `docs` | 기존 이미지/추가 문서가 있는 디렉터리 |
| `local/docs` | 이번 역공학 산출물 |

## Client 구조

| 경로 | 역할 |
| --- | --- |
| `apps/client/app/page.tsx` | 루트 화면. 인증 상태에 따라 대시보드 리다이렉트 또는 랜딩 표시 |
| `apps/client/app/auth/*` | 로그인/회원가입 화면 |
| `apps/client/app/dashboard/*` | 대시보드, 내 모듈, 탐색, 지식, 설정, 통계 |
| `apps/client/app/modules/[id]` | 워크플로우 편집기 |
| `apps/client/app/shared/[urlSlug]` | 공개 WebApp 실행 화면 |
| `apps/client/app/embed/chat/[urlSlug]` | iframe 위젯/채팅 임베드 화면 |
| `apps/client/app/stream-api/workflows/[workflowId]/route.ts` | SSE 스트리밍용 Next API 프록시 |
| `apps/client/app/features/*` | 도메인별 API/컴포넌트/훅/스토어/타입 |
| `apps/client/lib/apiClient.ts` | 공통 Axios 클라이언트 |

## Gateway 구조

| 경로 | 역할 |
| --- | --- |
| `apps/gateway/main.py` | FastAPI 앱, CORS, 세션 미들웨어, `/api/v1` 라우터 등록 |
| `apps/gateway/api/api.py` | v1 라우터 묶음 |
| `apps/gateway/api/v1/endpoints/*.py` | 실제 API 엔드포인트 |
| `apps/gateway/auth` | JWT 쿠키 인증 의존성, Google OAuth 설정 |
| `apps/gateway/services` | 앱/워크플로우/배포/LLM/RAG/스케줄/스토리지 서비스 |
| `apps/gateway/services/ingestion` | 파일/API 문서 처리 orchestrator, parser, processor |
| `apps/gateway/core` | 설정, 보안 서비스 |
| `apps/gateway/static/widget.js` | 정적 위젯 파일 |

## Shared 구조

| 경로 | 역할 |
| --- | --- |
| `apps/shared/db/models` | SQLAlchemy 모델 |
| `apps/shared/db/session.py` | DB 엔진/세션 |
| `apps/shared/db/seed.py` | 개발 사용자, LLM provider/model seed |
| `apps/shared/alembic` | DB 마이그레이션 |
| `apps/shared/schemas` | Pydantic 스키마 |
| `apps/shared/celery_app.py` | Celery 앱/큐/Redis 설정 |
| `apps/shared/pubsub.py` | Redis Pub/Sub 유틸 |
| `apps/shared/connectors` | 외부 DB connector |
| `apps/shared/services/ingestion` | DB ingestion, vector store, chunker/transformer |
| `apps/shared/services/llm_client` | provider별 LLM client factory |
| `apps/shared/utils` | 암호화, 템플릿, prompt injection guard 등 |

## Workflow Engine 구조

| 경로 | 역할 |
| --- | --- |
| `apps/workflow_engine/main.py` | 워커 앱 진입점 |
| `apps/workflow_engine/tasks.py` | Celery 태스크: workflow 실행/스트리밍 |
| `apps/workflow_engine/workflow/core` | 엔진, 노드 팩토리, 엣지, 로거 |
| `apps/workflow_engine/workflow/nodes` | 노드별 실행 구현 |
| `apps/workflow_engine/services` | LLM, retrieval, sandbox, sync 서비스 |

## Sandbox 구조

| 경로 | 역할 |
| --- | --- |
| `apps/sandbox/main.py` | FastAPI 샌드박스 앱 |
| `apps/sandbox/api/v1/endpoints/execute.py` | 코드 실행/메트릭/헬스 API |
| `apps/sandbox/core` | executor, scheduler, bucket, history |
| `apps/sandbox/nsjail` | NSJail wrapper/config |
| `apps/sandbox/models` | job/result 모델 |

## 주의할 구조 차이

- README의 `apps/server` 또는 `cd docker` 전제와 현재 코드 구조가 일부 다르다.
- 현재 백엔드는 `apps/gateway`, `apps/workflow_engine`, `apps/log_system`, `apps/shared`, `apps/sandbox`로 분리되어 있다.
- `apps/client/node_modules`, `.next`, Python `.venv`, `__pycache__`가 저장소 안에 존재하지만 문서화 대상의 핵심 소스는 아니다.
