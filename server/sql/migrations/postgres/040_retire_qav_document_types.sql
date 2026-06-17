-- 040 Q/A/V revamp 2 - retire Q/A/V document types.
-- Basis: group 0022 DB0006 §4.3, D0005 §3.6, R0001-3,4
-- System types cannot be deleted, so mark them inactive. Keep rows to preserve historical document FKs.
-- Remove the is_system filter so both global system copies (is_system=1) and future project overrides
-- (typically is_system=0) are covered. Today Q/A/V only exists as global system types, so runtime coverage
-- is unchanged; this avoids a mismatch between the comment and behavior.
-- Effect: excluded from create dialogs and type legends because only active types are shown; new creation is blocked.

UPDATE document_types
   SET is_active = 0, updated_at = CURRENT_TIMESTAMP
 WHERE type_code IN ('Q','A','V');