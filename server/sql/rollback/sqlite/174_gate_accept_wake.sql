-- Roll back only the new intent fields; decisions, effects, refs and run events are retained.
ALTER TABLE gate_decisions DROP COLUMN accept_event_seq;
ALTER TABLE gate_decisions DROP COLUMN accept_wake;
