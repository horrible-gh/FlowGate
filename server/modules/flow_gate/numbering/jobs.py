"""numbering_jobs queue worker — single-process polling mode."""
from __future__ import annotations

import threading
import time
import logging
from pathlib import Path
from typing import Optional

from ..db import numbering_jobs as db_jobs
from .migration_service import process_job

logger = logging.getLogger(__name__)


class NumberingWorker:
    """Worker that processes the numbering_jobs queue with a single thread.

    Usage::

        worker = NumberingWorker()
        worker.start()          # Start the background thread
        ...
        worker.stop()           # Stop the worker
    """

    def __init__(
        self,
        poll_interval: float = 5.0,
        backup_dir: Optional[Path] = None,
    ) -> None:
        self._poll_interval = poll_interval
        self._backup_dir = backup_dir
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        """Start the worker thread."""
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="numbering-worker"
        )
        self._thread.start()
        logger.info("[NumberingWorker] started")

    def stop(self, timeout: float = 10.0) -> None:
        """Stop the worker thread."""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=timeout)
        logger.info("[NumberingWorker] stopped")

    def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                self._process_one_batch()
            except Exception as exc:
                logger.exception(f"[NumberingWorker] Exception: {exc}")
            self._stop_event.wait(self._poll_interval)

    def _process_one_batch(self) -> None:
        """Fetch and process one job in queued status."""
        # Process the oldest queued job across all projects
        from ..db.connection import get_store
        row = get_store()._fetch_one(
            "SELECT id FROM numbering_jobs WHERE status='queued' ORDER BY created_at LIMIT 1"
        )
        if row is None:
            return
        job_id = row["id"]
        try:
            result = process_job(job_id, backup_dir=self._backup_dir)
            logger.info(
                f"[NumberingWorker] job={job_id} → {result.get('status')}"
            )
        except Exception as exc:
            logger.error(f"[NumberingWorker] job={job_id} processing error: {exc}")


# Singleton worker instance
_worker: Optional[NumberingWorker] = None
_worker_meta = threading.Lock()


def get_worker(poll_interval: float = 5.0) -> NumberingWorker:
    """Return the singleton worker instance."""
    global _worker
    with _worker_meta:
        if _worker is None:
            _worker = NumberingWorker(poll_interval=poll_interval)
        return _worker
