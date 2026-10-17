-- 156_documents_superseded_by_index.sql — flowgate.v02.0064 T#4 (DB0007 §3.3) (SQLite)
-- The reverse lookup of the R↔B succession chain test (L0005 in_succession_chain, DB0007 §4 Q8):
-- SELECT doc_id, meta FROM documents WHERE superseded_by = ?. The index only narrows the predecessor
-- candidates; whether one is a succession is read from its meta.converted_to by the application
-- (a card line replacement fills superseded_by too). Additive; rollback: DROP INDEX idx_documents_superseded_by.
CREATE INDEX IF NOT EXISTS idx_documents_superseded_by ON documents (superseded_by);
