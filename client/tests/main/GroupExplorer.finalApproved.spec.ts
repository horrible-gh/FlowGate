/**
 * GroupExplorer — the completed/discarded visibility toggle, now against the SERVER variant.
 *
 * 0454 T0006: the hidden state no longer means "fetch everything and filter it out on the
 * client". The explorer asks the server for `include_terminal=false` and gets a tree with the
 * terminal (final-approved / discarded) groups and their descendants already gone; pressing
 * the toggle re-fetches the `include_terminal=true` variant. So every case below is driven by
 * TWO server payloads, and each assertion says which variant the request asked for — a test
 * that only checked the rendered ids would pass just as happily with the old
 * "always fetch the full tree" behaviour.
 */
import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import i18n from '@shared/i18n'
import GroupExplorer from '@main/components/GroupExplorer.vue'
import { useExplorerStore } from '@main/stores/explorer'

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

// project > module > { G1 final-approved, G2 in-progress, G3 no-flag (legacy server),
//                      G4 discarded }
const FULL_NODES = [
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

const TERMINAL_IDS = [
  'p.default.0001', 'p.default.0001.0001-R',
  'p.default.0004', 'p.default.0004.0003-DC',
]

/** What the server answers for `include_terminal=false`: the terminal subtrees are simply
 *  not in the payload. Written out as a filter of FULL_NODES so the two fixtures cannot
 *  drift apart, and asserted to actually be shorter below. */
const PRUNED_NODES = FULL_NODES.filter((node) => !TERMINAL_IDS.includes(node.id as string))

const ok = (nodes: unknown[]) => ({ data: { data: { nodes } } })

/** Every group-tree URL the component has requested, in order. */
function requestedUrls(): string[] {
  return getRequest.mock.calls.map((call) => String(call[0]))
}

/** true/false, per request, read off the URL the store actually built. */
function requestedVariants(): boolean[] {
  return requestedUrls().map((url) => /[?&]include_terminal=true(&|$)/.test(url))
}

/** The server: answers each request with the variant its URL asked for. */
function serveBothVariants() {
  getRequest.mockImplementation((url: string) =>
    Promise.resolve(ok(/[?&]include_terminal=true(&|$)/.test(url) ? FULL_NODES : PRUNED_NODES)),
  )
}

const GroupTreeNodeStub = {
  name: 'GroupTreeNode',
  // 0454 T0004 — GroupExplorer also passes childrenIndex/treeChildrenIndex (the
  // parent->children Map); declared here so the stub matches the real prop contract,
  // even though this file's assertions only read allNodes.
  props: ['node', 'allNodes', 'treeNodes', 'childrenIndex', 'treeChildrenIndex', 'projectId'],
  template: '<li class="stub-node" />',
  emits: ['open', 'tree-changed', 'create-requirement'],
}

async function mountExplorer() {
  serveBothVariants()
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

async function clickToggle(wrapper: ReturnType<typeof mount>) {
  await toggleBtn(wrapper).trigger('click')
  await flushPromises()
}

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason?: unknown) => void
  const promise = new Promise<T>((res, rej) => { resolve = res; reject = rej })
  return { promise, resolve, reject }
}

const timeout = () =>
  Object.assign(new Error('timeout of 130000ms exceeded'), { code: 'ECONNABORTED' })

beforeEach(() => {
  setActivePinia(createPinia())
  localStorage.clear()
  getRequest.mockReset()
})

describe('GroupExplorer — the fixtures', () => {
  it('the pruned payload really is shorter than the full one', () => {
    // Guards the guard: if PRUNED_NODES ever stopped dropping anything, every "hidden"
    // assertion below would pass for the wrong reason.
    expect(PRUNED_NODES).toHaveLength(FULL_NODES.length - TERMINAL_IDS.length)
    expect(PRUNED_NODES.map((x) => x.id)).not.toContain('p.default.0001')
    expect(PRUNED_NODES.map((x) => x.id)).not.toContain('p.default.0004')
  })
})

describe('GroupExplorer final-approved group filter', () => {
  it('asks the server for the PRUNED variant by default, and renders it', async () => {
    const wrapper = await mountExplorer()

    // The request itself is the contract: hidden state must not pull the terminal subtrees
    // over the wire only to filter them out afterwards (0454 T0006 §3.1).
    expect(requestedVariants()).toEqual([false])
    expect(requestedUrls()[0]).toContain('include_terminal=false')
    expect(requestedUrls()[0]).toContain('branch=main')

    const ids = visibleIds(wrapper)
    // final-approved group and its documents are absent from the payload AND the screen
    expect(ids).not.toContain('p.default.0001')
    expect(ids).not.toContain('p.default.0001.0001-R')
    // in-progress group, legacy no-flag group, and the module node all remain
    expect(ids).toContain('p.default.0002')
    expect(ids).toContain('p.default.0003')
    expect(ids).toContain('module:p:default')
    expect(toggleBtn(wrapper).attributes('aria-pressed')).toBe('true')
  })

  it('a successful hidden load contains no terminal node at all', async () => {
    const wrapper = await mountExplorer()
    const store = useExplorerStore()
    // Not just "not rendered": the nodes the store kept for this variant have none of them.
    const cached = store.getCachedGroupTree('p', false) ?? []
    expect(cached.map((x) => x.id)).toEqual(PRUNED_NODES.map((x) => x.id))
    expect(store.getCachedGroupTree('p', true)).toBeUndefined()
    expect(visibleIds(wrapper)).not.toContain('p.default.0004')
  })

  it('toggling on force-fetches the FULL variant and shows every group', async () => {
    const wrapper = await mountExplorer()
    await clickToggle(wrapper)

    expect(requestedVariants()).toEqual([false, true])
    expect(requestedUrls()[1]).toContain('include_terminal=true')

    const ids = visibleIds(wrapper)
    expect(ids).toContain('p.default.0001')
    expect(ids).toContain('p.default.0001.0001-R')
    expect(ids).toContain('p.default.0002')
    expect(toggleBtn(wrapper).attributes('aria-pressed')).toBe('false')
  })

  it('toggling back off fetches the pruned variant again and removes them', async () => {
    const wrapper = await mountExplorer()
    await clickToggle(wrapper)
    await clickToggle(wrapper)

    expect(requestedVariants()).toEqual([false, true, false])
    const ids = visibleIds(wrapper)
    expect(ids).not.toContain('p.default.0001')
    expect(ids).not.toContain('p.default.0004')
    expect(ids).toContain('p.default.0002')
    expect(toggleBtn(wrapper).attributes('aria-pressed')).toBe('true')
  })

  it('persists the setting per project in localStorage', async () => {
    const wrapper = await mountExplorer()
    // default is hidden; toggling on must store the shown state explicitly
    await clickToggle(wrapper)
    expect(localStorage.getItem('flowgate:show-final-approved-groups:p')).toBe('1')
  })

  it('a stored "shown" setting makes the FIRST request the full variant', async () => {
    localStorage.setItem('flowgate:show-final-approved-groups:p', '1')
    const wrapper = await mountExplorer()

    // The restored setting has to be read before the initial fetch, or the first paint
    // would be missing the completed groups the toggle already claims to be showing.
    expect(requestedVariants()).toEqual([true])
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
    await clickToggle(wrapper)
    ids = visibleIds(wrapper)
    expect(ids).toContain('p.default.0001') // final-approved (AC)
    expect(ids).toContain('p.default.0004') // discarded (DC)
    expect(ids).toContain('p.default.0004.0003-DC')
  })

  it('a background refresh keeps asking for the CURRENT toggle state', async () => {
    const wrapper = await mountExplorer()
    await clickToggle(wrapper)

    await wrapper.setProps({ refreshToken: 1 })
    await flushPromises()

    expect(requestedVariants()).toEqual([false, true, true])
    expect(visibleIds(wrapper)).toContain('p.default.0001')
  })
})

describe('GroupExplorer — toggle re-fetch failures and races', () => {
  it('a failed toggle re-fetch keeps the rendered tree and offers a retry', async () => {
    const wrapper = await mountExplorer()
    const before = visibleIds(wrapper)

    getRequest.mockReset()
    getRequest.mockRejectedValue(timeout())
    await toggleBtn(wrapper).trigger('click')
    await new Promise((r) => setTimeout(r, 900)) // getTreeWithRetry's single backoff
    await flushPromises()

    // Not the blocking error screen: the tree is still there, unchanged, with a retry.
    expect(wrapper.find('.tree-ul').exists()).toBe(true)
    expect(wrapper.find('.sdb-state--error').exists()).toBe(false)
    expect(wrapper.find('[data-test="explorer-refresh-error"]').exists()).toBe(true)
    expect(visibleIds(wrapper)).toEqual(before)

    // …and the retry, once the server answers, delivers the variant the toggle now wants.
    serveBothVariants()
    await wrapper.find('[data-test="explorer-refresh-retry"]').trigger('click')
    await flushPromises()
    expect(wrapper.find('[data-test="explorer-refresh-error"]').exists()).toBe(false)
    expect(visibleIds(wrapper)).toContain('p.default.0001')
  })

  it('a slow OLDER toggle response does not overwrite the newest one', async () => {
    const wrapper = await mountExplorer()

    // Click on: its request hangs. Click off: its request answers immediately. The screen
    // must end up on the pruned tree the user last asked for, even though the "on" response
    // lands afterwards.
    const slowFull = deferred<unknown>()
    getRequest.mockReset()
    getRequest.mockImplementationOnce(() => slowFull.promise)
    getRequest.mockImplementation((url: string) =>
      Promise.resolve(ok(/include_terminal=true/.test(url) ? FULL_NODES : PRUNED_NODES)),
    )

    await toggleBtn(wrapper).trigger('click')   // -> true, hangs
    await toggleBtn(wrapper).trigger('click')   // -> false, resolves
    await flushPromises()

    expect(visibleIds(wrapper)).not.toContain('p.default.0001')

    // The stale "shown" payload arrives late and must be dropped on the floor.
    slowFull.resolve(ok(FULL_NODES))
    await flushPromises()

    expect(toggleBtn(wrapper).attributes('aria-pressed')).toBe('true')
    expect(visibleIds(wrapper)).not.toContain('p.default.0001')
    expect(visibleIds(wrapper)).not.toContain('p.default.0004')
    expect(visibleIds(wrapper)).toContain('p.default.0002')
  })
})

describe('GroupExplorer — revealing a target inside a hidden terminal group', () => {
  it('fetches the FULL variant, turns the toggle on, persists it and expands the ancestors', async () => {
    const wrapper = await mountExplorer()
    const store = useExplorerStore()
    expect(visibleIds(wrapper)).not.toContain('p.default.0001')

    // The reveal request a tree node raises (e.g. after creating a document in a group that
    // has since been finally approved). The target is absent from the pruned variant, so a
    // reveal that trusted the current display state would find nothing to expand.
    wrapper.findComponent(GroupTreeNodeStub).vm.$emit('tree-changed', 'p.default.0001.0001-R')
    await flushPromises()

    expect(requestedVariants()).toEqual([false, true])
    // Reveal outranks the hide setting: the toggle flips on and is written through.
    expect(localStorage.getItem('flowgate:show-final-approved-groups:p')).toBe('1')
    expect(toggleBtn(wrapper).attributes('aria-pressed')).toBe('false')
    // The full payload is what is on screen, and the ancestors are open.
    expect(visibleIds(wrapper)).toContain('p.default.0001.0001-R')
    expect(store.isGroupNodeExpanded('p', 'p.default.0001')).toBe(true)
    expect(store.isGroupNodeExpanded('p', 'module:p:default')).toBe(true)
  })

  it('a reveal into a still-visible group does not flip the toggle', async () => {
    const wrapper = await mountExplorer()

    wrapper.findComponent(GroupTreeNodeStub).vm.$emit('tree-changed', 'p.default.0002.0001-R')
    await flushPromises()

    // The reveal still reads the full variant (it must be able to reach anything), but the
    // hide setting is only forced on when the target actually sits inside a terminal group.
    expect(requestedVariants()).toEqual([false, true])
    expect(localStorage.getItem('flowgate:show-final-approved-groups:p')).toBeNull()
    expect(toggleBtn(wrapper).attributes('aria-pressed')).toBe('true')
  })
})
