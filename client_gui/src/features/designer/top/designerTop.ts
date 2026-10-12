// flowgate.v02.0038 — Designer top band model (0001-R rev2 / 0007-T): the commands the Header,
// Menu Bar and Toolbar emit, the Menu Bar structure, the search scopes, the layout density
// levels, the Window menu targets and the UI-only run state. Pure data and functions: no
// screen, store, API or persistence.
//
// The parts only emit commands. The DesignerShell handles the ones whose target already exists
// (left / right panel display, panel tabs, run button state, density choice); every other
// command is the event boundary a later Feature group wires (0001-R §8). No keyboard shortcut is
// assigned or shown here — shortcuts are decided in a later step (0001-R §6.7, 0007-T §7).
//
// flowgate.v02.0059 T#2 (0011-CH contract D): 정렬 / 배치 is a submenu of the Edit menu and the Toolbar's
// 정렬 / 배치 ▾ drop-down, both listing the same commands — six 정렬 and two 균등 분배 of the selected cards
// (2 / 3 or more selected, `selectedCount`) and, in the Toolbar's, 자동 배치. The layout density is the
// spacing scale of 자동 배치 (DENSITY_LAYOUT_SCALE, the one place its numbers live); 자율형 has none.

/** Commands of the top band. Window panel / tab and density choices have their own events. */
export type DesignerTopCommand =
  | 'project.new'
  | 'project.open'
  | 'project.close'
  | 'group.new'
  | 'group.open'
  | 'group.close'
  | 'flow.new'
  | 'flow.open'
  | 'flow.close'
  | 'designer.close'
  | 'edit.undo'
  | 'edit.redo'
  | 'edit.cut'
  | 'edit.copy'
  | 'edit.paste'
  | 'edit.delete'
  /** flowgate.v02.0059 T#2: 정렬 / 균등 분배 of the selected cards (DESIGNER_ARRANGE_GROUPS). */
  | DesignerArrangeCommand
  | 'edit.auto-layout'
  | 'edit.select-all'
  /** flowgate.v02.0059 T#1: the Toolbar's [선택] toggle (area / click multi-selection on the Canvas). */
  | 'edit.select-mode'
  | 'run.next'
  | 'run.all'
  | 'run.stop'
  | 'run.rerun'
  | 'run.validate'
  | 'view.fit'
  | 'view.zoom-in'
  | 'view.zoom-out'
  | 'view.zoom-reset'
  | 'help.shortcuts'
  | 'help.guide'
  | 'help.about'
  | 'header.notifications'
  | 'header.ai'
  | 'header.settings'
  | 'header.user'

/** flowgate.v02.0059 T#2: the 정렬 / 분배 commands (the Center's arrange kinds after `edit.`). */
export type DesignerArrangeCommand =
  | 'edit.align-left'
  | 'edit.align-center'
  | 'edit.align-right'
  | 'edit.align-top'
  | 'edit.align-middle'
  | 'edit.align-bottom'
  | 'edit.distribute-horizontal'
  | 'edit.distribute-vertical'

/** The 정렬 / 배치 submenu in display order, one group per separator: horizontal, vertical, 균등 분배. */
export const DESIGNER_ARRANGE_GROUPS: readonly (readonly DesignerArrangeCommand[])[] = [
  ['edit.align-left', 'edit.align-center', 'edit.align-right'],
  ['edit.align-top', 'edit.align-middle', 'edit.align-bottom'],
  ['edit.distribute-horizontal', 'edit.distribute-vertical'],
]

/** How many selected cards a 정렬 (2) / 균등 분배 (3) command needs. */
export function arrangeSelectionMinimum(cmd: DesignerTopCommand): number | null {
  if (cmd === 'edit.distribute-horizontal' || cmd === 'edit.distribute-vertical') return 3
  return cmd.startsWith('edit.align-') ? 2 : null
}

/** File menu levels that carry a "recent" submenu. */
export type DesignerRecentKind = 'project' | 'group' | 'flow'

/** One entry of a recent submenu. The lists are fed from outside; nothing is read here. */
export interface DesignerRecentItem {
  id: string
  name: string
}

export type DesignerRecentLists = Readonly<Record<DesignerRecentKind, readonly DesignerRecentItem[]>>

export const EMPTY_RECENT_LISTS: DesignerRecentLists = Object.freeze({ project: [], group: [], flow: [] })

export interface DesignerRecentRequest {
  kind: DesignerRecentKind
  id: string
}

/** Canvas layout density, widest first (0001-R §3.4). */
export type DesignerDensity = 'very-wide' | 'wide' | 'normal' | 'free'

export const DESIGNER_DENSITIES: readonly DesignerDensity[] = ['very-wide', 'wide', 'normal', 'free']

/**
 * flowgate.v02.0059 T#2 (0011-CH contract D): the spacing scale 자동 배치 uses at each density (1 = the
 * server's auto_layout spacing). 자율형 (`null`) lays nothing out: cards stay where the user put them, and
 * 자동 배치 chosen then uses 1. The numbers were proposed in 0011-CH and are tuned here only.
 */
export const DENSITY_LAYOUT_SCALE: Readonly<Record<DesignerDensity, number | null>> = Object.freeze({
  'very-wide': 1.5,
  wide: 1.25,
  normal: 1,
  free: null,
})

/** Independent display areas: shown / hidden, drawn as check items (0001-R §3.5). */
export type DesignerWindowPanel = 'left' | 'right' | 'inspector' | 'minimap'

export const DESIGNER_WINDOW_PANELS: readonly DesignerWindowPanel[] = ['left', 'right', 'inspector', 'minimap']

/** Tabs inside the left / right panels: move targets, one active per panel (0001-R §3.5). */
export type DesignerWindowTab = 'project' | 'file' | 'palette' | 'conversation' | 'git'

/** Window menu tab groups, one per panel, in display order. */
export const DESIGNER_WINDOW_TAB_GROUPS: readonly (readonly DesignerWindowTab[])[] = [
  ['project', 'file'],
  ['palette', 'conversation', 'git'],
]

/** What the Window menu shows: each panel's visibility and the active tab of each panel. */
export interface DesignerWindowState {
  panels: Readonly<Record<DesignerWindowPanel, boolean>>
  activeTabs: readonly DesignerWindowTab[]
}

/** Global search scopes (0001-R §2.2). */
export type DesignerSearchScope = 'all' | 'flow' | 'node' | 'file' | 'document'

export const DESIGNER_SEARCH_SCOPES: readonly DesignerSearchScope[] = ['all', 'flow', 'node', 'file', 'document']

/**
 * Clickable search examples. i18n `search.examples.<key>` is the label, `search.example_values.<key>`
 * the text the click puts into the input.
 */
export const DESIGNER_SEARCH_EXAMPLES: readonly string[] = ['designer', 'document_id', 'file_name', 'flow_name']

export interface DesignerSearchRequest {
  scope: DesignerSearchScope
  query: string
}

/** Locales of the shared i18n instance, in the order the Header shows them. */
export type DesignerLocale = 'ko' | 'en' | 'ja'

export const DESIGNER_LOCALES: readonly DesignerLocale[] = ['ko', 'en', 'ja']

// --- Menu Bar structure ------------------------------------------------------------------------

export type DesignerMenuKey = 'file' | 'edit' | 'run' | 'view' | 'window' | 'help'

/** One row of a menu. `label` is an i18n key under `gui.designer.top.items`. */
export type DesignerMenuEntry =
  | { kind: 'command'; command: DesignerTopCommand; label: string }
  | { kind: 'recent'; recent: DesignerRecentKind; label: string }
  | { kind: 'density'; label: string }
  /** flowgate.v02.0059 T#2: the 정렬 / 배치 submenu (DESIGNER_ARRANGE_GROUPS). */
  | { kind: 'arrange'; label: string }
  | { kind: 'panel'; panel: DesignerWindowPanel }
  | { kind: 'tab'; tab: DesignerWindowTab }
  | { kind: 'separator' }

export interface DesignerMenu {
  key: DesignerMenuKey
  entries: readonly DesignerMenuEntry[]
}

const item = (cmd: DesignerTopCommand, label: string): DesignerMenuEntry => ({ kind: 'command', command: cmd, label })
const separator: DesignerMenuEntry = { kind: 'separator' }

/** The six menus in display order. No AI menu, no Save / Save As (0001-R §3 / §3.1). */
export const DESIGNER_MENUS: readonly DesignerMenu[] = [
  {
    key: 'file',
    entries: [
      item('project.new', 'project_new'),
      item('project.open', 'project_open'),
      { kind: 'recent', recent: 'project', label: 'project_recent' },
      item('project.close', 'project_close'),
      separator,
      item('group.new', 'group_new'),
      item('group.open', 'group_open'),
      { kind: 'recent', recent: 'group', label: 'group_recent' },
      item('group.close', 'group_close'),
      separator,
      item('flow.new', 'flow_new'),
      item('flow.open', 'flow_open'),
      { kind: 'recent', recent: 'flow', label: 'flow_recent' },
      item('flow.close', 'flow_close'),
      separator,
      item('designer.close', 'designer_close'),
    ],
  },
  {
    key: 'edit',
    entries: [
      item('edit.undo', 'undo'),
      item('edit.redo', 'redo'),
      separator,
      item('edit.cut', 'cut'),
      item('edit.copy', 'copy'),
      item('edit.paste', 'paste'),
      item('edit.delete', 'delete'),
      separator,
      { kind: 'arrange', label: 'arrange' },
      item('edit.auto-layout', 'auto_layout'),
      separator,
      item('edit.select-all', 'select_all'),
    ],
  },
  {
    key: 'run',
    entries: [
      item('run.next', 'run_next'),
      item('run.all', 'run_all'),
      separator,
      item('run.stop', 'run_stop'),
      item('run.rerun', 'run_rerun'),
      separator,
      item('run.validate', 'run_validate'),
    ],
  },
  {
    key: 'view',
    entries: [
      item('view.fit', 'zoom_fit'),
      item('view.zoom-in', 'zoom_in'),
      item('view.zoom-out', 'zoom_out'),
      item('view.zoom-reset', 'zoom_reset'),
      separator,
      { kind: 'density', label: 'density' },
    ],
  },
  {
    key: 'window',
    entries: [
      ...DESIGNER_WINDOW_PANELS.map((panel): DesignerMenuEntry => ({ kind: 'panel', panel })),
      ...DESIGNER_WINDOW_TAB_GROUPS.flatMap((group): DesignerMenuEntry[] => [
        separator,
        ...group.map((tab): DesignerMenuEntry => ({ kind: 'tab', tab })),
      ]),
    ],
  },
  {
    key: 'help',
    entries: [item('help.shortcuts', 'shortcuts'), item('help.guide', 'guide'), separator, item('help.about', 'about')],
  },
]

// --- UI-only run state -------------------------------------------------------------------------

/** Which run button started the current run, or null when nothing runs. */
export type DesignerRunMode = 'next' | 'all' | null

/** Screen state of the top band, owned by one DesignerShell like its layout. Never persisted. */
export interface DesignerTopState {
  runMode: DesignerRunMode
  density: DesignerDensity
}

export function createDesignerTopState(): DesignerTopState {
  return { runMode: null, density: 'normal' }
}

/**
 * Run mode after a command (0001-R §4.1.1): starting sets the button that was used, stopping
 * clears it, and a start while a run is on changes nothing. UI state only — no execution or stop
 * contract stands behind it yet, so a run ends only by stopping it.
 */
export function nextRunMode(current: DesignerRunMode, cmd: DesignerTopCommand): DesignerRunMode {
  if (cmd === 'run.stop') return null
  if (current !== null) return current
  if (cmd === 'run.next') return 'next'
  if (cmd === 'run.all') return 'all'
  return current
}

// --- menu targets (flowgate.v02.0039 T#3 / v02.0042 / v02.0043) ---------------------------------

/**
 * What the Designer holds for the menus to act on. `flow` is the work target of 0042 — an open
 * Flow — behind the Edit / Run / View commands, the layout density and Flow 닫기. `project` is
 * the Project context (the Project opened with 열기, 0040) behind the Group / Flow commands,
 * their recent lists and Project 닫기. `group` is a Group row chosen in the Project Explorer,
 * behind Group 닫기. The File menu follows each command's own precondition (0043 0001-R §3),
 * not the work target alone: without it a first Flow could never be opened from the menu.
 */
export interface DesignerMenuTargets {
  project: boolean
  group: boolean
  flow: boolean
  /**
   * flowgate.v02.0059 T#2: whether the open Flow's structure edits can be undone / redone. Left out
   * (undefined) the command follows the open Flow alone; the Shell states both explicitly.
   */
  canUndo?: boolean
  canRedo?: boolean
  /** flowgate.v02.0059 T#2: how many cards of the open Flow are selected (정렬 2+, 균등 분배 3+). */
  selectedCount?: number
}

export const ALL_MENU_TARGETS: DesignerMenuTargets = Object.freeze({ project: true, group: true, flow: true })

export const NO_MENU_TARGETS: DesignerMenuTargets = Object.freeze({ project: false, group: false, flow: false })

/**
 * Commands that never wait for a target (0039 confirmed design 15-designer-shell-initial-state-v7):
 * creating / opening a Project, closing the Designer, Help and the Header entries. The Window
 * menu is not a command list and stays available as a whole.
 */
const ALWAYS_COMMANDS: ReadonlySet<DesignerTopCommand> = new Set<DesignerTopCommand>([
  'project.new',
  'project.open',
  'designer.close',
  'help.shortcuts',
  'help.guide',
  'help.about',
  'header.notifications',
  'header.ai',
  'header.settings',
  'header.user',
])

/** Commands that need the Project context only. */
const PROJECT_COMMANDS: ReadonlySet<DesignerTopCommand> = new Set<DesignerTopCommand>([
  'project.close',
  'group.new',
  'group.open',
  'flow.new',
  'flow.open',
])

/**
 * Whether a menu command can be chosen. ALWAYS_COMMANDS always can; PROJECT_COMMANDS need the
 * Project context, Group 닫기 a chosen Group, Flow 닫기 an open Flow; everything else needs the
 * open Flow and then the run state decides.
 */
export function isMenuCommandEnabled(
  cmd: DesignerTopCommand,
  runMode: DesignerRunMode,
  targets: DesignerMenuTargets = ALL_MENU_TARGETS,
): boolean {
  if (ALWAYS_COMMANDS.has(cmd)) return true
  if (PROJECT_COMMANDS.has(cmd)) return targets.project
  if (cmd === 'group.close') return targets.group
  if (cmd === 'flow.close') return targets.flow
  if (!targets.flow) return false
  if (cmd === 'edit.undo') return targets.canUndo !== false
  if (cmd === 'edit.redo') return targets.canRedo !== false
  const minimum = arrangeSelectionMinimum(cmd)
  if (minimum !== null) return (targets.selectedCount ?? 0) >= minimum
  if (cmd === 'run.stop') return runMode !== null
  if (cmd === 'run.next' || cmd === 'run.all' || cmd === 'run.rerun') return runMode === null
  return true
}

/**
 * Whether a submenu entry can be opened: 최근 Project always, 최근 Group / Flow with the Project
 * context, the layout density and 정렬 / 배치 with an open Flow.
 */
export function isSubmenuEnabled(entry: DesignerMenuEntry, targets: DesignerMenuTargets = ALL_MENU_TARGETS): boolean {
  if (entry.kind === 'density' || entry.kind === 'arrange') return targets.flow
  if (entry.kind === 'recent') return entry.recent === 'project' || targets.project
  return true
}

/** Toolbar zoom value: a Canvas zoom factor as a whole percent (1x when there is none). */
export function zoomPercent(zoom: number | null | undefined): number {
  return Math.round((zoom ?? 1) * 100)
}
