"""A cache for ``recordsTotal``: counting every row of a large table on every draw is the slowest part of a draw."""

from __future__ import annotations

import threading
import time


class TotalCache:
    """Keeps each table's unfiltered total for ``ttl`` seconds. Share one instance between tables and requests.

    Nothing outside the process tells it about writes: a total can be up to ``ttl`` seconds old. Call ``clear`` after a
    write that must show at once.
    """

    def __init__(self, ttl: float = 300.0) -> None:
        self.ttl = ttl
        self._entries: dict[str, tuple[int, float]] = {}
        self._lock = threading.Lock()

    def get(self, key: str) -> int | None:
        with self._lock:
            entry = self._entries.get(key)
        if entry is None or time.monotonic() - entry[1] >= self.ttl:
            return None
        return entry[0]

    def put(self, key: str, total: int) -> None:
        with self._lock:
            self._entries[key] = (total, time.monotonic())

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
