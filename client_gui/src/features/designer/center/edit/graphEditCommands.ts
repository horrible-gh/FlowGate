// flowgate.v02.0045 S3 — edit commands on the Graph draft (D0004 §2.3 "편집 명령"): add / remove a
// node, connect / disconnect two nodes. Each applied command answers the commands that undo it,
// so the Canvas never edits the Graph directly and every structure edit can be taken back.
// Pure: no Vue, no transport.
import type { DraftEdge, DraftModel, DraftNode } from './draftModel'

export type EditCommand =
  | { readonly type: 'add_node'; readonly node: DraftNode }
  | { readonly type: 'remove_node'; readonly key: string }
  | { readonly type: 'connect'; readonly edge: DraftEdge }
  | { readonly type: 'disconnect'; readonly key: string }

export interface AppliedCommand {
  readonly model: DraftModel
  /** Applied in order, these restore the model the command started from. */
  readonly undo: readonly EditCommand[]
}

/** The edited model and its undo, or null when the command does not apply (unknown key, ...). */
export function applyCommand(model: DraftModel, command: EditCommand): AppliedCommand | null {
  switch (command.type) {
    case 'add_node': {
      if (model.nodes.some((n) => n.key === command.node.key)) return null
      return { model: { ...model, nodes: [...model.nodes, command.node] }, undo: [{ type: 'remove_node', key: command.node.key }] }
    }
    case 'remove_node': {
      const node = model.nodes.find((n) => n.key === command.key)
      if (!node) return null
      const cut = model.edges.filter((e) => e.source === command.key || e.target === command.key)
      return {
        model: { nodes: model.nodes.filter((n) => n !== node), edges: model.edges.filter((e) => !cut.includes(e)) },
        undo: [{ type: 'add_node', node }, ...cut.map((edge): EditCommand => ({ type: 'connect', edge }))],
      }
    }
    case 'connect': {
      const { edge } = command
      const keys = new Set(model.nodes.map((n) => n.key))
      if (!keys.has(edge.source) || !keys.has(edge.target) || model.edges.some((e) => e.key === edge.key)) return null
      return { model: { ...model, edges: [...model.edges, edge] }, undo: [{ type: 'disconnect', key: edge.key }] }
    }
    case 'disconnect': {
      const edge = model.edges.find((e) => e.key === command.key)
      if (!edge) return null
      return { model: { ...model, edges: model.edges.filter((e) => e !== edge) }, undo: [{ type: 'connect', edge }] }
    }
  }
}

/** Applies a batch (an undo entry); null when any command of it does not apply. */
export function applyCommands(model: DraftModel, commands: readonly EditCommand[]): DraftModel | null {
  let current = model
  for (const command of commands) {
    const applied = applyCommand(current, command)
    if (!applied) return null
    current = applied.model
  }
  return current
}

/**
 * Applies a batch and answers the commands that restore the model it started from (the undo of
 * each command, last first) — an undo entry taken, its redo; a redo entry taken, its undo.
 */
export function applyCommandsWithUndo(
  model: DraftModel,
  commands: readonly EditCommand[],
): { model: DraftModel; undo: EditCommand[] } | null {
  let current = model
  const undo: EditCommand[] = []
  for (const command of commands) {
    const applied = applyCommand(current, command)
    if (!applied) return null
    current = applied.model
    undo.unshift(...applied.undo)
  }
  return { model: current, undo }
}

const map = (key: string, keyMap: Readonly<Record<string, string>>) => keyMap[key] ?? key

/** The command with the keys a save issued (a `tmp_` key becomes the stored one). */
export function remapCommand(command: EditCommand, keyMap: Readonly<Record<string, string>>): EditCommand {
  switch (command.type) {
    case 'add_node':
      return keyMap[command.node.key] ? { ...command, node: { ...command.node, key: keyMap[command.node.key], isNew: false } } : command
    case 'remove_node':
    case 'disconnect':
      return { ...command, key: map(command.key, keyMap) }
    case 'connect': {
      const { edge } = command
      return {
        ...command,
        edge: {
          ...edge, key: map(edge.key, keyMap), isNew: keyMap[edge.key] ? false : edge.isNew,
          source: map(edge.source, keyMap), target: map(edge.target, keyMap),
        },
      }
    }
  }
}

/** The command with some keys renamed (an entry revived under new keys; the other history follows). */
export function renameCommandKeys(command: EditCommand, renamed: Readonly<Record<string, string>>): EditCommand {
  switch (command.type) {
    case 'add_node':
      return { ...command, node: { ...command.node, key: map(command.node.key, renamed) } }
    case 'remove_node':
    case 'disconnect':
      return { ...command, key: map(command.key, renamed) }
    case 'connect': {
      const { edge } = command
      return { ...command, edge: { ...edge, key: map(edge.key, renamed), source: map(edge.source, renamed), target: map(edge.target, renamed) } }
    }
  }
}

/**
 * An undo entry re-adding a node / edge whose removal was already saved: a stored key no longer
 * in the draft cannot be sent again (P0008 S3-1), so it comes back as a new one (new identity).
 */
export function reviveCommands(
  commands: readonly EditCommand[],
  savedKeys: ReadonlySet<string>,
  newKey: () => string,
): EditCommand[] {
  const renamed: Record<string, string> = {}
  const out: EditCommand[] = []
  for (const command of commands) {
    if (command.type === 'add_node' && !command.node.isNew && !savedKeys.has(command.node.key)) {
      renamed[command.node.key] = newKey()
      out.push({ ...command, node: { ...command.node, key: renamed[command.node.key], isNew: true } })
    } else if (command.type === 'connect') {
      const { edge } = command
      const revive = !edge.isNew && !savedKeys.has(edge.key)
      out.push({
        ...command,
        edge: {
          ...edge, key: revive ? newKey() : edge.key, isNew: revive || edge.isNew,
          source: map(edge.source, renamed), target: map(edge.target, renamed),
        },
      })
    } else {
      out.push(command)
    }
  }
  return out
}
