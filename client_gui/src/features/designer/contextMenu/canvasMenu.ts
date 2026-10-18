// flowgate.v02.0060 — ④ canvas menu: blank canvas / single card / multi-selected card / edge
// (0006-D rev2 §3.6, §5.3 ④). The card kind is decided from selectedNodeIds membership alone (same
// in move and select mode) and never changes the selection. The add-card submenu lists the supplied
// built-in palette definitions (custom items excluded); the blank-canvas snapshot carries the
// canvas coordinate the card is added at.

import { action as a, makeMenu, separator as s, snapshot, submenu } from './model'
import type { CanvasTarget, CardDefinition, MenuItem, PartInput } from './types'
export interface CanvasAvailability {
  readonly editable: boolean
  readonly hasSaveTarget?: boolean
  readonly canSelectAll?: boolean
  readonly graphReady?: boolean
  readonly structureEditable?: boolean
  readonly definitionExists?: boolean
  readonly progressed?: boolean
}
export interface CanvasMenuInput extends PartInput<CanvasTarget, CanvasAvailability> { readonly definitions?: readonly CardDefinition[] }
/** Selection mode is deliberately not an input: membership alone determines the kind. */
export function canvasNodeTarget(flowId: string, nodeId: string, selectedNodeIds: readonly string[]): CanvasTarget {
  return snapshot(selectedNodeIds.length >= 2 && selectedNodeIds.includes(nodeId)
    ? { kind: 'multi', flowId, nodeId, selectedNodeIds }
    : { kind: 'node', flowId, nodeId })
}
export function canvasMenu({ target, available: v, connected, definitions = [] }: CanvasMenuInput) {
  let items: MenuItem[]
  if (target.kind === 'canvas') {
    const cards = definitions.filter((definition) => !definition.custom).map((definition) => ({
      ...a('canvas.addCard', 'addCard', '＋', !v.editable || !v.hasSaveTarget),
      key: `canvas.addCard:${definition.itemId}`, label: definition.label, definition,
    }))
    items = [
      submenu('canvas.addCard', 'addCard', '＋', cards), s('canvas-paste'),
      a('canvas.paste', 'paste', '▣'), a('canvas.selectAll', 'selectAll', '▢', !v.canSelectAll || !v.graphReady),
      s('canvas-arrange'), a('canvas.arrange', 'arrangeLayout', '≡'),
    ]
  } else if (target.kind === 'node') {
    items = [a('node.detail', 'detail', 'ⓘ'), s('node-copy'), a('node.copy', 'copy', '▣'), s('node-delete'),
      a('node.delete', 'delete', '×', !v.editable || !v.structureEditable || !v.definitionExists, true)]
  } else if (target.kind === 'multi') {
    items = [a('multi.editSettings', 'editSettings', '⚙'), s('multi-copy'), a('multi.copy', 'copy', '▣'),
      a('multi.arrange', 'arrange', '≡'), s('multi-delete'), a('multi.delete', 'delete', '×', false, true)]
  } else {
    items = [a('edge.disconnect', 'disconnect', '⌁', !v.editable || !!v.progressed, true)]
  }
  return makeMenu(target, items, connected)
}

