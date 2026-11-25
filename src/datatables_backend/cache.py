"""A cache for ``recordsTotal``: counting every row of a large table on every draw is the slowest part of a draw."""

from __future__ import annotations

import hashlib
import inspect
import threading
import time
from typing import Any
from typing import Protocol


class TotalStore(Protocol):
    """Where a table keeps ``recordsTotal``: any object with these two methods. ``TotalCache`` is the in-process one.

    The key is an opaque string, the SHA-256 digest of what the table's total depends on, so a store never sees a bound
    value. It is stable across processes for bound values whose ``repr`` (SQL) or ``str`` (OpenSearch) is
    deterministic; a set or an object whose ``repr`` holds its address differs per process. It may change between
    releases of this library, which gives a miss and never a wrong total. The store decides when a total ends; the
    library registers no hook that would tell it about a write, so an application that must show a write at once gives
    the table a store of its own and empties it after the write.

    An exception from ``get`` or ``put`` propagates and the request fails (a Redis that is down, say). A caller who
    prefers to count again catches it inside the store and returns None from ``get``. Under ``fetch`` a sync store runs
    inline and blocks the event loop on every draw: give ``fetch`` an ``AsyncTotalStore``.
    """

    def get(self, key: str) -> int | None:
        """The total kept under ``key``, or None if there is none (or it has ended)."""
        ...

    def put(self, key: str, total: int) -> None:
        """Keep ``total`` under ``key``."""
        ...


class AsyncTotalStore(Protocol):
    """``TotalStore`` with coroutine methods, for ``fetch``. ``fetch_sync`` refuses it with a ``TypeError``: before
    anything is queried when ``get`` or ``put`` is a coroutine function, at the first store call when a plain method
    returns an awaitable."""

    async def get(self, key: str) -> int | None:
        """The total kept under ``key``, or None if there is none (or it has ended)."""
        ...

    async def put(self, key: str, total: int) -> None:
        """Keep ``total`` under ``key``."""
        ...


class TotalCache:
    """The in-process ``TotalStore``: keeps each table's unfiltered total for ``ttl`` seconds. Share one instance
    between tables and requests.

    Nothing outside the process tells it about writes: a total can be up to ``ttl`` seconds old. Call ``clear`` after a
    write that must show at once.

    A total is kept under what the table sends: on SQL the database's URL and the count with every bound value (the
    request's ``scope`` included), on OpenSearch the index's name, the base filter and the request's ``scope``. Rows
    scoped by anything else share a total, so give each such
    scope a cache of its own: a database session's state (row-level security, a role, a schema search path or
    schema translation, the current database), OpenSearch's document-level security, or a second cluster with the
    same index name.

    A bound value enters the key by its ``repr``, which must not change between requests: a value whose ``repr``
    holds its address never finds its total again. It keeps a key as given: the backends hand it the key's digest, so
    no bound value is kept. A caller who uses it directly passes a key of their own choosing.

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
            entry = self._entries.get(key)
        if entry is None or time.monotonic() - entry[1] >= self.ttl:
            return None
        return entry[0]

    def put(self, key: str, total: int) -> None:
        with self._lock:
            self._entries.pop(key, None)  # a total stored again is the newest, which keeps the dict in age order
            self._entries[key] = (total, time.monotonic())
            if len(self._entries) > self.max_entries:  # one put adds one entry, so one drop is enough
                del self._entries[next(iter(self._entries))]

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()


def key_digest(key: str) -> str:
    """A key holds bound values, which can be personal data or a long list: a store is handed only its digest."""
    return hashlib.sha256(key.encode()).hexdigest()


_ASYNC_STORE = (
    'total_cache is an AsyncTotalStore (its get or put returns an awaitable), which fetch_sync cannot await: pass a '
    'TotalStore, or call fetch.'
)


def require_sync_store(store: TotalStore | AsyncTotalStore) -> None:
    """Refuses a store whose methods are coroutine functions, before a query runs.

    Raises:
        TypeError: ``get`` or ``put`` is a coroutine function.
    """
    if inspect.iscoroutinefunction(store.get) or inspect.iscoroutinefunction(store.put):
        raise TypeError(_ASYNC_STORE)


def _sync_result(result: Any) -> Any:
    """What a sync store returned; an awaitable (a store written in any async way) is no answer."""
    if inspect.isawaitable(result):
        if inspect.iscoroutine(result):
            result.close()  # never awaited, so Python would warn
        raise TypeError(_ASYNC_STORE)
    return result


def read_total(store: TotalStore | AsyncTotalStore, key: str) -> int | None:
    return _sync_result(store.get(key))


def write_total(store: TotalStore | AsyncTotalStore, key: str, total: int) -> None:
    _sync_result(store.put(key, total))


async def read_total_async(store: TotalStore | AsyncTotalStore, key: str) -> int | None:
    result = store.get(key)
    return await result if inspect.isawaitable(result) else result


async def write_total_async(store: TotalStore | AsyncTotalStore, key: str, total: int) -> None:
    result = store.put(key, total)
    if inspect.isawaitable(result):
        await result
