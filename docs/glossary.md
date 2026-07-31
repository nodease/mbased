# Glossary

Status: Draft

이 문서는 Nodease/Moduly 문서 전반에서 반복해서 쓰는 제품, 아키텍처, 데이터 모델 용어의 기준 정의다. 기능별 문서에서는 아래 용어를 재정의하지 않고 이 문서를 참조한다.

## Product Naming

| 용어 | 정의 |
| --- | --- |
| Nodease | 기존 Moduly 코드를 리팩토링해 만드는 신규 서비스명. 기업 내부 AI workflow/LLMOps 운영 플랫폼을 가리키는 제품 관점 명칭이다. |
| Moduly | 리팩토링의 출발점이 되는 기존 코드베이스와 현재 코드/배포 리소스에 남아 있는 명칭. 기존 코드, 컨테이너, Helm chart, README 실행 명령 등 인프라 식별자는 Moduly 기준으로 읽는다. |

## Organization And Permissions

| 용어 | 정의 |
| --- | --- |
| Organization | 조직 범위와 tenant-like boundary. DB에서는 단수형 `organization` table을 사용한다. 리소스 접근 scope의 최상위 기준이다. |
| Active Organization | 요청자가 현재 작업 대상으로 선택한 organization. API 요청에서는 `X-Organization-Id` header로 전달한다. |
| Organization Scope | 리소스가 속한 organization 경계. scope 밖 리소스는 존재 여부를 숨기기 위해 `404`로 응답한다. |
| OrganizationMembership | User가 Organization에 직접 소속되어 있음을 나타내는 1차 관계. `organization_memberships` table이 기준이다. |
| Team | Organization 안의 권한 부여 단위. DB에서는 `teams` table을 사용한다. |
| TeamMembership | User가 Organization 안의 Team에 배정되어 있다는 관계. Team permission 계산의 전제이며, organization 소속 자체의 기준은 아니다. |
| RBAC | Role-Based Access Control. Nodease에서는 고정 role table 대신 organization membership, team permission, user direct permission, `auth_state`로 판정한다. |
| auth_state | DB enum이 아닌 application-level permission state string. 운영 권한은 `none`, `viewer`, `operator`, `builder`, `manager`, 감사 권한은 `auditor`, `raw_auditor`를 사용한다. |
| Team Permission | Team 단위 resource 권한. `team_workflow_permissions`, `team_knowledge_permissions`, `team_llm_permissions`, `team_audit_permissions`를 사용한다. |
| User Direct Permission | Team 권한으로 처리하기 어려운 user별 additive allow 예외 권한. Workflow, Knowledge Base, LLM Credential 같은 resource별 user direct permission table로 표현하며, team 권한을 낮추는 explicit deny로 쓰지 않는다. |
| Resource | 권한 판정 대상이 되는 업무 객체. 대표적으로 Workflow, Knowledge Base, LLM Credential, Audit 대상 organization이 있다. |
| Explicit Deny | 명시적 거부 권한. 현재 권한 모델에는 도입하지 않는다. 권한 판정은 허용 권한 중 가장 강한 값을 선택하는 방식이다. |
| Knowledge Domain Permission | Organization 안의 Knowledge 관리 업무를 Team/User에게 위임하는 additive allow. `catalog_manage`, `permission_delegate`, `lifecycle_manage`, `sync_manage`를 사용하며 KB content access나 Collection route를 자동 부여하지 않는다. |
| Knowledge Delegator | Domain `permission_delegate` 또는 resource `manage`로 KB/Collection resource grant를 관리하는 actor. Domain delegator는 자신이나 자신이 속한 Team에 content-plane 권한을 부여해 self-escalation할 수 없다. |

## Workflow And Execution

| 용어 | 정의 |
| --- | --- |
| App | 제품상 project boundary. DB에서는 기존 `apps` table을 사용한다. 하나의 App은 workflow와 deployment의 상위 단위로 취급된다. |
| Workflow | 사용자가 캔버스에서 구성하는 자동화 흐름. DB에서는 `workflows` table을 사용한다. |
| Canvas | Workflow를 편집하는 화면/표면을 가리키는 제품 용어. 별도 데이터 엔티티가 아니라 Workflow editing surface다. |
| Node | Workflow 안의 실행 단위. LLM, Condition, HTTP, Email, Code, Webhook 등 구체 노드 타입으로 동작한다. |
| Workflow Run | Workflow 실행 1회를 나타내는 실행 기록. DB에서는 `workflow_runs` table을 사용한다. |
| Workflow Node Run | Workflow Run 안에서 개별 node가 실행된 기록. DB에서는 `workflow_node_runs` table을 사용한다. |
| Deployment | Workflow를 공개 또는 인증 내부 실행 표면으로 활성화한 결과. DB에서는 `workflow_deployments` table을 사용한다. |
| Public Chatbot | `DeploymentType.chatbot`으로 게시하는 무인증 공개 채팅 표면. Execution subject 없이 anonymous public-only RAG 경계를 사용한다. |
| Internal Chatbot | `DeploymentType.internal_chatbot`으로 게시하는 인증 내부 채팅 표면. 로그인 사용자를 execution subject로 사용하고 workflow·Knowledge 권한을 실행 시점에 다시 검사한다. |
| Execution Subject | Workflow 실행 시점에 Knowledge/source 등 데이터 접근 권한을 평가하는 실제 사용자 또는 승인된 실행 주체. Interactive user 또는 향후 승인된 service account가 될 수 있다. Workflow/deployment owner, credential/billing principal과 Conversation Access Grant로 대체하거나, 명시되지 않은 경우 owner 권한으로 fallback하지 않는다. |
| Schedule | Deployment 실행을 정해진 시간/주기로 트리거하는 설정. DB에서는 `schedules` table을 사용하며 deployment와 1:1 관계다. |
| Webhook | 외부 시스템이 HTTP 요청으로 Workflow를 실행하게 하는 인바운드 트리거. |
| Public Run API | 배포된 workflow를 app secret 기반 Bearer 인증으로 실행하는 public endpoint 계열. 일반 사용자 세션 인증과 구분한다. |
| Anonymous Public Audience | Execution Subject가 없는 public runtime의 principal kind. Public visibility/exposure policy만 평가하며 synthetic user/subject ID나 private permission을 만들지 않는다. |
| Credential Principal | Provider credential 사용 근거가 되는 server-derived principal. Credential 선택·사용에만 쓰며 Knowledge Execution Subject나 Audit Actor로 승격하지 않는다. |
| Billing Principal | Provider usage와 budget을 귀속할 organization/workflow/deployment 주체. Execution Subject, Credential Principal과 Audit Actor와 별도로 파생한다. |
| Audit Actor | 관리·보안 사건을 실제로 요청하거나 수행한 user/system/public 주체. Public Conversation request lifecycle은 `actor_id=null`, `actor_type='public'`, 비동기 physical purge/compliance completion은 `actor_type='system'`을 사용한다. App/deployment owner나 Access Grant를 actor로 합성하지 않는다. |

## Memory And Conversation

| 용어 | 정의 |
| --- | --- |
| Memory Bounded Context | Conversation session, turn, entry, summary, provenance와 retention lifecycle을 전문적으로 소유하는 독립 업무 경계. Gateway와 Workflow Engine은 Memory application contract로 협업하며 Memory table을 직접 변경하지 않는다. 초기에는 별도 network service가 아닌 모듈러 모놀리스 package로 도입한다 ([ADR-0030](decisions/ADR-0030-memory-bounded-context.md)). |
| Conversation Session | 특정 organization/app/workflow/deployment ID와 immutable deployment version 또는 snapshot hash, conversation mapping/Memory policy version, authenticated execution subject 또는 public audience에 binding된 여러 turn의 lifecycle aggregate. New, close, reset, delete, expiry와 retention 상태를 가지며 active deployment 변경에 자동 rebind하지 않는다. |
| Conversation Turn | 하나의 user request와 그 Workflow 실행 결과를 연결하는 logical 대화 단위. Queue delivery attempt와 구분하며 pending_dispatch/queued/running/completed/failed/cancelled 상태, request idempotency와 turn version을 가진다. |
| Conversation Memory | Conversation Session에서 생성된 completed turn과 승인된 bounded projection. Workflow execution log나 node 설정 자체가 아니며 current authorization, retention과 provenance policy를 적용받는다. |
| Node Memory | 특정 LLM node가 Conversation Memory의 어떤 channel/source를 어느 turn/token/summary/failure policy로 사용할지 정하는 versioned graph/deployment 설정. 기본값은 OFF다. |
| Memory Context | Current session/subject/audience, node policy, source authorization과 token budget을 적용해 LLM에 제공하는 bounded untrusted context. 사용자 transcript와 동일한 projection이 아니다. |
| Conversation Access Grant | Public conversation을 이어갈 권한을 주는 server-issued bearer capability. 사용자 identity, execution subject, credential/billing principal 또는 audit actor가 아니다. Secret으로 취급하며 V1 source-of-truth에는 verifier hash, session/deployment ID·version/audience binding, expiry와 `active`, `transcript_only`, `revoked`, `expired` state만 저장한다. Standalone rotation/grace는 지원하지 않고 reset은 old grant 즉시 revoke와 새 session/grant 원자 발급으로 처리한다. 응답 복구용 token 원문은 별도 암호화 replay store에 최대 10분만 보관하고 이후 same-key retry는 새 grant 없이 `memory.secret_replay_expired`로 닫는다. |
| Memory Data Dependency | Memory Entry가 어떤 source와 권한에 의존해 생성됐는지를 나타내는 server-derived provenance reference. Source kind, organization, canonical resource/version, sensitivity와 authorization-safe reference를 포함하며 raw source payload/path는 포함하지 않는다. V1에서는 값에 영향을 준 dependency와 결과를 선택한 활성 control dependency를 모두 필수로 취급한다. |
| Runtime Data Dependency Envelope | Knowledge, connector/tool, subworkflow, LLM, transform/code 결과가 값·제어 lineage를 잃지 않도록 Workflow Runtime에서 전달하는 bounded dependency 집합과 server-derived completeness marker. Client나 임의 node가 canonical dependency를 발급할 수 없고 각 node output은 모든 값 dependency와 결과를 선택한 활성 control dependency의 합집합을 상속한다. Explicit complete empty envelope은 허용하지만 missing/unknown envelope은 empty가 아니다. |
| Authorization Decision Revision | Source-owning authorization adapter가 특정 execution principal/resource에 내린 결정을 표현하는 opaque revision. Authenticated subject와 anonymous public audience에 공통으로 쓰며 membership, permission, visibility, source ACL 또는 lifecycle이 바뀌면 함께 변경한다. |
| Provider Execution Capability | [ADR-0064](decisions/ADR-0064-provider-execution-capability-boundary.md)에 따라 LLM Credentials domain이 authoritative하게 발급·검증하는 short-lived opaque capability. Canonical runtime scope, server-derived credential principal, credential permission decision, verified relation/provider-routing/pricing revision, invocation/admission/provider-attempt, main/summary purpose, 실제 request token/cost cap과 expiry를 identity/revision에 고정하고 provider 호출 전에 commit한다. Memory, Workflow와 Budget은 opaque identity/revision과 자기 operation binding만 소비하며 scope를 재정의하지 않는다. 현재 provider-routing fingerprint는 중앙 egress authorization을 뜻하지 않는다. |
| Turn Dispatch Job | Pending Conversation Turn과 같은 transaction에서 저장되고 Worker task publish/claim/Workflow admission observation/reconciliation을 조정하는 durable outbox/process state. Gateway의 일회성 broker publish 성공 여부를 실행 접수의 source of truth로 사용하지 않게 한다. |
| Provisional Memory Projection | 현재 turn 안에서는 작업 맥락으로 사용할 수 있지만 CompleteTurn 성공 전에는 다음 turn의 Conversation Memory 후보가 아닌 중간 node projection. |
| Summary Generation Job | Fenced generation lease, budget reservation, provider 호출, summary CAS, usage commit과 reconciliation을 durable하고 idempotent하게 조정하는 Memory process state. |
| Context Materialization Plan | Raw text를 복제하지 않고 ordered entry/summary reference, policy version, server-keyed content digest, lifecycle/content/source revision과 authorization decision revision set만 보존하는 short-lived Memory Context 조립 계획. Digest는 client/telemetry에 노출하지 않는다. |
| Memory Context Lease | Context Materialization Plan을 main provider adapter가 provider attempt와 Provider Execution Capability로 claim하고 current authorization을 재검증할 때 사용하는 short-lived authorization lease. Session/execution subject 또는 audience/node와 authorization decision revision에 binding되며 stale permission window를 제한한다. |
| Memory Context Provider Attempt | Context lease claim, durable provider-start marker, main provider outcome과 usage reconciliation을 연결하는 실행 단위. Start marker 전 crash와 start 이후 unknown outcome의 재시도 정책을 구분한다. Summary provider process는 별도 Summary Generation Job이 소유한다. |
| Purge Receipt | Conversation grant/session 접근이 즉시 차단된 뒤 비동기 purge 상태만 조회하도록 발급하는 scoped capability 또는 authenticated reference. Raw session content 접근 권한을 부여하지 않으며 public purge terminal 후 최소 24시간, 발급 후 최대 8일까지 유효하다. `completed_with_hold`는 compliance 격리 완료이지 물리 삭제 완료가 아니다. |
| Personal Agent Memory | 여러 conversation/workflow를 넘어 사용자 장기 선호·업무 맥락을 저장하는 향후 capability. Conversation Memory와 다른 opt-in, ownership, privacy와 view/edit/delete 정책이 필요하며 현재 구현 범위가 아니다. |

## Agent And Generation

| 용어 | 정의 |
| --- | --- |
| Agent | 제품 문맥에서는 사용자의 자연어 요청을 받아 workflow 생성을 돕거나 특정 workflow/node 안에서 제한된 작업을 수행하는 AI 실행 주체를 뜻한다. 현재 문서 범위에서 전역 Q&A 에이전트나 독립 DB 엔티티로 확정된 용어는 아니다. |
| Agent Builder | Accepted ADR-0045의 direct-edit UX, ADR-0046의 GraphMutation/CAS 저장과 ADR-0054의 생성 모드 계약을 따른다. ADR-0019는 Superseded Preview characterization 기록이다. 자연어 프롬프트를 typed GraphMutation으로 변환하며 기본 단계별 생성, eligibility를 통과한 빠른 생성과 고급 구조만 생성을 제공한다. 모든 모드는 같은 권한·validation·CAS·acknowledgement·Undo 경계를 사용하고 실제 parameter 값은 workflow graph에만 저장한다. |
| Agent Builder Intent Plan Cache | Accepted ADR-0063의 feature flag 기본 비활성 Gateway 내부 L1 cache. 결정적으로 동등하다고 검증 가능한 요청에 대해 provider 호출 전 cache-safe typed intent plan을 재사용한다. 완성 graph나 protected resource reference를 저장하지 않으며 hit에도 현재 권한, Knowledge, target, Catalog, GraphMutation과 CAS를 다시 검증한다. Redis는 source of truth가 아니고 오류 시 DB audit replay가 아닌 Planner cold miss로 fail-open한다. Production과 staging은 cache 전용 Redis와 외부 readiness evidence 확인 후 운영자가 설정한 attestation이 필요하다. |
| Agent Builder 생성 모드 | `guided_generate`는 graph 저장 뒤 Knowledge와 parameter를 단계별 확인하는 기본 모드, `quick_generate`는 안전하게 확정 가능한 변경안을 한 화면에서 검토하고 명시적으로 적용하는 모드, `structure_only`는 unresolved 설정을 남길 수 있는 고급 구조 생성 모드다. 기존 `configure_and_generate`는 guided mode의 호환 별칭이다. |
| 빠른 생성 Eligibility | LLM 판단이 아니라 Gateway의 결정론적 정책이 현재 권한, Catalog, graph/resource revision과 미해결 선택을 검사해 `quick_generate` 가능 여부를 판정하는 경계. Credential, 권한 resource, 외부 부수효과, Condition/HTTP/code/egress 또는 의미 있는 복수 후보가 남으면 단계별 생성 전환을 사용자에게 요청한다. |
| GraphMutation | MBA-228에서 `initial_graph`, `graph_edit`, `replace_workflow`, `parameter_update`, `knowledge_binding` 변경을 typed node/edge operation으로 표현하는 공통 API 계약. `generation_mode`와 mutation kind는 독립이다. Full operations는 일회성 응답에만 존재하고 DB에는 base/expected-result/result hash와 completion context를 가진 safe envelope만 저장한다. 최초 구조 mutation이 Agent Builder 시작 전/final graph의 Workflow history boundary를 만들고 후속 parameter/Knowledge mutation은 별도 history entry 없이 final hash만 갱신한다. `reverted`는 boundary operation 최종 상태이며 mutation kind가 아니다. |
| Parameter Task | Agent Builder가 Catalog의 configurable parameter 하나를 추적하는 안전한 진행 record. 실제 값은 담지 않으며 pending, active, completed, deferred, skipped, invalid, canceled 상태와 task version, policy, resolution source만 보존한다. 완료 첫 Workflow Undo는 마지막 task UI를 다시 표시하며 task 간 이동은 `이전 항목` control이 담당한다. 전체 boundary Undo는 모든 task를 canceled로 닫는다. |
| Knowledge Placement | Planner가 KB 선택별 완성 graph 대신 반환하는 typed topology 관계. Requirement와 timing, target/effect, Knowledge step 및 upstream/downstream/empty-selection bridge를 표현하며 backend가 Catalog template으로 KB 선택 graph와 KB-free graph를 결정적으로 구성한다. |
| Agent Skill | 특정 provider 기능이 아니라 Nodease 내부에서 재사용할 수 있는 일반적인 절차/context/routing artifact 개념. Workflow 생성, LLM node의 RAG 옵션 구성, 검증 checklist를 안내할 수 있지만 권한을 부여하거나 source of truth가 되지는 않는다. 현재 Knowledge 설계의 구체 구현 단위는 `Knowledge Skill`이며, Agent Skill은 전역 Q&A 에이전트나 독립 실행 권한을 뜻하지 않는다. |
| Wizard | Prompt/code/template 같은 특정 node 설정을 개선하거나 생성하는 보조 기능. 현재 코드에는 node 단위 wizard가 존재한다. |

## Knowledge And RAG

| 용어 | 정의 |
| --- | --- |
| Knowledge | 사내 문서와 데이터 소스를 저장, 색인, 검색, 추적하는 제품 영역을 가리키는 상위 용어다. |
| Knowledge Source | Knowledge가 수집하는 외부 또는 내부 원천 시스템. 예: Drive, Wiki, ticketing tool, chat archive, API, DB, object storage. Source 자체는 Nodease 권한을 부여하지 않으며 connector와 source ACL 정책을 통해 안전하게 수집된다. |
| Source Item | Knowledge Source 안에서 document-level KB 1개로 materialize될 수 있는 원자 항목. 파일, 페이지, ticket, thread, DB row 또는 API record가 될 수 있다. |
| Knowledge Source Connector | Source item과 source ACL을 열거, 가져오기, 동기화하는 adapter 계층. MCP/API source도 LLM 자유 tool-use가 아니라 이 계층 뒤의 server-side allowlist adapter로만 사용한다. Connector는 mbased permission을 직접 결정하지 않고 Outbound Egress Guard, protocol adapter policy, runtime source authorization boundary를 통과해야 한다. |
| Knowledge Base | 문서/source item 1개에 대응하는 permission, retrieval, sync, lifecycle atom. DB에서는 `knowledge_bases` table을 사용한다. Legacy 다중 `documents` row는 호환 read 대상일 수 있지만 신규 manual 등록은 최초 Document 하나로 제한한다. 여러 독립 문서를 함께 검색하는 묶음은 Knowledge Collection으로 구성한다. 세부 경계는 [ADR-0014](decisions/ADR-0014-knowledge-base-document-atom-and-collection-boundary.md)와 [ADR-0017](decisions/ADR-0017-knowledge-integration-provisional-implementation-baseline.md)을 따른다. |
| Knowledge Collection | 여러 document-level Knowledge Base를 묶는 grouping, routing, UX, operations 단위. Collection 권한은 하위 KB content retrieval 권한을 자동 부여하지 않는다. |
| Standalone Knowledge Base | 어떤 Knowledge Collection에도 연결되지 않은 KB. Collection membership은 선택 사항이며 하나의 KB는 0개 이상의 Collection에 연결될 수 있다. |
| Knowledge Collection Sync Job | 권한 있는 사용자가 요청한 KC 동기화를 durable하게 추적하는 DB record. Idempotency, single-flight, worker lease, retry, partial failure와 safe progress의 source of truth이며 child KB content 권한을 부여하지 않는다. |
| Knowledge Document Ingestion Job | Document process, sync, approval-resume 또는 reindex 한 generation의 durable DB record. Request admission, document single-flight, worker lease/heartbeat/fencing, retry/dead-letter와 terminal result의 source of truth다. Celery task ID나 Redis progress는 이 record를 대신하지 않는다. |
| Knowledge Ingestion Worker | Gateway parser/storage 의존성을 사용하면서 `knowledge` Celery queue만 소비하는 전용 worker. Job UUID만 입력받고 current authorization과 DB claim을 확인한 뒤 document ingestion을 실행한다. |
| Single-flight | 같은 Knowledge Collection에 queued/running sync job을 동시에 하나만 허용해 중복 요청과 Celery redelivery가 같은 target을 병렬 적용하지 못하게 하는 정책. |
| Safe Progress | Child KB/document/source identity와 정확한 hidden count를 노출하지 않고 `none`, `started`, `progressing`, `most`, `complete` 같은 범주로 표시하는 실행 진행 정보. |
| Knowledge Skill | Workflow Builder가 LLM node의 RAG 옵션을 구성할 때 어떤 source-of-truth tier를 먼저 볼지, 어떤 collection/KB 후보를 고려할지, 어떤 query template과 검증 절차를 쓸지 정의하는 Knowledge 도메인의 provider-neutral 절차 지식 artifact. Skill metadata/body/resource도 권한과 redaction-safe boundary 안에 있으며, 실제 근거는 KB/document version/citation에서 가져온다. MBA-145 Agent Builder MVP는 Knowledge Skill body/checklist를 prompt context로 직접 로드하지 않는다. |
| Skill Metadata | Skill 선택에 필요한 name, description, tag, owner, source tier, freshness 같은 요약 정보. 이 값 자체도 민감 metadata일 수 있어 organization/permission/display policy와 redaction/cap을 거친 safe field만 Workflow Builder, router, 실행 시점 RAG 경로에 제공한다. |
| Skill Freshness | Skill이 참조하는 source-of-truth version, 업무 절차, eval 결과가 아직 유효한지를 나타내는 상태. 예: `fresh`, `stale`, `review_required`, `deprecated`. |
| Skill Provenance | workflow draft, LLM node의 RAG 옵션, workflow test run, RAG strategy 비교가 어떤 skill id/version/freshness/eval 상태와 safe source reference를 사용했는지 남기는 redaction-safe summary. Raw skill body나 hidden source reference는 포함하지 않는다. |
| Source-of-Truth Tier | 정책 문서, ADR/decision record, semantic definition, curated query corpus처럼 근거로 삼을 source의 신뢰/우선순위 계층. Skill은 tier 선택 절차를 제공할 뿐 source of truth 자체가 아니다. Retrieval에서는 authorized evidence 안에서 ranking, tie-break, conflict resolution hint로만 사용한다. |
| Query Rewrite | 사용자의 자연어 query를 검색 친화적인 표현으로 바꾸는 LLM node의 선택적 RAG 기능. 접근 범위를 넓히거나 권한 판단을 수행하지 않으며, user query와 safe skill/template만 입력으로 사용한다. |
| Evidence Sufficiency Policy | 검색 결과만으로 답변할 수 있는지 판단하는 LLM node의 RAG 정책. 근거가 부족하면 LLM이 추측 답변을 만들지 않고 safe no-result 또는 insufficient-evidence 응답으로 닫게 한다. |
| Golden Question | Skill이나 retrieval strategy 회귀 검증에 쓰는 대표 질문/기대 근거 세트. 실제 raw restricted content가 아니라 safe fixture와 평가 기준으로 관리한다. |
| Collection Route Permission | Collection을 Agent/router 후보 scope로 사용할 수 있는 권한. Collection을 볼 수 있는 `collection.read`와 다르며, 하위 KB content retrieval 권한을 자동 부여하지 않는다. |
| Source Authorization Provenance | 외부 source ACL fact를 Nodease가 requester authorization과 freshness 판단에 사용할 수 있게 materialize한 safe provenance. KB `use` 권한 자체가 아니며 raw source principal/path/url을 user-facing surface에 노출하지 않는다. |
| Auto-ingested KB Use Provisioning | 자동 수집된 document-level KB가 retrieval 후보가 되도록 mbased KB `use` allow를 부여하는 절차. ADR-0017 baseline에서는 admin/team/user grant 또는 organization-approved connector/source policy가 `source_policy_kb_use_grants` row를 만들 때만 허용한다. Source ACL fact만으로는 KB `use`가 충족되지 않는다. |
| Document Version | document-level KB의 특정 색인/version artifact. 목표 모델에서는 active version만 기본 retrieval 대상이다. |
| Active Document Version | document-level KB에서 현재 retrieval-visible한 ready version. 새 version indexing/finalization이 성공하기 전까지 기존 active version을 비활성화하지 않는다. |
| Active Version Finalization | 새 document version의 redacted canonical text, chunks, embeddings, index artifact가 모두 준비된 뒤 active pointer와 processed state를 같은 finalization boundary에서 전환하는 단계. Crash recovery, fencing, outbox gate가 필요하다. |
| Document | 현재 구현의 Knowledge Base에 업로드되거나 연결된 원본 문서 메타데이터. 목표 모델에서는 `document_versions`로 전환된다. |
| Document Chunk | 검색과 citation을 위해 Document 또는 Document Version을 나눈 텍스트 조각. 목표 모델에서 chunk text, embedding input, retrieval-visible artifact는 redacted canonical text에서 생성된다. |
| RAG | Retrieval-Augmented Generation. 질문에 답하기 전에 Knowledge Base에서 관련 문서 조각을 검색해 LLM 응답에 활용하는 방식이다. |
| Retrieval | 질문 또는 query에 맞는 Document Chunk를 찾는 검색 과정이다. |
| Citation | 답변이 근거로 삼은 문서/청크 출처 정보. 일반 사용자용 Workflow/Chatbot Citation은 권한을 통과한 실제 prompt evidence의 제한된 safe projection이며 내부 resource identity와 raw content를 포함하지 않는다. 감사·운영용 privileged lineage는 별도 권한·보존 경계를 따른다. |
| Knowledge Permission Helper | KB permission, collection route scope, source ACL freshness/requester authorization을 service가 재사용할 수 있는 형태로 평가하는 helper. Router나 controller가 permission row 또는 raw source ACL을 직접 조합하지 않게 하는 경계다. |
| Safe Candidate Set | Permission helper와 source ACL gate를 통과해 router, Workflow Builder, 실행 시점 RAG 경로에 제공할 수 있는 KB/collection/skill 후보와 safe metadata의 집합. 권한 없는 resource id, raw source path/url/title, raw ACL fact, exact denied count를 포함하지 않는다. |
| Collection Router | Safe Candidate Set 안에서 질문에 적합한 collection/KB 후보를 선택하는 routing component. Access control을 수행하지 않고, 이미 필터링된 safe metadata만 소비한다. |
| Auto Collection Mode | 요청자가 explicit KB id를 직접 고르지 않고, route-allowed collection scope와 KB permission helper 결과로 safe candidate set을 만든 뒤 router가 후보 KB를 선택하는 목표 retrieval mode. |
| Explicit KB Mode | 요청자가 특정 KB를 명시하는 mode. Collection route permission을 생략할 수 있지만 KB visibility, KB permission helper, source ACL gate, final evidence policy는 생략할 수 없다. |
| Final Evidence Policy | LLM prompt, answer delta, citation preview, trace/audit summary에 evidence가 들어가기 전에 적용하는 최종 정책 gate. PII/secret, classification, source ACL, permission 상태를 안전하게 검증한다. |
| Partial Result | 일부 authorized KB retrieval이 operational failure를 겪었지만 남은 evidence가 모두 permission/source ACL/final policy gate를 통과한 경우 반환할 수 있는 제한적 결과. 실패 후보는 safe/bucketed summary로만 표시한다. |
| Source ACL | 외부 source system의 문서/source item 접근 제어 정보. source-managed KB에서는 mbased KB `use`와 별개의 필수 gate이며, stale/unmapped/ambiguous/unverified/revoked 상태는 fail-closed다. |
| Source ACL Fact | Source system에서 가져온 ACL 원천 사실. Raw principal, raw path, raw title, raw permission 값은 user-facing UI, router, audit/trace summary에 직접 노출하지 않는다. |
| Source ACL Freshness | Source ACL이 최신이고 requester authorization에 사용할 수 있는지 나타내는 상태. 예: `fresh`, `stale`, `unmapped`, `ambiguous`, `unverified`, `revoked`. Fresh가 아니면 source-managed KB retrieval은 fail-closed다. |
| Source ACL Provenance | Source Authorization Provenance의 alias. 새 문서와 table/field 이름은 Source Authorization Provenance를 우선 사용한다. |
| Requester Authorization | 현재 요청 actor가 source-managed KB의 원천 source에서도 접근 가능한지 확인하는 source ACL 기반 gate. 판정 결과는 `allowed`, `denied`, `unknown`, `not_applicable` 같은 값으로 표현하며, source ACL freshness/mapping 상태와 분리한다. Organization manager나 manual KB grant가 이 gate를 우회하지 않는다. KB-scoped classification/resolver read와 mutation은 protected row lookup/projection 전에 fresh gate를 적용하고 mutation은 bounded revision/watermark를 current KB authority와 함께 commit 직전에 다시 검증한다. Manual KB에는 `not_applicable`이다. |
| Source-Managed KB | 외부 connector/source sync가 생성·관리하는 document-level KB. 수동 grant만으로 source ACL freshness/requester authorization을 우회하지 않는다. |
| Source Subject Mapping State | Nodease execution subject와 source system subject가 안전하게 연결됐는지 나타내는 상태. `unmapped`, `ambiguous`, `stale`, `revoked`는 private source-managed retrieval에서 fail-closed로 처리한다. |
| Runtime Source Authorization Primitive | Retrieval 실행 시 source item 접근 가능 여부를 원천 source 기준으로 다시 확인하는 connector operation. `check_access_batch(subject_ref, source_item_refs[])`를 우선하며, 미지원 source는 bounded single `check_access(subject_ref, source_item_ref)` fallback만 허용한다. |
| Runtime Access Cache | Runtime source authorization 결과를 짧은 TTL로 보관하는 cache. Organization, connector, protected source identity, source item/document version, execution subject, mapping epoch, source ACL freshness epoch, operation을 key에 포함해야 하며 subject-level allow를 다른 item에 재사용하지 않는다. |
| Source Identity | Source item을 재동기화, tombstone matching, provenance 연결에 사용할 수 있게 식별하는 protected reference. Raw source id/url/path/title과 구분한다. |
| Protected Source Identity | 사용자-facing resource가 아닌 source identity 저장 경계. HMAC/hash ref, key version, rotation/backfill, tombstone matching 정책을 갖고 raw source id/url/path/title 노출을 막는다. |
| Safe Source Reference | raw source id/url/path/title 대신 citation, audit, UI에 제한적으로 사용할 수 있는 opaque/HMAC 기반 source reference. ADR-0017 provisional baseline은 protected source identity와 keyed HMAC reference를 사용하며, 구체 format과 key rotation/backfill column은 해당 migration/API 문서에서 고정한다. |
| Resource-Hidden Response | scope 밖, hidden, requester source authorization denied, source ACL stale 등 존재 추론 위험이 있는 경우의 안전한 응답 shape. Knowledge 목표 구조의 provisional matrix는 ADR-0017과 Knowledge implementation baseline을 따르며, 최종 HTTP/SSE shape와 audit 여부는 구현 PR의 API/test 계약에서 고정한다. |
| Redacted Canonical Text | source item에서 추출한 뒤 redaction/sanitization을 거친 canonical text. 목표 모델에서 chunk content, embedding input, retrieval-visible text의 기본 원천이다. |
| Content Safety Gate | 외부 source artifact를 redacted canonical text로 만들기 전에 file type allowlist, active content 차단, archive cap, parser isolation, malware/content scan hook을 평가하는 ingestion gate. 실패 또는 unknown은 indexing-visible artifact를 만들지 않는다. |
| Indexed Cache | Source item의 redacted canonical chunk, embedding, safe metadata를 Nodease에 저장하고 runtime source authorization을 final evidence gate 전에 다시 확인하는 Knowledge source mode. |
| Live-linked Knowledge Mode | Nodease 내부에 chunk/embedding을 저장하지 않고 source ref와 safe metadata만 저장하는 Knowledge source mode. Requester-scoped source-side search 또는 opaque-ref-only search가 있는 source에서만 일반 retrieval 후보가 될 수 있다. |
| Archived Copy | Compliance 목적의 protected raw source artifact를 opt-in 보존하는 Knowledge source mode. Raw artifact는 RAG, embedding, prompt, Agent answer stream, durable citation summary의 입력이 아니다. |
| Public Exposure Approval | Source-managed KB를 anonymous public-only 후보로 포함하기 위한 source/connector 별도 승인. Collection public visibility와 별개이며 approver, approval scope, target consistency, expiry, revocation behavior를 검증해야 한다. |
| Ephemeral Content Handle | Redaction 전 content를 process/run 범위에서만 참조하기 위한 short TTL handle. Durable DB, retry/dead-letter payload, audit, trace, log, user-facing response에 handle value나 raw content를 저장하지 않는다. |
| Canonical Metadata Source | Target cutover 후 metadata filter, citation summary, audit/trace summary에 사용할 authoritative metadata 위치. ADR-0017 provisional baseline은 document version 또는 canonical metadata table 쪽을 기준으로 두며, `documents.meta_info` 이후의 최종 table/column은 구현 PR의 migration과 API/schema 문서에서 고정한다. |
| Privacy Redaction Policy | PII/secret detector, masking/hash/drop/block rule, output-target별 redaction을 정의하는 공통 정책. Platform hard baseline은 관리자가 약화할 수 없고, organization/collection/source/KB 정책은 더 엄격한 방향으로만 조정한다. |
| Collection Privacy Policy Binding Revision | Collection의 published privacy policy를 exact same-Organization KB에 적용하는 explicit immutable protected binding. 일반 routing membership/lifecycle과 분리되며 V1 Organization manager의 impact acknowledgement와 expected-revision CAS가 필요하다. Collection archive/restore는 binding을 바꾸지 않고 archived 상태에서도 explicit unbind 전까지 적용된다. Active binding이 있는 membership unlink, Collection hard delete 또는 referenced-policy purge는 binding을 먼저 제거하기 전 허용하지 않는다. |
| Effective Privacy Policy Snapshot | Platform hard baseline, current Organization base와 모든 explicitly bound active Collection/source/KB stricter revision을 precedence가 아닌 strongest union으로 합성한 immutable materialization input. Exact scope와 Collection privacy/source/KB binding revisions, compiled digest와 derived mode/action/provider를 고정하며 scope/result 변경은 V1 Organization validity invalidation을 유발한다. |
| Hard-Baseline Detector | 모든 Knowledge canonicalization에서 organization policy보다 먼저 로컬로 실행하는 platform-owned PII/secret 최소 탐지 규칙. Organization은 rule/span/action을 제거하거나 약화할 수 없고 provider 장애 fallback으로 생략할 수 없다. |
| Organization Detector Provider | Organization Privacy Policy가 exact immutable revision으로 참조하는 DLP/NER detector 보호 리소스. Provider endpoint/config/credential은 server-owned lifecycle과 capability에서만 해석하며 request, graph 또는 document metadata가 선택하지 않는다. |
| Detector Egress Approval Revision | Exact Organization/provider/trust tier의 purpose, processor/ownership·endpoint/network boundary, data residency, retention, no-training/no-secondary-use와 active/expiry/revoke 상태를 고정하는 immutable protected approval. `external_approved`는 ADR-0067 public-address guard를 유지하고 `organization_private`는 별도 exact address allowlist와 dedicated network isolation profile을 추가로 요구한다. Private IP, DNS, mTLS 또는 credential만으로 승인을 대체하지 않는다. |
| Raw Parser Egress Approval Revision | Hard baseline 전에 raw bytes를 외부 parser로 보내기 위한 exact Organization/applicable source 또는 KB/parser immutable approval. Source-managed document는 source scope, manual document는 KB scope를 사용한다. Detector approval과 분리하며 purpose, processor/ownership, endpoint/network, residency/retention/no-training/no-secondary-use, credential capability와 `knowledge.parser.external_approved` public-address profile lifecycle을 고정한다. Private raw parser는 별도 Accepted ADR 전까지 지원하지 않고 current LlamaParse credential `use`만으로는 충족되지 않는다. |
| Provider-Safe View | `external_approved` detector에 보내기 전에 hard-baseline span을 로컬에서 제거하거나 안전한 placeholder로 치환한 attempt-scoped 최소화 text view. Sensitive-free 또는 compliance-pass 보증이 아니며 잔여 민감정보 가능성을 전제로 explicit egress approval이 필요하다. View-to-document offset map과 exact text/fingerprint는 durable storage, audit, trace와 log에 남기지 않는다. |
| Privacy Review Candidate Revision | Manual review가 필요한 pending generation의 encrypted staged redacted candidate를 나타내는 immutable revision. Reviewer의 추가 masking은 candidate-relative range로 정보가 줄어드는 새 revision을 만들며 replacement/raw text나 submitted range를 durable 저장하지 않는다. 최초 generation DB time부터 V1 code-owned 7-day TTL을 공유하고 successor가 연장하지 않는다. Exact current non-expired revision approval만 finalization에 사용하며, approve 뒤 body는 review에서 닫되 canonical finalization commit 또는 TTL까지 finalization-only input으로 유지한다. Expiry, reject, successor, generation authority revoke와 finalization 뒤에는 즉시 비노출·purge 대상으로 만들고 blanket/stale approval이나 terminal block override는 허용하지 않는다. |
| Privacy Review Decision | Exact generation/candidate revision에 대한 `mask|approve|reject`와 actor, current policy/binding/validity/source precondition, safe outcome 및 optional successor candidate ref를 결속하는 append-only record. Candidate body/range/raw/span/digest를 저장하지 않으며 candidate를 덮어쓰지 않는다. |
| Privacy Decision Manifest | Staged redacted candidate를 만든 Effective Privacy Policy Snapshot/digest, baseline/provider/detector-approval/raw-parser/masking/normalization revision, exact platform/Organization validity revision/epoch snapshot과 segment coverage/outcome을 raw content나 exact span 없이 고정하는 Target provenance. Approved exact candidate의 manifest만 Redacted Canonical Text finalization에 사용할 수 있다. Durable source/span identity는 organization-scoped keyed digest를 사용하고 audit/trace에는 digest 대신 opaque manifest ref만 제공한다. |
| Privacy Artifact Validity Revision | Privacy Decision Manifest가 현재 platform/Organization 최소 privacy contract를 만족하는지 판정하는 server-owned immutable `platform|organization` scoped revision. 각 scope는 monotonic `validity_epoch`을 갖고 manifest는 global platform과 same-Organization ref/epoch의 두 요소를 snapshot한다. Preserving은 epoch을 유지하고 security-invalidating 또는 모호한 변경은 해당 epoch을 `+1` CAS해 commit 시점부터 old epoch artifact를 prefilter/final evidence gate에서 제외한다. V1 Organization invalidation은 해당 Organization 전체 privacy-gated artifact에 적용한다. |
| Privacy Artifact Validity Vector | Privacy Decision Manifest가 finalization 때 고정하는 exact global platform validity revision/epoch과 same-Organization validity revision/epoch의 두 요소. Retrieval prefilter와 final evidence gate는 두 snapshot epoch가 current 두 epoch와 모두 같을 때만 artifact를 허용하며 client는 scope/ref/epoch를 제공하거나 완화하지 못한다. |
| Privacy Digest Key Ring | `privacy_digest_hmac_sha256_v1` durable provenance를 위해 provider/LLM credential과 분리해 관리하는 versioned secret key 경계. Organization별 domain-separated key를 파생하며 key material은 DB, provider wire와 observability sink에 저장하지 않는다. |
| Legacy-Unverified Knowledge Artifact | Privacy enforcement 전에 change-controlled Organization rollout allowlist의 server-owned immutable migration wave에 최대 한 번 고정된 pre-cutoff raw-derived artifact의 bounded rollout 분류. Opaque `legacy_artifact_ref`는 nullable version의 versioned 또는 unversioned chunk와 vector/keyword/hierarchy generation exact set을 결속하고 wave는 freeze 당시 exact platform/Organization validity ref+epoch을 고정한다. Privacy mode, compliant artifact 또는 일반 lifecycle enum이 아니며 request/runtime enrollment와 later-wave 이동을 허용하지 않는다. V1 `privacy_legacy_grace_v1`은 immutable enforcement activation부터 최대 30 x 24시간이고 deployment는 줄일 수만 있다. Runtime/preflight는 frozen/current epoch equality를 요구하고 invalidating epoch commit 즉시 legacy를 제외한다. Privacy-compliant active pointer finalization은 같은 transaction에서 eligibility를 비가역적으로 retire하며 이후 manifest가 stale/invalid가 되어도 legacy로 돌아가지 않는다. DB-time cutoff 또는 retirement 뒤에는 prefilter/final evidence gate에서 제외하고 cleanup receipt 뒤 되살리지 않는다. |
| Display Policy | Source-derived name, title, path, URL, description 같은 metadata를 UI에 표시해도 되는지 결정하는 redaction, length cap, allowlist, role/audience 정책. 표시 가능성과 durable audit/trace 저장 가능성은 별개다. |
| Raw Knowledge Artifact | Nodease가 보존하는 upload/fetch 원문 bytes를 위한 protected 저장 단위. Physical field/object 이름과 무관하며 RAG/embedding/prompt에는 사용하지 않는다. Organization/source opt-in, 암호화, retention/legal hold/purge, raw/compliance permission, fresh source ACL, access audit이 필요하다. Source system이 자체 보유하는 source-of-record object의 opaque protected reference는 raw body copy와 구분한다. |
| Raw Copy Cutover Disposition | Target enforcement 전 Nodease-held raw copy exact inventory item의 terminal `protected_migrated|purged` 결과. Valid opt-in과 retention/legal-hold 보존 조건을 모두 충족한 item만 protected migration하고, 그 조건을 충족하지 않으며 hold가 삭제를 막지 않는 item만 purge한다. Protected destination 또는 purge와 original-copy physical absence를 검증해야 하며 response 차단이나 DB path null 처리는 disposition이 아니다. No-opt-in legal-hold conflict, unknown/partial 또는 readable duplicate가 남으면 cutover를 활성화하지 않는다. 모든 item이 terminal인 final readiness와 canonical completion audit은 원자 확정하고 per-item receipt는 AuditLog로 복제하지 않는다. |
| Raw/Compliance Access | Raw Knowledge Artifact를 조회하는 별도 권한/flow. Agent answer, SSE stream, retrieval context와 분리되며 raw access audit이 선행돼야 한다. |
| Capped Preview | 사용자에게 출처 이해를 돕기 위해 제공하는 redacted and length-limited 미리보기 텍스트. Durable audit/trace/usage summary나 embedding input으로 복사하지 않는다. |
| Metadata Filter | 문서 metadata의 allowlist된 필드로 검색 범위를 좁히는 필터. 권한의 source가 아니며 RBAC/source ACL 판정을 대체하지 않는다. |
| Classification | ADR-0007의 문서 보안 민감도 metadata convention(`public`, `internal`, `confidential`, `pii`). 전용 column이 아니라 `documents.meta_info.classification`을 사용하며 문서 유형, 업무 주제, taxonomy 또는 chunking profile과 같은 의미가 아니다. Current demo/legacy의 다른 문자열은 canonical 보안 등급 확장이 아니라 정리 대상 compatibility data다. |
| Security Classification | `Classification`의 명시적 의미를 나타내는 목표 용어. 문서 보안 민감도만 표현하며 document type, taxonomy topic, processing profile, permission, source ACL과 분리된다. 현재 compatibility source는 `documents.meta_info.classification`이다. |
| Document Type | Parser와 processing profile이 이해하는 문서 구조 capability의 stable opaque ID. Platform-managed versioned registry가 소유하며 조직의 업무 주제나 자유 문자열 label이 아니다. |
| Knowledge Taxonomy | Organization이 관리하는 versioned 업무 주제 분류 체계. V1 목표는 server-issued stable opaque topic ID를 사용하는 immutable-version single-parent rooted forest이며 topic 0개인 empty forest도 유효하다. Complete version은 topic 최대 1,000개와 candidate replacement edge 최대 2,000개다. Taxonomy version은 server-owned label normalization contract를 snapshot하고 direct sibling canonical label을 unique하게 유지한다. Publish는 same-snapshot versioned impact preview와 explicit acknowledgement를 요구한다. Collection, chunk hierarchy와 permission hierarchy가 아니다. |
| Taxonomy Topic | Knowledge Taxonomy 안의 Organization-lifetime server-issued stable 업무 주제 ID. Mutable draft의 client UUID `draft_topic_key`와 mapping은 current complete-tree topic에 한정된 응답 유실 복구 정보일 뿐 assignment/runtime identity가 아니다. Allocation 뒤 편집은 server topic ID를 사용하고 current-mapped key 재제출은 conflict다. Topic 제거는 mapping도 제거하고 같은 key의 재도입에는 새 stable ID를 발급한다. 문서는 여러 topic을 가질 수 있고 optional primary topic 하나를 둘 수 있다. Label/path는 표시값이며 identity가 아니다. Deprecated topic은 같은 Organization의 active topic을 향한 merge/split replacement hint를 가질 수 있지만 assignment를 자동 변경하지 않고, lifetime replacement graph는 acyclic이며 최대 path depth가 8이다. Published ID가 deprecated/removed tombstone이 되면 active topic이나 다른 의미로 재사용하지 않는다. |
| Taxonomy Topic Tombstone | Organization-lifetime topic identity ledger에 남는 terminal stable ID marker. Deprecated 또는 published successor에서 제거된 topic ID의 재활성화·의미 재사용을 막으며 assignment/audit provenance를 삭제하지 않는다. 원천 source item 삭제를 나타내는 `Tombstone`과 다른 개념이다. |
| Assignment Impact Snapshot Revision | Taxonomy impact preview가 계산한 Organization 내 assignment/content/lifecycle, explicit profile override, current Processing Decision과 active artifact pointer/availability input 집합의 server-owned revision. 영향 집합을 바꾸는 authoritative mutation과 같은 Unit of Work에서 전진하며 preview acknowledgement의 stale 판정에 사용한다. |
| Taxonomy Impact Bucket Contract | `taxonomy_impact_bucket_v1`은 same-snapshot 내부 count를 `none=0`, `small=1..10`, `medium=11..100`, `large=101+`로 변환한다. Affected는 candidate와 current taxonomy의 topic-axis freshness 및 resolver-eligible topic/primary tuple이 달라지는 processing-eligible current assignment다. Exact version ID 차이 또는 이미 stale인 동일 tuple만으로 세지 않는다. Reindex candidate는 affected subset 중 active-ready artifact가 있고 candidate fingerprint가 비교 가능한 current decision과 다르거나 legacy artifact에 current decision/fingerprint가 없어 동일 materialization을 증명할 수 없는 row다. Profile revision/source/status만 달라 fingerprint가 같음이 증명되면 제외한다. Contract version은 preview token/publish precondition에 bind되고 exact count와 hidden identity는 외부에 노출하지 않는다. |
| Classification Assignment | Document-level KB에 적용되는 effective document type, 최대 32개 topic set, primary topic과 document-type/topic axis별 source·lock·content freshness 및 revision의 원자적 상태. Freshness는 `current`, `review_recommended`, `current_validation_required`, `review_required`를 구분한다. Current pointer가 없을 때 required nullable expected revision으로 최초 revision 1을 CAS하며 Current taxonomy가 없을 때 type-only assignment의 taxonomy ref는 null일 수 있다. New canonical content에서 locked axis만 review-recommended resolver input으로 승계하고 unlocked stale axis는 current-validation 전 filter/profile/materialization에서 제외한다. Suggestion과 구분되고 최초 assigned canonical content revision/snapshot과 manual-confirmation current-validation으로 전진한 last-validated refs를 분리한다. Current-validation reason은 server-computed content/taxonomy/registry changed-dimension set과 정확히 일치해야 한다. Source-managed mutation은 assignment lookup 전과 commit 직전에 source/display authorization을 검증하며, set에 content가 포함된 validation만 추가 `content_read`와 raw policy를 요구한다. Accepted suggestion source는 operational suggestion purge와 독립된 selected-axis 및 tagged generator safe provenance snapshot을 가진다. |
| Manual Axes | Classification Assignment의 complete manual PUT에서 value/manual authority를 변경할 non-empty `document_type|topics` axis set. Selected unlocked axis만 replace/takeover하고 unselected axis는 request value가 current와 일치해야 하며 server가 value/source/lock을 보존한다. Partial topic merge나 locked 반대 axis overwrite를 허용하는 필드가 아니다. |
| Classification Suggestion | Deterministic rule 또는 AI classifier가 exact canonical content revision/hash, nullable taxonomy pointer와 document-type registry version을 대상으로 만든 axis별 비권위 후보. Provenance는 공통 immutable `generator_contract_ref`와 `generator_kind=deterministic_rule|ai_classifier` tagged union이다. Deterministic kind는 approved rule-set/version만, AI kind는 classifier policy/model/prompt-template/calibration 및 bounded confidence만 가진다. Taxonomy가 없으면 type-only candidate만 허용하며 null-to-version 전환에서 아직 suggested인 candidate만 expire된다. Terminal review outcome은 다시 쓰지 않는다. Profile-only output artifact version 변경으로 expire되지 않으며 사람이 selected axis를 accept하기 전에는 assignment, security, permission, profile activation을 변경하지 않는다. |
| Suggestion Accept Preview | Immutable Classification Suggestion의 selected axes를 current assignment와 resolver/materialization vector에 hypothetically 적용해 axis/profile/reindex effect만 보여 주는 server-owned validation. Organization/KB/actor/suggestion/state와 current Processing Decision 및 active Artifact Build identity/fingerprint/availability를 포함한 전체 snapshot을 최대 10분 opaque `accept_preview_revision`의 server-side state에 bind하며 capability가 아니다. Accept는 exact revision을 요구하고 boolean 결과가 같아도 bound identity가 바뀐 stale preview를 commit하지 않는다. |
| Processing Profile | Stable profile identity 아래 tokenizer/version, chunk unit/size/overlap, boundary, parser, retrieval representation과 embedding reference를 고정하는 `platform` 또는 `organization` scope의 immutable published processing configuration revision. Chunking은 구성 요소 중 하나이며 profile 전체 이름이 아니다. Organization successor draft는 request가 지정한 same-identity immutable `published` `base_revision_id`의 normalized config를 복제하고 exact base lineage를 기록하며 mutable latest를 암묵적으로 선택하지 않는다. Organization manager는 draft/publish/deprecate catalog lifecycle을 관리하고 platform profile은 read-only다. 기존 character 기반 `chunk_size/chunk_overlap`을 token 값으로 재해석하지 않는다. |
| Processing Profile Schema V1 | `processing_profile_schema_v1`의 transport/processing safety envelope. `chunk_size_tokens` strict integer `64..8192`, `chunk_overlap_tokens` strict integer `0..floor(size/2)` inclusive이며 current catalog capability는 더 작게 제한할 수만 있다. 품질 최적 기본값이 아니고 변경은 새 schema version이다. |
| Organization Profile Catalog Revision | 한 Organization이 소유한 profile revision의 publish/deprecate selectability를 나타내는 monotonic revision. Organization policy/override/preview/finalizer가 exact snapshot으로 검증한다. |
| Platform Profile Catalog Revision | Approved platform profile revision의 publish/deprecate/default selectability를 나타내는 Organization catalog와 독립된 monotonic revision. Organization API에서는 read-only이고 profile-policy publish, override와 Platform default pointer mutation이 exact revision을 CAS한다. |
| Platform Default Processing Profile | Current Organization profile-policy가 없고 valid explicit override도 없을 때 resolver가 `platform_default` source로 선택하는 exact approved published/selectable Platform profile revision. Non-null `platform_default_profile_revision` pointer는 profile control plane 공개 전에 초기화한다. 변경은 platform registry owner/system actor의 exact Platform catalog CAS이며 pointer, catalog revision과 canonical audit를 원자적으로 확정한다. Organization에는 read-only이며 mutable latest, environment default 또는 legacy chunk setting이 아니다. |
| Processing Profile Mapping Policy | Resolver-eligible document type과 optional primary topic을 exact immutable Processing Profile revision에 매핑하는 Organization policy. Published policy의 Organization general rule은 optional이고 policy Platform general fallback은 required다. Current policy 안의 missing/stale organization mapping은 policy Platform general로 fallback할 수 있지만 invalid explicit override는 fallback하지 않고 review-required로 차단한다. Current policy가 없을 때는 이 policy fallback이 아니라 Platform Default Processing Profile을 사용한다. |
| Processing Profile Policy Schema V1 | `processing_profile_policy_v1` complete authoring document. Raw rules 최대 2,000개와 type+primary/type/primary strict matcher union, exact scoped profile revision reference, required nullable Organization/Platform general field를 사용한다. Incomplete draft는 Platform general null을 저장할 수 있지만 publish는 non-null Platform fallback을 요구한다. |
| Processing Profile Override | Document-level KB가 mapping policy보다 우선해 `{catalog_scope: organization|platform, profile_revision_id}`로 exact Processing Profile revision을 명시적으로 선택하는 KB-scoped revisioned state. Scope는 Organization과 Platform catalog의 같은 opaque ID를 구분한다. KB `manage`로만 set/clear하며 source-managed KB는 read/mutation 전 fresh source/display gate와 mutation commit 직전 revision/watermark 재검증을 추가한다. Mutation 자체는 reindex 또는 active pointer 변경을 자동 시작하지 않는다. |
| Canonical Content Revision | Redacted canonical content의 처리 입력 identity. KB/document scope, canonicalization contract와 canonical bytes/structure/materialization-safe metadata의 input hash를 고정한다. Source sync/document generation은 lineage ref로 분리하고 materialization input이 같으면 새 revision을 만들지 않는다. |
| Resolver Input Fingerprint | Assignment, current taxonomy/registry, nullable mapping policy, explicit override, Platform default ref, Organization/Platform profile catalog/selectability와 resolved exact scoped profile ref vector를 고정한 pre-build fingerprint. Stale preview/admission/finalization 차단과 정책 판단 재현에 사용한다. |
| Materialization Input Fingerprint | Canonical materialization input hash, 실제 output을 결정하는 immutable parser/chunk/representation/embedding configuration과 materialized assignment-derived field의 pre-build fingerprint. Output artifact ID/result bytes와 비물질적 display/pointer 값은 제외하며 `reindex_required` 판단에 사용한다. |
| Artifact Integrity Hash | Build 뒤 normalized chunk/representation/index manifest와 embedding completeness를 검증하는 output hash. Job identity, dedupe 또는 reindex admission 입력이 아니다. |
| Processing Decision Manifest | Canonical Content Revision, exact resolver input refs, resolved scoped profile ref, Resolver Input Fingerprint와 Materialization Input Fingerprint를 고정한 immutable 정책 판단 기록. Current decision pointer는 active artifact pointer와 분리된다. |
| Artifact Build Manifest | Canonical materialization input, immutable build configuration, Materialization Input Fingerprint, output artifact refs와 Artifact Integrity Hash를 고정한 immutable build provenance. Manifest reference 자체는 physical artifact retention pin이 아니다. |
| Decision Satisfaction | Processing Decision Manifest가 기존 또는 신규 Artifact Build Manifest로 충족됐음을 나타내는 append-only link. Existing artifact reuse는 current decision pointer만, new build는 decision/artifact pointer를 함께 전환한다. Purged artifact link는 provenance로 남지만 재사용할 수 없다. |
| Artifact Retention Pin | Current active/current decision, in-flight build, bounded citation/evidence retention 또는 legal hold 때문에 physical artifact purge를 막는 명시적 state. Manifest/Satisfaction provenance reference와 구분한다. |
| Artifact Availability Tombstone | Physical artifact 삭제 확인 뒤 manifest와 integrity provenance를 유지하면서 output availability를 `purged`로 표시하는 append-only marker. Tombstone, `purged` 전이와 canonical `knowledge.processing_artifact.purged` audit는 같은 completion transaction에서 확정한다. Completion 실패는 이미 삭제된 artifact를 `ready`로 되돌리지 않고 same-generation reconciler가 non-retrievable `purging`에서 완성한다. Purged artifact는 retrieval, citation과 existing-artifact satisfaction에 사용할 수 없다. |
| Reindex Idempotency Key | Reindex admission의 required `Idempotency-Key` HTTP header에 전달하는 lower-case hyphenated canonical UUID 36자. Header name만 case-insensitive이며 nil, textual alias, duplicate와 body field는 허용하지 않는다. Server는 canonical ASCII bytes의 SHA-256 digest만 scoped receipt identity로 저장하고 raw value를 response/log/audit/trace에 남기지 않는다. |
| Processing Admission Receipt | Current KB authority와 applicable fresh source authorization을 통과한 reindex idempotency request를 `unchanged/satisfied_existing/job_created/job_reused` safe result에 연결하는 durable record. Receipt lookup 전 source gate와 return/commit 전 revision 재검증을 요구한다. Organization/KB/actor/operation scope, protected request digest와 nullable safe source revision을 저장하며 exact authorized replay는 preview token expiry 뒤에도 기존 결과로 수렴하지만 audit를 중복 생성하지 않는다. 최초 result receipt/mutation과 `knowledge.processing_reindex.admitted` success audit은 원자적이다. Stored actor/source revision은 execution authorization provenance이며 capability가 아니다. `job_created` actor는 physical job의 immutable execution actor가 되고 다른 actor의 `job_reused` receipt는 이를 교체하지 않는다. |
| Retrieval Representation | Redacted canonical evidence에서 파생된 검색용 표현. Contextual prefix, parent summary, late/visual embedding input 등이 해당하며 canonical content를 덮어쓰지 않고 별도 identity/version/fingerprint를 가진다. |
| Query Intent | 현재 요청이 fact, section, multi-evidence, global synthesis 등 어떤 검색 동작을 요구하는지 나타내는 request-scoped 분류. Document Type과 별개이며 저장된 문서 유형 하나가 모든 질의 전략을 고정하지 않는다. |
| Ingestion Lock | 같은 source item 또는 document-level KB에 대한 동시 ingestion/finalization을 막는 lock. Owner token, TTL renew, fencing token 또는 DB advisory lock 같은 방어가 필요하다. |
| Fencing Token | 오래된 worker가 lock 만료 뒤 새 worker의 artifact를 finalize하거나 삭제하지 못하게 하는 단조 증가 또는 소유권 확인 token. |
| Knowledge Document Ingestion Job | Document process/sync/resume/reindex 요청의 durable 실행 record. MBA-288 브랜치에 구현됐지만 최신 dev에는 아직 병합되지 않았으며, queue task ID나 Redis progress가 source of truth가 아니다. Physical cleanup을 소유하는 Knowledge Ingestion Outbox와 구분한다. |
| Knowledge Ingestion Outbox | Active version 전환 뒤 superseded chunk와 향후 object/vector/orphan artifact의 physical cleanup side effect를 조정하는 retry 가능한 outbox. 최신 dev의 handler는 `cleanup_superseded`에 한정되며 process/sync 요청 job이 아니다. DB commit 전 physical delete나 external side effect가 먼저 확정되는 것을 피한다. |
| Recovery Scanner | outbox, staging version, orphan artifact, failed cleanup 같은 불완전 상태를 찾아 idempotent하게 재시도하거나 dead-letter 처리하는 운영 worker. |
| Tombstone | Source item이 원천 source에서 삭제되었거나 더 이상 열거되지 않는 상태를 나타내는 marker. 기본적으로 citation/audit history를 즉시 삭제한다는 뜻은 아니다. |
| Sync Run | Connector가 source item/content/ACL을 동기화하는 실행 1회. Lease, cursor, retry/backoff, dead-letter 상태를 가져야 한다. |
| Dead Letter | 재시도 한도를 넘었거나 자동 복구가 위험한 sync/outbox 작업을 운영자 remediation 대상으로 격리한 상태. |
| RAG Answer Run | standalone RAG Agent answer 실행 기록. DB에서는 `rag_answer_runs` table을 사용한다. trace/usage table과는 FK가 아니라 `correlation_id`로 느슨하게 연결한다. |

## LLM And Cost

| 용어 | 정의 |
| --- | --- |
| LLM Provider | OpenAI, Anthropic, Google 같은 외부 LLM 제공자. 호출은 `apps/shared/services/llm_client`의 자체 client 계층을 통해 수행한다. |
| LLM Model | Provider가 제공하는 구체 모델. DB에서는 `llm_models` table을 사용한다. |
| LLM Credential | Provider API 호출에 필요한 자격 정보. 사용자가 소유하고(`user_id`) organization scope에 속할 수 있다(`organization_id` nullable). DB에서는 `llm_credentials` table을 사용한다. secret 원문은 응답, 로그, trace에 노출하지 않는다. |
| Mail Credential | Organization이 관리하는 Mail provider 인증 resource. Workflow graph에는 opaque `credential_id`만 저장하며 secret은 `mail_credentials`에 암호화 저장한다. 조회·사용·관리는 `read/use/manage`로 분리하고 실행 직전에 권한과 상태를 재검증한다. |
| Credential-Model Relation | 특정 credential로 어떤 model을 사용할 수 있는지 나타내는 연결. DB에서는 `llm_rel_credential_models` table을 사용한다. |
| LLM Usage Log | LLM 호출의 token, latency, cost 등 사용량 기록. DB에서는 `llm_usage_logs` table을 사용한다. |
| Cost Optimizer | Workflow의 현재 모델과 후보 모델을 비교 실행해 비용 절감률과 품질 차이를 제시하는 기능이다. |
| Model Compare | 기존 compare API를 사용해 같은 workflow를 다른 model 조건으로 실행하고 결과와 비용을 비교하는 흐름이다. |

## Audit And Trace

| 용어 | 정의 |
| --- | --- |
| Audit | 사용자의 주요 action과 data change를 추적 가능한 기록으로 남기는 것. canonical 저장소는 `audit_logs` table이다. |
| Audit Log | actor, action, 대상 resource, status, metadata를 포함하는 감사 기록. |
| Canonical Action | audit log에 저장하는 표준 action 문자열. 예: `workflow.deploy`, `permission.denied`. |
| Trace | Workflow/RAG 실행 중 생긴 입력, 출력, 중간 결과, payload 접근 이력을 추적하는 실행 관측 데이터다. |
| Trace Payload | 실행 payload를 raw/redacted 정책에 맞춰 저장하는 기록. DB에서는 `trace_payloads` table을 사용한다. |
| Raw Payload | 마스킹 전 원본 payload. 접근은 최소화하고 조회 시 `trace_payload_access_events` 기록이 선행돼야 한다. |
| Redacted Payload | secret, credential, raw content 등 민감 값을 제거하거나 마스킹한 payload. 일반 조회는 redacted 기준을 우선한다. |
| Trace Payload Access Event | raw payload 등 민감 trace 접근을 별도로 기록하는 감사 이벤트. DB에서는 `trace_payload_access_events` table을 사용한다. |
| Policy Result | 정책 평가 결과. `pass`, `warn`, `block` 같은 값을 `audit_logs.audit_metadata.policy_result`에 저장한다. |
| Policy Reason | 정책 차단 원인을 나타내는 `{domain}.{reason}` 형식의 canonical machine code. Security Alert 대상 `policy.block`은 최상위 `audit_metadata.policy_reason`에 기록하며 사용자 표시 문구와 구분한다. |
| Correlation ID | FK가 아닌 application-level 연결 식별자. standalone RAG answer와 trace/usage/audit을 느슨하게 연결하는 데 사용하며 권한 판정 기준으로 쓰지 않는다. |
| Eligible Audit Event | Security Alert rule 평가에 사용할 수 있는 audit. 인증 actor, 검증된 organization, failure action과 action별 safe target/reason 계약을 모두 만족해야 한다. |
| Security Alert | Eligible Audit Event의 반복 패턴이 임계값을 충족했을 때 생성되는 organization-scoped 위험 신호와 관리자 대응 기록. 실제 침해 확정을 의미하지 않으며 자동 사용자 차단을 수행하지 않는다. |
| Detection Rule | 어떤 eligible audit를 어떤 시간 window와 threshold로 묶어 Security Alert를 만들지 정의하는 versioned 규칙. |
| Detection Key | Organization, actor, rule/version, 필요한 경우 policy reason을 조합한 내부 집계 key. 같은 key의 활성 alert 중복 방지에 사용하며 API에 노출하지 않는다. |
| Rule Version | Threshold나 detection key 의미 변경 전후의 alert를 섞지 않기 위한 rule 계약 버전. Security Alert MVP는 `v1`을 사용한다. |
| Alert Evidence | Security Alert 판단 근거가 된 `audit_logs` row와 alert의 연결. 원본 metadata를 복사하지 않고 safe audit projection으로 조회한다. |
| Cooldown | 같은 detection key의 반복 event가 alert를 계속 새로 만들지 않도록 기존 활성 alert에 occurrence와 evidence를 모으는 기간. Security Alert MVP는 마지막 탐지 기준 30분 sliding cooldown을 사용한다. |
| Reconciliation | 실시간 탐지 task가 놓친 audit를 processor별 처리 receipt로 찾는 복구 작업. 최대 100건씩 성공한 batch를 commit하고 receipt의 발견·평가 generation으로 batch 경계 뒤의 late-arrival 후속 재평가를 이어가며, 기능 활성화 이전 audit은 backfill하지 않는다. |

## Operations And Integrations

| 용어 | 정의 |
| --- | --- |
| Admin Dashboard | Organization 관리자와 권한을 가진 감사자가 audit log, LLM usage/cost, 사용자 접근 상태를 확인하는 운영 화면이다. Security Alert 탭의 조회·상태 변경은 현재 organization owner/manager에게만 허용하며 audit 전용 권한과 구분한다. |
| Gateway | `apps/gateway/` FastAPI 서비스. 인증된 API 진입점과 resource permission enforcement 경계다. |
| Workflow Engine | `apps/workflow_engine/` Celery worker. Workflow 실행과 node runtime을 담당한다. |
| External Effect | Workflow node가 Nodease 밖의 상태를 바꾸는 작업이다. 같은 logical 실행의 중복 전달이나 재시도에서 이 작업이 다시 수행되지 않도록 [ADR-0035](decisions/ADR-0035-external-effect-idempotency-boundary.md)의 영속 기록, 외부 서비스 중복 방지 지원 수준과 결과 재사용 지원 수준을 적용한다. |
| External Effect Slot | 한 node invocation에서 provider에 보내려 한 외부 작업의 안정된 자리다. Organization scope 안의 `execution_id + node_invocation_id + effect_sequence`로 DB에서 하나만 허용하며, `effect_sequence`는 operation과 무관한 0-based 순번이다. Operation이 재시도 중 바뀌어도 새 자리로 우회하지 못한다. |
| External Effect Attempt | 하나의 External Effect 수행 여부를 판단하기 위해 External Effect Slot에 영속적으로 저장하는 기록이다. Full logical identity에는 slot과 frozen `operation`이 포함되며 현재 node의 App/Workflow provenance, 준비, 외부 서비스 호출 시작, 최종 결과, 당시 provider contract, effect input digest와 안전한 자동 재호출 마지막 시각을 보존한다. Raw 외부 서비스 요청·응답 저장소는 아니다. |
| Provider Contract Profile | 특정 외부 서비스 operation의 중복 방지와 결과 재사용 계약을 version별로 고정한 정적 정보다. Key 전달 위치·header 이름 또는 body JSON Pointer·형식·최대 길이·보존 기간, effect success/rejection과 duplicate 응답 의미, 공식 문서 근거를 포함한다. 확인되지 않은 값은 추측하지 않으며 이미 사용한 version은 의미를 바꾸거나 제거하지 않고 새 version만 추가한다. |
| Provider Replay Capability | 결과가 불명확한 외부 서비스 호출을 최초 key로 안전하게 다시 호출할 수 있는지를 나타내는 지원 수준이다. 공식 계약과 통합 테스트가 있으면 `supported`, 지원하지 않음이 확인되면 `unsupported`, 충분히 검증되지 않았으면 `unknown`이다. `unknown`은 자동 재호출을 막는다. Generic HTTP에 사용자가 idempotency header를 입력한 것만으로 `supported`가 되지 않는다. |
| Replay Deadline | External Effect Attempt를 만들 때 DB 시각과 당시 Provider Contract Profile의 보존 기간으로 확정하는 자동 재호출 마지막 시각이다. 이후 배포의 현재 규칙으로 다시 계산하지 않으며 경계 시각부터 외부 서비스 자동 재호출을 금지한다. |
| Result Reuse Capability | 성공한 node 결과를 민감한 원문 없이 안전하게 저장해 중복 전달에서 다시 반환할 수 있는지를 나타내는 지원 수준이다. 가능하면 `supported`, 불가능하면 `unavailable`이며, `unavailable`인 성공 실행의 중복 전달은 외부 서비스를 다시 호출하지 않고 `external_effect.result_unavailable` 오류로 종료한다. |
| Execution ID | 하나의 logical workflow 실행을 식별하는 server-issued UUID다. Draft test, 배포/API/public/webhook, schedule, stream, subworkflow와 compare 실행에 공통으로 사용하고 Celery 재시도나 중복 전달에서도 유지하며 `workflow_run_id`와 같은 관찰용 식별자와 구분한다. |
| Node Invocation ID | logical workflow 실행 안에서 실제 node 호출 한 번을 식별하는 server-issued UUID다. Loop 반복과 subworkflow 호출 경로마다 다르게 발급하고, 같은 호출의 재시도에서는 유지한다. |
| External Effect Context | 외부 작업을 안전하게 기록하기 위해 Runtime이 내부에서만 전달하는 불변 값이다. 검증된 Organization/App/Workflow와 Execution/Node Invocation 식별자를 담으며 일반 workflow input, template, LLM prompt/tool input이나 node output에 합치지 않는다. |
| WorkflowNode Target Binding | Parent workflow의 retry나 duplicate delivery가 child app의 새 active deployment를 선택하지 않도록, server가 최초 command 또는 immutable deployment snapshot에 고정하는 target deployment ID/version/snapshot hash 정보다. Client 입력이나 public graph 응답이 아니며 Runtime이 canonical DB snapshot과 다시 검증한다. |
| Log System | `apps/log_system/` Celery worker. audit/trace/log 계열 비동기 처리를 담당한다. |
| Sandbox | `apps/sandbox/` NSJail 기반 격리 코드 실행 서비스. |
| Shared | `apps/shared/` 공통 패키지. DB model, schema, permission service, llm_client, tracing/audit utility를 포함한다. |
| Connection | 외부 DB나 외부 데이터 소스 연결 정보. DB에서는 `connections` table을 사용한다. 저장된 secret은 server-side에서만 사용하고 노출하지 않는다. |
| Outbound Egress Guard | Knowledge/RAG source-collection server-side outbound network dial 전 host/IP/port/proxy/timeout/size 정책을 검증하는 중앙 경계. HTTP URL fetch뿐 아니라 DB, SSH, SaaS, object storage connector도 대상이지만, workflow runtime outbound 전체는 별도 ADR 전에는 이 보호가 보장됐다고 해석하지 않는다. |
| Secret | API key, token, credential 원문, `encrypted_config`, `encrypted_password` 등 민감 값. 응답, 로그, trace, 문서, 테스트 fixture에 노출하지 않는다. |
