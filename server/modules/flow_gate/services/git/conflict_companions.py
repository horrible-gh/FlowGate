"""Verified target-side moves allowed as companions to a conflict resolution."""
from __future__ import annotations

import difflib
import re
from pathlib import Path, PurePosixPath


def candidates(root: Path, paths: set[str]) -> dict[str, list[str]]:
    """Derive relationships from committed Git trees, never submitted content.

    Accept Git-detected renames, or a relative re-export to a newly added regular
    file whose content is at least 60% identical to the original base file.
    Missing merge metadata and Git errors fail closed.
    """
    from modules.flow_gate.services import git_service as gs
    from modules.flow_gate.storage.safe_path import resolve_in_root

    def git(*args):
        p = gs._run_git(list(args), cwd=root)
        return p.stdout if p.returncode == 0 else None

    base = git("merge-base", "HEAD", "MERGE_HEAD")
    if not base:
        return {}
    base = base.strip()
    diff = git("diff", "--name-status", "-z", "--find-renames=60%", base, "HEAD", "--")
    if diff is None:
        return {}
    tokens = iter(diff.rstrip("\0").split("\0"))
    added, renames = set(), {}
    for status in tokens:
        old = next(tokens, "")
        if status.startswith("R"):
            new = next(tokens, "")
            renames[old] = new
            added.add(new)
        elif status == "A":
            added.add(old)
    result = {}
    for source in sorted(paths):
        original = git("show", f"{base}:{source}")
        if not original:
            continue
        possible = set()
        if source in renames:
            possible.add(renames[source])
        target_source = git("show", f"HEAD:{source}") or ""
        for spec in re.findall(r"export\s+[^;\n]*?from\s*['\"](\.[^'\"]+)['\"]", target_source):
            # Normalize only repository-derived module specifiers, never request paths.
            import posixpath
            stem = posixpath.normpath(str(PurePosixPath(source).parent / spec))
            possible.update(p for p in added if p in {stem, stem + ".ts", stem + ".tsx", stem + ".js", stem + ".jsx", stem + "/index.ts", stem + "/index.js"})
        accepted = []
        for dest in sorted(possible - paths):
            lexical = root / dest
            if any(p.is_symlink() for p in [lexical, *lexical.parents] if p != root and root in p.parents):
                continue
            safe = resolve_in_root(root, dest)
            if safe is None or not safe.is_file():
                continue
            mode = git("ls-tree", "HEAD", "--", dest) or ""
            if not mode.startswith(("100644 ", "100755 ")):
                continue
            moved = git("show", f"HEAD:{dest}")
            if moved and difflib.SequenceMatcher(None, original.splitlines(), moved.splitlines(), autojunk=False).ratio() >= 0.6:
                accepted.append(dest)
        if accepted:
            result[source] = accepted
    return result


def relocated_evidence(chunk: dict, side: str, companions: list[dict]):
    """Only a target-side selection may relocate the incoming side, verbatim.

    Require the entire dropped chunk, in order and contiguous, in ONE verified
    companion; isolated copies of added lines cannot excuse a discarded change.
    """
    if side != "ours":
        return None
    dropped = [line.strip() for line in chunk["theirs"]]
    # A lone common line is not enough evidence of a relocated chunk.
    if sum(bool(line) for line in dropped) < 2:
        return None
    for companion in companions:
        lines = [line.strip() for line in companion["content"].splitlines()]
        if any(lines[i:i + len(dropped)] == dropped for i in range(len(lines) - len(dropped) + 1)):
            return {"preserved": chunk["theirs"], "changed": [], "removed": [],
                    "companion_path": companion["path"]}
    return None
