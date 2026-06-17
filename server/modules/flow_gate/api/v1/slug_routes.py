from fastapi import APIRouter
from pydantic import BaseModel
from ...utils.slug import romanize_to_slug

router = APIRouter(prefix="/api/v1/slug", tags=["slug"])


class _RomanizeBody(BaseModel):
    text: str


class _RomanizeResponse(BaseModel):
    suggested: str


@router.post("/romanize", response_model=_RomanizeResponse)
def romanize_endpoint(body: _RomanizeBody):
    try:
        suggested = romanize_to_slug(body.text)
    except ValueError:
        # Return empty string on romanization failure (front-end will prompt the user to enter manually)
        suggested = ""
    return _RomanizeResponse(suggested=suggested)
