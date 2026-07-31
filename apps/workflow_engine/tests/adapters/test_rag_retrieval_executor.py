import os
import subprocess
import sys
import textwrap
from pathlib import Path


def _assert_isolated_script_succeeds(script: str) -> None:
    repository_root = Path(__file__).resolve().parents[4]
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(repository_root)

    completed = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(script)],
        cwd=repository_root,
        env=environment,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )

    assert completed.returncode == 0


def test_executor_uses_native_thread_after_gevent_monkey_patch() -> None:
    _assert_isolated_script_succeeds(
        """
        from gevent import monkey
        monkey.patch_all()

        import gevent
        import time

        allocate_native_lock = monkey.get_original('_thread', 'allocate_lock')
        original_get_ident = monkey.get_original('_thread', 'get_ident')
        original_sleep = monkey.get_original('time', 'sleep')
        from apps.workflow_engine.adapters.rag_retrieval_executor import (
            GeventNativeThreadRAGRetrievalExecutor,
            NativeThreadRAGRetrievalCancellation,
        )

        main_thread_id = original_get_ident()
        executor = GeventNativeThreadRAGRetrievalExecutor(1)
        try:
            worker_thread_id = executor.submit(original_get_ident).result()
            cancellation = NativeThreadRAGRetrievalCancellation()
            registered = allocate_native_lock()
            registered.acquire()
            observed = allocate_native_lock()
            observed.acquire()

            def wait_for_cancellation():
                unregister = cancellation.register(observed.release)
                registered.release()
                deadline = time.monotonic() + 1
                try:
                    while not cancellation.cancelled and time.monotonic() < deadline:
                        original_sleep(0.001)
                    if observed.acquire(timeout=1):
                        observed.release()
                finally:
                    unregister()

            job = executor.submit(wait_for_cancellation)
            gevent.sleep(0)
            if not registered.acquire(timeout=1):
                raise SystemExit(2)
            cancellation.cancel()
            job.result()
            if not observed.acquire(timeout=1):
                raise SystemExit(3)
        finally:
            executor.close()
        if worker_thread_id == main_thread_id:
            raise SystemExit(1)
        """
    )


def test_executors_share_process_wide_native_worker_cap() -> None:
    _assert_isolated_script_succeeds(
        """
        from gevent import monkey
        monkey.patch_all()

        import gevent

        allocate_native_lock = monkey.get_original('_thread', 'allocate_lock')
        from apps.workflow_engine.adapters.rag_retrieval_executor import (
            PROCESS_RAG_RETRIEVAL_MAX_WORKERS,
            GeventNativeThreadRAGRetrievalExecutor,
        )

        gate = allocate_native_lock()
        gate.acquire()
        state_lock = allocate_native_lock()
        state = {'active': 0, 'max_active': 0}

        def work():
            with state_lock:
                state['active'] += 1
                state['max_active'] = max(
                    state['max_active'],
                    state['active'],
                )
            try:
                gate.acquire()
                gate.release()
                return True
            finally:
                with state_lock:
                    state['active'] -= 1

        first = GeventNativeThreadRAGRetrievalExecutor(
            PROCESS_RAG_RETRIEVAL_MAX_WORKERS
        )
        second = GeventNativeThreadRAGRetrievalExecutor(
            PROCESS_RAG_RETRIEVAL_MAX_WORKERS
        )
        first_job_count = PROCESS_RAG_RETRIEVAL_MAX_WORKERS // 2
        jobs = [
            first.submit(work)
            for _ in range(first_job_count)
        ] + [
            second.submit(work)
            for _ in range(
                PROCESS_RAG_RETRIEVAL_MAX_WORKERS - first_job_count
            )
        ]
        gevent.spawn_later(0.1, gate.release)
        try:
            if not all(job.result() for job in jobs):
                raise SystemExit(2)
        finally:
            first.close()
            second.close()

        if state['max_active'] > PROCESS_RAG_RETRIEVAL_MAX_WORKERS:
            raise SystemExit(1)
        """
    )


def test_process_pool_rejects_overflow_before_spawning_and_recovers() -> None:
    _assert_isolated_script_succeeds(
        """
        from gevent import monkey
        monkey.patch_all()

        allocate_native_lock = monkey.get_original('_thread', 'allocate_lock')
        from apps.workflow_engine.adapters.rag_retrieval_executor import (
            PROCESS_RAG_RETRIEVAL_MAX_WORKERS,
            GeventNativeThreadRAGRetrievalExecutor,
        )
        from apps.workflow_engine.application.rag_retrieval_fanout import (
            RAGRetrievalFanoutError,
        )

        gate = allocate_native_lock()
        gate.acquire()
        first = GeventNativeThreadRAGRetrievalExecutor(
            PROCESS_RAG_RETRIEVAL_MAX_WORKERS
        )

        def blocked_work():
            gate.acquire()
            gate.release()
            return True

        jobs = [
            first.submit(blocked_work)
            for _ in range(PROCESS_RAG_RETRIEVAL_MAX_WORKERS)
        ]

        overflow_callback_calls = []

        def overflow_callback():
            overflow_callback_calls.append(True)
            return True

        overflow = GeventNativeThreadRAGRetrievalExecutor(1)
        try:
            try:
                overflow.submit(overflow_callback)
            except RAGRetrievalFanoutError:
                pass
            else:
                raise SystemExit(1)
            if overflow_callback_calls:
                raise SystemExit(2)

            gate.release()
            if not all(job.result() for job in jobs):
                raise SystemExit(3)

            recovered = overflow.submit(lambda: True)
            if not recovered.result():
                raise SystemExit(4)
        finally:
            first.close()
            overflow.close()
        """
    )


def test_cancellation_callbacks_do_not_block_gevent_hub() -> None:
    _assert_isolated_script_succeeds(
        """
        from gevent import monkey
        monkey.patch_all()

        import gevent
        import time

        allocate_native_lock = monkey.get_original('_thread', 'allocate_lock')
        original_get_ident = monkey.get_original('_thread', 'get_ident')
        original_sleep = monkey.get_original('time', 'sleep')
        from apps.workflow_engine.adapters.rag_retrieval_executor import (
            GeventNativeThreadRAGRetrievalExecutor,
            NativeThreadRAGRetrievalCancellation,
        )

        main_thread_id = original_get_ident()
        callback_started = allocate_native_lock()
        callback_started.acquire()
        callback_blocker = allocate_native_lock()
        callback_blocker.acquire()
        callback_finished = allocate_native_lock()
        callback_finished.acquire()
        callback_thread_ids = []

        def blocking_callback():
            callback_thread_ids.append(original_get_ident())
            callback_started.release()
            callback_blocker.acquire()
            callback_blocker.release()
            callback_finished.release()

        def release_callback():
            if not callback_started.acquire(timeout=1):
                return False
            original_sleep(0.2)
            callback_blocker.release()
            return True

        executor = GeventNativeThreadRAGRetrievalExecutor(1)
        cancellation = NativeThreadRAGRetrievalCancellation()
        cancellation.register(blocking_callback)
        helper_job = executor.submit(release_callback)
        gevent.sleep(0)

        started_at = time.monotonic()
        cancellation.cancel()
        cancel_elapsed = time.monotonic() - started_at
        gevent.sleep(0)

        try:
            if not callback_finished.acquire(timeout=1):
                raise SystemExit(2)
            if not helper_job.result():
                raise SystemExit(3)
        finally:
            executor.close()

        if cancel_elapsed >= 0.1:
            raise SystemExit(1)
        if callback_thread_ids == [main_thread_id]:
            raise SystemExit(4)
        """
    )


def test_unregister_disarms_queued_cancellation_callback() -> None:
    _assert_isolated_script_succeeds(
        """
        from gevent import monkey
        monkey.patch_all()

        import gevent

        allocate_native_lock = monkey.get_original('_thread', 'allocate_lock')
        from apps.workflow_engine.adapters.rag_retrieval_executor import (
            NativeThreadRAGRetrievalCancellation,
        )

        invoked = allocate_native_lock()
        invoked.acquire()
        cancellation = NativeThreadRAGRetrievalCancellation()
        unregister = cancellation.register(invoked.release)

        cancellation.cancel()
        unregister()
        gevent.sleep(0.05)

        if invoked.acquire(blocking=False):
            raise SystemExit(1)
        """
    )
