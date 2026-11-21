/**
 * flowgate.v02test.0060 T0005 — the Project tab follows the connection on its own.
 *
 * The real ProjectExplorer, project store and features/project api run; only the transport (`@shared/api`) and the
 * project event hub's subscribe are replaced. The subscribe mock records each subscription so a test can fire its
 * `resync` (the stream reopened / a connection-recovery signal) and see what was released.
 */
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import i18n from '@shared/i18n'
import { notifyGetRetry } from '@shared/requestRecovery'
import { registerAppMessages } from '@/app/i18n'

interface Subscription {
  projectId: string
  events: readonly string[]
  resync?: () => void
  released: boolean
}

const hub = vi.hoisted(() => ({ subs: [] as Subscription[] }))

vi.mock('@/features/project/projectEvents', async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  subscribeProjectEvents: vi.fn((projectId: string, sub: { events: readonly string[]; resync?: () => void }) => {
    const entry: Subscription = { projectId, events: [...sub.events], resync: sub.resync, released: false }
    hub.subs.push(entry)
    return () => { entry.released = true }
  }),
}))

const getRequest = vi.hoisted(() => vi.fn())
vi.mock('@shared/api', () => ({
  getRequest,
  postRequest: vi.fn(),
  extractApiErrorMessage: (_error: unknown, fallback: string) => fallback,
}))

import { ProjectExplorer } from '@/features/designer'

registerAppMessages(i18n)

function n(partial: Record<string, unknown>) {
  return { type_code: null, number: null, filename: null, has_md: false, md_path: null, ...partial }
}

const NODES = [
  n({ id: 'project:p', parent_id: null, node_type: 'project', label: 'Alpha' }),
  n({ id: 'module:p:ui', parent_id: 'project:p', node_type: 'module', label: 'ui', title: 'UI 개선' }),
  n({ id: 'p.ui.0001', parent_id: 'module:p:ui', node_type: 'group', label: 'Designer v0.2', number: '0001' }),
]

const ok = (nodes: unknown[]) => ({ data: { data: { nodes } } })
const down = () => Object.assign(new Error('Network Error'), { code: 'ERR_NETWORK' })

/** A GET that stays open until the test settles it. */
function deferred() {
  let resolve!: (value: unknown) => void
  let reject!: (reason: unknown) => void
  const promise = new Promise((res, rej) => { resolve = res; reject = rej })
  return { promise, resolve, reject }
}

let wrapper: VueWrapper<any> | null = null

async function mountExplorer(props: Record<string, unknown> = { projectId: 'p' }) {
  wrapper = mount(ProjectExplorer, { props, attachTo: document.body, global: { plugins: [i18n] } })
  await flushPromises()
  return wrapper
}

/** The live subscriptions of a Project. */
function live(projectId: string) {
  return hub.subs.filter((sub) => sub.projectId === projectId && !sub.released)
}

/** Fires the resync of the Project's live subscription(s) and lets the re-read land. */
async function resync(projectId = 'p') {
  const subs = live(projectId)
  expect(subs.length).toBe(1)
  subs[0].resync?.()
  await flushPromises()
}

const treeCalls = (projectId = 'p') =>
  getRequest.mock.calls.filter(([url]) => url === `/api/v1/projects/${projectId}/groups/tree`).length

beforeEach(() => {
  setActivePinia(createPinia())
  localStorage.clear()
  getRequest.mockReset()
  hub.subs.length = 0
  i18n.global.locale.value = 'ko'
})

afterEach(() => {
  wrapper?.unmount()
  wrapper = null
})

describe('ProjectExplorer — own resync (0060 T0005)', () => {
  it('subscribes to the current Project with a resync and no event names', async () => {
    getRequest.mockResolvedValueOnce(ok(NODES))
    await mountExplorer()
    const subs = live('p')
    expect(subs).toHaveLength(1)
    expect(subs[0].events).toEqual([])
    expect(typeof subs[0].resync).toBe('function')
  })

  it('(a) re-reads a failed first load on resync and the failure goes away', async () => {
    getRequest.mockRejectedValueOnce(down())
    const w = await mountExplorer()
    expect(w.find('[data-test="project-explorer-error"]').exists()).toBe(true)
    expect(treeCalls()).toBe(1)

    getRequest.mockResolvedValueOnce(ok(NODES))
    await resync()

    expect(treeCalls()).toBe(2)
    expect(w.find('[data-test="project-explorer-error"]').exists()).toBe(false)
    expect(w.findAll('[data-test="project-explorer-group"]').length).toBeGreaterThan(0)
  })

  it('(a) re-reads after a failed refresh on resync and the refresh warning goes away', async () => {
    getRequest.mockResolvedValueOnce(ok(NODES))
    const w = await mountExplorer()
    getRequest.mockRejectedValueOnce(down())
    await w.find('[data-test="project-explorer-refresh"]').trigger('click')
    await flushPromises()
    expect(w.find('[data-test="project-explorer-refresh-error"]').exists()).toBe(true)
    expect(w.text()).toContain('새로고침에 실패했습니다. 이전 목록을 표시합니다.')

    getRequest.mockResolvedValueOnce(ok(NODES))
    await resync()

    expect(treeCalls()).toBe(3)
    expect(w.find('[data-test="project-explorer-refresh-error"]').exists()).toBe(false)
    expect(w.text()).not.toContain('새로고침에 실패했습니다.')
  })

  it('(b) does nothing on resync while the tree is healthy', async () => {
    getRequest.mockResolvedValueOnce(ok(NODES))
    await mountExplorer()
    expect(treeCalls()).toBe(1)

    await resync()
    await resync()

    expect(treeCalls()).toBe(1)
  })

  it('sends one GET when the Designer bumps refreshToken for the same resync', async () => {
    getRequest.mockRejectedValueOnce(down())
    const w = await mountExplorer({ projectId: 'p', refreshToken: 0 })
    getRequest.mockResolvedValue(ok(NODES))

    // DesignerShell's resync handler runs first and bumps the token; then this part's resync arrives.
    void w.setProps({ refreshToken: 1 })
    live('p')[0].resync?.()
    await flushPromises()

    expect(treeCalls()).toBe(2)
    expect(w.find('[data-test="project-explorer-error"]').exists()).toBe(false)
  })

  it('a resync during an older read that then fails reads once more', async () => {
    const first = deferred()
    getRequest.mockReturnValueOnce(first.promise)
    const w = await mountExplorer()
    expect(w.find('[data-test="project-explorer-loading"]').exists()).toBe(true)

    live('p')[0].resync?.()
    live('p')[0].resync?.()
    await flushPromises()
    expect(treeCalls()).toBe(1)

    getRequest.mockResolvedValueOnce(ok(NODES))
    first.reject(down())
    await flushPromises()

    expect(treeCalls()).toBe(2)
    expect(w.find('[data-test="project-explorer-error"]').exists()).toBe(false)
  })

  it('a resync during an older read that then succeeds sends nothing more', async () => {
    const first = deferred()
    getRequest.mockReturnValueOnce(first.promise)
    await mountExplorer()
    live('p')[0].resync?.()
    await flushPromises()

    first.resolve(ok(NODES))
    await flushPromises()

    expect(treeCalls()).toBe(1)
  })

  it('(c) re-subscribes when the Project changes and releases the previous subscription', async () => {
    getRequest.mockResolvedValue(ok(NODES))
    const w = await mountExplorer()
    const before = live('p')[0]

    await w.setProps({ projectId: 'q' })
    await flushPromises()

    expect(before.released).toBe(true)
    expect(live('p')).toHaveLength(0)
    expect(live('q')).toHaveLength(1)
  })

  it('(d) releases the subscription on unmount', async () => {
    getRequest.mockResolvedValueOnce(ok(NODES))
    const w = await mountExplorer()
    const sub = live('p')[0]
    w.unmount()
    wrapper = null
    expect(sub.released).toBe(true)
    expect(live('p')).toHaveLength(0)
  })
})

describe('ProjectExplorer — 다시 연결하는 중… (0060 T0005)', () => {
  it('says reconnecting while the first load GET is re-sent', async () => {
    const first = deferred()
    getRequest.mockReturnValueOnce(first.promise)
    const w = await mountExplorer()
    const loading = () => w.find('[data-test="project-explorer-loading"]').text()
    expect(loading()).toContain('프로젝트 구조를 불러오는 중...')

    notifyGetRetry('/api/v1/projects/p/groups/tree', { include_terminal: 'false' })
    await flushPromises()
    expect(loading()).toContain('다시 연결하는 중…')

    first.resolve(ok(NODES))
    await flushPromises()
    expect(w.find('[data-test="project-explorer-loading"]').exists()).toBe(false)
    expect(w.text()).not.toContain('다시 연결하는 중…')
  })

  it('ignores a retry of another request', async () => {
    const first = deferred()
    getRequest.mockReturnValueOnce(first.promise)
    const w = await mountExplorer()
    notifyGetRetry('/api/v1/projects/other/groups/tree')
    notifyGetRetry('/api/v1/documents', { project_id: 'p' })
    await flushPromises()
    expect(w.find('[data-test="project-explorer-loading"]').text()).not.toContain('다시 연결하는 중…')
    first.resolve(ok(NODES))
    await flushPromises()
  })

  it('says reconnecting over the shown tree while a refresh GET is re-sent, in every locale', async () => {
    getRequest.mockResolvedValueOnce(ok(NODES))
    const w = await mountExplorer()
    const second = deferred()
    getRequest.mockReturnValueOnce(second.promise)
    await w.find('[data-test="project-explorer-refresh"]').trigger('click')
    await flushPromises()
    expect(w.find('[data-test="project-explorer-reconnecting"]').exists()).toBe(false)

    notifyGetRetry('/api/v1/projects/p/groups/tree')
    await flushPromises()
    const line = () => w.find('[data-test="project-explorer-reconnecting"]')
    expect(line().text()).toBe('다시 연결하는 중…')
    i18n.global.locale.value = 'en'
    await flushPromises()
    expect(line().text()).toBe('Reconnecting…')
    i18n.global.locale.value = 'ja'
    await flushPromises()
    expect(line().text()).toBe('再接続中…')

    second.resolve(ok(NODES))
    await flushPromises()
    expect(line().exists()).toBe(false)
  })
})
