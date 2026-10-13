"""Card approval (flowgate.v02.0056 T#1): one person's approval for a whole Designer card (Node).

v0.2 feature package (D0006 §2.1). The Graph workflow (``modules.flow_gate.graph_workflow``) is existing
code: it judges whether a node's card awaits approval (``services.node_approval``, shown on every node run
as ``awaiting_approval``) and this package approves the card's documents through the v0.1 document approval,
whose Graph contact moves the node on.

    approval        the card's approval documents, their order and the approval (checks, then writes)
    routes          POST /api/v1/runs/{run_id}/nodes/{node_key}/approval
    legacy_adapter  the Graph envelope, run, node approval state, group AI lease and v0.1 approval, called only
    integration     the Graph's port: the card's documents not approved yet (a node is done only with none)
"""
