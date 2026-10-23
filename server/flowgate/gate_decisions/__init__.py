"""Confirm gate decisions: the persistent records (flowgate.v02.0085 T#1, D0007 §7.1 / §7.2).

The records of what a person decided at a confirm gate and what that decision did outside the Flow. This package is
the persistence base only; the decision intake, the Git / archive work and the screens are later cards.

- ``repository``     the decisions, the effects of each phase, the archive checkpoints and the cleanup records
                     (migration 158); a decision is never deleted and no delete cascades into it.
- ``history``        one ordered history per gate: the new decisions, and the old ``gate_confirmed`` /
                     ``gate_rejected`` run events as legacy entries whose Git / archive facts are unknown.
- ``settings_lock``  when a gate card's settings may change (D0007 §7.2 last paragraph).

The operation job kind ``gate_publish`` is registered in ``modules.flow_gate.services.git.job_store`` (migration 159);
its request key is the decision id.
"""
