-- 128_tr_self_check_default_on.sql
-- flowgate.default.0652 T0002: TR Self-check becomes the default (ON). Existing projects are moved to ON
-- once by this migration; an explicit OFF saved afterwards is preserved.

ALTER TABLE project_settings ALTER COLUMN tr_self_check_enabled SET DEFAULT 1;
UPDATE project_settings SET tr_self_check_enabled = 1;
