/**
 * 0454 T0004 — parent→child index regression for GroupExplorer.vue / GroupTreeNode.vue.
 *
 * NR0003/0454 B0001: releasing the completed-hide toggle on a large tree was slow because
 * every recursive GroupTreeNode re-filtered the FULL node array for its own children
 * (`allNodes.filter(n => n.parent_id === id)`), `disposeDocuments`, and
 * `hasDocumentDescendant`, and GroupExplorer's hidden-descendant pass re-scanned the whole
 * array to a fixed point instead of walking parent→child links once. T0004 replaced all of
 * that with one parent_id→children Map (`buildTreeIndex`/`collectDescendantIds` in
 * `@main/utils/groupTreeIndex`), built once per node-array change and passed unchanged
 * through the recursion.
 *
 * This file:
 *  1. Reproduces the PRE-optimization semantics as plain "legacy oracle" functions (copied
 *     from the component source before this change) and compares them against the new
 *     index-based path across hide on/off × no/single-type filter, on the 0449 load-scale
 *     fixture augmented with terminal groups, nested subgroups and mixed doc types.
 *  2. Proves the new path performs no full-array `.filter`/`.find` scan proportional to node
 *     count (access-count check, not a timing threshold — T0004 explicitly asks not to gate
 *     pass/fail on an arbitrary millisecond figure).
 *  3. Mounts the REAL GroupExplorer/GroupTreeNode recursively on a small representative
 *     fixture and checks rendered DOM row order/labels and expand/collapse.
 *  4. Logs (does not assert on) real old-vs-new wall-clock numbers for the same "release the
 *     hide toggle" recompute on the shared fixture, for the work report.
 */
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'
import GroupExplorer from '@main/components/GroupExplorer.vue'
import { buildTreeIndex, collectDescendantIds, type TreeIndexNode } from '@main/utils/groupTreeIndex'
import { buildLoadNodes } from './fixtures/groupTreeLoadShape'

interface Node extends TreeIndexNode {
  node_type: string
  type_code?: string | null
  is_final_approved?: boolean
  is_discarded?: boolean
  number?: string | null
}

// ── Legacy oracle: the PRE-0454-T0004 component logic, verbatim ────────────────────────────

function legacyIsInsideFinalApprovedGroup(nodeId: string, list: Node[]): boolean {
  let node: Node | undefined = list.find((n) => n.id === nodeId)
  while (node) {
    if (node.node_type === 'group' && (node.is_final_approved === true || node.is_discarded === true)) return true
    const parentId = node.parent_id
    if (!parentId) break
    node = list.find((n) => n.id === parentId)
  }
  return false
}

function legacyBaseNodes(nodes: Node[], showFinalApprovedGroups: boolean, refreshError: boolean): Node[] {
  const hidden = new Set<string>()
  const trustTerminalFlags = !refreshError
  nodes.forEach((n) => {
    if (n.node_type !== 'group') return
    if (trustTerminalFlags && !showFinalApprovedGroups && (n.is_final_approved === true || n.is_discarded === true)) {
      hidden.add(n.id)
    }
  })
  if (hidden.size === 0) return nodes
  let changed = true
  while (changed) {
    changed = false
    nodes.forEach((n) => {
      if (!hidden.has(n.id) && n.parent_id && hidden.has(n.parent_id)) {
        hidden.add(n.id)
        changed = true
      }
    })
  }
  return nodes.filter((n) => !hidden.has(n.id))
}

// `filterTypes` generalizes the single-string `activeFilter` to a set of type codes so this
// oracle can be compared against the new path for BOTH single-type selection (the only thing
// the mounted GroupExplorer.vue UI drives today, one button = one Set entry) and multi-type
// selection (the algorithm itself does not care how many types match — see the TR's
// rejection-response note on the review finding about missing multi-type coverage).
function legacyFilteredNodes(base: Node[], filterTypes: string[] | 'all'): Node[] {
  if (filterTypes === 'all') return base
  const matchTypes = new Set(filterTypes)
  const visibleIds = new Set<string>()
  base.forEach((n) => {
    if (n.node_type === 'document' && n.type_code != null && matchTypes.has(n.type_code)) {
      visibleIds.add(n.id)
      let parentId = n.parent_id
      while (parentId) {
        visibleIds.add(parentId)
        const parent = base.find((p) => p.id === parentId)
        parentId = parent?.parent_id ?? null
      }
    }
  })
  return base.filter((n) => visibleIds.has(n.id))
}

function legacyChildren(allNodes: Node[], nodeId: string): Node[] {
  return allNodes.filter((n) => n.parent_id === nodeId)
}

function legacyDisposeDocuments(allNodes: Node[], nodeId: string) {
  return allNodes
    .filter((n) => n.node_type === 'document' && n.parent_id === nodeId)
    .map((n) => {
      const tc = n.type_code ?? ''
      const seq = (n.number ?? '').split('-')[0]
      return { id: n.id, typeCode: tc, shortId: `${tc}${seq}` }
    })
}

function legacyHasDocumentDescendant(sourceNodes: Node[], nodeId: string): boolean {
  const stack = sourceNodes.filter((n) => n.parent_id === nodeId)
  while (stack.length > 0) {
    const child = stack.pop()
    if (!child) continue
    if (child.node_type === 'document') return true
    stack.push(...sourceNodes.filter((n) => n.parent_id === child.id))
  }
  return false
}

// ── New path: the production index utility, plus the thin per-computed glue that mirrors
//    GroupExplorer.vue / GroupTreeNode.vue's current source exactly. ───────────────────────

function newBaseNodes(nodes: Node[], showFinalApprovedGroups: boolean, refreshError: boolean): Node[] {
  const trustTerminalFlags = !refreshError
  const hiddenRoots: string[] = []
  nodes.forEach((n) => {
    if (n.node_type !== 'group') return
    if (trustTerminalFlags && !showFinalApprovedGroups && (n.is_final_approved === true || n.is_discarded === true)) {
      hiddenRoots.push(n.id)
    }
  })
  if (hiddenRoots.length === 0) return nodes
  const { childrenByParent } = buildTreeIndex(nodes)
  const hidden = collectDescendantIds(childrenByParent, hiddenRoots)
  return nodes.filter((n) => !hidden.has(n.id))
}

// Mirrors GroupExplorer.vue's current filteredNodes computed verbatim, including its
// Set-membership match and the visibleIds-as-visited-set cycle guard (0454 T0004 rev2
// review finding — the ancestor walk had no visited set before this revision).
function newFilteredNodes(base: Node[], filterTypes: string[] | 'all'): Node[] {
  if (filterTypes === 'all') return base
  const { byId } = buildTreeIndex(base)
  const matchTypes = new Set(filterTypes)
  const visibleIds = new Set<string>()
  base.forEach((n) => {
    if (n.node_type === 'document' && n.type_code != null && matchTypes.has(n.type_code)) {
      visibleIds.add(n.id)
      let parentId = n.parent_id
      while (parentId && !visibleIds.has(parentId)) {
        visibleIds.add(parentId)
        const parent = byId.get(parentId)
        parentId = parent?.parent_id ?? null
      }
    }
  })
  return base.filter((n) => visibleIds.has(n.id))
}

/** Mirrors GroupTreeNode.vue's disposeDocuments computed verbatim: an index lookup (not a
 *  full-array filter) against the SAME childrenByParent Map the component receives as its
 *  childrenIndex prop. */
function newDisposeDocuments(childrenByParent: Map<string | null, Node[]>, nodeId: string) {
  return (childrenByParent.get(nodeId) ?? [])
    .filter((n) => n.node_type === 'document')
    .map((n) => {
      const tc = n.type_code ?? ''
      const seq = (n.number ?? '').split('-')[0]
      return { id: n.id, typeCode: tc, shortId: `${tc}${seq}` }
    })
}

/** Mirrors GroupTreeNode.vue's hasDocumentDescendant: walks the SAME Map via .get(), with a
 *  visited set guarding cyclic/malformed parent_id data. */
function newHasDocumentDescendant(childrenByParent: Map<string | null, Node[]>, nodeId: string): boolean {
  const stack = [...(childrenByParent.get(nodeId) ?? [])]
  const visited = new Set<string>()
  while (stack.length > 0) {
    const child = stack.pop()
    if (!child) continue
    if (visited.has(child.id)) continue
    visited.add(child.id)
    if (child.node_type === 'document') return true
    const grandchildren = childrenByParent.get(child.id)
    if (grandchildren) stack.push(...grandchildren)
  }
  return false
}

function idsOf(list: Node[]): string[] {
  return list.map((n) => n.id)
}

function flattenDFS(rootIds: string[], childrenOf: (id: string) => Node[]) {
  const rows: Array<{ id: string; parentId: string | null; depth: number }> = []
  function walk(id: string, parentId: string | null, depth: number) {
    rows.push({ id, parentId, depth })
    for (const child of childrenOf(id)) walk(child.id, id, depth + 1)
  }
  for (const id of rootIds) walk(id, null, 0)
  return rows
}

// ── Fixture: the 0449 load-scale generator, augmented (additively — see fixture options'
//    own doc comment) with terminal groups, a nested subgroup level and 3 doc types. ───────

const FIXTURE = buildLoadNodes({ markTerminal: true, nestSubgroups: true, docTypes: ['T', 'R', 'DS'] }) as Node[]

describe('0454 T0004 — legacy oracle vs index-based implementation', () => {
  it('the augmented fixture actually has terminal groups, subgroups and mixed types', () => {
    expect(FIXTURE.length).toBeGreaterThan(5885)
    expect(FIXTURE.some((n) => n.is_final_approved === true)).toBe(true)
    expect(FIXTURE.some((n) => n.is_discarded === true)).toBe(true)
    expect(FIXTURE.some((n) => n.id.endsWith('.sub'))).toBe(true)
    expect(new Set(FIXTURE.map((n) => n.type_code).filter(Boolean))).toEqual(new Set(['T', 'R', 'DS']))
  })

  // filterTypes generalizes the mounted UI's single-select activeFilter (still just one string
  // per click — no new filter UI in T0004's scope) to a Set of type codes. Single-type cases
  // (one-element array) are exactly what production drives today; the multi-type cases prove
  // the SAME ancestor-tracking algorithm is correct when more than one type matches, which is
  // what T0004 §2's Vitest instruction ("타입 필터 없음, 단일 타입, 복수 타입") asks for even though
  // no button in GroupExplorer.vue can select more than one type at once yet.
  const CASES: Array<{ label: string; show: boolean; filterTypes: string[] | 'all' }> = [
    { label: 'hide off, no filter', show: false, filterTypes: 'all' },
    { label: 'hide on (reveal), no filter', show: true, filterTypes: 'all' },
    { label: 'hide off, single type T', show: false, filterTypes: ['T'] },
    { label: 'hide off, single type DS', show: false, filterTypes: ['DS'] },
    { label: 'hide on, single type R', show: true, filterTypes: ['R'] },
    { label: 'hide off, multi type T+DS', show: false, filterTypes: ['T', 'DS'] },
    { label: 'hide on, multi type T+R+DS (all three at once)', show: true, filterTypes: ['T', 'R', 'DS'] },
  ]

  it.each(CASES)('$label — identical visible set, render order, dispose list and empty-group verdicts', ({ show, filterTypes }) => {
    const legacyBase = legacyBaseNodes(FIXTURE, show, false)
    const modernBase = newBaseNodes(FIXTURE, show, false)
    expect(idsOf(modernBase)).toEqual(idsOf(legacyBase))

    const legacyFiltered = legacyFilteredNodes(legacyBase, filterTypes)
    const modernFiltered = newFilteredNodes(modernBase, filterTypes)
    expect(idsOf(modernFiltered)).toEqual(idsOf(legacyFiltered))

    const modernChildrenIndex = buildTreeIndex(modernFiltered).childrenByParent
    const rootIds = modernFiltered.filter((n) => n.parent_id === null).map((n) => n.id)

    const legacyRows = flattenDFS(rootIds, (id) => legacyChildren(legacyFiltered, id))
    const modernRows = flattenDFS(rootIds, (id) => modernChildrenIndex.get(id) ?? [])
    expect(modernRows).toEqual(legacyRows)

    // disposeDocuments scope = allNodes (post hide+filter); isEmptyGroup/hasDocumentDescendant
    // scope = treeNodes (the full, pre-filter FIXTURE) — same split GroupTreeNode.vue keeps.
    const fullChildrenIndex = buildTreeIndex(FIXTURE).childrenByParent
    for (const n of legacyFiltered) {
      if (n.node_type !== 'group') continue
      // newDisposeDocuments walks modernChildrenIndex — the SAME Map-lookup path
      // GroupTreeNode.vue's disposeDocuments computed uses in production — against the
      // legacy full-array-filter oracle, so this actually exercises the new code path
      // (not two legacy calls compared against each other).
      expect(newDisposeDocuments(modernChildrenIndex, n.id)).toEqual(legacyDisposeDocuments(legacyFiltered, n.id))
      const legacyEmpty = !legacyHasDocumentDescendant(FIXTURE, n.id)
      const modernEmpty = !newHasDocumentDescendant(fullChildrenIndex, n.id)
      expect(modernEmpty).toBe(legacyEmpty)
    }
  })

  it('isInsideFinalApprovedGroup: byId-Map ancestor walk matches the legacy list.find walk', () => {
    const byId = buildTreeIndex(FIXTURE).byId
    // Mirrors GroupExplorer.vue's current isInsideFinalApprovedGroup verbatim, including the
    // visited-set cycle guard (0454 T0004 rev2 review finding — this loop had no visited set
    // before this revision).
    function newIsInsideFinalApprovedGroup(nodeId: string): boolean {
      let node = byId.get(nodeId)
      const visited = new Set<string>()
      while (node) {
        if (visited.has(node.id)) break
        visited.add(node.id)
        if (node.node_type === 'group' && (node.is_final_approved === true || node.is_discarded === true)) return true
        const parentId = node.parent_id
        if (!parentId) break
        node = byId.get(parentId)
      }
      return false
    }
    // Sample every group plus a handful of leaf documents — enough to cover both terminal
    // and non-terminal ancestors without walking all ~5,951 nodes twice per case.
    const sample = FIXTURE.filter((n) => n.node_type === 'group').concat(FIXTURE.slice(-50))
    for (const n of sample) {
      expect(newIsInsideFinalApprovedGroup(n.id)).toBe(legacyIsInsideFinalApprovedGroup(n.id, FIXTURE))
    }
  })

  it('a cyclic parent_id chain terminates instead of looping forever (baseNodes BFS)', () => {
    const cyclic: Node[] = [
      { id: 'a', parent_id: 'b', node_type: 'group' },
      { id: 'b', parent_id: 'a', node_type: 'group', is_final_approved: true },
      { id: 'c', parent_id: 'b', node_type: 'document', type_code: 'T' },
    ]
    const result = newBaseNodes(cyclic, false, false)
    // b is hidden (terminal); the BFS must still terminate and hide its cyclic descendant a
    // and true descendant c, without hanging the test process.
    expect(idsOf(result)).toEqual([])
  })

  it('a cyclic parent_id chain terminates instead of looping forever (hasDocumentDescendant)', () => {
    const cyclic: Node[] = [
      { id: 'a', parent_id: 'b', node_type: 'group' },
      { id: 'b', parent_id: 'a', node_type: 'group' },
    ]
    const { childrenByParent } = buildTreeIndex(cyclic)
    expect(newHasDocumentDescendant(childrenByParent, 'a')).toBe(false)
  })

  // 0454 T0004 rev2 review finding: the prior revision's cyclic-data tests exercised only
  // baseNodes' BFS and hasDocumentDescendant — NOT isInsideFinalApprovedGroup's ancestor climb
  // or filteredNodes' `while (parentId)` ancestor walk, which is exactly where the missing
  // visited-set guard actually lived. These two reproduce the review's a<->b example directly
  // against the (now-fixed) production-mirroring functions above.
  it('a cyclic parent_id chain terminates instead of looping forever (isInsideFinalApprovedGroup ancestor climb)', () => {
    const cyclic: Node[] = [
      { id: 'a', parent_id: 'b', node_type: 'group' },
      { id: 'b', parent_id: 'a', node_type: 'group', is_final_approved: true },
      { id: 'c', parent_id: 'b', node_type: 'document', type_code: 'T' },
    ]
    const byId = buildTreeIndex(cyclic).byId
    function newIsInsideFinalApprovedGroup(nodeId: string): boolean {
      let node = byId.get(nodeId)
      const visited = new Set<string>()
      while (node) {
        if (visited.has(node.id)) break
        visited.add(node.id)
        if (node.node_type === 'group' && (node.is_final_approved === true || node.is_discarded === true)) return true
        const parentId = node.parent_id
        if (!parentId) break
        node = byId.get(parentId)
      }
      return false
    }
    // A reveal target under the a<->b cycle (b is final-approved) must still terminate and
    // report true — this is applyReveal's actual call shape in GroupExplorer.vue.
    expect(newIsInsideFinalApprovedGroup('a')).toBe(true)
    expect(newIsInsideFinalApprovedGroup('c')).toBe(true)
  })

  it('a cyclic parent_id chain terminates instead of looping forever (filteredNodes ancestor walk)', () => {
    const cyclic: Node[] = [
      { id: 'a', parent_id: 'b', node_type: 'group' },
      { id: 'b', parent_id: 'a', node_type: 'group' },
      { id: 'c', parent_id: 'b', node_type: 'document', type_code: 'T' },
    ]
    // A type-matching document ('c') whose ancestors form an a<->b cycle: the ancestor walk must
    // terminate instead of looping forever, and both cyclic ancestors end up visible alongside
    // the matched document (same as a well-formed ancestor chain would).
    const result = newFilteredNodes(cyclic, ['T'])
    expect(new Set(idsOf(result))).toEqual(new Set(['a', 'b', 'c']))
  })
})

describe('0454 T0004 — structural performance: no full-array scan per node', () => {
  function withArrayScanCounter<T>(fn: () => T): { result: T; scanCalls: number } {
    let scanCalls = 0
    const origFilter = Array.prototype.filter
    const origFind = Array.prototype.find
    // eslint-disable-next-line no-extend-native
    Array.prototype.filter = function (this: unknown[], ...args: Parameters<typeof origFilter>) {
      scanCalls += 1
      return origFilter.apply(this, args)
    } as typeof origFilter
    // eslint-disable-next-line no-extend-native
    Array.prototype.find = function (this: unknown[], ...args: Parameters<typeof origFind>) {
      scanCalls += 1
      return origFind.apply(this, args)
    } as typeof origFind
    try {
      const result = fn()
      return { result, scanCalls }
    } finally {
      Array.prototype.filter = origFilter
      Array.prototype.find = origFind
    }
  }

  it('legacy oracle: full-array .filter/.find calls scale with node/group count', () => {
    const { scanCalls } = withArrayScanCounter(() => {
      const base = legacyBaseNodes(FIXTURE, true, false)
      for (const n of base) {
        legacyChildren(base, n.id)
        if (n.node_type === 'group') legacyHasDocumentDescendant(FIXTURE, n.id)
      }
    })
    // One .filter/.find per node just for `children`, plus more inside
    // hasDocumentDescendant's own recursion — comfortably more than one call per node.
    expect(scanCalls).toBeGreaterThan(FIXTURE.length)
  })

  it('new index path: reveal recompute performs ZERO full-array .filter/.find scans', () => {
    const { scanCalls } = withArrayScanCounter(() => {
      const base = newBaseNodes(FIXTURE, true, false)
      const { childrenByParent } = buildTreeIndex(base)
      const fullChildrenByParent = buildTreeIndex(FIXTURE).childrenByParent
      for (const n of base) {
        childrenByParent.get(n.id) ?? []
        if (n.node_type === 'group') newHasDocumentDescendant(fullChildrenByParent, n.id)
      }
    })
    // buildTreeIndex is a single for-of pass (Map.set), collectDescendantIds/get/stack walks
    // are Map lookups — none of this touches Array.prototype.filter/find.
    expect(scanCalls).toBe(0)
  })

  it('buildTreeIndex does one pass regardless of how many times children are looked up', () => {
    const { childrenByParent } = buildTreeIndex(FIXTURE)
    let totalChildren = 0
    for (const n of FIXTURE) totalChildren += (childrenByParent.get(n.id) ?? []).length
    // Every node is either a root (parent_id null, not counted as anyone's child) or exactly
    // one other node's child — so the sum of all children buckets is node count minus roots.
    const rootCount = FIXTURE.filter((n) => n.parent_id === null).length
    expect(totalChildren).toBe(FIXTURE.length - rootCount)
  })
})

describe('0454 T0004 — real-DOM recursive mount on a small representative fixture', () => {
  const { getRequest } = vi.hoisted(() => ({ getRequest: vi.fn() }))
  vi.mock('@shared/api', () => ({
    default: { head: vi.fn(), get: vi.fn(), post: vi.fn(), patch: vi.fn() },
    getRequest,
    patchRequest: vi.fn(),
    postRequest: vi.fn(),
  }))

  function n(partial: Record<string, unknown>) {
    return { type_code: null, number: null, filename: null, has_md: false, md_path: null, ...partial }
  }

  // project > module > { G1 (final-approved, has an R doc), G2 (in-progress, empty),
  //                      G3 (in-progress, has a nested subgroup with a T doc) }
  const SMALL_NODES = [
    n({ id: 'project:p', parent_id: null, node_type: 'project', label: 'P' }),
    n({ id: 'module:p:default', parent_id: 'project:p', node_type: 'module', label: 'default' }),
    n({ id: 'p.default.0001', parent_id: 'module:p:default', node_type: 'group', label: 'G1', is_final_approved: true }),
    n({ id: 'p.default.0001.0001-R', parent_id: 'p.default.0001', node_type: 'document', type_code: 'R', label: '[R]: r1', has_md: true, md_path: 'r1.md' }),
    n({ id: 'p.default.0002', parent_id: 'module:p:default', node_type: 'group', label: 'G2', is_final_approved: false }),
    n({ id: 'p.default.0003', parent_id: 'module:p:default', node_type: 'group', label: 'G3', is_final_approved: false }),
    n({ id: 'p.default.0003.sub', parent_id: 'p.default.0003', node_type: 'group', label: 'G3-sub', is_final_approved: false }),
    n({ id: 'p.default.0003.sub.0001-T', parent_id: 'p.default.0003.sub', node_type: 'document', type_code: 'T', label: '[T]: t1', has_md: true, md_path: 't1.md' }),
  ]

  async function clickRow(wrapper: ReturnType<typeof mount>, label: string): Promise<boolean> {
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

  beforeEach(() => {
    setActivePinia(createPinia())
    localStorage.clear()
    getRequest.mockReset()
  })

  it('expands the nested subgroup and renders every row in DFS order with correct depth-driven indentation source', async () => {
    getRequest.mockResolvedValue({ data: { data: { nodes: SMALL_NODES } } })
    const wrapper = mount(GroupExplorer, {
      props: { projectId: 'p' },
      global: { plugins: [i18n] },
    })
    await flushPromises()

    await clickRow(wrapper, 'P')
    await clickRow(wrapper, 'default')
    // G1 is final-approved and hidden by default — only G2 and G3 show.
    let labels = wrapper.findAll('.tree-lbl').map((el) => el.text())
    expect(labels).toContain('G2')
    expect(labels).toContain('G3')
    expect(labels).not.toContain('G1')

    await clickRow(wrapper, 'G3')
    await clickRow(wrapper, 'G3-sub')
    labels = wrapper.findAll('.tree-lbl').map((el) => el.text())
    // Full DFS render order down to the nested subgroup's document.
    const idx = (text: string) => labels.indexOf(text)
    expect(idx('G3')).toBeGreaterThan(-1)
    expect(idx('G3-sub')).toBeGreaterThan(idx('G3'))
    expect(idx('[T]: t1')).toBeGreaterThan(idx('G3-sub'))
  })

  it('reveals G1 on toggle and its disposeDocuments/isEmptyGroup verdicts hold through the real component', async () => {
    getRequest.mockResolvedValue({ data: { data: { nodes: SMALL_NODES } } })
    const wrapper = mount(GroupExplorer, {
      props: { projectId: 'p' },
      global: { plugins: [i18n] },
    })
    await flushPromises()
    await wrapper.find('button[aria-pressed]').trigger('click')
    await flushPromises()

    await clickRow(wrapper, 'P')
    await clickRow(wrapper, 'default')
    const labels = wrapper.findAll('.tree-lbl').map((el) => el.text())
    expect(labels).toContain('G1')

    await clickRow(wrapper, 'G1')
    expect(wrapper.findAll('.tree-lbl').map((el) => el.text())).toContain('[R]: r1')

    // G2 is empty (no document descendant) — its context menu must offer "new requirement".
    // ContextMenu <Teleport>s to <body>, outside the wrapper's own DOM subtree, so the
    // assertion has to look at document.body rather than wrapper.find.
    const g2Row = wrapper.findAll('.tree-row').find((row) => row.find('.tree-lbl').text() === 'G2')
    await g2Row!.trigger('contextmenu')
    await flushPromises()
    const menuItems = Array.from(document.body.querySelectorAll('.ctx-item')).map((el) => el.textContent)
    expect(menuItems.some((text) => text?.includes('New Starting Document'))).toBe(true)
  })
})

describe('0454 T0004 — real numbers: old vs new release-the-hide-toggle recompute', () => {
  function oldRevealPass(nodes: Node[]): void {
    const base = legacyBaseNodes(nodes, true, false)
    for (const n of base) {
      legacyChildren(base, n.id)
      if (n.node_type === 'group') legacyHasDocumentDescendant(nodes, n.id)
    }
  }

  function newRevealPass(nodes: Node[]): void {
    const base = newBaseNodes(nodes, true, false)
    const { childrenByParent } = buildTreeIndex(base)
    const fullChildrenByParent = buildTreeIndex(nodes).childrenByParent
    for (const n of base) {
      childrenByParent.get(n.id) ?? []
      if (n.node_type === 'group') newHasDocumentDescendant(fullChildrenByParent, n.id)
    }
  }

  function median(samples: number[]): number {
    const sorted = [...samples].sort((a, b) => a - b)
    const mid = Math.floor(sorted.length / 2)
    return sorted.length % 2 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2
  }

  // Not a pass/fail gate on any millisecond figure (T0004 explicitly asks not to write one) —
  // this test only MEASURES and logs. It fails only if the recompute throws, which would
  // indicate the passes are not comparable.
  it('logs median wall-clock over 7 reps on the shared augmented fixture', () => {
    const REPS = 7
    // One untimed warm-up rep each so V8 JIT warm-up does not skew the first measured rep.
    oldRevealPass(FIXTURE)
    newRevealPass(FIXTURE)

    const oldTimes: number[] = []
    for (let i = 0; i < REPS; i += 1) {
      const t0 = performance.now()
      oldRevealPass(FIXTURE)
      oldTimes.push(performance.now() - t0)
    }
    const newTimes: number[] = []
    for (let i = 0; i < REPS; i += 1) {
      const t0 = performance.now()
      newRevealPass(FIXTURE)
      newTimes.push(performance.now() - t0)
    }

    const oldMedian = median(oldTimes)
    const newMedian = median(newTimes)
    // eslint-disable-next-line no-console
    console.log(
      "[0454 T0004 benchmark] fixture=" + FIXTURE.length + " nodes, " + REPS + " reps " +
      "— OLD median=" + oldMedian.toFixed(3) + "ms (all: " + oldTimes.map((t) => t.toFixed(2)).join(", ") + ") " +
      "— NEW median=" + newMedian.toFixed(3) + "ms (all: " + newTimes.map((t) => t.toFixed(2)).join(", ") + ")",
    )
    expect(Number.isFinite(oldMedian)).toBe(true)
    expect(Number.isFinite(newMedian)).toBe(true)
  })
})
