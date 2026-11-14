"""v02gui.0015 T4 (0006-NR §6-4): the integration gate's syntax check of an attempt's result commit.

Before this, nothing between a card's approval and the merge into the group branch looked at the result itself: a
result whose files no longer parse (the 0005 incident: masked ``***`` text written back into Python files) was merged
as soon as a person or the automatic approval (``review_stage._after_work`` -> ``legacy.approve``) approved it.

Every approval path of a worktree attempt reaches ``result_merge.integrate`` (``defer_done``), and the result commit
is made there (``result_commit.commit_result``), so the gate sits in ``integrate`` between the commit and the merge:
``check`` judges the files the attempt changed (``input_commit``..``result_commit``, added or modified) and a failure
leaves the attempt ``failed`` (``verification_failed``) — nothing is merged, the node stays in review and nothing below
it opens, whichever approval it had.

What is checked — the post-write check of the remote tools (T5, ``remote_write_guard.post_write_check``) on the
committed bytes (``git cat-file``, never the worktree):

- ``.py`` (``compile`` to an AST) and ``.json`` (``json.loads``); other files are not judged.
- Only what the attempt broke blocks. A file that failed the same check at ``input_commit`` already is reported as
  ``preexisting`` and passes (templates, non-UTF-8 sources, JSON with comments that were there before).
- A new ``.py`` file that does not compile blocks. A new ``.json`` file that does not parse is only a warning: JSON
  with comments (``tsconfig.json`` and the like) cannot be told from a broken file and no person could let it through.
- Files above ``remote_write_guard.CHECK_LIMIT_BYTES`` and changes beyond ``MAX_FILES`` checked files are skipped.

Reading the commits is never a reason to block (as T3's unreadable answers): when Git cannot list or read them the
verdict is ``unavailable`` and the integration goes on — the merge of an unreadable result fails on its own.

The failure details (path, checker, line, short message; at most ``MAX_REPORTED`` entries, never source text) go on
the attempt's integration run event (``verification``).
"""
from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path
from typing import Optional

from modules.flow_gate.services import remote_write_guard as guard

log = logging.getLogger(__name__)

PASSED, FAILED, UNAVAILABLE = "passed", "failed", "unavailable"
REASON = "verification_failed"
CHECKED_SUFFIXES = (".py", ".json")
BLOCKING_NEW_SUFFIXES = (".py",)              # a new file of these kinds that does not parse blocks
MAX_FILES = 500
MAX_REPORTED = 20
GIT_TIMEOUT_SEC = 60


def _git(args: list[str], root: Path, data: Optional[bytes] = None) -> Optional[bytes]:
    """Raw stdout of a read-only git command, None on any failure."""
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    try:
        proc = subprocess.run(["git", *args], cwd=str(root), input=data, capture_output=True, env=env,
                              timeout=GIT_TIMEOUT_SEC)
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return None
    return proc.stdout if proc.returncode == 0 else None


def changed_files(root: Path, base: str, result: str) -> Optional[list[str]]:
    """Paths added or modified from ``base`` to ``result`` (a rename counts as an addition), None when unreadable."""
    out = _git(["diff", "--name-only", "-z", "--no-renames", "--diff-filter=AM", "--no-ext-diff", "--no-textconv",
                base, result, "--"], root)
    if out is None:
        return None
    return [p.decode("utf-8", "surrogateescape") for p in out.split(b"\0") if p]


def read_blobs(root: Path, specs: list[str]) -> Optional[list[Optional[bytes]]]:
    """``git cat-file --batch`` of ``<rev>:<path>`` specs, in order (None for a missing object); None when unreadable."""
    if not specs:
        return []
    request = b"".join(s.encode("utf-8", "surrogateescape") + b"\n" for s in specs)
    out = _git(["cat-file", "--batch"], root, request)
    if out is None:
        return None
    blobs: list[Optional[bytes]] = []
    pos = 0
    for _spec in specs:
        end = out.find(b"\n", pos)
        if end < 0:
            return None
        line = out[pos:end]
        pos = end + 1
        if line.endswith((b" missing", b" ambiguous")):          # "<spec> missing": the spec may hold spaces
            blobs.append(None)
            continue
        header = line.rsplit(b" ", 2)                           # "<oid> <type> <size>"
        if len(header) != 3 or not header[2].isdigit():
            return None
        size = int(header[2])
        blobs.append(out[pos:pos + size] if header[1] == b"blob" else None)   # a tree at that path: not a file
        pos += size + 1                                         # the content and its trailing newline
    return blobs


def _broken(path: str, data: Optional[bytes]) -> Optional[dict]:
    if data is None:
        return None
    report = guard.post_write_check(path, data, "utf-8")
    return report if report and report.get("status") == "syntax_error" else None


def check(root: Optional[Path], base: Optional[str], result: Optional[str]) -> dict:
    """The verdict of one result commit: ``status`` (``passed`` / ``failed`` / ``unavailable``), ``checked``,
    ``failures`` / ``warnings`` (clipped), ``failure_count``, ``preexisting``, ``skipped``."""
    verdict = {"status": PASSED, "checked": 0, "failures": [], "failure_count": 0, "warnings": [],
               "preexisting": 0, "skipped": 0}
    if root is None or not base or not result or base == result:
        return {**verdict, "status": UNAVAILABLE if root is None or not base or not result else PASSED}
    paths = changed_files(root, base, result)
    if paths is None:
        return {**verdict, "status": UNAVAILABLE}
    paths = [p for p in paths if Path(p).suffix.lower() in CHECKED_SUFFIXES]
    if any("\n" in p for p in paths):                           # not expressible in a --batch request line
        verdict["skipped"] += sum(1 for p in paths if "\n" in p)
        paths = [p for p in paths if "\n" not in p]
    if len(paths) > MAX_FILES:
        verdict["skipped"] += len(paths) - MAX_FILES
        paths = paths[:MAX_FILES]
    blobs = read_blobs(root, [f"{result}:{p}" for p in paths])
    if blobs is None:
        return {**verdict, "status": UNAVAILABLE}
    broken = []
    for path, data in zip(paths, blobs):
        if data is None:
            continue
        verdict["checked"] += 1
        if len(data) > guard.CHECK_LIMIT_BYTES:
            verdict["skipped"] += 1
            continue
        report = _broken(path, data)
        if report:
            broken.append((path, report))
    if not broken:
        return verdict
    before = read_blobs(root, [f"{base}:{p}" for p, _r in broken])
    if before is None:
        return {**verdict, "status": UNAVAILABLE}
    failures, warnings = [], []
    for (path, report), old in zip(broken, before):
        entry = {"path": path, "checker": report.get("checker"), "line": report.get("line"),
                 "message": str(report.get("message") or "")[:200]}
        if old is not None:
            if _broken(path, old):
                verdict["preexisting"] += 1                     # it did not parse before this attempt either
            else:
                failures.append(entry)                          # the attempt broke it
        elif Path(path).suffix.lower() in BLOCKING_NEW_SUFFIXES:
            failures.append(entry)
        else:
            warnings.append(entry)
    verdict.update(failures=failures[:MAX_REPORTED], failure_count=len(failures), warnings=warnings[:MAX_REPORTED],
                   status=FAILED if failures else PASSED)
    return verdict
