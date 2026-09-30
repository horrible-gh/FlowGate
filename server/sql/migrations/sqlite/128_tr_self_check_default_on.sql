-- 128_tr_self_check_default_on.sql
-- flowgate.default.0652 T0002: TR Self-check becomes the default (ON). Existing projects are moved to ON
-- once by this migration; an explicit OFF saved afterwards is preserved.

-- SQLite cannot ALTER a column default; every project_settings INSERT sets tr_self_check_enabled explicitly.
UPDATE project_settings SET tr_self_check_enabled = 1;
