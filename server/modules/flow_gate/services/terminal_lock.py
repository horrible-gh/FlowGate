"""Installation lock shared by the web server and the setup-terminal CLI."""
from contextlib import contextmanager
import os

from .terminal_errors import BundleError

if os.name == "nt":
    import msvcrt

    def _lock(stream):
        stream.seek(0)
        msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)

    def _unlock(stream):
        stream.seek(0)
        msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
else:
    import fcntl

    def _lock(stream):
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _unlock(stream):
        fcntl.flock(stream, fcntl.LOCK_UN)


@contextmanager
def installation_lock(root):
    root.mkdir(parents=True, exist_ok=True)
    with (root / "install.lock").open("a+b") as stream:
        try:
            _lock(stream)
        except OSError as exc:
            raise BundleError("terminal_install_running", "Another installer owns the terminal directory", 409) from exc
        try:
            yield
        finally:
            _unlock(stream)
