from apps.shared.services.node_config_fingerprint import llm_node_config_fingerprint


def test_llm_node_fingerprint_tracks_runtime_settings_but_ignores_ui_state():
    base = {
        "model_id": "gpt-4.1",
        "parameters": {"max_tokens": 800},
        "system_prompt": "safe prompt",
        "selected_tab": "basic",
    }
    same_runtime = {**base, "selected_tab": "advanced", "panel_width": 480}
    changed_runtime = {**base, "parameters": {"max_tokens": 400}}

    assert llm_node_config_fingerprint(base) == llm_node_config_fingerprint(
        same_runtime
    )
    assert llm_node_config_fingerprint(base) != llm_node_config_fingerprint(
        changed_runtime
    )


def test_llm_node_fingerprint_tracks_model_routing_refresh_interval():
    first = {
        "model_id": "gpt-4.1",
        "model_routing_policy": {"refresh": {"refresh_every_runs": 20}},
    }
    second = {
        "model_id": "gpt-4.1",
        "model_routing_policy": {"refresh": {"refresh_every_runs": 50}},
    }

    assert llm_node_config_fingerprint(first) != llm_node_config_fingerprint(second)


def test_llm_node_fingerprint_tracks_knowledge_collections():
    """RAG collection 교체는 같은 검증 evidence를 재사용하면 안 된다."""
    first = {
        "model_id": "gpt-4.1",
        "knowledgeCollections": [{"id": "collection-a"}],
    }
    second = {
        "model_id": "gpt-4.1",
        "knowledgeCollections": [{"id": "collection-b"}],
    }

    assert llm_node_config_fingerprint(first) != llm_node_config_fingerprint(second)
