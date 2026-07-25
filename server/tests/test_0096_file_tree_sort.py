"""R0003 / 0096 — File explorer ordering regression.

The file tree must order folders before files (folders-first), and within each
group use natural, case-insensitive comparison. Previously get_file_tree() used a
bare ``sorted(os.listdir(...))`` which interleaved folders and files in raw
Unicode codepoint order, so numeric/uppercase-named files floated above
lowercase-named folders ("folders sank to the bottom") and ``file10`` sorted
before ``file2``.
"""
from modules.flow_gate import process_service
from modules.flow_gate.process_service import _file_tree_sort_key


def test_sort_key_folders_before_files():
    # A file that sorts very early by name must still come after any folder.
    assert _file_tree_sort_key("0001-R.md", is_dir=False) > _file_tree_sort_key("zzz", is_dir=True)
    assert _file_tree_sort_key("src", is_dir=True) < _file_tree_sort_key("0001-R.md", is_dir=False)


def test_sort_key_case_insensitive():
    # 'Z'/'z' must not jump ahead of 'a' (raw codepoint would put 'Z'=90 < 'a'=97).
    same_group = sorted(["Zebra", "apple", "Banana"], key=lambda n: _file_tree_sort_key(n, False))
    assert same_group == ["apple", "Banana", "Zebra"]


def test_sort_key_natural_numeric():
    files = sorted(["file10.txt", "file2.txt", "file1.txt"], key=lambda n: _file_tree_sort_key(n, False))
    assert files == ["file1.txt", "file2.txt", "file10.txt"]
    docs = sorted(["0010-T.md", "0002-N.md", "0001-R.md"], key=lambda n: _file_tree_sort_key(n, False))
    assert docs == ["0001-R.md", "0002-N.md", "0010-T.md"]


def _patch_tree(monkeypatch, root):
    from modules.flow_gate.db import projects as _proj
    from modules.flow_gate.services import git_service

    monkeypatch.setattr(_proj, "get_by_id", lambda pid: {"project_name": "p"})
    monkeypatch.setattr(_proj, "get_settings", lambda pid: {"branch": "main"})
    monkeypatch.setattr(git_service, "base_src_root", lambda pid, name, branch: root)


def test_get_file_tree_orders_folders_first_then_natural(tmp_path, monkeypatch):
    # Build a FlowGate-style tree: numeric/uppercase files + lowercase folders.
    (tmp_path / "src").mkdir()
    (tmp_path / "Docs").mkdir()
    (tmp_path / "assets").mkdir()
    for fname in ("0001-R_document.md", "0010-T_document.md", "0002-N_document.md",
                  "README.md", "file2.txt", "file10.txt"):
        (tmp_path / fname).write_text("x", encoding="utf-8")
    # hidden / .db must stay excluded
    (tmp_path / ".hidden").write_text("x", encoding="utf-8")
    (tmp_path / "flowgate.db").write_text("x", encoding="utf-8")

    _patch_tree(monkeypatch, tmp_path)
    nodes = process_service.get_file_tree("proj")["nodes"]
    top = [n["name"] for n in nodes if n["parent_id"] is None]

    assert ".hidden" not in top and "flowgate.db" not in top
    folders = [n for n in top if n in ("src", "Docs", "assets")]
    files = [n for n in top if n not in ("src", "Docs", "assets")]
    # folders-first: every folder precedes every file
    assert top == folders + files
    assert folders == ["assets", "Docs", "src"]
    assert files == ["0001-R_document.md", "0002-N_document.md", "0010-T_document.md",
                     "file2.txt", "file10.txt", "README.md"]
