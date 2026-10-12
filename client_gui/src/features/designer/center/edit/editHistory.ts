// flowgate.v02.0059 T#2 (0011-CH contract C) — one entry of a Flow's undo / redo history. Structure
// edits and card moves share one history (the draft editor's stacks), so they are taken back and
// forward in the order they were made:
// - `edit`: draft commands (graphEditCommands.ts) — stepping one changes the draft and saves it.
// - `move`: cards moved by one action — a drag of one card or of the selection, one 정렬 / 분배, one
//   자동 배치 or 배치 밀도 change — with each card's position before and after. Stepping one puts the
//   cards back (`from`) or forward (`to`) through the Center's move path and layout save queue; the
//   draft is not saved for it.
// Keys follow the draft's: a save answering a `tmp_` key, or a node revived under a new key, renames
// the node in every entry. Pure: no Vue, no transport.
import { remapCommand, renameCommandKeys, type EditCommand } from './graphEditCommands'

export interface NodePoint {
  readonly x: number
  readonly y: number
}

/** A card moved by an action: where it was and where it went. */
export interface NodeMove {
  readonly nodeId: string
  readonly from: NodePoint
  readonly to: NodePoint
}

/** Where a card is put. */
export interface NodePlacement {
  readonly nodeId: string
  readonly x: number
  readonly y: number
}

export type HistoryEntry =
  | { readonly kind: 'edit'; readonly commands: readonly EditCommand[] }
  | { readonly kind: 'move'; readonly moves: readonly NodeMove[] }

export const editEntry = (commands: readonly EditCommand[]): HistoryEntry => ({ kind: 'edit', commands })

export const moveEntry = (moves: readonly NodeMove[]): HistoryEntry => ({ kind: 'move', moves })

/** The positions a move entry puts its cards at: back where they were (`undo`) or where they went (`redo`). */
export function movePlacements(moves: readonly NodeMove[], step: 'undo' | 'redo'): NodePlacement[] {
  return moves.map(({ nodeId, from, to }) => ({ nodeId, ...(step === 'undo' ? from : to) }))
}

const renameMoves = (moves: readonly NodeMove[], keyMap: Readonly<Record<string, string>>) =>
  moves.map((move) => (keyMap[move.nodeId] ? { ...move, nodeId: keyMap[move.nodeId] } : move))

/** The entry with the keys a save issued (a `tmp_` key becomes the stored one). */
export function remapEntry(entry: HistoryEntry, keyMap: Readonly<Record<string, string>>): HistoryEntry {
  return entry.kind === 'move'
    ? moveEntry(renameMoves(entry.moves, keyMap))
    : editEntry(entry.commands.map((command) => remapCommand(command, keyMap)))
}

/** The entry with some keys renamed (an entry revived under new keys; the other history follows). */
export function renameEntry(entry: HistoryEntry, renamed: Readonly<Record<string, string>>): HistoryEntry {
  return entry.kind === 'move'
    ? moveEntry(renameMoves(entry.moves, renamed))
    : editEntry(entry.commands.map((command) => renameCommandKeys(command, renamed)))
}

export type HistoryStack = 'undo' | 'redo'

/** The undo and redo stacks of one Flow (newest last), each keeping its newest `limit` entries. */
export function createHistoryStacks(limit: number) {
  let undo: HistoryEntry[] = []
  let redo: HistoryEntry[] = []
  const pushed = (stack: HistoryEntry[], entry: HistoryEntry) => [...stack.slice(-(limit - 1)), entry]
  return {
    top(from: HistoryStack): HistoryEntry | null {
      const stack = from === 'undo' ? undo : redo
      return stack.length ? stack[stack.length - 1] : null
    },
    size: (from: HistoryStack) => (from === 'undo' ? undo : redo).length,
    /** A new action of the user: its entry (if any) is kept for undo and every redo is dropped. */
    push(entry: HistoryEntry | null) {
      if (entry) undo = pushed(undo, entry)
      redo = []
    },
    /** Takes the newest entry off a stack. */
    take(from: HistoryStack) {
      if (from === 'undo') undo = undo.slice(0, -1)
      else redo = redo.slice(0, -1)
    },
    /** Puts an entry on a stack without dropping anything (a stepped entry going the other way). */
    keep(to: HistoryStack, entry: HistoryEntry) {
      if (to === 'undo') undo = pushed(undo, entry)
      else redo = pushed(redo, entry)
    },
    map(change: (entry: HistoryEntry) => HistoryEntry) {
      undo = undo.map(change)
      redo = redo.map(change)
    },
    clear() {
      undo = []
      redo = []
    },
  }
}
