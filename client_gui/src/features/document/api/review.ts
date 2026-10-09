// flowgate.v02.0048 T#3 — skeleton of the review document API (D#1 §6.3). Owned by T#6 (actions), which
// fills this file only: request functions, their types and the snake_case -> camelCase
// normalization, every export name starting with `review`. index.ts already re-exports it. The
// v0.1 endpoints it will wrap (NR0003 §2):
//   approve / reject   POST /api/v1/documents/{doc_id}/review_transitions/{approve|reject}  (comment -> rejection_reason)
//   run / rework       POST /api/v1/ai-invoke/start  (action_scope new|edit|rework, mode single)
//   rework hint        GET  /api/v1/ai-invoke/rework-hint
//   AI running         GET  /api/v1/ai-invoke/active
export {}
