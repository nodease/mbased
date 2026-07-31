# Moduly 역공학 문서 인덱스

> Status: Reference-only historical document.
>
> 이 문서는 과거 `main` 브랜치 기준으로 작성된 역공학 참고 문서다. 본문에 등장하는 `local/docs` 경로는 당시 산출물 위치를 뜻하며, 현재 active documentation root인 `docs/`의 source of truth를 의미하지 않는다. 구현 기준은 상위 [문서 인덱스](../../README.md)와 active 문서를 따른다.

작성 기준: `main` 브랜치, 프로젝트 루트 `/Users/hong-yoonki/Desktop/krafton/final-project/mbased`

이 문서는 기존 코드와 설정을 근거로 프로젝트를 역공학해 정리한 로컬 문서입니다. 공식 요구사항 문서가 아니라, 현재 구현체에서 확인 가능한 사실을 재구성한 문서입니다.

## 문서 구조

| 문서 | 목적 |
| --- | --- |
| [00-documentation-method.md](./00-documentation-method.md) | 역공학 방법, 근거 수준, 문서 읽는 법 |
| [01-project-overview.md](./01-project-overview.md) | 제품/기술 스택/서비스 경계 개요 |
| [sections/02-repository-map.md](./sections/02-repository-map.md) | 폴더 구조와 책임 |
| [sections/03-runtime-architecture.md](./sections/03-runtime-architecture.md) | 런타임 구성, 서비스 간 통신, 실행 흐름 |
| [sections/04-frontend-screens.md](./sections/04-frontend-screens.md) | Next.js 화면/라우트/주요 API 호출 |
| [sections/05-api-spec.md](./sections/05-api-spec.md) | Gateway/Sandbox API 엔드포인트 목록 |
| [sections/06-data-model.md](./sections/06-data-model.md) | DB 모델, 관계, 상태값 |
| [sections/07-workflow-engine.md](./sections/07-workflow-engine.md) | 워크플로우 엔진과 노드 타입 |
| [sections/08-rag-knowledge.md](./sections/08-rag-knowledge.md) | 지식베이스/RAG/문서 처리 흐름 |
| [sections/09-auth-security.md](./sections/09-auth-security.md) | 인증, 쿠키, 암호화, 권한 규칙 |
| [sections/10-deployment-infra.md](./sections/10-deployment-infra.md) | Docker, 개발 스크립트, Helm/K8s, CI |
| [sections/11-tests-quality.md](./sections/11-tests-quality.md) | 테스트/벤치마크/품질 확인 방법 |
| [appendix/12-evidence-index.md](./appendix/12-evidence-index.md) | 주요 사실별 근거 파일 |
| [appendix/13-clarity-audit.md](./appendix/13-clarity-audit.md) | 1차 문서화 후 명확성/왜곡 점검 결과 |

## 핵심 요약

Moduly는 드래그 앤 드롭 방식으로 AI 워크플로우를 만들고 실행/배포하는 플랫폼입니다. 구현은 Next.js 클라이언트, FastAPI Gateway, Celery 기반 Workflow Engine, NSJail 기반 Sandbox, Log System, Shared 패키지, PostgreSQL/pgvector, Redis, Docker/Kubernetes 인프라로 나뉩니다.

주요 기능군은 다음과 같습니다.

- 이메일/비밀번호 및 Google OAuth 로그인
- 앱 생성, 수정, 삭제, 마켓 공개, 마켓 앱 복제
- React Flow 기반 워크플로우 편집, 초안 저장, 테스트 실행, SSE 스트리밍
- API, WebApp, Widget, MCP, Workflow Node, Schedule, Webhook 타입의 배포 모델
- LLM Provider/Credential/Model 관리, 모델 가격/사용량 집계
- 파일/API/외부 DB 기반 지식베이스 생성, 문서 처리, 벡터 검색, RAG 테스트
- Python 코드 노드 실행을 위한 Sandbox 서비스
- 실행 로그, 노드 로그, 통계/모니터링 화면

## 근거 수준 표기

- 확정: 코드, 설정 파일, 스키마, 라우트에서 직접 확인됨.
- 추정: 코드에서 의도는 보이나 실제 런타임 검증이 필요한 부분.
- 주의: 코드와 문서/주석/환경 설정 간 차이가 있거나, 미구현/레거시 흔적이 있는 부분.

## 주요 주의사항

- `README.md`의 일부 명령은 현재 폴더 구조와 다를 수 있습니다. 예를 들어 README에는 `cd docker` 후 `cd infra/helm/moduly`가 나오지만 실제 `infra/helm/moduly`는 프로젝트 루트 아래에 있습니다.
- `apps/client/package.json`의 `dev:all`은 `../server`를 참조하지만 현재 저장소에는 `apps/server`가 아니라 `apps/gateway`, `apps/workflow_engine` 등이 존재합니다.
- `apps/workflow_engine/tasks.py`의 `execute_deployed_workflow`는 `WorkflowDeployment.workflow_id`, `deployment.graph_data`를 참조하지만 현재 모델에는 해당 필드가 없습니다. 실제 사용 경로는 `workflow.execute` 및 `workflow.execute_by_deployment` 쪽으로 보입니다.
- LLM 가격 상수에는 현재 날짜 이후처럼 보이는 모델명이 포함되어 있습니다. 외부 가격 정확성은 검증하지 않았고, 문서는 코드의 현재 상태만 기록합니다.
