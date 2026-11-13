"""Processing workers. The job state lives in the database; the queue is only a wake-up list,
so nothing is lost on restart (``PipelineRunner.recover`` re-queues interrupted runs).

Known limitation (documented): parsers run in-process in a background thread. Resource
limits bound the work, but a hard time limit and process isolation arrive with the desktop
runtime hardening (TASK_005).
"""

from __future__ import annotations

import logging
import queue
import threading
from uuid import UUID

from pricepilot.application.intake import PipelineRunner

logger = logging.getLogger("pricepilot.worker")


class InlineWorker:
    """Runs jobs synchronously on submit (tests, CLI)."""

    def __init__(self, runner: PipelineRunner) -> None:
        self._runner = runner

    def start(self) -> None:
        for run_id in self._runner.recover():
            self._runner.execute(run_id)

    def submit(self, run_id: UUID) -> None:
        self._runner.execute(run_id)

    def stop(self) -> None:
        return None


class ThreadWorker:
    """One background thread; documents are processed one at a time (SQLite-friendly)."""

    def __init__(self, runner: PipelineRunner) -> None:
        self._runner = runner
        self._queue: queue.Queue[UUID | None] = queue.Queue()
        self._thread = threading.Thread(target=self._loop, name="pricepilot-worker", daemon=True)

    def start(self) -> None:
        for run_id in self._runner.recover():
            self._queue.put(run_id)
        self._thread.start()

    def submit(self, run_id: UUID) -> None:
        self._queue.put(run_id)

    def stop(self, timeout: float = 10.0) -> None:
        self._queue.put(None)
        if self._thread.is_alive():
            self._thread.join(timeout)

    def _loop(self) -> None:
        while True:
            run_id = self._queue.get()
            if run_id is None:
                return
            try:
                self._runner.execute(run_id)
            except Exception as exc:  # the runner already maps errors; never kill the loop
                logger.error("worker error", exc_info=exc, extra={"error_code": "INTERNAL_ERROR"})
