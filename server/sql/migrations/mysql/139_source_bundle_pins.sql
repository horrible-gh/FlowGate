-- 139_source_bundle_pins.sql
-- flowgate.default.0682 T#1 (D#1 3.7): Retention Pin for the Source Bundle a Test Basis
-- executes from. One row per TS (its current Basis); successor/re-approval replace the row,
-- Group cleanup deletes the Group's rows. TTL and explicit cleanup skip pinned Bundles.
-- Additive only.

CREATE TABLE IF NOT EXISTS source_bundle_pins (
 ts_document_id VARCHAR(191) PRIMARY KEY,
 bundle_id VARCHAR(64) NOT NULL,
 basis_id VARCHAR(64) NOT NULL, project_id VARCHAR(191) NOT NULL, group_id VARCHAR(191) NOT NULL,
 pinned_at VARCHAR(40) NOT NULL,
 FOREIGN KEY(bundle_id) REFERENCES source_bundles(bundle_id),
 INDEX idx_source_bundle_pins_bundle(bundle_id),
 INDEX idx_source_bundle_pins_group(group_id)
);
