"""Graph pre-run validation (flowgate.v02.0061, 0002-CH turn 13): the decided rules of what a draft must look
like before it is confirmed and run.

v0.2 feature package (D0006 §2.1). The Graph workflow (``modules.flow_gate.graph_workflow``) is existing code:
its validator (``domain.graph_validator``) reports every problem of a draft at once and, through
``services.graph_validation_ports``, lets a registered handler validate in its place. This package is that
handler. The validator's pieces (the issue shape and order, the well-formedness checks, the graph walks, the
structures put to the policy slots) are reused, not copied, and the engine itself is not moved here. Nothing
is registered on import.

The rules (R §2.4 / §2.5, 0002-CH turns 7 / 13):

    1  an isolated node (no edge at all) is left out of a run — no issue
    2  a path not starting from a requirement / bug node (R / B) is left out of a run as a whole — no issue
    3  a left-out path joining the run path (an edge from a left-out node into a run node) — ``excluded_path_join``
    4  a cycle — ``cycle``
    5  a branch and an AND join are allowed (the slots ``flowgate.graph_policies`` registers decide them)

    run_scope       pure: the run start nodes and the nodes a run may reach (rules 1-2); an isolated root node
                    is no start (turn 15). ``flowgate.graph_policies.start`` (the ``start_node`` slot) opens a
                    run from ``run_scope.run_starts``
    validator       pure: ``validate_graph(graph, policies) -> issues`` (rules 1-5)
    legacy_adapter  the validator pieces and the port of the Graph workflow, called only
    integration     registration of the validator at the port
"""
