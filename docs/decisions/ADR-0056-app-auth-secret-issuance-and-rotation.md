# ADR-0056: App 인증 secret 발급·검증·rotation 경계

Status: Accepted
Related ADRs: ADR-0008, ADR-0009, ADR-0010, ADR-0041

## Context

App의 `auth_secret`은 public API run과 webhook을 호출할 수 있는 bearer credential이다. 현재 구현은 이 원문을 `apps.auth_secret`에 저장하고 일반 `AppResponse`와 `DeploymentResponse`에 포함하며, Client가 조회 결과에서 표시·복사한다. Deployment 생성 service도 ORM 응답 객체에 원문을 동적으로 주입한다.

일반 resource 조회와 credential 발급은 권한·감사·보존 정책이 다르다. Bearer secret 원문이 목록·상세·배포 응답, 브라우저 상태 또는 DB snapshot에서 유출되면 별도 사용자 인증 없이 public execution surface에 재사용할 수 있다. ADR-0041은 public webhook의 header-only 전달과 bounded credential parsing을 확정했지만 발급, 응답 제거, verifier 저장과 rotation lifecycle은 MBA-247로 남겼다.

## Options Considered

1. 기존 원문 저장과 일반 응답을 유지하고 UI에서 masking한다.
   - UI 표시만 가릴 뿐 원문이 API와 브라우저까지 전달되는 문제를 해결하지 못한다.
2. 원문을 application encryption으로 저장하고 일반 응답에서만 제거한다.
   - public 인증에는 복호화가 필요하지 않은데도 reversible credential과 keyring 운영 경계를 유지한다.
3. 명시적 one-time 발급 surface, 비가역 verifier, bounded current/previous rotation을 도입한다.
   - migration과 Client 전환이 필요하지만 발급·검증·rotation의 권위를 Gateway 한 곳에 고정할 수 있다.

## Decision

### 1. 일반 resource 응답은 secret 원문을 반환하지 않는다

`AppResponse`, `DeploymentResponse`, App clone/list/detail, Deployment create/list/detail/toggle/run-info와 browser-access projection은 `auth_secret` 원문을 포함하지 않는다. Masked preview도 원문을 Client에 전달하므로 제공하지 않는다. ORM 또는 legacy model에 원문 속성이 남아 있어도 response schema가 직렬화하지 않아야 한다.

Client가 App 또는 Deployment 생성 요청에 secret을 지정할 수 없게 한다. 신규 secret은 Gateway가 CSPRNG로 생성한다.

### 2. 원문은 명시적 rotation 성공 응답에서 한 번만 반환한다

App resource 아래에 safe status와 one-time rotation command를 둔다.

- `GET /api/v1/apps/{app_id}/auth-secret/status`
- `POST /api/v1/apps/{app_id}/auth-secret/rotate`

`expected_version=0`은 미설정 App의 최초 발급이다. Expand 기간에는 migration 뒤 구버전 Pod가 만든 configured generation 0 raw-only state를 managed generation 1로 전환할 때도 같은 값이 사용된다. 양수는 managed current secret의 rotation이다. Status와 성공 rotation 응답은 `Cache-Control: no-store, no-cache`와 `Pragma: no-cache`를 사용하며 성공 rotation 응답만 새 원문을 포함한다. 이후 status나 일반 조회로 원문을 다시 읽을 수 없다. V1은 raw secret replay/idempotency response store를 추가하지 않는다. Client는 모달/component memory 안에서도 one-time 원문과 발급 version을 함께 보존하고, 다시 mount된 control의 no-store status version이 일치할 때만 원문을 재표시·복사·테스트 header에 사용한다. 응답 유실 또는 다른 탭의 rotation 뒤 refresh version이 보존한 version과 다르면 원문을 즉시 폐기하며, 이를 새 current secret으로 표시·복사하지 않는다. 이 경우 status의 최신 version을 이용해 사용자가 새 rotation을 명시적으로 수행한다.

Status 응답은 `rotation_enabled`를 포함한다. Checked-in 배포 설정과 application default는 `APP_AUTH_SECRET_LIFECYCLE_MODE=disabled`이며 이 상태에서는 권한 확인 뒤 rotation command를 `503 app.auth_secret_lifecycle_unavailable`로 차단한다. 모든 Gateway Pod가 verifier-aware revision으로 수렴한 뒤에만 별도 배포 변경으로 `active`를 설정한다.

`api`와 `webhook` deployment는 App secret 인증에 의존하므로 configured secret이 없는 상태로 active가 될 수 없다. Lifecycle mode가 `disabled`이면 active preflight/create/toggle을 `503 app.auth_secret_lifecycle_unavailable`로 막고, mode가 `active`지만 아직 발급하지 않았다면 `409 deployment.app_auth_secret_required`와 `issue_app_auth_secret` action을 반환한다. Inactive draft와 secret 인증을 사용하지 않는 deployment type은 이 gate의 대상이 아니다.

자동 HTTP mutation retry는 허용하지 않는다. 같은 `expected_version` 재요청은 `409 app.auth_secret_version_conflict`로 끝나며 새 secret을 생성하거나 반환하지 않는다.

### 3. App당 current verifier 하나와 previous verifier 하나를 저장한다

Gateway는 server-generated ASCII token에 domain-separated SHA-256 V1을 적용해 고정 길이 verifier를 저장하고 이를 신규 인증 권위로 사용한다. Token이 최소 256-bit CSPRNG entropy를 가지므로 verifier만 유출된 공격자가 bearer token을 복구하는 것은 현실적으로 불가능하다. 신규 생성 token은 shared trace와 webhook capture redactor가 일반 free-text field에서도 식별할 수 있는 고정 비밀 marker를 포함한다. Marker는 인증·권한 판정의 근거가 아니며 audit, trace, log 같은 durable metadata에는 원문과 함께 남기지 않는다. 향후 verifier algorithm 변경을 위해 current/previous algorithm version을 함께 저장한다.

Production의 일반 migration-first rolling workflow는 migration 뒤에도 구형 Gateway가 잠시 traffic을 받으므로 이 expand를 그대로 수행하는 안전한 경로가 아니다. 구형 Gateway는 lifecycle mode를 모르고 legacy 일반 응답에서 원문을 계속 반환하며 verifier-only row를 인증할 수 없다. 따라서 expand는 migration 전에 구형 Gateway를 ingress에서 drain/fence하고, migration과 verifier-aware `disabled` revision 수렴을 완료한 뒤 traffic을 재개하는 maintenance rollout을 사용해야 한다. 별도 redaction-only compatibility release를 먼저 전체 수렴시키는 대안도 가능하지만, 구형과 verifier-only 활성 revision이 동시에 traffic을 받는 배포는 지원하지 않는다. 혼합 구간에 신규 원문을 legacy column에 이중 쓰는 방식도 구버전 응답 노출과 previous grace 불일치 때문에 사용하지 않는다. 모든 Pod의 revision 수렴을 확인하고 lifecycle mode를 `active`로 바꾼 뒤에만 신규 원문을 one-time 응답으로 발급하며 DB에는 verifier만 저장한다. Verifier가 있는 generation은 verifier만 인증 권위로 사용하며 raw fallback을 허용하지 않는다.

인증 candidate는 ADR-0041의 1~512 ASCII 제한을 통과한 뒤 같은 verifier로 변환하고 `compare_digest`로 current와 비교한다. Current가 불일치하고 previous grace가 유효할 때만 previous와 비교한다. Missing, malformed, unknown verifier version, invalid state와 mismatch는 동일한 authentication failure로 fail-closed한다.

### 4. rotation은 row lock과 version CAS로 직렬화한다

Rotation application service는 active organization과 App `deploy` 권한을 확인한 뒤 App row를 `SELECT FOR UPDATE`로 잠근다. Locked row의 organization과 권한 scope를 다시 확인하고 request의 `expected_version`과 current version이 일치할 때만 새 token을 생성한다.

성공 transition은 current를 previous로 이동하고 새 verifier를 current로 설정하며 version을 1 증가시킨다. Secret state mutation과 필수 audit outbox는 같은 transaction에서 flush·commit한다. Audit persistence, flush 또는 commit 실패 시 전체 mutation을 rollback하고 새 원문을 반환하지 않는다.

동시 요청은 한 winner만 성공한다. Loser는 winner commit 뒤 최신 version을 관찰하고 conflict로 종료한다.

### 5. previous grace는 최대 5분이며 즉시 폐기를 지원한다

표준 rotation은 previous verifier를 5분 동안만 허용한다. Request의 `revoke_previous_immediately=true`이면 previous verifier를 저장하지 않고 기존 current를 commit 시점부터 즉시 폐기한다. Client가 임의 grace 숫자를 지정하거나 조직별 정책을 만들 수 없다.

Previous expiry는 timezone-aware UTC server clock으로 계산한다. `now < previous_valid_until`일 때만 유효하고 경계 시각부터 거부한다. 새 rotation은 기존 previous를 폐기하고 직전 current만 새 previous로 보존한다.

### 6. secret lifecycle은 active organization과 App deploy 권한을 요구한다

Status와 rotation은 인증된 사용자, `X-Organization-Id` active organization 일치와 App `deploy` 권한을 요구한다. 현재 permission model에서 deploy/manage가 모두 manager auth state를 요구하더라도 lifecycle service는 `deploy` 의미를 사용한다. Missing, cross-organization과 scope 밖 App은 ADR-0010 resource hiding을 적용한다.

### 7. audit와 관측에는 allowlist metadata만 남긴다

성공 발급·rotation은 canonical action `app.auth_secret.rotated`를 사용한다. 최초 발급 여부는 safe metadata의 `previous_version=0`으로 구분하며 별도 action을 만들지 않는다. Permission denial은 기존 `permission.denied` 경계를 사용한다.

허용 metadata는 organization ID, App ID, actor ID, previous/current version, immediate revoke 여부와 previous grace 활성 여부다. Caller-controlled request ID, IP, User-Agent를 이 action metadata에 병합하지 않는다. Secret, candidate, verifier, prefix, 길이, Authorization header와 fingerprint는 응답 외 durable system에 남기지 않는다.

### 8. legacy 원문은 expand/activate/reconcile/contract 순서로 제거한다

첫 migration은 verifier/version/previous metadata를 additive하게 추가하고 기존 원문을 동일 verifier로 backfill한다. Generation 0은 구버전 Pod가 migration 뒤 생성할 수 있는 raw-only compatibility state와 아직 credential이 없는 null state를 모두 허용한다. 새 코드는 generation 0 raw-only row를 제한적으로 검증하고, generation 1 이상에서는 verifier를 권위로 사용한다.

Rollout은 네 단계로 나눈다.

1. **Expand**: 구형 Gateway traffic을 먼저 drain/fence한 뒤 additive migration, 일반 응답 비노출과 verifier 인증을 배포하고 verifier-aware `disabled` revision 수렴 뒤 traffic을 재개한다. Lifecycle mode는 `disabled`로 고정해 신규 발급·rotation을 차단한다. 별도 redaction-only 선행 release가 수렴하지 않은 상태에서 구형 Gateway와 신형 Gateway가 동시에 traffic을 받는 일반 rolling은 허용하지 않는다. Generation 0 raw-only compatibility 인증은 migration과 legacy process 종료 사이의 late arrival 복구에만 사용한다.
2. **Activate**: 모든 Gateway Pod가 verifier-aware revision으로 수렴했음을 확인한 뒤 별도 설정 rollout에서 lifecycle mode를 `active`로 바꾼다. 이 시점부터 발급·rotation은 legacy raw를 저장하지 않고 current/previous verifier만 쓴다.
3. **Reconcile**: 혼합 구간에 생긴 generation 0 raw-only late arrival를 verifier state로 backfill하고, generation 0 fallback 사용량과 raw-only row가 0인지 확인한 뒤 fallback을 중단한다. 기존 generation 1 이상 row의 legacy raw도 이 단계에서 null로 정리할 수 있다.
4. **Contract**: verifier-only revision 수렴과 raw write 부재, raw-only row 0을 확인한 뒤 legacy column을 제거한다.

`auth_secret IS NULL` row가 하나라도 있으면 구 schema의 NOT NULL 원문을 복구할 수 없으므로 expand migration downgrade를 허용하지 않는다. 여기에는 verifier-only row뿐 아니라 credential이 아직 없는 신규 App도 포함된다. Expand 단계의 DB snapshot에는 migration 전 credential과 late arrival 원문이 남을 수 있으므로 일반 API 비노출은 즉시 개선되지만 DB-at-rest 위험 제거는 reconcile/contract 완료 조건이다.

## Rationale

- Resource 조회와 credential 발급을 분리해 accidental disclosure surface를 줄인다.
- 복호화가 필요 없는 bearer credential을 verifier로 저장해 DB 유출이 즉시 실행 권한 유출로 이어지지 않게 한다.
- Fixed current/previous state와 bounded grace로 consumer 전환을 지원하면서 active credential 수를 제한한다.
- Row lock과 expected version을 결합해 rotation winner와 사용자 관찰 상태를 일치시킨다.
- One-time response replay store를 만들지 않아 새로운 raw secret 보존 경계를 피한다.

## Consequences

- 기존 Client는 일반 App/Deployment 조회에서 secret을 표시·복사할 수 없다.
- 사용자는 최초 사용 또는 기존 값을 잃은 경우 명시적으로 secret을 발급·rotation해야 한다.
- 응답 유실 시 같은 secret을 복구할 수 없으며 새 rotation이 필요하다.
- 기존 App secret은 verifier backfill 뒤에도 동작한다. Expand 혼합 구간에는 lifecycle mutation이 일시적으로 비활성화되며, Pod 수렴 뒤 명시적 활성화가 필요하다.
- 활성화 뒤 신규 발급·rotation은 원문을 DB에 저장하지 않는다. 또한 expand 뒤 생성된 미설정 App도 raw null일 수 있으므로 `auth_secret IS NULL` row가 생긴 이후 구형 schema/image rollback은 지원하지 않는다.
- 여러 consumer별 독립 revoke와 attribution이 필요하면 단일 App secret을 확장하지 않고 별도 App API key 도메인을 설계해야 한다.

## Affected Files

- `apps/shared/db/models/app.py`
- `apps/shared/alembic/versions/`
- `apps/shared/schemas/app.py`
- `apps/shared/schemas/deployment.py`
- `apps/gateway/services/app_auth_secret_service.py`
- `apps/gateway/core/config.py`
- `apps/gateway/api/v1/endpoints/app.py`
- `apps/gateway/api/v1/endpoints/webhook.py`
- `apps/gateway/api/v1/endpoints/run.py`
- `apps/gateway/services/deployment_service.py`
- `apps/client/app/features/app/`
- `apps/client/app/features/workflow/`
- `docs/features/deployment/`

## Follow-up Review Notes

- Expand는 구형 Gateway traffic을 drain/fence한 maintenance rollout 또는 선행 redaction-only revision 수렴을 요구한다. 일반 migration-first rolling으로 구형 Gateway와 verifier-aware Gateway를 동시에 serving 상태에 두지 않는다.
- Lifecycle mode 활성화는 모든 Gateway instance가 verifier 기반 인증으로 수렴했음을 확인한 별도 설정 rollout이어야 한다. Expand image 배포와 같은 rollout에서 활성화하지 않으며, active 설정을 적용하기 전에 serving Gateway image revision이 하나로 수렴했음을 검증한다.
- 후속 reconcile은 generation 0 late arrival를 verifier로 backfill하고 raw-only row와 fallback 사용량이 0임을 확인한 뒤 fallback을 중단한다.
- Contract migration은 verifier-only revision 수렴과 raw write 부재를 확인한 뒤 legacy `apps.auth_secret` 원문과 column을 삭제한다.
- Production ingress와 application log가 Authorization 및 compatibility header를 기록하지 않는지 rollout 전에 다시 검증한다.
- 여러 active key, named consumer key, standalone revoke 또는 raw response replay가 필요하면 별도 ADR과 데이터 모델 검토를 요구한다.
