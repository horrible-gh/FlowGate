// flowgate.v02.0048 T#3 — skeleton of the check document API (D#1 §6.3). Owned by T#12 (check), which
// fills this file only: request functions, their types and the snake_case -> camelCase
// normalization, every export name starting with `check`. index.ts already re-exports it. The
// v0.1 endpoints it will wrap (NR0003 §2):
//   final approval   POST /api/v1/documents/workflow/final-approval  { doc_id }
//   approve (AC)     POST /api/v1/documents/review_transitions/approve  { doc_id, git_action, git_target_branch? }
//   Git context      GET  /api/v1/groups/{gid}/git/finalize?context=approval
//   reopen/restore   POST /api/v1/documents/workflow/reopen · /restore
export {}
