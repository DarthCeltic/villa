"""A bounded worker pool for the observe service's heavy read work."""
from __future__ import annotations

import asyncio
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor

DEFAULT_MAX_WORKERS = 4
DEFAULT_MAX_PENDING = 512
WORKERS_ENV = "ARGUS_HEAVY_WORKERS"
PENDING_ENV = "ARGUS_HEAVY_QUEUE"


class PoolSaturated(RuntimeError):
    """Raised instead of queueing without limit."""

    def __init__(self, name: str, retry_after_s: int):
        super().__init__("%s is saturated; retry in about %d s" % (name, retry_after_s))
        self.name = name
        self.retry_after_s = retry_after_s


def _bounded_int(raw, default: int, low: int, high: int) -> int:
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        return default
    return value if low <= value <= high else default


def sizes(profile_id: str | None = None, cpu_count: int | None = None, environ=None) -> dict:
    """Worker and queue limits."""
    env = os.environ if environ is None else environ
    cpus = cpu_count or os.cpu_count() or 2
    base = max(2, min(DEFAULT_MAX_WORKERS, cpus // 2))
    if profile_id == "cpu_only":
        base = min(base, 2)
    return {"max_workers": _bounded_int(env.get(WORKERS_ENV), base, 1, 16),
            "max_pending": _bounded_int(env.get(PENDING_ENV), DEFAULT_MAX_PENDING, 1, 100_000)}


class BoundedPool:
    def __init__(self, name: str, *, max_workers: int, max_pending: int, clock=time.monotonic):
        if max_workers < 1 or max_pending < 1:
            raise ValueError("max_workers and max_pending must be positive")
        self.name = name
        self.max_workers = int(max_workers)
        self.max_pending = int(max_pending)
        self._clock = clock
        self._executor = ThreadPoolExecutor(max_workers=self.max_workers, thread_name_prefix="argus-" + name)
        self._lock = threading.Lock()
        self._admitted = 0
        self._running = 0
        self._completed = 0
        self._failed = 0
        self._rejected = 0
        self._peak_running = 0
        self._peak_admitted = 0
        self._wait_max_s = 0.0
        self._mean_run_s = 0.5

    def _retry_after(self) -> int:
        queued = max(0, self._admitted - self._running)
        return max(1, min(60, int(round(self._mean_run_s * (queued / self.max_workers + 1)))))

    def _call(self, fn, args, kwargs, admitted_at):
        started = self._clock()
        with self._lock:
            self._running += 1
            self._peak_running = max(self._peak_running, self._running)
            self._wait_max_s = max(self._wait_max_s, started - admitted_at)
        try:
            return fn(*args, **kwargs)
        finally:
            ran = self._clock() - started
            with self._lock:
                self._running -= 1
                self._mean_run_s = 0.8 * self._mean_run_s + 0.2 * ran

    def _release(self, future) -> None:
        with self._lock:
            self._admitted -= 1
            if future.cancelled() or future.exception() is not None:
                self._failed += 1
            else:
                self._completed += 1

    async def run(self, fn, *args, **kwargs):
        """Run `fn` on a pool thread."""
        with self._lock:
            if self._admitted >= self.max_workers + self.max_pending:
                self._rejected += 1
                raise PoolSaturated(self.name, self._retry_after())
            self._admitted += 1
            self._peak_admitted = max(self._peak_admitted, self._admitted)
            admitted_at = self._clock()
        try:
            future = self._executor.submit(self._call, fn, args, kwargs, admitted_at)
        except BaseException:
            with self._lock:
                self._admitted -= 1
            raise
        future.add_done_callback(self._release)
        return await asyncio.wrap_future(future)

    def status(self) -> dict:
        with self._lock:
            queued = max(0, self._admitted - self._running)
            level = "SATURATED" if self._admitted >= self.max_workers + self.max_pending else (
                "BUSY" if queued > 0 else "IDLE" if self._running == 0 else "WORKING")
            return {"name": self.name, "max_workers": self.max_workers, "max_pending": self.max_pending,
                    "running": self._running, "queued": queued, "level": level,
                    "completed": self._completed, "failed": self._failed, "rejected": self._rejected,
                    "peak_running": self._peak_running, "peak_admitted": self._peak_admitted,
                    "longest_wait_s": round(self._wait_max_s, 3)}

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)
