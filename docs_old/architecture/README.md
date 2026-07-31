# 아키텍처

Status: Draft
Authority: Architecture
Source of Truth: Yes
Verified Against: dev @ ec576b4f24155697aed8843acc6e5a3fc835f7e1

아키텍처 문서는 서비스 경계, 런타임 흐름, 보안/RBAC 적용 위치를 정의한다.

| 문서 | 역할 |
| --- | --- |
| [system-overview.md](system-overview.md) | 상위 서비스 구조와 책임 |
| [auth-rbac.md](auth-rbac.md) | 인증, organization context, RBAC enforcement 위치 |
| [knowledge-rag.md](knowledge-rag.md) | Knowledge/RAG metadata, permission, hierarchy, trace 경계 |
| [tracing-audit.md](tracing-audit.md) | audit/tracing 경계와 raw payload 접근 원칙 |

## 권위

- Controller에 비즈니스 로직을 넣지 않는다.
- Controller에서 DB를 직접 상세 조회해 권한 판단을 하지 않는다.
- RBAC, tracing access, audit recording은 service/helper 경계에서 수행한다.
- DB schema의 최종 기준은 [data-model/](../data-model/README.md)를 따른다.

현재 구현 예외:

- 일부 Gateway endpoint에는 과도기 controller/service-local helper 권한 판단이 남아 있다. Team 관리 API는 권한 판정과 team/team member 조회를 `TeamService`로 이관했지만, user directory와 permission management 계열 API는 router/service 경계에서 DB query와 permission helper를 직접 조합한다.
- 따라서 위 원칙은 목표 architecture rule이며, 현재 코드와 맞출 때는 각 API 문서의 "현재 구현 세부사항"을 함께 확인한다.
