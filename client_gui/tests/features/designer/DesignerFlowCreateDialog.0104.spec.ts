/** D0104 §7.2 (creation only) and §3.5.3: real dialog/store/adapter, mocked transport. */
import { mount, flushPromises, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import { setCurrentUser } from '@/core/auth/currentUser'
import { useProjectStore } from '@/features/project'
import DesignerFlowCreateDialog from '@/features/designer/fileMenu/DesignerFlowCreateDialog.vue'
import { createFlow, fetchWorkBaseOptions } from '@/features/project/api'

const { get, post } = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }))
vi.mock('@shared/api', () => ({
  default: { get },
  getRequest: get,
  postRequest: post,
  extractApiErrorMessage: (_error: unknown, fallback: string) => fallback,
}))
registerAppMessages(i18n)
function deferred<T = any>() {
  let resolve!: (value: T) => void
  let reject!: (error: unknown) => void
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no })
  return { promise, resolve, reject }
}
const options = (names = ['main', 'feature'], base: string | null = 'main', enabled = true) =>
  ({ data: { git_enabled: enabled, base_branch: base, branches: names.map(name => ({ name, is_project_base: name === base })) } })
const tree = (pid: string) => ({ data: { data: { nodes: [
  { id: `project:${pid}`, parent_id: null, node_type: 'project', label: pid },
  { id: `module:${pid}:m`, parent_id: `project:${pid}`, node_type: 'module', label: 'm' },
] } } })
const account = (username = 'alice') => setCurrentUser({ username, email: null, isAdmin: false })
let wrappers: VueWrapper[] = []
let store: ReturnType<typeof useProjectStore>
beforeEach(() => {
  localStorage.clear()
  sessionStorage.clear()
  setActivePinia(createPinia())
  account()
  i18n.global.locale.value = 'ko'
  get.mockReset().mockImplementation(async (url: string) => url.endsWith('/groups/tree') ? tree(url.split('/')[4]) : options())
  post.mockReset().mockResolvedValue({ data: { group_id: 'p.m.1', work_base_ref: 'feature', effective_work_base_ref: 'feature' } })
  store = useProjectStore()
})
afterEach(() => {
  wrappers.forEach(w => w.unmount())
  wrappers = []
  store.$dispose()
  setCurrentUser(null)
  document.body.innerHTML = ''
})
function open(props: Record<string, unknown> = {}) {
  const w = mount(DesignerFlowCreateDialog, { props: { open: true, projectId: 'p', ...props }, global: { plugins: [i18n] }, attachTo: document.body })
  wrappers.push(w)
  return w
}
const el = <T extends HTMLElement = HTMLElement>(name: string) => document.querySelector<T>(`[data-test="designer-flow-create-${name}"]`)!
const disabled = () => el<HTMLButtonElement>('submit').disabled
async function input(name: string, value: string) {
  const node = el<HTMLInputElement>(name)
  node.value = value
  node.dispatchEvent(new Event(name === 'title' ? 'input' : 'change', { bubbles: true }))
  await flushPromises()
}
async function click(name: string) { el(name).click(); await flushPromises() }
const optionReads = () => get.mock.calls.filter(([url]) => url.endsWith('/git/work-base-options'))

describe('creation states and independent reads', () => {
  it('blocks unknown and loading, and enables branch selection before the tree completes', async () => {
    const c = deferred(), g = deferred()
    get.mockImplementation((url: string) => url.endsWith('/groups/tree') ? g.promise : c.promise)
    open()
    expect(get).toHaveBeenCalledTimes(2)
    expect(disabled()).toBe(true)
    await input('title', 'Flow')
    el('submit').click()
    expect(post).not.toHaveBeenCalled()
    c.resolve(options())
    await flushPromises()
    expect(el<HTMLSelectElement>('base').disabled).toBe(false)
    expect(el<HTMLSelectElement>('group').disabled).toBe(true)
    await input('base', 'feature')
    g.resolve(tree('p'))
    await flushPromises()
    await click('submit')
    expect(post).toHaveBeenCalledWith('/api/v1/groups', expect.objectContaining({ work_base_ref: 'feature', title: 'Flow', module: 'm' }))
  })

  it('keeps the selector visible for unknown/error; hides it only after explicit disabled', async () => {
    const c = deferred()
    get.mockImplementation((url: string) => url.endsWith('/groups/tree') ? Promise.resolve(tree('p')) : c.promise)
    const w = open({ projectId: null })
    expect(disabled()).toBe(true)
    expect(el('base-field')).not.toBeNull()
    await w.setProps({ projectId: 'p' })
    c.reject({ response: { status: 403 } })
    await flushPromises()
    expect(el('base-error').textContent).toContain('베이스 브랜치를 불러오지 못했습니다.')
    expect(disabled()).toBe(true)
    get.mockResolvedValue(options([], null, false))
    await click('base-retry')
    expect(el('base-field')).toBeNull()
    await input('title', 'No Git')
    await click('submit')
    expect(post.mock.calls[0][1]).not.toHaveProperty('work_base_ref')
  })

  it.each([null, 'deleted'])('does not choose a missing default (%s)', async base => {
    get.mockImplementation(async (url: string) => url.endsWith('/groups/tree') ? tree('p') : options(['feature'], base))
    open()
    await flushPromises()
    await input('title', 'Flow')
    expect(disabled()).toBe(true)
    expect(el<HTMLSelectElement>('base').value).toBe('')
    await input('base', 'feature')
    expect(disabled()).toBe(false)
  })

  it('preserves input through a failed refresh and icon retry; requires selection after removal', async () => {
    open()
    await flushPromises()
    await input('title', 'Keep me')
    await input('base', 'feature')
    expect(el('base-retry')).toBeNull()
    get.mockImplementation(async (url: string) => {
      if (url.endsWith('/groups/tree')) return tree('p')
      throw new Error('temporary lookup failure')
    })
    // A permission refresh revalidates options; ready state has no retry button.
    account()
    await flushPromises()
    expect(el('base-error')).not.toBeNull()
    get.mockImplementation(async (url: string) => url.endsWith('/groups/tree') ? tree('p') : options())
    await click('base-retry')
    expect(el<HTMLSelectElement>('base').value).toBe('feature')
    expect(el<HTMLInputElement>('title').value).toBe('Keep me')
    expect(el<HTMLSelectElement>('group').value).toBe('module:p:m')
    expect(el('base-retry')).toBeNull()
    get.mockImplementation(async (url: string) => url.endsWith('/groups/tree') ? tree('p') : options(['main']))
    account()
    await flushPromises()
    expect(el<HTMLSelectElement>('base').value).toBe('')
    expect(disabled()).toBe(true)
  })

  it('limits rapid submits to one and never replaces a rejected ref with the default', async () => {
    const submit = deferred()
    post.mockReturnValue(submit.promise)
    open()
    await flushPromises()
    await input('title', 'Flow')
    await input('base', 'feature')
    el('submit').click(); el('submit').click()
    expect(post).toHaveBeenCalledTimes(1)
    submit.reject({ response: { status: 409, data: { detail: { code: 'group_work_base_internal', message: 'Owned branch' } } } })
    await flushPromises()
    expect(el('error')).not.toBeNull()
    expect(el<HTMLSelectElement>('base').value).toBe('feature')
    expect(post).toHaveBeenCalledTimes(1)
  })

  it('ignores a previous project response and preserves a still-valid selection on project switch', async () => {
    const slow = deferred()
    get.mockImplementation((url: string) => url.endsWith('/groups/tree') ? Promise.resolve(tree(url.split('/')[4])) : url.includes('/p/') ? slow.promise : Promise.resolve(options(['other'], 'other')))
    const w = open()
    await w.setProps({ projectId: 'q' })
    await flushPromises()
    slow.resolve(options())
    await flushPromises()
    expect(el<HTMLSelectElement>('base').value).toBe('other')
    expect(el<HTMLSelectElement>('group').value).toBe('module:q:m')
    get.mockResolvedValue(options(['other', 'main'], 'main'))
    await w.setProps({ projectId: 'r' })
    await flushPromises()
    expect(el<HTMLSelectElement>('base').value).toBe('other')
  })

  it('close/reopen ignores old create success/error and refreshes options for the new session', async () => {
    const pending = deferred()
    post.mockReturnValue(pending.promise)
    const w = open()
    await flushPromises()
    await input('title', 'Old')
    await click('submit')
    await w.setProps({ open: false })
    await w.setProps({ open: true })
    await flushPromises()
    pending.resolve({ data: { group_id: 'old' } })
    await flushPromises()
    expect(w.emitted('created')).toBeUndefined()
    expect(el<HTMLInputElement>('title').value).toBe('')
    expect(optionReads()).toHaveLength(2)
  })

  it('a closed session never applies its delayed options or tree error', async () => {
    const c = deferred(), g = deferred()
    get.mockImplementation((url: string) => url.endsWith('/groups/tree') ? g.promise : c.promise)
    const w = open()
    await w.setProps({ open: false })
    c.resolve(options())
    g.reject(new Error('old tree error'))
    await flushPromises()
    get.mockImplementation(async (url: string) => url.endsWith('/groups/tree') ? tree('p') : options(['new'], 'new'))
    await w.setProps({ open: true })
    await flushPromises()
    expect(el<HTMLSelectElement>('base').value).toBe('new')
    expect(el('error')).toBeNull()
  })

  it.each(['en', 'ja'])('renders the added branch keys in %s', async locale => {
    i18n.global.locale.value = locale
    open()
    await flushPromises()
    expect(el('base-field').textContent).toContain(locale === 'en' ? 'Base branch' : 'ベースブランチ')
  })
})

describe('T0014 compact dialog and accessible lookup feedback', () => {
  it('uses the shared modal with three labeled fields, branch SVG and only cancel/create in its footer', async () => {
    open()
    await flushPromises()
    const surface = document.querySelector('.dfm-create-dialog')!
    expect(surface.getAttribute('role')).toBe('dialog')
    expect(surface.getAttribute('aria-modal')).toBe('true')
    expect(surface.querySelector('h2')?.textContent).toBe('새 플로우')
    expect([...surface.querySelectorAll('label')].map(label => label.textContent)).toEqual(['그룹', '이름', '베이스 브랜치'])
    expect(surface.querySelector('.dfm-branch-icon')?.getAttribute('aria-hidden')).toBe('true')
    expect(surface.querySelector('.dfm-branch-control .dfm-chevron')).not.toBeNull()
    expect([...surface.querySelectorAll('.dfm-actions button')].map(button => button.textContent?.trim())).toEqual(['취소', '생성'])
    expect(el('base-retry')).toBeNull()
    expect(el('base-loading')).toBeNull()
    expect(surface.textContent).not.toContain('시안 상태')
    expect(surface.querySelector('.demo, .toast, .reopen')).toBeNull()
  })

  it.each([
    ['ko', '브랜치 목록 다시 불러오기', '브랜치 목록 불러오는 중', '베이스 브랜치를 불러오지 못했습니다.'],
    ['en', 'Reload branches', 'Loading branches', 'Could not load base branches.'],
    ['ja', 'ブランチ一覧を再読み込み', 'ブランチ一覧を読み込み中', 'ベースブランチを読み込めませんでした。'],
  ])('shows a localized icon retry and spinner, never raw diagnostics (%s)', async (locale, retry, loading, failure) => {
    i18n.global.locale.value = locale
    const first = deferred(), second = deferred()
    const raw = 'Traceback: C:/private/checkout PermissionError stack details'
    get.mockImplementation((url: string) => url.endsWith('/groups/tree') ? Promise.resolve(tree('p')) : first.promise)
    open()
    await input('title', 'Preserved')
    expect(el('base-loading').getAttribute('role')).toBe('status')
    expect(el('base-loading').getAttribute('aria-label')).toBe(loading)
    expect(el('base-retry')).toBeNull()
    expect(document.querySelector('.dfm-branch-control .dfm-chevron')).toBeNull()
    expect(el('base').closest('.dfm-control')?.getAttribute('aria-busy')).toBe('true')
    document.querySelector('form[data-test="designer-flow-create"]')!.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))
    expect(post).not.toHaveBeenCalled()
    first.reject({ response: { status: 500, data: { detail: raw } } })
    await flushPromises()
    expect(el('base-loading')).toBeNull()
    expect(el('base-error').tagName).toBe('P')
    expect(el('base-error').textContent).toBe(failure)
    expect(el('base-error').getAttribute('role')).toBe('status')
    expect(el('base-error').getAttribute('aria-live')).toBe('polite')
    expect(el('base').getAttribute('aria-describedby')).toBe(el('base-error').id)
    expect(el('base').getAttribute('aria-invalid')).toBe('true')
    expect(el('base-retry').parentElement).toBe(el('base').parentElement)
    expect(el('base-retry').getAttribute('aria-label')).toBe(retry)
    expect(el('base-retry').getAttribute('title')).toBe(retry)
    expect(el('base-retry').textContent?.trim()).toBe('')
    expect(el('base-retry').querySelector('svg')).not.toBeNull()
    expect(document.querySelector('.dfm-create-dialog')!.textContent).not.toContain(raw)
    expect(el('base-field').querySelector('[role="alert"]')).toBeNull()
    await click('submit')
    expect(post).not.toHaveBeenCalled()
    get.mockImplementation((url: string) => url.endsWith('/groups/tree') ? Promise.resolve(tree('p')) : second.promise)
    const retryButton = el('base-retry')
    retryButton.click()
    retryButton.click()
    await flushPromises()
    expect(optionReads()).toHaveLength(2)
    expect(el('base-retry')).toBeNull()
    expect(el('base-loading')).not.toBeNull()
    expect(disabled()).toBe(true)
    expect(el<HTMLInputElement>('title').value).toBe('Preserved')
    second.resolve(options())
    await flushPromises()
    expect(el('base-loading')).toBeNull()
    expect(el('base-error')).toBeNull()
    expect(el('base').getAttribute('aria-describedby')).toBeNull()
    expect(el('base').getAttribute('aria-invalid')).toBeNull()
    expect(disabled()).toBe(false)
    await input('base', 'feature')
    await click('submit')
    expect(post).toHaveBeenCalledWith('/api/v1/groups', expect.objectContaining({ title: 'Preserved', work_base_ref: 'feature' }))
  })

  it.each(['ready', 'error', 'moved'])('restores retry focus after %s without stealing another input focus', async outcome => {
    get.mockImplementation(async (url: string) => {
      if (url.endsWith('/groups/tree')) return tree('p')
      throw new Error('temporary failure')
    })
    open()
    await flushPromises()
    const next = deferred()
    get.mockImplementation((url: string) => url.endsWith('/groups/tree') ? Promise.resolve(tree('p')) : next.promise)
    el('base-retry').focus()
    await click('base-retry')
    if (outcome === 'moved') el('title').focus()
    if (outcome === 'error') next.reject(new Error('still unavailable'))
    else next.resolve(options())
    await flushPromises()
    expect(document.activeElement).toBe(el(outcome === 'moved' ? 'title' : outcome === 'error' ? 'base-retry' : 'base'))
  })

  it('keeps initial focus, Tab wrapping, Escape, close/cancel and focus return', async () => {
    const opener = document.createElement('button')
    document.body.appendChild(opener)
    opener.focus()
    const w = open()
    await flushPromises()
    expect(document.activeElement).toBe(el('title'))
    el('submit').focus()
    el('submit').dispatchEvent(new KeyboardEvent('keydown', { key: 'Tab', bubbles: true, cancelable: true }))
    expect(document.activeElement).toBe(el('close'))
    el('close').dispatchEvent(new KeyboardEvent('keydown', { key: 'Tab', shiftKey: true, bubbles: true, cancelable: true }))
    expect(document.activeElement).toBe(el('submit'))
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
    await click('close')
    await click('cancel')
    expect(w.emitted('close')).toHaveLength(3)
    await w.setProps({ open: false })
    await flushPromises()
    expect(document.activeElement).toBe(opener)
  })

  it('ignores an older project failure without replacing the current branch or feedback', async () => {
    const old = deferred()
    get.mockImplementation((url: string) => url.endsWith('/groups/tree') ? Promise.resolve(tree(url.split('/')[4]))
      : url.includes('/p/') ? old.promise : Promise.resolve(options(['other'], 'other')))
    const w = open()
    await w.setProps({ projectId: 'q' })
    await flushPromises()
    old.reject(new Error('old project failed'))
    await flushPromises()
    expect(el<HTMLSelectElement>('base').value).toBe('other')
    expect(el('base-error')).toBeNull()
    expect(el('base-retry')).toBeNull()
    expect(disabled()).toBe(false)
  })
})

describe('option sharing and adapter contract', () => {
  it('coalesces concurrent reads, reuses success, separates projects and force-revalidates', async () => {
    const c = deferred()
    get.mockReturnValue(c.promise)
    const one = store.loadWorkBaseOptions('p'), two = store.loadWorkBaseOptions('p')
    expect(optionReads()).toHaveLength(1)
    c.resolve(options())
    expect(await one).toEqual(await two)
    await store.loadWorkBaseOptions('p')
    expect(optionReads()).toHaveLength(1)
    await store.loadWorkBaseOptions('q')
    await store.loadWorkBaseOptions('p', { force: true })
    expect(optionReads()).toHaveLength(3)
    expect(localStorage.length).toBe(0)
    expect(sessionStorage.length).toBe(0)
  })

  it('invalidates pending reads without allowing their late success to replace the new cache', async () => {
    const old = deferred(), fresh = deferred()
    get.mockReturnValueOnce(old.promise).mockReturnValueOnce(fresh.promise)
    const a = store.loadWorkBaseOptions('p').catch(() => 'stale')
    store.invalidateWorkBaseOptions('p')
    const b = store.loadWorkBaseOptions('p')
    fresh.resolve(options(['new'], 'new'))
    await b
    old.resolve(options())
    expect(await a).toBe('stale')
    expect((await store.loadWorkBaseOptions('p')).baseBranch).toBe('new')
    expect(optionReads()).toHaveLength(2)
  })

  it('drops pending/cache on logout, account switch and permission refresh', async () => {
    const old = deferred()
    get.mockReturnValueOnce(old.promise).mockResolvedValue(options(['bob'], 'bob'))
    const a = store.loadWorkBaseOptions('p').catch(() => 'stale')
    setCurrentUser(null)
    account('bob')
    await store.loadWorkBaseOptions('p')
    old.resolve(options())
    expect(await a).toBe('stale')
    account('bob') // new auth/me object: permission/admin refresh
    await store.loadWorkBaseOptions('p')
    expect(optionReads()).toHaveLength(3)
  })

  it('does not cache errors or accept malformed Git-disabled responses', async () => {
    get.mockRejectedValueOnce({ response: { status: 403 } }).mockResolvedValueOnce({ data: { branches: [] } }).mockResolvedValue(options())
    await expect(store.loadWorkBaseOptions('p')).rejects.toThrow()
    await expect(store.loadWorkBaseOptions('p')).rejects.toThrow()
    expect((await store.loadWorkBaseOptions('p')).gitEnabled).toBe(true)
    expect(optionReads()).toHaveLength(3)
  })

  it('encodes the project, preserves exact selected ref and maps both returned refs', async () => {
    await fetchWorkBaseOptions('p/a')
    expect(get).toHaveBeenCalledWith('/api/v1/projects/p%2Fa/git/work-base-options')
    expect(await createFlow({ projectId: 'p', moduleName: 'm', title: 'Flow', workBaseRef: 'feature' })).toEqual({
      id: 'p.m.1', title: 'Flow', workBaseRef: 'feature', effectiveWorkBaseRef: 'feature',
    })
    await createFlow({ projectId: 'p', moduleName: 'm', title: 'legacy' })
    expect(post.mock.calls[1][1]).not.toHaveProperty('work_base_ref')
  })

  it('invalidates options after successful creation and keeps failed creation cache', async () => {
    await store.loadWorkBaseOptions('p')
    post.mockRejectedValueOnce(new Error('failed'))
    await expect(store.createFlow({ projectId: 'p', moduleName: 'm', title: 'Flow' })).rejects.toThrow()
    await store.loadWorkBaseOptions('p')
    expect(optionReads()).toHaveLength(1)
    await store.createFlow({ projectId: 'p', moduleName: 'm', title: 'Flow' })
    await store.loadWorkBaseOptions('p')
    expect(optionReads()).toHaveLength(2)
  })
})
