// flowgate.v02.0056 F2-2 — the [작성] windows of card lines without a document (O3 0009-TR §4-2), kept the way the
// document windows are (documentWindows.ts): module state of this tab, one window per card line (flow · node ·
// slot), opening it again brings it back. The window writes the line's document when it is saved
// (SlotWriteWindow.vue → cardLineWrite).
//
// The lines written here (`written`): the 201 answer's `line` stands for the card's line until the Canvas read
// shows a document on it (the Inspector merges them over its target — inspectorTarget withWrittenCardLines — and
// settles them on every target it shows). A line the Canvas read already has a document for is no longer
// shown from here and is dropped.
//
// A line the server refuses because it already has a document (409 slot_has_document / node_token_unavailable)
// opens that document: at once when the refusal names it, else as soon as the Canvas read shows it (`pendingOpen`,
// settled with the written lines).
import { reactive } from 'vue'
import type { CardLineRecord, CardLineWriteResult } from '@/features/document'

/** What the [작성] window writes and whom it tells. */
export interface SlotWriteRequest {
  readonly flowId: string
  readonly nodeKey: string
  /** The card line's slot (O3 §4-2 `slot`). */
  readonly slot: string
  /** The line's document type, when the card line names it. */
  readonly docType: string | null
  /** The line's name as the documents tab shows it (지시서 · 레포트 …). */
  readonly name: string
  /**
   * flowgate.v02.0088 F14: the i18n key of that name, when the opener has one. The window shows the key in the
   * current language; without it (an older caller) or for a key the catalog does not have, `name` as it came.
   * UI only — never written, sent or used to tell windows apart (the key stays flow / node / slot).
   */
  readonly nameKey?: string | null
  /** The card's title (the window's sub title). */
  readonly cardTitle: string
  /** The card runs now (open): what is written may become its result, which needs a body. */
  readonly nodeRunning: boolean
  /** Told after a 201 (the opener reads the Canvas again). */
  readonly written?: (result: CardLineWriteResult) => void
  /** Told after a refusal that means the line has a document now (the opener reads the Canvas again). */
  readonly reread?: () => void
}

export interface SlotWriteWindowState {
  /** flow \n node \n slot. */
  readonly key: string
  request: SlotWriteRequest
  minimized: boolean
  readonly x: number
  readonly y: number
  raise: number
}

const CASCADE_STEPS = 6
const CASCADE_PX = 28
const ORIGIN_X = 240
const ORIGIN_Y = 112

const windows = reactive<SlotWriteWindowState[]>([])
let cascade = 0

/** The key of a card line. */
export const slotWriteKey = (flowId: string, nodeKey: string, slot: string) => `${flowId}\n${nodeKey}\n${slot}`

export function slotWriteWindowList(): readonly SlotWriteWindowState[] {
  return windows
}

export function slotWriteWindowOf(key: string): SlotWriteWindowState | undefined {
  return windows.find((win) => win.key === key)
}

/** Opens the [작성] window of a card line; one already open is brought back with the newest request. */
export function openSlotWriteWindow(request: SlotWriteRequest): void {
  const key = slotWriteKey(request.flowId, request.nodeKey, request.slot)
  const open = slotWriteWindowOf(key)
  if (open) {
    open.request = request
    open.minimized = false
    open.raise += 1
    return
  }
  const offset = (cascade++ % CASCADE_STEPS) * CASCADE_PX
  windows.push({ key, request, minimized: false, x: ORIGIN_X + offset, y: ORIGIN_Y + offset, raise: 0 })
}

export function setSlotWriteWindowMinimized(key: string, minimized: boolean): void {
  const open = slotWriteWindowOf(key)
  if (open) open.minimized = minimized
}

export function closeSlotWriteWindow(key: string): void {
  const index = windows.findIndex((win) => win.key === key)
  if (index !== -1) windows.splice(index, 1)
}

// --- The lines written here -------------------------------------------------------------------------

const written = reactive(new Map<string, CardLineRecord>())
const pendingOpen = reactive(new Set<string>())

/** Keeps the 201 line of a card line until the Canvas read shows the document. */
export function recordWrittenCardLine(flowId: string, nodeKey: string, line: CardLineRecord): void {
  written.set(slotWriteKey(flowId, nodeKey, line.slot), line)
}

/** The lines of a card written here and not yet shown by the Canvas read (reactive). */
export function writtenCardLines(flowId: string, nodeKey: string): readonly CardLineRecord[] {
  const prefix = `${flowId}\n${nodeKey}\n`
  return [...written.entries()].filter(([key]) => key.startsWith(prefix)).map(([, line]) => line)
}

/** Opens the line's document once the Canvas read shows it (a refusal that named none). */
export function openWhenShown(flowId: string, nodeKey: string, slot: string): void {
  pendingOpen.add(slotWriteKey(flowId, nodeKey, slot))
}

/**
 * The Canvas read of a card as the Inspector shows it: a written line the read now has a document for is dropped,
 * and a line waiting to be opened is opened (`open`) once the read has its document.
 */
export function settleWrittenCardLines(
  flowId: string,
  nodeKey: string,
  lines: readonly { readonly slot: string; readonly docId: string | null }[],
  open: (docId: string) => void,
): void {
  for (const line of lines) {
    if (line.docId === null) continue
    const key = slotWriteKey(flowId, nodeKey, line.slot)
    if (written.has(key)) written.delete(key)
    if (pendingOpen.has(key)) {
      pendingOpen.delete(key)
      open(line.docId)
    }
  }
}

/** Closes every [작성] window and forgets the written lines (sign-out, tests). */
export function closeAllSlotWriteWindows(): void {
  windows.splice(0, windows.length)
  written.clear()
  pendingOpen.clear()
  cascade = 0
}
