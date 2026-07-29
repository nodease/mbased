# Nodease - Product Requirements Document

Status: Active

> 명칭: Nodease는 기존 Moduly 코드를 리팩토링해 만드는 신규 서비스명이다. 코드와 배포 리소스에는 아직 `Moduly` 명칭이 남아 있으므로, 이 문서는 제품 관점에서는 Nodease를 사용하고 기존 코드/인프라 식별자는 Moduly 기준으로 읽는다.

## 1. 제품 개요

Nodease는 기존 Moduly 코드를 리팩토링해 만드는 기업 내부 AI 플랫폼이다. 한 회사의 플랫폼 조직이 운영하고, 사내 여러 팀이 AI workflow를 만들고 실행하고 배포하는 데 사용한다. 기존 Moduly의 workflow builder/runtime 위에 조직 단위 권한(RBAC), 감사/추적(audit/tracing), LLM 사용량/비용 관측을 내장해 "만들 수 있는 플랫폼"을 "운영할 수 있는 플랫폼"으로 확장한다.

### 해결하는 문제

기업이 AI를 업무에 본격적으로 도입할 때 부딪히는 세 가지 문제를 해결하고, 그 기반에 기업용 데이터 거버넌스를 둔다.

1. **AI 비용 고민**: LLM 사용이 늘수록 호출 비용이 빠르게 커지지만, 그 비용이 어떤 workflow에서 얼마나 발생하는지 보이지 않는다. 고성능 모델을 관성적으로 쓰면서도 "이 작업에 이 모델이 꼭 필요한가"를 검증할 방법이 없고, 모델·프롬프트·RAG 범위·출력 정책을 조정했을 때 품질이 얼마나 달라지는지 근거가 없어 최적화 결정을 내리지 못한다. 비용을 workflow 단위로 가시화하고 절감/품질 트레이드오프를 근거로 제시해 이 고민을 덜어준다.

2. **workflow 진입장벽**: 노드 기반 workflow 빌더는 강력하지만 트리거, 조건 분기, 변수 연결 같은 개념을 이해해야 해서 비개발자에게 진입장벽이 높다. 자동화가 필요한 현업과 만들 수 있는 빌더가 분리되어 요청과 대기가 반복된다. Agent Builder가 자연어 요청에서 실행 가능한 workflow 초안을 생성해, 만드는 일의 시작점을 프롬프트 한 줄로 낮춘다.

3. **흩어진 사내 지식**: 문서가 위키, 드라이브, 개별 폴더에 흩어져 있어 필요한 정보를 찾기 어렵고, 같은 질문에 사람마다 다른 답을 안다. 사내 데이터를 모두 통합한 지식 저장소를 만들어 RAG로 질문에 답하게 한다. 단, 통합 저장소는 권한 통제 없이 모으면 봐서는 안 될 사람에게 노출되므로, 질문한 사용자의 권한에 맞는 자료만 검색하는 것이 전제다.

**기반 — 데이터 거버넌스**: 위 세 기능은 기업 환경에서 거버넌스 없이 도입할 수 없다. 누가 어떤 데이터와 모델을 언제 썼는지(audit/tracing), 누가 무엇에 접근할 수 있는지(RBAC), 민감 정보가 응답과 기록에 어디까지 남는지(redaction/policy)가 모든 기능 아래에 깔려 있어야 한다. 거버넌스는 개별 기능이 아니라 이 제품의 전제 조건이며, 4개 축 중 Admin 대시보드는 이 기반을 관리자와 감사자에게 보이게 하는 조회 표면이다.

### 핵심 가치

- **비용을 아는 운영**: workflow별 비용이 보이고, 모델 교체 시 절감/품질 트레이드오프를 제시한다.
- **자연어로 시작하는 자동화**: 프롬프트 한 줄로 workflow 초안이 생성된다 (Agent Builder).
- **모든 사내 데이터가 모이는 통합 RAG**: 흩어진 사내 데이터를 통합 지식 저장소로 관리하고, 생성된 workflow의 실행 주체 권한에 맞는 자료만 RAG 옵션이 켜진 LLM node가 검색해 답변에 사용한다.
- **권한과 감사가 내장된 운영 (기반)**: 모든 리소스 접근은 organization/team/user 권한으로 판정되고, 주요 행위는 audit log로 남는다.

## 2. 사용자

기업 내부 사용자를 RBAC 역할 기준으로 구분한다. 권한 상태 정의는 [ADR-0006](decisions/ADR-0006-accept-rbac-auth-state-and-user-direct-permission.md)을 따른다.

| 사용자 | 역할 | 주요 행동 |
| --- | --- | --- |
| 플랫폼 관리자 | organization owner/manager, auditor/raw_auditor 겸임 | 조직/팀/멤버 관리, 권한 부여/신청 승인, 운영 대시보드 확인, audit log 검색과 접근 이력 확인 |
| 빌더 | builder | workflow 생성/편집/배포, Agent Builder 사용, 운영 중 비용 최적화 실행 |
| 현업 사용자 | operator/viewer | 배포된 workflow 실행, RAG 질의 |

## 3. 범위 정의

### 3.1 이번 범위: 4개 데모 축

| 축 | 현재 상태 | 목표 사용자 흐름 |
| --- | --- | --- |
| **Agent Builder** | 자연어 요청으로 workflow 초안을 만들고 GraphMutation/CAS로 저장한 뒤 필수 설정을 보완하는 direct-edit 경로. 현재 mode 값은 `configure_and_generate|structure_only` | 목표 계약은 기본 `단계별 생성`, control 또는 자연어로 요청하는 `빠른 생성`, 고급 `구조만 생성`을 제공한다. 세 모드는 같은 GraphMutation/CAS 저장 계약을 사용하며 빠른 생성도 변경 요약 확인과 명시적 적용을 거친다. 예: "사내 복지, 휴가, 인사 정책 문서를 바탕으로 직원 질문에 답변해줘" → `[입력] → [Knowledge Base-backed LLM] → [응답]` |
| **Admin 대시보드** | `audit_logs`, `llm_usage_logs`, `workflow_runs` 데이터는 이미 쌓임 | 조회 UI: 권한 신청/승인 이력, 누가 언제 뭘 했는지(audit), workflow별 비용(usage), 예산 위험 표시 |
| **비용 최적화** | `POST /api/v1/workflows/{id}/compare` 모델 비교 API 구현됨 | "비용 최적화" UI: LLM 노드의 현재 설정과 후보 설정을 같은 입력으로 비교하고, `modelRouting`, `promptRouting`, task-aware RAG, `responseFormat`, `maxOutputTokens` 조정에 따른 비용·품질 차이를 표시 |
| **통합 RAG** | KB 구축/검색, metadata-aware·hierarchical retrieval 구현됨 | 현재 데모는 준비된 KB 검색/citation과 권한 경계를 유지하고, 목표 구조는 gate 승인 후 자동 수집 가능한 사내 지식 통합 저장소, document-level KB 권한 경계, collection 기반 routing으로 확장 |

### 3.2 기반 기능 (구현됨, 유지 대상)

이번 범위의 전제가 되는 기존 기능이다. 깨뜨리지 않는 것이 요구사항이다.

- Workflow 생성/편집/실행/배포 (schedule/webhook/API 트리거와 공개·인증 내부 챗봇 포함)
- Organization/Team 관리, 초대, RBAC 권한 부여와 차단
- LLM credential 관리와 모델 연결
- 외부 DB 연결(connectors)과 workflow DB 노드 사용 경로
- Audit/tracing 기록 (canonical action 기준: [ADR-0008](decisions/ADR-0008-audit-action-naming-standard.md))

### 3.3 제외 (이번 범위 아님)

- 외부 고객 대상 SaaS 과금/구독
- SSO/외부 IdP 연동
- Marketplace 공개 정책, deployment 권한 고도화 ([ADR-0010](decisions/ADR-0010-resource-access-403-404-policy.md) 제외 항목)
- user direct audit permission (`user_audit_permissions`)
- explicit deny 권한 모델 ([ADR-0006](decisions/ADR-0006-accept-rbac-auth-state-and-user-direct-permission.md)에서 도입하지 않기로 결정)
- 모바일 전용 UI

### 3.4 Conversation Memory 목표 범위

Public Chatbot의 MBA-318 범위는 Client가 완료된 최근 대화 이력을 매 요청에 전달하는 bounded history다. 서버는 익명 Conversation Session·Turn·Entry·Transcript·Access Grant를 영구 저장하지 않으며 Public WorkflowRun/NodeRun/Trace에도 대화 원문을 남기지 않는다. 최대 20 turn과 4,096-token context를 서버가 다시 검증한다.

인증된 조직 내부 Chatbot의 durable Conversation Memory는 후속 제품 목표다. 이 surface는 deployment version, organization과 execution subject에 고정된 Session, current authorization/provenance, retention, transcript와 lifecycle을 제공하며 Public stateless history와 자동 병합하지 않는다.

## 4. 사용자 시나리오

### 시나리오 1: 권한 요청 후 Agent Builder로 workflow 생성·배포 (신입 빌더, 플랫폼 관리자)

1. 기존에 회원 가입한 신입사원이 Nodease에 로그인한다.
2. 신입사원이 `내 워크플로우` 화면에서 새 모듈 생성을 시도한다.
3. 시스템은 해당 사용자가 workflow 생성 권한을 갖고 있지 않음을 확인하고, "새 모듈을 만들 권한이 없습니다. 관리자에게 신청해주세요." 안내를 표시한다.
4. 신입사원은 권한 신청 버튼을 눌러 workflow 생성/배포 권한을 요청한다.
5. 플랫폼 관리자는 관리자 화면의 `권한` 탭에서 신입사원의 요청을 확인한다.
6. 관리자는 요청자, 요청 권한, 신청 사유를 확인한 뒤 권한 요청을 승인한다.
7. 신입사원은 다시 `내 워크플로우` 화면으로 돌아와 새 모듈을 생성한다.
8. 신입사원은 Agent Builder에 자연어로 만들고 싶은 workflow를 요청한다.
9. 기본 `단계별 생성`은 입력 노드, LLM 노드, 응답 노드 구조를 typed GraphMutation으로 editor에 반영하고 CAS 저장 acknowledgement를 완료한다.
10. 신입사원은 통합 설정 카드에서 권한 있는 Knowledge Base와 node parameter를 순서대로 확인하거나 수정한다.
11. 시스템은 각 변경의 권한, stale graph, Catalog validation과 CAS 저장을 확인하고 모든 필수 설정 acknowledgement가 끝난 경우에만 생성 완료로 표시한다.
12. 신입사원은 저장된 workflow를 별도 테스트 실행하고, 응답 노드에서 결과가 반환되는 것을 확인한다.
13. 신입사원은 workflow를 배포하고, 배포된 workflow를 실행한다.
14. workflow 생성, 권한 승인, Agent Builder mutation 저장·acknowledgement, 배포와 실행 행위는 audit log에 기록된다.

시연에서 사용하는 Agent Builder workflow는 사내 지식 통합 질의 workflow다. 이 workflow는 직원의 질문을 입력으로 받아, 사내 복지/휴가/인사 정책 문서가 색인된 Knowledge Base를 검색하고, LLM이 권한이 허용된 문서 근거를 바탕으로 답변을 생성한다. Slack 등 외부 채널 연동은 이번 시연에서 제외하고, Nodease 내부 입력 노드와 응답 노드로 결과를 확인한다.

### 시나리오 2: 감사 로그와 보안·운영 위험 관측 (조직 관리자)

1. 조직 관리자는 admin 계정으로 로그인한다.
2. 관리자는 관리자 화면에서 audit 목록 탭으로 이동한다.
3. 관리자는 권한 신청, 권한 승인, workflow 생성, workflow 배포, workflow 실행 기록을 확인한다.
4. 관리자는 이번 달 조직에서 사용한 LLM 비용을 숫자로 확인한다.
5. 관리자는 예산을 초과했거나 예산 위험 구간에 들어간 workflow 개수를 확인한다.
6. 관리자는 필요하면 특정 audit log를 열어본다. 
7. 관리자는 상세에서 actor, action, target, status, timestamp를 확인한다.
8. 관리자는 user actor를 선택해 current organization의 membership, role, team/direct permission source를 확인한다.
9. 관리자는 필요한 경우 항목별 확인과 선택 사유를 거쳐 member를 정지·재활성화하거나 role/resource access를 회수·재부여하고, 결과 audit의 안전한 변경 전후 상태를 확인한다.
10. 같은 사용자의 권한 거부 또는 보안 allowlist 정책 차단이 임계값을 넘으면 Sidebar의 open 보안 알림 badge를 확인한다.
11. 관리자는 알림 overlay에서 최근 위험 신호를 선택해 Admin Dashboard의 `보안 알림` 탭과 해당 alert 상세로 이동한다.
12. 관리자는 탐지 규칙, 심각도, 발생 횟수, 최초·최근 탐지 시각과 안전한 관련 audit를 확인한다.
13. 관리자는 alert를 확인 상태로 바꾸고, 필요하면 기존 사용자 접근 관리 화면에서 current organization membership 또는 권한을 수동 조치한다.
14. 대응 완료, 오탐 또는 위험 수용 사유를 남겨 alert를 해결하고 해당 lifecycle audit을 확인한다.

이 시나리오는 Nodease가 workflow 생성 도구에 그치지 않고, 기업 내부 AI workflow 운영에 필요한 감사 가능성과 비용/위험 관측 표면을 제공한다는 점을 보여준다.

### 시나리오 3: 비용 위험 workflow 발견과 LLM 노드 단위 최적화 (빌더)

1. 또 다른 author 페르소나가 Nodease에 로그인한다.
2. author는 `내 워크플로우` 화면에서 본인이 운영 중인 workflow 목록을 확인한다.
3. author는 workflow 목록에서 예산 사용률이 높은 workflow를 확인하고, 예산 80%에 근접한 workflow를 선택한다.
4. 선택한 workflow는 예산 80%에 근접했지만 실행 기록은 10회 미만이다.
5. author는 사용 횟수에 비해 비용이 높다고 판단하고 해당 workflow의 tracing 화면으로 이동한다.
6. 실행 상세 화면에서 노드별 trace를 확인한다.
7. LLM 노드의 prompt tokens, completion tokens, total cost, latency를 확인하고, 비용 대부분이 LLM 노드에서 발생하는 것을 확인한다.
8. author는 workflow 전체가 아니라 가장 비용이 큰 LLM 노드 하나만 대상으로 A/B 테스트를 실행한다.
9. A안은 기존 설정을 사용한다. 예: high model, 권한 범위 내 general RAG(broad retrieval), 프롬프트 상 JSON output 요구, maxOutputTokens 2000.
10. B안은 최적화 설정을 사용한다. 예: modelRouting auto, promptRouting ticket_triage_workflow, selectedModel mid, task-aware RAG(권한 범위 안에서 업무 유형에 필요한 근거만 정밀 선택), responseFormat json, maxOutputTokens 800.
11. 시스템은 같은 입력과 같은 이전 노드 결과를 기준으로 A/B 실행 결과를 비교한다.
12. author는 A/B 결과에서 비용 절감률, 토큰 사용량, 모델, 응답 품질, 후속 노드 사용성을 비교한다.
13. B안은 고급 모델을 무조건 쓰는 대신 작업 유형에 맞는 중간 모델을 사용하고, 권한 범위 안에서 업무 유형에 필요한 근거만 정밀하게 선택하는 task-aware RAG로 context token을 줄이며, 최소 JSON 출력으로 output token을 줄인다.
14. author는 품질이 유지되면서 비용이 절감되는 것을 확인하고 최적화된 설정을 채택한다.

RAG 보안 경계: 어떤 RAG 모드에서도 권한 없는 문서는 검색 후보, prompt, citation, trace에 포함되지 않는다 (NFR-007). A안의 문제는 보안 우회가 아니라 권한 있는 문서 중 불필요한 문서까지 넓게 포함되어 context token과 비용이 커지는 것이고, A/B의 차이는 권한 적용 여부가 아니라 권한 검사를 통과한 문서 안에서 근거를 얼마나 정밀하게 선택하느냐다. 시연에서 권한/정책상 제외된 문서를 표시할 때는 문서명과 정확한 건수를 노출하지 않는 안전한 요약으로만 표시한다.

목표 KB 통합 구조에서는 source-managed KB가 mbased KB `use`와 fresh source ACL/requester authorization을 모두 통과한 경우에만 evidence로 사용된다. 자동 수집, collection routing, source ACL materialization, resource hiding API matrix는 [ADR-0017](decisions/ADR-0017-knowledge-integration-provisional-implementation-baseline.md)의 임시 baseline을 기준으로 MBA-105에서 구현한다.

후순위: `내 워크플로우` 상단 알림 패널(예산 초과로 정지된 workflow 개수, 예산 80% 육박 workflow 리스트)은 후순위 구현 항목이다. 구현이 완료되면 이 시나리오에 단계로 다시 추가한다.

이 시나리오는 Nodease의 비용 최적화가 단순히 싼 모델로 바꾸는 기능이 아니라, workflow trace를 기반으로 병목 노드를 찾고, 작업 유형·권한·RAG 범위·출력 정책을 함께 조정하는 운영 흐름임을 보여준다.

### 통합 데모 흐름 (시연용)

실제 시연은 위 시나리오 1~3을 하나의 이야기로 통합해 진행한다. 액터는 신입 빌더(author), 플랫폼 관리자 (admin), 기존 author 세 명이다.

**사전 준비**

- 신입사원 계정은 로그인 가능하지만 workflow 생성/배포 권한이 없다.
- 관리자 계정은 권한 신청 승인과 audit/운영 패널 조회 권한을 갖고 있다.
- 기존 author 계정은 비용 위험 workflow를 보유하고 있다.
- Agent Builder 시연을 위한 사내 복지/휴가/인사 정책 Knowledge Base가 준비되어 있다.
- 비용 최적화 시연을 위한 고객 서비스 티켓 처리 workflow와 비교용 실행 로그가 준비되어 있다.
- 비용 최적화 시연을 위해 LLM trace, workflow run, usage 데이터가 준비되어 있다.

**1막 — 권한 요청과 승인**

1. 신입사원이 `/dashboard`에서 `내 워크플로우`로 이동한다.
2. 신입사원이 `새 모듈` 버튼을 누른다.
3. 시스템은 권한 없음 팝업을 표시한다.
4. 신입사원은 `권한 신청하기`를 누르고 workflow 생성/배포 권한을 신청한다.
5. 관리자가 `/dashboard/admin?tab=permissions` 또는 관리자 화면의 `권한` 탭으로 이동한다.
6. 관리자는 신입사원의 권한 신청 목록을 확인하고 승인한다.
7. 신입사원이 다시 `내 워크플로우` 화면으로 돌아와 새 모듈을 생성한다.

**2막 — Agent Builder로 사내 지식 통합 질의 workflow 생성·실행·배포**

8. 신입사원이 새 workflow 편집 화면에서 Agent Builder를 연다.
9. 신입사원은 흩어진 사내 문서를 검색해 답변하는 RAG workflow 생성을 자연어로 요청한다.
10. 예시 요청은 "사내 복지, 휴가, 인사 정책 문서를 통합 검색해서 직원 질문에 답변하는 워크플로우를 만들어줘"로 한다.
11. Agent Builder의 기본 `단계별 생성`은 입력 노드, LLM 노드, Answer 노드 구조를 typed GraphMutation으로 editor에 반영하고 CAS 저장한다.
12. 신입사원은 통합 설정 카드에서 사내 문서 Knowledge Base binding, 입력/출력 mapping과 validation 상태를 순서대로 확인한다.
13. 각 설정 변경은 같은 GraphMutation/CAS 경계에서 저장되고 server acknowledgement 뒤에만 다음 설정으로 진행한다.
14. 시스템은 권한 재확인, stale check와 validation을 모두 통과하고 필수 설정 acknowledgement가 끝난 경우에만 workflow 생성 완료로 표시한다.
15. 저장된 workflow의 테스트 입력에 사내 문서 질문을 넣는다. 예: "가족돌봄휴가를 연차와 붙여서 사용할 수 있어? 신청은 어디서 해야 해?"
16. workflow를 테스트 실행한다.
17. LLM 노드는 권한이 허용된 Knowledge Base에서 관련 문서를 검색하고, 검색 결과를 바탕으로 답변을 생성한다.
18. Answer 노드에서 최종 응답을 확인한다.
19. 실행 상세에서 RAG retrieval 기록 또는 citation을 확인한다.
20. 신입사원은 workflow를 배포한다.

**3막 — 관리자 감사/운영 관측**

21. 관리자가 admin 화면으로 돌아간다.
22. 관리자는 audit 목록에서 권한 신청, 권한 승인, Agent Builder mutation 저장·acknowledgement, workflow 배포와 실행 기록을 확인한다.
23. 이번 달 조직 LLM 사용 비용을 확인한다.
24. 예산 위험/초과 workflow 개수를 확인한다.

보안 이상 접근 탐지와 관리자 대응 흐름은 시나리오 2에서 별도로 확인한다. 비용 위험 알림은 Security Alert와 섞지 않는다.

**4막 — 비용 위험 workflow 최적화**

24. 기존 author 계정으로 전환한다.
25. author는 `내 워크플로우` 화면에서 workflow 목록과 예산 사용률을 확인한다.
26. author는 예산 80%에 육박한 workflow를 선택한다.
27. 해당 workflow의 실행 기록이 10회 미만임에도 비용이 높다는 것을 확인한다.
28. workflow report의 tracing 화면에서 노드별 비용을 확인한다.
29. LLM 노드가 비용 대부분을 차지하는 것을 확인한다.
30. author는 LLM 노드 단위 A/B 테스트를 실행한다.
31. A안은 기존 high model, 권한 범위 내 general RAG, maxOutputTokens 2000 설정으로 둔다.
32. B안은 ticket_triage_workflow에 맞춰 mid model, 권한 범위 내 task-aware RAG, 최소 JSON, maxOutputTokens 800 설정을 적용한다.
33. A/B 결과에서 비용 절감률과 품질 유지 여부를 확인한다.
34. 최적화 결과를 바탕으로 B안을 채택한다.

후순위: `내 워크플로우` 상단의 예산 초과/위험 workflow 알림 패널은 후순위 구현 항목이다. 구현이 완료되면 이 막에 단계로 다시 추가한다.

### 발표 마무리 메시지

Nodease는 단순히 AI 답변을 생성하는 도구가 아니다. 조직 내 사용자가 어떤 권한으로 workflow를 만들고 배포하는지 통제하고, 실행 이후에는 audit log와 tracing으로 누가 무엇을 했는지 확인하며, 비용이 커지는 workflow는 노드 단위로 분석하고 최적화할 수 있게 한다. 즉 Nodease는 AI workflow를 "만드는 도구"에서 "운영 가능한 기업 내부 AI 플랫폼"으로 확장하는 서비스다.


## 5. 핵심 기능 목록

세부 명세(API, 화면, 테스트)는 각 feature 문서를 기준으로 한다. PRD는 무엇이 필요한지만 정의한다.

### Agent Builder — [features/agent-builder/](features/agent-builder/requirements.md)

- FR-001: 자연어 프롬프트로부터 typed GraphMutation 기반 workflow 생성안 생성
- FR-002: 기본 `단계별 생성`에서 graph를 CAS 저장한 뒤 Knowledge와 parameter를 순차적으로 확인·수정하고 acknowledgement 완료 후 생성 완료 처리
- FR-003: 명시적 `빠른 생성` 요청은 서버 eligibility를 통과한 경우에만 변경 요약을 제공하고, 사용자 `생성 적용` 뒤 같은 GraphMutation/CAS 경계로 저장
- FR-004: `구조만 생성` 결과의 unresolved 설정은 저장할 수 있으나 test, run과 deployment preflight에서 차단
- FR-005: 생성 결과에 필요한 credential/권한 또는 외부 부수효과 확인이 남으면 빠른 생성을 자동 완료하지 않고 명시적 동의 뒤 단계별 생성으로 전환
- FR-006: Knowledge Base-backed LLM node 설정을 포함한 사내 지식 통합 RAG workflow 생성
- FR-007: Agent Builder의 최초 planner와 repair LLM 호출을 실제 사용자·조직·workflow·model·credential에 각각 귀속하고, token·cost를 기존 관리 비용, workflow 예산과 워크플로우 화면의 workflow별 월 예상 비용 집계에 포함한다. 관리 페이지는 조직 총비용을, 워크플로우 화면은 workflow별 총비용을 유지하면서 workflow 실행 비용과 Agent Builder 비용을 함께 구분 표시한다. 워크플로우 화면의 page-level 비용·추세·위험 요약은 표시하지 않는다. 원문 prompt/provider 응답과 credential secret은 저장하지 않는다 ([ADR-0055](decisions/ADR-0055-agent-builder-intent-usage-attribution.md), [ADR-0060](decisions/ADR-0060-my-module-cost-summary-presentation.md)).

### Admin 대시보드 — [features/admin-dashboard/](features/admin-dashboard/requirements.md)

- FR-011: audit log 검색/필터 (행위자, action, 대상, 기간)와 개별 로그 상세 조회 (actor, action, target, status, timestamp)
- FR-012: workflow별 LLM 사용량/비용 집계 표시
- FR-013: 검증된 organization과 인증 actor가 있는 `permission.denied` 및 보안 allowlist `policy.block`을 시간 window와 임계값 기준으로 탐지해 영속 Security Alert로 저장한다. Organization owner/manager는 Sidebar open badge와 Admin Dashboard `보안 알림` 탭에서 alert와 안전한 audit 근거를 확인하고 `open/acknowledged/resolved` lifecycle을 관리한다. 자동 사용자 차단은 하지 않고 기존 actor access management를 통한 수동 조치만 제공한다 ([ADR-0028](decisions/ADR-0028-security-alert-detection-and-lifecycle.md), [Security Alert requirements](features/security-alert/requirements.md)).
- FR-014: workflow 생성/배포 권한 신청 목록 조회와 승인/거절, 부여된 App 생성 권한의 목록 조회와 회수
- FR-015: 조직 월간 비용과 예산 위험/초과 workflow 개수 요약. 비용·예산 위험은 FR-013 Security Alert 탐지 입력과 분리한다.
- FR-016: Audit log의 user actor를 current organization member access profile과 연결하고 membership, role, team/App-creation/direct/team-inherited permission source를 조회
- FR-017: Organization manager가 actor access 항목을 하나씩 정지·재활성화, role 변경, team/direct/App-creation 권한 회수·재부여하고 optional reason을 기록. Audit `auditor`/`raw_auditor`는 조회 전용
- FR-018: Access-management audit detail에 target별 allowlist로 만든 안전한 변경 전후 상태를 표시하고 raw before/after, secret, hidden resource는 제외

### 비용 최적화 — [features/cost-optimizer/](features/cost-optimizer/requirements.md)

- FR-021: LLM 노드 하나를 대상으로 현재 설정(A)과 후보 설정(B)을 같은 입력, 같은 이전 노드 결과 기준으로 비교 실행. 기존 compare API는 node 단위 model/prompt 단일 교체만 지원하므로 설정 묶음 비교로 확장한다.
- FR-022: `modelRouting`, `promptRouting`, task-aware RAG(권한 범위 내 근거 정밀 선택), `responseFormat`, `maxOutputTokens` 조정에 따른 비용 절감률과 품질 차이를 요약한 추천 리포트
- FR-023: LLM 노드 설정 확장 — `modelRouting`, `promptRouting`, task-aware RAG 검색 모드, `responseFormat`, `maxOutputTokens` 설정을 LLM 노드에 추가한다. FR-021~022 비교의 전제이며 현재 코드에 없는 신규 기능이다. 모든 RAG 검색 모드는 NFR-007의 권한 경계 안에서 동작하며, 모드 간 차이는 권한 적용 여부가 아니라 권한 범위 내 검색 정밀도다.
- FR-024(Target): `capability_routing_v2`에서는 Requirement Judge가 bounded 요구 능력만 판정하고 서버가 현재 실행의 데이터 접근 범위, ADR-0064의 server-derived credential principal과 model-bound deployment policy, provider lifecycle, output/effect hard gate와 versioned model evidence를 적용한 뒤 최종 모델을 선택한다. 인증 실행의 데이터 접근 범위는 current execution subject이고, subject가 없는 public·webhook·schedule·API·system 실행은 ADR-0018의 anonymous public-only 범위다. Owner, actor 또는 credential principal을 private data subject로 합성하지 않는다. 다중 후보를 승인하는 별도 credential policy가 Accepted·구현되기 전에는 capability-required V2를 effective strategy로 사용할 수 없다. V1 policy·cache·learner를 V2로 자동 재해석하지 않으며, accepted cache는 activation profile과 exact requirement source identity에 결합한다. Exact Judge-only 또는 승인 learner version, current-valid non-V2 rollback policy를 고정한 activation profile, 실제 delivery를 소비하는 Worker의 호환 runtime capability, locked holdout, 독립 승인과 limited canary를 요구한다. Production 승격은 봉인된 canary evidence snapshot/revision에 직렬화하고 emergency lifecycle은 environment global kill과 activation-domain scoped block을 분리한다. 전용 platform actor와 target resource 권한 경계가 구현되기 전에는 production activation과 billable benchmark를 일반 product API로 제공하지 않는다. 현재 구현은 V2 의미 일부를 `judge_bootstrap_incremental_v1` 경로에서 실행하는 과도기 상태이며 Target 완료를 뜻하지 않는다 ([ADR-0073 Capability Routing V2](decisions/ADR-0073-requirement-judge-capability-routing-v2.md)).

### 통합 RAG — [features/knowledge/](features/knowledge/requirements.md)

- FR-031: 사내 문서 업로드/색인과 KB 단위 권한 관리
- FR-032: 권한·metadata 필터가 적용된 검색과 citation 반환
- FR-033: retrieval 기록 추적 (redaction-safe metadata 기준)
- FR-034: Agent Builder가 생성한 workflow에서 준비된 Knowledge Base를 연결해 사내 문서 질의를 실행
- FR-035: 목표 구조에서는 사내 문서/source item 자동 수집, document-level KB 색인, collection 묶음 관리, source ACL two-gate를 도입한다. MBA-105 구현 baseline은 [ADR-0014](decisions/ADR-0014-knowledge-base-document-atom-and-collection-boundary.md)와 [ADR-0017](decisions/ADR-0017-knowledge-integration-provisional-implementation-baseline.md)을 따른다.
- FR-036: 목표 구조에서는 Workflow Builder가 LLM node의 RAG 옵션을 구성할 때 source tier 선택, collection/KB routing hint, query template, validation checklist를 재사용하기 위해 provider-neutral Knowledge Skill을 도입할 수 있다. Skill은 권한 source나 최종 근거가 아니며 [ADR-0015](decisions/ADR-0015-knowledge-skill-context-routing-boundary.md)의 authorization/redaction/freshness/eval gate를 따른다.

### 권한 신청 — [features/organization/](features/organization/requirements.md)

- FR-041: workflow 생성/배포 권한이 없는 사용자에게 차단 안내를 표시하고, 요청 권한과 신청 사유를 담은 권한 신청을 제출받는다. 관리자 측 목록 조회와 승인/거절은 FR-014를 따른다. 현재 코드에는 초대(invite)만 있고 신청(request) 흐름은 없는 신규 기능이다.
- FR-042: 권한 신청 제출/승인/거절은 canonical audit action으로 기록한다. 필요한 action 명명은 [ADR-0008](decisions/ADR-0008-audit-action-naming-standard.md) 갱신으로 정의한다.

### 예산 관리 — [features/budget-management/](features/budget-management/requirements.md)

- FR-051: workflow 단위 예산을 설정/수정한다. 현재 코드에 예산 개념이 없는 신규 기능이며, FR-015·FR-052의 전제다.
- FR-052: `내 워크플로우` 목록에서 workflow별 예산 사용률을 표시한다. 빌더가 비용 위험 workflow를 발견하는 경로다 (시나리오 3).

### Conversation Memory — [features/conversation-memory/](features/conversation-memory/requirements.md)

- FR-061: Public Chatbot Client는 완료된 최근 20 turn을 매 요청에 전달하고 서버는 4,096-token context 상한을 재검증하며 익명 transcript를 영구 저장하지 않는다.
- FR-062: Memory Context와 저장 entry는 현재 session subject/audience 권한, 값 dependency와 활성 control dependency provenance, privacy/retention 정책을 모두 통과해야 한다.
- FR-063: LLM provider 호출은 LLM Credentials가 발급한 short-lived Provider Execution Capability를 사용하고 credential/grant revoke 뒤 stale capability/context를 새 호출에 사용하지 않는다.
- FR-064: Public Chatbot은 legacy `memory_mode`, browser-generated `conversation_id`와 execution-log history를 사용하지 않는다. 인증형 내부 Chatbot의 durable Memory는 별도 후속 이슈다.

## 6. 비기능 요구사항

| ID | 항목 | 기준 |
| --- | --- | --- |
| NFR-001 | 권한 enforcement | 모든 리소스 API는 서버(Gateway)에서 권한을 판정한다. 프론트 UI 차단만으로 처리하지 않는다. |
| NFR-002 | 테넌시 경계 | 리소스 접근은 organization 경계 안에서만 허용한다 (`X-Organization-Id`, [ADR-0009](decisions/ADR-0009-active-organization-header-context.md)). |
| NFR-003 | 감사 기록 | 주요 행위는 canonical audit action으로 기록한다 ([ADR-0008](decisions/ADR-0008-audit-action-naming-standard.md)). |
| NFR-004 | 민감정보 경계 | credential 원문, API key, token 등 secret은 일반 resource 응답, log, audit, trace projection에 포함하지 않는다. 명시적 발급·회전 또는 보호된 raw trace처럼 원문 처리가 제품 기능상 필요한 경우에는 별도 응답 surface, 최소 권한, 제한 보존, 접근 감사를 갖춘 계약으로 분리한다. 외부 provider 결과와 workflow output은 실행 surface별 projection·redaction 정책을 따른다. |
| NFR-005 | 기존 경로 보존 | workflow 생성/저장/실행/배포의 기존 경로가 깨지지 않는다. |
| NFR-006 | 성능 목표 | TBD (데모 환경 기준 목표치 확정 필요) |
| NFR-007 | RAG 권한 경계 | 모든 RAG 검색 모드는 권한 검사를 통과한 문서만 검색 후보로 사용한다. 권한 없는 문서는 검색 후보, prompt, citation, trace 어디에도 포함되지 않는다. 권한/정책상 제외된 문서를 화면에 표시할 때는 문서명과 정확한 건수를 노출하지 않는 안전한 요약(bucketed summary)으로만 표시한다. |
| NFR-008 | Security Alert 반영 | 정상 worker와 notification 경로에서 eligible event가 임계값에 도달한 뒤 관리자 UI에 1분 이내 반영한다. 탐지 실패는 원래 authorization 결과나 사용자 응답을 변경하지 않는다. |
| NFR-009 | Conversation Memory 격리 | Public Chatbot history는 Client request에만 존재하고 권한 근거가 아니며 server run/node/trace에 원문을 저장하지 않는다. 인증형 내부 Memory session은 deployment version, organization과 execution subject에 고정하고 credential/billing principal 및 audit actor와 분리한다. |
| NFR-010 | Password login 남용 방지 | Email/password login은 credential 검증 전에 account, trusted source network와 account+network 기준의 분산 admission을 적용한다. Counter는 versioned HMAC identity만 사용하고 raw account/IP를 저장하지 않으며, limiter 장애는 credential 검증 전 fail-closed한다 ([ADR-0047](decisions/ADR-0047-password-login-abuse-prevention-boundary.md)). |

## 7. 성공 지표

이 프로젝트의 성공 기준은 **데모 시나리오 완주**다. 시연은 통합 데모 흐름으로 진행하며, 아래 시나리오별 조건이 그 흐름 안에서 모두 동작하면 성공으로 판단한다.

- [ ] 시나리오 1 (권한 신청): 권한 없는 신입사원이 workflow 생성/배포 권한을 신청하고, 관리자가 승인한 뒤 새 workflow 생성까지 완주
- [ ] 시나리오 1 (Agent Builder): Agent Builder가 사내 복지/휴가/인사 정책을 바탕으로 Knowledge Base-backed RAG workflow 구조를 만들고, 기본 단계별 생성에서 권한 있는 Knowledge와 parameter를 확인해 CAS 저장·acknowledgement를 완료한 뒤 별도 테스트 실행에서 응답과 citation/retrieval 근거를 확인
- [ ] 시나리오 2: 조직 관리자가 audit와 비용/예산 위험 요약을 확인하고, 반복 권한·정책 차단에서 생성된 Security Alert를 Sidebar와 Admin Dashboard에서 확인·조사·해결하며 필요한 경우 current organization user access를 수동 조치
- [ ] 시나리오 3: 비용 위험 workflow를 trace로 분석하고 LLM 노드 단위 A/B 비교를 통해 `modelRouting`, `promptRouting`, task-aware RAG, `responseFormat`, `maxOutputTokens` 조정 효과를 확인

## 8. Open Questions

- NFR-006 성능 목표치
- 통합 RAG의 destructive production cutover/reset, raw artifact opt-in, code-bearing Knowledge Skill, platform-wide Workflow egress guard는 [ADR-0017](decisions/ADR-0017-knowledge-integration-provisional-implementation-baseline.md) 범위 밖이며 별도 승인 전까지 구현하지 않는다.

Security Alert의 탐지 대상, 임계값, cooldown, lifecycle, organization 권한과 비범위는 [ADR-0028](decisions/ADR-0028-security-alert-detection-and-lifecycle.md)에서 확정한다. 인증 전/IP 기반 탐지, 플랫폼 운영자 경보, 비용·실행 실패·대량 삭제 같은 운영 이상과 외부 전달 채널은 후속 범위다.
