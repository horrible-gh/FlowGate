"""flowgate.v02.0045 T0011 — source-level guards for the Graph Workflow package.

D0004 §2.1 (one responsibility per file, fixed dependency direction, pure domain) with the
L0009 §1.5 numbers, and the S1/S2 v0.1 touch points (route registration only).
"""
from __future__ import annotations

import ast
from pathlib import Path

SERVER = Path(__file__).resolve().parents[1]
PKG = SERVER / "modules" / "flow_gate" / "graph_workflow"
PKG_MOD = "modules.flow_gate.graph_workflow"

SERVER_GRAPH_FILE_MAX_LINES = 400
SERVER_GRAPH_ROUTE_FILE_MAX_LINES = 250

LAYERS = ("api", "services", "projection", "store", "domain")
# layer -> Graph layers it may import (D0004 §2.2 dependency flow)
ALLOWED_GRAPH_LAYERS = {
    "api": {"api", "services", "domain"},
    "services": {"services", "projection", "store", "domain"},
    "projection": {"projection", "domain"},
    "store": {"store"},
    "domain": {"domain"},
}
# layer -> non-Graph modules.flow_gate prefixes it may import
ALLOWED_OUTSIDE = {
    "api": ("modules.flow_gate.services.auth_outbound", "modules.flow_gate.rbac.permission_service"),
    "services": (
        "modules.flow_gate.db.connection", "modules.flow_gate.db.groups",
        "modules.flow_gate.api.v1.events", "modules.flow_gate.services.work_plan_apply_service",
        "modules.flow_gate.process_service",
    ),
    "projection": ("modules.flow_gate.db.documents", "modules.flow_gate.db.groups", "modules.flow_gate.db.workflow_sequences"),
    "store": ("modules.flow_gate.db.connection",),
    "domain": (),
}


def _files():
    return sorted(p for p in PKG.rglob("*.py") if "__pycache__" not in p.parts)


def _layer(path: Path) -> str:
    rel = path.relative_to(PKG).parts
    return rel[0] if len(rel) > 1 else "package"


def _imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    base = ".".join([PKG_MOD, *path.relative_to(PKG).with_suffix("").parts[:-1]])
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                parts = base.split(".")
                parts = parts[: len(parts) - (node.level - 1)]
                module = ".".join(parts + ([node.module] if node.module else []))
            else:
                module = node.module or ""
            # `from pkg import mod` names a module: check pkg.mod (pkg.Name for plain names).
            out += [f"{module}.{a.name}" for a in node.names]
    return out


def test_every_file_stays_small():
    for path in _files():
        lines = len(path.read_text(encoding="utf-8").splitlines())
        limit = SERVER_GRAPH_ROUTE_FILE_MAX_LINES if path.name.endswith("_routes.py") else SERVER_GRAPH_FILE_MAX_LINES
        assert lines <= limit, f"{path.relative_to(SERVER)}: {lines} > {limit}"


def test_dependency_direction():
    for path in _files():
        layer = _layer(path)
        if layer == "package":
            continue
        assert layer in LAYERS, path
        for spec in _imports(path):
            where = f"{path.relative_to(SERVER)}: {spec}"
            if spec.startswith(PKG_MOD + "."):
                target = spec[len(PKG_MOD) + 1:].split(".")[0]
                assert target in ALLOWED_GRAPH_LAYERS[layer], where
            elif spec.startswith("modules."):
                assert spec.startswith(ALLOWED_OUTSIDE[layer]), where
            elif spec.split(".")[0] in ("fastapi", "starlette", "pydantic"):
                assert layer == "api", where


def test_domain_is_pure_standard_library():
    for path in (PKG / "domain").rglob("*.py"):
        for spec in _imports(path):
            top = spec.split(".")[0]
            assert spec.startswith(PKG_MOD + ".domain") or top in {
                "__future__", "dataclasses", "typing", "math", "itertools", "collections",
            }, f"{path.name}: {spec}"


def test_store_writes_only_graph_tables():
    import re

    for path in (PKG / "store").rglob("*.py"):
        src = path.read_text(encoding="utf-8")
        for table in re.findall(r"(?:INSERT INTO|(?<!DO )UPDATE|DELETE FROM)\s+(\w+)", src):
            assert table.startswith("graph_"), f"{path.name} writes {table}"


def test_v01_touch_points_are_route_registration_only():
    main = (SERVER / "routers" / "main.py").read_text(encoding="utf-8")
    graph_lines = [line for line in main.splitlines() if "graph" in line.lower()]
    assert graph_lines == [
        "from modules.flow_gate.graph_workflow.api.canvas_routes import router as _graph_canvas_router",
        'app.include_router(_graph_canvas_router, prefix=f"{CONTEXT}", tags=["GraphCanvas"])',
    ]


def test_migration_141_has_the_same_tables_in_three_dialects():
    import re

    names = {}
    for dialect in ("sqlite", "postgres", "mysql"):
        sql = (SERVER / "sql" / "migrations" / dialect / "141_graph_layout_identity.sql").read_text(encoding="utf-8")
        names[dialect] = re.findall(r"CREATE TABLE IF NOT EXISTS (\w+)", sql)
        assert ("CREATE TRIGGER" in sql) == (dialect == "sqlite"), dialect
    assert names["sqlite"] == names["postgres"] == names["mysql"] == [
        "graph_flow_guards", "graph_node_positions", "graph_viewports",
        "graph_identity_records", "graph_identity_rows", "graph_retired_node_keys",
    ]
