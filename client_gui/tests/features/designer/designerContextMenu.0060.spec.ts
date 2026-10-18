import { afterEach, describe, expect, it, vi } from 'vitest'
import { mount, flushPromises, type VueWrapper } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import { nextTick } from 'vue'
import DesignerContextMenu from '@/features/designer/contextMenu/DesignerContextMenu.vue'
import { createDesignerMenuHost } from '@/features/designer/contextMenu/host'
import { openContextMenu } from '@/features/designer/contextMenu/entry'
import { menuPosition, submenuPosition } from '@/features/designer/contextMenu/position'
import { makeMenu, action } from '@/features/designer/contextMenu/model'
import { canvasMenu } from '@/features/designer/contextMenu/canvasMenu'
import { arrangeChoices, connectedNow, definitions, modelFor, targets, firstConnected } from './contextMenuFixtures.0060'

let wrapper: VueWrapper | null = null
const cleanup: (() => void)[] = []
afterEach(() => {
  wrapper?.unmount(); wrapper = null
  cleanup.splice(0).forEach((fn) => fn())
  document.body.innerHTML = ''
  vi.restoreAllMocks()
})
const q = (selector: string) => document.querySelector<HTMLElement>(selector)!
const menu = () => q('[data-level="0"]')
const key = async (key: string) => {
  document.activeElement!.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true }))
  await flushPromises()
}
async function setup(model = modelFor(targets[5])) {
  const trigger = document.createElement('button')
  document.body.append(trigger); trigger.focus()
  const host = createDesignerMenuHost()
  const i18n = createI18n({ legacy: false, locale: 'ko', fallbackLocale: 'en' })
  wrapper = mount(DesignerContextMenu, { props: { host }, attachTo: document.body, global: { plugins: [i18n] } })
  host.open(model, { x: 40, y: 50 })
  await flushPromises()
  return { host, trigger, i18n }
}
const button = (name: string) => q('[data-key="' + name + '"]')
const key_ = (name: string) => key(name)

describe('Designer common context menu (0060)', () => {
  it('renders icons, separators, danger, child arrows and localized labels', async () => {
    const { i18n } = await setup(modelFor(targets[3]))
    expect(menu().textContent).toContain('새 폴더')
    expect(menu().querySelectorAll('[role="separator"]')).toHaveLength(3)
    expect(button('folder.git').getAttribute('aria-haspopup')).toBe('menu')
    expect(button('folder.delete').classList.contains('danger')).toBe(true)
    expect(button('folder.open').querySelector('[aria-hidden]')?.textContent).toBe('↗')
    i18n.global.locale.value = 'en'
    await nextTick()
    expect(button('folder.open').textContent).toContain('Open')
    i18n.global.locale.value = 'ja'
    await nextTick()
    expect(button('folder.open').textContent).toContain('開く')
  })
  it('opens an all-disabled menu, consumes once, focuses first, rejects click/Enter/Space and owns Escape', async () => {
    const locked = modelFor(targets[9], firstConnected, { progressed: true })
    const { host, trigger } = await setup(locked)
    expect(button('edge.disconnect').getAttribute('aria-disabled')).toBe('true')
    expect(document.activeElement).toBe(button('edge.disconnect'))
    const bubble = vi.fn()
    document.addEventListener('keydown', bubble)
    cleanup.push(() => document.removeEventListener('keydown', bubble))
    button('edge.disconnect').click()
    await key('Enter'); await key(' ')
    expect(wrapper!.emitted('request')).toBeUndefined()
    expect(host.current.value).not.toBeNull()
    bubble.mockClear()
    await key('Escape')
    expect(host.current.value).toBeNull()
    expect(document.activeElement).toBe(trigger)
    expect(bubble).not.toHaveBeenCalled()
    await key('Escape')
    expect(bubble).toHaveBeenCalledTimes(1)
    const ev = new MouseEvent('contextmenu', { cancelable: true })
    expect(openContextMenu(ev, host, locked)).toBe(true)
    expect(ev.defaultPrevented).toBe(true)
    expect(openContextMenu(ev, host, locked)).toBe(false)
  })
  it('focuses first enabled and arrows/Home/End visit disabled items, skipping separators', async () => {
    await setup(modelFor({ kind: 'tab', flowId: null, tabIds: ['f'] }))
    expect(document.activeElement).toBe(button('tab.closeAll'))
    await key('Home')
    expect(document.activeElement).toBe(button('tab.close'))
    await key('ArrowDown')
    expect(document.activeElement).toBe(button('tab.closeOthers'))
    await key('End')
    expect(document.activeElement).toBe(button('tab.closeAll'))
    await key('ArrowDown')
    expect(document.activeElement).toBe(button('tab.close'))
    await key('ArrowUp')
    expect(document.activeElement).toBe(button('tab.closeAll'))
  })
  it('opens submenus by hover/right/click, closes by left or another root item, and never requests a parent', async () => {
    await setup(modelFor(targets[3]))
    const parent = button('folder.git')
    parent.dispatchEvent(new MouseEvent('mouseenter'))
    await flushPromises()
    expect(q('[data-level="1"]')).not.toBeNull()
    expect(parent.getAttribute('aria-expanded')).toBe('true')
    button('folder.open').dispatchEvent(new MouseEvent('mouseenter'))
    await nextTick()
    expect(q('[data-level="1"]')).toBeNull()
    parent.focus()
    await key('ArrowRight')
    expect(document.activeElement).toBe(button('folder.git.changes'))
    await key('ArrowLeft')
    expect(q('[data-level="1"]')).toBeNull()
    expect(document.activeElement).toBe(parent)
    parent.click()
    await flushPromises()
    expect(wrapper!.emitted('request')).toBeUndefined()
    button('folder.git.history').click()
    await nextTick()
    expect(wrapper!.emitted('request')).toEqual([[{ command: 'folder.git.history', target: targets[3] }]])
  })
  it('does not open disabled parents even when all children remain visible definitions', async () => {
    await setup(modelFor(targets[3], undefined, { git: 'denied' }))
    const parent = button('folder.git')
    expect(parent.title).toBe('Git 읽기 권한 없음')
    parent.dispatchEvent(new MouseEvent('mouseenter')); parent.click(); parent.focus()
    await key('ArrowRight'); await key('Enter')
    expect(q('[data-level="1"]')).toBeNull()
    expect(wrapper!.emitted('request')).toBeUndefined()
  })
  it('selects a leaf exactly once with the opening snapshot and returns focus', async () => {
    const target = { kind: 'tab', flowId: 'f', tabIds: ['f', 'other'] } as const
    const { host, trigger } = await setup(modelFor(target))
    const oldButton = button('tab.close')
    const generation = host.current.value!.generation
    oldButton.click(); oldButton.click()
    await nextTick()
    expect(wrapper!.emitted('request')).toEqual([[{ command: 'tab.close', target }]])
    expect(host.select('tab.close', generation)).toBeNull()
    expect(document.activeElement).toBe(trigger)
  })
  it('selects a card child by Space with its exact input definition', async () => {
    const { host } = await setup(modelFor(targets[6], firstConnected))
    button('canvas.addCard').focus()
    await key('ArrowRight')
    await key('End')
    await key(' ')
    const request = (wrapper!.emitted('request')![0] as any[])[0]
    expect(request.command).toBe('canvas.addCard')
    expect(request.definition).toMatchObject({ itemId: 'confirm', nodeKind: 'approval', docType: null })
    expect(request.target).toEqual(targets[6])
    expect(host.current.value).toBeNull()
  })
  it('replaces one owned session and rejects stale selection and invalidation', async () => {
    const { host, trigger } = await setup()
    const old = host.current.value!.generation
    const replacement = host.open(modelFor(targets[2]), { x: 90, y: 100 })!
    expect(replacement).toBeGreaterThan(old)
    expect(host.select('tab.close', old)).toBeNull()
    expect(host.invalidate(old)).toBe(false)
    await flushPromises()
    expect(document.querySelectorAll('[data-level="0"]')).toHaveLength(1)
    expect(button('flow.rename')).not.toBeNull()
    expect(host.invalidate(replacement)).toBe(true)
    expect(document.activeElement).toBe(trigger)
  })
  it.each(['pointerdown', 'mousedown', 'contextmenu', 'scroll', 'resize', 'invalidate'])('closes and restores focus on %s', async (type) => {
    const { host, trigger } = await setup()
    if (type === 'resize') window.dispatchEvent(new Event(type))
    else if (type === 'invalidate') host.invalidate(host.current.value!.generation)
    else document.body.dispatchEvent(new Event(type, { bubbles: true }))
    await nextTick()
    expect(host.current.value).toBeNull()
    expect(document.activeElement).toBe(trigger)
  })
  it('keeps internal scroll and contextmenu inside, then restores fallback if trigger disappears', async () => {
    const { host, trigger } = await setup()
    menu().dispatchEvent(new Event('scroll', { bubbles: true }))
    const right = new MouseEvent('contextmenu', { bubbles: true, cancelable: true })
    button('tab.close').dispatchEvent(right)
    expect(right.defaultPrevented).toBe(true)
    expect(host.current.value).not.toBeNull()
    const fallback = document.createElement('button')
    document.body.append(fallback)
    host.open(modelFor(targets[5]), { x: 1, y: 1 }, fallback)
    trigger.remove()
    host.close()
    expect(document.activeElement).toBe(fallback)
  })
  it('tears down listeners and closes on unmount without leaving Escape ownership', async () => {
    const { host, trigger } = await setup()
    wrapper!.unmount(); wrapper = null
    expect(host.current.value).toBeNull()
    expect(document.activeElement).toBe(trigger)
    const bubble = vi.fn()
    document.addEventListener('keydown', bubble)
    cleanup.push(() => document.removeEventListener('keydown', bubble))
    await key('Escape')
    expect(bubble).toHaveBeenCalledTimes(1)
  })
  it('does not steal focus from a closed/replaced session during nextTick positioning', async () => {
    const { host, trigger } = await setup()
    host.open(modelFor(targets[3]), { x: 1, y: 1 })
    host.close()
    await flushPromises()
    expect(document.activeElement).toBe(trigger)
    expect(menu()).toBeNull()
  })
  it.each(['input', 'textarea', 'select', 'editable', 'editable-child', 'plaintext'])('preserves native menu in %s', async (kind) => {
    const { host } = await setup()
    host.close()
    const el = document.createElement(['input', 'textarea', 'select'].includes(kind) ? kind : 'div')
    if (kind.startsWith('editable') || kind === 'plaintext') el.setAttribute('contenteditable', kind === 'plaintext' ? 'plaintext-only' : 'true')
    if (kind === 'editable-child') el.append(document.createElement('span'))
    document.body.append(el)
    const event = new MouseEvent('contextmenu', { bubbles: true, cancelable: true })
    el.addEventListener('contextmenu', (e) => openContextMenu(e, host, modelFor(targets[5])))
    ;(el.firstElementChild ?? el).dispatchEvent(event)
    expect(event.defaultPrevented).toBe(false)
    expect(host.current.value).toBeNull()
  })
  // flowgate.v02.0060 T#1 (Q 459): a multi-selection / blank Canvas menu is never empty now; an empty model (no item, or
  // only run items not connected) still opens nothing and the outer Canvas does not take the same event over.
  it('does not consume empty/excluded targets or fall back from an empty inner model to the outer canvas', async () => {
    const { host } = await setup()
    host.close()
    const e = new MouseEvent('contextmenu', { cancelable: true })
    expect(openContextMenu(e, host, makeMenu(targets[8], []))).toBe(false)
    expect(openContextMenu(e, host, modelFor(targets[6], connectedNow))).toBe(false)
    expect(e.defaultPrevented).toBe(false)
    expect(host.current.value).toBeNull()
    const runOnly = new MouseEvent('contextmenu', { cancelable: true })
    expect(openContextMenu(runOnly, host, makeMenu(targets[7], [action('node.run.holdSet', '', '⏸')], []))).toBe(false)
    expect(runOnly.defaultPrevented).toBe(false)
    for (const _excluded of ['minimap', 'palette-blank']) {
      const event = new MouseEvent('contextmenu', { cancelable: true })
      expect(openContextMenu(event, host, null)).toBe(false)
      expect(event.defaultPrevented).toBe(false)
    }
  })
  it('measures panels and positions a submenu left when near the right boundary', async () => {
    vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function (this: HTMLElement) {
      if (this.dataset.key === 'folder.git') return { left: 800, right: 990, top: 80, width: 190, height: 30 } as DOMRect
      return { left: 0, right: 190, top: 0, width: 190, height: 240 } as DOMRect
    })
    const { host } = await setup(modelFor(targets[3]))
    host.open(modelFor(targets[3]), { x: window.innerWidth, y: window.innerHeight })
    await flushPromises()
    expect(parseFloat(menu().style.left)).toBe(window.innerWidth - 198)
    expect(parseFloat(menu().style.top)).toBe(window.innerHeight - 248)
    button('folder.git').click()
    await flushPromises()
    expect(parseFloat(q('[data-level="1"]').style.left)).toBe(606)
  })
  it.each([[0, 0], [1000, 0], [0, 800], [1000, 800], [-50, -60]])('clamps viewport corner %j', (x, y) => {
    const pos = menuPosition({ x, y }, { width: 190, height: 260 }, { width: 1000, height: 800 })
    expect(pos.x).toBeGreaterThanOrEqual(8); expect(pos.y).toBeGreaterThanOrEqual(8)
    expect(pos.x + 190).toBeLessThanOrEqual(992); expect(pos.y + 260).toBeLessThanOrEqual(792)
  })
  it('places the submenu on the right when it fits, and clamps the left edge too', () => {
    expect(submenuPosition({ left: 10, right: 200, top: 50 }, { width: 190, height: 260 }, { width: 1000, height: 800 })).toEqual({ x: 204, y: 50 })
    expect(submenuPosition({ left: 10, right: 200, top: 50 }, { width: 190, height: 260 }, { width: 300, height: 800 })).toEqual({ x: 8, y: 50 })
  })

  // flowgate.v02.0060 T#1 (Q 459, WP 0012 [C]): 연결 예정 / 미구현 items are shown in place with the disabled look; the
  // reason is the tooltip and the accessible description only (ko / en / ja), and they never request.
  it.each([
    ['ko', '연결 예정', '미구현'], ['en', 'Coming soon', 'Not implemented'], ['ja', '接続予定', '未実装'],
  ] as const)('an unconnected item says why in %s (tooltip + aria-describedby) and requests nothing', async (locale, planned, unimplemented) => {
    const { i18n } = await setup(modelFor(targets[2], connectedNow))
    i18n.global.locale.value = locale
    await nextTick()
    for (const [key, text] of [['flow.rename', planned], ['flow.delete', planned]] as const) {
      const entry = button(key)
      expect(entry.getAttribute('aria-disabled')).toBe('true')
      expect(entry.title).toBe(text)
      const described = document.getElementById(entry.getAttribute('aria-describedby')!)!
      expect(described.textContent).toBe(text)
      expect(described.hidden).toBe(true)
      expect(entry.textContent).not.toContain(text)
    }
    wrapper!.unmount(); wrapper = null
    const second = await setup(modelFor(targets[0], connectedNow))
    second.i18n.global.locale.value = locale
    await nextTick()
    expect(button('project.info').title).toBe(unimplemented)
    expect(button('project.newGroup').title).toBe('')
    expect(button('project.newGroup').hasAttribute('aria-describedby')).toBe(false)
    button('project.info').focus()
    button('project.info').click()
    await key_(' ')
    await key_('Enter')
    expect(wrapper!.emitted('request')).toBeUndefined()
    expect(second.host.current.value).not.toBeNull()
  })
  it('draws the mockup cells: icon column, label, › only on a submenu, danger and disabled classes', async () => {
    await setup(modelFor(targets[4], connectedNow))
    const cells = (key: string) => Array.from(button(key).children).map((cell) => cell.className)
    expect(cells('file.open')).toEqual(['designer-context-icon', 'designer-context-label'])
    expect(cells('file.git')).toEqual(['designer-context-icon', 'designer-context-label', 'designer-context-arrow'])
    expect(button('file.git').querySelector('.designer-context-arrow')!.textContent).toBe('›')
    expect(button('file.delete').classList.contains('danger')).toBe(true)
    expect(button('file.delete').getAttribute('aria-disabled')).toBe('true')
    expect(menu().querySelectorAll('.designer-context-separator')).toHaveLength(2)
    expect(Array.from(menu().querySelectorAll('[role="menuitem"]')).map((entry) => entry.querySelector('.designer-context-icon')!.textContent))
      .toEqual(['↗', '✎', '↓', '⑂', '×'])
  })
  it('a linked Git parent opens its submenu of disabled children; a separator inside a submenu is skipped by the keys', async () => {
    await setup(modelFor(targets[4], connectedNow))
    button('file.git').focus()
    await key_('ArrowRight')
    const sub = q('[data-level="1"]')
    expect(Array.from(sub.querySelectorAll('[role="menuitem"]')).map((entry) => [entry.getAttribute('data-key'), entry.getAttribute('aria-disabled'), (entry as HTMLElement).title]))
      .toEqual([['file.git.changes', 'true', '연결 예정'], ['file.git.revert', 'true', '미구현'], ['file.git.history', 'true', '미구현']])
    expect(document.activeElement).toBe(button('file.git.changes'))
    button('file.git.revert').click()
    await key_('Enter')
    expect(wrapper!.emitted('request')).toBeUndefined()
    wrapper!.unmount(); wrapper = null
    const model = canvasMenu({ target: targets[6] as never, connected: connectedNow, definitions, arrange: arrangeChoices((c) => c.startsWith('edit.distribute-')),
      available: { editable: true, hasSaveTarget: true, canSelectAll: true, graphReady: true } })
    await setup(model)
    button('canvas.arrange').focus()
    await key_('ArrowRight')
    const panel = q('[data-level="1"]')
    expect(panel.querySelectorAll('[role="separator"]')).toHaveLength(2)
    expect(document.activeElement).toBe(button('canvas.arrange:edit.align-left'))
    await key_('End')
    expect(document.activeElement).toBe(button('canvas.arrange:edit.distribute-vertical'))
    await key_('ArrowUp'); await key_('ArrowUp')
    expect(document.activeElement).toBe(button('canvas.arrange:edit.align-bottom'))
    await key_('Enter')
    expect(wrapper!.emitted('request')).toEqual([[{ command: 'canvas.arrange', target: targets[6], arrange: 'edit.align-bottom' }]])
  })

})

// flowgate.v02.0060 T#1 (WP 0012 §1, §6): the 11 kinds drawn as the mockup's menuDefs (flowgate-v02-context-menu-v2.html,
// transcribed here: icon + words per item, '|' a .fg-ctx-line, '!' danger, '›' a submenu with its children), with the
// T#1 state of each item (run / 연결 예정 / 미구현). paletteBlank, insDoc, insAttach, git and chat are not in scope.
describe('the 11 kinds against the mockup menuDefs (T#1)', () => {
  const G = ['≋ 수정 확인', '↶ 되돌리기!', '◷ 이력']
  const C = ['기본 설계', '프로토콜', '로직', 'DB설계', '조사', '작업계획', '작업', '테스트', '확인'].map((label) => '＋ ' + label)
  const MOCKUP: Array<[string, (string | [string, string[]])[]]> = [
    ['project', ['＋ 새 그룹', '|', 'ⓘ 프로젝트 정보']],
    ['group', ['＋ 새 플로우', '|', '✎ 이름 변경', 'ⓘ 정보']],
    ['flow', ['✎ 이름 변경', '|', '× 삭제!']],
    ['folder', ['↗ 열기', '|', '＋ 새 폴더', '＋ 새 파일', '|', '✎ 이름 변경', '↓ 다운로드', '↑ 업로드', ['⑂ Git ›', G], '|', '× 삭제!']],
    ['file', ['↗ 열기', '|', '✎ 이름 변경', '↓ 다운로드', ['⑂ Git ›', G], '|', '× 삭제!']],
    ['tab', ['× 닫기', '⊟ 다른 탭 닫기', '☒ 전체 닫기']],
    ['canvas', [['＋ 카드 추가 ›', C], '|', '▣ 붙여넣기', '▢ 전체 선택', '|', '≡ 정렬 · 배치']],
    ['node', ['ⓘ 상세 보기', '|', '▣ 복사', '|', '× 삭제!']],
    ['multi', ['⚙ 공통설정 편집', '|', '▣ 복사', '≡ 정렬', '|', '× 삭제!']],
    ['edge', ['⌁ 연결 해제!']],
    ['palette', ['＋ 카드 추가']],
  ]
  const PLANNED = new Set(['group.rename', 'flow.rename', 'flow.delete', 'folder.newFolder', 'folder.newFile', 'folder.download',
    'folder.upload', 'folder.delete', 'file.download', 'file.git.changes', 'file.delete'])
  const RUN = new Set<string>(connectedNow)
  const draw = (level: number) => Array.from(document.querySelectorAll<HTMLElement>(`[data-designer-context-menu] [data-level="${level}"] > *`))
    .filter((el) => !el.hasAttribute('hidden'))
    .map((el) => el.getAttribute('role') === 'separator' ? '|'
      : Array.from(el.children).map((cell) => cell.textContent!.trim()).join(' ') + (el.classList.contains('danger') ? '!' : ''))
  it.each(MOCKUP.map(([kind, expected], i) => [kind, expected, i] as const))('%s: icons, words, separators, danger and › as the mockup', async (_kind, expected, i) => {
    await setup(modelFor(targets[i]))
    const top = expected.map((entry) => (Array.isArray(entry) ? entry[0] : entry))
    expect(draw(0)).toEqual(top)
    for (const entry of expected) {
      if (!Array.isArray(entry)) continue
      const parent = Array.from(menu().querySelectorAll<HTMLElement>('[role="menuitem"]')).find((el) => el.textContent!.includes(entry[0].split(' ')[1]))!
      parent.dispatchEvent(new MouseEvent('mouseenter'))
      await flushPromises()
      expect(draw(1)).toEqual(entry[1])
    }
  })
  it.each(MOCKUP.map(([kind], i) => [kind, i] as const))('%s: the T#1 state of each item — run, 연결 예정 or 미구현 (disabled, its tooltip)', async (_kind, i) => {
    await setup(modelFor(targets[i], connectedNow))
    const shown = (level: number) => Array.from(document.querySelectorAll<HTMLElement>(`[data-designer-context-menu] [data-level="${level}"] [role="menuitem"]`))
    const check = (entry: HTMLElement) => {
      const key = entry.dataset.key!
      const command = key.split(':')[0]
      if (entry.getAttribute('aria-haspopup')) return
      if (RUN.has(command)) expect([key, entry.getAttribute('aria-disabled'), entry.title]).toEqual([key, 'false', ''])
      else expect([key, entry.getAttribute('aria-disabled'), entry.title]).toEqual([key, 'true', PLANNED.has(command) ? '연결 예정' : '미구현'])
    }
    shown(0).forEach(check)
    for (const parent of shown(0).filter((entry) => entry.getAttribute('aria-haspopup'))) {
      parent.dispatchEvent(new MouseEvent('mouseenter'))
      await flushPromises()
      shown(1).forEach(check)
    }
  })
})
