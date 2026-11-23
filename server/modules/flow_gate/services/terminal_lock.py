"""OS-owned installation lock shared by the web server and setup-terminal CLI."""
from contextlib import contextmanager
import os
from .terminal_errors import BundleError


@contextmanager
def installation_lock(root):
    root.mkdir(parents=True, exist_ok=True)
    stream = (root / "install.lock").open("a+b")
    acquired = False
    try:
        if os.name == "nt":
            import msvcrt
            stream.seek(0)  # Windows permits locking a byte beyond EOF.
            try:
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                acquired = True
            except OSError as exc:
                raise BundleError("terminal_install_running", "Another installer owns the terminal directory", 409) from exc
        else:
            import fcntl
            try:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
            except OSError as exc:
                raise BundleError("terminal_install_running", "Another installer owns the terminal directory", 409) from exc
        yield
    finally:
        if acquired:
            if os.name == "nt":
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream, fcntl.LOCK_UN)
        stream.close()
