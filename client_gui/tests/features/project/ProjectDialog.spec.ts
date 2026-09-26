import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { Project } from '@/features/project/types'

const fetchActiveProjects = vi.hoisted(() => vi.fn())
const createProjectApi = vi.hoisted(() => vi.fn())
const suggestProjectSlug = vi.hoisted(() => vi.fn())

vi.mock('@/features/project/api', () => ({
  fetchActiveProjects,
  createProject: createProjectApi,
  suggestProjectSlug,
}))

const currentUser = vi.hoisted(() => ({ value: null as { username: string; email: string | null; isAdmin: boolean } | null }))
vi.mock('@/core/auth/currentUser', () => ({ useCurrentUser: () => ({ user: currentUser }) }))

import ProjectDialog from '@/features/project/ProjectDialog.vue'
import { useProjectStore } from '@/features/project/store'

function project(id: string, overrides: Partial<Project> = {}): Project {
  return {
    id,
    name: id,
    description: `desc-${id}`,
    color: null,
    isActive: true,
    createdAt: null,
    updatedAt: null,
    modules: [],
    ...overrides,
  }
}

function flushPromises(): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, 0))
}

// Content lives behind ModalShell's Teleport(to="body") — same convention as
// ModalShell.spec.ts — so DOM lookups use `document`, not the wrapper's own tree.
describe('features/project/ProjectDialog', () => {
  beforeEach(() => {
    fetchActiveProjects.mockReset().mockResolvedValue([])
    createProjectApi.mockReset()
    suggestProjectSlug.mockReset().mockResolvedValue(null)
    currentUser.value = { username: 'member', email: null, isAdmin: false }
    localStorage.clear()
    setActivePinia(createPinia())
  })

  afterEach(() => {
    document.body.querySelectorAll('.ui-modal-overlay').forEach((el) => el.remove())
  })

  function mountDialog() {
    return mount(ProjectDialog, { attachTo: document.body })
  }

  it('renders nothing until the store opens the Dialog, and fetches the active list on open', async () => {
    const wrapper = mountDialog()
    const store = useProjectStore()
    expect(document.querySelector('.ui-modal-overlay')).toBeNull()

    fetchActiveProjects.mockResolvedValue([project('a')])
    store.openDialog()
    await wrapper.vm.$nextTick()

    expect(document.querySelector('.ui-modal-overlay')).not.toBeNull()
    expect(fetchActiveProjects).toHaveBeenCalledTimes(1)
    wrapper.unmount()
  })

  it('shows LoadingState while the list is in flight, then the rows once it resolves', async () => {
    let resolveFetch: (value: Project[]) => void = () => {}
    fetchActiveProjects.mockImplementationOnce(() => new Promise<Project[]>((resolve) => { resolveFetch = resolve }))
    const wrapper = mountDialog()
    const store = useProjectStore()
    store.openDialog()
    await wrapper.vm.$nextTick()
    expect(document.querySelector('.ui-loading-state')).not.toBeNull()

    resolveFetch([project('a')])
    await flushPromises()
    await wrapper.vm.$nextTick()
    expect(document.querySelector('.ui-loading-state')).toBeNull()
    expect(document.querySelectorAll('.project-row')).toHaveLength(1)
    wrapper.unmount()
  })

  it('shows ErrorState with retry on failure, and retry re-fetches', async () => {
    fetchActiveProjects.mockRejectedValueOnce(new Error('offline')).mockResolvedValueOnce([project('a')])
    const wrapper = mountDialog()
    const store = useProjectStore()
    store.openDialog()
    await flushPromises()
    await wrapper.vm.$nextTick()

    expect(document.querySelector('.ui-error-state')).not.toBeNull()
    ;(document.querySelector('.ui-error-state button') as HTMLElement).dispatchEvent(new MouseEvent('click', { bubbles: true }))
    await flushPromises()
    await wrapper.vm.$nextTick()

    expect(fetchActiveProjects).toHaveBeenCalledTimes(2)
    expect(document.querySelectorAll('.project-row')).toHaveLength(1)
    wrapper.unmount()
  })

  it('shows a distinct EmptyState when there are no Projects at all', async () => {
    fetchActiveProjects.mockResolvedValue([])
    const wrapper = mountDialog()
    useProjectStore().openDialog()
    await flushPromises()
    await wrapper.vm.$nextTick()

    expect(document.querySelector('.ui-empty-state-title')?.textContent).toBe('프로젝트가 없습니다.')
    wrapper.unmount()
  })

  it('search filters client-side by name/id/description, distinguishing "no results" from "no projects"', async () => {
    fetchActiveProjects.mockResolvedValue([project('alpha', { name: 'Alpha' }), project('beta', { name: 'Beta' })])
    const wrapper = mountDialog()
    useProjectStore().openDialog()
    await flushPromises()
    await wrapper.vm.$nextTick()
    expect(document.querySelectorAll('.project-row')).toHaveLength(2)

    const search = document.querySelector('.pd-search-input') as HTMLInputElement
    search.value = 'alpha'
    search.dispatchEvent(new Event('input'))
    await wrapper.vm.$nextTick()
    expect(document.querySelectorAll('.project-row')).toHaveLength(1)
    expect(document.querySelector('.project-row-name')?.textContent).toBe('Alpha')

    search.value = 'zzz-no-match'
    search.dispatchEvent(new Event('input'))
    await wrapper.vm.$nextTick()
    expect(document.querySelectorAll('.project-row')).toHaveLength(0)
    expect(document.querySelector('.ui-empty-state-title')?.textContent).toBe('일치하는 프로젝트가 없습니다.')
    wrapper.unmount()
  })

  it('a row click only changes the Dialog-local selection, never store.currentProject', async () => {
    fetchActiveProjects.mockResolvedValue([project('a'), project('b')])
    const wrapper = mountDialog()
    const store = useProjectStore()
    store.openDialog()
    await flushPromises()
    await wrapper.vm.$nextTick()
    const priorCurrentId = store.currentProject?.id

    const rows = document.querySelectorAll('.project-row-main')
    ;(rows[1] as HTMLElement).dispatchEvent(new MouseEvent('click', { bubbles: true }))
    await wrapper.vm.$nextTick()

    expect(document.querySelectorAll('.project-row-main')[1].getAttribute('aria-pressed')).toBe('true')
    expect(store.currentProject?.id).toBe(priorCurrentId)
    expect(document.querySelector('.ui-modal-overlay')).not.toBeNull()
    wrapper.unmount()
  })

  it('an explicit 열기 changes current Project and closes the Dialog', async () => {
    fetchActiveProjects.mockResolvedValue([project('a'), project('b')])
    const wrapper = mountDialog()
    const store = useProjectStore()
    store.openDialog()
    await flushPromises()
    await wrapper.vm.$nextTick()

    const openButtons = document.querySelectorAll('.project-row-open')
    ;(openButtons[1] as HTMLElement).dispatchEvent(new MouseEvent('click', { bubbles: true }))
    await wrapper.vm.$nextTick()

    expect(store.currentProject?.id).toBe('b')
    expect(store.dialogOpen).toBe(false)
    wrapper.unmount()
  })

  it('a double-click on a row also opens it', async () => {
    fetchActiveProjects.mockResolvedValue([project('a')])
    const wrapper = mountDialog()
    const store = useProjectStore()
    store.openDialog()
    await flushPromises()
    await wrapper.vm.$nextTick()

    ;(document.querySelector('.project-row-main') as HTMLElement).dispatchEvent(new MouseEvent('dblclick', { bubbles: true }))
    await wrapper.vm.$nextTick()

    expect(store.currentProject?.id).toBe('a')
    expect(store.dialogOpen).toBe(false)
    wrapper.unmount()
  })

  it('marks the current Project with a Badge, independent of the Dialog-local selection', async () => {
    localStorage.setItem('fg_current_project_id', 'a')
    fetchActiveProjects.mockResolvedValue([project('a'), project('b')])
    const wrapper = mountDialog()
    useProjectStore().openDialog()
    await flushPromises()
    await wrapper.vm.$nextTick()

    const rows = document.querySelectorAll('.project-row')
    expect(rows[0].querySelector('.ui-badge')?.textContent).toBe('현재')
    expect(rows[1].querySelector('.ui-badge')).toBeNull()
    wrapper.unmount()
  })

  it('never renders a recent-projects section or a favorite star', async () => {
    fetchActiveProjects.mockResolvedValue([project('a')])
    const wrapper = mountDialog()
    useProjectStore().openDialog()
    await flushPromises()
    await wrapper.vm.$nextTick()

    const surfaceText = document.querySelector('.ui-modal-surface')?.textContent ?? ''
    expect(surfaceText).not.toContain('최근 프로젝트')
    expect(document.querySelector('.project-star')).toBeNull()
    expect(surfaceText).not.toContain('★')
    wrapper.unmount()
  })

  it('hides the + 새 프로젝트 entry point for a non-admin user', async () => {
    currentUser.value = { username: 'member', email: null, isAdmin: false }
    const wrapper = mountDialog()
    useProjectStore().openDialog()
    await wrapper.vm.$nextTick()
    const newButton = Array.from(document.querySelectorAll('.pd-intro .ui-btn')).find((el) => el.textContent?.includes('새 프로젝트'))
    expect(newButton).toBeUndefined()
    wrapper.unmount()
  })

  it('shows the + 새 프로젝트 entry point for an admin user, opening the create child Modal', async () => {
    currentUser.value = { username: 'admin', email: null, isAdmin: true }
    const wrapper = mountDialog()
    useProjectStore().openDialog()
    await wrapper.vm.$nextTick()

    const newButton = Array.from(document.querySelectorAll('.pd-intro .ui-btn')).find((el) => el.textContent?.includes('새 프로젝트'))
    expect(newButton).toBeTruthy()
    newButton?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    await wrapper.vm.$nextTick()

    expect(document.querySelector('.pcd-title')?.textContent).toBe('새 프로젝트')
    wrapper.unmount()
  })

  it('places + 새 프로젝트 as an intro-area action beside the guidance text, and search as its own row below (MirageGlass 02-project-dialog.html parity), not a search toolbar', async () => {
    currentUser.value = { username: 'admin', email: null, isAdmin: true }
    fetchActiveProjects.mockResolvedValue([project('a')])
    const wrapper = mountDialog()
    useProjectStore().openDialog()
    await wrapper.vm.$nextTick()

    expect(document.querySelector('.pd-intro .ui-btn')).not.toBeNull()
    expect(document.querySelector('.pd-intro .pd-search-input')).toBeNull()
    expect(document.querySelector('.pd-search .ui-btn')).toBeNull()
    expect(document.querySelector('.pd-search .pd-search-input')).not.toBeNull()
    wrapper.unmount()
  })

  it('after a successful create: closes the child Modal, refreshes the list, selects the new Project, keeps the parent open and current unchanged', async () => {
    currentUser.value = { username: 'admin', email: null, isAdmin: true }
    fetchActiveProjects.mockResolvedValueOnce([project('a')]).mockResolvedValueOnce([project('a'), project('new-project', { name: 'New Project' })])
    createProjectApi.mockResolvedValue(project('new-project', { name: 'New Project' }))

    const wrapper = mountDialog()
    const store = useProjectStore()
    store.openDialog()
    await flushPromises()
    await wrapper.vm.$nextTick()
    const priorCurrentId = store.currentProject?.id

    const newButton = Array.from(document.querySelectorAll('.pd-intro .ui-btn')).find((el) => el.textContent?.includes('새 프로젝트')) as HTMLElement
    newButton.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    await wrapper.vm.$nextTick()
    ;(document.querySelector('#pcd-name') as HTMLInputElement).value = 'New Project'
    ;(document.querySelector('#pcd-name') as HTMLInputElement).dispatchEvent(new Event('input'))
    await wrapper.vm.$nextTick()
    ;(document.querySelector('.pcd-actions .ui-btn--primary') as HTMLElement).dispatchEvent(new MouseEvent('click', { bubbles: true }))
    await flushPromises()
    await wrapper.vm.$nextTick()

    expect(document.querySelector('.pcd-title')).toBeNull()
    expect(store.projects.map((p) => p.id)).toEqual(['a', 'new-project'])
    expect(store.currentProject?.id).toBe(priorCurrentId)
    expect(document.querySelector('.ui-modal-overlay')).not.toBeNull()
    const selectedRow = Array.from(document.querySelectorAll('.project-row')).find((el) => el.querySelector('.project-row-main')?.getAttribute('aria-pressed') === 'true')
    expect(selectedRow?.getAttribute('data-project-id')).toBe('new-project')
    wrapper.unmount()
  })

  it('shows a body guidance block that does not imply a Designer move', async () => {
    fetchActiveProjects.mockResolvedValue([project('a')])
    const wrapper = mountDialog()
    useProjectStore().openDialog()
    await wrapper.vm.$nextTick()

    expect(document.querySelector('.pd-intro-title')?.textContent).toBe('프로젝트를 선택하세요.')
    expect(document.querySelector('.pd-intro-desc')).not.toBeNull()
    const surfaceText = document.querySelector('.ui-modal-surface')?.textContent ?? ''
    expect(surfaceText).not.toContain('디자이너')
    wrapper.unmount()
  })

  it('closing the parent Dialog also closes the create child Modal — no orphan Modal, no stale session on reopen', async () => {
    currentUser.value = { username: 'admin', email: null, isAdmin: true }
    fetchActiveProjects.mockResolvedValue([project('a')])
    const wrapper = mountDialog()
    const store = useProjectStore()
    store.openDialog()
    await flushPromises()
    await wrapper.vm.$nextTick()

    const newButton = Array.from(document.querySelectorAll('.pd-intro .ui-btn')).find((el) => el.textContent?.includes('새 프로젝트')) as HTMLElement
    newButton.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    await wrapper.vm.$nextTick()
    expect(document.querySelector('.pcd-title')).not.toBeNull()

    // Parent closes programmatically (Escape/backdrop/store.closeDialog() all go through
    // this same store state) while the child Modal is still open.
    store.closeDialog()
    await wrapper.vm.$nextTick()

    expect(document.querySelector('.ui-modal-overlay')).toBeNull()
    expect(document.querySelector('.pcd-title')).toBeNull()

    fetchActiveProjects.mockResolvedValueOnce([project('a')])
    store.openDialog()
    await flushPromises()
    await wrapper.vm.$nextTick()

    // The reopened parent must not resurrect the previous create session.
    expect(document.querySelector('.pcd-title')).toBeNull()
    wrapper.unmount()
  })

  it('a create still in flight when the parent Dialog closes and reopens does not leak into the new session', async () => {
    currentUser.value = { username: 'admin', email: null, isAdmin: true }
    fetchActiveProjects.mockResolvedValue([project('a')])
    let resolveCreate: (value: Project) => void = () => {}
    createProjectApi.mockImplementationOnce(() => new Promise<Project>((resolve) => { resolveCreate = resolve }))

    const wrapper = mountDialog()
    const store = useProjectStore()
    store.openDialog()
    await flushPromises()
    await wrapper.vm.$nextTick()

    const newButton = Array.from(document.querySelectorAll('.pd-intro .ui-btn')).find((el) => el.textContent?.includes('새 프로젝트')) as HTMLElement
    newButton.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    await wrapper.vm.$nextTick()
    ;(document.querySelector('#pcd-name') as HTMLInputElement).value = 'Pending'
    ;(document.querySelector('#pcd-name') as HTMLInputElement).dispatchEvent(new Event('input'))
    await wrapper.vm.$nextTick()
    ;(document.querySelector('.pcd-actions .ui-btn--primary') as HTMLElement).dispatchEvent(new MouseEvent('click', { bubbles: true }))
    await wrapper.vm.$nextTick()

    // Parent closes (cascading the child closed) while the create request is still pending.
    store.closeDialog()
    await wrapper.vm.$nextTick()
    expect(document.querySelector('.pcd-title')).toBeNull()

    fetchActiveProjects.mockResolvedValueOnce([project('a')])
    store.openDialog()
    await flushPromises()
    await wrapper.vm.$nextTick()

    resolveCreate(project('pending', { name: 'Pending' }))
    await flushPromises()
    await wrapper.vm.$nextTick()

    // The stale create's success must not reopen the old child session or select a Project
    // the new session never created.
    expect(document.querySelector('.pcd-title')).toBeNull()
    const selected = Array.from(document.querySelectorAll('.project-row')).find((el) => el.querySelector('.project-row-main')?.getAttribute('aria-pressed') === 'true')
    expect(selected?.getAttribute('data-project-id')).not.toBe('pending')
    wrapper.unmount()
  })

  it('closes on Escape and on a backdrop click', async () => {
    fetchActiveProjects.mockResolvedValue([])
    const wrapper = mountDialog()
    const store = useProjectStore()
    store.openDialog()
    await wrapper.vm.$nextTick()

    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
    await wrapper.vm.$nextTick()
    expect(store.dialogOpen).toBe(false)
    wrapper.unmount()
  })

  it('clears search and Dialog-local selection each time it reopens', async () => {
    fetchActiveProjects.mockResolvedValue([project('a'), project('b')])
    const wrapper = mountDialog()
    const store = useProjectStore()
    store.openDialog()
    await flushPromises()
    await wrapper.vm.$nextTick()

    const search = document.querySelector('.pd-search-input') as HTMLInputElement
    search.value = 'a'
    search.dispatchEvent(new Event('input'))
    await wrapper.vm.$nextTick()

    store.closeDialog()
    await wrapper.vm.$nextTick()
    store.openDialog()
    await flushPromises()
    await wrapper.vm.$nextTick()

    expect((document.querySelector('.pd-search-input') as HTMLInputElement).value).toBe('')
    expect(document.querySelectorAll('.project-row')).toHaveLength(2)
    wrapper.unmount()
  })

  it('clears the Dialog-local selection once search hides it, instead of keeping a stale hidden selection', async () => {
    fetchActiveProjects.mockResolvedValue([project('alpha', { name: 'Alpha' }), project('beta', { name: 'Beta' })])
    const wrapper = mountDialog()
    const store = useProjectStore()
    store.openDialog()
    await flushPromises()
    await wrapper.vm.$nextTick()

    ;(document.querySelectorAll('.project-row-main')[1] as HTMLElement).dispatchEvent(new MouseEvent('click', { bubbles: true }))
    await wrapper.vm.$nextTick()
    expect(document.querySelectorAll('.project-row-main')[1].getAttribute('aria-pressed')).toBe('true')

    const search = document.querySelector('.pd-search-input') as HTMLInputElement
    search.value = 'alpha'
    search.dispatchEvent(new Event('input'))
    await wrapper.vm.$nextTick()

    expect(document.querySelectorAll('.project-row')).toHaveLength(1)
    expect(document.querySelector('.project-row-main')?.getAttribute('aria-pressed')).toBe('false')
    wrapper.unmount()
  })

  it('a list fetch that resolves after the Dialog closed does not resurrect the Dialog-local selection', async () => {
    let resolveFetch: (value: Project[]) => void = () => {}
    fetchActiveProjects.mockImplementationOnce(() => new Promise<Project[]>((resolve) => { resolveFetch = resolve }))
    const wrapper = mountDialog()
    const store = useProjectStore()
    store.openDialog()
    await wrapper.vm.$nextTick()

    store.closeDialog()
    resolveFetch([project('a')])
    await flushPromises()
    await wrapper.vm.$nextTick()

    expect(document.querySelector('.ui-modal-overlay')).toBeNull()
    fetchActiveProjects.mockResolvedValueOnce([project('a'), project('b')])
    store.openDialog()
    await flushPromises()
    await wrapper.vm.$nextTick()
    // The reopened session gets its own fresh default selection, not one left over from
    // the stale response that arrived while the Dialog was closed.
    const selected = Array.from(document.querySelectorAll('.project-row')).find((el) => el.querySelector('.project-row-main')?.getAttribute('aria-pressed') === 'true')
    expect(selected).toBeTruthy()
    wrapper.unmount()
  })

  it('the search control is the shared Input primitive, not a hand-rolled input', async () => {
    fetchActiveProjects.mockResolvedValue([project('a')])
    const wrapper = mountDialog()
    useProjectStore().openDialog()
    await wrapper.vm.$nextTick()

    const search = document.querySelector('.pd-search-input') as HTMLInputElement
    expect(search.classList.contains('ui-input')).toBe(true)
    expect(search.getAttribute('type')).toBe('search')
    expect(search.getAttribute('aria-label')).toBe('프로젝트 검색')
    expect(search.hasAttribute('data-modal-autofocus')).toBe(true)
    wrapper.unmount()
  })

  it('autofocuses the search input on open', async () => {
    fetchActiveProjects.mockResolvedValue([project('a')])
    const wrapper = mountDialog()
    useProjectStore().openDialog()
    await wrapper.vm.$nextTick()
    await wrapper.vm.$nextTick()
    expect(document.activeElement).toBe(document.querySelector('.pd-search-input'))
    wrapper.unmount()
  })

  it('the "/" shortcut focuses search without hijacking a literal "/" typed into another field', async () => {
    currentUser.value = { username: 'admin', email: null, isAdmin: true }
    fetchActiveProjects.mockResolvedValue([project('a')])
    const wrapper = mountDialog()
    useProjectStore().openDialog()
    await flushPromises()
    await wrapper.vm.$nextTick()

    const search = document.querySelector('.pd-search-input') as HTMLInputElement
    search.blur()
    document.body.focus()
    document.dispatchEvent(new KeyboardEvent('keydown', { key: '/', cancelable: true }))
    await wrapper.vm.$nextTick()
    expect(document.activeElement).toBe(search)

    // Typing "/" while already inside a text field (e.g. the create form) must not be
    // hijacked away from that field.
    const newButton = Array.from(document.querySelectorAll('.pd-intro .ui-btn')).find((el) => el.textContent?.includes('새 프로젝트')) as HTMLElement
    newButton.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    await wrapper.vm.$nextTick()
    await wrapper.vm.$nextTick()
    const nameInput = document.querySelector('#pcd-name') as HTMLInputElement
    nameInput.focus()
    const event = new KeyboardEvent('keydown', { key: '/', cancelable: true })
    document.dispatchEvent(event)
    await wrapper.vm.$nextTick()
    expect(document.activeElement).toBe(nameInput)
    wrapper.unmount()
  })

  it('the "/" shortcut does nothing while the create child Modal is open (this Dialog is not topmost)', async () => {
    currentUser.value = { username: 'admin', email: null, isAdmin: true }
    fetchActiveProjects.mockResolvedValue([project('a')])
    const wrapper = mountDialog()
    useProjectStore().openDialog()
    await flushPromises()
    await wrapper.vm.$nextTick()

    const newButton = Array.from(document.querySelectorAll('.pd-intro .ui-btn')).find((el) => el.textContent?.includes('새 프로젝트')) as HTMLElement
    newButton.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    await wrapper.vm.$nextTick()
    await wrapper.vm.$nextTick()

    const closeButton = document.querySelector('.pcd-head .ui-icon-btn') as HTMLElement
    closeButton.focus()
    document.dispatchEvent(new KeyboardEvent('keydown', { key: '/', cancelable: true }))
    await wrapper.vm.$nextTick()

    expect(document.activeElement).not.toBe(document.querySelector('.pd-search-input'))
    wrapper.unmount()
  })
})
