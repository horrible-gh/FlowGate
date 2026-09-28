"""flowgate.default.0615 T0004 §7/rev2 — a REAL, live HTTP server for the client-side
connected regression (client/tests/integration/FileExplorerLocalBranch.connected.0615.spec.ts).

This is deliberately NOT a pytest module (pytest is never imported here) and is never
collected by `pytest`/CI's default run: it is a standalone script, spawned as a
subprocess by the vitest integration spec so a REAL (non-mocked) FileExplorer.vue and
GitBranchManager.vue can issue REAL HTTP requests against it -- the exact boundary the
0615 T0004 rev1 rejection said two independently-mocked test suites could never catch
drifting apart.

Isolation strategy mirrors server/tests/test_git_local_branch_tree_0615.py's own
`full_repo` fixture line for line (same monkeypatches), just applied as plain
attribute assignment (no pytest `monkeypatch` fixture available outside a test
function) and exposed over a real socket instead of FastAPI's in-process TestClient
transport. `get_current_user` is overridden to an admin identity exactly as
`_client()` does there, so every RBAC-gated route in git_routes.router passes without
a real users/permissions database (rbac/decorators.py: "Users with is_admin=1 bypass
all permission checks"). `project_git_status` is monkeypatched directly at the
git_routes.git_service boundary (the same boundary test_local_branch_routes_rbac_
and_error_envelope patches read_local_branch_tree/read_local_branch_blob at) so
File Explorer's unconditional git/status fetch on mount gets a real 200 with no
slots, without needing a real projects/group_git_state database either.

Usage: python _local_branch_live_server_0615.py --port <port> --repo-root <dir>
Creates a fresh git repo (main branch, one README commit) under --repo-root and
serves git_routes.router on http://127.0.0.1:<port>/flowgate/api/v1/...
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

os.environ.setdefault("TESTING", "1")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")
os.environ.setdefault("ALLOWED_ORIGIN", "*")
os.environ.setdefault("CONTEXT", "/flowgate")
os.environ.setdefault("DB_TYPE", "sqlite3")

_SERVER_DIR = Path(__file__).resolve().parents[1]
if str(_SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(_SERVER_DIR))

from fastapi import FastAPI  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402

from modules.flow_gate.api.v1 import git_routes  # noqa: E402
from modules.flow_gate.auth.middleware import get_current_user  # noqa: E402
from modules.flow_gate.services import git_service  # noqa: E402
from modules.flow_gate.services.git import branches as branch_service  # noqa: E402


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def build_repo(repo_root: Path) -> Path:
    root = repo_root / "repo"
    root.mkdir(parents=True, exist_ok=True)
    _git(root, "init", "-b", "main")
    _git(root, "config", "user.name", "Test")
    _git(root, "config", "user.email", "test@example.com")
    (root / "README.md").write_text("base\n", encoding="utf-8")
    _git(root, "add", "README.md")
    _git(root, "commit", "-m", "base")
    return root


def build_app(repo_root: Path) -> FastAPI:
    root = build_repo(repo_root)
    storage = repo_root / "storage"
    storage.mkdir(parents=True, exist_ok=True)

    # Same isolation as test_git_local_branch_tree_0615.py's `full_repo` fixture --
    # a real git repo on disk, no real projects/group_git_state database required.
    git_service.get_storage_root = lambda: storage
    branch_service._branch_context = lambda project_id: ({"enabled": 1}, root, "main")
    git_service._base_root_of = lambda project_id: root
    git_service._load_secret_for = lambda cfg: ""
    git_service._acquire_lock = lambda project_id, holder: True
    git_service.db_git.release_lock = lambda project_id, holder: None
    git_service.db_git.list_states_of_project = lambda project_id: []
    git_service.db_git.list_open_sessions = lambda: []
    from modules.flow_gate.db import groups as db_groups
    db_groups.list_open_groups_by_work_base = lambda project_id, ref: []
    # File Explorer's mount also fetches GET .../git/status (group slot metadata,
    # unrelated to this T's ordinary-local-branch scope) -- give it a real 200 with
    # no slots rather than chasing project_git_status's own internal DB reads
    # (db_git.get_config, terminal_cleanup, ...) one by one.
    git_routes.git_service.project_git_status = lambda project_id: {
        "ok": True, "status": {
            "enabled": False, "base_branch": None, "base_path_state": "empty",
            "ahead_count": None, "behind_count": None,
            "slots": [], "pending": [], "pending_count": 0, "cleanable_count": 0,
            "terminal_cleanup": None,
        },
    }

    app = FastAPI()
    app.add_middleware(
        CORSMiddleware,
        # jsdom (the vitest integration spec's environment) enforces real CORS and
        # rejects a bare "*" once any request header is present, so the exact test
        # origin (vitest/jsdom's default) is listed explicitly rather than relying
        # on the wildcard.
        allow_origins=["http://localhost:3000", "*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(git_routes.router, prefix="/flowgate")
    app.dependency_overrides[get_current_user] = lambda: {"user_id": "admin", "is_admin": True}

    @app.get("/flowgate/_live_server_ready")
    def _ready() -> dict:
        return {"ok": True}

    # File Explorer's base-checkout view (unrelated to this T's ordinary local
    # branch scope) hits the separate files router, which this minimal app does
    # not mount. An empty base tree is enough to let the base view render without
    # a 404 blocking error -- the spec never selects base and asserts on it.
    @app.get("/flowgate/api/v1/projects/{project_id}/files/tree")
    def _base_tree_stub(project_id: str) -> dict:
        return {"ok": True, "data": {"nodes": [], "complete": True}}

    return app


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--repo-root", type=str, required=True)
    args = parser.parse_args()

    import uvicorn
    app = build_app(Path(args.repo_root))
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
