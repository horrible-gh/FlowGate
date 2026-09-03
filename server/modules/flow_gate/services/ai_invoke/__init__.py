"""The AI invoke engine, as real modules (flowgate.default.0501 T6 / NR0003 §12).

Layout and responsibilities are NR0003 §12-§20:

    runtime.py       parameters + the in-memory run registry + scratch (§13)
    oracle.py        completion probes + pure helpers (§16)
    admission.py     provider/lease/token/worktree policy + start_run (§14)
    provider_api.py  HTTP/API transport (§15)
    provider_cli.py  subprocess/CLI transport (§15)
    worker.py        provider-neutral execution: fallback, retry, tool dispatch (§15)
    finalize.py      judge, stop classification, persistence, payload (§17)
    chain.py         continuous chain: status/cancel/pause/resume, handoff (§18)
    review.py        the review/rework gate (§19)
    diagnostics.py   run history/status read models
    facade.py        the `ai_invoke_service` surface, re-exported name by name (§20)

Import direction (NR0003 §28), enforced by test_ai_invoke_package_graph_0501.py:

    runtime  <- everything          (runtime imports nothing from this package)
    oracle   <- everything but runtime
    review   never imports chain    (chain imports review; §18)
    facade   is imported by nobody inside this package

This replaces the part-file assembly of 0497 -- which exec()'d one file's compiled
source into another module's globals() dict -- and the flat
`ai_invoke_*.py` siblings of 0501 T1-T5. Nothing here shares a globals() dict with
anything else, and no name arrives in a module without an import statement that names it.
"""
