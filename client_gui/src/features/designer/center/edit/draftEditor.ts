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
// - flowgate.v02.0053 T#5: every stored save is reported (`onSaved`) with what the server answered,
//   so the Center can show the card lines the save made without reading the Flow again.
// - flowgate.v02.0059 T#2: an undone entry waits in a redo stack (same renames as the undo stack);
//   a new edit empties it, a fresh read of the Flow empties both.
// Pure TypeScript, no Vue: the Center session mirrors `status` into its reactive state.
import type { FlowDraftError, FlowDraftSaveResult } from '../graph/flowGraphSource'
import { CLIENT_KEY_PREFIX, draftContent, remapDraft, type DraftModel } from './draftModel'
import { applyCommand, applyCommandsWithUndo, remapCommand, renameCommandKeys, reviveCommands, type EditCommand } from './graphEditCommands'

export const DRAFT_SAVE_DELAY_MS = 400
const UNDO_LIMIT = 100

/** `idle` saved, `pending` waiting to send, `saving` on the wire, `error` / `conflict` refused. */
export type DraftEditStatus = 'idle' | 'pending' | 'saving' | 'error' | 'conflict'

export interface DraftEditorOptions {
  save(version: number, content: ReturnType<typeof draftContent>): Promise<FlowDraftSaveResult>
  /** The draft (and undo) after a save renamed `tmp_` keys. */
  onKeys(keyMap: Readonly<Record<string, string>>): void
  /** flowgate.v02.0053 T#5: a save was stored (after `onKeys`); its answer carries the card lines. */
  onSaved?(result: FlowDraftSaveResult): void
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
  canRedo(): boolean
  /** True while an edit is waiting or a save runs — a Graph read now could be older. */
  busy(): boolean
  /** `undoable: false` — the edit is saved but never offered to undo (a Flow's default node). */
  apply(command: EditCommand, undoable?: boolean): boolean
  undo(): boolean
  redo(): boolean
  /** Sends a waiting save now. */
  flush(): void
  /** Resolves when nothing waits or runs: true when the draft is saved, false when refused. */
  settled(): Promise<boolean>
  /** A fresh read of the Flow: the draft as stored, its version, and no undo / redo. */
  reset(model: DraftModel, version: number): void
  newKey(): string
  dispose(): void
}

let keySeq = 0

export function newClientKey(): string {
  keySeq += 1
  return `${CLIENT_KEY_PREFIX}${Date.now().toString(36)}${keySeq.toString(36)}`
}

/** The key a command adds (a node / an edge), else null. */
const addedKey = (command: EditCommand) =>
  command.type === 'add_node' ? command.node.key : command.type === 'connect' ? command.edge.key : null

const storedKeys = (model: DraftModel) =>
  new Set([...model.nodes, ...model.edges].filter((x) => !x.isNew).map((x) => x.key))

export function createDraftEditor(model: DraftModel, version: number, options: DraftEditorOptions): DraftEditor {
  const delay = options.delayMs ?? DRAFT_SAVE_DELAY_MS
  let current = model
  let saved = version
  let status: DraftEditStatus = 'idle'
  let errorCode: string | null = null
  let undoStack: EditCommand[][] = []
  let redoStack: EditCommand[][] = []
  let timer: ReturnType<typeof setTimeout> | null = null
  let editSeq = 0
  let sentSeq = 0
  let inFlight = false
  let disposed = false
  let savedKeys = storedKeys(model)
  let sentModel: DraftModel | null = null
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
    const sent = current
    sentModel = sent
    setStatus('saving')
    let request: Promise<FlowDraftSaveResult>
    try {
      request = options.save(saved, draftContent(sent))
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
          redoStack = redoStack.map((entry) => entry.map((c) => remapCommand(c, result.keyMap)))
          options.onKeys(result.keyMap)
        }
        options.onSaved?.(result)
        // What the server holds now is what this save sent, not the (maybe newer) editor model.
        savedKeys = storedKeys(remapDraft(sent, result.keyMap))
        sentModel = null
        if (editSeq !== sentSeq) {
          send()
          return
        }
        setStatus('idle')
        if (timer === null) settle(true)
      },
      (error: FlowDraftError) => {
        inFlight = false
        sentModel = null
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

  const pushed = (stack: EditCommand[][], entry: EditCommand[]) => [...stack.slice(-(UNDO_LIMIT - 1)), entry]

  /** `history: 'new'` an edit of the user (redo is dropped), 'undo' / 'redo' a step through the history. */
  function edit(next: DraftModel, history: 'new' | 'undo' | 'redo', entry: EditCommand[] | null = null) {
    current = next
    if (history === 'new') {
      if (entry) undoStack = pushed(undoStack, entry)
      redoStack = []
    } else if (history === 'undo') {
      if (entry) redoStack = pushed(redoStack, entry)
    } else if (entry) {
      undoStack = pushed(undoStack, entry)
    }
    editSeq += 1
    if (status === 'error') errorCode = null
    schedule()
  }

  /**
   * Takes the last entry of a stack and applies it. A saved node / edge the entry re-adds has no
   * stored key any more and comes back under a new one; the rest of the history follows the rename.
   */
  function step(from: 'undo' | 'redo'): boolean {
    const stack = from === 'undo' ? undoStack : redoStack
    if (status === 'conflict' || !stack.length) return false
    const original = stack[stack.length - 1]
    // A save on the wire deletes every stored key its model lacks: those keys are gone for a revive.
    const serverKeys = sentModel ? new Set([...savedKeys].filter((k) => storedKeys(sentModel as DraftModel).has(k))) : savedKeys
    const entry = reviveCommands(original, serverKeys, newClientKey)
    const rest = stack.slice(0, -1)
    const applied = applyCommandsWithUndo(current, entry)
    if (!applied) {
      if (from === 'undo') undoStack = rest
      else redoStack = rest
      options.onChange()
      return false
    }
    const renamed: Record<string, string> = {}
    original.forEach((command, i) => {
      const was = addedKey(command)
      const now = addedKey(entry[i])
      if (was !== null && now !== null && was !== now) renamed[was] = now
    })
    const follow = (entries: EditCommand[][]) =>
      Object.keys(renamed).length ? entries.map((e) => e.map((c) => renameCommandKeys(c, renamed))) : entries
    if (from === 'undo') {
      undoStack = follow(rest)
      redoStack = follow(redoStack)
    } else {
      redoStack = follow(rest)
      undoStack = follow(undoStack)
    }
    edit(applied.model, from, applied.undo)
    return true
  }

  return {
    model: () => current,
    version: () => saved,
    status: () => status,
    errorCode: () => errorCode,
    canUndo: () => undoStack.length > 0 && status !== 'conflict',
    canRedo: () => redoStack.length > 0 && status !== 'conflict',
    busy,
    apply(command, undoable = true) {
      if (status === 'conflict') return false
      const applied = applyCommand(current, command)
      if (!applied) return false
      edit(applied.model, 'new', undoable ? [...applied.undo] : null)
      return true
    },
    undo: () => step('undo'),
    redo: () => step('redo'),
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
      redoStack = []
      editSeq = sentSeq
      errorCode = null
      savedKeys = storedKeys(model)
      sentModel = null
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
