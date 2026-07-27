from apps.gateway.api.api import api_router


def _registered_paths():
    paths = set()
    for included in api_router.routes:
        router = getattr(included, "original_router", None)
        context = getattr(included, "include_context", None)
        if router is None or context is None:
            path = getattr(included, "path", None)
            if path:
                paths.add(path)
            continue
        prefix = context.prefix
        paths.update(
            f"{prefix}{route.path}"
            for route in router.routes
            if getattr(route, "path", None)
        )
    return paths


def test_public_conversation_lifecycle_routes_are_not_registered():
    paths = _registered_paths()

    assert "/run-public/{url_slug}" in paths
    assert "/run-public/{url_slug}/conversations" not in paths
    assert "/run-public/{url_slug}/conversation/close" not in paths
    assert "/run-public/{url_slug}/conversation/reset" not in paths
    assert "/run-public/{url_slug}/conversation" not in paths
    assert "/run-public/{url_slug}/conversation/transcript" not in paths
    assert "/run-public/{url_slug}/conversation/turns/{turn_id}" not in paths
    assert "/run-public/{url_slug}/conversation/purge-status" not in paths
