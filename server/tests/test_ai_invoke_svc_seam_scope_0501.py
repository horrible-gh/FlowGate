"""flowgate.default.0501 T6: every `_svc().<name>` reference in the ai_invoke package
is a genuine, individually-justified compatibility seam -- not the "import the whole
service and reach through it for everything" hub T0012 §12/§27 forbids.

T6 moved the engine into `ai_invoke/` (NR0003 §12) and, with it, changed the SPELLING of
the seam from a module-level `import ai_invoke_service as svc` to a call-time `_svc()`.
The reason is §28: with the shim imported at module level the package's own graph is
cyclic (`ai_invoke_service` -> `facade` -> every module -> `ai_invoke_service`) and a
fresh interpreter entering at `facade` cannot load it. Resolving the shim at call time is
what makes the graph one-way while leaving every existing monkeypatch working, and this
test's inventory is unchanged in substance: a name may be reached through the shim only
because a test patches it there, or because it is the one documented cycle break.

T0012 §13-15 requires T5 to keep every EXISTING `monkeypatch.setattr(<alias of
ai_invoke_service>, "<name>", fake)` seam working, and §8 forbids a direct
ai_invoke_chain <-> ai_invoke_review import cycle (so the handful of names
ai_invoke_review.py needs back from ai_invoke_chain.py go through ai_invoke_service
instead, by design). T6/NR0003 §28 adds a second, parallel case: `worker.py` may not
import `chain.py` either (chain.py reaches admission -> ... and finalize.py, and
finalize.py reaches admission -- an import from worker back to chain would close a
cycle through either), so the two post-hop auto-resume names worker.py needs from
chain.py go through the same seam. Those are the ONLY reasons a name may be reached as
`svc.<name>` inside one of the split modules. This test makes that inventory
mechanical instead of asserted in prose: it collects every `svc.<name>` call site in
the package's modules, collects every name any test file actually monkeypatches on ANY
alias of `ai_invoke_service` (not just the literal spelling `svc` -- test files alias it
as `service`, `invoke`, `ais`, `aiv`, `ai_svc`, ... too), and fails loudly, naming the
exact unjustified symbol, if a future edit adds an `svc.<name>` reference that is
neither an established seam nor a documented cycle-break primitive.
"""
from __future__ import annotations

import re
from pathlib import Path

_SERVER_DIR = Path(__file__).resolve().parents[1]
_SERVICES_DIR = _SERVER_DIR / "modules" / "flow_gate" / "services"
_TESTS_DIR = Path(__file__).resolve().parent

# Every module of the package, globbed rather than listed: a hardcoded file list is
# exactly the "physical filenames became a test contract" problem NR0003 §9 named, and
# it silently stops covering whatever the next split adds. facade.py is excluded because
# re-exporting the surface IS its job.
_PKG_DIR = _SERVICES_DIR / "ai_invoke"
_SPLIT_MODULES = tuple(
    sorted(p.stem for p in _PKG_DIR.glob("*.py")
           if p.stem not in ("__init__", "facade"))
)

# T0012 §8 / NR0003 §18: chain.py <-> review.py may not import each other
# bidirectionally. chain.py imports review.py directly (allowed -- only one direction of
# the pair). The reverse handful of names -- review.py reaching chain.py's own
# handoff/auto-resume primitives -- keep going through the ai_invoke_service shim
# instead of a forbidden direct import back.
#
# NR0003 §28 (T6): the same shape recurs between worker.py and chain.py. worker.py
# reaches chain.py's post-hop auto-resume machinery (spawn the next hop if the inbox
# self-chain queued one; drop it and park a durable row on a crash), but chain.py itself
# reaches admission.py and finalize.py, and finalize.py reaches admission.py too -- a
# direct worker -> chain import would close a cycle back through either path. So these
# two names keep going through the shim exactly like review.py's handful above.
_CYCLE_BREAK_EXCEPTIONS = {
    "review": {
        "_park_handoff",
        "_clear_handoff_row",
        "_spawn_auto_resume",
        "clear_auto_resume",
        "request_auto_resume",
    },
    "worker": {
        "_maybe_auto_resume_hop",
        "pop_auto_resume",
    },
}

_SVC_USE_RE = re.compile(r"_svc\(\)\.([A-Za-z_][A-Za-z0-9_]*)")

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
    source = (_PKG_DIR / f"{module_name}.py").read_text(encoding="utf-8")
    # The `_svc()` helper's own definition and docstring contain no `_svc().<name>`
    # call, so no exclusion is needed for them.
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
    assert _SPLIT_MODULES, "the ai_invoke package glob found nothing -- scan is broken"
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
        "_svc().<name> reference(s) in the ai_invoke package are neither an established "
        "monkeypatch seam (T0012 §13-15) nor the documented chain/review cycle-break "
        "primitive (T0012 §8) -- route these through a direct `from . import <owner>` "
        f"instead: {unjustified}"
    )
