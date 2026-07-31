# 테스트와 품질

> Status: Reference-only historical document.
>
> 이 문서는 과거 `main` 브랜치 기준 역공학 산출물이다. 현재 구현 기준은 active `docs/` 문서와 코드를 따른다.

## 테스트 실행 스크립트

파일: `scripts/test.sh`

실행 범위:

1. Gateway service tests
2. Workflow Engine service tests
3. Shared/unit tests
4. Sandbox service tests
5. Client app build

명령:

```bash
./scripts/test.sh
```

## Gateway tests

경로:

- `apps/gateway/tests/api/test_webhook_api.py`
- `apps/gateway/tests/services/test_knowledge_service.py`
- `apps/gateway/tests/services/test_llm_client_openai.py`

주제:

- Webhook API
- Knowledge service
- OpenAI LLM client

## Workflow Engine tests

경로:

- `apps/workflow_engine/tests/integration/test_workflow_integration.py`
- `apps/workflow_engine/tests/nodes/*`
- `apps/workflow_engine/tests/services/test_sync_service.py`

주제:

- Answer node
- Code node
- Condition node
- GitHub node
- HTTP node
- LLM runtime
- Mail node
- NodeFactory
- Parallel execution optimization
- Start node
- Webhook node
- Workflow engine
- Timeout/error cases
- Sub workflow node
- Sync service

## Sandbox tests

경로:

- `apps/sandbox/tests/test_scheduler.py`

주제:

- Sandbox scheduler

## Shared tests

경로:

- `apps/shared/tests/integration/test_vector_store_workflow.py`
- `apps/shared/tests/services/test_vector_store_service.py`
- `apps/shared/tests/manual/*`

주제:

- Vector store workflow/service
- Manual debug/verification scripts

## RAG evaluation

경로:

- `tests/evaluation`

목적:

- Retrieval-Augmented Generation 검색 성능 평가
- Recall@K, Precision@K, Hit@K, MRR, NDCG@K

주요 파일:

- `rag_metrics.py`
- `rag_evaluator.py`
- `test_rag_baseline.py`
- `prepare_datasets.py`
- `index_documents.py`
- `run_benchmark.py`

주의:

- README에는 `cd apps/server` 경로가 나오지만 현재 구조에는 `apps/server`가 없다. 실행 경로 수정이 필요할 수 있다.

## Load tests

경로:

- `tests/load`

도구:

- Locust
- psutil
- pandas
- matplotlib

환경변수:

- `LOAD_TEST_DEPLOYMENT_SLUG`
- `LOAD_TEST_AUTH_TOKEN`
- `LOAD_TEST_DEPLOYMENT_SLUG_1`, `LOAD_TEST_AUTH_TOKEN_1`
- `LOAD_TEST_DEPLOYMENT_SLUG_2`, `LOAD_TEST_AUTH_TOKEN_2`
- `LOAD_TEST_DEPLOYMENT_SLUG_3`, `LOAD_TEST_AUTH_TOKEN_3`
- `TEST_TENANT_COUNT`
- `TEST_FAST_WEIGHT`, `TEST_SLOW_WEIGHT`, `TEST_HEAVY_WEIGHT`
- `SANDBOX_FORCE_FIFO`

목적:

- 배포된 워크플로우 실행 API 부하 테스트
- Sandbox scheduler 부하 테스트
- CPU/메모리 모니터링
- 결과를 `HISTORY.md`와 reports 폴더에 기록

## Client tests

`apps/client/package.json`

스크립트:

- `npm run lint`
- `npm run test`
- `npm run build`

테스트 도구:

- Vitest
- Testing Library
- jsdom

확인된 테스트 파일:

- `useAuthRedirect.test.ts`
- `ChunkPreviewList.test.tsx`
- `useAutoSync.test.ts`
- `useWorkflowStore.test.ts`

## 문서화 중 실행 여부

이번 문서화 사이클에서는 테스트를 실행하지 않았다. 목적이 코드 변경 검증이 아니라 현재 구현체 역공학이기 때문이다. 다만 문서의 명확성 검증을 위해 파일 존재 여부, 라우트 정적 추출, 모델/서비스 코드 대조는 수행했다.

## 권장 품질 게이트

문서와 코드 간 정합성을 유지하려면 다음을 주기적으로 실행하는 것이 좋다.

```bash
./scripts/test.sh
```

추가 권장:

```bash
cd apps/client
npm run lint
npm run test
```

```bash
cd apps/workflow_engine
.venv/bin/python -m pytest tests
```

```bash
cd apps/gateway
.venv/bin/python -m pytest tests
```

## 테스트 공백

- API 전체 OpenAPI schema snapshot test는 보이지 않는다.
- DB migration 정합성 test는 제한적으로 보인다.
- End-to-end browser test는 보이지 않는다.
- OAuth, cookie domain, production secure cookie 정책은 런타임 환경별 검증이 필요하다.
- Sandbox NSJail 실제 격리 강도는 별도 보안 테스트가 필요하다.
