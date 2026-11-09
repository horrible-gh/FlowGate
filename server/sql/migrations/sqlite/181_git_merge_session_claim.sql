-- 181_git_merge_session_claim.sql
-- flowgate.v02.0153 T#4 (T0011 §4.2): DB session claim/lease for the automatic processing of one
-- merge session (sweep / startup / reconcile). Two server instances on the same DB and repository
-- must never run that processing for the same session at the same time, the row also records who
-- processed it last. Additive only: no existing table is changed.
--
--   claim_token            NULL = free. A random token per acquisition, every write is a CAS on it.
--   owner_instance_id      server_instance.instance_id of the holder. No FK (the 158 precedent):
--                          instance rows are retired independently of this evidence.
--   purpose                sweep | startup | reconcile
--   claimed_at/lease_until now_iso() strings, an expired lease or a DEAD owner may be reclaimed.
--   last_*                 the previous holder, its purpose, its outcome (closed_orphan | hold |
--                          resumed | reconcile:<status> | skipped | ..., a reclaimed claim appends
--                          "/reclaimed_from:<instance>") and its release time.
--
-- Claim state is deliberately NOT kept in git_merge_session.context: that column is written whole,
-- without a CAS, by the session code paths.
--
-- Rollback = DROP TABLE git_merge_session_claim (loses only claim bookkeeping, never session data).

CREATE TABLE IF NOT EXISTS git_merge_session_claim (
    merge_id               INTEGER      NOT NULL PRIMARY KEY REFERENCES git_merge_session(merge_id) ON DELETE CASCADE,
    claim_token            VARCHAR(64)  NULL,
    owner_instance_id      VARCHAR(37)  NULL,
    purpose                VARCHAR(32)  NULL,
    claimed_at             VARCHAR(40)  NULL,
    lease_until            VARCHAR(40)  NULL,
    last_owner_instance_id VARCHAR(37)  NULL,
    last_purpose           VARCHAR(32)  NULL,
    last_outcome           VARCHAR(128) NULL,
    last_released_at       VARCHAR(40)  NULL
);
