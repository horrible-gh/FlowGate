// flowgate.v02.0048 T#3 — skeleton of the testSpec document API (D#1 §6.3). Owned by T#11 (testSpec), which
// fills this file only: request functions, their types and the snake_case -> camelCase
// normalization, every export name starting with `testSpec`. index.ts already re-exports it. The
// v0.1 endpoints it will wrap (NR0003 §2):
//   spec       PUT  /api/v1/documents/{doc_id}/test-spec · GET /api/v1/documents/{doc_id}/test-document
//   run        POST /api/v1/documents/{ts_id}/test-spec/runs · …/cases/{case_id}/run
//   result     POST /api/v1/documents/test-results · POST /api/v1/documents/test-run/{run_id}/cancel
export {}
