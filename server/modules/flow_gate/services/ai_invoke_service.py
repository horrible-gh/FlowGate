"""AI invoke engine — the stable import path (flowgate.default.0501 T6).

The implementation lives in the `ai_invoke/` package next to this file, laid out along
NR0003 §12's module boundaries. This module is the compatibility surface §20 asks for
and nothing else: `ai_invoke.facade` names every symbol the package exports and where it
comes from, and the star import below re-binds that inventory here so that

    from modules.flow_gate.services import ai_invoke_service

keeps working for `ai_invoke_routes.py`, `invoke_mention_service.py`,
`mutation_policy.py` and the ~90 test modules that use it -- including the ones that
monkeypatch private names on it. Those patches are still observed inside the package:
its modules read a seam as `_svc().<name>`, an attribute lookup on THIS module performed
at call time, so replacing the attribute here still changes what every caller sees.

Nothing is defined here. To find a symbol, grep `ai_invoke/facade.py` for its name; the
import line beside it names the owning module.
"""

from __future__ import annotations

from .ai_invoke.facade import *  # noqa: F401,F403  (inventory: facade.__all__)
