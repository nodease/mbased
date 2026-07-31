# 명확성/왜곡 감사

> Status: Reference-only historical document.
>
> 이 문서는 과거 `main` 브랜치 기준 역공학 문서의 검증 기록이다. 현재 active source of truth 검증 결과가 아니다.

감사 일시: 2026-06-26

목표: 1차 문서화 후 문서가 명확한지, 코드와 다른 왜곡이 없는지 점검하고 보정한다.

## 요구사항 점검

| 요구사항 | 상태 | 근거 |
| --- | --- | --- |
| 프로젝트 루트 기준 `./local/` 디렉터리 생성 | 충족 | `local/docs` 생성 |
| `./local/docs`에 문서화 | 충족 | 문서 인덱스와 섹션 파일 생성 |
| 필요 시 문서 분기 | 충족 | overview, repository, runtime, frontend, API, DB, workflow, RAG, auth, infra, tests, appendix로 분기 |
| 분기한 정보를 담는 문서 작성 | 충족 | `README.md`에 문서 구조와 목적 표기 |
| main 브랜치 기준 | 충족 | 작업 시작 시 `git branch --show-current`가 `main` |
| 역공학 기반 문서화 | 충족 | API/DB/서비스/프론트/인프라 파일 직접 확인 |
| 문서화 후 명확성/왜곡 점검 | 충족 | 이 감사 문서 작성 및 보정 사항 반영 |

## 왜곡 방지 점검

| 점검 항목 | 결과 |
| --- | --- |
| README 내용만 믿고 쓰지 않았는가 | 코드/설정과 대조했고 불일치 항목을 주의사항으로 기록 |
| API 목록이 누락되지 않았는가 | FastAPI 데코레이터를 정적으로 추출해 route table 작성 |
| DB 모델을 실제 파일 기준으로 기록했는가 | `apps/shared/db/models/*.py` 기준으로 작성 |
| 프론트 화면을 실제 `page.tsx` 기준으로 기록했는가 | `find apps/client/app -path '*/page.tsx'` 결과 기준 |
| 노드 타입을 UI와 엔진 모두 기준으로 기록했는가 | `nodeRegistry.tsx`와 `NodeFactory`를 모두 반영 |
| 추정과 확정을 구분했는가 | 각 문서에 주의/검증 필요 섹션 작성 |
| 보안상 민감한 `.env` 값을 노출하지 않았는가 | 실제 `.env` 내용은 읽지 않고 예시/코드 사용처만 문서화 |

## 보정한 항목

1. README에는 `apps/server` 계열 경로가 보이나 실제 구조에는 없으므로 문서에 주의사항으로 표시했다.
2. `apps/client/package.json`의 `dev:all`이 `../server`를 참조하는 점을 주의사항으로 표시했다.
3. `execute_deployed_workflow`가 현재 모델과 맞지 않는 필드를 참조하는 점을 Workflow Engine 문서와 README 주의사항에 표시했다.
4. `pluginNode`는 UI registry에 있으나 실제 실행 미구현임을 명시했다.
5. `LLMUsageLog.latency_ms`의 실제 컬럼명이 `atency_ms`로 보이는 점을 DB 문서에 표시했다.
6. LLM 가격 상수는 외부 최신 가격으로 검증하지 않고 코드 상태로만 기록한다고 표시했다.
7. Sandbox 최상위 `/`, `/health` 라우트와 v1 라우트를 구분해 API 문서에 보강했다.
8. `knowledgeApi.updateKnowledgeBase()` 클라이언트 타입과 백엔드 204 응답의 불일치를 표시했다.
9. 코드에서 실제 읽는 Sandbox 세부 환경변수와 `CORS_ORIGINS`를 인프라 문서에 보강했다.
10. 프론트 테스트 목록에 `ChunkPreviewList.test.tsx`를 추가했다.
11. `/run/{url_slug}` 인증 header와 Webhook 인증 방식을 API/아키텍처 문서에 구체화했다.
12. `DeploymentService.run_deployment()`가 `trigger_mode` 인자를 받지만 실행 context에는 `"app"`을 고정하는 점을 주의사항으로 추가했다.
13. `tests/load`의 numbered load-test 환경변수와 Sandbox load-test 환경변수를 테스트 문서에 보강했다.
14. Webhook/Scheduler execution context와 `log.create_run`의 `RunTriggerMode` 정규화가 다를 수 있는 점을 런타임/데이터 모델/워크플로우 문서에 추가했다.
15. DB 문서에서 `metadata`, `atency_ms`처럼 DB 컬럼명과 SQLAlchemy 속성명이 다른 필드를 명시했다.

## 2026-06-27 재검증

목표: `./local/docs`의 문서화 내용이 현재 코드와 일치하는지, 문서 간 모순이 없는지 재검사했다.

자동 정적 대조 결과:

```json
{
  "docs": 15,
  "doc_lines": 2555,
  "routes": 78,
  "gateway_routes": 75,
  "sandbox_v1_routes": 3,
  "sandbox_top_routes_checked": 2,
  "pages": 17,
  "node_factory_keys": 16,
  "ui_node_types": 17,
  "db_tables": 16,
  "client_tests": 4,
  "python_tests": 26,
  "envs": 48,
  "load_envs": 11,
  "issues": []
}
```

수동 대조 항목:

- `run.py`, `webhook.py`, `deployment_service.py` 기준 배포 실행, 공개 실행, Webhook 실행 흐름 확인
- `workflow.py`, `tasks.py`, `workflow_engine.py`, `webhook_node.py` 기준 SSE 실행과 Webhook payload mapping 위치 확인
- `main.py` 기준 Sandbox root `/`, `/health`, `/v1/sandbox/*` 라우트 구분 확인
- `useGlobalStats.ts`, `useGlobalLogs.ts`, `statistics/page.tsx` 기준 통계 화면이 앱 목록과 워크플로우별 실행/통계 API를 합산하는 구조임을 확인
- `apps/shared/db/models/*.py` 기준 실제 SQLAlchemy table 16개와 DB 문서의 테이블 목록 확인
- `.venv`, `node_modules`, `__pycache__`를 제외한 테스트 파일 목록과 테스트 문서 확인
- 문서 내부 markdown link와 backtick 경로 참조가 실제 저장소 경로와 맞는지 확인

이번 재검증에서 보정한 항목:

- 누락된 Client 테스트 `ChunkPreviewList.test.tsx` 추가
- 코드에서 실제 읽는 Sandbox 세부 환경변수와 `CORS_ORIGINS` 추가
- `/run/{url_slug}`와 Webhook 인증 방식 상세화
- `DeploymentService.run_deployment()`의 `trigger_mode` 고정 동작을 주의사항으로 기록
- `tests/load/load1.py`, `load2.py`, `load3.py`, `sandbox_locust.py`의 환경변수 목록 보강
- `apps/log_system/tasks.py`가 `webhook`, `schedule`, `scheduler` trigger 문자열을 명시적으로 매핑하지 않는 점 기록
- `document_chunks.metadata`/`metadata_`, `llm_models.metadata`/`model_metadata`, `llm_usage_logs.atency_ms`/`latency_ms` 구분 기록

검증 범위:

- 정적 route/model/page/env/test 추출과 소스 레벨 수동 대조까지 완료했다.
- 전체 테스트 실행, OpenAPI 런타임 schema 생성, 브라우저 E2E 검증은 수행하지 않았다.

## 남은 검증 필요

아래는 문서 왜곡이라기보다 런타임 확인이 필요한 항목이다.

- 실제 OpenAPI schema 생성 결과와 정적 route table 비교
- 전체 `./scripts/test.sh` 실행
- Next.js 화면별 E2E navigation 검증
- OAuth production cookie domain 동작 검증
- Sandbox NSJail 격리 정책 검증
- DB migration과 Gateway `create_all` 병행 정책 검증
- RAG ingestion import path가 모든 실행 방식에서 정상인지 검증

## 최종 판단

현재 문서는 “코드에서 확인 가능한 사실”과 “주의/검증 필요”를 분리해 기록하고 있다. 제품 요구사항 문서로 바로 쓰기보다는, 다음 단계의 정식 요구사항/기능 명세/API 명세 초안으로 사용할 수 있는 역공학 기반 시스템 지도에 가깝다.
