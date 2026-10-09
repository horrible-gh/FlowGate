// flowgate.v02.0048 T#3 — skeleton of the qa document API (D#1 §6.3). Owned by T#7 (qna), which
// fills this file only: request functions, their types and the snake_case -> camelCase
// normalization, every export name starting with `qa`. index.ts already re-exports it. The
// v0.1 endpoints it will wrap (NR0003 §2):
//   list     GET  /api/v1/q/{doc_id}
//   answer   POST /api/v1/q/{doc_id}/items/{item_id}/answers  { body, author_kind, selected_option_ids }
export {}
