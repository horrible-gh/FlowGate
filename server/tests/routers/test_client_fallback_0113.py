"""API misses stay 404 with a built SPA; registered method errors stay 405."""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from routers.client_fallback import api_aware_fallback


@pytest.fixture
def client():
    app = FastAPI()
    @app.get("/flowgate/api/v1/existing")
    def existing():
        return {"ok": True}
    app.router.add_api_route("/{full_path:path}", lambda full_path: {"shell": True}, methods=["GET"],
                             route_class_override=api_aware_fallback("/flowgate"))
    return TestClient(app)


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
@pytest.mark.parametrize("prefix", ["/api/v1", "/flowgate/api/v1"])
def test_missing_api_is_404(client, method, prefix):
    assert client.request(method, prefix + "/runs/new-run/nodes/card/recover").status_code == 404


def test_existing_route_and_spa_method_contracts(client):
    response = client.post("/flowgate/api/v1/existing")
    assert response.status_code == 405 and response.headers["allow"] == "GET"
    assert client.get("/flowgate/api/v1/existing").json() == {"ok": True}
    assert client.get("/main/documents").json() == {"shell": True}
    assert client.post("/main/documents").status_code == 405
