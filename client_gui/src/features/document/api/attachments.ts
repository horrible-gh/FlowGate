// flowgate.v02.0048 T#3 — skeleton of the attachments document API (D#1 §6.3). Owned by T#8 (attachments), which
// fills this file only: request functions, their types and the snake_case -> camelCase
// normalization, every export name starting with `attachments`. index.ts already re-exports it. The
// v0.1 endpoints it will wrap (NR0003 §2):
//   list     GET    /api/v1/documents/{doc_id}/attachments
//   open     GET    /api/v1/documents/{doc_id}/attachments/{name}   (+ /read)
//   add      POST   /api/v1/documents/{doc_id}/attachments          (multipart)
//   delete   DELETE /api/v1/documents/{doc_id}/attachments/{name}
export {}
