"""요청당 인증 프리앰블을 쿼리 한 번으로 접는다 (0291 T1, CH0016).

문제
----

인증된 요청 하나는 핸들러가 시작되기도 전에 고정 3쿼리를 쓴다::

    SELECT jti FROM token_blacklist WHERE jti=?      -- 이 액세스 토큰이 폐기됐나
    SELECT * FROM auth_sessions WHERE session_id=?   -- 이 세션이 살아 있나
    SELECT * FROM users WHERE user_id=?              -- 이 사용자가 활성인가

0276 T0009 가 여기에 5초 TTL 캐시를 붙였지만, CH0016 에 올라온 실측 덤프는 세 쿼리가
여전히 각각 네 번씩 찍힌다. 이유는 두 가지이고 이 파일과 ``utils/ttl_cache.py`` 가
각각 하나씩 맡는다.

1. **콜드 버스트.** 화면 하나를 그릴 때 프론트가 요청을 한꺼번에 여러 개 쏜다. 전부
   같은 순간에 같은 키를 미스하므로, 첫 로더가 돌아오기 전에 나머지가 이미 DB 로
   출발해 있다. TTL 캐시는 원리적으로 이걸 못 막는다 — ``ttl_cache.SingleFlight`` /
   ``TTLCache.get_or_load`` 의 single-flight 가 이 몫이다.
2. **콜드 미스 자체가 3왕복이다.** 캐시가 비었을 때(프로세스 기동 직후, TTL 만료 직후,
   무효화 직후) 세 번을 따로 간다. 세 쿼리는 서로 독립이고 전부 단일 행 조회이므로,
   한 번에 읽지 않을 이유가 없다. 그게 이 파일이다.

설계
----

이 모듈은 **프리페치**다. 캐시를 하나 더 만들지 않는다.

``prefetch()`` 는 ``auth_cache`` 의 기존 세 맵(blacklist / sessions / users)에 값을
채워 넣기만 하고, 판정은 여전히 ``token_store.is_blacklisted`` /
``session_store.is_session_active`` / ``middleware.get_current_user`` 가 그 맵을 통해
한다. 이게 중요한 이유는 **무효화 경로를 하나도 건드리지 않아도 되기 때문**이다.
로그아웃·세션 폐기·사용자 변경은 이미 그 세 맵을 무효화하고, 프리페치가 별도 사본을
만들지 않으므로 "폐기는 TTL 이 아니라 즉시 반영된다" 는 0276 의 보장이 그대로 유지된다.
합쳐진 결과를 따로 캐시했다면 그 사본은 어떤 훅도 지우지 못했을 것이다.

따라서 실패해도 안전하다. 프리페치가 예외로 끝나면 아무것도 채우지 않고 조용히
돌아가며, 호출부는 종전대로 세 번 조회한다. 인증 경로에 새 실패 지점을 만들지 않는다.

수치 (CH0016 덤프 기준)
-----------------------

* 콜드 요청 하나: 3쿼리 → 1쿼리.
* 동시에 들어온 요청 N개(덤프의 N=4): 3N → 1. single-flight 가 N을 1로, 이 합침이
  3을 1로 만든다.
* 웜 요청: 0쿼리 (0276 T0009 부터 그랬고, 여기서 바뀌지 않는다).
"""
from __future__ import annotations

from ..utils.ttl_cache import MISS as _MISS, SingleFlight as _SingleFlight
from . import auth_cache as _auth_cache

# 합침 쿼리 자체의 콜드 버스트를 막는다. 캐시가 아니라 중복 제거만 한다 — 왜 캐시가
# 아니어야 하는지는 SingleFlight 의 독스트링에 있다.
_flight = _SingleFlight()

# 한 행짜리 앵커에서 users 로 LEFT JOIN 한다. users 행이 없어도 결과가 한 행 나와야
# blacklist/session 판정을 함께 받아올 수 있다 — 그냥 `FROM users` 로 쓰면 삭제된
# 사용자일 때 0행이 되어 나머지 두 사실을 잃는다.
#
# 표준 SQL-92 범위 안에서만 쓴다(스칼라 서브쿼리 + 파생 테이블). db/dialect.py 는
# 플레이스홀더만 바꾸므로 SQLite / MySQL / PostgreSQL 에서 같은 문장이 돈다.
_ANCHOR = "FROM (SELECT 1 AS fg_anchor) fg_a"
_USER_JOIN = " LEFT JOIN users u ON u.user_id = ?"
_BLACKLIST_COL = "(SELECT COUNT(*) FROM token_blacklist tb WHERE tb.jti = ?) AS fg_jti_revoked"
_SESSION_COL = (
    "(SELECT COUNT(*) FROM auth_sessions s WHERE s.session_id = ? AND s.revoked_at IS NULL)"
    " AS fg_session_active"
)


def _missing(jti: str | None, sid: str | None, user_id: str | None) -> tuple[bool, bool, bool]:
    """세 캐시 중 지금 비어 있는 것. 비어 있는 것만 합침 쿼리에 넣는다."""
    need_jti = bool(jti) and _auth_cache.blacklist_cache().get(jti) is _MISS
    need_sid = bool(sid) and _auth_cache.session_cache().get(sid) is _MISS
    need_user = bool(user_id) and _auth_cache.user_cache().get(user_id) is _MISS
    return need_jti, need_sid, need_user


def _load(jti: str | None, sid: str | None, user_id: str,
          need_jti: bool, need_sid: bool, need_user: bool) -> None:
    # ``db.users`` 를 거쳐 store 를 잡는다. 이 문장의 주 테이블이 users 이고, 그
    # 모듈이 쓰는 것과 **같은 store** 로 읽어야 하기 때문이다. connection.get_store 를
    # 직접 부르면 users 조회의 store 를 갈아끼운 호출부(테스트 하네스가 그렇게 한다)
    # 에서 이 프리페치만 다른 DB 를 보게 되고, 그러면 "사용자 없음" 을 캐시해 401 을
    # 만든다 — 프리페치가 판정을 바꾸는 유일한 경로라 여기서 막는다.
    from modules.flow_gate.db.users import get_store

    columns: list[str] = []
    params: list = []
    if need_user:
        columns.append("u.*")
    if need_jti:
        columns.append(_BLACKLIST_COL)
    if need_sid:
        columns.append(_SESSION_COL)
    # 앵커의 `?` 가 SELECT 목록의 `?` 들보다 뒤에 오도록 파라미터를 같은 순서로 쌓는다.
    if need_jti:
        params.append(jti)
    if need_sid:
        params.append(sid)
    join = ""
    if need_user:
        join = _USER_JOIN
        params.append(user_id)

    row = get_store()._fetch_one(f"SELECT {', '.join(columns)} {_ANCHOR}{join}", params)
    if row is None:
        # 한 행 앵커라 정상적으로는 일어나지 않는다. 일어났다면 아무것도 채우지
        # 않고 종전 경로에 맡긴다 — 추측으로 캐시를 채우는 것보다 낫다.
        return

    if need_jti:
        _auth_cache.blacklist_cache().set(jti, bool(row.get("fg_jti_revoked")))
    if need_sid:
        _auth_cache.session_cache().set(sid, bool(row.get("fg_session_active")))
    if need_user:
        user = {k: v for k, v in row.items() if not k.startswith("fg_")}
        # LEFT JOIN 이 안 맞으면 user 컬럼이 전부 NULL 이다 = 그런 사용자가 없다.
        _auth_cache.user_cache().set(user_id, user if user.get("user_id") is not None else None)


def prefetch(jti: str | None, sid: str | None, user_id: str | None) -> None:
    """세 캐시 중 비어 있는 것들을 쿼리 한 번으로 채운다. 실패는 무시한다.

    두 개 이상 비어 있을 때만 움직인다. 하나뿐이면 합쳐도 왕복 수가 같고, 원래
    호출부가 쓰는 좁은 쿼리(인덱스 하나로 끝난다)를 쓰는 편이 낫다.
    """
    if not user_id or not _auth_cache.caching_enabled():
        # TTL=0 은 "캐시 이전 동작으로 돌려 달라" 는 뜻이다. 그때 프리페치는 채울
        # 곳이 없어 왕복을 하나 더 늘리기만 한다.
        return
    need_jti, need_sid, need_user = _missing(jti, sid, user_id)
    if (need_jti + need_sid + need_user) < 2:
        return
    try:
        _flight.do(
            ("preamble", jti, sid, user_id),
            lambda: _load(jti, sid, user_id, need_jti, need_sid, need_user),
        )
    except Exception:
        # 인증 경로에 새 실패 지점을 만들지 않는다: 못 채웠으면 호출부가 종전대로
        # 세 번 조회하고, 결과는 완전히 동일하다.
        return
