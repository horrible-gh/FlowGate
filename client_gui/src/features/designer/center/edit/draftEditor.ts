// flowgate.v02.0045 S3 — the editor of one Flow's Graph draft: applies edit commands, keeps
// their undo, and stores the whole draft (P0008 S3-1, whole replacement) after a quiet time.
//
// - One save of the draft on the wire at a time; edits made meanwhile go out in the next save,
//   which carries the version the previous one answered (P0008 `draft_version`).
// - A save answering keys for `tmp_` nodes / edges renames them everywhere (model, undo) and
//   reports the renames (`onKeys`), so the Center can move the selection and store positions.
// - `draft_version_conflict` (another tab / user saved first, P0008 S3-2) stops saving: the
//   status becomes `conflict` and edits are kept on screen until the Graph is read again.
// - `dispose` (tab closed) sends what waits; edits made while a save ran still go out right after
//   it, and that save's `onKeys` still reports the renames (the owner stores the new positions).
// Pure TypeScript, no Vue: the Center session mirrors `status` into its reactive state.
import type { FlowDraftError, FlowDraftSaveResult } from '../graph/flowGraphSource'
import { CLIENT_KEY_PREFIX, draftContent, remapDraft, type DraftModel } from './draftModel'
import { applyCommand, applyCommands, remapCommand, reviveCommands, type EditCommand } from './graphEditCommands'

export const DRAFT_SAVE_DELAY_MS = 400
const UNDO_LIMIT = 100

/** `idle` saved, `pending` waiting to send, `saving` on the wire, `error` / `conflict` refused. */
export type DraftEditStatus = 'idle' | 'pending' | 'saving' | 'error' | 'conflict'

export interface DraftEditorOptions {
  save(version: number, content: ReturnType<typeof draftContent>): Promise<FlowDraftSaveResult>
  /** The draft (and undo) after a save renamed `tmp_` keys. */
  onKeys(keyMap: Readonly<Record<string, string>>): void
  /** Status / model / undo availability changed. */
  onChange(): void
  delayMs?: number
}

export interface DraftEditor {
  model(): DraftModel
  version(): number
  status(): DraftEditStatus
  /** The refusal code of a failed save (`error` / `conflict`), else null. */
  errorCode(): string | null
  canUndo(): boolean
  /** True while an edit is waiting or a save runs — a Graph read now could be older. */
  busy(): boolean
  apply(command: EditCommand): boolean
  undo(): boolean
  /** Sends a waiting save now. */
  flush(): void
  /** Resolves when nothing waits or runs: true when the draft is saved, false when refused. */
  settled(): Promise<boolean>
  /** A fresh read of the Flow: the draft as stored, its version, and no undo. */
  reset(model: DraftModel, version: number): void
  newKey(): string
  dispose(): void
}

let keySeq = 0

export function newClientKey(): string {
  keySeq += 1
  return `${CLIENT_KEY_PREFIX}${Date.now().toString(36)}${keySeq.toString(36)}`
}

const storedKeys = (model: DraftModel) =>
  new Set([...model.nodes, ...model.edges].filter((x) => !x.isNew).map((x) => x.key))

export function createDraftEditor(model: DraftModel, version: number, options: DraftEditorOptions): DraftEditor {
  const delay = options.delayMs ?? DRAFT_SAVE_DELAY_MS
  let current = model
  let saved = version
  let status: DraftEditStatus = 'idle'
  let errorCode: string | null = null
  let undoStack: EditCommand[][] = []
  let timer: ReturnType<typeof setTimeout> | null = null
  let editSeq = 0
  let sentSeq = 0
  let inFlight = false
  let disposed = false
  let savedKeys = storedKeys(model)
  let waiters: Array<(ok: boolean) => void> = []

  const busy = () => timer !== null || inFlight || (editSeq !== sentSeq && status !== 'error' && status !== 'conflict')

  function settle(ok: boolean) {
    const list = waiters
    waiters = []
    for (const resolve of list) resolve(ok)
  }

  function setStatus(next: DraftEditStatus) {
    status = next
    options.onChange()
  }

  function schedule() {
    if (disposed || status === 'conflict') return
    if (timer) clearTimeout(timer)
    timer = setTimeout(() => {
      timer = null
      send()
    }, delay)
    setStatus(inFlight ? 'saving' : 'pending')
  }

  function send() {
    if (inFlight || status === 'conflict' || editSeq === sentSeq) return
    inFlight = true
    const seq = editSeq
    setStatus('saving')
    let request: Promise<FlowDraftSaveResult>
    try {
      request = options.save(saved, draftContent(current))
    } catch (error) {
      request = Promise.reject(error)
    }
    request.then(
      (result) => {
        inFlight = false
        sentSeq = seq
        saved = result.version
        errorCode = null
        if (Object.keys(result.keyMap).length) {
          current = remapDraft(current, result.keyMap)
          undoStack = undoStack.map((entry) => entry.map((c) => remapCommand(c, result.keyMap)))
          options.onKeys(result.keyMap)
        }
        savedKeys = storedKeys(current)
        if (editSeq !== sentSeq) {
          send()
          return
        }
        setStatus('idle')
        if (timer === null) settle(true)
      },
      (error: FlowDraftError) => {
        inFlight = false
        sentSeq = seq
        errorCode = (error && typeof error.code === 'string' && error.code) || 'save_failed'
        setStatus(errorCode === 'draft_version_conflict' ? 'conflict' : 'error')
        settle(false)
      },
    )
  }

  function flush() {
    if (timer) {
      clearTimeout(timer)
      timer = null
    }
    send()
  }

  function edit(next: DraftModel, undo: EditCommand[] | null) {
    current = next
    if (undo) undoStack = [...undoStack.slice(-(UNDO_LIMIT - 1)), undo]
    editSeq += 1
    if (status === 'error') errorCode = null
    schedule()
  }

  return {
    model: () => current,
    version: () => saved,
    status: () => status,
    errorCode: () => errorCode,
    canUndo: () => undoStack.length > 0 && status !== 'conflict',
    busy,
    apply(command) {
      if (status === 'conflict') return false
      const applied = applyCommand(current, command)
      if (!applied) return false
      edit(applied.model, [...applied.undo])
      return true
    },
    undo() {
      if (status === 'conflict' || !undoStack.length) return false
      const entry = reviveCommands(undoStack[undoStack.length - 1], savedKeys, newClientKey)
      const next = applyCommands(current, entry)
      undoStack = undoStack.slice(0, -1)
      if (!next) {
        options.onChange()
        return false
      }
      edit(next, null)
      return true
    },
    flush,
    settled() {
      if (!busy()) return Promise.resolve(status === 'idle')
      return new Promise((resolve) => {
        waiters.push(resolve)
        flush()
      })
    },
    reset(model, version) {
      if (timer) clearTimeout(timer)
      timer = null
      current = model
      saved = version
      undoStack = []
      editSeq = sentSeq
      errorCode = null
      savedKeys = storedKeys(model)
      setStatus('idle')
      settle(true)
    },
    newKey: newClientKey,
    dispose() {
      disposed = true
      if (timer) {
        clearTimeout(timer)
        timer = null
        send()
      }
      settle(status === 'idle')
    },
  }
}
