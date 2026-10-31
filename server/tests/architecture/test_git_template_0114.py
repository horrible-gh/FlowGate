"""T#2: real Git topology, copy isolation, publication, and mode contracts."""
import hashlib
import stat

import pytest

from git_template_support import GitRepositoryFactory, _git_cmd, seed_card_settings


def _files(root):
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob("*") if p.is_file()}


def _topology(repo):
    refs = _git_cmd(["for-each-ref", "--format=%(refname)"], repo).splitlines()
    return {ref: (_git_cmd(["rev-parse", ref + "^{tree}"], repo),
                  _git_cmd(["log", "--format=%s", ref], repo)) for ref in refs}


def _destination(tmp_path, name):
    root = tmp_path / name
    root.mkdir()
    return root


def test_direct_and_template_have_identical_topology_and_configuration(tmp_path):
    direct = GitRepositoryFactory(tmp_path / "unused", mode="direct")
    factory = GitRepositoryFactory(tmp_path / "originals")
    db, ds = direct("card-settings-v02", _destination(tmp_path, "direct"))
    cb, cs = factory("card-settings-v02", _destination(tmp_path, "copy"))
    assert not direct.root.exists()
    assert _topology(db) == _topology(cb)
    assert _topology(ds) == _topology(cs)
    for seed, bare in ((ds, db), (cs, cb)):
        assert _git_cmd(["remote", "get-url", "origin"], seed) == str(bare)
        assert _git_cmd(["symbolic-ref", "HEAD"], seed) == "refs/heads/feature/v0.2.0"
        assert _git_cmd(["status", "--porcelain"], seed) == ""
        assert _git_cmd(["rev-list", "--count", "main"], seed) == "2"
        assert _git_cmd(["rev-list", "--count", "feature/v0.2.0"], seed) == "3"
        assert _git_cmd(["rev-parse", "main~1"], seed) == _git_cmd(["rev-parse", "refs/heads/old"], bare)
        assert _git_cmd(["config", "--get", "remote.origin.fetch"], seed) == "+refs/heads/*:refs/remotes/origin/*"


@pytest.mark.parametrize("sample", range(2))
def test_copies_and_original_stay_independent(sample, tmp_path, git_repositories):
    # The real session fixture is exercised under serial and xdist sessions.
    factory = git_repositories
    first = _destination(tmp_path, "first")
    second = _destination(tmp_path, "second")
    bare, seed = factory("card-settings-v02", first)
    other_bare, other_seed = factory("card-settings-v02", second)
    original = _files(factory.root)
    untouched = _files(second)
    assert not (seed / ".git/objects/info/alternates").exists()
    for rel in _files(seed / ".git/objects"):
        other = other_seed / ".git/objects" / rel
        assert not other.exists() or not (seed / ".git/objects" / rel).samefile(other)
    (seed / "v02.txt").write_text("changed\n", encoding="utf-8")
    _git_cmd(["add", "-A"], seed)
    _git_cmd(["commit", "-m", "copy-only"], seed)
    _git_cmd(["push", "origin", "HEAD:refs/heads/copy-only"], seed)
    _git_cmd(["worktree", "add", "-b", "copy-wt", str(tmp_path / "worktree")], seed)
    assert "refs/heads/copy-only" in _topology(bare)
    assert "refs/heads/copy-only" not in _topology(other_bare)
    assert _files(second) == untouched
    assert _files(factory.root) == original
    assert not list(factory.root.rglob("worktrees"))


def test_one_original_per_shape_but_direct_runs_builder_each_time(tmp_path):
    calls = []
    def build(root):
        calls.append(root)
        return seed_card_settings(root)
    template = GitRepositoryFactory(tmp_path / "originals", shapes={"shape": build})
    template("shape", _destination(tmp_path, "a"))
    template("shape", _destination(tmp_path, "b"))
    assert len(calls) == 1
    assert all(not p.stat().st_mode & stat.S_IWRITE
               for p in template.root.rglob("*") if p.is_file())
    direct = GitRepositoryFactory(tmp_path / "unused", "direct", {"shape": build})
    direct("shape", _destination(tmp_path, "c"))
    direct("shape", _destination(tmp_path, "d"))
    assert len(calls) == 3


def test_failed_original_is_never_published_or_retried(tmp_path):
    calls = []
    def broken(root):
        calls.append(root)
        (root / "partial").write_text("incomplete", encoding="utf-8")
        raise ValueError("seed failed")
    factory = GitRepositoryFactory(tmp_path / "originals", shapes={"broken": broken})
    with pytest.raises(ValueError, match="seed failed"):
        factory("broken", _destination(tmp_path, "a"))
    with pytest.raises(RuntimeError, match="Git template broken failed"):
        factory("broken", _destination(tmp_path, "b"))
    assert calls and len(calls) == 1
    assert not factory._originals
    assert not list((tmp_path / "a").iterdir())
    assert not list((tmp_path / "b").iterdir())


def test_fixture_uses_requested_mode(pytestconfig, git_repositories):
    assert git_repositories.mode == pytestconfig.getoption("--fg-git-mode")
