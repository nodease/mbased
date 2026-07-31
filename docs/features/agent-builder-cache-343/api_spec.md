# MBA-343 Agent Builder Cache Spine Internal API Specification

Status: Draft

## 1. API Surface

MBA-343은 public HTTP API를 추가하거나 변경하지 않는다. 이 문서는 Gateway application 내부에서만
사용하는 Python contract를 정의한다. 외부 response, DB schema와 Redis wire protocol은 이 문서의 API가 아니다.

## 2. Public Package Boundary

`apps.gateway.application.agent_builder.intent_cache` package는 다음 이름만 public export한다.

| Contract | Responsibility |
| --- | --- |
| `CachedIntentPlanV1` | 직렬화 가능한 strict cache value |
| `IntentPlanningContext` | 직렬화할 수 없는 transient planning input |
| `EphemeralCacheScope` | actor/organization/selected target identity의 non-serializable holder |
| `IntentCacheKey` | 후속 key builder가 만든 opaque digest와 namespace |
| `CacheBoundaryDecision` | `hit|miss|bypass|error` 내부 결과 |
| `IntentNormalizationResult` | normalizer의 strict eligible/bypass result |
| `IntentRehydrationResult` | rehydrator의 strict success/failure result |
| `IntentPlanLoadResult` | store load의 strict hit/miss/invalid/unavailable result |
| `IntentPlanSaveResult` | store save의 strict stored/unavailable result |
| `IntentNormalizerPort` | deterministic eligibility/normalization interface |
| `IntentPlanStorePort` | typed cache load/save interface |
| `IntentPlanRehydratorPort` | current-context rehydration interface |
| `IntentPlanCacheBoundary` | composition이 노출하는 단일 cache entry point |
| `DisabledIntentPlanCacheBoundary` | MBA-343의 유일한 concrete boundary |
| `IntentPlanExecution` | Planner 결과와 cache decision을 묶는 내부 no-op result |
| `CanonicalIntentPlanCodec` | strict canonical bytes encode/decode |
| `IntentPlanCodecError` | codec의 payload-safe typed failure |

Internal helper와 concrete nested model은 필요하지 않으면 package root에서 export하지 않는다.

## 3. `CachedIntentPlanV1`

모든 cache value object는 Pydantic strict/frozen model이며 `extra=forbid`, strict field type,
field alias 미사용과 coercion 금지를 적용한다. Tuple의 순서는 semantic contract다.

| Field | Type | Cardinality and invariant |
| --- | --- | --- |
| `schema_version` | literal `1` | required |
| `request_type` | `new_workflow|modify_workflow` | clarification/unsupported/validation result 금지 |
| `draft_mode` | `new_workflow|modify_workflow|replace_workflow` | 아래 valid pair 중 하나 |
| `ordered_capabilities` | tuple of `CapabilityRef` | 1..32, 현재 Catalog v3 allowlist exact membership |
| `logical_steps` | tuple of `LogicalStepRef` | capability와 같은 순서/개수, 1..32 |
| `edit_placement` | `CachedEditPlacement` or null | selected-target modify에서만 required |
| `integration_actions` | tuple of `IntegrationActionRef` | 0..16, 중복 없음 |
| `parameter_guidance_refs` | tuple of `CachedParameterGuidanceRef` | 0..128, `(step, parameter_key)` 중복 없음 |
| `knowledge_requirements` | tuple of `CachedKnowledgeRequirement` | 0..8, requirement ref 중복 없음 |
| `knowledge_placements` | tuple of `CachedKnowledgePlacement` | 0..8, requirement ref가 존재해야 함 |
| `risk_flags` | tuple of `CacheRiskFlag` | 0..8, 중복 없음 |
| `contract_versions` | `IntentPlanContractVersions` | required |

Rendered `intent_summary`, step purpose, topic와 guidance text는 field로 존재하지 않는다. Rehydrator가
current `IntentPlanningContext.full_safe_message`, ordered capability, logical step, current Catalog와 canonical
registry에서 결정적으로 만든다. Summary는 request/draft type의 공통 문장에서 파생하지 않는다.

Valid `(request_type, draft_mode)` pair는 `(new_workflow, new_workflow)`,
`(modify_workflow, modify_workflow)`와 `(modify_workflow, replace_workflow)`뿐이다. Selected-target
`edit_placement`는 `(modify_workflow, modify_workflow)`에만 required이며 다른 두 pair에서는 null이어야 한다.

### 3.1 `LogicalStepRef`

| Field | Type | Rule |
| --- | --- | --- |
| `capability` | `CapabilityRef` | 현재 Catalog v3 Agent Builder allowlist member |
| `occurrence` | strict integer | 같은 capability의 1-based occurrence, 1..32 |

`logical_steps[n].capability`은 `ordered_capabilities[n]`과 같아야 하고 occurrence는 해당 capability의
등장 순서와 정확히 일치해야 한다. 실제 `step_id`, node ID 또는 배열 index는 저장하지 않는다.

### 3.2 `CachedEditPlacement`

| Field | Type | Rule |
| --- | --- | --- |
| `placement` | `before|after|between` | required |
| `target_reference_type` | `selected_node|selected_edge` | natural-language target 금지 |
| `step_refs` | tuple of `LogicalStepRef` | 1..32, plan member만 허용 |

`before|after`는 `selected_node`, `between`은 `selected_edge`만 허용한다. 실제 selected target identity는
plan에 없고 `EphemeralCacheScope`에서 후속 HMAC input으로만 사용한다. Natural-language target 기반 modify는
exact alias가 있더라도 strict plan schema가 거부한다. 전체 요청의 runtime bypass/store-ineligible 판정은
후속 projection/coordinator 범위다.

### 3.3 `IntegrationActionRef` and `CacheRiskFlag`

`IntegrationActionRef` v1 member는 `github.pull_request.read`와
`github.pull_request.comment`다. Unsupported create action은 cache할 수 없다.

`CacheRiskFlag` v1 member는 다음 다섯 값이다.

- `external_action_requested`
- `slack_channel_unresolved`
- `github_configuration_unresolved`
- `gmail_credential_unresolved`
- `external_configuration_unresolved`

`unsupported_capability`이 필요한 result는 actionable plan이 아니므로 cache value로 만들지 않는다.

### 3.4 `CachedKnowledgeRequirement`

| Field | Type | Rule |
| --- | --- | --- |
| `requirement_ref` | canonical requirement ref | `^kr_[1-8]$`, plan 안에서 unique |
| `required` | strict boolean | required |
| `evidence_kind` | literal `policy_or_reference` | required |
| `target_step_ref` | `LogicalStepRef` | plan member |
| `topic_refs` | tuple of `CanonicalKnowledgeTopicRef` | 1..20, 순서 보존, 첫 occurrence만 유지 |

### 3.5 `CachedKnowledgePlacement`

| Field | Type | Rule |
| --- | --- | --- |
| `requirement_ref` | canonical requirement ref | matching requirement required |
| `timing` | `before_graph|after_graph` | required |
| `effect_kind` | `insert_step|binding_only` | timing과 일치 |
| `target_step_ref` | `LogicalStepRef` or null | after-graph binding에 required |
| `knowledge_step_ref` | `LogicalStepRef` or null | before-graph insert에 required |
| `upstream_step_ref` | `LogicalStepRef` or null | before-graph insert에 required |
| `downstream_step_ref` | `LogicalStepRef` or null | before-graph insert에 required |
| `empty_selection_bridge` | literal `connect_upstream_to_downstream` or null | before-graph insert에 required |

After-graph placement는 `binding_only + target_step_ref`만 허용하고 나머지 topology ref를 금지한다.
Before-graph placement는 `insert_step`과 네 topology/bridge field를 모두 요구한다.

### 3.6 `CachedParameterGuidanceRef`

| Field | Type | Rule |
| --- | --- | --- |
| `logical_step_ref` | `LogicalStepRef` | plan member |
| `parameter_key` | canonical Catalog key | 해당 capability의 현재 Catalog parameter exact member |
| `reason_template_ref` | `CanonicalGuidanceReasonRef` | registry exact member |
| `input_guidance_template_ref` | `CanonicalInputGuidanceRef` | registry exact member와 current input type 호환 |

Reason/input guidance 문자열, template argument와 actual/default parameter value는 허용하지 않는다.

### 3.7 `IntentPlanContractVersions`

| Field | Type | Rule |
| --- | --- | --- |
| `normalizer_version` | `VersionRef` | required |
| `cache_schema_version` | literal `1` | root schema와 일치 |
| `planner_contract_version` | `VersionRef` | required |
| `catalog_version` | literal `3` | current Catalog contract |
| `canonical_text_registry_version` | literal `intent-text-v1` | current manifest와 일치 |
| `materializer_version` | `VersionRef` | required |

HMAC key version은 cache key/envelope namespace가 소유하며 plan value에 넣지 않는다.

## 4. Reference Contract

`VersionRef`와 generic internal ref는 ASCII lower-case token
`^[a-z0-9][a-z0-9._:-]{0,127}$`을 만족해야 한다. 이 형식만으로 canonical text ref를 허용하지 않는다.

- Knowledge topic: `topic.<slug>.v1`
- Guidance reason: `guidance.reason.<slug>.v1`
- Input guidance: `guidance.input.<slug>.v1`

### 4.1 Catalog v3 Contract Snapshot

`CapabilityRef`는 `catalog_version=3`에 고정된 다음 19개 member만 허용한다.

- `answer`
- `code_execution`
- `condition`
- `file_extraction`
- `github_pr_comment`
- `github_pr_read`
- `gmail_reply_draft_create`
- `http_request`
- `knowledge_backed_llm`
- `llm`
- `mail_search`
- `mail_terminal_acknowledgement`
- `schedule_trigger`
- `slack_send`
- `start_input`
- `template_render`
- `variable_extraction`
- `webhook_trigger`
- `workflow_call`

`apps.gateway.application.agent_builder.intent_cache.catalog_snapshot`은 위 member와 다음
`capability -> parameter_key -> input_type` 관계를 immutable pure-data snapshot으로 소유한다.

| Node type | Role | Capability | Allowed parameter keys and input types |
| --- | --- | --- | --- |
| `startNode` | `entry` | `start_input` | `variables:json` |
| `webhookTrigger` | `entry` | `webhook_trigger` | `variable_mappings:json` |
| `scheduleTrigger` | `entry` | `schedule_trigger` | `cron_expression:text`, `timezone:text` |
| `llmNode` | `intermediate` | `llm`, `knowledge_backed_llm` | `model_id:resource_ref`, `output_format_type:select`, `output_json_schema:json`, `system_prompt:textarea`, `user_prompt:textarea`, `assistant_prompt:textarea`, `referenced_variables:variable_selector_list`, `citationDisplayMode:select`, `auto_model_routing:boolean`, `fallback_model_id:resource_ref`, `model_routing_refresh_every_runs:number`, `model_routing_validation_budget_usd:number`, `model_routing_max_cohorts:number`, `knowledgeBases:resource_ref` |
| `workflowNode` | `intermediate` | `workflow_call` | `workflowId:resource_ref`, `appId:resource_ref` |
| `codeNode` | `intermediate` | `code_execution` | `code:code` |
| `conditionNode` | `branch` | `condition` | `cases:json` |
| `fileExtractionNode` | `intermediate` | `file_extraction` | `referenced_variables:variable_selector` |
| `variableExtractionNode` | `intermediate` | `variable_extraction` | `source_selector:variable_selector`, `mappings:json` |
| `answerNode` | `terminal` | `answer` | `outputs:json` |
| `httpRequestNode` | `intermediate` | `http_request` | `url:text` |
| `slackPostNode` | `intermediate` | `slack_send` | `slackMode:select`, `bot_token:secret`, `url:secret`, `channel:text`, `message:textarea`, `blocks:json`, `attachments:json`, `thread_ts:text`, `username:text`, `icon_emoji:text` |
| `templateNode` | `intermediate` | `template_render` | `template:textarea` |
| `githubNode` | `intermediate` | `github_pr_read`, `github_pr_comment` | `action:select`, `api_token:secret`, `repo_owner:text`, `repo_name:text`, `pr_number:number`, `comment_body:textarea` |
| `mailNode` | `intermediate` | `mail_search` | `credential_id:credential_ref`, `keyword:text`, `sender:text`, `subject:text`, `start_date:text`, `end_date:text`, `folder:select`, `max_results:number`, `unread_only:boolean`, `mark_as_read:boolean`, `processing_mode:select` |
| `gmailDraftNode` | `intermediate` | `gmail_reply_draft_create` | `credential_id:credential_ref`, `processing_ref_selector:variable_selector`, `reply_body_selector:variable_selector` |
| `mailAcknowledgeNode` | `terminal` | `mail_terminal_acknowledgement` | `processing_ref_selector:variable_selector`, `required_effect_ref_selectors:variable_selector_list` |

Production contract는 `apps.shared.services.workflow_node_catalog`를 import하거나 JSON file을 runtime에 읽지 않는다.
대신 static drift test가 snapshot을 current Catalog v3와 비교한다. Node type/role, capability,
parameter key 또는 input type membership이 달라지면 test가
fail-closed하고 `catalog_version` 또는 cache schema version을 올리는 별도 변경 없이는 cache contract를
자동 확장하지 않는다.

### 4.2 Canonical Text Reference v1

`canonical_text_registry_version=intent-text-v1`이 허용하는 초기 closed member는 다음뿐이다.

| Type | Allowed member |
| --- | --- |
| `CanonicalKnowledgeTopicRef` | `topic.internal_documents.v1` |
| `CanonicalGuidanceReasonRef` | `guidance.reason.delivery_destination_required.v1` |
| `CanonicalInputGuidanceRef` | `guidance.input.select_slack_channel_id.v1` |

이 최소 집합은 현재 Agent Builder 회귀 fixture의 `사내 문서` topic과 Slack `channel` guidance를 위한
reference contract만 선행한다. 실제 alias, canonical text와 template rendering manifest는 후속
`intent_rehydration_registry.py`가 소유한다. 그 manifest는 위 집합을 빠짐없이 정확히 한 번 구현해야 한다.
`topic.internal_documents.v1`은 target capability가 `knowledge_backed_llm`일 때만 허용한다.
두 Slack guidance ref는 같은 hint에서 함께 사용하고 `(capability=slack_send, parameter_key=channel,
input_type=text)`일 때만 허용한다.
이 세 member 밖의 provider text는 fuzzy, case correction 또는 의미 추론으로 보정하지 않고 후속 projection에서
store-ineligible로 처리한다.

### 4.3 Request Summary Projection and Step Purpose v1

`intent-text-v1`은 summary 문자열이나 request별 ref를 plan에 저장하지 않는다. 대신 다음 exact immutable
projection descriptor를 소유하며 future rehydrator가 매 요청의 current transient context에 적용한다.

| Property | Exact v1 value |
| --- | --- |
| `projection_id` | `summary.current_safe_message.v1` |
| `source` | `IntentPlanningContext.full_safe_message` |
| `whitespace_profile` | `python-split-v1`: Python `" ".join(value.split())`과 같은 Unicode whitespace collapse와 trim |
| `redaction_profile` | `agent-builder-safe-summary-v1`: 현재 `_safe_summary`의 fail-closed trace redaction, auth/secret/URL/path redaction과 lowercase `[redacted]` marker contract |
| `max_codepoints` | redaction 뒤 첫 240 Unicode code point |
| `failure` | empty 또는 `[redacted]` marker가 남으면 `summary_projection_failed` |
| `provider_summary` | cache-eligible canonical rehydration에서는 사용하지 않음 |
| `persistence` | summary, source message와 provider summary를 cache key/value/diagnostic/metric/audit에 저장하지 않음 |

Cold miss와 warm hit는 모두 current `full_safe_message`에 위 projection을 적용한다. 같은 safe request에는 같은
summary를 만들고, 같은 request/draft pair와 logical plan을 만들더라도 서로 다른 safe request는 각 current
context에서 독립적으로 summary를 재구성한다. MBA-343은 descriptor snapshot만 구현하며 실제 projection과
structured request rendering은 후속 rehydration 이슈가 소유한다.

각 `LogicalStepRef.capability`의 canonical `purpose`는 다음 exact immutable table에서 가져온다.

| Capability | Canonical purpose |
| --- | --- |
| `start_input` | `사용자 입력을 받습니다.` |
| `webhook_trigger` | `Webhook payload를 받습니다.` |
| `schedule_trigger` | `설정된 일정에 따라 workflow를 시작합니다.` |
| `file_extraction` | `입력 파일에서 텍스트를 추출합니다.` |
| `variable_extraction` | `입력 데이터에서 필요한 변수를 추출합니다.` |
| `github_pr_read` | `GitHub Pull Request와 변경 파일을 조회합니다.` |
| `mail_search` | `메일을 검색합니다.` |
| `gmail_reply_draft_create` | `원본 메일 thread에 Gmail 답장 초안을 생성합니다.` |
| `mail_terminal_acknowledgement` | `필수 작업 성공 후 원본 메일 처리를 완료합니다.` |
| `http_request` | `외부 HTTP API를 호출합니다.` |
| `workflow_call` | `다른 workflow를 호출합니다.` |
| `code_execution` | `sandbox에서 코드를 실행합니다.` |
| `template_render` | `입력값으로 템플릿을 렌더링합니다.` |
| `condition` | `조건에 따라 흐름을 분기합니다.` |
| `llm` | `입력을 분석하고 결과를 생성합니다.` |
| `knowledge_backed_llm` | `Knowledge Base 근거로 입력을 분석하고 결과를 생성합니다.` |
| `github_pr_comment` | `생성한 내용을 GitHub Pull Request 댓글로 등록합니다.` |
| `slack_send` | `이전 단계 결과를 Slack 메시지로 전송합니다.` |
| `answer` | `이전 단계 결과를 응답으로 반환합니다.` |

Summary/purpose text와 template argument는 plan value에 저장하지 않는다. Future
`intent_rehydration_registry.py`는 topic/guidance, summary projection과 purpose table을 exact 구현하고
`canonical_text_registry_version=intent-text-v1`로 함께 versioning해야 한다. MBA-343은 descriptor/table
contract와 exact membership test만 구현하며 structured request rendering은 구현하지 않는다.

## 5. `IntentPlanningContext`

이 타입은 cache value가 아닌 non-serializable transient class다. Pydantic `BaseModel` 또는 dataclass dump
대상으로 만들지 않고 `__slots__`, object-identity equality와 고정 redacted `repr`을 사용한다.

| Field | Type | Rule |
| --- | --- | --- |
| `full_safe_message` | strict string | 1..4000, sanitizer를 통과한 전체 요청, 중간 절단 금지 |
| `workflow_context` | `IntentLogicalTopology` | 전체 safe topology, 중간 절단 금지 |
| `planner_runtime` | `PlannerRuntimeFingerprint` | 검증된 provider/model/credential relation digest |
| `generation_mode` | `guided_generate|quick_generate|structure_only` | legacy alias를 정규화한 canonical value |
| `knowledge_context_fingerprint` | 64-char lower hex digest | 권한·lifecycle을 통과한 전체 후보 집합 |
| `contract_versions` | `IntentPlanContractVersions` | required |
| `scope` | `EphemeralCacheScope` | non-serializable actual identity holder |

### 5.1 `IntentLogicalTopology`

| Field | Type | Rule |
| --- | --- | --- |
| `workflow_present` | strict boolean | required |
| `nodes` | tuple of `IntentLogicalNode` | 전체 node projection, cache용 임의 상한 절단 금지 |
| `edges` | tuple of `IntentLogicalEdge` | 전체 edge projection, cache용 임의 상한 절단 금지 |

Tuple 길이는 기존 request admission과 graph validation이 허용한 전체 graph 크기이며 cache 전용 추가 상한은
두지 않는다. `workflow_present=false`이면 nodes와 edges는 모두 비어 있어야 한다. `workflow_present=true`는
빈 workflow shell을 허용하므로 빈 tuple도 유효하다. Node와 edge `logical_ref`의 ordinal은 각 tuple의
1-based 위치와 같아야 한다.

`IntentLogicalNode`는 다음 field만 갖는다.

| Field | Type | Rule |
| --- | --- | --- |
| `logical_ref` | `n_<ordinal>` | `^n_[1-9][0-9]*$`, node tuple 위치와 일치 |
| `node_type` | Catalog v3 Agent Builder node type | 4.1 snapshot이 가리키는 current node type |
| `safe_label` | strict string | 1..255, 기존 secret/path sanitizer 통과 |
| `role` | `entry|intermediate|branch|terminal` | Catalog v3 connection role과 일치 |

`IntentLogicalEdge`는 다음 field만 갖는다.

| Field | Type | Rule |
| --- | --- | --- |
| `logical_ref` | `e_<ordinal>` | `^e_[1-9][0-9]*$`, edge tuple 위치와 일치 |
| `source_node_ref` | `IntentLogicalNode.logical_ref` | node tuple member |
| `target_node_ref` | `IntentLogicalNode.logical_ref` | node tuple member, source와 다름 |
| `source_handle_kind` | `standard|condition_case|condition_default` | structural meaning only |
| `source_handle_ordinal` | strict integer or null | `condition_case`에서만 1..32, 나머지는 null |
| `target_handle_kind` | literal `standard` | raw handle ID 금지 |

실제 node/edge ID, position, viewport, raw handle, condition value와 raw parameter는 포함하지 않는다. Complete
safe projection을 만들 수 없으면 context를 부분 생성하지 않고 후속 normalization result를
`bypass/graph_projection_incomplete`로 만든다. MBA-343은 그 normalization을 구현하지 않는다.

### 5.2 `PlannerRuntimeFingerprint`

| Field | Type | Rule |
| --- | --- | --- |
| `provider_ref` | `openai|google|anthropic` | validated current runtime |
| `model_relation_fingerprint` | 64-char lower hex digest | raw model/credential ID 없음 |
| `credential_relation_fingerprint` | 64-char lower hex digest | credential config 없음 |

### 5.3 `EphemeralCacheScope`

`EphemeralCacheScope`는 다음 constructor input을 private slot에만 보유한다.

| Input | Type | Rule |
| --- | --- | --- |
| `actor_id` | UUID | required |
| `organization_id` | UUID | required |
| `selected_target_type` | `selected_node|selected_edge` or null | target identity와 함께 존재하거나 함께 null |
| `selected_target_id` | strict string or null | 1..255, target type과 함께 존재하거나 함께 null |

Public property, iterator와 mapping view를 제공하지 않는다. Future domain-separated key builder만 같은 package의
private slot을 소비할 수 있으며 MBA-343 production path에는 consumer가 없다. 이 타입의 안전 경계는
privileged Python introspection 방어가 아니라 accidental serialization/logging 방지다.

`IntentPlanningContext`와 `EphemeralCacheScope`는 plain `__slots__` class이며 dataclass/Pydantic model이 아니다.
둘 다 `model_dump`, `dict`, `__iter__`, `__getstate__`를 제공하지 않고 `__reduce_ex__`에서 `TypeError`를 발생시킨다.
다음 검증 결과를 고정한다.

- `json.dumps(value)`, `vars(value)`, `dataclasses.asdict(value)`와 `pickle.dumps(value)`는 `TypeError`다.
- `repr(IntentPlanningContext)`는 `<IntentPlanningContext redacted>`다.
- `repr(EphemeralCacheScope)`는 `<EphemeralCacheScope redacted>`다.
- equality는 object identity이고 `__hash__ = None`이다.

## 6. Codec Contract

`CanonicalIntentPlanCodec.encode(plan, max_payload_bytes)`는 canonical UTF-8 bytes를 반환한다.
`decode(payload, max_payload_bytes)`는 `CachedIntentPlanV1`을 반환한다.

Canonical encoder는 `plan.model_dump(mode="json", exclude_none=False)` 결과에 Python 표준 JSON encoder의
`sort_keys=True`, `separators=(",", ":")`, `ensure_ascii=False`, `allow_nan=False`를 적용하고 UTF-8로
encode한다. BOM, trailing newline과 trailing whitespace를 붙이지 않는다. Schema의 모든 key와 ref는 ASCII고
rendered user text가 plan에 없으므로 Unicode normalization은 codec 책임이 아니다. Decoder는 다음 순서를
고정한다.

1. byte size를 parse 전에 검사한다.
2. UTF-8 strict mode로 decode한다.
3. `object_pairs_hook`으로 duplicate key를, `parse_constant`로 non-finite number를 거부해 raw object tree를
   한 번 검사하고 JSON root가 object인지 확인한다.
4. 같은 원본 `payload` bytes를 `CachedIntentPlanV1.model_validate_json(payload, strict=True)`에 전달해
   Pydantic JSON mode로 검증한다. JSON array를 Python `list`로 parse한 뒤 strict tuple model에 Python mode로
   전달하거나 임의 tuple coercion을 수행하지 않는다.
5. 검증된 model을 canonical re-encode해 원본 bytes와 exact 비교한다.

최소 plan의 expected canonical bytes는 test 안의 literal golden fixture로 고정한다. Codec 구현 변경이 같은
semantic plan의 bytes를 바꾸면 cache schema version을 올리지 않는 한 test를 갱신해서는 안 된다.

두 method는 public `IntentPlanCodecError`만 발생시키며 다음 safe error code 중 하나만 노출한다.

| Code | Allowed path category | Meaning |
| --- | --- | --- |
| `payload_too_large` | `payload_size` | parse 전 bounded size 초과 |
| `invalid_utf8` | `root` | UTF-8 decode 실패 |
| `invalid_json` | `root` | JSON object가 아니거나 syntax/duplicate key/non-finite number 오류 |
| `unsupported_schema_version` | `contract_version` | 지원하지 않는 plan version |
| `forbidden_cache_content` | `cache_content` | 금지 field/value 발견 |
| `invalid_plan_schema` | `reference|payload_shape` | strict DTO의 ref 또는 일반 shape 검증 실패 |
| `non_canonical_payload` | `root` | decode 후 canonical re-encode bytes 불일치 |

`IntentPlanCodecError`는 `ValueError`의 final subclass이며 read-only `code`와 `path_category`만 제공한다.
`path_category`는 `root|payload_size|contract_version|reference|cache_content|payload_shape` 중 하나다.
위 table에 없는 code/path 조합은 error construction에서 거부한다.
`str(error)`와 `repr(error)`는 `<code>:<path_category>`만 포함하고 field value, 전체 field path, validation input,
payload excerpt와 chained parser exception을 포함하지 않는다. Underlying parser/Pydantic failure를 catch한
block에서는 closed code/category만 남기고 block을 빠져나온 뒤 public error를 발생시킨다. 결과 error의
`__cause__`와 `__context__`는 모두 null이어야 하며 traceback local에 parser/Pydantic failure를 연결하지 않는다.

### 6.1 `IntentCacheKey`

후속 key builder가 제공하는 `IntentCacheKey`는 다음 strict immutable field만 가진다.

| Field | Type | Rule |
| --- | --- | --- |
| `namespace` | literal `agent-builder:intent-plan` | required |
| `key_version` | `VersionRef` | required |
| `digest` | 64-char lower hex digest | raw key material이 아닌 HMAC 결과 |

이 타입은 raw request, identity, graph, HMAC secret과 key material projection을 받지 않는다. Key 생성과
Redis 문자열 rendering은 MBA-343 범위가 아니며 store port는 이 opaque typed key만 받는다.

## 7. Port Contracts

### 7.1 `IntentNormalizerPort`

`normalize(context: IntentPlanningContext) -> IntentNormalizationResult`다.

`IntentNormalizationResult`는 strict immutable DTO이며 다음 field를 갖는다.

| Field | Type | Rule |
| --- | --- | --- |
| `status` | `eligible|bypass` | required |
| `intent_signature` | 64-char lower hex SHA-256 or null | eligible에서 required, raw normalized text 아님 |
| `reason` | `NormalizationBypassReason` or null | bypass에서 required |
| `normalizer_version` | `VersionRef` | required |
| `sensitive_input_detected` | strict boolean | `sensitive_input` reason에서만 true |

`NormalizationBypassReason` v1 member는 `sensitive_input`, `explicit_value_suspected`,
`unknown_token_sequence`, `ambiguous_target`, `unsupported_request_shape`, `normalizer_disabled`,
`input_projection_truncated`, `graph_projection_incomplete`다. Eligible result는 signature가 있고 reason이 null이며
`sensitive_input_detected=false`다. Bypass result는 signature가 null이고 reason이 있다. 일반적인 ineligible
request를 exception으로 표현하지 않는다. Normalization 구현은 MBA-343 범위가 아니다.

### 7.2 `IntentPlanStorePort`

`load(key: IntentCacheKey) -> IntentPlanLoadResult`와
`save(key: IntentCacheKey, plan: CachedIntentPlanV1) -> IntentPlanSaveResult`를 제공한다.

`IntentPlanLoadResult`는 다음 invariant를 갖는 strict immutable DTO다.

| Status | Plan | Reason |
| --- | --- | --- |
| `hit` | required | null |
| `miss` | null | `not_found` |
| `invalid` | null | `invalid_cached_plan` |
| `unavailable` | null | `cache_unavailable` |

`IntentPlanSaveResult`는 `status=stored`이면 `reason=null`, `status=unavailable`이면
`reason=cache_unavailable`인 strict immutable DTO다. Unknown status/reason과 모순된 plan/reason 조합은
construction에서 거부한다.
Port는 TTL, Redis command, connection과 serialization bytes를 노출하지 않는다. Adapter failure mapping과
fail-open orchestration은 후속 coordinator가 소유한다.

### 7.3 `IntentPlanRehydratorPort`

`rehydrate(plan: CachedIntentPlanV1, context: IntentPlanningContext) -> IntentRehydrationResult`다.
`IntentRehydrationResult`는 strict immutable DTO이며 `status=success`일 때 기존
`AgentBuilderStructuredRequest`가 required이고 reason은 null이다. `status=failure`일 때 structured request는
null이고 reason은 `contract_version_mismatch|catalog_member_missing|canonical_reference_missing|
guidance_input_type_incompatible|logical_reference_invalid|summary_projection_failed|current_context_invalid` 중
하나다. 새 downstream DTO를 복제하지 않는다.
Resource resolution, permission과 lifecycle revalidation은 별도 현재-context port가 완료한 결과만
입력받는다. Success는 provider `intent_summary`가 아니라 current context의 `full_safe_message`와
`summary.current_safe_message.v1`에서 request-specific summary를 만들어야 한다. 구현은 MBA-343 범위가 아니다.

## 8. Boundary Decision

`CacheBoundaryDecision`은 strict immutable `outcome`, `plan`, `reason` 세 field와 다음 invariant를 갖는다.

| Outcome | Plan | Reason |
| --- | --- | --- |
| `hit` | required | null |
| `miss` | null | null 또는 `not_found|invalid_cached_plan|rehydration_failed` |
| `bypass` | null | `feature_disabled|normalization_bypass` |
| `error` | null | `cache_unavailable` |

`IntentPlanExecution`은 다음 두 field만 갖는 strict immutable 내부 result다.

| Field | Type | Rule |
| --- | --- | --- |
| `structured_request` | `AgentBuilderStructuredRequest` | `planner_call`이 반환한 동일 object |
| `decision` | `CacheBoundaryDecision` | disabled에서는 `bypass/feature_disabled` |

`IntentPlanCacheBoundary.execute(
planner_call: Callable[[], AgentBuilderStructuredRequest],
context: IntentPlanningContext | None = None,
) -> IntentPlanExecution`는 application의 단일 실행 계약이다. `planner_call`은 기존
extract/validate/normalize sequence를 캡슐화한다.

MBA-343의 `DisabledIntentPlanCacheBoundary.execute(...)`는 `planner_call`을 정확히 한 번 호출하고
그 결과와 `bypass/feature_disabled`를 `IntentPlanExecution`으로 반환한다. Optional context는 읽거나
복사, 직렬화 또는 log하지 않는다. normalizer/store/rehydrator port, codec, provider와 I/O를 직접
호출하지 않으며 `planner_call`이 던진 exception은 변환 또는 재시도 없이 그대로 전파한다.

## 9. Composition Contract

`AgentBuilderComposition.intent_plan_cache()`는 `IntentPlanCacheBoundary`를 반환하며 MBA-343에서는
항상 disabled concrete type이다. `orchestration()`은 이 factory를 정확히 한 번 호출하고 반환값을
`AgentBuilderService`에 주입한다. Composition의 다른 factory는 이 boundary를 만들지 않는다.

`AgentBuilderService`는 direct construction 호환을 위해 boundary 인수가 생략되면 같은 disabled
implementation을 기본값으로 사용한다. `_structure_request`는 기존 extractor 호출, schema validation과
semantic normalization sequence를 인수 없는 `planner_call` closure로 만들고 boundary의 `execute`를 통해
정확히 한 번 실행한 뒤 `structured_request`만 기존 흐름에 전달한다.

MBA-343은 production `IntentPlanningContext`를 구성하지 않고 enabled boundary도 제공하지 않는다. 따라서
이 seam은 cache lookup, hit/miss, put, diagnostic 또는 fail-open 동작을 활성화하지 않는다. Public endpoint,
request/response schema와 environment variable은 변경하지 않는다.
