# ADR-0050: Workflow Generic HTTP egress 경계

Status: Accepted

Related ADRs: [ADR-0022](ADR-0022-hexagonal-module-boundary.md), [ADR-0035](ADR-0035-external-effect-idempotency-boundary.md), [ADR-0049](ADR-0049-connector-test-security-boundary.md)
Deployment surface correction: active EKS manifest에 관한 Decision 12와 consequence는 [ADR-0068](ADR-0068-eks-support-surface-removal.md)가 대체한다. Worker egress 정책의 현재 배포 증거는 provider-neutral Helm이다.
Outbound enforcement correction: Decision 12와 잔여 위험의 direct public 80/443 경계는 [ADR-0072](ADR-0072-outbound-proxy-only-network-enforcement.md)가 대체한다. Generic HTTP의 application port, public 80 호환성과 request/response 계약은 유지한다.


## 배경

Generic HTTP node는 사용자 설정 URL을 대상으로 Workflow Worker에서 HTTP 요청을 수행한다. 기존 구현은 provider와 node가 `httpx.Client`를 직접 만들고 URL의 기본 형식만 확인해 loopback, private network, link-local, cloud metadata와 DNS rebinding 목적지로 연결될 수 있었다. 검증을 추가하더라도 hostname을 실제 연결 시점에 다시 해석하면 validation-to-dial TOCTOU가 남는다.

이 경로는 [ADR-0035](ADR-0035-external-effect-idempotency-boundary.md)의 durable external-effect attempt와 canonical request 계약을 사용한다. 따라서 SSRF 차단을 위해 기존 HTTPX 직렬화, 정상 response shape와 전송 전·후 실패 분류를 임의로 바꾸면 안 된다. 애플리케이션 검증을 우회하는 향후 코드에 대비해 Worker 배포 경계에도 별도의 방어가 필요하다.

## 결정

1. Workflow Engine application 계층에 transport-neutral `OutboundHttpPort`를 둔다. `HttpRequestNode`와 `GenericHttpEffectAdapter`는 HTTP client, socket, DNS resolver 또는 concrete egress policy를 직접 생성하지 않는다. Production composition만 guarded adapter를 조립한다.
2. Shared `OutboundEgressGuard`를 URL과 address 정책의 source of truth로 사용한다. Generic HTTP는 `http|https`, `GET|POST|PUT|PATCH|DELETE`, public 80/443만 허용하고 URL userinfo/fragment, hop-by-hop header, proxy 제어 header와 request가 지정한 `Accept-Encoding`을 거부한다.
3. hostname의 모든 A/AAAA 결과를 검사한다. 하나라도 loopback, private, link-local, multicast, reserved, unspecified, carrier-grade NAT, documentation/benchmark 또는 metadata를 포함한 차단 network에 속하면 전체 요청을 fail-closed한다. 차단 network 집합은 Python runtime의 `is_private` 분류 변화에 의존하지 않도록 명시한다.
4. 검증된 전체 IP 목록을 DNS/OS 순서대로 실제 TCP destination 후보로 사용한다. Custom httpcore network backend는 원래 hostname을 다시 DNS 조회하지 않고, request byte가 전송되지 않은 TCP connect 실패에만 다음 검증 IP를 시도한다. 연결 peer가 현재 선택한 IP와 정확히 일치하는지 request byte 전송 전에 확인하며, 정책 거부나 peer mismatch에서는 폴백하지 않고 stream을 닫는다.
5. HTTPS TCP destination은 IP로 고정하되 TLS SNI와 certificate hostname 검증에는 원래 origin hostname을 유지한다. `verify=false`, request-controlled CA와 HTTPS downgrade 우회는 제공하지 않는다.
6. HTTPX client는 `trust_env=false`, `follow_redirects=false`, TLS verification을 강제한다. 3xx는 후속 hop을 호출하지 않고 기존 Generic HTTP response로 반환한다.
7. timeout은 최대 30초, request header는 최대 50개·이름 128 bytes·값 8 KiB·총 16 KiB, request body는 1 MiB, response body는 10 MiB로 제한한다. Response는 raw stream을 제한 크기까지만 읽고 `Accept-Encoding: identity`를 강제하며 압축 응답은 허용하지 않는다.
8. Adapter 오류는 전체 URL, query, header, body, resolved IP와 내부 exception을 포함하지 않는 safe code와 failure phase만 application port에 전달한다. [ADR-0035](ADR-0035-external-effect-idempotency-boundary.md)에 따라 Generic HTTP의 parsed hostname+명시 port인 `host`와 query/userinfo가 없는 `path` trace는 유지한다. 정책 거부와 peer mismatch는 non-retryable before-send, 전송 전임이 증명되는 일시 DNS/connect 실패만 retryable before-send, response 수신 또는 read 이후 실패는 outcome unknown이다.
9. Provider는 위 phase를 기존 external-effect 결과로 mapping한다. 정책 거부는 `invalid_prepared_request`, 전송 전 일시 연결 실패는 `connection_failed`, 전송 뒤 결과 상실은 `outcome_unknown`을 사용하며 신규 public error code를 추가하지 않는다.
10. 정상 public 요청의 HTTPX V1 canonical request digest, JSON serialization, 3xx/4xx/5xx를 포함한 status-agnostic transport 성공과 `status`, `data`, `headers` output을 유지한다.
11. 구현은 기존 wire semantics를 보존하기 위해 HTTPX 0.28과 httpcore 1.0의 custom network backend 조립을 사용한다. `httpx>=0.28.1,<0.29`, `httpcore>=1.0.9,<1.1`을 direct dependency로 고정하고 compatibility test로 내부 pool 조립 회귀를 탐지한다.
12. Helm production의 후속 network enforcement는 ADR-0072이 소유한다. Workflow Worker는 DNS, configured PostgreSQL port, Redis 6379, Sandbox 8194와 internal Squid 3129만 직접 허용하고 public 80/143/443/993 direct route를 두지 않는다. Generic HTTP/HTTPS와 address-pinned IMAP tunnel은 source/listener ACL이 분리된 Squid를 통과한다. 외부 dependency CIDR은 해당 service port에만 적용하며 public catch-all CIDR은 거부한다. 배포 cluster는 `NetworkPolicy`를 실제 집행하는 CNI를 사용해야 한다.

표준 Kubernetes `NetworkPolicy`는 FQDN이나 같은 Worker process 안의 승인 adapter와 우회 client를 구분하지 못한다. 또한 policy는 additive이고 node-local/`hostNetwork` 트래픽에는 CNI별 예외가 있을 수 있다. 따라서 이번 정책을 public 허용 port의 모든 direct dial을 proxy-only로 강제하는 것으로 표현하지 않는다. 완전한 proxy-only 경계는 전용 egress proxy 또는 FQDN-aware CNI와 admission control을 포함한 후속 아키텍처 결정으로 다룬다.

## 검토한 대안

### Provider 내부에 URL 검사만 추가

거부한다. 검증 후 hostname 재해석을 막지 못하고 node/provider마다 정책이 복제되며, 향후 다른 client가 쉽게 우회할 수 있다.

### 모든 Workflow outbound를 한 번에 proxy로 이전

장기적으로 더 강한 경계지만 Generic HTTP 보안 수정을 Connector, Mail, Slack, GitHub와 cluster egress 인프라 전환까지 확대한다. MBA-283에서는 application port와 Worker private-destination 방어를 먼저 확정하고 전면 proxy 전환은 후속 작업으로 남긴다.

### Redirect를 hop마다 재검증하며 추적

제품 기능으로 가능하지만 현재 Generic HTTP는 3xx를 response로 반환할 수 있고 redirect 추적이 필수 요구가 아니다. 이번에는 redirect를 추적하지 않아 private hop과 header 전달 위험을 제거하고 기존 status output을 유지한다.

### HTTP protocol과 TLS를 직접 구현

거부한다. 새 parser·serializer·TLS surface를 만들고 기존 canonical request와 HTTPX 동작을 깨뜨릴 위험이 크다. HTTPX/httpcore 호환 범위를 제한하고 adapter test로 조립 지점을 감시한다.

## 영향

- Generic HTTP node/provider는 application outbound port 밖에서 네트워크를 생성하지 않는다.
- SSRF, 혼합 DNS 결과와 DNS rebinding은 request 전에 차단되며 검증 IP와 실제 peer가 결합된다.
- 기존 external-effect idempotency, retry/no-replay와 정상 response 계약은 유지된다.
- Worker는 애플리케이션 guard와 NetworkPolicy의 두 방어선을 갖는다.
- 신규 endpoint, DB migration, 저장 graph shape와 public error code는 없다.
- Helm 또는 활성 EKS manifest를 배포하는 환경은 필요한 external DB/Redis/Sandbox CIDR을 정확히 설정하고 CNI enforcement를 확인해야 한다.

## 잔여 위험과 후속 검토

- Generic HTTP 외의 GitHub 및 다른 Workflow outbound adapter는 같은 application port로 아직 이관되지 않았다.
- 표준 `NetworkPolicy`는 CNI 집행이 없거나 같은 pod를 선택하는 별도 allow-all egress policy가 있으면 제한이 합산되어 direct dial 차단이 약화될 수 있다. ADR-0072의 실제 CNI 음성 probe를 완료 조건으로 사용한다.
- node-local resolver, node metadata proxy, `hostNetwork`와 CNI 구현별 정책 적용 차이는 cluster 배포 점검이 필요하다.
- Generic HTTP의 raw response `data`/`headers` output allowlist와 trace redaction 확대는 별도 보안 결정이 필요하다.
- private SaaS endpoint, 조직별 destination allowlist, private CA와 FQDN-aware policy는 이번 범위에서 지원하지 않는다.
