"""LLM 노드 기억 조회 스코프 테스트.

docs/features/chatbot-deployment/test_cases.md:
- execution_context에 conversation_id가 있으면(챗봇 공개 실행 등) 기억 조회는
  workflow_id + conversation_id로 격리되고 user_id 필터는 사용하지 않는다.
  (공개 실행은 user_id가 앱 소유자로 고정되어 격리 기준이 될 수 없다.)
- conversation_id가 없으면 기존 workflow_id + user_id 스코프를 유지한다(하위호환).
- memory_mode가 꺼져 있으면 쿼리 자체를 하지 않는다.
"""

from uuid import uuid4

from apps.workflow_engine.workflow.nodes.llm.entities import LLMNodeData
from apps.workflow_engine.workflow.nodes.llm.llm_node import LLMNode


def _make_node(execution_context):
    data = LLMNodeData(title="LLM", model_id="gpt-4o-mini")
    return LLMNode("llm-1", data, execution_context)


def test_memory_scoped_by_conversation_id_when_present():
    session = _RecordingSession()
    node = _make_node(
        {
            "memory_mode": True,
            "workflow_id": str(uuid4()),
            "user_id": str(uuid4()),
            "conversation_id": "conv-A",
            "workflow_run_id": str(uuid4()),
            "db": session,
        }
    )

    # runs가 비어 있어 요약 없이 None을 반환하지만, 그 전에 WorkflowRun 쿼리 필터는 기록된다.
    assert node._build_memory_summary() is None

    cols = session.run_filter_columns
    assert cols is not None
    assert any(c.endswith(".conversation_id") for c in cols)
    assert not any(c.endswith(".user_id") for c in cols)


def test_memory_scoped_by_user_id_without_conversation_id():
    session = _RecordingSession()
    node = _make_node(
        {
            "memory_mode": True,
            "workflow_id": str(uuid4()),
            "user_id": str(uuid4()),
            "workflow_run_id": str(uuid4()),
            "db": session,
        }
    )

    assert node._build_memory_summary() is None

    cols = session.run_filter_columns
    assert cols is not None
    assert any(c.endswith(".user_id") for c in cols)
    assert not any(c.endswith(".conversation_id") for c in cols)


def test_memory_disabled_does_not_query():
    session = _RecordingSession()
    node = _make_node(
        {
            "memory_mode": False,
            "workflow_id": str(uuid4()),
            "user_id": str(uuid4()),
            "conversation_id": "conv-A",
            "db": session,
        }
    )

    assert node._build_memory_summary() is None
    # memory_mode가 꺼져 있으면 WorkflowRun 조회 자체를 하지 않는다.
    assert session.run_filter_columns is None


# --- fakes -------------------------------------------------------------------


class _RecordingQuery:
    def __init__(self, session, model):
        self.session = session
        self.model = model

    def filter(self, *expressions):
        if getattr(self.model, "__name__", "") == "WorkflowRun":
            self.session.run_filter_columns = [str(e.left) for e in expressions]
        return self

    def order_by(self, *args, **kwargs):
        return self

    def limit(self, *args, **kwargs):
        return self

    def all(self):
        return []


class _RecordingSession:
    def __init__(self):
        self.run_filter_columns = None

    def query(self, model, *rest):
        return _RecordingQuery(self, model)

    def close(self):
        pass
