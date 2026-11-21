"""Session-authenticated audio upload/playback/delete routes."""
from fastapi import APIRouter, Depends, File, Form, UploadFile
from fastapi.responses import FileResponse
from modules.flow_gate.auth.middleware import get_current_user
from modules.flow_gate.services import message_audio_service as service
from .voice_routes import guard

router = APIRouter(prefix="/flows/{flow_id}/message-audio", tags=["MessageAudio"])


@router.post("")
@guard
def upload(flow_id: str, file: UploadFile = File(...), service_id: str | None = Form(None, alias="service"),
           model: str | None = Form(None), user=Depends(get_current_user)):
    return service.upload(flow_id, file, user, service_id, model)


@router.get("/{audio_id}/content")
@guard
def content(flow_id: str, audio_id: str, user=Depends(get_current_user)):
    return FileResponse(service.content(flow_id, audio_id, user), media_type="audio/mp4",
                        headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "private, no-store"})


@router.delete("/{audio_id}")
@guard
def remove(flow_id: str, audio_id: str, user=Depends(get_current_user)):
    return service.remove(flow_id, audio_id, user)
