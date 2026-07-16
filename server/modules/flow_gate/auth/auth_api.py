"""Authentication API including per-login session management."""
from datetime import datetime,timezone
from typing import Optional
import jwt
from fastapi import APIRouter,Depends,HTTPException,Request
from pydantic import BaseModel
from modules.flow_gate.db import users as db_users
from .backup_codes import generate_codes,store_codes,verify_backup_code
from .jwt_service import create_access_token,create_refresh_token,create_temp_token,decode_token,decode_token_no_verify_exp
from .middleware import get_current_user,verify_token
from .password import hash_password,validate_password,verify_password
from .token_store import blacklist_token,get_refresh_token,revoke_refresh_token,rotate_refresh_token,store_refresh_token
from .session_store import create_request_session,create_session,get_session,list_active_sessions,revoke_all_sessions,revoke_other_sessions,revoke_session,touch_session
from .totp_service import TOTP_LOCK_MAX_ATTEMPTS,TOTP_LOCK_MINUTES,encrypt_totp_secret,generate_totp_secret,get_totp_provisioning_uri,verify_totp_code
router=APIRouter()
def _now_utc():return datetime.now(timezone.utc)
def _roles(uid):
    try:
        from modules.flow_gate.db.connection import get_store
        return [r["role_name"] for r in get_store()._fetch_all("SELECT DISTINCT r.role_name FROM roles r JOIN user_project_roles upr ON r.role_id=upr.role_id WHERE upr.user_id=?",[uid])]
    except Exception:return []
def _tokens(user,request):
    uid=user["user_id"]; sid=create_request_session(uid,request); roles=_roles(uid)
    access,_=create_access_token(uid,user["username"],roles,bool(user.get("is_admin")),sid=sid)
    refresh,jti,expires=create_refresh_token(uid,sid=sid); store_refresh_token(jti,uid,expires,sid)
    return {"access_token":access,"refresh_token":refresh,"token_type":"bearer","first_login_required":bool(user.get("first_login_required")),"user":{"user_id":uid,"username":user["username"],"email":user["email"]}}
def _lock(user,prefix):
    value=user.get(prefix+"_locked_until")
    if value:
        until=datetime.fromisoformat(value)
        if until.tzinfo is None:until=until.replace(tzinfo=timezone.utc)
        if _now_utc()<until:raise HTTPException(423,{"code":"account_locked","locked_until":value})
def _failed(user,prefix):
    from datetime import timedelta
    count=(user.get(prefix+"_failed_count") or 0)+1; values={prefix+"_failed_count":count}
    if count>=5:values[prefix+"_locked_until"]=(_now_utc()+timedelta(minutes=15)).isoformat(timespec="seconds")
    db_users.update(user["user_id"],values)
def _reset(uid,prefix):db_users.update(uid,{prefix+"_failed_count":0,prefix+"_locked_until":None})
class LoginRequest(BaseModel):username:str; password:str; locale:str="en"
class TotpVerifyRequest(BaseModel):temp_token:str; code:str
class TotpBackupRequest(BaseModel):temp_token:str; backup_code:str
class RefreshRequest(BaseModel):refresh_token:str
class LogoutRequest(BaseModel):refresh_token:Optional[str]=None
class PasswordChangeRequest(BaseModel):current_password:Optional[str]=None; new_password:str
@router.post("/login")
async def login(request:Request,body:LoginRequest):
    user=db_users.get_by_username(body.username) or (db_users.get_by_email(body.username) if "@" in body.username else None)
    if not user:raise HTTPException(400,"invalid_credentials")
    _lock(user,"login")
    if not verify_password(body.password,user.get("password","")):_failed(user,"login"); raise HTTPException(400,"invalid_credentials")
    _reset(user["user_id"],"login")
    if not user.get("is_active"):raise HTTPException(403,"account_inactive")
    if user.get("totp_secret"):
        token,_=create_temp_token(user["user_id"]); return {"totp_required":True,"temp_token":token}
    return _tokens(user,request)
def _temp_user(token):
    try:payload=decode_token(token)
    except jwt.ExpiredSignatureError:raise HTTPException(401,"token_expired")
    except jwt.InvalidTokenError:raise HTTPException(401,"token_expired")
    if payload.get("type")!="temp" or not payload.get("totp_pending"):raise HTTPException(401,"token_expired")
    user=db_users.get_by_id(payload.get("sub"))
    if not user:raise HTTPException(401,"token_expired")
    return user
@router.post("/totp/verify")
async def totp_verify(request:Request,body:TotpVerifyRequest):
    user=_temp_user(body.temp_token); _lock(user,"totp")
    if not verify_totp_code(user.get("totp_secret"),body.code):_failed(user,"totp"); raise HTTPException(401,"invalid_code")
    _reset(user["user_id"],"totp"); return _tokens(user,request)
@router.post("/totp/backup")
async def totp_backup(request:Request,body:TotpBackupRequest):
    user=_temp_user(body.temp_token); _lock(user,"totp")
    if not verify_backup_code(user["user_id"],body.backup_code):_failed(user,"totp"); raise HTTPException(401,"invalid_backup_code")
    _reset(user["user_id"],"totp"); return _tokens(user,request)
@router.post("/totp/setup")
async def totp_setup(request:Request,current_user:dict=Depends(get_current_user)):
    secret=generate_totp_secret(); db_users.update(current_user["user_id"],{"totp_secret":encrypt_totp_secret(secret)}); codes=generate_codes(); store_codes(current_user["user_id"],codes)
    return {"qr_uri":get_totp_provisioning_uri(secret,current_user.get("username",current_user["user_id"])),"backup_codes":codes,"secret_masked":secret[:4]+"****"}
@router.post("/refresh")
async def refresh(request:Request,body:RefreshRequest):
    try:payload=decode_token(body.refresh_token)
    except jwt.ExpiredSignatureError:raise HTTPException(401,"Token has expired")
    except jwt.InvalidTokenError:raise HTTPException(401,"Invalid authentication credentials")
    if payload.get("type")!="refresh" or not payload.get("jti") or not payload.get("sub"):raise HTTPException(401,"Invalid authentication credentials")
    jti,uid=payload["jti"],payload["sub"]; row=get_refresh_token(jti)
    if not row:raise HTTPException(401,"Invalid authentication credentials")
    if row.get("revoked_at"):
        sid=row.get("session_id"); session=get_session(sid) if sid else None
        if sid and session and session.get("revoked_at") and not row.get("replaced_by"):raise HTTPException(401,"session_revoked")
        revoke_all_sessions(uid,"reuse_detected"); raise HTTPException(401,"Token reuse detected. All sessions revoked.")
    expires=datetime.fromisoformat(row["expires_at"])
    if expires.tzinfo is None:expires=expires.replace(tzinfo=timezone.utc)
    if _now_utc()>expires:revoke_refresh_token(jti); raise HTTPException(401,"Token has expired")
    user=db_users.get_by_id(uid)
    if not user or not user.get("is_active"):raise HTTPException(401,"Invalid authentication credentials")
    sid=row.get("session_id")
    if sid:
        session=get_session(sid)
        if not session or session.get("revoked_at"):raise HTTPException(401,"session_revoked")
    else:sid=create_session(uid)
    access,_=create_access_token(uid,user["username"],_roles(uid),bool(user.get("is_admin")),sid=sid)
    refresh_token,new_jti,_=create_refresh_token(uid,expires,sid); rotate_refresh_token(jti,new_jti,uid,expires,sid); touch_session(sid)
    return {"access_token":access,"refresh_token":refresh_token,"token_type":"bearer"}
@router.post("/logout")
async def logout(request:Request,body:LogoutRequest,payload:dict=Depends(verify_token)):
    uid=payload.get("sub"); sid=payload.get("sid")
    if payload.get("jti") and uid:blacklist_token(payload["jti"],uid,payload.get("exp",0))
    if body.refresh_token:
        try:
            ref=decode_token_no_verify_exp(body.refresh_token); sid=ref.get("sid") or sid
            if ref.get("jti"):revoke_refresh_token(ref["jti"])
        except Exception:pass
    if sid and uid:revoke_session(sid,uid,"logout")
    return {"message":"Logged out successfully"}
@router.post("/password/change")
async def password_change(request:Request,body:PasswordChangeRequest,current_user:dict=Depends(get_current_user),payload:dict=Depends(verify_token)):
    uid=current_user["user_id"]
    if not current_user.get("first_login_required"):
        if not body.current_password:raise HTTPException(400,"current_password_required")
        if not verify_password(body.current_password,current_user.get("password","")):raise HTTPException(401,"current_password_incorrect")
    if body.current_password and verify_password(body.new_password,current_user.get("password","")):raise HTTPException(422,"same_as_current")
    violations=validate_password(body.new_password)
    if violations:raise HTTPException(400,{"code":"invalid_password_policy","violations":violations})
    db_users.update(uid,{"password":hash_password(body.new_password),"first_login_required":0})
    if payload.get("sid"):revoke_other_sessions(uid,payload["sid"],"password_change")
    else:revoke_all_sessions(uid,"password_change")
    return {"message":"Password changed successfully","first_login_required":False}
@router.get("/sessions")
async def sessions(payload:dict=Depends(verify_token)):
    sid=payload.get("sid"); rows=list_active_sessions(payload["sub"])
    return {"sessions":[{**row,"is_current":bool(sid and row["session_id"]==sid)} for row in rows]}
@router.delete("/sessions/{session_id}")
async def delete_session(session_id:str,payload:dict=Depends(verify_token)):
    if session_id==payload.get("sid"):raise HTTPException(409,"current_session_use_logout")
    if not revoke_session(session_id,payload["sub"],"remote"):raise HTTPException(404,"session_not_found")
    return {"message":"Session revoked","session_id":session_id}
@router.post("/sessions/revoke-others")
async def revoke_others(payload:dict=Depends(verify_token)):
    if not payload.get("sid"):raise HTTPException(409,"current_session_unknown")
    count=revoke_other_sessions(payload["sub"],payload["sid"],"revoke_others")
    return {"message":"Other sessions revoked","revoked_count":count}
@router.get("/me")
async def me(current_user:dict=Depends(get_current_user)):
    return {"user_id":current_user["user_id"],"username":current_user.get("username"),"email":current_user.get("email"),"is_admin":bool(current_user.get("is_admin")),"first_login_required":bool(current_user.get("first_login_required")),"roles":_roles(current_user["user_id"])}

# Backward-compatible helper names used by the established authentication tests.
def _check_totp_lock(user): return _lock(user,"totp")
def _increment_totp_fail(user): return _failed(user,"totp")
def _reset_totp_lock(user_id): return _reset(user_id,"totp")
def _check_login_lock(user): return _lock(user,"login")
def _increment_login_fail(user): return _failed(user,"login")
def _reset_login_lock(user_id): return _reset(user_id,"login")
