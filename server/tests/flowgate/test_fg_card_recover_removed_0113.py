"""0113 T#2: the removed manual card endpoint cannot mutate a run in any node state."""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from modules.flow_gate.graph_workflow.api import run_routes
from modules.flow_gate.graph_workflow.services import run_state_ports


@pytest.mark.parametrize("state", ["pending", "open", "review", "done", "rejected"])
def test_removed_endpoint_is_404_without_dispatch(monkeypatch, state):
    def forbidden(*args, **kwargs):
        raise AssertionError("removed endpoint must never load or mutate a run")
    monkeypatch.setattr(run_routes.run_service, "run_flow_id", forbidden)
    app = FastAPI()
    app.include_router(run_routes.router)
    from routers.client_fallback import api_aware_fallback
    app.router.add_api_route("/{full_path:path}", lambda full_path: {"shell": True}, methods=["GET"],
                             route_class_override=api_aware_fallback("/flowgate"))
    response = TestClient(app).post(f"/api/v1/runs/new-verification-run/nodes/{state}/recover", json={})
    assert response.status_code == 404
    assert all(not route.path.endswith("/recover") for route in app.routes)
    assert TestClient(app).post("/api/v1/runs/existing").status_code == 405
    assert TestClient(app).post("/app/designer").status_code == 405
    assert TestClient(app).get("/app/designer").json() == {"shell": True}


def test_manual_recovery_port_is_absent():
    assert "recover_card" not in run_state_ports.PORT_NAMES
    assert not hasattr(run_state_ports, "recover_card")
