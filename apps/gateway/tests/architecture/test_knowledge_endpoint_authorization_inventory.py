import ast
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[4]
KNOWLEDGE_ENDPOINT = ROOT / "apps/gateway/api/v1/endpoints/knowledge.py"
RAG_ENDPOINT = ROOT / "apps/gateway/api/v1/endpoints/rag.py"
INGESTION_SERVICE = ROOT / "apps/gateway/services/ingestion/service.py"
CLIENT_DOCUMENT_SETTINGS = (
    ROOT
    / "apps/client/app/dashboard/knowledge/[id]/document/[documentId]/page.tsx"
)
CLIENT_API_SOURCE_VIEWER = (
    ROOT
    / "apps/client/app/features/knowledge/components/ingestion-views/ApiSourceViewer.tsx"
)


def _module(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def _function(path: Path, function_name: str) -> ast.FunctionDef | ast.AsyncFunctionDef:
    for node in _module(path).body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name == function_name:
                return node
    raise AssertionError(f"missing endpoint function: {function_name}")


def _call_name(call: ast.Call) -> str | None:
    if isinstance(call.func, ast.Name):
        return call.func.id
    if isinstance(call.func, ast.Attribute):
        return call.func.attr
    return None


def _calls(function: ast.FunctionDef | ast.AsyncFunctionDef, call_name: str):
    return [
        node
        for node in ast.walk(function)
        if isinstance(node, ast.Call) and _call_name(node) == call_name
    ]


def _assert_positional_action(
    path: Path,
    function_name: str,
    call_name: str,
    action_index: int,
    expected_action: str,
) -> None:
    calls = _calls(_function(path, function_name), call_name)
    assert calls, f"{function_name} must call {call_name}"
    assert any(
        len(call.args) > action_index
        and isinstance(call.args[action_index], ast.Constant)
        and call.args[action_index].value == expected_action
        for call in calls
    ), f"{function_name} must authorize {expected_action} via {call_name}"


@pytest.mark.parametrize(
    ("function_name", "expected_action"),
    [
        ("get_knowledge_base", "read"),
        ("_manageable_knowledge_base", "manage"),
        ("update_knowledge_base", "write"),
        ("archive_knowledge_base", "manage"),
        ("restore_knowledge_base", "manage"),
        ("delete_knowledge_base", "manage"),
    ],
)
def test_knowledge_base_endpoints_use_canonical_actions(
    function_name: str,
    expected_action: str,
):
    _assert_positional_action(
        KNOWLEDGE_ENDPOINT,
        function_name,
        "load_kb",
        1,
        expected_action,
    )


@pytest.mark.parametrize(
    ("function_name", "expected_action"),
    [
        ("get_document", "read"),
        ("get_document_edit_config", "write"),
        ("get_document_content", "content_read"),
        ("process_document", "write"),
        ("preview_document_chunking", "write"),
        ("sync_document", "write"),
    ],
)
def test_knowledge_document_endpoints_use_canonical_actions(
    function_name: str,
    expected_action: str,
):
    _assert_positional_action(
        KNOWLEDGE_ENDPOINT,
        function_name,
        "_authorized_knowledge_document",
        2,
        expected_action,
    )


@pytest.mark.parametrize(
    ("function_name", "expected_action"),
    [
        ("analyze_document", "write"),
        ("confirm_document_parsing", "write"),
        ("delete_document", "write"),
        ("get_document_progress", "read"),
    ],
)
def test_rag_document_endpoints_use_canonical_actions(
    function_name: str,
    expected_action: str,
):
    _assert_positional_action(
        RAG_ENDPOINT,
        function_name,
        "_authorize_knowledge_document_action",
        5,
        expected_action,
    )


def test_upload_existing_kb_uses_write_and_search_uses_use_gate():
    _assert_positional_action(
        RAG_ENDPOINT,
        "_load_writable_knowledge_base",
        "load_kb",
        1,
        "write",
    )
    assert _calls(_function(RAG_ENDPOINT, "upload_document"), "_get_or_create_knowledge_base")
    assert _calls(
        _function(RAG_ENDPOINT, "_get_or_create_knowledge_base"),
        "_load_writable_knowledge_base",
    )
    assert _calls(_function(RAG_ENDPOINT, "search_test_chat"), "_authorize_rag_use")
    assert _calls(_function(RAG_ENDPOINT, "search_test_pure"), "_authorize_rag_use")


def test_document_creation_uses_central_registration_service():
    upload_function = _function(RAG_ENDPOINT, "upload_document")
    assert _calls(upload_function, "register_initial_document")
    assert not _calls(upload_function, "Document")
    assert "def create_pending_document(" not in INGESTION_SERVICE.read_text(
        encoding="utf-8"
    )


def test_presigned_upload_is_scoped_to_writable_kb_and_empty_slot():
    function = _function(RAG_ENDPOINT, "generate_presigned_url")
    assert _calls(function, "_load_writable_knowledge_base")
    assert _calls(function, "_ensure_initial_document_slot")


def test_sync_domain_override_is_bounded_and_processing_actions_are_audited():
    sync_function = _function(KNOWLEDGE_ENDPOINT, "sync_document")
    authorization_call = _calls(sync_function, "_authorized_knowledge_document")[0]
    domain_keyword = next(
        keyword
        for keyword in authorization_call.keywords
        if keyword.arg == "domain_action"
    )
    assert isinstance(domain_keyword.value, ast.Constant)
    assert domain_keyword.value.value == "sync_manage"

    for path, function_name in [
        (KNOWLEDGE_ENDPOINT, "sync_document"),
        (RAG_ENDPOINT, "confirm_document_parsing"),
    ]:
        function = _function(path, function_name)
        assert any(
            isinstance(decorator, ast.Call)
            and _call_name(decorator) == "audit"
            and decorator.args
            and isinstance(decorator.args[0], ast.Attribute)
            and decorator.args[0].attr == "DOCUMENT_PROCESS"
            for decorator in function.decorator_list
        )


def test_production_knowledge_services_have_no_owner_authorization_predicate():
    paths = [
        KNOWLEDGE_ENDPOINT,
        RAG_ENDPOINT,
        ROOT / "apps/gateway/services/knowledge_base_query_service.py",
        ROOT / "apps/gateway/services/knowledge_lifecycle_service.py",
    ]

    for path in paths:
        source = path.read_text(encoding="utf-8")
        assert "KnowledgeBase.user_id" not in source, path
        assert "delete_owned_knowledge_base" not in source, path


def test_client_does_not_restore_raw_api_config_from_read_metadata_or_storage():
    settings_source = CLIENT_DOCUMENT_SETTINGS.read_text(encoding="utf-8")
    viewer_source = CLIENT_API_SOURCE_VIEWER.read_text(encoding="utf-8")

    assert "sessionStorage" not in settings_source
    assert "api_preview" not in settings_source
    assert "meta_info?.api_config" not in settings_source
    assert "JSON.stringify(apiConfig" not in viewer_source
