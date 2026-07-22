import os
import subprocess
import sys
import textwrap
from pathlib import Path


def test_executor_uses_native_thread_after_gevent_monkey_patch() -> None:
    repository_root = Path(__file__).resolve().parents[4]
    script = textwrap.dedent(
        """
        from gevent import monkey
        monkey.patch_all()

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
                finally:
                    unregister()

            job = executor.submit(wait_for_cancellation)
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
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(repository_root)

    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=repository_root,
        env=environment,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )

    assert completed.returncode == 0
