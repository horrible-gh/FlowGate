// Designer context menu contract: the 40 command
// names of the 11 menu kinds, the target snapshots, the item contract (action / one-level submenu /
// separator) and the command request a chosen item turns into. Pure types: no store, API, session
// or shell. `flow.delete` means the existing dispose (DC) request and `*.git.revert` the
// "restore the Flow worktree's uncommitted changes to the group HEAD" request — both stay unwired until a
// later T connects them; no permanent-delete or Base-restore command exists.
// all 40 items are shown where the mockup puts them; one the area has
// not connected is shown disabled and says why — '연결 예정' (PLANNED_COMMANDS, the 2-B existing server contracts) or
// '미구현' (everything else) — and never requests.
// the Designer connects the 11 PLANNED_COMMANDS through their existing server
// contracts (flow.delete = the existing dispose, DC); a host that lends no server path still shows them '연결 예정'.

export const COMMANDS = [
  'project.newGroup', 'project.info', 'group.newFlow', 'group.rename', 'group.info',
  'flow.rename', 'flow.delete', 'folder.open', 'folder.newFolder', 'folder.newFile',
  'folder.rename', 'folder.download', 'folder.upload', 'folder.git.changes',
  'folder.git.revert', 'folder.git.history', 'folder.delete', 'file.open', 'file.rename',
  'file.download', 'file.git.changes', 'file.git.revert', 'file.git.history', 'file.delete',
  'tab.close', 'tab.closeOthers', 'tab.closeAll', 'canvas.addCard', 'canvas.paste',
  'canvas.selectAll', 'canvas.arrange', 'node.detail', 'node.copy', 'node.delete',
  'multi.editSettings', 'multi.copy', 'multi.arrange', 'multi.delete',
  'edge.disconnect', 'palette.addCard',
] as const
/** flowgate.v02gui.0003: `node.copyId` (the card's document id, as the Inspector's ID 복사) is outside the mockup's 40 as well. */
/** `group.archive` / `group.restore` (the Group's display-only archive) are outside the 40 too. */
/** `group.delete` (an empty Group only; never a Flow, document or run) as well. */
export type MenuCommand = typeof COMMANDS[number] | 'flow.baseBranch' | 'flow.archive' | 'flow.restore' | 'node.copyId' | 'group.archive' | 'group.restore' | 'group.delete'
/** 0006-D §5.3 follow-up integration (2-B): an existing server contract a later T connects — '연결 예정' until then. */
export const PLANNED_COMMANDS: readonly MenuCommand[] = Object.freeze([
  'group.rename', 'flow.rename', 'flow.delete', 'folder.newFolder', 'folder.newFile', 'folder.download',
  'folder.upload', 'folder.delete', 'file.download', 'file.git.changes', 'file.delete',
])
/** flowgate.v02.0060 T#2 — the card's existing run menu (0061 T#15: Hold 설정 / 해제, 되돌리기) shown inside the card
 * menu, so one menu stays open on a card. Not part of the mockup's 40; they keep their own rules and path. */
export const CARD_RUN_COMMANDS = ['node.run.holdSet', 'node.run.holdRelease', 'node.run.timeMachine'] as const
export type CardRunCommand = typeof CARD_RUN_COMMANDS[number]
export type MenuItemCommand = MenuCommand | CardRunCommand
export type ConnectedCommands = ReadonlySet<MenuItemCommand> | readonly MenuItemCommand[]
export interface Point { readonly x: number; readonly y: number }
export type ProjectTarget =
  | { readonly kind: 'project'; readonly projectId: string }
  /** `archived` (v02gui.0027): the Group's archive state as its row shows it; absent = not archived. */
  /** `flowTotal` (v02gui.0027 0008-T): every Flow of the Group on the server as its row knows it; absent = unknown. */
  | { readonly kind: 'group'; readonly projectId: string; readonly groupId: string; readonly archived?: boolean; readonly flowTotal?: number }
  | { readonly kind: 'flow'; readonly projectId: string; readonly groupId: string; readonly flowId: string }
interface FileScope {
  readonly projectId: string
  readonly flowId: string
  readonly path: string
  readonly commit: string | null
}
export type FileTarget =
  | (FileScope & { readonly kind: 'folder'; readonly root: boolean })
  | (FileScope & { readonly kind: 'file'; readonly change: string | null })
export interface TabTarget { readonly kind: 'tab'; readonly flowId: string | null; readonly tabIds: readonly string[] }
export type CanvasTarget =
  | { readonly kind: 'canvas'; readonly flowId: string; readonly screen: Point; readonly canvas: Point }
  | { readonly kind: 'node'; readonly flowId: string; readonly nodeId: string }
  | { readonly kind: 'multi'; readonly flowId: string; readonly nodeId: string; readonly selectedNodeIds: readonly string[] }
  | { readonly kind: 'edge'; readonly flowId: string; readonly edgeId: string }
export interface PaletteTarget { readonly kind: 'palette'; readonly definitionId: string }
export type MenuTarget = ProjectTarget | FileTarget | TabTarget | CanvasTarget | PaletteTarget
/** Structurally accepts existing PaletteItemDefinition; the caller supplies the catalog. */
export interface CardDefinition {
  readonly itemId: string
  readonly label: string
  readonly category: 'design' | 'workflow' | 'control'
  readonly nodeKind: string
  readonly docType: string | null
  readonly icon: string
  readonly accent: string
  readonly custom: boolean
}
/** Git state reasons, and the two reasons of an item not connected (Q 459): 'planned' (awaiting integration) / 'unimplemented' (unimplemented). */
export type DisabledReason = 'unlinked' | 'unavailable' | 'no-worktree' | 'denied' | 'planned' | 'unimplemented'
export type UnconnectedReason = Extract<DisabledReason, 'planned' | 'unimplemented'>
interface ItemDisplay {
  readonly key: string
  readonly labelKey?: string
  readonly label?: string
  readonly icon: string
  readonly danger?: boolean
  readonly disabled?: boolean
  readonly disabledReason?: DisabledReason
  /** v02gui.0027 (0008-T): a small line under the label (a message key of the menu and its values), e.g. Delete Group's state. */
  readonly hint?: { readonly key: string; readonly params?: Readonly<Record<string, string | number>> }
}
export interface MenuAction extends ItemDisplay {
  readonly kind: 'action'
  readonly command: MenuItemCommand
  readonly definition?: CardDefinition
  /** alignment · layout / alignment: the Edit › alignment / layout command this child of the submenu stands for. */
  readonly arrange?: string
}
export interface MenuSeparator { readonly kind: 'separator'; readonly key: string }
export interface MenuSubmenu extends ItemDisplay {
  readonly kind: 'submenu'
  readonly children: readonly (MenuAction | MenuSeparator)[]
}
export type MenuItem = MenuAction | MenuSubmenu | MenuSeparator
export interface CommandRequest<T extends MenuTarget = MenuTarget> {
  readonly command: MenuItemCommand
  readonly target: T
  readonly definition?: CardDefinition
  readonly arrange?: string
}
export interface MenuModel<T extends MenuTarget = MenuTarget> {
  readonly target: T
  readonly items: readonly MenuItem[]
  request(key: string): CommandRequest<T> | null
}
export interface PartInput<T extends MenuTarget, A> {
  readonly target: T
  readonly available: A
  readonly connected?: ConnectedCommands
}

