"""v0.2 card completion (flowgate.v02.0119 T#2, T-1): the completion record is the only source of a Graph node's done.

``completion`` records (idempotently) and applies a node's completion — cause ``ai_review_passed`` / ``no_review_submitted``
/ ``human_submitted``, actor ``system`` / ``ai`` (the real provider) / ``user``, the run's issuer as ``requested_by`` only —
and answers the Graph's ``card_completion_ports``. ``repository`` is the store (migration 169), ``legacy_adapter`` the one
place this package takes ``modules.flow_gate`` from, ``integration`` the startup registration.

flowgate.v02.0119 T#4 (T-3): the removed card approval (``flowgate.card_approval``) handed over what is not a person's
approval: ``outputs`` (the card's required outputs — the instruction it uses, approved — instead of every approval line's
document), ``instruction`` (the instruction's automatic approval, written by the system) and ``gates`` (the read-only gates
the v0.1 approval asked, now the completion's own judgement).
"""
