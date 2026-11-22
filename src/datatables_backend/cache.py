"""A cache for ``recordsTotal``: counting every row of a large table on every draw is the slowest part of a draw."""

from __future__ import annotations

import hashlib
import threading
import time


class TotalCache:
    """Keeps each table's unfiltered total for ``ttl`` seconds. Share one instance between tables and requests.

    Nothing outside the process tells it about writes: a total can be up to ``ttl`` seconds old. Call ``clear`` after a
    write that must show at once.

    A total is kept under what the table sends: on SQL the database's URL and the count with every bound value, on
    OpenSearch the index's name and the base filter. Rows scoped by anything else share a total, so give each such
    scope a cache of its own: a database session's state (row-level security, a role, a schema search path or
    schema translation, the current database), OpenSearch's document-level security, or a second cluster with the
    same index name.

    A bound value enters the key by its ``repr``, which must not change between requests: a value whose ``repr``
    holds its address never finds its total again.
    """

    def __init__(self, ttl: float = 300.0) -> None:
        self.ttl = ttl
        self._entries: dict[str, tuple[int, float]] = {}
        self._lock = threading.Lock()

    def get(self, key: str) -> int | None:
        with self._lock:
            entry = self._entries.get(_digest(key))
        if entry is None or time.monotonic() - entry[1] >= self.ttl:
            return None
        return entry[0]

    def put(self, key: str, total: int) -> None:
        with self._lock:
            self._entries[_digest(key)] = (total, time.monotonic())

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()


def _digest(key: str) -> str:
    """A key holds bound values, which can be personal data or a long list: only its digest is kept."""
    return hashlib.sha256(key.encode()).hexdigest()
