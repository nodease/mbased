# 배포 API

Status: Draft
Authority: API
Source of Truth: Yes
Verified Against: dev @ ec576b4f24155697aed8843acc6e5a3fc835f7e1

## 범위

Deployment 생성, 조회, 활성화, public deployment info, run/webhook 계약을 정의한다.

## Deployment 엔드포인트

| Status | Method | Path | Request | Response | Permission |
| --- | --- | --- | --- | --- | --- |
| Implemented | `POST` | `/api/v1/deployments` | `DeploymentCreate` | `DeploymentResponse` | workflow `deploy` |
| Implemented | `GET` | `/api/v1/deployments` | query | `DeploymentResponse[]` | workflow `read` |
| Implemented | `GET` | `/api/v1/deployments/nodes` | query | `dict[]` | workflow `read` |
| Implemented | `GET` | `/api/v1/deployments/{deployment_id}` | 없음 | `DeploymentResponse` | workflow `read` |
| Implemented | `GET` | `/api/v1/deployments/public/{url_slug}/info` | 없음 | `DeploymentInfoResponse` | public |
| Implemented | `PATCH` | `/api/v1/deployments/{deployment_id}/toggle` | 없음 | `DeploymentResponse` | workflow `deploy` |
| Implemented | `DELETE` | `/api/v1/deployments/{deployment_id}` | 없음 | message | workflow `manage` |

위 permission은 route 구현 상태 기준이다. MVP 3의 배포 범위는 기본 `read/deploy/manage` enforcement가 아니라 deploy checklist, version diff, trigger mode 정합성, operations dashboard 같은 운영 기능 강화를 뜻한다.

현재 구현 세부사항:

- `GET /api/v1/deployments`는 `app_id` 또는 `workflow_id` 중 하나가 없으면 빈 배열을 반환한다.
- `app_id`와 `workflow_id`를 함께 보낼 경우 두 값이 같은 primary workflow를 가리키지 않으면 `400`을 반환한다.
- `GET /api/v1/deployments/nodes`는 active `WORKFLOW_NODE` deployment만 반환하며, 현재 user가 해당 app의 workflow `read` 권한을 가진 항목만 포함한다.
- 배포 생성 시 `graph_snapshot`이 없으면 저장된 workflow draft를 snapshot으로 사용한다. draft도 없으면 `400`을 반환한다.
- 배포 생성 시 app에 `url_slug` 또는 `auth_secret`이 없으면 서버가 생성한다.

## Public Run 및 Webhook

| Status | Method | Path | Request | Response | 인증 |
| --- | --- | --- | --- | --- | --- |
| Implemented | `POST` | `/api/v1/run/{url_slug}` | JSON body | run result | app `auth_secret` |
| Implemented | `POST` | `/api/v1/run-public/{url_slug}` | JSON body | run result | public deployment policy |
| Implemented | `POST` | `/api/v1/hooks/{url_slug}` | request body | webhook result | webhook secret/header policy |
| Implemented | `GET` | `/api/v1/hooks/{url_slug}/capture/start` | 없음 | capture state | capture helper endpoint |
| Implemented | `GET` | `/api/v1/hooks/{url_slug}/capture/status` | 없음 | capture state | capture helper endpoint |

Public run/webhook 인증 세부사항:

- `POST /api/v1/run/{url_slug}`는 `Authorization: Bearer <auth_secret>` 또는 `X-Auth-Secret: <auth_secret>`을 받는다. secret이 없거나 일치하지 않으면 deployment service가 `401 Invalid authentication secret`을 반환한다.
- `POST /api/v1/run-public/{url_slug}`는 app/web embed용 공개 실행 경로이며 app secret 인증을 요구하지 않는다. 응답에 embed 지원 CORS header를 추가한다.
- `POST /api/v1/hooks/{url_slug}`는 `?token=<auth_secret>`, `Authorization: Bearer <auth_secret>`, `X-Webhook-Secret: <auth_secret>` 중 하나를 허용한다. 인증 실패 시 `403`을 반환한다.
- Webhook capture 상태는 현재 process memory의 `CAPTURE_SESSIONS`에 저장된다. 캡처 완료 후 `GET /hooks/{url_slug}/capture/status`가 payload를 반환하고 세션을 제거한다.

## 스키마

### `DeploymentCreate`

| Field | Type | Required | 설명 |
| --- | --- | --- | --- |
| `app_id` | UUID | Yes | 배포할 app |
| `type` | enum | No | 기본값 `API` |
| `url_slug` | string | No | schema에는 있지만 현재 서비스는 이 값을 사용하지 않는다. URL slug는 `apps.url_slug`가 소유한다. |
| `description` | string | No | 설명 |
| `config` | object | No | 배포 설정 |
| `is_active` | boolean | No | 생성 후 활성 여부 |
| `graph_snapshot` | object | No | workflow graph snapshot |
| `auth_secret` | string | No | schema에는 있지만 현재 서비스는 이 값을 사용하지 않는다. Secret은 `apps.auth_secret`가 소유한다. |

`auth_secret`은 원칙적으로 응답에 원문으로 노출하지 않는 방향이 맞다. 그러나 현재 코드 기준 `DeploymentService.create_deployment`는 응답 객체에 `app.auth_secret` 원문을 주입하고 `DeploymentResponse.auth_secret`이 이를 노출할 수 있다. 이 동작은 후속 보안 정렬 대상이다.

## Rollback 표현

별도 rollback API는 만들지 않는다. 이전 deployment를 다시 활성화하는 동작은 `PATCH /deployments/{deployment_id}/toggle`로 수행한다.

Audit action은 토글 전 상태에 따라 구분한다.

| 상황 | `audit_logs.action` |
| --- | --- |
| active deployment를 비활성화하거나, active deployment가 없는 상태에서 활성화 | `deployment.toggle` |
| 다른 deployment가 이미 active인 상태에서 inactive였던 이전 deployment를 활성화 | `deployment.activate_previous` |
