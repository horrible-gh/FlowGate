"""Regression coverage for shared input validation and escaped 500 responses."""
import pytest
from fastapi import Depends, FastAPI, Request, UploadFile, File
from fastapi.testclient import TestClient
from modules.flow_gate.api.input_validation import reject_nul_input
from modules.flow_gate.api.request_observability import RequestIdMiddleware, unhandled_error_response


@pytest.fixture
def client():
    app = FastAPI(dependencies=[Depends(reject_nul_input)])
    app.add_middleware(RequestIdMiddleware)
    app.add_exception_handler(Exception, unhandled_error_response)

    @app.post("/login")
    async def login(request: Request):
        return await request.json()

    @app.get("/text/{value}")
    def text(value: str):
        return {"value": value}

    @app.post("/form")
    async def form(request: Request):
        return dict(await request.form())

    @app.post("/upload")
    async def upload(file: UploadFile = File()):
        return {"size": len(await file.read())}

    @app.get("/boom")
    def boom():
        raise ValueError("do not expose this")

    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


@pytest.mark.parametrize("body", [
    {"username": "user\0", "password": "secret"},
    {"outer": [{"request_id": "a\0b"}]}, {"bad\0key": "ok"},
])
def test_json_nul_rejected_before_handler(client, body):
    r = client.post("/login", json=body)
    assert r.status_code == 422
    assert "secret" not in r.text
    assert r.headers["x-request-id"]


@pytest.mark.parametrize("url", ["/text/a%00b", "/text/good?q=a%00b"])
def test_url_nul_rejected(client, url):
    assert client.get(url).status_code == 422


def test_form_and_upload_text_checked_binary_unchanged(client):
    assert client.post("/form", data={"username": "a\0b"}).status_code == 422
    assert client.post("/form", data={"username": "normal"}).json() == {"username": "normal"}
    assert client.post("/upload", files={"file": ("good.bin", b"a\0b")}).json() == {"size": 3}
    # httpx escapes NUL in filenames as %00; send the actual wire value to test NUL.
    body = (b'--test-boundary\r\nContent-Disposition: form-data; name="file"; '
            b'filename="bad\0.bin"\r\nContent-Type: application/octet-stream\r\n\r\nabc'
            b'\r\n--test-boundary--\r\n')
    assert client.post("/upload", content=body, headers={
        "Content-Type": "multipart/form-data; boundary=test-boundary"}).status_code == 422


def test_normal_json_and_literal_escape_preserved(client):
    body = {"username": "normal", "text": r"literal \u0000"}
    assert client.post("/login", json=body).json() == body


def test_unhandled_500_has_id_without_exposing_exception(client):
    r = client.get("/boom", headers={"X-Request-ID": "req-error-0050"})
    assert r.status_code == 500
    assert r.headers["x-request-id"] == "req-error-0050"
    assert r.text == "Internal Server Error"
