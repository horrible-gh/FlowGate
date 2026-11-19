// Diagnostics accept only a fixed schema; transport objects, URLs and response bodies never enter storage.
export interface TreeReport {
  id: string; project_id: string; screen: 'full_tree' | 'designer_tree'
  status: number | 'no_response'; elapsed_ms: number; occurred_at: string; request_id: string
}
type Send = (report: TreeReport) => Promise<unknown>
export const newTreeReportId = () => `tree-${globalThis.crypto?.randomUUID?.() ?? `${Date.now()}:${Math.random().toString(36).slice(2)}`}`
const key = (owner: string) => `flowgate.user.${owner}.tree-read-failures`
export function readTreeReports(owner: string): TreeReport[] {
  try {
    const rows: unknown = JSON.parse(localStorage.getItem(key(owner)) ?? '[]')
    return Array.isArray(rows) ? rows.filter((r) => r && /^[A-Za-z0-9.:-]{8,200}$/.test(r.id)
      && typeof r.project_id === 'string' && ['full_tree', 'designer_tree'].includes(r.screen)
      && (r.status === 'no_response' || (Number.isInteger(r.status) && r.status >= 400 && r.status <= 599))
      && Number.isInteger(r.elapsed_ms) && r.elapsed_ms >= 0 && typeof r.occurred_at === 'string'
      && typeof r.request_id === 'string').slice(-50).map(clean) : []
  } catch { return [] }
}
function clean(r: TreeReport): TreeReport {
  return { id: r.id, project_id: r.project_id, screen: r.screen, status: r.status,
    elapsed_ms: r.elapsed_ms, occurred_at: r.occurred_at, request_id: r.request_id }
}
function save(owner: string, rows: TreeReport[]) {
  try { localStorage.setItem(key(owner), JSON.stringify(rows.slice(-50).map(clean))) } catch { /* best effort */ }
}
const flushing = new Set<string>()
export async function flushTreeReports(owner: string, send: Send) {
  if (!owner || flushing.has(owner)) return
  flushing.add(owner)
  try {
    for (const report of readTreeReports(owner)) {
      try { await send(report) } catch { break }
      save(owner, readTreeReports(owner).filter((r) => r.id !== report.id))
    }
  } finally { flushing.delete(owner) }
}
export async function reportTreeFailure(owner: string, report: TreeReport, send: Send) {
  if (!owner) return
  if (report.status === 'no_response' || [502, 503, 504].includes(report.status)) {
    save(owner, [...readTreeReports(owner).filter((r) => r.id !== report.id), clean(report)])
  } else {
    try { await send(clean(report)) } catch {
      save(owner, [...readTreeReports(owner), clean(report)])
    }
  }
}
