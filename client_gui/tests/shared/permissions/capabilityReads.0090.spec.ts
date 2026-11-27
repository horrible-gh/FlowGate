import { AxiosError, type AxiosAdapter } from 'axios'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

// flowgate.v02test.0090 card "화면 권한" (rejection rework):
//  (1) every path that publishes a capability answer — /auth/me (first read and background refresh), the project list
//      (fetchActiveProjects), a project's own capabilities, the workspace list — belongs to the session that asked and to
//      the order the reads were started in: an answer that leaves before a sign-out / account switch cannot fill the
//      next session's store, and two answers that arrive in the wrong order leave the newer one's;
//  (2) the server sends no permission version, so the FIRST REQUEST after a change — however soon after the last answer —
//      asks again: there is no waiting window. A grant turns buttons on and a loss turns them off without anyone pressing
//      a disabled button; a request that leaves while a round is out queues exactly one more round.
// Only the transport is replaced; the answer shapes are rbac/capabilities.py's.

const api = vi.hoisted(() => ({ get: vi.fn() }))
vi.mock('@shared/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@shared/api')>()),
  getRequest: (path: string, params?: unknown) => api.get(path, params),
}))

import realApi from '@shared/api'
import { loadCurrentUser, setCurrentUser, useCurrentUser } from '@/core/auth/currentUser'
import { fetchActiveProjects } from '@/features/project/api'
import {
  ensureWorkspaceCapabilityList,
  loadProjectCapabilities,
  newProjectDecision,
  parseCapabilities,
  projectCan,
  refreshCapabilities,
  setProjectCapabilities,
  setSystemCapabilities,
  startCapabilityRead,
  systemCan,
  useCapabilities,
} from '@shared/permissions/capabilities'
import { reportRequestLeaving } from '@shared/permissions/requestSignal'
import { PROJECT_ACTIONS, SYSTEM_ACTIONS, resetCapabilities, wireBlock } from '../../helpers/capabilities'

const VIEWER_REFUSED = Object.fromEntries(
  ['project.run', 'project.document.update', 'project.git.write'].map((a) => [a, { reason: 'role_limit', minimum_role: 'role_developer' }]),
)
const viewerProject = () => wireBlock(PROJECT_ACTIONS, 'role_viewer', VIEWER_REFUSED)
const developerProject = () => wireBlock(PROJECT_ACTIONS, 'role_developer')
const adminSystem = () => wireBlock(SYSTEM_ACTIONS, 'system_admin')
const nobodySystem = () => wireBlock(SYSTEM_ACTIONS, null, Object.fromEntries(SYSTEM_ACTIONS.map((a) => [a, { reason: 'system_admin_only' }])))

/** A request whose answer the test hands over later. */
function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((r) => { resolve = r })
  return { promise, resolve }
}
const me = (username: string, caps: unknown) => ({ data: { username, email: null, is_admin: false, capabilities: caps } })
const tick = () => new Promise<void>((resolve) => setTimeout(resolve, 0))

beforeEach(() => {
  api.get.mockReset()
  setCurrentUser(null)
  resetCapabilities()
})
afterEach(() => {
  vi.useRealTimers()
  setCurrentUser(null)
  resetCapabilities()
})

describe('a read that left before sign-out or an account switch fills nothing', () => {
  it('first /auth/me: its person and system block never arrive in the next session', async () => {
    const answer = deferred<unknown>()
    api.get.mockReturnValueOnce(answer.promise)
    const loading = loadCurrentUser()
    setCurrentUser(null) // signed out while /auth/me is still out
    answer.resolve(me('alice', adminSystem()))
    await loading
    expect(useCurrentUser().user.value).toBeNull()
    expect(systemCan('system.settings.manage')).toBe(false)
  })

  it('background /auth/me (a refresh): the same, also for an account switch', async () => {
    setCurrentUser({ username: 'alice', email: null, isAdmin: false })
    setSystemCapabilities(parseCapabilities(nobodySystem()))
    const answer = deferred<unknown>()
    api.get.mockReturnValueOnce(answer.promise).mockReturnValue(new Promise(() => undefined))
    void refreshCapabilities() // currentUser re-reads /auth/me behind the signal
    await tick()
    setCurrentUser({ username: 'bob', email: null, isAdmin: false }) // another person signs in on this screen
    expect(systemCan('system.settings.manage')).toBe(false)
    answer.resolve(me('alice', adminSystem())) // alice's answer comes late
    await tick()
    expect(useCurrentUser().user.value).toMatchObject({ username: 'bob' })
    expect(systemCan('system.settings.manage')).toBe(false) // alice's administrator block did not come back
  })

  it('two /auth/me reads that arrive in the wrong order leave the newer one', async () => {
    setCurrentUser({ username: 'alice', email: null, isAdmin: false })
    setSystemCapabilities(parseCapabilities(nobodySystem()))
    const older = deferred<unknown>()
    const newer = deferred<unknown>()
    api.get.mockReturnValueOnce(older.promise).mockReturnValueOnce(newer.promise).mockReturnValue(new Promise(() => undefined))
    void refreshCapabilities()
    await tick()
    void refreshCapabilities()
    await tick()
    newer.resolve(me('alice', wireBlock(SYSTEM_ACTIONS, 'system_admin')))
    await tick()
    expect(systemCan('system.settings.manage')).toBe(true)
    older.resolve(me('alice', nobodySystem())) // the older question's answer, late
    await tick()
    expect(systemCan('system.settings.manage')).toBe(true)
  })

  it('the project list (fetchActiveProjects) answering after sign-out does not refill the permission store', async () => {
    const answer = deferred<unknown>()
    api.get.mockReturnValueOnce(answer.promise)
    const listing = fetchActiveProjects()
    resetCapabilities() // sign-out
    answer.resolve({ data: { projects: [{ project_id: 'p1', project_name: 'P1', capabilities: developerProject() }] } })
    const projects = await listing
    expect(projects.map((p) => p.id)).toEqual(['p1']) // the caller still gets its list
    api.get.mockReturnValue(new Promise(() => undefined)) // reading p1 now would ask the server: it never answers here
    expect(projectCan('p1', 'project.run')).toBe(false) // the store holds none of the earlier person's blocks
  })

  it('two project lists that arrive in the wrong order leave the newer list\'s blocks', async () => {
    const older = deferred<unknown>()
    const newer = deferred<unknown>()
    api.get.mockReturnValueOnce(older.promise).mockReturnValueOnce(newer.promise)
    const first = fetchActiveProjects()
    const second = fetchActiveProjects()
    newer.resolve({ data: { projects: [{ project_id: 'p1', project_name: 'P1', capabilities: developerProject() }] } })
    await second
    expect(projectCan('p1', 'project.run')).toBe(true)
    older.resolve({ data: { projects: [{ project_id: 'p1', project_name: 'P1', capabilities: viewerProject() }] } })
    await first
    expect(projectCan('p1', 'project.run')).toBe(true) // the older list's viewer block is dropped
  })

  it('a project list answer older than a project\'s own newer read is dropped for that project only', async () => {
    const list = deferred<unknown>()
    api.get.mockReturnValueOnce(list.promise)
    const listing = fetchActiveProjects()
    setProjectCapabilities('p1', parseCapabilities(developerProject()), startCapabilityRead()) // a newer read of p1
    list.resolve({ data: { projects: [
      { project_id: 'p1', project_name: 'P1', capabilities: viewerProject() },
      { project_id: 'p2', project_name: 'P2', capabilities: viewerProject() },
    ] } })
    await listing
    expect(projectCan('p1', 'project.run')).toBe(true)
    expect(projectCan('p2', 'project.view')).toBe(true) // p2 had no newer answer: the list's block is taken
    expect(projectCan('p2', 'project.run')).toBe(false)
  })

  it('a project\'s own capabilities answering after sign-out, and the workspace list answering after sign-out, fill nothing', async () => {
    const projectAnswer = deferred<unknown>()
    const workspaceAnswer = deferred<unknown>()
    api.get.mockImplementation((path: string) => (path.endsWith('/capabilities') ? projectAnswer.promise : workspaceAnswer.promise))
    const project = loadProjectCapabilities('p1')
    const workspace = ensureWorkspaceCapabilityList()
    resetCapabilities()
    projectAnswer.resolve({ data: { capabilities: developerProject() } })
    workspaceAnswer.resolve({ data: { workspaces: [{ workspace_id: 'w', is_default: true, capabilities: wireBlock(['workspace.project.create'], 'workspace_admin') }] } })
    await Promise.all([project, workspace])
    expect(projectCan('p1', 'project.run')).toBe(false)
    expect(newProjectDecision().allowed).toBe(false)
  })
})

describe('the first request after a change asks for the answers again, at once', () => {
  /** What the server answers now, per path; the test changes it to grant or take away. */
  let served: { system: unknown; project: unknown; list: unknown[] | null }
  const capabilityCalls = (suffix = '/capabilities') => api.get.mock.calls.filter(([path]) => String(path).endsWith(suffix)).length
  const listCalls = () => api.get.mock.calls.filter(([path]) => path === '/api/v1/projects').length
  beforeEach(() => {
    setCurrentUser({ username: 'alice', email: null, isAdmin: false })
    served = { system: nobodySystem(), project: viewerProject(), list: null }
    api.get.mockImplementation(async (path: string) => {
      if (path === '/auth/me') return me('alice', served.system)
      if (path === '/api/v1/projects') return { data: { projects: served.list ?? [] } }
      if (path.endsWith('/capabilities')) return { data: { capabilities: served.project } }
      return { data: {} }
    })
    // The answers were taken in just now: no age has passed, which is what the rejected build waited out.
    setSystemCapabilities(parseCapabilities(served.system))
    setProjectCapabilities('p1', parseCapabilities(served.project))
  })

  it('a grant, the very next request: the answers were published a moment ago and the button turns on all the same', async () => {
    expect(projectCan('p1', 'project.run')).toBe(false)
    expect(systemCan('system.settings.manage')).toBe(false)
    served.project = developerProject()
    served.system = adminSystem()
    reportRequestLeaving('/api/v1/projects/p1/groups') // an ordinary request, no time has passed since the answers
    expect(projectCan('p1', 'project.run')).toBe(false) // the old answer stays on show while the new one is out
    await vi.waitFor(() => expect(projectCan('p1', 'project.run')).toBe(true))
    await vi.waitFor(() => expect(systemCan('system.settings.manage')).toBe(true))
  })

  it('a loss, the very next request: the button turns off', async () => {
    served.project = developerProject()
    setProjectCapabilities('p1', parseCapabilities(developerProject()))
    expect(projectCan('p1', 'project.run')).toBe(true)
    served.project = viewerProject() // someone changed the role
    reportRequestLeaving('/api/v1/documents/d1')
    await vi.waitFor(() => expect(projectCan('p1', 'project.run')).toBe(false))
    expect(projectCan('p1', 'project.view')).toBe(true)
    expect(useCapabilities(() => 'p1').refusal('project.run')).toMatchObject({ reason: 'role_limit' })
  })

  it('a rise and then a fall: each request after a change is seen by the screen, one after the other', async () => {
    served.project = developerProject()
    reportRequestLeaving('/api/v1/x')
    await vi.waitFor(() => expect(projectCan('p1', 'project.run')).toBe(true))
    served.project = viewerProject()
    reportRequestLeaving('/api/v1/y') // right after, again: no 10 s to wait
    await vi.waitFor(() => expect(projectCan('p1', 'project.run')).toBe(false))
    expect(capabilityCalls()).toBe(2)
  })

  it('a burst of requests shares the round that is out and queues exactly one more', async () => {
    const first = deferred<unknown>()
    api.get.mockReset()
    api.get.mockImplementation(async (path: string) => {
      if (path === '/auth/me') return me('alice', served.system)
      if (path.endsWith('/capabilities')) return { data: { capabilities: served.project } }
      return { data: {} }
    })
    api.get.mockImplementationOnce(() => first.promise) // the first round's /capabilities (project reads go first), held
    reportRequestLeaving('/api/v1/a')
    for (let n = 0; n < 8; n++) reportRequestLeaving('/api/v1/b' + n) // eight more while it is out
    await tick()
    expect(capabilityCalls()).toBe(1) // still one question: they share it
    first.resolve({ data: { capabilities: viewerProject() } })
    await vi.waitFor(() => expect(capabilityCalls()).toBe(2)) // and one more round, not eight
    await tick()
    await tick()
    expect(capabilityCalls()).toBe(2)
  })

  it('a change that lands while a round is out is seen by the round the next request queued', async () => {
    const stale = deferred<unknown>()
    api.get.mockReset()
    api.get.mockImplementation(async (path: string) => {
      if (path === '/auth/me') return me('alice', served.system)
      if (path.endsWith('/capabilities')) return { data: { capabilities: served.project } }
      return { data: {} }
    })
    api.get.mockImplementationOnce(() => stale.promise)
    reportRequestLeaving('/api/v1/a') // round 1 asks; its answer is out
    await tick()
    served.project = developerProject() // the role changes after round 1's question left
    reportRequestLeaving('/api/v1/b') // the person's next request: queues round 2
    stale.resolve({ data: { capabilities: viewerProject() } }) // round 1 answers with what it saw
    await vi.waitFor(() => expect(projectCan('p1', 'project.run')).toBe(true))
  })

  it('several projects on show: ONE list read carries them all, and only a project the list left out is read singly', async () => {
    const row = (id: string, caps: unknown) => ({ project_id: id, capabilities: caps })
    setProjectCapabilities('p2', parseCapabilities(viewerProject()))
    setProjectCapabilities('p3', parseCapabilities(viewerProject()))
    served.project = developerProject()
    served.list = [row('p1', developerProject()), row('p2', developerProject())] // p3 (archived, say) is not in the list
    reportRequestLeaving('/api/v1/x')
    await vi.waitFor(() => expect(projectCan('p3', 'project.run')).toBe(true))
    expect(projectCan('p1', 'project.run')).toBe(true)
    expect(projectCan('p2', 'project.run')).toBe(true)
    expect(listCalls()).toBe(1)
    expect(capabilityCalls()).toBe(1) // p3 only
  })

  it('a project list that fails falls back to reading each project', async () => {
    setProjectCapabilities('p2', parseCapabilities(viewerProject()))
    served.project = developerProject()
    api.get.mockImplementation(async (path: string) => {
      if (path === '/api/v1/projects') throw new AxiosError('down', 'ERR_NETWORK')
      if (path === '/auth/me') return me('alice', served.system)
      return { data: { capabilities: served.project } }
    })
    reportRequestLeaving('/api/v1/x')
    await vi.waitFor(() => expect(projectCan('p2', 'project.run')).toBe(true))
    expect(projectCan('p1', 'project.run')).toBe(true)
  })

  it('the permission reads themselves do not start another round', async () => {
    for (const path of ['/auth/me', '/api/v1/projects/p1/capabilities', '/rbac/projects/p1/my-permissions', '/api/v1/workspaces', '/api/v1/projects?status=active']) {
      reportRequestLeaving(path)
    }
    await tick()
    expect(api.get).not.toHaveBeenCalled()
  })

  it('a round does not feed itself: after it ends, no further round starts by itself', async () => {
    reportRequestLeaving('/api/v1/x')
    await vi.waitFor(() => expect(capabilityCalls()).toBe(1))
    await tick()
    await tick()
    expect(capabilityCalls()).toBe(1)
    expect(api.get.mock.calls.filter(([path]) => path === '/auth/me')).toHaveLength(1)
  })

  it('nothing on show, nothing to ask: a signed-out screen sends no permission read', async () => {
    setCurrentUser(null)
    resetCapabilities()
    reportRequestLeaving('/api/v1/x')
    await tick()
    expect(api.get).not.toHaveBeenCalled()
  })

  it('a failed re-ask keeps the answer on show', async () => {
    api.get.mockImplementation(async () => { throw new AxiosError('down', 'ERR_NETWORK') })
    reportRequestLeaving('/api/v1/x')
    await tick()
    await tick()
    expect(projectCan('p1', 'project.view')).toBe(true)
    expect(projectCan('p1', 'project.run')).toBe(false)
    expect(useCapabilities(() => 'p1').refusal('project.run')).toMatchObject({ reason: 'role_limit' })
  })

  it('through the real request interceptor: the first request sent after the change asks for the answers again', async () => {
    const adapter: AxiosAdapter = async (config) => ({ status: 200, data: {}, headers: {}, statusText: '', config })
    const original = realApi.defaults.adapter
    realApi.defaults.adapter = adapter
    try {
      served.project = developerProject()
      await realApi.get('/api/v1/projects/p1/groups/tree')
      await vi.waitFor(() => expect(projectCan('p1', 'project.run')).toBe(true))
      served.project = viewerProject()
      await realApi.post('/api/v1/projects/p1/run', {}) // a write is a request too
      await vi.waitFor(() => expect(projectCan('p1', 'project.run')).toBe(false))
    } finally {
      realApi.defaults.adapter = original
    }
  })

  it('coming back to the window asks without any request', async () => {
    served.project = developerProject()
    Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => 'visible' })
    document.dispatchEvent(new Event('visibilitychange'))
    await vi.waitFor(() => expect(projectCan('p1', 'project.run')).toBe(true))
  })
})
