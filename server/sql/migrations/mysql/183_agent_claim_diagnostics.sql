-- flowgate.v02test.0022 CH0002: say why an AI-run Job ended not_claimed.
-- agents.last_claim_poll_at: last claim call that passed authentication (AgentJobService.claim).
-- agents.last_claim_refusal_code / _at: last claim call the server refused (agent_disabled,
--   capability_required, ...). Written in its own transaction because the claim rolls back.
-- agent_jobs.claim_wait_cause: the server's verdict when the claim wait ran out
--   (agent_offline, claim_refused, polled_not_taken, agent_busy, claim_polling_stopped, agent_missing).
-- All columns are nullable with no default: existing rows and older servers are unaffected.
ALTER TABLE agents ADD COLUMN last_claim_poll_at VARCHAR(40) NULL;
ALTER TABLE agents ADD COLUMN last_claim_refusal_code VARCHAR(64) NULL;
ALTER TABLE agents ADD COLUMN last_claim_refusal_at VARCHAR(40) NULL;
ALTER TABLE agent_jobs ADD COLUMN claim_wait_cause VARCHAR(64) NULL;
