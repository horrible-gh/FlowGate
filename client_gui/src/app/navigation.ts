import type { NavGlyphName } from './components/navGlyphs'

// The v0.2 screen map (T0011 §8/§12): one entry per top-level /app screen, in the order the
// approved 01-dashboard.html sidebar draws them. The router builds its routes from this list
// and the Sidebar renders it, so a label/destination can never drift between the two.
// Labels live in i18n under gui.nav.<key>.{title,sub}.
export type AppNavKey = 'dashboard' | 'projects' | 'designer' | 'git' | 'ai' | 'settings'

export interface AppNavItem {
  key: AppNavKey
  /** Path inside the /app router base ('/' is /app itself). */
  path: string
  glyph: NavGlyphName
  /** Icon colour from the mockup's sidebar. */
  accent: string
}

export const APP_NAV_ITEMS: readonly AppNavItem[] = [
  { key: 'dashboard', path: '/', glyph: 'home', accent: '#57aaff' },
  { key: 'projects', path: '/projects', glyph: 'folder', accent: '#a9c5ef' },
  { key: 'designer', path: '/designer', glyph: 'flow', accent: '#4fe0d3' },
  { key: 'git', path: '/git', glyph: 'git', accent: '#a47bff' },
  { key: 'ai', path: '/ai', glyph: 'robot', accent: '#9fb6e1' },
  { key: 'settings', path: '/settings', glyph: 'gear', accent: '#b7c9e4' },
]

/** Screens that exist only as a "준비 중" placeholder in this step (T0011 §8/§9). */
export const PLACEHOLDER_NAV_KEYS: readonly AppNavKey[] = ['projects', 'designer', 'git', 'settings']