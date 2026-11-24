import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { Project } from '@/features/project/types'
import './projectI18n'

const fetchActiveProjects = vi.hoisted(() => vi.fn())
vi.mock('@/features/project/api', () => ({ fetchActiveProjects, createProject: vi.fn(), suggestProjectSlug: vi.fn(async () => null) }))
const currentUser = vi.hoisted(() => ({ value: null as { username: string; email: string | null; isAdmin: boolean } | null }))
vi.mock('@/core/auth/currentUser', () => ({ useCurrentUser: () => ({ user: currentUser }) }))

import ProjectDialog from '@/features/project/ProjectDialog.vue'
import { projectWindowUi } from '@/features/project/windowState'
import { useProjectStore } from '@/features/project/store'

const project = (id: string): Project => ({ id, name: id, description: `desc-${id}`, color: null, isActive: true, createdAt: null, updatedAt: null, modules: [] })
const flush = () => new Promise<void>((resolve) => setTimeout(resolve, 0))
const panel = () => document.querySelector<HTMLElement>('.ui-modeless-panel')

// The Designer taskbar's [Open project] opens the project list as a floating window instead of the modal.
describe('project list as a floating window', () => {
  beforeEach(() => {
    fetchActiveProjects.mockReset().mockResolvedValue([project('a'), project('b')])
    currentUser.value = { username: 'member', email: null, isAdmin: false }
    localStorage.clear()
    setActivePinia(createPinia())
  })
  afterEach(() => { document.body.innerHTML = ''; projectWindowUi.open = false })

  it('opens without a backdrop, with the same search and list, and lights the taskbar state', async () => {
    const wrapper = mount(ProjectDialog, { attachTo: document.body })
    const store = useProjectStore()
    store.openDialog({ window: true })
    await flush()
    await wrapper.vm.$nextTick()
    expect(document.querySelector('.ui-modal-overlay')).toBeNull()
    expect(panel()).not.toBeNull()
    expect(document.querySelector('[data-test="project-window"] input[type="search"]')).not.toBeNull()
    expect(document.querySelectorAll('.project-row')).toHaveLength(2)
    expect(projectWindowUi.open).toBe(true)
    wrapper.unmount()
  })

  it('opens a project from the window and closes it, and the File menu path still opens the modal', async () => {
    const wrapper = mount(ProjectDialog, { attachTo: document.body })
    const store = useProjectStore()
    store.openDialog({ window: true })
    await flush()
    await wrapper.vm.$nextTick()
    ;(document.querySelector('[data-project-id="b"] .project-row-open') as HTMLElement).dispatchEvent(new MouseEvent('click', { bubbles: true }))
    await wrapper.vm.$nextTick()
    expect(store.dialogOpen).toBe(false)
    expect(store.currentProjectId).toBe('b')
    expect(wrapper.emitted('opened')).toHaveLength(1)
    expect(projectWindowUi.open).toBe(false)
    store.openDialog()
    await flush()
    await wrapper.vm.$nextTick()
    expect(document.querySelector('.ui-modal-overlay')).not.toBeNull()
    expect(panel()).toBeNull()
    wrapper.unmount()
  })

  it('brings a minimized window back when it is asked for again instead of opening a second one', async () => {
    const wrapper = mount(ProjectDialog, { attachTo: document.body })
    const store = useProjectStore()
    store.openDialog({ window: true })
    await flush()
    await wrapper.vm.$nextTick()
    ;(document.querySelector('[data-modeless-action="minimize"]') as HTMLElement).click()
    await wrapper.vm.$nextTick()
    expect(document.querySelector('[data-modeless-action="restore"]')).not.toBeNull()
    expect(projectWindowUi.open).toBe(true)
    store.openDialog({ window: true })
    await flush()
    await wrapper.vm.$nextTick()
    expect(document.querySelectorAll('.ui-modeless-panel')).toHaveLength(1)
    expect(document.querySelector('[data-modeless-action="minimize"]')).not.toBeNull()
    ;(document.querySelector('[data-modeless-action="close"]') as HTMLElement).click()
    await wrapper.vm.$nextTick()
    expect(store.dialogOpen).toBe(false)
    expect(panel()).toBeNull()
    wrapper.unmount()
  })
})
