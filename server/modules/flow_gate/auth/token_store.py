"""Access blacklist and refresh-token persistence."""
from datetime import datetime,timezone
from modules.flow_gate.db.connection import get_store,now_iso
from . import auth_cache as _auth_cache
def _iso(dt):return dt.isoformat(timespec="seconds")
def blacklist_token(jti,user_id,exp_timestamp):
    get_store()._execute("INSERT OR IGNORE INTO token_blacklist (jti,user_id,revoked_at,expires_at) VALUES (?,?,?,?)",[jti,user_id,now_iso(),_iso(datetime.fromtimestamp(exp_timestamp,timezone.utc))])
    _auth_cache.invalidate_token(jti)  # revocation must take effect now, not in TTL seconds
def is_blacklisted(jti):
    # 0276 NR0003 발견 2: one of the five fixed per-request auth queries.
    return _auth_cache.blacklist_cache().get_or_load(
        jti, lambda: get_store()._fetch_one("SELECT jti FROM token_blacklist WHERE jti=?",[jti]) is not None)
def store_refresh_token(jti,user_id,expires_at,session_id=None):get_store()._execute("INSERT INTO refresh_tokens (jti,user_id,issued_at,expires_at,session_id) VALUES (?,?,?,?,?)",[jti,user_id,now_iso(),_iso(expires_at),session_id])
def get_refresh_token(jti):return get_store()._fetch_one("SELECT * FROM refresh_tokens WHERE jti=?",[jti])
def revoke_refresh_token(jti):get_store()._execute("UPDATE refresh_tokens SET revoked_at=? WHERE jti=?",[now_iso(),jti])
def revoke_all_refresh_tokens(user_id):get_store()._execute("UPDATE refresh_tokens SET revoked_at=? WHERE user_id=? AND revoked_at IS NULL",[now_iso(),user_id])
def rotate_refresh_token(old_jti,new_jti,user_id,new_expires_at,session_id=None):
    store=get_store()
    with store.transaction():
        now=now_iso(); store._execute("INSERT INTO refresh_tokens (jti,user_id,issued_at,expires_at,session_id) VALUES (?,?,?,?,?)",[new_jti,user_id,now,_iso(new_expires_at),session_id]); store._execute("UPDATE refresh_tokens SET revoked_at=?,replaced_by=? WHERE jti=?",[now,new_jti,old_jti])
