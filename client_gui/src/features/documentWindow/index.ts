// flowgate.v02.0053 T#3 — public surface of the document window: one in-tab modeless window per
// document, MD Viewer body, ✎ text editing in the same window. Openers call `openDocumentWindow`
// (the Inspector through its openDocument.ts); the App Shell mounts `DocumentWindows` once.
// flowgate.v02.0056 F2-2 — and the [작성] window of a card line without a document (openSlotWriteWindow, with the
// lines written there) and the AI call window of a document (openDocumentAiWindow, with the runs it started).
export { default as DocumentWindows } from './DocumentWindows.vue'
export * from './documentWindows'
export * from './documentWindowModel'
export * from './slotWriteWindows'
export * from './documentAiWindows'
