/**
 * 0449 T0004 item 1 — a failed BACKGROUND refresh must not take the group tree down.
 *
 * NR0003 E1, reproduced by execution: with nodes already rendered, bumping refreshToken and
 * failing both GETs (the first and getTreeWithRetry's one retry) removed every node from the
 * DOM and left the blocking error screen — "그룹으로 이동할 수 없음". `reload()`'s `silent`
 * flag suppressed only the loading branch; the catch set the blocking error regardless, and
 * `v-else-if="error"` replaced the whole `.tree-ul`.
 *
 * The contract now: nothing rendered yet → blocking error (unchanged). Something rendered →
 * keep it, show a non-blocking notice with a retry, and swap in the new tree atomically when
 * a retry finally succeeds.
 *
 * 0449 TR0005 rev1 — this file used to replace GroupTreeNode with an empty `<li>` stub and
 * assert on its `allNodes` prop. That proves which nodes were *passed down*, which is not what
 * completion criterion 1 asks for: it asks that the node DOM and the group ENTRY behaviour
 * survive. A prop can be intact while the rows are unclickable or the entry path is broken.
 * The real GroupTreeNode is mounted here instead — only its three dialogs and the context
 * menu are stubbed — and every assertion goes through rendered rows, real clicks, real
 * store-backed expansion, and the tab the entry actually opens.
 */
import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import i18n from '@shared/i18n'
import GroupExplorer from '@main/components/GroupExplorer.vue'
import { useTabsStore } from '@main/stores/tabs'

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

const PROJECT = n({ id: 'project:p', parent_id: null, node_type: 'project', label: 'P' })
const MODULE = n({ id: 'module:p:default', parent_id: 'project:p', node_type: 'module', label: 'default' })
const DOC_0449 = n({
  id: 'p.default.0449.0001-R',
  parent_id: 'p.default.0449',
  node_type: 'document',
  label: 'R0001',
  type_code: 'R',
  has_md: true,
  md_path: 'work/0449/0001-R.md',
})

const NODES = [
  PROJECT,
  MODULE,
  n({ id: 'p.default.0449', parent_id: 'module:p:default', node_type: 'group', label: 'G', is_final_approved: false }),
  DOC_0449,
]

// The reopened group as the STALE cache still describes it: wf_done at the time of the last
// successful fetch, so is_final_approved is true and the default toggle hides it.
const STALE_FINAL_APPROVED_NODES = [
  PROJECT,
  MODULE,
  n({ id: 'p.default.0449', parent_id: 'module:p:default', node_type: 'group', label: 'G', is_final_approved: true }),
  DOC_0449,
  n({ id: 'p.default.0448', parent_id: 'module:p:default', node_type: 'group', label: 'Other', is_final_approved: false }),
]

const NEXT_NODES = [
  ...NODES,
  n({ id: 'p.default.0450', parent_id: 'module:p:default', node_type: 'group', label: 'G2', is_final_approved: false }),
]

// Only the dialogs GroupTreeNode owns are stubbed — they teleport and are not what item 1 is
// about. The tree rows themselves are the real component.
const DIALOG_STUBS = {
  CreateEditGroupModal: true,
  GroupDiscardModal: true,
  GroupTokenIssueModal: true,
  ContextMenu: true,
  ContextMenuItem: true,
}

const timeout = () =>
  Object.assign(new Error('timeout of 130000ms exceeded'), { code: 'ECONNABORTED' })

const ok = (nodes: unknown[]) => ({ data: { data: { nodes } } })

/** Every label currently in the rendered tree DOM (not a prop). */
function renderedLabels(wrapper: any): string[] {
  return wrapper.findAll('.tree-ul .tree-lbl').map((x: any) => x.text())
}

/** Click the rendered row carrying `label`. Returns false when no such row is on screen. */
async function clickRow(wrapper: any, label: string): Promise<boolean> {
  for (const row of wrapper.findAll('.tree-row')) {
    const lbl = row.find('.tree-lbl')
    if (lbl.exists() && lbl.text() === label) {
      await row.trigger('click')
      await flushPromises()
      return true
    }
  }
  return false
}

/** Walk down to the group rows the way a user does: expand the project, then the module. */
async function openToGroups(wrapper: any) {
  await clickRow(wrapper, 'P')
  await clickRow(wrapper, 'default')
}

async function mountWithNodes(nodes = NODES) {
  getRequest.mockResolvedValueOnce(ok(nodes))
  const wrapper = mount(GroupExplorer, {
    props: { projectId: 'p', refreshToken: 0 },
    global: { plugins: [i18n], stubs: DIALOG_STUBS },
  })
  await flushPromises()
  expect(wrapper.find('.tree-ul').exists()).toBe(true)
  await openToGroups(wrapper)
  return wrapper
}

/** One background refresh whose first GET *and* retry both fail (the reproduced shape). */
async function failedRefresh(wrapper: any, token: number) {
  getRequest.mockRejectedValueOnce(timeout()).mockRejectedValueOnce(timeout())
  await wrapper.setProps({ refreshToken: token })
  // getTreeWithRetry backs off 800ms before its single retry.
  await new Promise((r) => setTimeout(r, 900))
  await flushPromises()
}

beforeEach(() => {
  setActivePinia(createPinia())
  localStorage.clear()
  getRequest.mockReset()
})

describe('GroupExplorer — background refresh failure keeps the rendered tree', () => {
  it('keeps the node DOM and shows a NON-blocking error after two failed refresh GETs', async () => {
    const wrapper = await mountWithNodes()
    expect(renderedLabels(wrapper)).toContain('G')

    await failedRefresh(wrapper, 1)

    // The tree survived, with its rows still rendered.
    expect(wrapper.find('.tree-ul').exists()).toBe(true)
    expect(renderedLabels(wrapper)).toContain('G')
    // The failure is reported, but not as the blocking screen that replaces the tree.
    expect(wrapper.find('[data-test="explorer-refresh-error"]').exists()).toBe(true)
    expect(wrapper.find('.sdb-state--error').exists()).toBe(false)
    // Exactly one wave: the initial mount GET, then this refresh's GET + its single retry.
    expect(getRequest).toHaveBeenCalledTimes(3)
  })

  it('the group row is still ENTERABLE after the failure, and opens its document', async () => {
    const wrapper = await mountWithNodes()
    const tabs = useTabsStore()

    await failedRefresh(wrapper, 1)

    // Entering the group: the row still responds to a click by expanding and revealing its
    // documents. A preserved `allNodes` prop cannot show this — the row has to be live.
    expect(renderedLabels(wrapper)).not.toContain('R0001')
    expect(await clickRow(wrapper, 'G')).toBe(true)
    expect(renderedLabels(wrapper)).toContain('R0001')

    // …and the document opens, which is what "그룹으로 들어간다" means for the user.
    expect(await clickRow(wrapper, 'R0001')).toBe(true)
    expect(tabs.tabs.map((t) => t.id)).toContain('p.default.0449.0001-R')
    expect(tabs.activeTabId).toBe('p.default.0449.0001-R')
  })

  it('an expansion opened BEFORE the failure is still open after it', async () => {
    const wrapper = await mountWithNodes()
    await clickRow(wrapper, 'G')
    expect(renderedLabels(wrapper)).toContain('R0001')

    await failedRefresh(wrapper, 1)

    // The GroupTreeNode instances were never torn down, so their open state is untouched.
    expect(renderedLabels(wrapper)).toContain('R0001')
  })

  it('recovers atomically when the retry button’s next request succeeds', async () => {
    const wrapper = await mountWithNodes()
    await failedRefresh(wrapper, 1)

    getRequest.mockResolvedValueOnce(ok(NEXT_NODES))
    await wrapper.find('[data-test="explorer-refresh-retry"]').trigger('click')
    await flushPromises()

    expect(wrapper.find('[data-test="explorer-refresh-error"]').exists()).toBe(false)
    expect(wrapper.find('.tree-ul').exists()).toBe(true)
    expect(renderedLabels(wrapper)).toContain('G2')
  })

  it('keeps the tree AND the retry affordance when the retry fails again', async () => {
    const wrapper = await mountWithNodes()
    await failedRefresh(wrapper, 1)

    getRequest.mockRejectedValueOnce(timeout()).mockRejectedValueOnce(timeout())
    await wrapper.find('[data-test="explorer-refresh-retry"]').trigger('click')
    await new Promise((r) => setTimeout(r, 900))
    await flushPromises()

    expect(wrapper.find('.tree-ul').exists()).toBe(true)
    expect(renderedLabels(wrapper)).toContain('G')
    expect(await clickRow(wrapper, 'G')).toBe(true)
    expect(renderedLabels(wrapper)).toContain('R0001')
    expect(wrapper.find('[data-test="explorer-refresh-error"]').exists()).toBe(true)
    expect(wrapper.find('[data-test="explorer-refresh-retry"]').exists()).toBe(true)
    expect(wrapper.find('.sdb-state--error').exists()).toBe(false)
  })

  it('still blocks with the old error screen when the INITIAL load has nothing to keep', async () => {
    getRequest.mockRejectedValue(timeout())
    const wrapper = mount(GroupExplorer, {
      props: { projectId: 'p', refreshToken: 0 },
      global: { plugins: [i18n], stubs: DIALOG_STUBS },
    })
    await new Promise((r) => setTimeout(r, 900))
    await flushPromises()

    expect(wrapper.find('.sdb-state--error').exists()).toBe(true)
    expect(wrapper.find('.tree-ul').exists()).toBe(false)
    expect(wrapper.find('[data-test="explorer-refresh-error"]').exists()).toBe(false)
  })

  it('a stale is_final_approved=true group does not lose its entry point across the failure', async () => {
    // Reopen scenario: the cached tree still calls the group final-approved, so the default
    // hide toggle had removed it. Preserving the tree alone would preserve that removal —
    // exactly the group the user just reopened and wants to reach.
    const wrapper = await mountWithNodes(STALE_FINAL_APPROVED_NODES)
    expect(renderedLabels(wrapper)).not.toContain('G')

    await failedRefresh(wrapper, 1)

    expect(wrapper.find('.tree-ul').exists()).toBe(true)
    expect(renderedLabels(wrapper)).toContain('G')
    expect(renderedLabels(wrapper)).toContain('Other')
    // Back on screen AND usable: the point of restoring the row is that it can be entered.
    const tabs = useTabsStore()
    expect(await clickRow(wrapper, 'G')).toBe(true)
    expect(await clickRow(wrapper, 'R0001')).toBe(true)
    expect(tabs.tabs.map((t) => t.id)).toContain('p.default.0449.0001-R')

    // …and when a refresh finally succeeds, the server's fresh flags govern again: the group
    // now really is in progress, so it stays; hiding resumes for whatever is still terminal.
    const fresh = [
      PROJECT,
      MODULE,
      n({ id: 'p.default.0449', parent_id: 'module:p:default', node_type: 'group', label: 'G', is_final_approved: false }),
      DOC_0449,
      n({ id: 'p.default.0448', parent_id: 'module:p:default', node_type: 'group', label: 'Other', is_final_approved: true }),
    ]
    getRequest.mockResolvedValueOnce(ok(fresh))
    await wrapper.find('[data-test="explorer-refresh-retry"]').trigger('click')
    await flushPromises()

    expect(wrapper.find('[data-test="explorer-refresh-error"]').exists()).toBe(false)
    expect(renderedLabels(wrapper)).toContain('G')
    expect(renderedLabels(wrapper)).not.toContain('Other')
  })
})
