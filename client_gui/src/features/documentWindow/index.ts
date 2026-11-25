// public surface of the document window: one in-tab modeless window per
// document, MD Viewer body, ✎ text editing in the same window. Openers call `openDocumentWindow`
// (the Inspector through its openDocument.ts); the App Shell mounts `DocumentWindows` once.
// flowgate.v02.0056 F2-2 — and the [작성] window of a card line without a document (openSlotWriteWindow, with the
// lines written there) and the AI call window of a document (openDocumentAiWindow, with the runs it started).
export { default as DocumentWindows } from './DocumentWindows.vue'
export * from './documentWindows'
export * from './documentWindowModel'
export { documentHistoryVerdictKey, documentHistoryWhen } from './documentHistoryModel'
// the scenario table shares the document window's result glyph.
export { default as TestMarkBadge } from './TestMarkBadge.vue'
export { default as TestScenarioList } from './TestScenarioList.vue'
export { testVerdictMark, TEST_PENDING_MARK, testScenarioRows } from './specialDocumentModel'
export * from './slotWriteWindows'
export * from './documentAiWindows'
// the document upload / download: the coordinator (documentIo), the edit state registry it
// guards with, and the connected [업로드][다운로드] group the Inspector's document lines draw.
export { default as DocumentIoGroup } from './DocumentIoGroup.vue'
export {
  documentIoButtons,
  documentIoDownload,
  documentIoDraw,
  documentIoLeave,
  documentIoForget,
  documentIoNotices,
  documentIoUpload,
  dismissDocumentIoNotice,
} from './documentIo'
export {
  documentIoGuarded, documentIoHasUnresolved, documentIoMarkUnresolved, documentIoTsBegin, documentIoTsEnd,
  documentIoUnresolvedOwner,
} from './documentEditRegistry'
// the work plan edit window (features/workPlanWindow; DocumentWindows mounts its host): openers
// outside the window features (the Inspector's openDocument.ts) open it through this surface like the other in-tab windows.
export { openWorkPlanEditWindow, type WorkPlanEditRequest } from '@/features/workPlanWindow/workPlanEditWindows'
// flowgate.v02.0074 T#2 — and the opener a work plan window asked for by the document [업로드] (its JSON import) gets.
export { setWorkPlanEditOpener, type WorkPlanEditOpener } from '@/features/workPlanWindow/workPlanEditWindows'

export * from './tsEditSession'

export { default as TestEvidenceFiles } from './TestEvidenceFiles.vue'
