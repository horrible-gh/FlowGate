// flowgate.v02.0068 T#1 — the document edit state registry (0068 D §2.1, L §2.11). Module state of this tab,
// like documentWindows: nothing is sent anywhere.
// - Every open document window registers what it shows or edits and whether its editor reads, holds unsaved
//   text or saves (an entry), and answers the upload's requests through it: a guard started (on_guard), a
//   guard released (on_release) and "read the new stored body" (reflect).
// - Per document id it keeps the upload in progress (`running`, one at a time), its guard (`guards`: no edit
//   entry, input or save in any window of that document) and a save the previous Designer already sent
//   (`inflight`, a guard that outlives the Designer).
// - The module outlives the Designer, so every upload / download carries a token of the Designer's epoch;
//   leaving the Designer (documentIoDesignerExit) starts a new epoch and the older tokens are dead: after
//   any await their work stops (alive), and a release only clears what its own token holds.
// The document windows are mounted by the App, not by the Designer: an entry stays registered while its
// window is open (unregistered when it unmounts), across Designer epochs.
import { reactive } from 'vue'
import { REFLECT_TIMEOUT_MS } from './documentIoModel'

/** One upload or download of one document, in one Designer epoch. */
export interface DocumentIoToken {
  readonly docId: string
  readonly epoch: number
  readonly serial: number
}

export type DocumentEditorState = 'none' | 'loading' | 'ready' | 'error'
export type DocumentReflectResult = 'ok' | 'failed' | 'skipped'
export type DocumentUploadPhase = 'picking' | 'reading' | 'saving' | 'reflecting'

/** What an open document window tells the registry (read at call time) and does for it. */
export interface DocumentEditEntry {
  /** The documents the window stands for: the one shown, and the one its editor edits. */
  related(): readonly string[]
  editor(): DocumentEditorState
  dirty(): boolean
  saving(): boolean
  /** A guard started on the document: a pending editor read becomes a late answer. */
  onGuard(docId: string): void
  /** The document's guard ended: a deferred editor read starts now. */
  onRelease(docId: string): void
  /** Read the new stored body (the view, or a clean editor). Starts its read before it returns. */
  reflect(docId: string): Promise<DocumentReflectResult>
}

let epoch = 0
let serial = 0
let entryKeys = 0

const entries = new Map<number, DocumentEditEntry>()
/** Reflect answers not given yet, per entry: settled `skipped` when the window goes. */
const pendingReplies = new Map<number, Set<(result: DocumentReflectResult) => void>>()
/** Reflect waits not over yet: ended at once when the Designer is left. */
const reflectWaits = new Set<() => void>()

const running = reactive(new Map<string, { token: DocumentIoToken; phase: DocumentUploadPhase }>())
const guards = reactive(new Map<string, DocumentIoToken>())
const inflight = reactive(new Map<string, DocumentIoToken>())
const downloading = reactive(new Map<string, DocumentIoToken>())

/** The current Designer epoch. */
export function documentIoEpoch(): number {
  return epoch
}

/** A new token of the current epoch for one upload or download of the document. */
export function documentIoNewToken(docId: string): DocumentIoToken {
  serial += 1
  return Object.freeze({ docId, epoch, serial })
}

/** The token's upload is still the document's upload of this epoch. */
export function documentIoAlive(token: DocumentIoToken): boolean {
  return token.epoch === epoch && running.get(token.docId)?.token === token
}

/** An upload runs for the document, or a save of the previous Designer has not answered yet. */
export function documentIoBusy(docId: string): boolean {
  return running.has(docId) || inflight.has(docId)
}

/** The document is guarded: no edit entry, input or save in its windows (reactive). */
export function documentIoGuarded(docId: string): boolean {
  return guards.has(docId) || inflight.has(docId)
}

/** A window of the document holds unsaved text or saves it. */
export function documentIoConflict(docId: string): boolean {
  return [...entries.values()].some((entry) => entry.related().includes(docId) && (entry.dirty() || entry.saving()))
}

/** A window of the document holds unsaved text (what a download does not contain). */
export function documentIoUnsaved(docId: string): boolean {
  return [...entries.values()].some((entry) => entry.related().includes(docId) && entry.dirty())
}

/** The windows that stand for the document now. */
function entriesOf(docId: string): DocumentEditEntry[] {
  return [...entries.values()].filter((entry) => entry.related().includes(docId))
}

/** Registers an open window; the returned function unregisters it (its unanswered reflects are `skipped`). */
export function registerDocumentEditEntry(entry: DocumentEditEntry): () => void {
  const key = ++entryKeys
  entries.set(key, entry)
  return () => {
    entries.delete(key)
    const replies = pendingReplies.get(key)
    pendingReplies.delete(key)
    replies?.forEach((settle) => settle('skipped'))
  }
}

// --- The upload's state (used by the coordinator, documentIo.ts) ----------------------------------------

export function documentIoRunning(docId: string): { readonly token: DocumentIoToken; readonly phase: DocumentUploadPhase } | undefined {
  return running.get(docId)
}

/** Starts the token's upload (picking). The caller checked busy first. */
export function documentIoStartUpload(token: DocumentIoToken): void {
  running.set(token.docId, { token, phase: 'picking' })
}

export function documentIoSetPhase(token: DocumentIoToken, phase: DocumentUploadPhase): void {
  const job = running.get(token.docId)
  if (job?.token === token) running.set(token.docId, { token, phase })
}

/** Starts the guard of the token's document and tells its windows (the same synchronous turn as the check). */
export function documentIoGuard(token: DocumentIoToken): void {
  guards.set(token.docId, token)
  documentIoSetPhase(token, 'reading')
  for (const entry of entriesOf(token.docId)) entry.onGuard(token.docId)
}

/** Clears what the token holds — its guard and its upload — and nothing of another token. */
export function documentIoRelease(token: DocumentIoToken): void {
  const docId = token.docId
  let wasGuard = false
  if (guards.get(docId) === token) {
    guards.delete(docId)
    wasGuard = true
  }
  if (running.get(docId)?.token === token) running.delete(docId)
  if (wasGuard && token.epoch === epoch) {
    for (const entry of entriesOf(docId)) entry.onRelease(docId)
  }
}

/**
 * Asks every window of the token's document to read the new stored body and waits for their answers — at
 * most REFLECT_TIMEOUT_MS, and no longer once the Designer is left. A window that closes counts as answered;
 * an answer after the wait is dropped (the window keeps its editor loading until its own read ends).
 */
export function documentIoReflectAll(token: DocumentIoToken, timeoutMs: number = REFLECT_TIMEOUT_MS): Promise<void> {
  const replies = [...entries.entries()]
    .filter(([, entry]) => entry.related().includes(token.docId))
    .map(([key, entry]) => trackReply(key, entry, token.docId))
  return new Promise<void>((resolve) => {
    let done = false
    const finish = () => {
      if (done) return
      done = true
      clearTimeout(timer)
      reflectWaits.delete(finish)
      resolve()
    }
    const timer = setTimeout(finish, timeoutMs)
    reflectWaits.add(finish)
    void Promise.all(replies).then(finish)
  })
}

function trackReply(key: number, entry: DocumentEditEntry, docId: string): Promise<DocumentReflectResult> {
  return new Promise<DocumentReflectResult>((resolve) => {
    let replies = pendingReplies.get(key)
    if (!replies) pendingReplies.set(key, (replies = new Set()))
    const own = replies
    // The first of the window's answer and its unmount counts.
    const settle = (result: DocumentReflectResult) => {
      if (!own.delete(settle)) return
      resolve(result)
    }
    own.add(settle)
    let reply: Promise<DocumentReflectResult>
    try {
      reply = entry.reflect(docId)
    } catch {
      reply = Promise.resolve('failed')
    }
    reply.then(settle, () => settle('failed'))
  })
}

// --- A save the previous Designer sent (L §2.12 orphan_settled) -----------------------------------------

export function documentIoInflight(docId: string): DocumentIoToken | undefined {
  return inflight.get(docId)
}

/**
 * The previous Designer's save of the token answered: the guard it kept ends. Returns false when the token
 * is not the one kept (nothing to do).
 */
export function documentIoDropInflight(token: DocumentIoToken): boolean {
  if (inflight.get(token.docId) !== token) return false
  inflight.delete(token.docId)
  return true
}

/** The windows of the document (this epoch's) resume an editor read they deferred for its guard. */
export function documentIoResume(docId: string): void {
  if (documentIoGuarded(docId)) return
  for (const entry of entriesOf(docId)) entry.onRelease(docId)
}

// --- Downloads ----------------------------------------------------------------------------------------

export function documentIoDownloading(docId: string): boolean {
  return downloading.has(docId)
}

export function documentIoStartDownload(token: DocumentIoToken): void {
  downloading.set(token.docId, token)
}

export function documentIoEndDownload(token: DocumentIoToken): void {
  if (downloading.get(token.docId) === token) downloading.delete(token.docId)
}

// --- Leaving the Designer -----------------------------------------------------------------------------

/**
 * on_designer_exit (L §2.11): a save already sent is kept as `inflight` (it cannot be called back), the epoch
 * moves on so every older token is dead, the uploads, guards and downloads of the epoch are cleared, and the
 * reflect waits end at once. The windows stay open (the App mounts them): a window whose editor read waited
 * for a guard that is gone now reads.
 */
export function documentIoDesignerExit(): void {
  for (const [docId, job] of running) {
    if (job.phase === 'saving') inflight.set(docId, job.token)
  }
  const released = [...guards.keys()]
  epoch += 1
  running.clear()
  guards.clear()
  downloading.clear()
  for (const finish of [...reflectWaits]) finish()
  for (const docId of released) documentIoResume(docId)
}

/** Test support: forgets everything (a fresh tab). */
export function resetDocumentEditRegistry(): void {
  epoch = 0
  serial = 0
  entries.clear()
  for (const replies of pendingReplies.values()) replies.forEach((settle) => settle('skipped'))
  pendingReplies.clear()
  for (const finish of [...reflectWaits]) finish()
  running.clear()
  guards.clear()
  inflight.clear()
  downloading.clear()
}
