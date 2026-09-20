import type { AiProvenance } from './aiReview'

// /api/v1/document/{doc_id}/relations revision-history row. revision_no keeps the
// backend's existing meaning: it is the backed-up revision that the edit replaced.
export interface DocumentRevision {
  revision_no?: number | null
  created_at?: string | null
  edit_reason?: string | null
  linked_doc_id?: string | null
  backup_path?: string | null
  editor_provider?: AiProvenance | null
}
