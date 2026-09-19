// AI review result (document_reviews child record), matching ai_review / ai_review_history in the backend detail response.
// A review belongs to its target document rather than being a document itself. The server derives finding_count from findings.

// 0582 T0005 §6: the ONE public shape every AI-provenance surface outside document_reviews
// uses (rejection auto-reject, rejection rework response, Q&A) — null when there is no
// evidence (a human action, or an AI action whose token carried no bound run, e.g. a
// [Copy Mention] hand-off). document_reviews keeps its own established AiReviewProvider
// shape below; this one is for everything that did not already have a field of its own.
export interface AiProvenance {
  ai_run_id?: string | null
  ai_provider_id?: string | null
  ai_provider_name?: string | null
}

export interface AiReviewFinding {
  locus?: string | null
  note?: string | null
}

export interface AiReviewProvider {
  run_id?: string | null
  requested_provider_id?: string | null
  actual_provider_id?: string | null
  actual_provider_name?: string | null
  provider_source?: string | null
  attempt_no?: number | null
  fallback_used?: boolean | null
}

export interface AiReview {
  id?: number | null
  revision_no?: number | null
  reviewer_id?: string | null
  reviewer_name?: string | null
  review_provider?: AiReviewProvider | null
  review_intent?: 'normal' | 'rerun' | string | null
  superseded_review_id?: number | null
  verdict?: 'pass' | 'issues' | 'hold' | string | null
  finding_count?: number | null
  findings?: AiReviewFinding[]
  comment?: string | null
  reviewed_at?: string | null
  created_at?: string | null
}
