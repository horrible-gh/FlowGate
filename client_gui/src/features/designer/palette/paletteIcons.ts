// flowgate.v02.0030 — Palette glyph table (MirageGlass 03-designer-shell, right Palette and the
// "팔레트 종류 추가" icon picker). Every glyph is SVG path data on a 24x24 box drawn with
// stroke="currentColor", the same form as center/canvas/flowNodeKinds.ts, so a Palette item's
// icon key can travel with a drag and be drawn by whoever receives it.
//
// PALETTE_ICON_CHOICES is the picker of the add dialog, in the deck's order. The three
// BUILTIN_ONLY glyphs are drawn by default items only and are not offered in the picker.
// Keys are open: an unknown key draws the 'document' glyph.

export const PALETTE_ICON_CHOICES = [
  'document',
  'file',
  'folder',
  'layers',
  'layout',
  'flow',
  'branch',
  'logic',
  'code',
  'terminal',
  'api',
  'server',
  'cloud',
  'database',
  'table',
  'chart',
  'search',
  'bug',
  'test',
  'check',
  'shield',
  'lock',
  'user',
  'users',
  'settings',
  'package',
  'globe',
  'link',
  'message',
  'clock',
  'play',
  'alert',
  'star',
  'bookmark',
] as const

const BUILTIN_ONLY = ['monitor', 'clipboard', 'pencil'] as const

export type PaletteIconChoice = (typeof PALETTE_ICON_CHOICES)[number]
export type PaletteIconKey = PaletteIconChoice | (typeof BUILTIN_ONLY)[number]

export const DEFAULT_PALETTE_ICON: PaletteIconChoice = 'document'

const ICON_PATHS: Record<PaletteIconKey, string> = {
  document: 'M5 4h14v16H5z M8 9h8M8 13h6',
  file: 'M6 3h8l4 4v14H6z M14 3v5h5',
  folder: 'M3 6h7l2 2h9v10H3z',
  layers: 'M12 3l8 4-8 4-8-4 8-4Z M4 12l8 4 8-4M4 17l8 4 8-4',
  layout: 'M5 4h14a2 2 0 0 1 2 2v12a2 2 0 0 1 -2 2h-14a2 2 0 0 1 -2 -2v-12a2 2 0 0 1 2 -2z M3 9h18M9 9v11',
  flow: 'M4 6a2 2 0 1 0 4 0a2 2 0 1 0 -4 0z M16 6a2 2 0 1 0 4 0a2 2 0 1 0 -4 0z M10 18a2 2 0 1 0 4 0a2 2 0 1 0 -4 0z M8 6h8M7.5 7.5 11 16M16.5 7.5 13 16',
  branch: 'M4 5a2 2 0 1 0 4 0a2 2 0 1 0 -4 0z M16 7a2 2 0 1 0 4 0a2 2 0 1 0 -4 0z M16 17a2 2 0 1 0 4 0a2 2 0 1 0 -4 0z M8 5h3a4 4 0 0 1 4 4v6M8 5v12h8',
  logic: 'M4 5h6v5H4zM14 14h6v5h-6zM14 3h6v5h-6zM10 7h2v9h2',
  code: 'M9 7l-5 5 5 5M15 7l5 5-5 5M13 5l-2 14',
  terminal: 'M5 4h14a2 2 0 0 1 2 2v12a2 2 0 0 1 -2 2h-14a2 2 0 0 1 -2 -2v-12a2 2 0 0 1 2 -2z M7 9l3 3-3 3M12 16h5',
  api: 'M3 12a2 2 0 1 0 4 0a2 2 0 1 0 -4 0z M17 6a2 2 0 1 0 4 0a2 2 0 1 0 -4 0z M17 18a2 2 0 1 0 4 0a2 2 0 1 0 -4 0z M7 12h5a5 5 0 0 0 5-5M12 12a5 5 0 0 1 5 5',
  server: 'M6 4h12a2 2 0 0 1 2 2v2a2 2 0 0 1 -2 2h-12a2 2 0 0 1 -2 -2v-2a2 2 0 0 1 2 -2z M6 14h12a2 2 0 0 1 2 2v2a2 2 0 0 1 -2 2h-12a2 2 0 0 1 -2 -2v-2a2 2 0 0 1 2 -2z M8 7h.01M8 17h.01',
  cloud: 'M7 18h10a4 4 0 0 0 .5-8A6 6 0 0 0 6 9a4.5 4.5 0 0 0 1 9Z',
  database: 'M5 5a7 3 0 1 0 14 0a7 3 0 1 0 -14 0z M5 5v7c0 1.7 3.1 3 7 3s7-1.3 7-3V5M5 12v7c0 1.7 3.1 3 7 3s7-1.3 7-3v-7',
  table: 'M5 4h14a2 2 0 0 1 2 2v12a2 2 0 0 1 -2 2h-14a2 2 0 0 1 -2 -2v-12a2 2 0 0 1 2 -2z M3 9h18M9 4v16M15 4v16',
  chart: 'M4 20V10M10 20V4M16 20v-7M22 20H2',
  search: 'M5 10a5 5 0 1 0 10 0a5 5 0 1 0 -10 0z M14 14l6 6',
  bug: 'M8 9h8v7a4 4 0 0 1-8 0V9Z M9 5l3 3 3-3M5 11h3M16 11h3M5 16h3M16 16h3',
  test: 'M9 3h6M10 3v6l-5 9h14l-5-9V3 M8 15h8',
  check: 'M3 12a9 9 0 1 0 18 0a9 9 0 1 0 -18 0z M8 12l3 3 5-6',
  shield: 'M12 3 20 7v5c0 5-3.5 8-8 9-4.5-1-8-4-8-9V7l8-4Z M8.5 12l2.2 2.2 4.8-5',
  lock: 'M7 10h10a2 2 0 0 1 2 2v6a2 2 0 0 1 -2 2h-10a2 2 0 0 1 -2 -2v-6a2 2 0 0 1 2 -2z M8 10V7a4 4 0 0 1 8 0v3',
  user: 'M8 8a4 4 0 1 0 8 0a4 4 0 1 0 -8 0z M4 21a8 8 0 0 1 16 0',
  users: 'M6 8a3 3 0 1 0 6 0a3 3 0 1 0 -6 0z M15 9a2 2 0 1 0 4 0a2 2 0 1 0 -4 0z M3 20a6 6 0 0 1 12 0M14 15a5 5 0 0 1 7 5',
  settings:
    'M9 12a3 3 0 1 0 6 0a3 3 0 1 0 -6 0z M19 12a7 7 0 0 0-.1-1l2-1.5-2-3.5-2.5 1a7 7 0 0 0-1.7-1L14.5 3h-5L9 6a7 7 0 0 0-1.7 1L4.8 6 3 9.5 5 11a7 7 0 0 0 0 2l-2 1.5L4.8 18l2.5-1a7 7 0 0 0 1.7 1l.5 3h5l.5-3a7 7 0 0 0 1.7-1l2.5 1 1.8-3.5-2-1.5a7 7 0 0 0 .1-1Z',
  package: 'M12 3l8 4-8 4-8-4 8-4Z M4 7v10l8 4 8-4V7M12 11v10',
  globe: 'M3 12a9 9 0 1 0 18 0a9 9 0 1 0 -18 0z M3 12h18M12 3a15 15 0 0 1 0 18M12 3a15 15 0 0 0 0 18',
  link: 'M10 13a5 5 0 0 0 7.1.1l2-2a5 5 0 0 0-7.1-7.1l-1.2 1.2M14 11a5 5 0 0 0-7.1-.1l-2 2A5 5 0 0 0 12 20l1.2-1.2',
  message: 'M4 5h16v11H9l-5 4V5Z',
  clock: 'M3 12a9 9 0 1 0 18 0a9 9 0 1 0 -18 0z M12 7v5l3 2',
  play: 'M3 12a9 9 0 1 0 18 0a9 9 0 1 0 -18 0z M10 8l6 4-6 4V8Z',
  alert: 'M12 3 2.5 20h19L12 3Z M12 9v5M12 17h.01',
  star: 'M12 3l2.8 5.7 6.2.9-4.5 4.4 1.1 6.2-5.6-3-5.6 3 1.1-6.2L3 9.6l6.2-.9L12 3Z',
  bookmark: 'M7 4h10v17l-5-3-5 3V4Z',
  monitor: 'M5 4h14a2 2 0 0 1 2 2v9a2 2 0 0 1 -2 2h-14a2 2 0 0 1 -2 -2v-9a2 2 0 0 1 2 -2z M8 21h8M12 17v4',
  clipboard:
    'M7 4h10a2 2 0 0 1 2 2v12a2 2 0 0 1 -2 2h-10a2 2 0 0 1 -2 -2v-12a2 2 0 0 1 2 -2z M9 4V2h6v2M8 9h8M8 13h8M8 17h5',
  pencil: 'M14 6l4 4-8 8H6v-4l8-8Z M12 8l4 4',
}

export function isPaletteIconChoice(key: string): key is PaletteIconChoice {
  return (PALETTE_ICON_CHOICES as readonly string[]).includes(key)
}

export function paletteIconPath(key: string): string {
  // Own keys only: a key such as 'constructor' must not reach Object.prototype.
  return Object.prototype.hasOwnProperty.call(ICON_PATHS, key)
    ? ICON_PATHS[key as PaletteIconKey]
    : ICON_PATHS[DEFAULT_PALETTE_ICON]
}
