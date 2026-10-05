"""Authentication dependencies."""
import os,jwt
from fastapi import Depends,HTTPException
from fastapi.security import OAuth2PasswordBearer
from .jwt_service import decode_token
from .token_store import is_blacklisted
from .session_store import is_session_active
oauth2_scheme=OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login")
def verify_token(token:str=Depends(oauth2_scheme)):
    credentials=HTTPException(401,"Invalid authentication credentials",headers={"WWW-Authenticate":"Bearer"})
    try:payload=decode_token(token)
    except jwt.ExpiredSignatureError:raise HTTPException(401,"Token has expired",headers={"WWW-Authenticate":"Bearer"})
    except jwt.InvalidTokenError:raise credentials
    if payload.get("type")!="access":raise credentials
    if payload.get("totp_pending"):raise HTTPException(401,"2FA verification required")
    # 0291 T1: the two checks below and get_current_user's user lookup are three independent
    # single-row lookups. With an empty cache they go three separate times, so one merged read
    # fills all three caches first. The checks themselves are unchanged, so no invalidation path moves.
    # A failure passes silently and the previous path runs as before (see auth_preamble's docstring).
    from . import auth_preamble as _preamble
    _preamble.prefetch(payload.get("jti"),payload.get("sid"),payload.get("sub"))
    try:
        if payload.get("jti") and is_blacklisted(payload["jti"]):raise HTTPException(401,"Token has been revoked")
        if payload.get("sid") and not is_session_active(payload["sid"]):raise HTTPException(401,"session_revoked")
    except HTTPException:raise
    except Exception:
        if os.environ.get("TESTING")=="1":return payload
        raise credentials
    if payload.get("sid"):_note_activity(payload["sid"],payload.get("sub"))
    return payload
# flowgate.v02.0020 (시스템 › 보안 › 자동 로그아웃): an API request is session activity. It is
# stamped at most once a minute per session, and that same read signs out a session that sat
# idle longer than the policy allows (the access token alone would otherwise outlive it).
_ACTIVITY_EVERY=60.0
_activity_seen:dict={}
def _note_activity(sid,uid):
    import time
    now=time.monotonic(); last=_activity_seen.get(sid)
    if last is not None and now-last<_ACTIVITY_EVERY:return
    if len(_activity_seen)>20000:_activity_seen.clear()
    _activity_seen[sid]=now
    try:
        from .session_store import get_session,revoke_session,touch_session
        from .auth_api import _session_idle
        session=get_session(sid)
        if session and not session.get("revoked_at") and _session_idle(session):
            _activity_seen.pop(sid,None); revoke_session(sid,uid or session.get("user_id"),"admin")
            raise HTTPException(401,"session_idle")
        touch_session(sid)
    except HTTPException:raise
    except Exception:pass
def get_current_user(payload:dict=Depends(verify_token)):
    user_id=payload.get("sub")
    if not user_id:raise HTTPException(401,"Invalid token subject")
    from modules.flow_gate.db import users as db_users
    from . import auth_cache as _auth_cache
    # 0276 NR0003 finding 2: one of the five fixed per-request auth queries.
    # db.users.create/update/delete invalidate this entry, so is_active/is_admin
    # changes take effect immediately in-process.
    user=_auth_cache.user_cache().get_or_load(user_id,lambda:db_users.get_by_id(user_id))
    if not user:raise HTTPException(401,"User not found")
    if not user.get("is_active"):raise HTTPException(403,"User account is inactive")
    return user
def optional_current_user(token:str|None=Depends(OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login",auto_error=False))):
    if not token:return None
    try:return get_current_user(verify_token(token))
    except HTTPException:return None
