"""v02gui.0031 D: the AI role matrix (class ai-base-select on every model select) in the real app path.

Runs the existing opt-in 0120 Chromium test (real SQLite/API through TestClient, real Vue components)
with its scratch directory supplied here, so selection -> save -> reload, inherit / not_used values,
the general-group preservation and the 390px layout are exercised with the base-select picker in place.
The 0120 test is not edited; this wrapper only falls back to an installed Chromium when Playwright's own
cache has none (the same fallback the 0144 layout test uses).
"""
import glob
import os

import pytest

import test_ai_role_entry_browser_0120 as role_entry
from test_ai_group_role_profiles_0120 import world  # noqa: F401 - the real DB/API fixture

playwright = pytest.importorskip('playwright.sync_api')


def _installed_chromium():
    explicit = os.environ.get('FG_CHROMIUM_PATH')
    if explicit:
        return explicit
    found = sorted(glob.glob('C:/Users/*/AppData/Local/ms-playwright/chromium-*/chrome-win*/chrome.exe'))
    return found[-1] if found else None


def test_role_matrix_selection_saves_and_reloads_with_base_select(world, tmp_path, monkeypatch):
    original = playwright.BrowserType.launch

    def launch(self, **kwargs):
        try:
            return original(self, **kwargs)
        except Exception:
            executable = _installed_chromium()
            if not executable:
                raise
            return original(self, executable_path=executable, **kwargs)

    monkeypatch.setattr(playwright.BrowserType, 'launch', launch)
    monkeypatch.setenv('FG_BROWSER_ARTIFACTS', str(tmp_path / 'artifacts'))
    role_entry.test_role_entry_create_save_refresh_and_general_preservation(world)
