# ADR-0049: Connector 연결 테스트 보안 경계

Status: Accepted
Related ADRs: [ADR-0008](ADR-0008-audit-action-naming-standard.md), [ADR-0009](ADR-0009-active-organization-header-context.md), [ADR-0010](ADR-0010-resource-access-403-404-policy.md), [ADR-0022](ADR-0022-incremental-hexagonal-architecture-adoption.md)

## 배경

`POST /api/v1/connectors/test`는 DB/SSH credential을 저장하지 않고 외부 연결을 확인하는 helper다. 기존 구현은 인증·organization scope·분산 admission 없이 workflow compatibility용 PostgreSQL connector를 호출하므로 외부 사용자가 Gateway를 network oracle 또는 자원 고갈 경로로 사용할 수 있다. Direct PostgreSQL adapter에 public-address 검증이 일부 존재하지만 endpoint 인증, multi-replica rate/concurrency, request ingress, TLS, timeout과 오류 비노출을 함께 보장하지 않는다.

현재 `connections`는 `user_id` 소유이고 `organization_id`가 없다. 따라서 urgent test hardening에 connection ownership/RBAC migration까지 섞지 않는다.

## 결정

1. 연결 테스트는 로그인 사용자와 `X-Organization-Id`의 active organization membership을 요구한다. Active member/manager는 테스트할 수 있지만 이 capability는 connection create/use/manage 권한이 아니다.
2. 인증과 organization scope를 통과한 요청만 body를 읽는다. Gateway는 actual JSON body 32 KiB, 전체 receive 5초를 적용하고 repository edge는 canonical route와 단일 trailing-slash 동치 경로를 하나의 bounded location으로 처리해 32 KiB, 5초 idle receive와 request-target log 억제를 적용한다. Child path는 Connector test endpoint로 취급하지 않는다. Edge를 우회하는 direct Gateway/Next rewrite에서도 최외곽 ASGI transport sanitizer가 같은 두 경로의 connector-test query 전체를 access-log-visible scope에서 제거한다. Raw query는 보존하지 않고 존재 여부 boolean marker만 endpoint에 전달해 `400 connector.test_payload_invalid`로 닫는다.
3. 구조·port 검증 뒤 Redis admission보다 먼저 process-local executor slot을 non-blocking 예약한다. Local busy는 Redis rate와 Connector audit을 소비하지 않는다. Redis acquire 실패나 request 취소는 시작하지 않은 local 예약을 즉시 반환한다. 이후 Redis의 단일 atomic acquire에서 user/organization/network fixed-window rate와 user/organization/global concurrency lease를 함께 판정한다. Request network identity는 password login과 같은 trusted-proxy resolver로 정규화한다. Forwarded header는 설정된 trusted proxy peer에서만 사용하고 해석 불가 identity는 body와 network 전에 fail-closed한다. Distributed concurrency가 거부된 요청도 rate counter를 소비하지 않는다. Redis 장애도 network 전에 fail-closed한다. Acquire, renew, release는 각각 Connector 전용 operation deadline을 적용해 Redis transport가 응답하지 않아도 요청이나 lease 정리가 무기한 대기하지 않는다.
4. Admission identity는 dedicated key와 scope domain tag로 HMAC-SHA256 처리한다. Redis에는 digest와 최소 128-bit owner token만 저장한다. 실행 중 owner는 Redis time 기준 heartbeat로 lease를 연장하고, 완료 시 정확한 owner member만 제거한다. API timeout 뒤에도 actual probe가 server-owned hard deadline 전에 끝나면 completion까지 heartbeat를 유지한다. Hard deadline에는 async probe와 heartbeat를 취소하고 distributed lease를 해제한다. 취소할 수 없는 blocking driver thread의 local executor capacity는 실제 thread가 끝날 때까지 재사용하지 않는다. Process crash와 release 실패는 TTL로 회수한다.
5. V1 strict probe의 기본·production 경로는 public address로만 resolve되는 PostgreSQL host와 deployment-managed port allowlist만 허용한다. Local development는 `CONNECTOR_TEST_LOCAL_PROFILE_ENABLED=true`, `NODE_ENV=development`, 서버가 설정한 최대 4개의 exact canonical `host:port`, 전용 공개 CA를 모두 요구한다. Wildcard, CIDR, suffix, raw IP target과 request-controlled override는 허용하지 않는다. Exact local target의 모든 DNS 결과는 RFC1918, IPv6 ULA 또는 loopback이어야 하며 public/private mixed result는 거부한다. Host-run `localhost`가 IPv4/IPv6 loopback을 함께 반환할 때는 loopback-only bind와 일치하도록 검증된 IPv4를 결정적으로 우선한다. 모든 성공 경로는 실제 libpq 연결을 검증된 한 IP에 고정하고 다른 주소로 재시도하지 않는다.
6. Public target은 시스템 CA bundle, exact local target은 서버가 설정한 `CONNECTOR_TEST_TRUSTED_LOCAL_CA_FILE`을 사용해 TLS `verify-full`을 적용한다. Local CA는 64 KiB 이하의 현재 유효한 단일 PEM `CA:TRUE` 공개 certificate여야 하고 private key나 certificate bundle을 포함할 수 없다. 부재·invalid CA는 startup 또는 DNS 전에 fail-closed한다. Gateway는 UTC certificate validity API를 제공하는 `cryptography>=42.0.0`을 선언한다. 요청자는 CA나 SSL mode를 지정할 수 없다. 요청당 connection attempt는 한 번이고, query는 read-only `SELECT 1`, result는 one-row scalar로 제한한다. Connect 5초, statement 3초, API 10초, probe hard deadline 20초, lease 30초를 적용한다.
7. SSH-enabled test는 host-key와 approved private-network 정책이 도입되기 전까지 `connector.ssh_probe_not_supported`로 network 전에 거부한다. 기존 persisted connector create/schema compatibility를 이 결정으로 제거하지 않는다.
8. Expected target/connection 실패는 기존 `200 {success:false}` UX를 유지하되 server-owned static message와 allowlist reason code만 반환한다. 인증, ingress, admission과 schema 오류는 표준 HTTP error envelope을 사용한다. Connector `429 Retry-After`는 설정된 credentialed CORS origin에만 response header로 노출해 browser cooldown을 지원하며 origin allowlist는 넓히지 않는다.
9. Admission 이후 결과는 `connection.test` action으로 best-effort audit한다. Organization, actor, result, reason code와 coarse duration만 기록하며 host/IP/port/database/username/credential, raw network identity와 exception text를 기록하지 않는다. Post-probe audit 실패는 network probe를 재시도하지 않는다.
10. Endpoint는 auth/context, bounded ingress, use-case 호출과 HTTP mapping만 담당한다. Application은 framework-independent command/result/error/port를 소유하고 Redis, PostgreSQL과 audit 구현은 adapter/composition에서 조립한다.
11. Local/Docker 연결 시연은 일반 플랫폼 서비스와 분리된 explicit `connector-demo` profile과 전용 bridge network를 사용한다. Init service는 CA signing key를 container 임시 filesystem에서만 사용하고 종료 전에 제거한다. Server TLS material, PostgreSQL bootstrap admin credential, Connector demo credential은 서로 다른 private named volume에 두며 공개 CA certificate만 git-ignore된 local bind directory로 내보내 Gateway에 read-only mount한다. PostgreSQL은 세 private volume을 읽지만 one-shot verifier에는 Connector demo credential만 제공한다. Server certificate SAN은 `connector-test-postgres`와 `localhost`로 제한한다. Demo PostgreSQL TCP는 TLS 1.2 이상 `hostssl`/SCRAM만 허용하고 평문을 거부하며, Connector 계정은 read-only non-superuser다. Docker Gateway는 Connector admission에만 전용 demo Redis logical DB 15를 사용하고 platform Redis 설정은 유지한다. Connector composition이 만든 전용 client는 Gateway shutdown에서 닫는다.
12. Local profile은 `NODE_ENV`만으로 활성화하지 않는다. Explicit local-profile flag, exact target, local CA가 함께 있어야 하고 production은 세 setting 중 하나라도 있으면 시작하지 않는다. Production Helm과 기본 Compose는 local setting, service, network, volume, mount를 포함하지 않는다. 실제 Redis test는 실행별 namespace만 정리하며 `FLUSHDB`/`FLUSHALL`을 사용하지 않는다.
13. Connector startup과 secret-ingress 정책은 runtime, Docker Compose, Helm, Nginx와 ASGI middleware를 함께 검사하는 통합 배포 계약으로 고정한다. Base Compose는 development 기본 기동을 위해 admission HMAC 값을 비워 둘 수 있지만 외부 `CONNECTOR_TEST_ADMISSION_HMAC_KEY`를 반드시 Gateway 컨테이너에 전달하고, production의 누락·짧은 값은 runtime startup에서 fail-closed한다. Helm은 required Secret 주입을 유지한다. 실제 secret 값은 tracked Compose/env example이나 검증 output에 남기지 않는다.

초기 admission 기본값은 다음과 같다.

| Scope | Limit |
| --- | --- |
| User rate | aligned 60초 window당 5 |
| Organization rate | aligned 60초 window당 30 |
| Network rate | aligned 60초 window당 20 |
| User concurrency | 1 |
| Organization concurrency | 4 |
| Global concurrency | 16 |

제한값은 positive bounded environment setting으로 조정할 수 있지만 `0`, non-finite 값, 과도한 상한이나 누락으로 production 경계를 비활성화할 수 없다. Rate window/user/organization/network rate 상한은 각각 `300/100/1000/1000`, concurrency 상한은 `128`, connect/statement/API/probe hard/Redis operation/lease timeout 상한은 각각 `10/10/30/60/5/120`초다. Redis operation 기본값은 1초이며 `Redis operation < API < probe hard`, `3 × Redis operation < lease`, `connect < API`, `statement < API < lease` 관계를 유지한다. `CONNECTOR_TEST_ALLOWED_PORTS`는 중복 없는 `1..65535` 정수 1~16개만 허용하고 invalid 설정은 Gateway startup을 실패시킨다. Local profile의 각 target port도 이 allowlist에 포함되어야 한다.

Production admission HMAC key가 없거나 32 byte보다 짧으면 Gateway는 시작하지 않는다. Helm 배포는 별도 `secrets.connectorTestAdmissionHmacKey`, Docker Compose 배포는 외부 `CONNECTOR_TEST_ADMISSION_HMAC_KEY`를 32 byte 이상으로 제공해야 하며 auth/session key를 재사용하지 않는다.

## 검토한 대안

### 로그인만 요구

Organization attribution과 tenant rate scope가 없어 채택하지 않았다.

### Manager 또는 App 생성 권한만 허용

Knowledge DB source 작성 흐름과 다른 capability를 재사용해 권한 의미를 왜곡하므로 채택하지 않았다.

### Process-local limiter 또는 Redis 장애 시 local fallback

Multi-replica 전체 상한을 보장하지 못하고 장애를 우회 조건으로 만들기 때문에 채택하지 않았다.

### 기존 SSH tunnel 유지

Public bastion 뒤 private target, SSH host-key와 remote target 승인이 정의되지 않아 채택하지 않았다.

### 개발·데모에서 모든 포트 허용

환경 이름만으로 outbound network oracle 경계를 제거할 수 있어 채택하지 않았다. 개발·데모도 설치자가 관리하는 정확한 port allowlist를 사용한다.

### Local private network 전체 또는 CIDR 허용

편의를 위해 arbitrary private-network oracle을 다시 열고 DNS 변경 시 승인 범위가 확장되므로 채택하지 않았다. 개발 환경의 exact hostname과 port, 전용 CA를 함께 고정한다.

### 모든 connector outbound path를 한 번에 변경

Connection ownership, create/schema/runtime compatibility와 Knowledge ingestion까지 범위가 확대되므로 unauthenticated test surface를 우선 닫는다.

## 영향

- `/connectors/test` caller는 로그인과 active organization header가 필요하다.
- Deployment allowlist 밖 DB port와 SSH-enabled test는 V1에서 실패한다.
- Redis가 connector test의 필수 security dependency가 된다.
- Redis acquire/renew/release 무응답은 각각 bounded deadline 뒤 `connector.admission_unavailable`로 닫히며 public network probe로 fallback하지 않는다.
- Trusted proxy 설정이 실제 ingress topology와 일치하면 network rate는 복원된 client network 단위로 적용한다. 설정되지 않거나 신뢰할 수 없는 forwarded header는 socket peer 단위로 보수적으로 적용한다.
- API timeout 뒤 probe는 20초 hard deadline까지만 distributed lease를 보유한다. Hard deadline 뒤에도 반환하지 않는 driver thread는 새 작업에 재할당하지 않은 local executor slot 하나를 계속 점유할 수 있다.
- Docker connector demo의 Gateway admission은 전용 Redis를 사용하며 platform workflow/pub-sub Redis 설정은 바뀌지 않는다.
- Helm 배포자는 `secrets.connectorTestAdmissionHmacKey`를 32 byte 이상의 별도 secret으로 provisioning해야 한다.
- Production Docker Compose 배포자는 `CONNECTOR_TEST_ADMISSION_HMAC_KEY`를 외부에서 설정해야 하며 Compose가 해당 이름을 Gateway 컨테이너에 전달하는지 render 단계에서 확인해야 한다.
- Helm 배포자는 `connectorTest.allowedPorts`로 1~16개의 정확한 허용 포트를 정한다. 기본·production은 `5432`다.
- Local demo는 explicit profile에서만 생성되며 일반 `dev`/Docker 기동에는 자동 포함되지 않는다.
- Local exact-target profile은 explicit flag와 `NODE_ENV=development`를 모두 요구하고 production 설정은 startup fail-fast한다.
- Demo PostgreSQL은 TLS-only·최소권한이고, CA signing key는 persistent storage에 남지 않는다.
- Connection row schema와 create/detail/schema owner 계약은 변경하지 않는다.
- Client는 raw Axios/backend detail 대신 canonical status/reason만 표시하고, detail wire shape의 `connection_name`/`ssh.auth_type`은 form용 camelCase로 명시적으로 변환한다. Secret은 detail에서 복원하지 않는다.
- DB migration은 없다.

## 잔여 위험과 후속 검토

- Authenticated `POST /connectors`의 저장 전 probe와 schema/runtime SSH path는 별도 hardening이 필요하다.
- 운영 private network/SSH를 다시 허용하려면 host-key lifecycle, organization-approved network segment, target pinning과 audit 정책을 새 ADR로 승인해야 한다. Local exact-target profile은 운영 private-network 권한 primitive가 아니다.
- Trusted proxy CIDR이 실제 ingress topology와 다르면 network limit이 proxy 단위로 보수 적용되거나, 잘못 넓게 신뢰한 경우 forwarded identity spoofing 위험이 생긴다. 배포자는 정확한 peer CIDR만 설정해야 한다.
- Python thread에서 실행 중인 libpq 호출은 coroutine cancellation로 강제 종료할 수 없다. Hard deadline은 분산 lease의 무기한 점유를 막지만 local slot은 driver 반환 또는 process restart까지 점유될 수 있다. 이 잔여 위험이 운영상 허용되지 않으면 probe를 kill 가능한 격리 process/worker로 옮긴다.
- libpq/TLS parser의 protocol message bounds는 dependency 변경 때 재검토한다. 실질적으로 unbounded process-memory surface가 확인되면 probe를 memory-bounded isolated worker로 이동한다.
