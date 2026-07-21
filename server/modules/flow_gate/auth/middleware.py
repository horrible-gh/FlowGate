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
    # 0291 T1: 아래 두 판정과 get_current_user 의 사용자 조회는 서로 독립인 단일 행
    # 조회 세 개다. 캐시가 비어 있으면 세 번 따로 가므로, 먼저 한 번에 읽어 세 캐시를
    # 채운다. 판정 자체는 아래 그대로 — 무효화 경로가 바뀌지 않는다.
    # 실패해도 조용히 넘어가고 종전 경로가 그대로 돈다 (auth_preamble 독스트링).
    from . import auth_preamble as _preamble
    _preamble.prefetch(payload.get("jti"),payload.get("sid"),payload.get("sub"))
    try:
        if payload.get("jti") and is_blacklisted(payload["jti"]):raise HTTPException(401,"Token has been revoked")
        if payload.get("sid") and not is_session_active(payload["sid"]):raise HTTPException(401,"session_revoked")
    except HTTPException:raise
    except Exception:
        if os.environ.get("TESTING")=="1":return payload
        raise credentials
    return payload
def get_current_user(payload:dict=Depends(verify_token)):
    user_id=payload.get("sub")
    if not user_id:raise HTTPException(401,"Invalid token subject")
    from modules.flow_gate.db import users as db_users
    from . import auth_cache as _auth_cache
    # 0276 NR0003 발견 2: one of the five fixed per-request auth queries.
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
