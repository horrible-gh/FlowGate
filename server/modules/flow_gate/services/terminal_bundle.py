"""Pinned terminal bundle paths and imports; never install into site-packages."""
import importlib
import json
from pathlib import Path
import os
import shutil
import platform
import sys
import sysconfig
import threading

from modules.flow_gate.storage.paths import get_storage_root
from .terminal_errors import BundleError

CATALOG = json.loads(Path(__file__).with_name("terminal_catalog.json").read_text(encoding="utf-8"))
_import_lock = threading.RLock()


def root():
    return get_storage_root().resolve() / "terminal"


def required():
    return sys.platform == "win32"


def specification():
    python = f"cp{sys.version_info.major}{sys.version_info.minor}"
    abi = python + ("t" if sysconfig.get_config_var("Py_GIL_DISABLED") else "")
    arch = sysconfig.get_platform().replace("-", "_")
    spec = next((a for a in CATALOG["artifacts"]
                 if a["python"] == python and a["abi"] == abi and a["platform"] == arch), None)
    if spec is None:
        raise BundleError("terminal_platform_unsupported", "No pinned wheel for this Python ABI and architecture", 409)
    return spec


def active_path():
    try:
        marker = json.loads((root() / "installed.json").read_text(encoding="utf-8"))
        spec = specification()
        if marker.get("sha256") != spec["sha256"]:
            return None
        path = root() / "bundles" / spec["sha256"][:16]
        if (path / "winpty" / "__init__.py").is_file():
            return path
    except (OSError, ValueError, BundleError):
        pass
    return None


def installed():
    return not required() or active_path() is not None


def prune_runtime():
    import psutil
    for directory in (root() / "runtime").glob("*"):
        if directory.name.isdigit() and not psutil.pid_exists(int(directory.name)):
            shutil.rmtree(directory, ignore_errors=True)


def load_winpty(directory=None):
    live_import = directory is None
    directory = Path(directory) if directory is not None else active_path()
    if directory is None:
        raise ImportError("Install terminal shell components in System Settings")
    with _import_lock:
        if live_import:
            prune_runtime()
            cached = root() / "runtime" / str(os.getpid()) / directory.name
            if not cached.exists():
                cached.parent.mkdir(parents=True, exist_ok=True)
                shutil.copytree(directory, cached)
            directory = cached
        prefix = str(directory.resolve())
        module = sys.modules.get("winpty")
        if module is not None and not Path(module.__file__).resolve().is_relative_to(directory.resolve()):
            for name in list(sys.modules):
                if name == "winpty" or name.startswith("winpty."):
                    del sys.modules[name]
        if prefix not in sys.path:
            sys.path.insert(0, prefix)
        importlib.invalidate_caches()
        module = importlib.import_module("winpty")
        if not Path(module.__file__).resolve().is_relative_to(directory.resolve()):
            raise ImportError("winpty did not load from the terminal bundle")
        return module
