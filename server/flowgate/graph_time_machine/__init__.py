"""Graph Time Machine: rewind a done node and everything below it.

read-only impact and safety checks (``impact``, ``repository``, ``service``). the earlier implementation: the operation itself —
``POST /runs/{run_id}/nodes/{node_key}/time-machine`` (``routes``, ``operation``): its journal in the run event log
(``journal``), the per-merge Git reverts with compensation (``git_revert``), the DB rewind (``rewind``) and the server
start's recovery of an unfinished operation.
"""
