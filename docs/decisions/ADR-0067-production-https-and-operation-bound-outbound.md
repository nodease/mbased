# ADR-0067: 프로덕션 HTTPS와 operation-bound outbound 경계

Status: Accepted

Related ADRs: ADR-0050, ADR-0057, ADR-0064

Outbound enforcement completion: Decision 10과 결과의 proxy-only 잔여 위험은 이 결정 당시 상태이며, 현재 network 경계는 [ADR-0072](ADR-0072-outbound-proxy-only-network-enforcement.md)가 대체한다. Application operation policy와 guarded request 계약은 계속 유지한다.

## 배경

프로덕션 Frontend 설정이 browser bundle에 cluster 내부 HTTP Gateway 주소를 공개할 수 있었고, Gateway CORS 설정도 환경에 따른 HTTPS 불변식을 강제하지 않았다. 또한 LLM provider, Knowledge API·문서 fetch와 Workflow 원격 파일 추출은 서로 다른 HTTP client를 사용해 URL 사전 검증과 실제 dial 사이의 DNS 재해석, ambient proxy, response size와 오류 redaction 정책이 일관되지 않았다.

ADR-0050은 사용자 설정 Generic HTTP node의 별도 application port와 HTTP 호환 계약을 정의한다. MBA-178은 이를 변경하지 않고 credential 또는 민감한 body를 전송하는 server-owned operation과 프로덕션 browser 경계를 좁게 강화해야 한다.

## 결정

1. 프로덕션 browser API 기본 주소는 same-origin `/api/v1`이다. `NEXT_PUBLIC_API_URL`을 명시할 때는 공개 HTTPS origin만 허용하고 path, query, fragment, userinfo, wildcard, loopback, private·cluster hostname을 거부한다. 일반 `/api`는 Ingress 또는 repository Nginx가 Gateway로 직접 라우팅하고, SSE Next.js server route만 cluster 내부 HTTP `API_URL`을 사용할 수 있다. `API_URL`은 public 변수로 fallback하거나 browser bundle로 projection하지 않는다. Provider-neutral production Helm reference는 미설정 ingress를 기본 비활성화하며, operator가 외부 HTTPS termination, trusted proxy와 `/api` Gateway route를 함께 제공해야 한다.
2. Gateway의 credentialed CORS allowlist는 production과 알 수 없는 환경에서 공개 HTTPS origin만 허용한다. Development/test는 loopback HTTP만 예외로 허용하며 wildcard, userinfo, path, query와 fragment는 모든 환경에서 거부한다. CORS는 인증이나 non-browser caller 차단 수단이 아니다.
3. Shared outbound registry는 operation ID별 immutable profile과 결정론적 revision을 소유한다. Profile은 허용 method·scheme·port, request/response 상한, content type, timeout과 redirect 수를 고정한다. 알 수 없는 operation, HTTP 또는 비표준 port는 민감 operation에서 network I/O 전에 fail-closed한다.
4. Guarded transport는 요청 전에 모든 DNS 결과를 public address 정책으로 검증하고 그중 검증된 주소로 직접 연결한다. Sync와 async transport의 DNS 검증은 bounded worker에서 수행하며 DNS 조회와 복수 IP TCP 연결 시도는 profile connect timeout의 단일 deadline을 공유한다. Async 경로는 event loop를 차단하지 않는다. 연결 peer가 선택한 주소와 다르거나 HTTP `Host` authority가 URL authority와 다르면 request를 보내지 않으며 TLS SNI와 certificate hostname 검증은 원래 host를 유지한다. Environment proxy와 transport retry는 사용하지 않고 response stream을 profile 상한으로 제한한다. Remote response를 받은 뒤 header/body 검증이 실패하면 전송 전 실패로 되돌리지 않고 outcome unknown으로 전달한다.
5. Operation binding은 승인 origin 안의 path와 query를 허용하되 userinfo와 fragment를 거부한다. 이는 API query와 provider가 발급한 서명 URL을 보존하기 위한 것이며 query는 log·trace·audit에 저장하지 않는다. Redirect는 origin allowlist를 DNS 조회보다 먼저 적용하고 HTTPS downgrade, private destination과 cross-origin hop을 거부한다. Cross-origin redirect가 필요한 provider 또는 document source는 별도의 server-owned endpoint policy 없이는 허용하지 않는다.
6. LLM generation·embedding과 model discovery는 별도 profile을 사용한다. Credential row의 저장 당시 `baseUrl`은 목적지 권위가 아니며 현재 server-owned Provider catalog endpoint와 API key만 materialize한다. Provider execution capability의 egress fingerprint에는 현재 LLM transport profile revision을 포함해 profile 변경 뒤 stale capability가 provider 호출 전에 거부되게 한다. Provider가 요청을 처리했을 수 있는 response header/body 거부와 read/write 결과 불명은 concrete invocation adapter가 Workflow application 오류 `outcome_unknown`으로 번역한다. LLM node는 Shared client 예외 타입에 의존하지 않고 이 application 계약으로 fallback provider 호출과 Workflow task 자동 재시도를 금지한다. Legacy Completions 전환은 legacy model과 redacted endpoint-unsupported 응답이 함께 확인된 경우에만 허용하고, billable response validation 실패나 Responses-native model에는 적용하지 않는다.
7. Knowledge API source, remote document fetch·preview와 Workflow FileExtraction 원격 파일은 명시 operation profile과 guarded transport를 사용한다. Workflow node에는 concrete HTTP client를 주입하지 않고 원격 파일 application port만 전달한다. 원격 파일은 2xx response만 문서로 수락하며 그 밖의 status body는 기록하거나 parser에 전달하지 않는다. 수락한 response body는 전체를 memory에 적재하지 않고 profile byte 상한을 적용하며 임시 파일로 직접 streaming하고, 실패한 partial file은 제거한다.
8. Policy denial과 provider/network failure는 safe reason code, status와 phase만 상위 계층에 전달한다. Credential header, prompt, body, document content, raw URL, resolved IP와 provider raw response·exception을 로그, trace, audit 또는 API 오류에 저장하지 않는다.
9. Google OAuth token exchange/refresh, Gmail profile/message/modify/draft, GitHub pull request read/comment와 Slack API/webhook의 fixed-SaaS 호출은 MBA-356의 operation-bound requester와 guarded transport를 사용한다. 각 operation은 server-owned ID와 endpoint profile에 묶이며 application service와 node는 concrete HTTP client를 생성하지 않는다. Gmail message 목록·상세 조회처럼 같은 operation과 승인 origin에 bounded fan-out이 있는 작업은 context-managed guarded session 안에서 연결 풀을 재사용하되 각 request URL을 network I/O 전에 다시 검증한다. Provider path/body 검증, 직렬화된 전체 request body 상한, 응답 분류, idempotency와 outcome 판정은 기존 provider adapter와 effect ledger가 계속 소유한다. 이 이관은 Generic HTTP, 내부 service 통신 또는 아직 등록되지 않은 adapter까지 platform 전체 outbound가 중앙화되었다는 뜻이 아니다.
10. Application guard가 operation 의미와 요청 정책의 권위자다. 기존 NetworkPolicy와 Squid는 독립된 보조 방어선이며 public 80/443의 direct dial을 물리적으로 차단하는 proxy-only 보장은 아니다. 전용 egress proxy, DNS/CONNECT/TLS 책임, HA와 admission enforcement는 MBA-357과 후속 ADR이 소유한다.
11. ADR-0050의 Workflow Generic HTTP는 기존 호환성을 위해 public HTTP 80을 계속 지원한다. MBA-178의 HTTPS-only profile을 Generic HTTP나 내부 Sandbox 통신에 암묵적으로 적용하지 않는다.

## 검토한 대안

### 모든 outbound를 제품 Gateway가 대리

Worker의 credential, prompt, mail과 document body가 Gateway에 집중되고 병목과 단일 장애점이 생긴다. 각 process의 operation port가 같은 Shared guarded transport primitive를 조립하도록 한다.

### URL 사전 검사 후 일반 HTTP client 사용

검사와 연결 사이에 hostname이 다시 해석되어 credential이나 body가 private destination으로 전송될 수 있다. 실제 dial 주소와 peer를 요청 전에 고정한다.

### MBA-178에서 proxy-only 네트워크까지 구현

Application semantic policy와 cluster topology는 변경·검증·rollback 단위가 다르며 address-pinned direct transport는 CONNECT proxy와 그대로 결합되지 않는다. 네트워크 강제는 MBA-357로 분리한다.

## 결과

- Browser bundle에 cluster 내부 HTTP 주소가 들어가지 않고 프로덕션 API 호출은 public HTTPS ingress를 사용한다.
- LLM, Knowledge, Workflow 원격 파일과 MBA-356 대상 Google OAuth/Gmail/GitHub/Slack fixed-SaaS 요청은 검증된 public address와 server-owned endpoint 정책에 binding된다.
- Profile 변경은 provider execution capability를 stale 처리하지만 외부 provider 호출의 exactly-once나 business retry를 새로 보장하지 않는다.
- MBA-356 대상 fixed-SaaS adapter는 operation-bound transport로 이관됐지만, 등록되지 않은 adapter의 직접 연결과 proxy-only network enforcement는 잔여 위험이다. 후자는 MBA-357이 소유한다.
