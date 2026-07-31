from types import SimpleNamespace

from apps.shared.schemas.workflow_citation import WORKFLOW_CITATION_RESULT_KEY
from apps.workflow_engine.workflow.core.workflow_engine import WorkflowEngine


def _engine():
    engine = WorkflowEngine.__new__(WorkflowEngine)
    engine.is_subworkflow = False
    engine.node_schemas = {
        "llm-used": SimpleNamespace(type="llmNode", data={}),
        "middle": SimpleNamespace(type="templateNode", data={}),
        "llm-control-only": SimpleNamespace(type="llmNode", data={}),
        "answer": SimpleNamespace(type="answerNode", data={}),
    }
    engine.nodes_by_type = {"answerNode": ["answer"]}
    engine.data_dependencies = {
        "llm-used": set(),
        "middle": {"llm-used"},
        "llm-control-only": set(),
        "answer": {"middle"},
    }
    engine.node_instances = {
        "llm-used": SimpleNamespace(
            _user_citations={
                "version": 1,
                "items": [
                    {
                        "citation_id": "evidence-1",
                        "evidence_rank": 1,
                        "label": "휴가 정책",
                        "page_number": 2,
                        "section": None,
                        "content_preview": "미리보기",
                    }
                ],
            }
        ),
        "llm-control-only": SimpleNamespace(
            _user_citations={
                "version": 1,
                "items": [
                    {
                        "citation_id": "evidence-1",
                        "evidence_rank": 1,
                        "label": "노출되면 안 되는 문서",
                        "page_number": None,
                        "section": None,
                        "content_preview": None,
                    }
                ],
            }
        ),
    }
    return engine


def test_final_response_contains_only_answer_data_lineage_citations():
    engine = _engine()
    results = {
        "llm-used": {},
        "middle": {},
        "llm-control-only": {},
        "answer": {"answer": "응답"},
    }

    response = engine._with_user_citations(results["answer"], results)

    assert response["answer"] == "응답"
    assert [
        item["label"] for item in response[WORKFLOW_CITATION_RESULT_KEY]["items"]
    ] == ["휴가 정책"]


def test_reserved_key_is_server_owned_and_durable_output_drops_sidecar():
    engine = _engine()
    results = {"llm-used": {}, "middle": {}, "answer": {"answer": "응답"}}
    response = engine._with_user_citations({"answer": "응답"}, results)

    assert response[WORKFLOW_CITATION_RESULT_KEY]["version"] == 1
    durable = engine._without_user_citations(response)
    assert WORKFLOW_CITATION_RESULT_KEY not in durable


def test_reserved_node_id_collision_preserves_stream_output_and_skips_citations():
    engine = _engine()
    engine.node_schemas[WORKFLOW_CITATION_RESULT_KEY] = SimpleNamespace(
        type="templateNode", data={}
    )
    engine.data_dependencies[WORKFLOW_CITATION_RESULT_KEY] = set()
    stream_results = {
        "llm-used": {},
        "middle": {},
        "answer": {"answer": "응답"},
        WORKFLOW_CITATION_RESULT_KEY: {"text": "legacy node output"},
    }

    response = engine._with_user_citations(stream_results, stream_results)

    assert response == stream_results
    assert engine._without_user_citations(response) == stream_results


def test_subworkflow_and_malformed_envelope_do_not_project_citations():
    engine = _engine()
    engine.node_instances["llm-used"]._user_citations = {"version": 99, "items": []}
    results = {"llm-used": {}, "middle": {}, "answer": {"answer": "응답"}}

    assert WORKFLOW_CITATION_RESULT_KEY not in engine._with_user_citations(
        results["answer"], results
    )

    engine.is_subworkflow = True
    assert engine._with_user_citations(
        {
            "answer": "응답",
            WORKFLOW_CITATION_RESULT_KEY: {"spoofed": True},
        },
        results,
    ) == {
        "answer": "응답",
        WORKFLOW_CITATION_RESULT_KEY: {"spoofed": True},
    }


def test_skipped_llm_node_does_not_reuse_previous_ephemeral_citation():
    engine = _engine()
    results = {"middle": {}, "answer": {"answer": "응답"}}

    response = engine._with_user_citations(results["answer"], results)

    assert WORKFLOW_CITATION_RESULT_KEY not in response


def test_code_node_source_dependency_keeps_llm_citation_in_answer_lineage():
    engine = _engine()
    engine.node_schemas["code"] = SimpleNamespace(
        type="codeNode",
        data={"inputs": [{"name": "answer", "source": "llm-used.text"}]},
    )
    engine.data_dependencies["code"] = engine._extract_value_selectors(
        engine.node_schemas["code"]
    )
    engine.data_dependencies["answer"] = {"code"}
    results = {"llm-used": {}, "code": {}, "answer": {"answer": "응답"}}

    response = engine._with_user_citations(results["answer"], results)

    assert engine.data_dependencies["code"] == {"llm-used"}
    assert response[WORKFLOW_CITATION_RESULT_KEY]["items"][0]["label"] == "휴가 정책"
