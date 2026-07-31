# Deployment Test Cases

Status: Draft

## MBA-357 Outbound Proxy-Only Contracts

- Production proxy mode는 exact internal host와 HTTP listener `3128/3129`, Connector listener `3130`만 허용하고 blank, public, localhost/loopback/link-local, userinfo, path/query/fragment, stale revision과 direct mode를 network I/O 전에 거부한다. Ambient proxy와 broad `NO_PROXY`는 결과를 바꾸지 않는다.
- Guarded sync/async transport는 application URL/DNS policy를 proxy 연결 전에 다시 평가하고 Squid만 dial한다. Proxy connection/tunnel failure 뒤 origin direct dial은 0회이며 HTTPS SNI/certificate hostname, response cap, stream/cancellation과 safe error 계약을 유지한다.
- S3/object storage client는 explicit proxy 설정 또는 explicit empty proxy map을 사용하며 ambient environment를 신뢰하지 않는다. Invalid proxy config에서는 provider client 생성과 signed request가 0회다.
- Squid config는 pinned image, CONNECT 443, Workflow-only HTTP 80 listener, IMAP 143/993와 Connector 전용 `3130` listener의 배포 관리 포트 allowlist, final deny-all, private/reserved/metadata IPv4/IPv6 deny, no SSL bump, no cache와 `access_log none`을 검증한다.
- Compose는 Gateway·Knowledge·Workflow가 egress-capable network에 직접 연결되지 않고 Squid만 egress network를 사용하며 host-published listener가 없는지 검증한다. Disposable runtime은 safe origin 성공, mixed A/AAAA/CNAME/rebind 차단, unauthorized source 차단, direct dial과 proxy-down fallback 실패를 확인한다.
- Helm render는 Squid Deployment/Service/PDB, immutable digest, minimum replica, security context, resource bounds, listener별 ingress와 workload별 egress를 검증한다. Logger/Beat/Frontend에는 public route와 proxy access가 없어야 한다. Proxy-only Frontend는 exact bundled Gateway URL만 허용하고 Gateway 비활성 또는 alternate `API_URL`을 render 전에 거부한다.
- Canary render의 workload selector에는 `nodease.io/egress-mode=proxy-v1`이 있고 final render에서는 없어야 한다. Pod phase annotation, `maxUnavailable=0`, `maxSurge=1`과 PDB를 함께 확인한다.
- Remote CI는 pinned kind/Kubernetes와 Calico IPv4에서 target direct HTTPS 실패, authorized Squid HTTPS/Connector 성공과 Sandbox-shaped unauthorized source 실패를 실제 실행한다. Unrestricted control에서 IPv4-mapped public/private 연결이 가능한지 먼저 증명한 뒤 보호 workload의 mapped public/private/metadata direct 연결 실패를 확인한다. Dual-stack과 실제 운영 CNI는 release probe이며 미검증 환경을 지원으로 표시하지 않는다.
- Image contract는 `cl100k_base`/`o200k_base`, NLTK `punkt`/`punkt_tab`/`stopwords`, runtime 기본값과 동일한 immutable E5 `(model_id, revision)` snapshot과 opt-in CrossEncoder model이 build 단계에 적재되는지 검증한다. E5와 CrossEncoder build identity는 final stage runtime 환경에 유지되어야 하며 ingestion runtime source에는 `nltk.download`가 없어야 한다.
- External parser readiness가 없는 `llamaparse` 요청은 source fetch, credential lookup, SDK 호출과 PyMuPDF fallback이 모두 0회이고 preview와 durable ingestion 모두 exact safe reason `knowledge.raw_parser_egress_unavailable`를 보존해야 한다.
- Sandbox `enable_network=true` 요청은 Scheduler 조회와 큐 변경 전에 HTTP `422`와 `sandbox.network_access_unsupported`만 반환해야 한다. 내부 Scheduler·Executor·NSJail command builder 호출도 같은 요청을 거부하고, NSJail command에는 `--disable_clone_newnet`이 없어야 하며 Compose는 `SANDBOX_ENABLE_NETWORK` override를 제공하지 않아야 한다.
- Synthetic secret marker를 application/proxy logs와 failure output에 넣어도 URL/query/header/body, credential, resolved IP와 raw provider exception이 남지 않아야 한다.

## Unit Tests

- App secret generator는 호출마다 최소 256-bit entropy의 bounded ASCII token과 common redactor가 free-text에서 식별할 수 있는 고정 marker를 만들고 repr/log helper에 원문을 포함하지 않는다.
- Domain-separated verifier는 같은 candidate에 안정적인 fixed-length 결과를 만들며 current/유효 previous를 constant-time 경로로 검증한다. Unknown algorithm, malformed state, non-ASCII, 0/513-byte candidate와 expiry 경계 `now >= previous_valid_until`은 fail-closed한다.
- Rotation state transition은 최초 `0 -> 1`, 일반 `N -> N+1`, 즉시 previous 폐기, 기존 previous 교체, stale expected version과 두 경쟁 요청의 단일 winner를 검증한다.
- Audit metadata sanitizer는 secret, verifier, candidate, prefix, 길이, Authorization과 fingerprint를 허용하지 않는다.
- API/Webhook 배포 input 단계에서 발급한 one-time secret은 optimization·success 단계까지 메모리로 전달되어 즉시 테스트에 사용할 수 있고, 단계 전환 중 초기화되거나 browser storage에 저장되지 않는다. 모달 close/reopen은 해당 값을 폐기한다. 기존 configured App의 success 테스트는 사용자가 입력한 기존 secret을 component memory에서만 사용하며 storage나 App/Deployment state에 저장하지 않는다.

- Readiness inventory는 object가 아닌 JSONB policy와 malformed/unknown contract를 예외로 중단하지 않고 `malformed`로 집계하며 raw policy 값을 출력하지 않는다.
- Deployment application package는 FastAPI, SQLAlchemy, DB model, concrete adapter/service/composition module을 import하지 않는다.
- Pure preflight use case는 repository port snapshot만으로 결과를 만들고 active blocker는 HTTPException이 아닌 typed `DeploymentPreflightBlocked`를 반환한다. Compatibility facade만 이를 기존 409 envelope으로 mapping한다.
- Preflight graph scanner는 shared strict parser로 root/embedded LLM node의 direct KB와 Collection 목록을 검사한다. Malformed shape와 20개 초과는 active/inactive 여부와 무관한 fixed non-downgradable blocker다.
- Embedded `subGraph` 순회는 반복 방식이며 호출 스택보다 깊은 입력에서도 같은 Collection audience policy를 적용한다.
- Selected Collection repository projection은 selected IDs, active organization, active lifecycle, `sync_state != source_deleted`로 제한하고 same-organization active/retrieval-visible KB aggregate만 계산한다. Direct KB와 Collection child preflight는 ADR-0070 enforcement 뒤 current-valid compliant manifest 또는 frozen/current platform·Organization validity epoch equality와 cutoff를 통과한 eligible pre-cutoff legacy artifact가 없는 KB를 `knowledge_privacy_artifact_unavailable`로 처리한다. Child ID를 application result로 반환하거나 Collection별 N+1 query를 만들지 않는다.
- Anonymous-public preflight는 private Collection과 source-managed Collection/member를 fail-closed하고, public manual Collection은 통과시킨다. Missing/inactive/cross-org는 하나의 generic unavailable code로 처리한다.
- Candidate budget 가능성은 bucket/boolean warning으로만 반환되고 active create를 차단하지 않는다. Client success step은 warning을 text status로 표시한다.
- MBA-219 node configuration evaluator는 FastAPI, SQLAlchemy, concrete service와 catalog loader를 import하지 않고 composition이 주입한 immutable node side-effect mapping과 resource snapshot port만 사용한다.
- Mail/Gmail Draft/Mail Acknowledge/Slack은 managed validator, LLM/HTTP/GitHub는 runtime-authoritative로 명시된다. External node가 registry에 없거나 `implemented=false`이면 실행 표면에서 `node_configuration_validator_unavailable`로 차단한다.
- Gateway Google login OAuth metadata/token/userinfo/JWKS는 세션마다 새 operation-bound guarded transport를 사용하고 ambient proxy를 무시한다. Helm NetworkPolicy의 내부·외부 PostgreSQL 허용 port는 application `DB_PORT`와 동일한 canonical chart helper에서 렌더한다.
- Kubernetes NetworkPolicy CIDR은 API-canonical native IPv4/IPv6 형식이어야 하고 IPv4-mapped IPv6 prefix를 직접 넣지 않는다. Application/Squid mapped-range 차단과 pinned Calico의 direct destination negative probe를 함께 검증한다.
- 외부 PostgreSQL 또는 Redis를 사용하는 proxy-only chart는 `worker.enabled=false`여도 해당 dependency CIDR 누락을 Helm render 단계에서 거부한다.
- External DB·Redis·Sandbox CIDR은 RFC1918/ULA private network와 exact public `/32`·`/128` host를 허용한다. `0.0.0.0/1`과 `128.0.0.0/1` 같은 split catch-all, public network prefix, private block보다 넓은 supernet, loopback, link-local, metadata, multicast, mapped IPv6와 invalid CIDR은 Helm render가 같은 safe fixed error로 거부해야 한다.
- 최상위 graph와 다단계 Loop `subGraph`, WorkflowNode target graph의 managed node를 같은 audience/principal로 검사한다. Malformed nested graph와 nesting 한도 초과는 `workflow_graph_invalid`로 차단한다.
- Runtime audience resolver는 `api`, `webapp`, `widget`, `chatbot`, `mcp`, `schedule`, `webhook`를 anonymous public-only로 판정한다.
- `internal_chatbot`은 authenticated run/run-info surface에서만 허용하고 public info와 public app run surface에서는 fail-closed로 거부한다.
- `internal_chatbot` preflight는 server-derived audience를 `authenticated_user`로 판정하며 private KB 참조만으로 활성 배포를 차단하지 않는다. 다만 repository를 조회해 direct KB와 Collection의 organization/lifecycle/sync/retrieval readiness를 검증하고 unavailable reference는 차단한다.
- `internal_chatbot` authenticated run은 current user를 `execution_subject`로 Runtime에 전달하고, 챗봇 `memory_mode`와 deployment/user 기준 conversation namespace를 적용한다.
- `workflow_node` direct public/API/webhook/authenticated run execution is rejected. Subworkflow audience resolver는 parent execution subject를 상속하고, subject가 없으면 anonymous public-only로 판정한다.
- Workflow-node target resolver는 `workflowNode.data.appId`를 사용하고 `workflowId`로 target app을 찾지 않는다.
- Workflow-node target app이 존재하고 `active_deployment_id`가 있어도 active deployment의 `type`이 `workflow_node`가 아니면 `workflow_node_target_unavailable`로 처리한다.
- Workflow-node target이 pending active candidate graph를 가리킬 때도 candidate deployment type이 `workflow_node`가 아니면 `workflow_node_target_unavailable`로 처리한다.
- Workflow-node target unavailable, cycle, depth cap 초과는 `workflow_node_inherited` audience나 inactive preview에서도 warning으로 낮추지 않고 blocked로 유지한다.
- Scheduler service는 active `type=schedule` deployment만 로드/실행하고, non-schedule deployment id로 job이 호출되면 dispatch하지 않는다.
- Preflight response sanitizer는 hidden KB id/name/path, exact denied count, raw source metadata, raw exception을 제거한다.
- Preflight response/409/public graph projection은 Collection/child ID, label, membership, exact count와 두 Knowledge reference 배열을 노출하지 않는다.
- Central deployment runtime policy matrix는 public info, authenticated run/run-info, API secret run, webhook run, schedule run, workflow-node child run surface별 허용 deployment type을 고정하고 unknown surface/type을 fail-closed로 거부한다. 기본 policy는 불변 객체이며 composition dependency로 교체 주입할 수 있지만 환경변수나 전역 mutation으로 확장할 수 없다.
- Schedule occurrence key/state/reason/settings helper는 DB/framework 없이 테스트하고 naive datetime, unknown state/reason, mutable settings input을 fail-closed한다.
- Schedule application use case는 access-management command/model/port/recorder와 FastAPI/Celery/SQLAlchemy query를 import하지 않는다. Schedule 전용 audit port를 사용하고 concrete Gateway UnitOfWork만 composition에서 주입한다.
- Schedule audit recorder는 system actor, canonical action/target과 strict metadata allowlist만 현재 UoW에 추가하며 commit/rollback하지 않는다.
- System tick의 `Schedule.next_run_at`/`last_run_at`만 바뀌면 generic `schedule.updated`가 생성되지 않고 cron/timezone/lifecycle 또는 unrelated tracked mutation audit은 유지된다.
- Main-generation deployment LLM credential policy는 graph의 exact `llmNode`와 chat `model_id`만 수용하고, credential selector/auto-routing/default fallback field가 graph에 있으면 거부한다. Query-embedding policy는 Knowledge가 설정된 같은 node와 active embedding model만 수용한다. Capability issue/admission은 policy replacement, credential revoke, verified relation 또는 permission revision 변화 뒤 stale capability를 provider 호출 전에 거부한다.
- Main-generation policy는 deployment version/node당 active row를 최대 하나만 허용한다. Query-embedding policy는 같은 node/model slot당 active row를 최대 하나만 허용하되 서로 다른 embedding model slot은 공존한다. Concurrent 최초 write도 canonical deployment lock과 partial unique constraint를 우회하지 못한다.

## API Tests

- App list/detail/clone과 Deployment create/list/detail/toggle/run-info/browser-access 응답은 App `url_slug` 같은 safe field를 보존하되 `auth_secret` 원문 또는 masked preview를 포함하지 않는다. App/Deployment request의 `auth_secret` unknown field는 무시하지 않고 validation error로 거부한다.
- Secret status/rotation은 active organization과 App workflow `deploy` 권한을 요구한다. Same-organization permission denial은 `403`과 정확히 한 번의 safe `permission.denied` audit, cross-organization/missing은 resource hiding `404`이며 secret state를 변경하지 않는다.
- `expected_version=0` 최초 발급과 현재 version rotation만 성공한다. Stale/동시 loser는 `409 app.auth_secret_version_conflict`이며 generator, audit과 state mutation을 실행하지 않는다.
- Rotation 성공 response만 신규 원문을 한 번 포함한다. 같은 response의 다른 field, 후속 status와 모든 일반 resource endpoint에는 원문·verifier가 없고 audit/trace/log capture에도 남지 않는다.
- Audit outbox add/flush/commit 실패는 transaction 전체를 rollback하고 성공 response 또는 신규 원문을 반환하지 않는다.
- Authenticated deployment list는 App `url_slug`를 각 deployment 응답에 포함하되 App `auth_secret`을 목록 조합 과정에서 주입하지 않는다.
- Deployment LLM credential policy GET/PUT는 active organization manager만 허용하고, safe purpose/model policy projection 외 encrypted credential config, server credential principal, capability ID와 raw provider data를 반환하지 않는다. Missing/cross-organization deployment는 404, non-manager는 403, immutable graph/purpose/model mismatch·unavailable credential·unverified relation·`use` denial은 safe 422, concurrent active selection은 409로 정규화한다.
- Capability-required provider admission은 실제 prompt/output/cost upper bound를 검증하고 Shared credential config 경계를 거친 control row를 provider network 호출 전에 commit한다.
- `POST /api/v1/deployments/preflight`는 blocked 결과도 `200 OK`와 `status="blocked"`로 반환한다.
- `POST /api/v1/deployments/preflight` with `is_active=false`는 null unresolved blocker만 `status="warning"`으로 반환하되 required action은 유지한다. Non-null unavailable credential과 structural blocker는 `status="blocked"`다.
- Client Mail node 기본 데이터는 `credential_id=null`과 `configuration_state=unresolved`를 함께 생성한다. 이 형태의 draft 저장은 허용하지만 credential을 선택하기 전 실행·활성화는 configuration preflight에서 차단한다. 구버전 Client가 만든 null Mail node는 상태 필드가 없어도 draft 저장과 inactive warning이 가능하지만 명시적 null 상태는 invalid다.
- `POST /api/v1/deployments` with `is_active=true`는 private KB가 anonymous/public 실행 surface에 포함되면 `409 deployment.preflight.blocked`를 반환한다.
- `POST /api/v1/deployments` with `is_active=false`는 같은 graph를 저장할 수 있지만 active deployment 교체, public URL 활성화, schedule job 생성을 하지 않는다.
- Inactive deployment activation/toggle은 private KB preflight 실패 시 `409 deployment.preflight.blocked`를 반환한다.
- Stale/missing/invalid privacy manifest, frozen/current validity epoch 불일치 legacy, compliant pointer 뒤 남은 legacy, retired legacy, DB-time cutoff equality/경과와 artifact 부재는 preview에서 fixed `knowledge_privacy_artifact_unavailable` + `reprocess_or_remove_unavailable_knowledge`를 반환한다. Invalidating platform/Organization epoch commit과 preflight를 경합시키면 item bulk update/cleanup 전에도 runtime과 같은 결과로 즉시 차단한다. Active create/enable/toggle은 `409 deployment.preflight.blocked`이고 inactive preview/create에서 허용된 availability warning만 저장 가능하며, response에는 document/policy/provider identity와 exact count가 없다.
- Active create/toggle은 unresolved/invalid Mail 또는 Slack, unavailable Mail credential, subject 없는 Mail surface를 기존 `409 deployment.preflight.blocked` envelope으로 차단하며 task/schedule/active-pointer side effect를 만들지 않는다.
- `is_active=false` preview/create는 null unresolved configuration과 상태 필드가 누락된 구버전 null Mail node만 warning으로 보존한다. Slack unresolved와 다른 legacy selector invalid가 함께 있으면 두 issue를 모두 유지하고 blocked한다. Invalid Mail/Slack 조합, 임의 non-null UUID, revoked/cross-organization/permission-denied credential, malformed graph와 validator unavailable은 blocked로 유지한다.
- Missing/revoked/cross-organization/permission-denied Mail credential은 모두 `mail_credential_unavailable`이며 response에 credential ID/name/email과 permission 상세가 없다.
- Node `position` 누락·잘못된 좌표와 edge `id` 누락·빈 값, dangling edge, cycle, duplicate node ID, 진입점 오류와 고립 실행 node는 최상위와 Loop subgraph에서 `workflow_graph_invalid`로 차단되고 inactive deployment row와 task를 만들지 않는다. 최상위 graph는 명시적 trigger/start 하나를 요구하고 Loop body는 별도 trigger 없이 incoming executable edge가 없는 실행 진입점 하나를 허용한다. 합산 node 1,000개, edge 5,000개와 depth 16 경계는 통과하며 각각 1개 초과하면 같은 reason으로 차단한다.
- Preview permission denial은 audit 0건이며 create/toggle enforcement의 same-organization denial은 resource별 `permission.denied` 정확히 1건이다.
- 여러 Mail credential preflight는 scalar permission과 같은 결과를 내고 organization/user/membership query를 credential마다 반복하지 않는다. Scalar/bulk 모두 revoked credential의 잔존 grant를 운영 권한으로 집계하지 않는다.
- Active deployment delete는 다른 deployment를 자동 active로 승격하지 않는다.
- Public/API run endpoint, webhook endpoint, authenticated deployment run/run-info endpoint는 `workflow_node` active deployment를 직접 실행하거나 실행 정보를 노출하지 않는다.
- Public info와 public/API slug run은 `App.active_deployment_id`가 가리키는 deployment의 `app_id`가 요청 app과 일치할 때만 노출하거나 실행한다. 다른 app 소유 deployment를 가리키는 stale/corrupt pointer는 safe 404로 닫고 task를 dispatch하지 않는다.
- Public info 기본 policy는 `webapp`, `widget`, `chatbot`만 허용하고 API/`internal_chatbot`/MCP/schedule/webhook/workflow-node/unknown/empty type을 safe 404로 닫는다. 명시적으로 주입한 immutable test policy가 기본 policy를 mutation하지 않고 독립적으로 동작하는지 검증한다.
- App status, clone, workflow-node deployment listing은 active deployment id뿐 아니라 deployment `app_id` ownership도 확인한다. Cross-app pointer로 다른 app의 graph/schema/status를 복제하거나 노출하지 않는다.
- Webhook endpoint는 active deployment가 `type=webhook`이 아닌 경우 API/chatbot/schedule/workflow-node 등 모든 non-webhook deployment와 unknown/empty type을 safe 404로 거부하며, budget check와 background task 등록 전에 종료한다.
- Webhook endpoint는 Bearer 또는 `X-Webhook-Secret` 중 정확히 하나의 valid credential source만 허용한다. Scheme case-insensitivity, credential byte 보존, 1/512-byte 경계, non-ASCII/empty/malformed/oversized candidate와 constant-time verifier 경로를 검증한다.
- Query `token` key는 empty/multiple/value 없는 형태와 valid header 동반 여부에 관계없이 static `400 webhook.query_secret_not_supported`로 body read 전에 거부된다. Query 값, raw target, credential header와 parser error가 response/log/audit/trace/metric에 남지 않는다.
- Bearer와 custom header 동시 사용, 같은 credential header duplicate occurrence와 comma-folded ambiguous value는 값이 같아도 `400 webhook.credential_ambiguous`로 거부하고 body/capture/deployment/budget/background publish를 건드리지 않는다.
- Missing/invalid credential과 invalid server-side secret state는 같은 `403 webhook.authentication_failed`를 반환하며 unauthenticated body를 읽지 않는다.
- Missing/duplicate/malformed Content-Type, unsupported type/charset, duplicate/compressed Content-Encoding은 `415 webhook.payload.unsupported_media_type`으로 queue admission 전에 거부된다. Case-insensitive `application/json`, vendor `application/*+json`, UTF-8 charset와 well-formed non-charset parameter는 허용된다.
- Repository Nginx에서 query sentinel을 붙인 1 MiB 초과 Webhook 요청은 413으로 거부되며 sentinel과 request target이 access/error log에 남지 않는다. Nginx config test는 Webhook location의 log 억제, `client_max_body_size 1m`, `client_body_timeout 5s`, `proxy_request_buffering off`를 함께 확인한다.
- Gateway query-redaction middleware는 exact/empty/repeated/percent-encoded `token` field를 downstream scope에서 제거하고 boolean marker만 보존한다. 다른 Webhook query field와 비-Webhook query는 바꾸지 않으며, 실제 Uvicorn access log에 synthetic token value가 남지 않는지 확인한다.
- Content-Length는 missing, exact 1 MiB, over-limit, understated, duplicate, negative, non-decimal 경계를 검증한다. Actual stream은 0/exact/over-limit와 multi-chunk crossing을 검증하고 over-limit은 `413 webhook.payload.too_large`로 즉시 중단된다.
- Body stream stall/disconnect, decode/parse/traversal stage deadline을 검증한다. 5초를 넘긴 성공은 없고 timeout은 `408 webhook.payload.timeout`, disconnect는 `400 webhook.payload.invalid`이며 raw exception은 노출되지 않는다.
- Strict JSON은 object root와 nested standard JSON value를 보존한다. Array/string/finite number/boolean/null root, UTF-8 BOM, invalid UTF-8, trailing data와 `NaN`/`Infinity`는 `400 webhook.payload.invalid`로 downstream 전에 거부한다. Depth 19/20/21과 total node 9,999/10,000/10,001 경계를 iterative traversal로 검증한다.
- Payload의 `app_id`, organization/workflow/deployment/user/trigger/execution-context 유사 key는 일반 workflow input으로만 전달되고 server-derived execution context나 ORM field를 덮어쓰지 않는다.
- App 404, query/header/auth/media/size/JSON 실패 단계별 spy test는 body read, capture mutation, deployment query, budget, BackgroundTasks와 Celery publish가 mandatory order보다 먼저 발생하지 않는지 검증한다.
- 동일한 valid webhook delivery 두 건은 각각 기존 admission을 거친다. MBA-93은 payload hash dedupe나 exactly-once를 약속하지 않으며 MBA-247 미병합 상태에서는 single active secret 회귀만 검증한다.
- Deployment success UI와 Webhook node panel은 query-integrated URL을 표시·복사하지 않고 endpoint와 header instruction을 분리한다. Secret은 URL, browser storage, analytics 또는 Client log에 저장되지 않는다.
- Repository Nginx config/smoke는 webhook location의 query/header safe logging, 1 MiB body guard와 5초 idle timeout을 검증한다. Direct Gateway test가 static detail code를, proxy test가 edge status와 secret 비노출을 검증하며 production ingress 설정은 rollout evidence로 별도 확인한다.
- Gateway runtime endpoints, webhook endpoint, scheduler, Workflow Engine task는 같은 central runtime policy 결과를 사용하며 서로 다른 allowlist를 갖지 않는다.
- 직접 생성한 runtime policy에 mutable dict/set을 전달한 뒤 원본을 변경해도 policy 결과가 바뀌지 않아야 하며, direct constructor도 unknown type/surface/trigger를 fail-fast로 거부한다. Gateway webhook/scheduler/DeploymentService와 Workflow Engine provider에 custom immutable policy를 주입했을 때 해당 surface 판정에 실제 적용되어야 한다.
- Workflow-node runtime은 target app이 조직 소속인데 parent `execution_context.organization_id`가 없거나 target app organization과 다르면 fail-closed로 실행하지 않는다.
- Workflow-node runtime은 target app organization이 없으면 legacy/ambiguous target으로 보고 fail-closed로 실행하지 않는다.
- Workflow-node runtime은 target app의 active deployment가 `type=workflow_node`가 아니면 fail-closed로 실행하지 않는다.
- Workflow-node runtime의 active deployment query는 deployment id, target app id, `is_active=true`를 함께 요구하고 설정 오류는 정확한 non-retryable `WorkflowNodeConfigurationError`로 반환한다.
- Workflow-node runtime은 `workflow_node_visited_app_ids`에 target app이 이미 있거나 `workflow_node_depth`가 cap에 도달하면 DB lookup 또는 subworkflow dispatch 전에 fail-closed로 실행하지 않는다.
- Workflow-node runtime의 순환/depth/target 설정 오류는 Celery retry로 재시도하지 않고 non-retryable runtime error로 즉시 실패한다.
- Active `type=workflow_node` deployment graph에 `scheduleTrigger`가 있어도 schedule record나 scheduler job을 생성하지 않는다.
- Scheduler startup query와 dispatch 시점은 모두 schedule deployment가 해당 app의 현재 `active_deployment_id`인지 재확인한다. Queue에는 graph가 아니라 deployment id를 넣고, worker가 실행 직전 active/type/app/current pointer를 다시 확인해 enqueue 후 삭제/비활성/stale/non-current가 된 deployment를 retry 없이 종료한다.
- APScheduler에 job이 남아 있어도 `Schedule.id + deployment_id` row가 없으면 queue와 budget service를 호출하지 않고 local job을 제거한다. Worker queue context에 다른 organization/workflow/app/deployment/version 또는 execution subject를 넣어도 DB의 canonical App/Deployment context로 덮어쓰거나 제거하고 correlation allowlist만 보존한다.
- Agent Builder primary 전환과 active Deployment 생성이 겹치면 두 경로는 같은 App row lock으로 직렬화된다. Primary 전환이 먼저 commit되고 생성 요청이 snapshot을 생략했으면 배포는 새 primary의 server graph를 사용한다. 잠금 전 old primary에 binding된 명시적 client snapshot 요청은 `409 deployment.graph_snapshot_stale`로 종료하며 old graph나 active pointer를 저장하지 않는다. Active Deployment 생성이 먼저 commit되면 primary 전환은 최신 pointer를 확인해 차단된다.
- Deployment toggle은 App lifecycle lock 획득 뒤 deployment state를 refresh한 다음 단일 active pointer 정책을 적용한다. Run/run-info read path는 이 exclusive lock을 사용하지 않아 동일 App의 동시 실행 요청을 직렬화하지 않는다.
- 같은 DB session에서 App의 이전 primary가 identity map에 남아 있어도 lifecycle lock 이후 최신 primary와 active pointer를 사용한다. App에 Workflow 이력이 둘 이상이고 inactive Deployment의 source Workflow를 증명할 수 없으면 activation은 `409 deployment.reactivation_provenance_unavailable`로 실패하며 deployment/pointer를 변경하지 않는다.
- Invalid cron/timezone 활성화는 safe `422 deployment.schedule_configuration_invalid`로 실패하고 parser 원문을 노출하지 않으며 schedule/deployment partial mutation을 남기지 않는다.
- Public route 목록에는 schedule claim 조회, status mutation, outcome acknowledgment 또는 redrive endpoint가 없어야 한다.
- Blocking preflight 예외는 broad catch에서 generic `400`으로 변환되지 않는다.
- Source-managed KB public 후보는 public exposure approval primitive가 없으면 blocked로 처리한다.
- Webhook capture start/status rejects unauthenticated requests.
- Webhook capture start/status rejects authenticated users without target workflow `deploy` permission.
- Webhook capture status rejects missing, wrong, expired, or different-requester `capture_id`.
- Webhook capture cancel deletes a waiting session, rejects wrong/different-requester `capture_id`, and requires target workflow `deploy` permission.
- Webhook received after capture cancel follows the normal execution path instead of the capture path.
- Webhook capture stores and returns only a redacted/capped payload preview; token, secret, authorization, cookie, password, raw payload/content fields and known token patterns such as generated App secret marker, JWT, GitHub, Slack, AWS, Google API keys, and PEM private keys are not returned as raw values. Public identifier fields such as `issue.key` and `project.key` are preserved, while secret-bearing names such as `api_key`, `secret_key`, `access_key`, `x-api-key`, `secret-key`, and `api.key` are redacted. Secret-like or oversized JSON field names are sanitized before returning or storing the preview. Large arrays are capped while iterating the preview and are not copied in full before applying the item limit.
- Webhook capture deletes the session after the captured status is read once.

## E2E Tests

- 배포 설정과 Webhook node의 secret control은 safe status만 표시하고 명시적 발급/rotation 성공 직후에만 신규 secret을 보여준다. 화면 재진입, reload와 일반 App/Deployment refetch로 secret을 복구할 수 없다.
- API/Webhook 배포 input은 status 조회 중, 미설정, lifecycle mutation disabled 상태의 미설정 App, status 조회 실패와 App ID 누락에서 다음 단계를 차단한다. `configured=true`이면 mutation gate가 disabled여도 기존 credential 배포는 허용하고 발급·교체만 비활성화한다. 발급 성공 callback 뒤에만 다음 단계가 활성화된다.
- Deployment 오류 formatter는 top-level 표준 `{error: ...}`와 legacy nested `{detail: {error: ...}}`를 모두 처리한다. `deployment.app_auth_secret_required`와 `app.auth_secret_lifecycle_unavailable`은 generic Axios 문구 대신 안전한 상태를 표시하고, unknown required action이나 raw detail은 렌더링하지 않는다.
- REST API load client는 `Authorization: Bearer`만 사용하고 폐기된 `X-Auth-Secret`을 전송하지 않는다. 테스트 시작·실패·리포트에서 token 값, prefix 또는 일부 preview를 출력하지 않는 정적 계약 테스트를 유지한다.
- Direct-runtime 검증 script가 ingress를 호출하지 않더라도 고정 raw App secret fixture를 저장하지 않는다. Legacy raw-only fixture는 같은 candidate의 verifier로 승격하고, 미설정 fixture만 임시 candidate를 생성·폐기하며 verifier-only canonical state를 저장한다. 이미 유효한 managed state는 불필요하게 rotation하지 않는다.
- One-time secret copy는 browser storage, URL, analytics와 console에 원문을 남기지 않는다. 기본 rotation 안내는 previous가 최대 5분 유효함을, 즉시 폐기 선택은 기존 consumer가 즉시 실패할 수 있음을 명확히 표시한다.
- Version conflict와 응답 유실 UI는 POST를 자동 재시도하지 않고 status refresh 후 사용자가 새 rotation을 명시적으로 선택하게 한다. Client는 one-time secret과 발급 version을 함께 보존하며, Input에서 Success로 전환하거나 success control을 다시 mount한 뒤 status version이 다르면 이전 원문을 폐기해 stale credential을 새 값처럼 표시·복사·테스트 header에 사용하지 않는다.

- Workflow 설정 사이드바는 deployment list의 App `url_slug`로 공개 Chatbot/Widget URL을 구성하고 slug 누락 시 `/embed/chat/undefined` 링크와 복사 동작을 렌더링하지 않는다.
- 배포 목록은 enabled policy라도 비활성 revision이면 `활성화 후 적용`, 활성 revision이면 `집행 중`으로 구분한다.

- Deployment modal은 preflight preview가 blocked인 경우 safe reason과 required actions를 표시하고 hidden KB identity를 표시하지 않는다.
- Inactive save 후 activation을 시도하면 같은 preflight blocker가 사용자에게 표시된다.
- 공개/내부 챗봇 배포 결과는 각각 공개 링크와 인증 내부 링크만 표시하며, 내부 링크의 `401`은 safe `next`를 보존해 이메일/비밀번호 로그인 후 원래 링크로 복귀한다. 상세 assertion은 [chatbot-deployment test cases](../chatbot-deployment/test_cases.md)를 따른다.
- Disposable PostgreSQL에 연결한 dispatcher 두 개가 같은 occurrence를 동시에 처리해도 claim은 하나이고 `next_run_at`은 한 번만 전진한다.
- Duplicate Celery task를 Worker 두 개가 받아도 stable workflow run identity 하나와 engine admission 한 번만 발생한다.
- Schedule dispatch가 managed node blocker를 발견하면 budget과 Celery publisher 호출은 0회이고 claim은 `canceled + configuration_preflight_blocked`, audit은 기존 `schedule_dispatch.canceled`와 safe reason만 가진다.
- Schedule preflight adapter가 infrastructure exception을 내면 UoW가 rollback되고 publish request가 생성되지 않는다.
- Disposable pgvector PostgreSQL CI는 실제 Alembic head에서 두 dispatcher session과 두 Worker admission session을 동시에 실행해 각각 winner가 하나임을 필수 검증한다. Opt-in skip만 존재하고 CI에서 실행되지 않는 상태는 완료 증거로 인정하지 않는다.
- Broker publish 전후 장애와 Worker admission 전 종료는 bounded recovery되고, admission 후 종료는 outcome unknown으로 격리되어 자동 replay되지 않는다.
- Migration-first, disabled rollout, drain, claim activation과 역순 rollback rehearsal에서 legacy direct dispatcher와 claim dispatcher가 동시에 활성화되지 않는다.
- Coordinated rollout이 첫 서비스 적용 뒤 실패하면 같은 commit image와 desired fingerprint를 가진 선행 서비스, 승인된 previous fingerprint를 가진 나머지 서비스 조합만 재개한다. 반대 순서, 다른 image, 임의 third fingerprint, active claim 상태의 direct settings 변경은 fail-closed한다.
- `claim -> disabled` 직접 전환은 evaluator가 거부한다. `disabled`/`drain -> claim` activation과 `drain -> disabled` rollback은 transition preflight가 nonterminal/unreviewed outcome을 하나라도 찾거나 DB 검사를 완료하지 못하면 두 deployment 적용 전에 중단한다.
- Activation preflight는 legacy/new schedule task, rollback preflight는 new schedule task가 active/reserved/scheduled이거나 Redis workflow queue depth가 0이 아니면 중단한다. Redis inspection 실패도 fail-closed하며 payload/body를 파싱하거나 로그에 남기지 않는다.

## Migration And Persistence Tests

- Query embedding policy write mode는 application default와 미설정 환경에서 `disabled`이고 unknown value를 거부한다. Disabled query PUT은 manager scope 확인 뒤 policy row/provider selection 없이 safe `503`으로 끝나며 main-generation PUT은 계속 동작한다. Active mode는 MBA-320의 purpose-aware Gateway/worker drain 검증 없이는 운영에 적용하지 않는다.
- Disposable PostgreSQL CI는 같은 canonical location에서 main/query 공존, 서로 다른 embedding model query slot 공존, 같은 query slot 중복·동시 최초 write의 하나의 winner를 검증한다. Downgrade는 policy/capability/usage 세 테이블을 deterministic `ACCESS EXCLUSIVE` lock으로 직렬화한 뒤 query row를 검사하고, 검사와 DDL 사이 concurrent write가 있으면 row 의미를 변환하지 않고 실패해야 한다.
- MBA-247 expand migration은 single Alembic head를 유지하고 기존 non-null `apps.auth_secret`을 같은 V1 verifier와 version 1로 backfill한 뒤 legacy column을 nullable로 바꾼다. Raw value를 migration output에 기록하지 않는다.
- Expand release의 checked-in manifest와 application default는 lifecycle mode를 `disabled`로 유지한다. 이 상태의 status는 `rotation_enabled=false`이며 권한이 있는 rotation도 secret 생성, row lock, audit과 mutation 전에 `503 app.auth_secret_lifecycle_unavailable`로 끝난다.
- Docker Compose와 Helm values/template은 lifecycle mode를 기본 disabled로 Gateway에 전달한다. Status와 성공 rotation 응답은 no-store/no-cache header를 반환한다.
- Caller-controlled request ID/IP/User-Agent에 secret-like 값을 넣어 rotation해도 `app.auth_secret.rotated`와 permission-denied audit metadata에 해당 값이 저장되지 않는다.
- REST API/Webhook 배포 모달은 첫 active preflight 이전 input 단계에서 App ID 기반 secret status와 발급·교체 control에 접근할 수 있다.
- 발급된 one-time secret을 표시한 상태에서 교체 확인을 열었다가 취소하거나 refresh가 같은 version인 rotation 실패를 확인해도 기존 원문은 새 rotation 성공 전까지 현재 component memory에 유지된다. 성공 응답을 부모 state로 전달한 것만으로 status를 즉시 재조회하거나 원문을 숨기지 않는다. 단, 원문과 함께 보존한 발급 version이 초기·재진입 status 또는 실패 뒤 refresh의 version과 다르면 원문을 즉시 폐기한다.
- `--reset` 없는 demo seed upsert는 valid managed App verifier/current/previous/generation 상태를 seed의 예측 가능한 credential로 되돌리지 않으며, legacy raw value만 null로 정리한다. 명시적 reset으로 row가 삭제된 경우에는 seed state를 새로 생성한다.
- Active deployment secret blocker는 request ID와 `required_actions`를 포함한 표준 `error` envelope로 반환한다.
- Managed current verifier가 malformed이면 previous verifier와 grace가 유효해도 public 인증은 fail-closed한다.
- Secret이 없는 active API/Webhook preview/create/toggle은 mode가 `disabled`이면 503, `active`이면 `deployment.app_auth_secret_required` 409로 DB mutation 전에 차단된다. Inactive API/Webhook draft와 active non-secret deployment type은 허용한다.
- Expand rollout rehearsal은 migration 전에 legacy Gateway traffic을 drain/fence하고 verifier-aware disabled revision 수렴 뒤 traffic을 재개한다. 선행 redaction-only release가 없는 상태에서 old/new Gateway가 동시에 serving되는 일반 rolling은 안전한 완료 증거로 인정하지 않는다.
- 모든 Gateway Pod 수렴 뒤 lifecycle mode를 `active`로 주입한 Fresh App 발급과 rotation은 current/previous verifier와 safe metadata만 저장하고 legacy `auth_secret`은 null로 유지한다. Raw와 verifier는 일반 response, audit outbox, trace와 log에 없어야 한다. Generation 1 이상 인증은 legacy raw가 있어도 verifier를 권위로 사용한다.
- Migration 뒤 기존 secret과 migration 이후 구버전 Pod가 만든 generation 0 raw-only secret은 public run/webhook에서 인증되며 일반 response에는 노출되지 않는다. Verifier-only 또는 unconfigured App처럼 `auth_secret IS NULL` row가 있어 구 schema의 non-null raw를 복구할 수 없으면 downgrade는 DDL 전에 fail-closed한다.
- 후속 reconcile test는 generation 0 late arrival backfill과 fallback 사용량/raw-only row 0을, contract test는 raw null 수렴 뒤 legacy column이 제거되고 mixed old revision이 남아 있지 않음을 검증한다.
- 실제 PostgreSQL 동시 rotation은 같은 App row에서 한 transaction만 version을 증가시키며 loser는 winner commit 뒤 최신 version을 관찰한다. Commit 실패와 rollback 뒤에는 이전 current 인증만 유지된다.

- Alembic 기준 merge head에서 upgrade는 single head를 유지하고 claim table/constraint/index와 schedule-only nullable WorkflowRun executor를 정확히 반영한다. Controlled downgrade/re-upgrade는 system WorkflowRun 이력, admitted claim의 durable run correlation, active/unreviewed claim, configuration quarantine이 모두 없는 경우에만 성공하며, 하나라도 있으면 첫 schedule DDL 전에 fail-closed하고 revision을 보존한다.
- Claim `organization_id`는 non-null이고 canonical App과 일치하며 lifecycle FK cascade가 없다. Schedule/Deployment 삭제 뒤에도 outcome review organization provenance를 유지한다.
- Claim 생성, `next_run_at`, budget policy audit은 한 commit이며 audit/claim/next-run 중 하나가 실패하면 모두 rollback한다.
- Claim model은 generic ORM audit listener 대상이 아니며 raw input, graph, prompt/evidence, credential/provider response와 raw exception column이 없다.
- Dead-letter reason/correlation DB constraint는 admission 전 reason에 run/start correlation을 금지하고 admission 후 reason에 task/enqueue/start/run correlation을 모두 요구한다. 기존 모순 row가 있으면 migration은 값을 추측해 보정하지 않고 constraint 교체 전에 fail-closed한다.
- 실제 PostgreSQL은 null safe reason의 canceled/dead-lettered claim, null resolution의 completed outcome review, null task id의 system schedule WorkflowRun을 모두 거부한다.
- MBA-219 migration은 schedule claim table과 최신 safe-reason constraint를 만든 revision의 후손에 있어야 한다. 실제 PostgreSQL은 canceled claim의 `configuration_preflight_blocked`를 허용하지만 pending/dispatching/enqueued/running/succeeded/dead-lettered 상태의 같은 reason은 거부한다. 해당 reason row가 남아 있으면 downgrade는 constraint DDL 전에 fail-closed한다.
- `pending`/`dispatching`/`enqueued`에 `workflow_run_id`를 직접 기록하면 domain과 실제 PostgreSQL check constraint가 모두 거부한다. 한 claim의 publish 결과 write 실패 뒤에도 같은 prepared batch의 다음 claim은 publish/result 처리를 계속하며, terminal finalization 일시 실패는 engine call 1회를 유지한 채 fresh session write만 bounded 재시도한다.
- Gateway/Worker startup readiness는 같은 shared helper 결과를 사용하고 introspection 실패, stale head, 필수 column 누락을 safe하게 거부한다. Concurrent migration은 advisory lock owner 하나만 진행하며 contender는 bounded wait 안에서 owner가 끝나면 이어서 진행하고 제한 시간을 넘기면 DDL 전에 실패한다.

현재 지원 상태(ADR-0068): provider-neutral Helm/Compose는 schedule dispatch disabled만 검증한다. 아래 coordinated rollout 검증은 future CD의 acceptance criteria이며 현재 실행 가능한 EKS/Dev workflow 테스트가 아니다.

- Target CD는 기존 운영 Deployment에 fingerprint annotation이 없는 최초 disabled rollout만 bootstrap으로 허용하고 drain/claim desired mode의 annotation 누락을 fail-closed해야 한다.
- Target CD는 동일 immutable release의 Logger image를 Gateway/Worker보다 먼저 배포·검증하고 Logger가 수렴하지 않으면 claim admission을 활성화하지 않아야 한다.
- Target CD가 일부 image push 또는 apply 뒤 같은 release로 재실행되면 기존 OCI registry digest를 재사용하고 immutable identity로 남은 단계를 수행해야 한다. 최종 성공은 Logger/Gateway/Worker의 generation, replica, Pod imageID, fingerprint와 Ready condition이 모두 수렴할 때만 허용한다.
- Target CD integration은 일부 Worker inspect 누락이나 관측 사이 task 이동을 차단하고, 기대 Ready Worker와 응답 집합이 일치하며 앞뒤 task/queue 관측이 연속 두 번 0일 때만 통과해야 한다.
- Lock 대기 또는 느린 budget 평가가 transaction 시작 뒤 발생해도 dispatch lease와 execution deadline은 전환 직전 DB wall clock 이후로 설정되고 commit 직후 만료되지 않는다.
- `disabled` mode에서 신규 occurrence/dispatch/admission은 0건이지만 schema-ready DB의 visibility, terminal cleanup, pending/running age 관측은 계속 실행된다. Schema가 없는 최초 bootstrap에서는 maintenance를 시작하지 않는다.
- Helm 기본/production render는 disabled mode에서 통과하고 non-disabled mode에서 현재 지원하지 않는다는 safe error로 실패한다. Gateway와 Worker를 모두 비활성화해도 chart 전역 검증이 `claim|drain`을 같은 safe error로 거부한다. Docker Compose에 `SCHEDULE_DISPATCH_MODE=claim|drain` 환경값이 있어도 render된 Gateway/Worker mode와 fingerprint는 disabled를 유지한다.
- CI support-surface guard는 모든 PR의 scope 분류 전에 repository execution closure를 검사하고, workflow/composite action 변경에서는 deployment validation도 선택한다. Legacy path, allowlist 밖으로 이름을 바꾼 workflow, 삭제 뒤 남은 stale allowlist entry, 승인 workflow·local action·지원하는 정적 형식으로 전이 참조한 `scripts/**` 실행 파일/Python module 안에 AWS credential/ECR/EKS/eksctl 신호를 넣은 경우와 `infra/k8s`, `infra/terraform` prefix가 다시 추적되면 실패한다. Provider-neutral 위임과 bounded 현재 승인 closure만 통과하며, 발견된 참조의 누락·경로 이탈·허용 prefix 밖 local 실행·non-UTF-8·symlink·과도한 크기/깊이/개수는 fail-closed한다. 최상위 workflow를 바꾸지 않고 참조 스크립트만 바꾼 PR도 같은 검사를 우회할 수 없어야 한다.
- Production values에는 root `serviceAccount` 계약만 존재하고 Gateway, Workflow Worker, Knowledge Worker Deployment가 모두 같은 helper 결과를 `serviceAccountName`으로 사용한다. Component별 중첩 annotation은 허용하지 않는다.
- Helm storage 기본값 `LOCAL`은 cloud 좌표 없이 통과하며 Gateway, Workflow Worker, Knowledge Worker Pod에 `S3_BUCKET_NAME`/`AWS_REGION` reference를 만들지 않는다. Production CLOUD는 CI 전용 non-secret placeholder와 두 reference를 함께 렌더하고, 각 필수 `configMapKeyRef`가 같은 render의 ConfigMap key에 대응해야 한다. Unknown type, 빈 bucket, 빈 region과 과거 `gateway.env`/`worker.env` storage key를 각각 주입하면 render 전에 실패한다. Component별 storage key와 implicit region default는 없어야 한다. Helm 파일만 변경한 PR도 이 실제 render closure와 support-surface/storage 계약 테스트를 deployment job에서 실행한다.
- Production Helm actual render는 Knowledge worker replica 2개, concurrency 2, `knowledge` queue, prefetch 1, exact `knowledge@%h` nodename, migration init container, root ServiceAccount, CLOUD storage env와 singleton `Recreate` Beat를 함께 포함해야 한다. Beat 비활성, replica 0과 concurrency 0 override는 각각 fixed safe message로 render 실패해야 한다. Readiness helper unit test는 exact local pong만 성공시키고 empty/malformed/다른 replica 응답, invalid timeout과 broker exception을 무출력 실패로 닫아야 한다. Rendered worker는 내부 2초 ping보다 긴 5초 exec readiness timeout을 사용하고 liveness probe를 포함하지 않으며 CLOUD mode에서 local upload PVC를 만들거나 mount하지 않아야 한다.
- Gateway storage 설정은 type을 trim/대문자로 정규화한다. `CLOUD`의 bucket 또는 region이 null/empty/whitespace이면 safe validation error로 시작을 거부하고 provider client를 생성하지 않으며 unknown type을 LOCAL로 fallback하지 않는다.
- Direct Gateway 개발 예시에는 정확히 하나의 지원 storage mode(`LOCAL` 또는 `CLOUD`)가 있어야 하고 legacy `PROD` 값은 없어야 한다. 현재 로컬 예시의 `LOCAL`은 cloud 좌표 없이 유효해야 한다.
- S3 upload와 presigned URL provider failure는 cause chain 없이 stable safe error로 변환되고 provider exception text가 exception이나 log에 남지 않는다. Upload 실패 뒤 request file pointer는 기존 계약대로 초기 위치로 복원한다.
- Production proxy mode의 S3 client와 workload identity nested STS client는 같은 botocore session default config의 exact proxy와 retry 정책을 상속한다. Ambient proxy가 있어도 사용하지 않고 credential exchange 실패 뒤 direct fallback하지 않는다.
- Helm render는 `egressProxy.service.httpsPort!=3128`, `httpCompatiblePort!=3129` 또는 `connectorTcpPort!=3130`을 각각 고정된 safe message로 거부한다. Connector port 목록은 1~16개의 unique valid port이며 strict Connector test 목록을 포함해야 하고, application env·Squid ACL·IPv4/IPv6 proxy egress rule에 동일하게 렌더되어야 한다. Squid rule은 public 80/443과 Worker-only IMAP 143/993도 포함한다.
- 기본 Helm `values.yaml`은 추가 CIDR override 없이 local direct-pinned profile로 실제 렌더되고 egress-proxy workload를 만들지 않는다. Production values는 proxy와 exact source CIDR을 명시적으로 활성화한다.
- 기본 및 production Helm render에서 모든 strict egress NetworkPolicy는 기본 `kube-system` DNS peer를 공유해야 한다. DNS namespace와 Pod label selector를 override한 render에서는 모든 policy가 같은 custom peer와 UDP/TCP 53만 사용해야 하며, invalid namespace, non-map selector와 빈 label key는 render 전에 safe fixed error로 실패해야 한다. Kubernetes가 허용하는 빈 label value는 정상 렌더해야 한다. Pinned Calico E2E는 기본 DNS 조회 성공 뒤 Gateway, Knowledge, Workflow, Logger, Beat와 Frontend의 proxy/internal 정상 경로 및 direct public 차단을 검증한다.
- Proxy mode의 기본 PostgreSQL Connector는 `CONNECTOR_EGRESS_ALLOWED_PORTS`에 포함된 custom DB port를 application guard와 `3130` dialer 양쪽에서 허용하고, 목록 밖 port는 origin dial 전에 거부해야 한다. Direct local/development mode는 기본 `5432` 제한을 유지해야 한다.
- 실제 Workflow Worker와 같은 `gevent.monkey.patch_all()` 이후 blocking client가 local relay에 연결해도 native accept/forward loop가 진행되어야 한다. 반대로 relay 밖의 직접 dialer 호출은 cooperative connection factory를 사용해야 한다. 이 검증은 별도 subprocess에서 실제 relay와 CONNECT handshake를 사용하고 timeout 또는 direct fallback을 성공으로 간주하지 않는다.
- Gateway/Workflow Dockerfile만 변경해도 tiktoken·NLTK와 opt-in CrossEncoder preload 계약 테스트가 deployment validation에서 실행된다.
- Demo seed embedding은 shared guarded OpenAI client를 사용하며 bare provider SDK를 생성하지 않는다.
- Kubernetes direct HTTPS negative probe는 먼저 workload pod DNS 성공을 확인하고 public IPv4를 얻은 뒤 `curl --resolve`로 direct TCP를 강제해 실패를 확인한다. IPv4-mapped probe는 unrestricted control success와 보호 workload의 mapped public/private/metadata failure를 함께 요구한다. Workflow probe는 같은 IPv4 path에서 Squid `3129`를 통한 143/993 CONNECT 성공을, Connector probe는 `3130`을 통한 배포 허용 포트 성공과 HTTPS 거부를 별도로 확인한다.
- 기본 환경에서 schedule schema downgrade를 시도하면 sibling migration DDL 전에 실패하고 Alembic head가 유지된다. 파괴적 opt-in 없는 성공 downgrade/re-upgrade는 안전성 증거로 인정하지 않는다.
- Schedule Celery task의 producer와 task registration은 모두 `ignore_result=True`이고 `task_store_errors_even_if_ignored=False`다. 성공과 실패 실행 뒤 Redis result backend에는 workflow output, RAG evidence, sync 상세 또는 raw exception이 생성되지 않으며 task outcome은 claim/status/finalization summary로 제한된다. 실제 Redis key 부재는 opt-in integration evidence로 별도 실행한다.
- Schedule structured signal capture와 Scheduler/Worker 오류 log capture에는 정의된 event/value/status/reason/mode 또는 operation/attempt/exception type만 존재하고 UUID, idempotency key, raw payload와 raw exception message가 없다.
- 1024회를 넘는 고빈도 missed occurrence도 quarantine 없이 현재 시각 이후 첫 fire time으로 coalesce하고, 미래 cursor를 과거로 되돌리지 않는다.

## Permission Tests

- App secret status와 rotation은 workflow `read` 또는 `execute`만 가진 사용자에게 허용되지 않고 `deploy` 권한 판정을 사용한다.
- Organization manager/direct deploy 권한 등 기존 effective permission 경로는 같은 resolver 결과를 사용하며 endpoint별 우회 규칙을 만들지 않는다.

- Preflight preview는 workflow deploy/manage 권한 없이는 호출할 수 없다.
- Organization member이지만 KB `use` 권한이 없는 사용자의 private KB 후보는 authenticated run에서는 denied 또는 unavailable로 표시되고, anonymous deployment에서는 blocked로 표시된다.
- Client-supplied `audience` hint는 create/activation의 server-derived audience 차단을 완화하지 못한다.
- `authenticated_user` application override가 명시된 내부 use-case 테스트 외에는 public deployment service가 authenticated override를 전달하지 않는다.
- System schedule의 LLM credential permission denial은 credential principal을 user audit actor로 사용하지 않고 system actor로 기록한다.
- Terminal cleanup은 retention을 지난 일반 dead-letter와 검토 완료 `execution_outcome_unknown` claim을 정리하지만, `outcome_reviewed_at`이 null인 `execution_outcome_unknown` claim은 보존한다.
- `disabled` mode는 BackgroundScheduler job 또는 legacy direct enqueue를 만들지 않는다. Schedule 실행이 필요한 환경은 drain 검증 없이 fallback하지 않고 `claim` mode activation 절차를 사용한다.

## Edge Cases

- workflow-node target active deployment가 없으면 safe blocked/warning reason을 반환하고 target app hidden identity를 노출하지 않는다.
- workflow-node 순환 또는 depth cap 초과는 safe blocked reason으로 닫는다.
- 1,100단계 embedded subgraph도 recursive stack error 없이 검사하고 최하위 Collection blocker를 반환한다.
- Direct-only, Collection-only, mixed preflight 결과는 기존 KB bucket을 보존하면서 additive Collection bucket/limit flag를 반환한다.
- Warning-only preflight 뒤 create가 정확히 한 번 호출되고 성공 결과에 safe warning이 표시되며, blocked preflight 뒤에는 create가 호출되지 않는다.
- workflow-node target이 현재 활성화 후보 app을 다시 참조하면 기존 active deployment가 아니라 candidate graph 기준으로 순환을 감지한다.
- 일부 authorized KB의 operational failure는 Knowledge partial-result 정책으로만 표시하고 preflight permission denial과 섞지 않는다.
- Preflight와 runtime resolver는 privacy artifact readiness에 대해 같은 결과를 내야 한다. Frozen legacy의 platform/Organization validity epoch 중 하나라도 current와 다르면 남은 cutoff와 physical artifact가 있어도 ready가 아니다. Compliant pointer가 한 번 확정된 뒤 manifest가 stale해진 document도 남은 legacy/cutoff로 되돌아가지 않고, stale cache/vector hit는 final preflight evidence가 아니다.
- Conversation-capable activation preflight는 input/output mapping, node Memory policy, immutable deployment version/snapshot hash, contract/storage generation과 Worker capability 누락을 각각 fail-closed 한다.
- Preflight 통과 뒤 active deployment pointer가 바뀌어도 old session task가 새 snapshot으로 자동 rebind되지 않고 pinned version을 실행하거나 side effect 전에 version conflict로 닫힌다.
- Public `chatbot` preflight는 login cookie나 client audience hint가 있어도 anonymous public-only이며 private KB 후보를 차단한다.
- Future authenticated internal Chatbot policy는 public route, public Access Grant, generic workflow execute 권한만으로 우회할 수 없고 별도 access permission/runtime namespace를 요구한다.
- API/webapp/widget/MCP/workflow-node/schedule/webhook와 일반 authenticated deployment run은 explicit future contract 없이 Conversation Session을 생성하지 않는다.
- Missing/null/unlisted Origin, wildcard, client config와 environment fallback은 public browser session activation/create를 허용하지 않는다. Versioned deployment allowlist만 통과한다.
- `browser_access_policy` create/preflight/revision은 같은 canonical validator와 fixed error code를 사용하고 Client local normalization은 server result를 덮어쓰지 않는다.
- Browser policy revision은 source immutable deployment snapshot을 복제한 새 version이며 default inactive, source row/current draft/active pointer 불변과 transaction-bound audit를 검증한다.
- Active browser policy revision은 일반 create/toggle과 같은 composed node catalog 및 actor principal preflight를 사용해 unresolved external node를 활성화하지 않는다.
- Deployment 관리 list/detail 응답은 legacy unknown/malformed browser policy를 canonical disabled policy로 대체하고 response validation 500을 만들지 않는다.
- Public browser projection은 active pointer, app ownership, active 상태와 Chatbot/Widget type을 함께 검증하고 null/malformed policy를 disabled로 닫으며 graph/config/secret/organization/KB identity를 노출하지 않는다.
- Public browser projection query는 version/type/policy만 조회하고 graph/config/schema JSONB를 로드하지 않는다.
- Next embed response는 exact parent CSP 또는 `'none'` 하나만 반환하고 broad static CSP/XFO 충돌/cache stale을 만들지 않는다. Gateway lookup timeout/error도 fail-open하지 않는다.
- Public info/run의 수동 wildcard CORS 제거 후 same-origin iframe은 정상이고 external direct JavaScript는 deployment parent 목록으로 ACAO를 얻지 못한다.
- Production browser API resolver는 빈 설정에서 `/api/v1`, 공개 HTTPS origin에서 canonical absolute API URL을 만들고 HTTP, loopback/private/cluster host, userinfo/path/query/fragment/wildcard와 malformed port를 거부한다. Docker/Helm Frontend profile은 내부 HTTP 주소를 stream server route의 `API_URL`에만 두고 `NEXT_PUBLIC_API_URL`로 주입하지 않는다. Provider-neutral production reference는 ingress를 기본 비활성화하고 operator가 HTTPS termination과 `/api` Gateway route를 함께 설정하며, repository Nginx의 일반 `/api` route는 Next.js를 우회해 Gateway로 직접 연결된다.
- Production/unknown Gateway CORS는 공개 HTTPS origin만 허용하고 development/test는 loopback HTTP만 예외로 허용한다. Helm production/default profile과 공식 `scripts/dev.sh`는 이 정책과 일치하는 `NODE_ENV`를 명시하고, 잘못된 전체 origin 문자열은 startup error나 log에 포함하지 않는다.
- Browser access readiness report는 current active pointer의 `chatbot`/`widget`만 분류하고 `legacy_null`, `malformed`, `disabled`, `enabled` count와 safe deployment ID/version/type만 출력한다. Raw origin, app/organization identity, graph/config와 secret은 출력하지 않는다.
- Runtime principal mapping은 authenticated execution subject, credential principal, billing principal과 audit actor를 구분하며 public actor에 app/deployment creator를 합성하지 않는다.
