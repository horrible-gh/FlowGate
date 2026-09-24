// Stroke icons drawn in the approved 01-dashboard.html (24x24 viewBox, stroke=currentColor).
// They name FlowGate screens (project/designer/git/AI/settings), so they are app-owned rather
// than a domain-free components/ui asset (T0011 §6). Static markup only — never user input.
export const NAV_GLYPHS = {
  home: '<path d="M3 10.5 12 3l9 7.5"/><path d="M5 9.5V21h14V9.5"/><path d="M9 21v-7h6v7"/>',
  folder: '<path d="M3 7h6l2 2h10v10H3z"/><path d="M3 7V5h7l2 2"/>',
  flow: '<circle cx="12" cy="5" r="2.4"/><circle cx="5" cy="18" r="2.4"/><circle cx="19" cy="18" r="2.4"/><path d="M12 7.5v4M7.1 16.5l3-3M16.9 16.5l-3-3"/>',
  git: '<circle cx="6" cy="5" r="2"/><circle cx="18" cy="6" r="2"/><circle cx="7" cy="19" r="2"/><path d="M6 7v5c0 2 1 4 4 4h4c3 0 4-2 4-4V8"/>',
  robot: '<rect x="5" y="7" width="14" height="11" rx="3"/><path d="M9 12h.01M15 12h.01M9 15h6M12 3v4M4 10H2M22 10h-2"/>',
  gear: '<path d="M12 8.5a3.5 3.5 0 1 0 0 7 3.5 3.5 0 0 0 0-7Z"/><path d="M19 12a7 7 0 0 0-.1-1l2-1.5-2-3.5-2.5 1A7 7 0 0 0 15 6l-.3-2.7h-4L10.5 6A7 7 0 0 0 9 7L6.5 6l-2 3.5 2 1.5a7 7 0 0 0 0 2L4.5 14.5l2 3.5 2.5-1a7 7 0 0 0 1.5 1l.2 2.7h4L15 18a7 7 0 0 0 1.5-1l2.5 1 2-3.5-2-1.5c.1-.3.1-.7.1-1Z"/>',
  plus: '<path d="M12 4v16M4 12h16"/>',
  search: '<circle cx="11" cy="11" r="7"/><path d="m20 20-3.6-3.6"/>',
  bell: '<path d="M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9"/><path d="M10 21h4"/>',
} as const

export type NavGlyphName = keyof typeof NAV_GLYPHS
