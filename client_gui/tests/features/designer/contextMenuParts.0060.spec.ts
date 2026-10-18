import { describe, expect, it } from 'vitest'
import { CARD_RUN_COMMANDS, COMMANDS, type MenuItem, type MenuTarget } from '@/features/designer/contextMenu/types'
import { action, makeMenu, separator, visibleItems } from '@/features/designer/contextMenu/model'
import { canvasMenu, canvasNodeTarget } from '@/features/designer/contextMenu/canvasMenu'
import { projectTreeMenu } from '@/features/designer/contextMenu/projectTreeMenu'
import { fileExplorerMenu } from '@/features/designer/contextMenu/fileExplorerMenu'
import { flowTabMenu } from '@/features/designer/contextMenu/flowTabMenu'
import { paletteMenu } from '@/features/designer/contextMenu/paletteMenu'
import { contextMenuMessages } from '@/features/designer/contextMenu/messages'
import { actions, definitions, fileAvailable, firstConnected, modelFor, targets } from './contextMenuFixtures.0060'

// Expected sequence comes from the attached mockup, including separator positions.
const sequences = [
  ['project.newGroup', '|', 'project.info'],
  ['group.newFlow', '|', 'group.rename', 'group.info'],
  ['flow.rename', '|', 'flow.delete'],
  ['folder.open', '|', 'folder.newFolder', 'folder.newFile', '|', 'folder.rename', 'folder.download', 'folder.upload',
    ['folder.git.changes', 'folder.git.revert', 'folder.git.history'], '|', 'folder.delete'],
  ['file.open', '|', 'file.rename', 'file.download', ['file.git.changes', 'file.git.revert', 'file.git.history'], '|', 'file.delete'],
  ['tab.close', 'tab.closeOthers', 'tab.closeAll'],
  [definitions.map((item) => 'canvas.addCard:' + item.itemId), '|', 'canvas.paste', 'canvas.selectAll', '|', 'canvas.arrange'],
  ['node.detail', '|', 'node.copy', '|', 'node.delete'],
  ['multi.editSettings', '|', 'multi.copy', 'multi.arrange', '|', 'multi.delete'],
  ['edge.disconnect'],
  ['palette.addCard'],
]
const outline = (items: readonly MenuItem[]) => items.map((item) =>
  item.kind === 'separator' ? '|' : item.kind === 'submenu' ? item.children.map((child) => child.key) : item.key)
const glyphs = ['＋|ⓘ', '＋|✎ⓘ', '✎|×', '↗|＋＋|✎↓↑⑂|×', '↗|✎↓⑂|×', '×⊟☒', '＋|▣▢|≡', 'ⓘ|▣|×', '⚙|▣≡|×', '⌁', '＋']

describe('five independent menu parts / eleven kinds (0060)', () => {
  it.each(targets.map((target, i) => [target.kind, target, i] as const))('%s follows the complete mockup without performing commands', (_, target, i) => {
    const before = JSON.stringify(target)
    const model = modelFor(target)
    expect(outline(model.items)).toEqual(sequences[i])
    expect(model.items.map((item) => item.kind === 'separator' ? '|' : item.icon).join('')).toBe(glyphs[i])
    for (const item of actions(model.items)) {
      expect(!!item.danger).toBe(/delete|revert|disconnect/.test(item.command))
      expect(item.disabled).toBe(false)
      expect(model.request(item.key)).toEqual({
        command: item.command, target,
        ...(item.definition ? { definition: item.definition } : {}),
      })
    }
    expect(JSON.stringify(target)).toBe(before)
    expect(model.request('not-a-command')).toBeNull()
  })
  it('defines exactly forty commands and no permanent-delete or Base restore command', () => {
    const all = new Set(targets.flatMap((target) => actions(modelFor(target).items).map((a) => a.command)))
    expect([...all].sort()).toEqual([...COMMANDS].sort())
    expect(all.size).toBe(40)
    expect([...all].join(' ')).not.toMatch(/permanent|base|untracked/)
  })
  it('all five parts default to no connected commands', () => {
    const models = [
      projectTreeMenu({ target: { kind: 'project', projectId: 'p' }, available: { currentProject: true } }),
      fileExplorerMenu({ target: { kind: 'folder', projectId: 'p', flowId: 'f', path: '', root: true, commit: null }, available: fileAvailable }),
      flowTabMenu({ target: { kind: 'tab', flowId: null, tabIds: [] }, available: { openTabIds: [] } }),
      canvasMenu({ target: { kind: 'node', flowId: 'f', nodeId: 'n' }, available: { editable: true } }),
      paletteMenu({ target: { kind: 'palette', definitionId: 'task' }, available: { canPlace: true } }),
    ]
    expect(models.map((m) => m.items)).toEqual([[], [], [], [], []])
    for (const target of targets) expect(modelFor(target, []).items).toEqual([])
  })
  it('keeps deferred entries hidden in both rollout command lists', () => {
    const later = [...firstConnected, 'group.rename', 'flow.rename', 'flow.delete', 'folder.newFolder', 'folder.newFile',
      'folder.download', 'folder.upload', 'folder.delete', 'file.download', 'file.git.changes', 'file.delete'] as const
    for (const kind of ['flow', 'file', 'multi']) expect(modelFor(targets.find((t) => t.kind === kind)!, firstConnected).items).toEqual([])
    for (const list of [firstConnected, later]) {
      const visible = targets.flatMap((target) => actions(modelFor(target, list).items).map((a) => a.command))
      expect(visible.some((command) => /revert|history|^multi\./.test(command))).toBe(false)
    }
    expect(new Set(later).size).toBe(22)
  })
  it('hides root folder open/rename/delete and empty parents, tidies separators', () => {
    const root = { kind: 'folder', projectId: 'p', flowId: 'f', path: '', root: true, commit: null } as const
    const commands = actions(modelFor(root).items).map((item) => item.command)
    expect(commands).not.toContain('folder.open')
    expect(commands).not.toContain('folder.rename')
    expect(commands).not.toContain('folder.delete')
    expect(modelFor(root, firstConnected).items).toEqual([])
    const items = [separator('a'), action('node.copy', 'copy', '▣'), separator('b'), separator('c'),
      action('node.delete', 'delete', '×'), separator('d')]
    expect(outline(visibleItems(items, ['node.copy', 'node.delete']))).toEqual(['node.copy', '|', 'node.delete'])
    expect(outline(visibleItems(items, ['node.delete']))).toEqual(['node.delete'])
  })
  it.each([
    ['project', { currentProject: false }, ['project.newGroup']],
    ['group', { currentProject: false }, ['group.newFlow', 'group.rename']],
    ['flow', { currentProject: false }, ['flow.delete']],
    ['flow', { disposed: true }, ['flow.rename', 'flow.delete']],
    ['folder', { expanded: true }, ['folder.open']],
    ['folder', { canMutate: false }, ['folder.newFolder', 'folder.newFile', 'folder.upload', 'folder.git.revert', 'folder.delete']],
    ['folder', { virtualDeleted: true }, ['folder.newFolder', 'folder.newFile', 'folder.upload', 'folder.delete']],
    ['folder', { canRead: false }, ['folder.download']],
    ['file', { canMutate: false }, ['file.git.revert', 'file.delete']],
    ['file', { virtualDeleted: true }, ['file.download', 'file.delete']],
    ['file', { canRead: false }, ['file.download']],
    ['file', { hasChanges: false }, ['file.git.changes']],
    ['file', { trackedInHead: false }, ['file.git.revert']],
    ['file', { hasHeadChanges: false }, ['file.git.revert']],
    ['folder', { hasHeadChanges: false }, ['folder.git.revert']],
    ['tab', { openTabIds: ['f'] }, ['tab.closeOthers']],
    ['tab', { openTabIds: [] }, ['tab.close', 'tab.closeOthers', 'tab.closeAll']],
    ['canvas', { editable: false }, ['canvas.addCard']],
    ['canvas', { hasSaveTarget: false }, ['canvas.addCard']],
    ['canvas', { canSelectAll: false }, ['canvas.selectAll']],
    ['canvas', { graphReady: false }, ['canvas.selectAll']],
    ['node', { editable: false }, ['node.delete']],
    ['node', { structureEditable: false }, ['node.delete']],
    ['node', { definitionExists: false }, ['node.delete']],
    ['edge', { editable: false }, ['edge.disconnect']],
    ['edge', { progressed: true }, ['edge.disconnect']],
    ['palette', { canPlace: false }, ['palette.addCard']],
  ])('%s condition %j disables only its affected actions', (kind, override, expected) => {
    const model = modelFor(targets.find((target) => target.kind === kind)!, COMMANDS, override as object)
    expect([...new Set(actions(model.items).filter((item) => item.disabled).map((item) => item.command))]).toEqual(expected)
    for (const item of actions(model.items).filter((entry) => entry.disabled)) expect(model.request(item.key)).toBeNull()
  })
  it.each(['unlinked', 'unavailable', 'no-worktree', 'denied'])('distinguishes Git %s and disables its parent and children', (git) => {
    for (const target of targets.filter((t) => t.kind === 'file' || t.kind === 'folder')) {
      const model = modelFor(target, COMMANDS, { git })
      const parent = model.items.find((item) => item.kind === 'submenu')
      expect(parent).toMatchObject({ disabled: true, disabledReason: git })
      for (const child of actions(model.items).filter((i) => i.command.includes('.git.'))) {
        expect(child).toMatchObject({ disabled: true, disabledReason: git })
        expect(model.request(child.key)).toBeNull()
      }
    }
  })
  it('never infers HEAD changes from Base status and keeps revert scoped to the target Flow/path', () => {
    const target = targets[4]
    const unavailable = modelFor(target, COMMANDS, { hasHeadChanges: undefined, trackedInHead: undefined })
    expect(unavailable.request('file.git.revert')).toBeNull()
    expect(unavailable.request('file.git.changes')).not.toBeNull()
    expect(modelFor(target).request('file.git.revert')).toEqual({ command: 'file.git.revert', target })
    // The request names the one Flow worktree and path only: no Base, branch or other Flow.
    for (const scoped of [targets[3], target]) {
      const request = modelFor(scoped).request(scoped.kind + '.git.revert')!
      expect(Object.keys(request)).toEqual(['command', 'target'])
      expect(request.target).toMatchObject({ projectId: 'p', flowId: 'f' })
      expect(Object.keys(request.target).join(' ')).not.toMatch(/base|branch|ref/i)
    }
    // A deleted tracked file can be restored; ordinary mutation remains disabled.
    const deleted = modelFor(target, COMMANDS, { virtualDeleted: true })
    expect(deleted.request('file.git.revert')).not.toBeNull()
    expect(deleted.request('file.delete')).toBeNull()
  })
  it('keeps empty-tab close entries disabled and all-disabled lists visible', () => {
    const tab = modelFor({ kind: 'tab', flowId: null, tabIds: ['other'] })
    expect(actions(tab.items).filter((item) => item.disabled).map((i) => i.command)).toEqual(['tab.close', 'tab.closeOthers'])
    const edge = modelFor(targets[9], firstConnected, { progressed: true })
    expect(edge.items).toHaveLength(1)
    expect(edge.request('edge.disconnect')).toBeNull()
  })
  it('copies targets, ordered selections, positions and the exact supplied nine card definitions', () => {
    const input = { kind: 'multi', flowId: 'f', nodeId: 'n', selectedNodeIds: ['n2', 'n'] } as const
    const mutable = { ...input, selectedNodeIds: [...input.selectedNodeIds] }
    const menu = modelFor(mutable)
    mutable.selectedNodeIds.reverse()
    expect(menu.target).toEqual(input)
    expect(Object.isFrozen((menu.target as Extract<MenuTarget, { kind: 'multi' }>).selectedNodeIds)).toBe(true)
    const canvas = targets[6] as Extract<MenuTarget, { kind: 'canvas' }>
    const defs = definitions.map((d) => ({ ...d }))
    const model = canvasMenu({ target: canvas, connected: ['canvas.addCard'], definitions: defs, available: { editable: true, hasSaveTarget: true } })
    defs[0].label = 'changed later'
    for (const definition of definitions) expect(model.request('canvas.addCard:' + definition.itemId)?.definition).toEqual(definition)
    expect(model.request('canvas.addCard')).toBeNull()
    expect(definitions.at(-1)?.docType).toBeNull()
  })
  it.each([
    [[], 'n', 'node'], [['n'], 'n', 'node'], [['n', 'n2'], 'n', 'multi'], [['n', 'n2'], 'outside', 'node'],
  ])('classifies membership without changing selection %j', (selection, clicked, kind) => {
    const selected = selection as string[]
    const before = [...selected]
    for (const _selectMode of [true, false]) {
      const target = canvasNodeTarget('f', clicked as string, selected)
      expect(target.kind).toBe(kind)
      expect(selected).toEqual(before)
      if (target.kind === 'multi') {
        expect(target.selectedNodeIds).toEqual(before)
        expect(modelFor(target, firstConnected).items).toEqual([])
      }
    }
  })
  it.each(['ko', 'en', 'ja'] as const)('has local i18n labels for every item in %s', (locale) => {
    const messages = contextMenuMessages[locale]
    for (const target of targets) {
      for (const item of modelFor(target).items) {
        if (item.kind === 'separator') continue
        expect(messages).toHaveProperty(item.labelKey!)
        if (item.kind === 'submenu') for (const child of item.children) if (!child.label) expect(messages).toHaveProperty(child.labelKey!)
      }
    }
  })
  it('rejects a disabled parent request even if a child is enabled', () => {
    const model = makeMenu(targets[0], [{ kind: 'submenu', key: 'x', icon: '⑂', disabled: true,
      children: [action('project.info', 'info', 'ⓘ')] }], COMMANDS)
    expect(model.request('project.info')).toBeNull()
  })
})

// flowgate.v02.0060 T#2: the card's existing run menu (0061 T#15) carried into the single card menu only (§3.6).
describe('card run items in the card menu (T#2)', () => {
  const runItems = [
    { command: 'node.run.holdSet' as const, label: 'Hold 설정', disabled: false, danger: false },
    { command: 'node.run.timeMachine' as const, label: '되돌리기 — 완료', disabled: true, danger: true },
  ]
  const connected = [...firstConnected, ...CARD_RUN_COMMANDS]
  const available = { editable: true, structureEditable: true, definitionExists: true }
  it('keeps the 40 mockup commands apart from the three run commands', () => {
    expect(COMMANDS).toHaveLength(40)
    expect(CARD_RUN_COMMANDS.filter((command) => (COMMANDS as readonly string[]).includes(command))).toEqual([])
  })
  it('appends them after a separator on a single card, worded by the caller', () => {
    const single = canvasMenu({ target: targets[7] as never, available, connected, runItems })
    expect(single.items.map((item) => item.kind === 'separator' ? '|' : item.key)).toEqual(['node.delete', '|', 'node.run.holdSet', 'node.run.timeMachine'])
    expect(actions(single.items).map((item) => [item.label ?? null, item.disabled, item.danger])).toEqual([
      [null, false, true], ['Hold 설정', false, false], ['되돌리기 — 완료', true, true],
    ])
    expect(single.request('node.run.holdSet')).toEqual({ command: 'node.run.holdSet', target: targets[7] })
    expect(single.request('node.run.timeMachine')).toBeNull()
  })
  it('never adds them to a card of a multi-selection: no single card command there, so no item and no menu (§3.6)', () => {
    const multi = canvasMenu({ target: targets[8] as never, available, connected, runItems })
    expect(multi.items).toEqual([])
    expect(multi.request('node.run.holdSet')).toBeNull()
    expect(multi.request('node.run.timeMachine')).toBeNull()
  })
  it('never adds them to the blank Canvas or a line, nor shows them unless connected', () => {
    for (const target of [targets[6], targets[9]]) {
      const model = canvasMenu({ target: target as never, available: { ...available, canSelectAll: true, graphReady: true, hasSaveTarget: true }, connected, runItems, definitions })
      expect(actions(model.items).some((item) => item.command.startsWith('node.run.'))).toBe(false)
    }
    expect(canvasMenu({ target: targets[7] as never, available, connected: firstConnected, runItems }).items.map((item) => item.key)).toEqual(['node.delete'])
  })
})

