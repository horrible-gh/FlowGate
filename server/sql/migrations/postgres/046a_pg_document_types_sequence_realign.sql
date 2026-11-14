-- 046a_pg_document_types_sequence_realign.sql
-- flowgate.v02gui.0017 (NR 0003 A, option (ma)): PostgreSQL only, hand-authored.
--
-- Why: 023 and 025 rebuild document_types as document_types_new(id SERIAL) and copy the old
-- rows WITH their explicit ids. Explicit-id inserts never advance a PostgreSQL sequence, so the
-- final table sequence restarts at 1 while ids 1..N are already taken. On a fresh database the
-- later id-less seeds then draw 1, 2, 3 ...: 047 (CH) and 078b (WP) collide silently through
-- ON CONFLICT DO NOTHING (so those rows are lost), and 113 (T2) aborts with
-- duplicate key (id)=(3).
--
-- What: move the document_types id sequence forward so the next nextval is greater than
-- MAX(id). The sequence name is looked up with pg_get_serial_sequence (023/025 leave it named
-- document_types_new_id_seq1 or similar, so it must never be hardcoded). The sequence is only
-- ever moved FORWARD: GREATEST(next value it would hand out now, MAX(id) + 1). An empty table
-- (MAX(id) IS NULL) leaves the sequence untouched. Re-running is a no-op.
--
-- Order: the migrator applies pending files in filename order (sorted glob), so 046a runs
-- before 047 on a fresh database, and before 113 on a database that previously stopped at 113.
-- On an already-healthy database it runs once and changes nothing.
--
-- historical 023 / 025 / 113 are intentionally NOT edited (immutable applied migrations).
-- This file has no SQLite / MySQL counterpart: AUTOINCREMENT / AUTO_INCREMENT already advance
-- past explicit ids there. It is listed in _PG_HAND_AUTHORED_FILES of
-- server/tools/regen_dialect_migrations.py so regeneration never touches it.
DO $fg_seq_realign$
DECLARE
    seq_name    text;
    max_id      bigint;
    seq_last    bigint;
    seq_called  boolean;
    next_now    bigint;
    next_target bigint;
BEGIN
    IF to_regclass('document_types') IS NULL THEN
        RETURN;
    END IF;
    seq_name := pg_get_serial_sequence('document_types', 'id');
    IF seq_name IS NULL THEN
        RETURN;
    END IF;
    SELECT MAX(id) INTO max_id FROM document_types;
    IF max_id IS NULL THEN
        RETURN;
    END IF;
    EXECUTE format('SELECT last_value, is_called FROM %s', seq_name) INTO seq_last, seq_called;
    IF seq_called THEN
        next_now := seq_last + 1;
    ELSE
        next_now := seq_last;
    END IF;
    next_target := GREATEST(next_now, max_id + 1);
    IF next_target > next_now THEN
        PERFORM setval(seq_name, next_target, false);
    END IF;
END $fg_seq_realign$;
