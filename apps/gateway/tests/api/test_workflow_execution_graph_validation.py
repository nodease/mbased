import pytest
from fastapi import HTTPException

from apps.gateway.api.v1.endpoints.workflow import validate_execution_graph


def test_mail_acknowledgement_node_is_terminal_in_backend_execution_graph():
    graph = {
        "nodes": [
            {
                "id": "mail-ack",
                "type": "mailAcknowledgeNode",
                "position": {"x": 0, "y": 0},
                "data": {},
            },
            {
                "id": "http",
                "type": "httpRequestNode",
                "position": {"x": 320, "y": 0},
                "data": {},
            },
        ],
        "edges": [
            {
                "id": "edge-after-ack",
                "source": "mail-ack",
                "target": "http",
            }
        ],
    }

    with pytest.raises(HTTPException) as exc_info:
        validate_execution_graph(graph)

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "종료 노드에서는 다른 노드로 연결할 수 없습니다."
