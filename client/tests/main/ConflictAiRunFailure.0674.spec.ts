// flowgate.default.0674 T0004 §2-1 (D1) — a conflict AI run that ends WITHOUT resolving the
// conflict must not vanish from the conflict screens. 0668 NR0003: run aiv_20261005_001444
// went running -> finished(outcome=none, exit_code=1, "Selected model is at capacity") and
// both GitFinalizePanel and GitStatusPanel simply re-read the same conflict list.
import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import i18n from '@shared/i18n'
import GitFinalizePanel from '@main/components/GitFinalizePanel.vue'
import GitStatusPanel from '@main/components/GitStatusPanel.vue'
import GitConflictResolverDialog from '@main/components/GitConflictResolverDialog.vue'
import GitMergeReviewDialog from '@main/components/GitMergeReviewDialog.vue'
import { useProjectStore } from '@main/stores/project'
import { useAiInvokeRunsStore, type AiInvokeRunEntry } from '@main/stores/aiInvokeRuns'
import { judgeConflictAiRun } from '@main/composables/useConflictAiRunFailures'
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
const CAPACITY_REASON =
  '"Codex2 GPT 6-Sol" failed (exit code 1): Selected model is at capacity. Please try a different model. '
  + 'The run ended without completing its work.'

const GROUP_ID = 'test2.default.0668'
const state = {
  group_id: GROUP_ID, branch: 'test2_default_0668', base_branch: 'main',
  status: 'conflict', choices: [], ahead_count: 1, behind_count: 0, merge_id: 190,
  review_state: null as string | null,
}

function startRun(runId = 'aiv_20261005_001444', groupId = GROUP_ID) {
  useAiInvokeRunsStore().trackStarted({
    run_id: runId, group_id: groupId, doc_ref: '', status: 'running',
    action_scope: 'resolve_conflict', provider_id: 'c2', provider_name: 'Codex2 GPT 6-Sol',
  })
}

function finishRun(over: Record<string, unknown> = {}, runId = 'aiv_20261005_001444', groupId = GROUP_ID) {
  useAiInvokeRunsStore().trackFinished({
    run_id: runId, group_id: groupId, doc_ref: '', status: 'finished',
    action_scope: 'resolve_conflict', outcome: 'none', docs_reached: 0, docs_target: 0,
    reached_doc_ids: [], end_reason: 'exited', exit_code: 1,
    last_message_received: false, last_message: null,
    stop_code: 'provider_failed', stop_reason: CAPACITY_REASON,
    ...over,
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
    if (url.endsWith('/git/finalize')) return Promise.resolve({ data: { ok: true, state: { ...state } } })
    if (url.includes('/git/merge/190/conflicts')) {
      return Promise.resolve({
        data: { ok: true, files: [{ path: 'server/modules/flow_gate/services/git/cleanup.py', content: CONFLICT, conflict_count: 1 }] },
      })
    }
    if (url.endsWith('/git/status')) {
      return Promise.resolve({ data: { ok: true, status: gitStatus() } })
    }
    return Promise.reject(new Error('unexpected GET ' + url))
  })
})

function gitStatus() {
  return {
    enabled: true, base_branch: 'main', base_path_state: 'ready', ahead_count: 0, behind_count: 0,
    slots: [],
    pending: [{
      group_id: GROUP_ID, branch: 'test2_default_0668', status: 'conflict', default_action: 'merge',
      merge_id: 190, review_state: state.review_state,
    }],
    pending_count: 1,
  }
}

function entry(over: Partial<AiInvokeRunEntry>): AiInvokeRunEntry {
  return { phase: 'finished', outcome: 'none', stopReason: null, lastMessage: null, ...over } as AiInvokeRunEntry
}

describe('judgeConflictAiRun (0674 D1)', () => {
  it('a complete landing is no failure', () => {
    expect(judgeConflictAiRun('r1', entry({ outcome: 'complete' }))).toBeNull()
  })

  it('outcome none carries the server reason first, then the last message', () => {
    expect(judgeConflictAiRun('r1', entry({ stopReason: 'provider down', lastMessage: 'hi' }))?.reason)
      .toBe('provider down')
    expect(judgeConflictAiRun('r1', entry({ lastMessage: 'only   this\nline' }))?.reason)
      .toBe('only this line')
    expect(judgeConflictAiRun('r1', entry({ outcome: 'partial' }))).toMatchObject({ outcome: 'partial', reason: null })
  })

  it('a lost run and an ending this tab never saw are failures without a reason', () => {
    expect(judgeConflictAiRun('r1', entry({ phase: 'lost', outcome: null }))).toMatchObject({ lost: true })
    expect(judgeConflictAiRun('r1', undefined)).toMatchObject({ runId: 'r1', outcome: null, reason: null })
  })

  it('a very long reason is clipped', () => {
    const reason = judgeConflictAiRun('r1', entry({ stopReason: 'x'.repeat(1000) }))?.reason ?? ''
    expect(reason.length).toBeLessThanOrEqual(300)
  })
})

function mountFinalizePanel() {
  useProjectStore().setCurrentProject('test2')
  return mount(GitFinalizePanel, {
    props: { groupId: GROUP_ID },
    global: { plugins: [i18n], stubs: { AppIcon: true, GitMergeReviewDialog: true, teleport: true } },
  })
}

async function openResolver(wrapper: ReturnType<typeof mountFinalizePanel>) {
  await flushPromises()
  await (wrapper.vm as any).openConflictDialog()
  await flushPromises()
  return wrapper.findComponent(GitConflictResolverDialog)
}

describe('GitFinalizePanel — a failed conflict AI run is said, not swallowed (0674 D1)', () => {
  it('running -> finished(outcome=none, exit_code=1) with the conflict still open shows the failure', async () => {
    const wrapper = mountFinalizePanel()
    await openResolver(wrapper)
    startRun()
    await flushPromises()
    expect(wrapper.findComponent(GitConflictResolverDialog).props('aiRunFailure')).toBeFalsy()

    finishRun()
    await flushPromises()
    await flushPromises()

    const dialog = wrapper.findComponent(GitConflictResolverDialog)
    expect(dialog.exists()).toBe(true)
    const failure = String(dialog.props('aiRunFailure'))
    expect(failure).toContain('Selected model is at capacity')
    expect(failure).toContain(i18n.global.t('main.git_finalize.conflict_ai_failed_next'))
    // the panel itself says it too, under the conflict summary
    expect(wrapper.find('[data-test="conflict-ai-failed"]').text()).toContain('Selected model is at capacity')
    // the dialog's AI strip turns into the failure line; no run is live any more
    const strip = dialog.find('[data-test="conflict-ai-run"]')
    expect(strip.attributes('data-kind')).toBe('failed')
    expect(dialog.props('aiRunNotice')).toBeFalsy()
    wrapper.unmount()
  })

  it('a failure without any recorded reason still says the run ended unresolved', async () => {
    const wrapper = mountFinalizePanel()
    await openResolver(wrapper)
    startRun()
    await flushPromises()
    finishRun({ stop_code: null, stop_reason: null })
    await flushPromises()
    await flushPromises()

    expect(String(wrapper.findComponent(GitConflictResolverDialog).props('aiRunFailure')))
      .toContain(i18n.global.t('main.git_finalize.conflict_ai_failed_no_reason'))
    wrapper.unmount()
  })

  it('outcome=complete + reviewPending keeps the hand-over to the approval gate, with no failure', async () => {
    const wrapper = mountFinalizePanel()
    await openResolver(wrapper)
    startRun()
    await flushPromises()
    state.review_state = 'resolved_pending_review'
    finishRun({ outcome: 'complete', exit_code: 0, stop_code: null, stop_reason: null })
    await flushPromises()
    await flushPromises()

    expect(wrapper.findComponent(GitConflictResolverDialog).exists()).toBe(false)
    expect(wrapper.findComponent(GitMergeReviewDialog).exists()).toBe(true)
    expect(wrapper.find('[data-test="conflict-ai-failed"]').exists()).toBe(false)
    wrapper.unmount()
  })

  it('a new run (another provider) clears the old failure while it works', async () => {
    const wrapper = mountFinalizePanel()
    await openResolver(wrapper)
    startRun()
    await flushPromises()
    finishRun()
    await flushPromises()
    await flushPromises()
    expect(wrapper.findComponent(GitConflictResolverDialog).props('aiRunFailure')).toBeTruthy()

    startRun('aiv_20261005_001445')
    await flushPromises()
    const dialog = wrapper.findComponent(GitConflictResolverDialog)
    expect(dialog.props('aiRunFailure')).toBeFalsy()
    expect(dialog.props('aiRunNotice')).toBeTruthy()
    wrapper.unmount()
  })

  it('abort clears the failure', async () => {
    const wrapper = mountFinalizePanel()
    await openResolver(wrapper)
    startRun()
    await flushPromises()
    finishRun()
    await flushPromises()
    await flushPromises()
    expect(wrapper.find('[data-test="conflict-ai-failed"]').exists()).toBe(true)

    await (wrapper.vm as any).$.setupState.abortMerge()
    await flushPromises()
    expect(wrapper.find('[data-test="conflict-ai-failed"]').exists()).toBe(false)
    wrapper.unmount()
  })

  it("another group's run never paints this panel", async () => {
    const wrapper = mountFinalizePanel()
    await openResolver(wrapper)
    startRun('aiv_other', 'test2.default.0999')
    await flushPromises()
    finishRun({}, 'aiv_other', 'test2.default.0999')
    await flushPromises()
    await flushPromises()
    expect(wrapper.findComponent(GitConflictResolverDialog).props('aiRunFailure')).toBeFalsy()
    wrapper.unmount()
  })
})

describe('GitStatusPanel — the header resolver says the run failed too (0674 D1)', () => {
  it('re-opens the resolver with the failure, reason and next steps', async () => {
    const wrapper = mount(GitStatusPanel, {
      props: { projectId: 'test2' },
      global: { plugins: [i18n], stubs: { AppIcon: true, GitMergeReviewDialog: true, teleport: true } },
    })
    await flushPromises()
    await wrapper.find('.git-status-row-main .btn-danger').trigger('click')
    await flushPromises()
    expect(wrapper.findComponent(GitConflictResolverDialog).exists()).toBe(true)

    startRun()
    await flushPromises()
    expect(wrapper.findComponent(GitConflictResolverDialog).props('aiRunNotice')).toBeTruthy()

    finishRun()
    await flushPromises()
    await flushPromises()
    await flushPromises()

    const dialog = wrapper.findComponent(GitConflictResolverDialog)
    expect(dialog.exists()).toBe(true)
    expect(dialog.props('aiRunNotice')).toBeFalsy()
    const failure = String(dialog.props('aiRunFailure'))
    expect(failure).toContain('Selected model is at capacity')
    expect(failure).toContain(i18n.global.t('main.git_finalize.conflict_ai_failed_next'))
    wrapper.unmount()
  })
})

describe('GitConflictResolverDialog — failure line (0674 D1)', () => {
  function makeFile(path: string): ConflictFileState {
    const segments = parseConflictFile(CONFLICT)
    if (!segments) throw new Error('fixture must parse')
    return { path, conflict_count: 1, directText: CONFLICT, mode: 'chunk', segments, notice: '' }
  }
  function mountDialog(props: Record<string, unknown>) {
    return mount(GitConflictResolverDialog, {
      props: {
        files: [makeFile('a.txt')], branch: 'b', baseBranch: 'main', busy: false,
        loadStatus: 'ready' as const, errorMessage: '',
        providers: [{ id: 'p1', name: 'P1' }], selectedProvider: 'p1',
        ...props,
      },
      global: { plugins: [i18n], stubs: { AppIcon: true, teleport: true } },
    })
  }

  it('renders the failure as its own warning line', () => {
    const wrapper = mountDialog({ aiRunFailure: 'The AI conflict resolution ended without resolving the conflict.' })
    const strip = wrapper.find('[data-test="conflict-ai-run"]')
    expect(strip.exists()).toBe(true)
    expect(strip.attributes('data-kind')).toBe('failed')
    expect(strip.classes()).toContain('git-conflict-ai-strip--failed')
    expect(strip.text()).toContain('ended without resolving')
    wrapper.unmount()
  })

  it('a live run outranks a stale failure', () => {
    const wrapper = mountDialog({ aiRunFailure: 'old failure', aiRunNotice: 'P1 is resolving the conflict' })
    const strip = wrapper.find('[data-test="conflict-ai-run"]')
    expect(strip.attributes('data-kind')).toBe('run')
    expect(strip.text()).toContain('P1 is resolving')
    wrapper.unmount()
  })
})
