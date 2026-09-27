"""0608 T0005 — EOL-only conflicts and the tool-driven conflict mention.

NR0003 found the 0594 resolver prompt (1.8M chars) was 83% line-ending noise: 0599's merge
98 wrote its resolved files through a Windows text-mode ``write_text`` (6132cf58), the LF
i18n files came out CRLF, and every later merge touching them became one whole-file
conflict. This file pins the three fixes:

  1. a resolution is written in bytes, in the file's own line ending (``_path_eol``);
  2. a conflict that is only a CRLF/LF difference is merged on normalised text and taken
     out of the resolver's hands; a real conflict keeps only its real chunks
     (``separate_eol_conflicts`` / ``apply_eol_separation``);
  3. the conflict mention carries chunk LOCATIONS plus the Remote source section instead
     of pre-loading every chunk's text once the set is large.

TR0006 rejection fixes are pinned at the end: a failing ``git merge-file`` (any exit
outside the 0..127 conflict count) leaves the conflict and the index untouched; the
mention's read instruction never produces a start_line below 1 and a chunk on line 1
reads back through the real ``/remote/read`` pipeline; and ``.gitattributes`` appears in
the reviewed manifest and in the commit alike.

``TestReplay0594`` replays the real 0594 merge from this repository's own history (the
same inputs as NR0003 §1.2) and measures files / chunks / mention size. The connected
finalize → session → resolve-token path is in
test_git_integration_0115.py::TestGitEndToEnd::test_eol_only_conflict_is_separated_and_lf_survives_resolution_0608.
"""
from __future__ import annotations

import base64
import json
import os
import re
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
os.environ.setdefault("FLOWGATE_GIT_ENCRYPT_KEY", base64.b64encode(b"K" * 32).decode())
os.environ.setdefault("FLOWGATE_STORAGE_DIR", tempfile.mkdtemp(prefix="fg-eol-0608-"))

_SERVER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SERVER_DIR))

from modules.flow_gate.api import token_routes  # noqa: E402
from modules.flow_gate.services import git_service  # noqa: E402
from modules.flow_gate.services.git import conflict  # noqa: E402


def _source_repo() -> Path:
    """This checkout (read-only here: blobs and attributes). A function rather than a
    module-level path so the 0382 scratch guard does not trace every value computed from
    its git output — ``base_rev``, then the tmp ``repo`` — back to the repository."""
    return _SERVER_DIR.parent


_API = "http://127.0.0.1:8089/flowgate/api/v1"
_TOKEN = "vZT4wE0ek3RmMUXwber9Ni6gpZZ2SNdmBAqUQDLbuzc"   # real token length (43)


def _git(args, cwd, check=True):
    env = os.environ.copy()
    env.update({"GIT_AUTHOR_NAME": "T", "GIT_AUTHOR_EMAIL": "t@t",
                "GIT_COMMITTER_NAME": "T", "GIT_COMMITTER_EMAIL": "t@t"})
    proc = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, env=env)
    if check:
        assert proc.returncode == 0, (args, proc.stderr.decode("utf-8", "replace"))
    return proc


def _make_merge(tmp: Path, base: dict, ours: dict, theirs: dict, attrs: str = "") -> Path:
    """A throwaway repo mid-merge: ``main`` (ours) merging ``flowgate_default_0594``
    (theirs) with zdiff3, exactly as finalize runs it. ``core.autocrlf=false`` like the
    FlowGate repositories (this host's system git config turns it on)."""
    repo = tmp / "repo"
    _git(["init", "-b", "main", str(repo)], tmp)
    _git(["config", "core.autocrlf", "false"], repo)

    def commit(files: dict, msg: str):
        for path, data in files.items():
            target = repo / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        _git(["add", "-A"], repo)
        _git(["commit", "-m", msg], repo)

    if attrs:
        (repo / ".gitattributes").write_text(attrs, encoding="utf-8")
    commit(base, "base")
    _git(["checkout", "-b", "flowgate_default_0594"], repo)
    commit(theirs, "theirs")
    _git(["checkout", "main"], repo)
    commit(ours, "ours")
    merged = _git(["-c", "merge.conflictStyle=zdiff3", "merge", "--no-ff", "-m", "m",
                   "flowgate_default_0594"], repo, check=False)
    assert merged.returncode != 0
    return repo


def _unmerged(repo: Path) -> list[str]:
    out = _git(["diff", "--name-only", "--diff-filter=U"], repo).stdout.decode()
    return sorted(line.strip() for line in out.splitlines() if line.strip())


def _chunks(repo: Path, path: str) -> list[dict]:
    text = (repo / path).read_text(encoding="utf-8", errors="replace")
    return token_routes._split_conflict_chunks(text)


# ── 1. the write path: bytes in the file's own line ending ──────────────────────────


class TestResolvedFileKeepsItsLineEnding:
    def test_text_mode_write_is_the_0599_mechanism(self, tmp_path):
        """The bug being fixed, stated on this platform: `write_text` of LF content is
        CRLF on Windows, and CRLF content comes out CR-CR-LF."""
        target = tmp_path / "f.ts"
        target.write_text("a\nb\n", encoding="utf-8")
        if os.name == "nt":
            assert target.read_bytes() == b"a\r\nb\r\n"
            target.write_text("a\r\nb\r\n", encoding="utf-8")
            assert target.read_bytes() == b"a\r\r\nb\r\r\n"
        else:
            assert target.read_bytes() == b"a\nb\n"

    def test_lf_head_file_is_written_lf_and_crlf_head_file_crlf(self, tmp_path):
        repo = tmp_path / "r"
        _git(["init", "-b", "main", str(repo)], tmp_path)
        _git(["config", "core.autocrlf", "false"], repo)
        (repo / "lf.ts").write_bytes(b"x\ny\n")
        (repo / "crlf.vue").write_bytes(b"x\r\ny\r\n")
        _git(["add", "-A"], repo)
        _git(["commit", "-m", "c"], repo)

        for path, content, want in (
            ("lf.ts", "p\nq\n", b"p\nq\n"),
            ("lf.ts", "p\r\nq\r\n", b"p\nq\n"),            # a CRLF submission is folded back
            ("crlf.vue", "p\nq\n", b"p\r\nq\r\n"),         # what read_text handed out (LF)
            ("crlf.vue", "p\r\nq\r\n", b"p\r\nq\r\n"),     # never CR-CR-LF
            ("new.py", "p\nq\n", b"p\nq\n"),               # not in HEAD → LF
        ):
            conflict._write_resolved_file(repo, path, repo / path, content)
            assert (repo / path).read_bytes() == want, (path, content)

    def test_eol_attribute_wins_over_head(self, tmp_path):
        repo = tmp_path / "r"
        _git(["init", "-b", "main", str(repo)], tmp_path)
        _git(["config", "core.autocrlf", "false"], repo)
        (repo / "en.ts").write_bytes(b"x\r\n")
        (repo / ".gitattributes").write_text("*.ts -text\n", encoding="utf-8")
        _git(["add", "-A"], repo)
        _git(["commit", "-m", "c"], repo)
        assert conflict._path_eol(repo, "en.ts") == "crlf"
        (repo / ".gitattributes").write_text("*.ts text eol=lf\n", encoding="utf-8")
        assert conflict._path_eol(repo, "en.ts") == "lf"

    def test_repository_pins_the_i18n_files_to_lf(self):
        """The repo policy half: the three files 6132cf58 flipped are LF again and
        `.gitattributes` keeps them LF whatever writes them next."""
        for name in ("en", "ja", "ko"):
            path = f"client/shared/i18n/{name}.ts"
            assert conflict._path_eol(_source_repo(), path) == "lf"
            assert b"\r" not in (_source_repo() / path).read_bytes(), path


# ── 2. EOL-only conflicts are separated, real ones keep only real chunks ────────────


def _lines(prefix: str, n: int, eol: bytes = b"\n") -> list[bytes]:
    return [f"{prefix} {i}".encode() + eol for i in range(n)]


class TestSeparateEolConflicts:
    def _repo(self, tmp_path, attrs=""):
        base = b"".join(_lines("line", 30))
        theirs_lines = _lines("line", 30)
        theirs_lines[10] = b"line 10 changed by the group\n"
        # ours: the whole file flipped to CRLF, nothing else (the 6132cf58 shape) …
        eol_ours = base.replace(b"\n", b"\r\n")
        # … and a second file flipped to CRLF AND genuinely conflicting on one line.
        real_ours = _lines("line", 30, b"\r\n")
        real_ours[20] = b"line 20 changed on main\r\n"
        real_theirs = _lines("line", 30)
        real_theirs[20] = b"line 20 changed by the group\n"
        return _make_merge(
            tmp_path,
            base={"eol.ts": base, "real.py": base, "plain.txt": b"a\n"},
            ours={"eol.ts": eol_ours, "real.py": b"".join(real_ours), "plain.txt": b"ours\n"},
            theirs={"eol.ts": b"".join(theirs_lines), "real.py": b"".join(real_theirs),
                    "plain.txt": b"theirs\n"},
            attrs=attrs,
        )

    def test_eol_only_file_is_merged_staged_and_left_in_its_own_line_ending(self, tmp_path):
        repo = self._repo(tmp_path)
        assert _unmerged(repo) == ["eol.ts", "plain.txt", "real.py"]
        # git's view: the EOL flip makes each of the two files ONE whole-file chunk.
        assert [len(_chunks(repo, p)) for p in ("eol.ts", "real.py")] == [1, 1]
        assert _chunks(repo, "eol.ts")[0]["end_line"] > 55

        outcome = conflict.separate_eol_conflicts(repo, _unmerged(repo))

        assert outcome == {"eol_only": ["eol.ts"], "renormalized": ["real.py"]}
        assert _unmerged(repo) == ["plain.txt", "real.py"]      # eol.ts staged
        merged = (repo / "eol.ts").read_bytes()
        assert merged.count(b"\r\n") == 30 and merged.count(b"\n") == 30   # ours' CRLF
        assert b"line 10 changed by the group\r\n" in merged
        # the real conflict now holds only its real chunk, in ours' line ending
        chunks = _chunks(repo, "real.py")
        assert len(chunks) == 1
        assert chunks[0]["ours"] == ["line 20 changed on main"]
        assert chunks[0]["theirs"] == ["line 20 changed by the group"]
        assert chunks[0]["base"] == ["line 20"]
        assert (repo / "real.py").read_bytes().count(b"\r\n") == (repo / "real.py").read_bytes().count(b"\n")
        # a plain conflict with no CR anywhere is left exactly as git wrote it
        assert b"<<<<<<< HEAD" in (repo / "plain.txt").read_bytes()

    def test_labels_of_the_real_merge_are_kept(self, tmp_path):
        repo = self._repo(tmp_path)
        conflict.separate_eol_conflicts(repo, ["real.py"])
        text = (repo / "real.py").read_text(encoding="utf-8")
        assert "<<<<<<< HEAD" in text and ">>>>>>> flowgate_default_0594" in text

    def test_committed_eol_attribute_prevents_the_conflict_altogether(self, tmp_path):
        """The repository policy half: with `text eol=lf` committed, the CRLF write is
        normalised at `git add`, so the flip never becomes a conflict at all."""
        repo = self._repo(tmp_path, attrs="eol.ts text eol=lf\n")
        assert "eol.ts" not in _unmerged(repo)
        assert b"\r" not in (repo / "eol.ts").read_bytes()

    def test_eol_attribute_decides_the_written_line_ending(self, tmp_path):
        repo = self._repo(tmp_path)
        (repo / ".gitattributes").write_text("eol.ts text eol=lf\n", encoding="utf-8")
        outcome = conflict.separate_eol_conflicts(repo, _unmerged(repo))
        assert "eol.ts" in outcome["eol_only"]
        assert b"\r" not in (repo / "eol.ts").read_bytes()

    def test_apply_records_eol_only_files_on_the_session(self, tmp_path, monkeypatch):
        repo = self._repo(tmp_path)
        db = git_service.db_git
        resolved: list[str] = []
        stored: dict = {}
        monkeypatch.setattr(db, "session_files", lambda merge_id: [
            {"path": p} for p in ("eol.ts", "plain.txt", "real.py")])
        monkeypatch.setattr(db, "mark_file_resolved", lambda merge_id, path: resolved.append(path))
        monkeypatch.setattr(db, "get_session", lambda merge_id: {"context": "{}"})
        monkeypatch.setattr(db, "session_context", lambda session: {"review_state": None})
        monkeypatch.setattr(db, "set_session_context", lambda merge_id, ctx: stored.update(ctx))

        git_service.apply_eol_separation(7, repo)

        assert resolved == ["eol.ts"]
        assert stored == {"review_state": None, "eol_only_paths": ["eol.ts"],
                          "eol_renormalized_paths": ["real.py"]}


# ── 3. the real 0594 merge, replayed ───────────────────────────────────────────────

_0594_PATHS = [
    "client/shared/i18n/en.ts",
    "client/shared/i18n/ja.ts",
    "client/shared/i18n/ko.ts",
    "client/src/main/components/ReviewActionBar.vue",
    "server/modules/flow_gate/api/inbox_routes.py",
    "server/modules/flow_gate/services/git/conflict.py",
    "server/modules/flow_gate/services/git/finalize.py",
    "server/modules/flow_gate/services/git_service.py",
    "server/modules/flow_gate/workflow/routers/workflow.py",
    "server/tests/test_git_facade_seam_scope_0550.py",
]
_0594_THEIRS = "1a968388"          # flowgate_default_0594 head (merge_head of sessions 99~103)


def _blob(rev: str, path: str) -> bytes:
    return _git(["cat-file", "blob", f"{rev}:{path}"], _source_repo()).stdout


def _conflict_mention(repo: Path, paths: list[str], eol_only: list[str], refs: dict) -> str:
    """The real `_build_conflict_mention`, fed what `list_conflicts` would read."""
    files = []
    for path in paths:
        content = (repo / path).read_text(encoding="utf-8", errors="replace")
        files.append({"path": path, "content": content,
                      "conflict_count": sum(1 for l in content.splitlines() if l.startswith("<<<<<<<"))})
    payload = {"ok": True, "merge_id": 100, "branch": "flowgate_default_0594",
               "base_branch": "main", "files": files, "kind": "merge", "tr_conflict": None,
               "eol_only_paths": eol_only, "refs": refs}
    original = token_routes.git_service.list_conflicts
    token_routes.git_service.list_conflicts = lambda group_id, merge_id: payload
    try:
        return token_routes._build_conflict_mention(
            group_id="flowgate.default.0594", project_id="flowgate", merge_id=100,
            scratch_dir="/scratch/x", raw_token=_TOKEN, api_base_url=_API, locale="ko",
        )
    finally:
        token_routes.git_service.list_conflicts = original


class TestReplay0594:
    """NR0003 §1.2 inputs: sessions 100 (ours 8fd51466) and 103 (ours e40a3ae0)."""

    @pytest.mark.parametrize("ours_rev", ["8fd51466", "e40a3ae0"])
    def test_0594_becomes_6_files_24_chunks_and_a_small_mention(self, tmp_path, ours_rev):
        for rev in (ours_rev, _0594_THEIRS):
            if _git(["cat-file", "-e", f"{rev}^{{commit}}"], _source_repo(), check=False).returncode:
                pytest.fail(f"0594 replay needs commit {rev} in {_source_repo()} (full clone required)")
        base_rev = _git(["merge-base", ours_rev, _0594_THEIRS], _source_repo()).stdout.decode().strip()
        repo = _make_merge(
            tmp_path,
            base={p: _blob(base_rev, p) for p in _0594_PATHS},
            ours={p: _blob(ours_rev, p) for p in _0594_PATHS},
            theirs={p: _blob(_0594_THEIRS, p) for p in _0594_PATHS},
        )
        refs = {"ours": ours_rev, "theirs": _0594_THEIRS}

        # as git produced it: the recorded session shape (NR0003 §1.2 table)
        assert _unmerged(repo) == sorted(_0594_PATHS)
        assert sum(len(_chunks(repo, p)) for p in _0594_PATHS) == 28

        outcome = conflict.separate_eol_conflicts(repo, _unmerged(repo))

        assert outcome["eol_only"] == [
            "client/shared/i18n/en.ts", "client/shared/i18n/ja.ts", "client/shared/i18n/ko.ts",
            "client/src/main/components/ReviewActionBar.vue",
        ]
        remaining = _unmerged(repo)
        assert len(remaining) == 6 and all(p.startswith("server/") for p in remaining)
        assert sum(len(_chunks(repo, p)) for p in remaining) == 24
        for path in outcome["eol_only"]:
            data = (repo / path).read_bytes()
            assert b"<<<<<<<" not in data
            # written in ours' own line ending: no churn against the base branch
            assert conflict._majority_eol(data) == conflict._majority_eol(_blob(ours_rev, path))
            assert b"\r\r\n" not in data

        mention = _conflict_mention(repo, sorted(_0594_PATHS), outcome["eol_only"], refs)
        assert len(mention) <= 10_000, len(mention)
        payload = json.loads(mention.split("```json\n", 1)[1].rsplit("\n```", 1)[0])
        assert payload["chunk_text"] == "omitted"
        assert [f["path"] for f in payload["files"]] == remaining
        assert sum(len(f["chunks"]) for f in payload["files"]) == 24
        assert [r for r in payload["resolved_files"] if r["reason"] == "eol_only"] == [
            {"path": p, "reason": "eol_only"} for p in outcome["eol_only"]
        ]
        # every advertised range is a real marker pair, readable back through /remote/read
        for entry in payload["files"]:
            lines = (repo / entry["path"]).read_text(encoding="utf-8", errors="replace").splitlines()
            for chunk in entry["chunks"]:
                assert lines[chunk["start_line"] - 1].startswith("<<<<<<<")
                assert lines[chunk["end_line"] - 1].startswith(">>>>>>>")
        assert f"POST {_API}/remote/read" in mention
        assert f"GET {_API}/help/tools" in mention
        assert f'"ref": "{ours_rev}"' in mention


# ── 4. the mention itself ─────────────────────────────────────────────────────────


def _mention_for(files, **extra) -> str:
    payload = {"ok": True, "merge_id": 42, "branch": "group/branch", "base_branch": "main",
               "files": files, "kind": "merge", "tr_conflict": None, **extra}
    original = token_routes.git_service.list_conflicts
    token_routes.git_service.list_conflicts = lambda group_id, merge_id: payload
    try:
        return token_routes._build_conflict_mention(
            group_id="grp.default.0001", project_id="grp", merge_id=42,
            scratch_dir=r"C:\scratch\tok", raw_token="tok_raw", api_base_url=_API, locale="ko",
        )
    finally:
        token_routes.git_service.list_conflicts = original


def _session(mention: str) -> dict:
    return json.loads(mention.split("```json\n", 1)[1].rsplit("\n```", 1)[0])


_SMALL = "a\n<<<<<<< HEAD\nours\n||||||| base\nbase\n=======\ntheirs\n>>>>>>> branch\nz\n"


class TestToolDrivenConflictMention:
    def test_small_conflict_still_inlines_its_chunk_text_with_locations(self):
        mention = _mention_for([{"path": "a.py", "content": _SMALL, "conflict_count": 1}])
        session = _session(mention)
        assert session["chunk_text"] == "inline"
        (chunk,) = session["files"][0]["chunks"]
        assert (chunk["start_line"], chunk["end_line"]) == (2, 8)
        assert (chunk["ours"], chunk["base"], chunk["theirs"]) == (["ours"], ["base"], ["theirs"])
        assert "The chunk text is included below." in mention

    def test_large_conflict_carries_locations_not_text(self):
        body = "".join(f"<<<<<<< HEAD\n{'o' * 60}\n||||||| base\nb\n=======\n{'t' * 60}\n>>>>>>> br\nsep\n"
                       for _ in range(60))
        mention = _mention_for([{"path": "big.py", "content": body, "conflict_count": 60}])
        session = _session(mention)
        assert session["chunk_text"] == "omitted"
        chunks = session["files"][0]["chunks"]
        assert len(chunks) == 60
        assert chunks[0] == {"chunk": 1, "start_line": 1, "end_line": 7,
                             "ours_lines": 1, "base_lines": 1, "theirs_lines": 1}
        assert "o" * 60 not in mention and "t" * 60 not in mention
        assert "The chunk text is NOT included" in mention
        # one line per chunk location
        assert mention.count('{"chunk": ') == 60

    def test_remote_source_section_is_read_only(self):
        mention = _mention_for([{"path": "a.py", "content": _SMALL, "conflict_count": 1}])
        section = mention.split("## Remote project source CRUD\n---\n", 1)[1].split("\n\n", 1)[0]
        tools = next(l for l in section.splitlines() if l.startswith("Tools: "))
        names = [n.strip() for n in tools[len("Tools: "):].split(",")]
        assert "read" in names and "grep" in names
        assert not {"write", "patch", "remove"} & set(names)
        assert "Authorization: Bearer tok_raw" in section
        assert "Remote write, patch and remove are not available to this run." in mention

    def test_eol_only_and_already_resolved_files_are_listed_apart(self):
        mention = _mention_for(
            [{"path": "a.py", "content": _SMALL, "conflict_count": 1},
             {"path": "i18n/en.ts", "content": "clean\n", "conflict_count": 0},
             {"path": "done.py", "content": "clean\n", "conflict_count": 0}],
            eol_only_paths=["i18n/en.ts"],
        )
        session = _session(mention)
        assert [f["path"] for f in session["files"]] == ["a.py"]
        assert session["resolved_files"] == [
            {"path": "i18n/en.ts", "reason": "eol_only"},
            {"path": "done.py", "reason": "resolved"},
        ]

    def test_nothing_left_tells_the_worker_to_complete(self):
        mention = _mention_for(
            [{"path": "i18n/en.ts", "content": "clean\n", "conflict_count": 0}],
            eol_only_paths=["i18n/en.ts"],
        )
        assert '{"files": [], "complete": true}' in mention
        assert _session(mention)["files"] == []

    def test_mention_stays_english_and_scratch_free(self):
        mention = _mention_for([{"path": "a.py", "content": _SMALL, "conflict_count": 1}])
        assert not re.search(r"[\uac00-\ud7a3]", mention)
        assert r"C:\scratch\tok" not in mention


# ── 5. TR0006: a failing merge-file must not touch the conflict ──────────────────────


def _index_stages(repo: Path) -> bytes:
    return _git(["ls-files", "-u", "-z"], repo).stdout


class TestMergeFileFailureLeavesTheConflictAlone:
    """`git merge-file` exits with the conflict count; 128/129/255 are errors whose
    stdout is not a merge. Read as a count, an empty stdout would have replaced the
    conflicted file and dropped its markers."""

    @pytest.mark.parametrize("exit_code,stdout", [
        (129, b""),                                  # usage error (unsupported option)
        (128, b""),                                  # fatal
        (255, b""),                                  # -1 as seen by the parent
        (-1, b""),
        (1, b"partial output without markers\n"),   # a "count" with no conflict in it
    ])
    def test_error_exit_keeps_file_and_index(self, tmp_path, monkeypatch, exit_code, stdout):
        repo = TestSeparateEolConflicts()._repo(tmp_path)
        paths = _unmerged(repo)
        before_files = {p: (repo / p).read_bytes() for p in paths}
        before_index = _index_stages(repo)

        real_run = subprocess.run

        def fake_run(args, *a, **kw):
            if list(args[:2]) == ["git", "merge-file"]:
                return subprocess.CompletedProcess(args, exit_code, stdout, b"usage: git merge-file")
            return real_run(args, *a, **kw)

        monkeypatch.setattr(conflict.subprocess, "run", fake_run)

        assert conflict._eol_normalized_merge(repo, "real.py") is None
        outcome = conflict.separate_eol_conflicts(repo, paths)

        assert outcome == {"eol_only": [], "renormalized": []}
        assert {p: (repo / p).read_bytes() for p in paths} == before_files
        assert _index_stages(repo) == before_index
        assert _unmerged(repo) == paths
        assert b"<<<<<<<" in (repo / "real.py").read_bytes()

    def test_error_exit_marks_nothing_resolved_on_the_session(self, tmp_path, monkeypatch):
        repo = TestSeparateEolConflicts()._repo(tmp_path)
        db = git_service.db_git
        resolved: list[str] = []
        stored: dict = {}
        monkeypatch.setattr(db, "session_files", lambda merge_id: [
            {"path": p} for p in ("eol.ts", "plain.txt", "real.py")])
        monkeypatch.setattr(db, "mark_file_resolved", lambda merge_id, path: resolved.append(path))
        monkeypatch.setattr(db, "get_session", lambda merge_id: {"context": "{}"})
        monkeypatch.setattr(db, "session_context", lambda session: {})
        monkeypatch.setattr(db, "set_session_context", lambda merge_id, ctx: stored.update(ctx))
        real_run = subprocess.run
        monkeypatch.setattr(conflict.subprocess, "run", lambda args, *a, **kw: (
            subprocess.CompletedProcess(args, 129, b"", b"")
            if list(args[:2]) == ["git", "merge-file"] else real_run(args, *a, **kw)))

        git_service.apply_eol_separation(7, repo)

        assert resolved == []
        assert stored == {"eol_only_paths": [], "eol_renormalized_paths": []}
        assert _unmerged(repo) == ["eol.ts", "plain.txt", "real.py"]


# ── 6. TR0006: the read instruction works for a chunk on line 1 ──────────────────────


def _remote_read(monkeypatch, root: Path, body: dict) -> tuple[int, dict]:
    """The real `/remote/read` pipeline (validation → root → execute); only the token
    lookup, the scope table and the history log are stubbed."""
    from modules.flow_gate.services import remote_tool_service as remote

    grant = {"grant_id": 1, "project": "grp", "module": "default", "group_id": "grp.default.0001"}
    monkeypatch.setattr(remote, "_authenticate", lambda raw: grant)
    monkeypatch.setattr(remote.db_grants, "get_scopes", lambda grant_id: {"read", "grep"})
    monkeypatch.setattr(remote, "_resolve_src_root", lambda g, op="read": root)
    monkeypatch.setattr(remote, "_log", lambda *a, **kw: None)
    monkeypatch.setattr(remote, "_locale_for_grant", lambda g: "en")
    return remote.handle("read", "tok_raw", body)


class TestReadInstructionIsValidAtTheTopOfTheFile:
    _AT_TOP = "<<<<<<< HEAD\nours\n||||||| base\nbase\n=======\ntheirs\n>>>>>>> branch\nz\n"

    def test_instruction_clamps_start_line_to_one(self):
        mention = _mention_for([{"path": "a.py", "content": self._AT_TOP, "conflict_count": 1}])
        assert '"start_line": <max(1, start_line - 40)>' in mention
        assert "<start_line - 40>" not in mention

    @pytest.mark.parametrize("large", [False, True])
    def test_following_the_instruction_for_a_line_1_chunk_succeeds(self, tmp_path, monkeypatch, large):
        content = self._AT_TOP
        if large:       # the location-only mention, like the 0594 replay
            content += "".join(f"<<<<<<< HEAD\n{'o' * 60}\n||||||| base\nb\n=======\n{'t' * 60}\n"
                               f">>>>>>> br\nsep\n" for _ in range(60))
        (tmp_path / "a.py").write_text(content, encoding="utf-8", newline="\n")
        session = _session(_mention_for([{"path": "a.py", "content": content, "conflict_count": 1}]))
        assert session["chunk_text"] == ("omitted" if large else "inline")
        chunk = session["files"][0]["chunks"][0]
        assert chunk["start_line"] == 1

        # exactly what the instruction says to send
        body = {"path": "a.py", "start_line": max(1, chunk["start_line"] - 40),
                "end_line": chunk["end_line"] + 40}
        status, envelope = _remote_read(monkeypatch, tmp_path, body)
        assert status == 200, envelope
        assert envelope["content"].startswith("<<<<<<< HEAD\n")
        assert envelope["returned_start_line"] == 1

        # the unclamped arithmetic the old instruction produced is what the reviewer saw fail
        status, envelope = _remote_read(monkeypatch, tmp_path, dict(body, start_line=chunk["start_line"] - 40))
        assert status == 422
        assert envelope["error"]["details"]["reason"] == "invalid_line_range"


# ── 7. TR0006: tracked repo config is in the manifest AND the commit ─────────────────


class TestRepoConfigFileIsReviewedAndCommitted:
    """`.gitattributes` used to count as top-level dot debris: the TR manifest dropped it
    while `git add -u` committed it anyway. The shared rule now keeps the repository's
    own policy files, and secrets stay out."""

    def test_rule_keeps_policy_files_and_still_drops_secrets(self):
        from modules.flow_gate.services import path_exclusion_rules as rules
        from modules.flow_gate.services import tr_scope_service as trs

        for path in (".gitattributes", ".gitignore", "client/.gitignore"):
            assert rules.exclusion_reason(path) is None, path
            assert trs.is_excluded_path(path) is False, path
            assert git_service._is_hidden_source_path(path) is False, path
        assert rules.exclusion_reason(".env") == rules.REASON_DOT_TOPLEVEL
        assert rules.exclusion_reason(".git/config") == rules.REASON_DOT_TOPLEVEL
        assert rules.exclusion_reason(".venv/.gitignore") == rules.REASON_DOT_TOPLEVEL
        assert rules.is_excluded_path("client/node_modules/pkg/.gitignore") is True
        assert git_service._is_hidden_source_path("server/.env.local") is True

    def test_manifest_changes_list_and_commit_agree(self, tmp_path, monkeypatch):
        from modules.flow_gate.services import tr_scope_service as trs

        repo = tmp_path / "repo"
        _git(["init", "-b", "main", str(repo)], tmp_path)
        _git(["config", "core.autocrlf", "false"], repo)
        (repo / ".gitattributes").write_bytes(b"*.sh text eol=lf\n")
        (repo / "kept.py").write_bytes(b"x = 1\n")
        (repo / ".env").write_bytes(b"A=1\n")
        _git(["add", "-A"], repo)
        _git(["commit", "-m", "base"], repo)
        _git(["checkout", "-b", "work"], repo)
        # the 0608 shape: a tracked policy edit next to an ordinary one, uncommitted
        (repo / ".gitattributes").write_bytes(b"*.sh text eol=lf\nclient/shared/i18n/*.ts text eol=lf\n")
        (repo / "kept.py").write_bytes(b"x = 2\n")

        monkeypatch.setattr(git_service, "effective_src_root_ex",
                            lambda project_id, group_id: (repo, git_service.SRC_ROOT_WORKTREE))
        monkeypatch.setattr(git_service.db_git, "get_state", lambda gid: {"branch": "work"})
        monkeypatch.setattr(git_service.db_git, "get_config", lambda pid: {"base_branch": "main"})
        monkeypatch.setattr(trs, "resolve_stage", lambda project_id: trs.STAGE_ENFORCE)

        # the manifest the server computes for the TR
        silent = trs.evaluate("p", "g", "## 변경 파일\n\n- kept.py\n")
        assert ".gitattributes" in silent["detected"]
        assert silent["unreported"] == [".gitattributes"]      # hiding it is now caught
        result = trs.evaluate("p", "g", "## 변경 파일\n\n- .gitattributes\n- kept.py\n")
        manifest = sorted(row["path"] for row in result["file_manifest"])
        assert manifest == [".gitattributes", "kept.py"]
        assert result["verdict"] == trs.VERDICT_PASS

        # the reviewer's change list reads the same diff
        head = _git(["rev-parse", "HEAD"], repo).stdout.decode().strip()
        base = _git(["merge-base", "main", "HEAD"], repo).stdout.decode().strip()
        monkeypatch.setattr(git_service, "_group_diff_context",
                            lambda project_id, group_id: ("main", "work", head, repo, base))
        monkeypatch.setattr(git_service, "_group_untracked_safe", lambda *a: [])
        changes = git_service.read_group_changes("p", "g")["data"]
        assert sorted(c["path"] for c in changes["changes"]) == manifest

        # and the commit carries exactly that — no more, no less
        excluded = git_service._absorb_worker_edits(repo, "test: absorb", None)
        assert excluded == []
        committed = _git(["diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD"], repo).stdout.decode()
        assert sorted(committed.split()) == manifest
