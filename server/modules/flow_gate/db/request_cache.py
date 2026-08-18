"""Request-scoped read memoization + per-request query accounting (0291 NR0003 P3-1 / 4-8).

NR0003 finding 5: within a single request the same row is read repeatedly with the same
parameters — ``users`` 3x, ``documents WHERE doc_id='…0010-AC'`` 3x, ``documents WHERE
group_id AND seq=7`` 3x, ``workflow_return_points WHERE group_id`` 2x, ``projects`` 2x,
``project_settings`` 2x. There is one cause: every service entry point queries on its own
and none of them knows the others already read it.

Bolting another TTL cache onto this is the wrong answer. The 5-second TTL used by
``meta_cache`` / ``auth_cache`` is **time-based**, so it leaks at the second boundary, and
widening coverage multiplies the "a read right after a write sees the old value" risk by
the number of tables. That is why NR0003 P3-1 named request scope — **it has no staleness
risk in principle.** The cache is discarded wholesale at the request boundary, so its maximum lifetime is one request, and writes inside it are cleared by rule 2 below.

Policy
----

1. **Outside a scope it does nothing.** The cache lives only inside ``request_scope()``.
   Background workers (``TestRunWorker`` / ``NumberingWorker``) and boot paths never open a
   scope, so their behaviour is unchanged. What gets cached is decided by one question: "are we serving a request?"

2. **One write in the request empties that request's whole cache.** No per-table dependency
   tracking: which SELECT is affected by which UPDATE cannot be known from the SQL string
   alone, and getting it wrong breaks read-your-writes. A request usually does 0 to a few
   writes against dozens of reads, so discarding everything still leaves a large win. **Correctness is not traded for performance.**

3. **Reads inside a transaction are not cached.** A transaction is a unit of writing. Its
   SELECTs often re-read what was just written, and a cache could mask the effect of a
   statement run through the transaction handle. This boundary is kept even with rule 2,
   because rule 2 clears the scope cache but does not stop reads mid-transaction.

4. **Copy on the way in and on the way out.** Some callers mutate the returned dict directly
   (``get_rejected_documents_with_reasons()`` attaches ``doc["reject_events"]``). Without a
   copy the cached entry is polluted, which is a bug that did not exist before. Same reason
   as ``meta_cache``'s copy-out convention.

5. **Off by default under TESTING.** Same judgement as ``meta_cache``. Some tests touch
   tables directly with raw SQL (bypassing rule 2's invalidation), so leaving it on by
   default makes failures order-dependent. The 0291 tests turn it on explicitly to verify it.
   ``FLOWGATE_REQUEST_CACHE`` forces it either way (``1``/``0``).

Instrumentation (NR0003 4-8)
-----------------

The same scope counts **queries, cache hits and writes per request**. NR0003 §1-2 stated
that endpoint attribution was an "estimate", because the SQL log carries no request
correlation id and entries were grouped by time adjacency. The scope IS the request boundary, so counting here is a measurement, not an estimate.

Planting a correlation id in the SQL log itself (NR0003 4-8's original plan) was not chosen.
The log is emitted by the third-party ``sqloader``, outside this tree. Aggregating at the
request boundary answers the same questions ("how many queries does this endpoint use?",
"how much did P1-P3 reduce it on the same scale?") without touching the package.
"""
from __future__ import annotations

import copy
import os
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Iterator, Optional

# A cache key longer than this is not cached. Callers like `doc_id IN (?x900)` still exist,
# and such a query is (a) expensive just to build a key for and (b) almost never repeated
# with identical parameters in the same request — the cache would cost without paying.
_MAX_KEY_LEN = 4096

_MISS = object()


@dataclass
class RequestScope:
    """One request's cache and counters."""

    label: str = ""
    entries: dict[tuple[str, str], Any] = field(default_factory=dict)
    queries: int = 0       # reads that reached the DB (uncacheable ones included)
    hits: int = 0          # reads served by the cache — these never reached the DB
    writes: int = 0        # writes that emptied the cache
    uncacheable: int = 0   # queries not storable (in a transaction / key too long / off)

    @property
    def reads(self) -> int:
        """Total reads this request asked for — the number NR0003 §1-2 could only estimate."""
        return self.queries + self.hits

    def summary(self) -> str:
        return (
            f"{self.label} reads={self.reads} db={self.queries} cached={self.hits} "
            f"skipped={self.uncacheable} writes={self.writes}"
        )


_scope: ContextVar[Optional[RequestScope]] = ContextVar("flowgate_request_scope", default=None)


def enabled() -> bool:
    raw = os.environ.get("FLOWGATE_REQUEST_CACHE")
    if raw is None or raw.strip() == "":
        return not os.environ.get("TESTING")
    return raw.strip() not in ("0", "false", "False", "")


@contextmanager
def request_scope(label: str = "") -> Iterator[RequestScope]:
    """Open the cache scope for one request.

    Nesting reuses the outer scope: a scope means "one request", and opening a new one inside
    it means the boundary was drawn wrong. Joining silently beats running the cache at half
    effectiveness.

    A scope object is always returned, even with the cache off. Instrumentation is independent
    of caching, and counters appearing and vanishing with ``enabled()`` would make them untrustworthy.
    """
    existing = _scope.get()
    if existing is not None:
        yield existing
        return
    scope = RequestScope(label=label)
    token = _scope.set(scope)
    try:
        yield scope
    finally:
        _scope.reset(token)


def current() -> Optional[RequestScope]:
    return _scope.get()


def _key(sql: str, params) -> Optional[tuple[str, str]]:
    try:
        rendered = repr(tuple(params or ()))
    except Exception:
        # If a parameter has no stable repr, identity cannot be decided.
        return None
    if len(sql) + len(rendered) > _MAX_KEY_LEN:
        return None
    return (sql, rendered)


def _cache_key(scope: RequestScope, sql: str, params, in_transaction: bool):
    """Return a key if this read can be memoised, otherwise None.

    ``lookup()`` and ``store()`` must make the same decision or the counters drift apart, so it lives in one place.
    """
    if in_transaction or not enabled():
        return None
    return _key(sql, params)


def lookup(sql: str, params, in_transaction: bool) -> Any:
    """Return the cached result, or ``MISS`` when there is none.

    A MISS bumps no counter — the caller hits the DB and ``store()`` bumps it there, so that
    ``queries`` equals the real number of DB round trips exactly.
    """
    scope = _scope.get()
    if scope is None:
        return _MISS
    key = _cache_key(scope, sql, params, in_transaction)
    if key is not None and key in scope.entries:
        scope.hits += 1
        return copy.deepcopy(scope.entries[key])
    return _MISS


def store(sql: str, params, value: Any, in_transaction: bool) -> None:
    """Record the result the DB just returned."""
    scope = _scope.get()
    if scope is None:
        return
    scope.queries += 1
    key = _cache_key(scope, sql, params, in_transaction)
    if key is None:
        scope.uncacheable += 1
        return
    scope.entries[key] = copy.deepcopy(value)


def invalidate() -> None:
    """A write happened in this request — discard the whole scope cache (rule 2)."""
    scope = _scope.get()
    if scope is None:
        return
    scope.writes += 1
    scope.entries.clear()


def is_miss(value: Any) -> bool:
    return value is _MISS
