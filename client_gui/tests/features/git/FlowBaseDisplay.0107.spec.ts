/** T#3 D §6.2-4/7/8: real 0104 dialog, store, HTTP display boundary and Git panel.
 * Only transport and non-work-base Git answers are fixtures. No real Git writes. */
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import { setCurrentUser } from '@/core/auth/currentUser'
import { useProjectStore } from '@/features/project'
import { DesignerGitPanel } from '@/features/git'
import { createHttpGitSource } from '@/features/git/gitSource'
import FlowBaseBranchDialog from '@/features/designer/fileMenu/FlowBaseBranchDialog.vue'
import { G, P, createFakeGitSource, status } from './fakeGitSource'
import { createFakeWorktreeSource } from './fakeWorktreeSource'

const { get, patch } = vi.hoisted(() => ({ get: vi.fn(), patch: vi.fn() }))
vi.mock('@shared/api', async (original) => ({
  ...(await original<typeof import('@shared/api')>()), default: { get }, getRequest: get, patchRequest: patch,
}))
registerAppMessages(i18n)
const BASE = 'feature/v0.2.0'
const SAVED = 'release/0104'
const FLOW = { groupId: G, name: 'A' }
const FLOW_B = { groupId: 'p.m.0002', name: 'B' }
let stored = BASE
let locked = false
let wrappers: VueWrapper[] = []
let store: ReturnType<typeof useProjectStore>
const response = (gid = G) => ({ data: {
  group_id: gid, project_id: P, git_enabled: true, work_base_ref: stored, effective_work_base_ref: stored,
  work_base_locked: locked, work_base_editable: !locked, lock_evidence: [], can_edit: true,
  reason: locked ? 'history_locked' : null,
} })
function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (error: unknown) => void
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no })
  return { promise, resolve, reject }
}
beforeEach(() => {
  localStorage.clear(); sessionStorage.clear()
  setActivePinia(createPinia())
  setCurrentUser({ username: 'alice', email: null, isAdmin: false })
  i18n.global.locale.value = 'ko'
  store = useProjectStore(); stored = BASE; locked = false
  get.mockReset().mockImplementation(async (url: string) => {
    if (url.endsWith('/work-base')) return response(url.split('/').at(-2))
    if (url.endsWith('/work-base-options')) return { data: { git_enabled: true, base_branch: 'main',
      branches: ['main', BASE, SAVED].map(name => ({ name, is_project_base: name === 'main' })) } }
    if (url.endsWith('/groups/tree')) return { data: { data: { nodes: [] } } }
    throw new Error(`unexpected GET ${url}`)
  })
  patch.mockReset().mockImplementation(async (_url: string, body: { work_base_ref: string }) => {
    stored = body.work_base_ref
    return { data: {} }
  })
})
afterEach(() => {
  wrappers.reverse().forEach(w => w.unmount()); wrappers = []
  store.$dispose(); setCurrentUser(null); document.body.innerHTML = ''
})
const q = (name: string) => document.body.querySelector<HTMLElement>(`[data-test="${name}"]`)
const reads = () => get.mock.calls.filter(([url]) => /\/groups\/[^/]+\/work-base$/.test(url))
function holdNextRead() {
  const pending = deferred<ReturnType<typeof response>>()
  const otherwise = get.getMockImplementation()!
  let used = false
  get.mockImplementation((url: string, ...args: unknown[]) => {
    if (!used && /\/groups\/[^/]+\/work-base$/.test(url)) { used = true; return pending.promise }
    return otherwise(url, ...args)
  })
  return pending
}
async function click(el: HTMLElement | null) {
  expect(el).not.toBeNull(); el!.click(); await flushPromises()
}
const refresh = (w: VueWrapper) => (w.vm as unknown as { refresh(): Promise<void> }).refresh()
async function panel(props: Record<string, unknown> = {}) {
  const wt = createFakeWorktreeSource()
  // Use the real GitSource -> store -> API -> GET for every display read.
  const http = createHttpGitSource()
  wt.source.loadWorkBase.mockImplementation(http.worktree!.loadWorkBase)
  const fake = createFakeGitSource({ status: status({ baseBranch: 'main' }) }, { worktree: wt.source })
  const w = mount(DesignerGitPanel, { props: { projectId: P, flow: FLOW, source: fake.source, ...props },
    attachTo: document.body, global: { plugins: [i18n] } })
  wrappers.push(w); await flushPromises()
  return { w, wt, fake }
}
function dialog() {
  const w = mount(FlowBaseBranchDialog, { props: { open: true, projectId: P, groupId: G },
    attachTo: document.body, global: { plugins: [i18n] } })
  wrappers.push(w); return w
}
async function save() {
  const d = dialog(); await flushPromises()
  const choice = q('flow-base-choice') as HTMLSelectElement
  choice.value = SAVED; choice.dispatchEvent(new Event('change')); await flushPromises()
  await click(q('flow-base-save'))
  expect(patch).toHaveBeenLastCalledWith(`/api/v1/groups/${G}/work-base`, {
    work_base_ref: SAVED, expected_work_base_ref: BASE,
  })
  expect(d.emitted('saved')).toHaveLength(1)
  await d.setProps({ open: false }); await flushPromises()
}
async function expectBoth(base: string) {
  await click(document.querySelector('[data-test="git-worktree-entry"][data-tab="status"]'))
  expect(q('git-base-branch')!.textContent).toContain(`플로우 베이스 · ${base}`)
  expect(q('git-status-base')!.textContent).toBe(base)
}

describe('D §6.2-4/7 — 0104 save and fresh display', () => {
  it.each(['refresh', 'reactivate', 'reopen', 'F5'] as const)('0104 save then %s reads the saved base, without reusing old display state', async (trigger) => {
    const t = await panel()
    await expectBoth(BASE)
    await store.loadWorkBaseOptions(P); await store.loadWorkBaseOptions('q')
    const trees = await Promise.all([store.fetchGroupTree(P), store.fetchGroupTree('q')])
    const settings = vi.spyOn(store, 'loadFlowWorkBase')
    await save()
    expect(settings).toHaveBeenCalledTimes(1) // the settings dialog alone
    expect(store.getCachedGroupTree(P)).toBeUndefined()
    expect(store.getCachedGroupTree('q')).toBe(trees[1])
    expect(store.knownGitEnabled[P]).toBeUndefined()
    expect(store.knownGitEnabled.q).toBe(true)
    const before = reads().length
    if (trigger === 'refresh') await refresh(t.w)
    if (trigger === 'reactivate') {
      await t.w.setProps({ active: false }); await t.w.setProps({ active: true })
    }
    if (trigger === 'reopen') {
      t.w.unmount(); wrappers.splice(wrappers.indexOf(t.w), 1); await panel()
    }
    if (trigger === 'F5') {
      // App recreation and delayed restoration of context; the existing Shell restore itself
      // is covered by designerSessionRestore.0105.spec.ts, not reimplemented here.
      t.w.unmount(); wrappers.splice(wrappers.indexOf(t.w), 1)
      store.$dispose(); setActivePinia(createPinia()); store = useProjectStore()
      const restored = await panel({ projectId: null, flow: null })
      expect(reads()).toHaveLength(before)
      expect(q('git-base-branch')?.textContent ?? '').not.toContain(BASE)
      const pending = holdNextRead()
      await restored.w.setProps({ projectId: P, flow: FLOW }); await flushPromises()
      expect(q('git-base-branch')!.textContent).toContain('플로우 베이스 · —')
      pending.resolve(response()); await flushPromises()
    }
    await flushPromises(); await expectBoth(SAVED)
    expect(reads()).toHaveLength(before + 1)
    expect(settings).toHaveBeenCalledTimes(1)
    expect(t.fake.db.status?.baseBranch).toBe('main')
  })

  it('a locked 0104 setting offers no write and every display refresh keeps its saved base', async () => {
    locked = true
    const t = await panel(); const d = dialog(); await flushPromises()
    expect(q('flow-base')?.dataset.state).toBe('readonly')
    expect(q('flow-base-save')).toBeNull(); expect(q('flow-base-choice')).toBeNull()
    await d.setProps({ open: false }); await refresh(t.w); await expectBoth(BASE)
    expect(patch).not.toHaveBeenCalled(); expect(stored).toBe(BASE)
  })
})

describe('D §6.2-8 — HTTP work-base count', () => {
  it.each(['group_id', 'project_id'] as const)('%s mismatch is refused by the real display path and never reaches either row or shared Git knowledge', async field => {
    await store.loadWorkBaseOptions(P); await store.loadWorkBaseOptions('q')
    const known = { ...store.knownGitEnabled }
    const settings = vi.spyOn(store, 'loadFlowWorkBase')
    const pending = holdNextRead()
    await panel()
    pending.resolve({ data: { ...response().data, [field]: field === 'group_id' ? 'p.m.wrong' : 'q',
      effective_work_base_ref: 'must-not-display', git_enabled: false } })
    await flushPromises(); await expectBoth('—')
    expect(q('git-status-failed')).not.toBeNull()
    expect(store.knownGitEnabled).toEqual(known)
    expect(settings).not.toHaveBeenCalled()
    expect(reads()).toHaveLength(1)
  })

  it('counts context, transition and completed refresh; running header/external/activation reads share one GET', async () => {
    const settings = vi.spyOn(store, 'loadFlowWorkBase')
    const t = await panel(); expect(reads()).toHaveLength(1)
    await expectBoth(BASE)
    for (const name of ['files', 'merge', 'status']) await click(document.querySelector(`[data-test="git-window-tab"][data-tab="${name}"]`))
    await t.w.setProps({ readOnly: true }); await t.w.setProps({ readOnly: false })
    expect(reads()).toHaveLength(1)
    await t.w.setProps({ flow: FLOW_B }); await flushPromises(); expect(reads()).toHaveLength(2)
    await refresh(t.w); await flushPromises(); expect(reads()).toHaveLength(3)
    const pending = holdNextRead()
    await click(q('git-refresh'))
    const shared = refresh(t.w)
    await t.w.setProps({ active: false }); await t.w.setProps({ active: true }); await flushPromises()
    expect(reads()).toHaveLength(4)
    pending.resolve(response(FLOW_B.groupId)); await shared; await flushPromises()
    await refresh(t.w); expect(reads()).toHaveLength(5)
    await t.w.setProps({ active: false }); await t.w.setProps({ active: true }); await flushPromises()
    expect(reads()).toHaveLength(6)
    expect(settings).not.toHaveBeenCalled()
    expect(reads().map(([url]) => url)).toEqual([G, ...Array(5).fill(FLOW_B.groupId)].map(g => `/api/v1/groups/${g}/work-base`))
  })
})
