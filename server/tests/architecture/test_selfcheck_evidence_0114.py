"""flowgate.v02.0114 T#3: short real pytest runs of the Self-check result block plugin.

D 0006 9.2-5 (a)(b) and 9.2-6 (k)(l)(m)(o)(p)(q): a small synthetic test tree is run with
the real plugin (tests/selfcheck_evidence.py, registered by its conftest the way
tests/conftest.py does), the block is read back by the real server parser, and one input
is changed between consecutive runs. What must move: the edited test's own evidence (body),
every item of the module (module global, helper, data file it names), the users of an
edited fixture (conftest, test-tree module or outside the tree), the run level (conftest
hook, pytest.ini). What must not: another test's evidence when only one body changes, any
evidence when only product code changes or a new test file is added. A dynamically loading
module is untraceable. Rejection rej_01M67VSC9EK69SR8 added the inputs that used to slip
through, each checked on a real pair through the judge: a data file read by a conftest
fixture or a test-tree fixture module, a test-tree helper taken by a fixture defined outside
the tree, and a test-tree plugin loaded by ``pytest_plugins`` or ``-p``. Inner output never reaches this run's own stdout unmarked: the
markers are masked before any assertion message shows it.
"""
from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

import selfcheck_evidence as plugin
from modules.flow_gate.services import tr_self_check_baseline as baseline
from modules.flow_gate.services import tr_self_check_result as result_block

_TESTS_DIR = Path(__file__).resolve().parents[1]


def _mask(text: str) -> str:
    return text.replace(result_block.BEGIN_MARKER, "<begin>").replace(result_block.END_MARKER, "<end>")


def _write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text).lstrip("\n"), encoding="utf-8")


def _run(project: Path, *args: str, path: tuple = ()) -> tuple[subprocess.CompletedProcess, dict, dict]:
    env = os.environ.copy()
    env.pop("FLOWGATE_REQUIRE_FULL_SUITE", None)
    env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    env["PYTHONPATH"] = os.pathsep.join([*map(str, path), str(_TESTS_DIR), env.get("PYTHONPATH", "")]).rstrip(os.pathsep)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    proc = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *args, "tests"],
                          cwd=str(project), capture_output=True, text=True, encoding="utf-8",
                          errors="replace", env=env, timeout=300)
    summary, environment = result_block.summarize(proc.stdout, proc.returncode)
    assert summary["status"] == result_block.COMPLETE, (summary, _mask(proc.stdout[-3000:]), _mask(proc.stderr[-2000:]))
    return proc, summary, environment


def _items(summary: dict) -> dict:
    return summary["definitions"]["items"]


def _changed(before: dict, after: dict) -> set:
    a, b = _items(before), _items(after)
    return {n for n in set(a) | set(b) if a.get(n) != b.get(n)}


CARDS = "tests/test_cards.py::"
USES = "tests/test_uses.py::"


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "proj"
    _write(root, "pytest.ini", "[pytest]\n")
    _write(root, "tests/conftest.py", """
        import sys
        from pathlib import Path

        import pytest
        import selfcheck_evidence

        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "product"))


        def pytest_configure(config):
            selfcheck_evidence.register(config, capabilities=lambda: {"git": True, "symlinks": True})


        @pytest.fixture
        def shared_numbers():
            return [1, 2, 3]
    """)
    _write(root, "tests/data/sample.json", '{"v": 1}\n')
    _write(root, "tests/support_mod.py", """
        import pytest


        @pytest.fixture
        def support_value():
            return "s1"
    """)
    _write(root, "product/app.py", "VALUE = 1\n")
    _write(root, "product/ext_fixtures.py", """
        import pytest


        @pytest.fixture
        def ext_value():
            return 10
    """)
    _write(root, "tests/test_cards.py", """
        import json
        from pathlib import Path

        RUN_FIELDS = ("a", "b")


        def get_settings(values):
            assert values is not None
            return list(values)


        def test_fields():
            assert RUN_FIELDS == ("a", "b", "c")


        def test_helper(shared_numbers):
            assert get_settings(shared_numbers) == [9]


        def test_data():
            data = json.loads((Path(__file__).parent / "data" / "sample.json").read_text(encoding="utf-8"))
            assert data["v"] == 2


        def test_body():
            assert 1 == 2


        def test_other_body():
            assert 3 == 4
    """)
    _write(root, "tests/test_uses.py", """
        from ext_fixtures import ext_value  # noqa: F401  (a fixture defined outside the test tree)
        from support_mod import support_value  # noqa: F401  (a fixture of a test-tree module)


        def test_with_conftest_fixture(shared_numbers):
            assert shared_numbers == []


        def test_with_support_fixture(support_value):
            assert support_value == "x"


        def test_with_external_fixture(ext_value):
            assert ext_value == 0


        def test_without_fixture():
            assert 0
    """)
    _write(root, "tests/test_product.py", """
        import app


        def test_product_value():
            assert app.VALUE == 2
    """)
    return root


def _edit(root: Path, rel: str, old: str, new: str) -> None:
    path = root / rel
    text = path.read_text(encoding="utf-8")
    assert old in text, (rel, old)
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def test_5a_6k_the_block_ends_the_output_with_every_environment_category_and_definition(project):
    proc, summary, env = _run(project)
    assert proc.stdout.rstrip().endswith(result_block.END_MARKER)
    assert set(env) == set(result_block.ENV_CATEGORIES)
    assert all(env[c] != result_block.UNREADABLE for c in result_block.ENV_CATEGORIES)
    assert env["host"]["capabilities"] == {"git": True, "symlinks": True}
    assert env["workers"] == {"mode": "serial", "applied": 0, "dist": "no", "cpu_limit": None}
    assert set(env["env"]) == set(plugin.host_keys())
    assert summary["collected"] == summary["completed"] == 10 and summary["failures_total"] == 10
    assert summary["definitions"]["run_status"] == "ok" and len(_items(summary)) == 10
    assert result_block.UNTRACEABLE not in _items(summary).values()
    # same tree, same command: identical evidence, so a real pair is reusable on its own terms
    _proc, again, env_again = _run(project)
    assert (_items(again), again["definitions"]["run"], env_again) == (_items(summary), summary["definitions"]["run"], env)


def test_6l_6p_6q_6o_each_definition_input_moves_exactly_the_items_that_use_it(project):
    _p, base, _e = _run(project)

    _edit(project, "tests/test_cards.py", "assert 1 == 2", "assert 1 == 5")            # (l) one body
    _p, body, _e = _run(project)
    assert _changed(base, body) == {CARDS + "test_body"}                                # (q) other bodies are no input

    _edit(project, "tests/test_cards.py", 'RUN_FIELDS = ("a", "b")', 'RUN_FIELDS = ("a",)')   # (p1) module global
    _p, glob_, _e = _run(project)
    cards = {n for n in _items(base) if n.startswith(CARDS)}
    assert _changed(body, glob_) == cards

    _edit(project, "tests/test_cards.py", "return list(values)", "return sorted(values)")      # (p2) helper
    _p, helper, _e = _run(project)
    assert _changed(glob_, helper) == cards

    (project / "tests/data/sample.json").write_text('{"v": 3}\n', encoding="utf-8")            # (p3) data file
    _p, data, _e = _run(project)
    assert _changed(helper, data) == cards

    (project / "product/app.py").write_text("VALUE = 5\n", encoding="utf-8")                  # (o) product only
    _p, product, _e = _run(project)
    assert _changed(data, product) == set()

    _write(project, "tests/test_added.py", "def test_brand_new():\n    assert False\n")     # (o) new file
    _p, added, _e = _run(project)
    assert _changed(product, added) == {"tests/test_added.py::test_brand_new"}
    assert data["definitions"]["run"] == added["definitions"]["run"]


def test_6m_fixture_edits_move_their_users_and_conftest_hooks_or_settings_move_the_run(project):
    _p, base, _e = _run(project)

    _edit(project, "tests/conftest.py", "return [1, 2, 3]", "return [1, 2]")              # conftest fixture
    _p, conf_fixture, _e = _run(project)
    assert _changed(base, conf_fixture) == {CARDS + "test_helper", USES + "test_with_conftest_fixture"}
    assert base["definitions"]["run"] == conf_fixture["definitions"]["run"]

    _edit(project, "tests/support_mod.py", 'return "s1"', 'return "s2"')                   # test-tree module fixture
    _p, support, _e = _run(project)
    assert _changed(conf_fixture, support) == {n for n in _items(base) if n.startswith(USES)}

    _edit(project, "product/ext_fixtures.py", "return 10", "return 11")                     # fixture outside the tree
    _p, external, _e = _run(project)
    assert _changed(support, external) == {USES + "test_with_external_fixture"}

    _edit(project, "tests/conftest.py", "def pytest_configure(config):",
          "def pytest_report_header(config):\n    return 'x'\n\n\ndef pytest_configure(config):")   # a hook
    _p, hook, _e = _run(project)
    assert hook["definitions"]["run"] != external["definitions"]["run"]

    (project / "pytest.ini").write_text("[pytest]\nmarkers =\n    slow: slow\n", encoding="utf-8")  # settings
    _p, ini, _e = _run(project)
    assert ini["definitions"]["run"] != hook["definitions"]["run"]


def test_6q_a_dynamically_loading_module_is_untraceable(project):
    _write(project, "tests/test_dynamic.py", """
        import importlib

        NAME = "support" + "_mod"


        def test_dyn():
            assert importlib.import_module(NAME) is None
    """)
    _p, summary, _e = _run(project)
    assert _items(summary)["tests/test_dynamic.py::test_dyn"] == result_block.UNTRACEABLE
    assert _items(summary)[CARDS + "test_body"] != result_block.UNTRACEABLE


def _row(run_id, proc, summary, env, clean):
    """A stored run row around one real run (the server's parser made summary and env)."""
    state = baseline.EMPTY_SHA256 if clean else "f" * 64
    return {"self_check_run_id": run_id, "project_id": "p", "group_id": "g", "status": "completed",
            "timed_out": False, "recovery_state": "none", "exit_code": proc.returncode, "program": "pytest",
            "args": ["-q", "tests"], "cwd_relative": ".", "policy_version": "v1", "host_node_key": "node",
            "source_head_before": "a" * 40, "source_head_after": "a" * 40,
            "source_status_hash_before": state, "source_status_hash_after": state,
            "source_changed_during_run": False, "worktree_state_changed": False,
            "result_summary": result_block.dumps(summary), "environment_evidence": result_block.dumps(env)}


def _judge_real(first: tuple, second: tuple) -> dict:
    """The judge on two real runs: ``first`` the clean baseline, ``second`` the after-run."""
    return baseline.judge(_row("base", *first, True), _row("after", *second, False),
                          anchor={"state": "recorded", "base_commit": "a" * 40}, is_ancestor=lambda c, h: True)


def test_6l_6p_real_pairs_classify_changed_definitions_apart_from_existing_failures(project):
    """The judge on rows built from two real runs: the edited body and the edited module global
    are definition-changed failures; a module untouched by the edit keeps its existing failures."""
    proc0, base, env0 = _run(project)
    _edit(project, "tests/test_cards.py", 'RUN_FIELDS = ("a", "b")', 'RUN_FIELDS = ("a", "b", "d")')
    proc1, after, env1 = _run(project)

    result = _judge_real((proc0, base, env0), (proc1, after, env1))
    assert result["reusable"] is True, result["reasons"]
    comparison = result["comparison"]
    cards = sorted(n for n in _items(base) if n.startswith(CARDS))
    assert [c["nodeid"] for c in comparison["definition_changed_failures"]] == cards
    assert not set(comparison["existing_failures"]) & set(cards)
    assert USES + "test_without_fixture" in comparison["existing_failures"]


_MINI_CONFTEST = """
    import sys
    from pathlib import Path

    import pytest
    import selfcheck_evidence

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "outside"))
    {extra}

    def pytest_configure(config):
        selfcheck_evidence.register(config, capabilities=lambda: {{"git": True}})
    {fixtures}
"""

_KNOWN = "tests/test_known.py::test_known_failure"


def _mini(tmp_path: Path, files: dict, *, extra: str = "", fixtures: str = "") -> Path:
    """A small tree: a conftest that registers the plugin and puts outside/ on sys.path, a module
    with a failure no edit touches, and ``files``."""
    root = tmp_path / "mini"
    _write(root, "pytest.ini", "[pytest]\n")
    _write(root, "tests/conftest.py", _MINI_CONFTEST.format(extra=extra, fixtures=fixtures))
    _write(root, "tests/test_known.py", "def test_known_failure():\n    assert 0\n")
    for rel, text in files.items():
        _write(root, rel, text)
    return root


def _pair(root: Path, rel: str, old: str, new: str, *args: str, path: tuple = ()) -> tuple[dict, dict, dict]:
    """Run, edit one input, run again: (before summary, after summary, judgement)."""
    proc0, base, env0 = _run(root, *args, path=path)
    _edit(root, rel, old, new)
    proc1, after, env1 = _run(root, *args, path=path)
    return base, after, _judge_real((proc0, base, env0), (proc1, after, env1))


def _never_existing(result: dict, nodeid: str) -> None:
    """The edited input's failure is not an existing failure: either the whole baseline is out,
    or the item is a definition-changed failure."""
    comparison = result["comparison"]
    if comparison is None:
        assert result["reusable"] is False and result["reasons"]
        return
    assert nodeid not in comparison["existing_failures"]
    assert nodeid in {c["nodeid"] for c in comparison["definition_changed_failures"]}
    assert _KNOWN in comparison["existing_failures"]


def test_6m_6p_a_data_file_read_by_a_conftest_fixture_is_a_run_level_input(tmp_path):
    """rej_01M67VSC9EK69SR8 P1: a conftest fixture reads tests/expected.txt; only that file changes."""
    root = _mini(tmp_path, {
        "tests/expected.txt": "1\n",
        "tests/test_case.py": "def test_value(expected):\n    assert expected == '3'\n",
    }, fixtures="""
    @pytest.fixture
    def expected():
        return (Path(__file__).parent / "expected.txt").read_text(encoding="utf-8").strip()
    """)
    base, after, result = _pair(root, "tests/expected.txt", "1", "2")
    assert base["definitions"]["run"] != after["definitions"]["run"]
    assert result["reusable"] is False and "definition_run_mismatch" in {r["code"] for r in result["reasons"]}
    _never_existing(result, "tests/test_case.py::test_value")


@pytest.mark.parametrize("reader", ["fixture_module", "test_module"])
def test_6p_a_data_file_read_through_a_tree_fixture_module_or_directly_moves_its_users(tmp_path, reader):
    root = _mini(tmp_path, {
        "tests/data/expected.txt": "1\n",
        "tests/data_fixtures.py": """
            from pathlib import Path

            import pytest


            @pytest.fixture
            def expected():
                return (Path(__file__).parent / "data" / "expected.txt").read_text(encoding="utf-8").strip()
        """,
        "tests/test_case.py": """
            from pathlib import Path

            from data_fixtures import expected  # noqa: F401


            def test_value(expected):
                assert expected == "3"


            def test_direct():
                assert (Path(__file__).parent / "data" / "expected.txt").read_text(encoding="utf-8").strip() == "3"
        """ if reader == "test_module" else """
            from data_fixtures import expected  # noqa: F401


            def test_value(expected):
                assert expected == "3"
        """,
    })
    base, after, result = _pair(root, "tests/data/expected.txt", "1", "2")
    moved = {"tests/test_case.py::test_value"} | ({"tests/test_case.py::test_direct"} if reader == "test_module" else set())
    assert _changed(base, after) == moved
    assert base["definitions"]["run"] == after["definitions"]["run"]
    assert result["reusable"] is True, result["reasons"]
    for nodeid in moved:
        _never_existing(result, nodeid)


@pytest.mark.parametrize("hops", ["direct", "through_outside_module"])
def test_6m_a_fixture_outside_the_tree_taking_a_test_tree_helper_moves_its_users(tmp_path, hops):
    """rej_01M67VSC9EK69SR8 P1: outside/ext_fixtures.py takes expected_value from tests/support.py;
    only the helper's return value changes."""
    files = {
        "tests/support.py": "def expected_value():\n    return 1\n",
        "tests/test_case.py": """
            from ext_fixtures import expected  # noqa: F401  (defined outside the test tree)


            def test_value(expected):
                assert expected == 3
        """,
    }
    if hops == "direct":
        files["outside/ext_fixtures.py"] = """
            import pytest
            from support import expected_value


            @pytest.fixture
            def expected():
                return expected_value()
        """
    else:
        files["outside/bridge.py"] = "from support import expected_value  # noqa: F401\n"
        files["outside/ext_fixtures.py"] = """
            import pytest
            from bridge import expected_value


            @pytest.fixture
            def expected():
                return expected_value()
        """
    root = _mini(tmp_path, files)
    base, after, result = _pair(root, "tests/support.py", "return 1", "return 2")
    if hops == "direct":   # traced through the outside module: the user moves, the run does not
        assert _changed(base, after) == {"tests/test_case.py::test_value"}
        assert base["definitions"]["run"] == after["definitions"]["run"]
    else:                  # not traced, but loaded from the tree: the whole run moves
        assert base["definitions"]["run"] != after["definitions"]["run"]
    _never_existing(result, "tests/test_case.py::test_value")
    # and the fixture's own body outside the tree still moves its user
    base2, after2, result2 = _pair(root, "outside/ext_fixtures.py", "return expected_value()",
                                   "return expected_value() + 0")
    assert "tests/test_case.py::test_value" in _changed(base2, after2)
    _never_existing(result2, "tests/test_case.py::test_value")


_PLUGIN = """
    def pytest_runtest_setup(item):
        item.module.EXPECTED = 1
"""


@pytest.mark.parametrize("how", ["pytest_plugins", "command_line"])
def test_6m_a_test_tree_plugin_loaded_by_name_is_a_run_level_input(tmp_path, how):
    """rej_01M67VSC9EK69SR8 P1: a test-tree plugin's hook sets the value a test checks; only the
    plugin changes."""
    plugin = "test_plugin" if how == "pytest_plugins" else "cli_plugin"
    root = _mini(tmp_path, {
        f"tests/{plugin}.py": _PLUGIN,
        "tests/test_case.py": "EXPECTED = 0\n\n\ndef test_value():\n    assert EXPECTED == 3\n",
    }, extra=f"pytest_plugins = [{plugin!r}]" if how == "pytest_plugins" else "")
    args = ("-p", plugin) if how == "command_line" else ()
    base, after, result = _pair(root, f"tests/{plugin}.py", "EXPECTED = 1", "EXPECTED = 2", *args,
                                path=(root / "tests",))
    assert base["definitions"]["run_status"] == after["definitions"]["run_status"] == "ok"
    assert base["definitions"]["run"] != after["definitions"]["run"]
    assert result["reusable"] is False and "definition_run_mismatch" in {r["code"] for r in result["reasons"]}
    _never_existing(result, "tests/test_case.py::test_value")


def test_5b_116_long_failures_still_leave_the_block_inside_the_stored_tail(tmp_path):
    root = tmp_path / "many"
    _write(root, "pytest.ini", "[pytest]\n")
    _write(root, "tests/conftest.py", """
        import selfcheck_evidence


        def pytest_configure(config):
            selfcheck_evidence.register(config, capabilities=lambda: {"git": True})
    """)
    _write(root, "tests/test_many.py", """
        import pytest

        LONG = "x" * 400


        @pytest.mark.parametrize("n", range(116))
        def test_many(n):
            values = [LONG + str(i) for i in range(8)]
            assert values == [], LONG
    """)
    env = os.environ.copy()
    env.pop("FLOWGATE_REQUIRE_FULL_SUITE", None)
    env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    env["PYTHONPATH"] = os.pathsep.join([str(_TESTS_DIR), env.get("PYTHONPATH", "")]).rstrip(os.pathsep)
    proc = subprocess.run([sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-rA", "tests"],
                          cwd=str(root), capture_output=True, text=True, encoding="utf-8", errors="replace",
                          env=env, timeout=300)
    stored = proc.stdout.encode("utf-8")[-64 * 1024:].decode("utf-8", errors="ignore")   # Self-check's kept tail
    assert len(proc.stdout.encode("utf-8")) > 2 * 64 * 1024
    summary, _env = result_block.summarize(stored, proc.returncode)
    assert summary["status"] == result_block.COMPLETE, (summary, _mask(stored[-1500:]))
    assert summary["failures_total"] == 116 and summary["collected"] == summary["completed"] == 116


def test_6i_parallel_runs_report_their_applied_workers_with_the_same_definitions(project):
    pytest.importorskip("xdist")
    _p, serial, serial_env = _run(project)
    _p, parallel, parallel_env = _run(project, "-p", "xdist.plugin", "-n", "2", "--dist", "loadfile")
    assert parallel_env["workers"] == {"mode": "xdist", "applied": 2, "dist": "loadfile", "cpu_limit": None}
    assert serial_env["workers"]["mode"] == "serial"
    assert _items(parallel) == _items(serial)
    assert parallel["definitions"]["run"] == serial["definitions"]["run"]
    assert {k: v for k, v in parallel_env.items() if k != "workers"} == \
        {k: v for k, v in serial_env.items() if k != "workers"}


def test_the_plugin_and_the_server_parser_share_markers_schema_and_limit():
    assert (plugin.BEGIN_MARKER, plugin.END_MARKER, plugin.SCHEMA, plugin.MAX_BLOCK_BYTES, plugin.UNTRACEABLE) == (
        result_block.BEGIN_MARKER, result_block.END_MARKER, result_block.SCHEMA, result_block.MAX_BLOCK_BYTES,
        result_block.UNTRACEABLE)


def test_the_flowgate_conftest_registers_the_plugin_with_its_capability_judgement():
    text = (_TESTS_DIR / "conftest.py").read_text(encoding="utf-8")
    assert "selfcheck_evidence.register(config, capabilities=lambda: dict(_capability_state))" in text
