"""Self-check result block with environment and test definition evidence (flowgate.v02.0114 T#3).

D 0006 2.1 / 3.2 / 3.4 conditions 7-8 / 7.1. ``conftest.py`` registers :class:`EvidencePlugin`
(``register``) next to its own terminal summary. The plugin

* fingerprints, when collection is finished, every collected item's test definition and the
  run-level definition, and hands both to the reports (``user_properties``) -- under xdist
  the workers collect, only the controller prints;
* composes, at ``pytest_terminal_summary``, one result block: counts, failures, environment
  evidence and per-item definition evidence;
* prints it as the very last thing of the output (``pytest_unconfigure``), so that a very
  long failure report still leaves it inside the 64 KiB stdout tail Self-check keeps.

The block (markers and schema mirror ``modules/flow_gate/services/tr_self_check_result.py``,
which reads it as untrusted input)::

    ===FLOWGATE-RESULT-BLOCK-BEGIN v1===
    {"schema": "flowgate.pytest-result/1", "exitstatus", "collected", "completed",
     "outcomes": {passed, failed, error, skipped, xfailed, xpassed},
     "failures": [{"nodeid", "outcome": failed|error}], "failures_total",
     "truncated", "complete",
     "environment": {"tools", "host", "env", "dependencies", "workers"},
     "definitions": {"run", "run_status", "scope": all|failed_only, "items": {nodeid: sha256|"untraceable"}}}
    ===FLOWGATE-RESULT-BLOCK-END v1===

At most 32 KiB: the passing items' definitions go first, then the failure list is cut and the
block says ``truncated`` (and so is not ``complete``).

Definition evidence (D 3.4 condition 8), deliberately conservative (file / module level):

* run level -- the pytest ini file and every loaded conftest with its hooks, session-scoped
  and autouse fixtures and module code, plus the test-tree modules the conftests import.
  A conftest fixture that is neither session-scoped nor autouse is cut out of it and counted
  for the items that use it instead. Every other plugin module of the test tree that pytest
  registered (``pytest_plugins``, ``-p``) is a whole-file input with its imports, and so is
  every test-tree module that is loaded once collection ends but that no item or conftest
  reaches by its imports (a module pulled in from outside the tree, a package ``__init__``):
  their hooks and import-time code can touch any test. The test-tree data files any of these
  name are run-level inputs too (a conftest fixture that reads a file).
* item level -- the test function's own source (decorators and parametrize values included),
  the parametrize ids/values and markers, the module's whole non-test-function code (constants,
  expected values, helpers and their asserts, classes, imports, module-level code -- another
  test's body is not an input), the test-tree modules the module imports (whole files,
  transitively), every fixture the item uses (conftest fixture source; a test-tree fixture
  module with its imports and the data it names; outside the tree: the fixture's source plus
  the test-tree modules and data its module reaches), and the test-tree data files the module
  names. A fixture of an installed distribution (pytest's own, a plugin package) is covered by
  the environment evidence (tools, dependencies) instead.
* untraceable -- dynamic loading (``importlib.import_module`` with a non-literal name,
  ``spec_from_file_location``, ``run_path``, ``exec``/``eval``, ``request.getfixturevalue``)
  makes the item ``untraceable``: never "same".

Product source outside the test tree is not a definition (it is what is under test).
"""
from __future__ import annotations

import ast
import fnmatch
import hashlib
import inspect
import json
import os
import platform
import re
import site
import subprocess
import sys
import sysconfig
import types
from pathlib import Path
from typing import Callable, Optional

import pytest

BEGIN_MARKER = "===FLOWGATE-RESULT-BLOCK-BEGIN v1==="
END_MARKER = "===FLOWGATE-RESULT-BLOCK-END v1==="
SCHEMA = "flowgate.pytest-result/1"
MAX_BLOCK_BYTES = 32 * 1024
UNTRACEABLE = "untraceable"
PLUGIN_NAME = "flowgate-selfcheck-evidence"
PROP_ITEM = "flowgate_definition"
PROP_RUN = "flowgate_definition_run"

#: Where Self-check's HOST_KEYS are declared; read as text, never imported.
_POLICY_FILE = Path(__file__).resolve().parents[1] / "modules" / "flow_gate" / "services" / "tr_self_check_policy.py"
_ADDRESS = re.compile(r" at 0x[0-9a-fA-F]+")
_DYNAMIC_CALLS = {"import_module", "__import__", "spec_from_file_location", "run_path", "load_source",
                  "exec_module", "exec", "eval", "getfixturevalue"}
_SKIP_DIRS = {"__pycache__", ".pytest_cache", "node_modules", ".git"}
_OUTCOMES = ("passed", "failed", "error", "skipped", "xfailed", "xpassed")


def register(config, capabilities: Optional[Callable[[], dict]] = None) -> "EvidencePlugin":
    """Register the plugin once per pytest config. ``capabilities`` returns the host
    capability judgement (``tests/conftest.py`` ``CAPABILITIES``) -- category (b)."""
    plugin = config.pluginmanager.get_plugin(PLUGIN_NAME)
    if plugin is None:
        plugin = EvidencePlugin(config, capabilities)
        config.pluginmanager.register(plugin, PLUGIN_NAME)
    elif capabilities is not None:
        plugin.capabilities = capabilities
    return plugin


def _sha(data) -> str:
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def _canonical(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _sanitize(text: str) -> str:
    return _ADDRESS.sub("", text)


class _Untraceable(Exception):
    pass


# ── environment evidence (D 3.4 condition 7, categories a-e) ─────────────────

def host_keys() -> tuple[str, ...]:
    """``tr_self_check_policy.HOST_KEYS`` read from its source (no import, no side effect)."""
    tree = ast.parse(_POLICY_FILE.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "HOST_KEYS" for t in node.targets):
            value = ast.literal_eval(node.value)
            if isinstance(value, tuple) and value and all(isinstance(v, str) for v in value):
                return value
    raise LookupError("HOST_KEYS")


def _tool_versions() -> dict:
    from importlib import metadata
    try:
        xdist = metadata.version("pytest-xdist")
    except Exception:
        xdist = "absent"
    try:
        done = subprocess.run(["git", "--version"], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              timeout=10, check=False)
        git = done.stdout.decode("utf-8", "replace").strip() if done.returncode == 0 else "unavailable"
    except Exception:
        git = "unavailable"
    return {"python": f"{platform.python_implementation()} {platform.python_version()}",
            "pytest": pytest.__version__, "xdist": xdist, "git": git or "unavailable"}


def _dependencies() -> dict:
    from importlib import metadata
    entries = set()
    for dist in metadata.distributions():
        name = (dist.metadata["Name"] or "").strip()
        if name:
            entries.add(f"{re.sub(r'[-_.]+', '-', name).lower()}=={dist.version}")
    listing = "\n".join(sorted(entries))
    return {"count": len(entries), "sha256": _sha(listing)}


def _workers(config) -> dict:
    """What was applied, not what was asked: xdist resolves ``-n auto`` into ``option.tx``."""
    option = config.option
    numprocesses = getattr(option, "numprocesses", None)
    dist = getattr(option, "dist", "no") or "no"
    tx = getattr(option, "tx", None) or []
    if numprocesses and dist != "no" and tx:
        # Step 4 adds the run tree's processor limit here (cpu_limit).
        return {"mode": "xdist", "applied": len(tx), "dist": str(dist), "cpu_limit": None}
    return {"mode": "serial", "applied": 0, "dist": "no", "cpu_limit": None}


# ── definition evidence (D 3.4 condition 8) ──────────────────────────────────

def _segment(lines: list[str], node) -> tuple[int, int]:
    start = min([d.lineno for d in getattr(node, "decorator_list", [])] + [node.lineno])
    return start, node.end_lineno


def _calls(tree) -> list[tuple[str, Optional[ast.Call]]]:
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            name = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else None
            if name in _DYNAMIC_CALLS:
                found.append((name, node))
    return found


def _is_fixture_decorator(dec) -> bool:
    target = dec.func if isinstance(dec, ast.Call) else dec
    return (isinstance(target, ast.Name) and target.id == "fixture") or (
        isinstance(target, ast.Attribute) and target.attr == "fixture")


class _ModuleInfo:
    """One source file, parsed once."""

    def __init__(self, path: Path, collector: "_DefinitionCollector"):
        self.path = path
        self.text = path.read_text(encoding="utf-8")
        self.tree = ast.parse(self.text)
        self.lines = self.text.splitlines(keepends=True)
        self.dynamic = False
        self.imports: set[Path] = set()
        for name, call in _calls(self.tree):
            literal = call.args[0].value if call.args and isinstance(call.args[0], ast.Constant) \
                and isinstance(call.args[0].value, str) else None
            if name in ("import_module", "__import__") and literal:
                self.imports |= collector.resolve_module(literal, path, 0)
            else:
                self.dynamic = True
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    self.imports |= collector.resolve_module(alias.name, path, 0)
            elif isinstance(node, ast.ImportFrom):
                base = node.module or ""
                self.imports |= collector.resolve_module(base, path, node.level)
                for alias in node.names:
                    self.imports |= collector.resolve_module(f"{base}.{alias.name}" if base else alias.name,
                                                             path, node.level)
        self.imports.discard(path)

    def cut(self, segments: dict[str, tuple[int, int]]) -> str:
        """The source without the given segments (1-based inclusive line ranges)."""
        drop = set()
        for start, end in segments.values():
            drop.update(range(start, end + 1))
        return "".join(line for number, line in enumerate(self.lines, 1) if number not in drop)

    def text_of(self, segment: tuple[int, int]) -> str:
        return "".join(self.lines[segment[0] - 1:segment[1]])


class _TestModule:
    def __init__(self, info: _ModuleInfo, functions: list[str], classes: list[str]):
        self.info = info
        self.segments: dict[str, tuple[int, int]] = {}
        self.names: dict[str, set[str]] = {}

        def is_test(name: str, patterns: list[str]) -> bool:
            return any(fnmatch.fnmatch(name, p) if any(c in p for c in "*?[") else name.startswith(p)
                       for p in patterns)

        def record(key: str, node) -> None:
            self.segments[key] = _segment(info.lines, node)
            self.names[key] = {n.id for n in ast.walk(node) if isinstance(n, ast.Name)} | {
                n.attr for n in ast.walk(node) if isinstance(n, ast.Attribute)}

        for node in info.tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and is_test(node.name, functions):
                record(node.name, node)
            elif isinstance(node, ast.ClassDef):
                unittest_case = any((isinstance(b, ast.Name) and b.id.endswith("TestCase")) or (
                    isinstance(b, ast.Attribute) and b.attr.endswith("TestCase")) for b in node.bases)
                if not (unittest_case or is_test(node.name, classes)):
                    continue
                for child in node.body:
                    if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) and (
                            is_test(child.name, functions) or (unittest_case and child.name.startswith("test"))):
                        record(f"{node.name}.{child.name}", child)
        self.skeleton = _sha(info.cut(self.segments))
        self.segment_hash = {key: _sha(info.text_of(seg)) for key, seg in self.segments.items()}
        self.uses_getfixturevalue = {key for key in self.segments if "getfixturevalue" in self.names[key]}


class _Conftest:
    def __init__(self, info: _ModuleInfo):
        self.info = info
        itemizable: dict[str, tuple[int, int]] = {}
        for node in info.tree.body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            decorators = [d for d in node.decorator_list if _is_fixture_decorator(d)]
            if not decorators:
                continue
            name, run_level = node.name, False
            for dec in decorators:
                for kw in (dec.keywords if isinstance(dec, ast.Call) else []):
                    value = kw.value.value if isinstance(kw.value, ast.Constant) else ...
                    if kw.arg == "autouse" and value is not False:
                        run_level = True
                    elif kw.arg == "scope" and value not in ("function", "class", "module"):
                        run_level = True   # session/package, or not a literal: whole run
                    elif kw.arg == "name":
                        if not isinstance(value, str):
                            run_level = True
                        else:
                            name = value
            if not run_level:
                itemizable[name] = _segment(info.lines, node)
        self.fixtures = {name: _sha(info.text_of(seg)) for name, seg in itemizable.items()}
        self.skeleton = _sha(info.cut(itemizable))


def _installed_bases() -> list[Path]:
    """Where installed distributions and the standard library live: code there is a tool or a
    dependency (environment evidence), never a test definition."""
    bases = set()
    paths = sysconfig.get_paths()
    candidates = [paths.get(k) for k in ("stdlib", "platstdlib", "purelib", "platlib")]
    try:
        candidates += list(site.getsitepackages())
    except Exception:
        pass
    try:
        candidates.append(site.getusersitepackages())
    except Exception:
        pass
    for entry in candidates:
        if entry:
            try:
                bases.add(Path(entry).resolve())
            except OSError:
                continue
    return sorted(bases)


def _module_file(module) -> Optional[Path]:
    name = getattr(module, "__file__", None)
    if not isinstance(name, str) or not name.endswith(".py"):
        return None
    try:
        return Path(name).resolve()
    except OSError:
        return None


class _DefinitionCollector:
    def __init__(self, session):
        self.session = session
        self.config = session.config
        registered = {f for f in (_module_file(p) for p in self.config.pluginmanager.get_plugins()
                                  if isinstance(p, types.ModuleType)) if f is not None}
        self.conftests = sorted(f for f in registered if f.name == "conftest.py")
        dirs = [c.parent for c in self.conftests]
        tops = [d for d in dirs if not any(o != d and o in d.parents for o in dirs)]
        self.roots = tops or [Path(str(self.config.rootpath)).resolve()]
        # Test-tree plugin modules pytest loaded by name (pytest_plugins, -p): hooks of the run.
        self.plugin_modules = sorted(f for f in registered if f.name != "conftest.py" and self.in_tree(f))
        self.installed = [b for b in _installed_bases() if not self.in_tree(b)]
        # Only sys.path entries a test-tree file can be found from: inside the tree or above it.
        self.search = []
        for entry in sys.path:
            try:
                base = Path(entry or os.getcwd()).resolve()
            except OSError:
                continue
            if base.is_dir() and base not in self.search and (
                    self.in_tree(base) or any(base in root.parents for root in self.roots)):
                self.search.append(base)
        self._resolved: dict[tuple, frozenset] = {}
        self._files: dict[Path, str] = {}
        self._infos: dict[Path, _ModuleInfo] = {}
        self._tests: dict[Path, _TestModule] = {}
        self._confs: dict[Path, _Conftest] = {}
        self._closure: dict[Path, tuple[tuple, bool]] = {}
        self.functions = list(self.config.getini("python_functions") or ["test"])
        self.classes = list(self.config.getini("python_classes") or ["Test"])
        self.data_files = self._scan_data()

    def in_tree(self, path: Path) -> bool:
        return any(root == path or root in path.parents for root in self.roots)

    def is_installed(self, path: Path) -> bool:
        return not self.in_tree(path) and any(base == path or base in path.parents for base in self.installed)

    def file_hash(self, path: Path) -> str:
        if path not in self._files:
            self._files[path] = _sha(path.read_bytes())
        return self._files[path]

    def info(self, path: Path) -> _ModuleInfo:
        if path not in self._infos:
            self._infos[path] = _ModuleInfo(path, self)
        return self._infos[path]

    def resolve_module(self, name: str, importer: Path, level: int) -> set[Path]:
        """Test-tree files a module name can mean, by path only (nothing is imported)."""
        if not name and not level:
            return set()
        key = (name, importer.parent if level else None, level)
        if key not in self._resolved:
            parts = [p for p in name.split(".") if p]
            bases = [importer.parent.parents[level - 2] if level > 1 else importer.parent] if level else self.search
            found = set()
            for base in bases:
                target = base.joinpath(*parts) if parts else base
                if not self.in_tree(target):
                    continue   # product / third-party code is not a definition
                for candidate in (target.with_suffix(".py") if parts else None, target / "__init__.py"):
                    if candidate is not None and candidate.is_file():
                        found.add(candidate.resolve())
            self._resolved[key] = frozenset(found)
        return set(self._resolved[key])

    def closure(self, path: Path) -> tuple[tuple, bool]:
        """``((file, sha256), ...)`` of the test-tree modules ``path`` imports, transitively, and
        whether any of them (or ``path``) loads code dynamically."""
        if path in self._closure:
            return self._closure[path]
        seen, dynamic, stack = set(), False, [path]
        while stack:
            current = stack.pop()
            if current in seen:
                continue
            seen.add(current)
            info = self.info(current)
            dynamic = dynamic or info.dynamic
            stack.extend(info.imports - seen)
        seen.discard(path)
        result = (tuple(sorted((str(p), self.file_hash(p)) for p in seen)), dynamic)
        self._closure[path] = result
        return result

    def _scan_data(self) -> list[Path]:
        """Non-Python files of the test tree; also keeps the tree's Python files for
        ``unattributed_data``. Taken before any test runs."""
        files, self._python = [], []
        for root in self.roots:
            for dirpath, dirnames, filenames in os.walk(root):
                dirnames[:] = sorted(d for d in dirnames if d not in _SKIP_DIRS and not d.startswith(".test-tmp"))
                for f in sorted(filenames):
                    path = Path(dirpath, f).resolve()
                    if f.endswith(".py"):
                        self._python.append(path)
                    elif not f.endswith((".pyc", ".pyo")):
                        files.append(path)
        return sorted(files)

    def data_named(self, paths) -> list:
        """Test-tree data files any of the given source files names."""
        texts = [self.info(Path(p)).text for p in paths]
        return [(str(f), self.file_hash(f)) for f in self.data_files if any(f.name in t for t in texts)]

    def data_for(self, path: Path) -> list:
        """Test-tree data files the module (or a test-tree module it imports) names."""
        return self.data_named([path] + [Path(p) for p, _h in self.closure(path)[0]])

    def loaded_unreached(self) -> list[Path]:
        """Test-tree modules loaded now that no static analysis so far has reached: not a test
        module, a conftest, a registered plugin or anything they import. Called after every
        item was fingerprinted, so what is left is code no item accounts for."""
        reached = {p for p in self._infos if self.in_tree(p)}
        patterns = list(self.config.getini("python_files") or ["test_*.py", "*_test.py"])
        out = set()
        for module in list(sys.modules.values()):
            path = _module_file(module)
            if path is None or path in reached or not self.in_tree(path) or not path.is_file():
                continue
            if any(fnmatch.fnmatch(path.name, pattern) for pattern in patterns):
                continue   # a test module whose items are not selected is no input of the run
            out.add(path)
        return sorted(out)

    def unattributed_data(self) -> str:
        """Data files no module names: an input of every item (they may be found dynamically)."""
        if not hasattr(self, "_unattributed"):
            # Every Python file of the tree, so the answer does not depend on the selection.
            named = set()
            for path in self._python:
                try:
                    text = path.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                named.update(f for f in self.data_files if f.name in text)
            self._unattributed = _sha(_canonical([(str(f), self.file_hash(f)) for f in self.data_files
                                                  if f not in named]))
        return self._unattributed

    def test_module(self, path: Path) -> _TestModule:
        if path not in self._tests:
            self._tests[path] = _TestModule(self.info(path), self.functions, self.classes)
        return self._tests[path]

    def conftest(self, path: Path) -> _Conftest:
        if path not in self._confs:
            self._confs[path] = _Conftest(self.info(path))
        return self._confs[path]

    # run level -------------------------------------------------------------
    def run_fingerprint(self) -> Optional[str]:
        """Call after ``item_fingerprint`` for every item (``loaded_unreached`` depends on it)."""
        inipath = getattr(self.config, "inipath", None)
        parts = {"ini": self.file_hash(Path(inipath).resolve()) if inipath else None,
                 "conftests": [], "plugins": [], "loaded": [], "data": []}
        sources = []
        for path in self.conftests:
            closure, dynamic = self.closure(path)
            if dynamic:
                return None
            parts["conftests"].append([str(path), self.conftest(path).skeleton, closure])
            sources += [path] + [Path(p) for p, _h in closure]
        for key, paths in (("plugins", self.plugin_modules), ("loaded", self.loaded_unreached())):
            for path in paths:
                closure, dynamic = self.closure(path)
                if dynamic:
                    return None
                parts[key].append([str(path), self.file_hash(path), closure])
                sources += [path] + [Path(p) for p, _h in closure]
        parts["data"] = self.data_named(sorted(set(sources)))
        return _sha(_canonical(parts))

    # item level ------------------------------------------------------------
    def _fixture_part(self, fixturedef, module: Path) -> list:
        func = fixturedef.func
        getter = getattr(func, "_get_wrapped_function", None)   # pytest >= 8.4 fixture objects
        if callable(getter):
            try:
                func = getter()
            except Exception:
                pass
        func = inspect.unwrap(func)
        func = getattr(func, "__func__", func)
        try:
            source_file = Path(inspect.getsourcefile(func) or "").resolve()
        except TypeError as exc:
            raise _Untraceable("fixture source") from exc
        if source_file == module:
            return ["module"]
        if source_file in self.conftests:
            conftest = self.conftest(source_file)
            digest = conftest.fixtures.get(fixturedef.argname)
            return ["conftest", str(source_file), digest or "run"]
        if self.in_tree(source_file):
            closure, dynamic = self.closure(source_file)
            if dynamic:
                raise _Untraceable("fixture module loads code dynamically")
            return ["tree", str(source_file), self.file_hash(source_file), closure, self.data_for(source_file)]
        try:
            source = _sha(inspect.getsource(func))
        except (OSError, TypeError) as exc:
            raise _Untraceable("fixture source") from exc
        qualname = getattr(func, "__qualname__", "")
        if self.is_installed(source_file):
            return ["installed", qualname, source]
        # Defined outside the tree, but it may take test-tree modules and data: those are inputs.
        closure, dynamic = self.closure(source_file)
        if dynamic:
            raise _Untraceable("fixture module loads code dynamically")
        return ["external", qualname, source, closure, self.data_for(source_file)]

    def item_fingerprint(self, item) -> str:
        if not isinstance(item, pytest.Function) or getattr(item, "path", None) is None:
            return UNTRACEABLE
        module = Path(str(item.path)).resolve()
        if not self.in_tree(module):
            return UNTRACEABLE
        try:
            test = self.test_module(module)
            closure, dynamic = self.closure(module)
            if dynamic or test.info.dynamic:
                return UNTRACEABLE
            name = getattr(item, "originalname", None) or item.name
            key = f"{item.cls.__name__}.{name}" if getattr(item, "cls", None) is not None else name
            if key in test.uses_getfixturevalue:
                return UNTRACEABLE
            own = test.segment_hash.get(key, "in-skeleton")
            called = sorted(test.segment_hash[k] for k in test.segments
                            if k != key and k.split(".")[-1] in test.names.get(key, ()))
            callspec = getattr(item, "callspec", None)
            params = None if callspec is None else [callspec.id, _sanitize(repr(sorted(
                (k, repr(v)) for k, v in callspec.params.items())))]
            markers = sorted([m.name, _sanitize(repr(m.args)), _sanitize(repr(sorted(m.kwargs.items())))]
                             for m in item.iter_markers())
            fixtures = []
            name2defs = getattr(getattr(item, "_fixtureinfo", None), "name2fixturedefs", {}) or {}
            for fixture_name in sorted(item.fixturenames):
                for fixturedef in name2defs.get(fixture_name, ()):
                    fixtures.append([fixture_name, self._fixture_part(fixturedef, module)])
            parts = {"nodeid": item.nodeid, "own": own, "called": called, "params": params,
                     "markers": markers, "skeleton": test.skeleton, "imports": closure,
                     "fixtures": fixtures, "data": self.data_for(module),
                     "unattributed_data": self.unattributed_data()}
            return _sha(_canonical(parts))
        except (_Untraceable, OSError, SyntaxError, ValueError, UnicodeDecodeError):
            return UNTRACEABLE


# ── the plugin ───────────────────────────────────────────────────────────────

class EvidencePlugin:
    def __init__(self, config, capabilities: Optional[Callable[[], dict]] = None):
        self.config = config
        self.capabilities = capabilities
        self.is_worker = getattr(config, "workerinput", None) is not None
        try:
            keys = host_keys()
            self.env_snapshot = {key: os.environ.get(key) for key in keys}
        except Exception:
            self.env_snapshot = None
        self.collected = 0
        self.failures: dict[str, str] = {}
        self.outcomes = {name: 0 for name in _OUTCOMES}
        self.finished: set[str] = set()
        self.definitions: dict[str, str] = {}
        self.run_definitions: set[str] = set()
        self.block: Optional[str] = None

    # collecting process (serial session or xdist worker) ----------------------
    @pytest.hookimpl(trylast=True)
    def pytest_collection_finish(self, session):
        try:
            collector = _DefinitionCollector(session)
        except Exception:
            collector = None
        digests = []
        for item in session.items:
            try:
                digest = collector.item_fingerprint(item) if collector else UNTRACEABLE
            except Exception:
                digest = UNTRACEABLE
            digests.append(digest)
        try:
            # After the items: what they reach decides which loaded modules are run-level.
            run = (collector.run_fingerprint() if collector else None) or UNTRACEABLE
        except Exception:
            run = UNTRACEABLE
        for item, digest in zip(session.items, digests):
            item.user_properties.append((PROP_ITEM, digest))
            item.user_properties.append((PROP_RUN, run))

    # reporting process (serial session or xdist controller) -------------------
    def pytest_runtest_logreport(self, report):
        if self.is_worker:
            return
        nodeid = report.nodeid
        for key, value in getattr(report, "user_properties", None) or ():
            if key == PROP_ITEM and isinstance(value, str):
                self.definitions[nodeid] = value
            elif key == PROP_RUN and isinstance(value, str):
                self.run_definitions.add(value)
        xfail = hasattr(report, "wasxfail")
        if report.when == "call":
            if xfail:
                self.outcomes["xfailed" if report.skipped else "xpassed"] += 1
            elif report.passed:
                self.outcomes["passed"] += 1
            elif report.failed:
                self.outcomes["failed"] += 1
            elif report.skipped:
                self.outcomes["skipped"] += 1
        elif report.failed:
            self.outcomes["error"] += 1
        elif report.skipped:
            self.outcomes["xfailed" if xfail else "skipped"] += 1
        if report.failed:
            if report.when == "call" or nodeid not in self.failures:
                self.failures[nodeid] = "failed" if report.when == "call" else "error"
        if report.when == "teardown":
            self.finished.add(nodeid)

    def pytest_sessionfinish(self, session, exitstatus):
        self.collected = int(getattr(session, "testscollected", 0) or 0)

    @pytest.hookimpl(trylast=True)
    def pytest_terminal_summary(self, terminalreporter, exitstatus, config):
        if self.is_worker:
            return
        try:
            self.block = self.compose(int(exitstatus))
        except Exception as exc:  # the block is evidence; it never fails the run
            self.block = None
            terminalreporter.write_line(f"flowgate result block not written: {type(exc).__name__}")

    @pytest.hookimpl(trylast=True)
    def pytest_unconfigure(self, config):
        if self.is_worker or not self.block:
            return
        reporter = config.pluginmanager.get_plugin("terminalreporter")
        text = f"{BEGIN_MARKER}\n{self.block}\n{END_MARKER}"
        if reporter is not None:
            reporter.ensure_newline()
            reporter.write_line(text)
            flush = getattr(reporter, "flush", None)
            if callable(flush):
                flush()
        else:
            sys.stdout.write("\n" + text + "\n")
            sys.stdout.flush()

    # composition ----------------------------------------------------------------
    def environment(self) -> dict:
        env = {}
        for name, build in (("tools", _tool_versions), ("dependencies", _dependencies),
                            ("workers", lambda: _workers(self.config))):
            try:
                env[name] = build()
            except Exception:
                pass   # a category that cannot be built is missing, never guessed
        try:
            capabilities = dict(self.capabilities()) if self.capabilities else {}
            if capabilities:
                env["host"] = {"os": platform.system() or "unknown", "platform": sys.platform,
                               "capabilities": {str(k): bool(v) for k, v in sorted(capabilities.items())}}
        except Exception:
            pass
        if self.env_snapshot:
            env["env"] = dict(self.env_snapshot)
        return env

    def compose(self, exitstatus: int) -> str:
        values = self.run_definitions
        if not values:
            run, run_status = None, "missing"
        elif len(values) > 1:
            run, run_status = None, "inconsistent"
        else:
            only = next(iter(values))
            run, run_status = (None, UNTRACEABLE) if only == UNTRACEABLE else (only, "ok")
        failures = [{"nodeid": n, "outcome": o} for n, o in sorted(self.failures.items())]
        completed = len(self.finished)
        block = {
            "schema": SCHEMA, "exitstatus": exitstatus, "collected": self.collected, "completed": completed,
            "outcomes": dict(self.outcomes), "failures": failures, "failures_total": len(failures),
            "truncated": False,
            "complete": self.collected == completed and exitstatus in (0, 1),
            "environment": self.environment(),
            "definitions": {"run": run, "run_status": run_status, "scope": "all",
                            "items": dict(sorted(self.definitions.items()))},
        }
        text = _canonical(block)
        if len(text.encode("utf-8")) > MAX_BLOCK_BYTES:
            failed = set(self.failures)
            block["definitions"]["scope"] = "failed_only"
            block["definitions"]["items"] = {k: v for k, v in block["definitions"]["items"].items() if k in failed}
            text = _canonical(block)
        while len(text.encode("utf-8")) > MAX_BLOCK_BYTES and block["failures"]:
            keep = block["failures"][:len(block["failures"]) // 2]
            kept = {f["nodeid"] for f in keep}
            block.update(failures=keep, truncated=True, complete=False)
            block["definitions"]["items"] = {k: v for k, v in block["definitions"]["items"].items() if k in kept}
            text = _canonical(block)
        if len(text.encode("utf-8")) > MAX_BLOCK_BYTES:
            block["definitions"] = {"run": None, "run_status": "missing", "scope": "failed_only", "items": {}}
            block.update(truncated=True, complete=False)
            text = _canonical(block)
        return text


def pytest_configure(config):
    """``-p selfcheck_evidence`` outside FlowGate's conftest: register without capabilities."""
    register(config)
