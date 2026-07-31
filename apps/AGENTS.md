# AGENTS.md - apps

상위 `AGENTS.md`를 함께 따른다. 이 파일은 `apps/` 아래 서비스 코드 리뷰 기준을 보강한다.

## Review guidelines

- Gateway, Workflow Engine, Log System, Sandbox, Shared 변경은 service boundary와 호출 흐름이 깨지지 않는지 확인한다.
- Gateway endpoint는 인증/권한 의존성, 요청 검증, service 호출, 응답 형식만 담당하는지 확인한다.
- 권한 판단은 프론트 UI나 caller 신뢰에 의존하지 않고 Gateway/API와 실행 경로에서 모두 강제되는지 확인한다.
- 제품 시연 핵심 흐름인 workflow 생성/저장/실행/배포, RAG 답변, LLM credential 사용, audit/trace 조회를 깨뜨리는 서비스 변경은 P0/P1 후보로 본다.
- `apps/shared/` 변경은 Gateway, Workflow Engine, Log System, Sandbox에 미치는 import, schema, DB session, model compatibility 영향을 확인한다.
- Celery task, workflow execution, schedule, pub/sub, distributed lock 변경은 retry, idempotency, timeout, duplicate processing, race condition을 확인한다.
- SQLAlchemy model/query 변경은 transaction scope, lazy/eager loading, N+1, nullable/default, cascade, migration 필요성을 확인한다.
- audit/tracing/logging 변경은 organization, user, workflow, run, node 식별자가 유지되는지와 민감 데이터가 남지 않는지 확인한다.
- LLM/RAG/credential 경로 변경은 provider error handling, token/secret masking, retrieval metadata 보존, chunk lineage 영향을 확인한다.
- sandbox/code execution 변경은 input validation, resource limit, filesystem/network boundary, execution result sanitization을 확인한다.
- 운영 스크립트, Docker, infra와 맞물린 변경은 로컬 개발 명령과 배포 리소스명이 문서와 충돌하지 않는지 확인한다.
