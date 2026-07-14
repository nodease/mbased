import gzip
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from apps.shared.db import demo_seed
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
        for spec in demo_seed.DEMO_DOCUMENT_SPECS
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
    source = tmp_path / "company_common_onboarding.pdf"
    source.write_bytes(b"%PDF-1.4\n")
    monkeypatch.setattr(demo_seed, "DEMO_ONBOARDING_PDF_DIR", tmp_path)
    spec = next(
        item
        for item in demo_seed.ONBOARDING_PDF_SPECS
        if item.key == "onboarding_company_common"
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
    assert str(demo_seed.KB_IDS["internal_leave_attendance"]) in kb_ids
    assert str(demo_seed.KB_IDS["internal_privacy_hr_records"]) in kb_ids
    assert str(demo_seed.KB_IDS["internal_developer_commit_convention"]) in kb_ids
    assert str(demo_seed.KB_IDS["internal_developer_compensation_band"]) in kb_ids
    assert str(demo_seed.KB_IDS["internal_compensation_access_policy"]) in kb_ids
    assert str(demo_seed.KB_IDS["legal_labor_standards"]) in kb_ids
    assert str(demo_seed.KB_IDS["legal_equal_employment"]) in kb_ids


def test_department_onboarding_graph_references_exact_rbac_demo_kbs():
    graph = demo_seed._department_onboarding_chatbot_graph()
    llm_node = next(node for node in graph["nodes"] if node["id"] == "llm-answer")

    assert llm_node["data"]["model_id"] == demo_seed.DEMO_CHAT_MINI_MODEL
    assert llm_node["data"]["scoreThreshold"] == 0.3
    assert llm_node["data"]["topK"] == 3
    assert [item["id"] for item in llm_node["data"]["knowledgeBases"]] == [
        str(demo_seed.KB_IDS["internal_onboarding"]),
        str(demo_seed.KB_IDS["internal_developer_onboarding_rules"]),
        str(demo_seed.KB_IDS["internal_planning_onboarding_guide"]),
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
        ("internal_onboarding", "department_development", "operator"),
        ("internal_onboarding", "department_planning", "operator"),
        (
            "internal_developer_onboarding_rules",
            "department_development",
            "operator",
        ),
        (
            "internal_planning_onboarding_guide",
            "department_planning",
            "operator",
        ),
    }


def test_department_onboarding_app_is_active_internal_chatbot(monkeypatch):
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
    ):
        calls[key] = {
            "name": name,
            "description": description,
            "owner_key": owner_key,
            "graph": graph,
            "deployed": deployed,
            "deployment_type": deployment_type,
        }
        return key

    monkeypatch.setattr(demo_seed, "_upsert_app_workflow", capture)

    workflows = demo_seed._seed_apps_and_workflows(object())

    assert workflows["department_onboarding_chatbot"] == (
        "department_onboarding_chatbot"
    )
    call = calls["department_onboarding_chatbot"]
    assert call["owner_key"] == "admin"
    assert call["deployed"] is True
    assert call["deployment_type"] is demo_seed.DeploymentType.INTERNAL_CHATBOT


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

    assert {key: spec.filename for key, spec in specs.items()} == {
        "onboarding_company_common": "company_common_onboarding.pdf",
        "onboarding_platform": "platform_team_onboarding_v4.pdf",
        "onboarding_sales": "sales_team_onboarding_v2.pdf",
        "onboarding_finance": "finance_team_onboarding_v3.pdf",
    }
    assert specs["onboarding_platform"].source_page_indexes == (0, 1, 2)
    assert all(
        (demo_seed.DEMO_ONBOARDING_PDF_DIR / spec.filename).is_file()
        for spec in specs.values()
    )
    assert not set(specs).intersection(
        spec.key for spec in demo_seed.DEMO_DOCUMENT_SPECS
    )


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
        ("onboarding_company_common", "onboarding_platform", "operator"),
        ("onboarding_company_common", "onboarding_sales", "operator"),
        ("onboarding_company_common", "onboarding_finance", "operator"),
        ("onboarding_company_common", "onboarding_people", "manager"),
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


def test_team_onboarding_access_control_app_is_active_internal_chatbot(monkeypatch):
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
    ):
        calls[key] = {
            "name": name,
            "description": description,
            "owner_key": owner_key,
            "graph": graph,
            "deployed": deployed,
            "deployment_type": deployment_type,
        }
        return key

    monkeypatch.setattr(demo_seed, "_upsert_app_workflow", capture)

    workflows = demo_seed._seed_apps_and_workflows(object())

    assert workflows["team_onboarding_access_control"] == (
        "team_onboarding_access_control"
    )
    call = calls["team_onboarding_access_control"]
    assert call["name"] == "팀별 온보딩 문서 접근 제어 데모"
    assert call["owner_key"] == "onboarding_people_manager"
    assert call["deployed"] is True
    assert call["deployment_type"] is demo_seed.DeploymentType.INTERNAL_CHATBOT


def test_demo_summary_reports_seeded_knowledge_documents():
    summary = demo_seed.demo_summary("demo")

    assert summary["knowledge_documents"] == {
        "public_law_pdfs": 7,
        "internal_markdown_docs": 11,
        "bundled_onboarding_pdfs": 4,
        "embedding_model": demo_seed.DEMO_EMBEDDING_MODEL,
        "fixture": demo_seed.DEMO_KNOWLEDGE_FIXTURE_PATH.as_posix(),
    }


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
    assert set(demo_seed.CREDENTIAL_MODEL_REL_IDS) == {
        demo_seed.DEMO_CHAT_MODEL,
        demo_seed.DEMO_CHAT_MINI_MODEL,
        demo_seed.DEMO_MODEL_ROUTER_BASE_MODEL,
        demo_seed.DEMO_MODEL_ROUTER_FALLBACK_MODEL,
        demo_seed.DEMO_MODEL_ROUTER_CHEAP_MODEL,
        demo_seed.DEMO_MODEL_ROUTER_BALANCED_MODEL,
        demo_seed.DEMO_EMBEDDING_MODEL,
        demo_seed.DEMO_MODEL_ROUTER_EMBEDDING_MODEL,
    }


def test_model_router_demo_workflow_enables_versioned_semantic_cohorts():
    graph = demo_seed._model_router_ticket_ops_graph()
    llm_node = next(node for node in graph["nodes"] if node["id"] == "llm-triage")
    data = llm_node["data"]

    assert data["auto_model_routing"] is True
    assert data["model_id"] == "gpt-4.1"
    assert data["fallback_model_id"] == "gpt-4.1-mini"
    semantic_router = data["model_routing_context"]["semantic_router"]
    assert semantic_router["route_catalog_version"] == "demo-ticket-routing-v7"
    assert (
        semantic_router["encoder_model_id"]
        == demo_seed.DEMO_MODEL_ROUTER_EMBEDDING_MODEL
    )
    assert semantic_router["input_paths"] == ["webhook-ticket.message"]
    assert semantic_router["aggregation"] == "centroid"
    assert semantic_router["min_margin"] == 0.005

    routes = semantic_router["routes"]
    assert {route["cohort_id"] for route in routes} == {
        "routine_support",
        "account_billing",
        "high_risk",
    }
    high_risk = next(route for route in routes if route["cohort_id"] == "high_risk")
    assert high_risk["safety_override"] is True
    assert high_risk["lexical_override_threshold"] == 1.0
    assert {signal["term"] for signal in high_risk["lexical_signals"]} >= {
        "계정 탈취",
        "변조",
        "법무 검토",
        "환불 분쟁",
        "unauthorized access",
    }
    assert all(len(route["utterances"]) >= 12 for route in routes)
    utterances = [
        utterance for route in routes for utterance in route["utterances"]
    ]
    assert len(utterances) == len(set(utterances))
    assert all(0 < route["threshold"] < 1 for route in routes)
    assert {route["cohort_id"]: route["threshold"] for route in routes} == {
        "routine_support": 0.35,
        "account_billing": 0.38,
        "high_risk": 0.34,
    }


def test_ticket_ops_input_schema_matches_webhook_mappings():
    graph = demo_seed._ticket_ops_graph()

    schema = demo_seed._input_schema_from_graph(graph)

    assert schema == {
        "variables": [
            {"name": "message", "type": "text", "label": "message"},
            {"name": "customerTier", "type": "text", "label": "customerTier"},
        ]
    }


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
    assert "2b6c7d8e9f02" in ancestry
    assert "a6f4d2c8e1b7" in ancestry
    assert script.get_heads() == ["fe5f6a7b8c90"]


def test_demo_knowledge_seed_contract_has_ids_and_permission_specs():
    document_keys = {spec.key for spec in demo_seed.DEMO_DOCUMENT_SPECS}
    public_keys = {
        spec.key
        for spec in demo_seed.DEMO_DOCUMENT_SPECS
        if spec.source_tier == "public"
    }
    private_keys = document_keys - public_keys

    assert document_keys <= set(demo_seed.KB_IDS)
    assert document_keys <= set(demo_seed.DOCUMENT_IDS)
    assert document_keys <= set(demo_seed.COLLECTION_ITEM_IDS)
    assert all(
        spec.collection_key == "legal_public"
        for spec in demo_seed.DEMO_DOCUMENT_SPECS
        if spec.source_tier == "public"
    )
    assert all(
        spec.collection_key == "internal_onboarding"
        for spec in demo_seed.DEMO_DOCUMENT_SPECS
        if spec.source_tier != "public"
    )

    permission_specs = set(demo_seed._demo_team_knowledge_permission_specs())
    for key in public_keys:
        assert (key, "platform_admin", "manager") in permission_specs
        assert (key, "customer_support_ops", "operator") in permission_specs
    for key in private_keys:
        assert (key, "platform_admin", "manager") in permission_specs
    assert {
        "internal_onboarding",
        "internal_developer_onboarding_rules",
        "internal_planning_onboarding_guide",
    } <= document_keys
    assert (
        "internal_developer_compensation_band",
        "ai_builder_onboarding",
        "operator",
    ) in permission_specs
    assert (
        "internal_compensation_access_policy",
        "ai_builder_onboarding",
        "operator",
    ) in permission_specs
    assert (
        "internal_privacy_hr_records",
        "ai_builder_onboarding",
        "operator",
    ) not in permission_specs
    assert (
        "internal_privacy_hr_records",
        "platform_admin",
        "manager",
    ) in permission_specs
    assert (
        "internal_privacy_hr_records",
        "hr_knowledge_users",
        "operator",
    ) in permission_specs
    for key in (
        "internal_onboarding",
        "internal_leave_attendance",
        "internal_benefits",
    ):
        assert (key, "tester_builder", "operator") in permission_specs

    collection_permission_specs = set(
        demo_seed._demo_team_knowledge_collection_permission_specs()
    )
    assert (
        "legal_public",
        "ai_builder_onboarding",
        "route",
    ) in collection_permission_specs
    assert (
        "internal_onboarding",
        "customer_support_ops",
        "read",
    ) in collection_permission_specs
    assert (
        "internal_onboarding",
        "tester_builder",
        "read",
    ) in collection_permission_specs
    assert (
        "internal_onboarding",
        "tester_builder",
        "route",
    ) in collection_permission_specs


def test_demo_knowledge_seed_excludes_personal_salary_records():
    document_keys = {spec.key for spec in demo_seed.DEMO_DOCUMENT_SPECS}
    filenames = {spec.filename for spec in demo_seed.DEMO_DOCUMENT_SPECS}

    assert "internal_developer_compensation_band" in document_keys
    assert "internal_compensation_access_policy" in document_keys
    assert all("개인별 실제 연봉" not in filename for filename in filenames)
    assert all("personal_salary" not in key for key in document_keys)


def test_committed_knowledge_fixture_matches_demo_seed_contract():
    fixture = demo_seed._read_demo_knowledge_fixture()
    document_keys = {spec.key for spec in demo_seed.DEMO_DOCUMENT_SPECS}
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


def test_committed_fixture_supports_department_onboarding_demo_questions():
    fixture = demo_seed._read_demo_knowledge_fixture()

    common_content = "\n".join(
        chunk["content"]
        for chunk in fixture["chunks_by_document"]["internal_onboarding"]
    )
    developer_content = "\n".join(
        chunk["content"]
        for chunk in fixture["chunks_by_document"][
            "internal_developer_onboarding_rules"
        ]
    )
    planning_content = "\n".join(
        chunk["content"]
        for chunk in fixture["chunks_by_document"][
            "internal_planning_onboarding_guide"
        ]
    )

    assert "휴가와 프로젝트 운영 규정이 충돌할 때" in common_content
    assert "repository 접근 권한" in developer_content
    assert "PRD에는 문제 정의" in planning_content


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
