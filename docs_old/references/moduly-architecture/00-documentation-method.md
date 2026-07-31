# 문서화 방법

> Status: Reference-only historical document.
>
> 이 문서는 과거 `main` 브랜치 기준 역공학 작업의 방법 기록이다. 본문에 등장하는 `local/docs`는 당시 산출물 경로이며, 현재 active documentation root인 `docs/`의 source of truth를 뜻하지 않는다.

## 목표

현재 `main` 브랜치의 구현체를 기준으로 프로젝트의 기능, 구조, 데이터 모델, API, 화면, 실행 흐름, 인프라, 테스트 정보를 역공학해 `local/docs` 아래에 문서화한다.

## 조사 방식

1. 브랜치와 작업트리 확인
   - 현재 브랜치: `main`
   - 기존 미추적 파일: `.agents/`, `docs/service_ad_video_scenarios.md`, `docs/tmp_ad_video_scenarios.md`
   - 위 파일들은 사용자 또는 기존 작업물로 간주하고 수정하지 않았다.

2. 구조 단서 수집
   - `README.md`
   - `apps/*/pyproject.toml`
   - `apps/client/package.json`
   - `docker`, `dev`, `infra`, `scripts`, `tests`

3. 기능 단서 수집
   - FastAPI 라우터 데코레이터
   - Next.js `page.tsx`와 feature API 래퍼
   - SQLAlchemy 모델과 Pydantic 스키마
   - Workflow Engine `NodeFactory`와 노드 구현
   - RAG/ingestion/storage 서비스

4. 문서 분기 기준
   - 사람이 읽을 때 큰 단위로 이해하기 쉬운 책임 경계
   - AI가 필요한 부분만 다시 읽어도 충분한 독립성
   - API, DB, 프론트 화면처럼 표가 많은 정보는 별도 파일로 분리

5. 감사 기준
   - 근거가 있는 내용과 추정을 분리했는가
   - README와 코드가 다를 때 코드 기준으로 적었는가
   - 문서 간 용어가 일관되는가
   - 누락되기 쉬운 레거시/주의사항을 표시했는가

## 근거로 사용한 대표 파일

- `README.md`
- `apps/client/app/**`
- `apps/client/lib/apiClient.ts`
- `apps/gateway/main.py`
- `apps/gateway/api/api.py`
- `apps/gateway/api/v1/endpoints/*.py`
- `apps/gateway/services/*.py`
- `apps/shared/db/models/*.py`
- `apps/shared/schemas/*.py`
- `apps/shared/celery_app.py`
- `apps/shared/pubsub.py`
- `apps/workflow_engine/tasks.py`
- `apps/workflow_engine/workflow/core/*.py`
- `apps/workflow_engine/workflow/nodes/**`
- `apps/sandbox/**`
- `apps/log_system/tasks.py`
- `docker/docker-compose.yml`
- `dev/docker-compose.yml`
- `infra/helm/moduly/values.yaml`
- `scripts/*.sh`

## 문서화 원칙

- 현재 코드에 없는 제품 의도는 쓰지 않는다.
- README나 주석의 문구가 코드와 다르면 코드 우선으로 기록한다.
- 런타임 실행이 필요한 사실은 “추정” 또는 “검증 필요”로 표시한다.
- 보안상 실제 `.env` 내용은 문서화하지 않고 예시 파일과 코드의 환경변수 사용처만 문서화한다.
