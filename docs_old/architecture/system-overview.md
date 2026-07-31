# 시스템 개요

Status: Draft
Authority: Architecture
Source of Truth: Yes
Verified Against: dev @ ec576b4f24155697aed8843acc6e5a3fc835f7e1

## 논리 서비스

| 구성요소 | 책임 |
| --- | --- |
| Client | Workflow 편집, 설정, RBAC/observability UI |
| Gateway | 인증된 API와 resource permission enforcement 경계 |
| Workflow Engine | Workflow 실행과 node runtime |
| Sandbox | 격리된 코드 실행 |
| Shared | DB model, schema, 공통 service, tracing/audit utility |
| PostgreSQL | 영속 상태 저장 |
| Redis/Celery | 비동기 task queue와 실행 조율 |

## 배포 런타임 구성

Full container stack 기준 Docker Compose 구성은 `docker/docker-compose.yml`을 따른다. 로컬 개발 스크립트 `scripts/dev.sh`는 이 파일이 아니라 `dev/docker-compose.yml`로 PostgreSQL, Redis, pgAdmin, Sandbox만 Compose로 띄우고 Gateway, worker, client는 host process로 실행한다.

| 구성요소 | 실제 서비스명/역할 |
| --- | --- |
| `postgres` | pgvector 기반 PostgreSQL |
| `redis` | Celery broker와 Pub/Sub |
| `gateway` | FastAPI Gateway. 내부 8000 port, Nginx를 통해 접근 |
| `workflow_engine` | Celery workflow worker |
| `log_system` | audit/trace/log 계열 Celery worker |
| `frontend` | Next.js client |
| `sandbox` | NSJail 기반 code execution sandbox. 기본 8194 port |
| `nginx` | `/`, `/api`, `/ws` reverse proxy 및 `/health` 단순 health endpoint |
| `proxy` | Squid forward proxy |

Helm 기준 구성은 `infra/helm/moduly/templates/*`를 따른다. 주요 workload는 `gateway`, `worker`, `logger`, `frontend`, `sandbox`이며, chart dependency로 PostgreSQL, Redis, `ingress-nginx`를 사용한다. Ingress와 Sandbox NetworkPolicy도 Helm template에 포함된다.

## 시작/초기화 규칙

- Docker Gateway entrypoint는 PostgreSQL readiness를 기다린 뒤 `CREATE EXTENSION IF NOT EXISTS vector`와 `alembic upgrade head`를 실행하고 Uvicorn을 시작한다.
- Gateway lifespan도 시작 시 audit listener 등록, pgvector extension 생성, `Base.metadata.create_all()`, default user/provider/model seed, model pricing sync, SchedulerService 초기화를 수행한다.
- Alembic과 `create_all()`이 함께 존재하므로 운영 schema 전략은 [implementation-plan/risk-consistency-verification.md](../implementation-plan/risk-consistency-verification.md)의 리스크 항목으로 추적한다.
- Gateway `/api/v1/health`는 DB `SELECT 1`까지 확인하고, Nginx `/health`는 reverse proxy 자체의 단순 200 OK endpoint다.

## 경계 규칙

- Gateway endpoint는 비즈니스 판단을 service/helper layer에 위임한다.
- Workflow Engine은 가능한 경우 user, organization, workflow, run, node 식별자를 포함한 execution context를 받아야 한다.
- 공통 tracing/audit service가 trace 접근과 payload 처리의 경계다.
- Secret 값은 client 응답으로 반환하거나 log에 기록하지 않는 것이 원칙이다. 단, 현재 deployment 생성 응답은 `auth_secret` 원문을 포함할 수 있어 보안 정렬 대상이다.

이전 Moduly 역공학 과정에서 정리한 상세 runtime 사실은 참조용이다: [references/moduly-architecture/](../references/moduly-architecture/README.md).
