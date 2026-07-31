# 배포와 인프라

> Status: Reference-only historical document.
>
> 이 문서는 과거 `main` 브랜치 기준 역공학 산출물이다. 현재 구현 기준은 active `docs/` 문서와 코드를 따른다.

## 로컬 개발 스크립트

### setup

파일: `scripts/setup.sh`

동작:

- Python3, npm 존재 확인
- `apps/gateway`, `apps/log_system`, `apps/workflow_engine` 가상환경 생성
- shared package editable install
- 각 Python app editable install
- `apps/client`에서 `npm install`

### dev

파일: `scripts/dev.sh`

동작:

1. `dev/docker-compose.yml`로 Postgres, Redis, pgAdmin 시작
2. Sandbox build/start
3. Postgres/Redis/Sandbox readiness 대기
4. Log System Celery worker 시작
5. Workflow Engine Celery worker 시작
6. Gateway uvicorn 시작
7. Next.js client 시작

접속 URL:

- Gateway: `http://localhost:8000`
- API docs: `http://localhost:8000/docs`
- Frontend: `http://localhost:3000`
- Sandbox: `http://localhost:8194`
- pgAdmin: `http://localhost:5050`

주의:

- `dev.sh`는 macOS fork 안전성 문제 대응으로 Celery `-P solo`를 사용한다.

### test

파일: `scripts/test.sh`

동작:

- Gateway tests
- Workflow Engine tests
- Shared tests
- Sandbox tests
- Client `npm run build`

## 개발용 Compose

파일: `dev/docker-compose.yml`

서비스:

- `postgres`: pgvector/pgvector:pg15, 5432
- `redis`: redis:7-alpine, 6379
- `pgadmin`: 5050
- `sandbox`: 8194, privileged

## 전체 Docker Compose

파일: `docker/docker-compose.yml`

서비스:

| 서비스 | 역할 | 포트 |
| --- | --- | --- |
| `postgres` | PostgreSQL + pgvector | 5432 |
| `redis` | Celery broker/result, Pub/Sub | 6379 |
| `gateway` | FastAPI Gateway | Nginx 통해 접근 |
| `workflow_engine` | Celery workflow worker | 내부 |
| `log_system` | Celery log worker | 내부 |
| `frontend` | Next.js app | Nginx 통해 접근 |
| `sandbox` | NSJail code execution | 8194 |
| `nginx` | 단일 진입점 | 80 |
| `proxy` | Squid HTTP proxy | 3128 |

Nginx routing:

- `/` -> frontend:3000
- `/api` -> gateway:8000
- `/ws` -> gateway:8000
- `/health` -> plain OK

## 주요 환경변수

보안:

- `SECRET_KEY`
- `MASTER_KEY`
- `ENCRYPTION_KEY`

DB:

- `DB_HOST`
- `DB_PORT`
- `DB_USER`
- `DB_PASSWORD`
- `DB_NAME`

Redis:

- `REDIS_HOST`
- `REDIS_PORT`
- `REDIS_DB`
- `REDIS_BROKER_DB`
- `REDIS_BACKEND_DB`
- `REDIS_PASSWORD`
- `REDIS_URL`

Frontend:

- `NEXT_PUBLIC_API_URL`
- `API_URL`
- `NODE_ENV`

CORS/Cookie:

- `CORS_ORIGINS`
- `COOKIE_DOMAIN`

Storage:

- `STORAGE_TYPE`
- `AWS_ACCESS_KEY_ID`
- `AWS_SECRET_ACCESS_KEY`
- `AWS_REGION`
- `S3_BUCKET_NAME`

OAuth:

- `GOOGLE_CLIENT_ID`
- `GOOGLE_CLIENT_SECRET`

RAG:

- `LLAMA_CLOUD_API_KEY`

Sandbox:

- `SANDBOX_URL`
- `SANDBOX_ENABLE_NETWORK`
- `SANDBOX_MIN_WORKERS`
- `SANDBOX_MAX_WORKERS`
- `SANDBOX_WORKER_IDLE_TIMEOUT`
- `SANDBOX_DEFAULT_TIMEOUT`
- `SANDBOX_MAX_TIMEOUT`
- `SANDBOX_MAX_MEMORY_MB`
- `SANDBOX_MAX_OUTPUT_SIZE`
- `SANDBOX_MAX_QUEUE_SIZE`
- `SANDBOX_MAX_PER_TENANT`
- `SANDBOX_SCALING_INTERVAL`
- `SANDBOX_EMA_ALPHA`
- `SANDBOX_TARGET_RPS_PER_WORKER`
- `SANDBOX_SCALE_DOWN_COOLDOWN`
- `SANDBOX_SCALE_DOWN_IDLE_TIME`
- `SANDBOX_AGING_INTERVAL`
- `SANDBOX_AGING_THRESHOLD_LOW`
- `SANDBOX_AGING_THRESHOLD_NORMAL`
- `SANDBOX_QUEUE_CLEANUP_INTERVAL`
- `SANDBOX_QUEUE_IDLE_TIMEOUT`
- `SANDBOX_NSJAIL_PATH`
- `SANDBOX_NSJAIL_CONFIG_PATH`
- `SANDBOX_PYTHON_PATH`
- `SANDBOX_TEMP_DIR`
- `SANDBOX_FORCE_FIFO`

## Helm / Kubernetes

파일: `infra/helm/moduly/values.yaml`

구성:

- gateway replica 2
- worker replica 4
- logger replica 1
- frontend replica 2
- sandbox replica 2
- PostgreSQL Bitnami chart 활성화, pgvector image 사용
- Redis standalone 활성화
- ingress 기본 비활성
- sandbox network policy 활성

이미지 repository:

- `ghcr.io/jungle-scope/moduly-gateway`
- `ghcr.io/jungle-scope/moduly-workflow-engine`
- `ghcr.io/jungle-scope/moduly-logger`
- `ghcr.io/jungle-scope/moduly-client`
- `ghcr.io/jungle-scope/moduly-sandbox`

주의:

- `values.yaml`의 secrets 값은 예시값이며 운영에서는 override해야 한다.
- Gateway/worker/frontend의 API URL은 release name 기반 Kubernetes service DNS를 사용한다.

## Terraform / K8s manifests

확인된 경로:

- `infra/terraform`: EKS/VPC/IAM/provider/output 관련 tf 파일
- `infra/k8s`: namespace별 deployment, ingress, storageclass

세부 값은 별도 인프라 리뷰 문서가 필요할 수 있다.

## GitHub Actions

확인된 workflow:

- `deploy-dev-namespace.yml`
- `deploy-eks-frontend.yml`
- `deploy-eks-gateway.yml`
- `deploy-eks-logger.yml`
- `deploy-eks-sandbox.yml`
- `deploy-eks-worker.yml`
- `publish-images.yml`

## 주의/검증 필요

- README의 Helm 설치 경로 안내 일부가 실제 루트 구조와 다를 수 있다.
- `apps/client/package.json`의 `dev:all`은 현재 백엔드 구조와 맞지 않는다.
- 운영 배포 시 DB migration 실행 책임이 Helm/entrypoint/Gateway lifespan 중 어디에 있는지 명확화가 필요하다.
