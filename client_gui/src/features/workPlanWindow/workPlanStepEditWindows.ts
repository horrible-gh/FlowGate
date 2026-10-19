// 0072 T#2: isolated Step drafts. Only apply() changes the parent; only the parent saves the WP.
// 0073 T#2: a Step draft is the pre-instruction and its attachment only — the review is set in the parent's review
// popover, so the baseline / snapshot check and apply() never see it and cannot write an old review back.
import { markRaw, reactive } from 'vue'
import { workPlanUploadInstruction, workPlanSaveFailureOf } from '@/features/document'
import type { WorkPlanStepDraft } from './workPlanEditModel'
import type { WorkPlanStepEditSession, WorkPlanStepEditTarget } from './useWorkPlanEditor'
import { setWorkPlanStepEditHandler } from './workPlanEditWindows'

export interface WorkPlanStepWindow {
  id: number
  docId: string
  session: WorkPlanStepEditSession
  draft: WorkPlanStepDraft
  baseline: WorkPlanStepDraft
  minimized: boolean
  raise: number
  x: number
  y: number
  uploading: boolean
  closed: boolean
  issue: string | null
  detail: string | null
}

const windows = reactive<WorkPlanStepWindow[]>([])
// A cancelled window may still have a POST in flight. A later parent PUT must wait for it,
// otherwise cleanup could run before the file arrives. Reopening a parent shares this guard.
const uploads = reactive(new Map<string, number>())
let seq = 0
const copy = <T>(v: T): T => JSON.parse(JSON.stringify(v)) as T
const equal = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b)

export function workPlanStepWindowList(): readonly WorkPlanStepWindow[] { return windows }
export function workPlanStepDirty(win: WorkPlanStepWindow): boolean { return !equal(win.draft, win.baseline) }
export function workPlanStepPending(docId: string): boolean {
  return (uploads.get(docId) ?? 0) > 0 || windows.some((win) => win.docId === docId && workPlanStepDirty(win))
}
export function workPlanStepBlocked(win: WorkPlanStepWindow): boolean {
  return win.closed || win.uploading || !win.session.alive() || !win.session.target() || !win.session.snapshot() || win.session.locked()
}
export function workPlanStepValidation(win: WorkPlanStepWindow): string | null {
  // Python's server limit counts Unicode code points (not UTF-16 code units).
  if (Array.from(win.draft.preInstructionText ?? '').length > win.session.preInstructionMaxChars()) return 'text_limit'
  return null
}

export function openWorkPlanStepWindow(target: WorkPlanStepEditTarget, session: WorkPlanStepEditSession): void {
  const current = windows.find((win) => win.docId === target.docId && win.session.target()?.rowId === target.rowId)
  if (current) { current.minimized = false; current.raise += 1; return }
  const draft = session.snapshot()
  if (!draft || !session.target()) return
  const id = ++seq
  windows.push({ id, docId: target.docId, session: markRaw(session), draft: copy(draft), baseline: copy(draft),
    minimized: false, raise: 0, x: 260 + (id % 6) * 24, y: 132 + (id % 6) * 24,
    uploading: false, closed: false, issue: null, detail: null })
}
export function closeWorkPlanStepWindow(win: WorkPlanStepWindow): void {
  win.closed = true
  const index = windows.findIndex((item) => item.id === win.id)
  if (index !== -1) windows.splice(index, 1)
  // Do not delete reserved files here: other drafts / the stored body may still refer to them.
  // The existing successful WP PUT owns cleanup. A cancelled draft never causes an implicit PUT.
}
export function closeWorkPlanStepWindows(docId?: string): void {
  for (const win of [...windows]) if (!docId || win.docId === docId) closeWorkPlanStepWindow(win)
}
export function installWorkPlanStepEditor(): () => void {
  return setWorkPlanStepEditHandler({ open: openWorkPlanStepWindow, pending: workPlanStepPending, parentClosed: closeWorkPlanStepWindows })
}

export function setWorkPlanStepValue<K extends keyof WorkPlanStepDraft>(win: WorkPlanStepWindow, key: K, value: WorkPlanStepDraft[K]): void {
  if (workPlanStepBlocked(win)) return
  if (win.session.target()?.pairRole === 'result') return
  win.draft = { ...win.draft, [key]: value }
  win.issue = null
  win.detail = null
}

/** Clean windows follow parent values. Dirty windows retain their draft, checked again at apply time. */
export function syncWorkPlanStep(win: WorkPlanStepWindow): void {
  const current = win.session.snapshot()
  if (current && !win.uploading && !workPlanStepDirty(win)) { win.baseline = copy(current); win.draft = copy(current) }
}
export function saveWorkPlanStep(win: WorkPlanStepWindow): boolean {
  if (workPlanStepBlocked(win)) return false
  const invalid = workPlanStepValidation(win)
  if (invalid) { win.issue = invalid; return false }
  // Never overwrite a new version of this Step's details with an old child draft.
  if (!equal(win.session.snapshot(), win.baseline)) { win.issue = 'conflict'; return false }
  const result = win.session.apply(copy(win.draft))
  if (result !== 'applied') { win.issue = result; return false }
  closeWorkPlanStepWindow(win)
  return true
}

export async function uploadWorkPlanStepFile(win: WorkPlanStepWindow, file: File): Promise<void> {
  if (workPlanStepBlocked(win) || win.session.target()?.pairRole === 'result') return
  const stepKey = win.session.attachmentStepKey()
  if (!stepKey) { win.issue = 'save_parent_first'; return }
  const limit = win.session.attachmentMaxBytes()
  if (limit > 0 && file.size > limit) { win.issue = 'file_limit'; return }
  win.uploading = true
  win.issue = null
  win.detail = null
  uploads.set(win.docId, (uploads.get(win.docId) ?? 0) + 1)
  try {
    const reference = await workPlanUploadInstruction(win.docId, stepKey, file)
    // A removed/replaced row or a closed parent cannot receive a late response. A lock that
    // began during POST may retain this child's reference, but still prevents apply and PUT.
    if (!win.closed && win.session.alive() && win.session.target()) {
      win.draft = { ...win.draft, preInstructionAttachment: reference }
    }
  } catch (error) {
    if (!win.closed) {
      win.issue = 'upload_failed'
      const failure = workPlanSaveFailureOf(error)
      win.detail = failure.message
      // This endpoint also returns 422 for file/step validation. Only its explicit
      // editability verdict locks the parent; a discarded Flow stays locked as on PUT.
      const refusal = failure.kind === 'disposed' ? 'disposed'
        : failure.kind === 'not_editable' && failure.message === 'Work plan is not editable.' ? 'not_editable' : undefined
      win.session.refreshLocks(refusal)
    }
  } finally {
    win.uploading = false
    const count = (uploads.get(win.docId) ?? 1) - 1
    if (count > 0) uploads.set(win.docId, count)
    else uploads.delete(win.docId)
  }
}
