"""Chunked realtime PCM API; browser auth never reaches the local voice module."""
from fastapi import APIRouter, Depends, File, Query, UploadFile
from pydantic import BaseModel, ConfigDict
from modules.flow_gate.auth.middleware import get_current_user
from modules.flow_gate.services import voice_streams
from .voice_routes import guard

router = APIRouter(prefix="/voice/streams", tags=["Voice"])


class Selection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    service: str | None = None
    model: str | None = None


@router.post("")
@guard
def start(body: Selection, user=Depends(get_current_user)):
    return voice_streams.start(user["user_id"], body.service, body.model)


@router.post("/{stream_id}/chunks")
@guard
def chunk(stream_id: str, file: UploadFile = File(...), index: int = Query(..., ge=0), final: bool = Query(False), user=Depends(get_current_user)):
    return voice_streams.chunk(stream_id, user["user_id"], file.file.read(160001), index, final)


@router.delete("/{stream_id}")
@guard
def cancel(stream_id: str, user=Depends(get_current_user)):
    return voice_streams.cancel(stream_id, user["user_id"])
