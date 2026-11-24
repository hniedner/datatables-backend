"""A cache for ``recordsTotal``: counting every row of a large table on every draw is the slowest part of a draw."""

from __future__ import annotations

import hashlib
import threading
import time


class TotalCache:
    """Keeps each table's unfiltered total for ``ttl`` seconds. Share one instance between tables and requests.

    Nothing outside the process tells it about writes: a total can be up to ``ttl`` seconds old. Call ``clear`` after a
    write that must show at once.

    A total is kept under what the table sends: on SQL the database's URL and the count with every bound value (the
    request's ``scope`` included), on OpenSearch the index's name, the base filter and the request's ``scope``. Rows
    scoped by anything else share a total, so give each such
    scope a cache of its own: a database session's state (row-level security, a role, a schema search path or
    schema translation, the current database), OpenSearch's document-level security, or a second cluster with the
    same index name.

    A bound value enters the key by its ``repr``, which must not change between requests: a value whose ``repr``
    holds its address never finds its total again.

    It holds at most ``max_entries`` totals (10,000 by default, about 2.6 MB). When it is full, the total stored
    longest ago is dropped: with one ``ttl`` that is the one nearest its expiry, and a read changes nothing. A total
    counted again after it expired is the newest. With more distinct totals inside one ``ttl`` than ``max_entries``
    (one per user or tenant that has its own scope), most requests count again: set ``max_entries`` to at least that
    number.

    Args:
        ttl: Seconds a total is kept.
        max_entries: The most totals kept; at least 1.

    Raises:
        ValueError: ``max_entries`` is below 1, which would cache nothing.
    """

    def __init__(self, ttl: float = 300.0, *, max_entries: int = 10_000) -> None:
        if max_entries < 1:
            raise ValueError(
                f'max_entries must be at least 1, not {max_entries}: pass how many totals to keep, 10000 by default.'
            )
        self.ttl = ttl
        self.max_entries = max_entries
        self._entries: dict[str, tuple[int, float]] = {}
        self._lock = threading.Lock()

    def get(self, key: str) -> int | None:
        with self._lock:
            entry = self._entries.get(_digest(key))
        if entry is None or time.monotonic() - entry[1] >= self.ttl:
            return None
        return entry[0]

    def put(self, key: str, total: int) -> None:
        digest = _digest(key)
        with self._lock:
            self._entries.pop(digest, None)  # a total stored again is the newest, which keeps the dict in age order
            self._entries[digest] = (total, time.monotonic())
            if len(self._entries) > self.max_entries:  # one put adds one entry, so one drop is enough
                del self._entries[next(iter(self._entries))]

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()


def _digest(key: str) -> str:
    """A key holds bound values, which can be personal data or a long list: only its digest is kept."""
    return hashlib.sha256(key.encode()).hexdigest()
