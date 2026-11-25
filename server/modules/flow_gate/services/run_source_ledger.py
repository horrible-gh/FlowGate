"""Which source changes belong to ONE chat AI run.

A group worktree is shared: card runs, other chat runs and people change files in it
while a chat run is working. Comparing the whole worktree at the run's start and end
therefore reports everybody's changes as that run's. This module records instead the
writes the run itself made, at the two places a chat run can change a file:

* a remote file tool call (write / patch / move / remove) made with the run's token --
  the content right before and right after that one call;
* a command the run executed -- the worktree right before and right after that one
  command, minus the paths another actor wrote through a file tool meanwhile.

Per path the ledger keeps ``base`` (the content before the run's first own write) and
``synth`` (``base`` plus the run's own writes only). When somebody else changed the same
file between two of the run's writes, the later write is replayed on ``synth`` with a
three-way merge, so the other actor's lines stay out of the run's change.

The ledger is process-local, like the run record it belongs to. Nothing here raises
into a file tool call or a command: a failure only drops the run change.
"""
from __future__ import annotations

import contextlib
import logging
import shutil
import tempfile
import threading
from pathlib import Path
from typing import Iterable, Iterator, Optional

logger = logging.getLogger(__name__)

GIT_TIMEOUT_SEC = 120
MAX_OPEN_LEDGERS = 256
_CHUNK = 50

# One side of a path: None (the file does not exist), ("bytes", content) read from the
# worktree, or ("oid", blob id) taken from a snapshot tree.
Side = Optional[tuple]

_lock = threading.RLock()
_ledgers: dict[str, "_Ledger"] = {}
_windows: list["_Window"] = []


class _Ledger:
    def __init__(self, run_id: str, root: Path):
        self.run_id = run_id
        self.root = root
        self.entries: dict[str, list] = {}      # path -> [base, synth]


class _Window:
    """One command of a run, from right before its process exists until it ended."""

    def __init__(self, run_id: str, root: Path, tree: str):
        self.run_id = run_id
        self.root = root
        self.tree = tree
        self.foreign: set[str] = set()
        self.closed = False


def _git(root: Path, args: list[str], index_file: Optional[str] = None):
    from modules.flow_gate.services import git_service

    extra = {"GIT_INDEX_FILE": index_file} if index_file else None
    return git_service._run_git(args, cwd=root, timeout=GIT_TIMEOUT_SEC, extra_env=extra)


def _capture(root: Path) -> Optional[str]:
    from modules.flow_gate.services import chat_run_changes_service

    return chat_run_changes_service.capture_tree(root)


def _resolved(root) -> Optional[Path]:
    try:
        return Path(root).resolve()
    except (OSError, RuntimeError, TypeError):
        return None


def _relative(root: Path, path: object) -> Optional[str]:
    if not isinstance(path, str) or not path:
        return None
    try:
        rel = (root / path).resolve().relative_to(root).as_posix()
    except (OSError, RuntimeError, ValueError):
        return None
    return rel if rel and rel != "." else None


def _read(root: Path, rel: str) -> Side:
    target = root / rel
    try:
        if target.is_symlink() or not target.is_file():
            return None
        return ("bytes", target.read_bytes())
    except OSError:
        return None


def _same(left: Side, right: Side) -> bool:
    if left is None or right is None:
        return left is None and right is None
    return left[0] == right[0] and left[1] == right[1]


# ── run lifetime ─────────────────────────────────────────────────────────────

def active() -> bool:
    """Whether any run is being recorded (the file tools' cheap early exit)."""
    return bool(_ledgers)


def open_run(run_id: str, root) -> bool:
    """Start recording ``run_id``'s own writes in the worktree ``root``."""
    resolved = _resolved(root)
    if not run_id or resolved is None:
        return False
    with _lock:
        _ledgers.pop(run_id, None)
        _ledgers[run_id] = _Ledger(run_id, resolved)
        while len(_ledgers) > MAX_OPEN_LEDGERS:     # runs that never reached finalize
            _ledgers.pop(next(iter(_ledgers)))
    return True


def is_open(run_id: Optional[str]) -> bool:
    with _lock:
        return bool(run_id) and run_id in _ledgers


def discard_run(run_id: Optional[str]) -> None:
    with _lock:
        _ledgers.pop(run_id or "", None)


def reset() -> None:
    """Forget everything (tests)."""
    with _lock:
        _ledgers.clear()
        del _windows[:]


# ── recording ────────────────────────────────────────────────────────────────

def _merge(ours: Side, base: Side, theirs: Side) -> Side:
    """``ours`` plus the change ``base`` -> ``theirs``. Falls back to ``theirs``."""
    sides = (ours, base, theirs)
    if any(side is None or side[0] != "bytes" for side in sides):
        return theirs
    tmpdir = tempfile.mkdtemp(prefix="flowgate-run-merge-")
    try:
        names = []
        for name, side in zip(("ours", "base", "theirs"), sides):
            target = Path(tmpdir) / name
            target.write_bytes(side[1])
            names.append(str(target))
        # Without -p the result replaces the first file, so no text decoding touches it.
        merged = _git(Path(tmpdir), ["merge-file", "--theirs", *names])
        if merged.returncode != 0:
            return theirs
        return ("bytes", (Path(tmpdir) / "ours").read_bytes())
    except Exception:
        logger.warning("run source ledger merge failed", exc_info=True)
        return theirs
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def record(run_id: str, path: str, before: Side, after: Side) -> None:
    """One own write of ``run_id`` to ``path``: its content right before and right after."""
    with _lock:
        ledger = _ledgers.get(run_id)
        if ledger is None:
            return
        entry = ledger.entries.get(path)
        if entry is None:
            if not _same(before, after):
                ledger.entries[path] = [before, after]
            return
        synth = entry[1]
        if _same(after, synth):
            return
        if _same(before, synth):
            entry[1] = after
            return
    # Somebody else changed the file since the run's last own write: replay only the
    # run's change. The merge runs a process, so it stays outside the lock.
    merged = _merge(synth, before, after)
    with _lock:
        ledger = _ledgers.get(run_id)
        entry = ledger.entries.get(path) if ledger is not None else None
        if entry is not None:
            entry[1] = merged


@contextlib.contextmanager
def recording(token: Optional[dict], root, paths: Iterable[object]) -> Iterator[None]:
    """Wrap ONE mutating file tool call made with ``token`` on ``paths`` under ``root``.

    The call is recorded for the token's chat run when that run is being recorded in
    this worktree; for every other run with a command in flight here the paths are
    noted as written by somebody else.
    """
    run_id: Optional[str] = None
    resolved: Optional[Path] = None
    rels: list[str] = []
    befores: dict[str, Side] = {}
    try:
        if _ledgers or _windows:
            resolved = _resolved(root)
            if resolved is not None:
                rels = [rel for rel in (_relative(resolved, path) for path in paths) if rel]
                candidate = str((token or {}).get("ai_run_id") or "")
                with _lock:
                    ledger = _ledgers.get(candidate)
                    if ledger is not None and ledger.root == resolved:
                        run_id = candidate
                if run_id:
                    befores = {rel: _read(resolved, rel) for rel in rels}
    except Exception:
        logger.warning("run source ledger: before-read failed (write not recorded)", exc_info=True)
        run_id = None
    try:
        yield
    finally:
        try:
            if rels and resolved is not None:
                with _lock:
                    for window in _windows:
                        if window.root == resolved and window.run_id != run_id:
                            window.foreign.update(rels)
                if run_id:
                    for rel in rels:
                        record(run_id, rel, befores.get(rel), _read(resolved, rel))
        except Exception:
            logger.warning("run source ledger: after-read failed (write not recorded)", exc_info=True)


# ── commands ─────────────────────────────────────────────────────────────────

def begin_command(run_id: Optional[str]) -> Optional[_Window]:
    """Right before a command of ``run_id`` starts. None when the run is not recorded."""
    try:
        with _lock:
            ledger = _ledgers.get(run_id or "")
        if ledger is None:
            return None
        tree = _capture(ledger.root)
        if not tree:
            return None
        window = _Window(ledger.run_id, ledger.root, tree)
        with _lock:
            _windows.append(window)
        return window
    except Exception:
        logger.warning("run source ledger: command start snapshot failed", exc_info=True)
        return None


def _raw_diff(root: Path, start_tree: str, end_tree: str) -> list[tuple[str, Optional[str], Optional[str]]]:
    proc = _git(root, ["diff", "--raw", "--no-renames", "--no-abbrev", "-z", start_tree, end_tree, "--"])
    if proc.returncode != 0:
        raise RuntimeError(f"diff --raw failed: {(proc.stderr or '')[:200]}")
    fields = (proc.stdout or "").split("\0")
    changes = []
    for index in range(0, len(fields) - 1, 2):
        meta, path = fields[index].split(), fields[index + 1]
        if len(meta) < 5 or not path:
            continue
        old, new = meta[2], meta[3]
        changes.append((path, None if not old.strip("0") else old, None if not new.strip("0") else new))
    return changes


def end_command(window: Optional[_Window]) -> None:
    """Right after the command ended: what it changed is the run's own. Idempotent."""
    if window is None or window.closed:
        return
    window.closed = True
    tree = None
    try:
        tree = _capture(window.root)
    except Exception:
        logger.warning("run source ledger: command end snapshot failed", exc_info=True)
    finally:
        with _lock:
            if window in _windows:
                _windows.remove(window)
            foreign = set(window.foreign)
    if not tree or tree == window.tree:
        return
    try:
        for path, old, new in _raw_diff(window.root, window.tree, tree):
            if path in foreign:
                continue
            record(window.run_id, path, ("oid", old) if old else None, ("oid", new) if new else None)
    except Exception:
        logger.warning("run source ledger: command change read failed", exc_info=True)


# ── the run's change as two trees ────────────────────────────────────────────

def _ignored(root: Path, paths: list[str]) -> set[str]:
    """Paths Git ignores (untracked + excluded), as the group change list leaves them out."""
    ignored: set[str] = set()
    for start in range(0, len(paths), _CHUNK):
        proc = _git(root, ["check-ignore", "-z", "--", *paths[start:start + _CHUNK]])
        if proc.returncode == 0:
            ignored.update(item for item in (proc.stdout or "").split("\0") if item)
    return ignored


def _blob(root: Path, tmpdir: str, path: str, side: Side) -> Optional[str]:
    if side is None:
        return None
    if side[0] == "oid":
        return side[1]
    spool = Path(tmpdir) / "blob"
    spool.write_bytes(side[1])
    # --path: the same clean filters `git add` applies to that path in a snapshot.
    proc = _git(root, ["hash-object", "-w", f"--path={path}", str(spool)])
    oid = (proc.stdout or "").strip()
    if proc.returncode != 0 or not oid:
        raise RuntimeError(f"hash-object failed: {(proc.stderr or '')[:200]}")
    return oid


def _tree(root: Path, tmpdir: str, name: str, items: list[tuple[str, str]]) -> str:
    index_file = str(Path(tmpdir) / f"index-{name}")
    for start in range(0, len(items), _CHUNK):
        args = ["update-index", "--add"]
        for path, oid in items[start:start + _CHUNK]:
            args += ["--cacheinfo", f"100644,{oid},{path}"]
        proc = _git(root, args, index_file=index_file)
        if proc.returncode != 0:
            raise RuntimeError(f"update-index failed: {(proc.stderr or '')[:200]}")
    proc = _git(root, ["write-tree"], index_file=index_file)   # no index file = the empty tree
    oid = (proc.stdout or "").strip()
    if proc.returncode != 0 or not oid:
        raise RuntimeError(f"write-tree failed: {(proc.stderr or '')[:200]}")
    return oid


def close_run(run_id: Optional[str]) -> Optional[tuple[str, str]]:
    """End the recording. (start tree, end tree) holding ONLY the paths the run changed,
    before and after its own writes -- or None when it changed nothing.

    Raises when the trees cannot be written; the ledger is gone either way.
    """
    with _lock:
        ledger = _ledgers.pop(run_id or "", None)
    if ledger is None or not ledger.entries:
        return None
    root = ledger.root
    tmpdir = tempfile.mkdtemp(prefix="flowgate-run-ledger-")
    try:
        ignored = _ignored(root, sorted(ledger.entries))
        before: list[tuple[str, str]] = []
        after: list[tuple[str, str]] = []
        for path in sorted(ledger.entries):
            if path in ignored:
                continue
            base, synth = ledger.entries[path]
            old, new = _blob(root, tmpdir, path, base), _blob(root, tmpdir, path, synth)
            if old == new:
                continue
            if old:
                before.append((path, old))
            if new:
                after.append((path, new))
        if not before and not after:
            return None
        return _tree(root, tmpdir, "start", before), _tree(root, tmpdir, "end", after)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
