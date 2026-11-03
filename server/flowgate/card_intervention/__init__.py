"""v0.2 card intervention (flowgate.v02.0119 T#3, T-2; 0004-CH seq 9 §1 / T-2, seq 13).

A general (non-gate) card in review is never approved by a person any more. When nothing completes it, it is one of two
states, told from the reason its automatic completion was not made (the B-1 ``auto_approval_skipped`` run event the card
stage already records, ``card_settings.legacy_adapter.record_approval_wait``):

- ``needs_intervention``: the card's own outcome. Its automatic rework ran first and the stage ended without a
  completion — the review loop's count spent, a hold, a stall, a failure (``review_not_passed:<stop reason>``) or its AI
  run ended abnormally (``run_not_clean:<stop code / end reason>``). Only that card's descendants wait (the Graph's
  ``stop_path`` way: a node not done propagates nothing, an AND join waits); independent branches go on. It moves again
  only by a memo and a rework / re-review request, or by the Time Machine — and by the person's explicit step / full
  command in two cases only (each revising 0119 T-2 within its own bounds; nothing is retried automatically):
  (flowgate.v02.0124 T#1, 0003-NR §5.1) when its reason is a review loop's spent budget
  (``reasons.command_rerequest_due``: ``review_not_passed:`` retry_exhausted / review_count_exhausted /
  total_timeout) on an assigned attempt an AI wrote and an AI reworks: the command requests its result review again as
  the next assignment ``seq`` (``card_settings.assigned_review.rerequest_review_locked``); and (flowgate.v02.0125 T#1)
  when its review count was spent on a revision no review answered: that command's re-review
  (``card_settings.assigned_review.command_review``). Every other reason, a person's document and a pre-feature
  attempt are not moved by a command.
- ``system_error``: the automatic completion path itself failed (``issuer_missing``, ``approval_refused``,
  ``review_not_started``, ``review_result_unknown``, …). It is never handed to a person: the run shows it in its error
  family and the run read's reconciliation tries the automatic path again (``retry_due``, bounded).

Nothing is converted: the reasons are the records the stage wrote before this package too, read with this meaning.

``reasons``  the classification of a recorded reason (pure)
``judge``    the ``card_settings_ports.intervention`` handler: the state of a card in review nothing completes
"""
