# 용어집

Status: Draft
Authority: Foundation
Source of Truth: Yes
Verified Against: dev @ ec576b4f24155697aed8843acc6e5a3fc835f7e1

| 용어 | 의미 |
| --- | --- |
| App | 제품상 project boundary. 기존 `apps` table을 사용한다. |
| Canvas | 제품상 workflow editing surface. 기존 `workflows`를 사용한다. |
| Organization | 조직 범위와 tenant-like boundary. 기존 `organization` table을 사용한다. |
| OrganizationMembership | User가 Organization에 직접 소속되어 있다는 1차 관계. 현재 조직 소속, active organization scope, organization manager/member 판정의 기준은 `organization_memberships` table이다. |
| Team | Organization 안의 권한 부여 단위. 기존 `teams` table을 사용한다. |
| TeamMembership | User가 Organization 안의 Team에 배정되어 있다는 관계. `team_memberships`는 team resource permission 계산을 위한 team 배정 관계이며, organization 소속의 기준이 아니다. 과거 team membership으로 organization 소속을 간접 표현하던 데이터는 `organization_memberships` backfill/legacy fallback 설명으로만 다룬다. |
| Team permission | Resource별 team 권한. `team_workflow_permissions`, `team_knowledge_permissions`, `team_llm_permissions`, `team_audit_permissions`를 사용한다. |
| User direct permission | Team 권한으로 처리하기 어려운 user별 additive allow 예외 권한. 현재 코드는 workflow와 LLM credential에 대해 `user_workflow_permissions`, `user_llm_permissions`를 구현한다. |
| auth_state | DB enum이 아닌 application-level permission state string. 현재 코드는 `none`, `viewer`, `operator`, `builder`, `manager`, `auditor`, `raw_auditor`와 legacy `read/write/execute/admin` compatibility mapping을 사용한다. |
| Audit | 사용자 action과 data change 기록. MVP 목표 상태에서는 `audit_logs`를 canonical table로 사용한다. |
| Trace payload | raw/redacted payload 분리 저장 구조. MVP 목표 상태에서는 `trace_payloads`와 `trace_payload_access_events`를 사용한다. |
