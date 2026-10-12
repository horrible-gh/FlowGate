// flowgate.v02.0053 T#9 — an in-memory Graph run server (P0008 §7 S5) for Shell-level tests whose
// `@shared/api` mocks now also receive the top band's run requests: GET …/runs/current, POST …/runs,
// POST /runs/{id}/stop, POST /runs/{id}/resume. One run per Flow; `refuse` makes the next start answer the
// given server error code instead (e.g. `run_policy_undecided`). `calls` records every request in order.

const CURRENT = /^\/api\/v1\/flows\/([^/]+)\/runs\/current$/
const START = /^\/api\/v1\/flows\/([^/]+)\/runs$/
const CONTROL = /^\/api\/v1\/runs\/([^/]+)\/(stop|resume)$/

interface FakeRun {
  run_id: string
  flow_id: string
  revision_no: number
  status: string
  open_node_keys: string[]
  last_event_seq: number
}

export interface FlowRunServer {
  readonly runs: Map<string, FakeRun>
  readonly calls: string[]
  /** The next start of any Flow is refused with this server code (409). */
  refuse(code: string | null): void
  /** The mocked `getRequest` answer for a run read URL, or null for any other URL. */
  get(url: string): { data: unknown } | null
  /** The mocked `postRequest` answer for a run URL, or null for any other URL (a refusal rejects). */
  post(url: string): Promise<{ data: unknown }> | null
}

function refusal(code: string, status = 409): Error {
  return Object.assign(new Error(code), { response: { status, data: { ok: false, error: { code, message: code, params: {} } } } })
}

export function createFlowRunServer(): FlowRunServer {
  const runs = new Map<string, FakeRun>()
  const calls: string[] = []
  let refuseCode: string | null = null
  let seq = 0

  const answer = (run: FakeRun | null) => ({ data: { ok: true, run: run ? { ...run, node_runs: [] } : null } })

  return {
    runs,
    calls,
    refuse(code) {
      refuseCode = code
    },
    get(url) {
      const current = CURRENT.exec(url)
      if (!current) return null
      calls.push(`GET current ${decodeURIComponent(current[1])}`)
      const run = runs.get(decodeURIComponent(current[1])) ?? null
      return answer(run && run.status !== 'completed' ? run : null)
    },
    post(url) {
      const start = START.exec(url)
      if (start) {
        const flowId = decodeURIComponent(start[1])
        calls.push(`POST start ${flowId}`)
        if (refuseCode) {
          const code = refuseCode
          refuseCode = null
          return Promise.reject(refusal(code))
        }
        const active = runs.get(flowId)
        if (active && active.status !== 'completed') return Promise.reject(refusal('run_already_active'))
        seq += 1
        const run: FakeRun = { run_id: `run_${seq}`, flow_id: flowId, revision_no: 1, status: 'running', open_node_keys: ['gn_a'], last_event_seq: 1 }
        runs.set(flowId, run)
        return Promise.resolve(answer(run))
      }
      const control = CONTROL.exec(url)
      if (!control) return null
      const [, runId, action] = control
      calls.push(`POST ${action} ${runId}`)
      const run = [...runs.values()].find((item) => item.run_id === runId)
      if (!run) return Promise.reject(refusal('run_not_found', 404))
      if (action === 'stop' && run.status !== 'running') return Promise.reject(refusal('run_not_running'))
      if (action === 'resume' && run.status !== 'stopped') return Promise.reject(refusal('run_not_stopped'))
      run.status = action === 'stop' ? 'stopped' : 'running'
      run.last_event_seq += 1
      return Promise.resolve(answer(run))
    },
  }
}
