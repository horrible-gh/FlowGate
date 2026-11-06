// Shell fixture for the current 0061 run contract: step/full, pause/resume, stop -> stopping.
// Kept separate from the legacy 0053 fixture used by other suites.
interface Run {
  run_id: string
  flow_id: string
  revision_no: number
  status: string
  mode: string
  open_node_keys: string[]
  last_event_seq: number
}
export function createPhaseRunServer() {
  const runs = new Map<string, Run>()
  const calls: string[] = []
  let refused: string | null = null
  const answer = (run: Run | null) => ({ data: { ok: true, run: run ? { ...run, node_runs: [] } : null } })
  return {
    runs, calls,
    refuse(code: string) { refused = code },
    get(url: string) {
      const match = /^\/api\/v1\/flows\/([^/]+)\/runs\/current$/.exec(url)
      if (!match) return null
      calls.push('GET current ' + decodeURIComponent(match[1]))
      return answer(runs.get(decodeURIComponent(match[1])) ?? null)
    },
    post(url: string, body: Record<string, unknown> = {}) {
      const start = /^\/api\/v1\/flows\/([^/]+)\/runs$/.exec(url)
      if (start) {
        const flowId = decodeURIComponent(start[1])
        calls.push('POST ' + String(body.mode) + ' ' + flowId)
        if (refused) {
          const code = refused
          refused = null
          return Promise.reject(Object.assign(new Error(code), {
            response: { status: 409, data: { ok: false, error: { code, message: code, params: {} } } },
          }))
        }
        const run: Run = { run_id: 'run_1', flow_id: flowId, revision_no: 1, status: 'running',
          mode: String(body.mode), open_node_keys: [], last_event_seq: 1 }
        runs.set(flowId, run)
        return Promise.resolve(answer(run))
      }
      const control = /^\/api\/v1\/runs\/([^/]+)\/(pause|resume|stop)$/.exec(url)
      if (!control) return null
      const [, id, action] = control
      const run = [...runs.values()].find((r) => r.run_id === id)
      if (!run) return Promise.reject(new Error('run_not_found'))
      calls.push('POST ' + action + ' ' + id)
      run.status = action === 'pause' ? 'paused' : action === 'resume' ? 'running' : 'stopping'
      run.last_event_seq++
      return Promise.resolve(answer(run))
    },
  }
}
export type PhaseRunServer = ReturnType<typeof createPhaseRunServer>
