"""Pseudo-terminal backends for the Designer terminal.

One object per shell: ``read()`` blocks for the next output chunk (``None`` at EOF),
``write()`` sends keyboard input, ``resize()`` changes the rows/columns the shell sees,
and ``terminate()`` ends the shell and EVERY process below it.

* Linux / macOS: the stdlib ``pty`` module. The shell is the server account's login
  shell, started as a login shell (``-l``) in a new session so it owns the terminal.
* Windows: ConPTY through ``pywinpty`` (``winpty`` module), running PowerShell.

No command filtering happens here or anywhere else (by design): the shell runs as the
server's own OS account and may ``cd`` anywhere.
"""
from __future__ import annotations

import codecs
import os
import shutil
import signal
import struct
import subprocess
import sys
import threading
import time
from typing import Optional

from util.credential_redaction import is_secret_key

IS_WINDOWS = os.name == "nt"

# Environment keys the server itself holds secrets in. The shell is an admin tool, but
# FlowGate's own signing/encryption keys have no business in its environment.
_SECRET_ENV_EXACT = frozenset({"SECRET_KEY", "DB_PASSWORD"})
_SECRET_ENV_MARKERS = ("KEY", "PEPPER", "SECRET", "TOKEN", "PASSWORD")
_NO_LOGIN_SHELLS = frozenset({"nologin", "false", "true"})

# Python helper the POSIX shell is started through: after ``setsid`` (start_new_session)
# it makes the pty its controlling terminal, then becomes the shell (same pid) via exec.
# Doing this in a child interpreter keeps preexec_fn (unsafe in a threaded server) out.
_CTTY_SHIM = (
    "import fcntl,os,sys,termios;"
    "fcntl.ioctl(0,termios.TIOCSCTTY,0);"
    "os.execvp(sys.argv[1],sys.argv[1:])"
)

TERMINATE_GRACE_SECONDS = 1.5
_READ_CHUNK = 65536


class PtyError(Exception):
    """The shell could not be started."""


def default_shell_argv() -> list[str]:
    """The server OS default shell."""
    if IS_WINDOWS:
        exe = shutil.which("powershell.exe") or shutil.which("pwsh.exe") or "powershell.exe"
        return [exe, "-NoLogo"]
    candidates: list[str] = []
    try:
        import pwd

        candidates.append(pwd.getpwuid(os.getuid()).pw_shell or "")
    except Exception:  # noqa: BLE001 -- no passwd entry (containers): use the fallbacks
        pass
    candidates += [os.environ.get("SHELL") or "", "/bin/bash", "/bin/sh"]
    for shell in candidates:
        if not shell or os.path.basename(shell) in _NO_LOGIN_SHELLS:
            continue
        if os.path.isfile(shell) and os.access(shell, os.X_OK):
            return [shell, "-l"]
    return ["/bin/sh", "-l"]


def _is_secret_env(key: str) -> bool:
    upper = key.upper()
    if upper in _SECRET_ENV_EXACT:
        return True
    if upper.startswith("FLOWGATE_") and any(marker in upper for marker in _SECRET_ENV_MARKERS):
        return True
    return is_secret_key(key)


def _utf8_locale(value: Optional[str]) -> bool:
    text = (value or "").upper().replace("-", "")
    return "UTF8" in text


def shell_env(base: Optional[dict] = None) -> dict:
    """Environment for the shell: the server's own, minus its secrets, plus terminal basics."""
    env = {k: v for k, v in dict(os.environ if base is None else base).items() if not _is_secret_env(k)}
    if IS_WINDOWS:
        return env
    env["TERM"] = "xterm-256color"
    env.setdefault("COLORTERM", "truecolor")
    # Korean input/output needs a UTF-8 locale.
    fallback = "en_US.UTF-8" if sys.platform == "darwin" else "C.UTF-8"
    if env.get("LC_ALL"):
        if not _utf8_locale(env["LC_ALL"]):
            env["LC_ALL"] = fallback
    elif not (_utf8_locale(env.get("LC_CTYPE")) or (not env.get("LC_CTYPE") and _utf8_locale(env.get("LANG")))):
        env["LC_CTYPE"] = fallback
        env.setdefault("LANG", fallback)
    return env


# -- process table helpers ------------------------------------------------------------


def _proc_stat(pid: int) -> Optional[tuple[str, int, int]]:
    """(state, ppid, session) from /proc on Linux, else None."""
    try:
        with open(f"/proc/{pid}/stat", "rb") as handle:
            raw = handle.read().decode("ascii", "replace")
    except OSError:
        return None
    fields = raw[raw.rfind(")") + 2:].split()
    try:
        return fields[0], int(fields[1]), int(fields[3])
    except (IndexError, ValueError):
        return None


def _process_rows() -> list[tuple[int, int, int]]:
    """Every visible (pid, ppid, session); session is -1 where the OS does not say."""
    rows: list[tuple[int, int, int]] = []
    if os.path.isdir("/proc/self"):
        for name in os.listdir("/proc"):
            if not name.isdigit():
                continue
            stat = _proc_stat(int(name))
            if stat and stat[0] != "Z":
                rows.append((int(name), stat[1], stat[2]))
        return rows
    try:
        import psutil  # optional

        for proc in psutil.process_iter(["pid", "ppid", "status"]):
            info = proc.info
            if info.get("status") != getattr(psutil, "STATUS_ZOMBIE", "zombie"):
                rows.append((int(info["pid"]), int(info.get("ppid") or 0), -1))
        return rows
    except ImportError:
        pass
    except Exception:  # noqa: BLE001
        return rows
    if IS_WINDOWS:
        return rows
    try:
        out = subprocess.run(["ps", "-A", "-o", "pid=,ppid=,stat="], capture_output=True,
                             text=True, timeout=5, check=False).stdout
    except (OSError, subprocess.SubprocessError):
        return rows
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 3 and parts[0].isdigit() and parts[1].isdigit() and not parts[2].startswith("Z"):
            rows.append((int(parts[0]), int(parts[1]), -1))
    return rows


def descendants(pid: int, rows: Optional[list[tuple[int, int, int]]] = None) -> list[int]:
    """Every live process below ``pid`` (children, grandchildren, ...)."""
    rows = _process_rows() if rows is None else rows
    children: dict[int, list[int]] = {}
    for child, parent, _sid in rows:
        children.setdefault(parent, []).append(child)
    found: list[int] = []
    pending = list(children.get(pid, []))
    while pending:
        current = pending.pop()
        if current in found or current == pid:
            continue
        found.append(current)
        pending.extend(children.get(current, []))
    return found


def has_children(pid: int) -> bool:
    """Whether the shell is running something right now (the ``busy`` frame)."""
    return any(parent == pid for _child, parent, _sid in _process_rows())


def _session_members(sid: int, rows: list[tuple[int, int, int]]) -> list[int]:
    """Processes still in the shell's session -- catches double-forked children that were
    re-parented away from the shell but never left its session (Linux only)."""
    return [pid for pid, _parent, session in rows if session == sid and pid != sid]


def _alive(pid: int) -> bool:
    stat = _proc_stat(pid)
    if stat is not None:
        return stat[0] != "Z"
    if os.path.isdir("/proc/self"):
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _signal_all(pids, sig) -> None:
    for pid in pids:
        try:
            os.kill(pid, sig)
        except (ProcessLookupError, PermissionError, OSError):
            pass


# -- POSIX ----------------------------------------------------------------------------


def _set_winsize(fd: int, rows: int, cols: int) -> None:
    import fcntl
    import termios

    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))


class PosixPty:
    def __init__(self, argv: list[str], cwd: str, env: dict, cols: int, rows: int):
        import pty

        master, slave = pty.openpty()
        try:
            _set_winsize(slave, rows, cols)
            self._proc = subprocess.Popen(
                [sys.executable, "-I", "-c", _CTTY_SHIM, *argv],
                stdin=slave, stdout=slave, stderr=slave,
                cwd=cwd, env=env, start_new_session=True, close_fds=True,
            )
        except OSError as exc:
            os.close(master)
            raise PtyError(f"shell start failed: {exc.__class__.__name__}") from exc
        finally:
            os.close(slave)
        self._master = master
        self._fd_lock = threading.Lock()
        self._closed = False
        self._decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        self.pid = self._proc.pid

    def read(self) -> Optional[str]:
        while True:
            try:
                data = os.read(self._master, _READ_CHUNK)
            except InterruptedError:
                continue
            except OSError:  # EIO once the last slave-side process is gone; EBADF after close
                data = b""
            if not data:
                return None
            text = self._decoder.decode(data)
            if text:
                return text

    def write(self, text: str) -> None:
        data = text.encode("utf-8", "replace")
        while data and not self._closed:
            try:
                written = os.write(self._master, data)
            except InterruptedError:
                continue
            except OSError:
                return
            data = data[written:]

    def resize(self, cols: int, rows: int) -> None:
        if not self._closed:
            try:
                _set_winsize(self._master, rows, cols)
            except OSError:
                pass

    def has_children(self) -> bool:
        return has_children(self.pid)

    def exit_code(self) -> Optional[int]:
        return self._proc.poll()

    def wait(self, timeout: float) -> Optional[int]:
        try:
            return self._proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            return None

    def _targets(self) -> set[int]:
        rows = _process_rows()
        found = set(descendants(self.pid, rows)) | set(_session_members(self.pid, rows))
        if self._proc.poll() is None:
            found.add(self.pid)
        return found

    def terminate(self, grace: float = TERMINATE_GRACE_SECONDS) -> None:
        """End the shell and every process below it, then release the pty."""
        targets = self._targets()
        try:
            os.killpg(self.pid, signal.SIGHUP)
        except (ProcessLookupError, PermissionError, OSError):
            pass
        _signal_all(targets, signal.SIGHUP)
        _signal_all(targets, signal.SIGTERM)
        deadline = time.monotonic() + grace
        while time.monotonic() < deadline:
            self._proc.poll()
            if not any(_alive(pid) for pid in targets):
                break
            time.sleep(0.05)
        for _round in range(3):
            survivors = {pid for pid in targets | self._targets() if _alive(pid)}
            if not survivors:
                break
            _signal_all(survivors, signal.SIGKILL)
            time.sleep(0.05)
            self._proc.poll()
            targets = survivors
        try:
            self._proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            pass
        self.close()

    def close(self) -> None:
        with self._fd_lock:
            if self._closed:
                return
            self._closed = True
            try:
                os.close(self._master)
            except OSError:
                pass


# -- Windows (ConPTY) -----------------------------------------------------------------


class WindowsPty:
    def __init__(self, argv: list[str], cwd: str, env: dict, cols: int, rows: int, bundle_path=None):
        try:
            from .terminal_bundle import load_winpty
            PtyProcess = load_winpty(bundle_path).PtyProcess
        except ImportError as exc:
            raise PtyError(f"Terminal dependency could not load: {exc}") from exc
        try:
            self._proc = PtyProcess.spawn(argv, cwd=cwd, env=env, dimensions=(rows, cols))
        except Exception as exc:  # noqa: BLE001 -- winpty raises several types
            raise PtyError(f"shell start failed: {exc.__class__.__name__}") from exc
        self.pid = int(self._proc.pid)
        self._closed = False

    def read(self) -> Optional[str]:
        while True:
            try:
                text = self._proc.read(_READ_CHUNK)
            except EOFError:
                return None
            except Exception:  # noqa: BLE001 -- closed handle
                return None
            if text:
                return text
            if not self._proc.isalive():
                return None
            time.sleep(0.01)

    def write(self, text: str) -> None:
        if not self._closed:
            try:
                self._proc.write(text)
            except Exception:  # noqa: BLE001
                pass

    def resize(self, cols: int, rows: int) -> None:
        if not self._closed:
            try:
                self._proc.setwinsize(rows, cols)
            except Exception:  # noqa: BLE001
                pass

    def has_children(self) -> bool:
        try:
            import psutil

            return bool(psutil.Process(self.pid).children())
        except Exception:  # noqa: BLE001 -- psutil missing or the shell is gone
            return False

    def exit_code(self) -> Optional[int]:
        try:
            if self._proc.isalive():
                return None
            return self._proc.exitstatus
        except Exception:  # noqa: BLE001
            return None

    def wait(self, timeout: float) -> Optional[int]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            code = self.exit_code()
            if code is not None:
                return code
            time.sleep(0.05)
        return self.exit_code()

    def terminate(self, grace: float = TERMINATE_GRACE_SECONDS) -> None:
        try:
            import psutil

            try:
                root = psutil.Process(self.pid)
                procs = root.children(recursive=True) + [root]
            except psutil.Error:
                procs = []
            for proc in procs:
                try:
                    proc.kill()
                except psutil.Error:
                    pass
            psutil.wait_procs(procs, timeout=grace)
        except ImportError:
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(self.pid)], capture_output=True,
                           timeout=10, check=False)
        self.close()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._proc.close(force=True)
        except Exception:  # noqa: BLE001
            pass


def open_pty(argv: list[str], cwd: str, env: dict, cols: int, rows: int):
    """Start ``argv`` in ``cwd`` on a new pseudo-terminal of ``cols`` x ``rows``."""
    backend = WindowsPty if IS_WINDOWS else PosixPty
    return backend(argv, cwd, env, cols, rows)
