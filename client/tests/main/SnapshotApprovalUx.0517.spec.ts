import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { nextTick } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'
import { getRequest, postRequest } from '@shared/api'
import SnapshotPendingCenter from '@main/components/SnapshotPendingCenter.vue'
import DialogShell from '@main/components/dialogs/DialogShell.vue'
import DialogHeader from '@main/components/dialogs/DialogHeader.vue'
import { useProjectStore } from '@main/stores/project'
import { useSnapshotRequestsStore, type SnapshotRequestRow } from '@main/stores/snapshotRequests'
import { resetDialogSystem } from '@main/composables/useDialogStack'

// flowgate.default.0517 T0012 — approved 시안 (MirageGlass deck yoylzrdu v3) 대조 회귀.
// C1~C11 대응.

vi.mock('@shared/api', () => ({
  default: { get: vi.fn(), post: vi.fn() },
  getRequest: vi.fn(),
  postRequest: vi.fn(),
}))

function row(over: Partial<SnapshotRequestRow> & { snapshot_id: string }): SnapshotRequestRow {
  return {
    project_id: 'flowgate',
    group_id: 'flowgate.default.0517',
    run_id: 'run_1',
    token_id: 'tok_1',
    provider_id: 'aip_claude',
    reason: 'build requires a real file tree',
    scope: 'directory',
    requested_paths: ['server/modules/flow_gate'],
    purpose: 'run pytest against the copy',
    source_kind: 'current_worktree',
    status: 'requested',
    requested_at: '2026-09-23T09:00:00+09:00',
    ...over,
  }
}

const mounted: { unmount: () => void }[] = []

function mountCenter() {
  const wrapper = mount(SnapshotPendingCenter, { global: { plugins: [i18n] } })
  mounted.push(wrapper)
  return wrapper
}

async function openPendingPanel(wrapper: ReturnType<typeof mount>) {
  await wrapper.find('.notif-bell').trigger('click')
  await flushPromises()
}

function dialogEl(): HTMLElement | null {
  return document.body.querySelector('.fg-dialog-surface.snap-approval-dialog')
}

function actionButton(id: string): HTMLButtonElement | null {
  return document.body.querySelector(`.snap-approval-dialog [data-dialog-action-id="${id}"]`)
}

afterEach(() => {
  while (mounted.length > 0) {
    try { mounted.pop()!.unmount() } catch { /* already unmounted */ }
  }
  resetDialogSystem()
  document.body.innerHTML = ''
})

beforeEach(() => {
  setActivePinia(createPinia())
  i18n.global.locale.value = 'ko'
  useProjectStore().currentProjectId = 'flowgate'
  vi.mocked(getRequest).mockReset()
  vi.mocked(postRequest).mockReset()
})

describe('Snapshot approval UX (flowgate.default.0517 T0012)', () => {
  it('C1: the detail dialog shows every requested field', async () => {
    vi.mocked(getRequest).mockResolvedValue({ data: { ok: true, requests: [row({ snapshot_id: 's1' })] } } as any)
    const wrapper = mountCenter()
    await flushPromises()
    await openPendingPanel(wrapper)
    await wrapper.find('[data-test="snap-pending-details"]').trigger('click')
    await flushPromises()

    const text = dialogEl()?.textContent ?? ''
    expect(text).toContain('aip_claude') // provider (fallback to id — no provider list loaded)
    expect(text).toContain('flowgate.default.0517') // group
    expect(text).toContain('current worktree') // source
    expect(text).toContain('server/modules/flow_gate') // requested path
    expect(text).toContain('build requires a real file tree') // reason
    expect(text).toContain('run pytest against the copy') // purpose
  })

  it('C2: approve carries no default — no autofocus, no auto-run, Enter does nothing unintended', async () => {
    vi.mocked(getRequest).mockResolvedValue({ data: { ok: true, requests: [row({ snapshot_id: 's2' })] } } as any)
    const wrapper = mountCenter()
    await flushPromises()
    await openPendingPanel(wrapper)
    await wrapper.find('[data-test="snap-pending-details"]').trigger('click')
    await flushPromises()
    await nextTick()

    const approveBtn = actionButton('approve')
    expect(approveBtn).not.toBeNull()
    expect(document.activeElement).not.toBe(approveBtn)
    expect(postRequest).not.toHaveBeenCalled()

    // Enter on whatever DOES hold focus must not submit approval.
    document.activeElement?.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }))
    await flushPromises()
    expect(postRequest).not.toHaveBeenCalled()
  })

  it('C3: a Pending row offers exactly [거절]/[자세히] — no approve action', async () => {
    vi.mocked(getRequest).mockResolvedValue({ data: { ok: true, requests: [row({ snapshot_id: 's3' })] } } as any)
    const wrapper = mountCenter()
    await flushPromises()
    await openPendingPanel(wrapper)

    const item = wrapper.get('[data-test="snap-pending-item"]')
    const buttons = item.findAll('button')
    expect(buttons.length).toBe(2)
    const labels = buttons.map((b) => b.text())
    expect(labels).toContain('거절')
    expect(labels).toContain('자세히')
    expect(labels.some((l) => l.includes('승인'))).toBe(false)
  })

  it('C4: approval is reachable only through [자세히] → detail dialog → [승인]', async () => {
    const target = row({ snapshot_id: 's4' })
    vi.mocked(getRequest).mockResolvedValue({ data: { ok: true, requests: [target] } } as any)
    vi.mocked(postRequest).mockResolvedValue({ data: { ok: true, request: { ...target, status: 'created' } } } as any)
    const wrapper = mountCenter()
    await flushPromises()
    await openPendingPanel(wrapper)
    await wrapper.find('[data-test="snap-pending-details"]').trigger('click')
    await flushPromises()

    vi.mocked(getRequest).mockResolvedValue({ data: { ok: true, requests: [] } } as any)
    actionButton('approve')!.click()
    await flushPromises()

    expect(postRequest).toHaveBeenCalledWith('/api/v1/snapshots/s4/approve', {})
    expect(dialogEl()).toBeNull()
    expect(useSnapshotRequestsStore().pending.length).toBe(0)
  })

  it('C5: a new request opens the detail dialog exactly once when nothing else blocks', async () => {
    vi.mocked(getRequest).mockResolvedValue({ data: { ok: true, requests: [] } } as any)
    const wrapper = mountCenter()
    await flushPromises()
    expect(dialogEl()).toBeNull()

    vi.mocked(getRequest).mockResolvedValue({ data: { ok: true, requests: [row({ snapshot_id: 's5' })] } } as any)
    await useSnapshotRequestsStore().fetchPending('flowgate')
    await flushPromises()
    await nextTick()

    expect(dialogEl()).not.toBeNull()
    expect(dialogEl()?.textContent).toContain('flowgate.default.0517')
    expect(wrapper.exists()).toBe(true)
  })

  it('C6: an existing blocking dialog defers auto-open; the request stays in Pending', async () => {
    vi.mocked(getRequest).mockResolvedValue({ data: { ok: true, requests: [] } } as any)
    mountCenter()
    await flushPromises()

    const blocker = mount(
      { components: { DialogShell, DialogHeader }, template: '<DialogShell :open="true" variant="confirm"><template #header><DialogHeader title="other" /></template></DialogShell>' },
      { global: { plugins: [i18n] } },
    )
    mounted.push(blocker)
    await flushPromises()

    vi.mocked(getRequest).mockResolvedValue({ data: { ok: true, requests: [row({ snapshot_id: 's6' })] } } as any)
    await useSnapshotRequestsStore().fetchPending('flowgate')
    await flushPromises()

    expect(dialogEl()).toBeNull()
    expect(useSnapshotRequestsStore().pending.some((r) => r.snapshot_id === 's6')).toBe(true)
  })

  it('C7: closing via the header X leaves the request requested and still pending', async () => {
    const target = row({ snapshot_id: 's7' })
    vi.mocked(getRequest).mockResolvedValue({ data: { ok: true, requests: [target] } } as any)
    const wrapper = mountCenter()
    await flushPromises()
    await openPendingPanel(wrapper)
    await wrapper.find('[data-test="snap-pending-details"]').trigger('click')
    await flushPromises()
    expect(dialogEl()).not.toBeNull()

    document.body.querySelector<HTMLButtonElement>('.snap-approval-dialog .fg-dialog-header__close')!.click()
    await flushPromises()

    expect(dialogEl()).toBeNull()
    expect(postRequest).not.toHaveBeenCalled()
    expect(useSnapshotRequestsStore().pending.some((r) => r.snapshot_id === 's7')).toBe(true)
  })

  it('C8: whole_source shows the yellow warning, the required-reason hint, and no source selector', async () => {
    const target = row({ snapshot_id: 's8', scope: 'whole_source', requested_paths: [] })
    vi.mocked(getRequest).mockResolvedValue({ data: { ok: true, requests: [target] } } as any)
    const wrapper = mountCenter()
    await flushPromises()
    await openPendingPanel(wrapper)

    const item = wrapper.get('[data-test="snap-pending-item"]')
    expect(item.classes()).toContain('snap-pending-item--whole')

    await item.find('[data-test="snap-pending-details"]').trigger('click')
    await flushPromises()

    const warning = document.body.querySelector('[data-test="snap-whole-source-warning"]')
    expect(warning).not.toBeNull()
    const text = dialogEl()?.textContent ?? ''
    expect(text).toContain('사유 필수')
    expect(text).not.toMatch(/\bmain\b/)
    expect(document.body.querySelector('select')).toBeNull()
  })

  it('C9: a fresh mount restores the pending count/list from the server without popping a dialog', async () => {
    vi.mocked(getRequest).mockResolvedValue({
      data: { ok: true, requests: [row({ snapshot_id: 's9a' }), row({ snapshot_id: 's9b' })] },
    } as any)
    const wrapper = mountCenter()
    await flushPromises()

    expect(useSnapshotRequestsStore().pendingCount).toBe(2)
    expect(wrapper.find('[data-test="snap-pending-badge"]').text()).toBe('2')
    expect(dialogEl()).toBeNull()
  })

  it('C11: approve/reject success re-reads the durable list and the badge follows it', async () => {
    const target = row({ snapshot_id: 's11' })
    vi.mocked(getRequest).mockResolvedValue({ data: { ok: true, requests: [target] } } as any)
    const wrapper = mountCenter()
    await flushPromises()
    expect(wrapper.find('[data-test="snap-pending-badge"]').exists()).toBe(true)

    await openPendingPanel(wrapper)
    vi.mocked(postRequest).mockResolvedValue({ data: { ok: true, request: { ...target, status: 'rejected' } } } as any)
    vi.mocked(getRequest).mockResolvedValue({ data: { ok: true, requests: [] } } as any)
    await wrapper.get('[data-test="snap-pending-reject"]').trigger('click')
    await flushPromises()

    expect(postRequest).toHaveBeenCalledWith('/api/v1/snapshots/s11/reject', {})
    expect(getRequest).toHaveBeenCalledWith('/api/v1/snapshots/pending', { project_id: 'flowgate' })
    expect(useSnapshotRequestsStore().pendingCount).toBe(0)
    expect(wrapper.find('[data-test="snap-pending-badge"]').exists()).toBe(false)
  })

  it('deck parity: "N건 더 있습니다" links back to the Pending list (yoylzrdu v3 ①/③)', async () => {
    vi.mocked(getRequest).mockResolvedValue({
      data: { ok: true, requests: [row({ snapshot_id: 's12a' }), row({ snapshot_id: 's12b' })] },
    } as any)
    const wrapper = mountCenter()
    await flushPromises()
    await openPendingPanel(wrapper)
    await wrapper.findAll('[data-test="snap-pending-details"]')[0].trigger('click')
    await flushPromises()

    const hint = document.body.querySelector('[data-test="snap-more-pending"]')
    expect(hint?.textContent).toContain('1건')

    document.body.querySelector<HTMLButtonElement>('.snap-more-pending-link')!.click()
    await flushPromises()

    expect(dialogEl()).toBeNull()
    expect(wrapper.find('.snap-pending-panel').exists()).toBe(true)
    expect(wrapper.findAll('[data-test="snap-pending-item"]').length).toBe(2)
  })

  it('C5/C9/C11 rework: a stale in-flight response from a project switched away from is dropped', async () => {
    const projectStore = useProjectStore()
    let resolveOld: ((v: any) => void) | null = null
    vi.mocked(getRequest).mockImplementation((_url: string, params: any) => {
      if (params.project_id === 'flowgate') {
        return new Promise((resolve) => { resolveOld = resolve })
      }
      return Promise.resolve({ data: { ok: true, requests: [] } } as any)
    })
    const wrapper = mountCenter()
    await flushPromises() // mount's fetchPending('flowgate') is now in flight, unresolved

    // Switch projects before the old project's request settles.
    projectStore.currentProjectId = 'other-project'
    await flushPromises()

    // The old ('flowgate') response finally arrives — after the switch.
    resolveOld!({ data: { ok: true, requests: [row({ snapshot_id: 'stale-old', project_id: 'flowgate' })] } })
    await flushPromises()

    expect(useSnapshotRequestsStore().pending.some((r) => r.snapshot_id === 'stale-old')).toBe(false)
    expect(useSnapshotRequestsStore().pendingCount).toBe(0)
    expect(wrapper.exists()).toBe(true)
  })

  it('C5/C9/C11 rework: an out-of-order completion within the same project cannot resurrect a decided row', async () => {
    const target = row({ snapshot_id: 'resurrect-me' })
    let resolveSlow: ((v: any) => void) | null = null
    let callCount = 0
    vi.mocked(getRequest).mockImplementation(() => {
      callCount += 1
      // Call #1: mount's initial fetch — resolves immediately with the row present.
      if (callCount === 1) return Promise.resolve({ data: { ok: true, requests: [target] } } as any)
      // Call #2: an SSE-triggered refresh, issued BEFORE the reject but slow to resolve.
      // Applying its (older-issued) response after a newer one would resurrect the row.
      if (callCount === 2) return new Promise((resolve) => { resolveSlow = resolve })
      // Call #3: the reject's own re-fetch, issued after #2 but resolves faster.
      return Promise.resolve({ data: { ok: true, requests: [] } } as any)
    })
    const wrapper = mountCenter()
    await flushPromises()
    expect(useSnapshotRequestsStore().pendingCount).toBe(1)

    const store = useSnapshotRequestsStore()
    // The SSE-triggered refresh starts (call #2) but is slow to resolve.
    const slowRefresh = store.fetchPending('flowgate')
    // Before it resolves, the row is rejected — its own re-fetch (call #3) resolves first.
    vi.mocked(postRequest).mockResolvedValue({ data: { ok: true, request: { ...target, status: 'rejected' } } } as any)
    await store.reject('resurrect-me')
    await flushPromises()
    expect(store.pendingCount).toBe(0)

    // Now the slow, older-issued SSE refresh (call #2) finally resolves with the stale row.
    resolveSlow!({ data: { ok: true, requests: [target] } })
    await slowRefresh
    await flushPromises()

    expect(store.pending.some((r) => r.snapshot_id === 'resurrect-me')).toBe(false)
    expect(store.pendingCount).toBe(0)
    expect(wrapper.exists()).toBe(true)
  })

  it('deck parity: pending panel title carries the count and rows show group/time/source', async () => {
    vi.mocked(getRequest).mockResolvedValue({ data: { ok: true, requests: [row({ snapshot_id: 's13' })] } } as any)
    const wrapper = mountCenter()
    await flushPromises()
    await openPendingPanel(wrapper)

    expect(wrapper.find('.notif-panel-title').text()).toContain('1')
    const meta = wrapper.get('[data-test="snap-pending-item"]').text()
    expect(meta).toContain('flowgate.default.0517')
    expect(meta).toContain('current worktree')
  })
})
