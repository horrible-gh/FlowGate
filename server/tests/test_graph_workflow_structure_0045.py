"""flowgate.v02.0045 T0011 / T0013 / T0015 — source-level guards for the Graph Workflow package.

D0004 §2.1 (one responsibility per file, fixed dependency direction, pure domain) with the
L0009 §1.5 numbers, and the v0.1 touch points of D0004 §4.3: route registration (S1~S5) and the
S4 engine-router delegation calls (one import + one call per contact). S5's document node
executor only calls the existing token service (D0004 §2.2 "호출만").
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
        "modules.flow_gate.db.connection", "modules.flow_gate.db.groups", "modules.flow_gate.db.documents",
        "modules.flow_gate.api.v1.events", "modules.flow_gate.services.work_plan_apply_service",
        "modules.flow_gate.process_service", "modules.flow_gate.services.token_service",
        # S5 AI node start (D0004 §3.4, L0009 §2.6.6): the existing AI invoke and its mention, called only.
        "modules.flow_gate.services.ai_invoke_service", "modules.flow_gate.services.mention_service",
        # flowgate.v02.0056 T#3: the card memo port the AI node start's mention asks (stdlib only, D0006 §2.3).
        "modules.flow_gate.services.card_memo_hooks",
        # flowgate.v02.0064 T-B1e: the body precheck the AI node start's mention asks (L0005 2.14), called only.
        "modules.flow_gate.services.ai_start_precheck",
        # flowgate.v02.0064 T-B4d: the card title port (inbox candidate) and the mention's title request wording.
        "modules.flow_gate.services.node_title_hooks",
    ),
    "projection": (
        "modules.flow_gate.db.documents", "modules.flow_gate.db.groups", "modules.flow_gate.db.workflow_sequences",
        "modules.flow_gate.db.connection",
    ),
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


def test_route_registration_is_the_only_main_py_touch_point():
    main = (SERVER / "routers" / "main.py").read_text(encoding="utf-8")
    graph_lines = [line for line in main.splitlines() if "graph" in line.lower()]
    assert graph_lines == [
        "from modules.flow_gate.graph_workflow.api.canvas_routes import router as _graph_canvas_router",
        "from modules.flow_gate.graph_workflow.api.graph_routes import router as _graph_definition_router",
        "from modules.flow_gate.graph_workflow.api.run_routes import router as _graph_run_router",
        'app.include_router(_graph_canvas_router, prefix=f"{CONTEXT}", tags=["GraphCanvas"])',
        'app.include_router(_graph_definition_router, prefix=f"{CONTEXT}", tags=["GraphDefinition"])',
        'app.include_router(_graph_run_router, prefix=f"{CONTEXT}", tags=["GraphRun"])',
    ]


# D0004 §4.3: v0.1 file -> number of contacts in it. Each contact is a delegation call to
# graph_workflow.api.v01_contact (decide / sequence save / advance / inbox / review).
# flowgate.v02.0048 D#2 §4.2: + the R↔B root-type conversion (guarded like a sequence save).
V01_CONTACT_FILES = {
    "modules/flow_gate/api/v1/workflow_decision_routes.py": 3,
    "modules/flow_gate/api/inbox_routes.py": 1,
    "modules/flow_gate/workflow/routers/workflow.py": 1,
    "modules/flow_gate/documents/routers/documents.py": 1,
}


def test_v01_contacts_are_thin_delegation_calls():
    for rel, contacts in V01_CONTACT_FILES.items():
        src = (SERVER / rel).read_text(encoding="utf-8")
        lines = [line for line in src.splitlines() if "graph" in line.lower()]
        imports = [line for line in lines if "import v01_contact as _graph_contact" in line]
        calls = [line for line in lines if "_graph_contact." in line]
        assert len(imports) == 1, rel
        assert len(calls) == contacts, (rel, calls)
        # nothing else of the Graph package reaches v0.1 code
        assert all("graph_workflow" not in line or line in imports for line in lines), rel
    # the v0.1 contact set is closed: no other v0.1 module imports the Graph package
    for path in (SERVER / "modules" / "flow_gate").rglob("*.py"):
        if PKG in path.parents:
            continue
        rel = path.relative_to(SERVER).as_posix()
        if "graph_workflow" in path.read_text(encoding="utf-8"):
            assert rel in V01_CONTACT_FILES or rel == "modules/flow_gate/api/v1/events/event_types.py", rel


def test_definition_store_never_rewrites_a_revision():
    """DB0010 I-05: no UPDATE / DELETE statement on the revision tables."""
    import re

    for path in (PKG / "store").rglob("*.py"):
        src = path.read_text(encoding="utf-8")
        assert not re.search(r"(?:UPDATE|DELETE FROM)\s+graph_revision", src), path.name


def test_graph_tables_of_each_migration_are_the_same_in_three_dialects():
    import re

    expected = {
        "142_graph_definitions.sql": [
            "graph_drafts", "graph_draft_nodes", "graph_draft_edges",
            "graph_revisions", "graph_revision_nodes", "graph_revision_edges",
        ],
        "143_graph_engine_assignment.sql": ["graph_assignments", "graph_root_bindings"],
        "144_graph_runs.sql": [
            "graph_runs", "graph_run_policy_entries", "graph_node_runs",
            "graph_run_token_map", "graph_run_events", "graph_propagations",
        ],
    }
    for name, tables in expected.items():
        found = {}
        for dialect in ("sqlite", "postgres", "mysql"):
            sql = (SERVER / "sql" / "migrations" / dialect / name).read_text(encoding="utf-8")
            found[dialect] = re.findall(r"CREATE TABLE IF NOT EXISTS (\w+)", sql)
            assert ("CREATE TRIGGER" in sql) == (dialect == "sqlite"), (name, dialect)
            assert not re.search(r"\b(ALTER|DROP|UPDATE)\b", sql), (name, dialect)
        assert found["sqlite"] == found["postgres"] == found["mysql"] == tables, name


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
