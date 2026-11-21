"""Group 0061 card 1 -- shared pieces of the prompt/response size measurement frame.

No behaviour change lives here: this module only *measures*. It holds
  * the exhaustive scan that finds every function building an AI-facing prompt,
  * the registry that says which measured cases cover which builder,
  * the baseline file (limits = measured size + 10 %), read and written in one place.

The cases themselves (which need monkeypatch and the real builders) live in
``test_prompt_size_baseline_0061.py``.
"""
from __future__ import annotations

import json
import math
import os
import re
from pathlib import Path

SERVER_DIR = Path(__file__).resolve().parents[4]
BASELINE_PATH = Path(__file__).with_name("prompt_size_baseline_0061.json")

# build_*mention*, _build_*mention*, build_prompt and the nested _standard_mention closure.
BUILDER_RE = re.compile(
    r"^\s*(?:async\s+)?def\s+(_?build_\w*mention\w*|_?build_prompt|_standard_mention)\s*\("
)
_SKIP_DIRS = {"tests", ".venv", "venv", "node_modules", "__pycache__", ".git", "sql", "templates"}

LIMIT_MARGIN = 1.10  # limit = measured + 10 % (requirement R5)


def builder_key(path: Path, name: str) -> str:
    return f"server/{path.relative_to(SERVER_DIR).as_posix()}::{name}"


def discover_builders() -> set[str]:
    """Every prompt-building function in the server source (tests excluded)."""
    found: set[str] = set()
    for root, dirs, files in os.walk(SERVER_DIR):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
        for filename in files:
            if not filename.endswith(".py"):
                continue
            path = Path(root) / filename
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            for line in text.splitlines():
                match = BUILDER_RE.match(line)
                if match:
                    found.add(builder_key(path, match.group(1)))
    return found


def limit_for(measured: int) -> int:
    return int(math.ceil(measured * LIMIT_MARGIN))


def load_baseline() -> dict:
    if not BASELINE_PATH.exists():
        return {"prompts": {}, "responses": {}}
    return json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
