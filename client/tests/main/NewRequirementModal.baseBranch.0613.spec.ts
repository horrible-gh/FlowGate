import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'
import NewRequirementModal from '@main/components/NewRequirementModal.vue'
import { useProjectStore } from '@main/stores/project'
import { useExplorerStore } from '@main/stores/explorer'

const { getRequest, postUrlEncoded, showToast, extractApiErrorMessage } = vi.hoisted(() => ({
  getRequest: vi.fn(),
  postUrlEncoded: vi.fn(),
  showToast: vi.fn(),
  extractApiErrorMessage: vi.fn((error: any, fallback: string) =>
    error?.response?.data?.error?.message || fallback),
}))

vi.mock('@shared/api', () => ({
  getRequest,
  postUrlEncoded,
  extractApiErrorMessage,
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
enableAutoUnmount(afterEach)

async function render(failingCatalogProject = '', groupWorkBaseLocked: Record<string, boolean> = {}, responseOverride?: (url: string) => Promise<any> | undefined, initialGroupId?: string) {
  getRequest.mockImplementation((url: string) => {
    const overridden = responseOverride?.(url)
    if (overridden) return overridden
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
    props: { initialGroupId },
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
    extractApiErrorMessage.mockClear()
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

    expect(wrapper.get('[data-test="base-branch-error"]').text()).toContain('catalog down')
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
// D0104 §3.5.1 / §7.2: deterministic response ordering, no elapsed-time claims.
function deferred<T = any>() {
  let resolve!: (value: T) => void
  let reject!: (reason: unknown) => void
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no })
  return { promise, resolve, reject }
}
const catalogResponse = (projectId = 'git-project') => ({ data: catalogs[projectId] })
const treeResponse = (projectId = 'git-project') => ({ data: { data: { nodes: treeNodes(projectId) } } })
const groupsResponse = (projectId = 'git-project') => ({ data: { groups: [{
  group_id: `${projectId}.default.0613`, work_base_ref: 'flowgate-v0.2',
  effective_work_base_ref: 'flowgate-v0.2', work_base_locked: false,
}] } })
const createButton = (wrapper: ReturnType<typeof mount>) => wrapper.get('[data-dialog-action-id="submit-1"]')

describe('NewRequirementModal — independent C/G/T and fail-closed creation (0104 T#4)', () => {
  beforeEach(() => {
    localStorage.clear()
    setActivePinia(createPinia())
    useProjectStore().setCurrentProject('git-project')
    i18n.global.locale.value = 'en'
    getRequest.mockReset()
    postUrlEncoded.mockReset()
    showToast.mockReset()
    extractApiErrorMessage.mockClear()
    postUrlEncoded.mockResolvedValue({ data: { ok: true, result: { doc_id: 'created' } } })
  })

  it('starts C/G/T once and enables branch choice on C alone; late G/T preserve the choice and title', async () => {
    const c = deferred(), g = deferred(), tree = deferred()
    const wrapper = await render('', {}, url => {
      if (url.endsWith('/git/work-base-options')) return c.promise
      if (url.startsWith('/api/v1/groups?')) return g.promise
      if (url.includes('/groups/tree?')) return tree.promise
    })
    await wrapper.get('[data-test="project-select"]').trigger('change')
    await wrapper.get('[data-test="project-select"]').trigger('change')
    for (const part of ['/git/work-base-options', '/api/v1/groups?', '/groups/tree?']) {
      expect(getRequest.mock.calls.filter(([url]) => String(url).includes(part))).toHaveLength(1)
    }
    await wrapper.get('input#newReqTitle').setValue('Keep input')
    expect(createButton(wrapper).attributes('disabled')).toBeDefined()
    // The form path must enforce the guard as well as the disabled footer button.
    await wrapper.get('form').trigger('submit')
    expect(postUrlEncoded).not.toHaveBeenCalled()
    c.resolve(catalogResponse())
    await flushPromises()
    const select = wrapper.get('[data-test="base-branch-select"]')
    expect(select.attributes('disabled')).toBeUndefined()
    await select.setValue('flowgate-v0.2')
    expect(createButton(wrapper).attributes('disabled')).toBeUndefined()
    g.resolve(groupsResponse())
    tree.resolve(treeResponse())
    await flushPromises()
    expect(wrapper.get('input#newReqTitle').element).toHaveProperty('value', 'Keep input')
    expect(wrapper.get('[data-test="base-branch-select"]').element).toHaveProperty('value', 'flowgate-v0.2')
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(postUrlEncoded).toHaveBeenCalledWith('/api/v1/outbox/create', expect.objectContaining({
      doc_type: 'R', title: 'Keep input', work_base_ref: 'flowgate-v0.2',
    }))
  })

  it('handles G and T before C without submitting an existing group with unknown Git status', async () => {
    const c = deferred()
    const wrapper = await render('', {}, url => url.endsWith('/git/work-base-options') ? c.promise : undefined)
    await wrapper.findAll('.group-toggle-btn')[0].trigger('click')
    await wrapper.get('input#newReqTitle').setValue('Existing root')
    await wrapper.get('form').trigger('submit')
    expect(postUrlEncoded).not.toHaveBeenCalled()
    c.resolve(catalogResponse())
    await flushPromises()
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(postUrlEncoded).toHaveBeenCalledWith('/api/v1/outbox/create', expect.objectContaining({ group_id: 'git-project.default.0613' }))
  })

  it('applies tree eligibility separately, blocks missing G metadata, then initialises an unlocked group on G', async () => {
    const g = deferred()
    const wrapper = await render('', {}, url => url.startsWith('/api/v1/groups?') ? g.promise : undefined)
    await wrapper.findAll('.group-toggle-btn')[0].trigger('click')
    await wrapper.get('input#newReqTitle').setValue('Existing root')
    await wrapper.get('form').trigger('submit')
    expect(postUrlEncoded).not.toHaveBeenCalled()
    g.resolve(groupsResponse())
    await flushPromises()
    expect(wrapper.get('[data-test="base-branch-select"]').element).toHaveProperty('value', 'flowgate-v0.2')
    await wrapper.get('[data-test="base-branch-select"]').setValue('main')
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(postUrlEncoded.mock.calls[0][1]).toMatchObject({ group_id: 'git-project.default.0613', work_base_ref: 'main' })
  })

  it('shows C failure immediately while G/T are pending, and retries without losing title', async () => {
    const c = deferred(), g = deferred(), tree = deferred()
    let first = true
    const wrapper = await render('', {}, url => {
      if (url.endsWith('/git/work-base-options') && first) return c.promise
      if (url.startsWith('/api/v1/groups?')) return g.promise
      if (url.includes('/groups/tree?')) return tree.promise
    })
    await wrapper.get('input#newReqTitle').setValue('Retry input')
    c.reject({ response: { data: { error: { message: 'catalog down' } } } })
    await flushPromises()
    expect(wrapper.get('[data-test="base-branch-error"]').text()).toContain('catalog down')
    await wrapper.get('form').trigger('submit')
    expect(postUrlEncoded).not.toHaveBeenCalled()
    first = false
    await wrapper.get('[data-test="base-branch-retry"]').trigger('click')
    await flushPromises()
    expect(wrapper.get('[data-test="base-branch-select"]').attributes('disabled')).toBeUndefined()
    expect(wrapper.get('input#newReqTitle').element).toHaveProperty('value', 'Retry input')
    g.resolve(groupsResponse())
    tree.resolve(treeResponse())
    await flushPromises()
  })

  it.each([undefined, null, 'false', 0])('never treats a malformed git_enabled=%s as disabled', async (git_enabled) => {
    const wrapper = await render('', {}, url => url.endsWith('/git/work-base-options')
      ? Promise.resolve({ data: { git_enabled, branches: [] } }) : undefined)
    expect(wrapper.find('[data-test="base-branch-cell"]').exists()).toBe(true)
    expect(wrapper.find('[data-test="base-branch-error"]').exists()).toBe(true)
    await wrapper.get('input#newReqTitle').setValue('Must stay blocked')
    await wrapper.get('form').trigger('submit')
    expect(postUrlEncoded).not.toHaveBeenCalled()
  })

  it('allows ref omission on explicit disabled C alone, while G/T remain pending', async () => {
    const g = deferred(), tree = deferred()
    const wrapper = await render('', {}, url => {
      if (url.endsWith('/git/work-base-options')) return Promise.resolve(catalogResponse('plain-project'))
      if (url.startsWith('/api/v1/groups?')) return g.promise
      if (url.includes('/groups/tree?')) return tree.promise
    })
    expect(wrapper.find('[data-test="base-branch-cell"]').exists()).toBe(false)
    await wrapper.get('input#newReqTitle').setValue('Plain root')
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(postUrlEncoded).toHaveBeenCalledTimes(1)
    expect(postUrlEncoded.mock.calls[0][1]).not.toHaveProperty('work_base_ref')
    g.resolve(groupsResponse())
    tree.resolve(treeResponse())
    await flushPromises()
  })

  it('keeps an explicit choice across same-project refresh, and requires reselection if it disappears', async () => {
    const wrapper = await render()
    await wrapper.get('[data-test="base-branch-select"]').setValue('flowgate-v0.2')
    await wrapper.get('[data-test="project-select"]').trigger('change')
    await flushPromises()
    expect(wrapper.get('[data-test="base-branch-select"]').element).toHaveProperty('value', 'flowgate-v0.2')
    const original = getRequest.getMockImplementation()!
    getRequest.mockImplementation((url: string) => url.endsWith('/git/work-base-options')
      ? Promise.resolve({ data: { git_enabled: true, base_branch: 'main', branches: [{ name: 'main' }] } }) : original(url))
    await wrapper.get('[data-test="project-select"]').trigger('change')
    await flushPromises()
    expect(wrapper.get('[data-test="base-branch-select"]').element).toHaveProperty('value', '')
    expect(createButton(wrapper).attributes('disabled')).toBeDefined()
  })

  it('does not choose another candidate when the configured default is missing', async () => {
    const wrapper = await render('', {}, url => url.endsWith('/git/work-base-options')
      ? Promise.resolve({ data: { git_enabled: true, base_branch: 'deleted', branches: [{ name: 'main' }] } }) : undefined)
    expect(wrapper.get('[data-test="base-branch-select"]').element).toHaveProperty('value', '')
    expect(createButton(wrapper).attributes('disabled')).toBeDefined()
    await wrapper.get('[data-test="base-branch-select"]').setValue('main')
    expect(createButton(wrapper).attributes('disabled')).toBeUndefined()
  })

  it('keeps existing-group selection and its edited ref across independent metadata refresh', async () => {
    const wrapper = await render('', { 'git-project.default.0613': false })
    await wrapper.findAll('.group-toggle-btn')[0].trigger('click')
    await wrapper.get('[data-test="base-branch-select"]').setValue('main')
    await wrapper.get('[data-test="project-select"]').trigger('change')
    await flushPromises()
    expect(wrapper.get('[data-test="group-select"]').element).toHaveProperty('value', 'git-project.default.0613')
    expect(wrapper.get('[data-test="base-branch-select"]').element).toHaveProperty('value', 'main')
  })

  it('applies initialGroupId once and preserves a different user-selected group on a late tree update', async () => {
    const nodes = treeNodes('git-project')
    const secondId = 'git-project.default.0614'
    const bothNodes = [...nodes, { ...nodes[1], id: secondId, number: '0614', label: 'Second group' }]
    const firstGroup = groupsResponse().data.groups[0]
    const wrapper = await render('', {}, url => {
      if (url.includes('/groups/tree?')) return Promise.resolve({ data: { data: { nodes: bothNodes } } })
      if (url.startsWith('/api/v1/groups?')) return Promise.resolve({ data: { groups: [firstGroup, { ...firstGroup, group_id: secondId }] } })
    }, 'git-project.default.0613')
    expect(wrapper.get('[data-test="group-select"]').element).toHaveProperty('value', 'git-project.default.0613')
    await wrapper.get('[data-test="group-select"]').setValue(secondId)
    await wrapper.get('[data-test="base-branch-select"]').setValue('main')
    useExplorerStore().groupTreeCache['git-project:main:full'] = [...bothNodes] as any
    await flushPromises()
    expect(wrapper.get('[data-test="group-select"]').element).toHaveProperty('value', secondId)
    expect(wrapper.get('[data-test="base-branch-select"]').element).toHaveProperty('value', 'main')
  })

  it('rejects the first A response after an A/B/A switch even when the project matches again', async () => {
    const oldCatalog = deferred()
    let requestsForA = 0
    const wrapper = await render('', {}, url => {
      if (url === '/api/v1/projects/git-project/git/work-base-options' && ++requestsForA === 1) return oldCatalog.promise
    })
    await wrapper.get('[data-test="project-select"]').setValue('git-project-2')
    await flushPromises()
    await wrapper.get('[data-test="project-select"]').setValue('git-project')
    await flushPromises()
    await wrapper.get('[data-test="base-branch-select"]').setValue('flowgate-v0.2')
    oldCatalog.resolve({ data: { git_enabled: false, branches: [] } })
    await flushPromises()
    expect(wrapper.get('[data-test="base-branch-select"]').element).toHaveProperty('value', 'flowgate-v0.2')
  })


  it.each(['resolve', 'reject'] as const)('ignores late C %s, G and T after project switch', async outcome => {
    const c = deferred(), g = deferred(), tree = deferred()
    const wrapper = await render('', {}, url => {
      if (url.includes('/git-project/') && url.endsWith('/git/work-base-options')) return c.promise
      if (url === '/api/v1/groups?project_id=git-project') return g.promise
      if (url.includes('/git-project/groups/tree?')) return tree.promise
    })
    await wrapper.get('[data-test="project-select"]').setValue('git-project-2')
    await flushPromises()
    if (outcome === 'resolve') c.resolve(catalogResponse())
    else c.reject(new Error('old failure'))
    g.resolve(groupsResponse())
    tree.resolve(treeResponse())
    await flushPromises()
    expect(wrapper.get('[data-test="base-branch-select"]').element).toHaveProperty('value', 'release')
    expect(wrapper.find('[data-test="base-branch-error"]').exists()).toBe(false)
    await wrapper.findAll('.group-toggle-btn')[0].trigger('click')
    expect(wrapper.get('[data-test="group-select"]').element).toHaveProperty('value', 'git-project-2.default.0613')
  })

  it('invalidates a closed instance before reopening and ignores its late catalog failure', async () => {
    const c = deferred()
    const old = await render('', {}, url => url.endsWith('/git/work-base-options') ? c.promise : undefined)
    await old.get('[data-dialog-action-id="action-0"]').trigger('click')
    expect(old.emitted('close')).toHaveLength(1)
    const reopened = await render()
    c.reject(new Error('closed catalog'))
    await flushPromises()
    expect(reopened.get('[data-test="base-branch-select"]').element).toHaveProperty('value', 'main')
    expect(old.find('[data-test="base-branch-error"]').exists()).toBe(false)
    old.unmount()
  })

  it('does not request context after unmount during the initial project-list read', async () => {
    const projectList = deferred()
    const wrapper = await render('', {}, url => url === '/api/v1/projects' ? projectList.promise : undefined)
    wrapper.unmount()
    projectList.resolve({ data: { projects } })
    await flushPromises()
    expect(getRequest).toHaveBeenCalledTimes(1)
  })

  it('blocks duplicate form submits and context changes until POST success is emitted', async () => {
    const wrapper = await render()
    const post = deferred()
    postUrlEncoded.mockReturnValue(post.promise)
    await wrapper.get('input#newReqTitle').setValue('One request')
    await wrapper.get('form').trigger('submit')
    await wrapper.get('form').trigger('submit')
    expect(postUrlEncoded).toHaveBeenCalledTimes(1)
    for (const selector of ['[data-test="project-select"]', '[data-test="module-select"]', '[data-test="base-branch-select"]', '.group-toggle-btn', '.root-tab']) {
      for (const control of wrapper.findAll(selector)) expect(control.attributes('disabled')).toBeDefined()
    }
    // Even a synthetic change event cannot invalidate the in-flight creation.
    const contextCalls = getRequest.mock.calls.length
    await wrapper.get('[data-test="project-select"]').trigger('change')
    expect(getRequest).toHaveBeenCalledTimes(contextCalls)
    expect(postUrlEncoded.mock.calls[0][1]).toMatchObject({ project: 'git-project', title: 'One request' })
    post.resolve({ data: { ok: true, result: { doc_id: 'git-project.default.0613.0001-R' } } })
    await flushPromises()
    expect(wrapper.emitted('created')).toEqual([[{ docId: 'git-project.default.0613.0001-R', openAfter: true }]])
    expect(wrapper.get('.alert-success').exists()).toBe(true)
    expect(wrapper.get('[data-test="project-select"]').attributes('disabled')).toBeUndefined()
  })


  it('shows G failure with retry, blocks existing-group submission, and recovers after G succeeds', async () => {
    const first = deferred(), retried = deferred()
    let calls = 0
    const wrapper = await render('', {}, url => url.startsWith('/api/v1/groups?')
      ? (++calls === 1 ? first.promise : retried.promise) : undefined)
    await wrapper.findAll('.group-toggle-btn')[0].trigger('click')
    await wrapper.get('input#newReqTitle').setValue('Retry group details')
    expect(createButton(wrapper).attributes('disabled')).toBeDefined()
    await wrapper.get('form').trigger('submit')
    expect(postUrlEncoded).not.toHaveBeenCalled()
    first.reject({ response: { status: 403, data: { detail: 'Forbidden' } } })
    await flushPromises()
    expect(wrapper.get('[data-test="base-branch-error"]').text()).toContain('Could not load group details.')
    expect(wrapper.get('[data-test="base-branch-retry"]').exists()).toBe(true)
    await wrapper.get('form').trigger('submit')
    expect(wrapper.get('.alert-danger').text()).toContain('Could not load group details.')
    expect(postUrlEncoded).not.toHaveBeenCalled()
    await wrapper.get('[data-test="base-branch-retry"]').trigger('click')
    await flushPromises()
    expect(calls).toBe(2)
    expect(wrapper.find('[data-test="base-branch-error"]').exists()).toBe(false)
    expect(createButton(wrapper).attributes('disabled')).toBeDefined()
    await wrapper.get('form').trigger('submit')
    expect(postUrlEncoded).not.toHaveBeenCalled()
    retried.resolve(groupsResponse())
    await flushPromises()
    expect(wrapper.get('[data-test="group-select"]').element).toHaveProperty('value', 'git-project.default.0613')
    expect(wrapper.get('input#newReqTitle').element).toHaveProperty('value', 'Retry group details')
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(postUrlEncoded).toHaveBeenCalledWith('/api/v1/outbox/create', expect.objectContaining({
      group_id: 'git-project.default.0613', work_base_ref: 'flowgate-v0.2',
    }))
    expect(wrapper.emitted('created')).toHaveLength(1)
  })

  it('keeps a group missing from successful G readonly, submits without ref, and can refresh its metadata', async () => {
    let missing = true
    const wrapper = await render('', {}, url => url.startsWith('/api/v1/groups?')
      ? Promise.resolve(missing ? { data: { groups: [] } } : groupsResponse()) : undefined)
    await wrapper.findAll('.group-toggle-btn')[0].trigger('click')
    await wrapper.get('input#newReqTitle').setValue('Legacy existing root')
    expect(wrapper.get('[data-test="base-branch-readonly"]').attributes('readonly')).toBeDefined()
    expect(wrapper.find('[data-test="base-branch-select"]').exists()).toBe(false)
    expect(wrapper.find('[data-test="base-branch-error"]').exists()).toBe(false)
    expect(wrapper.find('[data-test="base-branch-retry"]').exists()).toBe(false)
    expect(createButton(wrapper).attributes('disabled')).toBeUndefined()
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(postUrlEncoded.mock.calls[0][1]).toMatchObject({ group_id: 'git-project.default.0613' })
    expect(postUrlEncoded.mock.calls[0][1]).not.toHaveProperty('work_base_ref')
    expect(wrapper.emitted('created')).toHaveLength(1)
    // A successful missing-row response needs no error retry; an explicit refresh may restore it.
    missing = false
    await wrapper.get('[data-test="project-select"]').trigger('change')
    await flushPromises()
    expect(wrapper.get('[data-test="base-branch-select"]').element).toHaveProperty('value', 'flowgate-v0.2')
  })

  it.each([401, 403])('resolves HTTP %s string detail through the existing i18n error resolver', async status => {
    const wrapper = await render()
    await wrapper.get('input#newReqTitle').setValue('Authentication refusal')
    const error = { response: { status, data: { detail: status === 401 ? 'Not authenticated' : 'Forbidden' } } }
    extractApiErrorMessage.mockReturnValueOnce('Localized refusal')
    postUrlEncoded.mockRejectedValueOnce(error)
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(extractApiErrorMessage).toHaveBeenCalledWith(error, expect.any(String))
    expect(showToast).toHaveBeenCalledWith('Localized refusal', 'danger')
    expect(showToast).not.toHaveBeenCalledWith(error.response.data.detail, 'danger')
    expect(wrapper.emitted('created')).toBeUndefined()
  })

  it.each(['close', 'unmount'] as const)('ignores late POST success after %s', async action => {
    const wrapper = await render()
    const post = deferred()
    postUrlEncoded.mockReturnValue(post.promise)
    await wrapper.get('input#newReqTitle').setValue('Closed creation')
    await wrapper.get('form').trigger('submit')
    if (action === 'close') await wrapper.get('[data-dialog-action-id="action-0"]').trigger('click')
    else wrapper.unmount()
    post.resolve({ data: { ok: true, result: { doc_id: 'created-after-close' } } })
    await flushPromises()
    expect(wrapper.emitted('created')).toBeUndefined()
    expect(showToast).not.toHaveBeenCalled()
  })

  // T#2 is approved. The legacy outbox maps process_service errors[] to HTTP 400.
  // Frontend tests deliberately mock HTTP, never real Git.
  it.each(['group_work_base_busy', 'group_work_base_locked'])('shows %s using the existing toast and inline error paths', async code => {
    const wrapper = await render('', { 'git-project.default.0613': false })
    await wrapper.findAll('.group-toggle-btn')[0].trigger('click')
    await wrapper.get('[data-test="base-branch-select"]').setValue('main')
    await wrapper.get('input#newReqTitle').setValue('Server decides')
    const message = `${code}: server rejected the change`
    postUrlEncoded.mockRejectedValueOnce({ response: { status: 400, data: { ok: false, errors: [{ code, message }] } } })
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(showToast).toHaveBeenLastCalledWith(message, 'danger')
    expect(wrapper.get('[data-test="base-branch-select"]').element).toHaveProperty('value', 'main')
    postUrlEncoded.mockResolvedValueOnce({ data: { ok: false, errors: [{ code, message }] } })
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(wrapper.get('.alert-danger').text()).toContain(message)
    expect(wrapper.emitted('created')).toBeUndefined()
    expect(postUrlEncoded).toHaveBeenCalledTimes(2)
  })
})
