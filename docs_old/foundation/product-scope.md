# 제품 범위

Status: Draft
Authority: Foundation
Source of Truth: Yes
Verified Against: dev @ ec576b4f24155697aed8843acc6e5a3fc835f7e1

## 제품 정의

Nodease는 기존 Moduly의 workflow builder/runtime을 기반으로 LLMOps observability, RBAC, audit/tracing, RAG governance, enterprise operations를 단계적으로 강화하는 제품이다.

현재 코드와 배포 리소스의 런타임 명칭은 `Moduly`다. FastAPI title, Docker/Helm 리소스명, README 실행 명령, container name은 모두 Moduly 계열 이름을 사용한다. 따라서 이 문서에서 `Nodease`는 목표 제품명 또는 리브랜딩 명칭으로 취급하고, 코드/인프라 기준 식별자는 `Moduly`를 따른다.

## MVP 범위

| MVP | 범위 |
| --- | --- |
| MVP 1 | Foundation, RBAC/Organization, LLMOps observability |
| MVP 2 | Governance, RAG audit, data classification |
| MVP 3 | Optimization, deployment operations, enterprise dashboard |

상세 요구사항은 [requirements/](../requirements/README.md)를 따른다.

## 범위 규칙

- 현재 코드의 아키텍처와 물리 데이터 모델을 우선 보존한다.
- 큰 schema refactor보다 additive extension을 우선한다.
- RBAC, audit, tracing은 controller가 아니라 service/helper 경계에서 적용한다.
- secret value, credential 원문, API key, token, `encrypted_config` 값/content, raw payload는 문서와 로그에 노출하지 않는다. Column 이름과 현재 보안 TODO 설명은 허용한다. `credential_id` 같은 식별자는 권한 보호된 trace/API 응답 whitelist 안에서만 허용할 수 있다.
- 구현 계획은 source-of-truth 문서의 파생물로 취급한다.
