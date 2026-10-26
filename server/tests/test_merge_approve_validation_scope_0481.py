"""flowgate.default.0481 T0010 rev3 — "머지는 되지도 않음" (2026-09-08 10:33).

Approval validated the WHOLE merge candidate with the WRITE PLAN's rule, so any
merge carrying a file type with no registered syntax validator (`.md`, `.txt`,
`.lock`, `.png` and — the reviewer's actual case — `.tsbuildinfo`) could only ever
answer `pre_commit_validation_failed`. Pressing [승인] never merged, and no retry
could change that.

L0007 §2.7 scopes that rule to "변경되거나 생성된 모든 **plan** 대상 파일"
(`syntax_validation_scope`), which is the write-plan path — and the apply gate there
already refuses such a plan, so a plan-written file with an unregistered extension
can never reach a candidate. Approval therefore skips the verdict it has no
validator for; every other check (UTF-8, conflict markers, and every registered
validator) still runs on every path.

These run against a REAL git repository, so the blobs the validator reads are real
git objects and the errors are the ones an approval would actually produce.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

os.environ["TESTING"] = "1"
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")
os.environ.setdefault("ALLOWED_ORIGIN", "http://localhost")
os.environ.setdefault("CONTEXT", "/flowgate")
os.environ.setdefault("DB_TYPE", "sqlite")

_SERVER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SERVER_DIR))

from group_lock_stub import group_store  # noqa: E402,F401
from modules.flow_gate.services import git_service  # noqa: E402

_GIT = shutil.which("git") is not None
needs_git = pytest.mark.skipif(not _GIT, reason="git binary unavailable")
needs_node_syntax = pytest.mark.skipif(
    not (Path(git_service._FLOWGATE_CLIENT_DIR) / "node_modules" / "typescript").is_dir(),
    reason="client/node_modules (real css/ts parsers) unavailable in this checkout",
)

_IDENT = ["-c", "user.name=T", "-c", "user.email=t@t"]


@pytest.fixture
def repo(tmp_path_factory):
    """A real repository whose HEAD tree is the candidate under validation."""
    root = Path(tempfile.mkdtemp(prefix="fg-approve-scope-", dir=str(tmp_path_factory.mktemp("r"))))
    subprocess.run(["git", "init", "-b", "main", str(root)], capture_output=True, check=True)
    yield root
    shutil.rmtree(root, ignore_errors=True)


def _commit(root: Path, files: dict[str, str]) -> None:
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=str(root), capture_output=True, check=True)
    subprocess.run(
        ["git", *_IDENT, "commit", "-m", "candidate"],
        cwd=str(root), capture_output=True, check=True,
    )


def _context(root: Path, paths: list[str]) -> dict:
    """The two context keys the validator reads, built from the real HEAD tree."""
    listing = subprocess.run(
        ["git", "ls-tree", "-r", "HEAD"],
        cwd=str(root), capture_output=True, text=True, check=True,
    ).stdout.splitlines()
    manifest = []
    for line in listing:
        meta, path = line.split("\t", 1)
        mode, kind, oid = meta.split()
        manifest.append({"path": path, "mode": mode, "kind": kind, "oid": oid})
    return {
        "snapshot_manifest": manifest,
        "changes": [{"path": p, "status": "M", "old_path": None} for p in paths],
    }


@needs_git
@pytest.mark.parametrize("path", [
    "tsconfig.app.tsbuildinfo",   # the reviewer's own merge, 2026-09-08 10:24
    "README.md",
    "notes.txt",
    "package-lock.json.lock",
    "LICENSE",                    # no extension at all
])
def test_approval_does_not_veto_a_file_type_it_has_no_validator_for(repo, path):
    _commit(repo, {path: "anything at all\n"})
    context = _context(repo, [path])

    assert git_service._validate_review_changed_paths(
        repo, context, unregistered_extension="skip",
    ) == []

    # Control: the write-plan rule is unchanged, and IS what used to run here.
    rejected = git_service._validate_review_changed_paths(
        repo, context, unregistered_extension="reject",
    )
    assert [e["validator"] for e in rejected] == ["unsupported"]


@needs_git
def test_skip_still_refuses_leftover_conflict_markers(repo):
    marked = "a\n<<<<<<< HEAD\nours\n=======\ntheirs\n>>>>>>> group\nb\n"
    _commit(repo, {"README.md": marked})

    errors = git_service._validate_review_changed_paths(
        repo, _context(repo, ["README.md"]), unregistered_extension="skip",
    )

    assert [e["validator"] for e in errors] == ["conflict_marker"]


@needs_git
def test_skip_still_runs_every_registered_validator(repo):
    _commit(repo, {"broken.json": "{ this is not json ", "fine.json": '{"ok": true}\n'})

    errors = git_service._validate_review_changed_paths(
        repo, _context(repo, ["broken.json", "fine.json"]), unregistered_extension="skip",
    )

    assert [(e["path"], e["validator"]) for e in errors] == [("broken.json", "json")]


@needs_git
@needs_node_syntax
def test_a_mixed_candidate_is_judged_only_on_the_files_that_have_a_validator(repo):
    # The exact shape of the reviewer's merge: two source files plus two build
    # artefacts. Only a real syntax error may stop it.
    _commit(repo, {
        "src/app.json": '{"ok": true}\n',
        "src/index.css": "a { color: red; }\n",
        "tsconfig.app.tsbuildinfo": '{"program":{}}\n',
        "docs/CHANGELOG.md": "# changes\n",
    })
    paths = ["src/app.json", "src/index.css", "tsconfig.app.tsbuildinfo", "docs/CHANGELOG.md"]

    assert git_service._validate_review_changed_paths(
        repo, _context(repo, paths), unregistered_extension="skip",
    ) == []


def test_the_mode_is_spelled_out_rather_than_silently_defaulted():
    # A typo'd mode must not fall through to "permit everything".
    with pytest.raises(ValueError):
        git_service._validate_review_changed_paths(
            Path("."), {"changes": [], "snapshot_manifest": []},
            unregistered_extension="ignore",
        )


def test_approve_asks_for_the_merge_rule_not_the_write_plan_rule(monkeypatch, group_store):
    """The call site is the whole fix — pin which mode approval passes.

    `approve_merge_review` is guarded by the session target's domain lock (real store), a session lookup and a
    fingerprint check before it reaches the validator; this drives it to exactly
    that point and stops there, so the assertion is about the argument and nothing
    else. The end-to-end proof that the merge now commits and pushes is the real
    approval run recorded in 0011-TR §단계별 확인.
    """
    seen: dict[str, str] = {}

    session = {"merge_id": 4, "group_id": "test2.default.0009", "kind": "merge"}
    context = {
        "review_state": "resolved_pending_review",
        "review_fingerprint": "fp-1",
        "base_head": "b" * 40,
        "changes": [],
        "snapshot_manifest": [],
    }

    monkeypatch.setattr(git_service.db_git, "get_session", lambda _m: session)
    monkeypatch.setattr(git_service.db_git, "session_kind", lambda _s: git_service.db_git.SESSION_KIND_MERGE)
    monkeypatch.setattr(git_service.db_git, "session_context", lambda _s: context)
    monkeypatch.setattr(git_service.db_git, "set_session_context", lambda *_a, **_k: None)
    monkeypatch.setattr(git_service.db_git, "get_config", lambda _p: {"base_branch": "main"})
    monkeypatch.setattr(git_service, "_project_of_group", lambda _g: "test2")
    monkeypatch.setattr(git_service, "_base_root_of", lambda _p: Path("."))
    monkeypatch.setattr(git_service, "_live_candidate_matches_snapshot", lambda *_a: True)

    def _spy(_base_root, _context, *, unregistered_extension="reject"):
        seen["mode"] = unregistered_extension
        # One synthetic error stops the function here, before commit/push.
        return [{"path": "x.py", "validator": "python", "line": 1, "message": "stop here"}]

    monkeypatch.setattr(git_service, "_validate_review_changed_paths", _spy)

    result = git_service.approve_merge_review(
        "test2.default.0009", 4,
        attempt_id="00000000-0000-4000-8000-000000000000",
        review_fingerprint="fp-1", authority="human",
    )

    assert seen["mode"] == "skip"
    assert result["result"]["status"] == "pre_commit_validation_failed"
    # An ordinary parser failure keeps its own code, not the unsupported one.
    assert context["last_error"]["code"] == "syntax_validation_failed"



# 0096 T0004 — frozen-parent inheritance and BOM regressions.
_BALANCED = 'fixture = """\n<<<<<<< HEAD\nours\n=======\ntheirs\n>>>>>>> group/branch\n"""\n'
_ASYMMETRIC = 'fixture = """<<<<<<< HEAD\nours\n=======\ntheirs\n>>>>>>> group/branch\n"""\n'
_PREFIX = ''.join(f'# before {i}\n' for i in range(25))
_SUFFIX = ''.join(f'# after {i}\n' for i in range(25))


def _validate_blobs(monkeypatch, root, candidate, parents=(), *, ext='py', status='M', mode='skip'):
    """Use exact blob bytes; mock only the Git object transport."""
    path = 'new.' + ext
    old = 'old.' + ext
    blobs = {'candidate': candidate.encode('utf-8')}
    entries = {}
    for i, text in enumerate(parents):
        if text is not None:
            oid = f'parent-{i}'
            blobs[oid] = text.encode('utf-8')
            entries[(f'ref-{i}', old if i == 0 and status == 'R' else path)] = ('blob', oid)
    monkeypatch.setattr(git_service, '_cat_file_size', lambda _r, oid: len(blobs[oid]))
    monkeypatch.setattr(git_service, '_cat_file_blob_head', lambda _r, oid, size: blobs[oid][:size])
    monkeypatch.setattr(git_service, '_ls_tree_entry', lambda _r, ref, p: entries.get((ref, p)))
    context = {
        'base_head': 'ref-0' if parents else None,
        'merge_head': 'ref-1' if len(parents) > 1 else None,
        'snapshot_manifest': [{'path': path, 'kind': 'blob', 'oid': 'candidate'}],
        'changes': [{'path': path, 'status': status, 'old_path': old if status == 'R' else None}],
    }
    return git_service._validate_review_changed_paths(root, context, unregistered_extension=mode)


@pytest.mark.parametrize('bom', ['', '\ufeff'])
@pytest.mark.parametrize('source,valid', [('answer = 42\n', True), ('def broken(:\n', False), ('return 1\n', False)])
def test_python_bytes_keep_bom_and_compile_validation(monkeypatch, tmp_path, bom, source, valid):
    errors = _validate_blobs(monkeypatch, tmp_path, bom + source)
    assert (errors == []) is valid
    if errors:
        assert errors[0]['validator'] == 'python'


@pytest.mark.parametrize('status', ['M', 'R'])
@pytest.mark.parametrize('fixture', [_BALANCED, _ASYMMETRIC, _ASYMMETRIC * 2])
def test_preserved_fixtures_align_after_unrelated_edits(monkeypatch, tmp_path, status, fixture):
    parent = _PREFIX + fixture + _SUFFIX
    candidate = '# inserted\n' + parent.replace('# after 12', '# edited after 12')
    assert _validate_blobs(monkeypatch, tmp_path, candidate, [parent], status=status) == []


@pytest.mark.parametrize('status', ['M', 'R', 'A'])
@pytest.mark.parametrize('ext', ['py', 'md', 'txt'])
@pytest.mark.parametrize('mutation', ['duplicate', 'relocate', 'other', 'open', 'close'])
def test_new_or_relocated_markers_are_refused(monkeypatch, tmp_path, status, ext, mutation):
    parent = _PREFIX + _BALANCED + _SUFFIX
    candidate = {
        'duplicate': parent + _BALANCED,
        'relocate': _PREFIX + _SUFFIX + _BALANCED,
        'other': parent + _BALANCED.replace('group/branch', 'other'),
        'open': parent + '<<<<<<< HEAD\n',
        'close': parent + '>>>>>>> group/branch\n',
    }[mutation]
    parents = [] if status == 'A' else [parent, parent]
    errors = _validate_blobs(monkeypatch, tmp_path, candidate, parents, ext=ext, status=status)
    assert [e['validator'] for e in errors] == ['conflict_marker']


@pytest.mark.parametrize('removed', ['<<<<<<< HEAD\n', '>>>>>>> group/branch\n'])
def test_deleting_half_of_balanced_block_increases_orphans(monkeypatch, tmp_path, removed):
    parent = _PREFIX + _BALANCED + _SUFFIX
    errors = _validate_blobs(monkeypatch, tmp_path, parent.replace(removed, ''), [parent, parent])
    assert [e['validator'] for e in errors] == ['conflict_marker']


def test_deleting_whole_block_or_reducing_inherited_asymmetry_is_allowed(monkeypatch, tmp_path):
    parent = _PREFIX + _BALANCED + _SUFFIX
    assert _validate_blobs(monkeypatch, tmp_path, _PREFIX + _SUFFIX, [parent]) == []
    parent = _PREFIX + _ASYMMETRIC * 2 + _SUFFIX
    assert _validate_blobs(monkeypatch, tmp_path, _PREFIX + _ASYMMETRIC + _SUFFIX, [parent]) == []


@pytest.mark.parametrize('failure', ['absent', 'lookup', 'size', 'read', 'truncated', 'encoding'])
def test_unavailable_parent_never_exempts_markers(monkeypatch, tmp_path, failure):
    def fail(*_a):
        raise git_service.GitServiceError(500, 'git_error', 'unavailable')
    # Install transport mocks, then inject the failure at the chosen boundary.
    original = git_service._validate_review_changed_paths
    def validate(root, context, **kwargs):
        if failure == 'absent':
            monkeypatch.setattr(git_service, '_ls_tree_entry', lambda *_a: None)
        elif failure == 'lookup':
            monkeypatch.setattr(git_service, '_ls_tree_entry', fail)
        elif failure == 'size':
            saved = git_service._cat_file_size
            monkeypatch.setattr(git_service, '_cat_file_size', lambda r, oid: fail() if oid.startswith('parent') else saved(r, oid))
        else:
            saved = git_service._cat_file_blob_head
            def read(r, oid, size):
                if not oid.startswith('parent'):
                    return saved(r, oid, size)
                if failure == 'read':
                    return fail()
                if failure == 'truncated':
                    return b''
                return b'\xff' * size
            monkeypatch.setattr(git_service, '_cat_file_blob_head', read)
        return original(root, context, **kwargs)
    monkeypatch.setattr(git_service, '_validate_review_changed_paths', validate)
    errors = _validate_blobs(monkeypatch, tmp_path, _ASYMMETRIC, [_ASYMMETRIC])
    assert [e['validator'] for e in errors] == ['conflict_marker']


@pytest.mark.parametrize('balanced', [True, False])
def test_each_parent_can_contribute_its_own_fixture(monkeypatch, tmp_path, balanced):
    a = _BALANCED if balanced else _ASYMMETRIC
    b = a.replace('fixture', 'second').replace('group/branch', 'group/second')
    middle = ''.join(f'# middle {i}\n' for i in range(25))
    left = _PREFIX + a + middle + _SUFFIX
    right = _PREFIX + middle + b + _SUFFIX
    candidate = _PREFIX + a + middle + b + _SUFFIX
    assert _validate_blobs(monkeypatch, tmp_path, candidate, [left, right]) == []
    errors = _validate_blobs(monkeypatch, tmp_path, candidate + '>>>>>>> unknown\n', [left, right])
    assert [e['validator'] for e in errors] == ['conflict_marker']


def test_write_plan_does_not_gain_an_inheritance_exemption(monkeypatch, tmp_path):
    errors = _validate_blobs(monkeypatch, tmp_path, _ASYMMETRIC, [_ASYMMETRIC], mode='reject')
    assert [e['validator'] for e in errors] == ['conflict_marker']


def test_json_bom_policy_is_unchanged(monkeypatch, tmp_path):
    errors = _validate_blobs(monkeypatch, tmp_path, '\ufeff{"ok": true}\n', ext='json')
    assert [e['validator'] for e in errors] == ['json']


@pytest.mark.parametrize('name,lines', [
    ('test_conflict_prompt_size_guard.py', [64]),
    ('test_zdiff3_conflict_mention_0478.py', [39, 72]),
])
@pytest.mark.parametrize('mutation', ['unchanged', 'unrelated', 'reduce', 'duplicate', 'newblock'])
def test_frozen_session_296_actual_marker_blobs(monkeypatch, tmp_path, name, lines, mutation):
    import json
    data = json.loads((Path(__file__).parent / 'fixtures/merge_review_0096_blobs.json').read_text(encoding='utf-8'))[name]
    candidate = data['a3e2c64d']
    assert [i for i, line in enumerate(candidate.splitlines(), 1) if git_service.has_conflict_markers(line)] == lines
    assert git_service._marker_orphans(candidate.splitlines()) == (0, len(lines))
    if mutation == 'unrelated':
        candidate += '\n# unrelated edit\n'
    elif mutation == 'reduce':
        parts = candidate.splitlines(keepends=True)
        # Preserve Python triple-quote closure while removing the marker itself.
        parts[lines[-1] - 1] = parts[lines[-1] - 1].replace('>>>>>>> group/branch', '')
        candidate = ''.join(parts)
    elif mutation == 'duplicate':
        candidate += '\n>>>>>>> group/branch\n'
    elif mutation == 'newblock':
        candidate += '\n' + _BALANCED
    errors = _validate_blobs(monkeypatch, tmp_path, candidate, [data['fb128ad1'], data['b489fc92']], status='R')
    assert [e['validator'] for e in errors] == (['conflict_marker'] if mutation in ('duplicate', 'newblock') else [])


@pytest.mark.parametrize('name', ['test_ts021_r015_r0_integration.py', 'test_process_service_next_actions.py'])
def test_frozen_session_296_actual_bom_blobs(monkeypatch, tmp_path, name):
    import json
    data = json.loads((Path(__file__).parent / 'fixtures/merge_review_0096_blobs.json').read_text(encoding='utf-8'))[name]['a3e2c64d']
    assert data.startswith('\ufeff')
    assert _validate_blobs(monkeypatch, tmp_path, data, status='R') == []


@needs_git
def test_real_git_rename_reads_both_frozen_parent_paths(repo):
    parent = _PREFIX + _ASYMMETRIC + _SUFFIX
    _commit(repo, {'old.py': parent})
    base = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=repo, capture_output=True, text=True, check=True).stdout.strip()
    (repo / 'old.py').unlink()
    _commit(repo, {'new.py': '# moved\n' + parent})
    merged = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=repo, capture_output=True, text=True, check=True).stdout.strip()
    context = _context(repo, ['new.py'])
    context.update(base_head=base, merge_head=merged)
    context['changes'] = [{'path': 'new.py', 'old_path': 'old.py', 'status': 'R'}]
    assert git_service._validate_review_changed_paths(repo, context, unregistered_extension='skip') == []



@pytest.mark.parametrize('status', ['M', 'R'])
@pytest.mark.parametrize('ext', ['py', 'md', 'txt'])
def test_long_relocated_fixture_cannot_be_the_diff_anchor(monkeypatch, tmp_path, status, ext):
    long_block = _BALANCED.replace('ours\n', ''.join(f'payload {i}\n' for i in range(60)))
    parent = '# before\n' + long_block + '# after\n'
    candidate = '# before\n# after\n' + long_block
    errors = _validate_blobs(monkeypatch, tmp_path, candidate, [parent, parent], status=status, ext=ext)
    assert [e['validator'] for e in errors] == ['conflict_marker']


@pytest.mark.parametrize('status', ['M', 'R', 'A'])
@pytest.mark.parametrize('ext', ['py', 'md', 'txt'])
def test_identical_block_duplication_without_parents_is_refused(monkeypatch, tmp_path, status, ext):
    errors = _validate_blobs(monkeypatch, tmp_path, _BALANCED * 2, [], status=status, ext=ext)
    assert [e['validator'] for e in errors] == ['conflict_marker']
