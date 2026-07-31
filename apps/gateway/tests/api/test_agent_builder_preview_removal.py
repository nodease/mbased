from apps.gateway.api.v1.endpoints.agent_builder import router


def test_agent_builder_router_does_not_expose_legacy_preview_or_apply_routes():
    paths = {route.path for route in router.routes}

    assert not any("/drafts/" in path for path in paths)
    assert not any("preview-opened" in path for path in paths)
    assert "/sessions/{session_id}/knowledge-selection" in paths
    assert "/sessions/{session_id}/parameter-groups/{group_id}/cancel" in paths
