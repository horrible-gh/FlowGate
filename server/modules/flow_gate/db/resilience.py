"""PostgreSQL checkout validation. Never replay application statements or transactions."""
import logging
import time
from types import MethodType
from modules.flow_gate.api.request_observability import current_request_id

log = logging.getLogger(__name__)

def unavailable(exc):
    """Driver availability errors only; invalid credentials remain auth failures."""
    seen = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        if type(exc).__name__ in {"OperationalError", "InterfaceError", "PoolTimeoutError", "PoolError"}:
            return True
        exc = exc.__cause__ or exc.__context__
    return False

def auth_unavailable(exc):
    from fastapi import HTTPException
    log.warning("database unavailable during authentication type=%s request_id=%s", type(exc).__name__, current_request_id() or "-")
    return HTTPException(503, "Authentication service temporarily unavailable", headers={"Retry-After": "1"})

def install_checkout_validation(db):
    # This adapter is deliberately scoped to the pinned sqloader PostgreSQL wrapper.
    from sqloader.postgresql import PostgreSQLWrapper
    if not isinstance(db, PostgreSQLWrapper) or getattr(db, "_flowgate_validated", False):
        return
    db._checkout = MethodType(_checkout, db)
    db._flowgate_validated = True

def _checkout(db):
    # A whole idle pool can have died together. Drain all old entries before giving up.
    for attempt in range(db.pool_max + 1):
        conn = db.pool.getconn()
        try:
            if db._is_stale(conn):
                db._discard(conn)
                continue
            conn.autocommit = False
            with conn.cursor() as cursor:
                cursor.execute("SELECT 1")
            conn.rollback()  # No implicit ping transaction may reach the caller.
        except Exception as exc:
            db._discard(conn)
            log.warning("database checkout failed type=%s attempt=%s request_id=%s", type(exc).__name__, attempt + 1, current_request_id() or "-")
            if not unavailable(exc) or attempt == db.pool_max:
                raise
            continue
        with db._meta_lock:
            db._conn_meta.setdefault(id(conn), {"created": time.monotonic()})["returned"] = None
        return conn
    from psycopg2 import OperationalError
    raise OperationalError("No healthy database connection available")
