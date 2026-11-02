"""v0.2 card completion (flowgate.v02.0119 T#2, T-1): the completion record is the only source of a Graph node's done.

``completion`` records (idempotently) and applies a node's completion — cause ``ai_review_passed`` / ``no_review_submitted``
/ ``human_submitted``, actor ``system`` / ``ai`` (the real provider) / ``user``, the run's issuer as ``requested_by`` only —
and answers the Graph's ``card_completion_ports``. ``repository`` is the store (migration 169), ``legacy_adapter`` the one
place this package takes ``modules.flow_gate`` from, ``integration`` the startup registration.
"""
