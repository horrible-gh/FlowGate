/**
 * flowgate.v02gui.0027 (0008-T) — Delete Group (empty Groups only) on the client: the Group menu part (danger item, one line,
 * no hint under it — v02test.0027 0005-T), the Explorer display tree / ⋯ target carrying `flowTotal`, and the store /
 * API path (DELETE, 409 `module_not_empty` with its count). The Shell end to end (window, Archive instead, refresh) is in
 * contextMenuServerActions.0060.spec.ts; the server side in server/tests/.../test_group_delete_v02gui_0027.py.
 */
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import { useProjectStore, type GroupTreeNode } from '@/features/project'
import { ProjectExplorer } from '@/features/designer'
import { projectTreeMenu } from '@/features/designer/contextMenu/projectTreeMenu'
import { contextMenuMessages } from '@/features/designer/contextMenu/messages'
import { menuActionMessages } from '@/features/designer/contextMenu/attach/actionMessages'
import { buildExplorerProject } from '@/features/designer/projectExplorer/projectExplorerTree'
import type { MenuAction } from '@/features/designer/contextMenu/types'

const getRequest = vi.hoisted(() => vi.fn())
const postRequest = vi.hoisted(() => vi.fn())
const deleteRequest = vi.hoisted(() => vi.fn())
vi.mock('@shared/api', () => ({
  default: { delete: deleteRequest },
  getRequest,
  postRequest,
  extractApiErrorMessage: (_error: unknown, fallback: string) => fallback,
}))

registerAppMessages(i18n)

const ALL = ['group.newFlow', 'group.rename', 'group.info', 'group.archive', 'group.restore', 'group.delete'] as const
const keysOf = (model: ReturnType<typeof projectTreeMenu>) => model.items.map((item) => (item.kind === 'separator' ? '|' : item.key))
const deleteItem = (model: ReturnType<typeof projectTreeMenu>) =>
  model.items.find((item): item is MenuAction => item.kind === 'action' && item.key === 'group.delete')
const group = { kind: 'group' as const, projectId: 'p', groupId: 'module:p:ui' }

describe('v02gui.0027 0008-T — the Group menu part', () => {
  it('ends with 그룹 삭제 (danger) after its own separator, enabled, with no hint line, whatever the Flow count', () => {
    const empty = projectTreeMenu({ target: group, available: { currentProject: true }, connected: ALL })
    expect(keysOf(empty)).toEqual(['group.newFlow', '|', 'group.rename', 'group.info', '|', 'group.archive', '|', 'group.delete'])
    expect(deleteItem(empty)).toMatchObject({ danger: true, disabled: false, icon: '×', labelKey: 'groupDelete' })
    expect('hint' in deleteItem(empty)!).toBe(false)
    expect(empty.request('group.delete')).toEqual({ command: 'group.delete', target: group })
    // A Group with Flows is the same menu (no count / cannot-delete line); pressing it opens the refusal window (Archive instead).
    // The menu no longer takes the Flow count at all, so a target carrying `flowTotal` changes nothing here.
    const busy = projectTreeMenu({ target: { ...group, flowTotal: 3 } as typeof group, available: { currentProject: true }, connected: ALL })
    expect(deleteItem(busy)).toMatchObject({ disabled: false })
    expect('hint' in deleteItem(busy)!).toBe(false)
    expect(keysOf(busy)).toEqual(keysOf(empty))
    expect(busy.request('group.delete')?.command).toBe('group.delete')
    // An archived Group keeps the same rule.
    const archived = projectTreeMenu({ target: { ...group, archived: true }, available: { currentProject: true, groupArchived: true }, connected: ALL })
    expect(keysOf(archived).slice(-3)).toEqual(['group.restore', '|', 'group.delete'])
  })

  it('is hidden where not connected; never for the "All" bucket; off in another project', () => {
    const unconnected = projectTreeMenu({ target: group, available: { currentProject: true }, connected: ALL.filter((c) => c !== 'group.delete') })
    expect(keysOf(unconnected)).not.toContain('group.delete')
    expect(keysOf(unconnected).at(-1)).toBe('group.archive')
    const bucket = projectTreeMenu({ target: group, available: { currentProject: true, groupArchivable: false }, connected: ALL })
    expect(keysOf(bucket)).toEqual(['group.newFlow', '|', 'group.rename', 'group.info'])
    const otherProject = projectTreeMenu({ target: group, available: { currentProject: false }, connected: ALL })
    expect(otherProject.request('group.delete')).toBeNull()
  })

  it.each(['ko', 'en', 'ja'] as const)('has its label and the window strings in %s, and no hint lines', (locale) => {
    const m = contextMenuMessages[locale] as typeof contextMenuMessages.ko
    expect(m.groupDelete).toBeTruthy()
    expect('groupDeleteHint' in m).toBe(false)
    const w = menuActionMessages[locale].groupDelete
    for (const key of ['title', 'submit', 'archiveInstead', 'note', 'hint', 'blockedArchived'] as const) expect(w[key]).toBeTruthy()
    expect(w.blocked).toContain('{n}')
  })

  it('says the confirmed wording in Korean', () => {
    expect(contextMenuMessages.ko.groupDelete).toBe('그룹 삭제')
    expect(menuActionMessages.ko.groupDelete.blocked).toBe('Flow {n}개가 남아 있어 삭제할 수 없습니다. 먼저 아카이브하세요.')
    expect(menuActionMessages.ko.groupDelete.archiveInstead).toBe('대신 아카이브')
  })
})

describe('v02gui.0027 0008-T — the Explorer display tree', () => {
  const node = (partial: Partial<GroupTreeNode> & Pick<GroupTreeNode, 'id' | 'parentId' | 'kind'>): GroupTreeNode =>
    ({ label: partial.id, title: null, number: null, ...partial })
  it('carries the server Flow count, not the number of Flows the filtered tree lists', () => {
    const project = buildExplorerProject([
      node({ id: 'project:p', parentId: null, kind: 'project', label: 'Alpha' }),
      node({ id: 'module:p:ui', parentId: 'project:p', kind: 'module', label: 'ui', flowTotal: 3 }),
      node({ id: 'p.ui.0001', parentId: 'module:p:ui', kind: 'group', label: 'Designer' }),
      node({ id: 'module:p:empty', parentId: 'project:p', kind: 'module', label: 'empty', flowTotal: 0 }),
      node({ id: 'module:p:legacy', parentId: 'project:p', kind: 'module', label: 'legacy' }),
    ], 'project:p')!
    expect(project.groups.map((g) => [g.moduleName, g.flows.length, g.flowTotal])).toEqual([
      ['ui', 1, 3], ['empty', 0, 0], ['legacy', 0, undefined],
    ])
    expect('flowTotal' in project.groups[2]).toBe(false)
  })
})

function raw(partial: Record<string, unknown>) {
  return { type_code: null, number: null, filename: null, has_md: false, md_path: null, ...partial }
}
const NODES = [
  raw({ id: 'project:p', parent_id: null, node_type: 'project', label: 'Alpha' }),
  raw({ id: 'module:p:ui', parent_id: 'project:p', node_type: 'module', label: 'ui', title: 'UI 개선', flow_total: 3 }),
  raw({ id: 'p.ui.0001', parent_id: 'module:p:ui', node_type: 'group', label: 'Designer v0.2', number: '0001' }),
  raw({ id: 'module:p:empty', parent_id: 'project:p', node_type: 'module', label: 'empty', title: '빈 그룹', flow_total: 0 }),
]
const ok = (nodes: unknown[]) => ({ data: { data: { nodes } } })

let wrapper: VueWrapper<any> | null = null
beforeEach(() => {
  setActivePinia(createPinia())
  localStorage.clear()
  i18n.global.locale.value = 'ko'
  getRequest.mockReset()
  postRequest.mockReset()
  deleteRequest.mockReset()
})
afterEach(() => {
  wrapper?.unmount()
  wrapper = null
  document.body.innerHTML = ''
})

describe('v02gui.0027 0008-T — the Group row hands its Flow count to the menu', () => {
  it('the ⋯ target carries flowTotal from the tree read', async () => {
    getRequest.mockResolvedValue(ok(NODES))
    const menu = { project: vi.fn(() => true) }
    wrapper = mount(ProjectExplorer, {
      props: { projectId: 'p' },
      attachTo: document.body,
      global: { plugins: [i18n], provide: { [Symbol.for('flowgate.designer.contextMenu')]: menu } },
    })
    await flushPromises()
    const row = (label: string) => wrapper!.findAll('[data-test="project-explorer-group"]')
      .find((r) => r.find('[data-test="project-explorer-label"]').text() === label)!
    await row('UI 개선').get('[data-test="project-explorer-group-menu"]').trigger('click')
    await row('빈 그룹').get('[data-test="project-explorer-group-menu"]').trigger('click')
    expect(menu.project.mock.calls.map((call) => (call as unknown[])[1])).toEqual([
      { kind: 'group', projectId: 'p', groupId: 'module:p:ui', archived: false, flowTotal: 3 },
      { kind: 'group', projectId: 'p', groupId: 'module:p:empty', archived: false, flowTotal: 0 },
    ])
  })
})

describe('v02gui.0027 0008-T — the store / API path', () => {
  it('DELETEs the module key and drops every cached tree of the Project', async () => {
    getRequest.mockResolvedValue(ok(NODES))
    deleteRequest.mockResolvedValue({ data: { ok: true, deleted: true } })
    const store = useProjectStore()
    for (const includeArchived of [false, true]) await store.fetchGroupTree('p', { includeArchived })
    expect(store.getCachedGroupTree('p')!.find((n) => n.id === 'module:p:ui')?.flowTotal).toBe(3)
    await expect(store.deleteGroup('p', 'empty')).resolves.toBeUndefined()
    expect(deleteRequest).toHaveBeenCalledTimes(1)
    expect(deleteRequest).toHaveBeenLastCalledWith('/api/v1/projects/p/modules/empty')
    for (const a of [false, true]) expect(store.getCachedGroupTree('p', false, a)).toBeUndefined()
  })

  it('409 module_not_empty carries the code and the Flow count; the cached trees stay', async () => {
    getRequest.mockResolvedValue(ok(NODES))
    const store = useProjectStore()
    await store.fetchGroupTree('p')
    deleteRequest.mockRejectedValue(Object.assign(new Error('HTTP 409'), {
      response: { status: 409, data: { ok: false, http_status: 409, code: 'module_not_empty', flow_count: 3 } } }))
    const error = await store.deleteGroup('p', 'ui').catch((e: unknown) => e)
    expect(store.sourceRefusal(error)).toEqual({ reason: 'invalid', status: 409, code: 'module_not_empty', count: 3 })
    expect(store.getCachedGroupTree('p')).toBeDefined()
    deleteRequest.mockRejectedValue(Object.assign(new Error('HTTP 404'), {
      response: { status: 404, data: { ok: false, code: 'module_not_found' } } }))
    const missing = await store.deleteGroup('p', 'gone').catch((e: unknown) => e)
    expect(store.sourceRefusal(missing)).toEqual({ reason: 'not_found', status: 404, code: 'module_not_found' })
  })

  it('a deleted Group leaves the recent Group list; others stay', () => {
    const store = useProjectStore()
    store.recordRecentGroup({ id: 'module:p:empty', name: '빈 그룹', projectId: 'p' } as never)
    store.recordRecentGroup({ id: 'module:p:ui', name: 'UI 개선', projectId: 'p' } as never)
    store.forgetRecentGroup('module:p:empty')
    expect(store.recentGroups.map((g: { id: string }) => g.id)).toEqual(['module:p:ui'])
  })
})
