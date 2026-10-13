"""Card memos (flowgate.v02.0056 T#3, 0002-CH turn 5 §9 / turn 6 §8 / turn 10 O3): the memos a person keeps on a
Designer card (Node) to steer the card's AI.

v0.2 feature package (D0006 §2.1), designed new: the v0.1 M memo document is not reused (it is to be retired and
is left as it is). A memo is not a document — no ``documents`` row, file, number or workflow event — so writing one
never reaches ``documents._try_close_parent_on_child_created`` and never closes an R / B, and saving one never
calls an AI. A memo the person turned on for AI reading (``ai_ref``) goes into the mention of every AI run of its
card through the ``card_memo_hooks`` port of ``modules.flow_gate``.

    memos           pure-ish rules: the memo fields, the change checks and the "User memos" section (length cap)
    repository      the ``graph_node_memos`` table (migration 149)
    routes          GET / POST /api/v1/flows/{flow_id}/nodes/{node_key}/memos,
                    PATCH / DELETE /api/v1/flows/{flow_id}/nodes/{node_key}/memos/{memo_id}
    legacy_adapter  the Graph envelope, RBAC, the Flow load / disposal check and Flow guard, called only
    integration     port handler registration (``card_memo_hooks.memo_section``)
"""
