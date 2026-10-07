"""flowgate.default.0682 T#2 — Test Asset Policy: real repository layout and untracked assets.

D#1 §3.8 / §7 T#2 regression set over real git repositories and the production source
capture (see basis_support). One policy (``asset_kind``) decides for the
approval manifest, the asset API and the locator: ``tests/**``, ``test/**``,
``server/tests/**``, ``client/tests/**`` and ``__tests__`` under ``src``/``client/src``;
``.py`` runs on the existing pytest runner, JS/TS is pinned and editable but
``runner_unsupported`` (no JS/TS runner in this set), fixtures by extension. Git tracking
is not asked any more: an untracked test file a TR created is captured, run and edited
like a tracked one. Path safety and manifest-only edits stay.

0684 T#2: the run's copy (not a Bundle capture) applies the manifest rule, and the asset
API's authority is the approved specification (no stored Basis, no successor on edit).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess

import pytest
from fastapi import HTTPException

from basis_support import CASES, BasisEnv, git
from modules.flow_gate.services import spec_execution_service as execution
from modules.flow_gate.services import test_asset_service as assets
from modules.flow_gate.services import test_basis_service as basis
from modules.flow_gate.services import test_run_service as runner

SERVER_TEST = "server/tests/test_srv_0682.py"
SERVER_TEST_NEW = "server/tests/test_url_import_0647.py"
CLIENT_SPEC = "client/tests/main/DocumentEditDialog.urlImport.0647.spec.ts"
CLIENT_TSX = "client/tests/main/Panel.0682.spec.tsx"
CLIENT_JS = "client/tests/helpers/browser.0682.js"
CLIENT_FIXTURE = "client/tests/fixtures/doc.0682.json"
COLOCATED = "src/widgets/__tests__/widget.0682.test.ts"

LAYOUT = {
    SERVER_TEST: "def test_srv():\n    assert 1 + 1 == 2\n",
    SERVER_TEST_NEW: "def test_import():\n    assert 'url'.upper() == 'URL'\n",
    CLIENT_SPEC: "import { it } from 'vitest'\nit('imports', () => {})\n",
    CLIENT_TSX: "export const Panel = () => null\n",
    CLIENT_JS: "export function open() {}\n",
    CLIENT_FIXTURE: '{"title":"0682"}\n',
    COLOCATED: "export const t = 1\n",
}
TRACKED = {SERVER_TEST, CLIENT_TSX}  # the rest stays untracked (TR-created test files)


def _write(root, relative, text):
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8", newline="\n")
    return target


@pytest.fixture
def env(tmp_path, monkeypatch):
    environment = BasisEnv(tmp_path, monkeypatch)
    root = environment.repo("g1")
    for relative in sorted(TRACKED):
        _write(root, relative, LAYOUT[relative])
    git(root, "add", *sorted(TRACKED))
    git(root, "commit", "-m", "tracked layout tests")
    for relative in sorted(set(LAYOUT) - TRACKED):
        _write(root, relative, LAYOUT[relative])
    monkeypatch.setattr(basis, "source_root", lambda doc: environment.roots[doc["group_id"]])
    return environment


def _status(root, relative):
    out = subprocess.run(["git", "-C", str(root), "status", "--porcelain", "--", relative],
                         capture_output=True, text=True, check=True).stdout
    return out[:2]


def _layout_cases():
    return [
        {"case_id": "TC-001", "title": "layout case 1", "execution_mode": "automated",
         "automation_ref": SERVER_TEST + "::test_srv", "test_assets": ""},
        {"case_id": "TC-002", "title": "layout case 2", "execution_mode": "automated",
         "automation_ref": SERVER_TEST_NEW, "test_assets": "tests/fixture.json"},
        {"case_id": "TC-003", "title": "layout case 3", "execution_mode": "automated", "automation_ref": CLIENT_SPEC,
         "test_assets": ", ".join([CLIENT_TSX, CLIENT_JS, CLIENT_FIXTURE])},
        {"case_id": "TC-004", "title": "layout case 4", "execution_mode": "external", "automation_ref": "",
         "test_assets": COLOCATED},
    ]


def _asset_edit_env(env, monkeypatch):
    monkeypatch.setattr(assets, "_load", lambda ts_id: (env.doc(ts_id), {"cases": env.cases}))
    monkeypatch.setattr(assets.test_run_service, "_active_tsr_for_ts", lambda d: None)
    monkeypatch.setattr(assets.db_test_runs, "get_pending_failure_origin", lambda ts_id: None)
    monkeypatch.setattr(assets.db_test_runs, "latest_spec_run", lambda *a: None)
    monkeypatch.setattr(assets.db_test_runs, "get_running_by_doc", lambda ts_id: None)


def _measure(env, doc, run_id="run-1"):
    """The run's Basis (0684 T#2): copy + fingerprint in the preparing phase."""
    return env.run_basis(env.doc(doc["doc_id"]), run_id)[0]


def _copied(run_root):
    return {path.relative_to(run_root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in run_root.rglob("*") if path.is_file()}


def _hash(root, relative):
    return hashlib.sha256((root / relative).read_bytes()).hexdigest()


# ── the policy itself ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("path, kind", [
    ("tests/test_a.py", basis.KIND_RUNNABLE),
    ("test/unit/test_b.py", basis.KIND_RUNNABLE),
    (SERVER_TEST_NEW, basis.KIND_RUNNABLE),
    (CLIENT_SPEC, basis.KIND_UNSUPPORTED),
    (CLIENT_TSX, basis.KIND_UNSUPPORTED),
    (CLIENT_JS, basis.KIND_UNSUPPORTED),
    ("client/tests/a.jsx", basis.KIND_UNSUPPORTED),
    ("client/tests/a.mjs", basis.KIND_UNSUPPORTED),
    ("client/tests/a.cjs", basis.KIND_UNSUPPORTED),
    (COLOCATED, basis.KIND_UNSUPPORTED),
    ("client/src/main/__tests__/x.spec.ts", basis.KIND_UNSUPPORTED),
    ("src/__tests__/helper.py", basis.KIND_RUNNABLE),
    *[("client/tests/fixtures/f" + suffix, basis.KIND_FIXTURE) for suffix in (
        ".json", ".yaml", ".yml", ".toml", ".ini", ".txt", ".csv", ".xml", ".html",
        ".patch", ".snap")],
])
def test_policy_accepts_the_real_layout(path, kind):
    assert basis.asset_kind(path) == kind
    assert basis._test_only_path(path) is True


@pytest.mark.parametrize("path", [
    # product source, wrong root or co-located file outside __tests__
    "server/app.py", "client/src/main/App.vue", "src/widgets/widget.ts",
    "client/src/main/x.spec.ts", "server/modules/tests/test_x.py", "src/__tests__",
    "Tests/test_a.py", "Server/tests/test_a.py", "tests", "client/tests",
    # extensions without a kind (also case-exact)
    "tests/run.sh", "tests/tool.bin", "client/tests/a.vue", "tests/test_a.PY", "tests/data",
    # path safety
    "/tests/test_a.py", "tests/../server/app.py", "tests/./test_a.py", "tests//test_a.py",
    "tests\\test_a.py", "C:/tests/test_a.py", "C:tests/test_a.py", "tests/a:b.py",
    "tests/a\x00.py", "tests/a\nb.py", "", None,
    # Bundle exclusion: excluded directories on the way, secret names
    "tests/tmp/a.json", "client/tests/node_modules/x/index.js", "server/tests/__pycache__/a.py",
    "client/tests/dist/a.js", "tests/.env", "tests/secrets.json", "tests/private-key.txt",
    "client/tests/credentials/a.json",
])
def test_policy_refuses_product_unsafe_and_excluded_paths(path):
    assert basis.asset_kind(path) is None
    assert basis._test_only_path(path) is False


def test_policy_version_moved_with_the_policy():
    # T#1 put the policy version into the identity; the T#2 policy is a new version, so
    # a Basis approved under the T#1 policy is stale with its own reason (below).
    assert basis.ASSET_POLICY_VERSION == "test-asset-v2"


# ── locator: pytest stays the only runner ────────────────────────────────────

def test_locator_binds_py_under_every_root_and_marks_js_ts_runner_unsupported():
    def loc(ref, mode="automated"):
        return basis.locator({"execution_mode": mode, "automation_ref": ref})
    assert loc(SERVER_TEST + "::test_srv") == {
        "capability": "case_selectable", "path": SERVER_TEST, "node": "test_srv"}
    assert loc(SERVER_TEST_NEW)["capability"] == "suite_only"
    assert loc("tests/test_a.py::TestX::test_y")["node"] == "TestX::test_y"
    assert loc(CLIENT_SPEC) == {"capability": "runner_unsupported", "path": CLIENT_SPEC,
                                "node": None}
    assert loc(COLOCATED)["capability"] == "runner_unsupported"
    for ref in ("server/app.py", "tests/fixture.json", "tests/../server/app.py",
                "tests/test_a.py::not a node", "/tests/test_a.py", "tests\\test_a.py", ""):
        assert loc(ref) == {"capability": "unbound"}
    assert loc(CLIENT_SPEC, "external") == {"capability": "external"}
    assert loc(SERVER_TEST, "manual") == {"capability": "manual"}


def test_admit_never_selects_runner_unsupported_cases(env, monkeypatch):
    env.cases = _layout_cases()
    doc = env.ts()
    monkeypatch.setattr(runner, "load_spec_ts",
                        lambda doc_id: (env.doc(doc_id), {"cases": env.cases}))
    monkeypatch.setattr(runner, "_require_ts_admissible", lambda d, doc_id: None)
    monkeypatch.setattr(runner, "_active_tsr_for_ts", lambda d: None)
    with pytest.raises(HTTPException) as refused:
        execution.admit(doc["doc_id"], case_id="TC-003", runner_id="u", locale="en")
    assert refused.value.status_code == 422
    assert refused.value.detail["error"] == "case_not_selectable"
    assert refused.value.detail["capability"] == "runner_unsupported"
    # A suite run picks only the pytest bindings; with JS/TS only there is nothing to run.
    env.cases = [case for case in _layout_cases() if case["case_id"] == "TC-003"]
    with pytest.raises(HTTPException) as nothing:
        execution.admit(doc["doc_id"], case_id=None, runner_id="u", locale="en")
    assert nothing.value.status_code == 422
    assert nothing.value.detail["error"] == "no_automated_bindings"


def test_suite_selection_keeps_pytest_cases_only(env, monkeypatch):
    env.cases = _layout_cases()
    doc = env.ts()
    monkeypatch.setattr(runner, "load_spec_ts",
                        lambda doc_id: (env.doc(doc_id), {"cases": env.cases}))
    monkeypatch.setattr(runner, "_require_ts_admissible", lambda d, doc_id: None)
    monkeypatch.setattr(runner, "_active_tsr_for_ts", lambda d: None)
    inserted = {}
    monkeypatch.setattr(execution.db_test_runs, "get_running_by_doc", lambda doc_id: None)
    monkeypatch.setattr(execution.db_test_runs, "get_pending_failure_origin", lambda doc_id: None)
    monkeypatch.setattr(execution.db_test_runs, "insert_run",
                        lambda **kw: inserted.update(kw) or {"run_id": "r1", **kw})
    monkeypatch.setattr(runner, "_emit_started", lambda d, run: None)
    monkeypatch.setattr(runner, "_run_response", lambda run: {"run_id": run["run_id"]})
    admitted = execution.admit(doc["doc_id"], case_id=None, runner_id="u", locale="en")
    assert admitted["selected_case_ids"] == ["TC-001", "TC-002"]


# ── approval: real layout, tracked and untracked assets ──────────────────────

def test_untracked_and_tracked_layout_assets_enter_the_manifest(env):
    root = env.roots["g1"]
    assert _status(root, SERVER_TEST_NEW) == "??" and _status(root, CLIENT_SPEC) == "??"
    assert _status(root, SERVER_TEST) == "" and _status(root, CLIENT_TSX) == ""
    env.cases = _layout_cases()
    doc = env.ts()
    stored, run_root, _ = env.run_basis(doc)  # no git ls-files refusal
    captured = _copied(run_root)
    manifest = {entry["path"]: entry for entry in stored["manifest"]}
    assert set(manifest) == {SERVER_TEST, SERVER_TEST_NEW, CLIENT_SPEC, CLIENT_TSX, CLIENT_JS,
                             CLIENT_FIXTURE, COLOCATED, "tests/fixture.json"}
    for path, entry in manifest.items():
        assert entry["content_hash"] == captured[path] == _hash(root, path)
    assert {path: (entry["role"], entry["kind"]) for path, entry in manifest.items()} == {
        SERVER_TEST: ("test", basis.KIND_RUNNABLE),
        SERVER_TEST_NEW: ("test", basis.KIND_RUNNABLE),
        CLIENT_SPEC: ("test", basis.KIND_UNSUPPORTED),
        CLIENT_TSX: ("fixture", basis.KIND_UNSUPPORTED),
        CLIENT_JS: ("fixture", basis.KIND_UNSUPPORTED),
        CLIENT_FIXTURE: ("fixture", basis.KIND_FIXTURE),
        COLOCATED: ("fixture", basis.KIND_UNSUPPORTED),
        "tests/fixture.json": ("fixture", basis.KIND_FIXTURE),
    }
    assert stored["test_assets"]["policy_version"] == "test-asset-v2"
    assert basis.verdict(env.doc(doc["doc_id"]), stored)["state"] == basis.VALID
    # The live measurement applies the same manifest rule and agrees with the copy.
    assert basis.resolve(env.doc(doc["doc_id"]), env.cases)["basis_id"] == stored["basis_id"]


def test_untracked_asset_change_add_delete_make_the_basis_stale(env):
    root = env.roots["g1"]
    env.cases = _layout_cases()
    doc = env.ts()
    stored = _measure(env, doc)
    _write(root, CLIENT_SPEC, "changed\n")
    judged = basis.verdict(env.doc(doc["doc_id"]), stored)
    assert judged["reasons"] == [basis.REASON_SOURCE, basis.REASON_MANIFEST]
    _write(root, CLIENT_SPEC, LAYOUT[CLIENT_SPEC])
    assert basis.verdict(env.doc(doc["doc_id"]), stored)["state"] == basis.VALID
    (root / SERVER_TEST_NEW).unlink()
    assert basis.verdict(env.doc(doc["doc_id"]), stored)["reasons"] == [
        basis.REASON_SOURCE, basis.REASON_MANIFEST]
    _write(root, SERVER_TEST_NEW, LAYOUT[SERVER_TEST_NEW])
    _write(root, "client/tests/main/another.spec.ts", "x\n")  # not in the manifest
    assert basis.verdict(env.doc(doc["doc_id"]), stored)["reasons"] == [basis.REASON_SOURCE]


def test_committing_an_untracked_asset_keeps_the_basis_valid(env):
    root = env.roots["g1"]
    env.cases = _layout_cases()
    doc = env.ts()
    stored = _measure(env, doc)
    git(root, "add", SERVER_TEST_NEW, CLIENT_SPEC)
    git(root, "commit", "-m", "commit TR test files")
    assert basis.verdict(env.doc(doc["doc_id"]), stored)["state"] == basis.VALID


@pytest.mark.parametrize("bad, code", [
    ("server/app.py", "product_source_or_invalid_test_asset"),
    ("src/widgets/widget.ts", "product_source_or_invalid_test_asset"),
    ("tests/tool.bin", "product_source_or_invalid_test_asset"),
    ("tests/../server/app.py", "product_source_or_invalid_test_asset"),
    ("C:/tests/fixture.json", "product_source_or_invalid_test_asset"),
    ("/tests/fixture.json", "product_source_or_invalid_test_asset"),
    ("tests/tmp/cache.json", "test_asset_not_captured"),
    ("client/tests/node_modules/pkg/index.js", "test_asset_not_captured"),
    ("tests/secrets.json", "test_asset_not_captured"),
    ("tests/tool.exe", "product_source_or_invalid_test_asset"),
    # A backslash is refused as written, never rewritten to "/" first (D §3.8): the
    # files tests/fixture.json and client/tests/fixtures/doc.0682.json do exist.
    ("tests\\fixture.json", "product_source_or_invalid_test_asset"),
    ("client\\tests\\fixtures\\doc.0682.json", "product_source_or_invalid_test_asset"),
    ("client/tests\\fixtures/doc.0682.json", "product_source_or_invalid_test_asset"),
    ("client/tests/missing.spec.ts", "automation_asset_missing"),
])
def test_approval_refuses_assets_outside_the_policy(env, bad, code):
    root = env.roots["g1"]
    for relative in ("tests/tool.bin", "tests/tool.exe", "tests/tmp/cache.json", "tests/secrets.json",
                     "client/tests/node_modules/pkg/index.js", "src/widgets/widget.ts"):
        _write(root, relative, "x\n")  # present on disk; the policy still refuses them
    env.cases = [{**CASES[0], "test_assets": bad}]
    with pytest.raises(ValueError, match="^" + code):
        env.run_basis(env.ts())
    assert not (env.tmp_path / "runs" / "run-1").exists()


def test_backslash_asset_path_is_judged_as_written_everywhere(env):
    # One policy decision on the input text: _roles keeps the path verbatim, so the
    # approval refusal, the Live Probe manifest and the locator all see the backslash.
    raw = "tests\\fixture.json"
    cases = [{**CASES[0], "test_assets": raw + ", tests/fixture.json"}]
    assert basis._roles(cases) == {"tests/test_a.py": "test", raw: "fixture",
                                   "tests/fixture.json": "fixture"}
    assert basis.asset_kind(raw) is None
    env.cases = cases
    doc = env.ts()
    message = "^" + re.escape("product_source_or_invalid_test_asset: " + raw) + "$"
    with pytest.raises(ValueError, match=message):
        env.run_basis(doc)
    live = basis.resolve(env.doc(doc["doc_id"]), cases)
    entry = next(item for item in live["manifest"] if item["path"] == raw)
    assert entry == {"path": raw, "content_hash": None, "role": "fixture", "kind": None}
    assert basis.locator({"execution_mode": "automated",
                          "automation_ref": "tests\\test_a.py::test_a"}) == {"capability": "unbound"}


def test_approval_refuses_a_case_mismatched_asset_path(env):
    env.cases = [{**CASES[0], "test_assets": "client/tests/main/documentEditDialog.urlImport.0647.spec.ts"}]
    with pytest.raises(ValueError, match="^(test_asset_path_case_mismatch|automation_asset_missing)"):
        env.run_basis(env.ts())
    if os.path.normcase("A") == "a":  # case-insensitive filesystem: the spelling is named
        with pytest.raises(ValueError, match="^test_asset_path_case_mismatch"):
            env.run_basis(env.ts(), "run-2")


def test_symlinked_asset_directory_refuses_the_capture(env):
    root = env.roots["g1"]
    outside = env.tmp_path / "outside"
    outside.mkdir()
    (outside / "leak.spec.ts").write_text("x\n")
    try:
        os.symlink(outside, root / "client" / "tests" / "linked", target_is_directory=True)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlinks unavailable: {exc}")
    env.cases = [{**CASES[0], "test_assets": "client/tests/linked/leak.spec.ts"}]
    with pytest.raises(ValueError, match="^basis_capture_failed:unsafe_path"):
        env.run_basis(env.ts())


# ── execution: server/tests/** on the existing pytest runner ─────────────────

def test_server_tests_pytest_case_runs_on_the_existing_runner(env):
    env.cases = _layout_cases()
    doc = env.ts()
    _, run_root, scratch = env.run_basis(doc, "run-srv")
    # Untracked TR test files are in the execution source (the run's copy, not git archive).
    assert (run_root / SERVER_TEST_NEW).read_text() == LAYOUT[SERVER_TEST_NEW]
    assert (run_root / CLIENT_SPEC).is_file()
    for case_id, expected in (("TC-001", ["test_srv"]), ("TC-002", ["test_import"])):
        case = next(case for case in env.cases if case["case_id"] == case_id)
        loc = execution.AutomationRefResolver.resolve(case)
        nodeid = loc["path"] + ("::" + loc["node"] if loc.get("node") else "")
        result, xml, exit_code, output = execution.ExistingRunnerAdapter.run_pytest(
            nodeid, run_root, scratch, None)
        assert exit_code == 0, output
        assert result == "pass"
        assert re.findall(r'<testcase [^>]*?\bname="([^"]+)"', xml) == expected


# ── asset API: content and CAS edit under the same policy ────────────────────

@pytest.mark.parametrize("path, new", [
    (SERVER_TEST_NEW, "def test_import():\n    assert True\n"),   # untracked server .py
    (SERVER_TEST, "def test_srv():\n    assert 2 == 2\n"),       # tracked server .py
    (CLIENT_SPEC, "import { it } from 'vitest'\nit('edited', () => {})\n"),  # untracked .ts
    (CLIENT_TSX, "export const Panel = () => 1\n"),             # tracked .tsx
    (CLIENT_JS, "export function open() { return 1 }\n"),       # untracked .js
    (CLIENT_FIXTURE, '{"title":"edited"}\n'),                   # fixture
    (COLOCATED, "export const t = 2\n"),                        # src/**/__tests__/**
])
def test_layout_asset_read_and_cas_edit_stales_the_results(env, monkeypatch, path, new):
    root = env.roots["g1"]
    env.cases = _layout_cases()
    doc = env.ts()
    stored = _measure(env, doc)
    _asset_edit_env(env, monkeypatch)
    old_hash = _hash(root, path)
    read = assets.content(doc["doc_id"], path)
    assert read["content"] == LAYOUT[path] and read["content_hash"] == old_hash
    response = assets.update(doc["doc_id"], path, expected_hash=old_hash, content=new,
                             actor_id="u")
    assert (root / path).read_text(encoding="utf-8") == new
    assert response["content_hash"] == _hash(root, path) and response["results_stale"] is True
    assert [e[1] for e in env.store.events] == ["test_spec_asset_updated"]
    # Results made on the old bytes no longer count; the next run measures the new ones.
    assert basis.verdict(env.doc(doc["doc_id"]), stored)["state"] == basis.STALE
    rerun = _measure(env, doc, "run-2")
    assert {e["path"]: e["content_hash"] for e in rerun["manifest"]}[path] == response["content_hash"]
    assert basis.verdict(env.doc(doc["doc_id"]), rerun)["state"] == basis.VALID
    # The runner-unsupported asset stays unsupported after the edit.
    if path == CLIENT_SPEC:
        assert basis.locator(env.cases[2])["capability"] == "runner_unsupported"


def test_edit_outside_the_manifest_is_refused_even_inside_test_roots(env, monkeypatch):
    root = env.roots["g1"]
    env.cases = _layout_cases()
    doc = env.ts()
    _asset_edit_env(env, monkeypatch)
    _write(root, "client/tests/main/other.spec.ts", "other\n")
    for path in ("client/tests/main/other.spec.ts", "server/tests/test_other.py",
                 "server/app.py", "tests/test_a.py"):  # tests/test_a.py: not in this manifest
        before = (root / path).read_bytes() if (root / path).is_file() else None
        with pytest.raises(HTTPException) as refused:
            assets.update(doc["doc_id"], path, expected_hash="0" * 64, content="x\n",
                          actor_id="u")
        assert refused.value.status_code == 403
        assert refused.value.detail["error"] == "test_asset_not_allowlisted"
        with pytest.raises(HTTPException):
            assets.content(doc["doc_id"], path)
        assert ((root / path).read_bytes() if (root / path).is_file() else None) == before
    assert env.store.events == []


def test_product_path_named_by_the_specification_is_not_editable(env, monkeypatch):
    # Membership in the specification grants authority, but the policy is checked again:
    # a product path a Case names as an asset (or a stale stored Basis lists) is refused.
    env.cases = _layout_cases() + [{"case_id": "TC-005", "title": "product", "execution_mode":
                                    "manual", "automation_ref": "", "test_assets": "server/app.py"}]
    doc = env.ts()
    env.store.docs[doc["doc_id"]]["meta"] = json.dumps({"superseded_test_basis": {
        "manifest": [{"path": "server/app.py", "content_hash": "0" * 64, "role": "fixture"}]}})
    _asset_edit_env(env, monkeypatch)
    with pytest.raises(HTTPException) as refused:
        assets.update(doc["doc_id"], "server/app.py", expected_hash="0" * 64,
                      content="VALUE = 9\n", actor_id="u")
    assert refused.value.status_code == 403
    assert (env.roots["g1"] / "server" / "app.py").read_text() == "VALUE = 1\n"


def test_symlinked_parent_directory_blocks_asset_read_and_edit(env, monkeypatch):
    root = env.roots["g1"]
    env.cases = _layout_cases()
    doc = env.ts()
    _asset_edit_env(env, monkeypatch)
    # Swap client/tests/helpers for a link to a product directory after approval.
    product = root / "server"
    _write(root, "server/browser.0682.js", LAYOUT[CLIENT_JS])
    helpers = root / "client" / "tests" / "helpers"
    (helpers / "browser.0682.js").unlink()
    helpers.rmdir()
    try:
        os.symlink(product, helpers, target_is_directory=True)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlinks unavailable: {exc}")
    before = (product / "browser.0682.js").read_bytes()
    with pytest.raises(HTTPException) as refused:
        assets.update(doc["doc_id"], CLIENT_JS, expected_hash=_hash(root, "server/browser.0682.js"),
                      content="leak\n", actor_id="u")
    assert refused.value.status_code == 409
    assert refused.value.detail["error"] == "test_asset_unsafe_path"
    with pytest.raises(HTTPException) as read:
        assets.content(doc["doc_id"], CLIENT_JS)
    assert read.value.detail["error"] == "test_asset_unsafe_path"
    assert (product / "browser.0682.js").read_bytes() == before
    assert env.store.events == []


def test_safe_asset_file_names_each_refusal(env):
    root = env.roots["g1"]
    assert basis.safe_asset_file(root, CLIENT_SPEC) == root / CLIENT_SPEC
    refusals = {
        "server/app.py": "test_asset_not_allowlisted",
        "../outside/a.py": "test_asset_not_allowlisted",
        "client/tests/main/absent.spec.ts": "test_asset_missing",
        "client/tests/absent_dir/a.spec.ts": "test_asset_missing",
    }
    (root / "client" / "tests" / "dir.spec.ts").mkdir()
    refusals["client/tests/dir.spec.ts"] = "test_asset_unsafe_path"  # not a regular file
    if os.path.normcase("A") == "a":
        refusals["Client/tests/main/Panel.0682.spec.tsx".replace("Client", "client").replace(
            "Panel", "panel")] = "test_asset_path_case_mismatch"
    for path, code in refusals.items():
        with pytest.raises(ValueError, match="^" + code + "$"):
            basis.safe_asset_file(root, path)


# ── integration with T#1: policy version is part of the identity ─────────────

def test_basis_from_the_t1_policy_is_stale_with_the_asset_policy_reason(env):
    # A Basis T#1 stored: policy "test-asset-v1" and manifest entries without "kind".
    doc = env.ts()
    captured = _measure(env, doc)
    manifest = [{k: v for k, v in entry.items() if k != "kind"} for entry in captured["manifest"]]
    identity = {key: captured[key] for key in ("basis_version", "ts_document_id",
                                               "ts_revision_no", "source", "execution_profile")}
    identity["test_assets"] = {"policy_version": "test-asset-v1",
                               "manifest_hash": basis.canonical_hash(manifest),
                               "asset_count": len(manifest)}
    old = {"basis_id": basis.canonical_hash(identity), **identity,
           "binding": captured["binding"], "manifest": manifest}
    judged = basis.verdict(env.doc(doc["doc_id"]), old)
    assert judged["state"] == basis.STALE
    assert judged["reasons"] == [basis.REASON_MANIFEST, basis.REASON_ASSET_POLICY]
    # Recovery is [run again]: the next run measures under the current policy.
    fresh = _measure(env, doc, "run-2")
    assert basis.verdict(env.doc(doc["doc_id"]), fresh)["state"] == basis.VALID


# ── source capture with executable extensions (Windows lstat mode) ───────────
# Windows reports execute bits for .bat/.cmd/.exe from Path.lstat()/os.fstat() but not
# from DirEntry.stat(); the capture compared st_mode and failed every worktree that
# holds such a file (FlowGate tracks client/run.bat and server/run-dev.bat).

EXECUTABLES = {"client/run.bat": b"@echo off\r\n", "server/run-dev.bat": b"@echo off\r\n",
               "tools/build.cmd": b"@echo off\r\n", "tools/helper.exe": b"MZ\x00\x01"}


def test_scan_and_lstat_of_executable_extensions_compare_as_the_same_file(tmp_path):
    from modules.flow_gate.services import source_fingerprint as fingerprint
    for name in ("run.bat", "run.cmd", "tool.exe", "notes.txt"):
        (tmp_path / name).write_bytes(b"x")
    for entry in os.scandir(tmp_path):
        scanned = entry.stat(follow_symlinks=False)
        assert fingerprint._same(scanned, (tmp_path / entry.name).lstat()), entry.name
        fd = os.open(tmp_path / entry.name, os.O_RDONLY | getattr(os, "O_BINARY", 0))
        try:
            assert fingerprint._same(scanned, os.fstat(fd)), entry.name
        finally:
            os.close(fd)
    # A real change is still caught.
    before = (tmp_path / "run.bat").lstat()
    (tmp_path / "run.bat").write_bytes(b"xy")
    assert not fingerprint._same(before, (tmp_path / "run.bat").lstat())


@pytest.fixture
def exe_env(env):
    root = env.roots["g1"]
    for relative, data in EXECUTABLES.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    git(root, "add", "client/run.bat", "server/run-dev.bat")
    git(root, "commit", "-m", "tracked launchers")
    return env  # tools/build.cmd and tools/helper.exe stay untracked


def test_worktree_with_executable_extensions_is_captured(exe_env):
    import time
    from modules.flow_gate.services import source_fingerprint as fingerprint
    root = exe_env.roots["g1"]
    inspected = fingerprint.inspect_source(root.resolve(), time.monotonic() + 60)
    hashed = {entry["path"]: entry["sha256"] for entry in inspected["entries"]}
    for relative in EXECUTABLES:
        assert hashed[relative] == _hash(root, relative)


def test_layout_approval_and_run_with_executable_extensions_in_the_worktree(exe_env):
    env = exe_env
    root = env.roots["g1"]
    env.cases = _layout_cases()
    doc = env.ts()
    stored, run_root, scratch = env.run_basis(doc, "run-exe")  # used to fail on Windows
    captured = _copied(run_root)
    for relative in EXECUTABLES:
        assert captured[relative] == _hash(root, relative)
    manifest = {entry["path"]: entry for entry in stored["manifest"]}
    assert manifest[SERVER_TEST_NEW]["kind"] == basis.KIND_RUNNABLE
    assert manifest[CLIENT_SPEC]["kind"] == basis.KIND_UNSUPPORTED
    assert basis.verdict(env.doc(doc["doc_id"]), stored)["state"] == basis.VALID
    assert basis.resolve(env.doc(doc["doc_id"]), env.cases)["basis_id"] == stored["basis_id"]
    assert (run_root / "client" / "run.bat").read_bytes() == EXECUTABLES["client/run.bat"]
    case = next(case for case in env.cases if case["case_id"] == "TC-002")
    loc = execution.AutomationRefResolver.resolve(case)
    result, xml, exit_code, output = execution.ExistingRunnerAdapter.run_pytest(
        loc["path"], run_root, scratch, None)
    assert exit_code == 0, output
    assert result == "pass"
    # Editing a launcher is a source change, not an unmeasurable source.
    (root / "client" / "run.bat").write_bytes(b"@echo on\r\n")
    assert basis.verdict(env.doc(doc["doc_id"]), stored)["reasons"] == [basis.REASON_SOURCE]


def test_this_repository_worktree_executables_are_captured():
    # The real FlowGate worktree these tests run in tracks client/run.bat and
    # server/run-dev.bat. Every executable-extension file goes through the capture's own
    # scan and safe hash (DirEntry.stat vs lstat/fstat). The whole-tree capture is not
    # repeated here: other suites in the same run write server/logs while it is measured.
    import time
    from pathlib import Path
    from modules.flow_gate.services import source_fingerprint as fingerprint
    root = Path(__file__).resolve().parents[2]
    if not (root / ".git").exists() or not (root / "client" / "run.bat").is_file():
        pytest.skip("not running inside the FlowGate repository worktree")
    deadline = time.monotonic() + fingerprint.MEASURE_SECONDS
    files, _dirs = fingerprint._scan(root, deadline)
    launchers = [(name, st) for name, st in files
                 if name.rsplit(".", 1)[-1].lower() in ("bat", "cmd", "exe", "com")]
    assert "client/run.bat" in {name for name, _st in launchers}
    for name, st in launchers:
        item = fingerprint._hash_file(root, name, st, deadline)
        assert item["sha256"] == _hash(root, name)
