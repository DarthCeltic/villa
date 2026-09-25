"""A small time-bounded, single-flight memo for expensive read-only answers."""
from __future__ import annotations

import threading
import time
from collections import OrderedDict
from typing import Callable, Hashable


class SingleFlightTimeout(RuntimeError):
    """A caller waited for another caller's computation for longer than the bound; the computation may be hung, so the wait ends instead of parking a worker forever."""


MISS = object()


class SingleFlightTTL:
    def __init__(self, *, ttl_s: float, max_entries: int = 64, clock: Callable[[], float] = time.monotonic, wait_s: float = 60.0):
        if ttl_s <= 0 or max_entries < 1:
            raise ValueError("ttl_s and max_entries must be positive")
        self._ttl = float(ttl_s)
        self._wait_s = float(wait_s)
        self._max = int(max_entries)
        self._clock = clock
        self._lock = threading.Lock()
        self._done: OrderedDict = OrderedDict()
        self._inflight: dict = {}

    def peek(self, key: Hashable):
        """The stored answer if it is still fresh, else MISS."""
        with self._lock:
            hit = self._done.get(key)
            return hit[1] if hit is not None and hit[0] > self._clock() else MISS

    def get(self, key: Hashable, compute: Callable[[], object]):
        deadline = self._clock() + self._wait_s
        while True:
            with self._lock:
                hit = self._done.get(key)
                if hit is not None and hit[0] > self._clock():
                    self._done.move_to_end(key)
                    return hit[1]
                event = self._inflight.get(key)
                if event is None:
                    event = self._inflight[key] = threading.Event()
                    leader = True
                else:
                    leader = False
            if not leader:
                remaining = deadline - self._clock()
                if remaining <= 0 or not event.wait(remaining):
                    raise SingleFlightTimeout("the computation another caller started for %r has not finished in %.0f s" % (key, self._wait_s))
                continue
            try:
                value = compute()
                with self._lock:
                    self._done[key] = (self._clock() + self._ttl, value)
                    self._done.move_to_end(key)
                    while len(self._done) > self._max:
                        self._done.popitem(last=False)
                return value
            finally:
                with self._lock:
                    self._inflight.pop(key, None)
                event.set()

    def clear(self) -> None:
        with self._lock:
            self._done.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._done)
