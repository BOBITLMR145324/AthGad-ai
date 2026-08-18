"""
services/notification_queue.py
==============================
A small, bounded, single-worker queue for outbound notifications.

Dispatch jobs (SMS/email alert broadcasts) are enqueued instead of spawning a
fire-and-forget thread per request. This:
  - caps concurrency (no thread explosion under load),
  - serializes broadcasts so the dispatch-dedup claim is never raced by
    sibling threads from the same request burst,
  - keeps the API response fast (jobs run off the request thread).

Persistence/durability note: this is an in-process queue. Jobs are lost if the
process dies before draining. For durable, cross-worker delivery replace this
with Redis/RQ in a high-scale deployment.
"""

import threading
import queue
import traceback
import logging

logger = logging.getLogger(__name__)


class NotificationQueue:
    def __init__(self, maxsize: int = 200):
        self._queue = queue.Queue(maxsize=maxsize)
        self._worker = threading.Thread(
            target=self._run,
            name="notification-worker",
            daemon=True,
        )
        self._worker.start()

    def enqueue(self, fn, *args, **kwargs) -> bool:
        """
        Enqueues `fn(*args, **kwargs)` to run on the worker thread.
        Returns True when accepted; False when the queue is full (job dropped
        and logged so the loss is visible rather than silent).
        """
        try:
            self._queue.put_nowait((fn, args, kwargs))
            return True
        except queue.Full:
            logger.warning("[NOTIFICATION QUEUE] Queue full; notification job dropped.")
            return False

    def _run(self):
        """Worker loop: runs one job at a time, never exits on a bad job."""
        while True:
            fn, args, kwargs = self._queue.get()
            try:
                fn(*args, **kwargs)
            except Exception:
                logger.error("[NOTIFICATION QUEUE] Job failed:\n%s", traceback.format_exc())
            finally:
                self._queue.task_done()


# Module-level default queue shared by the app.
notification_queue = NotificationQueue()
