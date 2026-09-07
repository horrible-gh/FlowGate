// AI review result (document_reviews child record), matching ai_review / ai_review_history in the backend detail response.
// A review belongs to its target document rather than being a document itself. The server derives finding_count from findings.

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
  verdict?: 'pass' | 'issues' | 'hold' | string | null
  finding_count?: number | null
  findings?: AiReviewFinding[]
  comment?: string | null
  reviewed_at?: string | null
  created_at?: string | null
}
