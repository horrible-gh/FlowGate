"""Human Flow attachments: project RBAC and Flow guards are checked by the service."""
from fastapi import APIRouter, Depends, UploadFile, File, Form, Query, Body
from fastapi.responses import FileResponse, StreamingResponse
from starlette.background import BackgroundTask
from modules.flow_gate.auth.middleware import get_current_user
from . import flow
from .errors import AttachmentError
from .requests import RequestError

router = APIRouter()

def invoke(call):
    try:
        return call()
    except AttachmentError as exc:
        return exc.response()
    except RequestError as exc:
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=exc.status, content=exc.body())

@router.get("/flows/{flow_id}/attachment-requests")
def pending_files(flow_id: str, user: dict = Depends(get_current_user)):
    return invoke(lambda: flow.pending_requests(flow_id, user))

@router.get("/flows/{flow_id}/attachments")
def list_files(flow_id: str, user: dict = Depends(get_current_user)):
    return invoke(lambda: flow.listing(flow_id, user))

@router.post("/flows/{flow_id}/attachments", status_code=201)
def upload_file(flow_id: str, file: UploadFile = File(...), conflict: str = Form("ask"),
                expected_sha256: str = Form(""), key: str = Form(""), user: dict = Depends(get_current_user)):
    return invoke(lambda: flow.upload(flow_id, file, user, conflict, expected_sha256, key or None))

@router.get("/flows/{flow_id}/attachments/{key}")
def download_file(flow_id: str, key: str, user: dict = Depends(get_current_user)):
    def call():
        path, meta = flow.download(flow_id, key, user)
        return FileResponse(path, media_type=meta["media_type"], headers=meta["headers"])
    return invoke(call)

@router.delete("/flows/{flow_id}/attachments/{key}")
def delete_file(flow_id: str, key: str, expected_sha256: str = Query(...), user: dict = Depends(get_current_user)):
    return invoke(lambda: flow.remove(flow_id, key, user, expected_sha256))

@router.post("/flows/{flow_id}/attachments-zip")
def download_zip(flow_id: str, keys: list[str] = Body(..., embed=True), user: dict = Depends(get_current_user)):
    def call():
        stream = flow.archive(flow_id, keys, user)
        def chunks():
            while block := stream.read(65536):
                yield block
        return StreamingResponse(chunks(), media_type="application/zip",
            headers={"Content-Disposition": "attachment; filename=attachments.zip", "X-Content-Type-Options": "nosniff"},
            background=BackgroundTask(stream.close))
    return invoke(call)
