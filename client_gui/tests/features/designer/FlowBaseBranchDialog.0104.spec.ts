/** D0104 §7.2: change dialog only. Real menu connector, dialog, Pinia store and API adapter. */
import { mount, flushPromises, type VueWrapper } from '@vue/test-utils'
import { defineComponent, h, reactive, ref } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import { setCurrentUser } from '@/core/auth/currentUser'
import { useProjectStore } from '@/features/project'
import { fetchFlowWorkBase, updateFlowWorkBase } from '@/features/project/api'
import FlowBaseBranchDialog from '@/features/designer/fileMenu/FlowBaseBranchDialog.vue'
import DesignerContextMenu from '@/features/designer/contextMenu/DesignerContextMenu.vue'
import { useDesignerContextMenu } from '@/features/designer/contextMenu/attach/designerMenuConnector'
import { projectTreeMenu } from '@/features/designer/contextMenu/projectTreeMenu'
import { contextMenuMessages } from '@/features/designer/contextMenu/messages'

const { get, patch } = vi.hoisted(() => ({ get: vi.fn(), patch: vi.fn() }))
vi.mock('@shared/api', () => ({
  default: { get }, getRequest: get, patchRequest: patch,
  extractApiErrorMessage: (_error: unknown, fallback: string) => fallback,
}))
registerAppMessages(i18n)
function deferred<T = any>() {
  let resolve!: (value: T) => void
  let reject!: (error: unknown) => void
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no })
  return { promise, resolve, reject }
}
const view = (overrides = {}) => ({ data: {
  group_id: 'p.m.1', project_id: 'p', git_enabled: true,
  work_base_ref: 'main', effective_work_base_ref: 'main', work_base_locked: false,
  lock_evidence: [], work_base_editable: true, reason: null, can_edit: true, ...overrides,
} })
const candidates = () => ({ data: { git_enabled: true, base_branch: 'main',
  branches: ['main', 'feature'].map(name => ({ name, is_project_base: name === 'main' })) } })
const error = (status: number, code?: string) => ({ response: { status, data: {
  detail: code ? { code, message: 'diagnostic', details: {} } : 'Forbidden',
} } })
let wrappers: VueWrapper[] = []
let store: ReturnType<typeof useProjectStore>
beforeEach(() => {
  localStorage.clear(); sessionStorage.clear()
  setActivePinia(createPinia())
  setCurrentUser({ username: 'alice', email: null, isAdmin: false })
  i18n.global.locale.value = 'ko'
  get.mockReset().mockImplementation(async (url: string) => url.endsWith('/work-base') ? view() : candidates())
  patch.mockReset().mockResolvedValue({ data: { group_id: 'p.m.1' } })
  store = useProjectStore()
})
afterEach(() => {
  wrappers.forEach(w => w.unmount()); wrappers = []
  store.$dispose(); setCurrentUser(null); document.body.innerHTML = ''
})
function open(props = {}) {
  const w = mount(FlowBaseBranchDialog, { props: { open: true, projectId: 'p', groupId: 'p.m.1', ...props },
    global: { plugins: [i18n] }, attachTo: document.body })
  wrappers.push(w); return w
}
const el = <T extends HTMLElement = HTMLElement>(name: string) => document.querySelector<T>(`[data-test="flow-base-${name}"]`)
const state = () => document.querySelector('[data-test="flow-base"]')?.getAttribute('data-state')
async function choose(value = 'feature') {
  const select = el<HTMLSelectElement>('choice')!
  select.value = value; select.dispatchEvent(new Event('change', { bubbles: true })); await flushPromises()
}
async function click(name: string) { el(name)!.click(); await flushPromises() }
const optionReads = () => get.mock.calls.filter(([url]) => url.endsWith('/work-base-options'))

describe('authoritative read and four states', () => {
  it('only GETs the setting first, then enables candidates and blocks unchanged or empty save', async () => {
    const read = deferred(), opts = deferred()
    get.mockImplementation((url: string) => url.endsWith('/work-base') ? read.promise : opts.promise)
    open()
    expect(state()).toBe('loading'); expect(el('save')).toBeNull()
    expect(get).toHaveBeenCalledTimes(1)
    read.resolve(view()); await flushPromises()
    expect(optionReads()).toHaveLength(1); expect(el('save')).toBeNull()
    opts.resolve(candidates()); await flushPromises()
    expect(state()).toBe('editable'); expect(el<HTMLButtonElement>('save')!.disabled).toBe(true)
    await choose('main'); expect(el<HTMLButtonElement>('save')!.disabled).toBe(true)
    await choose(); expect(el<HTMLButtonElement>('save')!.disabled).toBe(false)
    expect(patch).not.toHaveBeenCalled() // no autosave
  })

  it.each([
    [null, false, '변경 권한이 없습니다'],
    ['disposed', true, '폐기된 Flow입니다'],
    ['git_disabled', true, 'Git을 사용하지 않습니다'],
    ['checkout_unavailable', true, '기준 체크아웃을 사용할 수 없습니다'],
    ['history_locked', true, '작업 이력이 있어 변경할 수 없습니다'],
    ['provision_busy', true, '준비 중'],
  ])('read-only reason %s and can_edit %s never reads options', async (reason, can_edit, text) => {
    get.mockResolvedValue(view({ reason, can_edit, git_enabled: reason !== 'git_disabled', work_base_editable: reason === null }))
    open(); await flushPromises()
    expect(state()).toBe('readonly'); expect(el('message')!.textContent).toBe(text)
    expect(el('current')!.textContent).toContain('main'); expect(el('save')).toBeNull()
    expect(optionReads()).toHaveLength(0)
    await new Promise(resolve => setTimeout(resolve, 15))
    expect(get).toHaveBeenCalledTimes(1) // provision_busy has no automatic polling
  })

  it.each([403, 500])('read failure %s shows error and explicit refresh', async status => {
    get.mockRejectedValueOnce(error(status))
    open(); await flushPromises()
    expect(state()).toBe('error'); expect(el('save')).toBeNull(); expect(optionReads()).toHaveLength(0)
    expect(el('message')!.textContent).toBe(status === 403 ? '조회 권한이 없습니다' : '현재 기준을 불러오지 못했습니다')
    await click('retry'); expect(state()).toBe('editable')
  })

  it.each([403, 500])('options failure %s keeps the current ref read-only', async status => {
    get.mockImplementation(async (url: string) => {
      if (url.endsWith('/work-base')) return view()
      throw error(status)
    })
    open(); await flushPromises()
    expect(state()).toBe('readonly'); expect(el('save')).toBeNull()
    expect(el('current')!.textContent).toContain('main')
    expect(el('message')!.textContent).toBe(status === 403 ? '후보 조회 권한 없음' : '후보를 불러오지 못했습니다')
    expect(el('retry')).not.toBeNull()
  })
})

describe('explicit optimistic save and invalidation', () => {
  it.each([null, 'main'])('sends exact stored expected %s, deduplicates clicks and invalidates only the target project', async original => {
    get.mockImplementation(async (url: string) => url.endsWith('/work-base') ? view({ work_base_ref: original }) : candidates())
    await store.loadWorkBaseOptions('other')
    const pending = deferred(); patch.mockReturnValue(pending.promise)
    const w = open(); await flushPromises(); await choose()
    const button = el('save')!; button.click(); button.click(); await flushPromises()
    expect(patch).toHaveBeenCalledTimes(1)
    expect(patch).toHaveBeenCalledWith('/api/v1/groups/p.m.1/work-base', {
      work_base_ref: 'feature', expected_work_base_ref: original,
    })
    pending.resolve({ data: {} }); await flushPromises()
    expect(w.emitted('saved')).toHaveLength(1); expect(w.emitted('close')).toHaveLength(1)
    const count = optionReads().length
    await store.loadWorkBaseOptions('other'); expect(optionReads()).toHaveLength(count)
    await store.loadWorkBaseOptions('p'); expect(optionReads()).toHaveLength(count + 1)
    await w.setProps({ open: false }); await w.setProps({ open: true }); await flushPromises()
    expect(get.mock.calls.filter(([url]) => url.endsWith('/work-base'))).toHaveLength(2)
  })

  it.each([
    ['group_disposed', '폐기된 Flow입니다'], ['group_work_base_git_disabled', 'Git을 사용하지 않습니다'],
    ['group_work_base_checkout_unavailable', '기준 체크아웃을 사용할 수 없습니다'],
    ['group_work_base_locked', '작업 이력이 있어 변경할 수 없습니다'],
    ['group_work_base_remote_only', '로컬 브랜치를 선택하세요'], ['group_work_base_internal', '내부 브랜치는 사용할 수 없습니다'],
    ['group_work_base_changed', '기준이 변경됐습니다. 재조회 후 다시 선택하세요'], ['group_work_base_busy', '준비 중'],
  ])('409 %s has its own message, refresh and requires a new choice', async (code, text) => {
    patch.mockRejectedValue(error(409, code))
    open(); await flushPromises(); await choose(); await click('save')
    expect(state()).toBe('readonly'); expect(el('message')!.textContent).toBe(text)
    expect(el('save')).toBeNull(); expect(get).toHaveBeenCalledTimes(2)
    await click('retry')
    expect(state()).toBe('editable'); expect(el<HTMLSelectElement>('choice')!.value).toBe('')
    expect(el<HTMLButtonElement>('save')!.disabled).toBe(true)
    expect(patch).toHaveBeenCalledTimes(1)
  })

  it('handles PATCH string 403 with no second save', async () => {
    patch.mockRejectedValue(error(403))
    open(); await flushPromises(); await choose(); await click('save')
    expect(state()).toBe('readonly'); expect(el('message')!.textContent).toBe('변경 권한이 없습니다')
    expect(el('save')).toBeNull()
  })

  it('failed save preserves caches and a 500 permits an explicit retry', async () => {
    patch.mockRejectedValue(error(500, 'group_work_base_update_failed'))
    open(); await flushPromises(); await choose(); await click('save')
    expect(el('message')!.textContent).toBe('저장하지 못했습니다'); expect(state()).toBe('editable')
    const count = optionReads().length; await store.loadWorkBaseOptions('p')
    expect(optionReads()).toHaveLength(count)
  })

  it('tree invalidation fences an older in-flight tree and drops both cached variants', async () => {
    const old = deferred()
    get.mockReturnValueOnce(old.promise)
    const before = store.fetchGroupTree('p')
    await store.saveFlowWorkBase('p', 'p.m.1', { workBaseRef: 'feature', expectedWorkBaseRef: 'main' })
    get.mockResolvedValue({ data: { data: { nodes: [] } } })
    await store.fetchGroupTree('p', { includeTerminal: true })
    old.resolve({ data: { data: { nodes: [{ id: 'stale', node_type: 'group', label: 'old' }] } } }); await before
    expect(store.getCachedGroupTree('p')).toBeUndefined()
    await store.fetchGroupTree('p')
    expect(store.getCachedGroupTree('p')).toEqual([])
    await store.saveFlowWorkBase('p', 'p.m.1', { workBaseRef: 'main', expectedWorkBaseRef: 'feature' })
    expect(store.getCachedGroupTree('p')).toBeUndefined()
    expect(store.getCachedGroupTree('p', true)).toBeUndefined()
  })
})

describe('scope generations', () => {
  it('ignores GET results after switching Flow and project', async () => {
    const old = deferred(); get.mockReturnValueOnce(old.promise)
    const w = open()
    get.mockImplementation(async (url: string) => url.endsWith('/work-base') ? view({ group_id: 'q.m.2', project_id: 'q', effective_work_base_ref: 'q-base' }) : candidates())
    await w.setProps({ groupId: 'q.m.2', projectId: 'q' }); await flushPromises()
    old.resolve(view()); await flushPromises()
    expect(el('current')!.textContent).toContain('q-base')
    expect(optionReads()).toHaveLength(1)
  })

  it('ignores candidates from a closed/reopened dialog', async () => {
    const old = deferred()
    get.mockImplementation((url: string) => url.endsWith('/work-base') ? Promise.resolve(view()) : old.promise)
    const w = open(); await flushPromises()
    await w.setProps({ open: false }); await w.setProps({ open: true, groupId: 'p.m.2' })
    // Wrong server group is an error and must never revive the old candidates.
    await flushPromises(); old.resolve(candidates()); await flushPromises()
    expect(state()).toBe('error'); expect(el('choice')).toBeNull()
  })

  it('late PATCH success does not close or publish saved for another Flow', async () => {
    const pending = deferred(); patch.mockReturnValue(pending.promise)
    const w = open(); await flushPromises(); await choose(); await click('save')
    get.mockImplementation(async (url: string) => url.endsWith('/work-base') ? view({ group_id: 'q.m.2', project_id: 'q' }) : candidates())
    await w.setProps({ groupId: 'q.m.2', projectId: 'q' }); await flushPromises()
    pending.resolve({ data: {} }); await flushPromises()
    expect(w.emitted('close')).toBeUndefined(); expect(w.emitted('saved')).toBeUndefined()
    expect(state()).toBe('editable')
  })

  it('permission/account refresh discards the old result and rechecks authority', async () => {
    const old = deferred(); get.mockReturnValueOnce(old.promise)
    open()
    get.mockResolvedValue(view({ can_edit: false }))
    setCurrentUser({ username: 'bob', email: null, isAdmin: false }); await flushPromises()
    old.resolve(view()); await flushPromises()
    expect(state()).toBe('readonly'); expect(optionReads()).toHaveLength(0)
  })
})

describe('menu and adapter contracts', () => {
  it.each([undefined, true, false])('menu hides only confirmed Git-disabled (%s), independent of client admin', enabled => {
    const menu = projectTreeMenu({ target: { kind: 'flow', projectId: 'p', groupId: 'module:p:m', flowId: 'p.m.1' },
      available: { currentProject: true, gitEnabled: enabled }, connected: ['flow.baseBranch'] })
    expect(menu.request('flow.baseBranch') !== null).toBe(enabled !== false)
  })

  it('the existing connector opens the right-clicked Flow through its host path, then rechecks Git state', async () => {
    const target = ref<string | null>(null)
    const git = ref<boolean | undefined>(undefined)
    const Harness = defineComponent({ setup() {
      const menu = useDesignerContextMenu({
        center: { state: reactive({ activeFlowId: null }) } as any, projectId: () => 'p',
        canSelectAll: () => false, selectAll: vi.fn(), openGroupCreate: vi.fn(), openFlowCreate: vi.fn(),
        selectPalette: vi.fn(), arrange: { groups: [], enabled: () => false, run: vi.fn() },
        projectGitEnabled: () => git.value, openFlowBaseBranch: (_pid, gid) => { target.value = gid },
      })
      return () => h('div', [
        h('button', { 'data-test': 'row', onContextmenu: (event: MouseEvent) => menu.api.project(event,
          { kind: 'flow', projectId: 'p', groupId: 'module:p:m', flowId: 'p.m.1' }) }, 'Flow'),
        h(DesignerContextMenu, { host: menu.host, onRequest: menu.run }),
        h(FlowBaseBranchDialog, { open: !!target.value, groupId: target.value, projectId: 'p', onClose: () => { target.value = null } }),
      ])
    } })
    const w = mount(Harness, { global: { plugins: [i18n] }, attachTo: document.body }); wrappers.push(w)
    await w.get('[data-test="row"]').trigger('contextmenu'); await flushPromises()
    const item = document.querySelector<HTMLButtonElement>('[data-key="flow.baseBranch"]')!
    expect(item.textContent).toContain('베이스 브랜치'); item.click(); await flushPromises()
    expect(get).toHaveBeenCalledWith('/api/v1/groups/p.m.1/work-base')
    expect(state()).toBe('editable')
    await click('cancel'); await w.get('[data-test="row"]').trigger('contextmenu'); await flushPromises()
    const stale = document.querySelector<HTMLButtonElement>('[data-key="flow.baseBranch"]')!
    git.value = false; stale.click(); await flushPromises()
    expect(target.value).toBeNull()
  })

  it('invalidating Git knowledge returns the menu to unknown and fences an older setting response', async () => {
    const pending = deferred(); get.mockReturnValueOnce(pending.promise)
    const read = store.loadFlowWorkBase('p.m.1')
    store.invalidateWorkBaseOptions('p')
    pending.resolve(view({ git_enabled: false })); await read
    expect(store.knownGitEnabled.p).toBeUndefined()
    get.mockResolvedValue(view({ git_enabled: false }))
    await store.loadFlowWorkBase('p.m.1')
    expect(store.knownGitEnabled.p).toBe(false)
    store.invalidateWorkBaseOptions('p')
    expect(store.knownGitEnabled.p).toBeUndefined()
  })

  it('encodes group id and preserves null without ever using the old /git/work-base API', async () => {
    get.mockResolvedValue(view({ group_id: 'p/m.1' }))
    expect((await fetchFlowWorkBase('p/m.1')).groupId).toBe('p/m.1')
    expect(get).toHaveBeenCalledWith('/api/v1/groups/p%2Fm.1/work-base')
    await updateFlowWorkBase('p/m.1', { workBaseRef: 'feature', expectedWorkBaseRef: null })
    expect(patch).toHaveBeenCalledWith('/api/v1/groups/p%2Fm.1/work-base', { work_base_ref: 'feature', expected_work_base_ref: null })
  })

  it.each(['ko', 'en', 'ja'] as const)('has localized dialog and menu keys in %s', async locale => {
    i18n.global.locale.value = locale
    open(); await flushPromises()
    expect(contextMenuMessages[locale].baseBranch).toBe(i18n.global.t('gui.flow_base.title'))
    for (const key of ['options_denied', 'changed', 'busy', 'locked', 'read_failed', 'save_failed']) {
      expect(i18n.global.t('gui.flow_base.' + key)).not.toBe('gui.flow_base.' + key)
    }
  })
})
