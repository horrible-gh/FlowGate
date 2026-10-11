"""Card run settings (flowgate.v02.0053 T#2): the settings every Designer card (Node) keeps.

v0.2 feature package (D0006 §2.1). The settings used to live on v0.1 sequence rows, so a card placed
on a Graph Flow had none; now each card keeps its own. The Graph workflow
(``modules.flow_gate.graph_workflow``) is existing code: it only calls ``card_settings_ports`` when it
hands a node out and binds its result, the AI invoke engine only calls ``ai_run_end_hooks`` when a run
ends, and ``integration.register`` (startup) connects those ports to this package.

    fields          pure: the settings of each card kind, their values, defaults, the run plan, the
                    result type, what follows a result and the review loop request
    settings        read a card / change one value / what a node's AI start takes / its result type
    review_stage    what follows a node's AI result: the card's AI review, follow / human
    routes          GET / PATCH /api/v1/flows/{flow_id}/nodes/{node_key}/settings
    repository      the ``graph_node_settings`` table (migration 146) and which card a node key is
    legacy_adapter  the Graph envelope, Flow guard, run state, AI policy, review loop and approval, called only
    integration     port handler registration
"""
