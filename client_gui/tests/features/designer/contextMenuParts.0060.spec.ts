import { describe, expect, it } from 'vitest'
import { CARD_RUN_COMMANDS, COMMANDS, PLANNED_COMMANDS, type MenuItem, type MenuTarget } from '@/features/designer/contextMenu/types'
import { action, makeMenu, separator, visibleItems } from '@/features/designer/contextMenu/model'
import { canvasMenu, canvasNodeTarget } from '@/features/designer/contextMenu/canvasMenu'
import { projectTreeMenu } from '@/features/designer/contextMenu/projectTreeMenu'
import { fileExplorerMenu } from '@/features/designer/contextMenu/fileExplorerMenu'
import { flowTabMenu } from '@/features/designer/contextMenu/flowTabMenu'
import { paletteMenu } from '@/features/designer/contextMenu/paletteMenu'
import { contextMenuMessages } from '@/features/designer/contextMenu/messages'
import {
  actions, arrangeChoices, arrangeGroups, connectedNow, definitions, fileAvailable, firstConnected, modelFor,
  plannedCommands, targets, unimplementedCommands,
} from './contextMenuFixtures.0060'

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
  item.kind === 'separator' ? '|' : item.kind === 'submenu' ? item.children.map((child) => child.kind === 'separator' ? '|' : child.key) : item.key)
/** [command, disabled, reason] of every action (submenu children included). */
const states = (items: readonly MenuItem[]) => actions(items).map((item) => [item.command, !!item.disabled, item.disabledReason ?? null])
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
  // flowgate.v02.0060 T#1 (Q item 459, replacing "연결되지 않은 명령은 숨김"): nothing connected → every mockup item stays
  // where the mockup puts it, disabled with its reason, and requests nothing.
  it('with no connected command every part still shows the whole mockup, all disabled with 연결 예정 / 미구현 (Q 459)', () => {
    const models = [
      projectTreeMenu({ target: { kind: 'project', projectId: 'p' }, available: { currentProject: true } }),
      fileExplorerMenu({ target: { kind: 'folder', projectId: 'p', flowId: 'f', path: '', root: true, commit: null }, available: fileAvailable }),
      flowTabMenu({ target: { kind: 'tab', flowId: null, tabIds: [] }, available: { openTabIds: [] } }),
      canvasMenu({ target: { kind: 'node', flowId: 'f', nodeId: 'n' }, available: { editable: true } }),
      paletteMenu({ target: { kind: 'palette', definitionId: 'task' }, available: { canPlace: true } }),
    ]
    expect(models.map((m) => outline(m.items))).toEqual([
      ['project.newGroup', '|', 'project.info'],
      ['folder.newFolder', 'folder.newFile', '|', 'folder.download', 'folder.upload', ['folder.git.changes', 'folder.git.revert', 'folder.git.history']],
      ['tab.close', 'tab.closeOthers', 'tab.closeAll'],
      ['node.detail', '|', 'node.copy', '|', 'node.delete'],
      ['palette.addCard'],
    ])
    for (const target of targets) {
      const model = modelFor(target, [])
      expect(outline(model.items)).toEqual(outline(modelFor(target).items))
      for (const item of actions(model.items)) {
        expect(item.disabled, item.key).toBe(true)
        expect(item.disabledReason, item.key).toBe((plannedCommands as readonly string[]).includes(item.command) ? 'planned' : 'unimplemented')
        expect(model.request(item.key)).toBeNull()
      }
    }
  })
  it('T#1 states of the 40: 13 run, 11 연결 예정, 16 미구현; the 2-B list is the part contract', () => {
    expect([...PLANNED_COMMANDS].sort()).toEqual([...plannedCommands].sort())
    expect(new Set([...connectedNow, ...plannedCommands, ...unimplementedCommands]).size).toBe(40)
    expect([...connectedNow, ...plannedCommands, ...unimplementedCommands].sort()).toEqual([...COMMANDS].sort())
    const shown = targets.flatMap((target) => states(modelFor(target, connectedNow).items))
    const byCommand = new Map(shown.map(([command, , reason]) => [command, reason]))
    expect(byCommand.size).toBe(40)
    expect([...byCommand].filter(([, reason]) => reason === null).map(([c]) => c).sort()).toEqual([...connectedNow].sort())
    expect([...byCommand].filter(([, reason]) => reason === 'planned').map(([c]) => c).sort()).toEqual([...plannedCommands].sort())
    expect([...byCommand].filter(([, reason]) => reason === 'unimplemented').map(([c]) => c).sort()).toEqual([...unimplementedCommands].sort())
    // Not one of the 27 requests anything; Git 되돌리기 / 이력 and the multi-selection items included.
    for (const target of targets) {
      const model = modelFor(target, connectedNow)
      for (const item of actions(model.items).filter((entry) => !(connectedNow as readonly string[]).includes(entry.command))) {
        expect(item.disabled).toBe(true)
        expect(model.request(item.key)).toBeNull()
      }
    }
  })
  it('the Flow, file and multi-selection menus are not empty any more; their unconnected items request nothing', () => {
    for (const kind of ['flow', 'file', 'multi']) {
      const model = modelFor(targets.find((t) => t.kind === kind)!, connectedNow)
      expect(model.items.length).toBeGreaterThan(0)
    }
    expect(states(modelFor(targets[2], connectedNow).items)).toEqual([['flow.rename', true, 'planned'], ['flow.delete', true, 'planned']])
    expect(states(modelFor(targets[4], connectedNow).items)).toEqual([
      ['file.open', true, 'unimplemented'], ['file.rename', true, 'unimplemented'], ['file.download', true, 'planned'],
      ['file.git.changes', true, 'planned'], ['file.git.revert', true, 'unimplemented'], ['file.git.history', true, 'unimplemented'],
      ['file.delete', true, 'planned'],
    ])
    expect(modelFor(targets[4], connectedNow).items.find((item) => item.kind === 'submenu')).toMatchObject({ disabled: false })
  })
  it('hides root folder open/rename/delete and empty parents, tidies separators', () => {
    const root = { kind: 'folder', projectId: 'p', flowId: 'f', path: '', root: true, commit: null } as const
    const commands = actions(modelFor(root).items).map((item) => item.command)
    expect(commands).not.toContain('folder.open')
    expect(commands).not.toContain('folder.rename')
    expect(commands).not.toContain('folder.delete')
    // Q 459: the root folder keeps its mockup items (the 2-B ones 연결 예정), still without 열기 / 이름 변경 / 삭제.
    expect(states(modelFor(root, connectedNow).items)).toEqual([
      ['folder.newFolder', true, 'planned'], ['folder.newFile', true, 'planned'], ['folder.download', true, 'planned'],
      ['folder.upload', true, 'planned'], ['folder.git.changes', true, 'unimplemented'], ['folder.git.revert', true, 'unimplemented'],
      ['folder.git.history', true, 'unimplemented'],
    ])
    const items = [separator('a'), action('node.copy', 'copy', '▣'), separator('b'), separator('c'),
      action('node.delete', 'delete', '×'), separator('d')]
    expect(outline(visibleItems(items, ['node.copy', 'node.delete']))).toEqual(['node.copy', '|', 'node.delete'])
    // A mockup item not connected stays (disabled); only an item outside the 40 (a run item) is hidden.
    expect(outline(visibleItems(items, ['node.delete']))).toEqual(['node.copy', '|', 'node.delete'])
    const run = [action('node.delete', 'delete', '×'), separator('r'), action('node.run.holdSet', '', '⏸')]
    expect(outline(visibleItems(run, ['node.delete']))).toEqual(['node.delete'])
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
        // Q 459: the multi-selection menu opens; only 정렬 runs (on the selection), no single card command.
        expect(states(modelFor(target, connectedNow).items)).toEqual([
          ['multi.editSettings', true, 'unimplemented'], ['multi.copy', true, 'unimplemented'], ['multi.arrange', false, null],
          ['multi.delete', true, 'unimplemented'],
        ])
      }
    }
  })
  it.each(['ko', 'en', 'ja'] as const)('has local i18n labels for every item in %s', (locale) => {
    const messages = contextMenuMessages[locale]
    for (const target of targets) {
      for (const item of [...modelFor(target).items, ...modelFor(target, []).items]) {
        if (item.kind === 'separator') continue
        expect(messages).toHaveProperty(item.labelKey!)
        if (item.disabledReason) expect(messages.reasons).toHaveProperty(item.disabledReason)
        if (item.kind === 'submenu') for (const child of item.children) if (child.kind === 'action' && !child.label) expect(messages).toHaveProperty(child.labelKey!)
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
    expect(single.items.map((item) => item.kind === 'separator' ? '|' : item.key))
      .toEqual(['node.detail', '|', 'node.copy', '|', 'node.delete', '|', 'node.run.holdSet', 'node.run.timeMachine'])
    expect(actions(single.items).map((item) => [item.label ?? null, item.disabled, item.danger])).toEqual([
      [null, true, false], [null, true, false], [null, false, true], ['Hold 설정', false, false], ['되돌리기 — 완료', true, true],
    ])
    expect(single.request('node.run.holdSet')).toEqual({ command: 'node.run.holdSet', target: targets[7] })
    expect(single.request('node.run.timeMachine')).toBeNull()
  })
  it('never adds them to a card of a multi-selection: its menu is the multi-selection one, no single card command (§3.6)', () => {
    const multi = canvasMenu({ target: targets[8] as never, available, connected: [...connected, ...connectedNow], runItems, arrange: arrangeChoices() })
    expect(outline(multi.items)).toEqual(['multi.editSettings', '|', 'multi.copy',
      ['multi.arrange:edit.align-left', 'multi.arrange:edit.align-center', 'multi.arrange:edit.align-right', '|',
        'multi.arrange:edit.align-top', 'multi.arrange:edit.align-middle', 'multi.arrange:edit.align-bottom', '|',
        'multi.arrange:edit.distribute-horizontal', 'multi.arrange:edit.distribute-vertical'], '|', 'multi.delete'])
    expect(actions(multi.items).some((item) => item.command.startsWith('node.'))).toBe(false)
    expect(multi.request('node.run.holdSet')).toBeNull()
    expect(multi.request('node.run.timeMachine')).toBeNull()
    expect(multi.request('node.delete')).toBeNull()
  })
  it('never adds them to the blank Canvas or a line, nor shows them unless connected', () => {
    for (const target of [targets[6], targets[9]]) {
      const model = canvasMenu({ target: target as never, available: { ...available, canSelectAll: true, graphReady: true, hasSaveTarget: true }, connected, runItems, definitions })
      expect(actions(model.items).some((item) => item.command.startsWith('node.run.'))).toBe(false)
    }
    expect(canvasMenu({ target: targets[7] as never, available, connected: firstConnected, runItems }).items.map((item) => item.key))
      .toEqual(['node.detail', 'node-copy', 'node.copy', 'node-delete', 'node.delete'])
  })
})


// flowgate.v02.0060 T#1 (WP 0012 [B]): 정렬 · 배치 / 정렬 open the Edit menu's 정렬 / 배치 commands.
describe('정렬 · 배치 / 정렬 as the Edit menu 정렬 / 배치 submenu (T#1)', () => {
  const canvas = targets[6]
  const multi = targets[8]
  const build = (target: MenuTarget, disabled?: (command: string) => boolean) =>
    canvasMenu({ target: target as never, connected: connectedNow, definitions, arrange: arrangeChoices(disabled),
      available: { editable: true, hasSaveTarget: true, canSelectAll: true, graphReady: true } })
  const sub = (target: MenuTarget, disabled?: (command: string) => boolean) =>
    build(target, disabled).items.find((item) => item.kind === 'submenu' && item.key.endsWith('.arrange'))!
  it.each([['canvas', canvas, 'canvas.arrange', 'arrangeLayout'], ['multi', multi, 'multi.arrange', 'arrange']] as const)(
    '%s: › and the 8 commands in DESIGNER_ARRANGE_GROUPS order, a separator between groups, the words given', (_kind, target, command, labelKey) => {
      const parent = sub(target)
      expect(parent).toMatchObject({ kind: 'submenu', key: command, labelKey, icon: '≡', disabled: false })
      if (parent.kind !== 'submenu') throw new Error('not a submenu')
      expect(parent.children.map((child) => child.kind === 'separator' ? '|' : child.label)).toEqual([
        'align-left', 'align-center', 'align-right', '|', 'align-top', 'align-middle', 'align-bottom', '|',
        'distribute-horizontal', 'distribute-vertical',
      ])
      const model = build(target)
      for (const group of arrangeGroups) {
        for (const kind of group) expect(model.request(`${command}:${kind}`)).toEqual({ command, target, arrange: kind })
      }
      expect(model.request(command)).toBeNull()
    })
  it('takes each enabled state from the caller (the Edit menu rule): 2 cards -> 정렬 only, fewer -> parent disabled', () => {
    const two = (c: string) => c.startsWith('edit.distribute-')
    for (const target of [canvas, multi]) {
      const parent = sub(target, two)
      expect(parent.disabled).toBe(false)
      expect(actions([parent]).filter((child) => child.disabled).map((child) => child.arrange)).toEqual(['edit.distribute-horizontal', 'edit.distribute-vertical'])
      expect(build(target, two).request(`${target.kind}.arrange:edit.distribute-vertical`)).toBeNull()
      expect(sub(target, () => true).disabled).toBe(true)
      expect(build(target, () => true).request(`${target.kind}.arrange:edit.align-left`)).toBeNull()
    }
  })
  it('without the groups keeps the mockup single item; not connected it is 미구현', () => {
    const plain = canvasMenu({ target: canvas as never, connected: connectedNow, available: { editable: true } })
    expect(actions(plain.items).find((item) => item.command === 'canvas.arrange')).toMatchObject({ kind: 'action', key: 'canvas.arrange', icon: '≡' })
    const unconnected = canvasMenu({ target: canvas as never, connected: firstConnected, arrange: arrangeChoices(), available: { editable: true } })
    const parent = unconnected.items.find((item) => item.key === 'canvas.arrange')!
    expect(parent).toMatchObject({ kind: 'submenu', disabled: false })
    expect(actions([parent]).every((child) => child.disabled && child.disabledReason === 'unimplemented')).toBe(true)
    expect(unconnected.request('canvas.arrange:edit.align-left')).toBeNull()
  })
  it('a single card and a line have no 정렬 (the mockup has none there)', () => {
    for (const target of [targets[7], targets[9]]) {
      const model = canvasMenu({ target: target as never, connected: connectedNow, arrange: arrangeChoices(),
        available: { editable: true, structureEditable: true, definitionExists: true } })
      expect(actions(model.items).some((item) => item.command.endsWith('.arrange'))).toBe(false)
    }
  })
})

// flowgate.v02.0060 T#1: the Git parent follows the Git state read, not its children being 연결 예정 / 미구현.
describe('Git parent of the file / folder menu (T#1)', () => {
  it('linked: the parent opens even though every child is 연결 예정 / 미구현', () => {
    for (const target of [targets[3], targets[4]]) {
      const parent = modelFor(target, connectedNow).items.find((item) => item.kind === 'submenu')!
      expect(parent).toMatchObject({ disabled: false })
      expect(actions([parent]).every((child) => child.disabled)).toBe(true)
    }
  })
  it.each(['unlinked', 'unavailable', 'no-worktree', 'denied'])('Git %s: the parent is disabled with that reason', (git) => {
    for (const target of [targets[3], targets[4]]) {
      const parent = modelFor(target, connectedNow, { git }).items.find((item) => item.kind === 'submenu')!
      expect(parent).toMatchObject({ disabled: true, disabledReason: git })
    }
  })
})
