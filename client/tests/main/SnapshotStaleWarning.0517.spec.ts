import { mount, flushPromises } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'
import { getRequest } from '@shared/api'
import GroupInfoModal from '@main/components/GroupInfoModal.vue'
import { resetDialogSystem } from '@main/composables/useDialogStack'

// flowgate.default.0517 T0012 §13 / C10 — "ACTIVE SNAPSHOT IS STALE" 경고.

vi.mock('@shared/api', () => ({
  default: { get: vi.fn(), post: vi.fn() },
  getRequest: vi.fn(),
  postRequest: vi.fn(),
}))

let wrapper: ReturnType<typeof mount> | null = null

afterEach(() => {
  wrapper?.unmount()
  wrapper = null
  resetDialogSystem()
  document.body.innerHTML = ''
})

beforeEach(() => {
  i18n.global.locale.value = 'ko'
  vi.mocked(getRequest).mockReset()
})

function mountModal() {
  wrapper = mount(GroupInfoModal, {
    props: {
      visible: false,
      projectId: 'flowgate',
      groupId: 'flowgate.default.0517',
      groupName: 'Test Group',
      documents: [],
    },
    global: { plugins: [i18n] },
  })
  return wrapper
}

describe('GroupInfoModal stale snapshot warning (flowgate.default.0517 T0012 C10)', () => {
  it('shows the ACTIVE SNAPSHOT IS STALE banner when the group has a created+stale snapshot', async () => {
    vi.mocked(getRequest).mockResolvedValue({
      data: { ok: true, requests: [{ snapshot_id: 's1', status: 'created', stale: true }] },
    } as any)
    const w = mountModal()
    await w.setProps({ visible: true })
    await flushPromises()

    expect(getRequest).toHaveBeenCalledWith(
      '/api/v1/snapshots/active',
      { project_id: 'flowgate', group_id: 'flowgate.default.0517' },
    )
    const banner = document.body.querySelector('[data-test="snap-stale-warning"]')
    expect(banner).not.toBeNull()
    expect(banner?.textContent).toContain('ACTIVE SNAPSHOT IS STALE')
  })

  it('stays silent when the active snapshot is fresh', async () => {
    vi.mocked(getRequest).mockResolvedValue({
      data: { ok: true, requests: [{ snapshot_id: 's2', status: 'created', stale: false }] },
    } as any)
    const w = mountModal()
    await w.setProps({ visible: true })
    await flushPromises()

    expect(document.body.querySelector('[data-test="snap-stale-warning"]')).toBeNull()
  })

  it('stays silent when there is no active snapshot for the group', async () => {
    vi.mocked(getRequest).mockResolvedValue({ data: { ok: true, requests: [] } } as any)
    const w = mountModal()
    await w.setProps({ visible: true })
    await flushPromises()

    expect(document.body.querySelector('[data-test="snap-stale-warning"]')).toBeNull()
  })

  // Rejection round 2: a slow /active response for a group the user has since closed and
  // moved away from must not land on a different group's screen.
  it('drops a late response from a previously-open group after closing and reopening for a different group', async () => {
    let resolveGroupA: ((value: unknown) => void) | null = null
    const deferredGroupA = new Promise((resolve) => { resolveGroupA = resolve })
    vi.mocked(getRequest).mockImplementation((_url: string, params?: Record<string, unknown>) => {
      if (params?.group_id === 'flowgate.default.0900') {
        return Promise.resolve({
          data: { ok: true, requests: [{ snapshot_id: 's3', status: 'created', stale: false }] },
        }) as any
      }
      return deferredGroupA as any
    })

    const w = mountModal()
    await w.setProps({ visible: true }) // opens on group A (flowgate.default.0517); fetch stays pending
    await flushPromises()

    // close group A, then reopen on group B — B's fetch resolves (fresh) before A's late response
    await w.setProps({ visible: false })
    await w.setProps({ visible: true, groupId: 'flowgate.default.0900' })
    await flushPromises()

    expect(document.body.querySelector('[data-test="snap-stale-warning"]')).toBeNull()

    // A's stale response finally arrives out of order — must not resurrect the warning on B's screen
    resolveGroupA!({
      data: { ok: true, requests: [{ snapshot_id: 's1', status: 'created', stale: true }] },
    })
    await flushPromises()

    expect(document.body.querySelector('[data-test="snap-stale-warning"]')).toBeNull()
  })

  // Rejection round 2: a stale response arriving after the modal has been closed must not
  // flip the warning back on for the next time it opens.
  it('does not resurrect the warning when a stale response arrives after the modal has closed', async () => {
    let resolve: ((value: unknown) => void) | null = null
    const deferred = new Promise((r) => { resolve = r })
    vi.mocked(getRequest).mockReturnValue(deferred as any)

    const w = mountModal()
    await w.setProps({ visible: true })
    await flushPromises()
    expect(document.body.querySelector('[data-test="snap-stale-warning"]')).toBeNull()

    await w.setProps({ visible: false })
    resolve!({
      data: { ok: true, requests: [{ snapshot_id: 's1', status: 'created', stale: true }] },
    })
    await flushPromises()

    expect(document.body.querySelector('[data-test="snap-stale-warning"]')).toBeNull()
  })
})
