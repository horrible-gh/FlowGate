// flowgate.v02.0053 T#3 — public surface of the document window: one in-tab modeless window per
// document, MD Viewer body, ✎ text editing in the same window. Openers call `openDocumentWindow`
// (the Inspector through its openDocument.ts); the App Shell mounts `DocumentWindows` once.
export { default as DocumentWindows } from './DocumentWindows.vue'
export * from './documentWindows'
export * from './documentWindowModel'
