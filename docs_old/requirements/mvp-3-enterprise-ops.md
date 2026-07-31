# MVP 3: 최적화 및 엔터프라이즈 운영

Status: Draft
Authority: Requirements
Source of Truth: Yes
Verified Against: dev @ ec576b4f24155697aed8843acc6e5a3fc835f7e1
Related ADRs: [ADR-202606290131-audit-action-naming-standard](../decisions/ADR-202606290131-audit-action-naming-standard.md)

## 목표

MVP 3는 MVP 1, 2에서 쌓은 실행/비용/권한/RAG/audit 데이터를 운영자가 실제로 활용할 수 있는 형태로 만든다.

결과물:

```text
기업용 AI Workflow / LLMOps 운영 MVP
  + 비용 최적화 추천
  + 배포 전 체크
  + workflow version diff
  + operations dashboard
  + trigger mode 정합성
  + 배포 재현성
```

## 재사용/의존 기반

| 기반 | 재사용 방식 |
| --- | --- |
| 기존 Moduly `workflow_deployments.graph_snapshot` | version diff와 deploy checklist |
| 기존 Moduly `llm_usage_logs` | cost recommendation |
| 기존 Moduly `workflow_runs` | failure/latency aggregation |
| MVP 1-2 `audit_logs` | `policy.block`/permission change aggregation |
| MVP 2 RAG trace metadata | RAG risk and stale index check |
| 기존 Moduly Scheduler/Webhook/API 실행 | trigger별 운영 통계 |
| 기존 Moduly Docker/Helm/K8s | 배포 재현성 |

## 배포 권한과 이전 배포 활성화

현재 Moduly 기준 deployment API에는 생성, 목록, node deployment 목록, 상세, public info, toggle, delete가 있고 별도 `rollback` API는 없다. API 표면은 `POST /api/v1/deployments`, `GET /api/v1/deployments`, `GET /api/v1/deployments/nodes`, `GET /api/v1/deployments/{deployment_id}`, `GET /api/v1/deployments/public/{url_slug}/info`, `PATCH /api/v1/deployments/{deployment_id}/toggle`, `DELETE /api/v1/deployments/{deployment_id}`를 기준으로 한다.

`GET /api/v1/deployments`는 `app_id` 또는 `workflow_id` 중 하나가 없으면 빈 배열을 반환하고, `/deployments/nodes`는 active `WORKFLOW_NODE` deployment 중 현재 user가 workflow `read` 권한을 가진 항목만 반환한다. 배포 생성 시 `graph_snapshot`이 없으면 저장된 workflow draft를 snapshot으로 사용한다.

`toggle`로 예전 deployment를 다시 active로 만들면, 같은 app의 다른 active deployment가 비활성화되고 `app.active_deployment_id`가 그 deployment로 바뀐다.

따라서 사용자는 "이전 배포로 되돌리기"처럼 사용할 수 있지만, 제품/코드 레벨에서는 아직 `rollback`이라는 명시 기능이 아니라 deployment toggle 동작이다. 별도 rollback permission은 두지 않고 기본 권한은 workflow `deploy/manage`로 다룬다. 다른 deployment가 이미 active인 상태에서 이전 deployment를 다시 활성화하는 경우 `audit_logs.action='deployment.activate_previous'`로 남긴다. MVP 3의 배포 범위는 이 기본 권한이 아니라 deploy checklist, version diff, trigger mode 정합성 같은 운영 기능 강화다.

## 사용자 흐름

1. `builder` 권한 user가 workflow를 수정하고 배포를 시도한다.
2. Deploy checklist가 비용, 권한, PII, RAG 변경 위험을 보여준다.
3. 고비용 노드에 대해 저비용 모델 후보가 추천된다.
4. `builder` 권한 user가 model/prompt 변경을 적용한다.
5. Version diff에서 이전 배포 대비 변경을 확인한다.
6. 배포 후 운영 dashboard에서 비용/실패율/latency/`policy.block`을 본다.
7. API/Webhook/Scheduler 실행이 올바른 trigger mode로 기록된다.
8. Docker Compose 또는 K8s 기준으로 동일 MVP를 재현한다.

## 추가 개발 범위

- deploy checklist API/UI
- workflow version diff
- cost recommendation rule
- cache candidate detection
- fallback/retry 최소 정책
- operations dashboard aggregation
- trigger mode logging 정합성 수정
- Docker/K8s 실행 문서 정리

## 작업 순서

1. Trigger Mode 정합성

작업:

- API/Webhook/Scheduler/App 실행 context 정리
- log system trigger mode normalization 수정
- 기존 run 기록과 dashboard aggregation 기준 확정

검증:

- 각 trigger별 실행 후 `workflow_runs.trigger_mode` 정확성 확인

주의:

- 기존 문서상 REST API 실행은 `trigger_mode="api"`를 전달하지만 `DeploymentService.run_deployment()` 내부 `execution_context.trigger_mode`는 `"app"`으로 고정될 수 있다.
- Webhook 실행 context에는 `trigger_mode=webhook`이 들어가지만 log 정규화가 `webhook`을 정확히 매핑하지 않으면 fallback으로 `api`가 기록될 수 있다.
- Scheduler는 context에 `trigger_mode=schedule`을 넣지만 `workflow_runs.trigger_mode` enum 값은 `scheduler`라서 정규화 정책이 필요하다.

2. Version Diff

작업:

- deployment snapshot diff 유틸 작성
- prompt/model/config/knowledge base 변경 추출
- UI에서 이전 배포와 현재 draft 비교

검증:

- prompt 변경, model 변경, RAG source 변경이 diff에 표시

3. Deploy Checklist

작업:

- 예상 비용 계산
- 고비용 node 탐지
- 권한 위반 탐지
- PII/confidential warning
- RAG stale index warning
- 모델 가격 누락 경고

검증:

- warning/block 구분
- 배포 차단 정책 적용 가능

4. Cost Recommendation

작업:

- high cost node rule
- cheaper model candidate rule
- prompt token warning
- cache candidate detection
- recommendation event 저장

검증:

- 추천 결과가 실제 usage log 기반으로 생성
- 가격 정보 없는 모델은 추천 제외 또는 경고 처리

5. Operations Dashboard

작업:

- model별 비용
- workflow별 비용
- user/team별 실행량
- failure rate
- latency p50/p95
- `policy.block` count
- cache/fallback 후보 count
- deployment diff/check, recommendation, cache/fallback 관련 event 집계

검증:

- dashboard 집계가 raw log와 일치

6. Deployment Reproducibility

작업:

- Docker Compose 실행 절차 점검
- 필수 env 정리
- migration 실행 정책 문서화
- demo seed/fixture 작성
- K8s/Helm values에서 Nodease 추가 env 정리

검증:

- 새 환경에서 seed workflow 실행 가능

## 완료 기준

| 영역 | 완료 기준 |
| --- | --- |
| Deploy check | 실제 graph/log/policy/RAG 상태 기반 warning/block 표시 |
| Recommendation | 최소 rule-based 비용 절감 추천 동작 |
| Version diff | prompt/model/config/knowledge 변경 비교 |
| Ops dashboard | 비용, 실패율, latency, `policy.block` 집계 |
| Trigger logging | api/webhook/scheduler/app이 정확히 기록 |
| Deployment | 로컬 Docker Compose 기준 재현 가능 |

## Demo Script

```text
1. `builder` 권한 user가 workflow를 수정한다.
2. Deploy checklist를 실행한다.
3. 비용/권한/RAG/PII warning을 확인한다.
4. 추천된 저비용 모델을 적용한다.
5. Version diff를 확인하고 배포한다.
6. API/Webhook/Scheduler로 실행한다.
7. Ops dashboard에서 비용/실패/latency/`policy.block`을 확인한다.
```

## 테스트 범위

- trigger mode normalization
- deployment diff
- deploy checklist rules
- recommendation rules
- dashboard aggregation
- Docker Compose smoke test

## MVP 3에서 하지 않을 것

- 정교한 ML 기반 품질 평가
- 실제 과금/결제
- 정식 SOC 2/ISO 인증 대응
- 완전한 MCP Gateway
- 전체 CDC 실시간 sync
