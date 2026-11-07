"""Per-test hang guard for the server suite (flowgate.v02.0146 T#2).

A test that hangs (a Git/subprocess call, a ``threading.Event`` nobody sets, a poll loop without exit)
used to be noticed only when the whole Self-check hit its 1800 s ceiling, with no hint which test it was.
This guard gives every test its own limit instead, and names the test that exceeded it.

* Limit: ``FLOWGATE_TEST_TIMEOUT`` seconds (default 900, ``0`` turns the guard off), or per test / class /
  module ``@pytest.mark.timeout(seconds)`` — the same marker name ``pytest-timeout`` uses, so the plugin can be
  installed later without touching a test. The guard stays active when that plugin is loaded (the plugin has no
  limit of its own unless one is configured, so standing down would silently drop this contract); the first
  limit reached wins. ``pytest-timeout`` is not installed on the Self-check host, which is why the guard exists.
* Scope: the whole test item — setup, call and teardown — so a fixture that hangs is covered too.
* Failure, never a pass: when the limit is reached the guard prints the test id, the limit and every thread's
  stack to stderr and ends the pytest process with exit status 70. A thread-based stop works the same on
  Windows and POSIX and also breaks a call blocked in C (a signal handler cannot). Under ``pytest-xdist``
  that worker crash is reported by xdist as a failed test ("worker crashed while running ...") and the other
  workers go on; in a serial run the run ends non-zero at once instead of consuming the Self-check ceiling.
* The limit must never be raised to hide a slow test; it only has to sit above the slowest legitimate one
  (about 200 s on a one-processor host for the Git-heavy card settings tests, see TESTING.md).
"""
from __future__ import annotations

import faulthandler
import os
import sys
import threading

import pytest

ENV_NAME = "FLOWGATE_TEST_TIMEOUT"
DEFAULT_SECONDS = 900.0
EXIT_STATUS = 70
_PLUGIN_NAME = "flowgate-hang-guard"


def limit_for(item) -> float:
    """Seconds this item may run; 0 means unlimited. The marker beats the environment, which beats the default."""
    marker = item.get_closest_marker("timeout")
    if marker is not None:
        raw = marker.args[0] if marker.args else marker.kwargs.get("timeout", marker.kwargs.get("seconds"))
    else:
        raw = os.environ.get(ENV_NAME)
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return DEFAULT_SECONDS
    return value if value > 0 else 0.0


def _expired(nodeid: str, seconds: float, capture) -> None:
    stream = sys.__stderr__ or sys.stderr
    try:
        if capture is not None:
            try:
                capture.suspend_global_capture(in_=True)    # fd-level capture would swallow the message below
            except Exception:
                pass
        stream.write(f"\n+++ FLOWGATE HANG GUARD: {nodeid} exceeded its {seconds:g}s test limit "
                     f"({ENV_NAME} / @pytest.mark.timeout); stacks of all threads follow +++\n")
        stream.flush()
        faulthandler.dump_traceback(file=stream, all_threads=True)
        stream.flush()
    finally:
        os._exit(EXIT_STATUS)


class HangGuard:
    def __init__(self, config):
        self._config = config

    @pytest.hookimpl(hookwrapper=True)
    def pytest_runtest_protocol(self, item, nextitem):
        seconds = limit_for(item)
        timer = None
        if seconds:
            capture = self._config.pluginmanager.getplugin("capturemanager")
            timer = threading.Timer(seconds, _expired, args=(item.nodeid, seconds, capture))
            timer.daemon = True
            timer.start()
        try:
            yield
        finally:
            if timer is not None:
                timer.cancel()


def install(config) -> bool:
    """Register the guard once; False when it is already installed.

    The guard does not stand down when ``pytest-timeout`` is loaded: that plugin has no limit unless one is given
    (``--timeout``, ini ``timeout``, ``PYTEST_TIMEOUT``), so deferring to it silently dropped the
    ``FLOWGATE_TEST_TIMEOUT`` / 900 s / ``0`` contract. Both can be active; whichever limit is reached first fails
    the run.
    """
    manager = config.pluginmanager
    if manager.has_plugin(_PLUGIN_NAME):
        return False
    manager.register(HangGuard(config), _PLUGIN_NAME)
    return True


def pytest_configure(config):
    # Lets the guard also be loaded on its own: ``pytest -p hang_guard`` (tests/test_hang_guard_0146.py).
    install(config)
