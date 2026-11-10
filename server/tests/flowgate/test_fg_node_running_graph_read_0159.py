"""0159 R5: real authenticated graph_read and user run read expose the same cause."""
import pytest
from test_graph_read_flow_0145 import (
    env, db_path, decided, wired, active, document_write_ports, starts, fork_join, completions,
    get, ok, FA, _h,
)

pytestmark = pytest.mark.usefixtures("fork_join", "completions", "document_write_ports")


def test_graph_read_exposes_same_running_cause_as_user_run_read(active):
    env, key, graph, writer, worker, chat, live = active
    user = env["client"].get(f"/api/v1/flows/{FA}/runs/current", headers=_h())
    assert user.status_code == 200, user.text
    user_node = next(n for n in user.json()["run"]["node_runs"] if n["node_key"] == key)
    assert (user_node["node_running"], user_node["node_running_cause"]) == (True, "graph_exec:"+writer["intent_exec_id"])
    for token in (worker, chat):
        current = ok(get(env, token, "runs/current"))
        ai_node = next(n for n in current["run"]["node_runs"] if n["node_key"] == key)
        assert ai_node["node_running_cause"] == user_node["node_running_cause"]
        canvas = ok(get(env, token, "canvas"))
        node = next(n for n in canvas["nodes"] if n["node_key"] == key)
        assert node["node_run"]["node_running_cause"] == user_node["node_running_cause"]
