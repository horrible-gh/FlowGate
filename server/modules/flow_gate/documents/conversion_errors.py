"""Refusals of the R↔B root conversion in the flat P0006 §3.5 shape (0064 L0005 2.5~2.8).

``ConversionRefused`` is an ``HTTPException`` so a direct caller still sees its status code, but the
route answers with ``response()``: ``{"error", "detail", "doc_id", ...}`` at the top level, the shape
the v0.1 contact refusals already use. The route handles it before the general ``409 "Conversion
failed"`` so the logical reason is never hidden.
"""
from __future__ import annotations

from typing import Any, Optional

from fastapi import HTTPException
from fastapi.responses import JSONResponse

DETAILS = {
    "root_body_unreadable": "The root body cannot be read, so it cannot be converted; recover the document first.",
    "front_matter_identity_unsafe": (
        "The front matter identity fields cannot be rewritten safely; correct them and convert again."),
    "attachment_target_rows_exist": (
        "Attachment rows already exist for the target document id; nothing was changed."),
    "attachment_path_unsafe": (
        "An attachment path is unsafe; nothing was changed. Fix the attachment and convert again."),
    "attachments_changed": "The attachments changed during the conversion; nothing was changed. Try again.",
    "attachment_target_exists": (
        "An attachment file already exists in the target attachment folder; nothing was changed."),
    "attachment_copy_mismatch": "An attachment could not be copied intact; nothing was changed. Try again.",
    "draft_version_conflict": "The Flow draft changed during the conversion; nothing was changed. Try again.",
    "transaction_required": "The conversion must run inside one transaction; nothing was changed.",
}
READ_ERROR_DETAIL = (
    "The root body cannot be read, so it cannot be converted; fix file permissions or encoding first.")


class ConversionRefused(HTTPException):
    """One refusal: ``code`` is the P0006 ``error`` value; ``extra`` the items that follow ``doc_id``."""

    def __init__(self, code: str, *, doc_id: Optional[str] = None, status_code: int = 409,
                 body: Optional[dict] = None, **extra: Any) -> None:
        if body is None:
            detail = (READ_ERROR_DETAIL if code == "root_body_unreadable" and extra.get("reason") == "read_error"
                      else DETAILS[code])
            body = {"error": code, "detail": detail, "doc_id": doc_id,
                    **{k: v for k, v in extra.items() if v is not None}}
        super().__init__(status_code=status_code, detail=body.get("detail") or code)
        self.code = code
        self.payload = body

    def body(self) -> dict:
        return dict(self.payload)

    def response(self) -> JSONResponse:
        return JSONResponse(status_code=self.status_code, content=self.body())
