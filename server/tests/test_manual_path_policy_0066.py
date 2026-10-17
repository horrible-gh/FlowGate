"""0066 D#1 manual path policy matrix and fail-closed ownership."""
import pytest

from modules.flow_gate.services import tr2_file_policy
from modules.flow_gate.services.git import manual_path_policy as p
from modules.flow_gate.services.git.credentials import GitServiceError


@pytest.mark.parametrize("path,in_head,staged,kind,visible,stage,revert,remove", [
    ("src/file.py", True, "M", "normal", True, True, True, False),
    ("src/new.py", False, None, "normal", True, True, False, True),
    ("src/new.py", False, "A", "normal", True, True, False, True),
    (".gitattributes", True, "M", "normal", True, True, True, False),
    ("src/.gitignore", True, "D", "normal", True, True, True, False),
    ("run.log", True, "D", "excluded", True, True, True, False),
    ("run.log", False, None, "excluded", False, False, False, False),
    ("run.log", False, "A", "excluded", True, False, False, False),
    ("server/.test-tmp-1/a.py", False, None, "excluded", False, False, False, False),
    ("managed.py", True, "D", "managed", True, False, False, False),
    ("managed.py", False, "A", "managed", True, False, False, False),
    ("server/.env.local", True, "M", "managed", False, False, False, False),
    ("server/.secret", False, "A", "normal", False, False, False, False),
])
def test_matrix(path, in_head, staged, kind, visible, stage, revert, remove):
    row = p.classify(path, in_head=in_head, staged=staged, unstaged=None,
                     untracked=not in_head and staged is None,
                     ownership=p.Ownership(frozenset({"managed.py", "server/.env.local"})))
    assert (row["class"], row["visible"], row["can_stage"], row["can_revert"],
            row["can_remove"]) == (kind, visible, stage, revert, remove)
    assert row["can_unstage"] == bool(staged and not p.hidden(path))


@pytest.mark.parametrize("staged,unstaged,reason", [("T", None, "type_change"),
                                                   (None, "T", "type_change"),
                                                   ("U", "M", "unmerged")])
def test_unsupported(staged, unstaged, reason):
    row = p.classify("a.py", in_head=True, staged=staged, unstaged=unstaged,
                     untracked=False, ownership=p.Ownership(frozenset()))
    assert row["unsupported"] == reason
    assert not any(row[k] for k in ("can_stage", "can_revert", "can_remove"))
    assert row["can_unstage"] == bool(staged)


@pytest.mark.parametrize("error,reason,code", [
    (tr2_file_policy.Tr2FilePolicyError(tr2_file_policy.TR_HISTORY_RECOVERY_REQUIRED),
     "tr_history_recovery_required", "tr_history_recovery_required"),
    (tr2_file_policy.Tr2OwnershipInvariantError("secret ledger details"),
     "ownership_invariant", "ownership_unavailable"),
    (RuntimeError("secret path"), "ownership_read_failed", "ownership_unavailable"),
])
def test_ownership_errors_never_become_empty_set(monkeypatch, error, reason, code):
    def boom(_):
        raise error
    monkeypatch.setattr(tr2_file_policy, "managed_paths", boom)
    owner = p.read_ownership("p.m.1")
    assert owner.paths is None and owner.view() == {"available": False, "reason": reason}
    with pytest.raises(GitServiceError) as caught:
        owner.require()
    assert caught.value.code == code and caught.value.details == {"reason": reason}
    for path in ("src/a.py", "server/.env.local"):
        row = p.classify(path, in_head=True, staged="M", unstaged=None,
                         untracked=False, ownership=owner)
        assert row["class"] == "unverified" and not row["can_stage"]
        assert row["can_unstage"] == (not p.hidden(path))
        assert row["visible"] == (not p.hidden(path))


def test_redact_hidden_even_when_owned():
    assert p.redact_diagnostic("server/.env.local and src/a.py", ["server/.env.local", "src/a.py"]) == (
        "<hidden> and src/a.py")
    assert p.visible_paths(["server/.env.local", "src/a.py"]) == ["src/a.py"]


@pytest.mark.parametrize("path", ["../a", "/a", r"C:\a", r"\\server\share\a", "a/../b",
                                  ".", "a/", "a//b", "a/\0b"])
def test_invalid_paths(path):
    with pytest.raises(GitServiceError) as caught:
        p.normalize_paths([path], False)
    assert caught.value.status == 422 and caught.value.details["reason"] == "invalid_path"


@pytest.mark.parametrize("paths,all_paths", [(None, False), ([], False), ([], True), (["a"], True)])
def test_exactly_one_selection(paths, all_paths):
    with pytest.raises(GitServiceError) as caught:
        p.normalize_paths(paths, all_paths)
    assert caught.value.status == 422


def test_path_limit_boundaries_and_duplicate_count():
    assert len(p.normalize_paths([f"{i}.py" for i in range(4096)], False)) == 4096
    assert p.normalize_paths(["a" * 4096], False) == ["a" * 4096]
    assert p.normalize_paths(["a"] * 4096, False) == ["a"]
