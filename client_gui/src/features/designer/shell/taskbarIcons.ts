// Line icons of the Designer taskbar, drawn here from a few strokes on a 24px grid (no OS, font or icon-set artwork).
// One per window kind, so the icon-only styles (modern, dock) can tell windows apart, and one per pinned button.
import type { ModelessKind } from '@/core/overlay/modelessRegistry'

// A circle as a path, so every icon is a single `d`.
const ring = (x: number, y: number, r: number) => `M${x - r} ${y}a${r} ${r} 0 1 0 ${2 * r} 0a${r} ${r} 0 1 0 ${-2 * r} 0`

// [Git] and [Search] are not pinned: Git stays in its own tab, search in the header box.
export type TaskbarPin = 'project' | 'attachments' | 'terminal'
export const TASKBAR_PINS: readonly TaskbarPin[] = ['project', 'attachments', 'terminal']

// A folder with an arrow going in: the [Open project] pin and the project window it opens.
const PROJECT_ICON = 'M3 6.5a1 1 0 0 1 1-1h5l2 2h9a1 1 0 0 1 1 1V18a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1z M12 10.5v5 M9.5 13l2.5 2.5 2.5-2.5'

export const KIND_ICONS: Record<ModelessKind, string> = {
  // a page with a folded corner and two lines
  document: 'M6 3h8l4 4v14H6z M14 3v4h4 M9 12h6 M9 16h6',
  // a clock with a turning-back arrow
  history: `M4 12a8 8 0 1 0 2.4-5.7 M4 4v4h4 M12 8v4l3 2`,
  // plus over minus: a change set
  change: 'M12 4v8 M8 8h8 M8 17h8 M4 3h16v18H4z',
  // a note whose bottom corner is turned
  note: 'M5 4h14v10l-6 6H5z M13 20v-6h6 M8 8h8 M8 11h5',
  // a speech balloon with a question mark
  question: 'M4 5h16v11h-9l-4 4v-4H4z M10 9a2 2 0 1 1 2.6 1.9c-.4.2-.6.5-.6 1v.4 M12 14.2v.1',
  // two branches joined
  git: `${ring(7, 5, 2)} ${ring(7, 19, 2)} ${ring(17, 7, 2)} M7 7v10 M17 9c0 5-10 3-10 8`,
  // two lines meeting into one
  merge: `M6 3v4c0 4 6 5 6 9v5 M18 3v4c0 4-6 5-6 9 M9 18l3 3 3-3`,
  // a four-pointed spark
  ai: 'M12 3l2 6.5 6.5 2.5-6.5 2.5L12 21l-2-6.5L3.5 12 10 9.5z',
  // a flask
  test: 'M9 3h6 M10 3v6l-5 9a2 2 0 0 0 1.8 3h10.4a2 2 0 0 0 1.8-3l-5-9V3 M7 15h10',
  // a checklist
  plan: 'M4 6.5l1.5 1.5 2.5-3 M11 6.5h9 M4 12.5l1.5 1.5 2.5-3 M11 12.5h9 M5 18.5h2 M11 18.5h9',
  // two sliders
  settings: `M4 7h9 M19 7h1 M4 17h3 M13 17h7 ${ring(16, 7, 2.5)} ${ring(10, 17, 2.5)}`,
  project: PROJECT_ICON,
  // a plain window
  window: 'M4 5h16v14H4z M4 9h16',
}

export const PIN_ICONS: Record<TaskbarPin, string> = {
  project: PROJECT_ICON,
  // a clip
  attachments: 'M15.5 7.5l-6.4 6.4a1.8 1.8 0 0 0 2.5 2.5l6.7-6.7a3.6 3.6 0 0 0-5.1-5.1l-6.9 6.9a5.4 5.4 0 0 0 7.6 7.6l5.6-5.6',
  // a screen with a prompt
  terminal: 'M3.5 5h17v14h-17z M7 10l3 2.5L7 15 M12.5 15.5h4.5',
}

// FlowGate's own marks for the [Menu] button and the designer item: canvas cards joined by edges, drawn the way the
// canvas draws them (square-cornered links), rather than the 2x2 / 3x3 squares other desktops use.
// [Menu]: one card linked to two cards.
export const MENU_ICON = 'M3 9.5h6v5H3z M15 4h6v5h-6z M15 15h6v5h-6z M9 12h3 M12 6.5v11 M12 6.5h3 M12 17.5h3'
// Designer: two cards with one link between them.
export const CANVAS_ICON = 'M3 4.5h8v6H3z M13 13.5h8v6h-8z M7 10.5v6h6'

// Menu groups and cards, in the same strokes as above (no font symbols, which take the look of the OS font).
export const MENU_CARD_ICONS = {
  // a chip with pins
  ai: 'M7 7h10v10H7z M10 3.5V7 M14 3.5V7 M10 17v3.5 M14 17v3.5 M3.5 10H7 M3.5 14H7 M17 10h3.5 M17 14h3.5',
  // a four-pointed spark
  'my-ai': KIND_ICONS.ai,
  // a speech balloon with two lines
  'question-policy': 'M4 5h16v11h-9l-4 4v-4H4z M8 9h8 M8 12h5',
  // two sliders
  settings: KIND_ICONS.settings,
  // a circle split down the middle, one half striped
  theme: `${ring(12, 12, 8)} M12 4v16 M12 8h3.5 M12 12h8 M12 16h3.5`,
  // a robot head
  agents: 'M6 8.5h12v10H6z M12 4.5v4 M9.5 12.5v1.5 M14.5 12.5v1.5 M3.5 12v3.5 M20.5 12v3.5',
  // a person
  account: `${ring(12, 8, 3.5)} M5 20c0-4 3-6.5 7-6.5s7 2.5 7 6.5`,
  // a shield with a check
  security: 'M12 3l7 3v5c0 5-3 8.5-7 10-4-1.5-7-5-7-10V6z M9 12l2 2 4-4',
} as const
export type MenuIconKey = keyof typeof MENU_CARD_ICONS
