"""Tiny thread-safe TTL map, shared by the per-request caches.

Extracted from auth/auth_cache.py (0276 T0009) so db/meta_cache.py (0282 NR0003
발견 2) can reuse it without importing the auth package — auth's __init__ pulls
in middleware/auth_api, which import db modules, and db importing auth back
would be a circular import at package-init time.

Each instance carries its own ``ttl_fn`` so every consumer keeps its own TTL
policy (env var, disable switch) while sharing the mechanics: monotonic clock
(system time changes cannot extend an entry's life), expired-entry eviction on
read, and a loader helper that runs outside the lock.

Single-flight (0291 T1, NR0003)
-------------------------------

``get_or_load`` originally let concurrent misses on the same key each run the
loader ("a concurrent duplicate load is harmless — both compute the same
value"). That is true of the *value* and false of the *cost*, and the cost is
the whole point of this module. The 0291 CH0016 query dump shows the failure
directly: ``token_blacklist`` ×4, then ``auth_sessions`` ×4, then ``users`` ×4,
in lockstep — the UI fires a burst of requests at once, all of them miss the
same cold key at the same instant, and every one of them goes to the DB. A TTL
cache cannot help a *cold burst*: by the time the first loader returns, the
other N-1 queries are already in flight. That is why the 5s TTL looked like it
was doing nothing.

So loads are now single-flighted per key: the first caller loads, the rest wait
for it and share the result. Details that matter:

  * The loader still runs **outside** the instance lock, so one slow query does
    not serialise unrelated keys.
  * **Re-entrancy is not a deadlock.** If a loader calls back into the same key
    on the same thread, that thread is recognised as the leader and runs the
    loader inline instead of waiting on itself.
  * **A failed leader does not fail its followers.** They fall back to loading
    themselves rather than inheriting an exception they did not cause — the
    herd returns for that one instant, which is the correct trade against
    turning one transient DB error into N.
  * Followers wait with a timeout. A leader that dies without ever signalling
    (thread killed, interpreter shutting down) must not park requests forever.
"""
from __future__ import annotations

import threading
import time
from typing import Any, Callable

MISS = object()

# 리더가 신호를 못 준 채로 사라지는 경우의 상한. 이 시간을 넘기면 팔로워는 기다림을
# 포기하고 자기가 직접 로드한다 — 즉 최악의 경우가 "느려짐" 이지 "멈춤" 이 아니다.
_LEADER_WAIT_TIMEOUT = 10.0


class _Load:
    """One in-flight load. Followers block on ``done`` until the leader sets it."""

    __slots__ = ("done", "value", "ok", "owner", "stale")

    def __init__(self) -> None:
        self.done = threading.Event()
        self.value: Any = None
        self.ok = False
        self.owner = threading.get_ident()
        # 이 로드가 도는 동안 invalidate()/clear() 가 지나갔다는 표시. 그러면 리더가
        # 읽어 온 값은 이미 낡았을 수 있으므로 캐시에 넣지 않고 팔로워에게도 주지
        # 않는다 — 무효화가 진행 중인 로드에 의해 되살아나는 것이 이 클래스가 만들
        # 수 있는 유일한 정합성 구멍이다.
        self.stale = False

    def finish(self, value: Any, ok: bool) -> None:
        self.value = value
        self.ok = ok
        self.done.set()

    def wait(self) -> Any:
        """Return the leader's value, or ``MISS`` if it failed / never answered."""
        if not self.done.wait(_LEADER_WAIT_TIMEOUT):
            return MISS
        return self.value if self.ok else MISS


class SingleFlight:
    """Deduplicate concurrent calls of the same key — **without** caching them.

    ``TTLCache`` single-flights as a side effect of caching. Some work wants the
    deduplication and must not want the cache: the 0291 auth preamble prefetch
    (auth/auth_preamble.py) populates several TTL caches from one query, and
    caching *its own* result would create a second copy that the revocation
    hooks do not know how to invalidate — a logout would stop taking effect
    immediately, which is the one guarantee auth_cache.py sells.

    So the leader runs ``fn`` and the followers merely wait for it to finish;
    nobody reads a return value. Whatever the leader published (here: the TTL
    cache entries) is what the followers then read through the normal path, and
    if an invalidation wiped it in the meantime they simply miss and load it
    themselves. Correctness lives in the caches, not here.
    """

    def __init__(self) -> None:
        self._inflight: dict[Any, _Load] = {}
        self._lock = threading.Lock()

    def do(self, key: Any, fn: Callable[[], Any]) -> None:
        with self._lock:
            load = self._inflight.get(key)
            if load is not None and load.owner != threading.get_ident():
                follower = True
            else:
                # 없거나(리더) 같은 스레드의 재진입이거나(그냥 실행) — 둘 다 직접 돈다.
                follower = False
                if load is None:
                    load = _Load()
                    self._inflight[key] = load
                else:
                    load = None  # 재진입: 진행 중인 항목을 건드리지 않는다.
        if follower:
            load.done.wait(_LEADER_WAIT_TIMEOUT)
            return
        try:
            fn()
        finally:
            if load is not None:
                with self._lock:
                    if self._inflight.get(key) is load:
                        del self._inflight[key]
                load.finish(None, False)


class TTLCache:
    def __init__(self, ttl_fn: Callable[[], float]) -> None:
        self._entries: dict[Any, tuple[float, Any]] = {}
        self._inflight: dict[Any, _Load] = {}
        self._lock = threading.Lock()
        self._ttl_fn = ttl_fn

    def get(self, key: Any) -> Any:
        ttl = self._ttl_fn()
        if ttl <= 0:
            return MISS
        now = time.monotonic()
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return MISS
            expires_at, value = entry
            if expires_at <= now:
                # Expired: drop it so the map cannot grow without bound.
                self._entries.pop(key, None)
                return MISS
            return value

    def set(self, key: Any, value: Any) -> Any:
        ttl = self._ttl_fn()
        if ttl > 0:
            with self._lock:
                self._entries[key] = (time.monotonic() + ttl, value)
        return value

    def invalidate(self, key: Any) -> None:
        with self._lock:
            self._entries.pop(key, None)
            load = self._inflight.pop(key, None)
        if load is not None:
            load.stale = True

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            loads = list(self._inflight.values())
            self._inflight.clear()
        for load in loads:
            load.stale = True

    def get_or_load(self, key: Any, loader: Callable[[], Any]) -> Any:
        """Return the cached value, or call loader() and cache its result.

        Concurrent misses on the same key are single-flighted: one caller runs
        the loader, the others wait for it. See the module docstring for why —
        a cold burst is exactly the case a plain TTL map cannot help with.

        The loader runs outside the lock, so a slow query on one key never
        blocks another.
        """
        cached = self.get(key)
        if cached is not MISS:
            return cached
        if self._ttl_fn() <= 0:
            # 캐싱이 꺼져 있으면 조율할 것도 없다: 호출부마다 자기 값을 읽는 것이
            # TTL=0 의 의미(= 캐시 이전 동작)다.
            return loader()

        now = time.monotonic()
        with self._lock:
            # 락 밖의 get() 과 여기 사이에 다른 스레드가 값을 채웠을 수 있다.
            entry = self._entries.get(key)
            if entry is not None and entry[0] > now:
                return entry[1]
            load = self._inflight.get(key)
            if load is None:
                load = _Load()
                self._inflight[key] = load
                role = "leader"
            elif load.owner == threading.get_ident():
                # 로더가 같은 키로 재진입했다 — 자기 자신을 기다리면 교착이다.
                # 진행 중인 load 는 건드리지 않고(끝내는 것은 바깥 호출부의 몫)
                # 이 호출만 직접 처리한다.
                role = "reentrant"
            else:
                role = "follower"

        if role == "reentrant":
            return self.set(key, loader())

        if role == "follower":
            shared = load.wait()
            if shared is not MISS:
                return shared
            # 리더가 실패했거나 응답이 없다. 남의 예외를 물려받는 대신 직접 읽는다.
            return self.set(key, loader())

        try:
            value = loader()
        except BaseException:
            with self._lock:
                if self._inflight.get(key) is load:
                    del self._inflight[key]
            load.finish(None, False)
            raise
        with self._lock:
            if self._inflight.get(key) is load:
                del self._inflight[key]
        if load.stale:
            # 로드 도중 무효화가 지나갔다: 이 값은 공표하지 않는다. 리더 자신은
            # 방금 DB 에서 읽은 것을 그대로 쓰고(그건 무효화 이전 캐시 도입 전과
            # 같은 상황이다), 팔로워는 각자 다시 읽는다.
            load.finish(None, False)
            return value
        self.set(key, value)
        load.finish(value, True)
        return value
