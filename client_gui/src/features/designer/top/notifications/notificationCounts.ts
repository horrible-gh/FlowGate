import type { InvokeRun } from './notificationApi'
import type { QaOpenItem } from '@/features/document/api/qa'
export function countLabel(count: number | null): string {
  return count == null || count <= 0 ? '' : count > 99 ? '99+' : String(count)
}
export function notificationCounts(runs: readonly InvokeRun[], questions: readonly QaOpenItem[] | null) {
  return { runs: runs.length, questions: questions?.length ?? null, bell: runs.length + (questions?.length ?? 0) }
}
