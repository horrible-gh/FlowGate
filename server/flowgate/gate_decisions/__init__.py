"""Confirm gate decisions: the persistent records (flowgate.v02.0085 T#1, D0007 §7.1 / §7.2).

The records of what a person decided at a confirm gate and what that decision did outside the Flow. This package is
the persistence base only (T#1); T#2 adds the decision intake; the Git / archive work and the screens are later cards.

- ``repository``     the decisions, the effects of each phase, the archive checkpoints and the cleanup records
                     (migration 158); a decision is never deleted and no delete cascades into it.
- ``history``        one ordered history per gate: the new decisions, and the old ``gate_confirmed`` /
                     ``gate_rejected`` run events as legacy entries whose Git / archive facts are unknown.
- ``settings_lock``  when a gate card's settings may change (D0007 §7.2 last paragraph).
- ``intake``         flowgate.v02.0085 T#2: the decision intake of the product gate rule (``human_confirm_v1``): the
                     request key and its replay, the D0007 §3.1 refusals, the reject at once, the confirm done at once
                     or accepted with its ``gate_publish`` job, and the gate part of a run read.
- ``preflight``      the confirm's settings snapshot and the Git checks made before anything is accepted (D0007 §7.1).
- ``routes``         ``GET /runs/{run_id}/nodes/{node_key}/gate-decisions`` (the history, cursor pages).
- ``legacy_adapter`` everything taken from ``modules.flow_gate``, called only.

The operation job kind ``gate_publish`` is registered in ``modules.flow_gate.services.git.job_store`` (migration 159);
its request key is the decision id.
"""
