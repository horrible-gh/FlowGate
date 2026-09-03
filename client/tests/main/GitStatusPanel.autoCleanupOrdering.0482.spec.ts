import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import i18n from '@shared/i18n'
import GitStatusPanel from '@main/components/GitStatusPanel.vue'

// flowgate.default.0482 T0011 완료 기준 3: 자동 cleanup 호출 순서와 경쟁 조건을
// 실제 요청 횟수/순서로 단언한다. mockupV9 spec은 정적 데이터 한 번의 mount만
// 다루므로 project A→B 전환, 늦은 A 응답, enabled=false/조회 실패의 0회 호출,
// git_busy의 스냅샷 보존은 이 파일이 새로 채운다 (반려 rej_01M1HZBVZ61GG8H9).

const { getRequest, postRequest, showToast } = vi.hoisted(() => ({
  getRequest: vi.fn(), postRequest: vi.fn(), showToast: vi.fn(),
}))
vi.mock('@shared/api', () => ({
  default: { head: vi.fn(), get: vi.fn(), post: vi.fn(), patch: vi.fn() }, getRequest, postRequest,
}))
vi.mock('@main/components/common/useToast', () => ({ useToast: () => ({ showToast }) }))

function baseStatus(overrides: Partial<Record<string, any>> = {}) {
  return {
    enabled: true, base_branch: 'main', base_path_state: 'checkout', ahead_count: 0, behind_count: 0,
    base_dirty: { dirty: false, files: [] },
    base_untracked: { count: 0, files: [], truncated: false },
    slots: [],
    pending: [],
    pending_count: 0, cleanable_count: 1,
    terminal_cleanup: { last_run_at: null, last_run_status: null, last_cleaned_count: 0, pending: [] },
    unpushed: { count: 0, commit_count: 0, merges: [], measured: true },
    ...overrides,
  }
}

function deferred<T>() {
  let resolve!: (v: T) => void
  let reject!: (e: any) => void
  const promise = new Promise<T>((res, rej) => { resolve = res; reject = rej })
  return { promise, resolve, reject }
}

function mountPanel(projectId: string) {
  return mount(GitStatusPanel, {
    props: { projectId },
    global: { plugins: [i18n], stubs: { AppIcon: true, GitConflictResolverDialog: true } },
  })
}

beforeEach(() => {
  setActivePinia(createPinia()); i18n.global.locale.value = 'ko'
  getRequest.mockReset(); postRequest.mockReset()
  postRequest.mockResolvedValue({ data: { ok: true, result: {} } })
  showToast.mockReset()
})
afterEach(() => {
  vi.restoreAllMocks()
})

describe('GitStatusPanel automatic cleanup request ordering (0482 T0011 완료기준 3)', () => {
  it('does not call cleanup when status.enabled is false', async () => {
    getRequest.mockResolvedValue({ data: { ok: true, status: baseStatus({ enabled: false }) } })
    const wrapper = mountPanel('flowgate')
    await flushPromises()
    expect(wrapper.exists()).toBe(true)
    expect(postRequest.mock.calls.filter(([url]) => String(url).includes('/git/cleanup'))).toHaveLength(0)
  })

  it('does not call cleanup when the status fetch itself fails', async () => {
    getRequest.mockRejectedValue(new Error('403'))
    mountPanel('flowgate')
    await flushPromises()
    expect(postRequest.mock.calls.filter(([url]) => String(url).includes('/git/cleanup'))).toHaveLength(0)
  })

  it('runs cleanup exactly once per project on mount and again exactly once after a manual re-fetch of the same project', async () => {
    getRequest.mockResolvedValue({ data: { ok: true, status: baseStatus() } })
    const wrapper = mountPanel('flowgate')
    await flushPromises()
    expect(postRequest.mock.calls.filter(([url]) => String(url).includes('/git/cleanup'))).toHaveLength(1)

    // A second status refresh for the SAME project must not re-trigger the
    // once-per-component-lifetime cleanup attempt (T0011 item 3: "정확히 한 번만 시도").
    await (wrapper.vm as any).fetchStatus()
    await flushPromises()
    expect(postRequest.mock.calls.filter(([url]) => String(url).includes('/git/cleanup'))).toHaveLength(1)
  })

  it('switches project A to B and ignores a late A status response without a second cleanup call', async () => {
    const aStatusDeferred = deferred<{ data: any }>()
    const bStatus = baseStatus({ terminal_cleanup: { last_run_at: null, last_run_status: null, last_cleaned_count: 0, pending: [] } })

    getRequest.mockImplementation((url: string) => {
      if (url.includes('/projects/a/')) return aStatusDeferred.promise
      if (url.includes('/projects/b/')) return Promise.resolve({ data: { ok: true, status: bStatus } })
      return Promise.resolve({ data: { ok: true, status: baseStatus() } })
    })

    const wrapper = mountPanel('a')
    // The mount-time fetch for 'a' is now in flight and unresolved.
    await wrapper.setProps({ projectId: 'b' })
    await flushPromises()

    // B resolved synchronously and must have driven exactly one cleanup call.
    expect(postRequest.mock.calls.filter(([url]) => String(url).includes('/git/cleanup'))).toHaveLength(1)
    expect(postRequest.mock.calls.filter(([url]) => String(url).includes('/projects/b/git/cleanup'))).toHaveLength(1)

    // A's late response now arrives. The sequence/projectId guard in fetchStatus
    // must discard it: no cleanup call for 'a', and B's already-rendered status
    // must not be clobbered by A's payload.
    aStatusDeferred.resolve({ data: { ok: true, status: baseStatus({ base_dirty: { dirty: true, files: ['late-a.ts'] } }) } })
    await flushPromises()

    expect(postRequest.mock.calls.filter(([url]) => String(url).includes('/git/cleanup'))).toHaveLength(1)
    expect(postRequest.mock.calls.some(([url]) => String(url).includes('/projects/a/git/cleanup'))).toBe(false)
    expect(wrapper.text()).not.toContain('late-a.ts')
  })

  it('switches project A to B and ignores a late REJECTED A status response without clobbering B', async () => {
    const aStatusDeferred = deferred<{ data: any }>()
    const bStatus = baseStatus({ terminal_cleanup: { last_run_at: null, last_run_status: null, last_cleaned_count: 0, pending: [] } })

    getRequest.mockImplementation((url: string) => {
      if (url.includes('/projects/a/')) return aStatusDeferred.promise
      if (url.includes('/projects/b/')) return Promise.resolve({ data: { ok: true, status: bStatus } })
      return Promise.resolve({ data: { ok: true, status: baseStatus() } })
    })

    const wrapper = mountPanel('a')
    // The mount-time fetch for 'a' is now in flight and unresolved.
    await wrapper.setProps({ projectId: 'b' })
    await flushPromises()

    // B resolved synchronously and must have driven exactly one cleanup call.
    expect(postRequest.mock.calls.filter(([url]) => String(url).includes('/git/cleanup'))).toHaveLength(1)

    // A's late response now REJECTS (403/404-style failure). The sequence/projectId
    // guard in fetchStatus's catch branch must discard it too: B's already-rendered
    // status must survive, not be erased by A's stale failure.
    aStatusDeferred.reject(new Error('403'))
    await flushPromises()

    expect(postRequest.mock.calls.filter(([url]) => String(url).includes('/git/cleanup'))).toHaveLength(1)
    expect(postRequest.mock.calls.some(([url]) => String(url).includes('/projects/a/git/cleanup'))).toBe(false)
    // The panel must still be showing B's status, not hidden by A's late failure
    // setting status.value = null.
    expect((wrapper.vm as any).status).not.toBeNull()
    expect((wrapper.vm as any).status?.pending_count).toBe(bStatus.pending_count)
  })

  it('shows a busy toast and keeps the prior snapshot when the automatic cleanup hits git_busy', async () => {
    const initialStatus = baseStatus({
      terminal_cleanup: { last_run_at: '2026-08-31T12:00:00+09:00', last_run_status: 'ok', last_cleaned_count: 3, pending: [] },
    })
    getRequest.mockResolvedValue({ data: { ok: true, status: initialStatus } })
    postRequest.mockImplementation(async (url: string) => {
      if (String(url).includes('/git/cleanup')) {
        const err: any = new Error('busy')
        err.response = { data: { error: { code: 'git_busy' } } }
        throw err
      }
      return { data: { ok: true, result: {} } }
    })

    const wrapper = mountPanel('flowgate')
    await flushPromises()

    expect(showToast).toHaveBeenCalledWith(
      i18n.global.t('main.git_status.cleanup_git_busy'), 'warning',
    )
    // git_busy must not trigger a follow-up status re-fetch that could overwrite the
    // snapshot already on screen (T0011 item 3: "스냅샷을 덮지 않고 건너뜀 상태로 표시").
    expect(getRequest.mock.calls.filter(([url]) => String(url).includes('/git/status'))).toHaveLength(1)
    // The prior snapshot (last_run_status='ok', count=3) is still what's rendered —
    // it was never replaced by "never run" or discarded because of the busy cleanup.
    expect(wrapper.find('.git-cleanup-never').exists()).toBe(true)
    expect(wrapper.find('.git-cleanup-never').text()).not.toBe(i18n.global.t('main.git_status.cleanup_never_run'))
    expect(wrapper.find('.git-cleanup-never').text()).toContain(String(3))
  })
})
