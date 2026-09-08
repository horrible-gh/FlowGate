// flowgate.default.0481 T0010 #1 / #3 — the Git panel's base-checkout section during a merge.
//
// T0010 opened with "AI에게 맡기기 호출해도 [기준 브랜치 AI 정리를 시작하지 못했습니다.]
// 메세지만 계속 뜨고". The cause is ownership: a merge stopped on a conflict leaves every
// unmerged AND every cleanly merged path in the base checkout's `git status`, so this section
// filled up with the merge itself and offered it as stray-edit cleanup — commit it, revert it
// file by file, or hand it to an AI. All three are wrong for a half-finished merge, and the AI
// one could never start (server side: PROJECT_SCOPED_ACTION_SCOPES in ai_invoke/admission.py).
import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import i18n from '@shared/i18n'
import GitStatusPanel from '@main/components/GitStatusPanel.vue'

const { getRequest, postRequest } = vi.hoisted(() => ({ getRequest: vi.fn(), postRequest: vi.fn() }))
vi.mock('@shared/api', () => ({
  default: { head: vi.fn(), get: vi.fn(), post: vi.fn(), patch: vi.fn() }, getRequest, postRequest,
}))
const { showToast } = vi.hoisted(() => ({ showToast: vi.fn() }))
vi.mock('@main/components/common/useToast', () => ({ useToast: () => ({ showToast }) }))

const GROUP = 'flowgate.default.0481'

function status(mergeInProgress: unknown) {
  return {
    enabled: true, base_branch: 'main', base_path_state: 'checkout', ahead_count: 0, behind_count: 0,
    base_dirty: {
      dirty: true,
      files: ['server/modules/flow_gate/services/git_service.py', 'client/src/main/components/GitStatusPanel.vue'],
      merge_in_progress: mergeInProgress,
    },
    base_untracked: { count: 0, files: [], truncated: false },
    slots: [],
    pending: [{ group_id: GROUP, branch: 'feature', status: 'conflict', default_action: 'merge', merge_id: 11 }],
    pending_count: 1, cleanable_count: 0, terminal_cleanup: null,
    unpushed: { count: 0, commit_count: 0, merges: [], measured: true },
  }
}

async function render(value: any) {
  getRequest.mockImplementation((url: string) => {
    if (url.endsWith('/conflicts')) return Promise.resolve({ data: { ok: true, files: [] } })
    if (url.endsWith('/git/finalize')) return Promise.resolve({ data: { state: {} } })
    return Promise.resolve({ data: { ok: true, status: value } })
  })
  const wrapper = mount(GitStatusPanel, {
    props: { projectId: 'flowgate' },
    global: { plugins: [i18n], stubs: { AppIcon: true, GitConflictResolverDialog: true } },
  })
  await flushPromises()
  return wrapper
}

beforeEach(() => {
  setActivePinia(createPinia())
  i18n.global.locale.value = 'ko'
  getRequest.mockReset(); postRequest.mockReset(); showToast.mockReset()
  postRequest.mockResolvedValue({ data: { ok: true, result: {} } })
})

const MERGE = { merge_id: 11, group_id: GROUP }

describe('GitStatusPanel — who owns the base-checkout changes', () => {
  it('offers the resolver, not a cleanup, while a merge holds the base checkout', async () => {
    const wrapper = await render(status(MERGE))
    const summary = wrapper.find('.git-base-dirty-alert .git-v9-summary')
    expect(summary.exists()).toBe(true)

    // the chip says whose changes these are …
    expect(summary.text()).toContain('진행 중인 병합')
    // … the one button is the way out …
    const labels = summary.findAll('button').map((b) => b.text())
    expect(labels.some((l) => l.includes('충돌 해소'))).toBe(true)
    // … and the action that would break the merge is not offered.
    expect(labels.some((l) => l.includes('AI에게 맡기기'))).toBe(false)

    wrapper.unmount()
  })

  it('does not offer commit, and disables per-file revert, for a merge-owned file list', async () => {
    const wrapper = await render(status(MERGE))
    await wrapper.find('.git-v9-link-btn').trigger('click')

    expect(wrapper.find('.git-base-commit-row').exists()).toBe(false)
    const revertButtons = wrapper.findAll('.git-base-dirty-filerow button')
    expect(revertButtons).toHaveLength(2)
    // 0441 TR0005: visible and disabled, with the reason on it — never silently removed.
    expect(revertButtons.every((b) => b.attributes('disabled') !== undefined)).toBe(true)
    expect(revertButtons[0].attributes('title')).toBeTruthy()

    wrapper.unmount()
  })

  it('leaves the ordinary stray-edit cleanup exactly as it was', async () => {
    const wrapper = await render(status(null))
    const summary = wrapper.find('.git-base-dirty-alert .git-v9-summary')
    expect(summary.text()).toContain('기준 브랜치 미커밋 변경')
    expect(summary.findAll('button').map((b) => b.text()).some((l) => l.includes('AI에게 맡기기'))).toBe(true)

    await wrapper.find('.git-v9-link-btn').trigger('click')
    expect(wrapper.find('.git-base-commit-row').exists()).toBe(true)
    const revertButtons = wrapper.findAll('.git-base-dirty-filerow button')
    expect(revertButtons.every((b) => b.attributes('disabled') === undefined)).toBe(true)

    wrapper.unmount()
  })

  it('makes the conflict row resolve button a filled danger control', async () => {
    // T0010 #3 — "잘 보이지도 않는 충돌해소 버튼". It was `btn-danger-ol` (white fill, pale
    // border) sitting next to a solid [AI에게 맡기기] inside a red card.
    const wrapper = await render(status(null))
    const resolve = wrapper
      .findAll('.git-status-row-main button')
      .find((b) => b.text().includes('충돌 해소'))
    expect(resolve).toBeTruthy()
    expect(resolve!.classes()).toContain('btn-danger')
    expect(resolve!.classes()).not.toContain('btn-danger-ol')

    wrapper.unmount()
  })
})

describe('GitStatusPanel — a refused base-dirty delegation says what happened', () => {
  async function pressDelegate(error: Record<string, unknown>) {
    const wrapper = await render(status(null))
    postRequest.mockRejectedValueOnce({ response: { data: error } })
    const button = wrapper
      .find('.git-base-dirty-alert .git-v9-summary')
      .findAll('button')
      .find((b) => b.text().includes('AI에게 맡기기'))!
    await button.trigger('click')
    await flushPromises()
    wrapper.unmount()
    return showToast.mock.calls.map((call) => String(call[0]))
  }

  it('names the merge that owns the files', async () => {
    const messages = await pressDelegate({ code: 'base_dirty_merge_in_progress', message: 'mid-merge' })
    expect(messages.some((m) => m.includes('진행 중인 병합'))).toBe(true)
    expect(messages.some((m) => m.includes('시작하지 못했습니다'))).toBe(false)
  })

  it('says a run is already going instead of reporting a failure', async () => {
    const messages = await pressDelegate({ code: 'base_dirty_run_in_progress' })
    expect(messages.some((m) => m.includes('진행 중입니다'))).toBe(true)
    expect(messages.some((m) => m.includes('시작하지 못했습니다'))).toBe(false)
  })

  it('relays the server message for a refusal it has no phrase for', async () => {
    // The generic sentence is what the operator stared at; anything unforeseen now shows the
    // server's own explanation instead of hiding it.
    const messages = await pressDelegate({
      code: 'worktree_unavailable',
      message: '그룹 작업 폴더를 쓸 수 없습니다 (원인: merge_conflict_open)',
    })
    expect(messages.some((m) => m.includes('merge_conflict_open'))).toBe(true)
  })
})
