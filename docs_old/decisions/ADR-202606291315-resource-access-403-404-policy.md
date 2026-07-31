# ADR-202606291315: Resource 접근 403/404 정책

Status: Accepted
Authority: Decision
Source of Truth: Yes
Verified Against: dev @ ec576b4f24155697aed8843acc6e5a3fc835f7e1
Created At: 2026-06-29 13:15 KST
Related ADRs: [ADR-202606290145-active-organization-header-context](ADR-202606290145-active-organization-header-context.md), [ADR-202606290131-audit-action-naming-standard](ADR-202606290131-audit-action-naming-standard.md)

## 배경

App/Workflow RBAC 적용 중 권한 부족을 항상 `404`로 숨길지, resource permission 부족을 `403`으로 노출할지 결정이 필요했다.

무조건 `404`로 숨기면 리소스 존재 여부 노출은 줄지만, 같은 organization scope 안에서 사용자가 권한 부족 원인을 알기 어렵고 UI, 운영, 감사 추적이 모호해진다. 반대로 모든 permission 부족을 `403`으로 반환하면 다른 organization의 resource id 존재 여부를 추론할 여지가 생긴다.

## 결정

MVP 1 App/Workflow 접근은 hybrid 정책을 사용한다.

1. 리소스가 없으면 `404 resource.not_found`를 반환한다.
2. 요청 user가 리소스의 organization scope 밖이면 `404 resource.not_found`로 숨긴다.
3. 요청 user가 같은 organization scope 안에 있지만 필요한 resource action 권한이 없으면 `403 permission.denied`를 반환한다.
4. 목록 API는 접근 가능한 resource만 반환하고, 숨겨진 resource 개수나 id는 노출하지 않는다.
5. resource permission helper에서 발생한 `403`은 `audit_logs.action='permission.denied'`로 기록한다.
6. organization scope 밖 접근을 `404`로 숨긴 경우에는 resource permission 부족 audit를 남기지 않는다.

## 구현 기준

- Organization scope 안 여부는 MBA-67 이후 organization membership helper로 판정한다. Active row는 `organization_auth_state`에 따르고, invited/suspended/removed row는 fail-closed 된다. Membership row 자체가 없는 legacy owner/manager만 호환 fallback으로 manager scope를 받는다.
- App 접근은 app 전용 permission table 없이 primary workflow 권한과 organization owner/manager 권한으로 판정한다.
- Workflow 접근은 workflow의 effective auth state와 organization scope 접근 여부를 함께 본다.
- `viewer`가 workflow 저장/실행을 시도하거나, `operator`가 draft 저장을 시도하는 경우는 같은 scope 안 action 권한 부족이므로 `403 permission.denied`다.
- 다른 organization의 app/workflow id로 접근하는 경우는 `404 resource.not_found`다.

## 영향

- API 오류 계약: [api/errors.md](../api/errors.md)
- App/Workflow API: [api/apps-workflows.md](../api/apps-workflows.md)
- RBAC 정책: [data-model/rbac-permission-policy.md](../data-model/rbac-permission-policy.md)

## 제외

- 같은 organization 안에서도 resource 존재 여부를 완전히 숨기는 고보안 모드는 MVP 1 범위에 포함하지 않는다.
- Deployment 권한 강화와 marketplace 공개 정책은 별도 이슈 범위다.
