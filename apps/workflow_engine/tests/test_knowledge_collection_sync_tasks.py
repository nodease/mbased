from apps.workflow_engine.knowledge_collection_sync_tasks import (
    execute_knowledge_collection_sync,
    recover_knowledge_collection_sync_jobs,
)


def test_collection_sync_tasks_use_late_ack_worker_loss_contract() -> None:
    assert execute_knowledge_collection_sync.name == (
        "workflow.knowledge_collection_sync.execute"
    )
    assert execute_knowledge_collection_sync.acks_late is True
    assert execute_knowledge_collection_sync.reject_on_worker_lost is True
    assert execute_knowledge_collection_sync.max_retries == 0
    assert recover_knowledge_collection_sync_jobs.acks_late is True
    assert recover_knowledge_collection_sync_jobs.reject_on_worker_lost is True


def test_malformed_job_id_is_safe_noop_before_database_access() -> None:
    assert execute_knowledge_collection_sync.run("not-a-uuid") == {
        "status": "missing",
        "reason_code": None,
    }
