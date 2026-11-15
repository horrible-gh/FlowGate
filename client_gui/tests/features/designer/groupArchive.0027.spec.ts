/**
 * flowgate.v02gui.0027 (0006-T) — Group archive / restore on the client: the Group menu part, the Explorer display tree,
 * the Group row's ⋯ button and the store / API path. The Shell end to end (window, refresh, restore) is in
 * contextMenuServerActions.0060.spec.ts; the server side in server/tests/.../test_group_archive_v02gui_0027.py.
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

const getRequest = vi.hoisted(() => vi.fn())
const postRequest = vi.hoisted(() => vi.fn())
vi.mock('@shared/api', () => ({
  getRequest,
  postRequest,
  extractApiErrorMessage: (_error: unknown, fallback: string) => fallback,
}))

registerAppMessages(i18n)

const ALL = ['group.newFlow', 'group.rename', 'group.info', 'group.archive', 'group.restore'] as const
const keysOf = (model: ReturnType<typeof projectTreeMenu>) => model.items.map((item) => (item.kind === 'separator' ? '|' : item.key))
const group = { kind: 'group' as const, projectId: 'p', groupId: 'module:p:ui' }

describe('v02gui.0027 — the Group menu part', () => {
  it('ends with 그룹 아카이브 after a separator; an archived Group offers 그룹 복원 instead', () => {
    const open = projectTreeMenu({ target: group, available: { currentProject: true }, connected: ALL })
    expect(keysOf(open)).toEqual(['group.newFlow', '|', 'group.rename', 'group.info', '|', 'group.archive'])
    expect(open.request('group.archive')).toEqual({ command: 'group.archive', target: group })
    const archived = projectTreeMenu({ target: { ...group, archived: true }, available: { currentProject: true, groupArchived: true }, connected: ALL })
    expect(keysOf(archived)).toEqual(['group.newFlow', '|', 'group.rename', 'group.info', '|', 'group.restore'])
    expect(archived.request('group.archive')).toBeNull()
    expect(archived.request('group.restore')?.command).toBe('group.restore')
  })

  it('is hidden where the host does not connect it, and never offered for the "All" bucket', () => {
    const unconnected = projectTreeMenu({ target: group, available: { currentProject: true }, connected: ['group.newFlow', 'group.rename'] })
    expect(keysOf(unconnected)).toEqual(['group.newFlow', '|', 'group.rename', 'group.info'])
    const bucket = projectTreeMenu({ target: group, available: { currentProject: true, groupArchivable: false }, connected: ALL })
    expect(keysOf(bucket)).toEqual(['group.newFlow', '|', 'group.rename', 'group.info'])
    const otherProject = projectTreeMenu({ target: group, available: { currentProject: false }, connected: ALL })
    expect(otherProject.request('group.archive')).toBeNull()
  })

  it.each(['ko', 'en', 'ja'] as const)('has its labels and window strings in %s', (locale) => {
    expect(contextMenuMessages[locale].groupArchive).toBeTruthy()
    expect(contextMenuMessages[locale].groupRestore).toBeTruthy()
    expect(menuActionMessages[locale].archive.note).toBeTruthy()
    expect(menuActionMessages[locale].archive.server.already).toBeTruthy()
    expect(i18n.global.t('gui.designer.project_explorer.group_menu', {}, { locale })).not.toBe('gui.designer.project_explorer.group_menu')
    expect(i18n.global.t('gui.designer.archive.group_restore_failed', {}, { locale })).not.toBe('gui.designer.archive.group_restore_failed')
  })
})

describe('v02gui.0027 — the Explorer display tree', () => {
  const node = (partial: Partial<GroupTreeNode> & Pick<GroupTreeNode, 'id' | 'parentId' | 'kind'>): GroupTreeNode =>
    ({ label: partial.id, title: null, number: null, ...partial })
  it('marks an archived Group (only) and keeps its Flows under it', () => {
    const project = buildExplorerProject([
      node({ id: 'project:p', parentId: null, kind: 'project', label: 'Alpha' }),
      node({ id: 'module:p:ui', parentId: 'project:p', kind: 'module', label: 'ui', title: 'UI 개선', archivedAt: '2026-11-15' }),
      node({ id: 'p.ui.0001', parentId: 'module:p:ui', kind: 'group', label: 'Designer' }),
      node({ id: 'module:p:core', parentId: 'project:p', kind: 'module', label: 'core' }),
    ], 'project:p')!
    expect(project.groups.map((g) => [g.name, g.archived ?? false, g.flows.map((f) => f.name)])).toEqual([
      ['UI 개선', true, ['Designer']], ['core', false, []],
    ])
    expect('archived' in project.groups[1]).toBe(false)
  })
})

function raw(partial: Record<string, unknown>) {
  return { type_code: null, number: null, filename: null, has_md: false, md_path: null, ...partial }
}
const NODES = [
  raw({ id: 'project:p', parent_id: null, node_type: 'project', label: 'Alpha' }),
  raw({ id: 'module:p:ui', parent_id: 'project:p', node_type: 'module', label: 'ui', title: 'UI 개선' }),
  raw({ id: 'p.ui.0001', parent_id: 'module:p:ui', node_type: 'group', label: 'Designer v0.2', number: '0001' }),
  raw({ id: 'module:p:old', parent_id: 'project:p', node_type: 'module', label: 'old', title: '옛 그룹', archived: true, archived_at: '2026-11-15', archived_by: 'alice' }),
]
const ok = (nodes: unknown[]) => ({ data: { data: { nodes } } })

let wrapper: VueWrapper<any> | null = null
beforeEach(() => {
  setActivePinia(createPinia())
  localStorage.clear()
  i18n.global.locale.value = 'ko'
  getRequest.mockReset()
  postRequest.mockReset()
})
afterEach(() => {
  wrapper?.unmount()
  wrapper = null
  document.body.innerHTML = ''
})

describe('v02gui.0027 — the Group row ⋯ button', () => {
  async function mountExplorer(menu: { project: ReturnType<typeof vi.fn> }) {
    getRequest.mockImplementation(async (_url: string, params: Record<string, string>) =>
      ok(params.include_archived === 'true' ? NODES : NODES.filter((n) => n.id !== 'module:p:old')))
    wrapper = mount(ProjectExplorer, {
      props: { projectId: 'p' },
      attachTo: document.body,
      global: { plugins: [i18n], provide: { [Symbol.for('flowgate.designer.contextMenu')]: menu } },
    })
    await flushPromises()
    return wrapper
  }
  const groupRow = (w: VueWrapper<any>, label: string) =>
    w.findAll('[data-test="project-explorer-group"]').find((row) => row.find('[data-test="project-explorer-label"]').text() === label)!

  it('opens the Group menu anchored at the button, with the row\'s archive state; the row is neither selected nor toggled', async () => {
    const menu = { project: vi.fn(() => true) }
    const w = await mountExplorer(menu)
    const button = groupRow(w, 'UI 개선').get('[data-test="project-explorer-group-menu"]')
    expect(button.attributes('aria-label')).toBe('그룹 메뉴')
    expect(button.attributes('tabindex')).toBe('-1')
    await button.trigger('click')
    await button.trigger('dblclick')
    expect(menu.project).toHaveBeenCalledTimes(1)
    const [event, target, fallback, alive] = menu.project.mock.calls[0] as unknown as [MouseEvent, unknown, HTMLElement, () => boolean]
    expect(event.type).toBe('click')
    expect(target).toEqual({ kind: 'group', projectId: 'p', groupId: 'module:p:ui', archived: false })
    expect(fallback).toBe(button.element)
    expect(alive()).toBe(true)
    expect(groupRow(w, 'UI 개선').attributes('aria-expanded')).toBe('false')
    expect(groupRow(w, 'UI 개선').attributes('aria-selected')).toBe('false')
    expect(w.emitted('select-group')).toBeUndefined()
  })

  it('hides an archived Group by default; "아카이브 포함" shows it dimmed and its ⋯ says it is archived', async () => {
    const menu = { project: vi.fn(() => true) }
    const w = await mountExplorer(menu)
    expect(groupRow(w, '옛 그룹')).toBeUndefined()
    await w.get('[data-test="project-explorer-include-archived"]').setValue(true)
    await flushPromises()
    expect(groupRow(w, '옛 그룹').classes()).toContain('is-archived')
    expect(groupRow(w, 'UI 개선').classes()).not.toContain('is-archived')
    await groupRow(w, '옛 그룹').get('[data-test="project-explorer-group-menu"]').trigger('click')
    expect(menu.project.mock.calls.at(-1)?.[1]).toEqual({ kind: 'group', projectId: 'p', groupId: 'module:p:old', archived: true })
  })
})

describe('v02gui.0027 — the store / API path', () => {
  it('POSTs archive / restore for the module key and drops every cached tree of the Project', async () => {
    getRequest.mockResolvedValue(ok(NODES))
    postRequest.mockResolvedValue({ data: { ok: true, archived: true, archived_at: '2026-11-15' } })
    const store = useProjectStore()
    for (const includeArchived of [false, true]) await store.fetchGroupTree('p', { includeArchived })
    await expect(store.setGroupArchived('p', 'ui', true)).resolves.toEqual({ archived: true, archivedAt: '2026-11-15' })
    expect(postRequest).toHaveBeenLastCalledWith('/api/v1/projects/p/modules/ui/archive', {})
    for (const a of [false, true]) expect(store.getCachedGroupTree('p', false, a)).toBeUndefined()
    postRequest.mockResolvedValue({ data: { ok: true, archived: false, archived_at: null } })
    await expect(store.setGroupArchived('p', 'ui', false)).resolves.toEqual({ archived: false, archivedAt: null })
    expect(postRequest).toHaveBeenLastCalledWith('/api/v1/projects/p/modules/ui/restore', {})
  })

  it('a refusal keeps the cached trees and carries the server code', async () => {
    getRequest.mockResolvedValue(ok(NODES))
    const store = useProjectStore()
    await store.fetchGroupTree('p')
    postRequest.mockRejectedValue(Object.assign(new Error('HTTP 409'), { response: { status: 409, data: { ok: false, code: 'already_archived' } } }))
    const error = await store.setGroupArchived('p', 'ui', true).catch((e: unknown) => e)
    expect(store.sourceRefusal(error)).toMatchObject({ status: 409, code: 'already_archived' })
    expect(store.getCachedGroupTree('p')).toBeDefined()
  })
})
