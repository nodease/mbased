# Outbound Proxy-Only Rollout Runbook

Status: Draft

## Scope

이 runbook은 ADR-0072의 Docker Compose 및 provider-neutral Helm HTTP/HTTPS egress, Worker IMAP tunnel과 external Connector DB/SSH tunnel 전환을 다룬다. Platform PostgreSQL/Redis와 Sandbox control은 대상이 아니다. Proxy 설정을 제거해 direct egress로 되돌리는 절차는 안전한 rollback이 아니다.

## Required inputs

- Immutable application 및 Squid image identity
- Connector DB/SSH의 배포 관리 포트 allowlist. 기본은 `22,5432`이며 추가 포트는 workload env, Squid ACL과 proxy NetworkPolicy에 동일하게 반영해야 한다.
- Exact cluster pod CIDR 목록인 `egressProxy.networkPolicy.authorizedSourceCidrs`
- 실제 cluster DNS의 namespace와 필요한 경우 Pod label selector인 `egressProxy.networkPolicy.dns`. 기본 `kube-system`과 다르면 operator values에서 반드시 override한다.
- NetworkPolicy를 실제 집행하는 CNI와 해당 version
- Release manifest 전체 NetworkPolicy 목록
- External DB/Redis/Sandbox가 있으면 RFC1918/ULA private network CIDR 또는 exact public host CIDR(`/32`, `/128`)
- Application operation별 safe synthetic probe. Credential, 실제 문서, prompt와 payload를 probe/log에 넣지 않는다.

Catch-all, split catch-all, public network prefix, private block보다 넓은 supernet, loopback, link-local, metadata, multicast 또는 출처를 알 수 없는 source/destination CIDR은 사용하지 않는다. Public managed dependency가 동적 주소 범위를 요구하면 broad public CIDR을 열지 말고 private connectivity 또는 별도 FQDN-aware egress 결정을 사용한다. Squid source CIDR은 coarse network coordinate이며 workload identity 증명이 아니다.

NodeLocal DNS처럼 host-network IP 또는 CIDR 허용이 필요한 cluster는 현재 namespace/pod DNS 계약의 지원 대상이 아니다. DNS를 살리기 위해 broad port 53 egress를 추가하지 말고 별도 정책 결정과 실제 CNI positive/negative probe를 먼저 마련한다.

## Preflight

1. Helm lint/render와 proxy deployment contract를 통과한다.
2. Squid image가 digest로 고정되고 replica가 2개 이상이며 PDB, resource bound, non-root/read-only/capability drop이 렌더되는지 확인한다. `3128/3129/3130` listener와 Connector allowlist가 application env, ConfigMap과 NetworkPolicy에서 일치해야 한다.
3. Gateway, Workflow Worker와 Knowledge Worker에 custom `OUTBOUND_*` 설정만 있고 ambient proxy/`NO_PROXY`가 없는지 확인한다.
4. Release 전체에서 target selector를 여는 다른 public 80/443 egress policy, `hostNetwork`, privileged target pod와 Sandbox proxy source가 없는지 확인한다.
5. 모든 strict policy가 동일한 operator-owned DNS namespace/Pod selector를 렌더하는지 확인하고, 배포 CNI에서 DNS, Service DNAT, IPv4/IPv6, private/metadata deny와 unauthorized source probe를 실행한다. PR CI의 pinned Calico IPv4 결과는 운영 CNI 또는 dual-stack 증거를 대체하지 않는다.
6. Proxy 장애 시 외부 operation이 실패하고 direct 연결이 성공하지 않는지 확인한다.

하나라도 확인할 수 없으면 activation을 중단한다.

## Canary

1. `egressProxy.networkPolicy.enforcementPhase=canary`로 렌더한다.
2. Target Deployment를 먼저 pause하고 Helm revision을 적용한다. Chart는 rollout을 자동으로 pause하지 않으므로 operator가 workload별로 resume/pause를 조율해야 한다.
3. Gateway, Workflow Worker, Knowledge Worker를 한 workload씩 resume한다. `maxSurge=1`, `maxUnavailable=0`에서 첫 `nodease.io/egress-mode=proxy-v1` pod가 Ready가 되면 즉시 해당 Deployment를 pause한다.
4. 새 pod에 대해서만 다음을 확인한다.
   - 승인 HTTPS/Generic HTTP operation 성공
   - direct public/private/metadata IPv4/IPv6 실패
   - proxy unavailable에서 direct fallback 0회
   - DB/Redis/Sandbox direct internal route와 검증 IP 기반 IMAP 143/993 tunnel 정상
   - safe error, latency, pool saturation과 application 기능 회귀
5. 실패하면 해당 Deployment를 pause한 채 원인을 수정한다. NetworkPolicy나 proxy 설정을 제거해 호출을 살리지 않는다.

## Final enforcement

1. Canary가 통과하면 rollout을 resume하고 old pod를 모두 drain한다.
2. 모든 target pod의 application image, config checksum, `nodease.io/egress-mode=proxy-v1` label과 startup readiness를 확인한다.
3. `enforcementPhase=final`로 Helm revision을 적용한다. Final policy는 revision label이 아니라 component 전체를 선택한다.
4. Direct/proxy/unauthorized/proxy-down probe를 다시 실행하고 additive allow policy가 없는지 재확인한다.
5. Dual-stack cluster는 IPv4와 IPv6 결과를 모두 확보한 뒤에만 지원 matrix에 기록한다.

## Rollback

- Application 오류는 직전 proxy-aware image/config revision으로 되돌린다.
- Squid config 오류는 직전 digest/config checksum으로 되돌리되 workload strict policy를 유지한다.
- Connector `3130` 또는 허용 포트가 실패하면 direct public route를 추가하지 않는다. 직전 일치 allowlist로 되돌리거나 해당 Connector operation을 중지한다.
- Proxy capacity 문제는 승인된 resource/replica 조정으로 해결한다.
- `egressProxy.enabled=false`, strict policy 삭제, broad public CIDR 또는 `NO_PROXY=*`는 rollback으로 사용하지 않는다. 불가피하게 direct path를 복구하면 proxy-only 보장이 해제된 보안 incident/degraded mode로 선언하고 별도 승인을 받아야 한다.

## Evidence and redaction

보존 가능한 증거는 release/image/config revision, CNI/version, phase, probe outcome, latency/error bucket, replica/readiness와 timestamp다. Raw URL/query/header/body, credential, provider response/exception, resolved IP와 사용자 입력은 저장하지 않는다.

## Supported baseline

- Docker Compose: repository disposable proxy/network integration
- PR CI: kind v0.31.0, Kubernetes v1.32.11 pinned node image, Calico commit `0ca9d1b93644778cafdf1812f3dda02ac0c361e8`, IPv4
- Release: 실제 운영 CNI와 사용하는 address family별 동일 positive/negative probe 필수
