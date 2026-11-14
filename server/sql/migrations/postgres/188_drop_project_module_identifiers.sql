-- 188_drop_project_module_identifiers.sql (postgres)
-- flowgate.v02gui.0012 (0001-B item B): a Group identifier change now renames the module key itself
-- everywhere (ids, module columns, id_counter, storage paths, branches; services/module_rename.py),
-- so the identifier -> permanent key alias table of 187 has no reader any more. Dropped so a stale
-- alias can never keep an identifier reserved or resolve it to another group. A module renamed under
-- 187 shows its key again; rename it once more to give it the new identifier. Safe to run again.
DROP TABLE IF EXISTS project_module_identifiers;
