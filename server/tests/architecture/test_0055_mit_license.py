"""Regression checks for the FlowGate v0.2 MIT license decision."""
from pathlib import Path

import pytest


@pytest.fixture
def repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def test_root_license_keeps_mit_grant_and_copyright(repo_root: Path) -> None:
    license_text = (repo_root / "LICENSE").read_text(encoding="utf-8")

    assert license_text.startswith("MIT License\n")
    assert "Copyright (c) 2026 horrible-gh" in license_text
    assert "Permission is hereby granted, free of charge" in license_text
    assert "The above copyright notice and this permission notice" in license_text
    assert "FSL-1.1-ALv2" not in license_text


def test_readme_describes_v02_as_mit(repo_root: Path) -> None:
    readme = (repo_root / "README.md").read_text(encoding="utf-8")
    license_section = readme.split("## License\n", 1)[1]

    assert "FlowGate v0.2 is available under the [MIT License](LICENSE)" in license_section
    assert "Copyright (c) 2026 horrible-gh" in license_section
    assert "FSL" not in license_section
    assert "source-available" not in license_section