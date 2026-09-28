import pytest
from modules.flow_gate.documents import tr2_service as tr2

@pytest.mark.parametrize("path", ["/etc/passwd","../escape","C:\\escape",
                                  "a/../b","", "a\\..\\b"])
def test_unsafe_syntax(path):
    with pytest.raises(tr2.Tr2ValidationError, match="tr2_path_unsafe"):
        tr2.normalized_target_path({"file":path})

def test_normalized_relative_path():
    assert tr2.normalized_target_path({"file":"src/./a.py"})=="src/a.py"
    assert tr2.normalized_target_path({"file":r"server\app.py"})=="server/app.py"

def test_symlink_escape_and_directory_target(tmp_path):
    outside=tmp_path.parent/"outside-0565"
    outside.mkdir(exist_ok=True)
    (tmp_path/"link").symlink_to(outside, target_is_directory=True)
    with pytest.raises(tr2.Tr2ValidationError, match="tr2_path_unsafe"):
        tr2.target_fingerprint({"edits":[{"file":"link/x","kind":"create_file"}]},tmp_path)
    (tmp_path/"folder").mkdir()
    with pytest.raises(tr2.Tr2ValidationError, match="tr2_path_unsafe"):
        tr2.target_fingerprint({"edits":[{"file":"folder","kind":"edit"}]},tmp_path)
