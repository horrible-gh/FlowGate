"""Area ownership and opt-in pytest selection (paths relative to server/).

No test module is imported to find its owner. A module-level literal
pytestmark = pytest.mark.area("name") (also inside a list) overrides the map.
Patterns use fnmatch on POSIX paths; * may span directories. Dotted names
are flat TOML keys, not nested tables. `also` expands one hop from standard upward
(not at veryfast/fast, which stay inside the changed sub-area).
"""
from __future__ import annotations

import ast
import os
from fnmatch import fnmatchcase
from pathlib import Path, PurePosixPath
import tomllib

import pytest


def path_key(value: str) -> str:
    value = value.replace("\\", "/")
    path = PurePosixPath(value)
    if not value.strip() or path.is_absolute() or ".." in path.parts or ":" in value:
        raise ValueError(f"expected a server-relative path: {value!r}")
    return str(path)


def declared_area(path: Path) -> str | None:
    text = path.read_text(encoding="utf-8-sig")
    if "pytestmark" not in text or ".area" not in text:
        return None
    tree = ast.parse(text, filename=str(path))
    declarations = []
    for stmt in tree.body:
        targets = stmt.targets if isinstance(stmt, ast.Assign) else (
            [stmt.target] if isinstance(stmt, ast.AnnAssign) else [])
        if not any(isinstance(t, ast.Name) and t.id == "pytestmark" for t in targets):
            continue
        for node in ast.walk(stmt.value):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "area"):
                if (len(node.args) != 1 or node.keywords
                        or not isinstance(node.args[0], ast.Constant)
                        or not isinstance(node.args[0].value, str)):
                    raise ValueError(f"{path}: area marker needs one literal string")
                declarations.append(node.args[0].value)
    if len(declarations) > 1:
        raise ValueError(f"{path}: multiple module area markers")
    return declarations[0] if declarations else None


class AreaMap:
    def __init__(self, root: Path):
        self.root = root.resolve()
        with (self.root / "tests/areas.toml").open("rb") as stream:
            self.areas = tomllib.load(stream)["area"]
        if not self.areas:
            raise ValueError("areas.toml has no areas")
        for name, entry in self.areas.items():
            if not isinstance(entry, dict) or set(entry) - {"source", "tests", "also"}:
                raise ValueError(f"invalid area entry {name}; quote dotted names")
            for key in ("source", "tests", "also"):
                values = entry.get(key, [])
                if not isinstance(values, list) or not all(isinstance(v, str) and v for v in values):
                    raise ValueError(f"{name}.{key} must be a list of nonempty strings")
                if key != "also":
                    for value in values:
                        path_key(value)
            for other in entry.get("also", []):
                self.expand(other)  # reject misspelled references even for fast runs
        self.files = {
            path.relative_to(self.root).as_posix(): path
            for path in sorted((self.root / "tests").rglob("test_*.py"))
            if "__pycache__" not in path.parts
        }
        self.owners = {key: self.owner(key, path) for key, path in self.files.items()}

    def expand(self, name: str) -> set[str]:
        names = {a for a in self.areas if a == name or a.startswith(name + ".")}
        if not names:
            raise ValueError(f"unknown area: {name}")
        return names

    def owner(self, key: str, path: Path | None = None) -> str:
        marker = declared_area(path) if path is not None else None
        if marker is not None:
            if marker not in self.areas:
                raise ValueError(f"{key}: unknown module area {marker}")
            return marker
        matches = [a for a, entry in self.areas.items()
                   if any(fnmatchcase(key, p) for p in entry.get("tests", []))]
        if len(matches) != 1:
            raise ValueError(f"{key}: expected exactly one area, got {matches}")
        return matches[0]

    def validate_patterns(self) -> None:
        # Use the same matcher as selection (including * across directories).
        source_files = []
        for directory, dirs, files in os.walk(self.root):
            dirs[:] = [d for d in dirs if d not in (".venv", "venv", "node_modules", ".git", "__pycache__")]
            source_files.extend((Path(directory) / f).relative_to(self.root).as_posix() for f in files)
        for name, entry in self.areas.items():
            for key, candidates in (("tests", self.files), ("source", source_files)):
                for pattern in entry.get(key, []):
                    if not any(fnmatchcase(p, pattern) for p in candidates):
                        raise ValueError(f"{name}.{key}: pattern matches no files: {pattern}")

    def select(self, area: str | None, changed: str | None, tier: str) -> set[str]:
        selected = set()
        if area is not None:
            for name in area.split(","):
                selected.update(self.expand(name.strip()))
        if changed is not None:
            for raw in changed.split(","):
                key = path_key(raw.strip())
                if key in self.owners:
                    matches = {self.owners[key]}
                elif key.startswith("tests/") and PurePosixPath(key).name.startswith("test_"):
                    matches = {self.owner(key)}  # deleted test path can still resolve via the map
                else:
                    matches = {a for a, entry in self.areas.items()
                               if any(fnmatchcase(key, p) for p in entry.get("source", []))}
                # A test module can also supply fixtures/helpers to other areas.
                # Explicit source dependencies still apply to such a path.
                matches.update(a for a, entry in self.areas.items()
                               if any(fnmatchcase(key, p) for p in entry.get("source", [])))
                if not matches:
                    raise ValueError(f"changed path has no area: {key}")
                selected.update(matches)
        if tier not in ("veryfast", "fast"):
            for name in tuple(selected):
                for other in self.areas[name].get("also", []):
                    selected.update(self.expand(other))
        return selected


class AreaSelection:
    def __init__(self, config, root: Path):
        try:
            self.mapping = AreaMap(root)
            # The install check runs every tier, so it expands `also` like the top tier.
            self.tier = ("install-check" if getattr(config.option, "install_check", False)
                         else config.getoption("--tier"))
            self.selected = self.mapping.select(config.getoption("--area"),
                config.getoption("--changed"), self.tier)
        except (OSError, ValueError, KeyError, SyntaxError) as exc:
            raise pytest.UsageError(f"test area selection: {exc}") from exc
        self.allowed = {key for key, area in self.mapping.owners.items() if area in self.selected}
        self.collected = set()
        # Pytest does not call ignore_collect for explicitly named files.
        args = []
        for arg in config.args:
            target = Path(str(config.invocation_params.dir)) / arg.split("::", 1)[0]
            key = self._key(target)
            if key not in self.mapping.files or key in self.allowed:
                args.append(arg)
        config.args[:] = args

    def _key(self, path):
        try:
            return Path(str(path)).resolve().relative_to(self.mapping.root).as_posix()
        except ValueError:
            return None

    def pytest_ignore_collect(self, collection_path, config):
        key = self._key(collection_path)
        if key in self.mapping.files and key not in self.allowed:
            return True
        return None

    @pytest.hookimpl(tryfirst=True)
    def pytest_collection_modifyitems(self, config, items):
        kept, excluded = [], []
        for item in items:
            key = self._key(item.path)
            if key not in self.allowed:
                excluded.append(item)
            else:
                kept.append(item)
                self.collected.add(key)
        items[:] = kept
        if excluded:
            config.hook.pytest_deselected(items=excluded)

    def pytest_sessionfinish(self, session, exitstatus):
        if hasattr(session.config, "workeroutput"):
            session.config.workeroutput["flowgate_area_files"] = sorted(self.collected)

    @pytest.hookimpl(optionalhook=True)
    def pytest_testnodedown(self, node, error):
        self.collected.update((getattr(node, "workeroutput", None) or {}).get("flowgate_area_files", []))

    def pytest_terminal_summary(self, terminalreporter, exitstatus, config):
        terminalreporter.write_line(
            f"area: {','.join(sorted(self.selected))} | tier: {self.tier} | "
            f"selected {len(self.allowed)} files / excluded {len(self.mapping.files)-len(self.allowed)} files | "
            f"collected {len(self.collected)} files (before tier skips); not the full suite")


def register(config, root: Path) -> None:
    # No map I/O or collection changes at all for legacy invocations.
    if (getattr(config.option, "area", None) is not None
            or getattr(config.option, "changed", None) is not None):
        config.pluginmanager.register(AreaSelection(config, root), "flowgate-test-areas")
