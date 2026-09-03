"""flowgate.default.0501 T5: every `svc.<name>` reference in the five split ai_invoke
modules is a genuine, individually-justified cross-module wire -- not the "import the
whole service and reach through it for everything" hub T0012 §12/§27 forbids.

T0012 §13-15 requires T5 to keep every EXISTING `monkeypatch.setattr(<alias of
ai_invoke_service>, "<name>", fake)` seam working, and §8 forbids a direct
ai_invoke_chain <-> ai_invoke_review import cycle (so the handful of names
ai_invoke_review.py needs back from ai_invoke_chain.py go through ai_invoke_service
instead, by design). Those are the ONLY two reasons a name may be reached as
`svc.<name>` inside one of the five split modules. This test makes that inventory
mechanical instead of asserted in prose: it collects every `svc.<name>` call site in
the five files, collects every name any test file actually monkeypatches on ANY alias
of `ai_invoke_service` (not just the literal spelling `svc` -- test files alias it as
`service`, `invoke`, `ais`, `aiv`, `ai_svc`, ... too), and fails loudly, naming the
exact unjustified symbol, if a future edit adds an `svc.<name>` reference that is
neither an established seam nor the documented cycle-break primitive.
"""
from __future__ import annotations

import re
from pathlib import Path

_SERVER_DIR = Path(__file__).resolve().parents[1]
_SERVICES_DIR = _SERVER_DIR / "modules" / "flow_gate" / "services"
_TESTS_DIR = Path(__file__).resolve().parent

# The five T5 split modules that import ai_invoke_service as `svc` for cross-module
# wiring (ai_invoke_runtime.py and ai_invoke_helpers.py use the same pattern but are
# out of T5's scope and audited separately).
_SPLIT_MODULES = (
    "ai_invoke_worker",
    "ai_invoke_chain",
    "ai_invoke_review",
    "ai_invoke_provider_cli",
    "ai_invoke_provider_api",
)

# T0012 §8: ai_invoke_chain.py <-> ai_invoke_review.py may not import each other
# bidirectionally. ai_invoke_chain.py imports ai_invoke_review.py directly (allowed --
# only one direction of the pair). The reverse handful of names -- ai_invoke_review.py
# reaching ai_invoke_chain.py's own handoff/auto-resume primitives -- keep going
# through ai_invoke_service instead of a forbidden direct import back.
_CYCLE_BREAK_EXCEPTIONS = {
    "ai_invoke_review": {
        "_park_handoff",
        "_clear_handoff_row",
        "_spawn_auto_resume",
        "clear_auto_resume",
        "request_auto_resume",
    },
}

_SVC_USE_RE = re.compile(r"\bsvc\.([A-Za-z_][A-Za-z0-9_]*)")

_ALIAS_IMPORT_RE = re.compile(
    r"from modules\.flow_gate\.services import ai_invoke_service as (\w+)"
)
_ALIAS_IMPORT_RE2 = re.compile(
    r"import modules\.flow_gate\.services\.ai_invoke_service as (\w+)"
)
_BARE_IMPORT_RE = re.compile(
    r"from modules\.flow_gate\.services import ai_invoke_service\b(?! as)"
)


def _svc_usages_in(module_name: str) -> set[str]:
    source = (_SERVICES_DIR / f"{module_name}.py").read_text(encoding="utf-8")
    # This file's own import line (`... import ai_invoke_service as svc`) does not
    # itself match `svc.<name>`, so no exclusion is needed for it.
    return set(_SVC_USE_RE.findall(source))


def _monkeypatched_seam_names() -> set[str]:
    """Every name any test monkeypatches on any alias of ai_invoke_service.

    Deliberately alias-agnostic: a patch on `ai_invoke_service.<name>` is the same
    patch regardless of what local name a given test file imported it under, and
    checking only the literal spelling `svc` undercounts real seams (several existing
    tests alias it `service`, `invoke`, `ais`, `aiv`, `ai_svc`, `svc_invoke`, ...).
    """
    seams: set[str] = set()
    for path in _TESTS_DIR.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        aliases = set(_ALIAS_IMPORT_RE.findall(text))
        aliases |= set(_ALIAS_IMPORT_RE2.findall(text))
        if _BARE_IMPORT_RE.search(text):
            aliases.add("ai_invoke_service")
        if not aliases:
            continue
        for alias in aliases:
            pattern = re.compile(
                r"setattr\(\s*" + re.escape(alias) + r"\s*,\s*[\"'](\w+)[\"']"
            )
            seams.update(pattern.findall(text))
    return seams


def test_every_svc_reference_in_split_modules_is_a_justified_seam_or_cycle_break():
    seam_names = _monkeypatched_seam_names()
    assert seam_names, "seam scan found nothing -- the alias/regex scan itself is broken"

    unjustified: dict[str, set[str]] = {}
    for module_name in _SPLIT_MODULES:
        used = _svc_usages_in(module_name)
        allowed = seam_names | _CYCLE_BREAK_EXCEPTIONS.get(module_name, set())
        leftover = used - allowed
        if leftover:
            unjustified[module_name] = leftover

    assert not unjustified, (
        "svc.<name> reference(s) in the T5 split modules are neither an established "
        "monkeypatch seam (T0012 §13-15) nor the documented ai_invoke_chain/"
        "ai_invoke_review cycle-break primitive (T0012 §8) -- route these through a "
        "direct `from . import ai_invoke_<owner>` instead: "
        f"{unjustified}"
    )
