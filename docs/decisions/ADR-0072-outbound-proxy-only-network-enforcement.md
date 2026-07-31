# ADR-0072: Outbound proxy-only 네트워크 강제 경계

Status: Accepted

## Context

ADR-0067은 LLM, Knowledge와 Workflow 외부 HTTP 호출을 server-owned operation profile과 guarded transport에 연결했다. 이 계층은 URL, method, redirect, 크기, timeout과 목적지 주소를 요청 전에 검증하지만, 같은 workload 안에서 새 HTTP client나 SDK가 직접 socket을 열면 애플리케이션 guard를 우회할 수 있다.

기존 Docker Compose의 Squid는 ambient proxy 환경변수와 외부 연결 가능한 공용 bridge에 의존했다. Provider-neutral Helm에는 Squid workload가 없었고 Workflow Worker NetworkPolicy는 public 80/443 direct egress를 허용했다. 표준 Kubernetes NetworkPolicy는 여러 policy의 allow 규칙이 합쳐지며 CNI가 실제로 집행해야 하므로 YAML 존재만으로 proxy-only를 보장할 수 없다.

이 결정은 애플리케이션 의미 정책을 Squid로 옮기려는 것이 아니다. Application guard와 네트워크 강제를 함께 적용해 하나의 계층이 우회되어도 다른 계층이 외부 HTTP/HTTPS direct dial을 막도록 한다.

## Options considered

### 1. 애플리케이션 guard만 유지한다

- 장점: 배포 변경이 작고 destination IP를 고정하는 현재 transport를 유지한다.
- 단점: ad hoc client와 SDK가 공통 factory를 우회하면 물리적인 차단 경계가 없다.

### 2. Squid와 NetworkPolicy만 사용한다

- 장점: workload의 direct public socket을 제한할 수 있다.
- 단점: SSL bump를 사용하지 않는 Squid는 HTTPS path, header, body, actor와 operation 권한을 판단할 수 없다. NetworkPolicy만으로 DNS rebinding과 provider별 payload 계약도 해결할 수 없다.

### 3. Application guard, explicit proxy transport와 proxy-only network를 결합한다

- 장점: 의미 정책과 물리 경계를 분리하면서 우회와 장애 시 direct fallback을 차단한다.
- 단점: proxy HA, CNI 검증, rollout과 운영 책임이 추가된다.

## Decision

Option 3을 채택한다.

1. Application guard는 operation, method, origin, redirect, timeout, request/response cap과 safe failure의 유일한 의미 정책 권위다. Squid와 NetworkPolicy는 이 판단을 대체하지 않는다.
2. 외부 HTTP/HTTPS transport는 server-owned `proxy_guarded_external` mode와 exact internal proxy endpoint를 사용한다. `HTTP_PROXY`, `HTTPS_PROXY`, `ALL_PROXY`, `NO_PROXY`는 정책 입력으로 사용하지 않으며 HTTP client는 `trust_env=false`와 retry 0을 사용한다.
3. Production Gateway, Workflow Worker와 Knowledge Worker는 proxy mode/revision/endpoint가 없거나 잘못되면 작업 수락 전에 fail-closed한다. Proxy 연결 또는 tunnel 실패 뒤 direct transport로 fallback하지 않는다.
4. Direct-pinned mode는 local/test와 exact internal 또는 별도 non-HTTP adapter에만 남는다. Production public HTTP workload는 direct mode로 시작할 수 없다.
5. Squid는 세 내부 listener를 제공한다.
   - `3128`: Gateway와 Knowledge Worker의 HTTPS CONNECT 443 전용
   - `3129`: Workflow Worker의 HTTPS CONNECT 443, Generic HTTP 호환 public 80과 address-pinned IMAP CONNECT 143/993
   - `3130`: Gateway·Knowledge·Workflow의 Connector DB/SSH CONNECT 전용. Application이 검증한 public IP와 배포 관리 포트 allowlist만 authority로 허용한다.
   `3128/3129`는 immutable `proxy-v1`, `3130`은 immutable `connector-egress-v1` 계약이다. Listener port는 Squid config, Service, workload 환경과 NetworkPolicy가 공유하며 Helm override로 변경할 수 없다. Connector target port allowlist는 최대 16개의 배포 관리 값으로 application, Squid와 proxy egress NetworkPolicy에 동일하게 렌더하고, Connector test allowlist가 이 집합을 벗어나면 startup 또는 Helm render를 실패시킨다. Source/listener 분리는 NetworkPolicy와 Squid ACL을 함께 사용한다. 이는 network-authorized source이며 cryptographic workload identity가 아니다.
6. Squid는 SSL bump/MITM을 사용하지 않는다. 원래 hostname SNI와 certificate 검증은 workload와 origin 사이의 CONNECT tunnel에서 유지한다.
7. Squid는 private, loopback, link-local, metadata, reserved와 multicast IPv4/IPv6 destination을 거부한다. Application과 Squid가 각각 DNS를 검증하며 CNAME 또는 A/AAAA 결과 중 하나라도 unsafe이면 전체 hostname을 거부한다.
8. Squid는 response cache와 request access log를 비활성화한다. Raw URL/query/header/body, credential, resolved IP와 provider exception은 proxy, application log, trace, audit와 metric label에 저장하지 않는다. 운영 관측은 readiness, replica, resource와 safe reason bucket으로 제한한다.
9. Docker Compose는 application/internal dependency network를 `internal`로 만들고, Gateway·Knowledge·Workflow는 각각 proxy client network만 추가로 사용한다. Squid만 별도 egress-capable network에 연결하며 host에 proxy port를 publish하지 않는다.
10. Provider-neutral Helm은 immutable Squid image digest, internal ClusterIP Service, 최소 2개 production replica, `maxUnavailable: 0`, PDB, non-root/read-only/capability-drop와 resource bound를 제공한다.
11. Helm의 target workload egress는 DNS, Squid와 필요한 exact internal/dedicated protocol만 허용한다. Logger와 Beat는 DB/Redis만 사용한다. Proxy-only Frontend server는 같은 release의 bundled Gateway만 사용하며, Gateway 비활성화 또는 다른 `API_URL`은 그 대상에 맞는 별도 egress 좌표와 계약이 추가되기 전까지 render를 거부한다. Sandbox는 일반 Squid source가 아니며 기존 격리를 완화하지 않는다. 외부 DB·Redis CIDR의 필수값과 금지 범위는 특정 Worker 활성 여부가 아니라 이를 소비하는 chart 전체 기준으로 render 전에 검증한다. Kubernetes `ipBlock`은 API가 수용하는 canonical native IPv4/IPv6 CIDR만 사용하며 IPv4-mapped IPv6 prefix를 manifest에 넣지 않는다. Application guard와 Squid는 mapped range를 계속 명시적으로 차단한다. NetworkPolicy의 IPv4 제외가 mapped destination 우회를 막는지는 unrestricted control의 mapped 연결 성공과 보호 workload의 mapped public/private/metadata 연결 실패를 같은 실제 CNI probe에서 비교해 검증한다.
12. Platform PostgreSQL, Redis와 Sandbox control은 기존 exact internal service/port를 직접 사용한다. External Connector DB/SSH는 public direct route 대신 `3130`을 사용하며, application이 DNS 검증으로 고정한 IP와 배포 관리 포트만 CONNECT authority로 보낸다. Connector strict test, 저장 connection의 schema/runtime 사용과 Workflow SSH compatibility가 같은 transport를 사용하고 proxy 실패 뒤 direct fallback하지 않는다. PostgreSQL TLS hostname과 SSH host 의미는 원래 hostname을 유지한다. IMAP은 `3129`의 143/993 CONNECT tunnel에서 같은 address-pinning/hostname-TLS 원칙을 사용한다. Squid는 Connector credential, DB/SSH 권한 또는 protocol payload를 해석하지 않는다.
13. Rollout은 `nodease.io/egress-mode=proxy-v1` revision label을 사용한다. `canary` phase의 strict workload policy는 이 label이 있는 새 pod만 선택한다. `final` phase는 revision label을 selector에서 제거해 해당 component의 모든 pod를 선택하며, old pod가 모두 drain된 뒤에만 적용한다. Pod annotation에 phase를 기록한다.
14. Kubernetes 완료 판정은 정적 render만으로 하지 않는다. PR CI는 pinned kind/Kubernetes와 Calico IPv4에서 target direct HTTPS 실패, authorized proxy 성공과 unauthorized source 실패를 실행한다. Dual-stack과 실제 배포 CNI는 release 환경에서 같은 positive/negative probe를 통과해야 하며, 통과하지 않은 CNI는 지원 대상으로 간주하지 않는다.
15. Standard NetworkPolicy가 additive라는 사실은 변하지 않는다. Release manifest와 cluster의 다른 allow policy, `hostNetwork`, privileged workload와 CNI enforcement를 배포 전 확인한다. 이 검증을 실패하면 proxy-only activation을 중단한다.
16. Object storage SDK는 ambient 환경이 아니라 explicit proxy configuration을 사용한다. 같은 botocore session의 default client config에 proxy와 retry 정책을 주입해 S3 client뿐 아니라 workload identity 자격증명을 교환하는 nested STS client에도 동일하게 적용한다. Signed request와 provider business semantics는 storage adapter가 계속 소유한다.
17. 이 결정은 Docker Compose와 provider-neutral Helm만 변경하며 ADR-0068의 EKS 비지원 경계를 확장하지 않는다.
18. Proxy-only workload가 첫 요청에서 외부 package 자산을 내려받지 않도록 Gateway/Knowledge와 Workflow image는 사용하는 tiktoken encoding, NLTK corpus, runtime과 동일한 immutable E5 `(model_id, revision)` snapshot과 build에서 활성화한 CrossEncoder 모델을 build 단계에 포함한다. Workflow image는 build에 사용한 E5 identity와 CrossEncoder model ID를 final stage의 runtime 기본 환경으로 함께 고정하며 model identity override는 build와 runtime을 동시에 바꾸는 custom image build로만 허용한다. Runtime은 NLTK 또는 활성 모델 download를 호출하지 않으며 자산 누락 시 keyword 부가기능만 bounded warning으로 생략한다. 필수 tokenizer 또는 활성 reranker 자산이 없는 image는 배포 계약 실패다. Demo seed의 runtime embedding도 bare provider SDK 대신 shared operation-bound guarded client를 사용한다.
19. External raw parser는 Knowledge의 별도 approval revision, parser capability와 guarded transport가 모두 구현되기 전까지 지원 대상으로 간주하지 않는다. 현재 `llamaparse` strategy는 source fetch, credential lookup, SDK call과 local parser fallback 전에 `knowledge.raw_parser_egress_unavailable`로 fail-closed하며 raw document를 외부 parser에 전송하지 않는다.
20. Sandbox의 사용자 코드 외부 네트워크 접근은 현재 지원하지 않는다. `enable_network=true` 요청은 API에서 `422 sandbox.network_access_unsupported`로 거부하고 Scheduler, Executor와 NSJail command builder도 같은 중앙 정책을 다시 적용한다. Compose 환경변수나 내부 호출로 NSJail network namespace 격리를 해제할 수 없다. 향후 지원하려면 Sandbox 전용 guarded outbound port, 목적지·protocol 정책, NetworkPolicy와 audit/redaction 계약을 별도 결정해야 한다.
21. 모든 strict egress NetworkPolicy는 `egressProxy.networkPolicy.dns`의 단일 operator-owned DNS peer를 공유한다. 기본 namespace는 기존 동작과 같은 `kube-system`이며, cluster DNS가 다른 namespace에 있거나 전용 Pod selector가 필요한 환경은 `namespace`와 선택적 `podSelectorLabels`를 명시해야 한다. 값이 없거나 형식이 잘못되면 Helm render를 실패시킨다. NodeLocal DNS처럼 host-network 또는 CIDR 예외가 필요한 환경은 이 namespace/pod 계약으로 지원된 것으로 간주하지 않으며 별도 결정과 release probe 없이 broad DNS egress를 열지 않는다.
22. Proxy mode의 PostgreSQL Connector는 application guard의 기본 허용 포트를 `connector-egress-v1` dialer가 가진 deployment-managed allowlist에서 가져온다. Direct local/development mode의 기본값은 `5432`를 유지하고, 호출자가 명시한 더 좁은 포트 정책은 덮어쓰지 않는다. Blocking DB driver와 `gevent` Worker가 함께 실행되는 경우 local CONNECT relay는 monkey-patched greenlet이 아니라 OS-native thread/socket/select primitive를 사용해 relay 진행을 보장한다. Relay 밖에서 dialer를 직접 사용하는 SSH 같은 호출은 cooperative socket을 유지해 Worker event loop를 막지 않는다. 이 경계는 검증 IP와 원래 hostname TLS 의미를 바꾸거나 proxy 실패 뒤 direct 연결로 fallback해서는 안 된다.
23. External platform DB·Redis·Sandbox NetworkPolicy 예외는 private IPv4(RFC1918) 또는 IPv6 ULA network CIDR을 허용하되 각 주소군의 private 범위보다 넓게 확장할 수 없다. Public destination은 exact IPv4 `/32` 또는 IPv6 `/128` host만 허용한다. 여러 broad public CIDR을 조합한 catch-all, loopback, link-local, metadata, multicast, IPv4-mapped IPv6와 문법적으로 잘못된 CIDR은 Helm render 전에 거부한다. Public managed dependency가 동적 주소 범위를 요구하면 broad CIDR을 열지 않고 private connectivity 또는 별도 FQDN-aware egress 결정을 사용한다.

## Security and protected-resource boundaries

| Boundary | Result | Evidence |
| --- | --- | --- |
| 정책·설정 | 완료 | Typed transport mode, exact endpoint/host/port, immutable revision과 production startup validation |
| 관리 API·UI | 해당 없음 | Operator-owned deployment setting이며 사용자 destination 등록 기능을 추가하지 않는다. |
| Runtime/background | 완료 | Gateway lifespan, Workflow worker init/process init, Knowledge worker init/bootstep과 guarded HTTP/S3 composition |
| Authorization | 완료 | 기존 operation/resource authorization을 유지하며 proxy source 권한으로 대체하지 않는다. |
| 외부 I/O 전 fail-closed | 완료 | Invalid/missing config, unsafe DNS와 proxy failure에서 origin direct dial을 수행하지 않는다. |
| Secret·trace·audit | 완료 | Squid access/cache log 비활성, safe application error와 synthetic marker 계약 |
| Lifecycle·HA | 완료 | 최소 replica, rolling bound, PDB, readiness/liveness와 canary/final selector 전환 |
| Network enforcement | 완료(기준 환경) | Compose disposable network test와 pinned kind+Calico IPv4 CI probe |
| Dual-stack·운영 CNI | Release gate | 실제 배포 환경에서 같은 probe를 통과해야 하며 미검증 환경은 활성화하지 않는다. |
| 비-HTTP protocol | 완료(IMAP·Connector) | IMAP은 ADR-0031 address pinning을 보존한 Worker-only 143/993 tunnel, external Connector DB/SSH는 validated-IP·deployment-port `3130` tunnel을 사용한다. Platform DB/Redis/Sandbox는 exact internal 경계를 유지한다. |
| Connector runtime composition | 완료 | Proxy mode Postgres runtime은 dialer의 deployment-managed port allowlist를 사용하고, `gevent` Worker의 blocking DB driver는 OS-native local relay로 전달된다. |
| External dependency CIDR | 완료 | Private network 범위 또는 exact public host만 허용하고 split catch-all과 unsafe address range는 Helm render 전에 거부한다. |
| Runtime package asset | 완료 | tiktoken encoding과 NLTK corpus를 image build에서 적재하고 runtime download를 금지한다. |
| External raw parser | 현재 미지원·fail-closed | Approval/guarded transport가 완성되기 전 source fetch·credential·SDK 호출 없이 safe reason으로 종료한다. |
| Sandbox 사용자 코드 network | 현재 미지원·fail-closed | API·Scheduler·Executor·NSJail command builder가 `enable_network=true`를 거부하고 Compose runtime override를 제공하지 않는다. |
| Cluster DNS 좌표 | 완료(namespace/pod mode) | 모든 strict policy가 한 Helm helper를 사용하며 기본값과 operator override를 실제 render로 검증한다. NodeLocal/CIDR mode는 release gate 이전 미지원이다. |

## Consequences

- External HTTP/HTTPS, production IMAP과 external Connector DB/SSH는 각각의 application guard와 분리된 Squid listener를 모두 통과한다.
- Proxy 장애는 해당 외부 operation의 가용성을 낮추지만 direct path로 보안을 완화하지 않는다.
- HTTPS payload inspection은 제공하지 않는다. Squid만으로 operation authorization이 완료됐다고 판단할 수 없다.
- NetworkPolicy를 집행하지 않거나 additive allow policy가 있는 cluster는 동일 chart를 렌더할 수 있어도 지원 환경이 아니다.
- Local Helm profile은 개발 편의를 위해 direct-pinned mode를 유지할 수 있지만 production readiness 증거로 사용할 수 없다.

## Affected files

- `apps/shared/services/outbound_proxy_policy.py`
- `apps/shared/services/guarded_http_transport.py`
- `apps/shared/services/connector_tcp_transport.py`
- `apps/gateway/lifespan.py`
- `apps/gateway/knowledge_worker*.py`
- `apps/gateway/services/storage.py`
- `apps/workflow_engine/outbound_proxy_startup.py`
- `docker/docker-compose.yml`
- `docker/gateway/Dockerfile`
- `docker/workflow_engine/Dockerfile`
- `docker/proxy/*`
- `infra/helm/moduly/*`
- `.github/workflows/pr-quality-gate.yml`
- `tests/ci/test_egress_proxy_*.py`
- `docs/architecture.md`
- `docs/engineering/outbound-proxy-rollout.md`
- `docs/features/deployment/*`
- `docs/features/workflow/*`

## Follow-up review notes

- 새 public HTTP adapter나 workload를 추가할 때 operation registry, explicit proxy composition, workload inventory와 NetworkPolicy probe를 같은 변경에서 갱신한다.
- 새 Connector DB/SSH port를 지원할 때 application, Squid와 NetworkPolicy가 소비하는 deployment allowlist를 함께 갱신하며 일반 HTTP/HTTPS listener로 우회하지 않는다.
- Dual-stack 또는 다른 CNI를 공식 지원하려면 실제 positive/negative evidence를 runbook의 지원 matrix에 추가한다.
- mTLS/service mesh workload identity, TLS inspection과 arbitrary organization destination 등록은 본 결정의 확장이 아니며 별도 ADR이 필요하다.
