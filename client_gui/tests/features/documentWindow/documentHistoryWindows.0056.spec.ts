/**
 * flowgate.v02.0056 F1 — the review history windows kept beside the document windows
 * (documentWindow/documentWindows.ts): one 검수·반려 이력 window per document, cascaded from their own origin;
 * a second open request restores and raises the one window; minimize, close, and close-all with the
 * document windows.
 */
import { afterEach, describe, expect, it } from 'vitest'
import {
  closeAllDocumentWindows,
  closeDocumentHistoryWindow,
  documentHistoryWindowList,
  documentHistoryWindowOf,
  documentWindowList,
  openDocumentHistoryWindow,
  openDocumentWindow,
  setDocumentHistoryWindowMinimized,
} from '@/features/documentWindow'

const DOC = 'flowgate.v02.0056.0007-TR'
const OTHER = 'flowgate.v02.0056.0008-T'

afterEach(() => closeAllDocumentWindows())

describe('review history windows (flowgate.v02.0056 F1)', () => {
  it('opens one window per document from its own origin, cascaded; the document windows are untouched', () => {
    openDocumentHistoryWindow(DOC)
    openDocumentHistoryWindow(OTHER)
    expect(documentHistoryWindowList().map((win) => [win.docId, win.x, win.y, win.minimized, win.raise])).toEqual([
      [DOC, 260, 132, false, 0],
      [OTHER, 288, 160, false, 0],
    ])
    expect(documentWindowList()).toEqual([])
    expect(documentHistoryWindowOf(DOC)?.docId).toBe(DOC)
    expect(documentHistoryWindowOf('nothing')).toBeUndefined()
  })

  it('a second open request is the same window: restored from minimized and raised once more', () => {
    openDocumentHistoryWindow(DOC)
    setDocumentHistoryWindowMinimized(DOC, true)
    expect(documentHistoryWindowOf(DOC)!.minimized).toBe(true)
    openDocumentHistoryWindow(DOC)
    expect(documentHistoryWindowList()).toHaveLength(1)
    expect(documentHistoryWindowOf(DOC)).toMatchObject({ minimized: false, raise: 1 })
    openDocumentHistoryWindow(DOC)
    expect(documentHistoryWindowOf(DOC)!.raise).toBe(2)
  })

  it('an empty id opens nothing; minimizing or closing a window that is not open does nothing', () => {
    openDocumentHistoryWindow('')
    setDocumentHistoryWindowMinimized(DOC, true)
    closeDocumentHistoryWindow(DOC)
    expect(documentHistoryWindowList()).toEqual([])
  })

  it('close drops the one window; the others keep their place', () => {
    openDocumentHistoryWindow(DOC)
    openDocumentHistoryWindow(OTHER)
    closeDocumentHistoryWindow(DOC)
    expect(documentHistoryWindowList().map((win) => win.docId)).toEqual([OTHER])
  })

  it('closeAllDocumentWindows closes the history windows with the document windows and starts the cascade again', () => {
    openDocumentWindow(DOC)
    openDocumentHistoryWindow(DOC)
    openDocumentHistoryWindow(OTHER)
    closeAllDocumentWindows()
    expect(documentWindowList()).toEqual([])
    expect(documentHistoryWindowList()).toEqual([])
    openDocumentHistoryWindow(OTHER)
    expect(documentHistoryWindowList().map((win) => [win.x, win.y])).toEqual([[260, 132]])
  })
})
