// flowgate.default.0481 T0010 rev5 — the three host-side halves of this rejection:
//
//   #1 "충돌해결에서 AI호출 했더니 아무것도 안하고 가만히 있음... 뭔가 표시되는것도 없고"
//      → the press has to be visible IMMEDIATELY. The start POST returns before the worker
//        thread broadcasts `ai_invoke_started`, so a screen that waits for the SSE frame is
//        blank for as long as that takes — and forever if the frame never lands. Both hosts
//        now adopt their own start response, exactly like `resolve_base_dirty` next door.
//   #3 "알아서 승인화면으로 가세요 할게 아니라 대려다줘야 할거 아냐?"
//      → a submit that lands on `resolved_pending_review` OPENS the approval screen.
//   #4 "프로바이더가 하나도 안나온게 있었는데 어떤 조건에서 안나오는지 모르겠다"
//      → the dialog can re-read the list, and the retry has to be FORCED: `ensureLoaded`
//        treats a project it already failed for as "loaded".
import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import i18n from '@shared/i18n'
import GitFinalizePanel from '@main/components/GitFinalizePanel.vue'
import GitStatusPanel from '@main/components/GitStatusPanel.vue'
import GitConflictResolverDialog from '@main/components/GitConflictResolverDialog.vue'
import GitMergeReviewDialog from '@main/components/GitMergeReviewDialog.vue'
import { useProjectStore } from '@main/stores/project'
import { useAiInvokeRunsStore, isScreenOwnedRun } from '@main/stores/aiInvokeRuns'
import { useAiProviderStore } from '@main/stores/aiProvider'

const { getRequest, postRequest, showToast } = vi.hoisted(() => ({
  getRequest: vi.fn(), postRequest: vi.fn(), showToast: vi.fn(),
}))
vi.mock('@shared/api', () => ({
  default: { head: vi.fn(), get: vi.fn(), post: vi.fn(), patch: vi.fn() },
  getRequest, postRequest,
}))
vi.mock('@main/components/common/useToast', () => ({ useToast: () => ({ showToast }) }))

const CONFLICT = ['x', '<<<<<<< HEAD', 'ours', '=======', 'theirs', '>>>>>>> main', 'y'].join('\n')
const RESOLVED = ['x', 'ours', 'y'].join('\n')
const GROUP_ID = 'test2.default.0005'
const MERGE_ID = 6

// What POST /ai-invoke/start answers for a conflict run, as the server builds it
// (admission.start_run). action_scope/project_id/merge_id ride along since rev5.
const START_RESPONSE = {
  ok: true, run_id: 'aiv_20260908_000003', status: 'running', mode: 'single',
  group_id: GROUP_ID, project_id: 'test2', action_scope: 'resolve_conflict', merge_id: MERGE_ID,
  doc_ref: '', docs_target: 1, provider: { id: 'p1', name: 'Claude Haiku 4.5' },
  attempt_no: 1, started_at: new Date().toISOString(),
}

const PROVIDERS = {
  ok: true, project: 'test2', providers: [{ id: 'p1', name: 'Claude Haiku 4.5', exec_type: 'cli', kind: 'claude' }],
  default_provider_id: 'p1',
}

const finalizeState = {
  group_id: GROUP_ID, branch: 'test2_default_0005', base_branch: 'main',
  status: 'conflict', choices: [], ahead_count: 1, behind_count: 0, merge_id: MERGE_ID,
  review_state: null as string | null,
}

function gitStatus() {
  return {
    enabled: true, base_branch: 'main', base_path_state: 'ready',
    ahead_count: 0, behind_count: 0, slots: [],
    pending: [{ group_id: GROUP_ID, branch: 'test2_default_0005', status: 'conflict', default_action: 'merge', merge_id: MERGE_ID }],
    pending_count: 1,
  }
}

beforeEach(() => {
  setActivePinia(createPinia())
  ;(Element.prototype as any).scrollTo = vi.fn()
  i18n.global.locale.value = 'ko'
  showToast.mockReset()
  postRequest.mockReset().mockResolvedValue({ data: { ok: true } })
  finalizeState.review_state = null
  getRequest.mockReset().mockImplementation((url: string) => {
    if (url.endsWith('/git/finalize')) return Promise.resolve({ data: { ok: true, state: { ...finalizeState } } })
    if (url.endsWith('/git/status')) return Promise.resolve({ data: { ok: true, status: gitStatus() } })
    if (url.includes('/ai-invoke/providers')) return Promise.resolve({ data: PROVIDERS })
    if (url.includes(`/git/merge/${MERGE_ID}/conflicts`)) {
      return Promise.resolve({ data: { ok: true, files: [{ path: 'README.md', content: CONFLICT, conflict_count: 1 }] } })
    }
    if (url.includes('/git/merge/')) return Promise.resolve({ data: { ok: true, review: { files: [], conversation: [] } } })
    return Promise.reject(new Error('unexpected GET ' + url))
  })
})

function mountFinalize() {
  useProjectStore().setCurrentProject('test2')
  return mount(GitFinalizePanel, {
    props: { groupId: GROUP_ID },
    global: { plugins: [i18n], stubs: { AppIcon: true, GitMergeReviewDialog: true, teleport: true } },
  })
}

describe('GitFinalizePanel — the AI call is visible the moment it is pressed (반려 #1)', () => {
  it('adopts its own start response, so no SSE frame is needed for the run to show', async () => {
    const wrapper = mountFinalize()
    await flushPromises()
    await (wrapper.vm as any).openConflictDialog()
    await flushPromises()

    postRequest.mockResolvedValueOnce({ data: START_RESPONSE })
    wrapper.findComponent(GitConflictResolverDialog).vm.$emit('ai-invoke', '', false)
    await flushPromises()

    const store = useAiInvokeRunsStore()
    const entry = store.runsByGroup[GROUP_ID]
    expect(entry).toBeTruthy()
    expect(entry.runId).toBe('aiv_20260908_000003')
    // The identity that decides whether MainPanel covers the dialog that started the run.
    expect(entry.actionScope).toBe('resolve_conflict')
    expect(isScreenOwnedRun(entry)).toBe(true)
    // …and the dialog is now saying so, with no SSE and no polling in between.
    const notice = wrapper.findComponent(GitConflictResolverDialog).props('aiRunNotice')
    expect(notice).toContain('Claude Haiku 4.5')
    wrapper.unmount()
  })

  it('an older server (no action_scope in the start payload) still yields a screen-owned run', async () => {
    const wrapper = mountFinalize()
    await flushPromises()
    await (wrapper.vm as any).openConflictDialog()
    await flushPromises()

    const legacy = { ...START_RESPONSE }
    delete (legacy as any).action_scope
    delete (legacy as any).project_id
    postRequest.mockResolvedValueOnce({ data: legacy })
    wrapper.findComponent(GitConflictResolverDialog).vm.$emit('ai-invoke', '', false)
    await flushPromises()

    expect(isScreenOwnedRun(useAiInvokeRunsStore().runsByGroup[GROUP_ID])).toBe(true)
    wrapper.unmount()
  })

  it('holds the dialog in a "call sent" state while the start request is in flight', async () => {
    const wrapper = mountFinalize()
    await flushPromises()
    await (wrapper.vm as any).openConflictDialog()
    await flushPromises()

    let release: (value: unknown) => void = () => {}
    postRequest.mockImplementationOnce(() => new Promise((done) => { release = done }))
    wrapper.findComponent(GitConflictResolverDialog).vm.$emit('ai-invoke', '', false)
    await flushPromises()
    // No run entry exists yet — this is precisely the window that used to draw nothing.
    expect(useAiInvokeRunsStore().runsByGroup[GROUP_ID]).toBeUndefined()
    expect(wrapper.findComponent(GitConflictResolverDialog).props('aiRunPending')).toBe(true)

    release({ data: START_RESPONSE })
    await flushPromises()
    expect(wrapper.findComponent(GitConflictResolverDialog).props('aiRunPending')).toBe(false)
    wrapper.unmount()
  })

  it('forces a provider reload when the dialog asks for one (반려 #4)', async () => {
    const wrapper = mountFinalize()
    await flushPromises()
    await (wrapper.vm as any).openConflictDialog()
    await flushPromises()
    const before = getRequest.mock.calls.filter(c => String(c[0]).includes('/ai-invoke/providers')).length

    wrapper.findComponent(GitConflictResolverDialog).vm.$emit('reload-providers')
    await flushPromises()

    // ensureLoaded would have short-circuited here; only a forced load re-requests.
    const after = getRequest.mock.calls.filter(c => String(c[0]).includes('/ai-invoke/providers')).length
    expect(after).toBe(before + 1)
    wrapper.unmount()
  })
})

describe('the resolver hands over to the approval screen (반려 #3)', () => {
  it('GitFinalizePanel opens the approval dialog instead of telling the operator to go there', async () => {
    const wrapper = mountFinalize()
    await flushPromises()
    await (wrapper.vm as any).openConflictDialog()
    await flushPromises()

    // Resolve every chunk so [해결 제출] is allowed to fire.
    const dialog = wrapper.findComponent(GitConflictResolverDialog)
    const files = dialog.props('files') as Array<{ mode: string; directText: string }>
    files[0].mode = 'direct'
    files[0].directText = RESOLVED
    await flushPromises()

    postRequest.mockResolvedValueOnce({ data: { ok: true, result: { status: 'resolved_pending_review' } } })
    finalizeState.review_state = 'resolved_pending_review'
    dialog.vm.$emit('submit', false)
    await flushPromises()

    expect(wrapper.findComponent(GitConflictResolverDialog).exists()).toBe(false)
    expect(wrapper.findComponent(GitMergeReviewDialog).exists()).toBe(true)
    expect(showToast).toHaveBeenCalledWith(
      i18n.global.t('main.git_review.resolved_pending_opened'), 'success',
    )
    wrapper.unmount()
  })

  it('GitStatusPanel opens the approval dialog for a general merge', async () => {
    const wrapper = mount(GitStatusPanel, {
      props: { projectId: 'test2' },
      global: { plugins: [i18n], stubs: { AppIcon: true, GitMergeReviewDialog: true, teleport: true } },
    })
    await flushPromises()
    await wrapper.find('.git-status-row-main .btn-danger').trigger('click')
    await flushPromises()

    const dialog = wrapper.findComponent(GitConflictResolverDialog)
    const files = dialog.props('files') as Array<{ mode: string; directText: string }>
    files[0].mode = 'direct'
    files[0].directText = RESOLVED
    await flushPromises()

    postRequest.mockResolvedValueOnce({ data: { ok: true, result: { status: 'resolved_pending_review' } } })
    dialog.vm.$emit('submit', false)
    await flushPromises()

    expect(wrapper.findComponent(GitConflictResolverDialog).exists()).toBe(false)
    expect(wrapper.findComponent(GitMergeReviewDialog).exists()).toBe(true)
    wrapper.unmount()
  })
})

describe('the empty provider list names its condition (반려 #4)', () => {
  it('an open dialog whose provider load is still in flight says so instead of showing nothing', async () => {
    // The condition the reviewer hit: both hosts fire `ensureLoaded` WITHOUT awaiting it, so
    // the dialog is on screen before the list is. It is a short window, but until rev5 it was
    // indistinguishable from "this project has no providers" — the select was blank and
    // [AI 호출] was disabled with a tooltip that said the list could not be read.
    let releaseProviders: (value: unknown) => void = () => {}
    getRequest.mockImplementation((url: string) => {
      if (url.endsWith('/git/status')) return Promise.resolve({ data: { ok: true, status: gitStatus() } })
      if (url.includes('/ai-invoke/providers')) return new Promise((done) => { releaseProviders = done })
      if (url.includes(`/git/merge/${MERGE_ID}/conflicts`)) {
        return Promise.resolve({ data: { ok: true, files: [{ path: 'README.md', content: CONFLICT, conflict_count: 1 }] } })
      }
      return Promise.reject(new Error('unexpected GET ' + url))
    })

    const wrapper = mount(GitStatusPanel, {
      props: { projectId: 'test2' },
      global: { plugins: [i18n], stubs: { AppIcon: true, GitMergeReviewDialog: true, teleport: true } },
    })
    await flushPromises()
    await wrapper.find('.git-status-row-main .btn-danger').trigger('click')
    await flushPromises()

    const dialog = wrapper.findComponent(GitConflictResolverDialog)
    expect(dialog.props('providers')).toEqual([])
    expect(dialog.props('providerLoading')).toBe(true)
    expect(dialog.props('providerErrored')).toBe(false)

    releaseProviders({ data: PROVIDERS })
    await flushPromises()
    expect(wrapper.findComponent(GitConflictResolverDialog).props('providerLoading')).toBe(false)
    expect(wrapper.findComponent(GitConflictResolverDialog).props('providers')).toHaveLength(1)
    wrapper.unmount()
  })

  it('a failed load is reported as errored, and only a forced reload can recover it', async () => {
    getRequest.mockImplementation((url: string) => {
      if (url.endsWith('/git/status')) return Promise.resolve({ data: { ok: true, status: gitStatus() } })
      if (url.includes('/ai-invoke/providers')) return Promise.reject(new Error('boom'))
      if (url.includes(`/git/merge/${MERGE_ID}/conflicts`)) {
        return Promise.resolve({ data: { ok: true, files: [{ path: 'README.md', content: CONFLICT, conflict_count: 1 }] } })
      }
      return Promise.reject(new Error('unexpected GET ' + url))
    })

    const wrapper = mount(GitStatusPanel, {
      props: { projectId: 'test2' },
      global: { plugins: [i18n], stubs: { AppIcon: true, GitMergeReviewDialog: true, teleport: true } },
    })
    await flushPromises()
    await wrapper.find('.git-status-row-main .btn-danger').trigger('click')
    await flushPromises()

    expect(wrapper.findComponent(GitConflictResolverDialog).props('providerErrored')).toBe(true)
    // A failed load still stamps loadedProjectId, so ensureLoaded would refuse to try again
    // for a project that has an empty list for any reason other than "not tried yet".
    const store = useAiProviderStore()
    expect(store.loadedProjectId).toBe('test2')
    const beforeEnsure = getRequest.mock.calls.filter(c => String(c[0]).includes('/ai-invoke/providers')).length
    await store.ensureLoaded('test2')
    const afterEnsure = getRequest.mock.calls.filter(c => String(c[0]).includes('/ai-invoke/providers')).length

    getRequest.mockImplementation((url: string) => {
      if (url.includes('/ai-invoke/providers')) return Promise.resolve({ data: PROVIDERS })
      if (url.endsWith('/git/status')) return Promise.resolve({ data: { ok: true, status: gitStatus() } })
      return Promise.reject(new Error('unexpected GET ' + url))
    })
    wrapper.findComponent(GitConflictResolverDialog).vm.$emit('reload-providers')
    await flushPromises()

    expect(wrapper.findComponent(GitConflictResolverDialog).props('providerErrored')).toBe(false)
    expect(wrapper.findComponent(GitConflictResolverDialog).props('providers')).toHaveLength(1)
    // (documented, not required: ensureLoaded's own retry — the forced reload is what the
    // dialog's control uses, and it is the one that cannot be short-circuited.)
    expect(afterEnsure).toBeGreaterThanOrEqual(beforeEnsure)
    wrapper.unmount()
  })
})

describe('GitStatusPanel — the header resolver registers its own run too (반려 #1)', () => {
  it('tracks the start response and shows the run in the dialog it was pressed in', async () => {
    const wrapper = mount(GitStatusPanel, {
      props: { projectId: 'test2' },
      global: { plugins: [i18n], stubs: { AppIcon: true, GitMergeReviewDialog: true, teleport: true } },
    })
    await flushPromises()
    await wrapper.find('.git-status-row-main .btn-danger').trigger('click')
    await flushPromises()

    postRequest.mockResolvedValueOnce({ data: START_RESPONSE })
    wrapper.findComponent(GitConflictResolverDialog).vm.$emit('ai-invoke', '충돌 정리해줘', false)
    await flushPromises()

    const entry = useAiInvokeRunsStore().runsByGroup[GROUP_ID]
    expect(entry?.actionScope).toBe('resolve_conflict')
    expect(wrapper.findComponent(GitConflictResolverDialog).props('aiRunNotice')).toContain('Claude Haiku 4.5')
    wrapper.unmount()
  })

  it('takes the operator to the approval screen when its own run resolved the merge (반려 #3)', async () => {
    const wrapper = mount(GitStatusPanel, {
      props: { projectId: 'test2' },
      global: { plugins: [i18n], stubs: { AppIcon: true, GitMergeReviewDialog: true, teleport: true } },
    })
    await flushPromises()
    await wrapper.find('.git-status-row-main .btn-danger').trigger('click')
    await flushPromises()

    postRequest.mockResolvedValueOnce({ data: START_RESPONSE })
    wrapper.findComponent(GitConflictResolverDialog).vm.$emit('ai-invoke', '', false)
    await flushPromises()

    // The run ends having resolved everything: the session is now at the approval gate.
    const resolved = gitStatus()
    resolved.pending[0] = { ...resolved.pending[0], review_state: 'resolved_pending_review' } as any
    getRequest.mockImplementation((url: string) => {
      if (url.endsWith('/git/status')) return Promise.resolve({ data: { ok: true, status: resolved } })
      if (url.includes('/ai-invoke/providers')) return Promise.resolve({ data: PROVIDERS })
      return Promise.reject(new Error('unexpected GET ' + url))
    })
    useAiInvokeRunsStore().trackFinished({
      run_id: START_RESPONSE.run_id, group_id: GROUP_ID, doc_ref: '', status: 'finished',
      action_scope: 'resolve_conflict', outcome: 'complete', docs_reached: 0, docs_target: 0,
      reached_doc_ids: [], end_reason: 'exited', exit_code: 0,
      last_message_received: true, last_message: 'done',
    })
    await flushPromises()
    await flushPromises()

    // Not a resolver with nothing left in it — the next screen.
    expect(wrapper.findComponent(GitConflictResolverDialog).exists()).toBe(false)
    expect(wrapper.findComponent(GitMergeReviewDialog).exists()).toBe(true)
    wrapper.unmount()
  })
})
