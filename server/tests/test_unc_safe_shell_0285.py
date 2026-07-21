r"""0285 B0001 -> NR0004 regression.

cmd.exe refuses a UNC path (``\\host\share\...``) as its current directory: it
prints "UNC 경로는 지원되지 않습니다 / UNC パスはサポートされません。" and silently resets
CWD to ``C:\Windows``, which breaks every relative path a ``shell=True`` command
relies on. ``process_runner.unc_safe_shell`` is the single defense: on Windows it
routes a UNC root through ``pushd`` (a temporary drive-letter mapping) with
``cwd=None`` instead of handing the UNC path to cmd.exe; POSIX and local /
mapped-drive roots pass through unchanged.

These are pure unit tests of the routing decision — no real cmd.exe — so they run
on the POSIX runner as well as on a Windows host.
"""
from modules.flow_gate.services import process_runner


def test_unc_root_on_windows_wraps_with_pushd(monkeypatch):
    monkeypatch.setattr(process_runner.os, "name", "nt")
    cmd, cwd = process_runner.unc_safe_shell(
        "python -m pytest -q", r"\\host\share\src\proj\main"
    )
    # Never hand the UNC path to cmd.exe as CWD; pushd relocates into it instead.
    assert cwd is None
    assert cmd == r'pushd "\\host\share\src\proj\main" && python -m pytest -q'


def test_local_root_on_windows_passes_through(monkeypatch):
    monkeypatch.setattr(process_runner.os, "name", "nt")
    cmd, cwd = process_runner.unc_safe_shell("pytest -q", r"C:\work\src\proj\main")
    assert cmd == "pytest -q"
    assert cwd == r"C:\work\src\proj\main"


def test_posix_never_wraps(monkeypatch):
    monkeypatch.setattr(process_runner.os, "name", "posix")
    # A value that merely looks UNC-ish must still pass through unchanged off Windows.
    cmd, cwd = process_runner.unc_safe_shell("pytest -q", "/srv/storage/src/proj/main")
    assert cmd == "pytest -q"
    assert cwd == "/srv/storage/src/proj/main"


def test_pushd_preserves_command_exit_code_shape(monkeypatch):
    # `&&` leaves the real command last on the line, so cmd /c returns the
    # command's exit code — the pass/fail signal the runner reads — not pushd's.
    # Nothing (no popd / trailing `&`) is appended after it that would clobber it.
    monkeypatch.setattr(process_runner.os, "name", "nt")
    cmd, _ = process_runner.unc_safe_shell("run-tests", r"\\h\s\proj")
    assert cmd.endswith("&& run-tests")
