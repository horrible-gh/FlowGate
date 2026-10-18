import { DEFAULT_PALETTE_ITEMS, paletteItemDefinition } from '@/features/designer/palette/paletteCatalog'
import { COMMANDS, type MenuAction, type MenuItem, type MenuTarget } from '@/features/designer/contextMenu/types'
import { projectTreeMenu } from '@/features/designer/contextMenu/projectTreeMenu'
import { fileExplorerMenu, type FileAvailability } from '@/features/designer/contextMenu/fileExplorerMenu'
import { flowTabMenu } from '@/features/designer/contextMenu/flowTabMenu'
import { canvasMenu } from '@/features/designer/contextMenu/canvasMenu'
import { paletteMenu } from '@/features/designer/contextMenu/paletteMenu'

const labels = ['기본 설계', '프로토콜', '로직', 'DB설계', '조사', '작업계획', '작업', '테스트', '확인']
export const definitions = DEFAULT_PALETTE_ITEMS.map((item, i) => paletteItemDefinition(item, labels[i]))
export const targets: MenuTarget[] = [
  { kind: 'project', projectId: 'p' },
  { kind: 'group', projectId: 'p', groupId: 'g' },
  { kind: 'flow', projectId: 'p', groupId: 'g', flowId: 'f' },
  { kind: 'folder', projectId: 'p', flowId: 'f', path: 'src', root: false, commit: 'c' },
  { kind: 'file', projectId: 'p', flowId: 'f', path: 'src/a', change: 'M', commit: 'c' },
  { kind: 'tab', flowId: 'f', tabIds: ['f', 'other'] },
  { kind: 'canvas', flowId: 'f', screen: { x: 2, y: 3 }, canvas: { x: 20, y: 30 } },
  { kind: 'node', flowId: 'f', nodeId: 'n' },
  { kind: 'multi', flowId: 'f', nodeId: 'n', selectedNodeIds: ['n2', 'n'] },
  { kind: 'edge', flowId: 'f', edgeId: 'e' },
  { kind: 'palette', definitionId: 'task' },
]
export const fileAvailable: FileAvailability = {
  expanded: false, canMutate: true, virtualDeleted: false, canRead: true, git: 'linked',
  hasChanges: true, hasHeadChanges: true, trackedInHead: true,
}
export function modelFor(target: MenuTarget, connected = COMMANDS as readonly typeof COMMANDS[number][], overrides = {}) {
  switch (target.kind) {
    case 'project': case 'group': case 'flow':
      return projectTreeMenu({ target, connected, available: { currentProject: true, disposed: false, ...overrides } })
    case 'folder': case 'file':
      return fileExplorerMenu({ target, connected, available: { ...fileAvailable, ...overrides } })
    case 'tab':
      return flowTabMenu({ target, connected, available: { openTabIds: ['f', 'other'], ...overrides } })
    case 'palette':
      return paletteMenu({ target, connected, available: { canPlace: true, ...overrides } })
    default:
      return canvasMenu({ target, connected, definitions, available: { editable: true, hasSaveTarget: true,
        canSelectAll: true, graphReady: true, structureEditable: true, definitionExists: true, progressed: false, ...overrides } })
  }
}
export const actions = (items: readonly MenuItem[]): MenuAction[] => items.flatMap((item) =>
  item.kind === 'separator' ? [] : item.kind === 'submenu' ? item.children.filter((child): child is MenuAction => child.kind === 'action') : [item])
export const firstConnected = [
  'project.newGroup', 'group.newFlow', 'folder.open', 'tab.close', 'tab.closeOthers', 'tab.closeAll',
  'canvas.addCard', 'canvas.selectAll', 'node.delete', 'edge.disconnect', 'palette.addCard',
] as const


// flowgate.v02.0060 T#1 (WP 0012 [B], [C]): 13 run (1차 11 + 정렬 2), 11 연결 예정 (2-B), 16 미구현 — 40.
export const arrangeConnected = ['canvas.arrange', 'multi.arrange'] as const
export const connectedNow = [...firstConnected, ...arrangeConnected] as const
export const plannedCommands = [
  'group.rename', 'flow.rename', 'flow.delete', 'folder.newFolder', 'folder.newFile', 'folder.download',
  'folder.upload', 'folder.delete', 'file.download', 'file.git.changes', 'file.delete',
] as const
export const unimplementedCommands = [
  'project.info', 'group.info', 'folder.rename', 'folder.git.changes', 'folder.git.revert', 'folder.git.history',
  'file.open', 'file.rename', 'file.git.revert', 'file.git.history', 'canvas.paste', 'node.detail', 'node.copy',
  'multi.editSettings', 'multi.copy', 'multi.delete',
] as const
/** The Edit menu's 정렬 / 배치 (designerTop DESIGNER_ARRANGE_GROUPS) as the connector hands it to the Canvas part. */
export const arrangeGroups = [
  ['edit.align-left', 'edit.align-center', 'edit.align-right'],
  ['edit.align-top', 'edit.align-middle', 'edit.align-bottom'],
  ['edit.distribute-horizontal', 'edit.distribute-vertical'],
] as const
export function arrangeChoices(disabled: (command: string) => boolean = () => false) {
  return arrangeGroups.map((group) => group.map((command) => ({ command, label: command.slice(5), disabled: disabled(command) })))
}
