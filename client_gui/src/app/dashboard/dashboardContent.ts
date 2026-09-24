import type { NavGlyphName } from '../components/navGlyphs'

// What the Dashboard shows, in the order of the approved 01-dashboard.html. The Dashboard
// only links to screens; it carries no project/Git/AI data or logic of its own (T0011 §9).

export type DashboardCardKey = 'projects' | 'designer' | 'git' | 'ai' | 'settings' | 'quick'
export type DashboardCardTone = 'blue' | 'cyan' | 'purple' | 'orange' | 'indigo'

export interface DashboardCard {
  key: DashboardCardKey
  tone: DashboardCardTone
  glyph: NavGlyphName
  /** Icon colour from the mockup. */
  accent: string
  /** Destination inside /app, or null while the card has nowhere real to go. */
  to: string | null
}

export const DASHBOARD_CARDS: readonly DashboardCard[] = [
  { key: 'projects', tone: 'blue', glyph: 'folder', accent: '#4ba3ff', to: '/projects' },
  { key: 'designer', tone: 'cyan', glyph: 'flow', accent: '#49dfd0', to: '/designer' },
  { key: 'git', tone: 'purple', glyph: 'git', accent: '#9b72ff', to: '/git' },
  { key: 'ai', tone: 'blue', glyph: 'robot', accent: '#54d2ec', to: '/ai' },
  { key: 'settings', tone: 'orange', glyph: 'gear', accent: '#ffae57', to: '/settings' },
  // "빠른 시작" has no v0.2 screen or action behind it yet, so it is an inert card.
  { key: 'quick', tone: 'indigo', glyph: 'plus', accent: '#9f8cff', to: null },
]

export type DashboardPanelKey = 'recent_projects' | 'recent_groups' | 'attention' | 'notices'

// Right-hand summary panels from the mockup. Their data (recent projects/groups, approval
// and Q&A counts, system notices) belongs to later feature work, so each renders an honest
// "not connected yet" state instead of the mockup's sample rows and counts.
export const DASHBOARD_PANELS: readonly DashboardPanelKey[] = ['recent_projects', 'recent_groups', 'attention', 'notices']
