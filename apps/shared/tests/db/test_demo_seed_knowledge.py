import gzip
import json
import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from apps.shared.db import demo_seed
from apps.shared.db.models.conversation_memory import ConversationSessionRecord
from apps.shared.db.models.workflow_run import RunStatus
from apps.shared.domain.app_auth_secret import (
    APP_AUTH_SECRET_VERIFIER_VERSION,
    app_auth_secret_verifier,
)
from apps.shared.domain.workflow_graph import validate_workflow_graph
from apps.shared.services.knowledge_safe_text import safe_label_from_text
from apps.shared.services.workflow_layout import calculate_workflow_auto_layout
from apps.workflow_engine.workflow.nodes.webhook.entities import WebhookTriggerNodeData
from apps.workflow_engine.workflow.nodes.webhook.webhook_node import WebhookTriggerNode
from scripts import seed_demo as seed_demo_script


class FakeSchemaInspector:
    def __init__(self, columns_by_table, *, alembic_revisions=None):
        self.columns_by_table = columns_by_table
        self.bind = FakeAlembicBind(alembic_revisions or [])

    def has_table(self, table_name):
        return table_name in self.columns_by_table

    def get_columns(self, table_name):
        return [{"name": name} for name in self.columns_by_table[table_name]]


class FakeAlembicBind:
    def __init__(self, revisions):
        self.revisions = revisions

    def execute(self, _stmt):
        return self

    def fetchall(self):
        return [(revision,) for revision in self.revisions]


class FailingSchemaInspector:
    def has_table(self, table_name):
        raise RuntimeError("secret raw database failure")


class ResetRecorderQuery:
    def __init__(self, session, target):
        self.session = session
        self.target = target

    def filter(self, *criteria):
        self.session.filter_criteria.setdefault(self.target, []).extend(criteria)
        return self

    def all(self):
        return []

    def delete(self, **_kwargs):
        self.session.deleted_targets.append(self.target)

    def update(self, *_args, **_kwargs):
        return None


class ResetRecorderSession:
    def __init__(self):
        self.deleted_targets = []
        self.filter_criteria = {}

    def query(self, target):
        return ResetRecorderQuery(self, target)

    def flush(self):
        return None

    def commit(self):
        return None


def write_minimal_demo_fixture(
    fixture_path,
    *,
    omitted_document_key=None,
    malformed_embedding_key=None,
):
    embedding = [0.0] * demo_seed.DEMO_EMBEDDING_DIMENSION
    specs = [
        spec
        for spec in demo_seed.INDEXED_DEMO_DOCUMENT_SPECS
        if spec.key != omitted_document_key
    ]
    with gzip.open(fixture_path, "wt", encoding="utf-8", newline="\n") as handle:
        handle.write(
            json.dumps(
                {
                    "record_type": "header",
                    "fixture_version": demo_seed.DEMO_SEED_VERSION,
                    "embedding_model": demo_seed.DEMO_EMBEDDING_MODEL,
                    "embedding_dimension": demo_seed.DEMO_EMBEDDING_DIMENSION,
                    "document_keys": [spec.key for spec in specs],
                },
                ensure_ascii=False,
            )
        )
        handle.write("\n")
        for spec in specs:
            handle.write(
                json.dumps(
                    {
                        "record_type": "document",
                        "key": spec.key,
                        "filename": spec.filename,
                        "content_hash": f"hash-{spec.key}",
                        "chunking_mode": "flat",
                        "chunking_fingerprint_hash": f"fingerprint-{spec.key}",
                    },
                    ensure_ascii=False,
                )
            )
            handle.write("\n")
            handle.write(
                json.dumps(
                    {
                        "record_type": "chunk",
                        "document_key": spec.key,
                        "chunk_index": 0,
                        "content": "fixture chunk",
                        "embedding": [0.0]
                        if spec.key == malformed_embedding_key
                        else embedding,
                        "token_count": 2,
                        "metadata": {},
                        "chunk_level": "flat",
                    },
                    ensure_ascii=False,
                )
            )
            handle.write("\n")


def test_demo_seed_embedding_uses_shared_guarded_openai_client(monkeypatch):
    captured = {}

    class FakeOpenAIClient:
        def __init__(self, *, model_id, credentials):
            captured["model_id"] = model_id
            captured["credentials"] = credentials

        def embed_batch_sync(self, texts):
            captured.setdefault("batches", []).append(tuple(texts))
            return [
                [float(index)] * demo_seed.DEMO_EMBEDDING_DIMENSION
                for index, _text in enumerate(texts)
            ]

    monkeypatch.setenv("OPENAI_API_KEY", "test-placeholder")
    monkeypatch.setattr(demo_seed, "OpenAIClient", FakeOpenAIClient)

    embeddings = demo_seed._embed_text_batches(["first", "", "third"])

    assert captured["model_id"] == demo_seed.DEMO_EMBEDDING_MODEL
    assert captured["credentials"]["baseUrl"] == demo_seed.DEMO_OPENAI_BASE_URL
    assert captured["batches"] == [("first", " ", "third")]
    assert len(embeddings) == 3
    assert all(
        len(embedding) == demo_seed.DEMO_EMBEDDING_DIMENSION
        for embedding in embeddings
    )


def test_resolve_legal_pdf_picks_latest_effective_date(tmp_path, monkeypatch):
    old_pdf = tmp_path / "채용절차의 공정화에 관한 법률(법률)(제12326호)(20140121).pdf"
    latest_pdf = (
        tmp_path / "채용절차의 공정화에 관한 법률(법률)(제17326호)(20200526).pdf"
    )
    old_pdf.write_bytes(b"%PDF-1.4\n")
    latest_pdf.write_bytes(b"%PDF-1.4\n")
    monkeypatch.setattr(demo_seed, "DEMO_LEGAL_DOCS_LABOR_DIR", tmp_path)

    spec = next(
        item
        for item in demo_seed.LEGAL_DOCUMENT_SPECS
        if item.key == "legal_fair_hiring"
    )

    assert demo_seed._resolve_legal_pdf(spec) == latest_pdf


def test_resolve_onboarding_pdf_uses_bundled_demodata(tmp_path, monkeypatch):
    source = tmp_path / "platform_team_onboarding_v4.pdf"
    source.write_bytes(b"%PDF-1.4\n")
    monkeypatch.setattr(demo_seed, "DEMO_ONBOARDING_PDF_DIR", tmp_path)
    spec = next(
        item
        for item in demo_seed.ONBOARDING_PDF_SPECS
        if item.key == "onboarding_platform"
    )

    assert demo_seed._resolve_onboarding_pdf(spec) == source


def test_copy_onboarding_pdf_excludes_manager_only_pages(tmp_path, monkeypatch):
    fitz = pytest.importorskip("fitz")
    source = tmp_path / "platform_team_onboarding_v4.pdf"
    with fitz.open() as document:
        for page_number in range(1, 5):
            page = document.new_page()
            page.insert_text((72, 72), f"page-{page_number}")
        document.save(source)

    upload_dir = tmp_path / "uploads"
    monkeypatch.setattr(demo_seed, "DEMO_UPLOAD_DIRS", (upload_dir,))
    spec = next(
        item
        for item in demo_seed.ONBOARDING_PDF_SPECS
        if item.key == "onboarding_platform"
    )

    copied_path = Path(demo_seed._copy_onboarding_pdf(spec, source))

    with fitz.open(copied_path) as copied:
        assert copied.page_count == 3
        copied_text = "\n".join(page.get_text() for page in copied)
    assert "page-3" in copied_text
    assert "page-4" not in copied_text


def test_hr_bot_graph_references_seeded_rag_kbs():
    graph = demo_seed._hr_bot_graph()
    llm_node = next(node for node in graph["nodes"] if node["id"] == "llm-answer")
    kb_ids = {item["id"] for item in llm_node["data"]["knowledgeBases"]}

    assert llm_node["data"]["model_id"] == demo_seed.DEMO_CHAT_MINI_MODEL
    assert llm_node["data"]["scoreThreshold"] == 0.3
    assert llm_node["data"]["topK"] == 4
    assert str(demo_seed.KB_IDS["hr"]) in kb_ids
    assert str(demo_seed.KB_IDS["hr_welfare"]) in kb_ids
    assert str(demo_seed.KB_IDS["legal_labor_standards"]) in kb_ids
    assert str(demo_seed.KB_IDS["legal_equal_employment"]) in kb_ids
    assert kb_ids.isdisjoint(
        str(kb_id)
        for kb_id in demo_seed.RETIRED_INTERNAL_DOCUMENT_KB_IDS.values()
    )
    assert "knowledgeCollections" not in llm_node["data"]


def test_legacy_demo_documents_have_distinct_knowledge_base_keys():
    mapping = demo_seed.LEGACY_DEMO_DOCUMENT_KB_KEYS

    assert set(mapping) == {"hr_leave", "hr_welfare", "finance_sensitive"}
    assert len(set(mapping.values())) == len(mapping)
    assert mapping["hr_leave"] == "hr"
    assert mapping["hr_welfare"] == "hr_welfare"
    assert all(kb_key in demo_seed.KB_IDS for kb_key in mapping.values())


def test_demo_upsert_preserves_valid_rotated_app_secret_state_without_reset():
    now = datetime(2026, 7, 18, tzinfo=timezone.utc)
    existing = SimpleNamespace(
        auth_secret="legacy-raw-value",
        auth_secret_verifier=app_auth_secret_verifier("current-value"),
        auth_secret_verifier_version=APP_AUTH_SECRET_VERIFIER_VERSION,
        auth_secret_generation=4,
        auth_secret_previous_verifier=app_auth_secret_verifier("previous-value"),
        auth_secret_previous_verifier_version=APP_AUTH_SECRET_VERIFIER_VERSION,
        auth_secret_previous_valid_until=now + timedelta(minutes=5),
        auth_secret_rotated_at=now,
    )
    seed_values = {
        "name": "데모 App",
        "auth_secret": None,
        "auth_secret_verifier": app_auth_secret_verifier("seed-value"),
        "auth_secret_verifier_version": APP_AUTH_SECRET_VERIFIER_VERSION,
        "auth_secret_generation": 1,
        "auth_secret_previous_verifier": None,
        "auth_secret_previous_verifier_version": None,
        "auth_secret_previous_valid_until": None,
        "auth_secret_rotated_at": now,
    }

    values = demo_seed._app_seed_values(existing, seed_values)

    assert values["name"] == "데모 App"
    assert values["auth_secret"] is None
    assert all(
        field not in values for field in demo_seed._MANAGED_APP_SECRET_STATE_FIELDS
    )
    assert demo_seed._app_seed_values(None, seed_values) == seed_values


def test_test_profile_seed_preserves_valid_rotated_app_secret_state_without_reset(
    monkeypatch,
):
    now = datetime(2026, 7, 18, tzinfo=timezone.utc)
    existing = SimpleNamespace(
        auth_secret="legacy-raw-value",
        auth_secret_verifier=app_auth_secret_verifier("current-value"),
        auth_secret_verifier_version=APP_AUTH_SECRET_VERIFIER_VERSION,
        auth_secret_generation=4,
        auth_secret_previous_verifier=app_auth_secret_verifier("previous-value"),
        auth_secret_previous_verifier_version=APP_AUTH_SECRET_VERIFIER_VERSION,
        auth_secret_previous_valid_until=now + timedelta(minutes=5),
        auth_secret_rotated_at=now,
    )
    captured_app_values = []

    class FakeSession:
        def get(self, model, row_id):
            if model is demo_seed.App and row_id == demo_seed.TEST_APP_ID:
                return existing
            return None

        def flush(self):
            return None

        def commit(self):
            return None

    def capture_upsert(_db, model, row_id, values):
        if model is demo_seed.App and row_id == demo_seed.TEST_APP_ID:
            captured_app_values.append(values)
        return SimpleNamespace(id=row_id)

    monkeypatch.setattr(demo_seed, "_adopt_existing_test_user_ids", lambda _db: None)
    monkeypatch.setattr(demo_seed, "hash_password", lambda _value: "hashed")
    monkeypatch.setattr(demo_seed, "_upsert_by_id", capture_upsert)

    demo_seed.seed_test_data(FakeSession())

    assert len(captured_app_values) == 1
    values = captured_app_values[0]
    assert values["name"] == "테스트용 기능 검증 워크플로우"
    assert values["auth_secret"] is None
    assert all(
        field not in values for field in demo_seed._MANAGED_APP_SECRET_STATE_FIELDS
    )


def test_hr_policy_collection_references_legacy_policy_kbs():
    target_keys = {
        knowledge_base_key
        for _, knowledge_base_key in demo_seed.HR_POLICY_COLLECTION_ITEMS
    }

    assert target_keys == {"hr", "hr_welfare"}
    assert target_keys <= set(demo_seed.KB_IDS)


def test_department_onboarding_graph_references_exact_rbac_demo_kbs():
    graph = demo_seed._department_onboarding_chatbot_graph()
    llm_node = next(node for node in graph["nodes"] if node["id"] == "llm-answer")

    assert llm_node["data"]["model_id"] == demo_seed.DEMO_CHAT_MINI_MODEL
    assert llm_node["data"]["scoreThreshold"] == 0.3
    assert llm_node["data"]["topK"] == 3
    assert llm_node["data"]["answerGroundingCheck"] == "basic"
    assert [item["id"] for item in llm_node["data"]["knowledgeBases"]] == [
        str(demo_seed.KB_IDS["onboarding_platform"]),
        str(demo_seed.KB_IDS["onboarding_sales"]),
    ]


def test_department_onboarding_users_belong_to_only_their_department_team():
    specs = {spec.key: spec for spec in demo_seed.USER_SPECS}

    assert specs["developer"].email == "dev@nodease.demo"
    assert specs["developer"].teams == ("department_development",)
    assert specs["developer"].membership_state == demo_seed.ORGANIZATION_MEMBERSHIP_ACTIVE
    assert specs["planning"].email == "planning@nodease.demo"
    assert specs["planning"].teams == ("department_planning",)
    assert specs["planning"].membership_state == demo_seed.ORGANIZATION_MEMBERSHIP_ACTIVE
    assert demo_seed.USER_IDS["developer"] != demo_seed.USER_IDS["planning"]
    assert demo_seed.TEAM_IDS["department_development"] != demo_seed.TEAM_IDS[
        "department_planning"
    ]


def test_department_onboarding_knowledge_permissions_are_fail_closed_by_team():
    department_teams = {"department_development", "department_planning"}
    department_specs = {
        (kb_key, team_key, auth_state)
        for kb_key, team_key, auth_state in demo_seed._demo_team_knowledge_permission_specs()
        if team_key in department_teams
    }

    assert department_specs == {
        (
            "onboarding_platform",
            "department_development",
            "operator",
        ),
        (
            "onboarding_sales",
            "department_planning",
            "operator",
        ),
    }


def test_team_onboarding_access_control_users_match_presentation_scenario():
    specs = {spec.key: spec for spec in demo_seed.USER_SPECS}

    assert specs["onboarding_platform_rookie"].email == "seoyeon.kim@nodease.demo"
    assert specs["onboarding_platform_rookie"].name == "김서연"
    assert specs["onboarding_platform_rookie"].teams == ("onboarding_platform",)
    assert specs["onboarding_sales_rookie"].email == "junho.lee@nodease.demo"
    assert specs["onboarding_sales_rookie"].name == "이준호"
    assert specs["onboarding_sales_rookie"].teams == ("onboarding_sales",)
    assert specs["onboarding_people_manager"].email == "jimin.park@nodease.demo"
    assert specs["onboarding_people_manager"].name == "박지민"
    assert specs["onboarding_people_manager"].teams == ("onboarding_people",)
    assert (
        specs["onboarding_people_manager"].organization_auth_state
        == demo_seed.ORGANIZATION_AUTH_MANAGER
    )


def test_team_onboarding_access_control_kbs_use_bundled_pdf_specs():
    specs = {spec.key: spec for spec in demo_seed.ONBOARDING_PDF_SPECS}

    expected_safe_labels = {
        "onboarding_platform": "온보딩 문서: 플랫폼개발팀",
        "onboarding_sales": "온보딩 문서: 영업팀",
        "onboarding_finance": "온보딩 문서: 재무팀",
    }
    expected_filenames = {
        "onboarding_platform": "platform_team_onboarding_v4.pdf",
        "onboarding_sales": "sales_team_onboarding_v2.pdf",
        "onboarding_finance": "finance_team_onboarding_v3.pdf",
    }
    assert "onboarding_company_common" not in demo_seed.KB_IDS
    assert "onboarding_company_common" not in demo_seed.DOCUMENT_IDS
    assert "onboarding_company_common" not in demo_seed.COLLECTION_ITEM_IDS
    assert "onboarding_company_common" in demo_seed.RETIRED_INTERNAL_DOCUMENT_KB_IDS
    assert "onboarding_company_common" in demo_seed.RETIRED_INTERNAL_DOCUMENT_IDS
    assert (
        "onboarding_company_common"
        in demo_seed.RETIRED_INTERNAL_DOCUMENT_COLLECTION_ITEM_IDS
    )
    assert {key: spec.filename for key, spec in specs.items()} == expected_filenames
    assert {key: spec.name for key, spec in specs.items()} == expected_safe_labels
    assert {
        key: safe_label_from_text(spec.name) for key, spec in specs.items()
    } == expected_safe_labels
    assert specs["onboarding_platform"].source_page_indexes == (0, 1, 2)
    assert all(
        (demo_seed.DEMO_ONBOARDING_PDF_DIR / spec.filename).is_file()
        for spec in specs.values()
    )
    assert not set(specs).intersection(
        spec.key for spec in demo_seed.DEMO_DOCUMENT_SPECS
    )


def test_team_onboarding_kb_seed_persists_safe_kb_name_labels(monkeypatch):
    expected_by_id = {
        demo_seed.KB_IDS[spec.key]: spec.name
        for spec in demo_seed.ONBOARDING_PDF_SPECS
    }
    captured = {}

    class SeedCaptured(Exception):
        pass

    def capture_upsert(_db, model, object_id, values):
        if model is not demo_seed.KnowledgeBase or object_id not in expected_by_id:
            return None
        captured[object_id] = values
        if len(captured) == len(expected_by_id):
            raise SeedCaptured
        return None

    monkeypatch.setattr(demo_seed, "_demo_knowledge_fixture_or_none", lambda: None)
    monkeypatch.setattr(demo_seed, "_upsert_by_id", capture_upsert)

    with pytest.raises(SeedCaptured):
        demo_seed._seed_knowledge(ResetRecorderSession())

    assert {
        object_id: values["safe_metadata"]["safe_label"]
        for object_id, values in captured.items()
    } == expected_by_id


def test_onboarding_pdf_indexing_requires_a_seed_openai_key_mode(monkeypatch):
    monkeypatch.delenv(demo_seed.DEMO_REGENERATE_KNOWLEDGE_FIXTURE_ENV, raising=False)
    monkeypatch.delenv(demo_seed.DEMO_ENABLE_RUNTIME_OPENAI_CREDENTIAL_ENV, raising=False)
    assert demo_seed._should_index_onboarding_pdfs() is False

    monkeypatch.setenv(demo_seed.DEMO_ENABLE_RUNTIME_OPENAI_CREDENTIAL_ENV, "1")
    assert demo_seed._should_index_onboarding_pdfs() is True


def test_team_onboarding_access_control_permissions_are_fail_closed_by_team():
    scenario_teams = {
        "onboarding_platform",
        "onboarding_sales",
        "onboarding_people",
        "onboarding_finance",
    }
    scenario_kbs = {spec.key for spec in demo_seed.ONBOARDING_PDF_SPECS}
    actual = {
        (kb_key, team_key, auth_state)
        for kb_key, team_key, auth_state in demo_seed._demo_team_knowledge_permission_specs()
        if kb_key in scenario_kbs and team_key in scenario_teams
    }

    assert actual == {
        ("onboarding_platform", "onboarding_platform", "operator"),
        ("onboarding_platform", "onboarding_people", "manager"),
        ("onboarding_sales", "onboarding_sales", "operator"),
        ("onboarding_sales", "onboarding_people", "manager"),
        ("onboarding_finance", "onboarding_finance", "operator"),
        ("onboarding_finance", "onboarding_people", "manager"),
    }


def test_team_onboarding_access_control_graph_references_bundled_pdf_kbs():
    graph = demo_seed._team_onboarding_access_control_graph()
    llm_node = next(node for node in graph["nodes"] if node["id"] == "llm-answer")

    assert [item["id"] for item in llm_node["data"]["knowledgeBases"]] == [
        str(demo_seed.KB_IDS[key])
        for key in (spec.key for spec in demo_seed.ONBOARDING_PDF_SPECS)
    ]
    assert "추측하지" in llm_node["data"]["system_prompt"]


def test_team_onboarding_adaptive_routing_demo_only_changes_routing_settings():
    """라우팅 실험 workflow는 원본 RAG/프롬프트/생성 파라미터를 그대로 복제한다."""
    source = demo_seed._team_onboarding_access_control_graph()
    experiment = demo_seed._team_onboarding_adaptive_routing_graph()
    source_llm = next(node for node in source["nodes"] if node["id"] == "llm-answer")
    experiment_llm = next(
        node for node in experiment["nodes"] if node["id"] == "llm-answer"
    )
    source_data = source_llm["data"]
    experiment_data = experiment_llm["data"]

    assert experiment_data["knowledgeBases"] == source_data["knowledgeBases"]
    assert experiment_data["system_prompt"] == source_data["system_prompt"]
    assert experiment_data["user_prompt"] == source_data["user_prompt"]
    assert experiment_data["parameters"] == source_data["parameters"]
    assert experiment_data["model_id"] == "gpt-4.1"
    assert experiment_data["auto_model_routing"] is True
    assert experiment_data["model_routing_context"] == {
        "customer_facing": False,
        "node_task": "employee_onboarding_guidance",
        "risk_level": "low",
    }
    policy = experiment_data["model_routing_policy"]
    assert policy["refresh"]["refresh_every_runs"] == 5
    assert policy["validation_budget_usd"] == 3.0
    assert policy["excluded_model_ids"] == ["gpt-5.6-sol"]


def test_enterprise_request_routing_graph_uses_current_routing_context():
    """통합 업무 요청 workflow는 입력군 없이 현재 난이도 라우팅 설정을 제공한다."""
    graph = demo_seed._enterprise_request_routing_graph()
    llm_node = next(node for node in graph["nodes"] if node["id"] == "llm-request")
    data = llm_node["data"]

    assert data["auto_model_routing"] is True
    assert data["model_id"] == demo_seed.DEMO_ONBOARDING_ROUTER_MODEL
    assert data["model_routing_context"] == {
        "customer_facing": False,
        "node_task": "enterprise_internal_request",
        "risk_level": "medium",
    }

    policy = data["model_routing_policy"]
    assert policy["refresh"]["refresh_every_runs"] == 10
    assert policy == {
        "refresh": {"refresh_every_runs": 10},
        "validation_budget_usd": 3.0,
        "excluded_model_ids": ["gpt-5.6-sol"],
    }
    assert {item["id"] for item in data["knowledgeBases"]} == {
        str(demo_seed.KB_IDS[key])
        for key in (
            "legal_privacy",
            "onboarding_platform",
            "onboarding_sales",
            "onboarding_finance",
        )
    }
    node_ids = {node["id"] for node in graph["nodes"]}
    assert all(
        edge["source"] in node_ids and edge["target"] in node_ids
        for edge in graph["edges"]
    )


def test_enterprise_request_routing_graph_validates_and_maps_webhook_payload():
    """시연 webhook의 실제 입력 필드가 LLM 입력 변수로 전달된다."""
    graph = demo_seed._enterprise_request_routing_graph()
    validate_workflow_graph(graph)
    webhook = next(node for node in graph["nodes"] if node["id"] == "webhook-request")
    node = WebhookTriggerNode(
        id=webhook["id"],
        data=WebhookTriggerNodeData.model_validate(webhook["data"]),
    )

    result = node.execute(
        {
            "query": "VPN 접근 권한을 회수하는 절차를 알려 주세요.",
            "department": "플랫폼",
            "requesterRole": "관리자",
            "locale": "ko-KR",
        }
    )

    assert result == {
        "query": "VPN 접근 권한을 회수하는 절차를 알려 주세요.",
        "department": "플랫폼",
        "requesterRole": "관리자",
        "locale": "ko-KR",
    }


def test_demo_seed_creates_requested_onboarding_workflows(monkeypatch):
    calls = {}

    def capture(
        _db,
        key,
        name,
        description,
        owner_key,
        graph,
        *,
        deployed,
        deployment_type=demo_seed.DeploymentType.API,
        list_updated_at=None,
    ):
        calls[key] = {
            "name": name,
            "description": description,
            "owner_key": owner_key,
            "graph": graph,
            "deployed": deployed,
            "deployment_type": deployment_type,
            "list_updated_at": list_updated_at,
        }
        return key

    monkeypatch.setattr(demo_seed, "_upsert_app_workflow", capture)

    workflows = demo_seed._seed_apps_and_workflows(object())

    assert set(workflows) == {
        "internal_it_helpdesk_routing",
        "onboarding_chatbot",
        "new_employee_onboarding_chatbot",
    }
    assert calls["internal_it_helpdesk_routing"]["name"] == "사내 IT 문의 자동 처리"
    assert calls["internal_it_helpdesk_routing"]["deployed"] is True
    assert (
        calls["internal_it_helpdesk_routing"]["deployment_type"]
        is demo_seed.DeploymentType.WEBHOOK
    )
    onboarding_call = calls["onboarding_chatbot"]
    assert onboarding_call["name"] == "온보딩 챗봇"
    assert onboarding_call["owner_key"] == "admin"
    assert onboarding_call["deployed"] is False
    assert onboarding_call["graph"] == {
        "nodes": [
            {
                "id": "start-onboarding-chatbot",
                "type": "startNode",
                "position": {"x": 250, "y": 250},
                "data": {
                    "title": "입력",
                    "displayNumber": 1,
                    "triggerType": "manual",
                    "variables": [],
                },
            }
        ],
        "edges": [],
        "viewport": {"x": 0, "y": 0, "zoom": 1},
    }
    new_employee_call = calls["new_employee_onboarding_chatbot"]
    assert new_employee_call["name"] == "신입 사원 온보딩 챗봇"
    assert new_employee_call["deployed"] is True
    assert (
        new_employee_call["deployment_type"]
        is demo_seed.DeploymentType.INTERNAL_CHATBOT
    )
    assert (
        onboarding_call["list_updated_at"]
        > calls["internal_it_helpdesk_routing"]["list_updated_at"]
        > new_employee_call["list_updated_at"]
    )
    assert set(demo_seed.APP_IDS) == {
        "internal_it_helpdesk_routing",
        "onboarding_chatbot",
        "new_employee_onboarding_chatbot",
    }
    assert set(demo_seed.WORKFLOW_IDS) == {
        "internal_it_helpdesk_routing",
        "onboarding_chatbot",
        "new_employee_onboarding_chatbot",
    }
    assert set(demo_seed.DEPLOYMENT_IDS) == {
        "internal_it_helpdesk_routing",
        "new_employee_onboarding_chatbot",
    }
    assert demo_seed.APP_IDS["new_employee_onboarding_chatbot"] == uuid.UUID(
        "97000000-0000-0000-0000-000000000001"
    )
    assert demo_seed.WORKFLOW_IDS["new_employee_onboarding_chatbot"] == uuid.UUID(
        "97000000-0000-0000-0000-000000000002"
    )
    assert demo_seed.DEPLOYMENT_IDS["new_employee_onboarding_chatbot"] == uuid.UUID(
        "97000000-0000-0000-0000-000000000003"
    )
    assert set(demo_seed.RETIRED_DEMO_APP_IDS) == {
        "hr_bot_example",
        "ticket_ops",
        "ticket_ops_warning",
        "ticket_ops_risk",
        "ticket_ops_paused",
        "test_inquiry",
        "department_onboarding_chatbot",
        "team_onboarding_access_control",
        "model_router_ticket_ops",
        "team_onboarding_adaptive_routing",
        "enterprise_request_routing",
    }


def test_demo_seed_grants_onboarding_workflow_execution_to_platform_and_sales(
    monkeypatch,
):
    upserts = []

    def capture_upsert(_db, model, row_id, values):
        upserts.append((model, row_id, values))

    monkeypatch.setattr(demo_seed, "_upsert_by_id", capture_upsert)

    demo_seed._seed_permissions(object())

    team_workflow_permissions = [
        (row_id, values)
        for model, row_id, values in upserts
        if model is demo_seed.TeamWorkflowPermission
    ]
    assert team_workflow_permissions == [
        (
            demo_seed.TEAM_PERMISSION_IDS["internal_it_helpdesk_routing"],
            {
                "grantee_organization_id": demo_seed.ORG_ID,
                "team_id": demo_seed.TEAM_IDS["platform_admin"],
                "workflow_id": demo_seed.WORKFLOW_IDS[
                    "internal_it_helpdesk_routing"
                ],
                "auth_state": "manager",
                "assigned_by": demo_seed.USER_IDS["admin"],
                "options": demo_seed._demo_options(
                    "permission-internal-it-helpdesk-routing"
                ),
                "flags": 0,
            },
        ),
        (
            demo_seed.TEAM_PERMISSION_IDS["onboarding_chatbot_platform"],
            {
                "grantee_organization_id": demo_seed.ORG_ID,
                "team_id": demo_seed.TEAM_IDS["onboarding_platform"],
                "workflow_id": demo_seed.WORKFLOW_IDS["onboarding_chatbot"],
                "auth_state": "operator",
                "assigned_by": demo_seed.USER_IDS["admin"],
                "options": demo_seed._demo_options(
                    "permission-onboarding-chatbot-platform"
                ),
                "flags": 0,
            },
        ),
        (
            demo_seed.TEAM_PERMISSION_IDS["onboarding_chatbot_sales"],
            {
                "grantee_organization_id": demo_seed.ORG_ID,
                "team_id": demo_seed.TEAM_IDS["onboarding_sales"],
                "workflow_id": demo_seed.WORKFLOW_IDS["onboarding_chatbot"],
                "auth_state": "operator",
                "assigned_by": demo_seed.USER_IDS["admin"],
                "options": demo_seed._demo_options(
                    "permission-onboarding-chatbot-sales"
                ),
                "flags": 0,
            },
        ),
        (
            demo_seed.TEAM_PERMISSION_IDS[
                "new_employee_onboarding_chatbot_platform"
            ],
            {
                "grantee_organization_id": demo_seed.ORG_ID,
                "team_id": demo_seed.TEAM_IDS["onboarding_platform"],
                "workflow_id": demo_seed.WORKFLOW_IDS[
                    "new_employee_onboarding_chatbot"
                ],
                "auth_state": "operator",
                "assigned_by": demo_seed.USER_IDS["admin"],
                "options": demo_seed._demo_options(
                    "permission-new-employee-onboarding-chatbot-platform"
                ),
                "flags": 0,
            },
        ),
        (
            demo_seed.TEAM_PERMISSION_IDS[
                "new_employee_onboarding_chatbot_sales"
            ],
            {
                "grantee_organization_id": demo_seed.ORG_ID,
                "team_id": demo_seed.TEAM_IDS["onboarding_sales"],
                "workflow_id": demo_seed.WORKFLOW_IDS[
                    "new_employee_onboarding_chatbot"
                ],
                "auth_state": "operator",
                "assigned_by": demo_seed.USER_IDS["admin"],
                "options": demo_seed._demo_options(
                    "permission-new-employee-onboarding-chatbot-sales"
                ),
                "flags": 0,
            },
        ),
    ]
    assert all(
        model is not demo_seed.UserWorkflowPermission
        for model, _row_id, _values in upserts
    )


def test_new_employee_onboarding_chatbot_graph_matches_demo_contract():
    graph = demo_seed._new_employee_onboarding_chatbot_graph()
    validate_workflow_graph(graph)

    assert [node["type"] for node in graph["nodes"]] == [
        "startNode",
        "llmNode",
        "answerNode",
    ]
    assert [(edge["source"], edge["target"]) for edge in graph["edges"]] == [
        ("start-onboarding-question", "llm-onboarding-answer"),
        ("llm-onboarding-answer", "answer-onboarding"),
    ]

    llm_data = graph["nodes"][1]["data"]
    assert llm_data["model_id"] == "gpt-5.6"
    assert llm_data["fallback_model_id"] == "gpt-5.4"
    assert llm_data["auto_model_routing"] is True
    assert llm_data["knowledgeCollections"] == [
        {
            "id": str(
                demo_seed.COLLECTION_IDS["team_onboarding_access_control"]
            ),
            "safeLabel": "팀별 온보딩 접근 제어 문서",
        }
    ]
    assert llm_data["knowledgeBases"] == [
        demo_seed._knowledge_base_ref(spec.key)
        for spec in demo_seed.ONBOARDING_PDF_SPECS
    ]
    assert llm_data["scoreThreshold"] == 0.3
    assert llm_data["topK"] == 5
    assert llm_data["context_variable"] == "question"


def test_new_employee_onboarding_chatbot_keeps_fixed_last_list_timestamp(
    monkeypatch,
):
    app = SimpleNamespace(
        id=demo_seed.APP_IDS["new_employee_onboarding_chatbot"],
        workflow_id=None,
        active_deployment_id=None,
        updated_at=None,
    )
    workflow = SimpleNamespace(
        id=demo_seed.WORKFLOW_IDS["new_employee_onboarding_chatbot"]
    )

    def capture_upsert(_db, model, _row_id, _values):
        if model is demo_seed.App:
            return app
        if model is demo_seed.Workflow:
            return workflow
        raise AssertionError(f"unexpected model: {model}")

    db = SimpleNamespace(get=lambda _model, _row_id: None, flush=lambda: None)
    monkeypatch.setattr(demo_seed, "_upsert_by_id", capture_upsert)

    list_updated_at = datetime(2026, 7, 21, tzinfo=timezone.utc)
    demo_seed._upsert_app_workflow(
        db,
        "new_employee_onboarding_chatbot",
        "신입 사원 온보딩 챗봇",
        "신입 사원 온보딩 챗봇",
        "admin",
        demo_seed._new_employee_onboarding_chatbot_graph(),
        deployed=False,
        list_updated_at=list_updated_at,
    )

    assert app.updated_at == list_updated_at


def test_demo_summary_reports_seeded_knowledge_documents():
    summary = demo_seed.demo_summary("demo")

    assert summary["apps"] == [
        "온보딩 챗봇",
        "사내 IT 문의 자동 처리",
        "신입 사원 온보딩 챗봇",
    ]
    assert summary["knowledge_documents"] == {
        "public_law_pdfs": 7,
        "internal_markdown_docs": 2,
        "bundled_onboarding_pdfs": 3,
        "embedding_model": demo_seed.DEMO_EMBEDDING_MODEL,
        "fixture": demo_seed.DEMO_KNOWLEDGE_FIXTURE_PATH.as_posix(),
    }


def test_demo_seed_excludes_internal_document_prefixed_knowledge_bases():
    prefixed_specs = [
        spec.name
        for spec in demo_seed.DEMO_DOCUMENT_SPECS
        if spec.name.startswith("사내문서:")
    ]

    assert prefixed_specs == []


def test_demo_summary_describes_secure_runtime_credential_input():
    credentials = demo_seed.demo_summary("demo")["credentials"]

    assert "OPENAI_API_KEY" in credentials
    assert ".env" not in credentials


def test_demo_seed_prerequisites_use_precomputed_fixture_without_openai(
    tmp_path, monkeypatch
):
    fixture_path = tmp_path / "demo_knowledge_chunks.jsonl.gz"
    write_minimal_demo_fixture(fixture_path)

    monkeypatch.setattr(demo_seed, "DEMO_KNOWLEDGE_FIXTURE_PATH", fixture_path)
    monkeypatch.setattr(demo_seed, "DEMO_LEGAL_DOCS_LABOR_DIR", tmp_path / "missing")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv(demo_seed.DEMO_REGENERATE_KNOWLEDGE_FIXTURE_ENV, raising=False)
    monkeypatch.delenv(
        demo_seed.DEMO_ENABLE_RUNTIME_OPENAI_CREDENTIAL_ENV,
        raising=False,
    )
    monkeypatch.setenv("ENCRYPTION_KEY", "present-only")

    demo_seed.validate_demo_seed_prerequisites()


def test_runtime_credential_indexes_onboarding_pdfs_without_legal_pdf_sources(
    tmp_path, monkeypatch
):
    fixture_path = tmp_path / "demo_knowledge_chunks.jsonl.gz"
    write_minimal_demo_fixture(fixture_path)

    monkeypatch.setattr(demo_seed, "DEMO_KNOWLEDGE_FIXTURE_PATH", fixture_path)
    monkeypatch.setattr(demo_seed, "_resolve_onboarding_pdf", lambda _spec: tmp_path)
    monkeypatch.setattr(
        demo_seed,
        "_resolve_legal_pdf",
        lambda _spec: pytest.fail("runtime credential mode must not require legal PDFs"),
    )
    monkeypatch.setenv("ENCRYPTION_KEY", "present-only")
    monkeypatch.setenv("OPENAI_API_KEY", "runtime-credential-value")
    monkeypatch.setenv(demo_seed.DEMO_ENABLE_RUNTIME_OPENAI_CREDENTIAL_ENV, "1")
    monkeypatch.delenv(demo_seed.DEMO_REGENERATE_KNOWLEDGE_FIXTURE_ENV, raising=False)

    demo_seed.validate_demo_seed_prerequisites()


def test_demo_fixture_rejects_missing_document(tmp_path, monkeypatch):
    fixture_path = tmp_path / "demo_knowledge_chunks.jsonl.gz"
    missing_key = demo_seed.DEMO_DOCUMENT_SPECS[0].key
    write_minimal_demo_fixture(fixture_path, omitted_document_key=missing_key)

    monkeypatch.setattr(demo_seed, "DEMO_KNOWLEDGE_FIXTURE_PATH", fixture_path)

    with pytest.raises(ValueError, match="document 누락"):
        demo_seed._read_demo_knowledge_fixture()


def test_demo_fixture_rejects_wrong_embedding_dimension(tmp_path, monkeypatch):
    fixture_path = tmp_path / "demo_knowledge_chunks.jsonl.gz"
    malformed_key = demo_seed.DEMO_DOCUMENT_SPECS[0].key
    write_minimal_demo_fixture(
        fixture_path,
        malformed_embedding_key=malformed_key,
    )

    monkeypatch.setattr(demo_seed, "DEMO_KNOWLEDGE_FIXTURE_PATH", fixture_path)

    with pytest.raises(ValueError, match="embedding 차원 오류"):
        demo_seed._read_demo_knowledge_fixture()


def test_demo_seed_runtime_credential_opt_in_requires_openai_key(tmp_path, monkeypatch):
    fixture_path = tmp_path / "demo_knowledge_chunks.jsonl.gz"
    write_minimal_demo_fixture(fixture_path)

    monkeypatch.setattr(demo_seed, "DEMO_KNOWLEDGE_FIXTURE_PATH", fixture_path)
    monkeypatch.setattr(demo_seed, "_load_seed_env", lambda: None)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("ENCRYPTION_KEY", "present-only")
    monkeypatch.setenv(demo_seed.DEMO_ENABLE_RUNTIME_OPENAI_CREDENTIAL_ENV, "1")

    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        demo_seed.validate_demo_seed_prerequisites()


def test_runtime_openai_key_uses_environment_without_prompt(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "runtime-credential-value")
    monkeypatch.setattr(
        seed_demo_script.getpass,
        "getpass",
        lambda _prompt: pytest.fail("environment key must not prompt"),
    )

    assert (
        seed_demo_script.resolve_runtime_openai_api_key()
        == "runtime-credential-value"
    )


def test_runtime_openai_key_prompts_without_environment(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(
        seed_demo_script.sys,
        "stdin",
        SimpleNamespace(isatty=lambda: True),
    )
    monkeypatch.setattr(
        seed_demo_script.getpass,
        "getpass",
        lambda _prompt: "prompted-runtime-value",
    )

    assert (
        seed_demo_script.resolve_runtime_openai_api_key()
        == "prompted-runtime-value"
    )
    assert os.environ["OPENAI_API_KEY"] == "prompted-runtime-value"


def test_runtime_openai_key_requires_environment_when_non_interactive(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(
        seed_demo_script.sys,
        "stdin",
        SimpleNamespace(isatty=lambda: False),
    )

    with pytest.raises(RuntimeError, match="OPENAI_API_KEY is required"):
        seed_demo_script.resolve_runtime_openai_api_key()


def test_runtime_openai_key_validation_requires_expected_embedding_dimension(
    monkeypatch,
):
    class ValidEmbeddingClient:
        def __init__(self, **_kwargs):
            pass

        def embed_sync(self, _text):
            return [0.0] * demo_seed.DEMO_EMBEDDING_DIMENSION

    monkeypatch.setattr(seed_demo_script, "OpenAIClient", ValidEmbeddingClient)

    assert seed_demo_script.validate_runtime_openai_api_key(
        "runtime-credential-value"
    )


def test_runtime_openai_key_validation_rejects_wrong_embedding_dimension(monkeypatch):
    class WrongDimensionClient:
        def __init__(self, **_kwargs):
            pass

        def embed_sync(self, _text):
            return [0.0]

    monkeypatch.setattr(seed_demo_script, "OpenAIClient", WrongDimensionClient)

    assert not seed_demo_script.validate_runtime_openai_api_key(
        "runtime-credential-value"
    )


def test_runtime_openai_validation_failure_warns_once_and_continues_on_enter(
    monkeypatch,
    capsys,
):
    monkeypatch.setattr(
        seed_demo_script,
        "resolve_runtime_openai_api_key",
        lambda: "runtime-credential-value",
    )
    monkeypatch.setattr(
        seed_demo_script,
        "validate_runtime_openai_api_key",
        lambda _key: False,
    )
    monkeypatch.setattr(
        seed_demo_script.sys,
        "stdin",
        SimpleNamespace(isatty=lambda: True),
    )
    monkeypatch.setattr("builtins.input", lambda _prompt: "")

    assert (
        seed_demo_script.prepare_runtime_openai_credential()
        == "runtime-credential-value"
    )

    captured = capsys.readouterr()
    assert captured.err.count("OpenAI API key could not be verified") == 1
    assert "runtime-credential-value" not in captured.err
    assert "runtime-credential-value" not in captured.out


def test_runtime_openai_validation_failure_stops_on_no(monkeypatch):
    monkeypatch.setattr(
        seed_demo_script,
        "resolve_runtime_openai_api_key",
        lambda: "runtime-credential-value",
    )
    monkeypatch.setattr(
        seed_demo_script,
        "validate_runtime_openai_api_key",
        lambda _key: False,
    )
    monkeypatch.setattr(
        seed_demo_script.sys,
        "stdin",
        SimpleNamespace(isatty=lambda: True),
    )
    monkeypatch.setattr("builtins.input", lambda _prompt: "n")

    with pytest.raises(SystemExit, match="OpenAI API key verification was not accepted"):
        seed_demo_script.prepare_runtime_openai_credential()


def test_runtime_openai_validation_failure_requires_tty_confirmation(monkeypatch):
    monkeypatch.setattr(
        seed_demo_script,
        "resolve_runtime_openai_api_key",
        lambda: "runtime-credential-value",
    )
    monkeypatch.setattr(
        seed_demo_script,
        "validate_runtime_openai_api_key",
        lambda _key: False,
    )
    monkeypatch.setattr(
        seed_demo_script.sys,
        "stdin",
        SimpleNamespace(isatty=lambda: False),
    )

    with pytest.raises(RuntimeError, match="interactive confirmation"):
        seed_demo_script.prepare_runtime_openai_credential()


def test_dry_run_skips_runtime_openai_credential_preparation(monkeypatch, capsys):
    monkeypatch.setattr(
        seed_demo_script.sys,
        "argv",
        [
            "seed_demo.py",
            "--profile",
            "demo",
            "--dry-run",
            "--enable-runtime-openai-credential",
        ],
    )
    monkeypatch.setattr(
        seed_demo_script,
        "prepare_runtime_openai_credential",
        lambda: pytest.fail("dry-run must not prompt or validate a credential"),
    )

    seed_demo_script.main()

    assert '"profile": "demo"' in capsys.readouterr().out


def test_demo_seed_prepares_runtime_credential_before_seed(monkeypatch):
    events = []

    class FakeSession:
        def close(self):
            events.append("close")

    monkeypatch.setattr(
        seed_demo_script.sys,
        "argv",
        [
            "seed_demo.py",
            "--profile",
            "demo",
            "--skip-schema",
            "--enable-runtime-openai-credential",
        ],
    )
    monkeypatch.setattr(
        seed_demo_script,
        "prepare_runtime_openai_credential",
        lambda: events.append("prepare"),
    )
    monkeypatch.setattr(
        seed_demo_script,
        "validate_demo_seed_prerequisites",
        lambda: events.append("prerequisites"),
    )
    monkeypatch.setattr(
        seed_demo_script,
        "check_demo_schema_readiness",
        lambda: events.append("schema"),
    )
    monkeypatch.setattr(seed_demo_script, "SessionLocal", lambda: FakeSession())
    monkeypatch.setattr(
        seed_demo_script,
        "seed_demo_data",
        lambda _db: events.append("seed"),
    )

    seed_demo_script.main()

    assert events == ["prepare", "prerequisites", "schema", "seed", "close"]


@pytest.mark.parametrize(
    ("reset_function", "adopt_function", "seed_function"),
    [
        (
            demo_seed.reset_demo_data,
            "_adopt_existing_demo_user_ids",
            "seed_demo_data",
        ),
        (
            demo_seed.reset_test_data,
            "_adopt_existing_test_user_ids",
            "seed_test_data",
        ),
    ],
)
def test_profile_reset_preserves_organization(
    monkeypatch,
    reset_function,
    adopt_function,
    seed_function,
):
    db = ResetRecorderSession()
    monkeypatch.setattr(demo_seed, "validate_demo_seed_prerequisites", lambda: None)
    monkeypatch.setattr(demo_seed, adopt_function, lambda _db: None)
    monkeypatch.setattr(demo_seed, seed_function, lambda _db: None)

    reset_function(db)

    assert demo_seed.Organization not in db.deleted_targets


def test_test_profile_reset_clears_agent_builder_state_child_first(monkeypatch):
    db = ResetRecorderSession()
    monkeypatch.setattr(demo_seed, "_adopt_existing_test_user_ids", lambda _db: None)
    monkeypatch.setattr(demo_seed, "seed_test_data", lambda _db: None)

    demo_seed.reset_test_data(db)

    targets = (
        demo_seed.AgentBuilderDraft,
        demo_seed.AgentBuilderRequest,
        demo_seed.AgentBuilderSession,
    )
    assert [db.deleted_targets.index(model) for model in targets] == sorted(
        db.deleted_targets.index(model) for model in targets
    )
    for model in targets:
        condition = db.filter_criteria[model][0].compile()
        assert f"{model.__tablename__}.organization_id" in str(condition)
        assert list(condition.params.values()) == [demo_seed.TEST_ORG_ID]


def test_demo_profile_reset_preserves_agent_builder_state(monkeypatch):
    db = ResetRecorderSession()
    monkeypatch.setattr(demo_seed, "validate_demo_seed_prerequisites", lambda: None)
    monkeypatch.setattr(demo_seed, "_adopt_existing_demo_user_ids", lambda _db: None)
    monkeypatch.setattr(demo_seed, "seed_demo_data", lambda _db: None)

    demo_seed.reset_demo_data(db)

    assert demo_seed.AgentBuilderDraft not in db.deleted_targets
    assert demo_seed.AgentBuilderRequest not in db.deleted_targets
    assert demo_seed.AgentBuilderSession not in db.deleted_targets


def test_demo_profile_reset_deletes_ingestion_jobs_before_documents(monkeypatch):
    db = ResetRecorderSession()
    monkeypatch.setattr(demo_seed, "validate_demo_seed_prerequisites", lambda: None)
    monkeypatch.setattr(demo_seed, "_adopt_existing_demo_user_ids", lambda _db: None)
    monkeypatch.setattr(demo_seed, "seed_demo_data", lambda _db: None)

    demo_seed.reset_demo_data(db)

    assert db.deleted_targets.index(
        demo_seed.KnowledgeDocumentIngestionJob
    ) < db.deleted_targets.index(demo_seed.Document)
    assert db.deleted_targets.index(
        demo_seed.KnowledgeDocumentIngestionJob
    ) < db.deleted_targets.index(demo_seed.KnowledgeBase)


def test_demo_profile_reset_deletes_retired_internal_knowledge_rows(monkeypatch):
    db = ResetRecorderSession()
    monkeypatch.setattr(demo_seed, "validate_demo_seed_prerequisites", lambda: None)
    monkeypatch.setattr(demo_seed, "_adopt_existing_demo_user_ids", lambda _db: None)
    monkeypatch.setattr(demo_seed, "seed_demo_data", lambda _db: None)

    demo_seed.reset_demo_data(db)

    kb_condition = db.filter_criteria[demo_seed.KnowledgeBase][0].compile()
    collection_condition = db.filter_criteria[demo_seed.KnowledgeCollection][
        0
    ].compile()
    reset_kb_ids = set(next(iter(kb_condition.params.values())))
    reset_collection_ids = set(next(iter(collection_condition.params.values())))
    assert set(demo_seed.RETIRED_INTERNAL_DOCUMENT_KB_IDS.values()) <= reset_kb_ids
    assert (
        set(demo_seed.RETIRED_INTERNAL_DOCUMENT_COLLECTION_IDS.values())
        <= reset_collection_ids
    )


def test_demo_seed_cleanup_deletes_retired_internal_knowledge_rows():
    db = ResetRecorderSession()

    demo_seed._delete_retired_internal_knowledge(db)

    for model in (
        demo_seed.TeamKnowledgePermission,
        demo_seed.TeamKnowledgeCollectionPermission,
        demo_seed.KnowledgeCollectionItem,
        demo_seed.KnowledgeCollection,
        demo_seed.DocumentChunk,
        demo_seed.Document,
        demo_seed.KnowledgeBase,
    ):
        assert model in db.deleted_targets
    assert db.deleted_targets.index(
        demo_seed.KnowledgeCollectionItem
    ) < db.deleted_targets.index(demo_seed.KnowledgeCollection)
    assert db.deleted_targets.index(demo_seed.DocumentChunk) < db.deleted_targets.index(
        demo_seed.Document
    )
    assert db.deleted_targets.index(demo_seed.Document) < db.deleted_targets.index(
        demo_seed.KnowledgeBase
    )


def test_demo_seed_cleanup_deletes_retired_workflows_child_first():
    db = ResetRecorderSession()

    demo_seed._delete_retired_demo_workflows(db)

    for model in (
        demo_seed.TracePayloadAccessEvent,
        demo_seed.TracePayload,
        ConversationSessionRecord,
        demo_seed.LLMUsageLog,
        demo_seed.WorkflowNodeRun,
        demo_seed.WorkflowRun,
        demo_seed.TeamWorkflowPermission,
        demo_seed.UserWorkflowPermission,
        demo_seed.WorkflowDeployment,
        demo_seed.Workflow,
        demo_seed.App,
    ):
        assert model in db.deleted_targets
    assert db.deleted_targets.index(
        demo_seed.WorkflowNodeRun
    ) < db.deleted_targets.index(demo_seed.WorkflowRun)
    assert db.deleted_targets.index(
        demo_seed.WorkflowDeployment
    ) < db.deleted_targets.index(demo_seed.Workflow)
    assert db.deleted_targets.index(demo_seed.Workflow) < db.deleted_targets.index(
        demo_seed.App
    )
    assert set(demo_seed.APP_IDS.values()).isdisjoint(
        demo_seed.RETIRED_DEMO_APP_IDS.values()
    )
    assert set(demo_seed.WORKFLOW_IDS.values()).isdisjoint(
        demo_seed.RETIRED_DEMO_WORKFLOW_IDS.values()
    )


def test_demo_runtime_credential_grants_agent_builder_user_permission(monkeypatch):
    upserts = []

    def capture_upsert(_db, model, row_id, values):
        upserts.append((model, row_id, values))

    monkeypatch.setattr(demo_seed, "_upsert_by_id", capture_upsert)

    demo_seed._seed_runtime_llm_permissions(object())

    user_permissions = {
        values["user_id"]: values
        for model, _row_id, values in upserts
        if model is demo_seed.UserLLMPermission
    }
    assert set(user_permissions) == {
        demo_seed.USER_IDS["author"],
        demo_seed.USER_IDS["tester_builder"],
    }
    assert user_permissions[demo_seed.USER_IDS["tester_builder"]]["auth_state"] == (
        "operator"
    )
    team_permissions = {
        values["team_id"]: values
        for model, _row_id, values in upserts
        if model is demo_seed.TeamLLMPermission
    }
    assert team_permissions[demo_seed.TEAM_IDS["department_development"]][
        "auth_state"
    ] == "operator"
    assert team_permissions[demo_seed.TEAM_IDS["department_planning"]][
        "auth_state"
    ] == "operator"


def test_demo_seed_chat_models_use_gpt_5_4_family():
    assert demo_seed.DEMO_CHAT_MODEL == "gpt-5.4"
    assert demo_seed.DEMO_CHAT_MINI_MODEL == "gpt-5.4-mini"
    assert demo_seed.DEMO_MODEL_ROUTER_LATEST_ECONOMY_MODEL == "gpt-5.6-luna"
    assert demo_seed.DEMO_MODEL_ROUTER_LATEST_BALANCED_MODEL == "gpt-5.6-terra"
    assert demo_seed.DEMO_MODEL_ROUTER_LATEST_ADVANCED_MODEL == "gpt-5.6"
    assert demo_seed.DEMO_MODEL_ROUTER_LATEST_SOL_MODEL == "gpt-5.6-sol"
    assert demo_seed.DEMO_MODEL_ROUTER_OMNI_MODEL == "gpt-4o"
    assert demo_seed.DEMO_MODEL_ROUTER_REASONING_MODEL == "o3"
    assert set(demo_seed.CREDENTIAL_MODEL_REL_IDS) == {
        demo_seed.DEMO_CHAT_MODEL,
        demo_seed.DEMO_CHAT_MINI_MODEL,
        demo_seed.DEMO_MODEL_ROUTER_BASE_MODEL,
        demo_seed.DEMO_MODEL_ROUTER_FALLBACK_MODEL,
        demo_seed.DEMO_MODEL_ROUTER_CHEAP_MODEL,
        demo_seed.DEMO_MODEL_ROUTER_BALANCED_MODEL,
        demo_seed.DEMO_MODEL_ROUTER_LATEST_ECONOMY_MODEL,
        demo_seed.DEMO_MODEL_ROUTER_LATEST_BALANCED_MODEL,
        demo_seed.DEMO_MODEL_ROUTER_LATEST_ADVANCED_MODEL,
        demo_seed.DEMO_MODEL_ROUTER_LATEST_SOL_MODEL,
        demo_seed.DEMO_MODEL_ROUTER_OMNI_MODEL,
        demo_seed.DEMO_MODEL_ROUTER_REASONING_MODEL,
        demo_seed.DEMO_ONBOARDING_ROUTER_MODEL,
        demo_seed.DEMO_EMBEDDING_MODEL,
    }


def test_model_router_demo_workflow_uses_current_routing_context():
    graph = demo_seed._model_router_ticket_ops_graph()
    llm_node = next(node for node in graph["nodes"] if node["id"] == "llm-triage")
    data = llm_node["data"]

    assert data["auto_model_routing"] is True
    assert data["model_id"] == "gpt-4.1"
    assert data["fallback_model_id"] == "gpt-4.1-mini"
    assert data["model_routing_context"] == {
        "customer_facing": True,
        "node_task": "customer_support_triage",
        "risk_level": "medium",
    }
    assert data["model_routing_policy"] == {
        "refresh": {"refresh_every_runs": 10},
        "validation_budget_usd": 3.0,
        "excluded_model_ids": ["gpt-5.6-sol"],
    }
    assert data["knowledgeBases"] == []


def test_internal_it_helpdesk_routing_demo_matches_presentation_contract():
    graph = demo_seed._internal_it_helpdesk_routing_graph()
    nodes = {node["id"]: node for node in graph["nodes"]}

    assert demo_seed.APP_IDS["internal_it_helpdesk_routing"] == uuid.UUID(
        "94000000-0000-0000-0000-000000000001"
    )
    assert demo_seed.WORKFLOW_IDS["internal_it_helpdesk_routing"] == uuid.UUID(
        "94000000-0000-0000-0000-000000000002"
    )
    assert demo_seed.DEPLOYMENT_IDS["internal_it_helpdesk_routing"] == uuid.UUID(
        "94000000-0000-0000-0000-000000000003"
    )
    assert demo_seed._input_schema_from_graph(graph) == {
        "variables": [
            {"name": "department", "type": "text", "label": "부서"},
            {"name": "message", "type": "text", "label": "문의"},
        ]
    }

    llm_data = nodes["llm-triage"]["data"]
    assert llm_data["auto_model_routing"] is True
    assert llm_data["model_routing_context"]["node_task"] == "internal_it_helpdesk"
    assert llm_data["knowledgeBases"] == [
        demo_seed._knowledge_base_ref("onboarding_platform"),
    ]
    assert llm_data["output_format"]["schema"]["required"] == [
        "문의 유형",
        "긴급도",
        "답변 초안",
    ]
    assert "경제형" in llm_data["model_routing_task_description"]
    assert "균형형" in llm_data["model_routing_task_description"]
    assert "고성능형" in llm_data["model_routing_task_description"]


def test_internal_it_helpdesk_routing_demo_starts_with_optimized_layout():
    graph = demo_seed._internal_it_helpdesk_routing_graph()
    positions = {
        node["id"]: node["position"]
        for node in graph["nodes"]
    }

    assert positions == {
        "webhook-ticket": {"x": 0, "y": 0},
        "llm-triage": {"x": 580, "y": 0},
        "extract-ticket": {"x": 1160, "y": 0},
        "condition-approval": {"x": 1740, "y": 0},
        "template-approval": {"x": 2320, "y": 300},
        "template-reply": {"x": 2320, "y": 0},
        "answer-approval": {"x": 2900, "y": 300},
        "answer-reply": {"x": 2900, "y": 0},
    }
    assert calculate_workflow_auto_layout(graph) == graph


def test_internal_it_helpdesk_routing_demo_orders_it_result_before_security_result():
    graph = demo_seed._internal_it_helpdesk_routing_graph()
    ordered_node_ids = [node["id"] for node in graph["nodes"]]
    nodes = {node["id"]: node for node in graph["nodes"]}

    assert nodes["answer-reply"]["data"]["title"] == "IT 안내 결과"
    assert nodes["answer-reply"]["data"]["displayNumber"] == 7
    assert nodes["answer-approval"]["data"]["title"] == "보안 대응 결과"
    assert nodes["answer-approval"]["data"]["displayNumber"] == 8
    assert ordered_node_ids.index("answer-reply") < ordered_node_ids.index(
        "answer-approval"
    )


def test_internal_it_helpdesk_routing_demo_seeds_all_presentation_logs():
    specs = demo_seed.INTERNAL_IT_HELPDESK_ROUTING_RUN_SPECS

    assert len(specs) == 9
    assert {spec.model_name for spec in specs} >= {
        "gpt-4o-mini",
        "gpt-4.1-mini",
        "gpt-4.1",
        "gpt-5.4",
        "gpt-5.6-terra",
    }
    assert all(spec.status == RunStatus.SUCCESS for spec in specs)
    assert all(spec.department and spec.message for spec in specs)
    assert all(spec.total_tokens > 0 and spec.total_cost > 0 for spec in specs)
    assert specs[-1].run_id == uuid.UUID("f4c671f9-5c32-4e00-95ca-e11f5fe560eb")
    assert specs[-1].model_name == "gpt-5.6-terra"
    assert specs[-1].department == "플랫폼개발"
    assert specs[-1].message == (
        "보안 교육을 아직 완료하지 않은 신규 입사자가 운영 저장소 접근과 배포 권한을 요청했습니다. "
        "SSO, VPN, Git 권한, 승인 절차를 함께 고려해 허용 여부를 판단해 주세요."
    )


def test_demo_audit_logs_reference_only_active_workflow(monkeypatch):
    upserts = []

    def capture_upsert(_db, model, row_id, values):
        upserts.append((model, row_id, values))

    monkeypatch.setattr(demo_seed, "_upsert_by_id", capture_upsert)

    demo_seed._seed_audit_logs(ResetRecorderSession())

    workflow_target_ids = {
        values["target_id"]
        for model, _row_id, values in upserts
        if model is demo_seed.AuditLog and values["target_type"] == "workflow"
    }
    assert workflow_target_ids == {
        str(demo_seed.WORKFLOW_IDS["internal_it_helpdesk_routing"])
    }
    assert workflow_target_ids.isdisjoint(
        str(workflow_id)
        for workflow_id in demo_seed.RETIRED_DEMO_WORKFLOW_IDS.values()
    )


def test_demo_seed_presets_rookie_security_alert_with_safe_evidence(monkeypatch):
    upserts = []
    adopted_rookie_id = uuid.uuid4()

    def capture_upsert(_db, model, row_id, values):
        upserts.append((model, row_id, values))

    monkeypatch.setattr(demo_seed, "_upsert_by_id", capture_upsert)
    monkeypatch.setitem(demo_seed.USER_IDS, "rookie", adopted_rookie_id)

    demo_seed._seed_security_alert(ResetRecorderSession())

    denied_audits = [
        (row_id, values)
        for model, row_id, values in upserts
        if model is demo_seed.AuditLog
        and values["action"] == demo_seed.AuditAction.PERMISSION_DENIED
    ]
    assert len(denied_audits) == 10
    assert all(
        values["actor_id"] == adopted_rookie_id
        and values["actor_type"] == demo_seed.ActorType.USER
        and values["category"] == demo_seed.AuditCategory.ACTION
        and values["status"] == demo_seed.AuditStatus.FAILURE
        and values["target_type"] == "organization"
        and values["target_id"] == str(demo_seed.ORG_ID)
        for _row_id, values in denied_audits
    )
    occurred_at = [values["occurred_at"] for _row_id, values in denied_audits]
    assert max(occurred_at) - min(occurred_at) <= timedelta(minutes=5)
    assert {
        values["audit_metadata"]["requested_operation"]
        for _row_id, values in denied_audits
    } == {"list", "resolve"}
    assert all(
        set(values["audit_metadata"])
        == {
            "demo_seed",
            "demo_seed_version",
            "demo_seed_key",
            "organization_id",
            "required_permission",
            "requested_operation",
            "denial_reason",
            "permission_action",
            "policy_result",
        }
        for _row_id, values in denied_audits
    )

    alert_rows = [
        (row_id, values)
        for model, row_id, values in upserts
        if model is demo_seed.SecurityAlert
    ]
    assert alert_rows == [
        (
            demo_seed.DEMO_SECURITY_ALERT_ID,
            {
                "organization_id": demo_seed.ORG_ID,
                "subject_actor_id": adopted_rookie_id,
                "rule_id": "repeated_permission_denied",
                "rule_version": "v1",
                "severity": "medium",
                "status": "open",
                "policy_reason": None,
                "detection_key": demo_seed._demo_security_alert_detection_key(),
                "occurrence_count": 10,
                "episode_count": 1,
                "first_detected_at": min(occurred_at),
                "last_detected_at": max(occurred_at),
                "last_episode_started_at": occurred_at[4],
                "lifecycle_version": 1,
                "acknowledged_by": None,
                "acknowledged_at": None,
                "resolution_type": None,
                "resolution_reason": None,
                "resolved_by": None,
                "resolved_at": None,
                "created_at": occurred_at[4],
                "updated_at": max(occurred_at),
            },
        )
    ]

    evidence_rows = [
        values
        for model, _row_id, values in upserts
        if model is demo_seed.SecurityAlertAuditEvent
    ]
    assert len(evidence_rows) == 10
    assert {values["audit_log_id"] for values in evidence_rows} == {
        row_id for row_id, _values in denied_audits
    }
    assert all(
        values["security_alert_id"] == demo_seed.DEMO_SECURITY_ALERT_ID
        for values in evidence_rows
    )

    detected_audit = next(
        values
        for model, _row_id, values in upserts
        if model is demo_seed.AuditLog
        and values["action"] == "security_alert.detected"
    )
    assert detected_audit["actor_id"] is None
    assert detected_audit["actor_type"] == demo_seed.ActorType.SYSTEM
    assert detected_audit["target_type"] == "security_alert"
    assert detected_audit["target_id"] == str(demo_seed.DEMO_SECURITY_ALERT_ID)
    assert detected_audit["audit_metadata"] == {
        "organization_id": str(demo_seed.ORG_ID),
        "rule_id": "repeated_permission_denied",
        "rule_version": "v1",
        "severity": "medium",
    }


def test_demo_security_alert_id_is_client_deep_link_compatible():
    alert_id = demo_seed.DEMO_SECURITY_ALERT_ID

    assert alert_id.variant == uuid.RFC_4122
    assert alert_id.version in {1, 2, 3, 4, 5}


def test_internal_it_helpdesk_seed_uses_catalog_tier_for_terra_security_reason():
    security_spec = next(
        spec
        for spec in demo_seed.INTERNAL_IT_HELPDESK_ROUTING_RUN_SPECS
        if spec.run_id == uuid.UUID("2c19f120-02f7-4a6f-a983-a73c01bf425f")
    )

    tier, reason_code, reason_short = demo_seed._internal_it_helpdesk_routing_reason(
        security_spec,
        approval_required=True,
    )

    assert tier == "advanced"
    assert reason_code == "security_incident_reasoning"
    assert reason_short == "보안 사고 판단에 적합"


def test_internal_it_helpdesk_seed_exposes_safe_judge_reason_factors_for_high_risk_run():
    security_spec = next(
        spec
        for spec in demo_seed.INTERNAL_IT_HELPDESK_ROUTING_RUN_SPECS
        if spec.run_id == uuid.UUID("2c19f120-02f7-4a6f-a983-a73c01bf425f")
    )

    assert demo_seed._internal_it_helpdesk_reason_factors(
        security_spec,
        approval_required=True,
    ) == [
        "high_decision_impact",
        "security_or_compliance_risk",
        "multi_step_reasoning",
    ]


def test_internal_it_helpdesk_seed_separates_judge_usage_node_id():
    assert (
        demo_seed._internal_it_helpdesk_usage_node_id("execution") == "llm-triage"
    )
    assert (
        demo_seed._internal_it_helpdesk_usage_node_id("judge")
        == "llm-triage:routing_judge"
    )


def test_ticket_ops_input_schema_matches_webhook_mappings():
    graph = demo_seed._ticket_ops_graph()

    schema = demo_seed._input_schema_from_graph(graph)

    assert schema == {
        "variables": [
            {"name": "message", "type": "text", "label": "message"},
            {"name": "customerTier", "type": "text", "label": "customerTier"},
        ]
    }

    llm_node = next(node for node in graph["nodes"] if node["id"] == "llm-triage")
    assert "knowledgeBases" not in llm_node["data"]


def test_schema_readiness_reports_stale_demo_db_columns():
    gaps = seed_demo_script.schema_readiness_gaps(
        FakeSchemaInspector(
            {
                "knowledge_bases": {"id", "name", "user_id"},
                "documents": {"id", "knowledge_base_id", "filename"},
            }
        ),
        required_columns={
            "knowledge_bases": {
                "id",
                "name",
                "organization_id",
                "embedding_model",
                "sync_state",
                "user_id",
            },
            "documents": {"id", "knowledge_base_id", "filename", "embedding_model"},
            "document_chunks": {"id", "document_id", "embedding"},
        },
    )

    assert gaps == {
        "missing_tables": ["document_chunks"],
        "missing_columns": {
            "documents": ["embedding_model"],
            "knowledge_bases": [
                "embedding_model",
                "organization_id",
                "sync_state",
            ],
        },
        "reason": None,
    }

    message = seed_demo_script.format_schema_readiness_error(gaps)
    assert (
        "Base.metadata.create_all() creates missing tables but does not ALTER"
        in message
    )
    assert "knowledge_bases: embedding_model, organization_id, sync_state" in message
    assert "alembic -c apps/shared/alembic.ini upgrade heads" in message
    assert "--profile demo --reset --drop-existing-data --yes" in message


def test_schema_readiness_reports_safe_reason_on_introspection_failure():
    gaps = seed_demo_script.schema_readiness_gaps(
        FailingSchemaInspector(),
        required_columns={"knowledge_bases": {"id", "sync_state"}},
    )

    assert gaps == {
        "missing_tables": [],
        "missing_columns": {},
        "reason": "schema_introspection_failed",
    }

    message = seed_demo_script.format_schema_readiness_error(gaps)
    assert "Readiness check failed: schema_introspection_failed" in message
    assert "secret raw database failure" not in message


def test_alembic_readiness_reports_missing_version_table():
    gaps = seed_demo_script.alembic_readiness_gaps(
        FakeSchemaInspector({"knowledge_bases": {"id"}}),
        code_heads=["head-1"],
        known_revisions=["head-1"],
    )

    assert gaps["ready"] is False
    assert gaps["missing_version_table"] is True

    message = seed_demo_script.format_schema_readiness_error(
        {
            "missing_tables": [],
            "missing_columns": {},
            "reason": None,
            "migration": gaps,
        }
    )
    assert "alembic_version table is missing" in message


def test_alembic_readiness_reports_database_behind_code_head():
    gaps = seed_demo_script.alembic_readiness_gaps(
        FakeSchemaInspector(
            {"alembic_version": {"version_num"}},
            alembic_revisions=["head-1"],
        ),
        code_heads=["head-2"],
        known_revisions=["head-1", "head-2"],
    )

    assert gaps["ready"] is False
    assert gaps["database_behind"] is True

    message = seed_demo_script.format_schema_readiness_error(
        {
            "missing_tables": [],
            "missing_columns": {},
            "reason": None,
            "migration": gaps,
        }
    )
    assert "DB revision does not match code head" in message
    assert "db=head-1" in message
    assert "code=head-2" in message


def test_alembic_readiness_reports_split_code_heads():
    gaps = seed_demo_script.alembic_readiness_gaps(
        FakeSchemaInspector(
            {"alembic_version": {"version_num"}},
            alembic_revisions=["head-1"],
        ),
        code_heads=["head-1", "head-2"],
        known_revisions=["head-1", "head-2"],
    )

    assert gaps["ready"] is False
    assert gaps["split_heads"] is True

    message = seed_demo_script.format_schema_readiness_error(
        {
            "missing_tables": [],
            "missing_columns": {},
            "reason": None,
            "migration": gaps,
        }
    )
    assert "multiple code heads" in message


def test_knowledge_safe_metadata_migration_is_preserved_in_the_single_head():
    script = seed_demo_script._alembic_script_directory()

    safe_metadata_revision = script.get_revision("fa7c8d9e0f12")
    merged_revision = script.get_revision("ff4b5c6d7e89")
    hardened_revision = script.get_revision("ff5c6d7e8f90")
    mail_credential_revision = script.get_revision("fc1d2e3f4a5b")
    mail_processing_revision = script.get_revision("fd2e3f4a5b67")
    security_alert_revision = script.get_revision("a06b7c8d9e10")
    security_alert_indexes_revision = script.get_revision("a17c8d9e0f21")
    security_alert_watermark_revision = script.get_revision("b28d9e0f1a32")
    knowledge_permissions_revision = script.get_revision("b39e0f1a2b43")
    external_effect_revision = script.get_revision("fe3f4a5b6c78")
    recommendation_revision = script.get_revision("fc9a1b2c3d4e")
    repair_revision = script.get_revision("fd0e1f2a3b4c")
    security_alert_episode_revision = script.get_revision("fe4a5b6c7d89")
    security_alert_outbox_revision = script.get_revision("c05d6e7f8a90")
    internal_chatbot_revision = script.get_revision("fc9d0e1f2a34")
    index_alignment_revision = script.get_revision("fd3e4f5a6b78")
    configuration_preflight_revision = script.get_revision("0f4a5b6c7d89")
    adaptive_routing_revision = script.get_revision("f1c2d3e4f5a6")
    adaptive_routing_controls_revision = script.get_revision("a6f4d2c8e1b7")
    security_alert_receipt_revision = script.get_revision("1a5b6c7d8e91")
    security_alert_generation_revision = script.get_revision("2b6c7d8e9f02")
    current_merge_revision = script.get_revision("c7f8a9b0d123")
    deployment_browser_policy_revision = script.get_revision("fd4e5f6a7b89")
    collection_sync_revision = script.get_revision("a7b8c9d0e1f2")
    audit_event_outbox_revision = script.get_revision("a8b9c0d1e2f3")
    audit_workflow_correlation_revision = script.get_revision("a9b0c1d2e3f4")
    document_ingestion_job_revision = script.get_revision("aa0b1c2d3e4f")
    agent_builder_intent_usage_revision = script.get_revision("a8c9d0e1f2a3")
    conversation_memory_revision = script.get_revision("ab1c2d3e4f50")
    agent_builder_memory_merge_revision = script.get_revision("ac2d3e4f5061")
    app_auth_secret_revision = script.get_revision("b0c1d2e3f4a5")
    routing_bootstrap_inputs_revision = script.get_revision("ba6f5c4d3e2f")
    routing_performance_revision = script.get_revision("bb7c8d9e0f13")
    routing_bootstrap_artifacts_revision = script.get_revision("bc8d9e0f1a24")
    routing_global_profiles_revision = script.get_revision("bd9e0f1a2b35")
    retired_input_cohort_cleanup_revision = script.get_revision("c6f8a1b2d3e4")
    workflow_node_secret_revision = script.get_revision("f5b6c7d8e9fa")
    learning_label_feature_hash_revision = script.get_revision("c3d4e5f6a7b8")
    routing_learning_task_requirements_revision = script.get_revision("c4e5f6a7b8c9")
    routing_prelearning_revision = script.get_revision("b05c6d7e8f94")
    provider_execution_revision = script.get_revision("ad1e2f3a4b5c")
    canonical_node_location_revision = script.get_revision("ae2f3a4b5c6d")
    routing_learner_revision = script.get_revision("c06d7e8f9a15")

    assert safe_metadata_revision.down_revision == "fa7b8c9d0e12"
    assert set(merged_revision.down_revision) == {"fa7c8d9e0f12", "ff3a4b5c6d78"}
    assert hardened_revision.down_revision == "ff4b5c6d7e89"
    assert mail_credential_revision.down_revision == "ff5c6d7e8f90"
    assert mail_processing_revision.down_revision == "fc1d2e3f4a5b"
    assert security_alert_revision.down_revision == "fd2e3f4a5b67"
    assert security_alert_indexes_revision.down_revision == "a06b7c8d9e10"
    assert security_alert_watermark_revision.down_revision == "a17c8d9e0f21"
    assert knowledge_permissions_revision.down_revision == "b28d9e0f1a32"
    assert external_effect_revision.down_revision == "b39e0f1a2b43"
    assert recommendation_revision.down_revision == "fe3f4a5b6c78"
    assert repair_revision.down_revision == "fc9a1b2c3d4e"
    assert security_alert_episode_revision.down_revision == "fd0e1f2a3b4c"
    assert security_alert_outbox_revision.down_revision == "fe4a5b6c7d89"
    assert internal_chatbot_revision.down_revision == "c05d6e7f8a90"
    assert index_alignment_revision.down_revision == "fc9d0e1f2a34"
    assert configuration_preflight_revision.down_revision == "fd3e4f5a6b78"

    internal_chatbot_source = Path(internal_chatbot_revision.path).read_text(
        encoding="utf-8"
    )
    assert 'op.execute("COMMIT")' not in internal_chatbot_source

    heads = script.get_heads()
    assert len(heads) == 1
    ancestry = {
        revision.revision
        for revision in script.iterate_revisions(heads[0], "base")
    }
    assert "fa7c8d9e0f12" in ancestry
    assert "c05d6e7f8a90" in ancestry
    assert "fc9d0e1f2a34" in ancestry
    assert "fd3e4f5a6b78" in ancestry
    assert "0f4a5b6c7d89" in ancestry
    assert adaptive_routing_revision.down_revision == "0f4a5b6c7d89"
    assert adaptive_routing_controls_revision.down_revision == "f1c2d3e4f5a6"
    assert security_alert_receipt_revision.down_revision == "0f4a5b6c7d89"
    assert security_alert_generation_revision.down_revision == "1a5b6c7d8e91"
    assert set(current_merge_revision.down_revision) == {
        "2b6c7d8e9f02",
        "a6f4d2c8e1b7",
    }
    assert deployment_browser_policy_revision.down_revision == "c7f8a9b0d123"
    assert collection_sync_revision.down_revision == "c1e5f4a3c2d4"
    assert audit_event_outbox_revision.down_revision == "a7b8c9d0e1f2"
    assert audit_workflow_correlation_revision.down_revision == "a8b9c0d1e2f3"
    assert document_ingestion_job_revision.down_revision == "a9b0c1d2e3f4"
    assert agent_builder_intent_usage_revision.down_revision == "aa0b1c2d3e4f"
    assert conversation_memory_revision.down_revision == "aa0b1c2d3e4f"
    assert set(agent_builder_memory_merge_revision.down_revision) == {
        "a8c9d0e1f2a3",
        "ab1c2d3e4f50",
    }
    assert app_auth_secret_revision.down_revision == "ac2d3e4f5061"
    assert routing_bootstrap_inputs_revision.down_revision == "c2e8f4a91d67"
    assert routing_performance_revision.down_revision == "ba6f5c4d3e2f"
    assert routing_bootstrap_artifacts_revision.down_revision == "bb7c8d9e0f13"
    assert routing_global_profiles_revision.down_revision == "bc8d9e0f1a24"
    assert "2b6c7d8e9f02" in ancestry
    assert "a6f4d2c8e1b7" in ancestry
    assert "a9b0c1d2e3f4" in ancestry
    assert "aa0b1c2d3e4f" in ancestry
    assert "ab1c2d3e4f50" in ancestry
    assert "b0c1d2e3f4a5" in ancestry
    assert retired_input_cohort_cleanup_revision.down_revision == "bd9e0f1a2b35"
    assert workflow_node_secret_revision.down_revision == "f4a5b6c7d8e9"
    assert learning_label_feature_hash_revision.down_revision == "f5b6c7d8e9fa"
    assert routing_learning_task_requirements_revision.down_revision == "c3d4e5f6a7b8"
    assert set(routing_prelearning_revision.down_revision) == {
        "af4a5b6c7d83",
        "c4e5f6a7b8c9",
    }
    assert provider_execution_revision.down_revision == "b05c6d7e8f94"
    assert canonical_node_location_revision.down_revision == "ad1e2f3a4b5c"
    assert "b05c6d7e8f94" in ancestry
    assert "ad1e2f3a4b5c" in ancestry
    assert "ae2f3a4b5c6d" in ancestry
    assert routing_learner_revision.down_revision == "ae2f3a4b5c6d"
    assert "c06d7e8f9a15" in ancestry


def test_demo_knowledge_seed_contract_has_ids_and_permission_specs():
    document_keys = {spec.key for spec in demo_seed.DEMO_DOCUMENT_SPECS}
    public_keys = {
        spec.key
        for spec in demo_seed.DEMO_DOCUMENT_SPECS
        if spec.source_tier == "public"
    }
    retired_keys = set(demo_seed.RETIRED_INTERNAL_DOCUMENT_KB_IDS)

    assert document_keys == public_keys
    assert document_keys <= set(demo_seed.KB_IDS)
    assert document_keys <= set(demo_seed.DOCUMENT_IDS)
    assert document_keys <= set(demo_seed.COLLECTION_ITEM_IDS)
    assert retired_keys.isdisjoint(demo_seed.KB_IDS)
    assert retired_keys.isdisjoint(demo_seed.DOCUMENT_IDS)
    assert retired_keys.isdisjoint(demo_seed.COLLECTION_ITEM_IDS)
    assert "internal_onboarding" not in demo_seed.COLLECTION_IDS
    assert all(
        spec.collection_key == "legal_public"
        for spec in demo_seed.DEMO_DOCUMENT_SPECS
    )

    permission_specs = set(demo_seed._demo_team_knowledge_permission_specs())
    for key in public_keys:
        assert (key, "platform_admin", "manager") in permission_specs
        assert (key, "customer_support_ops", "operator") in permission_specs
    assert all(kb_key in demo_seed.KB_IDS for kb_key, _, _ in permission_specs)
    assert retired_keys.isdisjoint(kb_key for kb_key, _, _ in permission_specs)

    collection_permission_specs = set(
        demo_seed._demo_team_knowledge_collection_permission_specs()
    )
    assert (
        "legal_public",
        "ai_builder_onboarding",
        "route",
    ) in collection_permission_specs
    assert all(
        collection_key in demo_seed.COLLECTION_IDS
        for collection_key, _, _ in collection_permission_specs
    )
    assert (
        "hr_policies",
        "hr_knowledge_users",
        "route",
    ) in collection_permission_specs
    assert (
        "hr_policies",
        "platform_admin",
        "read",
    ) in collection_permission_specs
    assert (
        "hr_welfare",
        "hr_knowledge_users",
        "operator",
    ) in permission_specs
    assert (
        "hr_welfare",
        "platform_admin",
        "manager",
    ) in permission_specs


def test_removed_internal_document_rows_keep_only_reset_cleanup_ids():
    document_keys = {spec.key for spec in demo_seed.DEMO_DOCUMENT_SPECS}
    filenames = {spec.filename for spec in demo_seed.DEMO_DOCUMENT_SPECS}
    retired_keys = set(demo_seed.RETIRED_INTERNAL_DOCUMENT_KB_IDS)

    assert {
        "internal_developer_compensation_band",
        "internal_compensation_access_policy",
    } <= retired_keys
    assert retired_keys.isdisjoint(document_keys)
    assert all("개인별 실제 연봉" not in filename for filename in filenames)
    assert all("personal_salary" not in key for key in document_keys)


def test_committed_knowledge_fixture_matches_demo_seed_contract():
    fixture = demo_seed._read_demo_knowledge_fixture()
    document_keys = {spec.key for spec in demo_seed.INDEXED_DEMO_DOCUMENT_SPECS}
    chunk_total = sum(len(chunks) for chunks in fixture["chunks_by_document"].values())

    assert set(fixture["documents"]) == document_keys
    assert set(fixture["chunks_by_document"]) == document_keys
    assert chunk_total >= len(document_keys)
    assert fixture["documents"]["legal_fair_hiring"]["filename"] == (
        "채용절차의 공정화에 관한 법률(법률)(제17326호)(20200526).pdf"
    )
    for document_key, chunks in fixture["chunks_by_document"].items():
        assert chunks, document_key
        assert len(chunks[0]["embedding"]) == demo_seed.DEMO_EMBEDDING_DIMENSION


def test_committed_fixture_excludes_retired_internal_documents():
    fixture = demo_seed._read_demo_knowledge_fixture()
    retired_keys = set(demo_seed.RETIRED_INTERNAL_DOCUMENT_KB_IDS)

    assert fixture["header"]["fixture_version"] == demo_seed.DEMO_SEED_VERSION
    assert retired_keys.isdisjoint(fixture["documents"])
    assert retired_keys.isdisjoint(fixture["chunks_by_document"])


def test_committed_knowledge_fixture_does_not_contain_secret_like_values():
    with gzip.open(
        demo_seed.DEMO_KNOWLEDGE_FIXTURE_PATH,
        "rt",
        encoding="utf-8",
    ) as handle:
        for line in handle:
            assert "sk-" not in line
            assert "OPENAI_API_KEY" not in line
            assert "ENCRYPTION_KEY" not in line
