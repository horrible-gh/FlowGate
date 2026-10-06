"""FlowGate v0.2 application package (flowgate.v02.0032 D0006 §2.1).

New and substantially reworked server code lives here; the existing
``modules.flow_gate`` tree stays where it is. Dependencies point one way only:
``flowgate`` may reuse ``modules.flow_gate``, never the reverse (the AI invoke engine
reaches this package through ``modules.flow_gate.services.chat_run_hooks`` handlers that
the composition root registers).
"""
