-- 139_source_bundle_pins.sql
-- flowgate.default.0682 T#1 (D#1 3.7): Retention Pin for the Source Bundle a Test Basis
-- executes from. One row per TS (its current Basis); successor/re-approval replace the row,
-- Group cleanup deletes the Group's rows. TTL and explicit cleanup skip pinned Bundles.
-- Additive only.

CREATE TABLE IF NOT EXISTS source_bundle_pins (
 ts_document_id TEXT PRIMARY KEY,
 bundle_id TEXT NOT NULL REFERENCES source_bundles(bundle_id),
 basis_id TEXT NOT NULL, project_id TEXT NOT NULL, group_id TEXT NOT NULL,
 pinned_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_source_bundle_pins_bundle ON source_bundle_pins(bundle_id);
CREATE INDEX IF NOT EXISTS idx_source_bundle_pins_group ON source_bundle_pins(group_id);
