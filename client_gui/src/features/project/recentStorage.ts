// flowgate.v02.0043 — local persistence of the Designer File menu's 최근 Project / Group / Flow
// lists (0001-R §3.5). Same boundary rule as currentProjectStorage.ts: no other module reads or
// writes these keys. The lists survive a reload, hold the newest entry first, one entry per id,
// and at most RECENT_LIMIT entries each. A stored value that is not a well-formed list is read
// as empty rather than failing the screen.
import type { RecentFlow, RecentGroup, RecentProject } from './types'

export const RECENT_PROJECTS_KEY = 'fg_recent_projects'
export const RECENT_GROUPS_KEY = 'fg_recent_groups'
export const RECENT_FLOWS_KEY = 'fg_recent_flows'
export const RECENT_LIMIT = 10

const isText = (value: unknown): value is string => typeof value === 'string' && value.length > 0
const isRecord = (value: unknown): value is Record<string, unknown> => value !== null && typeof value === 'object'

export const isRecentProject = (value: unknown): value is RecentProject =>
  isRecord(value) && isText(value.id) && isText(value.name)

export const isRecentGroup = (value: unknown): value is RecentGroup =>
  isRecord(value) && isRecentProject(value) && isText(value.projectId)

export const isRecentFlow = (value: unknown): value is RecentFlow =>
  isRecord(value) && isRecentGroup(value) && isText(value.groupId)

/** `entry` first, an older entry with the same id dropped, the list capped. Pure. */
export function pushRecent<T extends { id: string }>(list: readonly T[], entry: T): T[] {
  return [entry, ...list.filter((item) => item.id !== entry.id)].slice(0, RECENT_LIMIT)
}

function readList<T>(key: string, isEntry: (value: unknown) => value is T): T[] {
  const raw = localStorage.getItem(key)
  if (!raw) return []
  try {
    const parsed: unknown = JSON.parse(raw)
    return Array.isArray(parsed) ? parsed.filter(isEntry).slice(0, RECENT_LIMIT) : []
  } catch {
    return []
  }
}

function writeList<T>(key: string, list: readonly T[]): void {
  localStorage.setItem(key, JSON.stringify(list))
}

export const readRecentProjects = (): RecentProject[] => readList(RECENT_PROJECTS_KEY, isRecentProject)
export const readRecentGroups = (): RecentGroup[] => readList(RECENT_GROUPS_KEY, isRecentGroup)
export const readRecentFlows = (): RecentFlow[] => readList(RECENT_FLOWS_KEY, isRecentFlow)

export const writeRecentProjects = (list: readonly RecentProject[]): void => writeList(RECENT_PROJECTS_KEY, list)
export const writeRecentGroups = (list: readonly RecentGroup[]): void => writeList(RECENT_GROUPS_KEY, list)
export const writeRecentFlows = (list: readonly RecentFlow[]): void => writeList(RECENT_FLOWS_KEY, list)
