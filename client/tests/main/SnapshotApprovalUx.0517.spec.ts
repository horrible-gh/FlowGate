import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { nextTick } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'
import { getRequest, postRequest } from '@shared/api'
import NotificationCenter from '@main/components/NotificationCenter.vue'
import AppHeader from '@main/components/AppHeader.vue'
import DialogShell from '@main/components/dialogs/DialogShell.vue'
import DialogHeader from '@main/components/dialogs/DialogHeader.vue'
import { useProjectStore } from '@main/stores/project'
import { useNotificationsStore } from '@main/stores/notifications'
import { useSnapshotRequestsStore, type SnapshotRequestRow } from '@main/stores/snapshotRequests'
import { resetDialogSystem } from '@main/composables/useDialogStack'

// flowgate.default.0517 T0012 — approved 시안 (MirageGlass deck yoylzrdu v3) 대조 회귀.
// C1~C11 대응. T0026 moved the Pending entry point from its own header icon into the
// notification bell's panel ([승인 대기] section) and put a reason prompt in front of
// [거절], so every case here goes through the real entry point, NotificationCenter.

vi.mock('@shared/api', () => ({
  default: { head: vi.fn(), get: vi.fn(), post: vi.fn(), patch: vi.fn() },
  getRequest: vi.fn(),
  postRequest: vi.fn(),
  patchRequest: vi.fn(),
  serverLogout: vi.fn(),
}))

const { showToast } = vi.hoisted(() => ({ showToast: vi.fn() }))
vi.mock('@main/components/common/useToast', () => ({
  useToast: () => ({ showToast }),
}))

const { currentRoute } = vi.hoisted(() => ({ currentRoute: { value: { path: '/' } } }))
vi.mock('vue-router', () => ({
  useRouter: () => ({ push: vi.fn(), currentRoute }),
  RouterLink: { template: '<a><slot /></a>' },
}))
vi.mock('@main/composables/useDashboardNavigation', () => ({
  useDashboardNavigation: () => ({ openDashboardTarget: vi.fn() }),
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

/** Pending list answers with `rows`; every other GET (feed, AI detail…) answers empty. */
function servePending(rows: SnapshotRequestRow[]) {
  vi.mocked(getRequest).mockImplementation((url: string) =>
    Promise.resolve(
      url === '/api/v1/snapshots/pending'
        ? ({ data: { ok: true, requests: rows } } as any)
        : ({ data: {} } as any),
    ),
  )
}

const mounted: { unmount: () => void }[] = []

function mountCenter() {
  const notifications = useNotificationsStore()
  vi.spyOn(notifications, 'fetchFeed').mockResolvedValue()
  vi.spyOn(notifications, 'markSeen').mockResolvedValue()
  const wrapper = mount(NotificationCenter, { attachTo: document.body, global: { plugins: [i18n] } })
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

function rejectDialogEl(): HTMLElement | null {
  return document.body.querySelector('.fg-dialog-surface.snap-reject-dialog')
}

function actionButton(id: string): HTMLButtonElement | null {
  return document.body.querySelector(`.snap-approval-dialog [data-dialog-action-id="${id}"]`)
}

function rejectAction(id: string): HTMLButtonElement | null {
  return document.body.querySelector(`.snap-reject-dialog [data-dialog-action-id="${id}"]`)
}

async function typeReason(text: string) {
  const box = document.body.querySelector<HTMLTextAreaElement>('[data-test="snap-reject-reason"]')
  expect(box, 'reject reason box is not on screen').not.toBeNull()
  box!.value = text
  box!.dispatchEvent(new Event('input', { bubbles: true }))
  await flushPromises()
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
  currentRoute.value.path = '/'
  useProjectStore().currentProjectId = 'flowgate'
  vi.mocked(getRequest).mockReset()
  vi.mocked(postRequest).mockReset()
  showToast.mockReset()
})

describe('Snapshot approval UX (flowgate.default.0517 T0012)', () => {
  it('C1: the detail dialog shows every requested field', async () => {
    servePending([row({ snapshot_id: 's1' })])
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
    servePending([row({ snapshot_id: 's2' })])
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
    servePending([row({ snapshot_id: 's3' })])
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
    servePending([target])
    vi.mocked(postRequest).mockResolvedValue({ data: { ok: true, request: { ...target, status: 'created' } } } as any)
    const wrapper = mountCenter()
    await flushPromises()
    await openPendingPanel(wrapper)
    await wrapper.find('[data-test="snap-pending-details"]').trigger('click')
    await flushPromises()

    servePending([])
    actionButton('approve')!.click()
    await flushPromises()

    // T0026: the approve call is untouched — same URL, same empty body, no reason prompt.
    expect(postRequest).toHaveBeenCalledWith('/api/v1/snapshots/s4/approve', {})
    expect(rejectDialogEl()).toBeNull()
    expect(dialogEl()).toBeNull()
    expect(useSnapshotRequestsStore().pending.length).toBe(0)
  })

  it('C5: a new request opens the detail dialog exactly once when nothing else blocks', async () => {
    servePending([])
    const wrapper = mountCenter()
    await flushPromises()
    expect(dialogEl()).toBeNull()

    servePending([row({ snapshot_id: 's5' })])
    await useSnapshotRequestsStore().fetchPending('flowgate')
    await flushPromises()
    await nextTick()

    expect(dialogEl()).not.toBeNull()
    expect(dialogEl()?.textContent).toContain('flowgate.default.0517')
    // …with the notification panel itself still closed: auto-open does not need it.
    expect(wrapper.find('.notif-panel').exists()).toBe(false)
  })

  it('C6: an existing blocking dialog defers auto-open; the request stays in Pending', async () => {
    servePending([])
    mountCenter()
    await flushPromises()

    const blocker = mount(
      { components: { DialogShell, DialogHeader }, template: '<DialogShell :open="true" variant="confirm"><template #header><DialogHeader title="other" /></template></DialogShell>' },
      { global: { plugins: [i18n] } },
    )
    mounted.push(blocker)
    await flushPromises()

    servePending([row({ snapshot_id: 's6' })])
    await useSnapshotRequestsStore().fetchPending('flowgate')
    await flushPromises()

    expect(dialogEl()).toBeNull()
    expect(useSnapshotRequestsStore().pending.some((r) => r.snapshot_id === 's6')).toBe(true)
  })

  it('C7: closing via the header X leaves the request requested and still pending', async () => {
    servePending([row({ snapshot_id: 's7' })])
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
    servePending([row({ snapshot_id: 's8', scope: 'whole_source', requested_paths: [] })])
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
    expect(document.body.querySelector('.snap-approval-dialog select')).toBeNull()
  })

  it('C9: a fresh mount restores the pending count/list from the server without popping a dialog', async () => {
    servePending([row({ snapshot_id: 's9a' }), row({ snapshot_id: 's9b' })])
    const wrapper = mountCenter()
    await flushPromises()

    expect(useSnapshotRequestsStore().pendingCount).toBe(2)
    expect(wrapper.find('[data-test="notif-badge"]').text()).toBe('2')
    expect(dialogEl()).toBeNull()
  })

  it('C11: approve/reject success re-reads the durable list and the badge follows it', async () => {
    const target = row({ snapshot_id: 's11' })
    servePending([target])
    const wrapper = mountCenter()
    await flushPromises()
    expect(wrapper.find('[data-test="notif-badge"]').exists()).toBe(true)

    await openPendingPanel(wrapper)
    vi.mocked(postRequest).mockResolvedValue({ data: { ok: true, request: { ...target, status: 'rejected' } } } as any)
    await wrapper.get('[data-test="snap-pending-reject"]').trigger('click')
    await flushPromises()
    await typeReason('scope too wide')
    servePending([])
    rejectAction('reject-confirm')!.click()
    await flushPromises()

    expect(postRequest).toHaveBeenCalledWith('/api/v1/snapshots/s11/reject', { rejection_reason: 'scope too wide' })
    expect(getRequest).toHaveBeenCalledWith('/api/v1/snapshots/pending', { project_id: 'flowgate' })
    expect(useSnapshotRequestsStore().pendingCount).toBe(0)
    expect(wrapper.find('[data-test="notif-badge"]').exists()).toBe(false)
  })

  it('deck parity: "N건 더 있습니다" links back to the Pending list (yoylzrdu v3 ①/③)', async () => {
    servePending([row({ snapshot_id: 's12a' }), row({ snapshot_id: 's12b' })])
    const wrapper = mountCenter()
    await flushPromises()
    await openPendingPanel(wrapper)
    await wrapper.findAll('[data-test="snap-pending-details"]')[0].trigger('click')
    await flushPromises()
    expect(wrapper.find('.notif-panel').exists()).toBe(false)

    const hint = document.body.querySelector('[data-test="snap-more-pending"]')
    expect(hint?.textContent).toContain('1건')

    document.body.querySelector<HTMLButtonElement>('.snap-more-pending-link')!.click()
    await flushPromises()

    expect(dialogEl()).toBeNull()
    expect(wrapper.find('[data-test="snap-pending-section"]').exists()).toBe(true)
    expect(wrapper.findAll('[data-test="snap-pending-item"]').length).toBe(2)
  })

  it('C5/C9/C11 rework: a stale in-flight response from a project switched away from is dropped', async () => {
    const projectStore = useProjectStore()
    let resolveOld: ((v: any) => void) | null = null
    vi.mocked(getRequest).mockImplementation((url: string, params: any) => {
      if (url === '/api/v1/snapshots/pending' && params.project_id === 'flowgate') {
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
    vi.mocked(getRequest).mockImplementation((url: string) => {
      if (url !== '/api/v1/snapshots/pending') return Promise.resolve({ data: {} } as any)
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
    await store.reject('resurrect-me', 'not needed')
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

  it('deck parity: pending section title carries the count and rows show group/time/source', async () => {
    servePending([row({ snapshot_id: 's13' })])
    const wrapper = mountCenter()
    await flushPromises()
    await openPendingPanel(wrapper)

    expect(wrapper.find('.snap-pending-title').text()).toContain('1')
    const meta = wrapper.get('[data-test="snap-pending-item"]').text()
    expect(meta).toContain('flowgate.default.0517')
    expect(meta).toContain('current worktree')
  })
})

describe('Snapshot Pending inside the notification panel (flowgate.default.0517 T0026 §1)', () => {
  it('U1: the header has no Snapshot icon of its own — only Git, a divider, and the bell', async () => {
    servePending([row({ snapshot_id: 'u1' })])
    const wrapper = mount(AppHeader, {
      attachTo: document.body,
      global: {
        plugins: [i18n],
        stubs: { RouterLink: { template: '<a><slot /></a>' }, ProjectSelector: true, GitStatusPanel: true },
      },
    })
    mounted.push(wrapper)
    await flushPromises()

    expect(wrapper.find('.snap-pending-center').exists()).toBe(false)
    expect(wrapper.find('[aria-label="Snapshot Pending"]').exists()).toBe(false)
    const group = Array.from(wrapper.get('.hdr-actions').element.children)
    expect(group.map((el) => el.className)).toEqual([
      expect.stringContaining('git-menu-wrap'),
      'hdr-div',
      expect.stringContaining('notif-center'),
    ])
    // …and the pending count is on the bell instead.
    expect(wrapper.get('.notif-center [data-test="notif-badge"]').text()).toBe('1')
  })

  it('U2: with nothing pending the panel is the same three sections and the bell shows no badge', async () => {
    servePending([])
    const wrapper = mountCenter()
    await flushPromises()
    expect(wrapper.find('[data-test="notif-badge"]').exists()).toBe(false)

    await openPendingPanel(wrapper)
    const tabs = wrapper.findAll('.notif-section-tab')
    expect(tabs.map((tab) => tab.text())).toEqual(['일반', 'AI 0', '질의응답 0'])
    expect(tabs[0].attributes('aria-selected')).toBe('true')
    expect(wrapper.get('.notif-section-tabs').attributes('style')).toContain('repeat(3, 1fr)')
    expect(wrapper.find('[data-test="snap-pending-section"]').exists()).toBe(false)
  })

  it('U3: pending requests add a [승인 대기 N] section the panel opens on, and the bell counts them', async () => {
    servePending([row({ snapshot_id: 'u3a' }), row({ snapshot_id: 'u3b' })])
    const wrapper = mountCenter()
    await flushPromises()

    const badge = wrapper.get('[data-test="notif-badge"]')
    expect(badge.text()).toBe('2')
    expect(badge.attributes('title')).toContain('2건')

    await openPendingPanel(wrapper)
    const tab = wrapper.get('[data-test="notif-section-snapshot"]')
    expect(tab.text()).toBe('승인 대기 2')
    expect(tab.attributes('aria-selected')).toBe('true')
    expect(wrapper.get('.notif-section-tabs').attributes('style')).toContain('repeat(4, 1fr)')
    expect(wrapper.findAll('[data-test="snap-pending-item"]').length).toBe(2)

    // The other sections still work from the same panel.
    await wrapper.findAll('.notif-section-tab')[0].trigger('click')
    expect(wrapper.find('[data-test="snap-pending-section"]').exists()).toBe(false)
    expect(wrapper.find('.notif-section-body--general').exists()).toBe(true)
  })
})

describe('Snapshot [거절] asks for a reason (flowgate.default.0517 T0026 §2)', () => {
  it('R1: [거절] on a Pending row opens the reason prompt and sends nothing until confirmed', async () => {
    servePending([row({ snapshot_id: 'r1' })])
    const wrapper = mountCenter()
    await flushPromises()
    await openPendingPanel(wrapper)
    await wrapper.get('[data-test="snap-pending-reject"]').trigger('click')
    await flushPromises()

    expect(rejectDialogEl()).not.toBeNull()
    expect(rejectDialogEl()!.textContent).toContain('반려사유')
    expect(rejectDialogEl()!.textContent).toContain('build requires a real file tree')
    expect(postRequest).not.toHaveBeenCalled()
    // An empty (or blank) reason cannot be confirmed.
    expect(rejectAction('reject-confirm')!.disabled).toBe(true)
    await typeReason('   ')
    expect(rejectAction('reject-confirm')!.disabled).toBe(true)
    await typeReason('상대경로 하나만 다시 요청하세요')
    expect(rejectAction('reject-confirm')!.disabled).toBe(false)
    // Clicking inside the teleported prompt must not fold the panel underneath.
    rejectDialogEl()!.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    await flushPromises()
    expect(wrapper.find('[data-test="snap-pending-section"]').exists()).toBe(true)
    expect(postRequest).not.toHaveBeenCalled()
  })

  it('R2: [취소] closes the prompt without deciding; the request stays pending', async () => {
    servePending([row({ snapshot_id: 'r2' })])
    const wrapper = mountCenter()
    await flushPromises()
    await openPendingPanel(wrapper)
    await wrapper.get('[data-test="snap-pending-reject"]').trigger('click')
    await flushPromises()

    rejectAction('cancel')!.click()
    await flushPromises()

    expect(rejectDialogEl()).toBeNull()
    expect(postRequest).not.toHaveBeenCalled()
    expect(useSnapshotRequestsStore().pending.map((r) => r.snapshot_id)).toEqual(['r2'])
  })

  it('R3: [거절] in the detail dialog nests the prompt; confirming sends the trimmed reason and closes both', async () => {
    const target = row({ snapshot_id: 'r3' })
    servePending([target])
    vi.mocked(postRequest).mockResolvedValue({
      data: { ok: true, request: { ...target, status: 'rejected', rejection_reason: 'too broad' } },
    } as any)
    const wrapper = mountCenter()
    await flushPromises()
    await openPendingPanel(wrapper)
    await wrapper.find('[data-test="snap-pending-details"]').trigger('click')
    await flushPromises()

    actionButton('reject')!.click()
    await flushPromises()
    expect(dialogEl()).not.toBeNull() // still underneath
    expect(rejectDialogEl()).not.toBeNull()
    expect(postRequest).not.toHaveBeenCalled()

    await typeReason('  too broad  ')
    servePending([])
    rejectAction('reject-confirm')!.click()
    await flushPromises()

    expect(postRequest).toHaveBeenCalledTimes(1)
    expect(postRequest).toHaveBeenCalledWith('/api/v1/snapshots/r3/reject', { rejection_reason: 'too broad' })
    expect(rejectDialogEl()).toBeNull()
    expect(dialogEl()).toBeNull()
    expect(useSnapshotRequestsStore().pendingCount).toBe(0)
  })

  it('R4: a failed reject keeps the prompt and the typed reason on screen', async () => {
    servePending([row({ snapshot_id: 'r4' })])
    vi.mocked(postRequest).mockRejectedValue(new Error('422'))
    const wrapper = mountCenter()
    await flushPromises()
    await openPendingPanel(wrapper)
    await wrapper.get('[data-test="snap-pending-reject"]').trigger('click')
    await flushPromises()
    await typeReason('keep me')
    rejectAction('reject-confirm')!.click()
    await flushPromises()

    expect(showToast).toHaveBeenCalledWith('거절 처리에 실패했습니다. 다시 시도하세요.', 'danger')
    expect(rejectDialogEl()).not.toBeNull()
    expect(document.body.querySelector<HTMLTextAreaElement>('[data-test="snap-reject-reason"]')!.value).toBe('keep me')
    expect(useSnapshotRequestsStore().pending.map((r) => r.snapshot_id)).toEqual(['r4'])
  })
})
