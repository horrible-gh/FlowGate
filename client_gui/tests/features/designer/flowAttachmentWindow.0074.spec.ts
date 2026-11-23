// flowgate.v02test.0074 · 0005-T (items 3·4·5): newest-first order and the order menu, origin chips with counts that follow
// the search, the whole row selects (Shift = range over the shown rows), the bar under the list (one file / several
// files / delete with undo / clear), Esc, the first window size and focusAttachment().
import { mount, flushPromises } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import Window from '@/features/designer/flowAttachments/FlowAttachmentWindow.vue'
import Host from '@/features/designer/flowAttachments/FlowAttachmentHost.vue'
import { disposeFlowAttachmentOperations } from '@/features/designer/flowAttachments/operations'
import { FOCUS_HIGHLIGHT_MS, focusAttachment } from '@/features/designer/flowAttachments/focus'
import { SORT_STORAGE_KEY, countByOrigin, rangeKeys, readSort, sortFiles } from '@/features/designer/flowAttachments/listView'
import { initialRect } from '@/features/designer/flowAttachments/placement'
import type { FlowFile } from '@/features/designer/flowAttachments/api'

const api = vi.hoisted(() => ({ readFiles: vi.fn(), deleteFile: vi.fn(), uploadFile: vi.fn(), downloadFile: vi.fn(), downloadZip: vi.fn() }))
vi.mock('@/features/designer/flowAttachments/api', () => api)
vi.mock('@shared/api', () => ({ getRequest: vi.fn(async () => ({ data: { requests: [] } })) }))
vi.mock('@/features/designer/inspector/useInspectorResource', () => ({ useInspectorEvents: vi.fn() }))

const base = { filename: 'f', doc_id: 'doc', size: 2048, content_type: 'text/plain', content_sha256: 'sha', source_type: 'CH', source_title: 'Chat' }
// The server sends oldest first; newest first is k4, k5, k2, k3, k1 and the undated k6 last.
const files: FlowFile[] = [
  { ...base, key: 'k1', display_name: 'alpha.md', uploaded_at: '2026-11-01T09:00:00Z', origin: 'upload' },
  { ...base, key: 'k2', display_name: 'beta.png', uploaded_at: '2026-11-03T09:00:00Z', origin: 'ai' },
  { ...base, key: 'k3', display_name: 'gamma.txt', uploaded_at: '2026-11-02T09:00:00Z', origin: 'upload' },
  { ...base, key: 'k4', display_name: 'delta.md', uploaded_at: '2026-11-05T09:00:00Z', origin: 'requirement' },
  { ...base, key: 'k5', display_name: 'epsilon.md', uploaded_at: '2026-11-04T09:00:00Z', origin: 'ai' },
  { ...base, key: 'k6', display_name: 'zeta.log', uploaded_at: null, origin: 'upload' },
]
const global = { plugins: [createI18n({ legacy: false, locale: 'ko', messages: { ko: {} } })], stubs: { AttachmentRequestItem: true, Teleport: true } }
const props = { flowId: 'A', projectId: 'p', title: 'Flow A', position: 0, readOnly: false, current: true, focused: true, zIndex: 1 }
const hostProps = { flowId: 'A', projectId: 'p', title: 'Flow A', isReadOnly: () => false }

let wrapper: ReturnType<typeof mount> | undefined
const view = { width: innerWidth, height: innerHeight }
const scrolled: Element[] = []
function setViewport(width: number, height: number) {
  Object.defineProperty(window, 'innerWidth', { configurable: true, writable: true, value: width })
  Object.defineProperty(window, 'innerHeight', { configurable: true, writable: true, value: height })
}
beforeEach(() => {
  vi.clearAllMocks()
  localStorage.clear()
  scrolled.length = 0
  Element.prototype.scrollIntoView = function (this: Element) { scrolled.push(this) }
  api.readFiles.mockResolvedValue(files.map(f => ({ ...f })))
  api.deleteFile.mockResolvedValue({}); api.uploadFile.mockResolvedValue({}); api.downloadFile.mockResolvedValue(undefined); api.downloadZip.mockResolvedValue(undefined)
})
afterEach(() => {
  wrapper?.unmount(); wrapper = undefined
  disposeFlowAttachmentOperations(); vi.useRealTimers()
  setViewport(view.width, view.height)
  localStorage.clear()
})

const control = (name: string) => wrapper!.get(`[data-test="flow-attachment-${name}"]`)
const rowKeys = () => wrapper!.findAll('[data-test="flow-attachment-row"]').map(r => r.attributes('data-key'))
const row = (key: string) => wrapper!.get(`[data-test="flow-attachment-row"][data-key="${key}"]`)
const checkedKeys = () => wrapper!.findAll('[data-test="flow-attachment-row"]')
  .filter(r => (r.get('input').element as HTMLInputElement).checked).map(r => r.attributes('data-key'))
const chipCount = (chip: string) => control(`filter-${chip}`).get('[data-test="flow-attachment-filter-count"]').text()
const barOpen = () => control('bar').classes().includes('open')
async function mountWindow() { wrapper = mount(Window, { props, global }); await flushPromises() }
// A checkbox fires change only while it is in the document (jsdom, like a browser).
async function mountAttached() { wrapper = mount(Window, { props, global, attachTo: document.body }); await flushPromises() }
const selectedKeys = () => wrapper!.findAll('.fa-row.selected').map(r => r.attributes('data-key'))

describe('item 3 — newest first, order menu, origin chips with counts', () => {
  it('lists the newest upload / approval first, undated files last', async () => {
    await mountWindow()
    expect(rowKeys()).toEqual(['k4', 'k5', 'k2', 'k3', 'k1', 'k6'])
    expect((control('sort').element as HTMLSelectElement).value).toBe('newest')
  })

  it('the order menu switches to oldest first and by name, and the choice is remembered for the next window', async () => {
    await mountWindow()
    await control('sort').setValue('oldest')
    expect(rowKeys()).toEqual(['k1', 'k3', 'k2', 'k5', 'k4', 'k6'])
    await control('sort').setValue('name')
    expect(rowKeys()).toEqual(['k1', 'k2', 'k4', 'k5', 'k3', 'k6'])
    expect(localStorage.getItem(SORT_STORAGE_KEY)).toBe('name')
    wrapper!.unmount()
    await mountWindow()
    expect((control('sort').element as HTMLSelectElement).value).toBe('name')
    expect(rowKeys()[0]).toBe('k1')
  })

  it('chips [전체 / AI 첨부 / 직접 업로드] carry counts, and a chip keeps only its files', async () => {
    await mountWindow()
    expect(['all', 'ai', 'upload'].map(c => control(`filter-${c}`).text())).toEqual(['전체6', 'AI 첨부2', '직접 업로드3'])
    expect(control('filter-all').attributes('aria-pressed')).toBe('true')
    await control('filter-ai').trigger('click')
    expect(control('filter-ai').attributes('aria-pressed')).toBe('true')
    expect(rowKeys()).toEqual(['k5', 'k2'])
    expect(control('count').text()).toBe('2개')
    await control('filter-upload').trigger('click')
    expect(rowKeys()).toEqual(['k3', 'k1', 'k6'])
  })

  it('chip counts follow the search, and the chip and the search apply together', async () => {
    await mountWindow()
    await control('search').setValue('md')
    expect([chipCount('all'), chipCount('ai'), chipCount('upload')]).toEqual(['3', '1', '1'])
    await control('filter-ai').trigger('click')
    expect(rowKeys()).toEqual(['k5'])
    await control('search').setValue('nothing-like-this')
    expect([chipCount('all'), chipCount('ai'), chipCount('upload')]).toEqual(['0', '0', '0'])
    expect(wrapper!.find('.fa-empty').text()).toContain('검색 결과 없음')
  })

  it('select all, the count and the bar act on the files the chip shows only (#11 rule)', async () => {
    await mountWindow()
    await control('filter-upload').trigger('click')
    await control('select-all').setValue(true)
    expect(wrapper!.get('.fa-selected').text()).toBe('3개 선택')
    await control('zip').trigger('click'); await flushPromises()
    expect(api.downloadZip).toHaveBeenCalledWith('A', ['k3', 'k1', 'k6'])
    await control('filter-all').trigger('click')
    expect(checkedKeys()).toEqual(['k3', 'k1', 'k6'])
  })

  it('a row struck through for delete is not counted in its chip', async () => {
    await mountWindow()
    await row('k5').get('[data-test="flow-attachment-delete"]').trigger('click')
    expect(chipCount('ai')).toBe('1')
    expect(chipCount('all')).toBe('5')
  })
})

describe('item 4 — the whole row selects, Shift+click selects a range', () => {
  it('a press anywhere on the row (padding, type tile, name, size) toggles it', async () => {
    await mountWindow()
    await row('k2').trigger('click')
    expect(checkedKeys()).toEqual(['k2'])
    await row('k2').get('.fa-glyph').trigger('click')
    expect(checkedKeys()).toEqual([])
    await row('k3').get('.fa-size').trigger('click')
    await row('k1').get('.fa-name').trigger('click')
    expect(checkedKeys()).toEqual(['k3', 'k1'])
    expect(row('k3').classes()).toContain('selected')
  })

  it('the row buttons keep their own action and do not select', async () => {
    await mountWindow()
    await row('k2').get('[data-test="flow-attachment-download"]').trigger('click'); await flushPromises()
    expect(api.downloadFile).toHaveBeenCalledWith('A', expect.objectContaining({ key: 'k2' }))
    expect(checkedKeys()).toEqual([])
  })

  it('the checkbox still toggles once (no double toggle from the row)', async () => {
    await mountAttached()
    await row('k4').get('input').trigger('click')
    expect(selectedKeys()).toEqual(['k4'])
    expect(checkedKeys()).toEqual(['k4'])
    expect(wrapper!.get('.fa-selected').text()).toBe('1개 선택')
    await row('k4').get('input').trigger('click')
    expect(selectedKeys()).toEqual([])
    expect(checkedKeys()).toEqual([])
  })

  it('Shift+click selects from the last pressed row to the pressed row', async () => {
    await mountWindow()
    await row('k5').trigger('click')
    await row('k1').trigger('click', { shiftKey: true })
    expect(checkedKeys()).toEqual(['k5', 'k2', 'k3', 'k1'])
    // the anchor stays: Shift+click upwards from it
    await row('k4').trigger('click', { shiftKey: true })
    expect(checkedKeys()).toEqual(['k4', 'k5', 'k2', 'k3', 'k1'])
  })

  it('Shift on the checkbox selects the range too', async () => {
    await mountAttached()
    await row('k4').get('input').trigger('click')
    await row('k2').get('input').trigger('click', { shiftKey: true })
    expect(selectedKeys()).toEqual(['k4', 'k5', 'k2'])
    expect(checkedKeys()).toEqual(['k4', 'k5', 'k2'])
  })

  it('the range runs over the rows shown now: rows hidden by the search are not picked', async () => {
    await mountWindow()
    await control('search').setValue('md') // shows k4, k5, k1
    await row('k4').trigger('click')
    await row('k1').trigger('click', { shiftKey: true })
    expect(checkedKeys()).toEqual(['k4', 'k5', 'k1'])
    await control('search').setValue('')
    expect(checkedKeys()).toEqual(['k4', 'k5', 'k1'])
  })

  it('Shift+click with the anchor hidden by the chip is a plain toggle', async () => {
    await mountWindow()
    await row('k4').trigger('click') // requirement card: not under [AI 첨부]
    await control('filter-ai').trigger('click')
    await row('k2').trigger('click', { shiftKey: true })
    expect(checkedKeys()).toEqual(['k2'])
  })
})

describe('item 5 — the bar under the list, Esc', () => {
  it('stays down with nothing selected and comes up with a selection', async () => {
    await mountWindow()
    expect(barOpen()).toBe(false)
    expect(control('bar').attributes('aria-hidden')).toBe('true')
    await row('k2').trigger('click')
    expect(barOpen()).toBe(true)
    expect(control('bar').attributes('aria-hidden')).toBeUndefined()
    expect(control('bar-count').text()).toBe('1개 선택')
    // the top line keeps the select-all box and the count only
    expect(wrapper!.find('.fa-selection [data-test="flow-attachment-zip"]').exists()).toBe(false)
    expect(wrapper!.find('.fa-selection [data-test="flow-attachment-bulk-delete"]').exists()).toBe(false)
  })

  it('one file: [다운로드] gets the original file, no ZIP', async () => {
    await mountWindow()
    await row('k3').trigger('click')
    expect(wrapper!.find('[data-test="flow-attachment-zip"]').exists()).toBe(false)
    const one = control('download-selected')
    expect(one.text()).toContain('다운로드')
    await one.trigger('click'); await flushPromises()
    expect(api.downloadFile).toHaveBeenCalledWith('A', expect.objectContaining({ key: 'k3' }))
    expect(api.downloadZip).not.toHaveBeenCalled()
  })

  it('several files: [ZIP 다운로드] with the selected keys in the shown order', async () => {
    await mountWindow()
    await row('k1').trigger('click'); await row('k4').trigger('click')
    expect(control('bar-count').text()).toBe('2개 선택')
    expect(wrapper!.find('[data-test="flow-attachment-download-selected"]').exists()).toBe(false)
    expect(control('zip').text()).toContain('ZIP 다운로드')
    await control('zip').trigger('click'); await flushPromises()
    expect(api.downloadZip).toHaveBeenCalledWith('A', ['k4', 'k1'])
  })

  it('[삭제] uses the five-second undo: the numbered ↶ takes its place in the bar and brings the files back', async () => {
    await mountWindow()
    vi.useFakeTimers()
    await row('k1').trigger('click'); await row('k4').trigger('click')
    await control('bulk-delete').trigger('click')
    expect(wrapper!.findAll('.fa-row.gone').map(r => r.attributes('data-key'))).toEqual(['k4', 'k1'])
    expect(barOpen()).toBe(true)
    expect(control('bulk-undo').get('.fa-undo-badge').text()).toBe('2')
    expect(control('bulk-undo').find('.fa-ring').exists()).toBe(true)
    await control('bulk-undo').trigger('click')
    expect(wrapper!.findAll('.fa-row.gone')).toHaveLength(0)
    expect(barOpen()).toBe(false)
    await vi.advanceTimersByTimeAsync(5000)
    expect(api.deleteFile).not.toHaveBeenCalled()
  })

  it('[삭제] left alone sends the deletes after five seconds', async () => {
    await mountWindow()
    vi.useFakeTimers()
    await row('k2').trigger('click'); await row('k3').trigger('click')
    await control('bulk-delete').trigger('click')
    await vi.advanceTimersByTimeAsync(5000)
    await flushPromises()
    expect(api.deleteFile.mock.calls.map(([, f]) => f.key)).toEqual(['k2', 'k3'])
  })

  it('[선택 해제] clears the selection and the bar goes down', async () => {
    await mountWindow()
    await row('k1').trigger('click'); await row('k2').trigger('click')
    await control('clear-selection').trigger('click')
    expect(checkedKeys()).toEqual([])
    expect(barOpen()).toBe(false)
  })

  it('Esc inside the window clears the selection', async () => {
    await mountWindow()
    await row('k1').trigger('click'); await row('k2').trigger('click')
    await control('window').trigger('keydown', { key: 'Escape' })
    expect(checkedKeys()).toEqual([])
    expect(barOpen()).toBe(false)
  })

  it('Esc with nothing selected is left alone (not consumed)', async () => {
    await mountWindow()
    const event = new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true })
    control('window').element.dispatchEvent(event)
    expect(event.defaultPrevented).toBe(false)
  })
})

describe('item 5-2 — first window size', () => {
  // The real Teleport (to body): the size is set on the mounted window element itself.
  const realTeleport = { ...global, stubs: { AttachmentRequestItem: true } }
  async function mountInBody() {
    wrapper = mount(Window, { props, global: realTeleport, attachTo: document.body }); await flushPromises()
    return document.body.querySelector('[data-test="flow-attachment-window"]') as HTMLElement
  }
  it('opens at 720 × 600 inside a large viewport', async () => {
    setViewport(1440, 900)
    const el = await mountInBody()
    expect(el.style.width).toBe('720px')
    expect(el.style.height).toBe('600px')
    const left = parseFloat(el.style.left), top = parseFloat(el.style.top)
    expect(left + 720).toBeLessThanOrEqual(1440)
    expect(top + 600).toBeLessThanOrEqual(900)
  })

  it('a small viewport shrinks it to the viewport minus 25 px and keeps it on screen', async () => {
    setViewport(640, 480)
    const el = await mountInBody()
    expect(el.style.width).toBe('615px')
    expect(el.style.height).toBe('455px')
    expect(parseFloat(el.style.left) + 615).toBeLessThanOrEqual(640)
    expect(parseFloat(el.style.top) + 455).toBeLessThanOrEqual(480)
  })

  it('initialRect: steps later windows, never past the right or bottom edge', () => {
    expect(initialRect(1440, 900, 0)).toEqual({ x: 403, y: 189, width: 720, height: 600 })
    expect(initialRect(1440, 900, 1)).toEqual({ x: 588, y: 249, width: 720, height: 600 })
    for (const [w, h, p] of [[1024, 768, 0], [1024, 768, 3], [800, 500, 0], [600, 400, 2], [1920, 1080, 5]]) {
      const r = initialRect(w, h, p)
      expect(r.width).toBe(Math.min(720, w - 25))
      expect(r.height).toBe(Math.min(600, h - 25))
      expect(r.x + r.width).toBeLessThanOrEqual(w - 15)
      expect(r.y + r.height).toBeLessThanOrEqual(h - 15)
      expect(r.x).toBeGreaterThanOrEqual(10)
      expect(r.y).toBeGreaterThanOrEqual(8)
    }
  })
})

describe('item 3 — focusAttachment(flowId, attachmentId)', () => {
  it('opens the window, scrolls to the row and lights it for about two seconds', async () => {
    wrapper = mount(Host, { props: hostProps, global })
    vi.useFakeTimers()
    const shown = await focusAttachment('A', 'k3')
    expect(shown).toBe(true)
    expect(wrapper.findAllComponents(Window)).toHaveLength(1)
    expect(row('k3').classes()).toContain('flash')
    expect(scrolled.map(el => el.getAttribute('data-key'))).toEqual(['k3'])
    await vi.advanceTimersByTimeAsync(FOCUS_HIGHLIGHT_MS - 1)
    expect(row('k3').classes()).toContain('flash')
    await vi.advanceTimersByTimeAsync(1)
    expect(row('k3').classes()).not.toContain('flash')
  })

  it('an open window stays the one window; a file hidden by the chip and the search is shown by dropping them', async () => {
    wrapper = mount(Host, { props: hostProps, global })
    const host = wrapper.vm as unknown as { open: () => Promise<void> }
    await host.open(); await flushPromises()
    await control('filter-ai').trigger('click')
    await control('search').setValue('epsilon')
    expect(rowKeys()).toEqual(['k5'])
    expect(await focusAttachment('A', 'k1')).toBe(true)
    expect(wrapper.findAllComponents(Window)).toHaveLength(1)
    expect(control('filter-all').attributes('aria-pressed')).toBe('true')
    expect((control('search').element as HTMLInputElement).value).toBe('')
    expect(rowKeys()).toContain('k1')
    expect(row('k1').classes()).toContain('flash')
  })

  it('keeps a chip / search that does not hide the file', async () => {
    wrapper = mount(Host, { props: hostProps, global })
    const host = wrapper.vm as unknown as { open: () => Promise<void> }
    await host.open(); await flushPromises()
    await control('filter-ai').trigger('click')
    expect(await focusAttachment('A', 'k2')).toBe(true)
    expect(control('filter-ai').attributes('aria-pressed')).toBe('true')
  })

  it('reads the list again for a file not in it yet (just approved)', async () => {
    wrapper = mount(Host, { props: hostProps, global })
    const host = wrapper.vm as unknown as { open: () => Promise<void> }
    await host.open(); await flushPromises()
    api.readFiles.mockResolvedValue([...files, { ...base, key: 'new', display_name: 'fresh.md', uploaded_at: '2026-11-06T09:00:00Z', origin: 'ai' }])
    expect(await focusAttachment('A', 'new')).toBe(true)
    expect(rowKeys()[0]).toBe('new')
    expect(row('new').classes()).toContain('flash')
  })

  it('a file the Flow does not have ends quietly with false', async () => {
    wrapper = mount(Host, { props: hostProps, global })
    expect(await focusAttachment('A', 'missing')).toBe(false)
    expect(wrapper.findAll('.fa-row.flash')).toHaveLength(0)
    expect(scrolled).toEqual([])
  })

  it('false without a Host, for another Flow without a window, and for empty arguments', async () => {
    expect(await focusAttachment('A', 'k1')).toBe(false)
    wrapper = mount(Host, { props: hostProps, global })
    expect(await focusAttachment('Z', 'k1')).toBe(false)
    expect(wrapper.findAllComponents(Window)).toHaveLength(0)
    expect(await focusAttachment('', 'k1')).toBe(false)
    expect(await focusAttachment('A', '')).toBe(false)
    wrapper.unmount(); wrapper = undefined
    expect(await focusAttachment('A', 'k1')).toBe(false)
  })

  it('a Flow whose window is already open (not the Designer\'s current Flow) is brought to the front', async () => {
    wrapper = mount(Host, { props: hostProps, global })
    const host = wrapper.vm as unknown as { open: () => Promise<void> }
    await host.open(); await flushPromises()
    await wrapper.setProps({ flowId: 'B', title: 'Flow B' }); await flushPromises()
    expect(await focusAttachment('A', 'k2')).toBe(true)
    const [winA] = wrapper.findAllComponents(Window)
    expect(winA.props('focused')).toBe(true)
    expect(winA.find('[data-key="k2"]').classes()).toContain('flash')
  })
})

describe('listView helpers', () => {
  it('sortFiles keeps the incoming order on equal times', () => {
    const same = [{ ...files[0], key: 'x' }, { ...files[0], key: 'y' }, { ...files[0], key: 'z' }]
    expect(sortFiles(same, 'newest').map(f => f.key)).toEqual(['x', 'y', 'z'])
    expect(sortFiles(same, 'oldest').map(f => f.key)).toEqual(['x', 'y', 'z'])
  })
  it('countByOrigin and rangeKeys', () => {
    expect(countByOrigin(files)).toEqual({ all: 6, ai: 2, upload: 3 })
    expect(rangeKeys(['a', 'b', 'c', 'd'], 'c', 'a')).toEqual(['a', 'b', 'c'])
    expect(rangeKeys(['a', 'b'], 'gone', 'a')).toBeNull()
    expect(rangeKeys(['a', 'b'], null, 'a')).toBeNull()
  })
  it('readSort ignores a broken stored value', () => {
    localStorage.setItem(SORT_STORAGE_KEY, 'sideways')
    expect(readSort()).toBe('newest')
  })
})
