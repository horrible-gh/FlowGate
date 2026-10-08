"""Graph Workflow package (flowgate.v02.0045 D0004 §2.2).

Dependency direction is fixed (D0004 §2.1, guarded by test_graph_workflow_structure_0045):

    api  ->  services  ->  projection / store / domain
                           projection -> domain (+ v0.1 db read modules: documents, groups,
                                         workflow_sequences — read only)
                           store      -> (db.connection only; writes graph_* tables only)
    domain imports nothing outside the standard library.

S1+S2 scope (T0011): Canvas read API, legacy projector (sequence / document projection +
reference edges), read-only ownership judgement, layout store, node identity keeper with
rematch, layout (positions + viewport) save. Graph definition / assignment / run storage
arrive in S3~S5 and are not referenced here.
"""
