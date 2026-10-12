"""Card documents (flowgate.v02.0053 T#1): the documents every Designer card owns.

v0.2 feature package (D0006 §2.1). The Graph workflow (``modules.flow_gate.graph_workflow``) is
existing code: it only calls ``card_document_ports`` at its draft save, Canvas read, run step and
R↔B root conversion, and ``integration.register`` (startup) connects those ports to this package.

    slots           pure: the lines of each card kind
    lines           the line rules (place / release / adopt a result / follow a root conversion / read)
    compose         a person writes a line that has no document (flowgate.v02.0056 T#3)
    routes          POST /api/v1/flows/{flow_id}/nodes/{node_key}/slots/{slot}/document (flowgate.v02.0056 T#3)
    repository      the ``graph_node_documents`` table (migration 145)
    legacy_adapter  the v0.1 document, numbering, file and Graph supersede helpers, called only
    integration     port handler registration
"""
