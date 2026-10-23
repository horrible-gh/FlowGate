import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { Project } from '@/features/project/types'
import { setTestLocale } from './projectI18n'

const fetchActiveProjects = vi.hoisted(() => vi.fn())
const createProjectApi = vi.hoisted(() => vi.fn())
const suggestProjectSlug = vi.hoisted(() => vi.fn())

vi.mock('@/features/project/api', () => ({
  fetchActiveProjects,
  createProject: createProjectApi,
  suggestProjectSlug,
}))

import ProjectCreateDialog from '@/features/project/ProjectCreateDialog.vue'

function project(id = 'new-project'): Project {
  return { id, name: 'New Project', description: '', color: '#2563eb', isActive: true, createdAt: null, updatedAt: null, modules: [] }
}

function flushPromises(): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, 0))
}

// Rendered content lives behind ModalShell's Teleport(to="body"), so — same convention
// as ModalShell.spec.ts/ConfirmDialog.spec.ts — DOM assertions and interactions look at
// `document`, not at the wrapper's own tree.
describe('features/project/ProjectCreateDialog', () => {
  beforeEach(() => {
    fetchActiveProjects.mockReset().mockResolvedValue([])
    createProjectApi.mockReset()
    suggestProjectSlug.mockReset().mockResolvedValue(null)
    localStorage.clear()
    setActivePinia(createPinia())
  })

  afterEach(() => {
    document.body.querySelectorAll('.ui-modal-overlay').forEach((el) => el.remove())
  })

  function mountDialog(props: Record<string, unknown> = {}) {
    return mount(ProjectCreateDialog, { props: { open: true, ...props }, attachTo: document.body })
  }

  function setInput(selector: string, value: string): void {
    const el = document.querySelector(selector) as HTMLInputElement | HTMLTextAreaElement
    el.value = value
    el.dispatchEvent(new Event('input'))
  }

  function click(selector: string): void {
    ;(document.querySelector(selector) as HTMLElement).dispatchEvent(new MouseEvent('click', { bubbles: true }))
  }

  it('only offers the four real create fields — no structure/storage/path/member inputs', () => {
    const wrapper = mountDialog()
    expect(document.querySelector('#pcd-name')).not.toBeNull()
    expect(document.querySelector('#pcd-id')).not.toBeNull()
    expect(document.querySelector('#pcd-desc')).not.toBeNull()
    expect(document.querySelector('.pcd-colors')).not.toBeNull()
    const bodyText = document.querySelector('.ui-modal-surface')?.textContent ?? ''
    expect(bodyText).not.toContain('구조')
    expect(bodyText).not.toContain('저장소')
    expect(bodyText).not.toContain('구성원')
    wrapper.unmount()
  })

  it('requires a name before submitting, and never calls the create API for an empty name', async () => {
    const wrapper = mountDialog()
    setInput('#pcd-id', 'demo')
    click('.pcd-actions .ui-btn--primary')
    await wrapper.vm.$nextTick()
    expect(document.querySelector('.pcd-field-error')?.textContent).toBe('이름을 입력하세요.')
    expect(createProjectApi).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('rejects an invalid id format and never calls the create API while it is invalid', async () => {
    const wrapper = mountDialog()
    setInput('#pcd-name', 'Demo')
    setInput('#pcd-id', 'Not Valid!')
    await wrapper.vm.$nextTick()

    expect(document.querySelector('.pcd-field-error')?.textContent).toBe('영문 소문자, 숫자, -, _ 만 사용할 수 있습니다.')
    click('.pcd-actions .ui-btn--primary')
    await wrapper.vm.$nextTick()
    expect(createProjectApi).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('name filled but id empty cannot submit, and clicking create never calls the API', async () => {
    const wrapper = mountDialog()
    setInput('#pcd-name', 'Demo')
    await wrapper.vm.$nextTick()
    click('.pcd-actions .ui-btn--primary')
    await wrapper.vm.$nextTick()
    expect(createProjectApi).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('shows a required-id error only after a submit attempt, distinct from the format error', async () => {
    const wrapper = mountDialog()
    setInput('#pcd-name', 'Demo')
    await wrapper.vm.$nextTick()
    // Not yet attempted -- no error shown for an empty id.
    expect(document.querySelector('.pcd-field-error')).toBeNull()

    click('.pcd-actions .ui-btn--primary')
    await wrapper.vm.$nextTick()
    expect(document.querySelector('.pcd-field-error')?.textContent).toBe('식별자를 입력하세요.')
    wrapper.unmount()
  })

  it('submits successfully once a valid id is filled in after an empty-id submit attempt', async () => {
    createProjectApi.mockResolvedValue(project('demo'))
    const wrapper = mountDialog()
    setInput('#pcd-name', 'Demo')
    await wrapper.vm.$nextTick()
    click('.pcd-actions .ui-btn--primary')
    await wrapper.vm.$nextTick()
    expect(createProjectApi).not.toHaveBeenCalled()

    setInput('#pcd-id', 'demo')
    await wrapper.vm.$nextTick()
    click('.pcd-actions .ui-btn--primary')
    await flushPromises()
    await wrapper.vm.$nextTick()

    expect(createProjectApi).toHaveBeenCalledWith(expect.objectContaining({ id: 'demo' }))
    expect(wrapper.emitted('created')?.[0]).toEqual([project('demo')])
    wrapper.unmount()
  })

  it('submits successfully once an auto/manual id suggestion fills in a valid id', async () => {
    vi.useFakeTimers()
    createProjectApi.mockResolvedValue(project('demo-suggested'))
    suggestProjectSlug.mockResolvedValue('demo-suggested')
    const wrapper = mountDialog()
    setInput('#pcd-name', 'Demo')
    await wrapper.vm.$nextTick()

    await vi.advanceTimersByTimeAsync(400)
    await wrapper.vm.$nextTick()
    expect((document.querySelector('#pcd-id') as HTMLInputElement).value).toBe('demo-suggested')

    vi.useRealTimers()
    click('.pcd-actions .ui-btn--primary')
    await flushPromises()
    await wrapper.vm.$nextTick()

    expect(createProjectApi).toHaveBeenCalledWith(expect.objectContaining({ id: 'demo-suggested' }))
    expect(wrapper.emitted('created')?.[0]).toEqual([project('demo-suggested')])
    wrapper.unmount()
  })

  it('auto-suggests an id from the name after a debounce, while the id field has not been touched', async () => {
    vi.useFakeTimers()
    suggestProjectSlug.mockResolvedValue('demo-suggested')
    const wrapper = mountDialog()
    setInput('#pcd-name', 'Demo')
    await wrapper.vm.$nextTick()

    await vi.advanceTimersByTimeAsync(400)
    await wrapper.vm.$nextTick()

    expect(suggestProjectSlug).toHaveBeenCalledWith('Demo')
    expect((document.querySelector('#pcd-id') as HTMLInputElement).value).toBe('demo-suggested')
    wrapper.unmount()
    vi.useRealTimers()
  })

  it('stops auto-suggesting once the id field has been edited by hand', async () => {
    vi.useFakeTimers()
    suggestProjectSlug.mockResolvedValue('should-not-apply')
    const wrapper = mountDialog()
    setInput('#pcd-id', 'manual-id')
    setInput('#pcd-name', 'Demo')
    await wrapper.vm.$nextTick()

    await vi.advanceTimersByTimeAsync(400)
    await wrapper.vm.$nextTick()

    expect((document.querySelector('#pcd-id') as HTMLInputElement).value).toBe('manual-id')
    wrapper.unmount()
    vi.useRealTimers()
  })

  it('re-enables auto-suggestion when the 제안 button is used explicitly', async () => {
    suggestProjectSlug.mockResolvedValue('re-suggested')
    const wrapper = mountDialog()
    setInput('#pcd-id', 'manual-id')
    setInput('#pcd-name', 'Demo')
    await wrapper.vm.$nextTick()

    click('.pcd-id-row .ui-btn')
    await flushPromises()
    await wrapper.vm.$nextTick()

    expect((document.querySelector('#pcd-id') as HTMLInputElement).value).toBe('re-suggested')
    wrapper.unmount()
  })

  it('a pending auto-suggest debounce timer from a closed session never fires or fills the id after reopening', async () => {
    vi.useFakeTimers()
    const wrapper = mountDialog()
    setInput('#pcd-name', 'Alpha')
    await wrapper.vm.$nextTick()

    // Close and reopen (a fresh create session) before the 400ms debounce ever fires.
    await wrapper.setProps({ open: false })
    await wrapper.setProps({ open: true })
    await wrapper.vm.$nextTick()

    await vi.advanceTimersByTimeAsync(500)
    await wrapper.vm.$nextTick()

    expect(suggestProjectSlug).not.toHaveBeenCalled()
    expect((document.querySelector('#pcd-id') as HTMLInputElement).value).toBe('')
    wrapper.unmount()
    vi.useRealTimers()
  })

  it('an auto-suggest request already in flight before close/reopen never fills the id of the new session once it resolves late', async () => {
    vi.useFakeTimers()
    let resolveFirst: (value: string | null) => void = () => {}
    suggestProjectSlug.mockImplementationOnce(() => new Promise<string | null>((resolve) => { resolveFirst = resolve }))
    const wrapper = mountDialog()
    setInput('#pcd-name', 'Alpha')
    await wrapper.vm.$nextTick()
    await vi.advanceTimersByTimeAsync(400)
    expect(suggestProjectSlug).toHaveBeenCalledTimes(1)

    await wrapper.setProps({ open: false })
    await wrapper.setProps({ open: true })
    setInput('#pcd-name', 'Beta')
    await wrapper.vm.$nextTick()

    resolveFirst('alpha-slug')
    await vi.advanceTimersByTimeAsync(0)
    await wrapper.vm.$nextTick()

    expect((document.querySelector('#pcd-id') as HTMLInputElement).value).toBe('')
    wrapper.unmount()
    vi.useRealTimers()
  })

  it('a slower stale suggestion response for an older name never overwrites a newer name\'s already-applied result', async () => {
    vi.useFakeTimers()
    let resolveAlpha: (value: string | null) => void = () => {}
    suggestProjectSlug
      .mockImplementationOnce(() => new Promise<string | null>((resolve) => { resolveAlpha = resolve }))
      .mockResolvedValueOnce('beta-slug')
    const wrapper = mountDialog()

    setInput('#pcd-name', 'Alpha')
    await wrapper.vm.$nextTick()
    await vi.advanceTimersByTimeAsync(400) // Alpha's request is now in flight, unresolved

    setInput('#pcd-name', 'Beta')
    await wrapper.vm.$nextTick()
    await vi.advanceTimersByTimeAsync(400) // Beta's request fires and resolves
    await wrapper.vm.$nextTick()
    expect((document.querySelector('#pcd-id') as HTMLInputElement).value).toBe('beta-slug')

    // Alpha's older, slower request resolves after Beta's newer one already applied.
    resolveAlpha('alpha-slug')
    await vi.advanceTimersByTimeAsync(0)
    await wrapper.vm.$nextTick()

    expect((document.querySelector('#pcd-id') as HTMLInputElement).value).toBe('beta-slug')
    wrapper.unmount()
    vi.useRealTimers()
  })

  it('the 제안 button click supersedes a slower in-flight auto-suggest for an older name', async () => {
    vi.useFakeTimers()
    let resolveAuto: (value: string | null) => void = () => {}
    suggestProjectSlug
      .mockImplementationOnce(() => new Promise<string | null>((resolve) => { resolveAuto = resolve }))
      .mockResolvedValueOnce('manual-slug')
    const wrapper = mountDialog()

    setInput('#pcd-name', 'Alpha')
    await wrapper.vm.$nextTick()
    await vi.advanceTimersByTimeAsync(400) // auto-suggest for "Alpha" now in flight, unresolved

    click('.pcd-id-row .ui-btn')
    await vi.advanceTimersByTimeAsync(0)
    await wrapper.vm.$nextTick()
    expect((document.querySelector('#pcd-id') as HTMLInputElement).value).toBe('manual-slug')

    resolveAuto('auto-slug')
    await vi.advanceTimersByTimeAsync(0)
    await wrapper.vm.$nextTick()
    expect((document.querySelector('#pcd-id') as HTMLInputElement).value).toBe('manual-slug')
    wrapper.unmount()
    vi.useRealTimers()
  })

  it('releases the 제안 button loading state when a name change supersedes an in-flight manual suggest', async () => {
    vi.useFakeTimers()
    let resolveManual: (value: string | null) => void = () => {}
    suggestProjectSlug
      .mockImplementationOnce(() => new Promise<string | null>((resolve) => { resolveManual = resolve }))
      .mockResolvedValueOnce('auto-slug')
    const wrapper = mountDialog()

    setInput('#pcd-name', 'Alpha')
    await wrapper.vm.$nextTick()
    click('.pcd-id-row .ui-btn')
    await wrapper.vm.$nextTick()
    expect((document.querySelector('.pcd-id-row .ui-btn') as HTMLButtonElement).disabled).toBe(true)

    // Changing the name while the manual "제안" request is still in flight cancels it (its
    // ticket is no longer the latest) -- the button must not stay stuck disabled waiting for
    // a response that will never apply.
    setInput('#pcd-name', 'Beta')
    await wrapper.vm.$nextTick()
    expect((document.querySelector('.pcd-id-row .ui-btn') as HTMLButtonElement).disabled).toBe(false)

    // The latest auto-suggest (from the name change) finishing afterwards must not
    // re-enable a stuck state either -- it was already released.
    await vi.advanceTimersByTimeAsync(400)
    await wrapper.vm.$nextTick()
    expect((document.querySelector('.pcd-id-row .ui-btn') as HTMLButtonElement).disabled).toBe(false)

    // The stale manual request resolving even later must not resurrect the loading state.
    resolveManual('alpha-slug')
    await vi.advanceTimersByTimeAsync(0)
    await wrapper.vm.$nextTick()
    expect((document.querySelector('.pcd-id-row .ui-btn') as HTMLButtonElement).disabled).toBe(false)

    wrapper.unmount()
    vi.useRealTimers()
  })

  it('a direct id edit while a manual 제안 request is in flight cancels that request instead of letting its late response overwrite the id', async () => {
    let resolveManual: (value: string | null) => void = () => {}
    suggestProjectSlug.mockImplementationOnce(() => new Promise<string | null>((resolve) => { resolveManual = resolve }))
    const wrapper = mountDialog()

    setInput('#pcd-name', 'Alpha')
    await wrapper.vm.$nextTick()
    click('.pcd-id-row .ui-btn') // manual 제안 for "Alpha" now in flight
    await wrapper.vm.$nextTick()

    // Typing directly into the id field while that request is still pending must win --
    // the pending ticket is cancelled right here, not left to be raced against later.
    setInput('#pcd-id', 'manual-id')
    await wrapper.vm.$nextTick()

    resolveManual('alpha-slug')
    await flushPromises()
    await wrapper.vm.$nextTick()

    expect((document.querySelector('#pcd-id') as HTMLInputElement).value).toBe('manual-id')
    wrapper.unmount()
  })

  it('touching id, then clicking 제안, then changing the name cancels the pending manual suggestion instead of applying its stale slug to the new name', async () => {
    let resolveManual: (value: string | null) => void = () => {}
    suggestProjectSlug.mockImplementationOnce(() => new Promise<string | null>((resolve) => { resolveManual = resolve }))
    const wrapper = mountDialog()

    setInput('#pcd-id', 'manual-id')
    setInput('#pcd-name', 'Alpha')
    await wrapper.vm.$nextTick()

    click('.pcd-id-row .ui-btn') // manual 제안 for "Alpha" now in flight, id already touched
    await wrapper.vm.$nextTick()
    expect((document.querySelector('.pcd-id-row .ui-btn') as HTMLButtonElement).disabled).toBe(true)

    // Changing the name must invalidate the in-flight manual request even though the id
    // field is already touched -- watch(name) must not return early on the touched check
    // before cancelling it.
    setInput('#pcd-name', 'Beta')
    await wrapper.vm.$nextTick()
    expect((document.querySelector('.pcd-id-row .ui-btn') as HTMLButtonElement).disabled).toBe(false)

    resolveManual('alpha-slug')
    await flushPromises()
    await wrapper.vm.$nextTick()

    expect((document.querySelector('#pcd-id') as HTMLInputElement).value).toBe('manual-id')
    wrapper.unmount()
  })

  it('prevents duplicate submits while a create is already in flight', async () => {
    let resolveCreate: (value: Project) => void = () => {}
    createProjectApi.mockImplementation(() => new Promise<Project>((resolve) => { resolveCreate = resolve }))
    const wrapper = mountDialog()
    setInput('#pcd-name', 'Demo')
    setInput('#pcd-id', 'demo')
    await wrapper.vm.$nextTick()

    click('.pcd-actions .ui-btn--primary')
    click('.pcd-actions .ui-btn--primary')
    click('.pcd-actions .ui-btn--primary')
    await wrapper.vm.$nextTick()
    expect(createProjectApi).toHaveBeenCalledTimes(1)

    resolveCreate(project())
    await flushPromises()
    wrapper.unmount()
  })

  it('on success: emits created with the new Project and never emits close itself', async () => {
    createProjectApi.mockResolvedValue(project('demo'))
    const wrapper = mountDialog()
    setInput('#pcd-name', 'Demo')
    setInput('#pcd-id', 'demo')
    await wrapper.vm.$nextTick()

    click('.pcd-actions .ui-btn--primary')
    await flushPromises()
    await wrapper.vm.$nextTick()

    expect(wrapper.emitted('created')?.[0]).toEqual([project('demo')])
    expect(wrapper.emitted('close')).toBeUndefined()
    wrapper.unmount()
  })

  it('when the store reports the create as superseded (null), does not emit created', async () => {
    createProjectApi.mockResolvedValue(null)
    const wrapper = mountDialog()
    setInput('#pcd-name', 'Demo')
    setInput('#pcd-id', 'demo')
    await wrapper.vm.$nextTick()

    click('.pcd-actions .ui-btn--primary')
    await flushPromises()
    await wrapper.vm.$nextTick()

    expect(wrapper.emitted('created')).toBeUndefined()
    wrapper.unmount()
  })

  it('on failure: keeps the child Modal open, keeps the input, and shows the error via Alert', async () => {
    createProjectApi.mockRejectedValue(new Error('name already exists'))
    const wrapper = mountDialog()
    setInput('#pcd-name', 'Demo')
    setInput('#pcd-id', 'demo')
    await wrapper.vm.$nextTick()

    click('.pcd-actions .ui-btn--primary')
    await flushPromises()
    await wrapper.vm.$nextTick()

    expect(wrapper.emitted('close')).toBeUndefined()
    expect(wrapper.emitted('created')).toBeUndefined()
    expect(document.querySelector('.ui-alert')?.textContent).toContain('name already exists')
    expect((document.querySelector('#pcd-name') as HTMLInputElement).value).toBe('Demo')
    wrapper.unmount()
  })

  it('closes immediately (no confirm) when the form is pristine', async () => {
    const wrapper = mountDialog()
    click('.pcd-actions .ui-btn--secondary')
    await wrapper.vm.$nextTick()
    expect(wrapper.emitted('close')).toHaveLength(1)
    expect(document.querySelector('.ui-confirm-title')).toBeNull()
    wrapper.unmount()
  })

  it('asks for confirmation via ConfirmDialog when closing a dirty form, and cancel keeps the input', async () => {
    const wrapper = mountDialog()
    setInput('#pcd-name', 'Demo')
    await wrapper.vm.$nextTick()

    click('.pcd-actions .ui-btn--secondary')
    await wrapper.vm.$nextTick()
    expect(wrapper.emitted('close')).toBeUndefined()
    const confirmButtons = document.querySelectorAll('.ui-confirm-actions .ui-btn')
    expect(confirmButtons).toHaveLength(2)

    ;(confirmButtons[0] as HTMLElement).dispatchEvent(new MouseEvent('click', { bubbles: true }))
    await wrapper.vm.$nextTick()
    expect(wrapper.emitted('close')).toBeUndefined()
    expect((document.querySelector('#pcd-name') as HTMLInputElement).value).toBe('Demo')
    wrapper.unmount()
  })

  it('confirming discard on a dirty close emits close', async () => {
    const wrapper = mountDialog()
    setInput('#pcd-name', 'Demo')
    await wrapper.vm.$nextTick()

    click('.pcd-actions .ui-btn--secondary')
    await wrapper.vm.$nextTick()
    const confirmButtons = document.querySelectorAll('.ui-confirm-actions .ui-btn')
    ;(confirmButtons[1] as HTMLElement).dispatchEvent(new MouseEvent('click', { bubbles: true }))
    await wrapper.vm.$nextTick()
    expect(wrapper.emitted('close')).toHaveLength(1)
    wrapper.unmount()
  })

  it('an Escape while the dirty-close ConfirmDialog is topmost only cancels the confirm, not the create Dialog', async () => {
    const wrapper = mountDialog()
    setInput('#pcd-name', 'Demo')
    await wrapper.vm.$nextTick()

    click('.pcd-actions .ui-btn--secondary')
    await wrapper.vm.$nextTick()
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
    await wrapper.vm.$nextTick()

    expect(wrapper.emitted('close')).toBeUndefined()
    expect((document.querySelector('#pcd-name') as HTMLInputElement).value).toBe('Demo')
    wrapper.unmount()
  })

  it('resets all fields when reopened', async () => {
    const wrapper = mountDialog()
    setInput('#pcd-name', 'Demo')
    await wrapper.vm.$nextTick()

    await wrapper.setProps({ open: false })
    await wrapper.setProps({ open: true })
    await wrapper.vm.$nextTick()

    expect((document.querySelector('#pcd-name') as HTMLInputElement).value).toBe('')
    wrapper.unmount()
  })

  it('a create that resolves after this Modal was closed and reopened does not emit created for the new session', async () => {
    let resolveCreate: (value: Project) => void = () => {}
    createProjectApi.mockImplementationOnce(() => new Promise<Project>((resolve) => { resolveCreate = resolve }))
    const wrapper = mountDialog()
    setInput('#pcd-name', 'Demo')
    setInput('#pcd-id', 'demo')
    await wrapper.vm.$nextTick()
    click('.pcd-actions .ui-btn--primary')
    await wrapper.vm.$nextTick()

    // A new create session starts before the first request resolves.
    await wrapper.setProps({ open: false })
    await wrapper.setProps({ open: true })
    setInput('#pcd-name', 'Second')
    await wrapper.vm.$nextTick()

    resolveCreate(project('first'))
    await flushPromises()
    await wrapper.vm.$nextTick()

    expect(wrapper.emitted('created')).toBeUndefined()
    // The still-open, still-being-typed second session must be left untouched.
    expect((document.querySelector('#pcd-name') as HTMLInputElement).value).toBe('Second')
    wrapper.unmount()
  })

  it('a create that rejects after this Modal was closed and reopened does not surface its error in the new session', async () => {
    let rejectCreate: (err: Error) => void = () => {}
    createProjectApi.mockImplementationOnce(() => new Promise<Project>((_resolve, reject) => { rejectCreate = reject }))
    const wrapper = mountDialog()
    setInput('#pcd-name', 'Demo')
    setInput('#pcd-id', 'demo')
    await wrapper.vm.$nextTick()
    click('.pcd-actions .ui-btn--primary')
    await wrapper.vm.$nextTick()

    await wrapper.setProps({ open: false })
    await wrapper.setProps({ open: true })
    await wrapper.vm.$nextTick()

    rejectCreate(new Error('stale failure'))
    await flushPromises()
    await wrapper.vm.$nextTick()

    expect(document.querySelector('.ui-alert')).toBeNull()
    wrapper.unmount()
  })

  // flowgate.v02.0088 F01/F10: the Dialog's own words, field errors, color names and its general failure follow the
  // language; what the person typed is sent as typed.
  it.each([
    ['ja', '新しいプロジェクト', '名前を入力してください。', '色 #2563eb', 'プロジェクトを作成できませんでした。'],
    ['en', 'New project', 'Enter a name.', 'Color #2563eb', 'Could not create the project.'],
  ] as const)('shows its words in %s and sends the input unchanged', async (locale, title, nameRequired, colorName, failed) => {
    setTestLocale(locale)
    createProjectApi.mockRejectedValueOnce(new Error(''))
    const wrapper = mountDialog()
    expect(document.querySelector('.pcd-title')?.textContent).toBe(title)
    expect(document.querySelector('.pcd-color')?.getAttribute('aria-label')).toBe(colorName)
    setInput('#pcd-id', 'demo')
    click('.pcd-actions .ui-btn--primary')
    await wrapper.vm.$nextTick()
    expect(document.querySelector('.pcd-field-error')?.textContent).toBe(nameRequired)

    setInput('#pcd-name', '  프로젝트 가  ')
    setInput('#pcd-desc', '설명 원문')
    await wrapper.vm.$nextTick()
    click('.pcd-actions .ui-btn--primary')
    await flushPromises()
    await wrapper.vm.$nextTick()
    expect(createProjectApi).toHaveBeenCalledWith({ name: '프로젝트 가', id: 'demo', description: '설명 원문', color: '#2563eb' })
    // No server words: the localized general failure, never an English default.
    expect(document.querySelector('.ui-alert')?.textContent?.trim()).toBe(failed)
    wrapper.unmount()
  })

  it('re-renders a general failure still on screen in the new language, and keeps a server diagnostic as sent', async () => {
    createProjectApi.mockRejectedValueOnce(new Error(''))
    const wrapper = mountDialog()
    setInput('#pcd-name', 'Demo')
    setInput('#pcd-id', 'demo')
    await wrapper.vm.$nextTick()
    click('.pcd-actions .ui-btn--primary')
    await flushPromises()
    await wrapper.vm.$nextTick()
    expect(document.querySelector('.ui-alert')?.textContent?.trim()).toBe('프로젝트를 생성하지 못했습니다.')
    setTestLocale('en')
    await wrapper.vm.$nextTick()
    expect(document.querySelector('.ui-alert')?.textContent?.trim()).toBe('Could not create the project.')
    // A language switch is not an edit: the input stays and nothing is sent again.
    expect((document.querySelector('#pcd-name') as HTMLInputElement).value).toBe('Demo')
    expect(createProjectApi).toHaveBeenCalledTimes(1)

    createProjectApi.mockRejectedValueOnce(new Error('같은 식별자의 프로젝트가 있습니다.'))
    click('.pcd-actions .ui-btn--primary')
    await flushPromises()
    await wrapper.vm.$nextTick()
    expect(document.querySelector('.ui-alert')?.textContent?.trim()).toBe('같은 식별자의 프로젝트가 있습니다.')
    wrapper.unmount()
  })
})
