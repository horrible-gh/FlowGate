"""Graph execution policies: the decided rules of the Graph workflow's policy slots.

v0.2 feature package. The Graph workflow (``modules.flow_gate.graph_workflow``) is existing
code: its resolver (``domain.frontier``) asks a policy slot whenever a revision's structure needs one, and
its validator reports such a structure as ``rule_undecided`` until a rule is registered for the slot. This
package holds the rules decided in 0061 and ``integration.register`` (startup) registers them; the engine
itself is not moved here. Nothing is registered on import.

    start           pure: the start — ``start_node`` = ``root_start`` opens the requirement / bug nodes of
                    ``flowgate.graph_validation.run_scope`` (an isolated root node and a path they do not reach
                    are left out; several start nodes stay undecided, Q-1)
    fork            pure: a branch (several outgoing edges) — ``edge_condition`` = ``fork_all`` follows every
                    edge, ``parallelism`` = ``unbounded`` admits every reached node
    join            pure: a join (several incoming edges) — ``join`` = ``and_all`` opens the node once, after
                    every predecessor is approved
    gate            the confirm card: ``gate_node`` = ``human_confirm_v1`` - a person's confirm / reject; its
                    ``intake`` is ``flowgate.gate_decisions.intake``
    legacy_adapter  the slot registries and the validator's issue shape of the Graph workflow, called only
    integration     registration of every decided slot
"""
