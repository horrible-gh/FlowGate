import { DOMWrapper, flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import { DEFAULT_CHAT_SETTINGS } from '@/features/conversation/api'
import ConversationComposer from '@/features/conversation/ConversationComposer.vue'
import { draftUserId } from '@/features/conversation/drafts'
import type { QuickMessage, QuickMessageInput } from '@/features/conversation/quickMessages/api'
import { loadStickyIds, stickyStorageKey, useQuickMessages } from '@/features/conversation/quickMessages/useQuickMessages'

const net = vi.hoisted(() => ({ list: vi.fn(), canEdit: vi.fn(), create: vi.fn(), update: vi.fn(), remove: vi.fn(), order: vi.fn(), restore: vi.fn() }))
vi.mock('@/features/conversation/quickMessages/api', async (original) => ({
  ...(await original<typeof import('@/features/conversation/quickMessages/api')>()),
  fetchChatQuickMessages: net.list,
  fetchCanEditQuickMessages: net.canEdit,
  createChatQuickMessage: net.create,
  updateChatQuickMessage: net.update,
  deleteChatQuickMessage: net.remove,
  saveChatQuickMessageOrder: net.order,
  restoreChatQuickMessageOrder: net.restore,
}))

registerAppMessages(i18n)

// flowgate.v02test.0072 card 3: add / edit / delete / reorder on the quick message row itself.
const PROJECT = 'p'
const FLOW = 'p.m.0001'
const CHAT = 'p.m.0001.0002-CH'

const qm = (id: number, title: string, message: string, kind: 'sticky' | 'once', sortOrder = id): QuickMessage =>
  ({ id, title, message, kind, sortOrder })
const LIST = [qm(1, '근거', '근거를 붙여 주세요.', 'sticky'), qm(2, '짧게', '짧게 답해 주세요.', 'once'), qm(3, '표로', '표로 정리해 주세요.', 'once')]

let wrappers: VueWrapper[] = []
function mountComposer() {
  const wrapper = mount(ConversationComposer, {
    props: {
      chatId: CHAT, projectId: PROJECT, groupId: FLOW, disabled: false, settings: DEFAULT_CHAT_SETTINGS,
      savingSettings: false, pendingSourceMode: null, providers: [], selectedProviderId: '', providersStatus: 'ready',
      hasProviders: false, sending: false, running: false, runStarting: false, cancelling: false, copyingMention: false,
      manualCopyText: null, onSend: vi.fn(async () => true),
    },
    attachTo: document.body,
    global: { plugins: [i18n], stubs: { ComposerModelPicker: true, ComposerSourceModes: true, ComposerSettings: true, ComposerToolsMenu: true } },
  })
  wrappers.push(wrapper)
  return wrapper
}

const items = (w: VueWrapper) => w.findAll('[data-test="conv-quick-item"]')
const buttons = (w: VueWrapper) => w.findAll('[data-test="conv-quick-btn"]')
const titles = (w: VueWrapper) => buttons(w).map((b) => b.text())
const add = (w: VueWrapper) => w.find('[data-test="conv-quick-add"]')
const editToggle = (w: VueWrapper) => w.find('[data-test="conv-quick-edit"]')
// the windows are teleported to <body>
const dom = (selector: string) => {
  const el = document.body.querySelector(selector)
  return el ? new DOMWrapper(el) : null
}
const dialog = () => dom('[data-test="qm-dialog"]')
const confirmButtons = () => [...document.body.querySelectorAll('.ui-confirm-actions button')].map((el) => new DOMWrapper(el))

async function fill(title: string, message: string) {
  await dom('[data-test="qm-title"]')!.setValue(title)
  await dom('[data-test="qm-message"]')!.setValue(message)
}

async function openWithEdit(list: QuickMessage[] = LIST) {
  net.list.mockResolvedValue(list)
  net.canEdit.mockResolvedValue(true)
  const w = mountComposer()
  await flushPromises()
  return w
}

beforeEach(() => {
  i18n.global.locale.value = 'ko'
  localStorage.clear()
  net.list.mockReset().mockResolvedValue(LIST)
  net.canEdit.mockReset().mockResolvedValue(true)
  net.create.mockReset()
  net.update.mockReset()
  net.remove.mockReset().mockResolvedValue(undefined)
  net.order.mockReset().mockResolvedValue(undefined)
  net.restore.mockReset().mockResolvedValue(undefined)
})

afterEach(() => {
  for (const w of wrappers) w.unmount()
  wrappers = []
  document.body.innerHTML = ''
})

describe('permission', () => {
  it('without project.settings.edit there is no ＋ 추가, ✎ 편집 or right-click edit, but the buttons still turn on and off', async () => {
    net.canEdit.mockResolvedValue(false)
    const w = mountComposer()
    await flushPromises()
    expect(titles(w)).toEqual(['근거', '짧게', '표로'])
    expect(add(w).exists()).toBe(false)
    expect(editToggle(w).exists()).toBe(false)
    const event = new MouseEvent('contextmenu', { bubbles: true, cancelable: true })
    items(w)[0].element.dispatchEvent(event)
    await flushPromises()
    expect(event.defaultPrevented).toBe(false)
    expect(dialog()).toBeNull()
    await buttons(w)[1].trigger('click')
    expect(buttons(w)[1].attributes('data-state')).toBe('on')
  })

  it('a failed permission read counts as no permission', async () => {
    net.canEdit.mockRejectedValue(new Error('network'))
    const w = mountComposer()
    await flushPromises()
    expect(buttons(w)).toHaveLength(3)
    expect(add(w).exists()).toBe(false)
  })

  it('shows ＋ 추가 and ✎ 편집 at the right end of the row with the permission', async () => {
    const w = await openWithEdit()
    expect(add(w).text()).toBe('＋ 추가')
    expect(editToggle(w).text()).toBe('✎ 편집')
  })

  it('a hidden row (failed load) offers nothing to manage', async () => {
    net.list.mockRejectedValue(Object.assign(new Error('forbidden'), { response: { status: 403 } }))
    const w = mountComposer()
    await flushPromises()
    expect(w.find('[data-test="conv-quick-row"]').exists()).toBe(false)
    expect(add(w).exists()).toBe(false)
  })
})

describe('add', () => {
  it('opens a small window with title, text and the kind (파랑 · 1회 is the default), saves and shows the new button at the end', async () => {
    const w = await openWithEdit()
    net.create.mockImplementation(async (_p: string, input: QuickMessageInput, sortOrder: number) => ({ id: 10, ...input, sortOrder }))
    await add(w).trigger('click')
    expect(dialog()).not.toBeNull()
    expect(dom('[data-test="qm-dialog-title"]')!.text()).toBe('퀵메시지 추가')
    expect(dom('[data-test="qm-kind-once"]')!.attributes('aria-checked')).toBe('true')
    expect(dom('[data-test="qm-kind-sticky"]')!.attributes('aria-checked')).toBe('false')
    expect(dom('[data-test="qm-kind-once"]')!.text()).toContain('파랑 · 1회')
    expect(dom('[data-test="qm-kind-sticky"]')!.text()).toContain('빨강 · 계속')
    await fill('새 문구', '새 문구를 붙여 주세요.')
    await dom('[data-test="qm-kind-sticky"]')!.trigger('click')
    await dom('[data-test="qm-save"]')!.trigger('click')
    await flushPromises()
    expect(net.create).toHaveBeenCalledWith(PROJECT, { title: '새 문구', message: '새 문구를 붙여 주세요.', kind: 'sticky' }, 4)
    expect(dialog()).toBeNull()
    expect(titles(w)).toEqual(['근거', '짧게', '표로', '새 문구'])
    expect(buttons(w)[3].attributes('title')).toBe('[빨강 · 계속] 새 문구를 붙여 주세요.')
  })

  it('does not save a blank title or a blank text and says why inside the window', async () => {
    const w = await openWithEdit()
    await add(w).trigger('click')
    await fill('   ', '문구')
    await dom('[data-test="qm-save"]')!.trigger('click')
    await flushPromises()
    expect(net.create).not.toHaveBeenCalled()
    expect(dom('[data-test="qm-problem"]')!.text()).toBe('제목을 입력해 주세요.')
    await fill('제목', '  ')
    await dom('[data-test="qm-save"]')!.trigger('click')
    await flushPromises()
    expect(net.create).not.toHaveBeenCalled()
    expect(dom('[data-test="qm-problem"]')!.text()).toBe('보낼 문구를 입력해 주세요.')
    expect(dialog()).not.toBeNull()
    expect(titles(w)).toHaveLength(3)
  })

  it('stops a title over 12 characters at the input', async () => {
    const w = await openWithEdit()
    await add(w).trigger('click')
    await dom('[data-test="qm-title"]')!.setValue('1234567890abcdef')
    expect((dom('[data-test="qm-title"]')!.element as HTMLInputElement).value).toBe('1234567890ab')
    expect(dom('[data-test="qm-title"]')!.attributes('maxlength')).toBe('12')
  })

  it('keeps the window open with a notice when saving fails, and Cancel closes it without saving', async () => {
    const w = await openWithEdit()
    net.create.mockRejectedValue(new Error('server'))
    await add(w).trigger('click')
    await fill('제목', '문구')
    await dom('[data-test="qm-save"]')!.trigger('click')
    await flushPromises()
    expect(dom('[data-test="qm-failed"]')!.text()).toContain('저장하지 못했습니다')
    expect(dialog()).not.toBeNull()
    expect(titles(w)).toHaveLength(3)
    await dom('[data-test="qm-cancel"]')!.trigger('click')
    expect(dialog()).toBeNull()
  })

  it('shows "등록된 퀵메시지가 없습니다" and ＋ 추가 when there is none, and the first added button appears', async () => {
    const w = await openWithEdit([])
    expect(w.get('[data-test="conv-quick-empty"]').text()).toBe('등록된 퀵메시지가 없습니다')
    expect(add(w).exists()).toBe(true)
    expect(editToggle(w).exists()).toBe(false)
    net.create.mockImplementation(async (_p: string, input: QuickMessageInput, sortOrder: number) => ({ id: 1, ...input, sortOrder }))
    await add(w).trigger('click')
    await fill('첫째', '첫 문구')
    await dom('[data-test="qm-save"]')!.trigger('click')
    await flushPromises()
    expect(net.create).toHaveBeenCalledWith(PROJECT, { title: '첫째', message: '첫 문구', kind: 'once' }, 1)
    expect(titles(w)).toEqual(['첫째'])
    expect(w.find('[data-test="conv-quick-empty"]').exists()).toBe(false)
  })
})

describe('edit', () => {
  it('a right click on a button opens its edit window (the same window as ＋ 추가) and the change shows in the row', async () => {
    const w = await openWithEdit()
    net.update.mockImplementation(async (_p: string, id: number, input: QuickMessageInput) => ({ id, ...input, sortOrder: id }))
    const event = new MouseEvent('contextmenu', { bubbles: true, cancelable: true })
    items(w)[1].element.dispatchEvent(event)
    await flushPromises()
    expect(event.defaultPrevented).toBe(true)
    expect(dom('[data-test="qm-dialog-title"]')!.text()).toBe('퀵메시지 수정')
    expect((dom('[data-test="qm-title"]')!.element as HTMLInputElement).value).toBe('짧게')
    expect((dom('[data-test="qm-message"]')!.element as HTMLTextAreaElement).value).toBe('짧게 답해 주세요.')
    expect(dom('[data-test="qm-kind-once"]')!.attributes('aria-checked')).toBe('true')
    await fill('아주 짧게', '한 줄로 답해 주세요.')
    await dom('[data-test="qm-save"]')!.trigger('click')
    await flushPromises()
    expect(net.update).toHaveBeenCalledWith(PROJECT, 2, { title: '아주 짧게', message: '한 줄로 답해 주세요.', kind: 'once' })
    expect(titles(w)).toEqual(['근거', '아주 짧게', '표로'])
    expect(buttons(w)[1].attributes('title')).toBe('[파랑 · 1회] 한 줄로 답해 주세요.')
    expect(dialog()).toBeNull()
  })

  it('a blank title is not saved on edit either', async () => {
    const w = await openWithEdit()
    items(w)[0].element.dispatchEvent(new MouseEvent('contextmenu', { bubbles: true, cancelable: true }))
    await flushPromises()
    await dom('[data-test="qm-title"]')!.setValue('')
    await dom('[data-test="qm-save"]')!.trigger('click')
    await flushPromises()
    expect(net.update).not.toHaveBeenCalled()
    expect(dom('[data-test="qm-problem"]')).not.toBeNull()
  })

  it('✎ 편집 dots the buttons, a press opens the edit window instead of turning the button on, ✓ 완료 ends it', async () => {
    const w = await openWithEdit()
    await editToggle(w).trigger('click')
    expect(editToggle(w).text()).toBe('✓ 완료')
    expect(buttons(w).every((b) => b.classes().includes('is-editing'))).toBe(true)
    await buttons(w)[1].trigger('click')
    expect(buttons(w)[1].attributes('data-state')).toBe('off')
    expect(dom('[data-test="qm-dialog-title"]')!.text()).toBe('퀵메시지 수정')
    await dom('[data-test="qm-cancel"]')!.trigger('click')
    await editToggle(w).trigger('click')
    expect(editToggle(w).text()).toBe('✎ 편집')
    expect(buttons(w).some((b) => b.classes().includes('is-editing'))).toBe(false)
    await buttons(w)[1].trigger('click')
    expect(buttons(w)[1].attributes('data-state')).toBe('on')
  })

  it('Alt+1..9 does not turn buttons on or off while editing', async () => {
    const w = await openWithEdit()
    await editToggle(w).trigger('click')
    await w.get('[data-test="conv-input"]').trigger('keydown', { key: '2', code: 'Digit2', altKey: true })
    expect(buttons(w)[1].attributes('data-state')).toBe('off')
  })
})

describe('delete', () => {
  it('✕ asks once; confirming deletes the button, and a deleted sticky button is gone from the stored on-state too', async () => {
    const w = await openWithEdit()
    await buttons(w)[0].trigger('click')
    expect(loadStickyIds(draftUserId(), PROJECT)).toEqual([1])
    await editToggle(w).trigger('click')
    await w.findAll('[data-test="conv-quick-del"]')[0].trigger('click')
    expect(net.remove).not.toHaveBeenCalled()
    expect(document.body.textContent).toContain('"근거" 퀵메시지를 삭제합니다.')
    await confirmButtons()[0].trigger('click')
    expect(net.remove).not.toHaveBeenCalled()
    expect(titles(w)).toHaveLength(3)
    await w.findAll('[data-test="conv-quick-del"]')[0].trigger('click')
    await confirmButtons()[1].trigger('click')
    await flushPromises()
    expect(net.remove).toHaveBeenCalledTimes(1)
    expect(net.remove).toHaveBeenCalledWith(PROJECT, 1)
    expect(titles(w)).toEqual(['짧게', '표로'])
    expect(loadStickyIds(draftUserId(), PROJECT)).toEqual([])
    expect(localStorage.getItem(stickyStorageKey(draftUserId(), PROJECT))).toBeNull()
  })

  it('a refused delete keeps the button and says so in the row', async () => {
    const w = await openWithEdit()
    net.remove.mockRejectedValue(new Error('server'))
    await editToggle(w).trigger('click')
    await w.findAll('[data-test="conv-quick-del"]')[2].trigger('click')
    await confirmButtons()[1].trigger('click')
    await flushPromises()
    expect(titles(w)).toHaveLength(3)
    expect(w.get('[data-test="conv-quick-notice"]').text()).toBe('삭제하지 못했습니다.')
  })
})

describe('order', () => {
  const drag = async (w: VueWrapper, from: number, to: number) => {
    await items(w)[from].trigger('dragstart')
    await items(w)[to].trigger('dragover')
    await items(w)[to].trigger('drop')
    await items(w)[from].trigger('dragend')
    await flushPromises()
  }

  it('dragging a button onto another one reorders the row at once and saves the new order', async () => {
    const w = await openWithEdit()
    await editToggle(w).trigger('click')
    await drag(w, 0, 2)
    expect(titles(w)).toEqual(['짧게', '표로', '근거'])
    expect(net.order).toHaveBeenCalledTimes(1)
    expect(net.order.mock.calls[0][0]).toBe(PROJECT)
    expect(net.order.mock.calls[0][2]).toEqual([2, 3, 1])
  })

  it('goes back to the previous order and says so briefly when the order cannot be saved', async () => {
    const w = await openWithEdit()
    net.order.mockRejectedValue(new Error('server'))
    await editToggle(w).trigger('click')
    await drag(w, 2, 0)
    expect(titles(w)).toEqual(['근거', '짧게', '표로'])
    expect(w.get('[data-test="conv-quick-notice"]').text()).toBe('순서를 저장하지 못해 원래대로 되돌렸습니다.')
    // the previous order is written back to the server too, not only shown again
    expect(net.restore).toHaveBeenCalledTimes(1)
    expect(net.restore).toHaveBeenCalledWith(PROJECT, LIST)
  })

  it('says the order could not be put back when the server refuses that too, and shows the server order', async () => {
    const w = await openWithEdit()
    net.order.mockRejectedValue(new Error('server'))
    net.restore.mockRejectedValue(new Error('server'))
    net.list.mockClear().mockResolvedValue([qm(1, '근거', '근거를 붙여 주세요.', 'sticky'), qm(3, '표로', '표로 정리해 주세요.', 'once', 2), qm(2, '짧게', '짧게 답해 주세요.', 'once', 3)])
    await editToggle(w).trigger('click')
    await drag(w, 2, 0)
    expect(titles(w)).toEqual(['근거', '표로', '짧게'])
    expect(w.get('[data-test="conv-quick-notice"]').text()).toBe('순서를 저장하지 못했고 원래대로도 되돌리지 못해 서버의 현재 순서를 보여 줍니다.')
  })

  it('a drag outside edit mode does nothing', async () => {
    const w = await openWithEdit()
    expect(items(w)[0].attributes('draggable')).toBe('false')
    await drag(w, 0, 2)
    expect(net.order).not.toHaveBeenCalled()
    expect(titles(w)).toEqual(['근거', '짧게', '표로'])
  })

  it('Alt+→ on a focused button in edit mode moves it one place', async () => {
    const w = await openWithEdit()
    await editToggle(w).trigger('click')
    await buttons(w)[0].trigger('keydown', { key: 'ArrowRight', altKey: true })
    await flushPromises()
    expect(titles(w)).toEqual(['짧게', '근거', '표로'])
    expect(net.order.mock.calls[0][2]).toEqual([2, 1, 3])
  })
})

describe('languages', () => {
  it.each([
    ['en', '＋ Add', '✎ Edit', 'Add quick message'],
    ['ja', '＋ 追加', '✎ 編集', 'クイックメッセージを追加'],
    ['ko', '＋ 추가', '✎ 편집', '퀵메시지 추가'],
  ])('shows the %s strings of the row and the window', async (locale, addText, editText, dialogTitle) => {
    i18n.global.locale.value = locale as 'ko'
    const w = await openWithEdit()
    expect(add(w).text()).toBe(addText)
    expect(editToggle(w).text()).toBe(editText)
    await add(w).trigger('click')
    expect(dom('[data-test="qm-dialog-title"]')!.text()).toBe(dialogTitle)
    expect(document.body.textContent).not.toMatch(/gui\.quickMessages/)
  })
})

describe('useQuickMessages management', () => {
  const make = (extra: Partial<Parameters<typeof useQuickMessages>[0]> = {}, list = LIST, canEdit = true) =>
    useQuickMessages({
      projectId: () => PROJECT,
      userId: () => 'u1',
      fetch: async () => list,
      canEdit: async () => canEdit,
      create: async (_p, input, sortOrder) => ({ id: 20, ...input, sortOrder }),
      update: async (_p, id, input) => ({ id, ...input, sortOrder: list.find((i) => i.id === id)?.sortOrder ?? 0 }),
      remove: async () => undefined,
      saveOrder: async () => undefined,
      restoreOrder: async () => undefined,
      ...extra,
    })

  it('offers management only with the edit permission', async () => {
    const q = make({}, LIST, false)
    await q.load()
    expect(q.canEdit.value).toBe(false)
    q.setEditing(true)
    expect(q.editing.value).toBe(false)
    expect(await q.create({ title: 'a', message: 'b', kind: 'once' })).toBe(false)
    expect(await q.remove(1)).toBe(false)
    expect(await q.move(1, 2)).toBe('denied')
  })

  it('does not turn buttons on or off while editing and leaves edit mode on a reload', async () => {
    const q = make()
    await q.load()
    q.setEditing(true)
    q.toggle(2)
    expect(q.toggleAt(0)).toBe(true)
    expect(q.active.value).toEqual([])
    await q.load()
    expect(q.editing.value).toBe(false)
    q.toggle(2)
    expect(q.active.value.map((i) => i.id)).toEqual([2])
  })

  it('a changed kind drops the old on-state; a deleted once button drops out of the next send', async () => {
    const q = make({ update: async (_p, id, input) => ({ id, ...input, sortOrder: id }) })
    await q.load()
    q.toggle(1)
    q.toggle(2)
    expect(await q.update(1, { title: '근거', message: '근거를 붙여 주세요.', kind: 'once' })).toBe(true)
    expect(q.items.value.find((i) => i.id === 1)?.kind).toBe('once')
    expect(q.active.value.map((i) => i.id)).toEqual([2])
    expect(loadStickyIds('u1', PROJECT)).toEqual([])
    expect(await q.remove(2)).toBe(true)
    expect(q.active.value).toEqual([])
    expect(q.items.value.map((i) => i.id)).toEqual([1, 3])
  })

  it('a failed create / update / delete changes nothing', async () => {
    const boom = async () => { throw new Error('server') }
    const q = make({ create: boom, update: boom, remove: boom })
    await q.load()
    q.toggle(1)
    expect(await q.create({ title: 'a', message: 'b', kind: 'once' })).toBe(false)
    expect(await q.update(1, { title: 'a', message: 'b', kind: 'once' })).toBe(false)
    expect(await q.remove(1)).toBe(false)
    expect(q.items.value).toEqual(LIST)
    expect(q.active.value.map((i) => i.id)).toEqual([1])
  })

  it('moves a button, saves the order, and puts the old order back after a failed save', async () => {
    const saveOrder = vi.fn(async () => undefined)
    const q = make({ saveOrder })
    await q.load()
    expect(await q.move(3, 0)).toBe('moved')
    expect(q.items.value.map((i) => i.id)).toEqual([3, 1, 2])
    expect(q.items.value.map((i) => i.sortOrder)).toEqual([1, 2, 3])
    expect(saveOrder).toHaveBeenCalledWith(PROJECT, LIST, [3, 1, 2])
    const failing = make({ saveOrder: async () => { throw new Error('server') } })
    await failing.load()
    expect(await failing.move(1, 2)).toBe('restored')
    expect(failing.items.value.map((i) => i.id)).toEqual([1, 2, 3])
  })

  // A small stand-in for the server: rows keep their sort_order, a PATCH writes one of them.
  const fakeServer = (rows: readonly QuickMessage[] = LIST) => {
    const stored = new Map(rows.map((r) => [r.id, r.sortOrder]))
    const view = () => rows.map((r) => ({ ...r, sortOrder: stored.get(r.id)! })).sort((a, b) => a.sortOrder - b.sortOrder || a.id - b.id)
    return { stored, view, ids: () => view().map((r) => r.id) }
  }
  const deferred = () => {
    let resolve!: () => void
    const promise = new Promise<void>((done) => { resolve = done })
    return { promise, resolve }
  }

  it('ignores a move made while the previous order is still being saved, so the server ends with the order shown', async () => {
    const server = fakeServer()
    const gate = deferred()
    const calls: number[][] = []
    const q = make({
      fetch: async () => server.view(),
      saveOrder: async (_p, _before, order) => {
        calls.push([...order])
        await gate.promise
        order.forEach((id, index) => server.stored.set(id, index + 1))
      },
    })
    await q.load()
    const first = q.move(1, 2)
    expect(q.ordering.value).toBe(true)
    expect(await q.move(1, 0)).toBe('busy')
    expect(q.items.value.map((i) => i.id)).toEqual([2, 3, 1])
    gate.resolve()
    expect(await first).toBe('moved')
    expect(q.ordering.value).toBe(false)
    expect(calls).toEqual([[2, 3, 1]])
    expect(q.items.value.map((i) => i.id)).toEqual(server.ids())
    // once the save is done the next move is taken and ends in the same order on both sides
    gate.resolve()
    expect(await q.move(1, 0)).toBe('moved')
    expect(q.items.value.map((i) => i.id)).toEqual([1, 2, 3])
    expect(server.ids()).toEqual([1, 2, 3])
  })

  it('after a save that stored only some rows, writes the old order back to the server and shows it', async () => {
    const server = fakeServer()
    const q = make({
      fetch: async () => server.view(),
      // rows 3 and 2 are stored, then the save fails (row 1 never gets its new position)
      saveOrder: async (_p, _before, order) => {
        server.stored.set(order[0], 1)
        server.stored.set(order[1], 2)
        throw new Error('server')
      },
      restoreOrder: async (_p, before) => { before.forEach((item) => server.stored.set(item.id, item.sortOrder)) },
    })
    await q.load()
    expect(await q.move(3, 0)).toBe('restored')
    expect(server.stored.get(3)).toBe(3)
    expect(server.ids()).toEqual([1, 2, 3])
    expect(q.items.value.map((i) => i.id)).toEqual(server.ids())
    expect(q.items.value.map((i) => i.sortOrder)).toEqual([1, 2, 3])
    expect(q.ordering.value).toBe(false)
  })

  it('when the old order cannot be written back either, shows what the server has instead of the old order', async () => {
    const server = fakeServer()
    const q = make({
      fetch: async () => server.view(),
      saveOrder: async (_p, _before, order) => {
        server.stored.set(order[0], 1)
        server.stored.set(order[1], 2)
        throw new Error('server')
      },
      restoreOrder: async () => { throw new Error('server') },
    })
    await q.load()
    expect(await q.move(3, 0)).toBe('unrestored')
    expect(q.items.value.map((i) => i.id)).toEqual(server.ids())
    expect(server.ids()).not.toEqual([1, 2, 3])
    expect(q.ordering.value).toBe(false)
    // and the row can be moved again afterwards
    expect(await q.move(1, 0)).toBe('unrestored')
  })

  it('a move that does not change anything is not saved', async () => {
    const saveOrder = vi.fn(async () => undefined)
    const q = make({ saveOrder })
    await q.load()
    expect(await q.move(2, 1)).toBe('moved')
    expect(saveOrder).not.toHaveBeenCalled()
  })

  it('forgets a stored sticky button whose message no longer exists', async () => {
    localStorage.setItem(stickyStorageKey('u1', PROJECT), JSON.stringify([1, 99]))
    const q = make()
    await q.load()
    expect(q.active.value.map((i) => i.id)).toEqual([1])
    expect(loadStickyIds('u1', PROJECT)).toEqual([1])
  })
})
