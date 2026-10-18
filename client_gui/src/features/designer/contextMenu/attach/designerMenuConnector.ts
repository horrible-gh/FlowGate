// flowgate.v02.0060 T#2 — the Designer context menu attached (0006-D rev2 2단계-A, §3.1, §3.3, §3.4, §4.2, §5.3):
// the one menu host of the Designer Shell and the connectors of its five areas — ① project tree, ② file explorer,
// ③ Flow tab strip, ④ Canvas, ⑤ Palette card. An area hands over the event and the real ids it holds (row kind,
// node path, flowId, card / edge id, palette definition); the connector builds the snapshot and the availability,
// asks the independent part for the items and opens the menu through the entry rule. Opening reads state only:
// selection, select mode, active Flow / tab, expansion, view, positions and the save / undo queues stay as they are.
//
// A chosen command runs only after the connector built the same menu again from the state of that moment: the
// target must still be there in the same scope (project, active Flow, the Canvas selection and select mode seen at
// open) and the item must still be enabled — the snapshot's availability is never the reason to run. Each command
// runs through the existing path (the Shell's create dialogs and Edit › 전체 선택, the Center session's closeFlow /
// dropOnCanvas / removeNode / removeEdge / chooseCardMenu, the File Explorer's folder expansion, the Palette
// selection the Canvas click places). The card's existing run menu (0061 T#15) is carried into the single card menu
// so one menu is ever open on a card.
//
// flowgate.v02.0060 T#1 (WP 0012, Q item 459): the 11 first-step commands (§5.3 1차 연결) and 정렬 · 배치 / 정렬 are
// connected (CLIENT_COMMANDS, 13); every other item of the mockup's 40 is shown disabled where the mockup puts it —
// 연결 예정 for the 11 2-B commands, 미구현 for the 16 others — and never reaches an execute path here (no request,
// no API call). So the Flow row, the file row, the root folder and a card of a multi-selection open their menus now.
// The multi-selection menu takes no single card command and no run item (§3.6), and changes no selection. 정렬 · 배치
// and 정렬 open the Edit menu's 정렬 / 배치 commands (the Shell's DESIGNER_ARRANGE_GROUPS, its isMenuCommandEnabled
// and its onTopCommand → center.arrangeNodes); no arrange logic of its own.
//
// flowgate.v02.0060 T#2 (WP 0012, 0006-D rev2 §4.3 2-B): the 11 2-B commands are connected through the existing server
// contracts the Shell lends (`server`, features/project → the v0.1 routes): 이름 변경 (Group / Flow), Flow 삭제 = the
// existing dispose (DC), 새 폴더 / 새 파일, 폴더 다운로드 / 업로드 / 삭제, 파일 다운로드 / Git › 수정 확인 / 삭제. A chosen
// one opens its window (DesignerMenuActions) with the target the menu saw; right before the request the window builds the
// menu again from now, so a target that left its scope or an item no longer enabled (canMutate, virtual deleted node,
// read permission, Git state) runs nothing. The connector itself still calls no API. Without `server` (a host that lends
// none) the 11 stay 연결 예정. Flow 삭제 never deletes permanently; no document or branch delete exists here.
//
// The areas reach this through `provide` / `inject` under DESIGNER_CONTEXT_MENU (no import of this folder), so an
// area mounted outside the Designer Shell keeps its old behaviour.

import { markRaw, onBeforeUnmount, provide, shallowRef, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import type { DesignerCenterSession } from '../../center/designerCenterSession'
import type { FlowGraph } from '../../center/graph/flowGraph'
import { isEdgeProgressed } from '../../center/graph/edgeProgress'
import { isEdgeProtected } from '../../center/run/flowRunLock'
import type { FlowCardMenuItem, FlowCardMenuKey } from '../../center/run/flowCardMenu'
import {
  DEFAULT_PALETTE_ITEMS,
  PALETTE_DRAG_MIME,
  paletteItemDefinition,
  type PaletteItemDefinition,
} from '../../palette/paletteCatalog'
import { canvasMenu, canvasNodeTarget, type ArrangeChoice, type CardRunItem } from '../canvasMenu'
import { openContextMenu } from '../entry'
import { fileExplorerMenu, type FileAvailability } from '../fileExplorerMenu'
import { flowTabMenu } from '../flowTabMenu'
import { attachRightButtonGuard } from '../gesture'
import { createDesignerMenuHost, type DesignerMenuHost } from '../host'
import { paletteMenu } from '../paletteMenu'
import { projectTreeMenu } from '../projectTreeMenu'
import type {
  CanvasTarget,
  CardRunCommand,
  CommandRequest,
  FileTarget,
  MenuCommand,
  MenuItemCommand,
  MenuModel,
  Point,
  ProjectTarget,
} from '../types'
import DesignerMenuActions, { type DesignerMenuActionsHandle } from './DesignerMenuActions.vue'
import type { DesignerMenuServerDeps, MenuActionTarget, PendingMenuAction } from './menuActions'
export type { DesignerMenuServerDeps, MenuActionResult } from './menuActions'

/** The injection key the five areas use (`inject(Symbol.for('flowgate.designer.contextMenu'), null)`). */
export const DESIGNER_CONTEXT_MENU = Symbol.for('flowgate.designer.contextMenu')

/** §5.3 1차 연결 (2-A). */
export const FIRST_STEP_COMMANDS: readonly MenuCommand[] = Object.freeze([
  'project.newGroup', 'group.newFlow', 'folder.open', 'tab.close', 'tab.closeOthers', 'tab.closeAll',
  'canvas.addCard', 'canvas.selectAll', 'node.delete', 'edge.disconnect', 'palette.addCard',
])
/** T#1 (WP 0012 [B]): 정렬 · 배치 / 정렬 through the Edit menu's 정렬 / 배치 commands. */
export const ARRANGE_COMMANDS: readonly MenuCommand[] = Object.freeze(['canvas.arrange', 'multi.arrange'])
/** The commands that run on client paths alone (13: 1차 11 + 정렬 2). */
export const CLIENT_COMMANDS: readonly MenuCommand[] = Object.freeze([...FIRST_STEP_COMMANDS, ...ARRANGE_COMMANDS])
/** T#2 (0006-D §4.3 2-B): the 11 existing server contracts, run through the windows and the Shell's `server`. */
export const SERVER_COMMANDS: readonly MenuCommand[] = Object.freeze([
  'group.rename', 'flow.rename', 'flow.delete', 'folder.newFolder', 'folder.newFile', 'folder.download',
  'folder.upload', 'folder.delete', 'file.download', 'file.git.changes', 'file.delete',
])
/** The commands that run (24); the other 16 of the 40 are shown disabled as 미구현. */
export const CONNECTED_COMMANDS: readonly MenuCommand[] = Object.freeze([...CLIENT_COMMANDS, ...SERVER_COMMANDS])
const SERVER_SET: ReadonlySet<string> = new Set(SERVER_COMMANDS)

const RUN_COMMANDS: Readonly<Record<FlowCardMenuKey, CardRunCommand>> = {
  hold_set: 'node.run.holdSet',
  hold_release: 'node.run.holdRelease',
  time_machine: 'node.run.timeMachine',
}
const RUN_KEYS = Object.fromEntries(Object.entries(RUN_COMMANDS).map(([key, command]) => [command, key])) as Record<CardRunCommand, FlowCardMenuKey>

export type ProjectRowRef =
  | { readonly kind: 'project'; readonly projectId: string }
  | { readonly kind: 'group'; readonly projectId: string; readonly groupId: string }
  | { readonly kind: 'flow'; readonly projectId: string; readonly groupId: string; readonly flowId: string }

/** ② A File Explorer row (or its blank area, the root folder). `available` reads the explorer now; null = gone. */
export interface FileMenuEntry {
  readonly target: FileTarget
  readonly available: () => FileAvailability | null
  /** Unfolds the folder through the explorer's own expansion; whether it did. */
  readonly expand?: () => boolean
  readonly fallback?: HTMLElement | null
}

export interface CanvasMenuEntry {
  readonly flowId: string
  readonly kind: 'canvas' | 'node' | 'edge'
  readonly nodeId?: string
  readonly edgeId?: string
  /** Blank Canvas: the Graph point under the pointer (where 카드 추가 puts the card). */
  readonly canvas?: Point
  readonly fallback?: HTMLElement | null
}

export interface PaletteMenuEntry {
  readonly definition: PaletteItemDefinition
  /** The Palette still shows the item. */
  readonly alive?: () => boolean
  readonly fallback?: HTMLElement | null
}

/** What an area calls from its own contextmenu handler. Each returns whether a menu opened. */
export interface DesignerContextMenuApi {
  /** `alive` reads the Explorer's tree of now: false once the row is gone (the menu closes, nothing runs). */
  project(event: MouseEvent, row: ProjectRowRef, fallback?: HTMLElement | null, alive?: () => boolean): boolean
  file(event: MouseEvent, entry: FileMenuEntry): boolean
  tab(event: MouseEvent, flowId: string | null, fallback?: HTMLElement | null): boolean
  canvas(event: MouseEvent, entry: CanvasMenuEntry): boolean
  /** Installs the right-button gesture guard on the Canvas root; returns its disposer. */
  guardCanvas(element: HTMLElement): () => void
  palette(event: MouseEvent, entry: PaletteMenuEntry): boolean
}

/** The Edit menu's 정렬 / 배치, lent by the Shell (its command names, rule and path). */
export interface DesignerArrangeDeps {
  /** DESIGNER_ARRANGE_GROUPS: the commands in order, one group per separator. */
  readonly groups: readonly (readonly string[])[]
  /** isMenuCommandEnabled(command, runPhase, menuTargets) of now. */
  readonly enabled: (command: string) => boolean
  /** onTopCommand(command) on the active Flow, which runs center.arrangeNodes with that kind. */
  readonly run: (flowId: string, command: string) => void
}

/** What the Shell lends the connectors: its state reads and the existing paths the commands run through. */
export interface DesignerContextMenuDeps {
  readonly center: DesignerCenterSession
  readonly projectId: () => string | null
  /** Edit › 전체 선택 is available now (the top menu's own rule). */
  readonly canSelectAll: () => boolean
  /** Edit › 전체 선택 on the active Flow (the top menu's path). */
  readonly selectAll: (flowId: string) => void
  /** File › 새 Group (the top menu's dialog and create path). */
  readonly openGroupCreate: () => void
  /** The 새 Flow dialog with this Group given explicitly (the selection is left as it is). */
  readonly openFlowCreate: (groupId: string) => void
  /** The Palette item becomes the Palette selection the next Canvas click places. */
  readonly selectPalette: (definition: PaletteItemDefinition) => void
  /** 정렬 · 배치 / 정렬 (T#1). */
  readonly arrange: DesignerArrangeDeps
  /** T#2: the 2-B server paths (absent: the 11 stay 연결 예정). */
  readonly server?: DesignerMenuServerDeps
  /** Anything whose change hides an area (Dock collapse, Explorer mode, Tool tab): the menu closes. */
  readonly layout?: () => unknown
}

interface OpenMenu {
  readonly generation: number
  /** The same menu built from the state of now; null when the target left its scope. */
  readonly build: () => MenuModel | null
  /** False once the target is gone or its scope changed (closes the menu, nothing runs). */
  readonly alive: () => boolean
  /** `recheck`: the menu built again from now still has this item (for a window that runs later). */
  readonly execute: (request: CommandRequest, recheck: () => boolean) => void
  used: boolean
}

const requestKey = (request: CommandRequest) =>
  request.definition ? `${request.command}:${request.definition.itemId}` : request.arrange ? `${request.command}:${request.arrange}` : request.command

export function useDesignerContextMenu(deps: DesignerContextMenuDeps) {
  const { t, te } = useI18n()
  const host: DesignerMenuHost = createDesignerMenuHost()
  const { center } = deps
  /** The menu the host shows now, with what it was built from (replaced on every open). */
  const open = shallowRef<OpenMenu | null>(null)
  /** What runs: the 2-B commands only while the Shell lends their server paths. */
  const connected: readonly MenuCommand[] = deps.server ? CONNECTED_COMMANDS : CLIENT_COMMANDS
  /** T#2: the one 2-B window open now (a new one replaces it). */
  const pending = shallowRef<PendingMenuAction | null>(null)
  let actionSeq = 0
  function openAction(target: MenuActionTarget, stillValid: () => boolean) {
    pending.value = { seq: ++actionSeq, target, stillValid }
  }

  // ── shared ────────────────────────────────────────────────────────────────────────────────────

  function show(
    event: MouseEvent,
    build: () => MenuModel | null,
    alive: () => boolean,
    execute: (request: CommandRequest, recheck: () => boolean) => void,
    fallback?: HTMLElement | null,
  ): boolean {
    const opened = openContextMenu(event, host, build(), fallback ?? undefined)
    const session = host.current.value
    if (opened && session) open.value = { generation: session.generation, build, alive, execute, used: false }
    return opened
  }

  /** The menu's `request`: built again now, run only when the target is alive and the item still enabled. */
  function run(request: CommandRequest) {
    const entry = open.value
    if (!entry || entry.used) return
    entry.used = true
    if (!entry.alive()) return
    const now = entry.build()
    if (!now || now.request(requestKey(request)) === null) return
    const key = requestKey(request)
    entry.execute(request, () => entry.alive() && (entry.build()?.request(key) ?? null) !== null)
  }

  // ── ① project tree ────────────────────────────────────────────────────────────────────────────

  function project(event: MouseEvent, row: ProjectRowRef, fallback?: HTMLElement | null, alive?: () => boolean): boolean {
    const target: ProjectTarget = row
    const projectAt = deps.projectId()
    // The row must still be in the Explorer's tree (a re-read may have removed the Group / Flow) — §3.3, §3.4.
    const live = () => deps.projectId() === projectAt && (alive?.() ?? true)
    const build = () => live() ? projectTreeMenu({
      target,
      available: { currentProject: deps.projectId() === target.projectId },
      connected,
    }) : null
    return show(event, build, live, (request, recheck) => {
      const scope = request.target as ProjectTarget
      if (!live() || deps.projectId() !== scope.projectId) return
      if (request.command === 'project.newGroup') deps.openGroupCreate()
      else if (request.command === 'group.newFlow' && scope.kind === 'group') deps.openFlowCreate(scope.groupId)
      else if (!SERVER_SET.has(request.command)) return
      // T#2: 이름 변경 (Group / Flow) and Flow 삭제 (the existing dispose, DC) open their windows on this row.
      else if (request.command === 'group.rename' && scope.kind === 'group') {
        openAction({ kind: 'group.rename', projectId: scope.projectId, groupId: scope.groupId }, recheck)
      } else if ((request.command === 'flow.rename' || request.command === 'flow.delete') && scope.kind === 'flow') {
        openAction({ kind: request.command, projectId: scope.projectId, groupId: scope.groupId, flowId: scope.flowId }, recheck)
      }
    }, fallback)
  }

  // ── ② file explorer ───────────────────────────────────────────────────────────────────────────

  function file(event: MouseEvent, entry: FileMenuEntry): boolean {
    const build = () => {
      const available = entry.available()
      return available ? fileExplorerMenu({ target: entry.target, available, connected }) : null
    }
    const flowAt = center.state.activeFlowId
    const alive = () => center.state.activeFlowId === flowAt && entry.available() !== null
    return show(event, build, alive, (request, recheck) => {
      if (request.command === 'folder.open') {
        entry.expand?.()
        return
      }
      if (!SERVER_SET.has(request.command)) return
      // T#2: the Flow worktree commands open their windows with the node the menu saw (파일 다운로드 runs at once).
      const scope = request.target as FileTarget
      if (scope.kind === 'folder' && request.command.startsWith('folder.')) {
        openAction({
          kind: request.command as Extract<MenuActionTarget, { root: boolean }>['kind'],
          projectId: scope.projectId, flowId: scope.flowId, path: scope.path, root: scope.root,
        }, recheck)
      } else if (scope.kind === 'file' && request.command.startsWith('file.')) {
        openAction({
          kind: request.command as 'file.download' | 'file.git.changes' | 'file.delete',
          projectId: scope.projectId, flowId: scope.flowId, path: scope.path,
        }, recheck)
      }
    }, entry.fallback)
  }

  // ── ③ Flow tabs ───────────────────────────────────────────────────────────────────────────────

  const openTabIds = () => center.state.tabs.map((tab) => tab.flowId)

  function tab(event: MouseEvent, flowId: string | null, fallback?: HTMLElement | null): boolean {
    const target = { kind: 'tab' as const, flowId, tabIds: openTabIds() }
    const projectAt = deps.projectId()
    const build = () => flowTabMenu({ target, available: { openTabIds: openTabIds() }, connected })
    const alive = () => deps.projectId() === projectAt && (flowId === null || openTabIds().includes(flowId))
    return show(event, build, alive, (request) => {
      // The tab ids of this moment, each closed through the session's one close (its pending saves included).
      const ids = openTabIds()
      const close = request.command === 'tab.close'
        ? ids.filter((id) => id === flowId)
        : request.command === 'tab.closeOthers'
          ? ids.filter((id) => id !== flowId)
          : request.command === 'tab.closeAll' ? ids : []
      for (const id of close) center.closeFlow(id)
    }, fallback)
  }

  // ── ④ Canvas ──────────────────────────────────────────────────────────────────────────────────

  /** The shown Canvas of the Flow, or null (not the active Flow, Graph not ready). */
  function canvasState(flowId: string) {
    if (center.state.activeFlowId !== flowId) return null
    const load = center.state.graphs[flowId]
    const view = center.state.views[flowId]
    if (load?.status !== 'ready' || !view) return null
    const graph = load.graph as FlowGraph
    const marks = center.runMarks(flowId)
    return { graph, marks, view, editable: graph.draft?.editable === true && marks.lock === null }
  }

  const selectionKey = (flowId: string) => {
    const view = center.state.views[flowId]
    return JSON.stringify([view?.selectedNodeIds ?? null, center.selectMode.value])
  }

  function cardDefinitions(): PaletteItemDefinition[] {
    return DEFAULT_PALETTE_ITEMS.map((item) => paletteItemDefinition(item, t(`gui.designer.palette.items.${item.labelKey}`)))
  }

  /** The Edit menu's 정렬 / 배치 of now: its groups, its words (gui.designer.top.arrange.*) and its rule. */
  function arrangeChoices(): ArrangeChoice[][] {
    return deps.arrange.groups.map((group) => group.map((command) => ({
      command,
      label: t(`gui.designer.top.arrange.${command.slice('edit.'.length)}`),
      disabled: !deps.arrange.enabled(command),
    })))
  }

  /** The card's existing run menu (0061 T#15) worded as FlowCanvas worded it. */
  function runItems(flowId: string, nodeId: string): CardRunItem[] {
    return center.cardMenu(flowId, nodeId).map((entry: FlowCardMenuItem) => {
      const label = t(`gui.designer.center.card_menu.${entry.key}`)
      const reasonKey = entry.reason ? `gui.designer.center.card_menu.reasons.${entry.reason}` : ''
      return {
        command: RUN_COMMANDS[entry.key],
        label: reasonKey && te(reasonKey) ? t('gui.designer.center.card_menu.with_reason', { label, reason: t(reasonKey) }) : label,
        disabled: entry.disabled,
        danger: entry.danger,
      }
    })
  }

  const CANVAS_CONNECTED: readonly MenuItemCommand[] = Object.freeze([...connected, ...Object.values(RUN_COMMANDS)])

  /** The Canvas menu of the entry from the state of now (the card kind from the selection of now). */
  function buildCanvas(entry: CanvasMenuEntry, screen: Point): MenuModel | null {
    const state = canvasState(entry.flowId)
    if (!state) return null
    const { graph, marks, view, editable } = state
    let target: CanvasTarget
    if (entry.kind === 'canvas') {
      if (!entry.canvas) return null
      target = { kind: 'canvas', flowId: entry.flowId, screen, canvas: entry.canvas }
      return canvasMenu({
        target,
        available: { editable, hasSaveTarget: editable, canSelectAll: deps.canSelectAll(), graphReady: true },
        connected: CANVAS_CONNECTED,
        definitions: cardDefinitions(),
        arrange: arrangeChoices(),
      })
    }
    if (entry.kind === 'node') {
      const node = entry.nodeId ? graph.nodes.find((candidate) => candidate.id === entry.nodeId) : undefined
      if (!node) return null
      target = canvasNodeTarget(entry.flowId, node.id, view.selectedNodeIds)
      // A card of a multi-selection takes no single card command, its run menu included (§3.6); its menu is the
      // multi-selection's (정렬 runs on the selection, the rest 미구현).
      return canvasMenu({
        target,
        available: {
          editable,
          structureEditable: node.structureEditable === true && !marks.protectedNodeIds.has(node.id),
          definitionExists: !!node.definition,
        },
        connected: CANVAS_CONNECTED,
        runItems: target.kind === 'node' ? runItems(entry.flowId, node.id) : [],
        arrange: target.kind === 'multi' ? arrangeChoices() : [],
      })
    }
    const edge = entry.edgeId ? graph.edges.find((candidate) => candidate.id === entry.edgeId) : undefined
    // A reference line is never drawn, so it is never a target.
    if (!edge || edge.edgeClass === 'reference') return null
    target = { kind: 'edge', flowId: entry.flowId, edgeId: edge.id }
    return canvasMenu({
      target,
      available: {
        editable: editable && edge.editable === true,
        progressed: isEdgeProgressed(graph, edge.id) || isEdgeProtected({ edgeClass: edge.edgeClass ?? null, target: edge.target }, marks.protectedNodeIds),
      },
      connected: CANVAS_CONNECTED,
    })
  }

  function canvas(event: MouseEvent, entry: CanvasMenuEntry): boolean {
    const screen = { x: event.clientX, y: event.clientY }
    const projectAt = deps.projectId()
    const selectionAt = selectionKey(entry.flowId)
    const build = () => buildCanvas(entry, screen)
    // The card kind follows selectedNodeIds, so a selection or select mode change closes the menu (§3.4).
    const alive = () => deps.projectId() === projectAt && selectionKey(entry.flowId) === selectionAt && buildCanvas(entry, screen) !== null
    return show(event, build, alive, (request) => {
      const scope = request.target as CanvasTarget
      if (!canvasState(scope.flowId)) return
      if (request.command === 'canvas.addCard' && scope.kind === 'canvas' && request.definition) {
        // The Palette drop path, with the definition the submenu chose, at the point right-clicked.
        const definition = JSON.stringify(request.definition)
        const transfer = { getData: (type: string) => (type === PALETTE_DRAG_MIME ? definition : '') }
        center.dropOnCanvas(scope.flowId, transfer, scope.canvas.x, scope.canvas.y)
      } else if ((request.command === 'canvas.arrange' && scope.kind === 'canvas') || (request.command === 'multi.arrange' && scope.kind === 'multi')) {
        // The Edit menu's 정렬 / 배치 on the active Flow's selection — the one seen at open (alive checked it).
        if (request.arrange) deps.arrange.run(scope.flowId, request.arrange)
      } else if (request.command === 'canvas.selectAll') {
        deps.selectAll(scope.flowId)
      } else if (request.command === 'node.delete' && scope.kind === 'node') {
        center.removeNode(scope.flowId, scope.nodeId)
      } else if (request.command === 'edge.disconnect' && scope.kind === 'edge') {
        center.removeEdge(scope.flowId, scope.edgeId)
      } else if (request.command in RUN_KEYS && scope.kind === 'node') {
        center.chooseCardMenu(scope.flowId, scope.nodeId, RUN_KEYS[request.command as CardRunCommand])
      }
    }, entry.fallback)
  }

  /** The Canvas root keeps the disposer and releases the guard when it unmounts. */
  function guardCanvas(element: HTMLElement): () => void {
    return attachRightButtonGuard(element)
  }

  // ── ⑤ Palette ─────────────────────────────────────────────────────────────────────────────────

  /** Placing needs the open Flow's Canvas to take a card (the Canvas click placement's own condition). */
  function canPlace(): boolean {
    const flowId = center.state.activeFlowId
    return flowId !== null && !!canvasState(flowId)?.editable
  }

  function palette(event: MouseEvent, entry: PaletteMenuEntry): boolean {
    const definition = { ...entry.definition }
    const target = { kind: 'palette' as const, definitionId: definition.itemId }
    const flowAt = center.state.activeFlowId
    const build = () => paletteMenu({ target, available: { canPlace: canPlace() }, connected })
    const alive = () => center.state.activeFlowId === flowAt && (entry.alive?.() ?? true)
    return show(event, build, alive, (request) => {
      if (request.command === 'palette.addCard' && center.state.activeFlowId !== null) deps.selectPalette(definition)
    }, entry.fallback)
  }

  // ── invalidation (§3.4) ───────────────────────────────────────────────────────────────────────

  // The target left (graph re-read, tab closed, tree refreshed, selection changed): close, run nothing.
  watch(
    () => {
      const generation = host.current.value?.generation
      const entry = open.value
      return entry && generation === entry.generation ? entry.alive() : true
    },
    (ok) => {
      if (!ok && open.value) host.invalidate(open.value.generation)
    },
  )
  // Another project or active Flow, or an area hidden.
  watch(() => [deps.projectId(), center.state.activeFlowId, deps.layout?.()], () => host.close())

  // Another project closes a 2-B window (its target belongs to the project it was opened in).
  watch(() => deps.projectId(), () => { pending.value = null })

  onBeforeUnmount(() => {
    host.close()
    pending.value = null
  })

  const api: DesignerContextMenuApi = { project, file, tab, canvas, guardCanvas, palette }
  provide(DESIGNER_CONTEXT_MENU, api)
  /** T#2: the 2-B windows the Shell mounts (`<component :is="dialogs" :actions="actions" />`) and their state. */
  const actions: DesignerMenuActionsHandle | null = deps.server ? {
    pending,
    server: deps.server,
    close: (seq) => {
      if (pending.value?.seq === seq) pending.value = null
    },
  } : null
  return { host, run, api, actions, dialogs: markRaw(DesignerMenuActions) }
}

