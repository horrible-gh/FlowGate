"""Work plan deploy (flowgate.v02.0074 T#4, D0006 §3.1 ~ §3.6): a WP card's plan becomes Designer cards.

The steps of the work plan a Designer WP card holds are projected onto cards of the same Flow's Graph draft: one card
per WP card id (N + NR, T + TR, TS + TSR, D / P / L / DB), chained after the WP card in plan order. A step's
pre-instruction becomes the body of its card's instruction line (N / T: the instruction, design: DS) while that line
is still the card's untouched empty draft; the card's run then approves it and starts its result stage without an
instruction AI (the existing ``_instruction_job``, no new engine). Nothing is confirmed or run here.

- ``plan``        pure: the deploy targets of a plan, its digests, the card settings a new card starts with
- ``chain``       pure: the deploy chain after the WP card and the edge edits that keep it linear
- ``protection``  whether a card document is the card's untouched empty draft (written / removed only then)
- ``service``     status, preview and execution (WP save lock, Flow guard, WP row lock, restorable files)
- ``routes``      ``/api/v1/documents/{doc_id}/work-plan/deploy`` (status · preview · execution)
- ``legacy_adapter``  everything taken from ``modules.flow_gate``, called only
"""
