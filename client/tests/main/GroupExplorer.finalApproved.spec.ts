import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import i18n from '@shared/i18n'
import GroupExplorer from '@main/components/GroupExplorer.vue'

const { getRequest } = vi.hoisted(() => ({ getRequest: vi.fn() }))

vi.mock('@shared/api', () => ({
  default: { head: vi.fn(), get: vi.fn(), post: vi.fn(), patch: vi.fn() },
  getRequest,
  patchRequest: vi.fn(),
  postRequest: vi.fn(),
}))

function n(partial: Record<string, unknown>) {
  return {
    type_code: null,
    number: null,
    filename: null,
    has_md: false,
    md_path: null,
    ...partial,
  }
}

// project > module > { G1 final-approved, G2 in-progress, G3 no-flag (legacy server) }
const NODES = [
  n({ id: 'project:p', parent_id: null, node_type: 'project', label: 'P' }),
  n({ id: 'module:p:default', parent_id: 'project:p', node_type: 'module', label: 'default' }),
  n({ id: 'p.default.0001', parent_id: 'module:p:default', node_type: 'group', label: 'G1', is_final_approved: true }),
  n({ id: 'p.default.0001.0001-R', parent_id: 'p.default.0001', node_type: 'document', type_code: 'R', label: '[R]: r1', has_md: true, md_path: 'r1.md' }),
  n({ id: 'p.default.0002', parent_id: 'module:p:default', node_type: 'group', label: 'G2', is_final_approved: false }),
  n({ id: 'p.default.0002.0001-R', parent_id: 'p.default.0002', node_type: 'document', type_code: 'R', label: '[R]: r2', has_md: true, md_path: 'r2.md' }),
  // G3 has no is_final_approved field — simulates an older server response.
  n({ id: 'p.default.0003', parent_id: 'module:p:default', node_type: 'group', label: 'G3' }),
  n({ id: 'p.default.0003.0001-R', parent_id: 'p.default.0003', node_type: 'document', type_code: 'R', label: '[R]: r3', has_md: true, md_path: 'r3.md' }),
  // G4 is discarded (carries a file-less DC doc). It must be governed by the SAME
  // toggle as the final-approved group, not a separate control (review r4).
  n({ id: 'p.default.0004', parent_id: 'module:p:default', node_type: 'group', label: 'G4', is_discarded: true }),
  n({ id: 'p.default.0004.0003-DC', parent_id: 'p.default.0004', node_type: 'document', type_code: 'DC', label: '[Discard]: Group Discard' }),
]

const GroupTreeNodeStub = {
  name: 'GroupTreeNode',
  props: ['node', 'allNodes', 'treeNodes', 'projectId'],
  template: '<li class="stub-node" />',
}

async function mountExplorer() {
  getRequest.mockResolvedValue({ data: { data: { nodes: NODES } } })
  const wrapper = mount(GroupExplorer, {
    props: { projectId: 'p' },
    global: { plugins: [i18n], stubs: { GroupTreeNode: GroupTreeNodeStub } },
  })
  await flushPromises()
  return wrapper
}

// rootNodes is always [project:p], and it receives filteredNodes via :all-nodes,
// so we read the visible node id set off that single rendered GroupTreeNode.
function visibleIds(wrapper: ReturnType<typeof mount>): string[] {
  const node = wrapper.findComponent(GroupTreeNodeStub)
  return (node.props('allNodes') as Array<{ id: string }>).map((x) => x.id)
}

function toggleBtn(wrapper: ReturnType<typeof mount>) {
  return wrapper.find('button[aria-pressed]')
}

beforeEach(() => {
  setActivePinia(createPinia())
  localStorage.clear()
  getRequest.mockReset()
})

describe('GroupExplorer final-approved group filter', () => {
  it('hides final-approved groups and their documents by default (toggle off)', async () => {
    const wrapper = await mountExplorer()
    const ids = visibleIds(wrapper)
    // final-approved group and its documents are hidden out of the box
    expect(ids).not.toContain('p.default.0001')
    expect(ids).not.toContain('p.default.0001.0001-R')
    // in-progress group, legacy no-flag group, and the module node all remain
    expect(ids).toContain('p.default.0002')
    expect(ids).toContain('p.default.0003')
    expect(ids).toContain('module:p:default')
    expect(toggleBtn(wrapper).attributes('aria-pressed')).toBe('true')
  })

  it('shows every group once toggled on', async () => {
    const wrapper = await mountExplorer()
    await toggleBtn(wrapper).trigger('click')
    await wrapper.vm.$nextTick()

    const ids = visibleIds(wrapper)
    expect(ids).toContain('p.default.0001')
    expect(ids).toContain('p.default.0001.0001-R')
    expect(ids).toContain('p.default.0002')
    expect(toggleBtn(wrapper).attributes('aria-pressed')).toBe('false')
  })

  it('persists the setting per project in localStorage', async () => {
    const wrapper = await mountExplorer()
    // default is hidden; toggling on must store the shown state explicitly
    await toggleBtn(wrapper).trigger('click')
    expect(localStorage.getItem('flowgate:show-final-approved-groups:p')).toBe('1')
  })

  it('restores a shown setting from localStorage on mount', async () => {
    localStorage.setItem('flowgate:show-final-approved-groups:p', '1')
    const wrapper = await mountExplorer()
    expect(toggleBtn(wrapper).attributes('aria-pressed')).toBe('false')
    expect(visibleIds(wrapper)).toContain('p.default.0001')
  })

  it('treats a missing is_final_approved field as not-approved (back-compat)', async () => {
    const wrapper = await mountExplorer()
    // hiding is active by default; G3 has no flag and must stay visible
    expect(visibleIds(wrapper)).toContain('p.default.0003')
  })

  it('hides a discarded group by default and the SAME toggle reveals it (r4)', async () => {
    const wrapper = await mountExplorer()
    // out of the box the discarded group and its DC doc are hidden, like AC
    let ids = visibleIds(wrapper)
    expect(ids).not.toContain('p.default.0004')
    expect(ids).not.toContain('p.default.0004.0003-DC')

    // one click on the single visibility toggle surfaces BOTH the final-approved
    // group AND the discarded group — not "AC only" (the r4 regression)
    await toggleBtn(wrapper).trigger('click')
    await wrapper.vm.$nextTick()
    ids = visibleIds(wrapper)
    expect(ids).toContain('p.default.0001') // final-approved (AC)
    expect(ids).toContain('p.default.0004') // discarded (DC)
    expect(ids).toContain('p.default.0004.0003-DC')
  })
})
