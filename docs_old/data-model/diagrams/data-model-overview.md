# 데이터 모델 개요 다이어그램

Status: Draft
Authority: Data Model Diagram
Source of Truth: No
Verified Against: dev @ 860ece0dee7cab3925d27f30ea650baf0cb18b4e (PR #138 docs target, 2026-07-01 KST)

이 문서는 [physical-data-model.md](../physical-data-model.md)의 현재 코드 table 참조관계를 시각화한 보조 문서다. 구현 기준은 Mermaid 다이어그램이 아니라 물리 데이터 모델 문서의 table, column, relationship 설명이다.

`organization_memberships`는 현재 organization scope와 manager 판정의 우선 기준이다. Team permission 계산에는 `team_memberships`를 계속 사용한다. Organization member/invitation BE API는 구현됐고, full membership 관리 UI는 후속 범위다. `user_knowledge_permissions`, `user_audit_permissions`는 MVP 2/3 planned additive table이므로 현재 코드 기준 다이어그램에서는 제외한다.

```mermaid
erDiagram
  users ||--o{ organization : creates_manages
  organization ||--o{ teams : owns
  organization ||--o{ organization_memberships : has_members
  organization ||--o{ apps : scopes
  organization ||--o{ workflows : scopes
  organization ||--o{ knowledge_bases : scopes
  organization ||--o{ llm_credentials : scopes
  organization ||--o{ llm_usage_logs : scopes

  users ||--o{ organization_memberships : joins_orgs
  users ||--o{ organization_memberships : invites
  users ||--o{ team_memberships : joins
  teams ||--o{ team_memberships : has_members
  teams ||--o{ team_workflow_permissions : grants
  teams ||--o{ team_knowledge_permissions : grants
  teams ||--o{ team_llm_permissions : grants
  teams ||--o{ team_audit_permissions : grants
  users ||--o{ user_workflow_permissions : direct_grant
  users ||--o{ user_llm_permissions : direct_grant

  apps ||--o{ workflows : has
  apps ||--o{ workflow_deployments : deploys
  apps ||--o{ workflow_runs : runs

  workflows ||--o{ workflow_runs : runs
  workflows ||--o{ llm_usage_logs : logs
  workflows ||--o{ team_workflow_permissions : authorized_by
  workflows ||--o{ user_workflow_permissions : authorized_by

  workflow_deployments ||--o| schedules : may_have
  workflow_deployments ||--o{ workflow_runs : runs

  workflow_runs ||--o{ workflow_node_runs : has
  workflow_runs ||--o{ trace_payloads : stores
  workflow_runs ||--o{ trace_payload_access_events : audited_by
  workflow_runs ||--o{ llm_usage_logs : records

  workflow_node_runs ||--o{ trace_payloads : stores

  knowledge_bases ||--o{ documents : contains
  knowledge_bases ||--o{ document_chunks : denormalizes
  knowledge_bases ||--o{ team_knowledge_permissions : authorized_by
  documents ||--o{ document_chunks : contains

  llm_providers ||--o{ llm_models : provides
  llm_providers ||--o{ llm_credentials : has
  llm_credentials ||--o{ llm_rel_credential_models : enables
  llm_models ||--o{ llm_rel_credential_models : enabled_by
  llm_credentials ||--o{ llm_usage_logs : logs
  llm_credentials ||--o{ team_llm_permissions : authorized_by
  llm_credentials ||--o{ user_llm_permissions : authorized_by
  llm_models ||--o{ llm_usage_logs : logs

  users ||--o{ audit_logs : acts
```
