"""
Database seed helpers for startup.

- Seeds a placeholder user for local/dev
- Seeds system LLM providers (idempotent)
"""

import logging
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Iterable

from apps.shared.db.models.app import App
from apps.shared.db.models.llm import LLMProvider
from apps.shared.db.models.user import User
from apps.shared.db.models.workflow import Workflow
from apps.shared.db.models.workflow_deployment import DeploymentType, WorkflowDeployment
from apps.shared.db.models.workflow_run import (
    NodeRunStatus,
    RunStatus,
    RunTriggerMode,
    WorkflowNodeRun,
    WorkflowRun,
)
from apps.shared.domain.app_auth_secret import (
    APP_AUTH_SECRET_VERIFIER_VERSION,
    app_auth_secret_verifier,
)
from apps.shared.services.password_hashing import hash_password
from sqlalchemy.orm import Session

PLACEHOLDER_USER_ID = uuid.UUID("12345678-1234-5678-1234-567812345678")
DEV_WORKFLOW_APP_IDS = {
    "template": uuid.UUID("20000000-0000-0000-0000-000000000001"),
    "llm": uuid.UUID("20000000-0000-0000-0000-000000000002"),
    "branch": uuid.UUID("20000000-0000-0000-0000-000000000003"),
}
DEV_WORKFLOW_IDS = {
    "template": uuid.UUID("21000000-0000-0000-0000-000000000001"),
    "llm": uuid.UUID("21000000-0000-0000-0000-000000000002"),
    "branch": uuid.UUID("21000000-0000-0000-0000-000000000003"),
}
DEV_DEPLOYMENT_IDS = {
    "template": uuid.UUID("22000000-0000-0000-0000-000000000001"),
    "llm": uuid.UUID("22000000-0000-0000-0000-000000000002"),
    "branch": uuid.UUID("22000000-0000-0000-0000-000000000003"),
}

logger = logging.getLogger(__name__)


def seed_placeholder_user(db: Session) -> None:
    """Ensure the dev placeholder user exists."""
    user = db.query(User).filter(User.id == PLACEHOLDER_USER_ID).first()
    if user:
        return

    dev_user = User(
        id=PLACEHOLDER_USER_ID,
        email="dev@moduly.app",
        name="Dev User",
        password=hash_password("dev-password"),
        social_provider="none",
    )
    db.add(dev_user)
    db.commit()
    logger.info(
        "✅ 기본 user유저 (id: dev@moduly.app / password: dev-password ) 생성완료!"
    )


def _default_providers() -> Iterable[LLMProvider]:
    """Return the default LLM provider rows to seed."""
    return [
        LLMProvider(
            name="openai",
            description="OpenAI default provider",
            base_url="https://api.openai.com/v1",
            type="system",
            auth_type="api_key",
            doc_url="https://platform.openai.com/api-keys",
        ),
        LLMProvider(
            name="anthropic",
            description="Anthropic Claude provider",
            base_url="https://api.anthropic.com/v1",
            type="system",
            auth_type="api_key",
            doc_url="https://console.anthropic.com/settings/keys",
        ),
        LLMProvider(
            name="google",
            description="Google Gemini provider",
            base_url="https://generativelanguage.googleapis.com/v1beta/openai",
            type="system",
            auth_type="api_key",
            doc_url="https://aistudio.google.com/",
        ),
        LLMProvider(
            name="llamaparse",
            description="LlamaParse high-quality document parser (LlamaIndex Cloud)",
            base_url="https://api.cloud.llamaindex.ai",
            type="system",
            auth_type="api_key",
            doc_url="https://cloud.llamaindex.ai/api-key",
        ),
    ]


def seed_default_llm_providers(db: Session) -> None:
    """Insert default providers if missing; idempotent per name."""
    existing_providers = db.query(LLMProvider).all()
    existing_names = {p.name for p in existing_providers}

    providers_to_add = [p for p in _default_providers() if p.name not in existing_names]
    if not providers_to_add:
        logger.warning(
            f"ℹ️ LLM providers already exist ({len(existing_providers)}). Skipping seed."
        )
        return

    db.add_all(providers_to_add)
    db.commit()
    logger.info("✅ Default LLM providers seeded!")


def seed_default_llm_models(db: Session) -> None:
    """
    KNOWN_MODEL_PRICES를 기반으로 기본 LLM 모델을 시드합니다.
    gpt-5.4, o3-mini와 같은 모델이 DB에 존재하도록 보장합니다.
    또한, 해당 모델이 UI에 표시되도록 기존 Credential과 연결합니다.
    """
    from apps.shared.db.models.llm import (
        LLMModel,
        LLMProvider,
    )
    from apps.shared.services.llm_model_pricing import (
        get_model_pricing,
        known_model_prices,
    )

    # 1. 모든 Provider 조회 후 맵핑 생성
    providers = db.query(LLMProvider).all()
    provider_map = {p.name: p for p in providers}

    # 2. 기존 생성된 모델 조회
    existing_models = db.query(LLMModel).all()
    existing_model_ids = {m.model_id_for_api_call for m in existing_models}

    # Provider 매핑 규칙 (휴리스틱)
    def get_provider_name(model_id: str) -> str:
        if model_id.startswith("claude"):
            return "anthropic"
        elif model_id.startswith("gemini"):
            return "google"
        elif model_id.startswith("llamaparse"):
            return "llamaparse"
        else:
            return "openai"  # gpt, o1, o3, dall-e, tts, whisper 등은 기본적으로 OpenAI로 처리

    models_seeded_count = 0
    models_updated_count = 0

    # 3. KNOWN_MODEL_PRICES 순회하며 모델 생성 또는 가격 업데이트
    for model_id, pricing in known_model_prices().items():
        provider_name = get_provider_name(model_id)
        provider = provider_map.get(provider_name)

        if not provider:
            continue

        # 모델 찾기 또는 생성
        model = None
        if model_id in existing_model_ids:
            # 기존 모델 객체 찾기
            model = next(
                (m for m in existing_models if m.model_id_for_api_call == model_id),
                None,
            )
            if model and (
                float(model.input_price_1k or -1) != pricing["input"]
                or float(model.output_price_1k or -1) != pricing["output"]
            ):
                model.input_price_1k = pricing["input"]
                model.output_price_1k = pricing["output"]
                db.add(model)
                models_updated_count += 1
        else:
            # 새 모델 생성
            new_model_uuid = uuid.uuid4()
            model = LLMModel(
                id=new_model_uuid,
                provider_id=provider.id,
                model_id_for_api_call=model_id,
                name=model_id,
                type="embedding" if "embedding" in model_id else "chat",
                context_window=128000
                if "gpt-4" in model_id or "o1" in model_id or "claude" in model_id
                else 8192,
                input_price_1k=pricing["input"],
                output_price_1k=pricing["output"],
                is_active=True,
            )
            db.add(model)
            models_seeded_count += 1
            # 중복 방지를 위해 캐시 업데이트
            existing_model_ids.add(model_id)
            existing_models.append(model)

        if not model:
            continue

    # Existing rows can use executable aliases such as models/gemini-... or a
    # dated provider version. Update them too, rather than only rows created by
    # this seed invocation.
    for model in existing_models:
        pricing = get_model_pricing(model.model_id_for_api_call)
        if pricing is None:
            continue
        if (
            float(model.input_price_1k or -1) != pricing.standard_input_per_1k
            or float(model.output_price_1k or -1) != pricing.standard_output_per_1k
        ):
            model.input_price_1k = pricing.standard_input_per_1k
            model.output_price_1k = pricing.standard_output_per_1k
            db.add(model)
            models_updated_count += 1

    db.flush()
    from apps.shared.services.model_routing_global_profile_catalog import (
        seed_model_routing_global_profiles,
    )

    profile_result = seed_model_routing_global_profiles(db)
    if (
        models_seeded_count > 0
        or models_updated_count > 0
        or profile_result["created"] > 0
        or profile_result["updated"] > 0
    ):
        if models_seeded_count > 0:
            logger.info(f"🌱 Seeded {models_seeded_count} new LLM models.")
        if models_updated_count > 0:
            logger.info(
                f"💰 Updated pricing for {models_updated_count} existing models."
            )
        if profile_result["created"] or profile_result["updated"]:
            logger.info(
                "🧭 Synced %s model routing catalog profiles (%s created, %s updated).",
                profile_result["created"] + profile_result["updated"],
                profile_result["created"],
                profile_result["updated"],
            )
        db.commit()
        logger.info("✅ LLM models sync complete!")
    else:
        logger.warning("ℹ️ LLM models and routing catalog profiles up to date.")


def _node(
    node_id: str,
    node_type: str,
    x: int,
    y: int,
    data: dict,
) -> dict:
    return {
        "id": node_id,
        "type": node_type,
        "position": {"x": x, "y": y},
        "data": data,
    }


def _edge(
    edge_id: str,
    source: str,
    target: str,
    source_handle: str | None = None,
    target_handle: str | None = None,
) -> dict:
    edge = {"id": edge_id, "source": source, "target": target}
    if source_handle is not None:
        edge["sourceHandle"] = source_handle
    if target_handle is not None:
        edge["targetHandle"] = target_handle
    return edge


def _base_data(
    title: str,
    description: str,
    display_number: int,
    visible_properties: list[str] | None = None,
) -> dict:
    return {
        "title": title,
        "description": description,
        "displayNumber": display_number,
        "visibleProperties": visible_properties or [],
    }


def _start_data(
    title: str,
    description: str,
    display_number: int,
    variables: list[dict],
) -> dict:
    data = _base_data(title, description, display_number)
    # 프론트는 triggerType, 엔진은 trigger_type을 사용하므로 dev seed에는 둘 다 둔다.
    data.update(
        {
            "triggerType": "manual",
            "trigger_type": "manual",
            "variables": variables,
        }
    )
    return data


def _input_variable(
    variable_id: str,
    name: str,
    label: str,
    variable_type: str,
    required: bool = True,
    **extra,
) -> dict:
    return {
        "id": variable_id,
        "name": name,
        "label": label,
        "type": variable_type,
        "required": required,
        **extra,
    }


def _template_quick_reply_graph() -> dict:
    nodes = [
        _node(
            "start-customer",
            "startNode",
            0,
            120,
            _start_data(
                "고객 문의 입력",
                "고객 문의와 우선순위를 입력받습니다.",
                1,
                [
                    _input_variable(
                        "customer_message",
                        "customer_message",
                        "고객 문의",
                        "paragraph",
                        maxLength=1000,
                        max_length=1000,
                    ),
                    _input_variable(
                        "priority",
                        "priority",
                        "우선순위",
                        "select",
                        options=[
                            {"label": "일반", "value": "normal"},
                            {"label": "긴급", "value": "urgent"},
                        ],
                    ),
                ],
            ),
        ),
        _node(
            "template-reply",
            "templateNode",
            420,
            120,
            {
                **_base_data(
                    "응답 초안 생성",
                    "문의와 우선순위를 조합해 답변 초안을 만듭니다.",
                    2,
                    ["template", "variables"],
                ),
                "template": (
                    "문의 요약:\n"
                    "- 고객 문의: {{ customer_message }}\n"
                    "- 우선순위: {{ priority }}\n\n"
                    "답변 초안:\n"
                    "안녕하세요. 문의 주신 내용을 확인했습니다. "
                    "우선순위는 {{ priority }}로 접수되었고, 담당자가 순차적으로 확인하겠습니다."
                ),
                "variables": [
                    {
                        "name": "customer_message",
                        "value_selector": ["start-customer", "customer_message"],
                    },
                    {
                        "name": "priority",
                        "value_selector": ["start-customer", "priority"],
                    },
                ],
            },
        ),
        _node(
            "answer-reply",
            "answerNode",
            840,
            120,
            {
                **_base_data("최종 응답", "템플릿 결과를 최종 출력합니다.", 3),
                "outputs": [
                    {
                        "variable": "reply",
                        "label": "답변 초안",
                        "value_selector": ["template-reply", "text"],
                    }
                ],
            },
        ),
    ]
    return {
        "nodes": nodes,
        "edges": [
            _edge("edge-start-template", "start-customer", "template-reply"),
            _edge("edge-template-answer", "template-reply", "answer-reply"),
        ],
        "viewport": {"x": 120, "y": 80, "zoom": 0.9},
    }


def _llm_intent_graph() -> dict:
    nodes = [
        _node(
            "start-inquiry",
            "startNode",
            0,
            120,
            _start_data(
                "문의 입력",
                "LLM 의도 분류에 사용할 고객 문의를 입력받습니다.",
                1,
                [
                    _input_variable(
                        "customer_message",
                        "customer_message",
                        "고객 문의",
                        "paragraph",
                        maxLength=1200,
                        max_length=1200,
                    )
                ],
            ),
        ),
        _node(
            "llm-intent",
            "llmNode",
            420,
            120,
            {
                **_base_data(
                    "문의 의도 분석",
                    "고객 문의의 의도와 감정을 분류합니다.",
                    2,
                    ["model_id", "user_prompt"],
                ),
                "provider": "openai",
                "model_id": "gpt-5.4-mini",
                "fallback_model_id": "gpt-5.4",
                "system_prompt": "고객센터 문의를 분류하는 상담 운영 도우미입니다.",
                "user_prompt": (
                    "다음 고객 문의를 읽고 의도, 감정, 긴급도를 JSON으로 답하세요.\n"
                    "고객 문의: {{ customer_message }}"
                ),
                "assistant_prompt": "",
                "referenced_variables": [
                    {
                        "name": "customer_message",
                        "value_selector": ["start-inquiry", "customer_message"],
                    }
                ],
                "context_variable": "",
                "parameters": {"temperature": 0.2, "max_tokens": 500},
                "knowledgeBases": [],
            },
        ),
        _node(
            "answer-intent",
            "answerNode",
            840,
            120,
            {
                **_base_data("분석 결과", "LLM 분석 결과를 최종 출력합니다.", 3),
                "outputs": [
                    {
                        "variable": "analysis",
                        "label": "의도 분석",
                        "value_selector": ["llm-intent", "text"],
                    }
                ],
            },
        ),
    ]
    return {
        "nodes": nodes,
        "edges": [
            _edge("edge-start-llm", "start-inquiry", "llm-intent"),
            _edge("edge-llm-answer", "llm-intent", "answer-intent"),
        ],
        "viewport": {"x": 120, "y": 80, "zoom": 0.9},
    }


def _priority_branch_graph() -> dict:
    nodes = [
        _node(
            "start-priority",
            "startNode",
            0,
            160,
            _start_data(
                "문의 입력",
                "고객 문의와 우선순위를 입력받아 분기합니다.",
                1,
                [
                    _input_variable(
                        "customer_message",
                        "customer_message",
                        "고객 문의",
                        "paragraph",
                        maxLength=1000,
                        max_length=1000,
                    ),
                    _input_variable(
                        "priority",
                        "priority",
                        "우선순위",
                        "select",
                        options=[
                            {"label": "일반", "value": "normal"},
                            {"label": "긴급", "value": "urgent"},
                        ],
                    ),
                ],
            ),
        ),
        _node(
            "condition-priority",
            "conditionNode",
            420,
            160,
            {
                **_base_data(
                    "긴급 여부 분기",
                    "우선순위가 urgent면 긴급 응답으로 분기합니다.",
                    2,
                    ["cases"],
                ),
                "cases": [
                    {
                        "id": "urgent",
                        "case_name": "긴급",
                        "logical_operator": "and",
                        "conditions": [
                            {
                                "id": "cond-priority-urgent",
                                "variable_selector": ["start-priority", "priority"],
                                "operator": "equals",
                                "value": "urgent",
                            }
                        ],
                    }
                ],
            },
        ),
        _node(
            "template-urgent",
            "templateNode",
            840,
            40,
            {
                **_base_data("긴급 응답", "긴급 문의용 답변을 생성합니다.", 3),
                "template": (
                    "[긴급 접수]\n"
                    "문의 내용: {{ customer_message }}\n"
                    "담당자에게 즉시 전달하고 우선 처리하겠습니다."
                ),
                "variables": [
                    {
                        "name": "customer_message",
                        "value_selector": ["start-priority", "customer_message"],
                    }
                ],
            },
        ),
        _node(
            "template-default",
            "templateNode",
            840,
            300,
            {
                **_base_data("일반 응답", "일반 문의용 답변을 생성합니다.", 4),
                "template": (
                    "[일반 접수]\n"
                    "문의 내용: {{ customer_message }}\n"
                    "접수 순서에 따라 확인 후 답변드리겠습니다."
                ),
                "variables": [
                    {
                        "name": "customer_message",
                        "value_selector": ["start-priority", "customer_message"],
                    }
                ],
            },
        ),
        _node(
            "answer-urgent",
            "answerNode",
            1260,
            40,
            {
                **_base_data("긴급 최종 응답", "긴급 분기 출력을 반환합니다.", 5),
                "outputs": [
                    {
                        "variable": "reply",
                        "label": "답변",
                        "value_selector": ["template-urgent", "text"],
                    }
                ],
            },
        ),
        _node(
            "answer-default",
            "answerNode",
            1260,
            300,
            {
                **_base_data("일반 최종 응답", "기본 분기 출력을 반환합니다.", 6),
                "outputs": [
                    {
                        "variable": "reply",
                        "label": "답변",
                        "value_selector": ["template-default", "text"],
                    }
                ],
            },
        ),
    ]
    return {
        "nodes": nodes,
        "edges": [
            _edge("edge-start-condition", "start-priority", "condition-priority"),
            _edge(
                "edge-condition-urgent",
                "condition-priority",
                "template-urgent",
                source_handle="urgent",
            ),
            _edge(
                "edge-condition-default",
                "condition-priority",
                "template-default",
                source_handle="default",
            ),
            _edge("edge-urgent-answer", "template-urgent", "answer-urgent"),
            _edge("edge-default-answer", "template-default", "answer-default"),
        ],
        "viewport": {"x": 80, "y": 40, "zoom": 0.75},
    }


def _extract_input_schema(graph: dict) -> dict | None:
    for node in graph.get("nodes", []):
        if node.get("type") != "startNode":
            continue
        variables = node.get("data", {}).get("variables", [])
        return {
            "variables": [
                {
                    "name": variable["name"],
                    "type": variable.get("type", "text"),
                    "label": variable.get("label", variable["name"]),
                }
                for variable in variables
                if variable.get("name")
            ]
        }
    return None


def _extract_output_schema(graph: dict) -> dict | None:
    outputs = []
    for node in graph.get("nodes", []):
        if node.get("type") != "answerNode":
            continue
        for output in node.get("data", {}).get("outputs", []):
            if output.get("variable"):
                outputs.append(
                    {
                        "variable": output["variable"],
                        "label": output.get("label", output["variable"]),
                    }
                )
    return {"outputs": outputs} if outputs else None


def _upsert_dev_app_workflow(
    db: Session,
    key: str,
    name: str,
    description: str,
    icon: str,
    graph: dict,
) -> Workflow:
    app_id = DEV_WORKFLOW_APP_IDS[key]
    workflow_id = DEV_WORKFLOW_IDS[key]
    deployment_id = DEV_DEPLOYMENT_IDS[key]

    app = db.query(App).filter(App.id == app_id).first()
    if not app:
        app_secret = f"sk-dev-{key}"
        app = App(
            id=app_id,
            tenant_id=PLACEHOLDER_USER_ID,
            name=name,
            description=description,
            icon={
                "type": "emoji",
                "content": icon,
                "background_color": "#EFF6FF",
            },
            url_slug=f"dev-{key}-workflow",
            auth_secret=None,
            auth_secret_verifier=app_auth_secret_verifier(app_secret),
            auth_secret_verifier_version=APP_AUTH_SECRET_VERIFIER_VERSION,
            auth_secret_generation=1,
            auth_secret_rotated_at=datetime.now(timezone.utc),
            is_market=False,
            created_by=PLACEHOLDER_USER_ID,
        )
        db.add(app)
        db.flush()
    else:
        app.name = name
        app.description = description
        app.icon = {
            "type": "emoji",
            "content": icon,
            "background_color": "#EFF6FF",
        }

    workflow = db.query(Workflow).filter(Workflow.id == workflow_id).first()
    if not workflow:
        workflow = Workflow(
            id=workflow_id,
            tenant_id=PLACEHOLDER_USER_ID,
            app_id=app.id,
            created_by=PLACEHOLDER_USER_ID,
        )
        db.add(workflow)
        db.flush()

    workflow.app_id = app.id
    workflow.tenant_id = PLACEHOLDER_USER_ID
    workflow.created_by = PLACEHOLDER_USER_ID
    workflow.updated_by = PLACEHOLDER_USER_ID
    workflow.graph = graph
    workflow.features = {}
    workflow.env_variables = []
    workflow.runtime_variables = []
    app.workflow_id = workflow.id

    deployment = db.query(WorkflowDeployment).filter(
        WorkflowDeployment.id == deployment_id
    ).first()
    if not deployment:
        deployment = WorkflowDeployment(
            id=deployment_id,
            app_id=app.id,
            version=1,
            type=DeploymentType.API,
            graph_snapshot=graph,
            created_by=PLACEHOLDER_USER_ID,
            is_active=True,
        )
        db.add(deployment)

    deployment.app_id = app.id
    deployment.version = 1
    deployment.type = DeploymentType.API
    deployment.graph_snapshot = graph
    deployment.config = {"dev_seed": True}
    deployment.input_schema = _extract_input_schema(graph)
    deployment.output_schema = _extract_output_schema(graph)
    deployment.description = "Dev seed deployment"
    deployment.created_by = PLACEHOLDER_USER_ID
    deployment.is_active = True
    app.active_deployment_id = deployment.id

    return workflow


def _seed_template_run_logs(db: Session, workflow: Workflow) -> None:
    db.query(WorkflowRun).filter(WorkflowRun.workflow_id == workflow.id).delete(
        synchronize_session=False
    )

    now = datetime.now(timezone.utc)
    run_specs = [
        ("normal", RunStatus.SUCCESS, "normal", 0.0012, 0, None),
        ("urgent", RunStatus.SUCCESS, "urgent", 0.0015, 0, None),
        (
            "missing",
            RunStatus.FAILED,
            "",
            0,
            0,
            "변수 'priority' 값이 비어 있습니다.",
        ),
    ]

    for index, (suffix, status, priority, cost, tokens, error) in enumerate(run_specs):
        started_at = now - timedelta(days=index + 1, minutes=index * 11)
        run = WorkflowRun(
            workflow_id=workflow.id,
            user_id=PLACEHOLDER_USER_ID,
            deployment_id=DEV_DEPLOYMENT_IDS["template"],
            workflow_version=1,
            status=status,
            trigger_mode=RunTriggerMode.MANUAL,
            inputs={
                "customer_message": f"배송 상태를 확인하고 싶습니다. ({suffix})",
                "priority": priority,
            },
            outputs=None
            if status == RunStatus.FAILED
            else {
                "reply": f"문의 요약:\n- 고객 문의: 배송 상태를 확인하고 싶습니다. ({suffix})\n- 우선순위: {priority}"
            },
            error_message=error,
            started_at=started_at,
            finished_at=started_at + timedelta(seconds=1.2 + index),
            duration=1.2 + index,
            meta_info={"seed": "dev_workflows"},
            total_tokens=tokens,
            total_cost=Decimal(str(cost)),
        )
        db.add(run)
        db.flush()

        node_status = (
            NodeRunStatus.FAILED
            if status == RunStatus.FAILED
            else NodeRunStatus.SUCCESS
        )
        db.add_all(
            [
                WorkflowNodeRun(
                    workflow_run_id=run.id,
                    node_id="start-customer",
                    node_type="startNode",
                    status=NodeRunStatus.SUCCESS,
                    inputs=run.inputs,
                    process_data={},
                    outputs=run.inputs,
                    started_at=started_at,
                    finished_at=started_at + timedelta(milliseconds=80),
                ),
                WorkflowNodeRun(
                    workflow_run_id=run.id,
                    node_id="template-reply",
                    node_type="templateNode",
                    status=node_status,
                    inputs={"start-customer": run.inputs},
                    process_data={},
                    outputs=run.outputs,
                    error_message=error,
                    started_at=started_at + timedelta(milliseconds=100),
                    finished_at=started_at + timedelta(milliseconds=300),
                ),
            ]
        )


def seed_dev_workflow_examples(db: Session) -> None:
    """
    Seed local/dev workflow examples for real backend testing.

    - Does not create credentials or API keys.
    - Idempotently updates the same fixed apps/workflows/deployments.
    - Adds lightweight run logs for monitoring UI.
    """

    seed_placeholder_user(db)
    seed_default_llm_providers(db)
    seed_default_llm_models(db)

    template_workflow = _upsert_dev_app_workflow(
        db,
        key="template",
        name="[DEV] 템플릿 응답 워크플로우",
        description="credentials 없이 실행 가능한 Start → Template → Answer 예제",
        icon="🧩",
        graph=_template_quick_reply_graph(),
    )
    _upsert_dev_app_workflow(
        db,
        key="llm",
        name="[DEV] LLM 문의 의도 분석",
        description="사용자가 OpenAI credentials를 채운 뒤 실제 LLM 호출을 확인하는 예제",
        icon="🤖",
        graph=_llm_intent_graph(),
    )
    _upsert_dev_app_workflow(
        db,
        key="branch",
        name="[DEV] 우선순위 분기 워크플로우",
        description="Start → Condition → Template → Answer 분기 실행 예제",
        icon="🔀",
        graph=_priority_branch_graph(),
    )
    _seed_template_run_logs(db, template_workflow)

    db.commit()
    logger.info("✅ Dev workflow examples seeded.")
