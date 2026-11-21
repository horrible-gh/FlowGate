// Single service boundary for the current-project local persistence contract (the plan the earlier implementation
// /the design). No other module in this feature — and no component outside it — reads or
// writes `localStorage` for this concept directly.
//
// The storage key is the exact one v0.1's two independent stores
// (client/src/main/stores/project.ts, client/src/settings/stores/settings.js) already read
// and write, kept for compatibility. This module intentionally never touches
// `fg_current_branch` — v0.1 resets that key whenever the current project changes
// (project.ts `setCurrentProject`), but the plan the earlier implementation requires the v0.2 Project Feature
// to leave Git branch state alone entirely, so that key does not appear anywhere here.

const CURRENT_PROJECT_STORAGE_KEY = 'fg_current_project_id'

export function readStoredCurrentProjectId(): string | null {
  return localStorage.getItem(CURRENT_PROJECT_STORAGE_KEY) || null
}

export function writeStoredCurrentProjectId(projectId: string): void {
  localStorage.setItem(CURRENT_PROJECT_STORAGE_KEY, projectId)
}

export function clearStoredCurrentProjectId(): void {
  localStorage.removeItem(CURRENT_PROJECT_STORAGE_KEY)
}
