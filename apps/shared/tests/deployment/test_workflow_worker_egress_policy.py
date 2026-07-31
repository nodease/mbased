import ast
from pathlib import Path

import yaml

REPOSITORY_ROOT = Path(__file__).resolve().parents[4]


def _read(relative_path: str) -> str:
    return (REPOSITORY_ROOT / relative_path).read_text(encoding="utf-8")


def test_helm_worker_policy_is_default_on_and_rejects_unsafe_catch_all() -> None:
    values = yaml.safe_load(_read("infra/helm/moduly/values.yaml"))
    production = yaml.safe_load(_read("infra/helm/moduly/values-production.yaml"))
    template = _read("infra/helm/moduly/templates/worker-networkpolicy.yaml")
    helpers = _read("infra/helm/moduly/templates/_helpers.tpl")

    assert values["worker"]["networkPolicy"]["enabled"] is True
    assert production["worker"]["networkPolicy"]["enabled"] is True
    assert production["worker"]["networkPolicy"]["externalDatabaseCidrs"] == [
        "10.0.0.0/16"
    ]
    assert 'define "moduly.validateExternalDependencyCidr"' in helpers
    assert 'include "moduly.validateExternalDependencyCidr" $cidr' in helpers
    assert (
        "external dependency CIDRs must use valid private networks or exact public hosts"
        in helpers
    )
    assert "externalDatabaseCidrs is required" in helpers
    assert "externalRedisCidrs is required" in helpers
    assert "external dependency CIDRs cannot be empty" in helpers
    assert "externalDatabaseCidrs is required" not in template
    assert ".Values.egressProxy.enabled" in template
    assert 'component" "egress-proxy' in template
    assert "port: 3129" in template
    assert "- {}" not in template
    assert "hostNetwork: true" not in _read(
        "infra/helm/moduly/templates/worker-deployment.yaml"
    )

    assert "cidr: 0.0.0.0/0" not in template
    assert "cidr: ::/0" not in template
    assert 'port: {{ include "moduly.postgresql.port" . }}' in template
    for port in {53, 6379, 8194}:
        assert f"port: {port}" in template
    for public_port in {80, 143, 443, 993}:
        assert f"port: {public_port}" not in template


def test_generic_http_production_path_cannot_construct_httpx_client_directly() -> None:
    guarded_files = (
        "apps/workflow_engine/adapters/providers/generic_http.py",
        "apps/workflow_engine/workflow/nodes/http/http_node.py",
    )

    for relative_path in guarded_files:
        tree = ast.parse(_read(relative_path), filename=relative_path)
        direct_clients = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "httpx"
            and node.func.attr == "Client"
        ]
        assert direct_clients == []

    node_source = _read("apps/workflow_engine/workflow/nodes/http/http_node.py")
    composition_source = _read("apps/workflow_engine/composition/generic_http.py")
    assert "build_generic_http_effect_adapter" in node_source
    assert "GuardedHttpxOutboundAdapter" in composition_source
