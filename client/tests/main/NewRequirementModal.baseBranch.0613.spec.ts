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

const projects = [
  { project: 'git-project', modules: ['default'], git_enabled: true, base_branch: 'main' },
  { project: 'plain-project', modules: ['core'], git_enabled: false, base_branch: null },
  { project: 'git-project-2', modules: ['next'], git_enabled: true, base_branch: 'release' },
]

const catalogs: Record<string, object> = {
  'git-project': {
    ok: true,
    base_branch: 'main',
    branches: [
      { name: 'main', kind: 'base', can_be_create_source: true },
      { name: 'flowgate-v0.2', kind: 'local', can_be_create_source: true },
      { name: 'remote-only', kind: 'remote_only', can_be_create_source: false },
    ],
  },
  'git-project-2': {
    ok: true,
    base_branch: 'release',
    branches: [
      { name: 'release', kind: 'base', can_be_create_source: true },
      { name: 'maintenance', kind: 'local', can_be_create_source: true },
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

async function render(failingCatalogProject = '') {
  getRequest.mockImplementation((url: string) => {
    if (url === '/api/v1/projects') return Promise.resolve({ data: { projects } })
    if (url.includes('/groups/tree?')) {
      return Promise.resolve({ data: { data: { nodes: treeNodes(projectIdFromUrl(url)) } } })
    }
    if (url.includes('/git/branches')) {
      const projectId = projectIdFromUrl(url)
      if (projectId === failingCatalogProject) {
        return Promise.reject({ response: { data: { error: { message: 'catalog down' } } } })
      }
      return Promise.resolve({ data: catalogs[projectId] })
    }
    if (url.startsWith('/api/v1/groups?project_id=')) {
      const projectId = decodeURIComponent(url.split('=')[1] || '')
      return Promise.resolve({
        data: {
          groups: [{
            group_id: `${projectId}.default.0613`,
            work_base_ref: projectId === 'git-project' ? 'flowgate-v0.2' : null,
            effective_work_base_ref: projectId === 'git-project' ? 'flowgate-v0.2' : 'release',
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

  it('removes the Base Branch cell for non-Git projects and never calls their branch catalog', async () => {
    const wrapper = await render()

    await wrapper.get('[data-test="project-select"]').setValue('plain-project')
    await flushPromises()
    await flushPromises()

    expect(wrapper.find('[data-test="base-branch-cell"]').exists()).toBe(false)
    expect(wrapper.get('.project-context-row').classes()).not.toContain('project-context-row--git')
    expect(wrapper.get('[data-test="module-select"]').element).toHaveProperty('value', 'core')
    expect(getRequest.mock.calls.some(([url]) => url === '/api/v1/projects/plain-project/git/branches')).toBe(false)

    await wrapper.get('input#newReqTitle').setValue('Plain project requirement')
    await wrapper.get('[data-dialog-action-id="submit-1"]').trigger('click')
    await flushPromises()
    const payload = postUrlEncoded.mock.calls.at(-1)?.[1]
    expect(payload).not.toHaveProperty('work_base_ref')
  })

  it('shows the existing group effective base read-only and omits it from submission', async () => {
    const wrapper = await render()

    await wrapper.findAll('.group-toggle-btn')[0].trigger('click')
    await flushPromises()

    const readonly = wrapper.get('[data-test="base-branch-readonly"]')
    expect(readonly.attributes('readonly')).toBeDefined()
    expect((readonly.element as HTMLInputElement).value).toBe('flowgate-v0.2')

    await wrapper.get('input#newReqTitle').setValue('Existing group requirement')
    await wrapper.get('[data-dialog-action-id="submit-1"]').trigger('click')
    await flushPromises()
    const payload = postUrlEncoded.mock.calls.at(-1)?.[1]
    expect(payload).toMatchObject({ group_id: 'git-project.default.0613' })
    expect(payload).not.toHaveProperty('work_base_ref')
  })

  it('replaces the branch list and default when the project changes', async () => {
    const wrapper = await render()
    await wrapper.get('[data-test="base-branch-select"]').setValue('flowgate-v0.2')

    await wrapper.get('[data-test="project-select"]').setValue('git-project-2')
    await flushPromises()
    await flushPromises()

    const branchSelect = wrapper.get('[data-test="base-branch-select"]')
    expect((branchSelect.element as HTMLSelectElement).value).toBe('release')
    expect(branchSelect.findAll('option').map((option) => option.text())).toEqual(['release', 'maintenance'])
    expect(getRequest).toHaveBeenCalledWith('/api/v1/projects/git-project-2/git/branches')
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