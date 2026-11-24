"""Authenticated voice API; synchronous work runs in FastAPI's worker pool."""
from functools import wraps
from fastapi import APIRouter, Depends, File, Form, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, StrictBool
from modules.flow_gate.auth.middleware import get_current_user
from modules.flow_gate.services.voice_errors import VoiceError
from modules.flow_gate.services.voice_runtime import MAX_AUDIO_BYTES
from modules.flow_gate.services.voice_service import manager

router = APIRouter(prefix="/voice", tags=["Voice"])


def guard(handler):
    @wraps(handler)
    def wrapped(*args, **kwargs):
        try:
            return handler(*args, **kwargs)
        except VoiceError as exc:
            return JSONResponse(status_code=exc.status_code, content=exc.response())
    return wrapped


def admin(user):
    if not user.get("is_admin"):
        raise VoiceError("voice_admin_required", "Administrator access required", 403)


class Selection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    service: str
    model: str


class Enabled(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: StrictBool


@router.get("/status")
@guard
def status(user=Depends(get_current_user)):
    return manager.status(bool(user.get("is_admin")))


@router.get("/catalog")
@guard
def catalog(user=Depends(get_current_user)):
    return manager.catalog(bool(user.get("is_admin")))


@router.post("/installs", status_code=202)
@guard
def install(body: Selection, user=Depends(get_current_user)):
    admin(user)
    return manager.install(body.service, body.model)


@router.get("/installs/{install_id}")
@guard
def install_status(install_id: str, user=Depends(get_current_user)):
    admin(user)
    return manager.job(install_id)


@router.delete("/installs/{install_id}")
@guard
def cancel(install_id: str, user=Depends(get_current_user)):
    admin(user)
    return manager.cancel(install_id)


@router.delete("/services/{service}/models/{model}")
@guard
def remove(service: str, model: str, user=Depends(get_current_user)):
    admin(user)
    return manager.remove(service, model)


@router.put("/default")
@guard
def default(body: Selection, user=Depends(get_current_user)):
    admin(user)
    return manager.set_default(body.service, body.model)


@router.put("/enabled")
@guard
def enabled(body: Enabled, user=Depends(get_current_user)):
    admin(user)
    return manager.enable(body.enabled)


@router.post("/restart")
@guard
def restart(user=Depends(get_current_user)):
    admin(user)
    return manager.restart()


@router.post("/transcriptions")
@guard
def transcribe(file: UploadFile = File(...), service: str | None = Form(None), model: str | None = Form(None), user=Depends(get_current_user)):
    return manager.transcribe(file.file.read(MAX_AUDIO_BYTES + 1), service, model, user["user_id"])


@router.post("/test")
@guard
def test(file: UploadFile = File(...), service: str | None = Form(None), model: str | None = Form(None), user=Depends(get_current_user)):
    admin(user)
    return manager.transcribe(file.file.read(MAX_AUDIO_BYTES + 1), service, model, user["user_id"], testing=True)
