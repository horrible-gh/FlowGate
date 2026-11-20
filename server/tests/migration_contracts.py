"""Explicit engine-specific repairs excluded from cross-dialect parity checks."""

DIALECT_ONLY_MIGRATIONS = {
    "sqlite": {"114_repair_user_chat_source_access_token_fk.sql"},
    "mysql": set(),
    "postgres": {
        "046a_pg_document_types_sequence_realign.sql",
        "189_repair_pg_conversation_work_plan_doctypes.sql",
    },
}


def shared_migration_files(root):
    result = {}
    for dialect, repairs in DIALECT_ONLY_MIGRATIONS.items():
        names = {path.name for path in (root / dialect).glob("*.sql")}
        assert repairs <= names, f"{dialect}: missing engine repair {repairs - names}"
        result[dialect] = names - repairs
    return result
