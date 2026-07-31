# 워크플로우 엔진

> Status: Reference-only historical document.
>
> 이 문서는 과거 `main` 브랜치 기준 역공학 산출물이다. 현재 구현 기준은 active `docs/` 문서와 코드를 따른다.

## 책임

Workflow Engine은 워크플로우 graph의 nodes/edges를 받아 실제 노드를 실행하고 결과를 만들며, 실행 로그와 실시간 이벤트를 남긴다.

확정 근거:

- `apps/workflow_engine/tasks.py`
- `apps/workflow_engine/workflow/core/workflow_engine.py`
- `apps/workflow_engine/workflow/core/workflow_node_factory.py`
- `apps/workflow_engine/workflow/nodes/**`

## 실행 태스크

| Celery task | 함수 | 설명 |
| --- | --- | --- |
| `workflow.execute` | `execute_workflow` | graph와 user_input으로 일반/배포 워크플로우 실행 |
| `workflow.execute_deployed` | `execute_deployed_workflow` | workflow_id 기반 배포 실행으로 보이나 현재 모델과 불일치 흔적 있음 |
| `workflow.execute_by_deployment` | `execute_by_deployment` | deployment_id로 snapshot 조회 후 실행 |
| `workflow.stream` | `stream_workflow` | 외부 run_id를 받아 Pub/Sub/SSE 스트리밍 실행 |

## 엔진 검증 규칙

파일: `workflow_engine.py`

1. Cycle 금지
2. 시작 노드는 하나만 허용
3. 시작 노드가 없으면 에러
4. 시작 노드에서 도달할 수 없는 고립 노드는 에러
5. `note` 타입은 고립 노드 검증 대상에서 제외

시작 노드로 인정되는 타입:

- `startNode`
- `webhookTrigger`
- `scheduleTrigger`

## 실행 모델

- gevent 기반 동시 실행
- pool size 기본 10
- workflow timeout 기본 600초
- node timeout은 `node_schema.timeout`이 있으면 사용, 없으면 300초
- value_selector를 분석해 data dependency가 있는 노드는 참조 노드 완료 후 실행
- Condition node가 `selected_handle`을 반환하면 해당 handle의 edge만 다음 노드로 실행
- stream mode에서는 `workflow_start`, `node_start`, `node_finish`, `workflow_finish`, `error` 이벤트를 yield한다.

## NodeFactory 등록 타입

| 타입 | 클래스 | 구현 상태 |
| --- | --- | --- |
| `startNode` | `StartNode` | 구현 |
| `webhookTrigger` | `WebhookTriggerNode` | 구현 |
| `scheduleTrigger` | `ScheduleTriggerNode` | 구현 |
| `answerNode` | `AnswerNode` | 구현 |
| `codeNode` | `CodeNode` | 구현 |
| `conditionNode` | `ConditionNode` | 구현 |
| `llmNode` | `LLMNode` | 구현 |
| `httpRequestNode` | `HttpRequestNode` | 구현 |
| `slackPostNode` | `HttpRequestNode` | 구현. Slack 전용 UI가 HTTP node data 기반 |
| `githubNode` | `GithubNode` | 구현 |
| `mailNode` | `MailNode` | 구현 |
| `templateNode` | `TemplateNode` | 구현 |
| `workflowNode` | `WorkflowNode` | 구현 |
| `fileExtractionNode` | `FileExtractionNode` | 구현 |
| `variableExtractionNode` | `VariableExtractionNode` | 구현 |
| `loopNode` | `LoopNode` | 구현 |

UI registry에는 `pluginNode`가 있지만 `implemented=false`이고 NodeFactory에 없다.

## 노드별 기능

### startNode

파일: `nodes/start/start_node.py`

- 사용자 입력을 다음 노드에 전달
- 변수 타입 중 `number`는 int/float로 변환
- 결과에 variable `name`과 variable `id`를 모두 key로 넣는다.

### webhookTrigger

파일: `nodes/webhook`

- Webhook payload의 JSON path를 내부 변수명으로 매핑한다.
- Gateway Webhook API와 연결된다.

### scheduleTrigger

파일: `nodes/schedule`

- cron expression과 timezone을 가진다.
- cron expression은 5개 필드인지 검증한다.
- 실제 스케줄 등록은 Gateway `DeploymentService`와 `SchedulerService`에서 수행된다.

### answerNode

- 최종 출력 변수 목록을 가진다.
- 배포 실행에서는 AnswerNode 결과만 최종 결과로 반환한다.

### llmNode

파일: `nodes/llm/llm_node.py`

기능:

- 선택 모델에 대한 사용자 credential/client 조회
- fallback model 지원
- system/user/assistant prompt Jinja rendering
- memory mode context 구성 시도
- Knowledge Base 검색 결과를 비신뢰 컨텍스트로 삽입
- prompt injection guard system prompt 추가
- LLM 호출 후 usage/cost logging

주요 검증:

- `model_id` 필수
- system/user/assistant prompt 중 최소 하나 필요
- fallback model이 primary와 같으면 제거
- 불완전한 referenced variable은 정리

### codeNode

파일: `nodes/code/code_node.py`

- UI에서 정의한 input source `Node.variable`을 code input name에 매핑
- SandboxService를 통해 `/v1/sandbox/execute` 호출
- tenant_id는 execution_context의 user_id를 사용
- trigger mode를 Sandbox priority fallback에 전달

사용자 코드는 `def main(inputs): ... return dict` 형태를 기대한다.

### httpRequestNode / slackPostNode

- HTTP method, URL, headers, body, timeout, authType/authConfig, referenced variables를 가진다.
- `slackPostNode`는 NodeFactory에서 HttpRequestNode로 실행된다.

### conditionNode

- multi-branch cases 지원
- 각 case는 conditions와 logical operator를 가진다.
- operator: equals, not_equals, contains, not_contains, starts_with, ends_with, is_empty, is_not_empty, greater_than, less_than, greater_than_or_equals, less_than_or_equals
- 선택된 case handle을 반환해 다음 edge를 결정한다.

### templateNode

- Jinja2 template과 variables를 사용해 텍스트를 생성한다.

### workflowNode

- 다른 App/Workflow deployment를 서브 모듈처럼 실행하는 노드다.
- 입력 mapping과 예상 output 목록을 가진다.

### fileExtractionNode

- 파일 경로 변수에서 문서를 읽어 텍스트를 추출한다.

### variableExtractionNode

- JSON source selector와 JSON path mappings로 변수를 추출한다.

### githubNode

- action: `get_pr`, `comment_pr`
- GitHub token, repo owner/name, PR number, comment body 사용

### mailNode

- IMAP 기반 메일 검색
- provider: gmail, naver, daum, outlook, custom
- keyword/sender/subject/date/unread/mark-as-read 설정

### loopNode

파일: `nodes/loop/loop_node.py`

- 배열을 반복하며 subGraph 실행
- `loop.item`, `loop.index` 특수 변수 제공
- 명시적 input mapping 또는 모든 외부 변수 자동 전달
- max iterations 기본 100
- error strategy: `end`, `continue`
- output mapping과 flatten option 지원

## 프론트 노드 registry와 기본값

파일: `apps/client/app/features/workflow/config/nodeRegistry.tsx`

| UI 이름 | 타입 | 카테고리 | 기본값 핵심 |
| --- | --- | --- | --- |
| 입력 | `startNode` | trigger | variables |
| 웹훅 트리거 | `webhookTrigger` | trigger | provider custom, variable_mappings |
| 알람 트리거 | `scheduleTrigger` | trigger | `0 9 * * *`, UTC |
| LLM | `llmNode` | llm | model, prompts, params |
| 서브 모듈 | `workflowNode` | workflow | workflowId/appId/inputs/outputs |
| 코드 실행 | `codeNode` | logic | Python main template |
| IF/ELSE | `conditionNode` | logic | conditions/cases |
| 문서 추출 | `fileExtractionNode` | logic | referenced_variables |
| 변수 추출 | `variableExtractionNode` | logic | source_selector, mappings |
| 응답 | `answerNode` | logic | outputs |
| 반복 | `loopNode` | logic | loop_key, inputs, outputs |
| HTTP 요청 | `httpRequestNode` | plugin | GET, url, headers, body |
| slack | `slackPostNode` | plugin | Slack API defaults |
| 템플릿 | `templateNode` | logic | template, variables |
| github | `githubNode` | plugin | get_pr defaults |
| 메일 검색 | `mailNode` | plugin | Gmail IMAP defaults |

## 주의/검증 필요

- `apps/workflow_engine/workflow/nodes/start/entities.py`는 주석만 있고 실제 `StartNodeData`는 `start_node.py`에 있다.
- `execute_deployed_workflow`는 현재 모델과 맞지 않는 필드명을 사용한다. 실제 활성 실행 경로인지 확인이 필요하다.
- `DeploymentService.run_deployment()`는 `trigger_mode` 인자를 받지만 현재 `execution_context.trigger_mode`를 `"app"`으로 고정한다. `/run/{url_slug}` API 실행 로그가 `api`로 기록되는지 런타임 확인 또는 코드 수정이 필요하다.
- `log.create_run`은 현재 `webhook`, `schedule`, `scheduler` trigger 문자열을 명시적으로 `RunTriggerMode`에 매핑하지 않는다. Webhook/Scheduler 실행 로그의 trigger mode는 코드 수정 없이는 실제 의도와 다르게 저장될 수 있다.
- LLM memory mode는 코드에서 시도하지만, 사용자 UI/저장 형식과 완전히 연결되는지는 추가 런타임 검증이 필요하다.
