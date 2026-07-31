# ADR-0068: EKS 지원 표면 제거와 공급자 중립 배포 경계

Status: Accepted

## Context

Nodease 저장소에는 AWS EKS 전용 GitHub Actions workflow, raw Kubernetes manifest와 Terraform 구성이 함께 존재했지만 실제 지원·검증·운영 책임자가 확정되지 않았다. 이 구성은 특정 AWS account, ECR, ALB, IRSA, RDS와 storage class를 전제로 했고, Docker Compose 및 Helm과 별도의 배포 계약을 중복 유지했다.

이 상태에서 dev에서 main으로의 승격은 사용자가 지원되는 경로와 단순 잔존 artifact를 구분하기 어렵게 한다. 특히 schedule dispatch의 claim/drain 전환 workflow는 실제 Pod 수렴, drain과 immutable image identity를 함께 보장해야 하므로, 검증되지 않은 provider-specific workflow를 형식적으로 유지하거나 수동 명령으로 대체할 수 없다.

Helm chart와 GHCR image publisher는 서로 연결된 실제 소비 경로다. 반면 checked-in Helm render snapshot은 source of truth가 아니며 현재 values/template과 쉽게 어긋난다.

배포 경계는 파일 존재 여부만으로 완결되지 않는다. ConfigMap 생성 조건과 Pod 소비 조건이 다르면 schema-valid manifest도 `CreateContainerConfigError`로 실패하고, 승인 workflow가 local composite action이나 `scripts/**` 실행 파일로 실행을 위임하면 최상위 metadata만 읽는 content guard를 우회할 수 있다. 참조 파일만 변경한 PR에서 selector가 support-surface 검사를 선택하지 않는 문제도 같은 우회를 만든다. 선택형 CI가 Helm lint/schema만 실행하고 이를 검증하는 계약 테스트를 선택하지 않으면 같은 drift가 다시 병합될 수 있다.

## Options considered

### 1. 기존 EKS 구성을 즉시 복구해 공식 지원한다

- 장점: 기존 workflow와 Terraform 투자를 유지할 수 있다.
- 단점: 실제 AWS environment, OIDC, secret, cluster/CNI, rollout 운영 책임과 통합 증거가 없으므로 현재 이슈 범위를 넘는다.

### 2. EKS artifact를 남겨 두되 비공식으로 표시한다

- 장점: 삭제 변경이 작다.
- 단점: stale workflow가 다시 실행되거나 문서와 CI가 지원 대상으로 오인할 수 있다. 보안·운영 계약이 중복되고 drift를 지속한다.

### 3. EKS 전용 표면을 제거하고 지원 경계를 좁힌다

- 장점: 실제 검증 가능한 Docker Compose와 공급자 중립 Helm만 source of truth로 유지한다. 생성부-소비부 reference closure와 GitHub executable이 전이적으로 위임하는 local 실행 closure까지 한 계약으로 검사하고 재도입 조건을 명확히 할 수 있다.
- 단점: provider-neutral coordinated CD가 추가되기 전까지 non-disabled schedule 운영 활성화는 지원할 수 없다.

## Decision

Option 3을 채택한다.

1. 공식 배포 artifact는 Docker Compose와 infra/helm/moduly의 공급자 중립 Helm chart다.
2. 다음 EKS 전용 표면을 제거한다.
   - .github/workflows/deploy-dev-namespace.yml
   - .github/workflows/deploy-eks-*.yml
   - infra/k8s/**
   - infra/terraform/**
3. source of truth가 아닌 checked-in infra/helm/moduly/rendered.yaml을 제거한다. CI가 기본 및 production values를 매번 렌더링하고 schema를 검사한다.
4. publish-images.yml은 Helm의 실제 image source이므로 유지하되 ghcr.io/nodease namespace를 사용한다.
5. values-production.yaml은 provider-neutral reference로 유지한다.
   - provider account, endpoint, domain, IAM annotation과 storage class를 포함하지 않는다.
   - ingress는 기본 비활성이고 operator가 class, TLS, trusted proxy CIDR과 host를 명시해야 한다.
   - secret 값은 저장소에 두지 않으며 외부 secret manager 또는 배포 시 주입을 요구한다.
   - workload identity는 chart root `serviceAccount` 한 곳에서만 설정하며 Gateway, Workflow Worker와 Knowledge Worker가 같은 ServiceAccount를 명시적으로 사용한다.
   - 문서 저장소 설정은 chart root `storage` 한 곳만 source of truth로 사용한다. `CLOUD`는 bucket과 region이 모두 있어야 렌더되며 Gateway 설정도 provider client 생성 전에 같은 계약을 검증한다. 이전 component별 storage key는 조용히 무시하지 않고 root 설정으로 이관하라는 safe render error로 거부한다.
   - Gateway, Workflow Worker와 Knowledge Worker는 `LOCAL`에서 cloud 전용 key를 참조하지 않고 `CLOUD`에서만 bucket과 region을 함께 참조한다. CI는 실제 기본/production render에서 필수 ConfigMap reference가 같은 render의 key로 모두 해석되는지 검사한다.
6. Helm과 Compose의 schedule dispatch 기본값은 disabled다. 현재 지원 표면에는 안전한 coordinated CD가 없으므로 claim/drain 활성화는 fail-closed한다.
   - Helm은 non-disabled 값을 render 단계에서 거부하고 Compose는 mode와 fingerprint를 `disabled`로 고정해 shell 또는 `.env` 값으로 활성화하지 못하게 한다.
   - runtime ledger, readiness, transition preflight와 drain domain 코드는 삭제하지 않는다.
   - non-disabled activation은 immutable image identity, Logger/Gateway/Worker 순서, 실제 Pod 수렴과 안정 drain을 제공하는 별도 provider-neutral CD 결정과 구현 후에만 다시 지원한다.
7. Knowledge ingestion worker는 본 결정에서 활성화하지 않는다. production knowledgeWorker.enabled: false를 유지하고 활성화 완결성은 MBA-359가 소유한다.
8. PR 품질 게이트는 legacy dev namespace workflow, `deploy-eks-*` workflow와 `infra/k8s/**`, `infra/terraform/**`의 재도입을 거부한다.
   - 모든 executable GitHub workflow 변경은 support-surface validation을 선택한다. 현재 승인된 workflow path allowlist 밖의 파일은 이름과 확장자에 관계없이 실패하고, 삭제된 workflow의 stale allowlist entry도 허용하지 않으므로 이름 변경이나 나중 재추적으로 배포 표면을 재도입할 수 없다.
   - Allowlist 안의 workflow와 `.github/actions/**/action.yml|yaml` local action은 물론, 이들이 지원하는 정적 형식으로 참조하는 승인 local reusable workflow/action과 `scripts/**` 실행 파일·Python module도 AWS credential/ECR/EKS/eksctl 같은 provider-specific 실행 신호가 있으면 실패한다. Reader는 발견된 참조를 전이적으로 따라가되 repository 경로 containment, 허용 prefix, regular file, UTF-8, 개별 크기와 전체 깊이/개수 한도를 적용한다. 누락·symlink 또는 허용 범위 밖 local 위임은 fail-closed한다.
   - 참조 실행 파일만 바꿔 selector를 우회할 수 없도록 repository execution-closure 검사는 모든 PR의 scope 분류 전에 실행한다. Workflow/action 변경 시 deployment validation의 동일 검사도 유지한다. 이 내용 검사는 독립 write-maintainer 승인 정책을 대체하지 않는다.
   - Helm 기본/production values에 대해 lint와 render를 수행한다.
   - CLOUD storage의 unknown type, 빈 bucket, 빈 region은 각각 negative render로 실패함을 검증한다.
   - Helm 변경은 deployment validation job에서 `tests/ci`가 소유하는 support-surface와 storage deployment 계약 pytest를 직접 실행한다. 실제 Helm render test는 dependency build를 마친 이 step의 명시적 integration flag에서만 활성화하고 runner에 우연히 설치된 Helm binary를 실행 근거로 사용하지 않는다. 이 계약은 runtime package를 import하지 않으며 전체 Shared/root 회귀를 선택하는 대신 두 exact 계약만 실행해 선택형 CI 비용을 제한한다.
   - 렌더 결과는 kubeconform v0.7.0과 Kubernetes 1.31 compatibility baseline으로 검사한다. 이는 EKS 지원 선언이 아니다.
9. EKS를 다시 지원하려면 새 ADR과 이슈에서 cloud ownership, OIDC/secret, cluster/CNI, migration, rollback, schedule coordinated rollout, 실제 environment integration evidence를 함께 제시해야 한다.

## Security and protected-resource boundaries

| Boundary | Result | Evidence |
| --- | --- | --- |
| 정책·설정 식별자 | 완료 | Root `storage`와 `serviceAccount`만 chart 권위로 정의한다. `test_storage_deployment_contract.py`, `test_supported_deployment_surface.py`가 중복 설정, render reference closure와 workflow/local action/위임 실행 closure 경계를 검증한다. |
| 관리 API·UI | 해당 없음 | 이 결정은 제품 사용자가 관리하는 resource가 아니라 operator-owned Helm/process 설정을 변경한다. |
| 저장·GraphMutation | 해당 없음 | Graph나 durable DB에 provider reference 또는 credential을 새로 저장하지 않는다. |
| Deployment preflight | 완료 | `moduly.validateStorage`와 PR Helm negative render가 unknown/missing/legacy storage 값을 배포 전에 거부한다. |
| Runtime/background | 완료 | `apps/gateway/core/config.py`와 `apps/gateway/services/storage.py`가 S3 client 생성 전에 같은 필수값을 재검증하며 provider-not-called 테스트가 있다. |
| Transaction·TOCTOU | 해당 없음 | 설정은 process startup snapshot이며 DB transaction, capability 또는 장기 lock을 추가하지 않는다. |
| Retry·idempotency | 해당 없음 | Provider write retry, effect identity 또는 replay 동작을 추가·변경하지 않는다. |
| Background coordination | 해당 없음 | Lease, claim, fencing 또는 cancellation 소유권을 추가하지 않는다. |
| Lifecycle | 완료 | Schedule은 supported Helm/Compose에서 disabled만 허용하고 non-disabled mode는 render/config 경계에서 fail-closed한다. |
| 오류·resource hiding | 완료 | Unknown/incomplete storage는 LOCAL fallback 없이 stable safe error가 되며 upload/presign provider detail은 caller exception에 전달되지 않는다. |
| Secret/credential·redaction | 완료 | Production values에는 secret/provider account/ARN을 두지 않는다. 설정 ValidationError와 storage operation log test가 credential/provider 원문 비노출을 검증한다. |
| Authorization/RBAC | 해당 없음 | 애플리케이션 resource permission 또는 actor scope를 변경하지 않는다. |
| Legacy/migration | 완료 | Durable schema migration은 없다. 기존 component별 Helm storage override는 silent fallback 대신 render 실패로 식별되며 운영자가 root `storage`로 명시 이관해야 한다. Direct Gateway 개발 예시는 이미 지원값 `LOCAL`을 사용하고, 계약 테스트가 legacy `PROD` 안내의 재도입을 차단한다. |
| 문서·테스트 | 완료 | 본 ADR, architecture, deployment requirements/component/test cases와 CI·Gateway·deployment contract test를 함께 갱신한다. Helm 변경은 전용 job에서 두 exact 계약 test를 실행하며 모든 PR의 scope job이 위임 실행 closure를 먼저 검사한다. |
| Knowledge worker activation | 후속 이슈 | MBA-359. 현재 production default는 비활성이다. |
| Provider-neutral coordinated CD | 후속 이슈 | 별도 ADR/이슈와 실제 운영 증거가 필요하다. 현재 schedule mode는 disabled로 안전하게 고정된다. |

## Consequences

- 사용자는 로컬·단일 서버는 Docker Compose, Kubernetes packaging은 provider-neutral Helm으로 판단할 수 있다.
- Helm을 특정 managed Kubernetes에 설치하는 것은 가능하지만, 해당 cloud의 provisioning·ingress·identity·storage는 저장소가 공식 지원하거나 자동 구성하지 않는다.
- non-disabled distributed schedule 실행을 production에서 활성화할 공식 경로가 당분간 없다. 이를 수동 kubectl 절차로 우회해서는 안 된다.
- CI는 제거된 표면의 삭제 PR에서도 guard를 실행한다. 새 workflow는 명시적 allowlist 변경과 독립 승인이 필요하고, 기존 승인 workflow도 provider-specific 실행 신호를 포함할 수 없다.
- 승인 workflow가 local action이나 전이적으로 참조한 `scripts/**` 실행 파일·Python module에 provider-specific 실행을 숨기는 경로도 같은 guard에서 차단된다. 참조 파일만 바뀐 PR도 scope 분류 전 검사를 통과해야 한다.
- Production reference의 CLOUD storage placeholder는 그대로 배포할 수 없다. 운영자가 root storage bucket/region을 공급해야 Helm render와 Gateway startup을 통과한다.
- provider-specific 운영 배포를 추가할 때는 dormant sample이 아니라 소유권과 검증 증거를 갖춘 별도 기능으로 도입해야 한다.

## Affected files

- .github/workflows/pr-quality-gate.yml
- .github/workflows/publish-images.yml
- scripts/ci/changed_scope.py
- scripts/ci/check_supported_deployment_surface.py
- tests/ci/test_supported_deployment_surface.py
- tests/ci/test_pr_quality_gate_workflow.py
- tests/ci/test_storage_deployment_contract.py
- apps/gateway/core/config.py
- apps/gateway/services/storage.py
- infra/helm/moduly/values.yaml
- infra/helm/moduly/values-production.yaml
- infra/helm/moduly/templates/gateway-deployment.yaml
- infra/helm/moduly/templates/worker-deployment.yaml
- infra/helm/moduly/templates/knowledge-worker-deployment.yaml
- docs/architecture.md
- docs/engineering/ci-quality-gates.md
- docs/features/deployment/*
- docs/features/workflow/*

## Follow-up review notes

- provider-neutral coordinated CD가 제안되면 ADR-0029의 rollout safety invariant를 축소하지 말고 본 ADR의 현재 비지원 판정을 명시적으로 대체해야 한다.
- managed Kubernetes 지원을 다시 추가할 때 Helm compatibility와 cloud provisioning 지원을 별도 항목으로 표시해야 한다.
- Workflow allowlist 또는 provider-specific signal 목록을 변경할 때는 우회 문자열을 늘리는 방식이 아니라 새 운영 표면의 소유권·위협 모델·실제 검증 증거를 함께 재검토해야 한다.
- 새 local executable root나 동적 위임 방식이 필요해지면 parser 우회로 허용하지 말고 허용 prefix, 정적 해석 규칙, 실행 시점과 CI 검증 소유권을 본 결정 또는 후속 ADR에서 명시해야 한다.
