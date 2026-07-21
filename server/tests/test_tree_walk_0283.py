"""0283 TS0006 — get_file_tree tree-walk hardening (TR0005 §B).

Behavioural coverage for the fix to bug 0283.0001-B ("트리를 불러오지 못했습니다."):
`get_file_tree`'s directory walk used to be `os.listdir` wrapped in a bare
`except (OSError, PermissionError): return`, so a single transient failure on
remote/UNC storage silently produced an empty or partial tree with no log line.
TR0005 replaced it with `os.scandir` behind a bounded retry/backoff helper
(`_list_dir_with_retry`) that logs a warning once the retries are exhausted, and
guarded `os.path.relpath` against the Windows cross-drive `ValueError`.

`_list_dir_with_retry` and `walk_directory` are closures inside `get_file_tree`,
which itself needs a provisioned project + DB, so the retry helper is lifted out
of the real source with `ast` and exercised directly: the code under test is the
committed source, not a copy. The surrounding walk is verified structurally on
the same AST.

Deliberately stdlib-only (`unittest`, no pytest) so it runs on a bare
interpreter with no venv/pip step:

    cd server && python tests/test_tree_walk_0283.py
"""

from __future__ import annotations

import ast
import logging
import os
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

SERVER_ROOT = Path(__file__).resolve().parents[1]
SOURCE = SERVER_ROOT / "modules" / "flow_gate" / "process_service.py"
if str(SERVER_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVER_ROOT))


def _module_ast() -> ast.Module:
    return ast.parse(SOURCE.read_text(encoding="utf-8"), filename=str(SOURCE))


def _find_function(node: ast.AST, name: str) -> ast.FunctionDef:
    for child in ast.walk(node):
        if isinstance(child, ast.FunctionDef) and child.name == name:
            return child
    raise AssertionError(f"function {name!r} not found in {SOURCE}")


def _load_retry_helper():
    """exec `_list_dir_with_retry` exactly as committed, in an isolated namespace."""
    lines = SOURCE.read_text(encoding="utf-8").splitlines()
    fn = _find_function(_module_ast(), "_list_dir_with_retry")
    src = textwrap.dedent("\n".join(lines[fn.lineno - 1 : fn.end_lineno]))
    namespace: dict = {
        "os": os,
        "time": _FakeTime(),
        "logger": logging.getLogger("flowgate.test.tree_walk_0283"),
    }
    exec(compile(src, str(SOURCE), "exec"), namespace)  # noqa: S102 - committed source
    return namespace["_list_dir_with_retry"], namespace["time"]


class _FakeTime:
    """Stand-in for `time` so backoff is asserted on, not actually slept through."""

    def __init__(self) -> None:
        self.slept: list[float] = []

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)


class _FlakyScandir:
    """os.scandir that raises `failures` times before delegating to the real one."""

    def __init__(self, exc: Exception, failures: int) -> None:
        self.exc = exc
        self.failures = failures
        self.calls = 0
        self.real = os.scandir  # bound before patching, so the fallthrough is the real one

    def __call__(self, path):
        self.calls += 1
        if self.calls <= self.failures:
            raise self.exc
        return self.real(path)


class ListDirWithRetryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.list_dir, self.faketime = _load_retry_helper()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = self.tmp.name
        os.mkdir(os.path.join(self.root, "sub"))
        with open(os.path.join(self.root, "a.md"), "w", encoding="utf-8") as fh:
            fh.write("x")

    def _names(self, entries) -> list[str]:
        return sorted(entry.name for entry in entries)

    def test_happy_path_returns_scandir_entries_with_cached_types(self):
        entries = self.list_dir(self.root)
        self.assertEqual(["a.md", "sub"], self._names(entries))
        # scandir DirEntry (not a plain str): the walk relies on entry.is_dir()/entry.path
        # to avoid a per-entry stat() round-trip on remote storage.
        by_name = {entry.name: entry for entry in entries}
        self.assertTrue(by_name["sub"].is_dir())
        self.assertFalse(by_name["a.md"].is_dir())
        self.assertEqual(os.path.join(self.root, "a.md"), by_name["a.md"].path)
        self.assertEqual([], self.faketime.slept)

    def test_transient_oserror_is_retried_and_recovers(self):
        flaky = _FlakyScandir(OSError(5, "network hiccup"), failures=2)
        real_scandir, os.scandir = os.scandir, flaky
        self.addCleanup(lambda: setattr(os, "scandir", real_scandir))
        entries = self.list_dir(self.root)
        self.assertEqual(["a.md", "sub"], self._names(entries))
        self.assertEqual(3, flaky.calls)
        self.assertEqual(2, len(self.faketime.slept))  # backed off between attempts

    def test_transient_permission_error_is_retried_and_recovers(self):
        # Windows sharing violation while a worker writes the same directory.
        flaky = _FlakyScandir(PermissionError(13, "sharing violation"), failures=1)
        real_scandir, os.scandir = os.scandir, flaky
        self.addCleanup(lambda: setattr(os, "scandir", real_scandir))
        self.assertEqual(["a.md", "sub"], self._names(self.list_dir(self.root)))
        self.assertEqual(2, flaky.calls)

    def test_backoff_grows_between_attempts(self):
        flaky = _FlakyScandir(OSError("down"), failures=99)
        real_scandir, os.scandir = os.scandir, flaky
        self.addCleanup(lambda: setattr(os, "scandir", real_scandir))
        with self.assertLogs("flowgate.test.tree_walk_0283", level="WARNING"):
            self.list_dir(self.root, retries=3, delay=0.3)
        self.assertEqual(3, len(self.faketime.slept))
        self.assertAlmostEqual(0.3, self.faketime.slept[0])
        self.assertAlmostEqual(0.6, self.faketime.slept[1])
        self.assertTrue(all(b > a for a, b in zip(self.faketime.slept, self.faketime.slept[1:])))

    def test_exhausted_retries_log_a_warning_and_degrade_to_empty(self):
        flaky = _FlakyScandir(OSError("gone"), failures=99)
        real_scandir, os.scandir = os.scandir, flaky
        self.addCleanup(lambda: setattr(os, "scandir", real_scandir))
        with self.assertLogs("flowgate.test.tree_walk_0283", level="WARNING") as captured:
            result = self.list_dir(self.root, retries=3)
        # Degrades this subtree only — it must not raise and 500 the whole tree request.
        self.assertEqual([], result)
        self.assertEqual(3, flaky.calls)
        self.assertEqual(1, len(captured.records))
        message = captured.records[0].getMessage()
        self.assertIn("get_file_tree", message)
        self.assertIn(self.root, message)  # the failing path is diagnosable
        self.assertIn("gone", message)  # ...and so is the cause

    def test_missing_directory_does_not_raise(self):
        with self.assertLogs("flowgate.test.tree_walk_0283", level="WARNING"):
            self.assertEqual([], self.list_dir(os.path.join(self.root, "nope")))


class WalkDirectoryStructureTest(unittest.TestCase):
    """The walk itself needs a provisioned project, so assert on its AST."""

    def setUp(self) -> None:
        self.get_file_tree = _find_function(_module_ast(), "get_file_tree")
        self.walk = _find_function(self.get_file_tree, "walk_directory")

    def _attribute_calls(self, node: ast.AST) -> set[str]:
        names = set()
        for child in ast.walk(node):
            if isinstance(child, ast.Call) and isinstance(child.func, ast.Attribute):
                value = child.func.value
                if isinstance(value, ast.Name):
                    names.add(f"{value.id}.{child.func.attr}")
                else:
                    names.add(child.func.attr)
        return names

    def test_walk_uses_the_retry_helper_and_no_bare_listdir(self):
        calls = self._attribute_calls(self.walk)
        plain = {
            child.func.id
            for child in ast.walk(self.walk)
            if isinstance(child, ast.Call) and isinstance(child.func, ast.Name)
        }
        self.assertIn("_list_dir_with_retry", plain)
        self.assertNotIn("os.listdir", calls)

    def test_walk_reads_the_cached_dirent_type_instead_of_stat(self):
        calls = self._attribute_calls(self.walk)
        self.assertIn("entry.is_dir", calls)
        self.assertNotIn("isdir", calls)  # no os.path.isdir stat() per entry

    def test_walk_no_longer_swallows_listing_errors_silently(self):
        # The old shape was `try: os.listdir(...) except (OSError, PermissionError): return`.
        for handler in [n for n in ast.walk(self.walk) if isinstance(n, ast.ExceptHandler)]:
            self.assertFalse(
                all(isinstance(stmt, ast.Return) and stmt.value is None for stmt in handler.body),
                "walk_directory still has a bare swallow-and-return handler",
            )

    def test_relpath_is_guarded_against_cross_drive_valueerror(self):
        guarded = False
        for node in ast.walk(self.get_file_tree):
            if not isinstance(node, ast.Try):
                continue
            body = "".join(ast.dump(stmt) for stmt in node.body)
            if "relpath" not in body:
                continue
            for handler in node.handlers:
                if isinstance(handler.type, ast.Name) and handler.type.id == "ValueError":
                    guarded = True
        self.assertTrue(guarded, "os.path.relpath is not guarded against ValueError")

    def test_retry_helper_defaults_are_bounded(self):
        fn = _find_function(self.get_file_tree, "_list_dir_with_retry")
        defaults = [ast.literal_eval(d) for d in fn.args.defaults]
        self.assertEqual([3, 0.3], defaults)  # bounded — never an unbounded retry loop


class ModuleWiringTest(unittest.TestCase):
    def test_module_imports_cleanly_and_exposes_a_logger(self):
        import modules.flow_gate.process_service as process_service

        self.assertIsInstance(process_service.logger, logging.Logger)
        self.assertEqual(
            "modules.flow_gate.process_service", process_service.logger.name
        )
        self.assertTrue(callable(process_service.get_file_tree))


if __name__ == "__main__":
    unittest.main(verbosity=2)
