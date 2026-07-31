# Deployment Component Spec

Status: Draft

## Outbound Proxy Enforcement

- Application guard는 operation 의미 정책을, explicit transport는 exact internal Squid endpoint와 no-fallback을, Compose/NetworkPolicy는 direct socket 차단을 담당한다. Squid는 actor, organization, HTTPS path/header/body를 판정하지 않는다.
- Helm은 `egressProxy` root block을 단일 배포 권위로 사용한다. Image digest, policy revision, listener, authorized pod CIDR, replica/resource와 `canary|final` phase를 component별로 중복 정의하지 않는다. 기본 `values.yaml`은 local/development profile이므로 proxy를 비활성화하고, production profile이 proxy를 명시적으로 활성화하면서 exact authorized source CIDR을 제공한다.
- Gateway와 Knowledge Worker는 `3128` HTTPS CONNECT-only listener를, Workflow Worker는 `3129` HTTP-compatible listener를 사용한다. 두 포트는 `proxy-v1`의 immutable contract다. 세 workload의 external Connector DB/SSH는 별도 `connector-egress-v1`의 immutable `3130` listener를 사용하며, 검증 IP와 최대 16개의 배포 관리 포트 allowlist만 허용한다. Proxy mode의 PostgreSQL Connector runtime도 이 allowlist를 기본 정책으로 사용하고, `gevent` Worker의 blocking DB driver는 OS-native local relay를 통해 CONNECT tunnel에 연결한다. Helm은 listener override와 Connector test/egress allowlist 불일치를 render 전에 거부한다. Workflow listener의 CONNECT는 HTTPS 443과 address-pinned IMAP 143/993만 허용한다. `HTTP_PROXY`, `HTTPS_PROXY`, `ALL_PROXY`, `NO_PROXY`를 Pod에 주입하지 않는다.
- `canary`에서는 strict workload NetworkPolicy가 `nodease.io/egress-mode=proxy-v1` revision만 선택한다. `final`에서는 revision selector를 제거해 component의 unlabeled stale pod도 direct egress를 유지할 수 없게 한다. Phase는 Pod annotation에 남으며 rollout pause는 operator runbook이 소유한다.
- Gateway, Knowledge, Workflow, Logger, Beat, Frontend, Squid와 Sandbox의 strict egress policy는 `egressProxy.networkPolicy.dns`의 단일 DNS peer helper를 사용한다. 기본은 `kube-system` namespace 전체이며, 운영 환경은 실제 DNS namespace와 필요한 경우 Pod label selector를 제공한다. NodeLocal/CIDR DNS는 별도 network 계약 없이 암묵적으로 허용하지 않는다.
- External DB·Redis·Sandbox NetworkPolicy 좌표는 공통 semantic CIDR validator를 사용한다. RFC1918/ULA private network는 해당 private block 안에서만 허용하고 public dependency는 exact `/32` 또는 `/128` host만 허용해 여러 broad CIDR을 조합한 direct public egress 우회를 차단한다.
- Logger와 Beat는 DB/Redis, Frontend server는 같은 release의 bundled internal Gateway만 사용한다. Proxy-only Frontend에서 bundled Gateway를 끄거나 `API_URL`을 다른 backend로 바꾸면 Helm validation이 실패한다. Sandbox는 일반 proxy source가 아니며 사용자 코드의 `enable_network=true`를 API·Scheduler·Executor·NSJail command builder에서 `sandbox.network_access_unsupported`로 거부한다. Mail IMAP은 Worker-only tunnel 안에서도 ADR-0031의 IP pinning과 hostname TLS를 유지한다. Connector DB/SSH transport도 HTTP operation policy와 분리되지만 물리적인 public socket은 `3130` tunnel로만 열고 direct fallback하지 않는다.
- Gateway/Knowledge와 Workflow image는 사용하는 tiktoken encoding 및 NLTK corpus를 build 단계에 포함한다. Workflow image는 build에서 가져온 E5와 opt-in CrossEncoder identity를 final stage runtime 환경에도 동일하게 전달한다. Runtime network download는 허용하지 않으며 선택적 keyword 자산 누락은 bounded warning과 기능 생략으로 닫는다.
- External raw parser는 Knowledge approval/guarded transport가 완성될 때까지 구성 가능한 outbound adapter가 아니다. `llamaparse`는 source fetch와 credential/SDK 접근 전에 safe reason으로 종료하고 PyMuPDF 결과로 조용히 대체하지 않는다.
- Squid access/cache log는 비활성이다. UI/API surface는 추가하지 않으며 operator는 readiness, replica/resource와 safe failure bucket만 관측한다.

## Screens

- Deployment flow modal or equivalent deployment settings surface.
- Existing App/workflow deployment list and activation controls.
- Workflow editor의 게시하기 메뉴와 공개/내부 챗봇 배포 성공 화면.

## Components

- `DeploymentCredentialPolicyService`는 immutable deployment graph의 canonical LLM location과 purpose별 model type을 검증한다. Main generation은 location당 하나, query embedding은 location/model당 하나의 active server-owned policy를 관리하고 purpose를 생략한 legacy request는 main generation으로 해석한다. Manager actor와 credential `use`를 server-side 검증하며 graph나 runtime actor가 credential principal을 공급하지 못하게 한다. Query policy write는 기본-disabled server rollout mode를 통과해야 하며 manager 권한 확인 뒤 mode가 비활성인 요청을 provider selection과 row mutation 전에 거부한다.

- `DeploymentFlowModal` calls `POST /api/v1/deployments/preflight` before active create.
- If preview returns `blocked`, deployment creation is not attempted and the existing error step displays safe reason labels and required actions.
- If preview returns `warning`, deployment creation continues. The successful deployment result carries only the formatted safe warning, and `SuccessStep` displays it in an amber text banner with `role="status"`.
- Collection preflight copy may show affected Collection count bucket and candidate-budget-limited boolean. It never renders selected Collection/child identifiers, labels, membership, or exact hidden counts.
- Privacy-aware Knowledge preflight는 stale/invalid manifest, frozen/current validity epoch 불일치 legacy, retired/expired legacy와 artifact 부재를 하나의 fixed `knowledge_privacy_artifact_unavailable` label과 `reprocess_or_remove_unavailable_knowledge` action으로 표시한다. Invalidating epoch commit은 item projection/cleanup 전에도 runtime과 같은 resolver에서 즉시 반영한다. Document, policy, provider identity와 exact affected count는 렌더링하지 않는다. Inactive warning은 저장 가능성만 뜻하며 활성화 가능 또는 privacy 승인으로 표현하지 않는다.
- Existing activation toggle controls surface `deployment.preflight.blocked` responses without showing hidden KB identity.
- `DeploymentFlowModal`과 activation toggle은 기존 preflight response에서 MBA-219의 generic node reason/action을 함께 표시한다. Mail credential identity, Slack token/Webhook URL/channel/payload와 raw configuration은 렌더링하지 않는다.
- Active delete controls do not need preflight display in MBA-176 because delete no longer auto-promotes another deployment.
- 게시하기 메뉴는 공개 `chatbot`과 `internal_chatbot`을 별도 항목으로 제공한다.
- `SuccessStep`은 공개 챗봇에는 `/embed/chat/{url_slug}` 링크만, 내부 챗봇에는 `/modules/{workflow_id}/run?deploymentId={deployment_id}` 인증 링크만 표시한다. 내부 챗봇 결과에는 public REST API secret/test panel을 표시하지 않는다.
- Public `chatbot`/`widget` deployment form은 versioned browser access toggle과 exact parent origin editor를 제공한다. 기본 disabled, enabled 시 1~20개가 필요하며 `internal_chatbot`/다른 type에는 노출하지 않는다.
- Preflight response의 server canonical `normalized_browser_access_policy`가 최종 preview다. Client parser는 조기 UX validation만 담당한다.
- Release B 전 운영자는 `scripts/report_deployment_browser_access_readiness.py`의 safe JSON으로 current active public Chatbot/Widget의 `legacy_null`, `malformed`, `disabled`, `enabled` 건수와 deployment ID/version만 확인한다. 이 도구는 origin, app/organization identity, graph/config와 secret을 출력하지 않는다.
- Existing browser policy 변경은 source deployment를 복제한 inactive revision을 만들며 activation은 기존 deployment list/toggle flow에서 명시적으로 수행한다. 활성 revision만 `집행 중`, 비활성 revision은 `활성화 후 적용`으로 표시한다.
- Workflow 설정 사이드바의 공개 Chatbot/Widget URL은 deployment list가 반환한 App `url_slug`로 구성하며 slug가 없는 응답으로 `/embed/chat/undefined` 링크를 만들지 않는다.
- Embed 결과는 disabled에서 direct link만, enabled에서 iframe snippet을 표시한다. Release A compatibility 기간에는 아직 집행되지 않는 policy를 `집행 대기`로 표시하고 enforcement 완료처럼 표현하지 않는다.
- Target Conversation Memory preflight snapshot includes immutable deployment version/snapshot hash, conversation mapping and node Memory policy version, contract/storage generation and required Worker capability. Runtime revalidates the same binding and never resolves an existing session through the latest active deployment pointer.
- Public Chatbot and authenticated internal Chatbot use separate runtime policy/composition dependencies. They may share a visual Client component, but not auth/CORS/Origin, access permission, preflight audience or session namespace.
- Public `chatbot`/`widget` parent embedding origins are a deployment-owned versioned policy adapter used only to render CSP `frame-ancestors`. Memory or Client code must not reuse them as a CORS allowlist or read environment fallback to widen them.
- Frontend browser API resolver는 production에서 same-origin `/api/v1`을 기본으로 사용하고 명시적인 공개 HTTPS origin만 수용한다. 일반 `/api`는 Ingress/Nginx가 Gateway로 직접 라우팅하고 stream server route만 server-only `API_URL`을 사용한다. 이 내부 service URL을 `NEXT_PUBLIC_API_URL`이나 browser bundle로 projection하지 않는다.

### Public Webhook Components

- Deployment success UI와 Webhook trigger node panel은 secret을 query에 결합한 URL을 생성·복사하지 않는다. Endpoint URL과 secret을 분리해 표시하고 primary 방식인 `Authorization: Bearer` header 설정을 안내한다. `X-Webhook-Secret`은 기존 caller 호환을 위한 API 계약으로만 유지하며 UI에서 권장하지 않는다.
- `AppAuthSecretControl`은 safe status를 먼저 조회하고 사용자의 명시적 확인 뒤 최초 발급 또는 rotation을 실행한다. 일반 App·Deployment 응답에서 secret을 읽지 않는다.
- Status 조회도 shared cache나 브라우저 재사용으로 stale version이 남지 않도록 `no-store, no-cache` 계약을 사용한다.
- Status의 `rotation_enabled=false`이면 발급·교체 command를 렌더링하지 않고 Gateway 전환 대기 상태만 표시한다.
- 성공한 신규 secret은 발급 version과 함께 component memory에만 유지하고 한 번 표시·복사할 수 있다. 같은 control이 성공 응답을 부모 state로 전달한 것만으로 status를 즉시 재조회하거나 원문을 숨기지 않는다. 다시 mount된 control과 success test panel은 no-store status version이 이 발급 version과 일치할 때만 원문을 재표시·복사·Authorization header에 사용한다. 화면을 닫거나 새 rotation이 성공했을 때, 또는 response 유실·다른 탭 rotation 뒤 status refresh가 보존한 version과 다른 값을 확인했을 때 지운다. 교체 확인을 열었다가 취소하거나 같은 version의 실패를 확인한 경우에는 기존 원문을 유지한다. local/session storage, URL, analytics, toast detail과 Client log에는 넣지 않는다.
- Rotation UI는 기본 5분 전환 유예와 `이전 secret 즉시 폐기` 선택을 구분한다. Mutation request는 자동 재시도하지 않고 version conflict 또는 응답 유실 시 status를 새로 읽도록 안내한다.
- Status/rotation 권한이 없거나 resource가 숨겨진 경우 secret 상태나 App 존재 여부를 추론할 수 있는 상세를 렌더링하지 않는다.
- Secret을 URL, `localStorage`, `sessionStorage`, analytics, toast 또는 Client log에 넣지 않는다. 일반 App·Deployment response에는 원문 또는 masked preview를 포함하지 않는다.
- `AppAuthSecretService`는 status, App row lock, expected-version CAS, verifier 검증, current/previous transition과 필수 audit outbox transaction을 소유한다. Endpoint와 deployment/webhook ingress는 verifier 규칙을 복제하지 않는다.
- Expand release는 lifecycle mutation gate를 기본 `disabled`로 유지하고 generation 0 raw-only late arrival만 제한적으로 검증한다. 모든 Gateway Pod가 verifier-aware revision으로 수렴한 뒤 별도 설정 rollout에서 gate를 `active`로 바꾸며, 활성화 뒤 persistence adapter는 current raw를 저장하지 않는다. Generation 1 이상에서는 verifier만 인증 권위다.
- Active API/Webhook 배포 모달은 preflight 이전 input 단계에서 App secret status와 발급·교체 control을 표시한다. Preflight가 `app.auth_secret_lifecycle_unavailable` 또는 `deployment.app_auth_secret_required`를 반환하면 배포를 시도하지 않고 Gateway 전환 대기 또는 같은 App secret 발급 action을 유지한다. Input 단계에서 발급한 one-time secret은 발급 version과 함께 배포 모달이 열린 동안에만 메모리에 보존하여 success 단계의 복사·테스트에 전달하고, success 단계는 status 재검증이 끝날 때까지 이를 테스트 header에 사용하지 않는다. 모달을 닫거나 다시 열면 폐기한다. Inactive draft 저장은 유지한다.
- 기존 configured App을 재배포한 success 화면은 사용자가 이미 보관한 secret을 password input으로 일시 입력해 REST 실행을 확인할 수 있다. 이 값은 해당 component memory와 Authorization header에만 사용하며 App/Deployment state, browser storage, URL, analytics와 log에 기록하지 않는다.
- API/Webhook input은 App secret safe status를 `checking`, `ready`, `secret_required`, `lifecycle_unavailable`, `status_unavailable`로 투영한다. `configured=true`이면 lifecycle mutation gate가 disabled여도 기존 credential로 배포할 수 있으므로 `ready`이며 발급·교체 command만 숨긴다. 미설정, status 조회 실패 또는 App identity 누락은 `ready`가 될 때까지 다음 단계를 fail-closed로 차단한다.
- Client readiness gate는 사용자 흐름의 조기 차단일 뿐 활성화 권위가 아니다. Gateway는 create/toggle에서 현재 App 상태를 다시 검증한다. Client 오류 formatter는 top-level 표준 error envelope과 legacy nested envelope을 모두 읽되, required action은 allowlist된 label만 표시한다.
- Gateway endpoint는 ASGI/HTTP inbound adapter로 raw header occurrence와 query key presence를 추출하고 bounded stream을 수신한다. Framework-independent `application/webhook_ingress` policy가 credential/media/JSON limits를 판정하며 endpoint는 typed error를 static HTTP code로 mapping한다.
- Existing capture, active deployment/runtime policy, budget와 background publish orchestration은 ingress validation 뒤의 transitional path로 유지한다. Client validation은 보안 판단이 아니며 Gateway를 우회할 수 없다.
- Gateway의 outermost ASGI middleware는 `/api/v1/hooks` query에서 `token` field를 값 보존 없이 제거하고 boolean marker로 400 rejection을 유지해 Uvicorn access log 노출을 막는다. Repository Nginx는 `/api/v1/hooks/` 전용 location의 access/error log를 억제하고 1 MiB/5초 idle body guard와 request streaming을 적용한다.

### Internal Schedule Dispatch Components

- `SchedulerService`는 periodic tick lifecycle만 담당하는 inbound adapter다.
- Deployment application의 occurrence/dispatch use case가 claim, budget decision, next-run advancement, audit와 publish state transition을 조율한다.
- Existing `apps/gateway/composition/deployment.py`가 preflight와 분리된 schedule dispatch builder로 SQLAlchemy repository/UnitOfWork, schedule audit recorder, next-fire calculator와 Celery publisher를 주입한다.
- MBA-219 schedule dependency builder는 같은 DB session의 configuration preflight port를 추가로 주입한다. Schedule application use case는 deployment preflight concrete class를 import하지 않고 canonical graph snapshot과 organization만 port에 전달한다.
- Workflow Engine Celery task는 claim locator를 application use case로 전달하고 result를 task outcome으로 mapping한다. `apps/workflow_engine/composition/schedule_dispatch.py`가 DB adapter, audit, runtime policy use case와 engine 생성을 조립하며 task 본문은 세션 수명과 단계 호출만 담당한다.
- Public claim 조회/redrive UI는 제공하지 않는다. Outcome unknown acknowledgment는 protected operational job/CLI에서만 수행한다.
- 기존 deployment list/modal은 claim status나 idempotency key를 사용자에게 표시하지 않는다.
- Invalid legacy cron/timezone은 원문 대신 `schedule_configuration_invalid`만 Schedule row에 durable하게 기록하고 due/uninitialized 후보에서 제외한다. 유효한 schedule configuration 저장 시 quarantine code와 cursor 상태를 재계산한다.
- Admission correlation의 `WorkflowRun` row가 visibility grace 이후에도 없으면 claim에 one-time reported timestamp와 system audit만 기록한다. 이 signal은 Log System 지연/누락을 관측하기 위한 것이며 workflow 또는 external effect를 replay하거나 raw run identity를 노출하지 않는다.

### Schedule Dispatch Configuration Contract

현재 지원 상태(ADR-0068): Docker Compose와 provider-neutral Helm은 disabled mode만 지원한다. EKS workflow/raw manifest/Terraform은 제거됐고 provider-neutral coordinated CD는 아직 구현되지 않았다.

- Gateway와 Workflow Engine은 동일한 SCHEDULE_DISPATCH 환경변수 집합을 각 composition에서 검증해 주입받는다. 설정 파싱은 apps/shared/domain/schedule_dispatch.py가 소유한다. Helm helper는 non-disabled mode를 render 단계에서 거부하고 Docker Compose는 mode와 fingerprint를 `disabled`로 고정한다.
- Celery Worker process는 task 소비 전 이 공통 설정을 검증한다. startup hook을 우회한 전용 schedule task도 잘못된 설정을 raw error나 자동 retry로 노출하지 않고 safe permanent rejection으로 종료한다.
- `SCHEDULE_DISPATCH_MODE`의 기본값은 `disabled`다. `claim` 활성화는 Alembic migration 적용, disabled rollout, 기존 direct task drain과 pod 설정 일치 확인 이후에만 수행한다. `drain`은 신규 occurrence를 만들지 않고 이미 생성된 claim만 처리한다. `claim -> drain -> disabled`는 application rollout rollback이며, system schedule 실행 이력, admitted claim의 durable run correlation, active/unreviewed claim 또는 configuration quarantine이 남은 DB의 과거 schema downgrade는 모든 schedule revision에서 safe하게 거부된다.
- 단일 Helm release는 disabled bootstrap/image packaging만 허용하고 non-disabled mode를 render 단계에서 거부한다. 현재 claim/drain 전환을 수행하는 공식 CD는 없으며 수동 kubectl 전환은 지원하지 않는다.
- Gateway와 Worker는 공통 schema readiness service를 사용하고 `claim`/`drain` startup에서 Alembic head와 필수 claim column을 모두 확인한다. Alembic online migration은 같은 connection에서 bounded wait advisory lock을 획득해 동시 migration을 직렬화한다.
- Target CD(미구현): provider-neutral coordinated rollout은 동일 immutable release의 Log System image를 migration 이후, schedule claim admission 이전에 배포·검증해야 한다. disabled 최초 도입에 한해서만 fingerprint annotation 누락을 bootstrap으로 취급한다.
- Helm은 mode와 모든 dispatch batch/lease/deadline/retry/retention 값을 포함한 canonical nodease.io/schedule-dispatch-fingerprint Pod annotation을 기록하고 Downward API로 SCHEDULE_DISPATCH_MODE_FINGERPRINT를 주입한다. Docker Compose는 고정된 disabled mode와 나머지 bounded 설정으로 같은 canonical fingerprint를 직접 주입한다.
- Helm workload identity는 root `serviceAccount` helper와 template이 소유한다. Gateway, Workflow Worker와 Knowledge Worker Pod는 이 이름을 명시하며 production provider annotation은 root `serviceAccount.annotations` 한 곳에만 둔다.
- Helm 문서 저장소 설정은 root `storage.type/bucketName/region`만 사용한다. ConfigMap render helper는 type을 정규화하고 `CLOUD`의 두 좌표를 필수로 검사하며 implicit region default를 만들지 않는다. Gateway, Workflow Worker, Knowledge Worker는 `LOCAL`에서 cloud 전용 env를 참조하지 않고 `CLOUD`에서만 `S3_BUCKET_NAME`과 `AWS_REGION`을 함께 참조한다. CI는 기본/production 실제 render에서 필수 ConfigMap key와 Pod reference가 닫혀 있는지 검사한다. 과거 `gateway.env`/`worker.env` storage override가 남아 있으면 조용히 무시하지 않고 root block 이관을 요구하며 render를 중단한다. Gateway `Settings`는 같은 계약을 시작 시점에 재검증하고 S3 client 생성 전 실패하므로 Helm 외 Compose/direct 실행도 runtime까지 지연된 오류나 LOCAL fallback을 만들지 않는다. S3 adapter는 하나의 botocore session default config를 S3와 workload identity nested STS client에 공유해 explicit proxy와 bounded retry를 적용한다. Upload/presign provider 예외는 stable safe error로 변환하고 원문을 자체 로그에 기록하지 않는다.
- Provider-neutral production values는 Knowledge worker replica 2개와 process concurrency 2, singleton Beat를 활성화한다. Chart helper는 worker-only 또는 non-positive capacity를 render 전에 거부한다. Worker init container와 Celery bootstep은 migration/schema 및 credential keyring readiness를 fail-closed 확인하고, Pod readiness는 `knowledge@<pod-hostname>` exact destination으로 Celery self-ping을 보내 해당 consumer와 broker control path만 확인한다. Probe process는 내부 ping을 2초로 제한하고 Kubernetes exec timeout은 5초로 process 전체를 제한한다. 이 신호는 개별 ingestion 성공이나 queue drain을 보증하지 않는다. Redis 장애는 Pod를 `Unready`로 만들되 liveness restart loop를 만들지 않으며 health command는 raw failure detail을 출력하지 않는다.
- Support-surface guard는 승인 workflow path allowlist와 provider-specific content signal을 함께 사용한다. 같은 bounded reader가 지원하는 정적 참조 형식의 local reusable workflow/action, composite action runtime file과 `scripts/**` 실행 파일·Python module을 전이적으로 따라가므로 최상위 metadata가 안전한 위임처럼 보이게 하여 provider-specific 실행을 숨길 수 없다. 발견된 참조의 허용 prefix 밖 경로, 누락·symlink·non-UTF-8·과도한 크기/깊이/개수는 fail-closed한다. 참조된 스크립트만 바꾸는 selector 우회를 막기 위해 repository execution-closure 검사는 모든 PR의 scope 분류 전에 실행하고, workflow/composite action 변경은 deployment validation에서도 다시 검사한다. Guard·allowlist 변경 자체는 CI control path로서 current-head 독립 승인을 요구한다.
- Direct Gateway 개발 예시는 runtime과 동일하게 `LOCAL | CLOUD`만 사용한다. 현재 `dev/.env.example`은 로컬 파일 저장을 위한 `LOCAL` 예시이며, 운영 의미의 legacy `PROD` 값을 storage mode로 사용하지 않는다.
- Scheduler tick은 critical recovery, occurrence claim, pending dispatch를 먼저 수행한다. WorkflowRun visibility와 terminal cleanup은 각각 독립 UnitOfWork의 optional maintenance로 실행되어 실패가 dispatch를 중단하지 않는다.
- Pending dispatch는 canonical deployment/type/current-pointer 검증 뒤 configuration preflight를 budget보다 먼저 수행한다. Known blocker는 같은 UoW에서 `canceled + configuration_preflight_blocked`와 기존 canceled audit을 기록하고 publish batch에 넣지 않는다. Adapter/infrastructure exception은 UoW를 rollback해 fail-open을 막는다.
- `disabled`에서는 critical dispatch를 실행하지 않지만 schema-ready 환경의 visibility, retention cleanup과 pending/running age signal은 계속 실행한다.
- Target CD(미구현): coordinated production rollout은 mutable tag를 deployment identity로 사용하지 않고 OCI registry digest를 확정한 뒤 Logger/Gateway/Worker를 immutable image identity로 배포·검증해야 한다.
- Schedule Celery publisher/task는 `ignore_result`를 사용하고 workflow output, RAG evidence, sync 상세를 result backend에 저장하지 않는다.
- Schedule signal과 Scheduler/Worker 오류 로그는 UUID, idempotency key, raw payload와 raw exception message를 기록하지 않는다. 개별 claim 역추적은 durable claim과 canonical audit row를 사용한다.
- Outcome review use case는 exact dead-letter claim을 lock하고 allowlisted resolution/correlation과 system audit만 같은 transaction에 기록한다. Rollback preflight는 별도 read use case/CLI이며 review와 redrive를 수행하지 않는다.
- 현재 provider-specific production Gateway/Worker/coordinated workflow는 지원 표면에 없다. Future provider-neutral CD는 migration, immutable render, Logger 선행, staged Gateway/Worker apply, live convergence와 실패 재개를 하나의 승인된 경계에서 수행해야 한다.
- Target CD(미구현)는 claim에서 disabled로의 직접 전환을 거부하고 activation/rollback 전 transition preflight로 nonterminal claim과 미검토 outcome unknown이 0인지 확인해야 한다.
- Activation은 Legacy `workflow.execute_by_deployment`와 신규 dedicated schedule task, rollback은 신규 task의 active/reserved/scheduled 상태와 Redis workflow priority queue 전체 depth가 0인지 추가 확인한다. DB, worker inspection 또는 broker queue 확인 불가 시 fail-closed하고 Queue payload 원문은 읽거나 출력하지 않는다.
- Polling, batch size, lease/delivery/execution deadline, retry cap, retention 값은 환경변수로 조정할 수 있지만 domain range validation을 통과해야 한다. 잘못된 mode 또는 범위를 가진 값은 process startup에서 fail-fast한다.
- 이 설정은 secret이 아니지만 pod 환경과 운영 배포 이력에 남을 수 있으므로 raw workflow payload, credential, audit metadata와 섞어 기록하지 않는다.

## States

- `checking`: Preflight/create request in progress.
- `passed`: Preflight 통과 후 create를 계속 진행한다.
- `warning`: Create를 계속 진행하고 성공 화면에 non-blocking preflight warning을 표시한다.
- `blocked`: 활성 배포 생성/전환 불가. Error step 또는 toggle error message로 표시한다.
- `error`: 네트워크 또는 validation error. Hidden resource detail을 표시하지 않는다.

## Interactions

- Preview endpoint의 blocked response는 API 오류가 아니라 검사 결과로 처리하며, active create 요청을 보내지 않는다.
- Preview endpoint의 warning response는 active create를 계속하고 성공 결과에 safe warning text를 결합한다. Candidate budget warning을 배포 실패로 바꾸지 않는다.
- `is_active=false` 저장은 허용된 Knowledge activation warning과 null unresolved managed configuration warning을 보존할 수 있다. Non-null unavailable Mail credential, malformed graph, workflow-node structural 오류와 unsupported external validator처럼 inactive에서도 blocked인 결과는 저장하지 않는다. 현재 deployment modal은 active create만 제공한다.
- Active create 또는 activation toggle에서 `409 deployment.preflight.blocked`가 오면 error/blocked message로 safe reason과 required actions를 보여준다.
- Inactive preview는 null unresolved Mail/Slack configuration을 warning으로 표시하고 active create/toggle은 같은 문제를 blocked로 표시한다. Non-null unavailable credential, malformed graph와 unsupported external node는 inactive에서도 blocked로 표시한다.
- Shared graph validator는 endpoint, preflight와 Loop runtime이 최상위의 명시적 trigger/start 및 Loop body의 단일 implicit 진입점을 포함한 같은 structural contract를 사용하게 한다. Mail resource adapter는 bulk permission decision을 사용하며, preview는 무감사이고 enforcing facade는 확인된 same-organization permission denial을 정확히 한 번 감사한다.
- Workflow-node 대상은 node 설정의 target app 기준으로 검사된다는 점을 내부 상태에서 유지한다. UI copy는 workflow id나 hidden target identity를 노출하지 않는다.
- 내부 실행 페이지가 `401`을 받으면 현재 path/query/hash를 safe `next`로 보존해 로그인 화면으로 이동한다. 이메일/비밀번호 로그인만 same-origin `next`로 복귀하며 unsafe URL은 `/dashboard`로 닫는다.

## Accessibility

- Preflight status는 색상만으로 구분하지 않고 text label을 제공한다.
- 성공 화면의 non-blocking warning은 `role="status"`와 text reason/action을 제공한다.
- Required action list는 keyboard focus 순서 안에 있어야 하며, hidden KB id/name/path를 포함하지 않는다.
