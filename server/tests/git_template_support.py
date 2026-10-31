"""Worker-local Git seed templates. Product clone/worktree operations stay in tests."""
from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path


def _git_cmd(args, cwd):
    env = {**os.environ, "GIT_AUTHOR_NAME": "T", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "T", "GIT_COMMITTER_EMAIL": "t@t"}
    proc = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True, env=env)
    assert proc.returncode == 0, f"git {args}: {proc.stderr}"
    return proc.stdout.strip()


def seed_card_settings(tmp_path):
    """Original git_flow seed command sequence, before save_config/ensure_worktree."""
    bare, seed = tmp_path / "origin.git", tmp_path / "seed"
    _git_cmd(["init", "--bare", "-b", "main", str(bare)], tmp_path)
    _git_cmd(["init", "-b", "main", str(seed)], tmp_path)
    for n in (1, 2):
        (seed / f"f{n}.txt").write_text(f"{n}\n", encoding="utf-8")
        _git_cmd(["add", "-A"], seed)
        _git_cmd(["commit", "-m", f"c{n}"], seed)
    _git_cmd(["remote", "add", "origin", str(bare)], seed)
    _git_cmd(["push", "origin", "main", "main~1:refs/heads/old", "main:refs/heads/remote-only"], seed)
    _git_cmd(["checkout", "-b", "feature/v0.2.0"], seed)
    (seed / "v02.txt").write_text("v02\n", encoding="utf-8")
    _git_cmd(["add", "-A"], seed)
    _git_cmd(["commit", "-m", "v02"], seed)
    _git_cmd(["push", "origin", "feature/v0.2.0"], seed)
    return bare, seed


SHAPES = {"card-settings-v02": seed_card_settings}


class GitRepositoryFactory:
    """One immutable original per declared shape per pytest worker/session.

    Copy bytes (including objects), never clone, link, or register a worktree on
    the original. copyfile deliberately leaves copies writable. No run/session
    reuse and no fallback to direct mode after a template error.
    """

    def __init__(self, root, mode="template", shapes=None):
        if mode not in ("template", "direct"):
            raise ValueError(f"invalid Git mode: {mode}")
        self.root = Path(root)
        self.mode = mode
        self.shapes = dict(SHAPES if shapes is None else shapes)
        self._originals = {}
        self._errors = {}

    def _original(self, shape):
        if shape in self._errors:
            raise RuntimeError(f"Git template {shape} failed") from self._errors[shape]
        if shape not in self._originals:
            root = self.root / f"shape-{len(self._originals) + len(self._errors)}"
            try:
                root.mkdir(parents=True)
                bare, seed = self.shapes[shape](root)
                assert bare.is_dir() and (seed / ".git").is_dir()
                for gitdir in (bare, seed / ".git"):
                    assert not (gitdir / "worktrees").exists(), "original has worktrees"
                    assert not (gitdir / "objects/info/alternates").exists(), "shared objects"
                for path in root.rglob("*"):
                    assert not path.is_symlink(), "template must contain independent files"
                    if path.is_file():
                        path.chmod(stat.S_IREAD | stat.S_IRGRP | stat.S_IROTH)
            except Exception as error:
                self._errors[shape] = error
                raise
            self._originals[shape] = (bare, seed)
        return self._originals[shape]

    def __call__(self, shape, destination):
        builder = self.shapes[shape]  # undeclared shapes fail before creating anything
        destination = Path(destination)
        if self.mode == "direct":
            return builder(destination)
        original_bare, original_seed = self._original(shape)
        bare, seed = destination / "origin.git", destination / "seed"
        shutil.copytree(original_bare, bare, copy_function=shutil.copyfile)
        shutil.copytree(original_seed, seed, copy_function=shutil.copyfile)
        _git_cmd(["remote", "set-url", "origin", str(bare)], seed)
        return bare, seed
