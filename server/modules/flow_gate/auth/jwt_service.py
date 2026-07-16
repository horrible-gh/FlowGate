"""JWT creation and verification."""
from __future__ import annotations
import os, uuid
from datetime import datetime, timedelta, timezone
from typing import Optional
import jwt
SECRET_KEY=os.environ.get("SECRET_KEY",""); ALGORITHM="HS256"; ACCESS_TOKEN_EXPIRE_MINUTES=30; REFRESH_TOKEN_EXPIRE_DAYS=14; TOTP_PENDING_EXPIRE_MINUTES=5
def _get_secret():
    if SECRET_KEY:return SECRET_KEY
    from config import settings
    return settings.SECRET_KEY
def get_access_token_expire_minutes():
    try:
        from config import settings
        return int(settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    except Exception:
        try:return int(os.environ.get("ACCESS_TOKEN_EXPIRE_MINUTES",ACCESS_TOKEN_EXPIRE_MINUTES))
        except (TypeError,ValueError):return ACCESS_TOKEN_EXPIRE_MINUTES
def create_access_token(user_id,username,roles,is_admin=False,expires_delta=None,sid=None):
    jti,now=str(uuid.uuid4()),datetime.now(timezone.utc); payload={"sub":user_id,"username":username,"roles":roles,"is_admin":is_admin,"jti":jti,"type":"access","iat":int(now.timestamp()),"exp":int((now+(expires_delta or timedelta(minutes=get_access_token_expire_minutes()))).timestamp())}
    if sid:payload["sid"]=sid
    return jwt.encode(payload,_get_secret(),algorithm=ALGORITHM),jti
def create_refresh_token(user_id,expires_at=None,sid=None):
    jti,now=str(uuid.uuid4()),datetime.now(timezone.utc); expires_at=expires_at or now+timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS); payload={"sub":user_id,"jti":jti,"type":"refresh","iat":int(now.timestamp()),"exp":int(expires_at.timestamp())}
    if sid:payload["sid"]=sid
    return jwt.encode(payload,_get_secret(),algorithm=ALGORITHM),jti,expires_at
def create_temp_token(user_id):
    jti,now=str(uuid.uuid4()),datetime.now(timezone.utc)
    return jwt.encode({"sub":user_id,"jti":jti,"type":"temp","totp_pending":True,"iat":int(now.timestamp()),"exp":int((now+timedelta(minutes=TOTP_PENDING_EXPIRE_MINUTES)).timestamp())},_get_secret(),algorithm=ALGORITHM),jti
def decode_token(token):return jwt.decode(token,_get_secret(),algorithms=[ALGORITHM])
def decode_token_no_verify_exp(token):return jwt.decode(token,_get_secret(),algorithms=[ALGORITHM],options={"verify_exp":False})
