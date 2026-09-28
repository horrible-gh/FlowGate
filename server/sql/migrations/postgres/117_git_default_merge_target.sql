-- 117_git_default_merge_target.sql
-- flowgate.default.0594 T0016 §2.2. A non-base integration branch (e.g.
-- "flowgate-v0.2") that a group finalize actually merged into becomes the
-- project's suggested default target for the NEXT group's finalize dialog —
-- not a one-dialog-only pick that resets to base_branch every time. Additive,
-- NULL-able: existing rows keep today's behaviour (fall back to base_branch)
-- until the first non-base finalize on the project writes this column.

ALTER TABLE project_git_config ADD COLUMN default_merge_target TEXT;
