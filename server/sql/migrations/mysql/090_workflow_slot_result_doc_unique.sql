-- 090_workflow_slot_result_doc_unique.sql
-- flowgate.default.0457 (T0007) — NR0003 §2 / §7-1 / §9-4 / §9-9.
--
-- Two statements, in this order and only in this order: put back the slot B0001 broke,
-- then make the state it left unreachable.
--
-- 1. The repair. On 2026-08-24 06:59:40 KST a rejected resubmit of
--    flowgate.default.0454.0005-TR was registered into the *sixth* slot of sequence 737
--    (item id 5674), because inbox Step 7.5 asked which slot was the "in-progress head"
--    instead of which slot the document itself came from. That overwrote
--    flowgate.default.0454.0007-TR, which from then on belonged to no slot at all, and it
--    left 0005-TR occupying two slots at once (item_seq 4 and 6). 0457 T0005 closed the
--    code path; this file puts slot 6 back.
--
--    Every identifying fact NR0003 §2 established is in the WHERE clause: the sequence,
--    its root document, the slot id, item_seq, the slot type, the value the slot holds
--    right now, that the sibling slot 5672 still holds 0005-TR, and that the document
--    being restored exists and is a TR. If any one of them differs this is not the row the
--    report describes, so the statement changes nothing rather than guessing at other
--    data. On a database that never saw the incident — a fresh install, another
--    deployment — it is a no-op for the same reason, and re-running it after it has
--    applied is a no-op because the "currently holds 0005-TR" condition no longer matches.
--
--    Only result_doc_id moves. updated_at stays at 2026-08-23T22:29:48.165Z, the moment of
--    the eviction: T0007 pins every other column of both slots as unchanged, and that
--    timestamp is part of the record of what happened. workflow_item_results rows
--    4996 / 4999 / 5004 are likewise untouched — that ledger is the audit trail NR0003 §3
--    read to date the eviction, not damage to undo. No success row is invented for the
--    restoration either; a data repair is not a registration.
--
--    The sibling-slot condition reads workflow_sequence_items through a derived table.
--    MySQL cannot reference the table being updated in a subquery directly (error 1093);
--    a derived table is materialised first, which is legal there and behaves identically
--    on SQLite and PostgreSQL, so all three dialects carry the same condition.
--
-- 2. The constraint. documents.doc_id is a global identifier and a document belongs to at
--    most one slot, so a non-NULL result_doc_id must be unique across the whole table, not
--    merely within one sequence. Empty slots stay unconstrained: many rows may hold NULL.
--
--    idx_wfseq_items_result_doc (032, recreated by 033) was a plain index serving the same
--    reverse lookup, so it is dropped rather than left beside its unique replacement, which
--    answers that lookup just as well. The replacement takes a new name on purpose:
--    recreating a live index name silently redefines it, and tests/test_migration_numbering
--    .py::test_no_migration_recreates_an_index_that_still_exists reads CREATE INDEX
--    statements for exactly that.
--
--    This statement fails loudly on a database that holds a duplicate other than the one
--    repaired above. That is intended — it is an integrity constraint, not a hint. The
--    whole-table audit, registered as workflow_sequences.find_duplicate_result_doc_slots:
--
--        SELECT result_doc_id, sequence_id, id AS item_id, item_seq, sort_order
--        FROM workflow_sequence_items
--        WHERE result_doc_id IS NOT NULL AND result_doc_id IN (
--            SELECT result_doc_id FROM workflow_sequence_items
--            WHERE result_doc_id IS NOT NULL
--            GROUP BY result_doc_id HAVING COUNT(*) > 1)
--        ORDER BY result_doc_id, sort_order, id;
--
--    On the live database it returned exactly the two rows repaired above and nothing
--    else. Settle any other duplicate the same way — decide which slot each document
--    belongs to — before applying this file.
--
-- Reversal: DROP INDEX uq_wfseq_items_result_doc, recreate idx_wfseq_items_result_doc as a
-- plain index, and set slot 5674 back to 0005-TR. The repair needs no reversal to be safe.

UPDATE workflow_sequence_items
SET result_doc_id = 'flowgate.default.0454.0007-TR'
WHERE id = 5674
  AND sequence_id = 737
  AND item_seq = 6
  AND type = 'TR'
  AND result_doc_id = 'flowgate.default.0454.0005-TR'
  AND EXISTS (
      SELECT 1 FROM workflow_sequences ws
      WHERE ws.id = 737
        AND ws.doc_id = 'flowgate.default.0454.0001-B'
  )
  AND EXISTS (
      SELECT 1 FROM (
          SELECT id, sequence_id, item_seq, type, result_doc_id
          FROM workflow_sequence_items
      ) sib
      WHERE sib.id = 5672
        AND sib.sequence_id = 737
        AND sib.item_seq = 4
        AND sib.type = 'TR'
        AND sib.result_doc_id = 'flowgate.default.0454.0005-TR'
  )
  AND EXISTS (
      SELECT 1 FROM documents d
      WHERE d.doc_id = 'flowgate.default.0454.0007-TR'
        AND d.type_code = 'TR'
  );

DROP INDEX IF EXISTS idx_wfseq_items_result_doc ON workflow_sequence_items;

CREATE UNIQUE INDEX IF NOT EXISTS uq_wfseq_items_result_doc
    ON workflow_sequence_items (result_doc_id);
