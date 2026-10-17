// flowgate.v02.0045 S3+S4 — Graph draft editing of the open Flows (D0004 §2.3 center/edit, §3.3):
// one draft editor per Flow whose shown Graph is an editable draft, the Palette drop / connect /
// remove / undo commands, and the draft actions — validate (S3-5) and confirm (S4). TR0020 rev3:
// no start-draft / switch-view actions — the Center session picks the shown definition, and a
// Graph Flow's revision is edited as is (its first save creates the draft).
//
// The Center session is the host: it owns the shown Graph, the layout queue and the reads; this
// controller only hands it edited Graphs and asks for reads. A Palette drop arrives as the raw
// drag data; the host's `readDrop` (given by the Shell) says which node it means, so the Canvas
// and the Palette never know each other (D0004 §4.2).
//
// An editor stopped mid-save (tab closed, project changed) keeps its new nodes' shown positions
// and stores them through `keepPositions` once the save answers their keys, Graph gone or not.
//
// TR0020 rev4: nothing about saving is shown. A save the server refuses (`conflict`: saved
// elsewhere first; `error`) is answered the way the removed 다시 불러오기 button was: the Flow is
// read again as stored, which drops the refused local edits (the editor is reset from the read).
//
// flowgate.v02.0053 T#5: a stored save's card lines (the documents the server made for a placed card,
// T#1) go onto the shown Graph at once, so the Inspector's documents tab has them without a read.
//
// flowgate.v02.0059 T#2 (0011-CH contract C): card moves (`recordMove`) share the Flow's undo / redo with
// the structure edits; a stepped move goes back through the host's `placeNodes` (Graph + layout save). A
// Flow whose Graph is no editable draft keeps a moves-only history (an editor without save).
import type { FlowGraph } from '../graph/flowGraph'
import type { FlowDraftCheck, FlowDraftSaveResult, FlowGraphSource } from '../graph/flowGraphSource'
import { checkConnection, type ConnectionCheck } from './connectionRules'
import { createDraftActions } from './draftActions'
import { EMPTY_DRAFT, draftFromGraph, remapGraph, withCardDocuments, withDraft, type CanvasNodeSpec, type DraftModel } from './draftModel'
import { createDraftEditor, type DraftEditor, type DraftEditStatus } from './draftEditor'
import type { NodeMove, NodePlacement } from './editHistory'
import type { EditCommand } from './graphEditCommands'

export type DraftAction = 'validate' | 'confirm'

/** What the Canvas shows about one Flow's draft editing. */
export interface DraftEditState {
  status: DraftEditStatus
  errorCode: string | null
  canUndo: boolean; canRedo: boolean
  /** The action running now, if any. */
  action: DraftAction | null
  /** The last validation (or refused confirmation) of the draft; cleared by the next edit. */
  check: FlowDraftCheck | null
  /** Outcome of the last action: a confirmed revision, or a refusal code. */
  notice: { kind: 'confirmed'; revisionNo: number } | { kind: 'error'; code: string } | null
}

export interface DraftHost {
  readonly source: FlowGraphSource
  projectId(): string | null
  /** The ready Graph of an open Flow, else null. */
  graph(flowId: string): FlowGraph | null
  /** Shows an edited Graph (the Graph stays the session's; its Selection keeps only Nodes it holds). */
  setGraph(flowId: string, graph: FlowGraph): void
  /** Renames the selected node after a save issued its key. */
  renameSelection(flowId: string, keyMap: Readonly<Record<string, string>>): void
  queuePosition(flowId: string, nodeId: string, x: number, y: number): void
  /** flowgate.v02.0059 T#2: puts cards where an undone / redone move has them; whether any card went. */
  placeNodes(flowId: string, placements: readonly NodePlacement[]): boolean
  /** Stores positions of new nodes whose editor was stopped (the tab / project may be gone). */
  keepPositions?(projectId: string, flowId: string, positions: ReadonlyArray<{ nodeId: string; x: number; y: number }>): void
  /** Reads the Flow again once its saves settled (layout and draft). */
  reread(flowId: string): void
  /** The Flow's draft has nothing waiting or running any more (a deferred read may go now). */
  settled?(flowId: string): void
  /** The definition a Graph Flow shows from now on: 'draft' | 'revision' | null (server default). */
  setView(flowId: string, view: string | null): void
  readDrop?(transfer: Pick<DataTransfer, 'getData'>): CanvasNodeSpec | null
  delayMs?: number
}

export function createDraftController(host: DraftHost, edits: Record<string, DraftEditState>) {
  const editors = new Map<string, DraftEditor>()
  // flowgate.v02.0059 T#2: the move history of each open Flow with no editable draft.
  const moveOnly = new Map<string, DraftEditor>()
  const historyOf = (flowId: string) => editors.get(flowId) ?? moveOnly.get(flowId)
  // Stopped editors whose save still runs: their Flow and the positions of their `tmp_` nodes.
  const stopped = new Map<DraftEditor, { flowId: string; positions: Map<string, { x: number; y: number }> }>()

  function stateOf(flowId: string): DraftEditState {
    if (!edits[flowId]) edits[flowId] = { status: 'idle', errorCode: null, canUndo: false, canRedo: false, action: null, check: null, notice: null }
    return edits[flowId]
  }

  const refused = (status: DraftEditStatus) => status === 'conflict' || status === 'error'

  function mirror(flowId: string) {
    const editor = editors.get(flowId)
    const state = stateOf(flowId)
    const before = state.status
    state.status = editor?.status() ?? 'idle'
    state.errorCode = editor?.errorCode() ?? null
    state.canUndo = historyOf(flowId)?.canUndo() ?? false
    state.canRedo = historyOf(flowId)?.canRedo() ?? false
    // A save just refused: read the Flow again as stored (no note, no button — TR0020 rev4).
    const storageFailure = state.status === 'error' && state.errorCode === 'document_storage_unwritable'
    if (editor && refused(state.status) && !refused(before) && !storageFailure) host.reread(flowId)
    // Storage refusal saves nothing. Keep the local edit for a retry, including deferred reads.
    if (editor && !editor.busy() && !storageFailure) host.settled?.(flowId)
  }

  function show(flowId: string, editor: DraftEditor) {
    const graph = host.graph(flowId)
    if (graph) host.setGraph(flowId, withDraft(graph, editor.model()))
  }

  function onKeys(flowId: string, keyMap: Readonly<Record<string, string>>) {
    const graph = host.graph(flowId)
    if (!graph) return
    const renamed = remapGraph(graph, keyMap)
    host.renameSelection(flowId, keyMap) // first: the renamed Graph shown next keeps only the Nodes it holds
    host.setGraph(flowId, renamed)
    // A new node's position is stored once it has its key (a `tmp_` key is unknown to the server).
    for (const key of Object.values(keyMap)) {
      const node = renamed.nodes.find((n) => n.id === key)
      if (node) host.queuePosition(flowId, node.id, node.x, node.y)
    }
  }

  /** flowgate.v02.0053 T#5: the card lines a save answered, on the shown Graph (no read again). */
  function showLines(flowId: string, lines: NonNullable<FlowDraftSaveResult['cardDocuments']>) {
    const graph = host.graph(flowId)
    if (graph) host.setGraph(flowId, withCardDocuments(graph, lines))
  }

  function keepStoppedPositions(pid: string, flowId: string, editor: DraftEditor, keyMap: Readonly<Record<string, string>>) {
    const positions = stopped.get(editor)?.positions
    const kept = Object.entries(keyMap).flatMap(([tmp, nodeId]) => (positions?.has(tmp) ? [{ nodeId, ...positions.get(tmp)! }] : []))
    if (kept.length) host.keepPositions?.(pid, flowId, kept)
  }

  function openEditor(flowId: string, pid: string, model: DraftModel, version: number): DraftEditor {
    const editor: DraftEditor = createDraftEditor(model, version, {
      save: (v, content) => host.source.saveDraft!(pid, flowId, v, content),
      onKeys: (keyMap) => {
        if (editors.get(flowId) === editor) onKeys(flowId, keyMap)
        else keepStoppedPositions(pid, flowId, editor, keyMap)
      },
      onSaved: (result) => {
        if (editors.get(flowId) === editor && result.cardDocuments) showLines(flowId, result.cardDocuments)
      },
      onMove: (placements) => editors.get(flowId) === editor && host.placeNodes(flowId, placements),
      onChange: () => {
        if (editors.get(flowId) === editor) mirror(flowId)
        else if (!editor.busy() && stopped.delete(editor)) host.settled?.(flowId)
      },
      delayMs: host.delayMs,
    })
    return editor
  }

  function keepMoves(flowId: string) {
    const kept = moveOnly.get(flowId)
    if (kept) return kept.reset(EMPTY_DRAFT, 0)
    const history: DraftEditor = createDraftEditor(EMPTY_DRAFT, 0, {
      onKeys: () => {},
      onChange: () => moveOnly.get(flowId) === history && mirror(flowId),
      onMove: (placements) => moveOnly.get(flowId) === history && host.placeNodes(flowId, placements),
    })
    moveOnly.set(flowId, history)
  }

  /** Stops editing the Flow: what waits is sent, new nodes keep the positions shown now. */
  function stop(flowId: string) {
    const editor = editors.get(flowId)
    if (!editor) return
    editors.delete(flowId)
    const shown = new Map((host.graph(flowId)?.nodes ?? []).map((node) => [node.id, node] as const))
    const positions = new Map(editor.model().nodes.filter((n) => n.isNew)
      .map((n) => [n.key, { x: shown.get(n.key)?.x ?? n.x, y: shown.get(n.key)?.y ?? n.y }] as const))
    stopped.set(editor, { flowId, positions })
    editor.dispose()
    if (!editor.busy()) stopped.delete(editor)
  }

  // A save of the Flow waits or runs, also one of a stopped editor (a read now could be older).
  const busy = (flowId: string) =>
    !!editors.get(flowId)?.busy() || [...stopped].some(([editor, entry]) => entry.flowId === flowId && editor.busy())

  /** A Graph read was shown: (re)start the Flow's editor from it, or drop it. */
  function graphLoaded(flowId: string, graph: FlowGraph) {
    const pid = host.projectId()
    const editable = graph.draft?.editable === true && !!host.source.saveDraft && pid !== null
    const existing = editors.get(flowId)
    if (!editable) {
      stop(flowId)
      keepMoves(flowId)
      mirror(flowId)
      return
    }
    moveOnly.delete(flowId)
    const model = draftFromGraph(graph)
    const version = graph.draft!.version
    if (existing) existing.reset(model, version)
    else editors.set(flowId, openEditor(flowId, pid, model, version))
    mirror(flowId)
  }

  function run(flowId: string, command: EditCommand, undoable = true): boolean {
    const editor = editors.get(flowId)
    if (!editor || !editor.apply(command, undoable)) return false
    const state = stateOf(flowId)
    state.check = null
    state.notice = null
    show(flowId, editor)
    mirror(flowId)
    return true
  }

  function addNode(flowId: string, spec: CanvasNodeSpec, x: number, y: number, undoable = true): boolean {
    const editor = editors.get(flowId)
    if (!editor || !Number.isFinite(x) || !Number.isFinite(y)) return false
    return run(flowId, { type: 'add_node', node: { ...spec, key: editor.newKey(), isNew: true, assignee: null, x, y } }, undoable)
  }

  function drop(flowId: string, transfer: Pick<DataTransfer, 'getData'>, x: number, y: number): boolean {
    const spec = editors.has(flowId) && host.readDrop ? host.readDrop(transfer) : null
    return !!spec && addNode(flowId, spec, x, y)
  }

  function check(flowId: string, source: string, target: string): ConnectionCheck {
    const editor = editors.get(flowId)
    return editor ? checkConnection(editor.model(), source, target) : { ok: false, reason: 'not_editable' }
  }

  function connect(flowId: string, source: string, target: string): boolean {
    const editor = editors.get(flowId)
    if (!editor || !check(flowId, source, target).ok) return false
    return run(flowId, { type: 'connect', edge: { key: editor.newKey(), isNew: true, source, target } })
  }

  function stepHistory(flowId: string, move: 'undo' | 'redo'): boolean {
    const editor = historyOf(flowId)
    if (!editor || !editor[move]()) return false // undo / redo of the structure edits and moves (v02.0059 T#2)
    stateOf(flowId).check = null
    if (editors.get(flowId) === editor) show(flowId, editor)
    mirror(flowId)
    return true
  }

  // The validate / confirm actions (draftActions.ts, flowgate.v02.0059 T#2: moved there as they were).
  const { validate, confirm } = createDraftActions({ host, stateOf, editor: (flowId) => editors.get(flowId), stop })

  /** A tab closed / the project changed: send what waits, forget the Flow. */
  function close(flowId: string) {
    stop(flowId)
    moveOnly.delete(flowId)
    delete edits[flowId]
  }

  return {
    graphLoaded,
    busy,
    drop,
    check,
    connect,
    removeNode: (flowId: string, nodeId: string) => run(flowId, { type: 'remove_node', key: nodeId }),
    removeEdge: (flowId: string, edgeId: string) => run(flowId, { type: 'disconnect', key: edgeId }),
    /** flowgate.v02.0045 T0019: a new Flow's default node at (0, 0), not offered to undo. */
    seed: (flowId: string, spec: CanvasNodeSpec) => addNode(flowId, spec, 0, 0, false),
    /** TR0020 rev2: the Flow's editor has something saved or being saved (실행 confirms it first). */
    drawn: (flowId: string) => !!editors.get(flowId) && (editors.get(flowId)!.version() > 0 || editors.get(flowId)!.busy()),
    undo: (flowId: string) => stepHistory(flowId, 'undo'), redo: (flowId: string) => stepHistory(flowId, 'redo'),
    /** flowgate.v02.0059 T#2: cards were moved by one action (one undo entry) / were put somewhere (no entry). */
    recordMove: (flowId: string, moves: readonly NodeMove[]) => historyOf(flowId)?.record(moves) ?? false,
    placed: (flowId: string, placements: readonly NodePlacement[]) => editors.get(flowId)?.place(placements),
    validate,
    confirm,
    /** Reads the Flow again (after a refused save: the stored draft replaces the local edits). */
    reload: (flowId: string) => host.reread(flowId),
    close,
    closeAll() {
      for (const flowId of new Set([...editors.keys(), ...Object.keys(edits)])) close(flowId)
    },
  }
}

export type DraftController = ReturnType<typeof createDraftController>
