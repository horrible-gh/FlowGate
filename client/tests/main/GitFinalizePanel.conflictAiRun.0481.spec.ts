// flowgate.default.0481 T0010 rev3 — the resolver dialog now survives its own AI run
// (MainPanel no longer tears the panel down for a `resolve_conflict` scope), so the
// panel has to do the two jobs the removed cover used to do by accident: say the run
// is happening, and show its result when it ends.
import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import i18n from '@shared/i18n'
import GitFinalizePanel from '@main/components/GitFinalizePanel.vue'
import GitConflictResolverDialog from '@main/components/GitConflictResolverDialog.vue'
import GitMergeReviewDialog from '@main/components/GitMergeReviewDialog.vue'
import { useProjectStore } from '@main/stores/project'
import { useAiInvokeRunsStore } from '@main/stores/aiInvokeRuns'
import { parseConflictFile, type ConflictFileState } from '@main/composables/useConflictChunks'

const { getRequest, postRequest, showToast } = vi.hoisted(() => ({
  getRequest: vi.fn(), postRequest: vi.fn(), showToast: vi.fn(),
}))
vi.mock('@shared/api', () => ({
  default: { head: vi.fn(), get: vi.fn(), post: vi.fn(), patch: vi.fn() },
  getRequest, postRequest,
}))
vi.mock('@main/components/common/useToast', () => ({
  useToast: () => ({ showToast }),
}))

const CONFLICT = ['x', '<<<<<<< HEAD', 'ours', '=======', 'theirs', '>>>>>>> main', 'y'].join('\n')

function makeFile(path: string): ConflictFileState {
  const segments = parseConflictFile(CONFLICT)
  if (!segments) throw new Error('fixture must parse')
  return { path, conflict_count: 1, directText: CONFLICT, mode: 'chunk', segments, notice: '' }
}

const GROUP_ID = 'test2.default.0009'
const state = {
  group_id: GROUP_ID, branch: 'test2_default_0009', base_branch: 'main',
  status: 'conflict', choices: [], ahead_count: 1, behind_count: 0, merge_id: 4,
  review_state: null as string | null,
}

function mountPanel() {
  useProjectStore().setCurrentProject('test2')
  return mount(GitFinalizePanel, {
    props: { groupId: GROUP_ID },
    global: {
      plugins: [i18n],
      stubs: { AppIcon: true, GitMergeReviewDialog: true, teleport: true },
    },
  })
}

function startConflictRun() {
  useAiInvokeRunsStore().trackStarted({
    run_id: 'aiv-1', group_id: GROUP_ID, doc_ref: '', status: 'running',
    action_scope: 'resolve_conflict',
    provider_id: 'p1', provider_name: 'Claude Haiku 4.5',
  })
}

function finishConflictRun() {
  useAiInvokeRunsStore().trackFinished({
    run_id: 'aiv-1', group_id: GROUP_ID, doc_ref: '', status: 'finished',
    action_scope: 'resolve_conflict', outcome: 'complete', docs_reached: 0,
    docs_target: 0, reached_doc_ids: [], end_reason: 'exited', exit_code: 0,
    last_message_received: true, last_message: 'Resolved',
  })
}

beforeEach(() => {
  setActivePinia(createPinia())
  ;(Element.prototype as any).scrollTo = vi.fn()
  i18n.global.locale.value = 'en'
  getRequest.mockReset()
  postRequest.mockReset().mockResolvedValue({ data: { ok: true } })
  showToast.mockReset()
  state.review_state = null
  getRequest.mockImplementation((url: string) => {
    // A fresh object per call: mutating the shared literal in place would not
    // invalidate the panel's computeds (the ref hands out a reactive proxy).
    if (url.endsWith('/git/finalize')) return Promise.resolve({ data: { ok: true, state: { ...state } } })
    if (url.includes('/git/merge/4/conflicts')) {
      return Promise.resolve({
        data: { ok: true, files: [{ path: 'src/data/tasks.ts', content: CONFLICT, conflict_count: 1 }] },
      })
    }
    return Promise.reject(new Error('unexpected GET ' + url))
  })
})

async function openResolver(wrapper: ReturnType<typeof mountPanel>) {
  await flushPromises()
  await (wrapper.vm as any).openConflictDialog()
  await flushPromises()
  return wrapper.findComponent(GitConflictResolverDialog)
}

describe('GitFinalizePanel conflict AI run, in place (0481 T0010 rev3)', () => {
  it('tells the resolver dialog that its own run is working', async () => {
    const wrapper = mountPanel()
    const dialog = await openResolver(wrapper)
    expect(dialog.props('aiRunNotice')).toBeNull()

    startConflictRun()
    await flushPromises()

    const notice = wrapper.findComponent(GitConflictResolverDialog).props('aiRunNotice')
    expect(notice).toBeTruthy()
    expect(String(notice)).toContain('Claude Haiku 4.5')
    // The dialog is still there — that is the whole point of the change.
    expect(wrapper.findComponent(GitConflictResolverDialog).exists()).toBe(true)
    wrapper.unmount()
  })

  it('hands over to the approval gate when the run resolved the conflict', async () => {
    const wrapper = mountPanel()
    await openResolver(wrapper)
    startConflictRun()
    await flushPromises()

    state.review_state = 'resolved_pending_review'
    finishConflictRun()
    await flushPromises()
    await flushPromises()

    expect(wrapper.findComponent(GitConflictResolverDialog).exists()).toBe(false)
    expect(wrapper.findComponent(GitMergeReviewDialog).exists()).toBe(true)
    wrapper.unmount()
  })

  it('re-reads the conflict list when the run ended without resolving it', async () => {
    const wrapper = mountPanel()
    await openResolver(wrapper)
    startConflictRun()
    await flushPromises()
    const before = getRequest.mock.calls.filter(c => String(c[0]).includes('/conflicts')).length

    finishConflictRun()
    await flushPromises()
    await flushPromises()

    const after = getRequest.mock.calls.filter(c => String(c[0]).includes('/conflicts')).length
    expect(after).toBeGreaterThan(before)
    expect(wrapper.findComponent(GitConflictResolverDialog).exists()).toBe(true)
    wrapper.unmount()
  })
})

describe('GitConflictResolverDialog ai-run notice (0481 T0010 rev3)', () => {
  function mountDialog(aiRunNotice: string | null) {
    return mount(GitConflictResolverDialog, {
      props: {
        files: [makeFile('src/data/tasks.ts')],
        branch: 'test2_default_0009', baseBranch: 'main', busy: false,
        loadStatus: 'ready' as const, errorMessage: '',
        providers: [{ id: 'p1', name: 'Claude Haiku 4.5' }],
        selectedProvider: 'p1',
        aiRunNotice,
      },
      global: { plugins: [i18n], stubs: { AppIcon: true, teleport: true } },
    })
  }

  it('shows the run line and refuses a second call while one is working', () => {
    const wrapper = mountDialog('Claude Haiku 4.5 is resolving the conflict · 0:12 elapsed')
    expect(wrapper.find('[data-test="conflict-ai-run"]').exists()).toBe(true)
    const invoke = wrapper.findAll('button').find(b => b.text().includes('AI'))
    expect(invoke?.attributes('disabled')).toBeDefined()
    wrapper.unmount()
  })

  it('shows nothing and leaves the call enabled when no run is working', () => {
    const wrapper = mountDialog(null)
    expect(wrapper.find('[data-test="conflict-ai-run"]').exists()).toBe(false)
    const invoke = wrapper.findAll('button').find(b => b.text().includes('AI'))
    expect(invoke?.attributes('disabled')).toBeUndefined()
    wrapper.unmount()
  })
})
