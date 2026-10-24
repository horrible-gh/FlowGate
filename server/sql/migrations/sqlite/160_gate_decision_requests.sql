-- 160_gate_decision_requests.sql — flowgate.v02.0085 T#3 (D0007 §7.2 retry / cancel / resolve)
-- The request keys of the recovery actions on an accepted gate decision. One row per accepted request: the key is
-- (decision, actor, action, request id); the fingerprint is the request's content, so the same key with the same
-- content answers the stored result and the same key with other content is refused (gate_request_conflict).
-- A refused request stores nothing. Additive: no run, decision, job or snapshot is changed. No REFERENCES: the rows
-- stay with the decision history they belong to.
BEGIN;

CREATE TABLE IF NOT EXISTS gate_decision_requests (
    decision_id          TEXT NOT NULL,
    actor_user_id        TEXT NOT NULL,
    action               TEXT NOT NULL CHECK (action IN ('retry', 'cancel', 'resolve')),
    request_id           TEXT NOT NULL,
    request_fingerprint  CHAR(64) NOT NULL,
    http_status          INTEGER NOT NULL,
    response_json        TEXT NULL,
    created_at           TEXT NOT NULL,
    PRIMARY KEY (decision_id, actor_user_id, action, request_id)
);
CREATE INDEX IF NOT EXISTS idx_gate_decision_requests_decision ON gate_decision_requests (decision_id, created_at);

COMMIT;
