"""AI roles storage (flowgate.v02.0113 T#1, DB-1).

The storage layer of the role feature: account role defaults, card role slots and card init state
(``role_store``, migration 163), stage assignments (``assignment_store``, migration 164) and role invocation
records (``record_store``, migration 165). Each store tells on its own whether its tables exist (``available``),
so a DB with only some of the three migrations applied still works (DB-1 §3.1).

Only rows and the statements DB-1 §4 fixes live here. Which value a role resolves to, who a stage is assigned
to and when a record is made belong to L-1 / L-2 / L-3 (later cards); the API shapes belong to P-1.
"""
