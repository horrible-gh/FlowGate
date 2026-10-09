// flowgate.v02.0048 T#4 — ↗ 열기 (D#1 §9.2, A안). The Inspector keeps summaries only; editing
// reuses the v0.1 main document screen in a new browser tab (a temporary compatibility path while
// v0.2 is being built). Every feature opens a document through this one function, so moving the
// view inside v0.2 later changes only this file.
//
// - The URL carries the doc_id and nothing else: no token, session value or project id
//   (D#1 §9.5). It is a relative path — v0.1 and v0.2 are served from the same origin.
// - 'noopener' is required: the new tab gets no `window.opener` and starts with its own
//   sessionStorage, so it never reuses this tab's refresh token (CH0005 seq 8 §1). The new tab
//   signs in on its own: a remember-me login rotates once at boot, otherwise /login returns to
//   the document through `next`.
// - With 'noopener' the browser always returns null, so the return value is not a
//   popup-blocked signal and is not read.

/** The v0.1 main deep link for one document: `/main/?doc=<doc_id>`. */
export function openDocumentUrl(docId: string): string {
  return `/main/?doc=${encodeURIComponent(docId)}`
}

/** Opens the document in the v0.1 main screen in a new tab. */
export function openDocument(docId: string): void {
  window.open(openDocumentUrl(docId), '_blank', 'noopener')
}
