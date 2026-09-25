import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'
import NewRequirementModal from '@main/components/NewRequirementModal.vue'
import { useProjectStore } from '@main/stores/project'

const { getRequest, postUrlEncoded, showToast } = vi.hoisted(() => ({
  getRequest: vi.fn(),
  postUrlEncoded: vi.fn(),
  showToast: vi.fn(),
}))

vi.mock('@shared/api', () => ({
  getRequest,
  postUrlEncoded,
  extractApiErrorMessage: (error: any, fallback: string) =>
    error?.response?.data?.error?.message || fallback,
}))

vi.mock('@main/components/common/useToast', () => ({
  useToast: () => ({ showToast }),
}))

// 0613 TR0014: GET /projects (list_projects_endpoint) returns only project + modules —
// it never carries Git config. Mocking git_enabled/base_branch here would hide the real
// contract mismatch the way the earlier revision's mock did, so this fixture matches the
// live shape exactly and every Git signal below flows through work-base-options instead.
const projects = [
  { project: 'git-project', modules: ['default'] },
  { project: 'plain-project', modules: ['core'] },
  { project: 'git-project-2', modules: ['next'] },
]

// 0613 T0013 — GET /projects/{id}/git/work-base-options: already narrowed server-side to
// the names a new group may store (no remote-only / internal slot rows ever arrive).
// A disabled/absent integration answers git_enabled:false with no branches (200, never
// a rejection) — that response is this dialog's only source of truth for Git status.
const catalogs: Record<string, object> = {
  'git-project': {
    ok: true,
    git_enabled: true,
    base_branch: 'main',
    branches: [
      { name: 'flowgate-v0.2', is_project_base: false },
      { name: 'main', is_project_base: true },
    ],
  },
  'plain-project': {
    ok: true,
    git_enabled: false,
    base_branch: null,
    branches: [],
  },
  'git-project-2': {
    ok: true,
    git_enabled: true,
    base_branch: 'release',
    branches: [
      { name: 'maintenance', is_project_base: false },
      { name: 'release', is_project_base: true },
    ],
  },
}

function treeNodes(projectId: string) {
  return [
    {
      id: 'default', parent_id: null, node_type: 'module', type_code: null,
      number: null, filename: null, label: 'default', title: 'default',
      has_md: false, md_path: null,
    },
    {
      id: `${projectId}.default.0613`, parent_id: 'default', node_type: 'group', type_code: null,
      number: '0613', filename: null, label: 'Base Branch UI', title: 'Base Branch UI',
      has_md: false, md_path: null,
    },
  ]
}

function projectIdFromUrl(url: string): string {
  return decodeURIComponent(url.split('/projects/')[1]?.split('/')[0] || '')
}

// 0613 TR0014 rev2: the prior rejection's "existing group = readonly, no exceptions"
// rule is gone — work_base_locked (server-computed) now decides it per group. Defaults
// to locked:true (git-project's group) so every pre-existing test below, written against
// the old always-readonly behavior, keeps passing unchanged.
async function render(failingCatalogProject = '', groupWorkBaseLocked: Record<string, boolean> = {}) {
  getRequest.mockImplementation((url: string) => {
    if (url === '/api/v1/projects') return Promise.resolve({ data: { projects } })
    if (url.includes('/groups/tree?')) {
      return Promise.resolve({ data: { data: { nodes: treeNodes(projectIdFromUrl(url)) } } })
    }
    if (url.includes('/git/branches')) {
      // Branch Manager catalog (project.settings.read) — a requirement author may not hold it.
      return Promise.reject({ response: { status: 403, data: { detail: 'Forbidden' } } })
    }
    if (url.includes('/git/work-base-options')) {
      const projectId = projectIdFromUrl(url)
      if (projectId === failingCatalogProject) {
        return Promise.reject({ response: { data: { error: { message: 'catalog down' } } } })
      }
      return Promise.resolve({ data: catalogs[projectId] })
    }
    if (url.startsWith('/api/v1/groups?project_id=')) {
      const projectId = decodeURIComponent(url.split('=')[1] || '')
      const groupId = `${projectId}.default.0613`
      return Promise.resolve({
        data: {
          groups: [{
            group_id: groupId,
            work_base_ref: projectId === 'git-project' ? 'flowgate-v0.2' : null,
            effective_work_base_ref: projectId === 'git-project' ? 'flowgate-v0.2' : 'release',
            work_base_locked: groupWorkBaseLocked[groupId] ?? true,
          }],
        },
      })
    }
    return Promise.resolve({ data: { data: { nodes: [] } } })
  })
  const wrapper = mount(NewRequirementModal, {
    global: { plugins: [i18n], stubs: { teleport: true } },
  })
  await flushPromises()
  await flushPromises()
  return wrapper
}

describe('NewRequirementModal — Base Branch contract (0613 T#2)', () => {
  beforeEach(() => {
    localStorage.clear()
    setActivePinia(createPinia())
    useProjectStore().setCurrentProject('git-project')
    i18n.global.locale.value = 'en'
    getRequest.mockReset()
    postUrlEncoded.mockReset()
    showToast.mockReset()
    postUrlEncoded.mockResolvedValue({
      data: { ok: true, result: { doc_id: 'git-project.default.0613.0001-B' } },
    })
  })

  it('renders Module | Base Branch and sends the selected branch with the existing B form fields', async () => {
    const wrapper = await render()

    const contextRow = wrapper.get('.project-context-row')
    expect(contextRow.classes()).toContain('project-context-row--git')
    expect(contextRow.get('[data-test="module-cell"]').exists()).toBe(true)
    expect(contextRow.get('[data-test="base-branch-cell"]').exists()).toBe(true)
    expect(wrapper.get('[data-test="base-branch-select"]').element).toHaveProperty('value', 'main')
    expect(wrapper.find('.fg-dialog-body form').exists()).toBe(true)
    expect(wrapper.find('[data-dialog-action-id="submit-1"]').exists()).toBe(true)

    await wrapper.get('[data-test="base-branch-select"]').setValue('flowgate-v0.2')
    await wrapper.findAll('.root-tab')[1].trigger('click')
    await wrapper.get('input#newReqTitle').setValue('Login failure')
    await wrapper.get('textarea').setValue('Reproduction details')
    await wrapper.findAll('.form-row select')[0].setValue('reviewer')
    await wrapper.findAll('.form-row select')[1].setValue('none')
    await wrapper.get('input[type="checkbox"]').setValue(false)
    await wrapper.get('[data-dialog-action-id="submit-1"]').trigger('click')
    await flushPromises()

    expect(postUrlEncoded).toHaveBeenCalledWith(
      '/api/v1/outbox/create',
      expect.objectContaining({
        project: 'git-project',
        module: 'default',
        doc_type: 'B',
        title: 'Login failure',
        body: 'Reproduction details',
        owner: 'reviewer',
        template: 'none',
        work_base_ref: 'flowgate-v0.2',
      }),
    )
    expect(wrapper.emitted('created')?.[0]?.[0]).toMatchObject({ openAfter: false })
  })

  it('removes the Base Branch cell for non-Git projects even though work-base-options is still queried', async () => {
    const wrapper = await render()

    await wrapper.get('[data-test="project-select"]').setValue('plain-project')
    await flushPromises()
    await flushPromises()

    // 0613 TR0014: /projects carries no Git config, so the dialog cannot skip this call up
    // front — it must ask work-base-options and let that response's git_enabled:false hide
    // the section (see catalogs['plain-project'] above).
    expect(getRequest).toHaveBeenCalledWith('/api/v1/projects/plain-project/git/work-base-options')
    expect(wrapper.find('[data-test="base-branch-cell"]').exists()).toBe(false)
    expect(wrapper.get('.project-context-row').classes()).not.toContain('project-context-row--git')
    expect(wrapper.get('[data-test="module-select"]').element).toHaveProperty('value', 'core')

    await wrapper.get('input#newReqTitle').setValue('Plain project requirement')
    await wrapper.get('[data-dialog-action-id="submit-1"]').trigger('click')
    await flushPromises()
    const payload = postUrlEncoded.mock.calls.at(-1)?.[1]
    expect(payload).not.toHaveProperty('work_base_ref')
  })

  it('shows a locked existing group Base Branch read-only and omits it from submission', async () => {
    // 0613 TR0014 rev2: readonly is no longer the blanket rule for every existing
    // group — this fixture pins work_base_locked:true, the state a group reaches once
    // real Git work has started from it (git/group_work_base.group_work_base_locked).
    const wrapper = await render('', { 'git-project.default.0613': true })

    await wrapper.findAll('.group-toggle-btn')[0].trigger('click')
    await flushPromises()

    const readonly = wrapper.get('[data-test="base-branch-readonly"]')
    expect(readonly.attributes('readonly')).toBeDefined()
    expect((readonly.element as HTMLInputElement).value).toBe('flowgate-v0.2')
    expect(wrapper.find('[data-test="base-branch-select"]').exists()).toBe(false)

    await wrapper.get('input#newReqTitle').setValue('Existing group requirement')
    await wrapper.get('[data-dialog-action-id="submit-1"]').trigger('click')
    await flushPromises()
    const payload = postUrlEncoded.mock.calls.at(-1)?.[1]
    expect(payload).toMatchObject({ group_id: 'git-project.default.0613' })
    expect(payload).not.toHaveProperty('work_base_ref')
  })

  it('lets an unlocked existing group pick a different Base Branch and submits it', async () => {
    // The rejected rule blocked this outright ("기존 그룹 = readonly, 예외 없음"). Before
    // any Git work has started from the group's stored base (work_base_locked:false),
    // picking a different one is ordinary group setup, not a rewrite of history.
    const wrapper = await render('', { 'git-project.default.0613': false })

    await wrapper.findAll('.group-toggle-btn')[0].trigger('click')
    await flushPromises()

    expect(wrapper.find('[data-test="base-branch-readonly"]').exists()).toBe(false)
    const select = wrapper.get('[data-test="base-branch-select"]')
    // Defaults to the group's current effective Base Branch, not the project default.
    expect((select.element as HTMLSelectElement).value).toBe('flowgate-v0.2')
    expect(select.findAll('option').map((option) => option.text())).toEqual(['flowgate-v0.2', 'main'])

    await select.setValue('main')
    await wrapper.get('input#newReqTitle').setValue('Rebased existing group requirement')
    await wrapper.get('[data-dialog-action-id="submit-1"]').trigger('click')
    await flushPromises()

    expect(postUrlEncoded).toHaveBeenCalledWith(
      '/api/v1/outbox/create',
      expect.objectContaining({ group_id: 'git-project.default.0613', work_base_ref: 'main' }),
    )
  })

  it('replaces the branch list and default when the project changes', async () => {
    const wrapper = await render()
    await wrapper.get('[data-test="base-branch-select"]').setValue('flowgate-v0.2')

    await wrapper.get('[data-test="project-select"]').setValue('git-project-2')
    await flushPromises()
    await flushPromises()

    const branchSelect = wrapper.get('[data-test="base-branch-select"]')
    expect((branchSelect.element as HTMLSelectElement).value).toBe('release')
    expect(branchSelect.findAll('option').map((option) => option.text())).toEqual(['maintenance', 'release'])
    expect(getRequest).toHaveBeenCalledWith('/api/v1/projects/git-project-2/git/work-base-options')
  })

  it('surfaces branch catalog failure and does not silently fall back to main', async () => {
    const wrapper = await render('git-project')

    expect(wrapper.get('[data-test="base-branch-error"]').text()).toBe('catalog down')
    expect((wrapper.get('[data-test="base-branch-select"]').element as HTMLSelectElement).value).toBe('')
    expect(wrapper.get('[data-test="base-branch-select"]').attributes('disabled')).toBeDefined()
    expect(wrapper.get('[data-dialog-action-id="submit-1"]').attributes('disabled')).toBeDefined()

    await wrapper.get('input#newReqTitle').setValue('Must not submit')
    await wrapper.get('[data-dialog-action-id="submit-1"]').trigger('click')
    await flushPromises()
    expect(postUrlEncoded).not.toHaveBeenCalled()
  })

  it('reads Base Branch options from the requirement-author boundary, never the Branch Manager catalog', async () => {
    // The mock answers /git/branches with 403, exactly what a worker-role author gets
    // from the project.settings.read catalog. Creation must still work end to end.
    const wrapper = await render()

    expect(getRequest).toHaveBeenCalledWith('/api/v1/projects/git-project/git/work-base-options')
    expect(getRequest.mock.calls.some(([url]) => String(url).includes('/git/branches'))).toBe(false)
    expect(wrapper.find('[data-test="base-branch-error"]').exists()).toBe(false)
    const select = wrapper.get('[data-test="base-branch-select"]')
    expect(select.findAll('option').map((option) => option.text())).toEqual(['flowgate-v0.2', 'main'])
    expect((select.element as HTMLSelectElement).value).toBe('main')

    await select.setValue('flowgate-v0.2')
    await wrapper.get('input#newReqTitle').setValue('Worker requirement')
    await wrapper.get('[data-dialog-action-id="submit-1"]').trigger('click')
    await flushPromises()
    expect(postUrlEncoded).toHaveBeenCalledWith(
      '/api/v1/outbox/create',
      expect.objectContaining({ project: 'git-project', work_base_ref: 'flowgate-v0.2' }),
    )
  })

  it('shows the server message when a selected branch becomes stale without changing the selection', async () => {
    const wrapper = await render()
    await wrapper.get('[data-test="base-branch-select"]').setValue('flowgate-v0.2')
    await wrapper.get('input#newReqTitle').setValue('Stale base branch')
    postUrlEncoded.mockRejectedValueOnce({
      response: {
        data: {
          errors: [{
            code: 'group_work_base_not_found',
            message: 'Selected base branch no longer exists',
          }],
        },
      },
    })

    await wrapper.get('[data-dialog-action-id="submit-1"]').trigger('click')
    await flushPromises()

    expect(showToast).toHaveBeenCalledWith('Selected base branch no longer exists', 'danger')
    expect((wrapper.get('[data-test="base-branch-select"]').element as HTMLSelectElement).value)
      .toBe('flowgate-v0.2')
  })
})