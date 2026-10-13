"""Graph run state (flowgate.v02.0061 T#2): whether a Graph run goes on, and the step / full command.

v0.2 feature package (D0006 §2.1). The Graph workflow (``modules.flow_gate.graph_workflow``) is existing code: it keeps
the run, its nodes and its events, its resolver (``domain.frontier``) computes the run candidates, and it only calls
``run_state_ports`` after a step and when a paused / idle run goes on; ``integration.register`` (startup) connects
those ports to this package. The engine itself is not moved here. Nothing is registered on import.

    settle          after a step: a running run with no work going on opens what its command may still open, else
                    goes idle with its reason (completed, no_targets, waiting_approval, blocked_rejected, error);
                    an idle run whose existing work goes on again (a card's review started) is running again; an
                    idle run's reason follows its nodes
    command         the command a paused / idle run goes on with, recorded before its run status event, and a
                    step's targets (the nodes its command opens)
    legacy_adapter  the run store, the run step writes, the resolver's candidates and the node approval state of
                    the Graph workflow, called only
    integration     port handler registration
"""
