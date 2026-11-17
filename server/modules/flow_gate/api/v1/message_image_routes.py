"""Session-authenticated Flow message image endpoints."""
from fastapi import APIRouter, Depends, File, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from modules.flow_gate.auth.middleware import get_current_user
from modules.flow_gate.documents.attachments.naming import rfc5987_disposition
from modules.flow_gate.services import message_image_service as service

router = APIRouter(prefix="/flows/{flow_id}/message-images", tags=["MessageImages"])


@router.post("")
def upload(flow_id: str, file: UploadFile = File(...), user: dict = Depends(get_current_user)):
    try:
        return service.upload(flow_id, file, user)
    except service.MessageImageError as exc:
        return JSONResponse(status_code=exc.status, content=exc.body())


@router.get("/{image_id}/content")
def content(flow_id: str, image_id: str, user: dict = Depends(get_current_user)):
    try:
        row, path = service.content(flow_id, image_id, user)
        return FileResponse(path, media_type=row["media_type"], headers={
            "X-Content-Type-Options": "nosniff",
            "Content-Disposition": rfc5987_disposition(image_id).replace("attachment;", "inline;", 1),
            "Cache-Control": "private, no-store",
        })
    except service.MessageImageError as exc:
        return JSONResponse(status_code=exc.status, content=exc.body())


@router.delete("/{image_id}")
def remove(flow_id: str, image_id: str, user: dict = Depends(get_current_user)):
    try:
        return service.remove(flow_id, image_id, user)
    except service.MessageImageError as exc:
        return JSONResponse(status_code=exc.status, content=exc.body())
